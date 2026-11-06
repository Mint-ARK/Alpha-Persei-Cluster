"""Durable condition9 facts for successful Mimir observation-station dispatches."""
import json
import time

from account_db import savepoint_transaction
from lazy_timer import get_daily_5am_ts

KIND='admin_cat_dispatch_v1'


def read(db,uid):
    now=int(time.time());day=get_daily_5am_ts(now)
    rows=db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',(uid,KIND,'dispatch'))
    if rows:
        data=json.loads(rows[0]['value_json'])
        if (data.get('version')!=1 or any(type(data.get(k)) is not int or data[k]<0 for k in ('total','day','daily'))):
            raise ValueError('管理喵派遣累计记录损坏')
        return dict(data,day=day,daily=data['daily'] if data['day']==day else 0)
    # Old rows prove at most one dispatch, never an invented historical count.
    stamps=[int(q.get('start_time') or 0) for q in db.get_admin_cat_queues(uid)
            if 0<int(q.get('start_time') or 0)<=now and int(q.get('stop_time') or 0)>int(q.get('start_time') or 0)]
    old=db.get_admin_cat_explore_data(uid) or {}
    if 0<int(old.get('daily_time') or 0)<=now:stamps.append(int(old['daily_time']))
    return dict(version=1,total=int(bool(stamps)),day=day,daily=int(any(get_daily_5am_ts(t)==day for t in stamps)))


def record(db,uid,before):
    data=dict(before,total=before['total']+1,daily=before['daily']+1)
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
               'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
               (uid,KIND,'dispatch',json.dumps(data)))


def _unrestricted(raw):
    if raw in (None,''):return True
    try:
        values=json.loads(raw) if isinstance(raw,str) else raw
        return isinstance(values,(list,tuple)) and all(type(v) is int and v==0 for v in values)
    except (ValueError,TypeError):return False


def project_tasks(db,uid):
    # Both served native catalogs contain only6014(type6,[0],need1) and
    # 160145(type603,"",need1). Do not reinterpret other conditions as dispatch.
    with savepoint_transaction(db):
        facts=read(db,uid);now=int(time.time());changed=[]
        rows=db.query('''SELECT c.task_id,c.condition,c.need,c.task_type,c.additional_parameter,
            COALESCE(t.progress,0) progress,COALESCE(t.complete_flag,0) complete_flag,
            COALESCE(t.claimed_ts,0) claimed_ts,COALESCE(t.expired_ts,0) expired_ts
            FROM task_cfg c LEFT JOIN task t ON t.uid=? AND t.task_id=c.task_id
            WHERE c.task_id IN (6014,160145)''',(uid,))
        for row in rows:
            tid=int(row['task_id']);daily=tid==6014
            if (int(row['condition'])!=9 or int(row['need'])!=1
                    or int(row['task_type'])!=(6 if daily else 603) or not _unrestricted(row['additional_parameter'])
                    or row['complete_flag'] or row['claimed_ts'] or (row['expired_ts'] and row['expired_ts']<=now)):
                continue
            if not daily and db.query("SELECT 1 FROM claim_ledger WHERE uid=? AND key_id=? AND kind IN ('task','illustrated_task')",(uid,tid)):
                continue
            value=min(1,facts['daily'] if daily else facts['total'])
            if value<=int(row['progress']):continue
            expiry=int(row['expired_ts']) or (facts['day']+86400 if daily else 0)
            db.execute('INSERT INTO task(uid,task_id,task_type,progress,expired_ts) VALUES(?,?,?,?,?) '
                       'ON CONFLICT(uid,task_id) DO UPDATE SET progress=excluded.progress,task_type=excluded.task_type,expired_ts=excluded.expired_ts',
                       (uid,tid,row['task_type'],value,expiry))
            changed.append(dict(id=tid,progress=value,complete_flag=0,expired_timestamp=expiry))
        return changed


def notify(ctx,uid):
    from middleware import DownFrame
    changed=project_tasks(ctx.db,uid)
    if changed:
        raw=ctx.codec_encode('sc_28007',dict(progress_list=changed))
        if not isinstance(raw,(bytes,bytearray,memoryview)) or not raw:
            raise ValueError('管理喵派遣任务必要差量为空')
        frame=DownFrame(28007,bytes(raw))
        if hasattr(ctx,'append_frame'):ctx.append_frame(frame)
        else:
            if not hasattr(ctx,'pending_frames'):ctx.pending_frames=[]
            ctx.pending_frames.append(frame)
