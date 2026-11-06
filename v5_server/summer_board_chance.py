"""Native non-battle chances and two-phase ChessBoard prop acquisition.

The client applies rewards optimistically in its success callbacks. Only chance
creation needs 85033; confirmations must not replay inventory or full-map frames.
"""
from collections import Counter
from copy import deepcopy
import random
import summer_board_service as board
from hodur_service import require

EMPTY=dict(chance_id=0,is_unlock=0,item_list=[],choose_list=[])
SUPPORTED=tuple(range(1,15))


def prop(pid):
    require(type(pid) is int and str(pid) in board.CFG['props'],'未知棋盘道具')
    return board.CFG['props'][str(pid)]


def _draw(count,candidates):
    require(type(count) is int and 0<count<=len(candidates),'棋盘道具抽取数量错误')
    for pid in candidates:prop(pid)
    # Retain duplicate entries in native pools; sampling indexes keeps weighting.
    return random.sample(candidates,count)


def validate(hero):
    require(len(hero['item'])<=6 and len(hero['tmp_item'])<=64,'棋盘道具数量损坏')
    for pid in hero['item']+hero['tmp_item']:prop(pid)
    if hero['use_item']:prop(hero['use_item'])
    chance=hero['chance'];cid=chance['chance_id']
    if not cid:
        require(chance==EMPTY,'空机会残留异常参数');return
    require(str(cid) in board.CFG['chances'],'未知棋盘机会')
    cfg=board.CFG['chances'][str(cid)];items=chance['item_list'];chosen=chance['choose_list']
    if cfg['type'] in (1,2):
        count,candidates=cfg['params']
        require(len(items)==count and not (Counter(items)-Counter(candidates)),'棋盘机会道具配置损坏')
        require(not (Counter(chosen)-Counter(items)) and len(chosen)==len(set(chosen)),'棋盘购买记录损坏')
        require(cfg['type']==1 or not chosen,'非商店机会存在购买记录')
    elif cfg['type']==5:
        require(len(items)>=2 and 1<=items[0]<=len(cfg['params']) and not chosen,'棋盘随机机会记录损坏')
        row=cfg['params'][items[0]-1];require(items[1]==row[0],'棋盘随机结果类型损坏')
        if row[0] in (1,4,5):require(items==[items[0],row[0],row[1]],'棋盘随机结果参数损坏')
        elif row[0]==2:require(len(items)==row[1]+2 and not (Counter(items[2:])-Counter(row[2])),'棋盘随机道具损坏')
        else:require(False,'当前随机机会类型尚待处理')
    elif cfg['type'] in (4,6):require(not items and not chosen,'棋盘目标机会参数损坏')
    elif cfg['type'] in (9,14):
        from summer_board_games import validate as validate_game
        validate_game(hero,cfg)
    elif cfg['type'] in (3,7,8,10,11,12,13):require(not items and not chosen,'嵌套小游戏机会记录损坏')
    else:require(False,'当前棋盘小游戏机会尚待处理')


def open_chance(engine,cid,actor=0):
    require(actor==0 and type(cid) is int and str(cid) in board.CFG['chances'],'未知棋盘机会对象')
    hero=engine.entity(0);require(not hero['chance']['chance_id'] and not hero['tmp_item'],'棋盘机会或道具尚未完成')
    cfg=board.CFG['chances'][str(cid)];kind=cfg['type'];require(kind in SUPPORTED,'当前棋盘小游戏机会尚待处理')
    items=[];chosen=[]
    if kind in (1,2):items=_draw(*cfg['params'])
    elif kind==5:
        index=random.randrange(len(cfg['params']));row=cfg['params'][index];sub=row[0]
        require(sub in (1,2,4,5),'当前随机机会类型尚待处理')
        items=[index+1,sub]+(_draw(row[1],row[2]) if sub==2 else [row[1]])
    elif kind in (9,14):
        from summer_board_games import prepare
        items,chosen=prepare(hero,cfg)
    elif kind in (3,7,8,10,11,12,13):
        import uuid
        from summer_board_nested import open_local
        engine.state['nested_token']=uuid.uuid4().hex
        open_local(engine,cid)
    hero['chance']=dict(chance_id=cid,is_unlock=int(cfg['cost']==0),item_list=items,choose_list=chosen)
    validate(hero)
    engine.frames.append((85033,dict(chance=deepcopy(hero['chance']))))


def current(hero,cid):
    require(type(cid) is int and cid>0 and hero['chance']['chance_id']==cid,'当前没有对应棋盘机会')
    cfg=board.CFG['chances'][str(cid)];require(cfg['type'] in SUPPORTED,'当前棋盘小游戏机会尚待处理')
    return cfg,hero['chance']


def _pending(hero,items):
    require(len(hero['tmp_item'])+len(items)<=64,'棋盘待选道具过多')
    for pid in items:prop(pid)
    hero['tmp_item'].extend(items)


def apply(active,cmd,data):
    from summer_board_movement import Engine
    engine=Engine(active['chapter_id'],active['map']);hero=engine.entity(0)
    if cmd in (85020,85022,85018):
        cfg,chance=current(hero,data.get('chance_id'))
        if cmd==85020:
            require(chance['is_unlock']==0,'棋盘机会已解锁')
            require(board.attribute(hero,4)>=cfg['cost'],'棋盘金币不足')
            board.set_attribute(hero,4,board.attribute(hero,4)-cfg['cost']);chance['is_unlock']=1
        elif cmd==85018:
            pid=data.get('prop_id');item=prop(pid)
            require(cfg['type']==1 and chance['is_unlock']==1,'当前不是已开放棋盘商店')
            require(pid in chance['item_list'] and pid not in chance['choose_list'],'该道具不存在或已购买')
            require(board.attribute(hero,4)>=item['cost'],'棋盘金币不足')
            board.set_attribute(hero,4,board.attribute(hero,4)-item['cost'])
            chance['choose_list'].append(pid);_pending(hero,[pid])
        else:
            result=data.get('result');score=data.get('score',0)
            require(type(result) is int and result in (0,1) and type(score) is int and 0<=score<=0x7fffffff,'棋盘机会结果非法')
            require(result==1 or chance['is_unlock']==1,'请先解锁棋盘机会')
            require(cfg['type']!=5 or result==0,'随机机会需要确认结果')
            if cfg['type'] in (9,14):
                from summer_board_games import finish
                # Locked chance can be cancelled without granting retry attempts.
                if result==1 and chance['is_unlock']==0:require(score==0,'未开放机会不能提交点数')
                else:finish(active,engine,cfg,result,score)
            elif cfg['type'] in (3,7,8,10,11,12,13):
                from summer_board_nested import finish
                finish(active,cfg,result,score)
            else:require(score==0,'棋盘非战斗机会分数必须为零')
            if result==0:
                if cfg['type']==2:_pending(hero,chance['item_list'])
                elif cfg['type']==5:
                    _,sub,*values=chance['item_list']
                    if sub==1:board.set_attribute(hero,4,board.attribute(hero,4)+values[0])
                    elif sub==2:_pending(hero,values)
                    elif sub==4:engine.add_buff(0,values[0])
                    elif sub==5:
                        from summer_board_npc import stop
                        stop(engine,values[0])
                    else:require(False,'当前随机机会效果尚待处理')
            hero['chance']=deepcopy(EMPTY)
            if result==0 and cfg['chance_event']:
                engine.pool(cfg['chance_event'],13,[data['chance_id']])
    elif cmd in (85024,85036):
        pid=data.get('prop_id');prop(pid)
        require(pid in hero['tmp_item'],'该道具不在待选列表')
        if cmd==85024:
            replacement=data.get('replace_prop_id',0)
            require(type(replacement) is int and replacement>=0,'替换道具参数非法')
            if replacement:
                prop(replacement);require(replacement in hero['item'],'替换道具不存在')
                hero['item'].remove(replacement)
            require(len(hero['item'])<6,'棋盘道具背包已满')
            hero['item'].append(pid)
        hero['tmp_item'].remove(pid)
    elif cmd==85016:
        from summer_board_props import use
        use(active,data,engine)
    else:require(False,'未知棋盘机会操作')
    validate(hero);board._validate_map(active['map'])
    return dict(result=0),engine.frames
