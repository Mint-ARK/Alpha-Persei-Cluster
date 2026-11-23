# -*- coding: utf-8 -*-
"""
team_server.py — 关卡现役编队领域服务（编队数据唯一管理者）

- 接受 cs_54030 后按客户端的编队类型、组键和队伍槽位保存最近挑战。
- cs_63006 维持原生上阵保存语义，sc_63005 恢复已保存编队。
- STAGE_PASS 将胜利阵容单独保存在 stage_clear_team，供扫荡发放经验使用。
"""

import json
import time
import logging

from event_bus import bus, Events

logger = logging.getLogger("team_server")

# GetHeroTeamActivityID（p08support.lua:1183）中「按玩法共享关卡键」的 stage_type 集合
# （梦境 10/进阶 100、矩阵 14、公会 Boss 32/33、刻印突破 40、多维 52 等）
_SHARED_KEY_STAGE_TYPES = {10, 100, 14, 32, 33, 40, 52}

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


def battle_reserve_key(info, *, regression=None, special=None, boss=None, mythic=None, mythic_normal=False):
    """Match the stock ReserveParams used by the corresponding selection screens."""
    stage_type, dest = info["type"], info["dest"]
    if stage_type in (12, 13, 16, 19, 20, 21, 76):
        return None  # Tutorial, story experience, and trial formations are temporary.
    if regression:
        return 67, regression["boss_type"], 0
    if special:
        group, stage = special
        cont_id = 4 if stage["stage_type"] == 2 else dest % 10
        team_index = 0
        if group.mode == 4:
            indices = next((row.get("value") or [] for row in info.get("opts", [])
                            if row.get("key") == 2), [])
            team_index = int(indices[0]) if indices else 0
            if not 0 <= team_index < stage["stage_team"]:
                return None
        return 431, cont_id, team_index
    if boss:
        return 102, boss["boss_id"], 0
    if mythic_normal:
        return 103, 1, 0
    if mythic:
        index = int(info.get("index") or 0)
        return (103, 2, mythic["team_id"]) if index else (103, 3, 0)
    if stage_type == 53:
        return 11, 0, 0
    activity_id = hero_team_activity_id(stage_type, dest)
    return (stage_type, activity_id, 0) if activity_id else None


def own_hero_ids(heroes):
    result = []
    for hero in heroes or []:
        if isinstance(hero, dict):
            if int(hero.get("hero_type") or 1) != 1:
                continue
            hero = hero.get("hero_id") or hero.get("id") or 0
        if hero:
            result.append(int(hero))
    return result


def legacy_reserve_key(stage_type, dest):
    """Recover old saved rows when the original submission identifies the native slot."""
    if stage_type == 67 and dest > 2:
        from weekly_challenge_service import get_core_verification_catalog
        stage = get_core_verification_catalog().get("cycle_info", {}).get(str(dest))
        if stage:
            return 67, stage["boss_type"], 0
    if stage_type == 53 and 3070101 <= dest <= 3070126:
        return 11, 0, 0
    if stage_type == 88 and 3092001 <= dest <= 3093199:
        return 431, 4 if dest >= 3093001 else dest % 10, 0
    return None


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
                          team_index=0, add_clear_times=0, activity_key=None,
                          hero_chip_id=0, hero_chips=""):
        """原子保存/更新关卡现役队伍。

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
        if activity_key is None and not activity_id:
            return None
        hero_ids = own_hero_ids(heroes)
        if not hero_ids:
            return None

        now = int(time.time())
        db.execute(
            "INSERT INTO stage_team (uid, stage_type, activity_id, team_index, hero_json, "
            "cooperate_skill, mimir_id, mimir_chips, hero_chip_id, hero_chips, clear_times, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid, stage_type, activity_id, team_index) DO UPDATE SET "
            "hero_json=excluded.hero_json, cooperate_skill=excluded.cooperate_skill, "
            "mimir_id=excluded.mimir_id, mimir_chips=excluded.mimir_chips, "
            "hero_chip_id=excluded.hero_chip_id, hero_chips=excluded.hero_chips, "
            "clear_times = clear_times + ?, update_ts = ?",
            (uid, int(stage_type or 0), activity_id, int(team_index or 0),
             json.dumps(hero_ids), int(cooperate_skill or 0), int(mimir_id or 0),
             str(mimir_chips or ""), int(hero_chip_id or 0), str(hero_chips or ""),
             add_clear_times, now, add_clear_times, now))
        return self.get_stage_team(db, uid, stage_type, dest, team_index=team_index,
                                   activity_key=activity_id)

    def _resolve_key(self, stage_type, dest):
        """(stage_type, dest) → (stage_type, activity_id)"""
        stage_type = int(stage_type or 0)
        return stage_type, hero_team_activity_id(stage_type, dest)

    def get_stage_team(self, db, uid, stage_type, dest, team_index=0, *, activity_key=None):
        """查询关卡现役队伍"""
        if db is None:
            return None
        st, aid = self._resolve_key(stage_type, dest)
        if activity_key is not None:
            aid = int(activity_key)
        elif not aid:
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
        """查询关卡现役队伍的英雄 id 列表，无记录返回 []"""
        rec = self.get_stage_team(db, uid, stage_type, dest, team_index=team_index)
        return list(rec["hero_list"]) if rec else []

    def get_last_clear_heroes(self, db, uid, dest):
        rows = db.query(
            "SELECT hero_json FROM stage_clear_team WHERE uid=? AND stage_id=?",
            (uid, dest))
        return json.loads(rows[0]["hero_json"]) if rows else None

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
                if "battle_team_key" in kwargs:
                    key = kwargs["battle_team_key"]
                    if key is not None:
                        ctx.db.execute(
                            "UPDATE stage_team SET clear_times=clear_times+? "
                            "WHERE uid=? AND stage_type=? AND activity_id=? AND team_index=?",
                            (max(1, int(kwargs.get("times", 1))), uid, *key))
                        if not kwargs.get("sweep"):
                            heroes = own_hero_ids(kwargs.get("hero_details") or kwargs.get("heroes"))
                            now = int(time.time())
                            ctx.db.execute(
                                "INSERT INTO stage_clear_team(uid,stage_id,hero_json,hero_count,"
                                "cooperate_skill,mimir_id,mimir_chips,clear_times,last_clear_ts,update_ts) "
                                "VALUES(?,?,?,?,?,?,?,1,?,?) ON CONFLICT(uid,stage_id) DO UPDATE SET "
                                "hero_json=excluded.hero_json,hero_count=excluded.hero_count,"
                                "cooperate_skill=excluded.cooperate_skill,mimir_id=excluded.mimir_id,"
                                "mimir_chips=excluded.mimir_chips,clear_times=clear_times+1,"
                                "last_clear_ts=excluded.last_clear_ts,update_ts=excluded.update_ts",
                                (uid, stage_id, json.dumps(heroes), len(heroes),
                                 int(kwargs.get("cooperate_skill") or 0),
                                 int(kwargs.get("mimir_id") or 0), kwargs.get("mimir_chips") or "", now, now))
                    return
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
