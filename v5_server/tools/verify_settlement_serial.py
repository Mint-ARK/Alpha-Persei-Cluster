"""A20: actual Core/session/cache/Inventory/SQL/codec with a controlled settlement boundary."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import codec
import core
import middleware as mw
import enemy_kill_service
from inventory_service import InventoryService
from operations import _refresh_frames


class SerialTests(MemoryTests):
    def setUp(self):
        super().setUp()
        # One memory connection is shared deliberately; the fixture's SQL lock
        # protects this adapter, independently of the production session lock.
        shared = sqlite3.connect(':memory:', check_same_thread=False)
        shared.row_factory = sqlite3.Row
        previous = self.db._conn()
        previous.backup(shared)
        previous.close()
        self.db._local.conn = shared
        self.shared = shared
        for context in (patch.dict(mw._BATTLE_SESSIONS, {}, clear=True),
                        patch.object(enemy_kill_service, 'record_battle', return_value=[])):
            context.start()
            self.addCleanup(context.stop)
        mw.register_battle_session(20001, UID, dest=1030101, heroes=[1084], is_story=True)
        mw.register_battle_session(20002, UID, dest=1030102, heroes=[1084], is_story=True)
        self.sql_lock = threading.Lock()

    def finish(self, bid=20001):
        self.db._local.conn = self.shared
        conn = core.Connection(uid=UID, db=self.db, codec_encode=codec.encode, codec_decode=codec.decode, log=lambda *a, **k: None)
        response = self.core.dispatch(core.CoreRequest(54032, codec.encode('cs_54032', dict(battle_id=bid)), uid=UID), conn)
        answer = next(codec.decode(f['payload'], 'sc_54033') for f in response.frames if f['sc'] == 54033)
        return answer, response

    def grant(self, ctx, uid, bid, result, dest, **kwargs):
        with self.sql_lock:
            InventoryService.grant_item(ctx, uid, 1, 10, source='settlement-serial-probe')
            frames = _refresh_frames(ctx, uid, strict=True)
        return mw.build_battle_result(bid, result, dest, clear_times=1), frames

    def test_same_battle_waits_then_replays_receipt_without_second_award(self):
        first = threading.Event()
        second = threading.Event()
        release = threading.Event()
        started = threading.Event()
        entries = []
        guard = threading.Lock()
        def settle(*args, **kwargs):
            with guard:
                entries.append(args[2])
                (first if len(entries) == 1 else second).set()
            if not release.wait(3):
                raise AssertionError('fixture release missing')
            return self.grant(*args, **kwargs)
        def retry():
            started.set()
            return self.finish()
        with patch.object(mw, '_settle_frames', side_effect=settle), ThreadPoolExecutor(max_workers=2) as workers:
            one = workers.submit(self.finish)
            try:
                self.assertTrue(first.wait(2))
                two = workers.submit(retry)
                self.assertTrue(started.wait(2))
                second_entered = second.wait(0.25)
            finally:
                release.set()
            results = [one.result(3), two.result(3)]
        self.assertFalse(second_entered, (entries, self.balance(1)))
        self.assertEqual(entries, [20001])
        self.assertEqual(self.balance(1), 10)
        self.assertEqual([answer['result'] for answer, _ in results], [0, 0])
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sum(response.scs.count(17023) for _, response in results), 1)
        self.assertEqual(results[1][1].scs, [54033])

    def test_different_battles_can_enter_settlement_concurrently(self):
        together = threading.Barrier(2)
        def settle(*args, **kwargs):
            together.wait(2)
            return self.grant(*args, **kwargs)
        with patch.object(mw, '_settle_frames', side_effect=settle), ThreadPoolExecutor(max_workers=2) as workers:
            one = workers.submit(self.finish, 20001)
            two = workers.submit(self.finish, 20002)
            results = [one.result(4), two.result(4)]
        self.assertEqual([answer['result'] for answer, _ in results], [0, 0])
        self.assertEqual(self.balance(1), 20)

    def test_failure_before_award_releases_session_for_retry(self):
        calls = []
        def settle(*args, **kwargs):
            calls.append(args[2])
            if len(calls) == 1:
                raise RuntimeError('isolated failure before award')
            return self.grant(*args, **kwargs)
        with patch.object(mw, '_settle_frames', side_effect=settle):
            self.assertNotEqual(self.finish()[0]['result'], 0)
            self.assertNotIn('br', mw.get_battle_session(20001))
            self.assertEqual(self.finish()[0]['result'], 0)
            self.assertEqual(self.finish()[0]['result'], 0)
        self.assertEqual(calls, [20001, 20001])
        self.assertEqual(self.balance(1), 10)

    def test_response_failure_keeps_receipt_and_does_not_repeat_inventory(self):
        original = codec.encode
        def fail(name, *args, **kwargs):
            return None if name == 'sc_54033' else original(name, *args, **kwargs)
        with patch.object(mw, '_settle_frames', side_effect=self.grant) as settle:
            with patch.object(codec, 'encode', side_effect=fail):
                answer, _ = self.finish()
            self.assertNotEqual(answer['result'], 0)
            self.assertEqual(self.balance(1), 10)
            answer, response = self.finish()
        self.assertEqual(answer['result'], 0)
        self.assertEqual(response.scs, [54033])
        self.assertEqual(settle.call_count, 1)

    def test_cached_receipt_never_overwrites_newer_assets(self):
        with patch.object(mw, '_settle_frames', side_effect=self.grant) as settle:
            first, _ = self.finish()
            self.db.set_currency(UID, 1, 99)
            repeated, response = self.finish()
        self.assertEqual(first, repeated)
        self.assertEqual(response.scs, [54033])
        self.assertEqual(self.balance(1), 99)
        self.assertEqual(settle.call_count, 1)


if __name__ == '__main__':
    unittest.main()
