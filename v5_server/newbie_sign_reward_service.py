"""Ordinary-save newbie daily claims, bounded by native version sign length."""
import time
from lazy_timer import get_daily_5am_ts
from newbie_progress_reward_service import contract

# Preserve the existing ordinary-save customization. Version1 alone has a native
# fifteenth day; retain the old38x1 result there explicitly, never as overflow.
ORDINARY_REWARDS = {
    1: (38, 5), 2: (2, 20000), 3: (41002, 10), 4: (38, 5),
    5: (40504, 20), 6: (41301, 20), 7: (1, 500), 8: (38, 5),
    9: (2, 30000), 10: (41002, 15), 11: (38, 5), 12: (40504, 30),
    13: (41301, 30), 14: (1, 1000), 15: (38, 1),
}


def claim(db, uid, grant, *, now=None):
    now = int(time.time() if now is None else now)
    rows = db.query('SELECT * FROM newbie_activity WHERE uid=?', (uid,))
    if not rows:
        raise ValueError('新手活动未初始化')
    row = rows[0]
    trigger, completed = int(row.get('trigger_time') or 0), int(row.get('completed_time') or 0)
    if trigger <= 0 or trigger > now or completed < 0 or (completed and completed <= now):
        raise ValueError('新手活动未开启或已结束')
    version = contract()['versions'].get(str(int(row.get('version_id') or 3)))
    if not version:
        raise ValueError('未知的新手活动版本')
    received = int(row.get('now_sign_times') or 0)
    if not 0 <= received < len(version['sign_ids']):
        raise ValueError('新手签到已完成或进度无效')
    last = int(row.get('last_sign_ts') or 0)
    if last > 0 and get_daily_5am_ts(last) >= get_daily_5am_ts(now):
        # Reject same-day retry and clock rollback, never consume another day.
        raise ValueError('当前游戏日新手签到已领取')
    day = received + 1
    if day not in ORDINARY_REWARDS:
        raise ValueError('新手签到奖励目录缺失')
    cursor = db.execute('UPDATE newbie_activity SET now_sign_times=?,last_sign_ts=?,update_ts=? WHERE uid=?',
                        (day, now, now, uid))
    if cursor.rowcount != 1:
        raise RuntimeError('新手签到记录未保存')
    item, count = ORDINARY_REWARDS[day]
    grant(item, count)
    return [{'id': item, 'num': count}]
