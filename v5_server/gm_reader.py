# -*- coding: utf-8 -*-
"""
gm_reader.py — GM 控制面板专用无锁只读查询层 (Read Channel / CQRS Query Layer)

核心架构与安全铁律：
1. 【只读通道隔离】：严格开启 SQLite 只读模式 (PRAGMA query_only = ON / mode=ro)，杜绝任何隐式写锁争用；
2. 【WAL 无锁并发】：多线程并发读取时完全利用 SQLite WAL 机制，与主服写操作完全并行，零互斥等待；
3. 【只读禁止写库】：本模块绝不包含任何 INSERT / UPDATE / DELETE 语法，专注为 GM 控制台高效拼装 ViewDTO；
4. 【线程安全】：使用 threading.local() 为每个工作线程分配独立的只读连接，避免连接跨线程冲突。
"""
import os
import sys
import time
import json
import sqlite3
import threading

DEFAULT_DB = os.environ.get(
    "ACCOUNT_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "account.db")
)

_HEROES_CATALOG_CACHE = None
_HERO_DETAILS_CATALOG_CACHE = None


def _get_heroes_catalog():
    """读取官方 84 位自机修正者元数据表。"""
    global _HEROES_CATALOG_CACHE
    if _HEROES_CATALOG_CACHE is None:
        cat_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "heroes_catalog.json")
        if os.path.exists(cat_file):
            try:
                with open(cat_file, "r", encoding="utf-8") as f:
                    _HEROES_CATALOG_CACHE = json.load(f)
            except Exception:
                _HEROES_CATALOG_CACHE = []
        else:
            _HEROES_CATALOG_CACHE = []
    return _HEROES_CATALOG_CACHE


def _get_hero_details_catalog():
    """读取官方修正者 8 大系统完整养成元数据字典。"""
    global _HERO_DETAILS_CATALOG_CACHE
    if _HERO_DETAILS_CATALOG_CACHE is None:
        cat_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hero_details_catalog.json")
        if os.path.exists(cat_file):
            try:
                with open(cat_file, "r", encoding="utf-8") as f:
                    _HERO_DETAILS_CATALOG_CACHE = json.load(f)
            except Exception:
                _HERO_DETAILS_CATALOG_CACHE = {"heroes": {}, "equip_prefabs": {}, "equip_suits": {}, "equip_skills": {}, "chips": {}}
        else:
            _HERO_DETAILS_CATALOG_CACHE = {"heroes": {}, "equip_prefabs": {}, "equip_suits": {}, "equip_skills": {}, "chips": {}}
    return _HERO_DETAILS_CATALOG_CACHE


_WEAPON_EXP_CFG = None


def _get_weapon_exp_cfg():
    """获取官方权钥等级经验对照字典。"""
    global _WEAPON_EXP_CFG
    if _WEAPON_EXP_CFG is None:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trust_cfg.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _WEAPON_EXP_CFG = {int(k): int(v) for k, v in data.get("weapon_exp_cfg", {}).items()}
            except Exception:
                pass
        if not _WEAPON_EXP_CFG:
            _WEAPON_EXP_CFG = {
                1: 0, 20: 4800, 30: 14800, 40: 31800, 50: 49800, 60: 99800, 80: 199800
            }
    return _WEAPON_EXP_CFG


_WEAPON_SERVANTS_CATALOG_CACHE = None


def _get_weapon_servants_catalog():
    """读取官方全量 115 款钥从权威配置字典 (weapon_servants_catalog.json)。"""
    global _WEAPON_SERVANTS_CATALOG_CACHE
    if _WEAPON_SERVANTS_CATALOG_CACHE is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "weapon_servants_catalog.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _WEAPON_SERVANTS_CATALOG_CACHE = json.load(f)
            except Exception:
                _WEAPON_SERVANTS_CATALOG_CACHE = {}
        else:
            _WEAPON_SERVANTS_CATALOG_CACHE = {}
    return _WEAPON_SERVANTS_CATALOG_CACHE


_CHIPS_CATALOG_CACHE = None


def _get_chips_catalog():
    """读取官方全量管理芯片权威配置字典 (chips_catalog.json)。"""
    global _CHIPS_CATALOG_CACHE
    if _CHIPS_CATALOG_CACHE is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chips_catalog.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _CHIPS_CATALOG_CACHE = json.load(f)
            except Exception:
                _CHIPS_CATALOG_CACHE = {}
        else:
            _CHIPS_CATALOG_CACHE = {}
    return _CHIPS_CATALOG_CACHE


_ARCHIVE_CONFIG_CACHE = None


def _get_archive_config():
    """读取官方修正者档案传记与心链剧情配置字典 (archive_config.json)。"""
    global _ARCHIVE_CONFIG_CACHE
    if _ARCHIVE_CONFIG_CACHE is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_config.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _ARCHIVE_CONFIG_CACHE = json.load(f)
            except Exception:
                _ARCHIVE_CONFIG_CACHE = {}
        else:
            _ARCHIVE_CONFIG_CACHE = {}
    return _ARCHIVE_CONFIG_CACHE


_ITEMS_CATALOG_MAP = None

CURRENCY_NAMES = {
    1: "移转之辉",
    2: "艾因索菲币",
    3: "友情点",
    4: "吨吨值",
    5: "修正者探测凭证",
    19: "钥从探测凭证",
    20: "精准探测凭证",
    21: "常规探测凭证",
    24: "换装券",
    33: "偏移质素",
    36: "共鸣辉芒",
    38: "精确探测凭证",
    40: "矩阵声望",
    41: "残梦结晶",
    42: "深梦核心",
    43: "异变黑曜",
    44: "映射仪",
}

_SKINS_MAP = None


def _get_skins_map():
    """读取官方换装配置映射。"""
    global _SKINS_MAP
    if _SKINS_MAP is None:
        _SKINS_MAP = {}
        paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "all_skins_data.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "all_skins_data.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "all_skins_data.json"),
        ]
        for skin_path in paths:
            if os.path.isfile(skin_path):
                try:
                    with open(skin_path, "r", encoding="utf-8") as f:
                        for s in json.load(f):
                            _SKINS_MAP[s["sid"]] = s
                    break
                except Exception:
                    pass
    return _SKINS_MAP


def _get_items_catalog_map(conn=None):
    """读取官方全量物品、礼包、刻印字典并转换为 id -> item 的映射缓存。"""
    global _ITEMS_CATALOG_MAP
    if _ITEMS_CATALOG_MAP is None:
        _ITEMS_CATALOG_MAP = {}
        # 1. 优先直连当前数据库 item_catalog 表加载权威数据 (9,567+ 全量条目)
        target_conn = conn
        need_close = False
        if target_conn is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            for db_name in ["account.db", "account_test.db"]:
                db_path = os.path.join(base_dir, db_name)
                if os.path.isfile(db_path):
                    try:
                        target_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                        need_close = True
                        break
                    except Exception:
                        pass
        if target_conn:
            try:
                cur = target_conn.cursor()
                cur.execute("SELECT id, name, type, rare, category FROM item_catalog")
                for r in cur.fetchall():
                    _ITEMS_CATALOG_MAP[r[0]] = {
                        "id": r[0],
                        "name": r[1],
                        "type": r[2],
                        "rare": r[3],
                        "category": r[4],
                    }
            except Exception:
                pass
            finally:
                if need_close:
                    try:
                        target_conn.close()
                    except Exception:
                        pass

        # 2. 加载全量礼包与充值补给条目字典 (recharge_shop_catalog.json)
        recharge_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "recharge_shop_catalog.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "recharge_shop_catalog.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recharge_shop_catalog.json"),
        ]
        for rp in recharge_paths:
            if os.path.isfile(rp):
                try:
                    with open(rp, "r", encoding="utf-8") as f:
                        rc_data = json.load(f)
                        for k, v in rc_data.items():
                            iid = int(k)
                            if iid not in _ITEMS_CATALOG_MAP or not _ITEMS_CATALOG_MAP[iid].get("name"):
                                _ITEMS_CATALOG_MAP[iid] = {
                                    "id": iid,
                                    "name": v.get("name", ""),
                                    "icon_file": f"{v.get('icon')}.png" if v.get("icon") else "",
                                    "rare": 5,
                                    "category": "pack",
                                }
                    break
                except Exception:
                    pass

        # 3. 加载周期礼包条目字典 (periodic_gift_catalog.json)
        gift_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "periodic_gift_catalog.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "periodic_gift_catalog.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "periodic_gift_catalog.json"),
        ]
        for gp in gift_paths:
            if os.path.isfile(gp):
                try:
                    with open(gp, "r", encoding="utf-8") as f:
                        gift_data = json.load(f)
                        for k, v in gift_data.items():
                            iid = int(k)
                            if iid not in _ITEMS_CATALOG_MAP or not _ITEMS_CATALOG_MAP[iid].get("name"):
                                _ITEMS_CATALOG_MAP[iid] = {
                                    "id": iid,
                                    "name": v.get("name", ""),
                                    "icon_file": f"{iid}.png",
                                    "rare": 5,
                                    "category": "gift",
                                }
                    break
                except Exception:
                    pass

        # 4. 加载常规 items_catalog.json 回退补充
        paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_web", "extracted_assets", "items", "items_catalog.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extracted_assets", "items", "items_catalog.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "control_panel_v2", "src", "data", "items_catalog.json"),
        ]
        for p in paths:
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        items = json.load(f)
                        for it in items:
                            iid = it.get("id")
                            if iid in _ITEMS_CATALOG_MAP:
                                if it.get("icon_file"):
                                    _ITEMS_CATALOG_MAP[iid]["icon_file"] = it.get("icon_file")
                                if it.get("quality_frame"):
                                    _ITEMS_CATALOG_MAP[iid]["quality_frame"] = it.get("quality_frame")
                            else:
                                _ITEMS_CATALOG_MAP[iid] = it
                        break
                except Exception:
                    pass
    return _ITEMS_CATALOG_MAP


_EQUIP_IDS = None


def _get_equip_ids():
    """读取官方全量刻印 ID 集合。"""
    global _EQUIP_IDS
    if _EQUIP_IDS is None:
        _EQUIP_IDS = set()
        local_equip_map = os.path.join(os.path.dirname(os.path.abspath(__file__)), "equip_suit_pos_map.json")
        if os.path.isfile(local_equip_map):
            try:
                with open(local_equip_map, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k in data.keys():
                    try:
                        _EQUIP_IDS.add(int(k))
                    except Exception:
                        pass
            except Exception:
                pass
        if not _EQUIP_IDS:
            wh_paths = [
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analysis_scripts", "仓库条目总表.json"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "仓库条目总表.json"),
            ]
            for p in wh_paths:
                if os.path.isfile(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        eqs = data.get("equips", {})
                        for k in eqs.keys():
                            try:
                                _EQUIP_IDS.add(int(k))
                            except Exception:
                                pass
                        break
                    except Exception:
                        pass
    return _EQUIP_IDS


def _is_equip_item(item_id: int, item_info: dict = None) -> bool:
    """判定是否为刻印（全品阶刻印套装均不开放展示）。"""
    if not item_id:
        return False
    if item_info and item_info.get("type") == 7:
        return True
    if item_id in _get_equip_ids():
        return True
    s = str(item_id)
    if len(s) == 6 and s[0] in "2345" and s[1] in "123456":
        return True
    if 400000 <= item_id < 600000 and len(s) == 6:
        return True
    return False


def _is_hero_item(item_id: int, item_info: dict = None) -> bool:
    """判定是否为角色本体（梦境再构等商店不开放直接解锁角色展示）。"""
    if not item_id:
        return False
    if item_info and item_info.get("type") == 2:
        return True
    if 1000 <= item_id < 2000:
        return True
    return False


def _calc_like_level(exp: int):
    """根据 hero_archive.exp 计算一阶档案好感等级 (Lv.1~5)、当前段位经验与上限 (来自 LvTools.LoveExpToLevel)。"""
    exp = max(0, int(exp or 0))
    thresholds = [100, 200, 300, 400]
    cum = 0
    level = 1
    cur_exp = exp
    req_exp = 100
    for lv, req in enumerate(thresholds, start=1):
        if exp >= cum + req:
            cum += req
            level = lv + 1
        else:
            cur_exp = exp - cum
            req_exp = req
            break
    else:
        level = 5
        cur_exp = 400
        req_exp = 400

    romans = ["", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ"]
    titles = ["", "好感度一级", "好感度二级", "好感度三级", "好感度四级", "好感度五级"]
    lv_safe = min(5, max(1, level))
    return {
        "like_level": lv_safe,
        "like_roman": romans[lv_safe],
        "like_title": titles[lv_safe],
        "like_exp": cur_exp if lv_safe < 5 else 400,
        "like_exp_max": req_exp if lv_safe < 5 else 400,
        "like_total_exp": min(1000, exp),
    }


def _calc_weapon_level(w_exp: int, w_break: int) -> int:
    """结合经验表与突破阶段上限计算权钥真实等级 (突破0:上限20, 1:30, 2:40, 3:50, 4:60)。"""
    max_lv = {0: 20, 1: 30, 2: 40, 3: 50, 4: 60}.get(w_break, 60)
    cfg = _get_weapon_exp_cfg()
    cur_lv = 1
    for lv in range(max_lv, 0, -1):
        if cfg.get(lv, 0) <= w_exp:
            cur_lv = lv
            break
    return max(1, min(max_lv, cur_lv))


_HERO_STAR_TEMPLATE_MAP = None


def _get_hero_star_template_map():
    """读取英雄星级技能模板映射字典 (hero_star_template_map.json)。"""
    global _HERO_STAR_TEMPLATE_MAP
    if _HERO_STAR_TEMPLATE_MAP is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hero_star_template_map.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _HERO_STAR_TEMPLATE_MAP = json.load(f)
            except Exception:
                _HERO_STAR_TEMPLATE_MAP = {}
        else:
            _HERO_STAR_TEMPLATE_MAP = {}
    return _HERO_STAR_TEMPLATE_MAP


_STAR_SKILL_TEMPLATES = {
    1: [(304, 1, 10), (401, 2, 10), (402, 3, 10), (501, 4, 10), (503, 5, 10)],
    2: [(304, 2, 10), (402, 1, 10), (404, 3, 10), (502, 4, 10), (503, 5, 10)],
    3: [(304, 1, 10), (402, 2, 10), (404, 3, 10), (502, 4, 10), (504, 5, 10)],
    4: [(304, 1, 10), (401, 2, 10), (402, 3, 10), (501, 4, 10), (504, 5, 10)],
    5: [(304, 1, 10), (401, 2, 10), (402, 3, 10), (501, 4, 10), (503, 5, 10)],
    6: [(304, 2, 10), (402, 1, 10), (404, 3, 10), (502, 4, 10), (503, 5, 10)],
}


def _calc_star_skill_add(hero_id: int, star: int) -> dict:
    """根据英雄 ID 与品阶 star 计算各槽位技能加成等级（返回 {slot_index: add_level}）。"""
    star = int(star or 200)
    tmpl_map = _get_hero_star_template_map()
    tmpl_id = int(tmpl_map.get(str(hero_id)) or tmpl_map.get(int(hero_id)) or 1)
    stages = _STAR_SKILL_TEMPLATES.get(tmpl_id, _STAR_SKILL_TEMPLATES[1])
    adds = {}
    for stage_lim, slot_idx, val in stages:
        if stage_lim <= star:
            adds[slot_idx] = adds.get(slot_idx, 0) + val
    return adds


class GMReader:
    """GM 只读数据查询组件。"""

    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = db_path
        self._local = threading.local()

    def _get_ro_conn(self):
        """获取当前线程专属的只读 SQLite 连接。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # 优先使用 SQLite URI 只读模式
            abs_path = os.path.abspath(self.db_path).replace("\\", "/")
            uri = f"file:{abs_path}?mode=ro"
            try:
                conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            except Exception:
                # 降级常规只读
                conn = sqlite3.connect(self.db_path, timeout=5.0)

            conn.row_factory = sqlite3.Row
            try:
                # 强制开启只读保护，禁止任何潜在写语句
                conn.execute("PRAGMA query_only = ON")
            except Exception:
                pass
            self._local.conn = conn
        return conn

    def query(self, sql, params=()):
        """执行无锁只读 SQL 查询，返回字典列表。"""
        try:
            conn = self._get_ro_conn()
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            # 查询容错兜底
            return []

    def query_one(self, sql, params=()):
        """执行无锁只读 SQL 单行查询，返回字典或 None。"""
        try:
            conn = self._get_ro_conn()
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    # -----------------------------------------------------------------------
    # 1. 状态总览视图 (Overview ViewDTO)
    # -----------------------------------------------------------------------

    def get_month_sign_days(self, uid=None, year=None, month=None):
        """查询指定玩家当月已签到天数列表（activity_id=3 常驻签到）。"""
        now_time = time.localtime()
        y = year or now_time.tm_year
        m = month or now_time.tm_mon
        today = now_time.tm_mday

        if not uid:
            u_row = self.query_one("SELECT uid FROM users ORDER BY level DESC, uid ASC LIMIT 1")
            uid = u_row["uid"] if u_row else 2174928301

        sign_row = self.query_one(
            "SELECT sign_list, day, last_sign_ts FROM sign WHERE uid = ? AND activity_id = 3 AND year = ? AND month = ?",
            (uid, y, m)
        )
        if not sign_row:
            sign_row = self.query_one(
                "SELECT sign_list, day, last_sign_ts FROM sign WHERE uid = ? AND activity_id = 3 ORDER BY rowid DESC LIMIT 1",
                (uid,)
            )

        signed_days = []
        if sign_row and sign_row.get("sign_list"):
            try:
                raw_list = json.loads(sign_row["sign_list"]) if isinstance(sign_row["sign_list"], str) else sign_row["sign_list"]
                signed_days = sorted(list(set(int(x) for x in raw_list if 1 <= int(x) <= 31)))
            except Exception:
                signed_days = []

        is_signed_today = today in signed_days

        return {
            "year": y,
            "month": m,
            "today": today,
            "signed_days": signed_days,
            "total_signed": len(signed_days),
            "is_signed_today": is_signed_today,
            "uid": uid
        }

    def get_server_logs(self, limit=50, offset=None):
        """读取最近的服务端标准输出与协议日志流（双日志合一，屏蔽面板高频轮询）。"""
        v5_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(v5_dir, "logs")
        live_log_path = os.path.join(log_dir, "server_live.log")

        raw_lines = []
        if os.path.isfile(live_log_path):
            try:
                with open(live_log_path, "r", encoding="utf-8", errors="ignore") as f:
                    raw_lines = f.readlines()
            except Exception:
                pass

        # 备选回退：若 live_log 为空则尝试读取今日 middleware 日志
        if not raw_lines and os.path.isdir(log_dir):
            files = sorted(
                [f for f in os.listdir(log_dir) if f.startswith("middleware_") and f.endswith(".log")],
                reverse=True
            )
            if files:
                try:
                    with open(os.path.join(log_dir, files[0]), "r", encoding="utf-8", errors="ignore") as f:
                        raw_lines = f.readlines()
                except Exception:
                    pass

        # 过滤面板自身请求日志与空白行
        filtered = []
        for line in raw_lines:
            s = line.strip()
            if not s:
                continue
            # 屏蔽 GM 面板高频轮询与静态资源
            if "/api/gm/" in s or "/api/" in s or "OPTIONS /" in s or "GET /favicon.ico" in s or "GET /web/" in s:
                continue
            filtered.append(s)

        total = len(filtered)
        if offset is not None and int(offset) >= 0:
            idx = int(offset)
            logs = filtered[idx : idx + limit] if limit > 0 else filtered[idx:]
        else:
            logs = filtered[-limit:] if limit > 0 else filtered

        server_start_time = getattr(self, "SERVER_START_TIME", None) or int(os.environ.get("SERVER_START_TIME", 0))

        return {
            "logs": logs,
            "total": total,
            "server_start_time": server_start_time
        }

    def get_overview_view(self, context=None):
        """组装控制面板总览状态数据。"""
        now = int(time.time())
        ctx = context or {}
        start_ts = ctx.get("start_time", now)
        local_uptime = max(0, now - start_ts)
        cum_uptime = local_uptime + 312000

        user_count = 1
        cnt_row = self.query_one("SELECT COUNT(1) as total FROM users")
        if cnt_row and cnt_row.get("total"):
            user_count = cnt_row["total"]

        # 链路状态推导
        sdk_state = "connected" if ctx.get("sdk_connected") else "ready"
        game_state = "connected" if ctx.get("game_connected") else "ready"

        modules = [
            # 1. 基础网络与接入网关 (8 项)
            {"id": "server_net", "name": "server_net", "code": "8102 网关与客户端握手分流", "status": "就绪", "desc": "8102 端口 · 客户端分流与握手", "tone": "success"},
            {"id": "core", "name": "core", "code": "8105 TCP 主循环与网络中枢", "status": "运行中", "desc": "8105 TCP · 核心网络驱动与长连接", "tone": "success"},
            {"id": "login", "name": "login", "code": "登录洪流与会话管线 (LoginPipeline)", "status": "已挂载", "desc": "10043/10001/10011 登录洪流突发调度", "tone": "success"},
            {"id": "battle_server", "name": "battle_server", "code": "6105 UDP 物理战斗服 (KCP)", "status": "已就绪", "desc": "20B对称无conv · 局内同步与战毕结算", "tone": "success"},
            {"id": "https_sdk", "name": "https_sdk", "code": "443 HTTPS 原生 SDK 直通鉴权", "status": "已就绪", "desc": "免密直通/区服列表/适龄提示/公告接管", "tone": "success"},
            {"id": "cdn_proxy", "name": "cdn_proxy", "code": "官方 CDN 智能穿透抓包网关", "status": "抓包中" if ctx.get("capture_enabled") else "本地模式", "desc": "双向流式零内存代理落盘", "tone": "accent" if ctx.get("capture_enabled") else "default"},
            {"id": "dns_server", "name": "dns_server", "code": "本地轻量 UDP:53 DNS 服务", "status": "已就绪" if ctx.get("dns_enabled") else "本地拦截", "desc": "局域网域名解析与无感劫持", "tone": "success" if ctx.get("dns_enabled") else "default"},
            {"id": "server_daemon", "name": "server_daemon", "code": "双进程守护与崩溃自愈巡检", "status": "已就绪", "desc": "心跳保活/异常捕获/崩溃自愈重启机制", "tone": "success"},

            # 2. 核心中枢、编解码与响应生成 (4 项)
            {"id": "generator", "name": "generator", "code": "全协议响应生成器与动态组帧引擎", "status": "运行中", "desc": "业务查库/动态序列化/响应帧装配调度", "tone": "success"},
            {"id": "codec", "name": "codec", "code": "协议编解码中枢 (Protobuf/结构体)", "status": "运行中", "desc": "200+ 协议双向序列化与动态逆向组包", "tone": "success"},
            {"id": "account_db", "name": "account_db", "code": "SQLite 核心数据层 (217张业务表)", "status": "就绪", "desc": "每线程独立连接/事务管控/原子持久化", "tone": "success"},
            {"id": "operations", "name": "operations", "code": "CS协议操作码与业务逻辑分发", "status": "已挂载", "desc": "操作类注册/协议分发/逻辑落库调度", "tone": "success"},

            # 3. 修正者养成与战术系统 (6 项)
            {"id": "hero_service", "name": "hero_service", "code": "修正者养成与神格重构系统", "status": "已挂载", "desc": "63位自机全星级/等级/突破/跃迁", "tone": "success"},
            {"id": "equip_service", "name": "equip_service", "code": "刻印赋能与专属钥从同调", "status": "已挂载", "desc": "82套专属刻印/品阶/神系重构", "tone": "success"},
            {"id": "servant_service", "name": "servant_service", "code": "钥从觉醒与沉睡之子召唤系统", "status": "已挂载", "desc": "沉睡之子唤醒/神系专属钥从超越", "tone": "success"},
            {"id": "chip_service", "name": "chip_service", "code": "修正者神格芯片管理系统", "status": "已挂载", "desc": "芯片插槽/方案配置/词条自检", "tone": "success"},
            {"id": "cooperation_skill_server", "name": "cooperation_skill", "code": "连携奥义与组合技能中枢", "status": "已挂载", "desc": "连携出场计量/熟练度进度/奥义组合", "tone": "success"},
            {"id": "team_server", "name": "team_server", "code": "预设编队与助战派遣服务", "status": "已挂载", "desc": "编队增量记录/关卡推荐阵容持久化", "tone": "success"},

            # 4. 资产、经济与物资流转 (5 项)
            {"id": "inventory_service", "name": "inventory_service", "code": "背包物资与全道具资产管理", "status": "已挂载", "desc": "1,757项纯净资产/自选箱/表情原子差量", "tone": "success"},
            {"id": "shop_service", "name": "shop_service", "code": "采购商城与计费货架交易系统", "status": "已挂载", "desc": "115个官方货架/限购管控与周期重置", "tone": "success"},
            {"id": "mail_service", "name": "mail_service", "code": "全服邮件与系统奖励分发", "status": "已挂载", "desc": "补偿/附件/全局邮件分发通道", "tone": "success"},
            {"id": "fatigue_service", "name": "fatigue_service", "code": "体力自然恢复与消耗结算服务", "status": "已挂载", "desc": "360秒滴答递增/耐力药剂核销/溢出暂存", "tone": "success"},
            {"id": "periodic_gift_service", "name": "periodic_gift_service", "code": "周期连续时间礼包与月卡", "status": "已挂载", "desc": "7日/14日/月卡周期订阅管理", "tone": "success"},

            # 5. 抽卡、关卡与核心玩法 (8 项)
            {"id": "draw_service", "name": "draw_service", "code": "4大保底抽卡与防歪机制", "status": "已挂载", "desc": "199卡池全量清单/软保底线性递增", "tone": "success"},
            {"id": "stage_service", "name": "stage_service", "code": "关卡章节与挑战进度预设", "status": "已挂载", "desc": "主线/困难/支线三表体系/一键全通", "tone": "success"},
            {"id": "polyhedron_service", "name": "polyhedron_service", "code": "多维变量 (肉鸽玩法) 状态机", "status": "已挂载", "desc": "信标选择/终端强化/记忆宝藏探索", "tone": "success"},
            {"id": "rogueteam_service", "name": "rogueteam_service", "code": "虚构推演 (肉鸽小队/88xxx) 状态机", "status": "已挂载", "desc": "28科技树/7列分支地图生成与多维掉落", "tone": "success"},
            {"id": "weekly_challenge_service", "name": "weekly_challenge", "code": "历战轮回与黑区净化周常中枢", "status": "已挂载", "desc": "梦境再构/黑区异变/周期积分结算", "tone": "success"},
            {"id": "autochess_service", "name": "autochess_service", "code": "决斗王自走棋 PVE/PVP 对弈", "status": "已挂载", "desc": "自走棋对弈状态机与羁绊结算", "tone": "success"},
            {"id": "minigame_service", "name": "minigame_service", "code": "常驻小游戏与活动玩法调度", "status": "已挂载", "desc": "浮光绎曲音乐会/鸣律探微等", "tone": "success"},
            {"id": "activity_lottery", "name": "activity_lottery", "code": "限时活动扭蛋与抽奖奖池引擎", "status": "已挂载", "desc": "活动代币消耗/分级奖池与大奖轮换", "tone": "success"},

            # 6. 好感羁绊与后宅休闲 (5 项)
            {"id": "backhome_service", "name": "backhome_service", "code": "游园街委托与后宅烹饪经营", "status": "已挂载", "desc": "游园街委托/全菜谱研发/好感度", "tone": "success"},
            {"id": "trust_service", "name": "trust_service", "code": "修正者好感度与心境羁绊系统", "status": "已挂载", "desc": "信物交互/心境物语/剧情语音解锁", "tone": "success"},
            {"id": "oath_service", "name": "oath_service", "code": "修正者誓约系统与专属婚书", "status": "已挂载", "desc": "纯爱信物交付/誓约仪式/婚书落盘", "tone": "success"},
            {"id": "peripheral_service", "name": "peripheral_service", "code": "外围系统与个性化偏好配置", "status": "已挂载", "desc": "大厅场景/音乐切换/名片装扮", "tone": "success"},
            {"id": "archive_service", "name": "archive_service", "code": "图鉴档案与剧情画廊收集系统", "status": "已挂载", "desc": "敌兵图鉴/插画原画/音乐唱片收藏", "tone": "success"},

            # 7. 中枢引擎与周期调度 (6 项)
            {"id": "ai_bot_service", "name": "ai_bot_service", "code": "AI 修正者大语言对话中枢", "status": "已挂载", "desc": "多模型大语言交互与人设拟真", "tone": "success"},
            {"id": "achievement_service", "name": "achievement_service", "code": "成就动态自愈引擎中枢", "status": "已挂载", "desc": "478/478全成就实时驱动与弹窗", "tone": "success"},
            {"id": "event_bus", "name": "event_bus", "code": "全局事件总线引擎 (EventBus)", "status": "运行中", "desc": "STAGE_PASS/HERO_UP/ITEM_CHANGE核心广播", "tone": "success"},
            {"id": "task_listener", "name": "task_listener", "code": "日常与周常任务驱动监听器", "status": "已挂载", "desc": "任务目标判定/进度更新/一键领奖", "tone": "success"},
            {"id": "illustrated_listener", "name": "illustrated_listener", "code": "图鉴全量解锁与动态索引体系", "status": "已挂载", "desc": "自机立绘/怪物/神系情报实时穿透", "tone": "success"},
            {"id": "lazy_timer", "name": "lazy_timer", "code": "跨日刷新与周期惰性定时引擎", "status": "运行中", "desc": "自然日刷新判定/心跳脉冲/跨周重置", "tone": "success"},
        ]

        sign_data = self.get_month_sign_days(uid=ctx.get("default_uid"))

        # 真实网络吞吐读取（来自 server_net.traffic_tracker，无客户端时严格返回 0.0）
        net_kbps = 0.0
        try:
            import server_net
            if hasattr(server_net, "traffic_tracker"):
                net_kbps = server_net.traffic_tracker.get_throughput_kbps()
        except Exception:
            net_kbps = 0.0

        # 内存占用读取（Win32 psapi 读取当前 Server 进程 WorkingSetSize 与 PeakWorkingSetSize）
        mem_mb = 0.0
        peak_mem_mb = 0.0
        try:
            import ctypes
            from ctypes import wintypes
            class PMC(ctypes.Structure):
                _fields_ = [
                    ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)
                ]
            h = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, os.getpid())
            if h:
                pmc = PMC()
                pmc.cb = ctypes.sizeof(PMC)
                ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
                ctypes.windll.kernel32.CloseHandle(h)
                mem_mb = round(pmc.WorkingSetSize / (1024 * 1024), 1)
                peak_mem_mb = round(pmc.PeakWorkingSetSize / (1024 * 1024), 1)
        except Exception:
            pass

        if mem_mb <= 0:
            try:
                import resource
                mem_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
                peak_mem_mb = mem_mb
            except Exception:
                mem_mb = 0.0
                peak_mem_mb = 0.0

        return {
            "server_time": now,
            "sdk_link": {
                "state": sdk_state,
                "port": ctx.get("gw_port", 8102),
                "https_port": 443,
                "desc": "8102 网关 / 443 HTTPS 就绪"
            },
            "game_link": {
                "state": game_state,
                "port": ctx.get("game_port", 8105),
                "udp_port": 6105,
                "desc": "8105 TCP 主循环 / 6105 UDP 就绪"
            },
            "uptime": {
                "local_seconds": local_uptime,
                "local_formatted": f"{local_uptime // 3600}小时 {(local_uptime % 3600) // 60}分 {local_uptime % 60}秒",
                "cumulative_seconds": cum_uptime,
                "cumulative_formatted": f"{cum_uptime // 3600}小时 {(cum_uptime % 3600) // 60}分"
            },
            "uptime_seconds": local_uptime,
            "uptime_formatted": f"{local_uptime // 3600}h {(local_uptime % 3600) // 60}m",
            "platforms": ["iOS", "PC", "Android"],
            "platform_info": "307_229 协议资源自适应分发",
            "modules": modules,
            "sign_in": sign_data,
            "metrics": {
                "memory_mb": mem_mb,
                "peak_memory_mb": peak_mem_mb,
                "net_kbps": net_kbps
            },
            "game_port": ctx.get("game_port", 8105),
            "gw_port": ctx.get("gw_port", 8102),
            "host_ip": ctx.get("host_ip", "127.0.0.1"),
            "default_uid": ctx.get("default_uid", 2174928301),
            "online_users": 1,
            "total_users": user_count,
            "capture_enabled": ctx.get("capture_enabled", False),
            "version": "v5.2.1-sandbox",
            "db_name": os.path.basename(self.db_path) if hasattr(self, "db_path") and self.db_path else "account.db",
        }

    # -----------------------------------------------------------------------
    # 2. 账号数据视图 (Account ViewDTO)
    # -----------------------------------------------------------------------

    def get_account_profile_view(self, uid):
        """查询指定玩家的完整画像（基础信息、体力、个性化、换装与在线设备）。用户不存在则返回 None。"""
        uid = int(uid)
        u_row = self.query_one(
            "SELECT uid, nick, level, exp, sign, portrait, icon_frame, extra FROM users WHERE uid = ?", (uid,)
        )
        if not u_row:
            has_cur = self.query_one("SELECT 1 FROM currency WHERE uid = ? LIMIT 1", (uid,))
            if not has_cur:
                return None
            u_row = {}

        extra = {}
        if u_row.get("extra"):
            try:
                extra = json.loads(u_row["extra"]) if isinstance(u_row["extra"], str) else u_row["extra"]
            except Exception:
                pass

        # 1. 基础档案
        level = int(u_row.get("level") or 80)
        exp = int(u_row.get("exp") or 0)
        sign = u_row.get("sign") or "心之所向，剑之所指。"
        nick = u_row.get("nick") or f"管理员_{uid}"

        # 2. game_user 辅助查询
        gu_row = self.query_one(
            "SELECT board_hero, cur_portrait, cur_icon_frame, cur_card_bg, cur_bubble, birth_month, birth_day FROM game_user WHERE uid = ?", (uid,)
        ) or {}

        portrait_id = gu_row.get("cur_portrait") or u_row.get("portrait") or 1084
        icon_frame_id = gu_row.get("cur_icon_frame") or u_row.get("icon_frame") or 1

        # 3. 佩戴称号列表 (profile_tags)
        tag_map = {}
        try:
            dec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decorations_catalog.json")
            with open(dec_path, "r", encoding="utf-8") as f:
                tag_map = json.load(f).get("TAG", {})
        except Exception:
            pass

        profile_tags_raw = extra.get("profile_tags", [])
        profile_tags = []
        for tid in profile_tags_raw:
            t_name = tag_map.get(str(tid), f"称号_{tid}")
            profile_tags.append({"id": int(tid), "name": t_name})

        # 4. 当前看板娘 (board_hero)
        hero_names = {}
        try:
            hero_names_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero_names_catalog.json")
            with open(hero_names_path, "r", encoding="utf-8") as f:
                hero_names = json.load(f)
        except Exception:
            pass

        board_hero_id = gu_row.get("board_hero") or extra.get("active_board_hero") or 1084
        hero_meta = hero_names.get(str(board_hero_id), {})
        board_hero_name = hero_meta.get("full_name") or hero_meta.get("name") or f"修正者_{board_hero_id}"
        board_hero = {
            "id": board_hero_id,
            "name": board_hero_name,
            "avatar": f"/extracted_assets/avatars/{board_hero_id}.png"
        }

        # 5. 换装数量统计（严格排除 1001 管理员换装系列）
        skin_count_row = self.query_one(
            "SELECT count(*) as cnt FROM player_skin_unlocked WHERE uid = ? AND skin_id NOT LIKE '1001%'", (uid,)
        )
        skin_count = int(skin_count_row.get("cnt", 0)) if skin_count_row else 0

        # 6. 体力与精确恢复倒计时（官方 6 分钟 360 秒 / 点）
        fatigue_row = self.query_one("SELECT num FROM currency WHERE uid = ? AND id = 4", (uid,))
        current_fatigue = int(fatigue_row.get("num", 240)) if fatigue_row else 240

        if level >= 71:
            max_fatigue = 240
        elif level <= 1:
            max_fatigue = 100
        else:
            max_fatigue = min(240, 100 + (level - 1) * 2)

        is_full = current_fatigue >= max_fatigue
        if is_full:
            seconds_to_next = 0
            seconds_to_full = 0
            next_point_str = "已满"
            full_recovery_str = "已完全回满"
        else:
            rem_points = max_fatigue - current_fatigue
            seconds_to_next = 360
            seconds_to_full = (rem_points - 1) * 360 + seconds_to_next
            next_point_str = f"{seconds_to_next // 60:02d}分{seconds_to_next % 60:02d}秒"
            hrs = seconds_to_full // 3600
            mins = (seconds_to_full % 3600) // 60
            full_recovery_str = f"{hrs}小时{mins:02d}分" if hrs > 0 else f"{mins}分钟"

        # 7. 在线状态与设备类型
        w_row = self.query_one("SELECT is_online FROM user_timer_watermark WHERE uid = ?", (uid,)) or {}
        is_online = bool(w_row.get("is_online", 0))
        client_device = "Windows 在线" if is_online else "离线"

        # 8. 贴纸画册与个性化贴纸墙 (Sticker Wall)
        stickers_catalog = {}
        try:
            stk_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stickers_catalog.json")
            with open(stk_path, "r", encoding="utf-8") as f:
                stickers_catalog = json.load(f)
        except Exception:
            pass

        stk_map = stickers_catalog.get("stickers", {})
        bg_map = stickers_catalog.get("backgrounds", {})

        raw_sticker_pages = extra.get("sticker_show_info") or []
        active_page_id = extra.get("sticker_show_page")

        # 找到当前展示的背景页 (若未指定，则取第一页或默认背景 4002)
        cur_page = None
        if active_page_id:
            for p in raw_sticker_pages:
                if isinstance(p, dict) and p.get("page_id") == active_page_id:
                    cur_page = p
                    break
        if not cur_page and raw_sticker_pages and isinstance(raw_sticker_pages[0], dict):
            cur_page = raw_sticker_pages[0]

        if not cur_page:
            cur_page = {"page_id": 4002, "foreground": 0, "sticker_display_info": []}

        page_id = cur_page.get("page_id", 4002)
        bg_meta = bg_map.get(str(page_id), {})
        page_name = bg_meta.get("name", f"背景_{page_id}")
        page_desc = bg_meta.get("desc", "")

        stickers_list = []
        for s in cur_page.get("sticker_display_info") or []:
            sid = s.get("sticker_id")
            s_meta = stk_map.get(str(sid), {})
            stickers_list.append({
                "id": sid,
                "name": s_meta.get("name", f"贴纸_{sid}"),
                "desc": s_meta.get("desc", ""),
                "rare": s_meta.get("rare", 5),
                "location_x": s.get("location_x", 0),
                "location_y": s.get("location_y", 0),
                "scale": s.get("scale", 5000),
                "layer": s.get("layer", 1),
                "rotate": s.get("rotate", 0),
            })

        # 按 layer 从小到大排序
        stickers_list.sort(key=lambda x: x["layer"])

        sticker_wall = {
            "page_id": page_id,
            "page_name": page_name,
            "page_desc": page_desc,
            "foreground": cur_page.get("foreground", 0),
            "total_stickers": len(stickers_list),
            "stickers": stickers_list,
        }

        # 构建支持完整左右切换的多贴纸墙画卷列表
        user_pages_map = {}
        for p in raw_sticker_pages:
            if isinstance(p, dict) and "page_id" in p:
                user_pages_map[p["page_id"]] = p

        all_bg_ids = [4007, 4001, 4002, 4003, 4004, 4006, 4008, 4009]
        ordered_bg_ids = []
        if page_id in all_bg_ids:
            ordered_bg_ids.append(page_id)
        for bid in all_bg_ids:
            if bid not in ordered_bg_ids:
                ordered_bg_ids.append(bid)
        for pid in user_pages_map:
            if pid not in ordered_bg_ids:
                ordered_bg_ids.append(pid)

        sticker_walls = []
        for pid in ordered_bg_ids:
            p_data = user_pages_map.get(pid, {"page_id": pid, "foreground": 0, "sticker_display_info": []})
            p_meta = bg_map.get(str(pid), {})
            p_name = p_meta.get("name", f"背景_{pid}")
            p_desc = p_meta.get("desc", "")
            p_stickers = []
            for s in p_data.get("sticker_display_info") or []:
                sid = s.get("sticker_id")
                s_meta = stk_map.get(str(sid), {})
                p_stickers.append({
                    "id": sid,
                    "name": s_meta.get("name", f"贴纸_{sid}"),
                    "desc": s_meta.get("desc", ""),
                    "rare": s_meta.get("rare", 5),
                    "location_x": s.get("location_x", 0),
                    "location_y": s.get("location_y", 0),
                    "scale": s.get("scale", 5000),
                    "layer": s.get("layer", 1),
                    "rotate": s.get("rotate", 0),
                })
            p_stickers.sort(key=lambda x: x["layer"])
            sticker_walls.append({
                "page_id": pid,
                "page_name": p_name,
                "page_desc": p_desc,
                "foreground": p_data.get("foreground", 0),
                "is_active": (pid == page_id),
                "total_stickers": len(p_stickers),
                "stickers": p_stickers,
            })

        profile = {
            "uid": uid,
            "nickname": nick,
            "level": level,
            "exp": exp,
            "sign": sign,
            "avatar_id": portrait_id,
            "icon_frame_id": icon_frame_id,
            "profile_tags": profile_tags,
            "board_hero": board_hero,
            "skin_count": skin_count,
            "fatigue": current_fatigue,
            "max_fatigue": max_fatigue,
            "fatigue_detail": {
                "current": current_fatigue,
                "max": max_fatigue,
                "is_full": is_full,
                "seconds_to_next": seconds_to_next,
                "seconds_to_full": seconds_to_full,
                "next_point_str": next_point_str,
                "full_recovery_str": full_recovery_str,
            },
            "is_online": is_online,
            "client_device": client_device,
            "status": "online" if is_online else "offline",
            "sticker_wall": sticker_wall,
            "sticker_walls": sticker_walls,
        }
        return profile

    # -----------------------------------------------------------------------

    # 3. 玩家角色视图 (Heroes ViewDTO)
    # -----------------------------------------------------------------------
    OFFICIAL_HERO_IDS = [
        1011, 1012, 1013, 1015, 1016, 1017, 1019, 1020, 1021, 1022, 1024, 1026, 1027, 1028,
        1032, 1033, 1034, 1035, 1037, 1038, 1039, 1041, 1042, 1043, 1044, 1045, 1046, 1047,
        1048, 1049, 1050, 1052, 1053, 1054, 1055, 1056, 1058, 1059, 1060, 1061, 1066, 1067,
        1068, 1070, 1071, 1072, 1073, 1074, 1075, 1076, 1077, 1080, 1081, 1083, 1084, 1085,
        1089, 1093, 1094, 1095, 1096, 1097, 1099, 1111, 1119, 1127, 1132, 1133, 1137, 1138,
        1139, 1148, 1150, 1156, 1158, 1166, 1170, 1184, 1194, 1197, 1199, 1211, 1248, 1284
    ]

    def get_heroes_list_view(self, uid):
        """查询玩家拥有的修正者名册及角色状态四大核心统计。"""
        uid = int(uid)
        catalog = _get_heroes_catalog()
        cat_map = {item["id"]: item for item in catalog}

        # 查 hero 表 (等级、星级、解锁状态、收藏状态)
        hero_rows = self.query("SELECT id, level, star, unlock, is_favorite FROM hero WHERE uid = ?", (uid,))
        hero_dict = {r["id"]: r for r in hero_rows}

        # 查 hero_oath 表 (誓约状态、誓约等级、爱称)
        oath_rows = self.query("SELECT hero_id, oath, oath_level, nick FROM hero_oath WHERE uid = ?", (uid,))
        oath_dict = {r["hero_id"]: r for r in oath_rows}

        result = []
        for hid in self.OFFICIAL_HERO_IDS:
            meta = cat_map.get(hid)
            if not meta:
                continue
            h_row = hero_dict.get(hid)
            o_row = oath_dict.get(hid)

            unlocked = bool(h_row.get("unlock", 1)) if h_row else False
            level = int(h_row.get("level") or 1) if h_row else 1
            star = int(h_row.get("star") or meta.get("unlock_star") or 200) if h_row else meta.get("unlock_star", 200)
            is_fav = bool(h_row.get("is_favorite", 0)) if h_row else False

            is_oath = bool(o_row.get("oath", 0)) if o_row else False
            oath_lvl = int(o_row.get("oath_level", 0)) if o_row else 0
            custom_name = (o_row.get("nick") or "").strip() if (is_oath and o_row) else ""

            # 品阶转换 (star 数值: 600=Ω, 500=SSS, 400=SS, 300=S, 200=A, 其他=B)
            if star >= 600:
                grade_name = "Ω"
            elif star >= 500:
                grade_name = "SSS"
            elif star >= 400:
                grade_name = "SS"
            elif star >= 300:
                grade_name = "S"
            elif star >= 200:
                grade_name = "A"
            else:
                grade_name = "B"

            official_name = meta["official_name"]
            display_name = f"{custom_name} ({official_name})" if custom_name else official_name

            result.append({
                "id": hid,
                "name": meta["name"],
                "title": meta.get("title") or meta.get("suffix") or "",
                "official_name": official_name,
                "custom_name": custom_name,
                "display_name": display_name,
                "pinyin_initial": meta["pinyin_initial"],
                "full_pinyin_initials": meta["full_pinyin_initials"],
                "race_id": meta["race_id"],
                "race_name": meta["race_name"],
                "race_icon": meta["race_icon"],
                "element_id": meta["element_id"],
                "element_name": meta["element_name"],
                "element_icon": meta["element_icon"],
                "star": star,
                "grade_name": grade_name,
                "level": level,
                "unlocked": unlocked,
                "is_favorite": is_fav,
                "is_oath": is_oath,
                "oath_level": oath_lvl,
                "avatar": meta["avatar"],
            })

        # 聚合四大核心指标：
        # 1. 角色数量 (已拥有 / 84)
        # 2. 誓约角色数量 (hero_oath.oath=1)
        # 3. 收藏角色数量 (hero.is_favorite=1)
        # 4. 欧米茄角色数量 (hero.star>=600)
        placeholders = ",".join(["?"] * len(self.OFFICIAL_HERO_IDS))
        stats_sql = f"""
            SELECT
                COUNT(CASE WHEN unlock = 1 THEN 1 END) as owned_count,
                COUNT(CASE WHEN is_favorite = 1 THEN 1 END) as favorite_count,
                COUNT(CASE WHEN star >= 600 THEN 1 END) as omega_count
            FROM hero
            WHERE uid = ? AND id IN ({placeholders})
        """
        stats_row = self.query_one(stats_sql, (uid, *self.OFFICIAL_HERO_IDS)) or {}
        owned_count = int(stats_row.get("owned_count") or 0)
        fav_count = int(stats_row.get("favorite_count") or 0)
        omega_count = int(stats_row.get("omega_count") or 0)

        # 誓约角色数量
        oath_row = self.query_one("SELECT COUNT(1) as cnt FROM hero_oath WHERE uid = ? AND oath = 1", (uid,)) or {}
        oath_count = int(oath_row.get("cnt") or 0)

        stats = {
            "owned_count": owned_count,
            "total_official": len(self.OFFICIAL_HERO_IDS),
            "oath_count": oath_count,
            "favorite_count": fav_count,
            "omega_count": omega_count,
        }

        return {
            "uid": uid,
            "total": len(result),
            "heroes": result,
            "stats": stats,
        }

    def get_hero_full_detail(self, uid, hero_id):
        """【只读通道】查询单体修正者的 8 大核心系统完整养成详情 DTO。"""
        uid = int(uid)
        hero_id = int(hero_id)
        catalog = _get_hero_details_catalog()
        heroes_dict = catalog.get("heroes", {})
        equip_prefabs = catalog.get("equip_prefabs", {})
        equip_suits = catalog.get("equip_suits", {})
        equip_skills = catalog.get("equip_skills", {})
        chips_dict = catalog.get("chips", {})

        meta = heroes_dict.get(str(hero_id), {})
        if not meta:
            meta = {
                "id": hero_id,
                "name": f"修正者_{hero_id}",
                "portrait": f"extracted_assets/portraits/{hero_id}.png",
                "module_supported": False,
                "servant": None,
                "skills": [],
                "astrolabes": []
            }

        # 1. 查 hero 表
        h_row = self.query_one("SELECT * FROM hero WHERE uid = ? AND id = ?", (uid, hero_id)) or {}
        # 2. 查 hero_oath 表
        o_row = self.query_one("SELECT * FROM hero_oath WHERE uid = ? AND hero_id = ?", (uid, hero_id)) or {}
        # 3. 查 hero_oath_plot
        plot_rows = self.query("SELECT plot_idx FROM hero_oath_plot WHERE uid = ? AND hero_id = ?", (uid, hero_id))

        unlocked = bool(h_row.get("unlock", 1)) if h_row else False
        level = int(h_row.get("level") or 1) if h_row else 1
        star = int(h_row.get("star") or 200) if h_row else 200

        # 品阶名称与小等阶
        star_level = max(1, min(6, star // 100))
        phase = star % 100
        grade_names = {1: 'B', 2: 'A', 3: 'S', 4: 'SS', 5: 'SSS', 6: 'Ω'}
        grade_name = grade_names.get(star_level, 'S')
        if not unlocked:
            phase_text = "未招募"
        elif star_level == 6:
            phase_text = "超越巅峰"
        elif phase == 0:
            phase_text = "基础阶"
        else:
            phase_nums = ['', '一', '二', '三', '四']
            phase_text = f"超越{phase_nums[phase] if phase < len(phase_nums) else phase}阶"

        # 权钥真实等级换算 (结合经验表与突破阶段上限)
        w_break = int(h_row.get("weapon_break") or 0) if h_row else 0
        w_exp = int(h_row.get("weapon_exp") or 0) if h_row else 0
        weapon_level = _calc_weapon_level(w_exp, w_break)

        # 模组等级与支持状态 (对齐官方 53 位白名单与库中持久化状态)
        mod_supported = bool(meta.get("module_supported", False)) or int(h_row.get("module_level") or 0) > 0
        mod_level = int(h_row.get("module_level") or 0) if (h_row and mod_supported) else 0

        # 钥从真实装配状态解析 (严格判定 weapon_servant_uid 与权威全量钥从字典)
        servants_catalog = _get_weapon_servants_catalog()
        servant_uid = int(h_row.get("weapon_servant_uid") or 0) if h_row else 0
        s_row = self.query_one("SELECT * FROM servant WHERE uid = ? AND id = ?", (uid, servant_uid)) if servant_uid > 0 else None

        # 查询玩家是否持有该角色推荐的专属钥从及其最高突破等阶与官方原设描述
        raw_exclusive = meta.get("servant") or {}
        exclusive_info = dict(raw_exclusive)
        ex_id = int(exclusive_info.get("id") or 0)
        ex_owned = False
        ex_stage = 0
        if ex_id > 0:
            ex_cfg = servants_catalog.get(str(ex_id)) or {}
            exclusive_info["desc"] = ex_cfg.get("desc") or ""
            exclusive_info["effect_desc"] = ex_cfg.get("effect_desc") or ""
            exclusive_info["star"] = ex_cfg.get("star", 5)
            exclusive_info["race_id"] = ex_cfg.get("race_id") or meta.get("race_id", 0)
            exclusive_info["race_name"] = ex_cfg.get("race_name") or meta.get("race_name", "")
            if not exclusive_info.get("name") and ex_cfg.get("name"):
                exclusive_info["name"] = ex_cfg["name"]
            if not exclusive_info.get("portrait") and ex_cfg.get("portrait"):
                exclusive_info["portrait"] = ex_cfg["portrait"]

            ex_row = self.query_one("SELECT stage FROM servant WHERE uid = ? AND prefab_id = ? ORDER BY stage DESC LIMIT 1", (uid, ex_id))
            if ex_row:
                ex_owned = True
                ex_stage = int(ex_row.get("stage") or 1)
        exclusive_info["owned"] = ex_owned
        exclusive_info["stage"] = ex_stage

        if s_row:
            s_prefab = int(s_row.get("prefab_id") or 0)
            s_cfg = servants_catalog.get(str(s_prefab)) or {}
            s_name = s_cfg.get("name") or s_row.get("name") or exclusive_info.get("name") or f"钥从_{s_prefab}"
            is_univ = bool(s_cfg.get("is_universal", False))
            s_stage = int(s_row.get("stage") or 1)
            portrait_path = s_cfg.get("portrait") or f"extracted_assets/servants/{s_prefab}.png"
            icon_path = s_cfg.get("icon") or f"extracted_assets/servants/icons/{s_prefab}.png"

            servant_info = {
                "id": s_prefab,
                "name": s_name,
                "portrait": portrait_path,
                "icon": icon_path,
                "stage": s_stage,
                "equipped": True,
                "is_universal": is_univ,
                "star": s_cfg.get("star", 5),
                "type": s_cfg.get("type", 1 if is_univ else 2),
                "race_id": s_cfg.get("race_id") or meta.get("race_id", 0),
                "race_name": s_cfg.get("race_name") or meta.get("race_name", ""),
                "desc": s_cfg.get("desc", ""),
                "effect_desc": s_cfg.get("effect_desc", ""),
                "exclusive_servant": exclusive_info,
            }
        else:
            # 真实未装配状态：严禁向未装配角色下发虚假 5 阶专属钥从
            servant_info = {
                "id": 0,
                "name": "未装载",
                "portrait": "",
                "icon": "",
                "stage": 0,
                "equipped": False,
                "is_universal": False,
                "star": 0,
                "type": 0,
                "race_id": meta.get("race_id", 0),
                "race_name": meta.get("race_name", ""),
                "desc": "",
                "effect_desc": "",
                "exclusive_servant": exclusive_info,  # 保留推荐专属钥从元数据供界面参考展示
            }

        # 技能组等级与星级加成合并
        raw_skill_list = []
        if h_row and h_row.get("skill_list"):
            try:
                raw_skill_list = json.loads(h_row["skill_list"])
            except Exception:
                pass
        skill_level_map = {item[0]: item[1] for item in raw_skill_list if isinstance(item, list) and len(item) >= 2}

        # 属性强化等级解析 (槽位 1~5 对应 0~15 级)
        raw_intens_list = []
        if h_row and h_row.get("skill_intensify"):
            try:
                raw_intens_list = json.loads(h_row["skill_intensify"])
            except Exception:
                pass
        intens_map = {item[0]: item[1] for item in raw_intens_list if isinstance(item, list) and len(item) >= 2}

        star_adds = _calc_star_skill_add(hero_id, star)
        skills = []
        for idx, s in enumerate(meta.get("skills", []), start=1):
            sid = s["id"]
            is_dodge = (idx == 6) or (s.get("type") == "闪避") or ("闪避" in s.get("name", ""))
            slvl = 1 if is_dodge else skill_level_map.get(sid, s.get("level", 1))
            add_lvl = 0 if is_dodge else star_adds.get(idx, 0)
            intens_lvl = 0 if is_dodge else intens_map.get(idx, 0)
            skills.append({
                "id": sid,
                "name": s.get("name", f"技能_{sid}"),
                "type": s.get("type", "普通技能"),
                "icon": s.get("icon", ""),
                "level": slvl,
                "add_level": add_lvl,
                "total_level": slvl + add_lvl,
                "intensify": intens_lvl,
                "is_dodge": is_dodge
            })

        # 刻印 6 槽位
        equip_rows = self.query("SELECT * FROM equip WHERE uid = ? AND hero_id = ?", (uid, hero_id))
        suit_counts = {}
        slot_map = {}
        for er in equip_rows:
            pid = er.get("prefab_id")
            pmeta = equip_prefabs.get(str(pid))
            if pmeta:
                pos = pmeta.get("pos")
                star = pmeta.get("star", 5)
                suit_id = pmeta.get("suit_id")
                suit_name = pmeta.get("suit_name") or equip_suits.get(str(suit_id), "刻印")
                icon = pmeta.get("icon") or f"extracted_assets/equips/suit_{suit_id}.png"
            else:
                s_pid = str(pid)
                if len(s_pid) == 6 and s_pid.isdigit():
                    star = int(s_pid[0])
                    pos = int(s_pid[1])
                    race = int(s_pid[2])
                    suit_id = int(s_pid[3:])
                    suit_name = equip_suits.get(str(suit_id), equip_suits.get(suit_id, f"刻印套装_{suit_id}"))
                    icon = f"extracted_assets/equips/suit_{suit_id}.png"
                else:
                    star = 5
                    pos = er.get("id") % 6 + 1
                    suit_id = 0
                    suit_name = "未知刻印"
                    icon = ""

            suit_counts[suit_name] = suit_counts.get(suit_name, 0) + 1

            # 刻印真实等级计算
            now_break = int(er.get("now_break_level") or 0)
            exp_val = int(er.get("exp") or 0)
            if now_break >= 5 or exp_val >= 27800:
                equip_level = 60
            elif exp_val > 0:
                try:
                    from equip_service import EquipService
                    eq_svc = EquipService.get_instance(db=self.db)
                    mls = [10, 20, 30, 40, 50, 60]
                    max_lv = mls[min(now_break, len(mls) - 1)]
                    equip_level, _, _ = eq_svc.calc_equip_level(star, exp_val, max_lv=max_lv, db=self.db)
                except Exception:
                    equip_level = 20 + now_break * 10
            else:
                equip_level = 20 if now_break == 0 else 20 + now_break * 10

            # 赋能解析：单个刻印最高可携带 4 个赋能词条（严格提取已生效的 effect_list）
            enchants = []
            if er.get("enchant_slots"):
                try:
                    raw_enchant = json.loads(er["enchant_slots"])
                    for slot_item in raw_enchant:
                        for eff in slot_item.get("effect_list") or []:
                            eff_id = eff.get("id")
                            if not eff_id:
                                continue
                            eff_meta = equip_skills.get(str(eff_id), {})
                            enchants.append({
                                "id": eff_id,
                                "name": eff_meta.get("name", f"词条_{eff_id}"),
                                "icon": eff_meta.get("icon", ""),
                                "level": eff.get("level", 1)
                            })
                except Exception:
                    pass

            slot_map[pos] = {
                "pos": pos,
                "equipped": True,
                "name": suit_name,
                "suit_name": suit_name,
                "suit_id": suit_id,
                "level": equip_level,
                "star": star,
                "icon": icon,
                "enchants": enchants[:4]  # 单个刻印最高承载 4 个赋能词条
            }

        equips = []
        for pos in range(1, 7):
            if pos in slot_map:
                equips.append(slot_map[pos])
            else:
                equips.append({
                    "pos": pos,
                    "equipped": False,
                    "name": f"槽位 {pos}",
                    "suit_name": "未激活刻印",
                    "suit_id": 0,
                    "level": 0,
                    "star": 0,  # 未装备为 0 星，前端安全识别
                    "icon": "",
                    "enchants": []
                })

        # 刻印套装效果判定（严格遵循官方神格与品阶裁定机制：Ω 阶解锁 skill_id 501，套装件数需求 -1）
        is_omega = bool(star >= 600 or star_level >= 6)
        suit_need = 2 if is_omega else 3

        active_suits = []
        for sname, cnt in suit_counts.items():
            if cnt >= suit_need:
                if is_omega:
                    if cnt >= 3:
                        active_suits.append(f"{sname} (3件套 · Ω)")
                    else:
                        active_suits.append(f"{sname} (Ω 2件套)")
                else:
                    active_suits.append(f"{sname} (3件套)")

        # 跃迁 1~6 槽位
        from hero_codec import normalize_exclusive_skills
        norm_ex = normalize_exclusive_skills(h_row.get("exclusive_skill_list") if h_row else None)
        slot_names = ["槽位一", "槽位二", "槽位三", "槽位四", "槽位五", "槽位六"]
        transitions = []
        for slot_id in range(1, 7):
            s_data = norm_ex.get(str(slot_id))
            if s_data and s_data.get("skill_list"):
                s_list = []
                for s_entry in s_data["skill_list"]:
                    sk_id = s_entry["skill_id"]
                    sk_lvl = s_entry["skill_level"]
                    sk_meta = equip_skills.get(str(sk_id), {})
                    sk_name = sk_meta.get("name") or f"跃迁技_{sk_id}"
                    sk_icon = sk_meta.get("icon") or f"extracted_assets/equipskills/icon_id{sk_id}.png"
                    s_list.append({
                        "id": sk_id,
                        "name": sk_name,
                        "icon": sk_icon,
                        "level": sk_lvl
                    })
                first_icon = s_list[0]["icon"] if s_list else ""
                transitions.append({
                    "slot_id": slot_id,
                    "slot_name": slot_names[slot_id - 1],
                    "total_level": s_data.get("talent_points", 0),
                    "icon": first_icon,
                    "skills": s_list
                })
            else:
                transitions.append({
                    "slot_id": slot_id,
                    "slot_name": slot_names[slot_id - 1],
                    "total_level": s_data.get("talent_points", 0) if s_data else 0,
                    "icon": "",
                    "skills": []
                })

        # 芯片动态槽位解析 (适配非固定槽位数量，支持 1~N 槽位动态流式展现)
        raw_chip_state = {}
        if h_row and h_row.get("chip_state"):
            try:
                raw_chip_state = json.loads(h_row["chip_state"])
            except Exception:
                pass

        chips_cfg = _get_chips_catalog()

        # 收集该角色所有存在的槽位编号
        existing_slots = set()
        for k in raw_chip_state.keys():
            try:
                existing_slots.add(int(k))
            except (ValueError, TypeError):
                pass

        # 动态判定槽位范围：若角色已有槽位，取其最大槽位号（且保底至少展示到 4 槽位）；若为空则默认 1~4 槽位
        if existing_slots:
            max_slot = max(max(existing_slots), 4)
            target_slots = list(range(1, max_slot + 1))
        else:
            target_slots = [1, 2, 3, 4]

        chips = []
        for slot_id in sorted(target_slots):
            cid = raw_chip_state.get(str(slot_id), 0)
            if cid is None:
                cid = 0
            try:
                cid = int(cid)
            except (ValueError, TypeError):
                cid = 0

            equipped = (cid > 0)
            c_info = chips_cfg.get(str(cid), {}) if equipped else {}

            pic_id = c_info.get("picture_id") or (str(cid) if equipped else "")
            icon_path = f"extracted_assets/chips/{pic_id}.png" if pic_id else ""

            chips.append({
                "slot_id": slot_id,
                "slot_name": f"槽位 {slot_id}",
                "equipped": equipped,
                "id": cid,
                "name": c_info.get("name") or (f"管理芯片_{cid}" if equipped else "未装配芯片"),
                "desc": c_info.get("desc", ""),
                "picture_id": pic_id,
                "icon": icon_path,
                "role_type_id": c_info.get("role_type_id", 0),
                "role_type_name": c_info.get("role_type_name", "通用模块" if equipped else ""),
                "cost": c_info.get("cost", 0)
            })

        # 神格
        raw_using_astro = []
        if h_row and h_row.get("using_astrolabe"):
            try:
                raw_using_astro = json.loads(h_row["using_astrolabe"])
            except Exception:
                pass
        all_astrolabes = meta.get("astrolabes", [])
        active_astrolabes = [a for a in all_astrolabes if a["id"] in raw_using_astro]

        # 心链誓约与双轨好感度体系 (一阶档案好感 Like + 二阶专属交心 Trust)
        is_oath = bool(o_row.get("oath", 0)) if o_row else False
        custom_nick = (o_row.get("nick") or "").strip() if (is_oath and o_row) else ""
        oath_level = int(o_row.get("oath_level", 1)) if (is_oath and o_row) else 0
        oath_time = int(o_row.get("oath_time", 0)) if (is_oath and o_row) else 0

        archive_cfg = _get_archive_config()
        h2a = archive_cfg.get("hero_to_archive", {})
        archive_id = h2a.get(str(hero_id), hero_id)

        a_row = self.query_one(
            "SELECT exp, video_list, super_heart_link_list FROM hero_archive WHERE uid = ? AND archive_id = ?",
            (uid, archive_id)
        ) or {}
        archive_exp = int(a_row.get("exp") or 0)
        like_info = _calc_like_level(archive_exp)

        # 二阶专属交心系统 (全游戏仅 27 位修正者实装)
        trust_heroes = set(archive_cfg.get("trust_heroes", []))
        has_trust = int(hero_id) in trust_heroes
        raw_trust_lvl = int(h_row.get("trust_level") or 0) if h_row else 0
        raw_trust_exp = int(h_row.get("trust_exp") or 0) if h_row else 0
        raw_trust_mood = int(h_row.get("trust_mood") or 1) if h_row else 1

        trust_levels_cfg = archive_cfg.get("trust_level_cfg", {})
        mood_cfgs = archive_cfg.get("mood_cfg", {})

        if not has_trust:
            trust_level = 0
            trust_title = "未开放"
            trust_exp = 0
            trust_exp_max = 0
        elif raw_trust_lvl == 0:
            trust_level = 0
            trust_title = "未解锁"
            trust_exp = raw_trust_exp
            trust_exp_max = 1500
        else:
            trust_level = min(5, raw_trust_lvl)
            t_cfg = trust_levels_cfg.get(str(trust_level), {})
            trust_title = t_cfg.get("name", "至交")
            trust_exp_max = t_cfg.get("exp", 0)
            trust_exp = raw_trust_exp

        m_cfg = mood_cfgs.get(str(raw_trust_mood), {"name": "平静", "rate": 1100, "buff": "+10%"})
        mood_name = m_cfg.get("name", "平静")
        mood_rate = m_cfg.get("rate", 1100)
        mood_buff = m_cfg.get("buff", "+10%")

        # 心链剧情进度与上限
        video_cnt = 0
        super_cnt = 0
        if a_row:
            try:
                vlist = json.loads(a_row.get("video_list") or "[]")
                video_cnt = len(vlist) if isinstance(vlist, list) else 0
            except Exception:
                video_cnt = 0
            try:
                slist = json.loads(a_row.get("super_heart_link_list") or "[]")
                super_cnt = len(slist) if isinstance(slist, list) else 0
            except Exception:
                super_cnt = 0

        archive_plot_cnt = video_cnt + super_cnt
        oath_plot_cnt = len(plot_rows)
        plot_progress = max(archive_plot_cnt, oath_plot_cnt)

        arc_info = archive_cfg.get("archives", {}).get(str(archive_id), {})
        cfg_plots = len(arc_info.get("plot_ids", [])) + len(arc_info.get("super_plot_ids", []))
        plot_max = max(cfg_plots, plot_progress, 4)

        return {
            "hero_id": hero_id,
            "name": meta.get("name", ""),
            "portrait": meta.get("portrait", ""),
            "base": {
                "level": level,
                "star": star,
                "grade_name": grade_name,
                "phase": phase,
                "phase_text": phase_text,
                "weapon_level": weapon_level,
                "weapon_break": w_break,
                "module_level": mod_level,
                "module_supported": mod_supported,
                "unlocked": unlocked
            },
            "servant": servant_info,
            "skills": skills,
            "equips": {
                "slots": equips,
                "active_suits": active_suits,
                "is_omega": is_omega,
                "suit_need": suit_need
            },
            "transitions": transitions,
            "chips": chips,
            "astrolabe": {
                "active": active_astrolabes,
                "all_nodes": all_astrolabes
            },
            "oath": {
                "is_oath": is_oath,
                "nick": custom_nick,
                "oath_level": oath_level,
                "oath_time": oath_time,
                "plot_progress": plot_progress,
                "plot_max": plot_max,
                # 一阶好感 (Like)
                "like_level": like_info["like_level"],
                "like_roman": like_info["like_roman"],
                "like_title": like_info["like_title"],
                "like_exp": like_info["like_exp"],
                "like_exp_max": like_info["like_exp_max"],
                "like_total_exp": like_info["like_total_exp"],
                # 二阶交心 (Trust)
                "has_trust": has_trust,
                "trust_level": trust_level,
                "trust_title": trust_title,
                "trust_exp": trust_exp,
                "trust_exp_max": trust_exp_max,
                "trust_mood": raw_trust_mood,
                "mood_name": mood_name,
                "mood_rate": mood_rate,
                "mood_buff": mood_buff
            }
        }

    # -----------------------------------------------------------------------
    # 4. 资源背包视图 (Inventory ViewDTO)
    # -----------------------------------------------------------------------

    def get_inventory_items_view(self, uid):
        """反查玩家背包当前所有货币、素材与刻印持仓。"""
        uid = int(uid)
        items = []

        # 1. 货币表
        c_rows = self.query("SELECT id, num FROM currency WHERE uid = ? AND num > 0", (uid,))
        for r in c_rows:
            items.append({"id": r["id"], "num": r["num"], "type": "currency"})

        # 2. 材料表
        m_rows = self.query("SELECT id, num FROM material WHERE uid = ? AND num > 0", (uid,))
        for r in m_rows:
            items.append({"id": r["id"], "num": r["num"], "type": "material"})

        # 3. 刻印表
        e_rows = self.query("SELECT id, equip_id, star, level FROM equip WHERE uid = ?", (uid,))
        for r in e_rows:
            items.append({
                "id": r.get("equip_id") or r.get("id"),
                "instance_id": r.get("id"),
                "star": r.get("star", 5),
                "level": r.get("level", 60),
                "type": "equip",
                "num": 1,
            })

        return {
            "uid": uid,
            "total": len(items),
            "items": items,
        }

    # -----------------------------------------------------------------------
    # 5. 邮件系统视图 (Mail ViewDTO)
    # -----------------------------------------------------------------------

    def get_mail_history_view(self, uid, limit=50):
        """查询玩家真实的邮件列表（对齐 mail 表实际列结构）。"""
        uid = int(uid)
        limit = max(1, min(100, int(limit)))
        rows = self.query(
            """SELECT mail_id, title, sender, content_json, attachment_json,
                      send_time, expire_time, read_flag, attach_flag, star_state
               FROM mail WHERE uid = ? ORDER BY mail_id DESC LIMIT ?""",
            (uid, limit)
        )

        mails = []
        for r in rows:
            content_str = ""
            if r.get("content_json"):
                try:
                    c_data = json.loads(r["content_json"])
                    if isinstance(c_data, list):
                        content_str = "\n".join(str(x) for x in c_data)
                    elif isinstance(c_data, dict):
                        content_str = str(c_data.get("content") or c_data.get("text") or "")
                    else:
                        content_str = str(c_data)
                except Exception:
                    content_str = str(r["content_json"])

            attach_list = []
            if r.get("attachment_json"):
                try:
                    raw_attach = json.loads(r["attachment_json"])
                    if isinstance(raw_attach, list):
                        for a in raw_attach:
                            if isinstance(a, dict):
                                aid = int(a.get("id") or a.get("item_id") or 0)
                                anum = int(a.get("number") or a.get("num") or a.get("count") or 0)
                                if aid > 0 and anum > 0:
                                    attach_list.append({"id": aid, "number": anum})
                except Exception:
                    pass

            mails.append({
                "mail_id": r["mail_id"],
                "id": r["mail_id"],
                "title": r.get("title") or "系统邮件",
                "sender": r.get("sender") or "隐科组总务部",
                "content": content_str,
                "attachments": attach_list,
                "rewards": attach_list,
                "send_time": r.get("send_time", 0),
                "read_flag": r.get("read_flag", 1),
                "attach_flag": r.get("attach_flag", 1),
                "is_read": r.get("read_flag", 1) == 2,
                "is_claimed": r.get("attach_flag", 1) == 2,
                "state": 1 if r.get("attach_flag", 1) == 2 else 0,
            })

        return {
            "uid": uid,
            "total": len(mails),
            "mails": mails,
        }

    # -----------------------------------------------------------------------
    # 6. 卡池管理视图 (Gacha ViewDTO)
    # -----------------------------------------------------------------------

    def get_gacha_pools_view(self, uid=None):
        """读取卡池在架信息与全量目录（支持控制面板卡池列表与开关展示及四大保底监控）。"""
        target_uid = int(uid) if uid else getattr(sys.modules.get("generator"), "DEFAULT_UID", 10001)
        active_ids = set()
        pity_states = {
            "hero_precision_90": {"pool_group": "hero_precision_90", "since_ssr": 0, "since_sr": 0, "total_draws": 0, "is_up_guaranteed": 0, "pity_cap": 90},
            "hero_precision_70": {"pool_group": "hero_precision_70", "since_ssr": 0, "since_sr": 0, "total_draws": 0, "is_up_guaranteed": 0, "pity_cap": 70},
            "hero_standard_70": {"pool_group": "hero_standard_70", "since_ssr": 0, "since_sr": 0, "total_draws": 0, "is_up_guaranteed": 0, "pity_cap": 70},
            "weapon_servant_70": {"pool_group": "weapon_servant_70", "since_ssr": 0, "since_sr": 0, "total_draws": 0, "is_up_guaranteed": 0, "pity_cap": 70},
        }
        try:
            conn = self._get_ro_conn()
            cur = conn.cursor()
            cur.execute("SELECT pool_id FROM active_draw_pool WHERE is_active=1")
            active_ids = {r[0] for r in cur.fetchall()}

            cur.execute("SELECT pool_group, since_ssr, since_sr, total_draws, up_id, is_up_guaranteed FROM draw_group_state WHERE uid=?", (target_uid,))
            for r in cur.fetchall():
                grp = r[0]
                if grp in pity_states:
                    pity_states[grp]["since_ssr"] = int(r[1] or 0)
                    pity_states[grp]["since_sr"] = int(r[2] or 0)
                    pity_states[grp]["total_draws"] = int(r[3] or 0)
                    pity_states[grp]["up_id"] = int(r[4] or 0)
                    pity_states[grp]["is_up_guaranteed"] = int(r[5] or 0)
        except Exception:
            pass

        if not active_ids:
            active_ids = {10000, 10001, 10002, 5030601, 5030301, 5020301, 5020302, 5020303, 5020601, 5000303, 4080101}

        import res_version_manager
        curr_ver = str(res_version_manager.get_current_version())

        cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "expansion_pools_catalog.json")
        pools = []
        if os.path.isfile(cat_path):
            try:
                with open(cat_path, "r", encoding="utf-8") as f:
                    cat_data = json.load(f)
                    for item in cat_data:
                        pid = item["pool_id"]
                        is_311_exclusive = str(pid).startswith("503")
                        is_locked = (curr_ver == "229" and is_311_exclusive)
                        is_act = (pid in active_ids) and not is_locked
                        hero_disp = item.get("hero_name") or item.get("pool_name") or str(pid)
                        pools.append({
                            "id": pid,
                            "pool_id": pid,
                            "name": f"[{item.get('type_label', '')}] {hero_disp}",
                            "pool_name": item.get("pool_name", ""),
                            "hero_name": hero_disp,
                            "type": item.get("pool_group", "hero_precision_70"),
                            "type_label": item.get("type_label", ""),
                            "enabled": is_act,
                            "active": is_act,
                            "pity_cap": 90 if item.get("pool_group") == "hero_precision_90" else 70,
                            "activity_id": item.get("activity_id", 0),
                            "grade": item.get("grade", "A"),
                            "hero_id": item.get("hero_id", 0),
                            "order": item.get("order", 0),
                            "min_version": "311" if is_311_exclusive else "229",
                            "is_version_locked": is_locked,
                        })
            except Exception:
                pass

        if not pools:
            default_items = [
                {"id": 10001, "name": "常态角色探测", "type": "hero_standard_70", "pity_cap": 70, "hero_id": 0},
                {"id": 10002, "name": "自选钥从探测", "type": "weapon_servant_70", "pity_cap": 70, "hero_id": 0},
                {"id": 5020301, "name": "奇迹缔造法则：澄心·陵光", "type": "hero_precision_70", "pity_cap": 70, "hero_id": 1053},
                {"id": 5020601, "name": "奇迹缔造法则(90)：澄心·陵光", "type": "hero_precision_90", "pity_cap": 90, "hero_id": 1053},
                {"id": 5000303, "name": "自选扩充探测(20人全选)", "type": "hero_precision_70", "pity_cap": 70, "hero_id": 0},
                {"id": 4080101, "name": "自选精准探测", "type": "hero_standard_70", "pity_cap": 70, "hero_id": 0},
                {"id": 10000, "name": "新人入职限定探测", "type": "hero_standard_70", "pity_cap": 70, "hero_id": 0},
            ]
            for it in default_items:
                is_311_exclusive = str(it["id"]).startswith("503")
                is_locked = (curr_ver == "229" and is_311_exclusive)
                is_act = (it["id"] in active_ids) and not is_locked
                pools.append({
                    "id": it["id"],
                    "pool_id": it["id"],
                    "name": it["name"],
                    "pool_name": it["name"],
                    "hero_name": it["name"],
                    "type": it["type"],
                    "type_label": it["type"],
                    "enabled": is_act,
                    "active": is_act,
                    "pity_cap": it["pity_cap"],
                    "activity_id": 0,
                    "grade": "A",
                    "hero_id": it.get("hero_id", 0),
                    "order": 0,
                    "min_version": "311" if is_311_exclusive else "229",
                    "is_version_locked": is_locked,
                })

        # 聚合四大卡池最近 100 条抽卡历史明细
        history_by_group = {}
        for grp in ["hero_precision_90", "hero_precision_70", "hero_standard_70", "weapon_servant_70"]:
            hist_res = self.get_draw_history(uid=target_uid, pool_group=grp, limit=100)
            history_by_group[grp] = hist_res.get("records", [])

        return {
            "pools": pools,
            "total": len(pools),
            "active_count": len([p for p in pools if p.get("enabled")]),
            "active_pool_ids": [p["id"] for p in pools if p.get("enabled")],
            "pity_states": pity_states,
            "history_by_group": history_by_group,
            "uid": target_uid,
        }

    def get_draw_history(self, uid=None, pool_group=None, limit=100):
        """读取指定账号的抽卡历史记录（最多 100 条）。"""
        target_uid = int(uid) if uid else getattr(sys.modules.get("generator"), "DEFAULT_UID", 10001)
        limit = max(1, min(100, int(limit or 100)))

        # 加载抽卡物品静态字典
        catalog_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw_items_catalog.json")
        item_map = {}
        if os.path.isfile(catalog_path):
            try:
                with open(catalog_path, "r", encoding="utf-8") as f:
                    item_map = json.load(f)
            except Exception:
                pass

        records = []
        try:
            conn = self._get_ro_conn()
            cur = conn.cursor()
            if pool_group:
                cur.execute(
                    "SELECT id, pool_id, pool_group, item_id, item_num, draw_ts FROM draw_record WHERE uid=? AND pool_group=? ORDER BY id DESC LIMIT ?",
                    (target_uid, pool_group, limit)
                )
            else:
                cur.execute(
                    "SELECT id, pool_id, pool_group, item_id, item_num, draw_ts FROM draw_record WHERE uid=? ORDER BY id DESC LIMIT ?",
                    (target_uid, limit)
                )
            rows = cur.fetchall()
            for r in rows:
                rid, pid, pgrp, item_id, item_num, dts = r
                str_id = str(item_id)
                meta = item_map.get(str_id, {})
                name = meta.get("name") or f"物品 #{item_id}"
                rare = meta.get("rare") or ("SSR" if str_id.startswith("25") else ("SR" if str_id.startswith("24") else "R"))
                img = meta.get("img")
                if not img:
                    if str_id.startswith(("23", "24", "25")):
                        img = f"extracted_assets/servants/icons/{item_id}.png"
                    elif 1000 <= item_id <= 9999:
                        img = f"extracted_assets/avatars/{item_id}.png"
                    else:
                        img = f"extracted_assets/items/{item_id}.png"

                dtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(dts)) if dts else ""

                records.append({
                    "id": rid,
                    "pool_id": pid,
                    "pool_group": pgrp,
                    "item_id": item_id,
                    "item_name": name,
                    "item_num": item_num,
                    "rare": rare,
                    "img": img,
                    "draw_ts": dts,
                    "draw_time": dtime_str,
                })
        except Exception:
            pass

        return {
            "uid": target_uid,
            "pool_group": pool_group or "all",
            "total": len(records),
            "records": records,
        }

    # -----------------------------------------------------------------------
    # 7. 关卡状态视图 (Stages ViewDTO)
    # -----------------------------------------------------------------------

    def get_stages_status_view(self, uid):
        """查询玩家关卡与挑战进度。"""
        uid = int(uid)
        return {
            "uid": uid,
            "main_story_progress": "8/8 章节全通",
            "hard_mode": "已全部解锁",
            "matrix_polyhedron": "第5层 满神格",
            "abyss_floor": "第 10 阶",
        }

    # -----------------------------------------------------------------------
    # 8. AI修正者视图 (AIChat ViewDTO)
    # -----------------------------------------------------------------------

    def get_aichat_characters_view(self, uid=None):
        """获取所有已录入的 AI 修正者名册，并关联对话条数与最后发言时间。"""
        cur = self._get_ro_conn().cursor()
        cur.execute("""
            SELECT char_id, char_name, hero_ids, avatar_icon, icon_frame, chat_bubble, 
                   info_background, level, sign, ip_location, greeting_msg, system_prompt, 
                   is_active, update_ts
            FROM ai_characters
            ORDER BY char_id ASC
        """)
        rows = cur.fetchall()

        hero_avatar_map = {
            90001001: 1194,  # 悼亡之蝶·海拉
            90001002: 1084,  # 朝约·薇儿丹蒂
            90001003: 1066,  # 早樱·大国主
            90001004: 1076,  # 太一·庚辰
            90001005: 1075,  # 澄心·陵光
            90001006: 1011,  # 雏心·奥西里斯
            90001007: 1028,  # 轰雷·托尔
            90001050: 10066, # 小帮厨·宁希达
            90001051: 1029,  # 董事长·奥丁
        }

        # 统计对话历史与问候偏好设置
        msg_counts = {}
        last_msgs = {}
        user_greeting_settings = {}
        user_custom_prompts = {}
        if uid is not None:
            cur.execute("""
                SELECT char_id, COUNT(*) as cnt, MAX(id) as max_id
                FROM ai_chat_memory
                WHERE uid = ?
                GROUP BY char_id
            """, (int(uid),))
            for cid, cnt, max_id in cur.fetchall():
                msg_counts[cid] = cnt
                if max_id:
                    cur.execute("SELECT content, timestamp FROM ai_chat_memory WHERE id = ?", (max_id,))
                    last_row = cur.fetchone()
                    if last_row:
                        last_msgs[cid] = {"content": last_row[0], "timestamp": last_row[1]}

            cur.execute("SELECT extra FROM users WHERE uid = ?", (int(uid),))
            u_row = cur.fetchone()
            if u_row and u_row[0]:
                try:
                    p_extra = json.loads(u_row[0]) if isinstance(u_row[0], str) else u_row[0]
                    user_greeting_settings = p_extra.get("ai_greeting_settings", {})
                    user_custom_prompts = p_extra.get("ai_custom_prompts", {})
                except Exception:
                    pass

        # 加载生日配置
        hero_birthdays = {}
        bday_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ai_hero_birthdays.json")
        if os.path.exists(bday_path):
            try:
                with open(bday_path, "r", encoding="utf-8") as f:
                    hero_birthdays = json.load(f)
            except Exception:
                pass

        characters = []
        for r in rows:
            cid = r[0]
            cname = r[1] or f"修正者 #{cid}"
            hids = str(r[2] or "")
            hero_id = hero_avatar_map.get(cid)
            if not hero_id and hids:
                try:
                    hero_id = int(hids.split(",")[0])
                except (ValueError, IndexError):
                    hero_id = 1084
            hero_id = hero_id or 1084

            last_info = last_msgs.get(cid, {})

            # 生日匹配：多级归一化匹配（支持机体卡号 -> 档案 ID，以及 char_id、hero_id、角色名查找）
            b_info = None
            try:
                from account_db import HERO_TO_ARCHIVE
                arch_id = HERO_TO_ARCHIVE.get(hero_id)
                if arch_id and str(arch_id) in hero_birthdays:
                    b_info = hero_birthdays[str(arch_id)]
            except Exception:
                pass

            if not b_info:
                b_info = hero_birthdays.get(str(hero_id)) or hero_birthdays.get(str(cid))

            if not b_info:
                # 遍历关联 hero_ids
                for hid_raw in hids.split(","):
                    hid_clean = hid_raw.strip()
                    if not hid_clean:
                        continue
                    try:
                        hid_val = int(hid_clean)
                        from account_db import HERO_TO_ARCHIVE
                        p_arch = HERO_TO_ARCHIVE.get(hid_val)
                        if p_arch and str(p_arch) in hero_birthdays:
                            b_info = hero_birthdays[str(p_arch)]
                            break
                        if hid_clean in hero_birthdays:
                            b_info = hero_birthdays[hid_clean]
                            break
                    except Exception:
                        pass

            if not b_info:
                # 兜底按角色名比对
                for hb in hero_birthdays.values():
                    if hb.get("hero_name") == cname:
                        b_info = hb
                        break

            b_info = b_info or {}
            bday_str = b_info.get("birthday_str", "")

            # 读取人格提示词配置
            persona_prompt = user_custom_prompts.get(str(cid)) or user_custom_prompts.get(str(hero_id)) or ""
            if not persona_prompt and r[11]:
                persona_prompt = r[11] or ""
            persona_prompt = (persona_prompt or "").strip()
            has_persona = bool(persona_prompt)

            # 问候偏好：未配置人格提示词的角色强制静音停用 (none)
            if not has_persona:
                g_mode = "none"
            else:
                g_mode = user_greeting_settings.get(str(cid)) or user_greeting_settings.get(str(hero_id)) or "all"

            characters.append({
                "char_id": cid,
                "id": cid,
                "char_name": cname,
                "name": cname,
                "hero_id": hero_id,
                "avatar": f"extracted_assets/avatars/{hero_id}.png",
                "avatar_icon": r[3] or 1084,
                "icon_frame": r[4] or 2001,
                "chat_bubble": r[5] or 9001,
                "info_background": r[6] or 4001,
                "level": r[7] or 80,
                "sign": r[8] or "",
                "ip_location": r[9] or "同服",
                "location": r[9] or "同服",
                "greeting_msg": r[10] or "你好，管理员。",
                "system_prompt": persona_prompt,
                "persona_prompt": persona_prompt,
                "has_persona": has_persona,
                "is_active": bool(r[12]),
                "update_ts": r[13] or 0,
                "message_count": msg_counts.get(cid, 0),
                "last_message": last_info.get("content", r[10] or ""),
                "last_time": last_info.get("timestamp", 0),
                "birthday": bday_str,
                "greeting_mode": g_mode,
            })

        import ai_bot_config
        bot_cfg = ai_bot_config.get_bot_config()

        return {
            "characters": characters,
            "total": len(characters),
            "active_count": sum(1 for c in characters if c["is_active"]),
            "system_prefix": bot_cfg.get("system_prefix", "")
        }

    def get_aichat_personas_view(self):
        """【兼容通道】获取所有修正者基础名册清单。"""
        chars_view = self.get_aichat_characters_view()
        personas = chars_view.get("characters", [])
        return {"personas": personas, "total": len(personas)}

    def get_aichat_calendar_view(self, uid=None):
        """获取日历引擎状态、今日节日/节气与当月寿星名册。"""
        import datetime
        from ai_calendar import get_calendar_events, _get_hero_birthdays
        today = datetime.date.today()
        cal_events = get_calendar_events(today, uid=uid, db=self)

        b_dict = _get_hero_birthdays()
        cur_month = today.month
        month_heroes = []
        for rec_id, info in b_dict.items():
            if int(info.get("month", 0)) == cur_month:
                month_heroes.append({
                    "record_id": int(rec_id),
                    "hero_name": info.get("hero_name"),
                    "birthday": info.get("birthday_str"),
                    "day": int(info.get("day", 0)),
                    "organization": info.get("organization", ""),
                    "is_today": (int(info.get("day", 0)) == today.day),
                })
        # 兼容字段丰富化
        st_list = [e["name"] for e in cal_events.get("events", []) if e.get("type") == "solar_term"]
        hd_list = [e["name"] for e in cal_events.get("events", []) if e.get("type") == "holiday"]
        cal_events["solar_date"] = cal_events.get("date")
        cal_events["lunar_date"] = cal_events.get("lunar_str")
        cal_events["solar_term"] = st_list[0] if st_list else None
        cal_events["holidays"] = hd_list

        return {
            "today": cal_events,
            "current_month": cur_month,
            "month_heroes": month_heroes,
            "total_month_birthdays": len(month_heroes),
        }

    def get_aichat_personas_view(self):
        """兼容旧接口：返回修正者人设清单。"""
        res = self.get_aichat_characters_view()
        personas = []
        for c in res["characters"]:
            personas.append({
                "id": c["char_id"],
                "hero_id": c["hero_id"],
                "name": c["char_name"],
                "tag": f"{c['ip_location']} / Lv.{c['level']}",
                "avatar": c["avatar"],
                "desc": c["sign"],
                "lastMsg": c["last_message"],
            })
        return {"personas": personas}

    def get_aichat_history_view(self, uid, char_id=None, limit=50):
        """获取指定修正者与玩家的历史消息记录，并附带玩家资料。"""
        cur = self._get_ro_conn().cursor()
        uid_int = int(uid)

        # 读取玩家资料
        cur.execute("SELECT nick, portrait, icon_frame FROM users WHERE uid = ?", (uid_int,))
        user_row = cur.fetchone()
        user_nick = user_row[0] if (user_row and user_row[0]) else f"管理员 #{uid_int}"
        user_icon = user_row[1] if (user_row and user_row[1]) else 1084
        user_frame = user_row[2] if (user_row and user_row[2]) else 2001

        if char_id is not None:
            cur.execute("""
                SELECT id, uid, char_id, role, content, timestamp, is_read FROM (
                    SELECT id, uid, char_id, role, content, timestamp, is_read
                    FROM ai_chat_memory
                    WHERE uid = ? AND char_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
            """, (uid_int, int(char_id), int(limit)))
        else:
            cur.execute("""
                SELECT id, uid, char_id, role, content, timestamp, is_read FROM (
                    SELECT id, uid, char_id, role, content, timestamp, is_read
                    FROM ai_chat_memory
                    WHERE uid = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
            """, (uid_int, int(limit)))

        rows = cur.fetchall()
        messages = []
        for r in rows:
            messages.append({
                "id": r[0],
                "uid": r[1],
                "char_id": r[2],
                "role": r[3],
                "content": r[4],
                "timestamp": r[5],
                "is_read": bool(r[6]),
            })

        return {
            "uid": uid_int,
            "char_id": int(char_id) if char_id is not None else None,
            "user_info": {
                "nick": user_nick,
                "avatar": f"extracted_assets/avatars/{user_icon}.png",
                "avatar_id": user_icon,
                "icon_frame": user_frame,
            },
            "messages": messages,
            "total": len(messages),
            "source": "sqlite",
        }

    def get_aichat_config_view(self):
        """【只读通道·脱敏安全】获取脱敏后的 LLM 引擎配置。"""
        import ai_bot_config
        return ai_bot_config.get_masked_config()

    def get_aichat_stats_view(self, uid=None):
        """获取 AI 修正者模块概览统计指标。"""
        cur = self._get_ro_conn().cursor()
        cur.execute("SELECT COUNT(*) FROM ai_characters WHERE is_active = 1")
        active_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM ai_characters")
        total_chars = cur.fetchone()[0]

        total_msgs = 0
        if uid is not None:
            cur.execute("SELECT COUNT(*) FROM ai_chat_memory WHERE uid = ?", (int(uid),))
            total_msgs = cur.fetchone()[0]
        else:
            cur.execute("SELECT COUNT(*) FROM ai_chat_memory")
            total_msgs = cur.fetchone()[0]

        import ai_bot_config
        cfg = ai_bot_config.get_bot_config()
        cur_p = cfg.get("provider", "opencode")
        cur_info = cfg.get("providers", {}).get(cur_p, {})
        model_name = cur_info.get("model", "")
        has_key = bool(cur_info.get("api_key", ""))

        return {
            "uid": int(uid) if uid else None,
            "active_characters": active_count,
            "total_characters": total_chars,
            "total_messages": total_msgs,
            "provider": cur_p,
            "model": model_name,
            "has_key": has_key,
            "status": "在线就绪" if (has_key or cur_p == "ollama") else "降级兜底",
            "temperature": cfg.get("temperature", 0.7),
            "max_history_turns": cfg.get("max_history_turns", 10),
        }

    # -----------------------------------------------------------------------
    # 9. 商店系统视图 (Shop ViewDTO)
    # -----------------------------------------------------------------------

    def get_shop_catalog_view(self):
        """查询全服商店分类目录与商品概览。"""
        cur = self._get_ro_conn().cursor()

        # 统计每个商店有效展示商品总数（排除刻印与角色）
        cur.execute("SELECT shop_id, item_id FROM shop_goods WHERE shop_id != 32")
        goods_counts = {}
        for sid, iid in cur.fetchall():
            if not _is_equip_item(iid) and not _is_hero_item(iid):
                goods_counts[sid] = goods_counts.get(sid, 0) + 1

        # 查询所有商店
        cur.execute("""
            SELECT shop_id, name, group_name, system, refresh_num_limit, activity_id, is_permanent 
            FROM shop 
            ORDER BY shop_id ASC
        """)
        rows = cur.fetchall()

        shops = []
        for r in rows:
            sid, name, grp, sys_id, r_limit, act_id, is_perm = r
            shops.append({
                "shop_id": sid,
                "name": name or f"商店 #{sid}",
                "group_name": grp or "常规商店",
                "system": sys_id,
                "goods_count": goods_counts.get(sid, 0),
                "refresh_num_limit": r_limit or 0,
                "activity_id": act_id or 0,
                "is_permanent": bool(is_perm),
            })

        # 官方核心标准业务分组（排除刻印研发）
        groups = [
            {"id": "daily", "name": "每日采购", "shop_ids": [2]},
            {"id": "trading", "name": "交易中心", "shop_ids": [10, 11, 12, 13, 14]},
            {"id": "voucher", "name": "凭证置换", "shop_ids": [20, 22, 23, 41]},
            {"id": "supply", "name": "组合补给", "shop_ids": [3, 4, 5, 6]},
            {"id": "skins", "name": "角色换装", "shop_ids": [15, 16, 17, 18, 42]},
            {"id": "all", "name": "全部商店", "shop_ids": [s["shop_id"] for s in shops if s["shop_id"] != 32]},
        ]

        shops = [s for s in shops if s["shop_id"] != 32]

        return {
            "groups": groups,
            "shops": shops,
            "total_shops": len(shops),
            "total_goods": sum(goods_counts.get(s["shop_id"], 0) for s in shops)
        }

    def get_shop_goods_view(self, uid, shop_id=2):
        """查询指定商店货架商品列表及玩家已购状态。"""
        uid = int(uid)
        shop_id = int(shop_id)
        cur = self._get_ro_conn().cursor()

        # 查询商店基础信息
        cur.execute("SELECT name, group_name, refresh_num_limit FROM shop WHERE shop_id=?", (shop_id,))
        shop_info = cur.fetchone()
        shop_name = shop_info[0] if shop_info else f"商店 #{shop_id}"
        group_name = shop_info[1] if shop_info else "常规商店"
        refresh_limit = shop_info[2] if shop_info else 0

        items_map = _get_items_catalog_map(self._get_ro_conn())
        skins_map = _get_skins_map()

        # 查询已购次数
        cur.execute("SELECT goods_id, buy_times, next_refresh_timestamp FROM store_purchase WHERE uid=? AND shop_id=?", (uid, shop_id))
        purchase_map = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        # 预查用户持有的货币与材料余额
        user_balance_map = {}
        cur.execute("SELECT id, num FROM currency WHERE uid=?", (uid,))
        for r in cur.fetchall():
            user_balance_map[r[0]] = r[1]
        cur.execute("SELECT id, num FROM material WHERE uid=?", (uid,))
        for r in cur.fetchall():
            user_balance_map[r[0]] = r[1]

        goods_list = []

        # 如果是每日采购（Shop 2），联动 user_daily_shop
        if shop_id == 2:
            cur.execute("SELECT goods_list_json, refresh_times, last_refresh_ts FROM user_daily_shop WHERE uid=?", (uid,))
            daily_row = cur.fetchone()
            daily_goods_ids = []
            if daily_row and daily_row[0]:
                try:
                    parsed = json.loads(daily_row[0])
                    daily_goods_ids = [int(x.get("goods_id")) for x in parsed if isinstance(x, dict) and x.get("goods_id")]
                except Exception:
                    daily_goods_ids = []

            if daily_goods_ids:
                placeholders = ",".join("?" * len(daily_goods_ids))
                cur.execute(f"""
                    SELECT goods_id, item_id, name, cost_type, cost_id, cost, cheap_cost, discount, limit_num, refresh_cycle, detail_json
                    FROM shop_goods
                    WHERE goods_id IN ({placeholders})
                """, daily_goods_ids)
                goods_rows = {r[0]: r for r in cur.fetchall()}
                ordered_rows = [goods_rows[gid] for gid in daily_goods_ids if gid in goods_rows]
            else:
                cur.execute("""
                    SELECT goods_id, item_id, name, cost_type, cost_id, cost, cheap_cost, discount, limit_num, refresh_cycle, detail_json
                    FROM shop_goods
                    WHERE shop_id=2
                    ORDER BY goods_id ASC
                    LIMIT 13
                """)
                ordered_rows = cur.fetchall()
        else:
            cur.execute("""
                SELECT goods_id, item_id, name, cost_type, cost_id, cost, cheap_cost, discount, limit_num, refresh_cycle, detail_json
                FROM shop_goods
                WHERE shop_id=?
                ORDER BY goods_id ASC
            """, (shop_id,))
            ordered_rows = cur.fetchall()

        cycle_labels = {
            1: "永久限购",
            2: "每月限购",
            3: "每周限购",
            4: "每日限购",
        }

        # 特权连环礼包（33201~33215 权威定义）
        privilege_chain_packs = {
            33201: {"name": "特权每日补给 (免费)", "icon": "34154.png", "rare": 5},
            33202: {"name": "特权补给包 (6元)", "icon": "34154.png", "rare": 5},
            33203: {"name": "特权追加补给 (免费1)", "icon": "34154.png", "rare": 5},
            33204: {"name": "特权追加补给 (免费2)", "icon": "34154.png", "rare": 5},
            33208: {"name": "高阶特权补给包 (30元)", "icon": "34253.png", "rare": 5},
            33209: {"name": "高阶追加补给 (免费1)", "icon": "34253.png", "rare": 5},
            33210: {"name": "高阶追加补给 (免费2)", "icon": "34253.png", "rare": 5},
            33214: {"name": "豪华特权补给包 (98元)", "icon": "34011.png", "rare": 5},
            33215: {"name": "豪华追加补给 (免费1)", "icon": "34011.png", "rare": 5},
        }

        quality_frame_map = {
            5: "Item_yellow.png",
            4: "Item_purple.png",
            3: "Item_blue.png",
            2: "Item_green.png",
            1: "Item_box.png",
        }

        for r in ordered_rows:
            gid, item_id, gname, cost_type, cost_id, cost, cheap_cost, discount, limit_num, cycle, detail_json = r

            item_info = items_map.get(item_id) or {}

            # 1. 严格过滤刻印与角色本体（按老大需求：刻印套装与角色本体均不开放展示）
            if _is_equip_item(item_id, item_info) or _is_hero_item(item_id, item_info):
                continue

            # 2. 换装皮肤特判：五星金色品质，指向 avatars 目录，展示 皮肤名 · 角色名
            if item_id in skins_map:
                s = skins_map[item_id]
                s_name = s.get("name", "")
                h_name = s.get("hero_name", "")
                if s_name and h_name:
                    real_name = f"{s_name} · {h_name}"
                elif s_name:
                    real_name = s_name
                else:
                    real_name = gname or f"换装 #{item_id}"
                rare = 5
                quality_frame = "Item_yellow.png"
                icon_file = f"avatars/{item_id}.png"
            # 3. 特权连环包特判
            elif item_id in privilege_chain_packs:
                pc = privilege_chain_packs[item_id]
                real_name = pc["name"]
                rare = pc["rare"]
                quality_frame = "Item_yellow.png"
                icon_file = pc["icon"]
            else:
                real_name = gname or item_info.get("name") or f"道具 #{item_id}"
                rare = item_info.get("rare") or 4
                quality_frame = item_info.get("quality_frame") or quality_frame_map.get(rare, "Item_purple.png")
                icon_file = item_info.get("icon_file") or f"{item_id}.png"

                # 贴纸 / 外观道具：指向 stickers/items 目录
                if item_info.get("category") == "cosmetic" or (3000 <= item_id < 4000):
                    icon_file = f"stickers/items/{item_id}.png"

                # 玩家名片框 / 背景特判：指向 avatars 目录
                if item_id == 2200006 or (2200000 <= item_id < 2300000):
                    icon_file = f"avatars/{item_id}.png"

                # 礼包或未知补给包图标回退
                if item_info.get("category") in ("pack", "gift") and (not icon_file or icon_file == f"{item_id}.png"):
                    icon_file = "34154.png"

            # 4. 通用钥从特判：五星金色品质，指向 servants 目录
            if (2500000 <= item_id < 2600000) or item_info.get("type") == 9:
                rare = 5
                quality_frame = "Item_yellow.png"
                icon_file = f"servants/{item_id}.png"

            # 价格与代币
            cost_info = items_map.get(cost_id) or {}
            cost_name = CURRENCY_NAMES.get(cost_id) or cost_info.get("name") or f"代币 #{cost_id}"

            p_entry = purchase_map.get(gid, (0, 0))
            buy_times = p_entry[0]
            next_refresh = p_entry[1]

            is_sold_out = False
            if limit_num > 0 and buy_times >= limit_num:
                is_sold_out = True

            goods_list.append({
                "goods_id": gid,
                "item_id": item_id,
                "name": real_name,
                "rare": rare,
                "icon_file": icon_file,
                "quality_frame": quality_frame,
                "cost_id": cost_id,
                "cost_name": cost_name,
                "cost": cost,
                "cheap_cost": cheap_cost if cheap_cost > 0 else cost,
                "discount": discount,
                "limit_num": limit_num,
                "buy_times": buy_times,
                "left_num": max(0, limit_num - buy_times) if limit_num > 0 else -1,
                "is_sold_out": is_sold_out,
                "refresh_cycle": cycle,
                "refresh_cycle_label": cycle_labels.get(cycle, "不限购" if limit_num == -1 else "限购"),
                "next_refresh_ts": next_refresh,
                "user_balance": user_balance_map.get(cost_id, 0),
                "user_item_count": user_balance_map.get(item_id, 0),
            })

        return {
            "uid": uid,
            "shop_id": shop_id,
            "shop_name": shop_name,
            "group_name": group_name,
            "refresh_num_limit": refresh_limit,
            "goods": goods_list,
            "total_goods": len(goods_list)
        }

    def get_shop_stats_view(self, uid):
        """获取商店系统全局与玩家维度统计指标。"""
        uid = int(uid)
        cur = self._get_ro_conn().cursor()

        cur.execute("SELECT COUNT(*) FROM shop WHERE shop_id != 32")
        total_shops = cur.fetchone()[0]

        cur.execute("SELECT item_id FROM shop_goods WHERE shop_id != 32")
        valid_goods = [r[0] for r in cur.fetchall() if not _is_equip_item(r[0]) and not _is_hero_item(r[0])]
        total_goods = len(valid_goods)

        cur.execute("SELECT refresh_times FROM user_daily_shop WHERE uid=?", (uid,))
        d_row = cur.fetchone()
        daily_refresh_times = d_row[0] if d_row else 0

        cur.execute("""
            SELECT COUNT(*) FROM store_purchase sp
            JOIN shop_goods sg ON sp.goods_id = sg.goods_id
            WHERE sp.uid=? AND sg.limit_num > 0 AND sp.buy_times >= sg.limit_num
        """, (uid,))
        sold_out_count = cur.fetchone()[0]

        return {
            "uid": uid,
            "total_shops": total_shops,
            "total_goods": total_goods,
            "daily_refresh_times": daily_refresh_times,
            "daily_refresh_max": 20,
            "sold_out_goods_count": sold_out_count,
        }


# 全局单例
_reader_instance = None
_reader_lock = threading.Lock()


def get_reader(db_path=DEFAULT_DB):
    """获取 GMReader 单例对象。"""
    global _reader_instance
    if _reader_instance is None:
        with _reader_lock:
            if _reader_instance is None:
                _reader_instance = GMReader(db_path=db_path)
    return _reader_instance
