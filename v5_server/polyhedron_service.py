# -*- coding: utf-8 -*-
"""
polyhedron_service.py — 多维变量（Polyhedron / 矩阵）局内探索推进引擎与状态机

职责：
- 局内对局完整生命周期管理（开局、选门、战斗、战后3选1珍宝、商店、招募、编队、结算）
- 1-1 到 3-10 关卡树推进与大门随机生成（严格匹配官方 Tier 1/2/3 专属事件 ID 与地图）
- 14大信标BUFF与31大终端天赋全量计算与生效（初始金币、商店货架、免费刷新、放弃增币、第一层额外门、经验加成等）
- 区分常规小怪战、艰难精英战、首领BOSS战、队员招募房（303X401）与多维补给商店（303X601）
- 记忆珍宝掉落、商店在售货架、泉水回血、兔兔打字机强化
- 局内进度持久化存储至 account.db (polyhedron_run)
- 终局结算积分、多维历程经验（道具45）、终端经验（道具46）发放
"""

import os
import json
import time
import random

_CFG_CACHE = None


def get_poly_cfg():
    global _CFG_CACHE
    if _CFG_CACHE is None:
        cfg_path = os.path.join(os.path.dirname(__file__), "polyhedron_cfg.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                _CFG_CACHE = json.load(f)
        else:
            _CFG_CACHE = {}
    return _CFG_CACHE


# ---------------- 关卡层级路线与地图定义 ----------------
TIER_ROUTES = [
    1001, 1002, 1003, 1004, 1005, 1006, 1007,
    2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008,
    3001, 3002, 3003, 3004, 3005, 3006, 3007, 3008, 3009, 3010
]

# 官方关卡资源类型完整精确映射 (基于 Assets/ComBattle/ABResources/Map/MatrixNew/)
# Y=1: 普通小怪战, Y=2: 艰难精英战, Y=3: 首领BOSS战, Y=4: 队员招募房间, Y=5: 额外异常挑战, Y=6: 多维补给商店
STAGE_MAPS_BY_TIER = {
    1: {
        "normal": [3035105, 3035106, 3035107, 3035108, 3035109, 3035110, 3035111, 3035112],
        "elite": [3035205, 3035206, 3035207, 3035208],
        "boss": [3035301, 3035302, 3035303, 3035304],
        "employee": 3035401,
        "shop": 3035601,
        "extra": [3035521, 3035522, 3035523, 3035524, 3035541, 3035542, 3035561, 3035562]
    },
    2: {
        "normal": [3036105, 3036106, 3036107, 3036108, 3036109, 3036110, 3036111, 3036112],
        "elite": [3036205, 3036206, 3036207, 3036208],
        "boss": [3036301, 3036302, 3036303, 3036304],
        "employee": 3036401,
        "shop": 3036601,
        "extra": [3036521, 3036522, 3036523, 3036524, 3036541, 3036542, 3036543, 3036561, 3036562]
    },
    3: {
        "normal": [3037105, 3037106, 3037107, 3037108, 3037109, 3037110, 3037111, 3037112],
        "elite": [3037205, 3037206, 3037207, 3037208],
        "boss": [3037301, 3037302, 3037303, 3037304],
        "employee": 3037401,
        "shop": 3037601,
        "extra": [3037521, 3037522, 3037523, 3037524, 3037525, 3037541, 3037542, 3037561, 3037562]
    }
}

BOSS_TIERS = {
    1007: 3035301,
    2008: 3036301,
    3010: 3037301
}


class PolyhedronRunManager:
    """多维变量局内探索状态机管理器"""

    @classmethod
    def load_run(cls, uid, db):
        """读取玩家当前进行中的对局快照"""
        rows = db.query("SELECT state, tier_id, run_data FROM polyhedron_run WHERE uid = ?", (uid,))
        if not rows or not rows[0]["run_data"]:
            return None
        try:
            data = json.loads(rows[0]["run_data"])
            data["state"] = rows[0]["state"]
            data["tier_id"] = rows[0]["tier_id"]
            return data
        except Exception:
            return None

    @classmethod
    def save_run(cls, uid, db, run_data):
        """持久化保存对局数据"""
        now = int(time.time())
        state = run_data.get("state", 2)
        tier_id = run_data.get("tier_id", 1001)
        run_json = json.dumps(run_data, ensure_ascii=False)
        db.execute(
            "INSERT INTO polyhedron_run (uid, state, tier_id, run_data, update_ts) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET state=excluded.state, tier_id=excluded.tier_id, "
            "run_data=excluded.run_data, update_ts=excluded.update_ts",
            (uid, state, tier_id, run_json, now)
        )
        db.execute("UPDATE polyhedron_meta SET game_state = ?, update_ts = ? WHERE uid = ?", (state, now, uid))

    @classmethod
    def start_run(cls, uid, db, hero_id_list, beacon_id_list, difficulty):
        """cs_18010 -> 开启新一轮多维探索（首战直接进场）"""
        cfg = get_poly_cfg()
        leader_hero_id = hero_id_list[0] if hero_id_list else 1084

        # 1. 组装队长 snapshot
        leader_astros = []
        hero_rows = db.query("SELECT astrolabe_1, astrolabe_2, astrolabe_3 FROM polyhedron_hero WHERE uid=? AND hero_id=?",
                             (uid, leader_hero_id))
        if hero_rows:
            leader_astros = [x for x in [hero_rows[0].get("astrolabe_1"), hero_rows[0].get("astrolabe_2"), hero_rows[0].get("astrolabe_3")] if x and x > 0]

        leader_snapshot = {
            "hero_id": leader_hero_id,
            "star": 500,
            "equip_list": [],
            "astrolabe_list": leader_astros,
            "weapon": {
                "breakthrough": 5,
                "servant": {"id": 0, "stage": 1}
            },
            "skin": 0,
            "break_level": 5,
            "hero_slot_exclusive_skill_list": [],
            "weapon_module": {"level": 0}
        }

        # 2. 终端升级
        term_rows = db.query("SELECT terminal_id FROM polyhedron_terminal WHERE uid=?", (uid,))
        terminal_ids = [r["terminal_id"] for r in term_rows] if term_rows else []

        # 3. 初始金币与信标/终端 BUFF 计算
        initial_coins = 600
        if 1 in beacon_id_list:
            initial_coins += 200
        if 3001 in terminal_ids:
            initial_coins += 100

        # 4. 初始队长血量与列表
        std_id = 3051000 + (leader_hero_id % 1000)
        if str(leader_hero_id) in cfg.get("poly_heroes", {}):
            std_id = cfg["poly_heroes"][str(leader_hero_id)].get("standard_id", std_id)

        hero_list = [{
            "hero_id": leader_hero_id,
            "template_id": std_id,
            "health": 10000,
            "max_health": 10000,
            "reborn_cold_down": 0,
            "difference_attribute_list": [],
            "injured": 0,
            "heal": 0,
            "damage": 0
        }]

        # 5. 生成 1-1 开局战斗节点 (普通小怪战 1001)
        tier_id = 1001
        initial_stage_id = cls._pick_stage_id(tier_id, 1)

        # 计算初始全局 rolls (基础 3 次 + 终端 3003 天赋 +2 次)
        initial_rolls = 3
        if 3003 in terminal_ids:
            initial_rolls += 2

        run_data = {
            "state": 2,  # STARTED
            "tier_id": tier_id,
            "difficulty": difficulty,
            "start_info": {
                "leader": leader_snapshot,
                "beacon_id_list": beacon_id_list,
                "difficulty": difficulty,
                "terminal_id_list": terminal_ids
            },
            "coins": initial_coins,
            "rolls": initial_rolls,
            "max_rolls": initial_rolls,
            "hero_list": hero_list,
            "fight_hero_id_list": [leader_hero_id],
            "artifact_list": [],
            "effect_list": [],
            "attribute_list": [],
            "cooperate_unique_skill_id": 0,
            "current_event": {
                "id": 1001,
                "stage_id": initial_stage_id,
                "reward_type": 3001
            },
            "current_stage": {
                "stage_id": initial_stage_id,
                "save_point": 1,  # 1 = BATTLE (直接触发开局战斗)
                "reward": {"round": 0, "item_list": []},
                "params": [],
                "gate_list": [],
                "shop": None,
                "attribute_modify_list": []
            },
            "stats": {
                "battle_count": 0,
                "elite_count": 0,
                "boss_count": 0,
                "total_damage": 0,
                "total_injured": 0
            }
        }

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def select_gate(cls, uid, db, gate_index):
        """cs_18014 -> 玩家选择大门，正式推进至所选关卡"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        current_stage = run_data.get("current_stage") or {}
        current_gates = current_stage.get("gate_list") or []

        chosen_gate = None
        for g in current_gates:
            if g.get("index") == gate_index:
                chosen_gate = g
                break
        if not chosen_gate and current_gates:
            chosen_gate = current_gates[0]

        if not chosen_gate:
            return run_data

        # 选门传送：统一在此处推进至目标关卡层级！
        current_tier = run_data.get("tier_id", 1001)
        next_tier = cls._get_next_tier(current_tier)
        run_data["tier_id"] = next_tier
        tier_id = next_tier

        event_info = chosen_gate.get("event", {})
        event_id = event_info.get("id", 1001)
        reward_type = event_info.get("reward_type", 0)

        cfg = get_poly_cfg()
        event_cfg = cfg.get("events", {}).get(str(event_id), {})
        event_type = event_cfg.get("event_type", 0)
        if event_type == 0:
            if 1001 <= event_id <= 1003:
                event_type = 1
            elif 1002001 <= event_id <= 1002003:
                event_type = 1002
            elif 2001 <= event_id <= 2003:
                event_type = 2
            elif 3001 <= event_id <= 3003:
                event_type = 3
            elif 3002001 <= event_id <= 3002003:
                event_type = 3002
            elif 4001 <= event_id <= 4003:
                event_type = 4
            elif 5001 <= event_id <= 5003:
                event_type = 5
            elif event_id in (6001, 6002):
                event_type = 6
            elif event_id == 7001:
                event_type = 7
            elif 8001 <= event_id <= 8003:
                event_type = 8
            else:
                event_type = 1

        next_after_tier = cls._get_next_tier(tier_id)

        # 1. 补给商店节点 (非战斗交互关，save_point = 3)
        if event_type == 5:
            cur_stage_id = cls._pick_stage_id(tier_id, 5)
            shop_data = cls._generate_shop_data(run_data)
            run_data["current_event"] = {
                "id": event_id,
                "stage_id": cur_stage_id,
                "reward_type": 5  # 5 = PolyhedronConst.REWARD_TYEP.SHOP
            }
            run_data["current_stage"] = {
                "stage_id": cur_stage_id,
                "save_point": 3,  # OPTION (非战斗商店交互)
                "reward": {"round": 0, "item_list": []},
                "params": [],
                "gate_list": cls._generate_gates_for_tier(next_after_tier, run_data),
                "shop": shop_data,
                "attribute_modify_list": []
            }

        # 2. 同伴招募干预节点 (save_point = 1 BATTLE + reward_type = 2 TEAMMATE，触发 C# 展台 NPC 生成与交互 UI)
        elif event_type == 2:
            cur_stage_id = cls._pick_stage_id(tier_id, 2)
            candidate_heroes = cls._pick_recruit_heroes(run_data)
            params = []
            # 招募类型：1=试炼战斗招募, 2=花费金币招募, 3=免费直接招募
            enlist_type_candidates = [2, 3, 2]
            for idx, h in enumerate(candidate_heroes):
                etype = enlist_type_candidates[idx % len(enlist_type_candidates)]
                params.extend([int(h), etype])
            run_data["current_event"] = {
                "id": event_id,
                "stage_id": cur_stage_id,
                "reward_type": 2  # 2 = PolyhedronConst.REWARD_TYEP.TEAMMATE
            }
            run_data["current_stage"] = {
                "stage_id": cur_stage_id,
                "save_point": 1,  # 1 = BATTLE (让 C# 初始化 NPC 生成器并在展台上刷出角色模型，同时展示交互 UI)
                "reward": {"round": 0, "item_list": []},
                "params": params,
                "gate_list": cls._generate_gates_for_tier(next_after_tier, run_data),
                "shop": None,
                "attribute_modify_list": []
            }

        # 3. 战斗节点 (普通小怪战 / 精英战 / 珍宝战 / 打字机战 / 升级战 / Boss战 / 异常挑战)
        else:
            stage_id = cls._pick_stage_id(tier_id, event_type)
            run_data["current_event"] = {
                "id": event_id,
                "stage_id": stage_id,
                "reward_type": reward_type
            }
            run_data["current_stage"] = {
                "stage_id": stage_id,
                "save_point": 1,  # BATTLE (待战，进入触发怪物生成)
                "reward": {"round": 0, "item_list": []},
                "params": [],
                "gate_list": [],
                "shop": None,
                "attribute_modify_list": []
            }

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def on_battle_finish(cls, uid, db, is_win=True, damage_info=None):
        """战斗通关结算（由 cs_54032 触发）"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        tier_id = run_data.get("tier_id", 1001)
        event_info = run_data.get("current_event", {})
        reward_type = event_info.get("reward_type", 0)
        event_id = event_info.get("id", 1001)

        cfg = get_poly_cfg()
        event_cfg = cfg.get("events", {}).get(str(event_id), {})
        event_type = event_cfg.get("event_type", 1)

        stats = run_data.setdefault("stats", {})
        stats["battle_count"] = stats.get("battle_count", 0) + 1

        coin_gain = 400
        if tier_id in BOSS_TIERS or event_type in (6, 7):
            coin_gain = 1000
            stats["boss_count"] = stats.get("boss_count", 0) + 1
        elif event_type in (1002, 3002) or event_id >= 1002000:
            coin_gain = 700
            stats["elite_count"] = stats.get("elite_count", 0) + 1

        run_data["coins"] = run_data.get("coins", 600) + coin_gain
        cur_stage_id = event_info.get("stage_id") or cls._pick_stage_id(tier_id, event_type)

        # 累计出战英雄本场输出伤害、承伤与治疗量
        fight_hero_ids = run_data.get("fight_hero_id_list") or [h["hero_id"] for h in run_data.get("hero_list", [])[:3]]
        b_injured = int(damage_info.get("injured_num") or 0) if damage_info else 0
        b_kills = int(damage_info.get("enemy_dead_num") or 1) if damage_info else 1
        b_time_sec = max(1.0, (damage_info.get("battle_time") or 10000) / 1000.0) if damage_info else 10.0

        tier_major = tier_id // 1000 if tier_id > 1000 else 1
        if tier_id in BOSS_TIERS or event_type in (6, 7):
            stage_total_damage = int((tier_major * 1500000 + 1000000) * (1.0 + (b_time_sec / 30.0)))
        elif event_type in (1002, 3002) or event_id >= 1002000:
            stage_total_damage = int((tier_major * 600000 + 300000) * max(1, b_kills) * 0.5)
        else:
            stage_total_damage = int((tier_major * 300000 + 150000) * max(1, b_kills) * 0.4)

        num_fighters = max(1, len(fight_hero_ids))
        for idx, hid in enumerate(fight_hero_ids):
            for h in run_data.get("hero_list", []):
                if h.get("hero_id") == hid:
                    if idx == 0:
                        h_dmg = int(stage_total_damage * (0.65 if num_fighters >= 2 else 1.0))
                        h_inj = int(b_injured * 0.7)
                        h_heal = int(h_inj * 0.2)
                    elif idx == 1:
                        h_dmg = int(stage_total_damage * 0.25)
                        h_inj = int(b_injured * 0.2)
                        h_heal = int(h_inj * 0.3)
                    else:
                        h_dmg = int(stage_total_damage * 0.10)
                        h_inj = int(b_injured * 0.1)
                        h_heal = int(stage_total_damage * 0.05) if hid in (1084, 1061, 1056) else 0

                    if hid in (1084, 1056):
                        h_heal += int(stage_total_damage * 0.15) + 50000
                    elif hid == 1061:
                        h_heal += int(stage_total_damage * 0.08) + 20000

                    h["damage"] = h.get("damage", 0) + max(1000, h_dmg)
                    h["injured"] = h.get("injured", 0) + h_inj
                    h["heal"] = h.get("heal", 0) + h_heal
                    break

        # 1. 打字机强化关 (event_type == 4 / event_id in (4001, 4002, 4003))
        if event_type == 4 or event_id in (4001, 4002, 4003):
            typewriter_effects = [201, 202]
            run_data["current_stage"] = {
                "stage_id": cur_stage_id,
                "save_point": 2,  # REWARD (打字机属性强化 3选1)
                "reward": {
                    "round": 0,
                    "item_list": [{"class": 4, "params": [eff]} for eff in typewriter_effects]
                },
                "params": [],
                "gate_list": [],
                "shop": None,
                "attribute_modify_list": []
            }

        # 2. 珍宝升级关 (event_type == 8 / event_id in (8001, 8002, 8003))
        elif event_type == 8 or event_id in (8001, 8002, 8003):
            owned_arts = [a["id"] for a in run_data.get("artifact_list", []) if a.get("id")]
            if not owned_arts:
                owned_arts = [70501, 70551, 70601]
            upgrade_candidates = random.sample(owned_arts, min(3, len(owned_arts)))
            run_data["current_stage"] = {
                "stage_id": cur_stage_id,
                "save_point": 2,  # REWARD (珍宝升级 3选1)
                "reward": {
                    "round": 0,
                    "item_list": [{"class": 5, "params": [aid]} for aid in upgrade_candidates]
                },
                "params": [],
                "gate_list": [],
                "shop": None,
                "attribute_modify_list": []
            }

        # 3. 珍宝关 / 精英珍宝关 / Boss 战 (3001~3009 或 3007 专属)
        elif event_type in (3, 3002, 6, 7, 9) or (reward_type and reward_type >= 3000):
            effective_rt = 3007 if (tier_id in BOSS_TIERS or event_type in (6, 7) or reward_type == 3007) else reward_type
            candidates = cls._pick_reward_artifacts(run_data, effective_rt)
            run_data["current_stage"] = {
                "stage_id": cur_stage_id,
                "save_point": 2,  # REWARD (3选1珍宝)
                "reward": {
                    "round": 0,
                    "item_list": [{"class": 6, "params": [aid]} for aid in candidates]
                },
                "params": [],
                "gate_list": [],
                "shop": None,
                "attribute_modify_list": []
            }

        # 4. 纯货币战或其他直接通关
        else:
            if tier_id == 3010:
                run_data["state"] = 3  # SETTLEMENT
            else:
                next_tier = cls._get_next_tier(tier_id)
                run_data["current_stage"] = {
                    "stage_id": cur_stage_id,
                    "save_point": 3,  # OPTION (激活离开大门，当前 tier_id 仍保持不变)
                    "reward": {"round": 0, "item_list": []},
                    "params": [],
                    "gate_list": cls._generate_gates_for_tier(next_tier, run_data),
                    "shop": None,
                    "attribute_modify_list": []
                }

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def select_reward(cls, uid, db, reward_index):
        """cs_18012 -> 挑选珍宝/强化 (index=1~3) 或 放弃领金币 (index=0)"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        tier_id = run_data.get("tier_id", 1001)
        cur_stage = run_data.get("current_stage", {})
        cur_stage_id = cur_stage.get("stage_id") or cls._pick_stage_id(tier_id, 1)
        reward_items = cur_stage.get("reward", {}).get("item_list", [])

        # 官方配置基础 150 金币 (PolyhedronSettingCfg[21]) + 属性 28 (财富密码/终端天赋加成)
        give_up_coins = 150
        attrs = cls._compute_active_attributes(run_data)
        for a in attrs:
            if a["id"] == 28:
                give_up_coins += a["value"]

        beacons = run_data.get("start_info", {}).get("beacon_id_list", [])

        if reward_index > 0 and reward_index <= len(reward_items):
            chosen = reward_items[reward_index - 1]
            chosen_class = chosen.get("class")
            if chosen_class == 6:  # ARTIFACT
                art_id = chosen["params"][0]
                art_lvl = 1
                if 3 in beacons and random.random() < 0.20:
                    art_lvl = 2
                cls._add_artifact_to_run(uid, db, run_data, art_id, level=art_lvl)
            elif chosen_class == 5:  # ARTIFACT_UP_LEVEL
                art_id = chosen["params"][0]
                cls._add_artifact_to_run(uid, db, run_data, art_id, level=1)
            elif chosen_class == 4:  # BUFF / 打字机
                eff_id = chosen["params"][0]
                eff_list = run_data.setdefault("effect_list", [])
                if not any((e.get("id") == eff_id if isinstance(e, dict) else e == eff_id) for e in eff_list):
                    eff_list.append({"id": eff_id})
        else:
            run_data["coins"] = run_data.get("coins", 600) + give_up_coins

        if tier_id == 3010:
            run_data["state"] = 3  # SETTLEMENT
        else:
            # 领奖后留在当前战斗场景中，开启通往前方的传送大门
            # 注意：tier_id 保持不变（仍为当前关卡），大门生成对应下一层关卡！
            next_tier = cls._get_next_tier(tier_id)
            run_data["current_stage"] = {
                "stage_id": cur_stage_id,
                "save_point": 3,  # OPTION (激活传送门)
                "reward": {"round": 0, "item_list": []},
                "params": [],
                "gate_list": cls._generate_gates_for_tier(next_tier, run_data),
                "shop": None,
                "attribute_modify_list": []
            }

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def reroll_reward(cls, uid, db):
        """cs_18030 -> 重骰 3 选 1 珍宝"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        if run_data.get("rolls", 0) <= 0:
            return run_data

        run_data["rolls"] -= 1
        reward_type = run_data.get("current_event", {}).get("reward_type", 3001)
        candidates = cls._pick_reward_artifacts(run_data, reward_type)

        reward_info = run_data.setdefault("current_stage", {}).setdefault("reward", {})
        reward_info["round"] = reward_info.get("round", 0) + 1
        reward_info["item_list"] = [{"class": 6, "params": [aid]} for aid in candidates]

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def buy_shop_item(cls, uid, db, shop_index):
        """cs_18022 -> 购买商店货架珍宝道具"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        shop = run_data.get("current_stage", {}).get("shop")
        if not shop or "item_list" not in shop:
            return run_data

        items = shop["item_list"]
        if shop_index < 1 or shop_index > len(items):
            return run_data

        target_item = items[shop_index - 1]
        if target_item.get("is_available") != 1:
            return run_data

        price = target_item.get("price", 0)
        current_coins = run_data.get("coins", 0)
        if current_coins < price:
            return run_data

        run_data["coins"] = current_coins - price
        target_item["is_available"] = 0

        item_detail = target_item.get("item", {})
        item_class = item_detail.get("class", 6)
        if item_class == 6:
            art_id = item_detail["params"][0]
            cls._add_artifact_to_run(uid, db, run_data, art_id)
        elif item_class == 4:
            eff_id = item_detail["params"][0]
            eff_list = run_data.setdefault("effect_list", [])
            if not any((e.get("id") == eff_id if isinstance(e, dict) else e == eff_id) for e in eff_list):
                eff_list.append({"id": eff_id})
        elif item_class == 7:
            cls._heal_team(run_data, 50)

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def refresh_shop(cls, uid, db):
        """cs_18024 -> 刷新商店货架"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        shop = run_data.setdefault("current_stage", {}).setdefault("shop", {})
        refresh_times = shop.get("refresh_times", 0)

        attrs = cls._compute_active_attributes(run_data)
        free_refreshes = 0
        max_refreshes = 3
        for a in attrs:
            if a["id"] == 18:
                free_refreshes += a["value"]
            elif a["id"] == 16:
                max_refreshes = max(max_refreshes, a["value"])

        if refresh_times >= max_refreshes:
            return run_data

        cost = 0
        if refresh_times >= free_refreshes:
            paid_count = refresh_times - free_refreshes + 1
            cost = 100 * paid_count

        current_coins = run_data.get("coins", 0)
        if current_coins < cost:
            return run_data

        if cost > 0:
            run_data["coins"] = current_coins - cost

        shop["refresh_times"] = refresh_times + 1
        shop_data = cls._generate_shop_data(run_data)
        shop["item_list"] = shop_data["item_list"]

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def recover_shop_blood(cls, uid, db):
        """cs_18026 -> 生命泉水全员恢复"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        shop = run_data.setdefault("current_stage", {}).setdefault("shop", {})
        recover_times = shop.get("recover_times", 0)

        attrs = cls._compute_active_attributes(run_data)
        max_recover = 1
        for a in attrs:
            if a["id"] == 17:
                max_recover = max(max_recover, a["value"])

        if recover_times >= max_recover:
            return run_data

        cost = 100 * recover_times
        current_coins = run_data.get("coins", 0)
        if current_coins < cost:
            return run_data

        if cost > 0:
            run_data["coins"] = current_coins - cost
        shop["recover_times"] = recover_times + 1
        cls._heal_team(run_data, 50)

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def enlist_hero(cls, uid, db, hero_id):
        """cs_18020 -> 招募同伴加入局内队伍（保持招募房间数据完整）"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        cfg = get_poly_cfg()
        if hero_id > 0:
            params = run_data.get("current_stage", {}).get("params", [])
            enlist_type = 2
            for i in range(0, len(params), 2):
                if params[i] == hero_id and i + 1 < len(params):
                    enlist_type = params[i + 1]
                    break

            if enlist_type == 2:  # PAY (花钱招募)
                need_coin = 800
                poly_hero_cfgs = cfg.get("poly_heroes", {})
                if str(hero_id) in poly_hero_cfgs:
                    need_coin = poly_hero_cfgs[str(hero_id)].get("need_coin", 800)
                current_coins = run_data.get("coins", 0)
                if current_coins >= need_coin:
                    run_data["coins"] = current_coins - need_coin

            std_id = 3051000 + (hero_id % 1000)
            poly_hero_cfgs = cfg.get("poly_heroes", {})
            if str(hero_id) in poly_hero_cfgs:
                std_id = poly_hero_cfgs[str(hero_id)].get("standard_id", std_id)

            hero_list = run_data.setdefault("hero_list", [])
            if not any(h.get("hero_id") == hero_id for h in hero_list):
                hero_list.append({
                    "hero_id": hero_id,
                    "template_id": std_id,
                    "health": 10000,
                    "max_health": 10000,
                    "reborn_cold_down": 0,
                    "difference_attribute_list": [],
                    "injured": 0,
                    "heal": 0,
                    "damage": 0
                })

            fight_list = run_data.setdefault("fight_hero_id_list", [])
            if len(fight_list) < 3 and hero_id not in fight_list:
                fight_list.append(hero_id)

        # 招募完成后，保持场景与门列表，将 save_point 设为 3 (OPTION) 激活离开传送门
        if "current_stage" in run_data and isinstance(run_data["current_stage"], dict):
            run_data["current_stage"]["save_point"] = 3
        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def switch_team(cls, uid, db, fight_id_list):
        """cs_18028 -> 调整当前出战 3 人编队"""
        run_data = cls.load_run(uid, db)
        if not run_data:
            return None

        available_hids = {h["hero_id"] for h in run_data.get("hero_list", [])}
        valid_fight_list = [hid for hid in fight_id_list if hid in available_hids][:3]
        if valid_fight_list:
            run_data["fight_hero_id_list"] = valid_fight_list

        cls.save_run(uid, db, run_data)
        return run_data

    @classmethod
    def settle_run(cls, uid, db):
        """cs_18018 / 终局通关 -> 动态计算发放历程经验(45)、多维终端经验(46)、对局积分(29)"""
        run_data = cls.load_run(uid, db)
        now = int(time.time())

        tier_id = run_data.get("tier_id", 1001) if run_data else 1001
        difficulty = run_data.get("difficulty", 1) if run_data else 1
        artifacts = run_data.get("artifact_list", []) if run_data else []
        coins = run_data.get("coins", 0) if run_data else 0
        heroes = run_data.get("hero_list", []) if run_data else []
        beacons = run_data.get("start_info", {}).get("beacon_id_list", []) if run_data else []
        terminals = run_data.get("start_info", {}).get("terminal_id_list", []) if run_data else []

        tier_major = tier_id // 1000 if tier_id > 1000 else 1
        tier_level = tier_id % 1000 if tier_id > 1000 else 1

        # 1. 关卡进度与完成度 (满关 25 关)
        if tier_major == 1:
            cleared_stages = tier_level
        elif tier_major == 2:
            cleared_stages = 7 + tier_level
        else:
            cleared_stages = 15 + tier_level
        cleared_stages = min(25, max(1, cleared_stages))
        is_full_clear = (tier_id == 3010)

        # 2. 难度系数 (难度 1~20 动态放大)
        diff_mult = 1.0 + (max(1, difficulty) - 1) * 0.15
        exp_diff_mult = 1.0 + (max(1, difficulty) - 1) * 0.10

        # 3. 珍宝品质分类统计
        exclusive_arts = [a for a in artifacts if a.get("id", 0) < 70000]
        normal_arts = [a for a in artifacts if a.get("id", 0) >= 70000]

        # 4. 动态对局积分计算 (Point / Currency 29)
        base_stage_score = cleared_stages * 180 + (3500 if is_full_clear else (tier_major - 1) * 1000)
        art_score = len(exclusive_arts) * 200 + len(normal_arts) * 100
        coin_score = int(coins * 0.5)
        team_score = len(heroes) * 300
        raw_point = base_stage_score + art_score + coin_score + team_score
        point = int(raw_point * diff_mult)

        # 5. 历程经验 / 偏移等级经验 (Decision Exp / Currency 45)
        base_decision_exp = cleared_stages * 60 + (800 if is_full_clear else (tier_major - 1) * 250)
        decision_art_bonus = len(artifacts) * 20
        bounty_mult = 1.5 if 13 in beacons else 1.0
        decision_exp = int((base_decision_exp + decision_art_bonus) * exp_diff_mult * bounty_mult)

        # 6. 多维终端经验 (Terminal Exp / Currency 46)
        base_terminal_exp = cleared_stages * 80 + (1000 if is_full_clear else (tier_major - 1) * 350)
        terminal_art_bonus = len(artifacts) * 25
        terminal_exp = int((base_terminal_exp + terminal_art_bonus) * exp_diff_mult * bounty_mult)

        # 写入数据库货币累加
        db.execute("INSERT INTO currency (uid, id, num) VALUES (?, 45, ?) ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                   (uid, decision_exp))
        db.execute("INSERT INTO currency (uid, id, num) VALUES (?, 46, ?) ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                   (uid, terminal_exp))
        db.execute("INSERT INTO currency (uid, id, num) VALUES (?, 29, ?) ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                   (uid, point))

        # 通关记录难度
        if is_full_clear or tier_id in BOSS_TIERS:
            db.execute("INSERT OR REPLACE INTO polyhedron_difficulty (uid, difficulty_id, update_ts) VALUES (?, ?, ?)",
                       (uid, difficulty, now))

        if run_data:
            run_data["state"] = 3  # SETTLEMENT
            cls.save_run(uid, db, run_data)

        # 广播多维变量终局结算事件（驱动成就系统实时弹窗）
        try:
            import event_bus
            event_bus.bus.emit(
                event_bus.Events.POLYHEDRON_PASS,
                None,
                uid,
                difficulty=difficulty,
                artifacts=artifacts,
                heroes=heroes,
                beacons=beacons,
                terminals=terminals,
                is_full_clear=is_full_clear,
                times=1
            )
        except Exception:
            pass

        end_info = {
            "point": point,
            "decision_exp": decision_exp,
            "terminal_exp": terminal_exp
        }
        return end_info

    @classmethod
    def reset_run(cls, uid, db):
        """cs_18016 -> 结束探索重置回大厅未开始状态"""
        now = int(time.time())
        db.execute("UPDATE polyhedron_run SET state = 1, run_data = '', update_ts = ? WHERE uid = ?", (now, uid))
        db.execute("UPDATE polyhedron_meta SET game_state = 1, update_ts = ? WHERE uid = ?", (now, uid))

    @classmethod
    def get_policy_cycle_id(cls, now_ts=None, base_anchor=1700000000):
        """计算当前历程周期 ID（以每周一 05:00:00 为基准 7 天轮换周期）"""
        if now_ts is None:
            now_ts = int(time.time())
        # 604800 秒 = 7 天
        return int((now_ts - base_anchor) // 604800)

    @classmethod
    def check_and_refresh_policy_cycle(cls, uid, db, activity_id=4307301, force_cycle_id=None):
        """
        周期刷新管理器：
        仅在真实跨越周期时，重置“维度偏移”的历程经验（Currency 45）和已领取奖励记录（polyhedron_policy_claimed）！
        多维终端经验(46)、已点亮天赋加点、已解锁英雄、已解锁信标、最高难度通关记录等全部永久保留！
        """
        now = int(time.time())
        current_cycle_id = force_cycle_id if force_cycle_id is not None else cls.get_policy_cycle_id(now)

        meta = db.get("polyhedron_meta", uid, "AND activity_id=?", (activity_id,))
        last_cycle_id = meta.get("cycle_id", 0) if meta else 0

        if meta and last_cycle_id != 0 and last_cycle_id == current_cycle_id and force_cycle_id is None:
            return False  # 同一周期内，无需刷新

        if last_cycle_id == 0 and force_cycle_id is None:
            # 首次记录周期锚点，保留已有经验与领取状态
            db.upsert("polyhedron_meta", uid, {
                "activity_id": activity_id,
                "cycle_id": current_cycle_id,
                "update_ts": now
            }, keys=("uid", "activity_id"))
            return False

        # 跨周期刷新：仅重置历程经验与已领取记录
        db.execute("UPDATE currency SET num = 0 WHERE uid = ? AND id = 45", (uid,))
        db.execute("DELETE FROM polyhedron_policy_claimed WHERE uid = ? AND activity_id = ?", (uid, activity_id))

        db.upsert("polyhedron_meta", uid, {
            "activity_id": activity_id,
            "cycle_id": current_cycle_id,
            "update_ts": now
        }, keys=("uid", "activity_id"))

        return True

    # ---------------- 辅助内部生成算法 ----------------

    @classmethod
    def _generate_gates_for_tier(cls, tier_id, run_data=None):
        """根据当前所在 Tier 生成 2~4 扇候选前进门"""
        tier_level = tier_id % 1000 if tier_id > 1000 else 1
        tier_major = tier_id // 1000 if tier_id > 1000 else 1

        # 首领 BOSS 门 (1-7 / 2-8 / 3-10)
        if tier_id in BOSS_TIERS or (tier_major == 1 and tier_level == 7) or (tier_major == 2 and tier_level == 8) or (tier_major == 3 and tier_level == 10):
            boss_stage = BOSS_TIERS.get(tier_id, 3035301 if tier_major == 1 else (3036301 if tier_major == 2 else 3037301))
            boss_event_id = 6001 if tier_major == 1 else (6002 if tier_major == 2 else 7001)
            return [{
                "index": 1,
                "event": {
                    "id": boss_event_id,
                    "stage_id": boss_stage,
                    "reward_type": 3007 if tier_major < 3 else 0
                }
            }]

        beacons = run_data.get("start_info", {}).get("beacon_id_list", []) if run_data else []

        gates = []
        reward_sub_types = [3001, 3002, 3003, 3004, 3005, 3006, 3008, 3009]
        random.shuffle(reward_sub_types)

        # 门 1: 普通珍宝战 (强化普攻/技能/奥义/闪避/打字机/解和/粉碎)
        stage_1 = cls._pick_stage_id(tier_id, 3)
        event_art_id = 3000 + tier_major  # 3001 / 3002 / 3003
        gates.append({
            "index": 1,
            "event": {
                "id": event_art_id,
                "stage_id": stage_1,
                "reward_type": reward_sub_types[0]
            }
        })

        # 门 2: 商店 / 招募 / 打字机强化战 / 精英战
        if tier_level in (3, 6):
            stage_shop = cls._pick_stage_id(tier_id, 5)
            gates.append({
                "index": 2,
                "event": {
                    "id": 5000 + tier_major,  # 5001 / 5002 / 5003 (多维补给商店)
                    "stage_id": stage_shop,
                    "reward_type": 5  # 5 = PolyhedronConst.REWARD_TYEP.SHOP
                }
            })
        elif tier_level in (2, 5):
            # 信标 10 (孤狼之道): 无法招募队友，招募门转为精英珍宝战
            if 10 in beacons:
                stage_elite = cls._pick_stage_id(tier_id, 3002)
                gates.append({
                    "index": 2,
                    "event": {
                        "id": 3002000 + tier_major,  # 3002001 / 3002002 / 3002003 (精英战·珍宝)
                        "stage_id": stage_elite,
                        "reward_type": reward_sub_types[1]
                    }
                })
            else:
                stage_recruit = cls._pick_stage_id(tier_id, 2)
                gates.append({
                    "index": 2,
                    "event": {
                        "id": 2000 + tier_major,  # 2001 / 2002 / 2003 (队员招募干预节点)
                        "stage_id": stage_recruit,
                        "reward_type": 2  # 2 = PolyhedronConst.REWARD_TYEP.TEAMMATE
                    }
                })
        elif tier_level == 4:
            # 兔兔打字机强化战斗关 (产出打字机强化效果 3选1)
            stage_tw = cls._pick_stage_id(tier_id, 4)
            gates.append({
                "index": 2,
                "event": {
                    "id": 4000 + tier_major,  # 4001 / 4002 / 4003 (普通战·修正者属性)
                    "stage_id": stage_tw,
                    "reward_type": 4  # 4 = PolyhedronConst.REWARD_TYEP.HERO_ATTRIBUTE
                }
            })
        else:
            # 精英珍宝战 / 精英货币战
            stage_elite = cls._pick_stage_id(tier_id, 3002)
            gates.append({
                "index": 2,
                "event": {
                    "id": 3002000 + tier_major,  # 精英战·珍宝奖励
                    "stage_id": stage_elite,
                    "reward_type": reward_sub_types[1]
                }
            })

        # 门 3: 常规货币战 (金币战)
        stage_3 = cls._pick_stage_id(tier_id, 1)
        gates.append({
            "index": 3,
            "event": {
                "id": 1000 + tier_major,  # 1001 / 1002 / 1003 (普通战·货币)
                "stage_id": stage_3,
                "reward_type": 1  # 1 = PolyhedronConst.REWARD_TYEP.CURRENCY
            }
        })

        # 信标 4 (佣兵赌注): 第一层关卡选择过程中额外增加一个关卡选项
        if 4 in beacons and tier_major == 1:
            stage_4 = cls._pick_stage_id(tier_id, 8)
            gates.append({
                "index": 4,
                "event": {
                    "id": 8000 + tier_major,  # 8001 珍宝升级战
                    "stage_id": stage_4,
                    "reward_type": 7
                }
            })

        return gates

    @classmethod
    def _pick_stage_id(cls, tier_id, event_type):
        """选择具体关卡 ID（严格对应官方 Normal / Elite / Boss / Employee / Shop 资源包）"""
        tier_major = tier_id // 1000 if tier_id > 1000 else 1
        tier_level = tier_id % 1000 if tier_id > 1000 else 1
        tier_maps = STAGE_MAPS_BY_TIER.get(tier_major, STAGE_MAPS_BY_TIER[1])

        # 1. 多维补给商店 (event_type == 5 / "shop") -> 固定对应 303X601
        if event_type in (5, "shop"):
            return tier_maps.get("shop", 3035601)

        # 2. 队员招募房间 (event_type == 2 / "employee" / "recruit") -> 固定对应 303X401
        if event_type in (2, "employee", "recruit"):
            return tier_maps.get("employee", 3035401)

        # 3. 首领 Boss 战 (event_type in (6, 7, "boss") 或 守底 1-7/2-8/3-10) -> 选自 303X301..304
        if event_type in (6, 7, "boss") or tier_id in BOSS_TIERS or (tier_major == 1 and tier_level == 7) or (tier_major == 2 and tier_level == 8) or (tier_major == 3 and tier_level == 10):
            boss_list = tier_maps.get("boss", [3035301])
            return random.choice(boss_list)

        # 4. 艰难精英战 (event_type in (1002, 3002, "elite")) -> 选自 303X205..208
        if event_type in (1002, 3002, "elite"):
            elite_list = tier_maps.get("elite", [3035205])
            return random.choice(elite_list)

        # 5. 异常挑战战 (event_type in (10001, 10002, "extra")) -> 选自 303X521..562
        if event_type in (10001, 10002, "extra"):
            extra_list = tier_maps.get("extra", [3035521])
            return random.choice(extra_list)

        # 6. 普通小怪战 / 金币战 / 珍宝战 / 打字机战 / 珍宝升级战 (event_type in (1, 3, 4, 8, 9, "normal")) -> 选自 303X105..112 (排除无怪地图)
        normal_list = tier_maps.get("normal", [3035105])
        return random.choice(normal_list)

    @classmethod
    def _pick_reward_artifacts(cls, run_data, reward_type):
        """生成 3 选 1 珍宝列表（专属金色珍宝 3007 严格限定当前在队修正者 + 自动去重）"""
        cfg = get_poly_cfg()
        by_sub = cfg.get("artifacts_by_sub_type", {})
        art_cfgs = cfg.get("artifacts", {})

        current_hids = {int(h.get("hero_id")) for h in (run_data.get("hero_list") or []) if h.get("hero_id")}
        acquired_arts = {int(a.get("id")) for a in (run_data.get("artifact_list") or []) if a.get("id")}

        # 1. 专属金色珍宝（3007 / Boss 战通关 / 专属大门）
        if reward_type == 3007:
            spec_candidates = []
            for aid_str, acfg in art_cfgs.items():
                aid = int(aid_str)
                ex_hid = int(acfg.get("exclusive_hero_id") or 0)
                if ex_hid in current_hids and aid not in acquired_arts:
                    spec_candidates.append(aid)

            if len(spec_candidates) >= 3:
                return random.sample(spec_candidates, 3)
            else:
                other_arts = [
                    int(k) for k in art_cfgs.keys()
                    if int(k) not in acquired_arts and int(art_cfgs[k].get("exclusive_hero_id") or 0) == 0
                ]
                if not other_arts:
                    other_arts = [70501, 70502, 70503, 70551, 70552, 70601, 70751, 70801]

                needed = 3 - len(spec_candidates)
                filler = random.sample(other_arts, min(needed, len(other_arts)))
                res = spec_candidates + filler
                while len(res) < 3:
                    res.append(random.choice(other_arts))
                random.shuffle(res)
                return res

        # 2. 常规珍宝战（3001~3006, 3008, 3009）
        candidates = [int(x) for x in by_sub.get(str(reward_type), []) if int(x) not in acquired_arts]

        # 20% 概率混入当前队伍角色的专属金色珍宝
        if random.random() < 0.20 and current_hids:
            team_spec_arts = [
                int(k) for k, acfg in art_cfgs.items()
                if int(acfg.get("exclusive_hero_id") or 0) in current_hids and int(k) not in acquired_arts
            ]
            if team_spec_arts and candidates:
                lucky_spec = random.choice(team_spec_arts)
                sample_count = min(2, len(candidates))
                picked = random.sample(candidates, sample_count)
                res = [lucky_spec] + picked
                random.shuffle(res)
                return res

        if not candidates or len(candidates) < 3:
            all_arts = [int(k) for k in art_cfgs.keys() if int(art_cfgs[k].get("exclusive_hero_id") or 0) in (0, *current_hids) and int(k) not in acquired_arts]
            if len(all_arts) >= 3:
                return random.sample(all_arts, 3)
            return [70501, 70551, 70601]

        return random.sample(candidates, 3)

    @classmethod
    def _generate_shop_data(cls, run_data=None):
        """生成商店货架物品（珍宝池严格限定通用珍宝 + 当前队伍角色的专属金色珍宝）"""
        cfg = get_poly_cfg()
        art_cfgs = cfg.get("artifacts", {})
        current_hids = {int(h.get("hero_id")) for h in (run_data.get("hero_list") or []) if h.get("hero_id")} if run_data else set()
        acquired_arts = {int(a.get("id")) for a in (run_data.get("artifact_list") or []) if a.get("id")} if run_data else set()

        valid_pool = []
        team_spec_pool = []
        for aid_str, acfg in art_cfgs.items():
            aid = int(aid_str)
            if aid in acquired_arts:
                continue
            ex_hid = int(acfg.get("exclusive_hero_id") or 0)
            if ex_hid == 0:
                valid_pool.append(aid)
            elif ex_hid in current_hids:
                team_spec_pool.append(aid)

        attrs = cls._compute_active_attributes(run_data) if run_data else []
        art_count = 4
        for a in attrs:
            if a["id"] == 15:
                art_count = max(4, a["value"])

        shop_arts = []
        if team_spec_pool:
            picked_spec = random.choice(team_spec_pool)
            shop_arts.append(picked_spec)
            art_count -= 1

        if len(valid_pool) >= art_count:
            shop_arts.extend(random.sample(valid_pool, art_count))
        else:
            shop_arts.extend(valid_pool)

        random.shuffle(shop_arts)

        item_list = []
        for aid in shop_arts:
            item_list.append({
                "item": {
                    "class": 6,  # 6 = ARTIFACT
                    "params": [int(aid)]
                },
                "price": random.choice([300, 350, 400, 450]),
                "is_available": 1
            })

        # 商店特供：兔兔打字机强化道具 (class = 4, params = [201], 售价 500)
        item_list.append({
            "item": {
                "class": 4,  # 4 = BUFF (兔兔打字机强化)
                "params": [201]
            },
            "price": 500,
            "is_available": 1
        })

        return {
            "refresh_times": 0,
            "recover_times": 0,
            "item_list": item_list
        }

    @classmethod
    def _pick_recruit_heroes(cls, run_data):
        """生成 3 位招募同伴候选人列表"""
        cfg = get_poly_cfg()
        all_poly_heroes = [int(h) for h in cfg.get("poly_heroes", {}).keys()]
        if not all_poly_heroes:
            all_poly_heroes = [1011, 1024, 1027, 1037, 1038, 1039, 1050, 1058, 1066, 1084, 1093, 1094, 1099, 1138, 1148, 1184, 1199, 1284]
        current_hids = {h["hero_id"] for h in run_data.get("hero_list", [])}
        available = [h for h in all_poly_heroes if h not in current_hids]
        if not available:
            available = all_poly_heroes
        return random.sample(available, min(3, len(available)))

    @classmethod
    def _add_artifact_to_run(cls, uid, db, run_data, art_id, level=1):
        """将珍宝加入行囊并标记图鉴"""
        art_id = int(art_id)
        art_list = run_data.setdefault("artifact_list", [])
        found = False
        for a in art_list:
            if a["id"] == art_id:
                a["level"] = a.get("level", 1) + level
                found = True
                break
        if not found:
            art_list.append({"id": art_id, "level": level, "life_time": 0})

        now = int(time.time())
        db.execute("INSERT OR REPLACE INTO polyhedron_artifact (uid, artifact_id, state, update_ts) VALUES (?, ?, 2, ?)",
                   (uid, art_id, now))

    @classmethod
    def _heal_team(cls, run_data, percent=50):
        """全队生命恢复"""
        for h in run_data.get("hero_list", []):
            max_hp = h.get("max_health", 10000)
            cur_hp = h.get("health", 10000)
            h["health"] = min(max_hp, cur_hp + max_hp * percent // 100)

    @classmethod
    def _get_next_tier(cls, current_tier):
        """计算下一层级 ID"""
        if current_tier in TIER_ROUTES:
            idx = TIER_ROUTES.index(current_tier)
            if idx + 1 < len(TIER_ROUTES):
                return TIER_ROUTES[idx + 1]
        return current_tier

    @classmethod
    def _compute_active_attributes(cls, run_data):
        """根据当前激活的信标、终端加点和珍宝，汇总生成全局属性列表 (1..34)"""
        # 默认基础属性（保证商店刷新/回血/重骰/复活按钮在客户端正常开启）
        attrs = {
            1: 1,    # LEADER_REBORN_TIMES: 基础复活次数 1
            2: 1000, # REBORN_HEALTH_PERCENT: 复活恢复 100% 生命
            7: 0,    # RE_ROLL_GATE_TIMES: 大门重选
            8: 3,    # RE_ROLL_ARTIFACT_REWARD_TIMES: 珍宝重骰基础 3 次
            15: 4,   # SHOP_ITEM_NUM: 商店商品基础 4 个
            16: 3,   # SHOP_REFRESH_TIME: 商店刷新最大 3 次
            17: 1,   # SHOP_RECOVER_TIME: 商店回血最大 1 次
            18: 0,   # SHOP_FREE_REFRESH_TIME: 商店免费刷新 0 次
            28: 0,   # GIVE_UP_ADD_COIN_NUM: 放弃额外加金币 0
            30: 1,   # LEADER_REBORN_MAX_TIMES: 复活次数上限 1 次
            33: 0,   # EXTRA_EXP_RATE: 经验加成
            34: 0    # TIER_ONE_BONUS_GATE_NUM: 第一层额外门 0
        }

        beacons = run_data.get("start_info", {}).get("beacon_id_list", []) if run_data else []
        terminals = run_data.get("start_info", {}).get("terminal_id_list", []) if run_data else []

        # 信标效果映射
        if 3 in beacons:   # 改装能手
            attrs[6] = attrs.get(6, 0) + 200
        if 4 in beacons:   # 佣兵赌注
            attrs[34] = attrs.get(34, 0) + 1
        if 5 in beacons:   # 保守战略
            attrs[1] = attrs.get(1, 1) + 1
            attrs[30] = attrs.get(30, 1) + 1
        if 7 in beacons:   # 交易策略
            attrs[18] = attrs.get(18, 0) + 2
        if 8 in beacons:   # 财富密码
            attrs[28] = attrs.get(28, 0) + 100
        if 9 in beacons:   # 私有渠道
            attrs[15] = attrs.get(15, 4) + 3
        if 10 in beacons:  # 孤狼之道
            attrs[32] = attrs.get(32, 0) - 1000
        if 13 in beacons:  # 赏金猎人
            attrs[33] = attrs.get(33, 0) + 500

        # 终端天赋升级效果映射
        if 2001 in terminals:  # 商业直觉
            attrs[15] = attrs.get(15, 4) + 1
            attrs[16] = attrs.get(16, 3) + 1
        if 3002 in terminals:  # 免费刷新
            attrs[18] = attrs.get(18, 0) + 1
        if 3008 in terminals:  # 泉水强化
            attrs[17] = attrs.get(17, 1) + 1
        if 3012 in terminals:  # 大门重选
            attrs[7] = attrs.get(7, 0) + 2
        if 1101 in terminals:  # 核心重构
            attrs[1] = attrs.get(1, 1) + 1
            attrs[30] = attrs.get(30, 1) + 1
        if 3010 in terminals:  # 资金返还
            attrs[28] = attrs.get(28, 0) + 50

        # 8: 珍宝重骰次数（优先读取局内实时剩余 rolls 计数，确保客户端 x5 -> x4 -> x0 实时同步）
        if run_data and "rolls" in run_data:
            attrs[8] = max(0, int(run_data["rolls"]))
        else:
            base_rolls = 3
            if 3003 in terminals:
                base_rolls += 2
            attrs[8] = base_rolls

        # 格式化为 [{id: attr_id, value: val}]
        return [{"id": k, "value": v} for k, v in attrs.items() if 1 <= k <= 34]

    @classmethod
    def build_progress_obj(cls, run_data):
        """组装符合 p18_pb SC_18003 的完整字典结构"""
        if not run_data:
            return {}

        tier_id = run_data.get("tier_id", 1001)
        cur_event = run_data.get("current_event", {})
        cur_stage = run_data.get("current_stage", {})

        gate_net_list = []
        for g in cur_stage.get("gate_list", []):
            ev = g.get("event", {})
            gate_net_list.append({
                "index": g.get("index", 1),
                "event": {
                    "id": ev.get("id", 1001),
                    "stage_id": ev.get("stage_id", 0),
                    "reward_type": ev.get("reward_type", 0)
                }
            })

        reward_dict = cur_stage.get("reward") or {}
        reward_net = {
            "round": reward_dict.get("round", 0),
            "item_list": reward_dict.get("item_list", [])
        }

        shop_dict = cur_stage.get("shop")
        shop_net = None
        if shop_dict:
            shop_net = {
                "refresh_times": shop_dict.get("refresh_times", 0),
                "recover_times": shop_dict.get("recover_times", 0),
                "item_list": shop_dict.get("item_list", [])
            }

        stg_id = int(cur_stage.get("stage_id") or 0)
        if stg_id <= 0:
            stg_id = cls._pick_stage_id(tier_id, 1)

        stage_net = {
            "stage_id": stg_id,
            "save_point": cur_stage.get("save_point", 1),
            "reward": reward_net,
            "params": cur_stage.get("params", []),
            "gate_list": gate_net_list,
            "shop": shop_net,
            "attribute_modify_list": cur_stage.get("attribute_modify_list", [])
        }

        hero_net_list = []
        for h in run_data.get("hero_list", []):
            hero_net_list.append({
                "hero_id": h.get("hero_id", 1084),
                "template_id": h.get("template_id", 3051084),
                "health": h.get("health", 10000),
                "max_health": h.get("max_health", 10000),
                "reborn_cold_down": h.get("reborn_cold_down", 0),
                "difference_attribute_list": h.get("difference_attribute_list", []),
                "injured": h.get("injured", 0),
                "heal": h.get("heal", 0),
                "damage": h.get("damage", 0)
            })

        # 确保 effect_list 元素为对象字典 [{"id": eff_id}]
        formatted_effects = []
        for eff in run_data.get("effect_list", []):
            if isinstance(eff, dict):
                formatted_effects.append({"id": eff.get("id", 201)})
            elif isinstance(eff, int):
                formatted_effects.append({"id": eff})

        coins = run_data.get("coins", 600)
        stackable_items = [{"id": 1, "num": coins}]
        active_attrs = cls._compute_active_attributes(run_data)

        progress_obj = {
            "tier_id": tier_id,
            "event": {
                "id": cur_event.get("id", 1001),
                "stage_id": cur_event.get("stage_id", cur_stage.get("stage_id", 3035105)),
                "reward_type": cur_event.get("reward_type", 3001)
            },
            "stage": stage_net,
            "hero_list": hero_net_list,
            "fight_hero_id_list": run_data.get("fight_hero_id_list", [1084]),
            "artifact_list": run_data.get("artifact_list", []),
            "effect_list": formatted_effects,
            "attribute_list": active_attrs,
            "stackable_item_list": stackable_items,
            "cooperate_unique_skill_id": run_data.get("cooperate_unique_skill_id", 0)
        }
        return progress_obj
