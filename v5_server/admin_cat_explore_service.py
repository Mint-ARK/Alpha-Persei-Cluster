# -*- coding: utf-8 -*-
"""
admin_cat_explore_service.py — 管理员猫咪探索（弥弥尔探索 / 远征挂机收益玩法）领域服务

功能定位：
- 对应系统 ID 2801 (ViewConst.SYSTEM_ID.ADMIN_CAT_EXPLORE)，入口位于任务界面 (TaskView) 的探索快捷按钮。
- 管理员猫咪派遣挂机（4h/8h/10h/12h/20h）、多队列并发、倒计时与奇遇事件生成。
- 挂机完成结算：基于区域、挂机时长、猫咪专属技能加成与天赋加成精确计算产出（货币 53 经验、货币 54/55 代币、货币 56 芯片与奇遇事件）。
- 经验与等级系统：根据道具 53 累积量自适应提升探索等级 (1~5 级)，等级解锁更多区域、更多队列数量 (1~5 队) 与更长单次时间。
- 猫咪培养与技能升级：消耗货币 56 升级猫咪技能（1~10 级），解锁新猫咪。
- 周常奖励：每周探索天数累积 (1~7 天) 与周末宝箱阶梯倍率领取。
"""
import json
import math
import random
import time
from typing import Dict, List, Optional, Tuple

from inventory_service import InventoryService


class AdminCatExploreConst:
    # 探索相关代币/道具
    ITEM_EXP = 53              # 探索经验
    ITEM_COIN_COMMON = 54      # 探索通用货币 (弥弥尔代币)
    ITEM_COIN_RARE = 55        # 探索稀有货币
    ITEM_UPGRADE_CHIP = 56     # 探索培养芯片 (升级/解锁猫咪)

    # 错误码 / 提示码（严格对齐客户端官方 TipsCfg）
    SUCCESS = 0
    ERR_SYSTEM_LOCKED = 2
    ERR_PARAMS_INVALID = 2                     # ERROR_INVALID_OPERATION
    ERR_MIMIR_NOT_UNLOCKED = 23000             # ERROR_EXPLORE_MIMIR_NO_EXIST 管理喵不存在
    ERR_TIME_EXCEEDS_MAX = 23001               # ERROR_EXPLORE_TIME_ILLEGAL 探索时间不正确
    ERR_MIMIR_ALREADY_DISPATCHED = 23002       # ERROR_EXPLORE_MIMIR_ALREADY_EXIST
    ERR_AREA_ALREADY_EXPLORING = 23003         # ERROR_EXPLORE_AREA_NO_EXIST
    ERR_QUEUE_LIMIT_REACHED = 23004            # ERROR_EXPLORE_NUM_MAX 探索队列已满
    ERR_AREA_NOT_UNLOCKED = 23005              # ERROR_EXPLORE_LV_LIMIT 探索等级不足
    ERR_NOT_FINISHED_YET = 2
    ERR_ALREADY_CLAIMED = 2
    ERR_MATERIAL_NOT_ENOUGH = 402              # ERROR_ITEM_NOT_ENOUGH 道具/材料不足
    ERR_SKILL_MAX_LEVEL = 2
    ERR_CONDITION_NOT_MET = 2


# 等级配置 (1~5 级)
EXPLORE_LEVEL_CFG = {
    1: {"id": 1, "time": 4, "exp": 2400, "amount": 1, "area": [10001], "meow": [10001], "accumulate_rewards": [[54, 300], [55, 100]]},
    2: {"id": 2, "time": 8, "exp": 20000, "amount": 2, "area": [10001, 10002], "meow": [10001, 10002], "accumulate_rewards": [[54, 600], [55, 300]]},
    3: {"id": 3, "time": 10, "exp": 60000, "amount": 3, "area": [10001, 10002, 10003], "meow": [10001, 10002, 10003], "accumulate_rewards": [[54, 800], [55, 400]]},
    4: {"id": 4, "time": 12, "exp": 150000, "amount": 4, "area": [10001, 10002, 10003, 10004], "meow": [10001, 10002, 10003, 10004], "accumulate_rewards": [[54, 1000], [55, 600]]},
    5: {"id": 5, "time": 20, "exp": 0, "amount": 5, "area": [10001, 10002, 10003, 10004, 10005], "meow": [10001, 10002, 10003, 10004, 10005], "accumulate_rewards": [[54, 1200], [55, 800]]},
}

# 区域配置 (10001~10005)
EXPLORE_AREA_CFG = {
    10001: {"id": 10001, "area_name": "欧莫菲斯", "event_probability": 18, "reward": [[53, 50], [54, 60], [56, 8]], "event": [10001, 10002, 10003, 10004, 10005]},
    10002: {"id": 10002, "area_name": "诺曼斯", "event_probability": 21, "reward": [[53, 50], [55, 12], [56, 12]], "event": [20001, 20002, 20003, 20004, 20005]},
    10003: {"id": 10003, "area_name": "吉尔", "event_probability": 24, "reward": [[53, 50], [54, 80], [55, 18]], "event": [30001, 30002, 30003, 30004, 30005]},
    10004: {"id": 10004, "area_name": "欧莫菲斯旧址", "event_probability": 27, "reward": [[53, 50], [54, 100]], "event": [40001, 40002, 40003, 40004, 40005]},
    10005: {"id": 10005, "area_name": "虚空", "event_probability": 30, "reward": [[53, 50], [55, 22]], "event": [50001, 50002, 50003, 50004, 50005]},
}

# 管理员猫咪配置 (10001~10005)
EXPLORE_MEOW_CFG = {
    10001: {"id": 10001, "meow_name": "欧特", "skill": 10001, "inborn": 10001, "area_recommend": [10001], "consume": [], "unlock_condition": []},
    10002: {"id": 10002, "meow_name": "杰克", "skill": 10002, "inborn": 10002, "area_recommend": [10002], "consume": [[56, 600]], "unlock_condition": [[56, 400]]},
    10003: {"id": 10003, "meow_name": "猫之助", "skill": 10003, "inborn": 10003, "area_recommend": [10003], "consume": [[56, 700]], "unlock_condition": [[56, 2400]]},
    10004: {"id": 10004, "meow_name": "白金", "skill": 10004, "inborn": 10004, "area_recommend": [10004], "consume": [[56, 800]], "unlock_condition": [[56, 6000]]},
    10005: {"id": 10005, "meow_name": "巧克力", "skill": 10005, "inborn": 10005, "area_recommend": [10005], "consume": [[56, 900]], "unlock_condition": [[56, 16000]]},
}

# 技能配置 (1~10 级)
EXPLORE_MEOW_SKILL_CFG = {
    10001: {"id": 10001, "skill_type": 3, "skill_effect": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50], "skill_up_consume": [[[56, 300]], [[56, 300]], [[56, 400]], [[56, 400]], [[56, 500]], [[56, 500]], [[56, 600]], [[56, 600]], [[56, 700]]]},
    10002: {"id": 10002, "skill_type": 3, "skill_effect": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50], "skill_up_consume": [[[56, 300]], [[56, 300]], [[56, 400]], [[56, 400]], [[56, 500]], [[56, 500]], [[56, 600]], [[56, 600]], [[56, 700]]]},
    10003: {"id": 10003, "skill_type": 3, "skill_effect": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50], "skill_up_consume": [[[56, 300]], [[56, 300]], [[56, 400]], [[56, 400]], [[56, 500]], [[56, 500]], [[56, 600]], [[56, 600]], [[56, 700]]]},
    10004: {"id": 10004, "skill_type": 3, "skill_effect": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50], "skill_up_consume": [[[56, 300]], [[56, 300]], [[56, 400]], [[56, 400]], [[56, 500]], [[56, 500]], [[56, 600]], [[56, 600]], [[56, 700]]]},
    10005: {"id": 10005, "skill_type": 3, "skill_effect": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50], "skill_up_consume": [[[56, 300]], [[56, 300]], [[56, 400]], [[56, 400]], [[56, 500]], [[56, 500]], [[56, 600]], [[56, 600]], [[56, 700]]]},
}

# 奇遇事件配置
EXPLORE_EVENT_CFG = {
    10001: {"id": 10001, "effect": [1, 5]},
    10002: {"id": 10002, "effect": [2, 8]},
    10003: {"id": 10003, "effect": [1, 5]},
    10004: {"id": 10004, "effect": [2, 8]},
    10005: {"id": 10005, "effect": [1, 10]},
    20001: {"id": 20001, "effect": [1, 5]},
    20002: {"id": 20002, "effect": [2, 8]},
    20003: {"id": 20003, "effect": [1, 5]},
    20004: {"id": 20004, "effect": [2, 8]},
    20005: {"id": 20005, "effect": [1, 10]},
    30001: {"id": 30001, "effect": [1, 5]},
    30002: {"id": 30002, "effect": [2, 8]},
    30003: {"id": 30003, "effect": [1, 5]},
    30004: {"id": 30004, "effect": [2, 8]},
    30005: {"id": 30005, "effect": [1, 10]},
    40001: {"id": 40001, "effect": [1, 5]},
    40002: {"id": 40002, "effect": [2, 8]},
    40003: {"id": 40003, "effect": [1, 5]},
    40004: {"id": 40004, "effect": [2, 8]},
    40005: {"id": 40005, "effect": [1, 10]},
    50001: {"id": 50001, "effect": [1, 5]},
    50002: {"id": 50002, "effect": [2, 8]},
    50003: {"id": 50003, "effect": [1, 5]},
    50004: {"id": 50004, "effect": [2, 8]},
    50005: {"id": 50005, "effect": [1, 10]},
}

# 每周探索天数对应的大奖倍率
WEEKLY_RATE_MAP = [0, 1, 2, 3, 4, 6, 8, 10]


class AdminCatExploreService:
    _instance: Optional["AdminCatExploreService"] = None

    @classmethod
    def get_instance(cls) -> "AdminCatExploreService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _resolve_db(ctx_or_db):
        return getattr(ctx_or_db, "db", ctx_or_db)

    # ---------- 核心等级与状态计算 ----------

    def calculate_level(self, ctx_or_db, uid: int) -> Tuple[int, int]:
        """
        根据玩家当前持有的 53 号道具（探索经验）数量，严格依照客户端 ExploreLevelCfg 计算等级。
        :return: (level, current_level_exp)
        """
        db = self._resolve_db(ctx_or_db)
        _, total_exp = InventoryService.get_item_balance(db, uid, AdminCatExploreConst.ITEM_EXP)
        rem = int(total_exp or 0)
        level = 1
        reqs = [2400, 20000, 60000, 150000]
        for req in reqs:
            if rem >= req:
                rem -= req
                level += 1
            else:
                break
        return level, rem

    def get_or_create_explore_data(self, ctx_or_db, uid: int) -> dict:
        """获取或初始化玩家探索数据，支持跨周周期自愈与跨天累计。"""
        db = self._resolve_db(ctx_or_db)
        data = db.get_admin_cat_explore_data(uid)
        now = int(time.time())

        # 计算本周一 05:00 时间戳
        # 获取当前周一 05:00
        t_struct = time.localtime(now)
        # weekday: 0=Mon, 6=Sun
        days_since_monday = t_struct.tm_wday
        midnight_today = now - (t_struct.tm_hour * 3600 + t_struct.tm_min * 60 + t_struct.tm_sec)
        # 若今天已经过了 05:00，当前周期起点是周一 05:00；若还没到，算上周一 05:00
        mon_0500 = midnight_today - (days_since_monday * 86400) + (5 * 3600)
        if now < mon_0500:
            mon_0500 -= 7 * 86400

        if data is None:
            data = {
                "uid": uid,
                "weekly_time": 0,
                "daily_time": 0,
                "weekly_reward_state": 0,
                "weekly_scene_open": 0,
                "total_explore_c": 0,
                "last_weekly_reset_ts": mon_0500,
                "update_ts": now,
            }
            db.save_admin_cat_explore_data(uid, data)
            # 初始默认激活 10001 猫咪
            cats = db.get_admin_cat_list(uid)
            if not cats:
                db.unlock_admin_cat(uid, 10001, skill_level=1)
        else:
            # 跨周重置检测
            last_reset = data.get("last_weekly_reset_ts", 0)
            if last_reset < mon_0500:
                data["weekly_time"] = 0
                data["weekly_reward_state"] = 0
                data["weekly_scene_open"] = 0
                data["last_weekly_reset_ts"] = mon_0500
                db.save_admin_cat_explore_data(uid, data)

        # 确保基础猫咪 10001 存在
        cats = db.get_admin_cat_list(uid)
        if not cats:
            db.unlock_admin_cat(uid, 10001, skill_level=1)

        return data

    def generate_sc_67001_dict(self, ctx_or_db, uid: int) -> dict:
        """组装完整的 sc_67001 下行协议字典。"""
        db = self._resolve_db(ctx_or_db)
        data = self.get_or_create_explore_data(db, uid)
        cats = db.get_admin_cat_list(uid)
        queues = db.get_admin_cat_queues(uid)

        mimir_list = []
        for c in cats:
            mid = c["mimir_id"]
            lvl = c["skill_level"]
            skill_id = EXPLORE_MEOW_CFG.get(mid, {}).get("skill", mid)
            mimir_list.append({
                "mimir_id": mid,
                "skill": [{"skill_id": skill_id, "skill_level": lvl}]
            })

        return {
            "mimir": mimir_list,
            "explore_queue": queues,
            "weekly_time": data.get("weekly_time", 0),
            "daily_time": data.get("daily_time", 0),
            "weekly_reward_state": data.get("weekly_reward_state", 0),
            "weekly_scene_open": data.get("weekly_scene_open", 0),
            "total_explore_c": data.get("total_explore_c", 0),
        }

    # ---------- 探索派遣与时间轴生成 (67004) ----------

    def start_explore(self, ctx, uid: int, area_id: int, hour_time: int, mimir_id: int) -> int:
        """
        开始挂机探索 (cs_67004)。
        """
        db = self._resolve_db(ctx)
        self.get_or_create_explore_data(db, uid)
        level, _ = self.calculate_level(db, uid)
        lvl_cfg = EXPLORE_LEVEL_CFG.get(level, EXPLORE_LEVEL_CFG[1])

        # 1. 校验区域是否在当前等级允许范围内
        if area_id not in EXPLORE_AREA_CFG or area_id not in lvl_cfg["area"]:
            return AdminCatExploreConst.ERR_AREA_NOT_UNLOCKED

        # 2. 校验单次挂机时间是否超标
        if hour_time > lvl_cfg["time"] or hour_time <= 0:
            return AdminCatExploreConst.ERR_TIME_EXCEEDS_MAX

        # 3. 校验猫咪是否已解锁
        cats = {c["mimir_id"]: c for c in db.get_admin_cat_list(uid)}
        if mimir_id not in cats:
            return AdminCatExploreConst.ERR_MIMIR_NOT_UNLOCKED

        # 4. 校验队列与猫咪/区域占用
        queues = db.get_admin_cat_queues(uid)
        for q in queues:
            if q["area_id"] == area_id:
                return AdminCatExploreConst.ERR_AREA_ALREADY_EXPLORING
            if q["mimir_id"] == mimir_id:
                return AdminCatExploreConst.ERR_MIMIR_ALREADY_DISPATCHED

        if len(queues) >= lvl_cfg["amount"]:
            return AdminCatExploreConst.ERR_QUEUE_LIMIT_REACHED

        # 5. 生成挂机时间与奇遇时间轴事件
        now = int(time.time())
        duration_sec = hour_time * 3600
        stop_time = now + duration_sec

        # 时间轴事件：每 1800~3600 秒生成一个背景随机事件
        events = []
        cur_t = random.randint(60, 300)
        while cur_t < duration_sec - 120:
            addr = random.randint(101, 110)
            content = random.randint(201, 306)
            events.append({
                "time": cur_t,
                "address_id": addr,
                "content_id": content,
            })
            cur_t += random.randint(1800, 3600)

        # 6. 落库保存挂机队列
        db.save_admin_cat_queue(uid, area_id, mimir_id, now, stop_time, hour_time, events)

        # 7. 累计今日探索与每周天数
        exp_data = self.get_or_create_explore_data(db, uid)
        last_daily = exp_data.get("daily_time", 0)
        # 判定是否今天新一天 (05:00 刷新)
        is_same_day = False
        if last_daily > 0:
            t_last = time.localtime(last_daily)
            t_now = time.localtime(now)
            if t_last.tm_yday == t_now.tm_yday and t_last.tm_year == t_now.tm_year:
                is_same_day = True

        if not is_same_day:
            exp_data["daily_time"] = now
            exp_data["weekly_time"] = min(7, exp_data.get("weekly_time", 0) + 1)
            db.save_admin_cat_explore_data(uid, exp_data)

        return AdminCatExploreConst.SUCCESS

    # ---------- 探索完成与挂机收益结算 (67006) ----------

    def finish_explore(self, ctx, uid: int, area_id: int, force_finish: bool = True) -> Tuple[int, int, List[dict]]:
        """
        结算并领取挂机奖励 (cs_67006)。
        :param force_finish: 默认为 True，以提供单机调试与随时测试便利。
        :return: (result, event_id, reward_list)
        """
        db = self._resolve_db(ctx)
        self.get_or_create_explore_data(db, uid)
        queues = {q["area_id"]: q for q in db.get_admin_cat_queues(uid)}
        if area_id not in queues:
            return AdminCatExploreConst.ERR_PARAMS_INVALID, 0, []

        q = queues[area_id]
        now = int(time.time())
        if not force_finish and now < q["stop_time"]:
            return AdminCatExploreConst.ERR_NOT_FINISHED_YET, 0, []

        hour_time = q["target_explore_hour"]
        mimir_id = q["mimir_id"]
        area_cfg = EXPLORE_AREA_CFG.get(area_id, EXPLORE_AREA_CFG[10001])
        meow_cfg = EXPLORE_MEOW_CFG.get(mimir_id, EXPLORE_MEOW_CFG[10001])

        # 读取猫咪技能等级
        cats = {c["mimir_id"]: c for c in db.get_admin_cat_list(uid)}
        skill_lvl = cats.get(mimir_id, {}).get("skill_level", 1)
        skill_cfg = EXPLORE_MEOW_SKILL_CFG.get(meow_cfg["skill"], EXPLORE_MEOW_SKILL_CFG[10001])

        # 计算技能与天赋加成百分比
        skill_bonus = 0
        skill_type = skill_cfg.get("skill_type", 3)
        # 客户端技能加成条件：
        # skill_type in (1,2) 且 hour <= 8; skill_type in (3,4) 且 hour > 8; skill_type == 5
        if (skill_type in (1, 2) and hour_time <= 8) or (skill_type in (3, 4) and hour_time > 8) or (skill_type == 5):
            effects = skill_cfg.get("skill_effect", [5])
            eff_idx = min(len(effects) - 1, max(0, skill_lvl - 1))
            skill_bonus = effects[eff_idx]

        inborn_bonus = 0
        if area_id in meow_cfg.get("area_recommend", []):
            inborn_bonus = 10  # 天赋推荐区域固定提供 10% 增益

        # 随机奇遇事件判定
        event_id = 0
        event_bonus = 0
        prob = area_cfg.get("event_probability", 20)
        if random.randint(1, 100) <= prob:
            events = area_cfg.get("event", [])
            if events:
                event_id = random.choice(events)
                event_bonus = EXPLORE_EVENT_CFG.get(event_id, {}).get("effect", [1, 5])[1]

        total_multiplier = 1.0 + (skill_bonus + inborn_bonus + event_bonus) / 100.0

        # 计算基础掉落并应用倍率
        reward_items = []
        earned_explore_currency = 0
        for item_entry in area_cfg.get("reward", []):
            item_id = item_entry[0]
            base_rate = item_entry[1]
            amount = int(math.ceil(base_rate * hour_time * total_multiplier))
            if amount > 0:
                reward_items.append({"id": item_id, "num": amount})
                if item_id in (AdminCatExploreConst.ITEM_COIN_COMMON, AdminCatExploreConst.ITEM_COIN_RARE, AdminCatExploreConst.ITEM_UPGRADE_CHIP):
                    earned_explore_currency += amount

        # 发放奖励入库
        InventoryService.grant_items(ctx, uid, [(r["id"], r["num"]) for r in reward_items], source="admin_cat_explore")

        # 移除已完成队列
        db.remove_admin_cat_queue(uid, area_id)

        # 累积历史探索货币产出
        exp_data = self.get_or_create_explore_data(db, uid)
        exp_data["total_explore_c"] = exp_data.get("total_explore_c", 0) + earned_explore_currency
        db.save_admin_cat_explore_data(uid, exp_data)

        return AdminCatExploreConst.SUCCESS, event_id, reward_items

    # ---------- 猫咪技能升级 (67002) ----------

    def level_up_skill(self, ctx, uid: int, mimir_id: int, skill_id: int) -> int:
        """
        升级猫咪技能 (cs_67002)。
        """
        db = self._resolve_db(ctx)
        self.get_or_create_explore_data(db, uid)
        cats = {c["mimir_id"]: c for c in db.get_admin_cat_list(uid)}
        if mimir_id not in cats:
            return AdminCatExploreConst.ERR_MIMIR_NOT_UNLOCKED

        cur_level = cats[mimir_id]["skill_level"]
        if cur_level >= 10:
            return AdminCatExploreConst.ERR_SKILL_MAX_LEVEL

        skill_cfg = EXPLORE_MEOW_SKILL_CFG.get(skill_id, EXPLORE_MEOW_SKILL_CFG[10001])
        consumes = skill_cfg.get("skill_up_consume", [])
        c_idx = cur_level - 1
        if c_idx >= len(consumes):
            return AdminCatExploreConst.ERR_SKILL_MAX_LEVEL

        cost_item_entry = consumes[c_idx][0]
        cost_id, cost_num = cost_item_entry[0], cost_item_entry[1]

        # 检查并扣减材料
        _, balance = InventoryService.get_item_balance(db, uid, cost_id)
        if balance < cost_num:
            return AdminCatExploreConst.ERR_MATERIAL_NOT_ENOUGH

        InventoryService.cost_item(ctx, uid, cost_id, cost_num)

        # 技能等级 +1
        db.update_admin_cat_skill(uid, mimir_id, cur_level + 1)
        return AdminCatExploreConst.SUCCESS

    # ---------- 解锁新猫咪 (67010) ----------

    def unlock_mimir(self, ctx, uid: int, mimir_id: int) -> int:
        """
        解锁新猫咪 (cs_67010)。
        """
        db = self._resolve_db(ctx)
        if mimir_id not in EXPLORE_MEOW_CFG:
            return AdminCatExploreConst.ERR_PARAMS_INVALID

        exp_data = self.get_or_create_explore_data(db, uid)
        cats = {c["mimir_id"]: c for c in db.get_admin_cat_list(uid)}
        if mimir_id in cats:
            return AdminCatExploreConst.SUCCESS  # 已解锁幂等

        cfg = EXPLORE_MEOW_CFG[mimir_id]
        exp_data = self.get_or_create_explore_data(db, uid)
        tot_c = exp_data.get("total_explore_c", 0)

        # 检查解锁门槛 (total_explore_c >= requirement)
        unlock_cond = cfg.get("unlock_condition", [])
        if unlock_cond:
            req_c = unlock_cond[0][1]
            if tot_c < req_c:
                return AdminCatExploreConst.ERR_CONDITION_NOT_MET

        # 检查并扣除消耗材料
        consume = cfg.get("consume", [])
        if consume:
            cost_id, cost_num = consume[0][0], consume[0][1]
            _, bal = InventoryService.get_item_balance(db, uid, cost_id)
            if bal < cost_num:
                return AdminCatExploreConst.ERR_MATERIAL_NOT_ENOUGH
            InventoryService.cost_item(ctx, uid, cost_id, cost_num)

        # 解锁猫咪
        db.unlock_admin_cat(uid, mimir_id, skill_level=1)
        return AdminCatExploreConst.SUCCESS

    # ---------- 周末周常宝箱领取 (67008) ----------

    def claim_weekly_reward(self, ctx, uid: int) -> Tuple[int, List[dict]]:
        """
        领取周末周常累计大奖 (cs_67008)。
        """
        db = self._resolve_db(ctx)
        exp_data = self.get_or_create_explore_data(db, uid)
        weekly_time = exp_data.get("weekly_time", 0)
        reward_state = exp_data.get("weekly_reward_state", 0)

        if reward_state != 0 or weekly_time <= 0:
            return AdminCatExploreConst.ERR_ALREADY_CLAIMED, []

        level, _ = self.calculate_level(db, uid)
        lvl_cfg = EXPLORE_LEVEL_CFG.get(level, EXPLORE_LEVEL_CFG[1])
        base_rewards = lvl_cfg.get("accumulate_rewards", [])

        idx = min(len(WEEKLY_RATE_MAP) - 1, max(0, weekly_time))
        multiplier = WEEKLY_RATE_MAP[idx]

        reward_list = []
        for entry in base_rewards:
            item_id, count = entry[0], entry[1]
            total_num = count * multiplier
            reward_list.append({"id": item_id, "num": total_num})

        # 发放奖励
        InventoryService.grant_items(ctx, uid, [(r["id"], r["num"]) for r in reward_list], source="admin_cat_weekly_reward")

        # 标记已领取
        exp_data["weekly_reward_state"] = 1
        db.save_admin_cat_explore_data(uid, exp_data)

        return AdminCatExploreConst.SUCCESS, reward_list

    # ---------- 每周首次进入场景标记 (67012) ----------

    def set_weekly_scene_open(self, ctx, uid: int) -> int:
        """
        记录每周首次进入探索场景 (cs_67012)。
        """
        db = self._resolve_db(ctx)
        exp_data = self.get_or_create_explore_data(db, uid)
        exp_data["weekly_scene_open"] = 1
        db.save_admin_cat_explore_data(uid, exp_data)
        return AdminCatExploreConst.SUCCESS
