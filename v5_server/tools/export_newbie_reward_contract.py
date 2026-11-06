"""Export ordinary newbie claim eligibility, never replace customized rewards."""
import hashlib
import json
from pathlib import Path
import secrets
import shutil
from extract_autochess_cfg import parse_lua_file

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'decompiled_v2/x64/game/config'
OUT = ROOT / 'v5_server/newbie_reward_contract.json'


def build():
    versions = parse_lua_file(SOURCE / 'noobversioncfg.lua')
    assignments = parse_lua_file(SOURCE / 'assignmentcfg.lua')
    selected = {str(key): dict(task_type=value['noob_task_type'], sign_ids=value['noob_sign'],
                              progress_thresholds=[entry[0] for entry in value['noob_task_progress_reward']]
                              if isinstance(value['noob_task_progress_reward'], list) else [])
                for key, value in versions.items() if type(key) is int}
    types = {row['task_type'] for row in selected.values()}
    tasks = {str(key): dict(task_type=value['type'], phase=value['phase'])
             for key, value in assignments.items() if type(key) is int and value['type'] in types}
    return dict(versions=selected, tasks=tasks, source_sha256={name: hashlib.sha256((SOURCE / (name+'.lua')).read_bytes()).hexdigest()
                for name in ('noobversioncfg', 'assignmentcfg')})


if __name__ == '__main__':
    if OUT.exists():
        backup = ROOT / 'archive/newbie_reward_contract_exports'
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OUT, backup / ('before_' + secrets.token_hex(8) + '.json'))
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Exported', len(data['versions']), 'versions and', len(data['tasks']), 'task definitions')
