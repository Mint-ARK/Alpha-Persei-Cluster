"""Skuld native Core entry, reward and login probes; memory saves only."""
from collections import Counter
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID,NOW
from verify_minigame_point_rewards import ITEM_TYPES
import codec
import generator
import hero_codec
import middleware as mw
import skuld_service as sk
import minigame_point_service as points

ROOT=Path(__file__).resolve().parents[2]
TASKS=json.loads((ROOT/'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class SkuldTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for obj in [patch('time.time',return_value=NOW),patch.dict(mw._BATTLE_SESSIONS,{},clear=True)]:
            obj.start();self.addCleanup(obj.stop)
        self.activities=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400)
                         for i in [sk.ACTIVITY,sk.CFG['parent_activity'],*sk.CFG['limited_activities']]]
        self.activity_patch=patch.object(generator,'get_activity_list',side_effect=lambda *a,**k:sk.activity_policy([dict(r) for r in self.activities]))
        self.activity_patch.start();self.addCleanup(self.activity_patch.stop)
        self.tasks=[t for t in TASKS.values() if t['condition']==329001 or t['id'] in sk.LIMITED_TASKS]
        for t in self.tasks:
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                (t['id'],t['type'],t['condition'],t['need'],t['activity_id'],json.dumps(t['additional_parameter']),json.dumps(t['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,t['id'],t['type']))
        items={i for v in sk.NODES.values() for i,n in v['rewards']}|{i for t in self.tasks for i,n in t['reward']}|{i for v in points.CATALOG.values() if v['activity_id']==sk.ACTIVITY for i,n in v['reward']}
        # Native item types come from the published catalog fixture.
        for i in items|{1,2,4,14}:
            kind=1 if i in (1,2,4,14,39,76) else ITEM_TYPES[i][0]
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(i,kind))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        self.conn.resource_version='229'

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('skuld_progress','stage_archive_collect','activity_point_reward','task','claim_ledger','currency','material','users','hero','chapter','stage_sub')}

    def enter(self,node=1001,activity=0,kind=602,raw=None):
        c=sk.NODES[node];story=sk.CFG['battles'][str(c['param'])]['tag']==2
        if story:packet,response=self.call(54034,dict(stage_id=c['param'] if raw is None else raw,activity_id=activity),54035)
        else:packet,response=self.call(54030,dict(common_info=dict(type=kind,dest=node if raw is None else raw,activity_id=activity,
                                          hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def finish(self,result=1):
        mw.get_battle_session(self.bid)['result_data']=dict(result=result,info=dict(battle_time=67000))
        return self.call(54032,dict(battle_id=self.bid),54033)

    def battle(self,node=1001):
        self.assertEqual(self.enter(node)[0]['result'],0)
        packet,response=self.finish();self.assertEqual(packet['result'],0)
        return packet,response

    def puzzle(self,node=1003,activity=sk.ACTIVITY):return self.call(24054,dict(activity_id=activity,id=node),24055)
    def mark(self,key=1,activity=sk.ACTIVITY):return self.call(24052,dict(activity_id=activity,key=key),24053)
    def rec(self):return sk.read(self.db,UID)
    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']

    def route(self,stop=None):
        for node,c in sk.NODES.items():
            if c['type']==1:self.battle(node)
            else:self.assertEqual(self.puzzle(node)[0]['result'],0)
            if node==stop:return

    def packet(self,response,sc):
        self.assertIn(sc,response.scs)
        return codec.decode(next(f['payload'] for f in response.frames if f['sc']==sc),'sc_'+str(sc))

    def test_empty_snapshot_readonly_and_accounts_isolated(self):
        before=self.state()
        self.assertEqual(codec.decode(generator.gen_payload(24051,uid=UID,db=self.db),'sc_24051'),dict(activity_id=sk.ACTIVITY,point=0))
        self.assertEqual(self.state(),before);self.battle()
        self.assertEqual(sk.payload(self.db,999)['mission_list'],[])

    def test_story_resource_and_combat_logical_ids_canonicalized(self):
        self.battle();self.battle(1002)
        self.assertEqual(self.rec()['mission_list'],[dict(id=1001,times=1),dict(id=1002,times=1)])
        self.assertEqual(mw.get_battle_session(self.bid)['stage_type'],602)
        self.assertEqual(self.balance(76),5)

    def test_puzzle_battle_activity_type_and_prerequisite_guards(self):
        before=self.state()
        for node,activity in [(1003,sk.ACTIVITY),(1001,sk.ACTIVITY),(999,sk.ACTIVITY),(1003,1),(2001,sk.ACTIVITY)]:
            self.assertNotEqual(self.puzzle(node,activity)[0]['result'],0);self.assertEqual(self.state(),before)
        for node,activity in [(1002,0),(1001,1),(1004,0)]:
            self.assertNotEqual(self.enter(node,activity)[0]['result'],0);self.assertEqual(self.state(),before)
        self.battle();before=self.state()
        for args in [dict(kind=1),dict(raw=5240102),dict(activity=1)]:
            self.assertNotEqual(self.enter(1002,**args)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_all_36_nodes_unlock_sequentially_and_first_drops_exact(self):
        self.route();self.assertEqual(len(self.rec()['mission_list']),36)
        expected=Counter()
        for cfg in sk.NODES.values():
            for i,n in cfg['rewards']:expected[i]+=n
        for i,n in expected.items():self.assertEqual(self.balance(i),n,i)
        for t in self.tasks:self.assertEqual(self.value(t['id']),t['need'],t['id'])
        self.assertEqual(self.value(40001024),20)

    def test_lose_exit_and_same_battle_retransmission_no_duplicate(self):
        self.battle();self.enter(1002);before=self.state()
        packet,_=self.finish(2);self.assertEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.enter(1002);before=self.state();self.finish(3);self.assertEqual(self.state(),before)
        self.battle(1002);before=self.state();self.finish();self.assertEqual(self.state(),before)
        self.battle(1002);self.assertEqual(self.balance(76),5)

    def test_puzzle_local_callback_no_full_snapshot_and_resources_before_ack(self):
        self.route(1002);packet,response=self.puzzle()
        self.assertEqual(packet['result'],0);self.assertIn(28007,response.scs);self.assertIn(17023,response.scs)
        self.assertLess(response.scs.index(17023),response.scs.index(24055));self.assertNotIn(24051,response.scs)
        self.assertEqual(packet['reward_list'],[dict(id=i,num=n) for i,n in sk.NODES[1003]['rewards']])
        _,response=self.puzzle();self.assertNotIn(17023,response.scs);self.assertEqual(self.rec()['mission_list'][-1],dict(id=1003,times=2))

    def test_battle_callback_no_full_snapshot_and_no_main_stage_growth(self):
        before=self.state();_,response=self.battle();after=self.state()
        for t in ['users','hero','chapter','stage_sub']:self.assertEqual(before[t],after[t],t)
        self.assertNotIn(24051,response.scs);self.assertIn(17023,response.scs)
        self.assertEqual(self.balance(4),0)

    def test_marks_whitelist_and_idempotency(self):
        before=self.state()
        for key,act in [(0,sk.ACTIVITY),(11,sk.ACTIVITY),(1,1)]:
            self.assertNotEqual(self.mark(key,act)[0]['result'],0);self.assertEqual(self.state(),before)
        for i in sk.CFG['marks']:self.assertEqual(self.mark(i)[0]['result'],0)
        before=self.state();_,response=self.mark();self.assertEqual(self.state(),before);self.assertEqual(response.scs,[24053])

    def test_bad_json_rejected_without_overwrite_and_unknown_records_filtered(self):
        sk.save(self.db,UID,dict(client_opt_list=[1,999],mission_list=[dict(id=999,times=1),dict(id=1001,times=0)],reward_list=[999],point=123))
        self.assertEqual(self.rec()['mission_list'],[]);self.assertEqual(self.rec()['client_opt_list'],[1]);self.assertEqual(self.rec()['reward_list'],[])
        self.assertEqual(sk.payload(self.db,UID)['point'],0)
        self.db.execute("UPDATE skuld_progress SET mission_json='{' WHERE uid=?",(UID,));before=self.state()
        self.assertNotEqual(self.mark()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_native_point_claim_updates_both_caches_and_prevents_duplicate(self):
        self.route(1030)
        rid=32120101;packet,response=self.call(60054,dict(point_reward_id_list=[rid]),60055)
        self.assertEqual(packet['result'],0);self.assertEqual(self.packet(response,60107),dict(reward_info_list=[rid]))
        self.assertIn(rid,sk.payload(self.db,UID)['reward_list']);before=self.state()
        self.assertNotEqual(self.call(60054,dict(point_reward_id_list=[rid]),60055)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_claim_history_merges_legacy_and_shared_rows(self):
        sk.save(self.db,UID,dict(client_opt_list=[],mission_list=[],reward_list=[32120101],point=0))
        self.db.execute('INSERT INTO activity_point_reward(uid,activity_id,reward_id) VALUES(?,?,?)',(UID,sk.ACTIVITY,32120102))
        self.assertEqual(sk.payload(self.db,UID)['reward_list'],[32120101,32120102])

    def test_puzzle_grant_failure_rolls_back_and_retry_succeeds(self):
        from inventory_service import InventoryService
        self.route(1002);before=self.state()
        with patch.object(InventoryService,'grant_items',side_effect=RuntimeError('injected grant')):
            self.assertNotEqual(self.puzzle()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.puzzle()[0]['result'],0)

    def test_puzzle_encode_failure_rolls_back_asset_task_and_progress(self):
        self.route(1002);before=self.state();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        for target in ['sc_24055','sc_28007']:
            with patch.object(ctx,'codec_encode',side_effect=lambda name,data:None if name==target else enc(name,data)):
                self.assertNotEqual(self.puzzle()[0]['result'],0)
            self.assertEqual(self.state(),before,target)
        gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else gen(cmd,**kw)):
            self.assertNotEqual(self.puzzle()[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.puzzle()[0]['result'],0)

    def test_battle_ack_failure_rolls_back_and_does_not_cache_success(self):
        self.battle();self.enter(1002);before=self.state();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_54033' and d.get('result')==0 else enc(n,d)):
            self.assertNotEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertEqual(self.finish()[0]['result'],0)

    def test_story_ack_failure_no_orphan_session(self):
        before=set(mw._BATTLE_SESSIONS);ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_54035' and d.get('result')==0 else enc(n,d)):
            self.assertNotEqual(self.enter()[0]['result'],0)
        self.assertEqual(set(mw._BATTLE_SESSIONS),before);self.battle()

    def test_receipt_prevents_first_drop_after_legacy_marker_loss(self):
        self.route(1003);n=self.balance(76)
        self.db.execute("UPDATE skuld_progress SET mission_json=? WHERE uid=?",(json.dumps([m for m in self.rec()['mission_list'] if m['id']!=1003]),UID))
        self.assertEqual(self.puzzle()[0]['result'],0);self.assertEqual(self.balance(76),n)

    def test_login_task_recovery_from_saved_nodes_preserves_claims(self):
        self.route(1009);self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        self.db.execute('UPDATE task SET complete_flag=1,claimed_ts=1 WHERE uid=? AND task_id=40001001',(UID,))
        sk.restore_for_login(self.db,UID);self.assertEqual(self.value(40001001),0);self.assertEqual(self.value(40001002),1)
        data=codec.decode(generator.gen_payload(28001,uid=UID,db=self.db),'sc_28001')
        self.assertEqual(next(t['progress'] for t in data['assignment_list'] if t['id']==40001003),1)

    def test_resident_keeps_first_drops_and_points_blocks_limited_tasks(self):
        self.activities[1]['state']=0;self.route(1003)
        self.assertEqual(self.value(40001001),0);self.assertEqual(self.balance(76),7)
        self.assertEqual(sk.visible_tasks(self.db,UID,[dict(task_id=40001001)]),[])
        self.db.execute('UPDATE task SET progress=1 WHERE uid=? AND task_id=40001001',(UID,));before=self.state()
        for cmd,data,sc in [(28010,dict(id=40001001),28011),(28014,dict(id_list=[40001001]),28015)]:
            self.assertNotEqual(self.call(cmd,data,sc)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_native_chapter_archives_delta_and_readonly_login_recovery(self):
        self.assertEqual(sk.archive_payload(self.db,UID),dict(plot_list=[42010101]))
        self.battle();_,response=self.battle(1002)
        self.assertEqual(self.packet(response,24045),dict(plot_list=[42010104]));self.assertNotIn(24043,response.scs)
        self.route(1030)
        self.db.execute('INSERT INTO stage_archive_collect(uid,archive_id) VALUES(?,42110109)',(UID,))
        before=self.state();data=codec.decode(generator.gen_payload(24043,uid=UID,db=self.db),'sc_24043')
        self.assertEqual(set(data['plot_list']),{int(i) for i in sk.CFG['archives']}|{42110109});self.assertEqual(self.state(),before)

    def test_archive_delta_failure_rolls_back_battle_and_can_retry(self):
        self.battle();self.enter(1002);before=self.state();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_24045' else enc(n,d)):
            self.assertNotEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertEqual(self.finish()[0]['result'],0)

    def test_fresh_tasks_restore_before_read_and_resident_229_311_policy(self):
        import new_player_service,res_version_manager as versions
        self.route(1003);self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        with patch.object(new_player_service,'ensure_tasks'):data=new_player_service.payload(self.db,UID,28001)
        self.assertEqual(next(t['progress'] for t in data['assignment_list'] if t['id']==40001001),1)
        self.activity_patch.stop()
        for aid in [sk.ACTIVITY,sk.CFG['parent_activity'],*sk.CFG['limited_activities']]:
            self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,?,?,1,?,4,32)',
                            (UID,aid,0 if aid==sk.CFG['parent_activity'] else 1,NOW+86400))
        for version in ['229','311']:
            with versions.connection_version(version):
                rows=generator.get_activity_list(self.db,UID)
                self.assertFalse(sk.limited_context(rows));self.assertEqual({r['state'] for r in rows if r['activity_id'] in sk.CFG['limited_activities']},{0})

    def test_main_chapter12_hint_is_not_a_mandatory_gate_or_progress_copy(self):
        # Native entrance merely offers a spoiler warning and a main-story link.
        self.assertEqual(sk.CFG['main_story_hint_stage'],1011243);self.battle()
        self.assertEqual(self.db.query('SELECT * FROM chapter WHERE uid=?',(UID,)),[])


    def test_contract_export_and_static_login_state_override(self):
        from export_skuld_contract import build
        self.assertEqual(sk.CFG,build())
        import login,sys,struct
        from types import SimpleNamespace
        import new_player_service
        class Policy:
            def __init__(self,*a):pass
            def allows(self,cmd):return cmd==24051
            def report(self,log):pass
            def allows_empty_default(self,*a):return True
        raw=[];calls=[]
        class Generator:
            build_frame=staticmethod(generator.build_frame)
            @staticmethod
            def gen_payload(cmd,**kw):
                calls.append(cmd);return generator.gen_payload(cmd,**kw)
        capture=codec.encode('sc_24051',dict(activity_id=sk.ACTIVITY,client_opt=[10],mission_list=[dict(id=2006,times=1)]))
        self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic) VALUES(1,24051,?,?,0)',
                        (generator.build_frame(24051,capture)[:11],capture))
        stubs={
            'new_player_login_policy':SimpleNamespace(LoginPolicy=Policy),
            'birthday_service':SimpleNamespace(ensure_official_mail=lambda *a:False),
            'support_cosmetic_service':SimpleNamespace(ensure_support_cosmetic_mails=lambda *a:[]),
            'paid_entitlement_service':SimpleNamespace(settle=lambda *a,**kw:None),
            'res_version_manager':SimpleNamespace(get_current_version=lambda:'229',get_version_config=lambda v:{}),
            'rogueteam_service':SimpleNamespace(RogueTeamService=SimpleNamespace(get=lambda **kw:SimpleNamespace(get_session_data=lambda uid:None))),
            'trust_service':SimpleNamespace(TrustService=SimpleNamespace(get_instance=lambda:SimpleNamespace(
                get_displace_limit_payload=lambda *a:dict(limit_list=[]),get_combo_skill_payload=lambda *a:dict(info=dict(skill_list=[]))))),
            'ai_translator':SimpleNamespace(AITranslator=SimpleNamespace(get_instance=lambda **kw:SimpleNamespace(trigger_login_greeting=lambda *a,**kw:None))),
        }
        with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=False),patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
            login.send_login_flood(SimpleNamespace(sendall=raw.append,settimeout=lambda *a:None),self.db,UID,Generator(),frame_delay=0)
        packets=[p for p in raw if struct.unpack('>H',p[5:7])[0]+(65536 if p[4] else 0)==24051]
        self.assertEqual(len(packets),1);self.assertEqual(calls.count(24051),1)
        self.assertEqual(codec.decode(packets[0][11:],'sc_24051'),dict(activity_id=sk.ACTIVITY,point=0))


if __name__=='__main__':unittest.main(verbosity=2)
