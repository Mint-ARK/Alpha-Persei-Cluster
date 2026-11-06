"""Memory-only native voyage receipts, skill gates and photograph projection."""
import json
from unittest.mock import patch
import unittest

from verify_report_small_fixes import MemoryTests, UID, NOW
from verify_minigame_point_rewards import ITEM_TYPES
import codec
import generator
import minigame_service
import new_player_service
import quanzhou_collection as collection
from core import OperationError


class CollectionTests(MemoryTests):
    def setUp(self):
        super().setUp()
        visible = [dict(activity_id=i, state=1, start_time=1, stop_time=NOW + 86400)
                   for i in (323641, 323841)]
        for p in (patch.object(collection.time, 'time', return_value=NOW),
                  patch.object(generator, 'get_activity_list', return_value=visible)):
            p.start(); self.addCleanup(p.stop)
        self.visibility_patch = p
        self.ctx = self.conn.to_handler_ctx()
        for iid in (1, 40103, 22003):
            kind = 1 if iid == 1 else ITEM_TYPES[iid][0]
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)', (iid, kind))
        for iid in {77, 2110731} | {i for row in collection.CFG['point_rewards'].values() for i, n in row['reward_item_list']}:
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)', (iid, 1 if iid < 100 else ITEM_TYPES[iid][0]))
        for tid, row in collection.LEVEL_TASKS.items():
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) '
                            'VALUES(?,?,?,?,?,?,?)', (tid, row['type'], row['condition'], row['need'], row['activity_id'],
                                                    json.dumps(row['additional_parameter']), json.dumps(row['reward'])))

    def subquest(self, task, status=1):
        row = collection.CFG['tasks'][str(task)]
        self.db.execute('INSERT INTO qworld_quest_task(uid,main_quest_id,task_id,status,progress) '
                        'VALUES(?,?,?,?,?) ON CONFLICT(uid,main_quest_id,task_id) '
                        'DO UPDATE SET status=excluded.status,progress=excluded.progress',
                        (UID, row['main_task_id'], task, status, int(status == 1)))

    def state(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid')
                for t in ('ops_common', 'currency', 'material', 'quanzhou_sail_progress')}

    def main(self, mid, status=9):
        self.db.execute('INSERT INTO qworld_quest(uid,main_quest_id,status) VALUES(?,?,?) '
                        'ON CONFLICT(uid,main_quest_id) DO UPDATE SET status=excluded.status', (UID, mid, status))

    def test_export_matches_native_with_description_and_all_stages(self):
        from export_quanzhou_contract import build
        self.assertEqual(collection.CFG, build())
        self.assertEqual(len(collection.SEA_STAGES), 11)
        self.assertEqual(len(collection.PHOTOS), 10)
        self.assertIn('主线任务', collection.CFG['description'])
        self.assertEqual({v['need'] for v in collection.CFG['point_rewards'].values()}, set(range(1, 11)))

    def test_cold_snapshot_ignores_old_capture_receipts(self):
        self.db.execute('INSERT INTO quanzhou_sail_progress(uid,activity_id,skill_id_list,stage_id_list) VALUES(?,323841,?,?)',
                        (UID, json.dumps(list(collection.SEA_SKILLS)), json.dumps(list(collection.SEA_STAGES))))
        before = self.state()
        self.assertEqual(collection.sea_payload(self.db, UID), dict(data=dict(affix=[], stage_id_list=[])))
        self.assertEqual(collection.photograph_ids(self.db, UID), [])
        self.assertEqual(self.state(), before)

    def test_story_voyage_requires_active_or_completed_native_battle_task(self):
        for sea in (400001, 400101):
            with self.assertRaises(OperationError): collection.enter_validate(self.ctx, UID, sea)
        self.subquest(11101005, 0)
        entry = collection.enter_validate(self.ctx, UID, 400001)
        self.assertEqual(entry['stage_id'], 5240201)
        self.subquest(11101005)
        self.assertEqual(collection.enter_validate(self.ctx, UID, 400001)['sea_id'], 400001)
        with self.assertRaises(OperationError): collection.enter_validate(self.ctx, UID, 5240201)

    def test_all_six_crew_and_active_skills_follow_actual_subquest_completion(self):
        for iid, row in collection.SEA_SKILLS.items():
            self.subquest(row['task_id'])
        self.assertEqual(collection.unlocked_skills(self.db, UID), sorted(collection.SEA_SKILLS))
        self.subquest(11103901, 0)
        self.assertEqual(collection.enter_validate(self.ctx, UID, 400101, 2001)['skill_id'], 2001)
        for skill in (1001, 9999, -1, True):
            with self.assertRaises(OperationError): collection.enter_validate(self.ctx, UID, 400101, skill)
        self.subquest(11103005, 0)
        with self.assertRaises(OperationError): collection.enter_validate(self.ctx, UID, 400101, 2001)

    def test_projected_active_battle_task_does_not_require_an_existing_row(self):
        self.subquest(11101002)
        self.assertEqual(self.db.query('SELECT * FROM qworld_quest_task WHERE uid=? AND task_id=11101005', (UID,)), [])
        self.assertEqual(collection.enter_validate(self.ctx, UID, 400001)['stage_id'], 5240201)

    def test_challenge_requires_story_unlock_and_real_predecessor(self):
        with self.assertRaises(OperationError): collection.enter_validate(self.ctx, UID, 400201)
        self.subquest(11109001)
        collection.enter_validate(self.ctx, UID, 400201)
        with self.assertRaises(OperationError): collection.enter_validate(self.ctx, UID, 400202)
        collection.settle(self.ctx, UID, 400201, True)
        collection.enter_validate(self.ctx, UID, 400202)
        with self.assertRaises(OperationError): collection.enter_validate(self.ctx, UID, 400203)

    def test_all_ten_first_clear_drops_are_native_and_repeat_is_empty(self):
        for sea, row in collection.SEA_STAGES.items():
            if not row['reward']: continue
            before = (self.balance(1), self.balance(40103), self.balance(22003))
            result = collection.settle(self.ctx, UID, sea, True)
            self.assertTrue(result['first'])
            self.assertEqual((self.balance(1)-before[0], self.balance(40103)-before[1], self.balance(22003)-before[2]), (20, 10, 2))
            saved = self.state()
            self.assertEqual(collection.settle(self.ctx, UID, sea, True), dict(first=False, reward_list=[]))
            self.assertEqual(self.state(), saved)
        self.assertEqual(len(collection.read(self.db, UID)['completed']), 10)
        self.assertEqual(self.balance(77), 0)

    def test_tutorial_clear_has_no_drop_and_defeat_has_no_receipt(self):
        before = self.state()
        self.assertEqual(collection.settle(self.ctx, UID, 400001, False), dict(first=False, reward_list=[]))
        self.assertEqual(self.state(), before)
        result = collection.settle(self.ctx, UID, 400001, True)
        self.assertEqual(result, dict(first=True, reward_list=[]))
        self.assertEqual(self.balance(1), 0)
        self.assertEqual(collection.read(self.db, UID)['completed'], [400001])

    def test_state_encoding_failure_rolls_back_receipt_inventory_and_notifications(self):
        before = self.state()
        touched = self.ctx.touched_items.copy()
        pending = list(self.ctx.pending_frames)
        encode = self.ctx.codec_encode
        with patch.object(self.ctx, 'codec_encode', side_effect=lambda n, d: b'' if n == 'sc_86035' else encode(n, d)):
            with self.assertRaises(ValueError): collection.settle(self.ctx, UID, 400101, True)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.ctx.touched_items, touched)
        self.assertEqual(self.ctx.pending_frames, pending)
        self.assertTrue(collection.settle(self.ctx, UID, 400101, True)['first'])

    def test_photographs_only_unlock_from_finished_tasks_and_skills_only_push_on_change(self):
        self.subquest(11103006, 0)
        self.assertEqual(collection.photograph_ids(self.db, UID), [])
        self.subquest(11103006)
        self.assertEqual(collection.photograph_ids(self.db, UID), [2001])
        before = len(self.ctx.pending_frames)
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(len(self.ctx.pending_frames), before)
        self.subquest(11103005)
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(len(self.ctx.pending_frames), before + 1)
        payload = self.ctx.pending_frames[-1]
        self.assertEqual(codec.decode(payload.payload, 'sc_86035')['data']['affix'], [2001])
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(len(self.ctx.pending_frames), before + 1)

    def test_approved_story_weights_cap_and_exclude_initialization_and_side_prologues(self):
        for mid in (11199, 11211, 11212, 11213, 11216, 11217, 11218, 11219): self.main(mid)
        self.assertEqual(collection.level_facts(self.db, UID), dict(points=0, level=0))
        self.main(11101, 3)
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(self.balance(77), 0)
        for mid, expected in ((11101, 3), (11102, 6), (11103, 9), (11104, 10)):
            self.main(mid)
            collection.publish_progress(self.ctx, UID)
            self.assertEqual(self.balance(77), expected)
        for mid in range(11105, 11110): self.main(mid)
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(collection.level_facts(self.db, UID), dict(points=27, level=10))
        self.assertEqual(self.balance(77), 10)
        pending = len(self.ctx.pending_frames)
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(len(self.ctx.pending_frames), pending)
        self.assertEqual(self.balance(77), 10)

    def test_side_exploration_facts_raise_level_and_four_recommendation_tasks(self):
        for mid in (11201, 11307, 11311): self.main(mid)
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(self.balance(77), 3)
        self.assertEqual({r['task_id']: r['progress'] for r in self.db.query('SELECT task_id,progress FROM task WHERE uid=?', (UID,))},
                         {60501: 3, 60502: 3, 60503: 3, 60504: 3})
        diff = [f for f in self.ctx.pending_frames if f.cmd == 28007]
        self.assertEqual(len(diff), 1)
        self.assertEqual(len(codec.decode(diff[0].payload, 'sc_28007')['progress_list']), 4)
        self.db.execute('UPDATE task SET complete_flag=1,claimed_ts=? WHERE uid=? AND task_id=60501', (NOW, UID))
        self.main(11101); collection.publish_progress(self.ctx, UID)
        self.assertEqual(self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=60501', (UID,))[0]['progress'], 3)

    def test_login_restores_knowledge_silently_without_reducing_old_high_value(self):
        self.main(11101)
        self.assertEqual(len(collection.restore_for_login(self.db, UID)), 4)
        self.assertEqual(self.balance(77), 3)
        self.assertEqual(collection.restore_for_login(self.db, UID), [])
        self.db.set_currency(UID, 77, 17)
        collection.restore_for_login(self.db, UID)
        self.assertEqual(self.balance(77), 17)
        self.assertEqual(self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=60504', (UID,))[0]['progress'], 10)
        self.assertEqual(self.ctx.pending_frames, [])

    def test_full_story_can_claim_all_ten_native_reward_tiers_once(self):
        for mid in range(11101, 11110): self.main(mid)
        collection.publish_progress(self.ctx, UID)
        ids = sorted(map(int, collection.CFG['point_rewards']))
        packet, response = self.call(60054, dict(point_reward_id_list=ids), 60055)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.balance(77), 10)
        self.assertEqual(len(self.db.query('SELECT * FROM activity_point_reward WHERE uid=? AND activity_id=323841', (UID,))), 10)
        self.assertIn(17023, response.scs)
        self.assertEqual(self.balance(1), 200)
        self.assertEqual(self.balance(22003), 20)
        self.assertEqual(self.balance(1073), 1)
        self.assertEqual(self.balance(2925), 1)
        again, _ = self.call(60054, dict(point_reward_id_list=ids), 60055)
        self.assertNotEqual(again['result'], 0)
        self.assertEqual((self.balance(1), self.balance(22003), self.balance(77)), (200, 20, 10))

    def test_knowledge_task_frame_failure_rolls_back_currency_and_task_projection(self):
        self.main(11101)
        before = self.state()
        encode = self.ctx.codec_encode
        with patch.object(self.ctx, 'codec_encode', side_effect=lambda n, d: b'' if n == 'sc_28007' else encode(n, d)):
            with self.assertRaises(ValueError): collection.publish_progress(self.ctx, UID)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.query('SELECT * FROM task WHERE uid=?', (UID,)), [])
        self.assertEqual(self.ctx.touched_items, set())
        self.assertEqual(self.ctx.pending_frames, [])

    def test_zero_level_recommendations_exist_without_zero_delta_or_claim_revival(self):
        self.assertEqual(collection.ensure_tasks(self.db, UID), 4)
        self.assertEqual(collection.ensure_tasks(self.db, UID), 0)
        self.assertEqual(collection.project_tasks(self.db, UID), [])
        self.assertEqual({row['task_id']: row['progress'] for row in self.db.query('SELECT task_id,progress FROM task WHERE uid=?', (UID,))},
                         {i: 0 for i in range(60501, 60505)})
        collection.publish_progress(self.ctx, UID)
        self.assertEqual(self.ctx.pending_frames, [])
        other = UID + 1
        self.db.execute('INSERT INTO users(uid,nick) VALUES(?,?)', (other, 'claimed fixture'))
        self.db.execute('INSERT INTO claim_ledger(uid,kind,key_id,claim_ts) VALUES(?,?,?,?)', (other, 'task', 60501, NOW))
        self.assertEqual(collection.ensure_tasks(self.db, other), 3)
        self.assertEqual(self.db.query('SELECT task_id FROM task WHERE uid=? AND task_id=60501', (other,)), [])

    def test_actual_activity_and_task_generators_cover_fresh_and_ordinary_three_versions(self):
        self.visibility_patch.stop()
        for version in ('229', '311', '321'):
            actual = {row['activity_id']: row for row in generator.get_activity_list(self.db, UID, version)}
            for aid in (323641, 323841, 3200411):
                self.assertEqual(actual[aid]['state'], 1)
            self.assertEqual(actual[323641]['sub_activity_id_list'], [3200411, 323841])
            self.assertTrue(set(range(3200412, 3200417)) <= set(actual))
            for fresh in (False, True):
                if fresh:
                    self.db.set_system_config('save.profile', new_player_service.PROFILE)
                    self.db.set_system_config('save.owner_uid', str(UID))
                    self.db.upsert('newbie_activity', UID, dict(trigger_time=NOW, completed_time=4102444800))
                else:
                    self.db.set_system_config('save.profile', 'ordinary')
                packet = codec.decode(generator.gen_payload(28001, db=self.db, uid=UID, res_version=version), 'sc_28001')
                ids = {row['id'] for row in packet['assignment_list']}
                self.assertTrue(set(range(60501, 60505)) <= ids, (version, fresh))


if __name__ == '__main__':
    unittest.main(verbosity=2)
