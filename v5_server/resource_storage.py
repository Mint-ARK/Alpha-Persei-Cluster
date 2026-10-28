"""Server-owned shared resource root. No filesystem writes during import/read."""
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

import cdn_integrity as integrity

SERVER = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get('AETHERGAZER_SERVER_CONFIG') or SERVER/'server_config.json')
if not CONFIG_PATH.is_absolute():
    CONFIG_PATH = SERVER/CONFIG_PATH
PLATFORMS = ('pc', 'android', 'ios')
LEGACY = {'pc': r'D:\PC_DATA', 'android': r'D:\Android_DATA', 'ios': r'D:\iOS_DATA'}


def normalize(value, base=SERVER):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('请输入有效的服务器目录')
    path = Path(value.strip()).expanduser()
    return str((path if path.is_absolute() else Path(base)/path).resolve())


def volumes():
    if os.name != 'nt':
        return [str(p) for p in (Path('/'), Path('/mnt'), Path('/media'), Path('/data'), SERVER.parent) if p.is_dir()]
    import ctypes
    result = []
    for letter in 'DEFGHIJKLMNOPQRSTUVWXYZABC':
        path = letter + ':\\'
        if ctypes.windll.kernel32.GetDriveTypeW(path) == 3 and os.path.isdir(path):
            result.append(path)
    return result


def recommend():
    if os.name != 'nt':
        return str(SERVER/'cdn_cache')
    candidates = []
    system = os.path.normcase(os.environ.get('SystemDrive', 'C:') + '\\')
    for path in volumes():
        if os.path.normcase(path) == system or path[:2].upper() == 'C:' or not os.access(path, os.W_OK):
            continue
        try:
            candidates.append((path[:2].upper() == 'D:', shutil.disk_usage(path).free, path))
        except OSError:
            pass
    return os.path.join(max(candidates)[2], 'AlphaPersei_CDN_Cache') if candidates else ''


class Storage:
    def __init__(self, config=None):
        self.config = Path(config or CONFIG_PATH).resolve()
        self._lock = threading.RLock()
        self.root = ''
        self.read_roots = []
        self.warning = ''
        self._signature = None
        self.root_selected = False
        try:
            self._accept(self._read())
        except (ValueError, OSError, TypeError) as error:
            self.warning = '资源存储配置读取失败：' + str(error)

    def _read(self):
        data = json.loads(self.config.read_text(encoding='utf-8-sig')) if self.config.is_file() else {}
        if not isinstance(data, dict) or not isinstance(data.get('storage', {}), dict):
            raise ValueError('存储配置格式无效')
        return data

    def _accept(self, data):
        storage = data.get('storage', {})
        configured = storage.get('cdn_cache_dir')
        factory_default = os.name == 'nt' and configured in ('./cdn_cache', 'cdn_cache', '.\\cdn_cache') and not storage.get('cdn_root_selected')
        self.root_selected = bool(configured) and not factory_default
        value = os.environ.get('CDN_CACHE_DIR') or (None if factory_default else configured) or recommend()
        self.root = normalize(value, self.config.parent) if value else ''
        history = storage.get('cdn_read_roots', [])
        if not isinstance(history, list) or not all(isinstance(p, str) for p in history):
            raise ValueError('历史缓存目录格式无效')
        self.read_roots = list(dict.fromkeys(normalize(p, self.config.parent) for p in history if p.strip()))
        if factory_default:
            self.read_roots = list(dict.fromkeys([*self.read_roots, normalize(configured, self.config.parent)]))
        self.warning = ''

    def directories(self):
        with self._lock:
            return {p: normalize(os.environ[f'CDN_{p.upper()}_DIR'], self.config.parent)
                    if os.environ.get(f'CDN_{p.upper()}_DIR') else os.path.join(self.root, p) if self.root else '' for p in PLATFORMS}

    def refresh(self):
        """Called once per transfer session, never inside the indexed file loop."""
        def signature():
            try:
                stat = self.config.stat()
                return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size
            except FileNotFoundError:
                return ()
        with self._lock:
            current = signature()
            if current == self._signature:
                return
            for _ in range(3):
                data = self._read()
                after = signature()
                if after == current:
                    self._accept(data)
                    self._signature = after
                    return
                current = after
            raise ValueError('资源存储配置正在更新，请稍后重试')

    def history(self, platform):
        with self._lock:
            return [os.path.join(root, platform) for root in self.read_roots if root != self.root]

    def status(self):
        with self._lock:
            probe = Path(self.root or self.config.parent)
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            try:
                free = shutil.disk_usage(probe).free
            except OSError:
                free = None
            return dict(root=self.root, root_selected=self.root_selected, directories=self.directories(), read_roots=list(self.read_roots),
                config_path=str(self.config), recommended_root=recommend(), free_bytes=free,
                server_os='windows' if os.name == 'nt' else 'linux' if os.name == 'posix' else os.name,
                overridden=any(os.environ.get(key) for key in ('CDN_CACHE_DIR', 'CDN_PC_DIR', 'CDN_ANDROID_DIR', 'CDN_IOS_DIR')),
                warning=self.warning)

    def apply(self, value):
        root = normalize(value, self.config.parent)
        with self._lock:
            if self.status()['overridden']:
                raise ValueError('当前下载目录由CDN环境变量指定，请移除覆盖并重启后再修改')
            # Same byte-range lock is used by the Windows shell config writer.
            with integrity.object_lock(str(self.config), deadline=time.monotonic()+5):
                data = self._read()
                storage = data.setdefault('storage', {})
                history = storage.get('cdn_read_roots', [])
                if not isinstance(history, list) or not all(isinstance(p, str) for p in history):
                    raise ValueError('历史缓存目录格式无效')
                for p in PLATFORMS:
                    directory = Path(root)/p
                    directory.mkdir(parents=True, exist_ok=True)
                    # Create a new entry: modifying an old marker does not prove
                    # permission to create the downloader's new part files.
                    with (directory/('.cdn-write-test-'+uuid.uuid4().hex)).open('xb') as handle:
                        handle.write(b'1')
                        handle.flush()
                        os.fsync(handle.fileno())
                roots = [normalize(p, self.config.parent) for p in history if p.strip()] + self.read_roots
                if self.root and self.root != root:
                    roots.append(self.root)
                storage.update(cdn_cache_dir=root, cdn_root_selected=True,
                    cdn_read_roots=list(dict.fromkeys(p for p in roots if p != root)))
                # Already holding the shared config lock; publish_json takes that
                # lock itself, so use its verified atomic-copy primitive instead.
                part = str(self.config) + '.' + uuid.uuid4().hex + '.part'
                raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
                with open(part, 'xb') as handle:
                    if handle.write(raw) != len(raw):
                        raise OSError('Short storage config write')
                    handle.flush(); os.fsync(handle.fileno())
                integrity._publish(part, str(self.config), integrity.ObjectSpec(self.config.name), None, None)
                self._accept(data)
                return self.status()


def browse(value=None):
    roots = volumes()
    if not value:
        return dict(path='', parent=None, directories=[dict(name=p, path=p) for p in roots], roots=roots, truncated=False)
    path = Path(normalize(value, storage.config.parent))
    if not path.is_dir():
        raise ValueError('服务器目录不存在，请选择已有的父目录，或直接填写新目录路径')
    entries = []
    truncated = False
    with os.scandir(path) as items:
        for offset, item in enumerate(items):
            if offset >= 20000:
                truncated = True; break
            try:
                if item.is_dir(follow_symlinks=False) and not item.name.startswith('.'):
                    entries.append(dict(name=item.name, path=item.path))
                    if len(entries) >= 201:
                        truncated = True; break
            except OSError:
                continue
    return dict(path=str(path), parent=str(path.parent) if path.parent != path else '',
        directories=sorted(entries, key=lambda p:p['name'].casefold())[:200], roots=roots, truncated=truncated)


storage = Storage()
