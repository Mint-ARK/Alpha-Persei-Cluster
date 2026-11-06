"""Actual login flood + real MiniGame encoders, recording bytes in memory."""
from contextlib import ExitStack
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib
from verify_report_small_fixes import MemoryTests,UID
import generator
import codec
import login
import new_player_service

COMMANDS=(68181,60105,60107,68171,83021)


class EmptyLoginTests(MemoryTests):
    def flood(self,fresh=False,compressed=False,dynamic=1):
        class Policy:
            def __init__(self,*args):pass
            def allows(self,cmd):return cmd in COMMANDS
            def report(self,log):pass
            def allows_empty_default(self,*args):return True
        calls=[];raw=[];timeouts=[]
        class Generator:
            build_frame=staticmethod(generator.build_frame)
            @staticmethod
            def gen_payload(cmd,uid,db,res_version=None):
                calls.append(cmd)
                return generator.gen_payload(cmd,uid=uid,db=db,res_version=res_version) if cmd in COMMANDS else None
        self.db.execute('DELETE FROM login_push')
        for seq,cmd in enumerate(COMMANDS,1):
            header=bytearray(generator.build_frame(cmd,b'old capture')[:11])
            if compressed:header[2]=1
            self.db.execute('INSERT INTO login_push(seq,cmd,header,payload,dynamic,payload_json) VALUES(?,?,?,?,?,?)',
                (seq,cmd,bytes(header),zlib.compress(b'old capture') if compressed else b'old capture',dynamic,None))
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
        sink=SimpleNamespace(sendall=lambda value:raw.append(bytes(value)),settimeout=timeouts.append)
        with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=fresh),patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
            login.send_login_flood(sink,self.db,UID,Generator(),frame_delay=0)
        result=[]
        for packet in raw:
            self.assertEqual(struct.unpack('>H',packet[:2])[0]+2,len(packet))
            cmd=struct.unpack('>H',packet[5:7])[0]+(65536 if packet[4] else 0)
            if cmd not in COMMANDS:continue
            payload=zlib.decompress(packet[11:]) if packet[2] else packet[11:]
            result.append((cmd,payload,codec.decode(payload,'sc_'+str(cmd))))
        self.assertEqual([r[0] for r in result],list(COMMANDS))
        self.assertTrue(all(payload==b'' and obj=={} for _,payload,obj in result))
        self.assertEqual([c for c in calls if c in COMMANDS],list(COMMANDS))
        self.assertEqual(timeouts,[5,None])

    def test_existing_save_dynamic_empty_states_are_sent(self):self.flood()
    def test_new_save_empty_states_are_sent(self):self.flood(fresh=True)
    def test_new_save_static_rows_are_dynamically_rebuilt(self):self.flood(fresh=True,dynamic=0)
    def test_compressed_capture_header_preserves_valid_empty_state(self):self.flood(compressed=True)


if __name__=='__main__':unittest.main(verbosity=2)
