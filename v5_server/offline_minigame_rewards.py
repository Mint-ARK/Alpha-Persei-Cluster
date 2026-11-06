"""Explicit single-player compensation: resident PVP tasks may be claimed.

This policy grants eligibility, never battle/rank facts or inventory. Native
28010/28014 claims retain their reward and permanent claim-ledger semantics.
"""
import json

TASK_IDS = (60533, 60534, 60535, 60536)


def restore_tasks(db, uid):
    from activity_content_service import catalog
    native = catalog()['tasks']
    rows = []
    for tid in TASK_IDS:
        cfg = native[str(tid)]
        if cfg['type'] != 211 or cfg['condition'] != 420001 or cfg['activity_id'] != 0:
            raise ValueError('多人常驻奖励配置归属异常')
        rows.append((tid, cfg))
    changes = 0
    with db.transaction():
        for tid, cfg in rows:
            db.execute('''INSERT INTO task_cfg
                (task_id,name,task_type,condition,desc,need,activity_id,pre_id,reward_json,additional_parameter)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
                name=excluded.name,task_type=excluded.task_type,condition=excluded.condition,
                desc=excluded.desc,need=excluded.need,activity_id=excluded.activity_id,
                pre_id=excluded.pre_id,reward_json=excluded.reward_json,
                additional_parameter=excluded.additional_parameter''',
                (tid,cfg['name'],cfg['type'],cfg['condition'],cfg['desc'],cfg['need'],
                 0,cfg['pre_id'],json.dumps(cfg['reward']),json.dumps(cfg.get('additional_parameter') or [])))
            old = db.get('task',uid,'AND task_id=?',(tid,))
            claimed = bool((old or {}).get('complete_flag')) or db.is_task_claimed(uid,tid)
            # Imported captures may contain only the native claimed flag. Keep
            # that history authoritative for the normal task claim handler too.
            if claimed and not db.is_claimed(uid,'task',tid):
                db.mark_claimed(uid,'task',tid)
            target = dict(task_id=tid,name=cfg['name'],task_type=211,
                          progress=max(int((old or {}).get('progress') or 0),int(cfg['need'])),
                          complete_flag=1 if claimed else 0,expired_ts=0,
                          claimed_ts=int((old or {}).get('claimed_ts') or 0))
            if old is None or any(old.get(k) != v for k,v in target.items()):
                db.upsert('task',uid,target,keys=('uid','task_id'))
                changes += 1
    return changes
