"""Memory-only real codec/Core/login-flood AutoChess recovery checks."""
from copy import deepcopy
import json
import struct
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zlib

from verify_report_small_fixes import MemoryTests, UID
import autochess_login as recovery
import autochess_service as ac
import codec
import generator
import login
import new_player_service
import schema_default


class LoginTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc = ac.AutoChessService.get_instance(self.db)
        self.db.execute('INSERT INTO item_catalog(id,type,category) VALUES(80,1,?)', ('currency',))

    def op(self, cmd, **data):
        sc = 89143 if cmd == 89142 else cmd + 1
        return self.call(cmd, dict(game_type=0, **data), sc)

    def start(self):
        self.assertEqual(self.op(89116, other_param=101)[0]['result'], 0)

    def edit(self, **values):
        session = self.svc.get_session(UID)
        session.update(values)
        self.svc.save_session(UID)

    def battle(self, win=False, terminal=False):
        self.start()
        if win:
            self.edit(board_chess=[dict(unique_id=100500, chess_id=101, index=1,
                                       star=1, exp=1, atk=10000, hp=10000)])
        if terminal:
            self.edit(**({'win_count': 1} if win else {'hp': 1}))
        self.assertEqual(self.op(89144)[0]['result'], 0)
        packet, _ = self.op(89108)
        self.assertEqual(packet['result'], 0)
        return packet

    def stored(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid')
                for table in ('autochess_session', 'ops_common', 'autochess_stage', 'autochess_medal')}

    def flood(self, *, fresh=False, captured=True):
        # Actual TCP frame serialization; unrelated login features isolated.
        rows = []
        if captured:
            for i, cmd in enumerate((89201, 89125, 89101, 89109, 89113, 89101, 89125, 89201)):
                rows.append((i+1, cmd, generator.build_frame(cmd, b'')[:11], b'captured-invalid', 0, None))
        rows.append((192, 10201, generator.build_frame(10201, b'')[:11], schema_default.build('sc_10201'), 0, None))
        class Policy:
            def __init__(self, *args): pass
            def allows(self, cmd): return cmd in recovery.OWNED_COMMANDS or cmd == 10201
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
            self.assertNotIn(cmd, recovery.OWNED_COMMANDS, 'must keep the pre-encoded snapshot')
            return schema_default.build('sc_' + str(cmd))
        facade = NS(build_frame=generator.build_frame, gen_payload=generate)
        raw, logs = [], []
        with patch.dict(sys.modules, stubs), patch.object(new_player_service, 'enabled', return_value=fresh), \
             patch.object(login, 'load_login_push', return_value=rows), \
             patch.object(login, 'reconcile_oath_rights_before_login', return_value=False):
            login.send_login_flood(NS(sendall=raw.append, settimeout=lambda _: None), self.db, UID,
                                   facade, frame_delay=0, log=lambda *a: logs.append(a))
        packets = []
        for frame in raw:
            cmd = struct.unpack('>H', frame[5:7])[0] + (65536 if frame[4] else 0)
            if cmd in recovery.OWNED_COMMANDS or cmd == 10201:
                data = zlib.decompress(frame[11:]) if frame[2] else frame[11:]
                packets.append((cmd, codec.decode(data, 'sc_' + str(cmd))))
        return packets, logs

    def assert_snapshot(self, packets, state, replay=None):
        self.assertEqual([cmd for cmd, _ in packets],
                         [89201, 89101] + ([89109] if replay else []) + [89125, 10201])
        values = dict(packets)
        self.assertEqual(values[89125], dict(game_type=0, state=state))
        self.assertEqual(values[89101]['game_type'], 0)
        if replay:
            self.assertEqual(values[89109], replay)
            info = values[89109]['replay_info']
            self.assertIn('auto_chessboard_info1', info)
            self.assertIn('auto_chessboard_info2', info)

    def test_idle_empty_progress_static_duplicates_removed(self):
        packets, _ = self.flood()
        self.assert_snapshot(packets, 0)
        self.assertEqual(dict(packets)[89201], {})

    def test_prepare_does_not_replay_previous_consumed_round(self):
        self.battle()
        self.assertEqual(self.op(89126)[0]['result'], 0)
        before = self.stored()
        packets, _ = self.flood()
        self.assert_snapshot(packets, 1)
        self.assertEqual(self.stored(), before)

    def test_prepare_end_receives_board_before_state_callback(self):
        self.start(); self.op(89144)
        packets, _ = self.flood()
        self.assert_snapshot(packets, 2)
        self.assertEqual(self.op(89108)[0]['result'], 0)

    def test_round_settle_cold_uses_exact_saved_replay_and_can_settle(self):
        original = self.battle()
        before = self.stored()
        self.db._autochess_service = ac.AutoChessService(self.db)
        with patch.object(self.db._autochess_service, '_simulate_round_battle', side_effect=AssertionError('no recompute')):
            packets, _ = self.flood()
        self.assert_snapshot(packets, 3, original)
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.op(89126)[0]['result'], 0)

    def test_terminal_win_and_loss_restore_real_outcome(self):
        for win, state in ((True, 4), (False, 5)):
            with self.subTest(win=win):
                self.op(89128)
                original = self.battle(win=win, terminal=True)
                packets, _ = self.flood()
                self.assert_snapshot(packets, state, original)
                self.assertEqual(self.op(89126)[0]['result'], 0)

    def test_new_save_missing_all_captured_rows_still_restores(self):
        original = self.battle()
        packets, _ = self.flood(fresh=True, captured=False)
        self.assert_snapshot(packets, 3, original)

    def recoveries(self):
        return [json.loads(row['value_json']) for row in self.db.query(
            'SELECT value_json FROM ops_common WHERE uid=? AND kind=? ORDER BY rowid',
            (UID, recovery.RECOVERY_KIND))]

    def test_legacy_missing_pending_archived_cancelled_and_reentry_works(self):
        for state in (3, 4, 5):
            with self.subTest(state=state):
                self.start(); self.edit(state=state)
                self.db.execute('DELETE FROM ops_common WHERE kind=?', (self.svc.SESSION_KIND,))
                original = self.db.query('SELECT * FROM autochess_session WHERE uid=?', (UID,))
                packets, logs = self.flood(fresh=state == 5, captured=state != 5)
                self.assert_snapshot(packets, 0)
                self.assertEqual(dict(packets)[89101], codec.decode(self.svc._build_empty_89101(), 'sc_89101'))
                self.assertTrue(any('已保留恢复记录并取消本局' in row[0] for row in logs))
                archived = self.recoveries()[-1]
                self.assertEqual(archived['stored_session'], original)
                self.assertEqual(archived['stored_runtime'], [])
                self.assertEqual(archived['reason'], 'invalid_durable_round')
                self.assertEqual(archived['action'], 'cancel_without_settlement')
                self.assertNotEqual(self.op(89126)[0]['result'], 0)
                self.assertFalse(self.db.query('SELECT * FROM autochess_stage'))
                before = self.stored()
                self.assert_snapshot(self.flood()[0], 0)
                self.assertEqual(self.stored(), before)
        self.start()

    def test_mismatched_pending_identity_is_not_a_login_replay(self):
        self.battle()
        session = deepcopy(self.svc.get_session(UID))
        session['pending_round']['battle_uid'] += 1
        session['chess_bonus'] = {'101': [7, 8]}
        self.edit(**session)
        before = self.stored()
        self.assert_snapshot(self.flood()[0], 0)
        archived = self.recoveries()[0]
        self.assertEqual(archived['session'], session)
        self.assertEqual(archived['stored_session'], before['autochess_session'])
        self.assertEqual(archived['stored_runtime'], before['ops_common'])
        self.assertEqual(archived['session']['chess_bonus'], {'101': [7, 8]})

    def test_orphan_recovery_preserves_assets_claim_history_and_medal_growth(self):
        self.battle()
        self.edit(pending_round=None, chess_bonus={'101': [7, 8]})
        self.db.set_currency(UID, 80, 321)
        self.db.execute('INSERT INTO hero(uid,id,level) VALUES(?,?,?)', (UID, 1084, 80))
        self.db.execute('INSERT INTO autochess_stage VALUES(?,?,?)', (UID, 102, 1234))
        self.db.execute('INSERT INTO autochess_medal VALUES(?,?,?,?,?)', (UID, 1, 3, 1234, 2345))
        self.db.execute('INSERT INTO task(uid,task_id,progress,complete_flag,claimed_ts) VALUES(?,?,?,?,?)',
                        (UID, 9999, 1, 1, 1234))
        self.db.execute('INSERT INTO activity_point_reward VALUES(?,?,?,?)', (UID, 3740601, 9999, 1234))
        tables = ('currency', 'hero', 'autochess_stage', 'autochess_medal', 'task', 'activity_point_reward')
        before = {t: self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in tables}
        self.assert_snapshot(self.flood()[0], 0)
        self.assertEqual({t: self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in tables}, before)
        self.assertEqual(self.recoveries()[0]['session']['chess_bonus'], {'101': [7, 8]})

    def test_orphan_archive_and_both_cancel_writes_roll_back_after_actual_write(self):
        self.battle(); self.edit(pending_round=None)
        before = self.stored()
        execute = self.db.execute
        for target in ('archive', 'session', 'runtime'):
            with self.subTest(target=target):
                def fail(sql, params=(), **kw):
                    result = execute(sql, params, **kw)
                    match = ('INSERT OR REPLACE INTO autochess_session' in sql if target == 'session'
                             else 'INSERT INTO ops_common' in sql and params[1] ==
                             (recovery.RECOVERY_KIND if target == 'archive' else self.svc.SESSION_KIND))
                    if match: raise RuntimeError('isolated post-write fault: '+target)
                    return result
                with patch.object(self.db, 'execute', side_effect=fail):
                    with self.assertRaises(RuntimeError): self.flood()
                self.assertEqual(self.stored(), before)
                self.assertEqual(self.svc.get_session(UID)['state'], 3)
        self.assert_snapshot(self.flood()[0], 0)

    def test_orphan_cancel_and_archive_rollback_on_invalid_required_state_frame(self):
        self.battle(); self.edit(pending_round=None)
        before = self.stored(); encoder = recovery.encode
        for bad in (None, b''):
            with self.subTest(bad=bad):
                def fail(name, data):
                    return bad if name == 'sc_89125' else encoder(name, data)
                with patch.object(recovery, 'encode', side_effect=fail):
                    with self.assertRaises(RuntimeError): self.flood()
                self.assertEqual(self.stored(), before)
        self.assert_snapshot(self.flood()[0], 0)

    def test_orphan_recovery_isolated_savepoint_when_outer_catches_failure(self):
        self.battle(); self.edit(pending_round=None)
        before = self.stored()
        with self.db.transaction():
            self.db.set_currency(UID, 80, 123)
            with patch.object(recovery, 'encode', return_value=None):
                with self.assertRaises(RuntimeError): self.flood()
            self.assertEqual(self.stored(), before)
        self.assertEqual(self.balance(80), 123)

    def test_hidden_login_policy_does_not_cancel_orphan(self):
        self.battle(); self.edit(pending_round=None)
        before = self.stored()
        rows, payloads = recovery.prepare_login_frames([], self.db, UID, generator,
                                                       NS(allows=lambda cmd: False), lambda *a: None)
        self.assertEqual((rows, payloads), ([], {}))
        self.assertEqual(self.stored(), before)

    def test_foreign_replay_identity_is_refused_without_rewriting_save(self):
        self.battle()
        session = deepcopy(self.svc.get_session(UID))
        session['last_replay']['info']['user_id1'] = UID+1
        self.edit(**session); before = self.stored()
        with self.assertRaises(RuntimeError): self.flood()
        self.assertEqual(self.stored(), before)

    def test_multiplayer_capture_or_saved_session_never_emits_multiplayer_state(self):
        self.start(); self.edit(game_type=1, state=4)
        before = self.stored()
        self.assert_snapshot(self.flood()[0], 0)
        self.assertEqual(self.stored(), before)

    def test_read_or_encode_failure_never_falls_back_to_captured_state(self):
        self.battle()
        before = self.stored()
        with patch.object(recovery, 'encode', return_value=None):
            with self.assertRaises(RuntimeError): self.flood()
        query = self.db.query
        def fail(sql, *a, **kw):
            if 'FROM autochess_session' in sql: raise RuntimeError('isolated read fault')
            return query(sql, *a, **kw)
        with patch.object(self.db, 'query', side_effect=fail):
            with self.assertRaises(RuntimeError): self.flood()
        self.assertEqual(self.stored(), before)

    def test_actions_only_sent_when_replay_requested(self):
        original = self.battle()
        packets, _ = self.flood()
        self.assertNotIn(89113, dict(packets))
        ack, response = self.op(89142)
        self.assertEqual(ack['result'], 0)
        self.assertEqual(ack['replay_info'], original['replay_info'])
        self.assertIn(89113, response.scs)


if __name__ == '__main__': unittest.main(verbosity=2)
