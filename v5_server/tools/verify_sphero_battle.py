"""Native66 Core/UDP probes; all account state lives in SQLite memory."""
from copy import deepcopy
from collections import Counter
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from verify_sphero_schedule import ScheduleTests
from verify_report_small_fixes import UID
import battle_server as wire
import codec
import generator
from inventory_service import InventoryService
import middleware as mw
import sphero_battle_service as battle
import sphero_schedule_service as schedule


class SPBattleTests(ScheduleTests):
    def setUp(self):
        super().setUp()
        clock=patch.object(battle,'time',SimpleNamespace(time=lambda:self.now));clock.start();self.addCleanup(clock.stop)
        sessions=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);sessions.start();self.addCleanup(sessions.stop)
        self.visible=[dict(activity_id=i,state=1,start_time=0,stop_time=4102444800) for i in (242841,242851)]
        visible=patch.object(generator,'get_activity_list',side_effect=lambda *a,**kw:deepcopy(self.visible));visible.start();self.addCleanup(visible.stop)
        for tid,cfg in battle.CFG['battle_tasks'].items():
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                (int(tid),cfg['type'],cfg['condition'],cfg['need'],cfg['activity_id'],json.dumps(cfg['additional_parameter']),json.dumps(cfg['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,int(tid),cfg['type']))
        for item in battle.POOL.values():self.db.execute('INSERT INTO item_catalog(id,type,category) VALUES(?,7,?)',(item['reward'][0],'equip'))
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,));self.conn.resource_version='229'

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
            ('ops_common','users','currency','material','task','claim_ledger','history_cmd','equip','hero','chapter','stage_sub','sp_hero_challenge_stage','sp_hero_boss_progress')}
    def enter(self,stage=5170201,kind=66,activity=242841,team=None,times=1):
        team=[dict(hero_id=610501,hero_type=2)] if team is None else team
        packet,response=self.call(54030,dict(common_info=dict(type=kind,dest=stage,activity_id=activity,hero_list=team,battle_times=times)),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response
    def finish(self,result=1,milliseconds=60000):
        body=wire._f_varint2(1,int(result==1))+wire._f_varint2(2,milliseconds)
        raw=wire._f_varint2(1,self.bid)+wire._f_varint2(2,UID)+wire._f_varint2(3,result)+wire._f_bytes(4,body)
        bid,uid,result,report=wire.parse_battle_result(raw);self.assertEqual((bid,uid),(self.bid,UID));self.assertNotIn('score',report)
        mw.get_battle_session(self.bid)['result_data']=dict(result=result,info=report)
        return self.call(54032,dict(battle_id=bid),54033)
    def clear(self,stage=5170201,**kwargs):
        self.assertEqual(self.enter(stage)[0]['result'],0,stage)
        packet,response=self.finish(**kwargs);self.assertEqual(packet['result'],0,stage)
        return packet,response
    def seed_story(self):
        for i in battle.STORY:self.db.execute("INSERT INTO sp_hero_challenge_stage(uid,activity_id,stage_id,stage_kind) VALUES(?,242841,?,'unlock')",(UID,i))
    def seed_train(self,stage,value):
        self.db.execute("INSERT INTO sp_hero_challenge_stage(uid,activity_id,stage_id,stage_kind,group_type,value) VALUES(?,242841,?,'progress',?,?)",
            (UID,stage,battle.STAGES[stage]['sub_type'],value))
    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']

    def test_native_contract_seventeen_stages_training_order_drops_and_pool(self):
        from export_sphero_contract import build
        self.assertEqual(battle.CFG,build());self.assertEqual(len(battle.STAGES),17)
        self.assertEqual(battle.training_gain(5170211,1000),500);self.assertEqual(battle.training_gain(5170211,1500),250)
        self.assertEqual(battle.training_gain(5170212,2500),250);self.assertEqual(battle.training_gain(5170213,2500),500)
        self.assertEqual(sum(i['total'] for i in battle.POOL.values()),18)

    def test_story_requires_real_predecessor_matching_type_and_schedule(self):
        for stage,kind,activity in [(5170202,66,242841),(5170201,1,242841),(5170211,66,242841),(5170201,66,242851),(99999,66,242841),(5170201,66,242841)]:
            before=self.state();self.assertNotEqual(self.enter(stage,kind,activity)[0]['result'],0);self.assertEqual(self.state(),before)
        self.daily([2428401]*4);self.assertEqual(self.enter()[0]['result'],0)

    def test_entry_uses_configured_trial_and_rejects_multi_battle(self):
        self.daily([2428401]*4)
        for team in ([],[dict(hero_id=1084,hero_type=1)],[dict(hero_id=610501,hero_type=1)],[dict(hero_id=610501,hero_type=2)]*2):
            before=self.state();self.assertNotEqual(self.enter(team=team)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.enter(times=2)[0]['result'],0)
        self.assertEqual(self.enter()[0]['result'],0);self.assertIn('sphero_context',mw.get_battle_session(self.bid))

    def test_story_clear_and_replay_no_second_reward_or_second_schedule_completion(self):
        self.daily([2428401]*4);packet,response=self.clear()
        self.assertEqual(self.balance(53100),200);self.assertEqual(len(battle.stages(self.db,UID)[0]),1)
        self.assertEqual(sum(s['is_finish'] for s in schedule.read(self.db,UID)['schedules']),1)
        self.assertIn(83017,response.scs);self.assertIn(17023,response.scs);self.assertNotIn(83016,response.scs)
        self.assertNotIn(24009,response.scs);self.assertEqual(battle.read(self.db,UID)['rewarded'],0)
        before=self.state();again,repeat=self.call(54032,dict(battle_id=self.bid),54033)
        self.assertEqual(again['result'],0);self.assertEqual(self.state(),before);self.assertEqual(repeat.scs,[54033])
        _,response=self.clear();self.assertEqual(self.balance(53100),200);self.assertNotIn(83017,response.scs)

    def test_story_chains_six_levels_and_unlock_history(self):
        self.daily([2428401]*4)
        for i in list(battle.STORY)[:4]:self.clear(i)
        self.assertNotEqual(self.enter(5170205)[0]['result'],0)
        self.now=schedule.get_daily_5am_ts(self.now)+86400;self.daily([2428401]*4)
        self.clear(5170205);_,response=self.clear(5170206)
        self.assertEqual(battle.stages(self.db,UID)[0],set(battle.STORY));self.assertEqual(self.balance(53100),1200)
        self.assertEqual(next(r['data'] for r in self.packets(response,51001)[-1]['data_list'] if r['id']==112001),1)
        self.assertEqual(self.enter(5170211)[0]['result'],0)

    def test_failed_or_quit_battle_has_no_progress_award_or_quota_consumption(self):
        self.daily([2428401]*4)
        for result in (2,3):
            self.enter();before=self.state();packet,response=self.finish(result)
            self.assertEqual(packet['result'],0);self.assertEqual(battle.read(self.db,UID)['rewarded'],0)
            self.assertEqual(battle.stages(self.db,UID)[0],set());self.assertEqual(self.balance(53100),0)
            self.assertFalse(any(s['is_finish'] for s in schedule.read(self.db,UID)['schedules']))
            self.assertEqual(response.scs,[54033]);self.assertEqual(packet['battle_result'].get('all_drop_list',[]),[])

    def test_training_native_gain_threshold_and_new_level_unlock(self):
        self.seed_story();self.daily([2428402]*4)
        packet,response=self.clear(5170211);self.assertEqual(battle.stages(self.db,UID)[2][2],500)
        self.assertEqual(self.balance(53100),80);self.assertEqual(self.value(202811),500)
        self.assertIn(28007,response.scs);self.assertIn(83016,response.scs);self.assertNotIn(83015,response.scs)
        cold=self.packets(response,83016)[0];self.assertEqual(cold['challenge_times'],1)
        self.assertEqual(len(cold['train_list']),3);self.assertEqual(cold['train_list'][0]['value'],500)
        self.clear(5170211);self.clear(5170211);self.assertEqual(battle.stages(self.db,UID)[2][2],1500)
        self.clear(5170211);self.assertEqual(battle.stages(self.db,UID)[2][2],1750)

    def test_training_three_difficulties_are_not_timed_grades(self):
        self.seed_story();self.seed_train(5170211,2500);self.seed_train(5170212,2500)
        self.assertEqual(battle.training_gain(5170211,2500),200)
        self.assertEqual(battle.training_gain(5170212,2500),250)
        self.assertEqual(battle.training_gain(5170213,2500),500)
        self.clear(5170213,milliseconds=360000);self.assertEqual(battle.stages(self.db,UID)[2][2],3000)
        self.assertEqual(self.value(202801),1)

    def test_all_nine_training_levels_keep_three_groups_independent(self):
        self.seed_story()
        for group in (2,3,4):
            self.now=schedule.get_daily_5am_ts(self.now)+86400
            for stage,cfg in battle.TRAIN.items():
                if cfg['sub_type']==group:self.clear(stage)
            records=battle.stages(self.db,UID)
            self.assertEqual(records[2][group],1500)
            self.assertEqual(len([i for i in records[1] if battle.TRAIN[i]['sub_type']==group]),3)
        self.assertEqual(battle.stages(self.db,UID)[2],{2:1500,3:1500,4:1500});self.assertEqual(self.balance(53100),900)

    def test_quota_exhaustion_allows_play_and_clear_but_no_currency_or_training_gain(self):
        self.seed_story();self.seed_train(5170211,2000);state=battle.read(self.db,UID);state['rewarded']=4;battle.save(self.db,UID,state)
        self.daily([2428402]*4);packet,response=self.clear(5170212)
        self.assertEqual(battle.stages(self.db,UID)[2][2],2000);self.assertIn(5170212,battle.stages(self.db,UID)[1])
        self.assertEqual(self.balance(53100),0);self.assertNotIn(83016,response.scs);self.assertIn(83015,response.scs)
        self.assertIn(83017,response.scs);self.assertEqual(packet['battle_result'].get('all_drop_list',[]),[])

    def test_boss_full_score_for_fast_and_slow_success_keeps_both_best_records(self):
        self.seed_story();self.daily([2428405]*4)
        self.clear(5170221,milliseconds=10000);_,response=self.clear(5170222,milliseconds=600000)
        state=battle.read(self.db,UID);self.assertEqual(state['best'],{'5170221':5500000,'5170222':5500000})
        self.assertEqual((self.value(202841),self.value(202842)),(1,1))
        packet=self.packets(response,83020)[0];self.assertTrue(packet['is_start']);self.assertEqual(packet['fight_cnt'],2)
        self.assertEqual(len(packet['score_info_list']),2);self.assertEqual(packet['stage_id_list'],[5170221,5170222])
        self.assertEqual(len(packet['got_award_cfg_list']),2);self.assertEqual(len(self.db.query('SELECT * FROM equip WHERE uid=?',(UID,))),2)
        self.assertEqual(self.balance(53100),500)
        delta=self.packets(response,17023)[0];self.assertEqual(len(delta['equip_list']),1)
        self.assertEqual(delta['equip_list'][0]['num'],1);self.assertGreater(delta['equip_list'][0]['equip_id'],0)

    def test_training_and_boss_share_four_daily_rewards(self):
        self.seed_story()
        for _ in range(3):self.clear(5170211)
        self.clear(5170221);_,response=self.clear(5170222)
        state=battle.read(self.db,UID);self.assertEqual(state['rewarded'],4);self.assertEqual(state['boss_fights'],2)
        self.assertEqual(self.balance(53100),440);self.assertEqual(len(state['pool_draws']),1)
        self.assertEqual(self.value(202842),1);self.assertNotIn(83016,response.scs);self.assertIn(83020,response.scs)

    def test_boss_pool_has_eighteen_actual_instances_and_no_fabricated_receipts(self):
        self.seed_story()
        for i in range(18):
            if i and i%4==0:self.now=schedule.get_daily_5am_ts(self.now)+86400
            self.clear(5170221)
        state=battle.read(self.db,UID);self.assertEqual(Counter(state['pool_draws']),Counter({i:3 for i in battle.POOL}))
        self.assertEqual(len(self.db.query('SELECT * FROM equip WHERE uid=?',(UID,))),18)
        self.now+=86400;self.clear(5170221);self.assertEqual(len(battle.read(self.db,UID)['pool_draws']),18)
        self.assertEqual(len(self.db.query('SELECT * FROM equip WHERE uid=?',(UID,))),18)

    def test_cross_day_and_newer_entry_reject_old_receipt(self):
        self.daily([2428401]*4);self.enter();old=self.bid;self.enter();new=self.bid
        self.bid=old;before=self.state();self.assertNotEqual(self.finish()[0]['result'],0);self.assertEqual(self.state(),before)
        self.bid=new;self.now=schedule.get_daily_5am_ts(self.now)+86400;before=self.state()
        self.assertNotEqual(self.finish()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_daily_reset_retains_best_scores_pool_and_training(self):
        self.seed_story();self.clear(5170211);self.clear(5170221);self.now=schedule.get_daily_5am_ts(self.now)+86400
        state=battle.read(self.db,UID);self.assertEqual(state['rewarded'],0);self.assertEqual(state['boss_fights'],0)
        self.assertEqual(state['boss_today'],[]);self.assertEqual(state['best']['5170221'],5500000);self.assertEqual(len(state['pool_draws']),1)
        self.assertEqual(battle.stages(self.db,UID)[2][2],500)

    def test_closed_boss_blocks_entry_and_settlement_without_rewards(self):
        self.seed_story();self.enter(5170221);self.visible[1]['state']=0;before=self.state()
        self.assertNotEqual(self.finish()[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.enter(5170221)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_legacy_boss_score_and_claim_history_retained_without_free_inventory(self):
        self.db.execute('INSERT INTO sp_hero_boss_progress(uid,activity_id,boss_stage_id,score,achieved_list) VALUES(?,242851,5170222,4400000,?)',(UID,'[401,402,403,406]'))
        state=battle.read(self.db,UID);self.assertEqual(state['best'],{'5170222':4400000});self.assertEqual(len(state['pool_draws']),4)
        self.assertEqual(self.db.query('SELECT * FROM equip WHERE uid=?',(UID,)),[])
        packet=codec.decode(generator.gen_payload(83020,uid=UID,db=self.db),'sc_83020');self.assertTrue(packet['is_start'])
        self.assertEqual(packet['score_info_list'],[dict(stage_id=5170222,score=4400000)])

    def test_missing_task_progress_restores_from_train_and_boss_facts(self):
        self.seed_story();self.seed_train(5170211,3000);state=battle.read(self.db,UID);state['best']={'5170221':5500000};battle.save(self.db,UID,state)
        changes=battle.project_tasks(self.db,UID);self.assertTrue(changes);self.assertEqual(self.value(202801),1);self.assertEqual(self.value(202841),1)
        self.assertEqual(self.value(202842),0);self.assertEqual(self.value(202815),2500)
        self.assertEqual(battle.project_tasks(self.db,UID),[])

    def test_claimed_tasks_and_other_activity_thresholds_are_not_overwritten(self):
        self.seed_story();self.seed_train(5170211,3000)
        self.db.execute('UPDATE task SET complete_flag=1,claimed_ts=1 WHERE uid=? AND task_id=202811',(UID,))
        self.db.execute('INSERT INTO task_cfg(task_id,condition,need,additional_parameter,task_type,activity_id) VALUES(999111,160003,1,?,402,262003)',('[5170221,4000000]',))
        self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,999111,402)',(UID,))
        self.clear(5170221);self.assertEqual(self.value(202811),0);self.assertEqual(self.value(999111),0)

    def test_necessary_ack_and_state_failure_roll_back_reward_quota_claims_and_session_cache(self):
        self.seed_story();self.daily([2428405]*4);ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for target in ('sc_83020','sc_83016','sc_83017','sc_28007','sc_54033'):
            self.enter(5170221);before=self.state()
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==target and not d.get('result') else encode(n,d)):
                self.assertNotEqual(self.finish()[0]['result'],0)
            self.assertEqual(self.state(),before,target);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.finish()[0]['result'],0);self.assertEqual(len(battle.read(self.db,UID)['pool_draws']),1)

    def test_grant_failure_after_equipment_insert_rolls_back_instances(self):
        self.seed_story();self.enter(5170221);before=self.state();grant=InventoryService.grant_items
        def fail(*a,**kw):grant(*a,**kw);raise RuntimeError('isolated inventory failure')
        with patch.object(InventoryService,'grant_items',side_effect=fail):
            packet,response=self.finish();self.assertNotEqual(packet['result'],0);self.assertNotIn(17023,response.scs)
        self.assertEqual(self.state(),before);self.assertEqual(self.finish()[0]['result'],0)

    def test_no_world_xp_stamina_or_generic_chapter_growth(self):
        self.seed_story();before=self.state();self.clear(5170211)
        after=self.state()
        for key in ('users','hero','chapter','stage_sub'):self.assertEqual(after[key],before[key],key)
        self.assertEqual(self.balance(12),0);self.assertEqual(self.balance(4),0)

    def test_entry_packet_failure_does_not_publish_or_change_quota(self):
        self.daily([2428401]*4);before=self.state()
        with patch('battle_payload.build_sc_54003_dynamic',return_value=b''):
            self.assertNotEqual(self.enter()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertIsNone(battle.read(self.db,UID)['latest'])
        self.assertEqual(self.enter()[0]['result'],0)

    def test_sweep_and_unbound_settlement_are_rejected(self):
        self.seed_story();before=self.state()
        self.assertNotEqual(self.call(54038,dict(common_info=dict(type=66,dest=5170211,activity_id=242841,
            hero_list=[dict(hero_id=610501,hero_type=2)],battle_times=1)),54039)[0]['result'],0)
        self.assertEqual(self.state(),before)
        with self.assertRaises(Exception):mw._settle_frames(self.conn.to_handler_ctx(),UID,123,1,5170211,stage_type=66,activity_id=242841)
        self.assertEqual(self.state(),before)

    def test_battle_task_reward_can_be_claimed_once_with_atomic_currency(self):
        self.seed_story();self.clear(5170211)
        packet,response=self.call(28010,dict(id=202811),28011)
        self.assertEqual(packet['result'],0);self.assertEqual(self.balance(1),1030);self.assertIn(17023,response.scs)
        before=self.state();self.call(28010,dict(id=202811),28011);self.assertEqual(self.state(),before)

    def test_normal_and_fresh_task_snapshots_recover_battle_facts(self):
        self.seed_story();self.clear(5170211);self.clear(5170221)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,));generator.gen_payload(28001,uid=UID,db=self.db)
        self.assertEqual(self.value(202811),500);self.assertEqual(self.value(202841),1)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        import new_player_service
        with patch.object(new_player_service,'ensure_tasks'):
            payload=new_player_service.payload(self.db,UID,28001)
        values={r['id']:r['progress'] for r in payload['assignment_list']};self.assertEqual(values[202811],500);self.assertEqual(values[202841],1)

    def test_failed_inventory_delta_encoding_rolls_back_boss_and_can_retry(self):
        self.seed_story();self.enter(5170221);before=self.state();gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else gen(cmd,**kw)):
            self.assertNotEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertEqual(self.finish()[0]['result'],0)


if __name__=='__main__':
    suite=unittest.TestSuite(SPBattleTests(n) for n in SPBattleTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
