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
import draw_models
import generator
import hero_codec
import operations
from inventory_service import InventoryService as Inventory


ROOT=Path(__file__).resolve().parents[2]


class DrawProbabilityTests(unittest.TestCase):
    def test_defaults_match_previous_curve_at_every_pull(self):
        for group, floor, cap, sr in (
            ('hero_precision_90', 80, 90, 0.078),
            ('hero_precision_70', 65, 70, 0.078),
            ('hero_standard_70', 65, 70, 0.078),
            ('weapon_servant_70', 65, 70, 0.1108),
        ):
            rules = draw.DrawProbabilityRules.default_for(group)
            for pull in range(1, cap + 2):
                expected = (1.0 if pull >= cap else
                            0.016 + max(0, pull - floor) * ((1.0 - 0.016) / (cap - floor)))
                self.assertEqual(draw.DrawService.calc_ssr_prob(group, pull, rules=rules), expected)
                self.assertEqual(draw.DrawService.calc_ssr_prob(group, pull), expected)
                self.assertEqual(draw.DrawService.calc_sr_prob(group, pull), 1.0 if pull >= 10 else sr)

    def test_custom_rates_and_pity_limits(self):
        rules = draw.DrawProbabilityRules(0.20, 0.10, 61)
        group = 'hero_precision_70'
        self.assertEqual(draw.DrawService.calc_ssr_prob(group, 60, rules=rules), 0.20)
        self.assertAlmostEqual(draw.DrawService.calc_ssr_prob(group, 61, rules=rules), 0.28)
        self.assertEqual(draw.DrawService.calc_ssr_prob(group, 70, rules=rules), 1.0)
        self.assertEqual(draw.DrawService.calc_sr_prob(group, 9, rules=rules), 0.10)
        self.assertEqual(draw.DrawService.calc_sr_prob(group, 10, rules=rules), 1.0)
        for value in (-0.1, 1.1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                draw.DrawProbabilityRules(value, 0.10, 61)
        with self.assertRaises(ValueError):
            draw.DrawService.calc_ssr_prob(group, 1, rules=draw.DrawProbabilityRules(0.2, 0.1, 81))


class DrawAtomicTests(AcquisitionTests):
    def state(self):
        result=super().state()
        for table in ('draw_group_state','draw_feedback_state','servant','servant_history','illustrated'):
            result[table]=self.db.query('SELECT * FROM '+table+' ORDER BY rowid')
        return result

    def test_anchor_model_switch_preserves_pity_and_feedback(self):
        group = draw_models.ANCHOR_GROUP
        off_up = self.svc.anchor_off_up(HERO)[0]
        for hero in (HERO, off_up):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,3)', (hero + 10000,))
            self.db.upsert('hero', UID, dict(id=hero, unlock=1), keys=('uid', 'id'))
        self.svc.set_pity(UID, group, 88)
        for mode, legacy, rolls, hero, remaining in (
            (1, False, [0.0, 0.99], off_up, 89),
            (2, False, [0.99], HERO, 0),
            (3, True, [0.0], HERO, 0),
        ):
            self.db.set_system_config(draw_models.CONFIG_KEY, json.dumps(dict(mode=mode, anchor_legacy=legacy)))
            details_packet, _ = self.call(16018, dict(id=10001), 16019)
            self.assertEqual(details_packet['result'], 0)
            details = details_packet['pool_details']
            self.assertEqual(details['s_up_probability'], 100 if legacy else 55)
            self.assertEqual(bool(details.get('s_other_item')), not legacy)
            with patch.object(draw.random, 'random', side_effect=rolls), patch.object(draw.random, 'choice', side_effect=lambda seq: seq[0]):
                packet, _ = self.call(16010, dict(pool=10001, type=1), 16011)
            self.assertEqual(packet['result'], 0)
            self.assertEqual(packet['item'][0]['convert_from']['id'], hero)
            self.assertEqual(packet['ssr_draw_times'], remaining)
        self.assertEqual(self.balance(38), 27)
        self.assertEqual(self.db.query('SELECT COUNT(*) AS n FROM draw_record')[0]['n'], 3)
        self.assertGreater(self.db.query('SELECT residual_ema FROM draw_feedback_state')[0]['residual_ema'], 0)

    def test_all_s_ten_uses_normal_receipt_and_rolls_back_feedback(self):
        self.db.set_system_config(draw_models.CONFIG_KEY, json.dumps(dict(mode=3, feedback_bias=100, anchor_legacy=True)))
        self.db.upsert('hero', UID, dict(id=HERO, unlock=1), keys=('uid', 'id'))
        self.db.execute('INSERT INTO draw_feedback_state VALUES (?,?,?)', (UID, draw_models.ANCHOR_GROUP, 1.0))
        ctx = self.conn.to_handler_ctx()
        encode = ctx.codec_encode
        before = self.state()
        with patch.object(draw.random, 'random', return_value=0.999999):
            with patch.object(ctx, 'codec_encode', side_effect=lambda name, data: None if name == 'sc_16011' else encode(name, data)):
                packet, _ = self.call(16010, dict(pool=10001, type=10), 16011)
            self.assertNotEqual(packet['result'], 0)
            self.assertEqual(self.state(), before)
            packet, _ = self.call(16010, dict(pool=10001, type=10), 16011)
        self.assertEqual(packet['result'], 0)
        self.assertEqual([item['convert_from']['id'] for item in packet['item']], [HERO] * 10)
        self.assertEqual(self.balance(38), 20)
        self.assertEqual(self.db.query('SELECT COUNT(*) AS n FROM draw_record')[0]['n'], 10)
        self.assertAlmostEqual(self.db.query('SELECT residual_ema FROM draw_feedback_state')[0]['residual_ema'], 0.95 ** 10)

    def test_one_request_all_s_override_keeps_active_routing_and_expires(self):
        self.db.upsert('hero', UID, dict(id=HERO, unlock=1), keys=('uid', 'id'))
        actual_draw = self.svc.draw
        with patch.object(draw.random, 'random', return_value=0.999999):
            with patch.object(self.svc, 'draw', side_effect=lambda *args: actual_draw(*args, feedback_bias=100)):
                packet, _ = self.call(16010, dict(pool=10001, type=10), 16011)
            self.assertEqual(packet['result'], 0)
            self.assertEqual([item['convert_from']['id'] for item in packet['item']], [HERO] * 10)
            with patch.object(draw.random, 'choice', return_value=1011):
                packet, _ = self.call(16010, dict(pool=10001, type=1), 16011)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(packet['ssr_draw_times'], 1)
        self.assertEqual(self.svc.model_settings().mode, 0)
        self.assertEqual(self.balance(38), 19)

    def reject_draw(self, rank='S'):
        before=self.state()
        packet,response=self.draw_one(rank)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[16011])
        self.assertEqual(self.state(),before)
        ctx=self.conn.to_handler_ctx()
        for field in ('touched_items','touched_heroes','pending_frames','added_equips','added_weapons'):
            self.assertFalse(getattr(ctx,field,None),field)

    def test_server_can_pass_rules_for_one_draw(self):
        self.locked()
        group = self.svc.get_pool_group(10001)
        base = draw.DrawProbabilityRules.default_for(group)
        rules = draw.DrawProbabilityRules(1.0, base.sr_rate_after_ssr_miss, base.soft_pity_start)
        self.svc.set_pity(UID, group, 0, since_sr=0)
        draw_once = self.svc.draw
        with patch.object(self.svc, 'draw', side_effect=lambda *args: draw_once(*args, probability_rules=rules)), \
             patch.object(draw.random, 'random', return_value=0.99):
            packet, _ = self.call(16010, dict(pool=10001, type=1), 16011)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(packet['ssr_draw_times'], 0)
        self.assertEqual(len(packet['item']), 1)
        self.assertEqual(draw.DrawService.calc_ssr_prob(group, 1), draw.BASE_RATE_SSR)

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
