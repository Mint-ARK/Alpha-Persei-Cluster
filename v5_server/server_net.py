# -*- coding: utf-8 -*-
"""
server_net.py — V5 网络层（唯一碰 socket 的模块）

职责（老大定稿：组件独立模块，单向依赖）：
- TCP 监听（gateway/game）、每连接一线程
- 收帧 → transport 切帧/解 zlib → CoreRequest → core.dispatch → 组帧回发
- idx/srv 由网络层分配（传输计数，CORE 不碰）
- 登录洪流：replay 模块激活时 cs_10200 走素材流

依赖：transport（帧层）+ core（CORE 分发）。被 main.py 装配。
"""
import os
import json
import mimetypes
import socket
import socketserver
import sys
import time
import threading
import collections
import uuid

_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
if _CUR_DIR not in sys.path:
    sys.path.insert(0, _CUR_DIR)

import core as core_mod        # noqa: E402  CoreRequest/Connection/Core
import generator as _gen       # noqa: E402
from transport import pack_down, unpack_up, FrameParser  # noqa: E402
import cdn_proxy               # noqa: E402  官方 CDN 穿透与动态抓包器
import gen_cert                # noqa: E402  局域网 IP 与证书工具
from logger import log as _logger_log

_HOST_IP = [gen_cert.get_default_lan_ip()]


def get_host_ip():
    """获取当前配置的宿主机局域网 IP。"""
    return _HOST_IP[0]


def set_host_ip(ip):
    """设置宿主机局域网 IP。"""
    if ip and ip != "0.0.0.0":
        _HOST_IP[0] = str(ip)


_LATEST_CLIENT_PLATFORM = ["pc"]


def get_latest_client_platform() -> str:
    """获取最近一次连接或识别到的游戏客户端平台（'pc' / 'ios' / 'android'）。"""
    return _LATEST_CLIENT_PLATFORM[0]


def record_client_platform(plat: str):
    """记录客户端平台类型。"""
    if plat in ("ios", "android", "pc"):
        _LATEST_CLIENT_PLATFORM[0] = plat


# 全量数据初始化/快照帧（通常仅在登录洪流或初次拉取时全量下发一次）：
# 运行时业务操作中若被二次推送，极易因空包清空客户端对应系统内存（如 17009 空包清空材料，14009 空包清空英雄），
# 或引发客户端大列表重排刷新卡顿。通用网络层检测到此类帧二次推送时显式报警，方便后续开发与智能体缉查定位。
FULL_SYNC_FRAMES = {
    17009,  # 材料全量 (InitMaterialList -> materialList_ = {})，原子替代帧: 17023
    17007,  # 过期材料全量 (InitExpiredMaterialList -> expiredMaterialList_ = {})
    13009,  # 刻印全量 (EquipInit -> 全量重排与刷新)，原子替代帧: 13019
    14001,  # 英雄初始数据
    14009,  # 英雄/碎片全量 (InitHero -> 清空重置全部英雄内存)，原子替代帧: 14007
    46001,  # 钥从全量 (InitServant -> 全量重建)，原子替代帧: 46011/46013
    50001,  # 刻印芯片全量
    23009,  # 玩家信息全量
}


# ---------------- 真实服务端网络吞吐计量器 (ServerTrafficTracker) ----------------

class ServerTrafficTracker:
    """服务端真实网络传输流量计量器（线程安全，支持 1Hz 瞬时吞吐率计算）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._total_bytes = 0
        self._last_ts = time.time()
        self._last_total = 0
        self._current_kbps = 0.0

    def record_bytes(self, n):
        """记录传输的字节数（收/发均计入吞吐）。"""
        if n and n > 0:
            with self._lock:
                self._total_bytes += int(n)

    def get_throughput_kbps(self):
        """计算并获取当前瞬时网络吞吐（KB/s）。若无通信则返回 0.0。"""
        with self._lock:
            now = time.time()
            dt = now - self._last_ts
            if dt >= 0.5:
                delta_bytes = self._total_bytes - self._last_total
                kbps = (delta_bytes / 1024.0) / dt
                self._current_kbps = round(max(0.0, kbps), 1)
                self._last_ts = now
                self._last_total = self._total_bytes
            return self._current_kbps


traffic_tracker = ServerTrafficTracker()


def _resolve_static_web_file(static_web_dir, request_path):
    """Resolve a /web or /static_web asset without permitting path traversal."""
    if request_path.startswith("/web/"):
        relative = request_path[len("/web/"):]
    elif request_path.startswith("/static_web/"):
        relative = request_path[len("/static_web/"):]
    else:
        return None
    relative = relative.replace("\\", "/")
    root = os.path.realpath(static_web_dir)
    candidate = os.path.realpath(os.path.join(root, *relative.split("/")))
    try:
        if os.path.commonpath((root, candidate)) != root:
            return None
    except ValueError:
        return None

    if os.path.isfile(candidate):
        return candidate

    # 动态 WebP 透明回退与互转：若请求 .png / .jpg 文件但本地已瘦身为 .webp，透明返回 .webp
    lower_cand = candidate.lower()
    if lower_cand.endswith((".png", ".jpg", ".jpeg")):
        webp_candidate = os.path.splitext(candidate)[0] + ".webp"
        if os.path.isfile(webp_candidate):
            return webp_candidate
    elif lower_cand.endswith(".webp"):
        png_candidate = os.path.splitext(candidate)[0] + ".png"
        if os.path.isfile(png_candidate):
            return png_candidate
    return None


# ---------------- 在线游戏客户端 TCP 长连接管理 (Online Game Connections) ----------------

_online_conns_lock = threading.Lock()
_online_game_conns = {}  # uid (int) -> set of conn


def register_game_conn(conn, uid):
    """注册在线游戏 TCP 长连接（支持 uid 动态更新与多连接共存）。"""
    if conn is None or not uid:
        return
    with _online_conns_lock:
        uid_int = int(uid)
        s = _online_game_conns.setdefault(uid_int, set())
        s.add(conn)


def unregister_game_conn(conn, uid=None):
    """注销在线游戏 TCP 长连接。"""
    if conn is None:
        return
    with _online_conns_lock:
        if uid:
            uid_int = int(uid)
            s = _online_game_conns.get(uid_int)
            if s:
                s.discard(conn)
                if not s:
                    _online_game_conns.pop(uid_int, None)
        else:
            to_remove = []
            for u, s in _online_game_conns.items():
                s.discard(conn)
                if not s:
                    to_remove.append(u)
            for u in to_remove:
                _online_game_conns.pop(u, None)


def get_online_uids():
    """获取当前所有在线客户端 UID 列表。"""
    with _online_conns_lock:
        return list(_online_game_conns.keys())


def push_mail_notice(uid):
    """主动向指定 UID 的在线长连接下发 sc_30001（未读红点摘要通知帧）。
    返回成功下发通知的客户端连接数。
    """
    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        return 0

    with _online_conns_lock:
        conns = list(_online_game_conns.get(uid_int, []))
    if not conns:
        return 0

    conn0 = conns[0]
    db = getattr(conn0, "db", None)
    codec_encode = getattr(conn0, "codec_encode", None)
    if not db or not codec_encode:
        try:
            from account_db import get_db
            db = db or get_db()
        except Exception:
            pass

    if not db or not codec_encode:
        return 0

    try:
        from mail_service import MailService
        summary = MailService.get_instance(db).get_login_summary(uid_int)
        payload = codec_encode("sc_30001", summary)
        if not payload:
            return 0
    except Exception as _e:
        if hasattr(conn0, "log"):
            conn0.log(f"[PUSH] 构建 sc_30001 异常: {_e}", "WARN")
        return 0

    pushed_count = 0
    for c in conns:
        try:
            if hasattr(c, "send_push"):
                c.send_push(30001, payload)
                pushed_count += 1
        except Exception as _pe:
            if hasattr(c, "log"):
                c.log(f"[PUSH] push_mail_notice 异常: {_pe}", "WARN")

    return pushed_count


class GameHandler(socketserver.StreamRequestHandler):
    """game TCP 连接处理：收帧 → CORE 分发 → 组帧回发。
    server 属性由 ThreadedServer 装配注入（core/db/generator/codec/replay/material/frame_delay）。"""

    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        self.server.core.log(f"[TCP:{self.server.game_port}] [{peer}] 连接建立", "INFO")
        uid = _gen.DEFAULT_UID  # 雏形：登录后 uid 绑定（后续接登录链）
        conn = core_mod.Connection(uid=uid, db=self.server.db,
                                   generator=self.server.generator,
                                   codec_encode=self.server.codec_encode,
                                   codec_decode=self.server.codec_decode,
                                   log=self.server.core.log)

        sent_sc_counts = collections.defaultdict(int)
        self.sent_sc_counts = sent_sc_counts

        def _check_secondary_full_frame(sc_cmd, trigger_source):
            sent_sc_counts[sc_cmd] += 1
            if sc_cmd in FULL_SYNC_FRAMES and sent_sc_counts[sc_cmd] > 1:
                self.server.core.log(
                    f"[server_net][WARN] SC_{sc_cmd} 检测到二次推送，出现问题时需要缉查是否要替换为原子帧 ({trigger_source})",
                    "WARN"
                )

        _send_lock = threading.Lock()

        def _send_push(sc_cmd, payload, flag=0):
            try:
                _check_secondary_full_frame(sc_cmd, "push")
                with _send_lock:
                    srv = (conn.seq[0] if hasattr(conn, "seq") and conn.seq else 0) + 1
                    if hasattr(conn, "seq") and conn.seq:
                        conn.seq[0] = srv
                    raw = pack_down(sc_cmd, payload, 0, srv, flag=flag, compress=None)
                    traffic_tracker.record_bytes(len(raw))
                    self.request.sendall(raw)
                self.server.core.log(f"[PUSH #{srv}] -> SC_{sc_cmd} | Size: {len(payload)}B | flag={flag}", "INFO")
            except Exception as _e:
                self.server.core.log(f"[PUSH] SC_{sc_cmd} 推送异常: {_e}", "WARN")

        conn.send_push = _send_push
        register_game_conn(conn, conn.uid)
        fp = FrameParser("up")
        self.request.settimeout(1.0)
        try:
            while True:
                try:
                    data = self.request.recv(65536)
                    if data:
                        traffic_tracker.record_bytes(len(data))
                except socket.timeout:
                    # 挂机/空闲滴答：检查在线体力是否未满且到达 360 秒，若满足主动推送 sc_15009
                    if conn.uid:
                        try:
                            import fatigue_service as _fs
                            _fs.check_and_push_fatigue(conn, conn.uid, self.server.db, self.server.codec_encode)
                        except Exception:
                            pass
                    continue
                if not data:
                    break
                # 调试期诊断：每次 recv 的原始数据（前 60B hex）+ 切帧结果
                self.server.core.log(
                    f"[TCP:{self.server.game_port}] recv {len(data)}B buf前={len(fp.buf)}B "
                    f"hex={data[:60].hex(' ')}", "DEBUG")
                frames_in = []
                for frame in fp.feed(data):
                    try:
                        up = unpack_up(frame)
                    except Exception as e:
                        # 调试期记录吞帧（防静默丢请求导致客户端卡）
                        self.server.core.log(
                            f"[TCP:{self.server.game_port}] 吞帧 {len(frame)}B 异常: {e} "
                            f"hex={frame[:10].hex(' ') if len(frame) > 4 else frame.hex(' ')}", "WARN")
                        continue
                    frames_in.append(up)
                if frames_in:
                    self.server.core.log(
                        f"[TCP:{self.server.game_port}] 本次切帧 {len(frames_in)} 个 buf剩={len(fp.buf)}B "
                        f"cmds={[u['cmd'] for u in frames_in]}", "DEBUG")
                    # 注入客户端活跃心跳脉冲（滑动时间窗口记录在线时长）
                    if conn.uid:
                        try:
                            from lazy_timer import lazy_timer
                            lazy_timer.pulse_heartbeat(conn.to_handler_ctx(), conn.uid)
                        except Exception:
                            pass
                for up in frames_in:
                    cmd = up["cmd"]
                    # 登录洪流（10200 触发）——DB 驱动（login_push 表 + generator 动态帧）
                    # 2026-08-16 阶段2：重放系统从功能链路退役，洪流不再读素材文件
                    if cmd == 10200:
                        import login as _login
                        _login.send_login_flood(
                            self.request, self.server.db, conn.uid,
                            self.server.generator, frame_delay=self.server.frame_delay,
                            log=self.server.core.log)
                        # 登录洪流完成：标记全量初始化帧已下发首次初态基准
                        for _init_sc in FULL_SYNC_FRAMES:
                            sent_sc_counts[_init_sc] = max(1, sent_sc_counts[_init_sc])
                        # [Boss Directive] 登录洪流下发完毕，广播/注册激活在线体力主动定时器与长连接连接池
                        register_game_conn(conn, conn.uid)
                        try:
                            import fatigue_service as _fs
                            _fs.register_active_conn(conn)
                        except Exception as _fse:
                            self.server.core.log(f"[Fatigue] 注册主动定时器异常: {_fse}", "WARN")
                        continue
                    # 登录验证帧（10042→10043）——原生动态生成
                    if cmd == 10042:
                        try:
                            dec_up = self.server.codec_decode(up["payload"], "cs_10042")
                            if isinstance(dec_up, dict) and dec_up.get("user_id"):
                                old_uid = conn.uid
                                new_uid = int(dec_up["user_id"])
                                if new_uid != old_uid:
                                    unregister_game_conn(conn, old_uid)
                                    conn.uid = new_uid
                                    register_game_conn(conn, conn.uid)
                        except Exception:
                            pass
                        d_10043 = {
                            "result": 0,
                            "register_timestamp": 1690000000,
                            "timestamp": int(time.time()),
                            "verify_timestamp": 316800,
                            "uid_sign": "mock_uid_sign"
                        }
                        payload = self.server.codec_encode("sc_10043", d_10043) or b"\x08\x00\x10\x80\xb5\xed\xa5\x06\x18\x96\x96\xc5\xd4\x06\x20\x80\xab\x13\x2a\x09\x6d\x6f\x63\x6b\x5f\x73\x69\x67\x6e"
                        out = pack_down(10043, payload, up["index"], up["server_idx"] + 1)
                        traffic_tracker.record_bytes(len(out))
                        self.request.sendall(out)
                        self.server.core.log(f"[GAME] cs_10042 → sc_10043（原生动态生成，uid={conn.uid}）", "INFO")
                        continue
                    # CORE 分发
                    req = core_mod.CoreRequest(cmd, up["payload"], uid=conn.uid,
                                               index=up["index"], server_idx=up["server_idx"])
                    try:
                        resp = self.server.core.dispatch(req, conn)
                    except Exception as _de:
                        self.server.core.log(f"[GAME] 处理 CS_{cmd} 内部异常已隔离: {_de}", "ERROR")
                        continue
                    src_tag = str(getattr(resp, "src", "dynamic") or "dynamic").upper()
                    if not resp.reply:
                        self.server.core.log(f"CS_{cmd} -> (No Reply) | Source: [{src_tag}]", "DEBUG", module="NET", to_console=False)
                        continue
                    # 组帧回发（idx 回显请求，srv 递增；91xxx 用连接级）
                    out = b""
                    next_srv = up["server_idx"] + 1
                    for f in resp.frames:
                        srv = f["srv"] if f["srv"] is not None else next_srv
                        _check_secondary_full_frame(f["sc"], f"cmd=CS_{cmd}")
                        # compress=None = 自动：payload>10KB 就 deflate（与真实服务器一致）。
                        try:
                            out += pack_down(f["sc"], f["payload"], up["index"], srv,
                                             flag=f["flag"], compress=None)
                        except Exception as _pe:
                            # 单帧组装失败只丢这一帧并留日志，不连坐整条连接
                            self.server.core.log(
                                f"CS_{cmd} -> SC_{f['sc']} 组帧失败已丢弃: {_pe}", "ERROR", module="NET")
                            continue
                        # 底层逐帧写入底账文件，控制台由 LOGISTICS 物流日志高亮呈现
                        self.server.core.log(
                            f"[FRAME #{srv}] CS_{cmd} -> SC_{f['sc']} | Source: [{src_tag}] | Size: {len(f['payload'])}B | flag={f.get('flag', 0)}",
                            "DEBUG", module="NET", to_console=False
                        )
                        next_srv = srv + 1
                    if out:
                        traffic_tracker.record_bytes(len(out))
                        with _send_lock:
                            self.request.sendall(out)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            try:
                unregister_game_conn(conn, conn.uid)
            except Exception:
                pass
            try:
                import fatigue_service as _fs
                _fs.unregister_active_conn(conn)
            except Exception:
                pass
            if conn.uid:
                try:
                    from lazy_timer import lazy_timer
                    lazy_timer.on_disconnect(conn.to_handler_ctx(), conn.uid)
                except Exception:
                    pass
            self.server.core.log(f"[TCP:{self.server.game_port}] [{peer}] 连接关闭", "INFO")


class ThreadedServer(socketserver.ThreadingTCPServer):
    """每连接一线程 TCP 服务器。main 装配后注入依赖。"""
    allow_reuse_address = True
    daemon_threads = True


class GatewayHandler(socketserver.StreamRequestHandler):
    """gateway TCP 连接（8102）：登录握手 + 心跳。

    10038 → 原生 SC_10039（下发目标 game TCP 端口 + 动态时间戳）
    10042 → 原生 SC_10043（登录验证）
    10050 → 原生 SC_10051（心跳）
    """

    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        self.server.core.log(f"[GW:{self.server.gw_port}] [{peer}] 连接建立", "INFO")
        gw_resp_map = self.server.material.get("gw_resp_map") or {} if self.server.material else {}
        fp = FrameParser("up")
        try:
            while True:
                data = self.request.recv(65536)
                if not data:
                    break
                traffic_tracker.record_bytes(len(data))
                for frame in fp.feed(data):
                    try:
                        up = unpack_up(frame)
                    except Exception as e:
                        # 调试期记录吞帧
                        self.server.core.log(
                            f"[GW:{self.server.gw_port}] 吞帧 {len(frame)}B 异常: {e} "
                            f"hex={frame[:10].hex(' ') if len(frame) > 4 else frame.hex(' ')}", "WARN")
                        continue
                    cmd = up["cmd"]
                    out = b""
                    if cmd == 10038:
                        # 原生下发 SC_10039：动态判断客户端来源（本机 127.0.0.1 或 局域网 IP）
                        sock_ip = self.request.getsockname()[0] if hasattr(self.request, "getsockname") else "127.0.0.1"
                        peer_ip = self.client_address[0] if hasattr(self, "client_address") else "127.0.0.1"
                        lan_ip = getattr(self.server, "host_ip", None) or _HOST_IP[0] or get_host_ip()
                        game_ip = "127.0.0.1" if peer_ip in ("127.0.0.1", "::1") else lan_ip
                        d = {
                            "result": 0,
                            "server_id": 1,
                            "ip": game_ip,
                            "port": self.server.game_port or 8105,
                            "user_id": _gen.DEFAULT_UID,
                            "gstoken": "45f95a4402b51fddcd17b81dd3edfe06",
                            "timestamp": int(time.time()),
                        }
                        payload = self.server.codec_encode("sc_10039", d)
                        out = pack_down(10039, payload, up["index"], up["server_idx"] + 1)
                        self.server.core.log(f"[GW] cs_10038 → sc_10039（下发目标 {game_ip}:{self.server.game_port or 8105}，客户端 {peer_ip}）", "INFO")
                    elif cmd == 10042:
                        # 原生下发 SC_10043（登录验证）
                        d = {
                            "result": 0,
                            "register_timestamp": 1690000000,
                            "timestamp": int(time.time()),
                            "verify_timestamp": 316800,
                            "uid_sign": "mock_uid_sign"
                        }
                        payload = self.server.codec_encode("sc_10043", d)
                        out = pack_down(10043, payload, up["index"], up["server_idx"] + 1)
                        self.server.core.log(f"[GW] cs_10042 → sc_10043（原生登录验证）", "INFO")
                    elif cmd == 10050:
                        # 心跳动态生成（verify_timestamp=316800 常量）
                        p = self.server.codec_encode(
                            "sc_10051",
                            {"state": 0, "timestamp": int(time.time()), "verify_timestamp": 316800}
                        ) or b"\x08\x00"
                        out = pack_down(10051, p, up["index"], up["server_idx"] + 1)
                    elif gw_resp_map and cmd in gw_resp_map:
                        # 其余心跳查表回放
                        raw = bytes.fromhex(gw_resp_map[cmd])
                        out = pack_down(cmd + 1, raw[11:], up["index"], up["server_idx"] + 1)
                    else:
                        self.server.core.log(f"[GW] 未知 cmd={cmd} 不回包", "WARN")
                    if out:
                        traffic_tracker.record_bytes(len(out))
                        self.request.sendall(out)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            self.server.core.log(f"[GW:{self.server.gw_port}] [{peer}] 连接关闭", "INFO")


def start_gateway_server(port, core, db, generator, codec_encode, codec_decode,
                         material=None, host_ip=None):
    """启动 gateway TCP 服务器（登录握手）。"""
    srv = ThreadedServer(("0.0.0.0", port), GatewayHandler)
    srv.core = core
    srv.db = db
    srv.generator = generator
    srv.codec_encode = codec_encode
    srv.codec_decode = codec_decode
    srv.material = material
    srv.gw_port = port
    srv.host_ip = host_ip or _HOST_IP[0]
    srv.game_port = None  # main 装配时设为 game 端口
    return srv


# ---------------- HTTPS 层（Flask：原生 SDK 服务 + 直通登录 + 区服列表） ----------------

def build_https_app(core, material=None, game_port=8105, gw_port=8102, host_ip=None):
    """构建 Flask HTTPS 应用（原生 SDK 服务 + 直通鉴权 + 区服列表 + 官方 CDN 动态抓包代理）。

    1. 支持 Token 快速登录（秒进游戏，杜绝超时与降级验证码）
    2. 支持任意账密免验直通登录（留空方法口子，随时可做入库或校验扩展）
    3. 支持原生 SDK 配置、版本检查、公告、防沉迷与 OSS 直传
    4. 支持 cdn_proxy 官方 CDN 穿透抓包与自动持久化落盘
    5. 完全脱离外部素材依赖，零参数一键启动
    """
    import uuid
    from flask import Flask, request, jsonify, Response, send_file, redirect

    app = Flask("v5_https")
    idx = material.get("http_index") or {} if (material and isinstance(material, dict)) else {}
    lan_ip = host_ip or _HOST_IP[0] or get_host_ip()

    # 注册 GM 控制台统一 API 蓝图
    try:
        from gm_api import gm_bp, init_gm_api
        init_gm_api(core=core, game_port=game_port, gw_port=gw_port, host_ip=lan_ip)
        app.register_blueprint(gm_bp, url_prefix="/api/gm")
        print("[V5] GM API 统一蓝图已成功挂载到 /api/gm", flush=True)
    except Exception as _e:
        print(f"[V5] 挂载 GM API 蓝图异常: {_e}", flush=True)

    # 注册真实 HTTP 流量统计中间件（智能排除面板高频只读监控轮询 /api/gm/overview/*）
    @app.before_request
    def _track_request_traffic():
        if not request.path.startswith("/api/gm/overview/"):
            try:
                cl = request.content_length
                if cl and cl > 0:
                    traffic_tracker.record_bytes(cl)
                elif request.data:
                    traffic_tracker.record_bytes(len(request.data))
            except Exception:
                pass

    @app.after_request
    def _track_response_traffic(response):
        if not request.path.startswith("/api/gm/overview/"):
            try:
                cl = response.content_length
                if cl and cl > 0:
                    traffic_tracker.record_bytes(cl)
                elif hasattr(response, "data") and response.data:
                    traffic_tracker.record_bytes(len(response.data))
            except Exception:
                pass
        return response

    # SDK 账户与 Token 处理钩子（留空方法口子，方便未来对接真实注册/入库）
    def _verify_and_create_user(username, password=None):
        """账密校验与用户 Token 生成口子。当前不做校验，始终返回有效用户与 Token。"""
        uid = _gen.DEFAULT_UID
        token = f"{uuid.uuid4().hex[:32]}.{uid}"
        return {
            "uid": uid,
            "username": username or "Admin",
            "token": token
        }

    def _verify_token(token_str):
        """Token 消费与校验口子。当前不做校验，始终返回有效用户与 Token。"""
        uid = _gen.DEFAULT_UID
        token = token_str or f"{uuid.uuid4().hex[:32]}.{uid}"
        return {
            "uid": uid,
            "username": "Admin",
            "token": token
        }

    # 加载公告与适龄提示独立配置文件
    def _load_notice_cfg():
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notice_cfg.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "age_tip": "【深空之眼 · 隐科组特别提示】\n1. 本节点为管理员专享高维模拟世界，已解除体力限制与算力封锁。\n2. 适度抽卡益脑，沉迷游戏伤身；请管理员合理安排战斗时间，与修正者们携手守护盖亚世界！",
            "login_notice": {
                "title": "隐科组最高指令：V5 自主节点已全面激活",
                "content": "亲爱的管理员，欢迎连接深空之眼本地自主节点！\n\n- 原生协议链路已全面自包含，零外部素材依赖；\n- 隐科组 GM 运维控制台已上线（点击右上角反馈即可随时呼出）；\n- 适龄提示与公告系统已由本地核心接管；\n- 祝您在盖亚世界的战斗与探索一切顺利！",
                "enable": True
            }
        }

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
    @app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
    def _route(path):
        host = (request.headers.get("Host") or "").lower()
        qs = request.query_string.decode("utf-8", errors="replace")

        # 兼容处理 path
        if not path.startswith("/"):
            path = "/" + path

        raw_body = request.get_data()
        b_preview = raw_body[:300]
        b_str = f" body={b_preview.decode('utf-8', errors='ignore')}" if b_preview else ""
        client_ip = request.headers.get("X-Forwarded-For") or request.remote_addr or ""
        ua = request.headers.get("User-Agent", "")
        print(f"[HTTPS] [{request.method}] from={client_ip} host={host} path={path} qs={qs}{b_str}", flush=True)

        # 详细抓取客户端日志上报（崩溃、初始化异常、埋点）
        if "log" in path or "report" in path or "client" in path or "ta.ys4fun" in host:
            try:
                body_decoded = raw_body.decode("utf-8", errors="ignore")
                print(f"[CLIENT REPORT] path={path} host={host} UA={ua}\n  BODY={body_decoded}", flush=True)
            except Exception:
                pass

        # 1. 处理 OPTIONS 预检请求
        if request.method == "OPTIONS":
            resp = Response("", status=200)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD"
            resp.headers["Access-Control-Allow-Headers"] = "*"
            return resp

        static_web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_web")

        # 2. 静态 Web 页面服务（/web/* 与 /static_web/*）
        if path in ("/", "/web", "/web/"):
            return redirect("/web/gm_console.html", code=302)
        if path in ("/gm_console.html", "/control-panel.html"):
            return redirect("/web/gm_console.html", code=302)
        if path == "/agreement.html":
            return redirect("/web/agreement.html", code=302)

        if path.startswith("/web/") or path.startswith("/static_web/"):
            w_file = _resolve_static_web_file(static_web_dir, path)
            if w_file:
                mime_type = mimetypes.guess_type(w_file)[0] or "application/octet-stream"
                resp = send_file(w_file, mimetype=mime_type, conditional=True, max_age=0)
                resp.headers["Access-Control-Allow-Origin"] = "*"
                return resp

        # 3. 官方客服/反馈/工单外部链接拦截（soboten.com / ticketclient / feedback） -> 导向一体化随身安全与客服中枢
        if "ticketclient" in path or "soboten" in host or "feedback" in path:
            target_host = host if (host and not host.startswith("127.") and not host.startswith("localhost")) else f"{lan_ip}"
            if "getVisitorAndHelpConfig" in path:
                return jsonify({
                    "code": "1000",
                    "data": {
                        "companyName": "隐科组总务部",
                        "robotName": "弥特尔",
                        "msg": "欢迎使用深空之眼随身安全与客服中枢",
                        "url": f"https://{target_host}/web/agreement.html?tab=support"
                    }
                })
            if ".action" in path:
                return jsonify({
                    "retCode": "000000",
                    "retMsg": "操作成功",
                    "items": [],
                    "data": {
                        "status": "1",
                        "appId": "c27c76bb5b6d4e2a8660b17e2d6cffa0",
                        "helpUrl": f"https://{target_host}/web/agreement.html?tab=support"
                    }
                })
            # 浏览器或客户端 Webview 直接访问，302 跳转到标准客服地址
            if request.method == "GET" and not path.endswith((".js", ".css", ".png", ".jpg", ".svg", ".json")):
                return redirect("/web/agreement.html?tab=support", code=302)
            w_file = os.path.join(static_web_dir, "agreement.html")
            if os.path.isfile(w_file):
                with open(w_file, "r", encoding="utf-8") as f:
                    resp = Response(f.read(), status=200, mimetype="text/html; charset=utf-8")
                    resp.headers["Access-Control-Allow-Origin"] = "*"
                    return resp

        # 4. 官方协议/隐私政策/条款拦截（ys4fun-prod-pub.ys4fun.com / terms / privacy） -> 导向管理员特权协议
        if "privacy" in path or "terms" in path or "agreement" in path:
            if not path.endswith("agreementupdate/getLatest"):
                sub_tab = "terms" if "terms" in path else ("privacy" if "privacy" in path else "permission")
                if request.method == "GET" and not path.endswith((".js", ".css", ".png", ".jpg", ".svg", ".json")):
                    return redirect(f"/web/agreement.html?tab={sub_tab}", code=302)
                w_file = os.path.join(static_web_dir, "agreement.html")
                if os.path.isfile(w_file):
                    with open(w_file, "r", encoding="utf-8") as f:
                        resp = Response(f.read(), status=200, mimetype="text/html; charset=utf-8")
                        resp.headers["Access-Control-Allow-Origin"] = "*"
                        return resp

        # 5. GM 控制台后端 API：发送邮件 (/api/gm/send_mail)
        if path == "/api/gm/send_mail":
            from account_db import get_db
            db = get_db()
            data = request.get_json(silent=True) or {}
            target_uid = data.get("uid") or _gen.DEFAULT_UID
            title = data.get("title") or "【隐科组】战备物资补给"
            content = data.get("content") or "亲爱的管理员，物资已送达！"
            attachments = data.get("attachments") or []
            for it in attachments:
                aid = int(it.get("id") or it.get("item_id") or 0) if isinstance(it, dict) else (int(it[0]) if isinstance(it, (list, tuple)) and len(it) > 0 else 0)
                if (200000 <= aid <= 600000) or (830000 <= aid <= 859999):
                    return jsonify({"code": 10001, "msg": f"物品 ID {aid} 属于刻印或刻印套装类物品，仅供图鉴展示，禁止通过邮件发送"})
                if aid == 30054 or (1000000000 <= aid <= 2000000000):
                    return jsonify({"code": 10001, "msg": f"物品 ID {aid} 属于试衣底片类道具，未实现对应协议，仅供图鉴展示，禁止通过邮件发送"})
            if db:
                mid = db.send_gm_mail(target_uid, title, content, attachments)
                print(f"[GM] 成功向 UID {target_uid} 发送邮件 mid={mid}: {title} (附件={len(attachments)} 项)", flush=True)
                return jsonify({"code": 0, "msg": "success", "mail_id": mid})
            return jsonify({"code": 1, "msg": "db_not_ready"})

        # 6. GM 控制台后端 API：获取状态 (/api/gm/status) 与 切换抓包器 (/api/gm/toggle_capture)
        if path == "/api/gm/toggle_capture":
            mode = request.args.get("enabled")
            if mode is None:
                data = request.get_json(silent=True) or {}
                mode = data.get("enabled")
            if mode is not None:
                cdn_proxy.set_capture_enabled(str(mode).lower() in ("1", "true", "yes", "on"))
            else:
                cdn_proxy.set_capture_enabled(not cdn_proxy.is_capture_enabled())
            return jsonify({"code": 0, "msg": "success", "capture_enabled": cdn_proxy.is_capture_enabled()})

        if path == "/api/gm/status":
            import res_version_manager
            return jsonify({
                "code": 0,
                "data": {
                    "game_port": game_port,
                    "gw_port": gw_port,
                    "host_ip": lan_ip,
                    "capture_enabled": cdn_proxy.is_capture_enabled(),
                    "default_uid": _gen.DEFAULT_UID,
                    "server_time": int(time.time()),
                    "res_version": res_version_manager.get_current_version(),
                    "client_detection": res_version_manager.get_detected_client_info(),
                    "res_version_config": res_version_manager.get_version_config()
                }
            })

        # 6.01 GM 控制台后端 API：开放 PAY 开关管理 (/api/gm/system/pay_switch 与 /api/gm/pay_switch)
        if path in ("/api/gm/system/pay_switch", "/api/gm/pay_switch"):
            import res_version_manager
            curr_ver = res_version_manager.get_current_version()
            db = getattr(core, "db", None)
            if db is None:
                try:
                    from account_db import get_db
                    db = get_db()
                except Exception:
                    pass
            plat = get_latest_client_platform()
            is_eligible = (curr_ver == "229" and plat == "ios")
            if request.method == "POST":
                data = request.get_json(silent=True) or {}
                enabled = bool(data.get("enabled", False))
                if db:
                    db.set_system_config("ios_pay_enabled", "1" if enabled else "0")
                is_enabled = enabled
                msg = f"已{'开启' if enabled else '关闭'} iOS 229 PAY 功能"
                return jsonify({
                    "code": 0,
                    "msg": msg,
                    "data": {
                        "ios_pay_enabled": is_enabled,
                        "res_version": curr_ver,
                        "client_platform": plat,
                        "is_eligible": is_eligible
                    }
                }), 200
            else:
                is_enabled = (db.get_system_config("ios_pay_enabled", "0") == "1") if db else False
                return jsonify({
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "ios_pay_enabled": is_enabled,
                        "res_version": curr_ver,
                        "client_platform": plat,
                        "is_eligible": is_eligible
                    }
                }), 200

        # 6.0 GM 控制台后端 API：客户端资源版本管理与热切换 (/api/gm/system/res_version 与 /api/gm/res_version)
        if path in ("/api/gm/system/res_version", "/api/gm/res_version"):
            import res_version_manager
            if request.method == "POST":
                data = request.get_json(silent=True) or {}
                ver = data.get("version") or request.args.get("version")
                if not ver:
                    return jsonify({"code": 10001, "msg": "缺少 version 参数"}), 200
                ok, msg = res_version_manager.set_current_version(ver)
                if not ok:
                    return jsonify({"code": 10001, "msg": msg}), 200
                curr = res_version_manager.get_current_version()
                cfg = res_version_manager.get_version_config(curr)
                print(f"[GM] 动态切换客户端资源版本 -> {curr} ({cfg['display_name']})", flush=True)
                return jsonify({
                    "code": 0,
                    "msg": msg,
                    "data": {
                        "current_version": curr,
                        "version_name": cfg.get("version_name", ""),
                        "display_name": cfg.get("display_name", ""),
                        "asset_hash": cfg.get("assethash", {}).get("pc", ""),
                        "voice_list_file": cfg.get("voice_package_list", ""),
                        "client_detection": res_version_manager.get_detected_client_info(),
                        "version_config": cfg
                    }
                }), 200
            else:
                curr = res_version_manager.get_current_version()
                configs = res_version_manager.get_all_version_configs()
                curr_cfg = res_version_manager.get_version_config(curr)
                return jsonify({
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "current_version": curr,
                        "version_name": curr_cfg.get("version_name", ""),
                        "display_name": curr_cfg.get("display_name", ""),
                        "asset_hash": curr_cfg.get("assethash", {}).get("pc", ""),
                        "voice_list_file": curr_cfg.get("voice_package_list", ""),
                        "supported_versions": list(res_version_manager.SUPPORTED_VERSIONS),
                        "client_detection": res_version_manager.get_detected_client_info(),
                        "version_details": configs
                    }
                }), 200

        # 6.1 GM 控制台后端 API：切换关卡进度预设 (/api/gm/stage_preset)
        if path == "/api/gm/stage_preset":
            from stage_service import StageService
            from account_db import get_db
            db = get_db()
            mode = request.args.get("mode")
            if not mode:
                data = request.get_json(silent=True) or {}
                mode = data.get("mode") or "all_clear"
            target_uid = request.args.get("uid") or _gen.DEFAULT_UID
            svc = StageService.get_instance(db=db)
            ok, msg = svc.apply_stage_preset(int(target_uid), mode=mode)
            print(f"[GM] 切换关卡进度预设 UID={target_uid} mode={mode} 结果={ok} ({msg})", flush=True)
            return jsonify({"code": 0 if ok else 1, "msg": msg, "mode": mode, "uid": target_uid})

        # 6.2 GM 控制台后端 API：卡池管理与预设切换 (/api/gm/draw/*)
        if path.startswith("/api/gm/draw/"):
            from draw_service import DrawService, POOL_PRESETS
            from account_db import get_db
            db = get_db()
            svc = DrawService.get_instance(db=db)
            sub = path[len("/api/gm/draw/"):]

            if sub == "pools":
                # 获取全量/在架卡池列表与安全评级
                data = svc.list_all_pools()
                return jsonify({"code": 0, "msg": "success", "total": len(data), "data": data})

            if sub == "catalog":
                # 获取全量扩充/自选/限定卡池目录元数据
                cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "expansion_pools_catalog.json")
                active_set = set(svc.get_active_pools())
                catalog = []
                if os.path.isfile(cat_path):
                    with open(cat_path, "r", encoding="utf-8") as f:
                        raw_cat = json.load(f)
                        for item in raw_cat:
                            d = dict(item)
                            d["is_active"] = item["pool_id"] in active_set
                            d["is_selectable"] = svc.is_selectable_pool(item["pool_id"])
                            catalog.append(d)
                return jsonify({"code": 0, "msg": "success", "total": len(catalog), "data": catalog})

            if sub == "active":
                # 获取当前活跃在架卡池及其详情
                active_ids = svc.get_active_pools()
                cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "expansion_pools_catalog.json")
                catalog_map = {}
                if os.path.isfile(cat_path):
                    try:
                        with open(cat_path, "r", encoding="utf-8") as f:
                            for it in json.load(f):
                                catalog_map[it["pool_id"]] = it
                    except Exception:
                        pass
                active_details = []
                for pid in active_ids:
                    if pid in catalog_map:
                        it = dict(catalog_map[pid])
                        it["is_active"] = True
                        active_details.append(it)
                    else:
                        active_details.append({"pool_id": pid, "is_active": True, "pool_name": f"卡池 {pid}"})
                return jsonify({
                    "code": 0,
                    "msg": "success",
                    "total": len(active_ids),
                    "active_pools": active_ids,
                    "pools": active_details
                })

            if sub == "presets":
                # 获取可用卡池预设
                return jsonify({"code": 0, "msg": "success", "data": POOL_PRESETS})

            if sub == "set_active":
                # 动态设置当前在架卡池列表: POST/GET json or param
                body = request.get_json(silent=True) or {}
                pool_ids = body.get("pool_ids") or request.args.get("pool_ids")
                if isinstance(pool_ids, str):
                    pool_ids = [int(x.strip()) for x in pool_ids.split(",") if x.strip().isdigit()]
                if not pool_ids:
                    return jsonify({"code": 1, "msg": "pool_ids 不能为空"})
                ok, msg = svc.set_active_pools(pool_ids)
                print(f"[GM] 设置在架卡池 pool_ids={pool_ids} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "active_pools": svc.get_active_pools()})

            if sub in ("switch_preset", "apply_preset"):
                # 一键切换预设模板: POST/GET preset=xxx
                body = request.get_json(silent=True) or {}
                preset_key = body.get("preset_key") or body.get("preset") or request.args.get("preset") or "classic_safe"
                ok, msg = svc.apply_preset(preset_key)
                print(f"[GM] 切换卡池预设 preset={preset_key} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "preset": preset_key, "active_pools": svc.get_active_pools()})

            if sub == "set_pity":
                # 设置玩家抽卡保底: uid, pool_group, since_ssr, is_up_guaranteed
                body = request.get_json(silent=True) or {}
                target_uid = int(body.get("uid") or request.args.get("uid") or _gen.DEFAULT_UID)
                pool_group = body.get("pool_group") or request.args.get("pool_group") or "hero_precision_70"
                since_ssr = int(body.get("since_ssr") or request.args.get("since_ssr") or 0)
                is_up_guaranteed = body.get("is_up_guaranteed")
                if is_up_guaranteed is not None:
                    is_up_guaranteed = int(is_up_guaranteed)
                ok, msg = svc.set_pity(target_uid, pool_group, since_ssr, is_up_guaranteed)
                print(f"[GM] 调控保底 UID={target_uid} 系列={pool_group} since={since_ssr} 必UP={is_up_guaranteed} 结果={ok}", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "uid": target_uid, "state": svc.get_draw_state(target_uid, pool_group)})

            return jsonify({"code": 1, "msg": f"未知的抽卡 GM 接口: {path}"})

        # 6.3 GM 控制台后端 API：誓约系统管理与调试 (/api/gm/oath/*)
        if path.startswith("/api/gm/oath/"):
            from oath_service import OathService
            from account_db import get_db
            db = get_db()
            svc = OathService.get_instance()
            sub = path[len("/api/gm/oath/"):]
            body = request.get_json(silent=True) or {}
            target_uid = int(body.get("uid") or request.args.get("uid") or _gen.DEFAULT_UID)

            if sub == "status":
                data = svc.gm_get_status(db, target_uid)
                return jsonify({"code": 0, "msg": "success", "uid": target_uid, "data": data})

            if sub == "ready":
                hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
                ok, msg = svc.gm_ready_oath(db, target_uid, hid)
                print(f"[GM] 誓约就绪 UID={target_uid} hero={hid} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

            if sub == "unlock":
                hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
                ok, msg = svc.gm_unlock_oath(db, target_uid, hid)
                print(f"[GM] 誓约解锁 UID={target_uid} hero={hid} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

            if sub == "set_level":
                hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
                level = int(body.get("level") or request.args.get("level") or 1)
                ok, msg = svc.gm_set_oath_level(db, target_uid, hid, level)
                print(f"[GM] 誓约调级 UID={target_uid} hero={hid} level={level} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "uid": target_uid, "hero_id": hid, "level": level})

            if sub == "reset":
                hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
                ok, msg = svc.gm_reset_oath(db, target_uid, hid)
                print(f"[GM] 誓约重置 UID={target_uid} hero={hid} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

            if sub == "reset_tasks":
                hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
                ok, msg = svc.gm_reset_tasks(db, target_uid, hid)
                print(f"[GM] 誓约任务重置 UID={target_uid} hero={hid} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

            if sub == "give_rings":
                cnt = int(body.get("count") or request.args.get("count") or 10)
                ok, msg = svc.gm_give_rings(db, target_uid, count=cnt)
                print(f"[GM] 补发戒指 UID={target_uid} count={cnt} 结果={ok} ({msg})", flush=True)
                return jsonify({"code": 0 if ok else 1, "msg": msg, "uid": target_uid, "count": cnt})

            return jsonify({"code": 1, "msg": f"未知的誓约 GM 接口: {path}"})

        # 6.4 其余未被蓝图或历史逻辑捕获的 /api/gm/* 统一返回规范 JSON 404
        if path.startswith("/api/gm/"):
            return jsonify({"code": 404, "msg": f"未知的 GM 接口: {path}"}), 404

        # 7. 静态资源与语音包（高性能流式双向转发 + D:\iOS_DATA 落盘存储）
        static_res_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_resources")
        is_cdn_res = ("resources/" in path or "download" in host
                      or path.endswith((".bytes", ".ab", ".bundle", ".ys", ".patch"))
                      or (path.endswith(".json") and ("resources" in path or "hash" in path or "manifest" in path)))
        if is_cdn_res:
            fn = os.path.basename(path.split("?")[0])
            if fn and fn not in ("geetest.html", "notice_cfg.json", "sdk_config.json", "sdk_agreement.json"):
                upstream_host = host if ("download" in host) else "download-eo.ys4fun.com"
                return cdn_proxy.handle_resource_request(
                    path=path, host=upstream_host, query=qs, headers=dict(request.headers)
                )

        # 8. Geetest HTML 页面 (webstatic.ys4fun.com)
        if "geetest.html" in path:
            gt_file = os.path.join(static_res_dir, "geetest.html")
            if os.path.isfile(gt_file):
                with open(gt_file, "rb") as f:
                    data = f.read()
                resp = Response(data, status=200, mimetype="text/html; charset=utf-8")
                resp.headers["Access-Control-Allow-Origin"] = "*"
                return resp

        # 9. ThinkingAnalytics 埋点拦截 (ta.ys4fun.com)
        if "ta.ys4fun" in host or "sync_json" in path or "sync_data" in path:
            return jsonify({"code": 0, "msg": "success"})

        # 10. Packaging 安装包拦截 (packaging.ys4fun.com)
        if "packaging" in host:
            return jsonify({"errorCode": "0", "data": {}})

        # 11. OSS 上传拦截（PUT / dorm_cover / mock_oss / skzy-dorm）
        if request.method == "PUT" or "dorm_cover" in path or "mock_oss" in path or "skzy-dorm" in path:
            target_host = host if host else f"{lan_ip}:443"
            resp = Response(f'{{"code":1,"uploadUrl":"https://{target_host}/dorm_cover/snap.png","url":"https://{target_host}/dorm_cover/snap.png"}}', status=200, mimetype="application/json")
            resp.headers["ETag"] = '"d41d8cd98f00b204e9800998ecf8427e"'
            resp.headers["x-oss-request-id"] = "60B9E2B20000000000000001"
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

        # 12. OSS STS Token & AppUploadCfg（宿舍拍照/生成封面/分享图片上传）
        if "getAppUploadCfg" in path:
            return jsonify({
                "errorCode": "0",
                "data": {
                    "bucket": "skzy", "bucketName": "skzy",
                    "endpoint": "ys4fun.com", "domain": "https://skzy.ys4fun.com",
                    "path": "dorm_cover/", "prefix": "dorm_cover/",
                    "isCname": False, "cname": False
                }
            })

        if "getStsUploadToken" in path:
            return jsonify({
                "errorCode": "0",
                "data": {
                    "accessKeyId": "STS.mock_key_id", "accessKeySecret": "mock_secret",
                    "securityToken": "mock_token", "expiration": "2036-01-01T00:00:00Z",
                    "bucket": "skzy", "bucketName": "skzy",
                    "endpoint": "ys4fun.com", "domain": "https://skzy.ys4fun.com",
                    "path": "dorm_cover/", "prefix": "dorm_cover/",
                    "isCname": False, "cname": False
                }
            })

        # 13. 区服与客户端基础配置接口：gateway/get (action=base 或 action=server)
        if "gateway/get" in path or path.endswith("/gateway/get") or path.endswith("/gateway/get/"):
            action = request.args.get("action", "")
            if action == "base" or "action=base" in qs:
                return jsonify({
                    "errorCode": "0",
                    "data": {
                        "env": "prod",
                        "config": [
                            {"key": "FORUM_URL_HOME", "value": "https://m.bbs.ys4fun.com/"},
                            {"key": "USE_SDK_HOT_FIX", "value": "true"},
                            {"key": "PC_FEEDBACK", "value": "https://open.ys4fun.com/web/agreement.html?tab=support"},
                            {"key": "PC_SHOP", "value": "https://skzy.ys4fun.com/main/ipay/?uidByGame=%s_%s&gameAppId=%s&gameToken=%s"},
                            {"key": "FORUM_URL", "value": "https://m.bbs.ys4fun.com/gameLogin/?uidByGame=%s_%s&gameAppId=%s&gameToken=%s"},
                            {"key": "OFFICIAL_URL", "value": "https://skzy.ys4fun.com/m/"},
                            {"key": "REST_URL", "value": "https://prod-api-activity.ys4fun.com/skzy-activity"}
                        ]
                    }
                })

            # 默认返回区服列表 (action=server)
            mix_id = request.args.get("mixId", str(_gen.DEFAULT_UID))
            gw_ip = "127.0.0.1" if request.remote_addr in ("127.0.0.1", "::1") else lan_ip

            # 读取动态区服配置（支持自定义服名与多区服管理）
            zones_cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "server_zones.json")
            zones_list = []
            if os.path.isfile(zones_cfg_path):
                try:
                    with open(zones_cfg_path, "r", encoding="utf-8") as f:
                        z_data = json.load(f)
                        zones_list = z_data.get("zones", [])
                except Exception:
                    zones_list = []

            if not zones_list:
                zones_list = [
                    {"serverId": "1", "serverName": "艾因索菲", "env": "prod", "newServerFlag": 1, "maintain": False, "maintainReason": ""},
                    {"serverId": "2", "serverName": "蒂卡拉", "env": "prod", "newServerFlag": 0, "maintain": False, "maintainReason": ""},
                ]

            server_data = []
            for z in zones_list:
                s_id = str(z.get("serverId", "1"))
                s_name = str(z.get("serverName") or ("艾因索菲" if s_id == "1" else "蒂卡拉"))
                s_env = str(z.get("env") or "prod")
                s_new = int(z.get("newServerFlag", 1 if s_id == "1" else 0))
                s_maint = bool(z.get("maintain", False))
                s_reason = str(z.get("maintainReason", ""))
                server_data.append({
                    "serverId": s_id,
                    "serverName": s_name,
                    "env": s_env,
                    "ip": gw_ip,
                    "port": gw_port,
                    "newServerFlag": s_new,
                    "maintain": s_maint,
                    "maintainReason": s_reason,
                    "config": [
                        {"key": "OFFICIAL_URL", "value": "https://skzy.ys4fun.com/m/"},
                        {"key": "USE_SDK_HOT_FIX", "value": "true"},
                        {"key": "REST_URL", "value": "https://prod-api-activity.ys4fun.com/skzy-activity"},
                        {"key": "PC_FEEDBACK", "value": "https://open.ys4fun.com/web/agreement.html?tab=support"}
                    ],
                    "gameUserInfoList": [
                        {
                            "uid": str(_gen.DEFAULT_UID), "currentLevel": 93,
                            "nickName": "Admin", "lastLoginTime": "2026-08-28 12:00:00",
                            "mixId": str(mix_id or _gen.DEFAULT_UID)
                        }
                    ]
                })

            return jsonify({
                "errorCode": "0",
                "data": server_data
            })


        # 4.1 证书一键下载（供 iOS / Android 设备 Safari 访问 http://<HOST_IP>/cert 一键安装根证书）
        if path in ("/cert", "/ca.crt", "/sdk_ca.crt", "/sdk_cert.pem", "/cert.crt"):
            ca_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_ca.crt")
            if not os.path.isfile(ca_p):
                ca_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_cert.pem")
            if os.path.isfile(ca_p):
                with open(ca_p, "rb") as f:
                    cdata = f.read()
                resp = Response(cdata, status=200, mimetype="application/x-x509-ca-cert")
                resp.headers["Content-Disposition"] = "attachment; filename=AetherGazer_CA.crt"
                resp.headers["Access-Control-Allow-Origin"] = "*"
                return resp

        # 4.2 iPhone 描述文件一键下载（供 iOS 设备 Safari 访问 http://<HOST_IP>/mobileconfig 直接安装配置描述文件）
        if path in ("/mobileconfig", "/cert/mobileconfig", "/ca.mobileconfig", "/AetherGazer.mobileconfig", "/sdk_ca.mobileconfig"):
            mc_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AetherGazer.mobileconfig")
            if not os.path.isfile(mc_p):
                mc_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_ca.mobileconfig")
            if os.path.isfile(mc_p):
                with open(mc_p, "rb") as f:
                    mdata = f.read()
                resp = Response(mdata, status=200, mimetype="application/x-apple-aspen-config")
                resp.headers["Content-Disposition"] = "attachment; filename=AetherGazer.mobileconfig"
                resp.headers["Access-Control-Allow-Origin"] = "*"
                return resp

        # 5. Token 快速登录（客户端携带本地缓存 Token，秒进游戏无等待）
        if path.endswith("/pass/user/loginByToken"):
            req_token = request.form.get("token") or request.args.get("token") or ""
            uinfo = _verify_token(req_token)
            return jsonify({
                "errorCode": "0",
                "data": {
                    "userId": 1012030496969579190,
                    "regionNo": "1",
                    "phone": "13800000000",
                    "nickName": uinfo["username"],
                    "birth": -225014400000,
                    "gender": "male",
                    "token": uinfo["token"],
                    "valid": False,
                    "locked": False,
                    "newMsgCount": 0,
                    "totalRecharge": 0,
                    "totalRechargeDis": "0.00",
                    "realNameValid": True,
                    "realNameValidResult": "succ",
                    "adult": True,
                    "realNameUpdateLimit": 0,
                    "guest": False,
                    "privList": [],
                    "privPermList": [],
                    "inviteLimit": False,
                    "age": 25,
                    "registerFlag": False,
                    "birthday": "19990101",
                    "securityValid": False,
                    "loginType": "token"
                }
            })

        # 6. 账密登录（/sdk-api/pass/user/login 接受任意账密直接通过）
        if path.endswith("/sdk-api/pass/user/login") or (path.endswith("/pass/user/login") and "mix-sdk-api" not in path):
            username = request.form.get("username") or request.args.get("username") or "Admin"
            password = request.form.get("password") or request.args.get("password") or ""
            uinfo = _verify_and_create_user(username, password)
            return jsonify({
                "errorCode": "0",
                "data": {
                    "userId": 1012030496969579190,
                    "regionNo": "1",
                    "phone": str(username),
                    "nickName": str(username),
                    "birth": -225014400000,
                    "gender": "male",
                    "token": uinfo["token"],
                    "valid": False,
                    "locked": False,
                    "newMsgCount": 0,
                    "totalRecharge": 0,
                    "totalRechargeDis": "0.00",
                    "realNameValid": True,
                    "realNameValidResult": "succ",
                    "adult": True,
                    "realNameUpdateLimit": 0,
                    "guest": False,
                    "privList": [],
                    "privPermList": [],
                    "inviteLimit": False,
                    "age": 25,
                    "registerFlag": False,
                    "birthday": "19990101",
                    "securityValid": False,
                    "loginType": "username"
                }
            })

        # 7. 发送短信验证码（/sdk-api/pass/sms/send 直接返回成功放行倒计时）
        if path.endswith("/pass/sms/send"):
            phone = request.form.get("phone") or request.args.get("phone") or ""
            print(f"[SMS] 发送验证码到手机: {phone} (免验放行)", flush=True)
            return jsonify({"errorCode": "0", "data": {}})

        # 8. 短信验证码登录（/sdk-api/pass/user/loginBySms 接受任意手机号与验证码直接通过）
        if path.endswith("/pass/user/loginBySms") or path.endswith("/pass/user/registerBySms"):
            phone = request.form.get("phone") or request.args.get("phone") or "13800000000"
            auth_code = request.form.get("authCode") or request.args.get("authCode") or ""
            nick = phone
            if len(phone) >= 7:
                nick = phone[:3] + "****" + phone[-4:]
            uinfo = _verify_and_create_user(nick)
            print(f"[SMS] 手机短信登录: phone={phone}, code={auth_code} -> 登录成功", flush=True)
            return jsonify({
                "errorCode": "0",
                "data": {
                    "userId": 1012030496969579190,
                    "regionNo": "86",
                    "phone": str(phone),
                    "nickName": nick,
                    "birth": -225014400000,
                    "gender": "male",
                    "token": uinfo["token"],
                    "valid": False,
                    "locked": False,
                    "newMsgCount": 0,
                    "totalRecharge": 0,
                    "totalRechargeDis": "0.00",
                    "realNameValid": True,
                    "realNameValidResult": "succ",
                    "adult": True,
                    "realNameUpdateLimit": 0,
                    "guest": False,
                    "privList": [],
                    "privPermList": [],
                    "inviteLimit": False,
                    "age": 25,
                    "registerFlag": False,
                    "birthday": "19990101",
                    "securityValid": False,
                    "loginType": "phone"
                }
            })

        # 9. 手机号存在性检查与密码重置
        if path.endswith("/pass/user/isPhoneExist"):
            return jsonify({"errorCode": "0", "data": {"exist": True}})

        if path.endswith("/pass/user/resetPassword"):
            return jsonify({"errorCode": "0", "data": {}})

        def _check_is_pay_allowed():
            ua_low = request.headers.get("User-Agent", "").lower()
            if "ios" in ua_low or "iphone" in ua_low or "ipad" in ua_low or "darwin" in ua_low:
                record_client_platform("ios")
            elif "windows" in ua_low or "pc" in ua_low:
                record_client_platform("pc")
            elif "android" in ua_low:
                record_client_platform("android")
            import res_version_manager
            curr_ver = res_version_manager.get_current_version()
            plat = get_latest_client_platform()
            db = getattr(core, "db", None)
            if db is None:
                try:
                    from account_db import get_db
                    db = get_db()
                except Exception:
                    pass
            pay_on = (db.get_system_config("ios_pay_enabled", "0") == "1") if db else False
            return bool(curr_ver == "229" and plat == "ios" and pay_on)

        # 7. Mix SDK 登录（/mix-sdk-api/pass/user/login）
        if "mix-sdk-api" in path and path.endswith("/pass/user/login"):
            ua_low = request.headers.get("User-Agent", "").lower()
            if "ios" in ua_low or "iphone" in ua_low or "ipad" in ua_low or "darwin" in ua_low:
                record_client_platform("ios")
            channel_user_id = "1012030496969579190"
            token = f"{uuid.uuid4().hex[:32]}.{_gen.DEFAULT_UID}"
            is_pay_allowed = _check_is_pay_allowed()
            return jsonify({
                "errorCode": "0",
                "data": {
                    "id": _gen.DEFAULT_UID,
                    "createDate": 1760276673000,
                    "modifyDate": int(time.time() * 1000),
                    "gameId": 3,
                    "channelId": 9,
                    "channelUserId": channel_user_id,
                    "token": token,
                    "expireDate": int((time.time() + 86400 * 365) * 1000),
                    "enableLogin": True,
                    "enablePay": is_pay_allowed,
                    "enableNewRole": False,
                    "totalRecharge": 0,
                    "registerFlag": False,
                    "inviteLimit": False,
                    "targetChannelId": 7
                }
            })

        # 8. captcha 极验离线降级
        if path.endswith("/pass/captcha/init"):
            return jsonify({
                "errorCode": "0",
                "data": {
                    "providerType": "geetest_v3", "success": True,
                    "clientInitParam": {
                        "success": False, "captchaId": "e2ad319e27f7359a23322515c536e643",
                        "challenge": "", "newFailback": True,
                        "geetestUserId": "6b262a7fbdbee8b3134a1355d52c864e", "offline": True
                    }
                }
            })

        # 9. SDK 配置（客户端初始化必需）
        if path == "/config" or path.startswith("/config?"):
            ua_low = request.headers.get("User-Agent", "").lower()
            plat_req = request.args.get("platformType", "").lower()
            is_ios_client = "ios" in ua_low or "iphone" in ua_low or "ipad" in ua_low or "darwin" in ua_low or plat_req == "ios"
            plat_type = "ios" if is_ios_client else ("android" if ("android" in ua_low or plat_req == "android") else "pc")
            record_client_platform(plat_type)
            channel_id = 7 if plat_type == "ios" else (8 if plat_type == "android" else 9)
            return jsonify({
                "errorCode": "0",
                "data": {
                    "platformType": plat_type, "gameId": 3, "channelId": channel_id,
                    "appId": "bd2fc85f8ecd35fd13deec46560bb36a",
                    "appSecret": "6b262a7fbdbee8b3134a1355d52c864e",
                    "env": "prod", "logLevel": 3, "debug": False
                }
            })

        # 10. SDK 游戏协议与隐私链接（导向一体化随身安全与客服中枢）
        if path.endswith("/pass/gamecfg/getInfo"):
            target_host = host if (host and not host.startswith("127.") and not host.startswith("localhost")) else f"{lan_ip}"
            return jsonify({
                "errorCode": "0",
                "data": {
                    "privacyUrl": f"https://{target_host}/web/agreement.html?tab=privacy",
                    "infoCollectionListUrl": f"https://{target_host}/web/agreement.html?tab=permission",
                    "thirdSdkPrivacyUrl": f"https://{target_host}/web/agreement.html?tab=permission",
                    "userAgreementUrl": f"https://{target_host}/web/agreement.html?tab=terms",
                    "childrenPrivacyUrl": f"https://{target_host}/web/agreement.html?tab=privacy",
                    "helpUrl": f"https://{target_host}/web/agreement.html?tab=support",
                    "regist": {"pc": False}
                }
            })

        # 11. 协议更新与公告（优先读取官方完整结构，杜绝字段不匹配）
        if path.endswith("/pass/game/agreementupdate/getLatest"):
            agr_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_agreement.json")
            if os.path.isfile(agr_p):
                try:
                    with open(agr_p, "r", encoding="utf-8") as f:
                        return Response(f.read(), status=200, mimetype="application/json")
                except Exception:
                    pass
            return jsonify({
                "errorCode": "0",
                "data": {
                    "id": 3, "createDate": 1712783341000, "modifyDate": int(time.time() * 1000),
                    "gameId": 3, "title": "更新公告",
                    "content": "欢迎来到深空之眼自主节点！",
                    "startDate": 1712786400000, "status": "on", "sortWeight": 0
                }
            })

        # 12. 年龄提示（动态读取 notice_cfg.json）
        if path.endswith("/pass/game/channelext/getAgeTip"):
            ncfg = _load_notice_cfg()
            tip_text = ncfg.get("age_tip", "【深空之眼 · 隐科组特别提示】\n1. 本节点为管理员专享高维模拟世界，已解除体力限制与算力封锁。\n2. 适度抽卡益脑，沉迷游戏伤身；请管理员合理安排战斗时间，与修正者们携手守护盖亚世界！\n3. 祝管理员在此次同调观测中取得辉煌战果！")
            _logger_log("资产 [notice_cfg.json] 传递完毕 (200 OK)", "INFO", module="CDN")
            return jsonify({"errorCode": "0", "data": tip_text})

        # 12.1 登录公告（保持官方静默，杜绝 iOS 原生 WKWebView 空指针崩溃）
        if path.endswith("/pass/game/notice/getCurrent"):
            return jsonify({"errorCode": "0"})

        # 12.0 SDK 机型与上报配置（必须返回完整的 iOS 机型映射字典和布尔值，杜绝 Objective-C 解析崩溃）
        if path.endswith("/pass/game/sdkConfig"):
            cfg_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_config.json")
            if os.path.isfile(cfg_p):
                try:
                    with open(cfg_p, "r", encoding="utf-8") as f:
                        data_content = f.read()
                        _logger_log(f"资产 [sdk_config.json] 传递完毕 (200 OK, {len(data_content)}B)", "INFO", module="CDN")
                        return Response(data_content, status=200, mimetype="application/json")
                except Exception:
                    pass
            _logger_log("资产 [sdk_config.json] 动态配置传递完毕 (200 OK)", "INFO", module="CDN")
            return jsonify({
                "errorCode": "0",
                "data": {
                    "adCodeReportEnable": True,
                    "iosModelMap": {}
                }
            })

        # 12.1 移动端区号与地区列表
        if path.endswith("/pass/region/list"):
            return jsonify({
                "errorCode": "0",
                "data": [
                    {"id": 1, "regionNo": "86", "nameZhCn": "中国大陆", "sortWeight": 100},
                    {"id": 2, "regionNo": "852", "nameZhCn": "中国香港", "sortWeight": 90},
                    {"id": 3, "regionNo": "853", "nameZhCn": "中国澳门", "sortWeight": 80},
                    {"id": 4, "regionNo": "886", "nameZhCn": "中国台湾", "sortWeight": 70}
                ]
            })

        # 12.2 移动端全局系统配置
        if path.endswith("/pass/syscfg/getGlobalCfgInfo"):
            is_pay_allowed = _check_is_pay_allowed()
            return jsonify({
                "errorCode": "0",
                "data": {
                    "openRegister": True,
                    "openLogin": True,
                    "openPay": is_pay_allowed,
                    "guestLogin": False,
                    "phoneLogin": True,
                    "quickLogin": True
                }
            })

        # 13. 防沉迷配置（成年人无限制）
        if path.endswith("/pass/syscfg/getIndulgeLimitCfgInfo"):
            return jsonify({
                "errorCode": "0",
                "data": {
                    "realNameStatus": "on", "realNameDefResult": "succ",
                    "realNameTip": "已完成实名认证",
                    "indulgeTimeStatus": "off", "indulgeRechargeStatus": "off",
                    "enableGameEvent": True
                }
            })

        # 14. 资源版本更新检查（根据客户端平台定向分发：PC / iOS / Android）
        if path.endswith("/pass/game/updateversion/getLatest"):
            req_json = request.get_json(silent=True) or {}
            plat = (
                request.args.get("platformType")
                or request.form.get("platformType")
                or req_json.get("platformType")
                or ""
            ).lower()
            chid = str(request.args.get("channelId") or request.form.get("channelId") or req_json.get("channelId") or "")
            ua = request.headers.get("User-Agent", "").lower()

            is_ios = plat == "ios" or chid in ("7", "ios") or "iphone" in ua or "ios" in ua or "darwin" in ua
            is_android = plat == "android" or "android" in ua
            cur_plat = "ios" if is_ios else ("android" if is_android else "pc")
            record_client_platform(cur_plat)

            # 统一根据 res_version_manager 动态分发所选版本（229 或 311）之资源清单配置
            import res_version_manager
            ver_cfg = res_version_manager.get_version_config()
            cur_ver = ver_cfg["version"]
            cur_ver_name = ver_cfg["version_name"]
            chosen_assethash = res_version_manager.get_assethash(platform=cur_plat)

            if is_ios or is_android:
                print(f"[SDK] 移动端 ({cur_plat}) 版本检查响应 -> 下发配置 (version={cur_ver}, assethash={chosen_assethash})", flush=True)
                return jsonify({
                    "errorCode": "0",
                    "data": [
                        {
                            "createDate": int(time.time() * 1000),
                            "modifyDate": int(time.time() * 1000),
                            "sortWeight": 0,
                            "type": "noInstall",
                            "version": cur_ver,
                            "versionName": cur_ver_name,
                            "matchedAppVersion": "307",
                            "forceUpdate": False,
                            "fileSize": 0,
                            "disDate": int(time.time() * 1000),
                            "downloadUrl": f"https://download-eo.ys4fun.com/{cur_plat}/resources/;https://download.ys4fun.com/{cur_plat}/resources/",
                            "extraData": json.dumps({"assethash": chosen_assethash}),
                            "platformType": cur_plat,
                            "enableWhiteList": False,
                            "enableFullPub": True,
                            "enableGrayPub": True,
                            "assethash": chosen_assethash,
                            "batchId": 0
                        },
                        {
                            "createDate": int(time.time() * 1000),
                            "modifyDate": int(time.time() * 1000),
                            "sortWeight": 0,
                            "type": "install",
                            "version": "307",
                            "versionName": "307",
                            "forceUpdate": False,
                            "fileSize": 0,
                            "disDate": int(time.time() * 1000),
                            "downloadUrl": f"https://download-eo.ys4fun.com/{cur_plat}/resources/",
                            "extraData": "{\"videoMd5\":\"80ed3ea7f7a1c5314b86c33a0378f65c\",\"videoSize\":\"83636032\",\"videoName\":\"5.2_PV\",\"videoUrl\":\"https://download.ys4fun.com/video/5.2_PV.usm\"}",
                            "platformType": cur_plat,
                            "enableWhiteList": False,
                            "enableFullPub": True,
                            "enableGrayPub": False,
                            "batchId": 0
                        }
                    ]
                })

            # PC 平台响应
            print(f"[SDK] PC 平台版本检查响应 -> 下发配置 (version={cur_ver}, assethash={chosen_assethash})", flush=True)
            return jsonify({
                "errorCode": "0",
                "data": [
                    {
                        "createDate": int(time.time() * 1000), "modifyDate": int(time.time() * 1000),
                        "sortWeight": 0, "type": "noInstall", "version": cur_ver,
                        "versionName": cur_ver_name, "matchedAppVersion": "307",
                        "forceUpdate": False, "fileSize": 0, "disDate": int(time.time() * 1000),
                        "downloadUrl": "https://download-eo.ys4fun.com/pc/resources/;https://download.ys4fun.com/pc/resources/",
                        "extraData": json.dumps({"assethash": chosen_assethash}),
                        "platformType": "pc", "enableWhiteList": False,
                        "enableFullPub": True, "enableGrayPub": True,
                        "assethash": chosen_assethash,
                        "batchId": 0
                    },
                    {
                        "createDate": 1785986740964, "modifyDate": 1785986740964,
                        "sortWeight": 0, "type": "install", "version": "307",
                        "versionName": "307", "forceUpdate": True, "fileSize": 0,
                        "disDate": 1751385600000,
                        "downloadUrl": "https://packaging.ys4fun.com/packagepc/prod/307/AetherGazer_zh_cn_ali_prod_307_210_3_windows_limit_sdk_release_W12000000_5FF9A0158EA7C050D03D419F16DB8625.zip",
                        "extraData": "{\"videoMd5\":\"80ed3ea7f7a1c5314b86c33a0378f65c\",\"videoSize\":\"83636032\",\"videoName\":\"5.2_PV\",\"videoUrl\":\"https://download.ys4fun.com/video/5.2_PV.usm\"}",
                        "platformType": "pc", "enableWhiteList": False,
                        "enableFullPub": True, "enableGrayPub": False, "batchId": 8
                    }
                ]
            })

        # 15. SDK 服务器时间
        if path.endswith("/pass/sys/time"):
            return jsonify({"errorCode": "0", "data": int(time.time() * 1000)})

        # 16. 可选素材回放匹配（若传入 material 且有命中，保留作为备用）
        if idx:
            cands = idx.get((host, path)) or idx.get(("*", path)) or []
            if cands:
                ent = None
                for e in cands:
                    if e.get("query") == qs:
                        ent = e
                        break
                if ent is None:
                    for e in cands:
                        if e.get("query", "").split("&")[0] == qs.split("&")[0]:
                            ent = e
                            break
                if ent is None:
                    ent = cands[0]
                resp = ent.get("response") or ""
                ctype = ent.get("ctype") or "application/json"
                return Response(resp, status=ent.get("status", 200), mimetype=ctype)

        # 17. 其余未匹配（上报/日志/埋点等）→ 统一返回成功
        return jsonify({"errorCode": "0", "data": {}})

    @app.errorhandler(Exception)
    def _handle_exception(e):
        import traceback
        tb = traceback.format_exc()
        print(f"[HTTPS EXCEPTION] path={request.path} host={request.host} err={e}\n{tb}", flush=True)
        if request.path.startswith("/api/gm/"):
            return jsonify({"code": 50000, "msg": f"服务器内部异常: {e}", "data": None}), 200
        return jsonify({"errorCode": "0", "data": {}, "errorMsg": str(e)}), 200

    return app


def start_https(app, port, cert_file=None, key_file=None):
    """启动 Flask HTTPS（SDK 层）。端口 443 需管理员。
    证书：默认用 v5 目录的 sdk_cert.pem/sdk_key.pem（客户端需信任该证书）。"""
    cert_file = cert_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_cert.pem")
    key_file = key_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk_key.pem")
    if not (os.path.exists(cert_file) and os.path.exists(key_file)):
        raise FileNotFoundError(f"证书缺失: {cert_file} / {key_file}")
    ssl_ctx = (cert_file, key_file)

    from werkzeug.serving import make_server, WSGIRequestHandler
    import threading

    class _FilteredWSGIRequestHandler(WSGIRequestHandler):
        def setup(self):
            super().setup()
            try:
                # 开启 TCP_NODELAY，禁用 Nagle 算法，消除对 iOS 客户端 40ms 延迟确认死锁
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # 扩充发送缓冲区至 2MB，满足局域网 Wi-Fi 下的高带宽时延积 (BDP)
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
            except Exception:
                pass

        def log_request(self, code='-', size='-'):
            # 屏蔽 GM 控制面板内部轮询与静态静态资源请求，避免高频日志污染监控流
            if hasattr(self, 'path') and self.path and (
                self.path.startswith("/api/")
                or self.path.startswith("/favicon.ico")
                or self.path.startswith("/web/")
            ):
                return
            super().log_request(code, size)

    # 同时在后台启动 HTTP:80 监听，专供 C# OSS SDK 免证书免 DNS 极速直传
    def _start_http():
        try:
            srv_http = make_server("0.0.0.0", 80, app, request_handler=_FilteredWSGIRequestHandler, threaded=True)
            try:
                srv_http.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
            except Exception:
                pass
            srv_http.serve_forever()
        except Exception as e:
            print(f"[V5] HTTP:80 监听跳过: {e}")

    threading.Thread(target=_start_http, daemon=True).start()

    try:
        srv_https = make_server("0.0.0.0", port, app, request_handler=_FilteredWSGIRequestHandler, ssl_context=ssl_ctx, threaded=True)
        try:
            srv_https.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        except Exception:
            pass
        srv_https.serve_forever()
    except Exception as e:
        print(f"[V5] HTTPS:{port} 异常: {e}")


def start_game_server(port, core, db, generator, codec_encode, codec_decode,
                      replay=None, material=None, frame_delay=0.001):
    """启动 game TCP 服务器（main 装配用）。返回 server 对象（serve_forever 前）。"""
    srv = ThreadedServer(("0.0.0.0", port), GameHandler)
    srv.core = core
    srv.db = db
    srv.generator = generator
    srv.codec_encode = codec_encode
    srv.codec_decode = codec_decode
    srv.replay = replay
    srv.material = material
    srv.frame_delay = frame_delay
    srv.game_port = port

    # 启动体力高精度主动定时器后台守护线程
    try:
        import fatigue_service as _fs
        _fs.start_fatigue_ticker()
    except Exception as _e:
        print(f"[V5] 体力主动定时器启动异常: {_e}")

    return srv


if __name__ == "__main__":
    # 自检：模块加载 + 依赖存在
    print("server_net 模块加载 OK")
    print("  transport:", pack_down, unpack_up, FrameParser)
    print("  core:", core_mod.CoreRequest, core_mod.Connection)
    print("  generator:", _gen.DEFAULT_UID)
