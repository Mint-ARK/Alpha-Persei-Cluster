# -*- coding: utf-8 -*-
"""
minigame_service.py — 常驻小游戏服务中枢 (MiniGame Service)

集中纳管《深空之眼》全量常驻/轻量小游戏（海拉弹珠、卡达斯假日赛·坦克竞速、灰烬牛仔等）：
- 模块化纳管局外成长进度持久化、局内通关结算、技能/装备装配与挑战最高分记录；
- 统筹调用 inventory_service 统一资产/道具出入库并保持差量追踪；
- 触发全局 EventBus 广播（STAGE_PASS, STAGE_FIRST_CLEAR 等），与任务系统（task_listener）无缝联动；
- 驱动登录洪流动态帧生成（sc_89401, sc_84331 等）。
"""

import os
import json
import time
import logging

from core import Operation, OperationError, operation
from middleware import DownFrame
import event_bus
from event_bus import bus, Events
from inventory_service import InventoryService

logger = logging.getLogger("minigame_service")

# ---------------- 1. 远村异闻 (海拉弹珠, Activity 3840801) 元数据 ----------------
PINBALL_STAGES = {
    40601: {"lv_up": 2, "skill_unlock": 40603, "barrier_type": 1},
    40602: {"lv_up": 3, "skill_unlock": 40607, "barrier_type": 1},
    40603: {"lv_up": 4, "skill_unlock": 40610, "barrier_type": 1},
    40604: {"lv_up": 5, "skill_unlock": 40613, "barrier_type": 1},
    40605: {"lv_up": 6, "skill_unlock": 40604, "barrier_type": 2},
    40606: {"lv_up": 7, "skill_unlock": 40608, "barrier_type": 1},
    40607: {"lv_up": 8, "skill_unlock": 40611, "barrier_type": 1},
    40608: {"lv_up": 9, "skill_unlock": 40605, "barrier_type": 1},
    40609: {"lv_up": 10, "skill_unlock": 40609, "barrier_type": 1},
    40610: {"lv_up": 11, "skill_unlock": 40606, "barrier_type": 1},
    40611: {"lv_up": 12, "skill_unlock": 40612, "barrier_type": 1},
    40612: {"lv_up": 13, "skill_unlock": 40615, "barrier_type": 1},
    40650: {"lv_up": 0, "skill_unlock": 0, "barrier_type": 3},
}

# ---------------- 2. 卡达斯假日赛 (夏活坦克竞速, Activity 4343601) 元数据 ----------------
# 关卡对应的战车配件解锁（1=武器, 2=装备, 3=车体），用于战车改装及常驻任务进度推进（非外部背包资产）
SUMMER_RACE_STAGES = {
    436100: {"module_id": 0, "module_type": 0},
    436101: {"module_id": 103, "module_type": 1},
    436102: {"module_id": 204, "module_type": 2},
    436103: {"module_id": 101, "module_type": 1},
    436104: {"module_id": 102, "module_type": 1},
    436105: {"module_id": 107, "module_type": 1},
    436106: {"module_id": 201, "module_type": 2},
    436201: {"module_id": 108, "module_type": 1},
    436202: {"module_id": 203, "module_type": 2},
    436203: {"module_id": 104, "module_type": 1},
    436301: {"module_id": 202, "module_type": 2},
    436302: {"module_id": 205, "module_type": 2},
    436303: {"module_id": 105, "module_type": 1},
}

# ---------------- 3. 列车大劫案 (灰烬牛仔射击, Activity 3639701) 元数据 ----------------
ASH_REAL_TO_STAGE = {
    5280301: 404101, 5280302: 404102, 5280304: 404103, 5280306: 404104,
    5280311: 404201, 5280312: 404202, 5280313: 404203, 5280316: 404204,
    5280322: 404301, 5280324: 404302, 5280325: 404303, 5280326: 404304,
    5280327: 404401,
}
ASH_STAGE_TO_REAL = {v: k for k, v in ASH_REAL_TO_STAGE.items()}

# 灰烬牛仔同调率积分奖励 (363970101 ~ 363970110)
ASH_POINT_REWARDS = {
    363970101: {"need": 10, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970102: {"need": 20, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970103: {"need": 30, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970104: {"need": 40, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970105: {"need": 50, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970106: {"need": 60, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970107: {"need": 70, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970108: {"need": 80, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970109: {"need": 90, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
    363970110: {"need": 100, "rewards": [(1, 50), (40103, 10), (2, 20000)]},
}

# ---------------- 4. 战术研习记录 (奥西里斯研习, Activity 323801) 元数据 ----------------
OSIRIS_CHAPTER_STAGES = {
    1: [5260131, 5260132, 5260133],
    2: [5260121, 5260122, 5260123],
    3: [5260111, 5260112, 5260113],
    4: [5260141, 5260142, 5260143],
    5: [5260151, 5260152, 5260153],
    6: [5260161, 5260162, 5260163],
}
OSIRIS_STAGE_TO_CHAPTER = {}
for _c, _stages in OSIRIS_CHAPTER_STAGES.items():
    for _s in _stages:
        OSIRIS_STAGE_TO_CHAPTER[_s] = _c

# 奥西里斯同调率积分奖励 (32380101 ~ 32380118, 每档 20 钻石)
OSIRIS_POINT_REWARDS = {
    rid: {"need": rid - 32380100, "rewards": [(1, 20)]}
    for rid in range(32380101, 32380119)
}

# ---------------- 5. 溯梦之隙 (斯克尔德心象, Activity 321201) 元数据 ----------------
# 36 关全量关卡配置 (reward: drop_id, type: 1战斗/2拼图, param: 实际战斗关卡ID或拼图关卡ID)
SKULD_STAGES = {
    1001: {"reward": 32004101, "type": 1, "param": 5240101},
    1002: {"reward": 32004102, "type": 1, "param": 5240102},
    1003: {"reward": 32004103, "type": 2, "param": 103},
    1004: {"reward": 32004104, "type": 1, "param": 5240104},
    1005: {"reward": 32004105, "type": 1, "param": 5240105},
    1006: {"reward": 32004106, "type": 1, "param": 5240106},
    1007: {"reward": 32004107, "type": 2, "param": 107},
    1008: {"reward": 32004108, "type": 1, "param": 5240108},
    1009: {"reward": 32004109, "type": 2, "param": 109},
    1010: {"reward": 32004110, "type": 2, "param": 110},
    1011: {"reward": 32004111, "type": 1, "param": 5240111},
    1012: {"reward": 32004112, "type": 2, "param": 112},
    1013: {"reward": 32004113, "type": 2, "param": 113},
    1014: {"reward": 32004114, "type": 1, "param": 5240114},
    1015: {"reward": 32004115, "type": 2, "param": 115},
    1016: {"reward": 32004116, "type": 2, "param": 116},
    1017: {"reward": 32004117, "type": 1, "param": 5240117},
    1018: {"reward": 32004118, "type": 2, "param": 118},
    1019: {"reward": 32004119, "type": 1, "param": 5240119},
    1020: {"reward": 32004120, "type": 2, "param": 120},
    1021: {"reward": 32004121, "type": 1, "param": 5240121},
    1022: {"reward": 32004122, "type": 2, "param": 122},
    1023: {"reward": 32004123, "type": 1, "param": 5240123},
    1024: {"reward": 32004124, "type": 2, "param": 124},
    1025: {"reward": 32004125, "type": 1, "param": 5240125},
    1026: {"reward": 32004126, "type": 1, "param": 5240126},
    1027: {"reward": 32004127, "type": 2, "param": 127},
    1028: {"reward": 32004128, "type": 1, "param": 5240128},
    1029: {"reward": 32004129, "type": 1, "param": 5240129},
    1030: {"reward": 32004130, "type": 1, "param": 5240130},
    2001: {"reward": 32004131, "type": 2, "param": 201},
    2002: {"reward": 32004132, "type": 2, "param": 202},
    2003: {"reward": 32004133, "type": 2, "param": 203},
    2004: {"reward": 32004134, "type": 2, "param": 204},
    2005: {"reward": 32004135, "type": 2, "param": 205},
    2006: {"reward": 32004136, "type": 2, "param": 206},
}

SKULD_BATTLE_TO_STAGE = {
    v["param"]: k for k, v in SKULD_STAGES.items() if v["type"] == 1
}

# 斯克尔德心象信赖度大奖 (32120101 ~ 32120110, 每档 30 钻石 + 专属音乐/名片)
SKULD_POINT_REWARDS = {
    32120101: {"need": 10, "rewards": [(1, 30), (2200015, 1), (22003, 2)]},
    32120102: {"need": 20, "rewards": [(1, 30), (3051, 1), (22003, 2)]},
    32120103: {"need": 30, "rewards": [(1, 30), (91006, 1), (22003, 2)]},
    32120104: {"need": 40, "rewards": [(1, 30), (3052, 1), (22003, 2)]},
    32120105: {"need": 50, "rewards": [(1, 30), (91007, 1), (22003, 2)]},
    32120106: {"need": 60, "rewards": [(1, 30), (3053, 1), (22003, 2)]},
    32120107: {"need": 70, "rewards": [(1, 30), (91008, 1), (22003, 2)]},
    32120108: {"need": 80, "rewards": [(1, 30), (3054, 1), (22003, 2)]},
    32120109: {"need": 90, "rewards": [(1, 30), (91009, 1), (22003, 2)]},
    32120110: {"need": 100, "rewards": [(1, 30), (4007, 1), (3055, 1)]},
}

# ---------------- 6. 谧光叙录 (霍德尔专属活动, Activity 3941101) 元数据 ----------------
HODUR_STAGE_TO_CHAPTER = {
    5310220: 1, 5310219: 1,
    5310213: 2, 5310211: 2, 5310218: 2,
    5310201: 3, 5310202: 3, 5310221: 3,
    5310203: 4, 5310204: 4, 5310205: 4, 5310206: 4,
}

# ---------------- 7. 潜质觉醒 (现时无返之途·薇儿丹蒂 SP, Activity 242841/242851/242871) 元数据 ----------------
SP_HERO_ACTIVITY_ID = 242841
SP_HERO_BOSS_ACTIVITY_ID = 242851
SP_HERO_PUZZLE_ACTIVITY_ID = 242871
SP_HERO_BARBECUE_ACTIVITY_ID = 242861

SP_HERO_STORY_STAGES = [5170201, 5170202, 5170203, 5170204, 5170205, 5170206]
SP_HERO_TRAIN_STAGES = {
    5170211: 2, 5170212: 2, 5170213: 2,  # 基础训练组
    5170214: 3, 5170215: 3, 5170216: 3,  # 进阶训练组
    5170217: 4, 5170218: 4, 5170219: 4,  # 特殊训练组
}
SP_HERO_BOSS_STAGES = [5170221, 5170222]

# ---------------- 7. 极限机装 (乌尔碰碰车, Activity 4243101) 元数据 ----------------
VEHICLE_BALL_ACTIVITY_ID = 4243101
VEHICLE_BALL_LIMITED_TASK_ACTIVITY_ID = 4200401
VEHICLE_BALL_STAGES = [50101, 50102, 50103, 50104]
VEHICLE_BALL_VEHICLES = [50111, 50112, 50113]

# ---------------- 9. 浮光绎曲与鸣律探微 (常驻音律演奏会与夏日小游戏, Activity 3814801 / 283041) 元数据 ----------------
RESIDENT_MUSIC_ACTIVITY_ID = 3814801
RESIDENT_MUSIC_TASK_ACTIVITY_ID = 3800402

_MUSIC_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "activity_music_catalog.json")
_MUSIC_CATALOG = {}
if os.path.exists(_MUSIC_CATALOG_PATH):
    try:
        with open(_MUSIC_CATALOG_PATH, "r", encoding="utf-8") as _mf:
            _MUSIC_CATALOG = json.load(_mf)
    except Exception as _me:
        logger.warning(f"Failed to load activity_music_catalog.json: {_me}")

# ---------------- 10. 食与异世界 (夏日餐厅经营, Activity 3539101) 元数据 ----------------
SUMMER_PUB_ACTIVITY_ID = 3539101

SUMMER_PUB_BATTLE_TO_LEVEL = {
    5270201: 4030101,
    5270202: 4030102,
    5270203: 4030103,
    5270205: 4030201,
    5270206: 4030301,
    5270207: 4030305,
    5270208: 4030308,
    5270209: 4030308,
}

SUMMER_PUB_COOK_TO_LEVEL = {
    10100: 4030001,
    10200: 4030002,
    10300: 4030003,
    10400: 4030004,
    10500: 4030005,
    10600: 4030006,
    10700: 4030007,
    10800: 4030307,
}



class MiniGameService:
    """常驻小游戏领域服务单例"""
    _instance = None

    def __init__(self, db=None):
        self.db = db

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = MiniGameService(db=db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    def _resolve_db_uid(self, arg1, arg2=None):
        if isinstance(arg1, (int, str)) and not hasattr(arg1, "query"):
            uid = int(arg1)
            db = arg2 or self.db
        else:
            db = arg1 or self.db
            uid = int(arg2) if arg2 is not None else 2174928301
        return db, uid

    # ==================== 1. 远村异闻 · 海拉弹珠 (Activity 3840801) ====================

    def get_or_init_pinball(self, db, uid, activity_id=3840801):
        """获取或初始化海拉弹珠数据"""
        db = db or self.db
        rows = db.query(
            "SELECT * FROM pinball_progress WHERE uid=? AND activity_id=?",
            (uid, activity_id)
        )
        if rows:
            r = rows[0]
            try:
                cleared = json.loads(r.get("cleared_stage_list") or "[]")
            except Exception:
                cleared = []
            try:
                unlock_skills = json.loads(r.get("unlock_skill_list") or "[]")
            except Exception:
                unlock_skills = []
            try:
                equip_skills = json.loads(r.get("equip_skill_list") or "[]")
            except Exception:
                equip_skills = []
            return {
                "uid": uid,
                "activity_id": activity_id,
                "stage_count": int(r.get("stage_count") or 1),
                "cleared_stage_list": cleared,
                "unlock_skill_list": unlock_skills,
                "equip_skill_list": equip_skills,
                "score": int(r.get("score") or 0),
            }
        now = int(time.time())
        db.execute(
            """INSERT INTO pinball_progress 
               (uid, activity_id, stage_count, cleared_stage_list, unlock_skill_list, equip_skill_list, score, update_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, activity_id, 1, "[]", "[]", "[]", 0, now)
        )
        return {
            "uid": uid,
            "activity_id": activity_id,
            "stage_count": 1,
            "cleared_stage_list": [],
            "unlock_skill_list": [],
            "equip_skill_list": [],
            "score": 0,
        }

    def build_89401_payload(self, db, uid, activity_id=3840801):
        """生成 sc_89401 下行 payload"""
        db = db or self.db
        data = self.get_or_init_pinball(db, uid, activity_id)
        obj = {
            "hai_la_level": data["stage_count"],
            "stage_list": data["cleared_stage_list"],
            "skills": data["unlock_skill_list"],
            "select_active_skills": data["equip_skill_list"],
            "challenge_score": data["score"],
        }
        try:
            from codec import encode
            return encode("sc_89401", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_89401 失败: {e}")
            return b""

    def settle_pinball_level(self, ctx, uid, stage_id, score, harm):
        """结算海拉弹珠关卡 (cs_89402)"""
        db = getattr(ctx, "db", self.db)
        data = self.get_or_init_pinball(db, uid, 3840801)

        is_first_clear = False
        stage_cfg = PINBALL_STAGES.get(stage_id, {})
        barrier_type = stage_cfg.get("barrier_type", 1)

        # 1. 普通关卡通关与升级逻辑
        if barrier_type != 3:
            if stage_id not in data["cleared_stage_list"]:
                data["cleared_stage_list"].append(stage_id)
                is_first_clear = True

                unlock_sk = stage_cfg.get("skill_unlock", 0)
                if unlock_sk and unlock_sk not in data["unlock_skill_list"]:
                    data["unlock_skill_list"].append(unlock_sk)

                lv_up = stage_cfg.get("lv_up", 0)
                if lv_up > data["stage_count"]:
                    data["stage_count"] = lv_up

        # 2. 最高分记录（挑战关或常规打靶）
        if score > data["score"]:
            data["score"] = score

        # 3. 持久化落库
        now = int(time.time())
        db.execute(
            """INSERT INTO pinball_progress 
               (uid, activity_id, stage_count, cleared_stage_list, unlock_skill_list, equip_skill_list, score, update_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid, activity_id) DO UPDATE SET
                 stage_count = excluded.stage_count,
                 cleared_stage_list = excluded.cleared_stage_list,
                 unlock_skill_list = excluded.unlock_skill_list,
                 equip_skill_list = excluded.equip_skill_list,
                 score = excluded.score,
                 update_ts = excluded.update_ts""",
            (
                uid,
                data["activity_id"],
                data["stage_count"],
                json.dumps(data["cleared_stage_list"]),
                json.dumps(data["unlock_skill_list"]),
                json.dumps(data["equip_skill_list"]),
                data["score"],
                now,
            )
        )

        # 4. 触发全局 EventBus 广播与任务系统联动
        try:
            bus.emit(Events.STAGE_PASS, ctx, uid, times=1, stage_id=stage_id, stage_type="minigame_pinball")
            if is_first_clear:
                bus.emit(Events.STAGE_FIRST_CLEAR, ctx, uid, stage_id=stage_id, stage_type="minigame_pinball", first_rewards=[])
        except Exception as e:
            logger.warning(f"[MiniGameService] 弹珠事件广播异常: {e}")

        return data

    def equip_pinball_skills(self, ctx, uid, activity_id, skill_list):
        """装备海拉弹珠技能 (cs_89404)"""
        db = getattr(ctx, "db", self.db)
        data = self.get_or_init_pinball(db, uid, activity_id)

        clean_skills = [int(s) for s in skill_list if int(s) > 0]
        data["equip_skill_list"] = clean_skills
        now = int(time.time())

        db.execute(
            """UPDATE pinball_progress 
               SET equip_skill_list = ?, update_ts = ?
               WHERE uid = ? AND activity_id = ?""",
            (json.dumps(clean_skills), now, uid, activity_id)
        )
        return data

    # ==================== 2. 卡达斯假日赛 · 夏活坦克竞速 (Activity 4343601) ====================

    def get_or_init_kadas_race(self, db, uid, activity_id=4343601):
        """获取或初始化卡达斯假日赛数据"""
        db = db or self.db
        rows = db.query(
            "SELECT * FROM kadas_race_progress WHERE uid=? AND activity_id=?",
            (uid, activity_id)
        )
        if rows:
            r = rows[0]
            try:
                missions = json.loads(r.get("mission_list") or "[]")
            except Exception:
                missions = []
            try:
                tinfo = json.loads(r.get("tank_info") or "[]")
            except Exception:
                tinfo = []
            tank_id = int(r.get("node") or 312)
            if not tinfo:
                weapon = int(r.get("count") or 106)
                tinfo = [{"tank_id": tank_id, "weapon_list": [weapon] if weapon else []}]
            return {
                "uid": uid,
                "activity_id": activity_id,
                "tank_id": tank_id,
                "mission_list": missions,
                "tank_info": tinfo,
                "round": int(r.get("status") or 0),
                "max_score": int(r.get("ts") if r.get("ts") is not None else -1),
            }
        now = int(time.time())
        init_tinfo = [{"tank_id": 312, "weapon_list": [106]}]
        db.execute(
            """INSERT INTO kadas_race_progress 
               (uid, activity_id, node, node2, count, status, ts, update_ts, mission_list, tank_info)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, activity_id, 312, 312, 106, 0, -1, now, "[]", json.dumps(init_tinfo))
        )
        return {
            "uid": uid,
            "activity_id": activity_id,
            "tank_id": 312,
            "mission_list": [],
            "tank_info": init_tinfo,
            "round": 0,
            "max_score": -1,
        }

    def build_84331_payload(self, db, uid, activity_id=4343601):
        """生成 sc_84331 下行 payload"""
        db = db or self.db
        data = self.get_or_init_kadas_race(db, uid, activity_id)
        obj = {
            "activity_id": data["activity_id"],
            "mission_list": data["mission_list"],
            "tank_id": data["tank_id"],
            "tank_info": data["tank_info"],
            "challenge_stage_info": {
                "round": data["round"],
                "max_score": data["max_score"],
            },
        }
        try:
            from codec import encode
            return encode("sc_84331", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_84331 失败: {e}")
            return b""

    def settle_race_stage(self, ctx, uid, stage_id, result, round_num=0, score=0, kill_num=0):
        """结算卡达斯坦克竞速关卡 (cs_84332)"""
        db = getattr(ctx, "db", self.db)
        data = self.get_or_init_kadas_race(db, uid, 4343601)

        is_win = (result == 0 or result is True)
        is_first_clear = False
        rewards_granted = []

        if is_win and stage_id > 0:
            if stage_id not in data["mission_list"]:
                data["mission_list"].append(stage_id)
                is_first_clear = True

        # 计算当前已解锁的战车配件总数 (用于驱动常驻任务 50202101~50202112, cond=423002)
        module_count = sum(1 for sid in data["mission_list"] if sid in SUMMER_RACE_STAGES and SUMMER_RACE_STAGES[sid].get("module_id", 0) > 0)

        # 挑战模式积分与轮数记录
        if round_num > data["round"]:
            data["round"] = round_num
        if score > data["max_score"]:
            data["max_score"] = score

        now = int(time.time())
        db.execute(
            """UPDATE kadas_race_progress 
               SET status = ?, ts = ?, mission_list = ?, update_ts = ?
               WHERE uid = ? AND activity_id = ?""",
            (data["round"], data["max_score"], json.dumps(data["mission_list"]), now, uid, 4343601)
        )

        # 抛出全局广播 (通过 module_count 驱动常驻配件收集任务原子刷新)
        try:
            m_type = SUMMER_RACE_STAGES.get(stage_id, {}).get("module_type", 1)
            bus.emit(Events.STAGE_PASS, ctx, uid, times=1, stage_id=stage_id, stage_type="summer_race", module_count=module_count, module_type=m_type)
            if is_first_clear:
                bus.emit(Events.STAGE_FIRST_CLEAR, ctx, uid, stage_id=stage_id, stage_type="summer_race", first_rewards=[])
        except Exception as e:
            logger.warning(f"[MiniGameService] 坦克竞速事件广播异常: {e}")

        return data

    def modify_tank_build(self, ctx, uid, tank_id, weapon_list):
        """改装坦克武器 (cs_84334)"""
        db = getattr(ctx, "db", self.db)
        data = self.get_or_init_kadas_race(db, uid, 4343601)

        clean_weapons = [int(w) for w in weapon_list if int(w) > 0]
        data["tank_id"] = int(tank_id)

        # 更新对应 tank_id 的条目
        updated = False
        for t in data["tank_info"]:
            if t["tank_id"] == data["tank_id"]:
                t["weapon_list"] = clean_weapons
                updated = True
                break
        if not updated:
            data["tank_info"].append({"tank_id": data["tank_id"], "weapon_list": clean_weapons})

        now = int(time.time())
        primary_weapon = clean_weapons[0] if clean_weapons else 0
        db.execute(
            """UPDATE kadas_race_progress 
               SET node = ?, count = ?, tank_info = ?, update_ts = ?
               WHERE uid = ? AND activity_id = ?""",
            (data["tank_id"], primary_weapon, json.dumps(data["tank_info"]), now, uid, 4343601)
        )
        return data

    # ==================== 3. 列车大劫案 · 灰烬牛仔 (Activity 3639701) ====================

    def get_ash_shoot_stages(self, db, uid):
        """获取灰烬牛仔全量通关与最高分数据"""
        db = db or self.db
        rows = db.query(
            "SELECT stage_id, point FROM ash_shoot_stage WHERE uid=? ORDER BY stage_id",
            (uid,)
        )
        if not rows:
            return []
        return [{"stage_id": int(r["stage_id"]), "point": int(r["point"] or 0)} for r in rows]

    def build_68181_payload(self, db, uid):
        """生成 sc_68181 (灰烬牛仔关卡最高分汇总) 下行 payload"""
        from codec import encode
        stages = self.get_ash_shoot_stages(db, uid)
        try:
            return encode("sc_68181", {"pass_stages": stages})
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_68181 失败: {e}")
            return None

    def build_60105_payload(self, db, uid):
        """生成 sc_60105 (常驻积分活动分值汇总) 下行 payload"""
        from codec import encode
        db = db or self.db
        rows = db.query(
            "SELECT activity_id, point FROM activity_point WHERE uid=?",
            (uid,)
        )
        score_info_list = [{"key": int(r["activity_id"]), "score": int(r["point"] or 0)} for r in (rows or [])]
        try:
            return encode("sc_60105", {"score_info_list": score_info_list})
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_60105 失败: {e}")
            return None

    def build_60107_payload(self, db, uid):
        """生成 sc_60107 (常驻积分奖励领奖记录) 下行 payload"""
        from codec import encode
        db = db or self.db
        rows = db.query(
            "SELECT reward_id FROM activity_point_reward WHERE uid=?",
            (uid,)
        )
        reward_info_list = [int(r["reward_id"]) for r in (rows or [])]
        try:
            return encode("sc_60107", {"reward_info_list": reward_info_list})
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_60107 失败: {e}")
            return None

    def settle_ash_stage(self, ctx, uid, real_stage_id, point=8000, win=True):
        """灰烬牛仔战斗通关结算（由 h_54032 触发）"""
        from codec import encode
        db = ctx.db or self.db
        ash_stage_id = ASH_REAL_TO_STAGE.get(int(real_stage_id), int(real_stage_id))
        now = int(time.time())

        # 1. 查验历史记录并持久化
        rows = db.query(
            "SELECT point FROM ash_shoot_stage WHERE uid=? AND stage_id=?",
            (uid, ash_stage_id)
        )
        is_first_pass = not bool(rows)
        old_point = int(rows[0]["point"] or 0) if rows else 0
        new_point = max(old_point, int(point))

        if is_first_pass:
            db.execute(
                "INSERT INTO ash_shoot_stage (uid, stage_id, point, update_ts) VALUES (?, ?, ?, ?)",
                (uid, ash_stage_id, new_point, now)
            )
        else:
            db.execute(
                "UPDATE ash_shoot_stage SET point=?, update_ts=? WHERE uid=? AND stage_id=?",
                (new_point, now, uid, ash_stage_id)
            )

        # 2. 更新常驻积分 (同调率)
        act_rows = db.query(
            "SELECT point FROM activity_point WHERE uid=? AND activity_id=?",
            (uid, 3639701)
        )
        old_act_point = int(act_rows[0]["point"] or 0) if act_rows else 0
        new_act_point = min(100, old_act_point + 10)
        if not act_rows:
            db.execute(
                "INSERT INTO activity_point (uid, activity_id, point, update_ts) VALUES (?, ?, ?, ?)",
                (uid, 3639701, new_act_point, now)
            )
        else:
            db.execute(
                "UPDATE activity_point SET point=?, update_ts=? WHERE uid=? AND activity_id=?",
                (new_act_point, now, uid, 3639701)
            )

        # 3. 产出灰烬代币 (CurrencyIdMapCfg.CURRENCY_ASH_COIN_4_4.item_id = 79)
        coin_grant = [(79, 10)]
        InventoryService.grant_items(ctx, uid, coin_grant, source="ash_battle")

        # 4. 全局广播触发
        try:
            bus.emit(
                Events.STAGE_PASS,
                ctx,
                uid,
                times=1,
                stage_id=int(real_stage_id),
                ash_stage_id=ash_stage_id,
                stage_type="ash_shoot"
            )
            if is_first_pass:
                bus.emit(
                    Events.STAGE_FIRST_CLEAR,
                    ctx,
                    uid,
                    stage_id=int(real_stage_id),
                    ash_stage_id=ash_stage_id,
                    stage_type="ash_shoot"
                )
        except Exception as e:
            logger.warning(f"[MiniGameService] 灰烬牛仔广播发射异常: {e}")

        # 5. 组装 sc_68183 差量帧
        p68183 = None
        try:
            p68183 = encode("sc_68183", {
                "pass_stage": {
                    "stage_id": ash_stage_id,
                    "point": new_point
                }
            })
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_68183 失败: {e}")

        return {
            "real_stage_id": int(real_stage_id),
            "ash_stage_id": ash_stage_id,
            "point": new_point,
            "is_first_pass": is_first_pass,
            "p68183": p68183
        }

    def claim_point_rewards(self, ctx, uid, reward_id_list):
        """通用常驻积分奖励领取（由 ActivityPointRewardOp 触发）"""
        db = ctx.db or self.db
        now = int(time.time())
        to_grant_tuples = []
        reward_items_pb = []
        claimed_ids = []

        # 获取玩家已领取列表
        rows = db.query(
            "SELECT reward_id FROM activity_point_reward WHERE uid=?",
            (uid,)
        )
        already_claimed = set(int(r["reward_id"]) for r in (rows or []))

        for rid in reward_id_list:
            rid = int(rid)
            if rid in already_claimed:
                continue

            cfg = ASH_POINT_REWARDS.get(rid) or OSIRIS_POINT_REWARDS.get(rid) or SKULD_POINT_REWARDS.get(rid)
            if cfg:
                for item_id, count in cfg["rewards"]:
                    to_grant_tuples.append((item_id, count))
                    reward_items_pb.append({"id": item_id, "num": count})
            else:
                to_grant_tuples.append((1, 50))
                to_grant_tuples.append((2, 10000))
                reward_items_pb.append({"id": 1, "num": 50})
                reward_items_pb.append({"id": 2, "num": 10000})

            # 判断活动 ID：奥西里斯为 323801，斯克尔德心象为 321201，灰烬牛仔为 3639701
            if 32380101 <= rid <= 32380118:
                act_id = 323801
            elif 32120101 <= rid <= 32120110:
                act_id = 321201
            elif 363970101 <= rid <= 363970110:
                act_id = 3639701
            else:
                act_id = rid // 100

            db.execute(
                "INSERT INTO activity_point_reward (uid, activity_id, reward_id, update_ts) VALUES (?, ?, ?, ?)",
                (uid, act_id, rid, now)
            )
            if act_id == 321201:
                try:
                    sk_rows = db.query("SELECT reward_list FROM skuld_progress WHERE uid=? AND activity_id=321201", (uid,))
                    if sk_rows:
                        curr_r = json.loads(sk_rows[0].get("reward_list") or "[]")
                        if rid not in curr_r:
                            curr_r.append(rid)
                            db.execute(
                                "UPDATE skuld_progress SET reward_list=?, update_ts=? WHERE uid=? AND activity_id=321201",
                                (json.dumps(curr_r), now, uid)
                            )
                except Exception:
                    pass

            already_claimed.add(rid)
            claimed_ids.append(rid)

        if to_grant_tuples:
            InventoryService.grant_items(ctx, uid, to_grant_tuples, source="activity_point_reward")

        return {
            "claimed_ids": claimed_ids,
            "reward_list": reward_items_pb
        }

    # ==================== 4. 战术研习记录 · 奥西里斯研习 (Activity 323801) ====================

    def get_osiris_stages(self, db, uid):
        """获取奥西里斯全量关卡通关与得分记录"""
        db = db or self.db
        rows = db.query(
            "SELECT stage_id, score, settle FROM osiris_stage_progress WHERE uid=? ORDER BY stage_id",
            (uid,)
        )
        if not rows:
            return []
        return [{
            "stage_id": int(r["stage_id"]),
            "pass_time": int(r["score"] or 0),
            "point": int(r["settle"] or 0)
        } for r in rows]

    def get_osiris_chapters(self, db, uid):
        """获取奥西里斯已通关章节列表"""
        db = db or self.db
        rows = db.query(
            "SELECT chapter_no FROM osiris_chapter_progress WHERE uid=? ORDER BY chapter_no",
            (uid,)
        )
        return [int(r["chapter_no"]) for r in (rows or [])]

    def build_68171_payload(self, db, uid):
        """生成 sc_68171 (奥西里斯登录同步帧) 下行 payload"""
        from codec import encode
        stages = self.get_osiris_stages(db, uid)
        chapters = self.get_osiris_chapters(db, uid)
        try:
            return encode("sc_68171", {
                "pass_stages": stages,
                "pass_chapters": chapters
            })
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_68171 失败: {e}")
            return None

    def settle_osiris_stage(self, ctx, uid, stage_id, pass_time=60, point=10000, win=True):
        """奥西里斯战术研习战斗通关结算（由 h_54032 触发）"""
        from codec import encode
        from middleware import DownFrame
        db = ctx.db or self.db
        now = int(time.time())
        frames = []

        # 1. 关卡记录落库
        rows = db.query(
            "SELECT score, settle FROM osiris_stage_progress WHERE uid=? AND stage_id=?",
            (uid, stage_id)
        )
        is_first_pass = not bool(rows)
        old_point = int(rows[0]["settle"] or 0) if rows else 0
        new_point = max(old_point, int(point))
        new_time = int(pass_time)

        if is_first_pass:
            db.execute(
                "INSERT INTO osiris_stage_progress (uid, stage_id, score, settle, update_ts) VALUES (?, ?, ?, ?, ?)",
                (uid, stage_id, new_time, new_point, now)
            )
        else:
            db.execute(
                "UPDATE osiris_stage_progress SET score=?, settle=?, update_ts=? WHERE uid=? AND stage_id=?",
                (new_time, new_point, now, uid, stage_id)
            )

        # 2. 组装关卡结算推送帧 sc_68177
        try:
            p68177 = encode("sc_68177", {
                "pass_stage": {
                    "stage_id": int(stage_id),
                    "pass_time": new_time,
                    "point": new_point
                }
            })
            if p68177:
                frames.append(DownFrame(68177, p68177))
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_68177 失败: {e}")

        # 3. 章节完成度核查
        chapter_no = OSIRIS_STAGE_TO_CHAPTER.get(int(stage_id))
        if chapter_no:
            chapter_stages = OSIRIS_CHAPTER_STAGES.get(chapter_no, [])
            cleared_rows = db.query(
                f"SELECT stage_id FROM osiris_stage_progress WHERE uid=? AND stage_id IN ({','.join(str(s) for s in chapter_stages)})",
                (uid,)
            )
            cleared_ids = set(int(r["stage_id"]) for r in cleared_rows)
            if all(s in cleared_ids for s in chapter_stages):
                existing_ch = db.query(
                    "SELECT chapter_no FROM osiris_chapter_progress WHERE uid=? AND chapter_no=?",
                    (uid, chapter_no)
                )
                if not existing_ch:
                    db.execute(
                        "INSERT INTO osiris_chapter_progress (uid, chapter_no, value, update_ts) VALUES (?, ?, ?, ?)",
                        (uid, chapter_no, chapter_no, now)
                    )
                    try:
                        p68175 = encode("sc_68175", {"pass_chapters": [chapter_no]})
                        if p68175:
                            frames.append(DownFrame(68175, p68175))
                    except Exception as e:
                        logger.error(f"[MiniGameService] 编码 sc_68175 失败: {e}")

        # 4. 更新常驻积分 (同调率，上限 18)
        tot_cleared = db.query("SELECT count(*) as cnt FROM osiris_stage_progress WHERE uid=?", (uid,))
        cleared_cnt = min(18, int(tot_cleared[0]["cnt"]) if tot_cleared else 0)
        db.execute(
            """INSERT INTO activity_point (uid, activity_id, point, update_ts)
               VALUES (?, 323801, ?, ?)
               ON CONFLICT(uid, activity_id) DO UPDATE SET point = excluded.point, update_ts = excluded.update_ts""",
            (uid, cleared_cnt, now)
        )

        # 5. 全局广播触发 (STAGE_PASS 驱动任务系统)
        try:
            bus.emit(Events.STAGE_PASS, ctx, uid, times=1, stage_id=int(stage_id), stage_type="osiris_study")
            if is_first_pass:
                bus.emit(Events.STAGE_FIRST_CLEAR, ctx, uid, stage_id=int(stage_id), stage_type="osiris_study", first_rewards=[])
        except Exception as e:
            logger.warning(f"[MiniGameService] 奥西里斯广播异常: {e}")

        return {
            "stage_id": int(stage_id),
            "point": new_point,
            "is_first_pass": is_first_pass,
            "frames": frames
        }

    # ==================== 5. 溯梦之隙 · 斯克尔德心象 (Activity 321201) ====================

    def get_or_init_skuld(self, db, uid, activity_id=321201):
        """获取或初始化斯克尔德心象进度"""
        db = db or self.db
        rows = db.query(
            "SELECT * FROM skuld_progress WHERE uid=? AND activity_id=?",
            (uid, activity_id)
        )
        if rows:
            r = rows[0]
            try:
                opts = json.loads(r.get("client_opt_list") or "[]")
            except Exception:
                opts = []
            try:
                rewards = json.loads(r.get("reward_list") or "[]")
            except Exception:
                rewards = []
            try:
                missions = json.loads(r.get("mission_json") or "[]")
            except Exception:
                missions = []
            return {
                "uid": uid,
                "activity_id": activity_id,
                "client_opt_list": opts,
                "point": int(r.get("point") or 0),
                "reward_list": rewards,
                "mission_list": missions,
            }
        now = int(time.time())
        db.execute(
            """INSERT INTO skuld_progress 
               (uid, activity_id, client_opt_list, point, reward_list, mission_json, update_ts)
               VALUES (?, ?, '[]', 0, '[]', '[]', ?)""",
            (uid, activity_id, now)
        )
        return {
            "uid": uid,
            "activity_id": activity_id,
            "client_opt_list": [],
            "point": 0,
            "reward_list": [],
            "mission_list": [],
        }

    def build_24051_payload(self, db, uid, activity_id=321201):
        """构造 sc_24051 斯克尔德心象全量同步下行帧"""
        db = db or self.db
        data = self.get_or_init_skuld(db, uid, activity_id)

        # 实时同步玩家持有的心象代币 Currency 76
        cur_row = db.query("SELECT num FROM currency WHERE uid=? AND id=76", (uid,))
        coin_76 = int(cur_row[0]["num"]) if cur_row else data["point"]

        # 实时同步已领积分奖励
        rows = db.query("SELECT reward_id FROM activity_point_reward WHERE uid=? AND activity_id=?", (uid, activity_id))
        claimed_ids = [int(r["reward_id"]) for r in (rows or [])]
        if not claimed_ids:
            claimed_ids = data["reward_list"]

        obj = {
            "client_opt": data["client_opt_list"],
            "mission_list": data["mission_list"],
            "activity_id": activity_id,
            "point": coin_76,
            "reward_list": claimed_ids,
        }
        try:
            from codec import encode
            return encode("sc_24051", obj)
        except Exception as e:
            logger.error(f"build_24051_payload error: {e}")
            return None

    def save_skuld_mark(self, ctx, uid, key, activity_id=321201):
        """记录剧情选项与过场标记 (cs_24052)"""
        db = ctx.db or self.db
        data = self.get_or_init_skuld(db, uid, activity_id)
        opts = data["client_opt_list"]
        key = int(key)
        if key not in opts:
            opts.append(key)
            now = int(time.time())
            db.execute(
                "UPDATE skuld_progress SET client_opt_list=?, update_ts=? WHERE uid=? AND activity_id=?",
                (json.dumps(opts), now, uid, activity_id)
            )
        return {"result": 0}

    def settle_skuld_puzzle(self, ctx, uid, level_id, activity_id=321201):
        """结算拼图/行走解谜关卡 (cs_24054)"""
        db = ctx.db or self.db
        now = int(time.time())
        level_id = int(level_id)
        cfg = SKULD_STAGES.get(level_id)
        if not cfg or cfg["type"] != 2:
            return {"result": 0, "reward_list": []}

        data = self.get_or_init_skuld(db, uid, activity_id)
        missions = data["mission_list"]
        m_dict = {int(m["id"]): int(m.get("times", 0)) for m in missions}
        is_first = (m_dict.get(level_id, 0) == 0)

        # 累加通关次数
        m_dict[level_id] = m_dict.get(level_id, 0) + 1
        new_missions = [{"id": k, "times": v} for k, v in sorted(m_dict.items())]

        reward_list_pb = []
        if is_first:
            drop_id = cfg["reward"]
            import os
            drop_cfg_path = os.path.join(os.path.dirname(__file__), "drop_cfg.json")
            try:
                with open(drop_cfg_path, "r", encoding="utf-8") as f:
                    all_drops = json.load(f)
                d_info = all_drops.get(str(drop_id), {})
                to_grant = []
                for b in d_info.get("base", []):
                    item_id, cnt = int(b[0]), int(b[1])
                    to_grant.append((item_id, cnt))
                    reward_list_pb.append({"id": item_id, "num": cnt})
                if to_grant:
                    InventoryService.grant_items(ctx, uid, to_grant, source=f"skuld_puzzle_{level_id}")
            except Exception as e:
                logger.error(f"settle_skuld_puzzle drop error: {e}")

        db.execute(
            "UPDATE skuld_progress SET mission_json=?, update_ts=? WHERE uid=? AND activity_id=?",
            (json.dumps(new_missions), now, uid, activity_id)
        )
        return {"result": 0, "reward_list": reward_list_pb}

    def settle_skuld_stage(self, ctx, uid, dest_int, win=True, activity_id=321201):
        """结算战斗关卡 (通用战斗 5240101~5240130)"""
        if not win:
            return None
        dest_int = int(dest_int)
        level_id = SKULD_BATTLE_TO_STAGE.get(dest_int)
        if not level_id:
            return None

        db = ctx.db or self.db
        now = int(time.time())
        data = self.get_or_init_skuld(db, uid, activity_id)
        missions = data["mission_list"]
        m_dict = {int(m["id"]): int(m.get("times", 0)) for m in missions}
        is_first = (m_dict.get(level_id, 0) == 0)

        m_dict[level_id] = m_dict.get(level_id, 0) + 1
        new_missions = [{"id": k, "times": v} for k, v in sorted(m_dict.items())]

        first_rewards = []
        cfg = SKULD_STAGES.get(level_id)
        if is_first and cfg:
            drop_id = cfg["reward"]
            import os
            drop_cfg_path = os.path.join(os.path.dirname(__file__), "drop_cfg.json")
            try:
                with open(drop_cfg_path, "r", encoding="utf-8") as f:
                    all_drops = json.load(f)
                d_info = all_drops.get(str(drop_id), {})
                for b in d_info.get("base", []):
                    item_id, cnt = int(b[0]), int(b[1])
                    first_rewards.append((item_id, cnt))
            except Exception as e:
                logger.error(f"settle_skuld_stage drop error: {e}")

        db.execute(
            "UPDATE skuld_progress SET mission_json=?, update_ts=? WHERE uid=? AND activity_id=?",
            (json.dumps(new_missions), now, uid, activity_id)
        )

        bus.emit(Events.STAGE_PASS, ctx, uid, times=1, stage_id=dest_int, stage_type="skuld_stage")
        if is_first:
            bus.emit(Events.STAGE_FIRST_CLEAR, ctx, uid, stage_id=dest_int)

        return {
            "level_id": level_id,
            "is_first": is_first,
            "first_rewards": first_rewards
        }

    # ==================== 6. 谧光叙录 · 霍德尔活动 (Activity 3941101) ====================

    def get_or_init_hodur(self, db, uid, activity_id=3941101):
        """获取或初始化霍德尔活动 3 章节探索数据"""
        db = db or self.db
        rows = db.query(
            "SELECT * FROM hodur_progress WHERE uid=? AND activity_id=? ORDER BY chapter_id ASC",
            (uid, activity_id)
        )
        if not rows:
            now = int(time.time())
            for chap in (1, 2, 3):
                db.execute(
                    """INSERT OR IGNORE INTO hodur_progress 
                       (uid, activity_id, chapter_id, option_id_list, stage_clear_json, stat_json, update_ts)
                       VALUES (?, ?, ?, '[]', '[]', '{"hero_id": 236, "hero_blood": 45096, "hero_max_blood": 45096}', ?)""",
                    (uid, activity_id, chap, now)
                )
            rows = db.query(
                "SELECT * FROM hodur_progress WHERE uid=? AND activity_id=? ORDER BY chapter_id ASC",
                (uid, activity_id)
            )
        return rows

    def build_89421_payload(self, arg1, arg2=None):
        """生成 sc_89421 霍德尔全量状态下行 payload，兼容 (db, uid) 与 (uid, db)"""
        from codec import encode
        if isinstance(arg1, (int, str)) and not hasattr(arg1, "query"):
            uid = int(arg1)
            db = arg2 or self.db
        else:
            db = arg1 or self.db
            uid = int(arg2)
        rows = db.query(
            "SELECT chapter_id, option_id_list, stage_clear_json, stat_json "
            "FROM hodur_progress WHERE uid=? AND activity_id=3941101 ORDER BY chapter_id ASC",
            (uid,)
        )
        if not rows:
            self.get_or_init_hodur(db, uid, activity_id=3941101)
            rows = db.query(
                "SELECT chapter_id, option_id_list, stage_clear_json, stat_json "
                "FROM hodur_progress WHERE uid=? AND activity_id=3941101 ORDER BY chapter_id ASC",
                (uid,)
            )

        normal_stage_info = []
        for r in rows:
            chap_id = int(r["chapter_id"])
            opt_list = json.loads(r["option_id_list"] or "[]")
            boss_clears = json.loads(r["stage_clear_json"] or "[]")
            stat = json.loads(r["stat_json"] or "{}")

            boss_list = []
            for b in boss_clears:
                bid = b.get("boss_id") or b.get("stage_id", 0)
                res = b.get("result", 1) if "result" in b else b.get("clear", 1)
                boss_list.append({"boss_id": int(bid), "result": int(res)})

            hid = stat.get("hero_id") or stat.get("a", 236)
            hp = stat.get("hero_blood") or stat.get("b", 45096)
            max_hp = stat.get("hero_max_blood") or stat.get("c", 45096)

            normal_stage_info.append({
                "normal_stage_id": chap_id,
                "normal_option_info": [int(x) for x in opt_list],
                "normal_hero_stage_info": [{
                    "hero_id": int(hid),
                    "hero_blood": int(hp),
                    "hero_max_blood": int(max_hp)
                }],
                "normal_boss_id_list": boss_list
            })

        payload_dict = {
            "normal_stage_info": normal_stage_info,
            "challenge_stage_info": []
        }
        return encode("sc_89421", payload_dict)

    def reset_hodur_chapter(self, ctx, uid, chapter_id, activity_id=3941101):
        """重置霍德尔指定章节当前轮探索数据"""
        db = getattr(ctx, "db", self.db)
        self.get_or_init_hodur(db, uid, activity_id)
        row = db.query(
            "SELECT stat_json FROM hodur_progress WHERE uid=? AND activity_id=? AND chapter_id=?",
            (uid, activity_id, chapter_id)
        )
        stat = {"hero_id": 236, "hero_blood": 45096, "hero_max_blood": 45096}
        if row and row[0]["stat_json"]:
            try:
                curr_stat = json.loads(row[0]["stat_json"])
                stat["hero_id"] = curr_stat.get("hero_id") or curr_stat.get("a", 236)
                stat["hero_max_blood"] = curr_stat.get("hero_max_blood") or curr_stat.get("c", 45096)
                stat["hero_blood"] = stat["hero_max_blood"]
            except Exception:
                pass

        db.execute(
            "UPDATE hodur_progress SET option_id_list='[]', stat_json=?, update_ts=? "
            "WHERE uid=? AND activity_id=? AND chapter_id=?",
            (json.dumps(stat), int(time.time()), uid, activity_id, chapter_id)
        )
        return {"result": 0}

    def settle_hodur_challenge(self, ctx, uid, activity_id=3941101):
        """结算霍德尔挑战模式"""
        return {"result": 0}

    def select_hodur_event(self, ctx, uid, chapter_id, option_id, activity_id=3941101):
        """选择剧情事件/词缀选项"""
        db = getattr(ctx, "db", self.db)
        self.get_or_init_hodur(db, uid, activity_id)
        row = db.query(
            "SELECT option_id_list FROM hodur_progress WHERE uid=? AND activity_id=? AND chapter_id=?",
            (uid, activity_id, chapter_id)
        )
        opts = []
        if row and row[0]["option_id_list"]:
            try:
                opts = json.loads(row[0]["option_id_list"])
            except Exception:
                opts = []
        if option_id not in opts:
            opts.append(int(option_id))
        db.execute(
            "UPDATE hodur_progress SET option_id_list=?, update_ts=? "
            "WHERE uid=? AND activity_id=? AND chapter_id=?",
            (json.dumps(opts), int(time.time()), uid, activity_id, chapter_id)
        )
        return {"result": 0, "round": len(opts)}

    def settle_hodur_stage(self, ctx, uid, dest_int, win=True):
        """霍德尔战斗关卡结算 (5310201 <= dest_int <= 5310222)"""
        if not win:
            return None
        db = getattr(ctx, "db", self.db)
        chap_id = HODUR_STAGE_TO_CHAPTER.get(dest_int, 1)
        self.get_or_init_hodur(db, uid, activity_id=3941101)
        row = db.query(
            "SELECT stage_clear_json FROM hodur_progress WHERE uid=? AND activity_id=3941101 AND chapter_id=?",
            (uid, chap_id)
        )
        clears = []
        if row and row[0]["stage_clear_json"]:
            try:
                clears = json.loads(row[0]["stage_clear_json"])
            except Exception:
                clears = []
        existing = next((x for x in clears if (x.get("boss_id") == dest_int or x.get("stage_id") == dest_int)), None)
        is_first = False
        if not existing:
            clears.append({"boss_id": dest_int, "result": 1})
            is_first = True
            db.execute(
                "UPDATE hodur_progress SET stage_clear_json=?, update_ts=? "
                "WHERE uid=? AND activity_id=3941101 AND chapter_id=?",
                (json.dumps(clears), int(time.time()), uid, chap_id)
            )

        # 触发任务系统 condition=400004
        try:
            bus.emit(Events.STAGE_PASS, ctx, uid,
                     stage_id=dest_int,
                     stage_type="hodur",
                     dest_int=dest_int,
                     is_first=is_first)
        except Exception as e:
            if hasattr(ctx, "log"):
                ctx.log(f"[Hodur] 广播 STAGE_PASS 异常: {e}")

        return {"chapter_id": chap_id, "stage_id": dest_int, "is_first": is_first}

    # ==================== 7. 潜质觉醒 · 现时无返之途·薇儿丹蒂 SP (Activity 242841/242851/242871) ====================

    def _get_sphero_active_entrusts(self, db, uid):
        """获取玩家当前进行中的委托列表"""
        rows = db.query(
            "SELECT schedule_type, schedule_id, ts FROM sp_hero_challenge_schedule WHERE uid=? AND activity_id=? ORDER BY schedule_type",
            (uid, SP_HERO_ACTIVITY_ID)
        )
        return [
            {"index": int(r["schedule_type"]), "entrust_id": int(r["schedule_id"]), "start_time": int(r["ts"])}
            for r in rows
        ]

    def build_83016_payload(self, arg1, arg2=None):
        """生成 sc_83016 SP 英雄挑战主进入数据 payload，兼容 (db, uid) 与 (uid, db)"""
        from codec import encode
        db, uid = self._resolve_db_uid(arg1, arg2)

        p_json_str = None
        if uid == 2174928301:
            try:
                p_row = db.query("SELECT payload_json FROM login_push WHERE cmd=83016")
                if p_row and p_row[0].get("payload_json"):
                    p_json_str = p_row[0]["payload_json"]
            except Exception:
                pass

        base_data = None
        if p_json_str:
            try:
                base_data = json.loads(p_json_str) if isinstance(p_json_str, str) else p_json_str
            except Exception:
                base_data = None

        if not base_data:
            base_data = {
                "activity_id": SP_HERO_ACTIVITY_ID,
                "begin_entrust_list": [],
                "entrust_id_list": [2428402, 2428407, 2428407, 2428404, 2428405],
                "entrust_refresh_times": 0,
                "passed_chapter_level_list": [],
                "train_list": [
                    {"type": 3, "passed_level": [], "value": 0},
                    {"type": 2, "passed_level": [], "value": 0},
                    {"type": 4, "passed_level": [], "value": 0},
                ],
                "challenge_times": 0
            }

        # 同步数据库中当前的委托记录 (sp_hero_challenge_schedule)
        try:
            sched_rows = db.query(
                "SELECT schedule_type, schedule_id, ts FROM sp_hero_challenge_schedule WHERE uid=? AND activity_id=? ORDER BY rowid",
                (uid, SP_HERO_ACTIVITY_ID)
            )
            if sched_rows:
                base_data["begin_entrust_list"] = [
                    {"index": int(r["schedule_type"]), "entrust_id": int(r["schedule_id"]), "start_time": int(r["ts"])}
                    for r in sched_rows
                ]
            elif "begin_entrust_list" not in base_data:
                base_data["begin_entrust_list"] = []
        except Exception:
            pass

        # 同步剧情通关关卡 (sp_hero_challenge_stage stage_kind='unlock')
        try:
            story_rows = db.query(
                "SELECT stage_id FROM sp_hero_challenge_stage WHERE uid=? AND activity_id=? AND stage_kind='unlock' ORDER BY rowid",
                (uid, SP_HERO_ACTIVITY_ID)
            )
            if story_rows:
                base_data["passed_chapter_level_list"] = [int(r["stage_id"]) for r in story_rows]
            elif "passed_chapter_level_list" not in base_data:
                base_data["passed_chapter_level_list"] = []
        except Exception:
            pass

        # 同步训练组进度 (sp_hero_challenge_stage stage_kind='progress')
        try:
            train_rows = db.query(
                "SELECT stage_id, group_type, value FROM sp_hero_challenge_stage WHERE uid=? AND activity_id=? AND stage_kind='progress' ORDER BY rowid",
                (uid, SP_HERO_ACTIVITY_ID)
            )
            if train_rows:
                t_map = {t["type"]: t for t in base_data.get("train_list", [])}
                for r in train_rows:
                    gt = int(r["group_type"])
                    val = int(r["value"])
                    sid = int(r["stage_id"])
                    if gt not in t_map:
                        t_map[gt] = {"type": gt, "passed_level": [], "value": val}
                        base_data.setdefault("train_list", []).append(t_map[gt])
                    t_map[gt]["value"] = val
                    if sid not in t_map[gt]["passed_level"]:
                        t_map[gt]["passed_level"].append(sid)
        except Exception:
            pass

        return encode("sc_83016", base_data)

    def build_83020_payload(self, arg1, arg2=None):
        """生成 sc_83020 SP 英雄挑战 Boss 阶段数据 payload，兼容 (db, uid) 与 (uid, db)"""
        from codec import encode
        db, uid = self._resolve_db_uid(arg1, arg2)
        row = None
        try:
            rows = db.query(
                "SELECT boss_stage_id, score, achieved_list, fight_cnt, flag FROM sp_hero_boss_progress WHERE uid=? AND activity_id=?",
                (uid, SP_HERO_BOSS_ACTIVITY_ID)
            )
            if rows:
                row = rows[0]
        except Exception:
            row = None

        if row:
            ach_list = []
            if row.get("achieved_list"):
                try:
                    ach_list = json.loads(row["achieved_list"]) if isinstance(row["achieved_list"], str) else row["achieved_list"]
                except Exception:
                    ach_list = []
            boss_stage = int(row.get("boss_stage_id") or 0)
            score = int(row.get("score") or 0)
            data = {
                "activity_id": SP_HERO_BOSS_ACTIVITY_ID,
                "fight_cnt": int(row.get("fight_cnt") or 0),
                "score_info_list": [{"stage_id": boss_stage, "score": score}] if boss_stage else [],
                "is_start": False,
                "got_award_cfg_list": [int(x) for x in ach_list]
            }
        else:
            data = {
                "activity_id": SP_HERO_BOSS_ACTIVITY_ID,
                "fight_cnt": 0,
                "score_info_list": [],
                "is_start": False,
                "got_award_cfg_list": []
            }
        return encode("sc_83020", data)

    def build_83021_payload(self, arg1, arg2=None):
        """生成 sc_83021 SP 英雄挑战拼图通关列表 payload，兼容 (db, uid) 与 (uid, db)"""
        from codec import encode
        db, uid = self._resolve_db_uid(arg1, arg2)
        stages = []
        try:
            rows = db.query(
                "SELECT cleared_stage_list FROM sp_hero_puzzle_progress WHERE uid=? AND activity_id=?",
                (uid, SP_HERO_PUZZLE_ACTIVITY_ID)
            )
            if rows and rows[0].get("cleared_stage_list"):
                raw = rows[0]["cleared_stage_list"]
                stages = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            stages = []
        return encode("sc_83021", {"stage_completed_list": [int(x) for x in stages]})

    def build_83026_payload(self, arg1, arg2=None):
        """生成 sc_83026 SP 英雄挑战烤肉/加速记录 payload"""
        from codec import encode
        return encode("sc_83026", {"reward_record": 0, "ticket_record": 0})

    def confirm_sphero_schedule(self, ctx, uid, schedule_info_list, activity_id=SP_HERO_ACTIVITY_ID):
        """确认/保存今日日程列表"""
        db = getattr(ctx, "db", self.db)
        now = int(time.time())
        for item in schedule_info_list:
            idx = int(item.get("index") or 1)
            sid = int(item.get("schedule_id") or 2428401)
            db.execute(
                "INSERT OR REPLACE INTO sp_hero_challenge_schedule (uid, activity_id, schedule_type, schedule_id, ts, update_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, activity_id, idx, sid, now, now)
            )
        return {"result": 0}

    def dispatch_sphero_entrust(self, ctx, uid, index, entrust_id, activity_id=SP_HERO_ACTIVITY_ID):
        """派遣委托任务"""
        db = getattr(ctx, "db", self.db)
        now = int(time.time())
        db.execute(
            "INSERT OR REPLACE INTO sp_hero_challenge_schedule (uid, activity_id, schedule_type, schedule_id, ts, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, activity_id, index, entrust_id, now, now)
        )
        active = self._get_sphero_active_entrusts(db, uid)
        return {"result": 0, "begin_entrust_list": active}

    def cancel_sphero_entrust(self, ctx, uid, index, activity_id=SP_HERO_ACTIVITY_ID):
        """取消委托任务"""
        db = getattr(ctx, "db", self.db)
        db.execute(
            "DELETE FROM sp_hero_challenge_schedule WHERE uid=? AND activity_id=? AND schedule_type=?",
            (uid, activity_id, index)
        )
        active = self._get_sphero_active_entrusts(db, uid)
        return {"result": 0, "begin_entrust_list": active}

    def get_sphero_entrust_award(self, ctx, uid, index_list, activity_id=SP_HERO_ACTIVITY_ID):
        """领取已完成委托奖励"""
        db = getattr(ctx, "db", self.db)
        for idx in index_list:
            db.execute(
                "DELETE FROM sp_hero_challenge_schedule WHERE uid=? AND activity_id=? AND schedule_type=?",
                (uid, activity_id, int(idx))
            )
        award_num = max(1, len(index_list)) * 100
        rewards = [(53100, award_num)]
        if hasattr(ctx, "grant_rewards"):
            ctx.grant_rewards(rewards)
        else:
            try:
                db.add_material(uid, 53100, award_num)
            except Exception:
                pass

        try:
            from task_listener import _trigger_task_condition, _push_task_diff
            c1 = _trigger_task_condition(db, uid, condition=160001, delta=len(index_list))
            c2 = _trigger_task_condition(db, uid, condition=302, delta=award_num, param=53100)
            _push_task_diff(ctx, uid, c1 + c2)
        except Exception:
            pass

        active = self._get_sphero_active_entrusts(db, uid)
        return {
            "result": 0,
            "reward_list": [{"id": 53100, "num": award_num}],
            "begin_entrust_list": active
        }

    def use_sphero_accelerator(self, ctx, uid, index, use_cnt=1, activity_id=SP_HERO_ACTIVITY_ID):
        """使用加速卡加速委托"""
        db = getattr(ctx, "db", self.db)
        db.execute(
            "UPDATE sp_hero_challenge_schedule SET ts = ts - ? WHERE uid=? AND activity_id=? AND schedule_type=?",
            (int(use_cnt) * 3600, uid, activity_id, int(index))
        )
        active = self._get_sphero_active_entrusts(db, uid)
        return {"result": 0, "begin_entrust_list": active}

    def settle_sphero_barbecue(self, ctx, uid, stage_id, grade_id, hero_id, result):
        """烤肉小游戏结算"""
        db = getattr(ctx, "db", self.db)
        award_num = 50
        rewards = [(53100, award_num)]
        if hasattr(ctx, "grant_rewards"):
            ctx.grant_rewards(rewards)
        else:
            try:
                db.add_material(uid, 53100, award_num)
            except Exception:
                pass

        try:
            from task_listener import _trigger_task_condition, _push_task_diff
            c1 = _trigger_task_condition(db, uid, condition=160008, delta=1)
            c2 = _trigger_task_condition(db, uid, condition=302, delta=award_num, param=53100)
            _push_task_diff(ctx, uid, c1 + c2)
        except Exception:
            pass

        return {
            "result": 0,
            "reward_list": [{"id": 53100, "num": award_num}],
            "reward_recode": 1,
            "ticket_recode": 0
        }

    def settle_sphero_stage(self, ctx, uid, dest_int, battle_score=0, win=True):
        """SP 英雄关卡战斗结算：5170201~5170206 (剧情), 5170211~5170219 (训练), 5170221~5170222 (Boss)"""
        if not win:
            return None
        db = getattr(ctx, "db", self.db)
        now = int(time.time())
        extra_frames = []

        if 5170201 <= dest_int <= 5170206:
            db.execute(
                "INSERT OR IGNORE INTO sp_hero_challenge_stage (uid, activity_id, stage_id, stage_kind, group_type, value, update_ts) "
                "VALUES (?, ?, ?, 'unlock', 0, 0, ?)",
                (uid, SP_HERO_ACTIVITY_ID, dest_int, now)
            )
            try:
                bus.emit(Events.STAGE_PASS, ctx, uid, stage_id=dest_int, stage_type="sphero_story", dest_int=dest_int, win=win)
            except Exception:
                pass
            return {"type": "story", "stage_id": dest_int}

        elif 5170211 <= dest_int <= 5170219:
            grp = SP_HERO_TRAIN_STAGES.get(dest_int, 2)
            row = db.query(
                "SELECT value FROM sp_hero_challenge_stage WHERE uid=? AND activity_id=? AND group_type=? AND stage_kind='progress'",
                (uid, SP_HERO_ACTIVITY_ID, grp)
            )
            cur_val = int(row[0]["value"]) if row else 0
            new_val = min(3000, cur_val + 250)
            db.execute(
                "INSERT OR REPLACE INTO sp_hero_challenge_stage (uid, activity_id, stage_id, stage_kind, group_type, value, update_ts) "
                "VALUES (?, ?, ?, 'progress', ?, ?, ?)",
                (uid, SP_HERO_ACTIVITY_ID, dest_int, grp, new_val, now)
            )
            db.execute(
                "UPDATE sp_hero_challenge_stage SET value=?, update_ts=? WHERE uid=? AND activity_id=? AND group_type=? AND stage_kind='progress'",
                (new_val, now, uid, SP_HERO_ACTIVITY_ID, grp)
            )
            t_rows = db.query(
                "SELECT stage_id, group_type, value FROM sp_hero_challenge_stage WHERE uid=? AND activity_id=? AND stage_kind='progress'",
                (uid, SP_HERO_ACTIVITY_ID)
            )
            grp_map = {2: [], 3: [], 4: []}
            grp_val = {2: 0, 3: 0, 4: 0}
            for tr in t_rows:
                gt = int(tr["group_type"])
                if gt in grp_map:
                    sid = int(tr["stage_id"])
                    if sid not in grp_map[gt]:
                        grp_map[gt].append(sid)
                    grp_val[gt] = max(grp_val[gt], int(tr["value"]))
            train_list = [
                {"type": gt, "passed_level": grp_map[gt], "value": grp_val[gt]}
                for gt in [3, 2, 4]
            ]
            from codec import encode
            p83015 = ctx.codec_encode("sc_83015", {"activity_id": SP_HERO_ACTIVITY_ID, "train_list": train_list}) if ctx.codec_encode else encode("sc_83015", {"activity_id": SP_HERO_ACTIVITY_ID, "train_list": train_list})
            if p83015:
                extra_frames.append(DownFrame(83015, p83015))
            try:
                bus.emit(Events.STAGE_PASS, ctx, uid, stage_id=dest_int, stage_type="sphero_train", dest_int=dest_int, win=win, group_type=grp, progress=new_val)
            except Exception:
                pass
            return {"type": "train", "stage_id": dest_int, "frames": extra_frames}

        elif dest_int in (5170221, 5170222):
            b_rows = db.query(
                "SELECT boss_stage_id, score, fight_cnt, achieved_list FROM sp_hero_boss_progress WHERE uid=? AND activity_id=?",
                (uid, SP_HERO_BOSS_ACTIVITY_ID)
            )
            cur_sc = int(b_rows[0]["score"]) if b_rows else 0
            cur_cnt = int(b_rows[0]["fight_cnt"]) if b_rows else 0
            new_sc = max(cur_sc, battle_score or 4500000)
            new_cnt = cur_cnt + 1
            ach_list = [401, 402, 403, 406]
            db.execute(
                "INSERT OR REPLACE INTO sp_hero_boss_progress (uid, activity_id, boss_stage_id, score, achieved_list, fight_cnt, flag, update_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (uid, SP_HERO_BOSS_ACTIVITY_ID, dest_int, new_sc, json.dumps(ach_list), new_cnt, now)
            )
            from codec import encode
            p83020 = ctx.codec_encode("sc_83020", {
                "activity_id": SP_HERO_BOSS_ACTIVITY_ID,
                "fight_cnt": new_cnt,
                "score_info_list": [{"stage_id": dest_int, "score": new_sc}],
                "is_start": False,
                "got_award_cfg_list": ach_list
            }) if ctx.codec_encode else encode("sc_83020", {
                "activity_id": SP_HERO_BOSS_ACTIVITY_ID,
                "fight_cnt": new_cnt,
                "score_info_list": [{"stage_id": dest_int, "score": new_sc}],
                "is_start": False,
                "got_award_cfg_list": ach_list
            })
            if p83020:
                extra_frames.append(DownFrame(83020, p83020))
            try:
                bus.emit(Events.STAGE_PASS, ctx, uid, stage_id=dest_int, stage_type="sphero_boss", dest_int=dest_int, win=win, score=new_sc)
            except Exception:
                pass
            return {"type": "boss", "stage_id": dest_int, "frames": extra_frames}

        return None

    # ==================== 7. 极限机装 · 乌尔碰碰车 (Activity 4243101) ====================

    def build_89701_payload(self, arg1, arg2=None):
        """乌尔碰碰车登录全量数据 sc_89701 动态下行构建"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rows = db.query(
            "SELECT using_vehicle, stages_json, unlock_buffs_json FROM vehicle_ball_progress WHERE uid=?",
            (uid,)
        )
        if rows:
            r = rows[0]
            try:
                stages = json.loads(r.get("stages_json") or "[]")
            except Exception:
                stages = []
            try:
                unlock_buffs = json.loads(r.get("unlock_buffs_json") or "[]")
            except Exception:
                unlock_buffs = []
            using_vehicle = int(r.get("using_vehicle") or 50111)
        else:
            # 新玩家自愈初始化
            using_vehicle = 50111
            stages = []
            unlock_buffs = []
            now_ts = int(time.time())
            db.execute("""
                INSERT OR IGNORE INTO vehicle_ball_progress (uid, using_vehicle, stages_json, unlock_buffs_json, update_ts)
                VALUES (?, ?, '[]', '[]', ?)
            """, (uid, using_vehicle, now_ts))

        payload_dict = {
            "stages": stages,
            "using_vehicle": using_vehicle,
            "unlock_buffs": unlock_buffs,
        }
        from codec import encode
        return encode("sc_89701", payload_dict)

    def pass_vehicle_stage(self, ctx, uid, stage_id, is_pass, kv_list=None, buffs=None, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        """乌尔碰碰车关卡结算 cs_89702 -> sc_89703"""
        db = getattr(ctx, "db", self.db)
        kv_list = kv_list or []
        buffs = buffs or []

        rows = db.query(
            "SELECT using_vehicle, stages_json, unlock_buffs_json FROM vehicle_ball_progress WHERE uid=?",
            (uid,)
        )
        if rows:
            r = rows[0]
            stages = json.loads(r.get("stages_json") or "[]")
            unlock_buffs = json.loads(r.get("unlock_buffs_json") or "[]")
            using_vehicle = int(r.get("using_vehicle") or 50111)
        else:
            stages = []
            unlock_buffs = []
            using_vehicle = 50111

        buff_dict = {int(b["key"]): int(b["value"]) for b in unlock_buffs}

        # 局内新解锁 buff 加入
        for b_id in buffs:
            b_id = int(b_id)
            if b_id not in buff_dict:
                buff_dict[b_id] = 0  # 0: CAN_RECEIVE_REWARD

        if is_pass:
            if stage_id not in stages:
                stages.append(stage_id)

        now_ts = int(time.time())
        new_buffs = [{"key": k, "value": v} for k, v in buff_dict.items()]
        db.execute("""
            INSERT OR REPLACE INTO vehicle_ball_progress (uid, using_vehicle, stages_json, unlock_buffs_json, update_ts)
            VALUES (?, ?, ?, ?, ?)
        """, (uid, using_vehicle, json.dumps(stages), json.dumps(new_buffs), now_ts))

        # 提取 kv: 1=kill_count, 2=merge_count
        kill_cnt = 0
        merge_cnt = 0
        for item in kv_list:
            k = int(item.get("key", 0))
            v = int(item.get("value", 0))
            if k == 1:
                kill_cnt += v
            elif k == 2:
                merge_cnt += v

        from event_bus import bus, Events
        bus.emit(
            Events.STAGE_PASS,
            ctx,
            uid,
            stage_id=stage_id,
            stage_type="vehicle_ball",
            win=bool(is_pass),
            dest_int=stage_id,
            kill_count=kill_cnt,
            merge_count=merge_cnt
        )

        return {"result": 0}

    def set_vehicle(self, ctx, uid, vehicle_id, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        """乌尔碰碰车更换出战载具 cs_89704 -> sc_89705"""
        db = getattr(ctx, "db", self.db)
        rows = db.query(
            "SELECT using_vehicle, stages_json, unlock_buffs_json FROM vehicle_ball_progress WHERE uid=?",
            (uid,)
        )
        if rows:
            r = rows[0]
            stages = json.loads(r.get("stages_json") or "[]")
            unlock_buffs = json.loads(r.get("unlock_buffs_json") or "[]")
        else:
            stages = []
            unlock_buffs = []

        now_ts = int(time.time())
        db.execute("""
            INSERT OR REPLACE INTO vehicle_ball_progress (uid, using_vehicle, stages_json, unlock_buffs_json, update_ts)
            VALUES (?, ?, ?, ?, ?)
        """, (uid, int(vehicle_id), json.dumps(stages), json.dumps(unlock_buffs), now_ts))

        return {"result": 0}

    def get_vehicle_illustrate_reward(self, ctx, uid, buff_id, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        """乌尔碰碰车图鉴领奖 cs_89706 -> sc_89707"""
        db = getattr(ctx, "db", self.db)
        buff_id = int(buff_id)
        rows = db.query(
            "SELECT using_vehicle, stages_json, unlock_buffs_json FROM vehicle_ball_progress WHERE uid=?",
            (uid,)
        )
        if rows:
            r = rows[0]
            stages = json.loads(r.get("stages_json") or "[]")
            unlock_buffs = json.loads(r.get("unlock_buffs_json") or "[]")
            using_vehicle = int(r.get("using_vehicle") or 50111)
        else:
            stages = []
            unlock_buffs = []
            using_vehicle = 50111

        buff_dict = {int(b["key"]): int(b["value"]) for b in unlock_buffs}

        # 检查是否已领过
        cur_state = buff_dict.get(buff_id)
        if cur_state == 1:
            return {"result": 0, "reward_list": []}

        # 标记为已领奖 (1: RECEIVED)
        buff_dict[buff_id] = 1
        now_ts = int(time.time())
        new_buffs = [{"key": k, "value": v} for k, v in buff_dict.items()]
        db.execute("""
            INSERT OR REPLACE INTO vehicle_ball_progress (uid, using_vehicle, stages_json, unlock_buffs_json, update_ts)
            VALUES (?, ?, ?, ?, ?)
        """, (uid, using_vehicle, json.dumps(stages), json.dumps(new_buffs), now_ts))

        # 查验奖励并入库 (固定 10 钻石)
        rewards = [{"id": 1, "num": 10}]
        try:
            from inventory_service import InventoryService
            InventoryService.grant_items(ctx, uid, [(1, 10)], source="vehicle_ball_buff_reward")
        except Exception as e:
            if hasattr(ctx, "log"):
                ctx.log(f"[VehicleBall] 发放钻石奖励异常: {e}", "WARN")

        return {"result": 0, "reward_list": rewards}

    def unlock_vehicle_buff(self, ctx, uid, buff_id, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        """乌尔碰碰车解锁 Buff cs_89708 -> sc_89709"""
        db = getattr(ctx, "db", self.db)
        buff_id = int(buff_id)
        rows = db.query(
            "SELECT using_vehicle, stages_json, unlock_buffs_json FROM vehicle_ball_progress WHERE uid=?",
            (uid,)
        )
        if rows:
            r = rows[0]
            stages = json.loads(r.get("stages_json") or "[]")
            unlock_buffs = json.loads(r.get("unlock_buffs_json") or "[]")
            using_vehicle = int(r.get("using_vehicle") or 50111)
        else:
            stages = []
            unlock_buffs = []
            using_vehicle = 50111

        buff_dict = {int(b["key"]): int(b["value"]) for b in unlock_buffs}
        if buff_id not in buff_dict:
            buff_dict[buff_id] = 0  # 0: CAN_RECEIVE_REWARD
            now_ts = int(time.time())
            new_buffs = [{"key": k, "value": v} for k, v in buff_dict.items()]
            db.execute("""
                INSERT OR REPLACE INTO vehicle_ball_progress (uid, using_vehicle, stages_json, unlock_buffs_json, update_ts)
                VALUES (?, ?, ?, ?, ?)
            """, (uid, using_vehicle, json.dumps(stages), json.dumps(new_buffs), now_ts))

        return {"result": 0}

    # ==================== 9. 浮光绎曲与鸣律探微 · 常驻音律演奏会 (Activity 3814801 / 283041) ====================

    def get_or_init_music_records(self, db, uid):
        """获取或初始化浮光绎曲所有乐曲成绩记录"""
        db = db or self.db
        rows = db.query(
            "SELECT music_id, score, sign, is_rewarded FROM resident_music_record WHERE uid = ?",
            (uid,)
        )
        if not rows:
            now_ts = int(time.time())
            for mid_str, info in _MUSIC_CATALOG.items():
                db.execute(
                    "INSERT OR IGNORE INTO resident_music_record (uid, music_id, score, sign, is_rewarded, update_ts) VALUES (?, ?, 0, 0, 0, ?)",
                    (uid, int(mid_str), now_ts)
                )
            rows = db.query(
                "SELECT music_id, score, sign, is_rewarded FROM resident_music_record WHERE uid = ?",
                (uid,)
            )
        return rows

    def build_61047_payload(self, arg1, arg2=None):
        """生成常驻音律音乐会 sc_61047 登录/查询载荷 (challenge_info: [{id, score, sign}])，返回 bytes"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rows = self.get_or_init_music_records(db, uid)
        challenges = [
            {
                "id": int(r["music_id"]),
                "score": int(r["score"] or 0),
                "sign": int(r["sign"] or 0),
            }
            for r in rows
        ]
        obj = {"challenge_info": challenges}
        try:
            from codec import encode
            return encode("sc_61047", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_61047 失败: {e}")
            return b""

    def build_83124_payload(self, arg1, arg2=None):
        """生成夏日鸣律探微小游戏 sc_83124 载荷 (rhythm_stage_list, story_id_list)，返回 bytes"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rows = db.query(
            "SELECT stage_id, star_id_list, use_seconds FROM rhythm_game_progress WHERE uid = ? ORDER BY stage_id",
            (uid,)
        )
        stage_list = []
        for r in rows:
            try:
                stars = json.loads(r["star_id_list"]) if isinstance(r["star_id_list"], str) else (r["star_id_list"] or [])
            except Exception:
                stars = []
            st_item = {
                "id": int(r["stage_id"]),
                "use_seconds": int(r["use_seconds"] or 0),
            }
            if stars:
                st_item["star_id_list"] = stars
            stage_list.append(st_item)

        s_rows = db.query("SELECT story_id FROM rhythm_story_record WHERE uid = ?", (uid,))
        story_ids = [int(r["story_id"]) for r in s_rows]
        if not story_ids:
            story_ids = [911001011]

        obj = {
            "rhythm_stage_list": stage_list,
            "story_id_list": story_ids,
        }
        try:
            from codec import encode
            return encode("sc_83124", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_83124 失败: {e}")
            return b""

    def settle_music_stage(self, ctx, uid, music_id, score, state, other_data=None):
        """
        处理乐曲游玩结算 (cs_61048 -> sc_61049):
        1. 比对并刷新历史最高分 score 与完成状态 state (1:完成, 2:NoMistake全连, 3:Perfect全P);
        2. 触发全局 EventBus 广播与任务系统联动 (Condition 50104 -> 任务 40601001~40601008);
        3. 返回 sc_61049 响应体。
        """
        db = getattr(ctx, "db", self.db)
        now_ts = int(time.time())
        music_id = int(music_id)
        score = int(score)
        state = int(state)

        rows = db.query(
            "SELECT score, sign FROM resident_music_record WHERE uid = ? AND music_id = ?",
            (uid, music_id)
        )
        old_score = int(rows[0]["score"] or 0) if rows else 0
        old_sign = int(rows[0]["sign"] or 0) if rows else 0

        new_score = max(old_score, score)
        new_sign = max(old_sign, state)

        db.execute(
            """INSERT INTO resident_music_record (uid, music_id, score, sign, is_rewarded, update_ts)
               VALUES (?, ?, ?, ?, 0, ?)
               ON CONFLICT(uid, music_id) DO UPDATE SET
                   score = excluded.score,
                   sign = excluded.sign,
                   update_ts = excluded.update_ts""",
            (uid, music_id, new_score, new_sign, now_ts)
        )

        cfg = _MUSIC_CATALOG.get(str(music_id)) or {}
        act_id = cfg.get("activity_id", 0)
        song_idx = None
        if 91491 <= act_id <= 91498:
            song_idx = act_id - 91490
        elif 3814901 <= act_id <= 3814908:
            song_idx = act_id - 3814900

        if song_idx is not None and new_sign >= 1:
            task_id = 40601000 + song_idx
            try:
                bus.emit(Events.STAGE_PASS, ctx, uid, times=1, stage_id=music_id, stage_type="music_game", task_id=task_id, song_idx=song_idx)
            except Exception as e:
                logger.warning(f"[settle_music_stage] 任务事件广播异常: {e}")

        return {"result": 0}

    def claim_music_rewards(self, ctx, uid, id_list):
        """
        领取乐曲挑战奖励 (cs_61050 -> sc_61051):
        1. 校验曲目达标状态 (sign >= 1) 与是否已领奖 (is_rewarded == 0);
        2. 从 _MUSIC_CATALOG 提取配置奖励并通过 InventoryService 发放;
        3. 置位 is_rewarded = 1 并防重领;
        4. 返回 sc_61051 响应体。
        """
        db = getattr(ctx, "db", self.db)
        granted_rewards = []
        reward_items_to_grant = []

        now_ts = int(time.time())
        for mid in id_list:
            mid = int(mid)
            rows = db.query(
                "SELECT sign, is_rewarded FROM resident_music_record WHERE uid = ? AND music_id = ?",
                (uid, mid)
            )
            if not rows or int(rows[0]["sign"] or 0) < 1 or int(rows[0]["is_rewarded"] or 0) == 1:
                continue

            cfg = _MUSIC_CATALOG.get(str(mid)) or {}
            rewards = cfg.get("rewards") or []
            if rewards:
                for item_id, count in rewards:
                    reward_items_to_grant.append((item_id, count))
                    granted_rewards.append({"id": item_id, "num": count})

            db.execute(
                "UPDATE resident_music_record SET is_rewarded = 1, update_ts = ? WHERE uid = ? AND music_id = ?",
                (now_ts, uid, mid)
            )

        if reward_items_to_grant:
            InventoryService.grant_items(ctx, uid, reward_items_to_grant, source="resident_music_reward")

        return {"result": 0, "reward_list": granted_rewards}

    def play_rhythm_story(self, ctx, uid, activity_id, story_id):
        """记录鸣律探微剧情阅览 (cs_83126 -> sc_83127)"""
        db = getattr(ctx, "db", self.db)
        now_ts = int(time.time())
        story_id = int(story_id)
        db.execute(
            """INSERT INTO rhythm_story_record (uid, story_id, update_ts)
               VALUES (?, ?, ?)
               ON CONFLICT(uid, story_id) DO UPDATE SET update_ts = excluded.update_ts""",
            (uid, story_id, now_ts)
        )
        return {"result": 0}

    # ==================== 10. 食与异世界 · 夏日餐厅 (Activity 3539101) ====================

    def get_or_init_summer_pub(self, db, uid):
        """获取或初始化夏日酒馆/餐厅经营记录"""
        db = db or self.db
        if db is None:
            from account_db import get_db
            db = get_db()
        rows = db.query(
            "SELECT stage_list, cook_stages, battle_stages, illustrated, time_state FROM summer_pub_record WHERE uid = ?",
            (uid,)
        )
        if not rows:
            stage_list, cook_stages, battle_stages, illustrated, time_state = [], {}, [], {}, 1
            if uid in (2174928301, 99999905):
                capture_rows = db.query("SELECT payload_json FROM login_push WHERE cmd = 89011")
                if capture_rows and capture_rows[0].get("payload_json"):
                    try:
                        p = json.loads(capture_rows[0]["payload_json"])
                        stage_list = p.get("stage_list", [])
                        cook_stages = {str(item["stage_id"]): item["state"] for item in p.get("cook_stage_list", [])}
                        battle_stages = p.get("battle_stage_list", [])
                        illustrated = {str(item["illustrated_id"]): item["view_state"] for item in p.get("illustrated", [])}
                        time_state = p.get("time_state", 1)
                    except Exception:
                        pass
            now_ts = int(time.time())
            db.execute(
                """INSERT OR REPLACE INTO summer_pub_record 
                (uid, stage_list, cook_stages, battle_stages, illustrated, time_state, update_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uid, json.dumps(stage_list), json.dumps(cook_stages), json.dumps(battle_stages), json.dumps(illustrated), time_state, now_ts)
            )
            rows = db.query(
                "SELECT stage_list, cook_stages, battle_stages, illustrated, time_state FROM summer_pub_record WHERE uid = ?",
                (uid,)
            )
        r = rows[0]
        return {
            "stage_list": json.loads(r["stage_list"]) if isinstance(r["stage_list"], str) else (r["stage_list"] or []),
            "cook_stages": json.loads(r["cook_stages"]) if isinstance(r["cook_stages"], str) else (r["cook_stages"] or {}),
            "battle_stages": json.loads(r["battle_stages"]) if isinstance(r["battle_stages"], str) else (r["battle_stages"] or []),
            "illustrated": json.loads(r["illustrated"]) if isinstance(r["illustrated"], str) else (r["illustrated"] or {}),
            "time_state": int(r["time_state"] or 1),
        }

    def save_summer_pub(self, db, uid, record):
        """保存夏日餐厅经营记录"""
        db = db or self.db
        if db is None:
            from account_db import get_db
            db = get_db()
        now_ts = int(time.time())
        db.execute(
            """INSERT OR REPLACE INTO summer_pub_record
            (uid, stage_list, cook_stages, battle_stages, illustrated, time_state, update_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                uid,
                json.dumps(record.get("stage_list", [])),
                json.dumps(record.get("cook_stages", {})),
                json.dumps(record.get("battle_stages", [])),
                json.dumps(record.get("illustrated", {})),
                int(record.get("time_state", 1)),
                now_ts
            )
        )

    def build_89011_payload(self, arg1, arg2=None):
        """生成食与异世界 sc_89011 登录/全量载荷，返回 bytes"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rec = self.get_or_init_summer_pub(db, uid)
        cook_list = [
            {"stage_id": int(k), "state": int(v)}
            for k, v in rec["cook_stages"].items()
        ]
        illu_list = [
            {"illustrated_id": int(k), "view_state": int(v)}
            for k, v in rec["illustrated"].items()
        ]
        obj = {
            "stage_list": [int(x) for x in rec["stage_list"]],
            "cook_stage_list": cook_list,
            "battle_stage_list": [int(x) for x in rec["battle_stages"]],
            "illustrated": illu_list,
            "time_state": int(rec["time_state"]),
        }
        try:
            from codec import encode
            return encode("sc_89011", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_89011 失败: {e}")
            return b""

    def finish_summer_pub_pinball(self, ctx, uid, stage_id, sub_stage_id=None, illustrated=None, result=1):
        """夏日餐厅·弹珠台关卡结算 (cs_89002 -> sc_89003)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_summer_pub(db, uid)
        stage_id = int(stage_id or 0)
        sub_stage_id = int(sub_stage_id or 0) if sub_stage_id else stage_id
        result = int(result or 1)

        if result == 1:
            stage_list = rec.get("stage_list", [])
            if sub_stage_id not in stage_list:
                stage_list.append(sub_stage_id)
            rec["stage_list"] = stage_list

            ill_dict = rec.get("illustrated", {})
            for ill_id in (illustrated or []):
                sid = str(ill_id)
                if sid not in ill_dict:
                    ill_dict[sid] = 1
            rec["illustrated"] = ill_dict

            self.save_summer_pub(db, uid, rec)

            # 触发关卡推进与任务条件 350001
            bus.emit(
                Events.SUMMER_PUB_LEVEL_PASS,
                ctx,
                uid,
                level_id=stage_id,
                sub_stage_id=sub_stage_id
            )
            bus.emit(
                Events.STAGE_PASS,
                ctx,
                uid,
                times=1,
                stage_id=sub_stage_id,
                stage_type="summer_pub"
            )
        return {"result": 0}

    def view_summer_pub_illustration(self, ctx, uid, ill_id):
        """夏日餐厅·查看图鉴消红点 (cs_89004 -> sc_89005)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_summer_pub(db, uid)
        ill_id = int(ill_id or 0)
        if ill_id > 0:
            rec["illustrated"][str(ill_id)] = 2
            self.save_summer_pub(db, uid, rec)
        return {"result": 0}

    def cook_summer_pub(self, ctx, uid, stage_id, state):
        """夏日餐厅·料理烹饪/看CG上报 (cs_89006 -> sc_89007)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_summer_pub(db, uid)
        stage_id = int(stage_id or 0)
        state = int(state or 0)
        rec["cook_stages"][str(stage_id)] = state
        self.save_summer_pub(db, uid, rec)

        bus.emit(
            Events.SUMMER_PUB_COOK,
            ctx,
            uid,
            stage_id=stage_id,
            state=state
        )
        lvl = SUMMER_PUB_COOK_TO_LEVEL.get(stage_id)
        if lvl:
            bus.emit(
                Events.SUMMER_PUB_LEVEL_PASS,
                ctx,
                uid,
                level_id=lvl,
                stage_id=stage_id
            )
        return {"result": 0}

    def change_summer_pub_time_state(self, ctx, uid, time_state=None):
        """夏日餐厅·昼夜状态切换 (cs_89008 -> sc_89009)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_summer_pub(db, uid)
        if time_state is not None:
            rec["time_state"] = int(time_state)
        else:
            cur = int(rec.get("time_state", 1))
            rec["time_state"] = 2 if cur == 1 else 1
        self.save_summer_pub(db, uid, rec)
        return {"result": 0}

    # -------------------------------------------------------------
    # 诡谈夜话 (Activity 4142701 / RogueCard) 协议族 (89601~89629)
    # -------------------------------------------------------------
    def get_or_init_rogue_card(self, db, uid):
        """获取或初始化诡谈夜话玩家数据"""
        db = db or self.db
        if db is None:
            from account_db import get_db
            db = get_db()
        rows = db.query(
            """SELECT thread_id, thread_state, battle_id,
                      finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
                      challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
                      challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
                      save_data, save_rollback, update_ts
               FROM rogue_card_game_record WHERE uid = ?""",
            (uid,)
        )
        if not rows:
            now_ts = int(time.time())
            db.execute(
                """INSERT OR REPLACE INTO rogue_card_game_record
                (uid, thread_id, thread_state, battle_id,
                 finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
                 challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
                 challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
                 save_data, save_rollback, update_ts)
                VALUES (?, 0, 0, 0, '[]', '[]', '[]', '[]', 0, 0, 0, 0, '[]', '[]', '[]', '[]', '{}', '{}', ?)""",
                (uid, now_ts)
            )
            rows = db.query(
                """SELECT thread_id, thread_state, battle_id,
                          finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
                          challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
                          challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
                          save_data, save_rollback, update_ts
                   FROM rogue_card_game_record WHERE uid = ?""",
                (uid,)
            )
        r = rows[0]
        def _parse_json(val, default):
            if val is None:
                return default
            if isinstance(val, (list, dict)):
                return val
            try:
                return json.loads(val)
            except Exception:
                return default

        return {
            "thread_id": int(r["thread_id"] or 0),
            "thread_state": int(r["thread_state"] or 0),
            "battle_id": int(r["battle_id"] or 0),
            "finish_thread_list": _parse_json(r["finish_thread_list"], []),
            "like_thread_list": _parse_json(r["like_thread_list"], []),
            "post_thread_list": _parse_json(r["post_thread_list"], []),
            "view_thread_list": _parse_json(r["view_thread_list"], []),
            "challenge_deck": int(r["challenge_deck"] or 0),
            "challenge_diff": int(r["challenge_diff"] or 0),
            "challenge_state": int(r["challenge_state"] or 0),
            "challenge_battle_id": int(r["challenge_battle_id"] or 0),
            "challenge_status_list": _parse_json(r["challenge_status_list"], []),
            "gather_card_list": _parse_json(r["gather_card_list"], []),
            "gather_enhance_list": _parse_json(r["gather_enhance_list"], []),
            "gather_weal_woe_list": _parse_json(r["gather_weal_woe_list"], []),
            "save_data": _parse_json(r["save_data"], {}),
            "save_rollback": _parse_json(r["save_rollback"], {}),
        }

    def save_rogue_card(self, db, uid, rec):
        """保存诡谈夜话玩家数据"""
        db = db or self.db
        if db is None:
            from account_db import get_db
            db = get_db()
        now_ts = int(time.time())
        db.execute(
            """INSERT OR REPLACE INTO rogue_card_game_record
            (uid, thread_id, thread_state, battle_id,
             finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
             challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
             challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
             save_data, save_rollback, update_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uid,
                int(rec.get("thread_id", 0)),
                int(rec.get("thread_state", 0)),
                int(rec.get("battle_id", 0)),
                json.dumps(rec.get("finish_thread_list", [])),
                json.dumps(rec.get("like_thread_list", [])),
                json.dumps(rec.get("post_thread_list", [])),
                json.dumps(rec.get("view_thread_list", [])),
                int(rec.get("challenge_deck", 0)),
                int(rec.get("challenge_diff", 0)),
                int(rec.get("challenge_state", 0)),
                int(rec.get("challenge_battle_id", 0)),
                json.dumps(rec.get("challenge_status_list", [])),
                json.dumps(rec.get("gather_card_list", [])),
                json.dumps(rec.get("gather_enhance_list", [])),
                json.dumps(rec.get("gather_weal_woe_list", [])),
                json.dumps(rec.get("save_data", {})),
                json.dumps(rec.get("save_rollback", {})),
                now_ts
            )
        )

    def build_89601_payload(self, arg1, arg2=None):
        """构造 sc_89601 登录下发全量状态帧（动态生成）"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rec = self.get_or_init_rogue_card(db, uid)
        obj = {
            "story": {
                "thread_id": int(rec.get("thread_id", 0)),
                "thread_state": int(rec.get("thread_state", 0)),
                "battle_id": int(rec.get("battle_id", 0)),
                "finish_thread_list": [int(x) for x in rec.get("finish_thread_list", [])],
                "like_thread_list": [int(x) for x in rec.get("like_thread_list", [])],
                "post_thread_list": [int(x) for x in rec.get("post_thread_list", [])],
                "view_thread_list": [int(x) for x in rec.get("view_thread_list", [])],
            },
            "challenge": {
                "deck": int(rec.get("challenge_deck", 0)),
                "diff": int(rec.get("challenge_diff", 0)),
                "state": int(rec.get("challenge_state", 0)),
                "battle_id": int(rec.get("challenge_battle_id", 0)),
                "status_list": rec.get("challenge_status_list", []),
            },
            "gather_card_list": [int(x) for x in rec.get("gather_card_list", [])],
            "gather_enhance_list": [int(x) for x in rec.get("gather_enhance_list", [])],
            "gather_weal_woe_list": [int(x) for x in rec.get("gather_weal_woe_list", [])],
        }
        try:
            from codec import encode
            return encode("sc_89601", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_89601 失败: {e}")
            return b""

    def start_rogue_card_post(self, ctx, uid, thread_id):
        """诡谈夜话·开帖 (cs_89602 -> sc_89603)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        tid = int(thread_id or 0)
        battle_id = int(time.time() * 1000) & 0x7FFFFFFF
        rec["thread_id"] = tid
        rec["thread_state"] = 1
        rec["battle_id"] = battle_id
        post_list = rec.get("post_thread_list", [])
        if tid not in post_list:
            post_list.append(tid)
        rec["post_thread_list"] = post_list
        self.save_rogue_card(db, uid, rec)
        return {"result": 0}

    def complete_rogue_card_story(self, ctx, uid, thread_id, story_id=0, story_id_index=0):
        """诡谈夜话·完成剧情 (cs_89604 -> sc_89605)"""
        return {"result": 0}

    def click_like_rogue_card_post(self, ctx, uid, thread_id):
        """诡谈夜话·论坛点赞 (cs_89606 -> sc_89607)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        tid = int(thread_id or 0)
        like_list = rec.get("like_thread_list", [])
        if tid not in like_list:
            like_list.append(tid)
        rec["like_thread_list"] = like_list
        self.save_rogue_card(db, uid, rec)
        return {"result": 0}

    def reback_rogue_card_post(self, ctx, uid, thread_id):
        """诡谈夜话·重开/回退帖子 (cs_89608 -> sc_89609)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        rec["thread_id"] = 0
        rec["thread_state"] = 0
        rec["battle_id"] = 0
        rec["save_data"] = {}
        rec["save_rollback"] = {}
        self.save_rogue_card(db, uid, rec)
        return {"result": 0}

    def view_rogue_card_thread_post(self, ctx, uid, thread_id):
        """诡谈夜话·论坛阅览 (cs_89610 -> sc_89611)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        tid = int(thread_id or 0)
        view_list = rec.get("view_thread_list", [])
        if tid not in view_list:
            view_list.append(tid)
        rec["view_thread_list"] = view_list
        self.save_rogue_card(db, uid, rec)
        return {"result": 0}

    def complete_rogue_card_post(self, ctx, uid, thread_id, joker_result, info=None):
        """诡谈夜话·主线帖子对局结算 (cs_89612 -> sc_89613)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        tid = int(thread_id or 0)
        win = int(joker_result or 0) == 1

        if win:
            finish_list = rec.get("finish_thread_list", [])
            if tid not in finish_list:
                finish_list.append(tid)
            rec["finish_thread_list"] = finish_list

        # 重置进行中主线状态和对局缓存
        rec["thread_id"] = 0
        rec["thread_state"] = 0
        rec["battle_id"] = 0
        rec["save_data"] = {}
        rec["save_rollback"] = {}

        # 解析图鉴收集项
        if info and isinstance(info, dict):
            use_items = info.get("use_item_id", []) or []
            enhance_items = info.get("enhance_id", []) or []
            weal_woe_items = info.get("weal_woe_id", []) or []

            card_list = rec.get("gather_card_list", [])
            for cid in use_items:
                if cid not in card_list:
                    card_list.append(cid)
            rec["gather_card_list"] = card_list

            enh_list = rec.get("gather_enhance_list", [])
            for eid in enhance_items:
                if eid not in enh_list:
                    enh_list.append(eid)
            rec["gather_enhance_list"] = enh_list

            ww_list = rec.get("gather_weal_woe_list", [])
            for wid in weal_woe_items:
                if wid not in ww_list:
                    ww_list.append(wid)
            rec["gather_weal_woe_list"] = ww_list

        self.save_rogue_card(db, uid, rec)

        if win:
            bus.emit(Events.ROGUE_CARD_POST_FINISH, ctx, uid, post_id=tid)

        return {"result": 0}

    def settle_rogue_card_challenge(self, ctx, uid, deck, diff, info=None):
        """诡谈夜话·挑战模式对局结算 (cs_89616 -> sc_89617)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        deck_id = int(deck or 0)
        diff_id = int(diff or 0)

        status_list = rec.get("challenge_status_list", [])
        found = False
        for s in status_list:
            if s.get("deck") == deck_id:
                found = True
                if diff_id > s.get("max_diff", 0):
                    s["max_diff"] = diff_id
        if not found and deck_id > 0:
            status_list.append({"deck": deck_id, "max_diff": diff_id})
        rec["challenge_status_list"] = status_list

        rec["challenge_deck"] = 0
        rec["challenge_diff"] = 0
        rec["challenge_state"] = 0
        rec["challenge_battle_id"] = 0
        rec["save_data"] = {}
        rec["save_rollback"] = {}

        if info and isinstance(info, dict):
            use_items = info.get("use_item_id", []) or []
            enhance_items = info.get("enhance_id", []) or []
            weal_woe_items = info.get("weal_woe_id", []) or []

            card_list = rec.get("gather_card_list", [])
            for cid in use_items:
                if cid not in card_list:
                    card_list.append(cid)
            rec["gather_card_list"] = card_list

            enh_list = rec.get("gather_enhance_list", [])
            for eid in enhance_items:
                if eid not in enh_list:
                    enh_list.append(eid)
            rec["gather_enhance_list"] = enh_list

            ww_list = rec.get("gather_weal_woe_list", [])
            for wid in weal_woe_items:
                if wid not in ww_list:
                    ww_list.append(wid)
            rec["gather_weal_woe_list"] = ww_list

        self.save_rogue_card(db, uid, rec)
        return {"result": 0}

    def enter_rogue_card_game(self, ctx, uid, stage_id=0, deck=0, diff=0):
        """诡谈夜话·进入对局 (cs_89618 -> sc_89619)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        battle_id = int(time.time() * 1000) & 0x7FFFFFFF
        deck = int(deck or 0)
        diff = int(diff or 0)
        if deck > 0:
            rec["challenge_deck"] = deck
            rec["challenge_diff"] = diff
            rec["challenge_state"] = 1
            rec["challenge_battle_id"] = battle_id
        else:
            rec["thread_state"] = 1
            rec["battle_id"] = battle_id
        self.save_rogue_card(db, uid, rec)
        return {"result": 0, "battle_id": battle_id}

    def save_rogue_card_progress(self, ctx, uid, battle_id, battle_type=0, save_data=None, check_sum=None):
        """诡谈夜话·保存局内进度 (cs_89620 -> sc_89621)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        if save_data:
            rec["save_data"] = save_data
            self.save_rogue_card(db, uid, rec)
        return {"result": 0}

    def continue_rogue_card_progress(self, ctx, uid, battle_id=0, battle_type=0):
        """诡谈夜话·继续局内进度 (cs_89622 -> sc_89623)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        res = {"result": 0, "battle_id": int(battle_id or 0)}
        if rec.get("save_data"):
            res["save_data"] = rec["save_data"]
        if rec.get("save_rollback"):
            res["save_rollback"] = rec["save_rollback"]
        return res

    def save_rogue_card_rollback(self, ctx, uid, battle_id=0, battle_type=0):
        """诡谈夜话·保存回滚点存档 (cs_89624 -> sc_89625)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        if rec.get("save_data"):
            rec["save_rollback"] = dict(rec["save_data"])
            self.save_rogue_card(db, uid, rec)
        return {"result": 0, "battle_id": int(battle_id or 0), "battle_type": int(battle_type or 0)}

    def get_stage_save_data(self, ctx, uid, battle_id=0, battle_type=0, save_rollback=None):
        """诡谈夜话·恢复回滚关卡存档 (cs_89626 -> sc_89627)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        if save_rollback:
            rec["save_rollback"] = save_rollback
            self.save_rogue_card(db, uid, rec)
        return {"result": 0}

    def interrupt_rogue_card_post(self, ctx, uid, battle_id=0, battle_type=0):
        """诡谈夜话·中断对局暂存退出 (cs_89628 -> sc_89629)"""
        db = getattr(ctx, "db", self.db)
        rec = self.get_or_init_rogue_card(db, uid)
        res = {"result": 0, "battle_id": int(battle_id or 0)}
        if rec.get("save_data"):
            res["save_data"] = rec["save_data"]
        return res




# ==================== 协议算子实现 (Operations) ====================

# 1. 远村异闻 · 海拉弹珠算子
@operation
class PinballFinishOp(Operation):
    """海拉弹珠关卡结算：cs_89402 {stage_id, score, harm} -> sc_89403 {result: 0} + sc_89401。"""

    cmd = 89402
    sc = 89403

    def validate(self, data, ctx):
        if not data or "stage_id" not in data:
            raise OperationError(1, "缺少 stage_id")
        return data

    def apply(self, data, ctx):
        stage_id = int(data.get("stage_id", 0))
        score = int(data.get("score", 0))
        harm = int(data.get("harm", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_pinball_level(ctx, self.uid, stage_id, score, harm)
        ctx.log(
            f"cs_89402 -> 海拉弹珠关卡结算 stage={stage_id} score={score} harm={harm} "
            f"海拉等级={res['stage_count']} 通关数={len(res['cleared_stage_list'])}"
        )
        return res

    def respond(self, result, data, ctx):
        p89403 = ctx.codec_encode("sc_89403", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
        p89401 = MiniGameService.get_instance(ctx.db).build_89401_payload(ctx.db, self.uid)
        frames = [DownFrame(self.sc, p89403)]
        if p89401:
            frames.append(DownFrame(89401, p89401))
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class PinballEquipSkillOp(Operation):
    """海拉弹珠技能装配：cs_89404 {activity_id, skill_list} -> sc_89405 {result: 0} + sc_89401。"""

    cmd = 89404
    sc = 89405

    def validate(self, data, ctx):
        if not data or "activity_id" not in data:
            raise OperationError(1, "缺少 activity_id")
        return data

    def apply(self, data, ctx):
        aid = int(data.get("activity_id", 3840801))
        skill_list = data.get("skill_list", [])
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.equip_pinball_skills(ctx, self.uid, aid, skill_list)
        ctx.log(f"cs_89404 -> 海拉弹珠装配技能 activity={aid} skills={res['equip_skill_list']}")
        return res

    def respond(self, result, data, ctx):
        p89405 = ctx.codec_encode("sc_89405", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
        p89401 = MiniGameService.get_instance(ctx.db).build_89401_payload(ctx.db, self.uid)
        frames = [DownFrame(self.sc, p89405)]
        if p89401:
            frames.append(DownFrame(89401, p89401))
        return frames


# 2. 卡达斯假日赛 · 夏活坦克竞速算子
@operation
class SummerRaceCompleteOp(Operation):
    """坦克竞速关卡结算：cs_84332 {activity_id, stage_id, result, round, score, kill_num} -> sc_84333 {result: 0} + sc_84331。"""

    cmd = 84332
    sc = 84333

    def validate(self, data, ctx):
        if not data:
            raise OperationError(1, "请求数据为空")
        return data

    def apply(self, data, ctx):
        stage_id = int(data.get("stage_id", 0))
        result = int(data.get("result", 0))
        round_num = int(data.get("round") or 0)
        score = int(data.get("score") or 0)
        kill_num = int(data.get("kill_num") or 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_race_stage(ctx, self.uid, stage_id, result, round_num, score, kill_num)
        ctx.log(
            f"cs_84332 -> 坦克竞速结算 stage={stage_id} result={result} round={round_num} "
            f"score={score} 已通关数={len(res['mission_list'])}"
        )
        return res

    def respond(self, result, data, ctx):
        p84333 = ctx.codec_encode("sc_84333", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
        p84331 = MiniGameService.get_instance(ctx.db).build_84331_payload(ctx.db, self.uid)
        frames = [DownFrame(self.sc, p84333)]
        if p84331:
            frames.append(DownFrame(84331, p84331))
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SummerRaceModifyOp(Operation):
    """坦克战车改装：cs_84334 {activity_id, tank_id, weapon_list} -> sc_84335 {result: 0} + sc_84331。"""

    cmd = 84334
    sc = 84335

    def validate(self, data, ctx):
        if not data or "tank_id" not in data:
            raise OperationError(1, "缺少 tank_id")
        return data

    def apply(self, data, ctx):
        tank_id = int(data.get("tank_id", 0))
        weapon_list = data.get("weapon_list", [])
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.modify_tank_build(ctx, self.uid, tank_id, weapon_list)
        ctx.log(f"cs_84334 -> 坦克改装 tank={tank_id} weapons={weapon_list}")
        return res

    def respond(self, result, data, ctx):
        p84335 = ctx.codec_encode("sc_84335", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
        p84331 = MiniGameService.get_instance(ctx.db).build_84331_payload(ctx.db, self.uid)
        frames = [DownFrame(self.sc, p84335)]
        if p84331:
            frames.append(DownFrame(84331, p84331))
        return frames


# 3. 通用常驻积分领奖算子
@operation
class ActivityPointRewardOp(Operation):
    """活动积分奖励领取：cs_60054 {point_reward_id_list} -> sc_60055 {result: 0, reward_list} + sc_60107。"""

    cmd = 60054
    sc = 60055

    def validate(self, data, ctx):
        if not data:
            raise OperationError(1, "请求数据为空")
        return data

    def apply(self, data, ctx):
        reward_ids = data.get("point_reward_id_list", [])
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.claim_point_rewards(ctx, self.uid, reward_ids)
        ctx.log(f"cs_60054 -> 积分奖励领取 ids={reward_ids} 实领={len(res['claimed_ids'])} 道具数={len(res['reward_list'])}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        rewards = result.get("reward_list", [])
        p60055 = ctx.codec_encode("sc_60055", {"result": 0, "reward_list": rewards}) if ctx.codec_encode else encode("sc_60055", {"result": 0, "reward_list": rewards})
        p60107 = MiniGameService.get_instance(ctx.db).build_60107_payload(ctx.db, self.uid)
        frames = [DownFrame(self.sc, p60055)]
        if p60107:
            frames.append(DownFrame(60107, p60107))
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


# 4. 溯梦之隙 · 斯克尔德心象算子
@operation
class SkuldSendMarkOp(Operation):
    """斯克尔德心象标记记录：cs_24052 {activity_id, key} -> sc_24053 {result: 0}"""
    cmd = 24052
    sc = 24053

    def validate(self, data, ctx):
        if not data or "key" not in data:
            raise OperationError(1, "缺少 key")
        return data

    def apply(self, data, ctx):
        key = int(data.get("key", 0))
        act_id = int(data.get("activity_id") or 321201)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.save_skuld_mark(ctx, self.uid, key, activity_id=act_id)
        ctx.log(f"cs_24052 -> 斯克尔德标记 key={key} act={act_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p24053 = ctx.codec_encode("sc_24053", {"result": 0}) if ctx.codec_encode else encode("sc_24053", {"result": 0})
        frames = [DownFrame(self.sc, p24053)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SkuldPuzzleCompleteOp(Operation):
    """斯克尔德心象解谜完成：cs_24054 {activity_id, id} -> sc_24055 {result: 0, reward_list}"""
    cmd = 24054
    sc = 24055

    def validate(self, data, ctx):
        if not data or "id" not in data:
            raise OperationError(1, "缺少 id")
        return data

    def apply(self, data, ctx):
        level_id = int(data.get("id", 0))
        act_id = int(data.get("activity_id") or 321201)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_skuld_puzzle(ctx, self.uid, level_id, activity_id=act_id)
        ctx.log(f"cs_24054 -> 斯克尔德解谜完成 id={level_id} 奖励数={len(res.get('reward_list', []))}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        rewards = result.get("reward_list", [])
        p24055 = ctx.codec_encode("sc_24055", {"result": 0, "reward_list": rewards}) if ctx.codec_encode else encode("sc_24055", {"result": 0, "reward_list": rewards})
        frames = [DownFrame(self.sc, p24055)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


# 5. 谧光叙录 · 霍德尔活动算子
@operation
class HodurResetChapterOp(Operation):
    """霍德尔重置章节：cs_89422 {activity_id, chapter_id} -> sc_89423 {result: 0}"""
    cmd = 89422
    sc = 89423

    def validate(self, data, ctx):
        if not data or "chapter_id" not in data:
            raise OperationError(1, "缺少 chapter_id")
        return data

    def apply(self, data, ctx):
        chap_id = int(data.get("chapter_id", 1))
        act_id = int(data.get("activity_id") or 3941101)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.reset_hodur_chapter(ctx, self.uid, chap_id, activity_id=act_id)
        ctx.log(f"cs_89422 -> 霍德尔重置章节 chap={chap_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89423 = ctx.codec_encode("sc_89423", {"result": 0}) if ctx.codec_encode else encode("sc_89423", {"result": 0})
        frames = [DownFrame(self.sc, p89423)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class HodurSettleChallengeOp(Operation):
    """霍德尔结算挑战：cs_89424 {activity_id} -> sc_89425 {result: 0}"""
    cmd = 89424
    sc = 89425

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 3941101)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_hodur_challenge(ctx, self.uid, activity_id=act_id)
        ctx.log(f"cs_89424 -> 霍德尔结算挑战")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89425 = ctx.codec_encode("sc_89425", {"result": 0}) if ctx.codec_encode else encode("sc_89425", {"result": 0})
        frames = [DownFrame(self.sc, p89425)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class HodurSelectEventOp(Operation):
    """霍德尔选择事件：cs_89426 {activity_id, chapter_id, option_id} -> sc_89427 {round, result: 0}"""
    cmd = 89426
    sc = 89427

    def validate(self, data, ctx):
        if not data or "option_id" not in data:
            raise OperationError(1, "缺少 option_id")
        return data

    def apply(self, data, ctx):
        chap_id = int(data.get("chapter_id", 1))
        opt_id = int(data.get("option_id", 0))
        act_id = int(data.get("activity_id") or 3941101)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.select_hodur_event(ctx, self.uid, chap_id, opt_id, activity_id=act_id)
        ctx.log(f"cs_89426 -> 霍德尔选择事件 chap={chap_id} opt={opt_id} round={res.get('round')}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        rnd = int(result.get("round", 1))
        p89427 = ctx.codec_encode("sc_89427", {"result": 0, "round": rnd}) if ctx.codec_encode else encode("sc_89427", {"result": 0, "round": rnd})
        frames = [DownFrame(self.sc, p89427)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


# 6. 潜质觉醒 · 现时无返之途·薇儿丹蒂 SP 挑战算子
@operation
class SPHeroConfirmScheduleOp(Operation):
    """SP 英雄选定日程：cs_83003 {schedule_info_list, activity_id} -> sc_83004 {result: 0}"""
    cmd = 83003
    sc = 83004

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        schedules = data.get("schedule_info_list", [])
        act_id = int(data.get("activity_id") or SP_HERO_ACTIVITY_ID)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.confirm_sphero_schedule(ctx, self.uid, schedules, activity_id=act_id)
        ctx.log(f"cs_83003 -> SP 英雄选定日程 count={len(schedules)}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p83004 = ctx.codec_encode("sc_83004", {"result": 0}) if ctx.codec_encode else encode("sc_83004", {"result": 0})
        frames = [DownFrame(self.sc, p83004)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroGetScheduleAwardOp(Operation):
    """SP 英雄领取日程奖励：cs_83005 {activity_id} -> sc_83006 {result: 0, reward_list}"""
    cmd = 83005
    sc = 83006

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        rewards = [{"id": 1, "num": 50}, {"id": 53100, "num": 80}]
        ctx.log("cs_83005 -> SP 英雄领取日程积分奖励")
        return {"result": 0, "reward_list": rewards}

    def respond(self, result, data, ctx):
        from codec import encode
        rewards = result.get("reward_list", [])
        p83006 = ctx.codec_encode("sc_83006", {"result": 0, "reward_list": rewards}) if ctx.codec_encode else encode("sc_83006", {"result": 0, "reward_list": rewards})
        frames = [DownFrame(self.sc, p83006)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroRefreshEntrustListOp(Operation):
    """SP 英雄刷新待派遣委托：cs_83007 {activity_id} -> sc_83008 {result: 0, entrust_id_list}"""
    cmd = 83007
    sc = 83008

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        candidates = [2428401, 2428402, 2428404, 2428406, 2428407]
        ctx.log(f"cs_83007 -> SP 英雄刷新待派遣委托: {candidates}")
        return {"result": 0, "entrust_id_list": candidates}

    def respond(self, result, data, ctx):
        from codec import encode
        cands = result.get("entrust_id_list", [])
        p83008 = ctx.codec_encode("sc_83008", {"result": 0, "entrust_id_list": cands}) if ctx.codec_encode else encode("sc_83008", {"result": 0, "entrust_id_list": cands})
        frames = [DownFrame(self.sc, p83008)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroDispatchEntrustOp(Operation):
    """SP 英雄派遣委托：cs_83009 {index, entrust_id, activity_id} -> sc_83010 {result: 0, begin_entrust_list}"""
    cmd = 83009
    sc = 83010

    def validate(self, data, ctx):
        if not data or "index" not in data or "entrust_id" not in data:
            raise OperationError(1, "缺少 index 或 entrust_id")
        return data

    def apply(self, data, ctx):
        idx = int(data.get("index", 1))
        eid = int(data.get("entrust_id", 2428401))
        act_id = int(data.get("activity_id") or SP_HERO_ACTIVITY_ID)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.dispatch_sphero_entrust(ctx, self.uid, idx, eid, activity_id=act_id)
        ctx.log(f"cs_83009 -> SP 英雄派遣委托 pos={idx} entrust={eid}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        active = result.get("begin_entrust_list", [])
        p83010 = ctx.codec_encode("sc_83010", {"result": 0, "begin_entrust_list": active}) if ctx.codec_encode else encode("sc_83010", {"result": 0, "begin_entrust_list": active})
        frames = [DownFrame(self.sc, p83010)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroCancelEntrustOp(Operation):
    """SP 英雄取消委托：cs_83011 {index, activity_id} -> sc_83012 {result: 0, begin_entrust_list}"""
    cmd = 83011
    sc = 83012

    def validate(self, data, ctx):
        if not data or "index" not in data:
            raise OperationError(1, "缺少 index")
        return data

    def apply(self, data, ctx):
        idx = int(data.get("index", 1))
        act_id = int(data.get("activity_id") or SP_HERO_ACTIVITY_ID)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.cancel_sphero_entrust(ctx, self.uid, idx, activity_id=act_id)
        ctx.log(f"cs_83011 -> SP 英雄取消委托 pos={idx}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        active = result.get("begin_entrust_list", [])
        p83012 = ctx.codec_encode("sc_83012", {"result": 0, "begin_entrust_list": active}) if ctx.codec_encode else encode("sc_83012", {"result": 0, "begin_entrust_list": active})
        frames = [DownFrame(self.sc, p83012)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroGetEntrustAwardOp(Operation):
    """SP 英雄领取委托奖励：cs_83013 {index_list, activity_id} -> sc_83014 {result: 0, reward_list, begin_entrust_list}"""
    cmd = 83013
    sc = 83014

    def validate(self, data, ctx):
        if not data or "index_list" not in data:
            raise OperationError(1, "缺少 index_list")
        return data

    def apply(self, data, ctx):
        idx_list = data.get("index_list", [])
        act_id = int(data.get("activity_id") or SP_HERO_ACTIVITY_ID)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.get_sphero_entrust_award(ctx, self.uid, idx_list, activity_id=act_id)
        ctx.log(f"cs_83013 -> SP 英雄领取委托奖励 indices={idx_list}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        rewards = result.get("reward_list", [])
        active = result.get("begin_entrust_list", [])
        p83014 = ctx.codec_encode("sc_83014", {"result": 0, "reward_list": rewards, "begin_entrust_list": active}) if ctx.codec_encode else encode("sc_83014", {"result": 0, "reward_list": rewards, "begin_entrust_list": active})
        frames = [DownFrame(self.sc, p83014)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroUseAcceleratorOp(Operation):
    """SP 英雄使用委托加速卡：cs_83018 {activity_id, index, use_cnt} -> sc_83019 {result: 0, begin_entrust_list}"""
    cmd = 83018
    sc = 83019

    def validate(self, data, ctx):
        if not data or "index" not in data:
            raise OperationError(1, "缺少 index")
        return data

    def apply(self, data, ctx):
        idx = int(data.get("index", 1))
        cnt = int(data.get("use_cnt", 1))
        act_id = int(data.get("activity_id") or SP_HERO_ACTIVITY_ID)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.use_sphero_accelerator(ctx, self.uid, idx, use_cnt=cnt, activity_id=act_id)
        ctx.log(f"cs_83018 -> SP 英雄使用委托加速卡 pos={idx} count={cnt}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        active = result.get("begin_entrust_list", [])
        p83019 = ctx.codec_encode("sc_83019", {"result": 0, "begin_entrust_list": active}) if ctx.codec_encode else encode("sc_83019", {"result": 0, "begin_entrust_list": active})
        frames = [DownFrame(self.sc, p83019)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroSettleBarbecueOp(Operation):
    """SP 英雄烤肉小游戏结算：cs_83022 {stage_id, grade_id, hero_id, result} -> sc_83023 {result: 0, reward_list, reward_recode, ticket_recode}"""
    cmd = 83022
    sc = 83023

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        stage_id = int(data.get("stage_id", 0))
        grade_id = int(data.get("grade_id", 0))
        hero_id = int(data.get("hero_id", 0))
        res_val = int(data.get("result", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_sphero_barbecue(ctx, self.uid, stage_id, grade_id, hero_id, res_val)
        ctx.log(f"cs_83022 -> SP 英雄烤肉小游戏结算 stage={stage_id} grade={grade_id} res={res_val}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        rewards = result.get("reward_list", [])
        r_rec = int(result.get("reward_recode", 1))
        t_rec = int(result.get("ticket_recode", 0))
        p83023 = ctx.codec_encode("sc_83023", {
            "result": 0,
            "reward_list": rewards,
            "reward_recode": r_rec,
            "ticket_recode": t_rec
        }) if ctx.codec_encode else encode("sc_83023", {
            "result": 0,
            "reward_list": rewards,
            "reward_recode": r_rec,
            "ticket_recode": t_rec
        })
        frames = [DownFrame(self.sc, p83023)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SPHeroFreeRefreshEntrustOp(Operation):
    """SP 英雄免费刷新委托：cs_83040 {activity_id} -> sc_83041 {result: 0, begin_entrust_list}"""
    cmd = 83040
    sc = 83041

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or SP_HERO_ACTIVITY_ID)
        svc = MiniGameService.get_instance(ctx.db)
        active = svc._get_sphero_active_entrusts(getattr(ctx, "db", svc.db), self.uid)
        ctx.log("cs_83040 -> SP 英雄免费刷新委托")
        return {"result": 0, "begin_entrust_list": active}

    def respond(self, result, data, ctx):
        from codec import encode
        active = result.get("begin_entrust_list", [])
        p83041 = ctx.codec_encode("sc_83041", {"result": 0, "begin_entrust_list": active}) if ctx.codec_encode else encode("sc_83041", {"result": 0, "begin_entrust_list": active})
        frames = [DownFrame(self.sc, p83041)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


# ==================== 7. 极限机装 · 乌尔碰碰车算子 (Operations) ====================

@operation
class VehicleBallPassStageOp(Operation):
    """乌尔碰碰车关卡结算：cs_89702 {activity_id, stage_id, kv_list, buffs, is_pass} -> sc_89703 {result: 0}"""
    cmd = 89702
    sc = 89703

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or VEHICLE_BALL_ACTIVITY_ID)
        stage_id = int(data.get("stage_id") or 0)
        is_pass = int(data.get("is_pass") or 0)
        kv_list = data.get("kv_list", [])
        buffs = data.get("buffs", [])
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.pass_vehicle_stage(ctx, self.uid, stage_id, is_pass, kv_list=kv_list, buffs=buffs, activity_id=act_id)
        ctx.log(f"cs_89702 -> 乌尔碰碰车关卡结算: stage={stage_id}, is_pass={is_pass}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89703 = ctx.codec_encode("sc_89703", result) if ctx.codec_encode else encode("sc_89703", result)
        frames = [DownFrame(self.sc, p89703)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class VehicleBallSetVehicleOp(Operation):
    """乌尔碰碰车出战载具设定：cs_89704 {activity_id, vehicle} -> sc_89705 {result: 0}"""
    cmd = 89704
    sc = 89705

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or VEHICLE_BALL_ACTIVITY_ID)
        vehicle = int(data.get("vehicle") or 50111)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.set_vehicle(ctx, self.uid, vehicle, activity_id=act_id)
        ctx.log(f"cs_89704 -> 乌尔碰碰车切换载具: vehicle={vehicle}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89705 = ctx.codec_encode("sc_89705", result) if ctx.codec_encode else encode("sc_89705", result)
        frames = [DownFrame(self.sc, p89705)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class VehicleBallGetIllustrateRewardOp(Operation):
    """乌尔碰碰车图鉴领奖：cs_89706 {activity_id, buff_id} -> sc_89707 {result: 0, reward_list}"""
    cmd = 89706
    sc = 89707

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or VEHICLE_BALL_ACTIVITY_ID)
        buff_id = int(data.get("buff_id") or 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.get_vehicle_illustrate_reward(ctx, self.uid, buff_id, activity_id=act_id)
        ctx.log(f"cs_89706 -> 乌尔碰碰车图鉴领奖: buff_id={buff_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89707 = ctx.codec_encode("sc_89707", result) if ctx.codec_encode else encode("sc_89707", result)
        frames = [DownFrame(self.sc, p89707)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class VehicleBallUnlockBuffOp(Operation):
    """乌尔碰碰车解锁 Buff：cs_89708 {activity_id, buff_id} -> sc_89709 {result: 0}"""
    cmd = 89708
    sc = 89709

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or VEHICLE_BALL_ACTIVITY_ID)
        buff_id = int(data.get("buff_id") or 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.unlock_vehicle_buff(ctx, self.uid, buff_id, activity_id=act_id)
        ctx.log(f"cs_89708 -> 乌尔碰碰车解锁 Buff: buff_id={buff_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89709 = ctx.codec_encode("sc_89709", result) if ctx.codec_encode else encode("sc_89709", result)
        frames = [DownFrame(self.sc, p89709)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames
# 9. 浮光绎曲与鸣律探微算子
@operation
class MusicQueryCompeletOp(Operation):
    """浮光绎曲乐曲挑战结算：cs_61048 {id, score, other_data, state} -> sc_61049 {result: 0}"""
    cmd = 61048
    sc = 61049

    def validate(self, data, ctx):
        if not data or "id" not in data:
            raise OperationError(1, "缺少乐曲 id")
        return data

    def apply(self, data, ctx):
        music_id = int(data.get("id", 0))
        score = int(data.get("score", 0))
        state = int(data.get("state", 0))
        other_data = data.get("other_data") or []
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_music_stage(ctx, self.uid, music_id, score, state, other_data)
        ctx.log(f"cs_61048 -> 浮光绎曲结算: music_id={music_id} score={score} state={state}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p61049 = ctx.codec_encode("sc_61049", result) if ctx.codec_encode else encode("sc_61049", result)
        frames = [DownFrame(self.sc, p61049)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class MusicQueryRewardOp(Operation):
    """浮光绎曲乐曲奖励领取：cs_61050 {id_list} -> sc_61051 {result: 0, reward_list}"""
    cmd = 61050
    sc = 61051

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        id_list = data.get("id_list") or []
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.claim_music_rewards(ctx, self.uid, id_list)
        ctx.log(f"cs_61050 -> 浮光绎曲领奖: id_list={id_list}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p61051 = ctx.codec_encode("sc_61051", result) if ctx.codec_encode else encode("sc_61051", result)
        frames = [DownFrame(self.sc, p61051)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RhythmPlayStoryOp(Operation):
    """鸣律探微剧情阅览：cs_83126 {activity_id, story_id} -> sc_83127 {result: 0}"""
    cmd = 83126
    sc = 83127

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        activity_id = int(data.get("activity_id", 0))
        story_id = int(data.get("story_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.play_rhythm_story(ctx, self.uid, activity_id, story_id)
        ctx.log(f"cs_83126 -> 鸣律探微剧情已读: story_id={story_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p83127 = ctx.codec_encode("sc_83127", result) if ctx.codec_encode else encode("sc_83127", result)
        frames = [DownFrame(self.sc, p83127)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


# ==================== 10. 食与异世界 · 夏日餐厅算子 (Activity 3539101) ====================

@operation
class SummerPubPinBallOp(Operation):
    """夏日餐厅·弹珠台关卡结算：cs_89002 -> sc_89003 {result: 0}"""
    cmd = 89002
    sc = 89003

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        stage_id = int(data.get("stage_id", 0))
        sub_stage_id = int(data.get("sub_stage_id", 0))
        illustrated = data.get("illustrated") or []
        result = int(data.get("result", 1))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.finish_summer_pub_pinball(ctx, self.uid, stage_id, sub_stage_id, illustrated, result)
        ctx.log(f"cs_89002 -> 夏日餐厅弹珠结算 stage_id={stage_id} sub_stage_id={sub_stage_id} result={result}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89003", result) if ctx.codec_encode else encode("sc_89003", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SummerPubViewIlluOp(Operation):
    """夏日餐厅·查看图鉴：cs_89004 -> sc_89005 {result: 0}"""
    cmd = 89004
    sc = 89005

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        ill_id = int(data.get("ill_id") or data.get("id") or 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.view_summer_pub_illustration(ctx, self.uid, ill_id)
        ctx.log(f"cs_89004 -> 夏日餐厅查看图鉴 ill_id={ill_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89005", result) if ctx.codec_encode else encode("sc_89005", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SummerPubCookOp(Operation):
    """夏日餐厅·料理烹饪：cs_89006 -> sc_89007 {result: 0}"""
    cmd = 89006
    sc = 89007

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        stage_id = int(data.get("stage_id", 0))
        state = int(data.get("stage", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.cook_summer_pub(ctx, self.uid, stage_id, state)
        ctx.log(f"cs_89006 -> 夏日餐厅料理烹饪 stage_id={stage_id} state={state}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89007", result) if ctx.codec_encode else encode("sc_89007", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class SummerPubTimeStateOp(Operation):
    """夏日餐厅·昼夜状态切换：cs_89008 -> sc_89009 {result: 0}"""
    cmd = 89008
    sc = 89009

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        time_state = data.get("time_state")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.change_summer_pub_time_state(ctx, self.uid, time_state)
        ctx.log(f"cs_89008 -> 夏日餐厅昼夜切换 time_state={time_state}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89009", result) if ctx.codec_encode else encode("sc_89009", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@bus.subscribe(Events.STAGE_PASS)
def _on_stage_pass_for_summer_pub(ctx, uid, stage_id=0, **kwargs):
    """监听通关事件，自动记录夏日餐厅主线战斗关卡进度"""
    sid = int(stage_id or kwargs.get("dest_int", 0))
    if sid not in SUMMER_PUB_BATTLE_TO_LEVEL:
        return
    level_id = SUMMER_PUB_BATTLE_TO_LEVEL[sid]
    db = getattr(ctx, "db", None)
    svc = MiniGameService.get_instance(db)
    rec = svc.get_or_init_summer_pub(db, uid)
    battle_stages = rec.get("battle_stages", [])
    if level_id not in battle_stages:
        battle_stages.append(level_id)
        rec["battle_stages"] = battle_stages
        svc.save_summer_pub(db, uid, rec)
    bus.emit(
        Events.SUMMER_PUB_LEVEL_PASS,
        ctx,
        uid,
        level_id=level_id,
        stage_id=sid
    )


# ==================== 诡谈夜话 (Activity 4142701) 协议算子 ====================

@operation
class RogueCardStartPostOp(Operation):
    """诡谈夜话·开帖：cs_89602 {thread_id} -> sc_89603 {result: 0}"""
    cmd = 89602
    sc = 89603

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.start_rogue_card_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89602 -> 诡谈夜话开帖 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89603", result) if ctx.codec_encode else encode("sc_89603", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardCompleteStoryOp(Operation):
    """诡谈夜话·完成剧情：cs_89604 {thread_id, story_id, story_id_index} -> sc_89605 {result: 0}"""
    cmd = 89604
    sc = 89605

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        story_id = int(data.get("story_id", 0))
        story_id_index = int(data.get("story_id_index", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.complete_rogue_card_story(ctx, self.uid, thread_id, story_id, story_id_index)
        ctx.log(f"cs_89604 -> 诡谈夜话剧情推进 thread_id={thread_id} story_id={story_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89605", result) if ctx.codec_encode else encode("sc_89605", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardClickLikePostOp(Operation):
    """诡谈夜话·论坛点赞：cs_89606 {thread_id} -> sc_89607 {result: 0}"""
    cmd = 89606
    sc = 89607

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.click_like_rogue_card_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89606 -> 诡谈夜话论坛点赞 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89607", result) if ctx.codec_encode else encode("sc_89607", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardRebackPostOp(Operation):
    """诡谈夜话·重开/回退帖子：cs_89608 {thread_id} -> sc_89609 {result: 0}"""
    cmd = 89608
    sc = 89609

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.reback_rogue_card_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89608 -> 诡谈夜话重开帖子 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89609", result) if ctx.codec_encode else encode("sc_89609", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardViewThreadPostOp(Operation):
    """诡谈夜话·论坛阅览：cs_89610 {thread_id} -> sc_89611 {result: 0}"""
    cmd = 89610
    sc = 89611

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.view_rogue_card_thread_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89610 -> 诡谈夜话论坛阅览 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89611", result) if ctx.codec_encode else encode("sc_89611", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardCompletePostOp(Operation):
    """诡谈夜话·主线帖子对局结算：cs_89612 {thread_id, joker_result, info} -> sc_89613 {result: 0} + sc_28007(如有)"""
    cmd = 89612
    sc = 89613

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        joker_result = int(data.get("joker_result", 0))
        info = data.get("info")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.complete_rogue_card_post(ctx, self.uid, thread_id, joker_result, info)
        ctx.log(f"cs_89612 -> 诡谈夜话主线结算 thread_id={thread_id} win={joker_result==1}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89613", result) if ctx.codec_encode else encode("sc_89613", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardChallengeSettleOp(Operation):
    """诡谈夜话·挑战模式结算：cs_89616 {deck, diff, info} -> sc_89617 {result: 0}"""
    cmd = 89616
    sc = 89617

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        deck = int(data.get("deck", 0))
        diff = int(data.get("diff", 0))
        info = data.get("info")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_rogue_card_challenge(ctx, self.uid, deck, diff, info)
        ctx.log(f"cs_89616 -> 诡谈夜话挑战结算 deck={deck} diff={diff}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89617", result) if ctx.codec_encode else encode("sc_89617", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardEnterGameOp(Operation):
    """诡谈夜话·进入对局：cs_89618 {stage_id, deck, diff} -> sc_89619 {result: 0, battle_id}"""
    cmd = 89618
    sc = 89619

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        stage_id = int(data.get("stage_id", 0))
        deck = int(data.get("deck", 0))
        diff = int(data.get("diff", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.enter_rogue_card_game(ctx, self.uid, stage_id, deck, diff)
        ctx.log(f"cs_89618 -> 诡谈夜话进入对局 stage_id={stage_id} deck={deck} diff={diff} battle_id={res.get('battle_id')}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89619", result) if ctx.codec_encode else encode("sc_89619", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardSaveProgressPostOp(Operation):
    """诡谈夜话·保存局内进度：cs_89620 {battle_id, battle_type, save_data, check_sum} -> sc_89621 {result: 0}"""
    cmd = 89620
    sc = 89621

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        save_data = data.get("save_data")
        check_sum = data.get("check_sum")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.save_rogue_card_progress(ctx, self.uid, battle_id, battle_type, save_data, check_sum)
        ctx.log(f"cs_89620 -> 诡谈夜话保存局内进度 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89621", result) if ctx.codec_encode else encode("sc_89621", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardContinueProgressPostOp(Operation):
    """诡谈夜话·继续局内进度：cs_89622 {battle_id, battle_type} -> sc_89623 {result: 0, battle_id, save_data, save_rollback}"""
    cmd = 89622
    sc = 89623

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.continue_rogue_card_progress(ctx, self.uid, battle_id, battle_type)
        ctx.log(f"cs_89622 -> 诡谈夜话继续局内进度 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89623", result) if ctx.codec_encode else encode("sc_89623", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardSaveRollBackPostOp(Operation):
    """诡谈夜话·保存回滚点：cs_89624 {battle_id, battle_type} -> sc_89625 {result: 0, battle_id, battle_type, settle_info}"""
    cmd = 89624
    sc = 89625

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.save_rogue_card_rollback(ctx, self.uid, battle_id, battle_type)
        ctx.log(f"cs_89624 -> 诡谈夜话保存回滚点 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89625", result) if ctx.codec_encode else encode("sc_89625", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardGetStageSaveDataOp(Operation):
    """诡谈夜话·恢复回滚关卡：cs_89626 {battle_id, battle_type, save_rollback} -> sc_89627 {result: 0}"""
    cmd = 89626
    sc = 89627

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        save_rollback = data.get("save_rollback")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.get_stage_save_data(ctx, self.uid, battle_id, battle_type, save_rollback)
        ctx.log(f"cs_89626 -> 诡谈夜话恢复回滚关卡 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89627", result) if ctx.codec_encode else encode("sc_89627", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardInterruptPostOp(Operation):
    """诡谈夜话·中断对局：cs_89628 {battle_id, battle_type} -> sc_89629 {result: 0, battle_id, save_data}"""
    cmd = 89628
    sc = 89629

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.interrupt_rogue_card_post(ctx, self.uid, battle_id, battle_type)
        ctx.log(f"cs_89628 -> 诡谈夜话中断对局 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89629", result) if ctx.codec_encode else encode("sc_89629", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


