"""Transactional, opt-in Nginx integration. No game modules or DB access."""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import time

MARKER = "# Managed by AetherGazer Linux setup v2"
NAME = "aethergazer-game.conf"
DOMAINS = "ys4fun.com *.ys4fun.com ys4fun.cn *.ys4fun.cn soboten.com *.soboten.com"
PANEL_NAME = "aethergazer-panel.conf"


def quoted_path(path):
    value = str(Path(path).resolve())
    if any(c in value for c in '\r\n\x00$'):
        raise ValueError("Nginx文件路径不能含换行、NUL或$；请移动部署目录或手动配置")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def get_server_domains(config):
    domains = [DOMAINS]
    host_ip = str(config.get("host_ip") or "").strip()
    if host_ip and host_ip not in ("127.0.0.1", "0.0.0.0", "::1"):
        domains.append(host_ip)
    return " ".join(domains)


def upstream_directives(root, https_port):
    return f"""
        proxy_pass https://127.0.0.1:{https_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_ssl_server_name on;
        proxy_ssl_name open.ys4fun.com;
        proxy_ssl_verify on;
        proxy_ssl_trusted_certificate {quoted_path(root / 'sdk_ca.crt')};
        proxy_read_timeout 300s;
        proxy_buffering off;"""


def render_panel_config(root, config):
    domain = config['custom_domain']
    upstream = upstream_directives(root, config['https_port'])
    return f'''{MARKER}
    location = / {{
        return 302 https://{domain}/web/gm_console.html;
    }}

    location ^~ /web/ {{
        if ($scheme = http) {{ return 308 https://{domain}$request_uri; }}{upstream}
    }}

    location ^~ /api/gm/ {{
        if ($scheme = http) {{ return 308 https://{domain}$request_uri; }}{upstream}
    }}
'''


def render_config(root, config, token=None):
    health_block = f"""
    location = /__ag_setup_health {{
        default_type text/plain;
        return 200 "{token}";
    }}""" if token else ""
    domains = get_server_domains(config)
    return f'''{MARKER}
# AetherGazer standalone reverse proxy configuration
server {{
    listen 80;
    listen 443 ssl;
    server_name {domains};
    ssl_certificate {quoted_path(root / 'sdk_cert.pem')};
    ssl_certificate_key {quoted_path(root / 'sdk_key.pem')};{health_block}

    # ACME SSL verification channel (prevent proxy pass interference during renewal)
    location ^~ /.well-known/acme-challenge/ {{
        default_type text/plain;
        root /var/www/html;
        try_files $uri =404;
    }}

    location / {{{upstream_directives(root, config['https_port'])}
    }}
}}
'''


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} 失败：\n{result.stdout}\n{result.stderr}")
    return result.stdout + result.stderr


def dump_config():
    # -T checks syntax too; output may contain other sites' secrets, never log it.
    output = command(["nginx", "-T"])
    if re.search(r"conflicting server name|duplicate listen", output, re.I):
        raise ValueError("Nginx存在域名或监听配置冲突，请先处理；未覆盖其他站点")
    return output


def archive_file(path):
    path = Path(path)
    directory = path.parent / "archive"
    directory.mkdir(exist_ok=True)
    path.rename(directory / f"{path.name}.{time.time_ns()}.disabled")


def health(root, token):
    context = ssl.create_default_context(cafile=str(root / "sdk_ca.crt"))
    last = None
    for _ in range(5):
        try:
            with socket.create_connection(("127.0.0.1", 443), timeout=2) as raw:
                with context.wrap_socket(raw, server_hostname="open.ys4fun.com") as client:
                    client.sendall(b"GET /__ag_setup_health HTTP/1.1\r\nHost: open.ys4fun.com\r\nConnection: close\r\n\r\n")
                    response = b""
                    while len(response) < 16384:
                        part = client.recv(4096)
                        if not part:
                            break
                        response += part
            if b" 200 " in response.split(b"\r\n", 1)[0] and token.encode() in response.partition(b"\r\n\r\n")[2]:
                return
            last = "游戏域名未命中本次配置"
        except (OSError, ssl.SSLError) as exc:
            last = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"Nginx热加载后检查失败：{last}")


def pid_running(dump):
    match = re.search(r"(?m)^\s*pid\s+([^;]+);", dump)
    if match:
        pid_path = Path(match.group(1).strip().strip('"\''))
    else:
        version = command(["nginx", "-V"])
        match = re.search(r"--pid-path=([^\s]+)", version)
        if not match:
            raise ValueError("无法确定Nginx PID路径；请先配置明确的pid指令，或手动应用模板")
        pid_path = Path(match.group(1).strip('"\''))
    if not pid_path.is_absolute():
        raise ValueError("自动配置要求Nginx使用绝对PID路径；自定义启动参数请手动应用模板")
    if not pid_path.exists():
        return False
    text = pid_path.read_text().strip()
    if not text.isdigit() or int(text) <= 1:
        raise ValueError("Nginx PID文件无效")
    pid = int(text)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        raise ValueError("Nginx PID文件已失效，请先处理旧PID文件") from None
    # Do not send reload to a reused PID or silently target a custom instance.
    process = Path(f"/proc/{pid}/cmdline")
    if not process.exists() or "nginx: master process" not in process.read_bytes().decode(errors="replace"):
        raise ValueError("无法核验默认Nginx主进程；自定义实例请手动应用模板")
    title = process.read_bytes().decode(errors="replace")
    if re.search(r'(?:^|\s)-p', title):
        raise ValueError('检测到自定义 Nginx 前缀，请手动应用模板')
    config_option = re.search(r'(?:^|\s)-c\s*([^\s\x00]+)', title)
    if config_option:
        active = Path(config_option[1].strip('"\''))
        default = re.search(r'(?m)^# configuration file (.+):\s*$', dump)
        if not default or not active.is_absolute() or active.resolve() != Path(default[1]).resolve():
            raise ValueError('Nginx 主进程使用其他配置文件，请手动应用模板')
    globals_option = re.search(r"(?:^|\s)-g\s*(.*)$", title)
    if globals_option and not re.fullmatch(r"(?:\s*(?:daemon|master_process)\s+(?:on|off)\s*;\s*)+", globals_option[1].strip().strip('\"\'')):
        raise ValueError("Nginx使用了额外全局配置；请手动应用模板，避免PID或配置不一致")
    return True


def managed_paths(config):
    if not config:
        return None, None
    if config.get('domain_proxy_mode') == 'semi' and config.get('site_conf_path'):
        site = Path(config['site_conf_path']).resolve()
        panel_dir = site.parent / 'extension' / config['custom_domain']
        return site.parent / NAME, panel_dir / PANEL_NAME
    if config.get('nginx'):
        return Path(config['nginx_dir']).resolve() / NAME, None
    return None, None


def check_panel_include(config, sections, panel_path):
    site = Path(config['site_conf_path']).resolve()
    body = re.sub(r'(?m)^\s*#.*$', '', sections.get(site, ''))
    names = re.findall(r'\bserver_name\s+([^;]+);', body)
    includes = {value.strip().strip('"\'') for value in re.findall(r'\binclude\s+([^;]+);', body)}
    if (len(re.findall(r'(?m)^\s*server\s*\{', body)) != 1
            or names != [config['custom_domain']]
            or str(panel_path.parent / '*.conf') not in includes
            or panel_path.parent.resolve() != panel_path.parent):
        raise ValueError('半自动需要宝塔已加载的独立 HTTPS 站点及其 extension/*.conf，请检查路径或选择手动配置')
    if not re.search(r'(?m)^\s*listen\s+[^;]*\b443\s+[^;]*\bssl\b', body):
        raise ValueError('请先在宝塔站点启用 HTTPS，再运行半自动配置')


def prepare_certificate(root, host_ip):
    from gen_cert import generate_dual_layer_certs

    # Nginx reads the leaf at reload, before the game process first starts.
    if not generate_dual_layer_certs(output_dir=root, extra_ips=[host_ip]):
        raise RuntimeError('Nginx 上游证书准备失败')


def apply_nginx(root, config, previous, config_path, commit, *, local_paths=()):
    """Restore only our touched files, not unrelated concurrently edited sites."""
    from linux_manager import backup_write
    if not shutil.which("nginx"):
        raise ValueError("未安装Nginx。请安装后重试，或关闭Nginx选项；未保存配置")
    game_path, panel_path = managed_paths(config)
    paths = list(dict.fromkeys(path for path in (*managed_paths(previous), game_path, panel_path) if path))
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.read_text("utf-8").startswith(MARKER)):
            raise ValueError(f"拒绝接管非本向导创建的配置: {path}")
    before = dump_config()
    running = pid_running(before)
    sections = re.split(r"(?m)^# configuration file (.+):\s*$", before)
    sections = {Path(name).resolve(): body for name, body in zip(sections[1::2], sections[2::2])}
    if panel_path:
        check_panel_include(config, sections, panel_path)
    for path in (game_path, panel_path):
        if path and (not path.parent.is_dir() or not os.access(path.parent, os.W_OK)):
            raise ValueError(f"Nginx配置目录不存在或不可写: {path.parent}")
    if game_path:
        for file in ("sdk_cert.pem", "sdk_key.pem", "sdk_ca.crt"):
            if not (root / file).is_file():
                raise ValueError(f"缺少Nginx所需证书: {file}")
        # Reject exact/wildcard game-host ownership in other config files.
        for name, body in sections.items():
            if name in paths:
                continue
            body = re.sub(r"(?m)#.*$", "", body)
            for names in re.findall(r"\bserver_name\s+([^;]+);", body):
                if any(re.search(r"(?:ys4fun\.(?:com|cn)|soboten\.com)", name) for name in names.split()):
                    raise ValueError(f"其他站点已配置游戏域名: {name}；请手动合并，未改动")
        prepare_certificate(root, config['host_ip'])
    config_paths = (Path(config_path), *local_paths)
    snapshots = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
                 for path in [*paths, *config_paths]}
    token = secrets.token_hex(16)
    applied = False
    touched = []
    try:
        for path in paths:
            touched.append(path)
            if path == game_path:
                backup_write(path, render_config(root, config, token), 0o644)
            elif path == panel_path:
                backup_write(path, render_panel_config(root, config), 0o644)
            elif path.exists():
                archive_file(path)
        after = dump_config()
        if game_path and (token not in after or str(game_path) not in after):
            raise ValueError("指定目录未被Nginx加载，请选择nginx.conf实际include的目录")
        if panel_path and str(panel_path) not in after:
            raise ValueError("面板扩展配置未被 Nginx 加载")
        if running:
            applied = True  # reload may have succeeded even if the command times out.
            command(["nginx", "-s", "reload"])
        elif game_path:
            applied = True
            command(["nginx"])
        if game_path:
            health(root, token)
        touched.extend(config_paths)
        commit()
    except BaseException as original:
        failures = []
        for path in reversed(touched):
            try:
                snapshot = snapshots[path]
                if snapshot is None:
                    if path.exists():
                        archive_file(path)
                else:
                    backup_write(path, snapshot[0].decode("utf-8"), snapshot[1])
            except Exception as exc:
                failures.append(f"恢复 {path} 失败: {exc}")
        if applied:
            try:
                dump_config()
                # Never stop/restart nginx, even when the initial start was ours.
                command(["nginx", "-s", "reload"])
            except Exception as exc:
                failures.append(f"旧配置重新加载失败: {exc}")
        if failures:
            raise RuntimeError(f"{original}\n" + "\n".join(failures) + "\n备份保存在各文件同级archive，请检查。") from original
        raise
    print("Nginx已提交热加载。" if running else "Nginx配置已应用。")
    if game_path:
        print(f"游戏反代已加载: {game_path}")
    if panel_path:
        print(f"面板站点扩展已加载: {panel_path}")
