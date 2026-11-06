"""Isolated relay tests: upstream fixtures, temporary coordination, no real CDN/DB."""
import ast
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cdn_proxy as cdn
import cdn_transfer_control as transfer
import linux_manager as linux
from flask import Flask, request, jsonify


class Origin:
    def __init__(self, body, status=200, headers=None, error=False):
        self.status = status
        self.headers = {'content-length': str(len(body)), 'content-type': 'application/octet-stream'}
        self.headers.update({k.lower(): v for k, v in (headers or {}).items()})
        self.body = io.BytesIO(body)
        self.reads = 0
        self.closed = False
        self.error = error
    def getheader(self, name):
        return self.headers.get(name.lower())
    def read1(self, size):
        self.reads += 1
        if self.error and self.reads > 1:
            raise OSError('fixture upstream lost')
        return self.body.read(size)
    read = read1
    def close(self):
        self.closed = True


class Connection:
    _cdn_abort_detach = None
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


class RelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = ROOT.parent / 'archive/cdn_relay_20261030/fixtures'
        folder.mkdir(parents=True, exist_ok=True)
        cls.folder = Path(tempfile.mkdtemp(dir=folder))
        cls.app = Flask('relay-tests')

    def setUp(self):
        self.body = b'abcdefghijklmnopqrstuvwxyz' * 4
        self.name = hashlib.md5(self.body).hexdigest() + '.ys'
        self.origin = Origin(self.body)
        self.conn = Connection()
        self.gate = transfer.PriorityGate(self.folder / ('case-' + next(tempfile._get_candidate_names())))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(transfer, 'gate', self.gate))
        self.stack.enter_context(patch.object(cdn, 'UPSTREAM_CHUNK_SIZE', 16))
        self.opener = Mock(side_effect=lambda *a: (self.conn, self.origin))
        self.factory = self.stack.enter_context(patch.object(cdn, '_upstream_opener', return_value=self.opener))
        self.no_directory = self.stack.enter_context(patch.object(cdn, 'get_data_dir', side_effect=AssertionError('Relay must not create an asset directory')))
        self.previous_mode = cdn.get_capture_mode()
        self.addCleanup(cdn.set_capture_mode, self.previous_mode)

    def relay(self, method='GET', range_header=None):
        with self.app.test_request_context('/', method=method):
            return cdn.relay_upstream('android', self.name, range_header=range_header, log=lambda *a, **k: None)

    def assert_closed(self):
        self.assertTrue(self.conn.closed)
        self.assertTrue(self.origin.closed)
        self.assertEqual(self.gate._passive, 0)
        self.assertIsNone(transfer.current_token())
        self.no_directory.assert_not_called()

    def test_streams_before_full_read_without_asset_storage(self):
        response = self.relay()
        self.assertEqual(self.origin.reads, 0)
        self.assertEqual(self.gate._passive, 1)
        self.assertIsNone(transfer.current_token())
        iterator = iter(response.response)
        first = next(iterator)
        self.assertEqual(first, self.body[:16])
        self.assertEqual(self.origin.reads, 1)
        self.assertEqual(first + b''.join(iterator), self.body)
        response.close()
        self.assert_closed()
        self.assertFalse(list(self.gate.root.rglob('*.ys')))

    def test_client_disconnect_closes_origin_and_releases_priority(self):
        response = self.relay()
        next(iter(response.response))
        response.close()
        response.close()
        self.assert_closed()

    def test_close_before_first_chunk_also_releases_priority(self):
        response = self.relay()
        response.close()
        self.assert_closed()

    def test_head_returns_length_without_reading_body(self):
        response = self.relay(method='HEAD')
        self.assertEqual(response.content_length, len(self.body))
        self.assertEqual(response.get_data(), b'')
        self.assertEqual(self.origin.reads, 0)
        self.assertEqual(self.factory.call_args.kwargs['method'], 'HEAD')
        self.assert_closed()

    def test_range_and_suffix_range(self):
        for requested, first, last in [('bytes=10-20', 10, 20), ('bytes=90-', 90, 103), ('bytes=-8', 96, 103)]:
            with self.subTest(requested=requested):
                self.origin = Origin(self.body[first:last + 1], 206, {'Content-Range': f'bytes {first}-{last}/104'})
                self.conn = Connection()
                response = self.relay(range_header=requested)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.get_data(), self.body[first:last + 1])
                self.assertEqual(self.factory.call_args.kwargs['range_header'], requested)
                self.assert_closed()

    def test_bad_or_mismatched_range_rejected(self):
        response = self.relay(range_header='bytes=0-1,3-4')
        self.assertEqual(response.status_code, 400)
        self.factory.assert_not_called()
        self.origin = Origin(b'abc', 206, {'Content-Range': 'bytes 1-3/104'})
        self.assertEqual(self.relay(range_header='bytes=0-2').status_code, 502)
        self.assert_closed()

    def test_unsatisfied_range(self):
        self.origin = Origin(b'', 416, {'Content-Range': 'bytes */104'})
        response = self.relay(range_header='bytes=200-')
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers['Content-Range'], 'bytes */104')
        self.assert_closed()

    def test_truncated_body_raises_without_retry_or_silent_success(self):
        self.origin = Origin(self.body[:21], headers={'Content-Length': '104'})
        response = self.relay()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            response.get_data()
        self.assertEqual(self.opener.call_count, 1)
        self.assert_closed()

    def test_digest_failure_does_not_send_final_chunk(self):
        self.origin = Origin(b'X' * len(self.body))
        response = self.relay()
        emitted = []
        with self.assertRaisesRegex(ValueError, 'digest'):
            for chunk in response.response:
                emitted.append(chunk)
        self.assertLess(sum(map(len, emitted)), len(self.body))
        self.assert_closed()

    def test_origin_failure_mid_stream_does_not_restart(self):
        self.origin = Origin(self.body, error=True)
        response = self.relay()
        with self.assertRaises(OSError):
            response.get_data()
        self.assertEqual(self.opener.call_count, 1)
        self.assert_closed()

    def test_open_failure_has_three_attempt_limit(self):
        self.opener.side_effect = OSError('cannot connect')
        self.assertEqual(self.relay().status_code, 502)
        self.assertEqual(self.opener.call_count, 3)
        self.assertEqual(self.gate._passive, 0)

    def test_unexpected_compression_or_framing_rejected(self):
        for headers in ({'Content-Encoding': 'gzip'}, {'Content-Length': '-1'}, {'Transfer-Encoding': 'chunked'}):
            self.origin = Origin(self.body, headers=headers)
            self.conn = Connection()
            self.assertEqual(self.relay().status_code, 502)
            self.assert_closed()

    def test_modes_legacy_switch_and_local_cache_precedence(self):
        with patch.dict(sys.modules, resource_distribution=SimpleNamespace(resolve_resource=lambda *a: (False, None))), patch.object(cdn, 'find_cached_file', return_value=None), patch.object(cdn, 'stream_upstream_and_cache', return_value='cache') as save:
            cdn.set_capture_mode('off')
            self.assertEqual(cdn.handle_resource_request('/android/resources/' + self.name).status_code, 404)
            cdn.set_capture_mode('relay')
            response = cdn.handle_resource_request('/android/resources/' + self.name)
            self.assertEqual(response.get_data(), self.body)
            save.assert_not_called()
            cdn.set_capture_enabled(True)
            self.assertEqual(cdn.get_capture_mode(), 'relay')
            cdn.set_capture_mode('cache')
            self.assertEqual(cdn.handle_resource_request('/android/resources/' + self.name), 'cache')
            cdn.set_capture_enabled(False)
            self.assertEqual(cdn.get_capture_mode(), 'off')
            with self.assertRaises(ValueError):
                cdn.set_capture_mode('invalid')
        with patch.dict(sys.modules, resource_distribution=SimpleNamespace(resolve_resource=lambda *a: (False, None))), patch.object(cdn, 'find_cached_file', return_value='existing'), patch.object(cdn, 'serve_local_stream', return_value='local'):
            cdn.set_capture_mode('relay')
            self.assertEqual(cdn.handle_resource_request('/android/resources/' + self.name), 'local')

    def test_localhost_advertisement_uses_real_nic_for_cdn(self):
        with patch.dict(sys.modules, gen_cert=SimpleNamespace(get_all_lan_ips=lambda: ['10.0.0.8'])), patch.object(cdn.socket, 'socket') as sock:
            self.assertEqual(cdn.select_source_ip('127.0.0.1'), '10.0.0.8')
            sock.return_value.__enter__.return_value.bind.assert_called_once_with(('10.0.0.8', 0))

    def test_config_migration_and_mode_persistence_arguments(self):
        config = dict(linux.DEFAULTS, schema=2, usage='local', nginx=False, nginx_dir='/etc/nginx/conf.d')
        del config['cdn_mode']
        self.assertEqual(linux.validate_config(config)['cdn_mode'], 'off')
        config['cdn_mode'] = 'relay'
        args = linux.config_args(linux.validate_config(config))
        self.assertEqual(args[args.index('--cdn-mode') + 1], 'relay')

    def test_startup_flags_and_source_failure_degrade_to_off(self):
        tree = ast.parse((ROOT / 'main.py').read_text('utf-8-sig'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        parser = argparse.ArgumentParser()
        for node in ast.walk(main):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'add_argument' and node.value.args and isinstance(node.value.args[0], ast.Constant) and node.value.args[0].value in ('--cdn-mode', '--capture-cdn', '--no-capture-cdn'):
                exec(compile(ast.Module(body=[node], type_ignores=[]), 'flags', 'exec'), {'ap': parser})
        start = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'capture_mode' for t in n.targets))
        end = next(i for i, n in enumerate(main.body[start:], start) if isinstance(n, ast.If) and isinstance(n.test, ast.Attribute) and n.test.attr == 'self_check')
        code = compile(ast.Module(body=main.body[start:end], type_ignores=[]), 'startup', 'exec')
        for argv, error, expected in [([], False, 'off'), (['--cdn-mode', 'relay'], False, 'relay'), (['--capture-cdn'], False, 'cache'), (['--cdn-mode', 'relay', '--no-capture-cdn'], False, 'off'), (['--cdn-mode', 'relay'], True, 'off')]:
            proxy = Mock()
            if error:
                proxy.select_source_ip.side_effect = OSError('offline')
            exec(code, dict(args=parser.parse_args(argv), cdn_proxy=proxy, host_ip='127.0.0.1', log=Mock()))
            proxy.set_capture_mode.assert_called_once_with(expected)

    def test_actual_gm_mode_endpoint_preserves_legacy_calls(self):
        tree = ast.parse((ROOT / 'gm_api.py').read_text('utf-8-sig'))
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'toggle_capture_mode')
        route.decorator_list = []
        env = dict(cdn_proxy=cdn, request=request, jsonify=jsonify,
                   GMErrorCode=SimpleNamespace(SUCCESS=0, PARAM_INVALID=10001, SERVICE_ERROR=30001),
                   err=lambda code, msg: ({'code': code, 'msg': msg}, 200))
        exec(compile(ast.Module(body=[route], type_ignores=[]), 'gm-route', 'exec'), env)
        for body, mode in [({'mode': 'relay'}, 'relay'), ({'enabled': True}, 'relay'), ({'enabled': False}, 'off'), ({'enabled': True}, 'cache')]:
            with self.app.test_request_context('/', method='POST', json=body):
                response, status = env['toggle_capture_mode']()
                self.assertEqual(response.get_json()['data']['cdn_mode'], mode)
        with self.app.test_request_context('/', method='POST', json={'mode': 'invalid'}):
            response, status = env['toggle_capture_mode']()
            self.assertEqual(response['code'], 10001)


if __name__ == '__main__':
    unittest.main(verbosity=2)
