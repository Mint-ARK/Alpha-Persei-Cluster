"""A105: retained settlement receipts must not replay historical state.

Actual 54032/registry/codec/wire packing and regular-Core SQL/service fragments.
The existing round76 harness explicitly restricts settlement to regular-Core;
it does not run the full rewards/BUS/UDP engine or the native Lua client.
"""
import ast
import json
from pathlib import Path
import struct
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import audit_core_regular_old_settlement_probe as fixture


class SettlementRetryTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(fixture.ROOT))
        import weekly_challenge_service as ws
        self.previous_time = ws.time
        self.addCleanup(setattr, ws, "time", self.previous_time)
        # Reuse only the original read-only probe's explicit setup, never its
        # historical "defect exists" assertions. No real DB constructor runs.
        tree = ast.parse(Path(fixture.__file__).read_text(encoding="utf-8-sig"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        prefix = []
        for node in main.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "cases" for t in node.targets
            ):
                break
            prefix.append(node)
        else:
            raise AssertionError("round76 setup boundary changed")
        env = dict(vars(fixture))
        exec(compile(ast.Module(body=prefix, type_ignores=[]), str(fixture.__file__), "exec"), env)
        self.env, self.ns, self.codec, self.ws = env, env["ns"], env["codec"], ws
        self.ns["struct"] = struct
        self.ns['threading'] = threading
        fixture.extract("middleware.py", ["build_downstream", "_varint", "_settle_battle_session",
                                         "_take_trust_mood_frames", "_settlement_error_payload"], self.ns)
        # This older regular-Core fixture excludes monster statistics and has
        # no AccountDB counter methods. Keep that boundary explicit.
        counters = patch('enemy_kill_service.record_battle', return_value=[])
        counters.start()
        self.addCleanup(counters.stop)
        self.db = fixture.make_db(self.ns)
        self.addCleanup(self.db.c.close)
        self.bid = 105001
        self.ctx = self.new_context()
        self.register(self.bid)

    def new_context(self):
        return NS(db=self.db, codec_encode=self.codec.encode, codec_decode=self.codec.decode,
                  battle_id=[None], battle_stage=[0], log=lambda *a: None)

    def register(self, bid, result=1):
        self.ns["register_battle_session"](bid, 42, dest=7102, stage_type=67, heroes=[1011])
        self.ns["get_battle_session"](bid)["result_data"] = dict(result=result, info=dict(battle_time=30000))

    def finish(self, bid=None, ctx=None):
        req = NS(uid=42, payload=self.codec.encode("cs_54032", dict(battle_id=bid or self.bid)))
        return self.ns["h_54032"](req, ctx or self.ctx)

    def decoded(self, response, cmd):
        return [self.codec.decode(f.payload, "sc_" + str(cmd)) for f in response.frames if f.cmd == cmd]

    def snapshot(self):
        return list(self.db.c.iterdump())

    def test_first_settlement_preserves_frames_and_repeat_preserves_receipt_only(self):
        first = self.finish()
        self.assertEqual(first.cmds, [75009, 54033])
        self.assertEqual(self.decoded(first, 75009)[0]["now_cycle"], 7)
        receipt = self.decoded(first, 54033)[0]
        self.assertEqual(receipt["result"], 0)
        self.assertEqual(receipt["battle_result"]["battle_id"], self.bid)
        self.assertEqual(receipt["battle_result"]["dest"], 7102)
        self.assertEqual(receipt["battle_result"]["result"], 1)
        self.assertEqual([s["id"] for s in self.ws.get_core_verification_data(42, self.db)["stage_info"]], [7101, 7102])
        before = self.snapshot()
        for _ in range(3):
            retry = self.finish()
            self.assertEqual(retry.cmds, [54033])
            self.assertEqual(retry.frames[0].payload, first.frames[-1].payload)
        self.assertEqual(self.env["calls"], [self.bid])
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("extra", self.ns["get_battle_session"](self.bid))

    def test_cross_cycle_retry_cannot_restore_cycle7_state(self):
        first = self.finish()
        self.env["clock"][0] += 1
        current = self.ws.get_core_verification_data(42, self.db)
        self.assertEqual(current["now_cycle"], 8)
        self.assertEqual(current["stage_info"], [])
        before = self.snapshot()
        retry = self.finish()
        self.assertEqual(retry.cmds, [54033])
        self.assertEqual(retry.frames[0].payload, first.frames[-1].payload)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.ws.get_core_verification_data(42, self.db), current)
        self.assertEqual(self.env["calls"], [self.bid])

    def test_same_cycle_reset_is_not_undone_by_cached_progress(self):
        self.finish()
        result = self.env["reset"](NS(uid=42), {"type": 0}, self.ctx)
        self.assertEqual(result["result"], 0)
        current = self.ws.get_core_verification_data(42, self.db)
        self.assertEqual(current["stage_info"], [])
        before = self.snapshot()
        self.assertEqual(self.finish().cmds, [54033])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.ws.get_core_verification_data(42, self.db), current)

    def test_legacy_extra_does_not_overwrite_newer_inventory_or_illustrated(self):
        self.finish()
        # Finite cache/SQL fixtures: these do not claim full inventory grants
        # or monster-classification behavior. The encoded absolute frames are real.
        self.db.execute("CREATE TABLE currency(uid,id,num)")
        self.db.execute("INSERT INTO currency VALUES(42,1,125)")
        old = [self.ns["DownFrame"](17023, self.codec.encode("sc_17023", {
                   "normal_items": [{"id": 1, "num": 100}]})),
               self.ns["DownFrame"](52009, self.codec.encode("sc_52009", {
                   "enemy_info": [{"id": 2017, "times": 1, "is_view": 0}]}))]
        self.ns["get_battle_session"](self.bid)["extra"] = old
        projected = {"inventory": 125, "enemy_times": 2}
        before = self.snapshot()
        retry = self.finish()
        for f in retry.frames:
            if f.cmd == 17023:
                projected["inventory"] = self.decoded(retry, 17023)[0]["normal_items"][0]["num"]
            if f.cmd == 52009:
                projected["enemy_times"] = self.decoded(retry, 52009)[0]["enemy_info"][0]["times"]
        self.assertEqual(projected, {"inventory": 125, "enemy_times": 2})
        self.assertEqual(retry.cmds, [54033])
        self.assertEqual(self.snapshot(), before)
        self.assertIs(self.ns["get_battle_session"](self.bid)["extra"], old)

    def test_same_uid_new_context_recovers_receipt_without_settling_again(self):
        first = self.finish()
        ctx = self.new_context()
        before = self.snapshot()
        retry = self.finish(ctx=ctx)
        self.assertEqual(retry.cmds, [54033])
        self.assertEqual(retry.frames[0].payload, first.frames[-1].payload)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.env["calls"], [self.bid])

    def test_retry_does_not_drain_unrelated_pending_notifications(self):
        self.finish()
        pending = [self.ns["DownFrame"](17023, b"fixture", flag=1, srv=88)]
        self.ctx.pending_frames = pending
        self.assertEqual(self.finish().cmds, [54033])
        self.assertIs(self.ctx.pending_frames, pending)
        self.assertEqual((pending[0].payload, pending[0].flag, pending[0].srv), (b"fixture", 1, 88))

    def test_other_battle_still_settles_and_old_receipt_does_not_revert_it(self):
        first = self.finish()
        self.register(self.bid + 1)
        second = self.finish(self.bid + 1)
        self.assertEqual(second.cmds, [75009, 54033])
        before = self.snapshot()
        retry = self.finish()
        self.assertEqual(retry.cmds, [54033])
        self.assertEqual(retry.frames[0].payload, first.frames[-1].payload)
        self.assertEqual(self.env["calls"], [self.bid, self.bid + 1])
        self.assertEqual(self.snapshot(), before)

    def test_failure_and_quit_keep_result_and_wire_request_correlation(self):
        for result in (2, 3):
            with self.subTest(result=result):
                bid = self.bid + result
                self.register(bid, result=result)
                first = self.finish(bid)
                retry = self.finish(bid)
                self.assertEqual(first.cmds, [54033])
                self.assertEqual(retry.cmds, [54033])
                payload = retry.frames[0].payload
                receipt = self.codec.decode(payload, "sc_54033")
                self.assertEqual(receipt["battle_result"]["result"], result)
                self.assertEqual(receipt["battle_result"].get("all_drop_list", []), [])
                raw = retry.to_bytes(index=17, server_idx=28)
                self.assertEqual(struct.unpack(">H", raw[:2])[0], len(raw) - 2)
                self.assertEqual(struct.unpack(">HHH", raw[5:11]), (54033, 17, 29))
                self.assertEqual(raw[11:], payload)
                self.assertEqual(payload, first.frames[-1].payload)


def negative_control(path):
    test = SettlementRetryTests()
    test.setUp()
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "h_54032")
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), test.ns)
        first = test.finish()
        test.env["clock"][0] += 1
        current = test.ws.get_core_verification_data(42, test.db)
        before = test.snapshot()
        retry = test.finish()
        replay = test.decoded(retry, 75009)[0]
        assert current["now_cycle"] == 8 and current["stage_info"] == []
        assert replay["now_cycle"] == 7 and [s["id"] for s in replay["stage_info"]] == [7101, 7102]
        assert before == test.snapshot() and test.env["calls"] == [test.bid]
        assert test.decoded(first, 54033) == test.decoded(retry, 54033)
        return dict(source=str(path), current_state=current, stale_replayed_state=replay,
                    retry_cmds=retry.cmds, settlement_calls=test.env["calls"],
                    sql_unchanged=True, receipt_unchanged=True,
                    boundary="actual regular-Core fragments/SQL/service/handler/codec; no full reward engine or native client")
    finally:
        test.doCleanups()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--negative-control":
        print(json.dumps(negative_control(sys.argv[2]), ensure_ascii=False))
    else:
        unittest.main(verbosity=2)
