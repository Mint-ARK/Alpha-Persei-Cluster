"""A57/A58: real acquisition/draw operations and codec, memory databases only."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from verify_item_use_atomic_random import UseTests, entry
from verify_report_small_fixes import UID
import codec
import draw_service as draw
import inventory_service as inv
from inventory_service import InventoryService as Inventory
from event_bus import bus, Events
from hero_codec import initial_hero_row


ROOT = Path(__file__).resolve().parents[2]
HERO = 1075


class AcquisitionTests(UseTests):
    def setUp(self):
        super().setUp()
        for hid, star in ((1075,300),(1011,200),(1084,100)):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,2)', (hid,))
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,3)', (hid+10000,))
            self.db.execute('INSERT OR REPLACE INTO hero_cfg(hero_id,unlock_star,name) VALUES(?,?,?)', (hid,star,'probe'))
            from profile_rules import skin_portrait_config
            portrait = skin_portrait_config()[str(hid)]['portrait_id']
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,11)', (portrait,))
        self.svc = draw.DrawService.__new__(draw.DrawService)
        self.svc.db = self.db
        self.svc._pool_up_cfg = {'10001':dict(up_s=HERO,up_a=[1011])}
        self.svc._init_db_schema()
        owner = patch.object(draw.DrawService, '_instance', self.svc)
        owner.start(); self.addCleanup(owner.stop)
        self.db.execute('INSERT INTO draw_pool(pool_id,detail_json) VALUES(10001,?)',
                        (json.dumps(dict(pool_type=6,cost_once=[38,1],cost_ten_times=[38,10])),))
        self.db.set_currency(UID,38,30)

    def state(self):
        result = super().state()
        for table in ('hero','backhome_hero','draw_state','draw_record'):
            result[table] = self.db.query('SELECT * FROM '+table+' ORDER BY rowid')
        return result

    @contextmanager
    def bundle(self, rewards):
        cfg = copy.deepcopy(inv._get_item_usable_cfg())
        cfg['30009'].update(sub_type=501,param=rewards)
        with patch.object(inv,'_ITEM_USABLE_CFG_CACHE',cfg):
            yield

    def locked(self, hid=HERO):
        self.db.upsert('hero',UID,dict(id=hid,unlock=0,level=37,star=300,exp=77,
                       is_favorite=1,trust_level=3,trust_exp=200,weapon_exp=123,
                       skill_list=initial_hero_row(hid,300)['skill_list']),keys=('uid','id'))
        return self.db.get('hero',UID,'AND id=?',(hid,))

    def unlock_events(self, emitted):
        return [call for call in emitted.call_args_list if call.args and call.args[0]==Events.HERO_UNLOCK]

    def assert_hero_delta(self, response, hid=HERO):
        packets = [codec.decode(f['payload'],'sc_14007')['hero'] for f in response.frames if f['sc']==14007]
        matching = [p for p in packets if p['hero_base_info']['id']==hid]
        self.assertTrue(matching)
        self.assertTrue(all(p['unlock']==1 for p in matching))
        self.assertNotIn(14009,response.scs)

    def test_missing_row_gets_complete_initial_hero_and_portrait(self):
        with self.bundle([[HERO,1]]), patch.object(bus,'emit',wraps=bus.emit) as emitted:
            packet,response = self.use([entry(30009)])
        self.assertEqual(packet['result'],0)
        hero = self.db.get('hero',UID,'AND id=?',(HERO,))
        self.assertEqual((hero['unlock'],hero['level'],hero['star']),(1,1,300))
        self.assertTrue(json.loads(hero['skill_list']))
        self.assertEqual(self.balance(11075),0)
        self.assertEqual(len(self.unlock_events(emitted)),1)
        portrait = self.unlock_events(emitted)[0].kwargs['portrait_id']
        self.assertTrue(self.db.query("SELECT 1 FROM player_card WHERE uid=? AND kind='portrait' AND item_id=? AND obtained=1",(UID,portrait)))
        self.assert_hero_delta(response)

    def test_locked_row_is_acquired_without_resetting_progression(self):
        before = self.locked()
        with self.bundle([[HERO,1]]), patch.object(bus,'emit',wraps=bus.emit) as emitted:
            packet,response = self.use([entry(30009)])
        self.assertEqual(packet['result'],0)
        hero = self.db.get('hero',UID,'AND id=?',(HERO,))
        self.assertEqual(hero['unlock'],1)
        for key in ('level','star','exp','skill_list','weapon_exp','is_favorite','trust_level','trust_exp'):
            self.assertEqual(hero[key],before[key],key)
        self.assertEqual(self.balance(11075),0)
        self.assertEqual(len(self.unlock_events(emitted)),1)
        self.assert_hero_delta(response)

    def test_empty_locked_placeholder_receives_missing_defaults(self):
        self.db.upsert('hero',UID,dict(id=HERO,unlock=0,level=0,star=0,using_skin=0,battle_using_skin=0,skill_list='[]'),keys=('uid','id'))
        with self.bundle([[HERO,1]]):
            packet,response=self.use([entry(30009)])
        self.assertEqual(packet['result'],0)
        hero=self.db.get('hero',UID,'AND id=?',(HERO,))
        self.assertEqual((hero['unlock'],hero['level'],hero['star'],hero['using_skin'],hero['battle_using_skin']),(1,1,300,HERO,HERO))
        self.assertTrue(json.loads(hero['skill_list']))
        self.assert_hero_delta(response)

    def test_mixed_first_and_duplicate_rewards_match_assets(self):
        for owned in (False,True):
            for count in (1,2):
                with self.subTest(owned=owned,count=count):
                    self.db.execute('DELETE FROM hero WHERE uid=? AND id=?',(UID,HERO))
                    if owned:self.db.upsert('hero',UID,initial_hero_row(HERO,300),keys=('uid','id'))
                    self.db.execute('INSERT OR REPLACE INTO hero_piece(uid,hero_id,num) VALUES(?,?,7)',(UID,HERO))
                    self.db.execute('UPDATE material SET num=3 WHERE uid=? AND id=30009',(UID,))
                    with self.bundle([[HERO,count],[11075,5]]), patch.object(bus,'emit',wraps=bus.emit) as emitted:
                        packet,response=self.use([entry(30009)])
                    self.assertEqual(packet['result'],0)
                    pieces=5+30*(count if owned else count-1)
                    expected={11075:pieces}
                    if not owned:expected[HERO]=1
                    self.assertEqual({r['id']:r['num'] for r in packet['drop_list']},expected)
                    self.assertEqual(self.balance(11075),7+pieces)
                    self.assertEqual(len(self.unlock_events(emitted)),0 if owned else 1)
                    self.assert_hero_delta(response)

    def test_same_hero_in_separate_reward_lines_is_counted_per_copy(self):
        with self.bundle([[HERO,1],[HERO,2]]):packet,_=self.use([entry(30009)])
        self.assertEqual({r['id']:r['num'] for r in packet['drop_list']},{HERO:1,11075:60})
        self.assertEqual(self.balance(11075),60)

    def test_acquisition_ack_failure_rolls_back_then_retry_unlocks_once(self):
        self.locked()
        before=self.state()
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with self.bundle([[HERO,2]]),patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_17013' else encode(n,d)):
            self.reject([entry(30009)])
        self.assertEqual(self.state(),before)
        with self.bundle([[HERO,2]]):packet,_=self.use([entry(30009)])
        self.assertEqual({r['id']:r['num'] for r in packet['drop_list']},{HERO:1,11075:30})

    def draw_one(self, rank='S', count=1):
        ssr,sr={'S':(1,0),'A':(0,1),'B':(0,0)}[rank]
        rolls=None if rank!='B' else [0.5,0.5,0.0]
        chosen=1011 if rank=='A' else 1084
        with patch.object(self.svc,'calc_ssr_prob',return_value=ssr),patch.object(self.svc,'calc_sr_prob',return_value=sr):
            with patch.object(draw.random,'random',side_effect=rolls,return_value=0.5),patch.object(draw.random,'choice',return_value=chosen):
                return self.call(16010,dict(pool=10001,type=count),16011)

    def test_draw_s_a_b_locked_rows_are_new_acquisitions(self):
        for rank,hid in (('S',HERO),('A',1011),('B',1084)):
            with self.subTest(rank=rank):
                before=self.locked(hid)
                with patch.object(bus,'emit',wraps=bus.emit) as emitted:
                    packet,response=self.draw_one(rank)
                self.assertEqual(packet['result'],0)
                self.assertEqual(packet['item'][0]['id'],hid)
                hero=self.db.get('hero',UID,'AND id=?',(hid,))
                self.assertEqual((hero['unlock'],hero['level'],hero['exp']),(1,37,77))
                self.assertEqual(self.balance(hid+10000),0)
                self.assertEqual(len(self.unlock_events(emitted)),1)
                self.assert_hero_delta(response,hid)
                with patch.object(bus,'emit',wraps=bus.emit) as emitted:
                    packet,response=self.draw_one(rank)
                expected={'S':30,'A':18,'B':10}[rank]
                self.assertEqual(packet['result'],0)
                self.assertEqual((packet['item'][0]['id'],packet['item'][0]['num']),(hid+10000,expected))
                self.assertEqual(self.balance(hid+10000),expected)
                self.assertEqual(len(self.unlock_events(emitted)),0)

    def test_draw_grant_failure_rolls_back_locked_hero_and_ticket(self):
        self.locked()
        before=self.state()
        grant=Inventory.grant_item
        def broken(*args,**kwargs):
            grant(*args,**kwargs)
            raise RuntimeError('isolated failure after hero acquisition')
        with patch.object(Inventory,'grant_item',side_effect=broken):
            packet,_=self.draw_one()
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(self.state(),before)
        packet,_=self.draw_one()
        self.assertEqual(packet['result'],0)
        self.assertEqual(packet['item'][0]['id'],HERO)
        self.assertEqual(self.balance(38),29)

    def test_ten_draws_unlock_once_then_convert_and_keep_existing_pieces(self):
        self.locked()
        self.db.execute('INSERT INTO hero_piece(uid,hero_id,num) VALUES(?,?,7)',(UID,HERO))
        with patch.object(bus,'emit',wraps=bus.emit) as emitted:
            packet,response=self.draw_one(count=10)
        self.assertEqual(packet['result'],0)
        self.assertEqual([row['id'] for row in packet['item']],[HERO]+[11075]*9)
        self.assertEqual(self.balance(11075),277)
        self.assertEqual(self.balance(38),20)
        self.assertEqual(len(self.unlock_events(emitted)),1)
        self.assert_hero_delta(response)


if __name__=='__main__':
    before='--before' in sys.argv
    if before:
        path=ROOT/'archive/hero_acquisition_20261103/before/v5_server/inventory_service.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='InventoryService')
        for name in ('_grant_single_hero','_use_items'):
            method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
            method.decorator_list=[];namespace=dict(vars(inv))
            exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
            setattr(Inventory,name,classmethod(namespace[name]))
        path=ROOT/'archive/hero_acquisition_20261103/before/v5_server/draw_service.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='DrawService')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='draw')
        namespace=dict(vars(draw));exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
        draw.DrawService.draw=namespace['draw']
    names=[name for name in AcquisitionTests.__dict__ if name.startswith('test_')] if before else unittest.defaultTestLoader.getTestCaseNames(AcquisitionTests)
    suite=unittest.TestSuite(AcquisitionTests(name) for name in names)
    if not before:
        from verify_item_choice_count import ChoiceTests
        from verify_item_use_plan_guard import PlanTests
        suite.addTests(ChoiceTests(name) for name in ChoiceTests.__dict__ if name.startswith('test_'))
        suite.addTests(PlanTests(name) for name in PlanTests.__dict__ if name.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
