"""A250: native version/phase/claimed-task facts and atomic ordinary rewards."""
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID, NOW
import codec
import generator
from newbie_progress_reward_service import contract


class ProgressRewardTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock = patch('time.time', return_value=NOW); clock.start(); self.addCleanup(clock.stop)
        self.db.get_or_create_newbie_activity(UID)
        self.db.execute('UPDATE newbie_activity SET trigger_time=?,completed_time=?,version_id=3,max_phase=7 WHERE uid=?',
                        (NOW-60, NOW+86400, UID))
        self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(38,1)')

    def ids(self, kind=603, phase=None):
        return sorted(int(tid) for tid, row in contract()['tasks'].items()
                      if row['task_type']==kind and (phase is None or row['phase']==phase))

    def task(self, tid, kind=603, flag=1, claimed=NOW):
        self.db.upsert('task', UID, dict(task_id=tid, task_type=kind, progress=999,
                       complete_flag=flag, claimed_ts=claimed), keys=('uid', 'task_id'))

    def claim(self, index=1):
        return self.call(59014, {'id': index}, 59015)

    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('newbie_activity', 'newbie_pt_reward', 'task', 'currency', 'material')}

    def test_export_matches_current_native_versions_and_assignment_catalog(self):
        from export_newbie_reward_contract import build
        self.assertEqual(contract(), build())
        self.assertEqual(contract()['versions']['3']['progress_thresholds'], [10,20,30,45,60,75,90])
        self.assertEqual(contract()['versions']['2']['progress_thresholds'][0], 15)

    def test_nine_ten_claimed_boundary_once_custom_assets_and_cold_state(self):
        ids = self.ids()
        for tid in ids[:9]:
            self.task(tid)
        before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.task(ids[9]); packet, response = self.claim()
        self.assertEqual(packet['result'], 0)
        self.assertEqual(packet['reward_list'], [dict(id=38, num=2)])
        self.assertEqual(self.balance(38), 2); self.assertEqual(response.scs, [17023,59015])
        delta = codec.decode(response.frames[0]['payload'], 'sc_17023')
        self.assertEqual(next(item['num'] for item in delta['normal_items'] if item['id']==38), 2)
        before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)
        cold = codec.decode(generator.gen_payload(59011, uid=UID, db=self.db), 'sc_59011')
        self.assertEqual(cold['got_pt_id_list'], [1])

    def test_every_current_tier_uses_its_threshold_and_one_receipt(self):
        ids = self.ids()
        for index, threshold in enumerate(contract()['versions']['3']['progress_thresholds'], 1):
            for tid in ids[:threshold-1]:
                self.task(tid)
            self.assertNotEqual(self.claim(index)[0]['result'], 0)
            self.task(ids[threshold-1]); self.assertEqual(self.claim(index)[0]['result'], 0)
        self.assertEqual(self.balance(38), 14)
        before = self.state()
        for index in (0, 8, 99, self.ids()[0]):
            self.assertNotEqual(self.claim(index)[0]['result'], 0)
            self.assertEqual(self.state(), before)

    def test_only_native_current_type_unlocked_phase_and_complete_flag_count(self):
        first = self.ids(phase=1); second = self.ids(phase=2)
        self.db.execute('UPDATE newbie_activity SET max_phase=1 WHERE uid=?', (UID,))
        for tid in first[:9]:
            self.task(tid)
        self.task(first[9], flag=0, claimed=NOW)  # Progress/receipt timestamp alone is not the Lua flag.
        self.task(second[0])
        self.task(self.ids(kind=601)[0], kind=603)  # Wrong catalog identity, despite SQL type.
        self.task(99999999)
        before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.db.execute('UPDATE newbie_activity SET max_phase=2 WHERE uid=?', (UID,))
        self.assertEqual(self.claim()[0]['result'], 0)

    def test_version_two_uses_601_and_fifteen_not_version_three_rules(self):
        self.db.execute('UPDATE newbie_activity SET version_id=2 WHERE uid=?', (UID,))
        for tid in self.ids()[:20]:
            self.task(tid)
        self.assertNotEqual(self.claim()[0]['result'], 0)
        ids = self.ids(kind=601)
        for tid in ids[:14]:
            self.task(tid, kind=601)
        self.assertNotEqual(self.claim()[0]['result'], 0)
        self.task(ids[14], kind=601)
        self.assertEqual(self.claim()[0]['result'], 0)
        self.assertEqual(self.balance(38), 2)

    def test_inactive_or_no_progress_catalog_never_activates_or_grants(self):
        for tid in self.ids()[:100]:
            self.task(tid)
        for version, trigger, end in ((1,NOW-60,NOW+86400), (999,NOW-60,NOW+86400),
                                      (3,0,NOW+86400), (3,NOW+1,NOW+86400), (3,NOW-60,NOW)):
            self.db.execute('UPDATE newbie_activity SET version_id=?,trigger_time=?,completed_time=? WHERE uid=?',
                            (version,trigger,end,UID))
            before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.db.execute('DELETE FROM newbie_activity WHERE uid=?', (UID,))
        before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)

    def test_receipt_asset_or_required_packet_failure_rolls_back_and_can_retry(self):
        for tid in self.ids()[:10]:
            self.task(tid)
        for target in ('receipt', 'currency', 'ack', 'delta'):
            before = self.state()
            if target in ('receipt', 'currency'):
                real = self.db.execute
                def fail(sql, params=()):
                    result = real(sql, params)
                    if ((target=='receipt' and sql.startswith('INSERT INTO newbie_pt_reward'))
                            or (target=='currency' and 'currency' in sql and sql.lstrip().upper().startswith(('INSERT','UPDATE')))):
                        raise RuntimeError('after isolated '+target+' write')
                    return result
                fault = patch.object(self.db, 'execute', side_effect=fail)
            elif target == 'ack':
                ctx = self.conn.to_handler_ctx(); real = ctx.codec_encode
                fault = patch.object(ctx, 'codec_encode', side_effect=lambda schema,*a,**kw:
                                     None if schema=='sc_59015' else real(schema,*a,**kw))
            else:
                real = generator.gen_payload
                fault = patch.object(generator, 'gen_payload', side_effect=lambda command,*a,**kw:
                                     None if command==17023 else real(command,*a,**kw))
            with fault:
                packet, response = self.claim()
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [59015]); self.assertEqual(self.state(), before)
        self.assertEqual(self.claim()[0]['result'], 0); self.assertEqual(self.balance(38), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
