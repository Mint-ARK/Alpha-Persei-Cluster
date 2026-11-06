"""Export difficulty/team/stage contracts, without rewards or player state."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]


def build():
    result={}
    for profile,relative in (('229','decompiled_v2/x64/game/config/mythicfinalcfg.lua'),
                             ('311','v5_server/tools/diff_lua_decompiled_311/MythicFinalCfg.lua')):
        path=ROOT/relative;raw=parse_lua_file(path)
        rows={str(k):dict(team_num=v['team_num'],stages=v['stage_list']) for k,v in raw.items() if isinstance(k,int)}
        assert len(rows)==30
        for row in rows.values():
            assert row['team_num'] in (1,2)
            assert row['stages'] and all(len(stages)==row['team_num'] for stages in row['stages'])
        result[profile]=dict(source=relative,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),difficulties=rows)
    return result


if __name__=='__main__':
    path=ROOT/'v5_server/mythic_final_cfg.json'
    path.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n','utf-8')
    print(path)
