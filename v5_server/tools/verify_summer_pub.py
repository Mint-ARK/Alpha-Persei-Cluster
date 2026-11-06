"""SummerPub native request/callback contracts using isolated memory saves."""
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID, NOW
from verify_minigame_point_rewards import ITEM_TYPES
import codec
import generator
import hero_codec
import middleware as mw
import new_player_service
import summer_pub_service as pub
from event_bus import bus, Events

ROOT=Path(__file__).resolve().parents[2]
TASKS=json.loads((ROOT/'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class PubTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        sessions=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);sessions.start();self.addCleanup(sessions.stop)
        self.activities=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400)
                         for i in [pub.ACTIVITY,pub.CFG['parent_activity'],*pub.CFG['limited_activities'],3500407]]
        visibility=patch.object(generator,'get_activity_list',side_effect=lambda *a,**kw:pub.activity_policy([dict(r) for r in self.activities]))
        visibility.start();self.addCleanup(visibility.stop);self.activity_patch=visibility
        self.native_tasks=[t for t in TASKS.values() if t['id'] in pub.LIMITED_TASKS or 350001<=t['condition']<=350005]
        for t in self.native_tasks:
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                            (t['id'],t['type'],t['condition'],t['need'],t['activity_id'],json.dumps(t['additional_parameter']),json.dumps(t['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,t['id'],t['type']))
        reward_ids={i for t in self.native_tasks for i,n in t['reward']}|{i for l in pub.LEVELS.values() for i,n in l['rewards']}
        for iid in reward_ids|{1,2,4,14}:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,1 if iid in (1,2,4,14) else ITEM_TYPES[iid][0]))
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        self.conn.resource_version='229'

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('summer_pub_record','task','claim_ledger','currency','material','users','hero','chapter','stage_sub')}

    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']
    def record(self):return pub.read(self.db,UID)
    def cook(self,dish=10100,state=2,activity=pub.ACTIVITY):return self.call(89006,dict(activity_id=activity,stage_id=dish,stage=state),89007)
    def ball(self,level=4030104,hard=False,result=1,ills=None):
        sub=pub.LEVELS[level]['stages'][int(hard)]
        return self.call(89002,dict(activity_id=pub.ACTIVITY,stage_id=level,sub_stage_id=sub,result=result,
                                  illustrated=pub.BALLS[sub]['illustrations'] if ills is None else ills),89003)

    def enter(self,level=4030101,*,real=False,activity=pub.ACTIVITY,kind=87):
        cfg=pub.LEVELS[level];stage=cfg['stages'][0]
        if pub.CFG['battles'][str(stage)]['tag']==2:
            packet,response=self.call(54034,dict(stage_id=stage,activity_id=0),54035)
        else:
            packet,response=self.call(54030,dict(common_info=dict(type=kind,dest=stage if real else level,activity_id=activity,
                                            hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def finish(self,result=1):
        session=mw.get_battle_session(self.bid)
        if not session.get('is_story'):session['result_data']=dict(result=result,info=dict(battle_time=67000))
        return self.call(54032,dict(battle_id=self.bid),54033)

    def battle(self,level=4030101):
        self.assertEqual(self.enter(level)[0]['result'],0)
        packet,response=self.finish();self.assertEqual(packet['result'],0)
        return packet,response

    def dishes(self):
        for dish,cfg in pub.COOKS.items():
            if self.record()['cook_stages'].get(str(dish),0)==2:continue
            if pub.cook_unlocked(self.record(),dish):
                self.assertEqual(self.cook(dish,1)[0]['result'],0);self.assertEqual(self.cook(dish,2)[0]['result'],0)

    def route(self,hard=True):
        for level,cfg in pub.LEVELS.items():
            if cfg['type']==0:continue
            if cfg['type']==1:self.battle(level)
            else:
                self.assertEqual(self.ball(level)[0]['result'],0)
                if hard:self.assertEqual(self.ball(level,hard=True)[0]['result'],0)
            self.dishes()

    def intro(self):
        for level in [4030101,4030102,4030103]:self.battle(level)

    def test_empty_snapshot_readonly_no_capture_uid_special_case(self):
        before=self.state();packet=codec.decode(generator.gen_payload(89011,uid=UID,db=self.db),'sc_89011')
        self.assertEqual(packet,dict(time_state=1));self.assertEqual(self.state(),before)
        self.assertEqual(pub.payload(self.db,2174928301),pub.payload(self.db,UID))

    def test_bad_activity_ids_states_cross_substage_and_locked_requests_no_write(self):
        before=self.state()
        for dish,state,activity in [(999,2,pub.ACTIVITY),(10100,0,pub.ACTIVITY),(10100,3,pub.ACTIVITY),(10100,2,1),(10800,2,pub.ACTIVITY)]:
            self.assertNotEqual(self.cook(dish,state,activity)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.ball()[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.call(89004,dict(activity_id=pub.ACTIVITY,ill_id=40301),89005)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.call(89008,dict(activity_id=1),89009)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_native_logical_combat_and_real_story_ids_save_logical_progress(self):
        self.battle();self.assertEqual(self.record()['battle_stages'],[4030101]);self.assertEqual(self.balance(53164),1)
        self.battle(4030102);self.battle(4030103)
        self.assertEqual(self.record()['battle_stages'],[4030101,4030102,4030103]);self.assertEqual(self.value(40301001),1)

    def test_real_id_alias_but_wrong_type_and_activity_rejected(self):
        before=self.state()
        for kwargs in [dict(kind=1),dict(activity=1)]:self.assertNotEqual(self.enter(**kwargs)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.enter(real=True)[0]['result'],0);self.assertEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.record()['battle_stages'],[4030101])

    def test_story_cannot_skip_or_use_combat_entry(self):
        before=self.state()
        for stage in [5270202,5270209,5270204,5270208,5270201]:
            self.assertNotEqual(self.call(54034,dict(stage_id=stage,activity_id=0),54035)[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.assertNotEqual(self.enter(4030201)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_cooking_state_is_monotonic_and_only_cg_completes_cook_level(self):
        self.battle();self.assertEqual(self.cook(state=1)[0]['result'],0)
        self.assertFalse(pub.passed(self.record(),4030001));self.assertEqual(self.value(40304001),1)
        self.cook(state=2);self.assertTrue(pub.passed(self.record(),4030001))
        self.assertNotEqual(self.cook(state=1)[0]['result'],0);self.assertEqual(self.record()['cook_stages']['10100'],2)
        self.assertEqual(self.cook()[0]['result'],0)

    def test_ball_substage_illustrations_and_hard_gate(self):
        self.intro();before=self.state()
        self.assertNotEqual(self.ball(hard=True)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.ball(ills=[40314])[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.ball();self.assertEqual(packet['result'],0);self.assertEqual(self.balance(53165),1)
        self.assertNotIn(89011,response.scs);self.assertIn(17023,response.scs)
        self.assertEqual(self.ball(hard=True)[0]['result'],0);self.assertEqual(self.balance(53165),1)
        self.assertTrue(pub.passed(self.record(),4030104))

    def test_ball_failure_does_not_unlock_collect_grant_or_advance(self):
        self.intro();before=self.state();packet,response=self.ball(result=2)
        self.assertEqual(packet['result'],0);self.assertEqual(self.state(),before);self.assertNotIn(28007,response.scs)

    def test_illustration_requires_collection_and_view_only_updates_read_flag(self):
        self.intro();self.ball();packet,response=self.call(89004,dict(activity_id=pub.ACTIVITY,ill_id=40301),89005)
        self.assertEqual(packet['result'],0);self.assertEqual(self.record()['illustrated']['40301'],2);self.assertNotIn(89011,response.scs)
        before=self.state();self.call(89004,dict(activity_id=pub.ACTIVITY,ill_id=40301),89005);self.assertEqual(self.state(),before)

    def test_full_native_route_all_normal_hard_and_recipes_complete_tasks(self):
        self.route();record=self.record()
        self.assertTrue(all(pub.passed(record,l) for l in pub.LEVELS))
        self.assertEqual(len(record['stage_list']),32);self.assertEqual(len(record['cook_stages']),8)
        for t in self.native_tasks:
            if t['condition'] in (350001,350002,350003,350005,501):self.assertEqual(self.value(t['id']),t['need'],t['id'])
        self.assertEqual(self.value(40306006),26)

    def test_drop_rewards_exactly_once_and_no_ordinary_growth(self):
        before=self.state();self.route();after=self.state()
        for table in ['users','hero','chapter','stage_sub']:self.assertEqual(after[table],before[table],table)
        expected={}
        for cfg in pub.LEVELS.values():
            for iid,count in cfg['rewards']:expected[iid]=expected.get(iid,0)+count
        for iid,count in expected.items():self.assertEqual(self.balance(iid),count,iid)
        self.battle();self.ball();self.ball(hard=True)
        for iid,count in expected.items():self.assertEqual(self.balance(iid),count,iid)

    def test_battle_fail_and_same_session_replay_do_not_duplicate_progress(self):
        for outcome in [2,3]:
            self.enter();before=self.state();self.assertEqual(self.finish(outcome)[0]['result'],0);self.assertEqual(self.state(),before)
        self.battle();before=self.state();self.assertEqual(self.finish()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_transactional_task_resource_and_ack_failure_rollback_retry(self):
        self.intro();self.ball();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode;before=self.state()
        for name in ['sc_28007','sc_89003']:
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==name and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.ball(4030105)[0]['result'],0)
            self.assertEqual(self.state(),before)
        original=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else original(cmd,*a,**kw)):
            self.assertNotEqual(self.ball(4030105)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.ball(4030105)[0]['result'],0)

    def test_battle_task_resource_ack_preparation_is_atomic(self):
        self.enter();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_54033' and d.get('result')==0 else encode(n,d)):
            self.assertNotEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertEqual(self.finish()[0]['result'],0)

    def test_cook_time_and_view_ack_failures_do_not_commit(self):
        self.intro();self.ball();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for cmd,data,sc in [(89006,dict(stage_id=10100,stage=2),89007),(89008,{},89009),(89004,dict(ill_id=40301),89005)]:
            before=self.state();data['activity_id']=pub.ACTIVITY
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result')==0 else encode(n,d)):
                self.assertNotEqual(self.call(cmd,data,sc)[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_required_subscriber_failure_rolls_back(self):
        self.intro();before=self.state();handlers=list(bus._handlers[Events.SUMMER_PUB_PROGRESS])
        def fail(*a,**kw):raise RuntimeError('isolated subscriber')
        with patch.dict(bus._handlers,{Events.SUMMER_PUB_PROGRESS:handlers+[fail]}):self.assertNotEqual(self.ball()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.ball()[0]['result'],0)

    def test_toggle_no_client_time_parameter_and_cold_snapshot(self):
        for expected in [2,1]:
            packet,response=self.call(89008,dict(activity_id=pub.ACTIVITY),89009)
            self.assertEqual(packet['result'],0);self.assertEqual(self.record()['time_state'],expected)
            self.assertEqual(response.scs,[89009])
            self.assertEqual(codec.decode(generator.gen_payload(89011,uid=UID,db=self.db),'sc_89011')['time_state'],expected)

    def test_cold_and_fresh_task_recovery_without_rewards(self):
        self.route();self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        balances=self.db.query('SELECT * FROM material')
        generator.gen_payload(28001,uid=UID,db=self.db);self.assertEqual(self.value(40301015),1);self.assertEqual(self.value(40306006),26)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        with patch.object(new_player_service,'ensure_tasks'):packet=new_player_service.payload(self.db,UID,28001)
        self.assertEqual(next(t['progress'] for t in packet['assignment_list'] if t['id']==40301015),1)
        self.assertEqual(self.db.query('SELECT * FROM material'),balances)

    def test_resident_limited_policy_claims_and_main_rewards(self):
        self.activities=[r for r in self.activities if r['activity_id']!=pub.CFG['parent_activity']]
        self.route();self.assertEqual(self.value(40301015),1)
        self.assertTrue(all(self.value(t)==0 for t in pub.LIMITED_TASKS))
        self.db.execute('UPDATE task SET progress=999 WHERE uid=?',(UID,));before=self.state()
        self.assertNotEqual(self.call(28010,dict(id=40302001),28011)[0]['result'],0)
        self.assertNotEqual(self.call(28014,dict(id_list=[40302001]),28015)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.call(28010,dict(id=40301015),28011)[0]['result'],0)
        self.assertTrue(pub.visible_tasks(self.db,UID,[dict(task_id=40308001)]))  # resident skin DLC is separate

    def test_bad_json_is_not_silently_reset_and_uid_isolation(self):
        self.battle();self.db.execute("UPDATE summer_pub_record SET cook_stages='broken' WHERE uid=?",(UID,));before=self.state()
        with self.assertRaises(Exception):generator.gen_payload(89011,uid=UID,db=self.db)
        self.assertNotEqual(self.call(89008,dict(activity_id=pub.ACTIVITY),89009)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(pub.payload(self.db,UID+1),dict(stage_list=[],battle_stage_list=[],cook_stage_list=[],illustrated=[],time_state=1))

    def test_contract_export_and_static_login_state_override(self):
        from export_summer_pub_contract import build
        self.assertEqual(pub.CFG,build())
        import login
        class Policy:
            def __init__(self,*a):pass
            def allows(self,cmd):return cmd==89011
            def report(self,log):pass
            def allows_empty_default(self,*a):return True
        raw=[];calls=[]
        class Generator:
            build_frame=staticmethod(generator.build_frame)
            @staticmethod
            def gen_payload(cmd,**kw):
                calls.append(cmd);return generator.gen_payload(cmd,**kw)
        capture=codec.encode('sc_89011',dict(time_state=2,battle_stage_list=[4030308]))
        self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic) VALUES(1,89011,?,?,0)',
                        (generator.build_frame(89011,capture)[:11],capture))
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
        packets=[p for p in raw if struct.unpack('>H',p[5:7])[0]+(65536 if p[4] else 0)==89011]
        self.assertEqual(len(packets),1);self.assertEqual(calls.count(89011),1)
        self.assertEqual(codec.decode(packets[0][11:],'sc_89011'),dict(time_state=1))

    def test_actual_catalog_resident_policy_under_229_and_311(self):
        import res_version_manager as versions
        self.activity_patch.stop()
        for aid in [pub.ACTIVITY,pub.CFG['parent_activity'],*pub.CFG['limited_activities'],3500407]:
            self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,?,?,1,?,4,35)',
                            (UID,aid,0 if aid==pub.CFG['parent_activity'] else 1,NOW+86400))
        for version in ['229','311']:
            with versions.connection_version(version):
                rows=generator.get_activity_list(self.db,UID)
                self.assertFalse(pub.limited_context(rows))
                self.assertEqual({r['state'] for r in rows if r['activity_id'] in pub.CFG['limited_activities']},{0})

    def test_story_ack_failure_leaves_no_session_and_retry_works(self):
        self.battle();before=self.state();sessions=set(mw._BATTLE_SESSIONS);ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_54035' and d.get('result')==0 else encode(n,d)):
            self.assertNotEqual(self.enter(4030102)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(set(mw._BATTLE_SESSIONS),sessions)
        self.battle(4030102)

    def test_receipt_and_normal_marker_prevent_repeat_first_drops(self):
        self.intro();self.ball();balance=self.balance(53165)
        rec=self.record();rec['stage_list']=[];pub.save(self.db,UID,rec)
        self.ball();self.assertEqual(self.balance(53165),balance)


if __name__=='__main__':unittest.main(verbosity=2)
