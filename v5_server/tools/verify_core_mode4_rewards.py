"""Partial A93/A95 guard: real memory Core, Inventory and native reward/state codec."""
import json
import struct
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zlib

from verify_report_small_fixes import MemoryTests,UID,NOW
import codec
import core_mode4_reward_service as rewards
import generator
import inventory_service as inventory
import res_version_manager as versions
import weekly_challenge_service as weekly


class Mode4RewardTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.conn.resource_version='311'
        self.visible=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400)
                      for i in (4343301,4343501,4443301,4443501)]
        for p in (patch('time.time',return_value=NOW),
                  patch.object(generator,'get_activity_list',side_effect=lambda *a,**kw:self.visible)):
            p.start();self.addCleanup(p.stop)
        for iid in (1,2):self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,))

    def state(self):
        return {table:self.db.query('SELECT * FROM '+table+' ORDER BY rowid') for table in
                ('currency','material','core_verification_cl_progress','core_verification_cl_task_claimed')}

    def seed(self,stage=3092121,activity=4443501,mode=4,score=0):
        self.db.record_core_verification_cl_stage_clear(UID,activity,mode,stage,min_time_ms=30000,score=score,heroes=[1084])

    def claim(self,ids,activity=4443301):return self.call(89804,dict(activity_id=activity,assignment_id=ids),89805)
    def reject(self,ids,activity=4443301,code=None):
        before=self.state();packet,response=self.claim(ids,activity)
        self.assertNotEqual(packet['result'],0)
        if code is not None:self.assertEqual(packet['result'],code)
        self.assertEqual(response.scs,[89805]);self.assertFalse(packet.get('reward_list'))
        self.assertEqual(self.state(),before)

    def snapshot(self,activity=4443501):
        mode,data,_=weekly.get_core_verification_challenge_data(UID,self.db,activity)
        self.assertEqual(mode,4)
        raw=codec.encode('sc_89801',data)
        return {r['assignment_id']:r['state'] for r in codec.decode(raw,'sc_89801')['finish_assignment_list']}

    def test_native_two_baseline_exports_match_and_keep_old_reward_amounts(self):
        from export_core_mode4_rewards import build
        self.assertEqual(rewards.catalog(),build())
        self.assertEqual(len(rewards.catalog()['profiles']['311']['tasks']),6)
        self.assertEqual(rewards.catalog()['profiles']['229']['tasks']['50209']['items'],[dict(id=1,num=80)])

    def test_empty_unknown_duplicate_batch_and_unqualified_never_grant(self):
        for ids in ([],[99999999],[50301,50301],[50301,99999999]):self.reject(ids,code=2)
        self.reject([50301],code=2005)
        self.assertEqual(self.snapshot(),{i:0 for i in range(50301,50307)})

    def test_clear_qualification_state1_then_claim_state2_and_real_delta(self):
        self.seed();self.assertEqual(self.snapshot()[50301],1)
        packet,response=self.claim([50301]);self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs,[17023,89805]);self.assertEqual(packet['reward_list'],[dict(id=1,num=80)])
        delta=codec.decode(response.frames[0]['payload'],'sc_17023')
        self.assertIn(dict(id=1,num=80),delta['normal_items']);self.assertEqual(self.balance(1),80)
        self.assertEqual(self.snapshot()[50301],2);self.reject([50301],code=2013)

    def test_all_four_clear_tasks_batch_merge_once_without_full_snapshot_push(self):
        for stage in (3092121,3092122,3092123,3093121):self.seed(stage)
        states=self.snapshot();self.assertTrue(all(states[i]==1 for i in range(50301,50305)))
        packet,response=self.claim([50301,50302,50303,50304]);self.assertEqual(packet['result'],0)
        self.assertEqual(packet['reward_list'],[dict(id=1,num=300)]);self.assertEqual(self.balance(1),300)
        self.assertEqual(response.scs,[17023,89805])
        self.assertTrue(all(self.snapshot()[i]==2 for i in range(50301,50305)))

    def test_root_child_alias_share_ledger_and_old_parent_claims_prevent_reissue(self):
        self.seed();self.assertEqual(self.claim([50301],4443501)[0]['result'],0)
        self.reject([50301],4443301,2013)
        self.seed(3092122);self.db.claim_core_verification_cl_task(UID,4443301,50302)
        self.assertEqual(self.snapshot()[50302],2);self.reject([50302],4443501,2013)

    def test_unknown_inactive_other_mode_and_other_version_activities_rejected(self):
        self.seed()
        for activity in (0,111,3539501,4443401,4443502,4343301,4343501):self.reject([50301],activity)
        for missing in (4443301,4443501):
            self.visible=[a for a in self.visible if a['activity_id']!=missing]
            self.reject([50301])

    def test_progress_in_other_activity_or_mode_does_not_qualify(self):
        self.seed(activity=4343501);self.reject([50301],code=2005)
        self.seed(mode=1);self.reject([50301],code=2005)

    def test_score_tasks_wait_for_a94_even_with_arbitrarily_high_single_row_score(self):
        self.seed(3093121,score=999999999)
        for tid in (50305,50306):self.reject([tid],code=2005)
        states=self.snapshot();self.assertEqual(states[50304],1);self.assertEqual(states[50305],0);self.assertEqual(states[50306],0)
        self.reject([50304,50305],code=2005)

    def test_mixed_claimed_batch_is_atomic_and_never_reissues_or_partially_grants(self):
        self.seed();self.seed(3092122);self.assertEqual(self.claim([50301])[0]['result'],0)
        self.reject([50301,50302],code=2013);self.assertEqual(self.snapshot()[50302],1)
        self.assertEqual(self.claim([50302])[0]['result'],0);self.assertEqual(self.balance(1),160)

    def test_229_native_root_and_old_catalog_tasks_use_their_own_records(self):
        self.conn.resource_version='229';self.seed(3092111,4343501)
        self.reject([50301],4343301,2)
        packet,response=self.claim([50209],4343301);self.assertEqual(packet['result'],0)
        self.assertEqual(self.snapshot(4343501)[50209],2);self.assertEqual(self.balance(1),80)

    def test_cold_generator_res_version_restores_state1_and_state2(self):
        self.seed();self.seed(3092122);self.assertEqual(self.claim([50301])[0]['result'],0)
        packet=codec.decode(generator.gen_payload(89801,uid=UID,db=self.db,res_version='311'),'sc_89801')
        states={row['assignment_id']:row['state'] for row in packet['finish_assignment_list']}
        self.assertEqual(states[50301],2);self.assertEqual(states[50302],1)

    def test_actual_login_flood_serializes_state1_and_state2_before_10201(self):
        import login
        import new_player_service
        import schema_default
        self.seed();self.seed(3092122);self.claim([50301]);before=self.state()
        rows=[(1,89801,generator.build_frame(89801,b'')[:11],b'old-capture',1,None),
              (192,10201,generator.build_frame(10201,b'')[:11],schema_default.build('sc_10201'),0,None)]
        class Policy:
            def __init__(self,*a):pass
            def allows(self,cmd):return cmd in (89801,10201)
            def report(self,log):pass
            def allows_empty_default(self,*a):return True
        stubs={
            'new_player_login_policy':NS(LoginPolicy=Policy),
            'birthday_service':NS(ensure_official_mail=lambda *a:False),
            'support_cosmetic_service':NS(ensure_support_cosmetic_mails=lambda *a:[]),
            'paid_entitlement_service':NS(settle=lambda *a,**kw:None),
            'res_version_manager':NS(get_current_version=lambda:'311',get_version_config=lambda *a:dict(core_verification_activity_id=4443501)),
            'trust_service':NS(TrustService=NS(get_instance=lambda:NS(
                get_displace_limit_payload=lambda *a:dict(limit_list=[]),get_combo_skill_payload=lambda *a:dict(info=dict(skill_list=[]))))),
            'ai_translator':NS(AITranslator=NS(get_instance=lambda **kw:NS(trigger_login_greeting=lambda *a,**kw:None))),
        }
        facade=NS(build_frame=generator.build_frame,gen_payload=lambda cmd,**kw:generator.gen_payload(cmd,**kw) if cmd==89801 else schema_default.build('sc_'+str(cmd)))
        raw=[]
        with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=False),\
             patch.object(login,'load_login_push',return_value=rows),patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
            login.send_login_flood(NS(sendall=raw.append,settimeout=lambda _:None),self.db,UID,facade,frame_delay=0)
        packets=[]
        for wire in raw:
            cmd=struct.unpack('>H',wire[5:7])[0]+(65536 if wire[4] else 0)
            if cmd not in (89801,10201):continue
            body=zlib.decompress(wire[11:]) if wire[2] else wire[11:]
            packets.append((cmd,codec.decode(body,'sc_'+str(cmd))))
        self.assertEqual([cmd for cmd,_ in packets],[89801,10201])
        states={r['assignment_id']:r['state'] for r in packets[0][1]['finish_assignment_list']}
        self.assertEqual((states[50301],states[50302]),(2,1));self.assertEqual(self.state(),before)

    def test_mark_inventory_ack_and_asset_delta_failures_rollback_and_retry(self):
        self.seed()
        for fault in ('mark','inventory','ack','delta'):
            before=self.state();ctx=self.conn.to_handler_ctx()
            if fault=='mark':
                real=self.db.claim_core_verification_cl_task
                def fail(*a,**kw):real(*a,**kw);raise RuntimeError('isolated after mark')
                change=patch.object(self.db,'claim_core_verification_cl_task',side_effect=fail)
            elif fault=='inventory':
                real=inventory.grant_item
                def fail(*a,**kw):real(*a,**kw);raise RuntimeError('isolated after inventory')
                change=patch.object(inventory,'grant_item',side_effect=fail)
            elif fault=='ack':
                real=ctx.codec_encode
                change=patch.object(ctx,'codec_encode',side_effect=lambda name,*a,**kw:None if name=='sc_89805' else real(name,*a,**kw))
            else:
                real=generator.gen_payload
                change=patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else real(cmd,*a,**kw))
            with change:self.reject([50301])
            self.assertEqual(self.state(),before)
        self.assertEqual(self.claim([50301])[0]['result'],0)

    def test_read_failure_never_grants_or_returns_false_unclaimed_state(self):
        self.seed();before=self.state();real=self.db.query
        def fail(sql,*a,**kw):
            if 'FROM core_verification_cl_task_claimed' in sql:raise RuntimeError('isolated ledger read')
            return real(sql,*a,**kw)
        with patch.object(self.db,'query',side_effect=fail):
            packet,response=self.claim([50301]);self.assertNotEqual(packet['result'],0)
            with self.assertRaises(RuntimeError):weekly.get_core_verification_challenge_data(UID,self.db,4443501)
        self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main(verbosity=2)
