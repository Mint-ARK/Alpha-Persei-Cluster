# -*- coding: utf-8 -*-
"""
event_bus.py — V5 服务端全局事件总线（EventBus）
实现底层业务（战斗、背包、商店、养成、后宅）与上层模块（任务、战令、图鉴、成就）彻底解耦。
"""

import logging

logger = logging.getLogger('event_bus')


class Events:
    # ================= 关卡与战斗生命周期事件 =================
    STAGE_START          = 'stage_start'          # 战斗/关卡开始 (stage_id, heroes, battle_times, is_story)
    STAGE_PASS           = 'stage_pass'           # 关卡/战斗通关或扫荡 (times, stage_id, stage_type, win_stars)
    STAGE_FIRST_CLEAR    = 'stage_first_clear'    # 关卡首次通关 (stage_id, stage_type, first_rewards)
    STAGE_FAIL           = 'stage_fail'           # 战斗失败 (stage_id, heroes)
    STAGE_QUIT           = 'stage_quit'           # 战斗中途退出 (stage_id)
    CHAPTER_REWARD_CLAIM = 'chapter_reward_claim' # 领取章节星级奖励 (chapter_id, star_level, rewards)
    MONSTER_KILL         = 'monster_kill'         # 击杀怪物/视骸敌人 (monster_id, count, race, monster_type)

    STAMINA_COST     = 'stamina_cost'     # 消耗体力/吨吨值 (amount)
    GOLD_COST        = 'gold_cost'        # 消耗金币/艾因索菲币 (amount)
    SHOP_BUY         = 'shop_buy'         # 商店购买物品 (shop_id, goods_id, count)
    PERIODIC_GIFT_BUY = 'periodic_gift_buy' # 连续周期礼包购买 (goods_id, desc_id, buy_num)
    BUY_FATIGUE      = 'buy_fatigue'      # 移转之辉购买体力 (times, count)
    HERO_UPGRADE     = 'hero_upgrade'     # 修正者升级/突破/钥从升级 (hero_id, new_level)
    EQUIP_REFORGE    = 'equip_reforge'    # 刻印赋能/重构 (equip_id)
    DISPATCH_FINISH     = 'dispatch_finish'     # 修正者派遣完成 (dispatch_id)
    DORM_ACTION         = 'dorm_action'         # 游园街后宅交互 (action_type: visit, game, train, commission, furniture)
    POLYHEDRON_PASS          = 'polyhedron_pass'          # 多维变量通关/参与 (times)
    POLYHEDRON_TACTIC_KILL   = 'polyhedron_tactic_kill'   # 虚构推演战术击杀预留 (tactic_type: 1..6, kill_count)
    DISORDER_ABYSS_PASS      = 'disorder_abyss_pass'      # 失序深阱通关预留 (tier: 1..6)
    SUMMER_PARK_ACTION       = 'summer_park_action'       # 盛夏乐园活动专属预留 (oper)
    SUMMER_PUB_COOK          = 'summer_pub_cook'          # 夏日餐厅做菜与特写CG (dish_id, stage)
    SUMMER_PUB_LEVEL_PASS    = 'summer_pub_level_pass'    # 夏日餐厅关卡通关 (level_id)
    ROGUE_CARD_POST_FINISH   = 'rogue_card_post_finish'   # 诡谈夜话完成帖子 (post_id)
    STORY_BRANCH_EXPLORE     = 'story_branch_explore'     # 剧情多分支解谜与碎片收集预留 (chapter_id, branch_id, memory_count)
    DRAW_PERFORM        = 'draw_perform'        # 抽卡执行 (pool_id, draw_count, pool_group)
    DRAW_TEN_RESULT     = 'draw_ten_result'     # 抽卡十连产出 (items, pool_id)
    USER_LOGIN          = 'user_login'          # 玩家登录/跨天重置 (is_first_login)
    USER_BIRTHDAY       = 'user_birthday'       # 玩家/管理员生日广播 (uid, cur_year)
    PLAYER_BIRTHDAY     = 'user_birthday'       # 兼容旧常量名
    PLAYER_LEVEL_UP     = 'player_level_up'     # 玩家账号等级提升 (old_lv, new_lv, stamina_gain)
    ACTIVITY_POINT_GAIN = 'activity_point_gain' # 获得活跃度/PT值 (pt_id, amount, item_id)

    # ================= 充值发货与付费体系解耦事件 =================
    RECHARGE_SUCCESS       = 'recharge_success'       # 充值成功通用广播 (uid, goods_id, cost_cents, recharge_type, rewards)
    FIRST_RECHARGE_TRIGGER = 'first_recharge_trigger' # 首次充值达成广播 (uid, goods_id, cost_cents)
    MONTHLY_CARD_TRIGGER   = 'monthly_card_trigger'   # 月卡充值达成广播 (uid, goods_id, days)
    PASSPORT_BUY_TRIGGER   = 'passport_buy_trigger'   # 战令合约购买达成广播 (uid, goods_id, pay_level)

    # ================= 图鉴与收集系统联动事件 =================
    STORY_READ           = 'story_read'           # 剧情阅读/观看 (story_id, archive_id, story_kind, source)
    ARCHIVE_ANECDOTE_READ = 'archive_anecdote_read' # 档案轶事首次阅读 (archive_id, hero_id, rewards)
    HERO_UNLOCK          = 'hero_unlock'          # 解锁/获得新修正者 (hero_id, race)
    SERVANT_OBTAIN       = 'servant_obtain'       # 获得钥从/使魔 (servant_id)
    EQUIP_OBTAIN         = 'equip_obtain'         # 获得刻印/装备 (suit_id, pos)

    # ================= 芯片系统与红点联动事件 =================
    CHIP_UNLOCK              = 'chip_unlock'              # 芯片解锁 (chip_id, cat, hero_id)
    CHIP_EQUIP               = 'chip_equip'               # 芯片装配/卸下 (hero_id, slot_id, chip_id)
    CHAR_CHIP_ACTIVATE       = 'char_chip_activate'       # 角色助战模块芯片激活 (hero_id, chip_id, role_type_id)
    CHAR_CHIP_REDPOINT_CHECK = 'char_chip_redpoint_check' # 角色助战芯片红点检测广播 (hero_id, chip_id, can_unlock)

    # ================= 誓约系统与红点联动事件 =================
    OATH_UNLOCK              = 'oath_unlock'              # 缔结誓约 (hero_id, oath_time)
    OATH_LEVEL_UP            = 'oath_level_up'            # 誓约等级晋升 (hero_id, new_level)
    OATH_TASK_COMPLETE       = 'oath_task_complete'       # 誓约专属任务完成 (hero_id, task_id)
    OATH_NAME_CHANGE         = 'oath_name_change'         # 誓约自定义爱称修改 (hero_id, new_nick)
    MAIN_INTERACT            = 'main_interact'            # 主界面看板娘交互 (hero_id)
    DORM_GIFT                = 'dorm_gift'                # 家园赠送家具/礼物 (hero_id, furniture_id)
    DAILY_ASSISTANT          = 'daily_assistant'          # 设为主界面助理跨天累计 (hero_id, days)

    # ================= 外围系统（个性化/名片装扮）联动事件 =================
    PROFILE_UPDATE           = 'profile_update'          # 玩家个性化资料变更 (kind, value)
                                                             # kind: sign/nick/portrait/icon_frame/bubble/
                                                             #       card_bg/tags/poster_girl/show_hero/scene/bgm/
                                                             #       birthday/pure_mode/table_modules/likes/...

    # ================= 好感度系统与红点联动事件 =================
    TRUST_LEVEL_UP           = 'trust_level_up'           # 好感度阶级突破/升级 (hero_id, new_level, rewards)
    HERO_ARCHIVE_EXP_CHANGE  = 'hero_archive_exp_change'  # 一阶档案好感变动 (archive_id, old_exp, new_exp, delta, source)
    TRUST_MOOD_CHANGE        = 'trust_mood_change'        # 角色交心心情变动 (hero_id, old_mood, new_mood, reason)
    RELATION_NET_UNLOCK      = 'relation_net_unlock'      # 解锁关系网节点 (hero_id, node_id, tier, group_index)
    COMBO_SKILL_LEVEL_UP     = 'combo_skill_level_up'     # 连携技能升级 p73 (skill_id, new_level)
    COMBO_SKILL_PROGRESS     = 'combo_skill_progress'     # 连携出场计量变动广播 (cooperation_skill_server 发出:
                                                          #  combo_id, hero_ids, total_times, normal_times, hard_times, stage_kind)

    # ================= 统一时间驱动与周期广播源事件 =================
    TIME_TICK            = 'time_tick'            # 连续物理时间流逝推演 (delta_seconds, now_ts)
    DAILY_RESET_5AM      = 'daily_reset_5am'      # 每日 05:00 跨天重置 (cycle_start_ts, now_ts)
    WEEKLY_RESET_MON_5AM = 'weekly_reset_mon_5am' # 每周一 05:00 周常重置 (cycle_start_ts, now_ts)
    WEEKLY_RESET_THU_5AM = 'weekly_reset_thu_5am' # 每周四 05:00 梦境再构/玩法轮换 (cycle_start_ts, now_ts)
    MONTHLY_RESET_5AM    = 'monthly_reset_5am'    # 每月 1 日 05:00 月度重置 (cycle_start_ts, now_ts)
    HEARTBEAT_ACTIVE_TICK = 'heartbeat_active_tick' # 客户端滑动心跳活跃累加 (active_delta, now_ts)
    USER_DISCONNECT      = 'user_disconnect'      # 客户端断开连接 (logout_ts)
    SERVER_SHUTDOWN      = 'server_shutdown'      # 服务端进程退出信号 (shutdown_ts)


class EventBus:
    """轻量级全局事件总线，支持同步发布/订阅与异常沙箱隔离。"""
    _handlers = {}

    def __init__(self):
        # 兼容已有代码对实例属性的访问
        pass

    @classmethod
    def subscribe(cls, event_name, func=None):
        """支持装饰器与函数式两种用法:
        @bus.subscribe(Events.STAGE_PASS)
        def on_stage_pass(ctx, uid, **kwargs): ...
        或者:
        bus.subscribe(Events.STAGE_PASS, on_stage_pass)
        """
        def decorator(f):
            cls._handlers.setdefault(event_name, []).append(f)
            return f

        if func is not None:
            return decorator(func)
        return decorator

    @classmethod
    def register(cls, event_name, func):
        """函数式注册"""
        cls._handlers.setdefault(event_name, []).append(func)

    @classmethod
    def emit(cls, event_name, ctx=None, uid=0, **kwargs):
        """广播事件到所有订阅者。

        具备自适应传参与沙箱隔离：
        依次尝试:
        1. handler(ctx, uid, **kwargs)
        2. handler(uid=uid, **kwargs)
        3. handler(**kwargs)
        """
        handlers = cls._handlers.get(event_name, [])
        if not handlers:
            return

        for handler in list(handlers):
            try:
                try:
                    handler(ctx, uid, **kwargs)
                except TypeError:
                    try:
                        handler(uid=uid, **kwargs)
                    except TypeError:
                        handler(**kwargs)
            except Exception as e:
                hname = getattr(handler, '__name__', str(handler))
                msg = f'[EventBus ERROR] 事件 {event_name} 处理器 {hname} 执行异常: {e}'
                if hasattr(ctx, 'log'):
                    ctx.log(msg)
                else:
                    logger.exception(msg)


# 全局单例总线（可作为实例也可直接使用 EventBus 类）
bus = EventBus()
