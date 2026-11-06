"""Osiris Core/UDP probes, with isolated memory saves and native IDs."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID,NOW
from verify_minigame_point_rewards import ITEM_TYPES
import battle_server as wire
import codec
import generator
import hero_codec
import middleware as mw
import new_player_service
import osiris_service as osi
from event_bus import bus,Events
import minigame_point_service as points

ROOT=Path(__file__).resolve().parents[2]
TASKS=json.loads((ROOT/'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']
NORMAL=list(osi.NORMAL)
CHALLENGE=[s for s in osi.STAGES if s not in osi.NORMAL]


class OsirisTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        sessions=patch.dict(mw._BATTLE_SESSIONS,{},clear=True);sessions.start();self.addCleanup(sessions.stop)
        self.activities=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400)
                         for i in [osi.ACTIVITY,osi.CFG['parent_activity'],*osi.CFG['limited_activities']]]
        visible=patch.object(generator,'get_activity_list',side_effect=lambda *a,**k:osi.activity_policy([dict(r) for r in self.activities]))
        visible.start();self.addCleanup(visible.stop);self.activity_patch=visible
        self.native_tasks=[t for t in TASKS.values() if 340001<=t['condition']<=340009 or t['id'] in osi.LIMITED_TASKS]
        for t in self.native_tasks:
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                (t['id'],t['type'],t['condition'],t['need'],t['activity_id'],json.dumps(t['additional_parameter']),json.dumps(t['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,t['id'],t['type']))
        rewards={i for t in self.native_tasks for i,n in t['reward']}|{i for cfg in points.CATALOG.values() if cfg['activity_id']==osi.ACTIVITY for i,n in cfg['reward']}
        for iid in rewards|{1,2,4,14}:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,1 if iid in (1,2,4,14) else ITEM_TYPES[iid][0]))
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1049,100),keys=('uid','id'))
        self.conn.resource_version='229'

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('osiris_stage_progress','osiris_chapter_progress','activity_point','activity_point_reward','task','claim_ledger','users','hero','currency','material','chapter','stage_sub')}

    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']

    def enter(self,stage=NORMAL[0],activity=osi.ACTIVITY,kind=86,team=None):
        team=[(104904,2)] if team is None else team
        packet,response=self.call(54030,dict(common_info=dict(type=kind,dest=stage,activity_id=activity,
                                     hero_list=[dict(hero_id=i,hero_type=k) for i,k in team])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def finish(self,result=1,milliseconds=60000,records=None):
        records={} if records is None else records
        body=wire._f_varint2(1,int(result==1))+wire._f_varint2(2,milliseconds)
        for field,values in [(14,list(records)),(15,list(records.values()))]:
            body+=wire._f_bytes(field,b''.join(wire._varint(v) for v in values))
        raw=wire._f_varint2(1,self.bid)+wire._f_varint2(2,UID)+wire._f_varint2(3,result)+wire._f_bytes(4,body)
        bid,uid,result,info=wire.parse_battle_result(raw)
        self.assertEqual((bid,uid),(self.bid,UID));self.assertNotIn('score',info)
        mw.get_battle_session(bid)['result_data']=dict(result=result,info=info)
        return self.call(54032,dict(battle_id=bid),54033)

    def clear(self,stage=NORMAL[0],**kw):
        self.assertEqual(self.enter(stage)[0]['result'],0)
        packet,response=self.finish(**kw);self.assertEqual(packet['result'],0)
        return packet,response

    def learn_all(self):
        for stage in NORMAL:self.clear(stage)

    def packet(self,response,sc):
        self.assertIn(sc,response.scs)
        return codec.decode(next(f['payload'] for f in response.frames if f['sc']==sc),'sc_'+str(sc))

    def test_empty_snapshot_is_readonly_and_unknown_old_ids_filtered(self):
        before=self.state();self.assertEqual(codec.decode(generator.gen_payload(68171,uid=UID,db=self.db),'sc_68171'),{})
        self.assertEqual(self.state(),before)
        self.db.execute('INSERT INTO osiris_stage_progress(uid,stage_id,score,settle) VALUES(?,999999,60,10000)',(UID,))
        self.assertEqual(osi.payload(self.db,UID),dict(pass_stages=[],pass_chapters=[]))

    def test_entry_checks_native_stage_type_activity_and_prerequisite_chain(self):
        before=self.state()
        for args in [(999999,osi.ACTIVITY,86),(NORMAL[0],1,86),(NORMAL[0],osi.ACTIVITY,1),(NORMAL[1],osi.ACTIVITY,86),(CHALLENGE[0],osi.ACTIVITY,86)]:
            self.assertNotEqual(self.enter(*args)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_initial_trial_and_own_heroes_roles_duplicate_and_unlock(self):
        before=self.state()
        for team in [[],[(101108,2)],[(104904,2),(121102,2)],[(104904,2),(101108,2),(101108,2)],[(104904,2),(107507,2)],[(1084,1)]]:
            self.assertNotEqual(self.enter(team=team)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.enter(team=[(1049,1),(101108,2)])[0]['result'],0)
        self.learn_all()
        self.assertEqual(self.enter(CHALLENGE[0],team=[(104904,2),(107507,2)])[0]['result'],0)

    def test_normal_delta_inserts_pass_list_before_current_and_no_full_snapshot(self):
        _,response=self.clear()
        self.assertEqual(self.packet(response,68173)['pass_stages'],[dict(stage_id=NORMAL[0],pass_time=60000,point=0)])
        self.assertEqual(self.packet(response,60105),dict(score_info_list=[dict(key=osi.ACTIVITY,score=1)]))
        self.assertEqual(self.packet(response,60201),dict(activity_id=osi.ACTIVITY,point=1))
        self.assertLess(response.scs.index(68173),response.scs.index(68177));self.assertLess(response.scs.index(68177),response.scs.index(54033))
        self.assertNotIn(68171,response.scs);self.assertNotIn(17023,response.scs)

    def test_all_eighteen_learned_chapters_six_and_sandbox_not_learning_points(self):
        for i,stage in enumerate(NORMAL,1):
            _,response=self.clear(stage)
            self.assertEqual(self.db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=?',(UID,osi.ACTIVITY))[0]['point'],i)
            if i%3==0:self.assertEqual(self.packet(response,68175),dict(pass_chapters=[i//3]))
            else:self.assertNotIn(68175,response.scs)
        for stage in CHALLENGE:
            _,response=self.clear(stage);self.assertNotIn(60105,response.scs)
        self.assertEqual(len(osi.read(self.db,UID)),24);self.assertEqual(osi.payload(self.db,UID)['pass_chapters'],[1,2,3,4,5,6])
        self.assertEqual(self.db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=?',(UID,osi.ACTIVITY))[0]['point'],18)

    def test_replay_and_loss_exit_never_duplicate_or_unlock(self):
        for outcome in [2,3]:
            self.enter();before=self.state();packet,response=self.finish(result=outcome)
            self.assertEqual(packet['result'],0);self.assertEqual(self.state(),before);self.assertNotIn(68173,response.scs)
        self.clear();before=self.state();self.assertEqual(self.finish()[0]['result'],0);self.assertEqual(self.state(),before)
        _,response=self.clear();self.assertNotIn(68173,response.scs);self.assertNotIn(60105,response.scs)

    def test_no_ordinary_stamina_experience_main_stage_or_hero_growth(self):
        before=self.state();self.clear();after=self.state()
        for table in ['users','hero','currency','chapter','stage_sub']:self.assertEqual(after[table],before[table],table)

    def test_sandbox_private_score_native_config_and_current_vs_best_pair(self):
        self.learn_all();_,response=self.clear(CHALLENGE[0],milliseconds=60000)
        self.assertEqual(self.packet(response,68177)['pass_stage']['point'],1100000)
        self.assertEqual(osi.read(self.db,UID)[CHALLENGE[0]],dict(stage_id=CHALLENGE[0],pass_time=60000,point=1100000))
        _,response=self.clear(CHALLENGE[0],milliseconds=120000)
        self.assertEqual(self.packet(response,68177)['pass_stage']['point'],800000)
        self.assertEqual(osi.read(self.db,UID)[CHALLENGE[0]]['pass_time'],60000)
        self.assertEqual(osi.score(CHALLENGE[0],0),800000)

    def test_native_record_ids_mechanic_tasks_and_exact_aggregate(self):
        self.learn_all()
        for stage in CHALLENGE:
            records={}
            for t in self.native_tasks:
                if 340004<=t['condition']<=340009 and t['additional_parameter'][0]==stage:
                    records[osi.CFG['mechanic_records'][str(t['condition'])]]=t['additional_parameter'][1]
            self.clear(stage,records=records)
        for t in self.native_tasks:self.assertEqual(self.value(t['id']),t['need'],t['id'])
        self.assertEqual(self.value(40103004),18)

    def test_foreign_stage_record_ids_and_optional_missing_data_never_invent_tasks(self):
        self.learn_all();self.clear(5260114,records={94:9999,95:9999})
        self.assertEqual(self.value(40103019),0)
        self.clear(5260114,milliseconds=0,records={93:4});self.assertEqual(self.value(40103019),0)
        self.clear(5260114,records={93:5});self.assertEqual(self.value(40103019),1)

    def test_mechanics_only_winning_single_battle_and_persist_for_recovery(self):
        self.learn_all();self.clear(5260114,result=2,records={93:5});self.assertEqual(self.value(40103019),0)
        self.clear(5260114,records={93:3});self.clear(5260114,records={93:2});self.assertEqual(self.value(40103019),0)
        self.clear(5260114,records={93:5});self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id=40103019',(UID,))
        osi.restore_for_login(self.db,UID);self.assertEqual(self.value(40103019),1)
        self.assertIn(40103019,osi.extra(self.db,UID)[2]);self.assertEqual(osi.read(self.db,UID+1),{})

    def test_required_packets_rollback_before_caching_and_retry(self):
        self.clear(NORMAL[0]);self.clear(NORMAL[1]);self.enter(NORMAL[2]);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for target in ['sc_28007','sc_68173','sc_68175','sc_68177','sc_60105','sc_60201','sc_54033']:
            def fail(name,obj):
                if name==target and (name!='sc_54033' or obj.get('result')==0):return None
                return encode(name,obj)
            with patch.object(ctx,'codec_encode',side_effect=fail):self.assertNotEqual(self.finish()[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.finish()[0]['result'],0)

    def test_required_event_failure_rolls_back(self):
        self.enter();before=self.state();handlers=list(bus._handlers[Events.OSIRIS_STUDY_PROGRESS])
        def fail(*a,**kw):raise RuntimeError('isolated osiris subscription')
        with patch.dict(bus._handlers,{Events.OSIRIS_STUDY_PROGRESS:handlers+[fail]}):self.assertNotEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.finish()[0]['result'],0)

    def test_mechanic_fact_and_user_extra_rollback_when_current_packet_fails(self):
        self.learn_all();self.enter(5260114);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_68177' else encode(n,d)):
            self.assertNotEqual(self.finish(records={93:5})[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.finish(records={93:5})[0]['result'],0)

    def test_resident_filters_limited_but_global_recommendation_and_point_rewards_work(self):
        self.activities=[r for r in self.activities if r['activity_id']!=osi.CFG['parent_activity']]
        self.learn_all();self.clear(5260114,records={93:5})
        self.assertEqual(self.value(60505),1);self.assertTrue(all(self.value(t)==0 for t in osi.LIMITED_TASKS))
        self.assertEqual(self.call(60054,dict(point_reward_id_list=[32380101]),60055)[0]['result'],0)
        self.db.execute('UPDATE task SET progress=999 WHERE uid=?',(UID,));before=self.state()
        self.assertNotEqual(self.call(28010,dict(id=40103005),28011)[0]['result'],0)
        self.assertNotEqual(self.call(28014,dict(id_list=[40103005]),28015)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_cold_new_old_tasks_points_and_chapters_recover_without_assets(self):
        self.learn_all();self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        self.db.execute('DELETE FROM osiris_chapter_progress WHERE uid=?',(UID,));self.db.execute('UPDATE activity_point SET point=0 WHERE uid=?',(UID,))
        generator.gen_payload(28001,uid=UID,db=self.db);self.assertEqual(self.value(60505),1)
        self.assertEqual(osi.payload(self.db,UID)['pass_chapters'],[1,2,3,4,5,6])
        self.assertEqual(self.db.query('SELECT point FROM activity_point WHERE uid=? AND activity_id=?',(UID,osi.ACTIVITY))[0]['point'],18)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=?',(UID,))
        with patch.object(new_player_service,'ensure_tasks'):p=new_player_service.payload(self.db,UID,28001)
        self.assertEqual(next(t['progress'] for t in p['assignment_list'] if t['id']==60505),1);self.assertEqual(self.balance(1),0)

    def test_claims_all_eighteen_native_point_rewards_once(self):
        self.learn_all();ids=[int(i) for i,v in points.CATALOG.items() if v['activity_id']==osi.ACTIVITY]
        self.assertEqual(len(ids),18)
        packet,response=self.call(60054,dict(point_reward_id_list=ids),60055);self.assertEqual(packet['result'],0)
        self.assertEqual(self.packet(response,60107)['reward_info_list'],ids)
        self.assertEqual(osi.point_payload(self.db,UID)['reward_list'],sorted(ids))
        self.assertEqual(self.balance(1),360);self.assertIn(17023,response.scs);before=self.state()
        self.assertNotEqual(self.call(60054,dict(point_reward_id_list=ids),60055)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_shared_reward_delta_failure_rolls_back_claim_and_assets(self):
        self.clear();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_60107' else encode(n,d)):
            self.assertNotEqual(self.call(60054,dict(point_reward_id_list=[32380101]),60055)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call(60054,dict(point_reward_id_list=[32380101]),60055)[0]['result'],0)

    def test_legacy_fake_normal_scores_and_chapter_flags_are_not_authority(self):
        self.db.execute('INSERT INTO osiris_chapter_progress(uid,chapter_no,value) VALUES(?,6,6)',(UID,))
        self.assertEqual(osi.payload(self.db,UID)['pass_chapters'],[])
        self.assertNotEqual(self.enter(CHALLENGE[0])[0]['result'],0)
        self.db.execute('INSERT INTO osiris_stage_progress(uid,stage_id,score,settle) VALUES(?,?,60,10000)',(UID,NORMAL[0]))
        self.assertEqual(osi.read(self.db,UID)[NORMAL[0]]['point'],0)

    def test_actual_resident_activity_context_for_229_and_311(self):
        import res_version_manager as versions
        self.activity_patch.stop()
        for aid in [osi.ACTIVITY,osi.CFG['parent_activity'],*osi.CFG['limited_activities']]:
            self.db.execute('INSERT INTO activity(uid,activity_id,state,start_time,stop_time,template,theme) VALUES(?,?,?,1,?,4,33)',
                            (UID,aid,0 if aid==osi.CFG['parent_activity'] else 1,NOW+86400))
        for version in ['229','311']:
            with versions.connection_version(version):
                rows=generator.get_activity_list(self.db,UID);self.assertFalse(osi.limited_context(rows))
                self.assertEqual({r['state'] for r in rows if r['activity_id'] in osi.CFG['limited_activities']},{0})

    def test_contract_native_export_and_sweep_rejection(self):
        from export_osiris_contract import build
        self.assertEqual(osi.CFG,build());before=self.state()
        with self.assertRaises(ValueError):mw._settle_frames(self.conn.to_handler_ctx(),UID,1,1,NORMAL[0],stage_type=86,activity_id=osi.ACTIVITY,sweep=True)
        self.assertEqual(self.state(),before)

    def test_static_login_capture_replaced_by_current_account(self):
        import verify_minigame_empty_login as flood
        with patch.object(flood,'COMMANDS',(68171,)):
            flood.EmptyLoginTests.flood(self,dynamic=0)

    def test_native_60201_login_owner_is_preserved_and_scoped_row_replaced(self):
        import login
        captured=codec.encode('sc_60201',dict(activity_id=osi.ACTIVITY,point=18,reward_list=[32380101]))
        other=codec.encode('sc_60201',dict(activity_id=3639701,point=100))
        rows=[(1,60201,generator.build_frame(60201,p)[:11],p,0,None) for p in [captured,other,captured]]
        rebuilt=login.osiris_point_frame(rows,generator)
        self.assertEqual(len(rebuilt),2);self.assertEqual(rebuilt[0][3],other)
        self.assertEqual(json.loads(rebuilt[1][5]),dict(activity_id=osi.ACTIVITY))
        self.assertEqual(osi.point_payload(self.db,UID),dict(activity_id=osi.ACTIVITY,point=0,reward_list=[]))

    def test_60201_owner_frame_is_generated_in_actual_login(self):
        import login,struct,sys
        from types import SimpleNamespace
        class Policy:
            def __init__(self,*a):pass
            def allows(self,cmd):return cmd in (68171,60201)
            def report(self,log):pass
            def allows_empty_default(self,*a):return True
        captured=codec.encode('sc_60201',dict(activity_id=osi.ACTIVITY,point=18,reward_list=[32380101]))
        self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic) VALUES(1,60201,?,?,0)',
                        (generator.build_frame(60201,captured)[:11],captured))
        raw=[]
        stubs={
            'new_player_login_policy':SimpleNamespace(LoginPolicy=Policy),
            'birthday_service':SimpleNamespace(ensure_official_mail=lambda *a:False),
            'support_cosmetic_service':SimpleNamespace(ensure_support_cosmetic_mails=lambda *a:[]),
            'paid_entitlement_service':SimpleNamespace(settle=lambda *a,**kw:None),
            'res_version_manager':SimpleNamespace(get_current_version=lambda:'229',get_version_config=lambda v:{}),
            'rogueteam_service':SimpleNamespace(RogueTeamService=SimpleNamespace(get=lambda **kw:SimpleNamespace(get_session_data=lambda uid:None))),
            'trust_service':SimpleNamespace(TrustService=SimpleNamespace(get_instance=lambda:SimpleNamespace(
                get_displace_limit_payload=lambda *a:dict(limit_list=[]),get_combo_skill_payload=lambda *a:dict(info=dict(skill_list=[]))))),
            'ai_translator':SimpleNamespace(AITranslator=SimpleNamespace(get_instance=lambda **kw:SimpleNamespace(trigger_login_greeting=lambda *a,**kw:None))),
        }
        with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=False),patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
            login.send_login_flood(SimpleNamespace(sendall=raw.append,settimeout=lambda t:None),self.db,UID,generator,frame_delay=0)
        packets=[p for p in raw if struct.unpack('>H',p[5:7])[0]+(65536 if p[4] else 0)==60201]
        self.assertEqual(len(packets),1)
        self.assertEqual(codec.decode(packets[0][11:],'sc_60201'),dict(activity_id=osi.ACTIVITY,point=0))


if __name__=='__main__':unittest.main(verbosity=2)
