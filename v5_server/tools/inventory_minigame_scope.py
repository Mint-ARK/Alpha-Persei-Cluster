"""Client request inventory + current runtime declarations; never mounts a save."""
import ast
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'v5_server'))
os.environ['ACCOUNT_DB_PATH']=':memory:'

MINI_NAMES={
 'activityautocookaction','activitypushboxaction','activityquizaction','activityrhythmgameaction',
 'antitheticalcoupletaction','blistergameaction','cowboyaction','destroyboxgameaction','fishingaction',
 'godeateraction','ichibanaction','idolcompetitionaction','kagutsuchifishingeventaction','kagutsuchihanafudaaction',
 'kagutsuchiworkaction','monstercosplayaction','moonboaction','musicaction','pachinkoaction','parkouraction',
 'pinballaction','roguecardgameaction','simbusinessaction','skuldtravelaction','slayeraction','snowballgameaction',
 'springfestivalriddleaction','strongholdaction','summerlittlegameaction','swimsuitbattleaction','towergameaction',
 'valentinegameaction','zumaaction',
}


def build():
    import core,operations,minigame_service,peripheral_service,autochess_service
    from middleware import CORE_HANDLERS
    declared={}
    for path in (ROOT/'v5_server').glob('*.py'):
        for node in ast.parse(path.read_text('utf-8-sig')).body:
            if not isinstance(node,ast.ClassDef):continue
            if not any(isinstance(d,ast.Name) and d.id=='operation' for d in node.decorator_list):continue
            for item in node.body:
                if isinstance(item,ast.Assign):
                    for target in item.targets:
                        if isinstance(target,ast.Name) and target.id=='cmd' and isinstance(item.value,ast.Constant) and isinstance(item.value.value,int):
                            declared.setdefault(item.value.value,[]).append(dict(file=str(path.relative_to(ROOT)).replace('\\','/'),line=node.lineno,operation=node.name))
    rows=[]
    folder=ROOT/'decompiled_v2/x64/game/action/activitysubmodule'
    for path in sorted(folder.rglob('*.lua')):
        source=path.read_text('utf-8-sig')
        cmds=sorted(set(int(x) for x in re.findall(r'(?:SendWithLoading(?:New)?|Send)\(\s*(\d+)',source)))
        if not cmds:continue
        commands=[]
        for cmd in cmds:
            op=core.OPERATIONS.get(cmd)
            handler=CORE_HANDLERS.get(cmd)
            commands.append(dict(cmd=cmd,runtime_operation=type(op).__module__+'.'+type(op).__name__ if op else None,
                runtime_handler=handler.__name__ if handler else None,declared=declared.get(cmd,[]),
                coverage='runtime_entry' if op or handler else ('declaration_only' if cmd in declared else 'no_business_entry')))
        rows.append(dict(id='MG-C'+str(len(rows)+1).zfill(3),client=str(path.relative_to(ROOT)).replace('\\','/'),
            family=path.stem,scope='minigame' if path.stem in MINI_NAMES else 'activity_reference',
            scan_status='pending_chain_audit',live_status='not_tested',commands=commands,
            business_gaps=[v['cmd'] for v in commands if v['coverage']!='runtime_entry']))
    # Existing service families that are not all routed through this client folder.
    service=ROOT/'v5_server/minigame_service.py';headings=[]
    for number,line in enumerate(service.read_text('utf-8-sig').splitlines(),1):
        if line.startswith('# ---') and '元数据' in line:headings.append(dict(line=number,title=line.strip('# -')))
    return dict(version=1,source='current x64 client action calls and current Python runtime registry',
        historical_unfinished_count=3,historical_unfinished_names='unconfirmed; user no longer recalls names',
        exclusions_revoked='2026-11-04: minigames and Issue 8 RogueTeam now in scope',
        policy='入口存在不代表有持久进度/奖励/恢复，更不代表实机已验证。无业务入口也须先核是否为本地玩法或明确禁用。',
        service='v5_server/minigame_service.py',service_families=headings,client_families=rows,
        pending_regressions=[dict(id='MG-ROGUECARD-EXIT',name='诡谈夜话任务页返回Lua异常',status='pending_reproduction',
                                 evidence='archive/activity_catalog_fix_20261025_131213/rogue_task_exit_client.log')])


if __name__=='__main__':
    data=build();path=ROOT/'v5_server/docs/minigame_audit_scope_20261104.json'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n','utf-8')
    mini=[r for r in data['client_families'] if r['scope']=='minigame']
    print('service families',len(data['service_families']),'client game families',len(mini),'reference families',len(data['client_families'])-len(mini))
    print('requires route review',[(r['family'],r['business_gaps']) for r in mini if r['business_gaps']])
