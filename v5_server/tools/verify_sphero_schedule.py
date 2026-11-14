"""Memory-only SP Hero schedule/entrust protocol and transaction probes."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID,NOW
import codec
import generator
import minigame_service
from inventory_service import InventoryService
import sphero_schedule_service as sp


class ScheduleTests(MemoryTests):
    def setUp(self):
        super().setUp();self.now=NOW
        clock=patch.object(sp,'time',SimpleNamespace(time=lambda:self.now));clock.start();self.addCleanup(clock.stop)
        visible=patch.object(generator,'get_activity_list',return_value=[dict(activity_id=242841,state=1,start_time=0,stop_time=4102444800)])
        visible.start();self.addCleanup(visible.stop)
        for iid,kind in ((1,1),(2,1),(53100,6),(54057,1)):
            self.db.execute('INSERT INTO item_catalog(id,type,category) VALUES(?,?,?)',(iid,kind,'currency' if kind==1 else 'material'))
        self.db.set_currency(UID,2,100000);self.db.set_currency(UID,1,1000);self.db.set_currency(UID,54057,100)
        self.set_state(pool=[2428404]*5)

    def set_state(self,**kwargs):
        state=sp.read(self.db,UID);state.update(kwargs);sp.save(self.db,UID,state);return state
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('ops_common','users','currency','material','task','history_cmd','sp_hero_challenge_schedule')}
    def op(self,cmd,**data):return self.call(cmd,dict(activity_id=242841,**data),cmd+1)
    def dispatch(self,index=1,eid=2428404):return self.op(83009,index=index,entrust_id=eid)
    def claim(self,indices=[1]):return self.op(83013,index_list=indices)
    def daily(self,ids=[2428408]*4):
        return self.op(83003,schedule_info_list=[dict(index=i+1,schedule_id=s,is_finish=False) for i,s in enumerate(ids)])
    def packets(self,response,sc):return [codec.decode(f['payload'],'sc_'+str(sc)) for f in response.frames if f['sc']==sc]

    def test_config_is_exact_native_export(self):
        from export_sphero_contract import build
        self.assertEqual(sp.CFG,build());self.assertEqual(len(sp.ENTRUSTS),8)
        self.assertEqual(sp.CFG['activity']['daily_award'],[[53100,80]])
        self.assertEqual(sp.CFG['activity']['refresh_cost'],[[1,30]])

    def test_schedule_and_entrust_are_independent_and_legacy_is_preserved(self):
        self.db.execute('INSERT INTO sp_hero_challenge_schedule(uid,activity_id,schedule_type,schedule_id,ts) VALUES(?,242841,1,2428404,123)',(UID,))
        legacy=self.db.query('SELECT * FROM sp_hero_challenge_schedule');self.daily();self.dispatch()
        state=sp.read(self.db,UID);self.assertEqual(len(state['schedules']),4);self.assertEqual(len(state['entrusts']),1)
        self.assertEqual(state['schedules'][0]['schedule_id'],2428408)
        self.assertEqual(self.db.query('SELECT * FROM sp_hero_challenge_schedule'),legacy)
        cold=codec.decode(generator.gen_payload(83016,uid=UID,db=self.db),'sc_83016')
        self.assertEqual(cold['begin_entrust_list'],[dict(index=1,entrust_id=2428404,start_time=NOW)])
        self.assertEqual(len(codec.decode(generator.gen_payload(83017,uid=UID,db=self.db),'sc_83017')['schedule_list']),4)

    def test_no_capture_progress_bleeds_into_empty_owner_even_original_uid(self):
        self.db.execute('INSERT INTO users(uid,nick) VALUES(2174928301,?)',('旧主账号',))
        self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,payload_json) VALUES(1,83016,?,?,?)',
            (b'',b'',json.dumps(dict(passed_chapter_level_list=[5170206],begin_entrust_list=[dict(index=1,entrust_id=2428401,start_time=1)]))))
        raw=codec.decode(generator.gen_payload(83016,uid=2174928301,db=self.db),'sc_83016')
        self.assertEqual(raw.get('passed_chapter_level_list',[]),[]);self.assertEqual(raw.get('begin_entrust_list',[]),[])
        self.assertTrue(raw['entrust_id_list']);self.assertEqual(raw['challenge_times'],0)

    def test_native_dispatch_cost_consumes_one_candidate_and_emits_atomic_delta(self):
        packet,response=self.dispatch();self.assertEqual(packet['result'],0);self.assertEqual(self.balance(2),99200)
        self.assertEqual(sp.read(self.db,UID)['pool'],[2428404]*4)
        self.assertIn(17023,response.scs);self.assertNotIn(83016,response.scs)
        self.assertNotIn(83017,response.scs)

    def test_dispatch_rejects_bad_slots_absent_candidates_insufficient_cost_and_busy(self):
        for index,eid in [(0,2428404),(4,2428404),(1,2428401),(1,999)]:
            before=self.state();self.assertNotEqual(self.dispatch(index,eid)[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.set_currency(UID,2,799);before=self.state();self.assertNotEqual(self.dispatch()[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.set_currency(UID,2,10000);self.dispatch();before=self.state();self.assertNotEqual(self.dispatch()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_time_guard_config_rewards_and_duplicate_claim(self):
        self.dispatch();before=self.state();self.assertNotEqual(self.claim()[0]['result'],0);self.assertEqual(self.state(),before)
        self.now+=480*60;packet,response=self.claim()
        self.assertEqual(packet['reward_list'],[dict(id=53100,num=90)]);self.assertEqual(self.balance(53100),90)
        self.assertIn(17023,response.scs);self.assertIn(51001,response.scs);self.assertNotIn(83016,response.scs)
        before=self.state();self.assertNotEqual(self.claim()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_batch_claim_is_all_or_nothing_and_sums_real_rewards(self):
        self.dispatch(1);self.dispatch(2);self.now+=480*60
        for ids in ([],[1,1],[1,3]):
            before=self.state();self.assertNotEqual(self.claim(ids)[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.claim([1,2]);self.assertEqual(packet['reward_list'],[dict(id=53100,num=180)])
        self.assertEqual(response.scs.count(17023),1);self.assertEqual(sp.read(self.db,UID)['entrusts'],[])

    def test_acceleration_consumes_correct_item_and_hour_not_entire_job(self):
        self.dispatch();packet,response=self.op(83018,index=1,use_cnt=2);self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(54057),98);self.assertEqual(packet['begin_entrust_list'][0]['start_time'],NOW-7200)
        self.assertIn(17023,response.scs)
        for cnt in (0,7,999):
            before=self.state();self.assertNotEqual(self.op(83018,index=1,use_cnt=cnt)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.op(83018,index=1,use_cnt=6)[0]['result'],0);self.assertEqual(self.claim()[0]['result'],0)

    def test_completed_or_waiting_jobs_cannot_consume_accelerators(self):
        self.set_state(earned=500);self.dispatch(1);self.dispatch(2);self.dispatch(3);self.dispatch(4)
        before=self.state();self.assertNotEqual(self.op(83018,index=4,use_cnt=1)[0]['result'],0);self.assertEqual(self.state(),before)
        self.now+=480*60;before=self.state();self.assertNotEqual(self.op(83018,index=1,use_cnt=1)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_cancel_does_not_refund_or_create_reward(self):
        self.dispatch();packet,response=self.op(83011,index=1);self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(2),99200);self.assertEqual(self.balance(53100),0);self.assertNotIn(17023,response.scs)
        self.assertEqual(sp.read(self.db,UID)['pool'],[2428404]*4);self.assertEqual(sp.read(self.db,UID)['entrusts'],[])

    def test_queue_unlocks_from_durable_earned_total_and_promotes_on_refresh(self):
        self.set_state(earned=2000)
        for i in range(1,6):self.assertEqual(self.dispatch(i)[0]['result'],0)
        self.set_state(pool=[2428404]);self.assertEqual(self.dispatch(6)[0]['result'],0)
        self.assertEqual([e['start_time'] for e in sp.read(self.db,UID)['entrusts'] if e['index']>3],[0,0,0])
        self.now+=480*60;packet,_=self.op(83040);entries=packet['begin_entrust_list']
        self.assertEqual(len({e['index'] for e in entries}),6)
        self.assertTrue(all(e['start_time']==NOW+480*60 for e in entries if e['index']<=3))
        self.assertTrue(all(e['start_time']==NOW for e in entries if e['index']>3))
        self.assertEqual(self.claim([4,5,6])[0]['reward_list'],[dict(id=53100,num=270)])

    def test_offline_promotion_uses_completion_time_and_cancel_frees_active_slot(self):
        self.set_state(earned=500)
        for i in range(1,5):self.dispatch(i)
        self.now+=16*3600;sp.snapshot(self.db,UID)
        self.assertEqual(sp.read(self.db,UID)['entrusts'][-1]['start_time'],NOW+8*3600)
        self.assertEqual(self.claim([1,2,3,4])[0]['reward_list'],[dict(id=53100,num=360)])
        self.set_state(pool=[2428404]*5)
        for i in range(1,5):self.dispatch(i)
        self.op(83011,index=2);queue=sp.read(self.db,UID)['entrusts'];self.assertEqual(len({e['index'] for e in queue}),3)
        self.assertTrue(all(e['start_time'] for e in queue))

    def test_refresh_free_paid_limit_and_pool_survives_relogin(self):
        for i in range(4):
            packet,response=self.op(83007);self.assertEqual(packet['result'],0);self.assertEqual(len(packet['entrust_id_list']),5)
            self.assertEqual(self.balance(1),1000-max(0,i-1)*30)
            cold=sp.snapshot(self.db,UID);self.assertEqual(cold['entrust_id_list'],packet['entrust_id_list']);self.assertEqual(cold['entrust_refresh_times'],i+1)
        before=self.state();self.assertNotEqual(self.op(83007)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_empty_consumed_pool_never_gets_free_refill_on_relogin(self):
        self.set_state(pool=[2428404]);self.dispatch();self.op(83011,index=1)
        self.assertEqual(sp.snapshot(self.db,UID)['entrust_id_list'],[])
        before=self.state();self.assertNotEqual(self.dispatch()[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.op(83007)[0]['result'],0)

    def test_static_or_missing_login_frames_use_dynamic_state_in_native_order(self):
        import login,sys,struct
        import new_player_service
        class Policy:
            def __init__(self,*a):pass
            def allows(self,cmd):return cmd in (83016,83017)
            def report(self,log):pass
            def allows_empty_default(self,*a):return True
        self.daily();self.dispatch()
        # Deliberately put the old static schedule BEFORE its activity.
        for seq,cmd,data in [(1,83017,dict(activity_id=242841,schedule_list=[])),(2,83016,dict(activity_id=242841,entrust_refresh_times=99,challenge_times=99))]:
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
                self.db.execute('DELETE FROM login_push')
                self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic) VALUES(1,10201,?,?,0)',
                    (generator.build_frame(10201,b'')[:11],b''))
            raw=[]
            with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=False),patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
                login.send_login_flood(SimpleNamespace(sendall=raw.append,settimeout=lambda *a:None),self.db,UID,generator,frame_delay=0)
            cmds=[struct.unpack('>H',p[5:7])[0]+(65536 if p[4] else 0) for p in raw]
            self.assertEqual([c for c in cmds if c in (83016,83017)],[83016,83017])
            p=codec.decode(raw[cmds.index(83016)][11:],'sc_83016');self.assertEqual(p['entrust_refresh_times'],0)
            p=codec.decode(raw[cmds.index(83017)][11:],'sc_83017');self.assertTrue(p['schedule_list'][0]['is_finish'])

    def test_only_dispatch_completes_one_matching_schedule_and_returns_delta(self):
        self.daily();_,response=self.dispatch();delta=self.packets(response,83017)
        self.assertEqual(delta,[dict(activity_id=242841,schedule_list=[dict(index=1,schedule_id=2428408,is_finish=True)])])
        self.op(83011,index=1);self.assertEqual(sum(s['is_finish'] for s in sp.read(self.db,UID)['schedules']),1)
        self.assertNotEqual(self.daily()[0]['result'],0)
        self.assertEqual(self.op(83003,schedule_info_list=[dict(index=2,schedule_id=2428407,is_finish=False)])[0]['result'],0)

    def test_daily_award_requires_real_score_grants_and_claims_once(self):
        self.daily();before=self.state();self.assertNotEqual(self.op(83005)[0]['result'],0);self.assertEqual(self.state(),before)
        for _ in range(4):self.dispatch();self.op(83011,index=1)
        packet,response=self.op(83005);self.assertEqual(packet['reward_list'],[dict(id=53100,num=80)])
        self.assertEqual(self.balance(53100),80);self.assertEqual(self.balance(1),1000)
        self.assertIn(17023,response.scs);before=self.state();self.assertNotEqual(self.op(83005)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_next_day_resets_daily_fields_but_keeps_running_entrust_and_earned(self):
        self.daily();self.dispatch();old=sp.read(self.db,UID);self.set_state(refresh=4,claimed=True,earned=500)
        self.now=sp.get_daily_5am_ts(self.now)+86400
        state=sp.read(self.db,UID);self.assertEqual(state['schedules'],[]);self.assertEqual(state['refresh'],0);self.assertFalse(state['claimed'])
        self.assertEqual(state['entrusts'],old['entrusts']);self.assertEqual(state['earned'],500)
        self.assertEqual(self.op(83007)[0]['result'],0)

    def test_login_history_uses_real_earned_total_not_recorded_capture(self):
        captured=dict(data_list=[dict(id=112061,data=999999),dict(id=990001,data=7)])
        self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,payload_json) VALUES(1,51001,?,?,?)',(b'',b'',json.dumps(captured)))
        def history():return {r['id']:r['data'] for r in codec.decode(generator.gen_payload(51001,uid=UID,db=self.db),'sc_51001')['data_list']}
        values=history();self.assertEqual(values[112061],0);self.assertEqual(values[990001],7)
        self.dispatch();self.now+=480*60;self.claim()
        values=history();self.assertTrue(all(values[i]==90 for i in (112061,112062,112063)))
        self.assertEqual(values[990001],7);self.assertEqual(sp.max_slots(self.db,UID,sp.read(self.db,UID)),3)

    def test_legacy_history_wrapper_preserves_login_and_future_grants(self):
        import new_player_service

        legacy = json.dumps({'data': 22260, 'name': '累计获得「墨兰德流行纪念品」数量（item 53100）'}, ensure_ascii=False)
        for cid, value in ((112061, legacy), (112062, '12'), (112063, '{"data":true}')):
            self.db.execute("INSERT INTO history_cmd(uid,kind,key_id,data) VALUES(?,'history',?,?)",
                            (UID, str(cid), value))
        self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter) '
                        'VALUES(202826,1,302,1000,240046,?)', ('[53100]',))
        self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,202826,1)', (UID,))
        before = self.db.query('SELECT * FROM history_cmd ORDER BY key_id')

        with patch.object(new_player_service, 'enabled', return_value=False):
            tasks = codec.decode(generator.gen_payload(28001, uid=UID, db=self.db), 'sc_28001')
        self.assertEqual(next(row['progress'] for row in tasks['assignment_list'] if row['id'] == 202826), 1000)

        def history():
            payload = codec.decode(generator.gen_payload(51001, uid=UID, db=self.db), 'sc_51001')
            return {row['id']: row['data'] for row in payload['data_list'] if row['id'] in (112061, 112062, 112063)}

        self.assertEqual(history(), dict.fromkeys((112061, 112062, 112063), 22260))
        self.assertEqual(self.db.query('SELECT * FROM history_cmd ORDER BY key_id'), before)
        self.assertEqual(sp.max_slots(self.db, UID, sp.read(self.db, UID)), 6)

        self.dispatch()
        self.now += 480 * 60
        self.assertEqual(self.claim()[0]['reward_list'], [dict(id=53100, num=90)])
        self.assertEqual(history(), dict.fromkeys((112061, 112062, 112063), 22350))

    def test_locked_training_and_forged_finished_schedule_are_rejected(self):
        for entries in ([dict(index=1,schedule_id=2428402,is_finish=False)],
                        [dict(index=1,schedule_id=2428408,is_finish=True)],
                        [dict(index=1,schedule_id=999,is_finish=False)],
                        [dict(index=1,schedule_id=2428408,is_finish=False)]*2):
            before=self.state();self.assertNotEqual(self.op(83003,schedule_info_list=entries)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_wrong_activity_and_closed_activity_never_mutate(self):
        before=self.state();self.assertNotEqual(self.call(83009,dict(activity_id=242851,index=1,entrust_id=2428404),83010)[0]['result'],0);self.assertEqual(self.state(),before)
        with patch.object(generator,'get_activity_list',return_value=[]):
            self.assertNotEqual(self.dispatch()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_dispatch_and_claim_packet_failure_rolls_back_inventory_state_and_frames(self):
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for name,action in [('sc_83010',self.dispatch),('sc_83017',self.dispatch)]:
            if name=='sc_83017':self.daily();action=self.dispatch
            before=self.state()
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==name and not d.get('result') else encode(n,d)):
                self.assertNotEqual(action()[0]['result'],0)
            self.assertEqual(self.state(),before,name)
        before=self.state();gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else gen(cmd,**kw)):
            self.assertNotEqual(self.dispatch()[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.dispatch();self.now+=480*60;before=self.state()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_83014' and not d.get('result') else encode(n,d)):
            self.assertNotEqual(self.claim()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.claim()[0]['result'],0)

    def test_grant_failure_rolls_back_no_ghost_delta(self):
        self.dispatch();self.now+=480*60;before=self.state();grant=InventoryService.grant_items
        def fail(*a,**kw):grant(*a,**kw);raise RuntimeError('grant failed after write')
        with patch.object(InventoryService,'grant_items',side_effect=fail):
            packet,response=self.claim();self.assertNotEqual(packet['result'],0);self.assertNotIn(51001,response.scs);self.assertNotIn(17023,response.scs)
        self.assertEqual(self.state(),before);self.assertEqual(self.claim()[0]['result'],0)


if __name__=='__main__':unittest.main(verbosity=2)
