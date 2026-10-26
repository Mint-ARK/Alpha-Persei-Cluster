"""Legacy pristine preset; all construction is delegated to save_generator."""
import argparse
import json
from pathlib import Path
import sys

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))
from save_generator import SaveOptions, create_save

DEFAULT_TEMPLATE = SERVER_DIR / 'saves/templates/new_player_template.db'


def build_pristine_save(dest_path, uid=None, nick='管理员'):
    path = Path(dest_path).resolve()
    return create_save(path.name, SaveOptions(uid=uid, nick=nick), destination=path)


def main():
    parser = argparse.ArgumentParser(description='全新开荒存档预设；已有文件不会覆盖。更多选项见 create_save.py。')
    parser.add_argument('--out', type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument('--uid', help='省略时自动生成UID')
    parser.add_argument('--nick', default='管理员', help='保留501时仍在游戏内取名')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error('报告已存在，请使用新路径')
    report = build_pristine_save(args.out, args.uid, args.nick)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in ('status', 'path', 'uid', 'level', 'backup', 'integrity')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
