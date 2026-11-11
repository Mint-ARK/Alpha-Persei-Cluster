"""Isolated Flask tests. Fixture files retained under root archive; no DB/listeners."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['ACCOUNT_DB_PATH'] = ':memory:'
from flask import Flask, Blueprint, jsonify
import admin_access as access
import admin_session_auth as auth

ORIGIN = 'https://admin.example.test'
PASSWORD = 'test-only-long-password'


class PasswordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = auth.hash_password(PASSWORD)

    def setUp(self):
        parent = ROOT.parent / 'archive/admin_password_20261105/fixtures'
        parent.mkdir(parents=True, exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='case_', dir=parent))
        self.file = self.folder / 'auth.json'
        self.config = dict(schema=1, password_hash=self.password_hash, origin=ORIGIN)
        auth._write_private(self.file, json.dumps(self.config).encode())
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ))
        os.environ.pop(access.AUTH_FILE_ENV, None)
        os.environ[auth.PASSWORD_FILE_ENV] = str(self.file)
        with auth._STATES_LOCK:
            auth._STATES.clear()
        self.app = self.make_app()
        self.client = self.app.test_client()

    def replace_config(self, values):
        self.file.rename(self.folder / ('before_' + str(len(list(self.folder.iterdir()))) + '.json'))
        auth._write_private(self.file, json.dumps(values).encode())

    def make_app(self):
        app = Flask(__name__)
        app.config['mutations'] = 0
        access.install_admin_guard(app)
        bp = Blueprint('gm_api', __name__)
        @bp.route('/secret', methods=['GET', 'POST'])
        def secret():
            if auth.request.method == 'POST':
                app.config['mutations'] += 1
            return jsonify(key='fixture-secret-only')
        @bp.after_request
        def old_cors(response):
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        @bp.route('/explode')
        def explode():
            raise RuntimeError('isolated failure')
        app.register_blueprint(bp, url_prefix='/api/gm')
        app.add_url_rule('/api/system/res_version', 'shell_res_version', secret)
        # An alias outside the normal namespace must still require CSRF.
        def alias():
            app.config['mutations'] += 1
            return jsonify(ok=True)
        alias.__module__ = 'gm_api'
        app.add_url_rule('/legacy-admin-alias', 'alias', alias)
        @app.get('/sdk/login')
        @app.get('/assets/game.bundle')
        @app.get('/web/app.js')
        def public():
            return 'public'
        @app.errorhandler(Exception)
        def error(_):
            return jsonify(error='fixture'), 500
        return app

    def request(self, method, path, *, client=None, **kwargs):
        return (client or self.client).open(path, method=method, base_url=ORIGIN, **kwargs)

    def login(self, client=None, password=PASSWORD, **extra):
        return self.request('POST', '/api/gm/auth/login', client=client,
                            json={'password': password}, headers={'Origin': ORIGIN}, **extra)

    def headers(self, token):
        return {'Origin': ORIGIN, auth.CSRF_HEADER: token}

    def test_single_password_cookie_no_basic_bypass_and_private_read_write(self):
        basic = 'Basic ' + base64.b64encode(('admin:' + PASSWORD).encode()).decode()
        self.assertEqual(self.request('GET', '/api/gm/secret', headers={'Authorization': basic}).status_code, 401)
        login = self.login(); self.assertEqual(login.status_code, 200)
        cookie = login.headers['Set-Cookie']
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(attribute, cookie)
        self.assertNotIn('Domain=', cookie)
        self.assertNotIn(PASSWORD, login.get_data(as_text=True))
        token = login.json['csrf_token']; headers = self.headers(token)
        self.assertEqual(self.request('GET', '/api/gm/secret').status_code, 403)
        self.assertEqual(self.request('GET', '/api/gm/secret', headers=headers).status_code, 200)
        self.assertEqual(self.request('POST', '/api/gm/secret', headers=headers).status_code, 200)
        self.assertEqual(self.app.config['mutations'], 1)
        self.assertEqual(self.request('GET', '/legacy-admin-alias').status_code, 403)
        self.assertEqual(self.request('GET', '/legacy-admin-alias', headers=headers).status_code, 200)

    def test_sdk_cdn_static_and_disabled_mode_keep_contract(self):
        for path in ('/sdk/login', '/assets/game.bundle', '/web/app.js'):
            self.assertEqual(self.client.get(path).status_code, 200)
        os.environ.pop(auth.PASSWORD_FILE_ENV)
        app = self.make_app(); client = app.test_client()
        self.assertEqual(client.get('/api/gm/secret').status_code, 200)
        self.assertFalse(client.get('/api/gm/auth/session').json['enabled'])
        self.assertEqual(client.post('/api/gm/auth/login', json={'password': PASSWORD}).status_code, 404)

    def test_console_aliases_and_unknown_auth_routes_cannot_reach_fallback(self):
        for path in ('/web//gm_console.html', '/web/./gm_console.html', '/api/system/res_version',
                     '/legacy-admin-alias'):
            self.assertEqual(self.request('GET', path).status_code, 401, path)
        self.assertEqual(self.request('POST', '/api/gm/auth/missing', json={'password': PASSWORD}).status_code, 404)
        self.assertEqual(self.request('GET', '/api/gm/auth/login').status_code, 405)
        self.assertEqual(self.request('POST', '/api/gm/auth/login?password=never-use-query').status_code, 400)

    def test_login_schema_origin_https_and_spoofed_proxy_are_rejected(self):
        for payload in ({'username': 'admin', 'password': PASSWORD}, {'password': 3}, {'password': 'x'*5000}, []):
            self.assertEqual(self.request('POST', '/api/gm/auth/login', json=payload, headers={'Origin': ORIGIN}).status_code, 400)
        self.assertEqual(self.request('POST', '/api/gm/auth/login', json={'password': PASSWORD}).status_code, 403)
        self.assertEqual(self.request('POST', '/api/gm/auth/login', json={'password': PASSWORD}, headers={'Origin': 'https://evil.test'}).status_code, 403)
        self.assertEqual(self.client.post('/api/gm/auth/login', base_url='http://admin.example.test', json={'password': PASSWORD},
                        headers={'Origin': ORIGIN, 'X-Forwarded-Proto': 'https'}).status_code, 403)
        self.assertEqual(self.client.get('/api/gm/auth/session', base_url='https://wrong.example.test').status_code, 403)

    def test_cors_csrf_rejections_preflight_and_errors_never_keep_wildcard(self):
        response = self.request('GET', '/api/gm/secret', headers={'Origin': ORIGIN})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers['Access-Control-Allow-Origin'], ORIGIN)
        login = self.login(); token = login.json['csrf_token']
        for method, path, headers, expected in (
                ('POST', '/api/gm/secret', {'Origin': ORIGIN}, 403),
                ('POST', '/api/gm/secret', {auth.CSRF_HEADER: token}, 403),
                ('GET', '/api/gm/secret', self.headers('错'*43), 403),
                ('GET', '/api/gm/secret', dict(self.headers(token), Origin='https://evil.test'), 403),
                ('OPTIONS', '/api/gm/secret', {'Origin': ORIGIN}, 204),
                ('OPTIONS', '/api/gm/secret', {'Origin': 'https://evil.test'}, 403),
                ('GET', '/api/gm/explode', self.headers(token), 500)):
            response = self.request(method, path, headers=headers)
            self.assertEqual(response.status_code, expected)
            self.assertNotEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.app.config['mutations'], 0)

    def test_session_expiry_logout_and_reauthentication_rotate_tokens(self):
        with patch.object(auth.time, 'monotonic', return_value=100):
            login = self.login(); token = login.json['csrf_token']
            cookie = self.client.get_cookie(auth.COOKIE_NAME, domain='admin.example.test').value
            self.assertTrue(self.request('GET', '/api/gm/auth/session').json['authenticated'])
            second = self.login(); new_token = second.json['csrf_token']
            self.assertNotEqual(token, new_token)
            self.assertIsNone(next(iter(auth._STATES.values())).session(cookie))
            self.assertEqual(self.request('POST', '/api/gm/auth/logout', headers=self.headers(new_token)).status_code, 200)
            self.assertEqual(self.request('GET', '/api/gm/secret', headers=self.headers(new_token)).status_code, 401)
            self.login()
        with patch.object(auth.time, 'monotonic', return_value=3701):
            self.assertFalse(self.request('GET', '/api/gm/auth/session').json['authenticated'])

    def test_rate_limits_before_hash_ignore_untrusted_xff_and_expire(self):
        with patch.object(auth.PasswordConfig, 'verify', return_value=False) as verify:
            with patch.object(auth.time, 'monotonic', return_value=100):
                for i in range(5):
                    response = self.request('POST', '/api/gm/auth/login', json={'password': 'wrong'},
                        headers={'Origin': ORIGIN, 'X-Forwarded-For': '198.51.100.' + str(i)})
                    self.assertEqual(response.status_code, 401)
                self.assertEqual(self.login(password='wrong').status_code, 429)
                self.assertEqual(verify.call_count, 5)
            with patch.object(auth.time, 'monotonic', return_value=161):
                self.assertEqual(self.login(password='wrong').status_code, 401)

    def test_trusted_proxy_chain_uses_nearest_untrusted_client_and_ignores_left_spoof(self):
        self.replace_config(dict(self.config, trusted_proxies=['127.0.0.1/32']))
        with patch.object(auth.PasswordConfig, 'verify', return_value=False):
            for i in range(5):
                response = self.client.post('/api/gm/auth/login', base_url='http://admin.example.test',
                    json={'password': 'wrong'}, headers={'Origin': ORIGIN, 'X-Forwarded-Proto': 'https',
                    'X-Forwarded-For': '203.0.113.' + str(i) + ', 198.51.100.9'})
                self.assertEqual(response.status_code, 401)
            response = self.client.post('/api/gm/auth/login', base_url='http://admin.example.test',
                json={'password': 'wrong'}, headers={'Origin': ORIGIN, 'X-Forwarded-Proto': 'https',
                'X-Forwarded-For': '198.51.100.9'})
            self.assertEqual(response.status_code, 429)
            # Another real client behind the same explicitly trusted proxy has its own budget.
            self.assertEqual(self.request('POST', '/api/gm/auth/login', json={'password': 'wrong'},
                headers={'Origin': ORIGIN, 'X-Forwarded-For': '198.51.100.10'}).status_code, 401)

    def test_global_limit_and_bucket_expiry_bound_address_churn(self):
        with patch.object(auth.PasswordConfig, 'verify', return_value=False), patch.object(auth.time, 'monotonic', return_value=100):
            for i in range(auth.GLOBAL_ATTEMPTS):
                self.assertEqual(self.login(password='wrong', environ_overrides={'REMOTE_ADDR': '198.51.100.'+str(i+1)}).status_code, 401)
            self.assertEqual(self.login(password='wrong', environ_overrides={'REMOTE_ADDR': '203.0.113.9'}).status_code, 429)
            state = next(iter(auth._STATES.values()))
            self.assertLessEqual(len(state.attempts), auth.GLOBAL_ATTEMPTS)
        with patch.object(auth.time, 'monotonic', return_value=161):
            state.reserve_attempt('203.0.113.9'); state.finish_attempt()
            self.assertEqual(len(state.attempts), 1)

    def test_concurrent_attempts_reserve_capacity_before_expensive_verification(self):
        entered = threading.Event(); release = threading.Event(); count = [0]; lock = threading.Lock()
        def verify(_):
            with lock:
                count[0] += 1
                if count[0] == 2:
                    entered.set()
            if not release.wait(5):
                raise RuntimeError('test barrier timeout')
            return False
        def attempt(ip):
            return self.login(client=self.app.test_client(), password='wrong', environ_overrides={'REMOTE_ADDR': ip}).status_code
        with patch.object(auth.PasswordConfig, 'verify', side_effect=verify), ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(attempt, '198.51.100.'+str(i)) for i in (1, 2)]
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(pool.submit(attempt, '198.51.100.3').result(timeout=5), 429)
                self.assertEqual(count[0], 2)
            finally:
                release.set()
            self.assertEqual([job.result(timeout=5) for job in jobs], [401, 401])
        self.assertEqual(next(iter(auth._STATES.values())).in_flight, 0)

    def test_shared_apps_share_sessions_limits_and_password_rotation_revoke_all(self):
        app2 = self.make_app(); client2 = app2.test_client()
        login = self.login(); login2 = self.login(client2)
        token = login.json['csrf_token']; token2 = login2.json['csrf_token']
        new = PASSWORD + '-changed'
        response = self.request('POST', '/api/gm/auth/password', headers=self.headers(token),
            json={'current_password': PASSWORD, 'new_password': new})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.request('GET', '/api/gm/secret', client=client2, headers=self.headers(token2)).status_code, 401)
        self.assertEqual(len(list(self.folder.glob('auth.json.bak.*'))), 1)
        raw = self.file.read_text()
        self.assertNotIn(new, raw); self.assertNotIn(PASSWORD, raw)
        self.assertTrue(auth.load_password_config(str(self.file)).verify(new))
        self.assertEqual(self.login(password=PASSWORD).status_code, 401)
        self.assertEqual(self.login(password=new).status_code, 200)
        self.assertEqual(self.login(client=client2, password='wrong').status_code, 429)

    def test_password_write_failure_preserves_old_credential_and_session(self):
        token = self.login().json['csrf_token']; before = self.file.read_bytes()
        with patch.object(auth.os, 'replace', side_effect=OSError('isolated write failure')):
            response = self.request('POST', '/api/gm/auth/password', headers=self.headers(token),
                json={'current_password': PASSWORD, 'new_password': PASSWORD+'-new'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.request('GET', '/api/gm/secret', headers=self.headers(token)).status_code, 200)
        self.assertEqual(len(list(self.folder.glob('*.bak.*'))), 1)

    def test_password_change_requires_live_session_csrf_current_password_and_strength(self):
        payload = dict(current_password=PASSWORD, new_password=PASSWORD+'-new')
        before = self.file.read_bytes()
        self.assertEqual(self.request('POST', '/api/gm/auth/password', json=payload,
                                     headers={'Origin': ORIGIN}).status_code, 401)
        token = self.login().json['csrf_token']
        self.assertEqual(self.request('POST', '/api/gm/auth/password', json=payload,
                                     headers={'Origin': ORIGIN}).status_code, 403)
        for current, new, status in ((PASSWORD, 'short', 400), ('wrong', PASSWORD+'-new', 401)):
            response = self.request('POST', '/api/gm/auth/password', headers=self.headers(token),
                json={'current_password': current, 'new_password': new})
            self.assertEqual(response.status_code, status)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertTrue(self.request('GET', '/api/gm/auth/session').json['authenticated'])

    def test_external_config_replacement_revokes_sessions_and_invalid_file_fails_closed(self):
        token = self.login().json['csrf_token']
        self.replace_config(dict(self.config, session_seconds=300))
        self.assertEqual(self.request('GET', '/api/gm/secret', headers=self.headers(token)).status_code, 401)
        self.replace_config({'schema': 1})
        self.assertEqual(self.request('GET', '/api/gm/auth/session').status_code, 503)
        self.file.rename(self.folder / 'removed-config-retained.json')
        self.assertEqual(self.request('GET', '/api/gm/secret').status_code, 503)
        self.assertEqual(self.client.get('/sdk/login').status_code, 200)

    def test_enabled_bad_or_conflicting_configuration_never_falls_back_to_open(self):
        for values in ({}, dict(self.config, username='admin'), dict(self.config, origin='http://admin.test'),
                       dict(self.config, origin='https://admin.test/path'), dict(self.config, trusted_proxies=['0.0.0.0/0']),
                       dict(self.config, session_seconds=0), dict(self.config, password_hash='invalid')):
            self.replace_config(values)
            with self.assertRaises(access.AdminAuthConfigError):
                self.make_app()
        self.replace_config(self.config)
        os.environ[access.AUTH_FILE_ENV] = str(self.file)
        with self.assertRaises(access.AdminAuthConfigError):
            self.make_app()
        os.environ.pop(access.AUTH_FILE_ENV)
        os.environ[auth.PASSWORD_FILE_ENV] = ''
        with self.assertRaises(access.AdminAuthConfigError):
            self.make_app()

    def test_config_in_public_static_directory_is_rejected(self):
        # No write to deployment assets: resolve/stat path behavior is isolated.
        public = ROOT / 'static_web' / 'private-auth.json'
        with patch.object(auth.Path, 'resolve', side_effect=lambda **kwargs: public):
            with self.assertRaises(access.AdminAuthConfigError):
                auth.load_password_config(str(public))

    def test_actual_app_route_census_and_real_blueprint_cors_are_guarded_before_db(self):
        import server_net
        import gm_api
        with patch.object(gm_api, 'init_gm_api'), patch.object(gm_api, '_global_get_db', side_effect=AssertionError('real DB forbidden')):
            app = server_net.build_https_app(None)
            client = app.test_client()
            entry = self.request('GET', '/', client=client, follow_redirects=True)
            self.assertEqual(entry.status_code, 200)
            self.assertIn(b'<html', entry.data.lower())
            entry.close()
            for path in ('/web/gm_console.html', '/static_web/gm_console.html'):
                head = self.request('HEAD', path, client=client)
                self.assertEqual(head.status_code, 200)
                head.close()
                self.assertEqual(self.request('POST', path, client=client).status_code, 401)
                self.assertEqual(self.request('GET', path, client=client,
                                              headers={'Origin': 'https://evil.test'}).status_code, 403)
            self.assertFalse(self.request('GET', '/api/gm/auth/session', client=client).json['authenticated'])
            count = 0
            for rule in app.url_map.iter_rules():
                if not rule.endpoint.startswith('gm_api.') and rule.endpoint != 'shell_res_version':
                    continue
                path = re.sub(r'<(?:[^:>]+:)?[^>]+>', 'fixture', str(rule))
                for method in sorted(rule.methods - {'OPTIONS', 'HEAD'}):
                    response = self.request(method, path, client=client, headers={'Origin': ORIGIN})
                    self.assertEqual(response.status_code, 401, (method, path, rule.endpoint))
                    self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), ORIGIN)
                    count += 1
            self.assertGreater(count, 80)
            response = self.request('OPTIONS', '/api/gm/aichat/config', client=client, headers={'Origin': ORIGIN})
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), ORIGIN)
            self.assertNotIn('fixture-secret-only', response.get_data(as_text=True))
            print('Actual management route/method checks:', count)


if __name__ == '__main__':
    unittest.main(verbosity=2)
