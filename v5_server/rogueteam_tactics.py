"""First six-program tactic per run; native state and durable acquisitions."""
import functools
import inspect
import json
from pathlib import Path

from account_db import savepoint_transaction

with Path(__file__).with_name('rogueteam_tactics_contract.json').open(encoding='utf-8') as handle:
    CONTRACT = json.load(handle)

KIND = 'rogueteam_tactic_acquisitions_v1'
TEMPLATE = 100001


def _json(value, fallback):
    try:
        result = json.loads(value or json.dumps(fallback))
    except (ValueError, TypeError):
        return fallback.copy()
    return result if isinstance(result, type(fallback)) else fallback.copy()


def program_ids(state):
    found = set()
    for item in _json((state or {}).get('treasure_list_json'), []):
        iid = item.get('id') if isinstance(item, dict) else item
        if type(iid) is int and str(iid) in CONTRACT['programs']:
            found.add(iid)
    return found


def state_packet(state):
    meta = _json((state or {}).get('flow_json'), {})
    tactic = meta.get('tactic_v1') or {}
    camp = tactic.get('camp') if isinstance(tactic, dict) else None
    cfg = CONTRACT['tactics'].get(str(camp))
    if not state or not state.get('in_game') or not cfg or tactic.get('item_id') != cfg['id']:
        return dict(id=0, value=0)
    energy = tactic.get('energy', 0)
    if type(energy) is not int or not 0 <= energy <= 0xffffffff:
        raise ValueError('推演战术指令能量状态无效')
    return dict(id=int(camp), value=energy)


def _counts(db, uid):
    row = db.get('ops_common', uid, 'AND kind=? AND item_id=?', (KIND, str(TEMPLATE))) or {}
    result = _json(row.get('value_json'), {})
    return {key: value for key, value in result.items()
            if key in CONTRACT['tactics'] and type(value) is int and value >= 0}


def achievement_targets(db, uid):
    counts = _counts(db, uid)
    for cfg in CONTRACT['achievements'].values():
        template, camp = cfg['parameter']
        count = counts.get(str(camp), 0)
        if template == TEMPLATE and count:
            yield dict(cfg, progress=min(count, cfg['need']))


def activate(service, uid, tid, before, extra):
    from rogueteam_flow import row, flow, save_flow
    if tid != TEMPLATE or not before or not before.get('in_game'):
        return
    after = row(service, uid, tid)
    if not after or not after.get('in_game'):
        return
    meta = flow(after)
    if not meta.get('run_id') or meta['run_id'] != flow(before).get('run_id'):
        return
    if meta.get('tactic_v1'):
        return  # User policy: first eligible camp stays active until next run.
    ids = program_ids(after)
    if not (ids - program_ids(before)):
        return  # Reads, rank upgrades and old bags cannot create acquisition events.
    camps = {}
    for iid in ids:
        cfg = CONTRACT['programs'][str(iid)]
        if cfg['template'] == 1:
            for camp in cfg['camps']:
                camps[camp] = camps.get(camp, 0) + 1
    for key, cfg in CONTRACT['tactics'].items():
        camp = int(key)
        if cfg['template'] != 1 or camps.get(camp, 0) < CONTRACT['threshold']:
            continue
        # The native client starts energy at zero. No invented charge formula.
        meta['tactic_v1'] = dict(camp=camp, item_id=cfg['id'], energy=0)
        others = _json(after.get('other_item_list_json'), [])
        existing = {item.get('id') if isinstance(item, dict) else item for item in others
                    if isinstance(item, (int, dict))}
        if cfg['id'] not in existing:
            others.append(dict(id=cfg['id'], value=1))
            service._db().execute('UPDATE rogueteam_session SET other_item_list_json=? '
                'WHERE uid=? AND template_id=?', (json.dumps(others), uid, tid))
            extra.setdefault('update_list', []).append(dict(operate=1, id=cfg['id'], is_new=1,
                rare=1, source_rare=0, source_type=3, source_item_id=0))
        save_flow(service, uid, tid, meta)
        counts = _counts(service._db(), uid)
        counts[key] = min(0xffffffff, counts.get(key, 0) + 1)
        service._db().upsert('ops_common', uid, dict(kind=KIND, item_id=str(tid),
            value_json=json.dumps(counts)), keys=('uid', 'kind', 'item_id'))
        # Keep the suit skill in its native local bag. The old illustrated
        # helper aliases item types and would misfile it as a story event.
        extra['_tactic_state'] = dict(id=camp, value=0)
        if 'session' in extra:
            extra['session'] = service.get_session_data(uid, tid)
        break


def install(cls):
    """Outer layer after native room effects, so their gifts also count."""
    from rogueteam_flow import row
    for name in ('select_node', 'choose_event_option', 'commit_event_selection',
                 'buy_shop_item', 'leave_shop', 'shop_refresh', 'settle_battle_node', 'discard_items'):
        original = getattr(cls, name)
        signature = inspect.signature(original)

        def make(name, original, signature):
            @functools.wraps(original)
            def wrapped(self, uid, *args, **kwargs):
                bound = signature.bind(self, uid, *args, **kwargs)
                bound.apply_defaults()
                tid = bound.arguments.get('template_id', TEMPLATE)
                with savepoint_transaction(self._db()):
                    before = row(self, uid, tid)
                    result = original(*bound.args, **bound.kwargs)
                    if result[0] == 0:
                        extra = result[2] if name == 'settle_battle_node' else result[1]
                        if isinstance(extra, dict):
                            activate(self, uid, tid, before, extra)
                    return result
            return wrapped
        setattr(cls, name, make(name, original, signature))
