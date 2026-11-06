"""Opt-in single-password console sessions. No database or frontend dependency."""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from flask import current_app, g, jsonify, request

from admin_access import AdminAuthConfigError, _parse_password_hash, make_password_hash

PASSWORD_FILE_ENV = 'AG_ADMIN_PASSWORD_FILE'
COOKIE_NAME = '__Host-v5_admin'
CSRF_HEADER = 'X-Admin-CSRF'
AUTH_PREFIX = '/api/gm/auth/'
MAX_SESSIONS = 64
MAX_IP_BUCKETS = 1024
MAX_VERIFY_IN_FLIGHT = 2
IP_ATTEMPTS = 5
GLOBAL_ATTEMPTS = 30
ATTEMPT_WINDOW = 60
_STATES = {}
_STATES_LOCK = threading.Lock()


def _bad(reason):
    return AdminAuthConfigError('invalid single-password admin configuration: ' + reason)


@dataclass(frozen=True)
class PasswordConfig:
    path: Path
    origin: str
    session_seconds: int
    trusted_proxies: tuple
    iterations: int
    salt: bytes = field(repr=False)
    digest: bytes = field(repr=False)

    def verify(self, password):
        result = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), self.salt,
                                     self.iterations, dklen=len(self.digest))
        return hmac.compare_digest(result, self.digest)


def _valid_password(password, *, new=False):
    if not isinstance(password, str):
        return False
    try:
        return (12 if new else 1) <= len(password) and len(password.encode('utf-8')) <= 1024
    except UnicodeError:
        return False


def hash_password(password):
    """Provisioning helper; callers must obtain passwords via getpass, never argv."""
    if not _valid_password(password, new=True):
        raise ValueError('password must have at least 12 characters and at most 1024 UTF-8 bytes')
    return make_password_hash(password)


def load_password_config(path_value):
    if not isinstance(path_value, str) or not path_value.strip():
        raise _bad('AG_ADMIN_PASSWORD_FILE must name a file')
    try:
        path = Path(path_value).resolve(strict=True)
        web_root = Path(__file__).resolve().parent / 'static_web'
        if path.is_relative_to(web_root.resolve()):
            raise _bad('authentication file must be outside the static web directory')
        st = path.stat()
        if st.st_size > 8192 or (os.name != 'nt' and st.st_mode & 0o077):
            raise _bad('file must be small and accessible only by its owner')
        with path.open('rb') as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            raise _bad('authentication file is too large')
        data = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError, UnicodeError):
        raise _bad('authentication file is missing, unreadable or invalid JSON') from None
    keys = {'schema', 'password_hash', 'origin', 'session_seconds', 'trusted_proxies'}
    if (not isinstance(data, dict) or type(data.get('schema')) is not int
            or data['schema'] != 1 or set(data) - keys):
        raise _bad('expected schema 1 with single-password fields only')
    origin = data.get('origin')
    try:
        parts = urlsplit(origin) if isinstance(origin, str) else None
        if (not parts or parts.scheme != 'https' or not parts.hostname or parts.username
                or parts.password or parts.path or parts.query or parts.fragment
                or origin != origin.strip() or any(c.isspace() for c in origin)):
            raise ValueError()
        _ = parts.port
    except ValueError:
        raise _bad('origin must be an explicit HTTPS origin without path or credentials') from None
    seconds = data.get('session_seconds', 3600)
    if type(seconds) is not int or not 300 <= seconds <= 86400:
        raise _bad('session_seconds must be between 300 and 86400')
    proxies = data.get('trusted_proxies', [])
    if not isinstance(proxies, list) or len(proxies) > 16:
        raise _bad('trusted_proxies must be a small explicit CIDR list')
    try:
        networks = tuple(ipaddress.ip_network(value, strict=True) for value in proxies
                         if isinstance(value, str))
        if len(networks) != len(proxies) or any(n.prefixlen == 0 for n in networks):
            raise ValueError()
    except ValueError:
        raise _bad('invalid or unrestricted trusted proxy range') from None
    iterations, salt, digest = _parse_password_hash(data.get('password_hash'))
    return PasswordConfig(path, origin, seconds, networks, iterations, salt, digest)


def _signature(path):
    st = path.stat()
    return st.st_mtime_ns, st.st_size, st.st_ino, st.st_mode


def _write_private(path, content):
    # Exclusive creation; backup and staging files are retained on any failure.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())


class AuthState:
    def __init__(self, config):
        self.config = config
        self.signature = _signature(config.path)
        self.lock = threading.RLock()
        self.sessions = OrderedDict()
        self.attempts = OrderedDict()
        self.global_attempts = deque()
        self.in_flight = 0
        self.revision = 0

    def refresh(self):
        with self.lock:
            try:
                signature = _signature(self.config.path)
                if signature != self.signature:
                    config = load_password_config(str(self.config.path))
                    self.config = config
                    self.signature = signature
                    self.revision += 1
                    self.sessions.clear()
            except (OSError, AdminAuthConfigError):
                self.sessions.clear()
                # Do not allow deletion or a broken replacement to disable a live guard.
                raise _bad('enabled configuration is no longer valid') from None

    def _prune(self, now):
        for key, value in list(self.sessions.items()):
            if value['deadline'] <= now:
                self.sessions.pop(key, None)
        while self.global_attempts and self.global_attempts[0] <= now - ATTEMPT_WINDOW:
            self.global_attempts.popleft()
        for key, values in list(self.attempts.items()):
            while values and values[0] <= now - ATTEMPT_WINDOW:
                values.popleft()
            if not values:
                self.attempts.pop(key, None)

    def reserve_attempt(self, ip):
        # Reserve both rate and concurrency budgets atomically BEFORE PBKDF2.
        with self.lock:
            now = time.monotonic(); self._prune(now)
            values = self.attempts.get(ip, ())
            retry = 0
            if len(values) >= IP_ATTEMPTS:
                retry = max(retry, math.ceil(values[0] + ATTEMPT_WINDOW - now))
            if len(self.global_attempts) >= GLOBAL_ATTEMPTS:
                retry = max(retry, math.ceil(self.global_attempts[0] + ATTEMPT_WINDOW - now))
            if self.in_flight >= MAX_VERIFY_IN_FLIGHT:
                retry = max(retry, 1)
            if retry:
                return None, max(1, retry)
            # Global budget already bounds new active addresses to 30/window.
            if ip not in self.attempts and len(self.attempts) >= MAX_IP_BUCKETS:
                return None, ATTEMPT_WINDOW
            self.attempts.setdefault(ip, deque()).append(now)
            self.global_attempts.append(now)
            self.in_flight += 1
            return (self.config, self.revision), 0

    def finish_attempt(self):
        with self.lock:
            self.in_flight -= 1

    def session(self, token):
        if not isinstance(token, str) or len(token) != 43:
            return None
        key = hashlib.sha256(token.encode('utf-8')).digest()
        with self.lock:
            self._prune(time.monotonic())
            return self.sessions.get(key)

    def issue(self):
        with self.lock:
            self._prune(time.monotonic())
            if len(self.sessions) >= MAX_SESSIONS:
                self.sessions.popitem(last=False)
            token = secrets.token_urlsafe(32)
            session = dict(csrf=secrets.token_urlsafe(32),
                           deadline=time.monotonic() + self.config.session_seconds,
                           expires_at=int(time.time()) + self.config.session_seconds)
            self.sessions[hashlib.sha256(token.encode('utf-8')).digest()] = session
            return token, session

    def revoke(self, token):
        with self.lock:
            if isinstance(token, str):
                self.sessions.pop(hashlib.sha256(token.encode('utf-8')).digest(), None)

    def rotate(self, password_hash, revision):
        with self.lock:
            self.refresh()
            if revision != self.revision:
                return False
            path = self.config.path
            raw = path.read_bytes()
            data = json.loads(raw.decode('utf-8'))
            data['password_hash'] = password_hash
            suffix = secrets.token_hex(8)
            _write_private(path.with_name(path.name + '.bak.' + suffix), raw)
            staging = path.with_name(path.name + '.new.' + suffix)
            _write_private(staging, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            os.replace(staging, path)
            self.refresh()  # New hash, all sessions revoked, shared by every installed app.
            return True


def _shared_state(config):
    with _STATES_LOCK:
        key = str(config.path)
        if key not in _STATES:
            if len(_STATES) >= 16:
                raise _bad('too many authentication configurations in one process')
            _STATES[key] = AuthState(config)
        state = _STATES[key]
        state.refresh()
        return state


def _address(raw):
    try:
        ip = ipaddress.ip_address(raw)
        return ip.ipv4_mapped if getattr(ip, 'ipv4_mapped', None) else ip
    except (ValueError, TypeError):
        return None


def _trusted(ip, config):
    return ip is not None and any(ip in network for network in config.trusted_proxies)


def request_identity(config):
    peer = _address(request.remote_addr)
    secure = request.scheme == 'https'
    client = peer
    if _trusted(peer, config):
        proto = request.headers.get('X-Forwarded-Proto')
        if proto is not None:
            secure = proto == 'https'
        raw = request.headers.get('X-Forwarded-For', '')
        if raw:
            parts = raw.split(',')
            hops = [_address(part.strip()) for part in parts] if len(raw) <= 1024 and len(parts) <= 16 else []
            if not hops or any(ip is None for ip in hops):
                return None, False
            # Ignore a forged left prefix: walk from the real proxy toward the client.
            for hop in reversed(hops):
                if not _trusted(client, config):
                    break
                client = hop
    return str(client) if client is not None else 'unknown', secure


def _json(status, error=None, **data):
    response = jsonify(dict(ok=error is None, **({'error': error} if error else {}), **data))
    response.status_code = status
    return response


def _payload():
    if request.mimetype != 'application/json' or (request.content_length or 0) > 4096:
        return None
    try:
        raw = request.stream.read(4097)
        if len(raw) > 4096:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, UnicodeError):
        return None


def _clear_cookie(response):
    response.delete_cookie(COOKIE_NAME, path='/', secure=True, httponly=True, samesite='Strict')
    return response


def install(app, config):
    state = _shared_state(config)
    app.extensions['admin_access'] = dict(enabled=True, mode='password_session')

    def protected():
        from admin_access import is_admin_path
        # Endpoint checks cover aliases/mounts even when they are outside /api/gm.
        view = app.view_functions.get(request.endpoint)
        return (is_admin_path(request.path) or request.blueprint == 'gm_api'
                or getattr(view, '__module__', None) == 'gm_api'
                or request.endpoint == 'shell_res_version')

    def is_api():
        view = app.view_functions.get(request.endpoint)
        return (request.path.startswith('/api/') or request.blueprint == 'gm_api'
                or getattr(view, '__module__', None) == 'gm_api'
                or request.endpoint == 'shell_res_version')

    @app.before_request
    def password_guard():
        if not protected():
            return None
        g.admin_password_protected = True
        try:
            state.refresh()
        except AdminAuthConfigError:
            return _json(503, 'auth_configuration_invalid')
        config = state.config
        ip, secure = request_identity(config)
        # Host must be preserved by the proxy; arbitrary X-Forwarded-Host is ignored.
        if not secure or request.host.lower() != urlsplit(config.origin).netloc.lower():
            return _json(403, 'admin_https_origin_required')
        origin = request.headers.get('Origin')
        if origin is not None and origin != config.origin:
            return _json(403, 'origin_rejected')
        if request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            return _json(403, 'origin_rejected')
        if request.path.startswith(AUTH_PREFIX):
            if request.path not in {AUTH_PREFIX + name for name in ('login', 'session', 'logout', 'password')}:
                return _json(404, 'auth_route_not_found')
            if request.query_string:
                return _json(400, 'auth_query_not_allowed')
            expected = 'GET' if request.path == AUTH_PREFIX + 'session' else 'POST'
            if request.method not in (expected, 'OPTIONS'):
                return _json(405, 'method_not_allowed')
        if request.method == 'OPTIONS':
            # Credential-free preflight contains no account data and cannot mutate.
            return _json(204) if origin == config.origin else _json(403, 'origin_required')
        if request.path == AUTH_PREFIX + 'login':
            if request.method != 'POST':
                return _json(405, 'method_not_allowed')
            if origin != config.origin:
                return _json(403, 'origin_required')
            return None
        session = state.session(request.cookies.get(COOKIE_NAME))
        g.admin_password_session = session
        g.admin_password_ip = ip
        if request.path == AUTH_PREFIX + 'session':
            return None if request.method == 'GET' else _json(405, 'method_not_allowed')
        if session is None:
            return _json(401, 'authentication_required')
        # Some legacy GM GET routes mutate. Protect ALL API methods with CSRF.
        if is_api():
            csrf = request.headers.get(CSRF_HEADER, '')
            if len(csrf) != 43 or not hmac.compare_digest(csrf.encode('utf-8'), session['csrf'].encode('utf-8')):
                return _json(403, 'csrf_required')
        if request.method not in ('GET', 'HEAD') and origin != config.origin:
            return _json(403, 'origin_required')
        return None

    @app.after_request
    def password_response(response):
        if not getattr(g, 'admin_password_protected', False):
            return response
        # App hooks run after blueprint hooks, replacing legacy wildcard CORS even
        # for early rejection, exceptions and OPTIONS responses.
        for key in list(response.headers.keys()):
            if key.lower().startswith('access-control-'):
                response.headers.pop(key, None)
        if request.headers.get('Origin') == state.config.origin:
            response.headers['Access-Control-Allow-Origin'] = state.config.origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, HEAD'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Admin-CSRF, X-Save-Name'
            response.vary.add('Origin')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    def login():
        data = _payload()
        if not data or set(data) != {'password'} or not _valid_password(data.get('password')):
            return _json(400, 'invalid_request')
        config = state.config
        ip, _ = request_identity(config)
        reservation, retry = state.reserve_attempt(ip)
        if reservation is None:
            response = _json(429, 'try_later'); response.headers['Retry-After'] = str(retry)
            return response
        try:
            credential, revision = reservation
            valid = credential.verify(data['password'])
            with state.lock:
                state.refresh()
                if not valid or revision != state.revision:
                    return _json(401, 'invalid_password')
                state.revoke(request.cookies.get(COOKIE_NAME))
                token, session = state.issue()
                response = _json(200, csrf_token=session['csrf'], expires_at=session['expires_at'])
                response.set_cookie(COOKIE_NAME, token, path='/', secure=True, httponly=True,
                                    samesite='Strict', max_age=state.config.session_seconds)
                return response
        except (OSError, AdminAuthConfigError):
            return _json(503, 'auth_configuration_invalid')
        finally:
            state.finish_attempt()

    def session_info():
        session = g.admin_password_session
        if session is None:
            return _json(200, enabled=True, authenticated=False)
        return _json(200, enabled=True, authenticated=True, csrf_token=session['csrf'], expires_at=session['expires_at'])

    def logout():
        state.revoke(request.cookies.get(COOKIE_NAME))
        return _clear_cookie(_json(200))

    def change_password():
        data = _payload()
        if (not data or set(data) != {'current_password', 'new_password'}
                or not _valid_password(data.get('current_password'))
                or not _valid_password(data.get('new_password'), new=True)):
            return _json(400, 'invalid_request')
        reservation, retry = state.reserve_attempt(g.admin_password_ip)
        if reservation is None:
            response = _json(429, 'try_later'); response.headers['Retry-After'] = str(retry)
            return response
        try:
            credential, revision = reservation
            if not credential.verify(data['current_password']):
                return _json(401, 'invalid_password')
            new_hash = hash_password(data['new_password'])
            if not state.rotate(new_hash, revision):
                return _json(409, 'credentials_changed')
            return _clear_cookie(_json(200, reauthenticate=True))
        except (OSError, ValueError, AdminAuthConfigError):
            return _json(503, 'password_update_failed')
        finally:
            state.finish_attempt()

    for name, view, methods in (('login', login, ['POST']), ('session', session_info, ['GET']),
                                ('logout', logout, ['POST']), ('password', change_password, ['POST'])):
        app.add_url_rule(AUTH_PREFIX + name, endpoint='admin_password_' + name, view_func=view, methods=methods)
    return app
