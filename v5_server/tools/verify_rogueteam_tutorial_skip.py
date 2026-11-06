"""New save presets skip only Rogue tier101; memory saves and real wire codec."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from verify_save_generator import SaveGeneratorTests, UID as SAVE_UID
from verify_rogueteam_login import RogueLoginTests, UID
from save_presets import PRESET_SLOTS
import codec
import generator
import new_player_service as fresh
import rogueteam_login as recovery


class PresetTests(SaveGeneratorTests):
    def test_tutorial_all_presets_mark_skip_independently_of_501(self):
        for preset in PRESET_SLOTS.values():
            with self.subTest(preset=preset.key):
                db, _ = self.build(replace(preset.options, uid=SAVE_UID, support_mail=False))
                self.assertEqual(db.get_system_config('save.rogueteam_tutorial'), 'skip')
                self.assertEqual(db.get_system_config('save.owner_uid'), str(SAVE_UID))
                self.assertEqual(fresh.gateway_new_player(db, SAVE_UID), not preset.options.skip_intro)
                rows = db.query('SELECT * FROM guide WHERE uid=?', (SAVE_UID,))
                if not preset.options.skip_intro and not preset.options.skip_feature_guides:
                    self.assertEqual(rows, [])
                # Even before this mode's gameplay opens, the sole boolean
                # can be sent without granting stages or other feature access.
                policy = SimpleNamespace(allows=lambda cmd: False)
                _, payloads = recovery.prepare_login_frames([], db, SAVE_UID, generator, policy)
                self.assertEqual(set(payloads), {88029})
                self.assertEqual(codec.decode(payloads[88029], 'sc_88029'),
                                 dict(template_id=100001, finish_guide=1))


class LoginTests(RogueLoginTests):
    def mark(self):
        self.db.set_system_config('save.owner_uid', str(UID))
        self.db.set_system_config('save.rogueteam_tutorial', 'skip')

    def rows(self):
        raw = codec.encode('sc_88029', dict(template_id=100001, finish_guide=0))
        return [(1, 88029, b'', raw, 0, None), (2, 88029, b'', raw, 0, None),
                (3, 10201, b'', b'', 0, None)]

    def prepare(self, rows, uid=UID):
        return recovery.prepare_login_frames(rows, self.db, uid, generator,
                                            SimpleNamespace(allows=lambda cmd: False))

    def test_tutorial_new_owner_replaces_duplicate_captured_flags_before_login(self):
        self.mark()
        before = self.state()
        result, payloads = self.prepare(self.rows())
        self.assertEqual([row[1] for row in result], [88029, 10201])
        self.assertEqual(codec.decode(payloads[88029], 'sc_88029')['finish_guide'], 1)
        self.assertEqual(self.state(), before)

    def test_tutorial_missing_capture_still_sends_required_skip(self):
        self.mark()
        result, payloads = self.prepare(self.rows()[-1:])
        self.assertEqual([row[1] for row in result], [88029, 10201])
        self.assertEqual(codec.decode(payloads[88029], 'sc_88029')['finish_guide'], 1)

    def test_tutorial_legacy_and_foreign_uid_keep_existing_behavior(self):
        original = self.rows()
        self.assertEqual(self.prepare(original), (original, {}))
        self.mark()
        self.assertEqual(self.prepare(original, uid=UID + 1), (original, {}))

    def test_tutorial_empty_guide_frame_fails_without_mutating_state_or_input(self):
        self.mark()
        original = self.rows()
        before = self.state()
        from core import OperationError
        with patch.object(recovery, 'encode', return_value=None):
            with self.assertRaises(OperationError):
                self.prepare(original)
        self.assertEqual(original, self.rows())
        self.assertEqual(self.state(), before)


if __name__ == '__main__':
    suite = unittest.TestSuite()
    for cls in (PresetTests, LoginTests):
        suite.addTests(cls(name) for name in dir(cls) if name.startswith('test_tutorial_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
