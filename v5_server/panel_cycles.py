"""Read and reset the selected save's current recurring gameplay progress."""
import json
import os
import time
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from account_db import savepoint_transaction
from boss_challenge_cycle import bounds as dream_bounds
from lazy_timer import get_daily_5am_ts, get_weekly_mon_5am_ts
from mythic_cycle import bounds as mythic_bounds
from task_cycle_notifications import reset_activity_points, reset_task_progress
import weekly_challenge_service as weekly

DAY = 86400
WEEK = 7 * DAY
KINDS = ('daily', 'weekly', 'seizure', 'dream', 'iteration', 'polyhedron', 'battlepass', 'mythic')
TASK_FILTERS = {
    5: 'task_type=5 OR task_id BETWEEN 5001 AND 5099',
    6: 'task_type=6 OR task_id BETWEEN 6001 AND 6099',
    7: 'task_type=7 OR task_id BETWEEN 71000 AND 71999',
    8: 'task_type=8 OR task_id BETWEEN 81000 AND 81999',
}


class CycleChanged(ValueError):
    pass


@lru_cache(maxsize=4)
def _config(name):
    return json.loads(Path(__file__).with_name(name).read_text('utf-8-sig'))


def _one(db, sql, params):
    return next(iter(db.query(sql, params)), {})


def _json(row, key):
    return json.loads(row.get(key) or '[]')


def _int(row, key):
    return int(row.get(key) or 0)


def _currency(db, uid, item_id):
    return _int(_one(db, 'SELECT num FROM currency WHERE uid=? AND id=?', (uid, item_id)), 'num')


def _row(key, name, progress, target=None, claimed=False):
    status = ('claimed' if claimed else 'progress' if target is None else
              'ready' if progress >= target else 'pending')
    return dict(id=str(key), label=name, current=progress, target=target, status=status)


def _tiers(prefix, thresholds, progress, claimed):
    return [_row(f'{prefix}-{tier}', f'{prefix} · {tier:,}', progress, tier, tier in claimed)
            for tier in thresholds]


def _bounds(kind, now):
    if kind == 'daily':
        start = get_daily_5am_ts(now)
        return start, start + DAY
    if kind in ('weekly', 'battlepass'):
        start = get_weekly_mon_5am_ts(now)
        return start, start + WEEK
    if kind in ('seizure', 'iteration'):
        end = weekly.get_next_thursday_5am(now)
        return end - WEEK, end
    if kind == 'dream':
        return dream_bounds(now)[:2]
    if kind == 'polyhedron':
        return weekly.get_polyhedron_cycle(now)
    return mythic_bounds(now)


def _task_rows(db, uid, task_type, now, title=''):
    rows = db.query(
        'SELECT c.task_id,c.name,c.desc,c.need,t.progress,t.complete_flag,t.claimed_ts,t.expired_ts '
        'FROM task_cfg c LEFT JOIN task t ON t.uid=? AND t.task_id=c.task_id '
        f"WHERE c.task_type=? ORDER BY c.task_id", (uid, task_type))
    result = []
    for row in rows:
        expires = _int(row, 'expired_ts')
        current = not expires or expires > now
        need = max(1, _int(row, 'need'))
        name = row['desc'] or row['name'] or f"任务 {row['task_id']}"
        result.append(_row(f"task-{row['task_id']}", title + name,
                           _int(row, 'progress') if current else 0, need,
                           current and bool(row['claimed_ts'] or row['complete_flag'])))
    return result


def _tasks(db, uid, now, kind, start):
    daily = kind == 'daily'
    activity_id = 1 if daily else 3
    rows = _task_rows(db, uid, 6 if daily else 5, now)
    row = _one(db, 'SELECT * FROM activity_pt WHERE uid=? AND activity_pt_id=?', (uid, activity_id))
    clock = 'last_daily_task_refresh_ts' if daily else 'last_weekly_task_refresh_ts'
    clock_row = _one(db, f'SELECT {clock} FROM game_user WHERE uid=?', (uid,))
    current = not clock_row.get(clock) or int(clock_row[clock]) >= start
    thresholds = sorted(map(int, _config('activity_pt_cfg.json')[str(activity_id)]['rewards']))
    return rows + _tiers('活跃宝箱', thresholds, _int(row, 'active_point') if current else 0,
                         _json(row, 'get_id_list') if current else [])


def _seizure(db, uid, now, start):
    progress = _one(db, 'SELECT * FROM equip_seizure_progress WHERE uid=?', (uid,))
    if 0 < _int(progress, 'refresh_ts') <= now:
        progress = {}
    today = _int(progress, 'today_max_score') if _int(progress, 'last_day_tag') == weekly.get_daily_5am(now) else 0
    total = _int(progress, 'sum_score')
    claimed = set(_json(progress, 'got_reward_id_list'))
    rows = [_row('today', '今日最高分', today), _row('total', '本期累计积分', total)]
    for key, reward in sorted(weekly.get_equip_seizure_catalog()['rewards'].items(),
                              key=lambda item: int(item[1]['need'])):
        target = int(reward['need'])
        rows.append(_row(f'reward-{key}', f'积分奖励 · {target:,}', total, target, int(key) in claimed))
    return rows


def _dream(db, uid, now, start):
    saved = _one(db, 'SELECT * FROM boss_challenge_progress WHERE uid=?', (uid,))
    current = bool(saved) and _int(saved, 'last_weekly_ts') >= start
    _, normal, advance = weekly.get_boss_challenge_data(
        uid, db if current else None, strict=True, now_ts=now, selection=saved)
    catalog = weekly.get_boss_catalog()
    stars = {}
    rows = []
    for boss in normal['boss_challenge_list']:
        for record in boss['star_info']:
            stars[int(record['stage_id'])] = record['star_list']
        count = sum(bit == 1 for record in boss['star_info'] for bit in record['star_list'])
        rows.append(_row(f"normal-{boss['group_id']}", f"常规首领 {boss['group_id']} · 星数", count))
    for boss in advance['boss_list']:
        rows.append(_row(f"advance-{boss['id']}", f"扭曲首领 {boss['id']} · 积分", int(boss['max_point'])))
    area = catalog.get('normal_areas', {}).get(str(normal['area_id']), {})
    mode = catalog.get('advance_modes', {}).get(str(advance['advance_id']), {})
    return (rows + _tiers('星级奖励', [int(x['star']) for x in area.get('rewards', [])],
                          sum(bit == 1 for group in stars.values() for bit in group), normal['receive_star_list'])
            + _tiers('积分奖励', [int(x['point']) for x in mode.get('rewards', [])],
                     sum(int(x['max_point']) for x in advance['boss_list']), advance['receive_point_list']))


def _iteration(db, uid, now, start):
    cycle = weekly.get_core_verification_cycle(now)
    cleared = {int(x['info_id']) for x in db.query(
        'SELECT info_id FROM core_verification_stage_record WHERE uid=? AND cycle=? AND sign=1', (uid, cycle))}
    boss_one, boss_two = weekly.get_core_verification_stages(cycle)
    best = {1: 0, 2: 0}
    rows = []
    for stage in boss_one + boss_two:
        boss, diff, stage_id = map(int, (stage['boss_type'], stage['difficult'], stage['id']))
        if stage_id in cleared:
            best[boss] = max(best[boss], diff)
        rows.append(_row(f'stage-{stage_id}', f'首领 {boss} · 难度 {diff}', int(stage_id in cleared), 1))
    claimed = {int(x['task_id']) for x in db.query(
        'SELECT task_id FROM core_verification_claimed_tasks WHERE uid=? AND cycle IN (0,?)', (uid, cycle))}
    for key, reward in sorted(weekly.get_core_verification_rewards_catalog().items(), key=lambda item: int(item[0])):
        key = int(key)
        reward_type = int(reward['reward_type'])
        if reward_type in (1, 2):
            # Regression reward IDs include the rotation number.
            if key // 10000 != cycle:
                continue
            name, progress, target = f'回归测试 {reward_type}', best[reward_type], key % 10
        elif reward_type == 3:
            name, progress, target = '难度奖励', min(best.values()), key - 1000
        elif reward_type == 4:
            name, target = '首通奖励', key % 10
            progress = {0: min(best.values()), 1: best[1], 2: best[2]}.get((key - 2000) // 10, 0)
        else:
            continue
        rows.append(_row(f'reward-{key}', f'{name} · 难度 {target}', progress, target, key in claimed))
    return rows


def _polyhedron(db, uid, now, start):
    last = _int(_one(db, 'SELECT MAX(policy_cycle_start) AS start FROM polyhedron_meta WHERE uid=?', (uid,)), 'start')
    current = not last or last >= start
    progress = _currency(db, uid, 45) if current else 0
    claimed = {int(x['level']) for x in db.query('SELECT level FROM polyhedron_policy_claimed WHERE uid=?', (uid,))} if current else set()
    return [_row(f'policy-{level}', f'多维历程 · {level} 级', progress, need, level in claimed)
            for level, need in enumerate(weekly.POLYHEDRON_POLICY_EXP, 1)]


def _battlepass(db, uid, now, start):
    from paid_entitlement_service import battlepass_list_id, project_battlepass_claims, read_state
    saved = _one(db, 'SELECT * FROM battlepass WHERE uid=?', (uid,))
    state = read_state(db, uid)
    current = int(state.get('bp_cycle') or start) >= start
    progress = _currency(db, uid, 14) if current else 0
    list_id = battlepass_list_id()
    received = project_battlepass_claims(_json(saved, 'receive_info'),
                                        _int(saved, 'battlepass_list_id') or list_id, list_id) if current else []
    claimed = {(int(x['id']), int(x['is_pay'])) for x in received}
    config = _config('battlepass_cfg.json')
    tiers = config['types'][str(config['lists'][str(list_id)]['battlepass_type'])]
    rows = _task_rows(db, uid, 7, now, '每日 · ') + _task_rows(db, uid, 8, now, '每周 · ')
    tracks = [('免费轨', 0, 'reward_free')]
    if _int(saved, 'pay_level'):
        tracks.append(('进阶轨', 1, 'reward_pay'))
    for level, tier in enumerate(tiers[:70], 1):
        for name, paid, field in tracks:
            if tier.get(field):
                rows.append(_row(f"tier-{tier['id']}-{paid}", f'{name} · {level} 级',
                                 progress, level * 1000, (tier['id'], paid) in claimed))
    return rows


def _mythic(db, uid, now, start):
    import mythic_normal_service as normal
    import mythic_final_service as final
    state = normal.load(db, uid, now_ts=now)
    difficulty = normal.difficulty(db, uid)
    partition_ids = [difficulty, *(difficulty * 100 + index for index in range(1, 4))]
    rows = [_row(f'partition-{part}', '常规 · 主分区' if not index else f'常规 · 子分区 {index}',
                 int(part in state['cleared']), 1) for index, part in enumerate(partition_ids)]
    rows.extend(_tiers('常规星级奖励', range(1, 4), len(state['stars'].get(str(difficulty), [])), state['claimed']))
    saved = _one(db, 'SELECT * FROM mythic_final_progress WHERE uid=?', (uid,))
    key = f'mythic.final_cycle_v1.{uid}'
    marker = _one(db, 'SELECT value FROM server_global_config WHERE key=?', (key,))
    current = int(marker.get('value') or mythic_bounds(_int(saved, 'update_ts'))[0]) >= start
    difficulty = _int(saved, 'now_difficulty') or 30
    count = int(final.definition(difficulty)['team_num'])
    teams = {int(r['team_id']): int(r['clear_state']) for r in _json(saved, 'challenge_info_json')} if current else {}
    rows.extend(_row(f'team-{index}', f'失序深区 · 队伍 {index}', teams.get(index, 0), 1)
                for index in range(1, count + 1))
    max_clear = max(_json(saved, 'clear_list'), default=0) if current else 0
    claimed = set(_json(saved, 'receive_reward_list')) if current else set()
    rows.extend(_row(f'final-{diff}', f'失序深区 · 热度奖励 {diff}', int(diff <= max_clear), 1, diff in claimed)
                for diff in range(1, 31))
    return rows


READERS = {
    'daily': lambda db, uid, now, start: _tasks(db, uid, now, 'daily', start),
    'weekly': lambda db, uid, now, start: _tasks(db, uid, now, 'weekly', start),
    'seizure': _seizure, 'dream': _dream, 'iteration': _iteration,
    'polyhedron': _polyhedron, 'battlepass': _battlepass, 'mythic': _mythic,
}


def _revision(db, uid, kind):
    key = f'panel.cycle_reset.{uid}.{kind}'
    row = _one(db, 'SELECT value FROM server_global_config WHERE key=?', (key,))
    return key, row.get('value', '')


def _token(db, uid, kind, start, now):
    rotation = weekly.get_core_verification_cycle(now) if kind == 'iteration' else 0
    path = os.path.normcase(os.path.realpath(db.path))
    identity = json.dumps((path, uid, kind, start, rotation, _revision(db, uid, kind)[1]))
    return sha256(identity.encode('utf-8')).hexdigest()


def card(db, uid, kind, now):
    start, end = _bounds(kind, now)
    return dict(kind=kind, reset_at=end, token=_token(db, uid, kind, start, now),
                rows=READERS[kind](db, uid, now, start))


def _reset_tasks(db, uid, task_type, expires, login_id=0):
    task_filter = TASK_FILTERS[task_type]
    reset_task_progress(db, uid, f'{task_filter} OR task_id IN (SELECT task_id FROM task_cfg WHERE {task_filter})',
                        expires, (login_id,) if login_id else ())
    db.execute(f"DELETE FROM claim_ledger WHERE uid=? AND kind='task' AND key_id IN "
               f"(SELECT task_id FROM task_cfg WHERE {task_filter})", (uid,))


def reset(db, uid, kind, token, now_ts=None):
    now = int(time.time()) if now_ts is None else int(now_ts)
    start, end = _bounds(kind, now)
    with savepoint_transaction(db):
        if token != _token(db, uid, kind, start, now):
            raise CycleChanged('周期或进度版本已变更，请刷新后再试')
        if kind in ('daily', 'weekly'):
            daily = kind == 'daily'
            _reset_tasks(db, uid, 6 if daily else 5, end, 6001 if daily else 0)
            reset_activity_points(db, uid, 1 if daily else 3, 22 if daily else 35)
        elif kind == 'battlepass':
            from paid_entitlement_service import clear_battlepass_progress
            _reset_tasks(db, uid, 7, get_daily_5am_ts(now) + DAY, 71001)
            _reset_tasks(db, uid, 8, end)
            clear_battlepass_progress(db, uid, now)
        elif kind == 'seizure':
            db.get_equip_seizure_progress(uid, now, reset_current=True)
        elif kind == 'dream':
            db.check_and_lazy_refresh_boss_challenge(uid, now, reset_current=True)
        elif kind == 'iteration':
            cycle = weekly.get_core_verification_cycle(now)
            db.reset_core_verification_challenge(uid, cycle, 0)
            db.execute('DELETE FROM core_verification_claimed_tasks WHERE uid=? AND cycle=?', (uid, cycle))
        elif kind == 'polyhedron':
            from polyhedron_service import PolyhedronRunManager
            activity = weekly.get_polyhedron_activity(now_ts=now)['activity_id']
            PolyhedronRunManager.check_and_refresh_policy_cycle(uid, db, activity_id=activity, force_cycle_id=start)
        elif kind == 'mythic':
            db.reset_mythic_by_beacon(uid)
        else:
            raise ValueError('未知玩法')
        key, previous = _revision(db, uid, kind)
        db.set_system_config(key, str(max(time.time_ns() if now_ts is None else now * 10**9, int(previous or 0) + 1)))
        return card(db, uid, kind, now)


def check_battle(db, uid, session, kind):
    """A reconnect can still present a battle ID created before a panel reset."""
    _, revision = _revision(db, uid, kind)
    if revision and float(session.get('ts') or 0) < int(revision) / 10**9:
        raise CycleChanged('本期进度已重置，请重新开始挑战')
