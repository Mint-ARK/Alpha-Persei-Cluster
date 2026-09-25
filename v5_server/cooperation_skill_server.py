# -*- coding: utf-8 -*-
"""
cooperation_skill_server.py — 连携技能出场计量领域服务

职责（BS 结算广播架构）：
- 订阅 STAGE_PASS（战斗/扫荡胜利统一广播），提取 cooperate_skill 与 stage_kind
- 对 combo_skill_counter 计量表按 (uid, combo_id) 累计：total_times / normal_times / hard_times
- 计量变动后对外广播 COMBO_SKILL_PROGRESS（连携ID、所属角色id、变动后总次数、
  普通关卡数值、任务需要的特定高难关卡数值）
- trust_service 订阅 COMBO_SKILL_PROGRESS 用于连携技能升级进度与红点管理

计量口径（combo_stage_kind ← _settle_frames 关卡分类）：
- dream_boss  → 梦境再构魇渊难度 Boss（type3 条件行）
- mythic_deep → 黑区净化高难（type4 条件行）
- normal      → 任意普通关卡（type2 条件行；当前官方 lv>=1 条件组无 type2 行，仅计量）
"""

import os
import time
import logging

from event_bus import bus, Events

logger = logging.getLogger("cooperation_skill_server")

# stage_kind → 计量分列（hard/normal）；dream_boss/mythic_deep 计 hard，其余计 normal
_HARD_KINDS = {"dream_boss", "mythic_deep"}


class CooperationSkillServer:
    """连携技能出场计量服务单例"""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._init_event_listeners()
        return cls._instance

    def __init__(self):
        self._combo_cfg = {}      # {combo_id: cooperate_role_ids}（连携所属角色）
        self._cfg_loaded = False

    # ---------------- 配置 ----------------

    def _load_combo_roles(self):
        """读取 ComboSkillCfg 的合作英雄映射（trust_cfg.json 由 TrustService 抽取维护）"""
        if self._cfg_loaded:
            return self._combo_cfg
        self._cfg_loaded = True
        try:
            import json
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trust_cfg.json")
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in (cfg.get("combo_skill_cfg") or {}).items():
                self._combo_cfg[int(k)] = [int(x) for x in (v.get("cooperate_role_ids") or [])]
        except Exception as e:
            logger.warning(f"[CoopSkillServer] 加载连携角色映射失败: {e}")
        return self._combo_cfg

    # ---------------- 计量核心 ----------------

    def record_stage_clear(self, ctx, uid, combo_id, stage_kind="normal", times=1):
        """连携出场计量 +1（times 按战斗倍数），变动后广播 COMBO_SKILL_PROGRESS

        返回变动后的计量 dict（无连携/无变动返回 None）。
        """
        db = getattr(ctx, "db", None)
        combo_id = int(combo_id or 0)
        if db is None or combo_id <= 0:
            return None
        times = max(1, int(times or 1))
        kind = "hard" if stage_kind in _HARD_KINDS else "normal"
        now = int(time.time())

        col = "hard_times" if kind == "hard" else "normal_times"
        db.execute(
            f"INSERT INTO combo_skill_counter (uid, combo_id, total_times, normal_times, hard_times, update_ts) "
            f"VALUES (?, ?, ?, ?, ?, ?) "
            f"ON CONFLICT(uid, combo_id) DO UPDATE SET "
            f"total_times = total_times + ?, {col} = {col} + ?, update_ts = ?",
            (uid, combo_id, times, times if kind == "normal" else 0,
             times if kind == "hard" else 0, now, times, times, now))

        counter = self.get_counter(db, uid, combo_id)
        # 对外广播变动：连携ID、所属角色id、变动后总次数、普通关卡数值、高难关卡数值
        bus.emit(Events.COMBO_SKILL_PROGRESS, ctx, uid,
                 combo_id=combo_id,
                 hero_ids=list(self._load_combo_roles().get(combo_id, [])),
                 total_times=counter["total_times"],
                 normal_times=counter["normal_times"],
                 hard_times=counter["hard_times"],
                 stage_kind=stage_kind,
                 combo_stage_kind=stage_kind)
        return counter

    def get_counter(self, db, uid, combo_id):
        """查询连携计量 {combo_id, total_times, normal_times, hard_times}"""
        counter = {"combo_id": int(combo_id), "total_times": 0,
                   "normal_times": 0, "hard_times": 0}
        if db is None:
            return counter
        rows = db.query(
            "SELECT total_times, normal_times, hard_times FROM combo_skill_counter "
            "WHERE uid=? AND combo_id=?", (uid, int(combo_id)))
        if rows:
            counter["total_times"] = int(rows[0]["total_times"] or 0)
            counter["normal_times"] = int(rows[0]["normal_times"] or 0)
            counter["hard_times"] = int(rows[0]["hard_times"] or 0)
        return counter

    def get_all_counters(self, db, uid):
        """全量连携计量列表（控制台/GM 用）"""
        if db is None:
            return []
        rows = db.query(
            "SELECT combo_id, total_times, normal_times, hard_times FROM combo_skill_counter "
            "WHERE uid=? ORDER BY combo_id", (uid,))
        return [{"combo_id": int(r["combo_id"]),
                 "total_times": int(r["total_times"] or 0),
                 "normal_times": int(r["normal_times"] or 0),
                 "hard_times": int(r["hard_times"] or 0)} for r in rows]

    # ---------------- 事件订阅 ----------------

    def _init_event_listeners(self):
        @bus.subscribe(Events.STAGE_PASS)
        def on_stage_pass(ctx, uid, **kwargs):
            combo_id = kwargs.get("cooperate_skill") or 0
            if not combo_id:
                return
            try:
                self.record_stage_clear(ctx, uid, int(combo_id),
                                        stage_kind=kwargs.get("combo_stage_kind") or "normal",
                                        times=max(1, int(kwargs.get("times", 1))))
            except Exception as e:
                logger.warning(f"[CoopSkillServer] 连携计量异常 combo={combo_id}: {e}")


if __name__ == "__main__":
    CooperationSkillServer.get_instance()
    print("[CoopSkillServer] 自检: 单例与监听器装配完成")
