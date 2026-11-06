"""Permanent Hella pinball through native Core, SQL and codec in memory."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
import verify_minigame_point_rewards as point_checks
from account_db import AccountDB
import codec
import core
import generator
import hero_codec
import middleware as mw
import minigame_service as mini
import new_player_service
import pinball_service as pin
from event_bus import bus, Events

ROOT = Path(__file__).resolve().parents[2]
TASKS = json.loads((ROOT / 'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class PinballTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock = patch('time.time', return_value=NOW); clock.start(); self.addCleanup(clock.stop)
        sessions = patch.dict(mw._BATTLE_SESSIONS, {}, clear=True); sessions.start(); self.addCleanup(sessions.stop)
        for row in TASKS.values():
            if 392001 <= row['condition'] <= 392004:
                self.add_task(row)
        items = {i for r in TASKS.values() if 392001 <= r['condition'] <= 392004 for i, n in r['reward']}
        for iid in items:
            kind = 1 if iid in (1, 2, 14) else point_checks.ITEM_TYPES[iid][0]
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)', (iid, kind))
        self.db.execute('UPDATE users SET level=70 WHERE uid=?', (UID,))
        self.db.upsert('hero', UID, hero_codec.initial_hero_row(1084, 100), keys=('uid', 'id'))

    def add_task(self, row):
        self.db.execute('INSERT OR REPLACE INTO task_cfg(task_id,condition,need,task_type,additional_parameter,reward_json) VALUES(?,?,?,?,?,?)',
            (row['id'], row.get('condition', 392001), row.get('need', 1), row.get('type', 3002), json.dumps(row.get('additional_parameter', [])), json.dumps(row.get('reward', []))))
        self.db.execute('INSERT OR IGNORE INTO task(uid,task_id,task_type) VALUES(?,?,?)', (UID, row['id'], row.get('type', 3002)))

    def state(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid') for t in
                ('pinball_progress', 'task', 'claim_ledger', 'users', 'hero', 'currency', 'material', 'chapter', 'stage_sub', 'server_global_config')}

    def value(self, tid):
        return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?', (UID, tid))[0]['progress']

    def read(self):
        return pin.read(self.db, UID)

    def finish(self, stage=40601, score=0, harm=0):
        return self.call(89402, dict(stage_id=stage, score=score, harm=harm), 89403)

    def equip(self, skills, activity=3840801):
        return self.call(89404, dict(activity_id=activity, skill_list=skills), 89405)

    def enter_boss(self, dest=5300105, activity=3840801):
        packet, response = self.call(54030, dict(common_info=dict(type=603, dest=dest, activity_id=activity,
            hero_list=[dict(hero_id=1084, hero_type=1)])), 54031)
        if packet['result'] == 0:
            self.bid = self.conn.battle_id[0]
        return packet, response

    def boss_result(self, result=1):
        mw.get_battle_session(self.bid)['result_data'] = dict(result=result, info=dict(battle_time=40000))
        return self.call(54032, dict(battle_id=self.bid), 54033)

    def through(self, last):
        for stage in range(40601, last + 1):
            if stage == 40605:
                self.assertEqual(self.enter_boss()[0]['result'], 0)
                self.assertEqual(self.boss_result()[0]['result'], 0)
            else:
                self.assertEqual(self.finish(stage)[0]['result'], 0)

    def test_initial_frame_is_read_only_and_unknown_or_locked_requests_do_not_write(self):
        before = self.state()
        data = codec.decode(generator.gen_payload(89401, uid=UID, db=self.db), 'sc_89401')
        self.assertEqual(data.get('hai_la_level'), 1); self.assertEqual(data.get('challenge_score'), 0)
        self.assertEqual(self.state(), before)
        for stage in (999999, 40602, 40612, 40650, 5300101):
            self.assertNotEqual(self.finish(stage)[0]['result'], 0); self.assertEqual(self.state(), before)
        for skills, activity in (([40603], 3840801), ([], 1), ([999999], 3840801)):
            self.assertNotEqual(self.equip(skills, activity)[0]['result'], 0); self.assertEqual(self.state(), before)

    def test_normal_clear_grows_unlocks_tasks_and_never_sets_challenge_score(self):
        packet, response = self.finish(score=123456, harm=9999)
        self.assertEqual(packet['result'], 0)
        data = self.read()
        self.assertEqual(data['stage_count'], 2); self.assertEqual(data['unlock_skill_list'], [40603])
        self.assertEqual(data['cleared_stage_list'], [40601]); self.assertEqual(data['score'], 0)
        self.assertEqual(self.value(40604001), 1); self.assertEqual(self.value(40605001), 1)
        self.assertEqual(response.scs, [89401, 28007, 89403])
        before = self.state(); self.assertEqual(self.finish(score=999999)[1].scs, [89403])
        self.assertEqual(self.state(), before)

    def test_equipment_only_unlocked_unique_active_skills_two_slots_and_native_ack(self):
        self.through(40605)
        before = self.state()
        for skills in ([40607], [40605], [40601], [999999], [40603, 40603], [40603, 40604, 40605], [0]):
            self.assertNotEqual(self.equip(skills)[0]['result'], 0); self.assertEqual(self.state(), before)
        packet, response = self.equip([40604, 40603])
        self.assertEqual(packet['result'], 0); self.assertEqual(response.scs, [89405])
        cold = codec.decode(generator.gen_payload(89401, uid=UID, db=self.db), 'sc_89401')
        self.assertEqual(cold['select_active_skills'], [40604, 40603])
        self.assertTrue(set([40607, 40610, 40613]) <= set(cold['skills']))
        self.assertEqual(self.equip([])[0]['result'], 0); self.assertEqual(self.read()['equip_skill_list'], [])

    def test_boss_real_battle_entry_and_victory_unblock_sixth_stage(self):
        before = self.state()
        self.assertNotEqual(self.enter_boss()[0]['result'], 0); self.assertEqual(self.state(), before)
        self.through(40604)
        self.assertNotEqual(self.finish(40605)[0]['result'], 0)
        self.assertNotEqual(self.enter_boss(dest=5300104)[0]['result'], 0)
        self.assertNotEqual(self.enter_boss(activity=1)[0]['result'], 0)
        self.db.set_system_config('save.profile', new_player_service.PROFILE)
        self.db.set_system_config('save.owner_uid', str(UID))
        self.db.execute('INSERT INTO newbie_activity(uid,trigger_time,completed_time) VALUES(?,?,?)', (UID, NOW - 1, 2429985600))
        self.assertEqual(self.enter_boss()[0]['result'], 0)
        old_exp = self.db.get('users', UID).get('total_exp', 0)
        hero_exp = self.db.query('SELECT exp FROM hero WHERE uid=? AND id=1084', (UID,))[0]['exp']
        packet, response = self.boss_result()
        self.assertEqual(packet['result'], 0); self.assertEqual(packet['battle_result']['result'], 1)
        self.assertIn(40605, self.read()['cleared_stage_list']); self.assertEqual(self.read()['stage_count'], 6)
        self.assertIn(40604, self.read()['unlock_skill_list']); self.assertEqual(self.value(40604005), 1)
        self.assertEqual(response.scs.count(89401), 1); self.assertLess(response.scs.index(89401), response.scs.index(54033))
        self.assertEqual(self.db.get('users', UID).get('total_exp', 0), old_exp)
        self.assertEqual(self.db.query('SELECT exp FROM hero WHERE uid=? AND id=1084', (UID,))[0]['exp'], hero_exp)
        before = self.state(); replay = self.boss_result()[1]
        self.assertEqual(replay.scs, [54033]); self.assertEqual(self.state(), before)
        self.assertEqual(self.finish(40606)[0]['result'], 0)

    def test_lost_and_quit_boss_do_not_mark_clear_or_unlock_skills(self):
        self.through(40604)
        before = self.read()
        for result in (2, 3):
            self.assertEqual(self.enter_boss()[0]['result'], 0)
            packet, response = self.boss_result(result)
            self.assertEqual(packet['battle_result']['result'], result)
            self.assertNotIn(89401, response.scs); self.assertEqual(self.read(), before)
        self.assertNotEqual(self.finish(40606)[0]['result'], 0)

    def test_challenge_requires_full_route_and_only_updates_its_best_score(self):
        self.through(40612)
        packet, response = self.finish(40650, 7000, 500)
        self.assertEqual(packet['result'], 0); self.assertEqual(response.scs, [89401, 28007, 89403])
        self.assertEqual(self.read()['stage_count'], 13); self.assertEqual(self.read()['score'], 7000)
        self.assertNotIn(40650, self.read()['cleared_stage_list'])
        self.assertEqual(self.finish(40650, 6000)[1].scs, [28007, 89403])
        self.assertEqual(pin.read_statistics(self.db, UID)['challenge_points'], 13000)
        self.assertEqual(self.finish(40650, 8000)[0]['result'], 0); self.assertEqual(self.read()['score'], 8000)

    def test_normal_required_frames_and_ack_failures_roll_back_and_retry(self):
        ctx = self.conn.to_handler_ctx(); encode = ctx.codec_encode
        before = self.state()
        for name in ('sc_89401', 'sc_28007', 'sc_89403'):
            def fail(n, obj):
                if n == name and (n != 'sc_89403' or obj.get('result') == 0): raise RuntimeError('isolated pinball frame')
                return encode(n, obj)
            with patch.object(ctx, 'codec_encode', side_effect=fail):
                self.assertNotEqual(self.finish()[0]['result'], 0)
            self.assertEqual(self.state(), before)
        self.assertEqual(self.finish()[0]['result'], 0)

    def test_skill_save_and_ack_failures_roll_back(self):
        self.finish(); before = self.state(); execute = self.db.execute
        def fail(sql, *args, **kw):
            out = execute(sql, *args, **kw)
            if sql.startswith('INSERT INTO pinball_progress'): raise RuntimeError('isolated save-after')
            return out
        with patch.object(self.db, 'execute', side_effect=fail):
            self.assertNotEqual(self.equip([40603])[0]['result'], 0)
        self.assertEqual(self.state(), before)
        ctx = self.conn.to_handler_ctx(); encode = ctx.codec_encode
        with patch.object(ctx, 'codec_encode', side_effect=lambda n, obj: None if n == 'sc_89405' and obj.get('result') == 0 else encode(n, obj)):
            self.assertNotEqual(self.equip([40603])[0]['result'], 0)
        self.assertEqual(self.state(), before); self.assertEqual(self.equip([40603])[0]['result'], 0)

    def test_boss_necessary_progress_or_ack_failure_rolls_back_and_does_not_cache_success(self):
        self.through(40604)
        self.assertEqual(self.enter_boss()[0]['result'], 0)
        ctx = self.conn.to_handler_ctx(); encode = ctx.codec_encode
        before = self.state()
        for name in ('sc_89401', 'sc_54033'):
            def fail(n, obj):
                if n == name and (n != 'sc_54033' or obj.get('result') == 0): raise RuntimeError('isolated pinball boss frame')
                return encode(n, obj)
            with patch.object(ctx, 'codec_encode', side_effect=fail):
                self.assertNotEqual(self.boss_result()[0]['result'], 0)
            self.assertEqual(self.state(), before); self.assertNotIn('br', mw.get_battle_session(self.bid))
        self.assertEqual(self.boss_result()[0]['result'], 0)

    def test_late_and_old_task_recovery_in_both_login_builders(self):
        self.finish(); self.add_task(dict(id=990001, additional_parameter=[40601]))
        with patch.object(new_player_service, 'enabled', return_value=False):
            generator.gen_28001({}, self.db, UID)
        self.assertEqual(self.value(990001), 1)
        self.add_task(dict(id=990002, additional_parameter=[40601]))
        with patch.object(new_player_service, 'ensure_tasks'):
            new_player_service.payload(self.db, UID, 28001)
        self.assertEqual(self.value(990002), 1)

    def test_claimed_expired_and_bad_task_parameters_are_not_reopened(self):
        self.db.execute('UPDATE task SET complete_flag=1 WHERE task_id=40604001')
        self.db.execute('UPDATE task SET expired_ts=? WHERE task_id=40605001', (NOW,))
        self.add_task(dict(id=990003, additional_parameter=[40601]))
        self.db.execute("INSERT INTO claim_ledger(uid,kind,key_id) VALUES(?,'task',990003)", (UID,))
        for tid, params in ((990004, []), (990005, ['40601']), (990006, [40601, 40602])):
            self.add_task(dict(id=tid, additional_parameter=params))
        self.assertEqual(self.finish()[1].scs, [89401, 89403])
        self.assertEqual([self.value(i) for i in (40604001, 40605001, 990003, 990004, 990005, 990006)], [0] * 6)

    def test_native_task_reward_is_available_and_repeat_does_not_grant_again(self):
        self.finish()
        packet, response = self.call(28010, dict(id=40604001), 28011)
        self.assertEqual(packet['result'], 0); self.assertIn(17023, response.scs)
        expected = {i: n for i, n in TASKS['40604001']['reward']}
        self.assertEqual({i: self.balance(i) for i in expected}, expected)
        before = self.state(); self.call(28010, dict(id=40604001), 28011); self.assertEqual(self.state(), before)

    def test_legacy_invalid_ids_are_not_sent_and_valid_progress_skills_are_preserved(self):
        self.db.execute('INSERT INTO pinball_progress(uid,activity_id,stage_count,cleared_stage_list,unlock_skill_list,equip_skill_list,score) VALUES(?,3840801,2,?,?,?,?)',
            (UID, '[40601,999999]', '[40603,999999]', '[40603,40603,999999]', 100))
        before = self.state()
        frame = codec.decode(generator.gen_payload(89401, uid=UID, db=self.db), 'sc_89401')
        self.assertEqual(frame['stage_list'], [40601]); self.assertEqual(frame['skills'], [40603])
        self.assertEqual(frame['select_active_skills'], [40603]); self.assertEqual(frame['challenge_score'], 100)
        self.assertEqual(self.state(), before)

    def test_input_ranges_same_uid_cross_save_and_required_event_failures(self):
        other = AccountDB.new_save_workspace(); self.addCleanup(other.close)
        before = self.state(); ctx = self.conn.to_handler_ctx()
        for stage, score, harm in [(True, 0, 0), (40601, -1, 0), (40601, 0, 2**32)]:
            with self.assertRaises(core.OperationError): pin.settle(ctx, UID, stage, score, harm)
        self.assertEqual(self.state(), before)
        calls = []; handlers = list(bus._handlers[Events.PINBALL_CLEAR])
        def fail(ctx, uid, **kwargs): calls.append(uid); raise TypeError('isolated pinball subscriber')
        with patch.dict(bus._handlers, {Events.PINBALL_CLEAR: handlers + [fail]}):
            self.assertNotEqual(self.finish()[0]['result'], 0)
        self.assertEqual(calls, [UID]); self.assertEqual(self.state(), before)
        self.finish(); self.assertEqual(pin.read(other, UID)['cleared_stage_list'], [])
        self.assertEqual(pin.read(self.db, UID + 1)['cleared_stage_list'], [])

    def test_export_matches_current_lua_and_native_task_shapes(self):
        from export_pinball_contract import build
        self.assertEqual(pin.CFG, build())
        for version in ('229', '311'):
            tasks = json.loads((ROOT / 'v5_server/data/activity_content_cfg.json').read_text('utf-8'))['versions'][version]['tasks']
            self.assertTrue(all(r['need'] == 1 and len(r['additional_parameter']) == 1
                for r in tasks.values() if r['condition'] == 392001))

    def test_story_damage_accumulates_across_replays_without_affecting_challenge_points(self):
        self.finish(harm=8000)
        self.assertEqual(self.value(40605013), 8000)
        packet, response = self.finish(harm=2000)
        self.assertEqual(packet['result'], 0); self.assertEqual(response.scs, [28007, 89403])
        self.assertEqual(self.value(40605013), 9000); self.assertEqual(self.value(40605014), 10000)
        self.assertEqual(pin.read_statistics(self.db, UID), dict(version=1, story_harm=10000, challenge_points=0))
        self.assertEqual(self.read()['score'], 0)

    def test_challenge_total_and_best_are_distinct_and_restore_late_tasks(self):
        self.through(40612)
        self.finish(40650, 60000, 500000)
        self.finish(40650, 50000, 500000)
        self.assertEqual(self.read()['score'], 60000)
        self.assertEqual(pin.read_statistics(self.db, UID)['challenge_points'], 110000)
        self.assertEqual(pin.read_statistics(self.db, UID)['story_harm'], 0)
        self.assertEqual([self.value(i) for i in (40605017, 40605018, 40605019)], [50000, 100000, 1])
        self.add_task(dict(id=990010, condition=392004, need=200000, additional_parameter=[3]))
        self.add_task(dict(id=990011, condition=392003, need=1, additional_parameter=[3, 60001]))
        self.assertTrue(pin.restore_for_login(self.db, UID))
        self.assertEqual(self.value(990010), 110000); self.assertEqual(self.value(990011), 0)

    def test_old_cumulative_task_progress_adopts_a_lower_bound_without_summing_tiers(self):
        self.db.execute('UPDATE task SET progress=9000 WHERE task_id=40605013')
        self.db.execute('UPDATE task SET progress=20000 WHERE task_id=40605014')
        self.finish(harm=600)
        self.assertEqual(pin.read_statistics(self.db, UID)['story_harm'], 20600)
        self.assertEqual(self.value(40605014), 20600)
        self.add_task(dict(id=990012, condition=392002, need=100000, additional_parameter=[1]))
        pin.restore_for_login(self.db, UID)
        self.assertEqual(self.value(990012), 20600)

    def test_statistics_write_after_and_task_frame_failure_roll_back_counters_and_stage(self):
        before = self.state(); execute = self.db.execute
        def fail(sql, *args, **kw):
            result = execute(sql, *args, **kw)
            if sql.startswith('INSERT INTO server_global_config(key,value)'):
                raise RuntimeError('isolated statistics write-after')
            return result
        with patch.object(self.db, 'execute', side_effect=fail):
            self.assertNotEqual(self.finish(harm=8000)[0]['result'], 0)
        self.assertEqual(self.state(), before)
        ctx = self.conn.to_handler_ctx(); encode = ctx.codec_encode
        with patch.object(ctx, 'codec_encode', side_effect=lambda n, obj: None if n == 'sc_28007' else encode(n, obj)):
            self.assertNotEqual(self.finish(harm=8000)[0]['result'], 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.finish(harm=8000)[0]['result'], 0)
        self.assertEqual(pin.read_statistics(self.db, UID)['story_harm'], 8000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
