"""Osiris study and sandbox progress, native deltas and task projection."""
import json
from pathlib import Path
import time
from account_db import savepoint_transaction
from event_bus import bus, Events
from hodur_service import atomic, require

CFG=json.loads(Path(__file__).with_name('osiris_contract.json').read_text('utf-8'))
ACTIVITY=CFG['activity_id']
STAGES={int(k):v for k,v in CFG['stages'].items()}
CHAPTERS={int(k):v for k,v in CFG['chapters'].items()}
NORMAL={i:s for i,s in STAGES.items() if not s['rank_activity']}
HEROES={int(k):v for k,v in CFG['heroes'].items()}
TRIALS={v['charactor_id']:i for i,v in HEROES.items()}
LIMITED_TASKS=set(CFG['limited_tasks'])


def limited_context(rows):
    now=int(time.time())
    return any(int(r['activity_id'])==CFG['parent_activity'] and int(r.get('state') or 0)==1
               and int(r.get('start_time') or 0)<=now<int(r.get('stop_time') or 0) for r in rows)


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
    if LIMITED_TASKS.intersection(map(int,ids)):require(limited(db,uid),'战术研习常驻模式不可领取限时奖励')


def read(db,uid):
    rows=db.query('SELECT stage_id,score,settle FROM osiris_stage_progress WHERE uid=?',(uid,))
    out={}
    for r in rows:
        sid=r['stage_id'];duration=r['score'];point=r['settle']
        if type(sid) is not int or sid not in STAGES:continue
        if type(duration) is not int or not 0<=duration<=0xffffffff:continue
        if type(point) is not int or not 0<=point<=0xffffffff:continue
        out[sid]=dict(stage_id=sid,pass_time=duration,point=point if STAGES[sid]['rank_activity'] else 0)
    return out


def chapters(records):
    return [cid for cid,c in CHAPTERS.items() if c['type']==1 and all(s in records for s in c['stages'])]


def payload(db,uid):
    records=read(db,uid)
    return dict(pass_stages=[r for sid,r in sorted(records.items())],pass_chapters=chapters(records))


def sync(db,uid):
    records=read(db,uid);count=len(NORMAL.keys()&records.keys());now=int(time.time())
    for chapter in chapters(records):
        db.execute('INSERT OR IGNORE INTO osiris_chapter_progress(uid,chapter_no,value,update_ts) VALUES(?,?,?,?)',(uid,chapter,chapter,now))
    rows=db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=?',(uid,ACTIVITY))
    if (rows and rows[0]['point']!=count) or (not rows and count):
        db.execute('INSERT INTO activity_point(uid,activity_id,point,update_ts) VALUES(?,?,?,?) '
                   'ON CONFLICT(uid,activity_id) DO UPDATE SET point=excluded.point,update_ts=excluded.update_ts',(uid,ACTIVITY,count,now))
    return count


def point_payload(db,uid):
    from minigame_point_service import CATALOG
    count=sync(db,uid)
    ids=[r['reward_id'] for r in db.query('SELECT reward_id FROM activity_point_reward WHERE uid=? AND activity_id=? ORDER BY reward_id',(uid,ACTIVITY))
         if CATALOG.get(str(r['reward_id']),{}).get('activity_id')==ACTIVITY]
    return dict(activity_id=ACTIVITY,point=count,reward_list=ids)


def entry(db,uid,stage,activity,heroes=None):
    require(type(stage) is int and stage in STAGES and type(activity) is int and activity==ACTIVITY,'未知战术研习关卡或活动')
    cfg=STAGES[stage];records=read(db,uid)
    require(not cfg['pre_stage'] or cfg['pre_stage'] in records,'战术研习前置记录尚未完成')
    if cfg['rank_activity']:require(all(s in records for s in NORMAL),'沙盘模拟需完成全部战术学习')
    if heroes is not None:
        require(isinstance(heroes,list) and 1<=len(heroes)<=3,'研习队伍需一名队长和至多两名支援')
        unlocked={i for i,h in HEROES.items() if h['initial_unlock']}|{STAGES[s]['unlock_charactor'] for s in records}
        actual=[]
        for pos,h in enumerate(heroes):
            ref=h.get('hero_id');kind=h.get('hero_type')
            require(type(ref) is int and type(kind) is int and kind in (1,2),'研习角色引用不合法')
            hid=TRIALS.get(ref) if kind==2 else ref
            require(hid in HEROES and hid in unlocked,'该研习角色尚未解锁')
            require(HEROES[hid]['charactor_type']==(1 if pos==0 else 2),'研习队长/支援位置不匹配')
            if kind==1:
                require(bool(db.query('SELECT 1 FROM hero WHERE uid=? AND id=? AND unlock=1',(uid,hid))),'未拥有该研习修正者')
            actual.append(hid)
        require(len(actual)==len(set(actual)),'研习角色重复出战')
    return cfg


def score(stage,milliseconds):
    if not STAGES[stage]['rank_activity']:return 0
    cfg=CFG['scoring'];bonus=max(0,cfg['base_time']-milliseconds)*cfg['base_factor'] if milliseconds>0 else 0
    return min(0xffffffff,cfg['base_point']+bonus)


def extra(db,uid):
    row=db.get('users',uid);require(row is not None,'账号不存在')
    data=json.loads(row.get('extra') or '{}');require(isinstance(data,dict),'账号扩展数据损坏')
    state=data.get('osiris_study_v1',{})
    require(isinstance(state,dict),'研习任务事实损坏')
    flags=state.get('mechanic_tasks',[])
    require(isinstance(flags,list) and all(type(i) is int for i in flags),'研习机制任务事实损坏')
    return data,state,set(flags)


def remember_mechanics(db,uid,stage,report):
    if not STAGES[stage]['rank_activity'] or not limited(db,uid):return
    ids=report.get('record_data_ids',[]);values=report.get('record_data_numbers',[])
    if not isinstance(ids,list) or not isinstance(values,list) or len(ids)!=len(values):return
    if any(type(i) is not int or not 0<=i<=0x7fffffff for i in ids+values) or len(ids)!=len(set(ids)):return
    counters=dict(zip(ids,values));data,state,flags=extra(db,uid);before=set(flags)
    rows=db.query('SELECT task_id,condition,additional_parameter FROM task_cfg WHERE condition BETWEEN 340004 AND 340009')
    for r in rows:
        try:params=json.loads(r['additional_parameter'])
        except (TypeError,ValueError):continue
        if not isinstance(params,list) or len(params)!=2 or any(type(i) is not int for i in params):continue
        rid=CFG['mechanic_records'].get(str(r['condition']))
        if params[0]==stage and params[1]>0 and counters.get(rid,0)>=params[1]:flags.add(r['task_id'])
    if flags!=before:
        state['mechanic_tasks']=sorted(flags);data['osiris_study_v1']=state
        db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(data,ensure_ascii=False),uid))


def project_tasks(db,uid):
    sync(db,uid);marks=','.join('?' for _ in LIMITED_TASKS)
    rows=db.query(f'''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND (c.condition BETWEEN 340001 AND 340009 OR t.task_id IN ({marks}))
        AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0 AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=t.uid AND l.key_id=t.task_id AND l.kind IN ('task','illustrated_task'))''',(uid,*LIMITED_TASKS,int(time.time())))
    rows=visible_tasks(db,uid,rows)
    if not rows:return []
    records=read(db,uid);flags=None;counts={};changes=[]
    for r in sorted(rows,key=lambda r:r['condition']==501):
        try:params=json.loads(r['additional_parameter'])
        except (ValueError,TypeError):continue
        if not isinstance(params,list) or any(type(i) is not int for i in params):continue
        condition=r['condition'];need=int(r['need'] or 0)
        if need<=0:continue
        if condition==340001 and len(params)==1:value=int(params[0] in records)
        elif condition==340002 and len(params)==2:value=int(records.get(params[0],{}).get('point',-1)>=params[1])
        elif 340004<=condition<=340009:
            if flags is None:flags=extra(db,uid)[2]
            value=int(r['task_id'] in flags)
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
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'研习任务差量为空')
        ctx.append_frame(DownFrame(28007,bytes(packet)))


def restore_for_login(db,uid):
    with savepoint_transaction(db):return project_tasks(db,uid)


def settle(ctx,uid,stage,activity,result,report):
    require(type(result) is int and result in (1,2,3),'研习战斗结果非法')
    with atomic(ctx):
        entry(ctx.db,uid,stage,activity)
        if result!=1:return []
        milliseconds=report.get('battle_time',0)
        require(type(milliseconds) is int and 0<=milliseconds<=0xffffffff,'研习战斗耗时非法')
        records=read(ctx.db,uid);first=stage not in records;oldchapters=set(chapters(records));oldcount=len(NORMAL.keys()&records.keys())
        current=dict(stage_id=stage,pass_time=milliseconds,point=score(stage,milliseconds))
        if first or current['point']>=records[stage]['point']:
            ctx.db.execute('INSERT INTO osiris_stage_progress(uid,stage_id,score,settle,update_ts) VALUES(?,?,?,?,?) '
                'ON CONFLICT(uid,stage_id) DO UPDATE SET score=excluded.score,settle=excluded.settle,update_ts=excluded.update_ts',
                (uid,stage,milliseconds,current['point'],int(time.time())))
        remember_mechanics(ctx.db,uid,stage,report)
        bus.emit_required(Events.OSIRIS_STUDY_PROGRESS,ctx,uid)
        count=sync(ctx.db,uid);newchapters=sorted(set(chapters(read(ctx.db,uid)))-oldchapters)
        from operations import _refresh_frames
        from middleware import DownFrame
        frames=_refresh_frames(ctx,uid,strict=True)
        def push(sc,obj):
            packet=ctx.codec_encode('sc_'+str(sc),obj)
            require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'研习必要差量为空：'+str(sc))
            frames.append(DownFrame(sc,bytes(packet)))
        if first:push(68173,dict(pass_stages=[current]))
        if newchapters:push(68175,dict(pass_chapters=newchapters))
        push(68177,dict(pass_stage=current))
        if count!=oldcount:
            push(60105,dict(score_info_list=[dict(key=ACTIVITY,score=count)]))
            # Osiris' own record page reads 60201, not ActivityPointData's 60105.
            push(60201,point_payload(ctx.db,uid))
        return frames
