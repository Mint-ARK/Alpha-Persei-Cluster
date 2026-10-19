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
    """将非内置的动态版本序列化到 dynamic_versions.json。"""
    try:
        dynamic_only = {}
        for k, v in VERSION_CONFIGS.items():
            if k not in BUILTIN_VERSION_CONFIGS:
                dynamic_only[k] = v
        with open(DYNAMIC_VERSIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(dynamic_only, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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
        VERSION_CONFIGS[ver_str] = cfg
        if ver_str not in SUPPORTED_VERSIONS:
            SUPPORTED_VERSIONS.append(ver_str)
        if persist:
            _save_persisted_dynamic_versions()
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
    """
    扫描目标客户端目录下的核心特征版本校验文件。
    优先检查 AssetHash_Info.bytes（自动计算哈希并解析 buildCode），兼容 assethash_*_*_*.bytes 历史索引。
    若探测到官方新版（不在已知列表中），自动基于基线模型完成动态注册并准入！
    """
    global _DETECTED_CLIENT_INFO
    target_dir = custom_dir.strip() if custom_dir and isinstance(custom_dir, str) else None
    if not target_dir:
        target_dir = os.environ.get(ENV_CLIENT_STREAMING_ASSETS_DIR, "").strip() or DEFAULT_CLIENT_STREAMING_ASSETS_DIR

    result = {
        "detected": False,
        "version": None,
        "matched_file": None,
        "file_mtime": 0,
        "file_mtime_str": "",
        "client_dir": target_dir,
        "detail": "",
    }

    if not target_dir or not os.path.isdir(target_dir):
        result["detail"] = f"客户端 StreamingAssets 目录未配置或不存在: {target_dir or '未配置'}"
        with _LOCK:
            _DETECTED_CLIENT_INFO = copy.deepcopy(result)
        return result

    # 深度寻址：若传入的是游戏根目录，自动下潜寻找真实的资源存储子目录
    active_dir = target_dir
    candidates = [
        target_dir,
        os.path.join(target_dir, "AetherGazer_Data", "StreamingAssets", "Windows"),
        os.path.join(target_dir, "StreamingAssets", "Windows"),
        os.path.join(target_dir, "StreamingAssets"),
    ]
    for cd in candidates:
        if os.path.isdir(cd):
            if os.path.isfile(os.path.join(cd, "AssetHash_Info.bytes")):
                active_dir = cd
                break
            try:
                if any(ASSETHASH_PATTERN.match(f) for f in os.listdir(cd)):
                    active_dir = cd
                    break
            except Exception:
                pass

    # 路径一：优先探测 AssetHash_Info.bytes 官方核心索引
    info_file = os.path.join(active_dir, "AssetHash_Info.bytes")
    if os.path.isfile(info_file) and os.path.getsize(info_file) > 0:
        try:
            mtime = os.path.getmtime(info_file)
            mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) if mtime > 0 else ""
            with open(info_file, "r", encoding="utf-8") as f:
                jdata = json.load(f)
            b_code = str(jdata.get("buildCode", "")).strip()
            v_name = str(jdata.get("versionName", "")).strip()
            app_ver = str(jdata.get("appVersion", "307")).strip()

            with open(info_file, "rb") as f:
                hash_md5 = hashlib.md5(f.read()).hexdigest().lower()

            if b_code:
                extracted_ver = b_code
                try:
                    v_name = normalize_version_name(v_name)
                except ValueError as error:
                    result["version"] = extracted_ver
                    result["detail"] = f"客户端核心索引的版本名称无效: {error}"
                    with _LOCK:
                        _DETECTED_CLIENT_INFO = copy.deepcopy(result)
                    return result
                result["matched_file"] = f"AssetHash_Info.bytes (MD5: {hash_md5})"
                result["file_mtime"] = mtime
                result["file_mtime_str"] = mtime_str

                if extracted_ver not in SUPPORTED_VERSIONS:
                    b_num = int(extracted_ver) if extracted_ver.isdigit() else 321
                    baseline = get_baseline_for_build(b_num)
                    new_cfg = {
                        "version": extracted_ver,
                        "version_name": v_name,
                        "display_name": f"Build {extracted_ver} ({v_name})",
                        "matched_app_version": app_ver,
                        "assethash": {
                            "pc": f"assethash_{app_ver}_{extracted_ver}_{hash_md5}.bytes",
                            "android": f"assethash_{app_ver}_{extracted_ver}_{hash_md5}.bytes",
                            "ios": f"assethash_{app_ver}_{extracted_ver}_{hash_md5}.bytes",
                        },
                        "voice_package_list": f"voice_package_list_{app_ver}_{extracted_ver}.bytes",
                        "voice_hashes": {
                            "ja": f"voice_hash_ja_{app_ver}00{extracted_ver}.bytes",
                            "zh": f"voice_hash_zh_{app_ver}00{extracted_ver}.bytes",
                        },
                        **baseline,
                    }
                    register_version(extracted_ver, new_cfg, persist=True)

                result["detected"] = True
                result["version"] = extracted_ver
                cfg = VERSION_CONFIGS.get(extracted_ver, {})
                result["detail"] = (
                    f"成功通过客户端核心索引识别生效版本 Build {extracted_ver} ({cfg.get('display_name', '')}), "
                    f"特征MD5: {hash_md5} (修改时间: {mtime_str})"
                )
                with _LOCK:
                    _DETECTED_CLIENT_INFO = copy.deepcopy(result)
                return result
        except Exception:
            pass

    # 路径二：回退扫描 assethash_*_*_*.bytes 索引
    matched_entries = []
    try:
        for fname in os.listdir(active_dir):
            m = ASSETHASH_PATTERN.match(fname)
            if m:
                app_ver, res_ver, hash_str = m.group(1), m.group(2), m.group(3)
                full_path = os.path.join(active_dir, fname)
                try:
                    mtime = os.path.getmtime(full_path)
                except OSError:
                    mtime = 0.0
                matched_entries.append({
                    "filename": fname,
                    "full_path": full_path,
                    "app_ver": app_ver,
                    "res_ver": res_ver,
                    "hash": hash_str,
                    "mtime": mtime,
                })
    except Exception as e:
        result["detail"] = f"扫描客户端资源目录异常: {e}"
        with _LOCK:
            _DETECTED_CLIENT_INFO = copy.deepcopy(result)
        return result

    if not matched_entries:
        result["detail"] = f"在客户端目录 [{active_dir}] 下未找到合规的 AssetHash_Info.bytes 或 assethash_*_*_*.bytes 索引文件"
        with _LOCK:
            _DETECTED_CLIENT_INFO = copy.deepcopy(result)
        return result

    matched_entries.sort(key=lambda x: x["mtime"], reverse=True)
    latest_entry = matched_entries[0]
    extracted_ver = str(latest_entry["res_ver"]).strip()
    mtime_val = latest_entry["mtime"]
    mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime_val)) if mtime_val > 0 else ""

    result["matched_file"] = latest_entry["filename"]
    result["file_mtime"] = mtime_val
    result["file_mtime_str"] = mtime_str

    # 动态版本自愈：若客户端包含新版本，自动调用 register_version 接入
    if extracted_ver not in SUPPORTED_VERSIONS:
        try:
            with open(latest_entry["full_path"], "r", encoding="utf-8") as f:
                manifest = json.load(f)
            v_name = normalize_version_name(manifest.get("versionName"))
        except (OSError, ValueError, AttributeError) as error:
            result["version"] = extracted_ver
            result["detail"] = f"Build [{extracted_ver}] 清单缺少可用的版本名称，未自动注册: {error}"
            with _LOCK:
                _DETECTED_CLIENT_INFO = copy.deepcopy(result)
            return result
        app_ver = latest_entry["app_ver"]
        b_num = int(extracted_ver) if extracted_ver.isdigit() else 311
        baseline = get_baseline_for_build(b_num)
        new_cfg = {
            "version": extracted_ver,
            "version_name": v_name,
            "display_name": f"Build {extracted_ver} (客户端探测并自动接入)",
            "matched_app_version": app_ver,
            "assethash": {
                "pc": latest_entry["filename"],
                "android": latest_entry["filename"],
                "ios": latest_entry["filename"],
            },
            "voice_package_list": f"voice_package_list_{app_ver}_{extracted_ver}.bytes",
            "voice_hashes": {
                "ja": f"voice_hash_ja_{app_ver}00{extracted_ver}.bytes",
                "zh": f"voice_hash_zh_{app_ver}00{extracted_ver}.bytes",
            },
            **baseline,
        }
        register_version(extracted_ver, new_cfg, persist=True)

    result["detected"] = True
    result["version"] = extracted_ver
    cfg = VERSION_CONFIGS.get(extracted_ver, {})
    result["detail"] = (
        f"成功探测并识别到客户端生效版本 Build {extracted_ver} ({cfg.get('display_name', '')}), "
        f"特征文件: {latest_entry['filename']} (修改时间: {mtime_str})"
    )

    with _LOCK:
        _DETECTED_CLIENT_INFO = copy.deepcopy(result)
    return result


def apply_auto_client_version(custom_dir: Optional[str] = None, fallback_version: str = DEFAULT_VERSION) -> Tuple[str, dict]:
    info = detect_client_resource_version(custom_dir=custom_dir)
    if info.get("detected") and info.get("version") in SUPPORTED_VERSIONS:
        target_ver = info["version"]
        set_current_version(target_ver)
        return target_ver, info
    else:
        if info.get("version"):
            # 已识别构建但名称无效，保持当前有效配置，不能猜测该构建的版本名称。
            return get_current_version(), info
        target_ver = fallback_version if fallback_version in SUPPORTED_VERSIONS else DEFAULT_VERSION
        set_current_version(target_ver)
        return target_ver, info


def get_current_version() -> str:
    with _LOCK:
        return _CURRENT_VERSION


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

def probe_official_latest(platform: str = "pc", channel: str = "9") -> Tuple[bool, dict, str]:
    """
    通过内置 DoH 域名解析直连官方 upstream open.ys4fun.com，
    完全穿透本地 HOSTS 127.0.0.1 重定向，获取官方最新发布的客户端版本信息。
    """
    import cdn_proxy
    plat = platform.lower()
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

    if res_json.get("errorCode") not in ("0", 0, None) and res_json.get("code") not in (0, "0", None):
        return False, {}, f"官方返回错误: {res_json.get('msg') or res_json.get('errorCode')}"

    data_items = res_json.get("data", [])
    no_install_item = None
    install_item = None
    for item in data_items:
        if item.get("type") == "noInstall":
            no_install_item = item
        elif item.get("type") == "install":
            install_item = item

    target_item = no_install_item or install_item or (data_items[0] if data_items else None)
    if not target_item:
        return False, {}, "官方接口返回版本列表为空"

    version = target_item.get("version")
    version_name = target_item.get("versionName")
    try:
        version_name = normalize_version_name(version_name)
    except ValueError as error:
        return False, {}, f"官方版本名称无效: {error}"
    app_version = target_item.get("matchedAppVersion") or target_item.get("version") or "307"
    assethash_fn = target_item.get("assethash")

    # 若未直接带 assethash，尝试从 extraData 解析
    if not assethash_fn and target_item.get("extraData"):
        try:
            extra = json.loads(target_item["extraData"])
            assethash_fn = extra.get("assethash")
        except Exception:
            pass

    info = {
        "platform": plat,
        "version": str(version),
        "version_name": str(version_name),
        "app_version": str(app_version),
        "assethash": assethash_fn,
        "download_url": target_item.get("downloadUrl"),
        "raw": target_item,
    }
    return True, info, "成功拉取官方最新版本元数据"


# ----------------- 资产清单差异比对引擎 (Manifest Diff Engine) -----------------

def _load_manifest_content(platform: str, assethash_filename: str) -> Optional[dict]:
    """尝试从本地 static_resources 或专属数据目录读取清单，若本地缺失则经由 DoH 直接从官方 CDN 读取。"""
    import cdn_proxy
    plat = platform.lower()
    # 1. 本地查找
    cached = cdn_proxy.find_cached_file(assethash_filename, platform=plat)
    if cached and os.path.isfile(cached) and os.path.getsize(cached) > 0:
        try:
            with open(cached, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # 2. 远端拉取
    url_path = f"/{plat}/resources/{assethash_filename}"
    st, hdrs, body = cdn_proxy.fetch_upstream(OFFICIAL_CDN_HOST, url_path, timeout=15)
    if st == 200 and body:
        try:
            # 顺手缓存在本地以加速后续比对
            plat_static = os.path.join(STATIC_RESOURCES_DIR, plat)
            os.makedirs(plat_static, exist_ok=True)
            save_path = os.path.join(plat_static, assethash_filename)
            with open(save_path, "wb") as f:
                f.write(body)
            return json.loads(body.decode("utf-8"))
        except Exception:
            pass
    return None


def calculate_manifest_diff(local_ver: Optional[str] = None, remote_ver: Optional[str] = None, platform: str = "pc") -> dict:
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
    local_manifest = _load_manifest_content(plat, local_hash_fn)

    # 获取远端版本信息
    remote_info = {}
    if remote_ver and remote_ver in VERSION_CONFIGS:
        rem_cfg = get_version_config(remote_ver)
        remote_hash_fn = rem_cfg["assethash"].get(plat, rem_cfg["assethash"]["pc"])
        remote_info = {
            "version": remote_ver,
            "version_name": rem_cfg.get("version_name", remote_ver),
            "assethash": remote_hash_fn,
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

    remote_manifest = _load_manifest_content(plat, remote_hash_fn)
    if not local_manifest:
        return {
            "success": False,
            "error": f"未能加载本地版本 [{local_ver}] 的清单: {local_hash_fn}",
            "local_version": local_ver,
        }
    if not remote_manifest:
        return {
            "success": False,
            "error": f"未能加载远端清单: {remote_hash_fn}",
            "local_version": local_ver,
            "remote_version": remote_info.get("version"),
        }

    # 解析 assetHashList
    def parse_hash_list(h_list: list) -> Dict[str, Tuple[str, int]]:
        res = {}
        for line in h_list:
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    size = int(parts[2])
                except ValueError:
                    size = 0
                res[parts[0]] = (parts[1], size)
        return res

    local_map = parse_hash_list(local_manifest.get("assetHashList", []))
    remote_map = parse_hash_list(remote_manifest.get("assetHashList", []))

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
    has_update = (local_ver != rem_ver) or (len(modified_files) > 0) or (len(added_files) > 0)

    return {
        "success": True,
        "has_update": has_update,
        "platform": plat,
        "local_version": local_ver,
        "local_version_name": local_manifest.get("versionName", local_ver),
        "remote_version": rem_ver,
        "remote_version_name": remote_info.get("version_name", rem_ver),
        "remote_assethash": remote_hash_fn,
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
    """负责跨平台清单下载与增量资产同步的线程安全单例管理器。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel_requested = False
        self._state: Dict[str, Any] = {
            "status": "idle",  # idle | checking | downloading | registering | completed | error
            "progress_percent": 0.0,
            "current_file": "",
            "downloaded_files": 0,
            "total_files": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_kbps": 0.0,
            "message": "空闲",
            "error": None,
            "target_version": "",
            "version_name": "",
            "diff_summary": None,
        }

    def get_progress(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def cancel(self):
        with self._lock:
            self._cancel_requested = True
            if self._state["status"] in ("checking", "downloading"):
                self._state["status"] = "idle"
                self._state["message"] = "任务已被用户取消"

    def is_busy(self) -> bool:
        with self._lock:
            return self._state["status"] in ("checking", "downloading", "registering")

    def start_sync(
        self,
        target_version: Optional[str] = None,
        download_diff_assets: bool = True,
        platforms: Tuple[str, ...] = ("pc", "android", "ios"),
    ) -> Tuple[bool, str]:
        with self._lock:
            if self._state["status"] in ("checking", "downloading", "registering"):
                return False, "已有正在执行的版本更新任务"

            self._cancel_requested = False
            self._state = {
                "status": "checking",
                "progress_percent": 0.0,
                "current_file": "",
                "downloaded_files": 0,
                "total_files": 0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "speed_kbps": 0.0,
                "message": "正在探测官方最新版本与差异清单...",
                "error": None,
                "target_version": target_version or "",
                "version_name": "",
                "diff_summary": None,
            }

        self._thread = threading.Thread(
            target=self._run_worker,
            args=(target_version, download_diff_assets, platforms),
            daemon=True,
        )
        self._thread.start()
        return True, "版本更新同步任务已在后台启动"

    def _update_state(self, **kwargs):
        with self._lock:
            self._state.update(kwargs)

    def _run_worker(self, target_version, download_diff_assets, platforms):
        import cdn_proxy
        import shutil

        try:
            self._update_state(status="checking", message="正在通过 DoH 穿透探测官方最新版本...")

            # 1. 探测各平台最新版本信息
            plat_manifest_map = {}
            target_build = None
            target_vname = None
            target_app_ver = "307"

            for plat in platforms:
                if self._cancel_requested:
                    return
                ok, p_info, err = probe_official_latest(platform=plat)
                if not ok:
                    self._update_state(status="error", error=f"探测平台 [{plat}] 失败: {err}")
                    return
                plat_manifest_map[plat] = p_info
                if not target_build:
                    target_build = p_info["version"]
                    target_vname = p_info["version_name"]
                    target_app_ver = p_info["app_version"]

            if target_version:
                target_build = str(target_version)

            self._update_state(
                target_version=target_build,
                version_name=target_vname,
                message=f"已锁定目标版本: Build {target_build} ({target_vname})，开始同步清单与资源...",
            )

            # 2. 计算 PC 端差异
            diff_res = calculate_manifest_diff(
                local_ver=get_current_version(),
                remote_ver=target_build,
                platform="pc",
            )
            diff_summary = diff_res.get("diff_summary", {}) if diff_res.get("success") else None
            self._update_state(diff_summary=diff_summary)

            # 3. 准备要下载的清单文件列表
            manifest_files_to_download = []
            for plat, p_info in plat_manifest_map.items():
                app_v = p_info["app_version"]
                b_code = p_info["version"]
                # assethash
                if p_info.get("assethash"):
                    manifest_files_to_download.append((plat, p_info["assethash"]))
                # voice_package_list
                v_list = f"voice_package_list_{app_v}_{b_code}.bytes"
                manifest_files_to_download.append((plat, v_list))
                # voice_hashes
                manifest_files_to_download.append((plat, f"voice_hash_zh_{app_v}00{b_code}.bytes"))
                manifest_files_to_download.append((plat, f"voice_hash_ja_{app_v}00{b_code}.bytes"))

            # 4. 下载所有清单文件并分别落盘到 static_resources 与专属数据目录
            self._update_state(
                status="downloading",
                total_files=len(manifest_files_to_download) + (len(diff_summary.get("modified_files", [])) if download_diff_assets and diff_summary else 0),
                total_bytes=(diff_summary.get("total_download_bytes", 0) if download_diff_assets and diff_summary else 0) + len(manifest_files_to_download) * 50000,
                message="正在下载各平台核心版本清单与语音索引...",
            )

            downloaded_files_count = 0
            downloaded_bytes_count = 0
            start_time = time.time()

            for plat, fn in manifest_files_to_download:
                if self._cancel_requested:
                    return
                self._update_state(current_file=f"[{plat.upper()}] {fn}")
                plat_dir = os.path.join(STATIC_RESOURCES_DIR, plat)
                os.makedirs(plat_dir, exist_ok=True)
                dest_static = os.path.join(plat_dir, fn)
                data_dir = cdn_proxy.get_data_dir(plat)
                dest_data = os.path.join(data_dir, fn)

                url_path = f"/{plat}/resources/{fn}"
                st, hdrs, body = cdn_proxy.fetch_upstream(OFFICIAL_CDN_HOST, url_path, timeout=15)
                if st == 200 and body:
                    with open(dest_static, "wb") as f:
                        f.write(body)
                    try:
                        shutil.copy2(dest_static, dest_data)
                    except Exception:
                        pass
                    downloaded_bytes_count += len(body)
                downloaded_files_count += 1
                elapsed = max(0.1, time.time() - start_time)
                speed = (downloaded_bytes_count / 1024.0) / elapsed
                self._update_state(
                    downloaded_files=downloaded_files_count,
                    downloaded_bytes=downloaded_bytes_count,
                    speed_kbps=round(speed, 1),
                )

            # 5. 若请求下载增量资产，下载 PC 端的修改与新增文件
            if download_diff_assets and diff_summary:
                modified_and_added = diff_summary.get("modified_files", []) + diff_summary.get("added_files", [])
                pc_data_dir = cdn_proxy.get_data_dir("pc")

                for item in modified_and_added:
                    if self._cancel_requested:
                        return
                    new_hash = item.get("new_hash")
                    new_sz = item.get("new_size", 0)
                    asset_path = item.get("path", "")
                    fn_on_cdn = f"{new_hash}.ys"
                    self._update_state(current_file=f"{asset_path} ({fn_on_cdn})")

                    dest_file = os.path.join(pc_data_dir, fn_on_cdn)
                    if os.path.isfile(dest_file) and os.path.getsize(dest_file) == new_sz and new_sz > 0:
                        downloaded_files_count += 1
                        downloaded_bytes_count += new_sz
                    else:
                        url_path = f"/pc/resources/{fn_on_cdn}"
                        st, hdrs, body = cdn_proxy.fetch_upstream(OFFICIAL_CDN_HOST, url_path, timeout=30)
                        if st == 200 and body:
                            with open(dest_file, "wb") as f:
                                f.write(body)
                            downloaded_bytes_count += len(body)
                        downloaded_files_count += 1

                    elapsed = max(0.1, time.time() - start_time)
                    speed = (downloaded_bytes_count / 1024.0) / elapsed
                    pct = min(99.0, round((downloaded_files_count / max(1, self._state["total_files"])) * 100, 1))
                    self._update_state(
                        downloaded_files=downloaded_files_count,
                        downloaded_bytes=downloaded_bytes_count,
                        speed_kbps=round(speed, 1),
                        progress_percent=pct,
                        message=f"正在同步增量资源 ({downloaded_files_count}/{self._state['total_files']})",
                    )

            # 6. 注册新版本到服务中
            self._update_state(status="registering", message="正在将新版本注册入版本管理中心...")
            b_int = int(target_build) if target_build.isdigit() else 311
            baseline = get_baseline_for_build(b_int)
            new_reg_cfg = {
                "version": target_build,
                "version_name": target_vname,
                "display_name": f"Build {target_build} ({target_vname or '官方更新'})",
                "matched_app_version": target_app_ver,
                "assethash": {
                    "pc": plat_manifest_map.get("pc", {}).get("assethash", f"assethash_{target_app_ver}_{target_build}.bytes"),
                    "android": plat_manifest_map.get("android", {}).get("assethash", f"assethash_{target_app_ver}_{target_build}.bytes"),
                    "ios": plat_manifest_map.get("ios", {}).get("assethash", f"assethash_{target_app_ver}_{target_build}.bytes"),
                },
                "voice_package_list": f"voice_package_list_{target_app_ver}_{target_build}.bytes",
                "voice_hashes": {
                    "ja": f"voice_hash_ja_{target_app_ver}00{target_build}.bytes",
                    "zh": f"voice_hash_zh_{target_app_ver}00{target_build}.bytes",
                },
                **baseline,
            }
            register_version(target_build, new_reg_cfg, persist=True)

            self._update_state(
                status="completed",
                progress_percent=100.0,
                message=f"版本 Build {target_build} ({target_vname}) 已成功同步并就绪，可随时切换！",
            )
        except Exception as e:
            self._update_state(status="error", error=f"同步更新异常中断: {e}", message="更新同步失败")


update_task_manager = UpdateTaskManager()


# ----------------- CLI 终端更新检查与执行入口 -----------------

def check_and_print_update(sync: bool = False, platform: str = "pc"):
    """供命令行使用的交互式/自省式版本更新检测与下载工具。"""
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
        if st in ("completed", "error"):
            print("\n")
            if st == "completed":
                print(f"[+] {prog['message']}")
            else:
                print(f"[-] {prog['error']}")
            break
        time.sleep(0.5)
    print("=" * 68)
