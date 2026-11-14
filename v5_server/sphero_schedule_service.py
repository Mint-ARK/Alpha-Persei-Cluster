"""Account-scoped SP Hero daily schedules and timed entrusts.

The legacy SQL schedule table mixed two unrelated protocol records. Keep it
untouched for investigation; the versioned ops_common record is authoritative.
"""
from collections import Counter
from copy import deepcopy
import json
import logging
from pathlib import Path
import random
import time

from core import Operation
from hodur_service import require
from inventory_service import InventoryService
from lazy_timer import get_daily_5am_ts

CFG=json.loads(Path(__file__).with_name('sphero_contract.json').read_text('utf-8'))
ACTIVITY=242841
KIND='sphero_schedule_v1'
SET=CFG['settings']
SCHEDULES={int(i):v for i,v in CFG['schedules'].items()}
ENTRUSTS={int(i):v for i,v in CFG['entrusts'].items()}
_log = logging.getLogger(__name__)


def uint(value):return type(value) is int and 0<=value<=0xffffffff


def pool():
    # No native selection weights are exposed; sample configured entries equally.
    return random.choices(sorted(ENTRUSTS),k=SET['activity_hero_challenge_task_choose_num'][0])


def read(db,uid):
    require(db.get('users',uid) is not None,'账号不存在')
    rows=db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',(uid,KIND,str(ACTIVITY)))
    now=int(time.time());day=get_daily_5am_ts(now)
    state=json.loads(rows[0]['value_json']) if rows else dict(version=1,day=day,schedules=[],pool=None,refresh=0,claimed=False,entrusts=[],earned=0)
    require(isinstance(state,dict) and state.get('version')==1 and uint(state.get('day')),'日程存档损坏')
    if 'entrust_completed' not in state:
        state['entrust_completed']=db.query('SELECT COALESCE(MAX(t.progress),0) n FROM task t JOIN task_cfg c ON c.task_id=t.task_id '
            'WHERE t.uid=? AND c.activity_id=240046 AND c.condition=160001',(uid,))[0]['n']
    require(uint(state['entrust_completed']),'累计委托记录损坏')
    require(type(state.get('claimed')) is bool and uint(state.get('refresh')) and uint(state.get('earned')),'日程计数损坏')
    for key in ('schedules','entrusts'):require(isinstance(state.get(key),list),'日程列表损坏')
    require(state.get('pool') is None or isinstance(state['pool'],list),'委托候选损坏')
    require(all(isinstance(v,dict) for v in state['schedules']+state['entrusts']),'日程条目损坏')
    require(all(type(i) is int and i in ENTRUSTS for i in (state['pool'] or [])),'委托候选损坏')
    schedules=state['schedules'];entrusts=state['entrusts']
    require(len(schedules)<=SET['activity_hero_challenge_schedule_limit'][0] and len({s.get('index') for s in schedules})==len(schedules),'日程位置重复')
    require(all(uint(s.get('index')) and 1<=s['index']<=4 and type(s.get('schedule_id')) is int and s['schedule_id'] in SCHEDULES
                and type(s.get('is_finish')) is bool for s in schedules),'日程条目损坏')
    require(len(entrusts)<=6 and len({e.get('index') for e in entrusts})==len(entrusts),'委托位置重复')
    require(all(uint(e.get('index')) and 1<=e['index']<=6 and type(e.get('entrust_id')) is int and e['entrust_id'] in ENTRUSTS
                and uint(e.get('start_time')) and uint(e.get('accepted_at')) for e in entrusts),'委托条目损坏')
    if state['day']!=day:
        state.update(day=day,schedules=[],pool=None,refresh=0,claimed=False)
    return state


def save(db,uid,state):
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
        'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
        (uid,KIND,str(ACTIVITY),json.dumps(state,ensure_ascii=False)))


def ensure_pool(state):
    if state['pool'] is None:state['pool']=pool()


def history_value(db,uid,cid):
    rows=db.query("SELECT data FROM history_cmd WHERE uid=? AND kind='history' AND key_id=?",(uid,str(cid)))
    if not rows or rows[0]['data'] in (None, ''):
        return 0

    value = rows[0]['data']
    try:
        if isinstance(value, str) and value.lstrip().startswith('{'):
            # Older saves stored the counter together with its display name.
            value = json.loads(value).get('data')
        if isinstance(value, str):
            value = int(value, 10)
        if uint(value):
            return value
    except (ValueError, RecursionError):
        pass
    _log.warning('日程历史计数格式无效，已跳过该条记录 (uid=%s, key_id=%s)', uid, cid)
    return 0


def earned(db,uid,state):
    return max(state['earned'],InventoryService.get_item_balance(db,uid,53100)[1],
               *(history_value(db,uid,cid) for cid in (112061,112062,112063)))


def max_slots(db,uid,state):
    total=earned(db,uid,state)
    return 3+sum(total>=CFG['conditions'][str(cid)]['params'][1] for _,cid in SET['activity_hero_challenge_task_line_num'])


def history_entries(db,uid):
    total=earned(db,uid,read(db,uid))
    return [dict(id=cid,data=total) for cid in (112061,112062,112063)]


def wire_entrusts(state):
    return [{k:e[k] for k in ('index','entrust_id','start_time')} for e in sorted(state['entrusts'],key=lambda e:e['index'])]


def due(e):return e['start_time']+ENTRUSTS[e['entrust_id']]['time']*60


def promote(state,now):
    # Finished active entries move behind waiting entries; their reward stays
    # claimable. A promoted job starts at the preceding job's actual end time.
    for _ in range(6):
        waiting=sorted((e for e in state['entrusts'] if e['start_time']==0),key=lambda e:e['index'])
        if not waiting:break
        occupied={e['index']:e for e in state['entrusts'] if e['index']<=3}
        free=next((i for i in range(1,4) if i not in occupied),None)
        finished=min((e for e in occupied.values() if e['start_time'] and due(e)<=now),key=due,default=None)
        if free is None and finished is None:break
        first=waiting[0];position=free if free is not None else finished['index']
        start=now if free is not None else due(finished)
        last=max(e['index'] for e in waiting)
        for e in waiting[1:]:e['index']-=1
        if free is None:finished['index']=last
        first.update(index=position,start_time=max(start,first['accepted_at']))


def snapshot(db,uid):
    # Initialization is persisted so refreshing the UI cannot reroll the pool.
    state=read(db,uid);ensure_pool(state);promote(state,int(time.time()));save(db,uid,state)
    stories=db.query("SELECT stage_id FROM sp_hero_challenge_stage WHERE uid=? AND activity_id=? AND stage_kind='unlock'",(uid,ACTIVITY))
    from sphero_battle_service import read as battle_state,train_payload
    return dict(activity_id=ACTIVITY,begin_entrust_list=wire_entrusts(state),entrust_id_list=state['pool'],entrust_refresh_times=state['refresh'],
        passed_chapter_level_list=sorted({r['stage_id'] for r in stories if 5170201<=r['stage_id']<=5170206}),
        train_list=train_payload(db,uid),challenge_times=battle_state(db,uid)['rewarded'])


def schedule_payload(db,uid):return dict(activity_id=ACTIVITY,schedule_list=read(db,uid)['schedules'])


def frame(ctx,sc,data):
    from middleware import DownFrame
    raw=ctx.codec_encode('sc_'+str(sc),data)
    require(bool(raw),'日程响应编码失败')
    return DownFrame(sc,raw)


def finish_in_state(state,kind):
    for s in sorted(state['schedules'],key=lambda s:s['index']):
        if SCHEDULES[s['schedule_id']]['server_type']==kind and not s['is_finish']:
            s['is_finish']=True;return deepcopy(s)
    return None


def finish(ctx,uid,kind):
    state=read(ctx.db,uid);changed=finish_in_state(state,kind)
    if changed:
        save(ctx.db,uid,state)
        ctx.append_frame(frame(ctx,83017,dict(activity_id=ACTIVITY,schedule_list=[changed])))


def cost(ctx,uid,items):
    totals=Counter()
    for i,n in items:totals[i]+=n
    require(all(InventoryService.get_item_balance(ctx.db,uid,i)[1]>=n for i,n in totals.items()),'委托所需物品不足')
    for i,n in totals.items():require(InventoryService.cost_item(ctx,uid,i,n),'委托扣除物品失败')


def give(ctx,uid,state,items):
    totals=Counter()
    for i,n in items:totals[i]+=n
    InventoryService.grant_items(ctx,uid,list(totals.items()),source='sphero_entrust')
    # The common InventoryService hook persists earned totals. Refresh the
    # caller's copy so its later save cannot overwrite that atomic update.
    state['earned']=read(ctx.db,uid)['earned']
    return [dict(id=i,num=n) for i,n in totals.items() if n>0]


def allowed_schedule(db,uid,sid):
    cfg=SCHEDULES[sid]
    from sphero_battle_service import stages,STORY
    from sphero_leisure_service import cleared,PUZZLES
    removed={112001:set(STORY)<=stages(db,uid)[0],112011:set(PUZZLES)<=set(cleared(db,uid))}
    require(not cfg['remove_condition'] or not all(removed.get(cid,False) for cid in cfg['remove_condition']),'此日程已全部完成')
    for cid in cfg['pre_condition']:
        if cid==112001:
            rows=db.query("SELECT stage_id FROM sp_hero_challenge_stage WHERE uid=? AND activity_id=? AND stage_kind='unlock'",(uid,ACTIVITY))
            require(set(range(5170201,5170207))<={r['stage_id'] for r in rows},'请先完成回响启示')
        else:require(history_value(db,uid,cid)>0,'日程尚未解锁')


def apply(ctx,uid,cmd,data):
    require(type(data.get('activity_id')) is int and data['activity_id']==ACTIVITY,'未知薇儿丹蒂活动')
    from generator import get_activity_list
    now=int(time.time())
    require(any(a['activity_id']==ACTIVITY and int(a.get('state') or 0)==1 and int(a.get('start_time') or 0)<=now<int(a.get('stop_time') or 0)
                for a in get_activity_list(ctx.db,uid)),'薇儿丹蒂活动未开放')
    state=read(ctx.db,uid);ensure_pool(state);now=int(time.time());out=dict(result=0);changed=None
    if cmd==83003:
        entries=data.get('schedule_info_list');require(isinstance(entries,list) and 0<len(entries)<=4,'日程数量无效')
        require(all(isinstance(s,dict) and uint(s.get('index')) and 1<=s['index']<=4 and type(s.get('schedule_id')) is int
                    and s['schedule_id'] in SCHEDULES and s.get('is_finish') is False for s in entries),'日程参数无效')
        require(len({s['index'] for s in entries})==len(entries),'日程位置重复')
        mapping={s['index']:s for s in state['schedules']}
        for s in entries:
            require(not mapping.get(s['index'],{}).get('is_finish'),'已完成日程不可替换')
            allowed_schedule(ctx.db,uid,s['schedule_id']);mapping[s['index']]=dict(index=s['index'],schedule_id=s['schedule_id'],is_finish=False)
        state['schedules']=sorted(mapping.values(),key=lambda s:s['index'])
    elif cmd==83005:
        require(not state['claimed'],'今日的日程奖励已领取')
        score=sum(SCHEDULES[s['schedule_id']]['score'] for s in state['schedules'] if s['is_finish'])
        require(score>=SET['activity_hero_challenge_schedule_award_point_limit'][0],'日程积分不足')
        state['claimed']=True;out['reward_list']=give(ctx,uid,state,CFG['activity']['daily_award'])
    elif cmd==83007:
        require(state['refresh']<SET['activity_hero_challenge_task_refresh_num'][0],'今日刷新次数已用完')
        if state['refresh']>=SET['activity_hero_challenge_task_free_refresh_num'][0]:cost(ctx,uid,CFG['activity']['refresh_cost'])
        state['refresh']+=1;state['pool']=pool();out['entrust_id_list']=state['pool']
    elif cmd==83009:
        idx=data.get('index');eid=data.get('entrust_id')
        require(uint(idx) and 1<=idx<=max_slots(ctx.db,uid,state),'委托位置尚未解锁')
        require(type(eid) is int and eid in ENTRUSTS and eid in state['pool'],'委托不在当前候选列表')
        require(not any(e['index']==idx for e in state['entrusts']),'委托位置已占用')
        cost(ctx,uid,ENTRUSTS[eid]['cost']);state['pool'].remove(eid)
        state['entrusts'].append(dict(index=idx,entrust_id=eid,start_time=now if idx<=3 else 0,accepted_at=now))
        promote(state,now);out['begin_entrust_list']=wire_entrusts(state);changed=finish_in_state(state,8)
    elif cmd in (83011,83018):
        idx=data.get('index');require(uint(idx),'委托位置无效')
        entry=next((e for e in state['entrusts'] if e['index']==idx),None);require(entry is not None,'委托不存在')
        if cmd==83011:state['entrusts'].remove(entry);promote(state,now)
        else:
            count=data.get('use_cnt');require(type(count) is int and count>0,'加速数量无效')
            remaining=due(entry)-now
            seconds=SET['activity_hero_challenge_certificate_time'][0]*60
            require(entry['start_time']>0 and remaining>0 and count<=(remaining+seconds-1)//seconds,'委托无需加速或数量过多')
            cost(ctx,uid,[(CFG['activity']['certificate_cost_id'],count)])
            entry['start_time']=max(1,entry['start_time']-count*seconds)
        out['begin_entrust_list']=wire_entrusts(state)
    elif cmd==83013:
        indices=data.get('index_list');require(isinstance(indices,list) and indices and all(uint(i) for i in indices)
            and len(set(indices))==len(indices),'领奖位置无效或重复')
        selected=[next((e for e in state['entrusts'] if e['index']==i),None) for i in indices]
        require(all(e is not None and e['start_time']>0 and due(e)<=now for e in selected),'委托尚未完成或已领取')
        rewards=[r for e in selected for r in ENTRUSTS[e['entrust_id']]['reward_list']]
        state['entrusts']=[e for e in state['entrusts'] if e['index'] not in indices]
        state['entrust_completed']+=len(indices)
        out['reward_list']=give(ctx,uid,state,rewards);promote(state,now);out['begin_entrust_list']=wire_entrusts(state)
        from task_listener import _trigger_task_condition,_push_task_diff
        _push_task_diff(ctx,uid,_trigger_task_condition(ctx.db,uid,condition=160001,delta=len(indices)))
    elif cmd==83040:
        promote(state,now);out['begin_entrust_list']=wire_entrusts(state)
    else:require(False,'未知日程操作')
    save(ctx.db,uid,state)
    if changed:ctx.append_frame(frame(ctx,83017,dict(activity_id=ACTIVITY,schedule_list=[changed])))
    return out


class SPHeroScheduleOp(Operation):
    prepare_response_in_transaction=True

    def apply(self,data,ctx):return apply(ctx,self.uid,self.cmd,data)

    def respond(self,result,data,ctx):
        # ACK callbacks already update local schedules, pool and entrust entries.
        from operations import _refresh_frames
        frames=_refresh_frames(ctx,self.uid,strict=True)
        frames.append(frame(ctx,self.sc,result))
        return frames
