"""Memory-only NPC route, encounter, playback-contract and atomicity probes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from verify_summer_board_chance import ChanceTests
from verify_report_small_fixes import UID
import summer_board_service as board
import summer_board_movement as move
import summer_board_npc as npc
import summer_board_chance as chance


class NPCTests(ChanceTests):
    def spawn(self,kind=1011,pos=(6,6),fixed=1,buffs=None):
        extra,state=board.read(self.db,UID);engine=move.Engine(state['active']['chapter_id'],state['active']['map'])
        ref=engine.add_npc(kind,*pos,2);h=engine.entity(ref);board.set_attribute(h,3,fixed)
        if buffs is not None:h['buff']=buffs
        board.save(self.db,UID,extra,state);return ref
    def unit(self,ref):return next(h for h in self.record()['active']['map']['hero'] if h['id']==ref)
    def ready(self,pos=(3,7),gold=100):
        self.seed(pos=pos,budget=0,phase=3,buffs=[]);extra,state=board.read(self.db,UID)
        board.set_attribute(board.player(state['active']['map']),4,gold);board.save(self.db,UID,extra,state)
    def events(self,response):return [p['event'] for p in self.packets(response,85031)]

    def test_native_path_one_step_final_snapshot_and_repeat_guard(self):
        self.ready();ref=self.spawn();packet,response=self.end();self.assertEqual(packet['result'],0)
        self.assertEqual(packet['npc_path_list'],[dict(npc_id=ref,path=[dict(x=5,z=6)],move_num=1)])
        self.assertEqual(self.unit(ref)['pos'],dict(x=5,z=6));self.assertEqual(board.attribute(self.unit(ref),5),0)
        self.assertEqual(response.scs,[85015]);before=self.state();self.assertNotEqual(self.end()[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.enter();self.assertEqual(response.scs,[85001]);self.assertNotIn('next_npc_id',packet['map'])
        self.assertEqual(next(h for h in packet['map']['hero'] if h['id']==ref)['pos'],dict(x=5,z=6))

    def test_npc_cross_player_small_and_big_deduct_correct_local_gold(self):
        for kind,cost in [(1011,1),(1012,2)]:
            self.ready(pos=(5,6));ref=self.spawn(kind,fixed=2);packet,response=self.end();self.assertEqual(packet['result'],0)
            self.assertEqual(board.attribute(self.hero(),4),100-cost);self.assertEqual(self.hero()['pos'],dict(x=5,z=6))
            event=next(e for e in self.events(response) if e['trigger_type']==7)
            self.assertEqual((event['trigger_type_param'],event['trigger_player_a'],event['trigger_player_b']),([ref],ref,0))
            self.assertEqual(packet['npc_path_list'][0]['path'],[dict(x=5,z=6),dict(x=5,z=7)])
            self.assertNotIn(17023,response.scs)

    def test_npc_stay_player_teleport_and_cost_match_native_event(self):
        for kind,cost in [(1011,5),(1012,10)]:
            self.ready(pos=(5,6));ref=self.spawn(kind);packet,response=self.end();self.assertEqual(packet['result'],0)
            self.assertEqual(self.hero()['pos'],dict(x=3,z=7));self.assertEqual(board.attribute(self.hero(),4),100-cost)
            event=next(e for e in self.events(response) if e['trigger_type']==9)
            self.assertEqual((event['trigger_type_param'],event['trigger_player_a'],event['trigger_player_b']),([ref],ref,0))

    def test_hero_stay_npc_reward_and_tile_event_order(self):
        self.seed(pos=(6,6),budget=1,phase=2,buffs=[]);ref=self.spawn(pos=(5,6))
        packet,response=self.step(5,6);self.assertEqual(packet['result'],0)
        self.assertEqual(board.attribute(self.hero(),4),15);self.assertEqual(len(self.record()['active']['map']['hero']),1)
        events=self.events(response);self.assertEqual([e['trigger_type'] for e in events],[10,5])
        self.assertEqual(events[0]['trigger_type_param'],[ref]);self.assertEqual(events[0]['trigger_player_b'],ref)

    def test_hero_cross_npc_does_not_award_stay_reward_or_remove_unit(self):
        self.seed(pos=(6,6),budget=2,phase=2,buffs=[]);ref=self.spawn(pos=(5,6));packet,response=self.step(5,6)
        self.assertEqual(packet['result'],0);self.assertEqual(board.attribute(self.hero(),4),0)
        self.assertEqual(self.unit(ref)['pos'],dict(x=5,z=6));self.assertEqual(response.scs,[85011])

    def test_protection_buff_intercepts_npc_before_penalty_and_emits_effect_binding(self):
        self.ready(pos=(5,6));self.edit(buff=[dict(id=10007,duration=0,buff_effect=[])])
        ref=self.spawn(fixed=2);packet,response=self.end();self.assertEqual(packet['result'],0)
        self.assertEqual(len(self.record()['active']['map']['hero']),1);self.assertEqual(board.attribute(self.hero(),4),105)
        self.assertEqual(self.hero()['buff'],[]);events=self.events(response)
        self.assertEqual([(e['trigger_type'],e['trigger_type_param'],e['trigger_player_a'],e['trigger_player_b']) for e in events],[(22,[4],ref,0)])
        self.assertEqual(len(packet['npc_path_list'][0]['path']),1);self.assertEqual(packet['npc_path_list'][0]['move_num'],2)

    def test_hero_protection_pass_consumes_once_and_skips_npc_normal_event(self):
        self.seed(pos=(6,6),budget=2,phase=2,buffs=[dict(id=10007,duration=0,buff_effect=[])])
        ref=self.spawn(pos=(5,6));packet,response=self.step(5,6);self.assertEqual(packet['result'],0)
        self.assertEqual(len(self.record()['active']['map']['hero']),1);self.assertEqual(board.attribute(self.hero(),4),5)
        event=self.events(response)[0];self.assertEqual((event['trigger_type'],event['trigger_type_param'],event['trigger_player_b']),(24,[7],ref))

    def test_small_ducks_merge_new_monotonic_id_and_dead_pending_actor_is_excluded(self):
        self.ready();low=self.spawn(pos=(5,6));high=self.spawn();packet,response=self.end();self.assertEqual(packet['result'],0)
        self.assertEqual([r['npc_id'] for r in packet['npc_path_list']],[high]);units=self.record()['active']['map']['hero'][1:]
        self.assertEqual(len(units),1);self.assertGreater(units[0]['id'],high);self.assertEqual(units[0]['type'],1012)
        event=next(e for e in self.events(response) if e['trigger_type']==8)
        self.assertEqual((event['trigger_type_param'],event['trigger_player_a'],event['trigger_player_b']),([high],high,low))
        self.assertEqual(event['event_list'][0]['params'],[units[0]['id'],2])

    def test_big_duck_survivor_keeps_id_and_can_still_take_its_turn(self):
        self.ready();low=self.spawn(1012,pos=(5,6));high=self.spawn();packet,response=self.end();self.assertEqual(packet['result'],0)
        self.assertEqual([r['npc_id'] for r in packet['npc_path_list']],[high,low]);self.assertEqual(self.unit(low)['pos'],dict(x=5,z=7))
        self.assertEqual(len(self.record()['active']['map']['hero']),2)

    def test_stopped_npc_skipped_three_turns_then_resumes_no_zero_route(self):
        self.ready();ref=self.spawn();extra,state=board.read(self.db,UID);engine=move.Engine(4080101,state['active']['map']);npc.stop(engine,3);board.save(self.db,UID,extra,state)
        for remaining in (2,1,0):
            packet,response=self.end();self.assertEqual(packet['result'],0);self.assertNotIn('npc_path_list',packet)
            self.assertEqual(board.attribute(self.unit(ref),12),remaining);self.assertEqual(self.unit(ref)['pos'],dict(x=6,z=6))
            extra,state=board.read(self.db,UID);board.set_attribute(board.player(state['active']['map']),8,3);board.save(self.db,UID,extra,state)
        packet,response=self.end();self.assertEqual(packet['npc_path_list'][0]['move_num'],1);self.assertEqual(self.unit(ref)['pos'],dict(x=5,z=6))

    def test_stop_prop_and_random_chance_both_persist_native_stop_attribute(self):
        self.seed(budget=0,phase=0,buffs=[]);ref=self.spawn();self.edit(item=[10012]);self.assertEqual(self.op(85016,prop_id=10012)[0]['result'],0)
        self.assertEqual(board.attribute(self.unit(ref),12),3)
        self.ready();ref=self.spawn();extra,state=board.read(self.db,UID);engine=move.Engine(4080101,state['active']['map'])
        with patch.object(chance.random,'randrange',return_value=10):chance.open_chance(engine,201030)
        board.save(self.db,UID,extra,state);self.assertEqual(self.finish(201030)[0]['result'],0)
        self.assertEqual(board.attribute(self.unit(ref),12),3);self.assertEqual(self.hero()['chance'],chance.EMPTY)

    def test_fully_enclosed_npc_is_not_sent_as_zero_step_or_empty_ready_route(self):
        self.ready();ref=self.spawn();extra,state=board.read(self.db,UID)
        state['active']['map']['ban_dir']=[dict(pos=dict(x=6,z=6),ban_dir=[dict(direction=i,is_hide=0) for i in range(6)])]
        board.save(self.db,UID,extra,state);packet,response=self.end();self.assertEqual(packet['result'],0)
        self.assertNotIn('npc_path_list',packet);self.assertEqual(self.unit(ref)['pos'],dict(x=6,z=6))

    def test_npc_automatic_tile_does_not_spend_budget(self):
        self.ready();ref=self.spawn(pos=(3,7))
        extra,state=board.read(self.db,UID);next(h for h in state['active']['map']['hero'] if h['id']==ref)['direction']=5;board.save(self.db,UID,extra,state)
        with patch.object(npc.random,'choice',side_effect=lambda choices:next(p for p in choices if p[0]>3)):
            packet,response=self.end()
        self.assertEqual(packet['result'],0);route=packet['npc_path_list'][0]
        self.assertEqual(route['path'],[dict(x=4,z=7),dict(x=5,z=7)]);self.assertEqual(route['move_num'],1)

    def test_object_fix_walkable_blocks_paths_and_change_evicts_occupants(self):
        self.ready();ref=self.spawn();extra,state=board.read(self.db,UID);engine=move.Engine(4080101,state['active']['map'])
        native=move.MAPS[4080101]['extendThingInfo']
        # This tutorial has no blocking object extension; supply an isolated map
        # variant to test the native fix_walkable=1 callback without editing data.
        with patch.dict(move.MAPS[4080101],dict(extendThingInfo=native+[dict(thingTypeID=1004)])):
            tag=len(native)+1
            engine.change('object',5,6,tag);self.assertEqual(move.neighbors(4080101,state['active']['map'],engine.entity(ref)),[])
            engine.change('object',6,6,tag);self.assertFalse(engine.alive(ref))
            engine.entity(0)['pos']=dict(x=5,z=6);engine.change('object',5,6,tag)
            self.assertEqual(engine.entity(0)['pos'],dict(x=3,z=7))

    def test_npc_creation_does_not_reuse_deleted_high_id(self):
        self.ready();old=self.spawn();extra,state=board.read(self.db,UID);engine=move.Engine(4080101,state['active']['map'])
        engine.delete_npc(old);new=engine.add_npc(1011,6,6,2);self.assertGreater(new,old)
        board.save(self.db,UID,extra,state);self.enter();self.assertEqual(self.unit(new)['id'],new)

    def test_npc_path_ack_and_encounter_event_encoding_failure_roll_back_everything(self):
        self.ready(pos=(5,6));ref=self.spawn();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for sc in (85015,85031):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.end()[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.assertEqual(self.end()[0]['result'],0);self.assertEqual(board.attribute(self.hero(),4),95)

    def test_stop_counter_rolls_back_on_ack_failure(self):
        self.ready();ref=self.spawn();extra,state=board.read(self.db,UID);board.set_attribute(next(h for h in state['active']['map']['hero'] if h['id']==ref),12,3);board.save(self.db,UID,extra,state)
        before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_85015' and d.get('result',0)==0 else encode(n,d)):
            self.assertNotEqual(self.end()[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_route_auto_loop_has_bounded_failure_and_rolls_back(self):
        self.ready();self.spawn(pos=(3,7));before=self.state()
        original=move._hex
        with patch.object(move,'_hex',side_effect=lambda tile:dict(original(tile),updatable=1)),patch.object(npc,'_hex',side_effect=lambda tile:dict(original(tile),updatable=1)):
            self.assertNotEqual(self.end()[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_native_npc_start_end_callbacks_include_actor_parameter(self):
        self.ready();ref=self.spawn()
        with patch.dict(board.CFG['npcs']['1011'],dict(npc_start_event=11,npc_stop_event=17)):
            packet,response=self.end()
        self.assertEqual(packet['result'],0);events=self.events(response)
        self.assertEqual([(e['trigger_type'],e['trigger_type_param']) for e in events],[(11,[ref]),(12,[ref])])

    def test_unknown_npc_type_or_invalid_allocator_rejected_before_resume(self):
        self.ready();self.spawn();valid=self.db.get('users',UID)['extra']
        for mutate in [lambda m:m['hero'][1].update(type=99999),lambda m:m.update(next_npc_id=1)]:
            extra=json.loads(valid);mutate(extra[board.STATE_KEY]['active']['map']);self.db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra),UID))
            before=self.state();self.assertNotEqual(self.enter()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_native_minimum_dice_count_after_crossing_reduction_buff(self):
        self.ready(pos=(5,6));self.edit(buff=[dict(id=40003,duration=0,buff_effect=[])])
        ref=self.spawn(fixed=2);self.assertEqual(self.end()[0]['result'],0)
        unit=self.unit(ref);self.assertEqual(next(a['value'] for a in unit['attribute'] if a['id']==2),0)
        self.assertEqual(board.attribute(unit,2),1);self.assertEqual(board.attribute(unit,3),0)
        extra,state=board.read(self.db,UID);board.set_attribute(board.player(state['active']['map']),8,3);board.save(self.db,UID,extra,state)
        with patch.object(npc.random,'randint',return_value=1):packet,response=self.end()
        self.assertEqual(packet['result'],0);self.assertEqual(packet['npc_path_list'][0]['move_num'],1)


if __name__=='__main__':
    suite=unittest.TestSuite(NPCTests(n) for n in NPCTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
