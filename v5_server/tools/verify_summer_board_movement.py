"""Native map / Core movement probes, entirely on memory saves."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import verify_summer_board_bootstrap as fixture
from verify_report_small_fixes import UID
import codec
import generator
import summer_board_service as board
import summer_board_movement as move


class MovementTests(fixture.BoardTests):
    def step(self,x,z):return self.call(85010,dict(next_pos=dict(x=x,z=z)),85011)
    def end(self):return self.call(85014,{},85015)

    def seed(self,chapter=4080101,pos=(3,7),budget=2,phase=1,buffs=None):
        extra,state=board.read(self.db,UID)
        state['completed']=list(board.LEVELS);state['active']=None;board.save(self.db,UID,extra,state)
        self.enter(chapter);extra,state=board.read(self.db,UID);hero=board.player(state['active']['map'])
        hero['pos']=dict(x=pos[0],z=pos[1]);board.set_attribute(hero,5,budget);board.set_attribute(hero,8,phase)
        if buffs is not None:hero['buff']=buffs
        board.save(self.db,UID,extra,state)

    def test_actual_tutorial_automatic_tiles_do_not_consume_budget(self):
        self.enter();self.roll();packet,response=self.step(4,7);self.assertEqual(packet['result'],0)
        hero=board.player(self.record()['active']['map']);self.assertEqual(hero['pos'],dict(x=4,z=7));self.assertEqual(board.attribute(hero,5),2)
        self.assertEqual(response.scs,[85011]);self.assertEqual(hero['direction'],5)
        self.assertEqual(self.step(5,7)[0]['result'],0);self.assertEqual(board.attribute(board.player(self.record()['active']['map']),5),1)

    def test_real_end_tile_event_before_ack_and_local_currency_only(self):
        self.seed(pos=(6,6),budget=1,phase=2);before=self.state()
        packet,response=self.step(5,6);self.assertEqual(packet['result'],0);events=self.packets(response,85031)
        self.assertEqual(len(events),1);event=events[0]['event']
        self.assertEqual((event['trigger_type'],event['trigger_type_param'],event['event_pool_id']),(5,[5,6],101010))
        hero=board.player(self.record()['active']['map']);self.assertEqual(board.attribute(hero,4),5);self.assertEqual(board.attribute(hero,5),0);self.assertEqual(board.attribute(hero,8),3)
        self.assertEqual(response.scs,[85031,85011]);after=self.state()
        for t in before.keys()-{'users'}:self.assertEqual(before[t],after[t],t)
        self.assertNotIn(17023,response.scs);self.assertNotIn(85001,response.scs)

    def test_first_two_turns_guide_fixed_dice_and_nested_buff_transition(self):
        self.seed(pos=(6,6),budget=1,phase=2);self.step(5,6)
        packet,response=self.end();self.assertEqual(packet['result'],0);saved=self.record()['active']['map'];hero=board.player(saved)
        self.assertEqual(saved['turn'],1);self.assertEqual(board.attribute(hero,8),0);self.assertEqual(board.attribute(hero,3),3)
        self.assertEqual([b['id'] for b in hero['buff']],[30002]);self.assertEqual(hero['buff'][0]['duration'],0)
        events=self.packets(response,85031)
        self.assertEqual([(e['event']['trigger_type'],e['event']['event_pool_id']) for e in events],[(15,1152),(16,13)])
        self.assertEqual(response.scs[-1],85015);self.assertEqual(self.roll()[0]['dice_list'],[3])

    def test_repeat_turn_and_repeat_destination_do_not_double_update(self):
        self.seed(pos=(6,6),budget=1,phase=2,buffs=[]);self.step(5,6);before=self.state()
        self.assertNotEqual(self.step(5,6)[0]['result'],0);self.assertEqual(self.state(),before)
        self.end();before=self.state();self.assertNotEqual(self.end()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_wall_jump_bounds_and_outgoing_ban_direction_fail_without_write(self):
        self.enter();self.roll();before=self.state()
        for target in [(3,8),(5,7),(-1,7),(999,999),(3,7)]:
            self.assertNotEqual(self.step(*target)[0]['result'],0,target);self.assertEqual(self.state(),before)
        extra,state=board.read(self.db,UID);state['active']['map']['ban_dir']=[dict(pos=dict(x=3,z=7),ban_dir=[dict(direction=5,is_hide=0)])];board.save(self.db,UID,extra,state)
        before=self.state();self.assertNotEqual(self.step(4,7)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_move_requires_roll_budget_and_no_pending_choice(self):
        self.enter();before=self.state();self.assertNotEqual(self.step(4,7)[0]['result'],0);self.assertEqual(self.state(),before)
        for key,value in [('chance',dict(chance_id=101111,is_unlock=0,item_list=[],choose_list=[])),('tmp_item',[10001])]:
            self.seed();extra,state=board.read(self.db,UID);board.player(state['active']['map'])[key]=value;board.save(self.db,UID,extra,state)
            before=self.state();self.assertNotEqual(self.step(4,7)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_move_event_or_ack_encoding_failure_rolls_back_and_retry(self):
        self.seed(pos=(6,6),budget=1,phase=2,buffs=[]);before=self.state();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        for sc in [85031,85011]:
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result',0)==0 else enc(n,d)):
                self.assertNotEqual(self.step(5,6)[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.assertEqual(self.step(5,6)[0]['result'],0);self.assertEqual(board.attribute(board.player(self.record()['active']['map']),4),5)

    def test_end_turn_event_and_ack_failure_roll_back_duration_and_budget(self):
        self.seed(pos=(5,6),budget=0,phase=3);before=self.state();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        for sc in [85031,85015]:
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result',0)==0 else enc(n,d)):
                self.assertNotEqual(self.end()[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.assertEqual(self.end()[0]['result'],0)

    def test_all_maps_have_native_spawn_and_walkable_neighbors(self):
        for chapter,cfg in board.LEVELS.items():
            self.seed(chapter,pos=tuple(cfg['init_pos']),buffs=[]);state=self.record()['active']['map'];hero=board.player(state)
            self.assertEqual(move._hex(move.grid(chapter,state,*cfg['init_pos']))['walkable'],1)
            self.assertTrue(move.neighbors(chapter,state,hero),chapter)
            native=move.MAPS[chapter];self.assertEqual(len(native['tiles']),native['width']*native['height'])

    def test_floor_and_object_overlays_use_native_extension_tags(self):
        self.seed(buffs=[]);extra,state=board.read(self.db,UID);active=state['active'];engine=move.Engine(active['chapter_id'],active['map'])
        engine.pool(101032,5,[6,8],tile=move.grid(4080101,active['map'],6,8))
        self.assertEqual(active['map']['floor'][0]['tag'],4)
        self.assertEqual(move.grid(4080101,active['map'],6,8)['typeID'],move.MAPS[4080101]['extendInfo'][3]['typeID'])
        engine.change('object',6,8,-1);self.assertEqual(move.grid(4080101,active['map'],6,8)['thingTypeID'],0)
        board.save(self.db,UID,extra,state);payload=codec.decode(generator.gen_payload(85035,uid=UID,db=self.db),'sc_85035')
        self.assertEqual(payload['chapter_id'],4080101);self.assertEqual(self.enter()[0]['map']['floor'][0]['tag'],4)

    def test_blocked_floor_change_teleports_occupying_player_like_native_callback(self):
        self.seed(pos=(5,7),buffs=[]);active=self.record()['active'];engine=move.Engine(4080101,active['map'])
        tag=next(i for i,r in enumerate(move.MAPS[4080101]['extendInfo'],1) if board.CFG['hexes'][str(r['typeID'])]['walkable']==0)
        engine.change('floor',5,7,tag)
        self.assertEqual(board.player(active['map'])['pos'],dict(x=3,z=7))

    def test_buff_expiration_resets_fixed_dice_and_does_not_age_new_buff(self):
        self.seed(pos=(5,6),budget=0,phase=3,buffs=[dict(id=30003,duration=1,buff_effect=[])])
        extra,state=board.read(self.db,UID);board.set_attribute(board.player(state['active']['map']),3,5);board.save(self.db,UID,extra,state)
        packet,response=self.end();self.assertEqual(packet['result'],0);hero=board.player(self.record()['active']['map'])
        self.assertEqual(hero['buff'],[]);self.assertEqual(board.attribute(hero,3),0)
        self.assertEqual(self.packets(response,85031)[0]['event']['event_pool_id'],17)

    def test_step_buff_uses_native_effect_id_counter_and_local_gold(self):
        self.seed(pos=(6,6),budget=2,phase=2,buffs=[dict(id=10003,duration=0,buff_effect=[])])
        self.assertEqual(self.step(5,6)[0]['result'],0);hero=board.player(self.record()['active']['map'])
        self.assertEqual(board.attribute(hero,4),1);self.assertEqual(hero['buff'][0]['buff_effect'],[dict(id=1,num=1)])

    def test_native_map_turn_trigger_is_sent_with_round_parameter(self):
        self.seed(4080301,pos=tuple(board.LEVELS[4080301]['init_pos']),budget=0,phase=3,buffs=[])
        extra,state=board.read(self.db,UID);state['active']['map']['turn']=9;board.save(self.db,UID,extra,state)
        packet,response=self.end();self.assertEqual(packet['result'],0)
        event=self.packets(response,85031)[0]['event'];self.assertEqual((event['trigger_type'],event['trigger_type_param'],event['event_pool_id']),(20,[10],3101))

    def test_type2_chance_is_not_misread_as_event_rows_or_silently_finished(self):
        self.seed(pos=(5,7),budget=1,phase=2,buffs=[])
        packet,response=self.step(6,8);self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs,[85033,85011])
        self.assertEqual(self.packets(response,85033)[0]['chance']['item_list'],[1,2,10003])
        self.assertEqual(self.record()['active']['map']['task'][0]['progress'],0)

    def test_missing_native_pool_is_reported_before_mutating_client(self):
        self.seed(pos=(6,6),budget=1,phase=2,buffs=[]);before=self.state()
        with patch.dict(board.CFG['events'],{},clear=True):self.assertNotEqual(self.step(5,6)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_npc_collision_removes_duck_and_round_can_continue(self):
        self.seed(pos=(6,6),budget=1,phase=2,buffs=[]);extra,state=board.read(self.db,UID)
        npc=deepcopy(board.player(state['active']['map']));npc.update(id=1,type=1011,pos=dict(x=5,z=6));state['active']['map']['hero'].append(npc);board.save(self.db,UID,extra,state)
        self.assertEqual(self.step(5,6)[0]['result'],0)
        self.assertEqual(len(self.record()['active']['map']['hero']),1)
        self.assertEqual(self.end()[0]['result'],0)

    def test_event_recursion_is_bounded_and_whole_move_rolls_back(self):
        self.seed(pos=(6,6),budget=1,phase=2,buffs=[]);before=self.state()
        calls=[];original=move.Engine._pool
        def observe(engine,*args,**kwargs):
            calls.append(args[0]);return original(engine,*args,**kwargs)
        with patch.dict(board.CFG['events'],{'101010':dict(type=1,player_params=[[20005,1,30001]],npc_params=[]),'12':dict(type=1,player_params=[[20005,1,30001]],npc_params=[])}),patch.object(move.Engine,'_pool',new=observe):
            self.assertNotEqual(self.step(5,6)[0]['result'],0)
        self.assertEqual(len(calls),32)
        self.assertEqual(self.state(),before)

    def test_corrupt_overlay_or_off_map_unit_is_rejected_before_resume(self):
        self.enter();valid=self.db.get('users',UID)['extra']
        for mutate in [lambda m:m['hero'][0].update(pos=dict(x=999,z=999)),
                       lambda m:m.update(floor=[dict(pos=dict(x=3,z=7),tag=9999,state=0,rotation=0)]),
                       lambda m:m.update(object=[dict(pos=dict(x=3,z=7),tag=-2,rotation=0)])]:
            extra=json.loads(valid);mutate(extra[board.STATE_KEY]['active']['map']);self.db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra),UID))
            before=self.state();self.assertNotEqual(self.enter()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_extracts_all13_native_maps_and_matches_published_contract(self):
        from export_summer_board_maps import build
        source=Path(__file__).resolve().parents[2]/'archive/summer_board_movement_20261105/native_maps'
        self.assertEqual(build(source),move.DATA)


if __name__=='__main__':
    suite=unittest.TestSuite(MovementTests(n) for n in MovementTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
