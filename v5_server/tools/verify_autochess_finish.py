"""Actual Core preparation, retry, replay and props boundary tests (memory only)."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from verify_autochess_persistence import PersistenceTests,UID
import autochess_service as ac
import codec

class FinishTests(PersistenceTests):
    def piece(self,cid=101,exp=1,pos=1,uid=100500):
        return dict(unique_id=uid,chess_id=cid,index=pos,exp=exp,star=exp,atk=5,hp=5)
    def item(self,cid=101,uid=100600,index=1,kind=0):
        return dict(shop_unique_id=uid,id=cid,index=index,exp=1,star=1,atk=2,hp=3,shop_type=kind,is_lock=0)

    def test_exp_thresholds_merge_sale_and_skills_use_level(self):
        self.start();self.edit(gold=50,board_chess=[self.piece(exp=3)],shop_items=[self.item()])
        self.op(89102,shop_type=0,shop_unique_id=100600,to_index=1)
        s=self.cold();self.assertEqual(s['board_chess'][0]['exp'],4);self.assertEqual(self.svc._piece_level(s['board_chess'][0]),2)
        before=s['gold'];self.op(89122,uid=100500);self.assertEqual(self.cold()['gold']-before,2)

    def test_first_activity_shop_does_not_mix_later_chess(self):
        self.start()
        for level in range(1,5):
            for i in self.svc.generate_shop_items(level):self.assertIn(3740601,self.svc.cfg['chess'][str(i['id'])]['activity_id'])
        with self.assertRaises(ac.OperationError):self.svc.generate_shop_items(1,999999)

    def test_brahma_base_exp_is_three_not_rarity_four(self):
        self.assertEqual(self.op(89116,other_param=303)[0]['result'],0)
        s=self.cold();normal=[i for i in s['shop_items'] if i['shop_type']==0]
        self.assertTrue(normal);self.assertTrue(all(i['exp']==3 for i in normal))
        item=normal[0];self.assertEqual(self.op(89102,shop_type=0,shop_unique_id=item['shop_unique_id'],to_index=1)[0]['result'],0)
        self.assertEqual(self.svc._piece_level(self.cold()['board_chess'][0]),2)

    def test_prepare_capacity_swap_and_legacy_invalid_board(self):
        self.start();self.edit(gold=50,board_chess=[self.piece(pos=1),self.piece(pos=2,uid=100501)],shop_items=[self.item()])
        for pos in (0,6,99):
            before=self.state();self.assertNotEqual(self.op(89102,shop_type=0,shop_unique_id=100600,to_index=pos)[0]['result'],0);self.assertEqual(self.state(),before)
            self.assertNotEqual(self.op(89104,chess_list=[dict(key=100500,value=pos)])[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.op(89104,chess_list=[dict(key=100500,value=2)])[0]['result'],0)
        self.assertEqual([p['unique_id'] for p in self.cold()['board_chess']],[100501,100500])
        self.edit(board_chess=[self.piece(pos=99)]);before=self.state();self.assertNotEqual(self.op(89144)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_terminal_retry_restores_preparation_without_double_growth(self):
        self.start();self.edit(hp=1,gold=7,round=4,win_count=1,board_chess=[self.piece(cid=201)],restart_times=2)
        with patch.object(self.svc,'_simulate_round_battle',return_value=(2,[])):
            self.op(89144);self.op(89108)
        self.cold();self.assertEqual(self.op(89116,other_param=101)[0]['result'],0)
        s=self.cold();self.assertEqual((s['round'],s['gold'],s['hp'],s['win_count'],s['restart_times']),(4,17,1,1,3))
        self.assertEqual(s['board_chess'][0]['atk'],5)
        self.assertNotEqual(self.op(89116,other_param=101)[0]['result'],0)
        packet=codec.decode(self.svc.build_89101_payload(self.db,UID),'sc_89101');self.assertEqual(packet['pve_restart_times'],3)

    def test_retry_limit_does_not_reset_session(self):
        self.pending(hp=1);self.edit(restart_times=999);before=self.state()
        self.assertNotEqual(self.op(89116,other_param=101)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_cold_replay_is_read_only_and_complete_ack_then_group(self):
        original,_=self.pending(win=True);self.op(89126);self.cold();before=self.state()
        with patch.object(self.svc,'_simulate_round_battle',side_effect=AssertionError('replay is not simulation')):
            packet,response=self.op(89142)
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs[:2],[89143,89113]);self.assertEqual(self.state(),before)
        self.assertEqual(packet['replay_info'],original['replay_info'])

    def test_draw_zero_with_last_hp_never_loses_life(self):
        self.start();self.edit(hp=1)
        self.op(89144)
        with patch.object(self.svc,'_simulate_round_battle',return_value=(0,[])):self.op(89108)
        self.assertEqual(self.cold()['state'],3);self.op(89126)
        s=self.cold();self.assertEqual((s['hp'],s['win_count'],s['defeat_count'],s['state']),(1,0,0,1))

    def test_coins_reset_each_round_not_accumulate(self):
        self.start();self.edit(gold=99);self.op(89144);self.op(89108);self.op(89126)
        self.assertEqual(self.cold()['gold'],10)

    def test_prop_id_does_not_buy_same_id_chess_or_add_exp(self):
        self.op(89116,other_param=202)
        self.edit(gold=10,board_chess=[self.piece()],shop_items=[self.item(kind=1)])
        packet,response=self.op(89102,shop_type=1,shop_unique_id=100600,to_index=1)
        self.assertEqual(packet['result'],0);s=self.cold();self.assertEqual((s['gold'],s['board_chess'][0]['exp'],s['board_chess'][0]['atk'],s['board_chess'][0]['hp']),(9,1,6,6))
        self.assertEqual(response.scs[:2],[89103,89101])

    def test_prop_lock_and_reward_stock_survive_refresh(self):
        self.op(89116,other_param=202)
        self.edit(shop_items=[dict(self.item(kind=1),is_lock=1),self.item(uid=100601,kind=2)])
        self.op(89118);s=self.cold();self.assertTrue(any(i['shop_unique_id']==100600 for i in s['shop_items']));self.assertTrue(any(i['shop_unique_id']==100601 for i in s['shop_items']))
        before=s['gold'];self.assertEqual(self.op(89102,shop_type=2,shop_unique_id=100601,to_index=1)[0]['result'],0);self.assertEqual(self.cold()['gold'],before)

    def test_no_successful_empty_upgrade_or_brahma_buy(self):
        self.start();before=self.state()
        self.assertNotEqual(self.op(89134,attr_index=0,buff_index=1)[0]['result'],0)
        self.assertNotEqual(self.op(89140)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_brahma_boss_after_four_wins_even_with_prior_loss(self):
        self.op(89116,other_param=303)
        self.edit(round=7,win_count=4,board_chess=[self.piece()])
        preview,_=self.op(89138,group_id=303,round=7)
        enemy=preview['enemy_auto_chess_board']['chess_list'][0]
        self.assertEqual(enemy['chess_id'],17)
        self.assertEqual({a['key']:a['value'] for a in enemy['chess_attr_list']},{1:500,2:900,3:5})
        self.assertEqual(enemy['buff_list'][0]['buff_id'],544)
        with patch.object(self.svc,'_simulate_round_battle',return_value=(1,[])):
            self.op(89144);packet,_=self.op(89108)
        self.assertEqual(packet['replay_info']['auto_chessboard_info2']['chess_list'],preview['enemy_auto_chess_board']['chess_list'])
        s=self.cold();self.assertEqual(s['pending_round']['enemy_team_id'],130399);self.assertEqual(s['state'],4)
        self.assertEqual(self.op(89126)[0]['result'],0)
        self.assertTrue(self.db.query('SELECT 1 FROM autochess_stage WHERE uid=? AND stage_id=303',(UID,)))
        self.assertNotEqual(self.svc._enemy_team_id(dict(stage_id=303,win_count=3,round=99)),130399)
        self.assertNotEqual(self.svc._enemy_team_id(dict(stage_id=304,win_count=4,round=5)),130399)

    def test_enemy_exp_and_attribute_offsets_are_not_position_or_level(self):
        records=self.svc._enemy_records(130304)
        self.assertEqual([p['index'] for p in records],[1,2,3,4,5])
        actor=records[3]
        self.assertEqual(next(a['value'] for a in actor['chess_attr_list'] if a['key']==3),6)
        expected=self.svc.get_chess_cfg(104)['level_buffs'][2]
        self.assertEqual([b['buff_id'] for b in actor['buff_list']],expected)
        for p in records:
            self.assertTrue(codec.encode('sc_89139',dict(result=0,enemy_auto_chess_board=dict(chess_list=[p]))))

    def test_sell_summon_visible_and_first_activity_only(self):
        self.start();self.edit(board_chess=[self.piece(cid=211)])
        self.assertEqual(self.op(89122,uid=100500)[0]['result'],0)
        s=self.cold();self.assertFalse(s['bench_chess']);self.assertEqual(len(s['board_chess']),1)
        p=s['board_chess'][0];self.assertTrue(1<=p['index']<=5)
        cfg=self.svc.get_chess_cfg(p['chess_id']);self.assertEqual(cfg['star'],2);self.assertIn(3740601,cfg['activity_id'])

    def test_prepare_intrinsic_skill_and_attached_buff_restore_without_uid_reuse(self):
        self.start();piece=self.piece(cid=108,exp=3)
        piece['buff_list']=[dict(unique_id=900000,buff_id=401,owner_type=0,source_type=1,source_uid=piece['unique_id'],source_cfg_id=108)]
        self.edit(board_chess=[piece]);s=self.cold()
        p=codec.decode(self.svc.build_89101_payload(self.db,UID),'sc_89101')['auto_chessboard_info']['chess_list'][0]
        self.assertEqual({b['buff_id'] for b in p['buff_list']},set(self.svc.get_chess_cfg(108)['level_buffs'][1]+[401]))
        self.assertGreater(self.svc._allocate_uid(s),900000)

    def test_future_stock_applies_saved_global_growth_exactly_once(self):
        self.start();s=self.edit(chess_bonus={'206':[3,4]})
        self.cold();s=self.svc.get_session(UID)
        item=self.item(cid=206)
        with patch.object(self.svc,'generate_shop_items',return_value=[deepcopy(item)]):
            generated=self.svc._generate_session_shop(s)
        self.assertEqual((generated[0]['atk'],generated[0]['hp']),(5,7))
        self.edit(shop_items=generated)
        self.assertEqual(self.op(89102,shop_type=0,shop_unique_id=100600,to_index=1)[0]['result'],0)
        p=self.cold()['board_chess'][0];self.assertEqual((p['atk'],p['hp']),(5,7))

    def test_action_budget_failure_cannot_save_win_or_pending(self):
        from autochess_combat import Combat
        self.start();self.edit(win_count=1,board_chess=[dict(self.piece(),atk=0,hp=100000)])
        self.op(89144);before=self.state()
        with patch.object(Combat,'MAX_ROUNDS',2):
            self.assertNotEqual(self.op(89108)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.cold()['state'],2)
        self.assertFalse(self.db.query('SELECT * FROM autochess_stage'))

    def test_actual_preparation_end_saves_intrinsic_buff_and_replay_codec(self):
        self.start();self.edit(board_chess=[dict(self.piece(cid=110),atk=4,hp=50)])
        self.op(89144);packet,response=self.op(89108)
        self.assertEqual(packet['result'],0)
        self.assertTrue(packet['replay_info']['auto_chessboard_info1']['chess_list'][0]['buff_list'])
        self.assertEqual(response.scs[:3],[89109,89125,89113])
        self.assertEqual(self.op(89126)[0]['result'],0)
        self.assertGreater(self.cold()['board_chess'][0]['atk'],4)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(FinishTests(n) for n in FinishTests.__dict__ if n.startswith('test_')))
    raise SystemExit(not result.wasSuccessful())
