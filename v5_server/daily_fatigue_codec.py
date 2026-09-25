# -*- coding: utf-8 -*-
"""
daily_fatigue_codec.py — 每日定时免费体力补给系统 (11:00 & 18:00 各 +30 体力)
"""
import time


def get_today_zero_ts():
    """获取东八区今日 0 点时间戳"""
    now = int(time.time())
    return ((now + 28800) // 86400) * 86400 - 28800


def get_daily_fatigue_status(db, uid):
    """查询玩家今日 11 点与 18 点补给领取状态 (is_got_11, is_got_18)"""
    zero_ts = get_today_zero_ts()
    raw_conn = db._conn() if hasattr(db, "_conn") else db
    cur = raw_conn.cursor()
    cur.execute("SELECT key_id, claim_ts FROM claim_ledger WHERE uid=? AND kind='daily_fatigue';", (uid,))
    rows = {r[0]: r[1] for r in cur.fetchall()}
    
    is_got_11 = bool(rows.get(11, 0) >= zero_ts)
    is_got_18 = bool(rows.get(18, 0) >= zero_ts)
    return is_got_11, is_got_18


def claim_daily_fatigue(db, uid, fatigue_type):
    """领取补给并持久化写入 claim_ledger"""
    now = int(time.time())
    raw_conn = db._conn() if hasattr(db, "_conn") else db
    cur = raw_conn.cursor()
    cur.execute("""
        INSERT INTO claim_ledger (uid, kind, key_id, claim_ts)
        VALUES (?, 'daily_fatigue', ?, ?)
        ON CONFLICT(uid, kind, key_id) DO UPDATE SET claim_ts=excluded.claim_ts;
    """, (uid, fatigue_type, now))
    raw_conn.commit()


def build_sc_12045(db, uid, codec_encode=None):
    """构建 sc_12045 报文 (daily_fatigue_dessert_list)"""
    is_11, is_18 = get_daily_fatigue_status(db, uid)
    data = {
        "daily_fatigue_dessert_list": [
            {"type": 11, "is_got": is_11},
            {"type": 18, "is_got": is_18}
        ]
    }
    if codec_encode:
        return codec_encode("sc_12045", data)
    import codec
    return codec.encode("sc_12045", data)
