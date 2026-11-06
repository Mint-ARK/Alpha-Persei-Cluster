"""Export Kadas contracts from read-only native Lua and existing task catalogue."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def build():
    cfg = ROOT / 'decompiled_v2/x64/game/config'
    sources = [cfg / (name + '.lua') for name in ('activitytankcfg','activitytankbodycfg','activitytankweaponcfg')]
    def rows(path, fields):
        output = {}
        for sid, body in re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}', path.read_text('utf-8-sig'), re.M | re.S):
            row = {field: int(m[1]) for field in fields if (m := re.search(r'\b' + field + r' = (\d+)', body))}
            if path == sources[0]:
                if row.get('main_activity_id') != 4343601:
                    continue
                rewards = re.search(r'\breward_list = \{(.*?)\n\t\t\}', body, re.S)
                row['rewards'] = [[int(i),int(n)] for i,n in re.findall(r'\{\s*(\d+),\s*(\d+)\s*\}', rewards[1])] if rewards else []
            output[sid] = row
        return output
    stages = rows(sources[0], ['main_activity_id','mode_type','pre_stage','sort'])
    bodies = rows(sources[1], ['cost','body_type','sort','is_unlocked'])
    options = rows(sources[2], ['cost','item_type','sort','is_unlocked'])
    assert len(stages)==15 and len(bodies)==3 and len(options)==13
    mounts = ROOT/'v5_server/data/kadas_body_mounts.json'
    for i, body in bodies.items():
        native = json.loads(mounts.read_text('utf-8'))['bodies'][i]
        assert native['cost']==body['cost']
        body.update(weapon_slots=native['weapon_slots'],equip_slots=native['equip_slots'])
    catalog = ROOT / 'v5_server/data/activity_content_cfg.json'
    tasks = json.loads(catalog.read_text('utf-8'))['versions']['229']['tasks']
    limited = [int(i) for i,t in tasks.items() if t['activity_id'] in (4300401,4300402,4300403,4300404)
               or (423001<=t['condition']<=423006 and t['activity_id'] not in (0,4343601))]
    return dict(activity_id=4343601, parent_activity=4310001, limited_activities=[4300401,4300402,4300403,4300404],
        limited_tasks=sorted(limited), stages=stages,bodies=bodies,options=options,
        sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources+[catalog,mounts]})


if __name__=='__main__':
    path=ROOT/'v5_server/kadas_contract.json';text=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text('utf-8')!=text:
        raise SystemExit('Back up the existing contract before replacing it')
    path.write_text(text,encoding='utf-8')
    print('Exported 15 stages, 3 bodies, 13 options and native limited task ownership')
