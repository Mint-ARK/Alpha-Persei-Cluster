"""Native 85016 prop inputs and durable effects, tested only on memory saves."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from verify_summer_board_npc import NPCTests
from verify_report_small_fixes import UID
import summer_board_service as board
import summer_board_movement as move
import summer_board_props as props


class PropTests(NPCTests):
    def fresh(self,items,chapter=4080101):
        self.seed(chapter,pos=tuple(board.LEVELS[chapter]['init_pos']),budget=0,phase=0,buffs=[])
        extra,state=board.read(self.db,UID);m=state['active']['map'];m['hero']=[board.player(m)]
        h=board.player(m);h['item']=list(items);board.set_attribute(h,3,0);board.save(self.db,UID,extra,state)
    def use(self,pid,params=None):return self.op(85016,prop_id=pid,params=params or [])
    def force_end(self):
        extra,state=board.read(self.db,UID);h=board.player(state['active']['map'])
        board.set_attribute(h,5,0);board.set_attribute(h,8,3);board.save(self.db,UID,extra,state)
        return self.end()

    def test_selected_dice_uses_85016_parameter_and_85012_not_arena_85038(self):
        self.fresh([10002]);packet,response=self.use(10002,[5]);self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs,[85017]);self.assertEqual(self.record()['active']['prop_dice'],5)
        with patch.object(board.random,'randint',side_effect=AssertionError('random selected die')):
            self.assertEqual(self.roll()[0]['dice_list'],[5])
        before=self.state();self.assertNotEqual(self.op(85038)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_selected_dice_invalid_range_does_not_consume_item(self):
        self.fresh([10002]);extra,state=board.read(self.db,UID);board.set_attribute(board.player(state['active']['map']),1,3);board.save(self.db,UID,extra,state)
        before=self.state()
        for params in ([],[0],[4],[7],[1,2]):
            self.assertNotEqual(self.use(10002,params)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.use(10002,[3])[0]['result'],0);self.assertEqual(self.roll()[0]['dice_list'],[3])

    def test_selected_dice_survives_reconnect_and_is_removed_after_turn(self):
        self.fresh([10002]);self.use(10002,[6]);before=self.record();packet,response=self.enter()
        self.assertEqual(response.scs,[85001]);self.assertEqual(self.record(),before);self.assertNotIn('prop_dice',packet['map'])
        self.assertEqual(self.roll()[0]['dice_list'],[6]);self.assertEqual(self.force_end()[0]['result'],0)
        self.assertNotIn('prop_dice',self.record()['active']);self.assertEqual(self.hero()['use_item'],0)
        with patch.object(board.random,'randint',return_value=2):self.assertEqual(self.roll()[0]['dice_list'],[2])

    def test_selected_dice_does_not_erase_guide_next_turn_fixed_value(self):
        self.enter();self.edit(item=[10002]);self.assertEqual(self.use(10002,[5])[0]['result'],0)
        self.assertEqual(self.roll()[0]['dice_list'],[5]);self.assertEqual(self.force_end()[0]['result'],0)
        self.assertEqual(board.attribute(self.hero(),3),3);self.assertEqual(self.roll()[0]['dice_list'],[3])

    def test_selected_dice_supports_current_two_dice_without_changing_range(self):
        self.fresh([10002]);extra,state=board.read(self.db,UID);h=board.player(state['active']['map']);board.set_attribute(h,2,2);board.save(self.db,UID,extra,state)
        self.use(10002,[4]);self.assertEqual(self.roll()[0]['dice_list'],[4,4])

    def test_gun_removes_only_target_no_extra_capture_gold_or_event(self):
        self.fresh([10005]);first=self.spawn(pos=(6,6));other=self.spawn(pos=(5,6));before=self.state()
        packet,response=self.use(10005,[6,6]);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85017])
        self.assertEqual([h['id'] for h in self.record()['active']['map']['hero']],[0,other])
        self.assertEqual(board.attribute(self.hero(),4),0);self.assertEqual(self.hero()['use_item'],10005)
        after=self.state()
        for table in before.keys()-{'users'}:self.assertEqual(before[table],after[table])

    def test_swap_moves_both_positions_and_directions_without_encounter_or_floor_events(self):
        self.fresh([10006]);ref=self.spawn(pos=(5,6));before_hero=deepcopy(self.hero());before_npc=deepcopy(self.unit(ref))
        packet,response=self.use(10006,[5,6]);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85017])
        self.assertEqual((self.hero()['pos'],self.hero()['direction']),(before_npc['pos'],before_npc['direction']))
        self.assertEqual((self.unit(ref)['pos'],self.unit(ref)['direction']),(before_hero['pos'],before_hero['direction']))
        self.assertEqual(board.attribute(self.hero(),4),0);self.assertEqual(self.hero()['item'],[])

    def test_target_coordinate_must_exist_and_contain_npc(self):
        for pid in (10005,10006,10010):
            self.fresh([pid]);before=self.state()
            for coords in ([],[6],[999,999],[6,6],[6,6,0]):
                self.assertNotEqual(self.use(pid,coords)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_target_buff_self_or_npc_uses_native_add_event_binding(self):
        for own in (True,False):
            self.fresh([10010]);ref=0 if own else self.spawn(pos=(6,6),fixed=0);coords=[3,7] if own else [6,6]
            packet,response=self.use(10010,coords);self.assertEqual(packet['result'],0)
            unit=self.hero() if own else self.unit(ref);self.assertEqual([b['id'] for b in unit['buff']],[10010])
            self.assertEqual(board.attribute(unit,3),1)
            event=self.events(response)[0];self.assertEqual((event['trigger_type'],event['trigger_type_param'],event['trigger_player_a']),(16,[10010],ref))
            self.assertEqual([r['event'][0]['integer'] for r in event['event_list']],[20002])

    def test_invincible_npc_ignores_target_debuff_but_consumes_prop_as_native(self):
        self.fresh([10010]);ref=self.spawn(fixed=0);extra,state=board.read(self.db,UID);h=next(h for h in state['active']['map']['hero'] if h['id']==ref);board.set_attribute(h,9,1);board.save(self.db,UID,extra,state)
        packet,response=self.use(10010,[6,6]);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85017])
        self.assertEqual(self.unit(ref)['buff'],[]);self.assertEqual(self.hero()['item'],[])

    def test_place_barrier_or_bomb_uses_map_extension_and_no_full_snapshot(self):
        for pid,tag in [(10008,2),(10009,3)]:
            self.fresh([pid],4080201);packet,response=self.use(pid,[7,4]);self.assertEqual(packet['result'],0)
            self.assertEqual(response.scs,[85017]);change=self.record()['active']['map']['object'][0]
            self.assertEqual((change['pos'],change['tag']),(dict(x=7,z=4),tag))
            tile=move.grid(4080201,self.record()['active']['map'],7,4);self.assertEqual(tile['thingTypeID'],1000+tag)
            packet,response=self.enter(4080201);self.assertEqual(packet['map']['object'][0]['tag'],tag)

    def test_place_rejects_occupied_automatic_wall_and_missing_extension(self):
        self.fresh([10008]);before=self.state();self.assertNotEqual(self.use(10008,[5,6])[0]['result'],0);self.assertEqual(self.state(),before)
        self.fresh([10008],4080201);self.spawn(pos=(7,4));before=self.state()
        for coords in ([2,8],[7,4],[0,0]):
            self.assertNotEqual(self.use(10008,coords)[0]['result'],0);self.assertEqual(self.state(),before)
        native=move.MAPS[4080201];m=self.record()['active']['map']
        auto=next((list(map(int,k.split(','))) for k in native['tiles'] if move._hex(move.grid(4080201,m,*map(int,k.split(','))))['updatable']==1),None)
        self.assertIsNotNone(auto);self.assertNotEqual(self.use(10008,auto)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_repair_removes_only_clash_effects_and_sends_destroy_event(self):
        self.fresh([10011]);self.edit(buff=[dict(id=10010,duration=0,buff_effect=[]),dict(id=30001,duration=0,buff_effect=[])])
        extra,state=board.read(self.db,UID);board.set_attribute(board.player(state['active']['map']),3,1);board.save(self.db,UID,extra,state)
        packet,response=self.use(10011,[3,7]);self.assertEqual(packet['result'],0)
        self.assertEqual([b['id'] for b in self.hero()['buff']],[30001]);self.assertEqual(board.attribute(self.hero(),3),0)
        event=self.events(response)[0];self.assertEqual((event['trigger_type'],event['trigger_type_param'],event['event_pool_id']),(15,[10010],17))

    def test_repair_can_remove_positive_clash_buff_not_just_debuff(self):
        self.fresh([10011]);self.edit(buff=[dict(id=10003,duration=0,buff_effect=[])])
        packet,response=self.use(10011,[3,7]);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85017]);self.assertEqual(self.hero()['buff'],[])

    def test_repair_object_checks_native_destroyable_and_removes_overlay(self):
        self.fresh([10011],4080201);extra,state=board.read(self.db,UID);engine=move.Engine(4080201,state['active']['map']);engine.change('object',7,4,2);board.save(self.db,UID,extra,state)
        packet,response=self.use(10011,[7,4]);self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[85017])
        self.assertEqual(move.grid(4080201,self.record()['active']['map'],7,4)['thingTypeID'],0)
        self.assertEqual(self.record()['active']['map']['object'][0]['tag'],-1)

    def test_repair_rejects_no_clash_effect_and_non_destroyable_object(self):
        self.fresh([10011]);self.edit(buff=[dict(id=30001,duration=0,buff_effect=[])])
        before=self.state();self.assertNotEqual(self.use(10011,[3,7])[0]['result'],0);self.assertEqual(self.state(),before)
        self.fresh([10011],4080301);extra,state=board.read(self.db,UID);engine=move.Engine(4080301,state['active']['map']);engine.change('object',5,6,11);board.save(self.db,UID,extra,state)
        before=self.state();self.assertNotEqual(self.use(10011,[5,6])[0]['result'],0);self.assertEqual(self.state(),before)

    def test_every_prop_rejects_use_after_roll_or_another_prop(self):
        self.fresh([10005,10006]);self.spawn();self.roll();before=self.state()
        self.assertNotEqual(self.use(10005,[6,6])[0]['result'],0);self.assertEqual(self.state(),before)
        self.fresh([10005,10006]);self.spawn();self.use(10005,[6,6]);before=self.state()
        self.assertNotEqual(self.use(10006,[6,6])[0]['result'],0);self.assertEqual(self.state(),before)

    def test_selected_dice_record_corruption_rejected_before_resume(self):
        self.fresh([10002]);self.use(10002,[4]);valid=self.db.get('users',UID)['extra']
        for mutate in (lambda a:a.pop('prop_dice'),lambda a:a.update(prop_dice=7),lambda a:board.player(a['map']).update(use_item=0)):
            extra=json.loads(valid);mutate(extra[board.STATE_KEY]['active']);self.db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra),UID))
            before=self.state();self.assertNotEqual(self.enter()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_new_prop_effect_ack_failures_restore_all_state_and_retry(self):
        cases=[(10002,[5],4080101),(10005,[6,6],4080101),(10006,[6,6],4080101),(10008,[7,4],4080201),
               (10009,[7,4],4080201),(10010,[6,6],4080101),(10011,[3,7],4080101)]
        for pid,params,chapter in cases:
            self.fresh([pid],chapter)
            if pid in (10005,10006,10010):self.spawn(fixed=0)
            if pid==10011:self.edit(buff=[dict(id=10003,duration=0,buff_effect=[])])
            before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85017' and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.use(pid,params)[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertEqual(self.use(pid,params)[0]['result'],0)

    def test_target_buff_event_encoding_failure_restores_target_and_owned_prop(self):
        self.fresh([10010]);self.spawn(fixed=0);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85031' else encode(n,d)):
            self.assertNotEqual(self.use(10010,[6,6])[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_turn_cleanup_preserves_persistent_prop_effects_and_consumes_no_second_item(self):
        self.fresh([10008],4080201);self.use(10008,[7,4]);self.assertEqual(self.force_end()[0]['result'],0)
        self.assertEqual(self.hero()['use_item'],0);self.assertEqual(self.hero()['item'],[])
        self.assertEqual(self.record()['active']['map']['object'][0]['tag'],2)


if __name__=='__main__':
    suite=unittest.TestSuite(PropTests(n) for n in PropTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
