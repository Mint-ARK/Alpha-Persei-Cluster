"""Immutable main-manifest plans and a preflight cache index for execution groups."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

import cdn_integrity as integrity
import cdn_proxy

SCOPES = ('required', 'extended', 'all')
MANIFEST_NAME = re.compile(r'assethash_\d+_\d+_[0-9a-fA-F]{32}\.bytes')


def resolve_source(platform, profile_id=None, filename=None):
    integrity.platform(platform)
    if profile_id:
        import resource_manifests as manifests
        with manifests._LOCK:
            rows = manifests._records(manifests._state())
            row = rows.get(profile_id)
            if not row or row['platform'] != platform or not row['ready'] or row.get('removed'):
                raise ValueError('清单方案不属于本渠道或不可用')
            return manifests._main_path(row), row['label'], row['assethash']
    if not isinstance(filename, str) or not MANIFEST_NAME.fullmatch(filename):
        raise ValueError('请选择本渠道已保存的完整主清单')
    path = Path(integrity.safe_path(Path(cdn_proxy.DEFAULT_STATIC_DIR)/platform, filename))
    return path, filename, filename


def freeze_source(root, platform, profile_id=None, filename=None):
    source, label, name = resolve_source(platform, profile_id, filename)
    spec = integrity.ObjectSpec(name)
    if not integrity.valid_file(source, spec):
        raise ValueError('主清单缺失或校验失败，请先准备清单')
    digest = spec.md5
    folder = Path(root)/'blueprints'/platform/digest
    path = integrity.copy_verified(str(source), str(folder), spec)
    return dict(platform=platform, filename=name, label=label, snapshot=f'blueprints/{platform}/{digest}/{name}',
                sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())


class IndexManager:
    def __init__(self, root):
        self.root = Path(root)
        self.receipts = {}

    def _manifest(self, task):
        source = task['source']
        path = (self.root/source['snapshot']).resolve()
        if not path.is_relative_to((self.root/'blueprints').resolve()):
            raise ValueError('蓝图快照路径无效')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('任务蓝图已被修改')
        if hashlib.md5(raw).hexdigest() != integrity.ObjectSpec(source['filename']).md5:
            raise ValueError('任务蓝图与文件名摘要不一致')
        return integrity.decode_manifest(raw, source['filename'])

    def blueprint(self, task):
        data = self._manifest(task)
        items = integrity.manifest_items(data)
        pending = data.get('assetPendDict') or {}
        if not isinstance(pending, dict) or type(pending.get('disable', False)) is not bool:
            raise ValueError('延迟分组配置无效')
        groups = {} if pending.get('disable') else pending.get('pendDict', {})
        if not isinstance(groups, dict):
            raise ValueError('延迟分组配置无效')
        extended = set()
        for names in groups.values():
            if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                raise ValueError('延迟分组路径无效')
            extended.update(names)
        scope = task['scope']
        if scope not in SCOPES:
            raise ValueError('下载范围无效')
        selected = set(items) if scope == 'all' else set(items) & extended if scope == 'extended' else set(items)-extended
        unresolved = sorted(extended-set(items)) if scope != 'required' else []
        objects = {}
        for name in sorted(selected):
            digest, size = items[name]
            key = (task['platform'], digest+'.ys')
            spec = integrity.ObjectSpec(digest+'.ys', size, digest)
            if key in objects and objects[key] != spec:
                raise ValueError('同一对象大小冲突')
            objects[key] = spec
        return objects, unresolved

    def scan(self, platforms, token):
        """One directory enumeration per platform, no recursive cache traversal."""
        cache = {}
        for platform in platforms:
            entries = {}
            for directory in cdn_proxy.cache_roots(platform):
                integrity.checkpoint(token)
                try:
                    with os.scandir(directory) as iterator:
                        for offset, entry in enumerate(iterator):
                            if offset % 128 == 0:
                                integrity.checkpoint(token)
                            if re.fullmatch(r'[0-9a-fA-F]{32}\.ys', entry.name) and entry.is_file(follow_symlinks=False):
                                entries.setdefault(entry.name, []).append(entry.path)
                except FileNotFoundError:
                    continue
            cache[platform] = entries
        return cache

    def valid(self, paths, spec, token):
        for path in paths:
            try:
                with open(path, 'rb') as handle:
                    stat = os.fstat(handle.fileno())
                    identity = integrity._fingerprint(stat, handle)
                    key = (path, spec.md5, spec.size)
                    if self.receipts.get(key) == identity:
                        return path
                    integrity.validate_open_file(handle, spec, cancel=token)
                    self.receipts[key] = identity
                    return path
            except integrity.Cancelled:
                raise
            except (OSError, ValueError):
                continue
        return None

    def build(self, tasks, token, prepared=None):
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
        cache = self.scan({t['platform'] for t in tasks}, token)
        missing = []
        for offset, (key, item) in enumerate(union.items()):
            if offset % 64 == 0:
                integrity.checkpoint(token)
            present = self.valid(cache[key[0]].get(key[1], []), item['spec'], token)
            for task_id in item['subscribers']:
                summary = summaries[task_id]
                category = 'cached' if present else 'needed'
                summary[category+'_files'] += 1
                summary[category+'_bytes'] += item['spec'].size
            if not present:
                missing.append(item)
        for task_id, summary in summaries.items():
            directory = cdn_proxy.get_data_dir(next(t['platform'] for t in tasks if t['id'] == task_id))
            summary['output_dir'] = directory
            probe = Path(directory)
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            summary['free_bytes'] = shutil.disk_usage(probe).free
        return missing, summaries
