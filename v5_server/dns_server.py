# -*- coding: utf-8 -*-
"""局域网 DNS：目标域名指向本机，其他查询原样转发（UDP/TCP）。"""
import concurrent.futures
import socket
import struct
import threading
import time

UPSTREAM_DNS = ("223.5.5.5", 53)
UPSTREAM_DNS_BACKUP = ("114.114.114.114", 53)
HIJACK_SUFFIXES = ("ys4fun.com", "ys4fun.cn", "soboten.com")
MAX_MESSAGE = 65535


def parse_domain(data, offset=12):
    """解析标签与压缩指针；拒绝截断、循环指针和超长名称。"""
    labels, visited = [], set()
    idx, end, size = offset, None, 1
    while True:
        if idx >= len(data) or idx in visited:
            raise ValueError("truncated or cyclic DNS name")
        visited.add(idx)
        length = data[idx]
        if length & 0xC0 == 0xC0:
            if idx + 1 >= len(data):
                raise ValueError("truncated DNS pointer")
            if end is None:
                end = idx + 2
            idx = struct.unpack_from("!H", data, idx)[0] & 0x3FFF
            continue
        if length & 0xC0:
            raise ValueError("invalid DNS label")
        idx += 1
        if not length:
            return ".".join(labels), end if end is not None else idx
        size += length + 1
        if idx + length > len(data) or size > 255:
            raise ValueError("truncated or oversized DNS name")
        labels.append(data[idx:idx + length].decode("ascii"))
        idx += length


def _question(data):
    if len(data) < 12:
        raise ValueError("short DNS header")
    flags, count = struct.unpack_from("!HH", data, 2)
    if flags & 0x8000 or count != 1:
        raise ValueError("expected one DNS question")
    domain, end = parse_domain(data)
    if end + 4 > len(data):
        raise ValueError("short DNS question")
    qtype, qclass = struct.unpack_from("!HH", data, end)
    return domain, qtype, qclass


def _encoded_question(question):
    domain, qtype, qclass = question
    name = b"".join(bytes([len(part)]) + part.encode("ascii")
                    for part in domain.split(".") if part) + b"\0"
    return name + struct.pack("!HH", qtype, qclass)


def _response(data, question=None, rcode=0, answer=b""):
    # 保留 Opcode/RD/CD，不把客户端的 AD 位误报为已验证。
    flags = 0x8080 | (struct.unpack_from("!H", data, 2)[0] & 0x7910) | rcode
    return (data[:2] + struct.pack("!HHHHH", flags, int(question is not None),
                                 int(bool(answer)), 0, 0)
            + (_encoded_question(question) if question is not None else b"") + answer)


def build_dns_response(req_data, qname, target_ip):
    """目标域名仅合成 IN/A；AAAA/HTTPS 等返回空记录，不影响其他域名。"""
    question = _question(req_data)
    answer = b""
    if question[1:] == (1, 1):
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(target_ip)
    return _response(req_data, question, answer=answer)


def _recv_exact(sock, length):
    parts = bytearray()
    while len(parts) < length:
        chunk = sock.recv(length - len(parts))
        if not chunk:
            raise OSError("DNS TCP peer closed")
        parts.extend(chunk)
    return bytes(parts)


class DnsServer:
    """有界并发、双上游、UDP/TCP 同端口；不读写系统 DNS 配置。"""

    def __init__(self, host_ip="192.168.0.105", port=53, log=print, *,
                 upstreams=None, timeout=1.0, bind_host="0.0.0.0", upstream_bind_ip=None):
        socket.inet_aton(host_ip)
        if upstream_bind_ip is not None:
            socket.inet_aton(upstream_bind_ip)
        self.upstream_bind_ip = upstream_bind_ip
        self.host_ip, self.port, self.log = host_ip, port, log
        self.upstreams = tuple(upstreams if upstreams is not None
                               else (UPSTREAM_DNS, UPSTREAM_DNS_BACKUP))
        if not self.upstreams or timeout <= 0:
            raise ValueError("DNS needs upstreams and a positive timeout")
        self.timeout, self.bind_host = timeout, bind_host
        self.running = False
        self.sock = self._tcp_sock = None
        self._pool = self._tcp_pool = None
        self._thread = self._tcp_thread = None
        self._pending = threading.BoundedSemaphore(64)
        self._tcp_slots = threading.BoundedSemaphore(16)
        self._clients = set()
        self._state_lock = threading.Lock()
        self._warning_at = float("-inf")

    def _warn(self, message):
        with self._state_lock:
            now = time.monotonic()
            if now - self._warning_at < 30:
                return
            self._warning_at = now
        self.log(f"[DNS] [WARN] {message}（同类故障日志最多每 30 秒一次）")

    def start(self):
        if self.running:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind((self.bind_host, self.port))
            self.port = self.sock.getsockname()[1]
            self.sock.settimeout(0.25)
            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._tcp_sock.bind((self.bind_host, self.port))
            self._tcp_sock.listen(16)
            self._tcp_sock.settimeout(0.25)
            self._pool = concurrent.futures.ThreadPoolExecutor(16, thread_name_prefix="dns_fwd")
            self._tcp_pool = concurrent.futures.ThreadPoolExecutor(16, thread_name_prefix="dns_tcp")
            self.running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._tcp_thread = threading.Thread(target=self._accept_tcp, daemon=True)
            self._thread.start()
            self._tcp_thread.start()
            self.log(f"[DNS] [OK] UDP/TCP {self.bind_host}:{self.port}，目标 IP {self.host_ip}，上游 {self.upstreams}，出站地址 {self.upstream_bind_ip or '自动'}")
            return True
        except OSError as exc:
            self.stop()
            self.log(f"[DNS] [ERROR] DNS UDP/TCP 启动失败: {exc}")
            return False

    def stop(self):
        self.running = False
        with self._state_lock:
            sockets = [self.sock, self._tcp_sock, *self._clients]
        for sock in sockets:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        for thread in (self._thread, self._tcp_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=0.5)
        for pool in (self._pool, self._tcp_pool):
            if pool:
                pool.shutdown(wait=False, cancel_futures=True)

    def _local_response(self, data):
        if len(data) < 12 or data[2] & 0x80:
            return b""  # 不对响应包再响应。
        try:
            question = _question(data)
        except (ValueError, UnicodeError):
            return _response(data, rcode=1)
        if data[2] & 0x78:
            return _response(data, question, rcode=4)
        domain, _, qclass = question
        if qclass == 1 and any(domain.lower() == suffix or domain.lower().endswith("." + suffix)
                               for suffix in HIJACK_SUFFIXES):
            return build_dns_response(data, domain, self.host_ip)
        return None

    def _run(self):
        sock = self.sock
        while self.running:
            try:
                data, addr = sock.recvfrom(MAX_MESSAGE)
                response = self._local_response(data)
                if response is not None:
                    if response:
                        sock.sendto(response, addr)
                elif self._pending.acquire(blocking=False):
                    try:
                        future = self._pool.submit(self._forward, data, addr, sock)
                    except RuntimeError:
                        self._pending.release()
                        raise
                    future.add_done_callback(lambda _: self._pending.release())
                else:
                    sock.sendto(_response(data, _question(data), rcode=2), addr)
                    self._warn("上游转发队列已满，返回 SERVFAIL")
            except socket.timeout:
                continue
            except OSError as exc:
                if self.running:
                    self._warn(f"UDP 收发失败: {exc}")
            except RuntimeError:
                if not self.running:
                    break
                raise

    @staticmethod
    def _matches(data, response):
        if len(response) < 12 or response[:2] != data[:2] or not response[2] & 0x80:
            return False
        if response[2] & 0x78 != data[2] & 0x78:
            return False
        try:
            # _question 面向查询，校验响应时仅临时清除 QR。
            question = _question(response[:2] + bytes([response[2] & 0x7F]) + response[3:])
            expected = _question(data)
            return (question[0].lower(), *question[1:]) == (expected[0].lower(), *expected[1:])
        except (ValueError, UnicodeError):
            return False

    def _exchange(self, data, upstream, tcp):
        if tcp:
            source = (self.upstream_bind_ip, 0) if self.upstream_bind_ip else None
            with socket.create_connection(upstream, self.timeout, source_address=source) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(struct.pack("!H", len(data)) + data)
                length = struct.unpack("!H", _recv_exact(sock, 2))[0]
                response = _recv_exact(sock, length)
                if not self._matches(data, response) or response[2] & 2:
                    raise OSError("invalid upstream TCP response")
                return response
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            if self.upstream_bind_ip:
                sock.bind((self.upstream_bind_ip, 0))
            # connect 将源地址限制为选定上游；每个请求独立 socket，事务 ID 不串包。
            sock.connect(upstream)
            sock.send(data)
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("upstream DNS timed out")
                sock.settimeout(remaining)
                response = sock.recv(MAX_MESSAGE)
                if self._matches(data, response):
                    return response  # TC 原样保留，客户端可以通过本服务重试 TCP。

    def _resolve(self, data, tcp=False):
        last_response = None
        for upstream in self.upstreams:
            try:
                response = self._exchange(data, upstream, tcp)
                if response[3] & 15 not in (2, 5):
                    return response
                last_response = response
            except OSError:
                continue
        self._warn(f"全部上游解析失败，transport={'TCP' if tcp else 'UDP'}")
        return last_response or _response(data, _question(data), rcode=2)

    def _forward(self, data, client_addr, sock=None):
        response = self._resolve(data)
        if self.running:
            try:
                (sock or self.sock).sendto(response, client_addr)
            except OSError:
                pass

    def _accept_tcp(self):
        listener = self._tcp_sock
        while self.running:
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._tcp_slots.acquire(blocking=False):
                client.close()
                self._warn("TCP DNS 连接数已满")
                continue
            with self._state_lock:
                self._clients.add(client)
            try:
                future = self._tcp_pool.submit(self._serve_tcp, client)
                future.add_done_callback(lambda _, client=client: self._close_client(client))
            except RuntimeError:
                self._close_client(client)
                break

    def _close_client(self, client):
        client.close()
        with self._state_lock:
            self._clients.discard(client)
        self._tcp_slots.release()

    def _serve_tcp(self, client):
        client.settimeout(5.0)
        try:
            while self.running:
                length = struct.unpack("!H", _recv_exact(client, 2))[0]
                data = _recv_exact(client, length)
                response = self._local_response(data)
                if response is None:
                    response = self._resolve(data, tcp=True)
                if not response:
                    return
                client.sendall(struct.pack("!H", len(response)) + response)
        except OSError:
            pass


def start_dns_service(host_ip="192.168.0.105", log=print, *, upstreams=None):
    # main 的 host_ip 是本机真实 LAN 地址；出站绑定避免 TUN 把公共 DNS 改写为手机不可达的 fake-IP。
    server = DnsServer(host_ip=host_ip, log=log, upstreams=upstreams, upstream_bind_ip=host_ip)
    return server if server.start() else None


if __name__ == "__main__":
    srv = start_dns_service()
    if srv:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            srv.stop()
