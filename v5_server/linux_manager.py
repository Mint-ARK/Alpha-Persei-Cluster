"""Linux first-run setup and service commands; imports no game modules."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import socket
import subprocess
import sys
import time
import sqlite3

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "linux_config.json"
ADMIN_AUTH_FILE = ROOT / "linux_admin_auth.json"
SERVICE = "aethergazer.service"
DEFAULTS = dict(host_ip="127.0.0.1", https_port=443, http_port=80,
                web_bind="0.0.0.0", gw_port=8102, game_port=8105,
                battle_port=6105, dns=False, db="account.db", cdn_mode="off",
                custom_domain="", domain_proxy_mode="none", site_conf_path="",
                admin_auth_configured=False)
PORT_FIELDS = ("https_port", "http_port", "gw_port", "game_port", "battle_port")
NETWORK_FIELDS = (*PORT_FIELDS, "web_bind", "dns")
TERMUX_DEPENDENCY_HELP = (
    "Termux：以下命令逐行执行，使用系统 Python，不创建隔离原生包的 venv：\n"
    "pkg update\n"
    "pkg install python python-pip python-cryptography\n"
    'python -m pip install "Flask==3.1.3" "Werkzeug==3.1.8"'
)


def ask_password_prompt(prompt_text):
    try:
        import getpass
        if sys.stdin.isatty():
            return getpass.getpass(prompt_text)
    except Exception:
        pass
    return input(prompt_text)


def load_existing_admin_hash(auth_path=None):
    target = Path(auth_path or ADMIN_AUTH_FILE)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") == 1 and isinstance(data.get("password_hash"), str):
            return data["password_hash"]
    except Exception:
        pass
    return None


def ask_admin_password(existing_hash=None):
    """Mandatory console password setup for public security (minimum 12 characters)."""
    has_existing = bool(existing_hash)
    while True:
        if has_existing:
            prompt = "请输入 Web 控制面板管理密码 [已配置，直接回车保持现有密码；输入新密码则更新]: "
        else:
            prompt = "请输入 Web 控制面板管理密码（公网安全必填，至少 12 个字符）: "

        pwd = ask_password_prompt(prompt).strip()
        if not pwd:
            if has_existing:
                return existing_hash
            print("[错误] 管理密码为必填项（不可跳过），至少需要 12 个字符。")
            continue

        if len(pwd) < 12:
            print("[错误] 密码长度不足 12 位（公网防暴力破解铁律），请重新输入。")
            continue
        try:
            if len(pwd.encode("utf-8")) > 1024:
                print("[错误] 密码过长，最多支持 1024 个 UTF-8 字节。")
                continue
        except UnicodeError:
            print("[错误] 密码编码格式不合法。")
            continue

        confirm = ask_password_prompt("请再次输入管理密码以确认: ").strip()
        if confirm != pwd:
            print("[错误] 两次输入的密码不一致，请重新输入。")
            continue

        from admin_session_auth import hash_password
        return hash_password(pwd)


def derive_admin_origin(config, proxy_mode):
    if config.get("custom_domain"):
        domain = config["custom_domain"].strip()
        if proxy_mode in ("proxy", "managed"):
            return f"https://{domain}"
        port = config.get("https_port", 443)
        return f"https://{domain}" if port == 443 else f"https://{domain}:{port}"
    elif config.get("usage") == "local":
        port = config.get("https_port", 443)
        if proxy_mode in ("proxy", "managed"):
            return "https://127.0.0.1"
        return "https://127.0.0.1" if port == 443 else f"https://127.0.0.1:{port}"
    else:
        ip = config.get("host_ip", "127.0.0.1")
        if proxy_mode in ("proxy", "managed"):
            return f"https://{ip}"
        port = config.get("https_port", 443)
        return f"https://{ip}" if port == 443 else f"https://{ip}:{port}"


def backup_write(path, content, mode=0o600):
    """Preserve previous contents before replacing a configuration file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"拒绝覆盖符号链接: {path}")
    if path.exists():
        backup = path.parent / "archive" / f"{path.name}.{time.time_ns()}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        backup.chmod(mode)
    # O_EXCL prevents following an existing temporary symlink.
    temporary = path.with_name(path.name + f".{time.time_ns()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.chmod(mode)


def panel_domain(value):
    if not isinstance(value, str):
        raise ValueError('自定义域名必须为字符串')
    domain = value.strip().lower()
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if domain and (len(domain) > 253 or not re.fullmatch(rf'{label}(?:\.{label})*', domain)):
        raise ValueError('请输入一个域名，不含协议、路径或端口')
    return domain


def validate_config(value):
    if not isinstance(value, dict) or value.get("schema") not in (1, 2):
        raise ValueError("Linux 配置版本无效，请重新运行 server.sh setup")
    value = dict(value)
    value.setdefault('cdn_mode', 'off')
    value.setdefault('custom_domain', '')
    if value["schema"] == 1:
        # Migrate in memory. The next setup saves v2, preserving v1 in archive.
        for key in ("res_version", "username", "password_hash"):
            value.pop(key, None)
        value.update(schema=2, usage="local" if value.get("host_ip") == "127.0.0.1" else "lan",
                     nginx=value.get("web_bind") == "127.0.0.1",
                     nginx_dir="/etc/nginx/conf.d")
    value.setdefault("custom_domain", "")
    value.setdefault("domain_proxy_mode", "none")
    value.setdefault("site_conf_path", "")
    required = set(DEFAULTS) | {"schema", "usage", "nginx", "nginx_dir"}
    if set(value) != required:
        raise ValueError("Linux 配置字段缺失或未知")
    if value['cdn_mode'] not in ('off', 'relay', 'cache'):
        raise ValueError('CDN模式必须为off、relay或cache')
    value['custom_domain'] = panel_domain(value['custom_domain'])
    if value['domain_proxy_mode'] == 'no_https':
        value['domain_proxy_mode'] = 'manual' if value['custom_domain'] else 'none'
    if value["domain_proxy_mode"] not in ("none", "semi", "manual"):
        raise ValueError("无效域名反代对接方式")
    if not isinstance(value["site_conf_path"], str):
        raise ValueError("站点配置文件路径必须为字符串")
    if value['domain_proxy_mode'] == 'semi' and not (value['custom_domain'] and value['site_conf_path']):
        raise ValueError('半自动配置需要面板域名和宝塔站点文件')
    if not isinstance(value["host_ip"], str) or not isinstance(value["web_bind"], str):
        raise ValueError("IP地址必须为字符串")
    address = ipaddress.IPv4Address(value["host_ip"])
    if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255" or address in ipaddress.IPv4Network("198.18.0.0/15"):
        raise ValueError("对外发布地址必须是可访问的 IPv4")
    bind = ipaddress.IPv4Address(value["web_bind"])
    if bind.is_multicast or str(bind) == "255.255.255.255":
        raise ValueError("无效 Web 监听地址")
    for name in ("https_port", "http_port", "gw_port", "game_port", "battle_port"):
        port = value[name]
        minimum = 0 if name in ("http_port", "battle_port") else 1
        if type(port) is not int or not minimum <= port <= 65535:
            raise ValueError(f"无效端口 {name}")
    tcp = [value[n] for n in ("https_port", "http_port", "game_port") if value[n]] + gateway_ports(value["gw_port"], value["game_port"])
    if len(tcp) != len(set(tcp)) or (value["dns"] and 53 in tcp):
        raise ValueError("TCP 监听端口重复")
    if type(value["dns"]) is not bool or (value["dns"] and value["battle_port"] == 53):
        raise ValueError("DNS 配置或 UDP 端口冲突")
    if not isinstance(value["db"], str) or not value["db"].strip() or "\x00" in value["db"]:
        raise ValueError("无效数据库路径")
    if value["usage"] not in ("local", "lan", "public") or type(value["nginx"]) is not bool:
        raise ValueError("无效使用方式或 Nginx 开关")
    if not isinstance(value["nginx_dir"], str) or not value["nginx_dir"].startswith("/") or any(c in value["nginx_dir"] for c in '\r\n\x00'):
        raise ValueError("Nginx 配置目录必须是绝对路径")
    if value["nginx"] and (value["web_bind"] != "127.0.0.1" or value["https_port"] in (80, 443) or value["http_port"] in (80, 443)):
        raise ValueError("同机 Nginx 后端须监听 127.0.0.1，且不能占用外部 80/443")
    return value


def load_config(path=None):
    path = Path(path or CONFIG)
    if path.is_symlink():
        raise ValueError("Linux 配置不能是符号链接")
    # v2 holds network settings only, no secrets. DrvFS may not honor chmod 600.
    return validate_config(json.loads(path.read_text("utf-8")))


def config_args(config):
    args = []
    for name in DEFAULTS:
        if name in ("custom_domain", "domain_proxy_mode", "site_conf_path", "admin_auth_configured"):
            continue
        if name == "dns":
            if config[name]:
                args.append("--dns")
        else:
            args.extend(["--" + name.replace("_", "-"), str(config[name])])
    return args


def gateway_ports(base, game):
    # Same slot mapping as save_slot_manager, without importing game/DB modules.
    if not base:
        return []
    result = [base + n + int(base <= game <= base + n) for n in range(32)]
    if result[-1] > 65535:
        raise ValueError("网关起始端口太高：32 槽位端口池超出 65535")
    return result


def endpoints(https_port, http_port, gw_port, game_port, battle_port, web_bind="0.0.0.0", dns=False):
    endpoints = [(socket.SOCK_STREAM, web_bind, https_port),
                 (socket.SOCK_STREAM, web_bind, http_port),
                 (socket.SOCK_STREAM, "0.0.0.0", game_port),
                 (socket.SOCK_DGRAM, "0.0.0.0", battle_port)]
    endpoints.extend((socket.SOCK_STREAM, "0.0.0.0", p) for p in gateway_ports(gw_port, game_port))
    if dns:
        endpoints.extend([(socket.SOCK_STREAM, "0.0.0.0", 53), (socket.SOCK_DGRAM, "0.0.0.0", 53)])
    return [(kind, host, port) for kind, host, port in endpoints if port]


def probe_endpoints(entries):
    """Collect all bind errors, retaining sockets to detect intra-plan conflicts."""
    held = []
    errors = []
    try:
        for kind, host, port in entries:
            sock = socket.socket(socket.AF_INET, kind)
            held.append(sock)
            try:
                if os.name == "posix" and kind == socket.SOCK_STREAM:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                if kind == socket.SOCK_STREAM:
                    sock.listen(1)
            except OSError as exc:
                proto = "TCP" if kind == socket.SOCK_STREAM else "UDP"
                proc_info = find_port_process(port, proto.lower())
                proc_str = f" [占用进程: {proc_info}]" if proc_info else ""
                errors.append(f"{proto} {host}:{port} 无法监听: {exc}{proc_str}")
    finally:
        for sock in held:
            sock.close()
    return errors


def check_ports(https_port, http_port, gw_port, game_port, battle_port, web_bind="0.0.0.0", dns=False):
    """Public signature used by main.py; never kills an occupied listener."""
    errors = probe_endpoints(endpoints(https_port, http_port, gw_port, game_port, battle_port, web_bind, dns))
    if errors:
        raise OSError("\n".join(errors))


def prepare_environment():
    if is_termux():
        raise ValueError(TERMUX_DEPENDENCY_HELP)
    if sys.version_info < (3, 10):
        raise RuntimeError("需要 Python 3.10+")
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        if (ROOT / ".venv").exists():
            raise ValueError("现有 .venv 不完整，请先移动到 archive 备份，再重试安装")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("无法创建 venv。Debian/Ubuntu 请安装匹配版本的 python3-venv；RHEL/OpenCloudOS 请安装 python3-pip。然后重试。") from exc
    subprocess.run([str(python), "-c", "import sys; assert sys.version_info >= (3,10)"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-r", str(ROOT / "requirements.txt")], check=True)


def ask(label, default, validator=str):
    while True:
        result = input(f"{label} [当前/建议值: {default}；留空回车采用此值]: ").strip() or str(default)
        try:
            return validator(result)
        except (ValueError, TypeError, OSError) as exc:
            print(f"输入无效: {exc}")


def port_input(value):
    port = int(value)
    if not 0 <= port <= 65535:
        raise ValueError("端口范围 0..65535")
    return port


def is_termux():
    return bool(os.environ.get("TERMUX_VERSION") or "/com.termux/" in os.environ.get("PREFIX", ""))


def runtime_check():
    """No game imports, package installs, DB opens or certificate generation."""
    errors = []
    if sys.version_info < (3, 10):
        errors.append("Python 需要 3.10+")
    if sqlite3.sqlite_version_info < (3, 24):
        errors.append("SQLite 需要 3.24+")
    for module, package, floor in (("flask", "Flask", (3, 1)), ("werkzeug", "Werkzeug", (3, 1)), ("cryptography", "cryptography", (42, 0))):
        try:
            importlib.import_module(module)
            version = importlib.metadata.version(package)
            import re
            numbers = tuple(int(n) for n in re.findall(r"\d+", version)[:2])
            if numbers < floor:
                errors.append(f"{package}={version}，需要至少 {'.'.join(map(str, floor))}；发行推荐见 requirements.txt")
            else:
                print(f"[OK] {package} {version}")
        except (ImportError, importlib.metadata.PackageNotFoundError, OSError) as exc:
            errors.append(f"缺少或无法加载 {package}: {exc}")
    for name in ("main.py", "lua_proto_messages.json", "requirements.txt", "static_web/gm_console.html"):
        if not (ROOT / name).is_file():
            errors.append(f"缺少包内文件: {name}")
    if not os.access(ROOT, os.W_OK):
        errors.append(f"运行目录不可写: {ROOT}")
    print(f"Python {sys.version.split()[0]} / SQLite {sqlite3.sqlite_version} / {sys.executable}")
    if errors:
        print("\n".join("[缺项] " + error for error in errors))
        print("Ubuntu/Debian: 安装 python3 python3-venv python3-pip；RHEL 系列选择 Python 3.10+。")
        print("普通 Linux：sh server.sh prepare（显式创建 .venv 并安装发行依赖）。")
        print(TERMUX_DEPENDENCY_HELP)
        raise ValueError("环境检查未通过，修复缺项后重新运行。未保存配置。")


def network_report(config, *, probe=True):
    print("\n监听计划（不是运行实例快照）：")
    for name, host, ports, proto in (
        ("HTTPS", config["web_bind"], str(config["https_port"]), "TCP"),
        ("HTTP", config["web_bind"], str(config["http_port"]) if config["http_port"] else "关闭", "TCP"),
        ("游戏", "0.0.0.0", str(config["game_port"]), "TCP"),
        ("网关池", "0.0.0.0", ",".join(map(str, gateway_ports(config["gw_port"], config["game_port"]))), "TCP"),
        ("战斗", "0.0.0.0", str(config["battle_port"]) if config["battle_port"] else "关闭", "UDP"),
    ):
        print(f"  {name}: {proto} {host}:{ports}")
    print(f"  DNS: {'TCP/UDP 0.0.0.0:53' if config['dns'] else '关闭'}")
    print(f"客户端连接地址: {config['host_ip']}；存档: {config['db']}")
    print(f"CDN模式: {config['cdn_mode']}")
    if probe:
        errors = probe_endpoints(endpoints(**{k: config[k] for k in NETWORK_FIELDS}))
        print("\n".join("[端口异常] " + item for item in errors) if errors else "[OK] 本机绑定检查通过（检查后端口仍可能被其他进程占用）。")
    print("公网安全组 / NAT / 外部可达性：未确认；本机绑定成功不代表公网放行。")


def firewall_report():
    # Read-only evidence, deliberately never infers reachability from one rule.
    command = next((cmd for cmd in (["ufw", "status"], ["firewall-cmd", "--list-all"], ["nft", "list", "ruleset"]) if shutil.which(cmd[0])), None)
    if command:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            output = (result.stdout + result.stderr).strip()
            print(f"本机防火墙参考（{'读取成功' if result.returncode == 0 else '无法确认'}）：\n{output[:4000]}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"本机防火墙：未确认（{exc}）")
    else:
        print("本机防火墙：未确认（未找到可用的规则查询工具）。")


def choice(options):
    def parse(value):
        if value not in options:
            raise ValueError("请选择 " + "/".join(options))
        return value
    return parse


def address_input(value):
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255" or address in ipaddress.IPv4Network("198.18.0.0/15"):
        raise ValueError("请输入客户端可访问的真实 IPv4；本机可以使用 127.0.0.1")
    return str(address)


def ask_ports(config):
    selected = dict(https_port=0, http_port=0, gw_port=0, game_port=0, battle_port=0,
                    web_bind=config["web_bind"], dns=False)
    for name, label in (("game_port", "游戏 TCP 端口"), ("gw_port", "网关起始 TCP 端口（预留32槽，自动跳过游戏端口）"),
                        ("https_port", "HTTPS 监听端口"), ("http_port", "HTTP 监听端口（0关闭）"), ("battle_port", "战斗 UDP 端口（0关闭）")):
        def parse(value, name=name):
            port = port_input(value)
            if port == 0 and name not in ("http_port", "battle_port"):
                raise ValueError("此入口为必需项，不能关闭")
            if is_termux() and port and port < 1024:
                raise ValueError("Termux 使用高位端口（>=1024）；HTTPS 推荐18443，HTTP推荐1880")
            check_ports(**dict(selected, **{name: port}))
            return port
        config[name] = selected[name] = ask(label, config[name], parse)
    return config


def database_check(config):
    database = Path(config["db"])
    if not database.is_absolute():
        database = ROOT / database
    if not database.is_file():
        raise ValueError(f"存档不存在: {database}")
    if not os.access(database, os.W_OK) or not os.access(database.parent, os.W_OK):
        raise ValueError("运行用户需要写入存档及所在目录")


def is_port_occupied(port, proto="tcp", host="0.0.0.0"):
    sock_type = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    try:
        with socket.socket(socket.AF_INET, sock_type) as s:
            if os.name == "posix" and proto == "tcp":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            if proto == "tcp":
                s.listen(1)
    except OSError:
        return True
    if host == "0.0.0.0":
        try:
            with socket.socket(socket.AF_INET, sock_type) as s:
                s.bind(("127.0.0.1", port))
                if proto == "tcp":
                    s.listen(1)
        except OSError:
            return True
    return False


def find_port_process(port, proto="tcp"):
    proto_lower = proto.lower()
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(f"netstat -ano -p {proto_lower}", shell=True, text=True, errors="ignore")
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 4 and parts[0].upper() == proto_lower.upper():
                    local_addr = parts[1]
                    m = re.search(r":(\d+)$", local_addr)
                    if m and int(m.group(1)) == port:
                        pid = parts[-1]
                        return f"PID: {pid}"
        except Exception:
            pass
        return None
    if shutil.which("lsof"):
        try:
            cmd = ["lsof", "-nP", f"-i{proto_lower.upper()}:{port}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            lines = res.stdout.strip().splitlines()
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 2:
                    return f"{parts[0]} (PID: {parts[1]})"
        except Exception:
            pass
    if shutil.which("ss"):
        try:
            flag = "-tlnp" if proto_lower == "tcp" else "-ulnp"
            res = subprocess.run(["ss", flag, f"sport = :{port}"], capture_output=True, text=True, timeout=2)
            m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', res.stdout)
            if m:
                return f"{m.group(1)} (PID: {m.group(2)})"
        except Exception:
            pass
    return None


def probe_system_ports(gw_base=8102, game_port=8105):
    print("\n[系统与游戏所需端口状态探测]")
    ports_to_probe = [
        ("HTTP (Web/证书续签)", 80, "tcp"),
        ("HTTPS (原生SDK鉴权)", 443, "tcp"),
        ("游戏主协议 (Game TCP)", game_port, "tcp"),
        ("战斗服 (Battle UDP)", 6105, "udp"),
        ("本地DNS劫持 (可选)", 53, "udp"),
        ("反代备选HTTPS (本地)", 18443, "tcp"),
        ("反代备选HTTP (本地GM)", 1880, "tcp"),
    ]
    for label, port, proto in ports_to_probe:
        occupied = is_port_occupied(port, proto)
        if occupied:
            proc_info = find_port_process(port, proto)
            proc_str = f" -> [占用进程: {proc_info}]" if proc_info else ""
            print(f"  * {port:<5} ({label:<20} {proto.upper()}): 已占用{proc_str}")
        else:
            print(f"  * {port:<5} ({label:<20} {proto.upper()}): 空闲可用")

    # 完整探测网关槽位连接池全部 32 个端口 (8102..8134，自动避开 game_port)
    gw_pool = gateway_ports(gw_base, game_port)
    occupied_gw = []
    for port in gw_pool:
        if is_port_occupied(port, "tcp"):
            proc = find_port_process(port, "tcp")
            occupied_gw.append((port, proc))

    if not occupied_gw:
        print(f"  * {min(gw_pool)}-{max(gw_pool)} (网关槽位池 32端口 TCP): 全部空闲可用 (共 32 个槽位端口)")
    else:
        print(f"  * {min(gw_pool)}-{max(gw_pool)} (网关槽位池 32端口 TCP): 发现占用！({len(occupied_gw)}/32 个端口被占用)")
        for p, proc in occupied_gw:
            proc_str = f" -> [占用进程: {proc}]" if proc else ""
            print(f"      - 槽位端口 {p}: 已占用{proc_str}")

    print("\n[重要提示] 若为云服务器环境，您还需要登录云厂商控制台（腾讯云/阿里云/华为云等），")
    print(f"在安全组/防火墙规则中放行上述的所有端口流量（TCP: 80, 443, {game_port}, {min(gw_pool)}-{max(gw_pool)} 及 UDP: 6105）！")


def detect_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def is_cloud_environment():
    try:
        hostname = socket.gethostname()
        if any(hostname.startswith(p) for p in ("VM-", "iZ", "ecs-", "ins-", "tencent", "aliyun")):
            return True
        lan = detect_lan_ip()
        if lan.startswith("10.") or lan.startswith("172."):
            return True
    except Exception:
        pass
    return False


def detect_public_ip():
    import urllib.request
    for url in ("https://api.ipify.org", "http://icanhazip.com"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                ip = resp.read().decode().strip()
                if ipaddress.IPv4Address(ip) and not ipaddress.IPv4Address(ip).is_private:
                    return ip
        except Exception:
            continue
    return None


def _tty_select(label, options, default_value):
    import termios, tty
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    idx = 0
    for i, opt in enumerate(options):
        if opt[0] == default_value:
            idx = i
            break

    def render_menu(first=False):
        if not first:
            sys.stdout.write(f"\033[{len(options)}A")
        for i, (k, name, _) in enumerate(options):
            prefix = "> [*]" if i == idx else "  [ ]"
            sys.stdout.write(f"\r\033[K{prefix} {i+1}) {k:6s} - {name}\n")
        sys.stdout.flush()

    try:
        print(f"{label} (方向键 [↑/↓] 移动，回车确认，或按数字键 1-{len(options)}):")
        render_menu(first=True)
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ('\r', '\n'):
                break
            elif ch == '\x03':
                raise KeyboardInterrupt
            elif ch in [str(i+1) for i in range(len(options))]:
                idx = int(ch) - 1
                break
            elif ch == '\x1b':
                seq = sys.stdin.read(2)
                if seq == '[A':
                    idx = (idx - 1) % len(options)
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    render_menu()
                    tty.setraw(fd)
                elif seq == '[B':
                    idx = (idx + 1) % len(options)
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    render_menu()
                    tty.setraw(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    selected = options[idx][0]
    print(f"已选择: {selected} ({options[idx][1]})\n")
    return selected


def interactive_choice(label, options, default_value=None, tips=None, extra_aliases=None):
    if tips:
        print(tips)

    has_tty = False
    try:
        import termios, tty
        has_tty = sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        has_tty = False

    if has_tty:
        try:
            return _tty_select(label, options, default_value)
        except Exception:
            pass

    valid_map = {}
    for i, (k, name, _) in enumerate(options, 1):
        valid_map[str(i)] = k
        valid_map[k.lower()] = k
    if extra_aliases:
        for alias, target in extra_aliases.items():
            valid_map[alias.lower()] = target

    prompt_str = f"{label} (1-{len(options)})"
    if default_value is not None:
        prompt_str += f" [{default_value}]"
    prompt_str += ": "

    while True:
        try:
            raw = input(prompt_str).strip()
            if not raw and default_value is not None:
                return default_value
            low = raw.lower()
            if low in valid_map:
                return valid_map[low]
            print(f"输入无效，请输入 1-{len(options)} 或对应名称。")
        except (KeyboardInterrupt, EOFError):
            raise


def print_configuration_summary(config, proxy_mode):
    proxy_desc = {
        "proxy": "外部反向代理模式（由宝塔/现有Nginx承接 80/443，后端转发）",
        "managed": "向导自动接管 Nginx（自动生成配置并平滑重载）",
        "direct": "独立直连模式（直接监听 80/443 端口，无反代）",
    }.get(proxy_mode, "独立直连模式")

    print("\n当前配置清单:")
    print(f"  * 运行模式 (Mode)       : {config['usage']}")
    print(f"  * 客户端连接 IP (IP)    : {config['host_ip']}")
    print(f"  * 服务端监听 (Web Bind) : {config['web_bind']}")
    print(f"  * Web 服务端口          : HTTPS {config['https_port']} / HTTP {config['http_port'] or '关闭'}")
    print(f"  * 游戏核心端口          : TCP {config['game_port']} (主协议) / UDP {config['battle_port'] or '关闭'} (战斗)")
    print(f"  * 网关连接池            : TCP {config['gw_port']} ~ {config['gw_port']+31} (共 32 槽位)")
    print(f"  * CDN 资源模式          : {config['cdn_mode']}")
    print(f"  * 存档数据库            : {config['db']}")
    custom_domain = str(config.get("custom_domain") or "").strip()
    domain_mode = str(config.get("domain_proxy_mode") or "none").strip()
    domain_mode_desc = {
        "semi": "半自动注入现有站点配置 (Semi-Auto)",
        "manual": "手动配置反向代理 (Manual)",
        "none": "未启用 (IP直连)",
    }.get(domain_mode, "未启用")
    domain_display = custom_domain if custom_domain else "未绑定 (仅使用主机IP)"
    print(f"  * 管理域名 (Domain)     : {domain_display}")
    if custom_domain:
        print(f"  * 域名反代模式          : {domain_mode_desc}")
        if domain_mode == "semi" and config.get("site_conf_path"):
            print(f"  * 注入站点文件          : {config['site_conf_path']}")
    print(f"  * Web 代理架构          : {proxy_desc}")
    has_auth = (ROOT / "linux_admin_auth.json").is_file() or bool(config.get("admin_auth_configured"))
    print(f"  * 控制面板安全保护      : {'已配置密码防护 (不可跳过必选项，PBKDF2-SHA256 60万轮)' if has_auth else '未配置'}")


def print_completion_guide(config, proxy_mode):
    print("\n" + "=" * 80)
    print("配置已成功保存！(已更新 linux_config.json)")
    print("=" * 80)
    conf_path = ROOT / "nginx_aethergazer.conf"
    custom_domain = str(config.get("custom_domain") or "").strip()
    domain_mode = str(config.get("domain_proxy_mode") or "none").strip()

    if domain_mode == "semi" and config.get("site_conf_path"):
        print("\n半自动反代已热加载。启动服务后可访问面板。")
    elif domain_mode == "manual":
        print("\n[手动配置反向代理指南]:")
        print(f"  1. 为 {custom_domain} 配置 HTTPS 证书及续签，并在该域名的 server 块内 include {ROOT / 'nginx_aethergazer_panel.conf'}。")
        if not config['nginx']:
            print(f"  2. 将 {conf_path} 放入 Nginx 的 http 配置加载目录。")
        print("  检查语法并热加载: sudo nginx -t && sudo nginx -s reload")
    elif not config['nginx']:
        if conf_path.is_file():
            print(f"\n已为您在当前目录生成了一份开箱即用的 Nginx 反向代理配置:")
            print(f"  -> {conf_path.name}")
            print("\n[Nginx 配置使用指南]:")
            print("  1. 复制配置文件到 Nginx 包含目录:")
            print(f"     sudo cp ./{conf_path.name} /etc/nginx/conf.d/")
            print("  2. 检查语法并执行平滑热重载生效:")
            print("     sudo nginx -t && sudo nginx -s reload")

    print("\n" + "=" * 80)
    print("[Web 管理控制面板直达地址]")
    print(f"  {derive_admin_origin(config, proxy_mode)}/web/gm_console.html")
    print("=" * 80)

    print("\n" + "-" * 80)
    print("游戏服务端启动方式:")
    print("  * 前台运行测试 (Ctrl+C 停止):")
    print("    sh server.sh run")
    print("  * 注册为系统守护服务 (后台开机自启):")
    print("    sudo sh server.sh install-service")
    print("=" * 80)


def setup():
    print("=" * 80)
    print("[第 1/5 步] 运行环境与系统端口体检")
    print("=" * 80)
    runtime_check()
    previous = load_config() if CONFIG.exists() else None
    config = dict(previous or dict(DEFAULTS, schema=2, usage="local", nginx=False, nginx_dir="/etc/nginx/conf.d"))
    if is_termux() and previous is None:
        config.update(https_port=18443, http_port=1880)

    probe_system_ports()
    firewall_report()

    print("\n" + "=" * 80)
    print("[第 2/5 步] 确定运行环境与网络模式")
    print("=" * 80)
    if is_termux():
        config.update(usage="local", host_ip="127.0.0.1", dns=False)
        print("Termux 运行环境: 固定本机使用 (127.0.0.1)，内置 DNS 关闭。")
    else:
        usage_tips = (
            "[模式说明]\n"
            "  * [1] local 模式 : 本机使用，仅对本机上的游戏提供服务器连接。\n"
            "  * [2] lan 模式   : 本机作为局域网服务端提供服务，在局域网内的所有设备都可以访问服务器，包括本机（可能需要外部路由器或者无线网卡）。\n"
            "  * [3] public 模式: 本机作为服务端对所有可访问本机公网IP的设备提供服务，您需要有一个固定不变的IPv4地址，如果您尝试使用云服务器部署服务端程序，请选择此模式。"
        )
        smart_default = config.get("usage", "local")
        if previous is None and is_cloud_environment():
            smart_default = "public"

        config["usage"] = interactive_choice(
            "请选择运行模式",
            [
                ("local", "本机单机使用", "仅本机可连，开发调试推荐"),
                ("lan", "局域网联机服务", "家庭 Wi-Fi / 宿舍同路由联机"),
                ("public", "云服务器公网服务", "云主机部署，全网玩家直连"),
            ],
            default_value=smart_default,
            tips=usage_tips
        )
        if config["usage"] == "local":
            config["host_ip"] = ask("发布 IPv4（接受回环地址）", config.get("host_ip", "127.0.0.1"), address_input)
        elif config["usage"] == "lan":
            default_ip = detect_lan_ip() if previous is None else config.get("host_ip", "192.168.1.100")
            config["host_ip"] = ask("发布 IPv4 (局域网内设备可访问的本机 IP)", default_ip, address_input)
        else:
            default_ip = config.get("host_ip")
            if not default_ip or default_ip == "127.0.0.1":
                detected = detect_public_ip()
                default_ip = detected if detected else "127.0.0.1"
            config["host_ip"] = ask("发布 IPv4 (云服务器公网固定 IP)", default_ip, address_input)

    print("\n" + "=" * 80)
    print("[第 3/5 步] Web 访问与 Nginx 代理架构规划")
    print("=" * 80)
    proxy_mode = "proxy" if config.get("web_bind") == "127.0.0.1" and not config.get("nginx") else ("managed" if config.get("nginx") else "direct")
    if is_termux():
        config["nginx"] = False
        config["web_bind"] = "0.0.0.0"
        proxy_mode = "direct"
        config["custom_domain"] = ""
        print("Termux 运行环境: 默认独立直连模式 (不使用 Nginx)。")
    else:
        nginx_tips = (
            "[架构说明]\n"
            "  * [1] 外部反向代理模式（强烈推荐，适合宝塔面板 / 已有 Nginx / 1Panel 环境）\n"
            "        游戏服务端将在高位安全端口（如 18443 / 1880）提供服务；\n"
            "        由您现有的 Nginx 或宝塔接收 80/443 流量并转发给服务端。\n"
            "        向导最后会自动生成一份开箱即用的反代配置。\n"
            "        注意：配置完成后，您仍需自行使用 nginx -t 检查语法，确认无误后使用 nginx -s reload 热重载生效（宝塔面板可直接在面板点击重载配置）。\n\n"
            "  * [2] 本向导全自动接管 Nginx（仅适合无面板、原生 apt/dnf 安装的标准 Nginx）\n"
            "        向导自动在 /etc/nginx/conf.d 生成 aethergazer-game.conf 并执行平滑热重载。\n\n"
            "  * [3] 独立直连模式（不使用任何 Web 代理）\n"
            "        游戏服务端自身直接对外提供 80 / 443 访问，不使用反向代理。\n"
            "        （注意：必须确保当前无其他服务占用 80/443 端口）"
        )
        has_web_occ = is_port_occupied(80) or is_port_occupied(443)
        default_choice = "2" if config.get("nginx") else ("1" if has_web_occ or previous is None else "3")
        choice_key = interactive_choice(
            "请选择 Web 代理架构",
            [
                ("1", "外部反向代理模式", "推荐宝塔/已有Nginx环境"),
                ("2", "向导自动接管Nginx", "适合标准无面板原生Nginx"),
                ("3", "独立直连模式", "直接占用80/443，无反代"),
            ],
            default_value=default_choice,
            tips=nginx_tips,
            extra_aliases={"proxy": "1", "external": "1", "managed": "2", "auto": "2", "y": "2", "direct": "3", "standalone": "3", "n": "3"}
        )
        if choice_key == "1":
            config["nginx"] = False
            config["web_bind"] = "127.0.0.1"
            proxy_mode = "proxy"
        elif choice_key == "2":
            config["nginx"] = True
            config["web_bind"] = "127.0.0.1"
            proxy_mode = "managed"
            config["nginx_dir"] = ask("现有nginx.conf包含的配置目录（常见/etc/nginx/conf.d）", config.get("nginx_dir", "/etc/nginx/conf.d"))
        else:
            config["nginx"] = False
            config["web_bind"] = "0.0.0.0"
            proxy_mode = "direct"

        if proxy_mode in ("proxy", "managed"):
            custom_domain = ask(
                "请输入用于访问 Web 控制面板的域名 (直接回车则仅使用主机IP)",
                config.get("custom_domain", ""),
                panel_domain,
            )
            config["custom_domain"] = custom_domain
            if custom_domain:
                domain_tips = (
                    f"[反向代理与 HTTPS 对接方式说明]\n"
                    f"检测到您配置了 Web 面板独立域名 [{custom_domain}]。\n"
                    f"为了避免与宝塔面板/已有网站的 SSL 证书及域名产生冲突，请选择对接方式：\n\n"
                    "  * [1] 宝塔半自动：选择已经启用 HTTPS 的宝塔站点。向导写入站点扩展及游戏反代，校验后热加载。\n"
                    "  * [2] 手动：生成面板站点片段和独立游戏配置，供已有 HTTPS 站点使用。\n"
                )
                default_sub = '1' if config.get('domain_proxy_mode') == 'semi' or (
                    proxy_mode == 'proxy' and config.get('domain_proxy_mode') != 'manual') else '2'
                sub_choice = interactive_choice(
                    "请选择面板域名的反向代理对接方式",
                    [
                        ("1", "宝塔半自动配置反向代理", "使用现有 HTTPS 站点的扩展目录"),
                        ("2", "手动配置反向代理", "生成现有 HTTPS 站点可包含的片段"),
                    ],
                    default_value=default_sub,
                    tips=domain_tips,
                    extra_aliases={"semi": "1", "auto": "1", "manual": "2"}
                )
                if sub_choice == "1":
                    config["domain_proxy_mode"] = "semi"
                    default_site_conf = config.get("site_conf_path") or f"/www/server/panel/vhost/nginx/{custom_domain}.conf"
                    print("\n请输入宝塔中已启用 HTTPS 的站点配置文件绝对路径。")
                    print("按 Ctrl+C 可取消当前向导。\n")
                    while True:
                        try:
                            user_path = input(f"站点配置文件路径 [建议/默认: {default_site_conf}；留空回车采用]: ").strip() or default_site_conf
                            p = Path(user_path).expanduser().resolve()
                            if not p.is_file():
                                print(f"[提示] 未找到指定文件: {p}")
                                print("请检查路径是否输入正确，或确保已在宝塔面板建站（若需退出请按 Ctrl+C）。")
                                continue
                            config["site_conf_path"] = str(p)
                            print(f"[OK] 已锁定站点配置文件: {p}")
                            break
                        except KeyboardInterrupt:
                            print("\n\n已通过 Ctrl+C 中断配置向导。")
                            sys.exit(0)
                else:
                    config["domain_proxy_mode"] = "manual"
                    config["site_conf_path"] = ""
            else:
                config["domain_proxy_mode"] = "none"
                config["site_conf_path"] = ""
        else:
            config["custom_domain"] = ""
            config["domain_proxy_mode"] = "none"
            config["site_conf_path"] = ""

    print("\n" + "=" * 80)
    print("[第 4/5 步] 端口分配与高级设置")
    print("=" * 80)
    if previous is None and not is_termux():
        if proxy_mode in ("proxy", "managed"):
            if config["https_port"] in (80, 443):
                config["https_port"] = 18443
            if config["http_port"] in (80, 443):
                config["http_port"] = 1880
        else:
            if config["https_port"] not in (80, 443):
                config["https_port"] = 443
            if config["http_port"] not in (80, 443):
                config["http_port"] = 80

    ask_ports(config)

    if config["usage"] == "lan" and not is_termux():
        def dns_input(value):
            enabled = choice(("y", "n"))(value) == "y"
            check_ports(**dict({k: config[k] for k in NETWORK_FIELDS}, dns=enabled))
            if enabled:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
                    source.bind((config["host_ip"], 0))
                if ipaddress.IPv4Address(config["host_ip"]).is_loopback:
                    raise ValueError("当前DNS转发使用发布IP作为出站源地址，回环模式请关闭内置DNS")
            return enabled
        print("内置 DNS 为可选项，占用 TCP/UDP 53，仅在局域网网卡需要时开启:")
        config["dns"] = ask("启用内置 DNS y/n", "y" if config.get("dns") else "n", dns_input)
    else:
        config["dns"] = False

    cdn_tips = (
        "[CDN 模式说明]\n"
        "  * off  : 默认关闭。云服务器推荐，不回源、不额外消耗出口带宽。\n"
        "  * relay: 仅穿透不存盘。边下边发，适合磁盘较小的本地设备。\n"
        "  * cache: 穿透并存盘。需要本地留存完整静态资源的大硬盘环境。"
    )
    config["cdn_mode"] = interactive_choice(
        "请选择 CDN 模式",
        [
            ("off", "关闭 CDN 穿透", "云服务器推荐，省出口流量"),
            ("relay", "仅穿透不存盘", "边下边发，适合小硬盘设备"),
            ("cache", "穿透并存盘", "需要完整本地缓存"),
        ],
        default_value=config.get("cdn_mode", "off"),
        tips=cdn_tips
    )

    print("\n" + "-" * 80)
    print("[公网安全与控制面板密码设置 (不可跳过的必选项)]")
    print("为防止挂载在公网上的控制面板被扫描到后被人提取可能存在的 API KEY，必须设置管理访问密码。")
    print("密码采用 PBKDF2-SHA256 (60万轮) 摘要保护与独立会话沙箱，新建至少 12 个字符。")
    print("-" * 80)
    existing_hash = load_existing_admin_hash(ROOT / "linux_admin_auth.json")
    admin_password_hash = ask_admin_password(existing_hash)
    config["admin_auth_configured"] = True

    print("\n" + "=" * 80)
    print("[第 5/5 步] 配置总览与最终确认")
    print("=" * 80)
    config = validate_config(config)
    database_check(config)
    check_ports(**{k: config[k] for k in NETWORK_FIELDS})
    print_configuration_summary(config, proxy_mode)

    if ask("保存并应用上述配置 y/n", "y", choice(("y", "n"))) == "n":
        print("已取消，未保存配置。")
        return

    from linux_nginx import apply_nginx, managed_paths, render_config, render_panel_config
    game_template = ROOT / 'nginx_aethergazer.conf'
    panel_template = ROOT / 'nginx_aethergazer_panel.conf'

    def commit():
        backup_write(CONFIG, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        # 写入或更新控制面板公网安全认证凭据文件
        if admin_password_hash:
            origin = derive_admin_origin(config, proxy_mode)
            trusted_proxies = ["127.0.0.1/32"] if proxy_mode in ("proxy", "managed") else []
            auth_data = {
                "schema": 1,
                "password_hash": admin_password_hash,
                "origin": origin,
                "session_seconds": 3600,
                "trusted_proxies": trusted_proxies,
            }
            backup_write(ADMIN_AUTH_FILE, json.dumps(auth_data, ensure_ascii=False, indent=2) + "\n", mode=0o600)
            print(f"[公网安全] 控制面板管理密码凭据已安全落盘: {ADMIN_AUTH_FILE.name} (HTTPS 源: {origin})")
        backup_write(game_template, render_config(ROOT, config), 0o644)
        if config['custom_domain']:
            backup_write(panel_template, render_panel_config(ROOT, config), 0o644)

    if any((*managed_paths(config), *managed_paths(previous))):
        apply_nginx(ROOT, config, previous, CONFIG, commit,
                    local_paths=(ADMIN_AUTH_FILE, game_template, panel_template))
    else:
        commit()

    print_completion_guide(config, proxy_mode)


def systemd_quote(value, *, command=False):
    # systemd specifiers expand even inside double quotes; escape '%' as well.
    escaped = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if command:
        escaped = escaped.replace('$', '$$')
    return '"' + escaped + '"'


def generate_templates(config):
    import pwd
    if any(c in str(ROOT) for c in "\r\n\x00"):
        raise ValueError("部署路径不能包含换行或NUL")
    user = pwd.getpwuid(os.getuid()).pw_name
    root_str = str(ROOT.resolve()).replace('\\', '/').replace('%', '%%')
    sh_str = str((ROOT / 'server.sh').resolve()).replace('\\', '/').replace('%', '%%').replace('$', '$$')
    venv_py = ROOT / ".venv/bin/python"
    python = venv_py if venv_py.is_file() and os.access(venv_py, os.X_OK) else Path(sys.executable)
    # Invoking the venv symlink is what lets Python discover pyvenv.cfg.
    python_env = systemd_quote(f'PYTHON_BIN={python.absolute().as_posix()}')
    auth_file = ROOT / "linux_admin_auth.json"
    auth_env = f'Environment="AG_ADMIN_PASSWORD_FILE={str(auth_file.resolve()).replace(chr(92), "/").replace("%", "%%")}"\n' if auth_file.is_file() else ""
    unit = f'''[Unit]
Description=AetherGazer V5 server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=simple
User={user}
WorkingDirectory={root_str}
ExecStart=/bin/sh "{sh_str}" run
Environment=PYTHONUNBUFFERED=1
Environment={python_env}
{auth_env}Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM
UMask=0077

[Install]
WantedBy=multi-user.target
'''
    directory = ROOT / "linux_generated"
    backup_write(directory / SERVICE, unit)
    if config['nginx'] or config.get('domain_proxy_mode') in ('manual', 'semi'):
        from linux_nginx import render_config, render_panel_config
        backup_write(directory / "nginx.example.conf", render_config(ROOT, config, "preview"))
        if config['custom_domain']:
            backup_write(directory / 'nginx.panel.example.conf', render_panel_config(ROOT, config))


def check(config):
    runtime_check()
    database_check(config)
    probe_system_ports(config.get("gw_port", 8102), config.get("game_port", 8105))
    network_report(config)
    firewall_report()
    check_ports(**{k: config[k] for k in NETWORK_FIELDS})
    auth_file = ROOT / "linux_admin_auth.json"
    if auth_file.is_file():
        try:
            from admin_session_auth import load_password_config
            load_password_config(str(auth_file))
            print(f"Web 控制面板公网安全凭据体检通过: {auth_file.name}")
        except Exception as exc:
            print(f"[警告] Web 控制面板密码凭据配置异常: {exc}")
    else:
        print("[提示] 当前尚未配置 Web 控制面板密码保护文件 (linux_admin_auth.json)")
    print("包文件、配置、写入权限和本机监听端口检查通过。")


def is_server_process(pid: int, root_dir: Path) -> bool:
    """严格核实 PID 是否真实对应当前根目录下正在运行的 main.py 进程。
    严格遵循进程身份匹配原则，严禁以端口号追踪或误判，避免误杀 Nginx/系统服务。"""
    if pid <= 0 or pid == os.getpid():
        return False
    root_resolved = Path(root_dir).resolve()
    if sys.platform == "win32":
        try:
            ps_cmd = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                                 capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout:
                cmdline = res.stdout.strip().lower()
                norm_dir = str(root_resolved).lower()
                if "main.py" in cmdline and (norm_dir in cmdline or "v5_server" in cmdline):
                    return True
        except Exception:
            pass
        return False
    else:
        # Linux / Termux / POSIX
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, OSError):
            return False

        proc_dir = Path(f"/proc/{pid}")
        if proc_dir.is_dir():
            cmdline_file = proc_dir / "cmdline"
            if cmdline_file.exists():
                try:
                    raw = cmdline_file.read_bytes()
                    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\x00") if p]
                    cmdline_str = " ".join(parts)
                    norm_root = str(root_resolved)
                    if any("main.py" in p for p in parts):
                        cwd_link = proc_dir / "cwd"
                        if cwd_link.exists():
                            try:
                                real_cwd = str(cwd_link.resolve())
                                if real_cwd == norm_root or norm_root.startswith(real_cwd):
                                    return True
                            except Exception:
                                pass
                        if norm_root in cmdline_str or "v5_server" in cmdline_str:
                            return True
                except Exception:
                    pass
        # 兼容未挂载 /proc 或非 Linux 系统
        if shutil.which("ps"):
            try:
                res = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                     capture_output=True, text=True, timeout=3)
                if res.returncode == 0 and res.stdout:
                    out = res.stdout.strip()
                    if "main.py" in out and (str(root_resolved) in out or "v5_server" in out):
                        return True
            except Exception:
                pass
        return False


def get_running_server_pid(root_dir: Path) -> int | None:
    """获取当前服务端运行中的进程 PID（仅在验证身份严格匹配时返回）。"""
    pid_file = Path(root_dir) / ".server.pid"
    if pid_file.exists():
        try:
            raw_text = pid_file.read_text("utf-8").strip()
            if raw_text.isdigit():
                old_pid = int(raw_text)
                if is_server_process(old_pid, root_dir):
                    return old_pid
                try:
                    pid_file.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    # 若 .server.pid 丢失或未记录，在 Linux /proc 下扫描匹配本目录 main.py 的进程
    proc_dir = Path("/proc")
    if proc_dir.is_dir():
        try:
            for entry in proc_dir.iterdir():
                if entry.name.isdigit():
                    candidate_pid = int(entry.name)
                    if is_server_process(candidate_pid, root_dir):
                        try:
                            pid_file.write_text(str(candidate_pid), "utf-8")
                        except Exception:
                            pass
                        return candidate_pid
        except Exception:
            pass
    return None


def stop_server_process(root_dir: Path) -> tuple[bool, str]:
    """明确停止游戏服务端（支持 systemd 模式及独立前后台进程）。"""
    systemd_stopped = False
    if shutil.which("systemctl"):
        try:
            status = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], capture_output=True)
            if status.returncode == 0:
                subprocess.run(["systemctl", "stop", SERVICE], check=True)
                systemd_stopped = True
        except Exception:
            pass

    pid = get_running_server_pid(root_dir)
    if not pid:
        if systemd_stopped:
            return True, f"[OK] 已停止系统守护服务 {SERVICE}"
        return False, "[*] 游戏服务端当前未在运行。"

    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
            for _ in range(15):
                time.sleep(0.2)
                try:
                    os.kill(pid, 0)
                except (ProcessLookupError, OSError):
                    break
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
    except Exception as e:
        return False, f"[!] 停止进程失败 (PID: {pid}): {e}"
    finally:
        pid_file = Path(root_dir) / ".server.pid"
        if pid_file.exists():
            try:
                pid_file.unlink()
            except Exception:
                pass

    msg = f"[OK] 游戏服务端已安全停止 (PID: {pid})"
    if systemd_stopped:
        msg += f"，同时停止了系统守护服务 {SERVICE}"
    return True, msg


def print_server_status(root_dir: Path):
    """显示游戏服务端运行状态与进程信息。"""
    is_systemd_active = False
    if shutil.which("systemctl"):
        try:
            res = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], capture_output=True)
            is_systemd_active = (res.returncode == 0)
        except Exception:
            pass

    pid = get_running_server_pid(root_dir)
    config = load_config() if CONFIG.exists() else None

    print("=" * 64)
    if is_systemd_active or pid:
        print("  [运行中] 游戏服务端正在运行")
        if pid:
            print(f"  - 进程 PID: {pid}")
        if is_systemd_active:
            print(f"  - 运行方式: 系统守护服务 (systemd: {SERVICE})")
        else:
            print("  - 运行方式: 独立前台/后台进程")
        print(f"  - 服务目录: {root_dir}")
        if config:
            mode = config.get("proxy_mode", "direct")
            host_ip = config.get("host_ip", "0.0.0.0")
            domain = config.get("custom_domain")
            http_port = config.get("http_port", 1880)
            if mode in ("proxy", "managed"):
                access_host = domain or host_ip
                print(f"  - 控制面板: http://{access_host}/web/control-panel.html")
            else:
                print(f"  - 控制面板: http://{host_ip}:{http_port}/web/control-panel.html")
    else:
        print("  [*] 游戏服务端当前未在运行。")
        print("  - 前台运行: sh server.sh run")
        print("  - 守护服务: sudo sh server.sh install-service")
    print("=" * 64)


def start_server(root_dir: Path) -> int:
    """启动已安装的系统守护服务。"""
    pid = get_running_server_pid(root_dir)
    is_systemd_active = False
    if shutil.which("systemctl"):
        try:
            res = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], capture_output=True)
            is_systemd_active = (res.returncode == 0)
        except Exception:
            pass
    if is_systemd_active or pid:
        print(f"[*] 游戏服务端已在运行中 (PID: {pid or 'systemd'})。")
        return 0

    service_file = Path("/etc/systemd/system") / SERVICE
    if shutil.which("systemctl") and service_file.exists():
        try:
            subprocess.run(["systemctl", "start", SERVICE], check=True)
            print(f"[OK] 已启动系统守护服务 ({SERVICE})。")
            return 0
        except Exception as e:
            print(f"[!] 启动系统守护服务失败: {e}", file=sys.stderr)
            return 1
    else:
        print("提示：当前未安装系统守护服务。")
        print("  前台启动请执行: sh server.sh run")
        print("  安装后台开机自启请执行: sudo sh server.sh install-service")
        return 0


def restart_server(root_dir: Path) -> int:
    """安全重启服务端。"""
    stopped, stop_msg = stop_server_process(root_dir)
    print(stop_msg)
    service_file = Path("/etc/systemd/system") / SERVICE
    if shutil.which("systemctl") and service_file.exists():
        try:
            subprocess.run(["systemctl", "start", SERVICE], check=True)
            print(f"[OK] 已重新拉起系统守护服务 ({SERVICE})。")
            return 0
        except Exception as e:
            print(f"[!] 拉起服务失败: {e}", file=sys.stderr)
            return 1
    else:
        print("[*] 服务端已完全停止。若需前台启动请执行: sh server.sh run")
        return 0


def install_service(config):
    if os.geteuid() != 0:
        raise ValueError("安装系统服务需要 root；请使用 sudo sh server.sh install-service")
    if not shutil.which("systemctl") or not Path("/run/systemd/system").is_dir():
        raise ValueError("当前系统没有运行 systemd，请使用 sh server.sh run 前台启动")
    runtime_check()
    # 明确检查历史实例（包含系统守护服务及独立进程）
    is_active = False
    if shutil.which("systemctl"):
        res = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE])
        is_active = (res.returncode == 0)
    if is_active or get_running_server_pid(ROOT) is not None:
        raise ValueError("服务端正在运行；请先执行 sh server.sh stop，核对配置后再 install-service")
    check(config)
    generate_templates(config)
    backup_write(Path("/etc/systemd/system") / SERVICE, (ROOT / "linux_generated" / SERVICE).read_text("utf-8"), 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", SERVICE], check=True)
    subprocess.run(["systemctl", "status", "--no-pager", SERVICE], check=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Linux 安装、配置与服务管理")
    ap.add_argument("command", choices=("prepare", "environment", "setup", "run", "check", "templates", "install-service", "start", "stop", "restart", "status", "logs"))
    args, extra = ap.parse_known_args(argv)
    if args.command != "run" and extra:
        ap.error("仅 run 支持附加服务端参数")
    if args.command == "prepare":
        prepare_environment()
    elif args.command == "environment":
        runtime_check()
    elif args.command == "setup":
        setup()
    elif args.command == "run":
        runtime_check()
        saved = load_config() if CONFIG.exists() else None
        if saved is None and not extra:
            raise ValueError("首次运行请先执行 sh server.sh setup")
        # Retire the old Linux-generated authentication file, not explicit custom auth.
        if os.environ.get("AG_ADMIN_AUTH_FILE") and Path(os.environ["AG_ADMIN_AUTH_FILE"]).resolve() == CONFIG.resolve():
            os.environ.pop("AG_ADMIN_AUTH_FILE", None)
        auth_file = ROOT / "linux_admin_auth.json"
        if auth_file.is_file() and "AG_ADMIN_PASSWORD_FILE" not in os.environ:
            os.environ["AG_ADMIN_PASSWORD_FILE"] = str(auth_file.resolve())
            print(f"[安全] 已自动挂载控制面板管理会话保护 ({auth_file.name})")
        if "--no-dns" in extra:
            if "--dns" in extra:
                raise ValueError("--dns 与 --no-dns 不能同时使用")
            extra.remove("--no-dns")
            if saved:
                saved = dict(saved, dns=False)
        os.chdir(ROOT)
        sys.argv = [str(ROOT / "main.py")] + (config_args(saved) if saved else []) + extra
        runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
    elif args.command == "stop":
        stopped, msg = stop_server_process(ROOT)
        print(msg)
        return 0
    elif args.command == "status":
        print_server_status(ROOT)
        return 0
    elif args.command == "restart":
        return restart_server(ROOT)
    elif args.command == "start":
        return start_server(ROOT)
    elif args.command == "logs":
        if shutil.which("journalctl"):
            subprocess.run(["journalctl", "-u", SERVICE, "-n", "100", "-f"], check=True)
        else:
            print("当前系统未安装 journalctl，前台运行日志请直接查看控制台输出。")
        return 0
    else:
        config = load_config()
        {"check": check, "templates": generate_templates, "install-service": install_service}[args.command](config)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, EOFError, KeyboardInterrupt, subprocess.SubprocessError) as exc:
        print(f"[Linux] {exc}", file=sys.stderr)
        raise SystemExit(1)
