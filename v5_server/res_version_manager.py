# -*- coding: utf-8 -*-
"""
res_version_manager.py — 客户端资源版本与两级协议自适应管理器

职责：
1. 两级版本抽象模型：
   - 一级主基线 (Major Baseline)：Theme 43 (v5.2) 与 Theme 44 (v5.3+)，承载协议抑制 (sc_89013)、活动拓扑、淘汰商店等宏观架构；
   - 二级子构建 (Minor Build)：如 311、321 及未来官方更新的 322+，自动继承主基线，无需硬编码各版本号。
2. 动态自愈与注册：
   - 自动扫描本地客户端目录，若玩家直连官方更新出新版本（如 322），自动识别并基于基线动态注册，避免回退或报错；
   - 支持从 dynamic_versions.json 跨进程持久化加载已注册版本。
3. 官方上游 DoH 穿透探测：
   - 内置公共 DoH 域名解析，绕过 Windows HOSTS 重定向，直连 open.ys4fun.com；
   - 携带逆向还原的 YSMixHttpRequest 官方签名，全自动拉取官方最新发布的版本清单。
4. 增量差异对比引擎：
   - 精准解析 assethash 清单，比对本地与远端资产库，计算新增、删除与修改文件列表及体积。
5. 线程安全后台更新下载器 (UpdateTaskManager)：
   - 支持命令行与 GM Web 控制面板主动触发，提供文件级与字节级实时下载进度与速度监控；
   - 将清单与增量资源落盘至 static_resources 与各平台专属数据目录 (如 D:\\PC_DATA)，严禁触碰客户端目录。
"""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import uuid
import cdn_integrity as integrity
import cdn_active_downloads as active_downloads
import hashlib
import http.client
import json
import logging
import os
import random
import re
import socket
import ssl
import string
import threading
import time
from typing import Dict, List, Optional, Tuple, Any

_V5_DIR = os.path.dirname(os.path.abspath(__file__))
DYNAMIC_VERSIONS_PATH = os.path.join(_V5_DIR, "dynamic_versions.json")
STATIC_RESOURCES_DIR = os.path.join(_V5_DIR, "static_resources")

# 远期时间戳：活动时间统一在协议输出层延长，避免改写玩家数据库
FAR_FUTURE_TIMESTAMP = 2440962000
DEFAULT_VERSION = "321"

# 官方 API 逆向凭据与上游域名
OFFICIAL_API_HOST = "open.ys4fun.com"
OFFICIAL_API_PATH = "/mix-sdk-api/pass/game/updateversion/getLatest"
OFFICIAL_APP_ID = "bd2fc85f8ecd35fd13deec46560bb36a"
OFFICIAL_APP_SECRET = "2b32f70d9ca49dbc71dc8ce6f6ced10e"
OFFICIAL_CDN_HOST = "download-eo.ys4fun.com"

# ----------------- 两级架构基线模型定义 (Major Baseline Models) -----------------

THEME_43_BASELINE: Dict[str, Any] = {
    "theme_id": 43,
    "matched_app_version": "307",
    "battlepass_season": 32,
    "entry_activity_id": 4310001,
    "sign_activity_id": 4300101,
    "battlepass_list_id": 20032,
    "heart_demon_activity_id": 4335001,
    "core_verification_activity_id": 4343501,
    "polyhedron_activity_id": 4307301,
    "suppressed_cmds": [],
    "deprecated_shops": [],
    "description": "v5.2.1 兼容基线 (Theme 43)，涵盖 43 项常驻活动与经典验证拓扑。",
}

THEME_44_BASELINE: Dict[str, Any] = {
    "theme_id": 44,
    "matched_app_version": "307",
    "battlepass_season": 33,
    "entry_activity_id": 4410001,
    "sign_activity_id": 4400101,
    "battlepass_list_id": 20033,
    "heart_demon_activity_id": 4435001,
    "core_verification_activity_id": 4443501,
    "core_verification_active_activity_ids": [4443301, 4443401, 4443501],
    "polyhedron_activity_id": 4407301,
    "suppressed_cmds": [89013],
    "deprecated_shops": [16, 18, 24, 25, 34, 35, 36, 37, 42],
    "description": "v5.3+ 演进基线 (Theme 44)，支持 33 赛季战令、核心验证第 4 轮与 sc_89013 协议抑制。",
}


def get_baseline_for_build(build_num: int) -> Dict[str, Any]:
    """根据子构建号自动推导所属主基线能力模型。"""
    if build_num >= 311:
        return copy.deepcopy(THEME_44_BASELINE)
    return copy.deepcopy(THEME_43_BASELINE)


def normalize_version_name(value: str) -> str:
    """保证客户端去掉 v 后能比较三个数字；构建号不能充当版本名称。"""
    name = str(value or "").strip()
    match = re.fullmatch(r"[vV]?([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", name)
    if not match:
        raise ValueError(f"无效的版本名称 [{name}]，需要 v主版本.次版本.补丁号，不能使用构建号或占位名称")
    major, minor, patch = match.groups()
    return f"v{int(major)}.{int(minor)}.{int(patch or '0')}"


# 内置初始版本库
BUILTIN_VERSION_CONFIGS: Dict[str, Dict[str, Any]] = {
    "229": {
        "version": "229",
        "version_name": "v5.2.1",
        "display_name": "Build 229 (v5.2.1 兼容基线)",
        "matched_app_version": "307",
        "assethash": {
            "pc": "assethash_307_229_3807d27e1a6e26853f1fccfa8b7681d0.bytes",
            "android": "assethash_307_229_3807d27e1a6e26853f1fccfa8b7681d0.bytes",
            "ios": "assethash_307_229_29e8f745e8ec6032937a65a0bfd3a9cf.bytes",
        },
        "voice_package_list": "voice_package_list_307_229.bytes",
        "voice_hashes": {
            "ja": "voice_hash_ja_30700229.bytes",
            "zh": "voice_hash_zh_30700229.bytes",
        },
        **THEME_43_BASELINE,
    },
    "311": {
        "version": "311",
        "version_name": "v5.3.0",
        "display_name": "Build 311 (v5.3.0)",
        "matched_app_version": "307",
        "assethash": {
            "pc": "assethash_307_311_738463f12c9b762e6650822b949bd091.bytes",
            "android": "assethash_307_311_738463f12c9b762e6650822b949bd091.bytes",
            "ios": "assethash_307_229_29e8f745e8ec6032937a65a0bfd3a9cf.bytes",
        },
        "voice_package_list": "voice_package_list_307_311.bytes",
        "voice_hashes": {
            "ja": "voice_hash_ja_30700311.bytes",
            "zh": "voice_hash_zh_30700311.bytes",
        },
        **THEME_44_BASELINE,
    },
    "321": {
        "version": "321",
        "version_name": "v5.3.1",
        "display_name": "Build 321 (v5.3.1 最新版本)",
        "matched_app_version": "307",
        "assethash": {
            "pc": "assethash_307_321_a66c600ea8c72f003e03969fdd352250.bytes",
            "android": "assethash_307_321_da9ef437b47a66311f0e2e89e7318388.bytes",
            "ios": "assethash_307_321_fd5db716ec33e1924528de483faca2ce.bytes",
        },
        "voice_package_list": "voice_package_list_307_321.bytes",
        "voice_hashes": {
            "ja": "voice_hash_ja_30700321.bytes",
            "zh": "voice_hash_zh_30700321.bytes",
        },
        **THEME_44_BASELINE,
    },
}

VERSION_CONFIGS: Dict[str, Dict[str, Any]] = copy.deepcopy(BUILTIN_VERSION_CONFIGS)
SUPPORTED_VERSIONS: List[str] = list(VERSION_CONFIGS.keys())
_CURRENT_VERSION: str = DEFAULT_VERSION
_CONNECTION_VERSION = ContextVar('game_connection_version', default=None)
_LOCK = threading.Lock()
_VERSION_CHANGE_LISTENERS = []

# ----------------- 动态注册与持久化管理 (Dynamic Persistence) -----------------

def _load_persisted_dynamic_versions():
    """从本地 dynamic_versions.json 加载持久化的动态版本。"""
    if not os.path.isfile(DYNAMIC_VERSIONS_PATH):
        return
    try:
        with open(DYNAMIC_VERSIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    try:
                        cfg = copy.deepcopy(v)
                        cfg["version_name"] = normalize_version_name(cfg.get("version_name"))
                    except ValueError as error:
                        logging.getLogger(__name__).warning("忽略 Build %s 的无效持久化版本配置: %s", k, error)
                        continue
                    VERSION_CONFIGS[k] = cfg
                    if k not in SUPPORTED_VERSIONS:
                        SUPPORTED_VERSIONS.append(k)
    except Exception:
        pass


def _save_persisted_dynamic_versions():
    """Persist atomically and propagate failure; never advertise a failed commit."""
    dynamic_only = {k: v for k, v in VERSION_CONFIGS.items()
                    if k not in BUILTIN_VERSION_CONFIGS or v != BUILTIN_VERSION_CONFIGS[k]}
    integrity.publish_json(DYNAMIC_VERSIONS_PATH, dynamic_only)


_load_persisted_dynamic_versions()


def register_version(ver: str, config: Optional[Dict[str, Any]] = None, persist: bool = True) -> Dict[str, Any]:
    """
    动态注册新版本到服务端支持列表中。
    新构建必须携带清单或官方元数据中的版本名称，不能只由构建号猜测。
    """
    global SUPPORTED_VERSIONS
    ver_str = str(ver).strip()
    with _LOCK:
        if config is None:
            config = VERSION_CONFIGS.get(ver_str)
            if config is None:
                raise ValueError(f"Build [{ver_str}] 缺少真实版本名称，请先探测客户端清单或同步官方版本元数据")
        cfg = copy.deepcopy(config)
        cfg["version_name"] = normalize_version_name(cfg.get("version_name"))
        previous = VERSION_CONFIGS.get(ver_str)
        existed = ver_str in SUPPORTED_VERSIONS
        VERSION_CONFIGS[ver_str] = cfg
        if not existed:
            SUPPORTED_VERSIONS.append(ver_str)
        try:
            if persist:
                _save_persisted_dynamic_versions()
        except Exception:
            if previous is None:
                VERSION_CONFIGS.pop(ver_str, None)
            else:
                VERSION_CONFIGS[ver_str] = previous
            if not existed:
                SUPPORTED_VERSIONS.remove(ver_str)
            raise
        return copy.deepcopy(VERSION_CONFIGS[ver_str])


# ----------------- 本地客户端目录探测 (Client Detection) -----------------

def _find_default_client_streaming_assets_dir() -> str:
    env_dir = os.environ.get("CLIENT_STREAMING_ASSETS_PATH", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return env_dir

    try:
        cfg_path = os.path.join(_V5_DIR, "server_config.json")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            client_install = cfg.get("client", {}).get("game_install_dir", "").strip()
            if client_install:
                sub_candidates = [
                    os.path.join(client_install, "AetherGazer_Data", "StreamingAssets", "Windows"),
                    os.path.join(client_install, "StreamingAssets", "Windows"),
                    os.path.join(client_install, "StreamingAssets"),
                    client_install,
                ]
                for sc in sub_candidates:
                    if os.path.isdir(sc):
                        return sc
    except Exception:
        pass

    candidates = [
        r"D:\AtherGazer\AetherGazerLauncher\AetherGazer\AetherGazer_Data\StreamingAssets\Windows",
        r"C:\Program Files\AetherGazer\AetherGazerLauncher\AetherGazer\AetherGazer_Data\StreamingAssets\Windows",
        r"D:\AetherGazer\AetherGazerLauncher\AetherGazer\AetherGazer_Data\StreamingAssets\Windows",
        r"E:\AtherGazer\AetherGazerLauncher\AetherGazer\AetherGazer_Data\StreamingAssets\Windows",
        r"E:\AetherGazer\AetherGazerLauncher\AetherGazer\AetherGazer_Data\StreamingAssets\Windows",
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return ""


DEFAULT_CLIENT_STREAMING_ASSETS_DIR = _find_default_client_streaming_assets_dir()
ENV_CLIENT_STREAMING_ASSETS_DIR = "CLIENT_STREAMING_ASSETS_PATH"
ASSETHASH_PATTERN = re.compile(r"^assethash_(\d+)_(\d+)_([0-9a-fA-F]+)\.bytes$", re.IGNORECASE)

_DETECTED_CLIENT_INFO = {
    "detected": False,
    "version": None,
    "matched_file": None,
    "file_mtime": 0,
    "file_mtime_str": "",
    "client_dir": DEFAULT_CLIENT_STREAMING_ASSETS_DIR,
    "detail": "尚未执行客户端版本探测",
}


def get_detected_client_info() -> dict:
    with _LOCK:
        return copy.deepcopy(_DETECTED_CLIENT_INFO)


def detect_client_resource_version(custom_dir: Optional[str] = None) -> dict:
    """Read-only PC directory hint. Never register it as all-platform authority."""
    global _DETECTED_CLIENT_INFO
    target = (custom_dir or os.environ.get(ENV_CLIENT_STREAMING_ASSETS_DIR) or DEFAULT_CLIENT_STREAMING_ASSETS_DIR or '').strip()
    info = dict(detected=False, version=None, matched_file=None, file_mtime=0,
                file_mtime_str='', client_dir=target, detail='客户端目录未配置或不存在')
    folders = [target, os.path.join(target, 'AetherGazer_Data', 'StreamingAssets', 'Windows'),
               os.path.join(target, 'StreamingAssets', 'Windows'), os.path.join(target, 'StreamingAssets')]
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        primary = os.path.join(folder, 'AssetHash_Info.bytes')
        candidates = [primary] if os.path.isfile(primary) else sorted(
            [os.path.join(folder, name) for name in os.listdir(folder) if ASSETHASH_PATTERN.fullmatch(name)],
            key=os.path.getmtime, reverse=True)
        for path in candidates[:1]:
            try:
                if os.path.getsize(path) > integrity.MAX_MANIFEST_BYTES:
                    raise ValueError('客户端清单过大')
                with open(path, 'rb') as handle:
                    raw = handle.read(integrity.MAX_MANIFEST_BYTES + 1)
                data = json.loads(raw)
                integrity.manifest_items(data)
                name = normalize_version_name(data.get('versionName'))
                stamp = os.path.getmtime(path)
                info.update(detected=True, version=str(data['buildCode']), app_version=str(data['appVersion']),
                    version_name=name, matched_file=os.path.basename(path), sha256=hashlib.sha256(raw).hexdigest(),
                    md5=hashlib.md5(raw).hexdigest(), file_mtime=stamp,
                    file_mtime_str=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stamp)),
                    detail='PC本机只读探测完成；仅匹配已登记原生清单，不改业务版本')
                break
            except (OSError, ValueError, KeyError, TypeError) as error:
                info['detail'] = str(error)
        if info['detected']:
            break
    with _LOCK:
        _DETECTED_CLIENT_INFO = copy.deepcopy(info)
    return info


def apply_auto_client_version(custom_dir: Optional[str] = None, fallback_version: str = DEFAULT_VERSION) -> Tuple[str, dict]:
    # Kept for older callers: automatic local hints no longer mutate the global
    # gameplay build or inject a PC filename into other platforms.
    info = detect_client_resource_version(custom_dir)
    return get_current_version(), info


def get_current_version() -> str:
    bound = _CONNECTION_VERSION.get()
    if bound is not None:
        return bound
    return get_default_version()


def get_default_version() -> str:
    with _LOCK:
        return _CURRENT_VERSION


def enter_connection_version(version=None):
    if version is not None:
        version = str(version)
        get_version_config(version)
    return _CONNECTION_VERSION.set(version)


def leave_connection_version(token):
    _CONNECTION_VERSION.reset(token)


@contextmanager
def connection_version(version=None):
    if version is None:
        yield
        return
    token = enter_connection_version(version)
    try:
        yield
    finally:
        leave_connection_version(token)


def register_version_change_listener(fn):
    with _LOCK:
        if fn not in _VERSION_CHANGE_LISTENERS:
            _VERSION_CHANGE_LISTENERS.append(fn)


def unregister_version_change_listener(fn):
    with _LOCK:
        if fn in _VERSION_CHANGE_LISTENERS:
            _VERSION_CHANGE_LISTENERS.remove(fn)


def _notify_version_changed(old_ver: str, new_ver: str):
    with _LOCK:
        listeners = list(_VERSION_CHANGE_LISTENERS)
    for fn in listeners:
        try:
            fn(old_ver, new_ver)
        except Exception:
            pass


def set_current_version(ver: str) -> Tuple[bool, str]:
    global _CURRENT_VERSION
    if ver is None:
        return False, "版本参数不能为空"
    ver_str = str(ver).strip()
    if ver_str.lower() == "auto":
        applied_ver, detect_info = apply_auto_client_version()
        if detect_info.get("detected"):
            return True, f"自动对齐客户端资源版本成功: {applied_ver} ({detect_info.get('detail')})"
        else:
            return True, f"自动对齐未命中客户端 ({detect_info.get('detail')})，保留或回退至有效版本: {applied_ver}"
    if ver_str not in SUPPORTED_VERSIONS:
        # 尝试看是否可以动态注册
        if ver_str.isdigit():
            try:
                register_version(ver_str)
            except ValueError as error:
                return False, str(error)
        else:
            return False, f"不支持的版本 [{ver_str}]，目前支持版本: {list(SUPPORTED_VERSIONS)}"
    try:
        cfg = get_version_config(ver_str)
    except ValueError as error:
        return False, str(error)
    old_ver = _CURRENT_VERSION
    with _LOCK:
        _CURRENT_VERSION = ver_str
    if old_ver != ver_str:
        _notify_version_changed(old_ver, ver_str)
    return True, f"成功切换客户端资源版本为: {ver_str} ({cfg.get('display_name', '')})"


def get_version_config(ver: Optional[str] = None) -> dict:
    if ver is None:
        ver = get_current_version()
    cfg = VERSION_CONFIGS.get(str(ver))
    if not cfg:
        # 未知构建必须先经清单/官方元数据注册，不能生成不可比较的占位名称。
        v_str = str(ver)
        if v_str.isdigit():
            cfg = register_version(v_str)
        else:
            cfg = VERSION_CONFIGS.get(DEFAULT_VERSION, BUILTIN_VERSION_CONFIGS[DEFAULT_VERSION])
    result = copy.deepcopy(cfg)
    result["version_name"] = normalize_version_name(result.get("version_name"))
    return result


def get_all_version_configs() -> List[dict]:
    curr = get_current_version()
    configs = []
    for k in SUPPORTED_VERSIONS:
        if k in VERSION_CONFIGS:
            c = get_version_config(k)
            c["is_current"] = (k == curr)
            configs.append(c)
    return configs


def get_assethash(platform: str = "pc", ver: Optional[str] = None) -> str:
    cfg = get_version_config(ver)
    plat = str(platform).lower() if platform else "pc"
    return cfg["assethash"].get(plat, cfg["assethash"]["pc"])


def get_voice_package_list(ver: Optional[str] = None) -> str:
    cfg = get_version_config(ver)
    return cfg["voice_package_list"]


# ----------------- 官方上游 DoH 穿透探测引擎 (Upstream DoH Probe) -----------------

PLATFORM_CHANNEL_IDS = {"pc": "9", "android": "8", "ios": "7"}


def probe_official_latest(platform: str = "pc", channel: Optional[str] = None) -> Tuple[bool, dict, str]:
    """
    通过内置 DoH 域名解析直连官方 upstream open.ys4fun.com，
    完全穿透本地 HOSTS 127.0.0.1 重定向，获取官方最新发布的客户端版本信息。
    """
    import cdn_proxy
    plat = platform.lower()
    if plat not in PLATFORM_CHANNEL_IDS:
        return False, {}, f"不支持的平台: {plat}"
    channel = str(channel) if channel is not None else PLATFORM_CHANNEL_IDS[plat]
    ts_str = str(int(time.time()))
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    params = {
        "platformType": plat,
        "appId": OFFICIAL_APP_ID,
        "timestamp": ts_str,
        "nonce": nonce,
        "channelId": str(channel),
    }
    sorted_keys = sorted(params.keys())
    sign_str = "".join([f"{k}={params[k]}&" for k in sorted_keys]) + f"appSecret={OFFICIAL_APP_SECRET}"
    params["signature"] = hashlib.md5(sign_str.encode("utf-8")).hexdigest()
    query_str = "&".join([f"{k}={params[k]}" for k in params])

    status, headers, body = cdn_proxy.fetch_upstream(
        OFFICIAL_API_HOST,
        OFFICIAL_API_PATH,
        query=query_str,
        timeout=10,
    )
    if status != 200 or not body:
        return False, {}, f"请求官方 API 失败，HTTP 状态码: {status}"

    try:
        res_json = json.loads(body.decode("utf-8"))
    except Exception as e:
        return False, {}, f"解析官方响应 JSON 异常: {e}"

    if any(res_json.get(key) not in ("0", 0, None) for key in ("errorCode", "code")):
        return False, {}, f"官方返回错误: {res_json.get('msg') or res_json.get('errorCode')}"

    data_items = res_json.get("data", [])
    if not isinstance(data_items, list):
        return False, {}, "官方版本列表格式无效"
    candidates = []
    for item in data_items:
        if not isinstance(item, dict) or item.get("platformType", plat) != plat:
            continue
        if item.get("channelId") is not None and str(item["channelId"]) != channel:
            continue
        assethash_fn = item.get("assethash")
        try:
            extra = item.get("extraData") or {}
            extra = json.loads(extra) if isinstance(extra, str) else extra
            assethash_fn = assethash_fn or extra.get("assethash")
        except (ValueError, AttributeError, TypeError):
            pass
        match = ASSETHASH_PATTERN.fullmatch(str(assethash_fn or ""))
        app_version = item.get("matchedAppVersion") or (match[1] if match else None)
        version = item.get("version") or (match[2] if match else None)
        candidates.append({
            "type": str(item.get("type", "")), "platform": plat, "channel_id": channel,
            "version": str(version or ""), "version_name": str(item.get("versionName") or ""),
            "app_version": str(app_version or ""), "assethash": assethash_fn or "",
            "download_url": item.get("downloadUrl") or "", "raw": item,
        })

    # App installation and resource builds are separate axes. Never let the last
    # 307 entry displace a newer 308 entry just because the response order changed.
    numeric = lambda value: int(value) if str(value).isdigit() else -1
    resources = [row for row in candidates if row["type"] == "noInstall" and row["assethash"]]
    if not resources:
        return False, {}, "官方接口没有返回可用的资源清单"
    target = max(resources, key=lambda row: (numeric(row["app_version"]), numeric(row["version"])))
    if not target["app_version"]:
        return False, {}, "官方资源清单缺少安装包版本"
    try:
        version_name = normalize_version_name(target["version_name"])
    except ValueError as error:
        return False, {}, f"官方版本名称无效: {error}"

    info = {
        "platform": plat,
        "channel_id": channel,
        "version": target["version"],
        "version_name": str(version_name),
        "app_version": target["app_version"],
        "assethash": target["assethash"],
        "install_version": max((row["version"] for row in candidates if row["type"] == "install"), key=numeric, default=""),
        "download_url": target["download_url"],
        "candidates": [{k: v for k, v in row.items() if k != "raw"} for row in candidates],
        "raw": target["raw"],
    }
    return True, info, "成功拉取官方最新版本元数据"


# ----------------- 资产清单差异比对引擎 (Manifest Diff Engine) -----------------

def _load_manifest_content(platform: str, assethash_filename: str, *, allow_download=True) -> Optional[dict]:
    """Load only a verified platform object, including remote cache population."""
    import cdn_proxy
    try:
        integrity.platform(platform)
        integrity.ObjectSpec(assethash_filename)
        cached = cdn_proxy.find_cached_file(assethash_filename, platform=platform)
        if not cached:
            if not allow_download:
                return None
            cached = cdn_proxy.download_verified(platform, assethash_filename,
                        os.path.join(STATIC_RESOURCES_DIR, platform))
        with open(cached, 'rb') as handle:
            return integrity.decode_manifest(handle.read(integrity.MAX_MANIFEST_BYTES + 1), assethash_filename)
    except (OSError, ValueError, http.client.HTTPException):
        return None


def manifest_download_status(platform, filename):
    """Only the requested platform's static folder counts as saved here."""
    integrity.platform(platform)
    if not isinstance(filename, str) or not re.fullmatch(r'assethash_\d+_\d+_[0-9a-fA-F]{32}\.bytes', filename):
        return dict(present=False, valid=False)
    path = integrity.safe_path(os.path.join(STATIC_RESOURCES_DIR, platform), filename)
    present = os.path.lexists(path)
    return dict(present=present, valid=present and integrity.valid_file(path, integrity.ObjectSpec(filename)))


def download_official_manifest(platform, filename, channel_id=None):
    """Explicit main-manifest save; never change versions or download assets."""
    import cdn_proxy
    plat = integrity.platform(platform)
    channel = PLATFORM_CHANNEL_IDS[plat]
    if channel_id is not None and str(channel_id) != channel:
        raise ValueError('平台与渠道不匹配')
    if not isinstance(filename, str) or not re.fullmatch(r'assethash_\d+_\d+_[0-9a-fA-F]{32}\.bytes', filename):
        raise ValueError('请提供官方返回的完整资源清单文件名')
    folder = os.path.join(STATIC_RESOURCES_DIR, plat)
    path = integrity.safe_path(folder, filename)
    def result(downloaded, skipped):
        return dict(platform=plat, channel_id=channel, filename=filename,
                    relative_path=f'static_resources/{plat}/{filename}',
                    downloaded=downloaded, skipped=skipped,
                    **manifest_download_status(plat, filename))
    if os.path.lexists(path):
        return result(False, True)
    from cdn_transfer_control import gate, current_token
    if current_token() is None:
        with gate.active(deadline=time.monotonic()+120):
            return download_official_manifest(platform, filename, channel_id)
    success, info, message = probe_official_latest(platform=plat)
    if not success:
        raise integrity.IntegrityError(message)
    if info.get('assethash') != filename or str(info.get('channel_id')) != channel:
        raise ValueError('官方清单已变化或渠道不匹配，请重新检测')
    match = ASSETHASH_PATTERN.fullmatch(filename)
    if (match[1], match[2]) != (str(info.get('app_version')), str(info.get('version'))):
        raise integrity.IntegrityError('官方元数据与清单身份不一致')
    # The metadata request above may race another save. Existing files remain
    # untouched, including invalid files which must not be silently overwritten.
    if os.path.lexists(path):
        return result(False, True)
    cached = cdn_proxy.find_cached_file(filename, platform=plat)
    if cached:
        integrity.copy_verified(cached, folder, integrity.ObjectSpec(filename))
    else:
        cdn_proxy.download_verified(plat, filename, folder, deadline=time.monotonic()+90, max_attempts=3)
    if not manifest_download_status(plat, filename)['valid']:
        raise integrity.IntegrityError('清单保存后校验失败')
    return result(True, False)


def calculate_manifest_diff(local_ver: Optional[str] = None, remote_ver: Optional[str] = None, platform: str = "pc", *, remote_metadata: Optional[dict] = None, allow_download=True) -> dict:
    """
    比对指定本地版本与远端最新版本的 assethash 清单，
    输出详细差异报表（修改/新增/删除文件清单及总下载字节数）。
    """
    if local_ver is None:
        local_ver = get_current_version()
    plat = platform.lower()

    # 获取本地清单
    local_cfg = get_version_config(local_ver)
    local_hash_fn = local_cfg["assethash"].get(plat, local_cfg["assethash"]["pc"])
    load_options = {} if allow_download else {'allow_download': False}
    local_manifest = _load_manifest_content(plat, local_hash_fn, **load_options)

    # 获取远端版本信息
    remote_info = {}
    if remote_metadata is not None:
        remote_info = remote_metadata
        remote_hash_fn = remote_info.get("assethash")
    elif remote_ver and remote_ver in VERSION_CONFIGS:
        rem_cfg = get_version_config(remote_ver)
        remote_hash_fn = rem_cfg["assethash"].get(plat, rem_cfg["assethash"]["pc"])
        remote_info = {
            "version": remote_ver,
            "version_name": rem_cfg.get("version_name", remote_ver),
            "assethash": remote_hash_fn,
            "app_version": rem_cfg.get("matched_app_version", ""),
        }
    else:
        ok, probed, msg = probe_official_latest(platform=plat)
        if ok:
            remote_info = probed
            remote_hash_fn = probed.get("assethash")
        else:
            return {
                "success": False,
                "error": f"无法获取官方远端版本: {msg}",
                "local_version": local_ver,
            }

    metadata = {
        "platform": plat, "channel_id": remote_info.get("channel_id", PLATFORM_CHANNEL_IDS.get(plat, "")),
        "local_version": local_ver,
        "local_version_name": (local_manifest or {}).get("versionName", local_cfg.get("version_name", "")),
        "local_app_version": str((local_manifest or {}).get("appVersion") or (ASSETHASH_PATTERN.fullmatch(local_hash_fn)[1] if ASSETHASH_PATTERN.fullmatch(local_hash_fn) else "")),
        "local_assethash": local_hash_fn,
        "remote_version": remote_info.get("version"),
        "remote_version_name": remote_info.get("version_name", ""),
        "remote_app_version": str(remote_info.get("app_version") or ""),
        "remote_assethash": remote_hash_fn,
        "remote_install_version": remote_info.get("install_version", ""),
        "remote_candidates": remote_info.get("candidates", []),
    }
    remote_manifest = _load_manifest_content(plat, remote_hash_fn, **load_options) if remote_hash_fn else None
    if not local_manifest:
        return {
            "success": False,
            "error": f"未能加载本地版本 [{local_ver}] 的清单: {local_hash_fn}",
            **metadata,
        }
    if not remote_manifest:
        return {
            "success": False,
            "error": f"未能加载远端清单: {remote_hash_fn}",
            **metadata,
        }

    try:
        local_map = integrity.manifest_items(local_manifest)
        remote_map = integrity.manifest_items(remote_manifest)
    except ValueError as error:
        return {"success": False, "error": str(error), **metadata}

    local_keys = set(local_map.keys())
    remote_keys = set(remote_map.keys())

    added_keys = remote_keys - local_keys
    removed_keys = local_keys - remote_keys
    common_keys = local_keys & remote_keys

    modified_files = []
    modified_total_bytes = 0

    for k in sorted(common_keys):
        old_h, old_sz = local_map[k]
        new_h, new_sz = remote_map[k]
        if old_h != new_h or old_sz != new_sz:
            modified_total_bytes += new_sz
            modified_files.append({
                "path": k,
                "old_hash": old_h,
                "old_size": old_sz,
                "new_hash": new_h,
                "new_size": new_sz,
                "size_diff": new_sz - old_sz,
            })

    added_files = []
    added_total_bytes = 0
    for k in sorted(added_keys):
        h, sz = remote_map[k]
        added_total_bytes += sz
        added_files.append({
            "path": k,
            "new_hash": h,
            "new_size": sz,
        })

    removed_files = [{"path": k, "old_hash": local_map[k][0], "old_size": local_map[k][1]} for k in sorted(removed_keys)]

    rem_ver = remote_info.get("version")
    has_update = (str(local_ver) != str(rem_ver)) or local_hash_fn != remote_hash_fn or bool(modified_files or added_files or removed_files)

    return {
        "success": True,
        "diff_ready": True,
        "has_update": has_update,
        **metadata,
        "total_local_assets": len(local_map),
        "total_remote_assets": len(remote_map),
        "diff_summary": {
            "added_count": len(added_files),
            "added_total_bytes": added_total_bytes,
            "removed_count": len(removed_files),
            "modified_count": len(modified_files),
            "modified_total_bytes": modified_total_bytes,
            "total_download_bytes": added_total_bytes + modified_total_bytes,
            "total_download_mb": round((added_total_bytes + modified_total_bytes) / (1024 * 1024), 2),
            "modified_files": modified_files,
            "added_files": added_files,
            "removed_files": removed_files,
        },
    }


# ----------------- 后台更新任务管理器 (Thread-Safe UpdateTaskManager) -----------------

class UpdateTaskManager:
    """One worker owns one cancellation token until it has actually terminated."""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread = None
        self._cancel = threading.Event()
        self._state = dict(status='idle', progress_percent=0.0, current_file='',
                           downloaded_files=0, total_files=0, downloaded_bytes=0,
                           total_bytes=0, speed_kbps=0.0, message='空闲', error=None,
                           target_version='', version_name='', diff_summary=None, task_id='',
                           active_downloads=0)

    def get_progress(self):
        with self._lock:
            return {**copy.deepcopy(self._state), 'max_concurrency': active_downloads.settings.limit()}

    def cancel(self):
        with self._lock:
            # Registration is a short atomic commit: once started it finishes.
            if self._state['status'] in ('checking', 'downloading', 'cancelling'):
                self._cancel.set()
                self._state.update(status='cancelling', message='正在停止下载，请稍候')

    def is_busy(self):
        with self._lock:
            return bool(self._thread and self._thread.is_alive()) or self._state['status'] in (
                'checking', 'downloading', 'registering', 'cancelling')

    def start_sync(self, target_version=None, download_diff_assets=True,
                   platforms=('pc', 'android', 'ios')):
        if not platforms or len(set(platforms)) != len(platforms) or any(p not in ('pc', 'ios', 'android') for p in platforms):
            return False, '请选择有效且不重复的资源平台'
        with self._lock:
            if self.is_busy():
                return False, '已有任务正在执行或停止，请等待结束'
            self._cancel = threading.Event()
            self._state.update(status='checking', progress_percent=0.0, current_file='',
                               downloaded_files=0, total_files=0, downloaded_bytes=0,
                               total_bytes=0, speed_kbps=0.0, error=None,
                               message='正在探测官方资源', target_version=target_version or '',
                               version_name='', diff_summary=None, task_id=uuid.uuid4().hex,
                               active_downloads=0)
            self._thread = threading.Thread(target=self._run_worker,
                args=(target_version, download_diff_assets, tuple(platforms)), daemon=True)
            self._thread.start()
        return True, '版本同步任务已启动'

    def _update_state(self, **kwargs):
        with self._lock:
            self._state.update(kwargs)

    def _run_worker(self, target_version, download_diff_assets, platforms):
        from cdn_transfer_control import gate
        try:
            with gate.active(self._cancel, time.monotonic()+4*3600):
                self._run_worker_active(target_version, download_diff_assets, platforms)
        except integrity.Cancelled:
            self._update_state(status='cancelled', message='已停止或让出给被动下载')
        except Exception as error:
            self._update_state(status='error', error=str(error), message='同步失败')

    def _run_worker_active(self, target_version, download_diff_assets, platforms):
        import cdn_proxy
        import resource_distribution
        cancel = self._cancel
        deadline = time.monotonic() + 4 * 3600
        try:
            plans = {}
            target_build = None
            for plat in platforms:
                integrity.checkpoint(cancel, deadline)
                ok, info, error = probe_official_latest(platform=plat)
                if not ok:
                    raise integrity.IntegrityError(f'探测平台 [{plat}] 失败: {error}')
                build = str(info['version'])
                if target_version and str(target_version) != build:
                    raise integrity.IntegrityError('请求版本与官方实际资源版本不一致，请重新检测')
                if target_build is not None and target_build != build:
                    raise integrity.IntegrityError('各平台资源版本不一致，请分平台同步')
                target_build = build
                integrity.ObjectSpec(info['assethash'])
                manifest_identity = ASSETHASH_PATTERN.fullmatch(info['assethash'])
                if not manifest_identity or (manifest_identity[1], manifest_identity[2]) != (str(info['app_version']), build):
                    raise integrity.IntegrityError('官方版本信息与清单身份不一致')
                plans[plat] = info
            first = plans[platforms[0]]
            self._update_state(target_version=target_build, version_name=first['version_name'])
            diffs = {}
            asset_jobs = {}
            for plat in platforms:
                integrity.checkpoint(cancel, deadline)
                result = calculate_manifest_diff(
                    local_ver=resource_distribution.get_platform_config(plat)['version'],
                    remote_ver=target_build, platform=plat, remote_metadata=plans[plat])
                integrity.checkpoint(cancel, deadline)
                if download_diff_assets and not result.get('success'):
                    raise integrity.IntegrityError(f'[{plat}] 无法得到可信增量清单: {result.get("error", "unknown")}')
                summary = result.get('diff_summary') or {}
                diffs[plat] = summary
                if download_diff_assets:
                    for entry in summary.get('modified_files', []) + summary.get('added_files', []):
                        spec = integrity.ObjectSpec(str(entry['new_hash']) + '.ys', entry['new_size'], entry['new_hash'])
                        key = (plat, spec.filename)
                        if key in asset_jobs and asset_jobs[key] != spec:
                            raise integrity.IntegrityError('增量对象规格冲突')
                        asset_jobs[key] = spec
            manifest_jobs = []
            for plat, info in plans.items():
                app, build = str(info['app_version']), str(info['version'])
                if not app.isdigit() or not build.isdigit():
                    raise integrity.IntegrityError('Invalid upstream version fields')
                for name in (info['assethash'], f'voice_package_list_{app}_{build}.bytes',
                             f'voice_hash_zh_{app}00{build}.bytes', f'voice_hash_ja_{app}00{build}.bytes'):
                    manifest_jobs.append((plat, integrity.ObjectSpec(name)))
            self._update_state(status='downloading', total_files=len(manifest_jobs) + len(asset_jobs),
                total_bytes=sum(spec.size for spec in asset_jobs.values()),
                diff_summary=diffs[platforms[0]] if len(platforms) == 1 else diffs,
                message='正在校验并同步资源')
            started = time.monotonic()
            count, byte_count = 0, 0
            def completed(path, manifest=False):
                nonlocal count, byte_count
                count += 1
                byte_count += os.path.getsize(path)
                total = self.get_progress()['total_files']
                manifest_bytes = os.path.getsize(path) if manifest else 0
                self._update_state(downloaded_files=count, downloaded_bytes=byte_count,
                    total_bytes=self.get_progress()['total_bytes'] + manifest_bytes,
                    progress_percent=min(99, round(100 * count / max(1, total), 1)),
                    speed_kbps=round(byte_count / 1024 / max(0.1, time.monotonic() - started), 1))
            def manifest_work(item):
                plat, spec = item
                integrity.checkpoint(cancel, deadline)
                self._update_state(current_file=f'[{plat}] {spec.filename}')
                path = cdn_proxy.download_verified(plat, spec.filename, cancel=cancel, deadline=deadline,
                                                   force=spec.md5 is None)
                integrity.copy_verified(path, os.path.join(STATIC_RESOURCES_DIR, plat), spec,
                                        cancel=cancel, deadline=deadline)
                return path
            def asset_work(item):
                (plat, _), spec = item
                integrity.checkpoint(cancel, deadline)
                self._update_state(current_file=f'[{plat}] {spec.filename}')
                path = cdn_proxy.download_verified(plat, spec.filename, expected_size=spec.size,
                    expected_md5=spec.md5, cancel=cancel, deadline=deadline)
                return path
            def window(active, _limit):
                self._update_state(active_downloads=active)
            active_downloads.run_batch(manifest_jobs, manifest_work,
                lambda path: completed(path, manifest=True), cancel=cancel,
                deadline=deadline, on_window=window)
            active_downloads.run_batch(asset_jobs.items(), asset_work, completed,
                cancel=cancel, deadline=deadline, on_window=window)
            integrity.checkpoint(cancel, deadline)
            # Serialize cancel vs commit. A cancellation acknowledged before this
            # point can never register a new version.
            with self._lock:
                integrity.checkpoint(cancel, deadline)
                self._state.update(status='registering', message='正在提交已验证版本')
                previous = get_version_config(target_build) if target_build in VERSION_CONFIGS else {}
                app = str(first['app_version'])
                config = {**previous, **get_baseline_for_build(int(target_build)), 'version': target_build,
                    'version_name': first['version_name'],
                    'display_name': f'Build {target_build} ({first["version_name"]})',
                    'matched_app_version': app,
                    'assethash': {**previous.get('assethash', {}), **{p: i['assethash'] for p, i in plans.items()}},
                    'voice_package_list': f'voice_package_list_{app}_{target_build}.bytes',
                    'voice_hashes': {lang: f'voice_hash_{lang}_{app}00{target_build}.bytes' for lang in ('ja', 'zh')}}
                resource_distribution.commit_official_bundles(plans,
                    lambda: register_version(target_build, config, persist=True))
                self._state.update(status='completed', progress_percent=100.0,
                                   message='全部必需资源已验证并完成同步')
        except integrity.Cancelled:
            self._update_state(status='cancelled', message='任务已停止，未完成文件保留且不参与分发')
        except Exception as error:
            self._update_state(status='error', error=str(error), message='同步失败，未提交新版本')


update_task_manager = UpdateTaskManager()


# ----------------- CLI 终端更新检查与执行入口 -----------------

def check_and_print_update(sync: bool = False, platform: str = "pc"):
    """供命令行使用的交互式/自省式版本更新检测与下载工具。"""
    if sync:
        from tools.download_assets import main as download_main
        return download_main(['--platform',platform,'--scope','all'])
    curr_ver = get_current_version()
    print("=" * 68)
    print(f"[*] 客户端版本管理单元 — 官方更新检查 (当前生效: Build {curr_ver})")
    print("=" * 68)
    print("[1/3] 正在通过内置 DoH 域名穿透直连 open.ys4fun.com 探测官方版本...")
    ok, p_info, err = probe_official_latest(platform=platform)
    if not ok:
        print(f"[-] 探测失败: {err}")
        return

    rem_ver = p_info["version"]
    rem_vname = p_info["version_name"]
    print(f"[+] 官方最新版本: Build {rem_ver} ({rem_vname}), App: {p_info['app_version']}")
    print(f"    官方 AssetHash: {p_info.get('assethash')}")

    print("[2/3] 正在分析本地与远端资源清单差异...")
    diff = calculate_manifest_diff(local_ver=curr_ver, remote_ver=rem_ver, platform=platform)
    if not diff.get("success"):
        print(f"[-] 差异计算失败: {diff.get('error')}")
        return

    summary = diff.get("diff_summary", {})
    mod_count = summary.get("modified_count", 0)
    add_count = summary.get("added_count", 0)
    del_count = summary.get("removed_count", 0)
    dl_mb = summary.get("total_download_mb", 0)

    print(f"[+] 资源比对结论: {'发现官方新版本/资源增量！' if diff.get('has_update') else '本地资源已是最新！'}")
    print(f"    - 新增资产: {add_count} 个")
    print(f"    - 删除资产: {del_count} 个")
    print(f"    - 变更资产: {mod_count} 个 (累计需同步: {dl_mb} MB)")

    if mod_count > 0:
        print("    - 核心变更文件预览 (前 15 项):")
        for m in summary.get("modified_files", [])[:15]:
            p = m["path"]
            old_s = m["old_size"]
            new_s = m["new_size"]
            print(f"      - {p}: {old_s} 字节 -> {new_s} 字节")

    if not sync:
        print("\n[提示] 如需立即同步下载官方新版本资源，请执行: python main.py --sync-update")
        print("=" * 68)
        return

    print("\n[3/3] 开始同步官方版本清单与差异资源...")
    started, msg = update_task_manager.start_sync(
        target_version=rem_ver,
        download_diff_assets=True,
        platforms=("pc", "android", "ios"),
    )
    if not started:
        print(f"[-] {msg}")
        return

    while True:
        prog = update_task_manager.get_progress()
        st = prog["status"]
        pct = prog["progress_percent"]
        f_cnt = f"{prog['downloaded_files']}/{prog['total_files']}"
        b_mb = round(prog["downloaded_bytes"] / (1024 * 1024), 2)
        spd = prog["speed_kbps"]
        cf = prog["current_file"]
        if cf and len(cf) > 35:
            cf = cf[:32] + "..."
        print(f"\r[{st.upper():<10}] {pct:5.1f}% | 文件: {f_cnt} | 已下: {b_mb:6.1f}MB | 速度: {spd:6.1f}KB/s | 当前: {cf:<35}", end="", flush=True)
        if st in ("completed", "error", "cancelled"):
            print("\n")
            if st == "completed":
                print(f"[+] {prog['message']}")
            else:
                print(f"[-] {prog['error']}")
            break
        time.sleep(0.5)
    print("=" * 68)
