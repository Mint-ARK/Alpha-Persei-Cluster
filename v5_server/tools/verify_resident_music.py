"""Resident music native Core/SQL/codec probes; SQLite memory only."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
import verify_minigame_point_rewards as point_tests
import codec
import core
import generator
import minigame_service as mini
import new_player_service
import resident_music_service as music
from account_db import AccountDB
from event_bus import bus, Events
from inventory_service import InventoryService

ROOT = Path(__file__).resolve().parents[2]
TASKS = json.loads((ROOT / 'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class MusicTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock = patch('time.time', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        for row in TASKS.values():
            if row['condition'] == 50104:
                self.add_task(row)
        reward_items = {i for c in music.CATALOG.values() for i, n in c['rewards']}
        reward_items.update(i for r in TASKS.values() if r['condition'] == 50104 for i, n in r['reward'])
        for iid in reward_items:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',
                            (iid, 1 if iid in (1, 2, 14) else point_tests.ITEM_TYPES[iid][0]))

    def add_task(self, row):
        self.db.execute('INSERT OR REPLACE INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
            (row['id'], row.get('type', 3002), row.get('condition', 50104), row.get('need', 1), row.get('activity_id', 0),
             json.dumps(row.get('additional_parameter', [])), json.dumps(row.get('reward', []))))
        self.db.execute('INSERT OR IGNORE INTO task(uid,task_id,task_type) VALUES(?,?,?)', (UID, row['id'], row.get('type', 3002)))

    def value(self, tid):
        return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?', (UID, tid))[0]['progress']

    def state(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid') for t in
                ('resident_music_record', 'task', 'claim_ledger', 'currency', 'material')}

    def settle(self, mid=92, score=40000, state=1):
        self.conn.to_handler_ctx().codec_encode = self.conn.codec_encode
        return self.call(61048, dict(id=mid, score=score, state=state), 61049)

    def claim(self, ids):
        self.conn.to_handler_ctx().codec_encode = self.conn.codec_encode
        return self.call(61050, dict(id_list=ids), 61051)

    def record(self, mid):
        return self.db.query('SELECT * FROM resident_music_record WHERE uid=? AND music_id=?', (UID, mid))[0]

    def test_all_matching_native_tasks_progress_by_song_activity_not_task_number(self):
        self.add_task(dict(id=990001, additional_parameter=[3814901]))
        self.add_task(dict(id=990002, additional_parameter=[4214901]))
        self.add_task(dict(id=990003, additional_parameter=[3814902]))
        packet, response = self.settle()
        self.assertEqual(packet['result'], 0)
        self.assertEqual([self.value(i) for i in (40601001, 40607003, 150464, 990001)], [1] * 4)
        self.assertEqual(self.value(990002), 0); self.assertEqual(self.value(990003), 0)
        self.assertEqual(response.scs, [28007, 61049])
        changed = codec.decode(response.frames[0]['payload'], 'sc_28007')['progress_list']
        self.assertEqual(len(changed), 4)
        mid = next(int(i) for i, c in music.CATALOG.items() if c['activity_id'] == 4214901)
        self.assertEqual(self.settle(mid)[0]['result'], 0); self.assertEqual(self.value(990002), 1)

    def test_repeated_completions_do_not_repeat_task_deltas_or_count_as_battle_clear(self):
        self.add_task(dict(id=990010, condition=501, need=10))
        self.settle()
        self.assertEqual(self.value(990010), 0)
        before = self.state()
        self.assertEqual(self.settle(score=20000)[1].scs, [61049])
        self.assertEqual(self.state(), before)

    def test_best_score_keeps_its_own_finish_state_and_ties_follow_native_callback(self):
        self.settle(score=40000, state=1)
        self.settle(score=35000, state=3)
        self.assertEqual((self.record(92)['score'], self.record(92)['sign']), (40000, 1))
        self.settle(score=41000, state=2)
        self.assertEqual((self.record(92)['score'], self.record(92)['sign']), (41000, 2))
        self.settle(score=41000, state=1)
        self.assertEqual(self.record(92)['sign'], 1)
        cold = codec.decode(generator.gen_payload(61047, uid=UID, db=self.db), 'sc_61047')
        self.assertIn(dict(id=92, score=41000, sign=1), cold['challenge_info'])

    def test_unknown_song_and_bad_numeric_parameters_do_not_write_or_push_tasks(self):
        before = self.state()
        for args in [(999999, 1, 1), (92, 1, 0), (92, 1, 4)]:
            packet, response = self.settle(*args)
            self.assertNotEqual(packet['result'], 0); self.assertNotIn(28007, response.scs)
            self.assertEqual(self.state(), before)
        ctx = self.conn.to_handler_ctx()
        for args in [(92, -1, 1), (92, 2**32, 1), ('92', 1, 1), (True, 1, 1), (92, 1, True)]:
            with self.assertRaises(core.OperationError):
                mini.MiniGameService.get_instance(self.db).settle_music_stage(ctx, UID, *args)
        self.assertEqual(self.state(), before)

    def test_claimed_expired_and_malformed_tasks_are_not_reopened(self):
        self.db.execute('UPDATE task SET complete_flag=1 WHERE task_id=40601001')
        self.db.execute('UPDATE task SET expired_ts=? WHERE task_id=40607003', (NOW,))
        self.db.execute("INSERT INTO claim_ledger(uid,kind,key_id) VALUES(?,'task',150464)", (UID,))
        for tid, params, need in [(990020, [], 1), (990021, ['3814901'], 1), (990022, [3814901], 3)]:
            self.add_task(dict(id=tid, additional_parameter=params, need=need))
        self.assertEqual(self.settle()[1].scs, [61049])
        self.assertEqual([self.value(tid) for tid in (40601001, 40607003, 150464, 990020, 990021, 990022)], [0] * 6)

    def test_task_write_after_and_task_delta_encoding_failure_roll_back_song_and_retry(self):
        before = self.state(); execute = self.db.execute
        def fail(sql, *args, **kw):
            value = execute(sql, *args, **kw)
            if sql.startswith('UPDATE task SET progress=1'):
                raise RuntimeError('isolated task write-after')
            return value
        with patch.object(self.db, 'execute', side_effect=fail):
            self.assertNotEqual(self.settle()[0]['result'], 0)
        self.assertEqual(self.state(), before)
        encode = self.conn.codec_encode
        def fail_encode(name, obj):
            if name == 'sc_28007': raise RuntimeError('isolated task frame')
            return encode(name, obj)
        self.conn.codec_encode = fail_encode
        self.assertNotEqual(self.settle()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.conn.codec_encode = encode
        self.assertEqual(self.settle()[0]['result'], 0)

    def test_settlement_ack_preparation_failure_rolls_back(self):
        before = self.state(); encode = self.conn.codec_encode
        def fail(name, obj):
            if name == 'sc_61049' and obj.get('result') == 0: raise RuntimeError('isolated settlement ack')
            return encode(name, obj)
        self.conn.codec_encode = fail
        self.assertNotEqual(self.settle()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.conn.codec_encode = encode
        self.assertEqual(self.settle()[0]['result'], 0)

    def test_zero_default_snapshot_is_complete_read_only_and_filters_unknown_old_rows(self):
        self.db.execute('INSERT INTO resident_music_record(uid,music_id,score,sign) VALUES(?,999999,7,1)', (UID,))
        self.db.execute('INSERT INTO resident_music_record(uid,music_id,score,sign) VALUES(?,92,40000,1)', (UID,))
        before = self.state()
        cold = codec.decode(generator.gen_payload(61047, uid=UID, db=self.db), 'sc_61047')
        self.assertEqual(len(cold['challenge_info']), 166)
        self.assertEqual({r['id'] for r in cold['challenge_info']}, set(map(int, music.CATALOG)))
        self.assertEqual(self.state(), before)

    def test_late_task_recovery_uses_completed_music_and_both_login_builders(self):
        self.settle()
        self.add_task(dict(id=990030, additional_parameter=[3814901]))
        with patch.object(new_player_service, 'enabled', return_value=False):
            cold = codec.decode(generator.gen_28001({}, self.db, UID), 'sc_28001')
        self.assertEqual(self.value(990030), 1); self.assertTrue(cold)
        self.add_task(dict(id=990031, additional_parameter=[3814901]))
        with patch.object(new_player_service, 'ensure_tasks'):
            fresh = new_player_service.payload(self.db, UID, 28001)
        self.assertTrue(fresh); self.assertEqual(self.value(990031), 1)

    def test_native_legacy_reward_requires_target_score_and_pushes_inventory_delta(self):
        self.settle(mid=1, score=31359)
        before = self.state()
        self.assertNotEqual(self.claim([1])[0]['result'], 0); self.assertEqual(self.state(), before)
        self.settle(mid=1, score=31360)
        packet, response = self.claim([1])
        self.assertEqual(packet['result'], 0)
        self.assertEqual({r['id']: r['num'] for r in packet['reward_list']}, {1: 50, 11033: 5, 2: 2000})
        self.assertEqual(self.balance(1), 50); self.assertEqual(self.balance(11033), 5)
        self.assertEqual(response.scs, [17023, 61051])
        self.assertEqual(self.record(1)['is_rewarded'], 1)
        self.settle(mid=1, score=35000)
        self.assertEqual(self.record(1)['is_rewarded'], 1)
        before = self.state()
        self.assertNotEqual(self.claim([1])[0]['result'], 0); self.assertEqual(self.state(), before)

    def test_claim_batch_is_all_or_nothing_for_duplicate_unknown_unfinished_or_empty_reward(self):
        self.settle(mid=1, score=39200)
        before = self.state()
        for ids in [[], [1, 1], [1, 999999], [1, 2], [1, 92]]:
            self.assertNotEqual(self.claim(ids)[0]['result'], 0); self.assertEqual(self.state(), before)
        self.settle(mid=2, score=47100)
        packet, response = self.claim([1, 2])
        self.assertEqual(packet['result'], 0); self.assertEqual(self.balance(2), 6000)
        self.assertEqual(response.scs.count(17023), 1)

    def test_claim_grant_delta_and_ack_failure_roll_back_flags_and_balances(self):
        self.settle(mid=1, score=39200)
        before = self.state(); grant = InventoryService.grant_items
        def failed_grant(*args, **kw):
            grant(*args, **kw); raise RuntimeError('isolated grant-after')
        with patch.object(InventoryService, 'grant_items', side_effect=failed_grant):
            self.assertNotEqual(self.claim([1])[0]['result'], 0)
        self.assertEqual(self.state(), before)
        encode = self.conn.codec_encode
        for fail_name in ('sc_17023', 'sc_61051'):
            def fail(name, obj):
                if name == fail_name and (name != 'sc_61051' or obj.get('result') == 0):
                    raise RuntimeError('isolated music claim frame')
                return encode(name, obj)
            self.conn.codec_encode = fail
            with patch.object(generator, 'encode', side_effect=fail):
                self.assertNotEqual(self.claim([1])[0]['result'], 0)
            self.assertEqual(self.state(), before)
        self.conn.codec_encode = encode
        self.assertEqual(self.claim([1])[0]['result'], 0)

    def test_native_config_targets_and_all_condition_parameters_are_supported(self):
        from export_resident_music_contract import build
        self.assertEqual(music.CONTRACT, build())
        for version in ('229', '311'):
            tasks = json.loads((ROOT / 'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions'][version]['tasks']
            native = [r for r in tasks.values() if r['condition'] == 50104]
            self.assertTrue(native)
            self.assertTrue(all(r['need'] == 1 and len(r['additional_parameter']) == 1 for r in native))

    def test_resident_task_rewards_can_be_claimed_once_through_native_task_protocol(self):
        self.assertNotEqual(self.call(28010, dict(id=40601001), 28011)[0]['result'], 0)
        self.settle()
        packet, response = self.call(28010, dict(id=40601001), 28011)
        self.assertEqual(packet['result'], 0); self.assertIn(17023, response.scs)
        self.assertEqual(self.balance(1), 50); self.assertEqual(self.balance(40413), 20)
        self.assertEqual(self.balance(22003), 4)
        before = self.state()
        self.call(28010, dict(id=40601001), 28011)
        self.assertEqual(self.state(), before)
        self.settle(score=41000)
        self.assertGreater(self.db.query('SELECT claimed_ts FROM task WHERE uid=? AND task_id=40601001', (UID,))[0]['claimed_ts'], 0)

    def test_same_uid_in_another_save_and_other_uid_in_same_save_are_isolated(self):
        other = AccountDB.new_save_workspace(); self.addCleanup(other.close)
        self.settle()
        self.assertEqual(other.query('SELECT * FROM resident_music_record'), [])
        self.assertEqual(music.records(other, UID)[91]['score'], 0)
        self.assertEqual(music.project_tasks(self.db, UID + 1), [])
        self.assertEqual(self.db.query('SELECT * FROM resident_music_record WHERE uid=?', (UID + 1,)), [])
        self.assertIsNot(mini.MiniGameService.get_instance(self.db), mini.MiniGameService.get_instance(other))

    def test_required_music_subscriber_typeerror_runs_once_and_rolls_back(self):
        before = self.state(); called = []
        original = list(bus._handlers[Events.MUSIC_COMPLETE])
        def fail(ctx, uid, **kw):
            called.append(uid); raise TypeError('isolated subscriber body')
        with patch.dict(bus._handlers, {Events.MUSIC_COMPLETE: original + [fail]}):
            self.assertNotEqual(self.settle()[0]['result'], 0)
        self.assertEqual(called, [UID]); self.assertEqual(self.state(), before)
        self.assertEqual(self.settle()[0]['result'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
