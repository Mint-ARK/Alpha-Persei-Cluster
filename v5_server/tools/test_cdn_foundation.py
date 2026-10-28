"""Offline acceptance/fault probes; retain all fixtures under project archive."""
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch

sys.dont_write_bytecode = True
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import cdn_integrity as core
import cdn_proxy as cdn
import res_version_manager as versions
import fetch_manifests
from flask import Flask

ROOT = SERVER.parent / 'archive' / 'cdn_foundation_repair_20261027' / ('checks_' + uuid.uuid4().hex)
ROOT.mkdir(parents=True)
RESULTS = []


def within_fixture(path):
    # Windows may surface an equivalent long-path spelling during concurrent
    # publication. Compare canonical paths in the same namespace.
    clean=lambda p: Path(os.path.normcase(str(Path(p).resolve()).removeprefix('\\\\?\\')))
    return clean(path).is_relative_to(clean(ROOT))


def isolate(event, args):
    if event == 'open':
        filename, _, flags = args
        if isinstance(filename, (str, bytes, os.PathLike)) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            if not within_fixture(filename):
                raise PermissionError('Probe refuses writes outside fixtures: '+str(filename))
    if event in ('os.remove', 'os.rmdir'):
        raise PermissionError('Probe forbids physical deletion')
    if event in ('os.rename', 'shutil.copyfile'):
        if any(not within_fixture(p) for p in args[:2]):
            raise PermissionError('Probe refuses mutation outside fixtures')
    if event in ('socket.connect', 'socket.bind'):
        raise PermissionError('Probe forbids network')


sys.addaudithook(isolate)


class Response:
    def __init__(self, body, status=200, headers=None, reset=False):
        self.body, self.status, self.reset = io.BytesIO(body), status, reset
        self.headers = {k.lower(): str(v) for k, v in (headers or {}).items()}

    def getheader(self, name):
        return self.headers.get(name.lower())

    def getheaders(self):
        return list(self.headers.items())

    def read(self, n=-1):
        if self.reset and self.body.tell() > 0:
            raise ConnectionResetError('injected reset')
        return self.body.read(min(n, 17) if n > 0 else n)


class Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def md5(data):
    return hashlib.md5(data).hexdigest()


def manifest():
    body = json.dumps({'appVersion': 307, 'buildCode': 321, 'versionName': 'v5.3.1',
                       'assetHashList': ['thing.ys|' + md5(b'GOOD') + '|4']}).encode()
    return 'assethash_307_321_' + md5(body) + '.bytes', body


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / self._testMethodName
        self.root.mkdir()
        import cdn_transfer_control as transfers
        gate_patch = patch.object(transfers, 'gate', transfers.PriorityGate(self.root/'transfers'))
        gate_patch.start(); self.addCleanup(gate_patch.stop)
        recycle_patch = patch.object(transfers, 'recycle_partial', side_effect=lambda path: Path(path).replace(str(path)+'.recycled'))
        recycle_patch.start(); self.addCleanup(recycle_patch.stop)
        self.payload = b'0123456789'
        self.spec = core.ObjectSpec(md5(self.payload) + '.ys', len(self.payload))

    def tearDown(self):
        RESULTS.append(self._testMethodName)

    def opener(self, response):
        connections = []
        def open_response(attempt, timeout):
            conn = Connection()
            connections.append(conn)
            return conn, response() if callable(response) else response
        return open_response, connections

    def put(self, payload=None):
        target = self.root / self.spec.filename
        target.write_bytes(self.payload if payload is None else payload)
        return target

    def test_01_full_and_warm_file_no_network(self):
        opener, conns = self.opener(Response(self.payload, headers={'Content-Length': 10}))
        path = core.ensure_file(self.spec, self.root, opener)
        self.assertEqual(Path(path).read_bytes(), self.payload)
        self.assertTrue(conns[0].closed)
        with patch.object(core.hashlib, 'md5', side_effect=AssertionError('warm rehash')):
            core.ensure_file(core.ObjectSpec(self.spec.filename), self.root,
                             lambda *_: self.fail('warm download'))

    def test_02_corrupt_equal_size_cache_not_accepted(self):
        target = self.put(b'XXXXXXXXXX')
        self.assertFalse(core.valid_file(target, self.spec))
        opener, _ = self.opener(Response(self.payload))
        core.ensure_file(self.spec, self.root, opener)
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertEqual(len(list(self.root.glob('*.before_publish_*'))), 1)

    def test_03_faults_never_replace_old(self):
        for label, response in {
            'short': Response(b'123', headers={'Content-Length': 10}),
            'long': Response(self.payload + b'X', headers={'Content-Length': 10}),
            'hash': Response(b'XXXXXXXXXX', headers={'Content-Length': 10}),
            'range': Response(self.payload, status=206, headers={'Content-Range': 'bytes 0-9/10'}),
            'encoding': Response(self.payload, headers={'Content-Encoding': 'gzip'}),
            'status': Response(b'error', status=503),
            'ambiguous': Response(self.payload, headers={'Content-Length': 10, 'Transfer-Encoding': 'chunked'}),
            'badlength': Response(self.payload, headers={'Content-Length': '-1'}),
            'reset': Response(b'X' * 80, reset=True),
        }.items():
            with self.subTest(label=label):
                directory = self.root / label
                directory.mkdir()
                target = directory / self.spec.filename
                target.write_bytes(b'ORIGINAL')
                opener, conns = self.opener(response)
                with self.assertRaises((ValueError, OSError)):
                    core.ensure_file(self.spec, directory, opener, max_attempts=1)
                self.assertEqual(target.read_bytes(), b'ORIGINAL')
                self.assertTrue(all(c.closed for c in conns))

    def test_04_persisted_bytes_not_network_hash(self):
        opener, _ = self.opener(Response(self.payload))
        real_open = open
        class MutatingWriter:
            def __init__(self, f): self.f = f
            def __enter__(self): return self
            def __exit__(self, *args): return self.f.__exit__(*args)
            def __getattr__(self, key): return getattr(self.f, key)
            def write(self, data): return self.f.write(b'X' * len(data))
        def mutate(path, mode='r', *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            return MutatingWriter(handle) if mode == 'xb' else handle
        with patch('builtins.open', side_effect=mutate), self.assertRaises(core.IntegrityError):
            core.ensure_file(self.spec, self.root, opener)
        self.assertFalse((self.root / self.spec.filename).exists())

    def test_05_disk_failure_and_replace_failure_preserve_old(self):
        old = self.put(b'OLD')
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                opener, _ = self.opener(Response(self.payload))
                with patch.object(core.os, operation, side_effect=OSError('injected disk failure')):
                    with self.assertRaises((ValueError, OSError)):
                        core.ensure_file(self.spec, self.root, opener, max_attempts=1)
                self.assertEqual(old.read_bytes(), b'OLD')

    def test_06_parser_path_and_conflict_rejection(self):
        data = {'appVersion': 307, 'buildCode': 321, 'assetHashList': ['p|' + 'a' * 32 + '|4']}
        self.assertEqual(core.manifest_items(data), {'p': ('a' * 32, 4)})
        for filename in ('../../escaped', 'C:\\oops.ys', 'a/b', 'NUL', '%2e%2e.json'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                core.ObjectSpec(filename)
        for entries in (['p|hash|4'], ['p|' + 'a' * 32 + '|-1'],
                        ['p|' + 'a' * 32 + '|4', 'p|' + 'b' * 32 + '|4'],
                        ['p|' + 'a' * 32 + '|4', 'q|' + 'a' * 32 + '|5']):
            with self.assertRaises(ValueError):
                core.manifest_items({'assetHashList': entries})
        with self.assertRaises(ValueError):
            core.decode_manifest(b'{"x":1,"x":2}', 'manifest.json')

    def test_07_invalid_manifest_not_published(self):
        for name in ('voice_package_list_307_321.bytes', 'voice_hash_ja_30700321.bytes', 'manifest.json'):
            opener, _ = self.opener(Response(b'<html>failure</html>'))
            with self.assertRaises(ValueError):
                core.ensure_file(core.ObjectSpec(name), self.root, opener)
            self.assertFalse((self.root / name).exists())
        name, body = manifest()
        opener, _ = self.opener(Response(body))
        self.assertTrue(Path(core.ensure_file(core.ObjectSpec(name), self.root, opener)).exists())

    def test_08_http_local_ranges_and_validators(self):
        path = self.put()
        app = Flask(__name__)
        cases = [('bytes=-4', None, 'GET', 206, b'6789'),
                 ('bytes=2-5', None, 'GET', 206, b'2345'),
                 ('bytes=7-', None, 'GET', 206, b'789'),
                 ('bytes=50-', None, 'GET', 416, b''),
                 ('bytes=8-2', None, 'GET', 416, b''),
                 ('bytes=1-2', '"old"', 'GET', 200, self.payload),
                 ('bytes=1-2', '"' + md5(self.payload) + '"', 'GET', 206, b'12'),
                 ('bytes=1-2', None, 'HEAD', 200, b'')]
        for range_value, validator, method, status, body in cases:
            with self.subTest(range=range_value, method=method, validator=validator):
                headers = {'Range': range_value}
                if validator: headers['If-Range'] = validator
                with app.test_request_context('/', method=method, headers=headers):
                    response = cdn.serve_local_stream(str(path), range_value)
                    self.assertEqual(response.status_code, status)
                    wire_body = b''.join(response.get_app_iter({'REQUEST_METHOD': method}))
                    self.assertEqual(wire_body, body)
                    self.assertGreaterEqual(int(response.headers.get('Content-Length', '0')), 0)
                    response.close()

    def test_09_platform_shared_fallback_and_host_rejected(self):
        static = self.root / 'static'
        static.mkdir()
        name = 'voice_package_list_307_321.bytes'
        (static / name).write_bytes(b'{"infos":[]}')
        with patch.object(cdn, 'DEFAULT_STATIC_DIR', str(static)), \
             patch.object(cdn, '_SERVER_DIR', str(self.root / 'server')), \
             patch.object(cdn.os, 'name', 'posix'), \
             patch.object(cdn, 'PLATFORM_DIRS', {'ios': str(self.root / 'ios')}), \
             patch.object(cdn.storage, 'history', return_value=[]):
            self.assertIsNone(cdn.find_cached_file(name, 'ios'))
        with patch.object(cdn, 'download_verified', side_effect=AssertionError('untrusted request')):
            self.assertEqual(cdn.handle_resource_request('/pc/resources/' + self.spec.filename,
                             host='download.attacker.invalid').status_code, 400)

    def test_10_runtime_does_not_send_invalid_upstream_body(self):
        with patch.object(cdn, 'download_verified', side_effect=core.IntegrityError('bad digest')):
            response = cdn.stream_upstream_and_cache('/pc/resources/' + self.spec.filename,
                                                     log=lambda *a, **k: None)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(self.payload, response.get_data())

    def test_11_retry_limit_and_cancellation(self):
        attempts = []
        def opener(attempt, timeout):
            attempts.append(attempt)
            raise ConnectionResetError('reset before response')
        with self.assertRaises(ValueError):
            core.ensure_file(self.spec, self.root, opener)
        self.assertEqual(attempts, [0, 1, 2])
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(core.Cancelled):
            core.ensure_file(self.spec, self.root, lambda *_: self.fail('cancelled request'), cancel=cancel)
        with self.assertRaises(TimeoutError):
            core.ensure_file(self.spec, self.root, opener, deadline=time.monotonic() - 1)

    def test_12_concurrent_single_object_download_once(self):
        opened, outcomes = [], []
        gate = threading.Barrier(3)
        def opener(*_):
            opened.append(1)
            time.sleep(0.05)
            return Connection(), Response(self.payload)
        def download():
            gate.wait()
            outcomes.append(core.ensure_file(self.spec, self.root, opener))
        threads = [threading.Thread(target=download) for _ in range(2)]
        for thread in threads: thread.start()
        gate.wait()
        for thread in threads: thread.join(3)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(len(opened), 1)

    def test_13_real_http_framing_parser(self):
        class Sock:
            def __init__(self, raw): self.raw = raw
            def makefile(self, *args): return io.BytesIO(self.raw)
        raws = [(b'Content-Length: 10\r\n\r\n' + self.payload, True),
                (b'Content-Length: 10\r\n\r\n123', False),
                (b'Transfer-Encoding: chunked\r\n\r\n4\r\n0123\r\n6\r\n456789\r\n0\r\n\r\n', True),
                (b'Transfer-Encoding: chunked\r\n\r\n4\r\n0123\r\n', False)]
        for index, (raw, success) in enumerate(raws):
            directory = self.root / str(index)
            directory.mkdir()
            response = http.client.HTTPResponse(Sock(b'HTTP/1.1 200 OK\r\n' + raw))
            response.begin()
            opener, _ = self.opener(response)
            if success:
                core.ensure_file(self.spec, directory, opener, max_attempts=1)
                self.assertEqual((directory / self.spec.filename).read_bytes(), self.payload)
            else:
                with self.assertRaises(ValueError):
                    core.ensure_file(self.spec, directory, opener, max_attempts=1)
                self.assertFalse((directory / self.spec.filename).exists())

    def test_14_metadata_error_connection_always_closed(self):
        opener, conns = self.opener(lambda: Response(b'X' * 80, reset=True))
        with patch.object(cdn, '_upstream_opener', return_value=opener):
            status, _, body = cdn.fetch_upstream('open.ys4fun.com', '/', log=lambda *_: None)
        self.assertEqual((status, body), (502, b''))
        self.assertEqual(len(conns), 3)
        self.assertTrue(all(c.closed for c in conns))

    def test_15_update_failures_not_completed_or_registered(self):
        name, _ = manifest()
        info = dict(version='321', version_name='v5.3.1', app_version='307', assethash=name)
        manager = versions.UpdateTaskManager()
        with patch.object(versions, 'probe_official_latest', return_value=(True, info, '')), \
             patch.object(versions, 'calculate_manifest_diff', return_value={'success': True, 'diff_summary': {}}), \
             patch('resource_distribution.get_platform_config', return_value={'version': '321'}), \
             patch.object(cdn, 'download_verified', side_effect=core.IntegrityError('503')), \
             patch.object(versions, 'register_version') as register:
            manager._run_worker(None, True, ('ios',))
        self.assertEqual(manager.get_progress()['status'], 'error')
        self.assertEqual(manager.get_progress()['downloaded_files'], 0)
        register.assert_not_called()

    def test_16_cancel_waits_until_worker_terminates(self):
        entered, release = threading.Event(), threading.Event()
        name, _ = manifest()
        info = dict(version='321', version_name='v5.3.1', app_version='307', assethash=name)
        def blocked_probe(**_):
            entered.set()
            release.wait(3)
            return True, info, ''
        manager = versions.UpdateTaskManager()
        with patch.object(versions, 'probe_official_latest', side_effect=blocked_probe), \
             patch.object(versions, 'register_version') as register:
            self.assertTrue(manager.start_sync(platforms=('pc',))[0])
            self.assertTrue(entered.wait(1))
            manager.cancel()
            self.assertEqual(manager.get_progress()['status'], 'cancelling')
            self.assertFalse(manager.start_sync(platforms=('pc',))[0])
            release.set()
            manager._thread.join(3)
        self.assertEqual(manager.get_progress()['status'], 'cancelled')
        self.assertFalse(manager.is_busy())
        register.assert_not_called()

    def test_17_actual_manifest_loader_and_cli_parser(self):
        name, raw = manifest()
        static = self.root / 'pc'
        static.mkdir()
        (static / name).write_bytes(raw)
        config = {'assethash': {'pc': name}, 'voice_hashes': {}}
        with patch.object(versions, 'get_version_config', return_value=config):
            tasks = core.load_platform_tasks('pc', self.root / 'missing', static)
        self.assertEqual(tasks, {md5(b'GOOD'): ('thing.ys', 4)})
        self.assertNotIn('buildCode', tasks)
        with patch.object(cdn, 'download_verified') as download:
            self.assertIsNone(versions._load_manifest_content('pc', '../../escaped.json'))
            download.assert_not_called()

    def test_18_prepare_uses_common_verified_downloader(self):
        name, _ = manifest()
        with patch.object(cdn, 'download_verified', side_effect=core.IntegrityError('invalid')):
            self.assertFalse(fetch_manifests.download_single_manifest('pc', name, str(self.root / name), log=lambda *_: None))
        self.assertFalse((self.root / name).exists())

    def test_19_web_single_platform_success_uses_engine(self):
        name, raw = manifest()
        info = dict(version='321', version_name='v5.3.1', app_version='307', assethash=name)
        objects = {name: raw,
            'voice_package_list_307_321.bytes': b'{"infos":[]}',
            'voice_hash_ja_30700321.bytes': b'{"version":30700321,"assetHashList":[]}',
            'voice_hash_zh_30700321.bytes': b'{"version":30700321,"assetHashList":[]}',
            md5(b'GOOD') + '.ys': b'GOOD'}
        requests = []
        (self.root / 'data' / 'ios').mkdir(parents=True)
        def get_dir(plat):
            self.assertEqual(plat, 'ios')
            directory = self.root / 'data' / plat
            return str(directory)
        def open_request(_host, path, **_):
            requests.append(path)
            self.assertTrue(path.startswith('/ios/resources/'))
            return self.opener(Response(objects[path.rsplit('/', 1)[-1]]))[0]
        diff = {'success': True, 'diff_summary': {'modified_files': [], 'added_files': [
            {'new_hash': md5(b'GOOD'), 'new_size': 4, 'path': 'thing.ys'}]}}
        manager = versions.UpdateTaskManager()
        with patch.object(versions, 'probe_official_latest', return_value=(True, info, '')), \
             patch.object(versions, 'calculate_manifest_diff', return_value=diff), \
             patch('resource_distribution.get_platform_config', return_value={'version': '321'}), \
             patch.object(versions, 'STATIC_RESOURCES_DIR', str(self.root / 'static')), \
             patch.object(cdn, 'get_data_dir', side_effect=get_dir), \
             patch.object(cdn, '_upstream_opener', side_effect=open_request), \
             patch('resource_distribution.commit_official_bundles', side_effect=lambda _plans, commit: commit()), \
             patch.object(versions, 'register_version') as register:
            manager._run_worker(None, True, ('ios',))
        self.assertEqual(manager.get_progress()['status'], 'completed', manager.get_progress().get('error'))
        self.assertEqual(manager.get_progress()['downloaded_files'], 5)
        self.assertEqual(len(requests), 5)
        register.assert_called_once()
        self.assertEqual((self.root / 'data/ios' / (md5(b'GOOD') + '.ys')).read_bytes(), b'GOOD')
        self.assertFalse((self.root / 'data/pc').exists())

    def test_20_tls_sni_and_closed_handshake_failure(self):
        from types import SimpleNamespace
        contexts, sockets = [], []
        class Raw:
            closed = False
            def close(self): self.closed = True
        def tls():
            import ssl
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            contexts.append(context)
            return context
        def socket_factory(*_args, **_kwargs):
            raw = Raw()
            sockets.append(raw)
            return raw
        with patch.object(cdn, '_TLS_CONTEXT', None), \
             patch.object(cdn.ssl, 'create_default_context', side_effect=tls), \
             patch.object(cdn, 'resolve_doh', return_value=['203.0.113.1']), \
             patch.object(cdn, 'create_direct_socket', side_effect=socket_factory):
            opener = cdn._upstream_opener('download-eo.ys4fun.com', '/ios/resources/file')
            with self.assertRaises((AttributeError, TypeError)):
                opener(0, 1)
        import ssl
        self.assertTrue(contexts[0].check_hostname)
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(sockets[0].closed)

    def test_21_cli_paths_use_shared_engine_and_hashes(self):
        import importlib.util
        def load(name):
            spec = importlib.util.spec_from_file_location(name, SERVER / 'tools' / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            with patch.object(cdn, 'get_data_dir', return_value=str(self.root)):
                spec.loader.exec_module(module)
            return module
        pc, ios = load('download_pc_assets'), load('download_ios_assets')
        target = self.put()
        with patch.object(cdn, 'download_verified', return_value=str(target)) as download:
            self.assertTrue(pc.download_single_file(self.spec.filename, 10, str(self.root)))
            self.assertEqual(download.call_args.kwargs['expected_size'], 10)
            self.assertTrue(ios.download_single_file((md5(self.payload), ('p', 10)), data_dir=str(self.root))[0])
            self.assertEqual(download.call_args.kwargs['expected_md5'], md5(self.payload))
            before = download.call_count
            self.assertFalse(ios.download_single_file(('../../escaped', ('p', 10)), data_dir=str(self.root))[0])
            self.assertEqual(download.call_count, before)

    def test_22_version_registration_failure_does_not_report_completed(self):
        before = dict(versions.VERSION_CONFIGS)
        supported = list(versions.SUPPORTED_VERSIONS)
        with patch.object(versions, '_save_persisted_dynamic_versions', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                versions.register_version('9999', {'version_name': 'v5.3.1'})
        self.assertEqual(versions.VERSION_CONFIGS, before)
        self.assertEqual(versions.SUPPORTED_VERSIONS, supported)

    def test_23_cancel_during_receive_does_not_publish(self):
        cancel = threading.Event()
        response = Response(self.payload)
        original_read = response.read
        def read(n):
            result = original_read(n)
            cancel.set()
            return result
        response.read = read
        opener, conns = self.opener(response)
        with self.assertRaises(core.Cancelled):
            core.ensure_file(self.spec, self.root, opener, cancel=cancel)
        self.assertFalse((self.root / self.spec.filename).exists())
        self.assertTrue(conns[0].closed)

    def test_24_changed_cache_invalidates_prior_validation(self):
        path = self.put()
        self.assertTrue(core.valid_file(path, self.spec))
        before = path.stat()
        path.write_bytes(b'XXXXXXXXXX')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertFalse(core.valid_file(path, self.spec))

    def test_25_transient_retry_succeeds_without_partial_append(self):
        attempts, conns = [], []
        def opener(attempt, _timeout):
            attempts.append(attempt)
            conn = Connection()
            conns.append(conn)
            return conn, Response(b'error', status=503) if attempt == 0 else Response(self.payload)
        path = core.ensure_file(self.spec, self.root, opener)
        self.assertEqual(Path(path).read_bytes(), self.payload)
        self.assertEqual(attempts, [0, 1])
        self.assertTrue(all(c.closed for c in conns))

    def test_26_process_lock_downloads_one_copy(self):
        import subprocess
        code = '''
import sys,time,pathlib,io,hashlib
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
import cdn_integrity as c
root=pathlib.Path(sys.argv[2]);body=b'0123456789'
spec=c.ObjectSpec(hashlib.md5(body).hexdigest()+'.ys',len(body))
class Conn:
 def close(self):pass
class Resp:
 status=200
 def __init__(self):self.stream=io.BytesIO(body)
 def getheader(self,name):return None
 def read(self,n):return self.stream.read(n)
def opener(*args):
 (root/(sys.argv[3]+'.opened')).write_text('1')
 time.sleep(0.2)
 return Conn(),Resp()
c.ensure_file(spec,root,opener,deadline=time.monotonic()+5)
'''
        processes = [subprocess.Popen([sys.executable, '-B', '-c', code, str(SERVER),
                        str(self.root), str(index)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for index in range(2)]
        try:
            for process in processes:
                _, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
        self.assertEqual(len(list(self.root.glob('*.opened'))), 1)
        self.assertEqual((self.root / self.spec.filename).read_bytes(), self.payload)

    def _parallel_validation(self, path, spec, outcome, expected=True):
        """Hold a leader until all twelve other requests have joined its flight."""
        from concurrent.futures import ThreadPoolExecutor, Future
        gate = threading.Barrier(14)
        waiters_ready, entered, release = threading.Event(), threading.Event(), threading.Event()
        guard = threading.Lock()
        waiters = 0
        calls = []
        class ObservedFuture(Future):
            def result(self, timeout=None):
                nonlocal waiters
                with guard:
                    waiters += 1
                    if waiters == 12:
                        waiters_ready.set()
                return super().result(timeout)
        def validate(handle, object_spec, key):
            calls.append(1)
            entered.set()
            if not release.wait(4):
                raise TimeoutError('probe leader release timed out')
            return outcome(handle, object_spec, key)
        def request_file():
            gate.wait(4)
            return core.valid_file(path, spec)
        with patch.object(core, 'Future', ObservedFuture), \
             patch.object(core, '_validate_uncached_file', side_effect=validate):
            with ThreadPoolExecutor(max_workers=13) as pool:
                jobs = [pool.submit(request_file) for _ in range(13)]
                try:
                    gate.wait(4)
                    self.assertTrue(entered.wait(2))
                    self.assertTrue(waiters_ready.wait(3))
                finally:
                    release.set()
                self.assertEqual([job.result(timeout=4) for job in jobs], [expected] * 13)
        self.assertEqual(len(calls), 1)
        self.assertFalse(core._validation_flights)

    def test_27_thirteen_cold_cache_requests_hash_once(self):
        path = self.put()
        self._parallel_validation(path, self.spec, core._validate_uncached_file)
        with patch.object(core, '_validate_uncached_file', side_effect=AssertionError('warm validation')):
            self.assertTrue(core.valid_file(path, self.spec))

    def test_28_bad_file_shared_rejection_and_repair(self):
        path = self.put(b'XXXXXXXXXX')
        self._parallel_validation(path, self.spec, core._validate_uncached_file, expected=False)
        with patch.object(core, '_validate_uncached_file', side_effect=AssertionError('repeat bad hash')):
            self.assertFalse(core.valid_file(path, self.spec))
        path.write_bytes(self.payload)
        self.assertTrue(core.valid_file(path, self.spec))

    def test_29_different_files_validate_in_parallel(self):
        from concurrent.futures import ThreadPoolExecutor
        inside = threading.Barrier(13)
        actual = core._validate_uncached_file
        def validate(handle, spec, key):
            # All thirteen leaders must enter before any one can finish.
            inside.wait(4)
            return actual(handle, spec, key)
        tasks = []
        for index in range(13):
            payload = f'different resource {index}'.encode()
            spec = core.ObjectSpec(md5(payload) + '.ys', len(payload))
            path = self.root / spec.filename
            path.write_bytes(payload)
            tasks.append((path, spec))
        with patch.object(core, '_validate_uncached_file', side_effect=validate):
            with ThreadPoolExecutor(max_workers=13) as pool:
                jobs = [pool.submit(core.valid_file, path, spec) for path, spec in tasks]
                self.assertEqual([job.result(timeout=6) for job in jobs], [True] * 13)

    def test_30_failed_leader_releases_waiters_and_allows_retry(self):
        path = self.put()
        def failed(*_):
            raise OSError('injected disk read error')
        self._parallel_validation(path, self.spec, failed, expected=False)
        self.assertTrue(core.valid_file(path, self.spec))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FoundationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT / 'result.json').write_text(json.dumps({'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'methods': RESULTS}, indent=2), encoding='utf-8')
    print('Retained fixtures:', ROOT)
    sys.exit(not result.wasSuccessful())
