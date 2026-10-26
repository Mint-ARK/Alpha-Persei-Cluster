# -*- coding: utf-8 -*-
"""
hero_service.py — V5 服务端统一修正者角色养成领域服务 (Unified Hero Growth Service)

核心职责：
1. 统一管理修正者角色战斗养成生命周期（等级经验、突破进阶、神识超越升星、技能升级、元素属性强化）；
2. 纳管专属权钥系统（权钥经验升级、材料折算、溢出经验低阶返还、突破阶数、钥从佩戴与置换）；
3. 纳管神格体系（节点解锁、3 槽位 FIFO 装配、流派一键装配、批量装配、全部卸下）；
4. 纳管跃迁系统（1~6 槽位天赋点强化、跃迁词条技能保存）与武器同调模组升级；
5. 纳管战术芯片（AI 行动芯片 4 槽装配）与外观个性化（主界面看板立绘、战斗皮肤、特别关注）；
6. 统一联动刻印佩戴（1~6 槽位穿戴/置换、双端一致性同步、未锁刻印一键卸下、阵营专属绑定）；
7. 封装下行网络帧刷新逻辑（构建 sc_14007 及关联资产差分帧，确保前端绝对零卡死）；
8. 预留标准化 GM 底层接口（一键满配角色、一键全解锁角色、状态查询与重置），提供清晰对接文档。
"""

import os
import json
import time
import logging
from core import OperationError
from middleware import DownFrame
import event_bus
from codec import encode

logger = logging.getLogger("hero_service")

# 静态皮肤/场景/头像映射缓存
_SKIN_HERO_MAP = None
_SKIN_SCENE_MAP = None
_SKIN_PORTRAIT_MAP = None
_WEAPON_MODULE_MAX_LEVELS = None
_WEAPON_MODULE_RULES = None


def _get_weapon_module_max_levels():
    """Read the allowlist and exact module caps exported from WeaponModuleCfg.cost."""
    global _WEAPON_MODULE_MAX_LEVELS
    if _WEAPON_MODULE_MAX_LEVELS is None:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weapon_module_cfg.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            roles = cfg.get("roles")
            if not isinstance(roles, dict):
                raise ValueError("roles must be an object")
            parsed = {}
            for hero_id, max_level in roles.items():
                hid = int(hero_id)
                cap = int(max_level)
                if hid > 0 and cap > 0:
                    parsed[hid] = cap
            _WEAPON_MODULE_MAX_LEVELS = parsed
        except Exception as exc:
            logger.warning("加载 weapon_module_cfg.json 失败，武器模块升级按拒绝处理: %s", exc)
            _WEAPON_MODULE_MAX_LEVELS = {}
    return _WEAPON_MODULE_MAX_LEVELS


def _get_weapon_module_rules():
    """Packaged client conditions/costs; missing rules never mean free upgrades."""
    global _WEAPON_MODULE_RULES
    if _WEAPON_MODULE_RULES is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weapon_module_cfg.json")
        try:
            with open(path, encoding="utf-8") as stream:
                rules = json.load(stream).get("rules")
            if not isinstance(rules, dict):
                raise ValueError("missing module rules")
            _WEAPON_MODULE_RULES = rules
        except Exception as exc:
            logger.warning("加载模组资格/消耗规则失败，拒绝操作: %s", exc)
            _WEAPON_MODULE_RULES = {}
    return _WEAPON_MODULE_RULES


def _module_condition_met(db, uid, hero, condition):
    """Mirror only verified client conditions using current persistent facts."""
    kind = int(condition["type"])
    params = condition["params"]
    required = int(params[0])
    if kind == 1001:
        return int(hero.get("level") or 0) >= required
    if kind == 1003:
        return int(hero.get("star") or 0) >= required * 100
    if kind == 1004:
        return min(int(hero.get("clear_times") or 0), 100) >= required
    if kind == 1007:
        return int(hero.get("weapon_exp") or 0) >= int(condition["required_weapon_exp"])
    if kind in (1011, 5002):
        table = condition["stage_table"]
        if table not in ("chapter", "stage_sub"):
            raise OperationError(406, "模组关卡条件配置无效")
        rows = db.query(f"SELECT clear_times FROM {table} WHERE uid=? AND id=?", (uid, required))
        if not rows and table == "stage_sub":
            rows = db.query("SELECT clear_times FROM chapter WHERE uid=? AND id=?", (uid, required))
        return bool(rows and int(rows[0].get("clear_times") or 0) > 0)
    if kind == 5001:
        plots = condition["plot_ids"]
        if not plots:
            raise OperationError(406, "模组档案条件缺少剧情配置")
        rows = db.query("SELECT video_list FROM hero_archive WHERE uid=? AND archive_id=?",
                        (uid, int(condition["archive_id"])))
        raw = rows[0].get("video_list") if rows else []
        try:
            viewed = json.loads(raw) if isinstance(raw, str) else (raw or [])
            if not isinstance(viewed, list):
                raise ValueError("invalid video list")
            viewed = {int(value) for value in viewed}
        except (ValueError, TypeError):
            raise OperationError(406, "模组档案已读记录无效")
        return all(int(plot) in viewed for plot in plots)
    # 1048's HistoryData condition has no authoritative persistent producer yet.
    # Never replace that history with a guessed module/mission flag.
    raise OperationError(406, f"模组条件 {condition['id']} 尚无可验证的持久进度")

def _get_skin_hero_map():
    global _SKIN_HERO_MAP
    if _SKIN_HERO_MAP is None:
        map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_hero_map.json")
        if os.path.exists(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    _SKIN_HERO_MAP = json.load(f)
            except Exception as e:
                logger.warning(f"加载 skin_hero_map.json 失败: {e}")
                _SKIN_HERO_MAP = {}
        else:
            _SKIN_HERO_MAP = {}
    return _SKIN_HERO_MAP

def _get_skin_scene_map():
    global _SKIN_SCENE_MAP
    if _SKIN_SCENE_MAP is None:
        map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_scene_map.json")
        if os.path.exists(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    _SKIN_SCENE_MAP = json.load(f)
            except Exception as e:
                logger.warning(f"加载 skin_scene_map.json 失败: {e}")
                _SKIN_SCENE_MAP = {}
        else:
            _SKIN_SCENE_MAP = {}
    return _SKIN_SCENE_MAP

def _get_skin_portrait_map():
    global _SKIN_PORTRAIT_MAP
    if _SKIN_PORTRAIT_MAP is None:
        map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_portrait_map.json")
        if os.path.exists(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    _SKIN_PORTRAIT_MAP = {str(sid):int(row['portrait_id'] if isinstance(row, dict) else row)
                                          for sid, row in raw.items()}
            except Exception as e:
                logger.warning(f"加载 skin_portrait_map.json 失败: {e}")
                _SKIN_PORTRAIT_MAP = {}
        else:
            _SKIN_PORTRAIT_MAP = {}
    return _SKIN_PORTRAIT_MAP

def skin_to_hero_id(skin_or_hero_id):
    """根据皮肤 ID 或英雄 ID 解析出英雄原型 ID。"""
    sid = int(skin_or_hero_id or 0)
    if sid <= 0:
        return 0
    m = _get_skin_hero_map()
    str_sid = str(sid)
    if str_sid in m:
        return int(m[str_sid])
    if sid >= 100000:
        return int(str_sid[:4])
    return sid


# ==================== 养成规则与静态常量 ====================

# 角色升级经验道具折算
EXP_ITEMS = {
    40101: 500,
    40102: 1000,
    40103: 5000,
    40104: 10000
}

# 突破阶数对应等级上限
BREAK_CAPS = {
    0: 20,
    1: 40,
    2: 50,
    3: 60,
    4: 70,
    5: 80
}
HERO_BREAK_LIMITS = BREAK_CAPS

EXP_ITEM_VALUES = [
    (40104, 10000),
    (40103, 5000),
    (40102, 1000),
    (40101, 500)
]

def material_give_back(cut_exp):
    """溢出经验按经验道具从高到低折算返还（完全对齐客户端 MaterialTools.materialGiveBack）"""
    if cut_exp <= 0:
        return []
    refund = []
    rem = int(cut_exp)
    for iid, val in EXP_ITEM_VALUES:
        cnt = rem // val
        if cnt > 0:
            refund.append({"id": iid, "num": cnt})
            rem -= cnt * val
    return refund

def get_hero_break_max_level(db, race, break_level):
    """查询指定阵营与突破阶数对应的最大等级上限（从 hero_break_cfg 动态获取）"""
    if db:
        row = db.query("SELECT max_level FROM hero_break_cfg WHERE race=? AND break_times=?", (race, break_level))
        if row and row[0].get("max_level"):
            return int(row[0]["max_level"])
    return 20 + int(break_level) * 10


def _hero_growth_int(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise OperationError(2, f"角色成长数据无效: {name}")
    return value


def _hero_break_context(db, uid, hero_id):
    """Read owned hero, player and the configured stage sequence before costs."""
    rows = db.query("SELECT level, exp, break_level, unlock FROM hero WHERE uid=? AND id=?", (uid, hero_id))
    if not rows:
        raise OperationError(406, f"英雄 {hero_id} 不存在")
    hero = rows[0]
    if hero.get("unlock") != 1:
        raise OperationError(2, "角色未解锁")
    for name, minimum in (("level", 1), ("exp", 0), ("break_level", 0)):
        _hero_growth_int(hero.get(name), name, minimum)
    roles = db.query("SELECT race FROM hero_cfg WHERE hero_id=?", (hero_id,))
    if len(roles) != 1:
        raise OperationError(2, "角色阵营配置缺失或重复")
    race = _hero_growth_int(roles[0].get("race"), "race", 1)
    players = db.query("SELECT level FROM users WHERE uid=?", (uid,))
    if len(players) != 1:
        raise OperationError(2, "玩家等级数据缺失或重复")
    player_level = _hero_growth_int(players[0].get("level"), "user_level", 1)
    stages = {}
    for config in db.query("SELECT * FROM hero_break_cfg WHERE race=? ORDER BY break_times", (race,)):
        stage = _hero_growth_int(config.get("break_times"), "break_times")
        _hero_growth_int(config.get("max_level"), "max_level", 1)
        _hero_growth_int(config.get("user_level"), "user_level")
        if stage in stages:
            raise OperationError(2, "突破阶段配置重复")
        stages[stage] = config
    if not stages or sorted(stages) != list(range(len(stages))) or hero["break_level"] not in stages:
        raise OperationError(2, "突破阶段配置缺失")
    if any(stages[i + 1]["max_level"] <= stages[i]["max_level"] for i in range(len(stages) - 1)):
        raise OperationError(2, "突破阶段等级上限配置无效")
    return hero, player_level, stages


def _hero_break_costs(config):
    """Malformed configuration must never turn a paid breakthrough into a free one."""
    costs = [(2, _hero_growth_int(config.get("cost_gold"), "cost_gold"))]
    try:
        items = json.loads(config["cost"]) if isinstance(config.get("cost"), str) else config["cost"]
    except (KeyError, ValueError, TypeError):
        raise OperationError(2, "突破消耗配置无效") from None
    if not isinstance(items, list):
        raise OperationError(2, "突破消耗配置无效")
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise OperationError(2, "突破消耗配置无效")
        costs.append((_hero_growth_int(item[0], "cost_item", 1), _hero_growth_int(item[1], "cost_num", 1)))
    return [(iid, num) for iid, num in costs if num]

def level_to_exp(level, curve):
    """计算达到指定等级所需的累计总经验（1 到 level-1 级消耗之和）"""
    return sum(curve.get(i, 0) for i in range(1, int(level)))

def exp_to_level(total_exp, curve, max_lv=100):
    """根据累计总经验推算角色当前等级，封顶在 max_lv"""
    lv = 1
    acc = 0
    total = int(total_exp)
    while lv < max_lv:
        need = curve.get(lv, 0)
        if total >= acc + need:
            acc += need
            lv += 1
        else:
            break
    return lv


# 神识超越（升星）阶梯列表与消耗配置
# star 编码：阶级×100 + phase (100=B, 200=A, 300=S, 400=SS, 500=SSS, 600=Ω)
STAR_LIST = [
    100, 101, 102, 103, 104,
    200, 201, 202, 203, 204,
    300, 301, 302, 303, 304,
    400, 401, 402, 403, 404,
    500, 501, 502, 503, 504,
    600
]

STAR_CFG = {
    100: (2, 100),   101: (2, 100),   102: (2, 100),   103: (2, 100),   104: (4, 1000),
    200: (3, 200),   201: (3, 200),   202: (3, 200),   203: (3, 200),   204: (6, 2000),
    300: (5, 300),   301: (5, 300),   302: (5, 300),   303: (5, 300),   304: (10, 3000),
    400: (10, 400),  401: (10, 400),  402: (10, 400),  403: (10, 400),  404: (50, 4000),
    500: (20, 500),  501: (20, 500),  502: (20, 500),  503: (20, 500),  504: (100, 5000),
}

# 技能升级消耗神力因子 (40301)
SKILL_MAX_LV = 35
SKILL_COSTS = {
    1: {1: 1, 2: 1, 3: 1, 4: 1, 5: 1},
    2: {1: 1, 2: 2, 3: 2, 4: 2, 5: 3},
    3: {1: 1, 2: 2, 3: 2, 4: 2, 5: 3},
    4: {1: 3, 2: 3, 3: 3, 4: 3, 5: 4},
    5: {1: 5, 2: 5, 3: 5, 4: 5, 5: 8},
    6: {1: 5, 2: 5, 3: 5, 4: 5, 5: 8},
    7: {1: 6, 2: 6, 3: 6, 4: 6, 5: 9},
    8: {1: 8, 2: 7, 3: 7, 4: 7, 5: 11},
    9: {1: 9, 2: 8, 3: 8, 4: 8, 5: 12},
    10: {1: 13, 2: 11, 3: 11, 4: 11, 5: 17},
    11: {1: 13, 2: 11, 3: 11, 4: 11, 5: 17},
    12: {1: 15, 2: 12, 3: 12, 4: 12, 5: 19},
    13: {1: 17, 2: 13, 3: 13, 4: 13, 5: 20},
    14: {1: 18, 2: 14, 3: 14, 4: 14, 5: 22},
    15: {1: 23, 2: 17, 3: 17, 4: 17, 5: 27},
    16: {1: 25, 2: 18, 3: 18, 4: 18, 5: 28},
    17: {1: 27, 2: 19, 3: 19, 4: 19, 5: 30},
    18: {1: 29, 2: 20, 3: 20, 4: 20, 5: 32},
    19: {1: 31, 2: 21, 3: 21, 4: 21, 5: 33},
    20: {1: 38, 2: 25, 3: 25, 4: 25, 5: 40},
    21: {1: 41, 2: 26, 3: 26, 4: 26, 5: 41},
    22: {1: 43, 2: 27, 3: 27, 4: 27, 5: 43},
    23: {1: 45, 2: 28, 3: 28, 4: 28, 5: 44},
    24: {1: 50, 2: 30, 3: 30, 4: 30, 5: 48},
    25: {1: 59, 2: 35, 3: 35, 4: 35, 5: 56},
    26: {1: 62, 2: 36, 3: 36, 4: 36, 5: 57},
    27: {1: 65, 2: 37, 3: 37, 4: 37, 5: 59},
    28: {1: 68, 2: 38, 3: 38, 4: 38, 5: 60},
    29: {1: 73, 2: 40, 3: 40, 4: 40, 5: 64},
    30: {1: 84, 2: 45, 3: 45, 4: 45, 5: 72},
    31: {1: 87, 2: 46, 3: 46, 4: 46, 5: 73},
    32: {1: 90, 2: 47, 3: 47, 4: 47, 5: 75},
    33: {1: 94, 2: 48, 3: 48, 4: 48, 5: 76},
    34: {1: 100, 2: 50, 3: 50, 4: 50, 5: 80},
}
ATTR_MAX_LV = 15

# 权钥强化与突破常量
WEAPON_MAT_EXP = {
    40201: 100,
    40202: 200,
    40203: 1000
}
# Compatibility snapshots for audit tools; runtime rules are exported from Lua.
WEAPON_BREAK_LIMITS = {
    0: 4800,    # Lv20
    1: 9800,    # Lv30
    2: 19800,   # Lv40
    3: 49800,   # Lv50
    4: 99800    # Lv60
}
WEAPON_BREAK_COSTS = {
    0: [(2, 1000), (40501, 15)],
    1: [(2, 1500), (40501, 25), (40502, 15)],
    2: [(2, 2000), (40502, 25), (40503, 15)],
    3: [(2, 3000), (40503, 25)]
}

_WEAPON_GROWTH_RULES = None


def _get_weapon_growth_rules():
    """Load the packaged client curve and fail before debits on invalid metadata."""
    global _WEAPON_GROWTH_RULES
    if _WEAPON_GROWTH_RULES is None:
        with open(os.path.join(os.path.dirname(__file__), "weapon_growth_cfg.json"), encoding="utf-8") as stream:
            raw = json.load(stream)
        stages, players = raw["stage_levels"], raw["player_levels"]
        curve = {int(k): v for k, v in raw["level_exp"].items()}
        costs = {int(k): v for k, v in raw["break_costs"].items()}
        if (raw.get("schema") != 1 or not stages or len(players) != len(stages)
                or any(type(v) is not int or v <= 0 for v in stages + players)
                or stages != sorted(set(stages)) or players != sorted(players)
                or set(curve) != set(range(1, stages[-1] + 1))
                or any(type(v) is not int or v < 0 for v in curve.values())
                or curve[1] != 0
                or any(curve[i] <= curve[i-1] for i in range(2, stages[-1] + 1))
                or type(raw["gold_per_exp"]) is not int or raw["gold_per_exp"] <= 0
                or set(costs) != set(range(len(stages)-1))):
            raise ValueError("权钥成长配置无效")
        for items in costs.values():
            if not items or any(not isinstance(item, list) or len(item) != 2
                    or any(type(v) is not int or v <= 0 for v in item) for item in items):
                raise ValueError("权钥突破成本配置无效")
        _WEAPON_GROWTH_RULES = dict(raw, level_exp=curve, break_costs=costs)
    return _WEAPON_GROWTH_RULES


def _weapon_break_eligibility(db, uid, rules, start, end):
    """Check every crossed stage using the current player's authoritative level."""
    if end == start:
        return
    players = db.query("SELECT level FROM users WHERE uid=?", (uid,))
    if len(players) != 1 or type(players[0].get("level")) is not int:
        raise OperationError(2, "玩家等级数据无效")
    for stage in range(start, end):
        if players[0]["level"] < rules["player_levels"][stage]:
            raise OperationError(2, "玩家等级不足，无法突破权钥")


def _weapon_growth_state(db, uid, hero_id):
    """Validate the receiving entity before consuming materials or servant UIDs."""
    rows = db.query("SELECT unlock, weapon_break, weapon_exp FROM hero WHERE uid=? AND id=?", (uid, hero_id))
    if len(rows) != 1:
        raise OperationError(406, f"权钥目标角色不存在或不唯一: {hero_id}")
    hero = rows[0]
    if hero.get("unlock") != 1:
        raise OperationError(2, "角色未解锁，无法养成权钥")
    stage, experience = hero.get("weapon_break"), hero.get("weapon_exp")
    rules = _get_weapon_growth_rules()
    if type(stage) is not int or not 0 <= stage < len(rules["stage_levels"]):
        raise OperationError(2, "权钥突破阶段无效")
    cap = rules["level_exp"][rules["stage_levels"][stage]]
    minimum = rules["level_exp"][rules["stage_levels"][stage-1]] if stage else 0
    if type(experience) is not int or not minimum <= experience <= cap:
        raise OperationError(2, "权钥历史经验无效，保留原状态")
    return stage, experience


def _weapon_material_plan(material_list):
    """Validate the entire list and aggregate duplicates without mutating assets."""
    items = [] if material_list is None else material_list
    if not isinstance(items, (list, tuple)):
        raise OperationError(2, "权钥经验材料列表无效")
    costs, experience = {}, 0
    for item in items:
        if not isinstance(item, dict):
            raise OperationError(2, "权钥经验材料条目无效")
        iid, count = item.get("id"), item.get("num")
        if type(iid) is not int or iid not in WEAPON_MAT_EXP:
            raise OperationError(2, "物品不是有效的权钥经验材料")
        if type(count) is not int or count <= 0:
            raise OperationError(2, "权钥经验材料数量必须为正整数")
        costs[iid] = costs.get(iid, 0) + count
        experience += WEAPON_MAT_EXP[iid] * count
        # SQLite stores both balances and experience as signed 64-bit integers.
        if experience > (1 << 63) - 1:
            raise OperationError(2, "权钥经验材料总量超出存储范围")
    return costs, experience

# 跃迁天赋点消耗配置
TRANSITION_TALENT_COST = {
    1: [(2, 1200), (41301, 16)],
    2: [(2, 1600), (41302, 4), (41301, 16)],
    3: [(2, 2000), (41302, 8), (41301, 16)],
    4: [(2, 2400), (41303, 4), (41302, 8)],
    5: [(2, 2800), (41303, 4), (41302, 12)],
    6: [(2, 4000), (41303, 8), (41302, 8)],
}


class HeroService:
    """统一修正者角色养成领域服务单例"""
    _instance = None

    def __init__(self, db=None):
        self.db = db
        self._listeners_ready = False

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            cls._instance.db = db
        if not cls._instance._listeners_ready:
            cls._instance._init_event_listeners()
            cls._instance._listeners_ready = True
        return cls._instance

    # ============ 战斗结算成长（STAGE_PASS 广播订阅，业务亲和 hero 表） ============

    def on_battle_result(self, ctx, uid, heroes, dest=0, times=1, grant_exp=True):
        """STAGE_PASS（战斗/扫荡胜利）订阅入口——出战英雄战毕成长：

        1. 角色熟练度（hero.clear_times +times，上限 100，gamesetting: mastery_gain=1,
           mastery_level_max=100；神格解锁/誓约任务 110 条件的数据源）
        2. 胜场累计（hero.battle_win_times，誓约任务 410003 与神格任务的数据源；
           原由 oath_service 监听内累加，迁入本模块防双写）
        3. 一阶档案好感度（hero_archive.exp +5*times，上限 1000）
        4. 角色经验（stage_info_cfg.hero_exp * times）
        每英雄 emit HERO_UPGRADE(proficiency_up) 携带真实 ctx——下游 task_listener/
        oath_service/trust_service 监听器据此推进任务、重估关系网增益并推送原子帧。
        """
        db = getattr(ctx, "db", None) or self.db
        if not db or not heroes:
            return
        times = max(1, int(times or 1))
        add_exp = self._load_stage_hero_exp(dest) * times if grant_exp else 0

        for h in heroes:
            try:
                # 兼容 dict/int 双形态（与 oath/trust 监听口径一致：dict 取 id 字段）
                hid = int(h.get("id") if isinstance(h, dict) else h)
                # 1. 角色熟练度（上限 100）
                db.execute(
                    "UPDATE hero SET clear_times = MIN(100, clear_times + ?) WHERE uid=? AND id=?",
                    (times, uid, hid))
                # 2. 胜场累计（无上限，誓约/神格任务数据源）
                db.execute(
                    "UPDATE hero SET battle_win_times = battle_win_times + ? WHERE uid=? AND id=?",
                    (times, uid, hid))
                # 3. 一阶档案好感度：具体战斗形态必须映射回 63 个规范档案。
                from archive_service import ArchiveService
                ArchiveService.get_instance().add_exp(
                    ctx,
                    uid,
                    hid,
                    5 * times,
                    source="battle",
                    source_hero_id=hid,
                )
                # 4. 角色经验
                if add_exp > 0:
                    db.execute(
                        "UPDATE hero SET exp = exp + ? WHERE uid=? AND id=?",
                        (add_exp, uid, hid))
                # 5. 养成广播（携带真实 ctx，下游监听器可推原子帧/推进任务）
                try:
                    from event_bus import bus, Events
                    bus.emit(Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="proficiency_up")
                except Exception:
                    pass
            except Exception:
                continue

    def _load_stage_hero_exp(self, dest):
        """读取 stage_info_cfg 的 hero_exp（角色经验/胜），默认 20"""
        try:
            import middleware as _mw
            stage_info = _mw._load_battle_cfgs()[3]
            s_info = (stage_info.get(str(dest)) or stage_info.get(dest) or {}) if dest else {}
            return int(s_info['hero_exp']) if s_info.get('hero_exp') is not None else 20
        except Exception:
            return 20

    def _init_event_listeners(self):
        """挂载全局事件总线监听器（随 get_instance 惰性注册）"""
        from event_bus import bus, Events

        @bus.subscribe(Events.STAGE_PASS)
        def on_stage_pass(ctx, uid, **kwargs):
            heroes = kwargs.get("heroes") or []
            if not heroes:
                return
            self.on_battle_result(ctx, uid, heroes,
                                  dest=int(kwargs.get("stage_id") or 0),
                                  times=max(1, int(kwargs.get("times", 1))),
                                  grant_exp=bool(kwargs.get('grant_exp', True)))

    # ==================== 道具资产扣减与增加辅助 ====================

    def _deduct_item(self, ctx, uid, item_id, count):
        """统一扣减道具（金币/材料/碎片），优先通过 InventoryService 或安全回退"""
        iid = int(item_id)
        num = int(count)
        if num <= 0:
            return
        
        # 优先使用 InventoryService
        try:
            from inventory_service import InventoryService
        except ModuleNotFoundError as exc:
            if exc.name != "inventory_service":
                raise
        else:
            succ = InventoryService.cost_item(ctx, uid, iid, num)
            if not succ:
                raise OperationError(3, f"道具扣减失败或余额不足: id={iid} 需{num}")
            return

        # Only absence of the inventory module permits this legacy path.
        # An attempted debit must propagate failures to the outer transaction.
        db = getattr(ctx, "db", self.db)
        if not db:
            return

        # 判断类型
        if iid == 2 or iid < 1000 or (54000 <= iid <= 55000) or (60000 <= iid <= 61000):
            # 货币
            row = db.query("SELECT num FROM currency WHERE uid=? AND id=?", (uid, iid))
            have = int(row[0]["num"] if row and row[0]["num"] is not None else 0)
            if have < num:
                raise OperationError(3, f"货币不足: id={iid} 需{num} 有{have}")
            db.execute("UPDATE currency SET num=num-? WHERE uid=? AND id=?", (num, uid, iid))
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.add(iid)
            if iid == 2:
                try:
                    event_bus.bus.emit(event_bus.Events.GOLD_COST, ctx, uid, amount=num)
                except Exception:
                    pass
        elif iid in (40101, 40102, 40103, 40104, 40201, 40202, 40203, 40301, 40501, 40502, 40503, 41301, 41302, 41303, 40701) or (40000 <= iid < 50000):
            # 材料
            row = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
            have = int(row[0]["num"] if row and row[0]["num"] is not None else 0)
            if have < num:
                raise OperationError(3, f"材料不足: id={iid} 需{num} 有{have}")
            db.execute("UPDATE material SET num=num-? WHERE uid=? AND id=?", (num, uid, iid))
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.add(iid)
        else:
            # 碎片或其他
            hid = iid % 10000 if iid > 10000 else iid
            row = db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hid))
            if row:
                have = int(row[0]["num"] or 0)
                if have < num:
                    raise OperationError(3, f"碎片不足: hero={hid} 需{num} 有{have}")
                db.execute("UPDATE hero_piece SET num=num-? WHERE uid=? AND hero_id=?", (num, uid, hid))
                if hasattr(ctx, "touched_items"):
                    ctx.touched_items.add(iid)
            else:
                row_m = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
                have_m = int(row_m[0]["num"] if row_m and row_m[0]["num"] is not None else 0)
                if have_m < num:
                    raise OperationError(3, f"条目不足: id={iid} 需{num} 有{have_m}")
                db.execute("UPDATE material SET num=num-? WHERE uid=? AND id=?", (num, uid, iid))
                if hasattr(ctx, "touched_items"):
                    ctx.touched_items.add(iid)

    def _add_item(self, ctx, uid, item_id, count):
        """统一增加道具（经验返还等）"""
        iid = int(item_id)
        num = int(count)
        if num <= 0:
            return
        
        try:
            from inventory_service import InventoryService
            InventoryService.add_item(ctx, uid, iid, num)
            return
        except Exception:
            pass

        db = getattr(ctx, "db", self.db)
        if not db:
            return
        if iid == 2 or iid < 1000:
            db.execute(
                "INSERT INTO currency (uid, id, num) VALUES (?,?,?) "
                "ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                (uid, iid, num))
        else:
            db.execute(
                "INSERT INTO material (uid, id, num) VALUES (?,?,?) "
                "ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                (uid, iid, num))
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.add(iid)

    # ==================== 1. 基础养成 (Level & Break) ====================

    def add_hero_exp(self, ctx, uid, hero_id, item_list):
        """
        英雄使用道具增加经验：cs_14014 -> sc_14015
        - 采用官方全量累计总经验模型 (hero.exp 保存从 1 级开始的累计经验)
        - 严格按突破阶段上限封顶 (LevelToExp(max_lv))，未突破前超出的经验绝不计入
        - 溢出经验按官方规则折算返还材料 (material_give_back) 并入库背包
        - 触发 HERO_UPGRADE 事件
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        row = db.query("SELECT level, exp, break_level FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not row:
            raise OperationError(406, f"英雄 {hid} 不存在")

        cur_level = row[0]["level"] or 1
        cur_exp = row[0]["exp"] or 0
        break_level = row[0]["break_level"] or 0

        race_row = db.query("SELECT race FROM hero_cfg WHERE hero_id=?", (hid,))
        race = race_row[0]["race"] if race_row and race_row[0].get("race") else 1

        curve_rows = db.query("SELECT level, hero_exp_need FROM game_level_setting")
        curve = {r["level"]: r["hero_exp_need"] for r in curve_rows} if curve_rows else {}

        max_lv = get_hero_break_max_level(db, race, break_level)
        max_exp = level_to_exp(max_lv, curve)

        # 检查是否已达当前阶段上限
        if cur_level >= max_lv and cur_exp >= max_exp:
            raise OperationError(2, f"英雄 {hid} 已达当前突破阶段等级上限 (Lv.{max_lv})，请先突破")

        # 1. 统计并扣除投入的经验道具
        raw_exp_add = 0
        for it in (item_list or []):
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                iid = int(it[0])
                num = int(it[1])
            elif isinstance(it, dict):
                iid = int(it.get("id", it.get("item_id", 0)))
                num = int(it.get("num", it.get("item_num", 0)))
            else:
                continue
            if iid not in EXP_ITEMS:
                raise OperationError(2, f"非经验道具: {iid}")
            if num <= 0:
                continue
            raw_exp_add += EXP_ITEMS[iid] * num
            self._deduct_item(ctx, uid, iid, num)

        if raw_exp_add <= 0:
            raise OperationError(2, "经验道具数量为 0")

        # 2. 计算累计总经验与封顶截断
        potential_exp = cur_exp + raw_exp_add
        if potential_exp > max_exp:
            final_exp = max_exp
            cut_exp = potential_exp - max_exp
        else:
            final_exp = potential_exp
            cut_exp = 0

        final_level = exp_to_level(final_exp, curve, max_lv)

        # 3. 溢出经验折算返还材料入库
        refund_items = []
        if cut_exp > 0:
            refund_items = material_give_back(cut_exp)
            for rit in refund_items:
                self._add_item(ctx, uid, rit["id"], rit["num"])

        # 4. 落库
        db.execute("UPDATE hero SET exp=?, level=?, update_ts=? WHERE uid=? AND id=?",
                   (final_exp, final_level, int(time.time()), uid, hid))

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, times=1, oper="level_up", new_level=final_level, break_level=break_level)
        except Exception:
            pass

        return {
            "level": final_level,
            "exp": final_exp,
            "exp_add": final_exp - cur_exp,
            "cut_exp": cut_exp,
            "refund_items": refund_items,
            "hero_id": hid,
            "cur_break": break_level
        }

    def break_hero(self, ctx, uid, hero_id):
        """
        英雄突破进阶：cs_14036 -> sc_14037
        - 按当前阵营配置校验阶段、角色等级与玩家等级
        - 检查已解锁且存在下一阶段，再读取该阶段消耗
        - 扣除金币与阵营材料
        - 递增 break_level 并广播事件
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        hero, player_level, stages = _hero_break_context(db, uid, hid)
        cur_break = hero["break_level"]
        if cur_break + 1 not in stages or hero["level"] >= stages[max(stages)]["max_level"]:
            raise OperationError(2, f"英雄 {hid} 已达最大突破等级")
        config = stages[cur_break]
        if hero["level"] < config["max_level"]:
            raise OperationError(2, "角色尚未达到当前突破阶段等级上限")
        if player_level < config["user_level"]:
            raise OperationError(2, "玩家等级不足，无法突破")
        for iid, num in _hero_break_costs(config):
            self._deduct_item(ctx, uid, iid, num)

        new_break = cur_break + 1
        db.execute("UPDATE hero SET break_level=?, update_ts=? WHERE uid=? AND id=?",
                   (new_break, int(time.time()), uid, hid))

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, times=1, oper="break_up", break_level=new_break)
        except Exception:
            pass

        return {"hero_id": hid, "break_level": new_break, "new_break": new_break}

    def one_click_hero_up(self, ctx, uid, hero_id, target_level=0, break_list=None, item_list=None, material_list=None):
        """
        英雄一键升级+突破：cs_14120 -> sc_14121
        - 支持自选等级滑块，先规划 break_list 中的阶段突破及成本
        - 依据新的突破阶段确定目标等级上限与累计总经验上限
        - 目标不得降低现有等级或经验；仅本次投入溢出部分折算返还材料
        - sc_14121 的 item_list 应答客户端弹出返还窗口
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        hero, player_level, stages = _hero_break_context(db, uid, hid)
        cur_level, cur_exp, cur_break = hero["level"], hero["exp"], hero["break_level"]
        target_level = _hero_growth_int(target_level, "target_level")
        requested_breaks = [] if break_list is None else break_list
        if not isinstance(requested_breaks, (list, tuple)) or any(type(stage) is not int for stage in requested_breaks):
            raise OperationError(2, "突破列表无效")
        # Lua builds this list with pairs and consumes max(stage), so wire order
        # is not a contract. Require a unique contiguous set, then plan in order.
        requested_breaks = sorted(requested_breaks)

        curve_rows = db.query("SELECT level, hero_exp_need FROM game_level_setting")
        curve = {r["level"]: r["hero_exp_need"] for r in curve_rows} if curve_rows else {}

        # 1. Lua sends the current 0-based break_times for each crossed stage.
        # Validate the whole sequence before simulating growth or deducting assets.
        breaks_done = len(requested_breaks)
        break_costs = []
        for stage in requested_breaks:
            if stage != cur_break or stage + 1 not in stages:
                raise OperationError(2, "突破列表必须从当前阶段开始连续且不超过配置上限")
            if cur_level >= stages[max(stages)]["max_level"]:
                raise OperationError(2, "角色已达最大等级，无法继续突破")
            config = stages[stage]
            if player_level < config["user_level"]:
                raise OperationError(2, "玩家等级不足，无法完成所选突破")
            break_costs.extend(_hero_break_costs(config))
            cur_break += 1

        stage_max_lv = stages[cur_break]["max_level"]
        if target_level > stage_max_lv:
            raise OperationError(2, "目标等级超过所选突破阶段上限")
        final_target_lv = target_level or stage_max_lv
        if any(type(curve.get(i)) is not int or curve[i] <= 0 for i in range(1, final_target_lv)):
            raise OperationError(2, "角色经验曲线缺失或无效")

        max_exp = level_to_exp(final_target_lv, curve)
        if final_target_lv < cur_level or max_exp < cur_exp:
            raise OperationError(2, "目标等级不能降低角色现有等级或累计经验")

        # 2. Plan the experience needed to reach every selected stage first.
        items = item_list or material_list or []
        raw_exp_add = 0
        experience_costs = []
        for it in items:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                iid = int(it[0])
                num = int(it[1])
            elif isinstance(it, dict):
                iid = int(it.get("item_id", it.get("id", 0)))
                num = int(it.get("item_num", it.get("num", 0)))
            else:
                continue
            if iid not in EXP_ITEMS or num <= 0:
                continue
            raw_exp_add += EXP_ITEMS[iid] * num
            experience_costs.append((iid, num))

        if target_level and cur_exp + raw_exp_add < max_exp:
            raise OperationError(2, "投入经验不足以达到目标等级")
        for stage in requested_breaks:
            cap = stages[stage]["max_level"]
            if final_target_lv <= cap or cur_exp + raw_exp_add < level_to_exp(cap, curve):
                raise OperationError(2, "升级方案尚未达到所选突破阶段")

        for iid, num in break_costs + experience_costs:
            self._deduct_item(ctx, uid, iid, num)

        # 3. 只将本次投入的超额部分返还，历史经验始终完整保留。
        accepted_exp = min(raw_exp_add, max_exp - cur_exp)
        final_exp = cur_exp + accepted_exp
        cut_exp = raw_exp_add - accepted_exp

        final_level = exp_to_level(final_exp, curve, final_target_lv)

        refund_items = []
        if cut_exp > 0:
            refund_items = material_give_back(cut_exp)
            for rit in refund_items:
                self._add_item(ctx, uid, rit["id"], rit["num"])

        # 4. 落库
        db.execute("UPDATE hero SET exp=?, level=?, break_level=?, update_ts=? WHERE uid=? AND id=?",
                   (final_exp, final_level, cur_break, int(time.time()), uid, hid))

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="level_up", new_level=final_level, break_level=cur_break)
        except Exception:
            pass

        return {
            "level": final_level,
            "exp": final_exp,
            "breaks": breaks_done,
            "hero_id": hid,
            "refund_items": refund_items,
            "break_level": cur_break
        }

    # ==================== 2. 神识超越 (Transcendence / Star Up) ====================

    def star_up_hero(self, ctx, uid, hero_id):
        """
        角色神识超越/升星：cs_14012 -> sc_14013
        - 严格按 B(100) -> A(200) -> S(300) -> SS(400) -> SSS(500) -> Ω(600) 阶梯推进
        - 扣除 hero_piece 碎片与金币
        - 校验 Ω 封顶拦截
        - 广播 HERO_UPGRADE 事件
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        hrow = db.query("SELECT star FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not hrow:
            raise OperationError(2, f"角色 {hid} 未解锁")
        cur_star = int(hrow[0]["star"] or 100)
        if cur_star >= 600:
            raise OperationError(2, "已达到最高超越阶段（Ω）")

        piece_cost, gold_cost = STAR_CFG.get(cur_star, (10, 1000))

        # 碎片在 hero_piece 表
        prow = db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hid))
        have_piece = int((prow[0]["num"] if prow else 0) or 0)
        if have_piece < piece_cost:
            raise OperationError(3, f"碎片不足: hero={hid} 需{piece_cost} 有{have_piece}")

        if gold_cost > 0:
            self._deduct_item(ctx, uid, 2, gold_cost)
        
        db.execute("UPDATE hero_piece SET num=num-? WHERE uid=? AND hero_id=?", (piece_cost, uid, hid))
        if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
            ctx.touched_items.add(10000 + hid)

        # 计算下一阶段
        try:
            next_star = STAR_LIST[STAR_LIST.index(cur_star) + 1]
        except (ValueError, IndexError):
            next_star = cur_star + 1

        db.execute("UPDATE hero SET star=?, update_ts=? WHERE uid=? AND id=?", (next_star, int(time.time()), uid, hid))
        
        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="star_up", new_star=next_star)
        except Exception:
            pass

        return {
            "hero_id": hid,
            "old_star": cur_star,
            "new_star": next_star,
            "piece_cost": piece_cost,
            "gold_cost": gold_cost,
            "piece_left": have_piece - piece_cost
        }

    # ==================== 3. 技能与元素属性强化 (Skills & Elements) ====================

    def upgrade_skill(self, ctx, uid, hero_id, skill_id, num=1):
        """
        技能升级：cs_14030 -> sc_14031
        - 支持普攻、1/2/3技、奥义升级（上限 35 级）
        - 严格按官方 SkillCosts 计算神力因子 (40301) 消耗
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        if isinstance(num, bool) or not isinstance(num, int) or num <= 0:
            raise OperationError(2, "技能升级数量必须为正整数")
        if isinstance(skill_id, bool) or not isinstance(skill_id, int) or skill_id <= 0:
            raise OperationError(2, "必须指定当前角色的可升级技能")
        from hero_codec import _configured_hero_skills
        configured = _configured_hero_skills(hid)[:5]
        if len(configured) != 5 or len(set(configured)) != 5:
            raise OperationError(406, f"英雄 {hid} 技能配置缺失")
        sid = skill_id
        if sid not in configured:
            raise OperationError(2, "技能不属于当前角色的可升级技能")
        slot = configured.index(sid) + 1
        row = db.query("SELECT skill_list FROM hero WHERE uid=? AND id=? AND unlock=1", (uid, hid))
        if not row:
            raise OperationError(406, f"英雄 {hid} 未解锁")
        try:
            raw_skills = row[0]["skill_list"]
            skills = json.loads(raw_skills) if isinstance(raw_skills, str) else raw_skills
            skills = [] if skills is None else skills
            if not isinstance(skills, list):
                raise ValueError("skill_list must be a list")
            idx, cur_lv = None, 1
            for i, s in enumerate(skills):
                if isinstance(s, dict):
                    stored_id = int(s.get('skill_id', s.get('id', 0)))
                    stored_level = int(s.get('skill_level', s.get('level', 1)))
                elif isinstance(s, (list, tuple)) and len(s) >= 2:
                    stored_id, stored_level = int(s[0]), int(s[1])
                else:
                    raise ValueError("invalid skill record")
                if stored_id == sid:
                    if idx is not None or not 1 <= stored_level <= SKILL_MAX_LV:
                        raise ValueError("duplicate skill or invalid level")
                    idx = i
                    cur_lv = stored_level
        except (ValueError, TypeError):
            raise OperationError(406, "角色技能记录无效")

        if cur_lv + num > SKILL_MAX_LV:
            raise OperationError(2, f"技能升级数量超过等级上限({SKILL_MAX_LV})")

        new_lv = cur_lv + num
        total_factor = 0
        for lv in range(cur_lv, new_lv):
            cost = SKILL_COSTS.get(lv, {}).get(slot)
            if cost is None:
                raise OperationError(406, f"技能费用配置缺失: {lv}/{slot}")
            total_factor += cost

        if total_factor > 0:
            self._deduct_item(ctx, uid, 40301, total_factor)

        if idx is None:
            skills.append([sid, new_lv])
        elif isinstance(skills[idx], dict):
            key = 'skill_level' if 'skill_level' in skills[idx] else 'level'
            skills[idx][key] = new_lv
        else:
            skills[idx] = list(skills[idx])
            skills[idx][1] = new_lv
        db.execute("UPDATE hero SET skill_list=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(skills), int(time.time()), uid, hid))

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="skill_upgrade", skill_id=sid, new_level=new_lv)
        except Exception:
            pass

        return {
            "skill": sid,
            "level": new_lv,
            "hero_id": hid,
            "num": num,
            "factor_cost": total_factor
        }

    def enhance_skill_element(self, ctx, uid, hero_id, index, num=1):
        """
        技能属性/元素强化：cs_14044 -> sc_14045
        - index 1~5 槽位属性，封顶 15 级
        - 查询 hero_skill_element_cfg 消耗材料
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        idx = int(index)
        if isinstance(num, bool) or not isinstance(num, int) or num <= 0:
            raise OperationError(2, "属性强化数量必须为正整数")
        if not (1 <= idx <= 5):
            raise OperationError(2, f"index 越界: {idx}")

        row = db.query("SELECT skill_list, skill_intensify FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not row:
            raise OperationError(406, f"英雄 {hid} 不存在")

        try:
            intens = json.loads(row[0]["skill_intensify"] or "[]")
        except Exception:
            intens = []

        cur = 0
        for item in intens:
            if isinstance(item, list) and len(item) >= 2 and item[0] == idx:
                cur = int(item[1] or 0)
                break
        if cur >= ATTR_MAX_LV:
            raise OperationError(2, f"属性强化已满级({ATTR_MAX_LV})")
        if cur + num > ATTR_MAX_LV:
            raise OperationError(2, f"属性强化数量超过等级上限({ATTR_MAX_LV})")

        targets = list(range(cur + 1, cur + num + 1))
        if not targets:
            raise OperationError(2, "无可升级的等级")

        cost = {}
        for L in targets:
            cfgrow = db.query(
                "SELECT cost FROM hero_skill_element_cfg WHERE hero_id=? AND level=?",
                (hid, L))
            if not cfgrow:
                raise OperationError(2, f"属性强化配置缺失 hero={hid} level={L}")
            try:
                cost_json = json.loads(cfgrow[0]["cost"] or "{}")
            except Exception:
                cost_json = {}
            for c in cost_json.get(str(idx)) or []:
                if isinstance(c, list) and len(c) >= 2:
                    cost[c[0]] = cost.get(c[0], 0) + int(c[1])

        for item_id, need in cost.items():
            self._deduct_item(ctx, uid, item_id, need)

        newlv = cur + len(targets)
        found = False
        for item in intens:
            if isinstance(item, list) and len(item) >= 2 and item[0] == idx:
                item[1] = newlv
                found = True
                break
        if not found:
            intens.append([idx, newlv])

        db.execute("UPDATE hero SET skill_intensify=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(intens), int(time.time()), uid, hid))

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="skill_element_upgrade", index=idx, level=newlv)
        except Exception:
            pass

        return {"hero_id": hid, "index": idx, "level": newlv}

    # ==================== 4. 权钥与钥从系统 (Weapon & Servant) ====================

    def enhance_weapon(self, ctx, uid, hero_id, material_list=None, servant_list=None):
        """
        权钥经验升级：cs_46016 -> sc_46017
        - 经验材料：初级 100 / 中级 200 / 高级 1000
        - 钥从材料：按星级折算经验，由 ServantService 校验和消费
        - 1 exp = 1 金币
        - 按突破阶段经验封顶，溢出经验按低阶材料返还 (40203 -> 40202 -> 40201)
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        cur_break, cur_exp = _weapon_growth_state(db, uid, hid)
        rules = _get_weapon_growth_rules()
        material_costs, exp_add = _weapon_material_plan(material_list)
        slist = servant_list or []

        food = None
        if slist:
            from servant_service import ServantService
            food = ServantService.get_instance(db=db)
            food_plan = food.plan_servants_as_food(ctx, uid, slist)
            exp_add += food_plan["exp"]
        cap_exp = rules["level_exp"][rules["stage_levels"][cur_break]]
        new_exp = min(cur_exp + exp_add, cap_exp)

        # 1. 消耗强化材料
        for iid, num in material_costs.items():
            self._deduct_item(ctx, uid, iid, num)

        # 金币只收实际接收的经验；退还的溢出部分不收金币。
        gold = (new_exp - cur_exp) * rules["gold_per_exp"]
        if gold:
            self._deduct_item(ctx, uid, 2, gold)
        if food:
            consumed = food.consume_servants_as_food(ctx, uid, slist)
            if consumed != (len(food_plan["consumed"]), food_plan["exp"]):
                raise OperationError(2, "钥从材料状态发生变化")

        overflow_exp = max(0, (cur_exp + exp_add) - cap_exp)

        giveback_items = []
        if overflow_exp > 0:
            n40203 = overflow_exp // 1000
            rem = overflow_exp % 1000
            n40202 = rem // 200
            rem = rem % 200
            n40201 = rem // 100
            for gid, gnum in [(40203, n40203), (40202, n40202), (40201, n40201)]:
                if gnum > 0:
                    self._add_item(ctx, uid, gid, gnum)
                    giveback_items.append((gid, gnum))

        updated = db.execute("UPDATE hero SET weapon_exp=?, update_ts=? WHERE uid=? AND id=?",
                             (new_exp, int(time.time()), uid, hid))
        if updated.rowcount != 1:
            raise OperationError(406, "权钥目标角色更新失败")

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="weapon_upgrade", weapon_exp=new_exp, weapon_break=cur_break)
        except Exception:
            pass

        return {
            "hero_id": hid,
            "exp_added": exp_add,
            "new_exp": new_exp,
            "cur_break": cur_break,
            "giveback": giveback_items
        }

    def break_weapon(self, ctx, uid, hero_id):
        """
        权钥突破：cs_46018 -> sc_46019
        - 消耗泉萃取液与金币，递增突破阶数 (上限 4 破)
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        cur_break, cur_exp = _weapon_growth_state(db, uid, hid)
        rules = _get_weapon_growth_rules()
        if cur_break >= len(rules["stage_levels"]) - 1:
            raise OperationError(2, f"权钥已达最高突破阶段({cur_break})")
        if cur_exp < rules["level_exp"][rules["stage_levels"][cur_break]]:
            raise OperationError(2, "权钥尚未达到当前阶段等级上限")
        _weapon_break_eligibility(db, uid, rules, cur_break, cur_break + 1)

        for item_id, count in rules["break_costs"][cur_break]:
            self._deduct_item(ctx, uid, item_id, count)

        new_break = cur_break + 1
        updated = db.execute("UPDATE hero SET weapon_break=?, update_ts=? WHERE uid=? AND id=?",
                             (new_break, int(time.time()), uid, hid))
        if updated.rowcount != 1:
            raise OperationError(406, "权钥目标角色更新失败")

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="weapon_upgrade", weapon_break=new_break)
        except Exception:
            pass

        return {"hero_id": hid, "new_break": new_break}

    def quick_upgrade_weapon(self, ctx, uid, hero_id, material_list=None, servant_list=None, target_level=0, breakthrough_times=0):
        """权钥一键升级/突破：cs_46034 -> sc_46035"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        cur_break, cur_exp = _weapon_growth_state(db, uid, hid)
        rules = _get_weapon_growth_rules()
        material_costs, exp_add = _weapon_material_plan(material_list)
        slist = servant_list or []
        if type(target_level) is not int or not 1 <= target_level <= rules["stage_levels"][-1]:
            raise OperationError(2, "权钥目标等级无效")
        if type(breakthrough_times) is not int or not 0 <= breakthrough_times < len(rules["stage_levels"]) - cur_break:
            raise OperationError(2, "权钥突破次数无效")

        # A boundary target may either stop at the cap or enable its next tier.
        required_break = cur_break
        while target_level > rules["stage_levels"][required_break]:
            required_break += 1
        final_break = cur_break + breakthrough_times
        allowed = {required_break}
        if (required_break < len(rules["stage_levels"]) - 1
                and target_level == rules["stage_levels"][required_break]):
            allowed.add(required_break + 1)
        if final_break not in allowed:
            raise OperationError(2, "权钥目标等级与突破次数不一致")
        new_exp = rules["level_exp"][target_level]
        if new_exp < cur_exp:
            raise OperationError(2, "权钥目标等级低于当前经验")
        _weapon_break_eligibility(db, uid, rules, cur_break, final_break)

        food = None
        if slist:
            from servant_service import ServantService
            food = ServantService.get_instance(db=db)
            food_plan = food.plan_servants_as_food(ctx, uid, slist)
            exp_add += food_plan["exp"]
        needed_exp = new_exp - cur_exp
        if exp_add < needed_exp:
            raise OperationError(2, "经验材料不足以达到权钥目标等级")

        for iid, num in material_costs.items():
            self._deduct_item(ctx, uid, iid, num)

        if needed_exp:
            self._deduct_item(ctx, uid, 2, needed_exp * rules["gold_per_exp"])

        for stage in range(cur_break, final_break):
            for item_id, count in rules["break_costs"][stage]:
                self._deduct_item(ctx, uid, item_id, count)
        if food:
            consumed = food.consume_servants_as_food(ctx, uid, slist)
            if consumed != (len(food_plan["consumed"]), food_plan["exp"]):
                raise OperationError(2, "钥从材料状态发生变化")

        cur_break = final_break
        overflow_exp = exp_add - needed_exp

        refund_items = []
        if overflow_exp > 0:
            n40203 = overflow_exp // 1000
            rem = overflow_exp % 1000
            n40202 = rem // 200
            rem = rem % 200
            n40201 = rem // 100
            for gid, gnum in [(40203, n40203), (40202, n40202), (40201, n40201)]:
                if gnum > 0:
                    self._add_item(ctx, uid, gid, gnum)
                    refund_items.append({"id": gid, "num": gnum})

        updated = db.execute("UPDATE hero SET weapon_exp=?, weapon_break=?, update_ts=? WHERE uid=? AND id=?",
                             (new_exp, cur_break, int(time.time()), uid, hid))
        if updated.rowcount != 1:
            raise OperationError(406, "权钥目标角色更新失败")

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="weapon_upgrade", weapon_exp=new_exp, weapon_break=cur_break)
        except Exception:
            pass

        return {"hero_id": hid, "new_break": cur_break, "new_exp": new_exp, "refund_items": refund_items}

    def replace_servant(self, ctx, uid, hero_id, servant_id):
        """
        钥从佩戴/更换：cs_46020 -> sc_46021
        - 自动解绑该钥从之前的佩戴者
        - 将其装配到指定 hero_id
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        suid = int(servant_id)
        if suid > 0:
            from servant_service import ServantService
            ServantService.get_instance(db=db).validate_servant_available(uid, suid, db=db)
        db.execute("UPDATE hero SET weapon_servant_uid=0 WHERE uid=? AND weapon_servant_uid=?", (uid, suid))
        db.execute("UPDATE hero SET weapon_servant_uid=?, update_ts=? WHERE uid=? AND id=?", (suid, int(time.time()), uid, hid))
        return {"hero_id": hid, "servant_id": suid}

    # ==================== 5. 神格矩阵体系 (Astrolabe System) ====================

    def unlock_astrolabe_node(self, ctx, uid, hero_id, astrolabe_id):
        """神格节点解锁：cs_14026 -> sc_14027"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        aid = int(astrolabe_id)
        hrow = db.query("SELECT unlock_astrolabe FROM hero WHERE uid=? AND id=?", (uid, hid))
        try:
            unlocked = json.loads(hrow[0]["unlock_astrolabe"]) if hrow and hrow[0]["unlock_astrolabe"] else []
        except Exception:
            unlocked = []
        if aid and aid not in unlocked:
            unlocked.append(aid)
            db.execute("UPDATE hero SET unlock_astrolabe=?, update_ts=? WHERE uid=? AND id=?",
                       (json.dumps(unlocked), int(time.time()), uid, hid))
            try:
                event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="astrolabe_unlock", astrolabe_id=aid)
            except Exception:
                pass
        return {"hero_id": hid, "astrolabe_id": aid}

    def equip_astrolabe_node(self, ctx, uid, hero_id, astrolabe_id, operation):
        """
        神格装配/卸下：cs_14028 -> sc_14029
        - op=1 装配：最多 3 槽位，溢出时先进先出 FIFO 弹出首个
        - op=2 卸下：从当前槽位中移除
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        aid = int(astrolabe_id)
        op_type = int(operation or 1)
        hrow = db.query("SELECT using_astrolabe FROM hero WHERE uid=? AND id=?", (uid, hid))
        try:
            using = json.loads(hrow[0]["using_astrolabe"]) if hrow and hrow[0]["using_astrolabe"] else []
        except Exception:
            using = []

        if op_type == 1:
            if aid and aid not in using:
                if len(using) >= 3:
                    using.pop(0)
                using.append(aid)
        elif op_type == 2:
            if aid in using:
                using.remove(aid)

        db.execute("UPDATE hero SET using_astrolabe=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(using), int(time.time()), uid, hid))
        return {"hero_id": hid, "astrolabe_id": aid, "operation": op_type, "using": using}

    def unload_all_astrolabe(self, ctx, uid, hero_id):
        """神格一键全部卸下：cs_14040 -> sc_14041"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        db.execute("UPDATE hero SET using_astrolabe='[]', update_ts=? WHERE uid=? AND id=?", (int(time.time()), uid, hid))
        return {"hero_id": hid}

    def equip_astrolabe_suit(self, ctx, uid, hero_id, astrolabe_suit_id):
        """神格流派一键装配：cs_14038 -> sc_14039 (三连节点自动激活并装配)"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        suit_id = int(astrolabe_suit_id or 0)
        nodes = [suit_id * 10 + 1, suit_id * 10 + 2, suit_id * 10 + 3] if suit_id else []

        # 写入正在使用的神格
        db.execute("UPDATE hero SET using_astrolabe=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(nodes), int(time.time()), uid, hid))

        # 确保节点在已解锁列表中
        hrow = db.query("SELECT unlock_astrolabe FROM hero WHERE uid=? AND id=?", (uid, hid))
        try:
            unlocked = json.loads(hrow[0]["unlock_astrolabe"]) if hrow and hrow[0]["unlock_astrolabe"] else []
        except Exception:
            unlocked = []
        dirty = False
        for n in nodes:
            if n not in unlocked:
                unlocked.append(n)
                dirty = True
        if dirty:
            db.execute("UPDATE hero SET unlock_astrolabe=? WHERE uid=? AND id=?", (json.dumps(unlocked), uid, hid))

        return {"hero_id": hid, "suit_id": suit_id, "using": nodes}

    def equip_astrolabe_list(self, ctx, uid, hero_id, astrolabe_id_list):
        """神格批量装配：cs_71116 -> sc_71117"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        lst = [int(x) for x in (astrolabe_id_list or []) if str(x).isdigit()]
        db.execute("UPDATE hero SET using_astrolabe=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(lst), int(time.time()), uid, hid))

        hrow = db.query("SELECT unlock_astrolabe FROM hero WHERE uid=? AND id=?", (uid, hid))
        try:
            unlocked = json.loads(hrow[0]["unlock_astrolabe"]) if hrow and hrow[0]["unlock_astrolabe"] else []
        except Exception:
            unlocked = []
        dirty = False
        for n in lst:
            if n and n not in unlocked:
                unlocked.append(n)
                dirty = True
        if dirty:
            db.execute("UPDATE hero SET unlock_astrolabe=? WHERE uid=? AND id=?", (json.dumps(unlocked), uid, hid))

        return {"hero_id": hid, "list": lst}

    # ==================== 6. 跃迁与同调武器模组 (Transition & Module) ====================

    def improve_transition_gift_pt(self, ctx, uid, hero_id, slot_id, lv_up_num=1):
        """
        跃迁槽位天赋点强化：cs_14112 -> sc_14113
        - slot_id 1~6，天赋点上限 6
        - 扣除金币与跃迁材料 (41301~41303)
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        sid = int(slot_id)
        num = max(1, int(lv_up_num or 1))

        hrow = db.query("SELECT exclusive_skill_list FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not hrow:
            raise OperationError(406, f"英雄 {hid} 不存在")
        from hero_codec import normalize_exclusive_skills
        ex_skills = normalize_exclusive_skills(hrow[0]["exclusive_skill_list"])

        cur_slot = ex_skills.setdefault(str(sid), {"talent_points": 0, "skill_list": []})
        cur_pts = int(cur_slot.get("talent_points") or 0)
        new_pts = min(6, cur_pts + num)

        for lvl in range(cur_pts + 1, new_pts + 1):
            costs = TRANSITION_TALENT_COST.get(lvl) or []
            for item_id, count in costs:
                self._deduct_item(ctx, uid, item_id, count)

        cur_slot["talent_points"] = new_pts
        ex_skills[str(sid)] = cur_slot
        db.execute("UPDATE hero SET exclusive_skill_list=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(ex_skills), int(time.time()), uid, hid))

        try:
            event_bus.bus.emit(event_bus.Events.HERO_UPGRADE, ctx, uid, hero_id=hid, oper="transition_upgrade", slot_id=sid, talent_points=new_pts)
        except Exception:
            pass

        return {"hero_id": hid, "slot_id": sid, "talent_points": new_pts, "lv_up_num": num}

    def save_transition_skill(self, ctx, uid, hero_id, slot_id, skill_list):
        """保存跃迁词条技能组合：cs_14114 -> sc_14115"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        sid = int(slot_id)
        skills = skill_list or []

        hrow = db.query("SELECT exclusive_skill_list FROM hero WHERE uid=? AND id=?", (uid, hid))
        from hero_codec import normalize_exclusive_skills
        raw_val = hrow[0]["exclusive_skill_list"] if hrow and hrow[0]["exclusive_skill_list"] else "{}"
        ex_skills = normalize_exclusive_skills(raw_val)

        cur_slot = ex_skills.setdefault(str(sid), {"talent_points": 0, "skill_list": []})
        clean_skills = []
        for sk in skills:
            if isinstance(sk, dict):
                sid_val = int(sk.get("skill_id") or sk.get("id") or 0)
                slv_val = int(sk.get("skill_level") or sk.get("level") or 1)
            elif isinstance(sk, (list, tuple)) and len(sk) >= 2:
                sid_val, slv_val = int(sk[0]), int(sk[1])
            else:
                continue
            if sid_val > 0:
                clean_skills.append({"skill_id": sid_val, "skill_level": slv_val})

        cur_slot["skill_list"] = clean_skills
        ex_skills[str(sid)] = cur_slot
        db.execute("UPDATE hero SET exclusive_skill_list=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(ex_skills), int(time.time()), uid, hid))
        return {"hero_id": hid, "slot_id": sid, "skills": clean_skills}

    def level_up_module(self, ctx, uid, hero_id):
        """同调模组首次开启及升级，按当前级别核任务/条件/费用。"""
        db = getattr(ctx, "db", self.db)
        if db is None:
            raise OperationError(406, "模组操作缺少数据库")
        from account_db import savepoint_transaction
        from operation_notifications import checkpoint_notifications, restore_notifications
        from types import SimpleNamespace
        context = ctx if ctx is not None else SimpleNamespace(db=db, touched_items=set())
        checkpoint = checkpoint_notifications(context)
        try:
            with savepoint_transaction(db):
                return self._apply_module_level(context, uid, hero_id)
        except BaseException:
            restore_notifications(context, checkpoint)
            raise

    def _apply_module_level(self, ctx, uid, hero_id):
        db = ctx.db
        hid = int(hero_id)
        hrow = db.query("SELECT * FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not hrow:
            raise OperationError(406, f"英雄 {hid} 不存在")
        if int(hrow[0].get("unlock") or 0) <= 0:
            raise OperationError(406, f"英雄 {hid} 尚未解锁")
        max_level = _get_weapon_module_max_levels().get(hid)
        if max_level is None:
            raise OperationError(406, f"英雄 {hid} 没有武器模块配置")
        primary_module_level = hrow[0].get("weapon_module_level")
        if primary_module_level is None:
            # Legacy compatibility is valid only when the primary field has
            # not been initialized. A stored zero is an authoritative lock.
            primary_module_level = hrow[0].get("module_level")
        cur_m_lv = int(primary_module_level or 0)
        if cur_m_lv < 0 or cur_m_lv >= max_level:
            raise OperationError(406, f"英雄 {hid} 武器模块已达到上限 {max_level}")
        if cur_m_lv == 0:
            assignment = hrow[0].get("weapon_module_assignment")
            if assignment is None:
                assignment = hrow[0].get("module_assignment")
            if int(assignment or 0) <= 0:
                raise OperationError(406, f"英雄 {hid} 模组初始任务尚未完成")
        rules = _get_weapon_module_rules().get(str(hid))
        if not isinstance(rules, list) or len(rules) != max_level:
            raise OperationError(406, f"英雄 {hid} 模组资格/消耗配置缺失")
        rule = rules[cur_m_lv]
        for condition in rule["conditions"]:
            if not _module_condition_met(db, uid, hrow[0], condition):
                raise OperationError(406, f"英雄 {hid} 模组条件 {condition['id']} 未达成")
        costs = rule["cost"]
        if not costs or any(len(pair) != 2 or int(pair[0]) <= 0 or int(pair[1]) <= 0 for pair in costs):
            raise OperationError(406, f"英雄 {hid} 模组消耗配置无效")
        for item_id, count in costs:
            self._deduct_item(ctx, uid, int(item_id), int(count))
        new_m_lv = cur_m_lv + 1
        changed = db.execute(
            "UPDATE hero SET weapon_module_level=?, module_level=?, update_ts=? "
            "WHERE uid=? AND id=? AND COALESCE(weapon_module_level,module_level,0)=?",
            (new_m_lv, new_m_lv, int(time.time()), uid, hid, cur_m_lv))
        if changed.rowcount != 1:
            raise OperationError(406, "模组等级已变化，请刷新后重试")
        return {"hero_id": hid, "level": new_m_lv}

    # ==================== 7. 外观形态与个性化 (Customization & Chips) ====================

    def set_using_skin(self, ctx, uid, hero_id, skin_id):
        """切换角色界面展示立绘与大厅看板：cs_14034 -> sc_14035。
        【核心分立机制】：只更新 using_skin，绝对严禁同步改动 battle_using_skin！"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        sid = int(skin_id)
        hrow = db.query("SELECT id, unlock, battle_using_skin FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not hrow or not hrow[0]["unlock"]:
            raise OperationError(406, f"英雄 {hid} 未解锁")

        if sid != 0 and sid != hid:
            skin_map = _get_skin_hero_map()
            expected_hid = skin_map.get(str(sid)) or skin_map.get(sid)
            if expected_hid and int(expected_hid) != hid:
                raise OperationError(406, f"皮肤 {sid} 不属于英雄 {hid}")
            srow = db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, sid))
            if not srow:
                raise OperationError(406, f"皮肤 {sid} 尚未解锁")

        # 核心分立：只更新 using_skin，保持 battle_using_skin 绝对独立！
        db.execute("UPDATE hero SET using_skin=?, update_ts=? WHERE uid=? AND id=?",
                   (sid, int(time.time()), uid, hid))

        # 若该角色正是当前大厅看板娘，联动刷新看板娘穿戴形态
        try:
            from peripheral_service import _load_user_extra, _save_user_extra
            extra = _load_user_extra(db, uid)
            gu = db.query("SELECT board_hero FROM game_user WHERE uid=?", (uid,))
            is_board_hero = (gu and int(gu[0]["board_hero"] or 0) == hid) or (extra.get("active_board_hero") == hid)
            if is_board_hero:
                extra["active_board_skin"] = sid if sid > 0 else hid
                _save_user_extra(db, uid, extra)
                event_bus.bus.emit(event_bus.Events.PROFILE_UPDATE, ctx, uid, kind="poster_girl_skin", value={"hero_id": hid, "skin_id": sid})
        except Exception as e:
            logger.warning(f"看板娘皮肤状态联动异常: {e}")

        cur_battle_skin = int(hrow[0]["battle_using_skin"] or 0)
        return {"hero_id": hid, "skin_id": sid, "battle_skin_id": cur_battle_skin}

    def set_battle_skin(self, ctx, uid, hero_id, skin_id):
        """独立切换战斗出战皮肤：cs_14046 -> sc_14047。
        【核心分立机制】：由界面【作战换装 [开/关]】独立控制，只更新 battle_using_skin！"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        sid = int(skin_id)
        hrow = db.query("SELECT id, unlock FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not hrow or not hrow[0]["unlock"]:
            raise OperationError(406, f"英雄 {hid} 未解锁")

        if sid != 0 and sid != hid:
            skin_map = _get_skin_hero_map()
            expected_hid = skin_map.get(str(sid)) or skin_map.get(sid)
            if expected_hid and int(expected_hid) != hid:
                raise OperationError(406, f"战斗皮肤 {sid} 不属于英雄 {hid}")
            srow = db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, sid))
            if not srow:
                raise OperationError(406, f"战斗皮肤 {sid} 尚未解锁")

        db.execute("UPDATE hero SET battle_using_skin=?, update_ts=? WHERE uid=? AND id=?",
                   (sid, int(time.time()), uid, hid))
        return {"hero_id": hid, "skin_id": sid}

    def unlock_skin(self, ctx, uid, skin_id):
        """皮肤解锁：cs_14110 -> sc_14111。
        原子写入 player_skin_unlocked，联动专属头像与伴生专属大厅场景。"""
        db = getattr(ctx, "db", self.db)
        sid = int(skin_id or 0)
        if sid <= 0:
            raise OperationError(406, "皮肤ID非法")
        skin_map = _get_skin_hero_map()
        hid = skin_map.get(str(sid)) or skin_map.get(sid)
        if not hid:
            if sid >= 100000:
                hid = int(str(sid)[:4])
            else:
                hid = sid

        now_ts = int(time.time())
        # 1. 写入已解锁皮肤 player_skin_unlocked
        if hasattr(db, "upsert"):
            db.upsert("player_skin_unlocked", uid, {"skin_id": sid, "unlock_ts": now_ts, "update_ts": now_ts}, keys=("uid", "skin_id"))
        else:
            db.execute("INSERT OR REPLACE INTO player_skin_unlocked (uid, skin_id, unlock_ts, update_ts) VALUES (?, ?, ?, ?)",
                       (uid, sid, now_ts, now_ts))

        # 2. 联动解锁专属头像（写入 player_card 表 kind='portrait'）
        from peripheral_service import PeripheralService
        portrait_id = PeripheralService(db).unlock_acquisition_portrait(uid, sid, ctx)
        event_bus.bus.emit(event_bus.Events.SKIN_UNLOCK, ctx, uid, skin_id=sid,
                           hero_id=int(hid), portrait_id=portrait_id, source='skin_unlock')

        # 3. 联动解锁专属伴生大厅场景（查 skin_scene_map.json）
        scene_map = _get_skin_scene_map()
        scene_id = scene_map.get(str(sid)) or scene_map.get(sid)
        if scene_id:
            scene_iid = int(scene_id)
            try:
                from inventory_service import InventoryService
                InventoryService.grant_items(ctx, uid, [(scene_iid, 1)], source="skin_companion_scene")
            except Exception as e:
                logger.warning(f"伴生场景 {scene_iid} 解锁发放异常: {e}")
                db.execute("INSERT OR REPLACE INTO user_scene (uid, scene_id, lasted_time, obtain_time, update_ts) VALUES (?, ?, 0, ?, ?)",
                           (uid, scene_iid, now_ts, now_ts))
            db.execute("INSERT OR REPLACE INTO scene (uid, scene_id, name, scene_type, is_current, obtain_time) VALUES (?, ?, '', 1, 0, ?)",
                       (uid, scene_iid, now_ts))
            if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                ctx.touched_items.add(scene_iid)

        return {"hero_id": int(hid), "skin_id": sid, "portrait_id": portrait_id, "scene_id": scene_id}

    def set_favorite(self, ctx, uid, hero_id, is_favorite):
        """标记/取消特别关注：cs_14106 / cs_14108"""
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        fav = 1 if is_favorite else 0
        db.execute("UPDATE hero SET is_favorite=?, update_ts=? WHERE uid=? AND id=?", (fav, int(time.time()), uid, hid))
        return {"hero_id": hid, "is_favorite": fav}

    def set_hero_chip(self, ctx, uid, hero_id, slot_id, secondary_chip):
        """装配/置换角色专属 AI 战术芯片：cs_50018 -> sc_50019 (统一委托至 ChipService)"""
        from chip_service import ChipService
        return ChipService.get_instance(getattr(ctx, "db", self.db)).enable_hero_chip(ctx, uid, hero_id, slot_id, secondary_chip)

    # ==================== 8. 刻印佩戴装配联动 (Equip-Hero Association) ====================

    def get_hero_equip_slots(self, uid, hero_id):
        """读取 hero.equip_slot JSON 映射 {"1": equip_id, ..., "6": equip_id}"""
        db = self.db
        rows = db.query("SELECT equip_slot FROM hero WHERE uid=? AND id=?", (uid, int(hero_id)))
        if not rows:
            return {}
        try:
            d = json.loads(rows[0]["equip_slot"] or "{}")
        except Exception:
            d = {}
        return d if isinstance(d, dict) else {}

    def swap_equip(self, ctx, uid, hero_id, equip_id, pos):
        """
        刻印穿戴/置换：cs_13012 -> sc_13013
        - 验证 pos 1~6
        - 若刻印原本装在其他角色身上，自动从对方槽位中卸下
        - 同步更新 equip.hero_id 与双方 hero.equip_slot，确保双端绝对一致
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        eid = int(equip_id)
        p = int(pos)
        if not (1 <= p <= 6):
            raise OperationError(2, f"刻印槽位越界: pos={p}（应为 1~6）")

        eq = db.query("SELECT id, hero_id FROM equip WHERE uid=? AND id=?", (uid, eid))
        if not eq:
            raise OperationError(406, f"刻印 {eid} 不存在")

        slots = self.get_hero_equip_slots(uid, hid)
        prev_owner = int(eq[0]["hero_id"] or 0)

        # 1. 若曾属于别的角色，从原拥有者槽位卸下
        if prev_owner and prev_owner != hid:
            other_slots = self.get_hero_equip_slots(uid, prev_owner)
            other_slots = {k: v for k, v in other_slots.items() if int(v or 0) != eid}
            db.execute("UPDATE hero SET equip_slot=?, update_ts=? WHERE uid=? AND id=?",
                       (json.dumps({str(k): int(v) for k, v in other_slots.items() if v}), int(time.time()), uid, prev_owner))

        # 2. 若当前槽位已有刻印，卸下原刻印
        old_eid = int(slots.get(str(p)) or 0)
        if old_eid and old_eid != eid:
            db.execute("UPDATE equip SET hero_id=0 WHERE uid=? AND id=?", (uid, old_eid))

        # 3. 装配新刻印
        db.execute("UPDATE equip SET hero_id=? WHERE uid=? AND id=?", (hid, uid, eid))
        slots[str(p)] = eid
        db.execute("UPDATE hero SET equip_slot=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps({str(k): int(v) for k, v in slots.items() if v}), int(time.time()), uid, hid))

        return {"hero_id": hid, "equip_id": eid, "pos": p, "replaced": old_eid, "prev_owner": prev_owner}

    def quick_dress_equips(self, ctx, uid, hero_id, use_equip_list):
        """
        一键快速穿戴刻印：cs_13026 -> sc_13027
        use_equip_list: [{"pos": int, "equip_id": int}, ...]
        sc_13027 规范：result 字段为 repeated use_equip_return [{"equip_id": eid, "result": 0}, ...]
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        affected_heroes = {hid}
        dressed_results = []

        slots = self.get_hero_equip_slots(uid, hid)

        for item in (use_equip_list or []):
            p = int(item.get("pos") or 0)
            eid = int(item.get("equip_id") or 0)
            if not (1 <= p <= 6) or not eid:
                continue

            eq = db.query("SELECT id, hero_id FROM equip WHERE uid=? AND id=?", (uid, eid))
            if not eq:
                dressed_results.append({"equip_id": eid, "result": 2})
                continue

            prev_owner = int(eq[0]["hero_id"] or 0)
            if prev_owner and prev_owner != hid:
                affected_heroes.add(prev_owner)
                other_slots = self.get_hero_equip_slots(uid, prev_owner)
                other_slots = {k: v for k, v in other_slots.items() if int(v or 0) != eid}
                db.execute("UPDATE hero SET equip_slot=?, update_ts=? WHERE uid=? AND id=?",
                           (json.dumps({str(k): int(v) for k, v in other_slots.items() if v}), int(time.time()), uid, prev_owner))

            old_eid = int(slots.get(str(p)) or 0)
            if old_eid and old_eid != eid:
                db.execute("UPDATE equip SET hero_id=0 WHERE uid=? AND id=?", (uid, old_eid))

            db.execute("UPDATE equip SET hero_id=? WHERE uid=? AND id=?", (hid, uid, eid))
            slots[str(p)] = eid
            dressed_results.append({"equip_id": eid, "result": 0})

        db.execute("UPDATE hero SET equip_slot=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps({str(k): int(v) for k, v in slots.items() if v}), int(time.time()), uid, hid))

        return {
            "hero_id": hid,
            "result": dressed_results,
            "affected_heroes": list(affected_heroes)
        }

    def save_hero_equip_slots(self, uid, hero_id, slots):
        """保存 hero.equip_slot JSON 映射"""
        db = self.db
        db.execute("UPDATE hero SET equip_slot=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps({str(k): int(v) for k, v in slots.items() if v}), int(time.time()), uid, int(hero_id)))

    def unload_all_equips(self, ctx, uid, hero_id):
        """
        全部卸下刻印：cs_13018 -> sc_13019
        - 保留已锁定 (is_lock=1) 的刻印
        - 卸下所有未锁刻印并清空 hero.equip_slot 对应槽位
        """
        db = getattr(ctx, "db", self.db)
        hid = int(hero_id)
        slots = self.get_hero_equip_slots(uid, hid)
        locked_rows = db.query("SELECT id FROM equip WHERE uid=? AND hero_id=? AND is_lock=1", (uid, hid))
        locked = {r["id"] for r in locked_rows} if locked_rows else set()

        db.execute("UPDATE equip SET hero_id=0 WHERE uid=? AND hero_id=? AND is_lock=0", (uid, hid))
        kept = {p: e for p, e in slots.items() if int(e or 0) in locked}
        db.execute("UPDATE hero SET equip_slot=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps({str(k): int(v) for k, v in kept.items() if v}), int(time.time()), uid, hid))

        return {"hero_id": hid, "removed": len(slots) - len(kept), "kept_locked": len(kept)}

    def bind_equip_hero(self, ctx, uid, equip_id, hero_id):
        """
        刻印角色专属重构绑定：cs_13046 -> sc_13047
        - 消耗金币 10000 + 重构秘仪 20 (40701)
        - 刻印专属共鸣将 race 与 race_hero 均标记为 hero_id（客户端判定 slot0.race == hero_id 触发 40% 专属共鸣）
        """
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        hid = int(hero_id)
        eq = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, eid))
        if not eq:
            raise OperationError(406, f"刻印 {eid} 不存在")

        self._deduct_item(ctx, uid, 2, 10000)
        self._deduct_item(ctx, uid, 40701, 20)

        db.execute("UPDATE equip SET race=?, race_hero=?, update_ts=? WHERE uid=? AND id=?",
                   (hid, hid, int(time.time()), uid, eid))
        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid, oper="reconstruct_hero", hero_id=hid)
        except Exception:
            pass
        return {"equip_id": eid, "hero_id": hid, "race": hid}

    # ==================== 9. 下行推送与标准 GM 接口 (Frame & GM API) ====================

    def build_hero_refresh_frames(self, ctx, uid, hero_id=None, *, strict=False):
        """
        统一角色养成原子差量刷新帧：
        - sc_17023: 原子差量变动帧，先于本函数生成的英雄帧。
        - sc_14007: 英雄即时全量同步帧（技能、突破、等级等）
        strict路径在提交前构建，必要帧失败向外传播；业务ACK相对位置须按客户端回调确定。
        """
        out = []
        db = getattr(ctx, "db", self.db)
        if not (db and uid):
            if strict:
                raise ValueError("必要角色刷新缺少数据库或uid")
            return out

        # 1. 优先调取通用 generator 刷新变动的资产与钥从 (sc_17023)，确保客户端原生数据先落地
        try:
            import generator as _gen
            touched = getattr(ctx, "touched_items", None)
            rem_eq = getattr(ctx, "removed_equips", None)
            rem_wl = getattr(ctx, "removed_weapons", None)
            del_servants = getattr(ctx, "deleted_servants", None)
            if del_servants:
                wl_from_del = [{"uid": x["uid"], "item": {"id": x["id"], "num": 0}} for x in del_servants if x.get("id")]
                rem_wl = (rem_wl or []) + wl_from_del

            if touched is not None or rem_eq or rem_wl:
                p_diff = _gen.gen_payload(17023, uid=uid, db=db, touched_items=(touched or set()), equip_list=rem_eq, weapon_list=rem_wl, strict=strict)
                if p_diff:
                    out.append(DownFrame(17023, p_diff))
                elif strict and (touched or rem_eq or rem_wl):
                    raise ValueError("必要资产差量sc_17023为空")
        except Exception as e:
            if strict:
                raise
            logger.warning(f"生成原子差量帧 17023 异常: {e}")

        # 2. 刷新英雄自身全量数据 (sc_14007)
        if hero_id:
            try:
                import hero_codec as _hc
                hf = _hc.build_hero_14007_frame(db, uid, int(hero_id))
                if hf:
                    out.append(hf)
                elif strict:
                    raise ValueError(f"必要角色刷新sc_14007为空: hero={hero_id}")
            except Exception as e:
                if strict:
                    raise
                logger.warning(f"构建 sc_14007 异常: {e}")

        if hasattr(ctx, "pop_pending_frames"):
            out.extend(ctx.pop_pending_frames())
        return out

    def build_diff_frames(self, ctx, uid):
        """仅下发原子差量变动帧 (sc_17023) 与挂起帧，不包含角色 14007 全量帧"""
        return self.build_hero_refresh_frames(ctx, uid, hero_id=None)

    def build_hero_14007_frame(self, ctx, uid, hero_id):
        """下发单角色即时全量权威同步帧 (sc_14007)"""
        if not hero_id:
            return []
        db = getattr(ctx, "db", self.db)
        try:
            import hero_codec as _hc
            hf = _hc.build_hero_14007_frame(db, uid, int(hero_id))
            return [hf] if hf else []
        except Exception as e:
            logger.warning(f"构建 sc_14007 异常: {e}")
            return []

    # ---------- GM 底层标准化接口 ----------

    def max_out_hero(self, *args, **kwargs):
        """
        [GM 工具] 一键满配指定角色：
        - 满等级 80、满突破 5
        - 满神识超越 Ω (star=600)
        - 满权钥 (Lv60, breakthrough=4, exp=99800)
        - 技能全满 (35级)
        - 同调武器模块满级 3
        - 神格全部解锁
        支持 max_out_hero(ctx, uid, hero_id) 或 max_out_hero(uid, hero_id)
        """
        if len(args) >= 3:
            ctx, uid, hero_id = args[0], args[1], args[2]
            db = getattr(ctx, "db", self.db)
        elif len(args) == 2:
            uid, hero_id = args[0], args[1]
            db = self.db
        else:
            uid = kwargs.get("uid")
            hero_id = kwargs.get("hero_id")
            db = kwargs.get("db", self.db)

        hid = int(hero_id)
        row = db.query("SELECT id, skill_list, unlock_astrolabe FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not row:
            raise OperationError(406, f"英雄 {hid} 不存在")

        # 技能全设为 35
        try:
            skills = json.loads(row[0]["skill_list"] or "[]")
        except Exception:
            skills = []
        for s in skills:
            if isinstance(s, list) and len(s) >= 2:
                s[1] = 35

        # 神格 1~9 全解
        unlocked = [hid * 10 + i for i in range(1, 10)]

        now = int(time.time())
        db.execute(
            """
            UPDATE hero SET 
                level=80,
                exp=0,
                break_level=5,
                star=600,
                weapon_break=4,
                weapon_exp=99800,
                weapon_module_level=3,
                module_level=3,
                skill_list=?,
                unlock_astrolabe=?,
                update_ts=?
            WHERE uid=? AND id=?
            """,
            (json.dumps(skills), json.dumps(unlocked), now, uid, hid)
        )
        return {"hero_id": hid, "status": "max_out_success"}

    def unlock_all_heroes(self, *args, **kwargs):
        """
        [GM 工具] 一键解锁所有官方角色
        支持 unlock_all_heroes(ctx, uid) 或 unlock_all_heroes(uid)
        """
        if len(args) >= 2:
            ctx, uid = args[0], args[1]
            db = getattr(ctx, "db", self.db)
        elif len(args) == 1:
            uid = args[0]
            db = self.db
        else:
            uid = kwargs.get("uid")
            db = kwargs.get("db", self.db)

        now = int(time.time())
        cfg_heroes = db.query("SELECT hero_id FROM hero_cfg")
        if not cfg_heroes:
            cfg_heroes = db.query("SELECT DISTINCT hero_id FROM draw_hero_pool")
        
        unlocked_count = 0
        for r in cfg_heroes:
            hid = r.get("hero_id")
            if not hid:
                continue
            db.execute(
                """
                INSERT INTO hero (uid, id, level, star, exp, break_level, unlock, update_ts)
                VALUES (?, ?, 1, 100, 0, 0, 1, ?)
                ON CONFLICT(uid, id) DO UPDATE SET unlock=1, update_ts=excluded.update_ts
                """,
                (uid, hid, now)
            )
            unlocked_count += 1
        return {"unlocked_count": unlocked_count}

    def reset_hero(self, *args, **kwargs):
        """
        [GM 工具] 重置指定角色初始状态
        支持 reset_hero(ctx, uid, hero_id) 或 reset_hero(uid, hero_id)
        """
        if len(args) >= 3:
            ctx, uid, hero_id = args[0], args[1], args[2]
            db = getattr(ctx, "db", self.db)
        elif len(args) == 2:
            uid, hero_id = args[0], args[1]
            db = self.db
        else:
            uid = kwargs.get("uid")
            hero_id = kwargs.get("hero_id")
            db = kwargs.get("db", self.db)

        hid = int(hero_id)
        now = int(time.time())
        db.execute(
            """
            UPDATE hero SET 
                level=1,
                exp=0,
                break_level=0,
                star=100,
                weapon_break=0,
                weapon_exp=0,
                weapon_module_level=0,
                module_level=0,
                using_astrolabe='[]',
                equip_slot='{}',
                update_ts=?
            WHERE uid=? AND id=?
            """,
            (now, uid, hid)
        )
        return {"hero_id": hid, "status": "reset_success"}
