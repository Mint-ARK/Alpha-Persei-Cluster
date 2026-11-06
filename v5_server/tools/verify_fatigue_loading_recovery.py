"""A52/A152: memory database, fixed clock, active pushes and login frames."""
import json
import struct
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from account_db import AccountDB
import codec
import fatigue_service as fatigue
import generator
import login

NOW=1793667600
DEFAULT=[2109401,2109403,2109402]


class RecoveryTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))

    def stamina(self,amount=50,last=NOW+86400):
        self.db.set_currency(UID,4,amount)
        self.db.execute('UPDATE currency SET last_fatigue_recover_time=? WHERE uid=? AND id=4',(last,UID))

    def fatigue_row(self):
        return self.db.query('SELECT num,last_fatigue_recover_time FROM currency WHERE uid=? AND id=4',(UID,))[0]

    def test_future_anchor_is_saved_and_recovery_resumes_at_360(self):
        self.stamina()
        self.assertEqual(fatigue.settle_fatigue(self.db,UID,NOW),(50,NOW))
        self.assertEqual(self.fatigue_row(),dict(num=50,last_fatigue_recover_time=NOW))
        self.assertEqual(fatigue.settle_fatigue(self.db,UID,NOW+359),(50,NOW))
        self.assertEqual(fatigue.settle_fatigue(self.db,UID,NOW+360),(51,NOW+360))

    def test_future_anchor_online_pushes_only_time_then_resumes(self):
        self.stamina()
        pushes=[]
        conn=SimpleNamespace(send_push=lambda cmd,payload:pushes.append((cmd,codec.decode(payload,'sc_'+str(cmd)))))
        with patch.object(fatigue.time,'time',return_value=NOW):
            self.assertTrue(fatigue.check_and_push_fatigue(conn,UID,self.db,codec.encode))
            self.assertEqual(pushes,[(17025,dict(last_fatigue_recover_time=NOW))])
            self.assertFalse(fatigue.check_and_push_fatigue(conn,UID,self.db,codec.encode))
        self.assertEqual(self.fatigue_row()['num'],50)
        pushes.clear()
        with patch.object(fatigue.time,'time',return_value=NOW+360):
            self.assertTrue(fatigue.check_and_push_fatigue(conn,UID,self.db,codec.encode))
        self.assertEqual([cmd for cmd,_ in pushes],[17023,17025])
        self.assertEqual(pushes[0][1]['normal_items'],[dict(id=4,num=51)])

    def test_normal_recovery_keeps_remainder_and_full_amount(self):
        self.stamina(last=NOW-743)
        self.assertEqual(fatigue.settle_fatigue(self.db,UID,NOW),(52,NOW-23))
        for amount in (240,300):
            self.stamina(amount=amount)
            self.assertEqual(fatigue.settle_fatigue(self.db,UID,NOW),(amount,0))
            self.assertEqual(self.fatigue_row()['last_fatigue_recover_time'],0)

    def set_loading(self,value):
        self.db.execute('INSERT OR REPLACE INTO loading_set(uid,id_list,update_ts) VALUES(?,?,0)',(UID,value))

    def loading(self,db=None):
        return codec.decode(generator.gen_payload(52021,uid=UID,db=db or self.db),'sc_52021').get('id_list',[])

    def test_explicit_empty_survives_cold_read(self):
        answer,_=self.call(52016,dict(all_id_list=[]),52017)
        self.assertEqual(answer['result'],0)
        self.assertEqual(self.loading(),[])
        cold=AccountDB.new_save_workspace()
        self.addCleanup(cold.close)
        self.db._checked_conn().backup(cold._checked_conn())
        self.assertEqual(self.loading(cold),[])

    def test_remove_last_picture_keeps_empty(self):
        self.set_loading('[1000101]')
        answer,_=self.call(52018,dict(type=False,id=1000101),52019)
        self.assertEqual(answer['result'],0)
        self.assertEqual(self.loading(),[])

    def test_nonempty_save_stays_selected(self):
        chosen=[1000101,2109401]
        self.assertEqual(self.call(52016,dict(all_id_list=chosen),52017)[0]['result'],0)
        self.assertEqual(self.loading(),chosen)

    def test_missing_and_damaged_settings_keep_legacy_fallback(self):
        self.assertEqual(self.loading(),DEFAULT)
        for value in ('broken','{}','null','["broken"]'):
            with self.subTest(value=value):
                self.set_loading(value)
                self.assertEqual(self.loading(),DEFAULT)
                self.assertEqual(self.db.get('loading_set',UID)['id_list'],value)

    def test_new_save_still_uses_empty_selection(self):
        import new_player_service
        with patch.object(new_player_service,'enabled',return_value=True):
            self.set_loading('[]')
            self.assertEqual(self.loading(),[])

    def flood(self,frames):
        class Socket:
            def __init__(self):self.frames=[]
            def settimeout(self,_):pass
            def sendall(self,data):self.frames.append(data)
        sock=Socket()
        with patch.object(login,'load_login_push',return_value=frames), \
             patch.object(login,'reconcile_oath_rights_before_login',return_value=False), \
             patch.object(login,'prepare_skin_draw_login',return_value=([],False)), \
             patch('ai_translator.AITranslator.trigger_login_greeting'):
            login.send_login_flood(sock,self.db,UID,generator,frame_delay=0)
        return sock

    def restored_loading(self,sock):
        matching=[]
        for frame in sock.frames:
            if struct.unpack_from('>H',frame,5)[0]==52021:
                payload=frame[11:]
                if frame[4]&1:payload=zlib.decompress(payload)
                matching.append(codec.decode(payload,'sc_52021').get('id_list',[]))
        return matching

    def test_login_static_record_cannot_restore_old_pictures(self):
        self.set_loading('[]')
        old=codec.encode('sc_52021',dict(id_list=DEFAULT))
        frames=[(1,52021,generator.build_frame(52021,b'')[:11],old,0,json.dumps(dict(id_list=DEFAULT)))]
        self.assertEqual(self.restored_loading(self.flood(frames)),[[]])

    def test_login_inserts_missing_settings_frame(self):
        self.set_loading('[]')
        # A real login sequence exists, but its settings row is missing.
        marker=(1,10201,generator.build_frame(10201,b'')[:11],b'',0,None)
        self.assertEqual(self.restored_loading(self.flood([marker])),[[]])

    def test_login_does_not_fall_back_to_stale_blob_when_settings_fail(self):
        self.set_loading('[]')
        old=codec.encode('sc_52021',dict(id_list=DEFAULT))
        frames=[(1,52021,generator.build_frame(52021,b'')[:11],old,0,None)]
        real=generator.gen_payload
        def unavailable(cmd,*args,**kwargs):
            return None if cmd==52021 else real(cmd,*args,**kwargs)
        with patch.object(generator,'gen_payload',side_effect=unavailable):
            with self.assertRaises(RuntimeError):
                self.flood(frames)


if __name__=='__main__':
    unittest.main()
