"""Actual serialized login flood probes with unrelated activity services isolated."""
import json
import struct
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zlib

from verify_report_small_fixes import MemoryTests, UID
import codec
import generator
import login
import new_player_service
import quanzhou_collection as collection
import quanzhou_quest as quest
import quanzhou_service as world
import schema_default

OWNED = (79601, 28801, 28837, 86035)


class QuanzhouLoginTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.execute('INSERT INTO item_catalog(id,type,category) VALUES(77,1,?)', ('currency',))

    def stored_quests(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid')
                for t in ('qworld_quest', 'qworld_quest_task')}

    def flood(self, *, fresh=False, captured=True, fail=None, empty=None):
        rows = [(1, 15009, generator.build_frame(15009, b'')[:11], b'captured-invalid-currency', 0,
                 json.dumps(dict(currency_list=[dict(id=77, num=99)], last_fatigue_recover_time=0)))]
        if captured:
            for i, cmd in enumerate((86035, 28837, 79601, 28801, 79601, 28837, 28801, 86035)):
                rows.append((i+2, cmd, generator.build_frame(cmd, b'')[:11], b'captured-invalid', 0, None))
        rows.append((192, 10201, generator.build_frame(10201, b'')[:11], schema_default.build('sc_10201'), 0, None))

        class Policy:
            def __init__(self, *args): pass
            def allows(self, cmd): return cmd in (*OWNED, 15009, 10201)
            def report(self, log): pass
            def allows_empty_default(self, *args): return True

        stubs = {
            'new_player_login_policy': NS(LoginPolicy=Policy),
            'birthday_service': NS(ensure_official_mail=lambda *a: False),
            'support_cosmetic_service': NS(ensure_support_cosmetic_mails=lambda *a: []),
            'paid_entitlement_service': NS(settle=lambda *a, **kw: None),
            'res_version_manager': NS(get_current_version=lambda: '229', get_version_config=lambda v: {}),
            'rogueteam_service': NS(RogueTeamService=NS(get=lambda **kw: NS(get_session_data=lambda uid: None))),
            'trust_service': NS(TrustService=NS(get_instance=lambda: NS(
                get_displace_limit_payload=lambda *a: dict(limit_list=[]),
                get_combo_skill_payload=lambda *a: dict(info=dict(skill_list=[]))))),
            'ai_translator': NS(AITranslator=NS(get_instance=lambda **kw: NS(trigger_login_greeting=lambda *a, **kw: None))),
        }
        def generate(cmd, **kw):
            if cmd == fail:
                raise ValueError('isolated Citong snapshot error')
            if cmd == empty:
                return None
            if cmd in (*OWNED, 15009):
                return generator.gen_payload(cmd, **kw)
            return schema_default.build('sc_' + str(cmd))
        facade = NS(build_frame=generator.build_frame, gen_payload=generate)
        raw, logs = [], []
        with patch.dict(sys.modules, stubs), patch.object(new_player_service, 'enabled', return_value=fresh), \
             patch.object(login, 'load_login_push', return_value=rows), \
             patch.object(login, 'reconcile_oath_rights_before_login', return_value=False):
            login.send_login_flood(NS(sendall=raw.append, settimeout=lambda _: None), self.db, UID,
                                   facade, frame_delay=0, log=lambda *a: logs.append(a))
        packets = []
        for wire in raw:
            cmd = struct.unpack('>H', wire[5:7])[0] + (65536 if wire[4] else 0)
            data = zlib.decompress(wire[11:]) if wire[2] else wire[11:]
            packets.append((cmd, codec.decode(data, 'sc_' + str(cmd)), data))
        return packets, logs

    def assert_sequence(self, packets):
        self.assertEqual([cmd for cmd, _, _ in packets if cmd in (*OWNED, 15009, 10201)], [15009, *OWNED, 10201])
        self.assertNotIn(17023, [cmd for cmd, _, _ in packets])
        self.assertNotIn(28007, [cmd for cmd, _, _ in packets])

    def test_empty_old_account_replaces_static_duplicates_and_does_not_write_initial_quests(self):
        before = self.stored_quests()
        packets, _ = self.flood()
        self.assert_sequence(packets)
        self.assertEqual(self.stored_quests(), before)
        values = {cmd: obj for cmd, obj, _ in packets}
        self.assertEqual(values[79601], dict(activity_id=323641))
        self.assertEqual(values[86035], dict(data={}))
        self.assertEqual(values[28837].get('sys_id_list', []), [])
        self.assertTrue(any(m['id'] == 11101 and m['status'] == 3 for m in values[28801]['quest_list']))
        self.assertFalse(any(m['id'] == 11102 for m in values[28801]['quest_list']))
        self.assertEqual([raw for cmd, _, raw in packets if cmd == 28837], [b''])
        self.assertNotIn(dict(id=77, num=99), values[15009].get('currency_list', []))

    def test_fresh_account_missing_all_four_packets_gets_ordered_native_snapshots(self):
        before = self.stored_quests()
        packets, _ = self.flood(fresh=True, captured=False)
        self.assert_sequence(packets)
        self.assertEqual(self.stored_quests(), before)
        self.assertEqual({cmd: obj for cmd, obj, _ in packets}[28801], quest.quest_payload(self.db, UID))

    def test_saved_position_functions_and_voyage_receipts_restore_from_account(self):
        position = dict(stage_id=1110101, map_status=0, position_x=1, position_y=2, position_z=3,
                        rotation_x=0, rotation_y=0, rotation_z=0, rotation_w=1)
        state = world.read(self.db, UID); state['position'] = position; world.save(self.db, UID, state)
        self.db.execute('INSERT INTO qworld_quest_task(uid,main_quest_id,task_id,status,progress) VALUES(?,11103,11103005,1,1)', (UID,))
        self.db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?)',
                        (UID, quest.KIND, str(quest.ACTIVITY), json.dumps([111010101])))
        state = collection.read(self.db, UID); state['completed'] = [400001, 400101]; collection.save(self.db, UID, state)
        before = self.stored_quests()
        packets, _ = self.flood()
        self.assert_sequence(packets)
        values = {cmd: obj for cmd, obj, _ in packets}
        self.assertEqual(values[79601]['stage_list'], [position])
        self.assertEqual(values[28837]['sys_id_list'], [111010101])
        self.assertEqual(values[86035], dict(data=dict(affix=[2001], stage_id_list=[400001, 400101])))
        self.assertEqual(self.stored_quests(), before)

    def test_knowledge_is_recovered_before_even_the_first_currency_packet(self):
        self.db.execute('INSERT INTO qworld_quest(uid,main_quest_id,status) VALUES(?,11101,9)', (UID,))
        before = self.stored_quests()
        packets, _ = self.flood()
        self.assert_sequence(packets)
        self.assertEqual(packets[0][0], 15009)
        self.assertIn(dict(id=77, num=3), packets[0][1]['currency_list'])
        self.assertEqual(self.balance(77), 3)
        self.assertEqual(self.stored_quests(), before)
        again, _ = self.flood(fresh=True)
        self.assertIn(dict(id=77, num=3), again[0][1]['currency_list'])
        self.assertEqual(self.balance(77), 3)

    def test_critical_dynamic_read_failure_never_replays_the_old_capture(self):
        for cmd in OWNED:
            with self.subTest(cmd=cmd):
                with self.assertRaises(RuntimeError): self.flood(fail=cmd)

    def test_currency_error_and_none_both_refuse_captured_knowledge_balance(self):
        self.db.execute('INSERT INTO qworld_quest(uid,main_quest_id,status) VALUES(?,11101,9)', (UID,))
        for mode in ('fail', 'empty'):
            with self.subTest(mode=mode):
                with self.assertRaises(RuntimeError): self.flood(**{mode: 15009})
                self.assertEqual(self.balance(77), 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
