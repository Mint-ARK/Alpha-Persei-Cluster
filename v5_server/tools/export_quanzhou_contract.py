"""Export native Citong/QWorld contracts without changing client files."""
import hashlib
import json
import re
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT = Path(__file__).resolve().parents[2]


def build():
    base = ROOT / 'decompiled_v2/x64/game/config'
    names = ['activitycfg', 'activitysandplaycfg', 'sandplaymapcfg', 'sandplaytaskmaincfg',
             'sandplaytaskcfg', 'conditioncfg', 'sandplaysystemcfg', 'sandplayfunctionopencfg',
             'quanzhouphasecfg', 'quanzhouphotocfg', 'seawarfarestagecfg', 'seawarfareskillcfg',
             'battlesandplaystagecfg', 'battleseawarfarestagecfg', 'activitypointrewardcfg',
             'dropcfg', 'itemcfg', 'sandplaytaskchaptercfg', 'assignmentcfg']
    paths = [base / (n + '.lua') for n in names]
    raw = dict(zip(names, map(parse_lua_file, paths)))
    numeric = lambda data: {str(i): v for i, v in data.items() if type(i) is int}
    mains = {i: v for i, v in raw['sandplaytaskmaincfg'].items()
             if type(i) is int and v.get('activity_id') == 323641}
    tasks = {i: v for i, v in raw['sandplaytaskcfg'].items()
             if type(i) is int and v.get('main_task_id') in mains}
    condition_ids = {4001, 70151}
    for row in mains.values():
        condition_ids.update(row.get('unlock_param') or [])
    sea = numeric(raw['seawarfarestagecfg'])
    drops = {row['reward'] for row in sea.values() if row['reward']}
    tips_path = base / 'tipscfg.lua'
    description = re.search(r'\[582003\] = \{\s*id = 582003,\s*desc = \[\[(.*?)\]\]',
                            tips_path.read_text('utf-8-sig'), re.S)
    assert description is not None
    result = dict(
        activity=raw['activitycfg'][323641], child_activity=raw['activitycfg'][323841],
        entry=raw['activitysandplaycfg'][323641], map=raw['sandplaymapcfg'][1110101],
        main_tasks=numeric(mains), tasks=numeric(tasks),
        conditions={str(i): raw['conditioncfg'][i] for i in sorted(condition_ids)},
        systems={str(i): raw['sandplaysystemcfg'][i] for i in raw['sandplaymapcfg'][1110101]['system_list']},
        function_open={str(i): v for i, v in raw['sandplayfunctionopencfg'].items()
                       if type(i) is int and v.get('map_id') == 1110101},
        phase=numeric(raw['quanzhouphasecfg']), photo=numeric(raw['quanzhouphotocfg']),
        sea_stages=sea, sea_skills=numeric(raw['seawarfareskillcfg']),
        sand_battles=numeric(raw['battlesandplaystagecfg']), sea_battles=numeric(raw['battleseawarfarestagecfg']),
        point_rewards={str(i): v for i, v in raw['activitypointrewardcfg'].items()
                       if type(i) is int and v.get('system_key') == 40007701},
        drops={str(i): raw['dropcfg'][i] for i in sorted(drops)},
        level_item=raw['itemcfg'][77],
        description=description.group(1).strip(),
        level_tasks={str(i): raw['assignmentcfg'][i] for i in range(60501, 60505)},
        chapters={str(i): v for i, v in raw['sandplaytaskchaptercfg'].items()
                  if type(i) is int and any(mid in mains for mid in v.get('task_id_list', []))},
        source_sha256={str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in [*paths, tips_path]})
    assert len(mains) == 53 and len(tasks) == 129
    assert {v['condition'] for v in tasks.values()} == {60, 330001, 330002}
    assert len(sea) == 11 and len(result['point_rewards']) == 10
    return result


if __name__ == '__main__':
    output = ROOT / 'v5_server/quanzhou_contract.json'
    if output.exists():
        archive = ROOT / 'v5_server/archive/quanzhou_20261105'
        archive.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()[:12]
        backup = archive / ('quanzhou_contract_' + digest + '.json')
        if not backup.exists():
            backup.write_bytes(output.read_bytes())
    output.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(output)
