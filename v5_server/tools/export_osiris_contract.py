"""Read-only export of Osiris study stages, party unlocks and native records."""
import hashlib
import json
from pathlib import Path
import re
from extract_autochess_cfg import parse_lua_file
from export_enemy_sweep_config import native_tables

ROOT=Path(__file__).resolve().parents[2]


def build():
    cfg=ROOT/'decompiled_v2/x64/game/config'
    sources=[cfg/(n+'.lua') for n in ['activitygeneralitystagecfg','activitygeneralitychaptercfg','activitygeneralitycharactorcfg','gamesetting']]
    raw,chapters,heroes,settings=map(parse_lua_file,sources)
    stages={str(v['stage_id']):{k:v[k] for k in ['chapter_id','pre_stage','generality','unlock_charactor','rank_activity']} for k,v in raw.items() if isinstance(k,int)}
    chapters={str(k):dict(type=v['type'],stages=[int(s) for s,r in stages.items() if r['chapter_id']==k]) for k,v in chapters.items() if isinstance(k,int)}
    heroes={str(k):{n:v[n] for n in ['charactor_type','charactor_id','initial_unlock']} for k,v in heroes.items() if isinstance(k,int)}
    native=ROOT/'archive/enemy_illustration_20261023/native_config.bytes'
    def record_row(raw):
        pos=0;out={}
        def varint():
            nonlocal pos
            value=shift=0
            while True:
                byte=raw[pos];pos+=1;value|=(byte&127)<<shift
                if byte<128:return value
                shift+=7
        while pos<len(raw):
            tag=varint()
            if tag&7==0:out[tag>>3]=varint()
            else:
                assert tag&7==2
                size=varint();out[tag>>3]=raw[pos:pos+size];pos+=size
        return out
    records={}
    for i,b in native_tables(native)['battle_record_data_type'].items():
        if 93<=i<=100:
            row=record_row(b);records[str(i)]=dict(type_id=row[2],name=row[3].decode('utf-8'))
    tasks_path=ROOT/'v5_server/data/activity_content_cfg.json'
    tasks=json.loads(tasks_path.read_text('utf-8'))['versions']['229']['tasks']
    limited=sorted(t['id'] for t in tasks.values() if t['activity_id'] in (330043,330044,330045,330046)
                   or (340001<=t['condition']<=340009 and t['activity_id'] not in (0,323801))
                   or (t['condition']==501 and t['activity_id']==332001 and 40103005 in t['additional_parameter']))
    assert len(stages)==24 and len(chapters)==7 and all(r['type_id']==int(i) for i,r in records.items())
    score={k:settings['activity_generality_'+k]['value'][0] for k in ['base_point','base_time','base_factor']}
    return dict(activity_id=323801,parent_activity=331001,limited_activities=[330043,330044,330045,330046],limited_tasks=limited,
                stages=stages,chapters=chapters,heroes=heroes,records=records,mechanic_records={'340004':93,'340005':94,'340006':95,'340007':96,'340008':97,'340009':98},
                scoring=dict(kind='private_time_bonus_v1',**score),
                sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources+[native,tasks_path]})


if __name__=='__main__':
    p=ROOT/'v5_server/osiris_contract.json';s=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if p.exists() and p.read_text('utf-8')!=s:raise SystemExit('Back up the old contract first')
    p.write_text(s,'utf-8');print('Exported 24 stages, seven chapters, native hero unlocks and records93-100')
