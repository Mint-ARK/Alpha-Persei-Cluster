# -*- coding: utf-8 -*-
"""
team_server.py — 关卡现役编队领域服务（编队数据唯一管理者）

官方生命周期（decompiled_v2 协议分析定稿）：
- 客户端选人界面的编队按关卡键 GetHeroTeamActivityID(type, dest) 分立（BattleTeamData.battleTeam_ 内存缓存）
- 出战 cs_54030 上报当前关卡的完整编队（hero_list 次序不可乱、连携、弥弥尔+芯片）
- 矩阵/多维的连携选择额外 Push(63006) 上阵即存
- 服务器按 (uid, stage_type, activity_id, team_index) 原子保存「每关一条现役队伍」，
  再次出战/通关直接覆盖更新（不保留历史版本），跨会话为客户端 63005 初始化提供底稿

职责：
- record_stage_team：出战/63006/结算统一写入入口（试用英雄 hero_type=2 不持久化）
- get_stage_team / get_activity_id：关卡现役队伍查询与关卡键换算（扫荡兜底/63005 组帧用）
- 订阅 STAGE_PASS：胜利结算覆盖更新现役队伍并累计通关次数
"""

import json
import time
import logging

from event_bus import bus, Events

logger = logging.getLogger("team_server")

# GetHeroTeamActivityID（p08support.lua:1183）中「按玩法共享关卡键」的 stage_type 集合
# （梦境 10/进阶 100、矩阵 14、公会 Boss 32/33、刻印突破 40、多维 52 等）
_SHARED_KEY_STAGE_TYPES = {10, 100, 14, 32, 33, 40, 52}

# 试用英雄类型（客户端 battlecontroller 组装：hero_type=2 为试玩/援助位，不持久化）
_TRIAL_HERO_TYPE = 2


def hero_team_activity_id(stage_type, dest):
    """复刻客户端 GetHeroTeamActivityID(stage_type, dest)：
    - 共享型玩法 → 返回 stage_type（该玩法全体关卡共用一个编队键）
    - 其余（普通主线/材料/支线/活动等）→ 返回 dest 本身（每关原子分立）
    """
    stage_type = int(stage_type or 0)
    dest = int(dest or 0)
    if not dest:
        return 0
    if stage_type in _SHARED_KEY_STAGE_TYPES:
        return stage_type
    return dest


class TeamServer:
    """关卡现役编队服务单例"""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._init_event_listeners()
        return cls._instance

    def __init__(self):
        pass

    # ---------------- 核心读写 ----------------

    def record_stage_team(self, ctx, uid, stage_type, dest, heroes=None,
                          cooperate_skill=0, mimir_id=0, mimir_chips="",
                          team_index=0, add_clear_times=0, activity_key=None):
        """原子保存/更新关卡现役队伍（出战、63006 上阵即存、胜利结算三路统一入口）。

        heroes: 出战修正者列表（保持上报次序，[0]=队长位；元素为 int、{"id": x} 或
                {"hero_id": x, "hero_type": n}——hero_type=2 试用英雄不持久化）
        dest: 关卡 id（与 activity_key 二选一）
        activity_key: 客户端直传的关卡键（cs_63006 的 cont_id），优先于 dest 换算
        add_clear_times: 胜利结算传 1（累计通关次数）；出战/63006 传 0（仅覆盖阵容）
        返回更新后的记录 dict（无有效英雄返回 None）。
        """
        db = getattr(ctx, "db", None)
        if db is None:
            return None
        stage_type = int(stage_type or 0)
        if activity_key is not None:
            activity_id = int(activity_key or 0)
        else:
            activity_id = hero_team_activity_id(stage_type, dest)
        if not activity_id:
            return None

        hero_ids = []
        for h in (heroes or []):
            if isinstance(h, dict):
                if int(h.get("hero_type") or 1) == _TRIAL_HERO_TYPE:
                    continue  # 试用英雄不持久化
                hid = int(h.get("hero_id") or h.get("id") or 0)
            else:
                hid = int(h)
            if hid:
                hero_ids.append(hid)
        if not hero_ids:
            return None

        now = int(time.time())
        db.execute(
            "INSERT INTO stage_team (uid, stage_type, activity_id, team_index, hero_json, "
            "cooperate_skill, mimir_id, mimir_chips, hero_chips, clear_times, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid, stage_type, activity_id, team_index) DO UPDATE SET "
            "hero_json=excluded.hero_json, cooperate_skill=excluded.cooperate_skill, "
            "mimir_id=excluded.mimir_id, mimir_chips=excluded.mimir_chips, "
            "hero_chips=excluded.hero_chips, "
            "clear_times = clear_times + ?, update_ts = ?",
            (uid, int(stage_type or 0), activity_id, int(team_index or 0),
             json.dumps(hero_ids), int(cooperate_skill or 0), int(mimir_id or 0),
             str(mimir_chips or ""), "", add_clear_times, now, add_clear_times, now))
        return self.get_stage_team(db, uid, stage_type, dest, team_index=team_index)

    def _resolve_key(self, stage_type, dest):
        """(stage_type, dest) → (stage_type, activity_id)"""
        stage_type = int(stage_type or 0)
        return stage_type, hero_team_activity_id(stage_type, dest)

    def get_stage_team(self, db, uid, stage_type, dest, team_index=0):
        """查询关卡现役队伍"""
        if db is None:
            return None
        st, aid = self._resolve_key(stage_type, dest)
        if not aid:
            return None
        rows = db.query(
            "SELECT * FROM stage_team WHERE uid=? AND stage_type=? AND activity_id=? AND team_index=?",
            (uid, st, aid, int(team_index or 0)))
        if not rows:
            return None
        r = dict(rows[0])
        try:
            r["hero_list"] = json.loads(r.get("hero_json") or "[]")
        except Exception:
            r["hero_list"] = []
        return r

    def get_stage_heroes(self, db, uid, stage_type, dest, team_index=0):
        """查询关卡现役队伍的英雄 id 列表（扫荡兜底用），无记录返回 []"""
        rec = self.get_stage_team(db, uid, stage_type, dest, team_index=team_index)
        return list(rec["hero_list"]) if rec else []

    def list_teams(self, db, uid, limit=200):
        """列出玩家全部关卡现役编队（控制台/GM 用）"""
        if db is None:
            return []
        rows = db.query(
            "SELECT stage_type, activity_id, team_index, hero_json, cooperate_skill, "
            "mimir_id, mimir_chips, clear_times, update_ts FROM stage_team "
            "WHERE uid=? ORDER BY update_ts DESC LIMIT ?", (uid, int(limit)))
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["hero_list"] = json.loads(item.get("hero_json") or "[]")
            except Exception:
                item["hero_list"] = []
            out.append(item)
        return out

    # ---------------- 事件订阅 ----------------

    def _init_event_listeners(self):
        @bus.subscribe(Events.STAGE_PASS)
        def on_stage_pass(ctx, uid, **kwargs):
            stage_id = kwargs.get("stage_id") or 0
            if not stage_id:
                return
            try:
                rec = self.record_stage_team(
                    ctx, uid, int(kwargs.get("stage_type_id") or 0), int(stage_id),
                    heroes=kwargs.get("heroes") or [],
                    cooperate_skill=kwargs.get("cooperate_skill") or 0,
                    mimir_id=kwargs.get("mimir_id") or 0,
                    mimir_chips=kwargs.get("mimir_chips") or "",
                    add_clear_times=max(1, int(kwargs.get("times", 1))))
                if rec and logger.isEnabledFor(logging.INFO):
                    logger.info(
                        f"[TeamServer] 关卡 {stage_id} 现役队伍已更新: "
                        f"heroes={rec.get('hero_list')} (队长={rec['hero_list'][0] if rec.get('hero_list') else 0}) "
                        f"连携={rec.get('cooperate_skill')} 弥弥尔={rec.get('mimir_id')} 通关次数={rec.get('clear_times')}")
            except Exception as e:
                logger.warning(f"[TeamServer] 现役队伍记录异常 stage={stage_id}: {e}")


if __name__ == "__main__":
    TeamServer.get_instance()
    print("[TeamServer] 自检: 单例与监听器装配完成")
