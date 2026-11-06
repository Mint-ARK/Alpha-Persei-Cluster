"""Native first-activity AutoChess props; no DB writes or normal chess purchase hooks.

Candidate weights are not in Lua: supported eligible items use equal probability.
The caller persists the returned session in its operation transaction.
"""
from copy import deepcopy
import json
from pathlib import Path
import random
from core import OperationError

SETTINGS = json.loads(Path(__file__).with_name('autochess_settings.json').read_text('utf-8'))['settings']
ACTIVITY = 3740601
SUPPORTED = frozenset((101,102,103,104,201,202,203,204,205,301,302,303,304,401,402,404,405))


def setting(name, default=None):
    return SETTINGS.get(name, default)


def _normal_shop(s):
    return [i for i in s.get('shop_items',[]) if i.get('shop_type',0)==0]


def _pieces(s):
    return s.get('board_chess',[])+s.get('bench_chess',[])


def _buff(svc,piece,bid):
    uid=svc._allocate_uid()
    return dict(unique_id=uid,buff_id=bid,owner_type=0,source_type=1,
                source_uid=piece['unique_id'],source_cfg_id=piece['chess_id'])


def generate_props(svc, session, count=2):
    # Lua shop is shown once stage 202 is selected; individual item gates are 201.
    if int(session.get('stage_id',0)) < SETTINGS['auto_chess_item_unlock'][0]:return []
    activity=int(session.get('activity_id',ACTIVITY));level=int(session.get('shop_level',1))
    pool=[c for c in svc.cfg.get('items',{}).values() if c['id'] in SUPPORTED
          and activity in c.get('activity_id',[]) and c.get('unlock_stage',0)<=session['stage_id']
          and 0<c.get('star',0)<=level and c.get('shop_price',0)>0]
    if not pool:raise OperationError(2,'当前关卡没有可用的道具商品')
    return [dict(shop_unique_id=svc._allocate_uid(session),id=random.choice(pool)['id'],
                 shop_type=1,index=i+1,is_lock=0,atk=0,hp=0,exp=1,star=1) for i in range(count)]


def buy_prop(svc,ctx,uid,session,item,to_index):
    """Plan against a copy; invalid targets/cost leave the caller's copy unchanged."""
    s=deepcopy(session)
    actual=next((i for i in s.get('shop_items',[]) if i.get('shop_unique_id')==item.get('shop_unique_id') and i.get('shop_type')==1),None)
    if actual is None:raise OperationError(2,'道具商品不存在')
    iid=int(actual['id']);cfg=svc.get_item_cfg(iid)
    if (not cfg or iid not in SUPPORTED or int(s.get('activity_id',ACTIVITY)) not in cfg.get('activity_id',[])
            or cfg.get('unlock_stage',0)>s.get('stage_id',0)):
        raise OperationError(2,'道具不属于当前玩法或尚未解锁')
    free=int(s.get('free_prop',0));price=0 if free>0 else int(cfg['shop_price'])
    if int(s.get('gold',0))<price:raise OperationError(402,'局内金币不足')
    from autochess_preparation import consume_prop_repeats, trigger
    extra=consume_prop_repeats(svc,s)
    _apply_prop_effect(svc,s,iid,cfg,to_index)
    # A repeated effect cannot resurrect an already consumed target or create
    # an invisible reward slot. Keep the successful first execution in that case.
    for _ in range(extra):
        repeated=deepcopy(s)
        try:
            _apply_prop_effect(svc,repeated,iid,cfg,to_index)
        except OperationError:
            continue
        s=repeated
    trigger(svc,s,4)
    effects=s.setdefault('prop_effects',{})
    effects['used_count']=int(effects.get('used_count',0))+1
    s['gold']=int(s['gold'])-price
    if free>0:s['free_prop']=free-1
    s['shop_items']=[i for i in s['shop_items'] if i.get('shop_unique_id')!=item['shop_unique_id']]
    session.clear();session.update(s)
    return session


def _apply_prop_effect(svc,s,iid,cfg,to_index):
    target=None
    if cfg['relevant_type'] in (6,15):
        target=next((p for p in s.get('board_chess',[]) if p.get('index')==to_index),None)
        if not target:raise OperationError(2,'请选择场上的有效棋子')
        if cfg['relevant_type']==15 and svc.get_chess_cfg(target['chess_id']).get('type') in (1,3):
            raise OperationError(2,'此道具不能作用于管理员棋子')
    elif int(to_index)!=0:
        raise OperationError(2,'非定向道具不接受棋子位置')
    effects=s.setdefault('prop_effects',{})
    normals=_normal_shop(s)
    all_pieces=_pieces(s)
    if iid in (101,103,104,304):
        params=svc.get_buff_cfg(cfg['buff_list'][0])['param']
        target['atk']=int(target.get('atk',0))+params[0][1]
        target['hp']=int(target.get('hp',0))+params[1][1]
    elif iid in (203,402):
        if not all_pieces:raise OperationError(2,'没有可使用道具的己方棋子')
        params=svc.get_buff_cfg(cfg['buff_list'][0])['param']
        for p in all_pieces:p['atk']=int(p.get('atk',0))+params[0][1];p['hp']=int(p.get('hp',0))+params[1][1]
    elif iid==102:
        if not normals:raise OperationError(2,'商店中没有可取得的棋子')
        # The native view renders exactly two reward slots. Reuse holes after
        # an earlier reward was taken instead of creating an invisible index 3.
        occupied={int(i['index']) for i in s['shop_items'] if i.get('shop_type')==2}
        reward_index=next((i for i in (1,2) if i not in occupied),None)
        if reward_index is None:raise OperationError(2,'请先领取奖励区中的棋子')
        taken=random.choice(normals)
        taken['shop_type']=2;taken['is_lock']=0
        taken['index']=reward_index
        taken['atk']=int(taken.get('atk',0))+1;taken['hp']=int(taken.get('hp',0))+1
    elif iid==201:
        effects['restart_money_bonus']=int(effects.get('restart_money_bonus',0))+1
        effects['money_ceiling_bonus']=int(effects.get('money_ceiling_bonus',0))+1
    elif iid==202:
        if not normals:raise OperationError(2,'商店中没有可强化的棋子')
        for p in normals:p['atk']=int(p.get('atk',0))+1;p['hp']=int(p.get('hp',0))+1
    elif iid==204:
        level=min(4,int(s.get('shop_level',1))+1)
        activity=int(s.get('activity_id',ACTIVITY))
        pool=[c for c in svc.cfg['chess'].values() if c.get('type')==0 and c.get('star')==level
              and activity in c.get('activity_id',[]) and c.get('shop_price',0)>0]
        if not pool:raise OperationError(2,'缺少高一阶棋子配置')
        base_exp=svc._base_chess_exp(s)
        generated=svc.generate_shop_items(shop_level=int(s.get('shop_level',1)),activity_id=activity,base_exp=base_exp)
        generated=[i for i in generated if i.get('shop_type',0)==0]
        if not generated:raise OperationError(2,'商店生成失败')
        chosen=random.choice(pool);slot=generated[0]
        slot.update(id=chosen['id'],atk=chosen['body'][0],hp=chosen['body'][1],
                    exp=base_exp,star=1+sum(base_exp>=int(v) for v in chosen.get('exp',[])))
        for generated_item in generated:
            svc._apply_chess_bonus(s,generated_item)
        # A prop refresh replaces the normal stock, leaving props/rewards intact.
        s['shop_items']=[i for i in s['shop_items'] if i.get('shop_type',0)!=0]+generated
    elif iid==205:
        effects['next_round_gold']=int(effects.get('next_round_gold',0))+2
    elif iid==301:
        bonus=effects.get('team_bonus',[0,0]);effects['team_bonus']=[int(bonus[0])+1,int(bonus[1])+1]
    elif iid==302:
        cfg_target=svc.get_chess_cfg(target['chess_id']);thresholds=cfg_target.get('exp',[])
        if not thresholds or svc._piece_exp(target)>=max(thresholds):raise OperationError(2,'棋子已满级')
        target['exp']=svc._piece_exp(target)+1;target['star']=svc._piece_level(target)
    elif iid==303:
        old=svc.get_chess_cfg(target['chess_id']);wanted=int(old['star'])+1
        pool=[c for c in svc.cfg['chess'].values() if c.get('type')==0 and c.get('star')==wanted
              and int(s.get('activity_id',ACTIVITY)) in c.get('activity_id',[]) and c.get('shop_price',0)>0]
        if not pool:raise OperationError(2,'棋子已达最高稀有度')
        new=random.choice(pool)
        old_bonus=s.get('chess_bonus',{}).get(str(old['id']),[0,0])
        target['chess_id']=new['id']
        target['atk']=int(target.get('atk',0))-int(old['body'][0])-int(old_bonus[0])+int(new['body'][0])
        target['hp']=int(target.get('hp',0))-int(old['body'][1])-int(old_bonus[1])+int(new['body'][1])
        svc._apply_chess_bonus(s,target)
        target['star']=svc._piece_level(target)
    elif iid==401:
        # CheckPropBuff tests configured inherent buffs as well as added buffs.
        shields=set(svc.get_buff_cfg(401).get('repeated',[]))
        levels=svc.get_chess_cfg(target['chess_id']).get('level_buffs',[])
        innate=levels[min(svc._piece_level(target),len(levels))-1] if levels else []
        active=set(innate)|{b.get('buff_id') for b in target.get('buff_list',[])+s.get('buff_list',[])}
        if active & shields:
            raise OperationError(2,'棋子已有相同护盾效果')
        target.setdefault('buff_list',[]).append(_buff(svc,target,401))
    elif iid==404:
        candidates=[p for p in all_pieces if p is not target]
        if not candidates:raise OperationError(2,'没有可继承属性的其他棋子')
        recipient=random.choice(candidates)
        recipient['atk']=int(recipient.get('atk',0))+int(target.get('atk',0))
        recipient['hp']=int(recipient.get('hp',0))+int(target.get('hp',0))
        s['board_chess'].remove(target)
    elif iid==405:
        # Lua RANDOM_SHOP_ONE explicitly counts NORMAL and REWARD stock.
        stock=[p for p in s['shop_items'] if p.get('shop_type',0) in (0,2)]
        candidates=[p for p in stock if svc._piece_exp(p)<max(svc.get_chess_cfg(p['id']).get('exp',[1]))]
        if not candidates:raise OperationError(2,'商店中没有可升级的棋子')
        p=random.choice(candidates);p['exp']=svc._piece_exp(p)+5
        # Native param is EXP+5, not a rarity/chess-ID replacement.
        p['star']=svc._piece_level(dict(p,chess_id=p['id']))


def choose_upgrade(svc,session,attr_index,buff_index):
    """Only consume server-created, persisted candidates; never ACK an absent offer.

    Creation cadence and the complete special administrator conversion remain a
    separate contract. This helper validates a supplied native offer only.
    """
    offer=session.get('hero_upgrade_info')
    if not isinstance(offer,dict):raise OperationError(2,'当前没有管理员升级机会')
    attrs={int(x['index']):x for x in offer.get('hero_upgrade_attrs',[])}
    buffs={int(x['index']):x for x in offer.get('hero_upgrade_buffs',[])}
    if (attrs and attr_index not in attrs) or (not attrs and attr_index!=0) or buff_index not in buffs:
        raise OperationError(2,'升级选项不属于当前机会')
    # These effects have no documented application rule in this helper. The
    # caller must add a real implementation before presenting an offer.
    raise OperationError(2,'管理员升级规则尚未完整实现')
