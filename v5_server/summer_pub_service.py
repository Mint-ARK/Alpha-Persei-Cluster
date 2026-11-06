"""SummerPub progress authority; native callbacks apply their own local state."""
import json
from pathlib import Path
import time
from account_db import savepoint_transaction
from core import Operation, operation
from event_bus import bus, Events
from hodur_service import atomic, require

CFG=json.loads(Path(__file__).with_name('summer_pub_contract.json').read_text('utf-8'))
ACTIVITY=CFG['activity_id']
LEVELS={int(k):v for k,v in CFG['levels'].items()}
COOKS={int(k):v for k,v in CFG['cooks'].items()}
BALLS={int(k):v for k,v in CFG['balls'].items()}
REAL={s:l for l,v in LEVELS.items() if v['type']==1 for s in v['stages']}
COMBAT={l:v for l,v in LEVELS.items() if v['type']==1}
LIMITED_TASKS=set(CFG['limited_tasks'])


def limited_context(activities):
    now=int(time.time())
    return any(int(r['activity_id'])==CFG['parent_activity'] and int(r.get('state') or 0)==1
               and int(r.get('start_time') or 0)<=now<int(r.get('stop_time') or 0) for r in activities)


def limited(db,uid):
    from generator import get_activity_list
    return limited_context(get_activity_list(db,uid))


def activity_policy(rows):
    if not limited_context(rows):
        for r in rows:
            if int(r['activity_id']) in CFG['limited_activities']:r['state']=0
    return rows


def visible_tasks(db,uid,rows):
    if not any(int(r['task_id']) in LIMITED_TASKS for r in rows):return rows
    return rows if limited(db,uid) else [r for r in rows if int(r['task_id']) not in LIMITED_TASKS]


def require_claimable(db,uid,ids):
    if LIMITED_TASKS.intersection(map(int,ids)):require(limited(db,uid),'食与异世界常驻模式不可领取限时奖励')


def read(db,uid):
    rows=db.query('SELECT * FROM summer_pub_record WHERE uid=?',(uid,))
    if not rows:return dict(stage_list=[],cook_stages={},battle_stages=[],illustrated={},time_state=1)
    row=rows[0];out={}
    for key,kind in [('stage_list',list),('cook_stages',dict),('battle_stages',list),('illustrated',dict)]:
        value=json.loads(row[key]) if isinstance(row[key],str) else row[key]
        require(isinstance(value,kind),'餐厅存档格式损坏：'+key);out[key]=value
    out['stage_list']=sorted({i for i in out['stage_list'] if type(i) is int and i in BALLS})
    out['battle_stages']=sorted({i for i in out['battle_stages'] if type(i) is int and i in COMBAT})
    out['cook_stages']={str(i):out['cook_stages'][str(i)] for i in COOKS if type(out['cook_stages'].get(str(i))) is int and out['cook_stages'][str(i)] in (1,2)}
    out['illustrated']={str(i):out['illustrated'][str(i)] for i in CFG['illustrations'] if type(out['illustrated'].get(str(i))) is int and out['illustrated'][str(i)] in (1,2)}
    require(type(row['time_state']) is int and row['time_state'] in (1,2),'餐厅昼夜存档格式损坏')
    out['time_state']=row['time_state'];return out


def save(db,uid,rec):
    values=[json.dumps(rec[k]) for k in ['stage_list','cook_stages','battle_stages','illustrated']]
    db.execute('INSERT INTO summer_pub_record(uid,stage_list,cook_stages,battle_stages,illustrated,time_state,update_ts) VALUES(?,?,?,?,?,?,?) '
               'ON CONFLICT(uid) DO UPDATE SET stage_list=excluded.stage_list,cook_stages=excluded.cook_stages,battle_stages=excluded.battle_stages,illustrated=excluded.illustrated,time_state=excluded.time_state,update_ts=excluded.update_ts',
               (uid,*values,rec['time_state'],int(time.time())))


def payload(db,uid):
    r=read(db,uid)
    return dict(stage_list=r['stage_list'],battle_stage_list=r['battle_stages'],time_state=r['time_state'],
                cook_stage_list=[dict(stage_id=int(k),state=v) for k,v in sorted(r['cook_stages'].items())],
                illustrated=[dict(illustrated_id=int(k),view_state=v) for k,v in sorted(r['illustrated'].items())])


def passed(rec,level):
    cfg=LEVELS[level]
    if cfg['type']==1:return level in rec['battle_stages']
    if cfg['type']==2:return cfg['stages'][0] in rec['stage_list']
    return rec['cook_stages'].get(str(cfg['stages'][0]),0)==2


def unlocked(rec,level):
    prev=LEVELS[level]['prev_id']
    return not prev or passed(rec,prev)


def cook_unlocked(rec,dish):
    # Final dish 10800 references its own adventure node 4030307. That node
    # opens after 4030306; demanding its completion would create a cycle.
    return all(unlocked(rec,l) if LEVELS[l]['type']==0 and dish in LEVELS[l]['stages']
               else passed(rec,l) for l in COOKS[dish]['unlocked_by_level'])


def battle_entry(db,uid,dest,activity,*,story=False):
    level=REAL.get(dest,dest)
    require(type(dest) is int and level in COMBAT,'未知餐厅战斗关卡')
    require(type(activity) is int and activity in ((0,ACTIVITY) if story else (ACTIVITY,)),'餐厅活动不匹配')
    real=COMBAT[level]['stages'][0]
    require((CFG['battles'][str(real)]['tag']==2)==story,'餐厅剧情/普通战斗入口不匹配')
    require(unlocked(read(db,uid),level),'餐厅前置关卡未完成')
    return level


def first_rewards(ctx,uid,level,already):
    if already:return []
    rows=ctx.db.query("SELECT 1 FROM claim_ledger WHERE uid=? AND kind='summer_pub_first' AND key_id=?",(uid,level))
    if rows:return []
    rewards=LEVELS[level]['rewards']
    if rewards:
        from inventory_service import InventoryService
        InventoryService.grant_items(ctx,uid,rewards,source='summer_pub_first')
    ctx.db.execute("INSERT INTO claim_ledger(uid,kind,key_id,claim_ts) VALUES(?,'summer_pub_first',?,?)",(uid,level,int(time.time())))
    return rewards


def changed(ctx,uid):bus.emit_required(Events.SUMMER_PUB_PROGRESS,ctx,uid)


def finish_battle(ctx,uid,dest,activity,result):
    require(type(result) is int and result in (1,2,3),'餐厅战斗结果非法')
    level=REAL.get(dest,dest);real=COMBAT.get(level,{}).get('stages',[0])[0]
    battle_entry(ctx.db,uid,dest,activity,story=bool(CFG['battles'].get(str(real),{}).get('tag')==2))
    with atomic(ctx):
        if result!=1:return [],[]
        rec=read(ctx.db,uid);was=passed(rec,level)
        if not was:rec['battle_stages'].append(level);save(ctx.db,uid,rec)
        rewards=first_rewards(ctx,uid,level,was);changed(ctx,uid)
        from operations import _refresh_frames
        return _refresh_frames(ctx,uid,strict=True),rewards


def project_tasks(db,uid):
    marks=','.join('?' for _ in LIMITED_TASKS)
    rows=db.query(f'''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND (c.condition BETWEEN 350001 AND 350005 OR c.task_id IN ({marks}))
        AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0 AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=t.uid AND l.key_id=t.task_id AND l.kind IN ('task','illustrated_task'))''',(uid,*LIMITED_TASKS,int(time.time())))
    rows=visible_tasks(db,uid,rows)
    if not rows:return []
    rec=read(db,uid);changes=[];counts={}
    for r in sorted(rows,key=lambda r:r['condition']==501):
        try:params=json.loads(r['additional_parameter'])
        except (ValueError,TypeError):continue
        if not isinstance(params,list) or any(type(i) is not int for i in params):continue
        condition=r['condition'];need=int(r['need'] or 0)
        if need<=0:continue
        if condition==350001 and len(params)==1 and params[0] in LEVELS:value=int(passed(rec,params[0]))
        elif condition==350002:
            value=int(params[0] in rec['stage_list']) if len(params)==1 and params[0] in BALLS and BALLS[params[0]]['type']==2 else sum(BALLS[s]['type']==2 for s in rec['stage_list']) if not params else 0
        elif condition==350003 and params==[3]:
            value=sum(h['type']==3 and h['recruit_level'] in LEVELS and passed(rec,h['recruit_level']) for h in CFG['heroes'].values())
        elif condition==350005 and not params:value=len(rec['cook_stages'])
        elif condition==501 and params and r['task_id'] in LIMITED_TASKS:
            key=tuple(params)
            if key not in counts:
                ph=','.join('?' for _ in params)
                counts[key]=db.query(f'SELECT COUNT(*) n FROM task t JOIN task_cfg c ON c.task_id=t.task_id WHERE t.uid=? AND t.task_id IN ({ph}) AND (t.progress>=c.need OR t.claimed_ts>0 OR t.complete_flag=1)',(uid,*params))[0]['n']
            value=counts[key]
        else:continue
        progress=min(need,value)
        if progress>int(r['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(progress,uid,r['task_id']))
            changes.append(dict(id=r['task_id'],progress=progress,complete_flag=0))
    return changes


def notify_tasks(ctx,uid):
    changes=project_tasks(ctx.db,uid)
    if changes:
        from middleware import DownFrame
        packet=ctx.codec_encode('sc_28007',dict(progress_list=changes))
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'餐厅任务差量为空')
        ctx.append_frame(DownFrame(28007,bytes(packet)))


def restore_for_login(db,uid):
    with savepoint_transaction(db):return project_tasks(db,uid)


class PubOperation(Operation):
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        require(type(data.get('activity_id')) is int and data['activity_id']==ACTIVITY,'餐厅活动不匹配')
        rec=read(ctx.db,self.uid)
        if self.cmd==89002:
            level=data.get('stage_id');sub=data.get('sub_stage_id');result=data.get('result',0);ills=data.get('illustrated',[])
            require(type(level) is int and level in LEVELS and LEVELS[level]['type']==2,'未知弹珠关卡')
            cfg=LEVELS[level]
            require(type(sub) is int and sub in cfg['stages'],'弹珠子关卡不属于当前关卡')
            require(type(result) is int and result in (1,2),'弹珠结果非法')
            require(unlocked(rec,level),'弹珠前置关卡未完成')
            require(sub==cfg['stages'][0] or cfg['stages'][0] in rec['stage_list'],'困难弹珠需先完成普通难度')
            require(isinstance(ills,list) and all(type(i) is int and i in BALLS[sub]['illustrations'] for i in ills),'弹珠图鉴不属于该关卡')
            if result==1:
                was=passed(rec,level)
                if sub not in rec['stage_list']:rec['stage_list'].append(sub)
                for i in ills:rec['illustrated'].setdefault(str(i),1)
                save(ctx.db,self.uid,rec)
                if sub==cfg['stages'][0]:first_rewards(ctx,self.uid,level,was)
                changed(ctx,self.uid)
        elif self.cmd==89004:
            ill=data.get('ill_id')
            require(type(ill) is int and str(ill) in rec['illustrated'],'未获得该餐厅图鉴')
            if rec['illustrated'][str(ill)]!=2:rec['illustrated'][str(ill)]=2;save(ctx.db,self.uid,rec)
        elif self.cmd==89006:
            dish=data.get('stage_id');state=data.get('stage')
            require(type(dish) is int and dish in COOKS and type(state) is int and state in (1,2),'料理或状态非法')
            require(cook_unlocked(rec,dish),'料理前置尚未完成')
            old=rec['cook_stages'].get(str(dish),0)
            require(state>=old,'料理状态不可退回；剧情已完成')
            if state>old:rec['cook_stages'][str(dish)]=state;save(ctx.db,self.uid,rec)
            changed(ctx,self.uid)
        else:
            rec['time_state']=3-rec['time_state'];save(ctx.db,self.uid,rec)
        return dict(result=0)

    def respond(self,result,data,ctx):
        from middleware import DownFrame
        from operations import _refresh_frames
        packet=ctx.codec_encode('sc_'+str(self.sc),result)
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'餐厅回执为空')
        return _refresh_frames(ctx,self.uid,strict=True)+[DownFrame(self.sc,bytes(packet))]


@operation
class SummerPubPinBallOp(PubOperation):cmd,sc=89002,89003
@operation
class SummerPubViewIlluOp(PubOperation):cmd,sc=89004,89005
@operation
class SummerPubCookOp(PubOperation):cmd,sc=89006,89007
@operation
class SummerPubTimeStateOp(PubOperation):cmd,sc=89008,89009
