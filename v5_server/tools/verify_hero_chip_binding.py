"""A67: real Core/codec, current Lua metadata, and memory-only persistence."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import chip_service as chip
import codec
from extract_autochess_cfg import parse_lua_file

ROOT = Path(__file__).resolve().parents[2]
_RAW = parse_lua_file(str(ROOT/'decompiled_v2/x64/game/config/chipcfg.lua'))
CFG = {entry['id']: entry for entry in _RAW.values() if isinstance(entry, dict) and 'id' in entry}


class BindingTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc = chip.ChipService.get_instance(self.db)
        for hid, unlocked in ((1011, 1), (1075, 1), (1084, 0)):
            self.db.execute('INSERT INTO hero(uid,id,unlock,level,chip_state) VALUES(?,?,?,70,?)',
                            (UID, hid, unlocked, '{}'))
        for cid in (101101, 101102, 101103, 101104, 101105, 101106, 101351):
            cfg = CFG[cid]
            self.db.execute('INSERT INTO chip(uid,id,cat,type_id,spec_char,role_type_id,unlocked) VALUES(?,?,?,?,?,?,1)',
                            (UID, cid, 'hero' if cfg['type_id'] == 3 else 'reviser',
                             cfg['type_id'], cfg['spec_char'], cfg['role_type_id']))
        self.db.execute("INSERT INTO chip(uid,id,cat,type_id,spec_char,role_type_id,unlocked) VALUES(?,900001,'hero',0,1011,1,1)", (UID,))
        self.db.execute('UPDATE hero SET chip_state=? WHERE uid=? AND id=1011',
                        (json.dumps({'2': 101102}), UID))

    def state(self):
        return {table: self.db.query('SELECT * FROM '+table+' ORDER BY rowid') for table in ('hero', 'chip', 'currency', 'task')}

    def slots(self, hid=1011):
        return json.loads(self.db.get('hero', UID, 'AND id=?', (hid,))['chip_state'])

    def equip(self, cid=101101, hid=1011, slot=1):
        return self.call(50018, dict(hero_id=hid, slot_id=slot, secondary=cid), 50019)

    def reject(self, **kwargs):
        before = self.state()
        with patch.object(chip.event_bus.bus, 'emit', wraps=chip.event_bus.bus.emit) as event:
            packet, response = self.equip(**kwargs)
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(response.scs, [50019])
        self.assertEqual(self.state(), before)
        self.assertFalse(event.called)

    def check_success_frames(self, response):
        self.assertEqual(response.scs[0], 50019)
        self.assertEqual(response.scs.count(50019), 1)
        # Ownership reconciliation can emit 53003 for a previously stale count.
        # Keep that real notification; the A71 suite verifies count semantics.
        self.assertTrue(all(sc in (50019, 53003) for sc in response.scs))
        for frame in response.frames:
            if frame['sc'] == 53003:
                delta = codec.decode(frame['payload'], 'sc_53003')
                self.assertTrue(delta['achievement_progress'])

    def test_wrong_hero_slot_type_and_missing_metadata_rejected(self):
        for request in (dict(hid=1075), dict(slot=2), dict(cid=101351), dict(cid=900001)):
            with self.subTest(request=request):
                self.reject(**request)

    def test_missing_target_rejected_for_both_equip_and_unload(self):
        self.reject(hid=999999)
        self.reject(cid=0, hid=999999)

    def test_unknown_locked_and_invalid_slots_rejected(self):
        self.reject(cid=999999)
        self.db.execute('UPDATE chip SET unlocked=0 WHERE uid=? AND id=101101', (UID,))
        self.reject()
        for slot in (0, 5, 99):
            self.reject(cid=0, slot=slot)

    def test_valid_replace_unload_and_repeat_preserve_other_slots(self):
        for cid in (101101, 101105, 101105, 0, 0):
            cfg = CFG[cid] if cid else CFG[101101]
            slot = cfg['role_type_id']
            packet, response = self.equip(cid=cid, slot=slot)
            self.assertEqual(packet['result'], 0)
            self.check_success_frames(response)
            self.assertEqual(self.slots()[str(slot)], cid)
            self.assertEqual(self.slots()['2'], 101102)
        cold = codec.decode(codec.encode('sc_50001', self.svc.get_sc_50001_dict(UID)), 'sc_50001')
        hero = next(h for h in cold['hero_chip_state'] if h['hero_id'] == 1011)
        self.assertEqual(hero['secondary'][0], 0)
        self.assertEqual(hero['secondary'][1], 101102)
        # ChipData.InitData uses ChipCfg.role_type_id for nonzero chips; the
        # immediate 50019 callback writes the requested slot, including zero.
        restored = [0, 0, 0, 0]
        for cid in hero['secondary']:
            if cid > 0:
                restored[CFG[cid]['role_type_id']-1] = cid
        self.assertEqual(restored, [self.slots().get(str(i), 0) for i in range(1, 5)])

    def test_empty_defaults_work_without_destroying_other_state(self):
        for raw in (None, '', '{}', '[]'):
            with self.subTest(raw=raw):
                self.db.execute('UPDATE hero SET chip_state=? WHERE uid=? AND id=1011', (raw, UID))
                self.assertEqual(self.equip()[0]['result'], 0)
                self.assertEqual(self.slots(), {'1': 101101})

    def test_corrupt_or_nonempty_array_state_is_not_silently_erased(self):
        for raw in ('broken', 'null', 'true', '[101101]', '12'):
            with self.subTest(raw=raw):
                self.db.execute('UPDATE hero SET chip_state=? WHERE uid=? AND id=1011', (raw, UID))
                self.reject()

    def test_existing_precreated_hero_policy_is_preserved(self):
        # A67 concerns existence and matching ownership, not a new unlock gate.
        self.db.execute('UPDATE hero SET unlock=0 WHERE uid=? AND id=1011', (UID,))
        self.assertEqual(self.equip()[0]['result'], 0)
        self.assertEqual(self.slots()['1'], 101101)

    def test_write_and_response_failures_roll_back_then_retry(self):
        execute = self.db.execute
        def fail(sql, *args, **kwargs):
            if sql.lstrip().startswith('UPDATE hero SET chip_state'):
                raise RuntimeError('isolated chip write failure')
            return execute(sql, *args, **kwargs)
        with patch.object(self.db, 'execute', side_effect=fail):
            self.reject()
        ctx = self.conn.to_handler_ctx()
        encode = ctx.codec_encode
        before = self.state()
        with patch.object(ctx, 'codec_encode', side_effect=lambda n, d: None if n == 'sc_50019' else encode(n, d)):
            packet, response = self.equip()
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(response.scs, [50019])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.equip()[0]['result'], 0)

    def test_current_lua_hero_chip_metadata_is_accepted_at_its_actual_slot(self):
        entries = [e for e in CFG.values() if e['type_id'] == 3]
        self.assertTrue(entries)
        for e in entries:
            self.assertIn(e['role_type_id'], (1, 2, 3, 4))
            self.assertGreater(e['spec_char'], 0)
            hid = e['spec_char']
            self.db.execute("INSERT OR IGNORE INTO hero(uid,id,unlock,chip_state) VALUES(?,?,1,'{}')", (UID, hid))
            self.db.execute("INSERT OR REPLACE INTO chip(uid,id,cat,type_id,spec_char,role_type_id,unlocked) VALUES(?,?,'hero',3,?,?,1)",
                            (UID, e['id'], hid, e['role_type_id']))
            packet, response = self.equip(cid=e['id'], hid=hid, slot=e['role_type_id'])
            self.assertEqual(packet['result'], 0, e['id'])
            self.check_success_frames(response)
            self.assertEqual(self.slots(hid)[str(e['role_type_id'])], e['id'])


if __name__ == '__main__':
    if '--before' in sys.argv:
        sys.argv.remove('--before')
        path = ROOT/'archive/hero_chip_binding_20261103/before/chip_service.py'
        cls = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                   if isinstance(n, ast.ClassDef) and n.name == 'ChipService')
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'enable_hero_chip')
        ns = dict(vars(chip))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), ns)
        chip.ChipService.enable_hero_chip = ns['enable_hero_chip']
    unittest.main(verbosity=2)
