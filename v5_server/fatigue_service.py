# -*- coding: utf-8 -*-
"""
fatigue_service.py — 体力/吨吨值自然恢复与时间戳精准对账引擎 (Fatigue Recovery Service)
严格对齐《深空之眼》官方机制：
- 恢复周期：360 秒（6 分钟）恢复 1 点体力 (GameSetting.fatigue_recovery = 6)
- 玩家体力上限：按等级计算 (80~100 级为 240 点)
- 时间戳对账：下发真实的 last_fatigue_recover_time，支持毫秒级秒级余数保留与倒计时平滑推进。
"""

import os
import time
import json
import logging

logger = logging.getLogger('fatigue_service')

FATIGUE_CURRENCY_ID = 4
RECOVER_INTERVAL_SEC = 360  # 6 分钟 = 360 秒


def get_max_fatigue_by_level(lv):
    """根据等级计算体力上限（严格对齐 GameLevelSetting.lua：1级为100，每级+2，71级以上封顶240）"""
    try:
        lv_int = int(lv or 1)
    except Exception:
        lv_int = 1
    if lv_int >= 71:
        return 240
    elif lv_int <= 1:
        return 100
    else:
        return min(240, 100 + (lv_int - 1) * 2)


def get_max_fatigue(db, uid):
    """根据玩家当前等级计算体力上限"""
    try:
        row = db.query("SELECT level FROM users WHERE uid=?", (uid,))
        lv = int(row[0]["level"]) if row and row[0].get("level") else 80
    except Exception:
        lv = 80
    return get_max_fatigue_by_level(lv)


# ==================== 在线连接管理与高精度主动定时器 ====================

import threading

_active_conns = set()
_conns_lock = threading.Lock()
_ticker_thread = None
_ticker_stop_event = threading.Event()


def register_active_conn(conn):
    """注册在线客户端连接（由登录洪流完成广播触发）"""
    if conn is None:
        return
    with _conns_lock:
        _active_conns.add(conn)
    uid = getattr(conn, 'uid', None)
    logger.info(f"[FatigueService] 玩家 UID={uid} 登录洪流下发完毕，已接入体力主动定时器")


def unregister_active_conn(conn):
    """注销在线客户端连接（TCP 断开时调用）"""
    if conn is None:
        return
    with _conns_lock:
        _active_conns.discard(conn)


def get_active_conns():
    with _conns_lock:
        return list(_active_conns)


def start_fatigue_ticker():
    """启动在线体力高精度主动定时器守护线程（全局单例，每 1.0 秒轮询一次）"""
    global _ticker_thread
    if _ticker_thread is not None and _ticker_thread.is_alive():
        return

    def _tick_loop():
        logger.info("[FatigueService] 体力主动定时器后台线程已就绪并启动轮询")
        while not _ticker_stop_event.is_set():
            time.sleep(1.0)
            try:
                conns = get_active_conns()
                for conn in conns:
                    uid = getattr(conn, "uid", None)
                    db = getattr(conn, "db", None)
                    codec_encode = getattr(conn, "codec_encode", None)
                    if uid and db and codec_encode:
                        check_and_push_fatigue(conn, uid, db, codec_encode)
            except Exception as e:
                logger.debug(f"[FatigueService Ticker Loop Exception] {e}")

    _ticker_thread = threading.Thread(target=_tick_loop, name="FatigueActiveTicker", daemon=True)
    _ticker_thread.start()


def settle_fatigue(db, uid, now_ts=None):
    """
    结算玩家自上次恢复时间点以来的自然回体：
    1. 若当前体力 >= 上限，则进入静止态，last_fatigue_recover_time 清零（置 0）；
    2. 若未满且时间戳 <= 0，初始化为当前时间（流逝时间记 0，防穿透）；
    3. 未满且时间戳有效时，按 360 秒整除结算回体点数，回满则置 0，未回满则平滑推进时间戳保留余数。
    """
    now_ts = int(time.time() if now_ts is None else now_ts)
    if db is None or not uid:
        return 240, 0

    try:
        rows = db.query("SELECT num, last_fatigue_recover_time, update_ts FROM currency WHERE uid=? AND id=4", (uid,))
        max_f = get_max_fatigue(db, uid)
        if not rows:
            db.execute(
                "INSERT OR REPLACE INTO currency (uid, id, num, currency_name, last_fatigue_recover_time, update_ts) VALUES (?, 4, ?, '吨吨值', 0, ?)",
                (uid, max_f, now_ts)
            )
            return max_f, 0

        cur_num = int(rows[0].get("num") or 0)
        last_recover_time = int(rows[0].get("last_fatigue_recover_time") or 0)

        # 1. 如果体力已满或溢出（>= max_f），不进行自然恢复，时间戳归零
        if cur_num >= max_f:
            if last_recover_time != 0:
                db.execute(
                    "UPDATE currency SET last_fatigue_recover_time=0, update_ts=? WHERE uid=? AND id=4",
                    (now_ts, uid)
                )
            return cur_num, 0

        # 2. 体力未满 (< max_f)：若时间戳 <= 0（刚从满体扣下或未激活），锚定当前时间启动倒计时，流逝秒数算 0
        if last_recover_time <= 0:
            db.execute(
                "UPDATE currency SET last_fatigue_recover_time=?, update_ts=? WHERE uid=? AND id=4",
                (now_ts, now_ts, uid)
            )
            return cur_num, now_ts

        # 3. 时钟回拨保护
        if last_recover_time > now_ts:
            last_recover_time = now_ts

        elapsed = max(0, now_ts - last_recover_time)
        recover_points = elapsed // RECOVER_INTERVAL_SEC

        if recover_points > 0:
            actual_add = min(recover_points, max_f - cur_num)
            new_num = cur_num + actual_add
            if new_num >= max_f:
                new_last_time = 0
            else:
                new_last_time = last_recover_time + recover_points * RECOVER_INTERVAL_SEC

            db.execute(
                "UPDATE currency SET num=?, last_fatigue_recover_time=?, update_ts=? WHERE uid=? AND id=4",
                (new_num, new_last_time, now_ts, uid)
            )
            logger.info(f"[FatigueService] UID={uid} 体力自然恢复: {cur_num} -> {new_num} (恢复 {actual_add} 点, 上次时间={new_last_time})")
            return new_num, new_last_time
        else:
            return cur_num, last_recover_time

    except Exception as e:
        logger.error(f"[FatigueService ERROR] 结算体力恢复失败: {e}")
        return 240, 0


def on_fatigue_consume(db, uid, consumed_amount, now_ts=None):
    """当玩家消耗体力时调用：按四象限判定是否重设 last_fatigue_recover_time 倒计时起点"""
    now_ts = int(time.time() if now_ts is None else now_ts)
    if db is None or not uid:
        return

    try:
        max_f = get_max_fatigue(db, uid)
        rows = db.query("SELECT num, last_fatigue_recover_time FROM currency WHERE uid=? AND id=4", (uid,))
        if rows:
            cur_num = int(rows[0].get("num") or 0)
            last_time = int(rows[0].get("last_fatigue_recover_time") or 0)
            new_num = max(0, cur_num - consumed_amount)

            if new_num >= max_f:
                # 扣除后依然 >= 上限：维持静止态置 0
                new_last_time = 0
            elif cur_num >= max_f or last_time <= 0:
                # 扣除前 >= 上限跌入 < 上限，或者原本未激活：激活倒计时起点置为当前消耗时刻
                new_last_time = now_ts
            else:
                # 扣除前已 < 上限且保持 < 上限：保持原时间戳，不吞剩余秒数
                new_last_time = last_time

            db.execute(
                "UPDATE currency SET num=?, last_fatigue_recover_time=?, update_ts=? WHERE uid=? AND id=4",
                (new_num, new_last_time, now_ts, uid)
            )
    except Exception as e:
        logger.error(f"[FatigueService ERROR] 消耗体力更新失败: {e}")
        try:
            db.execute("UPDATE currency SET num = max(0, num - ?) WHERE uid=? AND id=4", (consumed_amount, uid))
        except Exception:
            pass


def on_fatigue_grant(db, uid, add_amount, now_ts=None):
    """当玩家获得体力时调用（升级赠送、体力药使用、移转之辉兑换）：允许溢出，达到上限则时间戳清零"""
    now_ts = int(time.time() if now_ts is None else now_ts)
    if db is None or not uid or add_amount <= 0:
        return 0, 0

    try:
        max_f = get_max_fatigue(db, uid)
        rows = db.query("SELECT num, last_fatigue_recover_time FROM currency WHERE uid=? AND id=4", (uid,))
        if rows:
            cur_num = int(rows[0].get("num") or 0)
            last_time = int(rows[0].get("last_fatigue_recover_time") or 0)
        else:
            cur_num = 0
            last_time = 0

        new_num = cur_num + add_amount
        if new_num >= max_f:
            # 获得体力后达到或超出上限：时间戳清零进入静止态
            new_last_time = 0
        else:
            # 获得体力后依然不足上限：保留原计时起点（若原本为0则赋予当前时刻）
            new_last_time = last_time if last_time > 0 else now_ts

        db.execute(
            "INSERT INTO currency (uid, id, num, currency_name, last_fatigue_recover_time, update_ts) VALUES (?, 4, ?, '吨吨值', ?, ?) "
            "ON CONFLICT(uid, id) DO UPDATE SET num=excluded.num, last_fatigue_recover_time=excluded.last_fatigue_recover_time, update_ts=excluded.update_ts",
            (uid, new_num, new_last_time, now_ts)
        )
        return new_num, new_last_time
    except Exception as e:
        logger.error(f"[FatigueService ERROR] 获得体力更新失败: {e}")
        try:
            db.execute(
                "INSERT INTO currency (uid, id, num) VALUES (?, 4, ?) "
                "ON CONFLICT(uid, id) DO UPDATE SET num=num+excluded.num",
                (uid, add_amount)
            )
        except Exception:
            pass
        return 0, 0


def check_and_push_fatigue(conn, uid, db, codec_encode):
    """
    在线挂机主动推送器（仅当体力未满且达到 360 秒周期时触发）：
    1. 快速检查体力是否未满（若满则 0 耗时瞬间返回）；
    2. 若未满且时间已过 360 秒，触发 settle_fatigue 自然回体；
    3. 采用专属原子差量帧：sc_17023 (数值) + sc_17025 (时间戳)，彻底废除 15009 全量包！
    """
    if not conn or not uid or not db or not codec_encode:
        return False

    try:
        rows = db.query("SELECT num, last_fatigue_recover_time FROM currency WHERE uid=? AND id=4", (uid,))
        if not rows:
            return False

        cur_num = int(rows[0].get("num") or 0)
        last_recover_time = int(rows[0].get("last_fatigue_recover_time") or 0)
        max_f = get_max_fatigue(db, uid)

        if cur_num >= max_f:
            return False

        now_ts = int(time.time())
        if last_recover_time <= 0:
            settle_fatigue(db, uid, now_ts=now_ts)
            return False
        if now_ts - last_recover_time < RECOVER_INTERVAL_SEC:
            return False

        # 达到 360 秒，结算并生成专属原子差量推送
        new_num, new_last_time = settle_fatigue(db, uid, now_ts=now_ts)
        if new_num > cur_num and hasattr(conn, "send_push"):
            # 1. 数值专属原子差量帧 sc_17023
            p_17023 = codec_encode("sc_17023", {"normal_items": [{"id": 4, "num": new_num}]})
            if p_17023:
                conn.send_push(17023, p_17023)

            # 2. 时间戳专属原子帧 sc_17025
            p_17025 = codec_encode("sc_17025", {"last_fatigue_recover_time": new_last_time})
            if p_17025:
                conn.send_push(17025, p_17025)

            logger.info(f"[FatigueService] 主动推送原子差量帧: UID={uid} 体力恢复至 {new_num}/{max_f}, last_ts={new_last_time}")
            return True
    except Exception as e:
        logger.error(f"[FatigueService ERROR] check_and_push_fatigue 失败: {e}")

    return False


def set_fatigue(uid, amount, db=None, push=True):
    """【GM与系统专用】精准设定玩家当前体力点数，同步对齐 last_fatigue_recover_time 并尝试广播。"""
    from account_db import get_db
    db = db or get_db()
    if db is None or not uid:
        raise ValueError("数据库连接或 UID 无效")

    now_ts = int(time.time())
    amount = max(0, int(amount))
    max_f = get_max_fatigue(db, uid)

    # 若设置后 >= 上限，时间戳归零；若设置后 < 上限，时间戳初始化为当前时刻启动倒计时
    last_time = 0 if amount >= max_f else now_ts

    db.execute(
        "INSERT INTO currency (uid, id, num, currency_name, last_fatigue_recover_time, update_ts) VALUES (?, 4, ?, '吨吨值', ?, ?) "
        "ON CONFLICT(uid, id) DO UPDATE SET num=excluded.num, last_fatigue_recover_time=excluded.last_fatigue_recover_time, update_ts=excluded.update_ts",
        (uid, amount, last_time, now_ts)
    )
    logger.info(f"[FatigueService] GM 直接设定体力: UID={uid} -> {amount} 点 (上限={max_f}, last_ts={last_time})")

    # 如果有在线连接，尝试主动推送原子差量帧
    if push:
        try:
            with _conns_lock:
                for conn in list(_active_conns):
                    if getattr(conn, "uid", None) == uid and hasattr(conn, "send_push") and hasattr(conn, "codec_encode"):
                        p_17023 = conn.codec_encode("sc_17023", {"normal_items": [{"id": 4, "num": amount}]})
                        if p_17023:
                            conn.send_push(17023, p_17023)
                        p_17025 = conn.codec_encode("sc_17025", {"last_fatigue_recover_time": last_time})
                        if p_17025:
                            conn.send_push(17025, p_17025)
        except Exception as e:
            logger.error(f"[FatigueService] 推送 GM 体力变更差量帧失败: {e}")

    return amount, last_time

