"""Native UDP records -> real Core settlement -> durable Rogue facts/deltas."""
import json
import unittest
from unittest.mock import patch

from verify_report_small_fixes import UID
from verify_rogueteam_battle import RogueBattleTests
from verify_rogueteam_transition import TransitionTests
from verify_rogueteam_tactics import TacticTests
import achievement_service as achievements
import battle_server as udp
import middleware as mw
import rogueteam_flow as flow
import rogueteam_tactics as tactics
import rogueteam_battle_facts as facts


class ReportTests(RogueBattleTests):
    programs, seed, offer, gain = TacticTests.programs, TacticTests.seed, TacticTests.offer, TacticTests.gain
    reward_window, map = TransitionTests.reward_window, TransitionTests.map
    ok, op = TransitionTests.ok, TransitionTests.op

    def setUp(self):
        super().setUp()
        self.ach = achievements.AchievementService(self.db)
        self.ach.init_user_achievements(UID)
        self.gain(1)
        self.simple()

    def snapshot(self):
        return self.state(), self.db.query('SELECT * FROM ops_common ORDER BY uid,kind,item_id'), self.db.query(
            'SELECT * FROM achievement ORDER BY uid,achievement_id')

    def progress(self):
        return self.db.get('achievement', UID, 'AND achievement_id=63016')

    def finish_records(self, ids=(), counts=(), *, result=1, bid=None, packed=False, enemies=99):
        bid = self.bid if bid is None else bid
        info = udp._f_varint2(1, int(result == 1)) + udp._f_varint2(2, 45000) + udp._f_varint2(11, enemies)
        if packed:
            # Native repeated int32 accepts both packed and unpacked forms.
            info += udp._f_bytes(14, b''.join(udp._varint(i) for i in ids))
            info += udp._f_bytes(15, b''.join(udp._varint(i) for i in counts))
        else:
            info += b''.join(udp._f_varint2(14, i) for i in ids)
            info += b''.join(udp._f_varint2(15, i) for i in counts)
        raw = udp._f_varint2(1, bid) + udp._f_varint2(2, UID) + udp._f_varint2(3, result) + udp._f_bytes(4, info)
        got, owner, outcome, report = udp.parse_battle_result(raw)
        self.assertEqual((got, owner, outcome), (bid, UID, result))
        mw.get_battle_session(bid)['result_data'] = dict(result=outcome, info=report)
        return self.call(54032, dict(battle_id=bid), 54033)

    def enter_ok(self):
        self.assertEqual(self.enter()[0]['result'], 0)

    def test_report_contract_exact_native_record_names_and_wire(self):
        from export_rogueteam_battle_facts import export
        self.assertEqual(facts.CONTRACT, export())
        self.assertEqual(facts.CONTRACT['records']['80']['name'], '新多维大招充能继承用')
        self.assertEqual(facts.CONTRACT['records']['88']['name'], '1次流派大招击杀数')
        self.assertEqual(facts.CONTRACT['wire'], dict(record_ids=14, record_numbers=15))
        native = facts.CONTRACT['achievement']
        for key in ('condition', 'need', 'parameter'):
            self.assertEqual(achievements._CFG[63016][key], native[key])

    def test_report_udp_energy_and_single_cast_five_produce_native_deltas(self):
        self.enter_ok()
        session = mw.get_battle_session(self.bid)
        self.assertEqual(session['rogue_tactic_camp'], 1)
        self.assertTrue(session['rogue_report_token'])
        packet, response = self.finish_records([80, 88], [73, 5])
        self.assertEqual(packet['result'], 0)
        self.assertEqual(tactics.state_packet(self.saved()), dict(id=1, value=73))
        self.assertEqual(self.packets(response, 88211), [dict(id=1, value=73)])
        self.assertEqual(self.progress()['progress'], 1)
        self.assertIn(63016, [item['id'] for obj in self.packets(response, 53003) for item in obj['achievement_progress']])
        self.assertNotIn(53001, response.scs)
        self.assertLess(response.scs.index(88211), response.scs.index(54033))
        self.assertEqual(facts.evidence(self.db, UID), 5)

    def test_report_explicit_zero_spending_and_missing_value_keep_remaining_energy(self):
        self.enter_ok()
        self.assertEqual(self.finish_records([80], [73], result=2, packed=True)[0]['result'], 0)
        self.enter_ok()
        packet, response = self.finish_records([88], [4], result=2)
        self.assertEqual(packet['result'], 0)
        self.assertNotIn(88211, response.scs)
        self.assertEqual(tactics.state_packet(self.saved())['value'], 73)
        self.enter_ok()
        packet, response = self.finish_records([80], [0], result=3, packed=True)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.packets(response, 88211), [dict(id=1, value=0)])
        self.assertEqual(tactics.state_packet(self.saved())['value'], 0)

    def test_report_total_and_multiple_casts_do_not_fake_single_cast(self):
        for _ in range(2):
            self.enter_ok()
            packet, _ = self.finish_records([88, 82], [3, 99], result=2, enemies=9999)
            self.assertEqual(packet['result'], 0)
            self.assertEqual(self.progress()['progress'], 0)
        self.assertEqual(facts.evidence(self.db, UID), 3)
        self.enter_ok()
        self.finish_records([], [], result=2, enemies=99999)
        self.assertEqual(self.progress()['progress'], 0)

    def test_report_failure_or_quit_can_prove_cast_without_claiming_stage_victory(self):
        for result in (2, 3):
            self.enter_ok()
            node = self.saved()['map_info_json']
            packet, _ = self.finish_records([80, 88], [21, 5], result=result)
            self.assertEqual(packet['result'], 0)
            self.assertEqual(self.saved()['map_info_json'], node)
            self.assertEqual(self.progress()['progress'], 1)
            self.assertEqual(self.db.get('achievement', UID, 'AND achievement_id=63001')['progress'], 0)

    def test_report_invalid_duplicate_negative_or_mismatched_lists_roll_back(self):
        self.enter_ok()
        before = self.snapshot()
        for ids, values in (([80, 88], [20]), ([80, 80], [20, 30]),
                            ([88, 88], [2, 3]), ([80], [0xffffffff]), ([88], [0xffffffff])):
            packet, _ = self.finish_records(ids, values)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(self.snapshot(), before)
            self.assertNotIn('br', mw.get_battle_session(self.bid))

    def test_report_old_attempt_and_wrong_entry_camp_do_not_update_facts(self):
        self.enter_ok()
        old = self.bid
        self.enter_ok()
        before = self.snapshot()
        self.assertNotEqual(self.finish_records([80, 88], [99, 5], bid=old)[0]['result'], 0)
        self.assertEqual(self.snapshot(), before)
        session = mw.get_battle_session(self.bid)
        good = session['rogue_tactic_camp']
        session['rogue_tactic_camp'] = 2
        self.assertNotEqual(self.finish_records([80, 88], [99, 5])[0]['result'], 0)
        self.assertEqual(self.snapshot(), before)
        session['rogue_tactic_camp'] = good
        self.assertEqual(self.finish_records([80], [42])[0]['result'], 0)

    def test_report_required_frames_and_receipt_failures_roll_back_and_retry(self):
        self.enter_ok()
        before = self.snapshot()
        encode = self.ctx.codec_encode
        for schema in ('sc_88211', 'sc_53003', 'sc_54033'):
            with patch.object(self.ctx, 'codec_encode', side_effect=lambda name, *args, schema=schema, **kwargs:
                              None if name == schema else encode(name, *args, **kwargs)):
                packet, _ = self.finish_records([80, 88], [80, 5])
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(self.snapshot(), before)
            self.assertNotIn('br', mw.get_battle_session(self.bid))
        self.assertEqual(self.finish_records([80, 88], [80, 5])[0]['result'], 0)

    def test_report_cached_reply_does_not_consume_changed_report_twice(self):
        self.enter_ok()
        packet, _ = self.finish_records([80, 88], [41, 5])
        before = self.snapshot()
        again, response = self.finish_records([80, 88], [999, 9])
        self.assertEqual(packet, again)
        self.assertEqual(response.scs, [54033])
        self.assertEqual(self.snapshot(), before)

    def test_report_relogin_recovers_energy_and_achievement_without_rewards(self):
        self.enter_ok()
        self.finish_records([80, 88], [67, 5], result=2)
        before = self.snapshot()
        packet, response = self.call(88030, dict(type=0), 88031)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.packets(response, 88211), [dict(id=1, value=67)])
        self.assertEqual(self.snapshot(), before)
        self.db.execute('UPDATE achievement SET progress=0,achieve_time=0 WHERE uid=? AND achievement_id=63016', (UID,))
        self.ach.get_achievement_login_payload(UID)
        self.assertEqual(self.progress()['progress'], 1)
        balance = self.balance(1)
        self.assertEqual(self.call(53004, dict(id=[63016]), 53005)[0]['result'], 0)
        self.assertEqual(self.balance(1), balance + 10)

    def test_report_without_active_tactic_never_proves_a_cast(self):
        meta = flow.flow(self.saved())
        meta.pop('tactic_v1')
        flow.save_flow(self.svc, UID, 100001, meta)
        self.enter_ok()
        self.assertEqual(self.finish_records([80, 88], [99, 5], result=2)[0]['result'], 0)
        self.assertEqual(self.progress()['progress'], 0)
        self.assertEqual(tactics.state_packet(self.saved()), dict(id=0, value=0))


if __name__ == '__main__':
    names = [name for name in dir(ReportTests) if name.startswith('test_report_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ReportTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
