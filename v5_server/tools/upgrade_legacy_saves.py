"""Add missing canonical schema and explicitly migrate retired AI files.

Default mode operates on in-memory snapshots only. --apply backs up every target
under the project db_backup directory before opening any save for writes.
Credentials are never emitted in the plan/results and never imported at runtime.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from account_db import AccountDB, savepoint_transaction
import ai_api_profiles
from ai_bot_config import get_default_config, is_masked_key

ENV_NAMES = {'opencode':'OPENCODE_GO_API_KEY', 'deepseek':'DEEPSEEK_API_KEY',
             'gemini':'GEMINI_API_KEY', 'openai':'OPENAI_API_KEY', 'anthropic':'ANTHROPIC_API_KEY'}


def read_legacy(config_path=None, env_path=None):
    """Read only explicitly supplied files, never os.environ/APPDATA/global search."""
    cfg = get_default_config()
    if config_path and Path(config_path).is_file():
        saved = json.loads(Path(config_path).read_text('utf-8-sig'))
        if not isinstance(saved, dict):
            raise ValueError('旧AI配置必须为JSON对象')
        for key, value in saved.items():
            if key == 'providers':
                for provider, entry in value.items():
                    cfg['providers'][provider] = {**cfg['providers'].get(provider, {}), **entry}
            else:
                cfg[key] = value
    env = {}
    if env_path and Path(env_path).is_file():
        for raw in Path(env_path).read_text('utf-8-sig').splitlines():
            raw = raw.strip()
            if not raw or raw.startswith('#') or '=' not in raw:
                continue
            key, value = raw.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[key.removeprefix('export ').strip()] = value
    providers = {}
    for name, entry in cfg['providers'].items():
        entry = deepcopy(entry)
        var = entry.pop('api_key_env', ENV_NAMES.get(name, ''))
        key = entry.get('api_key') or env.get(var, '')
        if is_masked_key(key):
            raise ValueError('旧配置中有掩码密钥，不能用其覆盖真实配置')
        if (key and name != 'ollama') or name == cfg.get('provider'):
            entry['api_key'] = key
            providers[name] = entry
    return dict(config=cfg, providers=providers)


def schema_objects(db):
    return {row['name']: row for row in db.query(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def upgrade(db, legacy=None):
    """AccountDB is the sole SQL writer; existing rows/extra schema are retained."""
    reference = AccountDB.new_save_workspace()
    try:
        expected = schema_objects(reference)
        actual = schema_objects(db)
        for name, obj in expected.items():
            if obj['type'] != 'table' or name not in actual:
                continue
            columns = {r['name'] for r in db.query('PRAGMA table_info('+quoted(name)+')')}
            required = {r['name'] for r in reference.query('PRAGMA table_info('+quoted(name)+')')}
            if required - columns:
                raise ValueError('发现未列入本次方案的缺列：'+name)
        old_counts = {name: db.query('SELECT COUNT(*) n FROM '+quoted(name))[0]['n']
                      for name, obj in actual.items() if obj['type']=='table' and name!='server_global_config'}
        missing = [obj for name, obj in expected.items() if name not in actual]
        missing.sort(key=lambda obj: obj['type'] != 'table')
        before = ai_api_profiles.load(db)
        with savepoint_transaction(db):
            for obj in missing:
                db.execute(obj['sql'])
            if legacy:
                cfg = legacy['config']
                imported = {}
                for provider, entry in legacy['providers'].items():
                    store = ai_api_profiles.load(db)
                    match = next((p for p in (store or {}).get('profiles', {}).values()
                                  if p['provider']==provider and p['base_url']==entry['base_url']
                                  and p['model']==entry['model'] and p['api_key']==entry['api_key']), None)
                    if match:
                        imported[provider] = match['id']
                        continue
                    payload = {key: cfg[key] for key in ('temperature','max_tokens','max_history_turns','timeout_seconds','system_prefix')}
                    payload.update({key: entry[key] for key in ('type','base_url','model','api_key')})
                    payload.update(provider=provider, name=(provider+'（旧配置迁入）')[:64])
                    result = ai_api_profiles.update(db, payload)
                    imported[provider] = result['profiles'][-1]['id']
                if not (before or {}).get('active_id') and cfg.get('provider') in imported:
                    ai_api_profiles.update(db, dict(action='activate',id=imported[cfg['provider']]))
            if ai_api_profiles.load(db) is None:
                db.set_system_config(ai_api_profiles.CONFIG_KEY,
                    json.dumps(dict(version=1,active_id='',profiles={},routes={})))
            for name, count in old_counts.items():
                if db.query('SELECT COUNT(*) n FROM '+quoted(name))[0]['n'] != count:
                    raise ValueError('升级改变了既有业务数据行数：'+name)
            if db.query('PRAGMA integrity_check')[0]['integrity_check'] != 'ok':
                raise ValueError('升级后的存档未通过完整性检查')
        view = ai_api_profiles.view(db)
        return dict(tables_before=sum(o['type']=='table' for o in actual.values()),
                    tables_after=sum(o['type']=='table' for o in schema_objects(db).values()),
                    added_objects=[o['name'] for o in missing],
                    profiles=[dict(name=p['name'],provider=p['provider'],model=p['model'],
                                   has_key=p['has_api_key'],is_default=p['id']==view['active_id'])
                              for p in view['profiles']], business_row_counts_preserved=True)
    finally:
        reference.close()


def snapshot(path):
    db = AccountDB.new_save_workspace()
    source = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        source.backup(db._conn())
    finally:
        source.close()
    return db


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',action='append',dest='databases')
    ap.add_argument('--legacy-config',type=Path)
    ap.add_argument('--legacy-env',type=Path)
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--report',type=Path)
    args=ap.parse_args()
    paths=[]
    for name in args.databases or ['account.db','account_test.db']:
        path=(SERVER/name).resolve(strict=True)
        if path.parent!=SERVER or path.suffix.lower()!='.db':
            raise ValueError('升级目标必须是服务端根目录中的存档名')
        if path not in paths:paths.append(path)
    legacy=read_legacy(args.legacy_config,args.legacy_env) if args.legacy_config or args.legacy_env else None
    # Prove both upgrades on snapshots before writing either real file.
    plans=[]
    for path in paths:
        db=snapshot(path)
        try:plans.append(dict(save=path.name,**upgrade(db,legacy)))
        finally:db.close()
    backups={str(p):AccountDB.backup_save_file(p) for p in paths} if args.apply else {}
    results=[]
    if args.apply:
        for path in paths:
            db=AccountDB.open_existing(path)
            try:results.append(dict(save=path.name,backup=backups[str(path)],**upgrade(db,legacy)))
            finally:db.close()
    report=dict(applied=args.apply,plans=plans,results=results)
    if args.report:
        if args.report.exists():raise FileExistsError('报告已存在，请使用新文件名')
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
