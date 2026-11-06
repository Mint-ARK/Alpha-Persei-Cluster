"""Export native Summer ChessBoard bootstraps; leave client assets untouched."""
import hashlib
import json
from pathlib import Path
from extract_autochess_cfg import parse_lua_file

ROOT=Path(__file__).resolve().parents[2]


def build():
    base=ROOT/'decompiled_v2/x64/game/config'
    names=['chessboardlevelcfg','chessboardattributecfg','chessboardeventpoolcfg','chessboardtaskpoolcfg',
           'chessboardbuffpoolcfg','chessboardnpcpoolcfg','conditioncfg','chessboardhexcfg','chessboardthingcfg','chessboardbuffeffectcfg',
           'chessboardchancecfg','chessboardpropcfg','chessboardgamesettingcfg','activitywhackmolecfg',
           'activitytangramgamestagecfg','assignmentcfg']
    paths=[base/(n+'.lua') for n in names]
    levels,attrs,events,tasks,buffs,npcs,conditions,hexes,things,effects,chances,props,settings,whack,tangram,assignments=map(parse_lua_file,paths)
    levels={str(i):{k:v[k] for k in ['activity','id_level','init_pos','init_dir','eventpool_id','unlock_condition','next_level','level_event']}
            for i,v in levels.items() if isinstance(i,int) and 283021<=v['activity']<=283025}
    attrs={str(v['id']):{k:v[k] for k in ['enum','default','min','max']} for v in attrs.values() if isinstance(v,dict) and 'id' in v}
    events={str(i):{k:v[k] for k in ['id','type','player_params','npc_params']} for i,v in events.items() if isinstance(i,int)}
    tasks={str(i):{k:v[k] for k in ['need','type','cost','condition_type','finishevent']} for i,v in tasks.items() if isinstance(i,int)}
    buffs={str(i):{k:v[k] for k in ['add_event','turn_event','destory_event','remainround','updateround','buff_efffect','clash','buff_type']} for i,v in buffs.items() if isinstance(i,int)}
    npcs={str(i):{k:v[k] for k in ['init_attribute_list','player_stay_npc_event','player_cross_npc_event','npc_start_event','npc_stop_event','npc_stay_player_event','npc_cross_player_event','npc_stay_npc_event']} for i,v in npcs.items() if isinstance(i,int)}
    hexes={str(i):{k:v[k] for k in ['walkable','updatable']} for i,v in hexes.items() if isinstance(i,int)}
    things={str(i):{k:v[k] for k in ('fix_walkable','destoryable')} for i,v in things.items() if isinstance(i,int)}
    effects={str(i):{k:v[k] for k in ['trigger_time','trigger_count','event']} for i,v in effects.items() if isinstance(i,int)}
    chances={str(i):{k:v[k] for k in ['cost','chance_event','type','params']} for i,v in chances.items() if isinstance(i,int)}
    props={str(i):{k:v[k] for k in ['cost','type','params']} for i,v in props.items() if isinstance(i,int)}
    conditions={str(i):v for i,v in conditions.items() if isinstance(i,int) and v.get('type')==11501 and all(str(s) in levels for s in v['params'])}
    map_tasks={cid:[int(tid) for tid in tasks if int(tid)//100==next(r[1]//100 for r in events[str(v['eventpool_id'])]['player_params'] if r[0]==10012)] for cid,v in levels.items()}
    external_tasks={str(i):{k:v[k] for k in ('condition','need','additional_parameter','activity_id','type','reward','phase')}
        for i,v in assignments.items() if isinstance(i,int) and
        (v['condition']==163003 and v['additional_parameter'] and str(v['additional_parameter'][0]) in tasks or
         v['condition']==163001 and (not v['additional_parameter'] or all(str(p) in levels for p in v['additional_parameter'])))}
    nested={cid:dict(type={3:70,8:74,10:75,11:72,13:73}.get(v['type'],0),
        dest=0 if v['type']==8 else v['params'][0],stage=whack[v['params'][0]]['stage_id'] if v['type']==3 else v['params'][0])
        for cid,v in chances.items() if v['type'] in (3,7,8,10,11,12,13)}
    assert len(levels)==13 and all(c['type']==11501 and all(str(i) in levels for i in c['params']) for c in conditions.values())
    return dict(activity_id=283011,levels=levels,attributes=attrs,events=events,tasks=tasks,buffs=buffs,npcs=npcs,
                conditions=conditions,hexes=hexes,things=things,effects=effects,chances=chances,props=props,
                arena_props=settings[1]['chessboard_dice_type_prop'],nested=nested,map_tasks=map_tasks,external_tasks=external_tasks,
                implementation_phase='nested_and_progress',
                sources={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


if __name__=='__main__':
    path=ROOT/'v5_server/summer_board_contract.json';text=json.dumps(build(),ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text('utf-8')!=text:raise SystemExit('Back up the old contract first')
    path.write_text(text,'utf-8');print('Exported 13 Summer ChessBoard map bootstraps')
