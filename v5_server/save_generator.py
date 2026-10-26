"""One-account save generation shared by CLI and future launch/control panels."""
from collections import Counter
from contextlib import contextmanager, closing
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

SERVER = Path(__file__).resolve().parent
SLOTS = SERVER
NOOB_END_TS = 2461420799  # Protocol deadline; completion policy closes it earlier.


@lru_cache(maxsize=1)
def generation_config():
    return json.loads((SERVER / 'data/save_generation_cfg.json').read_text('utf-8'))


def validate_uid(value):
    raw = str(value)
    if not re.fullmatch(r'[1-9][0-9]{0,14}', raw, flags=re.ASCII):
        raise ValueError('UID须为1—15位正整数，不能有前导零')
    return int(raw)


def save_filename(name):
    if not isinstance(name, str) or not name or len(name) > 64:
        raise ValueError('存档名称须为1—64个字符')
    if name != name.strip() or name.endswith('.') or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name):
        raise ValueError('存档名称不能包含路径、系统保留符号或首尾空白')
    stem = name[:-3] if name.lower().endswith('.db') else name
    if not stem or stem in ('.', '..') or stem.endswith(('.', ' ')):
        raise ValueError('存档名称无效')
    if stem.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('不能使用系统保留名称')
    return stem + '.db'


@dataclass(frozen=True)
class SaveOptions:
    uid: str | int | None = None
    nick: str | None = None
    level: int = 1
    skip_intro: bool = False
    skip_feature_guides: bool = False
    clear_through: int | None = None  # None=no preclear; 0=prologue; 1..5=chapter.
    noob_tasks_ready: bool = False
    support_mail: bool = True

    def validate(self):
        if self.uid is not None:
            validate_uid(self.uid)
        if type(self.level) is not int or not 1 <= self.level <= 100:
            raise ValueError('等级须为1—100的整数')
        if self.clear_through is not None and (type(self.clear_through) is not int or not 0 <= self.clear_through <= 5):
            raise ValueError('预通关范围须为序章0至第五章5，或不预通关')
        if self.clear_through is not None and not self.skip_intro:
            raise ValueError('预通关包含序章，客户端会跳过501；请同时开启跳过501剧情')
        for flag in ('skip_intro', 'skip_feature_guides', 'noob_tasks_ready', 'support_mail'):
            if type(getattr(self, flag)) is not bool:
                raise ValueError(flag + '须为布尔值')
        if self.nick is not None and not isinstance(self.nick, str):
            raise ValueError('昵称须为文字')
        if self.skip_intro and len((self.nick or '').strip()) > 16:
            raise ValueError('昵称不能超过16个字符')
        return self


def total_exp_for_level(level):
    SaveOptions(level=level).validate()
    curve = json.loads((SERVER / 'user_level_setting.json').read_text('utf-8-sig'))
    return sum(int(curve[str(i)]) for i in range(1, level))


def used_uids(directory=None):
    """Read markers/users only; never initialize or migrate a scanned save."""
    found = set()
    root = Path(directory) if directory is not None else SERVER
    paths = list(root.rglob('*.db')) if root.exists() else []
    if directory is None and (SERVER / 'account.db').exists():
        paths.append(SERVER / 'account.db')
    for path in paths:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                found.update(int(row[0]) for row in conn.execute('SELECT uid FROM users'))
    return found


def allocate_uid(occupied, now=None):
    moment = datetime.fromtimestamp(time.time() if now is None else now, timezone(timedelta(hours=8)))
    # Bounded same-second collision handling, at most the following 59 seconds.
    for offset in range(60):
        candidate = validate_uid((moment + timedelta(seconds=offset)).strftime('%y%m%d%H%M%S'))
        if candidate not in occupied:
            return candidate
    raise ValueError('当前时间附近的UID均已使用，请稍后重试或自定义UID')


def plan_save(options, *, uid=None):
    """Pure preview: resources and toggles without opening an account database."""
    options.validate()
    cfg = generation_config()
    chapters = [] if options.clear_through is None else deepcopy(cfg['first_clear_chapters'][:options.clear_through + 1])
    total = Counter()
    for chapter in chapters:
        for stage in chapter['stages']:
            total.update({iid: num for iid, num in stage['rewards']})
    if set(total) & {4, 12}:
        raise ValueError('首通补偿不能包含体力或玩家经验')
    return dict(options=asdict(options), uid=uid if uid is not None else options.uid,
                nick=((options.nick or '').strip() or '管理员') if options.skip_intro else '管理员',
                level=options.level, total_exp=total_exp_for_level(options.level),
                resource_version=cfg['resource_version'],
                chapters=chapters, cleared_stages_count=sum(len(row['stages']) for row in chapters),
                first_clear_rewards=[[iid, count] for iid, count in sorted(total.items())],
                support_mails=deepcopy(cfg['support_mails']) if options.support_mail else [])


def _copy_catalogs(db, master):
    path = Path(master).resolve(strict=True)
    db.execute('ATTACH DATABASE ? AS master_db', (path.as_uri() + '?mode=ro',))
    copied = []
    try:
        with db.transaction():
            tables = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            for record in tables:
                table = record['name']
                quoted = '"' + table.replace('"', '""') + '"'
                columns = [row['name'] for row in db.query(f'PRAGMA table_info({quoted})')]
                if 'uid' not in columns and table not in ('hero_cfg', 'server_global_config'):
                    db.execute(f'INSERT INTO {quoted} SELECT * FROM master_db.{quoted}')
                    copied.append(table)
            blueprints = json.loads((SERVER / 'data/hero_blueprint.json').read_text('utf-8'))
            for hid, info in blueprints.items():
                db.execute('INSERT INTO hero_cfg(hero_id,unlock_star,name,race) VALUES(?,?,?,?)',
                           (int(hid), info['unlock_star'], info['full_name'], info['race']))
            db.execute('''INSERT OR IGNORE INTO hero_cfg(hero_id,unlock_star,name,race)
                SELECT hero_id,unlock_star,name,race FROM master_db.hero_cfg
                WHERE hero_id NOT IN(1001,1036,2001,2002)''')
            donors = db.query('SELECT uid,COUNT(*) n FROM master_db.activity GROUP BY uid ORDER BY n DESC,uid LIMIT 1')
            if not donors:
                raise ValueError('母库缺少活动目录')
            donor = donors[0]['uid']
            catalogs = {table: db.query(f'SELECT * FROM master_db.{table} WHERE uid=?', (donor,))
                        for table in ('activity', 'achievement')}
            templates = db.query('SELECT DISTINCT template_id FROM master_db.rogueteam_user WHERE uid=?', (donor,))
    finally:
        db.execute('DETACH DATABASE master_db')
    return copied, catalogs, templates


def _seed_player(db, uid, plan, options, catalogs, templates, now):
    from new_player_service import config, PROFILE
    from fatigue_service import get_max_fatigue_by_level
    cfg = config()
    defaults = cfg['settings']
    hero = cfg['starter_hero']
    hid = hero['id']
    db.upsert('users', uid, dict(account='player_slot_1', server_id=1, nick=plan['nick'],
        level=options.level, exp=plan['total_exp'], extra='{}', hero_num=1,
        plot_progress=plan['chapters'][-1]['stages'][-1]['id'] if plan['chapters'] else 0,
        is_changed_nick=1 if options.skip_intro else 0, change_nick_times=0, sign='',
        portrait=defaults['profile_avatar_default'][0], icon_frame=defaults['profile_avatar_frame_default'][0],
        likes=0, ip_location='', register_ts=now, club_id=0, club_post=0))
    db.upsert('game_user', uid, dict(birth_month=0, birth_day=0,
        cur_portrait=defaults['profile_avatar_default'][0], cur_icon_frame=defaults['profile_avatar_frame_default'][0],
        cur_bubble=defaults['profile_chat_bubble_default'][0], cur_card_bg=defaults['profile_business_card_default'][0],
        cur_sticker_bg=defaults['sticker_background_default'][0], show_hero=json.dumps([hid]), board_hero=hid,
        cur_scene=defaults['home_sence_default'][0], cur_home_bg=defaults['home_sence_default'][1]))
    from hero_codec import initial_hero_row
    initial = initial_hero_row(hid, hero['star'], now)
    names = db.query('SELECT name FROM hero_cfg WHERE hero_id=?', (hid,))
    if names:
        initial['name'] = names[0]['name']
    db.upsert('hero', uid, initial, keys=('uid', 'id'))
    from peripheral_service import PeripheralService
    PeripheralService(db).unlock_acquisition_portrait(uid, hid)
    from profile_rules import default_home_scene_ids
    for scene_id in default_home_scene_ids():
        db.upsert('user_scene', uid, dict(scene_id=scene_id, lasted_time=0, obtain_time=now, update_ts=now),
                  keys=('uid', 'scene_id'))
    for cid in (1, 2, 4, 5, 12, 19, 30, 31, 32, 36, 38, 101):
        num = get_max_fatigue_by_level(options.level) if cid == 4 else plan['total_exp'] if cid == 12 else 0
        db.set_currency(uid, cid, num, ts=now)
    for kind, key in [('portrait', 'profile_avatar_default'), ('icon_frame', 'profile_avatar_frame_default'),
                      ('chat_bubble', 'profile_chat_bubble_default'), ('card_bg', 'profile_business_card_default'),
                      ('sticker_bg', 'sticker_background_default')]:
        db.upsert('player_card', uid, dict(kind=kind, item_id=defaults[key][0], obtained=1), keys=('uid', 'kind', 'item_id'))
    for row in catalogs['activity']:
        db.upsert('activity', uid, {**row, 'uid': uid}, keys=('uid', 'activity_id'))
    for row in catalogs['achievement']:
        db.upsert('achievement', uid, {**row, 'uid': uid, 'progress': 0, 'complete_flag': 0, 'achieve_time': 0}, keys=('uid', 'achievement_id'))
    for row in templates:
        db.upsert('rogueteam_user', uid, dict(template_id=row['template_id'], difficult=1, max_difficult=1,
            last_score_id=0, point=0, rewarded_list_json='[]', update_ts=now), keys=('uid', 'template_id'))
    db.upsert('mythic_public', uid, dict(current_difficulty=1, is_new_difficulty=0, update_ts=now))
    db.upsert('mythic_difficulty', uid, dict(difficulty_id=1, is_open=1, update_ts=now), keys=('uid', 'difficulty_id'))
    db.upsert('mythic_final_progress', uid, dict(now_difficulty=1, can_choose_list='[1]', receive_reward_list='[]',
        clear_list='[]', challenge_info_json='[]', is_new_difficulty=0, update_ts=now))
    db.upsert('matrix_user', uid, dict(game_state=1, terminal_json='{}', beacon_json='[]', point_reward_got='[]', update_ts=now))
    db.upsert('abyss', uid, dict(activity_id=cfg['abyss_initial']['activity_id'], is_back=0,
        history_max_layer=cfg['abyss_initial']['layer'], last_version_max_unlock_layer=cfg['abyss_initial']['layer'], update_ts=now), keys=('uid', 'activity_id'))
    db.upsert('autochess_meta', uid, dict(rank_score=0, energy=0, sunglasses=0, update_ts=now))
    db.upsert('accumulate_sign', uid, dict(version=max(cfg['accumulate_sign_versions']), open_sign=1, login_days=0, update_ts=now))
    active = bool(plan['chapters']) or options.noob_tasks_ready
    max_phase = max(t['phase'] for t in cfg['tasks'].values() if t['type'] == cfg['noob']['noob_task_type']) if options.noob_tasks_ready else 1
    trigger = now - (max_phase - 1) * 86400 if active else 0
    db.upsert('newbie_activity', uid, dict(completed_time=NOOB_END_TS if active else 0, trigger_time=trigger,
        max_phase=max_phase, version_id=cfg['noob_version'], now_sign_times=0, last_sign_ts=0, update_ts=now))
    for key, value in {'save.owner_uid': uid, 'save.profile': PROFILE, 'save.initialized_at': now,
                       'save.generation_version': 1, 'save.noob_policy': 'completion',
                       'save.options': json.dumps(asdict(options), ensure_ascii=False),
                       'save.resource_version': plan['resource_version']}.items():
        db.set_system_config(key, str(value))


def build_save_workspace(options, uid, *, master=None, now=None):
    """Return a fully populated AccountDB in memory. Caller must close it."""
    from account_db import AccountDB
    from inventory_service import InventoryService
    from mail_service import MailService
    from new_player_service import config, ensure_tasks
    options.validate()
    uid = validate_uid(uid)
    if options.uid is not None and validate_uid(options.uid) != uid:
        raise ValueError('构建UID与指定UID不一致')
    now = int(time.time() if now is None else now)
    if now >= NOOB_END_TS:
        raise ValueError('新档协议活动期限需要更新')
    cfg = generation_config()
    # Refuse stale audited first-clear data instead of granting wrong rewards.
    for filename in ('stage_meta.json', 'daily_stage_drop_cfg.json', 'drop_cfg.json'):
        if hashlib.sha256((SERVER / filename).read_bytes()).hexdigest() != cfg['source_sha256']['v5_server/' + filename]:
            raise ValueError('请重新导出存档生成配置：' + filename)
    plan = plan_save(options, uid=uid)
    db = AccountDB.new_save_workspace()
    try:
        copied, catalogs, templates = _copy_catalogs(db, master or SERVER / 'account.db')
        with db.transaction():
            _seed_player(db, uid, plan, options, catalogs, templates, now)
            guides = [row for row in cfg['guides'] if (row['id'] == 501 and row['kind'] == 'base' and options.skip_intro)
                      or (row['id'] != 501 and options.skip_feature_guides)]
            for row in guides:
                db.upsert('guide', uid, dict(guide_id=row['id'], kind=row['kind']), keys=('uid', 'guide_id'))
            meta = json.loads((SERVER / 'stage_meta.json').read_text('utf-8'))
            for chapter in plan['chapters']:
                for stage in chapter['stages']:
                    sid = stage['id']
                    info = meta['stages'][str(sid)]
                    db.upsert('chapter', uid, dict(id=sid, chapter_id=chapter['chapter_id'], name=info['name'],
                        category=info['category'], difficulty=1, max_star=info['max_star'], stamina_need=info['stamina_need'],
                        clear_times=1, star_list=json.dumps([1] * info['max_star'] + [0] * (3 - info['max_star'])),
                        first_clear_ts=now, update_ts=now), keys=('uid', 'id'))
                    if info.get('is_story'):
                        db.upsert('story_unlock', uid, dict(story_id=sid, name=info['name'], unlock_flag=1), keys=('uid', 'story_id'))
            InventoryService.grant_items(db, uid, plan['first_clear_rewards'], silent=True, source='save_first_clear')
            db.set_system_config('save.first_clear_rewards', json.dumps(plan['first_clear_rewards']))
            db.execute('UPDATE users SET hero_num=(SELECT COUNT(*) FROM hero WHERE uid=? AND unlock=1) WHERE uid=?', (uid, uid))
            # Native definitions replace captured/partially imported catalogues;
            # task and shop purchase progress is built separately for this owner.
            from activity_content_service import sync_catalogs
            sync_catalogs(db, plan['resource_version'])
            ensure_tasks(db, uid, now, res_version=plan['resource_version'])
            for tid in (50101001, 50101002, 50101003, 50101004):
                db.upsert('task', uid, dict(task_id=tid, name='挑战', task_type=3002, progress=0,
                    complete_flag=0, expired_ts=0, claimed_ts=0), keys=('uid', 'task_id'))
            if options.noob_tasks_ready:
                for tid, task in config()['tasks'].items():
                    if task['type'] == config()['noob']['noob_task_type']:
                        db.execute('UPDATE task SET progress=? WHERE uid=? AND task_id=?', (int(task['need']), uid, int(tid)))
            if any(stage['id'] == 1030107 for ch in plan['chapters'] for stage in ch['stages']):
                from res_version_manager import get_version_config
                from lazy_timer import get_weekly_mon_5am_ts
                db.upsert('battlepass', uid, dict(battlepass_list_id=int(get_version_config('321')['battlepass_list_id']),
                    pay_level=0, is_start=1, weekly_gain_exp=0, start_timestamp=now,
                    end_timestamp=NOOB_END_TS, next_refresh_timestamp=get_weekly_mon_5am_ts(now) + 7 * 86400,
                    receive_info='[]', update_ts=now))
            mailer = MailService(db=db)
            mail_ids = []
            for mail in plan['support_mails']:
                mid = mailer.send_mail(uid, mail['title'], mail['content'], attachments=mail['attachments'], expire_time=NOOB_END_TS)
                if not mid:
                    raise ValueError('助力邮件创建失败：' + mail['title'])
                mail_ids.append(mid)
            db.set_system_config('save.support_mail_ids', json.dumps(mail_ids))
            db.set_system_config('save.support_mail_version', str(cfg.get('support_mail_version', 1)) if mail_ids else '0')
        if db.query('SELECT uid FROM users') != [{'uid': uid}]:
            raise ValueError('新档出现非主人账号')
        plan.update(static_tables=copied, total_guides_passed=len(guides), mail_ids=mail_ids,
                    fatigue=db.get_currency_num(uid, 4), integrity=db.query('PRAGMA integrity_check')[0]['integrity_check'])
        return db, plan
    except BaseException:
        db.close()
        raise


@contextmanager
def _creation_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.save-generation.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def create_save(name, options=None, *, destination=None, master=None):
    """Named cold-start save; destination is retained for old developer tools only."""
    options = (options or SaveOptions()).validate()
    filename = save_filename(name)
    path = Path(destination).resolve() if destination is not None else SLOTS / filename
    with _creation_lock(SLOTS):
        if path.exists():
            raise FileExistsError('存档已存在，请换一个名称：' + path.name)
        occupied = used_uids()
        uid = validate_uid(options.uid) if options.uid is not None else allocate_uid(occupied)
        if uid in occupied:
            raise ValueError('此UID已被本地存档使用，请换一个UID')
        db, report = build_save_workspace(options, uid, master=master)
        try:
            backup = db.export_new_save(path)
        finally:
            db.close()
    report.update(status='success', path=str(path), backup=backup, size_mb=round(path.stat().st_size / 1024**2, 2))
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='按名称创建单账号新档；不覆盖已有文件。')
    parser.add_argument('--name', required=True, help='存档名称，支持中文；保存到服务端同级目录')
    parser.add_argument('--uid', help='1—15位正整数；省略时按年月日时分秒生成')
    parser.add_argument('--nick', help='跳过501时使用；留空为管理员；保留501时在游戏内取名')
    parser.add_argument('--level', type=int, default=1, help='初始等级1—100，经验自动计算')
    parser.add_argument('--skip-intro', action=argparse.BooleanOptionalAction, default=False, help='跳过首次501剧情引导')
    parser.add_argument('--skip-feature-guides', action=argparse.BooleanOptionalAction, default=False, help='跳过功能首次引导')
    parser.add_argument('--clear-through', type=int, choices=range(6), help='预通关至指定章节；0为序章，1—5为对应章；省略则不预通关')
    parser.add_argument('--noob-tasks-ready', action=argparse.BooleanOptionalAction, default=False, help='七天新人课程达标待领，签到仍需逐日领取')
    parser.add_argument('--support-mail', action=argparse.BooleanOptionalAction, default=True, help='创建助力邮件，装饰与表情每50项拆分一封')
    parser.add_argument('--preview', action='store_true', help='只预览，不创建数据库或分配UID')
    parser.add_argument('--report', type=Path, help='可选：写入一个新的完整JSON报告')
    args = parser.parse_args(argv)
    try:
        save_filename(args.name)
        options = SaveOptions(uid=args.uid, nick=args.nick, level=args.level, skip_intro=args.skip_intro,
            skip_feature_guides=args.skip_feature_guides, clear_through=args.clear_through,
            noob_tasks_ready=args.noob_tasks_ready, support_mail=args.support_mail).validate()
        if args.report and args.report.exists():
            raise FileExistsError('报告已存在，请换一个名称')
        report = plan_save(options) if args.preview else create_save(args.name, options)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open('x', encoding='utf-8') as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
        summary = {key: value for key, value in report.items() if key not in ('chapters', 'support_mails', 'static_tables')}
        summary['chapters'] = [{'id': ch['chapter_id'], 'label': ch['label'], 'stage_count': len(ch['stages'])} for ch in report['chapters']]
        summary['support_mails'] = [{'title': mail['title'], 'attachment_count': len(mail['attachments'])} for mail in report['support_mails']]
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
