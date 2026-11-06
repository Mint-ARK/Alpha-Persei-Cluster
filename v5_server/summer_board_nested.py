"""Nested game bridge: current chance identity, battle isolation and local games."""
from copy import deepcopy
import uuid
import summer_board_service as board
from hodur_service import require

KINDS=(3,7,8,10,11,12,13)


def active_context(db,uid):
    extra,state=board.read(db,uid);active=state['active']
    if not active:return extra,state,None,None
    hero=board.player(active['map']);cfg=board.CFG['chances'].get(str(hero['chance']['chance_id']),{})
    return extra,state,active,cfg


def open_local(engine,cid):
    # Tangram StartGame requires template objects even though chess mode never
    # sends standalone progress requests. Preserve the existing activity owner.
    if board.CFG['chances'][str(cid)]['type']==12:
        engine.frames.append((86008,dict(activity_id=2800019,tangram_info_list=[dict(stage_id=i,tips_id_list=[],is_animation_finish=False) for i in (3001,3002)])))


def entry(db,uid,info):
    kind=int(info.get('type') or 0);dest=int(info.get('dest') or 0)
    if not reserved(kind,dest):return None
    _,state,active,cfg=active_context(db,uid)
    context=None
    if active and cfg.get('type') in KINDS and board.player(active['map'])['chance']['is_unlock']==1:
        cid=board.player(active['map'])['chance']['chance_id'];spec=board.CFG['nested'][str(cid)]
        if spec['type'] and (kind,dest)==(spec['type'],spec['dest']):
            require(int(info.get('battle_times') or 1)==1,'棋盘小游戏不能多倍或扫荡')
            context=dict(run_id=active['run_id'],chance_id=cid,token=active['map']['nested_token'])
    dedicated=reserved(kind,dest)
    require(context is not None or not dedicated,'没有匹配的已开放棋盘机会')
    return context


def reserved(kind,dest):
    return kind==75 or (kind==74 and dest==0) or (kind==70 and dest in (1,10)) or (kind in (72,73) and any(v['type']==kind and v['dest']==dest for v in board.CFG['nested'].values()))


def publish(db,uid,bid,context):
    if not context:return
    extra,state,active,cfg=active_context(db,uid)
    require(active and active['run_id']==context['run_id'] and active['map'].get('nested_token')==context['token'],'棋盘机会已变化')
    active['map']['nested_bid']=bid;active['map'].pop('nested_result',None);board.save(db,uid,extra,state)


def settle(ctx,uid,bid,result,session):
    context=session.get('summer_board_context');require(context and session.get('uid')==uid,'棋盘战斗身份不匹配')
    extra,state,active,cfg=active_context(ctx.db,uid)
    require(active and active['run_id']==context['run_id'] and active['map'].get('nested_token')==context['token']
            and active['map'].get('nested_bid')==bid and board.player(active['map'])['chance']['chance_id']==context['chance_id'],
            '棋盘战斗已过期或不属于当前机会')
    require(cfg.get('type') in KINDS,'当前机会不是嵌套小游戏')
    active['map']['nested_result']=dict(bid=bid,win=result==1)
    board.save(ctx.db,uid,extra,state)


def finish(active,cfg,result,score):
    require(cfg['type'] in KINDS,'不是嵌套小游戏')
    if result==0 and board.CFG['nested'][str(board.player(active['map'])['chance']['chance_id'])]['type']:
        require(active['map'].get('nested_result',{}).get('win') is True,'尚无当前小游戏的成功结算')
    if cfg['type'] in (8,10,11,12,13):require(score==0,'此小游戏不提交棋盘分数')
    else:require(0<=score<=0x7fffffff,'小游戏分数非法')
    # Bubble and Tangram are local bridges without a network start/result pair.
    for key in ('nested_token','nested_bid','nested_result'):active['map'].pop(key,None)


def validate(active):
    hero=board.player(active['map']);cfg=board.CFG['chances'].get(str(hero['chance']['chance_id']),{})
    if cfg.get('type') in KINDS:
        require(isinstance(active['map'].get('nested_token'),str) and bool(active['map']['nested_token']),'棋盘小游戏身份损坏')
    else:require(not any(k in active['map'] for k in ('nested_token','nested_bid','nested_result')),'棋盘小游戏身份残留')
