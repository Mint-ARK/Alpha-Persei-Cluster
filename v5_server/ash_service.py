"""Ash shooting: first-clear film, native stage delta and permanent progress."""
import json
from pathlib import Path
import time

from account_db import savepoint_transaction
from core import Operation, operation
from event_bus import bus, Events
from hodur_service import atomic, require

CFG=json.loads(Path(__file__).with_name('ash_contract.json').read_text('utf-8'))
ACTIVITY=CFG['activity_id']
STAGES={int(k):v for k,v in CFG['stages'].items()}
REAL={s['stage_id']:i for i,s in STAGES.items()}
SKILLS={int(k):v for k,v in CFG['skills'].items()}
NOTICES={int(k):v for k,v in CFG['notices'].items()}
LIMITED_TASKS=set(CFG['limited_tasks'])


def limited_context(activities):
    now=int(time.time())
    return any(int(r['activity_id'])==CFG['parent_activity'] and int(r.get('state') or 0)==1
        and int(r.get('start_time') or 0)<=now<int(r.get('stop_time') or 0) for r in activities)


def limited(db,uid):
    from generator import get_activity_list
    return limited_context(get_activity_list(db,uid))


def activity_policy(activities):
    if not limited_context(activities):
        for r in activities:
            if int(r['activity_id']) in CFG['limited_activities']:r['state']=0
    return activities


def visible_tasks(db,uid,rows,*,event=None):
    if not any(int(r['task_id']) in LIMITED_TASKS for r in rows):return rows
    if event is None:event=limited(db,uid)
    return rows if event else [r for r in rows if int(r['task_id']) not in LIMITED_TASKS]


def require_claimable(db,uid,ids):
    if LIMITED_TASKS.intersection(map(int,ids)):require(limited(db,uid),'列车大劫案常驻模式不可领取限时活动奖励')


def read(db,uid):
    out={}
    for r in db.query('SELECT stage_id,point FROM ash_shoot_stage WHERE uid=?',(uid,)):
        stage=r['stage_id']
        if type(stage) is int and stage in STAGES:
            point=int(r['point'] or 0)
            out[stage]=point if STAGES[stage]['type']==2 and 0<=point<=0xffffffff else 0
    return out


def payload(db,uid):return dict(pass_stages=[dict(stage_id=i,point=p) for i,p in sorted(read(db,uid).items())])


def film(db,uid):
    from inventory_service import InventoryService
    return InventoryService.get_item_balance(db,uid,79)[1]


def mirror_point(db,uid):
    balance=film(db,uid)
    rows=db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=?',(uid,ACTIVITY))
    if (rows and rows[0]['point']!=balance) or (not rows and balance):
        db.execute('INSERT INTO activity_point(uid,activity_id,point,update_ts) VALUES(?,?,?,?) '
            'ON CONFLICT(uid,activity_id) DO UPDATE SET point=excluded.point,update_ts=excluded.update_ts',
            (uid,ACTIVITY,balance,int(time.time())))


def entry(db,uid,real,activity,opts=None):
    require(type(real) is int and real in REAL and type(activity) is int and activity==ACTIVITY,'未知列车大劫案关卡或活动')
    stage=REAL[real];cfg=STAGES[stage];clears=read(db,uid)
    require(not cfg['front_unlock_id'] or cfg['front_unlock_id'] in clears,'列车大劫案前置关卡未通关')
    if cfg['type']==2:require(all(i in clears for i,s in STAGES.items() if s['type']==1),'影片花絮需完成全部正片关卡')
    if opts is not None:
        require(isinstance(opts,list),'列车技能参数格式错误')
        selected=[];seen=False
        for item in opts:
            require(isinstance(item,dict) and type(item.get('key')) is int and item['key']==1 and not seen,'未知或重复列车技能参数')
            selected=item.get('value',[]);seen=True
        require(isinstance(selected,list) and len(selected)<=1 and all(type(i) is int and i in SKILLS for i in selected),'未知列车技能')
        balance=film(db,uid)
        require(all(balance>=SKILLS[i] for i in selected),'列车技能影片进程尚未解锁')
    return stage


def settle(ctx,uid,real,activity,result,report):
    require(type(result) is int and result in (1,2,3),'列车战斗结果不合法')
    with atomic(ctx):
        stage=entry(ctx.db,uid,real,activity);cfg=STAGES[stage]
        if cfg['type']==1 and result!=1:return [],[]
        kills=report.get('enemy_dead_num',0)
        require(type(kills) is int and 0<=kills<=0xffffffff,'列车战报击杀数不合法')
        points=min(0xffffffff,kills*CFG['scoring']['points_per_enemy']) if cfg['type']==2 else 0
        records=read(ctx.db,uid);first=stage not in records
        already=ctx.db.query("SELECT 1 FROM claim_ledger WHERE uid=? AND kind='ash_stage_first' AND key_id=?",(uid,stage))
        rewards=[(79,cfg['film'])] if first and not already and cfg['film'] else []
        best=max(records.get(stage,0),points)
        if first or best!=records[stage]:
            ctx.db.execute('INSERT INTO ash_shoot_stage(uid,stage_id,point,update_ts) VALUES(?,?,?,?) '
                'ON CONFLICT(uid,stage_id) DO UPDATE SET point=excluded.point,update_ts=excluded.update_ts',(uid,stage,best,int(time.time())))
        if rewards:
            from inventory_service import InventoryService
            InventoryService.grant_items(ctx,uid,rewards,source='ash_first_clear')
            ctx.db.execute("INSERT INTO claim_ledger(uid,kind,key_id,claim_ts) VALUES(?,'ash_stage_first',?,?)",(uid,stage,int(time.time())))
        mirror_point(ctx.db,uid)
        bus.emit_required(Events.ASH_PROGRESS,ctx,uid)
        from operations import _refresh_frames
        frames=_refresh_frames(ctx,uid,strict=True)
        if first or cfg['type']==2:
            from codec import encode
            from middleware import DownFrame
            # Lua keeps max(point) but separately caches this report's point.
            packet=(ctx.codec_encode or encode)('sc_68183',dict(pass_stage=dict(stage_id=stage,point=points)))
            require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'列车关卡差量帧为空')
            frames.append(DownFrame(68183,bytes(packet)))
        return frames,rewards


def project_tasks(db,uid):
    mirror_point(db,uid)
    marks=','.join('?' for _ in LIMITED_TASKS)
    rows=db.query(f'''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND (c.condition IN (380001,380002) OR c.task_id IN ({marks}))
        AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0 AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger cl WHERE cl.uid=t.uid AND cl.key_id=t.task_id AND cl.kind IN ('task','illustrated_task'))''',
        (uid,*LIMITED_TASKS,int(time.time())))
    rows=visible_tasks(db,uid,rows)
    if not rows:return []
    records=read(db,uid);balance=film(db,uid);changes=[];completed={}
    for r in sorted(rows,key=lambda r:r['condition']==501):
        try:params=json.loads(r['additional_parameter']) if isinstance(r['additional_parameter'],str) else r['additional_parameter']
        except (ValueError,TypeError):continue
        if not isinstance(params,list) or any(type(i) is not int for i in params):continue
        cond=int(r['condition']);need=int(r['need'] or 0)
        if need<=0:continue
        if cond==380001 and need==1 and len(params)==1:value=int(params[0] in records)
        elif cond==380002 and need==1 and len(params)==2:value=int(params[0] in records and records[params[0]]>=params[1])
        elif cond==300 and params==[79]:value=balance
        elif cond==501 and params and r['task_id'] in LIMITED_TASKS:
            key=tuple(params)
            if key not in completed:
                ph=','.join('?' for _ in params)
                result=db.query(f'''SELECT COUNT(*) AS n FROM task t JOIN task_cfg c ON c.task_id=t.task_id
                    WHERE t.uid=? AND t.task_id IN ({ph}) AND (t.progress>=c.need OR t.claimed_ts>0 OR t.complete_flag=1)''',(uid,*params))
                completed[key]=int(result[0]['n'])
            value=completed[key]
        else:continue
        progress=min(need,value)
        if progress>int(r['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(progress,uid,r['task_id']))
            changes.append(dict(id=r['task_id'],progress=progress,complete_flag=0))
    return changes


def notify_tasks(ctx,uid):
    changes=project_tasks(ctx.db,uid)
    if changes:
        from codec import encode
        from middleware import DownFrame
        packet=(ctx.codec_encode or encode)('sc_28007',dict(progress_list=changes))
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'列车任务差量帧为空')
        ctx.append_frame(DownFrame(28007,bytes(packet)))


def restore_for_login(db,uid):
    with savepoint_transaction(db):return project_tasks(db,uid)


def notices_payload(db,uid):
    rows=db.query("SELECT key_id FROM claim_ledger WHERE uid=? AND kind='ash_momotalk_notice' ORDER BY key_id",(uid,))
    return dict(momotalk_list=[r['key_id'] for r in rows if r['key_id'] in NOTICES])


@operation
class AshReadMomotalkOp(Operation):
    cmd,sc=12120,12121
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        value=data.get('momotalk')
        require(type(value) is int and value in NOTICES,'未知列车对话提示')
        require(film(ctx.db,self.uid)>=NOTICES[value],'列车对话影片进程尚未解锁')
        ctx.db.execute("INSERT OR IGNORE INTO claim_ledger(uid,kind,key_id,claim_ts) VALUES(?,'ash_momotalk_notice',?,?)",(self.uid,value,int(time.time())))
        return dict(result=0)

    def respond(self,result,data,ctx):
        from codec import encode
        from middleware import DownFrame
        packet=(ctx.codec_encode or encode)('sc_12121',result)
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'列车对话回执为空')
        return [DownFrame(self.sc,bytes(packet))]
