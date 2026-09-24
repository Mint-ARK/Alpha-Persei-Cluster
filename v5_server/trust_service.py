# -*- coding: utf-8 -*-
"""
trust_service.py — 角色好感度与信任度通用领域服务 (TrustService)
解耦自 operations.py，负责修正者好感度（Trust）、心情倍率、送礼经验、
等级突破（Lv.1 ~ Lv.5）以及人际关系网（RelationNet）基础养成。
全面接入配置驱动（trust_cfg.json）、27位白名单英雄门禁、出战心情动力学、每日5点跨天重置与事件总线联动。
"""

import os
import json
import math
import random
import time
import logging

from event_bus import bus, Events

logger = logging.getLogger("trust_service")

# 静态保底映射（当 trust_cfg.json 未就绪时启用）
FALLBACK_SUPPORTED_HEROES = {
    1011, 1019, 1022, 1028, 1033, 1039, 1041, 1048, 1049, 1050,
    1066, 1075, 1081, 1084, 1093, 1095, 1099, 1111, 1119, 1127,
    1133, 1139, 1148, 1150, 1184, 1199, 1284
}

FALLBACK_GIFT_EXP = {
    30012: 40,
    30013: 80,
    30014: 160,
    30015: 320,
}

FALLBACK_MOOD_RATE = {
    1: 1.0,
    2: 1.1,
    3: 1.3,
}

FALLBACK_TRUST_LEVEL_CAP = {
    1: 1500,
    2: 1700,
    3: 2300,
    4: 2500,
    5: 0,      # Lv.5 满级
}

FALLBACK_TRUST_LEVEL_REWARDS = {
    1: [{"id": 1, "num": 50}],
    2: [{"id": 1, "num": 80}],
    3: [{"id": 1, "num": 100}],
    4: [{"id": 1, "num": 800}],
    5: [],
}

# 模块级兼容常量导出
SUPPORTED_TRUST_HEROES = FALLBACK_SUPPORTED_HEROES
GIFT_EXP = FALLBACK_GIFT_EXP
MOOD_RATE = FALLBACK_MOOD_RATE
TRUST_LEVEL_CAP = FALLBACK_TRUST_LEVEL_CAP
TRUST_LEVEL_REWARDS = FALLBACK_TRUST_LEVEL_REWARDS


class TrustService:
    """好感度与人际关系网核心领域服务单例"""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._init_config()
            cls._instance._init_event_listeners()
        return cls._instance

    def __init__(self):
        self.supported_heroes = set(FALLBACK_SUPPORTED_HEROES)
        self.gift_exp = dict(FALLBACK_GIFT_EXP)
        self.mood_rates = dict(FALLBACK_MOOD_RATE)
        self.trust_level_caps = dict(FALLBACK_TRUST_LEVEL_CAP)
        self.hero_trust_cfg = {}
        self.relation_net_cfg = {}
        self.relation_upgrade_cfg = {}
        self.relation_upgrade_condition_cfg = {}
        self.relation_story_cfg = {}
        self.weapon_exp_cfg = {}
        # 连携技能（协作技能网 p73）与礼物置换限次配置
        self.combo_skill_cfg = {}        # {id: {id, skill_id, max_level, cooperate_role_ids}}
        self.combo_skill_level_cfg = {}  # {id: {id, condition_type, index, target, level}}
        self.hero_ultimate_skill = {}    # {hero_id: 奥义技能id}（type=1 条件实时校验）
        self.trust_displace_limit = {}   # {item_id: 每日限次}
        self._intimate_index = None      # 「任一修正者」反查索引（懒加载）

    def _init_config(self):
        """加载 trust_cfg.json 配置字典"""
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trust_cfg.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
                if cfg_data.get("supported_heroes"):
                    self.supported_heroes = set(int(x) for x in cfg_data["supported_heroes"])
                if cfg_data.get("gift_exp"):
                    self.gift_exp = {int(k): int(v) for k, v in cfg_data["gift_exp"].items()}
                if cfg_data.get("mood_rates"):
                    self.mood_rates = {int(k): float(v) for k, v in cfg_data["mood_rates"].items()}
                if cfg_data.get("trust_level_caps"):
                    self.trust_level_caps = {int(k): int(v) for k, v in cfg_data["trust_level_caps"].items()}
                self.hero_trust_cfg = cfg_data.get("hero_trust_cfg", {})
                self.relation_net_cfg = cfg_data.get("relation_net_cfg", {})
                self.relation_upgrade_cfg = cfg_data.get("relation_upgrade_cfg", {})
                self.relation_upgrade_condition_cfg = cfg_data.get("relation_upgrade_condition_cfg", {})
                self.relation_story_cfg = cfg_data.get("relation_story_cfg", {})
                if cfg_data.get("weapon_exp_cfg"):
                    self.weapon_exp_cfg = {int(k): int(v) for k, v in cfg_data["weapon_exp_cfg"].items()}
                if cfg_data.get("combo_skill_cfg"):
                    self.combo_skill_cfg = {int(k): v for k, v in cfg_data["combo_skill_cfg"].items()}
                if cfg_data.get("combo_skill_level_cfg"):
                    self.combo_skill_level_cfg = {int(k): v for k, v in cfg_data["combo_skill_level_cfg"].items()}
                if cfg_data.get("hero_ultimate_skill"):
                    self.hero_ultimate_skill = {int(k): int(v) for k, v in cfg_data["hero_ultimate_skill"].items()}
                if cfg_data.get("trust_displace_limit"):
                    self.trust_displace_limit = {int(k): int(v) for k, v in cfg_data["trust_displace_limit"].items()}
                logger.info(f"[TrustService] 配置装载成功: {len(self.supported_heroes)}位白名单英雄, "
                            f"{len(self.gift_exp)}种礼物, {len(self.relation_net_cfg)}个关系网节点, "
                            f"{len(self.relation_upgrade_cfg)}个增益, {len(self.relation_story_cfg)}个羁绊故事, "
                            f"{len(self.combo_skill_cfg)}条连携技能")
            except Exception as e:
                logger.warning(f"[TrustService] 加载 trust_cfg.json 失败，启用静态保底: {e}")

    def _weapon_exp_to_level(self, exp):
        """权钥经验转换为权钥等级 (1~80)"""
        if not self.weapon_exp_cfg:
            if exp >= 199800: return 80
            if exp >= 99800: return 60
            if exp >= 19800: return 40
            if exp >= 4800: return 20
            return 1
        for lv in range(80, 0, -1):
            if self.weapon_exp_cfg.get(lv, 0) <= exp:
                return lv
        return 1

    def is_hero_supported(self, hero_id):
        """白名单检查：是否属于官方支持二阶深度交心的 27 位角色"""
        return int(hero_id) in self.supported_heroes

    def get_trust_info(self, db, uid, hero_id):
        """查询指定角色的好感度状态"""
        rows = db.query(
            "SELECT id, trust_level, trust_exp, trust_mood, relation_net FROM hero WHERE uid=? AND id=?",
            (uid, hero_id)
        )
        if not rows:
            return None
        r = rows[0]
        return {
            "hero_id": int(r["id"]),
            "trust_level": int(r["trust_level"] or 0),
            "trust_exp": int(r["trust_exp"] or 0),
            "trust_mood": int(r["trust_mood"] or 1),
            "relation_net": r.get("relation_net") or "[]",
        }

    def unlock_trust(self, ctx, uid, hero_id):
        """73020: 解锁好感度/心链 (带白名单门禁与心情初始化)"""
        hero_id = int(hero_id)
        if not self.is_hero_supported(hero_id):
            raise ValueError(f"角色 {hero_id} 不在二阶好感交心受支持列表中")

        # 检查是否已解锁
        t_info = self.get_trust_info(ctx.db, uid, hero_id)
        if t_info and t_info["trust_level"] > 0:
            return {"hero_id": hero_id, "mood": t_info["trust_mood"], "trust_level": t_info["trust_level"]}

        mood = random.choice([1, 2, 3])
        ctx.db.execute(
            "UPDATE hero SET trust_level=1, trust_exp=0, trust_mood=? WHERE uid=? AND id=?",
            (mood, uid, hero_id)
        )
        bus.emit(Events.TRUST_MOOD_CHANGE, ctx, uid, hero_id=hero_id, old_mood=0, new_mood=mood, reason="unlock")
        return {"hero_id": hero_id, "mood": mood, "trust_level": 1}

    def send_trust_item(self, ctx, uid, hero_id, item_list):
        """73012: 赠送好感度礼物（截断式按需扣减，永不因经验满额拦截请求）

        官方公式：单件经验 = math.floor(base_exp * mood_rate)，总量截断于阶段上限。
        批量拉满送礼时只消耗能吃下的数量（剩余空间 // 单件经验），礼物不浪费、
        请求恒回成功——客户端批量进度条/礼物列表渲染不因 result!=0 的拦截而状态错乱。
        """
        from operations import _item_deduct

        hero_id = int(hero_id)
        if not self.is_hero_supported(hero_id):
            raise ValueError(f"角色 {hero_id} 不在二阶好感交心受支持列表中")

        t_info = self.get_trust_info(ctx.db, uid, hero_id)
        if not t_info or (t_info["trust_level"] or 0) < 1:
            raise ValueError(f"角色 {hero_id} 尚未解锁交心")

        trust_lvl = t_info["trust_level"]
        cur_exp = t_info["trust_exp"]
        mood = t_info["trust_mood"] or 1
        rate = self.mood_rates.get(mood, 1.0)
        cap = self.trust_level_caps.get(trust_lvl, 1500)
        # 剩余可吃经验空间（cap=0 表示该阶段无上限）
        remaining = (cap - cur_exp) if cap > 0 else None

        total_exp = 0
        for it in item_list:
            iid = int(it.get("id") or it.get("item_id") or 0)
            num = int(it.get("num") or it.get("item_num") or 0)
            if not (iid and num > 0):
                continue
            # 喜好礼物（50001~50066）基准 20，通用礼物（30012~30015）40~320
            base_exp = self.gift_exp.get(iid, 20)
            single_exp = math.floor(base_exp * rate)
            if single_exp <= 0:
                continue
            # 按剩余空间截断实际消耗数量：有空间时至少吃 1 件（溢出部分截断，与官方
            # min(cur+total, cap) 语义一致），空间耗尽后一件不吃
            if remaining is not None:
                if remaining <= 0:
                    use_n = 0
                else:
                    use_n = min(num, max(1, remaining // single_exp))
            else:
                use_n = num
            if use_n <= 0:
                continue
            _item_deduct(ctx, uid, iid, use_n)
            total_exp += single_exp * use_n
            if remaining is not None:
                remaining -= single_exp * use_n

        # 经验截断于阶段上限（好感度升级必须玩家手动发起，绝不自动升级突破）
        new_exp = min(cur_exp + total_exp, cap) if cap > 0 else (cur_exp + total_exp)
        ctx.db.execute(
            "UPDATE hero SET trust_exp=? WHERE uid=? AND id=?",
            (new_exp, uid, hero_id)
        )
        return {
            "hero_id": hero_id,
            "exp_added": total_exp,
            "mood": mood,
            "rate": rate,
            "new_exp": new_exp,
            "trust_level": trust_lvl
        }

    def upgrade_trust_level(self, ctx, uid, hero_id):
        """73014: 好感度等级手动突破升级 (Lv.1 -> Lv.5)，校验经验满额后发放专属奖励与总线广播"""
        from operations import _item_add

        hero_id = int(hero_id)
        if not self.is_hero_supported(hero_id):
            raise ValueError(f"角色 {hero_id} 不在二阶好感交心受支持列表中")

        t_info = self.get_trust_info(ctx.db, uid, hero_id)
        cur_lvl = t_info["trust_level"] if t_info else 0
        if cur_lvl >= 5:
            return {"hero_id": hero_id, "rewards": [], "new_level": 5}

        cur_exp = t_info["trust_exp"] if t_info else 0
        cap = self.trust_level_caps.get(cur_lvl, 1500)
        # 严格校验经验是否已达到当前阶段上限
        if cap > 0 and cur_exp < cap:
            raise ValueError(f"角色 {hero_id} 好感度经验未达上限({cur_exp}/{cap})，无法突破")

        new_lvl = cur_lvl + 1
        ctx.db.execute(
            "UPDATE hero SET trust_level=?, trust_exp=0 WHERE uid=? AND id=?",
            (new_lvl, uid, hero_id)
        )

        # 读取官方 HeroTrustCfg 配置奖励（key 为 cur_lvl，即完成该阶段突破获得的专属奖励）
        h_cfg = self.hero_trust_cfg.get(str(hero_id), {}).get(str(cur_lvl), {})
        rewards = list(h_cfg.get("reward_item_list") or [])
        if not rewards:
            rewards = list(FALLBACK_TRUST_LEVEL_REWARDS.get(cur_lvl, [{"id": 1, "num": 50}]))

        for r in rewards:
            iid = int(r["id"])
            inum = int(r["num"])
            _item_add(ctx, uid, iid, inum)
            if iid >= 950000:
                try:
                    ctx.db.execute(
                        "INSERT INTO backhome_furniture (uid, furniture_id, num, give_num, update_ts) VALUES (?, ?, ?, 0, 0) "
                        "ON CONFLICT(uid, furniture_id) DO UPDATE SET num = num + excluded.num",
                        (uid, iid, inum)
                    )
                except Exception:
                    pass

        bus.emit(Events.TRUST_LEVEL_UP, ctx, uid, hero_id=hero_id, new_level=new_lvl, rewards=rewards)

        return {
            "hero_id": hero_id,
            "rewards": rewards,
            "new_level": new_lvl
        }

    def check_relation_condition(self, db, uid, condition_id, intimate_list):
        """查库校验关系网增益解锁条件（严格遵循官方 ArchiveTools '任一修正者(Any)' 逻辑门规则）
        Type 1: 修正者等级 >= arg[1]
        Type 2: 技能总等级 >= arg[1]
        Type 3: 权钥等级 >= arg[1]
        Type 4: 修正者星级 >= arg[1] (300, 400, 500, 600)
        Type 5: 修正者熟练度 (clear_times) >= arg[1]
        Type 6: 修正者跃迁总点数 (exclusive_skill_list talent_points) >= arg[1]
        """
        cond_cfg = self.relation_upgrade_condition_cfg.get(str(condition_id))
        if not cond_cfg:
            return False

        ctype = int(cond_cfg.get("type", 0))
        args = cond_cfg.get("arg", [])
        if not args:
            return False
        req_val = int(args[0])

        if not intimate_list:
            return False

        # 批量查库获取相关角色实时数据
        placeholders = ",".join("?" for _ in intimate_list)
        rows = db.query(
            f"SELECT id, level, star, skill_list, weapon_exp, clear_times, unlock, exclusive_skill_list "
            f"FROM hero WHERE uid=? AND id IN ({placeholders})",
            [uid] + [int(x) for x in intimate_list]
        )
        hero_map = {int(r["id"]): dict(r) for r in rows}

        for hid in intimate_list:
            hid = int(hid)
            h = hero_map.get(hid)
            if not h or int(h.get("unlock") or 0) != 1:
                continue

            if ctype == 1:
                # 修正者等级
                if int(h.get("level") or 0) >= req_val:
                    return True
            elif ctype == 2:
                # 技能总等级
                s_list = h.get("skill_list")
                if isinstance(s_list, str):
                    try:
                        s_list = json.loads(s_list)
                    except Exception:
                        s_list = []
                total_skill = 0
                if isinstance(s_list, list):
                    for sk in s_list:
                        if isinstance(sk, (list, tuple)) and len(sk) >= 2:
                            total_skill += int(sk[1] or 0)
                        elif isinstance(sk, dict):
                            total_skill += int(sk.get("level") or sk.get("skill_level") or 0)
                if total_skill >= req_val:
                    return True
            elif ctype == 3:
                # 权钥等级
                w_exp = int(h.get("weapon_exp") or 0)
                w_lvl = self._weapon_exp_to_level(w_exp)
                if w_lvl >= req_val:
                    return True
            elif ctype == 4:
                # 修正者星级 (300=3星, 400=4星, 500=5星, 600=Ω)
                if int(h.get("star") or 0) >= req_val:
                    return True
            elif ctype == 5:
                # 修正者熟练度 (clear_times 出战胜场)
                if int(h.get("clear_times") or 0) >= req_val:
                    return True
            elif ctype == 6:
                # 跃迁总点数 (exclusive_skill_list 各槽位 talent_points 累加)
                from hero_codec import normalize_exclusive_skills
                norm = normalize_exclusive_skills(h.get("exclusive_skill_list"))
                total_pts = sum(int(v.get("talent_points") or 0) for v in norm.values())
                if total_pts >= req_val:
                    return True

        return False

    def unlock_relation_net(self, ctx, uid, node_id, group_index):
        """73016: 解锁人际关系网节点/增益任务 (三级逻辑门防护：阶级门禁 + 查库条件门禁 + 防重幂等)"""
        node_id = int(node_id)
        group_index = int(group_index)

        # 1. 优先从 relation_net_cfg 反查真实 hero_id, tier(index), intimate, relation_upgrade_group
        net_node = self.relation_net_cfg.get(str(node_id))
        if net_node:
            hero_id = int(net_node["hero_id"])
            tier = int(net_node["index"])
            intimate = list(net_node.get("intimate") or [hero_id])
            upgrade_group = list(net_node.get("relation_upgrade_group") or [])
        elif node_id > 10000:
            hero_id = node_id // 100
            tier = node_id % 100
            intimate = [hero_id]
            upgrade_group = []
        else:
            hero_id = 1119
            tier = 1
            intimate = [hero_id]
            upgrade_group = []

        # 门禁 1: 阶级好感度门禁 (tier 节点要求好感等级 trust_level >= tier)
        t_info = self.get_trust_info(ctx.db, uid, hero_id)
        cur_t_lvl = t_info["trust_level"] if t_info else 0
        if cur_t_lvl < tier:
            raise ValueError(f"角色 {hero_id} 好感度等级不足 (当前 Lv.{cur_t_lvl}, 解锁需要 Lv.{tier})")

        # 门禁 1.5: group_index 合法性门禁（越界值严禁落库——客户端以 Lua 1-based
        # 数组下标索引 relation_upgrade_group，0/越界值会让角色列表页 GetRelationNetAttr
        # 触发 attempt to index a nil value 全页崩溃）
        if net_node and not (1 <= group_index <= len(upgrade_group)):
            raise ValueError(f"非法 group_index={group_index} (节点 {node_id} 合法范围 1~{len(upgrade_group)})")

        rows = ctx.db.query("SELECT relation_net FROM hero WHERE uid=? AND id=?", (uid, hero_id))
        rel_net = []
        if rows and rows[0]["relation_net"]:
            try:
                rel_net = json.loads(rows[0]["relation_net"])
            except Exception:
                rel_net = []

        tier_data = next((t for t in rel_net if t.get("tier") == tier), None)

        # 门禁 2: 防重/幂等门禁
        if tier_data and group_index in tier_data.get("upgrade_complete_list", []):
            return {"hero_id": hero_id, "tier": tier, "group_index": group_index, "node_id": node_id, "already": True}

        # 门禁 3: 查库条件门禁 (校验升级增益任务是否已达成；配置缺失属数据异常 → 拦截不放行)
        if upgrade_group and 1 <= group_index <= len(upgrade_group):
            upg_id = upgrade_group[group_index - 1]
            upg_cfg = self.relation_upgrade_cfg.get(str(upg_id))
            cond_id = (upg_cfg or {}).get("condition_id") if upg_cfg else None
            if not cond_id:
                raise ValueError(f"关系网增益配置缺失: node={node_id} upgrade={upg_id}（拒绝静默放行）")
            ok = self.check_relation_condition(ctx.db, uid, cond_id, intimate)
            if not ok:
                raise ValueError(f"未达成关系网增益解锁条件: cond_id={cond_id}, node_id={node_id}, group_index={group_index}")

        # 条件满足，写库落盘
        if not tier_data:
            tier_data = {"tier": tier, "upgrade_complete_list": []}
            rel_net.append(tier_data)
        if group_index not in tier_data["upgrade_complete_list"]:
            tier_data["upgrade_complete_list"].append(group_index)

        ctx.db.execute(
            "UPDATE hero SET relation_net=? WHERE uid=? AND id=?",
            (json.dumps(rel_net), uid, hero_id)
        )

        bus.emit(Events.RELATION_NET_UNLOCK, ctx, uid, hero_id=hero_id, node_id=node_id, tier=tier, group_index=group_index)

        return {"hero_id": hero_id, "tier": tier, "group_index": group_index, "node_id": node_id, "already": False}

    def exchange_trust_item(self, ctx, uid, use_item_list, reward_item_list):
        """73010: 置换好感度礼物（客户端每日限次 hero_trust_exchange_up_limit 的服务端强制版）

        限次按「目标礼物 item_id」当日累计（与客户端 UpdateTrustGiftDisplaceCount 同口径），
        超限拒绝（客户端对应 ShowTips HERO_TRUST_DISPLACE_UP_LIMIT），防重登绕过限次。
        """
        from operations import _item_deduct, _item_add

        # 门禁: 每日限次校验（仅配置了限次的礼物参与判定）
        day_key = self._displace_day_key()
        used_map = self._get_displace_used(ctx.db, uid, day_key)
        for it in reward_item_list:
            iid = int(it.get("id") or 0)
            num = int(it.get("num") or 0)
            limit = self.trust_displace_limit.get(iid)
            if iid and num > 0 and limit is not None:
                cur = used_map.get(iid, 0)
                if cur + num > limit:
                    raise ValueError(f"礼物 {iid} 今日置换次数已达上限({cur}/{limit})，次日 5 点刷新")

        for it in use_item_list:
            iid = int(it.get("id") or 0)
            num = int(it.get("num") or 0)
            if iid and num:
                _item_deduct(ctx, uid, iid, num)
        for it in reward_item_list:
            iid = int(it.get("id") or 0)
            num = int(it.get("num") or 0)
            if iid and num:
                _item_add(ctx, uid, iid, num)
                # 限次礼物累计当日已用量
                if self.trust_displace_limit.get(iid) is not None:
                    ctx.db.execute(
                        "INSERT INTO trust_displace_counter (uid, item_id, day_key, use) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(uid, item_id, day_key) DO UPDATE SET use = use + excluded.use",
                        (uid, iid, day_key, num)
                    )
        return {}

    def _displace_day_key(self, now_ts=None):
        """每日 05:00 跨天日键（与全局 DAILY_RESET_5AM 周期同边界）"""
        ts = int(now_ts if now_ts is not None else time.time())
        return time.strftime("%Y%m%d", time.localtime(ts - 5 * 3600))

    def _get_displace_used(self, db, uid, day_key):
        """查询当日各礼物已置换数量 {item_id: use}"""
        used = {}
        if db is None:
            return used
        rows = db.query(
            "SELECT item_id, use FROM trust_displace_counter WHERE uid=? AND day_key=?",
            (uid, day_key)
        )
        for r in rows:
            used[int(r["item_id"])] = int(r["use"] or 0)
        return used

    def get_displace_limit_payload(self, db, uid):
        """sc_73005 登录推送载荷: {limit_list: [{item_id, use}]}（仅回传有限次的礼物当日已用量）"""
        used = self._get_displace_used(db, uid, self._displace_day_key())
        limit_list = [
            {"item_id": iid, "use": used.get(iid, 0)}
            for iid in sorted(self.trust_displace_limit.keys())
        ]
        return {"limit_list": limit_list}

    # ==================================================================
    #                      连携技能（协作技能网 p73）
    # ==================================================================

    def _get_combo_state(self, db, uid, skill_id):
        """读取连携技能状态 → (level, progress{condition_id: times})，无记录=1级零进度"""
        rows = db.query(
            "SELECT level, progress_json FROM combo_skill_state WHERE uid=? AND skill_id=?",
            (uid, int(skill_id))
        )
        if not rows:
            return 1, {}
        try:
            prog = json.loads(rows[0]["progress_json"] or "{}")
        except Exception:
            prog = {}
        return int(rows[0]["level"] or 1), {int(k): int(v) for k, v in (prog or {}).items()}

    def get_combo_skill_payload(self, db, uid):
        """sc_73001 登录推送载荷: {info: {skill_list: [{id, level, progress_list: [{id, times}]}]}}
        全量连携；无记录默认 1 级零进度（与客户端 GetCurComboSkillLevel 默认值一致）"""
        skill_list = []
        for sid in sorted(self.combo_skill_cfg.keys()):
            level, prog = self._get_combo_state(db, uid, sid)
            skill_list.append({
                "id": sid,
                "level": level,
                "progress_list": [{"id": int(cid), "times": int(t)} for cid, t in sorted(prog.items()) if t > 0],
            })
        return {"info": {"skill_list": skill_list}}

    def build_combo_skill_rec(self, db, uid, skill_id):
        """构造单条 cooperate_skill_net_rec（sc_73003 更新帧载荷体）"""
        level, prog = self._get_combo_state(db, uid, skill_id)
        return {
            "id": int(skill_id),
            "level": level,
            "progress_list": [{"id": int(cid), "times": int(t)} for cid, t in sorted(prog.items()) if t > 0],
        }

    def _get_hero_ult_level(self, db, uid, hero_id):
        """查询修正者奥义技能等级（skill_list 中奥义技能 id 的等级，不含跃迁加成，官方 type=1 口径）"""
        ult_id = self.hero_ultimate_skill.get(int(hero_id))
        if not ult_id:
            return 0
        rows = db.query("SELECT skill_list FROM hero WHERE uid=? AND id=?", (uid, int(hero_id)))
        if not rows:
            return 0
        try:
            s_list = json.loads(rows[0]["skill_list"] or "[]")
        except Exception:
            s_list = []
        for sk in s_list:
            if isinstance(sk, (list, tuple)) and len(sk) >= 2 and int(sk[0]) == int(ult_id):
                return int(sk[1] or 0)
            if isinstance(sk, dict) and int(sk.get("skill_id") or 0) == int(ult_id):
                return int(sk.get("skill_level") or 0)
        return 0

    def _check_combo_conditions(self, db, uid, skill_id, cur_level):
        """校验从 cur_level 升级到 cur_level+1 的全部条件（该等级组任一不满足即拦截）

        condition_type 1 = 实时校验: 合作修正者奥义等级最小值 >= target（官方 CheckComboSkillUpContion 口径）
        condition_type 2/3/4/5 = 进度计数: progress[condition_id] >= target（服务端落库驱动）
        """
        cond_rows = [r for r in self.combo_skill_level_cfg.values() if int(r.get("level", -1)) == int(cur_level)]
        if not cond_rows:
            return True, ""

        combo = self.combo_skill_cfg.get(int(skill_id))
        roles = list((combo or {}).get("cooperate_role_ids") or [])
        _, prog = self._get_combo_state(db, uid, skill_id)

        for r in sorted(cond_rows, key=lambda x: x.get("index", 0)):
            ctype = int(r["condition_type"])
            target = int(r["target"])
            if ctype == 1:
                # 官方取合作英雄奥义等级最小值（均达到）
                min_ult = min((self._get_hero_ult_level(db, uid, h) for h in roles), default=0)
                if min_ult < target:
                    return False, f"连携修正者奥义等级需均达到 {target} 级（当前最低 {min_ult}）"
            else:
                times = int(prog.get(int(r["id"]), 0))
                if times < target:
                    return False, f"升级条件未达成: {r.get('des', r['id'])} ({times}/{target})"
        return True, ""

    def upgrade_combo_skill(self, ctx, uid, skill_id):
        """73018: 连携技能升级（等级 1~maxLevel，全部条件满足才放行），成功后由调用方推 sc_73003"""
        skill_id = int(skill_id)
        combo = self.combo_skill_cfg.get(skill_id)
        if not combo:
            raise ValueError(f"未知连携技能 id={skill_id}")

        level, prog = self._get_combo_state(ctx.db, uid, skill_id)
        max_lv = int(combo.get("max_level") or 3)
        if level >= max_lv:
            raise ValueError(f"连携技能 {skill_id} 已达满级 Lv.{max_lv}")

        ok, reason = self._check_combo_conditions(ctx.db, uid, skill_id, level)
        if not ok:
            raise ValueError(reason)

        new_level = level + 1
        now = int(time.time())
        ctx.db.execute(
            "INSERT INTO combo_skill_state (uid, skill_id, level, progress_json, update_ts) VALUES (?, ?, ?, '{}', ?) "
            "ON CONFLICT(uid, skill_id) DO UPDATE SET level=?, update_ts=?",
            (uid, skill_id, new_level, now, new_level, now)
        )
        bus.emit(Events.COMBO_SKILL_LEVEL_UP, ctx, uid, skill_id=skill_id, new_level=new_level)
        return {"skill_id": skill_id, "new_level": new_level}

    def record_combo_progress(self, ctx, uid, skill_id, condition_id, delta=1):
        """连携升级进度计数器累加（战斗结算钩子调用：type2 通关/type3 梦境魇渊/type4 黑区/type5 协作）"""
        skill_id = int(skill_id)
        _, prog = self._get_combo_state(ctx.db, uid, skill_id)
        cid = int(condition_id)
        prog[cid] = int(prog.get(cid, 0)) + int(delta)
        now = int(time.time())
        ctx.db.execute(
            "INSERT INTO combo_skill_state (uid, skill_id, level, progress_json, update_ts) VALUES (?, ?, 1, ?, ?) "
            "ON CONFLICT(uid, skill_id) DO UPDATE SET progress_json=?, update_ts=?",
            (uid, skill_id, json.dumps(prog), now, json.dumps(prog), now)
        )
        return prog[cid]

    def record_combo_stage_clear(self, ctx, uid, combo_skill_id, stage_kind="normal"):
        """通关进度钩子（结算链调用）：按关卡类别推进对应条件计数
        stage_kind: normal=任意关卡(type2) / dream_boss=梦境魇渊Boss(type3) / mythic_deep=黑区净化高难(type4)
        依据 ComboSkillLevelCfg 中该连携涉及的条件行自动精确推进"""
        cond_rows = [r for r in self.combo_skill_level_cfg.values()
                     if int(r.get("level", -1)) >= 1 and int(r["condition_type"]) in (2, 3, 4)]
        kind_map = {3: "dream_boss", 4: "mythic_deep"}
        for r in cond_rows:
            ctype = int(r["condition_type"])
            if ctype == 2 or kind_map.get(ctype) == stage_kind:
                self.record_combo_progress(ctx, uid, int(combo_skill_id), int(r["id"]), 1)

    def claim_relation_story_reward(self, ctx, uid, story_id):
        """73022: 领取关系网羁绊故事奖励（防刷）"""
        from operations import _item_add

        story_id = int(story_id)
        if not ctx.db.mark_claimed(uid, "relation_story", story_id):
            return {"id": story_id, "already": True, "rewards": []}

        # 查找配表奖励，若无则保底发放 50 移转之辉
        rewards = [{"id": 1, "num": 50}]
        s_cfg = self.relation_story_cfg.get(str(story_id))
        if s_cfg and s_cfg.get("story_reward"):
            rewards = [{"id": int(r[0]), "num": int(r[1])} for r in s_cfg["story_reward"] if len(r) >= 2]

        for r in rewards:
            _item_add(ctx, uid, r["id"], r["num"])
        return {"id": story_id, "already": False, "rewards": rewards}

    def _build_intimate_index(self):
        """构建「任一修正者」反查索引（懒加载缓存）:
        {intimate_hero_id: [(main_hero_id, tier, group_index, cond_id, intimate_list), ...]}

        语义：养成广播命中某修正者时，反查它作为条件成员参与的所有关系网增益，
        供 on_hero_upgrade 三级门禁复用（白名单 → 好感等级 → 查库已完成丢弃/条件重估）。
        """
        idx = {}
        for nid_str, node in self.relation_net_cfg.items():
            main = int(node.get("hero_id", 0))
            tier = int(node.get("index", 0))
            intimate = [int(x) for x in (node.get("intimate") or [])]
            for gi, upg_id in enumerate(node.get("relation_upgrade_group") or [], 1):
                cond_id = (self.relation_upgrade_cfg.get(str(upg_id)) or {}).get("condition_id")
                if not cond_id:
                    continue
                for ih in intimate:
                    idx.setdefault(int(ih), []).append((main, tier, gi, int(cond_id), intimate))
        return idx

    def evaluate_relation_conditions_for_hero(self, ctx, uid, hero_id):
        """养成广播后的关系网增益条件重估：
        遍历 hero_id 参与的全部未解锁增益，条件刚达成者向 ctx 追加 sc_14007 原子帧
        （客户端 HeroData.trust.relation 实时刷新 → 红点即时亮起，无需重登）。
        返回推送的帧数。"""
        db = getattr(ctx, "db", None)
        if db is None:
            return 0
        if self._intimate_index is None:
            self._intimate_index = self._build_intimate_index()
        entries = self._intimate_index.get(int(hero_id))
        if not entries:
            return 0

        pushed = 0
        seen_main = set()
        for (main_hero, tier, gi, cond_id, intimate) in entries:
            # 门禁 1: 主英雄白名单（关系网体系仅 27 位二阶好感角色参与）
            if not self.is_hero_supported(main_hero):
                continue
            # 门禁 2: 主英雄好感等级 >= tier（未达 tier 客户端不展示该节点）
            t_info = self.get_trust_info(db, uid, main_hero)
            if not t_info or (t_info["trust_level"] or 0) < tier:
                continue
            # 门禁 3: 查库已完成 → 丢弃广播；未完成 → 条件达成即推原子帧
            rows = db.query("SELECT relation_net FROM hero WHERE uid=? AND id=?", (uid, main_hero))
            rel_net = []
            if rows and rows[0]["relation_net"]:
                try:
                    rel_net = json.loads(rows[0]["relation_net"])
                except Exception:
                    rel_net = []
            tier_data = next((t for t in rel_net if t.get("tier") == tier), None)
            if tier_data and gi in tier_data.get("upgrade_complete_list", []):
                continue
            ok = self.check_relation_condition(db, uid, cond_id, intimate)
            if not ok:
                continue
            if main_hero not in seen_main:
                seen_main.add(main_hero)
                try:
                    import hero_codec as _hc
                    hf = _hc.build_hero_14007_frame(db, uid, int(main_hero))
                    if hf and hasattr(ctx, "append_frame"):
                        ctx.append_frame(hf)
                        pushed += 1
                        logger.info(f"[TrustService] 关系网增益条件达成: hero={main_hero} tier={tier} group={gi} cond={cond_id} → 推 sc_14007")
                except Exception as e:
                    logger.warning(f"[TrustService] 推送关系网原子帧失败 hero={main_hero}: {e}")
        return pushed

    def set_trust(self, db, uid, hero_id, level=5, exp=0, mood=1):
        """GM/调试辅助：直接设置好感度等级与心情"""
        db.execute(
            "UPDATE hero SET trust_level=?, trust_exp=?, trust_mood=? WHERE uid=? AND id=?",
            (int(level), int(exp), int(mood), uid, int(hero_id))
        )
        return {"hero_id": hero_id, "trust_level": level, "trust_exp": exp, "trust_mood": mood}

    def _init_event_listeners(self):
        """挂载全局事件总线监听器：出战心情动力学与每日5点跨天刷新"""

        @bus.subscribe(Events.STAGE_PASS)
        def on_stage_pass(ctx, uid, **kwargs):
            """监听战斗胜利通关：白名单二阶好感英雄出战胜场累计驱动心情升级
            （连携技能通关进度已迁移至 COMBO_SKILL_PROGRESS 订阅，由 cooperation_skill_server 计量广播驱动）"""
            heroes = kwargs.get("heroes") or []
            times = max(1, int(kwargs.get("times", 1)))
            db = getattr(ctx, "db", None)
            if not db or not heroes:
                return

            now_ts = int(time.time())
            for h in heroes:
                hid = int(h.get("id") if isinstance(h, dict) else h)
                # 门禁 1: 0 纳秒静默丢弃非白名单角色
                if not self.is_hero_supported(hid):
                    continue

                # 门禁 2: 必须已解锁交心 (trust_level >= 1)
                t_info = self.get_trust_info(db, uid, hid)
                if not t_info or (t_info["trust_level"] or 0) < 1:
                    continue

                # 累加出战胜场统计到 user_behavior_stat
                try:
                    db.execute(
                        """
                        INSERT INTO user_behavior_stat (uid, stat_key, hero_id, value, update_ts)
                        VALUES (?, 'trust_battle_wins', ?, ?, ?)
                        ON CONFLICT(uid, stat_key, hero_id) DO UPDATE SET
                            value = value + ?, update_ts = ?
                        """,
                        (uid, hid, times, now_ts, times, now_ts)
                    )
                except Exception:
                    pass

                # 心情动力学：根据当日胜场提升心情（上限 3 开心）
                cur_mood = t_info["trust_mood"] or 1
                if cur_mood < 3:
                    stat_rows = db.query(
                        "SELECT value FROM user_behavior_stat WHERE uid=? AND stat_key='trust_battle_wins' AND hero_id=?",
                        (uid, hid)
                    )
                    wins = int(stat_rows[0]["value"]) if stat_rows else 0
                    new_mood = cur_mood
                    if cur_mood == 1 and wins >= 5:
                        new_mood = 2
                    elif cur_mood == 2 and wins >= 10:
                        new_mood = 3

                    if new_mood > cur_mood:
                        db.execute("UPDATE hero SET trust_mood=? WHERE uid=? AND id=?", (new_mood, uid, hid))
                        bus.emit(Events.TRUST_MOOD_CHANGE, ctx, uid, hero_id=hid, old_mood=cur_mood, new_mood=new_mood, reason="battle_win")
                        logger.info(f"[TrustService] 英雄 {hid} 胜场达成({wins})，心情提升: {cur_mood} -> {new_mood}")

        @bus.subscribe(Events.COMBO_SKILL_PROGRESS)
        def on_combo_skill_progress(ctx, uid, **kwargs):
            """监听连携出场计量变动广播（cooperation_skill_server 发出）：
            依据变动连携 ID 与关卡类别推进 p73 连携技能升级进度（进度/红点管理）"""
            combo_id = kwargs.get("combo_id") or 0
            if not combo_id:
                return
            try:
                kind = kwargs.get("combo_stage_kind") or "normal"
                self.record_combo_stage_clear(ctx, uid, int(combo_id), stage_kind=kind)
                logger.info(f"[TrustService] 连携 {combo_id} 计量变动 → 升级进度+1 (kind={kind})")
            except Exception as e:
                logger.warning(f"[TrustService] 连携进度推进异常 combo={combo_id}: {e}")

        @bus.subscribe(Events.DAILY_RESET_5AM)
        def on_daily_reset_5am(ctx, uid, **kwargs):
            """监听每日凌晨 5:00 跨天重置：刷新所有已交心角色的当日心情与胜场统计"""
            db = getattr(ctx, "db", None)
            if not db:
                return

            for hid in self.supported_heroes:
                t_info = self.get_trust_info(db, uid, hid)
                if t_info and (t_info["trust_level"] or 0) >= 1:
                    old_mood = t_info["trust_mood"] or 1
                    new_mood = random.choice([1, 2, 3])
                    db.execute("UPDATE hero SET trust_mood=? WHERE uid=? AND id=?", (new_mood, uid, hid))
                    try:
                        db.execute(
                            "UPDATE user_behavior_stat SET value=0 WHERE uid=? AND stat_key='trust_battle_wins' AND hero_id=?",
                            (uid, hid)
                        )
                    except Exception:
                        pass
                    bus.emit(Events.TRUST_MOOD_CHANGE, ctx, uid, hero_id=hid, old_mood=old_mood, new_mood=new_mood, reason="daily_reset_5am")

        @bus.subscribe(Events.HERO_UPGRADE)
        def on_hero_upgrade(ctx, uid, **kwargs):
            """监听 HERO 模块角色养成广播 (level_up/star_up/skill_upgrade/weapon_upgrade/
            transition_upgrade/proficiency_up)：
            该修正者作为「任一修正者」成员参与的关系网增益条件重估——
            已完成的丢弃广播，未完成的在条件达成时向下推送 sc_14007 原子帧。
            """
            hid = kwargs.get("hero_id")
            if hid:
                try:
                    self.evaluate_relation_conditions_for_hero(ctx, uid, int(hid))
                except Exception as e:
                    logger.warning(f"[TrustService] HERO_UPGRADE 关系网重估异常: {e}")
            oper = kwargs.get("oper", "unknown")
            logger.debug(f"[TrustService] 收到 HERO_UPGRADE 广播: uid={uid}, hero_id={hid}, oper={oper}")


