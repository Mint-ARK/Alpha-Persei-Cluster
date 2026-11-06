"""Legacy Polyhedron must not grant RogueTeam achievements; memory-only."""
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import achievement_service as achievement
import codec
import task_listener
from event_bus import bus, Events


class BoundaryTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc = achievement.AchievementService(self.db)
        self.svc.init_user_achievements(UID)
        self.ids = [r['id'] for r in achievement._CFG.values()
                    if r['condition'] in (300005, 300006, 300009, 300012, 300013, 300014)]
        self.assertTrue(self.ids)
        self.assertTrue(all('虚构推演' in achievement._CFG[i]['desc'] for i in self.ids))

    def rows(self):
        return [self.db.get('achievement', UID, 'AND achievement_id=?', (i,))
                for i in self.ids]

    def test_polyhedron_unlocks_do_not_prove_rogue_progress(self):
        for table, field, values in (
            ('polyhedron_difficulty', 'difficulty_id', [19]),
            ('polyhedron_beacon', 'beacon_id', range(1, 15)),
            ('polyhedron_artifact', 'artifact_id', range(70501, 70531)),
            ('polyhedron_hero', 'hero_id', range(1001, 1031)),
        ):
            for value in values:
                self.db.upsert(table, UID, {field: value}, keys=('uid', field))
        before = self.rows()
        wire = codec.decode(self.svc.get_achievement_login_payload(UID), 'sc_53001')
        self.assertEqual(self.rows(), before)
        self.assertIsInstance(wire, dict)

    def test_completion_or_abandonment_preserves_participation_only(self):
        before = self.rows()
        with patch.object(task_listener, '_trigger_task_condition', return_value=[]) as task:
            for full in (False, True):
                bus.emit_required(Events.POLYHEDRON_PASS, self.conn.to_handler_ctx(), UID,
                                  is_full_clear=full, difficulty=19,
                                  terminals=[1, 2, 3, 6], beacons=list(range(1, 15)),
                                  artifacts=list(range(30)), heroes=list(range(30)),
                                  beacon_kill=True)
            self.assertEqual(task.call_count, 2)
            self.assertTrue(all(c.kwargs == dict(condition=423, delta=1)
                                for c in task.call_args_list))
        self.assertEqual(self.rows(), before)

    def test_existing_claim_history_is_preserved(self):
        self.db.execute('UPDATE achievement SET progress=1,complete_flag=1,achieve_time=123456 '
                        'WHERE uid=? AND achievement_id=63001', (UID,))
        before = self.rows()
        self.svc.get_achievement_login_payload(UID)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.balance(1), 0)


if __name__ == '__main__':
    unittest.main()
