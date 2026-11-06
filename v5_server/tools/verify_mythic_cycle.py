"""A90: controlled +08 clock, memory AccountDB/Core/codec, no real saves."""
import ast
import copy
import datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
from verify_boss_battle_period import BossBattlePeriodTests
from verify_item_use_plan_guard import PlanTests
import account_db
import codec
import hero_codec
import middleware as mw
import mythic_cycle as cycle
import mythic_affix_cfg as affix
import weekly_challenge_service as weekly
from event_bus import bus

ROOT=Path(__file__).resolve().parents[2]
stamp=lambda text:int(datetime.datetime.fromisoformat(text+'+08:00').timestamp())
MON=stamp('2026-11-02T05:00:00');THU=stamp('2026-11-05T05:00:00')


class MythicCycleTests(MemoryTests):
    def setUp(self):
        super().setUp();self.now=THU-60
        frozen=patch('time.time',side_effect=lambda:self.now);frozen.start();self.addCleanup(frozen.stop)
        sessions=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);sessions.start();self.addCleanup(sessions.stop)
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        for iid in (1,2,4):self.db.set_currency(UID,iid,1000)
        self.key='mythic.final_cycle_v1.'+str(UID)

    def seed(self,updated=None):
        self.db.upsert('mythic_final_progress',UID,dict(now_difficulty=20,can_choose_list='[1,20,21]',
            receive_reward_list='[1]',clear_list='[1,2]',challenge_info_json='[{"team_id":1,"clear_state":1,"use_time":45}]',
            is_new_difficulty=1,update_ts=self.now if updated is None else updated),keys=('uid',))

    def read(self):return self.db.get_mythic_final_progress(UID)
    def marker(self):return int(self.db.query('SELECT value FROM server_global_config WHERE key=?',(self.key,))[0]['value'])
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
            ('users','currency','material','hero','mythic_final_progress','mythic_progress_reward','mythic_progress_partition','server_global_config')}

    def enter(self):
        self.db.set_mythic_final_difficulty(UID,1)
        packet,response=self.call(54030,dict(common_info=dict(type=35,dest=3028001,activity_id=1,
            hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        self.assertEqual(packet['result'],0);self.bid=self.conn.battle_id[0]
        mw.get_battle_session(self.bid)['result_data']=dict(result=1,info=dict(battle_time=45000))
        return response

    def finish(self):return self.call(54032,dict(battle_id=self.bid),54033)
    def assert_rejected(self,packet,response,restore=True):
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(packet['battle_result']['result'],3)
        self.assertEqual(packet['battle_result']['battle_id'],self.bid)
        self.assertEqual(response.scs,[44007,44023,54033] if restore else [54033])
        self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertNotIn(17023,response.scs)

    def test_exact_monday_thursday_boundaries_and_shared_countdown(self):
        for boundary in (MON,THU,MON+604800,THU+604800):
            for delta in (-1,0,60,43200):
                with self.subTest(boundary=boundary,delta=delta):
                    now=boundary+delta;start,end=cycle.bounds(now)
                    self.assertLessEqual(start,now);self.assertGreater(end,now)
                    self.assertIn(end-start,(3*86400,4*86400))
                    self.assertEqual(cycle.bounds(end)[0],end)
                    self.assertEqual(weekly.get_next_semiweekly_5am(now),end)
                    self.assertEqual(affix.get_mythic_rotation(now)['next_refresh_timestamp'],end)
        self.assertEqual(cycle.bounds(MON-1),(THU-604800,MON))
        self.assertEqual(cycle.bounds(THU),(THU,MON+604800))

    def test_legacy_adoption_uses_real_boundary_and_preserves_current_progress(self):
        for now,updated,keep in ((MON+60,MON-11*3600,False),(THU+60,THU+30,True),
                                 (THU+60,THU-1,False),(THU+43260,THU+43230,True)):
            with self.subTest(now=now,updated=updated):
                self.now=now
                # Restore the absence of a migration marker in this memory-only case.
                self.db.execute('DELETE FROM server_global_config WHERE key=?',(self.key,))
                self.seed(updated);data=self.read()
                self.assertEqual(bool(data['clear_list']),keep);self.assertEqual(bool(data['receive_reward']),keep)
                self.assertEqual(bool(data['challenge_info']),keep)
                self.assertEqual(data['can_choose_list'],[1,20,21]);self.assertEqual(data['now_difficulty'],20)
                self.assertEqual(self.marker(),cycle.bounds(now)[0])
                before=self.state();self.assertEqual(self.read(),data);self.assertEqual(self.state(),before)

    def test_thursday_real_clear_claim_and_cold_payload_survive_repeated_reads(self):
        self.now=THU+60;self.db.set_mythic_final_difficulty(UID,1)
        self.db.record_mythic_final_clear(UID,1,1,45)
        self.assertTrue(self.db.claim_mythic_final_reward(UID,1))
        expected=self.read();self.assertEqual(expected['clear_list'],[1]);self.assertEqual(expected['receive_reward'],[1])
        before=self.state()
        for now in (THU+61,THU+43200,THU+86400):
            self.now=now;self.assertEqual(self.read(),expected);self.assertEqual(self.state(),before)
            payload=codec.encode('sc_44023',weekly.get_mythic_data(UID,self.db)[4])
            cold=codec.decode(payload,'sc_44023')
            self.assertEqual(cold['clear_list'],[1]);self.assertEqual(cold['receive_reward'],[1])

    def test_business_update_timestamp_cannot_hide_cross_period_refresh(self):
        self.seed();self.read();self.assertEqual(self.marker(),MON)
        self.now=THU+1
        self.db.execute('UPDATE mythic_final_progress SET update_ts=? WHERE uid=?',(self.now,UID))
        progress=self.read();self.assertFalse(progress['clear_list']);self.assertFalse(progress['receive_reward'])
        self.assertFalse(progress['challenge_info']);self.assertFalse(progress['is_new_difficulty'])
        self.assertEqual(self.marker(),THU)
        self.db.set_mythic_final_difficulty(UID,1);self.db.record_mythic_final_clear(UID,1)
        self.assertEqual(self.read()['clear_list'],[1]);self.assertEqual(self.marker(),THU)

    def test_clock_rewind_never_moves_marker_back_or_clears_current_progress(self):
        self.now=THU+60;self.seed();expected=self.read();before=self.state()
        self.now=MON+60
        self.assertEqual(self.read(),expected);self.assertEqual(self.state(),before);self.assertEqual(self.marker(),THU)

    def test_clear_and_marker_failures_roll_back_then_retry_once(self):
        self.seed();self.read();self.now=THU+60
        for fault in ('clear','marker','marker_read'):
            with self.subTest(fault=fault):
                before=self.state();execute=self.db.execute;query=self.db.query
                def fail_write(sql,*a,**kw):
                    result=execute(sql,*a,**kw)
                    if (fault=='clear' and sql.startswith('UPDATE mythic_final_progress')) or (fault=='marker' and 'server_global_config' in sql):
                        raise RuntimeError('isolated '+fault+' after write')
                    return result
                def fail_read(sql,*a,**kw):
                    if sql=='SELECT value FROM server_global_config WHERE key=?':raise RuntimeError('isolated marker read')
                    return query(sql,*a,**kw)
                with patch.object(self.db,'query',side_effect=fail_read) if fault=='marker_read' else patch.object(self.db,'execute',side_effect=fail_write):
                    with self.assertRaises(RuntimeError):self.read()
                self.assertEqual(self.state(),before)
        self.assertFalse(self.read()['clear_list']);self.assertEqual(self.marker(),THU)
        before=self.state();self.read();self.assertEqual(self.state(),before)

    def test_nested_refresh_failure_retains_callers_unrelated_write(self):
        self.seed();self.read();self.now=THU+60;before=self.state();set_config=self.db.set_system_config
        def fail(*a,**kw):set_config(*a,**kw);raise RuntimeError('isolated nested marker')
        with self.db.transaction():
            self.db.execute("UPDATE users SET nick='outer-kept' WHERE uid=?",(UID,))
            with patch.object(self.db,'set_system_config',side_effect=fail):
                with self.assertRaises(RuntimeError):self.read()
        for t in ('mythic_final_progress','server_global_config'):self.assertEqual(self.state()[t],before[t])
        self.assertEqual(self.db.get('users',UID)['nick'],'outer-kept')

    def test_empty_new_state_and_multiweek_refresh_are_uid_isolated(self):
        empty=self.read();self.assertFalse(empty['clear_list']);self.assertEqual(self.marker(),MON)
        self.seed();self.now+=604800*5
        self.db.upsert('mythic_final_progress',UID+1,dict(clear_list='[30]',update_ts=self.now),keys=('uid',))
        execute=self.db.execute
        with patch.object(self.db,'execute',wraps=execute) as writes:self.assertFalse(self.read()['clear_list'])
        self.assertEqual(sum(c.args[0].startswith('UPDATE mythic_final_progress') for c in writes.call_args_list),1)
        self.assertEqual(self.db.get('mythic_final_progress',UID+1)['clear_list'],'[30]')

    def test_real_core_settlement_and_receipt_replay_in_same_period(self):
        self.enter();packet,response=self.finish();self.assertEqual(packet['result'],0)
        self.assertEqual(self.read()['clear_list'],[1]);self.assertIn(44023,response.scs)
        before=self.state();packet,response=self.finish()
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[54033]);self.assertEqual(self.state(),before)

    def test_old_win_loss_quit_never_touch_new_progress_or_emit_stage_events(self):
        for result in (1,2,3):
            with self.subTest(result=result):
                self.now=THU-60;self.enter();session=mw.get_battle_session(self.bid)
                session['result_data']['result']=result;self.now=THU+60
                self.db.set_mythic_final_difficulty(UID,2);self.db.record_mythic_final_clear(UID,2)
                self.db.claim_mythic_final_reward(UID,1);before=self.state()
                with patch.object(bus,'emit',wraps=bus.emit) as events:self.assert_rejected(*self.finish())
                self.assertEqual(events.call_count,0);self.assertEqual(self.state(),before)
                self.assertEqual(self.read()['clear_list'],[1,2])

    def test_missing_future_or_foreign_session_timestamp_rejects(self):
        for change in (dict(ts=0),dict(ts=THU+604800),dict(uid=UID+1)):
            self.enter();mw.get_battle_session(self.bid).update(change)
            before=self.state();self.assert_rejected(*self.finish());self.assertEqual(self.state(),before)

    def test_direct_engine_also_checks_period_before_any_events(self):
        self.enter();self.now=THU+60
        with patch.object(bus,'emit',wraps=bus.emit) as events:
            with self.assertRaises(cycle.MythicPeriodExpired):
                mw._settle_frames(self.conn.to_handler_ctx(),UID,self.bid,1,3028001,heroes=[1084],stage_type=35,activity_id=1)
        self.assertEqual(events.call_count,0)

    def test_boundary_crossed_during_initialization_never_registers_session(self):
        build=mw.battle_payload.build_sc_54003_dynamic;before=set(mw._BATTLE_SESSIONS)
        def crossed(*a,**kw):
            payload=build(*a,**kw);self.now=THU;return payload
        with patch('battle_payload.build_sc_54003_dynamic',side_effect=crossed):
            packet,response=self.call(54030,dict(common_info=dict(type=35,dest=3028001,activity_id=1,hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54031])
        self.assertEqual(set(mw._BATTLE_SESSIONS),before)

    def test_boundary_crossed_during_engine_rolls_back_sql_and_pending_frames(self):
        self.enter();engine=mw._settle_frames;before=self.state()
        def crossed(*a,**kw):
            result=engine(*a,**kw);self.now=THU+60;return result
        with patch.object(mw,'_settle_frames',side_effect=crossed):self.assert_rejected(*self.finish())
        self.assertEqual(self.state()['currency'],before['currency']);self.assertFalse(self.read()['clear_list'])

    def test_committed_old_receipt_is_replayed_without_old_snapshots(self):
        self.enter();self.assertEqual(self.finish()[0]['result'],0)
        self.now=THU+60;self.db.set_mythic_final_difficulty(UID,2)
        self.db.record_mythic_final_clear(UID,2);before=self.state()
        packet,response=self.finish();self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[54033])
        self.assertEqual(self.state(),before);self.assertEqual(self.read()['clear_list'],[1,2])

    def test_failed_recovery_encoding_still_returns_complete_error_quit(self):
        self.enter();self.now=THU+60;ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda name,data:None if name=='sc_44023' else encode(name,data)):
            self.assert_rejected(*self.finish(),restore=False)


if __name__=='__main__':
    if '--before' in sys.argv:
        base=ROOT/'archive/mythic_cycle_20261104/before/v5_server'
        for module,name in ((account_db,'get_mythic_final_progress'),(weekly,'get_next_semiweekly_5am'),(affix,'get_semiweekly_cycle_info'),
                            (mw,'_settle_frames'),(mw,'h_54030'),(mw,'_settle_battle_session')):
            path=base/(module.__name__+'.py');tree=ast.parse(path.read_text('utf-8-sig'))
            parent=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AccountDB') if module is account_db else tree
            node=next(n for n in parent.body if isinstance(n,ast.FunctionDef) and n.name==name)
            ns=vars(module);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
            if module is account_db:setattr(account_db.AccountDB,name,ns[name])
        mw.CORE_HANDLERS[54030]=mw.h_54030
    suite=unittest.TestSuite(MythicCycleTests(n) for n in MythicCycleTests.__dict__ if n.startswith('test_'))
    if '--before' not in sys.argv:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(BossBattlePeriodTests))
        suite.addTest(PlanTests('test_registered_beacon_can_succeed_with_no_usable_cfg_or_drops'))
        suite.addTest(PlanTests('test_beacon_missing_handler_or_write_after_failure_does_not_consume'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
