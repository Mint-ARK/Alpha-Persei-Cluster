"""Rogue victory achievements: durable evidence, strict deltas, no reward grant."""
import json
from pathlib import Path
import time

from account_db import savepoint_transaction

with Path(__file__).with_name('rogueteam_achievement_contract.json').open(encoding='utf-8') as handle:
    CONTRACT = json.load(handle)

TEMPLATE = 100001
KIND = 'rogueteam_achievement_v1'


def _json(value, expected):
    try:
        parsed = json.loads(value or ('{}' if expected is dict else '[]'))
    except (ValueError, TypeError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


def _number(value):
    return value if type(value) is int and value >= 0 else 0


def held_counts(state):
    """Each native ID is one held item; rank/quantity-like fields are not counts."""
    relics, programs = set(), set()
    subtypes = {}
    for field, kind, seen in (('other_item_list_json', 3, relics), ('treasure_list_json', 4, programs)):
        for item in _json(state.get(field), list):
            iid = item.get('id') if isinstance(item, dict) else item
            if type(iid) is not int or iid in seen:
                continue
            cfg = CONTRACT['items'].get(str(iid), {})
            if cfg.get('type') != kind:
                continue
            seen.add(iid)
            if kind == 4:
                key = str(cfg['sub_type'])
                subtypes[key] = subtypes.get(key, 0) + 1
    return dict(relics=len(relics), program_total=len(programs), programs=subtypes)


def record_win(db, uid, state):
    """Called only after the first successful 88306, using its pre-clear state."""
    meta = _json((state or {}).get('flow_json'), dict)
    if (not state or state.get('template_id') != TEMPLATE or not state.get('in_game')
            or state.get('ending_won') != 1 or state.get('pending_ending') not in (1, 2, 3, 6)
            or not meta.get('plot_played') or not meta.get('run_id') or meta.get('report') is not None):
        return
    counts = held_counts(state)
    old = db.get('ops_common', uid, 'AND kind=? AND item_id=?', (KIND, str(TEMPLATE))) or {}
    evidence = _json(old.get('value_json'), dict)
    # Independent maxima are sufficient: none of these native conditions
    # combines multiple held categories in one achievement.
    for key in ('relics', 'program_total'):
        evidence[key] = max(_number(evidence.get(key)), counts[key])
    previous = evidence.get('programs')
    previous = previous if isinstance(previous, dict) else {}
    evidence['programs'] = {key: _number(value) for key, value in previous.items()}
    for key, count in counts['programs'].items():
        evidence['programs'][key] = max(evidence['programs'].get(key, 0), count)
    db.upsert('ops_common', uid, dict(kind=KIND, item_id=str(TEMPLATE),
        value_json=json.dumps(evidence)), keys=('uid', 'kind', 'item_id'))


def _history(values):
    result = {}
    for item in values:
        if not isinstance(item, dict) or type(item.get('key')) is not int:
            continue
        count = _number(item.get('value'))
        result[item['key']] = max(result.get(item['key'], 0), count)
    return result


def _targets(db, uid):
    history = db.get('rogueteam_history', uid, 'AND template_id=?', (TEMPLATE,)) or {}
    difficulties = _history(_json(history.get('diff_clear_json'), list))
    endings = _history(_json(history.get('ending_pass_json'), list))
    stored = db.get('ops_common', uid, 'AND kind=? AND item_id=?', (KIND, str(TEMPLATE))) or {}
    held = _json(stored.get('value_json'), dict)
    subtypes = held.get('programs')
    subtypes = subtypes if isinstance(subtypes, dict) else {}
    for cfg in CONTRACT['achievements'].values():
        condition, params = cfg['condition'], cfg['parameter']
        if not params or params[0] != TEMPLATE:
            continue
        if condition == 300005:
            passed = difficulties.get(params[1], 0) > 0
        elif condition == 300006:
            passed = endings.get(params[1], 0) > 0
        elif condition == 300013:
            passed = _number(held.get('relics')) >= params[1]
        elif condition == 300014:
            count = held.get('program_total') if params[1] == 0 else subtypes.get(str(params[1]))
            passed = _number(count) >= params[2]
        else:
            continue
        if passed:
            yield cfg
    from rogueteam_tactics import achievement_targets
    yield from achievement_targets(db, uid)
    from rogueteam_battle_facts import achievement_targets as battle_targets
    yield from battle_targets(db, uid)


def reconcile(db, uid, ctx=None):
    """Project only proven wins. Online returns one required 53003 frame or []."""
    import achievement_service as achievements
    from middleware import DownFrame
    achievements._ensure_cfg()
    frames = []
    with savepoint_transaction(db):
        changed = []
        now = int(time.time())
        for native in _targets(db, uid):
            aid, need = native['id'], native['need']
            cfg = achievements._CFG.get(aid) or {}
            db.execute('INSERT OR IGNORE INTO achievement '
                '(uid,achievement_id,name,condition,desc,need,reward_json) VALUES(?,?,?,?,?,?,?)',
                (uid, aid, cfg.get('name', ''), native['condition'], cfg.get('desc', ''),
                 need, json.dumps(cfg.get('reward') or [])))
            row = db.get('achievement', uid, 'AND achievement_id=?', (aid,))
            if row.get('complete_flag') == 1:
                continue
            progress = max(int(row.get('progress') or 0), native.get('progress', need))
            stamp = int(row.get('achieve_time') or 0)
            updated_stamp = (now if stamp in (0, 1786411503) else stamp) if progress >= need else 0
            if progress == row.get('progress') and stamp == updated_stamp and row.get('need') == need:
                continue
            stamp = updated_stamp
            cursor = db.execute('UPDATE achievement SET progress=?,need=?,achieve_time=? '
                'WHERE uid=? AND achievement_id=? AND complete_flag=0', (progress, need, stamp, uid, aid))
            if cursor.rowcount:
                changed.append(dict(id=aid, progress=progress, complete_flag=0, achieve_time=stamp))
        if changed and ctx is not None:
            raw = ctx.codec_encode('sc_53003', dict(achievement_progress=changed))
            if not isinstance(raw, (bytes, bytearray, memoryview)) or not raw:
                raise ValueError('推演成就必要差量53003编码失败')
            frames.append(DownFrame(53003, bytes(raw)))
    return frames
