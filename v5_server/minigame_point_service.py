"""One shared implementation for ActivityPointReward's native claim contract."""
from collections import Counter
import json
from pathlib import Path
import time

from account_db import savepoint_transaction
from core import Operation,OperationError
from inventory_service import InventoryService
from operation_notifications import checkpoint_notifications,restore_notifications

CATALOG=json.loads(Path(__file__).with_name('activity_point_reward_cfg.json').read_text('utf-8'))


def rewards_for(activity_id):
    return {int(rid):dict(need=row['need'],need_item_id=row['need_item_id'],rewards=row['reward'])
            for rid,row in CATALOG.items() if row['activity_id']==activity_id}


def require(ok,message):
    if not ok:raise OperationError(2,message)


def claim(ctx,uid,reward_ids):
    checkpoint=checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            return _claim(ctx,uid,reward_ids)
    except BaseException:
        restore_notifications(ctx,checkpoint)
        raise


def _claim(ctx,uid,reward_ids):
    require(isinstance(reward_ids,(list,tuple)) and bool(reward_ids),'缺少积分奖励档位')
    require(all(type(rid) is int and rid>0 for rid in reward_ids),'积分奖励ID不合法')
    require(len(set(reward_ids))==len(reward_ids),'积分奖励档位重复')
    selected=[]
    for rid in reward_ids:
        row=CATALOG.get(str(rid))
        require(row is not None and int(row['activity_id'])>0 and bool(row['reward']),'未知积分奖励档位')
        require(int(row['need'])>=0 and all(type(i) is int and type(n) is int and i>0 and n>0 for i,n in row['reward']),
                '积分奖励配置不完整')
        selected.append((rid,row))

    from generator import get_activity_list
    now=int(time.time())
    visible={int(r['activity_id']) for r in get_activity_list(ctx.db,uid)
             if r.get('state')==1 and int(r.get('start_time') or 0)<=now
             and (not r.get('stop_time') or int(r['stop_time'])>now)}
    claimed={int(r['reward_id']) for r in ctx.db.query('SELECT reward_id FROM activity_point_reward WHERE uid=?',(uid,))}
    special={}
    for activity,table,column in ((321201,'skuld_progress','reward_list'),(1101,'rogueteam_user','rewarded_list_json')):
        if not any(row['activity_id']==activity for _,row in selected):continue
        clause='activity_id=321201' if activity==321201 else 'template_id=100001'
        rows=ctx.db.query(f'SELECT {column} FROM {table} WHERE uid=? AND {clause}',(uid,))
        values=json.loads(rows[0][column] or '[]') if rows else []
        require(isinstance(values,list) and all(type(rid) is int for rid in values),'已领积分记录损坏')
        special[activity]=values
        claimed.update(values)
    require(not claimed.intersection(reward_ids),'积分奖励已领取')

    balances={}
    for rid,row in selected:
        activity=int(row['activity_id']);require(activity in visible,'积分奖励所属活动未开放')
        item=int(row['need_item_id'])
        if activity==1101:
            from rogueteam_service import RogueTeamConfig
            item=int(RogueTeamConfig.get().cfg['main']['point_item'])
        source=('item',item) if item else ('score',activity)
        if source not in balances:
            if item:balances[source]=InventoryService.get_item_balance(ctx.db,uid,item)[1]
            else:
                rows=ctx.db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=?',(uid,activity))
                balances[source]=int(rows[0]['point'] or 0) if rows else 0
        require(balances[source]>=int(row['need']),'活动积分未达奖励条件')

    rewards=Counter()
    for rid,row in selected:
        ctx.db.execute('INSERT INTO activity_point_reward(uid,activity_id,reward_id,update_ts) VALUES(?,?,?,?)',
                       (uid,int(row['activity_id']),rid,now))
        for iid,num in row['reward']:rewards[iid]+=num
        if row['activity_id'] in special:special[row['activity_id']].append(rid)
    if 321201 in special:
        ctx.db.execute('INSERT INTO skuld_progress(uid,activity_id,reward_list,update_ts) VALUES(?,321201,?,?) '
                       'ON CONFLICT(uid,activity_id) DO UPDATE SET reward_list=excluded.reward_list,update_ts=excluded.update_ts',
                       (uid,json.dumps(special[321201]),now))
    if 1101 in special:
        ctx.db.upsert('rogueteam_user',uid,dict(template_id=100001,rewarded_list_json=json.dumps(special[1101]),update_ts=now),keys=('uid','template_id'))
    items=list(rewards.items())
    summary=InventoryService.grant_items(ctx,uid,items,source='activity_point_reward')
    actual=InventoryService.rewards_after_overflow(items,summary)
    return dict(claimed_ids=list(reward_ids),reward_list=[dict(id=i,num=n) for i,n in actual],
                rogue_rewards=special.get(1101))


class ActivityPointClaimOp(Operation):
    cmd,sc=60054,60055
    prepare_response_in_transaction=True

    def apply(self,data,ctx):return claim(ctx,self.uid,data.get('point_reward_id_list'))

    def respond(self,result,data,ctx):
        from middleware import DownFrame
        from operations import _refresh_frames
        frames=_refresh_frames(ctx,self.uid,strict=True)
        if result['rogue_rewards'] is not None:
            payload=ctx.codec_encode('sc_88309',dict(template_id=100001,reward_list=result['rogue_rewards']))
            if not payload:raise ValueError('虚构推演积分领奖状态帧为空')
            frames.append(DownFrame(88309,payload))
        native_ids=[rid for rid in result['claimed_ids'] if CATALOG[str(rid)]['activity_id'] in (323801,321201)]
        if native_ids:
            payload=ctx.codec_encode('sc_60107',dict(reward_info_list=native_ids))
            if not payload:raise ValueError('常驻活动已领积分差量为空')
            frames.append(DownFrame(60107,payload))
        payload=ctx.codec_encode('sc_60055',dict(result=0,reward_list=result['reward_list']))
        if not payload:raise ValueError('积分领奖回执为空')
        frames.append(DownFrame(60055,payload))
        # ActivityPointAction.OnReceivePointReward marks the requested IDs locally.
        # Osiris and Skuld own separate callback caches: update their new IDs in
        # the shared resident cache, never replay the full reward history.
        return frames
