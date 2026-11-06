"""Resident PVE AutoChess first clears, medals and recoverable task facts.

Called inside the AutoChess operation transaction; no multiplayer records are
used and no task is auto-claimed. The native drop item is one first-clear token.
"""
import json
from pathlib import Path
import time

from core import OperationError
from event_bus import bus, Events
from inventory_service import InventoryService
from middleware import DownFrame

CFG = json.loads(Path(__file__).with_name('autochess_cfg.json').read_text('utf-8'))
ACTIVITY = 3740601
OWNERS = {3740601, 3700401, 3700402, 3700403}
STAGES = {int(k): v for k, v in CFG['stages'].items() if 1 <= int(v.get('area', 0)) <= 3}
MEDALS = {int(k): v for k, v in CFG['medals'].items()
          if v.get('unlock_stage') and set(v['unlock_stage']) <= set(STAGES)}


def cleared_stages(db, uid):
    return {int(r['stage_id']) for r in db.query(
        'SELECT stage_id FROM autochess_stage WHERE uid=? AND clear_time>0', (uid,))
        if int(r['stage_id']) in STAGES}


def project_tasks(db, uid):
    """Rebuild active native tasks from durable PVE facts, including old saves."""
    from activity_content_service import catalog
    native = catalog()['tasks']
    cleared = cleared_stages(db, uid)
    tokens = sum(int(STAGES[s]['drop_item'][1]) for s in cleared
                 if STAGES[s].get('drop_item') and STAGES[s]['drop_item'][0] == 80)
    bosses = sum(int(STAGES[s].get('is_challenge', 0)) == 1 for s in cleared)
    # Three normal gym badges; the all-hard medal isn't a fourth gym.
    gym_medals = sum(set(c['unlock_stage']) <= cleared for i, c in MEDALS.items()
                     if any(not STAGES[s].get('is_challenge') for s in c['unlock_stage']))
    rows = db.query('''SELECT t.task_id,t.progress,t.complete_flag,t.claimed_ts,t.expired_ts,
                      c.need,c.condition,c.activity_id,c.additional_parameter
                      FROM task t JOIN task_cfg c ON c.task_id=t.task_id WHERE t.uid=?''', (uid,))
    claimed = {int(r['key_id']) for r in db.query(
        "SELECT key_id FROM claim_ledger WHERE uid=? AND kind IN ('task','illustrated_task')", (uid,))
        if str(r['key_id']).isdigit()}
    changes = []
    now = int(time.time())
    for row in rows:
        tid = int(row['task_id']); cfg = native.get(str(tid))
        if (not cfg or cfg['activity_id'] not in OWNERS
                or int(row['activity_id'] or 0) != cfg['activity_id']
                or int(row['condition'] or 0) != cfg['condition']
                or row['complete_flag'] or row['claimed_ts'] or tid in claimed
                or (row['expired_ts'] and row['expired_ts'] <= now)):
            continue
        condition = cfg['condition']
        if condition == 300 and cfg.get('additional_parameter') == [80]:
            value = tokens
        elif condition == 390001:
            value = bosses
        elif condition == 390008:
            value = gym_medals
        else:
            continue
        value = min(int(row['need'] or 1), value)
        if value > int(row['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?', (value,uid,tid))
            changes.append(dict(id=tid,progress=value,complete_flag=0))
    return changes


def settle_completed(ctx, uid, stage_id):
    """Publish one successful whole-stage result, after pending identity validation."""
    stage_id = int(stage_id)
    if stage_id not in STAGES:
        raise OperationError(2, '非本期人机决斗王关卡')
    db = ctx.db
    cleared = cleared_stages(db, uid)
    first = stage_id not in cleared
    now = int(time.time())
    if first:
        db.execute('INSERT INTO autochess_stage(uid,stage_id,clear_time) VALUES(?,?,?) '
                   'ON CONFLICT(uid,stage_id) DO UPDATE SET clear_time=excluded.clear_time',
                   (uid,stage_id,now))
        cleared.add(stage_id)
    for mid, cfg in MEDALS.items():
        if set(cfg['unlock_stage']) <= cleared:
            db.execute('INSERT OR IGNORE INTO autochess_medal(uid,medal_id,level,unlock_time,upgrade_time) '
                       'VALUES(?,?,1,?,0)', (uid,mid,now))
    rewards = []
    drop = STAGES[stage_id].get('drop_item')
    if first and drop:
        if not (isinstance(drop,list) and len(drop)==2 and all(type(v) is int and v>0 for v in drop)):
            raise ValueError('自走棋首通掉落配置异常')
        rewards = [(drop[0],drop[1])]
        InventoryService.grant_items(ctx, uid, rewards, source='autochess_first_clear')
    # Preserve generic pass subscribers; FIRST_CLEAR occurs only on a new fact.
    # Native 300/80 and 390001/390008 use set-from-facts projection below, not
    # a second additive PT broadcast that could double-count a recovered task.
    bus.emit_required(Events.STAGE_PASS,ctx,uid,stage_id=stage_id,activity_id=ACTIVITY,source='autochess')
    if first:
        bus.emit_required(Events.STAGE_FIRST_CLEAR,ctx,uid,stage_id=stage_id,activity_id=ACTIVITY,source='autochess')
    changed = project_tasks(db,uid)
    if changed:
        payload = ctx.codec_encode('sc_28007',dict(progress_list=changed))
        if not payload:
            raise ValueError('必要自走棋任务差量 sc_28007 为空')
        ctx.append_frame(DownFrame(28007,payload))
    return dict(first=first,rewards=[dict(id=i,num=n) for i,n in rewards])
