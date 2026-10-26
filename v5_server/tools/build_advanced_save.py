"""Legacy advanced preset using the same configurable save generator."""
import argparse
import json
from pathlib import Path
import sys

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))
from save_generator import SaveOptions, create_save, total_exp_for_level, generation_config, NOOB_END_TS
from fatigue_service import get_max_fatigue_by_level

DEFAULT_OUT = SERVER_DIR / 'saves/templates/lv70_prologue_skipped_template.db'
ADVANCED_NOOB_END_TS = NOOB_END_TS
calc_total_exp_for_level = total_exp_for_level
get_max_fatigue = get_max_fatigue_by_level


def advanced_noob_window(now_ts):
    from new_player_service import config
    cfg = config()
    phase = max(int(row['phase']) for row in cfg['tasks'].values() if row['type'] == cfg['noob']['noob_task_type'])
    if now_ts >= NOOB_END_TS:
        raise ValueError('新档协议活动期限需要更新')
    return int(now_ts) - (phase - 1) * 86400, NOOB_END_TS, phase


def load_all_guides_from_lua(lua_dir=None):
    # Compatibility function: generated config ships with the server.
    return [(row['id'], row['kind']) for row in generation_config()['guides']]


def build_advanced_save(dest_path, uid=None, nick='管理员', level=70, clear_up_to_chapter=5, complete_noob_tasks=True):
    path = Path(dest_path).resolve()
    return create_save(path.name, SaveOptions(uid=uid, nick=nick, level=level, skip_intro=True,
        skip_feature_guides=True, clear_through=clear_up_to_chapter,
        noob_tasks_ready=complete_noob_tasks), destination=path)


def main():
    parser = argparse.ArgumentParser(description='进阶开局预设；更多独立开关见 create_save.py。')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--uid', help='省略时自动生成UID')
    parser.add_argument('--nick', default='管理员')
    parser.add_argument('--level', type=int, default=70)
    parser.add_argument('--chapters', type=int, choices=range(6), default=5, help='0为序章，1—5为对应章节')
    parser.add_argument('--complete-noob-tasks', action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    report = build_advanced_save(args.out, args.uid, args.nick, args.level, args.chapters, args.complete_noob_tasks)
    print(json.dumps({key: report[key] for key in ('status', 'path', 'uid', 'level', 'cleared_stages_count', 'backup', 'integrity')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
