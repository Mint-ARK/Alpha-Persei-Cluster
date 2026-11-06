"""SP Hero native stages, daily reward quota and durable per-stage facts."""
from collections import Counter
from contextlib import contextmanager
import json
import random
import time
import uuid

from event_bus import bus,Events
from hodur_service import require,atomic
from inventory_service import InventoryService
import sphero_schedule_service as schedule

CFG=schedule.CFG
STAGES={int(i):v for i,v in CFG['stages'].items()}
STORY={i:v for i,v in STAGES.items() if v['sub_type']==1}
TRAIN={i:v for i,v in STAGES.items() if 2<=v['sub_type']<=4}
BOSSES={i:v for i,v in STAGES.items() if v['sub_type']==5}
POOL={int(i):v for i,v in CFG['boss_pool'].items()}
ACTIVITY=242841
KIND='sphero_battle_v1'


def opened(db,uid,activity):
    from generator import get_activity_list
    now=int(time.time())
    return any(a['activity_id']==activity and int(a.get('state') or 0)==1 and int(a.get('start_time') or 0)<=now<int(a.get('stop_time') or 0)
               for a in get_activity_list(db,uid))


def read(db,uid):
    require(db.get('users',uid) is not None,'账号不存在')
    day=schedule.get_daily_5am_ts(int(time.time()))
    rows=db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',(uid,KIND,str(ACTIVITY)))
    state=json.loads(rows[0]['value_json']) if rows else dict(version=1,day=day,rewarded=0,boss_fights=0,boss_today=[],best={},pool_draws=[],latest=None)
    # Retain old boss receipts and the one score the old single-row schema knew.
    if not rows:
        legacy=db.query('SELECT boss_stage_id,score,achieved_list FROM sp_hero_boss_progress WHERE uid=? AND activity_id=242851',(uid,))
        if legacy:
            r=legacy[0]
            if r['boss_stage_id'] in BOSSES and r['score']>=0:state['best'][str(r['boss_stage_id'])]=r['score']
            draws=json.loads(r['achieved_list'] or '[]')
            require(isinstance(draws,list),'旧Boss奖励记录损坏')
            for i in draws:
                if type(i) is int and i in POOL and state['pool_draws'].count(i)<POOL[i]['total']:state['pool_draws'].append(i)
    require(isinstance(state,dict) and state.get('version')==1 and schedule.uint(state.get('day')),'挑战记录损坏')
    require(schedule.uint(state.get('rewarded')) and state['rewarded']<=CFG['settings']['activity_hero_challenge_max_num'][0]
            and schedule.uint(state.get('boss_fights')),'挑战次数损坏')
    require(isinstance(state.get('best'),dict) and all(str(i) in {str(s) for s in BOSSES} and schedule.uint(v) for i,v in state['best'].items()),'Boss分数损坏')
    require(isinstance(state.get('boss_today'),list) and all(type(i) is int and i in BOSSES for i in state['boss_today']),'Boss今日记录损坏')
    require(isinstance(state.get('pool_draws'),list) and all(type(i) is int and i in POOL for i in state['pool_draws'])
            and all(state['pool_draws'].count(i)<=v['total'] for i,v in POOL.items()),'Boss奖池记录损坏')
    require(state.get('latest') is None or isinstance(state['latest'],dict),'挑战身份损坏')
    if state['day']!=day:state.update(day=day,rewarded=0,boss_fights=0,boss_today=[],latest=None)
    return state


def save(db,uid,state):
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
        'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
        (uid,KIND,str(ACTIVITY),json.dumps(state)))


def stages(db,uid):
    rows=db.query('SELECT * FROM sp_hero_challenge_stage WHERE uid=? AND activity_id=?',(uid,ACTIVITY))
    stories={r['stage_id'] for r in rows if r['stage_id'] in STORY and r['stage_kind']=='unlock'}
    train={i:r for r in rows if (i:=r['stage_id']) in TRAIN and r['stage_kind']=='progress' and r['group_type']==TRAIN[i]['sub_type']}
    values={g:min(3000,max([r['value'] for i,r in train.items() if TRAIN[i]['sub_type']==g]+[0])) for g in (2,3,4)}
    return stories,train,values


def train_payload(db,uid):
    _,records,values=stages(db,uid)
    return [dict(type=g,value=values[g],passed_level=sorted(i for i in records if TRAIN[i]['sub_type']==g)) for g in (2,3,4)]


def boss_payload(db,uid):
    state=read(db,uid)
    return dict(activity_id=242851,stage_id_list=sorted(set(state['boss_today'])),fight_cnt=state['boss_fights'],
        score_info_list=[dict(stage_id=int(i),score=s) for i,s in sorted(state['best'].items())],
        is_start=opened(db,uid,242851),got_award_cfg_list=state['pool_draws'])


def history_entries(db,uid):
    stories,_,_=stages(db,uid);state=read(db,uid)
    return [dict(id=112001,data=int(set(STORY)<=stories)),
            dict(id=112031,data=state['best'].get('5170221',0)),dict(id=112032,data=state['best'].get('5170222',0))]


def training_gain(stage,value):
    group=STAGES[stage]['sub_type'];order=sorted(i for i,v in TRAIN.items() if v['sub_type']==group).index(stage)+1
    levels=CFG['settings']['activity_hero_challenge_train_num_'+str(order)]
    return next((n for threshold,n in levels if value<=threshold),levels[-1][1])


def entry(db,uid,info):
    stage=info['dest'];kind=info['type']
    if kind!=66 and stage not in STAGES:return None
    require(kind==66 and stage in STAGES,'薇儿活动关卡或战斗类型不匹配')
    require(info['activity_id']==ACTIVITY,'薇儿活动归属不匹配')
    require(info['battle_times']==1,'薇儿活动不支持多倍结算或扫荡')
    require(opened(db,uid,ACTIVITY),'薇儿活动未开放')
    cfg=STAGES[stage];stories,train,_=stages(db,uid)
    for previous,v in STAGES.items():
        if isinstance(v['next_unlock_id_list'],list) and stage in v['next_unlock_id_list']:
            require(previous in stories or previous in train,'请先通关前置关卡')
    if stage not in STORY:require(set(STORY)<=stories,'请先通关全部回响启示')
    if stage in BOSSES:require(opened(db,uid,242851),'侵蚀抵抗未开放')
    if stage in STORY and stage not in stories:
        require(any(not s['is_finish'] and CFG['schedules'][str(s['schedule_id'])]['server_type']==1 for s in schedule.read(db,uid)['schedules']),
                '请先安排回响启示日程')
    team=info.get('hero_details')
    expected=[dict(hero_id=trial,hero_type=2) for hero,trial in cfg['hero_list'] if hero and trial]
    require(team==expected,'请使用关卡配置的试用队伍')
    state=read(db,uid)
    return dict(day=state['day'],stage=stage,token=uuid.uuid4().hex)


@contextmanager
def publication(ctx,uid,bid,context):
    if not context:
        yield
        return
    try:
        with atomic(ctx):
            state=read(ctx.db,uid);require(state['day']==context['day'],'挑战初始化时已跨日，请重新进入')
            state['latest']=dict(context,bid=bid);save(ctx.db,uid,state)
            yield
    except BaseException:
        import middleware
        with middleware._BATTLE_LOCK:
            session=middleware._BATTLE_SESSIONS.get(bid)
            if session and session.get('uid')==uid and session.get('sphero_context')==context:middleware._BATTLE_SESSIONS.pop(bid,None)
        raise


def check(db,uid,bid,session):
    state=read(db,uid);context=session.get('sphero_context')
    require(session.get('uid')==uid and context and state['latest']==dict(context,bid=bid)
            and context['day']==state['day'] and context['stage']==session['dest'],'薇儿挑战已过期，请重新进入')
    require(session['stage_type']==66 and session['activity_id']==ACTIVITY,'薇儿结算身份不匹配')
    require(opened(db,uid,ACTIVITY) and (session['dest'] not in BOSSES or opened(db,uid,242851)),'薇儿活动已关闭')
    return state


def project_tasks(db,uid):
    _,_,values=stages(db,uid);state=read(db,uid);changes=[]
    for row in db.query('''SELECT t.task_id,t.progress,c.need FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND c.condition IN (160002,160003,160007) AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
        AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=t.uid AND l.key_id=t.task_id AND l.kind IN ('task','illustrated_task'))''',(uid,int(time.time()))):
        cfg=CFG['battle_tasks'].get(str(row['task_id']))
        if not cfg:continue
        params=cfg['additional_parameter']
        if cfg['condition']==160002:value=values.get(params[0],0)
        elif cfg['condition']==160003:value=int(state['best'].get(str(params[0]),0)>=params[1])
        else:value=int(max(values.values())>=params[0])
        value=min(row['need'],value)
        if value>int(row['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(value,uid,row['task_id']))
            changes.append(dict(id=row['task_id'],progress=value,complete_flag=0))
    return changes


@bus.subscribe(Events.SPHERO_BATTLE_PROGRESS)
def notify(ctx,uid,**kwargs):
    changes=project_tasks(ctx.db,uid)
    if changes:ctx.append_frame(schedule.frame(ctx,28007,dict(progress_list=changes)))


def rewards(stage,first,eligible):
    drop=CFG['drops'][str(STAGES[stage]['drop_lib_id'])]
    raw=drop['base_drop'] if first and isinstance(drop['base_drop'],list) else []
    if not raw and eligible:
        # All six current libraries are deterministic (one 100% entry).
        raw=drop['weight_drop'] if isinstance(drop['weight_drop'],list) else []
    return [(i,n) for i,n,prob in raw if prob==100]


def settle(ctx,uid,bid,session,result,report):
    require(type(result) is int and result in (1,2,3),'薇儿战斗结果非法')
    require(isinstance(report,dict) and schedule.uint(report.get('battle_time',0)),'薇儿战报耗时非法')
    with atomic(ctx):
        state=check(ctx.db,uid,bid,session);stage=session['dest'];group=STAGES[stage]['sub_type']
        if result!=1:
            state['latest']=None;save(ctx.db,uid,state)
            return [],[]
        stories,train,values=stages(ctx.db,uid);first=stage in STORY and stage not in stories
        eligible=stage not in STORY and state['rewarded']<CFG['settings']['activity_hero_challenge_max_num'][0]
        train_changed=False;now=int(time.time())
        if stage in STORY:
            ctx.db.execute("INSERT OR IGNORE INTO sp_hero_challenge_stage(uid,activity_id,stage_id,stage_kind,group_type,value,update_ts) VALUES(?,?,?,'unlock',0,0,?)",(uid,ACTIVITY,stage,now))
        elif stage in TRAIN:
            value=min(3000,values[group]+training_gain(stage,values[group])) if eligible else values[group]
            train_changed=stage not in train or value!=values[group]
            ctx.db.execute("INSERT INTO sp_hero_challenge_stage(uid,activity_id,stage_id,stage_kind,group_type,value,update_ts) VALUES(?,?,?,'progress',?,?,?) "
                'ON CONFLICT(uid,activity_id,stage_id) DO UPDATE SET value=excluded.value,update_ts=excluded.update_ts',(uid,ACTIVITY,stage,group,value,now))
            ctx.db.execute("UPDATE sp_hero_challenge_stage SET value=?,update_ts=? WHERE uid=? AND activity_id=? AND group_type=? AND stage_kind='progress'",(value,now,uid,ACTIVITY,group))
        else:
            # User policy: every successful boss clear receives the full score.
            full=CFG['settings']['activity_hero_challenge_boss_basescore'][0]+CFG['settings']['activity_hero_challenge_boss_timescore'][1]
            state['best'][str(stage)]=max(state['best'].get(str(stage),0),full)
            state['boss_fights']+=1;state['boss_today']=sorted(set(state['boss_today'])|{stage})
        items=rewards(stage,first,eligible)
        if stage in BOSSES and eligible:
            remaining=[i for i,v in POOL.items() for _ in range(v['total']-state['pool_draws'].count(i))]
            if remaining:
                choice=random.choice(remaining);state['pool_draws'].append(choice);items.append(tuple(POOL[choice]['reward']))
        if eligible:state['rewarded']+=1
        state['latest']=None;save(ctx.db,uid,state)
        sc_state=schedule.read(ctx.db,uid)
        wire_items=schedule.give(ctx,uid,sc_state,items) if items else []
        changed=schedule.finish_in_state(sc_state,group) if stage not in STORY or first else None
        schedule.save(ctx.db,uid,sc_state)
        bus.emit_required(Events.SPHERO_BATTLE_PROGRESS,ctx,uid)
        from operations import _refresh_frames
        frames=_refresh_frames(ctx,uid,strict=True)
        if first or stage in BOSSES:frames.append(schedule.frame(ctx,51001,dict(data_list=history_entries(ctx.db,uid))))
        # No isolated challenge_times packet exists.83016 is required when the
        # daily reward count changes; its train list also supplies the new values.
        if eligible:frames.append(schedule.frame(ctx,83016,schedule.snapshot(ctx.db,uid)))
        elif train_changed:frames.append(schedule.frame(ctx,83015,dict(activity_id=ACTIVITY,train_list=train_payload(ctx.db,uid))))
        if stage in BOSSES:frames.append(schedule.frame(ctx,83020,boss_payload(ctx.db,uid)))
        if changed:frames.append(schedule.frame(ctx,83017,dict(activity_id=ACTIVITY,schedule_list=[changed])))
        return frames,[(r['id'],r['num']) for r in wire_items]
