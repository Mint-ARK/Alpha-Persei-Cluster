"""A369: source-selected middleware, real dynamic payload/TeamServer and memory SQL."""
import ast
import copy
import json
import re
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_issue_hero_payload import MemoryDB, UID, HERO_ID, _insert_hero
from verify_mainline_trial_entry import entry_heroes, wire_fields
import battle_payload
import codec
import team_server

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / 'archive/battle_entry_fail_closed_20261022'


def selected(path, names, env):
    tree = ast.parse(path.read_text('utf-8-sig'))
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), env)


class BattleEntryFailureTests(unittest.TestCase):
    def setUp(self):
        memory = MemoryDB()
        self.addCleanup(memory.close)
        self.sql = memory.conn
        self.sql.executescript("""CREATE TABLE users(uid INTEGER,nickname TEXT,level INTEGER);
          INSERT INTO users VALUES(42,'current-account',60);
          CREATE TABLE currency(uid INTEGER,id INTEGER,num INTEGER);
          INSERT INTO currency VALUES(42,4,180);
          CREATE TABLE chapter_v2_backtrack(uid INTEGER PRIMARY KEY,map_id INTEGER,
            start_event INTEGER,now_location INTEGER,token TEXT,update_ts INTEGER);
          CREATE TABLE rogue_test_state(uid INTEGER PRIMARY KEY,node INTEGER);
          INSERT INTO rogue_test_state VALUES(42,99);""")
        tree = ast.parse((ROOT / 'account_db.py').read_text('utf-8-sig'))
        schema = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in n.targets))
        self.sql.executescript(re.search(r'CREATE TABLE IF NOT EXISTS stage_team\s*\(.*?\);', schema, re.S)[0])
        _insert_hero(memory)
        self.db = types.SimpleNamespace(_conn=lambda: self.sql, query=memory.query, execute=self.sql.execute)
        self.env = dict(time=time, battle_payload=battle_payload, _BATTLE_LOCK=threading.Lock(),
                        _BATTLE_SESSIONS={}, _BATTLE_PORT=[9000], _BATTLE_IP=['127.0.0.1'])
        names = {'CSRequest', 'CSResponse', 'DownFrame', '_varint', 'rd_varint', '_pb_fields',
                 '_pb_field_bytes', 'rewrite_54003_heroes', '_parse_battle_common', '_gen_battle_id',
                 'register_battle_session', '_chapter_map_battle_token', '_boss_battle_context', '_boss_mode_signature', 'h_54030',
                 '_error_payload', '_error_response', 'handle_request'}
        selected(ROOT / 'middleware.py', names, self.env)
        self.env['_CS2SC'] = {54030: 54031}
        self.env['CORE_HANDLERS'] = {54030: self.env['h_54030']}
        self.env['get_skeleton'] = Mock(side_effect=AssertionError('Unexpected skeleton fallback'))
        self.template = Mock(return_value=(ROOT / 'sc_54003_payload.bin').read_bytes())
        self.logs = []
        self.ctx = types.SimpleNamespace(db=self.db, battle_seq=[7], battle_id=[777], battle_stage=[1031900],
            hero_last_payload=[b'previous-battle'], codec_encode=codec.encode, codec_decode=codec.decode,
            log=lambda *a, **kw: self.logs.append(a), battle_03_payload=self.template)
        self.env['_BATTLE_SESSIONS'][777] = {'uid': UID, 'dest': 1031900, 'heroes': [HERO_ID]}
        team = team_server.TeamServer()
        self.team_patch = patch.object(team_server.TeamServer, 'get_instance', return_value=team)
        self.team_patch.start()
        self.addCleanup(self.team_patch.stop)
        team.record_stage_team(self.ctx, UID, 1, 1032302, heroes=[HERO_ID], cooperate_skill=123)

    def request(self, heroes=None, stage_type=1, dest=1032302, uid=UID):
        if heroes is None:
            heroes = [{'hero_id': HERO_ID, 'hero_type': 1}, {'hero_id': 318, 'hero_type': 2}]
        raw = codec.encode('cs_54030', {'common_info': dict(hero_list=heroes, type=stage_type,
            dest=dest, activity_id=0, battle_times=1, cooperate_unique_skill_id=0,
            cooperate_unique_skill_level=0), 'is_restarted_battle': 0})
        req = self.env['CSRequest'](54030, raw, uid=uid)
        return self.env['handle_request'](req, self.ctx)

    def snapshot(self):
        return copy.deepcopy(dict(sessions=self.env['_BATTLE_SESSIONS'],
            runtime=[self.ctx.battle_seq[0], self.ctx.battle_id[0], self.ctx.battle_stage[0],
                     self.ctx.hero_last_payload[0]],
            currency=self.db.query('SELECT * FROM currency'),
            teams=self.db.query('SELECT * FROM stage_team'),
            rogue=self.db.query('SELECT * FROM rogue_test_state')))

    def assert_failed(self, response, before):
        self.assertEqual(response.cmds, [54031])
        self.assertNotEqual(codec.decode(response.frames[0].payload, 'sc_54031')['result'], 0)
        self.assertEqual(self.snapshot(), before)
        self.template.assert_not_called()
        self.env['get_skeleton'].assert_not_called()

    @unittest.skipUnless((EVIDENCE / 'before/v5_server/middleware.py').is_file(),
                         'Pre-fix reproduction requires this repair archive')
    def test_before_source_reproduces_stale_and_missing_hero_packet_success(self):
        before_file = EVIDENCE / 'before/v5_server/middleware.py'
        selected(before_file, {'h_54030'}, self.env)
        self.env['CORE_HANDLERS'][54030] = self.env['h_54030']
        records = []
        for cached in (True, False):
            self.template.return_value = (ROOT / 'sc_54003_payload.bin').read_bytes() if cached else None
            with patch.object(battle_payload, 'build_sc_54003_dynamic', side_effect=RuntimeError('injected builder failure')):
                response = self.request()
            self.assertEqual(codec.decode(response.frames[0].payload, 'sc_54031')['result'], 0)
            self.assertIn(54007, response.cmds)
            if cached:
                self.assertIn(54003, response.cmds)
                raw = next(f.payload for f in response.frames if f.cmd == 54003)
                player = next(v for f, v in wire_fields(raw) if f == 1)
                battle = next(v for f, v in wire_fields(player) if f == 2)
                heroes = [codec.decode(v, 'battle_hero_net_rec') for f, v in wire_fields(battle) if f == 4]
            else:
                self.assertNotIn(54003, response.cmds)
                heroes = []
            records.append(dict(cache_present=cached, cmds=response.cmds,
                                heroes=heroes, session_published=self.ctx.battle_id[0] in self.env['_BATTLE_SESSIONS']))
        (EVIDENCE / 'before_reproduction.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), 'utf-8')

    def test_builder_exception_rejects_with_or_without_cache_and_preserves_previous_battle(self):
        before = self.snapshot()
        for cache in ((ROOT / 'sc_54003_payload.bin').read_bytes(), None):
            self.template.return_value = cache
            with patch.object(battle_payload, 'build_sc_54003_dynamic', side_effect=RuntimeError('injected failure')):
                self.assert_failed(self.request(), before)

    def test_missing_or_nonbytes_initialization_never_falls_back(self):
        before = self.snapshot()
        for payload in (None, b'', 'not encoded'):
            with patch.object(battle_payload, 'build_sc_54003_dynamic', return_value=payload):
                self.assert_failed(self.request(), before)

    def test_partial_missing_hero_is_rejected_instead_of_silently_omitted(self):
        before = self.snapshot()
        heroes = [{'hero_id': HERO_ID, 'hero_type': 1}, {'hero_id': 99999999, 'hero_type': 1}]
        self.assert_failed(self.request(heroes=heroes), before)

    def test_unknown_account_cannot_reuse_first_accounts_profile(self):
        before = self.snapshot()
        self.assert_failed(self.request(uid=43, heroes=[{'hero_id': 318, 'hero_type': 2}]), before)

    def test_missing_database_uid_and_empty_team_do_not_publish(self):
        before = self.snapshot()
        self.assert_failed(self.request(heroes=[]), before)
        self.assert_failed(self.request(uid=0), before)
        self.ctx.db = None
        self.assert_failed(self.request(), before)

    def test_start_encoding_exception_or_empty_packet_preserves_state(self):
        before = self.snapshot()
        for fail in (Mock(return_value=None), Mock(side_effect=ValueError('injected start encode failure'))):
            self.ctx.codec_encode = fail
            self.assert_failed(self.request(), before)

    def test_dynamic_sql_failure_leaves_team_session_and_fatigue_unchanged(self):
        self.sql.execute('ALTER TABLE equip RENAME TO equip_fault_fixture')
        before = self.snapshot()
        self.assert_failed(self.request(), before)

    def test_session_registration_failure_does_not_replace_context_or_team(self):
        before = self.snapshot()
        self.env['register_battle_session'] = Mock(side_effect=RuntimeError('injected registration failure'))
        self.assert_failed(self.request(), before)

    def test_retry_after_failure_succeeds_once_with_current_mixed_team(self):
        before = self.snapshot()
        with patch.object(battle_payload, 'build_sc_54003_dynamic', side_effect=RuntimeError('first attempt failed')) as build:
            self.assert_failed(self.request(), before)
            build.assert_called_once()
        response = self.request()
        self.assertEqual(response.cmds, [54031, 54003, 54007])
        heroes = entry_heroes(response.frames[1].payload)
        self.assertEqual([h['hero_base_info']['id'] for h in heroes], [HERO_ID, 318])
        self.assertEqual([h['hero_type'] for h in heroes], [1, 2])
        self.assertEqual(heroes[0]['hero_base_info']['exp'], 31)
        self.assertEqual(self.ctx.battle_seq[0], 8)
        self.assertEqual(len(self.env['_BATTLE_SESSIONS']), 2)
        start = codec.decode(response.frames[2].payload, 'sc_54007')['battle_start']
        self.assertEqual(start['battle_id'], self.ctx.battle_id[0])
        self.assertEqual(self.env['_BATTLE_SESSIONS'][start['battle_id']]['heroes'], [HERO_ID, 318])
        self.assertEqual(self.snapshot()['currency'], before['currency'])
        self.assertEqual(json.loads(self.snapshot()['teams'][0]['hero_json']), [HERO_ID])
        self.template.assert_not_called()

    def test_polyhedron_inferred_team_still_initializes_and_failure_does_not_publish(self):
        poly = types.SimpleNamespace(PolyhedronRunManager=types.SimpleNamespace(
            load_run=lambda *a: {'fight_hero_id_list': [318]}))
        before = self.snapshot()
        with patch.dict(sys.modules, {'polyhedron_service': poly}):
            with patch.object(battle_payload, 'build_sc_54003_dynamic', return_value=None):
                self.assert_failed(self.request(heroes=[], stage_type=52, dest=3035001), before)
            response = self.request(heroes=[], stage_type=52, dest=3035001)
        self.assertEqual(response.cmds, [54031, 54003, 54007])
        self.assertEqual(entry_heroes(response.frames[1].payload)[0]['hero_base_info']['id'], 318)

    def test_rogue_selection_waits_for_required_packets_and_success_keeps_mapping(self):
        svc = types.SimpleNamespace(is_valid_node=lambda *a: (True, 100, ''),
            get_session_data=lambda *a: {'map_info': [{'node_id': 100, 'param': 1032302}]},
            set_selected_node=lambda uid, node: self.sql.execute('UPDATE rogue_test_state SET node=? WHERE uid=?', (node, uid)))
        rogue = types.SimpleNamespace(RogueTeamService=types.SimpleNamespace(get_instance=lambda **kw: svc))
        run_state={'in_game':1,'select_node_id':100,'flow_json':'{"run_id":"entry-test"}'}
        rogue_flow=types.SimpleNamespace(row=lambda *a:run_state,flow=lambda state:json.loads(state['flow_json']))
        before = self.snapshot()
        with patch.dict(sys.modules, {'rogueteam_service': rogue,'rogueteam_flow':rogue_flow}):
            with patch.object(battle_payload, 'build_sc_54003_dynamic', return_value=None):
                self.assert_failed(self.request(stage_type=78, dest=100), before)
            response = self.request(stage_type=78, dest=100)
        self.assertEqual([f.cmd for f in response.frames if f.cmd in (54031, 54003, 54007)], [54031, 54003, 54007])
        self.assertEqual(self.ctx.battle_stage[0], 1032302)
        self.assertEqual(self.db.query('SELECT node FROM rogue_test_state'), [{'node': 100}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
