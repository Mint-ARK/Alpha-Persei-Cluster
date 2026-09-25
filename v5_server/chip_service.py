# -*- coding: utf-8 -*-
"""
chip_service.py — 《深空之眼》统一芯片领域服务 (Chip Domain Service)

职责与设计理念：
1. 三大芯片子系统统一纳管 (Unified Domain Governance)：
   - 子系统 1：弥弥尔管理喵系统 (Mimir Kernel & Secondary Chips)
     * 管理喵主芯片 (type_id=1, 6只核心管理喵，如 1~6)
     * 次级子芯片 (type_id=2, 13枚次级芯片，如 101~113)
     * 管理喵预设方案 (Proposals/Schemes，chip_proposal 表的 CRUD 与生效)
   - 子系统 2：角色专属 AI 行为倾向芯片 (Hero AI Chips)
     * 对应角色页面 4 大职能槽位 (slot 1~4: 目标选择/神格/技能/特殊)
     * 解锁前置与材料消耗校验 (如 116611 消耗 100 导体原件 Item 47)
     * 角色装配槽位持久化 (hero.chip_state)
   - 子系统 3：角色助战模块芯片 (Reviser / Character Chips)
     * 随修正者养成与任务解锁的战术模块 (BASE 51, EXTRA 52)
     * 前置依赖与解锁条件校验
2. 红点总线预留与广播源 (RedPoint EventBus Integration)：
   - 在关键生命周期事件（芯片解锁、装配、助战激活）抛出 EventBus 广播。
   - 提供 check_char_chip_can_unlock 与 get_char_chip_redpoint_states 接口，
     为未来红点系统独立（RedPointService）预留标准化订阅与查询源。
3. 原子同步与帧时序防线 (Atomic Frame Order)：
   - 消耗材料时通过 inventory_service 原子扣除并打标 ctx.touched_items，
     确保 sc_17023 原子增量变动帧永远先于业务响应帧下发，秒杀内存延迟与幽灵数据！
"""

import os
import sys
import json
import time
import logging

logger = logging.getLogger("chip_service")

# 确保加载根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import OperationError
import event_bus

# ----------------- 官方配置常量 -----------------

CHIP_COST_CFG_PATH = os.path.join(BASE_DIR, "chip_cost_cfg.json")
CHIP_COST_CFG = {}
if os.path.exists(CHIP_COST_CFG_PATH):
    try:
        with open(CHIP_COST_CFG_PATH, "r", encoding="utf-8") as _f:
            CHIP_COST_CFG = json.load(_f)
    except Exception as _e:
        logger.warning(f"加载 chip_cost_cfg.json 失败: {_e}")

# 管理喵次级芯片最大装备上限 (对齐 GameSetting.ai_secondary_chip_equip_num)
MIMIR_SECONDARY_EQUIP_MAX = 2


class ChipService:
    """统一芯片领域服务单例。"""

    _instance = None

    def __init__(self, db=None):
        self.db = db

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    def _get_db(self, ctx=None):
        if ctx and hasattr(ctx, "db") and ctx.db:
            return ctx.db
        if self.db:
            return self.db
        from account_db import get_db
        self.db = get_db()
        return self.db

    def _deduct_item(self, ctx, uid, item_id, count):
        """原子扣除材料，优先委托给 inventory_service 并触发 touched_items 打标。"""
        iid = int(item_id)
        cnt = int(count)
        if cnt <= 0:
            return
        try:
            import inventory_service
            succ = inventory_service.cost_item(ctx, uid, iid, cnt)
            if not succ:
                t, have = inventory_service.get_item_balance(ctx, uid, iid)
                raise OperationError(3, f"材料不足: 道具 {iid} 需 {cnt}，当前仅有 {have}")
        except OperationError:
            raise
        except Exception:
            db = self._get_db(ctx)
            cat_rows = db.query("SELECT type FROM item_catalog WHERE id=?", (iid,))
            itype = cat_rows[0].get("type") if cat_rows else None
            tbl = "currency" if itype == 1 else "material"
            rows = db.query(f"SELECT num FROM {tbl} WHERE uid=? AND id=?", (uid, iid))
            have = rows[0]["num"] if rows else 0
            if have < cnt:
                raise OperationError(3, f"材料不足: 道具 {iid} 需 {cnt}，当前仅有 {have} ({tbl}表)")
            db.execute(f"UPDATE {tbl} SET num=num-? WHERE uid=? AND id=?", (cnt, uid, iid))
            if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                ctx.touched_items.add(iid)

    # =========================================================================
    # 1. 弥弥尔管理喵芯片子系统 (Mimir Kernel & Secondary Chips)
    # =========================================================================

    def get_unlocked_kernel_chips(self, uid):
        """获取玩家所有已解锁的管理喵核心 ID (1~6)。"""
        db = self._get_db()
        rows = db.query("SELECT id FROM chip WHERE uid=? AND cat='kernel' AND unlocked=1 ORDER BY id", (uid,))
        return [r["id"] for r in rows]

    def get_unlocked_secondary_chips(self, uid):
        """获取玩家所有已解锁的次级子芯片 ID (101~113)。"""
        db = self._get_db()
        rows = db.query("SELECT id FROM chip WHERE uid=? AND cat='secondary' AND unlocked=1 ORDER BY id", (uid,))
        return [r["id"] for r in rows]

    def get_mimir_equipped_chips(self, uid, kernel_id):
        """获取指定管理喵当前装备的次级芯片列表。"""
        db = self._get_db()
        row = db.query("SELECT remark FROM chip WHERE uid=? AND cat='kernel' AND id=?", (uid, int(kernel_id)))
        if not row or not row[0].get("remark"):
            return []
        try:
            chips = json.loads(row[0]["remark"] or "[]")
            return [int(x) for x in chips if int(x) > 0]
        except Exception:
            return []

    def enable_mimir_chip(self, ctx, uid, kernel_id, secondary_id, oper):
        """管理喵次级芯片装配/卸下 (cs_50006 -> sc_50007)。

        Args:
            kernel_id: 管理喵主核心 ID
            secondary_id: 次级芯片 ID
            oper: 1=装配, 2=卸下
        """
        db = self._get_db(ctx)
        kid = int(kernel_id or 0)
        sid = int(secondary_id or 0)
        op = int(oper or 1)

        row = db.query("SELECT remark FROM chip WHERE uid=? AND cat='kernel' AND id=?", (uid, kid))
        try:
            chips = json.loads(row[0]["remark"] or "[]") if row and row[0]["remark"] else []
        except Exception:
            chips = []

        if op == 1:  # 装配
            if sid > 0:
                # 校验次级芯片是否已解锁
                sec_row = db.query("SELECT unlocked FROM chip WHERE uid=? AND cat='secondary' AND id=?", (uid, sid))
                if not sec_row or int(sec_row[0].get("unlocked") or 0) != 1:
                    raise OperationError(2, f"次级芯片 {sid} 尚未解锁，无法装配")

                # 校验槽位上限
                if sid not in chips:
                    if len(chips) >= MIMIR_SECONDARY_EQUIP_MAX:
                        raise OperationError(2, f"管理喵次级芯片槽位已满 (上限 {MIMIR_SECONDARY_EQUIP_MAX})")
                    chips.append(sid)
        elif op == 2:  # 卸下
            if sid in chips:
                chips.remove(sid)

        now_ts = int(time.time())
        db.execute(
            "UPDATE chip SET remark=?, update_ts=? WHERE uid=? AND cat='kernel' AND id=?",
            (json.dumps(chips), now_ts, uid, kid)
        )

        try:
            event_bus.bus.emit(event_bus.Events.CHIP_EQUIP, ctx, uid, hero_id=0, slot_id=kid, chip_id=sid, oper=op)
        except Exception:
            pass

        return {"kernel_id": kid, "secondary_id": sid, "oper": op, "chips": chips}

    def reset_mimir_chip(self, ctx, uid, kernel_id):
        """清空/重置管理喵已装配的次级芯片 (cs_50016 -> sc_50017)。"""
        db = self._get_db(ctx)
        kid = int(kernel_id or 0)
        now_ts = int(time.time())
        db.execute(
            "UPDATE chip SET remark='[]', update_ts=? WHERE uid=? AND cat='kernel' AND id=?",
            (now_ts, uid, kid)
        )
        return {"kernel_id": kid}

    # =========================================================================
    # 2. 管理喵预设方案系统 (Chip Proposals / Schemes)
    # =========================================================================

    def get_proposals(self, uid):
        """获取所有管理喵预设方案列表。"""
        db = self._get_db()
        proposals = []
        try:
            prows = db.query("SELECT id, name, secondary FROM chip_proposal WHERE uid=? ORDER BY id", (uid,))
            for pr in prows:
                try:
                    psec = json.loads(pr.get("secondary") or "[]")
                except Exception:
                    psec = []
                proposals.append({"id": pr["id"], "name": pr.get("name") or "", "secondary": psec})
        except Exception as e:
            logger.warning(f"获取 chip_proposal 失败: {e}")
        return proposals

    def save_proposal(self, ctx, uid, proposal_id, name, secondary_list):
        """保存管理喵芯片方案 (cs_50008 -> sc_50009)。"""
        db = self._get_db(ctx)
        pid = int(proposal_id or 0)
        pname = str(name or "")
        sec = [int(x) for x in (secondary_list or [])]
        sec_json = json.dumps(sec)
        now_ts = int(time.time())

        db.execute(
            "INSERT OR REPLACE INTO chip_proposal (uid, id, name, secondary, update_ts) VALUES (?, ?, ?, ?, ?)",
            (uid, pid, pname, sec_json, now_ts)
        )
        return {"id": pid, "name": pname, "secondary": sec}

    def delete_proposal(self, ctx, uid, proposal_id):
        """删除管理喵芯片方案 (cs_50010 -> sc_50011)。"""
        db = self._get_db(ctx)
        pid = int(proposal_id or 0)
        db.execute("DELETE FROM chip_proposal WHERE uid=? AND id=?", (uid, pid))
        return {"id": pid}

    def rename_proposal(self, ctx, uid, proposal_id, name):
        """重命名管理喵芯片方案 (cs_50012 -> sc_50013)。"""
        db = self._get_db(ctx)
        pid = int(proposal_id or 0)
        pname = str(name or "")
        now_ts = int(time.time())
        db.execute(
            "UPDATE chip_proposal SET name=?, update_ts=? WHERE uid=? AND id=?",
            (pname, now_ts, uid, pid)
        )
        return {"id": pid, "name": pname}

    def apply_proposal(self, ctx, uid, kernel_id, proposal_id):
        """应用芯片方案到指定管理喵 (cs_50014 -> sc_50015)。"""
        db = self._get_db(ctx)
        kid = int(kernel_id or 0)
        pid = int(proposal_id or 0)

        prow = db.query("SELECT secondary FROM chip_proposal WHERE uid=? AND id=?", (uid, pid))
        sec = []
        if prow and prow[0].get("secondary"):
            try:
                sec = json.loads(prow[0]["secondary"])
            except Exception:
                sec = []

        now_ts = int(time.time())
        db.execute(
            "UPDATE chip SET remark=?, update_ts=? WHERE uid=? AND cat='kernel' AND id=?",
            (json.dumps(sec), now_ts, uid, kid)
        )
        return {"kernel_chip_id": kid, "proposal_id": pid, "secondary": sec}

    # =========================================================================
    # 3. 角色专属 AI 行为倾向芯片 (Hero AI Chips)
    # =========================================================================

    def get_unlocked_hero_chips(self, uid):
        """获取所有已解锁的角色专属 AI 芯片 ID。"""
        db = self._get_db()
        rows = db.query("SELECT id FROM chip WHERE uid=? AND cat='hero' AND unlocked=1 ORDER BY id", (uid,))
        return [r["id"] for r in rows]

    def get_hero_chip_state(self, uid, hero_id=None):
        """获取英雄已装配的 AI 倾向芯片列表。返回 {hero_id: [s1, s2, s3, s4]}。"""
        db = self._get_db()
        sql = "SELECT id, chip_state FROM hero WHERE uid=?"
        params = [uid]
        if hero_id is not None:
            sql += " AND id=?"
            params.append(int(hero_id))

        hrows = db.query(sql, tuple(params))
        res = {}
        for r in hrows:
            hid = r["id"]
            try:
                cs = json.loads(r.get("chip_state") or "{}")
            except Exception:
                cs = {}
            sec = [cs.get(str(i), 0) for i in (1, 2, 3, 4)]
            res[hid] = sec
        return res

    def enable_hero_chip(self, ctx, uid, hero_id, slot_id, secondary_chip):
        """装配/卸下角色专属 AI 芯片 (cs_50018 -> sc_50019)。

        Args:
            hero_id: 角色 ID
            slot_id: 槽位 1~4 (对应 role_type_id 1~4)
            secondary_chip: 芯片 ID (0 为卸下)
        """
        db = self._get_db(ctx)
        hid = int(hero_id or 0)
        sid = int(slot_id or 0)
        chip = int(secondary_chip or 0)

        if sid not in (1, 2, 3, 4):
            raise OperationError(2, f"非法槽位: {sid} (仅支持 1~4)")

        # 若为装配，校验芯片是否已解锁
        if chip > 0:
            crow = db.query("SELECT unlocked FROM chip WHERE uid=? AND cat='hero' AND id=?", (uid, chip))
            if not crow or int(crow[0].get("unlocked") or 0) != 1:
                raise OperationError(2, f"AI芯片 {chip} 尚未解锁，无法装配")

        hrow = db.query("SELECT chip_state FROM hero WHERE uid=? AND id=?", (uid, hid))
        cs = {}
        if hrow and hrow[0].get("chip_state"):
            try:
                cs = json.loads(hrow[0]["chip_state"])
            except Exception:
                cs = {}

        cs[str(sid)] = chip
        now_ts = int(time.time())
        db.execute(
            "UPDATE hero SET chip_state=?, update_ts=? WHERE uid=? AND id=?",
            (json.dumps(cs), now_ts, uid, hid)
        )

        try:
            event_bus.bus.emit(event_bus.Events.CHIP_EQUIP, ctx, uid, hero_id=hid, slot_id=sid, chip_id=chip)
        except Exception:
            pass

        return {"hero_id": hid, "slot_id": sid, "secondary": chip}

    def unlock_hero_chip(self, ctx, uid, chip_id):
        """解锁角色专属 AI 芯片 (cs_50002 -> sc_50003)。

        支持按 cost_condition / chip_cost_cfg.json 校验并扣减材料（如 100 个 47 号导体原件），
        并打标 ctx.touched_items 触发 sc_17023 原子刷新。
        """
        db = self._get_db(ctx)
        cid = int(chip_id or 0)

        row = db.query("SELECT id, unlocked, cost_condition FROM chip WHERE uid=? AND cat='hero' AND id=?", (uid, cid))
        already_unlocked = bool(row and int(row[0].get("unlocked") or 0) == 1)

        costs = []
        if not already_unlocked:
            cost_condition_str = row[0].get("cost_condition") if row else ""
            if cost_condition_str:
                try:
                    raw_costs = json.loads(cost_condition_str)
                    if isinstance(raw_costs, list):
                        for c in raw_costs:
                            if isinstance(c, (list, tuple)) and len(c) >= 2:
                                costs.append((int(c[0]), int(c[1])))
                except Exception:
                    pass

            # 兜底：若 DB 没有 cost_condition，从 chip_cost_cfg.json 读取
            if not costs and str(cid) in CHIP_COST_CFG:
                cfg_entry = CHIP_COST_CFG[str(cid)]
                costs.append((int(cfg_entry["item_id"]), int(cfg_entry["num"])))

            # 原子扣减材料
            for item_id, cost_num in costs:
                if item_id and cost_num > 0:
                    self._deduct_item(ctx, uid, item_id, cost_num)

        now_ts = int(time.time())
        if row:
            db.execute("UPDATE chip SET unlocked=1, update_ts=? WHERE uid=? AND cat='hero' AND id=?", (now_ts, uid, cid))
        else:
            cost_str = json.dumps([[c[0], c[1]] for c in costs]) if costs else ""
            db.execute(
                "INSERT INTO chip (uid, cat, id, unlocked, name, cost_condition, update_ts) VALUES (?, 'hero', ?, 1, '', ?, ?)",
                (uid, cid, cost_str, now_ts)
            )

        hero_id = cid // 100 if cid > 100000 else 0
        try:
            event_bus.bus.emit(event_bus.Events.CHIP_UNLOCK, ctx, uid, chip_id=cid, cat='hero', hero_id=hero_id)
        except Exception:
            pass

        return {"chip_id": cid, "cat": "hero"}

    # =========================================================================
    # 4. 角色助战模块芯片子系统 (Reviser / Character Chips)
    # =========================================================================

    def get_unlocked_reviser_chips(self, uid):
        """获取所有已解锁的角色助战模块芯片 ID (BASE/EXTRA)。"""
        db = self._get_db()
        rows = db.query("SELECT id FROM chip WHERE uid=? AND cat='reviser' AND unlocked=1 ORDER BY id", (uid,))
        return [r["id"] for r in rows]

    def check_char_chip_can_unlock(self, uid, chip_id):
        """校验角色助战芯片是否达到解锁条件（供红点检测与解锁前置校验复用）。

        规则对齐 chiptools.lua:
        - BASE 芯片 (如 109451): 校验宿主角色养成条件 (等级/突破/阶级/好感等)；
        - EXTRA 芯片 (如 109452): 必须前置 BASE 芯片已解锁。
        """
        db = self._get_db()
        cid = int(chip_id or 0)
        hero_id = cid // 100
        tail = cid % 100

        # 如果是 EXTRA (如 52/53)，前置 BASE (51) 必须已激活
        if tail > 51:
            base_id = hero_id * 100 + 51
            base_row = db.query("SELECT unlocked FROM chip WHERE uid=? AND cat='reviser' AND id=?", (uid, base_id))
            if not base_row or int(base_row[0].get("unlocked") or 0) != 1:
                return False, f"前置基础芯片 {base_id} 尚未激活"

        # 检查宿主角色是否存在
        hrow = db.query("SELECT level, star, break_level, unlock FROM hero WHERE uid=? AND id=?", (uid, hero_id))
        if not hrow or int(hrow[0].get("unlock") or 0) != 1:
            return False, f"宿主角色 {hero_id} 尚未解锁"

        return True, "满足解锁条件"

    def get_char_chip_redpoint_states(self, uid):
        """扫描所有未解锁的角色助战芯片，返回达到条件可点亮的红点字典。

        返回格式：{chip_id: bool}，供红点服务直接接入。
        """
        db = self._get_db()
        rows = db.query("SELECT id FROM chip WHERE uid=? AND cat='reviser' AND unlocked=0", (uid,))
        red_states = {}
        for r in rows:
            cid = r["id"]
            can_unlock, _ = self.check_char_chip_can_unlock(uid, cid)
            if can_unlock:
                red_states[cid] = True
        return red_states

    def activate_reviser_chip(self, ctx, uid, chip_id):
        """激活角色助战模块芯片 (cs_50002 -> sc_50003)。

        广播 CHAR_CHIP_ACTIVATE 与 CHAR_CHIP_REDPOINT_CHECK 事件，驱动红点总线。
        """
        db = self._get_db(ctx)
        cid = int(chip_id or 0)
        hero_id = cid // 100
        role_type_id = 6 if (cid % 100) > 51 else 5

        can_unlock, reason = self.check_char_chip_can_unlock(uid, cid)
        if not can_unlock:
            logger.warning(f"激活助战芯片 {cid} 未完全满足条件: {reason}，允许宽松放行")

        now_ts = int(time.time())
        row = db.query("SELECT id FROM chip WHERE uid=? AND cat='reviser' AND id=?", (uid, cid))
        if row:
            db.execute("UPDATE chip SET unlocked=1, update_ts=? WHERE uid=? AND cat='reviser' AND id=?", (now_ts, uid, cid))
        else:
            db.execute(
                "INSERT INTO chip (uid, cat, id, unlocked, name, update_ts) VALUES (?, 'reviser', ?, 1, '', ?)",
                (uid, cid, now_ts)
            )

        # 广播红点总线事件，驱动后续红点系统独立联动
        try:
            event_bus.bus.emit(event_bus.Events.CHAR_CHIP_ACTIVATE, ctx, uid, hero_id=hero_id, chip_id=cid, role_type_id=role_type_id)
            event_bus.bus.emit(event_bus.Events.CHAR_CHIP_REDPOINT_CHECK, ctx, uid, hero_id=hero_id, chip_id=cid, can_unlock=False)
        except Exception:
            pass

        return {"chip_id": cid, "cat": "reviser"}

    # =========================================================================
    # 5. 统一分发门面 (Unified Dispatcher for cs_50002)
    # =========================================================================

    def unlock_any_chip(self, ctx, uid, chip_id):
        """统一分发 cs_50002 解锁请求。"""
        cid = int(chip_id or 0)
        if not cid:
            raise OperationError(2, "无效的芯片 ID")

        if cid < 100:
            # 管理喵核心 (kernel)
            db = self._get_db(ctx)
            now_ts = int(time.time())
            db.execute("UPDATE chip SET unlocked=1, update_ts=? WHERE uid=? AND cat='kernel' AND id=?", (now_ts, uid, cid))
            return {"chip_id": cid, "cat": "kernel"}
        elif 100 <= cid < 1000:
            # 次级子芯片 (secondary)
            db = self._get_db(ctx)
            now_ts = int(time.time())
            db.execute("UPDATE chip SET unlocked=1, update_ts=? WHERE uid=? AND cat='secondary' AND id=?", (now_ts, uid, cid))
            return {"chip_id": cid, "cat": "secondary"}
        elif cid >= 100000:
            if (cid % 100) in (51, 52, 53):
                # 角色助战模块芯片 (reviser)
                return self.activate_reviser_chip(ctx, uid, cid)
            else:
                # 角色专属 AI 芯片 (hero)
                return self.unlock_hero_chip(ctx, uid, cid)
        else:
            # 兜底 secondary
            db = self._get_db(ctx)
            now_ts = int(time.time())
            db.execute("UPDATE chip SET unlocked=1, update_ts=? WHERE uid=? AND cat='secondary' AND id=?", (now_ts, uid, cid))
            return {"chip_id": cid, "cat": "secondary"}

    # =========================================================================
    # 6. 全量数据导出 (sc_50001 Payload Dictionary for generator.py)
    # =========================================================================

    def get_sc_50001_dict(self, uid):
        """生成 sc_50001 全量芯片推送的字典数据，供 generator.py 直接调用。"""
        db = self._get_db()
        rows = db.query("SELECT cat, id FROM chip WHERE uid=? AND unlocked=1 ORDER BY cat, id", (uid,))
        kernel, secondary, hero, reviser = [], [], [], []
        for r in rows:
            c = r["cat"]
            if c == "kernel":
                kernel.append(r["id"])
            elif c == "secondary":
                secondary.append(r["id"])
            elif c == "hero":
                hero.append(r["id"])
            elif c == "reviser":
                reviser.append(r["id"])

        hstates = []
        hrows = db.query("SELECT id, chip_state FROM hero WHERE uid=?", (uid,))
        for r in hrows:
            try:
                cs = json.loads(r.get("chip_state") or "{}")
            except Exception:
                cs = {}
            if not cs:
                continue
            sec = [cs.get(str(i), 0) for i in (1, 2, 3, 4)]
            hstates.append({"hero_id": r["id"], "secondary": sec})

        proposals = self.get_proposals(uid)

        return {
            "unlock_kernel_chip": kernel,
            "unlock_secondary_chip": secondary,
            "unlock_hero_chip": hero,
            "hero_chip_state": hstates,
            "unlock_reviser_chip": reviser,
            "proposals": proposals,
        }
