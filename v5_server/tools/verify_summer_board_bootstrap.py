"""Memory-only native Summer ChessBoard phase-one Core probes."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID,NOW
import codec
import generator
import summer_board_service as board


class BoardTests(MemoryTests):
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ['users','currency','material','task','claim_ledger','chapter','stage_sub']}

    def enter(self,chapter=4080101):return self.call(85000,dict(chapter_id=chapter),85001)
    def roll(self):return self.call(85012,{},85013)
    def exit(self):return self.call(85028,{},85029)
    def record(self):return board.read(self.db,UID)[1]
    def packets(self,response,sc):return [codec.decode(f['payload'],'sc_'+str(sc)) for f in response.frames if f['sc']==sc]

    def test_empty_status_readonly_and_no_capture_fallback(self):
        before=self.state();self.assertEqual(codec.decode(generator.gen_payload(85035,uid=UID,db=self.db),'sc_85035'),dict(chapter_id=0))
        self.assertEqual(self.state(),before)

    def test_native_initial_map_required_player_chance_position_and_no_npc(self):
        packet,response=self.enter();self.assertEqual(packet['result'],0);m=packet['map'];h=m['hero'][0]
        self.assertEqual(h['id'],0);self.assertEqual(h['pos'],dict(x=3,z=7));self.assertEqual(h['direction'],5)
        self.assertEqual(h['chance'],dict(chance_id=0,is_unlock=0));self.assertEqual(board.attribute(h,6),0)
        self.assertEqual(board.attribute(h,10),0);self.assertEqual(len(m['hero']),1)
        self.assertNotIn(85035,response.scs);self.assertEqual(response.scs[-1],85001)

    def test_native_events_before_ack_with_nested_buff_and_actor_zero(self):
        _,response=self.enter();events=self.packets(response,85031)
        self.assertEqual(len(events),2);first=events[0]['event'];child=events[1]['event']
        self.assertEqual((first['trigger_type'],first['event_pool_id'],first['trigger_player_a'],first['trigger_player_b']),(1,1100,0,-1))
        self.assertEqual([r['event'][0]['integer'] for r in first['event_list']],[20002,10001,10012,20005])
        self.assertEqual((child['trigger_type'],child['trigger_type_param'],child['event_pool_id']),(16,[30001],12))
        self.assertEqual(response.scs,[85031,85031,85001])

    def test_durable_bootstrap_has_effects_once_resume_no_repeat(self):
        self.enter();first=self.record();hero=board.player(first['active']['map'])
        self.assertEqual(board.attribute(hero,6),1);self.assertEqual(board.attribute(hero,10),3);self.assertEqual(board.attribute(hero,3),2)
        self.assertEqual(first['active']['map']['task'],[dict(id=10101,progress=0)])
        self.assertEqual(hero['buff'],[dict(id=30001,duration=0,buff_effect=[])])
        _,response=self.enter();self.assertEqual(response.scs,[85001]);self.assertEqual(self.record(),first)

    def test_fixed_tutorial_roll_and_resume_move_budget_not_rerolled(self):
        self.enter();packet,response=self.roll();self.assertEqual(packet,dict(result=0,dice_list=[2]))
        h=board.player(self.record()['active']['map']);self.assertEqual(board.attribute(h,5),2);self.assertEqual(board.attribute(h,8),1)
        self.assertEqual(response.scs,[85013]);before=self.state();self.assertNotEqual(self.roll()[0]['result'],0);self.assertEqual(self.state(),before)
        packet,_=self.enter();self.assertEqual(board.attribute(packet['map']['hero'][0],5),2)

    def test_unknown_locked_and_cross_map_requests_do_not_overwrite(self):
        before=self.state()
        for chapter in [0,9999,4080102,4080502]:
            self.assertNotEqual(self.enter(chapter)[0]['result'],0);self.assertEqual(self.state(),before)
        self.enter();before=self.state()
        self.assertNotEqual(self.enter(4080201)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_abandon_clears_active_only_no_clear_reward_or_history(self):
        self.enter();before=self.state();packet,response=self.exit();self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs,[85029]);self.assertEqual(self.record()['completed'],[]);self.assertIsNone(self.record()['active'])
        after=self.state()
        for t in before.keys()-{'users'}:self.assertEqual(before[t],after[t],t)
        self.assertEqual(board.system_payload(self.db,UID),dict(chapter_id=0));self.assertNotEqual(self.enter(4080102)[0]['result'],0)
        _,response=self.enter();self.assertEqual(len(self.packets(response,85031)),2)

    def test_exit_retains_finished_history_and_unrelated_account_extra(self):
        extra=dict(other_feature=dict(value='保留'),summer_board_v1=dict(version=1,completed=[4080101],active=None))
        self.db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra),UID))
        self.enter(4080102);self.exit();restored=json.loads(self.db.get('users',UID)['extra'])
        self.assertEqual(restored['other_feature'],dict(value='保留'));self.assertEqual(self.record()['completed'],[4080101])

    def test_all_thirteen_bootstraps_native_tasks_buff_npc_and_camera_contract(self):
        for chapter in board.LEVELS:
            extra,state=board.read(self.db,UID);state['completed']=list(board.LEVELS);state['active']=None;board.save(self.db,UID,extra,state)
            packet,response=self.enter(chapter);self.assertEqual(packet['result'],0,chapter)
            saved=self.record()['active']['map'];self.assertTrue(saved['task']);self.assertEqual(board.attribute(board.player(saved),6),1)
            wire_events=self.packets(response,85031)
            expected=board.CFG['events'][str(board.LEVELS[chapter]['eventpool_id'])]['player_params']
            self.assertEqual([e['event'][0]['integer'] for e in wire_events[0]['event']['event_list']],[r[0] for r in expected])
            npc_events=[e for e in wire_events[0]['event']['event_list'] if e['event'][0]['integer']==10002]
            self.assertEqual([e['params'][0] for e in npc_events],[h['id'] for h in saved['hero'] if h['id']])
            _,response=self.enter(chapter);self.assertEqual(response.scs,[85001]);self.assertEqual(self.packets(response,85001)[0]['map']['hero'][0]['chance']['chance_id'],0)

    def test_random_dice_ranges_two_dice_and_state_guard(self):
        self.enter();extra,state=board.read(self.db,UID);h=board.player(state['active']['map'])
        board.set_attribute(h,3,0);board.set_attribute(h,2,2);board.set_attribute(h,1,3);board.save(self.db,UID,extra,state)
        with patch.object(board.random,'randint',side_effect=[1,3]) as rnd:
            self.assertEqual(self.roll()[0],dict(result=0,dice_list=[1,3]));self.assertEqual(rnd.call_count,2)
        self.assertEqual(board.attribute(board.player(self.record()['active']['map']),5),4)

    def test_entry_required_packets_failure_rolls_back_and_retry(self):
        before=self.state();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        for target in ['sc_85001','sc_85031']:
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==target and d.get('result',0)==0 else enc(n,d)):
                self.assertNotEqual(self.enter()[0]['result'],0)
            self.assertEqual(self.state(),before,target)
        self.assertEqual(self.enter()[0]['result'],0)

    def test_roll_and_exit_ack_failure_roll_back(self):
        self.enter();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        for sc,action in [(85013,self.roll),(85029,self.exit)]:
            before=self.state()
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_'+str(sc) and d.get('result')==0 else enc(n,d)):
                self.assertNotEqual(action()[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_bad_json_or_run_structure_never_reset_or_overwrite(self):
        self.db.execute("UPDATE users SET extra='{' WHERE uid=?",(UID,));before=self.state()
        self.assertNotEqual(self.enter()[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.execute("UPDATE users SET extra='{}' WHERE uid=?",(UID,));self.enter()
        good=self.db.get('users',UID)['extra']
        for change in [lambda s:s['active'].update(chapter_id=999),lambda s:s['active']['map'].update(hero=[]),lambda s:s.update(completed=[999])]:
            extra=json.loads(good);change(extra[board.STATE_KEY]);self.db.execute('UPDATE users SET extra=? WHERE uid=?',(json.dumps(extra),UID));before=self.state()
            self.assertNotEqual(self.roll()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_unimplemented_mutations_no_longer_ack_success(self):
        self.enter();before=self.state()
        for cmd,data in [(85010,dict(next_pos=dict(x=999,z=999))),(85014,{}),(85016,dict(prop_id=10001)),(85018,dict(prop_id=10001,chance_id=1)),
                         (85020,dict(chance_id=1)),(85022,dict(chance_id=1,result=1,score=99999)),(85024,dict(prop_id=10001)),
                         (85026,dict(task_id=10101)),(85036,dict(prop_id=10001)),(85038,{})]:
            self.assertNotEqual(self.call(cmd,data,cmd+1)[0]['result'],0,cmd);self.assertEqual(self.state(),before)

    def test_unrelated_accounts_and_login_status_use_own_run(self):
        self.enter();self.db.execute("INSERT INTO users(uid,nick) VALUES(43,'另一个')")
        self.assertEqual(board.system_payload(self.db,UID),dict(chapter_id=4080101));self.assertEqual(board.system_payload(self.db,43),dict(chapter_id=0))
        self.assertEqual(codec.decode(generator.gen_payload(85035,uid=43,db=self.db),'sc_85035'),dict(chapter_id=0))

    def test_map_schema_is_scoped_without_changing_other_chess_families(self):
        import decode_schema as ds
        codec._ensure_schema()
        self.assertEqual(ds.SCHEMA['sc_85001'][2]['ref'],'p85_chess_map_net_rec')
        self.assertEqual(ds.SCHEMA['sc_49001'][1]['ref'],'p49_chess_map_net_rec')
        self.assertEqual(ds.SCHEMA['chess_map_net_rec'][1]['name'],'pos')
        original=dict(activity_id=123,chapter=456,chapter_info=[])
        self.assertEqual(codec.decode(codec.encode('p49_chess_map_net_rec',original),'p49_chess_map_net_rec'),dict(activity_id=123,chapter=456))
        original=dict(pos=dict(x=-3,z=7),hero=[dict(id=1,temp_id=1084,hp_ratio=10000)],direction=2)
        self.assertEqual(codec.decode(codec.encode('chess_map_net_rec',original),'chess_map_net_rec'),original)

    def test_readonly_native_export_and_original_table_contract(self):
        from export_summer_board_contract import build
        self.assertEqual(board.CFG,build());self.assertEqual(len(board.LEVELS),13)
        self.assertEqual(board.LEVELS[4080102]['unlock_condition'],1150101)
        self.assertEqual(board.CFG['conditions']['1150101']['params'],[4080101])


    def test_static_login_status_is_replaced_with_current_run(self):
        self.enter()
        import login,sys,struct
        from types import SimpleNamespace
        import new_player_service
        class Policy:
            def __init__(self,*a):pass
            def allows(self,cmd):return cmd==85035
            def report(self,log):pass
            def allows_empty_default(self,*a):return True
        raw=[];calls=[]
        class Generator:
            build_frame=staticmethod(generator.build_frame)
            @staticmethod
            def gen_payload(cmd,**kw):
                calls.append(cmd);return generator.gen_payload(cmd,**kw)
        capture=codec.encode('sc_85035',dict(chapter_id=4080502))
        self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic) VALUES(1,85035,?,?,0)',
                        (generator.build_frame(85035,capture)[:11],capture))
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
            login.send_login_flood(SimpleNamespace(sendall=raw.append,settimeout=lambda *a:None),self.db,UID,Generator(),frame_delay=0)
        packets=[p for p in raw if struct.unpack('>H',p[5:7])[0]+(65536 if p[4] else 0)==85035]
        self.assertEqual(len(packets),1);self.assertEqual(calls.count(85035),1)
        self.assertEqual(codec.decode(packets[0][11:],'sc_85035'),dict(chapter_id=4080101))


if __name__=='__main__':unittest.main(verbosity=2)
