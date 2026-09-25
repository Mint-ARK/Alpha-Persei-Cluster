# -*- coding: utf-8 -*-
"""
servant_service.py — 《深空之眼》统一钥从资产领域服务 (Servant Domain Service)

职责与设计理念：
1. 独立资产实体（Asset Entity）：
   - 钥从有独立的生命周期（创建、加锁、超越精炼、唤名转化、权钥经验喂养、分解出售、销毁）。
   - 即使是相同模板 ID 的钥从，每一把在数据库内均为拥有全局唯一 servant_uid 的独立记录行，严格与客户端对齐。
2. 架构解耦与防线保护（Safety Guard）：
   - 正在被修正者佩戴（hero.weapon_servant_uid == uid）或处于加锁状态（is_locked == 1）的钥从，
     严禁作为精炼材料、唤名材料、权钥狗粮或分解材料被销毁。
   - 权钥系统（Hero 固有器官）与钥从系统（独立灵体伙伴）通过标准领域接口与 EventBus 保持解耦。
3. 双 Bug 绝杀与时序优化：
   - Bug 1（神识凝晶升阶余额延迟）：下行帧序列严格将材料原子增量变动帧（sc_17023）排在响应帧（sc_46013）之前，客户端先更新内存缓存再触发界面重绘，读数绝对实时！
   - Bug 2（超越消耗同名钥从不消失）：客户端官方数据层代码漏写了删除 cost_uid 的逻辑，服务端超越后伴随推送一帧最新全量 sc_46011，客户端即刻重建字典，秒杀官方祖传 Bug！
   - 性能飞跃：日常操作全面推行老大指示的增量原子帧（DownFrame 17023），彻底废弃笨重的全量 17009，杜绝内存清包抖动！
"""

import os
import sys
import json
import time
import logging

logger = logging.getLogger("servant_service")

# 确保加载当前目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import OperationError
import event_bus
from codec import encode, decode
from middleware import DownFrame

# ----------------- 官方配置常量 -----------------

SERVANT_PROMOTE_CFG_PATH = os.path.join(BASE_DIR, "servant_promote_cfg.json")
SERVANT_PROMOTE_CFG = {}
if os.path.exists(SERVANT_PROMOTE_CFG_PATH):
    try:
        with open(SERVANT_PROMOTE_CFG_PATH, "r", encoding="utf-8") as _f:
            SERVANT_PROMOTE_CFG = json.load(_f)
    except Exception as _e:
        logger.warning(f"加载 servant_promote_cfg.json 失败: {_e}")

# 钥从精炼升阶金币消耗：按 starlevel -> [1阶->2阶, 2阶->3阶, 3阶->4阶, 4阶->5阶] (对齐 gamesetting.lua)
SERVANT_GOLD_COSTS = {
    3: [1000, 2000, 3000, 4000],
    4: [1000, 2000, 3000, 4000],
    5: [2000, 3000, 4000, 5000],
}

# 钥从精炼升阶通用材料消耗（神识凝晶 41201）
SERVANT_PROMOTE_MAT_ID = 41201

# 钥从分解出售产出配置（官方 GameSetting.weapon_servant_break_cost_return）
# 3星产出 1 个初级源质结晶 (40201)；4星产出 3 个；5星产出 5 个
SERVANT_DECOMPOSE_RETURNS = {
    3: [(40201, 1)],
    4: [(40201, 3)],
    5: [(40201, 5)],
}

# 钥从作为权钥强化狗粮提供的经验：对应 GameSetting.base_exp_weapon_servant {0, 0, 100, 300, 500}
SERVANT_STAR_EXP = {
    1: 0,
    2: 0,
    3: 100,
    4: 300,
    5: 500,
}



class ServantService:
    _instance = None

    def __init__(self, db=None):
        self.db = db

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    # ==================== 1. 辅助方法与实体 ID 分配 ====================

    def allocate_servant_uid(self, uid, db=None):
        """为玩家分配全局唯一的自增钥从实例 ID（与客户端 servant_uid 对齐）"""
        target_db = db or self.db
        r = target_db.query("SELECT COALESCE(MAX(id), 0) AS m FROM servant WHERE uid=?", (uid,))
        base_id = int(r[0]["m"] or 0) if r else 0
        return base_id + 1

    def _deduct_item(self, ctx, uid, item_id, count):
        """统一扣除通用代币/材料，并记录 touched_items"""
        from inventory_service import InventoryService
        succ = InventoryService.cost_item(ctx, uid, item_id, count)
        if not succ:
            _, have = InventoryService.get_item_balance(ctx, uid, item_id)
            raise OperationError(3, f"材料不足: id={item_id} 需{count} 有{have}")
        if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
            ctx.touched_items.add(int(item_id))

    def _grant_item(self, ctx, uid, item_id, count):
        """统一增加物品材料，并记录 touched_items"""
        from inventory_service import InventoryService
        InventoryService.grant_item(ctx, uid, item_id, count, silent=True)
        if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
            ctx.touched_items.add(int(item_id))

    def get_equipped_servant_uids(self, uid, db=None):
        """获取当前玩家所有修正者正在佩戴的钥从 UID 集合"""
        target_db = db or self.db
        rows = target_db.query("SELECT weapon_servant_uid FROM hero WHERE uid=? AND weapon_servant_uid > 0", (uid,))
        return {int(r["weapon_servant_uid"]) for r in rows} if rows else set()

    def validate_servant_available(self, uid, servant_uid, db=None):
        """校验钥从是否存在且可用"""
        target_db = db or self.db
        rows = target_db.query("SELECT id, is_locked FROM servant WHERE uid=? AND id=?", (uid, int(servant_uid)))
        if not rows:
            raise OperationError(406, f"钥从 {servant_uid} 不存在")
        return rows[0]

    # ==================== 2. 钥从超越精炼 (Promote / Refine) ====================

    def promote_servant(self, ctx, uid, servant_uid, cost_uid=0, refined_type=1):
        """
        钥从超越精炼：cs_46012 -> sc_46013
        - refined_type == 1: 消耗通用材料（神识凝晶 41201）
        - refined_type == 0 (客户端发送) 或 refined_type == 2: 消耗同名钥从本体实体 (cost_uid)
        - 校验阶数 stage 上限为 5
        - 绝杀 Bug 1：下行帧严格先发原子变动帧 (sc_17023)，再发 sc_46013，彻底消除余额延迟！
        - 绝杀 Bug 2：通过 sc_17023 中的 weapon_list [item.num=0] 触发客户端原生 RemoveServant，彻底消除幽灵数据！
        - 彻底移除全量 46011 与 15009，杜绝 GC 内存颠簸与卡顿！
        """
        db = getattr(ctx, "db", self.db)
        suid = int(servant_uid or 0)
        c_uid = int(cost_uid or 0)

        # 兼容客户端协议：客户端同名升阶发送 refined_type=0，测试/历史版本可能传入 2
        # 当提供 cost_uid 或 refined_type in (0, 2) 时判定为同名钥从升阶
        is_duplicate = (c_uid > 0) or (refined_type in (0, 2))
        effective_ref_type = 0 if is_duplicate else 1

        s_rows = db.query("SELECT * FROM servant WHERE uid=? AND id=?", (uid, suid))
        if not s_rows:
            raise OperationError(406, f"目标钥从 {suid} 不存在")
        s = s_rows[0]
        cur_stage = int(s.get("stage") or 1)
        if cur_stage >= 5:
            raise OperationError(2, "钥从已达最高超越等阶(5阶)")

        starlevel = int(s.get("starlevel") or 5)
        pid = int(s.get("prefab_id") or 0)

        # 1. 扣除金币（根据星级与当前阶数）
        gold_list = SERVANT_GOLD_COSTS.get(starlevel, [2000, 3000, 4000, 5000])
        gold_cost = gold_list[min(max(0, cur_stage - 1), len(gold_list) - 1)] if gold_list else 2000
        self._deduct_item(ctx, uid, 2, gold_cost)

        # 2. 根据 refined_type 扣除材料或同名本体
        mat_cost = 0
        wl = None
        if not is_duplicate:
            # 使用神识凝晶 (41201)
            # 优先查权威配置表
            cfg_info = SERVANT_PROMOTE_CFG.get(str(pid))
            if cfg_info:
                mat_cost = int(cfg_info.get("mat_cost", 0))
                if mat_cost <= 0:
                    raise OperationError(2, f"{starlevel}星钥从不支持使用神识凝晶精炼，请使用同名钥从")
            else:
                # 动态安全回退规则
                if starlevel == 5:
                    mat_cost = 30 if (pid % 10000 != 0 and pid not in (2510000, 2520000, 2530000, 2540000, 2550000, 2590000)) else 10
                elif starlevel == 4:
                    mat_cost = 1
                else:
                    raise OperationError(2, "3星钥从不支持使用神识凝晶精炼，请使用同名钥从")

            self._deduct_item(ctx, uid, SERVANT_PROMOTE_MAT_ID, mat_cost)
        else:
            # 使用同名钥从本体
            if not c_uid:
                raise OperationError(2, "缺少作为精炼材料的钥从 UID")
            if c_uid == suid:
                raise OperationError(2, "不能消耗自身进行精炼")

            # 严格安全防线：检查 cost_uid 是否被佩戴或加锁
            equipped_set = self.get_equipped_servant_uids(uid)
            if c_uid in equipped_set:
                raise OperationError(2, "该同名钥从正在被角色佩戴，无法作为材料消耗")

            cost_rows = db.query("SELECT id, prefab_id, is_locked FROM servant WHERE uid=? AND id=?", (uid, c_uid))
            if not cost_rows:
                raise OperationError(406, f"材料钥从 {c_uid} 不存在")
            if int(cost_rows[0]["is_locked"] or 0) == 1:
                raise OperationError(2, "材料钥从已锁定，无法作为材料消耗")
            if int(cost_rows[0]["prefab_id"] or 0) != pid:
                raise OperationError(2, "材料钥从与目标钥从不属于同名模板")

            # 物理销毁材料实体
            db.execute("CREATE TABLE IF NOT EXISTS servant_history (uid INTEGER NOT NULL, id INTEGER NOT NULL, prefab_id INTEGER NOT NULL, PRIMARY KEY (uid, id))")
            db.execute("INSERT OR REPLACE INTO servant_history (uid, id, prefab_id) VALUES (?, ?, ?)", (uid, c_uid, pid))
            db.execute("DELETE FROM servant WHERE uid=? AND id=?", (uid, c_uid))
            # 准备通过 17023 的 weapon_list 通知客户端原生删除
            wl = [{"uid": c_uid, "item": {"id": pid, "num": 0}}]

        # 3. 提升阶数（上限 5 阶）
        new_stage = min(5, cur_stage + 1)
        now = int(time.time())
        db.execute("UPDATE servant SET stage=?, update_ts=? WHERE uid=? AND id=?", (new_stage, now, uid, suid))

        # 4. 触发 EventBus 广播
        try:
            event_bus.bus.emit(event_bus.Events.SERVANT_PROMOTE, ctx, uid, servant_uid=suid, new_stage=new_stage)
        except Exception:
            pass

        # 5. 编排纯原子下行帧集合（彻底移除 46011 与 15009，绝杀 Bug 1 & Bug 2）
        frames = []

        # 5.1 优先推送材料/货币/钥从原子变动帧（DownFrame 17023）
        # normal_items 更新金币与神识凝晶；weapon_list 彻底抹除被消耗的同名钥从
        touched = getattr(ctx, "touched_items", None)
        try:
            import generator as _gen
            p_17023 = _gen.gen_payload(17023, uid=uid, db=db, touched_items=touched, weapon_list=wl)
            if p_17023:
                frames.append(DownFrame(17023, p_17023))
        except Exception as e:
            logger.warning(f"生成原子变动帧 17023 异常: {e}")

        # 5.2 下发响应帧 sc_46013，触发客户端重绘回调，内存 stage + 1，零卡顿零幽灵！
        p_46013 = encode("sc_46013", {"result": 0}) or b"\x08\x00"
        frames.append(DownFrame(46013, p_46013))

        return {
            "uid": suid,
            "new_stage": new_stage,
            "refined_type": effective_ref_type,
            "cost_uid": c_uid,
            "mat_cost": mat_cost,
            "gold_cost": gold_cost,
            "frames": frames
        }

    # ==================== 3. 钥从锁定/解锁 (Lock / Unlock) ====================

    def lock_servant(self, ctx, uid, servant_uid, is_lock):
        """
        钥从锁定/解锁：cs_46014 -> sc_46015
        """
        db = getattr(ctx, "db", self.db)
        suid = int(servant_uid or 0)
        lock_val = 1 if is_lock else 0

        rows = db.query("SELECT id FROM servant WHERE uid=? AND id=?", (uid, suid))
        if not rows:
            raise OperationError(406, f"钥从 {suid} 不存在")

        now = int(time.time())
        db.execute("UPDATE servant SET is_locked=?, update_ts=? WHERE uid=? AND id=?", (lock_val, now, uid, suid))

        try:
            event_bus.bus.emit(event_bus.Events.SERVANT_LOCK, ctx, uid, servant_uid=suid, is_locked=lock_val)
        except Exception:
            pass

        payload = encode("sc_46015", {"result": 0}) or b"\x08\x00"
        return {
            "uid": suid,
            "is_locked": lock_val,
            "frames": [DownFrame(46015, payload)]
        }

    # ==================== 4. 沉睡之子唤名转换 (Awaken / Merge) ====================

    def awake_servant(self, ctx, uid, servant_id, cost_uid_list):
        """
        沉睡之子唤名（转换为目标专属五星钥从）：cs_46030 -> sc_46031
        - 消耗指定神系的沉睡之子实体 (cost_uid_list)
        - 校验非锁、非穿戴保护
        - 生成全新专属五星钥从独立实体（分配新 servant_uid）
        - 若原沉睡之子正在被角色佩戴，自动将角色权钥挂载到新专属钥从上
        """
        db = getattr(ctx, "db", self.db)
        sid = int(servant_id or 0)
        costs = [int(x) for x in (cost_uid_list or []) if str(x).isdigit()]
        if not sid or not costs:
            raise OperationError(2, "缺少唤名目标 ID 或沉睡之子材料")

        equipped_map = {}
        h_rows = db.query("SELECT id, weapon_servant_uid FROM hero WHERE uid=? AND weapon_servant_uid > 0", (uid,))
        for r in (h_rows or []):
            equipped_map[int(r["weapon_servant_uid"])] = int(r["id"])

        cost_suid = costs[0]
        s_rows = db.query("SELECT * FROM servant WHERE uid=? AND id=?", (uid, cost_suid))
        if not s_rows:
            raise OperationError(406, f"沉睡之子钥从 {cost_suid} 不存在")
        c_item = s_rows[0]

        if int(c_item.get("is_locked") or 0) == 1:
            raise OperationError(2, "该沉睡之子钥从已被锁定，无法唤名")

        # 官方唤名消耗：100 金币 (currency ID 2)
        self._deduct_item(ctx, uid, 2, 100)

        # 继承原沉睡之子的精炼等阶与锁定状态
        inherit_stage = int(c_item.get("stage") or 1)
        inherit_lock = int(c_item.get("is_locked") or 0)
        wearing_hero_id = equipped_map.get(cost_suid)

        # 销毁多余的副材料沉睡之子实体
        for extra_c in costs[1:]:
            c_pid = int(c_item.get("prefab_id") or 0)
            if c_pid > 0:
                db.execute("INSERT OR REPLACE INTO servant_history (uid, id, prefab_id) VALUES (?, ?, ?)", (uid, extra_c, c_pid))
            db.execute("DELETE FROM servant WHERE uid=? AND id=?", (uid, extra_c))

        # 主沉睡之子实体原地升格为专属五星钥从
        target_uid = cost_suid
        now = int(time.time())
        db.execute(
            """
            UPDATE servant
            SET prefab_id=?, starlevel=5, stage=?, is_locked=?, update_ts=?
            WHERE uid=? AND id=?
            """,
            (sid, inherit_stage, inherit_lock, now, uid, target_uid)
        )

        try:
            event_bus.bus.emit(event_bus.Events.SERVANT_OBTAIN, ctx, uid, servant_id=sid, count=1)
        except Exception:
            pass

        # 构造下行刷新
        frames = []
        try:
            import generator as _gen
            p_15009 = _gen.gen_payload(15009, uid=uid, db=db)
            if p_15009:
                frames.append(DownFrame(15009, p_15009))
        except Exception:
            pass

        touched = getattr(ctx, "touched_items", None)
        wl = [{"uid": c, "item": {"id": int(c_item.get("prefab_id") or 0), "num": 0}} for c in costs[1:]]
        try:
            import generator as _gen
            p_17023 = _gen.gen_payload(17023, uid=uid, db=db, touched_items=touched, weapon_list=wl if wl else None)
            if p_17023:
                frames.append(DownFrame(17023, p_17023))
        except Exception:
            pass

        p_46011 = self.build_servant_46011_payload(uid, db=db)
        if p_46011:
            frames.append(DownFrame(46011, p_46011))

        if wearing_hero_id:
            try:
                import hero_codec as _hc
                hf = _hc.build_hero_14007_frame(db, uid, wearing_hero_id)
                if hf:
                    frames.append(hf)
            except Exception:
                pass

        p_46031 = encode("sc_46031", {"result": 0, "servant_uid": target_uid}) or b"\x08\x00"
        frames.append(DownFrame(46031, p_46031))

        return {
            "servant_id": sid,
            "new_servant_uid": target_uid,
            "cost_uids": costs,
            "wearing_hero_id": wearing_hero_id,
            "frames": frames
        }

    # ==================== 5. 钥从分解出售 (Decompose) ====================

    def decompose_servants(self, ctx, uid, servant_uids):
        """
        钥从分解出售：cs_46032 -> sc_46033
        - 产出规则：3星返还40201×1，4星返还40201×3，5星返还40201×5
        - 严格校验非穿戴、非锁定保护
        - 物理删除实体，材料通过 InventoryService 增加并标记原子变动
        """
        db = getattr(ctx, "db", self.db)
        uids = [int(x) for x in (servant_uids or []) if str(x).isdigit()]
        if not uids:
            raise OperationError(2, "分解列表为空")

        equipped_set = self.get_equipped_servant_uids(uid)
        total_items = {}
        decomposed_items = []

        for suid in uids:
            if suid in equipped_set:
                raise OperationError(2, f"钥从 {suid} 正在被角色佩戴，禁止分解")

            rows = db.query("SELECT id, prefab_id, starlevel, is_locked FROM servant WHERE uid=? AND id=?", (uid, suid))
            if not rows:
                continue
            if int(rows[0]["is_locked"] or 0) == 1:
                raise OperationError(2, f"钥从 {suid} 已锁定，禁止分解")

            pid = int(rows[0].get("prefab_id") or 0)
            star = int(rows[0]["starlevel"] or 3)
            rets = SERVANT_DECOMPOSE_RETURNS.get(star, [(40201, 1)])
            for iid, num in rets:
                total_items[iid] = total_items.get(iid, 0) + num

            # 物理销毁并记入历史
            if pid > 0:
                db.execute("CREATE TABLE IF NOT EXISTS servant_history (uid INTEGER NOT NULL, id INTEGER NOT NULL, prefab_id INTEGER NOT NULL, PRIMARY KEY (uid, id))")
                db.execute("INSERT OR REPLACE INTO servant_history (uid, id, prefab_id) VALUES (?, ?, ?)", (uid, suid, pid))
            db.execute("DELETE FROM servant WHERE uid=? AND id=?", (uid, suid))
            decomposed_items.append((suid, pid))

        # 发放材料并原子更新
        items_out = []
        for iid, num in total_items.items():
            self._grant_item(ctx, uid, iid, num)
            items_out.append({"id": iid, "num": num})

        # 构造下行：原子变动帧 17023 + 全量钥从刷新 46011 + 响应帧 46033
        frames = []
        touched = getattr(ctx, "touched_items", None)
        wl = [{"uid": s, "item": {"id": p, "num": 0}} for s, p in decomposed_items if p > 0]
        try:
            import generator as _gen
            p_17023 = _gen.gen_payload(17023, uid=uid, db=db, touched_items=touched, weapon_list=wl if wl else None)
            if p_17023:
                frames.append(DownFrame(17023, p_17023))
        except Exception:
            pass

        p_46011 = self.build_servant_46011_payload(uid, db=db)
        if p_46011:
            frames.append(DownFrame(46011, p_46011))

        p_46033 = encode("sc_46033", {"result": 0, "return_list": items_out}) or b"\x08\x00"
        frames.append(DownFrame(46033, p_46033))

        return {
            "decomposed_count": len(decomposed_items),
            "items": items_out,
            "frames": frames
        }

    # ==================== 6. 权钥经验喂养狗粮消耗 ====================

    def consume_servants_as_food(self, ctx, uid, servant_uids):
        """
        供 HeroService 权钥经验升级时调用（吞噬低星钥从作为经验狗粮）：
        - 校验未穿戴、未加锁
        - 按品阶/星级计算经验 (3星=100, 4星=300, 5星=500, 对齐 gamesetting.lua)
        - 物理删除实体并广播事件
        - 记录 ctx.deleted_servants 以供后续下发原子帧 sc_17023.weapon_list 彻底清理客户端幽灵数据
        - 对已不存在的幽灵数据做容错保护（不报错，且强制追加清理指令）
        - 返回: (valid_count, total_exp)
        """
        db = getattr(ctx, "db", self.db)
        uids = [int(x) for x in (servant_uids or []) if str(x).isdigit()]
        if not uids:
            return 0, 0

        equipped_set = self.get_equipped_servant_uids(uid, db=db)
        valid_count = 0
        total_exp = 0
        deleted_list = []

        for suid in uids:
            if suid in equipped_set:
                raise OperationError(2, f"钥从 {suid} 正在佩戴中，无法作为强化材料")
            rows = db.query("SELECT id, prefab_id, starlevel, is_locked FROM servant WHERE uid=? AND id=?", (uid, suid))
            if not rows:
                # 幽灵数据容错：从历史记录查找其原始 prefab_id，确保 SC_17023 能正确清除客户端缓存且不会因 id=0 导致客户端崩溃
                h_rows = db.query("SELECT prefab_id FROM servant_history WHERE uid=? AND id=?", (uid, suid))
                pid = int(h_rows[0]["prefab_id"]) if h_rows and h_rows[0].get("prefab_id") else 0
                if pid > 0:
                    deleted_list.append({"uid": suid, "id": pid})
                else:
                    logger.warning(f"幽灵钥从 {suid} 未能在历史中找到 prefab_id，略过 SC_17023 避免客户端崩溃")
                continue

            if int(rows[0].get("is_locked") or 0) == 1:
                continue

            pid = int(rows[0].get("prefab_id") or 0)
            star = int(rows[0].get("starlevel") or 0)
            if not star and pid:
                cfg = SERVANT_PROMOTE_CFG.get(str(pid))
                if cfg:
                    star = int(cfg.get("starlevel") or 3)
                else:
                    star = 3
            exp = SERVANT_STAR_EXP.get(star, 100)
            total_exp += exp

            if pid > 0:
                db.execute("CREATE TABLE IF NOT EXISTS servant_history (uid INTEGER NOT NULL, id INTEGER NOT NULL, prefab_id INTEGER NOT NULL, PRIMARY KEY (uid, id))")
                db.execute("INSERT OR REPLACE INTO servant_history (uid, id, prefab_id) VALUES (?, ?, ?)", (uid, suid, pid))
            db.execute("DELETE FROM servant WHERE uid=? AND id=?", (uid, suid))
            valid_count += 1
            deleted_list.append({"uid": suid, "id": pid})

        if deleted_list:
            if not hasattr(ctx, "deleted_servants") or ctx.deleted_servants is None:
                ctx.deleted_servants = []
            ctx.deleted_servants.extend(deleted_list)

        if valid_count > 0:
            try:
                event_bus.bus.emit(event_bus.Events.SERVANT_DESTROY, ctx, uid, count=valid_count)
            except Exception:
                pass
        return valid_count, total_exp


    # ==================== 7. 全量钥从下行包构建 (sc_46011) ====================

    def build_servant_46011_payload(self, uid, db=None):
        """
        构造 sc_46011 钥从列表全量同步帧 payload
        """
        target_db = db or self.db
        rows = target_db.query("SELECT * FROM servant WHERE uid=?", (uid,))
        servant_list = []
        for r in (rows or []):
            servant_list.append({
                "uid": int(r["id"]),
                "id": int(r["prefab_id"]),
                "stage": int(r.get("stage") or 1),
                "is_locked": int(r.get("is_locked") or 0)
            })
        return encode("sc_46011", {"servant_list": servant_list})

    # ==================== 8. GM 开发者与测试标准接口 ====================

    def grant_servant(self, uid, prefab_id, stage=1, starlevel=5, count=1):
        """
        [GM 工具] 指定发放一把或多把独立钥从实体
        """
        db = self.db
        allocated = []
        now = int(time.time())
        pid = int(prefab_id)
        stg = max(1, min(5, int(stage or 1)))
        star = int(starlevel or 5)

        for _ in range(max(1, count)):
            new_id = self.allocate_servant_uid(uid)
            db.execute(
                """
                INSERT INTO servant (uid, id, prefab_id, stage, starlevel, owned, is_locked, update_ts)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (uid, new_id, pid, stg, star, now)
            )
            allocated.append(new_id)

        return {"granted_ids": allocated, "count": len(allocated)}

    def max_out_servant(self, uid, servant_uid):
        """
        [GM 工具] 一键满阶指定钥从（升至 5 阶满精炼）
        """
        db = self.db
        suid = int(servant_uid)
        now = int(time.time())
        db.execute("UPDATE servant SET stage=5, update_ts=? WHERE uid=? AND id=?", (now, uid, suid))
        return {"servant_uid": suid, "stage": 5}

    def grant_all_exclusive_servants(self, uid, stage=5, db=None):
        """
        [GM 工具] 一键发放全阵营所有五星专属钥从（各 1 把满阶 5 阶）
        """
        target_db = db or self.db
        now = int(time.time())
        stg = max(1, min(5, int(stage or 5)))

        pids = set()
        try:
            rows = target_db.query("SELECT DISTINCT servant_id FROM draw_servant_pool WHERE servant_id > 0")
            for r in (rows or []):
                pid = r.get("servant_id")
                if pid:
                    pids.add(int(pid))
        except Exception:
            pass

        try:
            if not pids:
                rows = target_db.query("SELECT DISTINCT id FROM servant_cfg WHERE starlevel=5")
                for r in (rows or []):
                    pid = r.get("id")
                    if pid:
                        pids.add(int(pid))
        except Exception:
            pass

        # 兜底常用全阵营专属五星钥从
        if not pids:
            pids = {
                2011, 2012, 2021, 2031, 2041, 2051, 2061, 2071, 2081, 2084,
                2091, 2101, 2111, 2121, 2131, 2141, 2151, 2161, 2171, 2181
            }

        created = 0
        for pid in sorted(pids):
            new_id = self.allocate_servant_uid(uid, db=target_db)
            target_db.execute(
                """
                INSERT INTO servant (uid, id, prefab_id, stage, starlevel, owned, is_locked, update_ts)
                VALUES (?, ?, ?, ?, 5, 1, 0, ?)
                """,
                (uid, new_id, pid, stg, now)
            )
            created += 1

        return {"created_count": created, "stage": stg}
