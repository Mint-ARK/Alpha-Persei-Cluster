"""A85/A87: explicit UTC+8 boundaries, migration, atomic resets and bounded BUS retries."""
import ast
import datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
from verify_boss_reward_guard import BossRewardTests
from verify_grant_failure_guard import GrantFailureTests
import account_db
import boss_challenge_cycle as clock
import codec
import generator
import lazy_timer
import timer_listeners
import weekly_challenge_service as weekly
from event_bus import bus, Events

ROOT=Path(__file__).resolve().parents[2]


def at(text):
    return int(datetime.datetime.fromisoformat(text).replace(tzinfo=clock.ZONE).timestamp())


WED=at('2026-11-04 06:13:19')
THU=at('2026-11-05 05:00:00')


class BossCycleTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.now=WED
        frozen=patch('time.time',side_effect=lambda:self.now);frozen.start();self.addCleanup(frozen.stop)

    def read(self,when=None):
        if when is not None:self.now=when
        return weekly.get_boss_challenge_data(UID,self.db,strict=True)

    def ids(self,data):
        return ([b['group_id'] for b in data[1]['boss_challenge_list']],
                [b['id'] for b in data[2]['boss_list']])

    def state(self):
        result={t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('boss_challenge_progress','boss_challenge_normal','boss_challenge_advance',
                 'boss_challenge_scores','boss_challenge_claimed_rewards','server_global_config')}
        if self.db.query("SELECT 1 FROM sqlite_master WHERE name='boss_challenge_exchange'"):
            result['boss_challenge_exchange']=self.db.query('SELECT * FROM boss_challenge_exchange ORDER BY rowid')
        return result

    def seed(self):
        data=self.read()
        bid=data[2]['boss_list'][0]['id'];gid=data[1]['boss_challenge_list'][0]['group_id']
        self.db.upsert('boss_challenge_progress',UID,dict(mode=2,advance_id=101,use_times=3),keys=('uid',))
        self.db.upsert('boss_challenge_advance',UID,dict(boss_id=bid,score=7000,used_heroes='[1075]'),keys=('uid','boss_id'))
        self.db.upsert('boss_challenge_scores',UID,dict(boss_id=bid,score=7000),keys=('uid','boss_id'))
        self.db.upsert('boss_challenge_normal',UID,dict(group_id=gid,finish_stage=1,used_heroes='[1084]'),keys=('uid','group_id'))
        self.db.claim_boss_challenge_reward(UID,2,7000)
        self.db.execute('INSERT OR REPLACE INTO boss_challenge_exchange(uid,goods_id,exchange_times,update_ts) VALUES(?,1,2,?)',(UID,self.now))

    def empty_week(self):
        for table in ('boss_challenge_normal','boss_challenge_advance','boss_challenge_scores','boss_challenge_claimed_rewards'):
            self.assertFalse(self.db.query('SELECT * FROM '+table),table)
        self.assertEqual(self.db.get_boss_challenge_progress(UID)['last_weekly_ts'],clock.bounds(self.now)[0])

    def reset_event(self,event=Events.WEEKLY_RESET_THU_5AM):
        fn=timer_listeners.on_boss_challenge_thu_reset if event==Events.WEEKLY_RESET_THU_5AM else timer_listeners.on_boss_challenge_daily_reset
        fn(self.conn.to_handler_ctx(),UID,cycle_start_ts=clock.bounds(self.now)[0],now_ts=self.now)

    def legacy(self,when):
        self.now=when
        start,_,daily=clock.bounds(when)
        self.db.upsert('boss_challenge_progress',UID,dict(mode=2,advance_id=101,cycle_id=clock.seed_at(start),
                       last_weekly_ts=start,last_daily_ts=daily,use_times=3),keys=('uid',))
        self.db.claim_boss_challenge_reward(UID,2,7000)
        self.db.upsert('boss_challenge_advance',UID,dict(boss_id=20001,score=7000),keys=('uid','boss_id'))

    def test_new_week_seed_changes_only_on_thursday_not_old_wednesday_boundary(self):
        self.seed();first=self.read();seed=self.db.get_boss_challenge_progress(UID)['cycle_id']
        for when in (WED,WED+1,THU-1):
            current=self.read(when)
            self.assertEqual(self.ids(current),self.ids(first))
            self.assertEqual(current[0]['next_refresh_time'],THU)
            self.assertEqual(self.db.get_boss_challenge_progress(UID)['cycle_id'],seed)
            self.assertEqual(current[2]['receive_point_list'],[7000])
        self.read(THU);self.empty_week()
        self.assertEqual(self.db.get_boss_challenge_progress(UID)['cycle_id'],clock.seed_at(THU))
        self.assertEqual(self.read()[0]['next_refresh_time'],THU+clock.WEEK)

    def test_midweek_old_save_adopts_visible_legacy_seed_without_clearing(self):
        for when in (WED,WED+1):
            with self.subTest(when=when):
                self.db.execute('DELETE FROM server_global_config')
                self.legacy(when);before=self.state()
                # Compute old visible lists with the original generator and no refresh.
                path=ROOT/'archive/boss_cycle_clock_20261104/before/v5_server/weekly_challenge_service.py'
                node=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name=='get_boss_challenge_data')
                ns=dict(vars(weekly));exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
                with patch.object(self.db,'check_and_lazy_refresh_boss_challenge'):
                    old=ns['get_boss_challenge_data'](UID,self.db,strict=True)
                current=self.read();self.assertEqual(self.ids(current),self.ids(old))
                self.assertEqual(self.state()['boss_challenge_advance'],before['boss_challenge_advance'])
                self.assertEqual(current[2]['receive_point_list'],[7000])
                adopted=self.db.get_boss_challenge_progress(UID)['cycle_id']
                self.read(THU-1)
                self.assertEqual(self.db.get_boss_challenge_progress(UID)['cycle_id'],adopted)
                self.assertEqual(self.ids(self.read()),self.ids(current))

    def test_utc8_clock_and_thursday_producer_agree_at_exact_boundaries(self):
        for when,start in ((THU-1,THU-clock.WEEK),(THU,THU),(THU+clock.WEEK-1,THU)):
            self.assertEqual(clock.bounds(when)[:2],(start,start+clock.WEEK))
            self.assertEqual(lazy_timer.get_weekly_thu_5am_ts(when),start)
        self.assertEqual(clock.bounds(at('2026-11-05 04:59:59'))[2],at('2026-11-04 05:00:00'))
        self.assertEqual(clock.bounds(THU)[2],THU)

    def test_every_weekly_write_failure_is_atomic_and_can_retry(self):
        self.seed();before=self.state();execute=self.db.execute;self.now=THU
        for target in ('boss_challenge_normal','boss_challenge_advance','boss_challenge_scores',
                       'boss_challenge_claimed_rewards','boss_challenge_exchange','UPDATE boss_challenge_progress'):
            with self.subTest(target=target):
                def fail(sql,*args,**kwargs):
                    result=execute(sql,*args,**kwargs)
                    if sql.startswith(('DELETE','UPDATE')) and target in sql:raise RuntimeError('isolated after '+target)
                    return result
                with patch.object(self.db,'execute',side_effect=fail):
                    with self.assertRaises(RuntimeError):self.db.check_and_lazy_refresh_boss_challenge(UID,self.now)
                self.assertEqual(self.state(),before)
        self.db.check_and_lazy_refresh_boss_challenge(UID,self.now);self.empty_week()

    def test_adoption_marker_read_or_write_failure_preserves_old_seed_and_claims(self):
        self.legacy(WED+1);before=self.state();query=self.db.query
        def fail_read(sql,*args,**kwargs):
            if 'FROM server_global_config' in sql:raise RuntimeError('isolated adoption read')
            return query(sql,*args,**kwargs)
        for fault in (patch.object(self.db,'query',side_effect=fail_read),
                      patch.object(self.db,'set_system_config',side_effect=RuntimeError('isolated adoption write'))):
            with fault:
                with self.assertRaises(RuntimeError):self.db.check_and_lazy_refresh_boss_challenge(UID,self.now)
            self.assertEqual(self.state(),before)
        self.read();self.assertEqual(self.read()[2]['receive_point_list'],[7000])

    def test_lazy_and_bus_order_never_clear_new_same_week_progress(self):
        for index, first in enumerate(('lazy','bus')):
            with self.subTest(first=first):
                self.now=WED+index*clock.WEEK;self.seed();self.now=THU+index*clock.WEEK
                if first=='lazy':self.read()
                else:self.reset_event()
                self.empty_week()
                bid=self.read()[2]['boss_list'][0]['id']
                self.db.upsert('boss_challenge_advance',UID,dict(boss_id=bid,score=1234),keys=('uid','boss_id'))
                self.db.claim_boss_challenge_reward(UID,2,9000)
                before=self.state()
                self.read();self.reset_event();self.reset_event(Events.DAILY_RESET_5AM)
                self.assertEqual(self.state(),before)

    def test_optional_exchange_table_missing_is_compatible_and_present_rows_reset(self):
        self.seed();self.now=THU
        self.db.execute('DROP TABLE boss_challenge_exchange')  # memory schema fixture only
        self.reset_event();self.empty_week()

    def test_daily_refresh_and_clock_rewind_preserve_weekly_data(self):
        self.seed();before=self.state();self.now=at('2026-11-04 05:00:00')
        self.db.upsert('boss_challenge_progress',UID,dict(last_daily_ts=self.now-86400),keys=('uid',))
        self.reset_event(Events.DAILY_RESET_5AM)
        self.assertEqual(self.db.get_boss_challenge_progress(UID)['use_times'],0)
        self.assertEqual(self.state()['boss_challenge_advance'],before['boss_challenge_advance'])
        self.assertEqual(self.state()['boss_challenge_claimed_rewards'],before['boss_challenge_claimed_rewards'])
        before=self.state();self.now-=86400
        self.reset_event();self.assertEqual(self.state(),before)

    def test_nested_failure_rolls_back_only_domain_savepoint(self):
        self.seed();before=self.state();self.now=THU;execute=self.db.execute
        def fail(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if sql.startswith('DELETE FROM boss_challenge_scores'):raise RuntimeError('isolated nested failure')
            return result
        with self.db.transaction():
            self.db.execute("UPDATE users SET nick='outer kept' WHERE uid=?",(UID,))
            with patch.object(self.db,'execute',side_effect=fail):
                with self.assertRaises(RuntimeError):self.db.check_and_lazy_refresh_boss_challenge(UID,self.now)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.db.get('users',UID)['nick'],'outer kept')

    def pulse(self):
        with patch.object(type(bus),'_handlers',{Events.WEEKLY_RESET_THU_5AM:[timer_listeners.on_boss_challenge_thu_reset]}):
            lazy_timer.LazyTimerBroadcaster().pulse(self.conn.to_handler_ctx(),UID,now_ts=self.now,force=True)

    def prepare_pulse(self):
        self.seed();self.now=THU-1
        self.db.get_timer_watermark(UID)
        self.db.update_timer_watermark(UID,last_weekly_thu_ts=THU-clock.WEEK,last_pulse_ts=self.now)
        self.now=THU

    def test_pulse_failure_retry_then_success_does_not_repeat_domain_reset(self):
        self.prepare_pulse();before=self.state();execute=self.db.execute
        def fail(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if sql.startswith('DELETE FROM boss_challenge_scores'):raise RuntimeError('isolated pulse reset')
            return result
        with patch.object(self.db,'execute',side_effect=fail):self.pulse()
        self.assertEqual(self.state(),before)
        self.assertEqual(self.db.get_timer_watermark(UID)['last_weekly_thu_ts'],THU-clock.WEEK)
        self.now=THU+5;self.pulse();self.empty_week()
        self.assertEqual(self.db.get_timer_watermark(UID)['last_weekly_thu_ts'],THU)
        bid=self.read()[2]['boss_list'][0]['id']
        self.db.upsert('boss_challenge_advance',UID,dict(boss_id=bid,score=300),keys=('uid','boss_id'))
        before=self.state();self.now+=5;self.pulse()
        self.assertEqual(self.state(),before)

    def test_failed_cycle_keeps_four_attempt_budget_and_never_marks_complete(self):
        self.prepare_pulse();before=self.state();execute=self.db.execute;attempts=[]
        def fail(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if sql.startswith('DELETE FROM boss_challenge_scores'):
                attempts.append(self.now);raise RuntimeError('isolated bounded retry')
            return result
        with patch.object(self.db,'execute',side_effect=fail):
            for delta in (0,1,5,6,35,36,155,156,1000):
                self.now=THU+delta;self.pulse()
        self.assertEqual(attempts,[THU,THU+5,THU+35,THU+155])
        self.assertEqual(self.state(),before)
        self.assertEqual(self.db.get_timer_watermark(UID)['last_weekly_thu_ts'],THU-clock.WEEK)


if __name__=='__main__':
    if '--before' in sys.argv:
        base=ROOT/'archive/boss_cycle_clock_20261104/before/v5_server'
        for module,name in ((weekly,'get_boss_challenge_data'),(lazy_timer,'get_weekly_thu_5am_ts'),
                            (timer_listeners,'on_boss_challenge_daily_reset'),(timer_listeners,'on_boss_challenge_thu_reset')):
            path=base/(module.__name__+'.py')
            node=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name==name)
            node.decorator_list=[];ns=dict(vars(module))
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns);setattr(module,name,ns[name])
        path=base/'account_db.py';tree=ast.parse(path.read_text('utf-8-sig'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AccountDB')
        node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='check_and_lazy_refresh_boss_challenge')
        ns=dict(vars(account_db));exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
        account_db.AccountDB.check_and_lazy_refresh_boss_challenge=ns[node.name]
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BossCycleTests)
    if '--before' not in sys.argv:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(BossRewardTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(GrantFailureTests))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
