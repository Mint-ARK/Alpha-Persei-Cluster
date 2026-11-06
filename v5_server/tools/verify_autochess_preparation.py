"""Actual Core, in-memory preparation economy and prop-skill checks."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from verify_autochess_persistence import PersistenceTests
import autochess_preparation as prep
import autochess_service as ac


class PreparationTests(PersistenceTests):
    def piece(self,cid,pos=1,exp=1,uid=None):
        return dict(unique_id=uid or 100500+pos,chess_id=cid,index=pos,exp=exp,star=1,atk=10,hp=10)

    def item(self,cid=101,kind=0,uid=101000,index=1):
        return dict(shop_unique_id=uid,id=cid,shop_type=kind,index=index,exp=1,star=1,atk=2,hp=3,is_lock=0)

    def start_props(self,board):
        self.assertEqual(self.op(89116,other_param=202)[0]['result'],0)
        self.edit(board_chess=board,gold=100)

    def prop(self,cid,index=0):
        self.edit(shop_items=[self.item(cid,1)])
        result=self.op(89102,shop_type=1,shop_unique_id=101000,to_index=index)
        self.assertEqual(result[0]['result'],0)
        return self.cold()

    def next_round(self):
        self.assertEqual(self.op(89144)[0]['result'],0)
        with patch.object(self.svc,'_simulate_round_battle',return_value=(3,[])):
            self.assertEqual(self.op(89108)[0]['result'],0)
        self.assertEqual(self.op(89126)[0]['result'],0)
        return self.cold()

    def test_prepare_hooks_use_config_level_and_repeat_parameters(self):
        self.start()
        self.edit(board_chess=[self.piece(101,1),self.piece(201,2,3),
                              self.piece(304,4,6),self.piece(302,5,3)])
        # Exaggerated native parameter proves this is not a chess-ID/level formula.
        self.svc.cfg['buffs']['3022']['param']=[4]
        self.assertEqual(self.op(89144)[0]['result'],0)
        board={p['index']:p for p in self.cold()['board_chess']}
        self.assertEqual((board[1]['atk'],board[1]['hp']),(50,40)) # (2+6)*5, 6*5
        self.assertEqual(board[4]['atk'],10)
        before=self.state();self.assertNotEqual(self.op(89144)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_separate_repeat_owners_add_their_extra_effects(self):
        self.start();self.edit(board_chess=[self.piece(201,1),self.piece(101,2),
                                          self.piece(302,3),self.piece(302,4)])
        self.op(89144)
        self.assertEqual(self.cold()['board_chess'][1]['atk'],13)

    def test_round_start_uses_economy_and_attached_buff_params(self):
        self.start();self.edit(board_chess=[self.piece(101,1,3),self.piece(202,2,6)],
                              gold=91,free_refresh=99,prop_effects={'restart_money_bonus':1,'next_round_gold':2})
        self.svc.cfg['buffs']['1012']['param']=[1,1,7]
        self.svc.cfg['buffs']['2026']['param']=[1,16,8]
        s=self.next_round()
        self.assertEqual((s['gold'],s['free_refresh']),(20,8))
        self.assertNotIn('next_round_gold',s['prop_effects'])

    def test_buy_and_sell_hooks_respect_actor_and_level(self):
        self.start();self.edit(board_chess=[self.piece(401,1,3)],gold=50,shop_items=[self.item()])
        self.svc.cfg['buffs']['4012']['param']=[114,1,[7,8]]
        with patch.object(prep.random,'sample',side_effect=lambda xs,n:xs[:n]):
            self.op(89102,shop_type=0,shop_unique_id=101000,to_index=2)
        self.assertEqual((self.cold()['board_chess'][0]['atk'],self.cold()['board_chess'][0]['hp']),(17,18))
        self.edit(board_chess=[self.piece(401,1,3),self.piece(102,2,6)])
        with patch.object(prep.random,'sample',side_effect=lambda xs,n:xs[:n]):self.op(89122,uid=100502)
        p=self.cold()['board_chess'][0]
        self.assertEqual((p['atk'],p['hp']),(14,23)) # Thor +4/+4, sold102 +0/+9
        self.edit(board_chess=[self.piece(101,1),self.piece(401,2)])
        self.op(89122,uid=100502)
        self.assertEqual(self.cold()['board_chess'][0]['atk'],10) # selling self is not OTHER

    def test_legacy_211_summon_uses_rarity_param_and_visible_cell(self):
        self.start();self.edit(board_chess=[self.piece(211,2,6)])
        self.assertEqual(self.op(89122,uid=100502)[0]['result'],0)
        p=self.cold()['board_chess'][0];cfg=self.svc.get_chess_cfg(p['chess_id'])
        self.assertEqual(cfg['star'],4);self.assertIn(3740601,cfg['activity_id'])
        self.assertIn(p['index'],range(1,6));self.assertEqual(self.cold()['bench_chess'],[])

    def test_prop_repeat_one_charge_persisted_quota_and_round_reset(self):
        self.start_props([self.piece(301,1,3),self.piece(101,2)])
        for expected in (12,14,15):
            before=self.cold()['gold'];s=self.prop(101,2)
            self.assertEqual(s['board_chess'][1]['atk'],expected)
            self.assertEqual(before-s['gold'],1)
        self.assertEqual(s['prop_effects']['used_count'],3)
        self.assertEqual(s['board_chess'][0]['prop_repeat_used'],2)
        self.next_round();s=self.prop(101,2)
        self.assertEqual(s['board_chess'][1]['atk'],17)

    def test_prop_hook_strengthens_only_real_shields_once_per_item(self):
        self.start_props([self.piece(207,1,3),self.piece(108,2),self.piece(101,3)])
        s=self.prop(101,3)
        self.assertEqual([p['atk'] for p in s['board_chess']],[12,12,11])
        s=self.prop(401,3)
        self.assertEqual([p['atk'] for p in s['board_chess']],[14,14,13])

    def test_failed_prop_does_not_consume_repeat_and_removed_target_is_not_repeated(self):
        self.start_props([self.piece(301,1),self.piece(101,2)])
        self.edit(shop_items=[self.item(101,1)])
        before=self.state();self.assertNotEqual(self.op(89102,shop_type=1,shop_unique_id=101000,to_index=5)[0]['result'],0)
        self.assertEqual(self.state(),before)
        s=self.prop(404,2)
        self.assertEqual(len(s['board_chess']),1)
        self.assertEqual((s['board_chess'][0]['atk'],s['board_chess'][0]['hp']),(20,20))
        self.assertEqual(s['gold'],100-self.svc.get_item_cfg(404)['shop_price'])

    def test_core_response_failure_rolls_back_prop_quota_and_stats(self):
        self.start_props([self.piece(301,1),self.piece(101,2)])
        self.edit(shop_items=[self.item(101,1)]);before=self.state();encoder=ac._encode
        def fail(name,data):
            if name=='sc_89103':raise RuntimeError('probe response failed')
            return encoder(name,data)
        with patch.object(ac,'_encode',side_effect=fail):
            self.assertNotEqual(self.op(89102,shop_type=1,shop_unique_id=101000,to_index=2)[0]['result'],0)
        self.assertEqual(self.state(),before)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(
        PreparationTests(n) for n in PreparationTests.__dict__ if n.startswith('test_')))
    raise SystemExit(not result.wasSuccessful())
