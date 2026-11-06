"""Native local BBQ/puzzle reports, configured caps and SP Hero reward closure."""
from collections import Counter
import json
import time

from core import Operation,operation
from event_bus import bus,Events
from hodur_service import require
from inventory_service import InventoryService
import sphero_schedule_service as schedule
from sphero_battle_service import opened

CFG=schedule.CFG
PUZZLES={int(i):v for i,v in CFG['puzzles'].items()}
BBQ={int(i):v for i,v in CFG['barbecue'].items()}
TASKS={int(i):v for i,v in CFG['own_tasks'].items()}
KIND='sphero_leisure_v1'
ACTIVITY=242841


def read(db,uid):
    require(db.get('users',uid) is not None,'账号不存在')
    rows=db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',(uid,KIND,str(ACTIVITY)))
    day=schedule.get_daily_5am_ts(int(time.time()))
    state=json.loads(rows[0]['value_json']) if rows else dict(version=1,day=day,coins=0,tickets=0,bbq_count=0,wreath=0,times={})
    require(isinstance(state,dict) and state.get('version')==1 and schedule.uint(state.get('day')),'休闲玩法记录损坏')
    require(all(schedule.uint(state.get(k)) for k in ('coins','tickets','bbq_count','wreath')),'休闲计数记录损坏')
    require(state['coins']<=CFG['settings']['activity_hero_challenge_barbecue_coin_limit'][0]
            and state['tickets']<=CFG['settings']['activity_hero_challenge_barbecue_ticket_limit'][0],'烧烤日上限记录损坏')
    require(isinstance(state.get('times'),dict) and all(str(i) in {str(k) for k in PUZZLES} and schedule.uint(v) for i,v in state['times'].items()),'解谜用时记录损坏')
    if state['day']!=day:state.update(day=day,coins=0,tickets=0)
    return state


def save(db,uid,state):
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
        'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
        (uid,KIND,str(ACTIVITY),json.dumps(state)))


def cleared(db,uid):
    rows=db.query('SELECT cleared_stage_list FROM sp_hero_puzzle_progress WHERE uid=? AND activity_id=242871',(uid,))
    values=json.loads(rows[0]['cleared_stage_list'] or '[]') if rows else []
    require(isinstance(values,list) and all(type(i) is int and i in PUZZLES for i in values),'解谜通关记录损坏')
    return sorted(set(values))


def puzzle_payload(db,uid):return dict(stage_completed_list=cleared(db,uid))


def barbecue_payload(db,uid):
    state=read(db,uid)
    return dict(reward_record=state['coins'],ticket_record=state['tickets'])


def claimed(db,uid):
    ids=set(TASKS);ph=','.join('?' for _ in ids)
    return {int(r['task_id']) for r in db.query(f'''SELECT task_id FROM task WHERE uid=? AND task_id IN ({ph})
        AND (COALESCE(complete_flag,0)>0 OR COALESCE(claimed_ts,0)>0)
        UNION SELECT key_id FROM claim_ledger WHERE uid=? AND key_id IN ({ph}) AND kind IN ('task','illustrated_task')''',
        (uid,*ids,uid,*ids))}


def wreath_total(db,uid,state):
    rewards=sum(n for tid in claimed(db,uid) for i,n in TASKS[tid]['reward'] if i==53101)
    return max(state['wreath'],rewards,InventoryService.get_item_balance(db,uid,53101)[1])


def history_entries(db,uid):
    finished=set(cleared(db,uid));got=claimed(db,uid)
    purchases={r['goods_id']:r['n'] for r in db.query('SELECT goods_id,SUM(buy_times) n FROM store_purchase WHERE uid=? AND shop_id=216 GROUP BY goods_id',(uid,))}
    shop_done=all(purchases.get(int(i),0)>=g['limit_num'] for i,g in CFG['shop'].items() if int(i)!=216000002)
    values={112011:int(set(PUZZLES)<=finished),112041:int(shop_done)}
    for cid,owner in ((112051,240045),(112052,240046),(112053,242841)):
        values[cid]=int(all(tid in got for tid,t in TASKS.items() if t['activity_id']==owner))
    return [dict(id=i,data=v) for i,v in values.items()]


def project_tasks(db,uid):
    state=read(db,uid);finished=set(cleared(db,uid));sc=schedule.read(db,uid)
    coins=schedule.earned(db,uid,sc);wreath=wreath_total(db,uid,state);changes=[]
    for row in db.query('''SELECT t.task_id,t.progress,c.need FROM task t JOIN task_cfg c ON c.task_id=t.task_id WHERE t.uid=?
        AND c.condition IN (302,160001,160004,160006,160008) AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
        AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=t.uid AND l.key_id=t.task_id AND l.kind IN ('task','illustrated_task'))''',(uid,int(time.time()))):
        cfg=TASKS.get(row['task_id'])
        if not cfg:continue
        cond=cfg['condition'];params=cfg['additional_parameter']
        if cond==302:value=coins if params==[53100] else (wreath if params==[53101] else 0)
        elif cond==160001:value=sc.get('entrust_completed',0)
        elif cond in (160006,160008):value=state['bbq_count']
        elif cond==160004:value=int(set(PUZZLES)<=finished) if not params else int(set(params)<=finished)
        else:continue
        value=min(row['need'],value)
        if value>int(row['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(value,uid,row['task_id']))
            changes.append(dict(id=row['task_id'],progress=value,complete_flag=0))
    return changes


@bus.subscribe(Events.SPHERO_LEISURE_PROGRESS)
def notify(ctx,uid,**kwargs):
    changes=project_tasks(ctx.db,uid)
    if changes:ctx.append_frame(schedule.frame(ctx,28007,dict(progress_list=changes)))
    changed=[]
    for row in history_entries(ctx.db,uid):
        prior=ctx.db.query("SELECT data FROM history_cmd WHERE uid=? AND kind='history' AND key_id=?",(uid,str(row['id'])))
        if prior and str(row['data'])==str(prior[0]['data']):continue
        ctx.db.execute("INSERT INTO history_cmd(uid,kind,key_id,data,update_ts) VALUES(?,'history',?,?,?) "
            'ON CONFLICT(uid,kind,key_id) DO UPDATE SET data=excluded.data,update_ts=excluded.update_ts',
            (uid,str(row['id']),str(row['data']),int(time.time())))
        changed.append(row)
    if changed:ctx.append_frame(schedule.frame(ctx,51001,dict(data_list=changed)))


def after_task_claim(ctx,uid,task_ids):
    if any(int(tid) in TASKS for tid in task_ids):bus.emit_required(Events.SPHERO_LEISURE_PROGRESS,ctx,uid)


def record_grants(ctx,uid,coins=0,wreath=0):
    # These two event items can also arrive through mail or the panel. Inventory
    # already changed the balance; never add the current grant twice.
    if coins:
        sc=schedule.read(ctx.db,uid)
        previous=max(sc['earned'],*(schedule.history_value(ctx.db,uid,cid) for cid in (112061,112062,112063)))
        sc['earned']=max(previous+coins,InventoryService.get_item_balance(ctx.db,uid,53100)[1]);schedule.save(ctx.db,uid,sc)
        for cid in (112061,112062,112063):
            ctx.db.execute("INSERT INTO history_cmd(uid,kind,key_id,data,update_ts) VALUES(?,'history',?,?,?) "
                'ON CONFLICT(uid,kind,key_id) DO UPDATE SET data=excluded.data,update_ts=excluded.update_ts',
                (uid,str(cid),str(sc['earned']),int(time.time())))
        ctx.append_frame(schedule.frame(ctx,51001,dict(data_list=[dict(id=i,data=sc['earned']) for i in (112061,112062,112063)])))
    if wreath:
        state=read(ctx.db,uid);state['wreath']=max(state['wreath']+wreath,InventoryService.get_item_balance(ctx.db,uid,53101)[1]);save(ctx.db,uid,state)
    from task_listener import _trigger_task_condition,_push_task_diff
    changes=[]
    for iid,n in ((53100,coins),(53101,wreath)):
        if n:changes.extend(_trigger_task_condition(ctx.db,uid,condition=302,delta=n,param=iid))
    _push_task_diff(ctx,uid,changes)
    bus.emit_required(Events.SPHERO_LEISURE_PROGRESS,ctx,uid)


@bus.subscribe(Events.SHOP_BUY)
def on_shop(ctx,uid,shop_id=0,**kwargs):
    if shop_id==216:bus.emit_required(Events.SPHERO_LEISURE_PROGRESS,ctx,uid)


def settle_barbecue(ctx,uid,data):
    stage=data.get('stage_id');grade=data.get('grade_id');hero=data.get('hero_id');result=data.get('result')
    require(type(stage) is int and stage in BBQ and type(grade) is int and 1<=grade<=5,'烧烤关卡或评级无效')
    require(type(hero) is int and hero==CFG['activity']['hero_id'],'烧烤修正者不匹配')
    require(type(result) is int and result in (1,2,3),'烧烤结果无效')
    require(opened(ctx.db,uid,ACTIVITY) and opened(ctx.db,uid,BBQ[stage]['activity_id']),'闲暇时刻未开放')
    state=read(ctx.db,uid);items=[]
    if result!=1:return dict(result=0,reward_list=[],reward_recode=state['coins'],ticket_recode=state['tickets'])
    if result==1:
        for field,key,limit in (('reward_list_coin','coins','activity_hero_challenge_barbecue_coin_limit'),('reward_list_ticket','tickets','activity_hero_challenge_barbecue_ticket_limit')):
            reward=BBQ[stage][field][grade-1]
            if reward:
                iid,num=reward;amount=min(num,CFG['settings'][limit][0]-state[key])
                if amount:items.append((iid,amount));state[key]+=amount
        state['bbq_count']+=1
    save(ctx.db,uid,state)
    sc=schedule.read(ctx.db,uid);rewards=schedule.give(ctx,uid,sc,items) if items else []
    changed=schedule.finish_in_state(sc,7) if result==1 else None;schedule.save(ctx.db,uid,sc)
    bus.emit_required(Events.SPHERO_LEISURE_PROGRESS,ctx,uid)
    if changed:ctx.append_frame(schedule.frame(ctx,83017,dict(activity_id=ACTIVITY,schedule_list=[changed])))
    return dict(result=0,reward_list=rewards,reward_recode=state['coins'],ticket_recode=state['tickets'])


def settle_puzzle(ctx,uid,data):
    stage=data.get('stage_id');result=data.get('result');auto=data.get('auto_completed');seconds=data.get('useseconds')
    require(type(stage) is int and stage in PUZZLES,'演绎行乐关卡无效')
    require(type(result) is int and result in (1,2,3) and type(auto) is int and auto in (0,1) and schedule.uint(seconds),'解谜结算参数无效')
    require(opened(ctx.db,uid,ACTIVITY) and opened(ctx.db,uid,PUZZLES[stage]['activity_id']),'演绎行乐未开放')
    finished=cleared(ctx.db,uid);pre=PUZZLES[stage]['pre_stage'];require(pre==0 or pre in finished,'请先完成前置解谜关卡')
    if result!=1:return dict(result=0,stage_id=stage,reward_list=[],finished_list=finished)
    state=read(ctx.db,uid);sc=schedule.read(ctx.db,uid);rewards=[];changed=None
    first=stage not in finished
    if result==1:
        if first:
            require(any(not s['is_finish'] and CFG['schedules'][str(s['schedule_id'])]['server_type']==6 for s in sc['schedules']),'请先安排演绎行乐日程')
            if auto:schedule.cost(ctx,uid,[(53100,CFG['settings']['activity_hero_challenge_color_cost'][0])])
            rewards=schedule.give(ctx,uid,sc,PUZZLES[stage]['reward']);finished.append(stage);finished.sort()
            ctx.db.execute('INSERT INTO sp_hero_puzzle_progress(uid,activity_id,cleared_stage_list,update_ts) VALUES(?,242871,?,?) '
                'ON CONFLICT(uid,activity_id) DO UPDATE SET cleared_stage_list=excluded.cleared_stage_list,update_ts=excluded.update_ts',
                (uid,json.dumps(finished),int(time.time())))
            changed=schedule.finish_in_state(sc,6)
        key=str(stage);state['times'][key]=min(seconds,state['times'].get(key,seconds))
        save(ctx.db,uid,state)
    schedule.save(ctx.db,uid,sc);bus.emit_required(Events.SPHERO_LEISURE_PROGRESS,ctx,uid)
    if changed:ctx.append_frame(schedule.frame(ctx,83017,dict(activity_id=ACTIVITY,schedule_list=[changed])))
    # OnClearLevelCallback replaces ColorPuzzleData from this ACK. No83021 echo.
    return dict(result=0,stage_id=stage,reward_list=rewards,finished_list=finished)


class LeisureOp(Operation):
    prepare_response_in_transaction=True

    def respond(self,result,data,ctx):
        from operations import _refresh_frames
        return _refresh_frames(ctx,self.uid,strict=True)+[schedule.frame(ctx,self.sc,result)]


@operation
class SPHeroPuzzleOp(LeisureOp):
    cmd,sc=83024,83025

    def apply(self,data,ctx):return settle_puzzle(ctx,self.uid,data)
