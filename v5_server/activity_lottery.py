# -*- coding: utf-8 -*-
"""activity_lottery.py

任务系统活跃度宝箱（日任务/周任务）神级换装、大场景及直购兑换券抽奖引擎。
支持：
- 梯度概率控制（日任务 2%~5%，周任务 10%~25%，500点终极档 100% 必爆神装大保底）
- 44 款稀世珍宝池（19款限定换装 + 8款3D大场景 + 17款直购换装兑换券）
- 优先抽取玩家未拥有的项目：
  * 换装：player_skin_unlocked 未解锁
  * 场景：user_scene 未拥有
  * 兑换券：对应换装未解锁 且 仓库 material 未持有该券
- 全图鉴防爆重复折算：
  * 重复换装 / 兑换券 -> 1000 移转之花（免费, ID 32）
  * 重复场景 -> 500 移转之花（免费, ID 32）
- 自动原子入库（player_skin_unlocked / user_scene / material / currency）
- 返回差分更新标记（touched_hero_ids, touched_scene_ids），便于上层组装 sc_14007 / sc_32009 差分帧
"""

import random
import time
from typing import Dict, List, Optional, Tuple, Any

# 18 款限定换装清单
PRIZE_SKINS: Dict[int, Dict[str, Any]] = {
    # 5 款 T0 顶级神级换装
    107602: {"name": "太一·庚辰「海上的私语」", "hero_id": 1076, "type": 8},
    109502: {"name": "苍鹭·托特「绘夜之诗」", "hero_id": 1095, "type": 8},
    102003: {"name": "三相·梵天「无间玩伴」", "hero_id": 1020, "type": 8},
    108502: {"name": "绮望·诗蔻蒂「永夜眷恋」", "hero_id": 1085, "type": 8},
    104402: {"name": "不灭王权·荷鲁斯「完美合奏」", "hero_id": 1044, "type": 8},
    # 8 款大场景特殊限定换装
    104301: {"name": "隐夜·伊里伽尔「童话式复古」", "hero_id": 1043, "type": 8},
    105401: {"name": "双司镇命·无常「慵懒靠近你」", "hero_id": 1054, "type": 8},
    106103: {"name": "玄机·执明「沉溺于夏日」", "hero_id": 1061, "type": 8},
    107002: {"name": "十曜·金乌「金雀钗」", "hero_id": 1070, "type": 8},
    107301: {"name": "巧构·麟钰「岁华似锦」", "hero_id": 1073, "type": 8},
    108501: {"name": "绮望·诗蔻蒂「梦的伊始」", "hero_id": 1085, "type": 8},
    113907: {"name": "冰渊·波塞冬「澄灵海色」", "hero_id": 1139, "type": 8},
    128403: {"name": "黯耀·薇儿丹蒂「与你同在的每一日」", "hero_id": 1284, "type": 8},
    # 5 款活动/战令纪念换装
    103901: {"name": "潮音·波塞冬「恋海」", "hero_id": 1039, "type": 8},
    104103: {"name": "铃兰之弦·雅典娜「红与黑」", "hero_id": 1041, "type": 8},
    106604: {"name": "震荡·大国主「兔迎初岁」", "hero_id": 1066, "type": 8},
    108404: {"name": "幼苗·薇儿丹蒂「甜心厨娘」", "hero_id": 1084, "type": 8},
    114801: {"name": "逆潮·前鬼坊天狗「校园时光」", "hero_id": 1148, "type": 8},
}

# 8 款 3D 大场景清单
PRIZE_SCENES: Dict[int, Dict[str, Any]] = {
    6001: {"name": "3D大厅场景「暮色珍珠」", "type": 21},
    6017: {"name": "3D大厅场景「思念晨意」", "type": 21},
    6018: {"name": "3D大厅场景「思念夜语」", "type": 21},
    6030: {"name": "3D大厅场景「喧笑时光」", "type": 21},
    6042: {"name": "3D大厅场景「暮光心旅」", "type": 21},
    6052: {"name": "3D大厅场景「少女欢奏之夜」", "type": 21},
    6059: {"name": "3D大厅场景「雨夜之约」", "type": 21},
    6013: {"name": "3D大厅场景「温柔的回响」", "type": 21},
}

# 17 款直购换装兑换券清单（Shop 16）
PRIZE_VOUCHERS: Dict[int, Dict[str, Any]] = {
    1037011: {"skin_id": 103701, "hero_id": 1037, "name": "换装兑换券-冒牌女仆", "type": 14},
    1013011: {"skin_id": 101301, "hero_id": 1013, "name": "换装兑换券-余烬", "type": 14},
    1048011: {"skin_id": 104801, "hero_id": 1048, "name": "换装兑换券-晖耀", "type": 14},
    1093031: {"skin_id": 109303, "hero_id": 1093, "name": "换装兑换券-谧夏绮影", "type": 14},
    1042011: {"skin_id": 104201, "hero_id": 1042, "name": "换装兑换券-冥夜鸢尾", "type": 14},
    1032031: {"skin_id": 103203, "hero_id": 1032, "name": "换装兑换券-盛音礼赞", "type": 14},
    1139031: {"skin_id": 113903, "hero_id": 1139, "name": "换装兑换券-白夜奇谭", "type": 14},
    1111011: {"skin_id": 111101, "hero_id": 1111, "name": "换装兑换券-鸦羽幽梦", "type": 14},
    1028011: {"skin_id": 102801, "hero_id": 1028, "name": "换装兑换券-喧哗颂歌", "type": 14},
    1199011: {"skin_id": 119901, "hero_id": 1199, "name": "换装兑换券-星月萤", "type": 14},
    1138011: {"skin_id": 113801, "hero_id": 1138, "name": "换装兑换券-青空之境", "type": 14},
    1017011: {"skin_id": 101701, "hero_id": 1017, "name": "换装兑换券-伪装", "type": 14},
    1042041: {"skin_id": 104204, "hero_id": 1042, "name": "换装兑换券-忘川灯影", "type": 14},
    1039061: {"skin_id": 103906, "hero_id": 1039, "name": "换装兑换券-浥轻尘", "type": 14},
    1072011: {"skin_id": 107201, "hero_id": 1072, "name": "换装兑换券-粽香滚滚", "type": 14},
    1094021: {"skin_id": 109402, "hero_id": 1094, "name": "换装兑换券-幻萤蝶梦", "type": 14},
    1094041: {"skin_id": 109404, "hero_id": 1094, "name": "换装兑换券-镇魂乐章", "type": 14},
}

# 宝箱概率阶梯配置
# pt_id = 1: 日任务宝箱
# pt_id = 3: 周任务宝箱
BOX_RATES: Dict[int, Dict[int, float]] = {
    1: {
        20: 0.02,   # 2%
        40: 0.02,   # 2%
        60: 0.03,   # 3%
        80: 0.04,   # 4%
        100: 0.05,  # 5%
    },
    3: {
        100: 0.10,  # 10%
        200: 0.15,  # 15%
        300: 0.20,  # 20%
        400: 0.25,  # 25%
        500: 1.00,  # 100% 终极档必出神装大保底
    }
}


def get_box_rate(pt_id: int, target_need: int) -> float:
    """获取指定档位宝箱的中奖概率"""
    return BOX_RATES.get(int(pt_id), {}).get(int(target_need), 0.0)


def roll_activity_box(db, uid: int, pt_id: int, target_need: int) -> Tuple[Optional[Dict[str, Any]], List[int], List[int]]:
    """为指定玩家抽取一个活跃度宝箱的额外神装彩蛋。

    Args:
        db: AccountDB 实例
        uid: 玩家 UID
        pt_id: 活跃度类型（1=日任务 / 3=周任务）
        target_need: 宝箱所需活跃度分值（如 20, 40, ..., 500）

    Returns:
        (grant_item_dict, touched_hero_ids, touched_scene_ids)
        grant_item_dict: None（未中奖）或 {"id": item_id, "num": 1, "is_convert": bool, ...}
        touched_hero_ids: 若解锁了新换装，返回所属英雄 ID 列表（用于组装 sc_14007）
        touched_scene_ids: 若解锁了新场景，返回场景 ID 列表（用于组装 sc_32009）
    """
    rate = get_box_rate(pt_id, target_need)
    if rate <= 0.0:
        return None, [], []

    # 摇随机数判断是否命中
    if rate < 1.0 and random.random() > rate:
        return None, [], []

    # 命中！开始选奖
    # 1. 查询玩家已拥有的换装、场景与兑换券
    skin_rows = db.query("SELECT skin_id FROM player_skin_unlocked WHERE uid=?", (uid,))
    owned_skins = {int(r["skin_id"]) for r in (skin_rows or []) if "skin_id" in r}

    scene_rows = db.query("SELECT scene_id FROM user_scene WHERE uid=?", (uid,))
    owned_scenes = {int(r["scene_id"]) for r in (scene_rows or []) if "scene_id" in r}

    mat_rows = db.query("SELECT id FROM material WHERE uid=? AND num > 0", (uid,))
    owned_materials = {int(r["id"]) for r in (mat_rows or []) if "id" in r}

    # 2. 筛选未拥有的项目
    unowned_skins = [sid for sid in PRIZE_SKINS if sid not in owned_skins]
    unowned_scenes = [scid for scid in PRIZE_SCENES if scid not in owned_scenes]
    unowned_vouchers = [
        vid for vid, meta in PRIZE_VOUCHERS.items()
        if meta["skin_id"] not in owned_skins and vid not in owned_materials
    ]

    unowned_pool = []
    for sid in unowned_skins:
        unowned_pool.append(("skin", sid))
    for scid in unowned_scenes:
        unowned_pool.append(("scene", scid))
    for vid in unowned_vouchers:
        unowned_pool.append(("voucher", vid))

    now_ts = int(time.time())
    touched_heroes = []
    touched_scenes = []

    if unowned_pool:
        # 还有未拥有的项目：等权随机选一项
        category, chosen_id = random.choice(unowned_pool)
        if category == "skin":
            meta = PRIZE_SKINS[chosen_id]
            # 原子入库 player_skin_unlocked
            db.execute(
                "INSERT OR REPLACE INTO player_skin_unlocked (uid, skin_id, unlock_ts, update_ts) VALUES (?, ?, ?, ?)",
                (uid, chosen_id, now_ts, now_ts)
            )
            touched_heroes.append(meta["hero_id"])
            return {"id": chosen_id, "num": 1, "is_convert": False, "name": meta["name"], "category": "skin"}, touched_heroes, touched_scenes
        elif category == "scene":
            meta = PRIZE_SCENES[chosen_id]
            # 原子入库 user_scene
            db.execute(
                "INSERT OR REPLACE INTO user_scene (uid, scene_id, lasted_time, obtain_time, update_ts) VALUES (?, ?, ?, ?, ?)",
                (uid, chosen_id, 0, now_ts, now_ts)
            )
            touched_scenes.append(chosen_id)
            return {"id": chosen_id, "num": 1, "is_convert": False, "name": meta["name"], "category": "scene"}, touched_heroes, touched_scenes
        else:
            # 兑换券：原子入库 material 仓库
            meta = PRIZE_VOUCHERS[chosen_id]
            db.execute(
                "INSERT INTO material (uid, id, num, name, update_ts) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(uid, id) DO UPDATE SET num = num + ?, update_ts = ?",
                (uid, chosen_id, 1, meta["name"], now_ts, 1, now_ts)
            )
            return {"id": chosen_id, "num": 1, "is_convert": False, "name": meta["name"], "category": "voucher"}, touched_heroes, touched_scenes
    else:
        # 已全图鉴达成（44 项全收集）：随机抽取一项，触发重复防暴折算！
        # 为避免客户端移转之辉本地代码污染展示，全部折算为移转之花（免费, ID 32）
        all_pool = (
            [("skin", sid) for sid in PRIZE_SKINS] +
            [("scene", scid) for scid in PRIZE_SCENES] +
            [("voucher", vid) for vid in PRIZE_VOUCHERS]
        )
        category, chosen_id = random.choice(all_pool)
        if category in ("skin", "voucher"):
            # 换装/兑换券折算 1000 移转之花 (ID 32)
            convert_id = 32
            convert_num = 1000
            db.execute(
                "INSERT INTO currency (uid, id, num) VALUES (?, ?, ?) "
                "ON CONFLICT(uid, id) DO UPDATE SET num = num + ?",
                (uid, convert_id, convert_num, convert_num)
            )
            return {"id": convert_id, "num": convert_num, "is_convert": True, "source_item": chosen_id, "category": "currency"}, [], []
        else:
            # 场景折算 500 移转之花 (ID 32)
            convert_id = 32
            convert_num = 500
            db.execute(
                "INSERT INTO currency (uid, id, num) VALUES (?, ?, ?) "
                "ON CONFLICT(uid, id) DO UPDATE SET num = num + ?",
                (uid, convert_id, convert_num, convert_num)
            )
            return {"id": convert_id, "num": convert_num, "is_convert": True, "source_scene": chosen_id, "category": "currency"}, [], []
