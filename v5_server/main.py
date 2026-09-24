# -*- coding: utf-8 -*-
"""
main.py — V5 服务器入口（模块装配）

职责（老大定稿）：
- MAIN 按启动参数装配模块；未激活模块不运行不干扰
- --replay：激活重放模块（登录洪流走素材流）
- 组装 CORE（core.dispatch）+ server_net（网络层）+ 数据库

架构（V5架构总览.md）：
  server_net ──→ core ──→ (generator / codec / account_db / skeleton)
  replay（旁路，--replay 激活才挂载）

启动：
  python main.py                    # 纯 CORE 模式（无重放）
  python main.py --replay <素材目录>  # 重放模式（登录洪流素材流）
  python main.py --self-check       # 自检退出
"""
import argparse
import os
import struct
import sys
import threading
import time

# 强制 UTF-8 编码支持（标准输入输出与 Windows 控制台代码页 65001）
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

# V5 自包含目录：全部模块（core/operations/generator/codec/account_db/
# server_net/login/replay/transport 等）与本文件同目录，直接 import，无需 sys.path 注入。
# schema 工具（decode_schema）与素材目录仍在仓库其他位置，由各模块自行以根目录相对路径引用。

import core as core_mod           # noqa: E402  CORE（响应调度+操作调度）
import operations                 # noqa: E402  操作注册（类版优先，配置版兜底）
import backhome_service           # noqa: E402  游园街领域服务（BackHome / Dorm / Canteen 事件总线挂载）
import peripheral_service         # noqa: E402  外围系统模块（32xxx 个性化/大厅场景/偏好，自注册操作 + 事件总线）
import achievement_service        # noqa: E402  成就领域服务（53xxx 领奖/物语，自注册操作 + 事件总线）
import minigame_service           # noqa: E402  常驻小游戏服务中枢（89xxx 弹珠/牛仔/坦克等，自注册操作 + 结算落库）
import autochess_service          # noqa: E402  决斗王自走棋服务（89xxx/90xxx PVE对弈状态机 + PVP拦截）
import periodic_gift_service       # noqa: E402  周期连续时间礼包服务（7日/14日/季卡 订阅管理+邮件下发）
import recharge_service            # noqa: E402  充值发货与累计充值服务（34xxx 直购/首充/累充积分/档位领奖）
import generator as _gen          # noqa: E402  响应生成

from codec import encode as _enc, decode as _dec  # noqa: E402
from account_db import get_db     # noqa: E402  数据层

# 网络层（V5 server_net 模块）与重放模块（--replay 激活才加载）
import server_net                 # noqa: E402
_replay = None


def _load_replay():
    global _replay
    if _replay is None:
        import replay
        _replay = replay
    return _replay


# ---------------- MAIN 装配 ----------------

def build_core(db=None, generator=None, codec_encode=None, codec_decode=None, log=None):
    """组装 CORE 单例（main 装配用）。"""
    core = core_mod.Core(db=db, generator=generator,
                         codec_encode=codec_encode, codec_decode=codec_decode,
                         log=log or (lambda *a, **k: None))
    core_mod.set_core(core)
    return core


def main(argv=None):
    ap = argparse.ArgumentParser(description="V5 模块化服务器（MAIN 装配）")
    ap.add_argument("--host-ip", default="", help="宿主机局域网 IP（默认自动检测真实 LAN IPv4）")
    ap.add_argument("--https-port", type=int, default=443, help="HTTPS SDK/区服端口（443 需管理员）")
    ap.add_argument("--gw-port", type=int, default=8102, help="gateway TCP 端口")
    ap.add_argument("--game-port", type=int, default=8105, help="game TCP 端口")
    ap.add_argument("--capture-cdn", action="store_true", default=True, help="开启官方 CDN 资源智能穿透与抓包（默认开启）")
    ap.add_argument("--no-capture-cdn", action="store_true", help="强制禁用官方 CDN 抓包（纯离线模式）")
    ap.add_argument("--dns", action="store_true", help="启动内置轻量 UDP:53 DNS 服务器（需管理员权限）")
    ap.add_argument("--replay", default="", metavar="素材目录",
                    help="激活重放模块并指定素材目录（如 archive/20260805_全流程_服务器2_蒂卡拉/complete_replay）")
    ap.add_argument("--frame-delay", type=float, default=0.001, help="重放帧间延迟")
    ap.add_argument("--frame-override", default="", help="覆盖帧 bin（改数据）")
    ap.add_argument("--battle-port", type=int, default=6105,
                    help="UDP 动态战斗服端口（0=禁用；sc_54007 下发给客户端）")
    ap.add_argument("--db", default=os.environ.get("ACCOUNT_DB_PATH", "account.db"),
                    help="指定挂载的数据库文件路径或名称 (默认: account.db)")
    ap.add_argument("--res-version", choices=["auto", "229", "311"], default="auto",
                    help="指定客户端分发资源版本 (默认: auto 优先自动对齐客户端目录版本, 可选: 229, 311)")
    ap.add_argument("--client-assets-dir", default="",
                    help="指定客户端 StreamingAssets 资源目录（用于自动版本探测，留空使用默认官方路径）")
    ap.add_argument("--log-level", choices=["DEBUG", "INFO", "WARN", "ERROR"],
                    default=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    help="设置服务端日志打印级别 (默认: INFO, 可选: DEBUG, INFO, WARN, ERROR)")
    ap.add_argument("--self-check", action="store_true", help="自检并退出")
    args = ap.parse_args(argv)

    import logger as _logger
    _logger.set_level(args.log_level)

    v5_dir = os.path.dirname(os.path.abspath(__file__))

    # 确定权威数据库挂载路径（默认使用 account.db 生产库）
    target_db = args.db
    if not os.path.isabs(target_db):
        cand = os.path.join(v5_dir, target_db)
        if os.path.exists(cand) or not os.path.exists(target_db):
            target_db = cand
    target_db = os.path.abspath(target_db)
    os.environ["ACCOUNT_DB_PATH"] = target_db
    import account_db
    account_db.DEFAULT_DB = target_db
    import gm_reader
    gm_reader.DEFAULT_DB = target_db

    # 装配客户端资源分发版本状态（默认 auto 优先与客户端自动对齐，支持 CLI 显式覆盖与 GM 面板动态切换）
    import res_version_manager
    target_res_ver = str(args.res_version).strip().lower()
    client_dir = args.client_assets_dir.strip() if args.client_assets_dir else None

    if target_res_ver == "auto":
        res_version_manager.apply_auto_client_version(
            custom_dir=client_dir,
            fallback_version=res_version_manager.DEFAULT_VERSION
        )
    else:
        res_version_manager.set_current_version(target_res_ver)
        res_version_manager.detect_client_resource_version(client_dir)

    # 记录服务启动时间戳
    server_start_ts = int(time.time())
    os.environ["SERVER_START_TIME"] = str(server_start_ts)
    setattr(gm_reader, "SERVER_START_TIME", server_start_ts)

    log_dir = os.path.join(v5_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    live_log_path = os.path.join(log_dir, "server_live.log")

    # 启动时轮转历史日志，保证当前会话启动时不加载过往残留日志
    if os.path.isfile(live_log_path):
        try:
            prev_log_path = os.path.join(log_dir, "server_live.prev.log")
            if os.path.isfile(prev_log_path):
                os.remove(prev_log_path)
            os.rename(live_log_path, prev_log_path)
        except Exception:
            pass

    class _TeeLogger:
        def __init__(self, stream, file_path):
            self.stream = stream
            self.file = open(file_path, "w", encoding="utf-8", buffering=1)
            self.lock = threading.Lock()

        def write(self, data):
            with self.lock:
                try:
                    self.stream.write(data)
                    self.stream.flush()
                except UnicodeEncodeError:
                    try:
                        enc = getattr(self.stream, "encoding", "utf-8") or "utf-8"
                        safe_text = data.encode(enc, errors="replace").decode(enc)
                        self.stream.write(safe_text)
                        self.stream.flush()
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    self.file.write(data)
                    self.file.flush()
                except Exception:
                    pass

        def flush(self):
            with self.lock:
                try:
                    self.stream.flush()
                except Exception:
                    pass
                try:
                    self.file.flush()
                except Exception:
                    pass

    if not isinstance(sys.stdout, _TeeLogger):
        sys.stdout = _TeeLogger(sys.stdout, live_log_path)
        sys.stderr = _TeeLogger(sys.stderr, live_log_path)

    # 统一挂接服务端日志器
    log = _logger.log

    # 打印客户端资源版本自适应探测日志
    curr_ver = res_version_manager.get_current_version()
    curr_cfg = res_version_manager.get_version_config(curr_ver)
    client_info = res_version_manager.get_detected_client_info()
    if target_res_ver == "auto":
        if client_info.get("detected"):
            log(f"客户端版本自动探测成功: 已自适应对齐至 Build {curr_ver} ({client_info.get('matched_file')})", module="VERSION")
        else:
            log(f"客户端版本自动探测未命中 ({client_info.get('detail')})，平滑降级至安全默认版本: Build {curr_ver}", module="VERSION")
    else:
        if client_info.get("detected") and client_info.get("version") != curr_ver:
            log(f"CLI 手动指定版本 ({curr_ver}) 与客户端探测版本 ({client_info.get('version')}) 不一致！将遵从 CLI 指定。", "WARN", module="VERSION")
        else:
            log(f"启动版本由 CLI 手动指定为: Build {curr_ver}", module="VERSION")

    # ---------------- 端口自检与冲突进程自动守护 (PortGuard) ----------------
    def _ensure_ports_clean(target_ports):
        curr_pid = os.getpid()
        target_ports = set(int(p) for p in target_ports if p and int(p) > 0)
        killed_pids = set()

        # 1. 检查并清理端口占用冲突
        try:
            import subprocess
            import re
            out = subprocess.check_output("netstat -ano", shell=True, text=True, errors="ignore")
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 4 and parts[0].upper() in ("TCP", "UDP"):
                    local_addr = parts[1]
                    m = re.search(r":(\d+)$", local_addr)
                    if m:
                        port = int(m.group(1))
                        if port in target_ports:
                            try:
                                pid = int(parts[-1])
                            except ValueError:
                                continue
                            if pid > 0 and pid != curr_pid and pid not in killed_pids:
                                log(f"发现端口 {port} 被残留进程 (PID: {pid}) 占用，正在自动终止清理...", module="PORT")
                                try:
                                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                                    killed_pids.add(pid)
                                    log(f"已成功清理冲突进程 PID: {pid}，释放端口 {port}。", module="PORT")
                                except Exception as e:
                                    log(f"清理 PID {pid} 失败: {e}", "ERROR", module="PORT")
        except Exception as e:
            log(f"netstat 端口扫描跳过: {e}", "WARN", module="PORT")

        # 2. 检查 .server.pid 记录的历史实例
        pid_file = os.path.join(v5_dir, ".server.pid")
        if os.path.isfile(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as f:
                    old_pid = int(f.read().strip())
                if old_pid > 0 and old_pid != curr_pid and old_pid not in killed_pids:
                    import subprocess
                    out = subprocess.check_output(["tasklist", "/FI", f"PID eq {old_pid}", "/FO", "CSV", "/NH"],
                                                  text=True, errors="ignore").strip()
                    if out and "No tasks" not in out and "没有运行" not in out:
                        log(f"发现 .server.pid 记录的旧实例 (PID: {old_pid})，正在自动终止...", module="PORT")
                        subprocess.run(["taskkill", "/F", "/PID", str(old_pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                        killed_pids.add(old_pid)
                        log(f"旧实例 PID {old_pid} 已清理。", module="PORT")
            except Exception:
                pass

        if killed_pids:
            time.sleep(0.5)

        # 3. 写入当前实例标识与设置控制台标题
        try:
            with open(pid_file, "w", encoding="utf-8") as f:
                f.write(str(curr_pid))
        except Exception:
            pass

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW(f"Tianchuan San (Mirfak) Server [PID: {curr_pid}]")
            except Exception:
                pass

        log(f"端口自检与进程守护就绪，当前服务单例 PID: {curr_pid}", module="PORT")

    # 执行端口预检与守护
    _ensure_ports_clean([args.game_port, args.gw_port, args.https_port, args.battle_port, 80])

    import gen_cert
    import cdn_proxy
    host_ip = args.host_ip.strip() if args.host_ip else gen_cert.get_default_lan_ip()
    server_net.set_host_ip(host_ip)

    if args.no_capture_cdn:
        cdn_proxy.set_capture_enabled(False)
    else:
        cdn_proxy.set_capture_enabled(True)

    if args.self_check:
        print("=== 深空之眼单机服务核心自检 ===")
        print(f"工作目录: {os.path.dirname(os.path.abspath(__file__))}")
        print(f"网络核心: OK (server_net)")
        curr_ver = res_version_manager.get_current_version()
        curr_cfg = res_version_manager.get_version_config(curr_ver)
        client_info = res_version_manager.get_detected_client_info()
        print(f"分发版本: {curr_ver} ({curr_cfg['display_name']})")
        if client_info.get("detected"):
            print(f"资源对齐: [已对齐] 命中客户端 Build {client_info['version']} ({client_info['matched_file']})")
        else:
            print(f"资源对齐: [未对齐/兜底] {client_info.get('detail')}")
        print(f"CDN 抓包: {'[已开启]' if cdn_proxy.is_capture_enabled() else '[已关闭]'}")
        print(f"局域网IP: {host_ip}")
        print(f"CORE 操作注册表: {sorted(core_mod.OPERATIONS.keys())}")
        print(f"代码版操作数: {len(core_mod.OPERATIONS)}")
        try:
            import middleware as _mw
            print(f"骨架条目: {len(_mw.load_skeleton())}")
        except Exception as e:
            print(f"骨架加载: 跳过 ({e})")
        print("自检通过")
        return 0

    # 自动校验与生成 ATS 合规双层证书与 iPhone 描述文件（若已有对应证书则不主动生成）
    v5_dir = os.path.dirname(os.path.abspath(__file__))
    gen_cert.generate_dual_layer_certs(output_dir=v5_dir, extra_ips=[host_ip], log=log, force=False)

    # 启动可选内置 DNS 服务
    if args.dns:
        import dns_server
        dns_server.start_dns_service(host_ip=host_ip, log=log)

    # 装配数据库 + CORE
    db = get_db(target_db)
    core = build_core(db=db, generator=_gen, codec_encode=_enc, codec_decode=_dec, log=log)
    log(f"权威数据库挂载: {os.path.basename(target_db)} ({target_db})", module="MAIN")
    log(f"CORE 装配完成，操作注册 {len(core_mod.OPERATIONS)} 个", module="MAIN")

    # 挂载版本守卫：若当前客户端资源分发版本为 229，执行幂等清洗下架 311 专属卡池
    try:
        from draw_service import DrawService
        draw_svc = DrawService.get_instance(db=db)
        cleaned = draw_svc.sanitize_pools_for_version(curr_ver)
        if cleaned > 0:
            log(f"启动版本守卫拦截: 已自动下架 {cleaned} 个 Build 311 专属卡池并同步活动状态", module="GACHA")
    except Exception as _e_sanitize:
        log(f"启动版本守卫异常: {_e_sanitize}", "WARN", module="GACHA")

    # 装配全局时间与周期事件订阅器
    import timer_listeners
    timer_listeners.register_timer_listeners()

    # 注册进程优雅退出信号捕获
    import signal
    import event_bus
    def _on_signal_exit(signum, frame):
        log(f"收到进程退出信号 ({signum})，广播 SERVER_SHUTDOWN...", module="MAIN")
        try:
            event_bus.bus.emit(event_bus.Events.SERVER_SHUTDOWN, ctx=core, uid=None, shutdown_ts=int(time.time()))
        except Exception:
            pass
        sys.exit(0)
    try:
        signal.signal(signal.SIGINT, _on_signal_exit)
        signal.signal(signal.SIGTERM, _on_signal_exit)
    except Exception:
        pass

    # 准备并校验客户端静态资源清单与语音包文件 (static_resources & D:\iOS_DATA)
    # 纯本地模式不得触发任何官方 CDN 请求，只同步已经存在的本地清单。
    try:
        if args.no_capture_cdn:
            log("纯本地资源模式：跳过官方 CDN 清单下载", module="CDN")
        else:
            import fetch_manifests
            fetch_manifests.prepare_manifests(log=log)
        cdn_proxy.sync_initial_manifests()
        log(f"资源存储路径已就绪: {cdn_proxy.get_data_dir()}", module="CDN")
    except Exception as e:
        log(f"静态资源准备异常: {e}", "WARN", module="CDN")

    # 装配重放模块（--replay 激活才加载）
    replay_mod = None
    material = None
    if args.replay and os.path.isdir(args.replay):
        replay_mod = _load_replay()
        override_map = {}
        if args.frame_override and os.path.isfile(args.frame_override):
            data = open(args.frame_override, "rb").read()
            try:
                import re as _re
                m = _re.match(r"(\d+)_", os.path.basename(args.frame_override))
                cmd = int(m.group(1)) if m else None
                if cmd is None and len(data) >= 7:
                    cmd = struct.unpack(">H", data[5:7])[0]
                if cmd:
                    override_map[cmd] = data
                    log(f"帧覆盖 cmd={cmd}", module="REPLAY")
            except Exception:
                pass
        material = replay_mod.load_replay_material(args.replay, override_map)
        # 登录分段提取（gw_seq/game_resp_map/gw_resp_map）
        _seg = replay_mod.split_login_segments(material["frames"], override_map)
        material["gw_seq"] = _seg["gw_seq"]
        material["game_resp_map"] = _seg["game_resp_map"]
        material["gw_resp_map"] = _seg["gw_resp_map"]
        log(f"重放模块激活: {len(material['frames'])} 帧素材, gw_seq={len(_seg['gw_seq'])}", module="REPLAY")
    else:
        log("重放模块未激活（原生动态调度模式）", module="REPLAY")

    # 启动 UDP 动态战斗服（XServer/KCP 协议，2026-08-22 V5 重写版）
    battle_srv = None
    if args.battle_port > 0:
        import battle_server as _bs
        import middleware as _mw
        battle_srv = _bs.BattleServer(
            args.battle_port,
            log=lambda *a, **k: log(*a, module="BATTLE"),
            server_ts=lambda: int(time.time()),
            bind_host="0.0.0.0")
        # 132 结果由战斗服内部登记（get_result/pop_battle_result 取用），
        # 客户端战毕必然回发 cs_54032 → h_54032 结算引擎被动应答，无需 UDP 线程推 TCP
        _mw.set_battle_server(battle_srv, args.battle_port, host_ip=host_ip)
        threading.Thread(target=battle_srv.run, daemon=True).start()
        log(f"UDP 动态战斗服监听 0.0.0.0:{args.battle_port}（下发目标 {host_ip}:{args.battle_port}）", module="BATTLE")

    # 启动 game TCP 服务器（server_net 网络层）
    srv = server_net.start_game_server(
        args.game_port, core, db, _gen, _enc, _dec,
        replay=replay_mod, material=material, frame_delay=args.frame_delay)
    log(f"game TCP 监听 {args.game_port}", module="NET")

    # 启动 gateway TCP 服务器（登录握手）
    gws = server_net.start_gateway_server(
        args.gw_port, core, db, _gen, _enc, _dec, material=material, host_ip=host_ip)
    gws.game_port = args.game_port
    gw_thread = threading.Thread(target=gws.serve_forever, daemon=True)
    gw_thread.start()
    log(f"gateway TCP 监听 {args.gw_port} (下发目标 IP: {host_ip})", module="NET")

    # 启动 HTTPS（SDK 层 + HTTP:80 辅助 + 官方 CDN 抓包拦截）
    app = server_net.build_https_app(core, material, args.game_port, args.gw_port, host_ip=host_ip)
    https_thread = threading.Thread(
        target=server_net.start_https, args=(app, args.https_port), daemon=True)
    https_thread.start()
    log(f"HTTPS(SDK) 监听 {args.https_port} (HTTP:80 直传已挂载)", module="NET")

    curr_ver = res_version_manager.get_current_version()
    curr_cfg = res_version_manager.get_version_config(curr_ver)
    client_info = res_version_manager.get_detected_client_info()

    print("=" * 64, flush=True)
    print(f"  [*] 深空之眼单机服务核心已启动：", flush=True)
    print(f"  - 宿主机局域网 IP : {host_ip}", flush=True)
    print(f"  - 客户端分发版本   : {curr_ver} ({curr_cfg['display_name']})", flush=True)
    if client_info.get("detected"):
        print(f"  - 客户端资源对齐   : [已对齐] 命中客户端 Build {client_info['version']} ({client_info['matched_file']})", flush=True)
    else:
        print(f"  - 客户端资源对齐   : [未对齐/兜底] {client_info.get('detail')}", flush=True)
    print(f"  - 根证书安装地址   : http://{host_ip}/cert", flush=True)
    print(f"  - PC 端资源落盘目录 : {cdn_proxy.get_data_dir('pc')}", flush=True)
    print(f"  - iOS 端资源落盘目录: {cdn_proxy.get_data_dir('ios')}", flush=True)
    print(f"  - 官方资源智能抓包 : {'[已开启] (双向流式转发 + 物理分盘落库)' if cdn_proxy.is_capture_enabled() else '[已关闭] (纯本地缓存)'}", flush=True)
    print(f"  - GM 运维控制台    : http://{host_ip}/web/gm_console.html", flush=True)
    print(f"  - 客户端关于与排障 : http://{host_ip}/web/agreement.html", flush=True)
    print("=" * 64, flush=True)

    while True:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            log("正常退出 (KeyboardInterrupt)", module="MAIN")
            break
        except BaseException as e:
            log(f"game TCP 捕获异常: {e}，保持持续运行...", "WARN", module="MAIN")
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
