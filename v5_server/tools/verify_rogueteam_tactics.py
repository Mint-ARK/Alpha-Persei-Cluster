"""Native six-program tactic acquisition: actual Core/codec, memory AccountDB."""
import json
import unittest
from unittest.mock import patch

from verify_report_small_fixes import UID
from verify_rogueteam_transition import TransitionTests
from verify_rogueteam_login import RogueLoginTests
import achievement_service as achievement
import codec
import rogueteam_flow as flow
import rogueteam_tactics as tactics


class TacticTests(TransitionTests):
    flood = RogueLoginTests.flood

    def setUp(self):
        super().setUp()
        self.raw = []
        self.ach = achievement.AchievementService(self.db)
        self.ach.init_user_achievements(UID)
        for iid in (70, 71):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)', (iid,))

    def programs(self, camp):
        return [cfg['id'] for cfg in tactics.CONTRACT['programs'].values() if camp in cfg['camps']][:6]

    def offer(self, iid):
        self.reward_window()
        self.set(other_info_json=json.dumps(dict(event_type=1,
            param_list=[dict(index=1, param=iid, rare=1)], drop_type=1)))

    def seed(self, camp, count=5):
        current = json.loads(self.saved()['treasure_list_json'])
        known = {item['id'] for item in current}
        for iid in self.programs(camp)[:count]:
            if iid not in known:
                current.append(dict(id=iid, rare=1))
        self.set(treasure_list_json=json.dumps(current))

    def gain(self, camp):
        self.seed(camp)
        self.offer(self.programs(camp)[5])
        return self.ok(88200, event_id=1, param_arg=1)[1]

    def packets(self, response, sc):
        return [codec.decode(frame['payload'], 'sc_' + str(sc))
                for frame in response.frames if frame['sc'] == sc]

    def snap(self):
        return self.state(), self.db.query('SELECT * FROM ops_common ORDER BY kind,item_id'), self.db.query(
            'SELECT * FROM achievement ORDER BY achievement_id')

    def next_run(self):
        self.assertEqual(self.call(88306, {}, 88307)[0]['result'], 0)
        self.assertEqual(self.svc.start_run(UID, 100001, 1, [dict(hero_id=1084)], [])[0], 0)

    def progress(self, aid):
        return self.db.get('achievement', UID, 'AND achievement_id=?', (aid,))

    def test_tactic_contract_exact_native_and_six_camp_mapping(self):
        from export_rogueteam_tactics_contract import export
        self.assertEqual(tactics.CONTRACT, export())
        self.assertEqual(tactics.CONTRACT['threshold'], 6)
        self.assertEqual(len(tactics.CONTRACT['programs']), 105)
        self.assertEqual(len(tactics.CONTRACT['tactics']), 6)
        self.assertEqual(len(tactics.CONTRACT['achievements']), 6)
        for key, cfg in tactics.CONTRACT['achievements'].items():
            for field in ('condition', 'parameter', 'need'):
                self.assertEqual(achievement._CFG[int(key)][field], cfg[field])

    def test_tactic_sixth_distinct_program_activates_and_progress_is_partial(self):
        self.seed(1, count=4)
        self.offer(self.programs(1)[4])
        response = self.ok(88200, event_id=1, param_arg=1)[1]
        self.assertNotIn(88211, response.scs)
        response = self.gain(1)
        self.assertEqual(self.packets(response, 88211), [dict(id=1, value=0)])
        self.assertIn(1500101, [item['id'] for item in json.loads(self.saved()['other_item_list_json'])])
        updates = [item for packet in self.packets(response, 88205) for item in packet['update_list']]
        self.assertIn(1500101, [item['id'] for item in updates])
        self.assertEqual(self.progress(63011)['progress'], 1)
        self.assertEqual(self.progress(63011)['achieve_time'], 0)
        self.assertIn(53003, response.scs)
        self.assertNotIn(53001, response.scs)
        self.assertLess(response.scs.index(53003), response.scs.index(88201))

    def test_tactic_one_per_run_and_no_other_camp_or_duplicate_refresh_counts(self):
        self.gain(1)
        response = self.gain(2)
        self.assertNotIn(88211, response.scs)
        self.assertEqual(tactics.state_packet(self.saved())['id'], 1)
        self.assertEqual(tactics._counts(self.db, UID), {'1': 1})
        self.offer(self.programs(1)[5])
        response = self.ok(88200, event_id=1, param_arg=1)[1]
        self.assertNotIn(53003, response.scs)
        self.assertEqual(tactics._counts(self.db, UID), {'1': 1})
        before = self.snap()
        self.rejected(88200, event_id=1, param_arg=1)
        self.assertEqual(self.snap(), before)

    def test_tactic_three_runs_complete_then_manual_reward(self):
        for index in range(1, 4):
            self.gain(1)
            self.assertEqual(self.progress(63011)['progress'], index)
            if index < 3:
                self.next_run()
        self.assertGreater(self.progress(63011)['achieve_time'], 0)
        self.db.set_currency(UID, 1, 0)
        self.assertEqual(self.call(53004, dict(id=[63011]), 53005)[0]['result'], 0)
        self.assertEqual(self.balance(1), 10)
        self.assertNotEqual(self.call(53004, dict(id=[63011]), 53005)[0]['result'], 0)
        self.assertEqual(self.balance(1), 10)

    def test_tactic_all_six_families_route_to_correct_achievement(self):
        expected = {1: 63011, 2: 63023, 3: 63012, 4: 63013, 5: 63014, 6: 63015}
        for camp, aid in expected.items():
            response = self.gain(camp)
            self.assertEqual(self.packets(response, 88211), [dict(id=camp, value=0)])
            self.assertEqual(self.progress(aid)['progress'], 1)
            self.next_run()

    def test_tactic_no_legacy_bag_or_login_or_rank_upgrade_fabricates_history(self):
        self.seed(1, count=6)
        self.ok(88030, type=0)
        self.assertEqual(tactics._counts(self.db, UID), {})
        self.assertEqual(tactics.state_packet(self.saved()), dict(id=0, value=0))
        self.offer(self.programs(1)[0])
        self.ok(88200, event_id=1, param_arg=1)
        self.assertEqual(tactics._counts(self.db, UID), {})

    def test_tactic_required_state_delta_and_ack_faults_roll_back_everything(self):
        self.seed(1)
        self.offer(self.programs(1)[5])
        before = self.snap()
        ctx = self.conn.to_handler_ctx()
        encode = ctx.codec_encode
        for schema in ('sc_88211', 'sc_88205', 'sc_53003', 'sc_88201'):
            with patch.object(ctx, 'codec_encode', side_effect=lambda name, data, schema=schema:
                              None if name == schema else encode(name, data)):
                self.assertNotEqual(self.call(88200, dict(event_id=1, param_arg=1), 88201)[0]['result'], 0)
            self.assertEqual(self.snap(), before, schema)
        self.ok(88200, event_id=1, param_arg=1)
        self.assertEqual(tactics._counts(self.db, UID), {'1': 1})

    def test_tactic_resume_and_serialized_login_restore_without_counting(self):
        self.gain(3)
        before = self.snap()
        response = self.ok(88030, type=0)[1]
        self.assertEqual(self.packets(response, 88211), [dict(id=3, value=0)])
        self.assertLess(response.scs.index(88211), response.scs.index(88031))
        packets = self.flood(fresh=True, captured=True)
        self.assertEqual([obj for cmd, obj, raw in packets if cmd == 88211], [dict(id=3, value=0)])
        self.assertLess([cmd for cmd, obj, raw in packets].index(88211), [cmd for cmd, obj, raw in packets].index(10201))
        self.assertEqual(self.snap(), before)

    def test_tactic_login_recovers_missing_progress_from_its_own_counter(self):
        self.gain(2)
        self.db.execute('UPDATE achievement SET progress=0,achieve_time=0 WHERE uid=? AND achievement_id=63023', (UID,))
        self.ach.get_achievement_login_payload(UID)
        self.assertEqual(self.progress(63023)['progress'], 1)
        self.assertEqual(self.progress(63023)['achieve_time'], 0)

    def test_tactic_simultaneous_camps_follow_configuration_order(self):
        programs = tactics.CONTRACT['programs']
        common = next(cfg['id'] for cfg in programs.values() if 1 in cfg['camps'] and 2 in cfg['camps'])
        owned = []
        for camp in (1, 2):
            mono = [cfg['id'] for cfg in programs.values() if cfg['camps'] == [camp]][:5]
            self.assertEqual(len(mono), 5)
            owned.extend(dict(id=iid, rare=1) for iid in mono)
        self.set(treasure_list_json=json.dumps(owned))
        self.offer(common)
        response = self.ok(88200, event_id=1, param_arg=1)[1]
        self.assertEqual(self.packets(response, 88211), [dict(id=1, value=0)])
        self.assertEqual(tactics._counts(self.db, UID), {'1': 1})

    def test_tactic_cold_login_required_state_failure_preserves_durable_run(self):
        import rogueteam_login as recovery
        self.gain(4)
        before = self.snap()
        encode = recovery.encode
        with patch.object(recovery, 'encode', side_effect=lambda schema, data:
                          None if schema == 'sc_88211' else encode(schema, data)):
            from core import OperationError
            with self.assertRaises(OperationError):
                self.flood(fresh=True, captured=True)
        self.assertEqual(self.snap(), before)


if __name__ == '__main__':
    names = [name for name in dir(TacticTests) if name.startswith('test_tactic_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(TacticTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
