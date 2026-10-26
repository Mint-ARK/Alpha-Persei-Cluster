"""Annual birthday mail, eligibility tracking, keepsakes and archive recovery."""
import json
import datetime
import time
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def config():
    return json.loads(Path(__file__).with_name('birthday_cfg.json').read_text('utf-8'))


def commemorative_items():
    return {row['commemorative_props_id'] for row in config()['episodes']}


def read_unlocked_stories(db, uid):
    ids = [row['story_id'] for row in config()['episodes']]
    placeholders = ','.join('?' for _ in ids)
    return {int(row['story_id']) for row in db.query(
        f'SELECT story_id FROM story_unlock WHERE uid=? AND unlock_flag=1 AND story_id IN ({placeholders})',
        (uid, *ids))}


def trigger_today_greeting(db, uid, today=None):
    """After commit, check the most recent birthday, including missed birthdays."""
    if getattr(db, '_in_txn', lambda: False)():
        raise RuntimeError('Birthday greeting must be scheduled after commit')
    today = today or datetime.date.today()
    if latest_eligible_birthday(db, uid, today) is None:
        return None
    from ai_translator import AITranslator
    return AITranslator.get_instance(db=db).trigger_login_greeting(uid, db=db, cur_date=today)


def record_birthday_setting(db, uid, today=None, infer_legacy=False):
    """The first known setting date is immutable, including later DOB edits."""
    from profile_rules import read_birthday
    if not read_birthday(db,uid)['month']:
        return
    today=today or datetime.date.today()
    start=today;basis='birthday_set' if not infer_legacy else 'first_seen'
    if infer_legacy:
        dates=[]
        for row in db.query('SELECT year,month,sign_list FROM sign WHERE uid=? AND activity_id=3',(uid,)):
            try:days=json.loads(row['sign_list'] or '[]')
            except (ValueError,TypeError):continue
            if not isinstance(days,list):continue
            for day in days:
                if type(day) is not int:continue
                try:signed=datetime.date(int(row['year']),int(row['month']),day)
                except (ValueError,TypeError):continue
                if signed<=today:dates.append(signed)
        if dates:start,basis=min(dates),'monthly_sign'
    db.execute("INSERT OR IGNORE INTO ops_common(uid,kind,item_id,value_json) VALUES(?,'birthday_calendar','profile',?)",
               (uid,json.dumps({'first_set_on':start.isoformat(),'basis':basis})))
    if not infer_legacy:
        row=db.query("SELECT value_json FROM ops_common WHERE uid=? AND kind='birthday_calendar' AND item_id='profile'",(uid,))[0]
        value=json.loads(row['value_json'])
        value['last_set_on']=today.isoformat()
        db.execute("UPDATE ops_common SET value_json=? WHERE uid=? AND kind='birthday_calendar' AND item_id='profile'",
                   (json.dumps(value),uid))


def latest_eligible_birthday(db, uid, today=None):
    """Latest birthday; explicit DOB setting also grants this year's past date."""
    from profile_rules import read_birthday
    today=today or datetime.date.today()
    birthday=read_birthday(db,uid)
    if not birthday['month']:
        return None
    rows=db.query("SELECT value_json FROM ops_common WHERE uid=? AND kind='birthday_calendar' AND item_id='profile'",(uid,))
    tracking=json.loads(rows[0]['value_json']) if rows else {}
    start=datetime.date.fromisoformat(tracking['first_set_on']) if tracking else today
    last_set=datetime.date.fromisoformat(tracking['last_set_on']) if tracking.get('last_set_on') else None
    user=db.query('SELECT register_ts FROM users WHERE uid=?',(uid,))
    if not user:
        return None
    registered=int(user[0]['register_ts'] or 0)
    if registered>0:
        start=max(start,datetime.date.fromtimestamp(registered))
    # At most eight years between Gregorian Feb 29 dates (century boundary).
    for year in range(today.year,max(0,today.year-9),-1):
        try:day=datetime.date(year,birthday['month'],birthday['day'])
        except ValueError:continue
        if day<=today:
            # First-time setup can occur after the selected birthday, even on a
            # freshly created save. This exception never reaches last year.
            if last_set is not None and day.year==today.year==last_set.year:
                return day
            return day if day>=start else None
    return None


def due_greeting_event(db, uid, today=None):
    """Dispatch-only catch-up event; the public calendar still describes today."""
    today=today or datetime.date.today()
    due=latest_eligible_birthday(db,uid,today)
    if due is None:return None
    return dict(type='user_birthday',name='管理员生日',bundle='user_birthday',
                desc=f'管理员生日{due.isoformat()}的祝福',birthday_year=due.year,
                birthday_date=due.isoformat(),delivery_date=today.isoformat(),is_catchup=due<today)


def ensure_official_mail(db, uid, today=None, ctx=None):
    """Stage gifts in one mail per birthday year; never add inventory here."""
    from mail_service import MailService
    from save_generator import NOOB_END_TS
    today=today or datetime.date.today()
    with db.transaction():
        record_birthday_setting(db,uid,today,infer_legacy=True)
        due=latest_eligible_birthday(db,uid,today)
        if due is None:
            return 0
        if db.query("SELECT 1 FROM ops_common WHERE uid=? AND kind='birthday_official_mail' AND item_id=?",(uid,str(due.year))):
            return 0
        cfg=config();letter=cfg['official_mail']
        owned={int(r['id']) for r in db.query('SELECT id FROM material WHERE uid=? AND num>0',(uid,))}
        for row in db.query('SELECT attachment_json FROM mail WHERE uid=? AND attach_flag=1 AND expire_time>?',(uid,int(time.time()))):
            owned.update(int(a['id']) for a in json.loads(row['attachment_json'] or '[]') if int(a.get('number',a.get('num',0)) or 0)>0)
        items=[{'id':r['commemorative_props_id'],'num':1} for r in cfg['episodes'] if r['commemorative_props_id'] not in owned]
        items.extend({'id':iid,'num':count} for iid,count in cfg['current_gifts'])
        mid=MailService(db).send_mail(uid,letter['title'],letter['desc'],sender=letter['sender'],
            template_id=letter['id'],attachments=items,expire_time=NOOB_END_TS)
        if not mid:raise RuntimeError('官方生日邮件创建失败')
        value=dict(mail_id=mid,birthday=due.isoformat(),sent_on=today.isoformat(),
                   story_ids=[r['story_id'] for r in cfg['episodes']],episode=cfg['current_id'])
        db.execute("INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,'birthday_official_mail',?,?)",
                   (uid,str(due.year),json.dumps(value)))
        if ctx is not None:
            from codec import encode
            from middleware import DownFrame
            from illustrated_listener import _push_frame
            encoder=getattr(ctx,'codec_encode',None) or encode
            payload=encoder('sc_30001',MailService(db).get_login_summary(uid))
            if not payload:raise ValueError('生日邮件摘要编码失败')
            _push_frame(ctx,DownFrame(30001,payload))
        return mid


def on_official_mail_claim(ctx, uid, mail_ids):
    """Claiming the official gift unlocks its replay archive and pictures."""
    ids=set(mail_ids);stories=set()
    for row in ctx.db.query("SELECT value_json FROM ops_common WHERE uid=? AND kind='birthday_official_mail'",(uid,)):
        value=json.loads(row['value_json'])
        if value.get('mail_id') in ids:stories.update(value['story_ids'])
    if stories:
        from illustrated_story_service import publish_story_unlock
        publish_story_unlock(ctx,uid,sorted(stories))


def complete_story(ctx, uid, story_id):
    cfg = config()
    current = next(row for row in cfg['episodes'] if row['id']==cfg['current_id'])
    if story_id != current['story_id']:
        return False
    db = ctx.db
    if not db._in_txn():
        raise RuntimeError('Birthday completion requires an account transaction')
    ensure_official_mail(db,uid,ctx=ctx)
    # Use the strict producer, rather than an exception-swallowing BUS listener.
    from illustrated_story_service import publish_story_unlock
    publish_story_unlock(ctx, uid, [row['story_id'] for row in cfg['episodes']])
    return True
