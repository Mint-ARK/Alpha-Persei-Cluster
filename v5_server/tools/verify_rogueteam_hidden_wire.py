"""Small Python mirror of native path/map-switch consumers, NOT a Lua VM.

Sources: ChallengeRogueTeamPathData, its MapData subclass, RogueTeamAction's
88001/88003/88221 bindings and RequestNextMapData, plus map-switch effect UI.
It models their state changes, not rendering, Lua variable recovery or timers.
"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
import verify_rogueteam_login as login_probe
import verify_rogueteam_battle as battle_probe
import codec
import hero_codec
import middleware as mw
import rogueteam_flow as flow
from rogueteam_service import RogueTeamService


class NativeMapMirror:
    def __init__(self):
        self.nodes = {}
        self.finished_path = []

    def columns(self):
        return sorted({node['col'] for node in self.nodes.values()})

    def init_data(self, values):
        self.nodes = {row['node_id']: deepcopy(row) for row in values}
        self.finished_path = []
        # SearchFinishPath takes one OVER node per consecutive column.
        for col in self.columns():
            if col != len(self.finished_path) + 1:
                break
            done = [node['node_id'] for node in self.nodes.values() if node['col'] == col and node['state'] == 3]
            if not done:
                break
            self.finished_path.append(done[0])

    def update_data(self, values):
        for row in values:
            self.nodes[row['node_id']] = deepcopy(row)

    def is_finished(self):
        cols = self.columns()
        return not cols or any(row['col'] == cols[-1] and row['state'] == 3 for row in self.nodes.values())


class NativePathMirror:
    def __init__(self):
        self.maps = [NativeMapMirror(), NativeMapMirror()]
        self.floor = 0
        self.floor_state = 0
        self.selected = 0
        self.current_map = 1
        self.refresh_sign = None
        self.exit_second_map = False
        self.switch_queued = False
        self.requested_sign = None
        self.trace = []

    def check_finish_node(self):
        for graph in self.maps:
            node = graph.nodes.get(self.selected)
            if node and node['state'] == 3:
                graph.finished_path.append(node['node_id'])
                self.selected = 0
        self.current_map = 1
        if self.refresh_sign == 3:
            self.current_map = 2
            return
        if self.refresh_sign == 2 or self.exit_second_map:
            return
        for index in (2, 1):
            if not self.maps[index-1].is_finished():
                self.current_map = index
                break

    def check_need_switch(self):
        # Tools.IsClearMapFloor + HasOnClearStateNode. The real UI prevents
        # duplicate effect windows; only a Boolean queue is needed here.
        if self.refresh_sign is not None and not self.selected and not any(
                node['state'] == 1 for node in self.maps[self.current_map-1].nodes.values()):
            self.switch_queued = True

    def receive(self, cmd, packet):
        if cmd == 88001:
            self.floor = packet['floor_num']
            self.floor_state = packet['floor_state']
            self.selected = packet['select_node_id']
            self.maps[0].init_data(packet.get('map_info', []))
            self.maps[1].init_data(packet.get('second_map_info', []))
            self.check_finish_node()
            self.check_need_switch()
        elif cmd == 88003:
            self.maps[0].update_data(packet.get('map_info', []))
            self.maps[1].update_data(packet.get('second_map_info', []))
            self.check_finish_node()
            self.check_need_switch()
        elif cmd == 88221:
            # Crucially the Lua binding does NOT call CheckFinishNode here.
            self.refresh_sign = packet['sign']
            self.check_need_switch()
        elif cmd == 88009 and packet['result'] == 0:
            if self.requested_sign not in (2, 3):
                raise AssertionError('Mirror has no matching native map-switch request')
            previous_sign = self.requested_sign
            self.refresh_sign = None
            if previous_sign == 3:
                self.exit_second_map = True
                self.check_finish_node()
                if not self.maps[0].finished_path:
                    raise AssertionError('Native exit callback needs the main map final completed node')
            elif previous_sign == 2:
                self.check_finish_node()
            self.requested_sign = None
            self.switch_queued = False
        self.trace.append((cmd, self.floor, self.current_map, self.selected, self.refresh_sign))

    def select_locally(self, node_id):
        # RequestSelectNode changes the local pointer before its 88005 ACK.
        if node_id not in self.maps[self.current_map-1].nodes:
            raise AssertionError('Selected node is not in the displayed graph')
        self.selected = node_id

    def request_next_map(self):
        # The effect view sends this after animation progress >=0.7.
        if not self.switch_queued or self.refresh_sign not in (2, 3):
            raise AssertionError('The native switch effect would not send 88008')
        self.requested_sign = self.refresh_sign
        return dict(sign=self.refresh_sign)

    def battle_map_key(self, map_template=1):
        # PathGetRogueTeamMapID always resolves a MAIN map node, even while
        # selecting a hidden node. Its fallback is main.finished_path[-1].
        node_id = self.selected if self.current_map == 1 else 0
        if not node_id:
            if not self.maps[0].finished_path:
                raise AssertionError('Hidden battle cannot resolve the native main-map strength source')
            node_id = self.maps[0].finished_path[-1]
        node = self.maps[0].nodes.get(node_id)
        if node is None:
            raise AssertionError('Native strength lookup points outside map_info')
        return map_template, self.floor, node['col']


def node(i, col, state, *, kind=8, param=60030, next_ids=()):
    return dict(node_id=i, col=col, row=3, node_type=kind, state=state, param=param, next_id_list=list(next_ids))


def snapshot(main, hidden=(), selected=0):
    return dict(floor_num=2, floor_state=0, select_node_id=selected, map_info=deepcopy(main), second_map_info=deepcopy(hidden))


def expect_state(consumer, *, phase, main_ids, hidden_ids, source_column=4):
    expected_map = 1 if phase in ('pending2', 'returned') else 2
    expected_sign = {'pending2': 2, 'pending3': 3}.get(phase)
    assert consumer.floor == 2, consumer.trace
    assert consumer.current_map == expected_map, consumer.trace
    assert consumer.refresh_sign == expected_sign, consumer.trace
    assert set(consumer.maps[0].nodes) == set(main_ids), consumer.trace
    assert set(consumer.maps[1].nodes) == set(hidden_ids), consumer.trace
    assert consumer.battle_map_key() == (1, 2, source_column), consumer.trace


class MirrorSourceTests(unittest.TestCase):
    def setUp(self):
        self.main = [node(100+col, col, 3, next_ids=[101+col]) for col in range(1, 5)]
        self.main.append(node(105, 5, 0, kind=1, param=4102101))
        self.hidden = [node(900001, 1, 0)]

    def test_pending_enter_ack_then_active_and_cold_active_use_main_strength_source(self):
        c = NativePathMirror()
        c.receive(88221, dict(sign=2))
        c.receive(88001, snapshot(self.main, self.hidden))
        expect_state(c, phase='pending2', main_ids=range(101, 106), hidden_ids=[900001])
        self.assertEqual(c.request_next_map(), dict(sign=2))
        c.receive(88001, snapshot(self.main, self.hidden))
        c.receive(88009, dict(result=0))
        expect_state(c, phase='activehidden', main_ids=range(101, 106), hidden_ids=[900001])
        c.select_locally(900001)
        self.assertEqual(c.battle_map_key(), (1, 2, 4))
        cold = NativePathMirror(); cold.receive(88001, snapshot(self.main, self.hidden, selected=900001))
        expect_state(cold, phase='activehidden', main_ids=range(101, 106), hidden_ids=[900001])

    def test_pending_exit_and_cold_return_keep_original_parent_graph_and_floor(self):
        completed = [dict(self.hidden[0], state=3)]
        c = NativePathMirror()
        c.receive(88221, dict(sign=3))
        c.receive(88001, snapshot(self.main, completed))
        expect_state(c, phase='pending3', main_ids=range(101, 106), hidden_ids=[900001])
        self.assertEqual(c.request_next_map(), dict(sign=3))
        c.receive(88001, snapshot(self.main, completed))
        c.receive(88009, dict(result=0))
        expect_state(c, phase='returned', main_ids=range(101, 106), hidden_ids=[900001])
        cold = NativePathMirror(); cold.receive(88001, snapshot(self.main, completed))
        expect_state(cold, phase='returned', main_ids=range(101, 106), hidden_ids=[900001])

    def test_flag_after_snapshot_does_not_recompute_native_map_choice(self):
        c = NativePathMirror(); c.receive(88001, snapshot(self.main, self.hidden))
        self.assertEqual(c.current_map, 2)
        c.receive(88221, dict(sign=2))
        self.assertEqual(c.current_map, 2)
        # An actual subsequent 88003, not an imagined flag callback, fixes it.
        c.receive(88003, dict(map_info=[], second_map_info=self.hidden))
        self.assertEqual(c.current_map, 1)

    def test_empty_parent_completed_path_cannot_support_a_hidden_battle(self):
        c = NativePathMirror(); c.receive(88001, snapshot([node(105, 5, 3)], self.hidden, selected=900001))
        with self.assertRaises(AssertionError): c.battle_map_key()


class CoreHiddenWireTests(MemoryTests):
    """Feed actual Core/codec and serialized cold-login packets to the mirror."""
    flood = login_probe.RogueLoginTests.flood
    enter_battle = battle_probe.RogueBattleTests.enter
    finish_battle = battle_probe.RogueBattleTests.finish

    def setUp(self):
        super().setUp()
        for p in (patch('time.time', return_value=NOW), patch.dict(mw._BATTLE_SESSIONS, {}, clear=True)):
            p.start(); self.addCleanup(p.stop)
        self.svc = RogueTeamService.get(db=self.db)
        self.db.upsert('hero', UID, hero_codec.initial_hero_row(1084, 100), keys=('uid', 'id'))
        self.db.upsert('rogueteam_user', UID, dict(template_id=100001, max_difficult=4), keys=('uid', 'template_id'))
        for iid in (70, 71):
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,1)', (iid,))
        self.assertEqual(self.svc.start_run(UID, 100001, 1, [dict(hero_id=1084)], [])[0], 0)
        main = [node(101, 1, 3, kind=1, param=4102101, next_ids=[201]),
                node(201, 2, 0, param=60020, next_ids=[301]),
                node(301, 3, 2, kind=1, param=4102102)]
        self.db.upsert('rogueteam_session', UID, dict(template_id=100001, floor_num=2, floor_state=0,
            map_info_json=json.dumps(main), second_map_json='[]', select_node_id=0,
            other_info_json='{}', shop_info_json='{}'), keys=('uid', 'template_id'))
        meta = flow.flow(self.saved())
        meta.update(hidden=False, phase='active')
        for key in ('pending_owner', 'shop_owner', 'map_switch', 'stepped_nodes'):
            meta.pop(key, None)
        flow.save_flow(self.svc, UID, 100001, meta)
        self.consumer = NativePathMirror()
        self.ok(88030, type=0)
        self.hidden_id = None

    def saved(self):
        return self.db.get('rogueteam_session', UID, 'AND template_id=?', (100001,))

    def feed_response(self, response, consumer=None):
        consumer = consumer or self.consumer
        for frame in response.frames:
            sc = frame['sc']
            if sc in (88001, 88003, 88009, 88221):
                consumer.receive(sc, codec.decode(frame['payload'], 'sc_' + str(sc)))

    def ok(self, cmd, **data):
        if cmd == 88004:
            self.consumer.select_locally(data['node_id'])
        if cmd == 88008:
            self.assertEqual(self.consumer.request_next_map(), dict(sign=data['sign']))
        packet, response = self.call(cmd, data, cmd+1)
        self.assertEqual(packet['result'], 0, (cmd, data, response.scs))
        if cmd == 88030:
            self.assertLess(response.scs.index(88001), response.scs.index(88031))
            if 88221 in response.scs:
                self.assertLess(response.scs.index(88221), response.scs.index(88001))
        self.feed_response(response)
        return packet, response

    def expect_phase(self, phase, *, consumer=None):
        consumer = consumer or self.consumer
        hidden = [self.hidden_id] if self.hidden_id is not None else []
        expect_state(consumer, phase=phase, main_ids=[101, 201, 301], hidden_ids=hidden, source_column=2)
        self.assertEqual(consumer.maps[0].nodes[201]['param'], 60020)
        self.assertEqual(consumer.maps[0].nodes[201]['state'], 3)

    def cold_phase(self, phase):
        before = self.saved()
        # Resume RPC's ACK opens the native path view, so all state must have
        # reached a fresh consumer before 88031 invokes that callback.
        previous = self.consumer
        self.consumer = NativePathMirror()
        try:
            self.ok(88030, type=0)
            self.expect_phase(phase)
        finally:
            self.consumer = previous
        packets = self.flood(fresh=True, captured=True)
        consumer = NativePathMirror()
        for sc, obj, raw in packets:
            consumer.receive(sc, obj)
        self.expect_phase(phase, consumer=consumer)
        self.assertEqual(self.saved(), before)
        sequence = [sc for sc, obj, raw in packets]
        if phase in ('pending2', 'pending3'):
            self.assertLess(sequence.index(88221), sequence.index(88001))
        self.consumer = consumer
        return consumer

    def enter_hidden(self):
        self.ok(88004, node_id=201)
        self.ok(88024, opt_id=600202)
        _, prepared = self.ok(88024, opt_id=600221)
        self.assertLess(prepared.scs.index(88221), prepared.scs.index(88003))
        self.expect_phase('pending2')
        self.cold_phase('pending2')
        _, accepted = self.ok(88008, sign=2)
        self.assertLess(accepted.scs.index(88001), accepted.scs.index(88009))
        snapshot_value = self.svc.get_session_data(UID)
        self.assertEqual(len(snapshot_value['second_map_info']), 1)
        self.hidden_id = snapshot_value['second_map_info'][0]['node_id']
        self.expect_phase('activehidden')
        self.cold_phase('activehidden')
        self.ok(88004, node_id=self.hidden_id)
        self.node = self.hidden_id
        self.assertEqual(self.consumer.battle_map_key(), (1, 2, 2))

    def test_real_core_nonbattle_round_trip_and_four_cold_phase_consumers(self):
        self.enter_hidden()
        for opt in (600301, 600402):
            self.ok(88024, opt_id=opt)
        _, prepared = self.ok(88024, opt_id=600421)
        self.assertLess(prepared.scs.index(88221), prepared.scs.index(88003))
        self.expect_phase('pending3')
        self.cold_phase('pending3')
        _, accepted = self.ok(88008, sign=3)
        self.assertLess(accepted.scs.index(88001), accepted.scs.index(88009))
        self.expect_phase('returned')
        self.cold_phase('returned')

    def has_toolkit(self):
        return any(item['id'] == 1330003 for item in json.loads(self.saved()['other_item_list_json']))

    def battle_branch(self, route, approach, battle_option, after_event, collect):
        self.enter_hidden()
        for opt in (route, approach, battle_option):
            self.ok(88024, opt_id=opt)
        self.assertFalse(self.has_toolkit(), 'Native toolkit belongs to the later collect option')
        self.assertEqual(self.consumer.current_map, 2)
        self.assertEqual(self.consumer.battle_map_key(), (1, 2, 2))
        # Converting an event to battle clears select_node_id so native UI can
        # click the changed battle node once more before 54030.
        self.ok(88004, node_id=self.hidden_id)
        packet, entered = self.enter_battle()
        self.assertEqual(packet['result'], 0, entered.scs)
        self.feed_response(entered)
        packet, settled = self.finish_battle()
        self.assertEqual(packet['result'], 0, settled.scs)
        self.feed_response(settled)
        self.assertFalse(self.has_toolkit())
        for _ in range(8):
            pending = json.loads(self.saved()['other_info_json'])
            if pending.get('event_id') == after_event:
                break
            self.assertTrue(pending.get('param_list'), pending)
            self.ok(88200, event_id=pending['event_type'], param_arg=pending['param_list'][0]['index'])
        self.assertEqual(json.loads(self.saved()['other_info_json'])['event_id'], after_event)
        self.assertFalse(self.has_toolkit())
        self.ok(88024, opt_id=collect)
        self.assertTrue(self.has_toolkit())
        self.expect_phase('pending3')
        self.cold_phase('pending3')
        self.ok(88008, sign=3)
        self.expect_phase('returned')
        self.cold_phase('returned')

    def test_actual_hidden_core_battle_600411_resumes_before_collect_and_return(self):
        self.battle_branch(600301, 600401, 600411, 60042, 600421)

    def test_actual_hidden_core_battle_600511_resumes_before_collect_and_return(self):
        self.battle_branch(600302, 600501, 600511, 60053, 600531)


if __name__ == '__main__':
    unittest.main(verbosity=2)
