"""Persistent Sandplay quests for map 1110101; native p28 acknowledgement flow.

Only active blueprint tasks accept client parameters. Battle objectives are fed
by the authoritative battle settlement hook, never by cs_28832.
"""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import time

from core import Operation, operation
from hodur_service import require
from inventory_service import InventoryService

CFG = json.loads(Path(__file__).with_name('quanzhou_contract.json').read_text(encoding='utf-8'))
MAINS = {int(k): v for k, v in CFG['main_tasks'].items()}
TASKS = {int(k): v for k, v in CFG['tasks'].items()}
FUNCTIONS = {int(k): v for k, v in CFG['function_open'].items()}
KIND = 'quanzhou_functions_v1'
ACTIVITY = 323641


def frame(ctx, sc, data):
    from middleware import DownFrame
    raw = ctx.codec_encode('sc_' + str(sc), data)
    require(bool(raw) or (sc == 28837 and data == {'sys_id_list': []}), '箱庭响应编码失败')
    return DownFrame(sc, raw)


def read(db, uid):
    require(db.get('users', uid) is not None, '账号不存在')
    mains = {i: dict(id=i, status=0, expired_timestamp=0, task_list=[]) for i in MAINS}
    tasks = {i: dict(id=i, status=-100, progress=0) for i in TASKS}
    for row in db.query('SELECT main_quest_id,status FROM qworld_quest WHERE uid=?', (uid,)):
        if row['main_quest_id'] in mains:
            require(row['status'] in (0, 1, 2, 3, 9), '箱庭主任务记录损坏')
            mains[row['main_quest_id']]['status'] = row['status']
    for row in db.query('SELECT main_quest_id,task_id,status,progress FROM qworld_quest_task WHERE uid=?', (uid,)):
        i = row['task_id']
        if i not in tasks:
            continue
        require(row['main_quest_id'] == TASKS[i]['main_task_id'] and row['status'] in (-101, -100, 0, 1)
                and type(row['progress']) is int and row['progress'] >= 0, '箱庭子任务记录损坏')
        tasks[i].update(status=row['status'], progress=min(row['progress'], TASKS[i]['need']))
    return mains, tasks


def condition(db, uid, cid, mains, tasks):
    c = CFG['conditions'].get(str(cid))
    require(c is not None, '箱庭前置条件配置缺失')
    if c['type'] == 100:
        return all(mains.get(i, {}).get('status') == 9 for i in c['params'])
    if c['type'] == 101:
        return all(tasks.get(i, {}).get('status') == 1 for i in c['params'])
    if c['type'] == 1014:
        return int(db.get('users', uid).get('level') or 1) >= c['params'][0]
    return False


def conjunction(values, op):
    # Native config uses 2 for every nonempty multi-prerequisite group. The
    # sole op=1 task has no prerequisites; there is no ambiguous playable case.
    values = list(values)
    return not values or (all(values) if op == 2 else any(values))


def advance(db, uid, mains, tasks):
    for mid, cfg in MAINS.items():
        if mains[mid]['status'] in (0, 1):
            if conjunction((condition(db, uid, cid, mains, tasks) for cid in cfg['unlock_param'] or []), cfg['unlock_operation']):
                mains[mid]['status'] = 2 if cfg['receive_npc'] else 3
    for tid, cfg in TASKS.items():
        if tasks[tid]['status'] == -100 and mains[cfg['main_task_id']]['status'] == 3:
            if conjunction((tasks.get(i, {}).get('status') == 1 for i in cfg['pre_task_id']), cfg['pre_operation']):
                tasks[tid]['status'] = 0


def payload(mains, tasks):
    out = []
    for mid, m in sorted(mains.items()):
        if m['status'] == 0:
            continue
        # Wire status is uint32. Missing tasks map to Lua NOT_START=-100;
        # skipped optional tasks remain absent rather than wrapping negatives.
        out.append(dict(id=mid, status=m['status'], expired_timestamp=0,
                        task_list=[dict(t) for i, t in sorted(tasks.items())
                                   if TASKS[i]['main_task_id'] == mid and t['status'] >= 0]))
    return dict(quest_list=out)


def quest_payload(db, uid):
    mains, tasks = read(db, uid)
    advance(db, uid, mains, tasks)
    return payload(mains, tasks)


def function_payload(db, uid):
    rows = db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?', (uid, KIND, str(ACTIVITY)))
    values = json.loads(rows[0]['value_json']) if rows else []
    require(isinstance(values, list) and all(type(i) is int and i in FUNCTIONS for i in values), '箱庭功能记录损坏')
    return dict(sys_id_list=sorted(set(values)))


def persist(ctx, uid, before, mains, tasks):
    old_m, old_t = before
    now = int(time.time())
    for mid, m in mains.items():
        if m != old_m[mid]:
            ctx.db.execute('INSERT INTO qworld_quest(uid,main_quest_id,status,expired_ts,update_ts) VALUES(?,?,?,0,?) '
                           'ON CONFLICT(uid,main_quest_id) DO UPDATE SET status=excluded.status,expired_ts=0,update_ts=excluded.update_ts',
                           (uid, mid, m['status'], now))
    for tid, t in tasks.items():
        if t != old_t[tid]:
            ctx.db.execute('INSERT INTO qworld_quest_task(uid,main_quest_id,task_id,status,progress,update_ts) VALUES(?,?,?,?,?,?) '
                           'ON CONFLICT(uid,main_quest_id,task_id) DO UPDATE SET status=excluded.status,progress=excluded.progress,update_ts=excluded.update_ts',
                           (uid, TASKS[tid]['main_task_id'], tid, t['status'], t['progress'], now))
    previous = {m['id']: m for m in payload(old_m, old_t)['quest_list']}
    changes = [m for m in payload(mains, tasks)['quest_list'] if previous.get(m['id']) != m]
    if changes:
        ctx.append_frame(frame(ctx, 28803, dict(quest_list=changes)))


def identifiers(data, key, config):
    ids = data.get(key)
    require(isinstance(ids, list) and 0 < len(ids) <= len(config)
            and all(type(i) is int and i in config for i in ids) and len(set(ids)) == len(ids), '箱庭任务列表无效')
    return ids


def apply(ctx, uid, cmd, data):
    mains, tasks = read(ctx.db, uid)
    before = deepcopy((mains, tasks))
    advance(ctx.db, uid, mains, tasks)
    rewards = Counter()
    if cmd == 28832:
        param, value = data.get('param'), data.get('value')
        require(type(param) is int and type(value) is int and 0 <= value <= 0xffffffff, '箱庭参数无效')
        if param == 0:
            require(value == 1, '箱庭刷新参数无效')
        else:
            cfg = TASKS.get(param)
            require(cfg is not None and cfg['condition'] == 60, '此箱庭目标不接受客户端进度')
            t = tasks[param]
            require(t['status'] == 0 or (t['status'] == 1 and value <= t['progress']), '箱庭任务尚未激活')
            require(mains[cfg['main_task_id']]['status'] == 3 or t['status'] == 1, '箱庭主任务尚未激活')
            t['progress'] = min(cfg['need'], max(t['progress'], value))
    elif cmd == 28822:
        ids = identifiers(data, 'quest_task_id', TASKS)
        # Validate the entire original batch before mutating. A caller cannot
        # finish a predecessor and an otherwise inactive successor in one call.
        for tid in ids:
            t = tasks[tid]
            require(t['status'] == 1 or (t['status'] == 0 and mains[TASKS[tid]['main_task_id']]['status'] == 3
                    and t['progress'] >= TASKS[tid]['need']), '箱庭目标尚未完成')
        for tid in ids:
            tasks[tid]['status'] = 1
    elif cmd == 28820:
        ids = identifiers(data, 'quest_id_list', MAINS)
        for mid in ids:
            require(mains[mid]['status'] == 9 or (mains[mid]['status'] == 3
                    and tasks[MAINS[mid]['end_task_id']]['status'] == 1), '箱庭主任务尚未完成')
        for mid in ids:
            if mains[mid]['status'] == 9:
                continue
            mains[mid]['status'] = 9
            for iid, n in MAINS[mid]['reward']:
                rewards[iid] += n
            for tid, t in tasks.items():
                if TASKS[tid]['main_task_id'] == mid and t['status'] != 1:
                    t['status'] = -101
    elif cmd == 28830:
        mid = data.get('quest_id')
        require(type(mid) is int and mid in MAINS and mains[mid]['status'] in (2, 3, 9), '箱庭任务不可接取')
        if mains[mid]['status'] != 9:
            mains[mid]['status'] = 3
    elif cmd == 28834:
        sid = data.get('sys_id')
        require(type(sid) is int and sid in FUNCTIONS, '未知箱庭功能')
        require(condition(ctx.db, uid, FUNCTIONS[sid]['condition_id'], mains, tasks), '箱庭功能尚未解锁')
        values = function_payload(ctx.db, uid)['sys_id_list']
        if sid not in values:
            values.append(sid)
            ctx.db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
                           'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
                           (uid, KIND, str(ACTIVITY), json.dumps(sorted(values))))
    else:
        raise ValueError('Unsupported QWorld operation')
    advance(ctx.db, uid, mains, tasks)
    persist(ctx, uid, before, mains, tasks)
    if rewards:
        summary = InventoryService.grant_items(ctx, uid, sorted(rewards.items()), source='quanzhou_quest')
        actual = InventoryService.rewards_after_overflow(sorted(rewards.items()), summary)
    else:
        actual = []
    if cmd in (28820, 28822):
        from quanzhou_collection import publish_progress
        publish_progress(ctx, uid)
    result = dict(result=0)
    if cmd in (28820, 28822):
        result['reward_list'] = [dict(id=i, num=n) for i, n in actual if n > 0]
    return result


def battle_clear(ctx, uid, stage_id):
    """Call exactly once for a validated winning battle, inside its transaction."""
    mains, tasks = read(ctx.db, uid)
    before = deepcopy((mains, tasks))
    advance(ctx.db, uid, mains, tasks)
    for tid, cfg in TASKS.items():
        if cfg['condition'] in (330001, 330002) and stage_id in cfg['additional_parameter']:
            if tasks[tid]['status'] == 0 and mains[cfg['main_task_id']]['status'] == 3:
                tasks[tid]['progress'] = min(cfg['need'], tasks[tid]['progress'] + 1)
    persist(ctx, uid, before, mains, tasks)


class QuestOperation(Operation):
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        return apply(ctx, self.uid, self.cmd, data)

    def respond(self, result, data, ctx):
        from operations import _refresh_frames
        if self.no_reply:
            return []
        return _refresh_frames(ctx, self.uid, strict=True) + [frame(ctx, self.sc, result)]


@operation
class ClaimMainQuest(QuestOperation):
    cmd, sc = 28820, 28821


@operation
class CompleteQuest(QuestOperation):
    cmd, sc = 28822, 28823


@operation
class TakeMainQuest(QuestOperation):
    cmd, sc = 28830, 28831


@operation
class QuestParameter(QuestOperation):
    cmd, sc = 28832, 28833


@operation
class UnlockFunction(QuestOperation):
    cmd, sc, no_reply = 28834, None, True
    # Core's opt-in response preparation requires an ACK, which this native
    # fire-and-forget push does not have. apply prepares every emitted delta.
    prepare_response_in_transaction = False
