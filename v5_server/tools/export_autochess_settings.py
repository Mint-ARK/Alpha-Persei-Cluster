"""Export native AutoChess GameSetting values, no runtime Lua parsing."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'decompiled_v2/x64/game/config/gamesetting.lua'


def build():
    cfg=parse_lua_file(SOURCE)
    return dict(source=SOURCE.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                settings={k:v['value'] for k,v in cfg.items() if str(k).startswith('auto_chess_')})


if __name__=='__main__':
    import argparse,shutil,time
    p=argparse.ArgumentParser();p.add_argument('--write',action='store_true');args=p.parse_args()
    out=build()
    if args.write:
        dest=ROOT/'v5_server/autochess_settings.json'
        if dest.exists():
            backup=ROOT/'archive'/('autochess_settings_'+time.strftime('%Y%m%d_%H%M%S'));backup.mkdir(parents=True,exist_ok=False)
            shutil.copy2(dest,backup/dest.name)
        dest.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n','utf-8')
    else:print(json.dumps(out,ensure_ascii=False,indent=2))
