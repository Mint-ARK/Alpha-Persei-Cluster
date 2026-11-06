"""Extract native SP Hero schedules/entrusts without changing client files."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]


def build():
    base=ROOT/'decompiled_v2/x64/game/config'
    names=['activityherochallengeschedulecfg','activityherochallengetaskcfg','activityherochallengecfg','gamesetting','conditioncfg',
           'battleverthandiexclusivecfg','dropcfg','activityrewardpoolcfg','activityrewardpoolitemcfg','assignmentcfg',
           'activityherochallengebarbecuecfg','activityherochallengecolor','shopcfg3']
    paths=[base/(n+'.lua') for n in names]
    schedules,entrusts,activities,settings,conditions,stages,drops,pools,items,tasks,barbecue,puzzles,shops=map(parse_lua_file,paths)
    numeric=lambda data:{str(i):v for i,v in data.items() if type(i) is int}
    return dict(activity=activities[242841],schedules=numeric(schedules),entrusts=numeric(entrusts),
        settings={k:v['value'] for k,v in settings.items() if k.startswith('activity_hero_challenge_')},
        conditions={str(i):v for i,v in conditions.items() if type(i) is int and 112001<=i<=112063},
        stages=numeric(stages),drops={str(i):drops[i] for i in {s['drop_lib_id'] for s in stages.values() if isinstance(s,dict) and 'drop_lib_id' in s}},
        boss_pool={str(i):v for i,v in items.items() if type(i) is int and v['activity_id'] in {i for i,v in pools.items() if type(i) is int and v['activity_id']==242851}},
        battle_tasks={str(i):v for i,v in tasks.items() if type(i) is int and v.get('activity_id') in (240045,240046) and v.get('condition') in (160002,160003,160007)},
        barbecue=numeric(barbecue),puzzles=numeric(puzzles),
        own_tasks={str(i):v for i,v in tasks.items() if type(i) is int and v.get('activity_id') in (240045,240046,242841)},
        shop={str(i):v for i,v in shops.items() if type(i) is int and v.get('shop_id')==216},
        source_sha256={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


if __name__=='__main__':
    output=ROOT/'v5_server/sphero_contract.json'
    output.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n','utf-8')
    print(output)
