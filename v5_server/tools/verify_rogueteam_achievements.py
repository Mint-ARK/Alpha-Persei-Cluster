"""Codex acceptance: actual Rogue Core battles, memory SQL and codec."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'v5_server'), str(ROOT / 'v5_server/tools')]
from verify_report_small_fixes import UID
from verify_rogueteam_battle import RogueBattleTests
import achievement_service as achievements
import codec
import rogueteam_achievement as projection


class IndependentTests(RogueBattleTests):
    def setUp(self):
        super().setUp()
        self.ach = achievements.AchievementService(self.db)
        self.ach.init_user_achievements(UID)

    def achievements(self):
        return self.db.query('SELECT * FROM achievement WHERE uid=? ORDER BY achievement_id', (UID,))

    def progress(self, aid):
        return self.db.get('achievement', UID, 'AND achievement_id=?', (aid,))['progress']

    def all_state(self):
        return self.state(), self.achievements(), self.db.query('SELECT * FROM ops_common ORDER BY uid,kind,item_id')

    def prepare_terminal(self):
        self.simple(terminal=True)
        self.assertEqual(self.enter()[0]['result'], 0)
        self.assertEqual(self.finish()[0]['result'], 0)
        self.assertEqual(self.call(88008, dict(sign=1), 88009)[0]['result'], 0)
        self.assertEqual(self.call(88218, {}, 88219)[0]['result'], 0)

    def test_review_win_and_repeat_use_delta(self):
        self.prepare_terminal()
        packet, response = self.call(88306, {}, 88307)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.progress(63001), 1)
        self.assertEqual(self.progress(63022), 1)
        self.assertEqual(self.progress(63002), 0)
        self.assertIn(53003, response.scs)
        self.assertNotIn(53001, response.scs)
        records = self.achievements()
        _, repeat = self.call(88306, {}, 88307)
        self.assertEqual(self.achievements(), records)
        self.assertNotIn(53003, repeat.scs)

    def test_review_failure_never_proves_win(self):
        before = self.achievements()
        self.assertEqual(self.enter()[0]['result'], 0)
        self.assertEqual(self.finish(result=2)[0]['result'], 0)
        self.call(88306, {}, 88307)
        self.assertEqual(self.achievements(), before)

    def test_review_required_delta_failure_rolls_back(self):
        self.prepare_terminal()
        before = self.all_state()
        encode = self.ctx.codec_encode
        with patch.object(self.ctx, 'codec_encode', side_effect=lambda schema, data: b'' if schema == 'sc_53003' else encode(schema, data)):
            packet, response = self.call(88306, {}, 88307)
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(self.all_state(), before)
        self.assertNotIn(53003, response.scs)
        self.assertEqual(self.call(88306, {}, 88307)[0]['result'], 0)

    def test_review_native_counts_at_terminal(self):
        relics = [int(k) for k, item in self.svc.config.items.items() if item['type'] == 3 and item['sub_type'] == 1][:10]
        programs = [int(k) for k, item in self.svc.config.items.items() if item['type'] == 4][:20]
        self.assertEqual((len(relics), len(programs)), (10, 20))
        self.set(other_item_list_json=json.dumps([dict(id=i) for i in relics]),
                 treasure_list_json=json.dumps([dict(id=i, rare=1) for i in programs]))
        self.prepare_terminal()
        packet, _ = self.call(88306, {}, 88307)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.progress(63009), 1)
        self.assertEqual(self.progress(63010), 1)

    def test_review_contract_reproduces_native_and_runtime_metadata(self):
        from export_rogueteam_achievement_contract import export
        self.assertEqual(projection.CONTRACT, export())
        self.assertEqual(len(projection.CONTRACT['achievements']), 10)
        self.assertEqual(len(projection.CONTRACT['items']), 169)
        for key, native in projection.CONTRACT['achievements'].items():
            runtime = achievements._CFG[int(key)]
            for field in ('id', 'condition', 'parameter', 'need'):
                self.assertEqual(runtime[field], native[field], (key, field))

    def test_review_counts_reject_wrong_bag_unknown_id_bool_and_duplicates(self):
        items = projection.CONTRACT['items']
        relic = next(int(k) for k, cfg in items.items() if cfg['type'] == 3)
        negative = next(int(k) for k, cfg in items.items() if cfg['type'] == 3 and cfg['sub_type'] == 2)
        story = next(int(k) for k, cfg in items.items() if cfg['type'] == 3 and cfg['sub_type'] == 3)
        program = next(int(k) for k, cfg in items.items() if cfg['type'] == 4)
        counts = projection.held_counts(dict(other_item_list_json=json.dumps([
            relic, dict(id=relic, num=100), negative, story, program, 139999999, True]),
            treasure_list_json=json.dumps([dict(id=program, rare=3, num=99),
                                         dict(id=program), dict(id=relic), dict(id=True)])))
        self.assertEqual(counts['relics'], 3)
        self.assertEqual(counts['program_total'], 1)
        self.assertEqual(sum(counts['programs'].values()), 1)

    def test_review_below_item_threshold_still_allows_valid_win(self):
        relics = [int(k) for k, cfg in projection.CONTRACT['items'].items() if cfg['type'] == 3][:9]
        programs = [int(k) for k, cfg in projection.CONTRACT['items'].items() if cfg['type'] == 4][:19]
        self.set(other_item_list_json=json.dumps(relics),
                 treasure_list_json=json.dumps([dict(id=i, rare=3) for i in programs]))
        self.prepare_terminal()
        self.assertEqual(self.call(88306, {}, 88307)[0]['result'], 0)
        self.assertEqual(self.progress(63001), 1)
        self.assertEqual(self.progress(63009), 0)
        self.assertEqual(self.progress(63010), 0)

    def test_review_login_uses_own_history_not_unlock_level_or_current_bag(self):
        self.db.upsert('rogueteam_user', UID, dict(template_id=100001, max_difficult=4), keys=('uid', 'template_id'))
        self.ach.get_achievement_login_payload(UID)
        self.assertEqual(self.progress(63004), 0)
        self.db.upsert('rogueteam_history', UID, dict(template_id=100001,
            diff_clear_json=json.dumps([dict(key=4, value=3)]),
            ending_pass_json=json.dumps([dict(key=2, value=1)])), keys=('uid', 'template_id'))
        balance = self.balance(1)
        payload = self.ach.get_achievement_login_payload(UID)
        self.assertTrue(codec.decode(payload, 'sc_53001')['achievement_list'])
        self.assertEqual(self.progress(63004), 1)
        self.assertEqual(self.progress(63001), 0)
        self.assertEqual(self.progress(63007), 1)
        self.assertEqual(self.progress(63009), 0)
        self.assertEqual(self.progress(63010), 0)
        self.assertEqual(self.balance(1), balance)

    def test_review_claimed_history_is_preserved_and_reward_is_manual(self):
        self.prepare_terminal()
        self.db.execute('UPDATE achievement SET progress=1,complete_flag=1,achieve_time=123456 '
                        'WHERE uid=? AND achievement_id=63022', (UID,))
        old = self.db.get('achievement', UID, 'AND achievement_id=63022')
        balance = self.balance(1)
        self.assertEqual(self.call(88306, {}, 88307)[0]['result'], 0)
        self.assertEqual(self.balance(1), balance)
        self.assertEqual(self.db.get('achievement', UID, 'AND achievement_id=63022'), old)
        packet, _ = self.call(53004, dict(id=[63001]), 53005)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.balance(1), balance + 10)
        self.assertNotEqual(self.call(53004, dict(id=[63001]), 53005)[0]['result'], 0)
        self.assertEqual(self.balance(1), balance + 10)

    def test_review_account_and_database_isolation(self):
        from account_db import AccountDB
        self.prepare_terminal()
        self.assertEqual(self.call(88306, {}, 88307)[0]['result'], 0)
        other = AccountDB.new_save_workspace()
        self.addCleanup(other.close)
        other.execute('INSERT INTO users(uid,nick) VALUES(?,?)', (UID, 'other'))
        other.execute('INSERT INTO achievement(uid,achievement_id,need) VALUES(?,?,?)', (UID, 63001, 1))
        projection.reconcile(other, UID)
        self.assertEqual(other.get('achievement', UID, 'AND achievement_id=63001')['progress'], 0)
        self.db.execute('INSERT INTO users(uid,nick) VALUES(?,?)', (UID + 1, 'other uid'))
        projection.reconcile(self.db, UID + 1)
        self.assertFalse(self.db.query('SELECT * FROM achievement WHERE uid=?', (UID + 1,)))

    def test_review_ack_and_delta_failure_roll_back_receipt_history_and_currency(self):
        self.prepare_terminal()
        before = self.all_state()
        encode = self.ctx.codec_encode
        for schema, invalid in (('sc_53003', None), ('sc_88307', None)):
            with patch.object(self.ctx, 'codec_encode', side_effect=lambda name, data, schema=schema, invalid=invalid:
                              invalid if name == schema else encode(name, data)):
                packet, response = self.call(88306, {}, 88307)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(self.all_state(), before)
            self.assertNotIn(53003, response.scs)
        self.assertEqual(self.call(88306, {}, 88307)[0]['result'], 0)

    def test_review_held_receipt_survives_new_run_for_recovery(self):
        relics = [int(k) for k, cfg in projection.CONTRACT['items'].items() if cfg['type'] == 3][:10]
        self.set(other_item_list_json=json.dumps(relics))
        self.prepare_terminal()
        self.assertEqual(self.call(88306, {}, 88307)[0]['result'], 0)
        self.assertEqual(self.svc.start_run(UID, 100001, 1, [dict(hero_id=1084)], [])[0], 0)
        # Isolated missing projection: only the new audited victory receipt
        # may recover this threshold; a current run's inventory cannot.
        self.db.execute('UPDATE achievement SET progress=0,achieve_time=0 WHERE uid=? AND achievement_id=63009', (UID,))
        self.ach.get_achievement_login_payload(UID)
        self.assertEqual(self.progress(63009), 1)


if __name__ == '__main__':
    names = [name for name in dir(IndependentTests) if name.startswith('test_review_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(IndependentTests(name) for name in names))
    sys.exit(not result.wasSuccessful())
