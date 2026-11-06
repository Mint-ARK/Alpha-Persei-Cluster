"""Local QTE and server-rolled arena chances, isolated from world inventory."""
import random
from collections import Counter
import summer_board_service as board
from hodur_service import require


def prepare(hero,cfg):
    from summer_board_chance import _draw
    if cfg['type']==9:
        kind,rewards,*pool=cfg['params']
        return (_draw(max(row[1] for row in rewards),pool[0]) if kind==2 else []),[]
    require(cfg['type']==14,'未知棋盘内置小游戏')
    count=board.attribute(hero,13);require(1<=count<=99,'竞技初始投掷次数损坏')
    return [],[count,0,0]


def validate(hero,cfg):
    chance=hero['chance'];items,chosen=chance['item_list'],chance['choose_list']
    if cfg['type']==9:
        kind,rewards,*pool=cfg['params'];require(not chosen,'QTE机会选择记录异常')
        if kind==1:require(not items,'金币QTE存在道具结果')
        else:
            require(kind==2 and len(items)==max(row[1] for row in rewards)
                    and not (Counter(items)-Counter(pool[0])),'QTE候选道具记录损坏')
    else:
        require(not items and len(chosen)==3 and all(type(i) is int and i>=0 for i in chosen),'竞技机会记录损坏')
        goal=cfg['params'][0]
        require(chosen[0]<=99+goal and chosen[1]<=goal+11 and chosen[2] in [0]+board.CFG['arena_props'],'竞技次数/点数/道具记录损坏')


def validate_active(active):
    hero=board.player(active['map']);cfg=board.CFG['chances'].get(str(hero['chance']['chance_id']),{})
    selected=active.get('arena_prop_dice')
    is_selected=cfg.get('type')==14 and hero['chance']['choose_list'][2]==10002
    if is_selected:require(type(selected) is int and 1<=selected<=6,'竞技选骰记录损坏')
    else:require('arena_prop_dice' not in active,'竞技选骰记录未清理')


def finish(active,engine,cfg,result,score):
    hero=engine.entity(0);chance=hero['chance']
    if cfg['type']==9:
        require(result==0 and score in (1,2,3),'QTE结果类别非法')
        kind,rewards,*pool=cfg['params'];reward=next((r[1] for r in rewards if r[0]==score),None)
        require(type(reward) is int and reward>=0,'QTE奖励配置缺失')
        if kind==1:board.set_attribute(hero,4,board.attribute(hero,4)+reward)
        else:
            from summer_board_chance import _pending
            _pending(hero,chance['item_list'][:reward])
    else:
        remaining,total,_=chance['choose_list'];goal=cfg['params'][0]
        require(score==total,'竞技提交点数不匹配')
        require((result==0 and total>=goal) or (result==1 and total<goal and remaining==0),'竞技尚未达到对应结算状态')
        if result==1:board.set_attribute(hero,13,board.attribute(hero,13)+1)
        active.pop('arena_prop_dice',None)


def use_prop(active,data,engine):
    hero=engine.entity(0);chance=hero['chance'];cfg=board.CFG['chances'][str(chance['chance_id'])]
    require(cfg['type']==14 and chance['is_unlock']==1,'竞技尚未开放')
    remain,total,used=chance['choose_list'];require(remain>0 and total<cfg['params'][0] and used==0,'本次竞技不能再使用道具')
    pid=data.get('prop_id');params=data.get('params',[])
    require(type(pid) is int and pid in board.CFG['arena_props'] and pid in hero['item'],'该道具不可用于竞技或未持有')
    require(isinstance(params,list) and all(type(v) is int for v in params),'竞技道具参数非法')
    if pid==10002:
        require(len(params)==1 and 1<=params[0]<=min(6,board.attribute(hero,1)),'竞技选择点数非法');active['arena_prop_dice']=params[0]
    else:require(pid==10001 and not params,'竞技双骰不接受参数')
    hero['item'].remove(pid);chance['choose_list'][2]=pid
    validate_active(active)


def roll(active):
    hero=board.player(active['map']);chance=hero['chance'];cfg=board.CFG['chances'].get(str(chance['chance_id']),{})
    require(cfg.get('type')==14 and chance['is_unlock']==1,'当前不是已开放竞技机会')
    remaining,total,pid=chance['choose_list'];require(remaining>0 and total<cfg['params'][0],'竞技已结束或投掷次数耗尽')
    count=2 if pid==10001 else 1;selected=active.get('arena_prop_dice')
    size=board.attribute(hero,1);require(1<=size<=6,'竞技骰子范围损坏')
    dice=[selected or random.randint(1,size) for _ in range(count)]
    # Native UI announces one additional attempt for each rolled six.
    remaining=remaining-1+dice.count(6);total+=sum(dice)
    chance['choose_list']=[remaining,total,0];active.pop('arena_prop_dice',None)
    validate(hero,cfg);validate_active(active)
    # Lua adds dice_list to its cached total. Do not send a second 85033 total.
    return dict(result=0,dice_list=dice,total_cnt=remaining),[]
