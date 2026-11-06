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

CHIP_UNLOCK_CFG = {}
try:
    with open(os.path.join(BASE_DIR, 'chip_unlock_cfg.json'), encoding='utf-8') as _f:
        CHIP_UNLOCK_CFG = json.load(_f)
except (OSError, ValueError) as _e:
    logger.warning('加载芯片解锁配置失败，拒绝猜测资格: %s', _e)


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

    def _unlock_config(self, cid, kind=None):
        cfg = CHIP_UNLOCK_CFG.get('chips', {}).get(str(cid))
        if not cfg or (kind is not None and cfg.get('type_id') != kind):
            raise OperationError(2, '芯片不在当前配置中或类别不匹配')
        return cfg

    def _chip_conditions_met(self, db, uid, condition_ids):
        if not condition_ids:
            return True
        conditions = [CHIP_UNLOCK_CFG.get('conditions', {}).get(str(cid)) for cid in condition_ids]
        if any(not c or c.get('type') not in (1015,1025,1026,1027)
               or len(c.get('params', [])) != 2 for c in conditions):
            return False
        hosts = sorted({int(c['params'][0]) for c in conditions})
        rows = db.query('SELECT id,unlock,level,star,skill_intensify FROM hero WHERE uid=? AND id IN ('+
                        ','.join('?' for _ in hosts)+')', (uid,*hosts))
        heroes = {int(r['id']): r for r in rows}
        for c in conditions:
            hid, need = map(int, c['params'])
            h = heroes.get(hid)
            if not h or int(h.get('unlock') or 0) != 1:
                return False
            kind = c['type']
            if kind == 1015 and int(h.get('level') or 0) < need:
                return False
            if kind == 1026 and int(h.get('star') or 0) < need:
                return False
            if kind == 1025:
                try:
                    attrs = json.loads(h.get('skill_intensify') or '[]')
                    if not isinstance(attrs, list):return False
                    values = {}
                    for item in attrs:
                        if (not isinstance(item,list) or len(item)!=2 or type(item[0]) is not int
                                or item[0] not in (1,2,3,4,5) or item[0] in values
                                or type(item[1]) is not int or item[1]<0):return False
                        values[item[0]] = item[1]
                except (TypeError, ValueError):
                    return False
                if sum(values.values()) < need:return False
            if kind == 1027:
                from archive_service import ArchiveService
                archive = ArchiveService.get_instance().get_archive_id(hid)
                rows = db.query('SELECT exp FROM hero_archive WHERE uid=? AND archive_id=?', (uid,archive))
                levels = CHIP_UNLOCK_CFG.get('love_level_exp', [])
                if not archive or not levels or not 1 <= need <= len(levels):return False
                exp = int(rows[0].get('exp') or 0) if rows else 0
                if exp < sum(levels[:need-1]):return False
        return True

    def _store_unlocked_chip(self, db, uid, cid, cfg, cat):
        rows = db.query('SELECT cat FROM chip WHERE uid=? AND id=?', (uid,cid))
        if rows and rows[0]['cat'] != cat:
            raise OperationError(2, '芯片记录类别与配置不一致')
        db.execute('''INSERT INTO chip(uid,id,cat,type_id,spec_char,role_type_id,unlocked,new_condition,cost_condition,module_condition,update_ts)
                      VALUES(?,?,?,?,?,?,1,?,?,?,?) ON CONFLICT(uid,id) DO UPDATE SET
                      type_id=excluded.type_id,spec_char=excluded.spec_char,role_type_id=excluded.role_type_id,
                      unlocked=1,new_condition=excluded.new_condition,cost_condition=excluded.cost_condition,
                      module_condition=excluded.module_condition,update_ts=excluded.update_ts''',
                   (uid,cid,cat,cfg['type_id'],cfg['spec_char'],cfg['role_type_id'],cfg['new_condition'],
                    json.dumps(cfg['costs']),json.dumps(cfg['module_conditions']),int(time.time())))

    def _deduct_item(self, ctx, uid, item_id, count):
        """原子扣除材料，优先委托给 inventory_service 并触发 touched_items 打标。"""
        iid = int(item_id)
        cnt = int(count)
        if cnt <= 0:
            return
        try:
            import inventory_service
        except ModuleNotFoundError as exc:
            if exc.name != "inventory_service":
                raise
        else:
            # Once cost_item is called its outcome may include writes. Never
            # debit again on an exception; let the caller's transaction roll back.
            succ = inventory_service.cost_item(ctx, uid, iid, cnt)
            if not succ:
                t, have = inventory_service.get_item_balance(ctx, uid, iid)
                raise OperationError(3, f"材料不足: 道具 {iid} 需 {cnt}，当前仅有 {have}")
            return

        # Legacy compatibility only when inventory_service was never imported.
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

    def _require_mimir_kernel(self, db, uid, kernel_id):
        rows = db.query("SELECT unlocked,type_id,remark FROM chip WHERE uid=? AND cat='kernel' AND id=?",
                        (uid, kernel_id))
        if (not rows or int(rows[0].get('type_id') or 0) != 1
                or int(rows[0].get('unlocked') or 0) != 1):
            raise OperationError(2, "管理喵不存在或尚未解锁")
        return rows[0]

    def _validate_mimir_chips(self, db, uid, chips, *, require_unlocked):
        """Validate the whole proposal, never filter a successful client's list.

        ChipCfg.type_id=2 is the Mimir page's selectable list. Character-assist
        type 4 uses its separate battle interface; it is not a secondary chip.
        Saved planning proposals may contain configured but still locked chips.
        """
        if (not isinstance(chips, list) or len(chips) > MIMIR_SECONDARY_EQUIP_MAX
                or any(type(cid) is not int or cid <= 0 for cid in chips)
                or len(set(chips)) != len(chips)):
            raise OperationError(2, "芯片方案数量、类型或重复条目无效")
        characters = set()
        for cid in chips:
            rows = db.query("SELECT unlocked,type_id,spec_char FROM chip WHERE uid=? AND cat='secondary' AND id=?",
                            (uid, cid))
            if not rows or int(rows[0].get('type_id') or 0) != 2:
                raise OperationError(2, "方案包含未知或不适用的次级芯片")
            row = rows[0]
            if require_unlocked and int(row.get('unlocked') or 0) != 1:
                raise OperationError(2, "方案包含尚未解锁的次级芯片")
            character = int(row.get('spec_char') or 0)
            if character and character in characters:
                raise OperationError(2, "方案包含互斥的同角色芯片")
            if character:
                characters.add(character)
        return list(chips)

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
        op = int(oper)
        if op not in (1, 2):
            raise OperationError(2, "无效的芯片装卸操作")
        row = self._require_mimir_kernel(db, uid, kid)
        self._validate_mimir_chips(db, uid, [sid], require_unlocked=(op == 1))

        try:
            chips = json.loads(row.get('remark') or '[]')
        except (TypeError, ValueError):
            raise OperationError(2, "当前芯片装配数据损坏，请先清空装配")
        if not isinstance(chips, list):
            raise OperationError(2, "当前芯片装配数据格式无效")

        if op == 1:  # 装配
            if sid not in chips:
                chips.append(sid)
        elif op == 2:  # 卸下
            if sid in chips:
                chips.remove(sid)

        self._validate_mimir_chips(db, uid, chips, require_unlocked=True)

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
        self._require_mimir_kernel(db, uid, kid)
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
        if pid <= 0:
            raise OperationError(2, "无效的芯片方案编号")
        sec = self._validate_mimir_chips(db, uid, secondary_list, require_unlocked=False)
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
        self._require_mimir_kernel(db, uid, kid)

        prow = db.query("SELECT secondary FROM chip_proposal WHERE uid=? AND id=?", (uid, pid))
        if not prow:
            raise OperationError(2, "芯片方案不存在")
        try:
            sec = json.loads(prow[0]['secondary'])
        except (TypeError, ValueError):
            raise OperationError(2, "芯片方案数据损坏")
        sec = self._validate_mimir_chips(db, uid, sec, require_unlocked=True)

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

        hrow = db.query("SELECT chip_state FROM hero WHERE uid=? AND id=?", (uid, hid))
        if not hrow:
            raise OperationError(2, "目标角色不存在")
        if chip < 0:
            raise OperationError(2, "无效的AI芯片ID")

        # 若为装配，校验芯片是否已解锁
        if chip > 0:
            crow = db.query("SELECT unlocked FROM chip WHERE uid=? AND cat='hero' AND id=?",
                            (uid, chip))
            if not crow or int(crow[0].get("unlocked") or 0) != 1:
                raise OperationError(2, f"AI芯片 {chip} 尚未解锁，无法装配")
            # Old valid rows can lack static metadata. Ownership is durable in
            # the DB; category, host and slot come from the native catalog.
            cfg = self._unlock_config(chip, 3)
            if cfg['spec_char'] != hid or cfg['role_type_id'] != sid:
                raise OperationError(2, "AI芯片与角色或槽位不匹配")

        try:
            cs = json.loads(hrow[0].get('chip_state') or '{}')
        except (TypeError, ValueError):
            raise OperationError(2, "当前角色芯片装配数据损坏")
        # 新角色默认值和旧存档可能用空数组表示尚未装配。
        if cs == []:
            cs = {}
        if not isinstance(cs, dict):
            raise OperationError(2, "当前角色芯片装配格式无效")

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
        """Validate the native target, host, level condition and complete price."""
        db = self._get_db(ctx)
        cid = int(chip_id or 0)
        cfg = self._unlock_config(cid, 3)
        row = db.query("SELECT unlocked FROM chip WHERE uid=? AND cat='hero' AND id=?", (uid,cid))
        if row and int(row[0].get('unlocked') or 0) == 1:
            return {'chip_id': cid, 'cat': 'hero'}
        host = int(cfg['host_id'])
        heroes = db.query('SELECT unlock FROM hero WHERE uid=? AND id=?', (uid,host))
        condition = int(cfg['new_condition'])
        if (not heroes or int(heroes[0].get('unlock') or 0) != 1
                or not self._chip_conditions_met(db,uid,[condition] if condition else [])):
            raise OperationError(2005, 'AI芯片解锁条件未达成')
        costs = {}
        for iid,num in cfg['costs']:
            costs[int(iid)] = costs.get(int(iid),0) + int(num)
        from inventory_service import InventoryService
        for iid,num in costs.items():
            if InventoryService.get_item_balance(db,uid,iid)[1] < num:
                raise OperationError(3, '芯片解锁材料不足')
        for iid,num in costs.items():
            if num:self._deduct_item(ctx,uid,iid,num)
        self._store_unlocked_chip(db,uid,cid,cfg,'hero')
        event_bus.bus.emit(event_bus.Events.CHIP_UNLOCK,ctx,uid,chip_id=cid,cat='hero',hero_id=host)
        return {'chip_id': cid, 'cat': 'hero'}

    # =========================================================================
    # 4. 角色助战模块芯片子系统 (Reviser / Character Chips)
    # =========================================================================

    def get_unlocked_reviser_chips(self, uid):
        """获取所有已解锁的角色助战模块芯片 ID (BASE/EXTRA)。"""
        db = self._get_db()
        rows = db.query("SELECT id FROM chip WHERE uid=? AND cat='reviser' AND unlocked=1 ORDER BY id", (uid,))
        return [r["id"] for r in rows]

    def check_char_chip_can_unlock(self, uid, chip_id, db=None):
        """Shared red-point/action guard using native base references and order."""
        db = db or self._get_db()
        cid = int(chip_id or 0)
        cfg = self._unlock_config(cid, 4)
        sequence = CHIP_UNLOCK_CFG.get('reviser_sequences', {}).get(str(cfg['base_id']))
        if not sequence or cid not in sequence:
            return False, '缺少有效的助战芯片顺序配置'
        owned = {int(r['id']) for r in db.query("SELECT id FROM chip WHERE uid=? AND cat='reviser' AND unlocked=1", (uid,))}
        if cid in owned:return True, '已解锁'
        if next((i for i in sequence if i not in owned),None) != cid:
            return False, '前置助战芯片尚未解锁'
        if not self._chip_conditions_met(db,uid,cfg['module_conditions']):
            return False, '助战芯片养成条件未达成'
        return True, '满足解锁条件'

    def get_char_chip_redpoint_states(self, uid):
        """扫描所有未解锁的角色助战芯片，返回达到条件可点亮的红点字典。

        返回格式：{chip_id: bool}，供红点服务直接接入。
        """
        db = self._get_db()
        rows = db.query("SELECT id FROM chip WHERE uid=? AND cat='reviser' AND unlocked=0", (uid,))
        red_states = {}
        for r in rows:
            cid = r["id"]
            cfg = CHIP_UNLOCK_CFG.get('chips', {}).get(str(cid))
            if not cfg or cfg.get('type_id') != 4:
                continue
            can_unlock, _ = self.check_char_chip_can_unlock(uid, cid)
            if can_unlock:
                red_states[cid] = True
        return red_states

    def activate_reviser_chip(self, ctx, uid, chip_id):
        db = self._get_db(ctx)
        cid = int(chip_id or 0)
        cfg = self._unlock_config(cid, 4)
        row = db.query("SELECT unlocked FROM chip WHERE uid=? AND cat='reviser' AND id=?", (uid,cid))
        if row and int(row[0].get('unlocked') or 0) == 1:
            return {'chip_id': cid, 'cat': 'reviser'}
        allowed, reason = self.check_char_chip_can_unlock(uid,cid,db=db)
        if not allowed:raise OperationError(2005,reason)
        self._store_unlocked_chip(db,uid,cid,cfg,'reviser')
        event_bus.bus.emit(event_bus.Events.CHAR_CHIP_ACTIVATE,ctx,uid,hero_id=cfg['host_id'],
                           chip_id=cid,role_type_id=cfg['role_type_id'])
        event_bus.bus.emit(event_bus.Events.CHAR_CHIP_REDPOINT_CHECK,ctx,uid,hero_id=cfg['host_id'],
                           chip_id=cid,can_unlock=False)
        return {'chip_id': cid, 'cat': 'reviser'}

    # =========================================================================
    # 5. 统一分发门面 (Unified Dispatcher for cs_50002)
    # =========================================================================

    def _unlock_simple_chip(self, ctx, uid, cid, cat):
        db = self._get_db(ctx)
        rows = db.query('SELECT unlocked FROM chip WHERE uid=? AND cat=? AND id=?', (uid, cat, cid))
        if not rows:
            raise OperationError(2, "芯片配置不存在")
        if int(rows[0].get('unlocked') or 0) == 1:
            return {'chip_id': cid, 'cat': cat}
        db.execute('UPDATE chip SET unlocked=1,update_ts=? WHERE uid=? AND cat=? AND id=?',
                   (int(time.time()), uid, cat, cid))
        event_bus.bus.emit(event_bus.Events.CHIP_UNLOCK, ctx, uid, chip_id=cid, cat=cat, hero_id=0)
        return {'chip_id': cid, 'cat': cat}

    def unlock_any_chip(self, ctx, uid, chip_id):
        cid = int(chip_id or 0)
        cfg = self._unlock_config(cid)
        kind = cfg['type_id']
        if kind in (1,2):
            return self._unlock_simple_chip(ctx,uid,cid,'kernel' if kind==1 else 'secondary')
        if kind == 3:return self.unlock_hero_chip(ctx,uid,cid)
        if kind == 4:return self.activate_reviser_chip(ctx,uid,cid)
        raise OperationError(2, '未知芯片类别')

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
