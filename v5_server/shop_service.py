# -*- coding: utf-8 -*-
"""
shop_service.py — V5 服务端统一商城与交易区领域服务 (Unified Shop Service)

核心职责：
1. 统一管理全类型商店（常规代币交易区、付费/补给、换装与场景商店）；
2. 每日采购（Shop 2）13 槽位合法商品池加权抽取、轮换与状态持久化；
3. 规范生成 sc_20007 与 sc_20009，清洗非法字段彻底消除 Codec 告警；
4. 购买全量接入 InventoryService 资产中心，广播 EventBus 驱动图鉴与任务；
5. 完整支持 cs_20014 主动刷新（20次上限、移转之辉阶梯扣除、货架重抽取）；
6. 深度订阅 LazyTimer 周期事件（每日/周一/每月 05:00 限购精细重置与在线即时推送）；
7. 预留面向控制面板与后续 Agent 的 GM 控制接口。
"""

import os
import json
import time
import random
import zlib
import re
import logging
from codec import encode, decode
from middleware import DownFrame
from event_bus import bus, Events
from inventory_service import InventoryService, ItemType
from core import OperationError
from lazy_timer import get_daily_5am_ts, get_weekly_mon_5am_ts, get_monthly_5am_ts
from periodic_gift_service import PeriodicGiftService

logger = logging.getLogger("shop_service")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POOL_PATH = os.path.join(BASE_DIR, "daily_shop_pool.json")

# 常规代币商店（交易区）：依赖客户端本地 ShopCfg，严禁向 sc_20007 下发以防客户端崩溃
NORMAL_SHOP_IDS = {2, 10, 11, 12, 13, 14, 20, 22, 23, 32, 41, 42, 99}

# 每日采购主动刷新移转之辉（钻石）阶梯消耗（GameCurrencyBuySetting.lua 权威定义）
# 前两次 10，3~4 次 20，5~6 次 30，7~8 次 40，9~20 次 50
REFRESH_DIAMOND_COSTS = [10, 10, 20, 20, 30, 30, 40, 40] + [50] * 12
DAILY_SHOP_REFRESH_LIMIT = 20

_ITEM_SKIN_MAP = {
    30124: 109503,  # 托特誓约换装「白夜之约」
    30098: 104903,  # 丝卡蒂「夏日阳光」
    30099: 104402,  # 提尔「极地探索」
    34096: 106604,  # 金乌换装
    34140: 104204,  # 操偶师换装
    34143: 103906,  # 换装
}


class ShopService:
    _instance = None
    _SHOP_BASE_TEMPLATE = None
    _SHOP_CFG_BASE_TEMPLATE = None
    _DAILY_POOL_CACHE = None
    _SKIN_HERO_MAP = None
    _TICKET_SKIN_MAP = None
    _RECHARGE_PACKS = None

    def __init__(self, db=None):
        self.db = db
        self._ensure_db_schema()

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    def _ensure_db_schema(self):
        """确保 user_daily_shop 表存在"""
        if self.db:
            try:
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS user_daily_shop (
                        uid INTEGER PRIMARY KEY,
                        refresh_times INTEGER DEFAULT 0,
                        goods_list_json TEXT DEFAULT '[]',
                        last_refresh_ts INTEGER DEFAULT 0,
                        update_ts INTEGER DEFAULT 0
                    );
                """)
            except Exception as e:
                logger.error(f"[ShopService] 初始化 user_daily_shop 表异常: {e}")

    # ==================== 1. 静态配置与基础模板缓存 ====================

    def _get_daily_shop_pool(self):
        """加载每日采购（Shop 2）13 槽位 3319 件商品候选池"""
        if ShopService._DAILY_POOL_CACHE is None:
            if os.path.exists(POOL_PATH):
                try:
                    with open(POOL_PATH, "r", encoding="utf-8") as f:
                        ShopService._DAILY_POOL_CACHE = json.load(f)
                except Exception as e:
                    logger.error(f"[ShopService] 加载 daily_shop_pool.json 失败: {e}")
                    ShopService._DAILY_POOL_CACHE = {}
            else:
                ShopService._DAILY_POOL_CACHE = {}
        return ShopService._DAILY_POOL_CACHE or {}

    def _get_shop_cfg_base_template(self):
        """从 login_push 加载 sc_20007 基准模板"""
        if ShopService._SHOP_CFG_BASE_TEMPLATE is None and self.db:
            try:
                rows = self.db.query("SELECT payload FROM login_push WHERE cmd=20007")
                if rows and rows[0]["payload"]:
                    raw = rows[0]["payload"]
                    if isinstance(raw, str):
                        raw = raw.encode("latin1", errors="ignore")
                    raw = bytes(raw)
                    decomp = zlib.decompress(raw) if raw.startswith(b"\x78\x9c") else raw
                    d = decode(decomp, "sc_20007")
                    ShopService._SHOP_CFG_BASE_TEMPLATE = d.get("shop_item_cfg_list") or []
            except Exception as e:
                logger.error(f"[ShopService] 加载 sc_20007 基准模板异常: {e}")
                ShopService._SHOP_CFG_BASE_TEMPLATE = []
        return ShopService._SHOP_CFG_BASE_TEMPLATE or []

    def _get_shop_base_template(self):
        """从 login_push 加载 sc_20009 基准模板"""
        if ShopService._SHOP_BASE_TEMPLATE is None and self.db:
            try:
                rows = self.db.query("SELECT payload FROM login_push WHERE cmd=20009")
                if rows and rows[0]["payload"]:
                    raw = rows[0]["payload"]
                    if isinstance(raw, str):
                        raw = raw.encode("latin1", errors="ignore")
                    raw = bytes(raw)
                    decomp = zlib.decompress(raw) if raw.startswith(b"\x78\x9c") else raw
                    d = decode(decomp, "sc_20009")
                    ShopService._SHOP_BASE_TEMPLATE = d.get("shop_item_list") or []
            except Exception as e:
                logger.error(f"[ShopService] 加载 sc_20009 基准模板异常: {e}")
                ShopService._SHOP_BASE_TEMPLATE = []
        return ShopService._SHOP_BASE_TEMPLATE or []

    def _get_skin_hero_map(self):
        if ShopService._SKIN_HERO_MAP is None:
            ShopService._SKIN_HERO_MAP = {}
            json_path = os.path.join(BASE_DIR, "skin_hero_map.json")
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        ShopService._SKIN_HERO_MAP = {int(k): int(v) for k, v in json.load(f).items()}
                except Exception:
                    pass
        return ShopService._SKIN_HERO_MAP

    def _get_ticket_skin_map(self):
        if ShopService._TICKET_SKIN_MAP is None:
            ShopService._TICKET_SKIN_MAP = {}
            json_path = os.path.join(BASE_DIR, "ticket_skin_map.json")
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        ShopService._TICKET_SKIN_MAP = {int(k): int(v) for k, v in json.load(f).items()}
                except Exception:
                    pass
        return ShopService._TICKET_SKIN_MAP

    def _get_recharge_packs(self):
        if ShopService._RECHARGE_PACKS is None:
            ShopService._RECHARGE_PACKS = {}
            json_path = os.path.join(BASE_DIR, "recharge_packs.json")
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        ShopService._RECHARGE_PACKS = json.load(f)
                except Exception:
                    pass
        return ShopService._RECHARGE_PACKS

    def _resolve_skin_id(self, rew_id):
        skin_map = self._get_skin_hero_map()
        if rew_id in skin_map:
            return rew_id
        return _ITEM_SKIN_MAP.get(rew_id)

    # ==================== 2. 每日采购（Shop 2）加权抽取与货架维护 ====================

    def roll_daily_shop_goods(self, uid, force_new=False):
        """
        按照官方 13 个槽位（1~9, 11~14）加权随机抽取每日采购货架商品
        :param uid: 玩家 UID
        :param force_new: 是否强制重新生成（跨天 05:00 或主动刷新）
        :return: 包含 13 个 goods_info 字典的列表
        """
        now_ts = int(time.time())
        cur_5am = get_daily_5am_ts(now_ts)
        tomorrow_5am = cur_5am + 86400

        pool = self._get_daily_shop_pool()
        if not pool:
            # 兜底：若池子不存在，返回官方基准 13 个商品
            return [
                {"goods_id": 2014007, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2013004, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2012002, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2011014, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2009006, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2008001, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2007004, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2006511, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2005791, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2004602, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2003465, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2002003, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
                {"goods_id": 2001005, "buy_times": 0, "next_refresh_timestamp": tomorrow_5am},
            ]

        # 检查数据库是否已有今日有效货架
        if not force_new and self.db:
            row = self.db.query("SELECT goods_list_json, last_refresh_ts FROM user_daily_shop WHERE uid=?", (uid,))
            if row and row[0]["goods_list_json"]:
                try:
                    saved_list = json.loads(row[0]["goods_list_json"])
                    if len(saved_list) == 13 and int(row[0]["last_refresh_ts"] or 0) >= cur_5am:
                        return saved_list
                except Exception:
                    pass

        # 重新为 13 个槽位各抽取 1 个合法商品
        rolled_goods = []
        valid_positions = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14]
        for pos in valid_positions:
            cands = pool.get(str(pos), [])
            if not cands:
                continue
            weights = [max(1, int(x.get("weight") or 100)) for x in cands]
            chosen = random.choices(cands, weights=weights, k=1)[0]
            rolled_goods.append({
                "goods_id": int(chosen["goods_id"]),
                "buy_times": 0,
                "next_refresh_timestamp": tomorrow_5am
            })

        # 倒序排列对齐官方基准惯例（14 -> 1）
        rolled_goods.sort(key=lambda x: x["goods_id"], reverse=True)

        if self.db:
            try:
                self.db.execute("""
                    INSERT INTO user_daily_shop (uid, goods_list_json, last_refresh_ts, update_ts)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid) DO UPDATE SET
                        goods_list_json = excluded.goods_list_json,
                        last_refresh_ts = excluded.last_refresh_ts,
                        update_ts = excluded.update_ts
                """, (uid, json.dumps(rolled_goods), now_ts, now_ts))
            except Exception as e:
                logger.error(f"[ShopService] 保存 user_daily_shop 失败: {e}")

        return rolled_goods

    def get_daily_shop_info(self, uid):
        """获取玩家当前每日采购的完整状态 (refresh_times, goods_list)"""
        now_ts = int(time.time())
        cur_5am = get_daily_5am_ts(now_ts)

        refresh_times = 0
        if self.db:
            r = self.db.query("SELECT refresh_times, last_refresh_ts FROM user_daily_shop WHERE uid=?", (uid,))
            if r:
                if int(r[0].get("last_refresh_ts") or 0) >= cur_5am:
                    refresh_times = int(r[0].get("refresh_times") or 0)
                else:
                    refresh_times = 0
                    self.db.execute("UPDATE user_daily_shop SET refresh_times=0 WHERE uid=?", (uid,))

        goods_list = self.roll_daily_shop_goods(uid, force_new=False)
        return refresh_times, goods_list

    # ==================== 3. 规范化协议下行数据组装 ====================

    def get_shop_cfg_payload(self):
        """
        动态生成全量商店配置数据（sc_20007）：
        1. 严格白名单隔离：仅包含付费/补给/皮肤等动态商店，普通代币商店（2, 10..41）绝不下发；
        2. 彻底清洗 GOODS_CFG 中非 Protobuf 规范的非法字段（give_id, taken_down, give），消除 Codec 警告！
        """
        base_list = self._get_shop_cfg_base_template()
        shop_cfg_map = {int(s["shop_id"]): s for s in base_list}
        dynamic_shop_ids = set(shop_cfg_map.keys())

        db_goods_by_shop = {}
        if self.db:
            db_goods = self.db.query(
                "SELECT goods_id, item_id, name, cost_type, cost_id, cost, cheap_cost, discount, "
                "limit_num, refresh_cycle, open_time, close_time, tag, detail_json, shop_id "
                "FROM shop_goods WHERE shop_id > 0"
            )
            for g in (db_goods or []):
                sid = int(g["shop_id"])
                if sid not in dynamic_shop_ids:
                    continue
                gid = int(g["goods_id"])
                dj_str = g.get("detail_json")
                dj = json.loads(dj_str) if dj_str else {}
                db_goods_by_shop.setdefault(sid, []).append({
                    "goods_id": gid,
                    "item_id": int(g["item_id"] or 0),
                    "name": g["name"] or "",
                    "cost_type": int(g["cost_type"] or 0),
                    "cost_id": int(g["cost_id"] or 0),
                    "cost": int(g["cost"] or 0),
                    "cheap_cost": int(g["cheap_cost"] or 0),
                    "discount": int(g["discount"] or 0),
                    "limit_num": int(g["limit_num"] if g["limit_num"] is not None else 1),
                    "refresh_cycle": int(g["refresh_cycle"] or 0),
                    "tag": int(g["tag"] or 0),
                    "detail": dj
                })

        out_shop_cfg_list = []

        for sid in sorted(dynamic_shop_ids):
            s = shop_cfg_map.get(sid, {"shop_id": sid, "goods_list": []})
            glist = s.get("goods_list", [])
            seen_gids = set()
            new_glist = []

            db_map = {g["goods_id"]: g for g in db_goods_by_shop.get(sid, [])}

            for g in glist:
                gid = int(g["goods_id"])
                # 严控货架：必须严格属于 shop_goods 数据库中激活的商品，已被清理删除的废弃/白屏条目坚决丢弃
                if gid not in db_map:
                    continue
                seen_gids.add(gid)
                dbg = db_map[gid]
                dj = dbg["detail"]
                g["cost_type"] = dbg["cost_type"]
                g["cost"] = dbg["cost"]
                g["cost_id"] = dbg["cost_id"]
                g["dlc"] = dj.get("dlc", 0)
                g["description"] = dj.get("description", g.get("description", 0))
                # 洁癖化清洗：删除非 GOODS_CFG 描述符的字段
                g.pop("give_id", None)
                g.pop("taken_down", None)
                g.pop("give", None)
                g["limit_display"] = 1
                g["open_time"] = {}
                g["close_time"] = {}
                new_glist.append(g)

            for gid, dbg in db_map.items():
                if gid not in seen_gids:
                    dj = dbg["detail"]
                    item_entry = {
                        "goods_id": gid,
                        "shop_sort": dj.get("shop_sort", 100),
                        "description": dj.get("description", 0),
                        "cost_type": dbg["cost_type"] or 0,
                        "cost_id": dbg["cost_id"] or 0,
                        "cost": dbg["cost"] or 0,
                        "cheap_cost_id": dj.get("cheap_cost_id", 0),
                        "cheap_cost": dbg["cheap_cost"] or 0,
                        "discount": dbg["discount"] or 0,
                        "limit_num": dbg["limit_num"],
                        "level_limit": [],
                        "limit_display": dj.get("limit_display", 1),
                        "refresh_cycle": dbg["refresh_cycle"] or 0,
                        "is_limit_time_discount": dj.get("is_limit_time_discount", 0),
                        "tag": dbg["tag"] or 0,
                        "dlc": dj.get("dlc", 0),
                        "cost_id_2": dj.get("cost_id_2", 0),
                        "cost_2": dj.get("cost_2", 0),
                        "cheap_cost_id_2": dj.get("cheap_cost_id_2", 0),
                        "cheap_cost_2": dj.get("cheap_cost_2", 0),
                        "is_limit_time_discount_2": 0,
                        "pre_goods_id": dj.get("pre_goods_id", []),
                        "open_time": dj.get("open_time", {}),
                        "close_time": dj.get("close_time", {}),
                    }
                    new_glist.append(item_entry)
                    seen_gids.add(gid)

            s["goods_list"] = new_glist
            out_shop_cfg_list.append(s)

        return {"shop_item_cfg_list": out_shop_cfg_list}

    def get_shop_data_payload(self, uid):
        """
        动态生成全量 116+ 商店购买状态与限购数据（sc_20009）：
        1. Shop 2（每日采购）：绑定当前已抽取的 13 槽位商品列表及已刷新次数；
        2. 普通代币商店（2, 10..41）：挂载 store_purchase 的实际已购次数与限购倒计时；
        3. 付费/补给/皮肤商店：动态叠加新增皮肤与商品。
        """
        now_ts = int(time.time())
        purchases = {}
        if self.db:
            for r in self.db.query("SELECT shop_id, goods_id, buy_times, next_refresh_timestamp FROM store_purchase WHERE uid=?", (uid,)):
                bt = int(r["buy_times"] or 0)
                nr = int(r["next_refresh_timestamp"] or 0)
                if nr > 0 and nr <= now_ts:
                    bt = 0
                    nr = 0
                purchases[(int(r["shop_id"]), int(r["goods_id"]))] = (bt, nr)

        base_list = self._get_shop_base_template()
        base_shop_map = {int(s["shop_id"]): s for s in base_list}

        db_goods_by_shop = {}
        if self.db:
            db_goods_rows = self.db.query("SELECT shop_id, goods_id FROM shop_goods WHERE shop_id > 0 ORDER BY shop_id, goods_id")
            for g in (db_goods_rows or []):
                db_goods_by_shop.setdefault(int(g["shop_id"]), []).append(int(g["goods_id"]))

        all_shop_ids = sorted(set(list(base_shop_map.keys()) + list(db_goods_by_shop.keys()) + [2]))
        out_shops = []

        daily_ref_times, daily_goods = self.get_daily_shop_info(uid)

        for sid in all_shop_ids:
            if sid == 2:
                glist = []
                for dg in daily_goods:
                    gid = dg["goods_id"]
                    bt, nr = purchases.get((2, gid), (0, dg.get("next_refresh_timestamp", 0)))
                    glist.append({
                        "goods_id": gid,
                        "buy_times": bt,
                        "next_refresh_timestamp": nr
                    })
                out_shops.append({
                    "shop_id": 2,
                    "refresh_times": daily_ref_times,
                    "goods_list": glist,
                    "is_need_tag": 0
                })
                continue

            s = base_shop_map.get(sid, {"shop_id": sid, "refresh_times": 0, "goods_list": [], "is_need_tag": 0})
            glist = []
            seen_gids = set()
            for g in s.get("goods_list", []):
                gid = int(g["goods_id"])
                if sid == 15 and gid >= 15010000:
                    continue
                seen_gids.add(gid)
                # 清洗抓包脏数据：未购商品统一默认为 0，只有玩家在 store_purchase 中有记录才计入已购
                bt, nr = purchases.get((sid, gid), (0, 0))
                glist.append({
                    "goods_id": gid,
                    "buy_times": bt,
                    "next_refresh_timestamp": nr
                })

            if sid not in NORMAL_SHOP_IDS:
                for gid in db_goods_by_shop.get(sid, []):
                    if sid == 15 and gid >= 15010000:
                        continue
                    if gid not in seen_gids:
                        bt, nr = purchases.get((sid, gid), (0, 0))
                        glist.append({
                            "goods_id": gid,
                            "buy_times": bt,
                            "next_refresh_timestamp": nr
                        })
                        seen_gids.add(gid)

            out_shops.append({
                "shop_id": sid,
                "refresh_times": int(s.get("refresh_times") or 0),
                "goods_list": glist,
                "is_need_tag": int(s.get("is_need_tag") or 0)
            })

        return {"shop_item_list": out_shops}

    def get_single_shop_payload(self, uid, shop_id):
        """获取单个商店的变动信息（sc_20005 changed_shop_info）"""
        sid = int(shop_id)
        now_ts = int(time.time())
        purchases = {}
        if self.db:
            for r in self.db.query("SELECT goods_id, buy_times, next_refresh_timestamp FROM store_purchase WHERE uid=? AND shop_id=?", (uid, sid)):
                bt = int(r["buy_times"] or 0)
                nr = int(r["next_refresh_timestamp"] or 0)
                if nr > 0 and nr <= now_ts:
                    bt = 0
                    nr = 0
                purchases[int(r["goods_id"])] = (bt, nr)

        if sid == 2:
            ref_times, daily_goods = self.get_daily_shop_info(uid)
            glist = []
            for dg in daily_goods:
                gid = dg["goods_id"]
                bt, nr = purchases.get(gid, (0, dg.get("next_refresh_timestamp", 0)))
                glist.append({
                    "goods_id": gid,
                    "buy_times": bt,
                    "next_refresh_timestamp": nr
                })
            return {"changed_shop_info": {
                "shop_id": 2,
                "refresh_times": ref_times,
                "goods_list": glist,
                "is_need_tag": 0
            }}

        base_list = self._get_shop_base_template()
        target_shop = None
        for s in base_list:
            if int(s["shop_id"]) == sid:
                target_shop = s
                break

        # 针对非常规商店（如充值/补给/皮肤等），严格校验与 sc_20007 保持一致的 shop_goods 白名单
        valid_db_gids = set()
        if sid not in NORMAL_SHOP_IDS and self.db:
            rows = self.db.query("SELECT goods_id FROM shop_goods WHERE shop_id=?", (sid,))
            valid_db_gids = {int(r["goods_id"]) for r in (rows or [])}

        goods_list = []
        seen_gids = set()
        if target_shop:
            for g in target_shop.get("goods_list", []):
                gid = int(g["goods_id"])
                if sid == 15 and gid >= 15010000:
                    continue
                # 严格过滤：严禁下发未在 shop_goods 白名单中激活的抓包残留商品（防止客户端无 ShopCfg 崩溃）
                if sid not in NORMAL_SHOP_IDS and valid_db_gids and gid not in valid_db_gids:
                    continue
                seen_gids.add(gid)
                # 清洗抓包脏数据：未购商品统一默认为 0，只有玩家在 store_purchase 中有记录才计入已购
                bt, nr = purchases.get(gid, (0, 0))
                goods_list.append({
                    "goods_id": gid,
                    "buy_times": bt,
                    "next_refresh_timestamp": nr
                })

        if sid not in NORMAL_SHOP_IDS and self.db:
            for g in self.db.query("SELECT goods_id FROM shop_goods WHERE shop_id=? ORDER BY goods_id", (sid,)):
                gid = int(g["goods_id"])
                if sid == 15 and gid >= 15010000:
                    continue
                if gid not in seen_gids:
                    bt, nr = purchases.get(gid, (0, 0))
                    goods_list.append({
                        "goods_id": gid,
                        "buy_times": bt,
                        "next_refresh_timestamp": nr
                    })
                    seen_gids.add(gid)

        return {"changed_shop_info": {
            "shop_id": sid,
            "refresh_times": int(target_shop.get("refresh_times") or 0) if target_shop else 0,
            "goods_list": goods_list,
            "is_need_tag": int(target_shop.get("is_need_tag") or 0) if target_shop else 0
        }}

    # ==================== 4. 购买核心业务引擎 (buy_goods) ====================

    @staticmethod
    def _get_balance(ctx, uid, item_id):
        try:
            _, bal = InventoryService.get_item_balance(ctx, uid, item_id)
            return int(bal or 0)
        except Exception:
            return 0

    def _cost_breakdown(self, ctx, uid, cost_id, total, cost_type=None):
        """精准分流扣费（优先移转之辉/移转之花）"""
        cid = int(cost_id or 0)
        total = int(total or 0)
        if total <= 0:
            return {}

        # 如果商品显式指定扣除移转之花 (ID 30, 31, 32)，精准扣减移转之花 (优先免费花32，再付费花31)
        if cid in (30, 31, 32):
            out = {}
            rem = total
            have32 = self._get_balance(ctx, uid, 32)
            have31 = self._get_balance(ctx, uid, 31)
            if have32 + have31 < rem:
                raise OperationError(12, f"移转之花不足 (拥有 {have32 + have31}, 需要 {total})")
            pay32 = min(have32, rem)
            if pay32 > 0:
                out[32] = pay32
                rem -= pay32
            if rem > 0:
                out[31] = rem
            return out

        # 如果扣费目标为移转之辉 (ID 1) 或 cost_type == 2 且未指定其他货币：优先扣移转之辉，不足以移转之花补齐
        if int(cost_type or 0) == 2 or cid == 1:
            have1 = self._get_balance(ctx, uid, 1)
            out = {}
            if have1 >= total:
                out[1] = total
            else:
                if have1 > 0:
                    out[1] = have1
                rem = total - max(0, have1)
                have32 = self._get_balance(ctx, uid, 32)
                have31 = self._get_balance(ctx, uid, 31)
                if have32 + have31 < rem:
                    raise OperationError(12, f"移转之辉与移转之花不足 (缺 {rem - have32 - have31})")
                pay32 = min(have32, rem)
                if pay32 > 0:
                    out[32] = pay32
                    rem -= pay32
                if rem > 0:
                    out[31] = rem
            return out

        have = self._get_balance(ctx, uid, cid)
        if have < total:
            raise OperationError(12, f"道具/货币 {cid} 不足 (拥有 {have}, 需要 {total})")
        return {cid: total}

    def buy_goods(self, ctx, uid, shop_id, buy_goods_list, buy_source=0):
        """
        商城物品购买统一引擎：
        1. 严格校验阶梯前置 (pre_goods_id)、限购与库存；
        2. 委托 InventoryService 扣减货币并原子入库发奖（含礼包自动解包、换装与场景自动识别）；
        3. 状态落库 store_purchase；
        4. 广播 Events.SHOP_BUY 驱动任务（条件600）及图鉴联动；
        5. 构建下行包列表（sc_20013, sc_14007, sc_32009 等）。
        """
        sid = int(shop_id or 0)
        if not buy_goods_list:
            raise OperationError(5, "购买列表为空")

        now = int(time.time())
        cur_5am = get_daily_5am_ts(now)
        tomorrow_5am = cur_5am + 86400

        packs = self._get_recharge_packs()
        skin_map = self._get_skin_hero_map()

        give_items = []
        cost_items = []
        touched_heroes = set()
        touched_scene = False

        for b in buy_goods_list:
            gid = int(b.get("buy_id") or b.get("goods_id") or 0)
            buy_num = int(b.get("buy_num") or 1)
            btype = int(b.get("buy_type") or 0)
            if buy_num <= 0:
                continue

            g = self._find_goods_cfg(gid, sid)
            if not g:
                raise OperationError(406, f"商品 {gid} 未配置或不在货架")

            dj = {}
            if isinstance(g.get("detail_json"), str):
                try:
                    dj = json.loads(g["detail_json"])
                except Exception:
                    pass
            elif isinstance(g.get("detail"), dict):
                dj = g["detail"]

            # 1. 校验前置阶梯商品 pre_goods_id
            raw_pre = dj.get("pre_goods_id")
            pre_ids = []
            if isinstance(raw_pre, list):
                pre_ids = [int(x) for x in raw_pre if str(x).isdigit()]
            elif isinstance(raw_pre, int) and raw_pre > 0:
                pre_ids = [raw_pre]
            elif isinstance(raw_pre, str):
                nums = re.findall(r'\d+', raw_pre)
                pre_ids = [int(x) for x in nums if int(x) > 0]

            for pre_id in pre_ids:
                pre_g = self._find_goods_cfg(pre_id, sid)
                if pre_g:
                    pre_limit = pre_g.get("limit_num")
                    if pre_limit is not None and pre_limit > 0:
                        pre_bought = self._get_bought(uid, pre_id)
                        if pre_bought < pre_limit:
                            raise OperationError(2, f"前置商品 {pre_id} 尚未售罄（已购 {pre_bought}/{pre_limit}），无法购买当前档位")

            # 2. 校验限购
            limit_num = int(g.get("limit_num") if g.get("limit_num") is not None else -1)
            refresh_cycle = int(g.get("refresh_cycle") or 0)
            bought = self._get_bought(uid, gid)
            if limit_num > 0 and (bought + buy_num > limit_num):
                raise OperationError(2, f"商品 {gid} 限购 {limit_num}（已购 {bought}）")

            # 直购换装商店（Shop 16）防重复购买与已拥有校验
            if sid == 16:
                item_target = int(g.get("item_id") or 0)
                give_target = int(g.get("give_id") or 0)
                desc_target = int(dj.get("description") or 0)
                voc_id = item_target or give_target or desc_target
                tmap = self._get_ticket_skin_map()
                sk_id = tmap.get(voc_id)
                if sk_id:
                    has_skin = False
                    if self.db:
                        has_skin = bool(self.db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, sk_id)))
                    has_voc = False
                    if self.db:
                        mat_row = self.db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, voc_id))
                        if mat_row and (mat_row[0].get("num") or 0) > 0:
                            has_voc = True
                    if has_skin:
                        raise OperationError(402, f"已拥有该换装，不可重复购买")
                    if has_voc:
                        raise OperationError(402, f"仓库已持有该换装兑换券，不可重复购买")

            # 3. 计算价格与扣款
            client_cost_items = b.get("cost_items") or []
            if client_cost_items:
                for c in client_cost_items:
                    cid = int(c.get("id") or 0)
                    cnum = int(c.get("num") or 0)
                    if cid and cnum > 0:
                        bal = self._get_balance(ctx, uid, cid)
                        if bal < cnum:
                            raise OperationError(12, f"货币/材料 {cid} 不足 (拥有 {bal}, 需要 {cnum})")
                        InventoryService.cost_item(ctx, uid, cid, cnum)
                        cost_items.append({"item_id": cid, "item_num": cnum})
            else:
                use_cheap = (
                    (btype == 1 and g.get("cheap_cost")) or
                    (int(g.get("cost") or 0) == 0 and int(g.get("cheap_cost") or 0) > 0) or
                    (int(g.get("discount") or 0) != 0 and int(g.get("cheap_cost") or 0) > 0)
                )
                if use_cheap:
                    cost_id = g.get("cheap_cost_id") or g.get("cost_id")
                    unit = g.get("cheap_cost")
                else:
                    cost_id = g.get("cost_id")
                    unit = g.get("cost")

                total_cost = int(unit or 0) * buy_num
                srv_cost = self._cost_breakdown(ctx, uid, cost_id, total_cost, g.get("cost_type"))
                for cid, cnum in srv_cost.items():
                    InventoryService.cost_item(ctx, uid, cid, cnum)
                    cost_items.append({"item_id": cid, "item_num": cnum})

            # 4. 获得道具解析并入库发奖
            give_unit = int(dj.get("give") or g.get("give_num") or 1)
            give_num = give_unit * buy_num
            item_target = int(g.get("item_id") or 0)
            desc_target = int(dj.get("description") or 0)
            give_target = int(dj.get("give_id") or g.get("give_id") or 0)

            pack_contents = (
                packs.get(str(item_target)) or
                (packs.get(str(desc_target)) if desc_target else None) or
                (packs.get(str(give_target)) if give_target else None)
            )

            target_scene = (
                item_target if (6000 <= item_target <= 6999) else
                (give_target if (6000 <= give_target <= 6999) else
                 (desc_target if (6000 <= desc_target <= 6999) else None))
            )
            target_skin = (
                self._resolve_skin_id(item_target) or
                self._resolve_skin_id(give_target) or
                self._resolve_skin_id(desc_target)
            )

            # 周期连续时间礼包 (sub_type 505 / 509) 独立领域服务解耦
            pg_svc = PeriodicGiftService.get_instance(self.db)
            periodic_cfg = (
                pg_svc.get_gift_cfg(desc_target) or
                pg_svc.get_gift_cfg(item_target) or
                pg_svc.get_gift_cfg(gid)
            )

            if periodic_cfg and int(periodic_cfg.get("sub_type") or 0) in (505, 509):
                # 【架构重构：废弃原有 recharge_packs.json 一次性全量直充 7/14 天材料的旧代码】
                # 旧逻辑注释存照：
                # pack_contents = packs.get(str(desc_target)) -> 7/14 天材料直接全量进背包
                # 新方案：仅发放 param[1] 立即赠品（若有），后续每日配额走系统邮件派发
                imme_items = periodic_cfg.get("imme_items") or []
                for rw_id, rw_cnt in imme_items:
                    total_give = int(rw_cnt) * buy_num
                    InventoryService.grant_items(ctx, uid, [(rw_id, total_give)], source="periodic_gift_imme")
                    give_items.append({"item_id": rw_id, "item_num": total_give})

                # 广播 PERIODIC_GIFT_BUY 事件，由 PeriodicGiftService 激活生命周期并立即派发首日邮件
                actual_desc_id = int(periodic_cfg.get("id") or desc_target or item_target or gid)
                bus.emit(
                    Events.PERIODIC_GIFT_BUY,
                    ctx,
                    uid,
                    goods_id=gid,
                    desc_id=actual_desc_id,
                    buy_num=buy_num
                )
            elif pack_contents:
                # 礼包自动解包（优先按组合礼包定义拆包发放）
                for rw_id, rw_cnt in pack_contents:
                    total_give = int(rw_cnt) * buy_num
                    target_sk = self._resolve_skin_id(rw_id)
                    if target_sk:
                        hid = skin_map.get(target_sk)
                        already_owned = False
                        if self.db:
                            existing = self.db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, target_sk))
                            already_owned = bool(existing)
                        if already_owned:
                            # 礼包内重复皮肤自动分解为 1000 移转之辉 (ID 1)
                            InventoryService.grant_items(ctx, uid, [(1, 1000 * total_give)], source="skin_duplicate_breakdown")
                            give_items.append({"item_id": 1, "item_num": 1000 * total_give})
                        else:
                            if self.db:
                                self.db.upsert("player_skin_unlocked", uid,
                                               {"skin_id": target_sk, "unlock_ts": now, "update_ts": now},
                                               keys=("uid", "skin_id"))
                            if hid:
                                touched_heroes.add(hid)
                            give_items.append({"item_id": target_sk, "item_num": total_give})
                    elif 6000 <= rw_id <= 6999:
                        already_owned = False
                        if self.db:
                            existing = self.db.query("SELECT 1 FROM user_scene WHERE uid=? AND scene_id=?", (uid, rw_id))
                            already_owned = bool(existing)
                        if already_owned:
                            # 礼包内重复场景自动分解为 500 移转之辉 (ID 1)
                            InventoryService.grant_items(ctx, uid, [(1, 500 * total_give)], source="scene_duplicate_breakdown")
                            give_items.append({"item_id": 1, "item_num": 500 * total_give})
                        else:
                            if self.db:
                                self.db.upsert("user_scene", uid,
                                               {"scene_id": rw_id, "lasted_time": 0, "obtain_time": now, "update_ts": now},
                                               keys=("uid", "scene_id"))
                            touched_scene = True
                            give_items.append({"item_id": rw_id, "item_num": total_give})
                    else:
                        InventoryService.grant_items(ctx, uid, [(rw_id, total_give)], source="shop_buy")
                        give_items.append({"item_id": rw_id, "item_num": total_give})
            elif target_scene:
                # 单独场景解锁与重复获取分解检测
                already_owned = False
                if self.db:
                    existing = self.db.query("SELECT 1 FROM user_scene WHERE uid=? AND scene_id=?", (uid, target_scene))
                    already_owned = bool(existing)
                if already_owned:
                    # 重复场景自动分解为 500 移转之辉 (ID 1)
                    InventoryService.grant_items(ctx, uid, [(1, 500 * give_num)], source="scene_duplicate_breakdown")
                    give_items.append({"item_id": 1, "item_num": 500 * give_num})
                else:
                    if self.db:
                        self.db.upsert("user_scene", uid,
                                       {"scene_id": target_scene, "lasted_time": 0, "obtain_time": now, "update_ts": now},
                                       keys=("uid", "scene_id"))
                    touched_scene = True
                    give_items.append({"item_id": target_scene, "item_num": give_num})
            elif target_skin:
                # 单独换装/皮肤解锁与重复获取分解检测
                hid = skin_map.get(target_skin)
                already_owned = False
                if self.db:
                    existing = self.db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, target_skin))
                    already_owned = bool(existing)
                if already_owned:
                    # 重复皮肤自动分解为 1000 移转之辉 (ID 1)
                    InventoryService.grant_items(ctx, uid, [(1, 1000 * give_num)], source="skin_duplicate_breakdown")
                    give_items.append({"item_id": 1, "item_num": 1000 * give_num})
                else:
                    if self.db:
                        self.db.upsert("player_skin_unlocked", uid,
                                       {"skin_id": target_skin, "unlock_ts": now, "update_ts": now},
                                       keys=("uid", "skin_id"))
                    if hid:
                        touched_heroes.add(hid)
                    give_items.append({"item_id": target_skin, "item_num": give_num})
            else:
                real_target = item_target or give_target or desc_target
                if real_target:
                    InventoryService.grant_items(ctx, uid, [(real_target, give_num)], source="shop_buy")
                    give_items.append({"item_id": real_target, "item_num": give_num})

            # 5. 更新限购记录 store_purchase
            if refresh_cycle == 4:
                nr_ts = tomorrow_5am
            elif refresh_cycle == 3:
                nr_ts = get_weekly_mon_5am_ts(now) + 604800
            elif refresh_cycle == 2:
                nr_ts = get_monthly_5am_ts(now) + 86400 * 31
            elif refresh_cycle > 0:
                nr_ts = now + refresh_cycle * 86400
            else:
                nr_ts = 0

            item_sid = int(g.get("shop_id") or sid or 0)
            if self.db:
                self.db.upsert(
                    "store_purchase",
                    uid,
                    {
                        "shop_id": item_sid,
                        "goods_id": gid,
                        "buy_times": bought + buy_num,
                        "next_refresh_timestamp": nr_ts,
                        "update_ts": now,
                    },
                    keys=("uid", "shop_id", "goods_id"),
                )

        # 6. 广播 Events.SHOP_BUY
        try:
            total_count = sum(int(x.get("buy_num") or 1) for x in buy_goods_list)
            bus.emit(Events.SHOP_BUY, ctx, uid, count=total_count, shop_id=sid)
        except Exception as e:
            logger.error(f"[ShopService] 广播 Events.SHOP_BUY 异常: {e}")

        # 7. 组装响应包
        p20013 = encode("sc_20013", {
            "result": 0,
            "give_items": give_items,
            "cost_items": cost_items
        }) or b"\x08\x00"

        frames = [DownFrame(20013, p20013)]

        # 追加 sc_20005 商店变动帧（仅常规代币货架商店需下发；充值礼包/补给商店严禁下发，以防客户端 SingleShopUpdate 遍历非 ShopCfg 道具崩溃）
        if sid in NORMAL_SHOP_IDS and sid != 3:
            try:
                p20005_data = self.get_single_shop_payload(uid, sid)
                if p20005_data:
                    p20005 = encode("sc_20005", p20005_data)
                    if p20005:
                        frames.append(DownFrame(20005, p20005))
            except Exception:
                pass

        # 追加 sc_30001 邮件未读红点摘要，若周期礼包下发邮件则即刻点亮邮箱红点
        try:
            import generator as _gen
            p30001 = _gen.gen_payload(30001, uid=uid, db=self.db)
            if p30001:
                frames.append(DownFrame(30001, p30001))
        except Exception:
            pass

        for hid in touched_heroes:
            try:
                import hero_codec as _hc
                hf = _hc.build_single_hero_frame(self.db, uid, hid)
                if hf:
                    frames.append(DownFrame(14007, hf))
            except Exception:
                pass

        if touched_scene:
            try:
                import generator as _gen
                p32009 = _gen.gen_payload(32009, uid=uid, db=self.db)
                if p32009:
                    frames.append(DownFrame(32009, p32009))
            except Exception:
                pass

        try:
            from operations import _refresh_frames
            frames.extend(_refresh_frames(ctx, uid))
        except Exception:
            pass

        if hasattr(ctx, "log"):
            ctx.log(f"cs_20012 -> 商店购买 {len(give_items)} 项 (已完成扣费、入库与事件广播)")

        return frames

    def _get_bought(self, uid, goods_id):
        if not self.db:
            return 0
        now_ts = int(time.time())
        rows = self.db.query("SELECT buy_times, next_refresh_timestamp FROM store_purchase WHERE uid=? AND goods_id=?", (uid, goods_id))
        if not rows:
            return 0
        nr = int(rows[0].get("next_refresh_timestamp") or 0)
        if nr > 0 and nr <= now_ts:
            try:
                self.db.execute("DELETE FROM store_purchase WHERE uid=? AND goods_id=?", (uid, goods_id))
            except Exception:
                pass
            return 0
        return int(rows[0]["buy_times"] or 0)

    def _find_goods_cfg(self, goods_id, shop_id):
        """寻找商品元数据（优先 shop_goods，其次 daily_shop_pool）"""
        gid = int(goods_id)
        if self.db:
            r = self.db.query("SELECT * FROM shop_goods WHERE goods_id=?", (gid,))
            if r:
                return r[0]

        pool = self._get_daily_shop_pool()
        for pos, cands in pool.items():
            for c in cands:
                if c.get("goods_id") == gid:
                    return c
        return None

    # ==================== 5. 主动刷新（cs_20014）与全同步（cs_20016） ====================

    def refresh_single_shop(self, ctx, uid, shop_id):
        """
        单商店主动刷新（cs_20014 → sc_20015 + sc_20005）：
        针对每日采购（Shop 2）：
        1. 检查 refresh_times < 20 次上限；
        2. 根据 REFRESH_DIAMOND_COSTS 计算移转之辉成本并扣除；
        3. refresh_times += 1；
        4. 重新加权抽取 13 槽位商品，重置 store_purchase 中新商品的已购状态；
        5. 返回 [sc_20015, sc_20005, sc_15009]。
        """
        sid = int(shop_id or 0)
        frames = []

        if sid == 2:
            now_ts = int(time.time())
            cur_5am = get_daily_5am_ts(now_ts)

            ref_times = 0
            if self.db:
                r = self.db.query("SELECT refresh_times, last_refresh_ts FROM user_daily_shop WHERE uid=?", (uid,))
                if r and int(r[0].get("last_refresh_ts") or 0) >= cur_5am:
                    ref_times = int(r[0].get("refresh_times") or 0)

            if ref_times >= DAILY_SHOP_REFRESH_LIMIT:
                raise OperationError(2, f"已达每日采购最大主动刷新次数 ({DAILY_SHOP_REFRESH_LIMIT} 次)")

            cost_idx = min(ref_times, len(REFRESH_DIAMOND_COSTS) - 1)
            diamond_cost = REFRESH_DIAMOND_COSTS[cost_idx]

            bal = self._get_balance(ctx, uid, 1)
            if bal < diamond_cost:
                raise OperationError(12, f"移转之辉不足 (需要 {diamond_cost}, 拥有 {bal})")

            InventoryService.cost_item(ctx, uid, 1, diamond_cost)

            new_ref_times = ref_times + 1
            new_goods = self.roll_daily_shop_goods(uid, force_new=True)

            if self.db:
                self.db.execute("""
                    UPDATE user_daily_shop
                    SET refresh_times=?, goods_list_json=?, last_refresh_ts=?, update_ts=?
                    WHERE uid=?
                """, (new_ref_times, json.dumps(new_goods), now_ts, now_ts, uid))

                self.db.execute("DELETE FROM store_purchase WHERE uid=? AND shop_id=2", (uid,))

            # 1. 先下发单店货架数据更新 sc_20005（客户端 ShopData 先行同步最新 13 项货架）
            shop_data = self.get_single_shop_payload(uid, 2)
            p20005 = encode("sc_20005", shop_data)
            if p20005:
                frames.append(DownFrame(20005, p20005))

            # 2. 仅针对性下发货币更新 sc_15009（更新移转之辉，彻底剔除无关的角色碎片与资料冗余帧）
            try:
                import generator as _gen
                p15009 = _gen.gen_payload(15009, uid=uid, db=self.db)
                if p15009:
                    frames.append(DownFrame(15009, p15009))
            except Exception:
                pass

            # 3. 最后下发 sc_20015 关闭 Loading 遮罩，驱动界面丝滑呈现，彻底消除卡顿与闪烁
            p20015 = encode("sc_20015", {"result": 0}) or b"\x08\x00"
            frames.append(DownFrame(20015, p20015))

            if hasattr(ctx, "log"):
                ctx.log(f"cs_20014 -> 每日采购主动刷新成功 (第 {new_ref_times}/20 次, 消耗 {diamond_cost} 移转之辉)")

            return frames

        p20015 = encode("sc_20015", {"result": 0}) or b"\x08\x00"
        frames.append(DownFrame(20015, p20015))
        if self.db:
            shop_data = self.get_single_shop_payload(uid, sid)
            p20005 = encode("sc_20005", shop_data)
            if p20005:
                frames.append(DownFrame(20005, p20005))
        return frames

    def refresh_all_shops(self, ctx, uid):
        """全商店时间同步（cs_20016 → sc_20017）"""
        now_ts = int(time.time())
        p20017 = encode("sc_20017", {"result": 0, "timestamp": now_ts}) or b"\x08\x00"
        return [DownFrame(20017, p20017)]

    # ==================== 6. 全局定时器 (LazyTimer) 周期重置订阅 ====================

    def on_daily_reset_5am(self, ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
        """
        每日 05:00 跨天周期重置：
        1. 仅定向清理 refresh_cycle == 4 (每日限购) 的已购记录（严控边界：严禁误清同商店内的月度/永久限购商品）；
        2. 重置每日采购 (Shop 2) refresh_times = 0，自动加权轮换 13 槽位新商品；
        3. 重置 Shop 2 的 store_purchase 记录；
        4. 若在线，顺风车推送 sc_20005 告知客户端货架已更新（涵盖 Shop 2 与 Shop 4 日常补给单品）。
        """
        if not uid or not self.db:
            return
        if now_ts is None:
            now_ts = int(time.time())

        # 1. 每日采购（Shop 2）全量货架重抽并清空
        self.roll_daily_shop_goods(uid, force_new=True)
        self.db.execute("UPDATE user_daily_shop SET refresh_times=0, last_refresh_ts=?, update_ts=? WHERE uid=?",
                        (now_ts, now_ts, uid))
        self.db.execute("DELETE FROM store_purchase WHERE uid=? AND shop_id=2", (uid,))

        # 2. 仅精确定向删除 refresh_cycle == 4 的日限购商品（如日常补给 Shop 4 中的每日应急冷却包 4034501）
        # 严禁误伤同商店内的永久限购 (refresh_cycle=1) 或月限购 (refresh_cycle=2) 礼包！
        try:
            self.db.execute("""
                DELETE FROM store_purchase
                WHERE uid=? AND goods_id IN (
                    SELECT goods_id FROM shop_goods WHERE refresh_cycle = 4 AND shop_id != 2
                )
            """, (uid,))
        except Exception:
            pass

        # 3. 在线即时推送更新帧
        if hasattr(ctx, "append_frame") or hasattr(ctx, "pending_frames"):
            # (1) 推送每日采购（Shop 2）全新货架
            try:
                shop2_data = self.get_single_shop_payload(uid, 2)
                p20005_2 = encode("sc_20005", shop2_data)
                if p20005_2:
                    df2 = DownFrame(20005, p20005_2)
                    if hasattr(ctx, "append_frame"):
                        ctx.append_frame(df2)
                    else:
                        ctx.pending_frames.append(df2)
            except Exception:
                pass

            # (2) 推送日常补给（Shop 4）单品重置：仅 4034501 变回 0/1 可购买，其余商品状态丝毫不动
            try:
                shop4_data = self.get_single_shop_payload(uid, 4)
                p20005_4 = encode("sc_20005", shop4_data)
                if p20005_4:
                    df4 = DownFrame(20005, p20005_4)
                    if hasattr(ctx, "append_frame"):
                        ctx.append_frame(df4)
                    else:
                        ctx.pending_frames.append(df4)
            except Exception:
                pass

        if hasattr(ctx, "log"):
            ctx.log(f"[ShopService] 用户 {uid} 触发每日 05:00 商城重置（每日采购货架/次数已重置，日常补给每日应急冷却包已重置）")

    def on_weekly_reset_mon_5am(self, ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
        """每周一 05:00 跨周周期重置：清理 refresh_cycle == 3 (每周限购) 的已购记录"""
        if not uid or not self.db:
            return
        try:
            self.db.execute("""
                DELETE FROM store_purchase
                WHERE uid=? AND goods_id IN (
                    SELECT goods_id FROM shop_goods WHERE refresh_cycle = 3
                )
            """, (uid,))
        except Exception:
            pass
        if hasattr(ctx, "log"):
            ctx.log(f"[ShopService] 用户 {uid} 触发每周一 05:00 商城重置（周限购已清空）")

    def on_monthly_reset_5am(self, ctx, uid, cycle_start_ts=0, now_ts=None, **kwargs):
        """每月 1 日 05:00 跨月周期重置：清理 refresh_cycle == 2 (每月限购) 的已购记录"""
        if not uid or not self.db:
            return
        try:
            self.db.execute("""
                DELETE FROM store_purchase
                WHERE uid=? AND goods_id IN (
                    SELECT goods_id FROM shop_goods WHERE refresh_cycle = 2
                )
            """, (uid,))
        except Exception:
            pass
        if hasattr(ctx, "log"):
            ctx.log(f"[ShopService] 用户 {uid} 触发每月 1 日 05:00 商城重置（月限购已清空）")

    def on_time_tick(self, ctx, uid, delta_seconds=0, now_ts=None, **kwargs):
        """物理连续时间流逝：清理到期的倒计时限时购买记录"""
        if not uid or not self.db:
            return
        if now_ts is None:
            now_ts = int(time.time())
        try:
            self.db.execute(
                "DELETE FROM store_purchase WHERE uid=? AND next_refresh_timestamp > 0 AND next_refresh_timestamp <= ?",
                (uid, now_ts)
            )
        except Exception:
            pass

    # ==================== 7. GM / 控制面板扩展接口 ====================

    def gm_reset_shop_refresh(self, uid, shop_id=2):
        """GM 接口：重置单商店刷新次数为 0"""
        if not self.db:
            return False, "数据库未连接"
        if shop_id == 2:
            self.db.execute("UPDATE user_daily_shop SET refresh_times=0 WHERE uid=?", (uid,))
            return True, f"玩家 {uid} 每日采购刷新次数已归零"
        return True, "操作成功"

    def gm_custom_set_goods(self, uid, shop_id, goods_id_list):
        """GM 接口：自定义指定上架商品"""
        if not self.db:
            return False, "数据库未连接"
        if shop_id == 2:
            if not goods_id_list or len(goods_id_list) != 13:
                return False, "每日采购货架必须恰好指定 13 个商品 ID"
            now_ts = int(time.time())
            tomorrow_5am = get_daily_5am_ts(now_ts) + 86400
            new_goods = [{"goods_id": int(x), "buy_times": 0, "next_refresh_timestamp": tomorrow_5am} for x in goods_id_list]
            self.db.execute("""
                INSERT INTO user_daily_shop (uid, goods_list_json, last_refresh_ts, update_ts)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    goods_list_json = excluded.goods_list_json,
                    update_ts = excluded.update_ts
            """, (uid, json.dumps(new_goods), now_ts, now_ts))
            return True, f"玩家 {uid} 每日采购货架已自定义为指定的 13 项商品"
        return False, "暂不支持该商店的自定义货架"

    def gm_trigger_cycle_refresh(self, uid, cycle_type="daily"):
        """GM 接口：强制立即触发周期刷新"""
        now_ts = int(time.time())
        class DummyCtx:
            def __init__(self, db):
                self.db = db
            def log(self, msg):
                pass
        ctx = DummyCtx(self.db)
        if cycle_type == "daily":
            self.on_daily_reset_5am(ctx, uid, now_ts=now_ts)
            return True, "已强制触发每日 05:00 商城重置"
        elif cycle_type == "weekly":
            self.on_weekly_reset_mon_5am(ctx, uid, now_ts=now_ts)
            return True, "已强制触发每周一 05:00 商城重置"
        elif cycle_type == "monthly":
            self.on_monthly_reset_5am(ctx, uid, now_ts=now_ts)
            return True, "已强制触发每月 1 日 05:00 商城重置"
        return False, f"未知周期类型: {cycle_type}"

    # ==================== 8. 同调档案回销 (Module Item Resolve, cs_14118) ====================

    def resolve_module_items(self, ctx, uid, item_list):
        """
        同调商店（Shop 14）专属：同调档案回销（cs_14118 → sc_14119）
        规则（GameSetting.weapon_module_break_return）：
        1. 产物为 41601（行动记录）；
        2. 兑换比例 1:4（每销毁 1 份同调档案，返还 4 份行动记录）；
        3. 扣减玩家背包中对应的角色同调档案（81xxx）；
        4. 发放对应数量的行动记录（41601）；
        5. 返回 sc_14119 + 包含 sc_17009 的全量差分刷新帧。
        """
        if not item_list:
            raise OperationError(5, "回销材料列表为空")

        total_gain_records = 0
        deduct_records = []

        # 1. 预校验存量
        for it in item_list:
            item_id = int(it.get("id") or 0)
            item_num = int(it.get("num") or 0)
            if item_id <= 0 or item_num <= 0:
                continue
            bal = self._get_balance(ctx, uid, item_id)
            if bal < item_num:
                raise OperationError(12, f"同调档案 {item_id} 数量不足 (拥有 {bal}, 需要 {item_num})")
            deduct_records.append((item_id, item_num))
            total_gain_records += item_num * 4

        if not deduct_records:
            raise OperationError(5, "有效回销材料为空")

        # 2. 执行扣减
        for item_id, item_num in deduct_records:
            InventoryService.cost_item(ctx, uid, item_id, item_num)

        # 3. 发放行动记录（41601）
        InventoryService.grant_items(ctx, uid, [(41601, total_gain_records)], source="module_resolve")

        # 4. 构建返回帧
        p14119 = encode("sc_14119", {"result": 0}) or b"\x08\x00"
        frames = [DownFrame(14119, p14119)]

        try:
            from operations import _refresh_frames
            frames.extend(_refresh_frames(ctx, uid))
        except Exception:
            pass

        if hasattr(ctx, "log"):
            ctx.log(f"cs_14118 -> 同调档案回销成功: 销毁 {len(deduct_records)} 种档案，获得行动记录×{total_gain_records}")

        return frames

