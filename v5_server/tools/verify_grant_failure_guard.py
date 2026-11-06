"""A81: real Core/Inventory/SQL/codec; all databases are memory-only."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
from verify_hero_unlock_delta import UnlockDeltaTests
import codec
import generator
import inventory_service as inventory
import operations
import weekly_challenge_service as weekly

ROOT = Path(__file__).resolve().parents[2]


class GrantFailureTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock = patch('time.time', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        for iid, kind in ((1, 1), (2, 1), (14, 1), (37, 1), (41, 1),
                          (20001, 4), (2310001, 9), (210001, 7)):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)', (iid, kind))
        self.db.check_and_lazy_refresh_boss_challenge(UID, NOW)
        self.db.upsert('boss_challenge_progress', UID, dict(mode=101), keys=('uid',))
        _, _, advance = weekly.get_boss_challenge_data(UID, self.db)
        self.db.upsert('boss_challenge_advance', UID,
                       dict(boss_id=advance['boss_list'][0]['id'], score=7000), keys=('uid','boss_id'))
        self.rewards = weekly.get_boss_challenge_reward_items(2, 7000, mode_id=101)
        self.assertEqual({r['id']: r['num'] for r in self.rewards}, {1:50, 41:7, 37:24})

    def claim(self):
        return self.call(45102, dict(point=7000), 45103)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('currency','material','hero','hero_piece','servant','equip','illustrated',
                 'boss_challenge_claimed_rewards','boss_challenge_progress','achievement')}

    def assert_clean_failure(self, before, packet, response):
        self.assertNotEqual(packet['result'], 0)
        self.assertFalse(packet.get('item_list'))
        self.assertEqual(response.scs, [45103])
        self.assertEqual(self.state(), before)
        ctx = self.conn.to_handler_ctx()
        for field in ('touched_items','touched_heroes','pending_frames','added_equips','added_weapons'):
            self.assertFalse(getattr(ctx, field, None), field)

    def assert_success(self, packet, response, rewards=None):
        rewards = self.rewards if rewards is None else rewards
        expected = {r['id']:r['num'] for r in rewards}
        self.assertEqual(packet['result'], 0)
        self.assertEqual({r['id']:r['num'] for r in packet['item_list']}, expected)
        delta = next(codec.decode(f['payload'], 'sc_17023') for f in response.frames if f['sc']==17023)
        for iid, count in expected.items():
            self.assertEqual(self.balance(iid), count)
            if iid not in (2310001, 210001):
                self.assertIn(dict(id=iid, num=count), delta.get('normal_items', []))
        self.assertEqual(response.scs.count(17023), 1)
        self.assertLess(response.scs.index(17023), response.scs.index(45103))
        # A79 now lets the native success callback maintain the online history.
        self.assertNotIn(45101, response.scs)
        status = weekly.get_boss_challenge_data(UID, self.db, strict=True)[2]
        self.assertIn(7000, status['receive_point_list'])
        self.assertEqual(len(self.db.query('SELECT * FROM boss_challenge_claimed_rewards WHERE uid=?',(UID,))),1)

    def test_native_success_is_one_grant_and_matches_delta_ack_and_history(self):
        with patch.object(inventory, 'grant_item', wraps=inventory.grant_item) as grant:
            packet, response = self.claim()
        self.assertEqual(grant.call_count, 3)
        self.assert_success(packet, response)

    def test_failure_before_write_never_enters_old_branch(self):
        before = self.state()
        with patch.object(inventory,'grant_item',side_effect=RuntimeError('isolated before write')), \
             patch.object(operations,'_item_table',wraps=operations._item_table) as old:
            packet,response=self.claim()
        self.assertEqual(old.call_count,0)
        self.assert_clean_failure(before,packet,response)
        self.assert_success(*self.claim())

    def test_write_then_failure_at_each_reward_rolls_back_and_retry_grants_once(self):
        grant=inventory.grant_item
        for fail_at in range(1,4):
            with self.subTest(fail_at=fail_at):
                before=self.state();calls=[]
                def fail(ctx,uid,iid,num,**kwargs):
                    result=grant(ctx,uid,iid,num,**kwargs);calls.append(iid)
                    if len(calls)==fail_at:raise RuntimeError('isolated after real write')
                    return result
                with patch.object(inventory,'grant_item',side_effect=fail), \
                     patch.object(operations,'_item_table',wraps=operations._item_table) as old:
                    packet,response=self.claim()
                self.assertEqual(len(calls),fail_at)
                self.assertEqual(old.call_count,0)
                self.assert_clean_failure(before,packet,response)
        self.assert_success(*self.claim())

    def test_ledger_failure_after_all_grants_rolls_back(self):
        before=self.state()
        with patch.object(self.db,'claim_boss_challenge_reward',side_effect=RuntimeError('isolated history failure')):
            packet,response=self.claim()
        self.assert_clean_failure(before,packet,response)
        self.assert_success(*self.claim())

    def test_material_and_instance_write_then_failure_drops_pending_deltas(self):
        grant=inventory.grant_item
        for iid in (20001,2310001,210001):
            with self.subTest(iid=iid):
                before=self.state()
                def fail(ctx,uid,item,num,**kwargs):
                    grant(ctx,uid,item,num,**kwargs)
                    raise RuntimeError('isolated after material or instance grant')
                rewards=[dict(id=iid,num=2)]
                with patch.object(weekly,'get_boss_challenge_reward_items',return_value=rewards), \
                     patch.object(inventory,'grant_item',side_effect=fail), \
                     patch.object(operations,'_item_table',wraps=operations._item_table) as old:
                    packet,response=self.claim()
                self.assertEqual(old.call_count,0)
                self.assert_clean_failure(before,packet,response)

    def test_explicit_old_currency_and_material_path_remains_available(self):
        rewards=[dict(id=1,num=12),dict(id=20001,num=7)]
        with patch.object(operations,'USE_NEW_INVENTORY_SERVICE',False), \
             patch.object(weekly,'get_boss_challenge_reward_items',return_value=rewards), \
             patch.object(inventory,'grant_item',side_effect=AssertionError('new path must not run')):
            packet,response=self.claim()
        self.assert_success(packet,response,rewards)

    def test_old_path_write_then_failure_also_rolls_back(self):
        execute=self.db.execute;before=self.state()
        def fail(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if 'INSERT INTO currency' in sql:raise RuntimeError('isolated explicit old write failure')
            return result
        with patch.object(operations,'USE_NEW_INVENTORY_SERVICE',False),patch.object(self.db,'execute',side_effect=fail):
            packet,response=self.claim()
        self.assert_clean_failure(before,packet,response)

    def test_source_return_contract_and_original_exception_are_preserved(self):
        ctx=self.conn.to_handler_ctx()
        with patch.object(inventory,'grant_item',return_value={}) as grant:
            self.assertIs(operations._item_add(ctx,UID,20001,3,source='probe-source'),True)
        grant.assert_called_once_with(ctx,UID,20001,3,source='probe-source')
        for iid in (14,1,20001):
            with self.subTest(iid=iid):
                error=RuntimeError('isolated exact exception')
                with patch.object(inventory,'grant_item',side_effect=error):
                    with self.assertRaises(RuntimeError) as raised:
                        operations._item_add(ctx,UID,iid,1)
                self.assertIs(raised.exception,error)
        with patch.object(operations,'USE_NEW_INVENTORY_SERVICE',False):
            self.assertIsNone(operations._item_add(ctx,UID,20001,0))


if __name__=='__main__':
    if '--before' in sys.argv:
        path=ROOT/'archive/grant_failure_guard_20261104/before/v5_server/operations.py'
        node=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name=='_item_add')
        namespace=dict(vars(operations))
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
        operations._item_add=namespace['_item_add']
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(GrantFailureTests)
    if '--before' not in sys.argv:
        suite.addTests(UnlockDeltaTests(n) for n in UnlockDeltaTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
