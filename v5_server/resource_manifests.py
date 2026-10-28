"""Native-only manifest catalogue, derived profiles and per-platform selection.

Only the bundled trust catalogue or verified official synchronization can grant
native provenance. Browser input and filesystem discovery cannot grant it.
Generated profiles get a URL namespace; their BASE voice files stay untouched.
"""
from collections import OrderedDict
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

import cdn_integrity as integrity

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'resource_distribution.json'
NATIVE_PATH = ROOT / 'native_manifest_sources.json'
PLATFORMS = ('pc', 'android', 'ios')
CHANNELS = {'pc': '9', 'android': '8', 'ios': '7'}
_LOCK = threading.RLock()
_sha_cache = OrderedDict()
_pc_hint = None
_startup = {}
_route_rows = {}
_route_stamp = None


def _routes_stamp():
    result = []
    for path in (CONFIG_PATH, NATIVE_PATH):
        try:
            stat = path.stat()
            result.append((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except FileNotFoundError:
            result.append((str(path), None))
    return tuple(result)


def _json_bytes(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[1-9][0-9]{0,3}', str(value)):
        raise ValueError(f'{label}必须为1至9999的整数')
    return str(value)


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise ValueError('清单方案ID无效')
    return value


def _read_json(path, default):
    if not path.is_file():
        return copy.deepcopy(default)
    with path.open('r', encoding='utf-8-sig') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('清单管理配置格式无效')
    return data


def _state():
    raw = _read_json(CONFIG_PATH, {})
    if raw.get('version') == 2:
        return raw
    return dict(version=2, selections={}, profiles={}, native_sources={}, legacy=raw.get('platforms', {}))


def _save(state):
    integrity.publish_json(str(CONFIG_PATH), state)


def _folder(platform):
    integrity.platform(platform)
    return ROOT / 'static_resources' / platform


def _windows_host():
    return os.name == 'nt'


def _main_path(record):
    name = integrity.basename(record['assethash'])
    if record['origin'] == 'native':
        return _source_file(record, name)
    if record.get('legacy_storage'):
        return Path(integrity.safe_path(_folder(record['platform']), name))
    return Path(integrity.safe_path(_folder(record['platform']) / 'generated' / _id(record['id']), name))


def _source_file(record, filename):
    snapshot = Path(integrity.safe_path(_folder(record['platform']) / 'native' / _id(record['id']), filename))
    return snapshot if snapshot.exists() else Path(integrity.safe_path(_folder(record['platform']), filename))


def _seal_base(record):
    """Keep source bytes immutable even if official sync replaces flat voice files."""
    folder = _folder(record['platform']) / 'native' / _id(record['id'])
    files = {record['assethash']: record['sha256'], **record['voice_files']}
    for name, digest in files.items():
        source = _source_file(record, name)
        _verify_file(source, digest)
        destination = integrity.copy_verified(str(source), str(folder), integrity.ObjectSpec(name))
        _verify_file(Path(destination), digest)


def _verify_file(path, sha256):
    if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise ValueError('清单来源身份无效')
    with path.open('rb') as handle:
        stat = os.fstat(handle.fileno())
        key = (str(path.resolve()), sha256, integrity._fingerprint(stat, handle))
        if key in _sha_cache:
            _sha_cache.move_to_end(key)
            return
        digest = hashlib.sha256()
        while True:
            chunk = handle.read(integrity.CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise ValueError('清单内容与来源登记不一致')
        integrity.validate_open_file(handle, integrity.ObjectSpec(path.name))
        if integrity._fingerprint(os.fstat(handle.fileno()), handle) != key[-1]:
            raise ValueError('校验期间清单发生变化')
        _sha_cache[key] = True
        if len(_sha_cache) > 1024:
            _sha_cache.popitem(last=False)


def _native_records(state):
    sources = _read_json(NATIVE_PATH, {'sources': []}).get('sources', [])
    sources = list(sources) + list(state.get('native_sources', {}).values())
    rows = {}
    for source in sources:
        row = copy.deepcopy(source)
        _id(row['id'])
        integrity.platform(row['platform'])
        if row.get('provenance') not in ('bundled-official', 'official-download'):
            continue
        row.update(origin='native', label=f"原生 {row['app_version']}/{row['resource_version']} · {row['assethash'][-14:-6]}",
                   base_id=None, removed=False)
        try:
            _verify_file(_main_path(row), row['sha256'])
            if not row.get('voice_files'):
                raise ValueError('来源缺少配套语音清单')
            for name, digest in row['voice_files'].items():
                _verify_file(_source_file(row, name), digest)
            row.update(ready=True, error='')
        except (OSError, ValueError) as error:
            row.update(ready=False, error=str(error))
        rows[row['id']] = row
    return rows


def _legacy_profiles(state, natives):
    """Read old explicit aliases without promoting them to native BASEs."""
    result = {}
    legacy = dict(state.get('legacy', {}))
    # The previously shipped iOS 308 compatibility file is derived, never BASE.
    bundled = 'assethash_308_331_a1b758805b37e09ab0643ab909c95f2d.bytes'
    if 'ios' not in legacy and (_folder('ios') / bundled).is_file():
        legacy['ios'] = dict(alias_enabled=True, bundled_compat=True,
            alias=dict(app_version='308', resource_version='331', source_version='321', assethash=bundled))
    for platform, old in legacy.items():
        alias = old.get('alias') if old.get('alias_enabled') else None
        if not alias:
            continue
        base = next((r for r in natives.values() if r['platform'] == platform and r['ready']
                     and r['resource_version'] == str(alias.get('source_version'))), None)
        if not base:
            continue
        try:
            path = Path(integrity.safe_path(_folder(platform), alias['assethash']))
            raw = path.read_bytes()
            data = integrity.decode_manifest(raw, path.name)
            with _main_path(base).open('rb') as handle:
                expected = json.load(handle)
            expected.update(appVersion=int(alias['app_version']), buildCode=int(alias['resource_version']))
            if data != expected or hashlib.md5(raw).hexdigest() not in path.name:
                continue
            ident = 'legacy-' + platform + '-' + hashlib.sha256(raw).hexdigest()[:12]
            result[ident] = dict(id=ident, platform=platform, channel_id=CHANNELS[platform],
                origin='derived', label=f"旧兼容 {alias['app_version']}/{alias['resource_version']}",
                app_version=str(alias['app_version']), resource_version=str(alias['resource_version']),
                version_name=data['versionName'], assethash=path.name, sha256=hashlib.sha256(raw).hexdigest(),
                base_id=base['id'], legacy_storage=True, removed=False, created_at=0,
                bundled_compat=bool(old.get('bundled_compat')))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def _records(state):
    global _route_rows, _route_stamp
    rows = _native_records(state)
    derived = {**_legacy_profiles(state, rows), **state.get('profiles', {})}
    for ident, source in derived.items():
        row = copy.deepcopy(source)
        if row.get('origin') != 'derived' or row.get('id') != ident:
            continue
        base = rows.get(row.get('base_id'))
        try:
            if not base or base['origin'] != 'native' or not base['ready'] or base['platform'] != row['platform']:
                raise ValueError('原生BASE不可用')
            if row.get('removed'):
                raise ValueError('已移入回收区')
            _verify_file(_main_path(row), row['sha256'])
            row.update(ready=True, error='')
        except (OSError, ValueError, KeyError) as error:
            row.update(ready=False, error=str(error))
        rows[ident] = row
    _route_rows = rows
    _route_stamp = _routes_stamp()
    return rows


def _latest(rows, platform):
    candidates = [r for r in rows.values() if r['platform'] == platform and r['origin'] == 'native' and r['ready']]
    if not candidates:
        raise ValueError(f'{platform}没有完整且已验证的原生清单组')
    return max(candidates, key=lambda r: (int(r['resource_version']), int(r['app_version']), r.get('registered_at', 0), r['id']))


def _selection(state, rows, platform, use_startup=True):
    latest = _latest(rows, platform)
    saved = state.get('selections', {}).get(platform, {})
    fallback = rows.get(saved.get('fallback_id'))
    if not fallback or fallback['platform'] != platform or fallback['origin'] != 'native' or not fallback['ready']:
        fallback = latest
    choice = _startup.get(platform, saved.get('selected_id', 'auto')) if use_startup else saved.get('selected_id', 'auto')
    # Migrate an old fixed source without retaining global "follow gameplay".
    if choice == 'auto' and platform not in state.get('selections', {}):
        old = state.get('legacy', {}).get(platform, {})
        aliases = [r for r in rows.values() if r['platform'] == platform and r.get('legacy_storage') and r['ready']]
        if old.get('alias_enabled') and aliases:
            choice = aliases[0]['id']
        elif old.get('source_version'):
            match = next((r for r in rows.values() if r['platform'] == platform and r['origin'] == 'native'
                          and r['resource_version'] == old['source_version'] and r['ready']), None)
            choice = match['id'] if match else 'auto'
    selected = rows.get(choice) if choice != 'auto' else rows.get(_pc_hint) if platform == 'pc' and _pc_hint else latest
    reason = ''
    if not selected or selected['platform'] != platform or not selected['ready']:
        selected = fallback
        reason = '所选方案不可用，已使用本渠道保险方案'
    return dict(selected_id=selected['id'] if reason else choice, requested_id=choice,
                effective_id=selected['id'], fallback_id=fallback['id'],
                fallback_active=bool(reason), notice=reason, channel_id=CHANNELS[platform]), selected


def _public(row, rows):
    base = row if row['origin'] == 'native' else rows.get(row.get('base_id'), {})
    result = {k: row.get(k) for k in ('id', 'platform', 'channel_id', 'origin', 'label', 'app_version',
        'resource_version', 'version_name', 'assethash', 'base_id', 'ready', 'error', 'removed', 'created_at', 'registered_at')}
    result.update(base_label=base.get('label', ''), base_assethash=base.get('assethash', ''),
                  base_resource_version=base.get('resource_version', ''),
                  base_sha256=base.get('sha256', ''), voice_source='BASE原生语音（原样复用）',
                  md5=re.search(r'_([0-9a-f]{32})\.bytes$', row['assethash'])[1])
    return result


def get_status():
    import res_version_manager
    with _LOCK:
        state = _state()
        rows = _records(state)
        platforms = {}
        for platform in PLATFORMS:
            selection, selected = _selection(state, rows, platform)
            platforms[platform] = {**selection, 'effective': _public(selected, rows),
                                   'latest_native_id': _latest(rows, platform)['id']}
        return dict(scope='server', gameplay_version=res_version_manager.get_current_version(),
                    platforms=platforms, profiles=[_public(r, rows) for r in rows.values()],
                    native_only_base=True)


def _materialize(state, rows):
    for platform in PLATFORMS:
        selection, _ = _selection(state, rows, platform, use_startup=False)
        state.setdefault('selections', {}).setdefault(platform, dict(
            selected_id=selection['selected_id'], fallback_id=selection['fallback_id']))
    state['profiles'] = {k: {a: b for a, b in r.items() if a not in ('ready', 'error')}
                         for k, r in rows.items() if r['origin'] == 'derived'}
    state.pop('legacy', None)
    return state


def save_platform(data):
    if not isinstance(data, dict):
        raise ValueError('请求体必须为JSON对象')
    with _LOCK:
        state = _state()
        rows = _records(state)
        changes = data.get('selections')
        if changes is None:
            changes = {integrity.platform(data.get('platform')): data}
        if not isinstance(changes, dict) or not changes:
            raise ValueError('请选择要应用的渠道')
        updates = {}
        for platform, values in changes.items():
            integrity.platform(platform)
            if not isinstance(values, dict):
                raise ValueError('渠道参数无效')
            selection = values.get('selected_id')
            if selection != 'auto':
                row = rows.get(selection)
                if not row or row['platform'] != platform or not row['ready']:
                    raise ValueError('所选清单不属于本渠道或不可用')
            fallback = rows.get(values.get('fallback_id'))
            if not fallback or fallback['platform'] != platform or fallback['origin'] != 'native' or not fallback['ready']:
                raise ValueError('保险方案必须是本渠道完整可用的原生清单')
            updates[platform] = dict(selected_id=selection, fallback_id=fallback['id'])
        for platform, update in updates.items():
            selection = update['selected_id']
            fallback = rows[update['fallback_id']]
            _seal_base(fallback)
            if selection != 'auto':
                chosen = rows[selection]
                _seal_base(chosen if chosen['origin'] == 'native' else rows[chosen['base_id']])
        state = _materialize(state, rows)
        state['selections'].update(updates)
        _save(state)
        for platform in updates:
            _startup.pop(platform, None)
        return get_status()


def _preview(data):
    if not isinstance(data, dict):
        raise ValueError('请求体必须为JSON对象')
    state = _state()
    rows = _records(state)
    platform = integrity.platform(data.get('platform'))
    if str(data.get('channel_id', CHANNELS[platform])) != CHANNELS[platform]:
        raise ValueError('平台与渠道不匹配')
    base = rows.get(data.get('base_id'))
    if not base or base['origin'] != 'native' or base['platform'] != platform or not base['ready']:
        raise ValueError('BASE只允许本渠道已核验的原生清单，派生副本不能作为BASE')
    app = _number(data.get('target_app_version'), '安装包版本')
    build = _number(data.get('target_resource_version'), '资源版本')
    if (app, build) == (base['app_version'], base['resource_version']):
        raise ValueError('目标版本与BASE相同，无需生成副本')
    label = data.get('label') or f'兼容 {app}/{build} · BASE {base["app_version"]}/{base["resource_version"]}'
    if not isinstance(label, str):
        raise ValueError('方案名称必须为字符串')
    label = label.strip()
    if not label or len(label) > 48 or any(ord(c) < 32 for c in label):
        raise ValueError('方案名称须为1至48个可见字符')
    manifest = json.loads(_main_path(base).read_bytes())
    manifest.update(appVersion=int(app), buildCode=int(build))
    payload = _json_bytes(manifest)
    digest = hashlib.md5(payload).hexdigest()
    filename = f'assethash_{app}_{build}_{digest}.bytes'
    token = hashlib.sha256(_json_bytes([base['id'], base['sha256'], base['voice_files'], app, build, digest])).hexdigest()
    preview = dict(platform=platform, channel_id=CHANNELS[platform], base_id=base['id'],
        base_label=base['label'], base_assethash=base['assethash'], base_sha256=base['sha256'],
        app_version=app, resource_version=build, version_name=base['version_name'], label=label,
        assethash=filename, md5=digest, size=len(payload), preview_token=token,
        voice_files=list(base['voice_files']), voice_source='BASE原生语音（原样复用）')
    return state, rows, preview, payload


def preview_profile(data):
    with _LOCK:
        return _preview(data)[2]


def generate_profile(data):
    with _LOCK:
        state, rows, preview, payload = _preview(data)
        if data.get('preview_token') != preview['preview_token']:
            raise ValueError('预览已失效，请重新预览后生成')
        existing = next((r for r in rows.values() if r['platform'] == preview['platform']
                         and r['assethash'] == preview['assethash'] and not r.get('removed')), None)
        if existing:
            if existing['origin'] == 'native':
                raise ValueError('目标内容与已登记原生清单冲突')
            return dict(profile=_public(existing, rows), catalogue=get_status(), existed=True)
        if any(r['platform'] == preview['platform'] and r['label'] == preview['label'] and not r.get('removed') for r in rows.values()):
            raise ValueError('本渠道已有同名方案，请使用不同名称')
        ident = 'custom-' + uuid.uuid4().hex[:12]
        _seal_base(rows[preview['base_id']])
        record = {k: preview[k] for k in ('platform', 'channel_id', 'base_id', 'label', 'app_version',
                  'resource_version', 'version_name', 'assethash')}
        record.update(id=ident, origin='derived', sha256=hashlib.sha256(payload).hexdigest(),
                      created_at=int(time.time()), removed=False)
        integrity.publish_bytes(str(_main_path(record).parent), integrity.ObjectSpec(record['assethash']), payload)
        state = _materialize(state, rows)
        state['profiles'][ident] = record
        # Generation is not application. Channel selections are unchanged.
        _save(state)
        rows[ident] = {**record, 'ready': True, 'error': ''}
        return dict(profile=_public(rows[ident], rows), catalogue=get_status(), existed=False)


def _recycle(path, destination):
    """No physical deletion: Windows recycle bin, Linux recoverable local area."""
    path = path.resolve()
    if not path.is_relative_to((ROOT / 'static_resources').resolve()):
        raise ValueError('拒绝移除受管目录外的文件')
    if os.name == 'nt':
        from save_slot_manager import _recycle_save_files
        _recycle_save_files([path])
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.resolve().is_relative_to((ROOT / 'archive').resolve()):
            raise ValueError('回收区路径无效')
        os.replace(path, destination)


def remove_profile(ident):
    with _LOCK:
        state = _state()
        rows = _records(state)
        row = rows.get(_id(ident))
        if not row or row['origin'] != 'derived':
            raise ValueError('只允许移除派生副本，原生BASE受保护')
        state = _materialize(state, rows)
        if row.get('removed'):
            return get_status()
        recovery_id = ident + '_' + uuid.uuid4().hex[:12]
        recovery = ROOT / 'archive' / 'resource_manifests' / 'recovery' / recovery_id
        recovery.mkdir(parents=True, exist_ok=False)
        path = _main_path(row)
        if path.is_file():
            shutil.copy2(path, recovery / path.name)
        # Pin valid alternatives before the old namespace can disappear.
        for platform in PLATFORMS:
            selection, _ = _selection(state, rows, platform)
            if selection['selected_id'] == ident or selection['effective_id'] == ident:
                state.setdefault('selections', {})[platform] = dict(
                    selected_id=selection['fallback_id'], fallback_id=selection['fallback_id'])
        record = state['profiles'][ident]
        record.update(removed=True, recovery_id=recovery_id, removed_at=int(time.time()))
        _save(state)
        for platform in list(_startup):
            if _startup[platform] == ident:
                _startup.pop(platform)
        warning = ''
        if path.exists():
            try:
                _recycle(path, ROOT / 'archive/resource_manifests/recycled' / recovery_id / path.name)
            except OSError:
                warning = '分发已移除并留有恢复副本，原文件回收失败'
        return {**get_status(), 'operation_notice': warning or '已移入回收区，可恢复'}


def restore_profile(ident):
    with _LOCK:
        state = _state()
        record = state.get('profiles', {}).get(_id(ident))
        if not record or record.get('origin') != 'derived' or not record.get('removed'):
            raise ValueError('未找到可恢复的副本')
        recovery_id = _id(record.get('recovery_id'))
        source = ROOT / 'archive/resource_manifests/recovery' / recovery_id / integrity.basename(record['assethash'])
        _verify_file(source, record['sha256'])
        integrity.copy_verified(str(source), str(_main_path(record).parent), integrity.ObjectSpec(record['assethash']))
        record.update(removed=False)
        _save(state)
        return get_status()


def _effective(platform):
    state = _state()
    rows = _records(state)
    selection, row = _selection(state, rows, platform)
    return state, rows, selection, row


def get_platform_config(platform):
    """Compatibility adapter: assets belong to their real native source build."""
    import res_version_manager as versions
    with _LOCK:
        _, rows, _, row = _effective(platform)
        base = row if row['origin'] == 'native' else rows[row['base_id']]
        cfg = versions.get_version_config(base['resource_version']) if base['resource_version'] in versions.VERSION_CONFIGS else {}
        cfg.update(version=base['resource_version'], version_name=base['version_name'])
        cfg['assethash'] = {**cfg.get('assethash', {}), platform: base['assethash']}
        return cfg


def resolve_connection_version(platform, reported=''):
    """A copied manifest's advertised build never replaces its native BASE."""
    import res_version_manager as versions
    with _LOCK:
        _, rows, _, row = _effective(platform)
        base = row if row['origin'] == 'native' else rows[row['base_id']]
        native = base['resource_version']
        reason = 'configured_native_base'
        if reported and reported != row['resource_version']:
            aliases = [r for r in rows.values() if r['platform'] == platform and r['ready']
                       and r['resource_version'] == reported]
            origins = {r['resource_version'] if r['origin'] == 'native' else rows[r['base_id']]['resource_version'] for r in aliases}
            if any(r['origin'] == 'native' for r in aliases):
                native = reported; reason = 'reported_native_build'
            elif len(origins) == 1:
                native = origins.pop(); reason = 'reported_registered_source'
            elif not aliases and reported in versions.VERSION_CONFIGS:
                native = reported; reason = 'reported_native_build'
        versions.get_version_config(native)
        return dict(platform=platform, version=native, reported=reported,
                    advertised=row['resource_version'], reason=reason)


def distribution_entries(platform, app_version=None):
    """Use explicit installation metadata when available, otherwise a list."""
    with _LOCK:
        _, rows, _, selected = _effective(platform)
        base = selected if selected['origin'] == 'native' else rows[selected['base_id']]
        candidates = [selected] + ([base] if base['id'] != selected['id'] else [])
        candidates.extend(r for r in rows.values() if r.get('bundled_compat') and r['ready']
                          and r['base_id'] == selected['id'] and r['platform'] == platform)
        if app_version:
            candidates = [r for r in candidates if r['app_version'] == str(app_version)]
            if not candidates:
                fallback = _latest(rows, platform)
                if fallback['app_version'] == str(app_version):
                    candidates = [fallback]
        for row in candidates:
            _seal_base(row if row['origin'] == 'native' else rows[row['base_id']])
        return [dict(app_version=r['app_version'], resource_version=r['resource_version'],
            version_name=r['version_name'], assethash=r['assethash'], profile_id=r['id'],
            download_path=f'/{platform}/resources/profiles/{r["id"]}/', source_version=base['resource_version'])
            for r in candidates]


def get_ios_manifest(_cfg, app_version):
    entries = distribution_entries('ios', app_version)
    return entries[0] if entries else None


def resolve_resource(path, platform):
    """Return (handled, local path). None path means deny, never recapture."""
    match = re.fullmatch(r'/(pc|android|ios)/resources/profiles/([a-zA-Z0-9_-]+)/([^/]+)', path)
    if not match:
        if '/resources/profiles/' in path:
            return True, None
        filename = path.rsplit('/', 1)[-1]
        if filename.startswith('assethash_'):
            with _LOCK:
                for row in _state().get('profiles', {}).values():
                    if row['platform'] == platform and row['assethash'] == filename:
                        if row.get('removed'):
                            return True, None
                        resolved = _records(_state()).get(row['id'])
                        if not resolved or not resolved.get('ready'):
                            return True, None
                        return True, str(_main_path(resolved))
        if filename.startswith(('voice_package_list_', 'voice_hash_')):
            with _LOCK:
                _, rows, _, selected = _effective(platform)
                candidates = [selected] + [r for r in rows.values() if r.get('bundled_compat') and r['ready']
                    and r['platform'] == platform and r['base_id'] == selected['id']]
                for row in candidates:
                    handled, resolved = resolve_resource(
                        f'/{platform}/resources/profiles/{row["id"]}/{filename}', platform)
                    if handled and resolved:
                        return True, resolved
        return False, None
    if match[1] != platform:
        return True, None
    filename = integrity.basename(match[3])
    with _LOCK:
        # SDK selection and catalogue reads populate this metadata snapshot.
        # Per-asset routing only checks two tiny config identities, not 28 files.
        if _route_stamp != _routes_stamp():
            _records(_state())
        row = _route_rows.get(match[2])
        if not row or row.get('removed') or not row.get('ready') or row['platform'] != platform:
            return True, None
        row = {**row, 'origin': row.get('origin', 'native')}
        if filename == row['assethash']:
            _verify_file(_main_path(row), row['sha256'])
            return True, str(_main_path(row))
        if re.fullmatch(r'[0-9a-fA-F]{32}\.ys', filename):
            return False, None
        base = row if row['origin'] == 'native' else _route_rows.get(row['base_id'])
        if not base:
            return True, None
        voice_version = f'{base["app_version"]}00{base["resource_version"]}'
        aliases = {f'voice_package_list_{row["app_version"]}_{row["resource_version"]}.bytes':
                   f'voice_package_list_{base["app_version"]}_{base["resource_version"]}.bytes'}
        aliases.update({f'voice_hash_{lang}_{row["app_version"]}00{row["resource_version"]}.bytes':
                        f'voice_hash_{lang}_{voice_version}.bytes' for lang in ('ja', 'zh')})
        actual = aliases.get(filename, filename)
        if actual not in base.get('voice_files', {}):
            return True, None
        actual_path = _source_file(base, actual)
        _verify_file(actual_path, base['voice_files'][actual])
        return True, str(actual_path)


def register_official_bundle(platform, info):
    """Internal only: invoked after verified official synchronization."""
    integrity.platform(platform)
    filename = integrity.basename(info['assethash'])
    folder = _folder(platform)
    raw = (folder / filename).read_bytes()
    data = integrity.decode_manifest(raw, filename)
    digest = hashlib.md5(raw).hexdigest()
    if digest not in filename:
        raise ValueError('官方主清单摘要错误')
    app, build = str(data['appVersion']), str(data['buildCode'])
    voice = [f'voice_package_list_{app}_{build}.bytes', *[f'voice_hash_{lang}_{app}00{build}.bytes' for lang in ('ja', 'zh')]]
    record = dict(id=f'native-{platform}-{digest[:12]}', platform=platform, channel_id=CHANNELS[platform],
        app_version=app, resource_version=build, version_name=data['versionName'], assethash=filename,
        sha256=hashlib.sha256(raw).hexdigest(), voice_files={}, provenance='official-download',
        registered_at=time.time_ns() // 1_000_000)
    for name in voice:
        if not integrity.valid_file(folder / name, integrity.ObjectSpec(name)):
            raise ValueError('官方语音清单未完整就绪')
        record['voice_files'][name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    with _LOCK:
        state = _state()
        declared = _read_json(NATIVE_PATH, {'sources': []}).get('sources', [])
        declared = [*declared, *state.get('native_sources', {}).values()]
        same = next((r for r in declared if r['platform'] == platform and r['sha256'] == record['sha256']
                     and r['voice_files'] == record['voice_files']), None)
        if same:
            record['id'] = same['id']
        else:
            identity = hashlib.sha256(_json_bytes([record['sha256'], record['voice_files']])).hexdigest()
            record['id'] = f'native-{platform}-{identity[:12]}'
        _seal_base(record)
        state.setdefault('native_sources', {})[record['id']] = record
        _save(state)


def commit_official_bundles(plans, commit_version):
    """Hide intermediate catalogue states; restore prior selection on commit failure."""
    with _LOCK:
        before = _state()
        try:
            for platform, info in plans.items():
                register_official_bundle(platform, info)
            return commit_version()
        except Exception:
            # The immutable snapshots are retained, but a failed sync must not
            # change auto/default channel selection or publish partial authority.
            _save(before)
            raise


def configure_startup(selector='auto', client_dir=None, profiles=()):
    """Initialize channel values; never change gameplay or non-PC from probing."""
    global _pc_hint
    import res_version_manager as versions
    with _LOCK:
        state = _state()
        rows = _records(state)
        _pc_hint = None
        _startup.clear()
        if _windows_host() and str(selector).lower() == 'auto':
            info = versions.detect_client_resource_version(client_dir)
            _pc_hint = next((r['id'] for r in rows.values() if r['origin'] == 'native'
                and r['platform'] == 'pc' and r['ready'] and r['sha256'] == info.get('sha256')), None)
        elif str(selector).isdigit():
            for platform in PLATFORMS:
                found = next((r for r in rows.values() if r['origin'] == 'native' and r['ready']
                    and r['platform'] == platform and r['resource_version'] == str(selector)), None)
                _startup[platform] = found['id'] if found else _latest(rows, platform)['id']
        elif str(selector).lower() != 'auto':
            profiles = (*profiles, str(selector))
        for value in profiles:
            if '=' not in value:
                raise ValueError('清单参数格式为 pc=方案名称或ID（或android/ios）')
            platform, name = value.split('=', 1)
            integrity.platform(platform)
            if name == 'auto':
                _startup[platform] = 'auto'
                continue
            matches = [r for r in rows.values() if r['platform'] == platform and r['ready']
                       and name in (r['id'], r['label'])]
            if len(matches) != 1:
                raise ValueError(f'{platform}方案不存在或名称不唯一: {name}')
            _startup[platform] = matches[0]['id']
        return get_status()
