"""Account-owned Polyhedron mutations and native response ordering."""
import json
from pathlib import Path
from core import Operation
from hodur_service import require

CONTRACT=json.loads(Path(__file__).with_name('polyhedron_contract.json').read_text('utf-8'))

def frame(ctx,cmd,data):
    from middleware import DownFrame
    raw=ctx.codec_encode('sc_'+str(cmd),data)
    require(isinstance(raw,(bytes,bytearray,memoryview)) and bool(raw),'多维必要帧编码失败: '+str(cmd))
    return DownFrame(cmd,bytes(raw))

def snapshot(ctx,uid):
    from weekly_challenge_service import get_polyhedron_data
    return frame(ctx,18001,get_polyhedron_data(uid,ctx.db,res_version=getattr(ctx,'res_version',None)))


def settlement_frames(ctx,uid,end):
    if end is None:return []
    from operations import _refresh_frames
    return [frame(ctx,18005,dict(end_info=end))]+_refresh_frames(ctx,uid,strict=True)


def cold_settlement(ctx,uid):
    from polyhedron_service import PolyhedronRunManager
    run=PolyhedronRunManager.load_run(uid,ctx.db)
    if run and run.get('state')==3 and run.get('end_info'):
        return [frame(ctx,18005,dict(end_info=run['end_info']))]
    return []


def terminal_level(db,uid):
    rows=db.query('SELECT num FROM currency WHERE uid=? AND id=46',(uid,))
    exp=rows[0]['num'] if rows else 0
    return max((r for r in CONTRACT['levels'].values() if r['exp']<=exp),key=lambda r:r['id'])

def terminal(db,uid,ids):
    require(len(ids)==len(set(ids)) and all(str(i) in CONTRACT['terminals'] for i in ids),'多维终端配置无效')
    current=terminal_level(db,uid)
    prior={r['terminal_id'] for r in db.query('SELECT terminal_id FROM polyhedron_terminal WHERE uid=?',(uid,))}
    require(prior<=set(ids),'移除多维终端需要使用重置次数')
    require(sum(CONTRACT['terminals'][str(i)]['cost'] for i in ids)<=current['point'],'多维终端点数不足')
    for i in ids:
        cfg=CONTRACT['terminals'][str(i)]
        require(cfg['need_level']<=current['id'],'多维终端等级不足')
    reachable={0}
    while True:
        added={i for i in ids if i not in reachable and any(p in reachable for p in CONTRACT['terminals'][str(i)]['pre_id_list'])}
        if not added:break
        reachable.update(added)
    require(set(ids)<=reachable,'多维终端必须连接已解锁根节点')
    db.execute('DELETE FROM polyhedron_terminal WHERE uid=?',(uid,))
    for i in ids:db.execute('INSERT INTO polyhedron_terminal(uid,terminal_id,update_ts) VALUES(?,?,0)',(uid,i))

class PolyhedronMutationOperation(Operation):
    prepare_response_in_transaction=True

    def apply(self,data,ctx):
        from polyhedron_service import PolyhedronRunManager as M
        cmd=self.cmd;uid=self.uid;db=ctx.db;end=None
        if cmd==18010:M.start_run(uid,db,data.get('hero_id_list') or [],data.get('beacon_id_list') or [],int(data.get('difficulty') or 1))
        elif cmd==18012:
            state=M.select_reward(uid,db,int(data.get('index',0)))
            if state['state']==3:end=M.settle_run(uid,db,ctx)
        elif cmd==18014:M.select_gate(uid,db,int(data.get('index',0)))
        elif cmd==18016:M.reset_run(uid,db)
        elif cmd==18018:end=M.settle_run(uid,db,ctx)
        elif cmd==18020:M.enlist_hero(uid,db,int(data.get('hero_id',0)))
        elif cmd==18022:M.buy_shop_item(uid,db,int(data.get('index',0)))
        elif cmd==18024:M.refresh_shop(uid,db)
        elif cmd==18026:M.recover_shop_blood(uid,db)
        elif cmd==18028:M.switch_team(uid,db,data.get('fight_id_list') or [])
        elif cmd==18030:M.reroll_reward(uid,db)
        elif cmd==18032:terminal(db,uid,data.get('upgrade_id_list') or [])
        elif cmd==18034:db.execute('UPDATE polyhedron_meta SET is_new=0 WHERE uid=?',(uid,))
        elif cmd==66002:
            rows=db.query('SELECT MIN(reset_times) AS remaining FROM polyhedron_meta WHERE uid=?',(uid,))
            require(rows and (rows[0]['remaining'] or 0)>0,'多维终端重置次数不足')
            require(db.query('SELECT 1 FROM polyhedron_terminal WHERE uid=?',(uid,)),'多维终端无需重置')
            db.execute('DELETE FROM polyhedron_terminal WHERE uid=?',(uid,))
            db.execute('UPDATE polyhedron_meta SET reset_times=reset_times-1 WHERE uid=?',(uid,))
        elif cmd==66004:
            from polyhedron_account import equip_astrolabes
            equip_astrolabes(db,uid,int(data.get('hero_id',0)),data.get('astrolabe_id_list') or [])
        elif cmd==66034:
            from polyhedron_account import unlock_beacon
            unlock_beacon(db,uid,int(data.get('beacon_id',0)))
        elif cmd==66036:
            from polyhedron_account import unlock_hero
            unlock_hero(ctx,uid,int(data.get('hero_id',0)))
        elif cmd==66038:
            from polyhedron_account import set_combo
            set_combo(db,uid,int(data.get('cooperate_unique_skill_id',0)))
        else:raise ValueError('未知多维操作')
        return dict(end_info=end)

    def respond(self,result,data,ctx):
        frames=settlement_frames(ctx,self.uid,result.get('end_info'))
        if self.cmd==66036:
            from operations import _refresh_frames
            frames+=_refresh_frames(ctx,self.uid,strict=True)
        frames.append(snapshot(ctx,self.uid))
        frames.append(frame(ctx,self.sc,dict(result=0)))
        return frames
