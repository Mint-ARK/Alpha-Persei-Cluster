"""Native p49 exploration state. SQL/encoding share the caller's savepoint.

Grid tag is the one-based extendInfo index; state is the grid's status. Client
Lua executes the acknowledged event queue locally, so no map snapshot is pushed
after an interaction. Unknown event semantics fail the entire operation.
"""
from copy import deepcopy
import json
from pathlib import Path
import random
import time
import uuid

from core import OperationError

CFG=json.loads(Path(__file__).with_name('warchess_contract.json').read_text('utf-8'))
LEVELS={int(k):v for k,v in CFG['levels'].items()}
DIRECTIONS=((1,1),(0,1),(-1,0),(-1,-1),(0,-1),(1,0))
KIND='warchess_runtime_v2'


def require(value,message):
    if not value:raise OperationError(2,message)


def _int(value):return type(value) is int and 0<=value<=0xffffffff


def position(value):
    require(isinstance(value,dict) and all(type(value.get(k)) is int and -0x80000000<=value[k]<=0x7fffffff for k in ('x','z')),'Invalid chess position')
    return value['x'],value['z']


def read(db,uid):
    require(db is not None and db.get('users',uid) is not None,'Missing chess account')
    rows=db.query("SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id='active'",(uid,KIND))
    value=json.loads(rows[0]['value_json']) if rows else None
    if value is not None:
        require(isinstance(value,dict) and value.get('version')==2 and value.get('chapter') in LEVELS and isinstance(value.get('run'),str) and bool(value['run']),'Invalid chess run')
        m=value.get('map');require(isinstance(m,dict),'Invalid chess map')
        position(m.get('pos'))
        require(all(isinstance(m.get(k),list) for k in ('map','hp_list','log','event_list','event_info','fog','guide_pos')),'Invalid chess saved lists')
        require(isinstance(m.get('bag'),dict) and all(isinstance(m['bag'].get(k),list) for k in ('item','artifact')),'Invalid chess bag')
        require(all(_int(i) and str(i) in CFG['items'] for i in m['bag']['artifact']),'Invalid chess artifact')
        for row in m['map']:
            position(row.get('pos'));require(all(_int(row.get(k)) for k in ('tag','state','rotation')),'Invalid chess grid')
        require(isinstance(value.get('events'),list) and _int(value.get('choice_count')),'Invalid chess event record')
    return value


def save(db,uid,state):
    db.execute("INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,'active',?) ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json",
               (uid,KIND,json.dumps(state,ensure_ascii=False,allow_nan=False)))
    if state is None:return
    cid=state['chapter'];m=state['map'];group=int(LEVELS[cid]['type'])
    db.execute('INSERT INTO warchess_map(uid,activity_id,current_chapter) VALUES(?,?,?) ON CONFLICT(uid,activity_id) DO UPDATE SET current_chapter=excluded.current_chapter',(uid,group,cid))
    db.execute('''INSERT INTO warchess_session(uid,chapter_id,pos_x,pos_z,direction,is_fog,fog_json,map_json,bag_json,hp_json,logs_json,events_json,update_ts)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uid,chapter_id) DO UPDATE SET pos_x=excluded.pos_x,pos_z=excluded.pos_z,
        direction=excluded.direction,is_fog=excluded.is_fog,fog_json=excluded.fog_json,map_json=excluded.map_json,bag_json=excluded.bag_json,
        hp_json=excluded.hp_json,logs_json=excluded.logs_json,events_json=excluded.events_json,update_ts=excluded.update_ts''',
        (uid,cid,m['pos']['x'],m['pos']['z'],m['direction'],int(m['is_fog']),*[json.dumps(m[k],ensure_ascii=False) for k in ('fog','map','bag','hp_list','log','event_list')],int(time.time())))


def active(db,uid):
    state=read(db,uid);require(state is not None,'No active chess exploration');return state


def progress(db,uid,chapter):
    counts={int(r['box_id']):int(r['num']) for r in db.query('SELECT box_id,num FROM warchess_box WHERE uid=? AND chapter_id=?',(uid,chapter))}
    cfg=LEVELS[chapter]
    return min(100,counts.get(10203,0)*cfg['sbox_progress']+counts.get(10204,0)*cfg['lbox_progress'])


def overview_payload(db,uid):
    # A separate native group is required for each level.type (WarChessData).
    current={int(r['activity_id']):int(r['current_chapter']) for r in db.query('SELECT activity_id,current_chapter FROM warchess_map WHERE uid=?',(uid,))}
    box={}
    for r in db.query('SELECT chapter_id,box_id,num FROM warchess_box WHERE uid=?',(uid,)):
        box.setdefault(int(r['chapter_id']),[]).append(dict(id=int(r['box_id']),num=int(r['num'])))
    groups={}
    for cid,cfg in LEVELS.items():groups.setdefault(int(cfg['type']),[]).append(dict(chapter_id=cid,box=box.get(cid,[])))
    return dict(chess_map_list=[dict(activity_id=g,chapter=current.get(g,0) if current.get(g,0) in LEVELS and LEVELS[current[g]]['type']==g else 0,chapter_info=rows) for g,rows in sorted(groups.items())])


def open_payload(db):
    opened={int(r['chapter_id']):int(r['open_ts'] or 0) for r in db.query('SELECT chapter_id,open_ts FROM warchess_chapter')}
    return dict(chess_open_info_list=[dict(chapter_id=i,timestamp=opened.get(i,0)) for i in sorted(LEVELS)])


def _event_info(db,uid,state):
    events=list(state['events'])
    for r in db.query('SELECT box_id,tag,pos_x,pos_z FROM warchess_box_record WHERE uid=? AND chapter_id=?',(uid,state['chapter'])):
        events.append([int(r['box_id']),int(r['pos_x']),int(r['pos_z']),int(r['tag'])])
    out={}
    for eid,x,z,tag in events:
        row=dict(pos=dict(x=x,z=z),tag=tag)
        if row not in out.setdefault(eid,[]):out[eid].append(row)
    return [dict(event_id=eid,pos_list=rows) for eid,rows in sorted(out.items())]


def project_tasks(db,uid):
    now=int(time.time());changes=[]
    counts={int(r['box_id']):int(r['n']) for r in db.query('SELECT box_id,SUM(num) n FROM warchess_box WHERE uid=? GROUP BY box_id',(uid,))}
    values={cid:progress(db,uid,cid) for cid in LEVELS}
    for r in db.query('''SELECT t.task_id,t.progress,c.condition,c.need FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND c.condition IN (460,461,462,463) AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
        AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
        AND NOT EXISTS(SELECT 1 FROM claim_ledger l WHERE l.uid=t.uid AND l.key_id=t.task_id AND l.kind IN ('task','illustrated_task'))''',(uid,now)):
        cfg=CFG['tasks'].get(str(r['task_id']))
        if not cfg:continue
        cond=cfg['condition'];params=cfg['additional_parameter'] or []
        if cond==460:value=counts.get({1:10203,2:10204}.get(params[0] if params else 0,0),0)
        elif cond in (462,463):
            targets=[p for p in params if p] or list(LEVELS)
            value=sum(values.get(cid,0)>=(100 if cond==463 else LEVELS.get(cid,{}).get('success_progress',101)) for cid in targets)
        else:continue  # 461's native condition is not inferred from its number.
        value=min(int(r['need']),value)
        if value>int(r['progress'] or 0):
            db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?',(value,uid,r['task_id']))
            changes.append(dict(id=r['task_id'],progress=value,complete_flag=0))
    return changes


class Engine:
    def __init__(self,db,uid,state,ctx=None):
        self.db=db;self.uid=uid;self.s=state;self.m=state['map'];self.cid=state['chapter'];self.native=CFG['maps'][str(self.cid)]
        self.ctx=ctx;self.frames=[];self.steps=0;self.stop=False

    def grid(self,pos):
        x,z=pos;key=f'{x},{z}';require(key in self.native['tiles'],'Chess position outside native map')
        row=next((r for r in self.m['map'] if position(r['pos'])==pos),None)
        tag=row['tag'] if row else 0
        require(0<=tag<=len(self.native['extend']),'Invalid saved chess extension')
        raw=self.native['extend'][tag-1] if tag else self.native['tiles'][key]
        return dict(typeID=raw['typeID'],status=row['state'] if row else raw['status'],tag=tag,
                    rotation=row['rotation'] if row else raw['rotationY'],params=raw['paramList'],pos=dict(x=x,z=z),
                    attribute=deepcopy(row['attribute']) if row else [])

    def hex(self,g):return CFG['hexes'].get(str(g['typeID']*10+g['status'])) if g['status'] and str(g['typeID']*10+g['status']) in CFG['hexes'] else CFG['hexes'][str(g['typeID'])]

    def change(self,pos,tag=None,state=None,rotation=None):
        g=self.grid(pos)
        if tag is not None:
            require(_int(tag) and tag<=len(self.native['extend']),'Unknown native extension')
            raw=self.native['extend'][tag-1] if tag else self.native['tiles'][f'{pos[0]},{pos[1]}']
            g.update(tag=tag,status=raw['status'],rotation=raw['rotationY'],attribute=[])
        if state is not None:g['status']=state
        if rotation is not None:g['rotation']=rotation%360
        row=dict(tag=g['tag'],pos=dict(x=pos[0],z=pos[1]),state=g['status'],attribute=g['attribute'],rotation=g['rotation'])
        self.m['map']=[r for r in self.m['map'] if position(r['pos'])!=pos]+[row]

    def actions(self,pos,secondary=False):
        g=self.grid(pos)
        if not g['params']:return []
        pool=CFG['pools'].get(str(g['params'][0]));require(pool is not None,'Missing native chess event pool')
        return deepcopy(pool['event_group_second' if secondary else 'event_group'] or [])

    def mark(self,eid,pos,tag):
        record=[eid,*pos,tag]
        if record not in self.s['events']:self.s['events'].append(record)

    def item(self,iid,num):
        cfg=CFG['objects'].get(str(iid));require(cfg is not None,'Unknown local chess item')
        items=self.m['bag']['item'];old=next((r['num'] for r in items if r['type']==iid),0);value=old+num
        require(value>=0,'Insufficient local chess item')
        if cfg['max']>0:value=min(value,cfg['max'])
        self.m['bag']['item']=[r for r in items if r['type']!=iid]+([dict(type=iid,num=value)] if value else [])

    def hp(self,amount):
        for r in self.m['hp_list']:
            if r['hp']>0:r['hp']=max(0,min(10000,r['hp']+amount))

    def reward(self,event,drop,pos,tag):
        if self.db.is_warchess_box_opened(self.uid,self.cid,event,tag,*pos):return
        cfg=CFG['drops'].get(str(drop));require(cfg is not None,'Missing native chess drop')
        require(not cfg['weight_drop'] and not cfg['random_drop'] and not cfg['weight_drop_count'],'Unsupported nonfixed native chess drop')
        require(self.ctx is not None and all(len(r)>=3 and r[2]==100 for r in cfg['base_drop']),'Chess drop probability requires explicit support')
        if not self.db.record_warchess_box_opened(self.uid,self.cid,event,tag,*pos):return
        from operations import _item_add
        for iid,num,_ in cfg['base_drop']:_item_add(self.ctx,self.uid,iid,num,source='warchess_box')

    def choices(self):
        # Rarity probabilities are native; uniform sampling within one rarity
        # has no client weight field and is explicitly a server policy.
        self.s['choice_count']+=1;weights=CFG['rarities'][str(min(self.s['choice_count'],max(map(int,CFG['rarities']))))]['rare_weight']
        available=[int(i) for i,r in CFG['items'].items() if not r['identify_buff'] and int(i) not in self.m['bag']['artifact']]
        result=[]
        for _ in range(3):
            eligible=[(rarity,w) for rarity,w in enumerate(weights,1) if w>0 and any(CFG['items'][str(i)]['item_rare']==rarity for i in available)]
            require(eligible,'No native chess artifact candidates')
            rarity=random.choices([r for r,w in eligible],weights=[w for r,w in eligible])[0]
            iid=random.choice([i for i in available if CFG['items'][str(i)]['item_rare']==rarity]);available.remove(iid);result.append(iid)
        return result

    def queue(self,actions,pos,tag=None,depth=0):
        require(depth<32,'Chess event recursion limit')
        tag=self.grid(pos)['tag'] if tag is None else tag
        queue=deepcopy(actions)
        while queue:
            self.steps+=1;require(self.steps<=1024,'Chess event action limit')
            row=queue.pop(0);eid=row[0];p=row[1:]
            if eid in (10000,10102,10103,10104,10105,10107,10108,10307,10701,10702,10703):pass
            elif eid==10101:
                entry=dict(index=pos[1]*self.native['width']+pos[0]+1,tag=tag,log=p[0])
                if entry not in self.m['log']:self.m['log'].append(entry)
            elif eid==10106:self.mark(eid,pos,tag)
            elif eid==10201:
                require(str(p[0]) in CFG['stages'],'Unknown native chess battle')
                if [eid,*pos,tag] in self.s['events']:continue
                self.s['pending']=dict(kind='battle',pos=list(pos),tag=tag,stage_id=p[0],rest=queue,event=eid,token=uuid.uuid4().hex,battle_id=None)
                return
            elif eid==10202:
                choices=self.choices();self.s['pending']=dict(kind='artifact',pos=list(pos),tag=tag,rest=queue,event=eid,choices=choices)
                self.frames.append((49011,dict(tag=tag,pos=dict(x=pos[0],z=pos[1]),event_id=eid,attribute=choices)))
                return
            elif eid in (10203,10204):self.reward(eid,p[0],pos,tag)
            elif eid==10205:
                require(str(p[0]) in CFG['items'] and CFG['items'][str(p[0])]['identify_buff'],'Invalid chess debuff')
                if p[0] not in self.m['bag']['artifact']:self.m['bag']['artifact'].append(p[0])
                self.stop=True
            elif eid in (10206,10404):
                self.s['pending']=dict(kind='purify' if eid==10206 else 'revive',pos=list(pos),tag=tag,rest=queue,event=eid)
                return
            elif eid==10301:self.change(pos,tag=p[0])
            elif eid==10302:self.change((p[0],p[1]),tag=p[2])
            elif eid in (10303,10304,10308,10309):
                target=pos if eid in (10303,10308) else (p[0],p[1]);values=p[0] if target==pos and eid in (10303,10308) else p[2]
                require(isinstance(values,list) and values,'Invalid chess state cycle')
                old=self.grid(target)['status'];n=(values.index(old)+1)%len(values) if old in values else 0
                self.change(target,state=values[n])
            elif eid==10305:self.change(pos,rotation=self.grid(pos)['rotation']+p[0]*60)
            elif eid==10306:
                target=(p[0],p[1]);self.change(target,rotation=self.grid(target)['rotation']+p[2]*60)
            elif eid in (10401,10402):self.hp(p[0]*100*(1 if eid==10401 else -1));self.stop|=eid==10402
            elif eid==10403:
                target=(p[0],p[1]);require(self.hex(self.grid(target))['move']>0,'Invalid chess teleport')
                self.m['pos']=dict(x=p[0],z=p[1]);self.stop=True
            elif eid==10501:self.item(p[0],p[1])
            elif eid==10502:
                if next((r['num'] for r in self.m['bag']['item'] if r['type']==p[0]),0)<p[1]:return
                self.item(p[0],-p[1]);self.change(pos,state=p[2])
            elif eid in (10601,10603):
                target=pos if eid==10601 else (p[0],p[1]);cases=p[0] if eid==10601 else p[2]
                nested=next((r[1] for r in cases if r[0]==self.grid(target)['status']),[])
                queue=deepcopy(nested)+queue
            elif eid==10602:queue=deepcopy(p[0])+queue
            elif eid==10901:
                attr=self.grid(pos)['attribute']
                if (attr[0] if attr else 0)<=p[0]:self.change(pos,tag=p[1])
                else:return
            elif eid==10902:
                for r in self.m['event_list']:
                    if CFG['globals'][str(r['id'])]['type']==8:r['progress']+=p[0]
            elif eid==11001:self.m['guide_pos']=[p[0],p[1]]
            elif eid==11002:
                require(len(p[0])>=2 and len(p[0])%2==0,'Invalid chess guide path');self.m['guide_pos']=p[0][-2:]
            else:raise OperationError(2,f'Native chess event {eid} needs a verified implementation')
            self.s['event_counts'][str(eid)]=self.s['event_counts'].get(str(eid),0)+1

    def globals(self,start=False):
        # Native narrative/progress/key/event globals. Advanced area/order/seal
        # mechanics stay explicit in coverage instead of silently completing.
        for event in list(self.m['event_list']):
            cfg=CFG['globals'][str(event['id'])];kind=cfg['type'];params=cfg['params'];ready=False
            require(kind in (1,2,3,4,8),f'Native chess global type {kind} needs a verified implementation')
            if kind==1:ready=start
            elif kind==2:event['progress']=progress(self.db,self.uid,self.cid);ready=event['progress']>=params[0]
            elif kind==3:
                event['progress']=next((r['num'] for r in self.m['bag']['item'] if r['type']==params[0]),0);ready=event['progress']>=params[1]
            elif kind==4:event['progress']=self.s['event_counts'].get(str(params[0]),0);ready=event['progress']>=params[1]
            elif kind==8:ready=event['progress']>=params[0]
            if ready:
                self.m['event_list'].remove(event)
                self.queue(cfg['event_list'] or [],position(self.m['pos']))
                require(not self.s.get('pending'),'Global chess event requires an explicit continuation')

    def finish(self):
        self.m['event_info']=_event_info(self.db,self.uid,self.s);save(self.db,self.uid,self.s)
        changes=project_tasks(self.db,self.uid)
        if changes:self.frames.append((28007,dict(progress_list=changes)))
        return self.frames


def _new_state(db,uid,cid):
    native=CFG['maps'][str(cid)];spawn=db.WARCHESS_SPAWN_POINTS.get(cid);require(spawn is not None,'Missing reviewed chess spawn')
    x,z=map(int,spawn);fog=bool(LEVELS[cid]['is_fog'])
    # Preserve the existing full-visibility policy; dimensions now come from
    # native maps instead of guessed 10x10. No unseen C# reveal radius is guessed.
    m=dict(pos=dict(x=x,z=z),direction=0,is_fog=fog,fog=[0xffffffff]*((native['width']*native['height']+31)//32) if fog else [native['width'],native['height']],
        map=[],bag=dict(item=[],artifact=[]),hp_list=[],log=[],event_list=[dict(id=int(i),progress=0) for i,r in CFG['globals'].items() if r['map_id']==cid],
        is_viewed_log=False,guide_pos=[],event_info=[])
    return dict(version=2,run=uuid.uuid4().hex,chapter=cid,map=m,pending=None,events=[],event_counts={},choice_count=0,last_move=None)


def enter(ctx,uid,cid):
    require(type(cid) is int and cid in LEVELS,'Unknown chess chapter')
    cfg=LEVELS[cid];unlock=cfg['unlock_level']
    require(not unlock or progress(ctx.db,uid,unlock)>=cfg['success_progress'],'Chess predecessor is not complete')
    condition=cfg['unlock_condition']
    if condition:
        require(condition[0] in (1,2) and len(condition[1])>=2,'Unknown chess narrative prerequisite')
        sid=condition[1][1]
        rows=ctx.db.query('SELECT clear_times FROM chapter WHERE uid=? AND id=? UNION ALL SELECT clear_times FROM stage_sub WHERE uid=? AND id=?',(uid,sid,uid,sid))
        require(any(int(r['clear_times'] or 0)>0 for r in rows),'Chess narrative prerequisite is not complete')
    state=read(ctx.db,uid)
    if state and state['chapter']==cid:
        engine=Engine(ctx.db,uid,state,ctx);frames=[];pending=state.get('pending')
        if pending and pending['kind']=='artifact':frames.append((49011,dict(tag=pending['tag'],pos=dict(x=pending['pos'][0],z=pending['pos'][1]),event_id=10202,attribute=pending['choices'])))
        state['map']['event_info']=_event_info(ctx.db,uid,state)
        return dict(result=0,map_info=deepcopy(state['map'])),frames
    if state:quit(ctx.db,uid)
    legacy=ctx.db.query('SELECT * FROM warchess_session WHERE uid=? AND chapter_id=?',(uid,cid))
    if legacy:
        # Old handlers conflated extension/tag and status and issued arbitrary
        # artifacts. Preserve their exact row before rebuilding a native run;
        # never reinterpret those ambiguous fields as proven event completion.
        ctx.db.execute('INSERT OR IGNORE INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?)',
                       (uid,KIND,'legacy:'+str(cid),json.dumps(legacy[0],ensure_ascii=False)))
    state=_new_state(ctx.db,uid,cid);engine=Engine(ctx.db,uid,state,ctx)
    # Previously claimed boxes retain their native extension and event marker.
    for r in ctx.db.query('SELECT box_id,tag,pos_x,pos_z FROM warchess_box_record WHERE uid=? AND chapter_id=?',(uid,cid)):
        pos=(int(r['pos_x']),int(r['pos_z']));tag=int(r['tag'])
        if f'{pos[0]},{pos[1]}' not in engine.native['tiles']:continue
        if tag:engine.change(pos,tag=tag)
        actions=engine.actions(pos)
        if any(a[0]==int(r['box_id']) for a in actions):
            for a in actions:
                if a[0]==10301:engine.change(pos,tag=a[1])
    state['map']['event_info']=_event_info(ctx.db,uid,state)
    wire=deepcopy(state['map']);engine.globals(start=True);engine.finish()
    return dict(result=0,map_info=wire),engine.frames


def move(ctx,uid,path):
    require(isinstance(path,list) and len(path)<=1024,'Invalid chess movement path')
    state=active(ctx.db,uid);engine=Engine(ctx.db,uid,state,ctx);coords=[position(p) for p in path]
    if state.get('last_move') and state['last_move']['path']==path and state['last_move']['after']==state['map']['pos']:
        return dict(result=0),[]
    pending=state.get('pending')
    require(not pending or pending['kind'] in ('battle','purify','revive') and not pending.get('battle_id'),'Chess decision or battle is pending')
    if pending:state['pending']=None
    state['last_receipt']=None
    origin=position(state['map']['pos']);current=origin
    for n,pos in enumerate(coords):
        if n==0 and pos==current:continue
        delta=(pos[0]-current[0],pos[1]-current[1]);require(delta in DIRECTIONS,'Nonadjacent chess movement')
        g=engine.grid(pos);h=engine.hex(g);require(h['move']>0,'Chess path crosses a blocked tile')
        state['map']['pos']=dict(x=pos[0],z=pos[1]);state['map']['direction']=DIRECTIONS.index(delta);current=pos
        if h['interact']==2:engine.queue(engine.actions(pos),pos);engine.globals()
        if engine.stop or state.get('pending'):break
    state['last_move']=dict(path=deepcopy(path),after=deepcopy(state['map']['pos']))
    engine.finish();return dict(result=0),engine.frames


def interact(ctx,uid,data):
    pos=position(data.get('pos'));kind=data.get('type');param=data.get('param',0)
    require(type(kind) is int and kind in (1,2) and _int(param),'Invalid chess interaction')
    state=active(ctx.db,uid);engine=Engine(ctx.db,uid,state,ctx);current=position(state['map']['pos'])
    require(pos==current or (pos[0]-current[0],pos[1]-current[1]) in DIRECTIONS,'Remote chess interaction')
    pending=state.get('pending');state['last_move']=None;state['last_receipt']=None
    if pending and pending['pos']==list(pos):
        require(kind==1,'Wrong chess continuation kind')
        if not param and pending['kind']=='artifact':
            return dict(result=0),[(49011,dict(tag=pending['tag'],pos=dict(x=pos[0],z=pos[1]),event_id=10202,attribute=pending['choices']))]
        if pending['kind']=='battle':
            # Reopening the same battle after a cold server restart is legal;
            # the next54030 replaces its published attempt identity.
            require(not param,'Unexpected chess battle selection');return dict(result=0),[]
        require(param>0,'Chess selection is required')
        if pending['kind']=='artifact':
            require(param in pending['choices'],'Unissued chess artifact');state['map']['bag']['artifact'].append(param)
        elif pending['kind']=='purify':
            require(param in state['map']['bag']['artifact'] and CFG['items'][str(param)]['identify_buff'],'Invalid chess purification');state['map']['bag']['artifact'].remove(param)
        elif pending['kind']=='revive':
            hero=next((h for h in state['map']['hp_list'] if h['id']==param),None);require(hero is not None and hero['hp']==0,'Invalid chess revive');hero['hp']=10000
        else:raise OperationError(2,'Unknown chess continuation')
        state['pending']=None;engine.mark(pending['event'],pos,pending['tag']);engine.queue(pending['rest'],pos,pending['tag'])
    else:
        require(not param,'Unsolicited chess selection')
        require(not pending or pending['kind'] in ('purify','revive') or pending['kind']=='battle' and not pending.get('battle_id'),'Another chess decision is pending')
        state['pending']=None;g=engine.grid(pos)
        require(engine.hex(g)['interact']!=0,'Chess tile is no longer interactive')
        require(kind==1,'Ocean secondary interactions need the ship state machine')
        engine.queue(engine.actions(pos),pos)
    if not state.get('pending'):engine.globals()
    engine.finish();return dict(result=0),engine.frames


def quit(db,uid):
    state=read(db,uid)
    if state:
        db.execute('UPDATE warchess_map SET current_chapter=0 WHERE uid=? AND current_chapter=?',(uid,state['chapter']))
    else:db.execute('UPDATE warchess_map SET current_chapter=0 WHERE uid=?',(uid,))
    save(db,uid,None)


def fire(ctx,uid):
    state=active(ctx.db,uid);require(1 in LEVELS[state['chapter']]['extra_gameplay'],'This map has no cannon gameplay')
    # Consuming ammo without resolving ray-hit events corrupts reload state.
    raise OperationError(2,'Cannon ray-hit state needs its dedicated implementation')


def switch(ctx,uid,kind):
    state=active(ctx.db,uid);require(2 in LEVELS[state['chapter']]['extra_gameplay'],'This map has no ship gameplay')
    raise OperationError(2,'Ship control state needs its dedicated implementation')


def prepare_battle(db,uid,stage_id,extant):
    state=active(db,uid);p=state.get('pending');require(p and p['kind']=='battle','No authorized chess battle')
    require(type(stage_id) is int and stage_id==p['stage_id'],'Wrong chess battle stage')
    expected=p['pos'][1]*CFG['maps'][str(state['chapter'])]['width']+p['pos'][0]+1
    require(type(extant) is int and extant==expected,'Wrong chess battle tile')
    return dict(uid=int(uid),run=state['run'],chapter=state['chapter'],token=p['token'],stage_id=stage_id,extant=extant)


def bind_battle(db,uid,context,battle_id):
    state=active(db,uid);p=state.get('pending')
    require(context.get('uid')==uid and context.get('run')==state['run'] and p and p.get('token')==context.get('token'),'Stale chess battle entry')
    p['battle_id']=str(battle_id);state['last_receipt']=None;save(db,uid,state);context['battle_id']=str(battle_id)


def settle_battle(ctx,uid,context,success,hp_list=None):
    state=active(ctx.db,uid);p=state.get('pending')
    require(context.get('uid')==uid and context.get('run')==state['run'] and p and p['kind']=='battle' and p.get('token')==context.get('token')
            and p.get('battle_id')==str(context.get('battle_id')),'Stale or already consumed chess result')
    engine=Engine(ctx.db,uid,state,ctx)
    if hp_list is not None:
        require(isinstance(hp_list,list) and all(isinstance(h,dict) and _int(h.get('id')) and type(h.get('hp')) is int and 0<=h['hp']<=10000 for h in hp_list),'Invalid chess hero HP')
        require(len({h['id'] for h in hp_list})==len(hp_list),'Duplicate chess hero HP')
        known={h['id']:h for h in state['map']['hp_list']};known.update({h['id']:deepcopy(h) for h in hp_list});state['map']['hp_list']=list(known.values())
        if hp_list:engine.frames.append((49013,dict(hp_list=hp_list)))
    if success:
        state['pending']=None;pos=tuple(p['pos']);engine.mark(10201,pos,p['tag'])
        engine.queue(p['rest'],pos,p['tag']);engine.globals()
    else:
        # The failed battle does not clear the tile. A new entry gets a new
        # identity, so a delayed old result cannot consume the retry.
        p['battle_id']=None;p['token']=uuid.uuid4().hex
    state['last_receipt']=deepcopy(context)
    engine.finish();return engine.frames


def frames(ctx,uid,events,ack=None):
    from middleware import DownFrame
    from operations import _refresh_frames
    out=_refresh_frames(ctx,uid,strict=True) if getattr(ctx,'touched_items',None) else []
    for cmd,data in events+([ack] if ack else []):
        payload=ctx.codec_encode('sc_'+str(cmd),data)
        require(isinstance(payload,(bytes,bytearray,memoryview)) and bool(payload),f'Missing required chess frame {cmd}')
        out.append(DownFrame(cmd,bytes(payload)))
    return out
