"""Linux first-run setup and service commands; imports no game modules."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "linux_config.json"
SERVICE = "aethergazer.service"
DEFAULTS = dict(host_ip="", res_version="321", https_port=443, http_port=80,
                web_bind="0.0.0.0", gw_port=8102, game_port=8105,
                battle_port=6105, dns=False, db="account.db")


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


def validate_config(value):
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Linux 配置版本无效，请重新运行 setup_linux.sh")
    required = set(DEFAULTS) | {"schema", "username", "password_hash"}
    if set(value) != required:
        raise ValueError("Linux 配置字段缺失或未知")
    address = ipaddress.IPv4Address(value["host_ip"])
    if address.is_unspecified or address.is_loopback or address.is_multicast or str(address) == "255.255.255.255" or address in ipaddress.IPv4Network("198.18.0.0/15"):
        raise ValueError("对外发布地址必须是可访问的 IPv4")
    if value["res_version"] not in ("229", "311", "321"):
        raise ValueError("资源版本必须为 229、311 或 321")
    ipaddress.IPv4Address(value["web_bind"])
    for name in ("https_port", "http_port", "gw_port", "game_port", "battle_port"):
        port = value[name]
        minimum = 0 if name in ("http_port", "battle_port") else 1
        if type(port) is not int or not minimum <= port <= 65535:
            raise ValueError(f"无效端口 {name}")
    tcp = [value[n] for n in ("https_port", "http_port", "gw_port", "game_port") if value[n]]
    if len(tcp) != len(set(tcp)) or (value["dns"] and 53 in tcp):
        raise ValueError("TCP 监听端口重复")
    if type(value["dns"]) is not bool or (value["dns"] and value["battle_port"] == 53):
        raise ValueError("DNS 配置或 UDP 端口冲突")
    if not isinstance(value["db"], str) or not value["db"].strip() or "\x00" in value["db"]:
        raise ValueError("无效数据库路径")
    if not isinstance(value["username"], str) or not value["username"] or ":" in value["username"]:
        raise ValueError("无效控制台用户名")
    if any(ord(char) < 32 or ord(char) == 127 for char in value["username"]):
        raise ValueError("控制台用户名不能包含控制字符")
    # The authentication module validates the full hash before app startup.
    if not isinstance(value["password_hash"], str) or not value["password_hash"].startswith("pbkdf2_sha256$"):
        raise ValueError("缺少控制台密码摘要")
    return value


def load_config(path=CONFIG):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Linux 配置不能是符号链接")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError(f"配置权限过宽，请执行 chmod 600 '{path}'")
    return validate_config(json.loads(path.read_text("utf-8")))


def config_args(config):
    args = []
    for name in DEFAULTS:
        if name == "dns":
            if config[name]:
                args.append("--dns")
        else:
            args.extend(["--" + name.replace("_", "-"), str(config[name])])
    return args


def check_ports(https_port, http_port, gw_port, game_port, battle_port, web_bind="0.0.0.0", dns=False):
    """Probe bind permission/conflicts without stopping any existing process."""
    endpoints = [(socket.SOCK_STREAM, web_bind, https_port),
                 (socket.SOCK_STREAM, web_bind, http_port),
                 (socket.SOCK_STREAM, "0.0.0.0", gw_port),
                 (socket.SOCK_STREAM, "0.0.0.0", game_port),
                 (socket.SOCK_DGRAM, "0.0.0.0", battle_port)]
    if dns:
        endpoints.extend([(socket.SOCK_STREAM, "0.0.0.0", 53), (socket.SOCK_DGRAM, "0.0.0.0", 53)])
    held = []
    try:
        for kind, host, port in endpoints:
            if not port:
                continue
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
                raise OSError(f"{proto} {host}:{port} 无法监听: {exc}；请调整端口或停止对应服务") from exc
    finally:
        for sock in held:
            sock.close()


def prepare_environment():
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
        result = input(f"{label} [{default}]: ").strip() or str(default)
        try:
            return validator(result)
        except (ValueError, TypeError) as exc:
            print(f"输入无效: {exc}")


def port_input(value):
    port = int(value)
    if not 0 <= port <= 65535:
        raise ValueError("端口范围 0..65535")
    return port


def setup():
    print("Linux 首次配置：只写入本目录；不会自动修改防火墙、Nginx 或系统服务。")
    previous = load_config() if CONFIG.exists() else None
    config = dict(DEFAULTS if previous is None else previous)
    proxy = ask("部署方式：1 直接监听，2 已有 Nginx 反代", "1", lambda s: s if s in ("1", "2") else int("invalid")) == "2"
    config["host_ip"] = ask("客户端可访问的公网或局域网 IPv4（云主机填公网 IP）", config["host_ip"], lambda s: str(ipaddress.IPv4Address(s)))
    config["res_version"] = ask("客户端资源版本 229/311/321", config["res_version"], lambda s: s if s in ("229", "311", "321") else int("invalid"))
    config["web_bind"] = "127.0.0.1" if proxy else "0.0.0.0"
    config["https_port"] = ask("HTTPS 后端端口" if proxy else "HTTPS 端口", 18443 if proxy else config["https_port"], port_input)
    config["http_port"] = ask("HTTP 端口（0 关闭；已有 Nginx 建议 0）", 0 if proxy else config["http_port"], port_input)
    for name, label in (("gw_port", "网关 TCP 端口"), ("game_port", "游戏 TCP 端口"), ("battle_port", "战斗 UDP 端口（0 关闭）")):
        config[name] = ask(label, config[name], port_input)
    config["dns"] = ask("启用内置 DNS（占用 TCP/UDP 53）y/n", "n", lambda s: s.lower() if s.lower() in ("y", "n") else int("invalid")) == "y"
    config["db"] = ask("存档路径（相对本目录）", config["db"])
    config["username"] = ask("控制台用户名", previous["username"] if previous else "admin")
    while True:
        password = getpass.getpass("设置控制台密码（至少12字符；重新配置留空保留）: ")
        if not password and previous:
            config["password_hash"] = previous["password_hash"]
            break
        if len(password) < 12:
            print("密码至少12字符")
        elif password != getpass.getpass("再次输入密码: "):
            print("两次密码不一致")
        else:
            from admin_access import make_password_hash
            config["password_hash"] = make_password_hash(password)
            break
    config["schema"] = 1
    validate_config(config)
    database = Path(config["db"])
    if not database.is_absolute():
        database = ROOT / database
    if not database.is_file():
        raise ValueError(f"存档不存在: {database}；请先复制需要挂载的存档，再运行向导")
    backup_write(CONFIG, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    generate_templates(config)
    print("配置已保存（权限600）；使用 sh run_server.sh 启动，或 sh manage_server.sh install-service 注册并启动后台服务。")
    print("控制台使用 HTTPS /web/gm_console.html；安全组需放行配置的网关 TCP、游戏 TCP、战斗 UDP 及对外 HTTP/HTTPS 端口。")
    print("先运行 sh manage_server.sh check 检查本机监听；云厂商安全组需在厂商控制台确认。")


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
    unit = f'''[Unit]
Description=AetherGazer V5 server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=simple
User={user}
WorkingDirectory={systemd_quote(ROOT)}
ExecStart=/bin/sh {systemd_quote(ROOT / 'run_server.sh', command=True)}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM
UMask=0077

[Install]
WantedBy=multi-user.target
'''
    directory = ROOT / "linux_generated"
    backup_write(directory / SERVICE, unit)
    # An example to merge with an existing site's SDK host/SNI rules, never auto-install.
    nginx = f'''# Example only: merge server_name/certificates with your existing Nginx site.
# Replace the certificate paths before enabling; do not overwrite an existing site.
server {{
    listen 80;
    listen 443 ssl;
    server_name _;
    ssl_certificate /REPLACE/fullchain.pem;
    ssl_certificate_key /REPLACE/privkey.pem;
    location / {{
        proxy_pass https://127.0.0.1:{config['https_port']};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_ssl_verify off;
        proxy_read_timeout 300s;
    }}
}}
'''
    backup_write(directory / "nginx.example.conf", nginx)


def check(config):
    for name in ("main.py", "lua_proto_messages.json", "requirements.txt", "static_web/gm_console.html"):
        if not (ROOT / name).is_file():
            raise ValueError(f"缺少包内文件: {name}")
    database = Path(config["db"])
    if not database.is_absolute():
        database = ROOT / database
    if not database.is_file():
        raise ValueError(f"存档不存在: {database}")
    if not os.access(ROOT, os.W_OK) or not os.access(database, os.W_OK) or not os.access(database.parent, os.W_OK):
        raise ValueError("运行用户需要写入包目录、存档及存档所在目录")
    from admin_access import load_credentials
    load_credentials(CONFIG)
    check_ports(**{k: config[k] for k in ("https_port", "http_port", "gw_port", "game_port", "battle_port", "web_bind", "dns")})
    print("包文件、配置、写入权限和本机监听端口检查通过。云安全组及客户端连接需另行确认。")


def install_service(config):
    if os.geteuid() != 0:
        raise ValueError("安装系统服务需要 root；请使用 sudo sh manage_server.sh install-service")
    if not shutil.which("systemctl") or not Path("/run/systemd/system").is_dir():
        raise ValueError("当前系统没有运行 systemd，请使用 sh run_server.sh 前台启动")
    subprocess.run([str(ROOT / ".venv/bin/python"), "-c", "import flask, werkzeug, cryptography"], check=True)
    # Stop a previous instance explicitly before checking its occupied ports.
    status = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE])
    if status.returncode == 0:
        raise ValueError("同名服务正在运行；请先 stop，核对配置后再 install-service")
    check(config)
    generate_templates(config)
    backup_write(Path("/etc/systemd/system") / SERVICE, (ROOT / "linux_generated" / SERVICE).read_text("utf-8"), 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", SERVICE], check=True)
    subprocess.run(["systemctl", "status", "--no-pager", SERVICE], check=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Linux 安装、配置与服务管理")
    ap.add_argument("command", choices=("prepare", "setup", "run", "check", "templates", "install-service", "start", "stop", "restart", "status", "logs"))
    args, extra = ap.parse_known_args(argv)
    if args.command != "run" and extra:
        ap.error("仅 run 支持附加服务端参数")
    if args.command == "prepare":
        prepare_environment()
    elif args.command == "setup":
        setup()
    elif args.command == "run":
        saved = load_config() if CONFIG.exists() else None
        if saved is None and not extra:
            raise ValueError("首次运行请先执行 sh setup_linux.sh")
        if saved:
            from admin_access import load_credentials
            load_credentials(CONFIG)
            os.environ["AG_ADMIN_AUTH_FILE"] = str(CONFIG)
        os.chdir(ROOT)
        sys.argv = [str(ROOT / "main.py")] + (config_args(saved) if saved else []) + extra
        runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
    elif args.command in ("start", "stop", "restart", "status", "logs"):
        if args.command == "logs":
            subprocess.run(["journalctl", "-u", SERVICE, "-n", "100", "-f"], check=True)
        else:
            subprocess.run(["systemctl", args.command, SERVICE, "--no-pager"], check=True)
    else:
        config = load_config()
        {"check": check, "templates": generate_templates, "install-service": install_service}[args.command](config)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[Linux] {exc}", file=sys.stderr)
        raise SystemExit(1)
