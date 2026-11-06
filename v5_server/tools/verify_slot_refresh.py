"""Concurrent SDK HTTP refresh + actual gateway/codec, without player files."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from types import SimpleNamespace
from unittest.mock import patch
import threading
import time
import unittest

import verify_slot_delete_ui as base
from server_net import build_https_app

slots = base.slots
HOST = base.HOST


class SlotRefreshTests(unittest.TestCase):
    setUp = base.SlotDeleteTests.setUp
    gateway = base.SlotDeleteTests.gateway

    @classmethod
    def setUpClass(cls):
        # Prevent registration from changing GM globals; no account DB exists.
        with patch('gm_api.init_gm_api'):
            cls.app = build_https_app(SimpleNamespace(log=lambda *a, **k: None), host_ip=HOST)
        cls.app.testing = True

    def request(self):
        with self.app.test_client() as client:
            return client.get('/gateway/get?action=server&mixId=42')

    @contextmanager
    def pending_request(self):
        observed = threading.Event()
        original = self.manager._refresh_client
        def entered(key):
            entry = original(key)
            observed.set()
            return entry
        with patch.object(slots, 'get_slot_manager', return_value=self.manager), \
             patch.object(self.manager, '_refresh_client', side_effect=entered), \
             ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.request)
            self.assertTrue(observed.wait(1), 'HTTP did not enter refresh wait')
            yield future

    def rows(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        body = response.get_json()
        self.assertEqual(body['errorCode'], '0')
        return body['data']

    def test_query_before_toggle_returns_delete_menu_and_one_notice(self):
        with self.pending_request() as response:
            packet = self.gateway()
            rows = self.rows(response.result(2))
        self.assertIn('已进入', packet['ban_reason'])
        self.assertEqual([r['serverId'] for r in rows], ['2', '3', '4'])
        self.assertIn('退出删除模式', rows[0]['serverName'])
        # Maintenance=true would prevent the next selected save reaching 10038.
        self.assertTrue(all(not r['maintain'] for r in rows))

    def test_query_before_exit_returns_normal_menu(self):
        self.manager.toggle_delete_mode(HOST)
        with self.pending_request() as response:
            packet = self.gateway()
            rows = self.rows(response.result(2))
        self.assertIn('退出', packet['ban_reason'])
        self.assertEqual([r['serverId'] for r in rows], ['1', '2', '3', '4'])

    def test_delete_result_refreshes_after_registry_change_or_failure(self):
        mapping = self.manager.get_slot_mapping()
        for success in (False, True):
            with self.subTest(success=success):
                self.manager.reset_session(HOST)
                self.manager.toggle_delete_mode(HOST)
                def delete(slot_id, peer):
                    if success:
                        mapping.pop(slot_id)
                        self.manager.reset_session(peer)
                    return success, '已删除' if success else '无法删除'
                with patch.object(self.manager, 'delete_slot_save', side_effect=delete) as delete_call:
                    with self.pending_request() as response:
                        packet = self.gateway(port=8106)
                        rows = self.rows(response.result(2))
                delete_call.assert_called_once_with(4, HOST)
                self.assertNotEqual(packet['result'], 0)
                self.assertEqual([r['serverId'] for r in rows],
                                 ['1', '2', '3'] if success else ['2', '3', '4'])

    def test_gateway_exception_also_releases_pending_query(self):
        with self.pending_request() as response:
            with patch.object(base.codec, 'encode', side_effect=RuntimeError('isolated codec failure')):
                packet = self.gateway()
            rows = self.rows(response.result(2))
        self.assertEqual(packet['result'], 1)
        self.assertEqual(len(rows), 3)  # Toggle happened before encoding failed.
        self.assertEqual(self.manager._refresh_clients['__local_client__']['pending'], 0)

    def test_query_during_action_waits_past_observation_window(self):
        started, finish = threading.Event(), threading.Event()
        def action():
            with self.manager.gateway_action(HOST):
                started.set()
                if not finish.wait(2):
                    raise AssertionError('fixture action was not released')
        with ThreadPoolExecutor(max_workers=1) as pool:
            action_future = pool.submit(action)
            self.assertTrue(started.wait(1))
            original_wait = self.manager.wait_for_gateway_refresh
            with patch.object(self.manager, 'wait_for_gateway_refresh',
                              side_effect=lambda ip: original_wait(ip, window=0.01, timeout=1)), \
                 self.pending_request() as response:
                try:
                    self.manager.toggle_delete_mode(HOST)
                    with self.assertRaises(FutureTimeout):
                        response.result(0.03)
                    # Nested completion must not release the outer action.
                    with self.manager.gateway_action('127.0.0.1'):
                        pass
                    self.assertFalse(response.done())
                finally:
                    finish.set()
                self.assertEqual(len(self.rows(response.result(2))), 3)
            action_future.result(1)

    def test_no_gateway_and_other_client_have_bounded_idle_wait(self):
        started = time.monotonic()
        with self.manager.gateway_action('192.168.0.104'):
            result = self.manager.wait_for_gateway_refresh(HOST, window=0.03, timeout=0.1)
        elapsed = time.monotonic() - started
        self.assertEqual(result, 'idle')
        self.assertGreaterEqual(elapsed, 0.025)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(self.manager.get_session_state(HOST), slots.STATE_NORMAL)

    def test_action_timeout_is_bounded_even_with_registry_lock_held(self):
        def wait():
            self.manager.configure_host(HOST)
            return self.manager.wait_for_gateway_refresh('127.0.0.1', window=0.01, timeout=0.04)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.manager.gateway_action(HOST), self.manager._lock:
                started = time.monotonic()
                self.assertEqual(pool.submit(wait).result(1), 'timeout')
                self.assertLess(time.monotonic() - started, 0.5)

    def test_http_timeout_does_not_read_busy_registry_or_return_stale_success(self):
        with patch.object(slots, 'get_slot_manager', return_value=self.manager), \
             patch.object(self.manager, 'wait_for_gateway_refresh', return_value='timeout'), \
             patch.object(self.manager, 'build_zones_response') as build:
            response = self.request()
        self.assertEqual(response.status_code, 503)
        build.assert_not_called()

    def test_repeated_list_gets_do_not_execute_actions(self):
        with patch.object(slots, 'get_slot_manager', return_value=self.manager), \
             patch.object(self.manager, 'toggle_delete_mode') as toggle, \
             patch.object(self.manager, 'delete_slot_save') as delete:
            for _ in range(2):
                self.assertEqual(len(self.rows(self.request())), 4)
        toggle.assert_not_called()
        delete.assert_not_called()

    def test_expired_refresh_clients_pruned_without_discarding_active_actions(self):
        self.manager._refresh_clients = {
            'old': {'pending': 0, 'revision': 1, 'touched': time.monotonic() - 61},
            'active': {'pending': 1, 'revision': 0, 'touched': time.monotonic() - 61},
        }
        self.manager.wait_for_gateway_refresh(HOST, window=0)
        self.assertNotIn('old', self.manager._refresh_clients)
        self.assertIn('active', self.manager._refresh_clients)


    def test_followup_query_waits_for_gateway_beyond_initial_window(self):
        self.assertEqual(self.manager.wait_for_gateway_refresh(HOST, window=0), 'idle')
        with patch.object(slots, 'REGION_REFRESH_FOLLOWUP_WINDOW_SECONDS', 1.5), \
             self.pending_request() as response:
            with self.assertRaises(FutureTimeout):
                response.result(0.5)
            self.gateway(port=8102)
            rows = self.rows(response.result(1))
        self.assertEqual([row['serverId'] for row in rows], ['1','2','3','4'])
        self.assertIn('返回', rows[0]['serverName'])

    def test_first_and_unrelated_client_queries_keep_short_window(self):
        self.manager.wait_for_gateway_refresh('192.168.0.104', window=0)
        with patch.object(slots, 'REGION_REFRESH_WINDOW_SECONDS', 0.01), \
             patch.object(slots, 'REGION_REFRESH_FOLLOWUP_WINDOW_SECONDS', 0.5):
            started = time.monotonic()
            self.assertEqual(self.manager.wait_for_gateway_refresh(HOST), 'idle')
            self.assertLess(time.monotonic()-started, 0.2)

    def test_followup_wait_is_bounded_without_gateway(self):
        self.manager.wait_for_gateway_refresh(HOST, window=0)
        with patch.object(slots, 'REGION_REFRESH_WINDOW_SECONDS', 0.001), \
             patch.object(slots, 'REGION_REFRESH_FOLLOWUP_WINDOW_SECONDS', 0.03):
            started = time.monotonic()
            self.assertEqual(self.manager.wait_for_gateway_refresh(HOST), 'idle')
            self.assertLess(time.monotonic()-started, 0.3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
