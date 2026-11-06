"""A62: native snapshot schema, per-pool choices and shared pity, memory only."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_draw_atomic_response import DrawAtomicTests
from verify_report_small_fixes import UID
import codec
import draw_service as draw
import operations


ROOT=Path(__file__).resolve().parents[2]
A,B,FIXED=99001,99002,99003
GROUP='hero_precision_90'


class PoolIsolationTests(DrawAtomicTests):
    def setUp(self):
        super().setUp()
        self.db.execute('INSERT OR IGNORE INTO item_catalog(id,type) VALUES(1076,2)')
        self.db.execute('INSERT OR IGNORE INTO item_catalog(id,type) VALUES(11076,3)')
        self.db.execute('INSERT INTO hero_cfg(hero_id,unlock_star) VALUES(1076,300)')
        from profile_rules import skin_portrait_config
        portrait=skin_portrait_config()['1076']['portrait_id']
        self.db.execute('INSERT OR IGNORE INTO item_catalog(id,type) VALUES(?,11)',(portrait,))
        for pid in (A,B,FIXED):
            detail=dict(pool_type=6,pool_selected_type=8 if pid!=FIXED else 0,cost_once=[38,1],cost_ten_times=[38,10])
            self.db.execute('INSERT INTO draw_pool(pool_id,detail_json) VALUES(?,?)',(pid,json.dumps(detail)))
            self.svc._pool_up_cfg[str(pid)]=dict(up_s=1075)

    def select(self, pid, target):
        return self.call(16016,dict(id=pid,up=target),16017)

    def choose_pair(self):
        for pid,target in ((A,1075),(B,1076)):
            self.assertEqual(self.select(pid,target)[0]['result'],0)

    def choices(self):
        return [self.svc.get_pool_user_up(UID,pid) for pid in (A,B)]

    def seed(self, group=GROUP):
        self.svc.save_draw_state(UID,group,dict(since_ssr=40,since_sr=4,total_draws=100,up_id=1076,is_up_guaranteed=1,last_pool_id=B))

    def listing(self, pids=(A,B)):
        with patch.object(self.svc,'get_active_pools',return_value=list(pids)):
            payload=self.svc.build_16015_payload(UID,res_version='321')
        return codec.decode(codec.encode('sc_16015',payload),'sc_16015')['draw_info_list']

    def pull(self, pid):
        with patch.object(self.svc,'calc_ssr_prob',return_value=1),patch.object(draw.random,'random',return_value=0.25):
            return self.call(16010,dict(pool=pid,type=1),16011)

    def test_native_snapshot_updates_without_nonexistent_since_sr_or_choice_copy(self):
        columns={row['name'] for row in self.db.query('PRAGMA table_info(draw_state)')}
        self.assertNotIn('since_sr',columns)
        self.choose_pair();self.seed()
        state=self.svc.get_draw_state(UID,GROUP);state['since_ssr']=41;state['total_draws']=101
        self.svc.save_draw_state(UID,GROUP,state,A)
        row=self.db.query('SELECT * FROM draw_state WHERE uid=? AND pool_id=?',(UID,A))[0]
        self.assertEqual((row['since_ssr'],row['total_draws'],row['up_id']),(41,101,1075))
        self.assertEqual(self.choices(),[1075,1076])
        self.assertEqual(self.svc.get_draw_state(UID,GROUP)['since_sr'],4)

    def test_legacy_extra_column_does_not_restore_cross_pool_overwrite(self):
        self.db.execute('ALTER TABLE draw_state ADD COLUMN since_sr INTEGER DEFAULT 0')
        self.choose_pair();self.seed()
        state=self.svc.get_draw_state(UID,GROUP)
        for pid in (A,B,A):
            state['since_ssr']+=1;state['total_draws']+=1
            self.svc.save_draw_state(UID,GROUP,state,pid)
            self.assertEqual(self.choices(),[1075,1076])

    def test_alternating_actual_draws_listing_and_details_use_each_choice(self):
        self.choose_pair()
        for pid,hid in ((A,1075),(B,1076),(A,1075),(B,1076)):
            with self.subTest(pid=pid,hid=hid):
                listed={r['id']:r['up'] for r in self.listing()}
                self.assertEqual(listed,{A:1075,B:1076})
                details,_=self.call(16018,dict(id=pid),16019)
                expected=draw.DRAW_ITEM_STD_S[hid]
                self.assertEqual(details['pool_details']['s_up_item'],[expected])
                packet,_=self.pull(pid)
                item=packet['item'][0]
                self.assertEqual(item.get('convert_from',{}).get('id',item['id']),hid)
                self.assertEqual(self.choices(),[1075,1076])

    def test_shared_counters_guarantee_and_gm_pity_preserve_choices(self):
        self.choose_pair();self.seed()
        self.seed('hero_standard_70')
        other=self.svc.get_draw_state(UID,'hero_standard_70')
        for pid in (A,B):
            with patch.object(self.svc,'calc_ssr_prob',return_value=0),patch.object(self.svc,'calc_sr_prob',return_value=0):
                with patch.object(draw.random,'random',side_effect=[0.99,0.99,0]),patch.object(draw.random,'choice',return_value=1084):
                    packet,_=self.call(16010,dict(pool=pid,type=1),16011)
            self.assertEqual(packet['result'],0)
        state=self.svc.get_draw_state(UID,GROUP)
        self.assertEqual((state['since_ssr'],state['since_sr'],state['total_draws'],state['is_up_guaranteed']),(42,6,102,1))
        self.assertEqual(self.svc.get_draw_state(UID,'hero_standard_70'),other)
        self.svc.set_pity(UID,GROUP,12,is_up_guaranteed=0,since_sr=3)
        self.assertEqual(self.choices(),[1075,1076])
        self.assertEqual(self.svc.get_draw_state(UID,GROUP)['since_sr'],3)

    def test_servant_pools_use_current_pool_target_not_last_group_choice(self):
        for pid in (A,B):
            self.db.execute('UPDATE draw_pool SET detail_json=? WHERE pool_id=?',(json.dumps(dict(pool_type=2,pool_selected_type=9,cost_once=[19,1])),pid))
            self.svc._pool_up_cfg[str(pid)]={}
        for iid in (2510000,2520000):self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,9)',(iid,))
        self.db.set_currency(UID,19,10)
        self.assertEqual(self.select(A,1)[0]['result'],0)
        self.assertEqual(self.select(B,2)[0]['result'],0)
        self.assertEqual(self.choices(),[2510000,2520000])
        for pid,target in ((A,2510000),(B,2520000)):
            listed={r['id']:r['up'] for r in self.listing()}
            self.assertEqual(listed[pid],target)
            details,_=self.call(16018,dict(id=pid),16019)
            self.assertEqual(details['pool_details']['s_up_item'],[draw.DRAW_ITEM_SERVANT_5[target]])
            packet,_=self.pull(pid)
            self.assertEqual(packet['item'][0]['id'],target)
        self.assertEqual(self.choices(),[2510000,2520000])
        self.assertEqual(self.balance(19),8)
        rows=self.db.query('SELECT id,prefab_id,stage FROM servant WHERE uid=?',(UID,))
        self.assertEqual({r['prefab_id'] for r in rows},{2510000,2520000})
        self.assertEqual(len({r['id'] for r in rows}),2)
        self.assertTrue(all(r['stage']==1 for r in rows))

    def test_fixed_up_ignores_shared_or_stale_pool_choice(self):
        self.choose_pair()
        self.db.upsert('draw_state',UID,dict(pool_id=FIXED,up_id=1076),keys=('uid','pool_id'))
        listed={r['id']:r['up'] for r in self.listing((A,B,FIXED))}
        self.assertEqual(listed[FIXED],0)
        details,_=self.call(16018,dict(id=FIXED),16019)
        self.assertEqual(details['pool_details']['s_up_item'],[draw.DRAW_ITEM_STD_S[1075]])
        packet,_=self.pull(FIXED)
        self.assertEqual(packet['item'][0]['id'],1075)
        self.assertEqual(self.choices(),[1075,1076])

    def test_progress_save_does_not_invent_or_restore_cleared_selection(self):
        self.seed()
        state=self.svc.get_draw_state(UID,GROUP)
        self.svc.save_draw_state(UID,GROUP,state,A)
        row=self.db.query('SELECT * FROM draw_state WHERE uid=? AND pool_id=?',(UID,A))[0]
        self.assertEqual((row['up_id'],row['since_ssr']),(0,40))
        self.choose_pair()
        self.assertEqual(self.select(A,0)[0]['result'],0)
        state=self.svc.get_draw_state(UID,GROUP)
        self.svc.save_draw_state(UID,GROUP,state,A)
        self.assertEqual(self.choices(),[0,1076])

    def test_selection_write_and_ack_failures_are_atomic(self):
        self.choose_pair();self.seed()
        execute=self.db.execute
        for needle in ('INSERT INTO draw_state','INSERT INTO draw_group_state'):
            before=self.state()
            def broken(sql,args=()):
                result=execute(sql,args)
                if needle in sql:raise RuntimeError('isolated selector write-after failure')
                return result
            with patch.object(self.db,'execute',side_effect=broken):packet,response=self.select(A,1076)
            self.assertNotEqual(packet['result'],0)
            self.assertEqual(response.scs,[16017])
            self.assertEqual(self.state(),before)
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        before=self.state()
        with patch.object(ctx,'codec_encode',side_effect=lambda name,data:None if name=='sc_16017' else encode(name,data)):
            packet,response=self.select(A,1076)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(response.scs,[16017])

    def test_direct_progress_save_rolls_back_both_rows_inside_outer_transaction(self):
        self.choose_pair();self.seed()
        before=self.state();state=self.svc.get_draw_state(UID,GROUP)
        state['since_ssr']=48;state['total_draws']=108
        execute=self.db.execute
        def broken(sql,args=()):
            result=execute(sql,args)
            if 'INSERT INTO draw_state' in sql:raise RuntimeError('isolated compatibility snapshot write failure')
            return result
        with self.db.transaction():
            self.db.set_currency(UID,2,77)
            with patch.object(self.db,'execute',side_effect=broken),self.assertRaises(RuntimeError):
                self.svc.save_draw_state(UID,GROUP,state,A)
            self.assertEqual(self.balance(2),77)
            for table in ('draw_state','draw_group_state'):
                self.assertEqual(self.db.query('SELECT * FROM '+table+' ORDER BY rowid'),before[table])
        self.assertEqual(self.choices(),[1075,1076])


if __name__=='__main__':
    before='--before' in sys.argv
    if before:
        path=ROOT/'archive/draw_pool_isolation_20261103/before/v5_server/draw_service.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='DrawService')
        for name in ('save_draw_state','set_pool_up','draw'):
            method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
            namespace=dict(vars(draw));exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
            setattr(draw.DrawService,name,namespace[name])
        path=ROOT/'archive/draw_pool_isolation_20261103/before/v5_server/operations.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='DrawSetPollUpOp')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='respond')
        namespace=dict(vars(operations));exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
        operations.DrawSetPollUpOp.respond=namespace['respond']
        operations.DrawSetPollUpOp.prepare_response_in_transaction=False
    names=[n for n in PoolIsolationTests.__dict__ if n.startswith('test_')] if before else unittest.defaultTestLoader.getTestCaseNames(PoolIsolationTests)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(PoolIsolationTests(n) for n in names))
    raise SystemExit(not result.wasSuccessful())
