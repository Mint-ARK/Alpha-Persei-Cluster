"""Hodur four-boss run, native HP carry and reward-calibrated private scoring."""
from contextlib import contextmanager
from functools import lru_cache
import copy
import json
from pathlib import Path
import uuid

import hodur_service as normal
from hodur_service import ACTIVITY, CFG, atomic, decode, require
from event_bus import bus, Events

STAGES=CFG['chapters']['4']['stages']
AFFIXES={int(k):v for k,v in CFG['affixes'].items()}


@lru_cache(maxsize=1)
def trials():
    return json.loads(Path(__file__).with_name('trial_system_data.json').read_text('utf-8'))['std_cfgs']


def read(db,uid):
    rows=db.query('SELECT stat_json FROM hodur_progress WHERE uid=? AND activity_id=? AND chapter_id=4',(uid,ACTIVITY))
    raw=decode(rows[0]['stat_json'],dict) if rows else {}
    state=copy.deepcopy(raw.get('challenge_v1',{}))
    defaults=dict(active=False,run_id='',revision=0,affixes=[],heroes=[],scores={},stage_best={},best_score=0,best_affixes=0,
                  last_status=[],last_stage=0,last_result=0,score_list=[0,0,0,0])
    require(isinstance(state,dict),'谧光挑战存档格式错误')
    for k,v in defaults.items():state.setdefault(k,v)
    require(type(state['active']) is bool and isinstance(state['run_id'],str),'谧光挑战运行标识错误')
    for k in ('revision','best_score','best_affixes','last_stage','last_result'):
        require(type(state[k]) is int and 0<=state[k]<=0xffffffff,'谧光挑战统计格式错误')
    require(isinstance(state['affixes'],list) and len(state['affixes'])==len(set(state['affixes']))
            and all(type(i) is int and i in AFFIXES for i in state['affixes']),'谧光挑战词缀格式错误')
    for name in ('scores','stage_best'):
        require(isinstance(state[name],dict) and all(str(i) in map(str,STAGES) and type(v) is int and 0<=v<=0xffffffff
            for i,v in state[name].items()),'谧光挑战成绩格式错误')
    for name in ('heroes','last_status'):
        require(isinstance(state[name],list) and len(state[name])<=3,'谧光挑战队伍格式错误')
        require(all(isinstance(h,dict) and all(type(h.get(k)) is int for k in ('hero_id','hero_blood','hero_max_blood'))
            and h['hero_id']>0 and 0<=h['hero_blood']<=h['hero_max_blood']<=0xffffffff for h in state[name]),'谧光挑战血量格式错误')
        require(len({h['hero_id'] for h in state[name]})==len(state[name]),'谧光挑战队伍重复')
    require(isinstance(state['score_list'],list) and len(state['score_list'])==4 and all(type(v) is int and 0<=v<=0xffffffff for v in state['score_list']),
            '谧光挑战评分明细格式错误')
    return state


def save(db,uid,state):
    db.execute('INSERT INTO hodur_progress(uid,activity_id,chapter_id,stat_json) VALUES(?,?,4,?) '
        'ON CONFLICT(uid,activity_id,chapter_id) DO UPDATE SET stat_json=excluded.stat_json',
        (uid,ACTIVITY,json.dumps(dict(challenge_v1=state))))


def payload(db,uid):
    s=read(db,uid)
    return dict(challenge_stage_id=4,challenge_buff_info=s['affixes'],challenge_max_score=s['best_score'],
        challenge_hero_stage_info=s['heroes'],last_status=s['last_status'],score_list=s['score_list'],
        challenge_boss_list=[dict(boss_id=i,score=s['scores'].get(str(i),0),max_score=s['stage_best'].get(str(i),0)) for i in STAGES])


def team_aliases(db,uid,team):
    require(isinstance(team,list) and 1<=len(team)<=3 and all(type(i) is int and i>0 for i in team),'谧光挑战出战队伍不合法')
    aliases={};actual=[]
    for ref in team:
        cfg=trials().get(str(ref))
        if cfg:
            hero=int(cfg['hero_id']);skins={hero,int(cfg.get('skin_id') or hero)}
        else:
            rows=db.query('SELECT id,using_skin,battle_using_skin FROM hero WHERE uid=? AND id=?',(uid,ref))
            require(bool(rows),'谧光挑战出战角色未拥有')
            hero=ref;skins={hero,*[int(rows[0][k]) for k in ('using_skin','battle_using_skin') if rows[0].get(k)]}
        actual.append(hero);aliases[str(ref)]=sorted({ref,*skins})
    require(len(set(actual))==len(actual),'谧光挑战不可重复出战同一角色')
    return aliases


def selected_affixes(opts):
    require(isinstance(opts,list),'谧光挑战词缀参数格式错误')
    values=[];seen=False
    for item in opts:
        require(isinstance(item,dict) and type(item.get('key')) is int and item['key']==1 and not seen,'未知或重复谧光挑战参数')
        values=item.get('value',[]);seen=True
    require(isinstance(values,list) and all(type(i) is int and i in AFFIXES for i in values)
        and len(set(values))==len(values),'未知或重复谧光挑战词缀')
    return sorted(values)


def entry(db,uid,stage,activity,team,opts):
    require(type(activity) is int and activity==ACTIVITY and stage in STAGES,'未知谧光挑战活动或关卡')
    history=normal.read(db,uid)
    require(all(s in history[c]['clears'] for c in history for s in normal.CHAPTERS[c]['stages']),'谧光挑战需完成全部普通章节')
    aliases=team_aliases(db,uid,team);affixes=selected_affixes(opts);state=read(db,uid)
    count=len(state['scores']);index=STAGES.index(stage)
    retry=bool(state['active'] and state['last_stage']==stage and (state['last_result']!=1 or index==count-1))
    if state['active']:
        require(affixes==state['affixes'],'谧光挑战进行中不能更换自限模块')
        require(index==count or retry,'谧光挑战关卡顺序不匹配')
        baseline=copy.deepcopy(state['last_status'] if retry else state['heroes'])
        eligible={h['hero_id'] for h in baseline if h['hero_blood']>0}
        require(set(team)==eligible,'谧光挑战需使用本轮存活队伍')
        by_id={h['hero_id']:h for h in baseline}
        baseline=[by_id[i] for i in team]+[h for h in baseline if h['hero_id'] not in eligible]
    else:
        require(index==0,'谧光挑战须从第一战开始')
        baseline=[dict(hero_id=i,hero_blood=1000000,hero_max_blood=1000000) for i in team]
    return dict(chapter_id=4,stage=stage,run_id=state['run_id'],revision=state['revision'],active=state['active'],
                team=list(team),aliases=aliases,affixes=affixes,before=baseline,retry=retry)


@contextmanager
def publication(ctx,uid,bid,token):
    if not token or token.get('chapter_id')!=4:
        yield token,None
        return
    try:
        with atomic(ctx):
            state=read(ctx.db,uid)
            require((state['run_id'],state['revision'],state['active'])==(token['run_id'],token['revision'],token['active']),
                '谧光挑战初始化期间进度已变化')
            state.update(active=True,affixes=token['affixes'],heroes=copy.deepcopy(token['before']),last_status=copy.deepcopy(token['before']))
            if not token['active']:state['run_id']=uuid.uuid4().hex
            if token['retry']:state['scores'].pop(str(token['stage']),None)
            state['revision']+=1
            save(ctx.db,uid,state)
            current=dict(token,run_id=state['run_id'],revision=state['revision'],active=True)
            from codec import encode
            packet=(ctx.codec_encode or encode)('sc_89421',normal.payload(ctx.db,uid))
            require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'谧光挑战初始化状态帧为空')
            yield current,bytes(packet)
    except BaseException:
        import middleware
        with middleware._BATTLE_LOCK:
            session=middleware._BATTLE_SESSIONS.get(bid)
            if session and session.get('uid')==uid and (session.get('hodur_context') or {}).get('chapter_id')==4:
                middleware._BATTLE_SESSIONS.pop(bid,None)
        raise


def check_battle(db,uid,session):
    token=session.get('hodur_context') or {};state=read(db,uid)
    require(token.get('chapter_id')==4 and state['active'] and token.get('run_id')==state['run_id']
        and token.get('revision')==state['revision'] and token.get('stage')==session.get('dest')
        and state['affixes']==token.get('affixes'),'谧光挑战状态已变化，请重新进入战斗')
    return state,token


def report_hp(report,token,*,required=True):
    chars=report.get('characters')
    if not required and ('characters' not in report or chars==[]):return None
    require(isinstance(chars,list) and bool(chars),'谧光战报缺少有效角色血量')
    out={};aliases=token['aliases']
    for char in chars:
        require(isinstance(char,dict),'谧光战报角色格式错误')
        ref_matches=[int(ref) for ref,ids in aliases.items() if char.get('hero_id') in ids]
        require(len(ref_matches)==1,'谧光战报角色与出战队伍不匹配')
        ref=ref_matches[0];hp=char.get('hero_blood');maximum=char.get('hero_max_blood')
        require(ref not in out and type(hp) is int and type(maximum) is int and -0x8000000000000000<=hp<=maximum<=0x7fffffffffffffff and maximum>0,
            '谧光战报角色血量不合法或重复')
        hp=max(0,hp)  # signed native HP can fall below zero after an overkill
        if maximum>0xffffffff:
            hp=(hp*1000000+maximum-1)//maximum;maximum=1000000
        out[ref]=dict(hero_id=ref,hero_blood=hp,hero_max_blood=maximum)
    require(set(out)==set(token['team']),'谧光战报角色血量不完整')
    return out


def score(report,affixes):
    """User-authorized private rule: four clears meet the highest reward tier.

    Native Lua reveals the four dimensions and efficiency, not their formula.
    No unverified record-ID is labeled as combo or mechanism consumption.
    """
    floor=(CFG['scoring']['clear_floor']+len(STAGES)*4-1)//(len(STAGES)*4)
    parts=[floor]*4
    milliseconds=report.get('battle_time');hits=report.get('total_hitted_num')
    if type(milliseconds) is int and milliseconds>0:
        limit=CFG['scoring']['time_bonus_limit_ms'];parts[0]+=floor*max(0,limit-milliseconds)//limit
    if type(hits) is int and hits>=0:
        limit=CFG['scoring']['hit_bonus_limit'];parts[1]+=floor*max(0,limit-hits)//limit
    efficiency=100+sum(AFFIXES[i]['point'] for i in affixes)
    return sum(parts)*efficiency//100,parts


def finish_battle(ctx,uid,session,result,report):
    with atomic(ctx):
        state,token=check_battle(ctx.db,uid,session)
        hp=report_hp(report,token)
        state['heroes']=[hp.get(h['hero_id'],h) for h in token['before']]
        stage=token['stage'];state['last_status']=copy.deepcopy(token['before'])
        state['last_stage']=stage;state['last_result']=result
        if result==1:
            points,parts=score(report,state['affixes']);state['scores'][str(stage)]=points
            state['stage_best'][str(stage)]=max(points,state['stage_best'].get(str(stage),0))
            state['score_list']=parts
            if len(state['scores'])==len(STAGES):state['best_affixes']=max(state['best_affixes'],len(state['affixes']))
        require(sum(state['scores'].values())<=0xffffffff,'谧光挑战总分超出协议范围')
        state['revision']+=1;save(ctx.db,uid,state)
        bus.emit_required(Events.HODUR_PROGRESS,ctx,uid)
        from codec import encode
        from middleware import DownFrame
        packet=(ctx.codec_encode or encode)('sc_89421',normal.payload(ctx.db,uid))
        require(isinstance(packet,(bytes,bytearray,memoryview)) and bool(packet),'谧光挑战结算状态帧为空')
        return [DownFrame(89421,bytes(packet))]


def settle(ctx,uid,activity):
    require(type(activity) is int and activity==ACTIVITY,'未知谧光挑战活动')
    with atomic(ctx):
        state=read(ctx.db,uid);require(state['active'],'谧光挑战尚未开始或已经结算')
        state['best_score']=max(state['best_score'],sum(state['scores'].values()))
        state.update(active=False,run_id=uuid.uuid4().hex,revision=state['revision']+1,affixes=[],heroes=[],scores={},
                     last_status=[],last_stage=0,last_result=0,score_list=[0,0,0,0])
        save(ctx.db,uid,state)
        bus.emit_required(Events.HODUR_PROGRESS,ctx,uid)
        # Lua cached the run before requesting 89424, then ResetData sums its
        # current scores locally. Do not erase that cache with an extra 89421.
        return dict(result=0)
