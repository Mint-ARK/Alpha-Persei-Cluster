"""Polyhedron scene publication and settlement; no ordinary stage rewards."""
from contextlib import contextmanager
import uuid
from polyhedron_flow import CONTRACT,snapshot,settlement_frames
from polyhedron_service import PolyhedronRunManager as M

STAGES={int(i) for i in CONTRACT['stages']}

def require(ok,message):
    if not ok:raise ValueError(message)

def reserved(kind,stage):return kind==52 or stage in STAGES


def battle_heroes(run):
    leader=run['start_info']['leader']['hero_id']
    living={h['hero_id'] for h in run['hero_list'] if h['health']>0}
    return [leader]+[h for h in run['fight_hero_id_list'] if h!=leader and h in living]

def entry(ctx,uid,info):
    if not reserved(info['type'],info['dest']):return None
    require(info['type']==52 and info['dest'] in STAGES and info['battle_times']==1,'多维战斗类型或场景不匹配')
    run=M.active(uid,ctx.db)
    require(info['dest']==run.get('current_stage',{}).get('stage_id'),'多维当前场景不匹配')
    point=run['current_stage'].get('save_point')
    require(point in (1,2,3),'多维场景阶段无效')
    # Old valid runs receive identity once during publication, never on reads.
    identity=run.get('run_id') or uuid.uuid4().hex
    info['heroes']=battle_heroes(run)
    require(bool(info['heroes']),'多维编队为空')
    return dict(run_id=identity,tier=run['tier_id'],stage=info['dest'],token=uuid.uuid4().hex,
        save_point=point,heroes=list(info['heroes']),
        interaction=point!=1 or run.get('current_event',{}).get('reward_type') in (2,5))

def entry_frames(ctx,uid):return [snapshot(ctx,uid)]

@contextmanager
def publication(ctx,uid,bid,context):
    if context is None:
        yield
        return
    with ctx.db.transaction():
        run=M.active(uid,ctx.db)
        require(run['tier_id']==context['tier'] and run['current_stage']['stage_id']==context['stage'] and run.get('run_id',context['run_id'])==context['run_id'],'多维初始化期间场景已变化')
        require(run['current_stage']['save_point']==context['save_point'] and battle_heroes(run)==context['heroes'],'多维初始化期间阶段或编队已变化')
        context['bid']=bid
        run.update(run_id=context['run_id'],settlement_version=1,latest=dict(context),receipt=None)
        if not context['interaction']:run['hp_report_complete']=False
        M.save_run(uid,ctx.db,run)
        yield

def validate(db,uid,session,after=False):
    context=session.get('polyhedron_context');run=M.load_run(uid,db)
    require(session.get('uid')==uid and isinstance(context,dict) and run and run.get('run_id')==context.get('run_id'),'多维战斗身份已失效')
    require(session.get('stage_type')==52 and session.get('dest')==context['stage'],'多维结算场景不匹配')
    expected=run.get('receipt') if after else run.get('latest')
    require(expected==context and run['tier_id']==context['tier'],'多维战斗已被新尝试或节点替代')
    if not after:
        require(run.get('state')==2 and not run.get('settled'),'多维探索已经结束')
        require(run['current_stage']['save_point']==context['save_point'],'多维场景阶段已变化')
    return run


def record_hp(run,context,report):
    characters=(report or {}).get('characters')
    # Missing older reports cannot establish health. No aggregate distribution
    # or automatic healing is substituted. Malformed nonempty reports fail.
    if characters==[] or 'characters' not in (report or {}):return
    require(isinstance(characters,list),'多维角色血量报告无效')
    actual={}
    for row in characters:
        hid=row.get('hero_id');hp=row.get('hero_blood');maximum=row.get('hero_max_blood')
        require(type(hid) is int and type(hp) is int and type(maximum) is int and hid not in actual
                and 0<maximum<=2147483647 and hp<=maximum,'多维角色血量数值无效')
        actual[hid]=(max(0,hp),maximum)
    require(set(actual)==set(context['heroes']),'多维角色血量名单不匹配')
    for hero in run['hero_list']:
        if hero['hero_id'] in actual:
            hero['health'],hero['max_health']=actual[hero['hero_id']]
    run['hp_report_complete']=True

def settle(ctx,uid,session,result,report):
    require(type(result) is int and result in (1,2,3),'多维战斗结果无效')
    run=validate(ctx.db,uid,session)
    interaction=session['polyhedron_context']['interaction']
    if not interaction:record_hp(run,session['polyhedron_context'],report)
    if not interaction:
        kills=(report or {}).get('enemy_dead_num',0)
        require(type(kills) is int and 0<=kills<=2147483647,'多维敌人计数无效')
        from polyhedron_account import add_progress
        add_progress(ctx.db,uid,kills=kills)
    run['latest']=None;run['receipt']=dict(session['polyhedron_context'])
    M.save_run(uid,ctx.db,run)
    end=None
    if result==1 and not interaction:
        run=M.on_battle_finish(uid,ctx.db,is_win=True,damage_info=report)
        if run['state']==3:end=M.settle_run(uid,ctx.db,ctx)
    # A shop/recruit scene is launched through 54030 too. Its exit is not a kill.
    return settlement_frames(ctx,uid,end)+[snapshot(ctx,uid)]
