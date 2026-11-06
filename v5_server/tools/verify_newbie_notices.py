"""A254: real memory Core and codec; no reward/activation side effects."""
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import codec
import generator
import new_player_service
from newbie_notice_service import NOTICE_COLUMNS

WIRE = {
    'mc_new_role': ('first_monthly_card_reward', 'is_new_tag_role_reward'),
    'mc_new_sign': ('first_monthly_card_reward', 'is_new_tag_sign_reward'),
    'fr_new6': ('first_recharge_reward', 'is_new_recharge_6'),
    'fr_new18': ('first_recharge_reward', 'is_new_recharge_18'),
    'bp_new': ('first_battlepass_reward', 'is_new_battlepass_reward'),
}


class NoticeTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.get_or_create_newbie_activity(UID)
        self.seed()

    def seed(self):
        self.db.execute('UPDATE newbie_activity SET mc_new_role=1,mc_new_sign=1,fr_new6=1,fr_new18=1,bp_new=1 WHERE uid=?', (UID,))

    def call_tag(self, command):
        return self.call(command, {}, command + 1)

    def snapshot(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('newbie_activity', 'currency', 'material', 'newbie_level_reward', 'newbie_pt_reward')}

    def flags(self, payload):
        data = payload['newbie_recharge_reward']
        return {column: bool(data[group][key]) for column, (group, key) in WIRE.items()}

    def cold(self, command):
        return codec.decode(generator.gen_payload(command, uid=UID, db=self.db), 'sc_' + str(command))

    def test_each_native_command_clears_only_its_column_and_success_is_ack_only(self):
        for command, column in NOTICE_COLUMNS.items():
            with self.subTest(command=command):
                self.seed(); before = self.snapshot()
                packet, response = self.call_tag(command)
                self.assertEqual(packet['result'], 0)
                self.assertEqual(response.scs, [command + 1])
                after = self.snapshot()
                for table in before:
                    if table != 'newbie_activity':
                        self.assertEqual(after[table], before[table])
                row = after['newbie_activity'][0]
                self.assertEqual({c: int(row[c]) for c in WIRE}, {c: int(c != column) for c in WIRE})
                for key, value in before['newbie_activity'][0].items():
                    if key not in (column, 'update_ts'):
                        self.assertEqual(row[key], value)
                before_repeat = self.snapshot()
                self.assertEqual(self.call_tag(command)[0]['result'], 0)
                self.assertEqual(self.snapshot(), before_repeat)

    def test_bp_column_and_ordinary_new_save_cold_payloads_agree(self):
        self.assertTrue(self.flags(self.cold(59009))['bp_new'])
        self.assertEqual(self.flags(self.cold(59009)), self.flags(self.cold(59011)))
        for command, column in NOTICE_COLUMNS.items():
            self.assertEqual(self.call_tag(command)[0]['result'], 0)
            self.assertFalse(self.flags(self.cold(59009))[column])
            self.assertEqual(self.flags(self.cold(59009)), self.flags(self.cold(59011)))
        self.db.set_system_config('save.profile', new_player_service.PROFILE)
        self.db.set_system_config('save.owner_uid', str(UID))
        self.assertEqual(self.flags(self.cold(59011)), {column: False for column in WIRE})

    def test_after_write_failure_restores_sql_and_ack_then_state_restores_local_flag(self):
        real = self.db.execute
        for command, column in NOTICE_COLUMNS.items():
            before = self.snapshot()
            def fail(sql, params=()):
                result = real(sql, params)
                if sql.startswith('UPDATE newbie_activity SET ' + column + '=0'):
                    raise RuntimeError('after isolated tag write')
                return result
            with patch.object(self.db, 'execute', side_effect=fail):
                packet, response = self.call_tag(command)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [command + 1, 59009])
            self.assertEqual(self.snapshot(), before)
            # Native ACK callbacks clear unconditionally. The next59009 restores it.
            local = {c: True for c in WIRE}; local[column] = False
            local.update(self.flags(codec.decode(response.frames[1]['payload'], 'sc_59009')))
            self.assertTrue(local[column])

    def test_required_ack_encoding_failure_rolls_back_and_retry_persists(self):
        for command, column in NOTICE_COLUMNS.items():
            self.seed(); before = self.snapshot(); ctx = self.conn.to_handler_ctx(); real = ctx.codec_encode
            with patch.object(ctx, 'codec_encode', side_effect=lambda schema, *a, **kw:
                    None if schema == 'sc_' + str(command + 1) else real(schema, *a, **kw)):
                packet, response = self.call_tag(command)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [command + 1, 59009])
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.call_tag(command)[0]['result'], 0)
            self.assertFalse(self.flags(self.cold(59009))[column])

    def test_missing_activity_is_noop_and_never_reactivated(self):
        self.db.execute('DELETE FROM newbie_activity WHERE uid=?', (UID,))
        for command in NOTICE_COLUMNS:
            packet, response = self.call_tag(command)
            self.assertEqual(packet['result'], 0)
            self.assertEqual(response.scs, [command + 1])
            self.assertFalse(self.db.query('SELECT 1 FROM newbie_activity WHERE uid=?', (UID,)))

    def test_failed_recovery_encoding_leaves_durable_truth_for_later_cold_read(self):
        before = self.snapshot(); ctx = self.conn.to_handler_ctx(); real = ctx.codec_encode
        with patch.object(ctx, 'codec_encode', side_effect=lambda schema, *a, **kw:
                          None if schema in ('sc_59025', 'sc_59009') else real(schema, *a, **kw)):
            packet, response = self.call_tag(59024)
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(response.scs, [59025])
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.flags(self.cold(59009))['bp_new'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
