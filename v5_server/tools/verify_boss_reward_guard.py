"""A79/A80: current-mode qualification, once-only claims and atomic response."""
import ast
from collections import Counter
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
from verify_grant_failure_guard import GrantFailureTests
import codec
import generator
import inventory_service as inventory
import operations
import weekly_challenge_service as weekly

ROOT=Path(__file__).resolve().parents[2]


class BossRewardTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        for iid in (1,2,37,41):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,))
        self.db.check_and_lazy_refresh_boss_challenge(UID,NOW)

    def select(self, mode):
        self.db.set_boss_challenge_mode(UID,mode)
        return weekly.get_boss_challenge_data(UID,self.db,strict=True)

    def score(self, value, table='boss_challenge_advance', bid=None):
        if bid is None:bid=weekly.get_boss_challenge_data(UID,self.db,strict=True)[2]['boss_list'][0]['id']
        self.db.upsert(table,UID,dict(boss_id=bid,score=value),keys=('uid','boss_id'))

    def stars(self, count, duplicate=False):
        _, normal, _=weekly.get_boss_challenge_data(UID,self.db,strict=True)
        remaining=count
        for boss in normal['boss_challenge_list']:
            values=[]
            for sid in weekly.get_boss_catalog()['stage_groups'][str(boss['group_id'])]['stages']:
                n=min(remaining,3);remaining-=n
                if n:values.append(dict(stage_id=sid,star_list=[1]*n+[0]*(3-n)))
            if duplicate and values:values.append(copy.deepcopy(values[0]))
            self.db.upsert('boss_challenge_normal',UID,dict(group_id=boss['group_id'],star_info=json.dumps(values)),keys=('uid','group_id'))
        self.assertEqual(remaining,0)

    def claim(self, kind, value=None):
        cmd,sc,key={1:(45004,45005,'star'),2:(45102,45103,'point'),3:(45112,45113,None)}[kind]
        return self.call(cmd,{} if key is None else {key:value},sc)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('currency','material','boss_challenge_progress','boss_challenge_normal',
                 'boss_challenge_advance','boss_challenge_scores','boss_challenge_claimed_rewards')}

    def reject(self,kind,value=None,code=None):
        before=self.state();packet,response=self.claim(kind,value)
        self.assertNotEqual(packet['result'],0)
        if code is not None:self.assertEqual(packet['result'],code)
        self.assertFalse(packet.get('item_list'))
        self.assertEqual(response.scs,[{1:45005,2:45103,3:45113}[kind]])
        self.assertEqual(self.state(),before)

    def assert_success(self,packet,response,expected):
        self.assertEqual(packet['result'],0)
        self.assertEqual({r['id']:r['num'] for r in packet.get('item_list',[])},dict(expected))
        self.assertNotIn(45001,response.scs);self.assertNotIn(45101,response.scs)
        if expected:
            self.assertEqual(response.scs.count(17023),1)
            delta=next(codec.decode(f['payload'],'sc_17023') for f in response.frames if f['sc']==17023)
            for row in delta['normal_items']:
                self.assertEqual(row['num'],self.balance(row['id']))
            self.assertLess(response.scs.index(17023),len(response.scs)-1)

    def expected(self,kind,tiers,mode=101):
        result=Counter()
        for tier in tiers:
            for row in weekly.get_boss_challenge_reward_items(kind,tier,area_id=mode,mode_id=mode):
                result[row['id']]+=row['num']
        return result

    def test_normal_threshold_unknown_duplicate_and_cold_history(self):
        self.select(4);self.stars(2)
        self.reject(1,3,2005);self.reject(1,2,2);self.reject(1,99999999,2)
        self.stars(3)
        self.assert_success(*self.claim(1,3),self.expected(1,[3],4))
        self.reject(1,3,2013)
        cold=codec.decode(generator.gen_payload(45001,uid=UID,db=self.db),'sc_45001')
        self.assertEqual(cold['receive_star_list'],[3])

    def test_normal_duplicate_stage_records_do_not_add_extra_stars(self):
        self.select(4);self.stars(2,duplicate=True)
        self.reject(1,3,2005)

    def test_canonical_and_legacy_advance_mode_choose_own_tiers(self):
        for mode,first in ((101,7000),(102,10000)):
            with self.subTest(mode=mode):
                self.db.execute('DELETE FROM boss_challenge_claimed_rewards')
                self.select(mode);self.score(first)
                self.assertEqual(self.db.get_boss_challenge_progress(UID)['mode'],2)
                other=10000 if mode==101 else 7000
                self.reject(2,other,2)
                self.assert_success(*self.claim(2,first),self.expected(2,[first],mode))
                self.reject(2,first,2013)
        self.db.execute('DELETE FROM boss_challenge_claimed_rewards')
        self.db.upsert('boss_challenge_progress',UID,dict(mode=101),keys=('uid',))
        self.score(7000)
        self.assert_success(*self.claim(2,7000),self.expected(2,[7000]))

    def test_single_then_all_and_all_then_single_only_grant_each_tier_once(self):
        self.select(101)
        tiers=[r['point'] for r in weekly.get_boss_catalog()['advance_modes']['101']['rewards']][:3]
        self.score(tiers[-1])
        self.assert_success(*self.claim(2,tiers[0]),self.expected(2,tiers[:1]))
        self.assert_success(*self.claim(3),self.expected(2,tiers[1:]))
        for tier in tiers:self.reject(2,tier,2013)
        before=self.state();self.assert_success(*self.claim(3),{})
        self.assertEqual(self.state(),before)
        expected=self.expected(2,tiers)
        for iid,count in expected.items():self.assertEqual(self.balance(iid),count)
        cold=codec.decode(generator.gen_payload(45101,uid=UID,db=self.db),'sc_45101')
        self.assertEqual(set(cold['receive_point_list']),set(tiers))

    def test_only_selected_bosses_and_mixed_old_scores_count_once(self):
        _,_,advance=self.select(101)
        a,b=[boss['id'] for boss in advance['boss_list']][:2]
        self.score(1000,bid=a);self.score(6000,table='boss_challenge_scores',bid=b)
        self.score(9000,table='boss_challenge_scores',bid=a)
        self.score(999999,table='boss_challenge_scores',bid=99999)
        self.reject(2,14000,2005)
        self.assert_success(*self.claim(2,7000),self.expected(2,[7000]))

    def test_no_selected_mode_wrong_kind_and_valid_empty_all(self):
        self.reject(1,3,2);self.reject(2,7000,2);self.reject(3,code=2)
        self.select(4);self.stars(3);self.reject(2,7000,2)
        self.select(101);self.reject(1,3,2)
        before=self.state();self.assert_success(*self.claim(3),{})
        self.assertEqual(self.state(),before)

    def test_query_progress_and_refresh_failures_never_mean_unclaimed(self):
        self.select(101);self.score(7000)
        self.db.claim_boss_challenge_reward(UID,2,7000)
        query=self.db.query
        for table in ('boss_challenge_progress','boss_challenge_advance','boss_challenge_scores','boss_challenge_claimed_rewards'):
            for kind in (2,3):
                with self.subTest(table=table,kind=kind):
                    before=self.state()
                    def fail(sql,*args,**kwargs):
                        if sql.lstrip().upper().startswith('SELECT') and ('FROM '+table+' ') in sql:
                            raise RuntimeError('isolated query '+table)
                        return query(sql,*args,**kwargs)
                    with patch.object(self.db,'query',side_effect=fail):
                        packet,response=self.claim(kind,7000)
                    self.assertEqual(packet['result'],9)
                    self.assertEqual(response.scs,[45103 if kind==2 else 45113])
                    self.assertEqual(self.state(),before)
        before=self.state()
        with patch.object(self.db,'check_and_lazy_refresh_boss_challenge',side_effect=RuntimeError('isolated refresh failure')):
            packet,response=self.claim(3)
        self.assertEqual(packet['result'],9);self.assertEqual(self.state(),before)

    def test_ack_and_atomic_delta_failure_roll_back_all_three_entries(self):
        for kind in (1,2,3):
            for target in ('empty_ack','raised_ack','delta'):
                with self.subTest(kind=kind,target=target):
                    self.select(4 if kind==1 else 101)
                    if kind==1:self.stars(3)
                    else:self.score(7000)
                    before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
                    sc={1:45005,2:45103,3:45113}[kind]
                    def fail(name,*args,**kwargs):
                        if name=='sc_'+str(sc):
                            if target=='raised_ack':raise RuntimeError('isolated ack error')
                            return None
                        return encode(name,*args,**kwargs)
                    if target=='delta':
                        real=generator.gen_payload
                        fault=patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else real(cmd,*a,**kw))
                    else:fault=patch.object(ctx,'codec_encode',side_effect=fail)
                    with fault:packet,response=self.claim(kind,3 if kind==1 else 7000)
                    self.assertNotEqual(packet['result'],0)
                    self.assertEqual(response.scs,[sc]);self.assertEqual(self.state(),before)
        self.assert_success(*self.claim(3),self.expected(2,[7000]))

    def test_marking_and_real_inventory_failure_roll_back_then_retry_all_once(self):
        self.select(101)
        tiers=[r['point'] for r in weekly.get_boss_catalog()['advance_modes']['101']['rewards']][:3]
        self.score(tiers[-1]);before=self.state();mark=self.db.claim_boss_challenge_reward
        def fail_mark(*a,**kw):
            mark(*a,**kw);raise RuntimeError('isolated marked then failed')
        with patch.object(self.db,'claim_boss_challenge_reward',side_effect=fail_mark):
            packet,response=self.claim(3)
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        grant=inventory.grant_item
        def fail_grant(*a,**kw):
            grant(*a,**kw);raise RuntimeError('isolated granted then failed')
        with patch.object(inventory,'grant_item',side_effect=fail_grant):
            packet,response=self.claim(3)
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assert_success(*self.claim(3),self.expected(2,tiers))
        for tier in tiers:self.reject(2,tier,2013)

    def test_expired_progress_is_refreshed_before_qualification(self):
        self.select(101);self.score(7000)
        self.db.claim_boss_challenge_reward(UID,2,7000)
        self.db.upsert('boss_challenge_progress',UID,dict(last_weekly_ts=0),keys=('uid',))
        self.reject(2,7000,2005)
        # A failed claim rolls back its refresh too; next normal data read persists it.
        weekly.get_boss_challenge_data(UID,self.db,strict=True)
        self.assertFalse(self.db.query('SELECT * FROM boss_challenge_claimed_rewards'))
        self.score(7000)
        self.assert_success(*self.claim(2,7000),self.expected(2,[7000]))

    def test_no_cross_mode_or_fixed_reward_fallback(self):
        self.assertEqual(weekly.get_boss_challenge_reward_items(1,2,area_id=4),[])
        self.assertEqual(weekly.get_boss_challenge_reward_items(2,7000,mode_id=102),[])
        self.assertEqual(weekly.get_boss_challenge_reward_items(1,99999999),[])
        self.assertEqual(weekly.get_boss_challenge_reward_items(2,99999999),[])

    def test_all_native_tiers_in_each_supported_mode_grant_configured_amounts(self):
        for mode in (3,4,101,102):
            with self.subTest(mode=mode):
                self.db.execute('DELETE FROM boss_challenge_claimed_rewards')
                self.db.execute('DELETE FROM currency')
                self.select(mode)
                kind=1 if mode<100 else 2
                cfg=weekly.get_boss_catalog()['normal_areas' if kind==1 else 'advance_modes'][str(mode)]
                tiers=[r['star' if kind==1 else 'point'] for r in cfg['rewards']]
                if kind==1:self.stars(max(tiers))
                else:self.score(max(tiers))
                if kind==1:
                    for tier in tiers:self.assert_success(*self.claim(kind,tier),self.expected(kind,[tier],mode))
                else:self.assert_success(*self.claim(3),self.expected(kind,tiers,mode))
                for iid,count in self.expected(kind,tiers,mode).items():self.assertEqual(self.balance(iid),count)
                for tier in tiers:self.reject(kind,tier,2013)


if __name__=='__main__':
    if '--before' in sys.argv:
        base=ROOT/'archive/boss_reward_guard_20261104/before/v5_server'
        for name in ('get_boss_challenge_reward_items','get_boss_challenge_data'):
            path=base/'weekly_challenge_service.py'
            node=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name==name)
            namespace=dict(vars(weekly));exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
            # New fixtures use strict to seed data; old function has no such keyword.
            if name=='get_boss_challenge_data':
                old=namespace[name]
                weekly.get_boss_challenge_data=lambda uid,db,**kw:old(uid,db)
            else:setattr(weekly,name,namespace[name])
        path=base/'operations.py';tree=ast.parse(path.read_text('utf-8-sig'))
        for name in ('BossChallengeClaimStarRewardOp','BossChallengeClaimPointRewardOp','BossChallengeClaimAllPointRewardOp'):
            cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
            for method in ('apply','respond'):
                node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method)
                namespace=dict(vars(operations));exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
                setattr(getattr(operations,name),method,namespace[method])
            getattr(operations,name).prepare_response_in_transaction=False
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BossRewardTests)
    if '--before' not in sys.argv:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(GrantFailureTests))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
