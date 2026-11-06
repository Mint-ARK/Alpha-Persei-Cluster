"""Export collection tiers from public HeroCfg and GameSetting, with an overwrite backup."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
from extract_autochess_cfg import parse_lua_file

ROOT = Path(__file__).resolve().parents[2]


def build_config():
    source = ROOT / 'decompiled_v2/x64/game/config'
    heroes = parse_lua_file(source / 'herocfg.lua')
    settings = parse_lua_file(source / 'gamesetting.lua')
    roster = {str(hid): row['race'] for hid, row in sorted(
        ((hid, row) for hid, row in heroes.items() if isinstance(hid, int)))
        if row.get('private') == 0}
    step = settings['hero_race_collect_each_tier']['value'][0]
    rewards = [dict(id=iid, num=num) for iid, num in settings['hero_race_collect_reward_list']['value']]
    assert isinstance(step, int) and step > 0 and roster and rewards
    assert all(r in (1, 2, 3, 4, 5, 9) for r in roster.values())
    tasks, by_race = {}, {}
    for race, count in sorted(Counter(roster.values()).items()):
        rows = []
        for tier, need in enumerate(range(step, count + 1, step), 1):
            # 保留旧配置的内部ID；客户端请求和存档仅使用race_type/need。
            task = dict(id=100 + race * 10 + tier, hero_race=race, need=need, reward=rewards)
            tasks[str(task['id'])] = task
            rows.append(task)
        by_race[str(race)] = rows
    return dict(tasks=tasks, by_race=by_race, each_tier=step, public_hero_races=roster)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'v5_server/collect_hero_race_cfg.json')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    config = build_config()
    if args.output.exists():
        if args.backup_dir is None:
            parser.error('Existing output requires --backup-dir')
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.output, args.backup_dir / (datetime.now().strftime('%Y%m%d_%H%M%S_%f_') + args.output.name))
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps(dict(public_heroes=len(config['public_hero_races']), tiers=len(config['tasks']))))


if __name__ == '__main__':
    main()
