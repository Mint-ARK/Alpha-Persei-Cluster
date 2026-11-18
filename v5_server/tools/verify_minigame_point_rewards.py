"""Current 60054 registrations, SQL, Inventory and native codec in memory."""
import json
import re
from pathlib import Path
from unittest.mock import patch
import unittest
from verify_report_small_fixes import MemoryTests,UID,NOW
import codec
import core
import generator
from inventory_service import InventoryService
import minigame_service as mini
import minigame_point_service as point
import operations
import multiplayer_policy

ITEM_TYPES={}
for source in (Path(__file__).resolve().parents[2]/'decompiled_v2/x64/game/config').glob('itemcfg*.lua'):
    for raw_id,body in re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}',source.read_text('utf-8-sig'),re.M|re.S):
        kind=re.search(r'^\t\ttype = (\d+)',body,re.M)
        subtype=re.search(r'^\t\tsub_type = (\d+)',body,re.M)
        if kind:ITEM_TYPES[int(raw_id)]=(int(kind[1]),int(subtype[1]) if subtype else 0)


class PointRewardTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.visible=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400) for i in (321201,323801,3639701,280004,1101)]
        visibility=patch.object(generator,'get_activity_list',side_effect=lambda *a,**kw:self.visible)
        visibility.start();self.addCleanup(visibility.stop)
        self.visibility_patch=visibility
        for iid in (1,2,70,76,79):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,))
        reward_ids={i for rid in ('32380101','32380102','363970101','32120101','32120102','24190001','110101')
                    for i,n in point.CATALOG[rid]['reward']}
        for iid in reward_ids:
            if iid in (1,2):continue
            kind,subtype=ITEM_TYPES[iid]
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))
    def grant(self,iid,num):InventoryService.grant_items(self.conn.to_handler_ctx(),UID,[(iid,num)],source='isolated_fixture')
    def score(self,activity,amount):
        self.db.execute('INSERT INTO activity_point(uid,activity_id,point) VALUES(?,?,?) '
                        'ON CONFLICT(uid,activity_id) DO UPDATE SET point=excluded.point',(UID,activity,amount))
    def claim(self,ids):return self.call(60054,dict(point_reward_id_list=ids),60055)
    def state(self):return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                           ('activity_point_reward','activity_point','currency','material','skuld_progress','rogueteam_user','claim_ledger')}

    def test_zero_score_unknown_and_invalid_empty_directory_rows_grant_nothing(self):
        for ids in ([363970101],[32380101],[32120101],[99999001],[4322503],[]):
            with self.subTest(ids=ids):
                before=self.state();packet,response=self.claim(ids)
                self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
                self.assertNotIn(17023,response.scs)

    def test_currency_threshold_is_authoritative_and_is_not_spent(self):
        self.score(3639701,100);before=self.state()
        self.assertNotEqual(self.claim([363970101])[0]['result'],0);self.assertEqual(self.state(),before)
        self.grant(79,10);packet,response=self.claim([363970101])
        self.assertEqual(packet['result'],0);self.assertEqual(self.balance(79),10)
        self.assertEqual(packet['reward_list'],[dict(id=i,num=n) for i,n in point.CATALOG['363970101']['reward']])
        self.assertEqual(self.balance(1),50);self.assertEqual(self.balance(2),20000)
        self.assertIn(17023,response.scs);self.assertNotIn(60107,response.scs)
        self.assertLess(response.scs.index(17023),response.scs.index(60055))

    def test_zero_item_uses_resident_score_and_restores_omitted_native_materials(self):
        self.score(323801,1)
        packet,_=self.claim([32380101])
        self.assertEqual(packet['result'],0)
        self.assertEqual({v['id']:v['num'] for v in packet['reward_list']},{1:20,40103:5,2:10000})
        self.assertEqual(self.balance(40103),5);self.assertEqual(self.balance(2),10000)
        self.assertEqual(self.db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=323801',(UID,))[0]['point'],1)

    def test_configured_non_minigame_reward_uses_real_owner_items_and_threshold(self):
        cfg=point.CATALOG['24190001'];self.grant(cfg['need_item_id'],cfg['need'])
        packet,_=self.claim([24190001]);self.assertEqual(packet['result'],0)
        self.assertEqual(packet['reward_list'],[dict(id=i,num=n) for i,n in cfg['reward']])
        row=self.db.query('SELECT activity_id FROM activity_point_reward WHERE uid=? AND reward_id=24190001',(UID,))[0]
        self.assertEqual(row['activity_id'],280004)
        cold=codec.decode(generator.gen_payload(60107,uid=UID,db=self.db),'sc_60107')
        self.assertIn(24190001,cold['reward_info_list'])

    def test_whole_batch_rejects_duplicates_unknowns_unqualified_and_already_claimed(self):
        self.grant(79,10)
        for ids in ([363970101,363970101],[363970101,99999001],[363970101,363970102]):
            before=self.state();self.assertNotEqual(self.claim(ids)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.claim([363970101])[0]['result'],0)
        self.grant(79,10);before=self.state()
        self.assertNotEqual(self.claim([363970101,363970102])[0]['result'],0);self.assertEqual(self.state(),before)

    def test_open_state_and_server_adjusted_window_are_required(self):
        self.grant(79,10)
        for change in (dict(state=0),dict(stop_time=NOW),dict(start_time=NOW+1)):
            row=next(r for r in self.visible if r['activity_id']==3639701);old=dict(row);row.update(change)
            before=self.state();self.assertNotEqual(self.claim([363970101])[0]['result'],0);self.assertEqual(self.state(),before)
            row.clear();row.update(old)
        self.assertEqual(self.claim([363970101])[0]['result'],0)

    def test_skuld_legacy_claim_flag_is_respected_and_other_progress_is_preserved(self):
        self.grant(76,20)
        self.db.execute('INSERT INTO skuld_progress(uid,activity_id,point,reward_list,client_opt_list,mission_json) VALUES(?,321201,7,?,?,?)',
                        (UID,'[32120101]','[1,2]','[{"id":1001,"times":1}]'))
        before=self.state();self.assertNotEqual(self.claim([32120101])[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.claim([32120102])[0]['result'],0)
        row=self.db.query('SELECT * FROM skuld_progress WHERE uid=?',(UID,))[0]
        self.assertEqual(json.loads(row['reward_list']),[32120101,32120102])
        self.assertEqual(row['point'],7);self.assertEqual(row['client_opt_list'],'[1,2]')
        self.assertEqual(self.balance(76),20)

    def test_rogue_score_uses_native_point_item_and_sends_full_claimed_id_state(self):
        self.db.upsert('rogueteam_user',UID,dict(template_id=100001,point=999999,rewarded_list_json='[110102]'),keys=('uid','template_id'))
        self.assertNotEqual(self.claim([110101])[0]['result'],0)
        self.grant(70,35);packet,response=self.claim([110101])
        self.assertEqual(packet['result'],0);self.assertEqual(self.balance(70),35)
        state=next(codec.decode(f['payload'],'sc_88309') for f in response.frames if f['sc']==88309)
        self.assertEqual(state['reward_list'],[110102,110101])
        before=self.state();self.assertNotEqual(self.claim([110102])[0]['result'],0);self.assertEqual(self.state(),before)

    def test_native_batch_rewards_are_summed_and_all_registered_claims_are_recorded(self):
        self.score(323801,2)
        packet,response=self.claim([32380101,32380102]);self.assertEqual(packet['result'],0)
        expected={}
        for rid in (32380101,32380102):
            for iid,num in point.CATALOG[str(rid)]['reward']:expected[iid]=expected.get(iid,0)+num
        self.assertEqual({v['id']:v['num'] for v in packet['reward_list']},expected)
        self.assertEqual(len(self.db.query('SELECT * FROM activity_point_reward WHERE uid=?',(UID,))),2)
        self.assertEqual(response.scs.count(17023),1)

    def test_grant_write_after_mutation_rolls_back_all_claims_and_notifications(self):
        self.score(323801,1);before=self.state();grant=InventoryService.grant_items
        def fail(ctx,uid,items,**kw):
            result=grant(ctx,uid,items,**kw)
            raise RuntimeError('isolated grant write-after fault')
        with patch.object(InventoryService,'grant_items',side_effect=fail):
            packet,response=self.claim([32380101]);self.assertNotEqual(packet['result'],0);self.assertNotIn(17023,response.scs)
        self.assertEqual(self.state(),before);self.assertEqual(self.claim([32380101])[0]['result'],0)

    def test_ack_and_inventory_difference_preparation_failures_roll_back_and_retry(self):
        self.score(323801,1);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_60055' else encode(n,d)):
            self.assertNotEqual(self.claim([32380101])[0]['result'],0)
        self.assertEqual(self.state(),before)
        gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else gen(cmd,*a,**kw)):
            self.assertNotEqual(self.claim([32380101])[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.claim([32380101])[0]['result'],0)

    def test_claim_record_write_failure_rolls_back_before_grant(self):
        self.score(323801,1);before=self.state();execute=self.db.execute
        def fail(sql,*args,**kw):
            result=execute(sql,*args,**kw)
            if sql.startswith('INSERT INTO activity_point_reward'):raise RuntimeError('isolated claim record fault')
            return result
        with patch.object(self.db,'execute',side_effect=fail):self.assertNotEqual(self.claim([32380101])[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_both_registration_classes_share_identical_transactional_implementation(self):
        self.assertIs(operations.ActivityPointTierRewardOp.apply,mini.ActivityPointRewardOp.apply)
        self.assertIs(operations.ActivityPointTierRewardOp.respond,mini.ActivityPointRewardOp.respond)
        self.assertTrue(operations.ActivityPointTierRewardOp.prepare_response_in_transaction)
        self.assertIsInstance(core.OPERATIONS.get(60054),mini.ActivityPointRewardOp)

    def test_shared_solo_claim_is_allowed_while_multiplayer_requests_stay_blocked(self):
        self.assertFalse(multiplayer_policy.blocks_command(60054))
        self.assertFalse(multiplayer_policy.blocks_command(60055))
        before=self.state()
        for cmd in (31002,33000,36010,70010,37010,90002,90102,58050,58160,58204,19002,27010):
            response=self.core.dispatch(core.CoreRequest(cmd,b'',uid=UID),self.conn)
            self.assertEqual(response.src,'offline-policy',cmd)
            self.assertEqual(codec.decode(response.frames[0]['payload'],'sc_'+str(response.frames[0]['sc']))['result'],17)
        self.assertEqual(self.state(),before)

    def test_actual_distributed_activity_catalog_allows_the_resident_claim(self):
        self.visibility_patch.stop()
        self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,323801,1,1,?,38,32)',(UID,NOW+86400))
        self.score(323801,1)
        packet,response=self.claim([32380101]);self.assertEqual(packet['result'],0)
        self.assertIn(17023,response.scs)


if __name__=='__main__':unittest.main(verbosity=2)
