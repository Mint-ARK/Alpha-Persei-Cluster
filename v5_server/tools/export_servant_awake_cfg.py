"""Export awakening recipes and target metadata from the current client Lua."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil

from extract_autochess_cfg import parse_lua_file
from generate_archive_config import _top_level_blocks, _number_list

SERVER = Path(__file__).resolve().parents[1]
CONFIG = SERVER.parent / 'decompiled_v2/x64/game/config'


def scalar(block, field):
    match = re.search(rf'(?m)^\t\t{field} = (\d+),?\s*$', block)
    if not match:
        raise ValueError(f'missing scalar {field}')
    return int(match[1])


def build_config():
    settings = parse_lua_file(str(CONFIG / 'gamesetting.lua'))
    recipes = {}
    for race, costs in settings['exclusive_weapon_servant_cost']['value']:
        (base, count), (currency, amount) = costs
        if count != 1 or currency != 2 or amount <= 0:
            raise ValueError('unsupported awakening recipe')
        recipes[str(race)] = dict(base_id=base, count=count, currency_id=currency, currency_num=amount)
    effects = {eid: _number_list(block, 'spec_char') for eid, block in
               _top_level_blocks((CONFIG / 'weaponeffectcfg.lua').read_text(encoding='utf-8'))}
    targets = {}
    for sid, block in _top_level_blocks((CONFIG / 'weaponservantcfg.lua').read_text(encoding='utf-8')):
        effect = _number_list(block, 'effect')
        heroes = effects.get(effect[0], []) if effect else []
        if not heroes or heroes[0] <= 0:
            continue
        entry = {field: scalar(block, field) for field in
                 ('race', 'type', 'starlevel', 'display_type', 'summon_blocked')}
        if entry['starlevel'] == 5 and entry['type'] == 2 and not entry['summon_blocked']:
            if str(entry['race']) not in recipes:
                raise ValueError(f'no recipe for {sid}')
            targets[str(sid)] = dict(entry, spec_hero=heroes[0])
    if not recipes or not targets:
        raise ValueError('empty awakening configuration')
    return dict(version=1, source='current Lua: GameSetting / WeaponServantCfg / WeaponEffectCfg',
                recipes=recipes, targets=targets)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, default=SERVER / 'servant_awake_cfg.json')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    config = build_config()
    if args.output.exists():
        if not args.backup_dir:
            parser.error('overwriting requires --backup-dir')
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.output, args.backup_dir / (datetime.now().strftime('%Y%m%d_%H%M%S_%f_') + args.output.name))
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"recipes={len(config['recipes'])}, targets={len(config['targets'])}")


if __name__ == '__main__':
    main()
