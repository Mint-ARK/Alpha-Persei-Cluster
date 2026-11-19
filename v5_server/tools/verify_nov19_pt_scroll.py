"""联防协议的战斗、扫荡、奖励与兑换使用隔离存档。"""
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
from activity_content_service import catalog, sync_catalogs
from battle_server import parse_pb_top
import codec
import generator
import middleware
from pt_scroll_service import login_frames


ALPHA = 4324301
BETA = 4224301
HERO = 1039


class PtScrollTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.conn.resource_version = "229"
        self.db.execute("INSERT INTO hero(uid,id,unlock,level,star) VALUES(?,?,1,70,600)", (UID, HERO))
        self.db.execute("UPDATE users SET level=70 WHERE uid=?", (UID,))
        for aid, theme in ((ALPHA, 43), (BETA, 42)):
            self.db.execute("INSERT INTO activity(uid,activity_id,state,theme,template) VALUES(?,?,1,?,243)",
                            (UID, aid, theme))
        for item in (1, 4, 53221, 53214, 54129, 54128):
            self.db.execute("INSERT INTO item_catalog(id,type,category) VALUES(?,1,'currency')", (item,))
        self.db.set_currency(UID, 4, 120)
        self.db.set_currency(UID, 54129, 4)
        sessions = patch.dict(middleware._BATTLE_SESSIONS, {}, clear=True)
        sessions.start()
        self.addCleanup(sessions.stop)

    def battle(self, dest=502101, activity=ALPHA, stage_type=61, times=1, cmd=54030):
        common = dict(type=stage_type, dest=dest, activity_id=activity, battle_times=times,
                      hero_list=[dict(hero_id=HERO, hero_type=1)])
        return self.call(cmd, dict(common_info=common), 54031 if cmd == 54030 else 54039)

    def finish(self, result=1):
        bid = self.conn.battle_id[0]
        middleware.get_battle_session(bid)["result_data"] = dict(result=result, info={"battle_time": 18000})
        return self.call(54032, dict(battle_id=bid), 54033)

    def test_alpha_first_clear_rewards_and_native_shop_exchange(self):
        reply, entry = self.battle()
        self.assertEqual(reply["result"], 0)
        info = next(frame["payload"] for frame in entry.frames if frame["sc"] == 54003)
        player = parse_pb_top(parse_pb_top(info)[1])
        hero = codec.decode(parse_pb_top(player[2])[4], "battle_hero_net_rec")
        self.assertEqual(hero["hero_base_info"]["star"], 600)

        finish, response = self.finish()
        self.assertEqual(finish["result"], 0)
        reward = finish["battle_result"]["all_drop_list"][0]["gain_list"]
        self.assertIn({"id": 53221, "num": 600}, reward)
        self.assertIn(17023, response.scs)
        self.assertEqual(self.balance(4), 90)
        self.assertEqual(self.balance(53221), 600)
        self.assertEqual(self.finish()[0], finish)
        self.assertEqual(self.balance(53221), 600)

        sync_catalogs(self.db, version="229")
        goods_id = next(int(gid) for gid, goods in catalog("229")["goods"].items()
                        if goods["shop_id"] == 242 and goods["give_id"] == 510082)
        self.db.execute("INSERT INTO item_catalog(id,type,category) VALUES(510082,7,'equip')")
        purchase, _ = self.call(20012, dict(shop_id=242, buy_goods_list=[dict(buy_id=goods_id, buy_num=1)]), 20013)
        self.assertEqual(purchase["result"], 0)
        self.assertEqual(self.balance(53221), 200)
        self.assertEqual(self.balance(510082), 1)

    def test_final_and_sweep_use_the_activity_cost_and_actual_map_id(self):
        self.assertNotEqual(self.battle(dest=502201, cmd=54038)[0]["result"], 0)
        self.assertEqual(self.balance(54129), 4)
        self.assertEqual(self.battle(dest=502201)[0]["result"], 0)
        self.assertEqual(self.finish()[0]["result"], 0)
        self.assertEqual(self.balance(54129), 2)
        packets = login_frames(self.db, UID, generator.get_activity_list(self.db, UID, "229"), codec.encode)
        progresses = {row["activity_id"]: row for row in (codec.decode(p.payload, "sc_76151") for p in packets)}
        self.assertEqual(progresses[ALPHA]["clear_stage_list"], [5350304])
        self.assertEqual(progresses[ALPHA]["clear_times"], 0)
        self.assertEqual(progresses[BETA].get("clear_stage_list", []), [])
        for item in (40803, 40103, 40203, 22003):
            self.db.execute("INSERT INTO item_catalog(id,type) VALUES(?,5)", (item,))
        self.assertEqual(self.battle(dest=502201, cmd=54038)[0]["result"], 0)
        self.assertEqual(self.balance(54129), 0)
        self.assertGreaterEqual(self.balance(53221), 440)

    def test_wrong_activity_stage_type_and_failed_run_do_not_grant(self):
        self.assertNotEqual(self.battle(activity=BETA)[0]["result"], 0)
        self.assertNotEqual(self.battle(stage_type=1)[0]["result"], 0)
        self.assertEqual(self.battle(dest=501101, activity=BETA)[0]["result"], 0)
        self.assertEqual(self.finish(result=2)[0]["result"], 0)
        self.assertEqual(self.balance(4), 120)
        self.assertEqual(self.balance(53214), 0)
        self.assertEqual(self.battle(dest=501101, activity=BETA)[0]["result"], 0)
        self.assertEqual(self.finish()[0]["result"], 0)
        self.assertEqual(self.balance(53214), 600)


if __name__ == "__main__":
    unittest.main()
