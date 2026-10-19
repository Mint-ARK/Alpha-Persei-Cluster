# -*- coding: utf-8 -*-
"""
daily_fatigue_codec.py — 每日定时免费体力补给状态与领取台账。

11:00 / 18:00 的客户端资格以东八区服务器时间为准。当前服务端每档
仍发放 60 点；这里不改客户端 Lua 中的历史显示配置，只负责把当前
``daily_fatigue``事实和旧 ``claim_ledger`` 读数合并成同一份状态。
"""
import time


UTC8_OFFSET_SECONDS = 8 * 60 * 60
DAILY_FATIGUE_TYPES = (11, 18)
DAILY_FATIGUE_HOURS = {11: 11, 18: 18}
DAILY_FATIGUE_KIND = "daily_fatigue"


def _now_ts(now_ts=None):
    return int(time.time() if now_ts is None else now_ts)


def get_today_zero_ts(now_ts=None):
    """获取东八区今日 0 点时间戳。"""
    now = _now_ts(now_ts)
    return ((now + UTC8_OFFSET_SECONDS) // 86400) * 86400 - UTC8_OFFSET_SECONDS


def get_today_date_str(now_ts=None):
    """返回东八区业务日字符串，避免依赖宿主机本地时区。"""
    now = _now_ts(now_ts)
    return time.strftime("%Y-%m-%d", time.gmtime(now + UTC8_OFFSET_SECONDS))


def is_claim_time_open(fatigue_type, now_ts=None):
    """按客户端 DailyFatigueData:GetStatus 的严格 ``server_time > hour`` 规则判断。"""
    try:
        fatigue_type = int(fatigue_type)
    except (TypeError, ValueError):
        return False
    hour = DAILY_FATIGUE_HOURS.get(fatigue_type)
    if hour is None:
        return False
    now = _now_ts(now_ts)
    return now > get_today_zero_ts(now) + hour * 3600


def _query_rows(db, sql, args=()):
    """Read through AccountDB without bypassing its transaction guard."""
    if hasattr(db, "query"):
        return db.query(sql, args)
    conn = db._conn() if hasattr(db, "_conn") else db
    return conn.execute(sql, args).fetchall()


def _row_value(row, name, index):
    if hasattr(row, "get"):
        return row.get(name)
    return row[index]


def get_claimed_types(db, uid, now_ts=None):
    """合并当前 ``daily_fatigue`` 与兼容用旧 ``claim_ledger`` 的当日状态。"""
    today = get_today_date_str(now_ts)
    zero_ts = get_today_zero_ts(now_ts)
    claimed = set()

    for row in _query_rows(db,
            "SELECT type FROM daily_fatigue WHERE uid=? AND date_str=?",
            (uid, today)):
        try:
            item_type = int(_row_value(row, "type", 0))
        except (TypeError, ValueError):
            continue
        if item_type in DAILY_FATIGUE_TYPES:
            claimed.add(item_type)

    for row in _query_rows(db,
            "SELECT key_id, claim_ts FROM claim_ledger "
            "WHERE uid=? AND kind=? AND claim_ts>=? AND claim_ts<?",
            (uid, DAILY_FATIGUE_KIND, zero_ts, zero_ts + 86400)):
        try:
            item_type = int(_row_value(row, "key_id", 0))
            claim_ts = int(_row_value(row, "claim_ts", 1) or 0)
        except (TypeError, ValueError):
            continue
        if item_type in DAILY_FATIGUE_TYPES and zero_ts <= claim_ts < zero_ts + 86400:
            claimed.add(item_type)
    return claimed


def get_daily_fatigue_status(db, uid, now_ts=None):
    """查询玩家今日 11 点与 18 点补给领取状态 (is_got_11, is_got_18)。"""
    claimed = get_claimed_types(db, uid, now_ts=now_ts)
    return 11 in claimed, 18 in claimed


def get_daily_fatigue_state(db, uid, fatigue_type, now_ts=None):
    """返回单档的统一状态，供 Operation 校验和边界测试复用。"""
    try:
        fatigue_type = int(fatigue_type)
    except (TypeError, ValueError):
        return {"type": None, "claimed": False, "time_open": False}
    return {
        "type": fatigue_type,
        "claimed": fatigue_type in get_claimed_types(db, uid, now_ts=now_ts),
        "time_open": is_claim_time_open(fatigue_type, now_ts=now_ts),
    }


def _claim_without_commit(db, uid, fatigue_type, now_ts):
    """在调用方事务中登记一档；返回 True=本次新登记，False=已登记。"""
    today = get_today_date_str(now_ts)
    zero_ts = get_today_zero_ts(now_ts)
    cur = db.execute(
        "INSERT INTO daily_fatigue (uid, type, date_str, update_ts) "
        "SELECT ?, ?, ?, ? "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM daily_fatigue WHERE uid=? AND type=? AND date_str=?"
        ") AND NOT EXISTS ("
        "  SELECT 1 FROM claim_ledger "
        "  WHERE uid=? AND kind=? AND key_id=? AND claim_ts>=? AND claim_ts<?"
        ")",
        (uid, fatigue_type, today, now_ts,
         uid, fatigue_type, today,
         uid, DAILY_FATIGUE_KIND, fatigue_type, zero_ts, zero_ts + 86400),
    )
    if cur.rowcount != 1:
        return False

    # daily_fatigue 是当前周期事实；claim_ledger 只作为旧数据的读取兼容
    # 源，不能再把新的周期领取写入永久台账。
    return True


def claim_daily_fatigue(db, uid, fatigue_type, now_ts=None):
    """在当前数据库事务中登记一档，禁止 raw connection 提前提交。"""
    try:
        fatigue_type = int(fatigue_type)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid daily fatigue type") from exc
    if fatigue_type not in DAILY_FATIGUE_TYPES:
        raise ValueError("invalid daily fatigue type")
    now = _now_ts(now_ts)
    tx = getattr(db, "transaction", None)
    if tx is None:
        return _claim_without_commit(db, uid, fatigue_type, now)
    with tx():
        return _claim_without_commit(db, uid, fatigue_type, now)


def build_sc_12045(db, uid, codec_encode=None, now_ts=None):
    """构建 sc_12045 报文 (daily_fatigue_dessert_list)。"""
    is_11, is_18 = get_daily_fatigue_status(db, uid, now_ts=now_ts)
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
