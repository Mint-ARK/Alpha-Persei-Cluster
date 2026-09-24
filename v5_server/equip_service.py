# -*- coding: utf-8 -*-
"""
equip_service.py — 《深空之眼》统一刻印资产领域服务 (Equip Domain Service)

职责与设计理念：
1. 独立资产实体（Asset Entity）：
   - 刻印属于独立资产实体（背包独立存放、独立等级/经验/突破/赋能/重构神系/专属修正者绑定）。
   - 修正者（Hero）与刻印通过孔位映射（hero.equip_slot <-> equip.hero_id）保持解耦与双向同步。
2. 累计经验与突破对齐（Cumulative Exp & Breakthrough）：
   - 刻印 exp 恒为从 1 级开始的全局累计经验值（对齐客户端 equipexpcfg.lua）。
   - 突破只提升等级上限，累计经验永不清零。
   - 修复官方一键强化时序缺陷：先突破放开上限，再注入经验，多余经验按材料价值（40803/40802/40801）
     通过 material_give_back 原路全量返还给玩家，彻底杜绝材料吞噬与负值异常！
3. 刻印继承全新落地（Equip Inherit, 13052 -> 13053）：
   - 5星刻印跨套装转换，严格保留等级、累计经验、突破阶数、赋能词条、神系与跃升绑定。
4. 全局总线与轻量原子刷新：
   - 强化、重构、突破触发 EventBus（EQUIP_REFORGE, GOLD_COST）。
   - 优先下发原子更新帧（sc_17023 / sc_13009 / sc_15009），仅在穿戴槽位变更时下发修复后的 sc_14007。
"""

import os
import sys
import json
import time
import random
import logging

logger = logging.getLogger("equip_service")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import OperationError
import event_bus
from codec import encode, decode
from middleware import DownFrame

# ----------------- 官方配置常量 -----------------

EQUIP_EXP_ITEMS = {
    40801: 50,    # 初级启示录
    40802: 100,   # 中级启示录
    40803: 500,   # 高级启示录
}

EXP_TIERS = [
    (40803, 500),
    (40802, 100),
    (40801, 50),
]

BASE_EXP_STAR = [0, 20, 50, 100, 150, 1250, 800, 800]

EQUIP_STRENGTHEN_GOLD_RATIO = 1

RESOLVE_NUM = {5: (41002, 5), 6: (41002, 10), 7: (41002, 10)}
BREAK_RETURN_MATS = {3: [1001], 4: [1001, 1002], 5: [1001, 1002, 1003, 1004, 1005]}
BREAK_MAT_RETURN = {
    1001: [(40501, 9)],
    1002: [(40501, 12), (40502, 9)],
    1003: [(40502, 12), (40503, 9)],
    1004: [(40504, 6)],
    1005: [(40504, 18)],
}

INHERIT_COST_DEFAULT = [(2, 20000), (41401, 10), (41002, 15)]


def material_give_back(overflow_exp):
    """根据多余经验计算从高到低返还的经验材料列表（对齐 MaterialTools.materialGiveBack）"""
    if overflow_exp <= 0:
        return []
    refund = []
    rem = int(overflow_exp)
    for iid, val in EXP_TIERS:
        cnt = rem // val
        if cnt > 0:
            refund.append({"id": iid, "num": cnt})
            rem -= cnt * val
    return refund


class EquipService:
    _instance = None

    def __init__(self, db=None):
        self.db = db
        self._curve_cache = {}

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    # ==================== 1. 经验与等级计算引擎 ====================

    def get_equip_curve(self, variant, db=None):
        """获取指定经验变种（variant 1..7）的各级升阶所需经验曲线 {level: need_exp}"""
        v = min(max(int(variant or 1), 1), 7)
        if v in self._curve_cache:
            return self._curve_cache[v]
        target_db = db or self.db
        col = f"exp{v}"
        rows = target_db.query(f"SELECT level, {col} AS need FROM equip_exp_curve ORDER BY level")
        curve = {int(r["level"]): int(r["need"]) for r in rows}
        if not curve:
            curve = {i: 50 + i * 10 for i in range(1, 61)}
        self._curve_cache[v] = curve
        return curve

    def calc_need_exp(self, variant, target_level, db=None):
        """计算从 1 级达到 target_level 所需的累计总经验"""
        curve = self.get_equip_curve(variant, db=db)
        return sum(curve.get(l, 0) for l in range(1, int(target_level)))

    def calc_equip_level(self, variant, total_exp, max_lv=60, db=None):
        """根据累计总经验计算当前等级与下一级剩余所需经验 (level, current_exp_in_level, next_need)"""
        curve = self.get_equip_curve(variant, db=db)
        lv = 1
        rem = int(total_exp or 0)
        while lv < max_lv:
            need = curve.get(lv, 1 << 30)
            if rem < need:
                break
            rem -= need
            lv += 1
        next_need = curve.get(lv, 0)
        return lv, rem, next_need

    def get_equip_cfg(self, prefab_id, db=None):
        """获取刻印静态配置"""
        target_db = db or self.db
        rows = target_db.query("SELECT * FROM equip_cfg WHERE prefab_id=?", (int(prefab_id),))
        if not rows:
            return None
        r = dict(rows[0])
        try:
            r["max_level"] = json.loads(r["max_level"] or "[20,30,40,50,60]")
        except Exception:
            r["max_level"] = [20, 30, 40, 50, 60]
        try:
            r["break_cost"] = json.loads(r["break_cost"] or "[]")
        except Exception:
            r["break_cost"] = []
        try:
            r["exp_variant"] = (json.loads(r["exp_variant"] or "[1]") or [1])[0]
        except Exception:
            r["exp_variant"] = 1
        r["starlevel"] = int(r.get("starlevel") or 5)
        r["break_times_max"] = int(r.get("break_times_max") or 4)

        from inventory_service import _get_equip_map
        eq_map = _get_equip_map()
        eq_info = eq_map.get(str(prefab_id))
        if eq_info:
            r["pos"] = int(eq_info.get("pos") or ((int(prefab_id) // 10000) % 10))
            r["suit"] = int(eq_info.get("suit") or 1)
        else:
            r["pos"] = (int(prefab_id) // 10000) % 10 if int(prefab_id) > 10000 else 1
            r["suit"] = int(prefab_id) % 1000
        return r

    # ==================== 2. 刻印强化与突破 ====================

    def enhance_equip(self, ctx, uid, equip_id, mat_list=None, equip_list=None):
        """
        刻印强化：cs_13014 -> sc_13015
        - 计算经验道具 + 狗粮刻印总经验
        - 依据当前突破阶数上限截断，多余经验原路返还 (material_give_back)
        - 按实际吸收经验 1:1 扣除金币，绝不贪污材料与金币
        """
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        eqs = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, eid))
        if not eqs:
            raise OperationError(406, f"刻印 {eid} 不存在")
        eq = eqs[0]

        cfg = self.get_equip_cfg(eq["prefab_id"], db=db)
        if not cfg:
            raise OperationError(4, f"刻印配置 {eq['prefab_id']} 缺失")

        cur_break = int(eq["now_break_level"] or 0)
        mls = cfg["max_level"]
        max_lv = mls[min(cur_break, len(mls) - 1)] if mls else 60
        variant = cfg["exp_variant"]
        cum_cap = self.calc_need_exp(variant, max_lv, db=db)
        cur_exp = int(eq["exp"] or 0)

        if cur_exp >= cum_cap:
            raise OperationError(2, f"刻印已达当前突破等级上限 (Lv{max_lv})，请先突破！")

        exp_add = 0
        touched_items = set()

        if mat_list:
            from inventory_service import InventoryService
            inv = InventoryService.get_instance(db=db)
            for it in mat_list:
                iid = int(it.get("item_id") or it.get("id") or 0)
                num = int(it.get("item_num") or it.get("num") or 0)
                if num <= 0:
                    continue
                if iid not in EQUIP_EXP_ITEMS:
                    raise OperationError(2, f"非刻印经验道具: {iid}")
                inv.deduct_item(ctx, uid, iid, num, db=db)
                exp_add += EQUIP_EXP_ITEMS[iid] * num
                touched_items.add(iid)

        if equip_list:
            for sub_eid in equip_list:
                sub_id = int(sub_eid)
                if sub_id == eid:
                    continue
                sub_row = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, sub_id))
                if not sub_row:
                    # 幽灵刻印：数据库中不存在，跳过不处理（不可推入 removed_equips 下发 sc_17023，否则客户端 RemoveEquip 访问 nil 必崩）
                    continue
                se = sub_row[0]
                if se.get("is_lock"):
                    raise OperationError(2, f"素材刻印 {sub_id} 已锁定，无法作为强化材料")
                if se.get("hero_id"):
                    raise OperationError(2, f"素材刻印 {sub_id} 正被角色穿戴，无法作为强化材料")
                scfg = self.get_equip_cfg(se["prefab_id"], db=db)
                star = scfg["starlevel"] if scfg else 5
                base_val = BASE_EXP_STAR[star] if star < len(BASE_EXP_STAR) else 100
                exp_add += base_val + int(se["exp"] or 0)
                db.execute("DELETE FROM equip WHERE uid=? AND id=?", (uid, sub_id))
                if not hasattr(ctx, "removed_equips") or ctx.removed_equips is None:
                    ctx.removed_equips = []
                ctx.removed_equips.append({
                    "equip_id": sub_id,
                    "prefab_id": int(se.get("prefab_id") or 0),
                    "num": 0,
                })

        if exp_add <= 0:
            if getattr(ctx, "removed_equips", None) or equip_list:
                # 客户端提交的均为已销毁的幽灵刻印，兜底返回成功并通过 sc_13009/13015 刷新客户端界面，防止出现 loading 遮罩异常
                new_lv, _, _ = self.calc_equip_level(variant, cur_exp, max_lv=max_lv, db=db)
                return {
                    "equip_id": eid,
                    "new_exp": cur_exp,
                    "new_level": new_lv,
                    "actual_exp_add": 0,
                    "exp": cur_exp,
                    "level": new_lv,
                    "exp_add": 0,
                    "refund_mats": [],
                    "gold_cost": 0,
                }
            raise OperationError(2, "强化经验总和为 0")

        max_possible = cum_cap - cur_exp
        actual_exp_add = min(exp_add, max_possible)
        overflow = exp_add - actual_exp_add
        new_exp = cur_exp + actual_exp_add

        refund_mats = []
        if overflow > 0:
            refund_mats = material_give_back(overflow)
            from inventory_service import InventoryService
            inv = InventoryService.get_instance(db=db)
            for rm in refund_mats:
                inv.add_item(ctx, uid, rm["id"], rm["num"], db=db)
                touched_items.add(rm["id"])

        gold_cost = int(actual_exp_add * EQUIP_STRENGTHEN_GOLD_RATIO)
        if gold_cost > 0:
            from inventory_service import InventoryService
            InventoryService.get_instance(db=db).deduct_item(ctx, uid, 2, gold_cost, db=db)
            touched_items.add(2)
            try:
                event_bus.bus.emit(event_bus.Events.GOLD_COST, ctx, uid, amount=gold_cost)
            except Exception:
                pass

        db.execute("UPDATE equip SET exp=?, update_ts=? WHERE uid=? AND id=?",
                   (new_exp, int(time.time()), uid, eid))

        new_lv, _, _ = self.calc_equip_level(variant, new_exp, max_lv=max_lv, db=db)

        if hasattr(ctx, "touched_items"):
            ctx.touched_items.update(touched_items)

        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid, oper="upgrade", new_level=new_lv)
        except Exception:
            pass

        return {
            "equip_id": eid,
            "new_exp": new_exp,
            "new_level": new_lv,
            "actual_exp_add": actual_exp_add,
            "exp": new_exp,
            "level": new_lv,
            "exp_add": actual_exp_add,
            "refund_mats": refund_mats,
            "gold_cost": gold_cost,
        }

    def breakthrough_equip(self, ctx, uid, equip_id):
        """
        刻印突破：cs_13022 -> sc_13023
        - 校验是否达到当前阶数上限
        - 扣除材料模板 equip_material_cfg
        - now_break_level 增加 1，累计经验保持不变
        """
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        eqs = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, eid))
        if not eqs:
            raise OperationError(406, f"刻印 {eid} 不存在")
        eq = eqs[0]

        cfg = self.get_equip_cfg(eq["prefab_id"], db=db)
        if not cfg:
            raise OperationError(4, f"刻印配置 {eq['prefab_id']} 缺失")

        cur_break = int(eq["now_break_level"] or 0)
        cap = cfg["break_times_max"]
        if cur_break >= cap:
            raise OperationError(2, f"刻印 {eid} 已达突破上限 ({cap}阶)")

        mls = cfg["max_level"]
        max_lv = mls[min(cur_break, len(mls) - 1)] if mls else 60
        cur_exp = int(eq["exp"] or 0)
        need_exp = self.calc_need_exp(cfg["exp_variant"], max_lv, db=db)
        if cur_exp < need_exp:
            raise OperationError(2, f"刻印经验未满 (需达 Lv{max_lv})，暂不能突破！")

        bcs = cfg["break_cost"]
        mat_id = bcs[cur_break] if cur_break < len(bcs) else (1001 + cur_break)

        from inventory_service import InventoryService
        inv = InventoryService.get_instance(db=db)
        mrow = db.query("SELECT item_list FROM equip_material_cfg WHERE mat_id=?", (mat_id,))
        if mrow:
            items = json.loads(mrow[0]["item_list"] or "[]")
            for iid, num in items:
                inv.deduct_item(ctx, uid, int(iid), int(num), db=db)
                if hasattr(ctx, "touched_items"):
                    ctx.touched_items.add(int(iid))

        new_break = cur_break + 1
        db.execute("UPDATE equip SET now_break_level=?, update_ts=? WHERE uid=? AND id=?",
                   (new_break, int(time.time()), uid, eid))

        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid, oper="breakthrough", break_level=new_break)
        except Exception:
            pass

        return {"equip_id": eid, "break_level": new_break, "cost_id": mat_id}

    def one_click_enhance(self, ctx, uid, equip_id, target_level=None, break_times=0, mat_list=None, equip_list=None):
        """
        刻印一键强化+突破：cs_13058 -> sc_13059
        - 步骤：
          1. 先按 break_times 执行突破，提升等级上限
          2. 精准充入目标经验，超出部分通过 material_give_back 100% 返还
          3. 扣除突破材料与强化金币
        """
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        eqs = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, eid))
        if not eqs:
            raise OperationError(406, f"刻印 {eid} 不存在")
        eq = eqs[0]

        cfg = self.get_equip_cfg(eq["prefab_id"], db=db)
        if not cfg:
            raise OperationError(4, f"刻印配置 {eq['prefab_id']} 缺失")

        from inventory_service import InventoryService
        inv = InventoryService.get_instance(db=db)
        cur_break = int(eq["now_break_level"] or 0)
        max_break = cfg["break_times_max"]

        b_times = int(break_times or 0)
        breaks_done = 0
        bcs = cfg["break_cost"]
        for _ in range(b_times):
            if cur_break >= max_break:
                break
            mat_id = bcs[cur_break] if cur_break < len(bcs) else (1001 + cur_break)
            mrow = db.query("SELECT item_list FROM equip_material_cfg WHERE mat_id=?", (mat_id,))
            if mrow:
                items = json.loads(mrow[0]["item_list"] or "[]")
                for iid, num in items:
                    inv.deduct_item(ctx, uid, int(iid), int(num), db=db)
                    if hasattr(ctx, "touched_items"):
                        ctx.touched_items.add(int(iid))
            cur_break += 1
            breaks_done += 1

        exp_add = 0
        if mat_list:
            for it in mat_list:
                iid = int(it.get("item_id") or it.get("id") or 0)
                num = int(it.get("item_num") or it.get("num") or 0)
                if num > 0 and iid in EQUIP_EXP_ITEMS:
                    inv.deduct_item(ctx, uid, iid, num, db=db)
                    exp_add += EQUIP_EXP_ITEMS[iid] * num
                    if hasattr(ctx, "touched_items"):
                        ctx.touched_items.add(iid)

        if equip_list:
            for sub_eid in equip_list:
                sub_id = int(sub_eid)
                if sub_id == eid:
                    continue
                sub_row = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, sub_id))
                if not sub_row:
                    continue
                if sub_row and not sub_row[0].get("is_lock") and not sub_row[0].get("hero_id"):
                    se = sub_row[0]
                    scfg = self.get_equip_cfg(se["prefab_id"], db=db)
                    star = scfg["starlevel"] if scfg else 5
                    base_val = BASE_EXP_STAR[star] if star < len(BASE_EXP_STAR) else 100
                    exp_add += base_val + int(se["exp"] or 0)
                    db.execute("DELETE FROM equip WHERE uid=? AND id=?", (uid, sub_id))
                    if not hasattr(ctx, "removed_equips") or ctx.removed_equips is None:
                        ctx.removed_equips = []
                    ctx.removed_equips.append({
                        "equip_id": sub_id,
                        "prefab_id": int(se.get("prefab_id") or 0),
                        "num": 0,
                    })

        mls = cfg["max_level"]
        cap_lv = mls[min(cur_break, len(mls) - 1)] if mls else 60
        t_lv = int(target_level or cap_lv)
        effective_target_lv = min(t_lv, cap_lv)
        variant = cfg["exp_variant"]
        target_cum_exp = self.calc_need_exp(variant, effective_target_lv, db=db)
        cur_exp = int(eq["exp"] or 0)

        refund_mats = []
        gold_cost = 0
        new_exp = cur_exp

        if exp_add > 0:
            need_exp = max(0, target_cum_exp - cur_exp)
            actual_exp_add = min(exp_add, need_exp)
            overflow = exp_add - actual_exp_add
            new_exp = cur_exp + actual_exp_add

            if overflow > 0:
                refund_mats = material_give_back(overflow)
                for rm in refund_mats:
                    inv.add_item(ctx, uid, rm["id"], rm["num"], db=db)
                    if hasattr(ctx, "touched_items"):
                        ctx.touched_items.add(rm["id"])

            gold_cost = int(actual_exp_add * EQUIP_STRENGTHEN_GOLD_RATIO)
            if gold_cost > 0:
                inv.deduct_item(ctx, uid, 2, gold_cost, db=db)
                if hasattr(ctx, "touched_items"):
                    ctx.touched_items.add(2)
                try:
                    event_bus.bus.emit(event_bus.Events.GOLD_COST, ctx, uid, amount=gold_cost)
                except Exception:
                    pass

        db.execute("UPDATE equip SET exp=?, now_break_level=?, update_ts=? WHERE uid=? AND id=?",
                   (new_exp, cur_break, int(time.time()), uid, eid))

        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid)
        except Exception:
            pass

        return {
            "equip_id": eid,
            "exp": new_exp,
            "breaks": breaks_done,
            "break_level": cur_break,
            "refund_mats": refund_mats,
            "gold_cost": gold_cost
        }

    # ==================== 3. 刻印继承系统 (Equip Inherit, 13052) ====================

    def inherit_equip(self, ctx, uid, new_equip_id, inherit_equip_prefab_id):
        """
        刻印继承：cs_13052 -> sc_13053
        - 校验目标套装 prefab_id，必须同孔位且星级 >= 5
        - 扣除继承材料模板（20000金币 + 10个沉睡之源 + 15个启示录）
        - 将 equip.prefab_id 换为 inherit_equip_prefab_id
        - 完美继承原有全部养成：等级、累计经验、突破阶数、赋能词条、神系重构与专属绑定！
        """
        db = getattr(ctx, "db", self.db)
        eid = int(new_equip_id)
        target_pid = int(inherit_equip_prefab_id)

        eqs = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, eid))
        if not eqs:
            raise OperationError(406, f"目标刻印 {eid} 不存在")
        eq = eqs[0]

        cur_cfg = self.get_equip_cfg(eq["prefab_id"], db=db)
        target_cfg = self.get_equip_cfg(target_pid, db=db)
        if not cur_cfg or not target_cfg:
            raise OperationError(4, "刻印配置缺失")

        if cur_cfg.get("starlevel", 0) < 5 or target_cfg.get("starlevel", 0) < 5:
            raise OperationError(2, "仅限 5 星及以上刻印进行继承操作！")

        cur_pos = cur_cfg.get("pos")
        target_pos = target_cfg.get("pos")
        if cur_pos is not None and target_pos is not None and cur_pos != target_pos:
            raise OperationError(2, f"继承孔位不匹配: 原刻印为{cur_pos}号位，目标为{target_pos}号位")

        if eq["prefab_id"] == target_pid:
            raise OperationError(2, "不能继承为相同的刻印套装！")

        from inventory_service import InventoryService
        inv = InventoryService.get_instance(db=db)
        for iid, cnt in INHERIT_COST_DEFAULT:
            inv.deduct_item(ctx, uid, iid, cnt, db=db)
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.add(iid)

        db.execute("UPDATE equip SET prefab_id=?, update_ts=? WHERE uid=? AND id=?",
                   (target_pid, int(time.time()), uid, eid))

        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid)
        except Exception:
            pass

        return {
            "equip_id": eid,
            "old_prefab_id": eq["prefab_id"],
            "new_prefab_id": target_pid,
            "wearing_hero_id": int(eq.get("hero_id") or 0)
        }

    # ==================== 4. 穿戴与槽位协同 ====================

    def swap_equip(self, ctx, uid, hero_id, equip_id, pos):
        """刻印穿戴/替换：cs_13012 -> sc_13013。委托并协调 HeroService"""
        from hero_service import HeroService
        return HeroService.get_instance(db=getattr(ctx, "db", self.db)).swap_equip(ctx, uid, hero_id, equip_id, pos)

    def quick_dress_equips(self, ctx, uid, hero_id, use_equip_list):
        """一键快速穿戴刻印：cs_13026 -> sc_13027。委托并协调 HeroService"""
        from hero_service import HeroService
        return HeroService.get_instance(db=getattr(ctx, "db", self.db)).quick_dress_equips(ctx, uid, hero_id, use_equip_list)

    def unload_all_equips(self, ctx, uid, hero_id):
        """一键卸下全套刻印：cs_13018 -> sc_13019。委托并协调 HeroService"""
        from hero_service import HeroService
        return HeroService.get_instance(db=getattr(ctx, "db", self.db)).unload_all_equips(ctx, uid, hero_id)

    def bind_equip_hero(self, ctx, uid, equip_id, hero_id):
        """刻印专属修正者祈生绑定：cs_13046 -> sc_13047。消耗重构秘典 41102 并绑定 race_hero"""
        from hero_service import HeroService
        return HeroService.get_instance(db=getattr(ctx, "db", self.db)).bind_equip_hero(ctx, uid, equip_id, hero_id)

    # ==================== 5. 锁定与保护 ====================

    def lock_equip(self, ctx, uid, equip_id, is_lock):
        """刻印锁定/解锁：cs_13016 -> sc_13017"""
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        lock_val = 1 if is_lock else 0
        db.execute("UPDATE equip SET is_lock=?, update_ts=? WHERE uid=? AND id=?",
                   (lock_val, int(time.time()), uid, eid))
        return {"equip_id": eid, "is_lock": lock_val}

    # ==================== 6. 赋能系统 (Enchant) ====================

    def _get_enchant_slot(self, db, uid, equip_id, slot_id):
        rows = db.query("SELECT enchant_slots FROM equip WHERE uid=? AND id=?", (uid, int(equip_id)))
        if not rows:
            raise OperationError(406, f"刻印 {equip_id} 不存在")
        try:
            slots = json.loads(rows[0]["enchant_slots"] or "[]")
        except Exception:
            slots = []
        for s in slots:
            if "effect_list" in s and isinstance(s["effect_list"], list):
                s["effect_list"] = [e for e in s["effect_list"] if isinstance(e, dict) and e.get("id")]
            if "preview_list" not in s or not isinstance(s["preview_list"], list):
                s["preview_list"] = []
        for i, s in enumerate(slots):
            if s.get("id") == int(slot_id):
                return slots, i, s
        slot = {"id": int(slot_id), "effect_list": [], "preview_list": []}
        slots.append(slot)
        slots.sort(key=lambda x: x.get("id", 0))
        for i, s in enumerate(slots):
            if s.get("id") == int(slot_id):
                return slots, i, s
        return slots, len(slots) - 1, slot

    def _save_enchant_slots(self, db, uid, equip_id, slots):
        clean_slots = []
        for s in slots:
            sid = int(s.get("id") or 1)
            effs = [e for e in (s.get("effect_list") or []) if isinstance(e, dict) and e.get("id")]
            raw_prev = s.get("preview_list") or []
            prevs = []
            for p in raw_prev:
                if isinstance(p, list):
                    pe_list = [pe for pe in p if isinstance(pe, dict) and pe.get("id")]
                    if pe_list:
                        prevs.append(pe_list)
                elif isinstance(p, dict) and p.get("id"):
                    prevs.append([p])
            clean_slots.append({"id": sid, "effect_list": effs, "preview_list": prevs})
        clean_slots.sort(key=lambda x: x["id"])
        db.execute("UPDATE equip SET enchant_slots=?, update_ts=? WHERE uid=? AND id=?",
                   (json.dumps(clean_slots, ensure_ascii=False), int(time.time()), uid, int(equip_id)))

    def refresh_enchant(self, ctx, uid, equip_id, slot_id, tier=1, lock_type=0):
        """刻印赋能刷新：cs_13028 -> sc_13029"""
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        sid = int(slot_id or 1)
        try:
            tier = int(tier or 1)
        except Exception:
            tier = 1
        lock_type = int(lock_type or 0)
        slots, idx, slot = self._get_enchant_slot(db, uid, eid, sid)

        from inventory_service import InventoryService
        inv = InventoryService.get_instance(db=db)
        is_locked = lock_type in (1, 2)
        if is_locked:
            inv.deduct_item(ctx, uid, 2, 2500, db=db)
            inv.deduct_item(ctx, uid, 40603, 5, db=db)
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.update({2, 40603})
        else:
            module = {1: 40601, 2: 40602, 3: 40603}.get(tier, 40601)
            inv.deduct_item(ctx, uid, 2, 500, db=db)
            inv.deduct_item(ctx, uid, module, 1, db=db)
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.update({2, module})

        pool = db.query("SELECT skill_id, lvmax FROM equip_skill_cfg WHERE skill_type=0")
        if not pool:
            pool = [{"skill_id": 101, "lvmax": 3}, {"skill_id": 102, "lvmax": 3}]

        def _roll_one():
            a = random.choice(pool)
            # 官方规则：单次洗练赋能词条基础等级恒为 1 级，全套刻印通过全局累加生效
            return {"id": a["skill_id"], "level": 1}

        existing_effects = [e for e in (slot.get("effect_list") or []) if isinstance(e, dict) and e.get("id")]
        rolled = []

        if is_locked:
            if lock_type == 1:
                locked_eff = existing_effects[0] if len(existing_effects) > 0 else _roll_one()
                rolled = [
                    {"id": locked_eff.get("id", 1), "level": locked_eff.get("level", 1)},
                    _roll_one()
                ]
            elif lock_type == 2:
                locked_eff = existing_effects[1] if len(existing_effects) > 1 else (existing_effects[0] if len(existing_effects) > 0 else _roll_one())
                rolled = [
                    _roll_one(),
                    {"id": locked_eff.get("id", 1), "level": locked_eff.get("level", 1)}
                ]
            else:
                locked_eff = existing_effects[0] if len(existing_effects) > 0 else _roll_one()
                rolled = [
                    {"id": locked_eff.get("id", 1), "level": locked_eff.get("level", 1)},
                    _roll_one()
                ]
        else:
            count = 2 if tier == 3 else 1
            for _ in range(count):
                rolled.append(_roll_one())

        prev_list = slot.get("preview_list") or []
        if not isinstance(prev_list, list):
            prev_list = []
        prev_list.append(rolled)
        if len(prev_list) > 10:
            prev_list.pop(0)
        slot["preview_list"] = prev_list

        self._save_enchant_slots(db, uid, eid, slots)

        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid, oper="enchant")
        except Exception:
            pass

        return {"equip_id": eid, "slot_id": sid, "lock_type": lock_type, "preview": rolled}

    def confirm_enchant(self, ctx, uid, equip_id, slot_id, confirm=True, preview_index=1):
        """刻印赋能确认保存/放弃：cs_13030 -> sc_13031"""
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        sid = int(slot_id or 1)
        p_idx = int(preview_index or 1) - 1
        slots, idx, slot = self._get_enchant_slot(db, uid, eid, sid)

        prev_list = slot.get("preview_list") or []
        chosen_roll = None
        if 0 <= p_idx < len(prev_list):
            chosen_roll = prev_list.pop(p_idx)
        elif prev_list:
            chosen_roll = prev_list.pop(0)

        if confirm and chosen_roll:
            if isinstance(chosen_roll, list):
                slot["effect_list"] = [e for e in chosen_roll if isinstance(e, dict) and e.get("id")]
            elif isinstance(chosen_roll, dict) and chosen_roll.get("id"):
                slot["effect_list"] = [chosen_roll]

        slot["preview_list"] = prev_list
        self._save_enchant_slots(db, uid, eid, slots)
        return {"confirmed": confirm, "effects": slot.get("effect_list", []), "equip_id": eid, "slot_id": sid}

    def give_up_all_enchant(self, ctx, uid, equip_id, slot_id):
        """放弃全部未确认赋能：cs_13044 -> sc_13045"""
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        sid = int(slot_id or 1)
        slots, idx, slot = self._get_enchant_slot(db, uid, eid, sid)
        slot["preview_list"] = []
        self._save_enchant_slots(db, uid, eid, slots)
        return {"equip_id": eid, "slot_id": sid}

    def directional_enchant(self, ctx, uid, equip_id, slot_id, skill_id, seq=1):
        """刻印定向赋能：cs_13060 -> sc_13061"""
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        sid = int(slot_id or 1)
        target_sk = int(skill_id or 0)
        if not target_sk:
            raise OperationError(2, "缺少技能 ID")

        slots, idx, slot = self._get_enchant_slot(db, uid, eid, sid)
        effs = [e for e in (slot.get("effect_list") or []) if isinstance(e, dict) and e.get("id")]
        for eff in effs:
            if eff.get("id") == target_sk:
                raise OperationError(2, f"技能 {target_sk} 已存在于该槽位")

        from inventory_service import InventoryService
        inv = InventoryService.get_instance(db=db)
        inv.deduct_item(ctx, uid, 40604, 1, db=db)
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.add(40604)

        t_idx = max(0, min(int(seq or 1) - 1, 1))
        if t_idx < len(effs):
            effs[t_idx] = {"id": target_sk, "level": 1}
        else:
            effs.append({"id": target_sk, "level": 1})

        slot["effect_list"] = effs
        self._save_enchant_slots(db, uid, eid, slots)

        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid, oper="enchant")
        except Exception:
            pass

        return {"equip_id": eid, "slot": sid, "id": target_sk, "seq": seq}

    # ==================== 7. 神系重构 (Reconstruct / Race) ====================

    def refresh_race(self, ctx, uid, equip_id):
        """神系重构刷新：cs_13032 -> sc_13033"""
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        if not db.query("SELECT id FROM equip WHERE uid=? AND id=?", (uid, eid)):
            raise OperationError(406, f"刻印 {eid} 不存在")

        from inventory_service import InventoryService
        inv = InventoryService.get_instance(db=db)
        inv.deduct_item(ctx, uid, 2, 1000, db=db)
        inv.deduct_item(ctx, uid, 40701, 1, db=db)
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.update([2, 40701])

        new_race = random.choice([1, 2, 3, 4, 5, 9])
        db.execute("UPDATE equip SET race_preview=?, update_ts=? WHERE uid=? AND id=?",
                   (new_race, int(time.time()), uid, eid))

        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_REFORGE, ctx, uid, equip_id=eid, oper="race_refresh")
        except Exception:
            pass

        return {"equip_id": eid, "race": new_race}

    def confirm_race(self, ctx, uid, equip_id, confirm=True):
        """神系重构确认：cs_13034 -> sc_13035"""
        db = getattr(ctx, "db", self.db)
        eid = int(equip_id)
        eq = db.query("SELECT race, race_preview FROM equip WHERE uid=? AND id=?", (uid, eid))
        if not eq:
            raise OperationError(406, f"刻印 {eid} 不存在")
        c_flag = bool(confirm)
        final_race = int(eq[0]["race_preview"] or 0) if c_flag else int(eq[0]["race"] or 0)
        if c_flag:
            db.execute("UPDATE equip SET race=?, race_preview=0, update_ts=? WHERE uid=? AND id=?",
                       (final_race, int(time.time()), uid, eid))
        else:
            db.execute("UPDATE equip SET race_preview=0, update_ts=? WHERE uid=? AND id=?",
                       (int(time.time()), uid, eid))
        return {"equip_id": eid, "race": final_race, "confirmed": c_flag}

    # ==================== 8. 分解与自动分解 ====================

    def resolve_equips(self, ctx, uid, equip_id_list):
        """刻印批量分解：cs_13024 -> sc_13025"""
        db = getattr(ctx, "db", self.db)
        eids = equip_id_list if isinstance(equip_id_list, list) else [equip_id_list]
        returns = {}
        removed = 0

        for eid in eids:
            try:
                eid_int = int(eid)
            except Exception:
                continue
            rows = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, eid_int))
            if not rows:
                continue
            e = rows[0]
            if e.get("is_lock") or e.get("hero_id"):
                continue

            cfg = self.get_equip_cfg(e["prefab_id"], db=db)
            star = cfg["starlevel"] if cfg else 5

            total_exp = int((e.get("exp") or 0) * 0.8)
            if 1 <= star < len(BASE_EXP_STAR):
                total_exp += BASE_EXP_STAR[star]
            for iid, per in EXP_TIERS:
                if total_exp >= per:
                    n = total_exp // per
                    returns[iid] = returns.get(iid, 0) + n
                    total_exp -= per * n

            blv = int(e.get("now_break_level") or 0)
            mats = BREAK_RETURN_MATS.get(star, [])
            for lv in range(1, min(blv, len(mats)) + 1):
                for iid, num in BREAK_MAT_RETURN.get(mats[lv - 1], []):
                    returns[iid] = returns.get(iid, 0) + num

            if star in RESOLVE_NUM:
                iid, num = RESOLVE_NUM[star]
                returns[iid] = returns.get(iid, 0) + num

            db.execute("DELETE FROM equip WHERE uid=? AND id=?", (uid, eid_int))
            removed += 1
            if not hasattr(ctx, "removed_equips") or ctx.removed_equips is None:
                ctx.removed_equips = []
            ctx.removed_equips.append({
                "equip_id": eid_int,
                "prefab_id": int(e.get("prefab_id") or 0),
                "num": 0,
            })

        if not removed:
            raise OperationError(2, "没有可分解的刻印（未勾选或均处于加锁/佩戴状态）")

        from inventory_service import InventoryService
        inv = InventoryService.get_instance(db=db)
        for iid, num in returns.items():
            inv.add_item(ctx, uid, iid, num, db=db)
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.add(iid)

        mat_list = [{"id": i, "num": n} for i, n in sorted(returns.items())]
        return {"removed": removed, "mat_list": mat_list}

    def save_auto_decompose_cfg(self, ctx, uid, type_, sign):
        """自动分解设置保存：cs_13054 -> sc_13055"""
        db = getattr(ctx, "db", self.db)
        t = int(type_ or 1)
        s = int(sign or 0)
        db.execute(
            "INSERT INTO ops_common (uid, kind, item_id, name, value_json) VALUES (?, 'auto_decompose', ?, '', ?) "
            "ON CONFLICT(uid, kind, item_id) DO UPDATE SET value_json=excluded.value_json",
            (uid, str(t), json.dumps({"sign": s}))
        )
        return {"type": t, "sign": s}

    # ==================== 9. 下行帧装配 ====================

    def build_equip_refresh_frames(self, ctx, uid, hero_id=None):
        """统一装配刻印操作后的标准下行差分刷新帧（帧序：17023 -> 15009 -> 14007）"""
        out = []
        db = getattr(ctx, "db", self.db)
        if not (db and uid):
            return out

        import generator as _gen

        # 1. sc_17023 增量/销毁差分帧置顶：使客户端优先执行 EquipData:RemoveEquip / AddItem，先清理内存残渣
        touched = getattr(ctx, "touched_items", set())
        rem_eq = getattr(ctx, "removed_equips", None)
        p_diff = _gen.gen_payload(17023, uid=uid, db=db, touched_items=touched, equip_list=rem_eq)
        if p_diff:
            out.append(DownFrame(17023, p_diff))

        # 2. sc_15009 货币变动帧
        p_curr = _gen.gen_payload(15009, uid=uid, db=db)
        if p_curr:
            out.append(DownFrame(15009, p_curr))

        # 3. sc_14007 英雄槽位同步帧（支持单 hero_id 或多英雄集合）
        # 注意：刻印常规操作严禁下发 sc_13009 全量帧！
        # 客户端在收到 sc_13015 等 ACK 回调中会在本地对当前刻印进行 exp 累加（EquipData:ApplyEquipStrengthSuccess）。
        # 若此前下发 sc_13009，客户端先被置为最新 exp，再被本地加一次，造成双端经验脱节（本地虚高到 10 级但服务端为 9 级）。
        # 官方设计仅通过 sc_17023(道具/刻印差量) + sc_15009(金币) 增量更新，sc_13009 仅在登录洪流中下发。
        if hero_id:
            try:
                import hero_codec as _hc
                hids = [hero_id] if isinstance(hero_id, (int, str)) else list(hero_id)
                for hid in set(hids):
                    if hid:
                        hf = _hc.build_hero_14007_frame(db, uid, int(hid))
                        if hf:
                            out.append(hf)
            except Exception as e:
                logger.warning(f"刻印刷新下发 sc_14007 异常: {e}")

        # 5. 上下文中事件总线累积的其它待发帧
        if hasattr(ctx, "pop_pending_frames"):
            out.extend(ctx.pop_pending_frames())
        elif hasattr(ctx, "pending_frames") and ctx.pending_frames:
            out.extend(ctx.pending_frames)
            ctx.pending_frames.clear()
        return out
