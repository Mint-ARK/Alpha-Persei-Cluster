"""Ordinary-save level award eligibility; preserve its existing custom reward."""
import time


def claim(db, uid, level, grant, *, now=None):
    now = int(time.time() if now is None else now)
    from new_player_service import config
    valid_levels = {entry[0] for entry in config()['settings']['levelup_reward']}
    if type(level) is not int or level not in valid_levels:
        raise ValueError('未知的新手等级奖励档位')
    rows = db.query('SELECT trigger_time,completed_time FROM newbie_activity WHERE uid=?', (uid,))
    if not rows:
        raise ValueError('新手活动未初始化')
    trigger = int(rows[0].get('trigger_time') or 0)
    completed = int(rows[0].get('completed_time') or 0)
    # Native IsFinishAllActivity treats completed_time=0 as not finished.
    if trigger <= 0 or trigger > now or completed < 0 or (completed and completed <= now):
        raise ValueError('新手活动未开启或已结束')
    user = db.get('users', uid)
    if not user or int(user.get('level') or 0) < level:
        raise ValueError('等级不足')
    if db.query('SELECT 1 FROM newbie_level_reward WHERE uid=? AND level=?', (uid, level)):
        # A success ACK would append the level again in the native UI callback.
        raise ValueError('新手等级奖励已领取')
    db.execute('INSERT INTO newbie_level_reward(uid,level,update_ts) VALUES(?,?,?)', (uid, level, now))
    # Only thresholds come from the native catalog. Ordinary-save1x200 is the
    # existing customized award; generated saves keep their separate native rewards.
    grant(1, 200)
    return [{'id': 1, 'num': 200}]
