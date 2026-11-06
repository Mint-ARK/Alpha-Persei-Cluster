"""Isolated configured prop plans: no AccountDB or disk saves."""
import json
from pathlib import Path
import sys
import unittest
from copy import deepcopy
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# core import requires explicit memory DB for this standalone probe.
import os
os.environ['ACCOUNT_DB_PATH']=':memory:'
import autochess_special as special
from core import OperationError


class Service:
    def __init__(self):
        self.cfg=json.loads(Path(__file__).resolve().parents[1].joinpath('autochess_cfg.json').read_text('utf-8'));self.uid=1000
    def get_item_cfg(self,id):return self.cfg['items'].get(str(id))
    def get_chess_cfg(self,id):return self.cfg['chess'].get(str(id))
    def get_buff_cfg(self,id):return self.cfg['buffs'].get(str(id))
    def _allocate_uid(self,s=None):self.uid+=1;return self.uid
    def _piece_exp(self,p):return max(1,int(p.get('exp',p.get('star',1))))
    def _piece_level(self,p):return 1+sum(self._piece_exp(p)>=t for t in self.get_chess_cfg(p.get('chess_id',p.get('id')))['exp'])
    def _base_chess_exp(self,s):return 3 if s["stage_id"] in (303,304) else 1
    def _apply_chess_bonus(self,s,p):
        delta=s.get('chess_bonus',{}).get(str(p.get('chess_id',p.get('id'))),[0,0])
        p['atk']+=delta[0];p['hp']+=delta[1]
    def generate_shop_items(self,shop_level=1,activity_id=None,base_exp=1):
        return [dict(shop_unique_id=self._allocate_uid(),shop_type=0,id=101,index=i,exp=base_exp,star=1+sum(base_exp>=x for x in (3,6)),atk=2,hp=3,is_lock=0) for i in range(1,5)]


class PropTests(unittest.TestCase):
    def setUp(self):
        self.svc=Service()
        self.s=dict(stage_id=303,activity_id=3740601,shop_level=4,gold=100,
                    board_chess=[dict(unique_id=10101,chess_id=101,index=1,atk=2,hp=3,exp=1,star=1),dict(unique_id=10102,chess_id=102,index=2,atk=3,hp=3,exp=1,star=1)],
                    bench_chess=[],shop_items=self.svc.generate_shop_items())
    def use(self,id,index=0):
        item=dict(id=id,shop_unique_id=self.svc._allocate_uid(),shop_type=1,index=1,is_lock=0)
        self.s['shop_items'].append(item);before=deepcopy(self.s)
        try:special.buy_prop(self.svc,None,42,self.s,item,index)
        except Exception:
            self.assertEqual(self.s,before);raise
        self.assertNotIn(item,self.s['shop_items']);return self.s
    def target(self):return self.s['board_chess'][0]

    def test_101_103_104_304_use_config_stats_without_exp_merge(self):
        for iid,delta in [(101,(1,1)),(103,(3,0)),(104,(0,2)),(304,(3,3))]:
            self.setUp();self.use(iid,1);p=self.target()
            self.assertEqual((p['atk'],p['hp'],p['exp'],p['unique_id']),(2+delta[0],3+delta[1],1,10101))
            self.assertEqual(self.s['gold'],100-self.svc.get_item_cfg(iid)['shop_price'])

    def test_203_402_buff_each_own_piece(self):
        for iid,amount in [(203,1),(402,2)]:
            self.setUp();before=deepcopy(self.s['board_chess']);self.use(iid)
            self.assertEqual([(p['atk'],p['hp']) for p in self.s['board_chess']],[(p['atk']+amount,p['hp']+amount) for p in before])

    def test_102_steal_is_reward_slot_not_unplaceable_bench(self):
        self.use(102);reward=[p for p in self.s['shop_items'] if p['shop_type']==2]
        self.assertEqual(len(reward),1);self.assertEqual((reward[0]['atk'],reward[0]['hp']),(3,4))
        self.assertEqual(len(self.s['board_chess']),2);self.assertEqual(self.s['bench_chess'],[])
        self.assertEqual(len([p for p in self.s['shop_items'] if p['shop_type']==0]),3)

    def test_201_and_205_save_next_round_resource_effects(self):
        self.use(201);self.use(205)
        self.assertEqual(self.s['prop_effects'],dict(restart_money_bonus=1,money_ceiling_bonus=1,next_round_gold=2,used_count=2))

    def test_102_two_reward_slots_reuse_hole_and_reject_full_without_charge(self):
        self.use(102);self.use(102)
        rewards=[p for p in self.s['shop_items'] if p['shop_type']==2]
        self.assertEqual({p['index'] for p in rewards},{1,2})
        before_gold=self.s['gold'];before_used=self.s['prop_effects']['used_count']
        with self.assertRaises(OperationError):self.use(102)
        self.assertEqual(self.s['gold'],before_gold)
        self.assertEqual(self.s['prop_effects']['used_count'],before_used)
        self.s['shop_items'].remove(next(p for p in rewards if p['index']==1))
        self.use(102)
        self.assertEqual({p['index'] for p in self.s['shop_items'] if p['shop_type']==2},{1,2})

    def test_202_updates_current_stock_without_merging(self):
        self.use(202)
        self.assertTrue(all((p['atk'],p['hp'],p['exp'])==(3,4,1) for p in self.s['shop_items']))

    def test_204_refresh_guarantees_higher_rarity_and_keeps_other_types(self):
        self.s['shop_level']=2
        kept=dict(id=101,shop_type=2,shop_unique_id=991,index=1,atk=3,hp=4,exp=1);self.s['shop_items'].append(kept)
        self.use(204);normal=[p for p in self.s['shop_items'] if p['shop_type']==0]
        self.assertEqual(len(normal),4);self.assertEqual(self.svc.get_chess_cfg(normal[0]['id'])['star'],3);self.assertIn(kept,self.s['shop_items']);self.assertTrue(all(x['exp']==3 for x in normal))

    def test_301_durable_team_aura_not_permanent_repeated_stats(self):
        before=deepcopy(self.s['board_chess']);self.use(301);self.use(301)
        self.assertEqual(self.s['board_chess'],before);self.assertEqual(self.s['prop_effects']['team_bonus'],[2,2])

    def test_302_exp_is_level_threshold(self):
        self.target()['exp']=2;self.use(302,1)
        self.assertEqual((self.target()['exp'],self.target()['star']),(3,2))
        self.target()['exp']=6
        with self.assertRaises(OperationError):self.use(302,1)

    def test_303_higher_rarity_preserves_extra_stats_experience_and_uid(self):
        self.target().update(atk=12,hp=13,exp=4)
        with patch.object(special.random,'choice',side_effect=lambda seq:seq[0]):self.use(303,1)
        p=self.target();cfg=self.svc.get_chess_cfg(p['chess_id'])
        self.assertEqual(cfg['star'],2);self.assertEqual(p['exp'],4);self.assertEqual(p['unique_id'],10101)
        self.assertEqual((p['atk']-cfg['body'][0],p['hp']-cfg['body'][1]),(10,10))

    def test_401_shield_record_is_persistent_not_duplicate(self):
        self.use(401,1);b=self.target()['buff_list'][0]
        self.assertEqual(b['buff_id'],401);self.assertEqual(b['source_uid'],10101)
        with self.assertRaises(OperationError):self.use(401,1)

    def test_401_respects_native_inherent_shield_repeat_block(self):
        self.target()['chess_id']=108 # Native level buff 3 conflicts with 401.
        with self.assertRaises(OperationError):self.use(401,1)

    def test_prop_refresh_and_transform_apply_chess_kind_bonus_once(self):
        self.s['shop_level']=1;self.s['chess_bonus']={'101':[4,5],'201':[7,9]}
        with patch.object(special.random,'choice',side_effect=lambda seq:seq[0]):self.use(204)
        for p in self.s['shop_items']:
            if p['shop_type']!=0:continue
            cfg=self.svc.get_chess_cfg(p['id']);delta=self.s['chess_bonus'].get(str(p['id']),[0,0])
            self.assertEqual((p['atk'],p['hp']),(cfg['body'][0]+delta[0],cfg['body'][1]+delta[1]))
        self.target().update(atk=16,hp=18) # base + kind bonus + 10/10 personal growth
        with patch.object(special.random,'choice',side_effect=lambda seq:seq[0]):self.use(303,1)
        p=self.target();cfg=self.svc.get_chess_cfg(p['chess_id']);delta=self.s['chess_bonus'].get(str(p['chess_id']),[0,0])
        self.assertEqual((p['atk'],p['hp']),(cfg['body'][0]+delta[0]+10,cfg['body'][1]+delta[1]+10))

    def test_404_transfer_and_remove_keeps_recipient_uid_and_exp(self):
        with patch.object(special.random,'choice',side_effect=lambda seq:seq[0]):self.use(404,1)
        self.assertEqual(len(self.s['board_chess']),1);p=self.target()
        self.assertEqual((p['unique_id'],p['atk'],p['hp'],p['exp']),(10102,5,6,1))

    def test_405_adds_five_exp_not_changes_chess_rarity(self):
        with patch.object(special.random,'choice',side_effect=lambda seq:seq[0]):self.use(405)
        p=self.s['shop_items'][0];self.assertEqual((p['id'],p['exp'],p['star']),(101,6,3))

    def test_405_can_upgrade_unclaimed_reward_stock(self):
        self.s['shop_items']=self.s['shop_items'][:1]
        self.s['shop_items'][0]['shop_type']=2
        self.use(405)
        p=self.s['shop_items'][0]
        self.assertEqual((p['shop_type'],p['exp'],p['star']),(2,6,3))

    def test_guards_no_target_wrong_type_cost_unsupported_and_replay(self):
        with self.assertRaises(OperationError):self.use(101,5)
        with self.assertRaises(OperationError):self.use(201,1)
        self.s['gold']=0
        with self.assertRaises(OperationError):self.use(101,1)
        self.s['gold']=100
        with self.assertRaises(OperationError):self.use(305,1)
        self.s['free_prop']=1;self.use(101,1);self.assertEqual(self.s['gold'],100);self.assertEqual(self.s['free_prop'],0)

    def test_generation_respects_gate_activity_type_and_level(self):
        self.s['stage_id']=201;self.assertEqual(special.generate_props(self.svc,self.s),[])
        self.s['stage_id']=202;self.s['shop_level']=1
        for p in special.generate_props(self.svc,self.s,100):
            self.assertEqual(p['shop_type'],1);self.assertIn(p['id'],[101,102,103,104]);self.assertEqual(p['exp'],1)
        self.s['activity_id']=999
        with self.assertRaises(OperationError):special.generate_props(self.svc,self.s)

    def test_missing_upgrade_is_not_false_success(self):
        with self.assertRaises(OperationError):special.choose_upgrade(self.svc,self.s,0,1)

    def test_all_first_activity_props_are_supported(self):
        self.assertEqual(special.SUPPORTED,{c['id'] for c in self.svc.cfg['items'].values() if 3740601 in c['activity_id']})


if __name__=='__main__':unittest.main(verbosity=2)
