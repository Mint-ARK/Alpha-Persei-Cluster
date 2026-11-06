"""Native Polyhedron unlock constraints and account-owned lifetime counters."""
import json
from polyhedron_flow import CONTRACT,terminal_level
from hodur_service import require


def progress(db,uid):
    rows=db.query("SELECT value_json FROM ops_common WHERE uid=? AND kind='polyhedron_progress_v1' AND item_id='lifetime'",(uid,))
    return json.loads(rows[0]['value_json']) if rows else {}


def add_progress(db,uid,**deltas):
    data=progress(db,uid)
    for key,value in deltas.items():
        require(type(value) is int and value>=0,'多维进度计数无效')
        data[key]=int(data.get(key,0))+value
    save_progress(db,uid,data)


def save_progress(db,uid,data):
    db.execute("INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,'polyhedron_progress_v1','lifetime',?) "
               "ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json",(uid,json.dumps(data)))


def record_clear(db,uid,run):
    data=progress(db,uid)
    leader=run['start_info']['leader']['hero_id']
    data['leaders']=sorted(set(data.get('leaders',[]))|{leader})
    # Only a measured terminal battle report proves a healthy clear.
    if run.get('hp_report_complete'):
        hero=next(h for h in run['hero_list'] if h['hero_id']==leader)
        if hero['health']*1000>=hero['max_health']*800:
            data['healthy_difficulty']=max(data.get('healthy_difficulty',0),run['difficulty'])
    save_progress(db,uid,data)


def unlock_beacon(db,uid,beacon):
    cfg=CONTRACT['beacons'].get(str(beacon));require(cfg is not None,'多维信标不存在')
    if db.query('SELECT 1 FROM polyhedron_beacon WHERE uid=? AND beacon_id=?',(uid,beacon)):return
    condition=cfg['condition']
    if condition:
        cond=CONTRACT['conditions'][str(condition)];kind=cond['type'];params=cond['params'];data=progress(db,uid)
        difficulty=max((r['difficulty_id'] for r in db.query('SELECT difficulty_id FROM polyhedron_difficulty WHERE uid=?',(uid,))),default=0)
        values={3001:difficulty,3002:terminal_level(db,uid)['id'],3003:data.get('coins',0),
                3004:data.get('healthy_difficulty',0),3005:data.get('upgrade_gates',0),
                3006:len(data.get('leaders',[])),3007:data.get('score',0),
                3008:data.get('shop_buys',0),3009:data.get('kills',0)}
        require(kind in values and values[kind]>=params[0],'多维信标解锁条件未达成')
    db.execute('INSERT INTO polyhedron_beacon(uid,beacon_id,update_ts) VALUES(?,?,0)',(uid,beacon))


def equip_astrolabes(db,uid,hero,ids):
    from polyhedron_service import get_poly_cfg
    require(str(hero) in get_poly_cfg()['poly_heroes'] and db.query('SELECT 1 FROM polyhedron_hero WHERE uid=? AND hero_id=?',(uid,hero)),'多维角色尚未解锁')
    require(len(ids)<=3 and len(ids)==len(set(ids)),'多维神格数量无效')
    for aid in ids:
        cfg=CONTRACT['astrolabes'].get(str(aid))
        require(cfg and cfg['hero_astrolabe_suit_id']//1000==hero,'多维神格不属于该角色')
        require(cfg['pre_astrolabe_id']==0 or cfg['pre_astrolabe_id'] in ids,'多维神格前置未装备')
    slots=list(ids)+[0]*3
    db.execute('UPDATE polyhedron_hero SET astrolabe_1=?,astrolabe_2=?,astrolabe_3=? WHERE uid=? AND hero_id=?',(*slots[:3],uid,hero))


def unlock_hero(ctx,uid,hero):
    from polyhedron_service import get_poly_cfg
    from inventory_service import InventoryService
    require(str(hero) in get_poly_cfg()['poly_heroes'],'多维角色不存在')
    require(ctx.db.query('SELECT 1 FROM hero WHERE uid=? AND id=? AND unlock=1',(uid,hero)),'账号尚未拥有该角色')
    if ctx.db.query('SELECT 1 FROM polyhedron_hero WHERE uid=? AND hero_id=?',(uid,hero)):return
    require(InventoryService.get_item_balance(ctx.db,uid,44)[1]>=1,'多维映射仪不足')
    from operations import _item_deduct
    _item_deduct(ctx,uid,44,1)
    ctx.db.execute('INSERT INTO polyhedron_hero(uid,hero_id,update_ts) VALUES(?,?,0)',(uid,hero))


def set_combo(db,uid,skill):
    from polyhedron_service import PolyhedronRunManager as M
    run=M.active(uid,db)
    cfg=CONTRACT['combos'].get(str(skill))
    require(skill==0 or cfg and set(cfg['cooperate_role_ids'])<=set(run['fight_hero_id_list']),'多维连携与当前编队不匹配')
    require(not run.get('latest') or run['latest'].get('interaction'),'多维战斗期间不能更换连携')
    run['cooperate_unique_skill_id']=skill;M.save_run(uid,db,run)
