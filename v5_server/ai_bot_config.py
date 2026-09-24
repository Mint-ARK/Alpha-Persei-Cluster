# -*- coding: utf-8 -*-
"""
ai_bot_config.py — AI 虚拟好友 Bot 配置与 API 密钥安全加载器

安全规范:
- 密钥与凭据严禁在 public JSON 或代码中明文硬编码；
- 优先从系统环境变量与 Git 忽略的 v5_server/.env 读取；
- 对外输出一律提供脱敏掩码 (mask_api_key)，绝不泄露明文密钥。
"""
import os
import json
import re
import shutil
import tempfile

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_bot_config.json")
LOCAL_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ROOT_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def mask_api_key(key: str) -> str:
    """对 API 密钥进行安全脱敏掩码处理。"""
    if not key or not isinstance(key, str):
        return ""
    key = key.strip()
    if key.lower() == "ollama":
        return "ollama"
    if len(key) <= 8:
        return "***"
    if key.startswith("sk-") and len(key) > 12:
        return f"{key[:7]}***{key[-4:]}"
    return f"{key[:3]}***{key[-3:]}"


def is_masked_key(key: str) -> bool:
    """判断传入的密钥字符串是否为脱敏后的掩码形式。"""
    if not key or not isinstance(key, str):
        return False
    return "***" in key


def _load_env_file(filepath):
    """安全解析 .env 文件内容。"""
    env_vars = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return env_vars


def get_api_key(key_name: str) -> str:
    """按优先级依次加载 API 密钥：
    1. 系统环境变量
    2. v5_server/.env (受 .gitignore 保护)
    3. 项目根目录 .env (受 .gitignore 保护)
    4. %APPDATA%/reasonix/.env
    """
    if not key_name:
        return ""

    # 1. 系统环境变量
    val = os.environ.get(key_name)
    if val:
        return val.strip()

    # 2. v5_server/.env
    env_map = _load_env_file(LOCAL_ENV_FILE)
    if key_name in env_map and env_map[key_name]:
        return env_map[key_name].strip()

    # 3. 项目根目录 .env
    env_map_root = _load_env_file(ROOT_ENV_FILE)
    if key_name in env_map_root and env_map_root[key_name]:
        return env_map_root[key_name].strip()

    # 4. APPDATA/reasonix/.env
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        reasonix_env = os.path.join(appdata, "reasonix", ".env")
        env_map_app = _load_env_file(reasonix_env)
        if key_name in env_map_app and env_map_app[key_name]:
            return env_map_app[key_name].strip()

    return ""


def set_env_variable(key_name: str, key_val: str, filepath: str = None) -> bool:
    """安全地将敏感环境变量写入或更新至受保护的 .env 文件。"""
    if not key_name:
        return False
    filepath = filepath or LOCAL_ENV_FILE
    try:
        lines = []
        found = False
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()

        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                if k.strip() == key_name:
                    new_lines.append(f"{key_name}={key_val}\n")
                    found = True
                    continue
            new_lines.append(line)

        if not found:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f"{key_name}={key_val}\n")

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if os.path.exists(filepath):
            shutil.copy2(filepath, filepath + ".bak")
        fd, tmp_path = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=os.path.dirname(filepath))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        finally:
            # 失败时保留临时文件，遵守项目假删除原则并便于诊断恢复。
            pass
        return True
    except Exception:
        return False


def get_default_config() -> dict:
    """返回默认基准配置模板（绝不包含任何硬编码明文密钥）。"""
    return {
        "provider": "opencode",
        "temperature": 0.7,
        "max_tokens": 1000,
        "max_history_turns": 10,
        "timeout_seconds": 30,
        "system_prefix": "你正在与《深空之眼》的管理员进行游戏内私聊。请保持游戏沉浸感，严禁输出任何代码块、markdown 标题或 AI 身份声明，直接像真正的游戏角色一样回复简短自然的对话（1~3句话为佳）。\n\n",
        "providers": {
            "opencode": {
                "type": "openai",
                "base_url": "https://opencode.ai/zen/go/v1",
                "model": "deepseek-v4-flash",
                "api_key_env": "OPENCODE_GO_API_KEY",
                "api_key": ""
            },
            "deepseek": {
                "type": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-flash",
                "api_key_env": "DEEPSEEK_API_KEY",
                "api_key": ""
            },
            "gemini": {
                "type": "gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3.8-flash",
                "api_key_env": "GEMINI_API_KEY",
                "api_key": ""
            },
            "openai": {
                "type": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5.6",
                "api_key_env": "OPENAI_API_KEY",
                "api_key": ""
            },
            "anthropic": {
                "type": "anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "model": "claude-sonnet-5",
                "api_key_env": "ANTHROPIC_API_KEY",
                "api_key": ""
            },
            "ollama": {
                "type": "openai",
                "base_url": "http://localhost:11434/v1",
                "model": "llama3.3:70b",
                "api_key": "ollama"
            }
        }
    }


def get_bot_config() -> dict:
    """加载当前配置，并解析真实 API 密钥注入内存。"""
    config = get_default_config()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                if isinstance(user_cfg, dict):
                    # 深度合并 providers
                    if "providers" in user_cfg and isinstance(user_cfg["providers"], dict):
                        for p_name, p_data in user_cfg["providers"].items():
                            if p_name in config["providers"]:
                                config["providers"][p_name].update(p_data)
                            else:
                                config["providers"][p_name] = p_data
                    # 合并顶层参数
                    for k in ["provider", "temperature", "max_tokens", "max_history_turns", "timeout_seconds", "system_prefix"]:
                        if k in user_cfg:
                            config[k] = user_cfg[k]
        except Exception:
            pass

    # 解析所有 provider 的 API Key
    for p_name, p_info in config.get("providers", {}).items():
        if p_name == "ollama":
            p_info["api_key"] = "ollama"
            continue

        raw_key = p_info.get("api_key", "")
        # 如果配置文件里没有或者为空，则从环境变量加载
        if not raw_key:
            env_key = p_info.get("api_key_env", "")
            if env_key:
                p_info["api_key"] = get_api_key(env_key)

    return config


def get_masked_config(cfg=None) -> dict:
    """对外（GM API / 前端控制台）返回安全脱敏后的配置字典，绝不含真实明文。"""
    if cfg is None:
        cfg = get_bot_config()

    masked = {
        "provider": cfg.get("provider", "opencode"),
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 1000),
        "max_history_turns": cfg.get("max_history_turns", 10),
        "timeout_seconds": cfg.get("timeout_seconds", 30),
        "system_prefix": cfg.get("system_prefix", ""),
        "providers": {}
    }

    for p_name, p_info in cfg.get("providers", {}).items():
        real_key = p_info.get("api_key", "")
        has_key = bool(real_key)
        m_key = mask_api_key(real_key)
        masked["providers"][p_name] = {
            "type": p_info.get("type", "openai"),
            "base_url": p_info.get("base_url", ""),
            "model": p_info.get("model", ""),
            "api_key_env": p_info.get("api_key_env", ""),
            "has_key": has_key,
            "masked_key": m_key,
            "api_key": m_key,
        }

    # V2 表单使用当前 Provider 的扁平字段；同时保留 providers 以兼容高级配置。
    selected = masked["providers"].get(masked["provider"], {})
    masked["type"] = selected.get("type", "openai")
    masked["base_url"] = selected.get("base_url", "")
    masked["model"] = selected.get("model", "")
    masked["has_api_key"] = selected.get("has_key", False)
    masked["api_key_masked"] = selected.get("masked_key", "")
    return masked


def save_bot_config(new_cfg: dict) -> bool:
    """安全保存 Bot 配置：
    - 若传入的 API Key 为掩码（含 ***）或为空，则保留原真实密钥；
    - 若传入了新的真实密钥，安全保存至本地 .env 文件，且 JSON 中仅存储 api_key: "" 与 api_key_env；
    - 更新 provider, model, temperature, max_tokens, system_prefix 等参数。
    """
    if not isinstance(new_cfg, dict):
        return False

    current_cfg = get_bot_config()

    # 1. 更新顶层通用字段
    if "provider" in new_cfg:
        if not isinstance(new_cfg["provider"], str) or not new_cfg["provider"].strip():
            return False
        current_cfg["provider"] = new_cfg["provider"].strip()
    if "temperature" in new_cfg:
        try:
            current_cfg["temperature"] = max(0.0, min(2.0, float(new_cfg["temperature"])))
        except (ValueError, TypeError):
            return False
    if "max_tokens" in new_cfg:
        try:
            current_cfg["max_tokens"] = max(100, min(8192, int(new_cfg["max_tokens"])))
        except (ValueError, TypeError):
            return False
    if "max_history_turns" in new_cfg:
        try:
            current_cfg["max_history_turns"] = max(1, min(50, int(new_cfg["max_history_turns"])))
        except (ValueError, TypeError):
            return False
    if "timeout_seconds" in new_cfg:
        try:
            current_cfg["timeout_seconds"] = max(1, min(120, int(new_cfg["timeout_seconds"])))
        except (ValueError, TypeError):
            return False
    if "system_prefix" in new_cfg and isinstance(new_cfg["system_prefix"], str):
        current_cfg["system_prefix"] = new_cfg["system_prefix"]

    # 2. 更新 providers 列表
    new_providers = new_cfg.get("providers", {})
    # 兼容 V2 当前表单的扁平提交格式。
    selected_provider = current_cfg.get("provider", "opencode")
    flat_provider = {}
    for key in ("type", "base_url", "model", "api_key"):
        if key in new_cfg:
            flat_provider[key] = new_cfg[key]
    if flat_provider:
        new_providers = dict(new_providers) if isinstance(new_providers, dict) else {}
        new_providers[selected_provider] = {
            **new_providers.get(selected_provider, {}),
            **flat_provider,
        }
    if isinstance(new_providers, dict):
        for p_name, p_data in new_providers.items():
            if not isinstance(p_data, dict):
                continue
            if p_name not in current_cfg["providers"]:
                current_cfg["providers"][p_name] = {
                    "type": "openai",
                    "base_url": "",
                    "model": "",
                    "api_key_env": f"{p_name.upper()}_API_KEY",
                    "api_key": ""
                }
            cur_p = current_cfg["providers"][p_name]

            if "base_url" in p_data:
                if not isinstance(p_data["base_url"], str) or not re.match(r"^https?://", p_data["base_url"].strip(), re.I):
                    return False
                cur_p["base_url"] = p_data["base_url"].strip()
            if "model" in p_data:
                if not isinstance(p_data["model"], str) or not p_data["model"].strip():
                    return False
                cur_p["model"] = p_data["model"].strip()
            if "type" in p_data:
                if not isinstance(p_data["type"], str) or p_data["type"].strip() not in ("openai", "anthropic", "gemini_native", "gemini"):
                    return False
                cur_p["type"] = p_data["type"].strip()

            # 处理密钥
            raw_key = p_data.get("api_key", "")
            if raw_key is None:
                raw_key = ""
            if not isinstance(raw_key, str):
                return False
            new_key = raw_key.strip()
            env_var_name = cur_p.get("api_key_env") or f"{p_name.upper()}_API_KEY"
            cur_p["api_key_env"] = env_var_name

            if new_key and not is_masked_key(new_key):
                # 传入了新的真实密钥：写入 .env 文件
                if not set_env_variable(env_var_name, new_key):
                    return False
                cur_p["api_key"] = new_key

    # 3. 构造干净的 JSON 持久化对象（绝不含明文 API Key）
    json_to_save = {
        "provider": current_cfg["provider"],
        "temperature": current_cfg["temperature"],
        "max_tokens": current_cfg["max_tokens"],
        "max_history_turns": current_cfg["max_history_turns"],
        "timeout_seconds": current_cfg.get("timeout_seconds", 30),
        "system_prefix": current_cfg["system_prefix"],
        "providers": {}
    }

    for p_name, p_info in current_cfg["providers"].items():
        json_to_save["providers"][p_name] = {
            "type": p_info.get("type", "openai"),
            "base_url": p_info.get("base_url", ""),
            "model": p_info.get("model", ""),
            "api_key_env": p_info.get("api_key_env", f"{p_name.upper()}_API_KEY"),
            "api_key": ""  # 强制落盘为空，安全通过 .env 解耦
        }

    try:
        if os.path.exists(CONFIG_FILE):
            shutil.copy2(CONFIG_FILE, CONFIG_FILE + ".bak")
        fd, tmp_path = tempfile.mkstemp(prefix="ai_bot_config.", suffix=".tmp", dir=os.path.dirname(CONFIG_FILE))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                json.dump(json_to_save, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CONFIG_FILE)
        finally:
            # 失败时保留临时文件，遵守项目假删除原则并便于诊断恢复。
            pass
        return True
    except Exception:
        return False
