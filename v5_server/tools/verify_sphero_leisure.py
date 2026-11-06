"""Memory-only BBQ, all20puzzles, collection rewards and native shelf checks."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from verify_sphero_schedule import ScheduleTests
from verify_report_small_fixes import UID
from verify_minigame_point_rewards import ITEM_TYPES
import codec
import generator
from inventory_service import InventoryService
import sphero_schedule_service as schedule
import sphero_leisure_service as leisure
import sphero_battle_service as battle
from event_bus import bus,Events


class LeisureTests(ScheduleTests):
    def setUp(self):
        super().setUp()
        clock=patch.object(leisure,'time',SimpleNamespace(time=lambda:self.now));clock.start();self.addCleanup(clock.stop)
        self.visible=[dict(activity_id=i,state=1,start_time=0,stop_time=4102444800) for i in (242841,242851,242861,242871)]
        visible=patch.object(generator,'get_activity_list',side_effect=lambda *a,**kw:deepcopy(self.visible));visible.start();self.addCleanup(visible.stop)
        for tid,cfg in leisure.TASKS.items():
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                (tid,cfg['type'],cfg['condition'],cfg['need'],cfg['activity_id'],json.dumps(cfg['additional_parameter']),json.dumps(cfg['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,tid,cfg['type']))
        items={i for t in leisure.TASKS.values() for i,n in t['reward']}|{i for v in leisure.CFG['shop'].values() for i in (v['give_id'],v['cost_id'])}
        for iid in items:
            kind=1 if iid<100 else ITEM_TYPES[iid][0]
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type,category) VALUES(?,?,?)',(iid,kind,'currency' if kind==1 else 'material'))
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,));self.conn.resource_version='229'
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('ops_common','users','currency','material','task','claim_ledger','history_cmd','hero','servant','sp_hero_puzzle_progress','store_purchase')}
    def bbq(self,stage=2428603,grade=5,hero=1284,result=1):return self.call(83022,dict(stage_id=stage,grade_id=grade,hero_id=hero,result=result),83023)
    def puzzle(self,stage=2428701,auto=0,result=1,seconds=30):return self.call(83024,dict(stage_id=stage,result=result,auto_completed=auto,useseconds=seconds),83025)
    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']
    def history(self):return {r['id']:r['data'] for r in codec.decode(generator.gen_payload(51001,uid=UID,db=self.db),'sc_51001')['data_list']}
    def next_day(self):self.now=schedule.get_daily_5am_ts(self.now)+86400

    def test_native_contract_three_difficulties_twenty_puzzles_and_forty_tasks(self):
        from export_sphero_contract import build
        self.assertEqual(leisure.CFG,build());self.assertEqual(len(leisure.BBQ),3);self.assertEqual(len(leisure.PUZZLES),20);self.assertEqual(len(leisure.TASKS),40)
        self.assertEqual(len(leisure.CFG['shop']),9)

    def test_all_fifteen_bbq_grades_match_native_reward_configuration(self):
        for stage,cfg in leisure.BBQ.items():
            for grade in range(1,6):
                self.next_day();before=self.balance(53100);ticket=self.balance(54057)
                packet,response=self.bbq(stage,grade);self.assertEqual(packet['result'],0)
                coin=cfg['reward_list_coin'][grade-1][1];raw=cfg['reward_list_ticket'][grade-1];count=raw[1] if raw else 0
                self.assertEqual((self.balance(53100)-before,self.balance(54057)-ticket),(coin,count))
                self.assertEqual((packet['reward_recode'],packet['ticket_recode']),(coin,count));self.assertIn(17023,response.scs)
                self.assertNotIn(83026,response.scs);self.assertNotIn(83016,response.scs)

    def test_bbq_caps_independently_truncate_and_keep_task_play_available(self):
        self.daily([2428407]*4);self.bbq();packet,response=self.bbq()
        self.assertEqual(packet['reward_list'],[dict(id=53100,num=50),dict(id=54057,num=4)])
        self.assertEqual((packet['reward_recode'],packet['ticket_recode']),(200,16))
        for _ in range(18):packet,response=self.bbq()
        self.assertEqual(packet.get('reward_list',[]),[]);self.assertNotIn(17023,response.scs)
        self.assertEqual(self.value(202802),20);self.assertEqual(self.value(202845),20)
        self.assertEqual(sum(s['is_finish'] for s in schedule.read(self.db,UID)['schedules']),4)
        self.assertEqual(leisure.read(self.db,UID)['bbq_count'],20)

    def test_bbq_bad_grade_hero_activity_and_quit_do_not_grant_or_count(self):
        for args in [dict(stage=0),dict(grade=0),dict(grade=6),dict(hero=1084),dict(result=4)]:
            before=self.state();self.assertNotEqual(self.bbq(**args)[0]['result'],0);self.assertEqual(self.state(),before)
        for result in (2,3):
            before=self.state();packet,response=self.bbq(result=result);self.assertEqual(packet['result'],0);self.assertEqual(self.state(),before);self.assertEqual(response.scs,[83023])
        self.visible[2]['state']=0;before=self.state();self.assertNotEqual(self.bbq()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_daily_bbq_caps_reset_without_erasing_lifetime_counts(self):
        self.bbq();self.bbq();self.next_day();data=codec.decode(generator.gen_payload(83026,uid=UID,db=self.db),'sc_83026')
        self.assertEqual(data,dict(reward_record=0,ticket_record=0));self.assertEqual(leisure.read(self.db,UID)['bbq_count'],2)
        self.bbq();self.assertEqual(leisure.read(self.db,UID)['bbq_count'],3)

    def test_puzzle_validates_predecessor_and_requires_new_clear_schedule(self):
        before=self.state();self.assertNotEqual(self.puzzle()[0]['result'],0);self.assertEqual(self.state(),before)
        self.daily([2428406]*4);before=self.state();self.assertNotEqual(self.puzzle(2428702)[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.puzzle();self.assertEqual(packet['result'],0);self.assertEqual(packet['finished_list'],[2428701])
        self.assertEqual(packet['reward_list'],[dict(id=53100,num=60),dict(id=54057,num=2)])
        self.assertIn(83017,response.scs);self.assertIn(17023,response.scs);self.assertNotIn(83021,response.scs)

    def test_puzzle_auto_clear_cost_is_paid_once_and_not_taken_on_cancel(self):
        self.daily([2428406]*4);self.db.set_material(UID,53100,299);before=self.state()
        self.assertNotEqual(self.puzzle(auto=1)[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.set_material(UID,53100,500);packet,_=self.puzzle(auto=1);self.assertEqual(packet['result'],0);self.assertEqual(self.balance(53100),260)
        self.puzzle(auto=1,seconds=25);self.assertEqual(self.balance(53100),260)
        before=self.state();self.puzzle(2428702,auto=1,result=3);self.assertEqual(self.state(),before)

    def test_replay_no_second_reward_or_daily_completion_and_best_time_persists(self):
        self.daily([2428406]*4);self.puzzle();before=self.state();packet,response=self.puzzle(seconds=40)
        self.assertEqual(packet.get('reward_list',[]),[]);self.assertNotIn(83017,response.scs);self.assertEqual(self.state(),before)
        self.puzzle(seconds=10);self.assertEqual(leisure.read(self.db,UID)['times']['2428701'],10)
        self.assertEqual(sum(s['is_finish'] for s in schedule.read(self.db,UID)['schedules']),1)

    def test_all_twenty_puzzles_unlock_tasks_history_and_remove_exhausted_schedule(self):
        for index,stage in enumerate(leisure.PUZZLES):
            if index%4==0:self.next_day();self.daily([2428406]*4)
            packet,_=self.puzzle(stage);self.assertEqual(packet['result'],0,stage)
        self.assertEqual(len(leisure.cleared(self.db,UID)),20);self.assertEqual(self.balance(53100),1200)
        self.assertEqual(self.balance(54057),140);self.assertEqual(self.value(202803),1);self.assertEqual(self.history()[112011],1)
        self.next_day();self.assertNotEqual(self.daily([2428406]*4)[0]['result'],0)

    def test_invalid_puzzle_fields_never_consume_or_unlock(self):
        self.daily([2428406]*4)
        for args in [dict(stage=999),dict(result=4),dict(auto=2)]:
            before=self.state();self.assertNotEqual(self.puzzle(**args)[0]['result'],0);self.assertEqual(self.state(),before)
        self.visible[3]['state']=0;before=self.state();self.assertNotEqual(self.puzzle()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_wreath_grants_immediately_unlock_role_task_and_claim_gives_real_hero(self):
        ctx=self.conn.to_handler_ctx()
        for _ in range(4):InventoryService.grant_items(ctx,UID,[(53101,1)],source='isolated_reward')
        self.assertEqual(self.value(202805),4)
        packet,response=self.call(28010,dict(id=202805),28011);self.assertEqual(packet['result'],0)
        self.assertEqual(self.db.query('SELECT unlock FROM hero WHERE uid=? AND id=1284',(UID,))[0]['unlock'],1)
        self.assertIn(14007,response.scs);self.assertEqual(self.history()[112053],1)
        before=self.state();self.call(28010,dict(id=202805),28011);self.assertEqual(self.state(),before)

    def test_four_native_collection_rewards_chain_into_role_and_wreath_not_auto_claimed(self):
        for tid in [202801,202802,202803,202804]:self.db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(leisure.TASKS[tid]['need'],UID,tid))
        packet,response=self.call(28014,dict(id_list=[202801,202802,202803,202804]),28015);self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(53101),4);self.assertEqual(self.value(202805),4);self.assertEqual(self.history()[112051],1)
        self.assertEqual(self.db.query('SELECT * FROM hero WHERE uid=? AND id=1284',(UID,)),[])
        self.assertEqual(self.call(28010,dict(id=202805),28011)[0]['result'],0)

    def test_owned_task_flags_and_shop_closure_do_not_assume_captured_completion(self):
        values=self.history();self.assertTrue(all(values[i]==0 for i in (112011,112041,112051,112052,112053)))
        for key,cfg in leisure.CFG['shop'].items():
            if int(key)!=216000002:self.db.execute('INSERT INTO store_purchase(uid,shop_id,goods_id,buy_times) VALUES(?,216,?,?)',(UID,int(key),cfg['limit_num']))
        self.assertEqual(self.history()[112041],1)
        self.assertFalse(self.history()[112052]);self.db.execute('UPDATE task SET complete_flag=1,claimed_ts=1 WHERE uid=?',(UID,))
        values=self.history();self.assertTrue(all(values[i]==1 for i in (112041,112051,112052,112053)))

    def test_shop216_native_nine_goods_and_purchases_use_real_currency_and_servant_delta(self):
        from activity_content_service import sync_catalogs
        from shop_service import ShopService
        sync_catalogs(self.db,'229');self.db.set_material(UID,53100,50000)
        svc=ShopService(db=self.db)
        cfg=svc.get_shop_data_payload(UID,'229');shelf=next(r for r in cfg['shop_item_list'] if r['shop_id']==216)
        self.assertEqual(len(shelf['goods_list']),9)
        # Native purchase dispatch validates the permanent shelf and persists its limit.
        packet,response=self.call(20012,dict(shop_id=216,buy_goods_list=[dict(buy_id=216000001,buy_num=1,buy_type=0)]),20013)
        self.assertEqual(packet['result'],0);self.assertEqual(self.balance(53100),34000)
        self.assertEqual(len(self.db.query('SELECT * FROM servant WHERE uid=? AND prefab_id=2540012',(UID,))),1)
        self.assertIn(17023,response.scs)

    def test_cold_recovery_of_bbq_puzzle_coins_wreath_and_entrust_tasks(self):
        self.daily([2428406]*4);self.puzzle();self.bbq()
        sc=schedule.read(self.db,UID);sc['entrust_completed']=100;sc['earned']=12000;schedule.save(self.db,UID,sc)
        state=leisure.read(self.db,UID);state['bbq_count']=20;state['wreath']=4;leisure.save(self.db,UID,state)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,));generator.gen_payload(28001,uid=UID,db=self.db)
        self.assertEqual((self.value(202802),self.value(202805),self.value(202804),self.value(202840)),(20,4,12000,100))
        self.assertEqual(codec.decode(generator.gen_payload(83021,uid=UID,db=self.db),'sc_83021')['stage_completed_list'],[2428701])

    def test_other_inventory_sources_keep_earned_total_after_spending(self):
        ctx=self.conn.to_handler_ctx()
        InventoryService.grant_items(ctx,UID,[(53100,1000)],source='mail')
        self.assertEqual(schedule.read(self.db,UID)['earned'],1000)
        InventoryService.cost_item(ctx,UID,53100,900)
        InventoryService.grant_items(ctx,UID,[(53100,2000)],source='panel')
        self.assertEqual(schedule.read(self.db,UID)['earned'],3000);self.assertEqual(self.value(202827),3000)
        self.assertEqual(self.balance(53100),2100)
        InventoryService.grant_items(ctx,UID,[(53101,4)],source='mail');InventoryService.cost_item(ctx,UID,53101,4)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id=202805',(UID,));leisure.project_tasks(self.db,UID)
        self.assertEqual(self.value(202805),4)

    def test_login_replaces_static_bbq_and_puzzle_and_inserts_missing_packets(self):
        import login,sys,struct
        import new_player_service
        self.daily([2428406]*4);self.puzzle();self.bbq()
        class Policy:
            def __init__(self,*a):pass
            def allows(self,cmd):return cmd in (83016,83017,83021,83026)
            def report(self,log):pass
            def allows_empty_default(self,*a):return True
        for seq,cmd,data in [(1,83021,dict(stage_completed_list=list(leisure.PUZZLES))),(2,83026,dict(reward_record=200,ticket_record=16))]:
            capture=codec.encode('sc_'+str(cmd),data)
            self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic) VALUES(?,?,?,?,0)',(seq,cmd,generator.build_frame(cmd,capture)[:11],capture))
        stubs={
            'new_player_login_policy':SimpleNamespace(LoginPolicy=Policy),
            'birthday_service':SimpleNamespace(ensure_official_mail=lambda *a:False),
            'support_cosmetic_service':SimpleNamespace(ensure_support_cosmetic_mails=lambda *a:[]),
            'paid_entitlement_service':SimpleNamespace(settle=lambda *a,**kw:None),
            'res_version_manager':SimpleNamespace(get_current_version=lambda:'229',get_version_config=lambda v:{}),
            'rogueteam_service':SimpleNamespace(RogueTeamService=SimpleNamespace(get=lambda **kw:SimpleNamespace(get_session_data=lambda uid:None))),
            'trust_service':SimpleNamespace(TrustService=SimpleNamespace(get_instance=lambda:SimpleNamespace(get_displace_limit_payload=lambda *a:dict(limit_list=[]),get_combo_skill_payload=lambda *a:dict(info=dict(skill_list=[]))))),
            'ai_translator':SimpleNamespace(AITranslator=SimpleNamespace(get_instance=lambda **kw:SimpleNamespace(trigger_login_greeting=lambda *a,**kw:None))),
        }
        for missing in (False,True):
            if missing:
                self.db.execute('DELETE FROM login_push');self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic) VALUES(1,10201,?,?,0)',(generator.build_frame(10201,b'')[:11],b''))
            raw=[]
            with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=False),patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
                login.send_login_flood(SimpleNamespace(sendall=raw.append,settimeout=lambda *a:None),self.db,UID,generator,frame_delay=0)
            cmds=[struct.unpack('>H',p[5:7])[0]+(65536 if p[4] else 0) for p in raw]
            for cmd in (83021,83026):self.assertEqual(cmds.count(cmd),1)
            self.assertEqual(codec.decode(raw[cmds.index(83021)][11:],'sc_83021')['stage_completed_list'],[2428701])
            self.assertEqual(codec.decode(raw[cmds.index(83026)][11:],'sc_83026'),dict(reward_record=150,ticket_record=12))

    def test_bad_record_is_not_silently_replaced(self):
        self.db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?)',(UID,leisure.KIND,'242841','{'))
        before=self.state();self.assertNotEqual(self.bbq()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_necessary_bbq_and_puzzle_packets_failure_rolls_back_all_rewards_and_progress(self):
        self.daily([2428406,2428407,2428406,2428407]);ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for target,action in [('sc_83023',self.bbq),('sc_83025',self.puzzle),('sc_83017',self.bbq),('sc_51001',self.puzzle),('sc_28007',lambda:self.bbq(grade=1))]:
            # A5 BBQ count crosses the first task threshold for the28007 check.
            if target=='sc_28007':
                state=leisure.read(self.db,UID);state['bbq_count']=4;leisure.save(self.db,UID,state)
            before=self.state()
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==target and not d.get('result') else encode(n,d)):
                self.assertNotEqual(action()[0]['result'],0)
            self.assertEqual(self.state(),before,target)

    def test_grant_failure_and_inventory_delta_failure_can_retry_without_duplicate(self):
        self.daily([2428406]*4);before=self.state();grant=InventoryService.grant_items
        def fail(*a,**kw):grant(*a,**kw);raise RuntimeError('after mutation')
        with patch.object(InventoryService,'grant_items',side_effect=fail):self.assertNotEqual(self.puzzle()[0]['result'],0)
        self.assertEqual(self.state(),before)
        gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else gen(cmd,**kw)):self.assertNotEqual(self.puzzle()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.puzzle()[0]['result'],0);self.assertEqual(self.balance(53100),60)


if __name__=='__main__':
    suite=unittest.TestSuite(LeisureTests(n) for n in LeisureTests.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
