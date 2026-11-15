"""Memory-only checks for the battle entry handshake and timing path."""
from pathlib import Path
import struct
import sys
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import battle_server as battle


class BattleEntryTests(unittest.TestCase):
    CLIENT_ID = 42
    ADDRESS = ("127.0.0.1", 6105)

    def setUp(self):
        self.logs = []
        self.packets = []
        self.server = battle.BattleServer.__new__(battle.BattleServer)
        self.server._lock = threading.Lock()
        self.server._conns = {}
        self.server._pong_seq = {}
        self.server._results = {}
        self.server.server_ts = None
        self.server.running = False
        self.server.log = self.logs.append
        self.server.sock = SimpleNamespace(sendto=lambda packet, addr: self.packets.append((packet, addr)))
        self.handshake()
        self.st = self.server._conns[self.CLIENT_ID]

    def tearDown(self):
        self.server.stop()

    def handshake(self, client_id=None):
        client_id = self.CLIENT_ID if client_id is None else client_id
        self.packets.clear()
        syn = bytes([battle.SYN]) + struct.pack("<II", client_id, client_id)
        self.server._handle_pkt(syn, self.ADDRESS)
        self.assertEqual(len(self.packets), 1)
        output = self.packets[0]
        if isinstance(output, tuple):
            packet, address = output
            self.assertEqual(address, self.ADDRESS)
        else:
            packet = output
        self.assertEqual(packet[0], battle.ACK)
        server_id, echoed_client_id = struct.unpack("<II", packet[1:9])
        self.assertEqual(echoed_client_id, client_id)
        self.assertEqual(server_id, self.server._conns[client_id]["conn"])
        return self.server._conns[client_id]

    def send_frame(self, st, opcode, body=b"", flag=2, rpc_id=0, sn=700):
        frame = battle.xs_encode_frame(opcode, body, flag, rpc_id)
        segment = struct.pack("<IBBHIIII", st["conn"], battle.KCP_CMD_PUSH, 0, 256,
                              1234, sn, 0, len(frame)) + frame
        packet = bytes([battle.MSG]) + struct.pack("<I", st["client_id"]) + segment
        self.packets.clear()
        self.server._handle_pkt(packet, self.ADDRESS)
        result = []
        for item in self.packets:
            packet = item[0] if isinstance(item, tuple) else item
            if packet[0] == battle.MSG and len(packet) >= 32 and packet[9] == battle.KCP_CMD_PUSH:
                result.append(self.decode_server_frame(packet))
        return result

    @staticmethod
    def decode_server_frame(packet):
        segment = packet[9:]
        length = struct.unpack_from("<I", segment, 16)[0]
        return battle.xs_parse_frame(segment[20:20 + length])

    def test_syn_handshake_emits_ack9_and_initializes_session(self):
        self.assertIs(self.server._conns[self.CLIENT_ID], self.st)
        self.assertGreater(self.st["conn"], 0)
        self.assertEqual(self.st["conv"], self.CLIENT_ID)
        self.assertEqual(self.server._pong_seq, {})
        self.assertTrue(any("-> ACK9" in line for line in self.logs))

    def test_ready_acknowledges_battle_and_starts_match(self):
        frames = self.send_frame(self.st, battle.OP_PLAYER_READY,
                                 battle._f_varint2(1, 81) + battle._f_varint2(2, 72), rpc_id=19)
        self.assertEqual([(f["opcode"], f["flag"], f["rpc_id"]) for f in frames],
                         [(battle.OP_ACK_PLAYER_READY, 2, 19), (battle.OP_START_MATCH, 0, 0)])
        self.assertEqual(battle.parse_pb_top(frames[0]["body"]), {1: 72})
        self.assertTrue(self.st["started"])
        self.assertTrue(self.st["player_ready"])
        self.assertTrue(any("入场Ready" in line for line in self.logs))

    def test_pong_echoes_client_milliseconds_and_uses_calibrated_delta_sequence(self):
        client_ms = 1_792_771_200_123
        expected = list(battle.PONG_DELTAS) + [battle.PONG_DELTAS[-1]]
        for seq, delta in enumerate(expected):
            frames = self.send_frame(self.st, battle.OP_PING, struct.pack("<Q", client_ms + seq),
                                     rpc_id=100 + seq, sn=700 + seq)
            self.assertEqual(len(frames), 1)
            frame = frames[0]
            self.assertEqual((frame["opcode"], frame["rpc_id"]), (battle.OP_PONG, 100 + seq))
            echoed, server_ms = struct.unpack("<QQ", frame["body"])
            self.assertEqual(echoed, client_ms + seq)
            self.assertEqual(server_ms - echoed, delta)
        self.assertEqual(self.server._pong_seq[self.CLIENT_ID], len(expected))

    def test_configured_server_time_keeps_millisecond_precision(self):
        self.server.server_ts = lambda: 1_792_771_200.456
        frames = self.send_frame(self.st, battle.OP_PING, struct.pack("<Q", 44), rpc_id=7)
        self.assertEqual(struct.unpack("<QQ", frames[0]["body"]), (44, 1_792_771_200_456))

    def test_reconnect_replaces_session_and_old_ping_is_ignored(self):
        old = self.st
        self.send_frame(old, battle.OP_PING, struct.pack("<Q", 5))
        replacement = self.handshake()
        self.assertIsNot(replacement, old)
        self.assertEqual(self.server._pong_seq, {})
        before = len(self.packets)
        self.server._handle_frame(battle.xs_encode_frame(battle.OP_PING, struct.pack("<Q", 6)), old)
        self.assertEqual(len(self.packets), before)
        self.assertIs(self.server._conns[self.CLIENT_ID], replacement)
        self.assertNotIn(self.CLIENT_ID, self.server._pong_seq)

    def test_malformed_and_unknown_packets_do_not_crash_or_mutate_session(self):
        original = self.st
        self.packets.clear()
        for packet in (b"", b"\x01", b"\x01\x00\x00\x00\x00", b"\x06" + b"\x00" * 40):
            self.server._handle_pkt(packet, self.ADDRESS)
        for frame in (b"", b"\x02\x64", battle.xs_encode_frame(999, b"unknown")):
            self.server._handle_frame(frame, original)
        self.assertIs(self.server._conns[self.CLIENT_ID], original)
        self.assertEqual(self.server._pong_seq, {})
        self.assertEqual(self.packets, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
