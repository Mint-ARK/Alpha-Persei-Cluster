"""Bind each GM request to a save without changing the live game's database."""
from pathlib import Path
from urllib.parse import unquote
import os
from flask import g, has_request_context, request


def catalog():
    from save_slot_manager import get_slot_manager, is_valid_save_db, extract_save_metadata, slot_id_to_port
    manager = get_slot_manager()
    registry = manager._load_registry()  # Listing must not rewrite the slot registry.
    slots = {name: sid for sid, name in registry.items()}
    active = manager.get_active_db()
    rows = []
    for path in sorted(manager.server_dir.glob('*.db')):
        name = path.name.lower()
        if path.is_symlink() or name.startswith(('_', '.', 'test_')) or '_bak_' in name or '_backup_' in name:
            continue
        if not is_valid_save_db(str(path)):
            continue
        uid, nick, level = extract_save_metadata(str(path))
        sid = slots.get(path.name)
        rows.append(dict(name=path.stem, filename=path.name, uid=uid, nick=nick, level=level,
                         slot_id=sid, port=slot_id_to_port(sid, manager.base_port, manager.game_port) if sid else None,
                         mounted=bool(active and os.path.normcase(os.path.abspath(active)) == os.path.normcase(str(path.resolve())))))
    return rows


def bind():
    filename = request.headers.get('X-Save-Name', '')
    if not filename:
        return
    filename = unquote(filename, errors='strict')
    if Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('存档名称不能包含路径')
    selected = next((row for row in catalog() if row['filename'] == filename), None)
    if selected is None:
        raise ValueError('所选存档不存在，请刷新存档列表')
    from save_slot_manager import get_slot_manager
    g.gm_save_path = str((get_slot_manager().server_dir / filename).resolve(strict=True))
    g.gm_save_uid = selected['uid']
    # Explicit account IDs must belong to this save; never silently fall back.
    body = request.get_json(silent=True) if request.is_json else None
    uid = request.args.get('uid', (body or {}).get('uid') if isinstance(body, dict) else None)
    if uid is not None:
        reader = get_reader(lambda: None)
        if not reader.query_one('SELECT uid FROM users WHERE uid=?', (uid,)):
            raise ValueError('UID不属于当前所选存档')


def get_db(fallback):
    if not has_request_context():
        return fallback()
    existing = getattr(g, 'gm_save_db', None)
    if existing is not None:
        return existing
    selected = getattr(g, 'gm_save_path', None)
    base = fallback() if not selected else None
    if selected:
        import account_db
        mounted = account_db._db
        if mounted is not None and os.path.normcase(os.path.abspath(mounted.path)) == os.path.normcase(selected):
            base = mounted
    path = getattr(g, 'gm_save_path', None) or getattr(base, 'path', None)
    if not path:
        return base
    from account_db import AccountDB
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        g.gm_save_backup = AccountDB.backup_save_file(path)
    if base is not None:
        g.gm_save_db = base
    else:
        g.gm_save_db = AccountDB.open_existing(path, readonly=request.method in ('GET', 'HEAD', 'OPTIONS'))
        g.gm_save_owns_db = True
    return g.gm_save_db


def get_reader(fallback):
    path = getattr(g, 'gm_save_path', None) if has_request_context() else None
    if not path:
        return fallback()
    if not getattr(g, 'gm_save_reader', None):
        from gm_reader import GMReader
        g.gm_save_reader = GMReader(db_path=path)
    return g.gm_save_reader


def close(_exc=None):
    if getattr(g, 'gm_save_owns_db', False):
        g.gm_save_db.close()
    reader = getattr(g, 'gm_save_reader', None)
    conn = getattr(reader._local, 'conn', None) if reader else None
    if conn is not None:
        conn.close()


def create(data):
    from dataclasses import asdict
    from save_generator import SaveOptions, create_save, plan_save, SLOTS
    from save_presets import PRESET_SLOTS
    from save_slot_manager import get_slot_manager, MAX_SAVE_SLOTS
    if not isinstance(data, dict):
        raise ValueError('请求体必须为JSON对象')
    presets = {preset.key: preset.options for preset in PRESET_SLOTS.values()}
    if data.get('preset', 'custom') not in ('custom', *presets):
        raise ValueError('未知存档预设')
    raw = data.get('options', {})
    if not isinstance(raw, dict) or set(raw) - set(SaveOptions.__dataclass_fields__):
        raise ValueError('存档参数包含未知字段')
    options = SaveOptions(**{**asdict(presets.get(data.get('preset'), SaveOptions())), **raw}).validate()
    if data.get('preview') is True:
        plan = plan_save(options)
        return dict(level=plan['level'], nick=plan['nick'], total_exp=plan['total_exp'],
                    cleared_stages_count=plan['cleared_stages_count'], first_clear_rewards=plan['first_clear_rewards'],
                    support_mail_count=len(plan['support_mails']))
    manager = get_slot_manager()
    with manager._lock:
        if manager.server_dir.resolve() != Path(SLOTS).resolve():
            raise ValueError('存档目录配置不一致')
        if len(catalog()) >= MAX_SAVE_SLOTS:
            raise ValueError('存档位置已满（最多30个）')
        name = data.get('name') or manager._next_preset_name()
        # Save creation backs up its initial DB before exporting. Registry has its own snapshot.
        if manager.registry_file.exists():
            import shutil, time
            backup = manager.server_dir.parent / 'archive' / 'panel_save_registry'
            backup.mkdir(parents=True, exist_ok=True)
            with manager.registry_file.open('rb') as src, (backup / f'{time.time_ns()}.json').open('xb') as dst:
                shutil.copyfileobj(src, dst)
        result = create_save(name, options)
        sid = manager.register_new_save(Path(result['path']).name)
        if sid is None:
            raise ValueError('存档已生成并保留，槽位登记未完成，请刷新检查')
        return dict(filename=Path(result['path']).name, uid=result['uid'], level=result['level'], slot_id=sid)
