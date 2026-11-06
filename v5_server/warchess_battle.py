"""Stage15 battle publication/settlement; caller owns mandatory54033 encoding."""
from contextlib import contextmanager
from copy import deepcopy
import warchess_service as chess

STAGES={int(i) for i in chess.CFG['stages']}


def reserved(kind,stage):return kind==15 or stage in STAGES


def entry(ctx,uid,info):
    if not reserved(info['type'],info['dest']):return None
    chess.require(info['type']==15 and info['dest'] in STAGES and info['battle_times']==1,'Invalid chess battle type, stage or multiplier')
    state=chess.active(ctx.db,uid)
    chess.require(info['activity_id']==chess.LEVELS[state['chapter']]['type'],'Wrong chess activity group')
    context=chess.prepare_battle(ctx.db,uid,info['dest'],info['index'])
    heroes=info['heroes'];chess.require(1<=len(heroes)<=3 and len(set(heroes))==len(heroes),'Invalid chess battle team')
    hp={h['id']:h['hp'] for h in state['map']['hp_list']}
    chess.require(all(hp.get(h,10000)>0 for h in heroes),'Defeated chess hero must be revived')
    context['heroes']=list(heroes)
    return context


@contextmanager
def publication(ctx,uid,bid,context):
    if context is None:
        yield
        return
    with ctx.db.transaction():
        state=chess.active(ctx.db,uid);known={h['id'] for h in state['map']['hp_list']}
        for hid in context['heroes']:
            if hid not in known:state['map']['hp_list'].append(dict(id=hid,hp=10000))
        chess.save(ctx.db,uid,state)
        chess.bind_battle(ctx.db,uid,context,bid)
        yield


def validate(db,uid,session,after=False):
    context=session.get('warchess_context');state=chess.active(db,uid)
    chess.require(session.get('uid')==uid and isinstance(context,dict) and context.get('uid')==uid
                  and context.get('run')==state['run'] and session.get('stage_type')==15
                  and session.get('dest')==context.get('stage_id'),'Invalid chess battle identity')
    if after:chess.require(state.get('last_receipt')==context,'Chess settlement receipt mismatch')
    else:
        p=state.get('pending')
        chess.require(p and p['kind']=='battle' and p.get('token')==context.get('token')
                      and p.get('battle_id')==context.get('battle_id'),'Chess battle was replaced or consumed')
    return state


def settle(ctx,uid,session,result,report):
    chess.require(type(result) is int and result in (1,2,3),'Invalid chess battle result')
    validate(ctx.db,uid,session);context=session['warchess_context'];characters=(report or {}).get('characters');hp=None
    if characters:
        chess.require(isinstance(characters,list),'Invalid chess health report');hp=[]
        for r in characters:
            hid=r.get('hero_id');blood=r.get('hero_blood');maximum=r.get('hero_max_blood')
            chess.require(type(hid) is int and type(blood) is int and type(maximum) is int
                          and 0<maximum<=2147483647 and 0<=blood<=maximum,'Invalid chess health values')
            hp.append(dict(id=hid,hp=blood*10000//maximum))
        chess.require({h['id'] for h in hp}==set(context['heroes']) and len(hp)==len(context['heroes']),'Chess report team mismatch')
    events=chess.settle_battle(ctx,uid,context,result==1,hp)
    return chess.frames(ctx,uid,events)
