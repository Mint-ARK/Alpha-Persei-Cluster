"""Permanent Hella pinball progression and its native battle-stage bridge."""
from contextlib import contextmanager
import json
from pathlib import Path
import time

from account_db import savepoint_transaction
from core import OperationError
from event_bus import bus, Events
from operation_notifications import checkpoint_notifications, restore_notifications

CFG = json.loads(Path(__file__).with_name('pinball_contract.json').read_text('utf-8'))
ACTIVITY = CFG['activity_id']
STAGES = {int(i): row for i, row in CFG['stages'].items()}
SKILLS = {int(i): row for i, row in CFG['skills'].items()}
NORMAL = {i for i, row in STAGES.items() if row['barrier_type'] != 3}
BATTLES = {row['barrier_id']: i for i, row in STAGES.items() if row['barrier_type'] == 2}


def require(ok, message):
    if not ok:
        raise OperationError(2, message)


@contextmanager
def atomic(ctx):
    checkpoint = checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            yield
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise


def _list(raw):
    values = json.loads(raw or '[]') if isinstance(raw, str) else raw
    require(isinstance(values, list), '弹珠存档列表格式错误')
    return values


def read(db, uid, activity=ACTIVITY):
    require(type(activity) is int and activity == ACTIVITY, '未知弹珠活动')
    rows = db.query('SELECT * FROM pinball_progress WHERE uid=? AND activity_id=?', (uid, activity))
    row = rows[0] if rows else {}
    cleared = sorted({i for i in _list(row.get('cleared_stage_list', [])) if type(i) is int and i in NORMAL})
    unlocked = {i for i in _list(row.get('unlock_skill_list', []))
                if type(i) is int and i in SKILLS and SKILLS[i]['skill_type'] in (1, 2)}
    unlocked.update(STAGES[i]['skill_unlock'] for i in cleared if STAGES[i]['skill_unlock'])
    equipped = []
    for i in _list(row.get('equip_skill_list', [])):
        if type(i) is int and i in unlocked and SKILLS[i]['skill_type'] == 1 and i not in equipped:
            equipped.append(i)
    level = max([1, int(row.get('stage_count') or 1)] + [STAGES[i]['lv_up'] for i in cleared])
    return dict(uid=uid, activity_id=activity, stage_count=min(level, max(s['lv_up'] for s in STAGES.values())),
                cleared_stage_list=cleared, unlock_skill_list=sorted(unlocked),
                equip_skill_list=equipped[:CFG['active_slots']], score=max(0, min(0xFFFFFFFF, int(row.get('score') or 0))))


def payload(db, uid):
    data = read(db, uid)
    return dict(hai_la_level=data['stage_count'], stage_list=data['cleared_stage_list'],
                skills=data['unlock_skill_list'], select_active_skills=data['equip_skill_list'], challenge_score=data['score'])


def save(db, data):
    db.execute('''INSERT INTO pinball_progress(uid,activity_id,stage_count,cleared_stage_list,unlock_skill_list,equip_skill_list,score,update_ts)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(uid,activity_id) DO UPDATE SET
        stage_count=excluded.stage_count,cleared_stage_list=excluded.cleared_stage_list,
        unlock_skill_list=excluded.unlock_skill_list,equip_skill_list=excluded.equip_skill_list,
        score=excluded.score,update_ts=excluded.update_ts''',
        (data['uid'], ACTIVITY, data['stage_count'], json.dumps(data['cleared_stage_list']),
         json.dumps(data['unlock_skill_list']), json.dumps(data['equip_skill_list']), data['score'], int(time.time())))


def check_stage(data, stage):
    require(type(stage) is int and stage in STAGES, '未知弹珠关卡')
    row = STAGES[stage]
    require(not row['pre_stage'] or row['pre_stage'] in data['cleared_stage_list'], '弹珠前置关卡尚未完成')
    if row['barrier_type'] == 3:
        require(NORMAL <= set(data['cleared_stage_list']), '弹珠挑战需完成全部普通关卡')
    return row


def battle_entry(db, uid, dest, activity):
    require(type(dest) is int and dest in BATTLES and activity == ACTIVITY, '弹珠战斗与活动不匹配')
    check_stage(read(db, uid), BATTLES[dest])
    return BATTLES[dest]


def settle(ctx, uid, stage, score=0, harm=0, *, from_battle=False):
    require(type(score) is int and 0 <= score <= 0xFFFFFFFF and type(harm) is int and 0 <= harm <= 0xFFFFFFFF,
            '弹珠结算数值不合法')
    with atomic(ctx):
        data = read(ctx.db, uid)
        row = check_stage(data, stage)
        require((row['barrier_type'] == 2) == bool(from_battle), '弹珠关卡结算入口不匹配')
        old = json.dumps(data, sort_keys=True)
        if row['barrier_type'] != 3:
            if stage not in data['cleared_stage_list']:
                data['cleared_stage_list'].append(stage); data['cleared_stage_list'].sort()
                if row['skill_unlock'] not in data['unlock_skill_list']:
                    data['unlock_skill_list'].append(row['skill_unlock']); data['unlock_skill_list'].sort()
                data['stage_count'] = max(data['stage_count'], row['lv_up'])
        else:
            data['score'] = max(data['score'], score)
        changed = old != json.dumps(data, sort_keys=True)
        if changed:
            save(ctx.db, data)
        if (row['barrier_type'] == 1 and harm) or (row['barrier_type'] == 3 and score):
            statistics = read_statistics(ctx.db, uid)
            if row['barrier_type'] == 1:
                statistics['story_harm'] += harm
            else:
                statistics['challenge_points'] += score
            ctx.db.execute('INSERT INTO server_global_config(key,value) VALUES(?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (statistics_key(uid), json.dumps(statistics)))
        bus.emit_required(Events.PINBALL_CLEAR, ctx, uid)
        return dict(data, changed=changed)


def equip(ctx, uid, activity, skills):
    require(type(activity) is int and activity == ACTIVITY, '未知弹珠活动')
    require(isinstance(skills, list) and len(skills) <= CFG['active_slots'], '弹珠最多装备两个主动技能')
    require(all(type(i) is int for i in skills) and len(set(skills)) == len(skills), '弹珠技能重复或格式不合法')
    with atomic(ctx):
        data = read(ctx.db, uid)
        require(all(i in data['unlock_skill_list'] and SKILLS[i]['skill_type'] == 1 for i in skills), '弹珠技能未解锁或不是主动技能')
        if data['equip_skill_list'] != skills:
            data['equip_skill_list'] = list(skills)
            save(ctx.db, data)
        return data


def statistics_key(uid):
    return 'pinball.statistics_v1.' + str(uid)


def read_statistics(db, uid):
    rows = db.query('SELECT value FROM server_global_config WHERE key=?', (statistics_key(uid),))
    if rows:
        data = json.loads(rows[0]['value'])
        require(isinstance(data, dict) and all(type(data.get(k)) is int and data[k] >= 0
                for k in ('story_harm', 'challenge_points')), '弹珠累计统计格式错误')
        return data
    # Old capped task progress proves only a lower bound. Adopt the highest
    # existing value once, never sum several tiers of the same counter.
    values = {int(r['condition']): max(0, int(r['progress'] or 0)) for r in db.query('''
        SELECT c.condition,MAX(t.progress) AS progress FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND c.condition IN (392002,392004) GROUP BY c.condition''', (uid,))}
    return dict(version=1, story_harm=values.get(392002, 0), challenge_points=values.get(392004, 0))


def project_tasks(db, uid):
    data = read(db, uid)
    cleared = set(data['cleared_stage_list'])
    statistics = read_statistics(db, uid)
    rows = db.query('''SELECT t.task_id,t.progress,c.need,c.additional_parameter
        ,c.condition FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND c.condition BETWEEN 392001 AND 392004
          AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
          AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
          AND NOT EXISTS(SELECT 1 FROM claim_ledger cl WHERE cl.uid=t.uid AND cl.key_id=t.task_id
                         AND cl.kind IN ('task','illustrated_task'))''', (uid, int(time.time())))
    changes = []
    for row in rows:
        try:
            stages = json.loads(row['additional_parameter']) if isinstance(row['additional_parameter'], str) else row['additional_parameter']
        except (ValueError, TypeError):
            continue
        if not isinstance(stages, list) or any(type(i) is not int for i in stages):
            continue
        condition = int(row['condition']); need = int(row['need'] or 0)
        if need <= 0:
            continue
        if condition == 392001 and need == 1 and len(stages) == 1:
            progress = int(stages[0] in cleared)
        elif condition == 392002 and stages == [1]:
            progress = statistics['story_harm']
        elif condition == 392004 and stages == [3]:
            progress = statistics['challenge_points']
        elif condition == 392003 and need == 1 and len(stages) == 2 and stages[0] == 3 and stages[1] > 0:
            progress = int(data['score'] >= stages[1])
        else:
            continue
        progress = min(need, progress)
        if progress > int(row['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?', (progress, uid, row['task_id']))
            changes.append(dict(id=int(row['task_id']), progress=progress, complete_flag=0))
    return changes


def notify_tasks(ctx, uid):
    changes = project_tasks(ctx.db, uid)
    if changes:
        from codec import encode
        from middleware import DownFrame
        payload = (ctx.codec_encode or encode)('sc_28007', dict(progress_list=changes))
        if not isinstance(payload, (bytes, bytearray, memoryview)) or not payload:
            raise ValueError('弹珠任务差量帧为空')
        ctx.append_frame(DownFrame(28007, bytes(payload)))


def restore_for_login(db, uid):
    with savepoint_transaction(db):
        return project_tasks(db, uid)


def finish_battle(ctx, uid, dest, activity):
    from middleware import DownFrame
    with atomic(ctx):
        stage = battle_entry(ctx.db, uid, dest, activity)
        data = settle(ctx, uid, stage, from_battle=True)
        if data['changed']:
            encoded = ctx.codec_encode('sc_89401', payload(ctx.db, uid))
            if not isinstance(encoded, (bytes, bytearray, memoryview)) or not encoded:
                raise ValueError('弹珠战斗进度帧为空')
            return [DownFrame(89401, bytes(encoded))]
    return []
