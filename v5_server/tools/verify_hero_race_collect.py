"""A151: memory-only Core/Inventory/codec checks against the current public Lua roster."""
import json
from collections import Counter
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from extract_autochess_cfg import parse_lua_file
import codec
import generator
import illustrated_listener as illustrated

ROOT = Path(__file__).resolve().parents[2]
HEROES = parse_lua_file(ROOT / 'decompiled_v2/x64/game/config/herocfg.lua')
PUBLIC = {hid: row['race'] for hid, row in HEROES.items()
          if isinstance(hid, int) and row.get('private') == 0}
SETTINGS = parse_lua_file(ROOT / 'decompiled_v2/x64/game/config/gamesetting.lua')
STEP = SETTINGS['hero_race_collect_each_tier']['value'][0]
REWARD = dict(SETTINGS['hero_race_collect_reward_list']['value'])
LIMITS = Counter(PUBLIC.values())
TIERS = {race: list(range(STEP, count + 1, STEP)) for race, count in LIMITS.items()}
MISSING = {2: [12], 4: [12, 15], 5: [12, 15], 9: [9, 12]}


class RaceCollectTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for iid, kind in ((1, 1), (2, 1), (40103, 4)):
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)', (iid, kind))

    def own(self, race=None, count=None):
        ids = sorted(hid for hid, r in PUBLIC.items() if race is None or r == race)
        for hid in ids[:count]:
            self.db.execute('INSERT OR REPLACE INTO hero(uid,id,unlock) VALUES(?,?,1)', (UID, hid))
        return ids

    def claim(self, entries):
        return self.call(52022, dict(collect_info_list=entries), 52023)

    def all_requests(self):
        return [dict(race_type=r, cnt_list=needs) for r, needs in sorted(TIERS.items())]

    def snapshot(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid')
                for t in ('hero_race_collect', 'currency', 'material')}

    def assert_awards(self, count):
        for iid, num in REWARD.items():
            self.assertEqual(self.balance(iid), num * count)

    def reject(self, entries, code=2):
        before = self.snapshot()
        answer, response = self.claim(entries)
        self.assertEqual(answer['result'], code)
        self.assertEqual(response.scs, [52023])
        self.assertEqual(self.snapshot(), before)

    def test_exported_roster_and_tiers_match_current_lua(self):
        cfg = illustrated._get_hero_race_cfg()
        self.assertEqual(len(PUBLIC), 84)
        self.assertEqual(cfg.get('public_hero_races'), {str(h): r for h, r in PUBLIC.items()})
        self.assertEqual({int(r): [t['need'] for t in tasks] for r, tasks in cfg['by_race'].items()}, TIERS)
        self.assertEqual(sum(len(v) for v in TIERS.values()), 26)
        for task in cfg['tasks'].values():
            self.assertEqual({v['id']: v['num'] for v in task['reward']}, REWARD)

    def test_seven_missing_tiers_reject_before_threshold_then_grant(self):
        for race, needs in MISSING.items():
            for need in needs:
                with self.subTest(race=race, need=need):
                    self.db.execute('DELETE FROM hero WHERE uid=?', (UID,))
                    ids = self.own(race, need - 1)
                    request = [dict(race_type=race, cnt_list=[need])]
                    self.reject(request, 2005)
                    self.db.execute('INSERT INTO hero(uid,id,unlock) VALUES(?,?,1)', (UID, ids[need - 1]))
                    self.assertEqual(self.claim(request)[0]['result'], 0)
        self.assert_awards(7)

    def test_all_twenty_six_rewards_and_cold_history(self):
        self.own()
        answer, response = self.claim(self.all_requests())
        self.assertEqual(answer['result'], 0)
        self.assert_awards(26)
        self.assertEqual({v['id']: v['num'] for v in answer['item_list']}, {iid: num * 26 for iid, num in REWARD.items()})
        cold = codec.decode(generator.gen_payload(52025, uid=UID, db=self.db), 'sc_52025')
        self.assertEqual({v['race_type']: v.get('cnt_list', []) for v in cold['hero_race_collect']}, TIERS)
        self.assertIn(17023, response.scs)

    def test_old_nineteen_claims_preserved_only_seven_new_awards(self):
        self.own()
        for race, needs in TIERS.items():
            old = [n for n in needs if n not in MISSING.get(race, [])]
            self.db.execute('INSERT INTO hero_race_collect(uid,race_type,received_cnt_list) VALUES(?,?,?)', (UID, race, json.dumps(old)))
        self.assertEqual(self.claim(self.all_requests())[0]['result'], 0)
        self.assert_awards(7)
        self.assertEqual(self.claim(self.all_requests())[0]['result'], 0)
        self.assert_awards(7)

    def test_duplicate_races_tiers_and_replay_are_idempotent(self):
        self.own(1, 6)
        request = [dict(race_type=1, cnt_list=[3, 3]), dict(race_type=1, cnt_list=[6, 3])]
        self.assertEqual(self.claim(request)[0]['result'], 0)
        self.assert_awards(2)
        before = self.snapshot()
        self.assertEqual(self.claim(request)[0]['result'], 0)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_or_ineligible_batch_never_partially_claims(self):
        self.own(1, 3)
        valid = dict(race_type=1, cnt_list=[3])
        for invalid, code in ((dict(race_type=1, cnt_list=[4]), 2),
                              (dict(race_type=1, cnt_list=[99]), 2),
                              (dict(race_type=99, cnt_list=[3]), 2),
                              (dict(race_type=2, cnt_list=[3]), 2005)):
            self.reject([valid, invalid], code)

    def test_locked_and_private_rows_do_not_count_as_public_owned(self):
        ids = self.own(1, 2)
        self.db.execute('INSERT INTO hero(uid,id,unlock,clear_times) VALUES(?,?,0,10)', (UID, ids[2]))
        private = next(h for h, row in HEROES.items() if isinstance(h, int) and row.get('private') != 0 and row['race'] == 1)
        self.db.execute('INSERT INTO hero(uid,id,unlock) VALUES(?,?,1)', (UID, private))
        self.assertEqual(illustrated.get_hero_race_counts(self.db, UID).get(1), 2)
        self.reject([dict(race_type=1, cnt_list=[3])], 2005)

    def test_empty_requests_are_not_success(self):
        for request in ([], [dict(race_type=1, cnt_list=[])]):
            self.reject(request)

    def test_history_write_failure_rolls_back_awards_then_retry(self):
        self.own(1, 3)
        before = self.snapshot()
        real = self.db.execute
        def fail(sql, *args, **kwargs):
            if sql.startswith('INSERT OR REPLACE INTO hero_race_collect'):
                raise RuntimeError('isolated claim history failure')
            return real(sql, *args, **kwargs)
        request = [dict(race_type=1, cnt_list=[3])]
        with patch.object(self.db, 'execute', side_effect=fail):
            self.assertNotEqual(self.claim(request)[0]['result'], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.claim(request)[0]['result'], 0)
        self.assert_awards(1)

    def test_necessary_ack_or_asset_frame_failure_rolls_back(self):
        self.own(1, 3)
        request = [dict(race_type=1, cnt_list=[3])]
        for name in ('ack', 'asset'):
            with self.subTest(name=name):
                before = self.snapshot()
                if name == 'ack':
                    real = codec.encode
                    def fail(msg, *args, **kwargs):
                        return None if msg == 'sc_52023' else real(msg, *args, **kwargs)
                    fault = patch.object(self.conn.to_handler_ctx(), 'codec_encode', side_effect=fail)
                else:
                    real = generator.gen_payload
                    def fail(cmd, *args, **kwargs):
                        return None if cmd == 17023 else real(cmd, *args, **kwargs)
                    fault = patch.object(generator, 'gen_payload', side_effect=fail)
                with fault:
                    self.assertNotEqual(self.claim(request)[0]['result'], 0)
                self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
