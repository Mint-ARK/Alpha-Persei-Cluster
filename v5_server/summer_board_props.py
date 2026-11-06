"""Authoritative prop effects; native success callbacks perform local playback."""
from copy import deepcopy
import summer_board_service as board
from hodur_service import require


def validate(active):
    hero=board.player(active['map']);override=active.get('prop_dice')
    if hero['use_item']==10002:
        require(type(override) is int and 1<=override<=6,'任意骰子选择记录损坏')
    else:require('prop_dice' not in active,'任意骰子残留选择记录')


def use(active,data,engine):
    from summer_board_chance import prop
    from summer_board_movement import grid,_hex
    from summer_board_npc import at,stop
    hero=engine.entity(0);pid=data.get('prop_id');cfg=prop(pid);params=data.get('params',[]);kind=cfg['type']
    if board.CFG['chances'].get(str(hero['chance']['chance_id']),{}).get('type')==14:
        from summer_board_games import use_prop
        use_prop(active,data,engine)
        return
    require(isinstance(params,list) and len(params)<=2 and all(type(v) is int and -0x80000000<=v<=0x7fffffff for v in params),'棋盘道具参数非法')
    require(board.attribute(hero,8)==0 and board.attribute(hero,5)==0 and board.attribute(hero,7)==0
            and hero['use_item']==0 and not hero['chance']['chance_id'] and not hero['tmp_item'],'当前不能使用棋盘道具')
    require(pid in hero['item'],'未持有该棋盘道具')
    if kind in (10001,10005,10006,10009):
        require(not params,'该道具不接受目标参数')
        if kind==10001:engine.add_buff(0,cfg['params'][0])
        elif kind==10009:stop(engine,cfg['params'][0])
        else:
            key,delta=cfg['params']
            require(key in board.ATTRIBUTES and (kind!=10006 or board.attribute(hero,key)+delta<=2),'骰子数量超出范围')
            board.set_attribute(hero,key,board.attribute(hero,key)+delta)
    elif kind==10004:
        require(len(params)==1 and 1<=params[0]<=min(6,board.attribute(hero,1)),'任意骰子点数超出当前范围')
        # Keep the choice separate from persistent guide/Buff fixed-dice values.
        # Lua's PropEffect adds a hard-coded 3, but rolls consume server dice_list.
        active['prop_dice']=params[0]
    else:
        require(len(params)==2,'该道具需要目标坐标')
        x,z=params;tile=grid(active['chapter_id'],active['map'],x,z);target=at(engine,dict(x=x,z=z))
        if kind in (10002,10003):
            require(target is not None,'目标位置没有棋盘NPC')
            if kind==10002:engine.delete_npc(target['id'])
            else:
                # Native swap is a teleport, not movement: no tile/encounter hooks.
                old_pos,old_dir=deepcopy(hero['pos']),hero['direction']
                hero['pos'],hero['direction']=deepcopy(target['pos']),target['direction']
                target['pos'],target['direction']=old_pos,old_dir
        elif kind==10007:
            require(hero['pos']!=dict(x=x,z=z) and target is None and tile['thingTypeID']==0,'目标位置已被占用')
            require(_hex(tile)['walkable']>=1 and _hex(tile)['updatable']==0,'该地块不能放置道具')
            engine.change('object',x,z,cfg['params'][0])
        elif kind==10008:
            if hero['pos']==dict(x=x,z=z):
                buffs=[b for b in hero['buff'] if board.CFG['buffs'][str(b['id'])]['clash']==1]
                require(bool(buffs),'玩家没有可清除的棋盘效果')
                for buff in buffs:engine.remove_buff(0,buff['id'])
            else:
                thing=board.CFG['things'].get(str(tile['thingTypeID']))
                require(thing is not None and thing['destoryable']>0,'该物件不可拆除')
                engine.change('object',x,z,-1)
        elif kind==10010:
            require(target is not None or hero['pos']==dict(x=x,z=z),'目标位置没有角色或NPC')
            # Native selector prioritizes an NPC if both entities share a tile.
            engine.add_buff(target['id'] if target else 0,cfg['params'][0])
        else:require(False,'未知棋盘道具效果')
    hero['item'].remove(pid);hero['use_item']=pid
    validate(active)
