"""Persist validated Web launch overrides and request an owned process restart."""
import json
import os
import signal
import subprocess
import sys
import threading
from ipaddress import IPv4Address
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'data' / 'launch_overrides.json'
RESTART_EXIT_CODE = 75
PORTS = ('https_port', 'http_port', 'gw_port', 'game_port', 'battle_port')
FIELDS = (*PORTS, 'host_ip', 'web_bind', 'dns', 'dns_upstream', 'cdn_mode',
          'db_path', 'client_assets_dir', 'log_level', 'replay_enabled', 'replay_path')
_lock = threading.Lock()
_current = {}
_restart = None
_pending = False


@lru_cache(maxsize=1)
def _systemd_restarts_on_exit():
    try:
        result = subprocess.run(
            ['systemctl', 'show', 'aethergazer.service', '--property=Restart', '--value'],
            capture_output=True, text=True, timeout=2, check=True)
        return result.stdout.strip() in ('always', 'on-failure')
    except (OSError, subprocess.SubprocessError):
        return False


def supervisor():
    manager = os.environ.get('AG_LAUNCH_SUPERVISOR')
    if manager == 'cli' and str(os.getppid()) != os.environ.get('AG_LAUNCH_PARENT_PID'):
        manager = None
    if os.name == 'nt' and manager in ('windows', 'cli'):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, os.getppid())
        if handle:
            try:
                code = wintypes.DWORD()
                if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259:
                    return manager
            finally:
                kernel.CloseHandle(handle)
        return ''
    if manager == 'cli':
        return manager
    if sys.platform.startswith('linux') and os.environ.get('INVOCATION_ID'):
        try:
            groups = Path('/proc/self/cgroup').read_text('utf-8').splitlines()
        except OSError:
            groups = []
        if (any(group.endswith('/aethergazer.service') for group in groups)
                and _systemd_restarts_on_exit()):
            return 'systemd'
    return ''


def run_supervised(argv):
    """Run one owned kernel and relaunch it only after a panel restart request."""
    env = os.environ | {'AG_LAUNCH_SUPERVISOR': 'cli', 'AG_LAUNCH_PARENT_PID': str(os.getpid())}
    command = [sys.executable, '-B', '-X', 'utf8', str(ROOT / 'main.py'), *argv]
    stopping = False
    child = None

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            child = subprocess.Popen(command, env=env)
            try:
                result = child.wait()
            except KeyboardInterrupt:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait()
                return 130
            if stopping or result != RESTART_EXIT_CODE:
                return result
            print('[启动托管] 正在按已保存的配置重启内核', flush=True)
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)


def _overrides():
    if not CONFIG.is_file():
        return {}
    data = json.loads(CONFIG.read_text('utf-8-sig'))
    values = data.get('overrides', {})
    if not isinstance(values, dict):
        raise ValueError('启动配置格式无效')
    return {key: values[key] for key in FIELDS if key in values}


def values_from_args(args):
    return dict(
        **{key: getattr(args, key) for key in PORTS},
        host_ip=args.host_ip, web_bind=str(args.web_bind), dns=args.dns,
        dns_upstream=[str(ip) for ip in args.dns_upstream or []],
        cdn_mode='off' if args.no_capture_cdn else 'cache' if args.capture_cdn else args.cdn_mode,
        db_path=args.db, client_assets_dir=args.client_assets_dir, log_level=args.log_level,
        replay_enabled=bool(args.replay), replay_path=args.replay,
    )


def _apply(args, values):
    for key in FIELDS:
        if key not in values or key in ('replay_enabled', 'replay_path', 'db_path'):
            continue
        value = values[key]
        if key == 'web_bind':
            value = IPv4Address(value)
        elif key == 'dns_upstream':
            value = [IPv4Address(ip) for ip in value]
        elif key == 'cdn_mode':
            args.capture_cdn = args.no_capture_cdn = False
        setattr(args, key, value)
    if 'db_path' in values:
        args.db = values['db_path']
    if 'replay_enabled' in values or 'replay_path' in values:
        enabled = values.get('replay_enabled', bool(args.replay))
        args.replay = values.get('replay_path', args.replay) if enabled else ''


def parse_args(parser, argv, *, launcher_arg_count=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    # Parse help and standalone diagnostics without touching saved launch values.
    if any(arg in raw for arg in ('-h', '--help', '--check-update', '--sync-update',
                                  '--list-resource-profiles', '--self-check')):
        return parser.parse_args(raw)
    if launcher_arg_count is None:
        launcher_arg_count = len(raw) if supervisor() in ('windows', 'cli') else 0
    base = parser.parse_args(raw[:launcher_arg_count])
    try:
        _apply(base, _overrides())
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f'读取面板启动配置失败: {exc}')
    explicit = raw[launcher_arg_count:]
    if any(arg == '--dns-upstream' or arg.startswith('--dns-upstream=') for arg in explicit):
        base.dns_upstream = None
    return parser.parse_args(explicit, namespace=base)


def configure(args, callback):
    global _current, _restart, _pending
    _current = values_from_args(args)
    _restart = callback
    _pending = False


def status():
    from logger import get_level_name
    with _lock:
        active = dict(_current)
        active['log_level'] = get_level_name()
        values = active | _overrides()
        return values | dict(active=active, supervisor=supervisor() if _restart else '',
                             restart_pending=_pending)


def _endpoints(values):
    from linux_manager import endpoints
    return endpoints(*(values[key] for key in PORTS), values['web_bind'], values['dns'])


def _overlap(a, b):
    return a[0] == b[0] and a[2] == b[2] and (a[1] == b[1] or '0.0.0.0' in (a[1], b[1]))


def _validate(changes, merged, replays):
    for key in PORTS:
        value = merged[key]
        if type(value) is not int or not (0 if key in ('http_port', 'battle_port') else 1) <= value <= 65535:
            raise ValueError(f'{key} 端口范围无效')
    for key in ('host_ip', 'web_bind', 'db_path', 'client_assets_dir', 'replay_path'):
        value = merged[key]
        if not isinstance(value, str) or len(value) > 4096 or any(ord(ch) < 32 for ch in value):
            raise ValueError(f'{key} 格式无效')
    if merged['host_ip']:
        IPv4Address(merged['host_ip'])
    IPv4Address(merged['web_bind'])
    for key in ('dns', 'replay_enabled'):
        if type(merged[key]) is not bool:
            raise ValueError(f'{key} 必须是布尔值')
    upstream = merged['dns_upstream']
    if not isinstance(upstream, list) or len(upstream) > 8 or any(not isinstance(ip, str) for ip in upstream):
        raise ValueError('DNS 上游最多填写 8 个 IPv4 地址')
    for ip in upstream:
        IPv4Address(ip)
    if merged['log_level'] not in ('DEBUG', 'INFO', 'WARN', 'ERROR'):
        raise ValueError('日志级别无效')
    if merged['cdn_mode'] not in ('off', 'relay', 'cache'):
        raise ValueError('CDN 模式无效')
    if 'db_path' in changes:
        from save_slot_manager import is_valid_save_db
        candidate = ROOT / merged['db_path']
        if (not merged['db_path'] or not candidate.is_file()
                or not is_valid_save_db(str(candidate)) or not os.access(candidate, os.W_OK)):
            raise ValueError('默认存档必须是有效且可写的已有数据库')
    if changes.keys() & {'replay_enabled', 'replay_path'} and merged['replay_enabled']:
        if merged['replay_path'] not in {item['path'] for item in replays}:
            raise ValueError('请选择可用的重放素材目录')
    entries = _endpoints(merged)
    for index, entry in enumerate(entries):
        if any(_overlap(entry, old) for old in entries[:index]):
            raise ValueError(f'监听端口冲突：{entry[1]}:{entry[2]}')
    if changes.keys() & {*PORTS, 'web_bind', 'dns'}:
        from linux_manager import probe_endpoints
        active = _endpoints(_current)
        errors = probe_endpoints(entry for entry in entries if not any(_overlap(entry, old) for old in active))
        if errors:
            raise ValueError('\n'.join(errors))


def save(changes, replays, *, restart=False):
    global _pending
    if not isinstance(changes, dict) or set(changes) - set(FIELDS):
        raise ValueError('启动配置参数无效')
    with _lock:
        if not _current:
            raise ValueError('启动配置尚未就绪')
        if _pending:
            raise ValueError('服务正在重启')
        if restart and (not _restart or not supervisor()):
            raise ValueError('当前进程由命令行直接运行，请手动重启')
        existing = _overrides()
        merged = _current | existing | changes
        _validate(changes, merged, replays)
        if changes:
            from linux_manager import backup_write
            backup_write(CONFIG, json.dumps({'overrides': existing | changes}, ensure_ascii=False, indent=2) + '\n')
            if 'log_level' in changes:
                from logger import set_level
                set_level(changes['log_level'])
        if restart:
            _pending = True
            # Give the HTTP worker time to deliver the acknowledgement before stopping listeners.
            timer = threading.Timer(0.75, _restart)
            timer.daemon = True
            timer.start()
        return merged
