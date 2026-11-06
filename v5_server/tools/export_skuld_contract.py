"""Export the native Skuld node, puzzle and first-clear contract read-only."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]


def build():
    base=ROOT/'decompiled_v2/x64/game/config'
    paths=[base/(n+'.lua') for n in ('skuldstagecfg','battleskuldstagecfg','skuldpuzzlestagecfg','dropcfg','stagearchivescollectcfg','conditioncfg')]
    nodes,battles,puzzles,drops,archives,conditions=map(parse_lua_file,paths)
    nodes={str(i):dict(type=v['type'][0],param=v['type'][1],pre_id=v['pre_id'],section=v['section'],
                         reward=v['reward'],rewards=[[r[0],r[1]] for r in drops[v['reward']]['base_drop']])
           for i,v in nodes.items() if isinstance(i,int)}
    battles={str(i):dict(tag=v['tag']) for i,v in battles.items() if isinstance(i,int)}
    archives={str(i):dict(name=v['name'],nodes=conditions[v['unlock_by_condition_id']]['params'] if v['unlock_by_condition_id'] else []) for i,v in archives.items() if isinstance(i,int) and 6010128 in v['chapter_id']}
    path=ROOT/'v5_server/data/activity_content_cfg.json'
    tasks=json.loads(path.read_text('utf-8'))['versions']['229']['tasks']
    limited=sorted(v['id'] for v in tasks.values() if v['activity_id'] in (320042,320043,320044,320045) or v['condition']==329001 and v['activity_id'] not in (0,321201))
    assert len(nodes)==36 and all(v['param'] in puzzles for v in nodes.values() if v['type']==2)
    return dict(activity_id=321201,parent_activity=321001,limited_activities=[320041,320042,320043,320044,320045],
                limited_tasks=limited,archives=archives,nodes=nodes,battles=battles,marks=list(range(1,11)),
                main_story_hint_stage=1011243,sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths+[path]})


if __name__=='__main__':
    path=ROOT/'v5_server/skuld_contract.json';text=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text('utf-8')!=text:raise SystemExit('Back up the previous contract first')
    path.write_text(text,'utf-8');print('Exported 36 native Skuld nodes and first-clear drops')
