"""Export only numeric lifecycle/collection contracts from the current Lua."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
CFG=ROOT/'decompiled_v2/x64/game/config'


def rows(name,fields):
    path=CFG/(name+'.lua');source=path.read_text('utf-8-sig')
    # Numeric root entries use one tab; ignore nested tables and prose entirely.
    blocks=re.findall(r'^\t(?:\[\d+\] = )?\{\s*\n(.*?)^\t\}',source,re.M|re.S)
    result={}
    for block in blocks:
        item={}
        for field in ('id',*fields):
            match=re.search(r'^\t\t'+field+r' = (-?\d+),?$',block,re.M)
            if match:item[field]=int(match[1])
        if 'id' not in item:continue
        if 'params' in fields:
            match=re.search(r'^\t\tparams = \{(.*?)\}',block,re.M|re.S)
            if match:item['params']=[int(x) for x in re.findall(r'-?\d+',match[1])]
        result[str(item['id'])]=item
    assert result,name
    return result,dict(path=str(path.relative_to(ROOT)).replace('\\','/'),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def build():
    data=dict(version=1,sources=[])
    for key,name,fields in (
        ('posts','roguecardpostingcfg',('type','pre_id','balatro_id','story_id1','story_id2')),
        ('stages','roguecardstagecfg',('type','round')),
        ('decks','roguecarddeckcfg',('condition',)),
        ('difficulties','roguecarddifficultycfg',()),
        ('cards','roguecarditemcfg',('type',)),
        ('enhances','roguecardenhancecfg',()),
        ('weal_woe','roguecardbosseffectcfg',('type',)),
        ('hand_types','roguecardhandtypecfg',()),
        ('conditions','conditioncfg',('type','params'))):
        table,source=rows(name,fields)
        if key=='conditions':table={k:v for k,v in table.items() if v.get('type') in (12701,12702)}
        data[key]=table;data['sources'].append(source)
    assert len(data['posts'])==len(set(data['posts'])) and len(data['stages'])==7
    assert len(data['decks'])==4 and len(data['difficulties'])==6
    for post in data['posts'].values():
        assert not post['pre_id'] or str(post['pre_id']) in data['posts']
        assert not post['balatro_id'] or str(post['balatro_id']) in data['stages']
    for deck in data['decks'].values():
        assert not deck['condition'] or str(deck['condition']) in data['conditions']
    return data


if __name__=='__main__':
    data=build();path=ROOT/'v5_server/rogue_card_cfg.json'
    assert not path.exists(),'Back up the generated file before replacing it'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n','utf-8')
    print({k:len(v) for k,v in data.items() if isinstance(v,dict)})
