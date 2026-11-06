"""Wuluo vehicle-ball progress, native task semantics and illustration rewards."""
from contextlib import contextmanager
import json
from pathlib import Path
import time

from account_db import savepoint_transaction
from core import OperationError
from event_bus import bus, Events
from operation_notifications import checkpoint_notifications, restore_notifications

CFG=json.loads(Path(__file__).with_name('vehicle_ball_contract.json').read_text('utf-8'))
ACTIVITY=CFG['activity_id']
STAGES={int(k):v for k,v in CFG['stages'].items()}
VEHICLES={int(k):v for k,v in CFG['vehicles'].items() if v['type']==1}
BUFFS={int(k):v for k,v in CFG['buffs'].items()}
LIMITED_TASKS=set(CFG['limited_tasks'])


def require(ok,message):
    if not ok:raise OperationError(2,message)


@contextmanager
def atomic(ctx):
    checkpoint=checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):yield
    except BaseException:
        restore_notifications(ctx,checkpoint)
        raise


def limited_context(activities):
    now=int(time.time())
    return any(int(a['activity_id'])==CFG['parent_activity'] and int(a.get('state') or 0)==1
               and int(a.get('start_time') or 0)<=now<int(a.get('stop_time') or 0) for a in activities)


def limited(db,uid):
    from generator import get_activity_list
    return limited_context(get_activity_list(db,uid))


def activity_policy(activities):
    if not limited_context(activities):
        for row in activities:
            if int(row['activity_id']) in CFG['limited_activities']:row['state']=0
    return activities


def visible_tasks(db,uid,rows,*,event=None):
    if not any(int(r['task_id']) in LIMITED_TASKS for r in rows):return rows
    if event is None:event=limited(db,uid)
    return rows if event else [r for r in rows if int(r['task_id']) not in LIMITED_TASKS]


def require_claimable(db,uid,ids):
    if LIMITED_TASKS.intersection(map(int,ids)):
        require(limited(db,uid),'极限机装常驻模式不可领取限时活动奖励')


def values(raw):
    data=json.loads(raw or '[]') if isinstance(raw,str) else raw
    require(isinstance(data,list),'极限机装存档列表格式错误')
    return data


def read(db,uid):
    rows=db.query('SELECT * FROM vehicle_ball_progress WHERE uid=?',(uid,))
    row=rows[0] if rows else {}
    stages=sorted({i for i in values(row.get('stages_json',[])) if type(i) is int and i in STAGES})
    buffs={}
    for item in values(row.get('unlock_buffs_json',[])):
        require(isinstance(item,dict),'极限机装图鉴格式错误')
        key,state=item.get('key'),item.get('value')
        if type(key) is int and key in BUFFS and type(state) is int and state in (0,1):
            buffs[key]=max(buffs.get(key,0),state)
    vehicle=row.get('using_vehicle',50111)
    if type(vehicle) is not int or vehicle not in VEHICLES or VEHICLES[vehicle]['unlock_stage'] not in [0,*stages]:vehicle=50111
    return dict(stages=stages,using_vehicle=vehicle,unlock_buffs=[dict(key=k,value=v) for k,v in sorted(buffs.items())])


def save(db,uid,data):
    db.execute('INSERT INTO vehicle_ball_progress(uid,using_vehicle,stages_json,unlock_buffs_json,update_ts) '
               'VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET using_vehicle=excluded.using_vehicle,'
               'stages_json=excluded.stages_json,unlock_buffs_json=excluded.unlock_buffs_json,update_ts=excluded.update_ts',
               (uid,data['using_vehicle'],json.dumps(data['stages']),json.dumps(data['unlock_buffs']),int(time.time())))


def statistics_key(uid):return 'vehicle_ball.statistics_v1.'+str(uid)


def statistics(db,uid):
    rows=db.query('SELECT value FROM server_global_config WHERE key=?',(statistics_key(uid),))
    stats=json.loads(rows[0]['value']) if rows else dict(version=1,kills=0,merges=0)
    require(isinstance(stats,dict) and stats.get('version')==1 and
            all(type(stats.get(k)) is int and stats[k]>=0 for k in ('kills','merges')),'极限机装统计格式错误')
    # Legacy 422003 already counted modifications correctly. Legacy 422004
    # counted stage wins, so it cannot serve as a trustworthy kill baseline.
    old=db.query('SELECT MAX(t.progress) AS n FROM task t JOIN task_cfg c ON c.task_id=t.task_id '
                 'WHERE t.uid=? AND c.condition=422003 AND c.additional_parameter IN (\'[]\',\'{}\')',(uid,))
    stats['merges']=max(stats['merges'],int(old[0]['n'] or 0))
    return stats


def validate_activity(activity):
    require(type(activity) is int and activity==ACTIVITY,'未知极限机装活动')


def add_buffs(data,buffs):
    require(isinstance(buffs,list) and all(type(i) is int and i in BUFFS for i in buffs),'未知极限机装武装')
    states={b['key']:b['value'] for b in data['unlock_buffs']}
    for i in buffs:states.setdefault(i,0)
    data['unlock_buffs']=[dict(key=k,value=v) for k,v in sorted(states.items())]


def settle(ctx,uid,activity,stage,is_pass,kv_list,buffs):
    validate_activity(activity)
    require(type(stage) is int and stage in STAGES,'未知极限机装关卡')
    require(type(is_pass) is int and is_pass in (0,1),'极限机装结算结果不合法')
    require(isinstance(kv_list,list),'极限机装统计参数格式错误')
    counts={}
    for item in kv_list:
        require(isinstance(item,dict) and type(item.get('key')) is int and item['key'] in (1,2)
                and item['key'] not in counts and type(item.get('value')) is int and 0<=item['value']<=0xFFFFFFFF,
                '极限机装统计参数不合法或重复')
        counts[item['key']]=item['value']
    with atomic(ctx):
        data=read(ctx.db,uid)
        require(all(i in data['stages'] for i in STAGES if i<stage),'极限机装前置关卡尚未完成')
        add_buffs(data,buffs)
        if is_pass and stage not in data['stages']:data['stages'].append(stage);data['stages'].sort()
        stats=statistics(ctx.db,uid)
        stats['kills']+=counts.get(1,0);stats['merges']+=counts.get(2,0)
        save(ctx.db,uid,data)
        ctx.db.execute('INSERT INTO server_global_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (statistics_key(uid),json.dumps(stats)))
        bus.emit_required(Events.VEHICLE_BALL_PROGRESS,ctx,uid)
        return dict(result=0)


def select(ctx,uid,activity,vehicle):
    validate_activity(activity)
    require(type(vehicle) is int and vehicle in VEHICLES,'未知极限机装载具')
    with atomic(ctx):
        data=read(ctx.db,uid)
        require(VEHICLES[vehicle]['unlock_stage'] in [0,*data['stages']],'极限机装载具尚未解锁')
        data['using_vehicle']=vehicle;save(ctx.db,uid,data)
        return dict(result=0)


def unlock(ctx,uid,activity,buff):
    validate_activity(activity)
    with atomic(ctx):
        data=read(ctx.db,uid);add_buffs(data,[buff]);save(ctx.db,uid,data)
        bus.emit_required(Events.VEHICLE_BALL_PROGRESS,ctx,uid)
        return dict(result=0)


def claim(ctx,uid,activity,buff):
    validate_activity(activity)
    require(type(buff) is int and buff in BUFFS and len(BUFFS[buff]['reward'])==2,'该极限机装武装无图鉴奖励')
    with atomic(ctx):
        data=read(ctx.db,uid);states={b['key']:b['value'] for b in data['unlock_buffs']}
        require(buff in states,'极限机装武装图鉴尚未解锁')
        require(states[buff]==0,'极限机装图鉴奖励已领取')
        # A permanent receipt also protects old flags accidentally reset by a
        # late unlock report; the database remains the authoritative save.
        require(not ctx.db.query("SELECT 1 FROM claim_ledger WHERE uid=? AND kind='vehicle_ball_buff' AND key_id=?",(uid,buff)),
                '极限机装图鉴奖励已领取')
        item,num=BUFFS[buff]['reward']
        from inventory_service import InventoryService
        InventoryService.grant_items(ctx,uid,[(item,num)],source='vehicle_ball_buff_reward')
        for row in data['unlock_buffs']:
            if row['key']==buff:row['value']=1
        save(ctx.db,uid,data)
        ctx.db.execute("INSERT INTO claim_ledger(uid,kind,key_id,claim_ts) VALUES(?,'vehicle_ball_buff',?,?)",(uid,buff,int(time.time())))
        return dict(result=0,reward_list=[dict(id=item,num=num)])


def project_tasks(db,uid):
    marks=','.join('?' for _ in CFG['limited_tasks'])
    rows=db.query(f'''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND (c.condition BETWEEN 422001 AND 422004 OR c.task_id IN ({marks}))
        AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0 AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger cl WHERE cl.uid=t.uid AND cl.key_id=t.task_id AND cl.kind IN ('task','illustrated_task'))''',
        (uid,*CFG['limited_tasks'],int(time.time())))
    rows=visible_tasks(db,uid,rows)
    if not rows:return []
    data=read(db,uid);stats=statistics(db,uid);changes=[];completed={}
    for r in sorted(rows,key=lambda r:r['condition']==501):
        try:params=json.loads(r['additional_parameter']) if isinstance(r['additional_parameter'],str) else r['additional_parameter']
        except (ValueError,TypeError):continue
        if not isinstance(params,list) or any(type(v) is not int for v in params):continue
        cond=int(r['condition']);need=int(r['need'] or 0)
        if need<=0:continue
        if cond==422001 and need==1 and len(params)==1:value=int(params[0] in data['stages'])
        elif cond==422002 and not params:value=sum(bool(BUFFS[b['key']]['reward']) for b in data['unlock_buffs'])
        elif cond==422003 and not params:value=stats['merges']
        elif cond==422004 and not params:value=stats['kills']
        elif cond==501 and params and r['task_id'] in LIMITED_TASKS:
            key=tuple(params)
            if key not in completed:
                ph=','.join('?' for _ in params)
                done=db.query(f'''SELECT COUNT(*) AS n FROM task t JOIN task_cfg c ON c.task_id=t.task_id
                    WHERE t.uid=? AND t.task_id IN ({ph}) AND (t.progress>=c.need OR t.claimed_ts>0 OR t.complete_flag=1)''',(uid,*params))
                completed[key]=int(done[0]['n'])
            value=completed[key]
        else:continue
        progress=min(need,value)
        if progress>int(r['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(progress,uid,r['task_id']))
            changes.append(dict(id=int(r['task_id']),progress=progress,complete_flag=0))
    return changes


def notify_tasks(ctx,uid):
    changes=project_tasks(ctx.db,uid)
    if changes:
        from codec import encode
        from middleware import DownFrame
        packet=(ctx.codec_encode or encode)('sc_28007',dict(progress_list=changes))
        if not isinstance(packet,(bytes,bytearray,memoryview)) or not packet:raise ValueError('极限机装任务差量帧为空')
        ctx.append_frame(DownFrame(28007,bytes(packet)))


def restore_for_login(db,uid):
    with savepoint_transaction(db):return project_tasks(db,uid)
