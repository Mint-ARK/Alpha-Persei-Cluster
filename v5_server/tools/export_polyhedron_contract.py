"""Read native Polyhedron configuration; never edit client Lua."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]

def build():
    base=ROOT/'decompiled_v2/x64/game/config'
    names=dict(stages='battlepolyhedronstagecfg',terminals='polyhedronterminalcfg',
        levels='polyhedronterminallevelcfg',settings='polyhedronsettingcfg',
        difficulties='polyhedrondifficultycfg',beacons='polyhedronbeaconcfg',
        astrolabes='heroastrolabecfg',combos='comboskillcfg',conditions='conditioncfg')
    result={};sources={}
    for key,name in names.items():
        path=base/(name+'.lua');raw=parse_lua_file(path)
        result[key]={str(i):v for i,v in raw.items() if type(i) is int}
        sources[str(path.relative_to(ROOT)).replace('\\','/')]=hashlib.sha256(path.read_bytes()).hexdigest()
    used={row['condition'] for row in result['beacons'].values() if row['condition']}
    result['conditions']={i:row for i,row in result['conditions'].items() if int(i) in used}
    result['astrolabes']={i:{k:row[k] for k in ('id','hero_astrolabe_suit_id','pre_astrolabe_id')}
                         for i,row in result['astrolabes'].items()}
    result['combos']={i:{k:row[k] for k in ('id','cooperate_role_ids')} for i,row in result['combos'].items()}
    result['sources']=sources
    return result

if __name__=='__main__':
    path=ROOT/'v5_server/polyhedron_contract.json'
    path.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n','utf-8')
    print(path)
