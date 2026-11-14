"""常规黑区：精确条件星、半周资格及同轮跨难度共享领奖。"""
import json
import time

from mythic_cycle import bounds, check_session, MythicPeriodExpired

KIND = 'mythic_normal_v1'
OMEGA_DIFFICULTY = 1001


def is_normal(stage_type, stage):
    stage = int(stage or 0)
    return int(stage_type or 0) == 11 or 3026001 <= stage <= 3026013 or 3027001 <= stage <= 3027039


def partition(stage):
    stage = int(stage)
    if 3026001 <= stage <= 3026013:
        return stage - 3026000, stage - 3026000, True
    if 3027001 <= stage <= 3027039:
        index = stage - 3027001
        difficulty = index // 3 + 1
        return difficulty, difficulty * 100 + index % 3 + 1, False
    raise ValueError('常规黑区关卡不存在')


def selected_difficulty(db, uid):
    """Read the shared black-zone selection, including the native Ω entry."""
    rows = db.query('SELECT current_difficulty FROM mythic_public WHERE uid=?', (int(uid),))
    if rows:
        result = int(rows[0]['current_difficulty'] or 0)
    else:
        from new_player_service import enabled
        result = 1 if enabled(db, uid) else 10
    if result not in range(1, 14) and result != OMEGA_DIFFICULTY:
        raise ValueError('常规黑区难度无效')
    return result


def difficulty(db, uid):
    result = selected_difficulty(db, uid)
    if result == OMEGA_DIFFICULTY:
        raise ValueError('当前选中失序深区，请先切换到常规黑区难度')
    return result


def _empty(cycle, generation=0):
    return dict(cycle=int(cycle), generation=int(generation), stars={}, cleared=[], claimed=[])


def load(db, uid, now_ts=None):
    cycle = bounds(now_ts)[0]
    rows = db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?', (int(uid), KIND, 'state'))
    if rows:
        state = json.loads(rows[0]['value_json'])
        if int(state['cycle']) < cycle:
            return _empty(cycle, state['generation'])
        return state
    state = _empty(cycle)
    # Old integer star counts cannot identify non-contiguous native conditions.
    # Preserve only current-period clear/claim facts; replay establishes exact stars.
    state['cleared'] = sorted({int(r['partition_id']) for r in db.query(
        'SELECT partition_id FROM mythic_progress_partition WHERE uid=? AND update_ts>=?', (int(uid), cycle))})
    state['claimed'] = sorted({int(r['reward_id']) for r in db.query(
        'SELECT reward_id FROM mythic_progress_reward WHERE uid=? AND update_ts>=?', (int(uid), cycle)) if int(r['reward_id']) in (1, 2, 3)})
    return state


def save(db, uid, state):
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
               'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
               (int(uid), KIND, 'state', json.dumps(state, separators=(',', ':'))))


def reset(db, uid):
    state = load(db, uid)
    save(db, uid, _empty(bounds()[0], int(state['generation']) + 1))


def payload(db, uid):
    if db is None:
        return dict(clear_partition_id_list=[], main_partition_star_list=[], star_reward_provide_list=[])
    state = load(db, uid)
    selected = selected_difficulty(db, uid)
    return dict(clear_partition_id_list=state['cleared'],
                main_partition_star_list=state['stars'].get(str(selected), []),
                star_reward_provide_list=state['claimed'])


def entry(db, uid, stage):
    selected = difficulty(db, uid)
    expected, pid, main = partition(stage)
    if expected != selected:
        raise ValueError('常规黑区关卡与当前难度不符')
    state = load(db, uid)
    return dict(cycle_start=bounds()[0], difficulty=selected, generation=state['generation'],
                stage_id=int(stage), partition_id=pid, main=main)


def validate(db, uid, session):
    check_session(session, uid)
    snapshot = session.get('mythic_normal_context')
    try:
        if not snapshot or snapshot != entry(db, uid, session['dest']):
            raise ValueError('常规黑区挑战已切换、跨期或被信标重置')
    except (ValueError, KeyError, TypeError) as exc:
        raise MythicPeriodExpired(str(exc)) from exc
    return snapshot


def evaluate(stage, win, report):
    _, _, main = partition(stage)
    if not main:
        return [], []  # Native subpartitions have no three_star_need.
    from middleware import _load_three_star_cfg
    conditions = _load_three_star_cfg().get(str(stage))
    if not isinstance(conditions, list) or len(conditions) != 3:
        raise ValueError('常规黑区星条件配置缺失')
    ids, counts = report.get('item_ids'), report.get('item_numbers')
    valid_items = (isinstance(ids, list) and isinstance(counts, list) and len(ids) == len(counts)
                   and all(type(v) is int and 0 < v <= 0x7fffffff for v in ids)
                   and all(type(v) is int and 0 <= v <= 0x7fffffff for v in counts))
    items = {}
    if valid_items:
        for iid, count in zip(ids, counts):
            items[iid] = items.get(iid, 0) + count
    stars, rows = [], []
    for index, condition in enumerate(conditions, 1):
        kind = int(condition[0])
        need, progress = 1, 0
        if kind == 15:
            need = int(condition[1]); progress = items.get(int(condition[2]), 0)
            achieved = valid_items and progress >= need
        elif kind == 8:
            progress = int(bool(win)); achieved = bool(win)
        elif kind == 4:
            elapsed = report.get('battle_time')
            achieved = bool(win) and type(elapsed) is int and 0 <= elapsed <= int(condition[1]) * 1000
            progress = int(achieved)
        else:
            raise ValueError('常规黑区未实现的星条件')
        stars.append(int(achieved))
        rows.append(dict(star_id=index, now_progress=progress, need_progress=need, is_achieve=int(achieved)))
    return stars, rows


def record(db, uid, identity, achieved, win):
    # Called inside settlement savepoint; never derive IDs from the number of stars.
    if identity != entry(db, uid, identity['stage_id']):
        raise MythicPeriodExpired('常规黑区结算身份过期')
    state = load(db, uid)
    pid = identity['partition_id']
    if identity['main']:
        key = str(identity['difficulty'])
        state['stars'][key] = sorted(set(state['stars'].get(key, [])) |
                                     {i for i, value in enumerate(achieved, 1) if value == 1})
    if win:
        state['cleared'] = sorted(set(state['cleared']) | {pid})
        # Retain historical clears for the existing fresh-save difficulty unlock policy.
        db.record_mythic_partition_clear(uid, pid, star=len(state['stars'].get(str(identity['difficulty']), [])) if identity['main'] else 0)
    save(db, uid, state)


def claim(db, uid, star):
    from core import OperationError
    import mythic_affix_cfg
    if db is None:
        raise OperationError(2, '常规黑区存档不可用')
    selected = difficulty(db, uid)
    state = load(db, uid)
    if star not in (1, 2, 3):
        raise OperationError(2, '常规黑区星奖档位不存在')
    if star in state['claimed']:
        raise OperationError(2013, '常规黑区星奖已领取')
    if len(state['stars'].get(str(selected), [])) < star:
        raise OperationError(2005, '常规黑区星数不足')
    template = mythic_affix_cfg.MYTHIC_NORMAL_STAR_REWARDS[selected]
    items = [dict(id=int(row['id']), num=int(row['num'])) for row in template]
    if not items or any(row['id'] <= 0 or row['num'] <= 0 for row in items):
        raise ValueError('常规黑区奖励配置无效')
    state['claimed'] = sorted(state['claimed'] + [star])
    save(db, uid, state)
    db.execute('INSERT INTO mythic_progress_reward(uid,reward_id,update_ts) VALUES(?,?,?) '
               'ON CONFLICT(uid,reward_id) DO UPDATE SET update_ts=excluded.update_ts', (int(uid), star, int(time.time())))
    return items
