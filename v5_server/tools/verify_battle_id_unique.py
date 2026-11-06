"""A21: actual connection/session allocator, uint64 codec and memory-only entry flow."""
import os
os.environ['ACCOUNT_DB_PATH'] = ':memory:'
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codec
import core
import middleware as mw

NOW = 1793678400


class BattleIDTests(unittest.TestCase):
    def setUp(self):
        for context in (patch.object(mw, '_BATTLE_ID_LAST', 0, create=True),
                        patch.dict(mw._BATTLE_SESSIONS, {}, clear=True),
                        patch.object(mw.time, 'time', return_value=NOW)):
            context.start()
            self.addCleanup(context.stop)

    def connection(self):
        return core.Connection(uid=42, db=None, codec_encode=codec.encode, codec_decode=codec.decode, log=lambda *a, **k: None)

    def test_two_connections_same_uid_same_millisecond_keep_both_sessions(self):
        first, second = self.connection(), self.connection()
        one, two = mw._gen_battle_id(first.battle_seq), mw._gen_battle_id(second.battle_seq)
        self.assertNotEqual(one, two)
        mw.register_battle_session(one, 42, dest=1030101)
        mw.register_battle_session(two, 42, dest=1030102)
        self.assertEqual(mw.get_battle_session(one)['dest'], 1030101)
        self.assertEqual(mw.get_battle_session(two)['dest'], 1030102)
        self.assertEqual(first.battle_seq, [1])
        self.assertEqual(second.battle_seq, [1])

    def test_parallel_allocations_are_unique(self):
        def allocate(_):
            sequence = [0]
            return [mw._gen_battle_id(sequence) for _ in range(64)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            groups = list(executor.map(allocate, range(8)))
        ids = [bid for group in groups for bid in group]
        self.assertEqual(len(set(ids)), 512)
        self.assertTrue(all(group == sorted(group) for group in groups))

    def test_clock_rollback_and_new_connection_do_not_reuse_ids(self):
        with patch.object(mw.time, 'time', side_effect=[NOW + 2, NOW + 1, NOW + 1]):
            ids = [mw._gen_battle_id([0]) for _ in range(3)]
        self.assertTrue(ids[0] < ids[1] < ids[2])

    def test_large_local_sequence_keeps_uint64_wire_roundtrip(self):
        ids = [mw._gen_battle_id([9999]), mw._gen_battle_id([0]), mw._gen_battle_id([10000])]
        self.assertEqual(ids, sorted(set(ids)))
        for bid in ids:
            self.assertTrue(0 < bid < (1 << 64))
            reply = codec.decode(codec.encode('sc_54035', dict(result=0, battle_id=bid)), 'sc_54035')
            request = codec.decode(codec.encode('cs_54032', dict(battle_id=bid)), 'cs_54032')
            self.assertEqual(reply['battle_id'], bid)
            self.assertEqual(request['battle_id'], bid)

    def test_two_actual_story_entry_responses_are_distinct_and_need_no_refresh(self):
        runner = core.Core(db=None, codec_encode=codec.encode, codec_decode=codec.decode, log=lambda *a, **k: None)
        ids = []
        for dest in (1030101, 1030102):
            conn = self.connection()
            req = core.CoreRequest(54034, b'\x08' + mw._varint(dest), uid=42)
            response = runner.dispatch(req, conn)
            self.assertEqual(response.scs, [54035])
            body = codec.decode(response.frames[0]['payload'], 'sc_54035')
            self.assertEqual(body['result'], 0)
            ids.append(body['battle_id'])
            self.assertEqual(conn.battle_id[0], body['battle_id'])
            self.assertEqual(mw.get_battle_session(body['battle_id'])['dest'], dest)
        self.assertNotEqual(*ids)

    def test_uint64_exhaustion_fails_instead_of_wrapping(self):
        mw._BATTLE_ID_LAST = (1 << 64) - 1
        with self.assertRaises(OverflowError):
            mw._gen_battle_id([0])
        self.assertEqual(mw._BATTLE_ID_LAST, (1 << 64) - 1)


if __name__ == '__main__':
    unittest.main()
