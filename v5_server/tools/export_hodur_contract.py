"""Read-only native Hodur chapter, event and battle metadata exporter."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]


def build():
    cfg=ROOT/'decompiled_v2/x64/game/config'
    files=[cfg/(n+'.lua') for n in ('activityhodurchaptercfg','activityhodurcfg','activityhodureventcfg','activityhodureventoptioncfg','battlehodurstagecfg')]
    def scalar(body,name):return int(re.search(r'\b'+name+r' = "?(\d+)',body)[1])
    def rows(path):return re.findall(r'^\t\[(\d+)\] = \{\n(.*?)^\t\}',path.read_text('utf-8-sig'),re.M|re.S)
    chapters={}
    for body in re.findall(r'^\t\{\n(.*?)^\t\}',files[0].read_text('utf-8-sig'),re.M|re.S):
        cid=scalar(body,'chapter_id')
        chapter={f:scalar(body,f) for f in ('previous_chapter','chapter_type')}
        chapter['stages']=[int(i) for i in re.findall(r'\{\s*(\d+)',re.search(r'\bstage_list = \{(.*?)\n\t\t\}',body,re.S)[1])]
        chapters[str(cid)]=chapter
    stages={i:{f:scalar(b,f) for f in ('chapter_id','previous_stage','event_lock')} for i,b in rows(files[1]) if scalar(b,'activity_id')==3941101}
    events={i:dict(condition=scalar(b,'condition'),options=list(map(int,re.findall(r'\d+',re.search(r'\bevent_option = \{(.*?)\}',b,re.S)[1])))) for i,b in rows(files[2])}
    options={i:dict(buff_id=scalar(b,'buff_id')) for i,b in rows(files[3])}
    battles={i:{f:scalar(b,f) for f in ('type','cost','hero_exp','user_exp') if re.search(r'\b'+f+r' = ',b)} for i,b in rows(files[4]) if i in stages}
    extra=[cfg/'publicbuffcfg.lua',cfg/'activityaffixpoolcfg.lua']
    buff_rows=dict(rows(extra[0]));pool_rows=dict(rows(extra[1]))
    affix_ids=list(map(int,re.findall(r'\d+',re.search(r'\baffix = \{(.*?)\}',
        next(b for b in re.findall(r'^\t\{\n(.*?)^\t\}',files[0].read_text('utf-8-sig'),re.M|re.S) if scalar(b,'chapter_id')==4),re.S)[1])))
    affixes={str(i):dict(point=scalar(pool_rows[str(scalar(buff_rows[str(i)],'buff_class_id'))],'point')) for i in affix_ids}
    catalog=ROOT/'v5_server/data/activity_content_cfg.json'
    tasks=json.loads(catalog.read_text('utf-8'))['versions']['229']['tasks']
    limited=sorted(int(i) for i,t in tasks.items() if t['activity_id'] in (3900401,3900402,3900403,3900404)
        or (400001<=t['condition']<=400005 and t['activity_id'] not in (0,3941101)))
    assert len(chapters)==4 and len(stages)==12 and len(events)==7 and len(options)==14 and len(battles)==12
    return dict(activity_id=3941101,parent_activity=3910001,limited_activities=[3900401,3900402,3900403,3900404],limited_tasks=limited,
        chapters=chapters,stages=stages,events=events,options=options,battles=battles,affixes=affixes,
        scoring=dict(kind='private_reward_calibrated_v1',reward_thresholds=sorted({t['additional_parameter'][0] for t in tasks.values() if t['condition']==400002}),
            clear_floor=max(t['additional_parameter'][0] for t in tasks.values() if t['condition']==400002),dimensions=4,time_bonus_limit_ms=300000,hit_bonus_limit=20,
            unresolved_dimensions=['combo','mechanism']),
        sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files+extra+[catalog]})


if __name__=='__main__':
    path=ROOT/'v5_server/hodur_contract.json';text=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text('utf-8')!=text:raise SystemExit('Back up the existing contract first')
    path.write_text(text,encoding='utf-8');print('Exported 4 chapters, 12 battles, 7 events and 14 options')
