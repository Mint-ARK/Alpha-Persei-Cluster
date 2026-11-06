"""Actual serialized login flood; Rogue source/codec retained, other modules isolated."""
import json
import struct
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zlib

from verify_report_small_fixes import MemoryTests,UID,NOW
import autochess_login
import codec
import generator
import hero_codec
import login
import new_player_service
import rogueteam_flow as flow
import rogueteam_login as recovery
import rogueteam_service as rogue
import schema_default


class RogueLoginTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.svc=rogue.RogueTeamService.get(db=self.db)
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        self.db.upsert('rogueteam_user',UID,dict(template_id=100001,max_difficult=4),keys=('uid','template_id'))
        self.raw=[]

    def saved(self):return self.db.get('rogueteam_session',UID,'AND template_id=?',(100001,))
    def set(self,**kw):self.db.upsert('rogueteam_session',UID,dict(template_id=100001,**kw),keys=('uid','template_id'))
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('rogueteam_user','rogueteam_session','rogueteam_history','rogueteam_illustrated',
                 'users','currency','material','hero','chapter','stage_sub','task','claim_ledger')}

    def start(self):
        self.assertEqual(self.svc.start_run(UID,100001,1,[dict(hero_id=1084)],[])[0],0)
        nodes=[dict(node_id=101,col=1,row=1,node_type=1,state=0,param=4101101,next_id_list=[201]),
               dict(node_id=201,col=2,row=1,node_type=1,state=2,param=4101102,next_id_list=[])]
        self.set(map_info_json=json.dumps(nodes),select_node_id=0,other_info_json='{}',shop_info_json='{}')
        self.assertEqual(self.svc.select_node(UID,101)[0],0)

    def flood(self,*,fresh=False,captured=True,fail_bond=None,deny=None):
        rows=[]
        if captured:
            for i,cmd in enumerate(tuple(recovery.OWNED_COMMANDS)*2):
                rows.append((i+1,cmd,generator.build_frame(cmd,b'')[:11],b'invalid-old-run',0,None))
        rows.append((192,10201,generator.build_frame(10201,b'')[:11],schema_default.build('sc_10201'),0,None))
        class Policy:
            def __init__(self,*args):pass
            def allows(self,cmd):return (cmd in recovery.OWNED_COMMANDS or cmd==10201) and cmd!=deny
            def report(self,log):pass
            def allows_empty_default(self,*args):return True
        stubs={
            'new_player_login_policy':NS(LoginPolicy=Policy),
            'birthday_service':NS(ensure_official_mail=lambda *a:False),
            'support_cosmetic_service':NS(ensure_support_cosmetic_mails=lambda *a:[]),
            'paid_entitlement_service':NS(settle=lambda *a,**kw:None),
            'res_version_manager':NS(get_current_version=lambda:'229',get_version_config=lambda v:{}),
            'trust_service':NS(TrustService=NS(get_instance=lambda:NS(
                get_displace_limit_payload=lambda *a:dict(limit_list=[]),
                get_combo_skill_payload=lambda *a:dict(info=dict(skill_list=[]))))),
            'ai_translator':NS(AITranslator=NS(get_instance=lambda **kw:NS(trigger_login_greeting=lambda *a,**kw:None))),
        }
        def generate(cmd,**kw):
            if cmd==88315:
                if fail_bond=='raise':raise ValueError('isolated bond generator fault')
                if fail_bond=='none':return None
                if fail_bond=='empty':return b''
                return generator.gen_payload(cmd,**kw)
            self.assertNotIn(cmd,recovery.OWNED_COMMANDS,'must retain pre-encoded authoritative snapshot')
            return schema_default.build('sc_'+str(cmd))
        facade=NS(build_frame=generator.build_frame,gen_payload=generate)
        self.raw=[];logs=[]
        with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=fresh),\
             patch.object(login,'load_login_push',return_value=rows),\
             patch.object(login,'reconcile_oath_rights_before_login',return_value=False),\
             patch.object(autochess_login,'prepare_login_frames',side_effect=lambda frames,*a,**kw:(frames,{})):
            login.send_login_flood(NS(sendall=self.raw.append,settimeout=lambda _:None),self.db,UID,facade,
                                   frame_delay=0,log=lambda *a:logs.append(a))
        packets=[]
        for wire in self.raw:
            cmd=struct.unpack('>H',wire[5:7])[0]+(65536 if wire[4] else 0)
            if cmd in recovery.OWNED_COMMANDS or cmd==10201:
                body=zlib.decompress(wire[11:]) if wire[2] else wire[11:]
                packets.append((cmd,codec.decode(body,'sc_'+str(cmd)),body))
        return packets

    def assert_sequence(self,packets,pending=(),active=True):
        self.assertEqual([cmd for cmd,obj,raw in packets],
                         [88315,88211]+([88001,88013,88019,*pending] if active else [])+[10201])
        self.assertEqual(sum(cmd==88315 for cmd,obj,raw in packets),1)
        self.assertEqual(next(obj for cmd,obj,raw in packets if cmd==88211),dict(id=0,value=0))

    def test_idle_removes_all_duplicate_old_run_packets_before_login_complete(self):
        before=self.state();packets=self.flood();self.assert_sequence(packets,active=False)
        self.assertEqual(self.state(),before)
        bond=packets[0][1];out=self.svc.get_outside_data(UID)
        self.assertEqual(bond,{key:out[key] for key in ('activity_id','next_timestamps','fetters_id')})

    def test_active_run_replaces_capture_once_and_preserves_hp_and_attributes(self):
        self.start();before=self.state();packets=self.flood();self.assert_sequence(packets)
        values={cmd:obj for cmd,obj,raw in packets}
        self.assertEqual(values[88013]['attr_list'],json.loads(self.saved()['attr_list_json']))
        self.assertEqual(values[88019]['hero_list'],json.loads(self.saved()['hero_list_json']))
        self.assertEqual(self.state(),before)

    def test_fresh_missing_capture_still_prepares_full_active_snapshot(self):
        self.start();before=self.state();packets=self.flood(fresh=True,captured=False)
        self.assert_sequence(packets);self.assertEqual(self.state(),before)

    def test_no_longer_in_game_never_replays_stale_run_tail(self):
        self.start();self.set(in_game=0);before=self.state()
        self.assert_sequence(self.flood(),active=False);self.assertEqual(self.state(),before)

    def test_empty_attributes_and_heroes_are_legitimate_zero_byte_packets(self):
        self.start();self.set(attr_list_json='[]',hero_list_json='[]');before=self.state()
        packets=self.flood();self.assert_sequence(packets)
        self.assertEqual({cmd:raw for cmd,obj,raw in packets if cmd in (88013,88019)},{88013:b'',88019:b''})
        self.assertEqual(self.state(),before)

    def test_none_or_empty_required_snapshot_never_sends_any_login_bytes(self):
        self.start()
        for target in ('sc_88001','sc_88013','sc_88019'):
            for bad in (None,b''):
                before=self.state();real=recovery.encode
                with patch.object(recovery,'encode',side_effect=lambda name,*a,**kw:bad if name==target else real(name,*a,**kw)):
                    with self.assertRaises(Exception):self.flood()
                self.assertEqual(self.raw,[]);self.assertEqual(self.state(),before)

    def test_missing_or_failed_bond_snapshot_stops_before_send(self):
        self.start()
        for fault in ('raise','none','empty'):
            before=self.state()
            with self.assertRaises(ValueError):self.flood(fail_bond=fault)
            self.assertEqual(self.raw,[]);self.assertEqual(self.state(),before)

    def test_policy_missing_active_tail_refuses_partial_recovery(self):
        self.start();before=self.state()
        for cmd in (88013,88019):
            with self.assertRaises(ValueError):self.flood(deny=cmd)
            self.assertEqual(self.raw,[]);self.assertEqual(self.state(),before)

    def test_pending_event_and_shop_restore_exact_saved_candidates_no_roll(self):
        self.start()
        event=dict(event_id=30010,opt_list=[dict(opt_id=300101)],trigger_type=1)
        self.set(other_info_json=json.dumps(event));before=self.state()
        with patch('random.choice',side_effect=AssertionError('login must not reroll')):
            packets=self.flood()
        self.assert_sequence(packets,pending=(88023,));self.assertEqual(self.state(),before)
        self.assertEqual(next(obj for cmd,obj,raw in packets if cmd==88023),event)
        shop=dict(event_type=rogue.RogueTeamConst.POP_EVENT_SHOP,param_list=[dict(index=1,param=1410101,rare=3,discount=100,buy_times=1)])
        self.set(other_info_json='{}',shop_info_json=json.dumps(shop));before=self.state()
        with patch('random.choice',side_effect=AssertionError('login must not reroll')):
            packets=self.flood()
        self.assert_sequence(packets,pending=(88027,));self.assertEqual(self.state(),before)
        actual=next(obj for cmd,obj,raw in packets if cmd==88027)['shop_info']['param_list']
        self.assertEqual(actual[0]['param'],1410101);self.assertEqual(actual[0]['rare'],3)

    def test_real_battle_reward_queue_restores_without_new_award_or_room_step(self):
        self.start()
        self.assertEqual(self.svc.settle_battle_node(UID,101,win=True,battle_info=dict(battle_time=45000))[0],0)
        before=self.state();pending=json.loads(self.saved()['other_info_json'])
        with patch('random.choice',side_effect=AssertionError('login must not reroll')):
            packets=self.flood()
        self.assert_sequence(packets,pending=(88015,));self.assertEqual(self.state(),before)
        actual=next(obj for cmd,obj,raw in packets if cmd==88015)['other_info']
        self.assertEqual(actual['param_list'],self.svc._norm_event(pending)['param_list'])
        again=self.flood();self.assertEqual(again,packets);self.assertEqual(self.state(),before)

    def test_ending_plot_and_ready_settle_restore_once_without_progression(self):
        self.start();self.set(floor_num=3,floor_state=1,pending_ending=6,ending_won=1)
        for phase,played in (('ending_plot',False),('ready_settle',True)):
            meta=flow.flow(self.saved());meta.update(phase=phase,plot_played=played)
            flow.save_flow(self.svc,UID,100001,meta);before=self.state()
            packets=self.flood();self.assert_sequence(packets,pending=(88217,));self.assertEqual(self.state(),before)
            self.assertEqual(next(obj for cmd,obj,raw in packets if cmd==88217),dict(id=6,state=int(played)))

    def test_pending_frame_failure_never_sends_partial_run(self):
        self.start();self.set(other_info_json=json.dumps(dict(event_id=30010,opt_list=[dict(opt_id=300101)],trigger_type=1)))
        real=recovery.encode;before=self.state()
        with patch.object(recovery,'encode',side_effect=lambda name,*a,**kw:None if name=='sc_88023' else real(name,*a,**kw)):
            with self.assertRaises(Exception):self.flood()
        self.assertEqual(self.raw,[]);self.assertEqual(self.state(),before)


if __name__=='__main__':
    unittest.main(verbosity=2)
