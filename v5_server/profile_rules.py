"""Shared yearless birthday validation and read projection."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def skin_portrait_config():
    return json.loads((Path(__file__).parent / 'data/skin_acquisition_cfg.json').read_text('utf-8'))


@lru_cache(maxsize=1)
def default_home_scene_ids():
    settings = json.loads(Path(__file__).with_name('new_player_cfg.json').read_text('utf-8'))['settings']
    result = tuple(dict.fromkeys(int(i) for i in settings['home_sence_default'] if int(i) > 0))
    if not result:
        raise ValueError('缺少默认大厅场景配置')
    return result


def project_home_scenes(rows, legacy_rows=(), skin_scene_ids=(), *, now_ts):
    """Account entitlements, including defaults, with saved expiry authoritative."""
    entries = {sid:dict(id=sid, lasted_time=0, obtain_time=0) for sid in default_home_scene_ids()}
    represented = set()
    for row in rows:
        sid = int(row['scene_id'])
        represented.add(sid)
        expires = int(row.get('lasted_time') or 0)
        if sid in entries:
            continue
        if sid > 0 and (expires == 0 or expires > now_ts):
            entries[sid] = dict(id=sid, lasted_time=expires, obtain_time=int(row.get('obtain_time') or 0))
    for row in legacy_rows:
        sid = int(row['scene_id'])
        if sid > 0 and sid not in represented:
            entries.setdefault(sid, dict(id=sid, lasted_time=0, obtain_time=int(row.get('obtain_time') or 0)))
    for sid in skin_scene_ids:
        sid = int(sid)
        if sid > 0 and sid not in represented:
            entries.setdefault(sid, dict(id=sid, lasted_time=0, obtain_time=0))
    return [entries[sid] for sid in sorted(entries)]


def project_like_history(value):
    """Only persisted account history can supply the profile's received likes."""
    if not isinstance(value, list):
        return []
    return [dict(id=row['id'], time=row['time']) for row in value
            if isinstance(row, dict) and type(row.get('id')) is int and row['id'] > 0
            and type(row.get('time')) is int and row['time'] >= 0]


MONTH_DAYS=(31,29,31,30,31,30,31,31,30,31,30,31)

def valid_birthday(month,day):
    return type(month) is int and type(day) is int and 1<=month<=12 and 1<=day<=MONTH_DAYS[month-1]

def read_birthday(db,uid):
    return birthday_from_rows(db.query('SELECT birth_month, birth_day FROM game_user WHERE uid=?',(uid,)))

def birthday_from_rows(rows):
    if not rows:return dict(month=0,day=0)
    if len(rows)!=1:raise ValueError('生日记录不唯一')
    month,day=rows[0].get('birth_month'),rows[0].get('birth_day')
    # A partial schema-default birthday has never represented a chosen date.
    if month in (None,0) or day in (None,0):return dict(month=0,day=0)
    if not valid_birthday(month,day):raise ValueError('历史生日格式无效，保留原存档')
    return dict(month=month,day=day)


PURE_MODE_LIMITS=dict(pattern_id=2,pattern_scope=2,switch_frequency=3,entry_time=3)
MODULE_DEFAULTS=(
    dict(module_id=1,module_location_x=1222,module_location_y=1084,module_size=6666),
    dict(module_id=2,module_location_x=9042,module_location_y=1738,module_size=6666),
    dict(module_id=3,module_location_x=8443,module_location_y=914,module_size=6666),
)
MODULE_SCALE_MIN=3333  # floor(0.5 / 1.5 * 10000), PureModeView.SaveComponentData.
MODULE_SCALE_MAX=10000


def validate_pure_mode(value):
    if not isinstance(value,dict) or any(type(value.get(k)) is not int or not 0<=value[k]<=maximum for k,maximum in PURE_MODE_LIMITS.items()):
        raise ValueError('纯白模式参数无效')
    return {k:value[k] for k in PURE_MODE_LIMITS}


def project_pure_mode(value):
    raw=value if isinstance(value,dict) else {}
    # The current Lua maps each optional legacy zero index to its first option.
    return {k:raw[k] if type(raw.get(k)) is int and 0<=raw[k]<=maximum else 0 for k,maximum in PURE_MODE_LIMITS.items()}


def _module(value):
    if not isinstance(value,dict) or type(value.get('module_id')) is not int or value['module_id'] not in (1,2,3):
        raise ValueError('桌面组件ID无效')
    for key in ('module_location_x','module_location_y'):
        if type(value.get(key)) is not int or not 0<=value[key]<=4294967295:
            raise ValueError('桌面组件坐标无效')
    if type(value.get('module_size')) is not int or not MODULE_SCALE_MIN<=value['module_size']<=MODULE_SCALE_MAX:
        raise ValueError('桌面组件尺寸无效')
    return {k:value[k] for k in ('module_id','module_location_x','module_location_y','module_size')}


def validate_modules(rows):
    if not isinstance(rows,list):raise ValueError('桌面组件必须是列表')
    if not rows:return []  # Current client defaults, not hidden components.
    if len(rows)!=3:raise ValueError('桌面组件必须上传完整快照')
    result=[_module(r) for r in rows]
    if {r['module_id'] for r in result}!={1,2,3}:raise ValueError('桌面组件ID重复')
    return result


def project_modules(rows):
    if rows is None or rows==[]:return []
    try:return validate_modules(rows)
    except ValueError:pass
    by_id={}
    for row in rows if isinstance(rows,list) else []:
        try:item=_module(row)
        except ValueError:continue
        by_id.setdefault(item['module_id'],item)
    return [dict(by_id.get(d['module_id'],d)) for d in MODULE_DEFAULTS]


def signature_value(user_row,extra):
    """A saved empty SQL signature is authoritative; NULL permits legacy fallback."""
    if user_row.get('sign') is not None:return str(user_row['sign'])
    value=extra.get('profile_sign')
    return '' if value is None else str(value)
