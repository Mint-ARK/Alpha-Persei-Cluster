"""Read-only export of Ash stages, first-clear film, unlocks and task ownership."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]


def build():
    cfg=ROOT/'decompiled_v2/x64/game/config'
    sources=[cfg/(n+'.lua') for n in ('ashshootstagecfg','battleashshootstagecfg','ashshootunlockitemcfg','ashshooteffectcfg','dropcfg')]
    def rows(path):return dict(re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}',path.read_text('utf-8-sig'),re.M|re.S))
    def scalar(body,name):return int(re.search(r'\b'+name+r' = (\d+)',body)[1])
    stages={i:{f:scalar(b,f) for f in ('stage_group','stage_id','type','front_unlock_id','activity_id')} for i,b in rows(sources[0]).items() if scalar(b,'activity_id')==3639701}
    battles=rows(sources[1]);drops=rows(sources[4])
    for i,s in stages.items():
        b=battles[str(s['stage_id'])];drop=scalar(b,'drop_lib_id');s['drop_lib_id']=drop
        if drop:
            raw=re.search(r'\bbase_drop = \{(.*?)\n\t\t\}',drops[str(drop)],re.S)[1]
            rewards=[list(map(int,pair)) for pair in re.findall(r'\{\s*(\d+),\s*(\d+),\s*(\d+)\s*\}',raw)]
            assert len(rewards)==1 and rewards[0][0]==79 and rewards[0][2]==100
            s['film']=rewards[0][1]
        else:s['film']=0
    unlocks={i:{f:scalar(b,f) for f in ('type','correlation_id','unlock_num')} for i,b in rows(sources[2]).items() if scalar(b,'activity_id')==3639701}
    effects={int(i):scalar(b,'type') for i,b in rows(sources[3]).items()}
    skills={str(v['correlation_id']):v['unlock_num'] for v in unlocks.values() if v['type']==2 and effects[v['correlation_id']]==1}
    notices={str(v['correlation_id']):v['unlock_num'] for v in unlocks.values() if v['type']==3}
    catalog=ROOT/'v5_server/data/activity_content_cfg.json'
    tasks=json.loads(catalog.read_text('utf-8'))['versions']['229']['tasks']
    limited=sorted(int(i) for i,t in tasks.items() if t['activity_id'] in (3600401,3600402,3600403,3600404)
        or (380001<=t['condition']<=380002 and t['activity_id'] not in (0,3639701)))
    assert len(stages)==13 and sum(s['film'] for s in stages.values())==100 and len(skills)==2
    return dict(activity_id=3639701,parent_activity=3610001,limited_activities=[3600401,3600402,3600403,3600404],limited_tasks=limited,
        stages=stages,skills=skills,notices=notices,scoring=dict(kind='private_enemy_count_v1',points_per_enemy=100,reward_thresholds=sorted({t['additional_parameter'][1] for t in tasks.values() if t['condition']==380002})),
        sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources+[catalog]})


if __name__=='__main__':
    path=ROOT/'v5_server/ash_contract.json';text=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text('utf-8')!=text:raise SystemExit('Back up the previous contract first')
    path.write_text(text,encoding='utf-8');print('Exported 13 stages, 100 first-clear film, two skills and dialogue notices')
