"""Experimental fresh-save onboarding and configuration-backed progression."""
import json
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

PROFILE = 'new_player_v1'


@lru_cache(maxsize=1)
def config():
    return json.loads(Path(__file__).with_name('new_player_cfg.json').read_text('utf-8'))


def enabled(db, uid):
    if db is None or not uid:
        return False
    try:
        rows = db.query("SELECT key,value FROM server_global_config WHERE key IN ('save.profile','save.owner_uid')")
    except sqlite3.OperationalError as exc:
        if 'no such table: server_global_config' in str(exc):
            return False
        raise
    values = {row['key']:row['value'] for row in rows}
    return values.get('save.profile') == PROFILE and str(values.get('save.owner_uid')) == str(uid)


def game_day(timestamp):
    return (int(timestamp) + 3*3600)//86400  # UTC+8, 05:00 refresh.


def gateway_new_player(db,uid):
    if not enabled(db,uid):
        return False
    if db.get_system_config('save.generation_version') == '1':
        # Feature guides and chapter progress must not decide the 501 switch.
        return not db.query("SELECT 1 FROM guide WHERE uid=? AND guide_id=501 AND COALESCE(kind,'base')='base'", (uid,))
    return not db.query('SELECT 1 FROM guide WHERE uid=? LIMIT 1',(uid,)) and not db.query(
        'SELECT 1 FROM chapter WHERE uid=? AND clear_times>0 LIMIT 1',(uid,))


def noob_state(db, uid, now=None):
    now = int(time.time() if now is None else now)
    rows = db.query('SELECT * FROM newbie_activity WHERE uid=?', (uid,))
    if not rows:
        raise ValueError('新手实验档缺少新手活动初始状态')
    row = dict(rows[0])
    trigger = int(row.get('trigger_time') or 0)
    phases = [int(t['phase'] or 1) for t in config()['tasks'].values()
              if t['type']==config()['noob']['noob_task_type']]
    row['max_phase'] = min(max(phases),max(1,game_day(now)-game_day(trigger)+1)) if trigger else 1
    return row


def require_noob_active(db, uid, now=None):
    now = int(time.time() if now is None else now)
    row = noob_state(db,uid,now)
    if not int(row.get('trigger_time') or 0) or int(row.get('completed_time') or 0) <= now:
        raise ValueError('新手任务活动尚未开启或已结束')
    return row


def close_noob_if_complete(db, uid, now=None):
    """Only generated saves opt in; no paid/periodic tasks join this policy."""
    if db.get_system_config('save.noob_policy') != 'completion':
        return False
    now = int(time.time() if now is None else now)
    cfg = config()
    row = db.get('newbie_activity', uid) or {}
    if not row.get('trigger_time') or int(row.get('completed_time') or 0) <= now:
        return False
    if int(row.get('now_sign_times') or 0) < len(cfg['noob']['noob_sign']):
        return False
    kinds = {cfg['noob']['noob_task_type'], *cfg['noob']['noob_advance_task_type']}
    required = {int(tid) for tid, task in cfg['tasks'].items() if task['type'] in kinds}
    claimed = {int(r['task_id']) for r in db.query(
        'SELECT task_id FROM task WHERE uid=? AND (claimed_ts>0 OR complete_flag=1)', (uid,))}
    if not required <= claimed:
        return False
    levels = {int(row[0]) for row in cfg['settings']['levelup_reward']}
    if not levels <= {int(r['level']) for r in db.query('SELECT level FROM newbie_level_reward WHERE uid=?', (uid,))}:
        return False
    points = set(range(1, len(cfg['noob']['noob_task_progress_reward']) + 1))
    if not points <= {int(r['pt_id']) for r in db.query('SELECT pt_id FROM newbie_pt_reward WHERE uid=?', (uid,))}:
        return False
    db.execute('UPDATE newbie_activity SET completed_time=?,update_ts=? WHERE uid=?', (now - 1, now, uid))
    return True


def ensure_tasks(db, uid, now=None, *, res_version=None):
    """补缺而不重置已有任务；只有当前阶段和已满足等级/前阶资格的任务。"""
    from activity_content_service import sync_catalogs, ensure_tasks as ensure_activity_tasks
    from generator import get_activity_list
    sync_catalogs(db, res_version)
    cfg = config(); row = noob_state(db,uid,now)
    level = int(db.get('users',uid)['level'])
    cleared = {int(r['id']) for r in db.query('SELECT id FROM chapter WHERE uid=? AND clear_times>0', (uid,))} \
        if db.get_system_config('save.generation_version') == '1' else set()
    advanced = cfg['noob']['noob_advance_task_type']
    claimed = {int(r['task_id']) for r in db.query(
        'SELECT task_id FROM task WHERE uid=? AND (claimed_ts>0 OR complete_flag=1)',(uid,))}
    plot_phases={}
    for kind in (3,):
        phases=sorted({int(t['phase'] or 1) for t in cfg['tasks'].values() if t['type']==kind})
        selected=phases[0]
        for phase in phases[:-1]:
            phase_ids={int(k) for k,t in cfg['tasks'].items() if t['type']==kind and int(t['phase'] or 1)==phase}
            if not phase_ids<=claimed:
                break
            selected=phases[phases.index(phase)+1]
        plot_phases[kind]=selected
    eligible = set()
    for i,kind in enumerate(advanced):
        previous = [int(k) for k,t in cfg['tasks'].items() if i and t['type']==advanced[i-1]]
        claimed = {int(r['task_id']) for r in db.query(
            'SELECT task_id FROM task WHERE uid=? AND (claimed_ts>0 OR complete_flag=1)',(uid,))}
        if level >= cfg['noob']['noob_advance_task_open'][i] and (not i or set(previous)<=claimed):
            eligible.add(kind)

    # 每日委托链式解锁 (110001..110015): 仅保留已完成任务和当前最新的一个未完成任务
    daily_offers = sorted([int(k) for k, t in cfg['tasks'].items() if t['type'] == 10])
    claimed_offers = {int(r['task_id']) for r in db.query(
        "SELECT task_id FROM task WHERE uid=? AND task_type=10 AND (claimed_ts>0 OR complete_flag=1)", (uid,))}
    active_offers = set()
    for tid in daily_offers:
        active_offers.add(tid)
        if tid not in claimed_offers:
            break

    with db.transaction():
        # 清除已被废弃的 1.0 Alpha 残留任务 (task_type=4，彻底消除永久幽灵红点)
        db.execute("DELETE FROM task WHERE uid=? AND task_type=4", (uid,))

        for tid_str, task in cfg['tasks'].items():
            tid = int(tid_str)
            kind = task['type']
            phase = int(task['phase'] or 1)
            if kind == cfg['noob']['noob_task_type']:
                # 603 新人课程 7 天任务全量下发，由客户端 ActivityNoobData:GetUnlockPhase() 控制后续天数锁定
                pass
            elif kind in advanced:
                if kind not in eligible:
                    continue
            elif kind == 3:
                # 主线剧情任务按阶段推进
                if phase > plot_phases[kind]:
                    continue
            elif kind == 10:
                # 每日委托：仅开启已完成任务 + 当前最新一阶段任务
                if tid not in active_offers:
                    continue
            elif kind in (5, 6, 7, 8):
                # 周期任务（日常/周常/战令）：全量常驻
                pass
            elif kind not in (1, 2):
                continue

            if task['condition'] == 2:
                progress = min(level, int(task['need'] or 0))
            elif task['condition'] == 401 and isinstance(task['additional_parameter'], list) and cleared.intersection(task['additional_parameter']):
                progress = min(1, int(task['need'] or 1))
            elif tid in (6001, 71001):
                progress = 1
            else:
                progress = 0

            db.execute('''INSERT OR IGNORE INTO task
                (uid,task_id,task_type,progress,complete_flag,expired_ts,claimed_ts)
                VALUES(?,?,?,?,0,0,0)''', (uid, tid, kind, progress))
        ensure_activity_tasks(db, uid, get_activity_list(db, uid, res_version), res_version, now)
        close_noob_if_complete(db, uid, now)


def claim_noob(db, uid, command, data, grant, now=None):
    now = int(time.time() if now is None else now)
    if not db._in_txn():
        with db.transaction():
            return claim_noob(db,uid,command,data,grant,now=now)
    row = require_noob_active(db,uid,now)
    cfg = config()
    rewards = []
    if command == 59012:
        index = int(row.get('now_sign_times') or 0)
        if index >= len(cfg['noob']['noob_sign']) or (row.get('last_sign_ts') and game_day(row['last_sign_ts'])==game_day(now)):
            return []
        rewards = [cfg['sign_rewards'][str(cfg['noob']['noob_sign'][index])]]
        cursor = db.execute('''UPDATE newbie_activity SET now_sign_times=?,last_sign_ts=?,update_ts=?
                               WHERE uid=?''',(index+1,now,now,uid))
        if cursor.rowcount != 1:
            raise RuntimeError('新手签到状态未保存')
    elif command == 59004:
        level = data.get('level')
        choices = {entry[0]:entry[1:] for entry in cfg['settings']['levelup_reward']}
        if type(level) is not int or level not in choices:
            raise ValueError('未知的新手等级奖励档位')
        if int(db.get('users',uid)['level']) < level:
            raise ValueError('等级不足')
        if db.query('SELECT 1 FROM newbie_level_reward WHERE uid=? AND level=?',(uid,level)):
            return []
        rewards = choices[level]
        db.execute('INSERT INTO newbie_level_reward(uid,level,update_ts) VALUES(?,?,?)',(uid,level,now))
    elif command == 59014:
        index = data.get('id')
        choices = cfg['noob']['noob_task_progress_reward']
        if type(index) is not int or not 1 <= index <= len(choices):
            raise ValueError('未知的新手任务累计奖励档位')
        if db.query('SELECT 1 FROM newbie_pt_reward WHERE uid=? AND pt_id=?',(uid,index)):
            return []
        ids = {int(k) for k,t in cfg['tasks'].items() if t['type']==cfg['noob']['noob_task_type']}
        completed = {int(r['task_id']) for r in db.query(
            'SELECT task_id FROM task WHERE uid=? AND (claimed_ts>0 OR complete_flag=1)',(uid,))}
        if len(ids & completed) < choices[index-1][0]:
            raise ValueError('新手任务累计完成数不足')
        rewards = choices[index-1][1:]
        db.execute('INSERT INTO newbie_pt_reward(uid,pt_id,update_ts) VALUES(?,?,?)',(uid,index,now))
    else:
        raise ValueError('未知新手领取协议')
    for item_id, quantity in rewards:
        grant(item_id,quantity)
    close_noob_if_complete(db, uid, now)
    return [{'id':item_id,'num':quantity} for item_id,quantity in rewards]


def guide_payload(db, uid, weak=False):
    clause = "kind='weak'" if weak else "(kind IS NULL OR kind<>'weak')"
    return {'mod_guide_list':[int(r['guide_id']) for r in db.query(
        f'SELECT guide_id FROM guide WHERE uid=? AND {clause} ORDER BY guide_id',(uid,))]}


class _StageGraph:
    """Configuration-only indexes; never cache an account's mutable progress."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.stages = {int(sid): item for sid, item in cfg['stages'].items()}
        self.incoming = {}
        for sid, item in self.stages.items():
            for target in item['next']:
                self.incoming.setdefault(int(target), set()).add(sid)
        self.roots = set()
        self.following = {}
        for sid, item in self.stages.items():
            incoming = self.incoming.get(sid)
            if incoming:
                for previous in incoming:
                    self.following.setdefault(previous, set()).add(sid)
            else:
                if item['chapter_first']:
                    self.roots.add(sid)
                if item['previous']:
                    self.following.setdefault(int(item['previous']), set()).add(sid)
        self.tower_positions = {}
        self.tower_sections = {}
        for cid, chapter in cfg['chapters'].items():
            if chapter['type'] == 7:
                sections = tuple(map(int, chapter['stages']))
                self.tower_sections[int(cid)] = sections
                for index, sid in enumerate(sections):
                    self.tower_positions[sid] = (int(cid), index)


@lru_cache(maxsize=1)
def _stage_graph(config_identity):
    # The graph retains cfg, so its identity cannot be reused while cached.
    # A replaced/reloaded config produces a new index automatically.
    return _StageGraph(config())


class StageAvailabilitySnapshot:
    """One account read per operation; all graph decisions below are in memory."""
    def __init__(self, db, uid, *, chapter_rows=None, sub_rows=None):
        self.db = db
        self.uid = int(uid)
        self.graph = _stage_graph(id(config()))
        if chapter_rows is None:
            chapter_rows = db.query('SELECT id,clear_times FROM chapter WHERE uid=?', (uid,))
        if sub_rows is None:
            sub_rows = db.query('SELECT id,clear_times FROM stage_sub WHERE uid=?', (uid,))
        # Preserve the original UNION semantics, even when the two physical
        # tables disagree. The display projection still uses its own authority.
        self.completed = {int(row['id']) for row in (*chapter_rows, *sub_rows)
                          if int(row['clear_times'] or 0) > 0}
        self.level = int(db.get('users', uid)['level'])
        self.tower_frontiers = {}
        for row in db.query('SELECT area,stage FROM tower WHERE uid=?', (uid,)):
            area = int(row['area'])
            position = self.graph.tower_positions.get(int(row['stage']))
            if position and position[0] == area:
                self.tower_frontiers[area] = position[1]
                self.completed.update(self.graph.tower_sections[area][:position[1] + 1])

    def cleared(self, sid):
        sid = int(sid)
        item = self.graph.stages.get(sid)
        if item and item['stage_type'] == 7:
            position = self.graph.tower_positions.get(sid)
            return bool(position and self.tower_frontiers.get(position[0], -1) >= position[1])
        return sid in self.completed

    def available(self, stage_id):
        stage = self.graph.stages[stage_id]
        if self.cleared(stage_id):
            return stage['stage_type'] != 7
        if self.level < stage['level']:
            return False
        chapters = self.graph.cfg['chapters']
        if any(not self.cleared(chapters[str(cid)]['stages'][-1])
               for cid in stage['chapter_pre'] if str(cid) in chapters):
            return False
        if stage['pre'] and any(not self.cleared(sid) for sid in stage['pre']):
            return False
        incoming = self.graph.incoming.get(stage_id)
        if incoming:
            return any(self.cleared(sid) for sid in incoming)
        return stage['chapter_first'] or bool(stage['previous'] and self.cleared(stage['previous']))

    def available_stage_ids(self):
        # Only a cleared node can expand the frontier. Walking through merely
        # unlocked nodes would incorrectly unlock the whole chapter at once.
        candidates = set(self.graph.roots)
        candidates.update(self.completed & self.graph.stages.keys())
        for previous in self.completed:
            candidates.update(self.graph.following.get(previous, ()))
        return {sid for sid in candidates if self.available(sid)}


def stage_available(db, uid, stage_id, *, snapshot=None):
    stage_id = int(stage_id)
    cfg = config()
    if str(stage_id) not in cfg['stages']:
        if 3026001<=stage_id<=3026013:
            return (stage_id-3026000) in mythic_open(db,uid)
        if 3027001<=stage_id<=3027039:
            return ((stage_id-3027001)//3+1) in mythic_open(db,uid)
        for difficulty, item in cfg['mythic_final'].items():
            if any(stage_id in group for group in item['stages']):
                row=db.get('mythic_final_progress',uid) or {}
                return int(difficulty) in json.loads(row.get('can_choose_list') or '[1]')
        return True  # Other modes retain their own mode-specific contracts.
    state = snapshot if snapshot is not None else StageAvailabilitySnapshot(db, uid)
    if state.db is not db or state.uid != int(uid):
        raise ValueError('Stage snapshot belongs to another account or database')
    return state.available(stage_id)


def mythic_open(db,uid):
    cleared={int(r['partition_id']) for r in db.query('SELECT partition_id FROM mythic_progress_partition WHERE uid=?',(uid,))}
    opened={1}
    # New-save policy: clear the preceding configured group to open the next.
    for difficulty in sorted(map(int,config()['mythic'])):
        if difficulty==1:
            continue
        previous=config()['mythic'].get(str(difficulty-1))
        if previous and set(previous['partitions'])<=cleared:
            opened.add(difficulty)
    return sorted(opened)


def after_stage_clear(db, uid, stage_id, now=None):
    if not enabled(db,uid):
        return
    now = int(time.time() if now is None else now)
    cfg=config(); item=cfg['stages'].get(str(stage_id))
    if item and item['stage_type']==7:
        previous=db.query('SELECT stage FROM tower WHERE uid=? AND area=?',(uid,item['chapter_id']))
        sections=cfg['chapters'][str(item['chapter_id'])]['stages']
        if not previous or sections.index(stage_id)>sections.index(int(previous[0]['stage'])):
            db.upsert('tower',uid,dict(area=item['chapter_id'],stage=stage_id),keys=('uid','area'))
    if stage_id==cfg['settings']['newbie_unlock_level_id'][0]:
        if db.get_system_config('save.noob_policy') == 'completion':
            from save_generator import NOOB_END_TS
            end_ts = NOOB_END_TS
        else:
            end_ts = now+cfg['settings']['newbie_time_limit'][0]*86400
        db.execute('''UPDATE newbie_activity SET trigger_time=?,completed_time=?,max_phase=1,update_ts=?
                      WHERE uid=? AND COALESCE(trigger_time,0)=0''',
                   (now,end_ts,now,uid))
    ensure_tasks(db,uid,now)


OVERRIDE_COMMANDS = {11035,17027,12009,59011,12011,12111,28001,52021,44009,44019,44023,48003,55001,62011}


def payload(db, uid, cmd, *, res_version=None):
    if cmd == 17027:
        from sign_service import accumulate_payload
        return accumulate_payload(db, uid, initial_version=max(config()['accumulate_sign_versions']))
    if cmd == 11035:
        initial=config()['hero_trial_initial']
        activity_id=initial['activity_id']
        row=db.get('hero_trial',uid,'AND activity_id=?',(activity_id,)) or {}
        entries=json.loads(row.get('activity_info_json') or '[]')
        valid=set(initial['trial_ids'])
        if any(entry.get('id') not in valid or entry.get('challenge_state') not in (0,1,2) for entry in entries):
            raise ValueError('Invalid saved hero trial state')
        return dict(activity_id=activity_id,activity_info=entries)
    if cmd == 48003:
        from schema_default import default_obj
        data=default_obj('sc_48003')
        data['user_matrix']['game_state']=(db.get('matrix_user',uid) or {}).get('game_state') or 1
        return data
    if cmd == 55001:
        defaults=config()['abyss_initial']; row=db.get('abyss',uid) or {}
        snapshots=db.query('SELECT raw_json FROM abyss_progress WHERE uid=?',(uid,))
        data=json.loads(snapshots[0]['raw_json'] or '{}') if snapshots else {}
        data.update(is_back=bool(row.get('is_back')),history_max_layer=int(row.get('history_max_layer') or defaults['layer']),
            last_version_max_unlock_layer=int(row.get('last_version_max_unlock_layer') or defaults['layer']),
            layer_reset_time=int(row.get('layer_reset_time') or 0),stage_reset_time=int(row.get('stage_reset_time') or 0),
            refresh_timestamp=int(row.get('refresh_timestamp') or 0),activity_id=int(row.get('activity_id') or defaults['activity_id']),
            got_layer_reward_list=[r['reward_id'] for r in db.query('SELECT reward_id FROM abyss_reward WHERE uid=?',(uid,))])
        data.setdefault('layer_info_list',[])
        return data
    if cmd == 12009:
        return dict(user_level=int(db.get('users',uid)['level']))
    if cmd in (12011,12111):
        return guide_payload(db,uid,cmd==12111)
    if cmd == 59011:
        import recharge_service
        ensure_tasks(db,uid,res_version=res_version)
        row=noob_state(db,uid)
        return dict(completed_time=int(row['completed_time'] or 0),trigger_time=int(row['trigger_time'] or 0),
            max_phase=row['max_phase'],version_id=config()['noob_version'],
            newbie_sign=dict(now_sign_times=int(row['now_sign_times'] or 0),last_sign_timestamp=int(row['last_sign_ts'] or 0)),
            newbie_level_reward=dict(received_level_list=[r['level'] for r in db.query('SELECT level FROM newbie_level_reward WHERE uid=?',(uid,))]),
            got_pt_id_list=[r['pt_id'] for r in db.query('SELECT pt_id FROM newbie_pt_reward WHERE uid=?',(uid,))],
            newbie_recharge_reward=recharge_service.RechargeService.get_instance(db=db).build_59009_payload(db,uid)['newbie_recharge_reward'])
    if cmd == 28001:
        ensure_tasks(db,uid,res_version=res_version)
        rows=db.query('SELECT * FROM task WHERE uid=?',(uid,))
        def phase(kind):
            return max([int(config()['tasks'][str(r['task_id'])]['phase'] or 1) for r in rows
                        if str(r['task_id']) in config()['tasks'] and config()['tasks'][str(r['task_id'])]['type']==kind] or [1])
        return dict(assignment_list=[dict(id=r['task_id'],progress=int(r['progress'] or 0),complete_flag=int(r['complete_flag'] or 0),expired_timestamp=int(r['expired_ts'] or 0)) for r in rows],send_type=0,newbie_phase=phase(3),assignment_phase=phase(4))
    if cmd == 52021:
        rows=db.query('SELECT id_list FROM loading_set WHERE uid=?',(uid,))
        return dict(id_list=json.loads(rows[0]['id_list']) if rows else [])
    if cmd == 44009:
        rows=db.query('SELECT partition_id,star FROM mythic_progress_partition WHERE uid=?',(uid,))
        selected=(db.get('mythic_public',uid) or {}).get('current_difficulty') or 1
        stars=next((int(r['star'] or 0) for r in rows if r['partition_id']==selected),0)
        return dict(clear_partition_id_list=[r['partition_id'] for r in rows],main_partition_star_list=list(range(1,stars+1)),star_reward_provide_list=[r['reward_id'] for r in db.query('SELECT reward_id FROM mythic_progress_reward WHERE uid=?',(uid,))])
    if cmd == 44019:
        return dict(open_difficulty_list=mythic_open(db,uid),difficulty=(db.get('mythic_public',uid) or {}).get('current_difficulty') or 1,is_new_difficulty=False)
    if cmd == 44023:
        row=db.get('mythic_final_progress',uid) or {}
        return dict(difficulty_id_can_choose=json.loads(row.get('can_choose_list') or '[1]'),now_difficulty=row.get('now_difficulty') or 1,
                    receive_reward=json.loads(row.get('receive_reward_list') or '[]'),clear_list=json.loads(row.get('clear_list') or '[]'),is_new_difficulty=False,challenge_info=json.loads(row.get('challenge_info_json') or '[]'))
    if cmd == 62011:
        return dict(end_timestamps=0,return_vs_id=110,return_level=1,mul_times=1,
                    receive_sign_index=0,received_sign_list=[],left_day=0,find_time=0,
                    open_draw_sign=False,pic_sign=False)
    raise ValueError(f'Unsupported fresh-save override {cmd}')


from event_bus import bus, Events


@bus.subscribe(Events.PLAYER_LEVEL_UP)
@bus.subscribe(Events.DAILY_RESET_5AM)
def refresh_onboarding_tasks(ctx,uid,**kwargs):
    db=getattr(ctx,'db',None)
    if not enabled(db,uid):
        return
    before={r['task_id'] for r in db.query('SELECT task_id FROM task WHERE uid=?',(uid,))}
    old_row = db.query('SELECT max_phase FROM newbie_activity WHERE uid=?', (uid,))
    old_max_phase = int(old_row[0]['max_phase'] or 1) if old_row else 1
    ensure_tasks(db,uid)
    after={r['task_id'] for r in db.query('SELECT task_id FROM task WHERE uid=?',(uid,))}
    new_row = noob_state(db, uid)
    new_max_phase = int(new_row.get('max_phase') or 1)
    if new_max_phase != old_max_phase:
        db.execute('UPDATE newbie_activity SET max_phase=? WHERE uid=?', (new_max_phase, uid))
    if before==after and new_max_phase == old_max_phase:
        return
    from codec import encode
    from middleware import DownFrame
    for cmd in (28001,59011):
        frame=DownFrame(cmd,encode('sc_'+str(cmd),payload(db,uid,cmd)))
        if hasattr(ctx,'append_frame'):
            ctx.append_frame(frame)
        else:
            if not hasattr(ctx,'pending_frames'):
                ctx.pending_frames=[]
            ctx.pending_frames.append(frame)
