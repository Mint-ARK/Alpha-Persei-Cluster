"""QTE/arena native protocol probes using only isolated memory accounts."""
import json
import unittest
from unittest.mock import patch
from verify_summer_board_props import PropTests
from verify_report_small_fixes import UID
import summer_board_service as board
import summer_board_movement as move
import summer_board_chance as chance
import summer_board_games as games


class GameTests(PropTests):
    def game(self,cid,chapter=4080101,gold=100):
        self.fresh([],chapter);extra,state=board.read(self.db,UID);hero=board.player(state['active']['map'])
        board.set_attribute(hero,4,gold);engine=move.Engine(chapter,state['active']['map']);chance.open_chance(engine,cid)
        board.save(self.db,UID,extra,state)
        return engine.frames
    def arena(self):
        self.game(501111,4080501);self.assertEqual(self.op(85020,chance_id=501111)[0]['result'],0)
    def arena_roll(self,dice=None):
        if dice is None:return self.op(85038)
        with patch.object(games.random,'randint',side_effect=dice):return self.op(85038)

    def test_qte_gold_three_grades_apply_native_mapping_once(self):
        for score,gold in [(1,1),(2,7),(3,15)]:
            self.game(101090,gold=0);packet,response=self.finish(101090,score=score)
            self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85023]);self.assertEqual(board.attribute(self.hero(),4),gold)
            before=self.state();self.assertNotEqual(self.finish(101090,score=score)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_qte_props_preselected_stable_and_finish_grants_only_grade_count(self):
        for score,count in [(1,1),(2,2),(3,3)]:
            self.game(101091);chosen=self.hero()['chance']['item_list'];self.assertEqual(len(chosen),3)
            before=self.record()
            with patch.object(chance.random,'sample',side_effect=AssertionError('QTE reroll')):self.enter()
            self.assertEqual(self.record(),before);packet,response=self.finish(101091,score=score)
            self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85023]);self.assertEqual(self.hero()['tmp_item'],chosen[:count])
            self.assertEqual(self.hero()['item'],[])
            for pid in chosen[:count]:self.assertEqual(self.op(85024,prop_id=pid)[0]['result'],0)
            self.assertEqual(self.hero()['item'],chosen[:count]);self.assertEqual(self.hero()['tmp_item'],[])

    def test_all_fourteen_qte_configs_have_native_outcomes_and_resume_shapes(self):
        for cid,cfg in board.CFG['chances'].items():
            if cfg['type']!=9:continue
            frames=self.game(int(cid));self.assertEqual(frames[0][0],85033)
            packet,response=self.enter();self.assertEqual(packet['result'],0)
            self.assertEqual(self.finish(int(cid),score=3)[0]['result'],0)

    def test_qte_bad_grade_nonzero_result_and_foreign_id_do_not_mutate(self):
        self.game(101090);before=self.state()
        for cid,result,score in [(101090,0,0),(101090,0,4),(101090,1,1),(101091,0,3)]:
            self.assertNotEqual(self.finish(cid,result,score)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_qte_pending_props_survive_relogin_without_a_second_finish(self):
        self.game(101091);self.finish(101091,score=2);snapshot=self.record();packet,response=self.enter()
        self.assertEqual(self.record(),snapshot);self.assertEqual(len(packet['map']['hero'][0]['tmp_item']),2)
        self.assertEqual(packet['map']['hero'][0]['chance']['chance_id'],0)
        pid=self.hero()['tmp_item'][0];self.assertEqual(self.op(85036,prop_id=pid)[0]['result'],0)

    def test_qte_full_bag_pending_requires_replace_or_discard(self):
        self.game(101091);self.edit(item=[10003]*6);self.finish(101091,score=1);pid=self.hero()['tmp_item'][0]
        before=self.state();self.assertNotEqual(self.op(85024,prop_id=pid)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.op(85024,prop_id=pid,replace_prop_id=10003)[0]['result'],0);self.assertEqual(len(self.hero()['item']),6)

    def test_qte_ack_failure_rolls_back_currency_or_pending_then_retry(self):
        for cid in (101090,101091):
            self.game(cid);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85023' and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.finish(cid,score=3)[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertEqual(self.finish(cid,score=3)[0]['result'],0)

    def test_arena_initial_three_counters_cost_and_no_normal_movement_changes(self):
        self.game(501111,4080501);self.assertEqual(self.hero()['chance']['choose_list'],[4,0,0])
        before=self.state();self.assertNotEqual(self.arena_roll()[0]['result'],0);self.assertEqual(self.state(),before)
        self.op(85020,chance_id=501111);before_hero=self.hero();packet,response=self.arena_roll([2])
        self.assertEqual(packet,dict(result=0,dice_list=[2],total_cnt=3));self.assertEqual(response.scs,[85039])
        self.assertEqual(self.hero()['chance']['choose_list'],[3,2,0]);self.assertEqual(board.attribute(self.hero(),4),95)
        for key in (5,8,13):self.assertEqual(board.attribute(self.hero(),key),board.attribute(before_hero,key))

    def test_arena_roll_six_refunds_one_attempt_and_no_85033_double_total(self):
        self.arena();packet,response=self.arena_roll([6]);self.assertEqual(packet['total_cnt'],4)
        self.assertEqual(self.hero()['chance']['choose_list'],[4,6,0]);self.assertEqual(response.scs,[85039])

    def test_arena_double_prop_consumes_once_and_refunds_for_each_six(self):
        self.arena();self.edit(item=[10001,10002]);before=self.hero()
        packet,response=self.use(10001);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85017])
        self.assertEqual(self.hero()['chance']['choose_list'][2],10001);self.assertEqual(self.hero()['use_item'],before['use_item'])
        before_state=self.state();self.assertNotEqual(self.use(10002,[5])[0]['result'],0);self.assertEqual(self.state(),before_state)
        packet,response=self.arena_roll([6,6]);self.assertEqual(packet['dice_list'],[6,6]);self.assertEqual(packet['total_cnt'],5)
        self.assertEqual(self.hero()['chance']['choose_list'],[5,12,0]);self.assertEqual(board.attribute(self.hero(),2),1)

    def test_arena_selected_dice_survives_reconnect_clears_per_roll(self):
        self.arena();self.edit(item=[10002]);self.assertEqual(self.use(10002,[5])[0]['result'],0)
        before=self.record();self.enter(4080501);self.assertEqual(self.record(),before)
        with patch.object(games.random,'randint',side_effect=AssertionError('arena chosen die')):packet,response=self.arena_roll()
        self.assertEqual(packet['dice_list'],[5]);self.assertNotIn('arena_prop_dice',self.record()['active'])
        self.assertEqual(self.hero()['chance']['choose_list'],[3,5,0]);self.assertEqual(self.arena_roll([1])[0]['dice_list'],[1])

    def test_arena_uses_whitelist_and_can_use_next_prop_after_each_roll(self):
        self.arena();self.edit(item=[10003,10001,10002]);before=self.state()
        self.assertNotEqual(self.use(10003)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.use(10001)[0]['result'],0);self.arena_roll([1,1]);self.assertEqual(self.use(10002,[4])[0]['result'],0)

    def test_arena_success_requires_actual_total_and_native_finish_event_once(self):
        self.arena();self.arena_roll([5]);self.arena_roll([5]);before=self.state()
        self.assertNotEqual(self.finish(501111,score=15)[0]['result'],0);self.assertEqual(self.state(),before)
        self.arena_roll([5]);before=self.state();self.assertNotEqual(self.arena_roll()[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.finish(501111,score=15);self.assertEqual(packet['result'],0)
        event=self.events(response)[0];self.assertEqual((event['trigger_type'],event['trigger_type_param'],event['event_pool_id']),(13,[501111],501991))
        self.assertEqual(self.record()['active']['map']['task'][0]['progress'],1);self.assertEqual(board.attribute(self.hero(),14),1)
        before=self.state();self.assertNotEqual(self.finish(501111,score=15)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_arena_failure_needs_exhaustion_adds_one_retry_and_no_finish_reward(self):
        self.arena();before=self.state();self.assertNotEqual(self.finish(501111,1,0)[0]['result'],0);self.assertEqual(self.state(),before)
        for _ in range(4):self.assertEqual(self.arena_roll([1])[0]['result'],0)
        self.assertEqual(self.hero()['chance']['choose_list'],[0,4,0]);before=self.state()
        self.assertNotEqual(self.arena_roll()[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.finish(501111,1,4);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85023])
        self.assertEqual(board.attribute(self.hero(),13),5);self.assertEqual(self.record()['active']['map']['task'][0]['progress'],0)
        extra,state=board.read(self.db,UID);engine=move.Engine(4080501,state['active']['map']);chance.open_chance(engine,501111);board.save(self.db,UID,extra,state)
        self.assertEqual(self.hero()['chance']['choose_list'],[5,0,0])

    def test_locked_arena_cancel_does_not_increase_retry_allowance(self):
        self.game(501111,4080501);self.assertEqual(self.finish(501111,1,0)[0]['result'],0)
        self.assertEqual(board.attribute(self.hero(),13),4);self.assertEqual(board.attribute(self.hero(),4),100)

    def test_arena_does_not_overwrite_normal_turn_prop_usage_or_selected_die(self):
        self.fresh([10002,10001],4080501);self.use(10002,[2]);self.roll()
        extra,state=board.read(self.db,UID);h=board.player(state['active']['map']);board.set_attribute(h,4,100)
        engine=move.Engine(4080501,state['active']['map']);chance.open_chance(engine,501111);board.save(self.db,UID,extra,state)
        self.op(85020,chance_id=501111);self.assertEqual(self.use(10001)[0]['result'],0);self.arena_roll([1,1])
        self.assertEqual(self.hero()['use_item'],10002);self.assertEqual(self.record()['active']['prop_dice'],2)
        self.assertEqual(self.hero()['chance']['choose_list'],[3,2,0])

    def test_arena_bad_score_and_wrong_terminal_result_rollback(self):
        self.arena();self.arena_roll([5]);before=self.state()
        for result,score in [(0,15),(1,5),(0,5),(2,5)]:
            self.assertNotEqual(self.finish(501111,result,score)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_arena_roll_prop_ack_and_finish_event_failures_are_atomic(self):
        self.arena();self.edit(item=[10002]);ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode;before=self.state()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85017' and d.get('result',0)==0 else encode(n,d)):
            self.assertNotEqual(self.use(10002,[6])[0]['result'],0)
        self.assertEqual(self.state(),before);self.use(10002,[6]);before=self.state()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85039' and d.get('result',0)==0 else encode(n,d)):
            self.assertNotEqual(self.arena_roll()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.arena_roll()[0]['dice_list'],[6])
        self.arena_roll([5]);self.arena_roll([4]);before=self.state()
        for sc in (85023,85031):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.finish(501111,score=15)[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_wrong_context_roll_and_nested_chances_keep_distinct_state(self):
        self.fresh([]);before=self.state();self.assertNotEqual(self.arena_roll()[0]['result'],0);self.assertEqual(self.state(),before)
        self.game(101090);before=self.state();self.assertNotEqual(self.arena_roll()[0]['result'],0);self.assertEqual(self.state(),before)
        for cfg in board.CFG['chances'].values():
            if cfg['type'] in (3,7,8,10,11,12,13):
                active=self.record()['active'];engine=move.Engine(active['chapter_id'],active['map'])
                engine.entity(0)['chance']=dict(chance_id=0,is_unlock=0,item_list=[],choose_list=[])
                chance.open_chance(engine,next(int(k) for k,v in board.CFG['chances'].items() if v is cfg))
                self.assertTrue(active['map']['nested_token'])

    def test_corrupt_qte_candidates_arena_counters_or_selected_die_rejected(self):
        for cid,chapter,mutate in [(101091,4080101,lambda a:board.player(a['map'])['chance'].update(item_list=[99999]*3)),
                                  (501111,4080501,lambda a:board.player(a['map'])['chance'].update(choose_list=[])),
                                  (501111,4080501,lambda a:board.player(a['map'])['chance'].update(choose_list=[4,0,10002]))]:
            self.game(cid,chapter);valid=self.db.get('users',UID)['extra'];extra=json.loads(valid);mutate(extra[board.STATE_KEY]['active'])
            self.db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra),UID));before=self.state()
            self.assertNotEqual(self.enter(chapter)[0]['result'],0);self.assertEqual(self.state(),before)
            self.db.execute('UPDATE users SET extra=? WHERE uid=?',(valid,UID))


if __name__=='__main__':
    suite=unittest.TestSuite(GameTests(n) for n in GameTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
