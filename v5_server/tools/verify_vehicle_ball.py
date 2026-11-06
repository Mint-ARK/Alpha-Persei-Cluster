"""Native vehicle-ball Core/codec/SQL checks, memory databases only."""
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
import minigame_service as mini
import new_player_service
import res_version_manager as versions
import vehicle_ball_service as vehicle
from event_bus import bus, Events
from inventory_service import InventoryService

ROOT=Path(__file__).resolve().parents[2]
TASKS=json.loads((ROOT/'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class VehicleTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.activities=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400) for i in [4210001,4243101,4200401,4200402,4200403,4200404]]
        visible=patch.object(generator,'get_activity_list',side_effect=lambda *a,**k:vehicle.activity_policy([dict(r) for r in self.activities]))
        visible.start();self.addCleanup(visible.stop);self.activity_patch=visible
        self.native_tasks=[t for t in TASKS.values() if 422001<=t['condition']<=422004 or t['id'] in vehicle.LIMITED_TASKS]
        for t in self.native_tasks:self.add_task(t)
        for iid in {i for t in self.native_tasks for i,n in t['reward']}|{1}:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,1 if iid in (1,2,14) else item_types.ITEM_TYPES[iid][0]))
        self.conn.resource_version='229'

    def add_task(self,t):
        self.db.execute('INSERT OR REPLACE INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
            (t['id'],t.get('type',3002),t['condition'],t['need'],t.get('activity_id',4243101),json.dumps(t.get('additional_parameter',[])),json.dumps(t.get('reward',[]))))
        self.db.execute('INSERT OR IGNORE INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,t['id'],t.get('type',3002)))

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('vehicle_ball_progress','task','claim_ledger','currency','material','server_global_config')}

    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']

    def settle(self,stage=50101,win=1,kills=0,merges=0,buffs=None,activity=4243101):
        return self.call(89702,dict(activity_id=activity,stage_id=stage,is_pass=win,
            kv_list=[dict(key=1,value=kills),dict(key=2,value=merges)],buffs=buffs or []),89703)

    def unlock(self,buff=1001,activity=4243101):return self.call(89708,dict(activity_id=activity,buff_id=buff),89709)
    def claim(self,buff=1001,activity=4243101):return self.call(89706,dict(activity_id=activity,buff_id=buff),89707)
    def select(self,vid=50111,activity=4243101):return self.call(89704,dict(activity_id=activity,vehicle=vid),89705)
    def resident(self):self.activities[0]['state']=0

    def test_initial_snapshot_is_native_and_read_only(self):
        before=self.state();p=codec.decode(generator.gen_payload(89701,uid=UID,db=self.db),'sc_89701')
        self.assertEqual(p.get('stages',[]),[]);self.assertEqual(p['using_vehicle'],50111)
        self.assertEqual(p.get('unlock_buffs',[]),[]);self.assertEqual(self.state(),before)

    def test_stage_prerequisite_activity_result_and_numeric_guards(self):
        before=self.state()
        for args in [(50102,{}),(50106,{}),(999999,{}),(50101,dict(activity=0)),(50101,dict(win=2)),(50101,dict(buffs=[999999]))]:
            self.assertNotEqual(self.settle(args[0],**args[1])[0]['result'],0);self.assertEqual(self.state(),before)
        ctx=self.conn.to_handler_ctx()
        for kv in [[dict(key=1,value=-1)],[dict(key=1,value=True)],[dict(key=1,value=2**32)],[dict(key=3,value=1)],[dict(key=1,value=1)]*2]:
            with self.assertRaises(core.OperationError):vehicle.settle(ctx,UID,4243101,50101,1,kv,[])
        self.assertEqual(self.state(),before)

    def test_unlocked_count_is_not_kills_and_kills_are_not_stage_wins(self):
        self.assertEqual(self.settle(kills=30,merges=7)[0]['result'],0)
        self.assertEqual(self.value(50103003),0)
        self.assertEqual(self.value(50105001),25);self.assertEqual(self.value(50105002),30)
        self.assertEqual(self.value(50104002),7)
        self.assertEqual(self.value(50101001),1);self.assertEqual(self.value(60541),1)

    def test_unlock_has_task_delta_and_does_not_reset_claimed_flag(self):
        for b in [1001,1002,1003,1004]:self.unlock(b)
        packet,response=self.unlock(2001)
        self.assertEqual(packet['result'],0);self.assertEqual(self.value(50103003),5)
        self.assertIn(28007,response.scs);self.assertNotIn(89701,response.scs)
        self.assertEqual(self.claim()[0]['result'],0)
        self.assertEqual(self.unlock()[1].scs,[89709]);self.settle(buffs=[1001])
        self.assertEqual(vehicle.read(self.db,UID)['unlock_buffs'][0]['value'],1)

    def test_failures_keep_run_stats_and_buffs_but_do_not_clear_stage(self):
        self.assertEqual(self.settle(win=0,kills=9,merges=3,buffs=[1001,2001])[0]['result'],0)
        self.assertEqual(vehicle.read(self.db,UID)['stages'],[])
        self.assertEqual(self.value(50101001),0);self.assertEqual(self.value(50105001),9)
        self.assertEqual(vehicle.statistics(self.db,UID)['merges'],3)

    def test_four_stage_order_and_native_vehicle_unlocks(self):
        self.assertNotEqual(self.select(50112)[0]['result'],0)
        self.assertNotEqual(self.select(50121)[0]['result'],0)
        self.assertNotEqual(self.select(999999)[0]['result'],0)
        self.assertNotEqual(self.select(activity=1)[0]['result'],0)
        self.settle();self.assertEqual(self.select(50112)[1].scs,[89705])
        self.assertNotEqual(self.select(50113)[0]['result'],0)
        self.settle(50102);self.assertEqual(self.select(50113)[0]['result'],0)
        self.settle(50103);self.settle(50104)
        cold=codec.decode(generator.gen_payload(89701,uid=UID,db=self.db),'sc_89701')
        self.assertEqual(cold['stages'],list(vehicle.STAGES));self.assertEqual(cold['using_vehicle'],50113)

    def test_rewards_require_unlocked_known_rewardable_buff(self):
        before=self.state()
        for buff,activity in [(1001,4243101),(999999,4243101),(5004,4243101),(1001,1)]:
            self.assertNotEqual(self.claim(buff,activity)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.unlock(999999)[0]['result'],0)
        self.assertNotEqual(self.unlock(activity=1)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_reward_native_receipt_balance_delta_and_no_duplicate_ui_success(self):
        self.unlock();packet,response=self.claim()
        self.assertEqual(packet['result'],0);self.assertEqual(packet['reward_list'],[dict(id=1,num=10)])
        self.assertEqual(self.balance(1),10);self.assertEqual(response.scs.count(17023),1);self.assertNotIn(89701,response.scs)
        delta=codec.decode(next(f['payload'] for f in response.frames if f['sc']==17023),'sc_17023')
        self.assertEqual(delta['normal_items'],[dict(id=1,num=10)])
        before=self.state();self.assertNotEqual(self.claim()[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.execute('UPDATE vehicle_ball_progress SET unlock_buffs_json=? WHERE uid=?',(json.dumps([dict(key=1001,value=0)]),UID))
        before=self.state();self.assertNotEqual(self.claim()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_reward_comes_from_contract_not_hardcoded_amount(self):
        self.unlock()
        with patch.dict(vehicle.BUFFS,{1001:dict(type=1,reward=[1,13])}):self.assertEqual(self.claim()[0]['reward_list'],[dict(id=1,num=13)])
        self.assertEqual(self.balance(1),13)

    def test_grant_write_and_required_packets_roll_back_and_retry(self):
        self.unlock();before=self.state();grant=InventoryService.grant_items
        def grant_then_fail(*a,**k):grant(*a,**k);raise RuntimeError('isolated after grant')
        with patch.object(InventoryService,'grant_items',side_effect=grant_then_fail):self.assertNotEqual(self.claim()[0]['result'],0)
        self.assertEqual(self.state(),before)
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for failed in ['sc_89707']:
            def fail(name,obj):
                if name==failed and (name!='sc_89707' or obj.get('result')==0):raise RuntimeError('isolated packet')
                return encode(name,obj)
            with patch.object(ctx,'codec_encode',side_effect=fail):self.assertNotEqual(self.claim()[0]['result'],0)
            self.assertEqual(self.state(),before)
        gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**k:None if cmd==17023 else gen(cmd,*a,**k)):
            self.assertNotEqual(self.claim()[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.claim()[0]['result'],0);self.assertEqual(self.balance(1),10)

    def test_settle_unlock_select_packet_failure_rollbacks(self):
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode;before=self.state()
        for name,action in [('sc_28007',lambda:self.settle()),('sc_89703',lambda:self.settle()),('sc_89709',lambda:self.unlock()),('sc_89705',lambda:self.select())]:
            def fail(n,d):
                if n==name and (n=='sc_28007' or d.get('result')==0):raise RuntimeError('isolated packet')
                return encode(n,d)
            with patch.object(ctx,'codec_encode',side_effect=fail):self.assertNotEqual(action()[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.assertEqual(self.settle()[0]['result'],0)

    def test_no_generic_stage_tasks_and_no_redundant_full_snapshot(self):
        self.add_task(dict(id=990001,condition=501,need=50))
        self.add_task(dict(id=990002,condition=1001,need=50))
        packet,response=self.settle()
        self.assertEqual(self.value(990001),0);self.assertEqual(self.value(990002),0)
        self.assertEqual(response.scs,[28007,89703]);self.assertEqual(self.settle()[1].scs,[89703])

    def test_aggregate_counts_completed_child_tasks_not_runs(self):
        self.settle(kills=100,merges=7,buffs=[1001,1002,1003,1004,2001])
        # clear1 + unlocked5 + modifications3/7 + kills25/100 = six children
        self.assertEqual(self.value(50102001),4);self.assertEqual(self.value(50102002),6)
        self.settle(kills=0,merges=0);self.assertEqual(self.value(50102002),6)

    def test_resident_limited_guard_single_explicit_and_implicit_bulk_claim(self):
        self.resident();self.settle(kills=200,merges=30,buffs=[1001])
        self.assertEqual(self.value(50103001),0);self.assertEqual(self.value(50101001),1)
        self.db.execute('UPDATE task SET progress=999999 WHERE task_id=50103001')
        before=self.state()
        self.assertNotEqual(self.call(28010,dict(id=50103001),28011)[0]['result'],0)
        self.assertNotEqual(self.call(28014,dict(id_list=[50103001,50101001]),28015)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call(28014,dict(id_list=[]),28015)[0]['result'],0)
        self.assertEqual(self.db.query('SELECT claimed_ts FROM task WHERE task_id=50103001')[0]['claimed_ts'],0)
        self.assertEqual(self.balance(1),80)

    def test_active_event_claim_and_same_version_expiry(self):
        self.settle();self.assertEqual(self.call(28010,dict(id=50103001),28011)[0]['result'],0)
        self.settle(50102);self.activities[0]['stop_time']=NOW
        self.assertNotEqual(self.call(28010,dict(id=50103002),28011)[0]['result'],0)

    def test_actual_activity_projection_keeps_permanent_entry_and_closes_limited_tree(self):
        self.activity_patch.stop()
        for aid in [4210001,4243101,*vehicle.CFG['limited_activities']]:
            self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,?,?,1,?,4,42)',
                (UID,aid,0 if aid==4210001 else 1,NOW+86400))
        for version in ['229','311']:
            with versions.connection_version(version):
                rows=generator.get_activity_list(self.db,UID)
                self.assertFalse(vehicle.limited_context(rows))
                self.assertEqual(next(r['state'] for r in rows if r['activity_id']==4243101),1)
                self.assertEqual({r['state'] for r in rows if r['activity_id'] in vehicle.CFG['limited_activities']},{0})

    def test_old_and_fresh_login_recover_facts_and_filter_limited(self):
        self.resident();self.settle();self.db.execute('UPDATE task SET progress=0 WHERE task_id=50101001')
        with patch.object(new_player_service,'enabled',return_value=False):old=codec.decode(generator.gen_28001({},self.db,UID),'sc_28001')
        self.assertEqual(self.value(50101001),1);self.assertNotIn(50103001,{t['id'] for t in old['assignment_list']})
        self.db.execute('UPDATE task SET progress=0 WHERE task_id=50101001')
        with patch.object(new_player_service,'ensure_tasks'):fresh=new_player_service.payload(self.db,UID,28001)
        self.assertEqual(self.value(50101001),1);self.assertNotIn(50103001,{t['id'] for t in fresh['assignment_list']})

    def test_corrupt_legacy_ids_filtered_readonly_and_bad_json_rejected(self):
        self.db.execute('INSERT INTO vehicle_ball_progress(uid,using_vehicle,stages_json,unlock_buffs_json) VALUES(?,?,?,?)',
            (UID,999999,json.dumps([50101,999999]),json.dumps([dict(key=1001,value=0),dict(key=1001,value=1),dict(key=999999,value=0)])))
        before=self.state();cold=codec.decode(generator.gen_payload(89701,uid=UID,db=self.db),'sc_89701')
        self.assertEqual(cold,dict(stages=[50101],using_vehicle=50111,unlock_buffs=[dict(key=1001,value=1)]));self.assertEqual(self.state(),before)
        self.db.execute("UPDATE vehicle_ball_progress SET unlock_buffs_json='broken' WHERE uid=?",(UID,))
        before=self.state();self.assertNotEqual(self.unlock()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_late_tasks_use_saved_statistics_without_summing_old_tiers(self):
        self.db.execute('UPDATE task SET progress=7 WHERE task_id=50104002')
        self.db.execute('UPDATE task SET progress=13 WHERE task_id=50104003')
        self.db.execute('UPDATE task SET progress=9 WHERE task_id=50105001')
        self.settle(kills=2,merges=1)
        self.assertEqual(vehicle.statistics(self.db,UID),dict(version=1,kills=2,merges=14))
        self.add_task(dict(id=990003,condition=422004,need=999))
        vehicle.restore_for_login(self.db,UID);self.assertEqual(self.value(990003),2)

    def test_claimed_expired_task_protection_and_save_isolation(self):
        self.db.execute('UPDATE task SET complete_flag=1 WHERE task_id=60541')
        self.db.execute('UPDATE task SET expired_ts=? WHERE task_id=50101001',(NOW,))
        self.settle();self.assertEqual(self.value(60541),0);self.assertEqual(self.value(50101001),0)
        other=AccountDB.new_save_workspace();self.addCleanup(other.close)
        self.assertEqual(vehicle.read(other,UID)['stages'],[]);self.assertEqual(vehicle.read(self.db,UID+1)['stages'],[])

    def test_config_export_and_required_listener_failure_once(self):
        from export_vehicle_ball_contract import build
        self.assertEqual(vehicle.CFG,build())
        calls=[];before=self.state();handlers=list(bus._handlers[Events.VEHICLE_BALL_PROGRESS])
        def fail(*a,**k):calls.append(1);raise TypeError('isolated subscriber')
        with patch.dict(bus._handlers,{Events.VEHICLE_BALL_PROGRESS:handlers+[fail]}):self.assertNotEqual(self.unlock()[0]['result'],0)
        self.assertEqual(calls,[1]);self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main(verbosity=2)
