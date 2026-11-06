"""A249 ordinary-save level rewards through actual registered Core operations."""
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
import codec
import generator


class LevelRewardTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock = patch('time.time', return_value=NOW); clock.start(); self.addCleanup(clock.stop)
        self.db.get_or_create_newbie_activity(UID)
        self.db.execute('UPDATE newbie_activity SET trigger_time=?,completed_time=? WHERE uid=?', (NOW-60, NOW+86400, UID))
        self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(1,1)')

    def claim(self, level):
        return self.call(59004, {'level': level}, 59005)

    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('users', 'newbie_activity', 'newbie_level_reward', 'currency', 'material')}

    def set_level(self, value):
        self.db.execute('UPDATE users SET level=? WHERE uid=?', (value, UID))

    def test_current_slots_level_threshold_and_once_keep_custom_amount(self):
        for level in (20, 30, 40, 50, 60):
            self.set_level(level-1); before = self.state()
            self.assertNotEqual(self.claim(level)[0]['result'], 0)
            self.assertEqual(self.state(), before)
            self.set_level(level); before_num = self.balance(1)
            packet, response = self.claim(level)
            self.assertEqual(packet['result'], 0)
            self.assertEqual(packet['reward_list'], [dict(id=1, num=200)])
            self.assertEqual(self.balance(1), before_num+200)
            self.assertEqual(response.scs, [17023, 59005])
            delta = codec.decode(response.frames[0]['payload'], 'sc_17023')
            self.assertEqual(next(r['num'] for r in delta['normal_items'] if r['id']==1), self.balance(1))
            before_repeat = self.state(); packet, response = self.claim(level)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [59005]); self.assertEqual(self.state(), before_repeat)
        cold = codec.decode(generator.gen_payload(59011, uid=UID, db=self.db), 'sc_59011')
        self.assertEqual(cold['newbie_level_reward']['received_level_list'], [20,30,40,50,60])

    def test_unknown_slots_cannot_grant_or_write_history(self):
        self.set_level(100)
        before = self.state()
        for level in (0, 10, 21, 999):
            self.assertNotEqual(self.claim(level)[0]['result'], 0)
            self.assertEqual(self.state(), before)

    def test_missing_unstarted_future_or_expired_activity_cannot_grant(self):
        self.set_level(20)
        for trigger, completed in ((0, NOW+86400), (NOW+1, NOW+86400), (NOW-60, NOW), (NOW-60, NOW-1)):
            self.db.execute('UPDATE newbie_activity SET trigger_time=?,completed_time=? WHERE uid=?', (trigger, completed, UID))
            before = self.state()
            self.assertNotEqual(self.claim(20)[0]['result'], 0)
            self.assertEqual(self.state(), before)
        self.db.execute('DELETE FROM newbie_activity WHERE uid=?', (UID,))
        before = self.state()
        self.assertNotEqual(self.claim(20)[0]['result'], 0)
        self.assertEqual(self.state(), before)

    def test_native_zero_completion_sentinel_and_old_unknown_history_are_preserved(self):
        self.set_level(20)
        self.db.execute('UPDATE newbie_activity SET completed_time=0 WHERE uid=?', (UID,))
        self.db.execute('INSERT INTO newbie_level_reward(uid,level,update_ts) VALUES(?,?,?)', (UID,999,NOW-1))
        self.assertEqual(self.claim(20)[0]['result'], 0)
        old = self.db.query('SELECT * FROM newbie_level_reward WHERE uid=? AND level=999', (UID,))
        self.assertEqual(old[0]['update_ts'], NOW-1)
        self.assertEqual(self.balance(1), 200)

    def test_ledger_or_asset_write_failure_rolls_back_and_retries_once(self):
        self.set_level(20)
        for target in ('ledger', 'currency'):
            before = self.state(); real = self.db.execute
            def fail(sql, params=()):
                result = real(sql, params)
                if ((target == 'ledger' and sql.startswith('INSERT INTO newbie_level_reward'))
                        or (target == 'currency' and 'currency' in sql and sql.lstrip().upper().startswith(('INSERT','UPDATE')))):
                    raise RuntimeError('after isolated '+target+' write')
                return result
            with patch.object(self.db, 'execute', side_effect=fail):
                packet, response = self.claim(20)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [59005]); self.assertEqual(self.state(), before)
        self.assertEqual(self.claim(20)[0]['result'], 0)
        self.assertEqual(self.balance(1), 200)

    def test_required_ack_or_asset_encoding_failure_rolls_back_history_and_grant(self):
        self.set_level(20)
        for target in ('ack', 'delta'):
            before = self.state()
            if target == 'ack':
                ctx = self.conn.to_handler_ctx(); real = ctx.codec_encode
                fault = patch.object(ctx, 'codec_encode', side_effect=lambda schema, *a, **kw:
                                     None if schema == 'sc_59005' else real(schema, *a, **kw))
            else:
                real = generator.gen_payload
                fault = patch.object(generator, 'gen_payload', side_effect=lambda command, *a, **kw:
                                     None if command == 17023 else real(command, *a, **kw))
            with fault:
                packet, response = self.claim(20)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [59005]); self.assertEqual(self.state(), before)
        self.assertEqual(self.claim(20)[0]['result'], 0)
        self.assertEqual(self.balance(1), 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
