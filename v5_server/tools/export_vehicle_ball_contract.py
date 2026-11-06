"""Export Wuluo's native stage, vehicle, buff and task ownership contract."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def build():
    cfg = ROOT/'decompiled_v2/x64/game/config'
    sources = [cfg/(name+'.lua') for name in ('mergeballstagecfg','mergeballvehiclecfg','mergeballbuffcfg')]
    def rows(path, fields):
        out = {}
        for sid, body in re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}', path.read_text('utf-8-sig'), re.M|re.S):
            row = {f:int(m[1]) for f in fields if (m:=re.search(r'\b'+f+r' = (\d+)',body))}
            if path==sources[0] and row.get('activity_id')!=4243101:continue
            if path==sources[2]:
                row['reward'] = list(map(int,re.findall(r'\d+',re.search(r'\breward = \{(.*?)\}',body,re.S)[1])))
            out[sid]=row
        return out
    stages=rows(sources[0],['activity_id','unlock_vehicle','unlock_skill','ult_progress'])
    vehicles=rows(sources[1],['type','unlock_stage'])
    buffs=rows(sources[2],['type'])
    catalog=ROOT/'v5_server/data/activity_content_cfg.json'
    tasks=json.loads(catalog.read_text('utf-8'))['versions']['229']['tasks']
    limited=[int(i) for i,t in tasks.items() if t['activity_id'] in (4200401,4200402,4200403,4200404)
             or (422001<=t['condition']<=422004 and t['activity_id'] not in (0,4243101))]
    assert len(stages)==4 and len(vehicles)==6 and len(buffs)==19
    return dict(activity_id=4243101,parent_activity=4210001,limited_activities=[4200401,4200402,4200403,4200404],
                limited_tasks=sorted(limited),stages=stages,vehicles=vehicles,buffs=buffs,
                sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources+[catalog]})


if __name__=='__main__':
    path=ROOT/'v5_server/vehicle_ball_contract.json';text=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text('utf-8')!=text:raise SystemExit('Back up the previous contract first')
    path.write_text(text,encoding='utf-8')
    print('Exported four stages, three vehicles, three passives and 19 buffs')
