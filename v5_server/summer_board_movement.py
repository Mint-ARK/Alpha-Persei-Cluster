"""Native hex movement and deterministic event projection for Summer ChessBoard."""
from copy import deepcopy
import json
from pathlib import Path
import summer_board_service as board
from hodur_service import require

DATA=json.loads((Path(__file__).with_name('data')/'summer_board_maps.json').read_text('utf-8'))
MAPS={int(i):m for i,m in DATA['maps'].items()}
DIRECTIONS=((1,1),(0,1),(-1,0),(-1,-1),(0,-1),(1,0))
FLOOR_KEYS=('typeID','status','rotationY','banDir','banDirIsHide','enterParam','exitParam','startMoveParam','endMoveParam')
THING_KEYS=('thingTypeID','thingRotationY','thingEnterParam','thingExitParam','thingStartMoveParam','thingEndMoveParam')


def validate_active(active):
    chapter=active['chapter_id'];state=active['map'];native=MAPS[chapter]
    require(len(state['hero'])<=65,'棋盘单位数量异常')
    if 'next_npc_id' in state:
        require(board._uint(state['next_npc_id']) and state['next_npc_id']>max(h['id'] for h in state['hero']),'棋盘NPC序号损坏')
    for h in state['hero']:
        require(str(h['pos']['x'])+','+str(h['pos']['z']) in native['tiles'],'棋盘单位坐标超出原始地图')
        require(h['id']==0 or str(h['type']) in board.CFG['npcs'],'未知棋盘NPC类型')
    for kind,maximum in [('floor',len(native['extendInfo'])),('object',len(native['extendThingInfo']))]:
        positions=set()
        for change in state[kind]:
            require(isinstance(change,dict) and board._position(change.get('pos')),'棋盘地图变更坐标损坏')
            x,z=change['pos']['x'],change['pos']['z'];tag=change.get('tag')
            require((x,z) not in positions and str(x)+','+str(z) in native['tiles'],'棋盘地图变更越界或重复')
            require(type(tag) is int and (-1 if kind=='object' else 0)<=tag<=maximum,'棋盘扩展索引越界')
            require(board._uint(change.get('rotation')) and (kind!='floor' or board._uint(change.get('state'))),'棋盘变更朝向或状态损坏')
            positions.add((x,z))
    for change in state['ban_dir']:
        require(isinstance(change,dict) and board._position(change.get('pos')) and isinstance(change.get('ban_dir'),list),'棋盘禁行配置损坏')
        for item in change['ban_dir']:
            require(isinstance(item,dict) and type(item.get('direction')) is int and 0<=item['direction']<6
                    and type(item.get('is_hide')) is int and item['is_hide'] in (0,1),'棋盘禁行方向配置损坏')


def grid(chapter,state,x,z):
    native=MAPS[chapter];key=str(x)+','+str(z)
    require(key in native['tiles'],'棋盘坐标越界')
    base=native['tiles'][key];out={k:deepcopy(base.get(k,[False]*6 if k.startswith('banDir') else 0)) for k in FLOOR_KEYS+THING_KEYS}
    for change in state['floor']:
        if change['pos']==dict(x=x,z=z):
            tag=change['tag'];require(type(tag) is int and 0<=tag<=len(native['extendInfo']),'棋盘地块变更损坏')
            source=native['extendInfo'][tag-1] if tag else base
            out.update({k:deepcopy(source.get(k,[False]*6 if k.startswith('banDir') else 0)) for k in FLOOR_KEYS})
            out['rotationY']=change['rotation'];out['status']=change['state']
    for change in state['object']:
        if change['pos']==dict(x=x,z=z):
            tag=change['tag'];require(type(tag) is int and -1<=tag<=len(native['extendThingInfo']),'棋盘物件变更损坏')
            source={} if tag==-1 else native['extendThingInfo'][tag-1] if tag else base
            out.update({k:deepcopy(source.get(k,0)) for k in THING_KEYS});out['thingRotationY']=change['rotation']
    for change in state['ban_dir']:
        if change['pos']==dict(x=x,z=z):
            out['banDir']=[False]*6;out['banDirIsHide']=[False]*6
            for item in change['ban_dir']:
                require(type(item['direction']) is int and 0<=item['direction']<6,'棋盘禁行方向损坏')
                out['banDir'][item['direction']]=True;out['banDirIsHide'][item['direction']]=bool(item['is_hide'])
    out.update(x=x,z=z)
    return out


def _hex(tile):
    cfg=board.CFG['hexes'].get(str(tile['typeID']));require(cfg is not None,'未知棋盘地块类型')
    return cfg


def neighbors(chapter,state,hero):
    pos=hero['pos'];source=grid(chapter,state,pos['x'],pos['z']);out=[]
    for direction,(dx,dz) in enumerate(DIRECTIONS):
        x,z=pos['x']+dx,pos['z']+dz
        if source['banDir'][direction] or str(x)+','+str(z) not in MAPS[chapter]['tiles']:continue
        target=grid(chapter,state,x,z)
        thing=board.CFG['things'].get(str(target['thingTypeID']))
        require(not target['thingTypeID'] or thing is not None,'未知棋盘物件类型')
        if _hex(target)['walkable']==1 and not (thing and thing['fix_walkable']==1):out.append((x,z,direction))
    return out


def _value(value):
    if type(value) is int:return dict(type=1,integer=value)
    require(isinstance(value,list),'未知棋盘事件参数')
    if all(type(i) is int for i in value):return dict(type=2,array=value)
    require(all(isinstance(row,list) and all(type(i) is int for i in row) for row in value),'未知棋盘事件嵌套参数')
    return dict(type=3,array2=[dict(array=row) for row in value])


class Engine:
    """Queue exactly the Lua-consumed timing while saving its resulting state."""
    def __init__(self,chapter,state):
        self.chapter=chapter;self.state=state;self.frames=[];self.steps=0;self.depth=0
        state.setdefault('next_npc_id',max(h['id'] for h in state['hero'])+1)

    def alive(self,ref):return any(h['id']==ref for h in self.state['hero'])

    def add_npc(self,npc,x,z,direction):
        require(str(npc) in board.CFG['npcs'] and type(direction) is int and 0<=direction<6,'未知棋盘NPC或朝向')
        grid(self.chapter,self.state,x,z)
        if len(self.state['hero'])>=65:return 0
        ref=self.state['next_npc_id'];require(ref<0xffffffff,'棋盘NPC序号溢出');self.state['next_npc_id']+=1
        self.state['hero'].append(dict(id=ref,type=npc,pos=dict(x=x,z=z),direction=direction,
            attribute=[dict(id=i,value=v) for i,v in board.CFG['npcs'][str(npc)]['init_attribute_list']],
            item=[],buff=[],tmp_item=[],use_item=0,chance=dict(chance_id=0,is_unlock=0,item_list=[],choose_list=[])))
        return ref

    def delete_npc(self,ref):
        require(type(ref) is int and ref>0,'不能移除棋盘玩家')
        self.state['hero']=[h for h in self.state['hero'] if h['id']!=ref]

    def entity(self,ref):
        found=next((h for h in self.state['hero'] if h['id']==ref),None)
        require(found is not None,'棋盘事件引用不存在的单位');return found

    def current(self,ref=0):
        pos=self.entity(ref)['pos'];return grid(self.chapter,self.state,pos['x'],pos['z'])

    def change(self,kind,x,z,tag,inherit=False):
        previous=grid(self.chapter,self.state,x,z);native=MAPS[self.chapter]
        if kind=='floor':
            require(type(tag) is int and 0<=tag<=len(native['extendInfo']),'未知棋盘扩展地块')
            source=native['extendInfo'][tag-1] if tag else native['tiles'][str(x)+','+str(z)]
            rotation=previous['rotationY'] if inherit else source.get('rotationY',0)
            change=dict(pos=dict(x=x,z=z),tag=tag,state=source.get('status',0),rotation=rotation)
        else:
            require(type(tag) is int and -1<=tag<=len(native['extendThingInfo']),'未知棋盘扩展物件')
            source={} if tag==-1 else native['extendThingInfo'][tag-1] if tag else native['tiles'][str(x)+','+str(z)]
            change=dict(pos=dict(x=x,z=z),tag=tag,rotation=source.get('thingRotationY',0))
        self.state[kind]=[r for r in self.state[kind] if r['pos']!=change['pos']]+[change]
        current=grid(self.chapter,self.state,x,z)
        blocked=(_hex(current)['walkable']==0 if kind=='floor' else
                 board.CFG['things'].get(str(current['thingTypeID']),{}).get('fix_walkable')==1)
        if blocked:
            for h in list(self.state['hero']):
                if h['pos']!=dict(x=x,z=z):continue
                if h['id']==0:
                    c=board.LEVELS[self.chapter];h['pos']=dict(x=c['init_pos'][0],z=c['init_pos'][1]);h['direction']=c['init_dir']
                else:self.state['hero'].remove(h)

    def remove_buff(self,ref,bid):
        hero=self.entity(ref);old=next((b for b in hero['buff'] if b['id']==bid),None)
        if old is None:return
        hero['buff'].remove(old);pool=board.CFG['buffs'][str(bid)]['destory_event']
        if pool:self.pool(pool,15,[bid],actor=ref)

    def add_buff(self,ref,bid):
        require(str(bid) in board.CFG['buffs'],'未知棋盘Buff');hero=self.entity(ref);cfg=board.CFG['buffs'][str(bid)]
        if board.attribute(hero,9)>=1 and cfg['buff_type']==2:return
        if cfg['clash']==1:
            for b in list(hero['buff']):
                if board.CFG['buffs'][str(b['id'])]['clash']==1:self.remove_buff(ref,b['id'])
        hero['buff']=[b for b in hero['buff'] if b['id']!=bid]+[dict(id=bid,duration=0,buff_effect=[])]
        if cfg['add_event']:self.pool(cfg['add_event'],16,[bid],actor=ref)

    def pool(self,pool,timing,params=None,*,actor=0,other=-1,tile=None):
        self.depth+=1
        try:
            require(self.depth<=32,'棋盘事件嵌套过深')
            return self._pool(pool,timing,params,actor=actor,other=other,tile=tile)
        finally:self.depth-=1

    def _pool(self,pool,timing,params=None,*,actor=0,other=-1,tile=None):
        cfg=board.CFG['events'].get(str(pool));require(cfg is not None,'棋盘事件配置缺失：'+str(pool))
        rows=cfg['player_params' if actor==0 else 'npc_params']
        if not rows:return
        if cfg['type']==2:
            from summer_board_chance import open_chance
            require(len(rows)==1,'棋盘机会入口数量异常')
            open_chance(self,rows[0],actor)
            return
        require(cfg['type']==1,'未知棋盘事件类型：'+str(pool))
        packet=dict(event=dict(trigger_type=timing,trigger_type_param=params or [],event_pool_id=pool,
            trigger_player_a=actor,trigger_player_b=other,event_list=[]))
        self.frames.append((85031,packet))
        tile=tile or self.current(actor)
        for row in rows:
            self.steps+=1;require(self.steps<=512,'棋盘单次事件数量过多')
            kind,*args=row;values=[];target=dict(event=[_value(v) for v in row],params=values);packet['event']['event_list'].append(target)
            if kind in (10001,10009,10010,10014,10015,10018,10021):continue
            if kind==10002:
                values.append(self.add_npc(*args))
            elif kind in (10003,10004):self.change('floor' if kind==10003 else 'object',*args)
            elif kind in (10005,10006):self.change('floor' if kind==10005 else 'object',tile['x'],tile['z'],args[0])
            elif kind in (10007,10017):
                pos=dict(x=tile['x'],z=tile['z']) if kind==10007 else dict(x=args[0],z=args[1])
                match=next((h for h in self.state['hero'] if h['id']!=0 and h['pos']==pos),None)
                if match:self.delete_npc(match['id'])
            elif kind==10008:
                require(actor>0 and other>0 and actor!=other,'棋盘合成对象非法')
                first=self.entity(actor);second=self.entity(other);require(first['pos']==second['pos'],'棋盘合成位置不一致')
                npc=args[0];require(str(npc) in board.CFG['npcs'],'未知棋盘合成类型')
                survivor=next((h for h in (first,second) if h['type']==npc),None)
                if survivor:
                    ref=survivor['id'];self.delete_npc(other if ref==actor else actor)
                else:
                    direction=first['direction'];self.delete_npc(actor);self.delete_npc(other)
                    ref=self.add_npc(npc,tile['x'],tile['z'],direction)
                values.extend([ref,self.entity(ref)['direction']])
            elif kind==10012:
                require(str(args[0]) in board.CFG['tasks'],'未知棋盘局内任务')
                if not any(t['id']==args[0] for t in self.state['task']):self.state['task'].append(dict(id=args[0],progress=0))
            elif kind==10013:
                require(any(t['condition_type']==args[0] for t in board.CFG['tasks'].values()),'未知棋盘任务条件')
                for task in self.state['task']:
                    if board.CFG['tasks'][str(task['id'])]['condition_type']==args[0]:task['progress']+=args[1]
            elif kind in (10019,10020):self.change('object',*(args[:2] if kind==10019 else [tile['x'],tile['z']]),-1)
            elif kind==20001:
                ref=actor if args[0]==1 else other;entity=self.entity(ref);_,x,z,direction=args
                require(_hex(grid(self.chapter,self.state,x,z))['walkable']==1,'棋盘传送目标不可行走')
                entity['pos']=dict(x=x,z=z);entity['direction']=direction
            elif kind==20002:
                ref=actor if args[0]==1 else other;entity=self.entity(ref);require(args[1] in board.ATTRIBUTES,'未知棋盘属性')
                board.set_attribute(entity,args[1],board.attribute(entity,args[1])+args[2])
            elif kind==20004:
                ref=actor if args[0]==1 else other
                for buff in list(self.entity(ref)['buff']):
                    if board.CFG['buffs'][str(buff['id'])]['buff_type']==2:self.remove_buff(ref,buff['id'])
            elif kind in (20005,20006):
                ref=actor if args[0]==1 else other
                (self.add_buff if kind==20005 else self.remove_buff)(ref,args[1])
            else:require(False,'当前棋盘事件暂不支持：'+str(kind))

    def trigger(self,tile,key,timing,*,actor=0):
        if tile[key]:self.pool(tile[key],timing,[tile['x'],tile['z']],actor=actor,tile=tile)

    def buff_effect(self,ref,when,other=-1):
        timings={1:21,2:22,3:23,4:24,5:14}
        for buff in list(self.entity(ref)['buff']):
            if not self.alive(ref):break
            if buff not in self.entity(ref)['buff']:continue
            for effect in board.CFG['buffs'][str(buff['id'])]['buff_efffect']:
                cfg=board.CFG['effects'][str(effect)]
                if cfg['trigger_time']!=when:continue
                used=next((r['num'] for r in buff['buff_effect'] if r['id']==effect),0)
                actor,target=(other,ref) if when in (1,2) else (ref,other)
                if cfg['trigger_count']==-1 or used<cfg['trigger_count']:
                    self.pool(cfg['event'],timings[when],[effect],actor=actor,other=target)
                buff['buff_effect']=[r for r in buff['buff_effect'] if r['id']!=effect]+[dict(id=effect,num=used+1)]

    def buff_step(self,ref=0):self.buff_effect(ref,5)

    def buff_turn(self,ref=0):
        original=list(self.entity(ref)['buff'])
        for b in original:b['duration']+=1
        for b in original:
            if b not in self.entity(ref)['buff']:continue
            cfg=board.CFG['buffs'][str(b['id'])]
            if cfg['updateround']>0 and b['duration']%cfg['updateround']==0 and cfg['turn_event']:
                self.pool(cfg['turn_event'],17,[b['id']],actor=ref)
            if cfg['remainround']>0 and cfg['remainround']<=b['duration']:self.remove_buff(ref,b['id'])


def move(active,pos):
    chapter=active['chapter_id'];state=active['map'];hero=board.player(state)
    require(board._position(pos),'棋盘移动坐标非法')
    require(board.attribute(hero,8) in (1,2,3) and board.attribute(hero,5)>0 and board.attribute(hero,7)==0,'当前棋盘不可移动')
    require(hero['chance']['chance_id']==0 and not hero['tmp_item'],'请先完成当前机会或道具选择')
    target=next((r for r in neighbors(chapter,state,hero) if r[:2]==(pos['x'],pos['z'])),None)
    require(target is not None,'目标不属于可行走相邻地块')
    engine=Engine(chapter,state);source=engine.current();destination=grid(chapter,state,pos['x'],pos['z'])
    if _hex(destination)['updatable']==1:
        hero['pos']=dict(pos);hero['direction']=target[2];board.set_attribute(hero,8,2)
        return engine.frames
    if board.attribute(hero,8)==1:
        engine.trigger(source,'thingStartMoveParam',26);engine.trigger(source,'startMoveParam',4)
    engine.trigger(source,'thingExitParam',25);engine.trigger(source,'exitParam',3)
    hero['pos']=dict(pos);hero['direction']=target[2];board.set_attribute(hero,8,2);board.set_attribute(hero,5,board.attribute(hero,5)-1)
    from summer_board_npc import hero_encounter
    hero_encounter(engine,destination)
    engine.trigger(destination,'thingEnterParam',19);engine.trigger(destination,'enterParam',2)
    if board.attribute(hero,5)<=0:
        board.set_attribute(hero,8,3);engine.trigger(destination,'thingEndMoveParam',27);engine.trigger(destination,'endMoveParam',5)
    engine.buff_step()
    board._validate_map(state)
    return engine.frames


def end_turn(active):
    chapter=active['chapter_id'];state=active['map'];hero=board.player(state)
    require(board.attribute(hero,8)==3 and board.attribute(hero,5)==0,'棋盘移动尚未结束或回合已处理')
    require(hero['chance']['chance_id']==0 and not hero['tmp_item'],'请先完成当前机会或道具选择')
    engine=Engine(chapter,state);engine.buff_turn();board.set_attribute(hero,8,0)
    if hero['use_item']:
        prop=board.CFG['props'][str(hero['use_item'])]
        require(prop['type'] in (10001,10002,10003,10004,10005,10006,10007,10008,10009,10010),'未知棋盘局内道具回合')
        if prop['type']==10006:
            key,delta=prop['params'];board.set_attribute(hero,key,board.attribute(hero,key)-delta)
        if prop['type']==10004:active.pop('prop_dice',None)
        hero['use_item']=0
    from summer_board_npc import run_turn
    paths=run_turn(engine)
    state['turn']+=1
    for turn,pool in board.LEVELS[chapter]['level_event']:
        if turn==state['turn']:engine.pool(pool,20,[turn])
    board._validate_map(state)
    return paths,engine.frames
