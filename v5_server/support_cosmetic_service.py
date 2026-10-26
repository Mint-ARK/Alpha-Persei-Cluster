"""Versioned additions for saves that opted into support mail."""
import json
import time


def ensure_support_cosmetic_mails(db, uid):
    from save_generator import generation_config, NOOB_END_TS
    from mail_service import MailService
    cfg = generation_config()
    previous = int(db.get_system_config('save.support_mail_version', '0') or 0)
    version = int(cfg['support_mail_version'])
    if not previous or previous >= version:
        return []
    with db.transaction():
        if int(db.get_system_config('save.support_mail_version','0') or 0) >= version:
            return []
        # A save contains one owner. Refuse an accidental cross-account grant.
        owners = db.query('SELECT uid FROM users')
        if owners != [{'uid': uid}]:
            raise ValueError('装饰补给只适用于当前单账号存档主人')
        desired = {int(a['id']): int(a['num']) for m in cfg['support_mails'] if m['group']==5
                   for a in m['attachments']}
        owned = {int(r['item_id']) for r in db.query('SELECT item_id FROM player_card WHERE uid=? AND obtained=1', (uid,))}
        owned.update(int(r['id']) for r in db.query('SELECT id FROM material WHERE uid=? AND num>0', (uid,)))
        owned.update(int(r['emoji_id']) for r in db.query('SELECT emoji_id FROM chat_sticker WHERE uid=?', (uid,)))
        for mail in db.query('SELECT attachment_json FROM mail WHERE uid=? AND attach_flag=1 AND expire_time>?', (uid,int(time.time()))):
            for attachment in json.loads(mail['attachment_json'] or '[]'):
                if int(attachment.get('number', attachment.get('num',0)) or 0)>0:
                    owned.add(int(attachment['id']))
        entries = [{'id':iid,'num':num} for iid,num in sorted(desired.items()) if iid not in owned]
        mailer = MailService(db=db)
        ids = []
        for offset in range(0,len(entries),50):
            mid = mailer.send_mail(uid, f'纪念卡通包·全装饰补给（{offset//50+1}）',
                '亲爱的管理员：新增装饰补给已送达。卡池产出与三位角色的专属誓约头像框、气泡保留原获取方式。',
                attachments=entries[offset:offset+50], expire_time=NOOB_END_TS)
            if not mid:
                raise ValueError('装饰补给邮件创建失败')
            ids.append(mid)
        prior_ids = json.loads(db.get_system_config('save.support_mail_ids','[]') or '[]')
        db.set_system_config('save.support_mail_ids',json.dumps(prior_ids+ids))
        db.set_system_config('save.support_mail_version',str(version))
        return ids
