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

# 全局抓包器开关与统计（默认开启智能穿透兜底：本地有秒发，本地无穿透拉取防404）
_CAPTURE_ENABLED = True
_AUTO_CACHE_FALLBACK = True
_DNS_CACHE = {}
_DNS_LOCK = threading.Lock()
_SOURCE_IP = None

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


def set_capture_enabled(val: bool):
    """设置抓包器开启/关闭状态。"""
    global _CAPTURE_ENABLED, _AUTO_CACHE_FALLBACK
    _CAPTURE_ENABLED = bool(val)
    _AUTO_CACHE_FALLBACK = bool(val)
    print(f"[CDN_PROXY] 抓包器状态已更新为: {'[已开启]' if _CAPTURE_ENABLED else '[已关闭]'}", flush=True)


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
    """Keep a locally bindable host address; NAT public IPs use a real local NIC."""
    advertised_ip = _validated_source_ip(advertised_ip)

    def bindable(value):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((value, 0))
        return value

    try:
        return bindable(advertised_ip)
    except OSError as exc:
        if exc.errno not in (errno.EADDRNOTAVAIL, 10049):
            raise

    import gen_cert
    for value in dict.fromkeys(gen_cert.get_all_lan_ips()):
        try:
            value = _validated_source_ip(value)
        except ValueError:
            continue
        if value == advertised_ip:
            continue
        try:
            return bindable(value)
        except OSError as exc:
            if exc.errno not in (errno.EADDRNOTAVAIL, 10049):
                raise
    raise OSError(errno.EADDRNOTAVAIL, "找不到可绑定的本机 CDN 出站 IPv4")


def set_source_ip(value):
    """使用 main 已确认的 LAN 地址，不更改系统路由或代理。"""
    value = _validated_source_ip(value)
    global _SOURCE_IP
    _SOURCE_IP = value
    with _DNS_LOCK:
        _DNS_CACHE.clear()


def _source_ip():
    if _SOURCE_IP:
        return _SOURCE_IP
    import gen_cert
    return gen_cert.get_default_lan_ip()


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
    if not _is_public_ipv4(target_ip):
        raise OSError(f"拒绝将官方请求发送到非公网地址: {target_ip}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    from cdn_transfer_control import current_token
    token=current_token()
    detach=token.attach(sock,sock) if token else None
    try:
        integrity.checkpoint(token)
        sock.bind((_source_ip(), 0))
        sock.settimeout(timeout)
        sock.connect((target_ip, port))
        integrity.checkpoint(token)
        return sock
    except Exception:
        sock.close()
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


def _upstream_opener(host, path, query='', headers=None, method='GET', body=None):
    if host not in TRUSTED_UPSTREAM_HOSTS:
        raise integrity.IntegrityError('Untrusted CDN upstream authority')
    if not path.startswith('/') or path.startswith('//') or any(c in path + query for c in '\r\n\x00'):
        raise integrity.IntegrityError('Invalid upstream request target')
    ips = None
    def open_response(attempt, timeout):
        nonlocal ips
        from cdn_transfer_control import current_token
        token = current_token()
        integrity.checkpoint(token)
        if ips is None:
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
                      progress=None, passive=False):
    """The shared entry point used by runtime, Web, preparation and CLI tools."""
    integrity.platform(platform)
    spec = integrity.ObjectSpec(filename, expected_size, expected_md5)
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
                    discard_partial=bool(token and token.discard_partials), progress=progress or (token.progress if token else None))
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


def fetch_upstream(host, path, query='', headers=None, method='GET', body=None, timeout=30, log=print):
    """Bounded small metadata requests. Asset bytes use download_verified instead."""
    try:
        opener = _upstream_opener(host, path, query, headers, method, body)
    except (ValueError, OSError):
        return 502, {}, b''
    deadline = time.monotonic() + max(1, min(float(timeout), 90))
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
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            if conn is not None:
                conn.close()
    log('[CDN_PROXY] 上游元数据请求失败（已结束有限重试）')
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
        if _CAPTURE_ENABLED or _AUTO_CACHE_FALLBACK:
            return stream_upstream_and_cache(path, host=host, query=query, headers=headers,
                                             range_header=range_header, log=log)
        return Response('Not Found', status=404)
    except (ValueError, OSError):
        return Response('Invalid or unavailable resource', status=404)
