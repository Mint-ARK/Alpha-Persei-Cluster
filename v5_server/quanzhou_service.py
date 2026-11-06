"""Citong map position and identity-bound 84/85 battles; no generic plot drops."""
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import uuid

from core import Operation, operation
from hodur_service import require, atomic
import quanzhou_collection as collection
import quanzhou_quest as quest

CFG = collection.CFG
ACTIVITY = 323641
CHILD = 323841
MAP = 1110101
KIND = 'quanzhou_runtime_v1'
SEA = collection.SEA_STAGES
SAND = {int(i): c for i,c in CFG['sand_battles'].items()}
SEA_REAL = {c['stage_id'] for c in SEA.values()}
POSITION_FIELDS = ('position_x','position_y','position_z','rotation_x','rotation_y','rotation_z','rotation_w')


def read(db, uid):
    require(db.get('users',uid) is not None,'账号不存在')
    rows = db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',(uid,KIND,str(ACTIVITY)))
    state = json.loads(rows[0]['value_json']) if rows else dict(version=1,position=None,latest=None)
    require(isinstance(state,dict) and state.get('version')==1,'刺桐存档格式错误')
    require(state.get('position') is None or isinstance(state['position'],dict),'刺桐位置记录损坏')
    require(state.get('latest') is None or isinstance(state['latest'],dict),'刺桐战斗记录损坏')
    return state


def save(db, uid, state):
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
               'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
               (uid,KIND,str(ACTIVITY),json.dumps(state,allow_nan=False)))


def position_payload(db, uid):
    position=read(db,uid)['position']
    return dict(activity_id=ACTIVITY,stage_list=[deepcopy(position)] if position else [])


def reserved(kind, dest):
    return kind in (84,85) or dest in SEA or dest in SAND or dest in SEA_REAL


def entry(ctx, uid, info):
    kind,dest=info['type'],info['dest']
    if not reserved(kind,dest):return None
    require(info['battle_times']==1,'刺桐战斗不支持多倍或扫荡')
    require(collection.activity_open(ctx.db,uid),'刺桐活动未开放')
    if kind==84:
        # GetDest is 400xxx; native GetActivityID may return nil -> wire zero.
        require(info['activity_id'] in (0,CHILD),'航行活动归属不匹配')
        checked=collection.enter_validate(ctx,uid,dest,info.get('index',0))
        real=checked['stage_id']
    elif kind==85 and dest in SAND:
        require(info['activity_id']==CHILD,'箱庭战斗活动归属不匹配')
        mains,tasks=quest.read(ctx.db,uid);quest.advance(ctx.db,uid,mains,tasks)
        require(any(t['status'] in (0,1) and quest.TASKS[i]['condition']==330002
                    and quest.TASKS[i]['additional_parameter']==[dest] for i,t in tasks.items()),
                '箱庭战斗任务尚未开启')
        team=[dict(hero_id=trial,hero_type=2) for hero,trial in SAND[dest]['hero_list'] if hero and trial]
        require(info['hero_details']==team,'请使用箱庭配置的试用队伍')
        real=dest
    else:
        raise ValueError('刺桐关卡与战斗类型不匹配')
    return dict(kind=kind,dest=dest,stage=real,activity=info['activity_id'],token=uuid.uuid4().hex)


@contextmanager
def publication(ctx, uid, bid, context):
    if not context:
        yield
        return
    try:
        with atomic(ctx):
            state=read(ctx.db,uid);state['latest']=dict(context,bid=bid)
            save(ctx.db,uid,state)
            yield
    except BaseException:
        import middleware as mw
        with mw._BATTLE_LOCK:
            current=mw._BATTLE_SESSIONS.get(bid)
            if current and current.get('uid')==uid and current.get('quanzhou_context')==context:
                mw._BATTLE_SESSIONS.pop(bid,None)
        raise


def settle(ctx, uid, bid, session, result, report):
    require(type(result) is int and result in (1,2,3),'刺桐战斗结果无效')
    require(isinstance(report,dict) and type(report.get('battle_time',0)) is int
            and report.get('battle_time',0)>=0,'刺桐战报耗时无效')
    with atomic(ctx):
        state=read(ctx.db,uid);context=session.get('quanzhou_context')
        require(session.get('uid')==uid and context and state['latest']==dict(context,bid=bid),
                '刺桐战斗已被新一次挑战替代')
        require(context['dest']==session['dest'] and context['kind']==session['stage_type']
                and context['activity']==session['activity_id'],'刺桐战斗身份不匹配')
        require(collection.activity_open(ctx.db,uid),'刺桐活动已关闭')
        rewards=[]
        if context['kind']==84:
            rewards=collection.settle(ctx,uid,context['dest'],result==1)['reward_list']
        if result==1:
            quest.battle_clear(ctx,uid,context['stage'])
        state['latest']=None;save(ctx.db,uid,state)
        from operations import _refresh_frames
        return _refresh_frames(ctx,uid,strict=True),rewards


@operation
class QuanzhouSavePositionOp(Operation):
    cmd=79602
    sc=79603
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        require(data.get('activity_id')==ACTIVITY,'箱庭活动不匹配')
        rows=data.get('stage_list')
        require(isinstance(rows,list) and len(rows)==1,'请保存当前一张箱庭地图')
        position=rows[0]
        require(position.get('stage_id')==MAP and position.get('map_status')==0,'箱庭地图不匹配')
        require(all(type(position.get(k)) in (float,int) and math.isfinite(position[k])
                    and abs(position[k])<=10000000 for k in POSITION_FIELDS),'箱庭坐标无效')
        require(collection.activity_open(ctx.db,self.uid),'刺桐活动未开放')
        state=read(ctx.db,self.uid)
        state['position']={k:position[k] for k in ('stage_id','map_status',*POSITION_FIELDS)}
        save(ctx.db,self.uid,state)
        return dict(result=0)

    def respond(self,result,data,ctx):
        return [collection.frame(ctx,self.sc,result)]
