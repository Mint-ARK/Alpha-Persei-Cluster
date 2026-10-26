"""Native activity content; player progress always comes from the selected save."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

PATH = Path(__file__).with_name('data') / 'activity_content_cfg.json'


@lru_cache(maxsize=1)
def _data():
    raw = PATH.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def catalog(version=None):
    import res_version_manager as rvm
    version = str(version or rvm.get_current_version())
    baseline = '311' if rvm.get_version_config(version).get('theme_id', 43) >= 44 else '229'
    return _data()[0]['versions'][baseline]


def sync_catalogs(db, version=None):
    """Idempotent fresh-save migration of definitions, never task/purchase state."""
    cfg = catalog(version)
    stamp = _data()[1] + (':311' if cfg is _data()[0]['versions']['311'] else ':229')
    if db.get_system_config('save.activity_content_catalog') == stamp:
        return
    with db.transaction():
        db.executemany('''INSERT INTO task_cfg
            (task_id,name,task_type,condition,desc,need,activity_id,pre_id,reward_json,additional_parameter)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
            name=excluded.name,task_type=excluded.task_type,condition=excluded.condition,
            desc=excluded.desc,need=excluded.need,activity_id=excluded.activity_id,
            pre_id=excluded.pre_id,reward_json=excluded.reward_json,
            additional_parameter=excluded.additional_parameter''',
            [(int(tid), t['name'], t['type'], t['condition'], t['desc'], t['need'],
              t['activity_id'], t['pre_id'], json.dumps(t['reward']),
              json.dumps(t.get('additional_parameter') or [])) for tid, t in cfg['tasks'].items()])
        db.executemany('''INSERT INTO shop_goods
            (goods_id,item_id,name,cost_type,cost_id,cost,cheap_cost,discount,limit_num,
             refresh_cycle,open_time,close_time,tag,detail_json,shop_id)
            VALUES(?,?,'',?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(goods_id) DO UPDATE SET
            item_id=excluded.item_id,cost_type=excluded.cost_type,cost_id=excluded.cost_id,
            cost=excluded.cost,cheap_cost=excluded.cheap_cost,discount=excluded.discount,
            limit_num=excluded.limit_num,refresh_cycle=excluded.refresh_cycle,
            open_time=excluded.open_time,close_time=excluded.close_time,tag=excluded.tag,
            detail_json=excluded.detail_json,shop_id=excluded.shop_id''',
            [(int(gid), g['give_id'], g['cost_type'], g['cost_id'], g['cost'], g['cheap_cost'],
              g['discount'], g['limit_num'], g['refresh_cycle'], json.dumps(g['open_time']),
              json.dumps(g['close_time']), g['tag'], json.dumps(g), g['shop_id'])
             for gid, g in cfg['goods'].items()])
        db.set_system_config('save.activity_content_catalog', stamp)


def complete_activity_tree(activities, version=None):
    """Fill open parents' children and reopen their captured task/shop directories."""
    data = catalog(version)
    cfg = data['activities']
    content_nodes = {int(k) for k, a in cfg.items() if int(a['activity_template']) == 4}
    content_nodes.update(int(s['activity_id']) for s in data['shops'].values())
    result = {int(a['activity_id']): deepcopy(a) for a in activities}
    pending = [aid for aid, a in result.items() if int(a['state']) == 1]
    visited = set()
    while pending:
        aid = pending.pop()
        if aid in visited:
            continue
        visited.add(aid)
        parent = result[aid]
        native = cfg.get(str(aid), {})
        children = parent.get('sub_activity_id_list') or native.get('sub_activity_list') or []
        parent['sub_activity_id_list'] = list(children)
        for child in children:
            child = int(child)
            if child not in result:
                row = cfg.get(str(child))
                if not row:
                    continue
                result[child] = dict(activity_id=child, start_time=parent['start_time'],
                    stop_time=parent['stop_time'], state=parent['state'], theme=row['activity_theme'],
                    template=row['activity_template'], sub_activity_id_list=list(row['sub_activity_list']))
            elif child in content_nodes and int(result[child]['state']) != 1:
                result[child].update(state=1, start_time=parent['start_time'], stop_time=parent['stop_time'])
            if int(result[child]['state']) == 1:
                pending.append(child)
    return list(result.values())


def ensure_tasks(db, uid, activities, version=None, now=None):
    """Add tasks for open activity nodes; no copied completion or synthetic rewards."""
    cfg = catalog(version)
    opened = {int(a['activity_id']) for a in activities if int(a['state']) == 1}
    existing = {int(t['task_id']) for t in db.query('SELECT task_id FROM task WHERE uid=?', (uid,))}
    from skin_draw_service import completed_story_ids
    finished = completed_story_ids(db, uid, version)
    from lazy_timer import get_daily_5am_ts
    login_cycle = int((db.get('game_user', uid) or {}).get('last_login_task_cycle_ts') or 0)
    logged_today = login_cycle == get_daily_5am_ts(now)
    level = int(db.get('users', uid)['level'])
    rows = []
    for tid, task in cfg['tasks'].items():
        tid = int(tid)
        if tid in existing or not task['activity_id'] or int(task['activity_id']) not in opened:
            continue
        progress = min(level, int(task['need'])) if task['condition'] == 2 else 0
        if task['condition'] == 800 and finished.intersection(task.get('additional_parameter') or []):
            progress = min(1, int(task['need']))
        if task['condition'] == 1 and logged_today:
            progress = min(1, int(task['need']))
        rows.append((uid, tid, task['name'], task['type'], progress))
    db.executemany('''INSERT OR IGNORE INTO task
        (uid,task_id,name,task_type,progress,complete_flag,expired_ts,claimed_ts)
        VALUES(?,?,?,?,?,0,0,0)''', rows)
    reconcile_story_tasks(db, uid, version)
    return len(rows)


def reconcile_story_tasks(db, uid, version=None):
    """Recover stranded existing tasks from real played records, never grant rewards."""
    from skin_draw_service import completed_story_ids
    finished = completed_story_ids(db, uid, version)
    if not finished:
        return []
    native = catalog(version)['tasks']
    changed = []
    for row in db.query('''SELECT task_id,progress FROM task WHERE uid=?
            AND COALESCE(complete_flag,0)=0 AND COALESCE(claimed_ts,0)=0''', (uid,)):
        task = native.get(str(row['task_id']))
        if not task or task['condition'] != 800 or not task['activity_id']:
            continue
        if finished.intersection(task.get('additional_parameter') or []):
            target = min(1, int(task['need']))
            if int(row['progress'] or 0) < target:
                changed.append(dict(id=row['task_id'], progress=target, complete_flag=0))
    db.executemany('''UPDATE task SET progress=? WHERE uid=? AND task_id=?
        AND COALESCE(complete_flag,0)=0 AND COALESCE(claimed_ts,0)=0 AND COALESCE(progress,0)<?''',
        [(r['progress'], uid, r['id'], r['progress']) for r in changed])
    return changed


def merge_shop_states(shops, db, uid, version=None):
    """Static activity shelves do not depend on captured login templates."""
    import time
    cfg = catalog(version)
    result = {int(s['shop_id']): deepcopy(s) for s in shops}
    now = int(time.time())
    purchases = {(int(p['shop_id']), int(p['goods_id'])): p for p in db.query(
        'SELECT shop_id,goods_id,buy_times,next_refresh_timestamp FROM store_purchase WHERE uid=?', (uid,))}
    by_shop = {sid: {int(g['goods_id']): g for g in shop['goods_list']} for sid, shop in result.items()}
    for gid, g in cfg['goods'].items():
        gid, sid = int(gid), int(g['shop_id'])
        if g['taken_down']:
            continue
        shop = result.setdefault(sid, dict(shop_id=sid, refresh_times=0, goods_list=[], is_need_tag=0))
        goods = by_shop.setdefault(sid, {})
        saved = purchases.get((sid, gid), {})
        refresh = int(saved.get('next_refresh_timestamp') or 0)
        expired = 0 < refresh <= now
        goods[gid] = dict(goods_id=gid, buy_times=0 if expired else int(saved.get('buy_times') or 0),
                          next_refresh_timestamp=0 if expired else refresh)
    for sid, goods in by_shop.items():
        result[sid]['goods_list'] = list(goods.values())
    return list(result.values())


def filter_static_shop_config(shops, version=None):
    """The wire GOODS_CFG cannot carry native give/give_id fields."""
    ids = {int(k) for k in catalog(version)['goods']}
    result = []
    for shop in shops:
        row = deepcopy(shop)
        row['goods_list'] = [g for g in row['goods_list'] if int(g['goods_id']) not in ids]
        if row['goods_list']:
            result.append(row)
    return result
