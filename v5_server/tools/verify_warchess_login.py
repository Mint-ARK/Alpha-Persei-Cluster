"""Serialized p49 login flood, account recovery and no static fallback."""
import json
import struct
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zlib
from verify_report_small_fixes import MemoryTests,UID
import codec
import generator
import login
import new_player_service
import schema_default
import warchess_service as chess


class WarChessLoginTests(MemoryTests):
    def flood(self,captured=True,fresh=False,fail=None,empty=None):
        rows=[]
        if captured:
            for seq,cmd in enumerate((49023,49001,49001,49023)):
                rows.append((seq,cmd,generator.build_frame(cmd,b'')[:11],b'old-account-recording',0,None))
        rows.append((192,10201,generator.build_frame(10201,b'')[:11],schema_default.build('sc_10201'),0,None))
        class Policy:
            def __init__(self,*args):pass
            def allows(self,cmd):return cmd in (49001,49023,10201)
            def report(self,log):pass
            def allows_empty_default(self,*args):return True
        stubs={
            'new_player_login_policy':NS(LoginPolicy=Policy),
            'birthday_service':NS(ensure_official_mail=lambda *a:False),
            'support_cosmetic_service':NS(ensure_support_cosmetic_mails=lambda *a:[]),
            'paid_entitlement_service':NS(settle=lambda *a,**kw:None),
            'res_version_manager':NS(get_current_version=lambda:'229',get_version_config=lambda v:{}),
            'rogueteam_service':NS(RogueTeamService=NS(get=lambda **kw:NS(get_session_data=lambda uid:None))),
            'trust_service':NS(TrustService=NS(get_instance=lambda:NS(get_displace_limit_payload=lambda *a:dict(limit_list=[]),get_combo_skill_payload=lambda *a:dict(info=dict(skill_list=[]))))),
            'ai_translator':NS(AITranslator=NS(get_instance=lambda **kw:NS(trigger_login_greeting=lambda *a,**kw:None)))}
        def generate(cmd,**kw):
            if cmd==fail:raise ValueError('isolated chess snapshot fault')
            if cmd==empty:return None
            if cmd in (49001,49023):return generator.gen_payload(cmd,**kw)
            return schema_default.build('sc_'+str(cmd))
        facade=NS(build_frame=generator.build_frame,gen_payload=generate);raw=[];logs=[]
        self.flood_error=None
        with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=fresh),patch.object(login,'load_login_push',return_value=rows),patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
            try:login.send_login_flood(NS(sendall=raw.append,settimeout=lambda _:None),self.db,UID,facade,frame_delay=0,log=lambda *a:logs.append(a))
            except RuntimeError as exc:self.flood_error=exc
        packets=[]
        for wire in raw:
            cmd=struct.unpack('>H',wire[5:7])[0]+(65536 if wire[4] else 0);data=zlib.decompress(wire[11:]) if wire[2] else wire[11:]
            packets.append((cmd,codec.decode(data,'sc_'+str(cmd)),data))
        return packets,logs

    def test_static_duplicates_are_replaced_with_account_native_groups(self):
        self.call(49002,dict(chapter_id=4040101),49003)
        packets,_=self.flood();self.assertIsNone(self.flood_error);self.assertEqual([p[0] for p in packets if p[0]!=73001],[49001,49023,10201])
        group=next(g for g in packets[0][1]['chess_map_list'] if g['activity_id']==0);self.assertEqual(group['chapter'],4040101)
        self.assertEqual(len(packets[1][1]['chess_open_info_list']),40)

    def test_fresh_missing_frames_are_supplied_without_creating_run(self):
        before=self.db.query('SELECT * FROM ops_common');packets,_=self.flood(captured=False,fresh=True)
        self.assertIsNone(self.flood_error);self.assertEqual([p[0] for p in packets if p[0]!=73001],[49001,49023,10201]);self.assertEqual(self.db.query('SELECT * FROM ops_common'),before)
        self.assertTrue(all(g['chapter']==0 for g in packets[0][1]['chess_map_list']))

    def test_dynamic_failure_never_replays_captured_blob(self):
        for cmd in (49001,49023):
            for mode in ('fail','empty'):
                packets,logs=self.flood(**{mode:cmd});self.assertNotIn(cmd,[p[0] for p in packets]);self.assertNotIn(10201,[p[0] for p in packets])
                self.assertIsNotNone(self.flood_error)

    def test_pending_selection_reloads_from_sql_without_new_candidates(self):
        self.call(49002,dict(chapter_id=4040101),49003);state=chess.read(self.db,UID)
        state['map']['pos']=dict(x=18,z=3);chess.Engine(self.db,UID,state).change((18,4),tag=4);chess.save(self.db,UID,state)
        self.call(49006,dict(type=1,pos=dict(x=18,z=4)),49007);before=chess.read(self.db,UID)
        self.flood();self.assertEqual(chess.read(self.db,UID),before)
        packet,response=self.call(49002,dict(chapter_id=4040101),49003);self.assertEqual(response.scs,[49011,49003])
        choices=codec.decode(response.frames[0]['payload'],'sc_49011')['attribute'];self.assertEqual(choices,before['pending']['choices'])


if __name__=='__main__':unittest.main(verbosity=2)
