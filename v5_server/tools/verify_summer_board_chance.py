"""Memory-only Core probes for native chance/prop callbacks and reconnects."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from verify_summer_board_movement import MovementTests
from verify_report_small_fixes import UID
import summer_board_service as board
import summer_board_chance as chance
import summer_board_movement as movement


class ChanceTests(MovementTests):
    def op(self,cmd,**data):return self.call(cmd,data,cmd+1)
    def hero(self):return board.player(self.record()['active']['map'])
    def edit(self,**values):
        extra,state=board.read(self.db,UID);board.player(state['active']['map']).update(values);board.save(self.db,UID,extra,state)
    def opened(self,cid,gold=100):
        self.seed(budget=0,phase=3,buffs=[]);extra,state=board.read(self.db,UID)
        board.set_attribute(board.player(state['active']['map']),4,gold)
        engine=movement.Engine(4080101,state['active']['map']);chance.open_chance(engine,cid)
        board.save(self.db,UID,extra,state)
        return engine.frames
    def finish(self,cid,result=0,score=0):return self.op(85022,chance_id=cid,result=result,score=score)

    def test_natural_tutorial_chance_pending_confirmation_and_next_fixed_roll(self):
        self.enter();self.roll()
        for x,z in [(4,7),(5,7),(6,8)]:self.assertEqual(self.step(x,z)[0]['result'],0)
        self.assertEqual(self.hero()['chance']['item_list'],[1,2,10003])
        self.assertEqual(self.hero()['tmp_item'],[]);self.assertEqual(self.hero()['item'],[])
        snapshot=self.record();packet,response=self.enter()
        self.assertEqual(response.scs,[85001]);self.assertEqual(self.record(),snapshot)
        self.assertEqual(packet['map']['hero'][0]['chance']['item_list'],[1,2,10003])
        packet,response=self.finish(101031);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85023])
        self.assertEqual(self.hero()['tmp_item'],[10003]);self.assertEqual(self.hero()['item'],[])
        self.assertNotEqual(self.end()[0]['result'],0)
        self.assertEqual(self.op(85024,prop_id=10003,replace_prop_id=0)[0]['result'],0)
        self.assertEqual(self.hero()['item'],[10003]);self.assertEqual(self.hero()['tmp_item'],[])
        self.assertEqual(self.end()[0]['result'],0);self.assertEqual(self.roll()[0]['dice_list'],[3])

    def test_pending_prop_survives_reconnect_without_regrant_or_reroll(self):
        self.opened(101031);self.finish(101031);before=self.record()
        packet,response=self.enter();self.assertEqual(response.scs,[85001]);self.assertEqual(self.record(),before)
        self.assertEqual(packet['map']['hero'][0]['tmp_item'],[10003])
        self.op(85024,prop_id=10003);before=self.state()
        for cmd,args in [(85022,dict(chance_id=101031,result=0,score=0)),(85024,dict(prop_id=10003)),(85036,dict(prop_id=10003))]:
            self.assertNotEqual(self.op(cmd,**args)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_paid_target_unlock_once_then_native_event_and_no_full_map(self):
        self.opened(101111,gold=5);before=self.state()
        self.assertNotEqual(self.finish(101111)[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.op(85020,chance_id=101111);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85021])
        self.assertEqual(board.attribute(self.hero(),4),0);before=self.state()
        self.assertNotEqual(self.op(85020,chance_id=101111)[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.finish(101111);self.assertEqual(packet['result'],0)
        events=self.packets(response,85031);self.assertEqual(events[0]['event']['trigger_type'],13)
        self.assertEqual(events[0]['event']['trigger_type_param'],[101111]);self.assertEqual(events[0]['event']['event_pool_id'],101991)
        self.assertEqual(self.record()['active']['map']['task'][0]['progress'],1)
        self.assertEqual(board.attribute(self.hero(),11),1);self.assertEqual(self.hero()['chance'],chance.EMPTY)
        self.assertNotIn(85033,response.scs);self.assertNotIn(85001,response.scs)

    def test_cancel_locked_target_no_charge_or_finish_effect(self):
        self.opened(101111,gold=0);packet,response=self.finish(101111,1)
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85023]);self.assertEqual(board.attribute(self.hero(),11),0)
        self.assertEqual(self.hero()['chance'],chance.EMPTY)

    def test_insufficient_gold_wrong_chance_bad_result_and_score_are_readonly(self):
        self.opened(101111,gold=4);before=self.state()
        for cmd,args in [(85020,dict(chance_id=101111)),(85020,dict(chance_id=101112)),
                         (85022,dict(chance_id=101111,result=2)),(85022,dict(chance_id=101111,result=1,score=3))]:
            self.assertNotEqual(self.op(cmd,**args)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_shop_stock_purchase_pending_replace_and_repeat_guard(self):
        with patch.object(chance.random,'sample',return_value=[10001,10002,10003]):self.opened(101050,gold=50)
        self.edit(item=[10004]*6)
        packet,response=self.op(85018,chance_id=101050,prop_id=10003)
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85019]);self.assertEqual(board.attribute(self.hero(),4),45)
        self.assertEqual(self.hero()['tmp_item'],[10003]);self.assertEqual(self.hero()['chance']['choose_list'],[10003])
        before=self.state()
        for pid in [10003,10004,99999]:
            self.assertNotEqual(self.op(85018,chance_id=101050,prop_id=pid)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.op(85024,prop_id=10003)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.op(85024,prop_id=10003,replace_prop_id=10004)[0]['result'],0)
        self.assertEqual(self.hero()['item'],[10004]*5+[10003]);self.assertEqual(board.attribute(self.hero(),4),45)
        self.assertEqual(self.finish(101050,1)[0]['result'],0)

    def test_shop_pending_purchase_survives_close_and_reload(self):
        with patch.object(chance.random,'sample',return_value=[10001,10002,10003]):self.opened(101050)
        self.op(85018,chance_id=101050,prop_id=10001);self.finish(101050,1);self.enter()
        self.assertEqual(self.hero()['tmp_item'],[10001]);self.assertEqual(self.op(85024,prop_id=10001)[0]['result'],0)

    def test_discard_only_pending_and_count_duplicate_occurrences(self):
        self.seed(budget=0,phase=3,buffs=[]);self.edit(item=[10003],tmp_item=[10002,10002])
        before=self.state();self.assertNotEqual(self.op(85036,prop_id=10003)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.op(85036,prop_id=10002)[0]['result'],0);self.assertEqual(self.hero()['tmp_item'],[10002])
        self.assertEqual(self.op(85024,prop_id=10002)[0]['result'],0);self.assertEqual(self.hero()['item'],[10003,10002])
        self.assertEqual(self.hero()['tmp_item'],[])

    def test_prop_chance_goes_to_pending_not_owned_and_cannot_inject_item(self):
        with patch.object(chance.random,'sample',return_value=[10002,10003]):self.opened(101041)
        self.assertEqual(self.finish(101041)[0]['result'],0);self.assertEqual(self.hero()['tmp_item'],[10002,10003])
        self.assertEqual(self.hero()['item'],[]);before=self.state()
        self.assertNotEqual(self.op(85024,prop_id=10004)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.op(85024,prop_id=10002,replace_prop_id=10003)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_random_result_frozen_on_open_and_gold_applied_once(self):
        with patch.object(chance.random,'randrange',return_value=1):self.opened(101030,gold=0)
        with patch.object(chance.random,'randrange',side_effect=AssertionError('reroll')):self.enter()
        self.assertEqual(self.hero()['chance']['item_list'],[2,1,10])
        self.assertEqual(self.finish(101030)[0]['result'],0);self.assertEqual(board.attribute(self.hero(),4),10)
        before=self.state();self.assertNotEqual(self.finish(101030)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_random_buff_uses_only_nested_add_event_no_duplicate_buff_instruction(self):
        with patch.object(chance.random,'randrange',return_value=9):self.opened(201030)
        packet,response=self.finish(201030);self.assertEqual(packet['result'],0)
        self.assertEqual([b['id'] for b in self.hero()['buff']],[10104])
        for p in self.packets(response,85031):
            self.assertNotIn(20005,[r['event'][0]['integer'] for r in p['event']['event_list']])

    def test_use_hat_and_double_dice_match_local_callback_and_turn_cleanup(self):
        self.seed(budget=0,phase=0,buffs=[]);self.edit(item=[10003,10001])
        packet,response=self.op(85016,prop_id=10003,params=[]);self.assertEqual(packet['result'],0)
        self.assertEqual(self.hero()['use_item'],10003);self.assertEqual([b['id'] for b in self.hero()['buff']],[10003])
        self.assertEqual(response.scs[-1],85017);before=self.state()
        self.assertNotEqual(self.op(85016,prop_id=10001)[0]['result'],0);self.assertEqual(self.state(),before)
        extra,state=board.read(self.db,UID);h=board.player(state['active']['map']);board.set_attribute(h,8,3);board.save(self.db,UID,extra,state)
        self.assertEqual(self.end()[0]['result'],0);self.assertEqual(self.hero()['use_item'],0)
        self.assertEqual(self.op(85016,prop_id=10001)[0]['result'],0);self.assertEqual(board.attribute(self.hero(),2),2)
        self.assertEqual(self.roll()[0]['dice_list'],[2,2])
        extra,state=board.read(self.db,UID);h=board.player(state['active']['map']);board.set_attribute(h,5,0);board.set_attribute(h,8,3);board.save(self.db,UID,extra,state)
        self.assertEqual(self.end()[0]['result'],0);self.assertEqual(board.attribute(self.hero(),2),1)

    def test_pending_prop_prevents_roll_and_target_use_not_faked(self):
        self.seed(budget=0,phase=0,buffs=[]);self.edit(tmp_item=[10003],item=[10005])
        before=self.state();self.assertNotEqual(self.roll()[0]['result'],0);self.assertEqual(self.state(),before)
        self.op(85036,prop_id=10003);before=self.state()
        self.assertNotEqual(self.op(85016,prop_id=10005,params=[3,7])[0]['result'],0);self.assertEqual(self.state(),before)

    def test_chance_frame_and_move_ack_failure_rollback_choice_and_budget(self):
        self.seed(pos=(5,7),budget=1,phase=2,buffs=[]);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for sc in (85033,85011):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.step(6,8)[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_all_mutation_ack_failures_rollback_and_allow_retry(self):
        for cid,cmd,args in [(101111,85020,dict(chance_id=101111)),(101031,85022,dict(chance_id=101031,result=0)),
                             (101050,85018,dict(chance_id=101050,prop_id=10003)),(101031,85024,dict(prop_id=10003)),
                             (101031,85036,dict(prop_id=10003))]:
            with patch.object(chance.random,'sample',side_effect=lambda population,k:list(population)[:k]):self.opened(cid)
            if cmd in (85024,85036):self.finish(cid)
            before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(cmd+1) and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.op(cmd,**args)[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertEqual(self.op(cmd,**args)[0]['result'],0)

    def test_finish_event_encoding_failure_rollback_charge_progress_and_chance(self):
        self.opened(101111);self.op(85020,chance_id=101111);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85031' else encode(n,d)):
            self.assertNotEqual(self.finish(101111)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.finish(101111)[0]['result'],0)

    def test_export_contract_reproduces_chance_and_prop_metadata(self):
        from export_summer_board_contract import build
        self.assertEqual(build(),board.CFG);self.assertEqual(len(board.CFG['props']),12)

    def test_teleport_chance_uses_native_finish_event(self):
        self.opened(101080);self.edit(pos=dict(x=6,z=8));packet,response=self.finish(101080)
        self.assertEqual(packet['result'],0);self.assertEqual(self.hero()['pos'],dict(x=3,z=7))
        event=self.packets(response,85031)[0]['event'];self.assertEqual((event['trigger_type'],event['event_pool_id']),(13,101081))

    def test_use_prop_ack_failure_restores_owned_buff_and_usage(self):
        self.seed(budget=0,phase=0,buffs=[]);self.edit(item=[10003]);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85017' and d.get('result',0)==0 else encode(n,d)):
            self.assertNotEqual(self.op(85016,prop_id=10003)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.op(85016,prop_id=10003)[0]['result'],0)

    def test_invalid_saved_chance_is_blocked_before_resume(self):
        self.opened(101031);valid=self.db.get('users',UID)['extra']
        import json
        for mutate in [lambda h:h['chance'].update(item_list=[1,2,99999]),lambda h:h.update(tmp_item=[99999]),
                       lambda h:h.update(chance=dict(chance_id=0,is_unlock=0,item_list=[10003],choose_list=[])),
                       lambda h:h.update(item=[10003]*7)]:
            extra=json.loads(valid);mutate(extra[board.STATE_KEY]['active']['map']['hero'][0])
            self.db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra),UID));before=self.state()
            self.assertNotEqual(self.enter()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_random_npc_stop_result_is_persisted_before_confirmation(self):
        self.seed(pos=(5,7),budget=1,phase=2,buffs=[])
        with patch.dict(board.CFG['events'],{'101031':dict(type=2,player_params=[201030],npc_params=[])}),patch.object(chance.random,'randrange',return_value=10):
            self.assertEqual(self.step(6,8)[0]['result'],0)
        self.assertEqual(self.hero()['chance']['item_list'],[11,5,3])
        self.assertEqual(self.finish(201030)[0]['result'],0)


if __name__=='__main__':
    suite=unittest.TestSuite(ChanceTests(n) for n in ChanceTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
