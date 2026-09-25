# -*- coding: utf-8 -*-
"""
autochess_service.py — 决斗王（道馆对弈自走棋，Activity 3740601 / 4041301）核心服务与状态机引擎

功能职责：
1. 静态配置元数据管理：加载并缓存 autochess_cfg.json（关卡、大区、棋子、道具、徽章、敌方阵容）。
2. 数据库落库与持久化：
   - autochess_stage: PVE关卡进度与通关时间戳；
   - autochess_medal: 道馆徽章解锁与升级；
   - autochess_card & autochess_meta: 实物卡牌收藏与天梯积分元数据；
   - autochess_session: 局内状态恢复与断点存档。
3. 登录洪流帧动态无损生成：
   - sc_89201 (关卡与徽章): 100% 逐字节对齐抓包存档；
   - sc_90051 (卡牌收藏): 100% 逐字节对齐抓包存档；
   - sc_89125 (游戏状态), sc_90007 (重连检查), sc_89207 (历史战绩).
4. PVE 单机对弈状态机（89xxx 协议族）：
   - 开局选关(89116->89117)；
   - 商店发牌与金币扣减(89118->89119)；
   - 棋子购买、出售、布阵、三星自动合成(89102/89122/89104/89130)；
   - 回合战斗生成(89108->89109，下发关卡敌人阵容回放包，客户端本地模拟演播)；
   - 关卡胜利结算(89126->89127，发奖落库 + EventBus STAGE_PASS 广播)。
5. PVP 模式截断与安全阻断（90xxx 协议族）：
   - cs_90002 匹配请求直接返回 sc_90003 { result: 608020 } (AUTO_CHESS_2_CLOSED，弹出 Toast 拦截)；
   - 其余联机协议静默回执兜底，杜绝客户端卡死转圈。
"""

import os
import json
import time
import random
import logging
from typing import Dict, Any, List, Optional

from core import Operation, OperationError, operation
from middleware import DownFrame
from event_bus import bus, Events
from codec import encode, decode

logger = logging.getLogger("autochess_service")

# 配置文件路径
CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autochess_cfg.json")


class AutoChessConst:
    GAME_TYPE_PVE = 0
    GAME_TYPE_PVP = 1
    GAME_TYPE_ONLINE = 2
    GAME_TYPE_PVP_5_0 = 3

    STATE_NONE = 0
    STATE_PREPARE = 1
    STATE_PREPARE_END = 2
    STATE_ROUND_SETTLE = 3
    STATE_TOTAL_SETTLE_WIN = 4
    STATE_TOTAL_SETTLE_LOSE = 5
    STATE_REPLAY = 6

    SHOP_TYPE_NORMAL = 0
    SHOP_TYPE_PROP = 1
    SHOP_TYPE_REWARD = 2

    # 战斗动作类型 (对齐 AutoChessConst.ACTION_TYPE)
    ACTION_TYPE_MOVE = 1
    ACTION_TYPE_FIGHT = 2
    ACTION_TYPE_EFFECT = 3
    ACTION_TYPE_DEAD = 5

    # 动作效果类型 (对齐 AutoChessConst.ACTION_EFFECT_TYPE)
    ACTION_EFFECT_TYPE_SUMMON = 3

    # 属性 Key (对齐 AutoChessConst.ATTR_KEY)
    KEY_NOW_MONEY = 1
    KEY_RESTART_MONEY = 2
    KEY_MONEY_INCRISE_CEIL = 3
    KEY_PREPARE_CELL_COUNT = 4
    KEY_BATTLE_CELL_COUNT = 5
    KEY_CHESS_GOOD_COUNT = 6
    KEY_PROP_GOOD_COUNT = 7
    KEY_REWARD_GOOD_COUNT = 8
    KEY_SHOP_LEVEL_UP_ROUND = 9
    KEY_REFRESH_SHOP_COST = 10
    KEY_SHOP_STAR_NUM = 11
    KEY_SHOP_CHESS_BASE_EXP = 12
    KEY_SHOP_FREE_REFRESH_COUNT = 16
    KEY_SHOP_FREE_PROP_COUNT = 17
    KEY_SHOP_FREE_CHESS_COUNT = 18

    # 棋盘 Key (对齐 AutoChessConst.USER_INFO_KEY)
    INFO_KEY_HP = 1
    INFO_KEY_VICTORY_ROUND_COUNT = 2
    INFO_KEY_CUR_ROUND_COUNT = 4
    INFO_KEY_STAGE_ID = 5
    INFO_KEY_BRAHMA_BOSS_FLAG = 6
    INFO_KEY_BATTLE_UID = 10
    INFO_KEY_SUNGLASS_FLAG = 11

    # 棋子属性 Key (对齐 AutoChessConst.CHESS_ATTRI_KEY)
    CHESS_ATTR_ATK = 1
    CHESS_ATTR_HP = 2
    CHESS_ATTR_EXP = 3

    # 活动与错误码
    ACTIVITY_AUTO_CHESS_MAIN = 3740601
    ACTIVITY_AUTO_CHESS_TASK = 3700401
    ACTIVITY_AUTO_CHESS_4_8 = 4041301
    TIP_CLOSED = 608020  # AUTO_CHESS_2_CLOSED
    TIP_INVALID_OP = 2
    REFRESH_SHOP_COST = 1
    PHASE1_MAX_AREA = 3


class AutoChessService:
    _instance: Optional["AutoChessService"] = None

    def __init__(self, db=None):
        self.db = db
        self.cfg: Dict[str, Any] = {}
        self._load_cfg()
        self._sessions: Dict[int, Dict[str, Any]] = {}
        self._next_uid = 100000

    @classmethod
    def get_instance(cls, db=None) -> "AutoChessService":
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    def _load_cfg(self):
        if os.path.exists(CFG_PATH):
            try:
                with open(CFG_PATH, "r", encoding="utf-8") as f:
                    self.cfg = json.load(f)
                logger.info(f"Loaded autochess_cfg.json successfully (stages={len(self.cfg.get('stages', {}))})")
            except Exception as e:
                logger.error(f"Failed to load autochess_cfg.json: {e}")
                self.cfg = {}
        else:
            logger.warning(f"autochess_cfg.json not found at {CFG_PATH}")
            self.cfg = {}

    def get_stage_cfg(self, stage_id: int) -> Optional[Dict[str, Any]]:
        return self.cfg.get("stages", {}).get(str(stage_id))

    def get_chess_cfg(self, chess_id: int) -> Optional[Dict[str, Any]]:
        return self.cfg.get("chess", {}).get(str(chess_id))

    def get_item_cfg(self, item_id: int) -> Optional[Dict[str, Any]]:
        return self.cfg.get("items", {}).get(str(item_id))

    def get_enemy_team_cfg(self, team_id: int) -> Optional[Dict[str, Any]]:
        return self.cfg.get("enemy_teams", {}).get(str(team_id))

    def get_buff_cfg(self, buff_id: int) -> Optional[Dict[str, Any]]:
        return self.cfg.get("buffs", {}).get(str(buff_id))

    def get_enemy_teams_for_stage(self, stage_id: int) -> List[int]:
        stage = self.get_stage_cfg(stage_id)
        if not stage:
            return []
        gid = str(stage.get("group_id", 0))
        return self.cfg.get("stage_group_map", {}).get(gid, [])

    def _require_phase1_game_type(self, game_type: int):
        if int(game_type) != AutoChessConst.GAME_TYPE_PVE:
            raise OperationError(AutoChessConst.TIP_CLOSED, "当前仅开放一期 PVE 自走棋")

    def _require_phase1_stage(self, stage_id: int) -> Dict[str, Any]:
        stage = self.get_stage_cfg(stage_id)
        if not stage:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, f"未知的一期 PVE 关卡: {stage_id}")
        if int(stage.get("area", 0)) > AutoChessConst.PHASE1_MAX_AREA:
            raise OperationError(AutoChessConst.TIP_CLOSED, "二期 PVE 教学与后续区域暂未开放")
        return stage

    def _require_session_state(self, uid: int, game_type: int, allowed_states) -> Dict[str, Any]:
        self._require_phase1_game_type(game_type)
        session = self.get_session(uid)
        if int(session.get("game_type", AutoChessConst.GAME_TYPE_PVE)) != AutoChessConst.GAME_TYPE_PVE:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "当前会话不是一期 PVE 对局")
        if session.get("state", AutoChessConst.STATE_NONE) not in set(allowed_states):
            raise OperationError(
                AutoChessConst.TIP_INVALID_OP,
                f"当前状态 {session.get('state')} 不允许执行该操作"
            )
        return session

    @staticmethod
    def _piece_exp(piece: Dict[str, Any]) -> int:
        return max(1, int(piece.get("exp", piece.get("star", 1))))

    def _sync_uid_counter(self, session: Dict[str, Any]):
        max_uid = self._next_uid
        for piece in session.get("board_chess", []) + session.get("bench_chess", []):
            max_uid = max(max_uid, int(piece.get("unique_id", 0)))
        for item in session.get("shop_items", []):
            max_uid = max(max_uid, int(item.get("shop_unique_id", 0)))
        self._next_uid = max_uid

    def _allocate_uid(self, session: Optional[Dict[str, Any]] = None) -> int:
        if session is not None:
            self._sync_uid_counter(session)
        self._next_uid += 1
        return self._next_uid

    # =========================================================================
    # 登录洪流与数据构造（逐字节无损对齐原素材）
    # =========================================================================

    def build_89201_payload(self, db, uid: int) -> bytes:
        """PVE 通关关卡列表 + 徽章解锁列表 (sc_89201)"""
        db = db or self.db
        stg_rows = []
        mdl_rows = []
        if db is not None:
            try:
                rows = db.query(
                    "SELECT stage_id, clear_time FROM autochess_stage WHERE uid=? ORDER BY clear_time DESC",
                    (uid,)
                )
                stg_rows = [{"id": r["stage_id"], "time": r["clear_time"]} for r in rows]
            except Exception as e:
                logger.warning(f"Query autochess_stage failed: {e}")

            try:
                rows = db.query(
                    "SELECT medal_id, level, unlock_time, upgrade_time FROM autochess_medal WHERE uid=? ORDER BY unlock_time DESC",
                    (uid,)
                )
                mdl_rows = [
                    {
                        "id": r["medal_id"],
                        "level": r["level"],
                        "unlock_time": r["unlock_time"],
                        "upgrade_time": r["upgrade_time"]
                    }
                    for r in rows
                ]
            except Exception as e:
                logger.warning(f"Query autochess_medal failed: {e}")

        obj = {"stage_list": stg_rows, "medal": mdl_rows}
        return encode("sc_89201", obj)

    def build_90051_payload(self, db, uid: int) -> bytes:
        """二期实物卡牌收藏列表 + 天梯元数据 (sc_90051)"""
        db = db or self.db
        cards = []
        rank_score = 4800
        energy = 28
        sunglasses = 1

        if db is not None:
            try:
                rows = db.query(
                    "SELECT card_id, num FROM autochess_card WHERE uid=? ORDER BY seq ASC",
                    (uid,)
                )
                cards = [{"id": r["card_id"], "num": r["num"]} for r in rows]
            except Exception as e:
                logger.warning(f"Query autochess_card failed: {e}")

            try:
                meta = db.query("SELECT rank_score, energy, sunglasses FROM autochess_meta WHERE uid=?", (uid,))
                if meta:
                    rank_score = meta[0]["rank_score"]
                    energy = meta[0]["energy"]
                    sunglasses = meta[0]["sunglasses"]
            except Exception as e:
                logger.warning(f"Query autochess_meta failed: {e}")

        obj = {
            "card_list": cards,
            "rank_score": rank_score,
            "energy": energy,
            "sunglasses": sunglasses
        }
        return encode("sc_90051", obj)

    def build_89125_payload(self, db, uid: int) -> bytes:
        """自走棋当前游戏状态 (sc_89125)"""
        session = self.get_session(uid)
        state = session.get("state", AutoChessConst.STATE_NONE) if session else AutoChessConst.STATE_NONE
        game_type = session.get("game_type", AutoChessConst.GAME_TYPE_PVE) if session else AutoChessConst.GAME_TYPE_PVE
        return encode("sc_89125", {"game_type": game_type, "state": state})

    def build_90007_payload(self, db, uid: int) -> bytes:
        """多人 PVP 断线重连检测 (sc_90007)"""
        return encode("sc_90007", {"can_reload": False})

    def build_89207_payload(self, db, uid: int) -> bytes:
        """历史对战战绩与徽章记录 (sc_89207)"""
        return encode("sc_89207", {"result": 0, "record": [], "medal_record": []})

    def build_89101_payload(self, db, uid: int) -> bytes:
        """局内整备与棋盘数据 (sc_89101)"""
        session = self.get_session(uid)
        if not session or session.get("state") == AutoChessConst.STATE_NONE:
            # 未在游戏中，返回空闲初始模板
            return self._build_empty_89101()

        return self._build_session_89101(session)

    def _build_empty_89101(self) -> bytes:
        attrs = [
            {"key": 1, "value": 0},
            {"key": 2, "value": 10},
            {"key": 3, "value": 10},
            {"key": 4, "value": 0},
            {"key": 6, "value": 4},
            {"key": 7, "value": 2},
            {"key": 8, "value": 2},
            {"key": 9, "value": 1},
            {"key": 10, "value": AutoChessConst.REFRESH_SHOP_COST},
            {"key": 11, "value": 4},
            {"key": 12, "value": 3},
            {"key": 13, "value": 0},
            {"key": 14, "value": 0},
            {"key": 15, "value": 0},
            {"key": 16, "value": 0},
            {"key": 17, "value": 0},
            {"key": 18, "value": 0}
        ]
        obj = {
            "game_type": 0,
            "auto_chessboard_info": {
                "base_info_list": [
                    {"key": 10, "value": 0, "value2": 562949954626314},
                    {"key": 11, "value": 1},
                    {"key": 1, "value": 100},
                    {"key": 4, "value": 1},
                    {"key": 5, "value": 101},
                    {"key": 2, "value": 0},
                    {"key": 6, "value": 0}
                ],
                "chess_list": []
            },
            "shop_items": [],
            "buff_list": [],
            "attr_list": attrs,
            "pve_restart_times": 0
        }
        return encode("sc_89101", obj)

    def build_chess_records(self, session: Dict[str, Any]) -> List[Dict[str, Any]]:
        chess_records = []
        for c in sorted(session.get("board_chess", []), key=lambda x: x.get("index", 0)):
            chess_records.append({
                "unique_id": c["unique_id"],
                "chess_id": c["chess_id"],
                "index": c["index"],
                "chess_attr_list": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": c.get("atk", 10)},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": c.get("hp", 100)},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": self._piece_exp(c)}
                ],
                "buff_list": []
            })
        for c in session.get("bench_chess", []):
            chess_records.append({
                "unique_id": c["unique_id"],
                "chess_id": c["chess_id"],
                "index": 0,
                "chess_attr_list": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": c.get("atk", 10)},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": c.get("hp", 100)},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": self._piece_exp(c)}
                ],
                "buff_list": []
            })
        return chess_records

    def _build_session_89101(self, session: Dict[str, Any]) -> bytes:
        stage_id = session.get("stage_id", 101)
        hp = session.get("hp", 100)
        round_num = session.get("round", 1)
        win_count = session.get("win_count", 0)
        gold = session.get("gold", 10)

        attrs = [
            {"key": AutoChessConst.KEY_NOW_MONEY, "value": gold},
            {"key": AutoChessConst.KEY_RESTART_MONEY, "value": 10},
            {"key": AutoChessConst.KEY_MONEY_INCRISE_CEIL, "value": 10},
            {"key": AutoChessConst.KEY_PREPARE_CELL_COUNT, "value": 0},
            {"key": AutoChessConst.KEY_CHESS_GOOD_COUNT, "value": 4},
            {"key": AutoChessConst.KEY_PROP_GOOD_COUNT, "value": 2},
            {"key": AutoChessConst.KEY_REWARD_GOOD_COUNT, "value": 2},
            {"key": AutoChessConst.KEY_SHOP_LEVEL_UP_ROUND, "value": 1},
            {"key": AutoChessConst.KEY_REFRESH_SHOP_COST, "value": AutoChessConst.REFRESH_SHOP_COST},
            {"key": AutoChessConst.KEY_SHOP_STAR_NUM, "value": session.get("shop_level", 1)},
            {"key": AutoChessConst.KEY_SHOP_CHESS_BASE_EXP, "value": 3},
            {"key": 13, "value": 0},
            {"key": 14, "value": 0},
            {"key": 15, "value": 0},
            {"key": 16, "value": 0},
            {"key": 17, "value": 0},
            {"key": 18, "value": 0}
        ]

        chess_records = self.build_chess_records(session)

        shop_records = []
        for s in session.get("shop_items", []):
            shop_records.append({
                "shop_type": s.get("shop_type", 0),
                "shop_unique_id": s["shop_unique_id"],
                "id": s["id"],
                "index": s["index"],
                "chess_attr_list": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": s.get("atk", 10)},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": s.get("hp", 100)},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": self._piece_exp(s)}
                ],
                "is_lock": s.get("is_lock", 0)
            })

        obj = {
            "game_type": session.get("game_type", 0),
            "auto_chessboard_info": {
                "base_info_list": [
                    {"key": AutoChessConst.INFO_KEY_BATTLE_UID, "value": 0, "value2": 562949954626314},
                    {"key": AutoChessConst.INFO_KEY_SUNGLASS_FLAG, "value": 1},
                    {"key": AutoChessConst.INFO_KEY_HP, "value": hp},
                    {"key": AutoChessConst.INFO_KEY_CUR_ROUND_COUNT, "value": round_num},
                    {"key": AutoChessConst.INFO_KEY_STAGE_ID, "value": stage_id},
                    {"key": AutoChessConst.INFO_KEY_VICTORY_ROUND_COUNT, "value": win_count},
                    {"key": AutoChessConst.INFO_KEY_BRAHMA_BOSS_FLAG, "value": 0}
                ],
                "chess_list": chess_records
            },
            "shop_items": shop_records,
            "buff_list": session.get("buff_list", []),
            "attr_list": attrs,
            "pve_restart_times": 0
        }
        return encode("sc_89101", obj)

    # =========================================================================
    # 对局会话与状态机核心 (Session & State Machine)
    # =========================================================================

    def get_session(self, uid: int) -> Dict[str, Any]:
        if uid not in self._sessions:
            # 优先从数据库加载
            if self.db is not None:
                try:
                    row = self.db.query("SELECT * FROM autochess_session WHERE uid=?", (uid,))
                    if row:
                        r = row[0]
                        self._sessions[uid] = {
                            "uid": uid,
                            "game_type": r.get("game_type", 0),
                            "state": r.get("state", 0),
                            "stage_id": r.get("stage_id", 0),
                            "round": r.get("round", 1),
                            "gold": r.get("gold", 10),
                            "hp": r.get("hp", 100),
                            "win_count": r.get("win_count", 0),
                            "defeat_count": r.get("defeat_count", 0),
                            "shop_level": r.get("shop_level", 1),
                            "board_chess": json.loads(r["chess_board_json"]) if r.get("chess_board_json") else [],
                            "bench_chess": json.loads(r["bench_chess_json"]) if r.get("bench_chess_json") else [],
                            "shop_items": json.loads(r["shop_items_json"]) if r.get("shop_items_json") else [],
                            "buff_list": json.loads(r["buff_list_json"]) if r.get("buff_list_json") else [],
                        }
                        self._sync_uid_counter(self._sessions[uid])
                        return self._sessions[uid]
                except Exception as e:
                    logger.warning(f"Load autochess_session failed: {e}")

            # 默认空会话
            self._sessions[uid] = {
                "uid": uid,
                "game_type": 0,
                "state": AutoChessConst.STATE_NONE,
                "stage_id": 0,
                "round": 1,
                "gold": 10,
                "hp": 100,
                "win_count": 0,
                "defeat_count": 0,
                "shop_level": 1,
                "board_chess": [],
                "bench_chess": [],
                "shop_items": [],
                "buff_list": [],
            }
        return self._sessions[uid]

    def save_session(self, uid: int):
        s = self._sessions.get(uid)
        if not s or self.db is None:
            return
        try:
            self.db.execute("""
                INSERT OR REPLACE INTO autochess_session (
                    uid, game_type, state, stage_id, round, gold, hp, win_count,
                    defeat_count, shop_level, chess_board_json, bench_chess_json,
                    shop_items_json, buff_list_json, update_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uid, s.get("game_type", 0), s.get("state", 0), s.get("stage_id", 0),
                s.get("round", 1), s.get("gold", 10), s.get("hp", 100),
                s.get("win_count", 0), s.get("defeat_count", 0), s.get("shop_level", 1),
                json.dumps(s.get("board_chess", [])), json.dumps(s.get("bench_chess", [])),
                json.dumps(s.get("shop_items", [])), json.dumps(s.get("buff_list", [])),
                int(time.time())
            ))
        except Exception as e:
            logger.error(f"Save autochess_session failed: {e}")

    def generate_shop_items(self, shop_level: int = 1, activity_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """从卡池随机生成 4 个棋子商店栏位（对齐 GameSetting.auto_chess_shop_star_rate 轮盘星级权重与 body 身材）"""
        all_chess = self.cfg.get("chess", {})
        candidates_by_star: Dict[int, List[Dict[str, Any]]] = {1: [], 2: [], 3: [], 4: []}
        all_candidates = []
        for cid_str, c in all_chess.items():
            if not isinstance(c, dict):
                continue
            # 必须为可购买的普通棋子 (type == 0)
            if c.get("type") != 0:
                continue
            # 必须拥有合法的等级增益结构 (level_buffs[1] 存在)
            lb = c.get("level_buffs")
            if not lb or not isinstance(lb, list) or len(lb) == 0 or not lb[0]:
                continue
            # 必须具有商店售价
            if c.get("shop_price", 0) <= 0:
                continue
            # 如果指定了活动ID，则必须包含在该活动卡池中
            if activity_id and activity_id not in c.get("activity_id", []):
                continue

            star = int(c.get("star", 1))
            all_candidates.append(c)
            if star in candidates_by_star:
                candidates_by_star[star].append(c)

        if not all_candidates:
            # 兜底：筛选任意合法 type == 0 棋子
            for c in all_chess.values():
                if isinstance(c, dict) and c.get("type") == 0:
                    lb = c.get("level_buffs")
                    if lb and isinstance(lb, list) and len(lb) > 0 and lb[0]:
                        all_candidates.append(c)
                        candidates_by_star[1].append(c)

        # 官方 GameSetting.auto_chess_shop_star_rate 权重分配
        star_rates = {
            1: [(1, 100)],
            2: [(1, 65), (2, 35)],
            3: [(1, 40), (2, 40), (3, 20)],
            4: [(1, 25), (2, 25), (3, 30), (4, 20)],
        }
        current_rates = star_rates.get(int(shop_level), [(1, 100)])

        items = []
        for idx in range(1, 5):
            # 轮盘抽取目标星级
            roll = random.randint(1, 100)
            target_star = 1
            cum = 0
            for st, weight in current_rates:
                cum += weight
                if roll <= cum:
                    target_star = st
                    break

            pool = candidates_by_star.get(target_star) or all_candidates
            choice = random.choice(pool) if pool else random.choice(all_candidates)
            cid = choice.get("id", 101)
            star = choice.get("star", 1)
            # 真实基础身材
            body = choice.get("body", [2, 3])
            atk = body[0] if len(body) > 0 else 2
            hp = body[1] if len(body) > 1 else 3

            items.append({
                "shop_type": AutoChessConst.SHOP_TYPE_NORMAL,
                "shop_unique_id": self._allocate_uid(),
                "id": cid,
                "index": idx,
                "star": star,
                "exp": 1,
                "atk": atk,
                "hp": hp,
                "is_lock": 0
            })
        return items

    def start_new_game(self, ctx, uid: int, game_type: int, stage_id: int):
        """开启新对局：cs_89116 -> sc_89117 + sc_89101 + sc_89125"""
        self._require_phase1_game_type(game_type)
        if not stage_id or stage_id <= 0:
            stage_id = 101
        self._require_phase1_stage(stage_id)
        stage_cfg = self.get_stage_cfg(stage_id) or {}
        defeat_num = int(stage_cfg.get("defeat_num", 3))

        session = self.get_session(uid)
        if session.get("state") not in (
            AutoChessConst.STATE_NONE,
            AutoChessConst.STATE_TOTAL_SETTLE_WIN,
            AutoChessConst.STATE_TOTAL_SETTLE_LOSE,
        ):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "已有进行中的一期 PVE 对局")
        session["game_type"] = game_type
        session["stage_id"] = stage_id
        session["state"] = AutoChessConst.STATE_PREPARE
        session["round"] = 1
        session["gold"] = 10
        session["hp"] = defeat_num
        session["win_count"] = 0
        session["defeat_count"] = 0
        session["shop_level"] = 1
        session["board_chess"] = []
        session["bench_chess"] = []
        session["buff_list"] = []
        session["shop_items"] = self.generate_shop_items(shop_level=1)

        self.save_session(uid)
        ctx.log(f"AutoChess: StartNewGame uid={uid} game_type={game_type} stage={stage_id} hp={defeat_num} gold=10")
        return session

    def refresh_shop(self, ctx, uid: int, game_type: int):
        """刷新商店：cs_89118 -> sc_89119 + sc_89101 (支持 202 追炎·前鬼免费刷新)"""
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        free_count = session.get("free_refresh", 0)
        if free_count > 0:
            session["free_refresh"] = free_count - 1
            cost = 0
        else:
            cost = AutoChessConst.REFRESH_SHOP_COST
            if session.get("gold", 0) < cost:
                raise OperationError(AutoChessConst.TIP_INVALID_OP, "金币不足，无法刷新商店")
            session["gold"] -= cost

        # 保留已锁定的商品
        current_shop = session.get("shop_items", [])
        locked_indices = {s["index"]: s for s in current_shop if s.get("is_lock") == 1}

        new_shop = self.generate_shop_items(session.get("shop_level", 1))
        final_shop = []
        for item in new_shop:
            idx = item["index"]
            if idx in locked_indices:
                final_shop.append(locked_indices[idx])
            else:
                final_shop.append(item)

        session["shop_items"] = final_shop
        self.save_session(uid)
        ctx.log(f"AutoChess: RefreshShop uid={uid} remain_gold={session['gold']} free_remain={session.get('free_refresh', 0)}")
        return session

    def buy_chess(self, ctx, uid: int, game_type: int, shop_type: int, shop_unique_id: int, to_index: int):
        """购买棋子：空格创建新 UID；同棋目标格按 EXP 定向合并并保留目标 UID。
        触发 ON_BUY 技能：102 潮音加血、401 托尔成长、311 梦影商店成长。
        """
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        shop_items = session.get("shop_items", [])

        target_item = None
        for s in shop_items:
            if s.get("shop_unique_id") == shop_unique_id:
                target_item = s
                break

        if not target_item:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "商店中未找到该商品")

        if int(target_item.get("shop_type", AutoChessConst.SHOP_TYPE_NORMAL)) != int(shop_type):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "商品类型与请求不一致")

        chess_cfg = self.get_chess_cfg(int(target_item["id"])) or {}
        price = int(chess_cfg.get("shop_price", 0))
        if price <= 0:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "该棋子不可购买")

        if session.get("gold", 0) < price:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "金币不足")

        if int(to_index) < 0:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "目标格非法")

        target_piece = None
        if int(to_index) > 0:
            target_piece = next(
                (p for p in session.get("board_chess", []) if int(p.get("index", 0)) == int(to_index)),
                None,
            )

        if target_piece is not None:
            if int(target_piece.get("chess_id", 0)) != int(target_item["id"]):
                raise OperationError(AutoChessConst.TIP_INVALID_OP, "目标格已被其他棋子占用")
            current_exp = self._piece_exp(target_piece)
            exp_limits = [int(v) for v in chess_cfg.get("exp", []) if isinstance(v, (int, float))]
            if exp_limits and current_exp >= max(exp_limits):
                raise OperationError(AutoChessConst.TIP_INVALID_OP, "目标棋子已达到最高等级")
            target_piece["exp"] = current_exp + self._piece_exp(target_item)
            target_piece["star"] = target_piece["exp"]  # 兼容旧存档字段，协议中按 EXP 下发
            target_piece["atk"] = int(target_piece.get("atk", 0)) + int(target_item.get("atk", 0))
            target_piece["hp"] = int(target_piece.get("hp", 0)) + int(target_item.get("hp", 0))
            new_piece = target_piece
        else:
            new_piece = {
                "unique_id": self._allocate_uid(session),
                "chess_id": target_item["id"],
                "index": int(to_index),
                "exp": self._piece_exp(target_item),
                "star": self._piece_exp(target_item),
                "atk": target_item.get("atk", 10),
                "hp": target_item.get("hp", 100),
            }
            if int(to_index) > 0:
                session.setdefault("board_chess", []).append(new_piece)
            else:
                session.setdefault("bench_chess", []).append(new_piece)

        session["gold"] -= price
        shop_items.remove(target_item)

        # 触发 ON_BUY 技能:
        all_allies = session.get("board_chess", []) + session.get("bench_chess", [])
        for p in all_allies:
            if p["unique_id"] == new_piece["unique_id"]:
                continue
            p_cid = int(p.get("chess_id", 0))
            p_star = self._piece_exp(p)
            # 102 潮音·波塞冬: 购买其他棋子时：使随机 1 个友方棋子 +3*star 血
            if p_cid == 102:
                candidates = [x for x in all_allies if x["unique_id"] != p["unique_id"]]
                if candidates:
                    target_f = random.choice(candidates)
                    target_f["hp"] = int(target_f.get("hp", 0)) + 3 * p_star
            # 401 轰雷·托尔: 购买&出售其他棋子时：随机 1 个己方棋子获得 +2*star 攻 / +2*star 血
            elif p_cid == 401:
                target_f = random.choice(all_allies) if all_allies else None
                if target_f:
                    target_f["atk"] = int(target_f.get("atk", 0)) + 2 * p_star
                    target_f["hp"] = int(target_f.get("hp", 0)) + 2 * p_star
            # 311 梦影·俄尼里伊: 每购买 2 次其他棋子时：商店中的棋子永久成长
            elif p_cid == 311:
                buy_cnt = session.get("buy_count_311", 0) + 1
                session["buy_count_311"] = buy_cnt
                if buy_cnt % 2 == 0:
                    for s_item in shop_items:
                        s_item["atk"] = int(s_item.get("atk", 0)) + 1 * p_star
                        s_item["hp"] = int(s_item.get("hp", 0)) + 1 * p_star

        self.save_session(uid)
        ctx.log(
            f"AutoChess: BuyChess uid={uid} piece={new_piece['chess_id']} "
            f"pos={to_index} target_uid={new_piece['unique_id']} exp={self._piece_exp(new_piece)} "
            f"gold_left={session['gold']}"
        )
        return session

    def _check_and_merge(self, session: Dict[str, Any], chess_id: int):
        """检测手牌或场上同名低星棋子自动合成"""
        all_pieces = session.get("board_chess", []) + session.get("bench_chess", [])
        matching_1star = [p for p in all_pieces if p["chess_id"] == chess_id and p.get("star", 1) == 1]
        if len(matching_1star) >= 3:
            # 合成 2 星：保留第一个，升级星级与属性，删除后两个
            keeper = matching_1star[0]
            to_remove = matching_1star[1:3]
            keeper["star"] = 2
            keeper["atk"] = int(keeper.get("atk", 10) * 1.8)
            keeper["hp"] = int(keeper.get("hp", 100) * 1.8)

            for rm in to_remove:
                if rm in session.get("board_chess", []):
                    session["board_chess"].remove(rm)
                if rm in session.get("bench_chess", []):
                    session["bench_chess"].remove(rm)
            logger.info(f"AutoChess: Auto merged 3x 1-star chess {chess_id} into 2-star!")

    def sell_chess(self, ctx, uid: int, game_type: int, unique_id: int):
        """出售棋子：cs_89122 -> sc_89123 + sc_89101 + sc_89133
        触发 ON_SELL 技能：211 阿波罗召唤、401 托尔成长。
        """
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        target = None
        for p in session.get("board_chess", []):
            if p["unique_id"] == unique_id:
                target = p
                session["board_chess"].remove(p)
                break
        if not target:
            for p in session.get("bench_chess", []):
                if p["unique_id"] == unique_id:
                    target = p
                    session["bench_chess"].remove(p)
                    break

        if not target:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "未找到目标棋子")

        # 返还金币 (按星级 1星=1, 2星=2, 3星=3)
        refund = target.get("star", 1)
        session["gold"] = session.get("gold", 0) + refund

        # 触发 ON_SELL 技能:
        sold_cid = int(target.get("chess_id", 0))
        sold_star = int(target.get("star", 1))
        all_allies = session.get("board_chess", []) + session.get("bench_chess", [])
        for p in all_allies:
            p_cid = int(p.get("chess_id", 0))
            p_star = self._piece_exp(p)
            # 401 轰雷·托尔: 购买&出售其他棋子时：随机 1 个己方棋子获得 +2*star 攻 / +2*star 血
            if p_cid == 401:
                target_f = random.choice(all_allies) if all_allies else None
                if target_f:
                    target_f["atk"] = int(target_f.get("atk", 0)) + 2 * p_star
                    target_f["hp"] = int(target_f.get("hp", 0)) + 2 * p_star

        # 211 光煌·阿波罗: 出售时：随机召唤 1 个 2 阶棋子到备战席
        if sold_cid == 211:
            summon_cand = [201, 202, 203, 204, 205, 206, 207, 208, 209, 210, 212, 213, 214, 215]
            gen_cid = random.choice(summon_cand)
            c_cfg = self.get_chess_cfg(gen_cid) or {}
            c_body = c_cfg.get("body", [4, 5])
            new_summon = {
                "unique_id": self._allocate_uid(session),
                "chess_id": gen_cid,
                "index": 0,
                "exp": 1,
                "star": 1,
                "atk": c_body[0] if len(c_body) > 0 else 4,
                "hp": c_body[1] if len(c_body) > 1 else 5,
            }
            session.setdefault("bench_chess", []).append(new_summon)

        self.save_session(uid)
        ctx.log(f"AutoChess: SellChess uid={uid} uid={unique_id} refund={refund}")
        return session

    def change_chess_team(self, ctx, uid: int, game_type: int, chess_list: List[Dict[str, Any]]):
        """调整棋子站位：cs_89104 -> sc_89105"""
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        # chess_list 格式: [{"key": unique_id, "value": new_index}, ...] 或 [{"unique_id": ..., "index": ...}]
        pos_map = {item.get("key", item.get("unique_id")): item.get("value", item.get("index")) for item in chess_list}

        all_pieces = session.get("board_chess", []) + session.get("bench_chess", [])
        known_uids = {p["unique_id"] for p in all_pieces}
        if any(uid_key not in known_uids for uid_key in pos_map):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "阵容包含未知棋子 UID")
        if any(pos is None for pos in pos_map.values()):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "阵容位置缺失")

        proposed_positions = {
            p["unique_id"]: int(p.get("index", 0))
            for p in all_pieces
        }

        # 智能换位 (Swap) 容错机制：
        # 若某棋子被移动至目标位置 T，且 T 原本已被未在 pos_map 中显式移动的棋子占用，
        # 则自动将原占位棋子对调至发起移动棋子的原位置，避免客户端误报“多个棋子不能占用同一格”
        explicit_uids = set(pos_map.keys())
        for moved_uid, new_pos in pos_map.items():
            new_pos = int(new_pos)
            old_pos = proposed_positions[moved_uid]
            if new_pos > 0 and new_pos != old_pos:
                occupier = next(
                    (p for p in all_pieces if proposed_positions[p["unique_id"]] == new_pos and p["unique_id"] not in explicit_uids),
                    None
                )
                if occupier:
                    proposed_positions[occupier["unique_id"]] = old_pos
            proposed_positions[moved_uid] = new_pos

        if any(pos < 0 for pos in proposed_positions.values()):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "棋子位置不能为负数")
        positive_positions = [pos for pos in proposed_positions.values() if pos > 0]
        if len(positive_positions) != len(set(positive_positions)):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "多个棋子不能占用同一格")

        new_board = []
        new_bench = []

        for p in all_pieces:
            uid_p = p["unique_id"]
            p["index"] = proposed_positions[uid_p]

            if p["index"] > 0:
                new_board.append(p)
            else:
                new_bench.append(p)

        # 保证在场棋子严格按 index (1, 2, 3...) 升序存储
        new_board.sort(key=lambda x: x["index"])
        new_bench.sort(key=lambda x: x.get("unique_id", 0))

        session["board_chess"] = new_board
        session["bench_chess"] = new_bench
        self.save_session(uid)
        ctx.log(f"AutoChess: ChangeChessTeam uid={uid} pieces_moved={len(chess_list)} total_board={len(new_board)}")

        # sc_89105 下发全量在场棋子的最新有序位置序列，防止客户端 ClearPlayerChessData 后丢失未移动棋子
        full_board_kvs = [{"key": p["unique_id"], "value": p["index"]} for p in new_board]
        return full_board_kvs

    def lock_shop(self, ctx, uid: int, game_type: int, lock_type: int, info_list: List[Dict[str, Any]]):
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        requested_uids = {
            int(shop_uid)
            for record in info_list
            for shop_uid in record.get("uid_list", [])
        }
        known_uids = {int(item.get("shop_unique_id", 0)) for item in session.get("shop_items", [])}
        if not requested_uids.issubset(known_uids):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "锁定列表包含未知商品 UID")
        is_locked = 1 if int(lock_type) else 0
        for item in session.get("shop_items", []):
            if int(item.get("shop_unique_id", 0)) in requested_uids:
                item["is_lock"] = is_locked
        self.save_session(uid)
        ctx.log(f"AutoChess: LockShop uid={uid} locked={is_locked} count={len(requested_uids)}")
        return session

    def merge_chess(self, ctx, uid: int, game_type: int, source_uid: int, loss_uid: int):
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        all_pieces = session.get("board_chess", []) + session.get("bench_chess", [])
        source = next((p for p in all_pieces if int(p.get("unique_id", 0)) == int(source_uid)), None)
        loss = next((p for p in all_pieces if int(p.get("unique_id", 0)) == int(loss_uid)), None)
        if source is None or loss is None or source is loss:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "合并棋子 UID 无效")
        if int(source.get("chess_id", 0)) != int(loss.get("chess_id", 0)):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "只能合并同名棋子")

        chess_cfg = self.get_chess_cfg(int(source["chess_id"])) or {}
        current_exp = self._piece_exp(source)
        exp_limits = [int(v) for v in chess_cfg.get("exp", []) if isinstance(v, (int, float))]
        if exp_limits and current_exp >= max(exp_limits):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "目标棋子已达到最高等级")

        source["exp"] = current_exp + self._piece_exp(loss)
        source["star"] = source["exp"]
        source["atk"] = int(source.get("atk", 0)) + int(loss.get("atk", 0))
        source["hp"] = int(source.get("hp", 0)) + int(loss.get("hp", 0))
        for collection_name in ("board_chess", "bench_chess"):
            collection = session.get(collection_name, [])
            if loss in collection:
                collection.remove(loss)
        self.save_session(uid)
        ctx.log(f"AutoChess: MergeChess uid={uid} keep={source_uid} loss={loss_uid} exp={source['exp']}")
        return session

    def prepare_end(self, ctx, uid: int, game_type: int):
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        session["state"] = AutoChessConst.STATE_PREPARE_END

        # 触发 ON_ROUND_END 钩子 (302 托特额外轮次, 201 大国主相邻赋攻, 304 霍德尔最前排增幅)
        board_chess = session.get("board_chess", [])
        if board_chess:
            # 302 托特·透特: 准备阶段结束时效果额外触发 1/2 次
            extra_rounds = max([p.get("star", 1) for p in board_chess if p.get("chess_id") == 302], default=0)
            exec_count = 1 + extra_rounds

            for _ in range(exec_count):
                for p in list(board_chess):
                    cid = p.get("chess_id")
                    star = p.get("star", 1)
                    idx = p.get("index", 0)

                    # 201 雀羽·大国主: 相邻友方棋子获得 +1/+2/+3 攻击力
                    if cid == 201:
                        for other in board_chess:
                            if other is not p and other.get("index", 0) in (idx - 1, idx + 1):
                                other["atk"] = int(other.get("atk", 0)) + star
                                ctx.log(f"AutoChess: Hook ON_ROUND_END [201 大国主] buffed adjacent uid={other.get('unique_id')} atk+{star}")

                    # 304 幽潮·霍德尔: 最前排棋子获得 +2/+4/+6 攻血
                    elif cid == 304:
                        front = min(board_chess, key=lambda x: x.get("index", 999))
                        if front:
                            front["atk"] = int(front.get("atk", 0)) + 2 * star
                            front["hp"] = int(front.get("hp", 0)) + 2 * star
                            ctx.log(f"AutoChess: Hook ON_ROUND_END [304 霍德尔] buffed front uid={front.get('unique_id')} atk+{2*star} hp+{2*star}")

        self.save_session(uid)
        ctx.log(f"AutoChess: PrepareEnd uid={uid} round={session.get('round', 1)}")
        return session

    def build_export_info(self, session: Dict[str, Any]) -> Dict[str, Any]:
        chess_list = []
        for piece in sorted(session.get("board_chess", []), key=lambda x: x.get("index", 0)):
            chess_list.append({
                "chess_id": int(piece["chess_id"]),
                "index": int(piece["index"]),
                "chess_kv": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": int(piece.get("atk", 0))},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": int(piece.get("hp", 0))},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": self._piece_exp(piece)},
                ],
                "buffs": [],
            })
        return {"chess_list": chess_list, "user_buff": []}

    def _simulate_round_battle(
        self,
        player_board: List[Dict[str, Any]],
        enemy_board: List[Dict[str, Any]],
        session: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """推演双方棋盘碰撞过程，生成完整行动帧 (FIGHT / DEAD / EFFECT / MOVE)，并计算胜负结果。
        全生命周期战斗技能引擎：
        - START_OF_BATTLE (204 休/308 努阿达/413 金固/411 执明)
        - ON_ATTACK / ON_FRONT_ALLY_ATTACK (303 薇儿丹蒂/103 狂鳄/203 朝约/213 诗蔻蒂/212 埃克什瓦)
        - FIGHT (对撞伤害结算、剧毒/瓦解一击必杀、圣盾抵消)
        - ON_SHIELD_BROKEN (307 阿修罗)
        - ON_DEAL_DAMAGE (113 雅典娜/412 塞赫麦特)
        - ON_DAMAGED (101 大国主/104 旧誓/110 羽灼/210 阿尔忒弥斯/309 阿努比斯/410 月读)
        - DEAD (阵亡动画下发)
        - ON_KILL (114 国常立/215 孟章/415 英招)
        - ON_FRIEND_DEAD (313 白泽/402 波塞冬)
        - DEATHRATTLE (105/405/406/106/112 亡语召唤、赋盾、伤害)
        - ON_SUMMON_SPAWN (208 维达尔/111 提尔)
        - MOVE (补位前移)
        """
        sorted_p = sorted(player_board, key=lambda x: x.get("index", 0))
        sorted_e = sorted(enemy_board, key=lambda x: x.get("index", 0))

        def _build_fighter(p: Dict[str, Any], side: int) -> Dict[str, Any]:
            cid = p.get("chess_id") or p.get("id", 101)
            star = p.get("star", 1)
            raw_buffs = p.get("buff_list", [])
            buff_dicts = []
            for b in raw_buffs:
                if isinstance(b, dict):
                    buff_dicts.append(b)
                elif isinstance(b, int):
                    buff_dicts.append({"buff_id": b, "unique_id": p.get("unique_id", 0) * 100 + len(buff_dicts) + 1})

            if not buff_dicts:
                c_cfg = self.get_chess_cfg(cid) or {}
                lb = c_cfg.get("level_buffs", [])
                if len(lb) >= star:
                    star_b = lb[star - 1]
                    bids = star_b if isinstance(star_b, list) else ([star_b] if star_b else [])
                    for b_idx, bid in enumerate(bids, 1):
                        buff_dicts.append({
                            "buff_id": bid,
                            "unique_id": p.get("unique_id", 0) * 100 + b_idx
                        })

            has_shield = False
            is_deadly = False
            deathrattles = []
            parsed_buffs = []

            for b in buff_dicts:
                bid = b.get("buff_id", 0)
                b_uid = b.get("unique_id", 0)
                bcfg = dict(self.get_buff_cfg(bid) or {})
                bcfg["buff_unique_id"] = b_uid
                bcfg["buff_id"] = bid
                kw = bcfg.get("keyword_type", 0)
                act = bcfg.get("action_type", 0)
                mom = bcfg.get("moment", 0)
                desc = bcfg.get("desc", "")

                if kw == 1 or act == 7 or "护盾" in desc or "圣盾" in desc:
                    has_shield = True
                if kw == 5 or act == 33 or "瓦解" in desc or "剧毒" in desc:
                    is_deadly = True
                if kw == 2 or mom == 17 or "离场" in desc or "亡语" in desc:
                    deathrattles.append(bcfg)
                parsed_buffs.append(bcfg)

            atk = p.get("atk")
            hp = p.get("hp")
            if atk is None or hp is None:
                for attr in p.get("chess_attr_list", []):
                    k = attr.get("key")
                    v = attr.get("value")
                    if k == AutoChessConst.CHESS_ATTR_ATK and atk is None:
                        atk = v
                    elif k == AutoChessConst.CHESS_ATTR_HP and hp is None:
                        hp = v
            if atk is None:
                c_cfg = self.get_chess_cfg(cid) or {}
                body = c_cfg.get("body", [2, 3])
                atk = (body[0] if len(body) > 0 else 2) * star
            if hp is None:
                c_cfg = self.get_chess_cfg(cid) or {}
                body = c_cfg.get("body", [2, 3])
                hp = (body[1] if len(body) > 1 else 3) * star

            atk = max(1, int(atk))
            hp = max(1, int(hp))

            return {
                "unique_id": p["unique_id"],
                "chess_id": cid,
                "index": p.get("index", 0),
                "star": star,
                "atk": atk,
                "hp": hp,
                "curr_hp": hp,
                "has_shield": has_shield,
                "is_deadly": is_deadly,
                "deathrattles": deathrattles,
                "buffs": parsed_buffs,
                "side": side,
                "shield_recharge_used": False,
            }

        p_fighters = [_build_fighter(p, 0) for p in sorted_p]
        e_fighters = [_build_fighter(e, 1) for e in sorted_e]

        if not p_fighters:
            return 2, []

        action_list = []
        action_id = 0
        max_turns = 100
        enemy_uid_seq = 290000
        p_dead_count = 0
        e_dead_count = 0

        def _buid(f):
            if not f:
                return 0
            buffs = f.get("buffs")
            if buffs and len(buffs) > 0 and isinstance(buffs[0], dict):
                return buffs[0].get("buff_unique_id", 0)
            return 0

        def _emit_add_attr(target_f, delta_atk, delta_hp, buff_uid):
            nonlocal action_id
            if delta_atk == 0 and delta_hp == 0:
                return
            target_f["atk"] = max(1, target_f["atk"] + delta_atk)
            target_f["hp"] = max(1, target_f["hp"] + delta_hp)
            target_f["curr_hp"] = max(1, target_f["curr_hp"] + delta_hp)
            action_id += 1
            action_list.append({
                "action_id": action_id,
                "action_type": AutoChessConst.ACTION_TYPE_EFFECT,
                "action_effect_info": {
                    "effect_enum": 1,  # ADD_ATTR
                    "buff_uid": int(buff_uid),
                    "target_list": [
                        {
                            "id": target_f["unique_id"],
                            "temp_id": target_f["chess_id"],
                            "update_list": [
                                {"key": AutoChessConst.CHESS_ATTR_ATK, "value": delta_atk},
                                {"key": AutoChessConst.CHESS_ATTR_HP, "value": delta_hp}
                            ]
                        }
                    ]
                }
            })

        def _emit_damage(target_f, damage, buff_uid):
            nonlocal action_id
            if damage <= 0 or target_f["curr_hp"] <= 0:
                return False
            if target_f["has_shield"]:
                target_f["has_shield"] = False
                actual_dmg = 0
            else:
                actual_dmg = damage
                target_f["curr_hp"] -= actual_dmg

            action_id += 1
            action_list.append({
                "action_id": action_id,
                "action_type": AutoChessConst.ACTION_TYPE_EFFECT,
                "action_effect_info": {
                    "effect_enum": 2,  # DAMAGE
                    "buff_uid": int(buff_uid),
                    "target_list": [
                        {
                            "id": target_f["unique_id"],
                            "temp_id": target_f["chess_id"],
                            "update_list": [
                                {"key": AutoChessConst.CHESS_ATTR_HP, "value": -actual_dmg}
                            ]
                        }
                    ]
                }
            })
            return target_f["curr_hp"] <= 0

        def _emit_shield(target_f, buff_uid):
            nonlocal action_id
            target_f["has_shield"] = True
            action_id += 1
            action_list.append({
                "action_id": action_id,
                "action_type": AutoChessConst.ACTION_TYPE_EFFECT,
                "action_effect_info": {
                    "effect_enum": 4,  # ADD_BUFF
                    "buff_uid": int(buff_uid),
                    "add_buff_info": [
                        {
                            "target_type": 0,
                            "target_uid": target_f["unique_id"],
                            "buff_info": {
                                "buff_id": 3,
                                "unique_id": target_f["unique_id"] * 100 + 99,
                                "owner_type": target_f["side"],
                                "source_type": 1,
                                "source_uid": target_f["unique_id"],
                                "source_cfg_id": target_f["chess_id"]
                            }
                        }
                    ]
                }
            })

        def _spawn_summon(s_cid, side, star=1, bonus_atk=0, bonus_hp=0, source_buff_uid=0):
            nonlocal action_id, enemy_uid_seq
            s_cfg = self.get_chess_cfg(s_cid) or {}
            s_body = s_cfg.get("body", [2, 2])
            s_star = s_cfg.get("star", star)
            s_atk = (s_body[0] if len(s_body) > 0 else 2) + bonus_atk
            s_hp = (s_body[1] if len(s_body) > 1 else 2) + bonus_hp

            if side == 0:
                s_uid = self._allocate_uid(session)
            else:
                enemy_uid_seq += 1
                s_uid = enemy_uid_seq

            s_rec = {
                "unique_id": s_uid,
                "chess_id": s_cid,
                "index": 1,
                "star": s_star,
                "atk": s_atk,
                "hp": s_hp,
                "chess_attr_list": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": s_atk},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": s_hp},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": s_star}
                ],
                "buff_list": []
            }
            s_fighter = _build_fighter(s_rec, side)

            # 触发 ON_SUMMON_SPAWN (208 维达尔套盾, 111 提尔加攻血)
            allies = p_fighters if side == 0 else e_fighters
            for ally in allies:
                if ally["chess_id"] == 208:
                    s_fighter["has_shield"] = True
                elif ally["chess_id"] == 111:
                    buid = _buid(ally)
                    _emit_add_attr(ally, 0, ally["star"], buid)

            action_id += 1
            action_list.append({
                "action_id": action_id,
                "action_type": AutoChessConst.ACTION_TYPE_EFFECT,
                "action_effect_info": {
                    "effect_enum": AutoChessConst.ACTION_EFFECT_TYPE_SUMMON,
                    "buff_uid": int(source_buff_uid),
                    "call_info": {
                        "user_id": side,
                        "chess_list": [s_rec]
                    }
                }
            })
            return s_fighter

        # 1. 战斗开始时 (BUS_BATTLE_START)
        for team, side in [(p_fighters, 0), (e_fighters, 1)]:
            for f in list(team):
                cid = f["chess_id"]
                star = f["star"]
                buid = _buid(f)
                if cid == 204 and team:  # 疾风之枪·休: 最前排+2*star/+2*star
                    _emit_add_attr(team[0], 2 * star, 2 * star, buid)
                elif cid == 308:  # 银臂·努阿达: 相邻赋盾+2*star攻
                    idx = team.index(f)
                    for n_idx in (idx - 1, idx + 1):
                        if 0 <= n_idx < len(team):
                            _emit_shield(team[n_idx], buid)
                            _emit_add_attr(team[n_idx], 2 * star, 0, buid)
                elif cid == 413:  # 荒獠·金固: 开局最前排召唤索贝克
                    s_f = _spawn_summon(9001, side, star=star, source_buff_uid=buid)
                    team.insert(0, s_f)
                elif cid == 411:  # 玄机·执明: 自身与随机1个队友+2*star攻血
                    _emit_add_attr(f, 2 * star, 2 * star, buid)
                    other_allies = [a for a in team if a is not f]
                    if other_allies:
                        _emit_add_attr(other_allies[0], 2 * star, 2 * star, buid)

        # 循环对撞交锋
        while p_fighters and e_fighters and action_id < max_turns:
            p = p_fighters[0]
            e = e_fighters[0]

            # 2. 攻击时触发 (BUS_ON_ATTACK)
            # 队首自身攻击
            if p["chess_id"] == 303:  # 黯耀·薇儿丹蒂
                buid = _buid(p)
                _emit_add_attr(p, 2 * p["star"], 2 * p["star"], buid)
            if e["chess_id"] == 303:
                buid = _buid(e)
                _emit_add_attr(e, 2 * e["star"], 2 * e["star"], buid)

            # 后排前方队友攻击响应 (ON_FRONT_ALLY_ATTACK)
            if len(p_fighters) > 1:
                p_sup = p_fighters[1]
                sup_cid = p_sup["chess_id"]
                sup_star = p_sup["star"]
                sup_buid = _buid(p_sup)
                if sup_cid == 103:  # 狂鳄: 自身+1*star攻
                    _emit_add_attr(p_sup, 1 * sup_star, 0, sup_buid)
                elif sup_cid == 203:  # 朝约: 队首+2*star血
                    _emit_add_attr(p, 0, 2 * sup_star, sup_buid)
                elif sup_cid == 213:  # 诗蔻蒂: 敌方打3伤害
                    _emit_damage(e, 3, sup_buid)

            if len(e_fighters) > 1:
                e_sup = e_fighters[1]
                sup_cid = e_sup["chess_id"]
                sup_star = e_sup["star"]
                sup_buid = _buid(e_sup)
                if sup_cid == 103:
                    _emit_add_attr(e_sup, 1 * sup_star, 0, sup_buid)
                elif sup_cid == 203:
                    _emit_add_attr(e, 0, 2 * sup_star, sup_buid)
                elif sup_cid == 213:
                    _emit_damage(p, 3, sup_buid)

            # 3. 交锋伤害结算 (FIGHT)
            p_shield_broken = False
            e_shield_broken = False

            if e["has_shield"]:
                p_actual_dmg = 0
                e["has_shield"] = False
                e_shield_broken = True
            else:
                p_actual_dmg = p["atk"]
                if p["is_deadly"]:
                    p_actual_dmg = max(p_actual_dmg, e["curr_hp"])

            if p["has_shield"]:
                e_actual_dmg = 0
                p["has_shield"] = False
                p_shield_broken = True
            else:
                e_actual_dmg = e["atk"]
                if e["is_deadly"]:
                    e_actual_dmg = max(e_actual_dmg, p["curr_hp"])

            action_id += 1
            action_list.append({
                "action_id": action_id,
                "action_type": AutoChessConst.ACTION_TYPE_FIGHT,
                "action_battle_info": {
                    "action_list": [
                        {"key": p["unique_id"], "value": -e_actual_dmg},
                        {"key": e["unique_id"], "value": -p_actual_dmg},
                    ]
                }
            })

            p["curr_hp"] -= e_actual_dmg
            e["curr_hp"] -= p_actual_dmg

            # 4. 破盾与再次获盾 (ON_SHIELD_BROKEN)
            if p["chess_id"] == 307 and (p_shield_broken or e_actual_dmg > 0) and not p.get("shield_recharge_used"):
                p["shield_recharge_used"] = True
                _emit_shield(p, _buid(p))
            if e["chess_id"] == 307 and (e_shield_broken or p_actual_dmg > 0) and not e.get("shield_recharge_used"):
                e["shield_recharge_used"] = True
                _emit_shield(e, _buid(e))

            # 5. 造成伤害响应 (ON_DEAL_DAMAGE)
            if p_actual_dmg > 0:
                for ally in p_fighters[1:]:
                    if ally["chess_id"] == 113:  # 雅典娜自身+1*star血
                        _emit_add_attr(ally, 0, 1 * ally["star"], _buid(ally))
                    elif ally["chess_id"] == 412:  # 塞赫麦特+2*star/+2*star
                        _emit_add_attr(ally, 2 * ally["star"], 2 * ally["star"], _buid(ally))
            if e_actual_dmg > 0:
                for ally in e_fighters[1:]:
                    if ally["chess_id"] == 113:
                        _emit_add_attr(ally, 0, 1 * ally["star"], _buid(ally))
                    elif ally["chess_id"] == 412:
                        _emit_add_attr(ally, 2 * ally["star"], 2 * ally["star"], _buid(ally))

            # 6. 受击狂暴与反击 (ON_DAMAGED)
            if e_actual_dmg > 0 and p["curr_hp"] > 0:
                p_cid = p["chess_id"]
                p_star = p["star"]
                p_buid = _buid(p)
                if p_cid == 101:  # 大国主: +1*star攻
                    _emit_add_attr(p, 1 * p_star, 0, p_buid)
                elif p_cid == 104:  # 旧誓: +1*star血
                    _emit_add_attr(p, 0, 1 * p_star, p_buid)
                elif p_cid == 110:  # 羽灼: +1*star攻/+1*star血
                    _emit_add_attr(p, 1 * p_star, 1 * p_star, p_buid)
                elif p_cid == 210 and e_fighters:  # 阿尔忒弥斯: 打3伤害
                    _emit_damage(e_fighters[0], 3, p_buid)
                elif p_cid == 309:  # 阿努比斯: 召唤胡狼
                    s_f = _spawn_summon(3091, 0, star=p_star, source_buff_uid=p_buid)
                    p_fighters.insert(0, s_f)

                # 410 震离·月读全场队友受击狂暴
                for ally in p_fighters:
                    if ally is not p and ally["chess_id"] == 410:
                        a_buid = _buid(ally)
                        _emit_add_attr(ally, 2 * ally["star"], 2 * ally["star"], a_buid)

            if p_actual_dmg > 0 and e["curr_hp"] > 0:
                e_cid = e["chess_id"]
                e_star = e["star"]
                e_buid = _buid(e)
                if e_cid == 101:
                    _emit_add_attr(e, 1 * e_star, 0, e_buid)
                elif e_cid == 104:
                    _emit_add_attr(e, 0, 1 * e_star, e_buid)
                elif e_cid == 110:
                    _emit_add_attr(e, 1 * e_star, 1 * e_star, e_buid)
                elif e_cid == 210 and p_fighters:
                    _emit_damage(p_fighters[0], 3, e_buid)
                elif e_cid == 309:
                    s_f = _spawn_summon(3091, 1, star=e_star, source_buff_uid=e_buid)
                    e_fighters.insert(0, s_f)

                for ally in e_fighters:
                    if ally is not e and ally["chess_id"] == 410:
                        a_buid = _buid(ally)
                        _emit_add_attr(ally, 2 * ally["star"], 2 * ally["star"], a_buid)

            # 7. 阵亡结算 (DEAD)
            p_dead = (p["curr_hp"] <= 0)
            e_dead = (e["curr_hp"] <= 0)
            dead_uids = []
            dead_p_fighter = None
            dead_e_fighter = None

            if p_dead:
                dead_uids.append(p["unique_id"])
                dead_p_fighter = p_fighters.pop(0)
                p_dead_count += 1
            if e_dead:
                dead_uids.append(e["unique_id"])
                dead_e_fighter = e_fighters.pop(0)
                e_dead_count += 1

            if dead_uids:
                action_id += 1
                action_list.append({
                    "action_id": action_id,
                    "action_type": AutoChessConst.ACTION_TYPE_DEAD,
                    "action_dead_info": {"uid_list": dead_uids}
                })

            # 8. 击杀触发 (ON_KILL)
            if e_dead and not p_dead and p["curr_hp"] > 0:
                p_cid = p["chess_id"]
                p_star = p["star"]
                p_buid = _buid(p)
                if p_cid == 114:  # 觅影·国常立: 其他己方棋子+1*star/+1*star
                    for ally in p_fighters:
                        if ally is not p:
                            _emit_add_attr(ally, 1 * p_star, 1 * p_star, p_buid)
                elif p_cid == 215:  # 青君·孟章: 自身+2*star/+2*star
                    _emit_add_attr(p, 2 * p_star, 2 * p_star, p_buid)
                elif p_cid == 415:  # 巡天·英招: 获得护盾
                    _emit_shield(p, p_buid)

            if p_dead and not e_dead and e["curr_hp"] > 0:
                e_cid = e["chess_id"]
                e_star = e["star"]
                e_buid = _buid(e)
                if e_cid == 114:
                    for ally in e_fighters:
                        if ally is not e:
                            _emit_add_attr(ally, 1 * e_star, 1 * e_star, e_buid)
                elif e_cid == 215:
                    _emit_add_attr(e, 2 * e_star, 2 * e_star, e_buid)
                elif e_cid == 415:
                    _emit_shield(e, e_buid)

            # 9. 队友阵亡触发 (ON_FRIEND_DEAD)
            if p_dead:
                for ally in p_fighters:
                    a_cid = ally["chess_id"]
                    a_star = ally["star"]
                    a_buid = _buid(ally)
                    if a_cid == 313 and e_fighters:  # 天诫·白泽: 对敌方首位打3伤害
                        _emit_damage(e_fighters[0], 3, a_buid)
                    elif a_cid == 402:  # 潮音·波塞冬: 己方全员+1*star/+1*star
                        for f in p_fighters:
                            _emit_add_attr(f, 1 * a_star, 1 * a_star, a_buid)

            if e_dead:
                for ally in e_fighters:
                    a_cid = ally["chess_id"]
                    a_star = ally["star"]
                    a_buid = _buid(ally)
                    if a_cid == 313 and p_fighters:
                        _emit_damage(p_fighters[0], 3, a_buid)
                    elif a_cid == 402:
                        for f in e_fighters:
                            _emit_add_attr(f, 1 * a_star, 1 * a_star, a_buid)

            # 10. 离场亡语 (DEATHRATTLE)
            p_summons = []
            e_summons = []

            for dead_f in [dead_p_fighter, dead_e_fighter]:
                if not dead_f:
                    continue
                side = dead_f["side"]
                spawned_summons = p_summons if side == 0 else e_summons
                dead_count = p_dead_count if side == 0 else e_dead_count

                for dr in dead_f.get("deathrattles", []):
                    act_type = dr.get("action_type", 0)
                    param = dr.get("param", [])
                    desc = dr.get("desc", "")

                    if act_type in (3, 23) or "召唤" in desc:
                        summon_cids = []
                        bonus_atk = 0
                        bonus_hp = 0

                        if act_type == 23 and isinstance(param, list) and len(param) >= 3:
                            c_cand = param[0]
                            c_cfg = self.get_chess_cfg(c_cand)
                            if c_cfg and c_cfg.get("type") == 2:
                                summon_cids.append(c_cand)
                                if isinstance(param[2], list) and len(param[2]) >= 2:
                                    bonus_atk = dead_count * param[2][0]
                                    bonus_hp = dead_count * param[2][1]
                        elif isinstance(param, list):
                            for item in param:
                                if isinstance(item, list):
                                    for sub in item:
                                        c = self.get_chess_cfg(sub)
                                        if c and c.get("type") == 2:
                                            summon_cids.append(sub)
                                else:
                                    c = self.get_chess_cfg(item)
                                    if c and c.get("type") == 2:
                                        summon_cids.append(item)

                        if not summon_cids:
                            dead_cid = dead_f["chess_id"]
                            if dead_cid == 105:
                                summon_cids = [1051 if dead_f["star"] == 1 else (1052 if dead_f["star"] == 2 else 1053)]
                            elif dead_cid == 405:
                                summon_cids = [4051 if dead_f["star"] == 1 else (4052 if dead_f["star"] == 2 else 4054)]

                        for s_cid in summon_cids:
                            s_f = _spawn_summon(s_cid, side, star=dead_f["star"], bonus_atk=bonus_atk, bonus_hp=bonus_hp, source_buff_uid=dr.get("buff_unique_id") or dr.get("id", 0))
                            spawned_summons.append(s_f)

                    elif act_type == 4 or "护盾" in desc:
                        side_team = p_fighters if side == 0 else e_fighters
                        if side_team:
                            _emit_shield(side_team[0], dr.get("buff_unique_id") or dr.get("id", 0))

                    elif act_type in (1, 2) or "造成" in desc:
                        opp_team = e_fighters if side == 0 else p_fighters
                        if opp_team:
                            _emit_damage(opp_team[0], 3, dr.get("buff_unique_id") or dr.get("id", 0))

            if p_summons:
                p_fighters = p_summons + p_fighters
            if e_summons:
                e_fighters = e_summons + e_fighters

            # 11. 补位位移 (MOVE)
            p_summon_uids = {s["unique_id"] for s in p_summons}
            e_summon_uids = {s["unique_id"] for s in e_summons}
            move_records = []

            if p_dead and p_fighters:
                for new_idx, fighter in enumerate(p_fighters, start=1):
                    if fighter.get("unique_id") not in p_summon_uids:
                        if fighter.get("index") != new_idx:
                            move_records.append({"key": fighter["unique_id"], "value": new_idx})
                    fighter["index"] = new_idx

            if e_dead and e_fighters:
                for new_idx, fighter in enumerate(e_fighters, start=1):
                    if fighter.get("unique_id") not in e_summon_uids:
                        if fighter.get("index") != new_idx:
                            move_records.append({"key": fighter["unique_id"], "value": new_idx})
                    fighter["index"] = new_idx

            if move_records:
                action_id += 1
                action_list.append({
                    "action_id": action_id,
                    "action_type": AutoChessConst.ACTION_TYPE_MOVE,
                    "action_move_info": {
                        "action_list": move_records
                    }
                })

        if not e_fighters and p_fighters:
            battle_result = 1  # WIN
        elif not p_fighters and e_fighters:
            battle_result = 2  # LOSE
        elif not p_fighters and not e_fighters:
            battle_result = 1 if len(player_board) >= len(enemy_board) else 3
        else:
            battle_result = 1

        return battle_result, action_list

    def launch_round_battle(self, ctx, uid: int, game_type: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """启动回合对战：cs_89108 -> sc_89109 + sc_89125 + sc_89113 (推演双方交锋动画与真实胜负)"""
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE_END})
        stage_id = session.get("stage_id", 101)
        round_num = session.get("round", 1)
        stage_cfg = self.get_stage_cfg(stage_id) or {}
        defeat_num = int(stage_cfg.get("defeat_num", 3))

        # 玩家棋盘构建 (装配星级技能与真实属性)
        player_board_records = []
        for p in session.get("board_chess", []):
            cid = p.get("chess_id") or p.get("id", 101)
            star = p.get("star", 1)
            c_cfg = self.get_chess_cfg(cid) or {}
            level_buffs = c_cfg.get("level_buffs", [])
            star_buff_records = []
            if len(level_buffs) >= star:
                raw_b = level_buffs[star - 1]
                bids = raw_b if isinstance(raw_b, list) else ([raw_b] if raw_b else [])
                for b_idx, bid in enumerate(bids, 1):
                    star_buff_records.append({
                        "unique_id": p["unique_id"] * 100 + b_idx,
                        "buff_id": bid,
                        "owner_type": 0,
                        "source_type": 1,
                        "source_uid": p["unique_id"],
                        "source_cfg_id": cid,
                    })

            p_atk = p.get("atk")
            p_hp = p.get("hp")
            if p_atk is None or p_hp is None:
                for attr in p.get("chess_attr_list", []):
                    k = attr.get("key")
                    v = attr.get("value")
                    if k == AutoChessConst.CHESS_ATTR_ATK and p_atk is None:
                        p_atk = v
                    elif k == AutoChessConst.CHESS_ATTR_HP and p_hp is None:
                        p_hp = v
            if p_atk is None or p_hp is None:
                body = c_cfg.get("body", [2, 3])
                if p_atk is None:
                    p_atk = (body[0] if len(body) > 0 else 2) * star
                if p_hp is None:
                    p_hp = (body[1] if len(body) > 1 else 3) * star

            player_board_records.append({
                "unique_id": p["unique_id"],
                "chess_id": cid,
                "index": p["index"],
                "star": star,
                "atk": p_atk,
                "hp": p_hp,
                "chess_attr_list": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": p_atk},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": p_hp},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": self._piece_exp(p)}
                ],
                "buff_list": star_buff_records
            })

        # 敌人棋盘预设构建 (装配星级技能与真实属性)
        enemy_team_ids = self.get_enemy_teams_for_stage(stage_id)
        enemy_team_id = enemy_team_ids[round_num - 1] if round_num <= len(enemy_team_ids) else (enemy_team_ids[-1] if enemy_team_ids else 110101)
        enemy_team_cfg = self.get_enemy_team_cfg(enemy_team_id) or {}

        enemy_board_records = []
        raw_team_info = enemy_team_cfg.get("team_info", [])
        for idx, entry in enumerate(raw_team_info, 1):
            # entry: [chess_id, star, hp_num, pos, buff_list]
            cid = entry[0] if len(entry) > 0 else 101
            star = entry[1] if len(entry) > 1 else 1
            pos = entry[3] if len(entry) > 3 and entry[3] > 0 else idx
            c_cfg = self.get_chess_cfg(cid) or {}
            c_body = c_cfg.get("body", [2, 3])
            c_atk = (c_body[0] if len(c_body) > 0 else 2) * star
            c_hp = (c_body[1] if len(c_body) > 1 else 3) * star

            level_buffs = c_cfg.get("level_buffs", [])
            star_buff_records = []
            if len(level_buffs) >= star:
                raw_b = level_buffs[star - 1]
                bids = raw_b if isinstance(raw_b, list) else ([raw_b] if raw_b else [])
                for b_idx, bid in enumerate(bids, 1):
                    star_buff_records.append({
                        "unique_id": (200000 + idx) * 100 + b_idx,
                        "buff_id": bid,
                        "owner_type": 1,
                        "source_type": 1,
                        "source_uid": 200000 + idx,
                        "source_cfg_id": cid,
                    })

            enemy_board_records.append({
                "unique_id": 200000 + idx,
                "chess_id": cid,
                "index": pos,
                "star": star,
                "atk": c_atk,
                "hp": c_hp,
                "chess_attr_list": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": c_atk},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": c_hp},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": star}
                ],
                "buff_list": star_buff_records
            })

        # 确保玩家与敌人棋盘记录严格按 index (1~5) 升序排列
        player_board_records.sort(key=lambda x: x["index"])
        enemy_board_records.sort(key=lambda x: x["index"])

        # 真实推演对战碰撞过程与动作帧
        battle_result, action_list = self._simulate_round_battle(
            player_board_records,
            enemy_board_records,
            session=session
        )
        session["last_battle_result"] = battle_result

        battle_uid = (int(time.time() * 1000) << 12) | (uid & 0xFFF)
        replay_info = {
            "game_type": game_type,
            "result": battle_result,
            "battle_uid": battle_uid,
            "user_id1": uid,
            "auto_chessboard_info1": {
                "base_info_list": [
                    {"key": AutoChessConst.INFO_KEY_HP, "value": session.get("hp", defeat_num)},
                    {"key": AutoChessConst.INFO_KEY_CUR_ROUND_COUNT, "value": round_num},
                    {"key": AutoChessConst.INFO_KEY_STAGE_ID, "value": stage_id},
                    {"key": AutoChessConst.INFO_KEY_VICTORY_ROUND_COUNT, "value": session.get("win_count", 0)}
                ],
                "chess_list": player_board_records
            },
            "user_id2": 999999,
            "auto_chessboard_info2": {
                "base_info_list": [
                    {"key": AutoChessConst.INFO_KEY_HP, "value": defeat_num},
                    {"key": AutoChessConst.INFO_KEY_CUR_ROUND_COUNT, "value": round_num},
                    {"key": AutoChessConst.INFO_KEY_STAGE_ID, "value": stage_id},
                    {"key": AutoChessConst.INFO_KEY_VICTORY_ROUND_COUNT, "value": 0}
                ],
                "chess_list": enemy_board_records
            },
            "max_group_id": 1,
            "point": 100,
            "point_detail": [],
            "enemy_name": stage_cfg.get("enemy_name", "馆主对弈者"),
            "enemy_icon": int(stage_cfg.get("enemy_icon", 1028)) if str(stage_cfg.get("enemy_icon", "")).isdigit() else 1028
        }

        win_num = max(1, int(stage_cfg.get("win_num", 1)))
        session["last_battle_result"] = battle_result
        if battle_result == 1:
            if session.get("win_count", 0) + 1 >= win_num:
                session["state"] = AutoChessConst.STATE_TOTAL_SETTLE_WIN
            else:
                session["state"] = AutoChessConst.STATE_ROUND_SETTLE
        else:
            if session.get("hp", defeat_num) - 1 <= 0:
                session["state"] = AutoChessConst.STATE_TOTAL_SETTLE_LOSE
            else:
                session["state"] = AutoChessConst.STATE_ROUND_SETTLE

        self.save_session(uid)
        ctx.log(f"AutoChess: LaunchRoundBattle uid={uid} round={round_num} result={battle_result} actions={len(action_list)}")
        return replay_info, action_list

    def settle_battle(self, ctx, uid: int, game_type: int):
        """结算战斗回合 / 关卡总胜利：cs_89126 -> sc_89127 + sc_89201 + sc_89125"""
        session = self._require_session_state(
            uid,
            game_type,
            {
                AutoChessConst.STATE_ROUND_SETTLE,
                AutoChessConst.STATE_TOTAL_SETTLE_WIN,
                AutoChessConst.STATE_TOTAL_SETTLE_LOSE,
            },
        )
        stage_id = session.get("stage_id", 101)
        stage_cfg = self.get_stage_cfg(stage_id) or {}
        battle_state = session["state"]
        last_result = session.get("last_battle_result", 1)
        is_stage_cleared = battle_state == AutoChessConst.STATE_TOTAL_SETTLE_WIN
        is_stage_lost = battle_state == AutoChessConst.STATE_TOTAL_SETTLE_LOSE

        if last_result == 1:
            session["win_count"] = session.get("win_count", 0) + 1
        elif last_result == 2:
            session["defeat_count"] = session.get("defeat_count", 0) + 1
            session["hp"] = max(0, session.get("hp", 3) - 1)

        if is_stage_cleared:
            # 标记通关并入库，会话恢复闲置
            session["state"] = AutoChessConst.STATE_NONE
            if self.db is not None:
                try:
                    self.db.execute(
                        "INSERT OR REPLACE INTO autochess_stage (uid, stage_id, clear_time) VALUES (?, ?, ?)",
                        (uid, stage_id, int(time.time()))
                    )
                    ctx.log(f"AutoChess: Stage {stage_id} CLEARED! Saved to autochess_stage.")
                except Exception as e:
                    logger.error(f"Save autochess_stage failed: {e}")

                # 检查道馆徽章解锁 (103->1001, 203->1002, 303->1003, 401->1004)
                medal_map = {103: 1001, 203: 1002, 303: 1003, 401: 1004}
                if stage_id in medal_map:
                    mid = medal_map[stage_id]
                    try:
                        self.db.execute(
                            "INSERT OR REPLACE INTO autochess_medal (uid, medal_id, level, unlock_time, upgrade_time) VALUES (?, ?, 1, ?, 0)",
                            (uid, mid, int(time.time()))
                        )
                        ctx.log(f"AutoChess: Medal {mid} UNLOCKED! Saved to autochess_medal.")
                    except Exception as e:
                        logger.error(f"Save autochess_medal failed: {e}")

            # 广播任务事件
            bus.emit(Events.STAGE_PASS, ctx, uid, stage_id=stage_id)
            bus.emit(Events.STAGE_FIRST_CLEAR, ctx, uid, stage_id=stage_id)
        elif is_stage_lost:
            session["state"] = AutoChessConst.STATE_NONE
        else:
            # 回合递进
            session["round"] = session.get("round", 1) + 1
            session["state"] = AutoChessConst.STATE_PREPARE
            session["shop_level"] = min(4, session["round"])
            # 每一轮战斗结束后，商店费用恢复到上限 10 点 (对齐 KEY_RESTART_MONEY = 10)
            session["gold"] = max(10, session.get("gold", 0))

            # 触发 ON_ROUND_START 钩子 (101 大国主额外金币, 202 前鬼免费刷新)
            for p in session.get("board_chess", []):
                p_cid = p.get("chess_id")
                p_star = p.get("star", 1)
                if p_cid == 101:
                    session["gold"] = session.get("gold", 0) + p_star
                    ctx.log(f"AutoChess: Hook ON_ROUND_START [101 大国主] added gold+{p_star}, now={session['gold']}")
                elif p_cid == 202:
                    free_refresh = 2 * p_star
                    session["free_refresh"] = session.get("free_refresh", 0) + free_refresh
                    ctx.log(f"AutoChess: Hook ON_ROUND_START [202 前鬼] added free_refresh+{free_refresh}")

            # 自动刷新一轮商店，并保留已锁定的商品
            current_shop = session.get("shop_items", [])
            locked_indices = {s["index"]: s for s in current_shop if s.get("is_lock") == 1}
            new_shop = self.generate_shop_items(session.get("shop_level", 1))
            final_shop = []
            for item in new_shop:
                idx = item["index"]
                if idx in locked_indices:
                    final_shop.append(locked_indices[idx])
                else:
                    final_shop.append(item)
            session["shop_items"] = final_shop

        self.save_session(uid)
        return is_stage_cleared, session

    def cancel_game(self, ctx, uid: int, game_type: int):
        """放弃 / 退出对局：cs_89128 -> sc_89129 + sc_89125"""
        session = self._require_session_state(
            uid,
            game_type,
            {
                AutoChessConst.STATE_PREPARE,
                AutoChessConst.STATE_PREPARE_END,
                AutoChessConst.STATE_ROUND_SETTLE,
                AutoChessConst.STATE_TOTAL_SETTLE_WIN,
                AutoChessConst.STATE_TOTAL_SETTLE_LOSE,
            },
        )
        session["state"] = AutoChessConst.STATE_NONE
        session["stage_id"] = 0
        session["board_chess"] = []
        session["bench_chess"] = []
        session["shop_items"] = []
        session["buff_list"] = []
        self.save_session(uid)
        ctx.log(f"AutoChess: CancelGame uid={uid}")
        return session


# =============================================================================
# Operations (协议算子注册)
# =============================================================================

# 1. 开启新对局
@operation
class AutoChessStartNewGameOp(Operation):
    cmd = 89116
    sc = 89117

    def validate(self, data, ctx):
        if not data:
            raise OperationError(1, "缺少请求参数")
        return data

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        game_type = data.get("game_type", 0)
        stage_id = data.get("other_param", 0)
        session = svc.start_new_game(ctx, self.uid, game_type, stage_id)
        return session

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        p89117 = encode("sc_89117", {"result": 0})
        p89101 = svc.build_89101_payload(ctx.db, self.uid)
        p89125 = svc.build_89125_payload(ctx.db, self.uid)
        return [
            DownFrame(89125, p89125),
            DownFrame(89101, p89101),
            DownFrame(89117, p89117)
        ]


# 2. 刷新商店
@operation
class AutoChessRefreshShopOp(Operation):
    cmd = 89118
    sc = 89119

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        game_type = data.get("game_type", 0)
        return svc.refresh_shop(ctx, self.uid, game_type)

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        p89119 = encode("sc_89119", {"result": 0})
        p89101 = svc.build_89101_payload(ctx.db, self.uid)
        return [
            DownFrame(89101, p89101),
            DownFrame(89119, p89119)
        ]


# 3. 购买棋子
@operation
class AutoChessBuyChessOp(Operation):
    cmd = 89102
    sc = 89103

    def validate(self, data, ctx):
        if not data or "shop_unique_id" not in data:
            raise OperationError(1, "缺少商品唯一ID")
        return data

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.buy_chess(
            ctx, self.uid,
            data.get("game_type", 0),
            data.get("shop_type", 0),
            data.get("shop_unique_id", 0),
            data.get("to_index", 0)
        )

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        session = result
        p89103 = encode("sc_89103", {"result": 0})
        p89101 = svc.build_89101_payload(ctx.db, self.uid)
        return [
            # ACK 必须先被客户端消费并完成本地 ClearChess/PlayMerge；随后再用
            # 权威快照校准金币、商店、棋盘及合并后消失的 UID。
            DownFrame(89103, p89103),
            DownFrame(89101, p89101),
        ]


# 4. 出售棋子
@operation
class AutoChessSellChessOp(Operation):
    cmd = 89122
    sc = 89123

    def validate(self, data, ctx):
        if not data or "uid" not in data:
            raise OperationError(1, "缺少棋子UID")
        return data

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.sell_chess(ctx, self.uid, data.get("game_type", 0), data["uid"])

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        session = result
        p89111 = encode("sc_89111", {
            "game_type": session.get("game_type", 0),
            "attr_list": [
                {"key": AutoChessConst.KEY_NOW_MONEY, "value": session.get("gold", 0)}
            ]
        })
        p89123 = encode("sc_89123", {"result": 0})
        p89101 = svc.build_89101_payload(ctx.db, self.uid)
        return [
            DownFrame(89111, p89111),
            DownFrame(89123, p89123),
            DownFrame(89101, p89101),
        ]


# 5. 调整阵容
@operation
class AutoChessChangeTeamOp(Operation):
    cmd = 89104
    sc = 89105

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.change_chess_team(ctx, self.uid, data.get("game_type", 0), data.get("chess_list", []))

    def respond(self, result, data, ctx):
        p89105 = encode("sc_89105", {"result": 0, "chess_list": result})
        return [DownFrame(89105, p89105)]


# 6. 锁定商店
@operation
class AutoChessLockShopOp(Operation):
    cmd = 89120
    sc = 89121

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.lock_shop(
            ctx,
            self.uid,
            data.get("game_type", 0),
            data.get("type", 0),
            data.get("info_list", []),
        )

    def respond(self, result, data, ctx):
        p89121 = encode("sc_89121", {"result": 0})
        return [DownFrame(89121, p89121)]


# 7. 启动回合对战
@operation
class AutoChessLaunchRoundBattleOp(Operation):
    cmd = 89108
    sc = 89109

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.launch_round_battle(ctx, self.uid, data.get("game_type", 0))

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        replay_info, action_list = result
        p89109 = encode("sc_89109", {"result": 0, "replay_info": replay_info})
        p89113 = encode("sc_89113", {
            "game_type": replay_info.get("game_type", 0),
            "battle_uid": replay_info["battle_uid"],
            "battle_info": {"group_id": 1, "action_list": action_list},
        })
        return [
            # 主应答先初始化 battleData，再切换结算态，最后补齐唯一 round group；
            # 客户端收到 89113 后 maxRound == initedRoundDataCount 才会真正启动模拟器。
            DownFrame(89109, p89109),
            DownFrame(89125, svc.build_89125_payload(ctx.db, self.uid)),
            DownFrame(89113, p89113),
        ]


# 8. 结算对战
@operation
class AutoChessSettleBattleOp(Operation):
    cmd = 89126
    sc = 89127

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        is_cleared, session = svc.settle_battle(ctx, self.uid, data.get("game_type", 0))
        return is_cleared, session

    def respond(self, result, data, ctx):
        is_cleared, session = result
        svc = AutoChessService.get_instance(ctx.db)
        p89127 = encode("sc_89127", {"result": 0})
        frames = []

        if is_cleared:
            # 关卡获胜，下发更新后的关卡/徽章帧与状态帧（在89127之前）
            frames.append(DownFrame(89201, svc.build_89201_payload(ctx.db, self.uid)))
            frames.append(DownFrame(89125, svc.build_89125_payload(ctx.db, self.uid)))
            frames.append(DownFrame(89127, p89127))
        else:
            # 新回合准备数据与状态帧：先 89125 -> 89127 让客户端跳转整备页，后发 89101 刷新商店新UID
            frames.append(DownFrame(89125, svc.build_89125_payload(ctx.db, self.uid)))
            frames.append(DownFrame(89127, p89127))
            frames.append(DownFrame(89101, svc.build_89101_payload(ctx.db, self.uid)))

        return frames


# 9. 放弃对局
@operation
class AutoChessCancelGameOp(Operation):
    cmd = 89128
    sc = 89129

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.cancel_game(ctx, self.uid, data.get("game_type", 0))

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        p89129 = encode("sc_89129", {"result": 0})
        p89125 = svc.build_89125_payload(ctx.db, self.uid)
        return [
            DownFrame(89125, p89125),
            DownFrame(89129, p89129)
        ]


# 10. 侦查敌方阵容
@operation
class AutoChessLookEnemyInfoOp(Operation):
    cmd = 89138
    sc = 89139

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        gid = data.get("group_id", 101)
        rnd = data.get("round", 1)
        svc = AutoChessService.get_instance(ctx.db)
        session = svc.get_session(self.uid) or {}
        stage_id = session.get("stage_id", gid)
        stage_cfg = svc.get_stage_cfg(stage_id) or {}
        defeat_num = int(stage_cfg.get("defeat_num", 3))

        tids = svc.cfg.get("stage_group_map", {}).get(str(gid), [])
        if not tids and str(stage_id) in svc.cfg.get("stage_group_map", {}):
            tids = svc.cfg.get("stage_group_map", {}).get(str(stage_id), [])
        tid = tids[rnd - 1] if rnd <= len(tids) else (tids[-1] if tids else 110101)
        t_cfg = svc.get_enemy_team_cfg(tid) or {}

        records = []
        for idx, entry in enumerate(t_cfg.get("team_info", []), 1):
            cid = entry[0] if len(entry) > 0 else 101
            star = entry[1] if len(entry) > 1 else 1
            pos = entry[3] if len(entry) > 3 and entry[3] > 0 else idx
            c_cfg = svc.get_chess_cfg(cid) or {}
            c_body = c_cfg.get("body", [2, 3])
            c_atk = (c_body[0] if len(c_body) > 0 else 2) * star
            c_hp = (c_body[1] if len(c_body) > 1 else 3) * star
            records.append({
                "unique_id": 300000 + idx,
                "chess_id": cid,
                "index": pos,
                "chess_attr_list": [
                    {"key": AutoChessConst.CHESS_ATTR_ATK, "value": c_atk},
                    {"key": AutoChessConst.CHESS_ATTR_HP, "value": c_hp},
                    {"key": AutoChessConst.CHESS_ATTR_EXP, "value": star}
                ],
                "buff_list": []
            })

        board = {
            "base_info_list": [
                {"key": AutoChessConst.INFO_KEY_HP, "value": defeat_num},
                {"key": AutoChessConst.INFO_KEY_CUR_ROUND_COUNT, "value": rnd},
                {"key": AutoChessConst.INFO_KEY_STAGE_ID, "value": stage_id}
            ],
            "chess_list": records
        }
        return board

    def respond(self, result, data, ctx):
        p89139 = encode("sc_89139", {"result": 0, "enemy_auto_chess_board": result})
        return [DownFrame(89139, p89139)]


# 11. 手动合成棋子
@operation
class AutoChessMergeChessOp(Operation):
    cmd = 89130
    sc = 89131

    def validate(self, data, ctx):
        if not data or "source_uid" not in data or "loss_uid" not in data:
            raise OperationError(1, "缺少合并棋子 UID")
        return data

    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.merge_chess(
            ctx,
            self.uid,
            data.get("game_type", 0),
            data["source_uid"],
            data["loss_uid"],
        )

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        p89131 = encode("sc_89131", {"result": 0})
        p89101 = svc.build_89101_payload(ctx.db, self.uid)
        return [
            DownFrame(89131, p89131),
            DownFrame(89101, p89101),
        ]


# 12. 快速跳过关卡
@operation
class AutoChessSkipStageOp(Operation):
    cmd = 89212
    sc = 89213

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        sid = data.get("stage_id", 101)
        AutoChessService.get_instance(ctx.db)._require_phase1_stage(sid)
        if ctx.db is not None:
            try:
                ctx.db.execute(
                    "INSERT OR REPLACE INTO autochess_stage (uid, stage_id, clear_time) VALUES (?, ?, ?)",
                    (self.uid, sid, int(time.time()))
                )
            except Exception as e:
                logger.warning(f"SkipStage insert failed: {e}")
        return data

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        p89213 = encode("sc_89213", {"result": 0})
        frames = [
            DownFrame(89201, svc.build_89201_payload(ctx.db, self.uid)),
            DownFrame(89213, p89213)
        ]
        return frames


# 13. 轻量应答族 (升级/一键购买/回放/整备结束/导出/暂停/战绩/榜单)
@operation
class AutoChessPlayerUpgradeOp(Operation):
    cmd = 89134
    sc = 89135
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(89135, encode("sc_89135", {"result": 0}))]

@operation
class AutoChessBuyChessOneKeyOp(Operation):
    cmd = 89140
    sc = 89141
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(89141, encode("sc_89141", {"result": 0}))]

@operation
class AutoChessLookReplayOp(Operation):
    cmd = 89142
    sc = 89143
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(89143, encode("sc_89143", {"result": 0}))]

@operation
class AutoChessPrepareEndOp(Operation):
    cmd = 89144
    sc = 89145
    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return svc.prepare_end(ctx, self.uid, data.get("game_type", 0))

    def respond(self, result, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        return [
            DownFrame(89145, encode("sc_89145", {"result": 0})),
            DownFrame(89125, svc.build_89125_payload(ctx.db, self.uid)),
        ]

@operation
class AutoChessExportInfoOp(Operation):
    cmd = 89146
    sc = 89147
    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        session = svc._require_session_state(
            self.uid,
            data.get("game_type", 0),
            {
                AutoChessConst.STATE_PREPARE_END,
                AutoChessConst.STATE_ROUND_SETTLE,
                AutoChessConst.STATE_TOTAL_SETTLE_WIN,
                AutoChessConst.STATE_TOTAL_SETTLE_LOSE,
            },
        )
        return svc.build_export_info(session)

    def respond(self, result, data, ctx):
        return [DownFrame(89147, encode("sc_89147", {"result": 0, "export_info": result}))]

@operation
class AutoChessSetGamePauseOp(Operation):
    cmd = 89148
    sc = 89149
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(89149, encode("sc_89149", {"result": 0}))]

@operation
class AutoChessRequestRecordOp(Operation):
    cmd = 89206
    sc = 89207
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(89207, encode("sc_89207", {"result": 0, "record": [], "medal_record": []}))]

@operation
class AutoChessFetchRankTeamInfoOp(Operation):
    cmd = 89208
    sc = 89209
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(89209, encode("sc_89209", {"result": 0}))]


# =============================================================================
# PVP 拦截与静默桩 (90xxx 族)
# =============================================================================

@operation
class AutoChessStartMatchOp(Operation):
    """PVP 匹配发起拦截：cs_90002 -> 回复错误码 608020 (AUTO_CHESS_2_CLOSED，弹出 Toast 并截断)"""
    cmd = 90002
    sc = 90003

    def apply(self, data, ctx):
        ctx.log(f"AutoChess: Intercepted PVP StartMatch cs_90002 from uid={self.uid}. Returning Toast error code 608020.")
        return data

    def respond(self, result, data, ctx):
        # 608020 = AUTO_CHESS_2_CLOSED -> 弹出 "活动已结束"
        p90003 = encode("sc_90003", {"result": AutoChessConst.TIP_CLOSED, "present_time": 0})
        return [DownFrame(90003, p90003)]


@operation
class AutoChessStopMatchOp(Operation):
    cmd = 90004
    sc = 90005
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90005, encode("sc_90005", {"result": 0}))]


@operation
class AutoChessReconnectCheckOp(Operation):
    cmd = 90008
    sc = 90009
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90009, encode("sc_90009", {"result": 0}))]


# 二期实物卡牌与心愿静默桩
@operation
class AutoChessGetCardDesireListOp(Operation):
    cmd = 90058
    sc = 90059
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90059, encode("sc_90059", {"result": 0, "wish_list": []}))]

@operation
class AutoChessCompleteDesireOp(Operation):
    cmd = 90060
    sc = 90061
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90061, encode("sc_90061", {"result": 0, "reward_list": []}))]

@operation
class AutoChessConfirmDesireOp(Operation):
    cmd = 90062
    sc = 90063
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90063, encode("sc_90063", {"result": 0}))]

@operation
class AutoChessGiveDesireCardOp(Operation):
    cmd = 90064
    sc = 90065
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90065, encode("sc_90065", {"result": 0}))]

@operation
class AutoChessDrawCardOp(Operation):
    cmd = 90068
    sc = 90069
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90069, encode("sc_90069", {"result": 0, "card_list": []}))]

@operation
class AutoChessCardExchangeOp(Operation):
    cmd = 90070
    sc = 90071
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90071, encode("sc_90071", {"result": 0}))]

@operation
class AutoChessSetWishCardOp(Operation):
    cmd = 90072
    sc = 90073
    def apply(self, data, ctx): return data
    def respond(self, result, data, ctx): return [DownFrame(90073, encode("sc_90073", {"result": 0}))]
