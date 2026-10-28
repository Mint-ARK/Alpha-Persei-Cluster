"""Gateway handoff lifecycle with real handlers/codec and isolated sockets."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import socket
import threading
import unittest

import verify_slot_delete_ui as base
import server_net as net

HOST = base.HOST
codec = base.codec


class FakeGateway:
    def __init__(self):
        self.first = True
        self.sent = []
        self.ready = threading.Event()
        self.closed = threading.Event()
    def getsockname(self): return HOST, 8104
    def recv(self, count):
        if self.first:
            self.first = False
            return base.pack_up(10038, b'', index=7, server_idx=3)
        if not self.closed.wait(2):
            raise AssertionError('handoff gateway did not close')
        return b''
    def sendall(self, frame):
        self.sent.append(frame)
        self.ready.set()
    def shutdown(self, how):
        assert how == socket.SHUT_RDWR
        self.closed.set()


class GatewayHandoffTests(unittest.TestCase):
    def setUp(self):
        base.SlotDeleteTests.setUp(self)
        self.registry = patch.dict(net._gateway_handoffs, clear=True)
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def data(self, **changes):
        data = dict(server_id=8, user_id=2174928301, gstoken='isolated-handoff-token')
        data.update(changes)
        return data

    def test_matches_peer_slot_uid_and_token_including_mapped_ipv6(self):
        sock = FakeGateway()
        net._register_gateway_handoff(sock, HOST, self.data())
        for peer, changes in [(HOST, {'server_id':9}), (HOST, {'user_id':42}),
                              (HOST, {'gstoken':'different'}), ('192.168.0.104', {})]:
            self.assertFalse(net._complete_gateway_handoff(peer, self.data(**changes)))
            self.assertFalse(sock.closed.is_set())
        self.assertTrue(net._complete_gateway_handoff('::ffff:' + HOST, self.data()))
        self.assertTrue(sock.closed.is_set())
        self.assertFalse(net._complete_gateway_handoff(HOST, self.data()))

    def test_same_uid_saves_and_concurrent_tokens_close_only_selected_socket(self):
        first, second, third = FakeGateway(), FakeGateway(), FakeGateway()
        net._register_gateway_handoff(first, HOST, self.data())
        net._register_gateway_handoff(second, HOST, self.data(server_id=9))
        net._register_gateway_handoff(third, HOST, self.data(gstoken='another-handoff'))
        self.assertTrue(net._complete_gateway_handoff(HOST, self.data()))
        self.assertFalse(second.closed.is_set())
        self.assertFalse(third.closed.is_set())
        net._forget_gateway_handoff(first)
        self.assertEqual(len(net._gateway_handoffs), 2)

    def test_incomplete_or_undecodable_login_cannot_close_a_gateway(self):
        sock = FakeGateway()
        net._register_gateway_handoff(sock, HOST, self.data())
        for data in [None, {}, self.data(gstoken=''), self.data(server_id=0), self.data(user_id=0)]:
            self.assertFalse(net._complete_gateway_handoff(HOST, data))
        self.assertFalse(sock.closed.is_set())

    def test_repeated_handshake_replaces_old_ticket_and_peer_close_cleans_up(self):
        sock = FakeGateway()
        net._register_gateway_handoff(sock, HOST, self.data())
        net._register_gateway_handoff(sock, HOST, self.data(gstoken='next-ticket'))
        self.assertEqual(len(net._gateway_handoffs), 1)
        self.assertFalse(net._complete_gateway_handoff(HOST, self.data()))
        net._forget_gateway_handoff(sock)
        self.assertEqual(net._gateway_handoffs, {})

    def game_exchange(self, login_data, fail_send=False):
        class GameSocket:
            def __init__(self):
                self.chunks = [base.pack_up(10042, codec.encode('cs_10042', login_data),
                                           index=8, server_idx=4), b'']
                self.sent = []
            def settimeout(self, seconds): pass
            def recv(self, count): return self.chunks.pop(0)
            def sendall(self, frame):
                if fail_send: raise BrokenPipeError('isolated game send failure')
                self.sent.append(frame)
        sock = GameSocket()
        handler = net.GameHandler.__new__(net.GameHandler)
        handler.request = sock
        handler.client_address = HOST, 12346
        handler.server = SimpleNamespace(core=SimpleNamespace(log=lambda *a, **k:None), db=None,
            generator=None, codec_encode=codec.encode, codec_decode=codec.decode, game_port=8105)
        with patch('lazy_timer.lazy_timer.on_disconnect'), patch('fatigue_service.unregister_active_conn'):
            handler.handle()
        return sock

    def test_actual_game_handler_sends_reply_before_closing_matching_gateway(self):
        sock = FakeGateway()
        net._register_gateway_handoff(sock, HOST, self.data())
        response = self.game_exchange(self.data())
        self.assertTrue(sock.closed.is_set())
        frame = base.unpack_down(response.sent[0])
        self.assertEqual(frame['cmd'], 10043)
        self.assertEqual(codec.decode(frame['payload'], 'sc_10043')['result'], 0)
        self.assertEqual(net._gateway_handoffs, {})

    def test_failed_game_reply_keeps_gateway_until_it_can_complete_or_disconnect(self):
        sock = FakeGateway()
        net._register_gateway_handoff(sock, HOST, self.data())
        self.game_exchange(self.data(), fail_send=True)
        self.assertFalse(sock.closed.is_set())
        self.assertEqual(len(net._gateway_handoffs), 1)

    def test_actual_gateway_stays_open_until_real_game_handshake_then_eof(self):
        sock = FakeGateway()
        path = str(Path(__file__).resolve().parents[1] / 'account.db')
        mapping = self.manager.get_slot_mapping()
        mapping[3] = replace(mapping[3], db_path=path)
        handler = net.GatewayHandler.__new__(net.GatewayHandler)
        handler.request = sock
        handler.client_address = HOST, 12345
        handler.server = SimpleNamespace(core=SimpleNamespace(log=lambda *a, **k:None), db=None,
            material=None, codec_encode=codec.encode, game_port=8105, gw_port=8104,
            base_gw_port=8102, host_ip=HOST)
        with patch.object(base.slots, 'get_slot_manager', return_value=self.manager), \
             patch.object(self.manager, 'get_active_db', return_value=path), \
             patch('new_player_service.gateway_new_player', return_value=False), \
             patch.object(base.slots, 'switch_active_database', side_effect=AssertionError('mount forbidden')), \
             ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(handler.handle)
            try:
                self.assertTrue(sock.ready.wait(1))
                self.assertFalse(sock.closed.is_set())
                frame = base.unpack_down(sock.sent[0])
                gateway = codec.decode(frame['payload'], 'sc_10039')
                self.assertEqual(gateway['result'], 0)
                self.assertEqual(len(gateway['gstoken']), 32)
                self.game_exchange({k: gateway[k] for k in ('server_id', 'user_id', 'gstoken')})
                task.result(1)
                self.assertTrue(sock.closed.is_set())
                self.assertEqual(net._gateway_handoffs, {})
            finally:
                sock.closed.set()


if __name__ == '__main__':
    unittest.main(verbosity=2)
