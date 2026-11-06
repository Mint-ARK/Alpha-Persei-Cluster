"""A260 authoritative59009, real claim/ledger/codec with unrelated settle isolated."""
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID, NOW
import codec
import generator
import paid_entitlement_service as paid
from recharge_service import RechargeService


class MonthStateTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for guard in (patch('time.time', return_value=NOW), patch.object(paid, 'settle')):
            guard.start(); self.addCleanup(guard.stop)
        # The already-covered periodic/compensation settlement is isolated here;
        # actual34024 claim, InventoryService, SQL receipt and Core codec still run.
        paid._save(self.db, UID, dict(small_long=True, small_since=NOW))
        self.db.execute('INSERT INTO game_user(uid) VALUES(?)', (UID,))
        self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(1,1)')
        self.db.get_or_create_newbie_activity(UID)
        self.db.execute('''UPDATE newbie_activity SET fr_first_gear=1,fr_second_gear=1,fr_new6=1,
            fr_new18=1,mc_flag=1,mc_role_flag=1,mc_sign_times=24,mc_new_role=1,mc_new_sign=1,
            bp_reward=1,bp_new=1 WHERE uid=?''', (UID,))

    def claim(self):
        return self.call(34024, {}, 34025)

    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('newbie_activity','currency','material','ops_common','month_card','sign','users','game_user','task')}

    def expected(self):
        rows = self.db.query('SELECT * FROM newbie_activity WHERE uid=?', (UID,))
        return RechargeService.project_59009_payload(rows[0] if rows else {})

    def test_real_skeleton_receives_complete_rights_before_ack_and_no_false_fallback(self):
        expected = self.expected(); packet, response = self.claim()
        self.assertEqual(packet['result'], 0); self.assertEqual(self.balance(1), 90)
        self.assertEqual(response.scs.count(59009), 1)
        state = next(codec.decode(frame['payload'],'sc_59009') for frame in response.frames if frame['sc']==59009)
        self.assertEqual(state, expected)
        self.assertLess(response.scs.index(17023), response.scs.index(59009))
        self.assertLess(response.scs.index(59009), response.scs.index(34025))
        cold = codec.decode(generator.gen_payload(59009,uid=UID,db=self.db),'sc_59009')
        self.assertEqual(state,cold)

    def test_same_day_retry_no_assets_and_preserves_all_existing_rights(self):
        self.claim(); expected = self.expected(); amount = self.balance(1)
        packet,response = self.claim()
        self.assertEqual(packet['result'],0); self.assertFalse(packet.get('reward_list'))
        self.assertEqual(self.balance(1),amount)
        self.assertEqual(next(codec.decode(frame['payload'],'sc_59009') for frame in response.frames if frame['sc']==59009),expected)

    def test_no_newbie_record_projects_absence_without_creating_activity(self):
        self.db.execute('DELETE FROM newbie_activity WHERE uid=?',(UID,))
        expected = self.expected(); packet,response = self.claim()
        self.assertEqual(packet['result'],0)
        self.assertFalse(self.db.query('SELECT 1 FROM newbie_activity WHERE uid=?',(UID,)))
        self.assertEqual(next(codec.decode(frame['payload'],'sc_59009') for frame in response.frames if frame['sc']==59009),expected)

    def test_required_ack_state_or_asset_failure_rolls_back_claim_and_retries_once(self):
        for target in ('sc_34025','sc_59009','delta'):
            before = self.state()
            if target=='delta':
                real=generator.gen_payload
                fault=patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw: None if cmd==17023 else real(cmd,*a,**kw))
            else:
                ctx=self.conn.to_handler_ctx(); real=ctx.codec_encode
                fault=patch.object(ctx,'codec_encode',side_effect=lambda schema,*a,**kw: None if schema==target else real(schema,*a,**kw))
            with fault:
                packet,response=self.claim()
            self.assertNotEqual(packet['result'],0); self.assertEqual(response.scs,[34025])
            self.assertEqual(self.state(),before)
        self.assertEqual(self.claim()[0]['result'],0); self.assertEqual(self.balance(1),90)


if __name__=='__main__':
    unittest.main(verbosity=2)
