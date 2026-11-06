"""A65/A66: actual Core/codec, current Lua metadata and memory-only SQL."""
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
LUA = ROOT / 'decompiled_v2/x64/game/config'


class ProposalTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for cid, cat, kind, unlocked in ((1, 'kernel', 1, 1), (2, 'kernel', 1, 0),
                (101, 'secondary', 2, 1), (102, 'secondary', 2, 1),
                (103, 'secondary', 2, 1), (104, 'secondary', 2, 0),
                (101105, 'hero', 3, 1), (101351, 'reviser', 4, 1),
                (99001, 'secondary', 3, 1), (99002, 'kernel', 2, 1)):
            self.db.execute('INSERT INTO chip(uid,cat,id,type_id,unlocked,remark) VALUES(?,?,?,?,?,?)',
                            (UID, cat, cid, kind, unlocked, '[101,102]' if cid == 1 else '[]'))
        self.svc = chip.ChipService.get_instance(self.db)
        self.proposal(1, [102, 103])

    def proposal(self, pid, values, raw=False):
        self.db.execute('INSERT OR REPLACE INTO chip_proposal(uid,id,name,secondary) VALUES(?,?,?,?)',
                        (UID, pid, '测试方案', values if raw else json.dumps(values)))

    def state(self):
        return {table: self.db.query('SELECT * FROM '+table+' ORDER BY rowid')
                for table in ('chip', 'chip_proposal', 'currency', 'task')}

    def apply(self, kid=1, pid=1):
        return self.call(50014, dict(kernel_chip_id=kid, proposal_id=pid), 50015)

    def save(self, values, pid=2):
        return self.call(50008, dict(id=pid, name='新方案', secondary=values), 50009)

    def reject(self, invoke, sc):
        before = self.state()
        with patch.object(chip.event_bus.bus, 'emit', wraps=chip.event_bus.bus.emit) as events:
            packet, response = invoke()
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(response.scs, [sc])
        self.assertEqual(self.state(), before)
        self.assertFalse(events.called)

    def test_missing_deleted_and_corrupt_proposals_do_not_clear_equipment(self):
        self.reject(lambda: self.apply(pid=999), 50015)
        self.db.execute('DELETE FROM chip_proposal WHERE uid=? AND id=1', (UID,))
        self.reject(self.apply, 50015)
        for raw in ('oops', '{}', 'null', '["101"]', '[true]', ''):
            with self.subTest(raw=raw):
                self.proposal(1, raw, raw=True)
                self.reject(self.apply, 50015)

    def test_application_revalidates_invalid_historical_lists(self):
        for values in ([101, 102, 103], [101, 101], [104], [999], [0], [101105], [101351], [99001]):
            with self.subTest(values=values):
                self.proposal(1, values)
                self.reject(self.apply, 50015)

    def test_save_rejects_bad_structure_or_wrong_chip_category(self):
        for values in ([101, 102, 103], [101, 101], [999], [0], [101105], [101351], [99001]):
            with self.subTest(values=values):
                self.reject(lambda: self.save(values), 50009)
        self.reject(lambda: self.save([101], pid=0), 50009)

    def test_locked_planning_can_be_saved_but_not_applied_until_unlocked(self):
        packet, _ = self.save([104])
        self.assertEqual(packet['result'], 0)
        self.reject(lambda: self.apply(pid=2), 50015)
        self.db.execute("UPDATE chip SET unlocked=1 WHERE uid=? AND id=104", (UID,))
        self.assertEqual(self.apply(pid=2)[0]['result'], 0)
        self.assertEqual(self.svc.get_mimir_equipped_chips(UID, 1), [104])

    def test_unknown_locked_or_wrong_kind_kernel_rejected_by_all_three_entries(self):
        for kid in (999, 2, 101, 99002):
            with self.subTest(kid=kid):
                self.reject(lambda: self.apply(kid=kid), 50015)
                self.reject(lambda: self.call(50006, dict(kernel_id=kid, secondary_id=103, oper=1), 50007), 50007)
                self.reject(lambda: self.call(50016, dict(kernel_id=kid), 50017), 50017)

    def test_nonempty_empty_and_repeated_proposals_match_lua_callback(self):
        for values in ([102, 103], [], [101]):
            self.proposal(1, values)
            for _ in range(2):
                packet, response = self.apply()
                self.assertEqual(packet['result'], 0)
                self.assertEqual(response.scs, [50015])
                # ChipAction.ResetChip then SetEnabledChipID for each saved ID.
                local = []
                for cid in values:
                    if cid not in local and len(local) < chip.MIMIR_SECONDARY_EQUIP_MAX:
                        local.append(cid)
                self.assertEqual(self.svc.get_mimir_equipped_chips(UID, 1), local)
        cold = codec.decode(codec.encode('sc_50001', self.svc.get_sc_50001_dict(UID)), 'sc_50001')
        self.assertEqual(next(p for p in cold['proposals'] if p['id'] == 1)['secondary'], [101])

    def test_manual_install_remove_capacity_and_reset(self):
        self.reject(lambda: self.call(50006, dict(kernel_id=1, secondary_id=103, oper=1), 50007), 50007)
        for sid, op in ((101, 2), (103, 1), (103, 2), (101, 1)):
            self.assertEqual(self.call(50006, dict(kernel_id=1, secondary_id=sid, oper=op), 50007)[0]['result'], 0)
        for sid, op in ((104, 1), (101105, 1), (999, 1), (101, 0), (101, 3)):
            self.reject(lambda: self.call(50006, dict(kernel_id=1, secondary_id=sid, oper=op), 50007), 50007)
        self.assertEqual(self.call(50016, dict(kernel_id=1), 50017)[0]['result'], 0)
        self.assertEqual(self.svc.get_mimir_equipped_chips(UID, 1), [])

    def test_response_failure_rolls_back_each_write_then_retries(self):
        calls = ((50014, dict(kernel_chip_id=1, proposal_id=1), 50015),
                 (50008, dict(id=2, name='新方案', secondary=[101]), 50009),
                 (50006, dict(kernel_id=1, secondary_id=101, oper=2), 50007),
                 (50016, dict(kernel_id=1), 50017))
        ctx = self.conn.to_handler_ctx()
        original = ctx.codec_encode
        for cmd, request, sc in calls:
            with self.subTest(cmd=cmd):
                before = self.state()
                with patch.object(ctx, 'codec_encode', side_effect=lambda n, d: None if n == 'sc_'+str(sc) else original(n, d)):
                    packet, response = self.call(cmd, request, sc)
                self.assertNotEqual(packet['result'], 0)
                self.assertEqual(response.scs, [sc])
                self.assertEqual(self.state(), before)
                self.assertEqual(self.call(cmd, request, sc)[0]['result'], 0)

    def test_current_lua_categories_and_slot_limit(self):
        settings = parse_lua_file(str(LUA/'gamesetting.lua'))
        cfg = parse_lua_file(str(LUA/'chipcfg.lua'))
        values = list(cfg.values()) if isinstance(cfg, dict) else cfg
        entries = [entry for entry in values if isinstance(entry, dict) and 'id' in entry]
        kernels = [e['id'] for e in entries if e['type_id'] == 1]
        secondaries = [e for e in entries if e['type_id'] == 2]
        self.assertEqual(settings['ai_secondary_chip_equip_num']['value'][0], chip.MIMIR_SECONDARY_EQUIP_MAX)
        self.assertEqual(len(kernels), 6)
        self.assertEqual(len(secondaries), 13)
        self.assertTrue(all(e['spec_char'] == 0 for e in secondaries))
        for e in secondaries:
            self.db.execute('INSERT OR REPLACE INTO chip(uid,cat,id,type_id,spec_char,unlocked) VALUES(?,?,?,?,?,1)',
                            (UID, 'secondary', e['id'], e['type_id'], e['spec_char']))
            self.assertEqual(self.svc._validate_mimir_chips(self.db, UID, [e['id']], require_unlocked=True), [e['id']])


if __name__ == '__main__':
    if '--before' in sys.argv:
        sys.argv.remove('--before')
        path = ROOT/'archive/chip_proposal_guard_20261103/before/chip_service.py'
        cls = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                   if isinstance(n, ast.ClassDef) and n.name == 'ChipService')
        for name in ('save_proposal', 'apply_proposal', 'enable_mimir_chip', 'reset_mimir_chip'):
            method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
            ns = dict(vars(chip))
            exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), ns)
            setattr(chip.ChipService, name, ns[name])
    unittest.main(verbosity=2)
