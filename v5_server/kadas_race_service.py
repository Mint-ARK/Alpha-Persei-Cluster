"""Kadas out-of-game state and native event/resident reward separation."""
from contextlib import contextmanager
import json
from pathlib import Path
import time

from account_db import savepoint_transaction
from core import OperationError
from event_bus import bus, Events
from operation_notifications import checkpoint_notifications, restore_notifications

CFG = json.loads(Path(__file__).with_name('kadas_contract.json').read_text('utf-8'))
ACTIVITY = CFG['activity_id']
STAGES = {int(k):v for k,v in CFG['stages'].items()}
BODIES = {int(k):v for k,v in CFG['bodies'].items()}
OPTIONS = {int(k):v for k,v in CFG['options'].items()}
LIMITED_TASKS = set(CFG['limited_tasks'])
DEFAULT_BODY = min((i for i,v in BODIES.items() if v['is_unlocked'] != 1), key=lambda i:BODIES[i]['sort'])
DEFAULT_OPTIONS = [i for i,v in OPTIONS.items() if v['is_unlocked'] != 1]


def require(ok, message):
    if not ok:
        raise OperationError(2, message)


@contextmanager
def atomic(ctx):
    checkpoint = checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            yield
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise


def limited_context(activities):
    now = int(time.time())
    return any(int(a['activity_id']) == CFG['parent_activity'] and int(a.get('state') or 0) == 1
               and int(a.get('start_time') or 0) <= now < int(a.get('stop_time') or 0)
               for a in activities)


def limited(db, uid):
    from generator import get_activity_list
    return limited_context(get_activity_list(db, uid))


def activity_policy(activities):
    if limited_context(activities):
        return activities
    for row in activities:
        if int(row['activity_id']) in CFG['limited_activities']:
            row['state'] = 0
    return activities


def visible_tasks(db, uid, rows, *, event=None):
    if not any(int(r['task_id']) in LIMITED_TASKS for r in rows):
        return rows
    if event is None:
        event = limited(db, uid)
    return rows if event else [r for r in rows if int(r['task_id']) not in LIMITED_TASKS]


def require_claimable(db, uid, ids):
    if LIMITED_TASKS.intersection(map(int, ids)):
        require(limited(db, uid), '卡达斯常驻模式不可领取限时活动奖励')


def values(raw):
    data = json.loads(raw or '[]') if isinstance(raw, str) else raw
    require(isinstance(data, list), '卡达斯存档列表格式错误')
    return data


def unlocked(missions):
    bodies = {i for i,v in BODIES.items() if v['is_unlocked'] != 1}
    options = set(DEFAULT_OPTIONS)
    for stage in missions:
        for kind, item in STAGES[stage]['rewards']:
            (bodies if kind == 3 else options).add(item)
    return bodies, options


def read(db, uid):
    rows = db.query('SELECT * FROM kadas_race_progress WHERE uid=? AND activity_id=?', (uid,ACTIVITY))
    row = rows[0] if rows else {}
    missions = sorted({i for i in values(row.get('mission_list',[])) if type(i) is int and i in STAGES})
    bodies, options = unlocked(missions)
    builds = {}
    for build in values(row.get('tank_info',[])):
        require(isinstance(build,dict), '卡达斯战车存档格式错误')
        body = build.get('tank_id')
        if type(body) is not int or body not in bodies:
            continue
        parts = [i for i in values(build.get('weapon_list',[])) if type(i) is int and i in options]
        builds[body] = [i for kind,key in ((1,'weapon_slots'),(2,'equip_slots'))
                        for i in [v for v in parts if OPTIONS[v]['item_type']==kind][:BODIES[body][key]]]
    current = int(row.get('node') or DEFAULT_BODY)
    if current not in bodies:
        current = DEFAULT_BODY
    if current not in builds:
        builds[current] = list(DEFAULT_OPTIONS)
    return dict(uid=uid,activity_id=ACTIVITY,tank_id=current,mission_list=missions,
        tank_info=[dict(tank_id=i,weapon_list=p) for i,p in sorted(builds.items())],
        round=max(0,int(row.get('status') or 0)),max_score=max(-1,min(0x7FFFFFFF,int(row.get('ts') if row.get('ts') is not None else -1))))


def save(db, data):
    current = next(b for b in data['tank_info'] if b['tank_id'] == data['tank_id'])
    primary = next((i for i in current['weapon_list'] if OPTIONS[i]['item_type']==1),0)
    db.execute('''INSERT INTO kadas_race_progress(uid,activity_id,node,node2,count,status,ts,update_ts,mission_list,tank_info)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uid,activity_id) DO UPDATE SET
        node=excluded.node,node2=excluded.node2,count=excluded.count,status=excluded.status,ts=excluded.ts,
        update_ts=excluded.update_ts,mission_list=excluded.mission_list,tank_info=excluded.tank_info''',
        (data['uid'],ACTIVITY,data['tank_id'],data['tank_id'],primary,data['round'],data['max_score'],int(time.time()),
         json.dumps(data['mission_list']),json.dumps(data['tank_info'])))


def payload(db, uid):
    data = read(db, uid)
    return {k:data[k] for k in ('activity_id','tank_id','mission_list','tank_info')} | dict(
        challenge_stage_info=dict(round=data['round'],max_score=data['max_score']))


def statistics_key(uid):
    return 'kadas.statistics_v1.'+str(uid)


def statistics(db, uid):
    rows = db.query('SELECT value FROM server_global_config WHERE key=?',(statistics_key(uid),))
    if rows:
        data = json.loads(rows[0]['value'])
        require(isinstance(data,dict) and type(data.get('points')) is int and data['points']>=0
                and isinstance(data.get('kills'),dict), '卡达斯累计统计格式错误')
        require(all(k.isdigit() and int(k) in OPTIONS and type(v) is int and v>=0 for k,v in data['kills'].items()), '卡达斯武器统计格式错误')
        return data
    # Capped historical task progress is only a lower bound, never sum tiers.
    points = 0; kills = {}
    for r in db.query('''SELECT t.progress,c.condition,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
                         WHERE t.uid=? AND c.condition IN (423004,423006)''',(uid,)):
        if r['condition']==423006:
            points=max(points,int(r['progress'] or 0))
        else:
            try: params=json.loads(r['additional_parameter'] or '[]')
            except (ValueError,TypeError):continue
            if isinstance(params,list) and len(params)==1 and type(params[0]) is int and params[0] in OPTIONS:
                key=str(params[0]);kills[key]=max(kills.get(key,0),int(r['progress'] or 0))
    return dict(version=1,points=points,kills=kills)


def settle(ctx, uid, activity, stage, result, round_num=0, score=0, kills=0):
    require(type(activity) is int and activity==ACTIVITY, '未知卡达斯活动')
    require(type(stage) is int and stage in STAGES, '未知卡达斯关卡')
    require(type(result) is int and result in (0,1), '卡达斯结算结果不合法')
    require(all(type(v) is int and 0<=v<=0xFFFFFFFF for v in (round_num,score,kills)), '卡达斯结算数值不合法')
    cfg=STAGES[stage]
    if cfg['mode_type']==4:
        require(score<=0x7FFFFFFF, '挑战分数超出原生int32范围')
    with atomic(ctx):
        data=read(ctx.db,uid)
        require(not cfg['pre_stage'] or cfg['pre_stage'] in data['mission_list'], '卡达斯前置关卡尚未完成')
        if cfg['mode_type']==4:
            require(all(i in data['mission_list'] for i,s in STAGES.items() if s['mode_type']==1), '卡达斯挑战需通关全部正赛')
        selected=next(b['weapon_list'] for b in data['tank_info'] if b['tank_id']==data['tank_id'])
        stats=statistics(ctx.db,uid)
        if result==0 and stage not in data['mission_list']:
            data['mission_list'].append(stage);data['mission_list'].sort()
        if cfg['mode_type']==4:
            data['round']=max(data['round'],round_num)
            data['max_score']=max(data['max_score'],score)
            stats['points']+=score
        if cfg['mode_type']!=5 and kills:
            for weapon in set(selected):
                if OPTIONS[weapon]['item_type']==1:
                    key=str(weapon);stats['kills'][key]=stats['kills'].get(key,0)+kills
        save(ctx.db,data)
        ctx.db.execute('INSERT INTO server_global_config(key,value) VALUES(?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value',(statistics_key(uid),json.dumps(stats)))
        bus.emit_required(Events.KADAS_PROGRESS,ctx,uid)
        return data


def modify(ctx, uid, activity, body, parts):
    require(type(activity) is int and activity==ACTIVITY, '未知卡达斯活动')
    require(type(body) is int and body in BODIES and isinstance(parts,list), '卡达斯车体或配件格式不合法')
    require(all(type(i) is int and i in OPTIONS for i in parts), '未知卡达斯配件')
    with atomic(ctx):
        data=read(ctx.db,uid);bodies,options=unlocked(data['mission_list'])
        require(body in bodies and set(parts)<=options, '卡达斯车体或配件尚未解锁')
        require(sum(OPTIONS[i]['item_type']==1 for i in parts)<=BODIES[body]['weapon_slots']
                and sum(OPTIONS[i]['item_type']==2 for i in parts)<=BODIES[body]['equip_slots'], '卡达斯配件超过原生孔位数量')
        require(sum(OPTIONS[i]['cost'] for i in parts)<=BODIES[body]['cost'], '卡达斯配件超过车体负载')
        # Lua serializes weapon mounts first, then equipment mounts. Keep each
        # type's order and allow the same unlocked module in separate mounts.
        ordered=[i for kind in (1,2) for i in parts if OPTIONS[i]['item_type']==kind]
        builds={b['tank_id']:b['weapon_list'] for b in data['tank_info']};builds[body]=ordered
        data['tank_id']=body;data['tank_info']=[dict(tank_id=i,weapon_list=p) for i,p in sorted(builds.items())]
        save(ctx.db,data)
        return data


def project_tasks(db, uid):
    rows=db.query('''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND (c.condition BETWEEN 423001 AND 423006 OR c.task_id IN (50202001,50202002,50202003,50202004,50202005))
        AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0 AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger cl WHERE cl.uid=t.uid AND cl.key_id=t.task_id AND cl.kind IN ('task','illustrated_task'))''',(uid,int(time.time())))
    rows=visible_tasks(db,uid,rows)
    if not rows:return []
    data=read(db,uid);stats=statistics(db,uid);bodies,options=unlocked(data['mission_list']);changes=[];completed_sets={}
    for row in sorted(rows,key=lambda r:r['condition']==501):
        try: params=json.loads(row['additional_parameter']) if isinstance(row['additional_parameter'],str) else row['additional_parameter']
        except (ValueError,TypeError):continue
        if not isinstance(params,list) or any(type(v) is not int for v in params):continue
        cond=int(row['condition']);need=int(row['need'] or 0)
        if need<=0:continue
        if cond==423001 and need==1 and len(params)==1:
            value=int(params[0] in data['mission_list'])
        elif cond==423002 and params and set(params)<={1,2,3}:
            value=sum(1 for i in options if OPTIONS[i]['item_type'] in params)+(len(bodies) if 3 in params else 0)
        elif cond==423003 and need==1 and len(params)==1 and params[0]>0:
            value=int(data['max_score']>=params[0])
        elif cond==423004 and len(params)==1:
            value=stats['kills'].get(str(params[0]),0)
        elif cond==423006 and not params:
            value=stats['points']
        elif cond==501 and params and row['task_id'] in LIMITED_TASKS:
            key=tuple(params)
            if key not in completed_sets:
                marks=','.join('?' for _ in params)
                done=db.query(f'''SELECT COUNT(*) AS n FROM task t JOIN task_cfg c ON c.task_id=t.task_id
                    WHERE t.uid=? AND t.task_id IN ({marks}) AND
                    (t.progress>=c.need OR t.claimed_ts>0 OR t.complete_flag=1)''',(uid,*params))
                completed_sets[key]=int(done[0]['n'])
            value=completed_sets[key]
        else:continue
        progress=min(need,value)
        if progress>int(row['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(progress,uid,row['task_id']))
            changes.append(dict(id=int(row['task_id']),progress=progress,complete_flag=0))
    return changes


def notify_tasks(ctx,uid):
    changes=project_tasks(ctx.db,uid)
    if changes:
        from codec import encode
        from middleware import DownFrame
        packet=(ctx.codec_encode or encode)('sc_28007',dict(progress_list=changes))
        if not isinstance(packet,(bytes,bytearray,memoryview)) or not packet:raise ValueError('卡达斯任务差量帧为空')
        ctx.append_frame(DownFrame(28007,bytes(packet)))


def restore_for_login(db,uid):
    with savepoint_transaction(db):return project_tasks(db,uid)
