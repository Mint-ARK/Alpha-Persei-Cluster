"""Extract existing ExploringEventCfg IDs/types; do not invent log rewards."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]


def build():
    source=ROOT/'decompiled_v2/x64/game/config/exploringeventcfg.lua'
    rows=parse_lua_file(source)
    events={str(i):dict(event_type=r['event_type'],reward=r['reward'])
            for i,r in rows.items() if type(i)is int}
    assert {r['event_type'] for r in events.values()}=={1,2,3}
    return dict(events=events,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())


if __name__=='__main__':
    target=ROOT/'v5_server/admin_cat_log_cfg.json'
    if target.exists():raise SystemExit('Back up the old contract before updating it')
    target.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
