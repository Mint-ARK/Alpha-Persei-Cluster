"""A139/A140: canonical thresholds, durable read state and login recovery."""
import json
import struct
import unittest
import zlib
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import achievement_service as achievement
import codec
import generator
import login
from event_bus import bus, Events


class ThresholdTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc=achievement.AchievementService(self.db)
        self.db.execute('UPDATE users SET level=10,extra=? WHERE uid=?',(json.dumps(dict(achievement_story_read=[])),UID))
        self.db.execute('INSERT INTO achievement(uid,achievement_id,condition,need,progress,complete_flag,achieve_time) VALUES(?,14401,2,20,10,0,123456)',(UID,))
        self.assertEqual(achievement._CFG[14401]['need'],10)

    def row(self):return self.db.get('achievement',UID,'AND achievement_id=14401')

    def claim(self):return self.call(53004,dict(id=[14401]),53005)

    def test_direct_claim_uses_current_high_to_low_threshold(self):
        answer,_=self.claim()
        self.assertEqual(answer['result'],0)
        self.assertEqual((self.row()['need'],self.row()['complete_flag']),(10,1))
        self.assertEqual(self.balance(1),10)

    def test_direct_claim_does_not_use_stale_low_threshold(self):
        self.db.execute('UPDATE achievement SET need=5,progress=7,achieve_time=0 WHERE uid=?',(UID,))
        answer,_=self.claim()
        self.assertEqual(answer['result'],2005)
        self.assertEqual(self.row()['complete_flag'],0)
        self.assertEqual(self.balance(1),0)

    def test_full_initialization_still_syncs_existing_thresholds(self):
        self.svc.init_user_achievements(UID)
        self.db.execute('UPDATE achievement SET need=20 WHERE uid=? AND achievement_id=14401',(UID,))
        self.svc.init_user_achievements(UID)
        self.assertEqual(self.row()['need'],10)
        self.assertEqual((self.row()['progress'],self.row()['achieve_time']),(10,123456))

    def test_reconcile_keeps_real_completion_time_and_claim_history(self):
        payload=self.svc.get_achievement_login_payload(UID)
        packet=codec.decode(payload,'sc_53001')
        row=next(v for v in packet['achievement_list'] if v['id']==14401)
        self.assertEqual(row['achieve_time'],123456)
        self.db.execute('UPDATE achievement SET need=20,complete_flag=1 WHERE uid=? AND achievement_id=14401',(UID,))
        self.svc.get_achievement_login_payload(UID)
        self.assertEqual((self.row()['need'],self.row()['complete_flag'],self.row()['achieve_time']),(10,1,123456))
        self.assertEqual(self.balance(1),0)

    def test_raised_threshold_clears_only_unearned_completion_time(self):
        self.db.execute('UPDATE users SET level=7 WHERE uid=?',(UID,))
        self.db.execute('UPDATE achievement SET need=5,progress=7 WHERE uid=?',(UID,))
        self.svc.reconcile_achievements(UID)
        self.assertEqual((self.row()['need'],self.row()['achieve_time']),(10,0))

    def test_level_event_syncs_need_and_pushes_matching_progress(self):
        self.db.execute('UPDATE achievement SET progress=0,achieve_time=0 WHERE uid=?',(UID,))
        ctx=self.conn.to_handler_ctx()
        bus.emit(Events.PLAYER_LEVEL_UP,ctx,UID,new_lv=10)
        self.assertEqual((self.row()['need'],self.row()['progress']),(10,10))
        updates=[codec.decode(f.payload,'sc_53003') for f in ctx.pending_frames if f.cmd==53003]
        self.assertTrue(any(r['id']==14401 and r['progress']==10 for p in updates for r in p['achievement_progress']))

    def fallback(self):
        with patch.object(achievement.AchievementService,'get_achievement_login_payload',side_effect=RuntimeError('injected reconcile failure')):
            return codec.decode(generator.gen_payload(53001,uid=UID,db=self.db),'sc_53001')

    def test_fallback_preserves_explicit_read_subset_or_empty(self):
        for read in ([],[2,5]):
            with self.subTest(read=read):
                extra=json.dumps(dict(achievement_story_read=read))
                self.db.execute('UPDATE users SET extra=? WHERE uid=?',(extra,UID))
                self.assertEqual(self.fallback().get('story_line',[]),read)
                self.assertEqual(self.db.get('users',UID)['extra'],extra)

    def test_fallback_does_not_turn_malformed_state_into_all_read(self):
        for raw in ('broken','[]',json.dumps(dict(achievement_story_read='bad'))):
            with self.subTest(raw=raw):
                self.db.execute('UPDATE users SET extra=? WHERE uid=?',(raw,UID))
                self.assertEqual(self.fallback().get('story_line',[]),[])
                self.assertEqual(self.db.get('users',UID)['extra'],raw)

    def test_missing_read_field_keeps_existing_points_compatibility(self):
        self.db.execute("UPDATE users SET extra='{}' WHERE uid=?",(UID,))
        self.db.set_currency(UID,33,220)
        self.assertEqual(self.fallback().get('story_line',[]),[1,2])
        self.assertEqual(codec.decode(self.svc.get_achievement_login_payload(UID),'sc_53001')['story_line'],[1,2])

    def test_real_missing_optional_table_uses_durable_empty_read_list(self):
        self.db.execute('DROP TABLE chip')
        packet=codec.decode(generator.gen_payload(53001,uid=UID,db=self.db),'sc_53001')
        self.assertEqual(packet.get('story_line',[]),[])
        self.assertEqual(self.balance(33),0)

    def test_static_login_record_cannot_overwrite_read_state(self):
        old=codec.encode('sc_53001',dict(story_line=[6,5,4,3,2,1]))
        records=[(1,53001,generator.build_frame(53001,b'')[:11],old,0,None)]
        class Socket:
            def __init__(self):self.frames=[]
            def settimeout(self,_):pass
            def sendall(self,value):self.frames.append(value)
        sock=Socket()
        with patch.object(login,'load_login_push',return_value=records), \
             patch.object(login,'reconcile_oath_rights_before_login',return_value=False), \
             patch.object(login,'prepare_skin_draw_login',return_value=([],False)), \
             patch('ai_translator.AITranslator.trigger_login_greeting'):
            login.send_login_flood(sock,self.db,UID,generator,frame_delay=0)
        packets=[]
        for frame in sock.frames:
            if struct.unpack_from('>H',frame,5)[0]==53001:
                payload=frame[11:]
                if frame[4]&1:payload=zlib.decompress(payload)
                packets.append(codec.decode(payload,'sc_53001').get('story_line',[]))
        self.assertEqual(packets,[[]])


if __name__=='__main__':
    unittest.main()
