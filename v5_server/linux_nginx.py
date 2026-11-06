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
PROXY_MARKER_START = "# --- AetherGazer Web GM Proxy Start ---"
PROXY_MARKER_END = "# --- AetherGazer Web GM Proxy End ---"


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
    proxy_mode = str(config.get("domain_proxy_mode") or "none").strip()
    # 半自动或手动反代模式下，自定义面板域名由外部站点配置接管，游戏客户端专用的反代配置中不可包含该域名，
    # 彻底杜绝 Nginx duplicate/conflicting server name 警告冲突！
    if proxy_mode in ("semi", "manual"):
        return " ".join(domains)
    custom = str(config.get("custom_domain") or "").strip()
    if custom:
        for d in custom.split():
            if d not in domains:
                domains.append(d)
    return " ".join(domains)


def render_site_proxy_location(https_port):
    return f"""    {PROXY_MARKER_START}
    location ^~ / {{
        proxy_pass https://127.0.0.1:{https_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_ssl_server_name on;
        proxy_ssl_name open.ys4fun.com;
        proxy_ssl_verify off;
        proxy_read_timeout 300s;
        proxy_buffering off;
    }}
    {PROXY_MARKER_END}"""


def inject_proxy_into_site_conf(site_conf_path, https_port, custom_domain=""):
    """安全地向外部 Nginx 站点配置注入或更新反向代理规则，操作前自动备份原文件。"""
    path = Path(site_conf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    # 1. 备份原文件
    backup_path = path.with_name(f"{path.name}.bak.{int(time.time())}")
    shutil.copy2(path, backup_path)

    content = path.read_text(encoding="utf-8")
    proxy_block = render_site_proxy_location(https_port)

    # 2. 如果已存在本向导标记块，则精准替换
    if PROXY_MARKER_START in content and PROXY_MARKER_END in content:
        pattern = re.compile(
            re.escape(PROXY_MARKER_START) + r".*?" + re.escape(PROXY_MARKER_END),
            re.DOTALL
        )
        new_content = pattern.sub(proxy_block.strip(), content)
    elif re.search(r"\bserver\s*\{", content):
        # 已有 server 块：在末尾反大括号前插入反代规则
        last_brace_index = content.rfind("}")
        if last_brace_index != -1:
            new_content = (
                content[:last_brace_index].rstrip() +
                "\n\n" + proxy_block + "\n" +
                content[last_brace_index:]
            )
        else:
            new_content = content + "\n\n" + proxy_block + "\n"
    else:
        # 文件无 server 块（空文件或配置片段）：包裹生成标准完整 server 块
        domain_str = custom_domain.strip() if custom_domain else "_"
        new_content = (
            f"# Managed by AetherGazer semi-auto proxy setup\n"
            f"server {{\n"
            f"    listen 80;\n"
            f"    listen 443 ssl;\n"
            f"    server_name {domain_str};\n\n"
            f"{proxy_block}\n"
            f"}}\n"
        )

    path.write_text(new_content, encoding="utf-8")
    return backup_path


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

    location / {{
        proxy_pass https://127.0.0.1:{config['https_port']};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_ssl_server_name on;
        proxy_ssl_name open.ys4fun.com;
        proxy_ssl_verify on;
        proxy_ssl_trusted_certificate {quoted_path(root / 'sdk_ca.crt')};
        proxy_read_timeout 300s;
        proxy_buffering off;
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
    if re.search(r"(?:^|\s)-(?:c|p)", title):
        raise ValueError("检测到带自定义配置/前缀的Nginx实例；请手动应用模板，避免操作错误实例")
    globals_option = re.search(r"(?:^|\s)-g\s*(.*)$", title)
    if globals_option and not re.fullmatch(r"(?:\s*(?:daemon|master_process)\s+(?:on|off)\s*;\s*)+", globals_option[1].strip().strip('\"\'')):
        raise ValueError("Nginx使用了额外全局配置；请手动应用模板，避免PID或配置不一致")
    return True


def apply_nginx(root, config, previous, config_path, commit):
    """Restore only our touched files, not unrelated concurrently edited sites."""
    from linux_manager import backup_write
    if not shutil.which("nginx"):
        raise ValueError("未安装Nginx。请安装后重试，或关闭Nginx选项；未保存配置")
    old_dir = Path(previous["nginx_dir"]) if previous and previous.get("nginx") else None
    new_dir = Path(config["nginx_dir"]) if config.get("nginx") else None
    paths = []
    if old_dir is not None and old_dir.is_dir():
        paths.append(old_dir / NAME)
    if new_dir is not None:
        if not new_dir.is_dir() or not os.access(new_dir, os.W_OK):
            raise ValueError(f"Nginx配置目录不存在或不可写: {new_dir}；请检查路径和权限")
        paths.append(new_dir / NAME)
    paths = list(dict.fromkeys(paths))
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.read_text("utf-8").startswith(MARKER)):
            raise ValueError(f"拒绝接管非本向导创建的配置: {path}")
    before = dump_config()
    running = pid_running(before)
    if config["nginx"]:
        for file in ("sdk_cert.pem", "sdk_key.pem", "sdk_ca.crt"):
            if not (root / file).is_file():
                raise ValueError(f"缺少Nginx所需证书: {file}")
        # Reject exact/wildcard game-host ownership in other config files.
        sections = re.split(r"(?m)^# configuration file (.+):\s*$", before)
        for index in range(1, len(sections), 2):
            if Path(sections[index]).resolve() in [p.resolve() for p in paths]:
                continue
            body = re.sub(r"(?m)#.*$", "", sections[index + 1])
            for names in re.findall(r"\bserver_name\s+([^;]+);", body):
                if any(re.search(r"(?:ys4fun\.(?:com|cn)|soboten\.com)", name) for name in names.split()):
                    raise ValueError(f"其他站点已配置游戏域名: {sections[index]}；请手动合并，未改动")
    snapshots = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None for path in [*paths, Path(config_path)]}
    token = secrets.token_hex(16)
    applied = False
    touched = []
    try:
        for path in paths:
            touched.append(path)
            if new_dir is not None and path == new_dir / NAME:
                backup_write(path, render_config(root, config, token), 0o644)
            elif path.exists():
                archive_file(path)
        after = dump_config()
        if config["nginx"] and (token not in after or str(new_dir / NAME) not in after):
            raise ValueError("指定目录未被Nginx加载，请选择nginx.conf实际include的目录")
        if running:
            applied = True  # reload may have succeeded even if the command times out.
            command(["nginx", "-s", "reload"])
        elif config["nginx"]:
            applied = True
            command(["nginx"])
        if config["nginx"]:
            health(root, token)
        touched.append(Path(config_path))
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
    if config["nginx"]:
        print("游戏域名HTTPS路由验证通过；Python后端尚未启动，本次不宣称游戏连接通过。")
