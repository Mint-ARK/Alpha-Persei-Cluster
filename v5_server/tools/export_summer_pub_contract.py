"""Read-only SummerPub level/food/billiard/reward contract export."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]


def build():
    cfg=ROOT/'decompiled_v2/x64/game/config'
    names=['summerpublevelcfg','summerpubcookcfg','summerpubherocfg','billiardstagecfg','billiardillustrationscfg','battlesummerpubstagecfg','dropcfg']
    sources=[cfg/(n+'.lua') for n in names]
    tables={n:dict(re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}',p.read_text('utf-8-sig'),re.M|re.S)) for n,p in zip(names,sources)}
    def num(body,key):return int(re.search(r'\b'+key+r' = (\d+)',body)[1])
    def array(body,key):
        match=re.search(r'\b'+key+r' = \{(.*?)\n\t\t\}',body,re.S)
        return list(map(int,re.findall(r'\d+',match[1]))) if match else []
    levels={i:{k:num(b,k) for k in ['type','prev_id','drop_lib_id']}|dict(stages=array(b,'stage_id')) for i,b in tables['summerpublevelcfg'].items()}
    for row in levels.values():
        raw=tables['dropcfg'].get(str(row['drop_lib_id']),'')
        base=re.search(r'\bbase_drop = \{(.*?)\n\t\t\}',raw,re.S)
        triples=[list(map(int,t)) for t in re.findall(r'\{\s*(\d+),\s*(\d+),\s*(\d+)\s*\}',base[1])] if base else []
        assert all(p==100 for i,n,p in triples)
        row['rewards']=[[i,n] for i,n,p in triples]
    cooks={i:dict(unlocked_by_level=array(b,'unlocked_by_level')) for i,b in tables['summerpubcookcfg'].items()}
    balls={i:dict(illustrations=array(b,'stage_illustration'),type=num(b,'type')) for i,b in tables['billiardstagecfg'].items() if num(b,'activity_id')==3539101}
    heroes={i:{k:num(b,k) for k in ['type','recruit_level']} for i,b in tables['summerpubherocfg'].items()}
    battles={i:dict(tag=num(b,'tag')) for i,b in tables['battlesummerpubstagecfg'].items()}
    illustrations=[int(i) for i,b in tables['billiardillustrationscfg'].items() if num(b,'activity_id')==3539101]
    path=ROOT/'v5_server/data/activity_content_cfg.json'
    tasks=json.loads(path.read_text('utf-8'))['versions']['229']['tasks']
    limited=sorted(t['id'] for t in tasks.values() if 3500401<=t['activity_id']<=3500405 or (350001<=t['condition']<=350005 and t['activity_id'] not in (0,3539101,3500407)))
    assert len(levels)==32 and len(cooks)==8 and len(balls)==32
    return dict(activity_id=3539101,parent_activity=3510001,limited_activities=list(range(3500401,3500406)),limited_tasks=limited,
                levels=levels,cooks=cooks,balls=balls,heroes=heroes,battles=battles,illustrations=illustrations,
                sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources+[path]})


if __name__=='__main__':
    path=ROOT/'v5_server/summer_pub_contract.json';text=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text('utf-8')!=text:raise SystemExit('Back up the previous contract first')
    path.write_text(text,'utf-8');print('Exported SummerPub levels, recipes, billiards and first-clear rewards')
