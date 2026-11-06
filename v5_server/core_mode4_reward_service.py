"""Bounded Mode4 reward guard; aggregate-score tasks await the per-team model."""
from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
import time

from core import OperationError


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name('core_mode4_rewards_cfg.json').read_text('utf-8'))


def definition(activity,*,version=None):
    import res_version_manager as versions
    current=int(versions.get_version_config(version)['core_verification_activity_id'])
    profile=next((p for p in catalog()['profiles'].values() if p['activity']==current),None)
    if profile is None or int(activity) not in (profile['parent'],profile['activity']):
        raise OperationError(2,'迭代校验Mode4活动不匹配')
    return profile


def facts(db,uid,profile):
    # Read failures must propagate; neither progress nor claims have an empty fallback.
    progress=db.query('SELECT stage_id,is_cleared,mode FROM core_verification_cl_progress WHERE uid=? AND activity_id=?',
                      (int(uid),profile['activity']))
    cleared={int(r['stage_id']) for r in progress if int(r['is_cleared'])==1 and int(r['mode'])==4}
    claimed={int(r['task_id']) for r in db.query(
        'SELECT task_id FROM core_verification_cl_task_claimed WHERE uid=? AND activity_id IN (?,?)',
        (int(uid),profile['parent'],profile['activity']))}
    return cleared,claimed


def qualified(task,cleared):
    # A94 has no durable team scores. A single historical best cannot prove 11307.
    return task['condition_type']==11304 and int(task['params'][0]) in cleared


def task_states(db,uid,activity):
    # Snapshot requests can explicitly target either served resource baseline.
    profile=next((p for p in catalog()['profiles'].values() if p['activity']==int(activity)),None)
    if profile is None:return []
    cleared,claimed=facts(db,uid,profile)
    return [dict(assignment_id=int(tid),state=2 if int(tid) in claimed else int(qualified(task,cleared)))
            for tid,task in sorted(profile['tasks'].items(),key=lambda pair:int(pair[0]))]


def claim(ctx,uid,activity,ids):
    if ctx.db is None:raise OperationError(2,'迭代校验Mode4存档不可用')
    profile=definition(activity)
    if (not isinstance(ids,list) or not ids or len(ids)>len(profile['tasks'])
            or any(type(tid) is not int or str(tid) not in profile['tasks'] for tid in ids)
            or len(set(ids))!=len(ids)):
        raise OperationError(2,'迭代校验Mode4任务列表无效')
    import generator
    now=int(time.time())
    visible={int(a['activity_id']) for a in generator.get_activity_list(ctx.db,uid)
             if int(a.get('state') or 0)==1 and int(a.get('start_time') or 0)<=now
             and (not a.get('stop_time') or now<int(a['stop_time']))}
    if profile['parent'] not in visible or profile['activity'] not in visible:
        raise OperationError(2,'迭代校验Mode4活动未开放')
    cleared,claimed=facts(ctx.db,uid,profile)
    if any(tid in claimed for tid in ids):raise OperationError(2013,'迭代校验Mode4任务已领取')
    if any(not qualified(profile['tasks'][str(tid)],cleared) for tid in ids):
        raise OperationError(2005,'迭代校验Mode4任务尚未达成或缺少可靠队伍总分')
    merged=Counter()
    for tid in ids:
        for item in profile['tasks'][str(tid)]['items']:
            iid,count=int(item['id']),int(item['num'])
            if iid<=0 or count<=0:raise ValueError('迭代校验Mode4奖励配置无效')
            merged[iid]+=count
    if not merged:raise ValueError('迭代校验Mode4奖励配置为空')
    for tid in ids:ctx.db.claim_core_verification_cl_task(uid,profile['activity'],tid)
    return [dict(id=i,num=n) for i,n in sorted(merged.items())]
