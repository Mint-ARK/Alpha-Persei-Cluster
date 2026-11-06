"""Resident PVP compensation through real task claims, memory-only."""
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import codec, generator, new_player_service
import onboarding_operations  # Production registration overrides the generic task classes.
from offline_minigame_rewards import restore_tasks, TASK_IDS
from activity_content_service import catalog


class OfflineRewardTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for iid,kind in ((1,1),(41302,6),(40603,6),(40104,6)):
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('task','claim_ledger','currency','material')}

    def test_policy_only_unlocks_four_resident_tasks_not_limited_or_assets(self):
        self.assertEqual(restore_tasks(self.db,UID),4)
        self.assertEqual(restore_tasks(self.db,UID),0)
        self.assertEqual({r['task_id'] for r in self.db.query('SELECT * FROM task')},set(TASK_IDS))
        self.assertFalse(self.db.query('SELECT * FROM currency'))
        self.assertFalse(self.db.query('SELECT * FROM material'))
        for tid in TASK_IDS:
            r=self.db.get('task',UID,'AND task_id=?',(tid,))
            self.assertEqual((r['progress'],r['complete_flag']),(catalog()['tasks'][str(tid)]['need'],0))

    def test_real_single_and_batch_claims_sync_once(self):
        restore_tasks(self.db,UID)
        answer,response=self.call(28010,dict(id=60533),28011)
        self.assertEqual(answer['result'],0);self.assertIn(17023,response.scs)
        self.assertEqual(self.balance(41302),50)
        answer,response=self.call(28014,dict(id_list=[60534,60535,60536]),28015)
        self.assertEqual(answer['result'],0);self.assertIn(17023,response.scs)
        self.assertEqual([self.balance(i) for i in (40603,40104,1)],[100,80,300])
        before=self.state();restore_tasks(self.db,UID)
        self.call(28014,dict(id_list=list(TASK_IDS)),28015)
        self.assertEqual(self.state(),before)

    def test_imported_claim_flags_and_ledger_survive_restoration(self):
        restore_tasks(self.db,UID)
        self.db.execute('UPDATE task SET complete_flag=1 WHERE uid=? AND task_id=60533',(UID,))
        self.db.mark_claimed(UID,'task',60534)
        restore_tasks(self.db,UID)
        for tid in (60533,60534):
            self.assertEqual(self.db.get('task',UID,'AND task_id=?',(tid,))['complete_flag'],1)
            self.call(28010,dict(id=tid),28011)
        self.assertEqual(self.balance(41302),0);self.assertEqual(self.balance(40603),0)

    def test_ack_encoding_failure_rolls_back_rewards_and_claim(self):
        restore_tasks(self.db,UID);before=self.state()
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode;generate=generator.gen_payload
        for cmd,body,ack in ((28010,dict(id=60536),28011),(28014,dict(id_list=list(TASK_IDS)),28015)):
            for failed in ('sc_'+str(ack),'sc_17023'):
                with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n==failed else encode(n,*a,**k)), \
                     patch.object(generator,'gen_payload',side_effect=lambda n,*a,**k:None if n==17023 and failed=='sc_17023' else generate(n,*a,**k)):
                    answer,_=self.call(cmd,body,ack)
                    self.assertNotEqual(answer['result'],0)
                self.assertEqual(self.state(),before)
        self.assertEqual(self.call(28010,dict(id=60536),28011)[0]['result'],0)
        self.assertEqual(self.balance(1),300)

    def test_restore_sql_failure_is_atomic(self):
        original=self.db.upsert
        def fail(table,uid,row,**kw):
            result=original(table,uid,row,**kw)
            if table=='task' and row['task_id']==60535:raise RuntimeError('injected policy failure')
            return result
        with patch.object(self.db,'upsert',side_effect=fail):
            with self.assertRaises(RuntimeError):restore_tasks(self.db,UID)
        self.assertFalse(self.db.query('SELECT * FROM task'))
        self.assertFalse(self.db.query('SELECT * FROM task_cfg'))

    def test_multiplayer_requests_stay_blocked(self):
        restore_tasks(self.db,UID);before=self.state()
        for cmd in (90002,90102):
            answer,_=self.call(cmd,{},cmd+1)
            self.assertEqual(answer['result'],17)
        self.assertEqual(self.state(),before)

    def test_normal_and_fresh_login_generation_use_real_policy(self):
        for profile in ('legacy','new_player_v1'):
            self.db.set_system_config('save.profile',profile)
            wire=codec.decode(generator.gen_payload(28001,uid=UID,db=self.db),'sc_28001')
            selected={r['id']:r for r in wire['assignment_list'] if r['id'] in TASK_IDS}
            self.assertEqual(set(selected),set(TASK_IDS))
            self.assertTrue(all(r['progress']>=catalog()['tasks'][str(i)]['need'] for i,r in selected.items()))
        answer,response=self.call(28010,dict(id=60536),28011)
        self.assertEqual(answer['result'],0)
        self.assertLess(response.scs.index(17023),response.scs.index(28011))
        wire=codec.decode(generator.gen_payload(28001,uid=UID,db=self.db),'sc_28001')
        self.assertEqual(next(r for r in wire['assignment_list'] if r['id']==60536)['complete_flag'],1)


if __name__=='__main__':unittest.main()
