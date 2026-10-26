"""Onboarding commands, using fresh-save rules only for the experiment."""
from core import Operation, OperationError, operation
from middleware import DownFrame
from operations import NoobSignInOp, NoobLevelRewardOp, NoobAccumulateRewardOp, TaskSubmitOp, TaskOneKeySubmitOp, _item_add, _refresh_frames
import new_player_service as fresh


class FreshNoobMixin:
    prepare_response_in_transaction = True

    def apply(self,data,ctx):
        if not fresh.enabled(ctx.db,self.uid):
            return super().apply(data,ctx)
        try:
            rewards=fresh.claim_noob(ctx.db,self.uid,self.cmd,data,
                                     lambda item,num:_item_add(ctx,self.uid,item,num))
        except ValueError as exc:
            raise OperationError(2,str(exc)) from exc
        return dict(rewards=rewards)

    def respond(self,result,data,ctx):
        if not fresh.enabled(ctx.db,self.uid):
            return super().respond(result,data,ctx)
        ack=ctx.codec_encode('sc_'+str(self.sc),dict(result=0,reward_list=result['rewards']))
        state=ctx.codec_encode('sc_59011',fresh.payload(ctx.db,self.uid,59011))
        tasks=ctx.codec_encode('sc_28001',fresh.payload(ctx.db,self.uid,28001))
        if not ack or not state or not tasks:
            raise ValueError('新手领取必要响应为空')
        frames=[DownFrame(self.sc,ack),DownFrame(59011,state),DownFrame(28001,tasks)]
        return frames+(_refresh_frames(ctx,self.uid,strict=True) if result['rewards'] else [])


@operation
class FreshNoobSignOp(FreshNoobMixin,NoobSignInOp):
    pass


@operation
class FreshNoobLevelOp(FreshNoobMixin,NoobLevelRewardOp):
    pass


@operation
class FreshNoobAccumulateOp(FreshNoobMixin,NoobAccumulateRewardOp):
    pass


class FreshTaskMixin:
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        if fresh.enabled(ctx.db,self.uid):
            ids=[data.get('id') or data.get('task_id')] if self.cmd==28010 else data.get('id_list') or data.get('ids') or []
            if not ids:
                kind=int(data.get('type') or 0)
                rows=ctx.db.query('''SELECT t.task_id,t.task_type FROM task t JOIN task_cfg c ON c.task_id=t.task_id
                    WHERE t.uid=? AND COALESCE(t.claimed_ts,0)=0 AND t.progress>=COALESCE(c.need,1)''',(self.uid,))
                ids=[r['task_id'] for r in rows if not kind or r['task_type']==kind]
            if isinstance(ids,(int,str)):
                ids=[ids]
            for tid in ids:
                meta=fresh.config()['tasks'].get(str(tid)) or {}
                if meta.get('type')==fresh.config()['noob']['noob_task_type']:
                    try:
                        state=fresh.require_noob_active(ctx.db,self.uid)
                    except ValueError as exc:
                        raise OperationError(2,str(exc)) from exc
                    if int(meta.get('phase') or 1)>state['max_phase']:
                        raise OperationError(2,'新手任务阶段尚未开放')
        result=super().apply(data,ctx)
        if fresh.enabled(ctx.db,self.uid):
            fresh.ensure_tasks(ctx.db,self.uid)
        return result

    def respond(self,result,data,ctx):
        if not fresh.enabled(ctx.db,self.uid):
            return super().respond(result,data,ctx)
        ack=ctx.codec_encode('sc_'+str(self.sc),dict(result=0,reward_list=result['rewards']))
        if not ack:
            raise ValueError('新手任务必要回执为空')
        frames=[DownFrame(self.sc,ack)]
        if result['rewards']:
            frames+=_refresh_frames(ctx,self.uid,strict=True)
        if fresh.enabled(ctx.db,self.uid):
            for cmd in (28001,59011):
                payload=ctx.codec_encode('sc_'+str(cmd),fresh.payload(ctx.db,self.uid,cmd))
                if not payload:
                    raise ValueError('新手任务必要快照为空')
                frames.append(DownFrame(cmd,payload))
        return frames


@operation
class FreshTaskSubmitOp(FreshTaskMixin,TaskSubmitOp):
    pass


@operation
class FreshTaskOneKeyOp(FreshTaskMixin,TaskOneKeySubmitOp):
    pass


class GuideCompleteOp(Operation):
    prepare_response_in_transaction = True
    weak=False

    def validate(self,data,ctx):
        value=data.get('mod_guide_id') if isinstance(data,dict) else None
        if type(value) is not int or not 0<value<2**32:
            raise OperationError(2,'引导编号非法')
        return data

    def apply(self,data,ctx):
        ctx.db.upsert('guide',self.uid,dict(guide_id=data['mod_guide_id'],kind='weak' if self.weak else 'base'),keys=('uid','guide_id'))
        if not ctx.db.query('SELECT 1 FROM guide WHERE uid=? AND guide_id=?',(self.uid,data['mod_guide_id'])):
            raise ValueError('引导完成记录未保存')
        return {}

    def respond(self,result,data,ctx):
        ack=ctx.codec_encode('sc_'+str(self.sc),dict(result=0))
        if not ack:
            raise ValueError('引导完成回执为空')
        return [DownFrame(self.sc,ack)]


@operation
class StrongGuideCompleteOp(GuideCompleteOp):
    cmd=12012
    sc=12013


@operation
class WeakGuideCompleteOp(GuideCompleteOp):
    cmd=12112
    sc=12113
    weak=True
