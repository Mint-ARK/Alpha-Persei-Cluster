"""失序深区配置队列、挑战代次及开战身份。SQL仍由AccountDB写入。"""
import json
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from mythic_cycle import MythicPeriodExpired, bounds, final_payload, check_session


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name('mythic_final_cfg.json').read_text('utf-8'))


def profile():
    import res_version_manager as versions
    return '311' if int(versions.get_version_config()['theme_id']) >= 44 else '229'


def definition(difficulty):
    row=catalog()[profile()]['difficulties'].get(str(int(difficulty)))
    if row is None:
        raise ValueError('失序深区档位不存在')
    return row


def stages(difficulty):
    import mythic_affix_cfg
    choices=definition(difficulty)['stages']
    return list(choices[mythic_affix_cfg.get_semiweekly_cycle_info()[0] % len(choices)])


def queue(difficulty, rows):
    count=int(definition(difficulty)['team_num'])
    if not isinstance(rows,list):raise ValueError('失序深区队列无效')
    if not rows:return []
    found={}
    for row in rows:
        tid=int(row['team_id']);state=int(row['clear_state']);elapsed=int(row['use_time'])
        if tid not in range(1,count+1) or tid in found or state not in (0,1) or elapsed<0:
            raise ValueError('失序深区队伍记录无效')
        found[tid]=dict(team_id=tid,clear_state=state,use_time=elapsed if state else 0)
    return [found.get(tid,dict(team_id=tid,clear_state=0,use_time=0)) for tid in range(1,count+1)]


def next_team(progress):
    rows=queue(progress['now_difficulty'],progress['challenge_info'])
    if not rows:return 1
    return next((row['team_id'] for row in rows if row['clear_state']==0),0)


def generation(db,uid):
    rows=db.query('SELECT value FROM server_global_config WHERE key=?',(f'mythic.final_attempt_v1.{int(uid)}',))
    value=int(rows[0]['value']) if rows else 0
    if value<0:raise ValueError('失序深区挑战代次无效')
    return value


def entry(db,uid,stage,activity=0):
    progress=db.get_mythic_final_progress(uid)
    diff=int(progress['now_difficulty']);route=stages(diff);stage=int(stage);activity=int(activity or 0)
    # 原生activity是105，不是热度；兼容旧测试端传当前热度及省略activity。
    if activity not in (0,105,diff):raise ValueError('失序深区活动或档位不匹配')
    if stage in route:team=route.index(stage)+1
    elif 1<=stage<=int(definition(diff)['team_num']):team=stage
    else:raise ValueError('失序深区关卡与当前档位不符')
    expected=next_team(progress)
    if team != (expected or 1):raise ValueError('失序深区队伍顺序不匹配')
    return dict(cycle_start=bounds()[0],difficulty=diff,generation=generation(db,uid),
                team_id=team,stage_id=route[team-1],profile=profile(),restart=expected==0)


def validate(db,uid,session,*,after_clear=False):
    check_session(session,uid)
    snapshot=session.get('mythic_context')
    if not snapshot:raise MythicPeriodExpired('失序深区缺少挑战身份，请重新进入')
    progress=db.get_mythic_final_progress(uid)
    try:
        current=entry(db,uid,session['dest'],session.get('activity_id',0)) if not after_clear else None
        if (snapshot['cycle_start']!=bounds()[0] or snapshot['difficulty']!=progress['now_difficulty']
                or snapshot['generation']!=generation(db,uid) or snapshot['profile']!=profile()):
            raise ValueError('失序深区挑战已切换或重置')
        if current and any(snapshot[k]!=current[k] for k in ('difficulty','team_id','stage_id','profile')):
            raise ValueError('失序深区队伍已推进')
    except (ValueError,KeyError,TypeError) as exc:
        raise MythicPeriodExpired(str(exc)) from exc
    return snapshot


@contextmanager
def publication(db,uid,bid,snapshot):
    if snapshot is None:
        yield
        return
    from account_db import savepoint_transaction
    try:
        with savepoint_transaction(db):
            yield
    except BaseException:
        # battle_id由进程级单调水位保证唯一。注册后SQL提交若失败，
        # 也须撤销尚未向客户端交付的会话，不能留下孤儿挑战。
        import middleware
        with middleware._BATTLE_LOCK:
            session=middleware._BATTLE_SESSIONS.get(bid)
            if session and session.get('uid')==uid and session.get('mythic_context'):
                middleware._BATTLE_SESSIONS.pop(bid,None)
        raise


class MythicMutationOperation:
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        from core import OperationError
        if ctx.db is None:raise OperationError(2,'失序深区存档不可用')
        try:
            changed=(ctx.db.set_mythic_final_difficulty(self.uid,int(data.get('difficulty_id') or 0))
                     if self.cmd==44024 else ctx.db.reset_mythic_final_team(self.uid))
        except ValueError as exc:raise OperationError(2,str(exc)) from exc
        return dict(changed=bool(changed))

    def respond(self,result,data,ctx):
        from middleware import DownFrame
        objects=[(self.sc,dict(result=0))]
        # SelectLevel只更新热度，不会清旧队列；真正切档需后续当前状态。
        # ReSetTeam成功回调已ClearBattleData，无需重复全量。
        if self.cmd==44024 and result['changed']:
            objects.append((44023,final_payload(ctx.db.get_mythic_final_progress(self.uid))))
        frames=[]
        for sc,obj in objects:
            payload=ctx.codec_encode('sc_'+str(sc),obj)
            if not isinstance(payload,(bytes,bytearray,memoryview)) or not payload:raise ValueError('失序深区必要响应为空')
            frames.append(DownFrame(sc,bytes(payload)))
        return frames
