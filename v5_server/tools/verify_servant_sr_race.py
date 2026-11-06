"""Four-star servants follow native optional_lists_a; real Core, memory only."""
import ast
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

from verify_draw_atomic_response import DrawAtomicTests
from verify_report_small_fixes import UID
from generate_archive_config import _top_level_blocks, _number_list
import codec
import draw_service as draw
import generator


ROOT=Path(__file__).resolve().parents[2]


class ServantRaceTests(DrawAtomicTests):
    def setUp(self):
        super().setUp()
        for iid in draw.SERVANT_4_LIST+draw.SERVANT_3_LIST:
            self.db.execute('INSERT OR IGNORE INTO item_catalog(id,type) VALUES(?,9)',(iid,))
        self.db.set_currency(UID,19,50)

    def servant_pool(self,pid=10002):
        detail=dict(pool_type=2,pool_selected_type=9,cost_once=[19,1],cost_ten_times=[19,10])
        self.db.execute('INSERT OR IGNORE INTO draw_pool(pool_id,detail_json) VALUES(?,?)',(pid,json.dumps(detail)))
        self.svc._pool_up_cfg[str(pid)]={}

    def select_child(self,race,pid=10002):
        self.servant_pool(pid)
        packet,_=self.call(16016,dict(id=pid,up=race),16017)
        self.assertEqual(packet['result'],0)

    def sr_draw(self,pid=10002,count=1,choice=max,sr=1):
        with patch.object(self.svc,'calc_ssr_prob',return_value=0),patch.object(self.svc,'calc_sr_prob',return_value=sr):
            with patch.object(draw.random,'random',return_value=0.5),patch.object(draw.random,'choice',side_effect=choice):
                return self.call(16010,dict(pool=pid,type=count),16011)

    def test_candidate_mapping_matches_both_native_pool_configurations(self):
        text=(ROOT/'decompiled_v2/x64/game/config/drawpoolcfg.lua').read_text('utf-8')
        seen=set()
        for pid,block in _top_level_blocks(text):
            if pid not in (10002,4020201):continue
            seen.add(pid)
            choices=_number_list(block,'optional_lists')
            body=re.search(r'(?ms)^\t\toptional_lists_a = \{(.*?)^\t\t\}',block)[1]
            groups=re.findall(r'(?ms)^\t\t\t\{(.*?)^\t\t\t\}',body)
            self.assertEqual(len(choices),len(groups))
            for target,group in zip(choices,groups):
                expected=tuple(int(n) for n in re.findall(r'(?m)^\s*(\d+),?\s*$',group))
                self.assertEqual(self.svc.get_servant_sr_candidates(target),expected)
        self.assertEqual(seen,{10002,4020201})
        self.assertEqual({sid for pair in draw.SERVANT_4_BY_CHILD.values() for sid in pair},set(draw.SERVANT_4_LIST))

    def test_all_six_races_produce_only_their_pair_and_complete_instances(self):
        for race,child in ((1,2510000),(2,2520000),(3,2530000),(4,2540000),(5,2550000),(9,2590000)):
            self.select_child(race)
            expected=draw.SERVANT_4_BY_CHILD[child]
            for index in (0,-1):
                with self.subTest(race=race,index=index):
                    before={r['id'] for r in self.db.query('SELECT id FROM servant WHERE uid=?',(UID,))}
                    packet,response=self.sr_draw(choice=lambda pool:pool[index])
                    self.assertEqual(packet['result'],0)
                    self.assertEqual(packet['item'][0]['id'],expected[index])
                    rows=[r for r in self.db.query('SELECT id,prefab_id,stage FROM servant WHERE uid=?',(UID,)) if r['id'] not in before]
                    self.assertEqual(len(rows),1)
                    row=rows[0]
                    self.assertEqual((row['prefab_id'],row['stage']),(expected[index],1))
                    delta=next(codec.decode(f['payload'],'sc_17023') for f in response.frames if f['sc']==17023)
                    self.assertTrue(any(r['uid']==row['id'] and r['item']['id']==expected[index] for r in delta['weapon_list']))
                    self.assertNotIn(46011,response.scs)

    def test_details_show_native_draw_item_ids_for_the_selected_pair(self):
        for race,child in ((1,2510000),(2,2520000),(3,2530000),(4,2540000),(5,2550000),(9,2590000)):
            with self.subTest(race=race):
                self.select_child(race)
                packet,_=self.call(16018,dict(id=10002),16019)
                self.assertEqual(packet['result'],0)
                details=packet['pool_details']
                self.assertEqual(details['a_other_item'],[draw.DRAW_ITEM_SERVANT_4[sid] for sid in draw.SERVANT_4_BY_CHILD[child]])
                self.assertFalse(details.get('a_up_item'))
                self.assertEqual(details['s_up_item'],[draw.DRAW_ITEM_SERVANT_5[child]])

    def test_alternating_pools_use_their_own_four_star_race(self):
        self.select_child(1,10002);self.select_child(2,99004)
        for pid,expected in ((10002,2410002),(99004,2420002),(10002,2410002)):
            with self.subTest(pid=pid):
                packet,_=self.sr_draw(pid)
                self.assertEqual(packet['item'][0]['id'],expected)
        self.assertEqual(self.svc.get_pool_user_up(UID,10002),2510000)
        self.assertEqual(self.svc.get_pool_user_up(UID,99004),2520000)

    def test_ten_draws_keep_instance_counts_cost_and_rebate(self):
        self.select_child(1)
        packet,response=self.sr_draw(count=10)
        self.assertEqual(packet['result'],0)
        self.assertEqual([r['id'] for r in packet['item']],[2410002]*10)
        self.assertEqual((self.balance(19),self.balance(36)),(40,200))
        rows=self.db.query('SELECT id,prefab_id,stage FROM servant WHERE uid=?',(UID,))
        self.assertEqual(len({r['id'] for r in rows}),10)
        self.assertTrue(all(r['prefab_id']==2410002 and r['stage']==1 for r in rows))
        delta=next(codec.decode(f['payload'],'sc_17023') for f in response.frames if f['sc']==17023)
        self.assertEqual(len(delta['weapon_list']),10)

    def test_sr_refresh_failure_rolls_back_instances_cost_pity_and_history(self):
        self.select_child(1)
        before=self.state();gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else gen(cmd,**kw)):
            packet,response=self.sr_draw()
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[16011])
        self.assertEqual(self.state(),before)
        packet,_=self.sr_draw()
        self.assertEqual(packet['item'][0]['id'],2410002)
        self.assertEqual(self.balance(19),49)

    def test_unset_target_preserves_unselected_candidate_fallback(self):
        self.servant_pool()
        self.assertEqual(self.svc.get_servant_sr_candidates(0),tuple(draw.SERVANT_4_LIST))
        packet,_=self.call(16018,dict(id=10002),16019)
        self.assertEqual(set(packet['pool_details']['a_other_item']),set(draw.SERVANT_4_DRAW_ITEMS))

    def test_three_star_pool_is_not_narrowed_by_four_star_selection(self):
        self.select_child(1)
        packet,_=self.sr_draw(sr=0)
        self.assertEqual(packet['result'],0)
        self.assertEqual(packet['item'][0]['id'],max(draw.SERVANT_3_LIST))
        self.assertEqual(self.balance(36),10)


if __name__=='__main__':
    before='--before' in sys.argv
    if before:
        path=ROOT/'archive/servant_sr_race_20261103/before/v5_server/draw_service.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='DrawService')
        for name in ('draw','get_pool_details'):
            method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
            namespace=dict(vars(draw));exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
            setattr(draw.DrawService,name,namespace[name])
    names=[n for n in ServantRaceTests.__dict__ if n.startswith('test_')] if before else unittest.defaultTestLoader.getTestCaseNames(ServantRaceTests)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ServantRaceTests(n) for n in names))
    raise SystemExit(not result.wasSuccessful())
