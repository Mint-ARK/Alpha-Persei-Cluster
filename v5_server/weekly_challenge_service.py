# -*- coding: utf-8 -*-
"""
weekly_challenge_service.py — 四大高难周常玩法（梦境再构/黑区净化/多维变量/迭代校验）
核心服务：惰性时间戳周期刷新、本地配置驱动的怪物/BOSS/词缀轮换、Protobuf 响应构建
"""
import os
import json
import time
import datetime
import random

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------- 1. 周期与时间戳计算引擎（周四 05:00 / 周一与周四 05:00） -----------------

def get_next_thursday_5am(now_ts=None):
    """
    计算下一个周四 05:00:00（东八区/本地时间）的时间戳。
    用于：梦境再构 (Boss Challenge)、迭代校验 (Core Verification) 等周常。
    """
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    target_weekday = 3 # Thursday (0=Mon, ..., 3=Thu, ..., 6=Sun)
    days_ahead = target_weekday - dt.weekday()
    target_dt = dt.replace(hour=5, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_ahead)
    if target_dt.timestamp() <= now_ts:
        target_dt += datetime.timedelta(days=7)
    return int(target_dt.timestamp())

def get_next_monday_5am(now_ts=None):
    """
    计算下一个周一 05:00:00（东八区/本地时间）的时间戳。
    用于：多维变量 (Polyhedron) 周期轮换。
    """
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    target_weekday = 0 # Monday (0=Mon, ..., 6=Sun)
    days_ahead = target_weekday - dt.weekday()
    target_dt = dt.replace(hour=5, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_ahead)
    if target_dt.timestamp() <= now_ts:
        target_dt += datetime.timedelta(days=7)
    return int(target_dt.timestamp())

def get_next_semiweekly_5am(now_ts=None):
    """
    计算半周刷新（周一 05:00 或 周四 05:00）的下一个时间戳。
    用于：黑区净化 (Mythic / Hazard Zone) 失序深区周期轮换。
    """
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    candidates = []
    for target_weekday in [0, 3]: # Mon=0, Thu=3
        days_ahead = target_weekday - dt.weekday()
        cand = dt.replace(hour=5, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_ahead)
        if cand.timestamp() > now_ts:
            candidates.append(cand.timestamp())
        else:
            cand_next = cand + datetime.timedelta(days=7)
            candidates.append(cand_next.timestamp())
    return int(min(candidates))

def get_weekly_cycle_id(now_ts=None, base_anchor=1700000000):
    if now_ts is None:
        now_ts = int(time.time())
    return int((now_ts - base_anchor) // 604800)


# ----------------- 2. 配置资产加载器 -----------------

_CATALOGS = {}

def _load_catalog(filename):
    if filename not in _CATALOGS:
        p = os.path.join(BASE_DIR, filename)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                _CATALOGS[filename] = json.load(f)
        else:
            _CATALOGS[filename] = {}
    return _CATALOGS[filename]

def get_boss_catalog():
    data = _load_catalog("boss_challenge_data_complete.json")
    if not data:
        data = _load_catalog("boss_challenge_catalog.json")
    return data

def get_last_thursday_5am(now_ts=None):
    if now_ts is None:
        now_ts = int(time.time())
    next_thu = get_next_thursday_5am(now_ts)
    return next_thu - 7 * 86400

def get_daily_5am(now_ts=None):
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    today_5am = dt.replace(hour=5, minute=0, second=0, microsecond=0)
    if today_5am.timestamp() > now_ts:
        today_5am -= datetime.timedelta(days=1)
    return int(today_5am.timestamp())


# ----------------- 3. 梦境再构 (Boss Challenge) 数据构建 -----------------

def calc_boss_advance_score(diff_idx, time_idx_list, affix_idx_list, mode_id=101):
    """
    计算进阶梦境单首领得分：
    Score = 难度基础分(7000~14000) + 限时条件分(300~1000) + 自选词缀分之和(100~500/个)
    """
    cat = get_boss_catalog()
    modes = cat.get("advance_modes", {})
    m_info = modes.get(str(mode_id), modes.get(mode_id, {}))
    diff_points = m_info.get("difficult_point", [7000, 8500, 10000, 11000, 12000, 13000, 14000])
    
    score = 7000
    if 1 <= int(diff_idx) <= len(diff_points):
        score = diff_points[int(diff_idx) - 1]
        
    cond_pool = cat.get("condition_pool", {
        "1": {"point": 600}, "2": {"point": 500}, "3": {"point": 400},
        "4": {"point": 800}, "5": {"point": 1000}, "6": {"point": 300}
    })
    for tid in (time_idx_list or []):
        c_obj = cond_pool.get(str(tid), cond_pool.get(int(tid) if str(tid).isdigit() else 0, {}))
        score += int(c_obj.get("point") or 0)
        
    aff_pool = cat.get("affix_pool", {})
    for aid in (affix_idx_list or []):
        a_obj = aff_pool.get(str(aid), aff_pool.get(int(aid) if str(aid).isdigit() else 0, {}))
        score += int(a_obj.get("point") or 0)
        
    return int(score)


def get_boss_challenge_reward_items(reward_type, reward_val, area_id=4, mode_id=102):
    """
    根据 reward_type (1=星级 star, 2=积分 point) 和档位值，从 boss_challenge_data_complete 中提取真实道具掉落列表。
    """
    cat = get_boss_catalog()
    items = []
    if int(reward_type) == 1:
        areas = cat.get("normal_areas", {})
        area_obj = areas.get(str(area_id), areas.get(area_id, {}))
        for rew in area_obj.get("rewards", []):
            if int(rew.get("star", 0)) == int(reward_val):
                for d in rew.get("drops", []):
                    items.append({"id": int(d[0]), "num": int(d[1])})
                break
        if not items:
            for _, a_obj in areas.items():
                for rew in a_obj.get("rewards", []):
                    if int(rew.get("star", 0)) == int(reward_val):
                        for d in rew.get("drops", []):
                            items.append({"id": int(d[0]), "num": int(d[1])})
                        break
                if items:
                    break
    elif int(reward_type) == 2:
        modes = cat.get("advance_modes", {})
        mode_obj = modes.get(str(mode_id), modes.get(mode_id, {}))
        for rew in mode_obj.get("rewards", []):
            if int(rew.get("point", 0)) == int(reward_val):
                for d in rew.get("drops", []):
                    items.append({"id": int(d[0]), "num": int(d[1])})
                break
        if not items:
            for _, m_obj in modes.items():
                for rew in m_obj.get("rewards", []):
                    if int(rew.get("point", 0)) == int(reward_val):
                        for d in rew.get("drops", []):
                            items.append({"id": int(d[0]), "num": int(d[1])})
                        break
                if items:
                    break
    # 兜底
    if not items:
        if int(reward_type) == 1:
            items = [{"id": 1, "num": 50}, {"id": 2, "num": 30000}]
        else:
            items = [{"id": 1, "num": 50}, {"id": 41, "num": 7}, {"id": 37, "num": 24}]
    return items


def get_boss_challenge_data(uid, db):
    """
    返回梦境再构当期动态数据：
    - sc_45201: mode, next_refresh_time, difficulty_list
    - sc_45001: 普通梦境 (boss_challenge_list, use_times, receive_star_list, area_id)
    - sc_45101: 进阶梦境 (advance_id, boss_list, receive_point_list)
    """
    now = int(time.time())
    next_rf = get_next_thursday_5am(now)
    cycle = get_weekly_cycle_id(now)
    cat = get_boss_catalog()
    
    if db and hasattr(db, "check_and_lazy_refresh_boss_challenge"):
        try:
            db.check_and_lazy_refresh_boss_challenge(uid, now)
        except Exception:
            pass

    # 1. 读取玩家梦境再构进度与模式
    user_saved_affixes = {}
    user_points = {}
    adv_heroes_map = {}
    adv_presets_map = {}
    
    normal_db_map = {}
    claimed_points = []
    claimed_stars = []
    user_mode = 0
    use_times = 0
    area_id = 4 # 默认异相层 (Lv80-100)
    advance_id = 101 # 默认扭曲梦境Ⅰ
    
    if db:
        try:
            prog = db.get_boss_challenge_progress(uid) if hasattr(db, "get_boss_challenge_progress") else None
            if not prog:
                rows = db.query("SELECT * FROM boss_challenge_progress WHERE uid=?", (uid,))
                prog = rows[0] if rows else {}
            if prog:
                raw_mode = int(prog.get("mode") or 0)
                use_times = int(prog.get("use_times") or 0)
                area_id = int(prog.get("area_id") or 4)
                advance_id = int(prog.get("advance_id") or 101)
                if raw_mode in (3, 4):
                    area_id = raw_mode
                    user_mode = 1
                elif raw_mode in (101, 102):
                    advance_id = raw_mode
                    user_mode = 2
                elif raw_mode in (1, 2):
                    user_mode = raw_mode
                else:
                    user_mode = 0
        except Exception:
            pass
            
        try:
            aff_rows = db.query("SELECT * FROM boss_challenge_affixes WHERE uid=?", (uid,))
            for r in aff_rows:
                user_saved_affixes[int(r["boss_id"])] = {
                    "affix_list": json.loads(r.get("affix_index_list") or "[]"),
                    "time_list": json.loads(r.get("time_index_list") or "[]"),
                    "diff_idx": int(r.get("diffculty_index") or 1)
                }
        except Exception:
            pass
            
        try:
            adv_rows = db.query("SELECT * FROM boss_challenge_advance WHERE uid=?", (uid,))
            for r in adv_rows:
                bid = int(r["boss_id"])
                user_points[bid] = int(r.get("score") or 0)
                adv_heroes_map[bid] = json.loads(r.get("used_heroes") or "[]")
                adv_presets_map[bid] = json.loads(r.get("last_heroes_cfg") or "[]")
        except Exception:
            pass
            
        try:
            score_rows = db.query("SELECT * FROM boss_challenge_scores WHERE uid=?", (uid,))
            for r in score_rows:
                bid = int(r["boss_id"])
                if bid not in user_points or user_points[bid] == 0:
                    user_points[bid] = int(r.get("score") or 0)
        except Exception:
            pass

        try:
            norm_rows = db.query("SELECT * FROM boss_challenge_normal WHERE uid=?", (uid,))
            for r in norm_rows:
                gid = int(r["group_id"])
                normal_db_map[gid] = {
                    "finish_stage": int(r.get("finish_stage") or 0),
                    "used_heroes": json.loads(r.get("used_heroes") or "[]"),
                    "last_heroes_cfg": json.loads(r.get("last_heroes_cfg") or "[]"),
                    "star_info": json.loads(r.get("star_info") or "[]")
                }
        except Exception:
            pass
            
        try:
            reward_rows = db.query("SELECT * FROM boss_challenge_claimed_rewards WHERE uid=?", (uid,))
            for r in reward_rows:
                if r.get("reward_type") == 1:
                    claimed_stars.append(int(r["reward_id"]))
                elif r.get("reward_type") == 2:
                    claimed_points.append(int(r["reward_id"]))
        except Exception:
            pass

    # 2. 进阶/扭曲梦境 BOSS 抽取（按周期确定性抽取 3 或 4 位首领）
    adv_pool = cat.get("advance_pool") or cat.get("advance_boss_pool", {})
    pool_keys = [int(k) for k in adv_pool.keys() if str(k).isdigit()]
    if not pool_keys:
        pool_keys = [20001, 20002, 20003, 20004]
    pool_keys.sort()
        
    b_nums = 4 if advance_id == 102 else 3
    rnd_adv = random.Random(cycle * 10007 + 45101)
    selected_pool_ids = rnd_adv.sample(pool_keys, min(b_nums, len(pool_keys)))

    adv_boss_list = []
    for bid in selected_pool_ids:
        b_info = adv_pool.get(str(bid), adv_pool.get(bid, {}))
        aff_pool_raw = b_info.get("affix_pool", [])
        aff_pool = [x.get("id", x) if isinstance(x, dict) else x for x in aff_pool_raw]
        aff_conf = user_saved_affixes.get(bid, {
            "affix_list": [],
            "time_list": [],
            "diff_idx": 1
        })
        adv_boss_list.append({
            "id": int(bid),
            "unlock_timestamp": now - 86400,
            "max_point": user_points.get(bid, 0),
            "used_heroes": adv_heroes_map.get(bid, []),
            "last_heroes_cfg": adv_presets_map.get(bid, [0, 0, 0]),
            "affix_index_list": aff_conf.get("affix_list", []),
            "time_index_list": aff_conf.get("time_list", []),
            "diffculty_index": aff_conf.get("diff_idx", 1)
        })

    # 3. 普通梦境首领抽取（Area 3/4 3位首领）
    norm_areas = cat.get("normal_areas", {})
    cur_area = norm_areas.get(str(area_id), norm_areas.get(area_id, {}))
    area_groups = cur_area.get("boss_groups") or [5009, 5010, 5023, 5024, 5025, 5026]
    area_boss_nums = cur_area.get("boss_nums") or 3
    
    rnd_norm = random.Random(cycle * 10007 + 45001)
    selected_groups = rnd_norm.sample(area_groups, min(area_boss_nums, len(area_groups)))
    
    stage_groups_cat = cat.get("stage_groups", {})
    normal_boss_list = []
    for gid in selected_groups:
        nd = normal_db_map.get(gid, {})
        g_info = stage_groups_cat.get(str(gid), stage_groups_cat.get(gid, {}))
        g_stages = g_info.get("stages", [])
        
        finish_stg = nd.get("finish_stage") or 0
        star_inf = nd.get("star_info") or []
        if not star_inf and finish_stg > 0 and g_stages:
            star_inf = [{"stage_id": finish_stg, "star_list": [1, 1, 1]}]
            
        normal_boss_list.append({
            "group_id": int(gid),
            "finish_stage": finish_stg,
            "used_heroes": nd.get("used_heroes", []),
            "last_heroes_cfg": nd.get("last_heroes_cfg", [0, 0, 0]),
            "star_info": star_inf,
            "unlock_timestamp": now - 86400
        })

    sc_45201_obj = {
        "mode": int(user_mode),
        "next_refresh_time": next_rf,
        "difficulty_list": [3, 4, 101, 102]
    }
    
    sc_45001_obj = {
        "use_times": use_times,
        "boss_challenge_list": normal_boss_list,
        "receive_star_list": claimed_stars,
        "area_id": int(area_id)
    }
    
    sc_45101_obj = {
        "advance_id": int(advance_id),
        "boss_list": adv_boss_list,
        "receive_point_list": claimed_points
    }
    
    return sc_45201_obj, sc_45001_obj, sc_45101_obj


# ----------------- 4. 黑区净化 (Mythic) 数据构建 -----------------

def get_mythic_data(uid, db):
    """
    返回黑区净化当期动态数据：
    - sc_44007: difficulty_list, superiority_affix_list, inferiority_affix_list, mythic_ultimate_affix_list, next_refresh_timestamp, recommend_team
    - sc_44009: clear_partition_id_list, main_partition_star_list, star_reward_provide_list
    - sc_44019: open_difficulty_list, difficulty, is_new_difficulty
    - sc_44021: stage_list
    - sc_44023: difficulty_id_can_choose, now_difficulty, receive_reward, clear_list, is_new_difficulty, challenge_info
    """
    import mythic_affix_cfg
    now = int(time.time())
    rot = mythic_affix_cfg.get_mythic_rotation(now)
    
    # 完整 13 档常规难度与真实分区 stage_id
    diff_list = []
    for d in range(1, 14):
        diff_list.append({
            "difficulty": d,
            "main_partition": {"partition": d, "stage_id": 3026000 + d},
            "sub_partition_list": [
                {"partition": d * 100 + 1, "stage_id": 3027000 + (d - 1) * 3 + 1},
                {"partition": d * 100 + 2, "stage_id": 3027000 + (d - 1) * 3 + 2},
                {"partition": d * 100 + 3, "stage_id": 3027000 + (d - 1) * 3 + 3}
            ]
        })
        
    user_diff = 10 # 默认选中难度 10 (可自由切换 1~13 档)
    claimed_star_rewards = []
    clear_partitions = []
    main_stars = [1, 2, 3]
    
    if db:
        try:
            pub_rows = db.query("SELECT * FROM mythic_public WHERE uid=?", (uid,))
            if pub_rows and pub_rows[0].get("current_difficulty") is not None and pub_rows[0].get("current_difficulty") > 0:
                user_diff = pub_rows[0].get("current_difficulty")
        except Exception:
            pass
            
        try:
            r_rows = db.query("SELECT reward_id FROM mythic_progress_reward WHERE uid=?", (uid,))
            claimed_star_rewards = [r["reward_id"] for r in r_rows]
        except Exception:
            pass
            
        try:
            p_rows = db.query("SELECT partition_id, star FROM mythic_progress_partition WHERE uid=?", (uid,))
            if p_rows:
                clear_partitions = [r["partition_id"] for r in p_rows]
        except Exception:
            pass

    # sc_44007: 全量词条与半周轮换数据
    sc_44007_obj = {
        "difficulty_list": diff_list,
        "superiority_affix_list": rot["superiority_affixes"],
        "inferiority_affix_list": rot["inferiority_affixes"],
        "mythic_ultimate_affix_list": rot["ultimate_affixes"],
        "next_refresh_timestamp": rot["next_refresh_timestamp"],
        "recommend_team": rot["recommend_team"]
    }
    
    # sc_44009: 常规黑区通关分区、星数与已领奖励
    sc_44009_obj = {
        "clear_partition_id_list": clear_partitions,
        "main_partition_star_list": main_stars,
        "star_reward_provide_list": claimed_star_rewards
    }
    
    # sc_44019: 已开放难度与当前选中难度
    sc_44019_obj = {
        "open_difficulty_list": [1001, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
        "difficulty": user_diff,
        "is_new_difficulty": False
    }
    
    # sc_44021: 30 档最终失序深区关卡列表 (1~19 档单关，20~30 档双关)
    stage_list = []
    for d in range(1, 31):
        if d <= 19:
            stage_list.append({"difficulty_id": d, "stage_id": [3028000 + d]})
        else:
            base_s = 3028020 + (d - 20) * 2
            stage_list.append({"difficulty_id": d, "stage_id": [base_s, base_s + 1]})
        
    sc_44021_obj = {
        "stage_list": stage_list
    }
    
    # sc_44023: 失序深区进度、可选档位、已通关、奖励领取与多队挑战状态
    final_prog = {
        "now_difficulty": 30,
        "can_choose_list": list(range(1, 31)),
        "receive_reward": [],
        "clear_list": [],
        "challenge_info": [],
        "is_new_difficulty": False
    }
    if db and hasattr(db, "get_mythic_final_progress"):
        try:
            final_prog = db.get_mythic_final_progress(uid)
        except Exception:
            pass
            
    sc_44023_obj = {
        "difficulty_id_can_choose": final_prog.get("can_choose_list", list(range(1, 31))),
        "now_difficulty": final_prog.get("now_difficulty", 30),
        "receive_reward": final_prog.get("receive_reward", []),
        "clear_list": final_prog.get("clear_list", []),
        "is_new_difficulty": final_prog.get("is_new_difficulty", False),
        "challenge_info": final_prog.get("challenge_info", [])
    }
    
    return sc_44007_obj, sc_44009_obj, sc_44019_obj, sc_44021_obj, sc_44023_obj


# ----------------- 5. 多维变量 (Polyhedron) 数据构建 -----------------

def get_polyhedron_data(uid, db, res_version=None):
    """
    返回多维变量当期动态数据：
    - sc_18001: 完整多维变量大厅与终端数据（由 account.db 动态驱动）
    """
    now = int(time.time())
    import res_version_manager
    activity_id = int(res_version_manager.get_version_config(res_version)["polyhedron_activity_id"])
    
    # 1. 玩家已解锁英雄及星盘配置（从 polyhedron_hero 表读取）
    hero_rows = db.query("SELECT hero_id, astrolabe_1, astrolabe_2, astrolabe_3 FROM polyhedron_hero WHERE uid = ?", (uid,))
    unlocked_heroes = []
    for hr in hero_rows:
        astros = [x for x in [hr.get("astrolabe_1"), hr.get("astrolabe_2"), hr.get("astrolabe_3")] if x and x > 0]
        unlocked_heroes.append({
            "hero_id": hr["hero_id"],
            "astrolabe_id_list": astros
        })
    if not unlocked_heroes:
        default_init_heroes = [1084, 1094, 1284]
        for hid in default_init_heroes:
            unlocked_heroes.append({"hero_id": hid, "astrolabe_id_list": []})
            db.upsert("polyhedron_hero", uid, {
                "hero_id": hid,
                "astrolabe_1": 0,
                "astrolabe_2": 0,
                "astrolabe_3": 0,
                "update_ts": now
            }, keys=("uid", "hero_id"))

    # 2. 已解锁信标列表
    beacon_rows = db.query("SELECT beacon_id FROM polyhedron_beacon WHERE uid = ?", (uid,))
    if beacon_rows:
        beacon_id_list = [r["beacon_id"] for r in beacon_rows]
    else:
        beacon_id_list = list(range(1, 16))

    # 3. 终端升级天赋列表
    term_rows = db.query("SELECT terminal_id FROM polyhedron_terminal WHERE uid = ?", (uid,))
    if term_rows:
        terminal_upgrades = [r["terminal_id"] for r in term_rows]
    else:
        terminal_upgrades = [
            2001, 3001, 3009, 3002, 3008, 3003, 3012, 3010,
            1001, 1002, 1018, 1006, 1003, 1010, 1007, 1014,
            1011, 1015, 1008, 1012, 1016, 1004, 1009, 1013,
            1017, 1005, 3011, 3004, 3005, 3006, 3007
        ]

    # 4. 已通关难度列表
    diff_rows = db.query("SELECT difficulty_id FROM polyhedron_difficulty WHERE uid = ? ORDER BY difficulty_id DESC", (uid,))
    if diff_rows:
        clear_difficulties = [r["difficulty_id"] for r in diff_rows]
    else:
        clear_difficulties = [19, 18, 17, 16, 11, 6, 1]

    # 5. 图鉴样本
    art_rows = db.query("SELECT artifact_id, state FROM polyhedron_artifact WHERE uid = ?", (uid,))
    if art_rows:
        manual_samples = [{"id": r["artifact_id"], "state": r["state"]} for r in art_rows]
    else:
        manual_samples = [
            {"id": 70562, "state": 2}, {"id": 70563, "state": 2}, {"id": 70811, "state": 2},
            {"id": 70805, "state": 2}, {"id": 70814, "state": 1}, {"id": 70806, "state": 2},
            {"id": 70810, "state": 2}, {"id": 70807, "state": 2}, {"id": 70561, "state": 2},
            {"id": 70551, "state": 2}, {"id": 70552, "state": 2}, {"id": 109405, "state": 2},
            {"id": 70812, "state": 2}, {"id": 70809, "state": 2}, {"id": 70714, "state": 2},
            {"id": 70704, "state": 2}, {"id": 70610, "state": 1}, {"id": 70615, "state": 1},
            {"id": 70609, "state": 2}, {"id": 109402, "state": 1}, {"id": 109401, "state": 2}
        ]

    # 6. 历程周期性刷新检测（仅重置维度偏移 Currency 45 与已领取记录）
    try:
        from polyhedron_service import PolyhedronRunManager
        PolyhedronRunManager.check_and_refresh_policy_cycle(uid, db, activity_id=activity_id)
    except Exception as _ce:
        pass

    # 7. Meta 元数据（游戏局内状态、重置次数、红点等）
    meta = db.get("polyhedron_meta", uid, "AND activity_id=?", (activity_id,))
    if not meta:
        meta = {
            "game_state": 1,
            "reset_times": 1,
            "already_challenge_times": 0,
            "is_new": 0
        }
        db.upsert("polyhedron_meta", uid, {
            "activity_id": activity_id,
            "game_state": 1,
            "reset_times": 1,
            "already_challenge_times": 0,
            "is_new": 0,
            "update_ts": now
        }, keys=("uid", "activity_id"))

    # 7. 历程已领取等级列表
    policy_rows = db.query("SELECT level FROM polyhedron_policy_claimed WHERE uid=? AND activity_id=?", (uid, activity_id))
    apply_id_list = [r["level"] for r in policy_rows] if policy_rows else []

    # 8. 局内进行中的对局状态恢复
    game_state = meta.get("game_state", 1)
    game_obj = {
        "state": game_state
    }
    try:
        from polyhedron_service import PolyhedronRunManager
        active_run = PolyhedronRunManager.load_run(uid, db)
        if active_run and active_run.get("state") in (2, 3):
            game_obj["state"] = active_run.get("state")
            game_obj["start_info"] = active_run.get("start_info", {})
            game_obj["progress"] = PolyhedronRunManager.build_progress_obj(active_run)
    except Exception as e:
        pass

    sc_18001_obj = {
        "game": game_obj,
        "decision": {
            "apply_id_list": apply_id_list
        },
        "terminal": {
            "reset_times": meta.get("reset_times", 1),
            "upgrade_id_list": terminal_upgrades
        },
        "beacon_id_list": beacon_id_list,
        "unlocked_hero_list": unlocked_heroes,
        "activity_id": activity_id,
        "clear_difficulty_list": clear_difficulties,
        "manual": {
            "sample_list": manual_samples
        },
        "already_challenge_times": meta.get("already_challenge_times", 0),
        "is_new": meta.get("is_new", 0)
    }
    
    return sc_18001_obj


# ----------------- 6. 迭代校验 (Core Verification) 数据构建 -----------------

def get_core_verification_catalog():
    return _load_catalog("core_verification_catalog.json")

def get_core_verification_cl_catalog():
    return _load_catalog("core_verification_cl_catalog.json")

def get_core_verification_rewards_catalog():
    return _load_catalog("core_verification_rewards_complete.json")

def get_core_verification_custom_affix_catalog():
    """获取迭代校验自定义词条库（常规与特殊）"""
    return _load_catalog("core_verification_affix_custom.json")

def get_core_verification_cycle(now_ts=None):
    """
    计算当前迭代校验周期（1~16 轮换）。
    """
    if now_ts is None:
        now_ts = int(time.time())
    base_anchor = 1700000000
    cycle_idx = int((now_ts - base_anchor) // 604800)
    return (cycle_idx % 16) + 1

def calc_core_verification_super_score(info_id, diff=8, battle_time_ms=0, affix_list=None):
    """
    核算极值挑战得分：
    基础通关分（10000）+ 词条加权（每个+800）+ 时间加成（最高3000）。
    """
    affix_list = affix_list or []
    base_score = 10000
    affix_score = len(affix_list) * 800
    time_sec = battle_time_ms / 1000.0 if battle_time_ms > 1000 else float(battle_time_ms)
    time_bonus = max(0, int(3000 - time_sec * 20))
    return base_score + affix_score + time_bonus

def get_core_verification_data(uid, db):
    """
    返回迭代校验常规当期动态数据（构建 sc_75009 字典结构）：
    - now_cycle: 1~16
    - next_cycle: 下一周期
    - stage_info: 16 关列表 [Boss1 8关 + Boss2 8关]
    - reward_list: 已领奖励 ID 列表
    - lock_list: [{boss_type: 1, hero_list: [...]}, {boss_type: 2, hero_list: [...]}]
    - refresh_timestamp: 下个周四 05:00
    - affix_list: 玩家保存的自选词缀
    - max_score_info: {lock_list: [{boss_type: 1, hero_list: [...], score: ...}, ...]}
    """
    now = int(time.time())
    next_rf = get_next_thursday_5am(now)
    now_cycle = get_core_verification_cycle(now)
    next_cycle = (now_cycle % 16) + 1

    cat = get_core_verification_catalog()
    cycle_info = cat.get("cycle_info", {})

    b1_stages = []
    b2_stages = []
    for sid_str, info in cycle_info.items():
        if int(info.get("cycle", 0)) == now_cycle:
            if int(info.get("boss_type", 0)) == 1:
                b1_stages.append(info)
            elif int(info.get("boss_type", 0)) == 2:
                b2_stages.append(info)

    b1_stages.sort(key=lambda x: int(x.get("difficult", 0)))
    b2_stages.sort(key=lambda x: int(x.get("difficult", 0)))

    if not b1_stages:
        for d in range(1, 9):
            b1_stages.append({"id": now_cycle * 1000 + 100 + d, "boss_type": 1, "difficult": d})
    if not b2_stages:
        for d in range(1, 9):
            b2_stages.append({"id": now_cycle * 1000 + 200 + d, "boss_type": 2, "difficult": d})

    records = {}
    locks = {1: [], 2: []}
    claimed_rewards = []
    affixes = []
    super_scores = {}

    if db:
        try:
            if hasattr(db, "get_core_verification_stage_records"):
                records = db.get_core_verification_stage_records(uid, now_cycle)
            if hasattr(db, "get_core_verification_hero_locks"):
                locks = db.get_core_verification_hero_locks(uid, now_cycle)
            if hasattr(db, "get_core_verification_claimed_tasks"):
                claimed_rewards = db.get_core_verification_claimed_tasks(uid, now_cycle)
            if hasattr(db, "get_core_verification_affixes"):
                affixes = db.get_core_verification_affixes(uid)
            if hasattr(db, "get_core_verification_super_scores"):
                super_scores = db.get_core_verification_super_scores(uid)
        except Exception:
            pass

    stage_info_list = []
    for s in b1_stages + b2_stages:
        sid = int(s["id"])
        rec = records.get(sid, {})
        if int(rec.get("sign", 0)) == 1:
            stage_info_list.append({
                "id": sid,
                "sign": 1,
                "min_time": int(rec.get("min_time", 0)),
                "score": int(rec.get("score", 0))
            })

    b1_super_stage = b1_stages[-1]["id"] if b1_stages else 0
    b2_super_stage = b2_stages[-1]["id"] if b2_stages else 0
    b1_score = super_scores.get(b1_super_stage) or records.get(b1_super_stage, {}).get("score", 0)
    b2_score = super_scores.get(b2_super_stage) or records.get(b2_super_stage, {}).get("score", 0)

    sc_75009_obj = {
        "now_cycle": int(now_cycle),
        "next_cycle": int(next_cycle),
        "stage_info": stage_info_list,
        "reward_list": claimed_rewards,
        "lock_list": [
            {"boss_type": 1, "hero_list": locks.get(1, []), "score": int(b1_score)},
            {"boss_type": 2, "hero_list": locks.get(2, []), "score": int(b2_score)}
        ],
        "refresh_timestamp": int(next_rf),
        "affix_list": affixes,
        "max_score_info": {
            "lock_list": [
                {"boss_type": 1, "hero_list": locks.get(1, []), "score": int(b1_score)},
                {"boss_type": 2, "hero_list": locks.get(2, []), "score": int(b2_score)}
            ]
        }
    }
    return sc_75009_obj

# ----------------- 迭代校验·挑战模式 (Challenge Mode) 数据构建 -----------------

def get_core_verification_challenge_data(uid, db, activity_id=None):
    """
    根据 activity_id 构建挑战模式 (Mode 1~4) 动态协议数据。
    返回: (mode_num, sc_payload_obj, sc_89021_obj)
    """
    now = int(time.time())
    cl_cat = get_core_verification_cl_catalog()
    if activity_id is None:
        activity_id = 3539501

    activity_id = int(activity_id)
    prog = {}
    claimed_tasks = []
    badges = []
    if db:
        try:
            if hasattr(db, "get_core_verification_cl_progress"):
                prog = db.get_core_verification_cl_progress(uid, activity_id)
            if hasattr(db, "get_core_verification_cl_claimed_tasks"):
                claimed_tasks = db.get_core_verification_cl_claimed_tasks(uid, activity_id)
            if hasattr(db, "get_core_verification_cl_badges"):
                badges = db.get_core_verification_cl_badges(uid)
        except Exception:
            pass

    mode_num = 1
    stages_pool = {}
    for mkey in ["mode1", "mode2", "mode3", "mode4"]:
        m_stages = cl_cat.get(mkey, {})
        for sid, sinfo in m_stages.items():
            if int(sinfo.get("activity_id", 0)) == activity_id:
                mode_num = int(sinfo.get("mode", 1))
                stages_pool = m_stages
                break
        if stages_pool:
            break

    if not stages_pool:
        stages_pool = cl_cat.get("mode1", {})
        mode_num = 1

    common_stages_cfg = [s for s in stages_pool.values() if int(s.get("activity_id", 0)) == activity_id and int(s.get("stage_type", 0)) == 1]
    challenge_stages_cfg = [s for s in stages_pool.values() if int(s.get("activity_id", 0)) == activity_id and int(s.get("stage_type", 0)) == 2]

    unlocked_buffs = []
    total_cost_limit = 0
    common_stage_data_list = []
    for cs in common_stages_cfg:
        sid = int(cs["stage_id"])
        c_prog = prog.get(sid, {})
        is_clr = (c_prog.get("is_cleared", 0) == 1)
        if is_clr:
            unlocked_buffs.extend(cs.get("stage_buff", []))
            total_cost_limit += int(cs.get("cost_limit_up", 0))
        common_stage_data_list.append({
            "stage_id": sid,
            "is_cleared": is_clr,
            "heroes": c_prog.get("heroes", [])
        })

    main_stage_cfg = challenge_stages_cfg[0] if challenge_stages_cfg else {}
    main_sid = int(main_stage_cfg.get("stage_id", 3093001))
    main_prog = prog.get(main_sid, {})
    selected_buffs = main_prog.get("select_buffs", [])

    task_items = [{"assignment_id": int(tid), "state": 2} for tid in claimed_tasks]
    formatted_buffs = [
        {"buff_id": int(b), "level": 1, "type": 1} if isinstance(b, int) else b
        for b in set(unlocked_buffs)
    ]
    formatted_badges = [
        {"illustrated_id": int(b.get("id", b.get("illustrated_id", 0))), "time": int(b.get("unlock_timestamp", b.get("time", 0)))}
        for b in badges
    ]

    if mode_num == 1:
        sc_obj = {
            "challenge_stage": {
                "stage_id": main_sid,
                "max_challenge_value": int(main_prog.get("score", 0)),
                "recently_challenge_value": int(main_prog.get("score", 0)),
                "challenge_lock": [],
                "challenge_buff": selected_buffs
            },
            "common_stage": [{"stage_id": s["stage_id"], "common_lock_id": []} for s in common_stage_data_list],
            "buff_list": formatted_buffs,
            "finish_assignment_list": task_items,
            "first_enter": False
        }
    elif mode_num == 2:
        sc_obj = {
            "challenge_stage": {
                "stage_id": main_sid,
                "max_challenge_value": int(main_prog.get("score", 0)),
                "recently_challenge_value": int(main_prog.get("score", 0)),
                "cost_limit": total_cost_limit,
                "challenge_buff": selected_buffs
            },
            "common_stage": [{"stage_id": s["stage_id"], "common_lock_id": []} for s in common_stage_data_list],
            "finish_assignment_list": task_items,
            "first_enter": False
        }
    elif mode_num == 3:
        sc_obj = {
            "challenge_stage": {
                "stage_id": main_sid,
                "max_challenge_value": int(main_prog.get("score", 0)),
                "recently_challenge_value": int(main_prog.get("score", 0)),
                "challenge_buff": selected_buffs
            },
            "common_stage": [{"stage_id": s["stage_id"], "common_lock_id": []} for s in common_stage_data_list],
            "finish_assignment_list": task_items,
            "first_enter": False
        }
    else:
        # Mode 4 (sc_89801)
        c_stages = []
        for s in common_stage_data_list:
            c_stages.append({
                "stage_id": s["stage_id"],
                "common_locks": []
            })
        sc_obj = {
            "common_stages": c_stages,
            "finish_assignment_list": task_items,
            "first_enter": False,
            "max_point": int(main_prog.get("score", 0))
        }

    sc_89021_obj = {
        "illustrated": formatted_badges
    }

    return mode_num, sc_obj, sc_89021_obj


# ----------------- 7. 介质攫取 (Equip Seizure) 数据构建与调度 -----------------

def get_equip_seizure_catalog():
    return _load_catalog("equip_seizure_catalog.json")

def get_next_seizure_affix_refresh(now_ts=None):
    """
    计算介质攫取增益词缀刷新（周一 05:00 / 周四 05:00 / 周六 05:00）的下一个时间戳。
    """
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    candidates = []
    for target_weekday in [0, 3, 5]: # Mon=0, Thu=3, Sat=5
        days_ahead = target_weekday - dt.weekday()
        cand = dt.replace(hour=5, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_ahead)
        if cand.timestamp() > now_ts:
            candidates.append(cand.timestamp())
        else:
            cand_next = cand + datetime.timedelta(days=7)
            candidates.append(cand_next.timestamp())
    return int(min(candidates))

def get_seizure_challenge_rate(now_ts=None):
    """
    介质攫取积分倍率：周六、周日 05:00 起为 1.2 倍，周一至周五 05:00 为 1.0 倍。
    """
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    if dt.hour < 5:
        eff_dt = dt - datetime.timedelta(days=1)
    else:
        eff_dt = dt
    if eff_dt.weekday() in (5, 6): # Saturday=5, Sunday=6
        return 1.2
    return 1.0

def get_daily_seizure_stage_id(now_ts=None):
    """
    每日 05:00 轮换一个关卡（从 26 个关卡中循环）。
    """
    if now_ts is None:
        now_ts = int(time.time())
    cat = get_equip_seizure_catalog()
    stages = cat.get("stages") or [
        3070101, 3070102, 3070103, 3070104, 3070105, 3070106, 3070107, 3070108, 3070109, 3070110,
        3070111, 3070112, 3070113, 3070114, 3070115, 3070116, 3070117, 3070118, 3070119, 3070120,
        3070121, 3070122, 3070123, 3070124, 3070125, 3070126
    ]
    dt = datetime.datetime.fromtimestamp(now_ts)
    if dt.hour < 5:
        eff_dt = dt - datetime.timedelta(days=1)
    else:
        eff_dt = dt
    base_date = datetime.date(2026, 1, 1)
    day_idx = (eff_dt.date() - base_date).days
    return stages[day_idx % len(stages)]

def get_seizure_cycle_index(now_ts=None):
    """
    计算介质攫取词缀轮换期次索引（每周3期：周一 05:00、周四 05:00、周六 05:00 切换）。
    """
    if now_ts is None:
        now_ts = int(time.time())
    dt = datetime.datetime.fromtimestamp(now_ts)
    if dt.hour < 5:
        eff_dt = dt - datetime.timedelta(days=1)
    else:
        eff_dt = dt
    # 基准锚点：2026-01-05（周一）
    base_monday = datetime.date(2026, 1, 5)
    days = (eff_dt.date() - base_monday).days
    weeks = days // 7
    weekday = eff_dt.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    if weekday in (0, 1, 2):
        sub_period = 0  # 周一 05:00 ~ 周四 05:00
    elif weekday in (3, 4):
        sub_period = 1  # 周四 05:00 ~ 周六 05:00
    else:
        sub_period = 2  # 周六 05:00 ~ 周一 05:00
    return weeks * 3 + sub_period

def get_seizure_affixes(now_ts=None):
    """
    根据当期词缀周期选择 2 个严格互补的增益词缀：
    - 词缀 1：属性偏移场（9201~9208，特定元素易伤 +100%）
    - 词缀 2：屏蔽无效化（9215~9222，敌人该元素以外抗性提高 90%，即唯独该元素不受减免）
    二者严格同元素匹配，确保玩家使用当期推荐属性角色出战。
    """
    cycle_idx = get_seizure_cycle_index(now_ts)
    elem_idx = cycle_idx % 8
    attr_affix = 9201 + elem_idx
    shield_affix = 9215 + elem_idx
    return [attr_affix, shield_affix]

def get_equip_seizure_data(uid, db):
    """
    返回介质攫取当期动态数据（构建 sc_35011 字典结构）：
    - stage_id
    - challenge_rate
    - affix_info: {affix_id_list, refresh_timestamp}
    - today_max_score
    - sum_score
    - refresh_timestamp
    - got_reward_id_list
    """
    now = int(time.time())
    next_weekly_rf = get_next_thursday_5am(now)
    next_affix_rf = get_next_seizure_affix_refresh(now)
    current_rate = get_seizure_challenge_rate(now)
    current_stage = get_daily_seizure_stage_id(now)
    current_affixes = get_seizure_affixes(now)
    
    prog = {
        "stage_id": current_stage,
        "challenge_rate": current_rate,
        "affix_id_list": current_affixes,
        "affix_refresh_ts": next_affix_rf,
        "today_max_score": 0,
        "sum_score": 0,
        "refresh_ts": next_weekly_rf,
        "got_reward_id_list": [],
        "last_day_tag": get_daily_5am(now)
    }
    
    if db:
        try:
            if hasattr(db, "get_equip_seizure_progress"):
                prog = db.get_equip_seizure_progress(uid, now)
        except Exception:
            pass
            
    sc_35011_obj = {
        "stage_id": int(prog.get("stage_id") or current_stage),
        "challenge_rate": float(prog.get("challenge_rate") or current_rate),
        "affix_info": {
            "affix_id_list": prog.get("affix_id_list") or current_affixes,
            "refresh_timestamp": int(prog.get("affix_refresh_ts") or next_affix_rf)
        },
        "today_max_score": int(prog.get("today_max_score") or 0),
        "sum_score": int(prog.get("sum_score") or 0),
        "refresh_timestamp": int(prog.get("refresh_ts") or next_weekly_rf),
        "got_reward_id_list": prog.get("got_reward_id_list") or []
    }
    return sc_35011_obj


def calc_equip_seizure_score(enemy_dead=0, battle_time_ms=0, explicit_score=0, is_win=False):
    """
    核算介质攫取作战得分（基于击杀怪物量/波次与时间阶梯折算 + Boss 累进悬赏）。
    对齐实机核心战绩锚点：
    - 13 波（~60怪）：约 3,000 分；
    - 31 波（~142怪）：约 11,400 分；
    - 35 波（~156怪）：约 13,800 分；
    - 37 波（~165怪）：约 14,600 分；
    - 38~39 波（~174怪）：约 15,000 ~ 15,400 分；
    - 45+ 波极限冲层：达 20,000 ~ 24,000 满档。
    """
    if explicit_score > 0:
        return int(explicit_score), 0
        
    if enemy_dead > 0:
        if enemy_dead <= 91:
            est_waves = max(1, int(round(enemy_dead / 4.8)))
        else:
            # 20波及以上（每5波包含4波杂兵+1波单体Boss）
            rem_dead = enemy_dead - 91
            cycles = rem_dead / 20.2
            est_waves = max(1, int(round(19 + cycles * 5)))
    elif battle_time_ms > 0:
        sec = battle_time_ms / 1000.0 if battle_time_ms > 1000 else float(battle_time_ms)
        est_waves = max(1, int(sec / 15.0))
    elif is_win:
        est_waves = 40
    else:
        est_waves = 1

    score = 0
    # 基础波次分（阶梯递进）
    # 1-10 波: 220/波
    score += min(est_waves, 10) * 220
    # 11-20 波: 280/波
    if est_waves > 10:
        score += min(est_waves - 10, 10) * 280
    # 21-30 波: 420/波
    if est_waves > 20:
        score += min(est_waves - 20, 10) * 420
    # 31-40 波: 400/波
    if est_waves > 30:
        score += min(est_waves - 30, 10) * 400
    # 41+ 波: 500/波
    if est_waves > 40:
        score += (est_waves - 40) * 500

    # Boss / 精英专属击杀悬赏（从 20 波开始，每 5 波一个 Boss）
    boss_bonus = 0
    for bw in range(20, est_waves + 1, 5):
        idx = (bw - 20) // 5
        boss_bonus += (500 + idx * 100)

    total_score = score + boss_bonus
    return min(24000, max(200, total_score)), est_waves


