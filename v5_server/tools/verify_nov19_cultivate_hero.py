"""Use the real login and operation routes with an isolated Build 229 save."""
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import codec
from cultivate_hero_service import CATALOG, payload
from event_bus import bus, Events
import generator
import login
from minigame_point_service import rewards_for
from transport import unpack_down


ACTIVITY = 4318901
COIN = CATALOG[ACTIVITY]["coin_id"]


class CultivateHeroTests(MemoryTests):
    def setUp(self):
        super().setUp()
        version = patch("res_version_manager.get_current_version", return_value="229")
        version.start()
        self.addCleanup(version.stop)
        self.db.execute("INSERT INTO activity(uid,activity_id,state,theme,template) VALUES(?,?,1,43,189)",
                        (UID, ACTIVITY))
        self.db.execute("INSERT INTO item_catalog(id,type,category) VALUES(?,1,'currency')", (COIN,))
        self.task, self.goal = next((tid, goal) for tid, goal in CATALOG[ACTIVITY]["tasks"].items()
                                    if goal["type"] == 1)
        self.db.execute("INSERT INTO hero(uid,id,level,unlock) VALUES(?,?,?,1)",
                        (UID, self.goal["hero"], self.goal["condition"]))

    def test_real_login_replaces_stale_state_and_includes_zero_progress_tasks(self):
        written = []
        sock = SimpleNamespace(settimeout=lambda _: None, sendall=written.append)
        commands = (11001, 64077, 10201)
        rows = [(i, cmd, generator.build_frame(cmd, b"")[:11], b"", cmd == 11001, None)
                for i, cmd in enumerate(commands)]
        with patch.object(login, "load_login_push", return_value=rows):
            login.send_login_flood(sock, self.db, UID, generator, frame_delay=0, log=lambda *args: None)
        packets = [unpack_down(frame) for frame in written]
        order = [packet["cmd"] for packet in packets]
        self.assertEqual(order.count(64077), 1)
        self.assertLess(order.index(11001), order.index(64077))
        self.assertLess(order.index(64077), order.index(10201))
        packet = codec.decode(packets[order.index(64077)]["payload"], "sc_64077")
        progress = {row["id"]: row["progress"] for row in packet["task_info_list"]}
        self.assertEqual(packet["activity_id"], ACTIVITY)
        self.assertEqual(set(progress), set(CATALOG[ACTIVITY]["tasks"]))
        self.assertEqual(progress[self.task], self.goal["condition"])
        self.assertIn(0, progress.values())
        self.assertFalse(packet["daily_pt_task_got_flag"])

    def test_claims_restore_after_login_and_duplicate_requests_add_nothing(self):
        hero = dict(activity_id=ACTIVITY, task_id_list=[self.task])
        daily = dict(activity_id=ACTIVITY)
        response, _ = self.call(64084, daily, 64085)
        self.assertNotEqual(response["result"], 0)
        self.db.add_activity_point(UID, 1, 100)
        self.assertEqual(self.call(64078, hero, 64079)[0]["result"], 0)
        self.assertEqual(self.call(64084, daily, 64085)[0]["result"], 0)
        before = self.balance(COIN)
        self.assertGreater(before, 0)
        self.assertNotEqual(self.call(64078, hero, 64079)[0]["result"], 0)
        self.assertNotEqual(self.call(64084, daily, 64085)[0]["result"], 0)
        self.assertEqual(self.balance(COIN), before)
        now = int(time.time())
        current = payload(self.db, UID, ACTIVITY, now)
        self.assertEqual(current["got_reward_task_id_list"], [self.task])
        self.assertTrue(current["daily_pt_task_got_flag"])
        self.assertFalse(payload(self.db, UID, ACTIVITY, now + 86400)["daily_pt_task_got_flag"])

    def test_tier_uses_same_cultivate_coin_as_the_client(self):
        tier, definition = min(rewards_for(ACTIVITY).items(), key=lambda pair: pair[1]["need"])
        for iid, _ in definition["rewards"]:
            self.db.execute("INSERT OR IGNORE INTO item_catalog(id,type) VALUES(?,?)",
                            (iid, 1 if iid == 2 else 5))
        self.db.set_currency(UID, COIN, definition["need"])
        answer, _ = self.call(60054, dict(point_reward_id_list=[tier]), 60055)
        self.assertEqual(answer["result"], 0)
        self.assertEqual(self.balance(COIN), definition["need"])
        self.assertIn(tier, payload(self.db, UID, ACTIVITY, int(time.time()))["got_point_id_list"])

    def test_online_growth_refreshes_the_full_activity_snapshot(self):
        ctx = self.conn.to_handler_ctx()
        bus.emit(Events.HERO_UPGRADE, ctx, UID, hero_id=self.goal["hero"], oper="level_up")
        frames = [frame for frame in ctx.pop_pending_frames() if frame.cmd == 64077]
        self.assertEqual(len(frames), 1)
        refreshed = codec.decode(frames[0].payload, "sc_64077")
        self.assertEqual(len(refreshed["task_info_list"]), len(CATALOG[ACTIVITY]["tasks"]))


if __name__ == "__main__":
    unittest.main()
