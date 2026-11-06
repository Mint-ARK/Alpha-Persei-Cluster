"""GH-9 / A137-A138: Core, real reward inventory, memory SQL and protocol."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import achievement_service as achievement
import generator
from inventory_service import InventoryService
from core import OperationError


class ClaimTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc=achievement.AchievementService(self.db)
        for aid in (10101,10102):
            cfg=achievement._CFG[aid]
            self.db.execute('INSERT INTO achievement(uid,achievement_id,need,progress,complete_flag) VALUES(?,?,?,?,0)',
                            (UID,aid,cfg['need'],cfg['need']))

    def request(self,ids):
        return self.call(53004,dict(id=ids),53005)

    def flag(self,aid=10101):
        return self.db.query('SELECT complete_flag FROM achievement WHERE uid=? AND achievement_id=?',(UID,aid))[0]['complete_flag']

    def snapshot(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('achievement','currency','material','users')}

    def expected(self,ids):
        result={}
        for aid in ids:
            for item,num in achievement._CFG[aid]['reward']:
                result[item]=result.get(item,0)+num
        return result

    def reject(self,ids,code):
        before=self.snapshot()
        answer,response=self.request(ids)
        self.assertEqual(answer['result'],code)
        self.assertFalse(answer.get('reward_list'))
        self.assertEqual(response.scs,[53005])
        self.assertEqual(self.snapshot(),before)

    def test_same_packet_repeated_id_grants_one_copy(self):
        answer,response=self.request([10101]*3)
        self.assertEqual(answer['result'],0)
        self.assertEqual({v['id']:v['num'] for v in answer['reward_list']},self.expected([10101]))
        self.assertEqual(self.flag(),1)
        for item,num in self.expected([10101]).items():
            self.assertEqual(self.balance(item),num)
        self.assertLess(response.scs.index(17023),response.scs.index(53005))
        self.reject([10101],2013)

    def test_multi_item_batch_deduplicates_without_losing_valid_rewards(self):
        answer,_=self.request([10102,10101,10102,10101])
        self.assertEqual(answer['result'],0)
        self.assertEqual({v['id']:v['num'] for v in answer['reward_list']},self.expected([10102,10101]))
        self.assertEqual((self.flag(),self.flag(10102)),(1,1))

    def test_not_ready_batch_is_rejected_in_full(self):
        self.db.execute('UPDATE achievement SET progress=0 WHERE achievement_id=10102')
        self.reject([10101,10102],2005)
        self.assertEqual(self.flag(),0)
        self.assertEqual(self.request([10101])[0]['result'],0)

    def test_claimed_and_unclaimed_batch_does_not_fake_partial_success(self):
        self.assertEqual(self.request([10101])[0]['result'],0)
        self.reject([10102,10101],2013)
        self.assertEqual(self.flag(10102),0)
        self.assertEqual(self.request([10102])[0]['result'],0)

    def test_empty_unknown_and_zero_requests_are_not_success(self):
        for ids in ([],[0],[99999999],[10101,0],[10101,99999999]):
            with self.subTest(ids=ids):
                self.reject(ids,2)

    def test_missing_row_does_not_fake_claim(self):
        self.db.execute('DELETE FROM achievement WHERE achievement_id=10102')
        self.reject([10101,10102],2005)

    def test_direct_service_call_is_atomic_and_replay_rejected(self):
        answer=self.svc.claim_achievements(UID,[10101]*3)
        self.assertEqual(answer['claimed_ids'],[10101])
        self.assertEqual({v['id']:v['num'] for v in answer['reward_list']},self.expected([10101]))
        before=self.snapshot()
        with self.assertRaises(OperationError) as caught:
            self.svc.claim_achievements(UID,[10101])
        self.assertEqual(caught.exception.code,2013)
        self.assertEqual(self.snapshot(),before)

    def test_inventory_failure_rolls_back_flag_and_award(self):
        before=self.snapshot()
        grant=InventoryService.grant_items
        def fail(*args,**kwargs):
            grant(*args,**kwargs)
            raise RuntimeError('injected inventory failure')
        with patch.object(InventoryService,'grant_items',side_effect=fail):
            self.assertNotEqual(self.request([10101])[0]['result'],0)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.request([10101])[0]['result'],0)

    def test_missing_diff_and_ack_roll_back_before_commit(self):
        for which in ('diff','ack'):
            with self.subTest(which=which):
                before=self.snapshot()
                if which=='diff':
                    real=generator.gen_payload
                    def fail(cmd,*args,**kwargs):
                        return None if cmd==17023 else real(cmd,*args,**kwargs)
                    context=patch.object(generator,'gen_payload',side_effect=fail)
                else:
                    import codec
                    real=codec.encode
                    def fail(name,*args,**kwargs):
                        return None if name=='sc_53005' else real(name,*args,**kwargs)
                    # Core reuses the connection's already-created HandlerCtx.
                    context=patch.object(self.conn.to_handler_ctx(),'codec_encode',side_effect=fail)
                with context:
                    answer,response=self.request([10101])
                self.assertNotEqual(answer['result'],0)
                self.assertEqual(response.scs,[53005])
                self.assertEqual(self.snapshot(),before)

    def test_conditional_flag_transition_must_succeed_before_award(self):
        before=self.snapshot()
        real=self.db.execute
        def intercept(sql,*args,**kwargs):
            if sql.startswith('UPDATE achievement SET complete_flag=1'):
                real('UPDATE achievement SET complete_flag=1 WHERE uid=? AND achievement_id=10101',(UID,))
            return real(sql,*args,**kwargs)
        with patch.object(self.db,'execute',side_effect=intercept):
            answer,_=self.request([10101])
        self.assertEqual(answer['result'],2013)
        self.assertEqual(self.snapshot(),before)


if __name__=='__main__':
    unittest.main()
