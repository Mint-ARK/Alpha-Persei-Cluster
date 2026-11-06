"""Node/pending ownership and required responses for the existing Rogue rules.

No client-side nonce is invented: pending revisions identify persisted server
facts, not otherwise indistinguishable consecutive client confirmations.
"""
import functools
import copy
import inspect
import json
from pathlib import Path

BATTLE_TYPES = (1, 2, 3, 4, 11)
REPLACE_TYPES = (1, 2, 5, 6, 7, 9)  # native RogueTeamRoomTypeCfg.can_replace


class InvalidTransition(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise InvalidTransition(message)


def required_frame(ctx, sc, data):
    from middleware import DownFrame
    raw = ctx.codec_encode('sc_' + str(sc), data)
    # b'' is valid for messages containing only empty repeated fields. None is
    # always failure; populated/required fields must not silently encode empty.
    require(isinstance(raw, (bytes, bytearray, memoryview)), '推演响应编码失败: ' + str(sc))
    require(bool(raw) or not any(v not in ([], {}, None) for v in data.values()), '推演必要响应为空: ' + str(sc))
    return DownFrame(sc, bytes(raw))


def has_pending(value):
    et = value.get('event_type')
    return bool(value.get('event_id') or value.get('_queue') or
                (et and (et not in (1, 2, 5, 11) or value.get('param_list'))))


def context(service, uid, tid=100001, *, active=True):
    from rogueteam_flow import row, flow, ensure_flow
    state = ensure_flow(service, uid, tid, row(service, uid, tid))
    require(state and state.get('in_game'), '推演不在进行中')
    meta = flow(state)
    require(meta.get('phase') == 'active' if active else meta.get('phase') in ('active', 'ending_plot', 'ready_settle'), '推演阶段不允许此操作')
    nodes = json.loads(state.get('map_info_json') or '[]')
    node = next((n for n in nodes if n['node_id'] == state.get('select_node_id')), None)
    return dict(state=state, meta=meta, nodes=nodes, node=node,
                pending=json.loads(state.get('other_info_json') or '{}'),
                shop=json.loads(state.get('shop_info_json') or '{}'))


def owner_key(c, node_id):
    s, m = c['state'], c['meta']
    return dict(run_id=m['run_id'], floor=s['floor_num'], hidden=bool(m.get('hidden')), node_id=node_id)


def pending_owner(c, kind='pending'):
    value = c[kind]
    require(has_pending(value), '推演没有对应待办')
    owner = c['meta'].get(kind + '_owner')
    selected = int(c['state'].get('select_node_id') or 0)
    if owner:
        require({k: owner.get(k) for k in ('run_id', 'floor', 'hidden')} ==
                {k: owner_key(c, 0)[k] for k in ('run_id', 'floor', 'hidden')}, '推演待办已跨局或跨图')
        require(not selected or owner.get('node_id') == selected, '推演待办不属于当前节点')
        nid = owner.get('node_id', 0)
        require(nid == 0 or any(n['node_id'] == nid for n in c['nodes']), '推演待办节点不存在')
        return owner
    # Legacy rows may be adopted only from a unique selected node, or the
    # explicit initial-reward window before the first room. Never infer from
    # a spare shop or a random CLEAN node elsewhere in the graph.
    if not c['node'] and kind == 'pending' and value.get('event_type') in (1, 2, 11):
        clean = [n for n in c['nodes'] if n.get('state') == 1]
        require(len(clean) == 1 and clean[0].get('node_type') in BATTLE_TYPES and not has_pending(c['shop']), '推演旧奖励归属不唯一')
        return owner_key(c, clean[0]['node_id'])
    require(c['node'] is not None or (kind == 'pending' and value.get('event_type') == 5
            and not c['state'].get('room_passed_count')), '推演旧待办缺少明确归属')
    return owner_key(c, selected)


def bind_pending(service, uid, tid, before, *, selected=None):
    from rogueteam_flow import row, flow, save_flow
    state = row(service, uid, tid)
    meta = flow(state)
    after = dict(state=state, meta=meta)
    for kind, column in (('pending', 'other_info_json'), ('shop', 'shop_info_json')):
        value = json.loads(state.get(column) or '{}')
        if has_pending(value):
            prior = before['meta'].get(kind + '_owner', {})
            if not prior and selected is None and has_pending(before[kind]):
                prior = pending_owner(before, kind)
            nid = selected if selected is not None else (state.get('select_node_id') or before['state'].get('select_node_id') or prior.get('node_id', 0))
            meta[kind + '_owner'] = owner_key(after, nid)
            if value != before[kind]:
                meta[kind + '_revision'] = int(meta.get(kind + '_revision') or 0) + 1
        else:
            meta.pop(kind + '_owner', None)
    if meta != flow(state):
        save_flow(service, uid, tid, meta)


def finish_map_node(nodes, node_id):
    """The only map-node finalizer. Missing pointers never choose another node."""
    target = next((n for n in nodes if n.get('node_id') == node_id), None)
    require(target is not None, '推演收尾节点不存在')
    if target.get('state') == 3:
        return False
    require(target.get('state') == 1, '推演节点尚未完成')
    target['state'] = 3
    for n in nodes:
        if n['node_id'] in target.get('next_id_list', []) and n.get('state') == 2:
            n['state'] = 0
    return True


@functools.lru_cache(maxsize=1)
def hero_contract():
    data = json.loads(Path(__file__).with_name('trial_system_data.json').read_text('utf-8'))
    return data['hero_cfgs'], data['std_cfgs']


def validate_heroes(service, uid, values, *, existing=(), limit=3, allow_empty=False):
    require(isinstance(values, list) and (allow_empty or values) and len(values) <= limit, '推演招募数量无效')
    hero_cfgs, trial_cfgs = hero_contract()
    allowed_trials = set(service.config.cfg['main']['trial_heroes'])
    seen = set(existing)
    clean = []
    for h in values:
        require(isinstance(h, dict), '推演英雄参数无效')
        hid, temp = h.get('hero_id'), h.get('temp_id', 0)
        require(type(hid) is int and str(hid) in hero_cfgs and hid not in seen and type(temp) is int, '推演英雄不存在或重复')
        if temp:
            require(temp in allowed_trials and trial_cfgs.get(str(temp), {}).get('hero_id') == hid, '推演试用英雄不匹配')
        else:
            require(service._db().get('hero', uid, 'AND id=?', (hid,)) is not None, '推演英雄尚未拥有')
        seen.add(hid)
        clean.append(dict(hero_id=hid, temp_id=temp, hp_ratio=10000))
    return clean


def require_action(service, uid, tid, name, args):
    c = context(service, uid, tid)
    p, node = c['pending'], c['node']
    if c['meta'].get('map_switch'):
        require(name in ('commit_event_selection', 'reset_operate_data', 'discard_items'), '请先完成推演地图切换')
    if name == 'select_node':
        require(not has_pending(p) and not has_pending(c['shop']), '请先处理当前推演待办')
    elif name == 'choose_event_option':
        pending_owner(c)
        require(node and node.get('node_type') in (6, 7, 8) and node.get('state') == 1 and p.get('event_id'), '当前不是事件交互')
        if args['opt_id'] == 0:
            require(not p.get('opt_list') and not p.get('_queue'), '事件尚有待选内容')
    elif name in ('buy_shop_item', 'leave_shop', 'shop_refresh'):
        pending_owner(c, 'shop')
        require(node and node.get('node_type') == 5 and node.get('state') == 1
                and c['shop'].get('event_type') == 7 and not has_pending(p), '当前不是游商交互')
        if name == 'shop_refresh':
            attrs = {a['attr_id']: a['value'] for a in json.loads(c['state']['attr_list_json'])}
            require(attrs.get(30, 0) > 0, '推演商店刷新尚未解锁')
    elif name == 'hero_recruit':
        pending_owner(c)
        require(p.get('event_type') == 3, '当前不是招募交互')
    elif name == 'commit_event_selection':
        pending_owner(c)
        require(p.get('event_type') in (1, 2, 5, 11), '当前待办不是物品选择')
    elif name == 'reset_operate_data':
        pending_owner(c)
        require(args['reset_type'] == 1 and p.get('event_type') == 1 and p.get('param_list'), '当前待办不可刷新')
    elif name == 'discard_items':
        pending_owner(c)
        require(p.get('event_type') == 9, '当前没有丢弃待办')
    return c


def install(cls):
    from account_db import savepoint_transaction
    from rogueteam_flow import flow, row, save_flow
    names = ('select_node', 'choose_event_option', 'commit_event_selection', 'buy_shop_item',
             'leave_shop', 'shop_refresh', 'hero_recruit', 'reset_operate_data', 'discard_items')
    for name in names:
        original = getattr(cls, name)
        signature = inspect.signature(original)
        def make(name, original, signature):
            @functools.wraps(original)
            def wrapped(self, uid, *args, **kwargs):
                call = signature.bind(self, uid, *args, **kwargs)
                call.apply_defaults()
                tid = call.arguments.get('template_id', 100001)
                try:
                    with savepoint_transaction(self._db()):
                        c = require_action(self, uid, tid, name, call.arguments)
                        if name == 'hero_recruit':
                            attrs = {a['attr_id']: a['value'] for a in json.loads(c['state']['attr_list_json'])}
                            old = json.loads(c['state']['hero_list_json'])
                            call.arguments['new_heroes'] = validate_heroes(self, uid, call.arguments['new_heroes'],
                                existing={h['hero_id'] for h in old}, limit=max(1, int(attrs.get(8, 1))), allow_empty=True)
                        elif name == 'discard_items':
                            validate_discard(self, c, call.arguments['item_id_list'])
                        result = original(*call.args, **call.kwargs)
                        if result[0] == 0:
                            extra = result[1] if len(result) == 2 else None
                            if name == 'choose_event_option' and extra and not extra.get('pop_window'):
                                fresh_state = row(self, uid, tid)
                                if not fresh_state.get('select_node_id') and not has_pending(json.loads(fresh_state.get('other_info_json') or '{}')):
                                    pop = self._mech_popup(uid, fresh_state, tid, self._db())
                                    if pop:
                                        extra['pop_window'] = pop
                            if name == 'commit_event_selection':
                                fresh_state = row(self, uid, tid)
                                fresh_pending = json.loads(fresh_state.get('other_info_json') or '{}')
                                if not has_pending(fresh_pending) and not c['pending'].get('_resume_event'):
                                    owner = pending_owner(c)
                                    nodes = json.loads(fresh_state['map_info_json'])
                                    nid = owner['node_id']
                                    if nid and finish_map_node(nodes, nid):
                                        self._db().execute('UPDATE rogueteam_session SET map_info_json=? WHERE uid=? AND template_id=?', (json.dumps(nodes), uid, tid))
                                        extra['map_info'] = [self._norm_node(n) for n in nodes]
                                        extra['is_floor_clear'] = self._is_floor_clear(nodes)
                                        extra['session'] = self.get_session_data(uid, tid)
                            if name == 'choose_event_option' and extra and extra.get('pop_window'):
                                pending = copy.deepcopy(extra['pop_window'])
                                fresh = json.loads(row(self, uid, tid).get('other_info_json') or '{}')
                                if fresh.get('_history'):
                                    pending['_history'] = fresh['_history']
                                if fresh.get('event_id'):
                                    pending['_resume_event'] = fresh
                                self._db().execute('UPDATE rogueteam_session SET other_info_json=? WHERE uid=? AND template_id=?',
                                    (json.dumps(pending), uid, tid))
                                extra['is_floor_clear'] = False
                            if name == 'commit_event_selection' and c['pending'].get('_resume_event'):
                                fresh = json.loads(row(self, uid, tid).get('other_info_json') or '{}')
                                if has_pending(fresh):
                                    fresh['_resume_event'] = c['pending']['_resume_event']
                                else:
                                    fresh = c['pending']['_resume_event']
                                    extra['next_event'] = fresh
                                self._db().execute('UPDATE rogueteam_session SET other_info_json=? WHERE uid=? AND template_id=?',
                                    (json.dumps(fresh), uid, tid))
                            if name == 'discard_items':
                                self._db().execute('UPDATE rogueteam_session SET other_info_json=? WHERE uid=? AND template_id=?', ('{}', uid, tid))
                            if name == 'choose_event_option' and extra and call.arguments['opt_id'] in (600411, 600511):
                                fresh = row(self, uid, tid)
                                converted = next((n for n in json.loads(fresh['map_info_json']) if n['node_id'] == c['node']['node_id']), None)
                                if converted and converted.get('node_type') in BATTLE_TYPES and converted.get('state') == 0:
                                    meta = flow(fresh)
                                    meta['event_battle'] = dict(owner_key(c, c['node']['node_id']),
                                        event_id=60042 if call.arguments['opt_id'] == 600411 else 60053,
                                        original_node=copy.deepcopy(c['node']))
                                    save_flow(self, uid, tid, meta)
                            if name == 'commit_event_selection' and c['pending'].get('_resume_event'):
                                fresh = row(self, uid, tid)
                                pending = json.loads(fresh.get('other_info_json') or '{}')
                                meta = flow(fresh)
                                battle_event = meta.get('event_battle')
                                if pending.get('event_id') and battle_event and battle_event.get('won'):
                                    nodes = json.loads(fresh['map_info_json'])
                                    restored = dict(battle_event['original_node'], state=1)
                                    nodes = [restored if n['node_id'] == restored['node_id'] else n for n in nodes]
                                    self._db().execute('UPDATE rogueteam_session SET map_info_json=?,select_node_id=? WHERE uid=? AND template_id=?',
                                        (json.dumps(nodes), restored['node_id'], uid, tid))
                                    meta.pop('event_battle', None)
                                    save_flow(self, uid, tid, meta)
                                    extra['map_info'] = [self._norm_node(n) for n in nodes]
                                    extra['session'] = self.get_session_data(uid, tid)
                                    extra['is_floor_clear'] = False
                            if name == 'choose_event_option' and extra and extra.get('map_switch'):
                                sign = extra['map_switch']
                                require(bool(c['meta'].get('hidden')) == (sign == 3), '隐藏图方向不匹配')
                                meta = flow(row(self, uid, tid))
                                require(not meta.get('map_switch'), '隐藏图切换已待处理')
                                meta['map_switch'] = dict(owner_key(c, c['node']['node_id']), sign=sign)
                                save_flow(self, uid, tid, meta)
                                fresh = row(self, uid, tid)
                                if has_pending(json.loads(fresh.get('other_info_json') or '{}')):
                                    # A threshold reward owns the room until consumed.
                                    # Keep CLEAN so cold native path selection stays on
                                    # relay, and no map-switch animation can run early.
                                    nodes = json.loads(fresh['map_info_json'])
                                    for node in nodes:
                                        if node['node_id'] == c['node']['node_id']:
                                            node['state'] = 1
                                    self._db().execute('UPDATE rogueteam_session SET map_info_json=?,select_node_id=? WHERE uid=? AND template_id=?',
                                        (json.dumps(nodes), c['node']['node_id'], uid, tid))
                                    extra['map_info'] = [self._norm_node(n) for n in nodes]

                            if extra and flow(row(self, uid, tid)).get('map_switch'):
                                extra['is_floor_clear'] = False
                            bind_pending(self, uid, tid, c, selected=call.arguments['node_id'] if name == 'select_node' else None)
                        return result
                except InvalidTransition:
                    return (2, [], None) if name == 'hero_recruit' else (2, None if name == 'select_node' else {})
            return wrapped
        setattr(cls, name, make(name, original, signature))

    original_step = cls.advance_room_step
    @functools.wraps(original_step)
    def step(self, uid, session, node_type, template_id=100001, out_attr=None):
        state = row(self, uid, template_id)
        meta = flow(state)
        nid = int(session.get('select_node_id') or 0)
        require(nid > 0, '推演计步缺少节点')
        key = str(session['floor_num']) + ':' + str(int(bool(meta.get('hidden')))) + ':' + str(nid)
        steps = meta.setdefault('stepped_nodes', [])
        if key in steps:
            return
        result = original_step(self, uid, session, node_type, template_id, out_attr)
        # Effects may update flow in future; merge only this receipt into the
        # newest metadata and preserve phase/pending ownership.
        meta = flow(row(self, uid, template_id))
        meta.setdefault('stepped_nodes', []).append(key)
        save_flow(self, uid, template_id, meta)
        return result
    cls.advance_room_step = step

    def replace_node(self, uid, node_id, node_type, template_id=100001):
        try:
            with savepoint_transaction(self._db()):
                c = context(self, uid, template_id)
                pending_owner(c)
                require(c['pending'].get('event_type') == 10, '当前没有节点替换待办')
                require(type(node_id) is int and type(node_type) is int and node_type in REPLACE_TYPES, '替换节点类型无效')
                by_id = {n['node_id']: n for n in c['nodes']}
                origin = c['node'] or max((n for n in c['nodes'] if n.get('state') == 3), key=lambda n: n.get('col', 0), default=None)
                todo = list(origin.get('next_id_list', [])) if origin else list(by_id)
                reachable = set()
                while todo:
                    nid = todo.pop()
                    if nid in reachable or nid not in by_id:
                        continue
                    reachable.add(nid);todo.extend(by_id[nid].get('next_id_list', []))
                target = by_id.get(node_id)
                require(target and node_id in reachable and target.get('row', 0) != 0
                        and target.get('state') in (0, 2), '节点不在可替换路径')
                from rogueteam_service import RogueTeamMapGenerator
                target['node_type'] = node_type
                target['param'] = RogueTeamMapGenerator._get_node_param(node_type, c['state']['floor_num'])
                self._db().execute('UPDATE rogueteam_session SET map_info_json=?,other_info_json=? WHERE uid=? AND template_id=?',
                    (json.dumps(c['nodes']), '{}', uid, template_id))
                bind_pending(self, uid, template_id, c)
                return 0, dict(map_info=[self._norm_node(n) for n in c['nodes']], session=self.get_session_data(uid, template_id))
        except InvalidTransition:
            return 2, {}
    cls.close_node_replace = replace_node


def validate_discard(service, c, ids):
    require(isinstance(ids, list) and all(type(i) is int for i in ids) and len(set(ids)) == len(ids), '丢弃列表无效')
    params = c['pending'].get('param_list') or []
    require(len(params) == 1, '丢弃待办参数无效')
    eff = service.config.get_effect(params[0].get('param')) or {}
    action, filt = eff.get('action'), eff.get('params') or []
    allowed = set()
    if action == 9:
        require(len(filt) >= 2, '丢弃藏品范围缺失')
        for t in json.loads(c['state']['treasure_list_json']):
            if isinstance(t, dict) and filt[0] <= t.get('rare', 1) <= filt[1]:
                allowed.add(t['id'])
    elif action == 10:
        require(filt and isinstance(filt[0], list), '丢弃圣物类别缺失')
        for t in json.loads(c['state']['other_item_list_json']):
            iid = t['id'] if isinstance(t, dict) else t
            if (service.config.get_item(iid) or {}).get('sub_type') in filt[0]:
                allowed.add(iid)
    else:
        raise InvalidTransition('丢弃效果不匹配')
    count = params[0].get('buy_times', 0)
    require(type(count) is int and count > 0 and len(ids) == min(count, len(allowed)) and set(ids) <= allowed, '丢弃数量或范围不符')
