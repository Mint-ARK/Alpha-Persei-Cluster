"""Persist native first-purchase notice dismissals without granting or activation."""
import time

NOTICE_COLUMNS = {
    59016: 'mc_new_role',
    59018: 'mc_new_sign',
    59020: 'fr_new6',
    59022: 'fr_new18',
    59024: 'bp_new',
}


def dismiss(db, uid, command):
    column = NOTICE_COLUMNS[command]
    rows = db.query('SELECT ' + column + ' FROM newbie_activity WHERE uid=?', (uid,))
    # A missing row has no notice to dismiss; never create/activate an activity here.
    if not rows or not int(rows[0].get(column) or 0):
        return
    cursor = db.execute('UPDATE newbie_activity SET ' + column + '=0,update_ts=? WHERE uid=?',
                        (int(time.time()), uid))
    if cursor.rowcount != 1:
        raise RuntimeError('新手通知标记未保存')


def recovery_payload(ctx, uid):
    rows = ctx.db.query('SELECT * FROM newbie_activity WHERE uid=?', (uid,))
    if not rows:
        return None
    from recharge_service import RechargeService
    payload = ctx.codec_encode('sc_59009', RechargeService.project_59009_payload(rows[0]))
    if not isinstance(payload, (bytes, bytearray, memoryview)) or not payload:
        raise ValueError('新手通知错误恢复帧为空')
    return bytes(payload)
