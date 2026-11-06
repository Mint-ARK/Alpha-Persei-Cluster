"""Bounded native room effects; persisted facts, never replayed by reads.

Only the five items in the adjacent contract are owned by this module. Old
inventories without effect receipts start their progress at zero; global room
counts cannot establish when an item was acquired.
"""
import copy
import functools
import inspect
import json
import random
from pathlib import Path

CONTRACT = json.loads(Path(__file__).with_name('rogueteam_effects_contract.json').read_text(encoding='utf-8'))
MANAGED = {int(i) for i in CONTRACT['items']}
BATTLES = {1, 2, 3, 4, 11}


def owned(state):
    return {int(x.get('id') if isinstance(x, dict) else x)
            for x in json.loads(state.get('other_item_list_json') or '[]')}


def attrs(state):
    return {int(x['attr_id']): x['value'] for x in json.loads(state.get('attr_list_json') or '[]')}


def runtime(meta):
    return meta.setdefault('room_effects_v1', {'items': {}, 'entered': {}, 'completed': [], 'victories': []})


def counter(rt, iid):
    return rt['items'].setdefault(str(iid), {'moment_time': 0, 'calc_value': 0})


def node_key(state, meta, nid):
    return f"{state['floor_num']}:{int(bool(meta.get('hidden')))}:{nid}"


def allow_gold(service, state, attr):
    return not (attr.get(26, 0) or service._has_item(state, 1310022))


def enter_room(service, uid, tid, state, node_type, attr):
    """Called after validated 88004, before the original node branch."""
    from rogueteam_flow import flow, row, save_flow
    meta = flow(row(service, uid, tid)); rt = runtime(meta)
    key = node_key(state, meta, state['select_node_id'])
    if key in rt['entered']:
        return
    rt['entered'][key] = int(node_type)
    for iid, amount, matches in ((1310002, 15, True), (1310003, 60, node_type == 5)):
        if not matches or not service._has_item(state, iid):
            continue
        c = counter(rt, iid); c['moment_time'] += 1
        if allow_gold(service, state, attr):
            attr[6] = attr.get(6, 0) + amount
            c['calc_value'] += amount
    save_flow(service, uid, tid, meta)


def effects_delta(before, after):
    old = {x['effect_id']: x for x in json.loads(before.get('effect_list_json') or '[]')}
    new = {x['effect_id']: x for x in json.loads(after.get('effect_list_json') or '[]')}
    changes = [x for eid, x in new.items() if old.get(eid) != x]
    changes += [dict(x, left_time=0) for eid, x in old.items() if eid not in new]
    return changes


def roll_relic_gift(service, state):
    """Bounded pool301 fallback; never mix negative/story IDs or duplicates.

    Native effects name pool301 but the client assets contain no server pool
    table. Keep the local uniform policy explicit instead of claiming weights.
    """
    candidates = [int(iid) for iid in CONTRACT['acquisition_items']
                  if int(iid) not in owned(state)]
    return random.choice(candidates) if candidates else None


def item_update(extra, item, source, *, old_rare=0):
    """Ownership before animation; source_type=1 is native RELIC."""
    iid = int(item['id']); rare = int(item.get('rare', 1))
    opt = 3 if old_rare else 1
    extra.setdefault('item_list', []).append(dict(opt=opt,
        treasure_list=[copy.deepcopy(item)] if 'rare' in item else [],
        other_list=[] if 'rare' in item else [iid]))
    extra.setdefault('update_list', []).append(dict(operate=opt, id=iid,
        is_new=int(not old_rare), rare=rare, source_rare=old_rare,
        source_type=1, source_item_id=source))


def upgrade_programs(service, treasures, extra, source, count=1, amount=1):
    # GetTreasureAffix -> GetItemRare: tactical (sub_type1) levels stop at3;
    # every other subtype always consumes affix level1 regardless of rare.
    candidates = [t for t in treasures if isinstance(t, dict)
                  and (service.config.get_item(t['id']) or {}).get('type') == 4
                  and service.config.get_item(t['id']).get('sub_type') == 1
                  and int(t.get('rare', 1)) < 3]
    selected = random.sample(candidates, min(count, len(candidates)))
    for item in selected:
        old = int(item.get('rare', 1)); item['rare'] = min(3, old + amount)
        item_update(extra, item, source, old_rare=old)
    return len(selected)


def apply_gift_on_get(service, uid, tid, state, iid, attr, treasures, others, heroes, extra):
    """Interpret native acquisition facts for the photo's newly granted item.

    Only IMMEDIATE and FOREVER attribute registration run here. Future room,
    victory and battle effects are never treated as an acquisition bonus.
    No inferred actual-recovery/max-HP formula is introduced.
    """
    if iid in MANAGED:
        return
    item = CONTRACT['acquisition_items'][str(iid)]
    for eid in item['effect_id_list']:
        effect = CONTRACT['acquisition_effects'][str(eid)]
        moment, action, params = effect['moment'], effect['action'], effect['params']
        if moment not in (1, 2):
            continue
        if action == 1001:
            op, aid, rate, value = params
            current = attr.get(aid, 0)
            value = current * value // 1000 if rate else value
            target = max(0, current + value if op == 1 else value)
            if aid == 6 and target > current and not allow_gold(service, state, attr):
                continue
            attr[aid] = min(attr.get(5, 3), target) if aid == 4 else target
        elif moment == 1 and action == 1002:
            op, value = params
            for hero in heroes:
                hp = int(hero.get('hp_ratio', 10000))
                if hp > 0:
                    hero['hp_ratio'] = min(10000, hp + value * 10) if op == 1 else max(1, hp + value * 10)
        elif moment == 1 and action == 1012:
            amount, count = params
            upgrade_programs(service, treasures, extra, iid, count=count, amount=amount)
        elif moment == 1 and action == 1016:
            pool, low, high, count = params
            held = {int(t['id']) for t in treasures}
            candidates = [int(key) for key, cfg in service.config.items.items()
                          if cfg.get('type') == 4 and int(key) not in held
                          and low <= service.config.get_item_default_rare(int(key)) <= high
                          and (pool == 400 or (401 <= pool <= 406 and
                               str(key).startswith((f'1410{pool - 400}', f'1420{pool - 400}'))))]
            for gift in random.sample(candidates, min(count, len(candidates))):
                received = dict(id=gift, rare=service.config.get_item_default_rare(gift))
                treasures.append(received); item_update(extra, received, iid)
                service.unlock_collection_item(uid, tid, gift, 4)
                service._bump_survey_program_tasks(uid, gift, tid)


def project(state, rt):
    result = [x for x in json.loads(state.get('effect_list_json') or '[]') if x.get('source_id') not in MANAGED]
    expired = set(json.loads(state.get('expired_items_json') or '[]'))
    for iid in sorted(owned(state) & MANAGED - expired):
        c = counter(rt, iid)
        for eid in CONTRACT['items'][str(iid)]['effect_id_list']:
            result.append(dict(source_type=0, source_id=iid, effect_id=eid,
                               left_time=9999, trigger_cd=0,
                               moment_time=c['moment_time'], calc_value=c['calc_value']))
    return result


def reconcile(service, uid, tid, before, name, call, extra):
    from rogueteam_flow import flow, row, save_flow
    after = row(service, uid, tid); meta = flow(after); rt = runtime(meta)
    prior = owned(before); current = owned(after); attr = attrs(after)
    old_nodes = {n['node_id']: n for n in json.loads(before.get('map_info_json') or '[]')}
    nodes = json.loads(after.get('map_info_json') or '[]')
    treasures = json.loads(after.get('treasure_list_json') or '[]')
    others = json.loads(after.get('other_item_list_json') or '[]')
    expired = set(json.loads(after.get('expired_items_json') or '[]'))
    for iid in current & MANAGED:
        counter(rt, iid)
    # Every completed physical node counts once. Reward claims, option-chain
    # hops, safe rooms and plot nodes cannot masquerade as another decision.
    acquired_gifts = []
    for node in nodes:
        nid = node['node_id']; key = node_key(before, meta, nid)
        if node.get('state') != 3 or old_nodes.get(nid, {}).get('state') == 3 or key in rt['completed']:
            continue
        rt['completed'].append(key)
        kind = rt['entered'].get(key, old_nodes.get(nid, node).get('node_type'))
        for iid, matches in ((1310016, kind == 7), (1310022, True)):
            if not matches or iid not in prior or iid not in current or iid in expired:
                continue
            c = counter(rt, iid); c['moment_time'] += 1
            if c['moment_time'] % 3:
                continue
            if iid == 1310016:
                gift_state = dict(after, other_item_list_json=json.dumps(others))
                gift = roll_relic_gift(service, gift_state)
                if gift:
                    if gift not in current:
                        others.append(dict(id=gift, value=1)); current.add(gift)
                        acquired_gifts.append(gift)
                        item_update(extra, dict(id=gift, value=1), iid)
                        service.unlock_collection_item(uid, tid, gift, 3)
                        c['calc_value'] += 1
            else:
                c['calc_value'] += upgrade_programs(service, treasures, extra, iid)

    # A victory is a distinct receipt from completing its reward window.
    for node in nodes:
        nid = node['node_id']; old = old_nodes.get(nid, {})
        key = node_key(before, meta, nid)
        if (old.get('state') == 0 and node.get('state') in (1, 3)
                and node.get('node_type') in BATTLES and key not in rt['victories']):
            rt['victories'].append(key)
            if 1310015 in prior and 1310015 in current and 1310015 not in expired:
                c = counter(rt, 1310015); c['moment_time'] += 1
                attr[5] = 1; attr[4] = min(1, attr.get(4, 0) + 1)

    # An item bought in this very shop starts after payment. Only a real,
    # positive price paid with an already-owned tree expires it.
    if name == 'shop_refresh' and attrs(before).get(6, 0) > attr.get(6, 0) and 1310002 in prior:
        expired.add(1310002)

    # Acquisition of managed items never executes future room effects early.
    for iid in (current - prior) & MANAGED:
        rt['items'][str(iid)] = {'moment_time': 0, 'calc_value': 0}
        if iid == 1310015:
            attr[5] = 1; attr[4] = min(1, attr.get(4, 0))
        elif iid == 1310022:
            attr[26] = 1
    if 1310022 in prior and 1310022 not in current:
        attr[26] = 0
    if 1310022 in current and 1310022 not in expired:
        attr[26] = 1
    # Native effect moments prevent periodic gifts from firing future effects.
    # Managed gifts are initialized by the path above.
    heroes = json.loads(after.get('hero_list_json') or '[]')
    for iid in acquired_gifts:
        apply_gift_on_get(service, uid, tid, after, iid, attr, treasures, others, heroes, extra)

    # The native effect-item consumer is a set by ID, so duplicate reward
    # entries must not stack the same permanent effect or reset its clock.
    seen = set(); unique = []
    for item in others:
        iid = int(item.get('id') if isinstance(item, dict) else item)
        if iid in MANAGED and iid in seen:
            continue
        seen.add(iid); unique.append(item)
    after.update(other_item_list_json=json.dumps(unique), treasure_list_json=json.dumps(treasures),
                 attr_list_json=json.dumps([dict(attr_id=k, value=v) for k, v in attr.items()]),
                 expired_items_json=json.dumps(sorted(expired)), hero_list_json=json.dumps(heroes))
    after['effect_list_json'] = json.dumps(project(after, rt))
    fields = ('other_item_list_json', 'treasure_list_json', 'attr_list_json', 'expired_items_json', 'hero_list_json', 'effect_list_json')
    service._db().upsert('rogueteam_session', uid, dict(template_id=tid, **{k:after[k] for k in fields}), keys=('uid', 'template_id'))
    save_flow(service, uid, tid, meta)
    if before.get('attr_list_json') != after['attr_list_json'] or 'attr_list' in extra:
        extra['attr_list'] = json.loads(after['attr_list_json'])
    if before.get('hero_list_json') != after['hero_list_json']:
        extra['hero_list'] = heroes
    changes = effects_delta(before, after)
    if changes:
        extra['effect_list'] = changes
    if 'session' in extra:
        extra['session'] = service.get_session_data(uid, tid)


def install(cls):
    from account_db import savepoint_transaction
    from rogueteam_flow import row
    for name in ('select_node', 'choose_event_option', 'commit_event_selection', 'buy_shop_item',
                 'leave_shop', 'shop_refresh', 'settle_battle_node', 'discard_items'):
        original = getattr(cls, name); signature = inspect.signature(original)
        def make(name, original, signature):
            @functools.wraps(original)
            def wrapped(self, uid, *args, **kwargs):
                call = signature.bind(self, uid, *args, **kwargs); call.apply_defaults()
                tid = call.arguments.get('template_id', 100001)
                with savepoint_transaction(self._db()):
                    before = row(self, uid, tid)
                    result = original(*call.args, **call.kwargs)
                    if result[0] == 0:
                        extra = result[2] if name == 'settle_battle_node' else result[1]
                        if isinstance(extra, dict):
                            reconcile(self, uid, tid, before, name, call, extra)
                    return result
            return wrapped
        setattr(cls, name, make(name, original, signature))
