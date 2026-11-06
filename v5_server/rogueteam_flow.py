"""Persistent run lifecycle and protocol boundaries around the contributed rules."""
import copy
import functools
import json
import math
import uuid


def row(service,uid,tid=100001):
    return service._db().get('rogueteam_session',uid,'AND template_id=?',(tid,))


def flow(state):
    return json.loads((state or {}).get('flow_json') or '{}')


def save_flow(service,uid,tid,value):
    service._db().execute('UPDATE rogueteam_session SET flow_json=? WHERE uid=? AND template_id=?',
                          (json.dumps(value,ensure_ascii=False),uid,tid))


def ensure_flow(service,uid,tid,state):
    meta=flow(state)
    if state and state.get('in_game') and not meta:
        phase='ending_plot' if state.get('ending_won') and state.get('pending_ending') in (1,2,3,6) else 'active'
        meta=dict(run_id=uuid.uuid4().hex,phase=phase,plot_played=False,awarded=False)
        save_flow(service,uid,tid,meta)
        state=dict(state,flow_json=json.dumps(meta))
    return state


def shop_price(item,offer,attributes):
    attr={int(a['attr_id']):int(a['value']) for a in attributes}
    prices=item.get('prices') or [item['price']]
    rare=min(3,max(1,int(offer.get('rare') or 1))) if int(item.get('type') or 0)==4 and int(item.get('sub_type') or 0)==1 else 1
    base=int(prices[rare-1] if rare<=len(prices) else prices[0])
    first=math.ceil(base*attr.get(11,1000)/1000)
    return math.ceil(first*int(offer.get('discount',100))/100)


def assignment_phase(tasks,fallback=1):
    ids=(30602001,30602005,30602009,30602013,30602017)
    flags={int(t.get('task_id',t.get('id',0))):int(t.get('complete_flag') or 0) for t in tasks}
    if not any(i in flags for i in ids):return fallback
    return next((n for n,i in enumerate(ids,1) if flags.get(i)!=1),5)


class RogueBattleExpired(ValueError):
    pass


def check_battle(db,uid,session,*,after=False):
    from rogueteam_service import RogueTeamService
    service=RogueTeamService.get(db=db)
    try:state=_active(service,uid,100001)
    except ValueError as exc:raise RogueBattleExpired(str(exc)) from exc
    if not session.get('rogue_run_id') or session['rogue_run_id']!=flow(state).get('run_id'):
        raise RogueBattleExpired('推演战斗已不属于当前局')
    meta=flow(state)
    if meta.get('phase') not in (('active','ending_plot','ready_settle') if after else ('active',)):
        raise RogueBattleExpired('推演战斗阶段已变化')
    if (session.get('rogue_floor_num')!=state.get('floor_num')
            or session.get('rogue_hidden')!=bool(meta.get('hidden'))):
        raise RogueBattleExpired('推演战斗地图已变化')
    node=next((n for n in json.loads(state.get('map_info_json') or '[]') if n.get('node_id')==session.get('node_id')),None)
    if (not node or node.get('node_type') not in (1,2,3,4,11)
            or session.get('rogue_real_stage')!=node.get('param')):
        raise RogueBattleExpired('推演战斗资源已变化')
    if not after and (session.get('node_id')!=state.get('select_node_id') or node.get('state')!=0):
        raise RogueBattleExpired('推演战斗节点已变化')


def _active(service,uid,tid):
    state=ensure_flow(service,uid,tid,row(service,uid,tid))
    if not state or not state.get('in_game') or flow(state).get('phase')=='settled':
        raise ValueError('推演不在进行中')
    return state


def install(cls):
    from account_db import savepoint_transaction
    originals={name:getattr(cls,name) for name in ('start_run','select_node','choose_event_option','commit_event_selection',
                'next_floor','settle_battle_node','settle_run','play_ending_plot','buy_shop_item','leave_shop')}
    cls._contribution_methods=originals
    original_session=cls.get_session_data
    def session_data(self,uid,template_id=100001):
        state=ensure_flow(self,uid,template_id,row(self,uid,template_id))
        result=original_session(self,uid,template_id)
        # Storage operates on the active graph. Native pathData indices are
        # permanent identities: main=1, relay=2, even while exploring relay.
        if result and flow(state).get('hidden'):
            result['map_info'],result['second_map_info']=result['second_map_info'],result['map_info']
        return result
    cls.get_session_data=session_data

    @classmethod
    def get(bound,db=None):
        if db is not None:return bound(db=db)
        if bound._instance is None or bound._instance._custom_db is not None:bound._instance=bound()
        return bound._instance
    cls.get=get

    def start(self,uid,template_id,difficult,hero_list,affix_pool_id_list):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            current=row(self,uid,tid)
            if current and current.get('in_game'):return 2,None
            from rogueteam_transition import validate_heroes, InvalidTransition
            try:
                if tid!=self.config.cfg['main']['id'] or type(difficult) is not int or difficult not in (1,2,3,4):return 2,None
                owned=self._db().get('rogueteam_user',uid,'AND template_id=?',(tid,)) or {}
                if difficult>int(owned.get('max_difficult') or 1):return 2,None
                if not isinstance(hero_list,list):return 2,None
                heroes=validate_heroes(self,uid,[h for h in hero_list if isinstance(h,dict) and h.get('hero_id')!=0])
                if not isinstance(affix_pool_id_list,list) or len(set(affix_pool_id_list))!=len(affix_pool_id_list):return 2,None
                if any(type(i) is not int or not self.config.get_optional_affix(i) for i in affix_pool_id_list):return 2,None
            except InvalidTransition:return 2,None
            result,session=originals['start_run'](self,uid,tid,difficult,heroes,affix_pool_id_list)
            if not result:save_flow(self,uid,tid,dict(run_id=uuid.uuid4().hex,phase='active',plot_played=False,awarded=False))
            return result,session
    cls.start_run=start

    def select(self,uid,node_id,template_id=100001):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            state=_active(self,uid,tid);nodes=json.loads(state['map_info_json']);target=next((n for n in nodes if n['node_id']==int(node_id)),None)
            selected=int(state.get('select_node_id') or 0);pending=json.loads(state.get('other_info_json') or '{}')
            if (target is None or target.get('state')!=0 or (selected and selected!=int(node_id))
                    or pending.get('event_id') or pending.get('param_list')):return 2,None
            if not selected:
                done=[n for n in nodes if n.get('state')==3]
                if done:
                    last=max(done,key=lambda n:n.get('col',0));allowed=last.get('next_id_list') or []
                else:
                    first=min(n.get('col',0) for n in nodes);allowed=[n['node_id'] for n in nodes if n.get('col',0)==first]
                if int(node_id) not in allowed:return 2,None
            return originals['select_node'](self,uid,node_id,tid)
    cls.select_node=select

    def choose(self,uid,opt_id,template_id=100001):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            state=_active(self,uid,tid);event=json.loads(state.get('other_info_json') or '{}');nodes=json.loads(state.get('map_info_json') or '[]')
            node=next((n for n in nodes if n['node_id']==state.get('select_node_id')),None)
            if int(opt_id or 0)==0:
                if node is None or node.get('state') not in (0,1):return 2,{}
                if event.get('param_list') or event.get('_queue'):return 2,{}
            elif int(opt_id) not in {int(x.get('opt_id') or 0) for x in event.get('opt_list',[])}:
                return 2,{}
            return originals['choose_event_option'](self,uid,opt_id,tid)
    cls.choose_event_option=choose

    def commit(self,uid,event_id,param_arg,template_id=100001):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            state=_active(self,uid,tid);pending=json.loads(state.get('other_info_json') or '{}')
            if int(pending.get('event_type') or 0)!=int(event_id) or not pending.get('param_list'):return 2,{}
            valid={int(p.get('index') or 0) for p in pending['param_list']}|{int(p.get('param') or 0) for p in pending['param_list']}
            if int(param_arg or 0) not in valid:return 2,{}
            return originals['commit_event_selection'](self,uid,event_id,param_arg,tid)
    cls.commit_event_selection=commit

    def buy(self,uid,index,template_id=100001):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            _active(self,uid,tid)
            return originals['buy_shop_item'](self,uid,index,tid)
    cls.buy_shop_item=buy

    def battle(self,uid,node_id,win=True,template_id=100001,battle_info=None):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            state=_active(self,uid,tid);meta=flow(state)
            if battle_info and battle_info.get('_run_id') and battle_info['_run_id']!=meta.get('run_id'):
                raise ValueError('旧推演战斗已失效')
            result=originals['settle_battle_node'](self,uid,node_id,win=win,template_id=tid,battle_info=battle_info)
            after=row(self,uid,tid)
            meta=flow(after)
            event_battle=meta.get('event_battle')
            if win and event_battle and event_battle.get('node_id')==node_id:
                from rogueteam_transition import owner_key, require, has_pending
                expected=dict(run_id=meta['run_id'],floor=after['floor_num'],hidden=bool(meta.get('hidden')))
                require(all(event_battle.get(k)==v for k,v in expected.items()), '剧情战斗归属已变化')
                pending=json.loads(after.get('other_info_json') or '{}')
                hist_events,hist_options=self._get_session_history(state)
                eid=event_battle['event_id'];hist_events.add(eid)
                attrs={a['attr_id']:a['value'] for a in json.loads(after['attr_list_json'])}
                options=self.filter_unlocked_options(uid,eid,after,attrs,json.loads(after['treasure_list_json']),
                    json.loads(after['other_item_list_json']),json.loads(after['hero_list_json']),hist_events,hist_options,tid)
                resume=dict(event_id=eid,opt_list=[self._build_option_entry(o) for o in options],trigger_type=1)
                self._save_session_history(resume,hist_events,hist_options)
                # Ordinary story battles always own their real reward window;
                # the following event is revealed only after its last claim.
                require(has_pending(pending), '剧情战斗缺少奖励待办')
                pending['_resume_event']=resume
                self._db().execute('UPDATE rogueteam_session SET other_info_json=? WHERE uid=? AND template_id=?',
                    (json.dumps(pending),uid,tid))
                event_battle['won']=True;save_flow(self,uid,tid,meta)
            if int(after.get('ending_won') or 0)==1 and int(after.get('pending_ending') or 0) in (1,2,3,6):
                meta['phase']='ending_plot';save_flow(self,uid,tid,meta)
            from rogueteam_transition import bind_pending
            bind_pending(self,uid,tid,dict(state=state,meta=flow(state),
                pending=json.loads(state.get('other_info_json') or '{}'),shop=json.loads(state.get('shop_info_json') or '{}')),
                selected=node_id)
            return result
    cls.settle_battle_node=battle

    def next_floor(self,uid,sign=1,template_id=100001):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            state=_active(self,uid,tid);nodes=json.loads(state.get('map_info_json') or '[]');meta=flow(state)
            if meta.get('phase') not in ('active','ending_plot','ready_settle'):return 2,None
            pending=json.loads(state.get('other_info_json') or '{}');shop=json.loads(state.get('shop_info_json') or '{}')
            from rogueteam_transition import has_pending
            if has_pending(pending) or has_pending(shop):return 2,None
            if int(sign) in (2,3):
                switch=meta.get('map_switch') or {}
                if switch.get('sign')!=int(sign):return 2,None
                if any(switch.get(k)!=v for k,v in dict(run_id=meta['run_id'],floor=state['floor_num'],hidden=bool(meta.get('hidden'))).items()):return 2,None
                source=next((n for n in nodes if n['node_id']==switch.get('node_id')),None)
                if not source or source.get('state')!=3 or state.get('select_node_id'):return 2,None
                other=json.loads(state.get('second_map_json') or '[]')
                if bool(meta.get('hidden'))!=(int(sign)==3):return 2,None
                if int(sign)==2:
                    # No native relay topology was present in extracted cfg.
                    # Explicit local policy: one relay story node, no RNG.
                    if other and any(n.get('state')!=3 for n in other):return 2,None
                    nid=max([900000]+[n['node_id'] for n in nodes+other])+1
                    other=[dict(node_id=nid,col=1,row=3,node_type=8,state=0,param=60030,next_id_list=[])]
                elif not other:return 2,None
                self._db().execute('UPDATE rogueteam_session SET map_info_json=?,second_map_json=?,select_node_id=0 WHERE uid=? AND template_id=?',
                                   (json.dumps(other),json.dumps(nodes),uid,tid))
                meta['hidden']=int(sign)==2;meta.pop('map_switch',None)
                meta.pop('pending_owner',None);meta.pop('shop_owner',None)
                save_flow(self,uid,tid,meta)
                return 0,self.get_session_data(uid,tid)
            if int(sign) not in (0,1) or meta.get('hidden') or meta.get('map_switch'):return 2,None
            terminal=max((int(n.get('col') or 0) for n in nodes),default=-1)
            if not any(int(n.get('col') or 0)==terminal and n.get('state')==3 for n in nodes):return 2,None
            pending=json.loads(state.get('other_info_json') or '{}')
            if pending.get('event_id') or pending.get('param_list') or pending.get('_queue'):return 2,None
            return originals['next_floor'](self,uid,sign,tid)
    cls.next_floor=next_floor

    def played(self,uid,template_id=100001):
        tid=int(template_id or 100001);state=_active(self,uid,tid);meta=flow(state)
        if meta.get('phase') not in ('ending_plot','ready_settle'):return 2
        meta.update(plot_played=True,phase='ready_settle');save_flow(self,uid,tid,meta);return 0
    cls.play_ending_plot=played

    def settle(self,uid,ending_id=0,is_win=False,template_id=100001):
        tid=int(template_id or 100001)
        with savepoint_transaction(self._db()):
            state=row(self,uid,tid);meta=flow(state)
            if meta.get('report') is not None:return 0,copy.deepcopy(meta['report'])
            if not state or not state.get('in_game'):return 2,{}
            won=bool(state.get('ending_won')) and int(state.get('pending_ending') or 0) in (1,2,3,6)
            if won and not meta.get('plot_played'):return 2,{}
            ending=int(state['pending_ending']) if won else 4
            code,report=originals['settle_run'](self,uid,ending,is_win=won,template_id=tid)
            if not code:
                meta.update(phase='settled',report=report,awarded=False);save_flow(self,uid,tid,meta)
            return code,report
    cls.settle_run=settle

    def pending_frames(self,uid,tid=100001):
        state=row(self,uid,tid)
        if not state or not state.get('in_game'):return []
        meta=flow(state);pending=json.loads(state.get('other_info_json') or '{}');shop=json.loads(state.get('shop_info_json') or '{}');objects=[]
        from rogueteam_transition import has_pending
        if meta.get('map_switch') and not has_pending(pending) and not has_pending(shop):
            objects.append((88221,dict(sign=meta['map_switch']['sign'])))
        if meta.get('phase') in ('ending_plot','ready_settle'):
            objects.append((88217,dict(id=int(state['pending_ending']),state=int(bool(meta.get('plot_played'))))))
        if pending.get('event_id'):objects.append((88023,{k:pending[k] for k in ('event_id','opt_list','trigger_type') if k in pending}))
        elif pending.get('param_list'):objects.append((88015,dict(other_info=self._norm_event(pending))))
        if shop.get('param_list'):objects.append((88027,dict(shop_info=self._norm_event(shop))))
        return objects
    cls.pending_frames=pending_frames
    from rogueteam_transition import install as install_transitions
    install_transitions(cls)
    from rogueteam_effects import install as install_room_effects
    install_room_effects(cls)
    from rogueteam_tactics import install as install_tactics
    install_tactics(cls)


def install_operations(namespace):
    from middleware import DownFrame
    from core import OperationError
    def service(ctx):
        from rogueteam_service import RogueTeamService
        return RogueTeamService.get(db=ctx.db)
    def frame(ctx,sc,data):
        payload=ctx.codec_encode('sc_'+str(sc),data)
        if not isinstance(payload,(bytes,bytearray,memoryview)) or not payload:raise ValueError(f'推演必要帧{sc}为空')
        return DownFrame(sc,bytes(payload))
    def settle_apply(self,data,ctx):
        svc=service(ctx);tid=int(data.get('template_id') or 100001)
        before=row(svc,self.uid,tid)
        code,report=svc.settle_run(self.uid,template_id=tid)
        if code:raise OperationError(code,'推演结算阶段不匹配')
        state=row(svc,self.uid,tid);meta=flow(state)
        if not meta.get('awarded'):
            for item,key in ((70,'survey_item_gain'),(71,'research_item_gain')):
                amount=int(report.get(key) or 0)
                if amount:namespace['_item_add'](ctx,self.uid,item,amount,source='rogueteam_settle')
            meta['awarded']=True;save_flow(svc,self.uid,tid,meta)
        from rogueteam_achievement import record_win, reconcile
        record_win(ctx.db,self.uid,before)
        achievement_frames=reconcile(ctx.db,self.uid,ctx=ctx)
        return dict(result=0,settle=report,achievement_frames=achievement_frames)
    def settle_respond(self,result,data,ctx):
        svc=service(ctx);report=result['settle'];out=svc.get_outside_data(self.uid)
        frames=namespace['_refresh_frames'](ctx,self.uid,strict=True)
        frames.extend(result.get('achievement_frames',[]))
        frames.append(frame(ctx,88307,{k:report[k] for k in ('result','end_info_list','collection_list','unlock_collection','total_time')}))
        fields=('template_id','difficult','collection_list','his_difficult','his_avg','unlock_collection','view_collection','tree_list','last_id')
        frames.append(frame(ctx,88305,{key:out[key] for key in fields}))
        return frames
    namespace['RogueTeamSettleScoreOp'].apply=settle_apply
    namespace['RogueTeamSettleScoreOp'].respond=settle_respond
    # Replace the UID-global delayed-report path from the attachment entirely.
    def plot_apply(self,data,ctx):return dict(result=service(ctx).play_ending_plot(self.uid))
    def plot_respond(self,result,data,ctx):return [frame(ctx,self.sc,dict(result=result['result']))]
    namespace['RogueTeamPlayEndingPlotOp'].apply=plot_apply
    namespace['RogueTeamPlayEndingPlotOp'].respond=plot_respond
    def next_apply(self,data,ctx):
        result,session=service(ctx).next_floor(self.uid,int(data.get('sign') or 1))
        return dict(result=result,session=session)
    def next_respond(self,result,data,ctx):
        frames=[]
        if result.get('session'):frames.append(frame(ctx,88001,result['session']))
        frames.append(frame(ctx,self.sc,dict(result=result['result'])))
        return frames
    namespace['RogueTeamNextFloorOp'].apply=next_apply
    namespace['RogueTeamNextFloorOp'].respond=next_respond
    for name in ('RogueTeamSyncPlayStateOp','RogueTeamNextFloorOp'):
        target=namespace[name];original=target.respond
        def respond(self,result,data,ctx,original=original):
            frames=original(self,result,data,ctx)
            present={f.cmd for f in frames}
            for sc,obj in service(ctx).pending_frames(self.uid):
                if sc not in present:frames.append(frame(ctx,sc,obj));present.add(sc)
            return frames
        target.respond=respond
    def replace_apply(self,data,ctx):
        code,extra=service(ctx).close_node_replace(self.uid,data.get('node_id'),data.get('node_type'))
        return dict(result=code,extra=extra)
    def replace_respond(self,result,data,ctx):
        frames=[frame(ctx,self.sc,dict(result=result['result']))]
        extra=result.get('extra') or {}
        if extra.get('map_info'):frames.append(frame(ctx,88003,dict(map_info=extra['map_info'],second_map_info=[])))
        if extra.get('session'):frames.append(frame(ctx,88001,extra['session']))
        return frames
    namespace['RogueTeamCloseReplaceNodeOp'].apply=replace_apply
    namespace['RogueTeamCloseReplaceNodeOp'].respond=replace_respond

    # Keep existing native packet ordering, while making every attempted data
    # frame mandatory. Record failures even when legacy respond catches them.
    # The encoder belongs to this connection; restore it on every exit path.
    from rogueteam_transition import required_frame
    for name,target in list(namespace.items()):
        if not name.startswith('RogueTeam') or not isinstance(target,type) or not hasattr(target,'respond'):continue
        original=target.respond
        def strict_respond(self,result,data,ctx,original=original):
            encode=ctx.codec_encode;failures=[];empty_frames={}
            def strict_encode(schema,obj):
                try:
                    if schema=='sc_88003':
                        snapshot=service(ctx).get_session_data(self.uid)
                        obj=dict(map_info=snapshot['map_info'],second_map_info=snapshot['second_map_info'])
                    raw=encode(schema,obj)
                    if not isinstance(raw,(bytes,bytearray,memoryview)) or (not raw and any(v not in ([],{},None) for v in obj.values())):
                        raise ValueError('推演必要帧编码失败: '+schema)
                    if not raw and schema.startswith('sc_'):
                        empty_frames[int(schema[3:])]=bytes(raw)
                    return bytes(raw)
                except Exception as exc:
                    failures.append(exc);raise
            ctx.codec_encode=strict_encode
            try:
                frames=original(self,result,data,ctx)
                if failures:raise failures[0]
                present={f.cmd for f in frames}
                for sc,raw in empty_frames.items():
                    if sc not in present:frames.append(DownFrame(sc,raw));present.add(sc)
                extra=result.get('extra') or result.get('update_info') or {}
                if result.get('result')==0:
                    # Effects added by completion may occur on node entry,
                    # shop exit or reward claims. Native item ownership must
                    # be installed before an effect references its source ID.
                    for key,sc in (('item_list',88021),('update_list',88205)):
                        if extra.get(key) and sc not in present:
                            # A full snapshot resets the bag. Inserting the
                            # ownership delta after it would grant twice.
                            index=next((i for i,f in enumerate(frames) if f.cmd in (88001,88017)),len(frames))
                            frames.insert(index,required_frame(ctx,sc,{key:extra[key]}));present.add(sc)
                    if extra.get('effect_list') and 88001 not in present:
                        frames.append(required_frame(ctx,88017,{'effect_list':extra['effect_list']}));present.add(88017)
                    if extra.get('attr_list') and 88013 not in present:
                        frames.append(required_frame(ctx,88013,{'attr_list':extra['attr_list']}));present.add(88013)
                    if extra.get('hero_list') and 88019 not in present:
                        frames.append(required_frame(ctx,88019,{'hero_list':extra['hero_list']}));present.add(88019)
                    for key,sc,field in (('hero_list',88019,'hero_list'),('attr_list',88013,'attr_list'),('map_info',88003,'map_info')):
                        if key in extra and extra[key]==[] and sc not in present:
                            frames.append(required_frame(ctx,sc,{field:[]}));present.add(sc)
                    if self.cmd==88030:
                        state=service(ctx).get_session_data(self.uid)
                        if state and state.get('in_game'):
                            for sc,obj in ((88013,dict(attr_list=state.get('attr_list',[]))),
                                           (88019,dict(hero_list=state.get('hero_list',[])))):
                                if sc not in present:frames.append(required_frame(ctx,sc,obj));present.add(sc)
                if extra.get('next_event') and self.cmd==88200:
                    frames.append(required_frame(ctx,88023,{k:extra['next_event'][k] for k in ('event_id','opt_list','trigger_type') if k in extra['next_event']}))
                if self.cmd==88212 and result.get('result')==0:
                    frames.append(required_frame(ctx,88001,service(ctx).get_session_data(self.uid)))
                # Native 88221 sets the flag but does not re-evaluate map
                # identity. Place it before the snapshot that performs that
                # evaluation; ACK 88009 must follow the accepted snapshot.
                if result.get('result')==0 and self.cmd!=88008:
                    switch=next((obj for sc,obj in service(ctx).pending_frames(self.uid) if sc==88221),None)
                    if switch:
                        frames=[f for f in frames if f.cmd!=88221]
                        frames.insert(0,required_frame(ctx,88221,switch))
                        if not any(f.cmd in (88001,88003) for f in frames):
                            frames.append(required_frame(ctx,88001,service(ctx).get_session_data(self.uid)))
                if self.cmd==88030:
                    frames=[f for f in frames if f.cmd!=88031]+[f for f in frames if f.cmd==88031]
                if result.get('result')==0:
                    tactic=extra.get('_tactic_state')
                    if self.cmd==88030:
                        from rogueteam_tactics import state_packet
                        tactic=state_packet(row(service(ctx),self.uid))
                    if tactic is not None:
                        frames.insert(0,required_frame(ctx,88211,tactic))
                    if extra.get('_tactic_state'):
                        from rogueteam_achievement import reconcile
                        index=next((i for i,f in enumerate(frames) if f.cmd==self.sc),len(frames))
                        frames[index:index]=reconcile(ctx.db,self.uid,ctx=ctx)
                return frames
            finally:
                ctx.codec_encode=encode
        target.respond=strict_respond
