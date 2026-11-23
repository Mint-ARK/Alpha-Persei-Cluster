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
    from task_cycle_notifications import task_cycle_reset, reset_task_progress, reset_activity_points, DAILY_TASK_FILTER
    with task_cycle_reset(ctx, uid, DAILY_TASK_FILTER, weekly=False):

        from lazy_timer import get_daily_5am_ts
        next_daily_5am = (cycle_start_ts or get_daily_5am_ts(now_ts)) + 86400

        # 1. 重置日常类任务 (task_type: 6=日常, 7=战令每日, 901=游园街每日, 1101=活动随机每日, 3001=活动每日)
        reset_task_progress(db, uid, DAILY_TASK_FILTER, next_daily_5am, (6001, 71001))

        # 防御性清除 claim_ledger 中残存的日常/战令每日类任务记录（防止历史脏数据阻塞后续完成）
        db.execute(
            "DELETE FROM claim_ledger WHERE uid=? AND kind='task' AND key_id IN ("
            "SELECT task_id FROM task_cfg WHERE task_type IN (6, 7, 901, 1101, 3001) OR task_id BETWEEN 10000 AND 10015"
            ")",
            (uid,)
        )

        # 2. 重置每日活跃度 PT (activity_pt_id=1) 并同步清零每日任务评分货币 (currency id=22)
        reset_activity_points(db, uid, 1, 22)

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
    from paid_entitlement_service import settle
    settle(ctx, uid, now_ts)
    from task_cycle_notifications import task_cycle_reset, reset_task_progress, reset_activity_points, WEEKLY_TASK_FILTER
    with task_cycle_reset(ctx, uid, WEEKLY_TASK_FILTER, weekly=True):

        from lazy_timer import get_weekly_mon_5am_ts
        next_wk_mon_5am = (cycle_start_ts or get_weekly_mon_5am_ts(now_ts)) + 7 * 86400

        # 1. 重置周常类任务 (task_type: 5=周常, 8=战令每周, 902=游园街每周)
        reset_task_progress(db, uid, WEEKLY_TASK_FILTER, next_wk_mon_5am)

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
        reset_activity_points(db, uid, 3, 35)

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

        # 7. 刷新探索领取资格和场景提示，累计倍率由成功领取清零。
        week_start = cycle_start_ts or get_weekly_mon_5am_ts(now_ts)
        if db.query("SELECT 1 FROM admin_cat_explore_data WHERE uid=? AND COALESCE(last_weekly_reset_ts,0)<?",
                    (uid, week_start)):
            db.execute(
                "UPDATE admin_cat_explore_data SET weekly_reward_state=0, weekly_scene_open=0, "
                "last_weekly_reset_ts=?, update_ts=? WHERE uid=?",
                (week_start, now_ts, uid)
            )
            encoder = getattr(ctx, "codec_encode", None)
            append = getattr(ctx, "append_frame", None)
            if callable(encoder) and callable(append):
                from admin_cat_explore_service import AdminCatExploreService
                from middleware import DownFrame

                data = AdminCatExploreService.get_instance().generate_sc_67001_dict(db, uid)
                snapshot = encoder("sc_67001", data)
                if not snapshot:
                    raise ValueError("探索派遣周重置状态帧为空")
                append(DownFrame(67001, snapshot))

        if hasattr(ctx, 'log'):
            ctx.log(f"[LazyTimer] 用户 {uid} 触发每周一 05:00 周常重置（周常任务/战令每周/战令周经验/游园街周积分/猫咪探索领取资格已刷新）")


# ==================== 4. 梦境再构（Boss Challenge）周期轮换监听器 ====================

@bus.subscribe(Events.DAILY_RESET_5AM)
def on_boss_challenge_daily_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """梦境再构每日 05:00 重置挑战次数"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    ctx.db.check_and_lazy_refresh_boss_challenge(uid, now_ts)


@bus.subscribe(Events.WEEKLY_RESET_THU_5AM)
def on_boss_challenge_thu_reset(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    """梦境再构每周四 05:00 周期轮换重置"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    if now_ts is None:
        now_ts = int(time.time())
    # 与读取路径共用模块水位，惰性刷新已完成时不再清玩家的新进度。
    changed = ctx.db.check_and_lazy_refresh_boss_challenge(uid, now_ts)
    if changed and hasattr(ctx, 'log'):
        ctx.log(f"[LazyTimer] 用户 {uid} 梦境再构周期已刷新")

    from boss_challenge_cycle import ADVANCE_GROUP_ROTATION_START
    if now_ts >= ADVANCE_GROUP_ROTATION_START:
        _push_boss_challenge_state(ctx, uid, now_ts)


def _push_boss_challenge_state(ctx, uid, now_ts=None):
    from middleware import DownFrame
    from weekly_challenge_service import get_boss_challenge_data

    overview, normal, advance = get_boss_challenge_data(uid, ctx.db, strict=True, now_ts=now_ts)
    frames = []
    # 45001 会通知两个客户端列表刷新，先推送概览与扭曲梦境。
    for cmd, data in ((45201, overview), (45101, advance), (45001, normal)):
        payload = ctx.codec_encode(f"sc_{cmd}", data)
        if not payload:
            raise ValueError(f"梦境再构刷新帧 sc_{cmd} 编码失败")
        frames.append(DownFrame(cmd, payload))
    for frame in frames:
        ctx.append_frame(frame)


@bus.subscribe(Events.PLAYER_LEVEL_UP)
def on_boss_challenge_unlock(ctx, uid, old_lv=1, new_lv=1, **kwargs):
    """管理员等级首次达到 30 时下发当周常规梦境档位。"""
    if uid and getattr(ctx, 'db', None) is not None and old_lv < 30 <= new_lv:
        _push_boss_challenge_state(ctx, uid)


@bus.subscribe(Events.WEEKLY_RESET_THU_5AM)
def on_core_verification_thu_reset(ctx, uid, now_ts=None, **kwargs):
    """向在线玩家下发新的一期和对应前瞻。"""
    if not uid or getattr(ctx, 'db', None) is None:
        return
    from middleware import DownFrame
    from weekly_challenge_service import get_core_verification_data

    payload = ctx.codec_encode("sc_75009", get_core_verification_data(uid, ctx.db, now_ts))
    if not payload:
        raise ValueError("回归测试刷新帧 sc_75009 编码失败")
    ctx.append_frame(DownFrame(75009, payload))


@bus.subscribe(Events.WEEKLY_RESET_THU_5AM)
def on_special_challenge_rotation(ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
    from core_special_challenge import ANCHOR, COMMANDS, active_groups, activity_rows, period

    if not uid or getattr(ctx, 'db', None) is None or not hasattr(ctx, 'append_frame'):
        return
    start, _, _ = period(now_ts)
    if start <= ANCHOR or cycle_start_ts != start:
        return

    from generator import gen_payload
    from middleware import DownFrame

    version = getattr(ctx, 'resource_version', None)
    packets = [(11003, ctx.codec_encode('sc_11003', {'activity': row}))
               for row in activity_rows(version, now_ts)]
    for group in active_groups(version, now_ts):
        cmd = COMMANDS[group.mode]
        packets.append((cmd, gen_payload(cmd, uid=uid, db=ctx.db, res_version=version,
                                        special_now=now_ts)))
    if not all(payload for _, payload in packets):
        raise ValueError('特殊挑战轮换状态帧编码失败')
    for cmd, payload in packets:
        ctx.append_frame(DownFrame(cmd, payload))


@bus.subscribe(Events.DAILY_RESET_5AM)
def on_seizure_daily_reset(ctx, uid, now_ts=None, **kwargs):
    """刷新已在线客户端的关卡、今日积分和到期的两类倒计时。"""
    if not uid or getattr(ctx, 'db', None) is None:
        return
    if not ctx.db.query("SELECT 1 FROM equip_seizure_progress WHERE uid=?", (uid,)):
        return
    from middleware import DownFrame
    from weekly_challenge_service import get_equip_seizure_data

    data = get_equip_seizure_data(uid, ctx.db, now_ts, strict=True)
    payload = ctx.codec_encode("sc_35011", data)
    if not payload:
        raise ValueError("介质攫取刷新帧 sc_35011 编码失败")
    ctx.append_frame(DownFrame(35011, payload))


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


@bus.subscribe(Events.WEEKLY_RESET_MON_5AM)
def on_polyhedron_weekly_reset(ctx, uid, now_ts=None, **kwargs):
    """在线跨周刷新历程，并推送时间、经验和领取状态。"""
    if not uid or getattr(ctx, 'db', None) is None:
        return
    if not ctx.db.query('SELECT 1 FROM polyhedron_meta WHERE uid=? LIMIT 1', (uid,)):
        return  # 尚未开启玩法的新档不因时间广播收到玩法初始化帧。
    from account_db import savepoint_transaction
    from operation_notifications import checkpoint_notifications, restore_notifications
    from polyhedron_service import PolyhedronRunManager
    from weekly_challenge_service import get_polyhedron_activity
    import generator
    from codec import encode
    from middleware import DownFrame
    version = getattr(ctx, 'res_version', None)
    activity = get_polyhedron_activity(version, now_ts)
    checkpoint = checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            PolyhedronRunManager.check_and_refresh_policy_cycle(
                uid, ctx.db, activity_id=activity['activity_id'])
            if hasattr(ctx, 'append_frame') or hasattr(ctx, 'pending_frames'):
                packets = [(11003, encode('sc_11003', {'activity': activity})),
                           (17023, generator.gen_payload(17023, uid=uid, db=ctx.db,
                                                       touched_items=[45], strict=True)),
                           (18001, generator.gen_payload(18001, uid=uid, db=ctx.db,
                                                       res_version=version))]
                for cmd, payload in packets:
                    if not isinstance(payload, bytes) or not payload:
                        raise ValueError(f'多维周刷新通知sc_{cmd}生成失败')
                    frame = DownFrame(cmd, payload)
                    if hasattr(ctx, 'append_frame'):
                        ctx.append_frame(frame)
                    else:
                        ctx.pending_frames.append(frame)
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise


@bus.subscribe(Events.TIME_TICK)
@bus.subscribe(Events.DAILY_RESET_5AM)
def on_paid_entitlement_tick(ctx, uid, now_ts=None, **kwargs):
    """Lazy settlement, including offline days, through the request's own save."""
    from paid_entitlement_service import settle
    settle(ctx, uid, now_ts)
