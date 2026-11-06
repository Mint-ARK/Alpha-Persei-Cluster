"""A267: real Core/paid settlement/codec, SQLite memory only; no real saves."""
import json
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
import codec
import generator
import paid_entitlement_service as paid


class BigAccumulateTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock = patch('time.time', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.db.execute('INSERT INTO game_user(uid) VALUES(?)', (UID,))
        for iid in (1, 14, 19, 38):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)', (iid,))
        for iid, _, _ in paid.BIG_POOL.values():
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,4)', (iid,))
        # Initialize unrelated weekly/small-card state in the memory fixture.
        # claim_big's real settle still runs, including legacy migration below.
        paid.settle(self.db, UID, NOW, notify=False)

    def seed(self, *, days=7, received=(201,), template=2, legacy=False):
        today = paid.get_daily_5am_ts(NOW)
        start = today - (days - 1) * paid.DAY
        record = [{'index': i + 1, 'times': min(5, days - i * 5)}
                  for i in range((days + 4) // 5)]
        paid._write_big(self.db, UID, dict(
            buy_timestamp=start-paid.DAY, is_sign=1, total_sign_times=days,
            total_sign_receive_list=json.dumps(list(received)),
            daily_record=json.dumps(record), template_id=template, is_expire_tip=0), NOW)
        state = paid.read_state(self.db, UID)
        for key in list(state):
            if key.startswith('big_'):
                state.pop(key)
        if not legacy:
            state.update(big_start=start, big_end=start+30*paid.DAY,
                         big_cursor=today, big_claim_day=today)
        paid._save(self.db, UID, state)
        return record

    def claim(self):
        return self.call(34110, {}, 34111)

    def snapshot(self):
        return tuple(self.db._conn().iterdump())

    def cold(self):
        return codec.decode(generator.gen_payload(34101, uid=UID, db=self.db), 'sc_34101')

    def states(self, response):
        return [codec.decode(f['payload'], 'sc_34101') for f in response.frames if f['sc'] == 34101]

    def test_legacy_signed_day7_claims_only_missing202_and_last_snapshot_is_final(self):
        record = self.seed(legacy=True)
        mail_count = len(self.db.query('SELECT * FROM mail'))
        packet, response = self.claim()
        self.assertEqual(packet['result'], 0)
        self.assertEqual(packet['rewards'], [{'id': 1, 'num': 200}])
        self.assertEqual(packet['total_sign_times'], 7)
        self.assertEqual(packet['total_sign_receive_list'], [201, 202])
        self.assertEqual(packet['daily_record'], record)
        self.assertEqual(packet['is_sign'], 1)
        self.assertEqual(self.balance(1), 200)
        self.assertFalse(self.db.query('SELECT * FROM material'))
        self.assertEqual(len(self.db.query('SELECT * FROM mail')), mail_count)
        # settle's legacy migration snapshot must not overwrite this claim.
        self.assertLess(response.scs.index(17023), response.scs.index(34111))
        self.assertEqual(response.scs[-1], 34101)
        self.assertEqual(self.states(response)[-1], self.cold())
        self.assertEqual(self.cold()['total_sign_receive_list'], [201, 202])
        prior = self.snapshot()
        retry, replay = self.claim()
        self.assertEqual(retry['result'], 0)
        self.assertFalse([r for r in retry.get('rewards', []) if r['num'] > 0])
        self.assertEqual(self.snapshot(), prior)
        self.assertEqual(self.states(replay)[-1], self.cold())

    def test_current_receipt_claims_missing202_once_without_daily_increment(self):
        record = self.seed()
        packet, response = self.claim()
        self.assertEqual(packet['rewards'], [{'id': 1, 'num': 200}])
        self.assertEqual(packet['daily_record'], record)
        self.assertEqual(packet['total_sign_times'], 7)
        self.assertEqual(response.scs.count(34111), 1)
        self.assertLess(response.scs.index(17023), response.scs.index(34111))
        self.assertLess(response.scs.index(34111), response.scs.index(34101))
        self.assertFalse([r for r in self.claim()[0].get('rewards', []) if r['num'] > 0])
        self.assertEqual(self.balance(1), 200)

    def test_unreached_milestone_does_not_grant_or_advance_day(self):
        record = self.seed(days=6)
        packet, _ = self.claim()
        self.assertEqual(packet['result'], 0)
        self.assertFalse([r for r in packet.get('rewards', []) if r['num'] > 0])
        self.assertEqual(packet['total_sign_times'], 6)
        self.assertEqual(packet['daily_record'], record)
        self.assertEqual(packet['total_sign_receive_list'], [201])
        self.assertEqual(self.balance(1), 0)

    def test_both_templates_keep_received_milestones_and_grant_only_due_missing_one(self):
        for template, days, received, reward, final in (
                (1, 7, (101,), {'id': 38, 'num': 10}, [101, 102]),
                (2, 14, (201, 202), {'id': 19, 'num': 10}, [201, 202, 203])):
            with self.subTest(template=template):
                record = self.seed(days=days, received=received, template=template)
                before = self.balance(reward['id'])
                packet, response = self.claim()
                self.assertEqual(packet['result'], 0)
                self.assertEqual([r for r in packet['rewards'] if r['num'] > 0], [reward])
                self.assertEqual(self.balance(reward['id']), before + reward['num'])
                self.assertEqual(packet['total_sign_receive_list'], final)
                self.assertEqual(packet['total_sign_times'], days)
                self.assertEqual(packet['daily_record'], record)
                self.assertEqual(self.states(response)[-1], self.cold())

    def test_required_ack_snapshot_and_asset_encoding_failures_rollback_and_retry(self):
        self.seed()
        for target in ('ack', 'snapshot', 'asset'):
            with self.subTest(target=target):
                # Keep fault cases independent even when running against old code.
                self.seed()
                self.db.set_currency(UID, 1, 0)
                before = self.snapshot()
                if target == 'ack':
                    ctx = self.conn.to_handler_ctx(); real = ctx.codec_encode
                    fault = patch.object(ctx, 'codec_encode', side_effect=lambda schema, *a, **kw:
                        None if schema == 'sc_34111' else real(schema, *a, **kw))
                else:
                    real = generator.gen_payload
                    cmd = 34101 if target == 'snapshot' else 17023
                    fault = patch.object(generator, 'gen_payload', side_effect=lambda sc, *a, **kw:
                        None if sc == cmd else real(sc, *a, **kw))
                with fault:
                    packet, response = self.claim()
                self.assertNotEqual(packet['result'], 0)
                self.assertEqual(response.scs, [34111])
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(self.conn.to_handler_ctx().pending_frames)
        self.assertEqual(self.claim()[0]['rewards'], [{'id': 1, 'num': 200}])
        self.assertFalse([r for r in self.claim()[0].get('rewards', []) if r['num'] > 0])
        self.assertEqual(self.balance(1), 200)

    def test_receipt_write_failure_rolls_back_real_grant_and_legacy_migration(self):
        self.seed(legacy=True)
        before = self.snapshot()
        with patch.object(paid, '_write_big', side_effect=RuntimeError('isolated receipt write')):
            packet, response = self.claim()
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(response.scs, [34111])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.conn.to_handler_ctx().pending_frames)
        self.assertEqual(self.claim()[0]['rewards'], [{'id': 1, 'num': 200}])
        self.assertEqual(self.balance(1), 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
