"""Finite skin/scene draws: catalogue, per-pool progress and login snapshots."""
import json
import random
import time
from functools import lru_cache
from pathlib import Path

from inventory_service import InventoryService

BASE = Path(__file__).resolve().parent
DRAW_STATE_COMMANDS = frozenset((68151, 68161, 68185, 68155))


class DrawError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@lru_cache(maxsize=1)
def catalogue():
    return json.loads((BASE / 'skin_draw_pool_cfg.json').read_text('utf-8'))


@lru_cache(maxsize=1)
def metadata():
    return json.loads((BASE / 'skin_draw_activity_cfg.json').read_text('utf-8'))


def version_config(version=None):
    import res_version_manager as rvm
    version = str(version or rvm.get_current_version())
    key = '311' if rvm.get_version_config(version)['theme_id'] >= 44 else '229'
    return metadata()['versions'][key]


def active_pools(version=None):
    return {int(pid): catalogue()[str(pid)]
            for main in version_config(version)['mains'].values() for pid in main['pool_list']}


def ticket_goods(version=None):
    ids = {int(gid) for cfg in active_pools(version).values() for k in ('payment', 'payment_free') for gid in cfg[k]}
    return {gid: metadata()['ticket_goods'][str(gid)] for gid in ids}


def merge_ticket_shop_config(shops, version=None):
    """Keep native ticket goods intact: dynamic GOODS_CFG has no give_id/give.

    UpdateShopCfg replaces, rather than merges, each client entry. Ticket IDs
    already exist in native ShopCfg; only their purchase states need pushing.
    Also strip recorded overrides so an old template cannot reintroduce them.
    """
    native_ids = {int(gid) for gid in metadata()['ticket_goods']}
    result = []
    for shop in shops:
        goods = [dict(g) for g in shop['goods_list'] if int(g['goods_id']) not in native_ids]
        if goods:
            result.append(dict(shop, goods_list=goods))
    return result


def ticket_shop_states(db, uid, version=None):
    rows = db.query('SELECT goods_id,buy_times FROM store_purchase WHERE uid=?', (uid,)) if db is not None else []
    purchases = {int(row['goods_id']): int(row['buy_times'] or 0) for row in rows}
    shops = {}
    for gid, row in ticket_goods(version).items():
        sid = row['shop_id']
        shop = shops.setdefault(sid, dict(shop_id=sid, refresh_times=0, is_need_tag=0, goods_list=[]))
        shop['goods_list'].append(dict(goods_id=gid, buy_times=purchases.get(gid, 0), next_refresh_timestamp=0))
    return shops


def merge_ticket_shop_states(shops, db, uid, version=None):
    result = {int(shop['shop_id']): dict(shop) for shop in shops}
    for sid, ticket_shop in ticket_shop_states(db, uid, version).items():
        shop = result.setdefault(sid, ticket_shop)
        goods = {int(g['goods_id']): g for g in shop['goods_list']}
        goods.update({int(g['goods_id']): g for g in ticket_shop['goods_list']})
        shop['goods_list'] = list(goods.values())
    return list(result.values())


def get_pool_state(db, uid, pool_id):
    """Missing rows mean untouched rewards; reads do not initialize player data."""
    cfg = catalogue()[str(int(pool_id))]
    rows = {int(row['drop_id']): row for row in db.query(
        'SELECT drop_id, remain_num, drawn_num FROM user_skin_draw_pool WHERE uid=? AND pool_id=?',
        (uid, int(pool_id)))}
    state = {}
    for key, drop in cfg['drops'].items():
        did = int(key)
        row = rows.get(did)
        remain = int(row['remain_num']) if row else int(drop['total'])
        drawn = int(row['drawn_num']) if row else 0
        if remain < 0 or drawn < 0 or remain + drawn != int(drop['total']):
            raise DrawError(2, f'卡池 {pool_id} 奖励格 {did} 进度不一致')
        state[did] = dict(drop_id=did, remain_num=remain, drawn_num=drawn)
    return state


def _progress(db, uid, pid, state):
    rows = db.query('SELECT misses,last_drop,already_drop FROM user_skin_draw_progress WHERE uid=? AND pool_id=?', (uid, pid))
    if not rows:
        # Old builds persisted counts but no draw order. Keep the partial ten-pull
        # remainder when first adopting that history; subsequent misses are exact.
        return dict(misses=sum(s['drawn_num'] for s in state.values()) % 10,
                    last_drop=0, already_drop=[0] * 10)
    row = rows[0]
    board = json.loads(row['already_drop'])
    if len(board) != 10 or any(not isinstance(i, int) or (i and i not in state) for i in board):
        raise DrawError(2, '翻牌进度格式错误')
    return dict(misses=int(row['misses']), last_drop=int(row['last_drop']), already_drop=board)


def _save_progress(db, uid, pid, progress):
    db.execute('''INSERT INTO user_skin_draw_progress(uid,pool_id,misses,last_drop,already_drop,update_ts)
        VALUES(?,?,?,?,?,?) ON CONFLICT(uid,pool_id) DO UPDATE SET misses=excluded.misses,
        last_drop=excluded.last_drop,already_drop=excluded.already_drop,update_ts=excluded.update_ts''',
        (uid, pid, progress['misses'], progress['last_drop'], json.dumps(progress['already_drop']), int(time.time())))


def _weighted(candidates, cfg, rng):
    weights = [cfg['drops'][str(s['drop_id'])]['weight'] * s['remain_num'] for s in candidates]
    if not candidates or any(w <= 0 for w in weights):
        raise DrawError(2, '卡池没有有效候选奖励')
    # Integer ranges exclude the upper endpoint and avoid floating point edges.
    value = rng.randrange(sum(weights))
    for candidate, weight in zip(candidates, weights):
        if value < weight:
            return candidate['drop_id']
        value -= weight
    raise AssertionError('unreachable weighted selection')


def _available(cfg, state):
    unlocked = {1}
    for stage, trigger in cfg['stage_unlock'].items():
        if state[int(trigger)]['drawn_num'] > 0:
            unlocked.add(int(stage))
    return [s for s in state.values() if s['remain_num'] > 0
            and cfg['drops'][str(s['drop_id'])]['pool_stage'] in unlocked]


def _new_oath_round(cfg, state, progress, rng):
    cores = [s for s in _available(cfg, state) if cfg['drops'][str(s['drop_id'])]['is_core']]
    progress.update(last_drop=_weighted(cores, cfg, rng) if cores else 0,
                    already_drop=[0] * 10, misses=0)


def pool_snapshot(db, uid, cfg, *, initialize=False, rng=random):
    pid = cfg['pool_id']
    state = get_pool_state(db, uid, pid)
    payload = dict(activity_id=cfg['activity_id'][0],
                   info=[dict(drop_id=did, num=s['remain_num']) for did, s in state.items()])
    if cfg['model'] == 'oath':
        progress = _progress(db, uid, pid, state)
        if initialize and not db.query('SELECT 1 FROM user_skin_draw_progress WHERE uid=? AND pool_id=?', (uid, pid)):
            _new_oath_round(cfg, state, progress, rng)
            _save_progress(db, uid, pid, progress)
        payload['draw_info2'] = dict(last_drop=progress['last_drop'], already_drop=progress['already_drop'])
    return payload


def login_packets(db, uid, version=None):
    """One state packet per real activity; choose new oath targets only once."""
    packets = []
    with db.transaction():
        for cfg in active_pools(version).values():
            cmd = 68185 if cfg['model'] == 'oath' else 68151
            packets.append((cmd, pool_snapshot(db, uid, cfg, initialize=True)))
        story_activities = {int(s['activity_id']) for m in version_config(version)['mains'].values()
                            for s in m['stories'] if s['activity_id']}
        for aid in sorted(story_activities):
            packets.append((68161, story_snapshot(db, uid, aid, version)))
        pending = db.query('SELECT skin_id FROM user_skin_draw_pop WHERE uid=? AND claimed=0 ORDER BY skin_id', (uid,))
        if pending:
            packets.append((68155, dict(skin_id=[r['skin_id'] for r in pending])))
    return packets


def completed_story_ids(db, uid, version=None):
    """Native 12002/12001 played records and the older activity-specific record."""
    allowed = {int(s['story_id']) for main in version_config(version)['mains'].values() for s in main['stories']}
    finished = {int(r['story_id']) for r in db.query('SELECT story_id FROM user_skin_story WHERE uid=?', (uid,))}
    finished.update(int(r['story_id']) for r in db.query(
        'SELECT story_id FROM story_unlock WHERE uid=? AND unlock_flag=1', (uid,)))
    return finished & allowed


def story_snapshot(db, uid, activity_id, version=None):
    # The client keeps one shared finishedStory_ array and filters it by main
    # activity when reading. Every 68161 replaces that array, rather than merging.
    return dict(activity_id=int(activity_id), story_stage=0, finished_story=sorted(completed_story_ids(db, uid, version)))


def merge_activities(activity_list, version=None):
    cfg = version_config(version)
    tree = cfg['activities']
    by_id = {int(row['activity_id']): dict(row) for row in activity_list}
    import res_version_manager as rvm
    for key, row in tree.items():
        aid = int(key)
        by_id[aid] = dict(activity_id=aid, start_time=1577836800,
                         stop_time=rvm.FAR_FUTURE_TIMESTAMP, state=1, **row)
    return list(by_id.values())


def _request(data, model, version):
    pid = int(data.get('pool_id') or 0)
    aid = int(data.get('activity_id') or 0)
    cfg = active_pools(version).get(pid)
    if not cfg or cfg['model'] != model or aid not in cfg['activity_id']:
        raise DrawError(2, '活动与卡池不匹配或当前版本未开放')
    return cfg, aid, pid


def _consume_ticket(ctx, uid, cfg):
    iid, num = cfg['cost_once']
    if not InventoryService.cost_item(ctx, uid, iid, num):
        raise DrawError(3, '抽取凭证不足')


def _grant(ctx, uid, cfg, did):
    from inventory_service import ItemType
    grants = []
    for iid, count in cfg['drops'][str(did)]['reward']:
        gift = metadata()['auto_gifts'].get(str(iid))
        if gift:
            owned_skin = any(InventoryService.classify_item(ctx.db, i) == ItemType.SKIN
                             and ctx.db.query('SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?', (uid, i))
                             for i, _ in gift)
            first_only = metadata()['first_skin_bonuses'].get(str(iid), []) if owned_skin else []
            grants.extend((int(i), int(n) * count) for i, n in gift if i not in first_only)
        else:
            grants.append((iid, count))
    duplicates = []
    for iid, count in grants:
        if InventoryService.classify_item(ctx.db, iid) == ItemType.SKIN and ctx.db.query(
                'SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?', (uid, iid)):
            duplicates.append(iid)
    summary = InventoryService.grant_items(ctx, uid, grants, source='skin_draw')
    # Some rerun skins have a separate story-item popup. Confirmation may claim
    # that item once, only after a real duplicate draw generated this record.
    if duplicates:
        main = next(m for m in metadata()['versions']['311']['mains'].values() if cfg['pool_id'] in m['pool_list'])
        story_item = int(main['story_item'])
        if story_item > 0:
            for skin in duplicates:
                ctx.db.execute('''INSERT OR IGNORE INTO user_skin_draw_pop(uid,skin_id,item_id,claimed)
                    VALUES(?,?,?,0)''', (uid, skin, story_item))
    from inventory_service import COSMETIC_KIND_MAP
    summary['profile_assets'] = []
    for iid, _ in grants:
        item_type = InventoryService.classify_item(ctx.db, iid)
        if item_type == ItemType.SCENE:
            summary['profile_assets'].append(('scene', iid))
        elif item_type == ItemType.COSMETIC:
            row = ctx.db.query('SELECT type FROM item_catalog WHERE id=?', (iid,))
            summary['profile_assets'].append((COSMETIC_KIND_MAP[int(row[0]['type'])], iid))
    return summary


def profile_payload(ctx, uid, summaries):
    from codec import decode
    from peripheral_service import PeripheralService
    assets = {tuple(asset) for summary in summaries for asset in summary.get('profile_assets', [])}
    if not assets:
        return None
    payload = PeripheralService.get_instance(db=ctx.db).get_profile_card_payload(uid, ctx.db)
    if not payload:
        raise ValueError('抽取装扮持有状态 sc_32009 为空')
    profile = decode(payload, 'sc_32009')
    fields = dict(scene='poster_background_list', icon_frame='icon_frame_list',
                  portrait='icon_list', bubble='chat_bubble_list', title='tag_info_list',
                  card_bg='information_background_list')
    for kind, iid in assets:
        if iid not in {int(row['id']) for row in profile.get(fields[kind], [])}:
            raise ValueError(f'抽取装扮持有状态缺少 {kind}/{iid}')
    return payload


def draw(ctx, uid, data, model, version=None, rng=random):
    cfg, aid, pid = _request(data, model, version)
    count = int(data.get('drop_type') or 0) if model == 't0' else 1
    index = int(data.get('card_index') or 0) if model == 'oath' else 0
    if (model == 't0' and count not in (1, 10)) or not 0 <= index <= 10:
        raise DrawError(2, '抽取次数或翻牌位置无效')
    state = get_pool_state(ctx.db, uid, pid)
    if not any(s['remain_num'] for s in state.values()):
        raise DrawError(2, '卡池已抽空')
    progress = _progress(ctx.db, uid, pid, state)
    if model == 't0' and sum(s['remain_num'] for s in state.values()) < count:
        raise DrawError(2, '剩余奖励不足十连，请使用单抽')
    if model == 'oath':
        board = progress['already_drop']
        initialized = ctx.db.query('SELECT 1 FROM user_skin_draw_progress WHERE uid=? AND pool_id=?', (uid, pid))
        if not initialized or (progress['last_drop'] and progress['last_drop'] in board) or all(board):
            _new_oath_round(cfg, state, progress, rng)
        if index and progress['already_drop'][index-1]:
            raise DrawError(2, '该位置已翻开')
        count = 1 if index else 10 - sum(bool(i) for i in progress['already_drop'])

    results, summaries = [], []
    for _ in range(count):
        available = _available(cfg, state)
        if not available:
            break
        if model == 't0':
            cores = [s for s in available if cfg['drops'][str(s['drop_id'])]['is_core']]
            normal = [s for s in available if not cfg['drops'][str(s['drop_id'])]['is_core']]
            use_core = cores and (not normal or progress['misses'] >= cfg['guarantee_interval']-1
                                   or rng.randrange(10000) < cfg['core_chance_basis_points'])
            did = _weighted(cores if use_core else normal, cfg, rng)
            card = 0
            progress['misses'] = 0 if use_core else progress['misses'] + 1
        else:
            target = progress['last_drop']
            board = progress['already_drop']
            normal = [s for s in available if not cfg['drops'][str(s['drop_id'])]['is_core']]
            win = target and (not normal or sum(bool(i) for i in board) >= 9
                              or rng.randrange(10000) < cfg['core_chance_basis_points'])
            did = target if win else _weighted(normal, cfg, rng)
            card = index or next(i+1 for i, value in enumerate(board) if not value)
            board[card-1] = did
        _consume_ticket(ctx, uid, cfg)
        row = state[did]
        row['remain_num'] -= 1
        row['drawn_num'] += 1
        ctx.db.update_skin_draw_pool_item(uid, pid, did, row['remain_num'], row['drawn_num'])
        summaries.append(_grant(ctx, uid, cfg, did))
        results.append(dict(card_index=card, drop_id=did))
        if model == 'oath' and did == progress['last_drop']:
            break
        if model == 'oath' and not index:
            iid, cost = cfg['cost_once']
            _, balance = InventoryService.get_item_balance(ctx, uid, iid)
            if balance < cost:
                break
    if model == 'oath' and ((progress['last_drop'] and progress['last_drop'] in progress['already_drop'])
                            or all(progress['already_drop'])):
        # The client captures the old target before sending the request. Its ACK
        # uses that target for the win animation; the following state packet is
        # already the next round, as required by the reset callback.
        _new_oath_round(cfg, state, progress, rng)
    _save_progress(ctx.db, uid, pid, progress)
    return dict(activity_id=aid, pool_id=pid, drop_list=results, summaries=summaries,
                snapshot=pool_snapshot(ctx.db, uid, cfg), model=model)


def finish_pop(ctx, uid, skin_id):
    skin_id = int(skin_id)
    rows = ctx.db.query('SELECT item_id,claimed FROM user_skin_draw_pop WHERE uid=? AND skin_id=?', (uid, skin_id))
    if not rows:
        raise DrawError(2, '没有待领取的换装剧情道具')
    if not rows[0]['claimed']:
        InventoryService.grant_item(ctx, uid, rows[0]['item_id'], 1, source='skin_draw_pop')
        ctx.db.execute('UPDATE user_skin_draw_pop SET claimed=1 WHERE uid=? AND skin_id=?', (uid, skin_id))
    return dict(skin_id=skin_id)
