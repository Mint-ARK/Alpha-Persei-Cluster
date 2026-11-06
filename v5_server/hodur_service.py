"""Hodur ordinary exploration, task projection and native state snapshot."""
from contextlib import contextmanager
import json
from pathlib import Path
import time
import uuid

from account_db import savepoint_transaction
from core import OperationError
from event_bus import bus, Events
from operation_notifications import checkpoint_notifications, restore_notifications

CFG=json.loads(Path(__file__).with_name('hodur_contract.json').read_text('utf-8'))
ACTIVITY=CFG['activity_id']
CHAPTERS={int(k):v for k,v in CFG['chapters'].items()}
STAGES={int(k):v for k,v in CFG['stages'].items()}
NORMAL={i:s for i,s in STAGES.items() if CHAPTERS[s['chapter_id']]['chapter_type']==1}
EVENTS={int(k):v for k,v in CFG['events'].items()}
OPTIONS={int(k):v for k,v in CFG['options'].items()}
LIMITED_TASKS=set(CFG['limited_tasks'])
OPTION_EVENT={o:e for e,v in EVENTS.items() for o in v['options']}
EVENT_CHAPTER={STAGES[s]['event_lock']:STAGES[s]['chapter_id'] for s in NORMAL if STAGES[s]['event_lock']}


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
    if LIMITED_TASKS.intersection(map(int,ids)):require(limited(db,uid),'谧光叙录常驻模式不可领取限时活动奖励')


def decode(raw,kind):
    value=json.loads(raw) if isinstance(raw,str) and raw else (kind() if raw is None or raw=='' else raw)
    require(isinstance(value,kind),'谧光叙录存档格式错误')
    return value


def read(db,uid):
    rows={r['chapter_id']:r for r in db.query('SELECT * FROM hodur_progress WHERE uid=? AND activity_id=?',(uid,ACTIVITY))}
    out={}
    for chapter,cfg in CHAPTERS.items():
        if cfg['chapter_type']!=1:continue
        row=rows.get(chapter,{})
        stat=decode(row.get('stat_json',{}),dict)
        options=[];seen_events=set()
        for option in decode(row.get('option_id_list',[]),list):
            if type(option) is int and option in OPTIONS:
                event=OPTION_EVENT[option]
                if EVENT_CHAPTER[event]==chapter and event not in seen_events:
                    options.append(option);seen_events.add(event)
        clears={}
        for item in decode(row.get('stage_clear_json',[]),list):
            require(isinstance(item,dict),'谧光叙录通关记录格式错误')
            stage=item.get('boss_id',item.get('stage_id'))
            result=item.get('result',item.get('clear',1))
            if type(stage) is int and stage in cfg['stages'] and type(result) is int and result in (0,1):
                clears[stage]=max(clears.get(stage,0),result)
        heroes=decode(stat.get('heroes',[]),list)
        if 'heroes' not in stat and ('hero_id' in stat or 'a' in stat):
            heroes=[dict(hero_id=stat.get('hero_id',stat.get('a')),hero_blood=stat.get('hero_blood',stat.get('b',0)),
                         hero_max_blood=stat.get('hero_max_blood',stat.get('c',0)))]
        heroes=[h for h in heroes if isinstance(h,dict) and all(type(h.get(k)) is int for k in ('hero_id','hero_blood','hero_max_blood'))
            and h['hero_id']>0 and 0<=h['hero_blood']<=h['hero_max_blood']<=0xFFFFFFFF]
        ever={e for e in decode(stat.get('events_ever',[]),list) if type(e) is int and EVENT_CHAPTER.get(e)==chapter}|seen_events
        out[chapter]=dict(chapter_id=chapter,options=options,clears=clears,heroes=heroes,events_ever=sorted(ever),run_id=str(stat.get('run_id') or ''))
    return out


def save(db,uid,data):
    stat={k:data[k] for k in ('heroes','events_ever','run_id')};stat['version']=1
    db.execute('INSERT INTO hodur_progress(uid,activity_id,chapter_id,option_id_list,stage_clear_json,stat_json,update_ts) '
        'VALUES(?,?,?,?,?,?,?) ON CONFLICT(uid,activity_id,chapter_id) DO UPDATE SET option_id_list=excluded.option_id_list,'
        'stage_clear_json=excluded.stage_clear_json,stat_json=excluded.stat_json,update_ts=excluded.update_ts',
        (uid,ACTIVITY,data['chapter_id'],json.dumps(data['options']),json.dumps([dict(boss_id=s,result=v) for s,v in data['clears'].items()]),json.dumps(stat),int(time.time())))


def payload(db,uid):
    data=read(db,uid)
    from hodur_challenge import payload as challenge_payload
    return dict(normal_stage_info=[dict(normal_stage_id=c,normal_option_info=r['options'],normal_hero_stage_info=r['heroes'],
        normal_boss_id_list=[dict(boss_id=s,result=r['clears'][s]) for s in CHAPTERS[c]['stages'] if s in r['clears']]) for c,r in data.items()],challenge_stage_info=[challenge_payload(db,uid)])


def validate_chapter(activity,chapter,data):
    require(type(activity) is int and activity==ACTIVITY,'未知谧光叙录活动')
    require(type(chapter) is int and chapter in data,'未知谧光叙录普通章节')
    previous=CHAPTERS[chapter]['previous_chapter']
    while previous:
        require(all(s in data[previous]['clears'] for s in CHAPTERS[previous]['stages']),'谧光叙录前置章节尚未通关')
        previous=CHAPTERS[previous]['previous_chapter']


def entry(db,uid,stage,activity,team=None,opts=None):
    if stage in CHAPTERS[4]['stages']:
        from hodur_challenge import entry as challenge_entry
        return challenge_entry(db,uid,stage,activity,team,opts or [])
    require(type(stage) is int and stage in NORMAL,'未知谧光叙录普通关卡')
    data=read(db,uid);cfg=STAGES[stage];chapter=cfg['chapter_id'];validate_chapter(activity,chapter,data)
    row=data[chapter];previous=cfg['previous_stage'];event=cfg['event_lock']
    require(not previous or row['clears'].get(previous)==1,'谧光叙录本轮前置关卡未完成')
    require(not event or any(o in EVENTS[event]['options'] for o in row['options']),'谧光叙录前置事件尚未选择')
    token=dict(chapter_id=chapter,run_id=row['run_id'])
    if team is not None:
        from hodur_challenge import team_aliases
        token.update(team=list(team),aliases=team_aliases(db,uid,team))
    return token


def check_battle(db,uid,session):
    if (session.get('hodur_context') or {}).get('chapter_id')==4:
        from hodur_challenge import check_battle as challenge_check
        return challenge_check(db,uid,session)
    token=entry(db,uid,int(session.get('dest') or 0),int(session.get('activity_id') or 0))
    saved=session.get('hodur_context') or {}
    require(all(saved.get(k)==v for k,v in token.items()),'谧光叙录章节已重置，请重新进入战斗')


def select(ctx,uid,activity,chapter,option):
    require(type(option) is int and option in OPTIONS,'未知谧光叙录事件选项')
    with atomic(ctx):
        data=read(ctx.db,uid);validate_chapter(activity,chapter,data);row=data[chapter]
        event=OPTION_EVENT[option];require(EVENT_CHAPTER[event]==chapter,'谧光叙录事件不属于此章节')
        require(not any(o in EVENTS[event]['options'] for o in row['options']),'谧光叙录本轮事件已经选择')
        condition=EVENTS[event]['condition'];require(not condition or row['clears'].get(condition)==1,'谧光叙录事件前置关卡未完成')
        row['options'].append(option);row['events_ever']=sorted(set(row['events_ever'])|{event});save(ctx.db,uid,row)
        bus.emit_required(Events.HODUR_PROGRESS,ctx,uid)
        return dict(result=0,round=len(row['options']))


def reset(ctx,uid,activity,chapter):
    with atomic(ctx):
        data=read(ctx.db,uid);validate_chapter(activity,chapter,data);row=data[chapter]
        row['options']=[];row['heroes']=[];row['clears']={s:0 for s in row['clears']};row['run_id']=uuid.uuid4().hex
        save(ctx.db,uid,row)
        return dict(result=0)


def finish_battle(ctx,uid,stage,activity,*,win=True,report=None,session=None):
    with atomic(ctx):
        token=entry(ctx.db,uid,stage,activity)
        if not win:return []
        row=read(ctx.db,uid)[token['chapter_id']]
        previous=decode(json.dumps(row),dict)
        if report is not None and session is not None:
            from hodur_challenge import report_hp
            hp=report_hp(report,session.get('hodur_context') or {},required=False)
            if hp is not None:row['heroes']=list(hp.values())
        if row['clears'].get(stage)==1 and previous==row:return []
        row['clears'][stage]=1;save(ctx.db,uid,row)
        bus.emit_required(Events.HODUR_PROGRESS,ctx,uid)
        # 89421 is the only native gameplay state push. The battle callback
        # reads it to open the next stage; 54033 does not update Hodur data.
        from codec import encode
        from middleware import DownFrame
        packet=(ctx.codec_encode or encode)('sc_89421',payload(ctx.db,uid))
        if not isinstance(packet,(bytes,bytearray,memoryview)) or not packet:raise ValueError('谧光叙录进度帧为空')
        return [DownFrame(89421,bytes(packet))]


def project_tasks(db,uid):
    marks=','.join('?' for _ in LIMITED_TASKS)
    rows=db.query(f'''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND (c.condition BETWEEN 400001 AND 400005 OR c.task_id IN ({marks}))
        AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0 AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger cl WHERE cl.uid=t.uid AND cl.key_id=t.task_id AND cl.kind IN ('task','illustrated_task'))''',
        (uid,*LIMITED_TASKS,int(time.time())))
    rows=visible_tasks(db,uid,rows)
    if not rows:return []
    from hodur_challenge import read as challenge_read
    challenge=challenge_read(db,uid)
    data=read(db,uid);clears={s for r in data.values() for s in r['clears']};events={e for r in data.values() for e in r['events_ever']};changes=[];completed={}
    for r in sorted(rows,key=lambda r:r['condition']==501):
        try:params=json.loads(r['additional_parameter']) if isinstance(r['additional_parameter'],str) else r['additional_parameter']
        except (ValueError,TypeError):continue
        if not isinstance(params,list) or any(type(v) is not int for v in params):continue
        cond=int(r['condition']);need=int(r['need'] or 0)
        if need<=0:continue
        if cond==400001 and need==1 and len(params)==1:value=int(params[0] in events)
        elif cond==400002 and need==1 and len(params)==1:value=int(challenge['best_score']>=params[0])
        elif cond==400003 and need==1 and len(params)==1:value=int(challenge['best_affixes']>=params[0])
        elif cond==400004 and need==1 and len(params)==1 and params[0] in NORMAL:value=int(params[0] in clears)
        elif cond==400005 and need==1 and len(params)==1 and params[0] in data:value=int(all(s in clears for s in CHAPTERS[params[0]]['stages']))
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
        if not isinstance(packet,(bytes,bytearray,memoryview)) or not packet:raise ValueError('谧光叙录任务差量帧为空')
        ctx.append_frame(DownFrame(28007,bytes(packet)))


def restore_for_login(db,uid):
    with savepoint_transaction(db):return project_tasks(db,uid)
