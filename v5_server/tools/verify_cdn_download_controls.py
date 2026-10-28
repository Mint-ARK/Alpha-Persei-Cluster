"""Isolated policy/API/live-window checks; retains fixtures and never opens saves."""
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
        self.settings = active.DownloadSettings(self.root / 'cdn_download_settings.json')

    def test_01_defaults_and_integer_bounds(self):
        self.assertEqual(self.settings.limit(), 32)
        for value in (0, 129, -1, True, False, 32.0, '32', None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.settings.save(value)
        self.assertFalse(Path(self.settings.path).exists())

    def test_02_persistence_and_old_settings_backup(self):
        self.settings.save(128)
        self.assertEqual(active.DownloadSettings(self.settings.path).limit(), 128)
        self.settings.save(1)
        backups = list(self.root.glob('*.before_publish_*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_bytes())['max_concurrency'], 128)
        self.assertEqual(self.settings.limit(), 1)

    def test_03_failed_save_does_not_apply(self):
        with patch.object(core, 'publish_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.settings.save(64)
        self.assertEqual(self.settings.limit(), 32)

    def test_04_flask_settings_api(self):
        app = Flask(__name__)
        app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        with patch.object(active, 'settings', self.settings):
            client = app.test_client()
            endpoint = '/api/gm/system/resource_download_settings'
            self.assertEqual(client.get(endpoint).get_json()['data']['max_concurrency'], 32)
            response = client.post(endpoint, json={'max_concurrency': 48}).get_json()
            self.assertEqual(response['code'], 0)
            self.assertEqual(response['data']['max_concurrency'], 48)
            for body in ({}, {'max_concurrency': True}, {'max_concurrency': '64'}, {'max_concurrency': 129}):
                self.assertNotEqual(client.post(endpoint, json=body).get_json()['code'], 0)
            self.assertEqual(self.settings.limit(), 48)

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


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ControlChecks))
    (ROOT / 'result.json').write_text(json.dumps({'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors)}, indent=2), encoding='utf-8')
    print('Retained fixtures:', ROOT)
    sys.exit(not result.wasSuccessful())
