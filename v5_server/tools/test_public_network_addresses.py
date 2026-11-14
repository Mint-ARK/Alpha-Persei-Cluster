"""Public/NAT addresses: selected gateway and boot wiring, fake outbound sockets."""
import ast
import errno
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cdn_proxy as cdn
import gen_cert


class AddressTests(unittest.TestCase):
    def setUp(self):
        self.sockets = []
        self.bindable = {'10.1.0.11', '192.168.0.103'}
        self.errors = {}
        self.unrouted = set()
        owner = self

        class Socket:
            closed = False

            def __init__(self, *args):
                owner.sockets.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def bind(self, address):
                self.bound = address
                ip = address[0]
                if ip in owner.errors:
                    raise OSError(owner.errors[ip], 'fixture bind failure')
                if ip not in owner.bindable:
                    raise OSError(errno.EADDRNOTAVAIL, 'fixture nonlocal address')

            def close(self):
                self.closed = True

            def settimeout(self, value):
                self.timeout = value

            def connect(self, target):
                self.target = target
                if self.bound[0] in owner.unrouted:
                    raise OSError(10051, 'fixture network unreachable')

        for manager in (patch.object(cdn.socket, 'socket', Socket),
                        patch.object(gen_cert, 'get_all_lan_ips', return_value=['10.1.0.11']),
                        patch.object(cdn, '_SOURCE_IP', None), patch.object(cdn, '_AUTO_SOURCE_IP', None),
                        patch.object(cdn, '_DNS_CACHE', {})):
            manager.start()
            self.addCleanup(manager.stop)

    def test_nat_public_ip_selects_local_nic_and_direct_socket_binds_local(self):
        selected = cdn.select_source_ip('43.139.154.74')
        self.assertEqual(selected, '10.1.0.11')
        self.assertTrue(all(sock.closed for sock in self.sockets))
        cdn.set_source_ip(selected)
        sock = cdn.create_direct_socket('1.1.1.1', 443)
        self.assertEqual(sock.bound, ('10.1.0.11', 0))
        self.assertEqual(sock.target, ('1.1.1.1', 443))
        sock.close()

    def test_lan_or_locally_assigned_public_address_preserves_explicit_interface(self):
        self.bindable.add('43.139.154.74')
        for value in ('192.168.0.103', '43.139.154.74'):
            with self.subTest(value=value):
                self.assertEqual(cdn.select_source_ip(value), value)
        gen_cert.get_all_lan_ips.assert_not_called()

    def test_windows_nonlocal_error_also_uses_local_address(self):
        self.errors['43.139.154.74'] = 10049
        self.assertEqual(cdn.select_source_ip('43.139.154.74'), '10.1.0.11')

    def test_invalid_advertisement_uses_local_route_and_permission_error_propagates(self):
        for value in ('127.0.0.1', '0.0.0.0', '198.18.0.1', '::1', 'bad'):
            with self.subTest(value=value):
                self.assertEqual(cdn.select_source_ip(value), '10.1.0.11')
        gen_cert.get_all_lan_ips.reset_mock()
        self.errors['43.139.154.74'] = errno.EACCES
        with self.assertRaises(OSError) as error:
            cdn.select_source_ip('43.139.154.74')
        self.assertEqual(error.exception.errno, errno.EACCES)
        gen_cert.get_all_lan_ips.assert_not_called()

    def test_lazy_source_skips_bindable_unrouted_interface_and_tun(self):
        self.bindable.update(('192.168.205.1', '198.18.0.1'))
        self.unrouted.add('192.168.205.1')
        gen_cert.get_all_lan_ips.return_value = ['192.168.205.1', '198.18.0.1', '192.168.0.103']
        self.assertEqual(cdn._source_ip(), '192.168.0.103')
        self.assertEqual([sock.bound[0] for sock in self.sockets], ['192.168.205.1', '192.168.0.103'])
        gen_cert.get_all_lan_ips.assert_called_once()
        self.assertEqual(cdn._source_ip(), '192.168.0.103')
        gen_cert.get_all_lan_ips.assert_called_once()

    def test_route_loss_invalidates_cached_source_and_dns(self):
        cdn.set_source_ip('192.168.0.103')
        self.assertEqual(cdn._source_ip(), '192.168.0.103')
        cdn._DNS_CACHE['open.ys4fun.com'] = ['1.1.1.1']
        self.unrouted.add('192.168.0.103')
        with self.assertRaises(OSError):
            cdn.create_direct_socket('1.1.1.1')
        self.assertEqual(cdn._DNS_CACHE, {})
        self.assertEqual(cdn._source_ip(), '10.1.0.11')

    def test_metadata_transport_failure_logs_error_code_without_signed_query(self):
        from unittest.mock import Mock
        opener = Mock(side_effect=OSError(10051, 'fixture network unreachable'))
        log = Mock()
        with patch.object(cdn, '_upstream_opener', return_value=opener):
            self.assertEqual(cdn.fetch_upstream('open.ys4fun.com', '/update', query='signature=private', log=log), (502, {}, b''))
        self.assertEqual(opener.call_count, 3)
        message = log.call_args.args[0]
        self.assertIn('10051', message)
        self.assertNotIn('signature', message)

    def test_unbindable_or_fake_local_candidates_fail_without_leaking_sockets(self):
        gen_cert.get_all_lan_ips.return_value = ['127.0.0.1', '198.18.0.1', '10.99.0.1']
        with self.assertRaises(OSError):
            cdn.select_source_ip('43.139.154.74')
        self.assertTrue(all(sock.closed for sock in self.sockets))
        self.assertIsNone(cdn._SOURCE_IP)

    def gateway(self, host_ip, remote='127.0.0.1', headers=None):
        tree = ast.parse((ROOT / 'server_net.py').read_text('utf-8-sig'))
        build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_https_app')
        route = next(n for n in build.body if isinstance(n, ast.FunctionDef) and n.name == '_route')
        branch = next(n for n in route.body if isinstance(n, ast.If) and 'gateway/get' in ast.unparse(n.test))
        fn = ast.parse('def gateway(path,qs=""):\n pass').body[0]
        fn.body = [branch]
        ast.fix_missing_locations(fn)
        paths = NS(join=os.path.join, dirname=os.path.dirname, abspath=os.path.abspath, isfile=lambda *a: False)
        env = dict(os=NS(path=paths), json=json, __file__=str(ROOT / 'server_net.py'),
                   _gen=NS(DEFAULT_UID=42), request=request, jsonify=jsonify, lan_ip=host_ip,
                   gw_port=8102, game_port=8105, time=NS(monotonic=lambda: 0), core=NS(log=lambda *args: None))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'server_net.py:gateway', 'exec'), env)
        app = Flask(__name__)
        manager = NS(configure_host=lambda _ip: None, wait_for_gateway_refresh=lambda _ip: 'idle',
                     build_zones_response=lambda client_ip, host_ip, game_port: [dict(serverId=3, ip=host_ip, port=8102)],
                     get_session_state=lambda _ip: 'NORMAL')
        with patch('save_slot_manager.get_slot_manager', return_value=manager), \
             app.test_request_context('/gateway/get?action=server', headers=headers or {},
                                      environ_base={'REMOTE_ADDR': remote}):
            return env['gateway']('gateway/get').get_json()

    def test_reverse_proxy_and_forwarded_headers_keep_published_public_gateway(self):
        for headers in ({}, {'X-Real-IP': '198.51.100.10'},
                        {'X-Forwarded-For': '198.51.100.10, 127.0.0.1'}):
            with self.subTest(headers=headers):
                data = self.gateway('43.139.154.74', headers=headers)
                self.assertEqual(data['errorCode'], '0')
                self.assertTrue(data['data'])
                self.assertEqual({(row['ip'], row['port']) for row in data['data']}, {('43.139.154.74', 8102)})

    def test_lan_and_explicit_loopback_gateway_remain_available(self):
        for host in ('192.168.0.103', '127.0.0.1'):
            for remote in ('127.0.0.1', '::1', '198.51.100.10'):
                with self.subTest(host=host, remote=remote):
                    self.assertEqual({row['ip'] for row in self.gateway(host, remote)['data']}, {host})

    def test_actual_boot_wiring_separates_publication_from_outbound_source(self):
        tree = ast.parse((ROOT / 'main.py').read_text('utf-8-sig'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        start = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'host_ip' for t in n.targets))
        block = main.body[start:start + 5]
        advertised = []
        env = dict(args=NS(host_ip='43.139.154.74', no_capture_cdn=False, capture_cdn=True, cdn_mode='off'), gen_cert=gen_cert, cdn_proxy=cdn,
                   server_net=NS(set_host_ip=advertised.append), log=lambda *a, **kw: None)
        exec(compile(ast.Module(body=block, type_ignores=[]), 'main.py:address-wiring', 'exec'), env)
        self.assertEqual(advertised, ['43.139.154.74'])
        self.assertEqual(cdn._source_ip(), '10.1.0.11')


if __name__ == '__main__':
    unittest.main(verbosity=2)
