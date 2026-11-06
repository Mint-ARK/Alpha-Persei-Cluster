"""Kadas native Core/codec/SQL probes using memory saves only."""
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
import kadas_race_service as race
import minigame_service as mini
import new_player_service
import res_version_manager as versions
from event_bus import bus, Events

ROOT=Path(__file__).resolve().parents[2]
TASKS=json.loads((ROOT/'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class KadasTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.activities=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400) for i in [4310001,4343601,4300401,4300402,4300403,4300404]]
        visible=patch.object(generator,'get_activity_list',side_effect=lambda *a,**k:race.activity_policy([dict(r) for r in self.activities]))
        visible.start();self.addCleanup(visible.stop);self.activity_patch=visible
        self.native_tasks=[r for r in TASKS.values() if 423001<=r['condition']<=423006 or r['id'] in race.LIMITED_TASKS]
        for row in self.native_tasks:self.add_task(row)
        for iid in {i for row in self.native_tasks for i,n in row['reward']}:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',
                            (iid,1 if iid in (1,2,14) else item_types.ITEM_TYPES[iid][0]))
        self.conn.resource_version='229'

    def add_task(self,row):
        self.db.execute('INSERT OR REPLACE INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
            (row['id'],row.get('type',3002),row['condition'],row['need'],row.get('activity_id',4343601),json.dumps(row.get('additional_parameter',[])),json.dumps(row.get('reward',[]))))
        self.db.execute('INSERT OR IGNORE INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,row['id'],row.get('type',3002)))

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('kadas_race_progress','task','claim_ledger','currency','material','server_global_config')}

    def value(self,tid):
        return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']

    def complete(self,stage=436100,*,result=0,score=0,round=0,kills=0,activity=4343601):
        return self.call(84332,dict(activity_id=activity,stage_id=stage,result=result,score=score,round=round,kill_num=kills),84333)

    def modify(self,body=312,parts=None,activity=4343601):
        return self.call(84334,dict(activity_id=activity,tank_id=body,weapon_list=[106] if parts is None else parts),84335)

    def through(self,last=436107):
        for stage in range(436100,last+1):self.assertEqual(self.complete(stage)[0]['result'],0)

    def resident(self):
        self.activities[0]['state']=0

    def test_initial_native_snapshot_is_read_only_with_signed_no_record_sentinel(self):
        before=self.state();snapshot=codec.decode(generator.gen_payload(84331,uid=UID,db=self.db),'sc_84331')
        self.assertEqual(snapshot['tank_id'],312)
        self.assertEqual(snapshot['tank_info'],[dict(tank_id=312,weapon_list=[106])])
        self.assertEqual(snapshot['challenge_stage_info'],dict(round=0,max_score=-1));self.assertEqual(self.state(),before)

    def test_unknown_activity_stage_result_predecessor_and_numeric_bounds_reject_without_writes(self):
        before=self.state()
        for stage,kwargs in [(999999,{}),(436101,{}),(436401,{}),(436100,dict(activity=1)),(436100,dict(result=2))]:
            self.assertNotEqual(self.complete(stage,**kwargs)[0]['result'],0);self.assertEqual(self.state(),before)
        for args in [(True,436100,0,0,0,0),(4343601,436100,False,0,0,0),(4343601,436100,0,-1,0,0)]:
            with self.assertRaises(core.OperationError):race.settle(self.conn.to_handler_ctx(),UID,*args)
        self.assertEqual(self.state(),before)

    def test_story_and_normal_clear_sync_after_ack_and_do_not_pollute_challenge(self):
        self.add_task(dict(id=990001,condition=501,need=20,additional_parameter=[]))
        self.through(436100)
        packet,response=self.complete(436101,score=9999,round=8)
        self.assertEqual(packet['result'],0)
        self.assertLess(response.scs.index(84333),response.scs.index(84331))
        self.assertEqual(response.scs.count(84331),1)
        self.assertEqual(self.value(60545),1);self.assertEqual(self.value(50202007),1)
        self.assertEqual(self.value(50202102),2);self.assertEqual(self.value(990001),0)
        self.assertEqual(race.read(self.db,UID)['max_score'],-1);self.assertEqual(race.read(self.db,UID)['round'],0)

    def test_failed_normal_stage_never_clears_or_updates_challenge_but_counts_reported_weapon_kills(self):
        self.through(436100)
        self.assertEqual(self.complete(436101,result=1,score=9000,round=9,kills=3)[0]['result'],0)
        self.assertNotIn(436101,race.read(self.db,UID)['mission_list'])
        self.assertEqual(race.read(self.db,UID)['max_score'],-1);self.assertEqual(self.value(50202025),3)

    def test_all_stage_rewards_unlock_bodies_equipment_and_default_weapon_counts(self):
        self.through(436102)
        bodies,options=race.unlocked(race.read(self.db,UID)['mission_list'])
        self.assertEqual(bodies,{311,312});self.assertEqual(options,{103,106,204})
        self.assertEqual(self.value(50202103),3)  # default106 + newly unlocked103/204, not the body

    def test_branch_prerequisites_and_challenge_unlock_follow_native_cfg(self):
        self.through(436103)
        self.assertEqual(self.complete(436201)[0]['result'],0)
        self.assertNotEqual(self.complete(436301)[0]['result'],0)
        self.assertNotEqual(self.complete(436401)[0]['result'],0)
        self.through()
        self.assertEqual(self.complete(436401,score=6000,round=5,result=1)[0]['result'],0)
        self.assertEqual(race.read(self.db,UID)['max_score'],6000)

    def test_modify_rejects_locked_unknown_wrong_activity_and_overload_preserves_other_builds(self):
        before=self.state()
        for body,parts,activity in [(311,[106],4343601),(312,[103],4343601),(999,[106],4343601),(312,[999],4343601),(312,[106],1),(312,[106]*4,4343601)]:
            self.assertNotEqual(self.modify(body,parts,activity)[0]['result'],0);self.assertEqual(self.state(),before)
        self.through(436102)
        self.assertEqual(self.modify(312,[103,204])[1].scs,[84335])
        self.assertEqual(self.modify(311,[106])[0]['result'],0)
        builds={b['tank_id']:b['weapon_list'] for b in race.read(self.db,UID)['tank_info']}
        self.assertEqual(builds,{311:[106],312:[103,204]})
        self.assertEqual(self.modify(312,[103,103])[0]['result'],0)  # separate native mounts may reuse a module
        self.assertEqual(self.modify(312,[])[0]['result'],0)

    def test_weapon_kills_use_current_build_not_unlock_reward_type_and_equipment_is_excluded(self):
        self.through(436102);self.modify(312,[103,204])
        self.complete(436102,kills=4)
        stats=race.statistics(self.db,UID)
        self.assertEqual(stats['kills'].get('103'),4);self.assertNotIn('204',stats['kills'])
        self.assertEqual(self.value(50202022),3);self.assertEqual(self.value(50202025),0)

    def test_challenge_cumulative_points_best_and_round_are_separate_and_losing_run_counts(self):
        self.through();self.complete(436401,score=10000,round=5,result=1)
        self.complete(436401,score=6000,round=3,result=1)
        state=race.read(self.db,UID)
        self.assertEqual((state['max_score'],state['round']),(10000,5))
        self.assertEqual(race.statistics(self.db,UID)['points'],16000)
        self.assertEqual(self.value(50202029),1);self.assertEqual(self.value(50202030),1)
        self.assertEqual(self.value(50202027),16000)
        before=self.state();self.assertNotEqual(self.complete(436401,score=2**31)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_limited_task_aggregate_counts_real_completed_children(self):
        self.through(436104)
        self.assertEqual(self.value(50202001),5)
        self.assertEqual(self.value(50202002),5)

    def test_resident_blocks_limited_progress_single_claim_and_explicit_batch_but_keeps_permanent(self):
        self.resident();self.through(436101)
        self.assertEqual(self.value(50202007),0);self.assertEqual(self.value(50202102),2)
        self.db.execute('UPDATE task SET progress=999999 WHERE task_id IN (50202007,50202102)')
        before=self.state()
        self.assertNotEqual(self.call(28010,dict(id=50202007),28011)[0]['result'],0)
        self.assertNotEqual(self.call(28014,dict(id_list=[50202007,50202102]),28015)[0]['result'],0)
        self.assertEqual(self.state(),before)
        packet,response=self.call(28010,dict(id=50202102),28011)
        self.assertEqual(packet['result'],0);self.assertIn(17023,response.scs)

    def test_implicit_bulk_claim_skips_historical_limited_rows_and_keeps_other_tasks(self):
        self.resident()
        self.db.execute('UPDATE task SET progress=999999 WHERE task_id IN (50202007,50202101)')
        packet,_=self.call(28014,dict(type=3002),28015)
        self.assertEqual(packet['result'],0)
        self.assertEqual(self.db.query('SELECT claimed_ts FROM task WHERE task_id=50202007')[0]['claimed_ts'],0)
        self.assertGreater(self.db.query('SELECT claimed_ts FROM task WHERE task_id=50202101')[0]['claimed_ts'],0)

    def test_event_context_allows_native_limited_reward_once_and_version_alone_is_not_authority(self):
        self.through(436101)
        self.assertEqual(self.call(28010,dict(id=50202007),28011)[0]['result'],0)
        before=self.state();self.call(28010,dict(id=50202007),28011);self.assertEqual(self.state(),before)
        self.activities[0]['stop_time']=NOW
        self.assertFalse(race.limited(self.db,UID))  # still the same 229 connection

    def test_actual_activity_projection_disables_limited_tree_for_resident(self):
        self.activity_patch.stop()
        for aid in (4310001,4343601,4300401,4300402,4300403,4300404):
            self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,?,1,1,?,4,43)',(UID,aid,NOW+86400))
        with versions.connection_version('311'):
            rows=generator.get_activity_list(self.db,UID)
            self.assertFalse(race.limited_context(rows))
            self.assertTrue(all(r['state']==0 for r in rows if r['activity_id'] in race.CFG['limited_activities']))
        with versions.connection_version('229'):
            self.assertTrue(race.limited_context(generator.get_activity_list(self.db,UID)))

    def test_login_restores_allowed_tasks_and_hides_limited_rows_in_both_save_paths(self):
        self.through(436102);self.resident()
        self.db.execute('UPDATE task SET progress=0 WHERE task_id=50202102')
        with patch.object(new_player_service,'enabled',return_value=False):
            old=codec.decode(generator.gen_28001({},self.db,UID),'sc_28001')
        self.assertEqual(self.value(50202102),2)
        self.assertNotIn(50202007,{r['id'] for r in old['assignment_list']})
        with patch.object(new_player_service,'ensure_tasks'):
            fresh=new_player_service.payload(self.db,UID,28001)
        self.assertNotIn(50202007,{r['id'] for r in fresh['assignment_list']})

    def test_settlement_write_task_state_and_ack_failures_roll_back_everything(self):
        self.complete();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for failed in ('sc_28007','sc_84331','sc_84333'):
            def fail(name,obj):
                if name==failed and (name!='sc_84333' or obj.get('result')==0):raise RuntimeError('isolated race packet')
                return encode(name,obj)
            with patch.object(ctx,'codec_encode',side_effect=fail):
                self.assertNotEqual(self.complete(436101,kills=3)[0]['result'],0)
            self.assertEqual(self.state(),before)
        execute=self.db.execute
        def failed_write(sql,*a,**kw):
            result=execute(sql,*a,**kw)
            if sql.startswith('INSERT INTO server_global_config'):raise RuntimeError('isolated stat write-after')
            return result
        with patch.object(self.db,'execute',side_effect=failed_write):self.assertNotEqual(self.complete(436101,kills=3)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.complete(436101,kills=3)[0]['result'],0)

    def test_modify_ack_failure_rolls_back_selection_and_build(self):
        before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_84335' and d.get('result')==0 else encode(n,d)):
            self.assertNotEqual(self.modify(parts=[])[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_claimed_expired_malformed_tasks_and_same_uid_other_save_are_isolated(self):
        self.db.execute('UPDATE task SET complete_flag=1 WHERE task_id=60545')
        self.db.execute('UPDATE task SET expired_ts=? WHERE task_id=50202007',(NOW,))
        self.add_task(dict(id=990002,condition=423001,need=1,additional_parameter=[]))
        self.through(436101)
        self.assertEqual(self.value(60545),0);self.assertEqual(self.value(50202007),0);self.assertEqual(self.value(990002),0)
        other=AccountDB.new_save_workspace();self.addCleanup(other.close)
        self.assertEqual(race.read(other,UID)['mission_list'],[]);self.assertEqual(race.statistics(other,UID)['points'],0)
        self.assertEqual(race.read(self.db,UID+1)['mission_list'],[])

    def test_config_matches_native_lua_and_required_listener_runs_once_on_typeerror(self):
        from export_kadas_contract import build
        self.assertEqual(race.CFG,build())
        calls=[];handlers=list(bus._handlers[Events.KADAS_PROGRESS]);before=self.state()
        def fail(ctx,uid,**kw):calls.append(uid);raise TypeError('isolated listener body')
        with patch.dict(bus._handlers,{Events.KADAS_PROGRESS:handlers+[fail]}):
            self.assertNotEqual(self.complete()[0]['result'],0)
        self.assertEqual(calls,[UID]);self.assertEqual(self.state(),before)

    def test_actual_body_mount_counts_reject_overflow_even_when_cost_fits(self):
        self.through(436103)
        before=self.state()
        for body,parts in [(312,[103]*3),(312,[204]*3),(311,[204]*2),(313,[204]*2)]:
            self.assertNotEqual(self.modify(body,parts)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.modify(312,[103,103,204,204])[0]['result'],0)
        self.assertEqual(self.modify(311,[103,103,204])[0]['result'],0)

    def test_old_invalid_ids_and_overflow_builds_are_filtered_without_writing_save(self):
        self.through(436103)
        self.db.execute('UPDATE kadas_race_progress SET node=311,tank_info=? WHERE uid=?',
            (json.dumps([dict(tank_id=311,weapon_list=[103,103,103,204,204,999999]),dict(tank_id=999999,weapon_list=[106])]),UID))
        before=self.state()
        data=codec.decode(generator.gen_payload(84331,uid=UID,db=self.db),'sc_84331')
        self.assertEqual(data['tank_info'],[dict(tank_id=311,weapon_list=[103,103,204])])
        self.assertEqual(self.state(),before)

    def test_old_statistics_lower_bounds_and_late_tasks_resume_without_summing_tiers(self):
        self.db.execute('UPDATE task SET progress=12000 WHERE task_id=50202027')
        self.db.execute('UPDATE task SET progress=15000 WHERE task_id=50202028')
        self.through();self.complete(436401,score=1000,kills=2)
        self.assertEqual(race.statistics(self.db,UID)['points'],16000)
        self.add_task(dict(id=990003,condition=423006,need=999999,additional_parameter=[]))
        self.add_task(dict(id=990004,condition=423004,need=99,additional_parameter=[106]))
        race.restore_for_login(self.db,UID)
        self.assertEqual(self.value(990003),16000);self.assertEqual(self.value(990004),2)


if __name__=='__main__':unittest.main(verbosity=2)
