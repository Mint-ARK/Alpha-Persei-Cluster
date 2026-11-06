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
   - sc_89201 (关卡与徽章): 按当前账号持久数据生成；
   - sc_90051 (卡牌收藏): 按当前账号持久数据生成；
   - sc_89125 (游戏状态), sc_90007 (重连检查), sc_89207 (历史战绩).
4. PVE 单机对弈状态机（89xxx 协议族）：
   - 开局选关(89116->89117)；
   - 商店发牌与金币扣减(89118->89119)；
   - 棋子购买、出售、布阵、经验合成(89102/89122/89104/89130)；
   - 回合战斗生成(89108->89109，下发关卡敌人阵容回放包，客户端本地模拟演播)；
   - 关卡胜利结算(89126->89127，首次凭证、徽章与任务差量)。
5. PVP 模式截断与安全阻断（90xxx 协议族）：
   - cs_90002 匹配请求直接返回 sc_90003 { result: 608020 } (AUTO_CHESS_2_CLOSED，弹出 Toast 拦截)；
   - 其余联机协议静默回执兜底，杜绝客户端卡死转圈。
"""

import os
import json
import time
import random
import logging
import threading
from copy import deepcopy
from typing import Dict, Any, List, Optional, Tuple

from core import Operation, OperationError, operation
from middleware import DownFrame
from event_bus import bus, Events
from codec import encode as _encode, decode


def encode(name, data):
    payload = _encode(name, data)
    if (not isinstance(payload, (bytes, bytearray))
            or (not payload and ('result' in data or 'game_type' in data))):
        raise ValueError(f'自走棋必要报文编码失败: {name}')
    return payload

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
    _instance_lock = threading.RLock()

    def __init__(self, db=None):
        self.db = db
        self.cfg: Dict[str, Any] = {}
        self._load_cfg()
        self._local = threading.local()
        self._next_uid = 100000

    @property
    def _sessions(self):
        # A mutable working copy belongs to this thread, never another save or
        # connection. SQL is re-read on every public access, including rollback.
        if not hasattr(self._local, 'sessions'):
            self._local.sessions = {}
        return self._local.sessions

    @classmethod
    def get_instance(cls, db=None) -> "AutoChessService":
        with cls._instance_lock:
            if db is None:
                if cls._instance is None:
                    cls._instance = cls()
                return cls._instance
            service = getattr(db, '_autochess_service', None)
            if service is None:
                service = cls(db=db)
                db._autochess_service = service
            return service

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

    def _enemy_team_id(self, session, round_num=None):
        stage_id = int(session['stage_id'])
        # Local simplified Brahma rule: four victories, then the configured boss.
        if stage_id == self._setting('auto_chess_boss_stage', 303) and int(session.get('win_count', 0)) >= 4:
            return self._setting('auto_chess_boss_team_id', 130399)
        ids = [tid for tid in self.get_enemy_teams_for_stage(stage_id)
               if int((self.get_enemy_team_cfg(tid) or {}).get('round', 0)) < 99]
        if not ids:
            raise OperationError(2, '缺少当前关卡敌方阵容')
        rnd = int(round_num if round_num is not None else session.get('round', 1))
        if rnd < 1:
            raise OperationError(2, '对战回合无效')
        return ids[min(rnd, len(ids)) - 1]

    def _enemy_records(self, team_id):
        """Team tuples use EXP, ATK, HP (never board position); positions are ordered."""
        team = self.get_enemy_team_cfg(team_id)
        if not team:
            raise OperationError(2, '敌方阵容配置缺失')
        records = []
        for pos, entry in enumerate(team.get('team_info', []), 1):
            cid, exp = int(entry[0]), int(entry[1])
            cfg = self.get_chess_cfg(cid)
            if not cfg:
                raise OperationError(2, '敌方棋子配置缺失')
            level = self._piece_level(dict(chess_id=cid, exp=exp))
            atk, hp = cfg['body']
            atk += int(entry[2]); hp += int(entry[3])
            levels = cfg.get('level_buffs', [])
            bids = levels[level - 1] if len(levels) >= level else []
            bids = bids if isinstance(bids, list) else [bids]
            extra = []
            for attachment in entry[4]:
                # Native 120101/11 contains bare [1,1], not valid export_buff
                # records. Ignore malformed extras, retaining the configured unit.
                if not isinstance(attachment, list) or len(attachment) != 2:
                    continue
                bid, params = attachment
                if int(bid) == 1:
                    # Native preconfigured teams encode permanent base increments here.
                    if isinstance(params, list) and len(params) >= 2:
                        atk += int(params[0]); hp += int(params[1])
                else:
                    extra.append(int(bid))
            uid = 200000 + pos
            buffs = [dict(unique_id=uid*100+i, buff_id=int(bid), owner_type=1,
                          source_type=1, source_uid=uid, source_cfg_id=cid)
                     for i, bid in enumerate(bids + extra, 1)]
            records.append(dict(unique_id=uid, chess_id=cid, index=pos,
                chess_attr_list=[dict(key=1,value=max(0,atk)), dict(key=2,value=max(1,hp)),
                                 dict(key=3,value=exp)], buff_list=buffs))
        return records

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

    def _piece_level(self, piece):
        cfg = self.get_chess_cfg(int(piece.get('chess_id', piece.get('id', 0)))) or {}
        return 1 + sum(self._piece_exp(piece) >= int(v) for v in cfg.get('exp', []) if isinstance(v, (int,float)))

    def _piece_buffs(self, piece, owner_type=0):
        cfg = self.get_chess_cfg(piece['chess_id']) or {}
        level = self._piece_level(piece)
        levels = cfg.get('level_buffs', [])
        bids = levels[level - 1] if len(levels) >= level else []
        bids = bids if isinstance(bids, list) else [bids]
        uid = piece['unique_id']
        buffs = [dict(unique_id=(1 << 40) + uid*100+i, buff_id=int(bid),
                      owner_type=owner_type, source_type=1, source_uid=uid,
                      source_cfg_id=piece['chess_id']) for i,bid in enumerate(bids, 1)]
        return buffs + deepcopy(piece.get('buff_list', []))

    def _validate_board(self, session, *, battle=False):
        board = session.get('board_chess', [])
        positions = [int(p.get('index', 0)) for p in board]
        uids = [int(p.get('unique_id', 0)) for p in board + session.get('bench_chess', [])]
        if (len(board) > 5 or any(p < 1 or p > 5 for p in positions)
                or len(set(positions)) != len(positions) or len(set(uids)) != len(uids)):
            raise OperationError(2, '准备棋盘位置须在1至5且不能重复')
        if battle and session.get('bench_chess'):
            raise OperationError(2, '请先将备战棋子移回棋盘或出售')

    def _base_chess_exp(self, session):
        stage = self.get_stage_cfg(session.get('stage_id', 0)) or {}
        return 3 if 622 in (stage.get('scene_buff') or []) else 1

    def _apply_chess_bonus(self, session, piece):
        bonus = session.get('chess_bonus', {}).get(str(piece.get('chess_id', piece.get('id'))), [0,0])
        piece['atk'] = int(piece.get('atk', 0)) + int(bonus[0])
        piece['hp'] = int(piece.get('hp', 0)) + int(bonus[1])
        return piece

    def _sync_uid_counter(self, session: Dict[str, Any]):
        max_uid = self._next_uid
        for piece in session.get("board_chess", []) + session.get("bench_chess", []):
            max_uid = max(max_uid, int(piece.get("unique_id", 0)))
            for buff in piece.get('buff_list', []):
                max_uid = max(max_uid, int(buff.get('unique_id', 0)))
        for buff in session.get('buff_list', []):
            max_uid = max(max_uid, int(buff.get('unique_id', 0)))
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
                raise

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
                raise

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
                "buff_list": self._piece_buffs(c)
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
                "buff_list": self._piece_buffs(c)
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
            {"key": AutoChessConst.KEY_RESTART_MONEY, "value": 10 + session.get("prop_effects",{}).get("restart_money_bonus",0)},
            {"key": AutoChessConst.KEY_MONEY_INCRISE_CEIL, "value": 10 + session.get("prop_effects",{}).get("money_ceiling_bonus",0)},
            {"key": AutoChessConst.KEY_PREPARE_CELL_COUNT, "value": 0},
            {"key": AutoChessConst.KEY_CHESS_GOOD_COUNT, "value": 4},
            {"key": AutoChessConst.KEY_PROP_GOOD_COUNT, "value": 2},
            {"key": AutoChessConst.KEY_REWARD_GOOD_COUNT, "value": 2},
            {"key": AutoChessConst.KEY_SHOP_LEVEL_UP_ROUND, "value": 1},
            {"key": AutoChessConst.KEY_REFRESH_SHOP_COST, "value": AutoChessConst.REFRESH_SHOP_COST},
            {"key": AutoChessConst.KEY_SHOP_STAR_NUM, "value": session.get("shop_level", 1)},
            {"key": AutoChessConst.KEY_SHOP_CHESS_BASE_EXP, "value": self._base_chess_exp(session)},
            {"key": 13, "value": 0},
            {"key": 14, "value": 0},
            {"key": 15, "value": 0},
            {"key": AutoChessConst.KEY_SHOP_FREE_REFRESH_COUNT, "value": session.get("free_refresh", 0)},
            {"key": 17, "value": session.get("free_prop",0)},
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
                    {"key": AutoChessConst.INFO_KEY_BATTLE_UID, "value": 0, "value2": (session.get("pending_round") or {}).get("battle_uid", 0)},
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
            "pve_restart_times": session.get("restart_times", 0)
        }
        return encode("sc_89101", obj)

    # =========================================================================
    # 对局会话与状态机核心 (Session & State Machine)
    # =========================================================================

    SESSION_KIND = 'autochess_runtime_v1'
    EXTRA_FIELDS = ('last_battle_result', 'pending_round', 'last_replay',
                    'free_refresh', 'buy_count_311', 'prepare_snapshot', 'restart_times', 'prop_effects', 'free_prop', 'chess_bonus')

    def get_session(self, uid: int) -> Dict[str, Any]:
        if self.db is None:
            if uid in self._sessions:
                return self._sessions[uid]
            rows, extra = [], []
        else:
            # Read both halves in one SQL snapshot. Errors must propagate;
            # a corrupt or unavailable save is not an empty game.
            with self.db.transaction():
                rows = self.db.query('SELECT * FROM autochess_session WHERE uid=?', (uid,))
                extra = self.db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',
                                      (uid, self.SESSION_KIND, '0')) if rows else []
        if rows:
            r = rows[0]
            session = {
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
            if extra:
                data = json.loads(extra[0]['value_json'])
                if not isinstance(data, dict) or data.get('version') != 1:
                    raise OperationError(2, '自走棋扩展存档格式无效')
                for key in self.EXTRA_FIELDS:
                    if key in data:
                        session[key] = data[key]
            for key in ('board_chess', 'bench_chess', 'shop_items', 'buff_list'):
                if not isinstance(session[key], list):
                    raise OperationError(2, '自走棋存档列表格式无效')
        else:
            session = dict(uid=uid, game_type=0, state=AutoChessConst.STATE_NONE,
                           stage_id=0, round=1, gold=10, hp=100, win_count=0,
                           defeat_count=0, shop_level=1, board_chess=[],
                           bench_chess=[], shop_items=[], buff_list=[])
        self._sessions[uid] = session
        self._sync_uid_counter(session)
        return session

    def save_session(self, uid: int):
        s = self._sessions.get(uid)
        if not s or self.db is None:
            return
        from account_db import savepoint_transaction
        with savepoint_transaction(self.db):
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

            extra = dict(version=1, **{key: s[key] for key in self.EXTRA_FIELDS if key in s})
            self.db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
                            'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
                            (uid, self.SESSION_KIND, '0', json.dumps(extra, ensure_ascii=False)))

    def pending_round(self, session):
        pending = session.get('pending_round')
        if (not isinstance(pending, dict) or pending.get('consumed') is not False
                or pending.get('stage_id') != session['stage_id']
                or pending.get('round') != session['round']
                or pending.get('state') != session['state']
                or pending.get('result') not in (0, 1, 2, 3)
                or not pending.get('battle_uid')):
            raise OperationError(2, '缺少有效的待结算战报，请退出本局后重新开始')
        replay = session.get('last_replay')
        if (not isinstance(replay, dict) or replay.get('info', {}).get('battle_uid') != pending['battle_uid']
                or replay['info'].get('result') != pending['result']):
            raise OperationError(2, '待结算战报与回合记录不一致')
        return pending

    def generate_shop_items(self, shop_level: int = 1, activity_id: Optional[int] = 3740601, base_exp: int = 1) -> List[Dict[str, Any]]:
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
            raise OperationError(2, '当前活动没有可购买棋子')

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
                "rarity": star,
                "star": 1 + sum(base_exp >= int(v) for v in choice.get("exp", [])),
                "exp": base_exp,
                "atk": atk,
                "hp": hp,
                "is_lock": 0
            })
        return items

    @staticmethod
    def _setting(name, default):
        from autochess_special import setting
        value = setting(name, [default])
        return int(value[0])

    def _generate_session_shop(self, session):
        from autochess_special import generate_props
        generated = self.generate_shop_items(session.get('shop_level', 1), base_exp=self._base_chess_exp(session))
        for piece in generated:
            self._apply_chess_bonus(session, piece)
        generated += generate_props(self, session)
        return generated

    def start_new_game(self, ctx, uid: int, game_type: int, stage_id: int):
        """开启新对局：cs_89116 -> sc_89117 + sc_89101 + sc_89125"""
        self._require_phase1_game_type(game_type)
        if not stage_id or stage_id <= 0:
            stage_id = 101
        self._require_phase1_stage(stage_id)
        from new_player_service import enabled, config
        if enabled(self.db,uid):
            stage_rule=config()['autochess'].get(str(stage_id),{})
            prerequisites={stage_rule.get('prev',0),config()['autochess_areas'].get(str(stage_rule.get('area')),{}).get('prev',0)}-{0}
            for previous in prerequisites:
                if not self.db.query('SELECT 1 FROM autochess_stage WHERE uid=? AND stage_id=? AND clear_time>0',(uid,previous)):
                    raise OperationError(2,'自走棋前置关卡尚未通关')
        stage_cfg = self.get_stage_cfg(stage_id) or {}
        defeat_num = int(stage_cfg.get("defeat_num", 3))

        session = self.get_session(uid)
        if session.get('state') == AutoChessConst.STATE_TOTAL_SETTLE_LOSE and session.get('stage_id') == stage_id:
            self.pending_round(session)
            snapshot = session.get('prepare_snapshot')
            retries = int(session.get('restart_times', 0))
            if not isinstance(snapshot, dict) or retries >= self._setting('auto_chess_pve_restart_times',999):
                raise OperationError(2, '本局没有可用的重新挑战机会')
            restored = deepcopy(snapshot)
            restored['restart_times'] = retries + 1
            restored['gold'] = int(restored.get('gold', 0)) + self._setting('auto_chess_pve_restart_add_coin',10)
            restored['state'] = AutoChessConst.STATE_PREPARE
            self._sessions[uid] = restored
            self.save_session(uid)
            return restored
        if session.get('state') == AutoChessConst.STATE_TOTAL_SETTLE_WIN:
            self.settle_battle(ctx, uid, game_type)
            session = self.get_session(uid)
        if session.get("state") not in (
            AutoChessConst.STATE_NONE,
            AutoChessConst.STATE_TOTAL_SETTLE_WIN,
            AutoChessConst.STATE_TOTAL_SETTLE_LOSE,
        ):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "已有进行中的一期 PVE 对局")
        for key in self.EXTRA_FIELDS:
            session.pop(key, None)
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
        session["shop_items"] = self._generate_session_shop(session)

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
        locked_indices = {(s.get("shop_type",0),s["index"]): s for s in current_shop if s.get("is_lock") == 1}

        new_shop = self._generate_session_shop(session)
        final_shop = []
        for item in new_shop:
            idx = (item.get("shop_type",0),item["index"])
            if idx in locked_indices:
                final_shop.append(locked_indices[idx])
            else:
                final_shop.append(item)

        session["shop_items"] = final_shop + [i for i in current_shop if i.get("shop_type")==2]
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

        if int(shop_type) == AutoChessConst.SHOP_TYPE_PROP:
            from autochess_special import buy_prop
            buy_prop(self, ctx, uid, session, target_item, int(to_index))
            self.save_session(uid)
            return session
        if int(shop_type) not in (AutoChessConst.SHOP_TYPE_NORMAL, AutoChessConst.SHOP_TYPE_REWARD):
            raise OperationError(2, '不支持的商品类型')

        chess_cfg = self.get_chess_cfg(int(target_item["id"])) or {}
        price = 0 if int(shop_type)==AutoChessConst.SHOP_TYPE_REWARD else int(chess_cfg.get("shop_price", 0))
        if price < 0 or (price == 0 and int(shop_type)!=AutoChessConst.SHOP_TYPE_REWARD):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "该棋子不可购买")

        if session.get("gold", 0) < price:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "金币不足")

        if not 1 <= int(to_index) <= 5:
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "目标格非法")

        self._validate_board(session)
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
            target_piece["star"] = self._piece_level(target_piece)
            target_piece["atk"] = int(target_piece.get("atk", 0)) + int(target_item.get("atk", 0))
            target_piece["hp"] = int(target_piece.get("hp", 0)) + int(target_item.get("hp", 0))
            new_piece = target_piece
        else:
            new_piece = {
                "unique_id": self._allocate_uid(session),
                "chess_id": target_item["id"],
                "index": int(to_index),
                "exp": self._piece_exp(target_item),
                "star": self._piece_level(target_item),
                "atk": target_item.get("atk", 10),
                "hp": target_item.get("hp", 100),
            }
            if int(to_index) > 0:
                session.setdefault("board_chess", []).append(new_piece)
            else:
                session.setdefault("bench_chess", []).append(new_piece)

        session["gold"] -= price
        shop_items.remove(target_item)

        from autochess_preparation import trigger
        trigger(self, session, 3, new_piece)

        self.save_session(uid)
        ctx.log(
            f"AutoChess: BuyChess uid={uid} piece={new_piece['chess_id']} "
            f"pos={to_index} target_uid={new_piece['unique_id']} exp={self._piece_exp(new_piece)} "
            f"gold_left={session['gold']}"
        )
        return session

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
        sell_prices = (self.get_chess_cfg(target['chess_id']) or {}).get('sell_price', [])
        level = self._piece_level(target)
        if len(sell_prices) < level:
            raise OperationError(2, '缺少棋子出售价格')
        refund = int(sell_prices[level - 1])
        session["gold"] = session.get("gold", 0) + refund

        from autochess_preparation import trigger
        trigger(self, session, 5, target)

        self.save_session(uid)
        ctx.log(f"AutoChess: SellChess uid={uid} uid={unique_id} refund={refund}")
        return session

    def change_chess_team(self, ctx, uid: int, game_type: int, chess_list: List[Dict[str, Any]]):
        """调整棋子站位：cs_89104 -> sc_89105"""
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE})
        # chess_list 格式: [{"key": unique_id, "value": new_index}, ...] 或 [{"unique_id": ..., "index": ...}]
        if len({item.get("key", item.get("unique_id")) for item in chess_list}) != len(chess_list):
            raise OperationError(2, "阵容请求UID重复")
        pos_map = {item.get("key", item.get("unique_id")): item.get("value", item.get("index")) for item in chess_list}

        all_pieces = session.get("board_chess", []) + session.get("bench_chess", [])
        known_uids = {p["unique_id"] for p in all_pieces}
        if any(uid_key not in known_uids for uid_key in pos_map):
            raise OperationError(AutoChessConst.TIP_INVALID_OP, "阵容包含未知棋子 UID")
        if any(pos is None or not 1 <= int(pos) <= 5 for pos in pos_map.values()):
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

        if any(pos < 0 or pos > 5 for pos in proposed_positions.values()):
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
        source["star"] = self._piece_level(source)
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
        self._validate_board(session, battle=True)
        session['prepare_snapshot'] = {k: deepcopy(v) for k,v in session.items()
                                       if k not in ('prepare_snapshot','pending_round','last_replay')}
        session["state"] = AutoChessConst.STATE_PREPARE_END

        from autochess_preparation import trigger
        trigger(self, session, 10)

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
                "buffs": [dict(buff_id=int(b['buff_id']), chess_kv=deepcopy(b.get('other_param', [])))
                          for b in piece.get('buff_list', [])],
            })
        return {"chess_list": chess_list, "user_buff": []}

    def _simulate_round_battle(self, player_board, enemy_board, session=None):
        from autochess_combat import simulate
        return simulate(self, player_board, enemy_board, session)

    def launch_round_battle(self, ctx, uid: int, game_type: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """启动回合对战：cs_89108 -> sc_89109 + sc_89125 + sc_89113 (推演双方交锋动画与真实胜负)"""
        session = self._require_session_state(uid, game_type, {AutoChessConst.STATE_PREPARE_END})
        self._validate_board(session, battle=True)
        stage_id = session.get("stage_id", 101)
        round_num = session.get("round", 1)
        stage_cfg = self.get_stage_cfg(stage_id) or {}
        defeat_num = int(stage_cfg.get("defeat_num", 3))

        # 玩家棋盘构建 (装配星级技能与真实属性)
        player_board_records = []
        for p in session.get("board_chess", []):
            cid = p.get("chess_id") or p.get("id", 101)
            star = self._piece_level(p)
            c_cfg = self.get_chess_cfg(cid) or {}
            star_buff_records = self._piece_buffs(p)

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

            team_bonus = session.get('prop_effects',{}).get('team_bonus',[0,0])
            p_atk += int(team_bonus[0]); p_hp += int(team_bonus[1])

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

        enemy_team_id = self._enemy_team_id(session)
        enemy_board_records = self._enemy_records(enemy_team_id)

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
        elif battle_result == 2:
            if session.get("hp", defeat_num) - 1 <= 0:
                session["state"] = AutoChessConst.STATE_TOTAL_SETTLE_LOSE
            else:
                session["state"] = AutoChessConst.STATE_ROUND_SETTLE

        else:
            session['state'] = AutoChessConst.STATE_ROUND_SETTLE

        session['pending_round'] = dict(stage_id=stage_id, round=round_num,
                                        battle_uid=battle_uid, result=battle_result,
                                        state=session['state'], consumed=False, enemy_team_id=enemy_team_id)
        for side in ('auto_chessboard_info1', 'auto_chessboard_info2'):
            for piece in replay_info[side]['chess_list']:
                for key in ('star','atk','hp'):
                    piece.pop(key, None)
        session['last_replay'] = dict(info=deepcopy(replay_info), actions=deepcopy(action_list))
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
        pending = self.pending_round(session)
        last_result = pending['result']
        if last_result in (0,3):
            battle_state = AutoChessConst.STATE_ROUND_SETTLE
        pending['consumed'] = True
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
            from autochess_progress import settle_completed
            settle_completed(ctx, uid, stage_id)
        elif is_stage_lost:
            session["state"] = AutoChessConst.STATE_NONE
        else:
            # 回合递进
            session["round"] = session.get("round", 1) + 1
            session["state"] = AutoChessConst.STATE_PREPARE
            session["shop_level"] = min(4, session["round"])
            # 每一轮战斗结束后，商店费用恢复到上限 10 点 (对齐 KEY_RESTART_MONEY = 10)
            effects = session.setdefault('prop_effects', {})
            session['gold'] = 10 + int(effects.get('restart_money_bonus',0)) + int(effects.pop('next_round_gold',0))
            session["free_refresh"] = 0

            from autochess_preparation import round_start
            round_start(self, session)

            # 自动刷新一轮商店，并保留已锁定的商品
            current_shop = session.get("shop_items", [])
            locked_indices = {(s.get("shop_type",0),s["index"]): s for s in current_shop if s.get("is_lock") == 1}
            new_shop = self._generate_session_shop(session)
            final_shop = []
            for item in new_shop:
                idx = (item.get("shop_type",0),item["index"])
                if idx in locked_indices:
                    final_shop.append(locked_indices[idx])
                else:
                    final_shop.append(item)
            session["shop_items"] = final_shop + [i for i in current_shop if i.get("shop_type")==2]

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
        for key in self.EXTRA_FIELDS:
            session.pop(key, None)
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
    prepare_response_in_transaction = True
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
        frames = [
            DownFrame(89125, p89125),
            DownFrame(89101, p89101),
            DownFrame(89117, p89117)
        ]
        from operations import _refresh_frames
        frames.extend(_refresh_frames(ctx, self.uid, strict=True))
        return frames


# 2. 刷新商店
@operation
class AutoChessRefreshShopOp(Operation):
    prepare_response_in_transaction = True
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
    prepare_response_in_transaction = True
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
    prepare_response_in_transaction = True
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
    prepare_response_in_transaction = True
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
    prepare_response_in_transaction = True
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
    prepare_response_in_transaction = True
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
    prepare_response_in_transaction = True
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

        from operations import _refresh_frames
        frames.extend(_refresh_frames(ctx, self.uid, strict=True))
        return frames


# 9. 放弃对局
@operation
class AutoChessCancelGameOp(Operation):
    prepare_response_in_transaction = True
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

        if not session or session.get('state') == AutoChessConst.STATE_NONE:
            svc._require_phase1_stage(int(gid))
            session = dict(stage_id=int(gid), round=int(rnd), win_count=0)
            stage_id = int(gid)
        tid = svc._enemy_team_id(session, int(rnd))
        records = svc._enemy_records(tid)

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
    prepare_response_in_transaction = True
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
    prepare_response_in_transaction = True
    cmd = 89212
    sc = 89213

    def apply(self, data, ctx):
        # The only native call is RemakeMainView.ShowTeachStage, for
        # GameSetting.auto_chess_2_tutorial_stage_id=401 (area 4, ONLINE).
        # Phase-1 PVE has no skip action; it must complete normal settlement.
        raise OperationError(17, '当前人机道馆不支持跳过关卡')


# 13. 轻量应答族 (升级/一键购买/回放/整备结束/导出/暂停/战绩/榜单)
@operation
class AutoChessPlayerUpgradeOp(Operation):
    cmd = 89134
    sc = 89135
    def apply(self, data, ctx):
        raise OperationError(2, '当前没有可执行的管理员升级机会')
    def respond(self, result, data, ctx): return [DownFrame(89135, encode("sc_89135", {"result": 0}))]

@operation
class AutoChessBuyChessOneKeyOp(Operation):
    cmd = 89140
    sc = 89141
    def apply(self, data, ctx):
        raise OperationError(2, '当前没有可执行的一键购买机会')
    def respond(self, result, data, ctx): return [DownFrame(89141, encode("sc_89141", {"result": 0}))]

@operation
class AutoChessLookReplayOp(Operation):
    prepare_response_in_transaction = True
    cmd = 89142
    sc = 89143
    def apply(self, data, ctx):
        svc = AutoChessService.get_instance(ctx.db)
        svc._require_phase1_game_type(data.get('game_type', 0))
        session = svc.get_session(self.uid)
        replay = session.get('last_replay')
        if not isinstance(replay, dict) or not replay.get('info') or not isinstance(replay.get('actions'), list):
            raise OperationError(2, '当前没有保存的战斗回放')
        return deepcopy(replay)
    def respond(self, result, data, ctx):
        info = result['info']
        return [DownFrame(89143, encode('sc_89143', dict(result=0,replay_info=info))),
                DownFrame(89113, encode('sc_89113', dict(game_type=info['game_type'],battle_uid=info['battle_uid'],
                          battle_info=dict(group_id=1,action_list=result['actions']))))]


@operation
class AutoChessPrepareEndOp(Operation):
    prepare_response_in_transaction = True
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
