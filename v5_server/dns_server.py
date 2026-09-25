# -*- coding: utf-8 -*-
"""
dns_server.py — 内置轻量级 DNS 拦截与转发服务器 (UDP 53)

特点：
1. 零依赖：纯 Python 标准库 (socket / struct / threading) 实现标准 RFC 1035 DNS 协议。
2. 域名劫持：将所有 *.ys4fun.com、*.ys4fun.cn、*.soboten.com 自动解析为指定的宿主机局域网 IP (host_ip)。
3. 透明转发：非深空之眼域名的常规上网请求，无缝转发至上游公共 DNS (223.5.5.5 / 114.114.114.114)。
4. 极致体验：iOS 手机连上局域网 Wi-Fi 后，只需在 Wi-Fi 设置中将 DNS 改为电脑 IP，无需安装任何第三方代理 App 即可秒连！
"""
import socket
import struct
import threading
import time

UPSTREAM_DNS = ("223.5.5.5", 53)
HIJACK_SUFFIXES = ("ys4fun.com", "ys4fun.cn", "soboten.com")


def parse_domain(data, offset=12):
    """解析 DNS 查询中的域名。"""
    labels = []
    idx = offset
    try:
        while True:
            if idx >= len(data):
                break
            length = data[idx]
            if length == 0:
                idx += 1
                break
            # 指针处理
            if (length & 0xC0) == 0xC0:
                ptr_offset = struct.unpack("!H", data[idx:idx + 2])[0] & 0x3FFF
                sub_name, _ = parse_domain(data, ptr_offset)
                labels.append(sub_name)
                idx += 2
                break
            idx += 1
            labels.append(data[idx:idx + length].decode("utf-8", errors="ignore"))
            idx += length
        return ".".join(labels), idx
    except Exception:
        return "", idx


import concurrent.futures

def build_dns_response(req_data, qname, target_ip):
    """构建符合 RFC 1035 标准的 DNS 响应报文（支持 Type A 与非 A 类型的标准空应答）。"""
    trans_id = req_data[:2]
    domain, next_offset = parse_domain(req_data, 12)
    if len(req_data) < next_offset + 4:
        return None
    qtype = struct.unpack("!H", req_data[next_offset:next_offset + 2])[0]
    qclass = struct.unpack("!H", req_data[next_offset + 2:next_offset + 4])[0]
    q_section = req_data[12:next_offset + 4]

    if qtype == 1:  # Type A (IPv4)
        flags = b"\x81\x80"  # Standard query response, No error
        counts = struct.pack("!HHHH", 1, 1, 0, 0)
        header = trans_id + flags + counts

        ans_name = b"\xc0\x0c"
        ans_type = struct.pack("!H", 1)   # Type A
        ans_class = struct.pack("!H", 1)  # Class IN
        ans_ttl = struct.pack("!I", 60)   # TTL 60s
        ans_len = struct.pack("!H", 4)    # Length 4 bytes
        ip_parts = [int(p) for p in target_ip.split(".")]
        ans_data = bytes(ip_parts)
        answer = ans_name + ans_type + ans_class + ans_ttl + ans_len + ans_data
        return header + q_section + answer
    else:
        # 非 Type A 查询（如 iOS 发起的 AAAA/Type 28 或 HTTPS/Type 65）：返回 NOERROR 且 0 answer，提示客户端降级走 Type A
        flags = b"\x81\x80"
        counts = struct.pack("!HHHH", 1, 0, 0, 0)
        header = trans_id + flags + counts
        return header + q_section


class DnsServer:
    """轻量级 UDP:53 DNS 服务器。"""

    def __init__(self, host_ip="192.168.0.105", port=53, log=print):
        self.host_ip = host_ip
        self.port = port
        self.log = log
        self.running = False
        self.sock = None
        self._thread = None
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="dns_fwd")

    def start(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
            self.sock.bind(("0.0.0.0", self.port))
            self.running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self.log(f"[DNS] [OK] 轻量级 DNS 服务已启动，监听 0.0.0.0:{self.port} -> 劫持目标 IP: {self.host_ip}")
            return True
        except PermissionError:
            self.log(f"[DNS] [WARN] 启动 53 端口失败 (需要管理员权限)，iOS 设备可通过 Surge/Shadowrocket 或手动代理连接。")
            return False
        except Exception as e:
            self.log(f"[DNS] [ERROR] 启动 DNS 服务失败: {e}")
            return False

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        try:
            self._pool.shutdown(wait=False)
        except Exception:
            pass

    def _run(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                if not data or len(data) < 12:
                    continue

                domain, next_offset = parse_domain(data, 12)
                domain_lower = domain.lower()

                # 判断是否需要劫持
                should_hijack = any(domain_lower == s or domain_lower.endswith("." + s) for s in HIJACK_SUFFIXES)

                if should_hijack:
                    resp_pkt = build_dns_response(data, domain, self.host_ip)
                    if resp_pkt:
                        self.sock.sendto(resp_pkt, addr)
                        self.log(f"[DNS] [HIJACK] 劫持解析: {domain} -> {self.host_ip} (客户端: {addr[0]})")
                else:
                    # 线程池转发给公共上游 DNS
                    self._pool.submit(self._forward, data, addr)
            except Exception:
                if not self.running:
                    break

    def _forward(self, data, client_addr):
        try:
            fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            fwd_sock.settimeout(2.5)
            fwd_sock.sendto(data, UPSTREAM_DNS)
            resp, _ = fwd_sock.recvfrom(2048)
            if self.sock and self.running:
                self.sock.sendto(resp, client_addr)
            fwd_sock.close()
        except Exception:
            pass


def start_dns_service(host_ip="192.168.0.105", log=print):
    """便捷启动入口。"""
    server = DnsServer(host_ip=host_ip, log=log)
    if server.start():
        return server
    return None


if __name__ == "__main__":
    srv = start_dns_service("192.168.0.105")
    if srv:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            srv.stop()
