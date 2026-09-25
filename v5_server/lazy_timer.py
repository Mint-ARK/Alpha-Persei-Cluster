# -*- coding: utf-8 -*-
"""
lazy_timer.py — V5 服务端全局惰性时间戳广播源（Lazy Timer Broadcaster）

核心机制：
1. 惰性脉冲驱动（Lazy Evaluation）：无后台死循环轮询，在玩家请求入站、心跳包与登录流触发；
2. 双轨时间建模：物理流逝时间（体力自然恢复、食堂烹饪、后宅疲劳恢复、周期重置）与会话活跃时间（在线时长）彻底解耦；
3. 时钟倒流与跳跃安全防护：下限钳位 max(0, delta_t)，上限截断，离散周期单次收敛；
4. 双层节流（Tiered Throttling）：纳秒级内存边界判定 + 5秒微防抖，杜绝高频请求时的数据库 I/O 竞争；
5. 即时落库（Write-Through）：所有状态与水位线即时持久化于 account.db，免疫 kill -9 与断电强杀。
"""

import time
import datetime
import logging
from event_bus import bus, Events

logger = logging.getLogger('lazy_timer')


# ==================== 标准时间周期计算工具函数 ====================

def get_daily_5am_ts(now_ts=None):
    """计算当前时间所处每日 05:00 周期的起始时间戳。"""
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    today_5am = dt.replace(hour=5, minute=0, second=0, microsecond=0)
    cycle_dt = today_5am if dt >= today_5am else (today_5am - datetime.timedelta(days=1))
    return int(cycle_dt.timestamp())


def get_weekly_mon_5am_ts(now_ts=None):
    """计算当前时间所处每周一 05:00 周期的起始时间戳（Monday=0）。"""
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    days_since_mon = dt.weekday()
    this_mon_5am = (dt - datetime.timedelta(days=days_since_mon)).replace(hour=5, minute=0, second=0, microsecond=0)
    cycle_dt = this_mon_5am if dt >= this_mon_5am else (this_mon_5am - datetime.timedelta(days=7))
    return int(cycle_dt.timestamp())


def get_weekly_thu_5am_ts(now_ts=None):
    """计算当前时间所处每周四 05:00 周期的起始时间戳（Thursday=3，梦境再构/Boss挑战轮换）。"""
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    days_since_thu = (dt.weekday() - 3) % 7
    this_thu_5am = (dt - datetime.timedelta(days=days_since_thu)).replace(hour=5, minute=0, second=0, microsecond=0)
    cycle_dt = this_thu_5am if dt >= this_thu_5am else (this_thu_5am - datetime.timedelta(days=7))
    return int(cycle_dt.timestamp())


def get_monthly_5am_ts(now_ts=None):
    """计算当前时间所处每月 1 日 05:00 周期的起始时间戳。"""
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    first_day_5am = dt.replace(day=1, hour=5, minute=0, second=0, microsecond=0)
    if dt >= first_day_5am:
        cycle_dt = first_day_5am
    else:
        # 上个月第一天 05:00
        prev_month_last_day = first_day_5am - datetime.timedelta(days=1)
        cycle_dt = prev_month_last_day.replace(day=1, hour=5, minute=0, second=0, microsecond=0)
    return int(cycle_dt.timestamp())


# ==================== 惰性时间戳广播源核心类 ====================

class LazyTimerBroadcaster:
    """全局惰性时间广播源"""

    MAX_SAFE_DELTA = 86400 * 30  # 最大允许连续推演 30 天，防止过大时间跳跃

    def pulse(self, ctx, uid, now_ts=None, force=False):
        """
        核心物理时间流逝脉冲：
        1. 获取用户水位线；
        2. 微防抖与周期边界判定（未跨界且 < 5s 时极速跳过）；
        3. 按严格优先级广播跨周期事件（月 -> 周一 -> 周四 -> 日）；
        4. 广播连续物理时间流逝事件（TIME_TICK）；
        5. 原子更新水位线。
        """
        if not uid or not hasattr(ctx, 'db') or ctx.db is None:
            return

        if now_ts is None:
            now_ts = int(time.time())

        db = ctx.db
        watermark = db.get_timer_watermark(uid)
        last_pulse = int(watermark.get('last_pulse_ts') or 0)
        last_daily = int(watermark.get('last_daily_5am_ts') or 0)
        last_wk_mon = int(watermark.get('last_weekly_mon_ts') or 0)
        last_wk_thu = int(watermark.get('last_weekly_thu_ts') or 0)
        last_monthly = int(watermark.get('last_monthly_ts') or 0)

        # 计算当前目标周期时间点
        cur_daily_5am = get_daily_5am_ts(now_ts)
        cur_wk_mon_5am = get_weekly_mon_5am_ts(now_ts)
        cur_wk_thu_5am = get_weekly_thu_5am_ts(now_ts)
        cur_monthly_5am = get_monthly_5am_ts(now_ts)

        # Tier-1 极速轻量过滤：无周期跨越且与上次脉冲间隔小于 5 秒
        is_crossing_boundary = (
            last_daily < cur_daily_5am or
            last_wk_mon < cur_wk_mon_5am or
            last_wk_thu < cur_wk_thu_5am or
            last_monthly < cur_monthly_5am
        )

        if not force and not is_crossing_boundary and (now_ts - last_pulse < 5) and (last_pulse > 0):
            return

        # 计算物理时间增量（带防倒流保护）
        if last_pulse <= 0:
            delta_t = 0
        else:
            delta_t = max(0, min(now_ts - last_pulse, self.MAX_SAFE_DELTA))

        updates = {'last_pulse_ts': now_ts}

        # ---------------- 周期重置事件广播（按严格级联时序） ----------------

        # 1. 每月 1 日 05:00 跨月重置
        if last_monthly < cur_monthly_5am:
            bus.emit(Events.MONTHLY_RESET_5AM, ctx, uid, cycle_start_ts=cur_monthly_5am, now_ts=now_ts)
            updates['last_monthly_ts'] = cur_monthly_5am

        # 2. 每周一 05:00 周常重置
        if last_wk_mon < cur_wk_mon_5am:
            bus.emit(Events.WEEKLY_RESET_MON_5AM, ctx, uid, cycle_start_ts=cur_wk_mon_5am, now_ts=now_ts)
            updates['last_weekly_mon_ts'] = cur_wk_mon_5am

        # 3. 每周四 05:00 梦境再构/Boss 玩法轮换
        if last_wk_thu < cur_wk_thu_5am:
            bus.emit(Events.WEEKLY_RESET_THU_5AM, ctx, uid, cycle_start_ts=cur_wk_thu_5am, now_ts=now_ts)
            updates['last_weekly_thu_ts'] = cur_wk_thu_5am

        # 4. 每日 05:00 日常跨天重置
        if last_daily < cur_daily_5am:
            bus.emit(Events.DAILY_RESET_5AM, ctx, uid, cycle_start_ts=cur_daily_5am, now_ts=now_ts)
            updates['last_daily_5am_ts'] = cur_daily_5am

        # ---------------- 连续物理时间增量广播 ----------------
        if delta_t > 0:
            bus.emit(Events.TIME_TICK, ctx, uid, delta_seconds=delta_t, now_ts=now_ts)

        # 原子落库水位线
        db.update_timer_watermark(uid, **updates)

    def pulse_heartbeat(self, ctx, uid, now_ts=None):
        """
        客户端活跃心跳脉冲：
        用于精确统计玩家在游戏客户端内的活跃在线时长，消除空转虚增与强杀丢失。
        """
        if not uid or not hasattr(ctx, 'db') or ctx.db is None:
            return

        if now_ts is None:
            now_ts = int(time.time())

        db = ctx.db
        watermark = db.get_timer_watermark(uid)
        last_hb = int(watermark.get('last_heartbeat_ts') or 0)
        today_online_sec = int(watermark.get('today_online_seconds') or 0)
        rec_date = str(watermark.get('today_online_date') or '')

        today_str = datetime.date.fromtimestamp(now_ts).isoformat()
        if rec_date != today_str:
            today_online_sec = 0

        # 滑动窗口：如果上一次发包在 60 秒内，计入实际增量；超过 60 秒（掉线/挂起重连）最多补 30 秒
        if last_hb <= 0:
            active_delta = 0
        else:
            diff = now_ts - last_hb
            if diff <= 0:
                active_delta = 0
            elif diff <= 60:
                active_delta = diff
            else:
                active_delta = 30

        today_online_sec += active_delta

        db.update_timer_watermark(
            uid,
            last_heartbeat_ts=now_ts,
            today_online_seconds=today_online_sec,
            today_online_date=today_str,
            is_online=1
        )

        if active_delta > 0:
            bus.emit(Events.HEARTBEAT_ACTIVE_TICK, ctx, uid, active_delta=active_delta, now_ts=now_ts)

        # 同步触发物理时间脉冲
        self.pulse(ctx, uid, now_ts=now_ts)

    def on_disconnect(self, ctx, uid, now_ts=None):
        """客户端 TCP 连接关闭钩子，立即标记离线"""
        if not uid or not hasattr(ctx, 'db') or ctx.db is None:
            return

        if now_ts is None:
            now_ts = int(time.time())

        db = ctx.db
        db.update_timer_watermark(uid, is_online=0, last_logout_ts=now_ts)
        bus.emit(Events.USER_DISCONNECT, ctx, uid, logout_ts=now_ts)


# 全局单例广播源
lazy_timer = LazyTimerBroadcaster()
