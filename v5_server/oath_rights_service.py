"""Persist the cosmetic assets awarded by WeddingRightCfg, once per right."""
from inventory_service import InventoryService, ItemType


# (right ID, required oath level, item ID, ItemCfg type, inventory kind).
# Non-item rights remain client level effects; they are not inventory rewards.
OATH_RIGHTS = {
    1284: ((1284203, 2, 2938, 12, 'icon_frame'), (1284303, 3, 7201, 22, 'title'), (1284304, 3, 9016, 26, 'bubble')),
    1049: ((1049203, 2, 2939, 12, 'icon_frame'), (1049303, 3, 7202, 22, 'title'), (1049304, 3, 9017, 26, 'bubble')),
    1095: ((1095203, 2, 2949, 12, 'icon_frame'), (1095206, 3, 7203, 22, 'title'), (1095207, 3, 9021, 26, 'bubble')),
}


def grant_eligible_rights(ctx, uid, hero_ids=None, *, silent=False):
    """Called within the owning transaction; never grants from snapshot builders."""
    db = getattr(ctx, 'db', ctx)
    oaths = db.query(
        'SELECT o.hero_id,o.oath_level FROM hero_oath o JOIN hero h ON h.uid=o.uid AND h.id=o.hero_id '
        'WHERE o.uid=? AND o.oath=1 AND h.unlock=1', (uid,))
    allowed = set(hero_ids) if hero_ids is not None else set(OATH_RIGHTS)
    eligible = [right for oath in oaths if int(oath['hero_id']) in allowed
                for right in OATH_RIGHTS.get(int(oath['hero_id']), ()) if int(oath['oath_level'] or 0) >= right[1]]
    pending = [r for r in eligible if not db.is_claimed(uid, 'oath_right', r[0])]
    # Validate the catalog before writes, avoiding an unknown ID falling back to material.
    for _, _, iid, item_type, _ in pending:
        rows = db.query('SELECT type FROM item_catalog WHERE id=?', (iid,))
        if not rows or int(rows[0]['type']) != item_type or InventoryService.classify_item(db, iid) != ItemType.COSMETIC:
            raise ValueError(f'誓约权益装扮配置无效: {iid}')
    rewards, changed = [], []
    for rid, _, iid, _, kind in pending:
        if not db.mark_claimed(uid, 'oath_right', rid):
            continue
        owned = db.query('SELECT obtained FROM player_card WHERE uid=? AND kind=? AND item_id=?', (uid, kind, iid))
        if owned and int(owned[0]['obtained'] or 0) == 1:
            continue
        summary = InventoryService.ensure_cosmetic_owned(ctx, uid, iid, silent=silent, source='oath_right')
        owned = db.query('SELECT obtained FROM player_card WHERE uid=? AND kind=? AND item_id=?', (uid, kind, iid))
        if not owned or int(owned[0]['obtained'] or 0) != 1:
            raise ValueError(f'誓约权益装扮未入账: {iid}')
        added = int(summary.get('material', {}).get(iid, 0))
        if added:
            rewards.append({'id': iid, 'num': added})
        changed.append((kind, iid))
    return {'reward_list': rewards, 'rights_changed': changed, 'has_rights': bool(eligible)}


def build_rights_profile_payload(ctx, uid, changed):
    from codec import decode
    from peripheral_service import PeripheralService
    payload = PeripheralService(db=ctx.db).get_profile_card_payload(uid, ctx.db)
    if not payload:
        raise ValueError('誓约装扮状态 sc_32009 为空')
    profile = decode(payload, 'sc_32009')
    fields = {'icon_frame': 'icon_frame_list', 'title': 'tag_info_list', 'bubble': 'chat_bubble_list'}
    for kind, iid in changed:
        if iid not in {int(r['id']) for r in profile.get(fields[kind], [])}:
            raise ValueError(f'誓约装扮状态遗漏: {iid}')
    return payload
