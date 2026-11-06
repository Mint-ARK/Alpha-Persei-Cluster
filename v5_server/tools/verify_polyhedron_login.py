"""Real TCP login serialization with only unrelated login services isolated."""
import struct
import sys
import zlib
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID,NOW
import codec
import generator
import hero_codec
import login
import new_player_service
import polyhedron_login as recovery
from polyhedron_service import PolyhedronRunManager as M
import schema_default


class PolyhedronLoginTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        self.db.upsert('polyhedron_hero',UID,dict(hero_id=1084),keys=('uid','hero_id'))
        self.call(18010,dict(hero_id_list=[1084],difficulty=1),18011)
        self.raw=[]

    def flood(self,*,fresh=False,captured=True,deny=None):
        rows=[]
        if captured:
            rows=[(i+1,cmd,generator.build_frame(cmd,b'')[:11],b'old-run',0,None)
                  for i,cmd in enumerate((18001,18003,18005,18001,18005))]
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
            'res_version_manager':NS(get_current_version=lambda:'229',get_version_config=lambda v:dict(polyhedron_activity_id=4307301)),
            'trust_service':NS(TrustService=NS(get_instance=lambda:NS(get_displace_limit_payload=lambda *a:dict(limit_list=[]),get_combo_skill_payload=lambda *a:dict(info=dict(skill_list=[]))))),
            'ai_translator':NS(AITranslator=NS(get_instance=lambda **kw:NS(trigger_login_greeting=lambda *a,**kw:None))),
        }
        def generate(cmd,**kw):
            self.assertNotIn(cmd,recovery.OWNED_COMMANDS)
            return schema_default.build('sc_'+str(cmd))
        self.raw=[]
        facade=NS(build_frame=generator.build_frame,gen_payload=generate)
        with patch.dict(sys.modules,stubs),patch.object(new_player_service,'enabled',return_value=fresh),\
             patch.object(login,'load_login_push',return_value=rows),\
             patch.object(login,'reconcile_oath_rights_before_login',return_value=False):
            login.send_login_flood(NS(sendall=self.raw.append,settimeout=lambda _:None),self.db,UID,facade,frame_delay=0,log=lambda *a:None)
        packets=[]
        for raw in self.raw:
            cmd=struct.unpack('>H',raw[5:7])[0]+(65536 if raw[4] else 0)
            if cmd in recovery.OWNED_COMMANDS or cmd==10201:
                body=zlib.decompress(raw[11:]) if raw[2] else raw[11:]
                packets.append((cmd,codec.decode(body,'sc_'+str(cmd))))
        return packets

    def test_active_and_fresh_login_replace_duplicates_without_reroll(self):
        before=M.load_run(UID,self.db)
        for fresh,captured in ((False,True),(True,False)):
            with patch('random.choice',side_effect=AssertionError('login rerolled')):
                packets=self.flood(fresh=fresh,captured=captured)
            self.assertEqual([cmd for cmd,obj in packets],[18001,10201])
            self.assertEqual(dict(packets)[18001]['game']['state'],2)
            self.assertEqual(M.load_run(UID,self.db),before)

    def test_terminal_receipt_precedes_login_complete_without_asset_gain(self):
        self.call(18018,{},18019);run=M.load_run(UID,self.db);balances=[self.balance(i) for i in (45,46,29)]
        packets=self.flood()
        self.assertEqual([cmd for cmd,obj in packets],[18001,18005,10201])
        self.assertEqual(dict(packets)[18005]['end_info'],run['end_info'])
        self.assertEqual([self.balance(i) for i in (45,46,29)],balances)
        self.assertEqual(self.flood(),packets)

    def test_required_state_or_receipt_encoding_failure_sends_no_login_bytes(self):
        self.call(18018,{},18019)
        for target in ('sc_18001','sc_18005'):
            real=recovery.encode
            with patch.object(recovery,'encode',side_effect=lambda name,*a,**kw:None if name==target else real(name,*a,**kw)):
                with self.assertRaises(Exception):self.flood()
            self.assertEqual(self.raw,[])
        with self.assertRaises(Exception):self.flood(deny=18005)
        self.assertEqual(self.raw,[])

    def test_corrupt_stored_run_is_not_silently_replaced_by_idle_snapshot(self):
        self.db.execute('UPDATE polyhedron_run SET run_data=? WHERE uid=?',('{broken',UID))
        with self.assertRaises(Exception):self.flood()
        self.assertEqual(self.raw,[])
        self.assertEqual(self.db.query('SELECT run_data FROM polyhedron_run WHERE uid=?',(UID,))[0]['run_data'],'{broken')


if __name__=='__main__':unittest.main()
