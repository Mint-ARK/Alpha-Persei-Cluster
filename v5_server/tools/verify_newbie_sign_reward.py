"""A248 ordinary sign eligibility, 05:00 boundaries and transactional responses."""
import datetime
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import codec
import generator


def stamp(value):
    return int(datetime.datetime.fromisoformat(value).timestamp())


class SignRewardTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.now = stamp('2026-11-05T12:00:00')
        clock = patch('time.time', side_effect=lambda: self.now); clock.start(); self.addCleanup(clock.stop)
        self.db.get_or_create_newbie_activity(UID)
        self.db.execute('UPDATE newbie_activity SET trigger_time=?,completed_time=?,version_id=3 WHERE uid=?',
                        (self.now-7*86400, self.now+60*86400, UID))
        for iid in (1,2,38):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)', (iid,))

    def claim(self):
        return self.call(59012, {}, 59013)

    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('newbie_activity', 'newbie_level_reward', 'currency', 'material')}

    def test_same_game_day_and_midnight_reject_without_local_extra_sign(self):
        self.now = stamp('2026-11-05T23:59:00')
        packet, response = self.claim(); self.assertEqual(packet['result'], 0)
        self.assertEqual(packet['reward_list'], [dict(id=38,num=5)])
        self.assertEqual(response.scs, [17023,59013])
        local_day = 1
        for now in (self.now, stamp('2026-11-06T00:01:00')):
            self.now = now; before = self.state(); packet, response = self.claim()
            if packet['result'] == 0:
                local_day += 1
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [59013]); self.assertEqual(self.state(), before)
        cold = codec.decode(generator.gen_payload(59011, uid=UID, db=self.db), 'sc_59011')
        self.assertEqual(cold['newbie_sign']['now_sign_times'], local_day)
        self.assertEqual(self.balance(38), 5)

    def test_exact_five_am_allows_next_day_and_ack_does_not_send_append_snapshot(self):
        self.now = stamp('2026-11-06T04:59:59')
        self.assertEqual(self.claim()[0]['result'], 0)
        self.now = stamp('2026-11-06T05:00:00')
        packet, response = self.claim()
        self.assertEqual(packet['result'], 0)
        self.assertEqual(packet['reward_list'], [dict(id=2,num=20000)])
        self.assertEqual(response.scs, [17023,59013])
        cold = codec.decode(generator.gen_payload(59011, uid=UID, db=self.db), 'sc_59011')
        self.assertEqual(cold['newbie_sign'], dict(now_sign_times=2,last_sign_timestamp=self.now))

    def test_each_version_has_a_finite_last_day_and_preserves_existing_amount(self):
        for version, cap, reward in ((2,14,dict(id=1,num=1000)), (3,14,dict(id=1,num=1000)), (1,15,dict(id=38,num=1))):
            self.db.execute('UPDATE newbie_activity SET version_id=?,now_sign_times=?,last_sign_ts=? WHERE uid=?',
                            (version,cap-1,self.now-86400,UID))
            packet, _ = self.claim(); self.assertEqual(packet['result'], 0)
            self.assertEqual(packet['reward_list'], [reward])
            self.now += 86400; before = self.state()
            self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)

    def test_expired_unstarted_missing_or_invalid_state_never_grants(self):
        for trigger, end, version, count in ((0,self.now+60,3,0), (self.now+1,self.now+60,3,0),
                    (self.now-60,self.now,3,0), (self.now-60,self.now+60,999,0), (self.now-60,self.now+60,3,-1)):
            self.db.execute('UPDATE newbie_activity SET trigger_time=?,completed_time=?,version_id=?,now_sign_times=? WHERE uid=?',
                            (trigger,end,version,count,UID))
            before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.db.execute('DELETE FROM newbie_activity WHERE uid=?', (UID,))
        before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)

    def test_zero_completion_is_native_open_sentinel_and_clock_rollback_does_not_sign(self):
        self.db.execute('UPDATE newbie_activity SET completed_time=0,last_sign_ts=? WHERE uid=?', (self.now+86400,UID))
        before = self.state(); self.assertNotEqual(self.claim()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.db.execute('UPDATE newbie_activity SET last_sign_ts=0 WHERE uid=?', (UID,))
        self.assertEqual(self.claim()[0]['result'], 0)

    def test_day_asset_or_required_packet_failure_rolls_back_and_retries_once(self):
        for target in ('day', 'currency', 'ack', 'delta'):
            before = self.state()
            if target in ('day','currency'):
                real = self.db.execute
                def fail(sql, params=()):
                    result = real(sql, params)
                    if ((target=='day' and sql.startswith('UPDATE newbie_activity SET now_sign_times'))
                            or (target=='currency' and 'currency' in sql and sql.lstrip().upper().startswith(('INSERT','UPDATE')))):
                        raise RuntimeError('after isolated '+target+' write')
                    return result
                fault = patch.object(self.db, 'execute', side_effect=fail)
            elif target=='ack':
                ctx = self.conn.to_handler_ctx(); real = ctx.codec_encode
                fault = patch.object(ctx, 'codec_encode', side_effect=lambda schema,*a,**kw:
                                     None if schema=='sc_59013' else real(schema,*a,**kw))
            else:
                real = generator.gen_payload
                fault = patch.object(generator, 'gen_payload', side_effect=lambda command,*a,**kw:
                                     None if command==17023 else real(command,*a,**kw))
            with fault:
                packet, response = self.claim()
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(response.scs, [59013]); self.assertEqual(self.state(), before)
        self.assertEqual(self.claim()[0]['result'], 0); self.assertEqual(self.balance(38), 5)


if __name__=='__main__':
    unittest.main(verbosity=2)
