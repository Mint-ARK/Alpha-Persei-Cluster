"""Narrow native Mode4 reward/condition contract for the two served first rounds."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]


def build():
    old=json.loads((ROOT/'v5_server/core_verification_cl_catalog.json').read_text('utf-8'))['rewards']
    result=dict(profiles={},source_sha256={})
    sources=[('229',ROOT/'decompiled_v2/x64/game/config',
              ('activitycfg.lua','coreverificationclrewardcfg.lua','conditioncfg.lua'),4343301),
             ('311',ROOT/'v5_server/tools/diff_lua_decompiled_311',
              ('ActivityCfg.lua','CoreVerificationClRewardCfg.lua','ConditionCfg.lua'),4443301)]
    for version,base,names,parent in sources:
        paths=[base/name for name in names]
        activities,rewards,conditions=map(parse_lua_file,paths)
        child=activities[parent]['sub_activity_list'][1]
        tasks={}
        for tid,row in rewards.items():
            if type(tid) is not int or row.get('activity_id')!=child or row.get('reward_type')!=1:continue
            condition=conditions[row['condition']]
            assert condition['type'] in (11304,11307)
            # Preserve existing customized rewards; fill genuinely missing 311 rows from Lua.
            items=old.get(str(tid),{}).get('rewards') or [dict(id=i,num=n) for i,n in row['reward']]
            tasks[str(tid)]=dict(condition_id=row['condition'],condition_type=condition['type'],
                                 params=condition['params'],items=items)
        assert len(tasks)==6
        result['profiles'][version]=dict(parent=parent,activity=child,tasks=tasks)
        for path in paths:
            result['source_sha256'][str(path.relative_to(ROOT)).replace('\\','/')]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


if __name__=='__main__':
    target=ROOT/'v5_server/core_mode4_rewards_cfg.json'
    if target.exists():raise SystemExit('Refuse to overwrite an existing exported contract without backup')
    target.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
