"""Isolated policy/API/live-window checks; retains fixtures and never opens saves."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch

sys.dont_write_bytecode = True
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import cdn_active_downloads as active
import cdn_integrity as core
import cdn_proxy
import res_version_manager as versions
import gm_api
from flask import Flask

ROOT = SERVER.parent / 'archive/cdn_download_controls_20261027' / ('checks_' + uuid.uuid4().hex)
ROOT.mkdir(parents=True)


def isolate(event, args):
    if event == 'open':
        name, _, flags = args
        if isinstance(name, (str, bytes, os.PathLike)) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            if not Path(name).resolve().is_relative_to(ROOT):
                raise PermissionError('Probe refuses writes outside fixtures')
    if event in ('os.remove', 'os.rmdir', 'socket.connect', 'socket.bind'):
        raise PermissionError('Probe forbids deletion and network')
    if event in ('os.rename', 'shutil.copyfile'):
        if any(not Path(p).resolve().is_relative_to(ROOT) for p in args[:2]):
            raise PermissionError('Probe refuses mutation outside fixtures')


sys.addaudithook(isolate)


class ControlChecks(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / self._testMethodName
        self.root.mkdir()
        import cdn_transfer_control as transfers
        gate_patch = patch.object(transfers, 'gate', transfers.PriorityGate(self.root/'transfers'))
        gate_patch.start(); self.addCleanup(gate_patch.stop)
        recycle = patch.object(transfers, 'recycle_partial', side_effect=lambda path: Path(path).replace(str(path) + '.retained'))
        recycle.start(); self.addCleanup(recycle.stop)
        self.settings = active.DownloadSettings(self.root / 'cdn_download_settings.json')

    def test_01_defaults_and_integer_bounds(self):
        self.assertEqual(self.settings.limit(), 32)
        for value in (0, 129, -1, True, False, 32.0, '32', None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.settings.save(value)
        self.assertFalse(Path(self.settings.path).exists())

    def test_02_persistence_and_old_settings_backup(self):
        Path(self.settings.path).write_text('{"version": 1, "max_concurrency": 128}', encoding='utf-8')
        self.settings = active.DownloadSettings(self.settings.path)
        self.assertEqual(self.settings.status()['max_bandwidth_kibps'], 0)
        self.settings.save(1, max_bandwidth_kibps=1536)
        backups = list(self.root.glob('*.before_publish_*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_bytes())['max_concurrency'], 128)
        self.assertEqual(self.settings.limit(), 1)
        self.assertEqual(active.DownloadSettings(self.settings.path).status()['max_bandwidth_kibps'], 1536)

    def test_03_failed_save_does_not_apply(self):
        with patch.object(core, 'publish_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.settings.save(64, max_bandwidth_kibps=2048)
        self.assertEqual(self.settings.limit(), 32)
        self.assertEqual(self.settings.status()['max_bandwidth_kibps'], 0)
        self.assertEqual(self.settings.bandwidth.reserve(core.CHUNK_SIZE, None), (core.CHUNK_SIZE, None))

    def test_04_flask_settings_api(self):
        app = Flask(__name__)
        app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        with patch.object(active, 'settings', self.settings):
            client = app.test_client()
            endpoint = '/api/gm/system/resource_download_settings'
            self.assertEqual(client.get(endpoint).get_json()['data']['max_concurrency'], 32)
            self.assertEqual(client.post(endpoint, json={'max_bandwidth_kibps': 2048}).get_json()['code'], 0)
            response = client.post(endpoint, json={'max_concurrency': 48}).get_json()
            self.assertEqual(response['code'], 0)
            self.assertEqual(response['data']['max_concurrency'], 48)
            self.assertEqual(response['data']['max_bandwidth_kibps'], 2048)
            for body in ({}, {'max_concurrency': True}, {'max_concurrency': '64'}, {'max_concurrency': 129}):
                self.assertNotEqual(client.post(endpoint, json=body).get_json()['code'], 0)
            for value in (-1, True, None, 1.5, '2048', 2**31):
                body = {'max_concurrency': 64, 'max_bandwidth_kibps': value}
                self.assertNotEqual(client.post(endpoint, json=body).get_json()['code'], 0)
            self.assertEqual(self.settings.limit(), 48)
            response = client.post(endpoint, json={'max_bandwidth_kibps': 0}).get_json()
            self.assertEqual(response['data']['max_concurrency'], 48)
            self.assertEqual(response['data']['max_bandwidth_kibps'], 0)

    def test_05_running_window_grows_and_shrinks_without_interrupting(self):
        limit = [2]
        releases = [threading.Event() for _ in range(8)]
        condition = threading.Condition()
        started, finished, errors = [], [], []
        shrunk = threading.Event()
        cancel = threading.Event()
        def worker(index):
            with condition:
                started.append(index)
                condition.notify_all()
            if not releases[index].wait(4):
                raise TimeoutError('fixture release timeout')
            return index
        def complete(index):
            with condition:
                finished.append(index)
                condition.notify_all()
        def window(count, maximum):
            if count == 1 and maximum == 1 and len(finished) == 4:
                shrunk.set()
        def run():
            try:
                active.run_batch(range(8), worker, complete, cancel=cancel,
                    deadline=time.monotonic() + 6, get_limit=lambda: limit[0], on_window=window)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            with condition:
                self.assertTrue(condition.wait_for(lambda: len(started) == 2, timeout=2))
            limit[0] = 5
            with condition:
                self.assertTrue(condition.wait_for(lambda: len(started) == 5, timeout=2))
            limit[0] = 1
            for index in range(4):
                releases[index].set()
            self.assertTrue(shrunk.wait(2))
            self.assertEqual(len(started), 5)
            self.assertFalse(cancel.is_set())
        finally:
            for release in releases:
                release.set()
            thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(finished), list(range(8)))

    def test_06_failure_drains_other_workers(self):
        cancel = threading.Event()
        entered = threading.Barrier(2)
        drained = threading.Event()
        windows = []
        def worker(index):
            entered.wait(3)
            if index == 0:
                raise ValueError('invalid object')
            try:
                if not cancel.wait(3):
                    raise TimeoutError('cancel did not propagate')
                core.checkpoint(cancel)
            finally:
                drained.set()
        with self.assertRaisesRegex(ValueError, 'invalid object'):
            active.run_batch(range(2), worker, lambda _: None, cancel=cancel,
                deadline=time.monotonic() + 5, get_limit=lambda: 2,
                on_window=lambda *window: windows.append(window))
        self.assertTrue(drained.is_set())
        self.assertEqual(windows[-1], (0, 2))

    def test_07_cancel_drains_before_return(self):
        cancel = threading.Event()
        started, drained = threading.Event(), threading.Event()
        def worker(_):
            started.set()
            try:
                cancel.wait(3)
                core.checkpoint(cancel)
            finally:
                drained.set()
        def stop():
            if started.wait(2):
                cancel.set()
        thread = threading.Thread(target=stop)
        thread.start()
        try:
            with self.assertRaises(core.Cancelled):
                active.run_batch([0], worker, lambda _: self.fail('cancelled completion'),
                    cancel=cancel, deadline=time.monotonic() + 5, get_limit=lambda: 1)
        finally:
            cancel.set()
            thread.join(3)
        self.assertTrue(drained.is_set())

    def test_08_progress_reads_current_setting(self):
        with patch.object(active, 'settings', self.settings):
            manager = versions.UpdateTaskManager()
            self.assertEqual(manager.get_progress()['max_concurrency'], 32)
            self.settings.save(64)
            self.assertEqual(manager.get_progress()['max_concurrency'], 64)
            self.assertEqual(manager.get_progress()['active_downloads'], 0)

    def mock_upstream(self, payloads, reads, on_read=None):
        def opener(_host, path):
            name = path.rsplit('/', 1)[-1]
            payload = payloads[name]

            class Response(io.BytesIO):
                status = 200

                def getheader(self, header):
                    return str(len(payload)) if header == 'Content-Length' else None

                def read1(self, size):
                    reads.setdefault(name, []).append(size)
                    if on_read:
                        on_read(len(reads[name]))
                    return self.read(min(size, 1536))

            return lambda *_: (io.BytesIO(), Response(payload))

        for override in (patch.object(cdn_proxy, '_upstream_opener', opener),
                         patch.object(active, 'settings', self.settings)):
            override.start()
            self.addCleanup(override.stop)

    def test_09_parallel_objects_share_a_bandwidth_budget(self):
        payloads = {}
        for value in (b'A', b'B', b'C', b'D'):
            payload = value * (64 * 1024)
            payloads[hashlib.md5(payload).hexdigest() + '.ys'] = payload
        reads = {}
        self.mock_upstream(payloads, reads)
        self.settings.save(4, max_bandwidth_kibps=256)
        started = time.monotonic()
        active.run_batch(payloads, lambda name: cdn_proxy.download_verified('pc', name, self.root),
                         lambda _: None, cancel=threading.Event(), deadline=started + 6)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertEqual(reads.keys(), payloads.keys())
        for name, payload in payloads.items():
            self.assertEqual((self.root / name).read_bytes(), payload)
            self.assertLessEqual(max(reads[name]), 256 * 1024 // 20)

    def test_10_running_download_reacts_to_saved_limit(self):
        payload = b'A' * (32 * 1024)
        name = hashlib.md5(payload).hexdigest() + '.ys'
        reads = {}
        entering = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        cancel = threading.Event()

        def pause_at_read(count):
            if count <= len(entering):
                entering[count - 1].set()
                if not release[count - 1].wait(3):
                    raise TimeoutError('fixture release timeout')

        self.mock_upstream({name: payload}, reads, pause_at_read)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(cdn_proxy.download_verified, 'pc', name, self.root, cancel=cancel)
            try:
                self.assertTrue(entering[0].wait(3))
                self.settings.save(max_bandwidth_kibps=1)
                release[0].set()
                self.assertTrue(entering[1].wait(3))
                self.assertLessEqual(reads[name][1], 1024 // 20)
                self.settings.save(max_bandwidth_kibps=0)
                release[1].set()
                self.assertEqual(Path(future.result(timeout=3)).read_bytes(), payload)
                self.assertGreater(reads[name][2], 1024)
            finally:
                cancel.set()
                for event in release:
                    event.set()

    def test_11_passive_transfer_preempts_throttled_active_read(self):
        from cdn_transfer_control import gate

        payloads = {hashlib.md5(data).hexdigest() + '.ys': data for data in (b'A' * 8192, b'B' * 8192)}
        active_name, passive_name = payloads
        reads = {}
        self.mock_upstream(payloads, reads)
        self.settings.save(max_bandwidth_kibps=1)
        waiting, cancel = threading.Event(), threading.Event()
        reserve = self.settings.bandwidth.reserve

        def signal(*args):
            waiting.set()
            return reserve(*args)

        def managed_download():
            with gate.active(cancel):
                return cdn_proxy.download_verified('pc', active_name, self.root)

        with patch.object(self.settings.bandwidth, 'reserve', side_effect=signal), ThreadPoolExecutor() as pool:
            future = pool.submit(managed_download)
            try:
                self.assertTrue(waiting.wait(3))
                path = cdn_proxy.download_verified('pc', passive_name, self.root, passive=True,
                                                   deadline=time.monotonic() + 3)
                self.assertEqual(Path(path).read_bytes(), payloads[passive_name])
                self.assertEqual(reads[passive_name][0], core.CHUNK_SIZE)
                with self.assertRaises(core.Cancelled):
                    future.result(timeout=3)
                self.assertFalse((self.root / active_name).exists())
            finally:
                cancel.set()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ControlChecks))
    (ROOT / 'result.json').write_text(json.dumps({'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors)}, indent=2), encoding='utf-8')
    print('Retained fixtures:', ROOT)
    sys.exit(not result.wasSuccessful())
