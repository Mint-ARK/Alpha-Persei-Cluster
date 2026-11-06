"""A61: atomic draw persistence and required response preparation, memory only."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_hero_acquisition import AcquisitionTests, HERO
from verify_report_small_fixes import UID
import draw_service as draw
import generator
import hero_codec
import operations
from inventory_service import InventoryService as Inventory


ROOT=Path(__file__).resolve().parents[2]


class DrawAtomicTests(AcquisitionTests):
    def state(self):
        result=super().state()
        for table in ('draw_group_state','servant','servant_history','illustrated'):
            result[table]=self.db.query('SELECT * FROM '+table+' ORDER BY rowid')
        return result

    def reject_draw(self, rank='S'):
        before=self.state()
        packet,response=self.draw_one(rank)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[16011])
        self.assertEqual(self.state(),before)
        ctx=self.conn.to_handler_ctx()
        for field in ('touched_items','touched_heroes','pending_frames','added_equips','added_weapons'):
            self.assertFalse(getattr(ctx,field,None),field)

    def test_empty_or_raising_ack_rolls_back_then_retry_is_one_draw(self):
        self.locked()
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for raising in (False,True):
            def broken(name,data):
                if name=='sc_16011':
                    if raising:raise RuntimeError('isolated draw ack failure')
                    return None
                return encode(name,data)
            with self.subTest(raising=raising),patch.object(ctx,'codec_encode',side_effect=broken):
                self.reject_draw()
        packet,response=self.draw_one()
        self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(38),29)
        self.assertEqual(len(self.db.query('SELECT * FROM draw_record WHERE uid=?',(UID,))),1)
        state=self.svc.get_draw_state(UID,'hero_precision_90')
        self.assertEqual(state['total_draws'],1)

    def test_required_asset_delta_failure_rolls_back(self):
        gen=generator.gen_payload
        for empty in (None,b''):
            with self.subTest(empty=empty),patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:empty if cmd==17023 else gen(cmd,**kw)):
                self.reject_draw()

    def test_hero_delta_exception_does_not_commit_acquisition(self):
        with patch.object(hero_codec,'build_hero_14007_frame',side_effect=RuntimeError('isolated hero frame failure')):
            self.reject_draw()

    def test_authoritative_group_progress_write_failure_rolls_back(self):
        execute=self.db.execute
        def broken(sql,args=()):
            if 'INSERT INTO draw_group_state ' in sql:raise RuntimeError('isolated group progress failure')
            return execute(sql,args)
        with patch.object(self.db,'execute',side_effect=broken):
            self.reject_draw()

    def test_history_insert_and_retention_failures_roll_back(self):
        execute=self.db.execute
        for needle in ('INSERT INTO draw_record','DELETE FROM draw_record'):
            def broken(sql,args=()):
                if needle in sql:raise RuntimeError('isolated history save failure')
                return execute(sql,args)
            with self.subTest(needle=needle),patch.object(self.db,'execute',side_effect=broken):
                self.reject_draw()

    def test_inventory_first_or_partial_write_failure_rolls_back(self):
        grant=Inventory.grant_items
        for partial in (False,True):
            def broken(ctx,uid,items,**kwargs):
                if partial:grant(ctx,uid,items,**kwargs)
                raise RuntimeError('isolated draw inventory failure')
            with self.subTest(partial=partial),patch.object(Inventory,'grant_items',side_effect=broken):
                self.reject_draw()

    def servant_draw(self):
        with patch.object(self.svc,'calc_ssr_prob',return_value=1),patch.object(draw.random,'random',return_value=0.5),patch.object(draw.random,'choice',return_value=2510000):
            return self.call(16010,dict(pool=10002,type=1),16011)

    def test_servant_delta_failure_rolls_back_instances_and_ticket(self):
        self.db.execute('INSERT INTO item_catalog(id,type) VALUES(2510000,9)')
        self.db.execute('INSERT INTO draw_pool(pool_id,detail_json) VALUES(10002,?)',(json.dumps(dict(cost_once=[19,1])),))
        self.db.set_currency(UID,19,5)
        gen=generator.gen_payload
        before=self.state()
        for empty in (None,b''):
            with self.subTest(empty=empty),patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:empty if cmd==17023 else gen(cmd,**kw)):
                packet,response=self.servant_draw()
            self.assertNotEqual(packet['result'],0)
            self.assertEqual(response.scs,[16011])
            self.assertEqual(self.state(),before)
            self.assertFalse(getattr(self.conn.to_handler_ctx(),'added_weapons',None))
        packet,response=self.servant_draw()
        self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(19),4)
        self.assertEqual(len(self.db.query('SELECT * FROM servant WHERE uid=?',(UID,))),1)
        self.assertNotIn(46011,response.scs)
        self.assertIn(17023,response.scs)

    def test_original_70_and_90_pity_boundaries_remain(self):
        for limit,cost_id,pool_type,group in ((70,18,0,'hero_standard_70'),(90,38,6,'hero_precision_90')):
            with self.subTest(limit=limit):
                self.db.set_currency(UID,cost_id,5)
                self.db.execute('UPDATE draw_pool SET detail_json=? WHERE pool_id=10001',(json.dumps(dict(pool_type=pool_type,cost_once=[cost_id,1])),))
                self.svc.save_draw_state(UID,group,dict(since_ssr=limit-1,since_sr=0,total_draws=limit-1,up_id=HERO,is_up_guaranteed=0),10001)
                with patch.object(draw.random,'random',return_value=0.999):
                    packet,_=self.call(16010,dict(pool=10001,type=1),16011)
                self.assertEqual(packet['result'],0)
                state=self.svc.get_draw_state(UID,group)
                self.assertEqual((state['since_ssr'],state['total_draws']),(0,limit))
                self.assertEqual(self.balance(cost_id),4)


if __name__=='__main__':
    before='--before' in sys.argv
    if before:
        path=ROOT/'archive/draw_atomic_response_20261103/before/v5_server/draw_service.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='DrawService')
        for name in ('draw','save_draw_state'):
            method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
            namespace=dict(vars(draw));exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
            setattr(draw.DrawService,name,namespace[name])
        path=ROOT/'archive/draw_atomic_response_20261103/before/v5_server/operations.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='DrawOp')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='respond')
        namespace=dict(vars(operations));exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
        operations.DrawOp.respond=namespace['respond']
        operations.DrawOp.prepare_response_in_transaction=False
    names=[name for name in DrawAtomicTests.__dict__ if name.startswith('test_')] if before else unittest.defaultTestLoader.getTestCaseNames(DrawAtomicTests)
    suite=unittest.TestSuite(DrawAtomicTests(name) for name in names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
