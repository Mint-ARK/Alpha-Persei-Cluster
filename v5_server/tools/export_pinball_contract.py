"""Export only the native permanent Hella pinball contract; Lua stays read-only."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def build():
    cfg = ROOT / 'decompiled_v2/x64/game/config'
    files = [cfg / 'pinballstagecfg.lua', cfg / 'pinballskillcfg.lua',
             ROOT / 'decompiled_v2/x64/game/views/activity/submodule/pinball/hellapinball/hellapinballheroview.lua',
             cfg / 'battlehellapinballstagecfg.lua']
    def rows(path, fields):
        output = {}
        for sid, body in re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}', path.read_text('utf-8-sig'), re.M | re.S):
            values = {field: int(match[1]) for field in fields
                      if (match := re.search(r'\b' + field + r' = (\d+)', body))}
            if values.get('activity_id') == 3840801:
                output[sid] = values
        return output
    stages = rows(files[0], ['activity_id', 'pre_stage', 'lv_up', 'skill_unlock', 'barrier_type', 'barrier_id'])
    skills = rows(files[1], ['activity_id', 'skill_type', 'skill_group'])
    battle_ids = {row['barrier_id'] for row in stages.values() if row['barrier_type'] == 2}
    battles = {}
    for sid, body in re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}', files[3].read_text('utf-8-sig'), re.M | re.S):
        if int(sid) in battle_ids:
            battles[sid] = {field: int(re.search(r'\b' + field + r' = (\d+)', body)[1])
                            for field in ['type', 'cost', 'hero_exp', 'user_exp']}
    assert len(stages) == 13 and len(skills) == 28
    assert '/2</color>' in files[2].read_text('utf-8-sig')
    assert len(battles) == len(battle_ids) and all(v == dict(type=603, cost=0, hero_exp=0, user_exp=0) for v in battles.values())
    return dict(activity_id=3840801, active_slots=2, stages=stages, skills=skills, battles=battles,
                sources={str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


if __name__ == '__main__':
    path = ROOT / 'v5_server/pinball_contract.json'
    text = json.dumps(build(), ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text('utf-8') != text:
        raise SystemExit('Back up the existing contract before replacing it')
    path.write_text(text, encoding='utf-8')
    print('Exported 13 stages, 28 skills and two active slots')
