"""Skuld node authority. Native callbacks apply online node/mark changes."""
import json
from pathlib import Path
import time
from account_db import savepoint_transaction
from core import Operation, operation
from event_bus import bus, Events
from hodur_service import atomic, require

CFG=json.loads(Path(__file__).with_name('skuld_contract.json').read_text('utf-8'))
ACTIVITY=CFG['activity_id']
NODES={int(k):v for k,v in CFG['nodes'].items()}
COMBAT={i:v for i,v in NODES.items() if v['type']==1}
REAL={v['param']:i for i,v in COMBAT.items()}
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
    if LIMITED_TASKS.intersection(map(int,ids)):require(limited(db,uid),'溯梦之隙常驻模式不可领取限时任务奖励')


def read(db,uid):
    rows=db.query('SELECT * FROM skuld_progress WHERE uid=? AND activity_id=?',(uid,ACTIVITY))
    if not rows:return dict(client_opt_list=[],mission_list=[],reward_list=[],point=0)
    r=rows[0];out={}
    for field,target in [('client_opt_list','client_opt_list'),('mission_json','mission_list'),('reward_list','reward_list')]:
        value=json.loads(r[field] or '[]');require(isinstance(value,list),'心象存档格式损坏：'+field);out[target]=value
    out['client_opt_list']=sorted({i for i in out['client_opt_list'] if type(i) is int and i in CFG['marks']})
    missions={}
    for m in out['mission_list']:
        if not isinstance(m,dict):continue
        i=m.get('id');n=m.get('times')
        if type(i) is int and i in NODES and type(n) is int and 0<n<=0xffffffff:missions[i]=max(missions.get(i,0),n)
    out['mission_list']=[dict(id=i,times=n) for i,n in sorted(missions.items())]
    from minigame_point_service import CATALOG
    rewards=out['reward_list']+[r['reward_id'] for r in db.query('SELECT reward_id FROM activity_point_reward WHERE uid=? AND activity_id=?',(uid,ACTIVITY))]
    out['reward_list']=sorted({i for i in rewards if type(i) is int and CATALOG.get(str(i),{}).get('activity_id')==ACTIVITY})
    out['point']=int(r['point'] or 0)
    return out


def save(db,uid,r):
    db.execute('INSERT INTO skuld_progress(uid,activity_id,client_opt_list,mission_json,reward_list,point,update_ts) VALUES(?,?,?,?,?,?,?) '
               'ON CONFLICT(uid,activity_id) DO UPDATE SET client_opt_list=excluded.client_opt_list,mission_json=excluded.mission_json,reward_list=excluded.reward_list,update_ts=excluded.update_ts',
               (uid,ACTIVITY,json.dumps(r['client_opt_list']),json.dumps(r['mission_list']),json.dumps(r['reward_list']),r['point'],int(time.time())))


def payload(db,uid):
    from inventory_service import InventoryService
    r=read(db,uid)
    return dict(activity_id=ACTIVITY,client_opt=r['client_opt_list'],mission_list=r['mission_list'],reward_list=r['reward_list'],
                point=InventoryService.get_item_balance(db,uid,76)[1])


def unlocked(rec,node):
    cleared={m['id'] for m in rec['mission_list']}
    return all(p in cleared for p in NODES[node]['pre_id'])


def archive_ids(db,uid):
    passed={m['id'] for m in read(db,uid)['mission_list']}
    return [int(i) for i,v in CFG['archives'].items() if all(n in passed for n in v['nodes'])]


def archive_payload(db,uid):
    # Preserve the account's other chapter archives. Skuld entries are derived
    # from native condition329101 rather than inherited capture flags.
    ids={r['archive_id'] for r in db.query('SELECT archive_id FROM stage_archive_collect WHERE uid=?',(uid,))
         if str(r['archive_id']) not in CFG['archives']}
    return dict(plot_list=sorted(ids|set(archive_ids(db,uid))))


def battle_entry(db,uid,dest,activity,*,story=False):
    require(type(dest) is int,'心象关卡参数非法')
    # GetDest is the logical node; LaunchStoryBattle sends the resource ID.
    node=REAL.get(dest) if story else dest
    require(node in COMBAT,'未知心象战斗节点')
    require(type(activity) is int and activity in (0,ACTIVITY),'心象活动不匹配')
    require((CFG['battles'][str(COMBAT[node]['param'])]['tag']==2)==story,'心象剧情/普通战斗入口不匹配')
    require(unlocked(read(db,uid),node),'心象前置关卡尚未完成')
    return node


def complete(ctx,uid,node):
    previous=set(archive_ids(ctx.db,uid))
    rec=read(ctx.db,uid);was=any(m['id']==node for m in rec['mission_list'])
    require(unlocked(rec,node),'心象前置关卡尚未完成')
    count=next((m['times'] for m in rec['mission_list'] if m['id']==node),0)
    rec['mission_list']=[m for m in rec['mission_list'] if m['id']!=node]+[dict(id=node,times=min(0xffffffff,count+1))]
    rewards=[]
    receipt=ctx.db.query("SELECT 1 FROM claim_ledger WHERE uid=? AND kind='skuld_first' AND key_id=?",(uid,node))
    if not was and not receipt:
        from inventory_service import InventoryService
        rewards=NODES[node]['rewards']
        summary=InventoryService.grant_items(ctx,uid,rewards,source='skuld_first')
        rewards=InventoryService.rewards_after_overflow(rewards,summary)
        ctx.db.execute("INSERT INTO claim_ledger(uid,kind,key_id,claim_ts) VALUES(?,'skuld_first',?,?)",(uid,node,int(time.time())))
    save(ctx.db,uid,rec)
    bus.emit_required(Events.SKULD_PROGRESS,ctx,uid)
    new=sorted(set(archive_ids(ctx.db,uid))-previous)
    if new:
        from middleware import DownFrame
        for i in new:
            ctx.db.execute('INSERT OR IGNORE INTO stage_archive_collect(uid,archive_id,name) VALUES(?,?,?)',(uid,i,CFG['archives'][str(i)]['name']))
        packet=ctx.codec_encode('sc_24045',dict(plot_list=new))
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'心象章节收藏差量为空')
        ctx.append_frame(DownFrame(24045,bytes(packet)))
    return rewards


def finish_battle(ctx,uid,node,activity,result):
    require(type(node) is int and node in COMBAT and type(result) is int and result in (1,2,3),'心象战斗结算非法')
    story=CFG['battles'][str(COMBAT[node]['param'])]['tag']==2
    battle_entry(ctx.db,uid,COMBAT[node]['param'] if story else node,activity,story=story)
    with atomic(ctx):
        if result!=1:return [],[]
        rewards=complete(ctx,uid,node)
        from operations import _refresh_frames
        return _refresh_frames(ctx,uid,strict=True),rewards


def project_tasks(db,uid):
    marks=','.join('?' for _ in LIMITED_TASKS)
    rows=db.query(f'''SELECT t.task_id,t.progress,c.condition,c.need,c.additional_parameter FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND (c.condition=329001 OR c.task_id IN ({marks}))
        AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0 AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=t.uid AND l.key_id=t.task_id AND l.kind IN ('task','illustrated_task'))''',(uid,*LIMITED_TASKS,int(time.time())))
    rows=visible_tasks(db,uid,rows)
    if not rows:return []
    rec=read(db,uid);passed={m['id'] for m in rec['mission_list']};changes=[];counts={}
    for r in sorted(rows,key=lambda r:r['condition']==501):
        try:params=json.loads(r['additional_parameter'])
        except (ValueError,TypeError):continue
        if not isinstance(params,list) or any(type(i) is not int for i in params):continue
        need=int(r['need'] or 0)
        if need<=0:continue
        if r['condition']==329001 and len(params)==1 and params[0] in NODES:value=int(params[0] in passed)
        elif r['condition']==501 and params and r['task_id'] in LIMITED_TASKS:
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
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'心象任务差量为空')
        ctx.append_frame(DownFrame(28007,bytes(packet)))


def restore_for_login(db,uid):
    with savepoint_transaction(db):return project_tasks(db,uid)


class SkuldOperation(Operation):
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        require(type(data.get('activity_id')) is int and data['activity_id']==ACTIVITY,'心象活动不匹配')
        if self.cmd==24052:
            key=data.get('key');require(type(key) is int and key in CFG['marks'],'未知心象标记')
            rec=read(ctx.db,self.uid)
            if key not in rec['client_opt_list']:rec['client_opt_list'].append(key);save(ctx.db,self.uid,rec)
            return dict(result=0)
        node=data.get('id');require(type(node) is int and node in NODES and NODES[node]['type']==2,'未知心象拼图节点')
        rewards=complete(ctx,self.uid,node)
        return dict(result=0,reward_list=[dict(id=i,num=n) for i,n in rewards])

    def respond(self,result,data,ctx):
        from middleware import DownFrame
        from operations import _refresh_frames
        packet=ctx.codec_encode('sc_'+str(self.sc),result)
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'心象回执为空')
        return _refresh_frames(ctx,self.uid,strict=True)+[DownFrame(self.sc,bytes(packet))]


@operation
class SkuldSendMarkOp(SkuldOperation):cmd,sc=24052,24053
@operation
class SkuldPuzzleCompleteOp(SkuldOperation):cmd,sc=24054,24055
