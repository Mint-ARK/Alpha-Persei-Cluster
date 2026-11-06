"""Server-planned native NPC routes, encounters and turn playback.

Lua consumes server paths; the unavailable C# branch preference is not assumed.
Our routing chooses a legal non-reversing neighbor uniformly, reversing only at
a dead end. Automatic tiles consume no dice, with bounded traversal.
"""
import random
import summer_board_service as board
from summer_board_movement import neighbors,grid,_hex
from hodur_service import require


def at(engine,pos,exclude=0):
    return next((h for h in engine.state['hero'] if h['id']>0 and h['id']!=exclude and h['pos']==pos),None)


def hero_encounter(engine,tile):
    hero=engine.entity(0);npc=at(engine,hero['pos'])
    if npc is None:return
    ref=npc['id'];stay=board.attribute(hero,5)<=0
    engine.buff_effect(0,3 if stay else 4,ref)
    if engine.alive(ref):
        pool=board.CFG['npcs'][str(npc['type'])]['player_stay_npc_event' if stay else 'player_cross_npc_event']
        if pool:engine.pool(pool,10 if stay else 6,[ref],actor=0,other=ref,tile=tile)


def stop(engine,turns):
    require(type(turns) is int and 0<turns<=3,'棋盘停止回合数非法')
    for npc in engine.state['hero']:
        if npc['id']>0:board.set_attribute(npc,12,max(board.attribute(npc,12),turns))


def _encounter(engine,ref,tile):
    npc=engine.entity(ref);hero=engine.entity(0);stay=board.attribute(npc,5)<=0
    cfg=board.CFG['npcs'][str(npc['type'])]
    if npc['pos']==hero['pos']:
        engine.buff_effect(0,1 if stay else 2,ref)
        if engine.alive(ref):
            pool=cfg['npc_stay_player_event' if stay else 'npc_cross_player_event']
            if pool:engine.pool(pool,9 if stay else 7,[ref],actor=ref,other=0,tile=tile)
    elif stay:
        other=at(engine,npc['pos'],ref)
        if other and cfg['npc_stay_npc_event']:
            engine.pool(cfg['npc_stay_npc_event'],8,[ref],actor=ref,other=other['id'],tile=tile)


def _step(engine,ref,target,index):
    npc=engine.entity(ref);source=engine.current(ref);x,z,direction=target;destination=grid(engine.chapter,engine.state,x,z)
    if _hex(destination)['updatable']==1:
        npc['pos']=dict(x=x,z=z);npc['direction']=direction
        return
    if index==0:
        engine.trigger(source,'thingStartMoveParam',26,actor=ref);engine.trigger(source,'startMoveParam',4,actor=ref)
    engine.trigger(source,'thingExitParam',25,actor=ref);engine.trigger(source,'exitParam',3,actor=ref)
    if not engine.alive(ref):return
    npc['pos']=dict(x=x,z=z);npc['direction']=direction;board.set_attribute(npc,5,board.attribute(npc,5)-1)
    _encounter(engine,ref,destination)
    if not engine.alive(ref):return
    engine.trigger(destination,'thingEnterParam',19,actor=ref)
    if not engine.alive(ref):return
    engine.trigger(destination,'enterParam',2,actor=ref)
    if not engine.alive(ref):return
    if board.attribute(npc,5)<=0:
        # Native NPC callback uses elseif, unlike the hero's two independent hooks.
        pool=destination['thingEndMoveParam']
        has_thing=pool and board.CFG['events'].get(str(pool),{}).get('npc_params')
        key,timing=('thingEndMoveParam',27) if has_thing else ('endMoveParam',5)
        engine.trigger(destination,key,timing,actor=ref)
    if engine.alive(ref):engine.buff_step(ref)


def run_turn(engine):
    paths=[];hops=0
    initial=sorted([h['id'] for h in engine.state['hero'] if h['id']>0],reverse=True)
    for ref in initial:
        if not engine.alive(ref):continue
        npc=engine.entity(ref)
        stopped=board.attribute(npc,12)
        if stopped:
            require(stopped<=3,'棋盘停止回合记录损坏');board.set_attribute(npc,12,stopped-1)
            continue
        if not neighbors(engine.chapter,engine.state,npc):continue
        count,size,fixed=(board.attribute(npc,key) for key in (2,1,3))
        require(1<=count<=2 and 1<=size<=6 and 0<=fixed<=6,'棋盘NPC骰子参数损坏')
        budget=sum(fixed or random.randint(1,size) for _ in range(count))
        board.set_attribute(npc,5,budget)
        route=dict(npc_id=ref,path=[],move_num=budget);paths.append(route)
        cfg=board.CFG['npcs'][str(npc['type'])]
        if cfg['npc_start_event']:engine.pool(cfg['npc_start_event'],11,[ref],actor=ref)
        require(engine.alive(ref) and board.attribute(npc,5)>0,'NPC开始事件导致无效掷骰')
        visited=set()
        while engine.alive(ref) and board.attribute(npc,5)>0:
            hops+=1;require(hops<=512,'棋盘NPC路径过长')
            options=neighbors(engine.chapter,engine.state,npc)
            # A route that ends with positive budget deadlocks Lua playback.
            require(bool(options),'NPC路径中途被完全封闭')
            nonreverse=[p for p in options if p[2]!=(npc['direction']+3)%6]
            target=random.choice(nonreverse or options)
            marker=(npc['pos']['x'],npc['pos']['z'],target,board.attribute(npc,5))
            require(marker not in visited,'棋盘NPC自动格路径循环');visited.add(marker)
            route['path'].append(dict(x=target[0],z=target[1]))
            _step(engine,ref,target,len(route['path'])-1)
        if engine.alive(ref):
            engine.buff_turn(ref)
            if engine.alive(ref) and cfg['npc_stop_event']:engine.pool(cfg['npc_stop_event'],12,[ref],actor=ref)
    return paths
