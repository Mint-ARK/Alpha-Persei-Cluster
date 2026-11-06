"""Current Core/codec + memory-only Rogue transition acceptance."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import codec
import hero_codec
import rogueteam_flow as flow
import rogueteam_transition as transition
from rogueteam_service import RogueTeamService


class TransitionTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc = RogueTeamService.get(db=self.db)
        for hid in (1084, 1050, 1039):
            self.db.upsert('hero', UID, hero_codec.initial_hero_row(hid, 100), keys=('uid', 'id'))
        self.assertEqual(self.svc.start_run(UID, 100001, 1, [dict(hero_id=1084)], [])[0], 0)

    def saved(self):
        return self.db.get('rogueteam_session', UID, 'AND template_id=?', (100001,))

    def set(self, **kw):
        self.db.upsert('rogueteam_session', UID, dict(template_id=100001, **kw), keys=('uid', 'template_id'))

    def state(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid') for t in
                ('rogueteam_session', 'rogueteam_user', 'rogueteam_illustrated', 'rogueteam_history', 'currency', 'material')}

    def attrs(self, **values):
        current = {a['attr_id']: a['value'] for a in json.loads(self.saved()['attr_list_json'])}
        current.update({int(k): v for k, v in values.items()})
        self.set(attr_list_json=json.dumps([dict(attr_id=k, value=v) for k, v in current.items()]))
        return current

    def map(self, kind=1, selected=101, state=0, param=None):
        if param is None:
            param = 4101101 if kind == 1 else (50010 if kind == 6 else 30010)
        self.set(map_info_json=json.dumps([
            dict(node_id=101, col=1, row=1, node_type=kind, state=state, param=param, next_id_list=[102]),
            dict(node_id=102, col=2, row=1, node_type=1, state=2, param=4101102, next_id_list=[])]),
            select_node_id=selected, other_info_json='{}', shop_info_json='{}')
        meta = flow.flow(self.saved())
        for k in ('pending_owner', 'shop_owner', 'stepped_nodes'):
            meta.pop(k, None)
        flow.save_flow(self.svc, UID, 100001, meta)

    def op(self, cmd, **data):
        return self.call(cmd, data, cmd + 1)

    def ok(self, cmd, **data):
        p, r = self.op(cmd, **data)
        self.assertEqual(p['result'], 0, (cmd, data, p, r.scs))
        return p, r

    def rejected(self, cmd, **data):
        before = self.state()
        p, r = self.op(cmd, **data)
        self.assertNotEqual(p['result'], 0, (cmd, data))
        self.assertEqual(before, self.state(), (cmd, data))
        return p, r

    def recruit_window(self):
        self.map(6, state=1)
        self.set(other_info_json=json.dumps(dict(event_type=3, param_list=[])))

    def reward_window(self, queue=False):
        self.map(1, state=1)
        p = dict(event_type=1, param_list=[dict(index=1, param=1410101, rare=1)], drop_type=1)
        if queue:
            p['_queue'] = [dict(event_type=2, param_list=[dict(index=1, param=1310001, rare=1)], drop_type=2)]
        self.set(other_info_json=json.dumps(p))

    def test_battle_cannot_be_completed_by_event_exit_or_shop_exit(self):
        self.map()
        self.rejected(88024, opt_id=0)
        self.rejected(88222, index=0)
        self.assertEqual(json.loads(self.saved()['map_info_json'])[0]['state'], 0)

    def test_recruit_without_context_cannot_clear_rewards_or_add_heroes(self):
        self.reward_window()
        self.rejected(88102, hero_list=[])
        self.rejected(88102, hero_list=[dict(hero_id=999999, hp_ratio=10000)])

    def test_refresh_outside_shop_cannot_spend_or_create_goods(self):
        self.map();self.attrs(**{'6': 1000, '30': 1})
        self.rejected(88224)
        self.assertEqual(json.loads(self.saved()['shop_info_json']), {})

    def test_recruit_owns_or_uses_configured_trial_and_limits_number(self):
        self.recruit_window()
        for heroes in ([dict(hero_id=999999)], [dict(hero_id=1084)], [dict(hero_id=1055)],
                       [dict(hero_id=1050), dict(hero_id=1039)], [dict(hero_id=1049, temp_id=3061055)]):
            self.rejected(88102, hero_list=heroes)
        self.ok(88102, hero_list=[dict(hero_id=1049, temp_id=3061049, hp_ratio=1)])
        heroes = json.loads(self.saved()['hero_list_json'])
        self.assertIn(dict(hero_id=1049, temp_id=3061049, hp_ratio=10000), heroes)
        self.assertEqual(json.loads(self.saved()['other_info_json'])['event_id'], 50011)
        self.rejected(88102, hero_list=[dict(hero_id=1039)])

    def test_recruit_empty_is_only_allowed_as_native_decline(self):
        self.recruit_window()
        self.ok(88102, hero_list=[])
        self.assertEqual(len(json.loads(self.saved()['hero_list_json'])), 1)
        self.assertEqual(json.loads(self.saved()['other_info_json'])['event_id'], 50011)

    def test_recruit_required_hero_or_event_frame_failure_rolls_back(self):
        self.recruit_window()
        ctx = self.conn.to_handler_ctx();encode = ctx.codec_encode
        for schema in ('sc_88019', 'sc_88023'):
            with patch.object(ctx, 'codec_encode', side_effect=lambda n, d, schema=schema: None if n == schema else encode(n, d)):
                self.rejected(88102, hero_list=[dict(hero_id=1050)])
        self.ok(88102, hero_list=[dict(hero_id=1050)])

    def test_shop_refresh_native_eligibility_cost_and_exit_once(self):
        self.map(5, selected=0)
        self.ok(88004, node_id=101)
        count = self.saved()['room_passed_count']
        self.attrs(**{'6': 1000})
        self.rejected(88224)
        self.attrs(**{'30': 1})
        self.ok(88224);self.ok(88224)
        attrs = {a['attr_id']: a['value'] for a in json.loads(self.saved()['attr_list_json'])}
        self.assertEqual(attrs[6], 940)
        self.ok(88222, index=0)
        self.assertEqual(self.saved()['room_passed_count'], count)
        self.rejected(88222, index=0)

    def test_event_requires_terminal_description_and_steps_exactly_once(self):
        self.map(6, state=1)
        self.set(other_info_json=json.dumps(dict(event_id=50011, opt_list=[dict(opt_id=500111)], trigger_type=1)))
        self.rejected(88024, opt_id=0)
        self.ok(88024, opt_id=500111)
        self.ok(88024, opt_id=0)
        self.assertEqual(self.saved()['room_passed_count'], 1)
        self.rejected(88024, opt_id=0)
        self.assertEqual(len(flow.flow(self.saved())['stepped_nodes']), 1)

    def test_event_popup_is_persisted_with_finished_node_owner_and_cold_recovery(self):
        self.map(6, state=1)
        self.set(other_info_json=json.dumps(dict(event_id=50012, opt_list=[], trigger_type=1)))
        self.attrs(**{'1': 95})
        _, response = self.ok(88024, opt_id=0)
        self.assertIn(88015, response.scs)
        pending = json.loads(self.saved()['other_info_json'])
        self.assertEqual(pending['event_type'], 11)
        self.assertEqual(flow.flow(self.saved())['pending_owner']['node_id'], 101)
        self.assertIn(88015, self.ok(88030, type=0)[1].scs)
        self.ok(88200, event_id=11, param_arg=pending['param_list'][0]['index'])
        self.assertEqual(self.saved()['room_passed_count'], 1)
        self.assertFalse(transition.has_pending(json.loads(self.saved()['other_info_json'])))

    def test_real_battle_reward_with_zero_selected_finishes_exact_owner(self):
        self.map()
        with self.db.transaction():
            self.assertEqual(self.svc.settle_battle_node(UID, 101, win=True)[0], 0)
        self.assertEqual(self.saved()['select_node_id'], 0)
        self.assertEqual(flow.flow(self.saved())['pending_owner']['node_id'], 101)
        pending = json.loads(self.saved()['other_info_json'])
        self.ok(88200, event_id=pending['event_type'], param_arg=pending['param_list'][0]['index'])
        self.assertEqual(json.loads(self.saved()['map_info_json'])[0]['state'], 3)
        self.assertEqual(self.saved()['room_passed_count'], 1)

    def test_legacy_zero_selected_single_clean_battle_reward_restores_without_reroll(self):
        self.reward_window(queue=True)
        self.set(select_node_id=0)
        before = json.loads(self.saved()['other_info_json'])
        self.ok(88030, type=0)
        self.assertEqual(json.loads(self.saved()['other_info_json']), before)
        self.ok(88200, event_id=1, param_arg=1)
        self.assertEqual(flow.flow(self.saved())['pending_owner']['node_id'], 101)
        self.ok(88200, event_id=2, param_arg=1)
        self.assertEqual(json.loads(self.saved()['map_info_json'])[0]['state'], 3)

    def test_legacy_ambiguous_clean_reward_fails_without_guessing(self):
        self.reward_window();self.set(select_node_id=0)
        nodes = json.loads(self.saved()['map_info_json']);nodes[1]['state'] = 1
        self.set(map_info_json=json.dumps(nodes))
        self.rejected(88200, event_id=1, param_arg=1)

    def test_resume_emits_required_empty_attribute_and_hero_lists(self):
        self.set(hero_list_json='[]', attr_list_json='[]')
        _, response = self.ok(88030, type=0)
        self.assertIn(88019, response.scs)
        self.assertIn(88013, response.scs)
        self.assertEqual(next(f['payload'] for f in response.frames if f['sc'] == 88013), b'')

    def test_two_reward_types_consume_in_order_and_repeat_cannot_finish_next(self):
        self.reward_window(queue=True)
        p, r = self.ok(88200, event_id=1, param_arg=1)
        self.assertFalse(p['window_opt'])
        self.assertEqual(self.saved()['select_node_id'], 101)
        self.assertEqual(json.loads(self.saved()['map_info_json'])[0]['state'], 1)
        self.rejected(88200, event_id=1, param_arg=1)
        self.ok(88200, event_id=2, param_arg=1)
        self.assertEqual(self.saved()['select_node_id'], 0)
        self.assertEqual(json.loads(self.saved()['map_info_json'])[0]['state'], 3)

    def test_reward_frame_failure_rolls_back_item_and_queue(self):
        self.reward_window(queue=True)
        ctx = self.conn.to_handler_ctx();encode = ctx.codec_encode
        for schema in ('sc_88021', 'sc_88001', 'sc_88015'):
            with patch.object(ctx, 'codec_encode', side_effect=lambda n, d, schema=schema: None if n == schema else encode(n, d)):
                self.rejected(88200, event_id=1, param_arg=1)

    def test_pending_owner_rejects_run_floor_hidden_and_selected_changes(self):
        self.reward_window()
        c = transition.context(self.svc, UID)
        transition.bind_pending(self.svc, UID, 100001, c)
        good = flow.flow(self.saved())
        for key, value in (('run_id', 'old'), ('floor', 99), ('hidden', True), ('node_id', 102)):
            meta = json.loads(json.dumps(good));meta['pending_owner'][key] = value
            flow.save_flow(self.svc, UID, 100001, meta)
            self.rejected(88200, event_id=1, param_arg=1)
        flow.save_flow(self.svc, UID, 100001, good)
        self.ok(88200, event_id=1, param_arg=1)

    def test_ending_phase_freezes_gameplay(self):
        self.recruit_window()
        meta = flow.flow(self.saved());meta['phase'] = 'ending_plot'
        flow.save_flow(self.svc, UID, 100001, meta)
        for cmd, data in ((88102, dict(hero_list=[])), (88024, dict(opt_id=0)), (88222, dict(index=0)),
                          (88224, {}), (88202, dict(event_id=1)), (88212, dict(item_id_list=[])), (88004, dict(node_id=101))):
            self.rejected(cmd, **data)

    def test_reset_only_treasure_window_and_keeps_owner_queue(self):
        self.reward_window(queue=True)
        self.rejected(88202, event_id=2)
        self.ok(88202, event_id=1)
        pending = json.loads(self.saved()['other_info_json'])
        self.assertEqual(len(pending['_queue']), 1)
        self.assertEqual(flow.flow(self.saved())['pending_owner']['node_id'], 101)
        self.set(other_info_json=json.dumps(dict(event_type=2, param_list=[dict(index=1, param=1310001)])))
        self.rejected(88202, event_id=1)

    def test_no_pending_discard_or_replace_cannot_fake_success(self):
        self.map()
        self.rejected(88212, item_id_list=[1410101])
        self.rejected(88208, node_id=102, node_type=5)

    def test_configured_replace_updates_only_reachable_node_and_consumes_pending(self):
        self.map(7, state=1)
        self.set(other_info_json=json.dumps(dict(event_type=10, param_list=[])))
        self.rejected(88208, node_id=101, node_type=5)
        self.rejected(88208, node_id=102, node_type=4)
        self.ok(88208, node_id=102, node_type=5)
        self.assertEqual(json.loads(self.saved()['map_info_json'])[1]['node_type'], 5)
        self.assertFalse(json.loads(self.saved()['other_info_json']))

    def test_discard_validates_current_effect_filter_and_quantity(self):
        self.map(7, state=1)
        self.set(treasure_list_json=json.dumps([dict(id=1410101, rare=1), dict(id=1410102, rare=2)]),
                 other_info_json=json.dumps(dict(event_type=9, param_list=[dict(index=1, param=4006111, buy_times=1)])))
        self.rejected(88212, item_id_list=[1410102])
        self.rejected(88212, item_id_list=[])
        self.ok(88212, item_id_list=[1410101])
        self.assertEqual(json.loads(self.saved()['treasure_list_json']), [dict(id=1410102, rare=2)])
        self.assertFalse(json.loads(self.saved()['other_info_json']))

    def test_required_frame_allows_schema_empty_repeated_but_rejects_none(self):
        ctx = self.conn.to_handler_ctx()
        self.assertEqual(transition.required_frame(ctx, 88003, dict(map_info=[], second_map_info=[])).payload, b'')
        with patch.object(ctx, 'codec_encode', return_value=None):
            with self.assertRaises(transition.InvalidTransition):
                transition.required_frame(ctx, 88003, dict(map_info=[], second_map_info=[]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
