"""RogueCard's server lifecycle; the card game itself runs in the client."""
import copy
import json
from pathlib import Path
import uuid

from account_db import savepoint_transaction
from core import OperationError
from event_bus import bus, Events

CFG=json.loads(Path(__file__).with_name('rogue_card_cfg.json').read_text('utf-8'))


def require(ok,message):
    if not ok:
        raise OperationError(2,message)


class RogueCardService:
    def __init__(self,owner,ctx,uid):
        self.owner,self.ctx,self.uid=owner,ctx,uid
        self.db=getattr(ctx,'db',None) or owner.db
        require(self.db is not None,'缺少目标存档')

    def load(self):
        rec=self.owner.get_or_init_rogue_card(self.db,self.uid)
        rows=self.db.query('SELECT value FROM server_global_config WHERE key=?',(self.key,))
        meta=json.loads(rows[0]['value']) if rows else {}
        if not meta:
            # Adopt a legacy snapshot only for the mode named by its stage ID.
            for mode,kind,prefix in (('story',1,''),('challenge',2,'challenge_')):
                bid=int(rec.get(prefix+'battle_id') or 0)
                if not bid:continue
                stage=(rec.get('save_data') or {}).get('stage_id')
                if not stage and mode=='story':stage=CFG['posts'].get(str(rec['thread_id']),{}).get('balatro_id')
                if not stage and mode=='challenge':stage=201
                state=dict(battle_id=bid,stage_id=stage,phase='active',save={},rollback={})
                if CFG['stages'].get(str(stage),{}).get('type')==kind:
                    saved=rec.get('save_data') or {}
                    if int(saved.get('stage_id') or 0)==stage:state['save']=copy.deepcopy(saved)
                    rollback=rec.get('save_rollback') or {}
                    if int(rollback.get('stage_id') or 0)==stage:state['rollback']=copy.deepcopy(rollback)
                    meta[mode]=state
        return rec,meta

    @property
    def key(self):return 'roguecard.runtime_v1.'+str(self.uid)

    def persist(self,rec,meta):
        self.owner.save_rogue_card(self.db,self.uid,rec)
        self.db.execute('INSERT INTO server_global_config(key,value) VALUES(?,?) '
                        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (self.key,json.dumps(meta,ensure_ascii=False)))

    def persist_progress(self,rec,meta):
        from rogue_card_progress import ensure_statistics,misc_view_count
        statistics=ensure_statistics(self.db,self.uid,meta)
        self.persist(rec,meta)
        # Ensure required subscribers are registered for direct service callers.
        import task_listener
        bus.emit_required(Events.ROGUE_CARD_PROGRESS,self.ctx,self.uid,
                          statistics=statistics,viewed_misc=misc_view_count(rec,CFG))

    def record_statistics(self,meta,info,kind,won,deck=0,diff=0):
        from rogue_card_progress import record_run
        record_run(self.db,self.uid,meta,info,kind=kind,won=won,deck=deck,diff=diff,
                   hand_type_count=len(CFG['hand_types']))

    def post(self,rec,tid):
        post=CFG['posts'].get(str(tid))
        require(post is not None,'帖子不存在')
        require(not post['pre_id'] or post['pre_id'] in rec['finish_thread_list'],'帖子尚未解锁')
        return post

    def mode(self,rec,meta,bid,kind):
        require(kind in (1,2),'对局类型错误')
        key='story' if kind==1 else 'challenge'
        state=meta.get(key) or {}
        expected=rec.get('battle_id' if kind==1 else 'challenge_battle_id')
        require(bid>0 and bid==expected==state.get('battle_id') and state.get('phase')=='active','对局已失效')
        return state

    def collections(self,rec,info):
        require(isinstance(info,dict),'结算数据错误')
        for source,target,table in (('use_item_id','gather_card_list','cards'),
                                    ('enhance_id','gather_enhance_list','enhances'),
                                    ('weal_woe_id','gather_weal_woe_list','weal_woe')):
            values=info.get(source) or []
            require(isinstance(values,list) and all(isinstance(v,int) and (v==0 or str(v) in CFG[table]) for v in values),'收集项不存在')
            for value in values:
                if value and value not in rec[target]:rec[target].append(value)

    def begin(self,thread_id):
        rec,meta=self.load();post=self.post(rec,thread_id)
        require(post['type']==1,'该帖子不能调查')
        rec.update(thread_id=thread_id,thread_state=1,battle_id=0)
        if thread_id not in rec['post_thread_list']:rec['post_thread_list'].append(thread_id)
        if post['balatro_id']==0:require(post['story_id1']>0,'帖子没有可用内容')
        meta['story']=dict(phase='intro',thread_id=thread_id,stage_id=post['balatro_id'])
        self.persist(rec,meta)
        return dict(result=0)

    start_rogue_card_post=begin
    reback_rogue_card_post=begin

    def complete_rogue_card_story(self,thread_id,story_id=0,story_id_index=0):
        rec,meta=self.load();post=self.post(rec,thread_id)
        require(story_id_index in (1,2) and story_id==post['story_id'+str(story_id_index)],'剧情不属于该帖子')
        # Replaying a completed story must not disturb another ongoing post.
        if thread_id in rec['finish_thread_list'] and rec['thread_id']!=thread_id:return dict(result=0,_unchanged=True)
        state=meta.get('story') or {}
        require(rec['thread_id']==thread_id,'当前调查帖子不匹配')
        if story_id_index==1:
            require(rec['thread_state'] in (1,2,3),'剧情阶段错误')
            if post['balatro_id']==0:
                self.finish(rec,thread_id)
                rec.update(thread_state=0,thread_id=0,battle_id=0)
                state['phase']='done'
            elif rec['thread_state']<3:rec['thread_state']=2
        else:
            require(thread_id in rec['finish_thread_list'],'尚未完成该调查')
            rec.update(thread_state=0,thread_id=0,battle_id=0)
            state['phase']='done'
        meta['story']=state;self.persist(rec,meta)
        return dict(result=0)

    def finish(self,rec,tid):
        if tid not in rec['finish_thread_list']:
            rec['finish_thread_list'].append(tid)
            bus.emit(Events.ROGUE_CARD_POST_FINISH,self.ctx,self.uid,post_id=tid)

    def click_like_rogue_card_post(self,thread_id):
        rec,meta=self.load();self.post(rec,thread_id)
        values=rec['like_thread_list']
        if thread_id in values:values.remove(thread_id)
        else:values.append(thread_id)
        self.persist(rec,meta);return dict(result=0)

    def view_rogue_card_thread_post(self,thread_id):
        rec,meta=self.load();self.post(rec,thread_id)
        if thread_id in rec['view_thread_list']:return dict(result=0,_unchanged=True)
        rec['view_thread_list'].append(thread_id)
        self.persist_progress(rec,meta);return dict(result=0)

    def enter_rogue_card_game(self,stage_id=0,deck=0,diff=0):
        rec,meta=self.load();stage=CFG['stages'].get(str(stage_id))
        require(stage is not None,'关卡不存在')
        bid=uuid.uuid4().int & ((1<<63)-1)
        if stage['type']==1:
            post=self.post(rec,rec['thread_id'])
            require(post['balatro_id']==stage_id and rec['thread_state'] in (1,2,3),'关卡不属于当前调查')
            require(not deck and not diff,'主线关卡不接受挑战参数')
            rec.update(thread_state=3,battle_id=bid)
            if rec['thread_id'] not in rec['post_thread_list']:rec['post_thread_list'].append(rec['thread_id'])
            key='story'
        else:
            config=CFG['decks'].get(str(deck));require(config is not None and str(diff) in CFG['difficulties'],'挑战参数错误')
            condition=CFG['conditions'].get(str(config['condition']))
            if condition:
                args=condition['params']
                unlocked=(args[0] in rec['finish_thread_list'] if condition['type']==12701 else
                          any(v['deck']==args[0] and v['max_diff']>=args[1] for v in rec['challenge_status_list']))
                require(unlocked,'牌组尚未解锁')
            cleared=next((v['max_diff'] for v in rec['challenge_status_list'] if v['deck']==deck),0)
            require(diff<=cleared+1,'挑战难度尚未解锁')
            rec.update(challenge_deck=deck,challenge_diff=diff,challenge_state=1,challenge_battle_id=bid)
            key='challenge'
        meta[key]=dict(battle_id=bid,stage_id=stage_id,phase='active',save={},rollback={})
        self.persist(rec,meta);return dict(result=0,battle_id=bid)

    def save_rogue_card_progress(self,battle_id,battle_type=0,save_data=None,check_sum=None):
        rec,meta=self.load();state=self.mode(rec,meta,battle_id,battle_type)
        self.validate_save(rec,state,save_data,battle_type)
        state['save']=copy.deepcopy(save_data)
        self.persist(rec,meta);return dict(result=0)

    def validate_save(self,rec,state,saved,kind):
        require(isinstance(saved,dict) and saved.get('stage_id')==state['stage_id'],'存档关卡不匹配')
        if kind==2:
            require(int(saved.get('deck_id') or rec['challenge_deck'])==rec['challenge_deck'] and
                    int(saved.get('difficulty') or rec['challenge_diff'])==rec['challenge_diff'],'存档挑战参数不匹配')
        # Validate IDs before they can reach client config lookups on re-login.
        probe=copy.deepcopy(rec);self.collections(probe,saved.get('settle_data') or {})

    def continue_rogue_card_progress(self,battle_id=0,battle_type=0):
        rec,meta=self.load();state=self.mode(rec,meta,battle_id,battle_type)
        require(bool(state['save']),'尚无可继续的对局存档')
        result=dict(result=0,battle_id=battle_id,save_data=state['save'])
        if state['rollback']:result['save_rollback']=state['rollback']
        return result

    def get_stage_save_data(self,battle_id=0,battle_type=0,save_rollback=None):
        # cs_89626 writes a checkpoint. It is not the read-current-save RPC.
        rec,meta=self.load();state=self.mode(rec,meta,battle_id,battle_type)
        require(battle_type==1,'挑战模式没有回滚点')
        self.validate_save(rec,state,save_rollback,battle_type)
        state['rollback']=copy.deepcopy(save_rollback)
        self.persist(rec,meta);return dict(result=0)

    def interrupt_rogue_card_post(self,battle_id=0,battle_type=0):
        # cs_89628 is a read; the old method name remains for wire compatibility.
        rec,meta=self.load();state=self.mode(rec,meta,battle_id,battle_type)
        require(bool(state['save']),'尚无对局存档')
        return dict(result=0,battle_id=battle_id,save_data=state['save'])

    def save_rogue_card_rollback(self,battle_id=0,battle_type=0):
        # cs_89624 interrupts/settles, it must never overwrite the rollback point.
        require(battle_type in (1,2),'对局类型错误')
        rec,meta=self.load()
        key='story' if battle_type==1 else 'challenge'
        previous=meta.get(key) or {}
        if previous.get('phase')=='interrupted' and previous.get('battle_id')==battle_id:
            return dict(copy.deepcopy(previous['receipt']),_unchanged=True)
        state=self.mode(rec,meta,battle_id,battle_type)
        info=copy.deepcopy((state['save'] or {}).get('settle_data') or {})
        self.collections(rec,info)
        self.record_statistics(meta,info,battle_type,False)
        if battle_type==1:rec.update(thread_id=0,thread_state=0,battle_id=0)
        else:rec.update(challenge_state=0,challenge_battle_id=0)
        receipt=dict(result=0,battle_id=battle_id,battle_type=battle_type,settle_info=info)
        state.update(phase='interrupted',receipt=receipt,save={},rollback={})
        self.persist_progress(rec,meta);return receipt

    def complete_rogue_card_post(self,thread_id,joker_result,info=None):
        rec,meta=self.load();post=self.post(rec,thread_id);state=meta.get('story') or {}
        if state.get('phase') in ('won','done') and state.get('thread_id')==thread_id:return dict(result=0,_unchanged=True)
        require(rec['thread_id']==thread_id and rec['thread_state']==3 and rec['battle_id']>0,'调查对局不匹配')
        require(post['balatro_id']==state.get('stage_id') and joker_result==1,'调查结算状态错误')
        self.collections(rec,info or {})
        self.record_statistics(meta,info or {},1,True)
        self.finish(rec,thread_id)
        rec.update(thread_state=4,battle_id=0)
        state.update(phase='won',thread_id=thread_id,save={},rollback={})
        self.persist_progress(rec,meta);return dict(result=0)

    def settle_rogue_card_challenge(self,deck,diff,info=None):
        rec,meta=self.load();state=meta.get('challenge') or {}
        if state.get('phase')=='won' and state.get('deck')==deck and state.get('diff')==diff:return dict(result=0,_unchanged=True)
        require(rec['challenge_state']==1 and rec['challenge_battle_id']>0 and
                state.get('phase')=='active' and rec['challenge_deck']==deck and rec['challenge_diff']==diff,'挑战对局不匹配')
        self.collections(rec,info or {})
        self.record_statistics(meta,info or {},2,True,deck,diff)
        status=next((v for v in rec['challenge_status_list'] if v['deck']==deck),None)
        if status:status['max_diff']=max(status['max_diff'],diff)
        else:rec['challenge_status_list'].append(dict(deck=deck,max_diff=diff))
        rec.update(challenge_state=0,challenge_battle_id=0)
        state.update(phase='won',deck=deck,diff=diff,save={},rollback={})
        self.persist_progress(rec,meta);return dict(result=0)


def dispatch(owner,ctx,uid,name,*args,**kwargs):
    db=getattr(ctx,'db',None) or owner.db
    with savepoint_transaction(db):
        return getattr(RogueCardService(owner,ctx,uid),name)(*args,**kwargs)


def install(namespace):
    cls=namespace['MiniGameService']
    # No incremental progress packet exists for this mode. Send its state before
    # the ACK callback; like/save callbacks already update their local copies.
    state_commands={89602,89604,89608,89610,89612,89616,89618,89624}
    for op in list(namespace.values()):
        if not isinstance(op,type) or not op.__name__.startswith('RogueCard'):continue
        op.prepare_response_in_transaction=True
        def respond(self,result,data,ctx):
            from middleware import DownFrame
            frames=[]
            if result.get('result')==0 and self.cmd in state_commands and not result.get('_unchanged'):
                payload=cls.get_instance(ctx.db).build_89601_payload(ctx.db,self.uid)
                if not payload:raise ValueError('诡谈夜话必要状态帧为空')
                frames.append(DownFrame(89601,payload))
            payload=ctx.codec_encode('sc_'+str(self.sc),{k:v for k,v in result.items() if not k.startswith('_')})
            if not payload:raise ValueError('诡谈夜话回执为空')
            if hasattr(ctx,'pop_pending_frames'):frames.extend(ctx.pop_pending_frames())
            frames.append(DownFrame(self.sc,payload))
            return frames
        op.respond=respond
