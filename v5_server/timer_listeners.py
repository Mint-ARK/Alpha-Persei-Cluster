# -*- coding: utf-8 -*-
"""
timer_listeners.py — 全局时间事件订阅器聚合模块

聚合监听 LazyTimer 广播的标准物理时间与周期事件：
1. StaminaListener: 6分钟体力自然恢复，严格对齐客户端倒计时；
2. DormListener: 游园街食堂烹饪产出与后宅英雄休息疲劳恢复（[2026-09-06 模块迁移] 已迁至 backhome_service.py）；
3. TaskAndBPResetListener: 日常(含战令每日)/周常(含战令每周与周经验上限)/活跃度PT跨天跨周原子重置；
4. BossChallengeResetListener: 梦境再构每日次数与周四周期轮换重置；
5. ShopResetListener: 商店日/周/月限购记录刷新。
"""

import time
import json
import logging
from event_bus import bus, Events

logger = logging.getLogger('timer_listeners')

STAMINA_RECOVER_INTERVAL = 360  # 6分钟 = 360秒


# ==================== 1. 体力自然恢复监听器 ====================

@bus.subscribe(Events.TIME_TICK)
def on_stamina_tick(ctx, uid, delta_seconds=0, now_ts=None, **kwargs):
    """连续时间流逝驱动体力（活力 ID=4）自然恢复：全服统一由 fatigue_service 引擎处理。"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    import fatigue_service as _fs
    _fs.settle_fatigue(ctx.db, uid, now_ts=now_ts)


# ==================== 2. 后宅与食堂做菜监听器 ====================
# [2026-09-06 游园街模块迁移] 原 on_dorm_tick（食堂惰性结算 + 宿舍疲劳恢复）
# 已迁至 backhome_service.py，由其模块 import 时向总线订阅 TIME_TICK。


# ==================== 3. 任务、战令与活跃度周期重置监听器 ====================

@bus.subscribe(Events.DAILY_RESET_5AM)
def on_daily_task_and_bp_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """每日 05:00 跨天重置：日常任务、战令每日任务、游园街每日、每日活跃度 PT 重置与登录任务自增。"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    if now_ts is None:
        now_ts = int(time.time())
    db = ctx.db

    from lazy_timer import get_daily_5am_ts
    next_daily_5am = (cycle_start_ts or get_daily_5am_ts(now_ts)) + 86400

    # 1. 重置日常类任务 (task_type: 6=日常, 7=战令每日, 901=游园街每日, 1101=活动随机每日, 3001=活动每日)
    db.execute(
        "UPDATE task SET progress=0, complete_flag=0, claimed_ts=0, expired_ts=? "
        "WHERE uid=? AND (task_type IN (6, 7, 901, 1101, 3001) OR (task_id BETWEEN 6001 AND 6099) OR (task_id BETWEEN 71000 AND 71999))",
        (next_daily_5am, uid)
    )

    # 每日登录任务自动达成 (6001 日常登录, 71001 战令登录)
    db.execute(
        "UPDATE task SET progress=1 WHERE uid=? AND task_id IN (6001, 71001)",
        (uid,)
    )

    # 防御性清除 claim_ledger 中残存的日常/战令每日类任务记录（防止历史脏数据阻塞后续完成）
    db.execute(
        "DELETE FROM claim_ledger WHERE uid=? AND kind='task' AND key_id IN ("
        "SELECT task_id FROM task_cfg WHERE task_type IN (6, 7, 901, 1101, 3001) OR task_id BETWEEN 10000 AND 10015"
        ")",
        (uid,)
    )

    # 2. 重置每日活跃度 PT (activity_pt_id=1) 并同步清零每日任务评分货币 (currency id=22)
    db.execute(
        "UPDATE activity_pt SET active_point=0, get_id_list='[]' WHERE uid=? AND activity_pt_id=1",
        (uid,)
    )
    db.execute(
        "UPDATE currency SET num=0 WHERE uid=? AND id=22",
        (uid,)
    )

    # 3. 重置后宅英雄每日投喂次数 (feed_times)
    db.execute("UPDATE backhome_hero SET feed_times=0 WHERE uid=?", (uid,))

    # 4. 更新 game_user 上的遗留时间戳标记
    db.execute(
        "UPDATE game_user SET last_daily_task_refresh_ts=? WHERE uid=?",
        (cycle_start_ts or now_ts, uid)
    )
    if hasattr(ctx, 'log'):
        ctx.log(f"[LazyTimer] 用户 {uid} 触发每日 05:00 跨天重置（日常任务/战令每日/每日活跃度/后宅投喂已清空）")


@bus.subscribe(Events.WEEKLY_RESET_MON_5AM)
def on_weekly_task_and_bp_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """每周一 05:00 周常重置：周常任务、战令每周任务、游园街每周、战令周经验上限清零、每周活跃度 PT 重置。"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    if now_ts is None:
        now_ts = int(time.time())
    db = ctx.db

    from lazy_timer import get_weekly_mon_5am_ts
    next_wk_mon_5am = (cycle_start_ts or get_weekly_mon_5am_ts(now_ts)) + 7 * 86400

    # 1. 重置周常类任务 (task_type: 5=周常, 8=战令每周, 902=游园街每周)
    db.execute(
        "UPDATE task SET progress=0, complete_flag=0, claimed_ts=0, expired_ts=? "
        "WHERE uid=? AND (task_type IN (5, 8, 902) OR (task_id BETWEEN 5001 AND 5099) OR (task_id BETWEEN 81000 AND 81999))",
        (next_wk_mon_5am, uid)
    )

    # 防御性清除 claim_ledger 中残存的周常/战令每周类任务记录
    db.execute(
        "DELETE FROM claim_ledger WHERE uid=? AND kind='task' AND key_id IN ("
        "SELECT task_id FROM task_cfg WHERE task_type IN (5, 8, 902)"
        ")",
        (uid,)
    )

    # 2. 战令本周获取经验清零 (weekly_gain_exp = 0) 并更新战令下次刷新时间戳 (next_refresh_timestamp)
    db.execute(
        "UPDATE battlepass SET weekly_gain_exp=0, next_refresh_timestamp=?, update_ts=? WHERE uid=?",
        (next_wk_mon_5am, now_ts, uid)
    )

    # 3. 重置每周活跃度 PT (activity_pt_id=3) 并同步清零每周任务评分货币 (currency id=35)
    db.execute(
        "UPDATE activity_pt SET active_point=0, get_id_list='[]' WHERE uid=? AND activity_pt_id=3",
        (uid,)
    )
    db.execute(
        "UPDATE currency SET num=0 WHERE uid=? AND id=35",
        (uid,)
    )

    # 4. 重置游园街贴票本周获取积分上限 (weekly_point = 0)
    db.execute(
        "UPDATE idol_trainee_rank SET weekly_point=0, update_ts=? WHERE uid=?",
        (now_ts, uid)
    )

    # 5. [已关闭] 游园街贴票跨周期（每 4 个星期一）清空重置
    # 按照最新业务契约：游园贴票（Item 61）在达成第 4 档（400贴票）领取自选碎片时原子扣除 400 点并开启新一轮轮转，
    # 贴票属于玩家持续累积的代币资产，绝不能在满 4 周时被定时器强制清零。
    # 原 db.execute("UPDATE currency SET num=0 WHERE uid=? AND id=61") 与 rank_got 清空已永久关闭。

    # 6. 更新 game_user 遗留时间戳标记
    db.execute(
        "UPDATE game_user SET last_weekly_task_refresh_ts=? WHERE uid=?",
        (cycle_start_ts or now_ts, uid)
    )

    # 7. 重置管理员猫咪探索周常累计天数与宝箱领取状态
    try:
        db.execute(
            "UPDATE admin_cat_explore_data SET weekly_time=0, weekly_reward_state=0, weekly_scene_open=0, last_weekly_reset_ts=?, update_ts=? WHERE uid=?",
            (cycle_start_ts or now_ts, now_ts, uid)
        )
    except Exception:
        pass

    if hasattr(ctx, 'log'):
        ctx.log(f"[LazyTimer] 用户 {uid} 触发每周一 05:00 周常重置（周常任务/战令每周/战令周经验/游园街周积分/猫咪探索周宝箱已清空更新）")


# ==================== 4. 梦境再构（Boss Challenge）周期轮换监听器 ====================

@bus.subscribe(Events.DAILY_RESET_5AM)
def on_boss_challenge_daily_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """梦境再构每日 05:00 重置挑战次数"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    db = ctx.db
    db.execute("UPDATE boss_challenge_progress SET use_times=0, last_daily_ts=? WHERE uid=?", (cycle_start_ts or now_ts, uid))


@bus.subscribe(Events.WEEKLY_RESET_THU_5AM)
def on_boss_challenge_thu_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """梦境再构每周四 05:00 周期轮换重置"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    if now_ts is None:
        now_ts = int(time.time())
    db = ctx.db
    cycle_id = int((now_ts - 1700000000) // 604800)

    db.execute("DELETE FROM boss_challenge_normal WHERE uid=?", (uid,))
    db.execute("DELETE FROM boss_challenge_advance WHERE uid=?", (uid,))
    db.execute("DELETE FROM boss_challenge_scores WHERE uid=?", (uid,))
    db.execute("DELETE FROM boss_challenge_exchange WHERE uid=?", (uid,))
    db.execute("DELETE FROM boss_challenge_claimed_rewards WHERE uid=?", (uid,))
    db.execute(
        "UPDATE boss_challenge_progress SET cycle_id=?, use_times=0, last_weekly_ts=?, update_ts=? WHERE uid=?",
        (cycle_id, cycle_start_ts or now_ts, now_ts, uid)
    )
    if hasattr(ctx, 'log'):
        ctx.log(f"[LazyTimer] 用户 {uid} 触发梦境再构周四 05:00 周期轮换（周期 ID: {cycle_id}）")


# ==================== 5. 商店限购周期重置监听器 ====================

@bus.subscribe(Events.DAILY_RESET_5AM)
def on_shop_daily_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """商店每日 05:00 重置（委托给 ShopService：每日限购清零、每日采购加权轮换货架与刷新次数清零、顺风车推送）"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    from shop_service import ShopService
    ShopService.get_instance(db=ctx.db).on_daily_reset_5am(ctx, uid, cycle_start_ts=cycle_start_ts, now_ts=now_ts, **kwargs)


@bus.subscribe(Events.WEEKLY_RESET_MON_5AM)
def on_shop_weekly_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """商店每周一 05:00 重置（委托给 ShopService：周限购清零）"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    from shop_service import ShopService
    ShopService.get_instance(db=ctx.db).on_weekly_reset_mon_5am(ctx, uid, cycle_start_ts=cycle_start_ts, now_ts=now_ts, **kwargs)


@bus.subscribe(Events.MONTHLY_RESET_5AM)
def on_shop_monthly_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """商店每月 1 日 05:00 重置（委托给 ShopService：月限购清零）"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    from shop_service import ShopService
    ShopService.get_instance(db=ctx.db).on_monthly_reset_5am(ctx, uid, cycle_start_ts=cycle_start_ts, now_ts=now_ts, **kwargs)


@bus.subscribe(Events.TIME_TICK)
def on_shop_time_tick(ctx, uid, delta_seconds=0, now_ts=None, **kwargs):
    """时间推演：清理到期的倒计时限时购买记录"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    from shop_service import ShopService
    ShopService.get_instance(db=ctx.db).on_time_tick(ctx, uid, delta_seconds=delta_seconds, now_ts=now_ts, **kwargs)


def register_timer_listeners():
    """确保订阅器加载注册（显式调用或 import 时自动注册）"""
    logger.info("[TimerListeners] 全局时间与周期事件订阅器已装配完毕。")
