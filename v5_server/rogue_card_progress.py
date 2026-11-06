"""Authoritative per-run RogueCard statistics and task progress projection."""
import json
import time

from account_db import savepoint_transaction
from core import OperationError

COUNTERS={411010:'use_card_num',411011:'discard_num',411012:'use_gold_num',
          411013:'use_enhance_num',411014:'gain_joker_num',411015:'use_a_num'}


def progress_key(uid):return 'roguecard.runtime_v1.'+str(uid)


def task_rows(db,uid):
    return db.query('''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter
        FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND c.condition BETWEEN 411009 AND 411018
          AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
          AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
          AND NOT EXISTS(SELECT 1 FROM claim_ledger cl WHERE cl.uid=t.uid
                         AND cl.key_id=t.task_id AND cl.kind IN ('task','illustrated_task'))''',(uid,int(time.time())))


def ensure_statistics(db,uid,meta):
    stats=meta.get('statistics')
    if stats is None:
        # Existing capped task progress is a confirmed lower bound. Adopt it
        # once, before the first new run, so future actions count immediately.
        offsets={str(r['task_id']):max(0,int(r['progress'] or 0)) for r in task_rows(db,uid)
                 if r['condition']!=411018}
        stats=dict(version=1,counters={},wins={},offsets=offsets)
        meta['statistics']=stats
    return stats


def record_run(db,uid,meta,info,*,kind,won,deck=0,diff=0,hand_type_count=6):
    if not isinstance(info,dict):raise OperationError(2,'结算统计格式错误')
    gains={}
    for condition,field in COUNTERS.items():
        value=info.get(field,0)
        if type(value) is not int or not 0<=value<=0x7FFFFFFF:raise OperationError(2,'结算统计超出合法范围')
        gains[str(condition)]=value
    contains=info.get('hand_type_contain_num') or []
    if (not isinstance(contains,list) or any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in contains)
            or (contains and len(contains)!=hand_type_count)):
        raise OperationError(2,'牌型统计长度或数值错误')
    # Client settlement UI indexes hand_type_use_num as [length - cfg_id + 1].
    # Both native hand-type counter arrays pass unchanged through the bridge.
    # Containment already includes the subtype; do not add straight-flush again.
    gains['411016']=contains[-3] if contains else 0  # cfg 3 = flush
    gains['411017']=contains[-4] if contains else 0  # cfg 4 = straight
    stats=ensure_statistics(db,uid,meta)
    for condition,value in gains.items():
        stats['counters'][condition]=int(stats['counters'].get(condition,0))+value
    if kind==2 and won:
        key=f'{deck}:{diff}'
        stats['wins'][key]=int(stats['wins'].get(key,0))+1


def misc_view_count(rec,cfg):
    return len({int(tid) for tid in rec.get('view_thread_list',[])
                if cfg['posts'].get(str(tid),{}).get('type')==3})


def challenge_parameters(raw):
    try:
        values=json.loads(raw) if isinstance(raw,str) else raw
    except (TypeError,ValueError):return None
    if not isinstance(values,(tuple,list)) or len(values)!=2:return None
    deck,diff=values
    if type(deck) is not int or type(diff) is not int or not 0<=deck<=4 or not 0<=diff<=6:return None
    return deck,diff


def project(db,uid,statistics,viewed_misc):
    changes=[]
    for row in task_rows(db,uid):
        condition=int(row['condition']);need=int(row['need'] or 0)
        if need<=0:continue
        if condition==411009:
            parameters=challenge_parameters(row['additional_parameter'])
            if parameters is None:continue
            deck,minimum=parameters
            value=0
            for key,count in statistics.get('wins',{}).items():
                actual_deck,actual_diff=map(int,key.split(':'))
                if (not deck or deck==actual_deck) and actual_diff>=minimum:value+=int(count)
        elif condition==411018:
            value=viewed_misc
        else:
            value=int(statistics.get('counters',{}).get(str(condition),0))
        if condition!=411018:value+=int(statistics.get('offsets',{}).get(str(row['task_id']),0))
        new=min(need,value)
        if new>int(row['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(new,uid,row['task_id']))
            changes.append(dict(id=int(row['task_id']),progress=new,complete_flag=0))
    return changes


def notify_projection(ctx,uid,statistics,viewed_misc=0):
    changes=project(ctx.db,uid,statistics,viewed_misc)
    if changes:
        from middleware import DownFrame
        from codec import encode
        payload=(getattr(ctx,'codec_encode',None) or encode)('sc_28007',dict(progress_list=changes))
        if not isinstance(payload,(bytes,bytearray,memoryview)) or not payload:
            raise ValueError('诡谈夜话任务差量帧为空')
        frame=DownFrame(28007,bytes(payload))
        if hasattr(ctx,'append_frame'):ctx.append_frame(frame)
        else:ctx.pending_frames.append(frame)
    return changes


def restore_for_login(db,uid):
    """Restore from durable facts before the caller builds its task snapshot."""
    from rogue_card_service import CFG
    with savepoint_transaction(db):
        rows=db.query('SELECT value FROM server_global_config WHERE key=?',(progress_key(uid),))
        records=db.query('SELECT view_thread_list FROM rogue_card_game_record WHERE uid=?',(uid,))
        views=json.loads(records[0]['view_thread_list'] or '[]') if records else []
        if not rows and not views:return []
        meta=json.loads(rows[0]['value']) if rows else {}
        adopted='statistics' not in meta
        statistics=ensure_statistics(db,uid,meta)
        if adopted:
            db.execute('INSERT INTO server_global_config(key,value) VALUES(?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value',(progress_key(uid),json.dumps(meta)))
        return project(db,uid,statistics,misc_view_count(dict(view_thread_list=views),CFG))
