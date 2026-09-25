# -*- coding: utf-8 -*-
"""
inventory_service.py — V5 服务端统一道具与资产管理服务 (Inventory Service)
统一纳管《深空之眼》全类型资产（货币、材料、碎片、刻印、钥从、修正者、皮肤、场景等），
负责出入库持久化、实例 ID 批量预分配、上下文变更跟踪，并在落库成功后统一触发 EventBus 广播。
"""

import os
import json
import time
import random
import logging
import event_bus

logger = logging.getLogger("inventory_service")

_DIR = os.path.dirname(os.path.abspath(__file__))
_EQUIP_MAP_CACHE = None
_USER_LEVEL_SETTINGS_CACHE = None


def _get_user_level_cfg():
    global _USER_LEVEL_SETTINGS_CACHE
    if _USER_LEVEL_SETTINGS_CACHE is None:
        p = os.path.join(_DIR, "user_level_setting.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _USER_LEVEL_SETTINGS_CACHE = {int(k): int(v) for k, v in json.load(f).items()}
            except Exception:
                _USER_LEVEL_SETTINGS_CACHE = {}
        else:
            _USER_LEVEL_SETTINGS_CACHE = {}
    return _USER_LEVEL_SETTINGS_CACHE


def _get_equip_map():
    global _EQUIP_MAP_CACHE
    if _EQUIP_MAP_CACHE is not None:
        return _EQUIP_MAP_CACHE
    p = os.path.join(_DIR, "equip_suit_pos_map.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                _EQUIP_MAP_CACHE = json.load(f)
                return _EQUIP_MAP_CACHE
        except Exception as e:
            logger.error(f"加载 equip_suit_pos_map.json 失败: {e}")
    _EQUIP_MAP_CACHE = {}
    return _EQUIP_MAP_CACHE


_ITEM_USABLE_CFG_CACHE = None
_DROP_CFG_CACHE = None


def _get_item_usable_cfg():
    global _ITEM_USABLE_CFG_CACHE
    if _ITEM_USABLE_CFG_CACHE is None:
        p = os.path.join(_DIR, "item_usable_cfg.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _ITEM_USABLE_CFG_CACHE = json.load(f)
            except Exception as e:
                logger.error(f"加载 item_usable_cfg.json 失败: {e}")
                _ITEM_USABLE_CFG_CACHE = {}
        else:
            _ITEM_USABLE_CFG_CACHE = {}
    return _ITEM_USABLE_CFG_CACHE


def _get_drop_cfg():
    global _DROP_CFG_CACHE
    if _DROP_CFG_CACHE is None:
        p = os.path.join(_DIR, "drop_cfg.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _DROP_CFG_CACHE = json.load(f)
            except Exception as e:
                logger.error(f"加载 drop_cfg.json 失败: {e}")
                _DROP_CFG_CACHE = {}
        else:
            _DROP_CFG_CACHE = {}
    return _DROP_CFG_CACHE


_ITEM_EXCHANGE_CFG_CACHE = None


def _get_exchange_cfg():
    global _ITEM_EXCHANGE_CFG_CACHE
    if _ITEM_EXCHANGE_CFG_CACHE is None:
        p = os.path.join(_DIR, "item_exchange_cfg.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _ITEM_EXCHANGE_CFG_CACHE = json.load(f)
            except Exception as e:
                logger.error(f"加载 item_exchange_cfg.json 失败: {e}")
                _ITEM_EXCHANGE_CFG_CACHE = {}
        else:
            _ITEM_EXCHANGE_CFG_CACHE = {}
    return _ITEM_EXCHANGE_CFG_CACHE


_STICKER_ITEM_MAP_CACHE = None


def get_sticker_item_map():
    global _STICKER_ITEM_MAP_CACHE
    if _STICKER_ITEM_MAP_CACHE is None:
        cfg = _get_item_usable_cfg()
        m = {}
        for k, v in (cfg or {}).items():
            iid = int(k)
            itype = int(v.get("type") or 0)
            param = v.get("param")
            if itype == 20 or (90000 <= iid < 92000):
                if param and isinstance(param, list) and len(param) > 0:
                    m[iid] = int(param[0])
                else:
                    m[iid] = iid
        _STICKER_ITEM_MAP_CACHE = m
    return _STICKER_ITEM_MAP_CACHE


class ItemType:
    CURRENCY = "currency"
    HERO = "hero"
    HERO_PIECE = "hero_piece"
    EQUIP = "equip"
    SERVANT = "servant"
    SKIN = "skin"
    SCENE = "scene"
    FURNITURE = "furniture"
    FURNITURE_SUIT = "furniture_suit"
    CANTEEN_INGREDIENTS = "canteen_ingredients"
    COSMETIC = "cosmetic"
    MATERIAL = "material"


COSMETIC_KIND_MAP = {
    11: "portrait",
    12: "icon_frame",
    13: "sticker",
    18: "sticker_bg",
    22: "title",
    23: "card_bg",
    25: "home_bg",
    26: "bubble",
    28: "portrait",
}


class InventoryService:
    """统一道具与资产管理服务单例/门面"""

    @classmethod
    def calc_player_level(cls, total_exp):
        """根据 user_level_setting.json 累加总经验计算玩家真实等级。"""
        cfg = _get_user_level_cfg()
        if not cfg:
            return 92
        lv = 1
        rem = int(total_exp or 0)
        while lv in cfg and rem >= cfg[lv]:
            rem -= cfg[lv]
            lv += 1
        return lv

    @classmethod
    def classify_item(cls, db, item_id):
        """权威判定道具资产类别 (ItemType)"""
        iid = int(item_id or 0)
        if iid <= 0:
            return ItemType.MATERIAL

        # 1. 货币特判区间 (主货币/活动货币/代币)
        if iid < 1000 or (54000 <= iid <= 55000) or (60000 <= iid <= 61000):
            return ItemType.CURRENCY

        # 2. item_catalog 权威配置表判定
        if db is not None:
            try:
                rows = db.query("SELECT type FROM item_catalog WHERE id=?", (iid,))
                if rows and rows[0]["type"]:
                    itype = int(rows[0]["type"])
                    if itype == 1:
                        return ItemType.CURRENCY
                    if itype == 2:
                        return ItemType.HERO
                    if itype == 3:
                        return ItemType.HERO_PIECE
                    if itype == 7:
                        return ItemType.EQUIP
                    if itype == 9:
                        return ItemType.SERVANT
                    if itype == 8:
                        return ItemType.SKIN
                    if itype == 21:
                        return ItemType.SCENE
                    if itype == 15:
                        return ItemType.FURNITURE
                    if itype == 24:
                        return ItemType.FURNITURE_SUIT
                    if itype == 16:
                        return ItemType.CANTEEN_INGREDIENTS
                    if itype in (11, 12, 13, 18, 22, 23, 25, 26, 28):
                        return ItemType.COSMETIC
                    if itype in (4, 5, 6, 10, 14, 20):
                        return ItemType.MATERIAL
            except Exception:
                pass

        # 3. 兜底启发式规则 (Heuristics Fallback)
        if (20000 <= iid < 30000) or (200000 <= iid < 300000):
            return ItemType.SERVANT
        if 400000 <= iid < 700000:
            return ItemType.EQUIP
        if 1000 <= iid < 2000:
            return ItemType.HERO
        if 11000 <= iid < 12000:
            return ItemType.HERO_PIECE
        if 950000 <= iid < 970000:
            return ItemType.FURNITURE

        return ItemType.MATERIAL

    @classmethod
    def get_item_balance(cls, ctx_or_db, uid, item_id):
        """查询道具/资产当前余额，返回 (item_type, balance_count)"""
        iid = int(item_id or 0)
        db = getattr(ctx_or_db, "db", ctx_or_db)
        if not db or iid <= 0:
            return ItemType.MATERIAL, 0

        t = cls.classify_item(db, iid)

        if t == ItemType.CURRENCY:
            if iid == 12:
                rows = db.query("SELECT exp FROM users WHERE uid=?", (uid,))
                return t, int(rows[0]["exp"] or 0) if rows else 0
            rows = db.query("SELECT num FROM currency WHERE uid=? AND id=?", (uid, iid))
            return t, int(rows[0]["num"] or 0) if rows else 0

        elif t == ItemType.HERO:
            rows = db.query("SELECT unlock FROM hero WHERE uid=? AND id=?", (uid, iid))
            is_unlocked = 1 if rows and int(rows[0].get("unlock") or 0) == 1 else 0
            return t, is_unlocked

        elif t == ItemType.HERO_PIECE:
            hid = iid % 10000 if iid > 10000 else iid
            rows = db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hid))
            return t, int(rows[0]["num"] or 0) if rows else 0

        elif t == ItemType.EQUIP:
            rows = db.query("SELECT COUNT(*) AS num FROM equip WHERE uid=? AND prefab_id=?", (uid, iid))
            return t, int(rows[0]["num"] or 0) if rows else 0

        elif t == ItemType.SERVANT:
            rows = db.query("SELECT COUNT(*) AS num FROM servant WHERE uid=? AND prefab_id=?", (uid, iid))
            return t, int(rows[0]["num"] or 0) if rows else 0

        elif t == ItemType.SKIN:
            rows = db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, iid))
            return t, 1 if rows else 0

        elif t == ItemType.SCENE:
            rows = db.query("SELECT 1 FROM user_scene WHERE uid=? AND scene_id=?", (uid, iid))
            return t, 1 if rows else 0

        elif t == ItemType.COSMETIC:
            rows = db.query("SELECT 1 FROM player_card WHERE uid=? AND item_id=? AND obtained=1", (uid, iid))
            if rows:
                return t, 1
            m_rows = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
            return t, int(m_rows[0]["num"] or 0) if m_rows else 0

        else:
            rows = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
            return t, int(rows[0]["num"] or 0) if rows else 0

    @classmethod
    def _check_overflow_and_exchange(cls, ctx, db, uid, iid, count, cur_num, limit, exch_info, source=""):
        """
        根据官方 ItemCfg / ShopTools 规则校验限额与过量分解转换逻辑
        :param cur_num: 当前持有量/解锁状态
        :param limit: 最大堆叠/持有上限 (0 表示不限制)
        :param exch_info: 来自 item_exchange_cfg.json 的配置 dict
        :return: (actual_add, surplus, exchange_grants)
        """
        if limit <= 0:
            return count, 0, []

        if cur_num >= limit:
            actual_add = 0
            surplus = count
        else:
            can_add = limit - cur_num
            actual_add = min(count, can_add)
            surplus = count - actual_add

        exchange_grants = []
        if surplus > 0 and exch_info:
            num_exchange = exch_info.get("num_exchange_item")
            if num_exchange and isinstance(num_exchange, list):
                for entry in num_exchange:
                    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        eid = int(entry[0])
                        enum = int(entry[1]) * surplus
                        if eid > 0 and enum > 0:
                            exchange_grants.append((eid, enum))

        return actual_add, surplus, exchange_grants

    @classmethod
    def grant_item(cls, ctx, uid, item_id, count=1, silent=False, source="", **kwargs):
        """单道具发放统一入口"""
        return cls.grant_items(ctx, uid, [(item_id, count)], silent=silent, source=source)

    @classmethod
    def grant_items(cls, ctx, uid, item_list, silent=False, source=""):
        """
        批量道具发放统一入口
        :param ctx: 请求上下文 (包含 db, touched_items, log 等) 或直接传入 db
        :param uid: 玩家 UID
        :param item_list: [(item_id, count), ...] 或 [[item_id, count], ...]
        :param silent: 是否静默入库（True 时不触发 EventBus 广播，适用于后台数据迁移、启动对齐等）
        :param source: 业务来源标记 (如 shop, battle, mail, gacha, task, gm)
        :return: 归类统计结果 dict
        """
        if not item_list:
            return {}

        db = getattr(ctx, "db", ctx)
        if db is None:
            return {}

        now = int(time.time())
        summary = {
            "currency": {},
            "material": {},
            "hero_piece": {},
            "hero_unlocked": [],
            "hero_converted": [],
            "equips": [],
            "servants": [],
            "skins": [],
            "scenes": [],
            "overflow_converted": [],
        }

        # 待触发的后置事件列表（确保 DB 操作完全成功后才 emit）
        pending_events = []

        # 1. 归类整理待入库数据
        equip_items = []
        servant_items = []

        for item in item_list:
            if not item:
                continue
            iid = int(item[0])
            count = int(item[1]) if len(item) > 1 else 1
            if count <= 0 or iid <= 0:
                continue

            itype = cls.classify_item(db, iid)

            if itype == ItemType.CURRENCY:
                if iid == 12:  # 管理员/玩家账号经验 (CURRENCY_TYPE_USER_EXP)
                    u_rows = db.query("SELECT exp, level FROM users WHERE uid=?", (uid,))
                    old_exp = int(u_rows[0]["exp"] or 0) if u_rows else 0
                    old_lv = int(u_rows[0]["level"] or 1) if u_rows else 1
                    new_exp = old_exp + count
                    new_lv = cls.calc_player_level(new_exp)
                    db.execute("UPDATE users SET exp=?, level=? WHERE uid=?", (new_exp, new_lv, uid))
                    # 保持 currency 表中的 id=12 严格等于实际 total_exp (用于 sc_15009/sc_17023 全量与差量)
                    db.execute(
                        "INSERT INTO currency (uid, id, num, update_ts) VALUES (?, 12, ?, ?) "
                        "ON CONFLICT(uid, id) DO UPDATE SET num = excluded.num, update_ts = excluded.update_ts",
                        (uid, new_exp, now),
                    )
                    # 若升级，按各级上限累加赠送体力、时间戳清零进入静止态，并广播升级事件
                    if new_lv > old_lv:
                        import fatigue_service as _fs
                        stamina_gain = sum(_fs.get_max_fatigue_by_level(lvl) for lvl in range(old_lv + 1, new_lv + 1))
                        _fs.on_fatigue_grant(db, uid, stamina_gain, now_ts=now)
                        summary["currency"][4] = summary["currency"].get(4, 0) + stamina_gain
                        if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                            ctx.touched_items.add(4)
                        pending_events.append((event_bus.Events.PLAYER_LEVEL_UP, {
                            "old_lv": old_lv,
                            "new_lv": new_lv,
                            "stamina_gain": stamina_gain,
                        }))

                    summary["currency"][12] = new_exp
                    if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                        ctx.touched_items.add(12)

                elif iid == 4:  # 体力/行动力 (CURRENCY_TYPE_FATIGUE)
                    import fatigue_service as _fs
                    _fs.on_fatigue_grant(db, uid, count, now_ts=now)
                    summary["currency"][4] = summary["currency"].get(4, 0) + count
                    if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                        ctx.touched_items.add(4)

                else:
                    db.execute(
                        "INSERT INTO currency (uid, id, num, update_ts) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts",
                        (uid, iid, count, now),
                    )
                    summary["currency"][iid] = summary["currency"].get(iid, 0) + count
                    if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                        ctx.touched_items.add(iid)

                    # 活动点/活跃度 (22每日 / 35每周 / 23) 联动
                    if iid in (22, 23, 35):
                        pt_id = 1 if iid == 22 else (3 if iid == 35 else 2)
                        if hasattr(db, "add_activity_point"):
                            db.add_activity_point(uid, pt_id, count)
                        pending_events.append((event_bus.Events.ACTIVITY_POINT_GAIN, {
                            "pt_id": pt_id, "amount": count, "item_id": iid
                        }))

                    # 战令经验 (14) 联动
                    elif iid == 14:
                        bp_rows = db.query("SELECT weekly_gain_exp FROM battlepass WHERE uid=?", (uid,))
                        if bp_rows:
                            cur_wk = int(bp_rows[0]["weekly_gain_exp"] or 0)
                            new_wk = min(15000, cur_wk + count)
                            db.execute("UPDATE battlepass SET weekly_gain_exp=? WHERE uid=?", (new_wk, uid))

            elif itype == ItemType.MATERIAL:
                exch_info = _get_exchange_cfg().get(str(iid))
                is_dyn_sticker = (90000 <= iid < 92000)

                # 判断是否有配置限额或属于带分解配置的动态表情
                if exch_info and (exch_info.get("limit", 0) > 0 or exch_info.get("num_exchange_item")):
                    limit = int(exch_info.get("limit") or 1)
                    m_rows = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
                    cur_m = int(m_rows[0]["num"] or 0) if m_rows else 0
                    if is_dyn_sticker:
                        s_rows = db.query("SELECT 1 FROM chat_sticker WHERE uid=? AND emoji_id=?", (uid, iid))
                        cur_num = max(cur_m, 1 if s_rows else 0)
                    else:
                        cur_num = cur_m

                    actual_add, surplus, exch_grants = cls._check_overflow_and_exchange(
                        ctx, db, uid, iid, count, cur_num, limit, exch_info, source=source
                    )

                    if actual_add > 0:
                        new_num = min(limit, cur_m + actual_add)
                        db.execute(
                            "INSERT INTO material (uid, id, num, update_ts) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(uid, id) DO UPDATE SET num = ?, update_ts = ?",
                            (uid, iid, new_num, now, new_num, now),
                        )
                        if is_dyn_sticker:
                            stk_map = get_sticker_item_map()
                            real_eid = stk_map.get(iid, iid)
                            db.execute(
                                "INSERT OR IGNORE INTO chat_sticker (uid, emoji_id, name, desc) VALUES (?, ?, '', '')",
                                (uid, real_eid)
                            )
                            if hasattr(ctx, "touched_chat_stickers"):
                                ctx.touched_chat_stickers = True
                            setattr(ctx, "touched_chat_stickers", True)
                        summary["material"][iid] = summary["material"].get(iid, 0) + actual_add
                        if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                            ctx.touched_items.add(iid)

                    if surplus > 0:
                        logger.info(f"[InventoryService] 材料/表情 {iid} 触发限额 (拥有={cur_num}, 请求={count}), 溢出={surplus}")
                        if exch_grants:
                            cls.grant_items(ctx, uid, exch_grants, silent=True, source=f"{source}_overflow")
                            for eid, enum in exch_grants:
                                summary["overflow_converted"].append({"src_id": iid, "surplus": surplus, "target_id": eid, "target_num": enum})
                else:
                    db.execute(
                        "INSERT INTO material (uid, id, num, update_ts) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts",
                        (uid, iid, count, now),
                    )
                    if is_dyn_sticker:
                        stk_map = get_sticker_item_map()
                        real_eid = stk_map.get(iid, iid)
                        db.execute(
                            "INSERT OR IGNORE INTO chat_sticker (uid, emoji_id, name, desc) VALUES (?, ?, '', '')",
                            (uid, real_eid)
                        )
                        if hasattr(ctx, "touched_chat_stickers"):
                            ctx.touched_chat_stickers = True
                        setattr(ctx, "touched_chat_stickers", True)
                    summary["material"][iid] = summary["material"].get(iid, 0) + count
                    if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                        ctx.touched_items.add(iid)

            elif itype == ItemType.HERO_PIECE:
                hid = iid % 10000 if iid > 10000 else iid
                db.execute(
                    "INSERT INTO hero_piece (uid, hero_id, num) VALUES (?, ?, ?) "
                    "ON CONFLICT(uid, hero_id) DO UPDATE SET num = num + excluded.num",
                    (uid, hid, count),
                )
                summary["hero_piece"][hid] = summary["hero_piece"].get(hid, 0) + count
                piece_iid = iid if iid > 10000 else (10000 + hid)
                if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                    ctx.touched_items.add(piece_iid)

            elif itype == ItemType.HERO:
                for _ in range(count):
                    is_new, is_piece, hid, pieces = cls._grant_single_hero(ctx, db, uid, iid, now)
                    if is_new:
                        summary["hero_unlocked"].append(hid)
                        pending_events.append((event_bus.Events.HERO_UNLOCK, {"hero_id": hid}))
                    elif is_piece:
                        summary["hero_converted"].append({"hero_id": hid, "pieces": pieces})
                        summary["hero_piece"][hid] = summary["hero_piece"].get(hid, 0) + pieces
                        piece_iid = 10000 + hid
                        if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                            ctx.touched_items.add(piece_iid)
                if hasattr(ctx, "touched_heroes") and ctx.touched_heroes is not None:
                    ctx.touched_heroes.add(iid)

            elif itype == ItemType.EQUIP:
                equip_items.append((iid, count))

            elif itype == ItemType.SERVANT:
                servant_items.append((iid, count))

            elif itype == ItemType.SKIN:
                exch_info = _get_exchange_cfg().get(str(iid))
                limit = int(exch_info.get("limit") or 1) if exch_info else 1
                rows = db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, iid))
                cur_num = 1 if rows else 0

                actual_add, surplus, exch_grants = cls._check_overflow_and_exchange(
                    ctx, db, uid, iid, count, cur_num, limit, exch_info, source=source
                )

                if actual_add > 0:
                    if hasattr(db, "upsert"):
                        db.upsert(
                            "player_skin_unlocked",
                            uid,
                            {"skin_id": iid, "unlock_ts": now, "update_ts": now},
                            keys=("uid", "skin_id"),
                        )
                    else:
                        db.execute(
                            "INSERT INTO player_skin_unlocked (uid, skin_id, unlock_ts, update_ts) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(uid, skin_id) DO UPDATE SET update_ts = excluded.update_ts",
                            (uid, iid, now, now),
                        )
                    summary["skins"].append(iid)

                if surplus > 0:
                    logger.info(f"[InventoryService] 皮肤 {iid} 触发保护性限额 (拥有={cur_num}, 请求={count}), 溢出={surplus}")
                    if exch_grants:
                        cls.grant_items(ctx, uid, exch_grants, silent=True, source=f"{source}_overflow")
                        for eid, enum in exch_grants:
                            summary["overflow_converted"].append({"src_id": iid, "surplus": surplus, "target_id": eid, "target_num": enum})

            elif itype == ItemType.SCENE:
                exch_info = _get_exchange_cfg().get(str(iid))
                limit = int(exch_info.get("limit") or 1) if exch_info else 1
                rows = db.query("SELECT 1 FROM user_scene WHERE uid=? AND scene_id=?", (uid, iid))
                cur_num = 1 if rows else 0

                actual_add, surplus, exch_grants = cls._check_overflow_and_exchange(
                    ctx, db, uid, iid, count, cur_num, limit, exch_info, source=source
                )

                if actual_add > 0:
                    if hasattr(db, "upsert"):
                        db.upsert(
                            "user_scene",
                            uid,
                            {"scene_id": iid, "lasted_time": 0, "obtain_time": now, "update_ts": now},
                            keys=("uid", "scene_id"),
                        )
                    else:
                        db.execute(
                            "INSERT INTO user_scene (uid, scene_id, lasted_time, obtain_time, update_ts) VALUES (?, ?, 0, ?, ?) "
                            "ON CONFLICT(uid, scene_id) DO UPDATE SET update_ts = excluded.update_ts",
                            (uid, iid, now, now),
                        )
                    summary["scenes"].append(iid)

                if surplus > 0:
                    logger.info(f"[InventoryService] 场景 {iid} 触发保护性限额 (拥有={cur_num}, 请求={count}), 溢出={surplus}")
                    if exch_grants:
                        cls.grant_items(ctx, uid, exch_grants, silent=True, source=f"{source}_overflow")
                        for eid, enum in exch_grants:
                            summary["overflow_converted"].append({"src_id": iid, "surplus": surplus, "target_id": eid, "target_num": enum})

            elif itype == ItemType.FURNITURE:
                if hasattr(db, "add_furniture"):
                    db.add_furniture(uid, iid, count)
                else:
                    db.execute("""
                        INSERT INTO backhome_furniture (uid, furniture_id, num, give_num, update_ts)
                        VALUES (?, ?, ?, 0, ?)
                        ON CONFLICT(uid, furniture_id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts
                    """, (uid, iid, count, now))
                db.execute("""
                    INSERT INTO material (uid, id, num, update_ts) VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts
                """, (uid, iid, count, now))
                summary["material"][iid] = summary["material"].get(iid, 0) + count
                if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                    ctx.touched_items.add(iid)

            elif itype == ItemType.FURNITURE_SUIT:
                if hasattr(db, "unlock_suit"):
                    db.unlock_suit(uid, iid)
                else:
                    db.execute("""
                        INSERT INTO backhome_suit (uid, suit_id, update_ts)
                        VALUES (?, ?, ?)
                        ON CONFLICT(uid, suit_id) DO UPDATE SET update_ts = excluded.update_ts
                    """, (uid, iid, now))
                # 同时入库 material 表供客户端背包与差量推送识别
                db.execute("""
                    INSERT INTO material (uid, id, num, update_ts) VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts
                """, (uid, iid, count, now))
                summary["material"][iid] = summary["material"].get(iid, 0) + count
                if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                    ctx.touched_items.add(iid)

            elif itype == ItemType.CANTEEN_INGREDIENTS:
                if hasattr(db, "add_canteen_ingredient"):
                    db.add_canteen_ingredient(uid, iid, count)
                else:
                    db.execute("""
                        INSERT INTO backhome_ingredient (uid, item_id, num, update_ts)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(uid, item_id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts
                    """, (uid, iid, count, now))
                db.execute("""
                    INSERT INTO material (uid, id, num, update_ts) VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts
                """, (uid, iid, count, now))
                summary["material"][iid] = summary["material"].get(iid, 0) + count
                if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                    ctx.touched_items.add(iid)

            elif itype == ItemType.COSMETIC:
                cat_rows = db.query("SELECT type FROM item_catalog WHERE id=?", (iid,))
                sub_t = int(cat_rows[0]["type"]) if cat_rows and cat_rows[0].get("type") else 13
                kind = COSMETIC_KIND_MAP.get(sub_t, "sticker")

                exch_info = _get_exchange_cfg().get(str(iid))
                # 装饰、名片、头像框、气泡等资产官方全局硬上限为 1
                limit = int(exch_info.get("limit") or 1) if exch_info else 1

                p_rows = db.query("SELECT obtained FROM player_card WHERE uid=? AND kind=? AND item_id=?", (uid, kind, iid))
                is_obtained = bool(p_rows and int(p_rows[0].get("obtained") or 0) == 1)
                m_rows = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
                cur_m = int(m_rows[0]["num"] or 0) if m_rows else 0
                cur_num = max(cur_m, 1 if is_obtained else 0)

                actual_add, surplus, exch_grants = cls._check_overflow_and_exchange(
                    ctx, db, uid, iid, count, cur_num, limit, exch_info, source=source
                )

                if actual_add > 0 or not is_obtained:
                    db.execute(
                        "INSERT INTO player_card (uid, kind, item_id, name, obtained) VALUES (?, ?, ?, '', 1) "
                        "ON CONFLICT(uid, kind, item_id) DO UPDATE SET obtained=1",
                        (uid, kind, iid)
                    )
                    new_mat_num = min(limit, cur_m + actual_add)
                    db.execute(
                        "INSERT INTO material (uid, id, num, update_ts) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(uid, id) DO UPDATE SET num = ?, update_ts = ?",
                        (uid, iid, new_mat_num, now, new_mat_num, now)
                    )
                    if sub_t == 20 or (90000 <= iid < 92000):
                        db.execute(
                            "INSERT OR IGNORE INTO chat_sticker (uid, emoji_id, name, desc) VALUES (?, ?, '', '')",
                            (uid, iid)
                        )
                    if actual_add > 0:
                        summary["material"][iid] = summary["material"].get(iid, 0) + actual_add
                    if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                        ctx.touched_items.add(iid)

                if surplus > 0:
                    logger.info(f"[InventoryService] 装饰资产 {iid} (kind={kind}) 触发限额 (拥有={cur_num}, 请求={count}), 溢出={surplus}")
                    if exch_grants:
                        cls.grant_items(ctx, uid, exch_grants, silent=True, source=f"{source}_overflow")
                        for eid, enum in exch_grants:
                            summary["overflow_converted"].append({"src_id": iid, "surplus": surplus, "target_id": eid, "target_num": enum})

        # 2. 批量处理装备类（一次性预分配自增 ID，杜绝循环查询 MAX(id) 冲突）
        if equip_items:
            new_equips, eq_events = cls._grant_equips_batch(ctx, db, uid, equip_items, now)
            summary["equips"].extend(new_equips)
            pending_events.extend(eq_events)

        # 3. 批量处理钥从类（一次性预分配自增 ID）
        if servant_items:
            new_servants, sv_events = cls._grant_servants_batch(ctx, db, uid, servant_items, now)
            summary["servants"].extend(new_servants)
            pending_events.extend(sv_events)

        # 4. 事务后置触发 EventBus 广播
        if not silent and pending_events:
            for ev_name, ev_kwargs in pending_events:
                try:
                    event_bus.bus.emit(ev_name, ctx, uid, **ev_kwargs)
                except Exception as e:
                    logger.error(f"[InventoryService] 触发事件 {ev_name} 异常: {e}")

        return summary

    @classmethod
    def _grant_single_hero(cls, ctx, db, uid, hero_id, now_ts):
        """单角色发放内部处理：未拥有则初始化，已拥有则折算碎片"""
        cfg = None
        try:
            cfg = db.query("SELECT unlock_star FROM hero_cfg WHERE hero_id=?", (hero_id,))
        except Exception:
            cfg = None
        if not cfg or not cfg[0].get("unlock_star"):
            try:
                cfg = db.query("SELECT unlock_star FROM draw_hero_pool WHERE hero_id=?", (hero_id,))
            except Exception:
                cfg = None
        star = int(cfg[0]["unlock_star"]) if cfg and cfg[0].get("unlock_star") else 300
        piece_add = 30 if star >= 300 else (18 if star == 200 else 10)

        exists = db.query("SELECT id FROM hero WHERE uid=? AND id=?", (uid, hero_id))
        if not exists:
            db.execute(
                "INSERT INTO hero (uid, id, level, star, exp, skill_list, unlock, update_ts) "
                "VALUES (?, ?, 1, ?, 0, '[]', 1, ?)",
                (uid, hero_id, star, now_ts),
            )
            return True, False, hero_id, 0
        else:
            prow = db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hero_id))
            cur = int(prow[0]["num"] or 0) if prow else 0
            new_cnt = cur + piece_add
            db.execute(
                "INSERT INTO hero_piece (uid, hero_id, num) VALUES (?, ?, ?) "
                "ON CONFLICT(uid, hero_id) DO UPDATE SET num=excluded.num",
                (uid, hero_id, new_cnt),
            )
            return False, True, hero_id, piece_add

    @classmethod
    def _grant_equips_batch(cls, ctx, db, uid, equip_items, now_ts):
        """批量发放刻印：连续自增 ID 分配与套装槽位解析"""
        r = db.query("SELECT COALESCE(MAX(id), 0) AS m FROM equip WHERE uid=?", (uid,))
        base_id = int(r[0]["m"] or 0) if r else 0

        eq_map = _get_equip_map()
        allocated_equips = []
        events_to_emit = []

        curr_id = base_id
        for iid, count in equip_items:
            eq_info = eq_map.get(str(iid))
            suit_id = eq_info["suit"] if eq_info else (iid // 100 if iid > 1000 else iid)
            pos = eq_info["pos"] if eq_info else ((iid // 10000) % 10)

            for _ in range(count):
                curr_id += 1
                db.execute(
                    "INSERT INTO equip (uid, id, prefab_id, exp, hero_id, is_lock, now_break_level, "
                    "enchant_slots, race, race_preview, race_hero, race_faction, update_ts, is_init) "
                    "VALUES (?, ?, ?, 0, 0, 0, 0, '[]', 0, 0, 0, 0, ?, 0)",
                    (uid, curr_id, iid, now_ts),
                )
                allocated_equips.append({"id": curr_id, "prefab_id": iid, "suit_id": suit_id, "pos": pos})

            events_to_emit.append(
                (
                    event_bus.Events.EQUIP_OBTAIN,
                    {"suit_id": suit_id, "pos": pos, "prefab_id": iid, "count": count},
                )
            )

        return allocated_equips, events_to_emit

    @classmethod
    def _grant_servants_batch(cls, ctx, db, uid, servant_items, now_ts):
        """批量发放钥从：连续自增 ID 分配"""
        r = db.query("SELECT COALESCE(MAX(id), 0) AS m FROM servant WHERE uid=?", (uid,))
        base_id = int(r[0]["m"] or 0) if r else 0

        allocated_servants = []
        events_to_emit = []

        curr_id = base_id
        for iid, count in servant_items:
            for _ in range(count):
                curr_id += 1
                db.execute(
                    "INSERT INTO servant (uid, id, prefab_id, stage, owned, is_locked, update_ts) "
                    "VALUES (?, ?, ?, 1, 1, 0, ?)",
                    (uid, curr_id, iid, now_ts),
                )
                allocated_servants.append({"id": curr_id, "prefab_id": iid})

            events_to_emit.append(
                (
                    event_bus.Events.SERVANT_OBTAIN,
                    {"servant_id": iid, "count": count},
                )
            )

        return allocated_servants, events_to_emit

    @classmethod
    def cost_item(cls, ctx_or_db, uid, item_id, count=1):
        """
        扣减道具/资产统一接口
        :return: True 扣减成功，False 余额不足或失败
        """
        iid = int(item_id or 0)
        count = int(count or 0)
        if count <= 0 or iid <= 0:
            return True

        db = getattr(ctx_or_db, "db", ctx_or_db)
        if db is None:
            return False

        t, have = cls.get_item_balance(db, uid, iid)
        if have < count:
            return False

        now = int(time.time())
        rem = have - count

        if t == ItemType.CURRENCY:
            if iid == 4:
                try:
                    import fatigue_service as _fs
                    _fs.on_fatigue_consume(db, uid, count, now_ts=now)
                except Exception:
                    db.execute(
                        "UPDATE currency SET num=?, update_ts=? WHERE uid=? AND id=?",
                        (rem, now, uid, iid),
                    )
            else:
                db.execute(
                    "UPDATE currency SET num=?, update_ts=? WHERE uid=? AND id=?",
                    (rem, now, uid, iid),
                )
            if hasattr(ctx_or_db, "touched_items") and ctx_or_db.touched_items is not None:
                ctx_or_db.touched_items.add(iid)
            return True

        elif t == ItemType.MATERIAL:
            db.execute(
                "UPDATE material SET num=?, update_ts=? WHERE uid=? AND id=?",
                (rem, now, uid, iid),
            )
            if hasattr(ctx_or_db, "touched_items") and ctx_or_db.touched_items is not None:
                ctx_or_db.touched_items.add(iid)
            return True

        elif t == ItemType.HERO_PIECE:
            hid = iid % 10000 if iid > 10000 else iid
            db.execute(
                "UPDATE hero_piece SET num=? WHERE uid=? AND hero_id=?",
                (rem, uid, hid),
            )
            piece_iid = iid if iid > 10000 else (10000 + hid)
            if hasattr(ctx_or_db, "touched_items") and ctx_or_db.touched_items is not None:
                ctx_or_db.touched_items.add(piece_iid)
            return True

        return False

    @classmethod
    def get_instance(cls, db=None):
        return cls

    @classmethod
    def deduct_item(cls, ctx_or_db, uid, item_id, count=1, db=None):
        ctx = ctx_or_db if hasattr(ctx_or_db, "db") else None
        target_db = db or getattr(ctx_or_db, "db", ctx_or_db)
        succ = cls.cost_item(ctx or target_db, uid, item_id, count=count)
        if not succ:
            from core import OperationError
            raise OperationError(3, f"道具/材料不足: id={item_id} 需{count}")
        return succ

    @classmethod
    def add_item(cls, ctx_or_db, uid, item_id, count=1, db=None):
        ctx = ctx_or_db if hasattr(ctx_or_db, "db") else None
        target_db = db or getattr(ctx_or_db, "db", ctx_or_db)
        return cls.grant_item(ctx or target_db, uid, item_id, count=count)

    @classmethod
    def use_item(cls, ctx, uid, item_id, count=1, use_list=None):
        """单道具使用统一入口"""
        item_req = {
            "item_info": {"id": item_id, "num": count, "time_valid": 0},
            "use_list": use_list or []
        }
        return cls.use_items(ctx, uid, [item_req])

    @classmethod
    def use_items(cls, ctx, uid, use_item_list):
        """
        统一道具/礼包使用接口（纳管礼包、自选箱、材料、体力药、皮肤券、表情激活等）
        :param ctx: 上下文 (含 db, touched_items, touched_heroes 等) 或直接 db
        :param uid: 玩家 UID
        :param use_item_list: 客户端 cs_17012 格式列表 [{"item_info": {...}, "use_list": [...]}, ...]
        :return: dict，包含 result (0=成功), drop_list (按 item_net_rec 规范生成的奖励列表), touched_heroes, touched_scene
        """
        if not use_item_list:
            return {"result": 0, "drop_list": [], "touched_heroes": [], "touched_scene": False}

        db = getattr(ctx, "db", ctx)
        if db is None:
            return {"result": 1, "drop_list": [], "touched_heroes": [], "touched_scene": False}

        usable_cfgs = _get_item_usable_cfg()
        drop_cfgs = _get_drop_cfg()

        # 1. 前置校验：若任一待消耗道具余额不足，返回错误码 3 (道具不足)
        for entry in use_item_list:
            info = entry.get("item_info") if isinstance(entry, dict) else getattr(entry, "item_info", None)
            if not info:
                continue
            iid = int(info.get("id") if isinstance(info, dict) else getattr(info, "id", 0))
            count = int(info.get("num") if isinstance(info, dict) else getattr(info, "num", 1))
            if count <= 0 or iid <= 0:
                continue
            _, have = cls.get_item_balance(db, uid, iid)
            if have < count:
                logger.warning(f"[InventoryService.use_items] 道具不足: uid={uid} id={iid} 拥有={have} 消耗={count}")
                return {"result": 3, "drop_list": [], "touched_heroes": [], "touched_scene": False}

        all_items_to_grant = []
        raw_drops = []
        touched_heroes = set()
        touched_scene = False

        # 2. 依次扣除消耗道具并按类型/配置展开产出
        for entry in use_item_list:
            info = entry.get("item_info") if isinstance(entry, dict) else getattr(entry, "item_info", None)
            if not info:
                continue
            iid = int(info.get("id") if isinstance(info, dict) else getattr(info, "id", 0))
            count = int(info.get("num") if isinstance(info, dict) else getattr(info, "num", 1))
            use_choices = entry.get("use_list") if isinstance(entry, dict) else getattr(entry, "use_list", [])
            use_choices = [int(x) for x in (use_choices or [])]

            if count <= 0 or iid <= 0:
                continue

            # 扣除道具
            succ = cls.cost_item(ctx, uid, iid, count=count)
            if not succ:
                return {"result": 3, "drop_list": [], "touched_heroes": list(touched_heroes), "touched_scene": touched_scene}

            cfg = usable_cfgs.get(str(iid))
            param = cfg.get("param") if cfg else None
            sub_type = int(cfg.get("sub_type") or 0) if cfg else 0
            itype = int(cfg.get("type") or 0) if cfg else 0

            # 特殊功能道具: 黑区信标 (41101)
            if iid == 41101:
                if hasattr(db, "reset_mythic_by_beacon"):
                    try:
                        db.reset_mythic_by_beacon(uid)
                    except Exception as e:
                        logger.error(f"重置黑区信标失败: {e}")
                continue

            # 分支 1: 自选箱 (use_choices 存在，或 sub_type 为 504, 508, 514, 515, 516, 517)
            if (use_choices or sub_type in (504, 508, 514, 515, 516, 517)) and param and isinstance(param, list):
                # 若客户端未上报选择索引，默认选取第 1 项避免越界穿透至全部发放
                choices = use_choices if use_choices else [1]
                for choice_idx in choices:
                    # 客户端协议选择索引为 1-based
                    if 1 <= choice_idx <= len(param):
                        chosen = param[choice_idx - 1]
                        if isinstance(chosen, (list, tuple)):
                            cid = int(chosen[0])
                            cnum = int(chosen[1]) * count
                        else:
                            cid = int(chosen)
                            cnum = 1 * count
                        all_items_to_grant.append((cid, cnum))
                        raw_drops.append({"id": cid, "num": cnum, "time_valid": 0})
                    else:
                        logger.warning(f"自选索引超界: iid={iid} choice_idx={choice_idx} total={len(param)}")

            # 分支 2: 掉落包 (sub_type == 507: 纳管 base 保底、random 独立概率、weight 权重轮盘)
            elif sub_type == 507 and param:
                drop_id = param[0] if isinstance(param, list) and param else param
                d_entry = drop_cfgs.get(str(drop_id))
                if d_entry:
                    base_list = d_entry.get("base") or []
                    rand_list = d_entry.get("random") or []
                    weight_list = d_entry.get("weight") or []
                    w_count = int(d_entry.get("weight_count") or (1 if weight_list else 0))

                    for _ in range(count):
                        # 1. 保底掉落
                        for b in base_list:
                            if len(b) >= 3 and b[2] < 100 and random.randint(1, 100) > b[2]:
                                continue
                            bid, bnum = int(b[0]), int(b[1])
                            all_items_to_grant.append((bid, bnum))
                            raw_drops.append({"id": bid, "num": bnum, "time_valid": 0})

                        # 2. 独立概率随机掉落
                        for r in rand_list:
                            if len(r) >= 3 and (r[2] >= 100 or random.randint(1, 100) <= r[2]):
                                rid, rnum = int(r[0]), int(r[1])
                                all_items_to_grant.append((rid, rnum))
                                raw_drops.append({"id": rid, "num": rnum, "time_valid": 0})
                            elif len(r) >= 2:
                                rid, rnum = int(r[0]), int(r[1])
                                all_items_to_grant.append((rid, rnum))
                                raw_drops.append({"id": rid, "num": rnum, "time_valid": 0})

                        # 3. 权重抽取掉落
                        if weight_list and w_count > 0:
                            valid_weights = [w for w in weight_list if len(w) >= 3]
                            total_w = sum(int(w[2]) for w in valid_weights)
                            if total_w > 0:
                                for _ in range(w_count):
                                    pick = random.randint(1, total_w)
                                    acc = 0
                                    for w in valid_weights:
                                        acc += int(w[2])
                                        if pick <= acc:
                                            wid, wnum = int(w[0]), int(w[1])
                                            all_items_to_grant.append((wid, wnum))
                                            raw_drops.append({"id": wid, "num": wnum, "time_valid": 0})
                                            break

            # 分支 3: 固定礼包 / 复合材料包 (sub_type in (501, 506, 518) 或 type == 5)
            elif (sub_type in (501, 506, 518) or itype == 5) and param and isinstance(param, list):
                if len(param) > 0 and isinstance(param[0], (list, tuple)):
                    for sub in param:
                        sid = int(sub[0])
                        snum = int(sub[1]) * count
                        all_items_to_grant.append((sid, snum))
                        raw_drops.append({"id": sid, "num": snum, "time_valid": 0})
                elif len(param) >= 2 and isinstance(param[0], int) and isinstance(param[1], int):
                    sid = int(param[0])
                    snum = int(param[1]) * count
                    all_items_to_grant.append((sid, snum))
                    raw_drops.append({"id": sid, "num": snum, "time_valid": 0})

            # 分支 4: 体力道具 / 货币好感道具 (sub_type in (401, 402, 403, 404))
            elif sub_type in (401, 402, 403, 404) and param:
                if isinstance(param, list) and len(param) > 0:
                    first = param[0]
                    if isinstance(first, (list, tuple)):
                        for entry_p in param:
                            cid = int(entry_p[0])
                            cnum = int(entry_p[1]) * count
                            all_items_to_grant.append((cid, cnum))
                            raw_drops.append({"id": cid, "num": cnum, "time_valid": 0})
                    elif len(param) >= 2 and isinstance(param[0], int) and isinstance(param[1], int):
                        cid = int(param[0])
                        cnum = int(param[1]) * count
                        all_items_to_grant.append((cid, cnum))
                        raw_drops.append({"id": cid, "num": cnum, "time_valid": 0})

            # 分支 5: 换装兑换券 / 限时体验卡 (type == 14 或 sub_type == 407)
            elif (itype == 14 or sub_type == 407) and param:
                skin_ids = param if isinstance(param, list) else [param]
                for sk_id in skin_ids:
                    sk_int = int(sk_id)
                    all_items_to_grant.append((sk_int, 1 * count))
                    raw_drops.append({"id": sk_int, "num": 1 * count, "time_valid": 0})

            # 分支 6: 动态表情/贴纸激活 (type == 20 或 90000 <= iid < 92000)
            elif itype == 20 or (90000 <= iid < 92000):
                stk_map = get_sticker_item_map()
                emoji_id = stk_map.get(iid) or (int(param[0]) if (param and isinstance(param, list)) else iid)
                if emoji_id:
                    db.execute(
                        "INSERT INTO chat_sticker (uid, emoji_id, name, desc) VALUES (?, ?, '', '') "
                        "ON CONFLICT(uid, emoji_id) DO NOTHING",
                        (uid, emoji_id)
                    )
                if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                    ctx.touched_items.add(iid)
                if hasattr(ctx, "touched_chat_stickers"):
                    ctx.touched_chat_stickers = True
                setattr(ctx, "touched_chat_stickers", True)

            # 分支 7: 场景解锁 (type == 21 或 6000 <= iid <= 6999)
            elif itype == 21 or (6000 <= iid <= 6999):
                all_items_to_grant.append((iid, count))
                raw_drops.append({"id": iid, "num": count, "time_valid": 0})
                touched_scene = True

            # 分支 8: 通用装饰/名片/气泡等
            elif itype in (11, 12, 13, 18, 22, 23, 25, 26, 28):
                all_items_to_grant.append((iid, count))
                raw_drops.append({"id": iid, "num": count, "time_valid": 0})

            else:
                # 兜底：若 param 具备 [[id, num]] 格式则作为产出
                if param and isinstance(param, list) and len(param) > 0 and isinstance(param[0], (list, tuple)):
                    for sub in param:
                        sid = int(sub[0])
                        snum = int(sub[1]) * count
                        all_items_to_grant.append((sid, snum))
                        raw_drops.append({"id": sid, "num": snum, "time_valid": 0})

        # 3. 统一入库入账
        grant_res = {}
        if all_items_to_grant:
            grant_res = cls.grant_items(ctx, uid, all_items_to_grant, source="use_item")
            if grant_res:
                if grant_res.get("hero_unlocked"):
                    touched_heroes.update(grant_res["hero_unlocked"])
                if grant_res.get("hero_converted"):
                    for conv in grant_res["hero_converted"]:
                        touched_heroes.add(conv["hero_id"])
                if grant_res.get("skins"):
                    for sk_id in grant_res["skins"]:
                        try:
                            rows = db.query("SELECT hero FROM skin WHERE id=?", (sk_id,))
                            if rows and rows[0]["hero"]:
                                touched_heroes.add(int(rows[0]["hero"]))
                        except Exception:
                            pass

        # 4. 合并 drop_list 相同道具并联动过量分解/碎片折算产物
        merged_drops = {}
        for d in raw_drops:
            did = d["id"]
            merged_drops[did] = merged_drops.get(did, 0) + d["num"]

        if grant_res:
            if grant_res.get("overflow_converted"):
                for conv in grant_res["overflow_converted"]:
                    src_id = conv["src_id"]
                    surplus = conv["surplus"]
                    target_id = conv["target_id"]
                    target_num = conv["target_num"]
                    if src_id in merged_drops:
                        merged_drops[src_id] -= surplus
                        if merged_drops[src_id] <= 0:
                            del merged_drops[src_id]
                    merged_drops[target_id] = merged_drops.get(target_id, 0) + target_num

            if grant_res.get("hero_converted"):
                for conv in grant_res["hero_converted"]:
                    hid = conv["hero_id"]
                    pieces = conv["pieces"]
                    piece_id = 10000 + hid
                    if hid in merged_drops:
                        del merged_drops[hid]
                    merged_drops[piece_id] = merged_drops.get(piece_id, 0) + pieces

        final_drop_list = [
            {"id": did, "num": dnum, "time_valid": 0}
            for did, dnum in merged_drops.items()
        ]

        return {
            "result": 0,
            "drop_list": final_drop_list,
            "touched_heroes": list(touched_heroes),
            "touched_scene": touched_scene
        }



# 顶层快捷函数封装
def grant_item(ctx, uid, item_id, count=1, silent=False, source=""):
    return InventoryService.grant_item(ctx, uid, item_id, count=count, silent=silent, source=source)


def grant_items(ctx, uid, item_list, silent=False, source=""):
    return InventoryService.grant_items(ctx, uid, item_list, silent=silent, source=source)


def cost_item(ctx_or_db, uid, item_id, count=1):
    return InventoryService.cost_item(ctx_or_db, uid, item_id, count=count)


def get_item_balance(ctx_or_db, uid, item_id):
    return InventoryService.get_item_balance(ctx_or_db, uid, item_id)


def classify_item(db, item_id):
    return InventoryService.classify_item(db, item_id)


def use_item(ctx, uid, item_id, count=1, use_list=None):
    return InventoryService.use_item(ctx, uid, item_id, count=count, use_list=use_list)


def use_items(ctx, uid, use_item_list):
    return InventoryService.use_items(ctx, uid, use_item_list)

