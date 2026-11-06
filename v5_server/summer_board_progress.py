"""Native task facts, clear history and external reward-task projections."""
import json
import time
import summer_board_service as board
from hodur_service import require


def history(db,uid,state=None):
    if state is None:_,state=board.read(db,uid)
    # Other HistoryData keys were originally sent as a static login payload.
    # Keep those keys while replacing every chess condition from durable facts.
    records={}
    for row in db.query('SELECT payload,payload_json FROM login_push WHERE cmd=51001 ORDER BY seq'):
        try:data=json.loads(row['payload_json'] or '{}')
        except (ValueError,TypeError):continue
        if not data and row.get('payload'):
            from codec import decode
            raw=bytes(row['payload'])
            try:data=decode(raw,'sc_51001') or {}
            except Exception:data={}
        for item in data.get('data_list',[]):records[item['id']]=int(item['data'])
    for row in db.query("SELECT key_id,data FROM history_cmd WHERE uid=? AND kind='history'",(uid,)):
        if str(row['key_id']).isdigit() and str(row['data']).isdigit():records[int(row['key_id'])]=int(row['data'])
    for cid,cfg in board.CFG['conditions'].items():records[int(cid)]=int(all(s in state['completed'] for s in cfg['params']))
    return dict(data_list=[dict(id=i,data=d) for i,d in sorted(records.items())])


def project_tasks(db,uid,state=None):
    if state is None:_,state=board.read(db,uid)
    done=set(state.get('finished_tasks',[]));completed=set(state['completed']);changes=[];now=int(time.time())
    for row in db.query('''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND c.condition IN (163001,163003) AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
        AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=t.uid AND l.key_id=t.task_id AND l.kind IN ('task','illustrated_task'))''',(uid,now)):
        cfg=board.CFG['external_tasks'].get(str(row['task_id']))
        if not cfg:continue
        params=cfg['additional_parameter'];value=int(all(i in done for i in params)) if cfg['condition']==163003 else int(all(i in completed for i in params) if params else bool(completed))
        progress=min(int(row['need']),value)
        if progress>int(row['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(progress,uid,row['task_id']))
            changes.append(dict(id=row['task_id'],progress=progress,complete_flag=0))
    return changes


def persist(ctx,uid,extra,state,events):
    active=state['active'];new_clear=False;old=set(state.get('finished_tasks',[]))
    if active:
        reached={t['id'] for t in active['map']['task'] if t['progress']>=board.CFG['tasks'][str(t['id'])]['need']}
        state['finished_tasks']=sorted(set(state.get('finished_tasks',[]))|reached|set(active.get('tasks_done',[])))
        if board.attribute(board.player(active['map']),7):
            require(set(board.CFG['map_tasks'][str(active['chapter_id'])])<=reached|set(active.get('tasks_done',[])),'棋盘终点任务尚未全部完成')
            if active['chapter_id'] not in state['completed']:
                state['completed'].append(active['chapter_id']);new_clear=True
    board.save(ctx.db,uid,extra,state)
    if new_clear:
        events.append((51001,dict(data_list=[dict(id=int(i),data=int(all(s in state['completed'] for s in cfg['params'])))
            for i,cfg in board.CFG['conditions'].items() if active['chapter_id'] in cfg['params']])))
    changes=project_tasks(ctx.db,uid,state) if new_clear or old!=set(state.get('finished_tasks',[])) else []
    if changes:events.append((28007,dict(progress_list=changes)))


def finish_task(active,tid):
    require(type(tid) is int and str(tid) in board.CFG['tasks'],'未知棋盘任务')
    task=next((t for t in active['map']['task'] if t['id']==tid),None)
    require(task is not None and task['progress']>=board.CFG['tasks'][str(tid)]['need'],'棋盘任务尚未完成或已提交')
    active['map']['task'].remove(task);active['tasks_done']=sorted(set(active.get('tasks_done',[]))|{tid})
    from summer_board_movement import Engine
    engine=Engine(active['chapter_id'],active['map']);pool=board.CFG['tasks'][str(tid)]['finishevent']
    if pool:engine.pool(pool,18,[tid])
    return dict(result=0),engine.frames
