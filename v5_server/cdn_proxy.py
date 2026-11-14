# -*- coding: utf-8 -*-
"""Verified CDN storage and bounded streaming downloads.

Missing objects are staged and verified before serving; warm objects reuse
stat-bound validation and are streamed in fixed-size blocks. Platforms never
fall back to a shared unqualified manifest.
"""
import http.client
import errno
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.request
from flask import Response, request, has_request_context
from werkzeug.exceptions import RequestedRangeNotSatisfiable
import cdn_integrity as integrity
from resource_storage import storage

from logger import log as _logger_log
import log_sifter

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATIC_DIR = os.path.join(_SERVER_DIR, "static_resources")

def _get_configured_cdn_root() -> str:
    """Compatibility accessor for the single shared storage authority."""
    return storage.root


def _resolve_platform_dir(platform_name: str, legacy_drive_dir: str = "") -> str:
    """Explicit config/environment wins; legacy directories are read-only roots."""
    return storage.directories()[platform_name]


PLATFORM_DIRS = storage.directories()


def _refresh_storage():
    global PLATFORM_DIRS
    storage.refresh()
    PLATFORM_DIRS = storage.directories()


from cdn_transfer_control import gate as _transfer_gate
_transfer_gate.on_acquired = _refresh_storage


def set_cdn_base_dir(base_dir: str):
    """Compatibility writer; obey the same maintenance boundary as Web."""
    global PLATFORM_DIRS
    from cdn_transfer_control import gate
    with gate.maintenance():
        storage.apply(base_dir)
        PLATFORM_DIRS = storage.directories()

LOCAL_CHUNK_SIZE = 1048576      # 1 MB 本地高速分块（消除 iOS 40ms Nagle 延迟，瞬间填满 TCP 窗口）
UPSTREAM_CHUNK_SIZE = 524288   # 512 KB 上游 CDN 拉取分块
CHUNK_SIZE = LOCAL_CHUNK_SIZE  # 保持向下兼容

# 默认仅分发已有资源；显式启用后才允许被动回源。
_CAPTURE_ENABLED = False
_AUTO_CACHE_FALLBACK = False
_CAPTURE_MODE = 'off'
CAPTURE_MODES = ('off', 'relay', 'cache')
_DNS_CACHE = {}
_DNS_LOCK = threading.Lock()
_SOURCE_IP = None
_AUTO_SOURCE_IP = None
_SOURCE_LOCK = threading.Lock()
_ROUTE_ERRORS = {errno.EADDRNOTAVAIL, errno.ENETUNREACH, errno.EHOSTUNREACH, 10049, 10051, 10065}

# 官方常用域名备用静态 IP（防止极端情况下 DoH 不可用）
FALLBACK_IPS = {
    "download-eo.ys4fun.com": ["111.31.122.137", "120.220.18.85", "183.204.11.49", "36.151.124.99"],
    "download.ys4fun.com": ["111.31.122.137", "120.220.18.85", "183.204.11.49", "36.151.124.99"],
    "open.ys4fun.com": ["203.107.63.115", "47.100.78.77"],
    "prod-api-activity.ys4fun.com": ["203.107.63.115"],
    "packaging.ys4fun.com": ["111.31.122.137", "120.220.18.85"],
}


def get_platform_from_path(path: str, headers=None) -> str:
    """根据请求路径及请求头精准判断客户端平台（pc / ios / android）。"""
    lower_path = path.lower()
    if "/ios/" in lower_path:
        return "ios"
    if "/android/" in lower_path:
        return "android"
    if "/pc/" in lower_path:
        return "pc"

    if headers:
        ua = headers.get("User-Agent", "").lower()
        if "iphone" in ua or "ipad" in ua or "ios" in ua or "darwin" in ua:
            return "ios"
        if "android" in ua:
            return "android"
        if "windows" in ua or "pc" in ua:
            return "pc"

    # 默认 PC 平台
    return "pc"


def get_data_dir(platform: str = "pc") -> str:
    """Use the selected platform directory; report failures without redirecting writes."""
    plat = platform.lower() if platform else "pc"
    primary_dir = PLATFORM_DIRS.get(plat, PLATFORM_DIRS["pc"])
    if not primary_dir:
        raise ValueError('尚未选择资源存储位置，请在控制面板选择服务器目录')
    if not os.path.isdir(primary_dir):
        os.makedirs(primary_dir, exist_ok=True)
    return primary_dir


def sync_initial_manifests():
    """Only seed from the explicitly qualified platform directory."""
    for plat in ('pc', 'ios', 'android'):
        source = os.path.join(DEFAULT_STATIC_DIR, plat)
        if not os.path.isdir(source):
            continue
        target = get_data_dir(plat)
        for name in os.listdir(source):
            if not name.endswith(('.bytes', '.json')):
                continue
            try:
                spec = integrity.ObjectSpec(name)
                integrity.copy_verified(integrity.safe_path(source, name), target, spec)
            except (OSError, ValueError) as error:
                _logger_log(f'[{plat}] 清单未就绪: {name} ({type(error).__name__})', 'WARN', module='CDN')


def is_capture_enabled():
    """查询当前抓包器是否开启。"""
    return _CAPTURE_ENABLED


def get_capture_mode():
    return _CAPTURE_MODE


def set_capture_mode(mode):
    if mode not in CAPTURE_MODES:
        raise ValueError('CDN模式必须为off、relay或cache')
    global _CAPTURE_MODE, _CAPTURE_ENABLED, _AUTO_CACHE_FALLBACK
    _CAPTURE_MODE = mode
    _CAPTURE_ENABLED = mode != 'off'
    _AUTO_CACHE_FALLBACK = mode == 'cache'
    print(f'[CDN_PROXY] 资源模式: {mode}', flush=True)


def set_capture_enabled(val: bool):
    """设置抓包器开启/关闭状态。"""
    set_capture_mode((_CAPTURE_MODE if _CAPTURE_MODE != 'off' else 'cache') if val else 'off')


def _is_public_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
        return address.version == 4 and address.is_global
    except ValueError:
        return False


def _validated_source_ip(value):
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_loopback or address in ipaddress.IPv4Network("198.18.0.0/15"):
        raise ValueError("CDN 出站地址必须是本机真实 LAN 地址")
    return str(address)


def select_source_ip(advertised_ip):
    """Prefer the advertised address when it has an outbound IPv4 route."""
    def routed(value):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((value, 0))
            # UDP connect selects a route without sending a packet.
            probe.connect(('223.5.5.5', 53))
        return value

    def candidates():
        yield advertised_ip
        import gen_cert
        yield from (ip for ip in gen_cert.get_all_lan_ips() if ip != advertised_ip)

    for value in candidates():
        try:
            value = _validated_source_ip(value)
        except ValueError:
            continue
        try:
            return routed(value)
        except OSError as exc:
            if (getattr(exc, 'winerror', None) or exc.errno) not in _ROUTE_ERRORS:
                raise
    raise OSError(errno.ENETUNREACH, "找不到具有出站路由的本机 CDN IPv4")


def set_source_ip(value):
    """使用 main 已确认的 LAN 地址，不更改系统路由或代理。"""
    value = _validated_source_ip(value)
    global _SOURCE_IP, _AUTO_SOURCE_IP
    with _SOURCE_LOCK:
        _SOURCE_IP = value
        _AUTO_SOURCE_IP = None
    with _DNS_LOCK:
        _DNS_CACHE.clear()


def _source_ip():
    global _AUTO_SOURCE_IP
    with _SOURCE_LOCK:
        if _AUTO_SOURCE_IP is None:
            _AUTO_SOURCE_IP = select_source_ip(_SOURCE_IP)
        return _AUTO_SOURCE_IP


def _resolve_lan_dns_ipv4(host):
    # 使用既有 DNS 报文校验/出站绑定，排除 TUN fake-IP 与 hosts 回环。
    import dns_server
    source = _source_ip()
    resolver = dns_server.DnsServer(source, upstream_bind_ip=source)
    question = (host, 1, 1)
    packet = struct.pack("!HHHHHH", time.time_ns() & 0xFFFF, 0x0100, 1, 0, 0, 0)
    packet += dns_server._encoded_question(question)
    for upstream in resolver.upstreams:
        try:
            response = resolver._exchange(packet, upstream, False)
            if response[3] & 15:
                continue
            _, offset = dns_server.parse_domain(response)
            offset += 4
            ips = []
            for _ in range(struct.unpack_from("!H", response, 6)[0]):
                _, offset = dns_server.parse_domain(response, offset)
                kind, cls, _, length = struct.unpack_from("!HHIH", response, offset)
                offset += 10
                if kind == 1 and cls == 1 and length == 4:
                    value = socket.inet_ntoa(response[offset:offset + 4])
                    if _is_public_ipv4(value) and value not in ips:
                        ips.append(value)
                offset += length
            if ips:
                return ips
        except (OSError, ValueError, struct.error):
            continue
    return []


def resolve_doh(host):
    """先物理 LAN DNS，再 DoH；只接受真实公网 IPv4。"""
    with _DNS_LOCK:
        if host in _DNS_CACHE and _DNS_CACHE[host]:
            cached = [value for value in _DNS_CACHE[host] if _is_public_ipv4(value)]
            if cached:
                return cached

    ips = _resolve_lan_dns_ipv4(host)
    if ips:
        with _DNS_LOCK:
            _DNS_CACHE[host] = ips
        return ips

    doh_urls = [
        f"https://dns.alidns.com/resolve?name={host}&type=A",
        f"https://223.5.5.5/resolve?name={host}&type=A",
        f"https://1.12.12.12/resolve?name={host}&type=A",
    ]

    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))

    ips = []
    for u in doh_urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "curl/8.0"})
            with opener.open(req, timeout=3) as resp:
                if resp.status == 200:
                    j = json.loads(resp.read().decode("utf-8"))
                    for a in j.get("Answer", []):
                        if a.get("type") == 1 and a.get("data"):
                            ip = a["data"].strip()
                            if _is_public_ipv4(ip):
                                ips.append(ip)
            if ips:
                break
        except Exception:
            continue

    if not ips and host in FALLBACK_IPS:
        ips = [value for value in FALLBACK_IPS[host] if _is_public_ipv4(value)]

    if ips:
        with _DNS_LOCK:
            _DNS_CACHE[host] = ips
        return ips
    raise OSError(f"无法解析可信官方公网地址: {host}")


def create_direct_socket(target_ip, port=443, timeout=30):
    """创建直接物理局域网网卡直连 Socket，绕过 Clash TUN 虚拟网卡环回拦截。"""
    global _AUTO_SOURCE_IP
    if not _is_public_ipv4(target_ip):
        raise OSError(f"拒绝将官方请求发送到非公网地址: {target_ip}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    from cdn_transfer_control import current_token
    token=current_token()
    detach=token.attach(sock,sock) if token else None
    source = None
    try:
        integrity.checkpoint(token)
        source = _source_ip()
        sock.bind((source, 0))
        sock.settimeout(timeout)
        sock.connect((target_ip, port))
        integrity.checkpoint(token)
        return sock
    except BaseException as error:
        sock.close()
        if isinstance(error, OSError) and (getattr(error, 'winerror', None) or error.errno) in _ROUTE_ERRORS:
            with _SOURCE_LOCK:
                if _AUTO_SOURCE_IP == source:
                    _AUTO_SOURCE_IP = None
            with _DNS_LOCK:
                _DNS_CACHE.clear()
        raise
    finally:
        if detach: detach()


def cache_roots(platform):
    plat = integrity.platform(platform)
    roots = [os.path.join(DEFAULT_STATIC_DIR, plat), PLATFORM_DIRS.get(plat),
             os.path.join(_SERVER_DIR, 'cdn_cache', plat)]
    roots.extend(storage.history(plat))
    legacy = {'pc': r'D:\PC_DATA', 'ios': r'D:\iOS_DATA', 'android': r'D:\Android_DATA'}
    if os.name == 'nt':
        roots.append(legacy[plat])
    return list(dict.fromkeys(root for root in roots if root))


def find_cached_file(filename: str, platform: str = "pc"):
    """Validate legacy objects once; reuse only while their file identity holds."""
    spec = integrity.ObjectSpec(filename)
    for root in cache_roots(platform):
        candidate = integrity.safe_path(root, filename)
        if integrity.valid_file(candidate, spec):
            return candidate
    return None


def serve_local_stream(file_path, range_header=None):
    """Stream an already validated open snapshot, with Werkzeug range handling."""
    handle = open(file_path, 'rb')
    try:
        spec = integrity.ObjectSpec(os.path.basename(file_path))
        digest = integrity.validate_open_file(handle, spec)
        stat = os.fstat(handle.fileno())
        def chunks():
            try:
                while True:
                    data = handle.read(LOCAL_CHUNK_SIZE)
                    if not data:
                        break
                    yield data
            finally:
                handle.close()
        mime = 'application/json' if spec.filename.endswith(('.bytes', '.json')) else 'application/octet-stream'
        response = Response(chunks(), mimetype=mime)
        response.content_length = stat.st_size
        response.last_modified = stat.st_mtime
        response.set_etag(digest)
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.call_on_close(handle.close)
        if has_request_context():
            environ = dict(request.environ)
        else:
            environ = {'REQUEST_METHOD': 'GET'}
        if range_header:
            environ['HTTP_RANGE'] = range_header
        if environ.get('REQUEST_METHOD') == 'HEAD':
            environ.pop('HTTP_RANGE', None)
        try:
            response.make_conditional(environ, accept_ranges=True, complete_length=stat.st_size)
        except RequestedRangeNotSatisfiable:
            response.close()
            return Response(status=416, headers={'Content-Range': f'bytes */{stat.st_size}',
                                                 'Content-Length': '0', 'Accept-Ranges': 'bytes'})
        return response
    except Exception:
        handle.close()
        raise


# Exact authorities, not substring matches. Local Host headers fall back to the
# resource authority in the routing layer; an explicit remote authority must pass.
TRUSTED_UPSTREAM_HOSTS = frozenset(FALLBACK_IPS)
_TLS_CONTEXT = None
_TLS_CONTEXT_LOCK = threading.Lock()


def _get_tls_context():
    global _TLS_CONTEXT
    with _TLS_CONTEXT_LOCK:
        if _TLS_CONTEXT is None:
            _TLS_CONTEXT = ssl.create_default_context()
        return _TLS_CONTEXT


def _upstream_opener(host, path, query='', headers=None, method='GET', body=None, *, range_header=None):
    if host not in TRUSTED_UPSTREAM_HOSTS:
        raise integrity.IntegrityError('Untrusted CDN upstream authority')
    if not path.startswith('/') or path.startswith('//') or any(c in path + query for c in '\r\n\x00'):
        raise integrity.IntegrityError('Invalid upstream request target')
    def open_response(attempt, timeout):
        from cdn_transfer_control import current_token
        token = current_token()
        integrity.checkpoint(token)
        ips = resolve_doh(host)
        integrity.checkpoint(token)
        if not ips:
            raise OSError('No upstream address')
        # Loading OS trust roots for every tiny asset is costly. Share the
        # immutable verified context; do not initialize TLS on warm cache hits.
        context = _get_tls_context()
        conn = http.client.HTTPSConnection(ips[attempt % len(ips)], 443, context=context, timeout=timeout)
        raw_socket = None
        try:
            raw_socket = create_direct_socket(ips[attempt % len(ips)], 443, timeout=timeout)
            conn.sock = context.wrap_socket(raw_socket, server_hostname=host, do_handshake_on_connect=False)
            raw_socket = None
            if token:
                conn._cdn_abort_detach = token.attach(conn, conn.sock)
            integrity.checkpoint(token)
            conn.sock.do_handshake()
            integrity.checkpoint(token)
            request_headers = {'Host': host, 'User-Agent': 'UnityPlayer/2022.3.62f3',
                               'Accept': '*/*', 'Accept-Encoding': 'identity'}
            if range_header and method == 'GET':
                request_headers['Range'] = range_header
            # SDK metadata may use signed request fields. Never forward client
            # Range, validators, cookies or framing headers to an object GET.
            if headers and method != 'GET':
                request_headers.update({k: v for k, v in headers.items()
                                        if k.lower() in ('content-type', 'user-agent')})
            conn.request(method, path + ('?' + query if query else ''), body=body, headers=request_headers)
            return conn, conn.getresponse()
        except Exception:
            conn.close()
            if getattr(conn, '_cdn_abort_detach', None):
                conn._cdn_abort_detach()
            if raw_socket is not None:
                raw_socket.close()
            raise
    return open_response


def download_verified(platform, filename, directory=None, *, expected_size=None,
                      expected_md5=None, host='download-eo.ys4fun.com', cancel=None,
                      deadline=None, max_attempts=3, force=False, indexed=False,
                      progress=None, passive=False, on_published=None):
    """The shared entry point used by runtime, Web, preparation and CLI tools."""
    integrity.platform(platform)
    spec = integrity.ObjectSpec(filename, expected_size, expected_md5)
    from cdn_active_downloads import settings
    from cdn_transfer_control import gate, current_token
    from contextlib import nullcontext
    deadline = deadline if deadline is not None else time.monotonic() + 7200
    while True:
        session = gate.passive() if passive else nullcontext(current_token()) if current_token() is not None else gate.active(cancel, deadline)
        token = None
        try:
            with session as token:
                target_directory = directory or get_data_dir(platform)
                if indexed and (token is None or token._gate is not gate):
                    raise integrity.IntegrityError('Indexed downloads require the active index session')
                opener = _upstream_opener(host, f'/{platform}/resources/{filename}')
                return integrity.ensure_file(spec, target_directory, opener, cancel=token or cancel, deadline=deadline,
                    max_attempts=max_attempts, force=force, indexed=indexed,
                    discard_partial=bool(token and token.discard_partials), progress=progress or (token.progress if token else None),
                    bandwidth=None if passive else settings.bandwidth, on_published=on_published)
        except integrity.Cancelled:
            # A standalone active request yields to foreground traffic, then
            # retries within its original deadline. Managed groups handle yield.
            if passive or current_token() is not None or not token or token.reason != 'passive':
                raise
            integrity.checkpoint(cancel, deadline)


def stream_upstream_and_cache(path, host='download-eo.ys4fun.com', query='', headers=None,
                              range_header=None, log=_logger_log):
    """Compatibility entry: verify a full object, then serve local client ranges."""
    filename = '<invalid>'
    try:
        filename = integrity.basename(path.split('?')[0].rsplit('/', 1)[-1])
        platform = get_platform_from_path(path, headers)
        target = download_verified(platform, filename, host=host, passive=True)
        log(f'[{platform.upper()}] CDN对象已验证: {filename} ({os.path.getsize(target)}B)', 'INFO', module='CDN')
        return serve_local_stream(target, range_header)
    except (OSError, ValueError, http.client.HTTPException) as error:
        log(f'CDN对象未就绪: {filename} ({type(error).__name__}: {error})', 'WARN', module='CDN')
        return Response('Upstream object unavailable', status=502)


def relay_upstream(platform, filename, *, host='download-eo.ys4fun.com', range_header=None, log=_logger_log):
    """Stream one upstream object without an asset file or full-body buffer.

    Retries end before response headers are returned. Body failures terminate
    the response; bytes already sent are never replayed or silently resumed.
    """
    from cdn_transfer_control import gate, bind
    from types import SimpleNamespace
    integrity.platform(platform)
    spec = integrity.ObjectSpec(filename)
    method = request.method if has_request_context() else 'GET'
    if method not in ('GET', 'HEAD'):
        return Response(status=405, headers={'Allow': 'GET, HEAD'})
    wanted = None
    if range_header and method == 'GET':
        if len(range_header) > 128:
            return Response('Invalid byte range', status=400)
        wanted = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
        if not wanted or not any(wanted.groups()) or (not wanted[1] and int(wanted[2]) == 0):
            return Response('Only a single byte range is supported', status=400)
        if wanted[1] and wanted[2] and int(wanted[2]) < int(wanted[1]):
            return Response('Invalid byte range', status=400)
    else:
        range_header = None
    session = gate.passive(bind_token=False)
    token = None
    conn = upstream = None
    closed = False
    guard = threading.Lock()
    def close_upstream():
        nonlocal conn, upstream
        try:
            if upstream is not None:
                upstream.close()
        finally:
            if conn is not None:
                try:
                    conn.close()
                finally:
                    detach = getattr(conn, '_cdn_abort_detach', None)
                    if detach:
                        detach()
            conn = upstream = None
    def cleanup():
        nonlocal closed
        with guard:
            if closed:
                return
            closed = True
        try:
            close_upstream()
        finally:
            if token is not None:
                session.__exit__(None, None, None)
    try:
        token = session.__enter__()
        deadline = time.monotonic() + 7200
        with bind(token):
            opener = _upstream_opener(host, f'/{platform}/resources/{filename}', method=method, range_header=range_header)
            for attempt in range(3):
                integrity.checkpoint(token, deadline)
                try:
                    conn, upstream = opener(attempt, 30)
                    if upstream.status in (408, 425, 429, 500, 502, 503, 504):
                        raise OSError(f'Transient upstream HTTP {upstream.status}')
                    break
                except (OSError, http.client.HTTPException):
                    close_upstream()
                    if attempt == 2:
                        raise
        status = upstream.status
        if status == 416:
            content_range = upstream.getheader('Content-Range') or ''
            if not re.fullmatch(r'bytes \*/\d+', content_range):
                raise integrity.IntegrityError('Invalid unsatisfied range')
            cleanup()
            return Response(status=416, headers={'Content-Range': content_range, 'Accept-Ranges': 'bytes',
                                                 'Access-Control-Allow-Origin': '*'})
        if status in (403, 404):
            cleanup()
            return Response(status=status, headers={'Access-Control-Allow-Origin': '*'})
        if status not in (200, 206) or (status == 206 and wanted is None):
            raise integrity.IntegrityError(f'Unexpected upstream HTTP {status}')
        # Reuse framing/encoding/size guards, handling partial responses separately.
        full_view = SimpleNamespace(status=200, getheader=lambda name: None if name.lower() == 'content-range' else upstream.getheader(name))
        expected, maximum = integrity._headers(full_view, spec)
        if status == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', upstream.getheader('Content-Range') or '')
            if not match:
                raise integrity.IntegrityError('Invalid upstream Content-Range')
            first, last, total_size = map(int, match.groups())
            if not 0 <= first <= last < total_size <= maximum:
                raise integrity.IntegrityError('Out-of-bounds upstream range')
            start = int(wanted[1]) if wanted[1] else max(0, total_size - int(wanted[2]))
            end = min(int(wanted[2]), total_size - 1) if wanted[1] and wanted[2] else total_size - 1
            if (first, last) != (start, end) or (expected is not None and expected != last - first + 1):
                raise integrity.IntegrityError('Upstream range differs from request')
            expected = last - first + 1
        elif upstream.getheader('Content-Range'):
            raise integrity.IntegrityError('Unexpected range on full object')
        headers = {name: upstream.getheader(name) for name in
                   ('Content-Type', 'ETag', 'Last-Modified', 'Cache-Control', 'Content-Range', 'Accept-Ranges')
                   if upstream.getheader(name) is not None}
        headers['Access-Control-Allow-Origin'] = '*'
        if expected is not None:
            headers['Content-Length'] = str(expected)
        if method == 'HEAD':
            cleanup()
            response = Response(status=status, headers=headers)
            # Response's empty body otherwise rewrites the origin's object length.
            if expected is not None:
                response.content_length = expected
            return response
        def chunks():
            total = 0
            digest = hashlib.md5() if status == 200 and spec.md5 else None
            try:
                read = getattr(upstream, 'read1', upstream.read)
                while True:
                    integrity.checkpoint(token, deadline)
                    chunk = read(UPSTREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > maximum or (expected is not None and total > expected):
                        raise integrity.IntegrityError('Relay body exceeds declared size')
                    if digest:
                        digest.update(chunk)
                        if expected is not None and total == expected and digest.hexdigest() != spec.md5:
                            raise integrity.IntegrityError('Relay object digest mismatch')
                    yield chunk
                if expected is not None and total != expected:
                    raise integrity.IntegrityError('Incomplete relay body')
                if digest and digest.hexdigest() != spec.md5:
                    raise integrity.IntegrityError('Relay object digest mismatch')
            except (OSError, ValueError, http.client.HTTPException) as error:
                log(f'CDN流式传输中断: {filename} ({type(error).__name__})', 'WARN', module='CDN')
                raise
            finally:
                cleanup()
        response = Response(chunks(), status=status, headers=headers,
                            content_type=headers.get('Content-Type', 'application/octet-stream'))
        response.call_on_close(cleanup)
        return response
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
        cleanup()
        log(f'CDN穿透未就绪: {filename} ({type(error).__name__})', 'WARN', module='CDN')
        return Response('Upstream relay unavailable', status=502)


def fetch_upstream(host, path, query='', headers=None, method='GET', body=None, timeout=30, log=print):
    """Bounded small metadata requests. Asset bytes use download_verified instead."""
    try:
        opener = _upstream_opener(host, path, query, headers, method, body)
    except (ValueError, OSError):
        return 502, {}, b''
    deadline = time.monotonic() + max(1, min(float(timeout), 90))
    failures = []
    for attempt in range(3):
        conn = None
        try:
            integrity.checkpoint(deadline=deadline)
            conn, response = opener(attempt, max(0.1, min(15, deadline - time.monotonic())))
            response_headers = dict(response.getheaders())
            if (response.getheader('Content-Encoding') or 'identity').lower() != 'identity':
                raise integrity.IntegrityError('Unexpected metadata encoding')
            data = bytearray()
            read_chunk = getattr(response, 'read1', response.read)
            while True:
                integrity.checkpoint(deadline=deadline)
                chunk = read_chunk(min(65536, integrity.MAX_MANIFEST_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > integrity.MAX_MANIFEST_BYTES:
                    raise integrity.IntegrityError('Metadata exceeds size limit')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or len(data) != int(length)):
                raise integrity.IntegrityError('Incomplete metadata response')
            return response.status, response_headers, bytes(data)
        except (OSError, ValueError, http.client.HTTPException) as error:
            code = getattr(error, 'winerror', None) or getattr(error, 'errno', None)
            failures.append(f'{type(error).__name__}({code})' if code is not None else type(error).__name__)
        finally:
            if conn is not None:
                conn.close()
    log(f'[CDN_PROXY] 上游元数据请求失败（{host}；{", ".join(failures)}）')
    return 502, {}, b''


def handle_resource_request(path, host='download-eo.ys4fun.com', query='', headers=None, log=_logger_log):
    try:
        filename = integrity.basename(path.split('?')[0].rsplit('/', 1)[-1])
        platform = get_platform_from_path(path, headers)
        range_header = headers.get('Range') if headers else None
        if host not in TRUSTED_UPSTREAM_HOSTS:
            return Response('Invalid upstream authority', status=400)
        import resource_distribution
        handled, managed_path = resource_distribution.resolve_resource(path, platform)
        if handled:
            if managed_path is None:
                return Response('Manifest profile unavailable', status=404)
            return serve_local_stream(managed_path, range_header)
        cached = find_cached_file(filename, platform)
        if cached:
            return serve_local_stream(cached, range_header)
        if get_capture_mode() == 'relay':
            return relay_upstream(platform, filename, host=host, range_header=range_header, log=log)
        if _CAPTURE_ENABLED or _AUTO_CACHE_FALLBACK:
            return stream_upstream_and_cache(path, host=host, query=query, headers=headers,
                                             range_header=range_header, log=log)
        return Response('Not Found', status=404)
    except (ValueError, OSError):
        return Response('Invalid or unavailable resource', status=404)
