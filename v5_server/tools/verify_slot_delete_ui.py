"""Slot delete-mode aliases, menus and actual gateway frames; no file DBs or sockets."""
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ACCOUNT_DB_PATH'] = ':memory:'
import codec
import save_slot_manager as slots
from server_net import GatewayHandler
from transport import pack_up, unpack_down

NOW = 1792828800
HOST = '192.168.0.103'


class SlotDeleteTests(unittest.TestCase):
    def setUp(self):
        self.manager = slots.SaveSlotManager.__new__(slots.SaveSlotManager)
        self.manager._lock = threading.RLock()
        self.manager._refresh_condition = threading.Condition()
        self.manager._refresh_clients = {}
        self.manager._sessions = {}
        self.manager._host_ip = ''
        self.manager.base_port = 8102
        self.manager.game_port = 8105
        self.manager.configure_host(HOST)
        mapping = {}
        for slot, port, name, action in ((1,8102,'新建','new'), (2,8103,'删除','delete_toggle'),
                                         (3,8104,'account','save'), (4,8106,'account_test','save')):
            mapping[slot] = slots.SaveSlotInfo(slot, port, None, name, 42, '探针', 1, NOW,
                                             slot == 3, action != 'save', action)
        mapping_patch = patch.object(self.manager, 'get_slot_mapping', return_value=mapping)
        mapping_patch.start(); self.addCleanup(mapping_patch.stop)
        clock = patch.object(slots.time, 'time', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)

    def menu(self, ip='127.0.0.1'):
        return self.manager.build_zones_response(ip, HOST)

    def gateway(self, port=8103, peer=HOST):
        class Socket:
            def __init__(self):
                self.chunks = [pack_up(10038, b'', index=7, server_idx=3), b'']
                self.sent = []
            def recv(self, count): return self.chunks.pop(0)
            def sendall(self, frame): self.sent.append(frame)
            def getsockname(self): return (HOST, port)
        sock = Socket()
        handler = GatewayHandler.__new__(GatewayHandler)
        handler.request = sock
        handler.client_address = (peer, 12345)
        handler.server = SimpleNamespace(core=SimpleNamespace(log=lambda *a, **k:None), db=None,
                material=None, codec_encode=codec.encode, game_port=8105, gw_port=port,
                base_gw_port=8102, host_ip=HOST)
        with patch.object(slots, 'get_slot_manager', return_value=self.manager):
            handler.handle()
        self.assertEqual(len(sock.sent), 1)
        frame = unpack_down(sock.sent[0])
        self.assertEqual(frame['cmd'], 10039)
        return codec.decode(frame['payload'], 'sc_10039')

    def test_http_loopback_and_gateway_host_share_mode(self):
        self.manager.toggle_delete_mode(HOST)
        self.assertEqual(self.manager.get_session_state('127.0.0.1'), slots.STATE_DELETE_MODE)
        self.assertEqual(self.manager.get_session_state('::1'), slots.STATE_DELETE_MODE)
        self.assertEqual(self.manager.get_session_state('192.168.0.104'), slots.STATE_NORMAL)

    def test_mapped_ipv6_and_other_lan_clients(self):
        self.manager.toggle_delete_mode('::ffff:192.168.0.103')
        self.assertEqual(self.manager.get_session_state('::ffff:127.0.0.1'), slots.STATE_DELETE_MODE)
        self.manager.toggle_delete_mode('192.168.0.104')
        self.manager.reset_session('127.0.0.1')
        self.assertEqual(self.manager.get_session_state(HOST), slots.STATE_NORMAL)
        self.assertEqual(self.manager.get_session_state('192.168.0.104'), slots.STATE_DELETE_MODE)

    def test_delete_menu_hides_new_and_preserves_ids_and_ports(self):
        self.assertEqual([r['serverId'] for r in self.menu()], ['1','2','3','4'])
        self.manager.toggle_delete_mode(HOST)
        rows = self.menu()
        self.assertEqual([(r['serverId'],r['port']) for r in rows], [('2',8103),('3',8104),('4',8106)])
        self.assertIn('退出删除模式',rows[0]['serverName'])
        self.assertTrue(all('确认删除' in r['serverName'] for r in rows[1:]))

    def test_menu_queries_do_not_toggle_mode(self):
        self.manager.toggle_delete_mode(HOST)
        self.assertEqual(self.menu(), self.menu(HOST))
        self.assertEqual(self.manager.get_session_state(HOST), slots.STATE_DELETE_MODE)

    def test_exit_from_alias_restores_normal_menu(self):
        self.manager.toggle_delete_mode(HOST)
        self.manager.toggle_delete_mode('127.0.0.1')
        self.assertEqual([r['serverId'] for r in self.menu()], ['1','2','3','4'])
        self.assertIn('删除存档',self.menu()[1]['serverName'])

    def test_mode_expiry_restores_normal_menu(self):
        self.manager.toggle_delete_mode(HOST)
        with patch.object(slots.time,'time',return_value=NOW+slots.STATE_TIMEOUT_SECONDS+1):
            self.assertEqual(self.manager.get_session_state('127.0.0.1'),slots.STATE_NORMAL)
            self.assertEqual(len(self.menu()),4)

    def test_actual_gateway_toggle_sends_one_nonexpired_notice(self):
        packet = self.gateway()
        self.assertEqual(packet['result'],1005)
        self.assertIn('已进入',packet['ban_reason'])
        self.assertEqual(packet['unblocking_time'],NOW+int(slots.STATE_TIMEOUT_SECONDS))
        self.assertGreater(packet['unblocking_time'],NOW+1)
        self.assertEqual(len(self.menu()),3)
        packet = self.gateway(peer='127.0.0.1')
        self.assertIn('退出',packet['ban_reason'])
        self.assertEqual(len(self.menu()),4)

    def test_actual_new_slot_opens_creation_menu_without_mounting(self):
        packet = self.gateway(port=8102)
        self.assertEqual(packet['result'],1005)
        self.assertIn('新建存档',packet['ban_reason'])
        self.assertGreater(packet['unblocking_time'],NOW+1)
        self.assertEqual(self.manager.get_session_state(HOST),slots.STATE_CREATE_MODE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
