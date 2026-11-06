"""Summer ChessBoard bootstrap, account-scoped resume and explicit run exit.

This is phase one. Tile events, movement, chances and task settlement are
implemented separately; they must not fall through to successful empty ACKs.
"""
from copy import deepcopy
import json
from pathlib import Path
import random
import uuid
from core import Operation, operation
from hodur_service import require

CFG=json.loads(Path(__file__).with_name('summer_board_contract.json').read_text('utf-8'))
LEVELS={int(i):v for i,v in CFG['levels'].items()}
ATTRIBUTES={int(i):v for i,v in CFG['attributes'].items()}
STATE_KEY='summer_board_v1'


def _uint(value):return type(value) is int and 0<=value<=0xffffffff


def _position(value):
    return isinstance(value,dict) and all(type(value.get(k)) is int and -0x80000000<=value[k]<=0x7fffffff for k in ('x','z'))


def _validate_map(value):
    require(isinstance(value,dict) and _uint(value.get('turn')),'棋盘地图记录损坏')
    require(all(isinstance(value.get(k),list) for k in ('hero','floor','object','ban_dir','task')),'棋盘地图列表损坏')
    ids=[]
    for h in value['hero']:
        require(isinstance(h,dict) and _uint(h.get('id')) and _uint(h.get('type')) and _position(h.get('pos')),'棋盘单位记录损坏')
        require(type(h.get('direction')) is int and 0<=h['direction']<=5 and _uint(h.get('use_item')),'棋盘单位朝向或道具损坏')
        require(all(isinstance(h.get(k),list) for k in ('attribute','item','buff','tmp_item')) and isinstance(h.get('chance'),dict),'棋盘单位状态损坏')
        require(all(_uint(i) for i in h['item']+h['tmp_item']),'棋盘道具记录损坏')
        chance=h['chance']
        require(_uint(chance.get('chance_id')) and type(chance.get('is_unlock')) is int and chance['is_unlock'] in (0,1)
                and all(isinstance(chance.get(k),list) and all(type(i) is int and -0x80000000<=i<=0x7fffffff for i in chance[k])
                        for k in ('item_list','choose_list')),'棋盘机会记录损坏')
        for buff in h['buff']:
            require(isinstance(buff,dict) and str(buff.get('id')) in CFG['buffs'] and _uint(buff.get('duration'))
                    and isinstance(buff.get('buff_effect'),list),'棋盘Buff记录损坏')
            require(all(isinstance(e,dict) and _uint(e.get('id')) and _uint(e.get('num')) for e in buff['buff_effect']),'棋盘Buff效果损坏')
        seen=[]
        for a in h['attribute']:
            require(isinstance(a,dict) and type(a.get('id')) is int and a['id'] in ATTRIBUTES and type(a.get('value')) is int
                    and -0x80000000<=a['value']<=0x7fffffff,'棋盘属性记录损坏')
            seen.append(a['id'])
        require(len(seen)==len(set(seen)),'棋盘属性重复')
        ids.append(h['id'])
    require(ids.count(0)==1 and len(ids)==len(set(ids)),'棋盘玩家或NPC编号损坏')
    for task in value['task']:
        require(isinstance(task,dict) and str(task.get('id')) in CFG['tasks'] and _uint(task.get('progress')),'棋盘任务记录损坏')
    require(len({t['id'] for t in value['task']})==len(value['task']),'棋盘任务重复')


def read(db,uid):
    row=db.get('users',uid);require(row is not None,'账号不存在')
    extra=json.loads(row.get('extra') or '{}');require(isinstance(extra,dict),'账号扩展记录损坏')
    state=extra.get(STATE_KEY,dict(version=1,completed=[],active=None))
    require(isinstance(state,dict) and type(state.get('version')) is int and state['version']==1,'棋盘存档版本损坏')
    done=state.get('completed');require(isinstance(done,list) and all(type(i) is int and i in LEVELS for i in done),'棋盘历史记录损坏')
    require(len(done)==len(set(done)),'棋盘历史关卡重复')
    finished=state.get('finished_tasks',[])
    require(isinstance(finished,list) and all(type(i) is int and str(i) in CFG['tasks'] for i in finished) and len(finished)==len(set(finished)),'棋盘历史任务损坏')
    active=state.get('active')
    if active is not None:
        require(isinstance(active,dict) and type(active.get('chapter_id')) is int and active['chapter_id'] in LEVELS
                and isinstance(active.get('run_id'),str) and bool(active['run_id']),'棋盘当前对局损坏')
        _validate_map(active.get('map'))
        from summer_board_chance import validate
        for hero in active['map']['hero']:validate(hero)
        from summer_board_movement import validate_active
        validate_active(active)
        from summer_board_props import validate as validate_props
        validate_props(active)
        from summer_board_games import validate_active as validate_games
        validate_games(active)
        from summer_board_nested import validate as validate_nested
        validate_nested(active)
        tasks_done=active.get('tasks_done',[])
        require(isinstance(tasks_done,list) and all(type(i) is int and i in CFG['map_tasks'][str(active['chapter_id'])] for i in tasks_done)
                and len(tasks_done)==len(set(tasks_done)),'棋盘已提交任务损坏')
    return extra,state


def save(db,uid,extra,state):
    extra[STATE_KEY]=state
    db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra,ensure_ascii=False),uid))


def system_payload(db,uid):
    _,state=read(db,uid)
    return dict(chapter_id=state['active']['chapter_id'] if state['active'] else 0)


def unlocked(state,chapter):
    cid=LEVELS[chapter]['unlock_condition']
    return not cid or all(i in state['completed'] for i in CFG['conditions'][str(cid)]['params'])


def player(board):return next(h for h in board['hero'] if h['id']==0)


def attribute(hero,key):
    value=next((a['value'] for a in hero['attribute'] if a['id']==key),ATTRIBUTES[key]['default'])
    # Native GetAttribute clamps its read to min; the stored value stays raw.
    return max(ATTRIBUTES[key]['min'],value)


def set_attribute(hero,key,value):
    value=max(0,value)
    require(value<=0x7fffffff,'棋盘属性超出范围')
    hero['attribute']=[a for a in hero['attribute'] if a['id']!=key]+[dict(id=key,value=value)]


def new_map(chapter):
    c=LEVELS[chapter]
    hero=dict(id=0,type=0,pos=dict(x=c['init_pos'][0],z=c['init_pos'][1]),direction=c['init_dir'],
              attribute=[dict(id=i,value=a['default']) for i,a in ATTRIBUTES.items()],item=[],buff=[],use_item=0,
              chance=dict(chance_id=0,is_unlock=0,item_list=[],choose_list=[]),tmp_item=[])
    return dict(turn=0,hero=[hero],floor=[],object=[],ban_dir=[],task=[])


def _event_packet(pool,rows,*,timing=1,params=None):
    return dict(event=dict(trigger_type=timing,trigger_type_param=params or [],event_pool_id=pool,trigger_player_a=0,trigger_player_b=-1,
                          event_list=[dict(event=[dict(type=1,integer=v) for v in row],params=[]) for row in rows]))


def bootstrap(chapter,board):
    """Apply native initial effects to durable state, queue client effects once.

    Send the pre-effect map with FIRST_MAP=0. The Lua first-enter callback then
    applies these events locally; reconnects receive the post-effect snapshot.
    """
    c=LEVELS[chapter];hero=player(board);packets=[]
    def apply_pool(pool,*,timing=1,params=None):
        rows=CFG['events'][str(pool)]['player_params'];out=[];children=[]
        for row in rows:
            kind,*args=row
            if kind==20002:
                require(args[0]==1 and args[1] in ATTRIBUTES,'未知棋盘初始化属性对象')
                set_attribute(hero,args[1],attribute(hero,args[1])+args[2]);out.append(row)
            elif kind==10012:
                require(str(args[0]) in CFG['tasks'],'未知棋盘初始化任务')
                if not any(t['id']==args[0] for t in board['task']):board['task'].append(dict(id=args[0],progress=0))
                out.append(row)
            elif kind==10002:
                npc_type,x,z,direction=args;ref=max(h['id'] for h in board['hero'])+1
                board['hero'].append(dict(id=ref,type=npc_type,pos=dict(x=x,z=z),direction=direction,
                    attribute=[dict(id=i,value=v) for i,v in CFG['npcs'][str(npc_type)]['init_attribute_list']],
                    item=[],buff=[],use_item=0,chance=dict(chance_id=0,is_unlock=0,item_list=[],choose_list=[]),tmp_item=[]))
                out.append(row)
                # FIRST_ENTER_MAP's10002 reads params[1] as the assigned NPC ID.
                continue
            elif kind==20005:
                require(args[0]==1 and str(args[1]) in CFG['buffs'],'未知棋盘初始化Buff')
                buff=args[1];hero['buff'].append(dict(id=buff,duration=0,buff_effect=[]));out.append(row)
                event=CFG['buffs'][str(buff)]['add_event']
                if event:children.append((event,buff))
            elif kind in (10001,10015):out.append(row) # Story and camera, no server-side reward/progress.
            else:raise ValueError('Unsupported native bootstrap event '+str(kind))
        packet=_event_packet(pool,out,timing=timing,params=params)
        npc_ids=iter(h['id'] for h in board['hero'] if h['id']!=0)
        for source,target in zip(out,packet['event']['event_list']):
            if source[0]==10002:target['params']=[next(npc_ids)]
        packets.append(packet)
        for event,buff in children:apply_pool(event,timing=16,params=[buff])
    apply_pool(c['eventpool_id'])
    set_attribute(hero,6,1)
    _validate_map(board)
    return packets


class BoardOperation(Operation):
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        extra,state=read(ctx.db,self.uid)
        from summer_board_progress import persist,finish_task
        if self.cmd==85000:
            chapter=data.get('chapter_id')
            require(type(chapter) is int and chapter in LEVELS,'未知夏日竞技棋盘')
            require(unlocked(state,chapter),'棋盘前置区域尚未完成')
            if state['active']:
                require(state['active']['chapter_id']==chapter,'请先结束当前棋盘探索')
                native={k:state['active']['map'][k] for k in ('turn','hero','floor','object','ban_dir','task')}
                return dict(result=0,map=native),[]
            board=new_map(chapter);wire_map=deepcopy(board);events=bootstrap(chapter,board)
            state['active']=dict(chapter_id=chapter,run_id=uuid.uuid4().hex,map=board)
            persist(ctx,self.uid,extra,state,events)
            return dict(result=0,map=wire_map),events
        require(state['active'] is not None,'当前没有棋盘探索')
        if self.cmd==85026:
            result=finish_task(state['active'],data.get('task_id'));persist(ctx,self.uid,extra,state,result[1])
            return result
        if self.cmd==85038:
            from summer_board_games import roll
            result=roll(state['active']);persist(ctx,self.uid,extra,state,result[1])
            return result
        if self.cmd in (85016,85018,85020,85022,85024,85036):
            from summer_board_chance import apply
            result=apply(state['active'],self.cmd,data)
            persist(ctx,self.uid,extra,state,result[1])
            return result
        if self.cmd in (85010,85014):
            from summer_board_movement import move, end_turn
            if self.cmd==85010:events=move(state['active'],data.get('next_pos'));reply=dict(result=0)
            else:
                paths,events=end_turn(state['active']);reply=dict(result=0,npc_path_list=paths)
            persist(ctx,self.uid,extra,state,events)
            return reply,events
        if self.cmd==85028:
            # Native ExploreView uses this for abandonment too. It clears the
            # run, never implies a clear and never grants chapter/task rewards.
            events=[];persist(ctx,self.uid,extra,state,events)
            state['active']=None;save(ctx.db,self.uid,extra,state)
            return dict(result=0),events
        if self.cmd==85012:
            hero=player(state['active']['map'])
            require(attribute(hero,8)==0 and attribute(hero,5)==0 and attribute(hero,7)==0,'当前无法掷骰')
            require(hero['chance']['chance_id']==0 and not hero['tmp_item'],'请先完成当前棋盘事件或道具选择')
            count=attribute(hero,2);size=attribute(hero,1);fixed=attribute(hero,3)
            require(1<=count<=2 and 1<=size<=6 and 0<=fixed<=6,'棋盘骰子参数损坏')
            selected=state['active'].get('prop_dice')
            dice=[selected or fixed or random.randint(1,size) for _ in range(count)]
            set_attribute(hero,5,sum(dice));set_attribute(hero,8,1)
            save(ctx.db,self.uid,extra,state)
            return dict(result=0,dice_list=dice),[]
        require(False,'当前棋盘操作不可用')

    def respond(self,result,data,ctx):
        from middleware import DownFrame
        reply,events=result;frames=[]
        for event in events:
            sc,event=event if isinstance(event,tuple) else (85031,event)
            packet=ctx.codec_encode('sc_'+str(sc),event)
            require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'棋盘初始化事件为空')
            frames.append(DownFrame(sc,bytes(packet)))
        packet=ctx.codec_encode('sc_'+str(self.sc),reply)
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'棋盘回执为空')
        return frames+[DownFrame(self.sc,bytes(packet))]


@operation
class BoardEnterOp(BoardOperation):cmd,sc=85000,85001
@operation
class BoardRollOp(BoardOperation):cmd,sc=85012,85013
@operation
class BoardMoveOp(BoardOperation):cmd,sc=85010,85011
@operation
class BoardEndTurnOp(BoardOperation):cmd,sc=85014,85015
@operation
class BoardExitOp(BoardOperation):cmd,sc=85028,85029


# Future phases replace these guards with their actual event/state handlers.
# Empty ACK success would mutate Lua while leaving the authoritative run stale.
for _cmd in (85016,85018,85020,85022,85024,85036):
    operation(type('BoardChance'+str(_cmd),(BoardOperation,),dict(cmd=_cmd,sc=_cmd+1)))
@operation
class BoardArenaRollOp(BoardOperation):cmd,sc=85038,85039
@operation
class BoardFinishTaskOp(BoardOperation):cmd,sc=85026,85027
