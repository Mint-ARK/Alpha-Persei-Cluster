# -*- coding: utf-8 -*-
"""Generate the server-side archive contract from immutable client configs.

This tool reads decompiled_v2 only and writes v5_server/archive_config.json.
Runtime services consume the generated JSON and never parse Lua directly.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
from pathlib import Path
import shutil


SERVER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = SERVER_DIR.parent
CLIENT_CONFIG_DIR = PROJECT_DIR / "decompiled_v2" / "x64" / "game" / "config"


def _top_level_blocks(text: str):
    matches = list(re.finditer(r"(?m)^\t\[(\d+)\] = \{", text))
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        yield int(match.group(1)), text[match.end():end]


def _number_list(block: str, field: str) -> list[int]:
    match = re.search(
        rf"(?ms)^\t\t{re.escape(field)} = \{{(.*?)^\t\t\}},?\s*$",
        block,
    )
    if not match:
        return []
    return [int(value) for value in re.findall(r"(?m)^\s*(\d+),?\s*$", match.group(1))]


def build_first_tier_gifts(item_path: Path) -> list[int]:
    gifts = []
    for item_id, block in _top_level_blocks(item_path.read_text(encoding='utf-8')):
        item_type = re.search(r'(?m)^\t\ttype = (\d+),?\s*$', block)
        sub_type = re.search(r'(?m)^\t\tsub_type = (\d+),?\s*$', block)
        if item_type and sub_type and int(item_type[1]) == 10 and int(sub_type[1]) == 1:
            gifts.append(item_id)
    if not gifts:
        raise ValueError('no first-tier archive gifts found in ItemCfg')
    return sorted(gifts)


def build_config(record_path: Path, anecdote_path: Path) -> dict:
    record_text = record_path.read_text(encoding="utf-8")
    anecdote_text = anecdote_path.read_text(encoding="utf-8")

    archives: dict[str, dict] = {}
    hero_to_archive: dict[str, int] = {}
    for archive_id, block in _top_level_blocks(record_text):
        hero_ids = _number_list(block, "hero_id")
        if not hero_ids:
            continue
        entry = {
            "hero_ids": hero_ids,
            "gift_like_ids": _number_list(block, "gift_like_id1"),
            "plot_ids": _number_list(block, "plot_id"),
            "super_plot_ids": _number_list(block, "super_plot_id"),
        }
        archives[str(archive_id)] = entry
        for hero_id in hero_ids:
            previous = hero_to_archive.setdefault(str(hero_id), archive_id)
            if previous != archive_id:
                raise ValueError(f"hero {hero_id} belongs to both {previous} and {archive_id}")

    anecdotes: dict[str, dict] = {}
    for hero_id, block in _top_level_blocks(anecdote_text):
        reward_numbers = _number_list(block, "reward")
        if len(reward_numbers) % 2:
            raise ValueError(f"hero {hero_id} has malformed anecdote reward")
        rewards = [
            {"id": reward_numbers[i], "num": reward_numbers[i + 1]}
            for i in range(0, len(reward_numbers), 2)
        ]
        archive_id = hero_to_archive.get(str(hero_id))
        if archive_id is None:
            raise ValueError(f"anecdote hero {hero_id} has no archive mapping")
        anecdotes[str(hero_id)] = {"archive_id": archive_id, "rewards": rewards}

    return {
        "version": 1,
        "source": {
            "hero_record": "decompiled_v2/x64/game/config/herorecordcfg.lua",
            "hero_anecdote": "decompiled_v2/x64/game/config/heroanedotecfg.lua",
            "first_tier_gifts": "decompiled_v2/x64/game/config/itemcfg.lua:type10/sub_type1",
        },
        "first_tier_gift_ids": build_first_tier_gifts(CLIENT_CONFIG_DIR / 'itemcfg.lua'),
        "archives": archives,
        "hero_to_archive": hero_to_archive,
        "anecdotes": anecdotes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=SERVER_DIR / "archive_config.json")
    parser.add_argument('--update-gifts-only', action='store_true', help='Preserve existing archive/trust data; update gift eligibility only')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    if args.update_gifts_only:
        config = json.loads(args.output.read_text(encoding='utf-8'))
        config['first_tier_gift_ids'] = build_first_tier_gifts(CLIENT_CONFIG_DIR / 'itemcfg.lua')
        config.setdefault('source', {})['first_tier_gifts'] = 'decompiled_v2/x64/game/config/itemcfg.lua:type10/sub_type1'
    else:
        config = build_config(
            CLIENT_CONFIG_DIR / "herorecordcfg.lua",
            CLIENT_CONFIG_DIR / "heroanedotecfg.lua",
        )
    if args.output.exists():
        if args.backup_dir is None:
            parser.error('Existing output requires --backup-dir')
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.output, args.backup_dir / (datetime.now().strftime('%Y%m%d_%H%M%S_%f_') + args.output.name))
    args.output.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {args.output}: archives={len(config['archives'])} "
        f"heroes={len(config['hero_to_archive'])} anecdotes={len(config['anecdotes'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
