"""Real Core checks: native 89212 only skips closed area-4 tutorial 401."""
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import autochess_progress as progress
import autochess_service as ac


class SkipGuardTests(MemoryTests):
    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('autochess_stage', 'autochess_medal', 'autochess_session', 'ops_common',
                 'currency', 'material', 'task', 'claim_ledger')}

    def skip(self, stage):
        return self.call(89212, dict(stage_id=stage), 89213)

    def assert_rejected(self, stage):
        before = self.state()
        packet, response = self.skip(stage)
        self.assertEqual(packet, dict(result=17))
        self.assertEqual(response.scs, [89213])
        self.assertEqual(self.state(), before)

    def test_all_phase1_stages_require_normal_completion(self):
        self.assertEqual(len(progress.STAGES), 12)
        for stage in progress.STAGES:
            with self.subTest(stage=stage): self.assert_rejected(stage)

    def test_closed_tutorial_and_unknown_stages_stay_closed(self):
        for stage in (401, 0, 999999):
            with self.subTest(stage=stage): self.assert_rejected(stage)

    def test_repeated_skip_preserves_old_clear_time_medal_and_assets(self):
        self.db.execute('INSERT INTO autochess_stage(uid,stage_id,clear_time) VALUES(?,101,123)', (UID,))
        self.db.execute('INSERT INTO autochess_medal(uid,medal_id,level,unlock_time,upgrade_time) VALUES(?,1001,2,100,200)', (UID,))
        self.db.set_currency(UID, 80, 4)
        self.assert_rejected(101)
        self.assert_rejected(101)

    def test_during_preparation_or_pending_round_never_changes_session_or_grants(self):
        self.assertEqual(self.call(89116, dict(game_type=0, other_param=101), 89117)[0]['result'], 0)
        self.assert_rejected(104)
        self.assertEqual(self.call(89144, dict(game_type=0), 89145)[0]['result'], 0)
        self.assertEqual(self.call(89108, dict(game_type=0), 89109)[0]['result'], 0)
        with patch.object(progress, 'settle_completed', side_effect=AssertionError('skip must not grant')):
            self.assert_rejected(101)
        self.assertEqual(ac.AutoChessService.get_instance(self.db).get_session(UID)['state'], 3)


if __name__ == '__main__': unittest.main(verbosity=2)
