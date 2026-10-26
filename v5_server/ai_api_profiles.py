"""Per-save API profiles. Credentials live only in the owning SQLite save."""
from copy import deepcopy
import json
import math
import re
import uuid

CONFIG_KEY = 'ai.api_profiles.v1'


def load(db):
    if not hasattr(db, 'query'):
        return None
    rows = db.query('SELECT value FROM server_global_config WHERE key=?', (CONFIG_KEY,))
    if not rows:
        return None
    data = json.loads(rows[0]['value'])
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('存档API配置格式无效')
    return data


def _runtime(profile):
    from ai_bot_config import get_default_config
    cfg = get_default_config()
    # Never inherit another save's credentials or environment once DB-managed.
    for value in cfg['providers'].values():
        value['api_key'] = ''
    cfg.update({key: profile[key] for key in
                ('provider', 'temperature', 'max_tokens', 'max_history_turns', 'timeout_seconds', 'system_prefix')})
    cfg['providers'][profile['provider']] = {key: profile[key] for key in ('type', 'base_url', 'model', 'api_key')}
    cfg['api_profile_id'] = profile['id']
    return cfg


def resolve(db, char_id=0, *, profile_id=None):
    store = load(db)
    if (store is None or not store['profiles']) and not profile_id:
        from ai_bot_config import get_default_config
        cfg = get_default_config()
        for value in cfg['providers'].values():
            value['api_key'] = ''
        cfg['api_profile_id'] = ''
        return cfg
    if profile_id is None:
        character = None
        if char_id and hasattr(db, 'get_ai_character'):
            character = db.get_ai_character(char_id) or db.get_ai_character_by_hero_id(char_id)
        key = str(character['char_id'] if character else char_id)
        profile_id = store['routes'].get(key) or store['active_id']
    if store is None or profile_id not in store['profiles']:
        raise ValueError('指定API配置不存在')
    return _runtime(store['profiles'][profile_id])


def view(db):
    from ai_bot_config import get_masked_config
    store = load(db)
    if store is None:
        return dict(storage='database', active_id='', profiles=[], routes={})
    profiles = []
    for profile in store['profiles'].values():
        masked = get_masked_config(_runtime(profile))
        profiles.append(dict(id=profile['id'], name=profile['name'], **masked))
    return dict(storage='database', active_id=store['active_id'], profiles=profiles,
                routes=deepcopy(store['routes']))


def _normalize(data, previous=None):
    from ai_bot_config import get_default_config, is_masked_key
    defaults = get_default_config()
    provider = data.get('provider', (previous or {}).get('provider', defaults['provider']))
    if not isinstance(provider, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', provider):
        raise ValueError('API厂商标识无效')
    preset = defaults['providers'].get(provider, dict(type='openai', base_url='', model='', api_key=''))
    if previous and previous['provider'] == provider:
        preset = {**preset, **previous}
    profile = dict(id=(previous or {}).get('id', uuid.uuid4().hex),
                   name=data.get('name', (previous or {}).get('name', '默认API')), provider=provider)
    for field in ('type', 'base_url', 'model'):
        profile[field] = data.get(field, preset.get(field, ''))
        if not isinstance(profile[field], str):
            raise ValueError(field + '须为文本')
        profile[field] = profile[field].strip()
    if profile['type'] not in ('openai', 'anthropic', 'gemini_native', 'gemini'):
        raise ValueError('不支持的API协议类型')
    from urllib.parse import urlsplit
    endpoint = urlsplit(profile['base_url'])
    if endpoint.scheme not in ('http', 'https') or not endpoint.hostname or endpoint.username or endpoint.password:
        raise ValueError('API地址须为不含账号密码的http/https地址')
    if not profile['model'] or len(profile['model']) > 160 or any(c in profile['model'] for c in '\r\n\t'):
        raise ValueError('模型调用名须为1—160字符的单行文本')
    if not isinstance(profile['name'], str) or not 1 <= len(profile['name'].strip()) <= 64:
        raise ValueError('API名称须为1—64字符')
    profile['name'] = profile['name'].strip()
    for field, low, high, integer in (('temperature', 0, 2, False), ('max_tokens', 100, 8192, True),
                                    ('max_history_turns', 1, 50, True), ('timeout_seconds', 1, 120, True)):
        val = data.get(field, (previous or {}).get(field, defaults[field]))
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not low <= val <= high or (integer and int(val) != val):
            raise ValueError(field + '超出允许范围')
        profile[field] = int(val) if integer else val
    profile['system_prefix'] = data.get('system_prefix', (previous or {}).get('system_prefix', defaults['system_prefix']))
    if not isinstance(profile['system_prefix'], str) or len(profile['system_prefix']) > 20000:
        raise ValueError('系统提示词须为不超过20000字符的文本')
    key = data.get('api_key', '')
    if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in '\r\n'):
        raise ValueError('API密钥格式无效')
    old_key = (previous or {}).get('api_key', '') if (previous or {}).get('provider') == provider else ''
    profile['api_key'] = key.strip() if key.strip() and not is_masked_key(key) else old_key
    if data.get('clear_key') is True:
        profile['api_key'] = ''
    if provider == 'ollama':
        profile['api_key'] = 'ollama'
    return profile


def update(db, data):
    """One atomic edit; malformed selections never change stored credentials."""
    if not isinstance(data, dict):
        raise ValueError('请求体必须为JSON对象')
    with db.transaction():
        store = load(db) or dict(version=1, active_id='', profiles={}, routes={})
        action = data.get('action', 'save')
        profile_id = data.get('id') or None
        if profile_id is not None and profile_id not in store['profiles']:
            raise ValueError('指定API配置不存在')
        if action == 'save':
            if not profile_id and len(store['profiles']) >= 32:
                raise ValueError('每个存档最多保存32组API')
            profile = _normalize(data, store['profiles'].get(profile_id))
            store['profiles'][profile['id']] = profile
            if not store['active_id']:
                store['active_id'] = profile['id']
        elif action == 'activate':
            if not profile_id:
                raise ValueError('请选择API配置')
            store['active_id'] = profile_id
        elif action == 'remove':
            if not profile_id:
                raise ValueError('请选择API配置')
            if profile_id == store['active_id'] or profile_id in store['routes'].values():
                raise ValueError('请先切换默认API或角色绑定，再移除此配置')
            del store['profiles'][profile_id]
        elif action == 'route':
            cid = data.get('char_id')
            character = db.get_ai_character(int(cid)) if cid else None
            if not character:
                raise ValueError('未知角色')
            if profile_id:
                store['routes'][str(character['char_id'])] = profile_id
            else:
                store['routes'].pop(str(character['char_id']), None)
        else:
            raise ValueError('未知API配置操作')
        db.set_system_config(CONFIG_KEY, json.dumps(store, ensure_ascii=False))
    return view(db)
