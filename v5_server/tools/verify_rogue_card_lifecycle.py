"""Native RogueCard RPCs through real Core and codec; no real save mounted."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID
import account_db
import codec
import generator
import minigame_service as mini
import rogue_card_service as rogue


class RogueCardTests(MemoryTests):
    def setUp(self):
        super().setUp();self.svc=mini.MiniGameService.get_instance(self.db)
        self.svc.get_or_init_rogue_card(self.db,UID)

    def rec(self):return self.svc.get_or_init_rogue_card(self.db,UID)
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('rogue_card_game_record','server_global_config','task','currency','material')}
    def call_rc(self,cmd,data):return self.call(cmd,data,cmd+1)
    def begin(self,tid=101):
        self.assertEqual(self.call_rc(89602,dict(thread_id=tid))[0]['result'],0)
        post=rogue.CFG['posts'][str(tid)]
        if post['story_id1']:
            self.assertEqual(self.call_rc(89604,dict(thread_id=tid,story_id=post['story_id1'],story_id_index=1))[0]['result'],0)
        if not post['balatro_id']:return 0
        packet,_=self.call_rc(89618,dict(stage_id=post['balatro_id']))
        self.assertEqual(packet['result'],0);return packet['battle_id']
    def challenge(self,deck=1,diff=1):
        packet,_=self.call_rc(89618,dict(stage_id=201,deck=deck,diff=diff))
        self.assertEqual(packet['result'],0);return packet['battle_id']
    def saved(self,stage=101,gold=7,deck=0,diff=0):
        return codec.decode(codec.encode('impermanence_save_data_net_rec',dict(stage_id=stage,gold=gold,cur_round=1,
            deck_id=deck,difficulty=diff,settle_data=dict(score=123,use_item_id=[1],enhance_id=[101]))),'impermanence_save_data_net_rec')
    def save(self,bid,kind,data):return self.call_rc(89620,dict(battle_id=bid,battle_type=kind,save_data=data))
    def full(self):return codec.decode(self.svc.build_89601_payload(self.db,UID),'sc_89601')

    def test_start_state_precedes_ack_and_pure_story_posts_are_recorded(self):
        packet,response=self.call_rc(89602,dict(thread_id=101))
        self.assertEqual(packet['result'],0)
        self.assertLess(response.scs.index(89601),response.scs.index(89603))
        self.assertEqual(self.full()['story']['thread_state'],1)
        self.assertIn(101,self.full()['story']['post_thread_list'])

    def test_save_continue_and_read_preserve_native_data(self):
        bid=self.begin();data=self.saved()
        self.assertEqual(self.save(bid,1,data)[0]['result'],0)
        packet,response=self.call_rc(89622,dict(battle_id=bid,battle_type=1))
        self.assertEqual(packet['save_data'],data)
        self.assertNotIn('save_rollback',packet)
        self.assertNotIn(89601,response.scs)
        before=self.state();packet,_=self.call_rc(89628,dict(battle_id=bid,battle_type=1))
        self.assertEqual(packet['save_data'],data);self.assertEqual(self.state(),before)

    def test_story_and_challenge_snapshots_are_independent(self):
        story=self.begin();self.save(story,1,self.saved(gold=7))
        challenge=self.challenge();self.save(challenge,2,self.saved(201,99,1,1))
        self.assertNotEqual(story,challenge)
        self.assertEqual(self.call_rc(89622,dict(battle_id=story,battle_type=1))[0]['save_data']['gold'],7)
        self.assertEqual(self.call_rc(89622,dict(battle_id=challenge,battle_type=2))[0]['save_data']['gold'],99)
        self.assertEqual(self.full()['story']['battle_id'],story)
        self.assertEqual(self.full()['challenge']['battle_id'],challenge)

    def test_wrong_battle_id_type_stage_and_challenge_context_are_rejected(self):
        story=self.begin();challenge=self.challenge();before=self.state()
        for bid,kind,data in ((story+1,1,self.saved()),(story,2,self.saved()),
                              (story,1,self.saved(102)),(challenge,2,self.saved(201,7,2,1))):
            self.assertNotEqual(self.save(bid,kind,data)[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_checkpoint_is_written_by_89626_not_interrupt(self):
        bid=self.begin();data=self.saved();checkpoint=self.saved(gold=4)
        self.save(bid,1,data)
        self.assertEqual(self.call_rc(89626,dict(battle_id=bid,battle_type=1,save_rollback=checkpoint))[0]['result'],0)
        packet,_=self.call_rc(89622,dict(battle_id=bid,battle_type=1))
        self.assertEqual(packet['save_rollback'],checkpoint)
        packet,response=self.call_rc(89624,dict(battle_id=bid,battle_type=1))
        self.assertEqual(packet['settle_info']['score'],123)
        self.assertEqual(self.full()['story']['battle_id'],0)
        self.assertEqual(self.full()['story'].get('finish_thread_list',[]),[])
        before=self.state();self.assertEqual(self.call_rc(89624,dict(battle_id=bid,battle_type=1))[0],packet)
        self.assertEqual(self.state(),before)

    def test_challenge_interrupt_never_increases_max_difficulty(self):
        bid=self.challenge();self.save(bid,2,self.saved(201,10,1,1))
        packet,_=self.call_rc(89624,dict(battle_id=bid,battle_type=2))
        self.assertEqual(packet['result'],0);self.assertEqual(packet['settle_info']['score'],123)
        self.assertEqual(self.full()['challenge'].get('status_list',[]),[])
        self.assertNotEqual(self.call_rc(89622,dict(battle_id=bid,battle_type=2))[0]['result'],0)

    def test_completed_post_updates_once_and_post_story_is_confirmed(self):
        self.begin()
        from event_bus import bus,Events
        with patch.object(bus,'emit',wraps=bus.emit) as emit:
            packet,response=self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))
            self.assertEqual(packet['result'],0)
            self.assertLess(response.scs.index(89601),response.scs.index(89613))
            self.assertEqual(self.full()['story']['thread_state'],4)
            self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))
            self.assertEqual(sum(c.args[0]==Events.ROGUE_CARD_POST_FINISH for c in emit.call_args_list),1)
        self.assertEqual(self.call_rc(89604,dict(thread_id=101,story_id=939012001,story_id_index=2))[0]['result'],0)
        self.assertEqual(self.full()['story']['thread_state'],0)
        self.assertEqual(self.full()['story'].get('finish_thread_list',[]),[101])
        self.assertEqual(self.call_rc(89602,dict(thread_id=102))[0]['result'],0)

    def test_plot_only_107_finishes_without_a_fake_battle(self):
        rec=self.rec();rec['finish_thread_list']=list(range(101,107));self.svc.save_rogue_card(self.db,UID,rec)
        self.assertEqual(self.begin(107),0)
        self.assertIn(107,self.full()['story'].get('finish_thread_list',[]))
        self.assertEqual(self.full()['story']['battle_id'],0)

    def test_unlock_rules_unknown_post_and_false_story_do_not_mutate(self):
        before=self.state()
        for cmd,args in ((89602,dict(thread_id=102)),(89602,dict(thread_id=99999)),
                         (89618,dict(stage_id=201,deck=2,diff=1)),(89618,dict(stage_id=201,deck=1,diff=6))):
            self.assertNotEqual(self.call_rc(cmd,args)[0]['result'],0);self.assertEqual(self.state(),before)
        self.begin();before=self.state()
        self.assertNotEqual(self.call_rc(89604,dict(thread_id=101,story_id=99999,story_id_index=1))[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_restart_expires_old_snapshot_and_keeps_other_mode(self):
        old=self.begin();self.save(old,1,self.saved())
        challenge=self.challenge();self.save(challenge,2,self.saved(201,8,1,1))
        self.assertEqual(self.call_rc(89608,dict(thread_id=101))[0]['result'],0)
        packet,_=self.call_rc(89618,dict(stage_id=101));self.assertNotEqual(packet['battle_id'],old)
        self.assertNotEqual(self.call_rc(89622,dict(battle_id=old,battle_type=1))[0]['result'],0)
        self.assertEqual(self.call_rc(89622,dict(battle_id=challenge,battle_type=2))[0]['save_data']['gold'],8)

    def test_challenge_win_unlocks_only_the_next_difficulty_and_is_idempotent(self):
        self.challenge();self.assertEqual(self.call_rc(89616,dict(deck=1,diff=1,info={}))[0]['result'],0)
        self.assertEqual(self.full()['challenge'].get('status_list',[]),[dict(deck=1,max_diff=1)])
        before=self.state();self.call_rc(89616,dict(deck=1,diff=1,info={}))
        self.assertEqual(self.state(),before)
        self.challenge(diff=2)
        before=self.state();self.assertNotEqual(self.call_rc(89616,dict(deck=1,diff=6,info={}))[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_like_callback_toggles_without_a_conflicting_full_state(self):
        for expected in ([101],[]):
            packet,response=self.call_rc(89606,dict(thread_id=101))
            self.assertEqual(packet['result'],0);self.assertEqual(self.full()['story'].get('like_thread_list',[]),expected)
            self.assertNotIn(89601,response.scs)

    def test_write_and_required_response_failures_roll_back(self):
        self.begin();before=self.state();execute=self.db.execute
        def write_then_fail(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if 'INSERT INTO server_global_config' in sql:raise RuntimeError('isolated runtime-state write')
            return result
        with patch.object(self.db,'execute',side_effect=write_then_fail):
            self.assertNotEqual(self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))[0]['result'],0)
        self.assertEqual(self.state(),before)
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda name,data:None if name=='sc_89613' else encode(name,data)):
            self.assertNotEqual(self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))[0]['result'],0)

    def test_invalid_collection_ids_do_not_reach_client_state(self):
        bid=self.begin();before=self.state()
        self.assertNotEqual(self.call_rc(89612,dict(thread_id=101,joker_result=1,info=dict(use_item_id=[999999])))[0]['result'],0)
        self.assertEqual(self.state(),before)
        bad=self.saved();bad['settle_data']['enhance_id']=[99999]
        self.assertNotEqual(self.save(bid,1,bad)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_same_uid_two_databases_remain_bound_and_legacy_snapshot_is_adopted(self):
        second=account_db.AccountDB.new_save_workspace();self.addCleanup(second.close)
        other=mini.MiniGameService.get_instance(second)
        self.assertIsNot(other,self.svc);self.assertIs(self.svc.db,self.db)
        rec=self.rec();rec.update(thread_id=101,thread_state=3,battle_id=123,save_data=self.saved())
        self.svc.save_rogue_card(self.db,UID,rec)
        packet,_=self.call_rc(89622,dict(battle_id=123,battle_type=1))
        self.assertEqual(packet['save_data']['gold'],7)
        self.assertFalse(other.get_or_init_rogue_card(second,UID)['finish_thread_list'])

    def test_post_task_receives_atomic_diff_and_failed_state_packet_rolls_it_back(self):
        self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter) VALUES(50005130,3002,411008,1,4100406,?)',('[101]',))
        self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,50005130,3002)',(UID,))
        self.begin();before=self.state()
        with patch.object(mini.MiniGameService,'build_89601_payload',return_value=b''):
            packet,response=self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))
            self.assertNotEqual(packet['result'],0);self.assertNotIn(28007,response.scs)
        self.assertEqual(self.state(),before)
        packet,response=self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))
        self.assertEqual(packet['result'],0);self.assertIn(28007,response.scs)
        task=self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=50005130',(UID,))[0]
        self.assertEqual(task['progress'],1)
        _,response=self.call_rc(89612,dict(thread_id=101,joker_result=1,info={}))
        self.assertNotIn(28007,response.scs);self.assertNotIn(89601,response.scs)

    def test_other_minigame_initial_packets_still_encode_with_bound_service(self):
        for cmd in (89401,84331,68181,60105,60107,68171,24051,89421,83016,83020,83021,83026,89701,61047,83124,89011,89601):
            with self.subTest(cmd=cmd):
                data=generator.gen_payload(cmd,uid=UID,db=self.db)
                # A message containing only empty repeated fields is valid b''.
                self.assertIsInstance(data,bytes,cmd)
                self.assertIsInstance(codec.decode(data,'sc_'+str(cmd)),dict)


if __name__=='__main__':unittest.main(verbosity=2)
