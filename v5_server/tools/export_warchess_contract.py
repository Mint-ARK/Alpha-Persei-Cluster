"""Export read-only native p49 maps/config; never modify the client bundle."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]


def build():
    base=ROOT/'decompiled_v2/x64/game/config'
    names={'levels':'warchesslevelcfg','hexes':'warchesshexcfg','pools':'warchesseventpoolcfg',
           'globals':'warchessglobalcfg','items':'warchessitemcfg','objects':'warchessobjectcfg',
           'rarities':'warchessrarecfg','stages':'battlechessstagecfg'}
    out={};sources={}
    for key,name in names.items():
        p=base/(name+'.lua');raw=parse_lua_file(p)
        out[key]={str(i):r for i,r in raw.items() if isinstance(i,int)}
        sources[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    drops=set()
    def walk(value):
        if isinstance(value,list):
            if value and isinstance(value[0],int) and value[0] in (10203,10204):
                drops.add(value[1])
            for child in value:walk(child)
        elif isinstance(value,dict):
            for child in value.values():walk(child)
    walk(out['pools']);walk(out['globals'])
    p=base/'dropcfg.lua';raw=parse_lua_file(p);out['drops']={str(i):raw[i] for i in sorted(drops)}
    sources[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    p=base/'assignmentcfg.lua';raw=parse_lua_file(p)
    out['tasks']={str(i):{k:r[k] for k in ('condition','additional_parameter','need')} for i,r in raw.items()
                  if isinstance(i,int) and r['condition'] in (460,461,462,463)}
    sources[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    folder=ROOT/'archive/warchess_state_20261105/native_maps'
    out['maps']={}
    for p in sorted(folder.glob('*.json')):
        if not p.stem.isdigit():continue
        r=json.loads(p.read_text('utf-8'));assert str(r['id'])==p.stem
        out['maps'][p.stem]=dict(width=r['width'],height=r['height'],
            tiles={str(t['x'])+','+str(t['z']):{k:t[k] for k in ('typeID','status','rotationY','paramList')} for t in r['mapInfo']},
            extend=r['extendInfo'] or [],sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    out['map_source']=json.loads((folder/'evidence.json').read_text('utf-8'))
    out['sources']=sources
    assert len(out['maps'])==40 and set(out['maps'])==set(out['levels'])
    return out


if __name__=='__main__':
    p=ROOT/'v5_server/warchess_contract.json';text=json.dumps(build(),ensure_ascii=False,separators=(',',':'))+'\n'
    if p.exists() and p.read_text('utf-8')!=text:raise SystemExit('Back up the old contract first')
    p.write_text(text,'utf-8');print('Exported 40 native WarChess maps and authoritative event/drop contracts')
