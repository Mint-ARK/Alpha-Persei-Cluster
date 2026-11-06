"""A160: real interaction Core, growth producer, task SQL and online codec; memory only."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from extract_autochess_cfg import parse_lua_file
import codec
from core import CoreRequest
from event_bus import bus, Events
from hero_service import HeroService
import task_listener

ROOT = Path(__file__).resolve().parents[2]
TASK = parse_lua_file(ROOT / 'decompiled_v2/x64/game/config/assignmentcfg.lua')[6013]


class InteractionTests(MemoryTests):
    def setUp(self):
        super().setUp()
        handlers = patch.dict(bus._handlers, {event: list(items) for event, items in bus._handlers.items()}, clear=True)
        handlers.start()
        self.addCleanup(handlers.stop)
        self.db.execute('INSERT INTO hero(uid,id,unlock,clear_times) VALUES(?,1084,1,0)', (UID,))
        self.db.execute('INSERT INTO game_user(uid,board_hero) VALUES(?,1084)', (UID,))
        self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,additional_parameter) VALUES(?,?,?,?,?)',
                        (6013, TASK['type'], TASK['condition'], TASK['need'], json.dumps(TASK['additional_parameter'])))
        self.db.execute('INSERT INTO task(uid,task_id,task_type,progress) VALUES(?,6013,?,0)', (UID, TASK['type']))

    def row(self, uid=UID):
        return self.db.get('task', uid, 'AND task_id=6013')

    def touch(self):
        return self.call(32054, {}, 32055)

    def snapshot(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid')
                for t in ('task', 'user_behavior_stat', 'users')}

    def diffs(self, response):
        return [row for f in response.frames if f['sc'] == 28007
                for row in codec.decode(f['payload'], 'sc_28007').get('progress_list', [])]

    def test_condition_eight_is_interaction_in_current_lua(self):
        self.assertEqual((TASK['condition'], TASK['type'], TASK['need']), (8, 6, 1))
        self.assertIn('交互', TASK['desc'])

    def test_all_growth_events_leave_interaction_task_unchanged(self):
        received = []
        bus.subscribe(Events.HERO_UPGRADE, lambda ctx, uid, **kw: received.append((ctx, uid, kw)))
        ctx = self.conn.to_handler_ctx()
        for oper in ('proficiency_up', 'level_up', 'break_up', 'skill_upgrade', 'star_up',
                     'skill_element_upgrade', 'weapon_upgrade', 'transition_upgrade', None):
            with self.subTest(oper=oper):
                bus.emit(Events.HERO_UPGRADE, ctx, UID, hero_id=1084, times=3, oper=oper)
                self.assertEqual(self.row()['progress'], 0)
        self.assertEqual(len(received), 9)
        self.assertTrue(all(owner is ctx and uid == UID for owner, uid, _ in received))

    def test_actual_battle_and_sweep_growth_preserved_without_interaction(self):
        ctx = self.conn.to_handler_ctx()
        received = []
        bus.subscribe(Events.HERO_UPGRADE, lambda ctx, uid, **kw: received.append(kw))
        svc = HeroService(self.db)
        for times in (1, 3):
            svc.on_battle_result(ctx, UID, [1084], dest=1030101, times=times, grant_exp=False)
        hero = self.db.get('hero', UID, 'AND id=1084')
        archive = self.db.get('hero_archive', UID, 'AND archive_id=1084')
        self.assertEqual((hero['clear_times'], hero['battle_win_times'], archive['exp']), (4, 4, 20))
        self.assertEqual(self.row()['progress'], 0)
        self.assertEqual([v['oper'] for v in received], ['proficiency_up', 'proficiency_up'])

    def test_actual_touch_completes_and_pushes_before_ack(self):
        answer, response = self.touch()
        self.assertEqual(answer['result'], 0)
        self.assertEqual(self.row()['progress'], 1)
        self.assertEqual([(v['id'], v['progress']) for v in self.diffs(response)], [(6013, 1)])
        self.assertLess(response.scs.index(28007), response.scs.index(32055))
        stats = self.db.query("SELECT value FROM user_behavior_stat WHERE uid=? AND stat_key='main_interact' AND hero_id=1084", (UID,))
        self.assertEqual(stats[0]['value'], 1)

    def test_repeated_touch_does_not_repeat_completed_task_diff(self):
        self.assertEqual(self.touch()[0]['result'], 0)
        answer, response = self.touch()
        self.assertEqual(answer['result'], 0)
        self.assertEqual(self.row()['progress'], 1)
        self.assertEqual(self.diffs(response), [])
        extra = json.loads(self.db.get('users', UID)['extra'])
        self.assertEqual(extra['daily_touch_counts']['1084'], 2)

    def test_claimed_history_is_not_reopened(self):
        self.db.execute('UPDATE task SET progress=1,complete_flag=1,claimed_ts=100 WHERE uid=?', (UID,))
        before = self.row()
        self.assertEqual(self.touch()[0]['result'], 0)
        self.assertEqual(self.row(), before)

    def test_touch_ack_failure_rolls_back_progress_counts_then_retry(self):
        before = self.snapshot()
        real = codec.encode
        def fail(name, *args, **kwargs):
            return None if name == 'sc_32055' else real(name, *args, **kwargs)
        with patch.object(self.conn.to_handler_ctx(), 'codec_encode', side_effect=fail):
            answer, response = self.touch()
        self.assertNotEqual(answer['result'], 0)
        self.assertEqual(response.scs, [32055])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.touch()[0]['result'], 0)
        self.assertEqual(self.row()['progress'], 1)

    def test_interaction_only_updates_current_uid(self):
        self.db.execute("INSERT INTO users(uid,nick) VALUES(43,'other-memory-user')")
        self.db.execute('INSERT INTO task(uid,task_id,task_type,progress) VALUES(43,6013,6,0)')
        self.assertEqual(self.touch()[0]['result'], 0)
        self.assertEqual(self.row()['progress'], 1)
        self.assertEqual(self.row(43)['progress'], 0)


if __name__ == '__main__':
    unittest.main()
