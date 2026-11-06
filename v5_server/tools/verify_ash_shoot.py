"""Ash shooting: native Core/UDP packets against isolated memory saves."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID, NOW
import verify_minigame_point_rewards as items
import ash_service as ash
import battle_server as wire
import codec
import generator
import hero_codec
import middleware as mw
import minigame_service as mini
import minigame_point_service as point
import new_player_service
import task_listener
from event_bus import bus, Events
from inventory_service import InventoryService

ROOT=Path(__file__).resolve().parents[2]
TASKS=json.loads((ROOT/'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']
NORMAL=[v['stage_id'] for v in ash.STAGES.values() if v['type']==1]
ENDLESS=5280327


class AshTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        sessions=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);sessions.start();self.addCleanup(sessions.stop)
        self.activities=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400)
                         for i in [ash.ACTIVITY,ash.CFG['parent_activity'],*ash.CFG['limited_activities']]]
        visibility=patch.object(generator,'get_activity_list',side_effect=lambda *a,**kw:ash.activity_policy([dict(r) for r in self.activities]))
        visibility.start();self.addCleanup(visibility.stop);self.activity_patch=visibility
        self.native_tasks=[t for t in TASKS.values() if t['id'] in ash.LIMITED_TASKS or t['condition'] in (380001,380002)]
        for t in self.native_tasks:
            self.db.execute('INSERT OR REPLACE INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                (t['id'],t['type'],t['condition'],t['need'],t['activity_id'],json.dumps(t['additional_parameter']),json.dumps(t['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,t['id'],t['type']))
        reward_ids={i for t in self.native_tasks for i,n in t['reward']}
        reward_ids.update(i for cfg in point.CATALOG.values() if cfg['activity_id']==ash.ACTIVITY for i,n in cfg['reward'])
        for iid in reward_ids|{1,2,79}:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,1 if iid in (1,2,79) else items.ITEM_TYPES[iid][0]))
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        self.conn.resource_version='229'

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('ash_shoot_stage','activity_point','activity_point_reward','task','claim_ledger','currency','material','users','hero','chapter','stage_sub')}

    def enter(self,stage=NORMAL[0],activity=ash.ACTIVITY,kind=89,opts=None):
        data=dict(common_info=dict(type=kind,dest=stage,activity_id=activity,
                  hero_list=[dict(hero_id=614201,hero_type=2)],opts=[] if opts is None else opts))
        packet,response=self.call(54030,data,54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def finish(self,result=1,kills=0):
        body=wire._f_varint2(1,int(result==1))+wire._f_varint2(2,73000)+wire._f_varint2(11,kills)
        packet=wire._f_varint2(1,self.bid)+wire._f_varint2(2,UID)+wire._f_varint2(3,result)+wire._f_bytes(4,body)
        bid,uid,result,report=wire.parse_battle_result(packet)
        self.assertEqual((bid,uid),(self.bid,UID));self.assertNotIn('score',report)
        mw.get_battle_session(bid)['result_data']=dict(result=result,info=report)
        return self.call(54032,dict(battle_id=bid),54033)

    def clear(self,stage=NORMAL[0],kills=0,result=1):
        self.assertEqual(self.enter(stage)[0]['result'],0)
        packet,response=self.finish(result,kills);self.assertEqual(packet['result'],0)
        return packet,response

    def all_normal(self):
        for stage in NORMAL:self.clear(stage)

    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']

    def test_empty_native_snapshot_has_no_fabricated_pass_or_write(self):
        before=self.state()
        self.assertEqual(codec.decode(generator.gen_payload(68181,uid=UID,db=self.db),'sc_68181'),{})
        self.assertEqual(codec.decode(generator.gen_payload(12119,uid=UID,db=self.db),'sc_12119'),{})
        self.assertEqual(self.state(),before)

    def test_entry_rejects_unknown_wrong_type_activity_locked_and_bad_skills(self):
        before=self.state()
        for args in [(999999,ash.ACTIVITY,89),(NORMAL[0],1,89),(NORMAL[0],ash.ACTIVITY,1),(NORMAL[1],ash.ACTIVITY,89),(ENDLESS,ash.ACTIVITY,89)]:
            self.assertNotEqual(self.enter(*args)[0]['result'],0);self.assertEqual(self.state(),before)
        for opts in [[dict(key=1,value=[404101])],[dict(key=2,value=[])],[dict(key=1,value=[999])],[dict(key=1,value=[]),dict(key=1,value=[])]]:
            self.assertNotEqual(self.enter(opts=opts)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_twelve_normal_first_clears_grant_exact_configured_100_film(self):
        total=0
        for stage in NORMAL:
            packet,response=self.clear(stage);total+=ash.STAGES[ash.REAL[stage]]['film']
            self.assertEqual(self.balance(79),total)
            self.assertEqual(response.scs.count(68183),1);self.assertNotIn(68181,response.scs)
            self.assertLess(response.scs.index(17023),response.scs.index(54033))
            self.assertLess(response.scs.index(68183),response.scs.index(54033))
        self.assertEqual(total,100);self.assertEqual(set(ash.read(self.db,UID)),set(ash.STAGES)-{404401})
        self.assertEqual(self.db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=?',(UID,ash.ACTIVITY))[0]['point'],100)
        self.assertEqual(self.db.query("SELECT COUNT(*) n FROM claim_ledger WHERE kind='ash_stage_first'")[0]['n'],12)

    def test_repeat_clear_and_same_battle_replay_do_not_grant_again(self):
        self.clear();before=self.state()
        self.assertEqual(self.call(54032,dict(battle_id=self.bid),54033)[0]['result'],0);self.assertEqual(self.state(),before)
        _,response=self.clear();self.assertEqual(self.state(),before)
        self.assertNotIn(17023,response.scs);self.assertNotIn(68183,response.scs)

    def test_regular_loss_and_exit_do_not_unlock_or_reward(self):
        for result in [2,3]:
            self.enter();before=self.state();packet,response=self.finish(result,100)
            self.assertEqual(packet['battle_result']['result'],result)
            self.assertEqual(self.state(),before);self.assertNotIn(68183,response.scs)

    def test_no_generic_stamina_exp_chapter_or_hero_growth(self):
        before=self.state();packet,_=self.clear();after=self.state()
        for table in ['users','hero','chapter','stage_sub']:self.assertEqual(after[table],before[table],table)
        self.assertEqual(packet['battle_result']['use_seconds'],73)
        self.assertEqual({r['id']:r['num'] for r in after['currency']},{79:5})

    def test_real_udp_kills_score_current_delta_and_best_login_snapshot(self):
        self.all_normal()
        for kills,result in [(40,1),(20,2),(0,3)]:
            _,response=self.clear(ENDLESS,kills,result)
            delta=codec.decode(next(f['payload'] for f in response.frames if f['sc']==68183),'sc_68183')
            self.assertEqual(delta['pass_stage']['point'],kills*100)
            self.assertEqual(ash.read(self.db,UID)[404401],4000);self.assertEqual(self.balance(79),100)
        cold=codec.decode(generator.gen_payload(68181,uid=UID,db=self.db),'sc_68181')
        self.assertEqual(cold['pass_stages'][-1],dict(stage_id=404401,point=4000))

    def test_specific_clear_film_score_and_aggregate_tasks_follow_native_conditions(self):
        self.all_normal();self.clear(ENDLESS,40)
        for t in self.native_tasks:
            if t['id'] in ash.LIMITED_TASKS:self.assertEqual(self.value(t['id']),t['need'],t['id'])
        self.assertEqual(self.call(28010,dict(id=40401018),28011)[0]['result'],0)

    def test_aggregate_counts_completed_children_not_battle_count(self):
        for _ in range(20):self.clear()
        self.assertEqual(self.value(40401019),0);self.assertEqual(self.balance(79),5)
        self.clear(NORMAL[1]);self.assertEqual(self.value(40401019),2)

    def test_skill_thresholds_unlock_from_real_currency(self):
        self.clear();self.assertEqual(self.enter(opts=[dict(key=1,value=[404101])])[0]['result'],0)
        self.assertNotEqual(self.enter(opts=[dict(key=1,value=[404102])])[0]['result'],0)
        self.all_normal();self.assertEqual(self.enter(opts=[dict(key=1,value=[404102])])[0]['result'],0)
        self.assertNotEqual(self.enter(opts=[dict(key=1,value=[404101,404102])])[0]['result'],0)

    def test_native_point_rewards_use_film_and_cannot_repeat(self):
        self.clear();self.assertNotEqual(self.call(60054,dict(point_reward_id_list=[363970101]),60055)[0]['result'],0)
        self.clear(NORMAL[1]);packet,response=self.call(60054,dict(point_reward_id_list=[363970101]),60055)
        self.assertEqual(packet['result'],0);self.assertEqual(self.balance(79),10);self.assertEqual(self.balance(1),50)
        self.assertLess(response.scs.index(17023),response.scs.index(60055));before=self.state()
        self.assertNotEqual(self.call(60054,dict(point_reward_id_list=[363970101]),60055)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_required_delta_task_inventory_and_ack_failures_rollback_then_retry(self):
        self.clear();self.enter(NORMAL[1]);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for failed in ['sc_28007','sc_68183','sc_54033']:
            def fail(name,obj):
                if name==failed and (name!='sc_54033' or obj.get('result')==0):return None
                return encode(name,obj)
            with patch.object(ctx,'codec_encode',side_effect=fail):self.assertNotEqual(self.finish()[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
        original=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**k:None if cmd==17023 else original(cmd,*a,**k)):
            self.assertNotEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.finish()[0]['result'],0)

    def test_required_event_failure_is_atomic_and_retryable(self):
        self.enter();before=self.state();handlers=list(bus._handlers[Events.ASH_PROGRESS])
        def fail(*a,**k):raise RuntimeError('isolated Ash subscriber failure')
        with patch.dict(bus._handlers,{Events.ASH_PROGRESS:handlers+[fail]}):self.assertNotEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertEqual(self.finish()[0]['result'],0)

    def test_resident_mode_hides_stops_and_blocks_limited_but_keeps_point_rewards(self):
        self.activities=[r for r in self.activities if r['activity_id']!=ash.CFG['parent_activity']]
        self.all_normal();self.clear(ENDLESS,40)
        self.assertTrue(all(self.value(tid)==0 for tid in ash.LIMITED_TASKS))
        self.assertTrue(all(r['state']==0 for r in generator.get_activity_list(self.db,UID) if r['activity_id'] in ash.CFG['limited_activities']))
        self.db.execute('UPDATE task SET progress=999 WHERE uid=?',(UID,));before=self.state()
        self.assertNotEqual(self.call(28010,dict(id=40401001),28011)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.call(60054,dict(point_reward_id_list=[363970101]),60055)[0]['result'],0)

    def test_login_recovers_tasks_and_mirror_without_regranting_film(self):
        self.all_normal();self.clear(ENDLESS,40)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        self.db.execute('UPDATE activity_point SET point=999 WHERE uid=?',(UID,))
        packet=codec.decode(generator.gen_payload(28001,uid=UID,db=self.db),'sc_28001')
        self.assertTrue(packet);self.assertEqual(self.value(40401018),1);self.assertEqual(self.value(40401024),18)
        self.assertEqual(self.balance(79),100);self.assertEqual(self.db.query('SELECT point FROM activity_point WHERE uid=?',(UID,))[0]['point'],100)

    def test_legacy_unknown_stages_and_fake_normal_points_filtered_readonly(self):
        for sid,score in [(404101,8000),(999999,8000),(404401,2345)]:
            self.db.execute('INSERT INTO ash_shoot_stage(uid,stage_id,point) VALUES(?,?,?)',(UID,sid,score))
        before=self.state();self.assertEqual(ash.read(self.db,UID),{404101:0,404401:2345});self.assertEqual(self.state(),before)

    def test_receipt_prevents_grant_if_progress_row_was_lost(self):
        self.clear();self.db.execute('DELETE FROM ash_shoot_stage WHERE uid=?',(UID,))
        self.clear();self.assertEqual(self.balance(79),5);self.assertIn(404101,ash.read(self.db,UID))

    def test_momotalk_unlock_notice_is_separate_uid_scoped_and_not_story_completion(self):
        before=self.state()
        for notice in [92910201,99999]:self.assertNotEqual(self.call(12120,dict(momotalk=notice),12121)[0]['result'],0)
        self.assertEqual(self.state(),before);self.all_normal()
        self.assertEqual(self.call(12120,dict(momotalk=92910201),12121)[0]['result'],0)
        before=self.state();self.assertEqual(self.call(12120,dict(momotalk=92910201),12121)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(codec.decode(generator.gen_payload(12119,uid=UID,db=self.db),'sc_12119'),dict(momotalk_list=[92910201]))
        self.assertEqual(ash.notices_payload(self.db,999),dict(momotalk_list=[]))

    def test_notice_ack_failure_rolls_back_and_generic_pt_cannot_double_count(self):
        self.all_normal();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode;before=self.state()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_12121' else encode(n,d)):
            self.assertNotEqual(self.call(12120,dict(momotalk=92910201),12121)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        task_listener.on_activity_point_gain(ctx,UID,pt_id=79,item_id=79,amount=100)
        self.assertEqual(self.value(40401007),0)

    def test_fresh_login_reads_after_task_repair_and_resident_filter(self):
        self.clear();self.clear(NORMAL[1])
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        with patch.object(new_player_service,'ensure_tasks'):
            payload=new_player_service.payload(self.db,UID,28001,res_version='229')
        progress={r['id']:r['progress'] for r in payload['assignment_list']}
        self.assertEqual(progress[40401001],1);self.assertEqual(progress[40401007],10)
        self.activities=[r for r in self.activities if r['activity_id']!=ash.CFG['parent_activity']]
        with patch.object(new_player_service,'ensure_tasks'):
            payload=new_player_service.payload(self.db,UID,28001,res_version='229')
        self.assertFalse(ash.LIMITED_TASKS.intersection(r['id'] for r in payload['assignment_list']))

    def test_native_contract_sweep_and_report_bounds(self):
        from export_ash_contract import build
        self.assertEqual(ash.CFG,build());self.assertEqual(len(ash.STAGES),13)
        before=self.state()
        with self.assertRaises(ValueError):
            mw._settle_frames(self.conn.to_handler_ctx(),UID,1,1,NORMAL[0],stage_type=89,activity_id=ash.ACTIVITY,sweep=True)
        for kills in [-1,True,2**32]:
            with self.assertRaises(Exception):ash.settle(self.conn.to_handler_ctx(),UID,NORMAL[0],ash.ACTIVITY,1,dict(enemy_dead_num=kills))
        self.assertEqual(self.state(),before)

    def test_static_captures_rebuilt_for_both_account_pushes_in_actual_login(self):
        import verify_minigame_empty_login as flood
        # Reuse the real flood/codec fixture; it isolates unrelated login services.
        with patch.object(flood,'COMMANDS',(68181,12119)):
            flood.EmptyLoginTests.flood(self,dynamic=0)

    def test_actual_229_311_catalog_uses_parent_state_not_version_number(self):
        import res_version_manager as versions
        self.activity_patch.stop()
        for aid in [ash.CFG['parent_activity'],ash.ACTIVITY,*ash.CFG['limited_activities']]:
            self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,?,?,1,?,4,36)',
                            (UID,aid,0 if aid==ash.CFG['parent_activity'] else 1,NOW+86400))
        for version in ['229','311']:
            with versions.connection_version(version):
                rows=generator.get_activity_list(self.db,UID)
                self.assertFalse(ash.limited_context(rows))
                self.assertEqual({r['state'] for r in rows if r['activity_id'] in ash.CFG['limited_activities']},{0})
        self.assertTrue(ash.limited_context(self.activities))
        next(r for r in self.activities if r['activity_id']==ash.CFG['parent_activity'])['stop_time']=NOW
        self.assertFalse(ash.limited_context(self.activities))

    def test_resident_bulk_claim_explicit_rejects_and_implicit_skips(self):
        self.activities=[r for r in self.activities if r['activity_id']!=ash.CFG['parent_activity']]
        self.db.execute('UPDATE task SET progress=999 WHERE uid=?',(UID,));before=self.state()
        self.assertNotEqual(self.call(28014,dict(id_list=[40401001]),28015)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call(28014,dict(id_list=[]),28015)[0]['result'],0)
        self.assertTrue(all(not r['claimed_ts'] for r in self.db.query('SELECT claimed_ts FROM task WHERE uid=?',(UID,))))


if __name__=='__main__':unittest.main(verbosity=2)
