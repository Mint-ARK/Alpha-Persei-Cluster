"""A88/A89: memory Core/native codec, config-backed relay and instance guards."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_mythic_cycle import MythicCycleTests
from verify_report_small_fixes import UID
from test_commit_recovery import deny_once
import account_db
import codec
import core
import generator
import middleware as mw
import mythic_final_service as service
import operations
import res_version_manager as versions
from event_bus import bus

ROOT=Path(__file__).resolve().parents[2]


class RelayTests(MythicCycleTests):
    def setUp(self):
        super().setUp();self.version='321';self.conn.resource_version=self.version
        self.db.set_mythic_final_difficulty(UID,20)

    def choose(self,diff):return self.call(44024,dict(difficulty_id=diff),44025)
    def reset(self):return self.call(44026,{},44027)
    def start(self,team=1,*,native=True,dest=None,activity=105):
        with versions.connection_version(self.version):
            route=service.stages(self.read()['now_difficulty'])
        if dest is None:dest=team if native else route[team-1]
        packet,response=self.call(54030,dict(common_info=dict(type=35,dest=dest,activity_id=activity,
              hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def complete(self,team=1,seconds=45,*,native=True,result=1):
        packet,response=self.start(team,native=native)
        self.assertEqual(packet['result'],0)
        mw.get_battle_session(self.bid)['result_data']=dict(result=result,info=dict(battle_time=seconds*1000))
        return self.finish()

    def progress(self,index,times):
        data=self.read();rows=data['challenge_info']
        # Bounded projection of InitFinalMythicUserData's next-team loop.
        actual=next((r['team_id'] for r in rows if r['clear_state']==0),0) if rows else 1
        self.assertEqual(actual,index)
        self.assertEqual([r['use_time'] for r in rows],times)
        return data

    def reject_old(self,bid):
        self.bid=bid;before=self.state()
        with patch.object(bus,'emit',wraps=bus.emit) as events:self.assert_rejected(*self.finish())
        self.assertEqual(events.call_count,0);self.assertEqual(self.state(),before)

    def test_native_105_team_dest_relay_and_cold_restore_all_versions(self):
        for version in ('229','311','321'):
            with self.subTest(version=version):
                self.version=version;self.conn.resource_version=version;self.reset();self.choose(20)
                self.assertEqual(self.complete(1)[0]['result'],0)
                first=self.progress(2,[45,0]);self.assertEqual(len(first['challenge_info']),2)
                if version=='229':self.assertEqual(mw.get_battle_session(self.bid)['mythic_context']['stage_id'],3028020)
                else:self.assertIn(mw.get_battle_session(self.bid)['mythic_context']['stage_id'],(3028051,3028053,3028055,3028057))
                with versions.connection_version(version):
                    cold=codec.decode(generator.gen_payload(44023,uid=UID,db=self.db),'sc_44023')
                self.assertEqual(cold['challenge_info'],first['challenge_info'])
                self.assertEqual(self.complete(2,60)[0]['result'],0)
                self.assertIn(20,self.progress(0,[45,60])['clear_list'])

    def test_physical_stage_id_resolves_against_selected_difficulty_in_311(self):
        self.assertEqual(self.complete(1,native=False)[0]['result'],0)
        self.progress(2,[45,0]);self.assertEqual(self.complete(2,60,native=False)[0]['result'],0)
        self.assertIn(20,self.read()['clear_list'])
        self.choose(30);self.assertEqual(self.complete(1,native=False)[0]['result'],0)
        self.assertEqual(self.read()['now_difficulty'],30);self.assertNotIn(30,self.read()['clear_list'])

    def test_failure_and_quit_keep_next_team_and_first_team_time(self):
        self.complete(1);before=self.read()
        for result in (2,3):
            with self.subTest(result=result):
                self.assertEqual(self.complete(2,result=result)[0]['result'],0)
                self.assertEqual(self.read(),before);self.progress(2,[45,0])
        self.assertEqual(self.complete(2,60)[0]['result'],0);self.progress(0,[45,60])

    def test_single_team_has_no_invented_second_team(self):
        self.choose(19);self.assertEqual(self.complete()[0]['result'],0)
        self.progress(0,[45]);self.assertIn(19,self.read()['clear_list'])
        packet,response=self.start(2);self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54031])

    def test_switch_drops_only_relay_and_same_selection_is_idempotent(self):
        self.choose(1);self.db.record_mythic_final_clear(UID,1);self.db.claim_mythic_final_reward(UID,1)
        self.choose(20);self.complete(1);before=self.read();generation=service.generation(self.db,UID)
        packet,response=self.choose(20);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[44025])
        self.assertEqual(self.read(),before);self.assertEqual(service.generation(self.db,UID),generation)
        packet,response=self.choose(30);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[44025,44023])
        self.assertFalse(self.read()['challenge_info']);self.assertEqual(self.read()['receive_reward'],[1]);self.assertEqual(self.read()['clear_list'],[1])
        with self.assertRaises(ValueError):self.db.record_mythic_final_clear(UID,30,2,60)
        self.assertNotIn(30,self.read()['clear_list']);self.assertEqual(self.complete(1)[0]['result'],0)
        self.assertNotIn(30,self.read()['clear_list']);self.progress(2,[45,0])

    def test_explicit_reset_only_ack_preserves_history_and_releases_first_team(self):
        self.choose(1);self.db.record_mythic_final_clear(UID,1);self.db.claim_mythic_final_reward(UID,1)
        self.choose(20);self.complete(1);generation=service.generation(self.db,UID)
        packet,response=self.reset();self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[44027])
        data=self.read();self.assertEqual(data['clear_list'],[1]);self.assertEqual(data['receive_reward'],[1]);self.assertFalse(data['challenge_info'])
        self.assertGreater(service.generation(self.db,UID),generation);self.assertEqual(self.start(1)[0]['result'],0)

    def test_switch_away_back_reset_and_beacon_invalidate_pending_battles(self):
        for kind in ('switch','reset','beacon'):
            with self.subTest(kind=kind):
                self.reset();self.choose(20);self.assertEqual(self.start()[0]['result'],0)
                old=self.bid;mw.get_battle_session(old)['result_data']=dict(result=1,info=dict(battle_time=45000))
                if kind=='switch':self.choose(30);self.choose(20)
                elif kind=='reset':self.reset()
                else:self.db.reset_mythic_by_beacon(UID)
                self.complete(1,55);self.reject_old(old);self.progress(2,[55,0])

    def test_out_of_order_duplicate_direct_and_invalid_difficulty_guards(self):
        before=self.state()
        for team in (0,2,3):
            with self.subTest(team=team):
                with self.assertRaises(ValueError):self.db.record_mythic_final_clear(UID,20,team,45)
                self.assertEqual(self.state(),before)
        for diff in (0,31,999999):
            packet,_=self.choose(diff);self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.db.record_mythic_final_clear(UID,20,1,45);before=self.state()
        self.db.record_mythic_final_clear(UID,20,1,999);self.assertEqual(self.state(),before)
        self.progress(2,[45,0])

    def test_bad_stage_activity_and_old_missing_identity_reject_before_events(self):
        for dest,activity in ((2,105),(999999,105),(3028020,105),(1,30)):
            with self.subTest(dest=dest,activity=activity):
                sessions=set(mw._BATTLE_SESSIONS);packet,response=self.start(dest=dest,activity=activity)
                self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54031]);self.assertEqual(set(mw._BATTLE_SESSIONS),sessions)
        self.start();mw.get_battle_session(self.bid)['mythic_context']=None
        self.reject_old(self.bid)

    def test_two_pending_first_team_battles_cannot_both_advance(self):
        self.start();old=self.bid;mw.get_battle_session(old)['result_data']=dict(result=1,info=dict(battle_time=45000))
        self.complete(1,55);self.reject_old(old);self.progress(2,[55,0])

    def test_completed_replay_stays_receipt_only_after_reset(self):
        self.complete(1);old=self.bid;self.reset();before=self.state();self.bid=old
        packet,response=self.finish();self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[54033]);self.assertEqual(self.state(),before)

    def test_completed_run_restarts_at_entry_and_registration_failure_rolls_back(self):
        self.complete(1);self.complete(2,60);before=self.state();generation=service.generation(self.db,UID)
        with patch.object(mw,'register_battle_session',side_effect=RuntimeError('isolated register failure')):
            packet,response=self.start()
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(service.generation(self.db,UID),generation)
        self.assertEqual(self.start()[0]['result'],0);self.assertFalse(self.read()['challenge_info'])
        self.assertIn(20,self.read()['clear_list']);self.assertGreater(service.generation(self.db,UID),generation)

    def test_registration_after_publish_or_commit_refusal_leaves_no_orphan_session(self):
        self.complete(1);self.complete(2,60);before=self.state();sessions=set(mw._BATTLE_SESSIONS)
        original=mw.register_battle_session;connection=self.db._conn()
        for kind in ('registered_then_raise','commit'):
            with self.subTest(kind=kind):
                def fail(*a,**kw):
                    original(*a,**kw)
                    if kind=='commit':deny_once(connection,'COMMIT')
                    else:raise RuntimeError('isolated error after publication')
                try:
                    with patch.object(mw,'register_battle_session',side_effect=fail):packet,response=self.start()
                finally:connection.set_authorizer(None)
                self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
                self.assertEqual(set(mw._BATTLE_SESSIONS),sessions)

    def test_write_and_generation_failures_roll_back_select_reset_beacon(self):
        self.complete(1);before=self.state();write=self.db.set_system_config
        def fail(key,value):
            write(key,value)
            if key.startswith('mythic.final_attempt_v1.'):raise RuntimeError('isolated generation write')
        with patch.object(self.db,'set_system_config',side_effect=fail):
            for change in (lambda:self.choose(30),self.reset):
                packet,_=change();self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
            with self.assertRaises(RuntimeError):self.db.reset_mythic_by_beacon(UID)
        self.assertEqual(self.state(),before)

    def test_required_ack_and_switch_state_failures_roll_back_generation_and_queue(self):
        self.complete(1);ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for name,change in (('sc_44025',lambda:self.choose(30)),('sc_44023',lambda:self.choose(30)),('sc_44027',self.reset)):
            with self.subTest(name=name):
                before=self.state()
                with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==name else encode(n,d)):
                    packet,_=change()
                self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)

    def test_required_settlement_progress_failure_rolls_back_then_retry(self):
        self.start();mw.get_battle_session(self.bid)['result_data']=dict(result=1,info=dict(battle_time=45000))
        before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_44023' else encode(n,d)):
            packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertEqual(self.state(),before)
        self.assertEqual(self.finish()[0]['result'],0);self.progress(2,[45,0])

    def test_queue_write_after_failure_rolls_back_settlement_before_retry(self):
        self.start();mw.get_battle_session(self.bid)['result_data']=dict(result=1,info=dict(battle_time=45000))
        before=self.state();execute=self.db.execute
        def fail(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if sql.startswith('INSERT INTO mythic_final_progress'):raise RuntimeError('isolated queue after write')
            return result
        with patch.object(self.db,'execute',side_effect=fail):packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.state(),before);self.assertEqual(self.finish()[0]['result'],0);self.progress(2,[45,0])

    def test_legacy_partial_queue_and_fresh_save_generator_share_complete_state(self):
        self.db.upsert('mythic_final_progress',UID,dict(now_difficulty=20,can_choose_list='[20]',
            challenge_info_json='[{"team_id":1,"clear_state":1,"use_time":45}]',update_ts=self.now),keys=('uid',))
        self.db.set_system_config('save.profile','new_player_v1');self.db.set_system_config('save.owner_uid',str(UID))
        self.progress(2,[45,0])
        cold=codec.decode(generator.gen_payload(44023,uid=UID,db=self.db),'sc_44023')
        self.assertEqual(cold['challenge_info'],self.read()['challenge_info'])


if __name__=='__main__':
    if '--before' in sys.argv:
        base=ROOT/'archive/mythic_relay_20261104/before/v5_server'
        for module,names in ((account_db,('get_mythic_final_progress','set_mythic_final_difficulty','reset_mythic_final_team','reset_mythic_by_beacon','record_mythic_final_clear')),
                             (mw,('register_battle_session','_settle_frames','h_54030','_settle_battle_session'))):
            path=base/(module.__name__+'.py');tree=ast.parse(path.read_text('utf-8-sig'))
            parent=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AccountDB') if module is account_db else tree
            for name in names:
                node=next(n for n in parent.body if isinstance(n,ast.FunctionDef) and n.name==name)
                ns=vars(module);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
                if module is account_db:setattr(account_db.AccountDB,name,ns[name])
        mw.CORE_HANDLERS[54030]=mw.h_54030
    suite=unittest.TestSuite(RelayTests(n) for n in RelayTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
