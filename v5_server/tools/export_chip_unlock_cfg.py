"""Export only native chip unlock metadata, referenced conditions and order."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
from extract_autochess_cfg import parse_lua_file

SERVER=Path(__file__).resolve().parents[1]
LUA=SERVER.parent/'decompiled_v2/x64/game/config'


def build_config():
    raw=parse_lua_file(str(LUA/'chipcfg.lua'))
    conditions=parse_lua_file(str(LUA/'conditioncfg.lua'))
    levels=parse_lua_file(str(LUA/'gamelevelsetting.lua'))
    chips={}
    used=set()
    sequences={}
    for cid,e in raw.items():
        if not isinstance(cid,int) or not isinstance(e,dict) or 'type_id' not in e:continue
        if e['type_id'] not in (1,2,3,4):raise ValueError('unsupported chip category')
        costs=e.get('cost_condition') or []
        if not isinstance(costs,list) or any(len(p)!=2 or p[0]<=0 or p[1]<0 for p in costs):
            raise ValueError(f'invalid costs {cid}')
        item={key:e[key] for key in ('type_id','spec_char','role_type_id','new_condition')}
        item.update(costs=costs,module_conditions=e.get('module_condition') or [])
        if item['type_id']==3:
            item['host_id']=item['spec_char']
            if item['new_condition']:used.add(item['new_condition'])
        elif item['type_id']==4:
            base=cid if item['role_type_id']==5 else item['spec_char']
            if raw[base]['type_id']!=4 or raw[base]['role_type_id']!=5:
                raise ValueError(f'invalid base for {cid}')
            item.update(base_id=base,host_id=raw[base]['spec_char'])
            used.update(item['module_conditions'])
            if item['role_type_id']==5:
                sequences[str(base)]=[base]+list(raw['get_id_list_by_spec_char'].get(base,[]))
        chips[str(cid)]=item
    exported={}
    for cid in sorted(used):
        c=conditions[cid]
        if c['type'] not in (1015,1025,1026,1027) or len(c['params'])!=2:
            raise ValueError(f'unsupported condition {cid}')
        exported[str(cid)]={key:c[key] for key in ('type','params')}
    love=[]
    for level in range(1,100):
        amount=levels[level]['hero_love_exp']
        love.append(amount)
        if amount==0:break
    if not love or love[-1]!=0:raise ValueError('missing affection cap')
    return dict(version=1,source='current Lua ChipCfg/ConditionCfg/GameLevelSetting and native spec_char index',
                chips=chips,conditions=exported,reviser_sequences=sequences,love_level_exp=love)


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--output',type=Path,default=SERVER/'chip_unlock_cfg.json')
    parser.add_argument('--backup-dir',type=Path)
    args=parser.parse_args()
    config=build_config()
    if args.output.exists():
        if not args.backup_dir:parser.error('overwriting requires --backup-dir')
        args.backup_dir.mkdir(parents=True,exist_ok=True)
        shutil.copy2(args.output,args.backup_dir/(datetime.now().strftime('%Y%m%d_%H%M%S_%f_')+args.output.name))
    args.output.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"chips={len(config['chips'])}, conditions={len(config['conditions'])}, sequences={len(config['reviser_sequences'])}")


if __name__=='__main__':main()
