"""A86: real 54030 -> session -> 54032 settlement and native codec, memory only."""
import ast
import copy
import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
from verify_settlement_serial import SerialTests
import codec
import core
import enemy_kill_service
import hero_codec
import middleware as mw
import boss_challenge_cycle as cycle
import weekly_challenge_service as weekly
from event_bus import bus, Events

ROOT=Path(__file__).resolve().parents[2]
THU=int(datetime.datetime(2026,11,5,5,tzinfo=cycle.ZONE).timestamp())


class BossBattlePeriodTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.now=THU-600
        frozen=patch('time.time',side_effect=lambda:self.now);frozen.start();self.addCleanup(frozen.stop)
        sessions=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);sessions.start();self.addCleanup(sessions.stop)
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        for iid in (1,2,4):self.db.set_currency(UID,iid,1000)
        for iid in (1,2,4):self.db.execute('INSERT OR IGNORE INTO item_catalog(id,type) VALUES(?,1)',(iid,))
        self.db.check_and_lazy_refresh_boss_challenge(UID,self.now)
        self.logs=[]
        self.conn.log=lambda *args,**kwargs:self.logs.append(str(args))
        self.conn.to_handler_ctx().log=self.conn.log

    def read(self):
        return weekly.get_boss_challenge_data(UID,self.db,strict=True)

    def enter(self,kind=2,legacy=False):
        self.db.set_boss_challenge_mode(UID,101 if kind==2 else 4)
        _,normal,advance=self.read()
        if kind==2:
            boss=advance['boss_list'][0]['id']
            pool=weekly.get_boss_catalog().get('advance_pool') or weekly.get_boss_catalog()['advance_boss_pool']
            if legacy:
                counts={int(b['id']):sum(pool[str(b['id'])]['stage_id']==pool[str(other['id'])]['stage_id'] for other in advance['boss_list']) for b in advance['boss_list']}
                boss=next(bid for bid,n in counts.items() if n==1)
            dest=int(pool[str(boss)]['stage_id']);activity=0;stage_type=100
        else:
            boss=normal['boss_challenge_list'][0]['group_id']
            dest=weekly.get_boss_catalog()['stage_groups'][str(boss)]['stages'][0];activity=0;stage_type=10
        self.stage=dest;self.boss=boss;self.kind=kind
        if legacy:
            bid=900000+len(mw._BATTLE_SESSIONS)
            mw.register_battle_session(bid,UID,dest=dest,stage_type=stage_type,activity_id=activity,heroes=[1084])
        else:
            packet,response=self.call(54030,dict(common_info=dict(type=stage_type,dest=dest,activity_id=activity,index=boss if kind==2 else 0,
                                  battle_times=1,hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
            self.assertEqual(packet['result'],0,(response.scs,self.logs[-4:]))
            bid=self.conn.to_handler_ctx().battle_id[0]
            self.assertIn(54007,response.scs)
        self.bid=bid
        session=mw.get_battle_session(bid)
        session['result_data']=dict(result=1,info=dict(battle_time=45000))
        return session

    def finish(self):return self.call(54032,dict(battle_id=self.bid),54033)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('users','currency','material','hero','hero_piece','chapter','stage_sub','task','achievement',
                 'boss_challenge_progress','boss_challenge_normal','boss_challenge_advance',
                 'boss_challenge_scores','boss_challenge_claimed_rewards')}

    def assert_rejected(self,packet,response,refresh=True):
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(packet['battle_result']['result'],3)
        self.assertEqual(packet['battle_result']['battle_id'],self.bid)
        self.assertFalse(packet['battle_result'].get('all_drop_list'))
        self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertNotIn(17023,response.scs)
        if refresh:self.assertEqual(response.scs,[45201,45001,45101,54033])
        else:self.assertEqual(response.scs,[54033])

    def test_real_entry_captures_cycle_and_mode_for_normal_and_advance(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                session=self.enter(kind)
                context=session['boss_context']
                self.assertEqual({k:context[k] for k in ('cycle_start','mode','branch')},dict(cycle_start=THU-cycle.WEEK,mode=kind,branch=4 if kind==1 else 101))
                self.assertEqual(session['ts'],THU-600)

    def test_same_cycle_real_settlement_and_replay_do_not_grant_twice(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                self.enter(kind)
                with patch.object(bus,'emit',wraps=bus.emit) as events:
                    packet,response=self.finish()
                self.assertEqual(packet['result'],0)
                self.assertEqual(packet['battle_result']['result'],1)
                self.assertEqual(sum(c.args[0]==Events.STAGE_PASS for c in events.call_args_list),1)
                before=self.state();repeated,replay=self.finish()
                self.assertEqual(repeated,packet)
                self.assertEqual(replay.scs,[54033]);self.assertEqual(self.state(),before)
                table='boss_challenge_normal' if kind==1 else 'boss_challenge_advance'
                self.assertTrue(self.db.query('SELECT * FROM '+table+' WHERE uid=?',(UID,)))

    def test_old_win_after_refresh_never_enters_settlement_bus_or_enemy_counter(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                self.now=THU-600;self.enter(kind);self.now=THU+60;self.read()
                # Preserve an independently earned new-cycle score/lock.
                bid=self.read()[2]['boss_list'][0]['id']
                self.db.upsert('boss_challenge_advance',UID,dict(boss_id=bid,score=12345,used_heroes='[1011]'),keys=('uid','boss_id'))
                before=self.state()
                with patch.object(mw,'_settle_frames',wraps=mw._settle_frames) as settle, \
                     patch.object(bus,'emit',wraps=bus.emit) as events, \
                     patch.object(enemy_kill_service,'record_battle',wraps=enemy_kill_service.record_battle) as enemies, \
                     patch.object(mw,'pop_battle_result',side_effect=AssertionError('expired must not poll UDP')):
                    packet,response=self.finish()
                self.assert_rejected(packet,response)
                self.assertEqual((settle.call_count,events.call_count,enemies.call_count),(0,0,0))
                self.assertEqual(self.state(),before)
                self.assert_rejected(*self.finish())

    def test_expired_loss_and_quit_are_not_success_or_failure_events(self):
        self.enter()
        self.now=THU
        for result in (2,3):
            with self.subTest(result=result):
                mw.get_battle_session(self.bid)['result_data']=dict(result=result,info={})
                with patch.object(bus,'emit',wraps=bus.emit) as events:
                    self.assert_rejected(*self.finish())
                self.assertEqual(events.call_count,0)

    def test_legacy_ts_without_snapshot_is_accepted_only_in_its_week(self):
        self.enter(legacy=True)
        packet,_=self.finish();self.assertEqual(packet['result'],0)
        self.enter(legacy=True);self.now=THU
        self.assert_rejected(*self.finish())

    def test_missing_or_future_start_time_never_defaults_to_current_period(self):
        self.enter()
        for value in (None,self.now+1):
            with self.subTest(ts=value):
                mw.get_battle_session(self.bid)['ts']=value
                self.assert_rejected(*self.finish())

    def test_mode_or_branch_switch_rejects_even_within_same_week(self):
        for kind,new_mode in ((1,3),(2,102)):
            with self.subTest(kind=kind):
                self.enter(kind);self.db.set_boss_challenge_mode(UID,new_mode)
                before=self.state();self.assert_rejected(*self.finish())
                self.assertEqual(self.state(),before)

    def test_committed_old_receipt_replays_after_refresh_without_old_state_frames(self):
        self.enter();first,_=self.finish();self.now=THU+60;self.read()
        before=self.state()
        with patch.object(mw,'_settle_frames',side_effect=AssertionError('cached receipt must not settle again')):
            packet,response=self.finish()
        self.assertEqual(packet,first);self.assertEqual(response.scs,[54033])
        self.assertEqual(self.state(),before)

    def test_direct_engine_call_rejects_before_any_stage_events(self):
        session=self.enter();self.now=THU
        with patch.object(bus,'emit',wraps=bus.emit) as events:
            with self.assertRaises(cycle.BattlePeriodExpired):
                mw._settle_frames(self.conn.to_handler_ctx(),UID,self.bid,1,self.stage,heroes=[1084],stage_type=100)
        self.assertEqual(events.call_count,0)

    def test_boundary_crossed_during_engine_rolls_back_sql_and_does_not_cache_success(self):
        self.enter();self.now=THU-1
        assets={k:v for k,v in self.state().items() if not k.startswith('boss_')}
        real=mw._settle_frames
        def cross(*args,**kwargs):
            result=real(*args,**kwargs);self.now=THU;return result
        with patch.object(mw,'_settle_frames',side_effect=cross):
            self.assert_rejected(*self.finish())
        self.assertEqual({k:v for k,v in self.state().items() if not k.startswith('boss_')},assets)
        self.assertFalse(self.db.query('SELECT * FROM boss_challenge_advance'))
        self.assertFalse(self.conn.to_handler_ctx().pending_frames)

    def test_real_settlement_failure_rolls_back_then_normal_retry_succeeds(self):
        self.enter();before=self.state();real=mw._settle_frames
        def fail(*args,**kwargs):
            real(*args,**kwargs);raise RuntimeError('isolated after actual settlement')
        with patch.object(mw,'_settle_frames',side_effect=fail):
            packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.state(),before)
        self.assertEqual(self.finish()[0]['result'],0)

    def test_refresh_encoding_failure_still_returns_complete_quit_ack(self):
        self.enter();self.now=THU;ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda name,data:None if name=='sc_45001' else encode(name,data)):
            self.assert_rejected(*self.finish(),refresh=False)


if __name__=='__main__':
    if '--before' in sys.argv:
        path=ROOT/'archive/boss_battle_period_20261104/before/v5_server/middleware.py'
        tree=ast.parse(path.read_text('utf-8-sig'))
        for name in ('register_battle_session','h_54030','_settle_battle_session','_settle_frames'):
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            namespace=dict(vars(mw));exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
            setattr(mw,name,namespace[name])
        mw.CORE_HANDLERS[54030]=mw.h_54030
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BossBattlePeriodTests)
    if '--before' not in sys.argv:suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SerialTests))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
