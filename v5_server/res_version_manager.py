# -*- coding: utf-8 -*-
"""
res_version_manager.py — 客户端资源版本与协议自适应状态管理器

职责：
1. 统一管理服务端当前生效的客户端资源分发版本（229 或 311）；
2. 提供线程安全的当前版本读写接口，支持启动参数注入与运行时 GM 控制面板热切换；
3. 维护各版本在 PC、iOS、Android 各平台的 AssetHash 校验清单、语音版本包及元数据；
4. 为业务响应调度（generator / operations）提供版本感知与自适应规则。
"""
import copy
import os
import re
import threading
import time

SUPPORTED_VERSIONS = ("229", "311")
DEFAULT_VERSION = "229"
# 2047-05-04 05:00:00 +08:00。活动时间统一在协议输出层延长，
# 不因切换版本去改写玩家数据库。
FAR_FUTURE_TIMESTAMP = 2440962000

import json

# 默认官方客户端 StreamingAssets 目录与环境变量配置
def _find_default_client_streaming_assets_dir() -> str:
    env_dir = os.environ.get("CLIENT_STREAMING_ASSETS_PATH", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return env_dir

    # 尝试从 server_config.json 读取客户端安装目录
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")
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

VERSION_CONFIGS = {
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
        "theme_id": 43,
        "battlepass_season": 32,
        "entry_activity_id": 4310001,
        "sign_activity_id": 4300101,
        "battlepass_list_id": 20032,
        "heart_demon_activity_id": 4335001,
        "core_verification_activity_id": 4343501,
        "polyhedron_activity_id": 4307301,
        "deprecated_shops": [],
        "description": "成熟稳定的 v5.2.1 兼容基线，涵盖 43 项常驻活动、商城与验证逻辑。",
    },
    "311": {
        "version": "311",
        "version_name": "v5.2.2",
        "display_name": "Build 311 (v5.2.2 兼容版本)",
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
        "theme_id": 44,
        "battlepass_season": 33,
        "entry_activity_id": 4410001,
        "sign_activity_id": 4400101,
        "battlepass_list_id": 20033,
        "heart_demon_activity_id": 4435001,
        "core_verification_activity_id": 4443501,
        # 311_CORE_VERIFICATION_GUARD:
        # Build 311 的首期 Mode 4 只开第 1 轮。其余轮次仍下发远期时间，但 state=0，
        # 避免客户端 templateCache_[433] 被第 4 轮覆盖，与 sc_89801 的 4443501 数据错位。
        "core_verification_active_activity_ids": [4443301, 4443401, 4443501],
        "polyhedron_activity_id": 4407301,
        "deprecated_shops": [16, 18, 24, 25, 34, 35, 36, 37, 42],
        "description": "v5.2.2 / v5.3.0 兼容环境，支持 33 赛季战令与 Theme 44 演进。",
    },
}

_CURRENT_VERSION = DEFAULT_VERSION
_LOCK = threading.Lock()

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
    """获取最近一次对客户端 StreamingAssets 目录进行版本探测的元数据副本。"""
    with _LOCK:
        return copy.deepcopy(_DETECTED_CLIENT_INFO)


def detect_client_resource_version(custom_dir: str = None) -> dict:
    """
    扫描目标客户端目录下的核心特征版本校验文件（形如 assethash_{app_ver}_{res_ver}_{md5}.bytes）。
    若存在多个历史文件，按修改时间倒序以最新修改的文件为准提取版本号。

    返回 dict 格式：
    {
        "detected": bool,
        "version": str or None,
        "matched_file": str or None,
        "file_mtime": float,
        "file_mtime_str": str,
        "client_dir": str,
        "detail": str,
    }
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

    matched_entries = []
    try:
        for fname in os.listdir(target_dir):
            m = ASSETHASH_PATTERN.match(fname)
            if m:
                app_ver, res_ver, hash_str = m.group(1), m.group(2), m.group(3)
                full_path = os.path.join(target_dir, fname)
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
        result["detail"] = f"在客户端目录 [{target_dir}] 下未找到合规的 assethash_*_*_*.bytes 索引文件"
        with _LOCK:
            _DETECTED_CLIENT_INFO = copy.deepcopy(result)
        return result

    # 按修改时间倒序排列，以最新修改时间生效的文件为当前权威版本
    matched_entries.sort(key=lambda x: x["mtime"], reverse=True)
    latest_entry = matched_entries[0]
    extracted_ver = str(latest_entry["res_ver"]).strip()
    mtime_val = latest_entry["mtime"]
    mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime_val)) if mtime_val > 0 else ""

    result["matched_file"] = latest_entry["filename"]
    result["file_mtime"] = mtime_val
    result["file_mtime_str"] = mtime_str

    if extracted_ver in SUPPORTED_VERSIONS:
        result["detected"] = True
        result["version"] = extracted_ver
        cfg = VERSION_CONFIGS.get(extracted_ver, {})
        result["detail"] = (
            f"成功探测并识别到客户端生效版本 Build {extracted_ver} ({cfg.get('display_name', '')}), "
            f"特征文件: {latest_entry['filename']} (修改时间: {mtime_str})"
        )
    else:
        result["detected"] = False
        result["version"] = extracted_ver
        result["detail"] = (
            f"探测到客户端特征文件提取出版本 Build {extracted_ver}，但服务端暂未支持该版本 "
            f"(目前支持: {list(SUPPORTED_VERSIONS)})"
        )

    with _LOCK:
        _DETECTED_CLIENT_INFO = copy.deepcopy(result)
    return result


def apply_auto_client_version(custom_dir: str = None, fallback_version: str = DEFAULT_VERSION) -> tuple[str, dict]:
    """
    自动检测客户端资源版本，并在支持列表中时自动应用为当前版本；
    若检测未命中或不支持，平滑降级至指定的 fallback_version。
    返回: (applied_version: str, detect_info: dict)
    """
    info = detect_client_resource_version(custom_dir=custom_dir)
    if info.get("detected") and info.get("version") in SUPPORTED_VERSIONS:
        target_ver = info["version"]
        set_current_version(target_ver)
        return target_ver, info
    else:
        target_ver = fallback_version if fallback_version in SUPPORTED_VERSIONS else DEFAULT_VERSION
        set_current_version(target_ver)
        return target_ver, info


def get_current_version() -> str:
    """获取当前生效的客户端资源版本（'229' 或 '311'）。"""
    with _LOCK:
        return _CURRENT_VERSION


_VERSION_CHANGE_LISTENERS = []


def register_version_change_listener(fn):
    """注册版本变更监听器，回调签名: fn(old_ver: str, new_ver: str)"""
    with _LOCK:
        if fn not in _VERSION_CHANGE_LISTENERS:
            _VERSION_CHANGE_LISTENERS.append(fn)


def unregister_version_change_listener(fn):
    """注销版本变更监听器"""
    with _LOCK:
        if fn in _VERSION_CHANGE_LISTENERS:
            _VERSION_CHANGE_LISTENERS.remove(fn)


def _notify_version_changed(old_ver: str, new_ver: str):
    """向所有订阅者广播客户端资源版本变更事件。"""
    with _LOCK:
        listeners = list(_VERSION_CHANGE_LISTENERS)
    for fn in listeners:
        try:
            fn(old_ver, new_ver)
        except Exception:
            pass


def set_current_version(ver: str) -> tuple[bool, str]:
    """
    设置当前客户端资源分发版本（线程安全）。
    支持传入 'auto' 触发自动探测客户端并自适应对齐。
    返回: (success: bool, msg: str)
    """
    global _CURRENT_VERSION
    if ver is None:
        return False, "版本参数不能为空"
    ver_str = str(ver).strip()
    if ver_str.lower() == "auto":
        applied_ver, detect_info = apply_auto_client_version()
        if detect_info.get("detected"):
            return True, f"自动对齐客户端资源版本成功: {applied_ver} ({detect_info.get('detail')})"
        else:
            return True, f"自动对齐未命中客户端 ({detect_info.get('detail')})，平滑降级为: {applied_ver}"
    if ver_str not in SUPPORTED_VERSIONS:
        return False, f"不支持的版本 [{ver_str}]，目前支持版本: {list(SUPPORTED_VERSIONS)}"
    old_ver = _CURRENT_VERSION
    with _LOCK:
        _CURRENT_VERSION = ver_str
    if old_ver != ver_str:
        _notify_version_changed(old_ver, ver_str)
    cfg = get_version_config(ver_str)
    return True, f"成功切换客户端资源版本为: {ver_str} ({cfg.get('display_name', '')})"


def get_version_config(ver: str = None) -> dict:
    """获取指定版本（缺省为当前版本）的完整配置元数据副本。"""
    if ver is None:
        ver = get_current_version()
    cfg = VERSION_CONFIGS.get(str(ver), VERSION_CONFIGS[DEFAULT_VERSION])
    return copy.deepcopy(cfg)


def get_all_version_configs() -> list[dict]:
    """获取所有支持版本的配置清单，并标注当前激活状态。"""
    curr = get_current_version()
    configs = []
    for k in SUPPORTED_VERSIONS:
        c = copy.deepcopy(VERSION_CONFIGS[k])
        c["is_current"] = (k == curr)
        configs.append(c)
    return configs


def get_assethash(platform: str = "pc", ver: str = None) -> str:
    """根据指定客户端平台（pc/ios/android）及资源版本，获取对应的 AssetHash 文件名。"""
    cfg = get_version_config(ver)
    plat = str(platform).lower() if platform else "pc"
    return cfg["assethash"].get(plat, cfg["assethash"]["pc"])


def get_voice_package_list(ver: str = None) -> str:
    """获取指定版本的语音包列表文件名。"""
    cfg = get_version_config(ver)
    return cfg["voice_package_list"]
