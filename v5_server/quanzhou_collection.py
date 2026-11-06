"""Citong native voyage completion, crew unlocks and photograph projection.

Old quanzhou_sail_progress rows were imported snapshots, so this module keeps
new confirmed voyage receipts in a separate durable record. Photograph unlocks
come from QWorld subquests; there is no photograph claim RPC in the client.
"""
from collections import Counter
import json
from pathlib import Path
import time

from account_db import savepoint_transaction
from core import OperationError
from inventory_service import InventoryService
from middleware import DownFrame
from operation_notifications import checkpoint_notifications, restore_notifications

CFG = json.loads(Path(__file__).with_name('quanzhou_contract.json').read_text('utf-8'))
ACTIVITY = 323641
CHILD_ACTIVITY = 323841
KIND = 'quanzhou_collection_v1'
SEA_STAGES = {int(i): v for i, v in CFG['sea_stages'].items()}
SEA_SKILLS = {int(i): v for i, v in CFG['sea_skills'].items()}
PHOTOS = {int(i): v for i, v in CFG['photo'].items()}
MAIN_STORY = set(range(11101, 11110))
SIDE_FINISH = {11201, 11202, 11203, 11206, 11207, 11208, 11209}
EXPLORATION = {int(i) for i, row in CFG['main_tasks'].items() if row['main_task_type'] == 3}
LEVEL_TASKS = {int(i): row for i, row in CFG['level_tasks'].items()}


def require(ok, message):
    if not ok:
        raise OperationError(2, message)


def activity_policy(activities, version=None):
    """Keep the resident entry and its native reward/task children reachable."""
    from activity_content_service import catalog
    from res_version_manager import FAR_FUTURE_TIMESTAMP
    native = catalog(version)['activities']
    result = {int(row['activity_id']): dict(row) for row in activities}
    for aid in (ACTIVITY, CHILD_ACTIVITY, 3200411):
        row = native[str(aid)]
        result[aid] = dict(activity_id=aid, start_time=1577836800, stop_time=FAR_FUTURE_TIMESTAMP,
                           state=1, theme=row['activity_theme'], template=row['activity_template'],
                           sub_activity_id_list=list(row['sub_activity_list']))
    return list(result.values())


def ensure_tasks(db, uid):
    """Native recommendation tasks exist at zero too; never revive claimed ones."""
    require(db.get('users', uid) is not None, '账号不存在')
    inserted = 0
    for tid, cfg in LEVEL_TASKS.items():
        db.execute('''INSERT OR IGNORE INTO task_cfg
            (task_id,name,task_type,condition,desc,need,activity_id,pre_id,reward_json,additional_parameter)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', (tid, cfg['name'], cfg['type'], cfg['condition'],
            cfg['desc'], cfg['need'], cfg['activity_id'], cfg['pre_id'],
            json.dumps(cfg['reward']), json.dumps(cfg['additional_parameter'])))
        result = db.execute('''INSERT INTO task(uid,task_id,task_type,progress,complete_flag,claimed_ts)
            SELECT ?,?,?,0,0,0 WHERE NOT EXISTS(SELECT 1 FROM claim_ledger WHERE uid=? AND key_id=?
            AND kind IN ('task','illustrated_task')) ON CONFLICT(uid,task_id) DO NOTHING''',
            (uid, tid, cfg['type'], uid, tid))
        inserted += max(0, result.rowcount)
    return inserted


def read(db, uid):
    require(db.get('users', uid) is not None, '账号不存在')
    rows = db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',
                    (uid, KIND, str(ACTIVITY)))
    state = json.loads(rows[0]['value_json']) if rows else dict(version=1, completed=[], skills=[])
    require(isinstance(state, dict) and state.get('version') == 1, '刺桐航行存档版本无效')
    for key, allowed in (('completed', SEA_STAGES), ('skills', SEA_SKILLS)):
        values = state.get(key)
        require(isinstance(values, list) and all(type(i) is int and i in allowed for i in values)
                and len(set(values)) == len(values), '刺桐航行存档损坏')
    return state


def save(db, uid, state):
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
               'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
               (uid, KIND, str(ACTIVITY), json.dumps(state)))


def finished_subquests(db, uid):
    return {int(row['task_id']) for row in db.query(
        'SELECT task_id FROM qworld_quest_task WHERE uid=? AND status=1', (uid,))}


def photograph_ids(db, uid):
    finished = finished_subquests(db, uid)
    return sorted(i for i, row in PHOTOS.items() if row['task_id'] in finished)


def unlocked_skills(db, uid):
    finished = finished_subquests(db, uid)
    return sorted(i for i, row in SEA_SKILLS.items() if row['task_id'] in finished)


def sea_payload(db, uid):
    state = read(db, uid)
    return dict(data=dict(affix=unlocked_skills(db, uid), stage_id_list=sorted(state['completed'])))


def frame(ctx, sc, data):
    payload = ctx.codec_encode('sc_' + str(sc), data)
    if not payload:
        raise ValueError('刺桐状态帧编码失败: ' + str(sc))
    return DownFrame(sc, payload)


def level_facts(db, uid):
    """Approved local rule: story=3, completed side/exploration=1, displayed cap10.

    The native help says story and exploration raise knowledge; it supplies no
    numeric award rule. These generous weights were explicitly selected by the
    user, and are not presented as reconstruction of the official server.
    """
    finished = {int(row['main_quest_id']) for row in db.query(
        'SELECT main_quest_id FROM qworld_quest WHERE uid=? AND status=9', (uid,))}
    points = len(finished & MAIN_STORY) * 3 + len(finished & SIDE_FINISH) + len(finished & EXPLORATION)
    return dict(points=points, level=min(10, points))


def sync_level(ctx_or_db, uid, *, notify=True):
    db = getattr(ctx_or_db, 'db', ctx_or_db)
    wanted = level_facts(db, uid)['level']
    current = InventoryService.get_item_balance(db, uid, 77)[1]
    if wanted > current:
        InventoryService.grant_items(ctx_or_db, uid, [(77, wanted-current)],
                                     silent=not notify, source='quanzhou_knowledge')
    return max(wanted, current)


def project_tasks(db, uid):
    ensure_tasks(db, uid)
    level = InventoryService.get_item_balance(db, uid, 77)[1]
    changed = []
    for tid, cfg in LEVEL_TASKS.items():
        rows = db.query('''SELECT t.task_id,COALESCE(t.progress,0) progress,c.task_type,c.need
            FROM task_cfg c LEFT JOIN task t ON t.uid=? AND t.task_id=c.task_id
            WHERE c.task_id=? AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
            AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
            AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=? AND l.key_id=c.task_id
                AND l.kind IN ('task','illustrated_task'))''', (uid, tid, int(time.time()), uid))
        if not rows:
            continue
        row = rows[0]
        value = min(int(row['need']), level)
        if value > int(row['progress']):
            db.execute('INSERT INTO task(uid,task_id,task_type,progress) VALUES(?,?,?,?) '
                       'ON CONFLICT(uid,task_id) DO UPDATE SET progress=excluded.progress',
                       (uid, tid, row['task_type'], value))
            changed.append(dict(id=tid, progress=value, complete_flag=0))
    return changed


def restore_for_login(db, uid):
    """Call before the login currency snapshot and task generation; no pushes."""
    with savepoint_transaction(db):
        ensure_tasks(db, uid)
        sync_level(db, uid, notify=False)
        return project_tasks(db, uid)


def publish_progress(ctx, uid):
    """Call after QWorld submissions; native crew and knowledge stay atomic."""
    checkpoint = checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            state = read(ctx.db, uid)
            skills = unlocked_skills(ctx.db, uid)
            if skills != state['skills']:
                state['skills'] = skills
                save(ctx.db, uid, state)
                ctx.append_frame(frame(ctx, 86035, sea_payload(ctx.db, uid)))
            level = sync_level(ctx, uid)
            changed = project_tasks(ctx.db, uid)
            if changed:
                ctx.append_frame(frame(ctx, 28007, dict(progress_list=changed)))
            return dict(photograph_ids=photograph_ids(ctx.db, uid), skill_ids=skills, level=level)
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise


def activity_open(db, uid):
    from generator import get_activity_list
    now = int(time.time())
    visible = {int(row['activity_id']) for row in get_activity_list(db, uid)
               if int(row.get('state') or 0) == 1 and int(row.get('start_time') or 0) <= now
               and (not row.get('stop_time') or int(row['stop_time']) > now)}
    return ACTIVITY in visible and CHILD_ACTIVITY in visible


def enter_validate(ctx, uid, sea_id, skill_id=0):
    """Validate type84's native dest (400xxx), not its 52402xx resource stage."""
    require(type(sea_id) is int and sea_id in SEA_STAGES, '航行关卡不存在')
    require(type(skill_id) is int and skill_id >= 0, '航行主动技能无效')
    require(activity_open(ctx.db, uid), '刺桐航行活动未开放')
    stage = SEA_STAGES[sea_id]
    state = read(ctx.db, uid)
    skills = unlocked_skills(ctx.db, uid)
    require(not skill_id or skill_id in skills and SEA_SKILLS[skill_id]['type'] == 2,
            '航行主动技能未解锁')
    if stage['type'] == 2:
        require(11109001 in finished_subquests(ctx.db, uid), '渡洋承运尚未解锁')
        require(not stage['pre_stage'] or stage['pre_stage'] in state['completed'], '航行前置关卡未完成')
    else:
        related = [int(i) for i, row in CFG['tasks'].items()
                   if row['condition'] == 330001 and row['additional_parameter'] == [stage['stage_id']]]
        require(bool(related), '航行关卡缺少原生任务入口')
        from quanzhou_quest import read as read_quests, advance
        mains, tasks = read_quests(ctx.db, uid)
        advance(ctx.db, uid, mains, tasks)
        require(any(tasks[tid]['status'] in (0, 1) for tid in related), '剧情航行任务尚未开放')
    return dict(stage_id=stage['stage_id'], sea_id=sea_id, skill_id=skill_id,
                unlocked_skills=skills)


def settle(ctx, uid, sea_id, won):
    """Called by a validated battle receipt. Grants native first-clear drops once.

    QWorld condition330001 progression belongs to the caller and quest module,
    so this function never advances battle tasks by itself.
    """
    require(type(sea_id) is int and sea_id in SEA_STAGES, '航行结算关卡不存在')
    require(type(won) is bool, '航行结算结果无效')
    if not won:
        return dict(first=False, reward_list=[])
    checkpoint = checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            state = read(ctx.db, uid)
            if sea_id in state['completed']:
                return dict(first=False, reward_list=[])
            stage = SEA_STAGES[sea_id]
            rewards = Counter()
            if stage['reward']:
                drop = CFG['drops'][str(stage['reward'])]
                require(not drop['weight_drop'] and not drop['random_drop'], '航行首通掉落含未知随机规则')
                for iid, amount, chance in drop['base_drop']:
                    require(chance == 100 and amount > 0, '航行首通掉落概率无效')
                    rewards[iid] += amount
            state['completed'] = sorted([*state['completed'], sea_id])
            state['skills'] = unlocked_skills(ctx.db, uid)
            save(ctx.db, uid, state)
            items = list(rewards.items())
            summary = InventoryService.grant_items(ctx, uid, items, source='quanzhou_voyage_first') if items else {}
            actual = InventoryService.rewards_after_overflow(items, summary)
            ctx.append_frame(frame(ctx, 86035, sea_payload(ctx.db, uid)))
            return dict(first=True, reward_list=[dict(id=i, num=n) for i, n in actual])
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise
