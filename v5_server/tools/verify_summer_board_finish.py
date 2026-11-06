"""Nested Core battle bridge and task/history reward chain, memory-only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from verify_summer_board_games import GameTests
from verify_report_small_fixes import UID
import summer_board_service as board
import summer_board_movement as move
import summer_board_nested as nested
import summer_board_progress as progress
import middleware as mw
import codec
import generator
import hero_codec
import minigame_point_service as points


class FinishTests(GameTests):
    def setUp(self):
        super().setUp();self.real_clears=[];owner=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);owner.start();self.addCleanup(owner.stop)
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1049,100),keys=('uid','id'))
        for tid,cfg in board.CFG['external_tasks'].items():
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                (int(tid),cfg['type'],cfg['condition'],cfg['need'],cfg['activity_id'],json.dumps(cfg['additional_parameter']),json.dumps(cfg['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,int(tid),cfg['type']))
        self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(54065,1)')
        self.conn.resource_version='229'
    def task_value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']
    def local_task(self,tid=10101,amount=1):
        extra,state=board.read(self.db,UID);engine=move.Engine(state['active']['chapter_id'],state['active']['map'])
        if not any(t['id']==tid for t in engine.state['task']):engine.pool(1100,1)
        for t in engine.state['task']:
            if t['id']==tid:t['progress']=amount
        board.save(self.db,UID,extra,state)
    def combat(self,cid=201112,chapter=4080201):
        self.game(cid,chapter);self.op(85020,chance_id=cid)
        spec=board.CFG['nested'][str(cid)]
        packet,response=self.call(54030,dict(common_info=dict(type=spec['type'],dest=spec['dest'],activity_id=0,
            hero_list=[dict(hero_id=1049,hero_type=1)])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response
    def battle_result(self,win=True):
        session=mw.get_battle_session(self.bid);session['result_data']=dict(result=1 if win else 3,info=dict(battle_time=12000))
        return self.call(54032,dict(battle_id=self.bid),54033)
    def mark_terminal(self,chapter=4080101):
        self.fresh([],chapter);extra,state=board.read(self.db,UID);m=state['active']['map']
        state['completed']=list(self.real_clears);self.real_clears.append(chapter)
        m['task']=[dict(id=i,progress=board.CFG['tasks'][str(i)]['need']) for i in board.CFG['map_tasks'][str(chapter)]]
        board.set_attribute(board.player(m),7,1);board.save(self.db,UID,extra,state)

    def test_all_seventeen_chances_open_and_tangram_template_push_precedes_chance(self):
        self.assertEqual(len(board.CFG['nested']),17)
        for cid,spec in board.CFG['nested'].items():
            frames=self.game(int(cid));self.assertTrue(self.record()['active']['map']['nested_token'])
            self.assertEqual(frames[-1][0],85033)
            if board.CFG['chances'][cid]['type']==12:
                self.assertEqual(frames[0][0],86008);payload=codec.decode(codec.encode('sc_86008',frames[0][1]),'sc_86008')
                self.assertEqual([r['stage_id'] for r in payload['tangram_info_list']],[3001,3002])

    def test_bubble_and_tangram_local_success_run_native_finish_pool(self):
        for cid,chapter,task in [(201111,4080201,20101),(401112,4080401,40102)]:
            self.game(cid,chapter);self.op(85020,chance_id=cid)
            extra,state=board.read(self.db,UID);m=state['active']['map']
            if not any(t['id']==task for t in m['task']):m['task'].append(dict(id=task,progress=0))
            board.save(self.db,UID,extra,state)
            packet,response=self.finish(cid);self.assertEqual(packet['result'],0)
            self.assertNotIn('nested_token',self.record()['active']['map'])
            self.assertEqual(self.events(response)[0]['trigger_type'],13)

    def test_core_battle_identity_and_settlement_gives_no_world_rewards(self):
        packet,response=self.combat();self.assertEqual(packet['result'],0)
        session=mw.get_battle_session(self.bid);self.assertEqual(session['summer_board_context']['chance_id'],201112)
        before=self.state();packet,response=self.battle_result();self.assertEqual(packet['result'],0)
        self.assertEqual(packet['battle_result'].get('all_drop_list',[]),[])
        for table in ('currency','material','chapter','stage_sub'):self.assertEqual(self.state()[table],before[table])
        self.assertTrue(self.record()['active']['map']['nested_result']['win'])
        self.assertNotIn(24009,response.scs);self.assertNotIn(17023,response.scs)

    def test_battle_chance_cannot_finish_before_current_success_receipt(self):
        self.game(201112,4080201);self.op(85020,chance_id=201112);before=self.state()
        self.assertNotEqual(self.finish(201112)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.combat()[0]['result'],0);self.battle_result(False);before=self.state()
        self.assertNotEqual(self.finish(201112)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_old_battle_after_new_run_or_reentry_cannot_set_current_receipt(self):
        self.combat();old_bid=self.bid;self.exit();self.combat();before=self.state();self.bid=old_bid
        self.assertNotEqual(self.battle_result()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_retry_latest_battle_supersedes_previous_attempt_same_chance(self):
        self.combat();old_bid=self.bid;spec=board.CFG['nested']['201112']
        self.call(54030,dict(common_info=dict(type=spec['type'],dest=spec['dest'],hero_list=[dict(hero_id=1049,hero_type=1)])),54031)
        new_bid=self.conn.battle_id[0];self.assertNotEqual(old_bid,new_bid)
        before=self.state();self.bid=old_bid;self.assertNotEqual(self.battle_result()[0]['result'],0);self.assertEqual(self.state(),before)
        self.bid=new_bid;self.assertEqual(self.battle_result()[0]['result'],0)

    def test_every_nested_battle_type_uses_exact_native_destination(self):
        for cid,spec in board.CFG['nested'].items():
            if not spec['type']:continue
            self.assertEqual(self.combat(int(cid))[0]['result'],0,(cid,spec))
            self.assertEqual(mw.get_battle_session(self.bid)['dest'],spec['dest'])

    def test_wrong_type_and_locked_dedicated_stage_are_rejected(self):
        self.fresh([]);before=self.state()
        for kind,dest in [(75,5190901),(70,1),(74,0),(72,5190740),(73,5190801)]:
            packet,response=self.call(54030,dict(common_info=dict(type=kind,dest=dest,hero_list=[dict(hero_id=1049,hero_type=1)])),54031)
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)

    def test_settlement_ack_failure_restores_receipt_and_allows_retry(self):
        self.combat();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_54033' and d.get('result',0)==0 else encode(n,d)):
            self.assertNotEqual(self.battle_result()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.battle_result()[0]['result'],0)

    def test_completed_local_task_submit_no_double_cost_and_external_diff(self):
        self.enter();self.local_task();gold=board.attribute(self.hero(),4)
        packet,response=self.op(85026,task_id=10101);self.assertEqual(packet['result'],0)
        self.assertEqual(self.record()['active']['tasks_done'],[10101]);self.assertEqual(self.task_value(210001),1)
        self.assertEqual(board.attribute(self.hero(),4),gold);self.assertIn(28007,response.scs)
        before=self.state();self.assertNotEqual(self.op(85026,task_id=10101)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_unfinished_or_foreign_local_task_is_rejected(self):
        self.enter();before=self.state()
        for tid in (10101,10102,99999):self.assertNotEqual(self.op(85026,task_id=tid)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_condition_progress_is_additive_and_updates_all_matching_tasks(self):
        self.enter();active=self.record()['active'];engine=move.Engine(4080101,active['map'])
        engine.pool(101991,13,[101111]);self.assertEqual(active['map']['task'][0]['progress'],1)
        engine.pool(101991,13,[101111]);self.assertEqual(active['map']['task'][0]['progress'],2)

    def test_true_terminal_records_history_unlock_and_external_task_without_direct_reward(self):
        self.mark_terminal();before=self.state();packet,response=self.exit();self.assertEqual(packet['result'],0)
        self.assertIn(4080101,self.record()['completed']);self.assertEqual(self.task_value(222001),1)
        rows=self.packets(response,51001)[0]['data_list'];self.assertEqual(rows,[dict(id=1150101,data=1)])
        self.assertEqual(self.enter(4080102)[0]['result'],0)
        for table in ('currency','material','chapter','stage_sub'):self.assertEqual(self.state()[table],before[table])

    def test_abandon_never_grants_clear_and_terminal_with_missing_tasks_rolls_back(self):
        self.enter();self.exit();self.assertEqual(self.record()['completed'],[])
        self.enter();extra,state=board.read(self.db,UID);board.set_attribute(board.player(state['active']['map']),7,1);board.save(self.db,UID,extra,state)
        before=self.state();self.assertNotEqual(self.exit()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_all_thirteen_maps_have_exact_tasks_and_completed_history_conditions(self):
        for chapter in board.LEVELS:
            self.mark_terminal(chapter);self.assertEqual(self.exit()[0]['result'],0)
        self.assertEqual(set(self.record()['completed']),set(board.LEVELS))
        payload=codec.decode(generator.gen_payload(51001,uid=UID,db=self.db),'sc_51001')
        rows=[r for r in payload['data_list'] if str(r['id']) in board.CFG['conditions']]
        self.assertEqual(len(rows),13);self.assertTrue(all(r['data']==1 for r in rows))

    def test_history_keeps_other_keys_but_ignores_captured_chess_clear(self):
        self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic,payload_json) VALUES(1,51001,?,?,0,?)',
            (b'',b'',json.dumps(dict(data_list=[dict(id=999,data='7'),dict(id=1150101,data='1')]))))
        payload=progress.history(self.db,UID);values={r['id']:r['data'] for r in payload['data_list']}
        self.assertEqual(values[999],7);self.assertEqual(values[1150101],0)

    def test_local_task_history_and_external_frame_encoding_failure_roll_back(self):
        self.enter();self.local_task();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_28007' else encode(n,d)):
            self.assertNotEqual(self.op(85026,task_id=10101)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.mark_terminal();before=self.state()
        for sc in (51001,85029):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result',0)==0 else encode(n,d)):
                self.assertNotEqual(self.exit()[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_all_thirteen_logical_chains_finish_tasks_clear_and_unlock_without_direct_rewards(self):
        completed=[]
        for chapter in board.LEVELS:
            self.fresh([],chapter);extra,state=board.read(self.db,UID);state['completed']=list(completed)
            board.set_attribute(board.player(state['active']['map']),4,100);board.save(self.db,UID,extra,state)
            for tid in board.CFG['map_tasks'][str(chapter)]:
                cid=next((int(cid) for cid,cfg in board.CFG['chances'].items() if cfg['chance_event'] and
                    any(row[0]==10013 and row[1]==tid for row in board.CFG['events'][str(cfg['chance_event'])]['player_params'])),None)
                if cid:
                    extra,state=board.read(self.db,UID);engine=move.Engine(chapter,state['active']['map'])
                    from summer_board_chance import open_chance
                    open_chance(engine,cid);board.save(self.db,UID,extra,state)
                    if self.hero()['chance']['is_unlock']==0:self.assertEqual(self.op(85020,chance_id=cid)[0]['result'],0)
                    cfg=board.CFG['chances'][str(cid)];spec=board.CFG['nested'].get(str(cid))
                    if spec and spec['type']:
                        packet,response=self.call(54030,dict(common_info=dict(type=spec['type'],dest=spec['dest'],hero_list=[dict(hero_id=1049,hero_type=1)])),54031)
                        self.assertEqual(packet['result'],0);self.bid=self.conn.battle_id[0];self.assertEqual(self.battle_result()[0]['result'],0)
                    if cfg['type']==14:
                        for _ in range(3):self.assertEqual(self.arena_roll([5])[0]['result'],0)
                    self.assertEqual(self.finish(cid,score=15 if cfg['type']==14 else 0)[0]['result'],0,(chapter,tid,cid))
                else:
                    self.assertEqual(tid,50103)
                    extra,state=board.read(self.db,UID);engine=move.Engine(chapter,state['active']['map'])
                    duck=next(h for h in engine.state['hero'] if h['id']>0 and h['type']==5019)
                    engine.entity(0)['pos']=dict(duck['pos']);engine.buff_effect(0,4,duck['id'])
                    events=engine.frames;progress.persist(self.conn.to_handler_ctx(),UID,extra,state,events)
                self.assertEqual(self.op(85026,task_id=tid)[0]['result'],0,(chapter,tid))
            self.assertEqual(self.exit()[0]['result'],0);completed.append(chapter)
            self.assertEqual(set(self.record()['completed']),set(completed))

    def test_external_task_reward_then_point_reward_complete_native_chain(self):
        from verify_minigame_point_rewards import ITEM_TYPES
        for iid in (40414,):self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,ITEM_TYPES[iid][0]))
        self.enter();self.local_task();self.op(85026,task_id=10101)
        packet,response=self.call(28010,dict(id=210001),28011);self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(54065),10);self.assertIn(17023,response.scs)
        visible=[dict(activity_id=280004,state=1,start_time=1,stop_time=2140000000)]
        with patch.object(generator,'get_activity_list',return_value=visible):
            packet,response=self.call(60054,dict(point_reward_id_list=[24190001]),60055);self.assertEqual(packet['result'],0)
            self.assertEqual(self.balance(40414),5);self.assertIn(17023,response.scs)
            before=self.balance(40414);self.assertNotEqual(self.call(60054,dict(point_reward_id_list=[24190001]),60055)[0]['result'],0)
            self.assertEqual(self.balance(40414),before)

    def test_external_task_progress_recovers_in_both_login_generators(self):
        import new_player_service
        self.enter();self.local_task();self.op(85026,task_id=10101)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id=210001',(UID,))
        # Scope out catalogue initialization, not the actual task projection.
        with patch.object(new_player_service,'ensure_tasks'):
            payload=new_player_service.payload(self.db,UID,28001)
        self.assertEqual(next(r['progress'] for r in payload['assignment_list'] if r['id']==210001),1)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id=210001',(UID,))
        payload=codec.decode(generator.gen_payload(28001,uid=UID,db=self.db),'sc_28001')
        self.assertEqual(next(r['progress'] for r in payload['assignment_list'] if r['id']==210001),1)


if __name__=='__main__':
    suite=unittest.TestSuite(FinishTests(n) for n in FinishTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
