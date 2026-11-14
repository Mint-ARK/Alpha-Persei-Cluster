"""Immutable resource and voice plans with a preflight cache index."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time

import cdn_integrity as integrity
import cdn_proxy

SCOPES = ('required', 'extended', 'all')
VOICE_LANGUAGES = ('zh', 'ja')
MANIFEST_NAME = re.compile(r'assethash_\d+_\d+_[0-9a-fA-F]{32}\.bytes')
ASSET_NAME = re.compile(r'[0-9a-fA-F]{32}\.ys')
RECEIPT_CHECKPOINT_SECONDS = 30
_log = logging.getLogger(__name__)


def saved_main_manifests(folder):
    try:
        with os.scandir(folder) as entries:
            return sorted(entry.name for entry in entries
                          if MANIFEST_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False))
    except FileNotFoundError:
        return []


def voice_manifest_names(filename, languages):
    _, app, build, _ = filename.split('_')
    package = f'voice_package_list_{app}_{build}.bytes'
    hashes = {lang: f'voice_hash_{lang}_{app}00{build}.bytes' for lang in languages}
    return package, hashes


def resolve_source(platform, profile_id=None, filename=None, languages=()):
    integrity.platform(platform)
    if not profile_id and (not isinstance(filename, str) or not MANIFEST_NAME.fullmatch(filename)):
        raise ValueError('请选择本渠道已保存的完整主清单')
    if profile_id or languages:
        import resource_manifests as manifests
        with manifests._LOCK:
            rows = manifests._records(manifests._state())
            candidates = [row for row in rows.values() if row['platform'] == platform and not row.get('removed')
                          and (row['id'] == profile_id if profile_id else row['assethash'] == filename)]
            ready = [row for row in candidates if row['ready']]
            if len(ready) > 1:
                raise ValueError('同名主清单有多个已登记来源，请选择具体清单方案')
            if ready:
                row = ready[0]
                base = None
                if languages:
                    base = row if row['origin'] == 'native' else rows[row['base_id']]
                return manifests._main_path(row), row['label'], base
            if profile_id or candidates:
                raise ValueError('清单方案不属于本渠道或不可用')
    folder = Path(cdn_proxy.DEFAULT_STATIC_DIR)/platform
    path = Path(integrity.safe_path(folder, filename))
    return path, filename, None


def freeze_source(root, platform, profile_id=None, filename=None, languages=()):
    source, label, base = resolve_source(platform, profile_id, filename, languages)
    spec = integrity.ObjectSpec(source.name)
    if not integrity.valid_file(source, spec):
        raise ValueError('主清单缺失或校验失败，请先准备清单')
    result = dict(platform=platform, label=label, **_freeze_manifest(root, platform, source, spec))
    if languages:
        package, hashes = voice_manifest_names(base['assethash'] if base else source.name, languages)
        snapshots = {}
        for name in (package, *hashes.values()):
            if base:
                import resource_manifests as manifests
                expected_sha256 = base['voice_files'].get(name)
                if not expected_sha256:
                    raise ValueError(f'原生来源缺少配套语音清单：{name}')
                path = manifests._source_file(base, name)
            else:
                expected_sha256 = None
                path = Path(integrity.safe_path(source.parent, name))
            if not path.is_file():
                raise ValueError(f'缺少 {name}，请先准备主清单和配套语音清单')
            with path.open('rb') as handle:
                raw = handle.read(integrity.MAX_MANIFEST_BYTES + 1)
            data = integrity.decode_manifest(raw, name)
            if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
                raise ValueError(f'语音清单与原生来源登记不一致：{name}')
            if name == package:
                listed = {entry['name'] for entry in data['infos']}
                if not set(languages).issubset(listed):
                    raise ValueError('语音包总表缺少所选语种')
            spec = integrity.ObjectSpec(name, len(raw), hashlib.md5(raw).hexdigest())
            snapshots[name] = _freeze_manifest(root, platform, path, spec)
        result['voice_package'] = snapshots[package]
        result['voice_hashes'] = {lang: snapshots[name] for lang, name in hashes.items()}
    return result


def _freeze_manifest(root, platform, source, spec):
    relative = Path('blueprints')/platform/spec.md5/spec.filename
    path = integrity.copy_verified(str(source), str(Path(root)/relative.parent), spec)
    return dict(filename=spec.filename, snapshot=relative.as_posix(),
                sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())


class IndexManager:
    def __init__(self, root):
        self.root = Path(root)
        self.receipt_path = self.root/'verified_assets.json'
        self.receipts = {}
        self._receipts_loaded = False
        self._receipts_lock = threading.Lock()
        self._dirty = False
        self._last_checkpoint = 0.0

    def _load_receipts(self):
        if self._receipts_loaded:
            return
        self._receipts_loaded = True
        try:
            with self.receipt_path.open('r', encoding='utf-8') as handle:
                data = json.load(handle)
            if data['version'] != 1 or not isinstance(data['files'], dict):
                raise ValueError('Unsupported verified asset index')
            self.receipts = {path: tuple(identity) for path, identity in data['files'].items()
                             if isinstance(identity, list) and len(identity) == 6
                             and all(type(value) is int for value in identity)
                             and identity[1] and identity[-1]}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            _log.warning('无法读取已验证资产索引，将重新校验文件：%s', error)

    def checkpoint_receipts(self, *, force=False):
        now = time.monotonic()
        if not force and now - self._last_checkpoint < RECEIPT_CHECKPOINT_SECONDS:
            return
        with self._receipts_lock:
            if not self._dirty:
                return
            files = self.receipts.copy()
            self._dirty = False
            self._last_checkpoint = now
        try:
            # This file is a rebuildable cache. Keep the previous complete
            # snapshot visible until the replacement has been written to disk.
            staged = self.receipt_path.with_suffix('.json.part')
            payload = json.dumps(dict(version=1, files=files), separators=(',', ':'))
            with staged.open('w', encoding='utf-8') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, self.receipt_path)
            integrity._sync_directory(self.root)
        except OSError as error:
            with self._receipts_lock:
                self._dirty = True
            _log.warning('无法保存已验证资产索引：%s', error)

    def record_verified(self, path, identity):
        key = os.path.normcase(path)
        with self._receipts_lock:
            if identity[1] and identity[-1] and self.receipts.get(key) != identity:
                self.receipts[key] = identity
                self._dirty = True

    def _manifest(self, source):
        path = (self.root/source['snapshot']).resolve()
        if not path.is_relative_to((self.root/'blueprints').resolve()):
            raise ValueError('蓝图快照路径无效')
        with path.open('rb') as handle:
            raw = handle.read(integrity.MAX_MANIFEST_BYTES + 1)
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('任务蓝图已被修改')
        md5 = integrity.ObjectSpec(source['filename']).md5
        if md5 and hashlib.md5(raw).hexdigest() != md5:
            raise ValueError('任务蓝图与文件名摘要不一致')
        return integrity.decode_manifest(raw, source['filename'])

    def blueprint(self, task):
        source = task['source']
        data = self._manifest(source)
        pending = data.get('assetPendDict') or {}
        if not isinstance(pending, dict) or type(pending.get('disable', False)) is not bool:
            raise ValueError('延迟分组配置无效')
        if task['scope'] not in SCOPES:
            raise ValueError('下载范围无效')
        objects = {}
        unresolved = []
        if task['kind'] == 'assets':
            groups = {} if pending.get('disable') else pending.get('pendDict', {})
            if not isinstance(groups, dict):
                raise ValueError('延迟分组配置无效')
            extended = set()
            for names in groups.values():
                if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                    raise ValueError('延迟分组路径无效')
                extended.update(names)
            items = integrity.manifest_items(data)
            self._add_objects(objects, task, items, extended)
            if task['scope'] != 'required':
                unresolved = sorted(extended - items.keys())
        if task.get('voice_languages'):
            delayed_voice = [] if pending.get('disable') else pending.get('allVoice')
            if task['scope'] != 'all' and (not isinstance(delayed_voice, list) or
                                           any(not isinstance(name, str) for name in delayed_voice)):
                raise ValueError('主清单缺少有效的语音延后名单，请选择全部语音')
            voice_extended = set(delayed_voice) if task['scope'] != 'all' else set()
            package = self._manifest(source['voice_package'])
            listed = {entry['name']: str(entry['version']) for entry in package['infos']}
            for lang in task['voice_languages']:
                voice = self._manifest(source['voice_hashes'][lang])
                if listed.get(lang) != str(voice['version']):
                    raise ValueError('语音索引与语音包总表版本不一致')
                self._add_objects(objects, task, integrity.manifest_items(voice), voice_extended)
        return objects, unresolved

    @staticmethod
    def _add_objects(objects, task, items, extended):
        scope = task['scope']
        selected = set(items) if scope == 'all' else items.keys() & extended if scope == 'extended' else items.keys() - extended
        for name in sorted(selected):
            digest, size = items[name]
            key = (task['platform'], digest+'.ys')
            spec = integrity.ObjectSpec(digest+'.ys', size, digest)
            if key in objects and objects[key] != spec:
                raise ValueError('同一对象大小冲突')
            objects[key] = spec

    def scan(self, destinations, token):
        """Index only the selected download directory for each platform."""
        cache = {}
        for platform, directory in destinations.items():
            integrity.checkpoint(token)
            if os.name == 'nt':
                try:
                    cache[platform] = {name: (os.path.join(directory, name), identity)
                                       for name, identity in integrity.windows_directory_files(directory, token)
                                       if ASSET_NAME.fullmatch(name)}
                    continue
                except integrity.Cancelled:
                    raise
                except OSError:
                    pass  # Some filesystems do not expose IDs via directory queries.
            entries = {}
            with os.scandir(directory) as iterator:
                for offset, entry in enumerate(iterator):
                    if offset % 128 == 0:
                        integrity.checkpoint(token)
                    if ASSET_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                        entries[entry.name] = (entry.path, None)
            cache[platform] = entries
        return cache

    def valid(self, entry, spec, token):
        if entry is None:
            return False
        path, listed_identity = entry
        key = os.path.normcase(path)
        receipt = self.receipts.get(key)
        if (listed_identity and receipt and listed_identity[2] == spec.size
                and integrity.windows_directory_identity_matches(listed_identity, receipt)):
            return True
        try:
            with open(path, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    return False
                identity = integrity._fingerprint(info, handle)
                if info.st_size == spec.size and receipt == identity:
                    return True
                integrity.validate_open_file(handle, spec, cancel=token)
                if integrity._fingerprint(os.fstat(handle.fileno()), handle) == identity:
                    self.record_verified(path, identity)
                    return True
        except integrity.Cancelled:
            raise
        except (OSError, ValueError):
            pass
        with self._receipts_lock:
            if self.receipts.pop(key, None) is not None:
                self._dirty = True
        return False

    def build(self, tasks, token, prepared=None, *, progress=None):
        self._load_receipts()
        self._last_checkpoint = time.monotonic()
        prepared = prepared or {}
        union, summaries = {}, {}
        for task in tasks:
            integrity.checkpoint(token)
            objects, unresolved = prepared.get(task['id']) or self.blueprint(task)
            summaries[task['id']] = dict(total_files=len(objects), total_bytes=sum(s.size for s in objects.values()),
                cached_files=0, cached_bytes=0, done_files=0, done_bytes=0, failed_files=0,
                unresolved_count=len(unresolved), unresolved_paths=unresolved[:200], failures=[],
                needed_files=0, needed_bytes=0)
            for key, spec in objects.items():
                if key in union and union[key]['spec'] != spec:
                    raise ValueError('跨任务对象规格冲突')
                union.setdefault(key, dict(key=key, spec=spec, subscribers=[]))['subscribers'].append(task['id'])
        destinations = {platform: cdn_proxy.get_data_dir(platform) for platform in {t['platform'] for t in tasks}}
        if progress is not None:
            progress(0, len(union))
        cache = self.scan({platform: os.path.realpath(directory) for platform, directory in destinations.items()}, token)
        missing = []
        last_progress = time.monotonic()
        for offset, (key, item) in enumerate(union.items()):
            if offset % 64 == 0:
                integrity.checkpoint(token)
                now = time.monotonic()
                if progress is not None and now - last_progress >= .25:
                    progress(offset, len(union))
                    last_progress = now
                self.checkpoint_receipts()
            present = self.valid(cache[key[0]].get(key[1]), item['spec'], token)
            for task_id in item['subscribers']:
                summary = summaries[task_id]
                category = 'cached' if present else 'needed'
                summary[category+'_files'] += 1
                summary[category+'_bytes'] += item['spec'].size
            if not present:
                item['output_dir'] = destinations[key[0]]
                missing.append(item)
        free_bytes = {platform: shutil.disk_usage(directory).free for platform, directory in destinations.items()}
        for task in tasks:
            platform = task['platform']
            summaries[task['id']].update(output_dir=destinations[platform], free_bytes=free_bytes[platform])
        self.checkpoint_receipts(force=True)
        return missing, summaries
