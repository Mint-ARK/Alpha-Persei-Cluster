"""AI configuration templates and masking; live credentials belong to one save.

No JSON, .env, process environment or other application's credentials are read.
"""
import re

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
                "api_key": ""
            },
            "deepseek": {
                "type": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-flash",
                "api_key": ""
            },
            "gemini": {
                "type": "gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3.8-flash",
                "api_key": ""
            },
            "openai": {
                "type": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5.6",
                "api_key": ""
            },
            "anthropic": {
                "type": "anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "model": "claude-sonnet-5",
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

def get_bot_config(db=None):
    """Compatibility accessor. A missing database returns an unconfigured template."""
    if db is not None:
        from ai_api_profiles import resolve
        return resolve(db)
    cfg = get_default_config()
    for provider in cfg['providers'].values():
        provider['api_key'] = ''
    cfg['api_profile_id'] = ''
    return cfg


def get_api_key(key_name):
    """Retired environment-key accessor; intentionally returns no credentials."""
    return ''


def set_env_variable(key_name, key_val, filepath=None):
    """Retired writer. Callers must save an API profile to an explicit database."""
    return False


def validate_model_route(route, providers=None):
    """A save stores provider/model selection only, never credentials/endpoints."""
    if not isinstance(route, dict) or set(route) != {'provider', 'model'}:
        raise ValueError('角色模型配置只能包含provider和model')
    provider = route['provider']
    model = route['model']
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError('角色模型provider不能为空')
    provider = provider.strip()
    providers = providers if providers is not None else get_default_config().get('providers', {})
    if provider not in providers:
        raise ValueError('角色模型provider未在服务端配置')
    if not isinstance(model, str) or not model.strip() or len(model) > 160 or any(c in model for c in '\r\n\t'):
        raise ValueError('角色模型model必须为1至160字符的单行调用名')
    return {'provider': provider, 'model': model.strip()}

def resolve_character_config(db, uid, char_id, cfg=None):
    """The current save's role binding/default is the only credential authority."""
    from ai_api_profiles import resolve
    return resolve(db, char_id)


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

def save_bot_config(new_cfg, db=None):
    """Compatibility form adapter; never writes a global file or environment."""
    if db is None or not isinstance(new_cfg, dict):
        return False
    from ai_api_profiles import load, update
    try:
        store = load(db)
        current = get_bot_config(db)
        payload = dict(new_cfg)
        provider = payload.get('provider') or current['provider']
        providers = payload.pop('providers', {})
        if isinstance(providers, dict):
            payload = {**providers.get(provider, {}), **payload}
        payload['provider'] = provider
        if store and store.get('active_id') and not payload.get('id'):
            payload['id'] = store['active_id']
        update(db, payload)
        return True
    except (ValueError, TypeError, KeyError):
        return False
