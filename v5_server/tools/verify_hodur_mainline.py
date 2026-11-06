"""Hodur ordinary exploration probes using native packets and memory saves."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID, NOW
import verify_minigame_point_rewards as item_types
from account_db import AccountDB
import codec
import core
import generator
import hero_codec
import middleware as mw
import minigame_service as mini
import new_player_service
import hodur_service as hodur
import res_version_manager as versions
from event_bus import bus,Events

ROOT=Path(__file__).resolve().parents[2]
TASKS=json.loads((ROOT/'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class HodurTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        sessions=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);sessions.start();self.addCleanup(sessions.stop)
        self.activities=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400) for i in [3910001,3941101,3900401,3900402,3900403,3900404]]
        visible=patch.object(generator,'get_activity_list',side_effect=lambda *a,**k:hodur.activity_policy([dict(r) for r in self.activities]))
        visible.start();self.addCleanup(visible.stop);self.activity_patch=visible
        self.native_tasks=[t for t in TASKS.values() if 400001<=t['condition']<=400005 or t['id'] in hodur.LIMITED_TASKS]
        for t in self.native_tasks:self.add_task(t)
        for iid in {i for t in self.native_tasks for i,n in t['reward']}:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,1 if iid in (1,2,14) else item_types.ITEM_TYPES[iid][0]))
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        self.conn.resource_version='229'

    def add_task(self,t):
        self.db.execute('INSERT OR REPLACE INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
            (t['id'],t.get('type',3002),t['condition'],t['need'],t.get('activity_id',3941101),json.dumps(t.get('additional_parameter',[])),json.dumps(t.get('reward',[]))))
        self.db.execute('INSERT OR IGNORE INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,t['id'],t.get('type',3002)))

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('hodur_progress','task','claim_ledger','users','hero','currency','material','chapter','stage_sub','server_global_config')}

    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']
    def select(self,chapter=1,option=1011,activity=3941101):return self.call(89426,dict(activity_id=activity,chapter_id=chapter,option_id=option),89427)
    def reset(self,chapter=1,activity=3941101):return self.call(89422,dict(activity_id=activity,chapter_id=chapter),89423)

    def enter(self,stage=5310220,activity=3941101,kind=92):
        packet,response=self.call(54030,dict(common_info=dict(type=kind,dest=stage,activity_id=activity,
            hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def result(self,result=1):
        mw.get_battle_session(self.bid)['result_data']=dict(result=result,info=dict(battle_time=43000))
        return self.call(54032,dict(battle_id=self.bid),54033)

    def clear(self,stage):
        self.assertEqual(self.enter(stage)[0]['result'],0)
        packet,response=self.result();self.assertEqual(packet['result'],0)
        return response

    def chapter(self,chapter):
        for stage in hodur.CHAPTERS[chapter]['stages']:
            event=hodur.STAGES[stage]['event_lock']
            if event:self.assertEqual(self.select(chapter,hodur.EVENTS[event]['options'][0])[0]['result'],0)
            self.clear(stage)

    def test_native_initial_snapshot_readonly_and_no_fabricated_hero(self):
        before=self.state();packet=codec.decode(generator.gen_payload(89421,uid=UID,db=self.db),'sc_89421')
        self.assertEqual([r['normal_stage_id'] for r in packet['normal_stage_info']],[1,2,3])
        self.assertTrue(all(not r.get('normal_hero_stage_info') for r in packet['normal_stage_info']))
        self.assertEqual(packet['challenge_stage_info'][0]['challenge_stage_id'],4)
        self.assertEqual(packet['challenge_stage_info'][0]['challenge_max_score'],0);self.assertEqual(self.state(),before)

    def test_unknown_cross_chapter_locked_and_early_event_reject_without_writes(self):
        before=self.state()
        for args in [(1,999999,3941101),(1,2011,3941101),(2,2011,3941101),(1,1011,3941101),(1,1011,1),(4,1011,3941101)]:
            self.assertNotEqual(self.select(*args)[0]['result'],0);self.assertEqual(self.state(),before)
        for chapter,act in [(999,3941101),(4,3941101),(2,3941101),(1,1)]:
            self.assertNotEqual(self.reset(chapter,act)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_natural_eight_battles_and_seven_choices_unlock_all_normal_tasks(self):
        for chapter in [1,2,3]:self.chapter(chapter)
        for row in self.native_tasks:
            if row['condition'] in (400001,400004,400005):self.assertEqual(self.value(row['id']),1,row['id'])
        data=hodur.read(self.db,UID)
        self.assertEqual([len(r['clears']) for r in data.values()],[2,3,3])
        self.assertEqual([len(r['options']) for r in data.values()],[1,3,3])

    def test_entry_rejects_wrong_type_activity_stage_and_missing_event(self):
        before=self.state()
        for stage,activity,kind in [(5310219,3941101,92),(5310213,3941101,92),(5310220,1,92),(5310220,3941101,1),(999999,3941101,92)]:
            self.assertNotEqual(self.enter(stage,activity,kind)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_battle_state_before_receipt_and_no_unrelated_growth(self):
        before=self.state();response=self.clear(5310220)
        self.assertIn(28007,response.scs);self.assertEqual(response.scs.count(89421),1)
        self.assertLess(response.scs.index(89421),response.scs.index(54033))
        after=self.state()
        for table in ['users','hero','currency','chapter','stage_sub']:self.assertEqual(after[table],before[table],table)
        packet,response=self.call(54032,dict(battle_id=self.bid),54033)
        self.assertEqual(packet['result'],0);self.assertNotIn(89421,response.scs);self.assertEqual(self.state(),after)

    def test_loss_and_exit_never_clear_or_issue_play_progress(self):
        for outcome in [2,3]:
            self.enter();before=self.state();packet,response=self.result(outcome)
            self.assertEqual(packet['battle_result']['result'],outcome)
            self.assertNotIn(89421,response.scs);self.assertEqual(self.state(),before)

    def test_reset_clears_current_bosses_choices_heroes_but_keeps_history(self):
        self.chapter(1);packet,response=self.reset();self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[89423])
        row=hodur.read(self.db,UID)[1]
        self.assertEqual(row['clears'],{5310220:0,5310219:0});self.assertEqual(row['options'],[]);self.assertEqual(row['heroes'],[])
        self.assertEqual(row['events_ever'],[101]);self.assertTrue(row['run_id'])
        self.assertEqual(self.value(40701002),1)
        self.assertNotEqual(self.select()[0]['result'],0)
        self.clear(5310220);self.assertEqual(self.select(option=1012)[0]['result'],0)
        # Earlier chapter history still unlocks chapter2 even after resetting1.
        self.assertEqual(self.select(2,2011)[0]['result'],0)

    def test_choices_are_mutually_exclusive_and_ack_uses_native_round(self):
        self.clear(5310220);self.assertEqual(self.select()[0]['round'],1);before=self.state()
        for option in [1011,1012]:self.assertNotEqual(self.select(option=option)[0]['result'],0);self.assertEqual(self.state(),before)
        self.clear(5310219);self.select(2,2011);self.clear(5310213)
        packet,response=self.select(2,2022);self.assertEqual(packet['round'],2)
        self.assertNotIn(89421,response.scs);self.assertIn(28007,response.scs)

    def test_reset_invalidates_already_open_battle_without_caching_success(self):
        self.enter();bid=self.bid;self.reset();before=self.state()
        self.assertNotEqual(self.result()[0]['result'],0)
        self.assertNotIn('br',mw.get_battle_session(bid));self.assertEqual(self.state(),before)

    def test_required_state_task_and_battle_ack_failures_roll_back_and_retry(self):
        self.enter();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for failed in ['sc_28007','sc_89421','sc_54033']:
            def fail(name,obj):
                if name==failed and (name!='sc_54033' or obj.get('result')==0):raise RuntimeError('isolated packet')
                return encode(name,obj)
            with patch.object(ctx,'codec_encode',side_effect=fail):self.assertNotEqual(self.result()[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.result()[0]['result'],0);self.assertEqual(self.value(40701001),1)

    def test_select_reset_ack_and_subscriber_failures_roll_back(self):
        self.clear(5310220);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for failed,action in [('sc_89427',lambda:self.select()),('sc_89423',lambda:self.reset())]:
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==failed else encode(n,d)):
                self.assertNotEqual(action()[0]['result'],0)
            self.assertEqual(self.state(),before)
        calls=[];handlers=list(bus._handlers[Events.HODUR_PROGRESS])
        def fail(*a,**k):calls.append(1);raise TypeError('isolated subscriber')
        with patch.dict(bus._handlers,{Events.HODUR_PROGRESS:handlers+[fail]}):self.assertNotEqual(self.select()[0]['result'],0)
        self.assertEqual(calls,[1]);self.assertEqual(self.state(),before)

    def test_legacy_zero_hp_and_unknown_ids_are_filtered_without_writes(self):
        self.db.execute('INSERT INTO hodur_progress(uid,activity_id,chapter_id,option_id_list,stage_clear_json,stat_json) VALUES(?,3941101,1,?,?,?)',
            (UID,json.dumps([1011,1012,2011,999999]),json.dumps([dict(boss_id=5310220,result=0),dict(boss_id=999999,result=1)]),
             json.dumps(dict(hero_id=236,hero_blood=0,hero_max_blood=45096))))
        before=self.state();row=codec.decode(generator.gen_payload(89421,uid=UID,db=self.db),'sc_89421')['normal_stage_info'][0]
        self.assertEqual(row['normal_hero_stage_info'][0]['hero_blood'],0)
        self.assertEqual(row['normal_boss_id_list'],[dict(boss_id=5310220,result=0)])
        self.assertEqual(row['normal_option_info'],[1011]);self.assertEqual(self.state(),before)

    def test_bad_json_never_overwrites_save_and_direct_bool_args_are_rejected(self):
        self.db.execute("INSERT INTO hodur_progress(uid,activity_id,chapter_id,option_id_list) VALUES(?,3941101,1,'broken')",(UID,))
        before=self.state();self.assertNotEqual(self.reset()[0]['result'],0);self.assertEqual(self.state(),before)
        other=AccountDB.new_save_workspace();self.addCleanup(other.close)
        ctx=self.conn.to_handler_ctx()
        with patch.object(ctx,'db',other):
            with self.assertRaises(core.OperationError):hodur.reset(ctx,UID,3941101,True)

    def test_normal_and_fresh_login_restore_old_completion_and_preserve_claimed_expired(self):
        self.chapter(1);self.reset();self.db.execute('UPDATE task SET progress=0')
        self.db.execute('UPDATE task SET complete_flag=1 WHERE task_id=60529')
        self.db.execute('UPDATE task SET expired_ts=? WHERE task_id=40701002',(NOW,))
        with patch.object(new_player_service,'enabled',return_value=False):generator.gen_28001({},self.db,UID)
        self.assertEqual(self.value(40701001),1);self.assertEqual(self.value(60529),0);self.assertEqual(self.value(40701002),0)
        self.assertEqual(self.value(40700008),1);self.assertEqual(self.value(40700005),1)
        self.db.execute('UPDATE task SET progress=0 WHERE task_id=40701001')
        with patch.object(new_player_service,'ensure_tasks'):new_player_service.payload(self.db,UID,28001)
        self.assertEqual(self.value(40701001),1)

    def test_resident_blocks_limited_rewards_and_preserves_permanent_claim(self):
        self.activities[0]['state']=0;self.chapter(1)
        self.assertEqual(self.value(40700005),0);self.assertEqual(self.value(40701001),1)
        limited=40700005;self.db.execute('UPDATE task SET progress=1 WHERE task_id=?',(limited,));before=self.state()
        self.assertNotEqual(self.call(28010,dict(id=limited),28011)[0]['result'],0)
        self.assertNotEqual(self.call(28014,dict(id_list=[limited,40701001]),28015)[0]['result'],0)
        self.assertEqual(self.state(),before)
        packet,response=self.call(28010,dict(id=40701001),28011)
        self.assertEqual(packet['result'],0);self.assertIn(17023,response.scs);self.assertEqual(self.balance(1),60)
        before=self.state();self.call(28010,dict(id=40701001),28011);self.assertEqual(self.state(),before)

    def test_implicit_bulk_skips_limited_and_login_hides_it(self):
        self.activities[0]['state']=0;self.clear(5310220)
        self.db.execute('UPDATE task SET progress=999999 WHERE task_id IN (40700005,40701001)')
        self.assertEqual(self.call(28014,dict(id_list=[]),28015)[0]['result'],0)
        self.assertEqual(self.db.query('SELECT claimed_ts FROM task WHERE task_id=40700005')[0]['claimed_ts'],0)
        with patch.object(new_player_service,'enabled',return_value=False):packet=codec.decode(generator.gen_28001({},self.db,UID),'sc_28001')
        self.assertNotIn(40700005,{r['id'] for r in packet['assignment_list']})

    def test_actual_resident_activity_projection_and_version_not_authority(self):
        self.activity_patch.stop()
        for aid in [3910001,3941101,*hodur.CFG['limited_activities']]:
            self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,?,?,1,?,4,39)',(UID,aid,0 if aid==3910001 else 1,NOW+86400))
        for ver in ['229','311']:
            with versions.connection_version(ver):
                rows=generator.get_activity_list(self.db,UID)
                self.assertFalse(hodur.limited_context(rows));self.assertEqual({r['state'] for r in rows if r['activity_id'] in hodur.CFG['limited_activities']},{0})
        self.assertTrue(hodur.limited_context(self.activities));self.activities[0]['stop_time']=NOW
        self.assertFalse(hodur.limited_context(self.activities))

    def test_no_generic_stage_count_and_save_uid_isolation(self):
        self.add_task(dict(id=990001,condition=501,need=30))
        self.clear(5310220);self.assertEqual(self.value(990001),0)
        other=AccountDB.new_save_workspace();self.addCleanup(other.close)
        self.assertEqual(hodur.read(other,UID)[1]['clears'],{});self.assertEqual(hodur.read(self.db,UID+1)[1]['clears'],{})

    def test_contract_native_export_and_sweep_rejection(self):
        from export_hodur_contract import build
        self.assertEqual(hodur.CFG,build());self.assertEqual(len(hodur.NORMAL),8)
        before=self.state()
        with self.assertRaises(ValueError):mw._settle_frames(self.conn.to_handler_ctx(),UID,1,1,5310220,stage_type=92,activity_id=3941101,sweep=True)
        self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main(verbosity=2)
