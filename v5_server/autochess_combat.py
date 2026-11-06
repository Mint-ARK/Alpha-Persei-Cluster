"""Bounded PVE battle interpreter; numeric buff contracts drive every effect.

The renderer receives the exact same mutations used for simulation. Permanent
growth is applied only to original player instances after a complete simulation.
"""
from copy import deepcopy
import random


class Combat:
    MAX_ACTIONS = 4096
    MAX_ROUNDS = 256
    MAX_EVENTS = 4096
    BATTLE_CELLS = 7

    def __init__(self, service, players, enemies, session):
        self.service, self.session = service, session
        self.actions, self.events, self.growth = [], [], {}
        self.event_count = 0
        self.dead_count = [0, 0]
        self.summon_count = [0, 0]
        self.global_growth = [{}, {}]
        self.removed = set()
        self.damage_bonus = [0, 0]
        self.used = {int(p['unique_id']) for p in players + enemies}
        self.next_buff = max([int(b.get('unique_id', 0)) for p in players + enemies
                              for b in p.get('buff_list', []) if isinstance(b, dict)] + [100000000])
        self.teams = [[self.fighter(p, 0) for p in sorted(players, key=lambda p:p['index'])],
                      [self.fighter(p, 1) for p in sorted(enemies, key=lambda p:p['index'])]]
        self.original = {p['unique_id']:p for p in (session or {}).get('board_chess', [])}
        for side in (0,1):
            self.damage_bonus[side] = sum(int(self.buffcfg(b)['param'][2])
                for f in self.teams[side] for b in f['buffs']
                if self.buffcfg(b).get('moment') == 1 and self.buffcfg(b).get('action_type') == 6
                and self.buffcfg(b).get('param', [])[:2] == [1,4])

    def fail(self, message):
        from core import OperationError
        raise OperationError(2, message)

    def action(self, kind, **data):
        if len(self.actions) >= self.MAX_ACTIONS:
            self.fail('自走棋行动上限已达到，本回合未结算')
        self.actions.append(dict(action_id=len(self.actions)+1, action_type=kind, **data))

    def fighter(self, record, side):
        record = deepcopy(record)
        cid = int(record['chess_id'])
        cfg = self.service.get_chess_cfg(cid) or {}
        attrs = {p['key']:p['value'] for p in record.get('chess_attr_list', [])}
        exp = int(attrs.get(3, record.get('exp', record.get('star', 1))))
        level = self.service._piece_level(dict(chess_id=cid,exp=exp))
        buffs = record.get('buff_list', [])
        if not buffs:
            levels = cfg.get('level_buffs', [])
            ids = levels[min(level,len(levels))-1] if levels else []
            ids = ids if isinstance(ids,list) else [ids]
            buffs = [dict(buff_id=bid,unique_id=record['unique_id']*100+i+1) for i,bid in enumerate(ids)]
        normalized=[]
        for b in buffs:
            if isinstance(b,int): b=dict(buff_id=b)
            self.next_buff += 1
            normalized.append(dict(b,unique_id=int(b.get('unique_id') or self.next_buff),used=0,triggers=0))
        body=cfg.get('body') or [2,3]
        atk=int(record.get('atk',attrs.get(1,body[0])))
        hp=int(record.get('hp',attrs.get(2,body[1])))
        return dict(uid=int(record['unique_id']),cid=cid,index=int(record['index']),side=side,
                    atk=max(0,atk),hp=hp,max_hp=hp,buffs=normalized,initial=record,level=level)

    def buffcfg(self,b):return self.service.get_buff_cfg(b['buff_id']) or {}
    def live(self,side):return [f for f in self.teams[side] if f['hp']>0]
    def is_shield(self,b):
        c=self.buffcfg(b)
        return c.get('moment')==1 and c.get('action_type')==7
    def is_deadly(self,f):
        return any(self.buffcfg(b).get('moment')==1 and self.buffcfg(b).get('action_type')==33 for b in f['buffs'])

    def targets(self,kind,owner,actor):
        allies=self.live(owner['side']);enemies=self.live(1-owner['side'])
        others=[f for f in allies if f is not owner]
        rear=[f for f in allies if f['index']>owner['index']]
        front=[f for f in allies if f['index']<owner['index']]
        if kind==111:return [owner] if owner['hp']>0 else []
        if kind==112:return allies
        if kind==113:return others
        if kind in (114,115,116):return random.sample(allies,min(kind-113,len(allies)))
        if kind in (117,118,119):return random.sample(others,min(kind-116,len(others)))
        if kind==120:return [f for f in allies if abs(f['index']-owner['index'])==1]
        if kind in (121,122,123):return front[-(kind-120):]
        if kind==124:return allies[:1]
        if kind in (127,128,129):return rear[:kind-126]
        if kind in (214,215,216):return random.sample(enemies,min(kind-213,len(enemies)))
        if kind==212:return enemies
        if kind==5:return others+enemies
        if kind==13:return [actor] if actor and actor['hp']>0 else []
        if kind==151:return [f for f in allies if any(self.is_shield(b) for b in f['buffs'])]
        if kind in (153,154,155):
            pool=self.targets(151,owner,actor);return random.sample(pool,min(kind-152,len(pool)))
        if kind in (156,157,158):
            pool=[f for f in allies if not any(self.is_shield(b) for b in f['buffs'])]
            return random.sample(pool,min(kind-155,len(pool)))
        return []

    def matches(self,enum,owner,actor):
        if enum in (0,1):return True
        if actor is None:return False
        if enum==111:return owner is actor
        if enum==112:return owner['side']==actor['side']
        if enum==113:return owner['side']==actor['side'] and owner is not actor
        if enum==212:return owner['side']!=actor['side']
        if enum in (121,122,123):
            return owner['side']==actor['side'] and 0<owner['index']-actor['index']<=enum-120
        return False

    def emit(self,moment,actor,victim=None):self.events.append((moment,actor,victim))

    def flush(self):
        while self.events:
            self.event_count+=1
            if self.event_count>self.MAX_EVENTS:self.fail('自走棋技能事件超出上限，本回合未结算')
            moment,actor,victim=self.events.pop(0)
            owners=[f for team in self.teams for f in team if f['hp']>0]
            if moment==17 and actor not in owners:owners.append(actor)
            for owner in owners:
                for buff in list(owner['buffs']):
                    cfg=self.buffcfg(buff)
                    if cfg.get('moment')!=moment or not self.matches(cfg.get('enum',0),owner,actor):continue
                    limit=int(cfg.get('effective_time',0));gap=int(cfg.get('gap',0))
                    if limit and buff['used']>=limit:continue
                    buff['triggers']+=1
                    if gap>0 and buff['triggers']%gap:continue
                    buff['used']+=1
                    repeats=1
                    if moment==17 and owner is actor and cfg.get('enum')==111:
                        repeats+=sum(int(self.buffcfg(b)['param'][0]) for f in owners for b in f['buffs']
                            if self.buffcfg(b).get('moment')==17 and self.buffcfg(b).get('action_type')==14
                            and self.matches(self.buffcfg(b).get('enum'),f,actor))
                    for _ in range(repeats):
                        self.effect(owner,buff,cfg,actor,victim)
                        if moment==17 and owner is actor and cfg.get('enum')==111:self.emit(21,owner)

    def attr(self,target,da,dh,buff,permanent=False):
        if not da and not dh:return
        # No implicit resurrection: dead units aren't legal buff targets.
        if target['hp']<=0:return
        target['atk']+=da;target['max_hp']+=dh;target['hp']+=dh
        self.action(3,action_effect_info=dict(effect_enum=1,buff_uid=buff['unique_id'],target_list=[dict(
            id=target['uid'],temp_id=target['cid'],update_list=[dict(key=1,value=da),dict(key=2,value=dh)])]))
        if permanent and target['side']==0 and target['uid'] in self.original:
            old=self.growth.setdefault(target['uid'],[0,0]);old[0]+=da;old[1]+=dh

    def grant(self,target,bid,source,buff):
        cfg=self.service.get_buff_cfg(bid) or {}
        if cfg.get('action_type') in (7,33) and any(self.buffcfg(x).get('action_type')==cfg.get('action_type') for x in target['buffs']):return
        self.next_buff+=1
        rec=dict(buff_id=bid,unique_id=self.next_buff,owner_type=target['side'],source_type=1,
                 source_uid=source['uid'],source_cfg_id=source['cid'])
        target['buffs'].append(dict(rec,used=0,triggers=0))
        self.action(3,action_effect_info=dict(effect_enum=4,buff_uid=buff['unique_id'],add_buff_info=[
            dict(target_type=0,target_uid=target['uid'],buff_info=rec)]))

    def hit(self,target,amount,source):
        amount=max(0,int(amount))
        if target['hp']<=0 or not amount:return 0
        shield=next((b for b in target['buffs'] if self.is_shield(b)),None)
        if shield:
            target['buffs'].remove(shield)
            self.action(7,remove_effect_info=dict(buff_uid=shield['unique_id'],target_list=[],attr_list=[]))
            self.emit(20,target);return 0
        amount += self.damage_bonus[source['side']]
        deadly=next((b for b in source['buffs'] if self.buffcfg(b).get('action_type')==33),None)
        if deadly:
            # TerminologyCfg[1100006]: the first actual damage destroys its
            # target. A shielded hit causes no damage and keeps this charge.
            source['buffs'].remove(deadly)
            self.action(7,remove_effect_info=dict(buff_uid=deadly['unique_id'],target_list=[],attr_list=[]))
            amount=max(amount,target['hp'])
        target['hp']-=amount
        self.emit(16,target)
        if target['side']!=source['side']:
            self.emit(23,source,target)
            if target['hp']<=0:self.emit(22,source,target)
        return amount

    def damage(self,target,amount,owner,buff):
        actual=self.hit(target,amount,owner)
        self.action(3,action_effect_info=dict(effect_enum=2,buff_uid=buff['unique_id'],target_list=[dict(
            id=target['uid'],temp_id=target['cid'],update_list=[dict(key=2,value=-actual)])]))

    def renumber(self,side):
        changes=[]
        for i,f in enumerate(self.teams[side],1):
            if f['index']!=i:changes.append(dict(key=f['uid'],value=i));f['index']=i
        if changes:self.action(1,action_move_info=dict(action_list=changes))

    def spawn(self,cids,owner,buff,front=False,bonus=(0,0),exp=1,templates=None):
        team=self.teams[owner['side']]
        cids=list(cids)[:max(0,self.BATTLE_CELLS-len(team))]
        if not cids:return 0
        at=0 if front else next((i for i,f in enumerate(team) if f['index']>owner['index']),len(team))
        new=[];records=[]
        # Shift existing positions before publishing summon records. Every new
        # unit has a distinct slot in the client, even a two-unit deathrattle.
        shifts=[]
        for f in team[at:]:f['index']+=len(cids);shifts.append(dict(key=f['uid'],value=f['index']))
        if shifts:self.action(1,action_move_info=dict(action_list=shifts))
        for i,cid in enumerate(cids):
            cfg=self.service.get_chess_cfg(cid)
            if not cfg:self.fail('自走棋召唤配置不存在')
            uid=self.service._allocate_uid(self.session)
            while uid in self.used:uid=self.service._allocate_uid(self.session)
            self.used.add(uid);body=cfg.get('body') or [2,2]
            global_bonus=self.global_growth[owner['side']].get(str(cid),[0,0])
            saved_bonus=((self.session or {}).get('chess_bonus') or {}).get(str(cid),[0,0]) if owner['side']==0 else [0,0]
            extra=[bonus[n]+global_bonus[n]+saved_bonus[n] for n in (0,1)]
            rec=dict(unique_id=uid,chess_id=cid,index=at+i+1,chess_attr_list=[dict(key=1,value=body[0]+bonus[0]),
                dict(key=2,value=body[1]+extra[1]),dict(key=3,value=exp)],buff_list=[])
            rec['chess_attr_list'][0]['value']=body[0]+extra[0]
            if templates:
                rec=deepcopy(templates[i]);rec.update(unique_id=uid,index=at+i+1)
                for b in rec.get('buff_list',[]):
                    self.next_buff+=1;b['unique_id']=self.next_buff
            f=self.fighter(rec,owner['side'])
            actual_exp=next((a['value'] for a in rec.get('chess_attr_list',[]) if a['key']==3),rec.get('exp',1))
            rec['chess_attr_list']=[dict(key=1,value=f['atk']),dict(key=2,value=f['hp']),dict(key=3,value=actual_exp)]
            self.damage_bonus[owner['side']]+=sum(int(self.buffcfg(b)['param'][2]) for b in f['buffs']
                if self.buffcfg(b).get('moment')==1 and self.buffcfg(b).get('action_type')==6
                and self.buffcfg(b).get('param',[])[:2]==[1,4])
            rec['buff_list']=[{k:v for k,v in b.items() if k not in ('used','triggers')} for b in f['buffs']]
            new.append(f);records.append({key:rec[key] for key in (
                'unique_id','chess_id','index','chess_attr_list','buff_list')})
        team[at:at]=new
        self.action(3,action_effect_info=dict(effect_enum=3,buff_uid=buff['unique_id'],call_info=dict(user_id=owner['side'],chess_list=records)))
        for f in new:self.emit(18,f);self.emit(19,f)
        self.renumber(owner['side'])
        return len(new)

    def effect(self,owner,buff,cfg,actor,victim):
        action=cfg.get('action_type');p=cfg.get('param') or []
        if action==5:
            for target in self.targets(p[0],owner,actor):self.attr(target,int(p[2][0]),int(p[2][1]),buff,p[1]==2)
        elif action==2:
            for target in self.targets(p[0],owner,actor):self.damage(target,int(p[1]),owner,buff)
        elif action==4:
            if p[0]==2:
                for bid in p[1]:
                    child=self.service.get_buff_cfg(bid) or {}
                    if child.get('moment')==1:
                        self.effect(owner,buff,child,actor,victim)
                    else:self.fail('不支持的自走棋玩家效果: %s'%bid)
            else:
                for target in self.targets(p[0],owner,actor):
                    for bid in p[1]:self.grant(target,bid,owner,buff)
        elif action==3:self.spawn(p[1],owner,buff,front=p[0]==2)
        elif action==22:
            for target in self.targets(p[0],owner,actor):
                self.spawn([target['cid']],owner,buff,templates=[target['initial']])
        elif action==23:self.spawn([p[0]],owner,buff,bonus=tuple(self.dead_count[owner['side']]*int(x) for x in p[2]))
        elif action==25:
            key=str(p[0]);old=self.global_growth[owner['side']].setdefault(key,[0,0])
            for n in (0,1):old[n]+=int(p[2][n])
            for target in self.live(owner['side']):
                if target['cid']==int(p[0]):self.attr(target,*p[2],buff)
        elif action==28:
            if actor and actor['side']==0 and actor['uid'] in self.original:
                self.removed.add(actor['uid'])
        elif action==36:
            count=int(((self.session or {}).get('prop_effects') or {}).get('used_count',0)) if owner['side']==0 else 0
            for target in self.targets(p[0],owner,actor):self.attr(target,p[2][0]+count*p[3][0],p[2][1]+count*p[3][1],buff,p[1]==2)
        elif action==37:
            # TerminologyCfg[1100007]: all JinGu instances share the limit.
            shared=cfg.get('id') in (4131,4132,4133,4137,4138,4139)
            if shared:
                if self.summon_count[owner['side']]>=8:return
            for cid,exp in p[1]:
                count=self.spawn([cid],owner,buff,front=p[0]==2,exp=exp)
                if shared:self.summon_count[owner['side']]+=count
        elif action==34:
            if victim and victim['cid']!=315:
                initial_attrs={a['key']:a['value'] for a in victim['initial'].get('chess_attr_list',[])}
                initial_hp=victim['initial'].get('hp',initial_attrs.get(2,victim['max_hp']))
                for target in self.targets(p[1],owner,actor):self.attr(target,0,initial_hp,buff)
        elif action==8 and len(p)>2 and p[1]==17:
            for target in self.targets(p[0],owner,actor):
                for b in list(target['buffs']):
                    c=self.buffcfg(b)
                    if c.get('moment')==17 and c.get('enum')==111:
                        self.effect(target,b,c,target,None);self.emit(21,target)
        elif action==8 and p==[2,99]:
            # The global summon limiter is enforced at action37 above.
            return
        elif action==14:
            # Multipliers modify self deathrattles in the event dispatcher.
            return
        else:self.fail('不支持的自走棋战斗效果: %s/action%s'%(cfg.get('id'),action))

    def deaths(self):
        while True:
            dead=[f for team in self.teams for f in team if f['hp']<=0]
            if not dead:return
            for f in dead:
                self.teams[f['side']].remove(f);self.dead_count[f['side']]+=1
            self.action(5,action_dead_info=dict(uid_list=[f['uid'] for f in dead]))
            for f in dead:self.emit(17,f)
            self.flush()
            for side in (0,1):self.renumber(side)

    def run(self):
        if not self.teams[0] and not self.teams[1]:return 0,[]
        if not self.teams[0]:return 2,[]
        if not self.teams[1]:return 1,[]
        for team in self.teams:
            for owner in list(team):
                for buff in list(owner['buffs']):
                    cfg=self.buffcfg(buff)
                    if cfg.get('moment')==1 and cfg.get('action_type') not in (0,1,6,7,33):
                        self.effect(owner,buff,cfg,owner,None)
        self.emit(11,None);self.flush();self.deaths()
        rounds=0
        while self.live(0) and self.live(1):
            rounds+=1
            if rounds>self.MAX_ROUNDS:self.fail('自走棋交锋上限已达到，本回合未结算')
            a,b=self.teams[0][0],self.teams[1][0]
            self.emit(12,a);self.emit(12,b);self.flush();self.deaths()
            if a not in self.live(0) or b not in self.live(1):continue
            da=max(0,a['atk']);db=max(0,b['atk'])
            got_b=self.hit(b,da,a);got_a=self.hit(a,db,b)
            self.action(2,action_battle_info=dict(action_list=[dict(key=a['uid'],value=-got_a),dict(key=b['uid'],value=-got_b)]))
            self.flush();self.deaths()
            if a['hp']>0:self.emit(13,a)
            if b['hp']>0:self.emit(13,b)
            self.flush();self.deaths()
        for uid,(da,dh) in self.growth.items():
            piece=self.original[uid];piece['atk']=int(piece.get('atk',0))+da;piece['hp']=int(piece.get('hp',0))+dh
        if self.session is not None:
            for cid,(da,dh) in self.global_growth[0].items():
                total=self.session.setdefault('chess_bonus',{}).setdefault(cid,[0,0])
                total[0]+=da;total[1]+=dh
                for key in ('board_chess','bench_chess','shop_items'):
                    for piece in self.session.get(key,[]):
                        if key=='shop_items' and piece.get('shop_type',0)==1:continue
                        if int(piece.get('chess_id',piece.get('id',0)))==int(cid):
                            piece['atk']=int(piece.get('atk',0))+da;piece['hp']=int(piece.get('hp',0))+dh
            self.session['board_chess']=[p for p in self.session.get('board_chess',[]) if p['unique_id'] not in self.removed]
        result=1 if self.live(0) else 2 if self.live(1) else 0
        return result,self.actions


def simulate(service,players,enemies,session=None):
    return Combat(service,players,enemies,session).run()
