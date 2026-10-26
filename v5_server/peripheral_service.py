# -*- coding: utf-8 -*-
"""
peripheral_service.py — 外围系统与个性化领域服务模块（玩家装扮 / 大厅场景 / 贴纸画册 / 偏好设置 / 誓约联动）

[2026-09-07 领域充血重构] 定位：
从初期的“散落协议收纳合集”全面蜕变升级为架构规范、业务充血、数据闭环的工业级领域服务。
集中纳管玩家名片社交形象、主界面大厅与看板娘实时交互、贴纸拼贴画册与套装奖励、账号偏好及局内云端配置。

四大核心业务领域：
1. 名片与个性化装扮域（Profile & Customization）：
   - 头像(32032)、头像框(32034)、名片背景(32114)、聊天气泡(32120)、称号标签(32116)、个性签名(32012)、点赞(32118)、名片展台(32014)；
   - 严格的资产持有性校验（406 拦截），支持初始默认装扮白名单；
   - 与 MomoTalk 模块明确边界（MomoTalk 专精 91016/cur_momotalk_frame，名片专精 32034/cur_icon_frame，气泡数据互通共享）。
2. 大厅场景、看板娘与誓约联动域（Hall & Poster Girl & Oath）：
   - 静态主看板娘设置(32016)与大厅场景(32108)、BGM(12026/12029)；
   - 多看板娘/皮肤轮换大厅实时角色捕获(32132 上报拦截)，准确映射 Skin->Hero；
   - 触摸看板娘(32054)：精准识别被触摸角色，维护总触摸与每日触摸两层计数，向誓约模块发射带参 MAIN_INTERACT 广播，驱动 410000 任务；
   - 陪伴累计时长统计(board_hero_seconds)，对齐 DAILY_ASSISTANT (410002) 誓约任务；
   - 纯白模式(32068)、桌面组件布置(32070)、随机展示族(32122~32134)。
3. 贴纸系统与套装奖励域（Sticker Canvas & Suit Rewards）：
   - 贴纸拼贴画册排版持久化(32038 上报拦截)，保存多层贴纸坐标/旋转/缩放/层级；
   - 贴纸展示页切换(32056)；
   - 贴纸套装奖励领取(32058->32059)，原子发放奖励并记录已领列表。
4. 账号基础属性与局内云端配置域（Account & Cloud Settings）：
   - 改名(23012)、生日(12030)、文本语言(12100)；
   - 局内操作按键布局(battle_ui_setting)与画质清晰度(graphic_setting)的云端归档存取方案。

登录洪流全数据闭环：
- sc_32009 玩家资料卡：全字段真值注入（看板娘、当前头像/框/背景/气泡、签名、展示英雄、贴纸画册排版、已领套装、纯白桌面）；
- sc_12029 大厅BGM：动态读取 game_user.bgm_id。
"""

import json
import time
import logging
import os

from core import Operation, OperationError, operation
from middleware import DownFrame
from event_bus import bus, Events

logger = logging.getLogger("peripheral_service")

# 缓存 skin -> hero 映射表
_SKIN_HERO_MAP = None


def _get_skin_hero_map():
    global _SKIN_HERO_MAP
    if _SKIN_HERO_MAP is None:
        map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_hero_map.json")
        if os.path.exists(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    _SKIN_HERO_MAP = json.load(f)
            except Exception as e:
                logger.warning(f"加载 skin_hero_map.json 失败: {e}")
                _SKIN_HERO_MAP = {}
        else:
            _SKIN_HERO_MAP = {}
    return _SKIN_HERO_MAP


# 缓存 skin -> scene 伴生大厅场景映射表
_SKIN_SCENE_MAP = None


def _get_skin_scene_map():
    global _SKIN_SCENE_MAP
    if _SKIN_SCENE_MAP is None:
        map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_scene_map.json")
        if os.path.exists(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    _SKIN_SCENE_MAP = json.load(f)
            except Exception as e:
                logger.warning(f"加载 skin_scene_map.json 失败: {e}")
                _SKIN_SCENE_MAP = {}
        else:
            _SKIN_SCENE_MAP = {}
    return _SKIN_SCENE_MAP


# 缓存装扮配置表（来自客户端 ItemCfg 导出的真实合法类型）
_DECORATIONS_CATALOG = None


def _get_decorations_catalog():
    global _DECORATIONS_CATALOG
    if _DECORATIONS_CATALOG is None:
        cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decorations_catalog.json")
        if os.path.exists(cat_path):
            try:
                with open(cat_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    _DECORATIONS_CATALOG = {
                        "portraits": {int(k) for k in raw.get("PORTRAIT", {}).keys()},
                        "frames": {int(k) for k in raw.get("FRAME", {}).keys()},
                        "card_bgs": {int(k) for k in raw.get("CARD_BG", {}).keys()},
                        "bubbles": {int(k) for k in raw.get("CHAT_BUBBLE", {}).keys()},
                        "tags": {int(k) for k in raw.get("TAG", {}).keys()},
                        "scenes": {int(k) for k in raw.get("SCENE", {}).keys()},
                        "stickers": {int(k) for k in raw.get("STICKER", {}).keys()},
                        "sticker_bgs": {int(k) for k in raw.get("STICKER_BG", {}).keys()},
                        "sticker_fgs": {int(k) for k in raw.get("STICKER_FG", {}).keys()},
                    }
            except Exception as e:
                logger.warning(f"加载 decorations_catalog.json 失败: {e}")
                _DECORATIONS_CATALOG = {}
        else:
            _DECORATIONS_CATALOG = {}
    return _DECORATIONS_CATALOG


def skin_to_hero_id(skin_or_hero_id):
    """根据皮肤 ID 或英雄 ID 解析出英雄原型 ID。"""
    sid = int(skin_or_hero_id or 0)
    if sid <= 0:
        return 0
    m = _get_skin_hero_map()
    str_sid = str(sid)
    if str_sid in m:
        return int(m[str_sid])
    # 规则兜底：如果是 10xx 形式本身就是 hero_id；如果是 10xxxx，截取前 4 位
    if sid >= 100000:
        candidate = int(str_sid[:4])
        return candidate
    return sid


# ======================================================================
# 内部数据读写工具
# ======================================================================

def _load_user_extra(db, uid):
    """读 users.extra JSON。"""
    if not db:
        return {}
    row = db.query("SELECT extra FROM users WHERE uid=?", (uid,))
    try:
        extra = json.loads((row[0]["extra"] if row else None) or "{}")
    except Exception:
        extra = {}
    return extra if isinstance(extra, dict) else {}


def _save_user_extra(db, uid, extra):
    if db:
        db.execute("UPDATE users SET extra=? WHERE uid=?",
                   (json.dumps(extra, ensure_ascii=False), uid))


def _safe_upsert(db, table, uid, row, keys=("uid",)):
    """安全 upsert：兼容真实 AccountDB 及 MockDB。"""
    if db and hasattr(db, "upsert"):
        try:
            db.upsert(table, uid, row, keys=keys)
        except Exception as e:
            logger.warning(f"[_safe_upsert] {table} uid={uid} failed: {e}")
    elif db and hasattr(db, "execute"):
        cols = list(row.keys())
        set_clause = ", ".join(f"{c}=?" for c in cols)
        vals = [row[c] for c in cols]
        try:
            db.execute(f"UPDATE {table} SET {set_clause} WHERE uid=?", (*vals, uid))
        except Exception:
            pass


def _as_id_list(value):
    """转换 proto 单值/列表两可字段为整型列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(value)]


# ======================================================================
# 领域服务：PeripheralService 单例
# ======================================================================

class PeripheralService:
    """外围与个性化系统领域服务（单例）。"""

    _instance = None

    # 默认基础装扮白名单（必须为 ItemCfg 中真实对应 type 的默认道具 ID，杜绝非装扮道具 ID 导致客户端崩溃）
    # 2110841: 默认头像（对应 1084 薇儿丹蒂，ItemCfg type=11，gamesetting.profile_avatar_default）
    DEFAULT_PORTRAITS = {2110841, 2110111, 2110121, 2110131, 2110151, 2110161, 2110171}
    DEFAULT_FRAMES = {2001}       # 默认头像框 (type=12)
    DEFAULT_BUBBLES = {9001}      # 默认聊天气泡 (type=26)
    DEFAULT_CARD_BGS = {8001}     # 初始名片背景 (type=23)
    DEFAULT_SCENES = {6000}       # 综合科办公室 (type=21)
    DEFAULT_TAGS = {7001, 7002, 7003, 7004, 7005, 7006, 7007, 7008}  # 初始解锁称号标签 (type=24)
    DEFAULT_STICKER_BGS = {4002}  # 默认贴纸背景底板 (type=28)

    @classmethod
    def get_valid_decorations(cls, category):
        """获取指定装扮类型的有效 ID 集合（从 decorations_catalog.json 解析）"""
        cat = _get_decorations_catalog()
        return cat.get(category, set())

    def __init__(self, db=None):
        self.db = db

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    # ------------------------------------------------------------------
    # 一、名片装扮与社交形象域 (Profile & Customization)
    # ------------------------------------------------------------------

    def check_asset_ownership(self, uid, kind, item_id):
        """检查装扮资产持有性。默认资产直接放行，非默认资产要求 player_card.obtained == 1。"""
        item_id = int(item_id or 0)
        if item_id == 0:
            return True
        if kind == "portrait" and item_id in self.DEFAULT_PORTRAITS:
            return True
        if kind == "icon_frame" and item_id in self.DEFAULT_FRAMES:
            return True
        if kind == "bubble" and item_id in self.DEFAULT_BUBBLES:
            return True
        if kind == "card_bg" and item_id in self.DEFAULT_CARD_BGS:
            return True
        if kind in ("tag", "title") and item_id in self.DEFAULT_TAGS:
            return True
        if kind == "sticker_bg" and item_id in self.DEFAULT_STICKER_BGS:
            return True
        if kind == "scene" and item_id in self.DEFAULT_SCENES:
            return True

        if not self.db:
            return True

        if kind == "scene":
            return any(row['id'] == item_id for row in self.get_scene_entries(uid))

        db_kind = "title" if kind == "tag" else kind
        rows = self.db.query(
            "SELECT obtained FROM player_card WHERE uid=? AND kind=? AND item_id=?",
            (uid, db_kind, item_id)
        )
        if rows and int(rows[0].get("obtained") or 0) == 1:
            return True
        return False

    def change_portrait(self, uid, icon_id, ctx=None):
        """更换头像：cs_32032 {icon_id}。"""
        icon_id = int(icon_id or 0)
        if not self.check_asset_ownership(uid, "portrait", icon_id):
            raise OperationError(406, "未获得该头像")
        if self.db:
            _safe_upsert(self.db, "game_user", uid, {"cur_portrait": icon_id})
            try:
                self.db.execute("UPDATE users SET portrait=? WHERE uid=?", (icon_id, uid))
            except Exception as e:
                logger.warning(f"同步 users.portrait 异常: {e}")
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="portrait", value=icon_id)
        return icon_id

    def change_frame(self, uid, frame_id, ctx=None):
        """更换头像框：cs_32034 {iconframe_id}。"""
        frame_id = int(frame_id or 0)
        if not self.check_asset_ownership(uid, "icon_frame", frame_id):
            raise OperationError(406, "未获得该头像框")
        if self.db:
            _safe_upsert(self.db, "game_user", uid, {"cur_icon_frame": frame_id})
            try:
                self.db.execute("UPDATE users SET icon_frame=? WHERE uid=?", (frame_id, uid))
            except Exception as e:
                logger.warning(f"同步 users.icon_frame 异常: {e}")
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="icon_frame", value=frame_id)
        return frame_id

    def change_card_bg(self, uid, bg_id, ctx=None):
        """更换名片背景：cs_32114 {id}。"""
        bg_id = int(bg_id or 0)
        if not self.check_asset_ownership(uid, "card_bg", bg_id):
            raise OperationError(406, "未获得该名片背景")
        if self.db:
            _safe_upsert(self.db, "game_user", uid, {"cur_card_bg": bg_id})
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="card_bg", value=bg_id)
        return bg_id

    def change_chat_bubble(self, uid, bubble_id, ctx=None):
        """更换聊天气泡/聊天框：cs_32120 {chat_bubble}。"""
        bubble_id = int(bubble_id or 0)
        if not self.check_asset_ownership(uid, "bubble", bubble_id):
            raise OperationError(406, "未获得该聊天气泡")
        if self.db:
            _safe_upsert(self.db, "game_user", uid, {"cur_bubble": bubble_id})
            if hasattr(self.db, "set_chat_bubble"):
                self.db.set_chat_bubble(uid, bubble_id)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="bubble", value=bubble_id)
        return bubble_id

    def _profile_hero_owned(self, uid, hero_id):
        if type(hero_id) is not int or hero_id <= 0:
            return False
        if hero_id == 1084:  # Existing initial-character policy.
            return True
        rows = self.db.query("SELECT unlock FROM hero WHERE uid=? AND id=?", (uid, hero_id))
        return bool(rows and rows[0].get("unlock") == 1)

    def change_show_heroes(self, uid, heroes, ctx=None):
        from sticker_wall_rules import read_extra, StickerWallError
        hero_list = [] if heroes is None else heroes
        if (not isinstance(hero_list, list) or len(hero_list) > 3
                or any(type(h) is not int or h <= 0 for h in hero_list)
                or len(set(hero_list)) != len(hero_list)):
            raise OperationError(2, "名片展示需为最多3位不同角色")
        try:
            with self.db.transaction():
                read_extra(self.db, uid)
                if any(not self._profile_hero_owned(uid, hid) for hid in hero_list):
                    raise OperationError(406, "尚未解锁展示角色")
                cursor = self.db.execute(
                    "INSERT INTO game_user(uid,show_hero) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET show_hero=excluded.show_hero",
                    (uid, json.dumps(hero_list)))
                if cursor.rowcount != 1:
                    raise OperationError(406, "展示角色未保存")
                bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="show_hero", value=hero_list)
        except StickerWallError as exc:
            raise OperationError(exc.code, str(exc)) from exc
        return hero_list

    def get_show_heroes(self, uid, db=None):
        db = db if db is not None else self.db
        rows = db.query("SELECT show_hero FROM game_user WHERE uid=?", (uid,))
        if not rows:
            return [1084]
        heroes = json.loads(rows[0].get("show_hero") or "[]")
        if not isinstance(heroes, list) or any(type(h) is not int for h in heroes):
            raise ValueError("历史名片角色格式无效")
        return list(dict.fromkeys(h for h in heroes if self._profile_hero_owned(uid, h)))[:3]

    def change_tags(self, uid, tags, ctx=None):
        """更换个性标签/称号：cs_32116 {tags[]}。客户端最多允许设置3个。"""
        tag_list = _as_id_list(tags)
        if len(tag_list) > 3:
            tag_list = tag_list[:3]
        valid_tags = self.get_valid_decorations("tags")
        for tid in tag_list:
            if not self.check_asset_ownership(uid, "tag", tid):
                raise OperationError(406, f"未获得该称号标签: {tid}")
            if valid_tags and tid not in valid_tags:
                raise OperationError(406, f"非法称号标签: {tid}")
        extra = _load_user_extra(self.db, uid)
        extra["profile_tags"] = tag_list
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="tags", value=tag_list)
        return tag_list

    def change_sign(self, uid, sign, ctx=None):
        """Save the primary signature and its legacy mirror atomically."""
        from sticker_wall_rules import read_extra, StickerWallError
        sign_str = str(sign or "").strip()
        try:
            with self.db.transaction():
                extra = read_extra(self.db, uid)
                extra["profile_sign"] = sign_str
                cursor = self.db.execute("UPDATE users SET extra=? WHERE uid=?", (json.dumps(extra, ensure_ascii=False), uid))
                if cursor.rowcount != 1:
                    raise StickerWallError("账号不存在或不唯一", 406)
                cursor = self.db.execute("UPDATE users SET sign=? WHERE uid=?", (sign_str, uid))
                if cursor.rowcount != 1:
                    raise StickerWallError("签名未保存", 406)
                bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="sign", value=sign_str)
        except StickerWallError as exc:
            raise OperationError(exc.code, str(exc)) from exc
        return sign_str

    def get_profile_signature(self, uid, db=None):
        from profile_rules import signature_value
        from sticker_wall_rules import read_extra
        db = db if db is not None else self.db
        rows = db.query("SELECT sign FROM users WHERE uid=?", (uid,))
        if len(rows) != 1:
            raise OperationError(406, "账号不存在或不唯一")
        extra = read_extra(db, uid) if rows[0].get("sign") is None else {}
        return signature_value(rows[0], extra)

    def send_like(self, uid, target_uid, source=0, activity_id=0, ctx=None):
        """
        点赞：cs_32118。
        - 被赞者（target_uid）累加收赞总数（users.likes 与 extra.profile_likes）；
        - 送赞者（uid）记录今日已点赞玩家列表（extra.today_send_like），支持跨天重置。
        """
        target_uid = int(target_uid or 0)
        today_str = time.strftime("%Y-%m-%d")

        # 1. 维护发起者（uid）的今日已点赞 UID 列表
        extra = _load_user_extra(self.db, uid)
        last_date = extra.get("last_like_date")
        today_likes = extra.get("today_send_like")
        if last_date != today_str or not isinstance(today_likes, list):
            today_likes = []

        if target_uid > 0 and target_uid not in today_likes:
            today_likes.append(target_uid)

        extra["today_send_like"] = today_likes
        extra["last_like_date"] = today_str
        _save_user_extra(self.db, uid, extra)

        # 2. 为目标玩家累加点赞数
        target_likes = 0
        if self.db and target_uid > 0 and target_uid != uid:
            try:
                self.db.execute("UPDATE users SET likes = COALESCE(likes, 0) + 1 WHERE uid=?", (target_uid,))
                target_extra = _load_user_extra(self.db, target_uid)
                target_likes = int(target_extra.get("profile_likes") or 0) + 1
                target_extra["profile_likes"] = target_likes
                _save_user_extra(self.db, target_uid, target_extra)
            except Exception as e:
                logger.warning(f"为目标玩家 {target_uid} 累加点赞异常: {e}")
        elif target_uid == uid or not self.db:
            # 自我点赞或单测无库情况
            cur = int(extra.get("profile_likes") or 0) + 1
            extra["profile_likes"] = cur
            _save_user_extra(self.db, uid, extra)
            target_likes = cur

        val = {"likes": target_likes, "target_uid": target_uid, "source": source, "activity_id": activity_id}
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="likes", value=val)
        return val

    def unlock_acquisition_portrait(self, uid, skin_id, ctx=None):
        """Persist the portrait attached to an acquired base/alternative skin."""
        from profile_rules import skin_portrait_config
        info = skin_portrait_config().get(str(int(skin_id)))
        if not info or not int(info.get('portrait_id') or 0):
            return 0
        portrait_id = int(info['portrait_id'])
        self.db.upsert('player_card', uid, dict(kind='portrait', item_id=portrait_id, obtained=1),
                       keys=('uid', 'kind', 'item_id'))
        if getattr(ctx, 'touched_items', None) is not None:
            ctx.touched_items.add(portrait_id)
        return portrait_id

    def query_portraits(self, uid):
        """查询头像持有列表：cs_32062。"""
        valid_set = self.get_valid_decorations("portraits")
        if not self.db:
            return [{"id": hid, "num": 1, "time_valid": 0} for hid in self.DEFAULT_PORTRAITS]
        rows = self.db.query("SELECT item_id FROM player_card WHERE uid=? AND kind='portrait' AND obtained=1", (uid,))
        res = []
        for r in (rows or []):
            iid = int(r["item_id"])
            if not valid_set or iid in valid_set:
                res.append({"id": iid, "num": 1, "time_valid": 0})
        existing_ids = {item["id"] for item in res}
        for hid in self.DEFAULT_PORTRAITS:
            if hid not in existing_ids:
                res.append({"id": hid, "num": 1, "time_valid": 0})
        return res

    def query_bubbles(self, uid):
        """查询气泡持有列表：cs_32066。"""
        valid_set = self.get_valid_decorations("bubbles")
        if not self.db:
            return [{"id": bid, "num": 1, "time_valid": 0} for bid in self.DEFAULT_BUBBLES]
        rows = self.db.query("SELECT item_id FROM player_card WHERE uid=? AND kind='bubble' AND obtained=1", (uid,))
        res = []
        for r in (rows or []):
            iid = int(r["item_id"])
            if not valid_set or iid in valid_set:
                res.append({"id": iid, "num": 1, "time_valid": 0})
        existing_ids = {item["id"] for item in res}
        for bid in self.DEFAULT_BUBBLES:
            if bid not in existing_ids:
                res.append({"id": bid, "num": 1, "time_valid": 0})
        return res

    def get_simple_foreign_info(self, user_id_list):
        """
        批量简要名片信息查询：cs_32036 -> sc_32037。
        针对获赞记录、好友列表等场景，批量提供玩家基础名片属性。
        若玩家在数据库中存在，读取真实昵称、等级、当前头像及头像框；
        若玩家为模板历史遗留或外部 ID（如 2148952675），
        提供合法的观测者兜底真值对象，杜绝客户端索引 nil 触发崩溃卡死。
        """
        uids = _as_id_list(user_id_list)
        brief_list = []

        valid_portraits = self.get_valid_decorations("portraits")
        valid_frames = self.get_valid_decorations("frames")

        for uid in uids:
            uid_int = int(uid or 0)
            if uid_int <= 0:
                continue
            nick = None
            level = None
            cur_p = None
            cur_f = None

            if self.db:
                try:
                    u_rows = self.db.query("SELECT nick, level FROM users WHERE uid=?", (uid_int,))
                    if u_rows:
                        nick = u_rows[0].get("nick")
                        level = u_rows[0].get("level")

                    g_rows = self.db.query("SELECT cur_portrait, cur_icon_frame FROM game_user WHERE uid=?", (uid_int,))
                    if g_rows:
                        cur_p = g_rows[0].get("cur_portrait")
                        cur_f = g_rows[0].get("cur_icon_frame")
                except Exception as e:
                    logger.warning(f"[get_simple_foreign_info] 查询 uid={uid_int} 异常: {e}")

            if not nick:
                nick = f"观测者_{str(uid_int)[-4:]}"
            if level is None or int(level) <= 0:
                level = 80
            else:
                level = int(level)

            cur_p = int(cur_p or 0)
            if cur_p == 0 or (valid_portraits and cur_p not in valid_portraits):
                cur_p = 2110841

            cur_f = int(cur_f or 0)
            if cur_f == 0 or (valid_frames and cur_f not in valid_frames):
                cur_f = 2001

            brief_list.append({
                "user_id": uid_int,
                "level": level,
                "base_info": {
                    "nick": str(nick),
                    "icon": cur_p,
                    "icon_frame": cur_f
                }
            })

        return brief_list

    def get_foreign_detail_info(self, target_uid):
        """
        他人名片详情查询：cs_32018 -> sc_32019。
        深度满足 foreigninfodata.lua / playerinfoview 消费的所有必选字段，
        杜绝字段缺失导致客户端崩溃或死循环。
        """
        target_uid = int(target_uid or 0)
        nick = None
        level = 80
        sign = "记录一切可能存在的未来"
        icon = 2110841
        icon_frame = 2001
        card_bg = 8001
        post_bg = 6000
        poster_hero_id = 1084
        poster_skin = 1084
        likes = 100
        hero_list = []

        valid_portraits = self.get_valid_decorations("portraits")
        valid_frames = self.get_valid_decorations("frames")
        valid_card_bgs = self.get_valid_decorations("card_bgs")

        if self.db:
            try:
                u_rows = self.db.query("SELECT nick, level, sign, likes FROM users WHERE uid=?", (target_uid,))
                if u_rows:
                    if u_rows[0].get("nick"):
                        nick = str(u_rows[0]["nick"])
                    if u_rows[0].get("level"):
                        level = int(u_rows[0]["level"])
                    if u_rows[0].get("sign"):
                        sign = str(u_rows[0]["sign"])
                    if u_rows[0].get("likes") is not None:
                        likes = int(u_rows[0]["likes"])

                g_rows = self.db.query(
                    "SELECT cur_portrait, cur_icon_frame, cur_card_bg, cur_home_bg, board_hero, show_hero FROM game_user WHERE uid=?",
                    (target_uid,)
                )
                if g_rows:
                    g = g_rows[0]
                    if g.get("cur_portrait"):
                        icon = int(g["cur_portrait"])
                    if g.get("cur_icon_frame"):
                        icon_frame = int(g["cur_icon_frame"])
                    if g.get("cur_card_bg"):
                        card_bg = int(g["cur_card_bg"])
                    if g.get("cur_home_bg"):
                        post_bg = int(g["cur_home_bg"])
                    if g.get("board_hero"):
                        bh = int(g["board_hero"])
                        poster_hero_id = bh if bh < 100000 else int(str(bh)[:4])
                        poster_skin = bh
                    if g.get("show_hero"):
                        try:
                            sh = json.loads(g["show_hero"])
                            if isinstance(sh, list):
                                for hid in sh:
                                    hero_list.append({"hero_id": int(hid), "star": 3, "using_skin": int(hid)})
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"[get_foreign_detail_info] 查询 uid={target_uid} 异常: {e}")

        if not nick:
            nick = f"观测者_{str(target_uid)[-4:]}"
        if not hero_list:
            hero_list = [{"hero_id": 1084, "star": 3, "using_skin": 1084}]

        if icon == 0 or (valid_portraits and icon not in valid_portraits):
            icon = 2110841
        if icon_frame == 0 or (valid_frames and icon_frame not in valid_frames):
            icon_frame = 2001
        if card_bg == 0 or (valid_card_bgs and card_bg not in valid_card_bgs):
            card_bg = 8001
        if post_bg == 0:
            post_bg = 6000

        return {
            "result": 0,
            "user_id": target_uid,
            "base_info": {
                "nick": nick,
                "icon": icon,
                "icon_frame": icon_frame
            },
            "sign": self.get_profile_signature(target_uid)
                if self.db and self.db.query("SELECT 1 FROM users WHERE uid=?", (target_uid,)) else sign,
            "sticker_show_info": self.get_sticker_wall_state(target_uid)["display"]
                if self.db and self.db.query("SELECT 1 FROM users WHERE uid=?", (target_uid,))
                else {"page_id": 4002, "foreground": 0, "sticker_display_info": []},
            "hero_list": hero_list,
            "level": level,
            "is_online": 1,
            "club_id": 0,
            "club_name": "",
            "club_icon": 0,
            "ip_location": "弥弥尔",
            "achievement_static_info": {"not_hide_num": 10, "hide_num": 0, "cfg_hide_num": 0},
            "hero_static_info": {"not_hide_num": len(hero_list), "hide_num": 0, "cfg_hide_num": 0},
            "weapon_servant_static_info": {"not_hide_num": 10, "hide_num": 0, "cfg_hide_num": 0},
            "sticker_static_info": {"not_hide_num": 5, "hide_num": 0, "cfg_hide_num": 0},
            "poster_hero": {
                "hero_id": poster_hero_id,
                "star": 3,
                "using_skin": poster_skin
            },
            "birthday": self.get_birthday_info(target_uid),
            "backhome_architecture_id": 0,
            "hero_id_list": [h["hero_id"] for h in hero_list],
            "likes": likes,
            "used_tag_list": [],
            "information_background_id": card_bg,
            "post_background_id": post_bg,
            "sticker_background_static_info": {"not_hide_num": 1, "hide_num": 0, "cfg_hide_num": 0},
            "sticker_foreground_static_info": {"not_hide_num": 1, "hide_num": 0, "cfg_hide_num": 0},
            "hero_oath_display": []
        }

    def get_foreign_hero_info(self, target_uid):
        """他人名片英雄列表查询：cs_32020 -> sc_32021。"""
        target_uid = int(target_uid or 0)
        return {
            "result": 0,
            "user_id": target_uid,
            "hero_list": [],
            "hero_oath_display": []
        }

    # ------------------------------------------------------------------
    # 二、大厅看板娘、场景与誓约联动域 (Hall & Poster Girl & Oath)
    # ------------------------------------------------------------------

    def change_poster_girl(self, uid, hero_id, ctx=None):
        """更换主看板娘：cs_32016 {poster_girl}。"""
        raw_id = int(hero_id or 0)
        if raw_id <= 0:
            raise OperationError(5, "看板娘ID非法")
        hid = skin_to_hero_id(raw_id)
        if hid <= 0:
            hid = raw_id

        # 校验角色是否已解锁
        if self.db:
            h_rows = self.db.query("SELECT id, unlock, using_skin FROM hero WHERE uid=? AND id=?", (uid, hid))
            is_unlocked = False
            if h_rows:
                unl = h_rows[0].get("unlock")
                if unl is None or int(unl) != 0:
                    is_unlocked = True
            elif hid == 1084:
                is_unlocked = True

            if not is_unlocked:
                raise OperationError(406, "未解锁该角色，无法设为看板娘")
            using_skin = int(h_rows[0].get("using_skin") or hid) if h_rows else hid
        else:
            using_skin = hid

        # 若入参为具体合法已解锁皮肤ID，则使用该皮肤
        if raw_id != hid and raw_id >= 100000 and self.db:
            s_rows = self.db.query("SELECT skin_id FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, raw_id))
            if s_rows:
                using_skin = raw_id

        now_ts = int(time.time())
        # 结算上一任看板娘累计时长
        self.settle_board_hero_duration(uid, now_ts=now_ts)
        extra = _load_user_extra(self.db, uid)
        # 同步静态与实时看板娘，对齐角色当前 using_skin，绝对严禁触碰出战皮肤
        extra["active_board_hero"] = hid
        extra["active_board_skin"] = using_skin
        _save_user_extra(self.db, uid, extra)

        if self.db:
            _safe_upsert(self.db, "game_user", uid, {"board_hero": hid})
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="poster_girl", value=hid)
        return hid

    def record_active_board_hero(self, uid, skin_or_hero_id, background_id=0):
        """Validate the complete silent report before closing the old segment."""
        from sticker_wall_rules import read_extra
        raw_id = skin_or_hero_id
        if type(raw_id) is not int or raw_id <= 0:
            raise OperationError(406, "看板角色无效")
        mapping = _get_skin_hero_map()
        hid = int(mapping.get(str(raw_id), raw_id))
        if not self._profile_hero_owned(uid, hid):
            raise OperationError(406, "尚未解锁看板角色")
        def skin_owned(skin):
            return (skin == hid or mapping.get(str(skin)) == hid and bool(self.db.query(
                "SELECT skin_id FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, skin))))
        if not skin_owned(raw_id):
            raise OperationError(406, "未拥有或不匹配的看板皮肤")
        real_skin_id = raw_id
        if raw_id == hid:
            rows = self.db.query("SELECT using_skin FROM hero WHERE uid=? AND id=?", (uid, hid))
            stored_skin = int(rows[0].get("using_skin") or hid) if rows else hid
            real_skin_id = stored_skin if skin_owned(stored_skin) else hid
        if type(background_id) is not int or background_id < 0:
            raise OperationError(406, "看板场景无效")
        now_ts = int(time.time())
        if background_id and not self.check_asset_ownership(uid, "scene", background_id):
            raise OperationError(406, "未拥有或已到期的看板场景")
        with self.db.transaction():
            read_extra(self.db, uid)
            self.settle_board_hero_duration(uid, now_ts=now_ts)
            extra = read_extra(self.db, uid)
            extra["active_board_hero"] = hid
            extra["active_board_skin"] = real_skin_id
            if background_id:
                extra["active_scene_id"] = background_id
            _save_user_extra(self.db, uid, extra)

    def touch_poster_girl(self, ctx, uid):
        """
        [32054 触摸核心] 触摸大厅看板娘，精准识别当前轮换角色，更新计数并向誓约模块发射广播。
        """
        extra = _load_user_extra(self.db, uid)
        hero_id = 0
        # 优先读取多看板娘轮换实时登台角色
        if extra.get("active_board_hero"):
            hero_id = int(extra["active_board_hero"])
        # 回退读取主看板娘
        if not hero_id and self.db:
            gu = self.db.query("SELECT board_hero FROM game_user WHERE uid=?", (uid,))
            hero_id = int(gu[0]["board_hero"] or 0) if gu else 0
        if not self._profile_hero_owned(uid, hero_id):
            rows = self.db.query("SELECT board_hero FROM game_user WHERE uid=?", (uid,))
            selected = int(rows[0].get("board_hero") or 0) if rows else 0
            hero_id = selected if self._profile_hero_owned(uid, selected) else 1084

        # 1. 累计总触摸次数（写入 user_behavior_stat）
        total_count = self._increment_behavior_stat(uid, "main_interact", hero_id, delta=1)

        # 2. 累计当日触摸次数（写入 users.extra）
        daily_counts = extra.get("daily_touch_counts") or {}
        hid_str = str(hero_id)
        daily_counts[hid_str] = int(daily_counts.get(hid_str, 0)) + 1
        extra["daily_touch_counts"] = daily_counts
        _save_user_extra(self.db, uid, extra)
        daily_count = daily_counts[hid_str]

        # 3. 发射 MAIN_INTERACT 事件广播（誓约模块已订阅，推进 410000 任务）
        bus.emit(Events.MAIN_INTERACT, ctx, uid, hero_id=hero_id, daily_count=daily_count, total_count=total_count)
        logger.info(f"[peripheral] 触摸看板娘 uid={uid} hero={hero_id} daily={daily_count} total={total_count} (广播已发射)")
        return {"hero_id": hero_id, "daily_count": daily_count, "total_count": total_count}

    def settle_board_hero_duration(self, uid, hero_id=None, *, now_ts=None):
        """结算当前看板娘陪伴累计时长（秒）。"""
        now_ts = int(time.time()) if now_ts is None else int(now_ts)
        extra = _load_user_extra(self.db, uid)
        last_ts = int(extra.get("board_hero_last_ts") or 0)
        if last_ts <= 0:
            extra["board_hero_last_ts"] = now_ts
            _save_user_extra(self.db, uid, extra)
            return 0
        delta = now_ts - last_ts
        if delta <= 0:
            return 0
        if hero_id is None:
            hero_id = int(extra.get("active_board_hero") or 0)
            if not hero_id and self.db:
                gu = self.db.query("SELECT board_hero FROM game_user WHERE uid=?", (uid,))
                hero_id = int(gu[0]["board_hero"] or 0) if gu else 0
        if self._profile_hero_owned(uid, hero_id):
            self._increment_behavior_stat(uid, "board_hero_seconds", hero_id, delta=delta)
        extra["board_hero_last_ts"] = now_ts
        _save_user_extra(self.db, uid, extra)
        return delta

    def _increment_behavior_stat(self, uid, stat_key, hero_id, delta=1):
        """递增 user_behavior_stat 行为统计计数。"""
        if not self.db:
            return delta
        now_ts = int(time.time())
        rows = self.db.query(
            "SELECT value FROM user_behavior_stat WHERE uid=? AND stat_key=? AND hero_id=?",
            (uid, stat_key, hero_id)
        )
        cur_val = int(rows[0]["value"] or 0) if rows else 0
        new_val = cur_val + delta
        self.db.execute(
            "INSERT OR REPLACE INTO user_behavior_stat (uid, stat_key, hero_id, value, update_ts) VALUES (?, ?, ?, ?, ?)",
            (uid, stat_key, hero_id, new_val, now_ts)
        )
        return new_val

    def set_home_scene(self, uid, scene_id, ctx=None):
        """设置大厅场景：cs_32108 {poster_background_id}。"""
        scene_id = int(scene_id or 0)
        if scene_id <= 0:
            raise OperationError(5, "场景ID非法")
        if not self.check_asset_ownership(uid, "scene", scene_id):
            raise OperationError(406, "未获得该大厅场景")
        now_ts = int(time.time())
        if self.db:
            self.db.execute("UPDATE scene SET is_current=0 WHERE uid=?", (uid,))
            # Selection does not grant ownership or make a trial permanent.
            self.db.execute("UPDATE scene SET is_current=1 WHERE uid=? AND scene_id=?", (uid, scene_id))
            _safe_upsert(self.db, "game_user", uid, {"cur_scene": scene_id})
        extra = _load_user_extra(self.db, uid)
        extra["active_scene_id"] = scene_id
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="scene", value=scene_id)
        return scene_id

    def set_bgm(self, uid, bgm_id, ctx=None):
        """设置大厅音乐：cs_12026 {id}。"""
        bgm_id = int(bgm_id or 0)
        if bgm_id <= 0:
            raise OperationError(5, "音乐ID非法")
        if self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, 'bgm', ?, 1)",
                (uid, bgm_id)
            )
            _safe_upsert(self.db, "game_user", uid, {"bgm_id": bgm_id})
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="bgm", value=bgm_id)
        return bgm_id

    def _save_lobby_setting(self, uid, key, value, ctx=None):
        from sticker_wall_rules import read_extra, StickerWallError
        try:
            with self.db.transaction():
                extra = read_extra(self.db, uid)
                extra[key] = value
                cursor = self.db.execute("UPDATE users SET extra=? WHERE uid=?", (json.dumps(extra, ensure_ascii=False), uid))
                if cursor.rowcount != 1:
                    raise StickerWallError("账号不存在或不唯一", 406)
                bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind=key, value=value)
        except StickerWallError as exc:
            raise OperationError(exc.code, str(exc)) from exc
        return value

    def set_pure_mode(self, uid, info, ctx=None):
        """Validate current UI indices, retaining its legacy zero aliases."""
        from profile_rules import validate_pure_mode
        try:
            value = validate_pure_mode(info)
        except ValueError as exc:
            raise OperationError(5, str(exc)) from exc
        return self._save_lobby_setting(uid, "pure_mode", value, ctx)

    def set_table_module(self, uid, modules, ctx=None):
        """Nonempty input is a complete three-component snapshot."""
        from profile_rules import validate_modules
        try:
            value = validate_modules(modules)
        except ValueError as exc:
            raise OperationError(5, str(exc)) from exc
        return self._save_lobby_setting(uid, "table_modules", value, ctx)

    def set_random_info(self, uid, info_list, ctx=None):
        """上传随机场景/少女装扮：cs_32122。"""
        extra = _load_user_extra(self.db, uid)
        extra["random_info"] = info_list or []
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="random_info", value=info_list)
        return info_list

    def set_random_model(self, uid, rtype, model, ctx=None):
        """随机展示开关：cs_32124。"""
        rtype = int(rtype or 0)
        model = int(model or 0)
        extra = _load_user_extra(self.db, uid)
        rm = extra.get("random_model") if isinstance(extra.get("random_model"), dict) else {}
        rm[str(rtype)] = model
        extra["random_model"] = rm
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="random_model", value={"type": rtype, "model": model})
        return {"type": rtype, "model": model}

    def set_show_hero_dressing_scene(self, uid, rtype, scene, ctx=None):
        """DLC场景展示开关：cs_32126。"""
        rtype = int(rtype or 0)
        scene = int(scene or 0)
        extra = _load_user_extra(self.db, uid)
        m = extra.get("show_hero_dressing_scene") if isinstance(extra.get("show_hero_dressing_scene"), dict) else {}
        m[str(rtype)] = scene
        extra["show_hero_dressing_scene"] = m
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="show_hero_dressing_scene", value={"type": rtype, "scene": scene})
        return {"type": rtype, "scene": scene}

    def set_routine_hero_dressing_scene(self, uid, rtype, scene, ctx=None):
        """登场动画展示：cs_32128。"""
        rtype = int(rtype or 0)
        scene = int(scene or 0)
        extra = _load_user_extra(self.db, uid)
        m = extra.get("routine_hero_dressing_scene") if isinstance(extra.get("routine_hero_dressing_scene"), dict) else {}
        m[str(rtype)] = scene
        extra["routine_hero_dressing_scene"] = m
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="routine_hero_dressing_scene", value={"type": rtype, "scene": scene})
        return {"type": rtype, "scene": scene}

    def set_random_list(self, uid, rtype, rlist, ctx=None):
        """随机池列表：cs_32130。"""
        rtype = int(rtype or 0)
        id_list = _as_id_list(rlist)
        extra = _load_user_extra(self.db, uid)
        m = extra.get("random_list") if isinstance(extra.get("random_list"), dict) else {}
        m[str(rtype)] = id_list
        extra["random_list"] = m
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="random_list", value={"type": rtype, "list": id_list})
        return {"type": rtype, "random_list": id_list}

    def set_special_scene_view(self, uid, main_bg, sub_bg, ctx=None):
        """特殊场景子视图：cs_32134。"""
        main_bg = int(main_bg or 0)
        sub_bg = int(sub_bg or 0)
        extra = _load_user_extra(self.db, uid)
        m = extra.get("sub_poster_background") if isinstance(extra.get("sub_poster_background"), dict) else {}
        m[str(main_bg)] = sub_bg
        extra["sub_poster_background"] = m
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="sub_poster_background", value={"main": main_bg, "sub": sub_bg})
        return {"main": main_bg, "sub": sub_bg}

    # ------------------------------------------------------------------
    # 三、贴纸画册与套装奖励域 (Sticker Canvas & Suits)
    # ------------------------------------------------------------------

    def get_sticker_wall_state(self, uid, db=None):
        from sticker_wall_rules import load_rules, read_extra, owned_assets, projected_pages, active_page_id, display_page, StickerWallError
        db = db if db is not None else self.db
        try:
            rules = load_rules()
            extra = read_extra(db, uid)
            cards = db.query("SELECT kind, item_id, obtained FROM player_card WHERE uid=?", (uid,))
            gu = db.query("SELECT cur_sticker_bg FROM game_user WHERE uid=?", (uid,))
            mirror = gu[0] if gu else {}
            owned = owned_assets(cards, rules)
            return dict(pages=projected_pages(extra, owned, rules), selected=active_page_id(extra, mirror, rules, owned),
                        display=display_page(extra, mirror, rules, owned), owned=owned)
        except StickerWallError as exc:
            raise OperationError(exc.code, str(exc)) from exc

    def save_sticker_show_info(self, uid, layout_list):
        """Apply changed-page snapshots atomically; never replace the whole album."""
        from sticker_wall_rules import load_rules, read_extra, owned_assets, merge_pages, StickerWallError
        db = self.db
        if not isinstance(layout_list, list):
            raise OperationError(2, "贴纸画册必须是页面列表")
        if not layout_list:
            return
        try:
            with db.transaction():
                rules = load_rules()
                extra = read_extra(db, uid)
                cards = db.query("SELECT kind, item_id, obtained FROM player_card WHERE uid=?", (uid,))
                extra["sticker_show_info"] = merge_pages(extra, layout_list, owned_assets(cards, rules), rules)
                updated = db.execute("UPDATE users SET extra=? WHERE uid=?",
                                     (json.dumps(extra, ensure_ascii=False), uid))
                if updated.rowcount != 1:
                    raise OperationError(406, "贴纸墙保存未命中账号")
        except StickerWallError as exc:
            raise OperationError(exc.code, str(exc)) from exc

    def change_sticker_show_page(self, uid, page_id):
        """Change only the selected wall, including an owned empty wall."""
        from sticker_wall_rules import load_rules, read_extra, owned_assets, StickerWallError
        db = self.db
        try:
            with db.transaction():
                rules = load_rules()
                extra = read_extra(db, uid)
                if type(page_id) is not int or page_id not in rules["backgrounds"]:
                    raise OperationError(2, "贴纸背景配置无效")
                cards = db.query("SELECT kind, item_id, obtained FROM player_card WHERE uid=?", (uid,))
                if page_id not in owned_assets(cards, rules)["backgrounds"]:
                    raise OperationError(406, "尚未获得该贴纸背景")
                extra["sticker_show_page"] = page_id
                updated = db.execute("UPDATE users SET extra=? WHERE uid=?",
                                     (json.dumps(extra, ensure_ascii=False), uid))
                if updated.rowcount != 1:
                    raise OperationError(406, "贴纸展示选择未命中账号")
                db.execute("INSERT INTO game_user(uid, cur_sticker_bg) VALUES(?, ?) "
                           "ON CONFLICT(uid) DO UPDATE SET cur_sticker_bg=excluded.cur_sticker_bg", (uid, page_id))
        except StickerWallError as exc:
            raise OperationError(exc.code, str(exc)) from exc
        return page_id

    def claim_sticker_suit_reward(self, uid, suit_id_list, ctx=None):
        """Validate the whole batch, grant and mark in the same transaction."""
        from sticker_wall_rules import load_rules, read_extra, owned_assets, suit_reward_plan, StickerWallError
        from inventory_service import InventoryService
        db = self.db
        try:
            with db.transaction():
                extra = read_extra(db, uid)
                cards = db.query("SELECT kind, item_id, obtained FROM player_card WHERE uid=?", (uid,))
                history, rewards = suit_reward_plan(suit_id_list, extra, owned_assets(cards, load_rules()))
                if not rewards:
                    return []
                InventoryService.grant_items(ctx if ctx is not None else db, uid,
                                            [(r["id"], r["num"]) for r in rewards], source="sticker_suit")
                extra["sticker_suit_rewarded"] = history
                cursor = db.execute("UPDATE users SET extra=? WHERE uid=?", (json.dumps(extra, ensure_ascii=False), uid))
                if cursor.rowcount != 1:
                    raise StickerWallError("账号不存在或不唯一", 406)
                return rewards
        except StickerWallError as exc:
            raise OperationError(exc.code, str(exc)) from exc

    def claim_skin_gift(self, uid, skin_id):
        """领取皮肤赠礼：cs_32052 {skin_id}。"""
        skin_id = int(skin_id or 0)
        extra = _load_user_extra(self.db, uid)
        gifts = set(extra.get("received_skin_gifts") or [])
        gifts.add(skin_id)
        extra["received_skin_gifts"] = list(gifts)
        _save_user_extra(self.db, uid, extra)
        logger.info(f"[peripheral] uid={uid} 领取皮肤赠礼 skin_id={skin_id}")
        return skin_id

    # ------------------------------------------------------------------
    # 四、账号属性与局内云端配置域 (Account & Cloud Settings)
    # ------------------------------------------------------------------

    def change_nickname(self, uid, nick, ctx=None):
        """23012: opening name (0->1), first rename (1->2), then gifts/card 6."""
        from inventory_service import InventoryService
        nick_str = str(nick or "").strip()
        if not nick_str:
            raise OperationError(5, "昵称不能为空")
        if len(nick_str) > 16:
            raise OperationError(5, "昵称过长")
        with self.db.transaction():
            rows = self.db.query(
                "SELECT nick,is_changed_nick,change_nick_times FROM users WHERE uid=?", (uid,))
            if len(rows) != 1:
                raise OperationError(406, "账号不存在或不唯一")
            user = rows[0]
            state = int(user.get("is_changed_nick") or 0)
            free_times = max(0, int(user.get("change_nick_times") or 0))
            costs = []
            # The same confirmed name is also safe to retry after a lost reply.
            if state == 0 or nick_str != user["nick"]:
                if state < 2:
                    state += 1
                elif free_times:
                    free_times -= 1
                else:
                    if not InventoryService.cost_item(ctx if ctx is not None else self.db, uid, 6, 1):
                        raise OperationError(3, "改名卡不足")
                    costs = [{"id": 6, "num": 1}]
                cursor = self.db.execute(
                    "UPDATE users SET nick=?, is_changed_nick=?, change_nick_times=? WHERE uid=?",
                    (nick_str, state, free_times, uid))
                if cursor.rowcount != 1:
                    raise OperationError(406, "昵称记录未保存")
                bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="nick", value=nick_str)
        return {"nick": nick_str, "is_changed_nick": state,
                "system_change_nick_times": free_times, "cost_item_list": costs}

    def set_birthday(self, uid, month, day, ctx=None):
        """Persist a yearless calendar date; February 29 remains valid."""
        from profile_rules import valid_birthday
        if not valid_birthday(month, day):
            raise OperationError(5, "生日格式不合法")
        with self.db.transaction():
            if len(self.db.query("SELECT uid FROM users WHERE uid=?", (uid,))) != 1:
                raise OperationError(406, "账号不存在或不唯一")
            cursor = self.db.execute(
                "INSERT INTO game_user(uid,birth_month,birth_day) VALUES(?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET birth_month=excluded.birth_month,birth_day=excluded.birth_day",
                (uid, month, day))
            if cursor.rowcount != 1:
                raise OperationError(406, "生日记录未保存")
            from birthday_service import record_birthday_setting, ensure_official_mail
            record_birthday_setting(self.db, uid)
            ensure_official_mail(self.db, uid, ctx=ctx)
            bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="birthday", value={"month": month, "day": day})
        return {"month": month, "day": day}

    def set_text_language(self, uid, lang, ctx=None):
        """设置语言偏好：cs_12100 {language}。"""
        lang_str = str(lang or "")
        extra = _load_user_extra(self.db, uid)
        extra["text_language"] = lang_str
        _save_user_extra(self.db, uid, extra)
        bus.emit(Events.PROFILE_UPDATE, ctx, uid, kind="text_language", value=lang_str)
        return lang_str

    def save_cloud_battle_ui(self, uid, layout_data):
        """局内战斗按键/操作布局云端保存。"""
        now_ts = int(time.time())
        extra = _load_user_extra(self.db, uid)
        extra["cloud_battle_ui"] = {
            "layout": layout_data,
            "update_ts": now_ts
        }
        _save_user_extra(self.db, uid, extra)
        return extra["cloud_battle_ui"]

    def save_cloud_graphic_setting(self, uid, graphic_data):
        """局内画质清晰度配置云端保存。"""
        now_ts = int(time.time())
        extra = _load_user_extra(self.db, uid)
        extra["cloud_graphic_setting"] = {
            "setting": graphic_data,
            "update_ts": now_ts
        }
        _save_user_extra(self.db, uid, extra)
        return extra["cloud_graphic_setting"]

    def get_cloud_settings(self, uid):
        """获取云端保存的局内键位与画质配置。"""
        extra = _load_user_extra(self.db, uid)
        return {
            "battle_ui": extra.get("cloud_battle_ui"),
            "graphic": extra.get("cloud_graphic_setting")
        }

    # ------------------------------------------------------------------
    # 五、登录洪流数据闭环 (Login Push Payloads)
    # ------------------------------------------------------------------

    def get_scene_entries(self, uid, db=None):
        from profile_rules import project_home_scenes
        db = db if db is not None else self.db
        rows = db.query("SELECT scene_id,lasted_time,obtain_time FROM user_scene WHERE uid=?", (uid,))
        legacy = db.query("SELECT scene_id,obtain_time FROM scene WHERE uid=?", (uid,))
        skin_map = _get_skin_scene_map()
        skins = db.query("SELECT skin_id FROM player_skin_unlocked WHERE uid=?", (uid,))
        bound = [skin_map[str(row['skin_id'])] for row in skins if str(row['skin_id']) in skin_map]
        return project_home_scenes(rows, legacy, bound, now_ts=int(time.time()))

    def get_profile_card_payload(self, uid, db):
        """
        [sc_32009 玩家资料卡全量真值闭环]
        彻底修复名片背景、展示英雄与贴纸画册排版断链问题，全量数据由数据库与 extra 驱动。
        """
        from codec import decode, encode

        # 1. 场景列表
        poster_bg = self.get_scene_entries(uid, db)

        # 2. 读取原始模板
        lp = db.query("SELECT payload FROM login_push WHERE cmd=32009")
        obj = None
        if lp and lp[0]["payload"]:
            try:
                obj = decode(lp[0]["payload"], "sc_32009")
            except Exception:
                obj = None
        if not isinstance(obj, dict):
            obj = {}

        obj["poster_background_list"] = poster_bg

        # 3. 动态注入 game_user 装扮真值
        try:
            valid_portraits = self.get_valid_decorations("portraits")
            valid_frames = self.get_valid_decorations("frames")
            valid_bubbles = self.get_valid_decorations("bubbles")
            valid_card_bgs = self.get_valid_decorations("card_bgs")
            valid_scenes = self.get_valid_decorations("scenes")
            valid_sticker_bgs = self.get_valid_decorations("sticker_bgs")

            gu = db.query(
                "SELECT board_hero, cur_portrait, cur_icon_frame, cur_card_bg, cur_bubble, show_hero, cur_scene, cur_sticker_bg FROM game_user WHERE uid=?",
                (uid,)
            )
            if gu:
                g = gu[0]
                if g.get("board_hero"):
                    obj["poster_girl"] = int(g["board_hero"])
                if g.get("cur_portrait"):
                    cur_p = int(g["cur_portrait"])
                    if valid_portraits and cur_p not in valid_portraits:
                        if cur_p == 1084:
                            cur_p = 2110841
                    obj["icon"] = cur_p
                if g.get("cur_icon_frame"):
                    cur_f = int(g["cur_icon_frame"])
                    if valid_frames and cur_f not in valid_frames:
                        cur_f = 2001
                    obj["icon_frame"] = cur_f
                if g.get("cur_bubble"):
                    cur_b = int(g["cur_bubble"])
                    if cur_b == 1 or (valid_bubbles and cur_b not in valid_bubbles):
                        cur_b = 9001
                    obj["chat_bubble"] = cur_b
                # 修复：名片背景真值注入
                if g.get("cur_card_bg"):
                    cur_bg = int(g["cur_card_bg"])
                    if cur_bg in (0, 1) or (valid_card_bgs and cur_bg not in valid_card_bgs):
                        cur_bg = 8001
                    obj["information_background_id"] = cur_bg
                # 修复：大厅场景真值注入（防重登后场景丢失）
                if g.get("cur_scene"):
                    cur_sc = int(g["cur_scene"])
                    if valid_scenes and cur_sc not in valid_scenes:
                        cur_sc = 6000
                    obj["poster_background_id"] = cur_sc
                else:
                    obj.setdefault("poster_background_id", 6000)
                # 修复：贴纸背景底板真值注入
                if g.get("cur_sticker_bg"):
                    cur_sbg = int(g["cur_sticker_bg"])
                    if valid_sticker_bgs and cur_sbg not in valid_sticker_bgs:
                        cur_sbg = 4002
                    obj["sticker_background"] = cur_sbg
                else:
                    obj.setdefault("sticker_background", 4002)
                # 修复：名片展示角色真值注入
                if g.get("show_hero"):
                    try:
                        sh = json.loads(g["show_hero"])
                        if isinstance(sh, list) and len(sh) > 0:
                            obj["heroes"] = [int(x) for x in sh]
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"game_user 真值注入异常: {e}")

        # 4. 动态从 player_card 注入全量已解锁资产列表（驱动客户端高亮/灰显状态）
        try:
            pc_rows = db.query("SELECT kind, item_id FROM player_card WHERE uid=? AND obtained=1", (uid,))
            kind_map = {}
            for r in (pc_rows or []):
                kind_map.setdefault(r["kind"], []).append(int(r["item_id"]))

            valid_portraits = self.get_valid_decorations("portraits")
            valid_frames = self.get_valid_decorations("frames")
            valid_bubbles = self.get_valid_decorations("bubbles")
            valid_card_bgs = self.get_valid_decorations("card_bgs")
            valid_tags = self.get_valid_decorations("tags")

            # 头像列表 (只下发在客户端 ItemCfg type=11 中注册的有效 ID，杜绝非头像道具导致客户端 InitPortraitList 崩溃)
            portraits = [pid for pid in kind_map.get("portrait", []) if (not valid_portraits or pid in valid_portraits)]
            for def_p in self.DEFAULT_PORTRAITS:
                if def_p not in portraits:
                    portraits.append(def_p)
            if portraits:
                obj["icon_list"] = [{"id": pid, "lasted_time": 0} for pid in portraits]

            # 头像框列表 (只下发在客户端 ItemCfg type=12 中注册的有效 ID，杜绝 1 等伪 ID)
            frames = [fid for fid in kind_map.get("icon_frame", []) if (not valid_frames or fid in valid_frames)]
            for def_f in self.DEFAULT_FRAMES:
                if def_f not in frames:
                    frames.append(def_f)
            if frames:
                obj["icon_frame_list"] = [{"id": fid, "lasted_time": 0} for fid in frames]

            # 气泡列表 (只下发在客户端 ItemCfg type=26 中注册的有效 ID，默认 9001，严禁下发 1)
            bubbles = [bid for bid in kind_map.get("bubble", []) if (not valid_bubbles or bid in valid_bubbles)]
            for def_b in self.DEFAULT_BUBBLES:
                if def_b not in bubbles:
                    bubbles.append(def_b)
            if bubbles:
                obj["chat_bubble_list"] = [{"id": bid, "lasted_time": 0} for bid in bubbles]

            # 名片背景列表 (只下发在客户端 ItemCfg type=23 中注册的有效 ID，默认 8001，严禁下发 0 或 1)
            card_bgs = [cbid for cbid in kind_map.get("card_bg", []) if (not valid_card_bgs or cbid in valid_card_bgs)]
            for def_bg in self.DEFAULT_CARD_BGS:
                if def_bg not in card_bgs:
                    card_bgs.append(def_bg)
            if card_bgs:
                obj["information_background_list"] = [{"id": cbid, "lasted_time": 0} for cbid in card_bgs]

            # 修复：称号/个性标签列表 (kind='title'，只下发有效 TAG ID，补充默认 DEFAULT_TAGS，杜绝客户端全锁定)
            tags = [tid for tid in kind_map.get("title", []) if (not valid_tags or tid in valid_tags)]
            for def_t in self.DEFAULT_TAGS:
                if def_t not in tags:
                    tags.append(def_t)
            if tags:
                obj["tag_info_list"] = [{"id": tid, "lasted_time": 0, "obtain_time": 0} for tid in tags]

            # 贴纸图鉴
            stickers = kind_map.get("sticker", [])
            if stickers:
                obj["all_sticker_list"] = stickers
            fg_stickers = kind_map.get("sticker_fg", [])
            if fg_stickers:
                obj["all_foreground_list"] = fg_stickers
            bg_stickers = kind_map.get("sticker_bg", [])
            if bg_stickers:
                obj["all_background_list"] = bg_stickers
        except Exception as e:
            logger.warning(f"player_card 动态注入异常: {e}")

        # 5. 读取 users 表主档 (likes / sign)
        try:
            u_rows = db.query("SELECT likes, sign FROM users WHERE uid=?", (uid,))
            if u_rows:
                if u_rows[0].get("likes") is not None:
                    obj["likes"] = int(u_rows[0]["likes"] or 0)
                if u_rows[0].get("sign"):
                    obj["sign"] = str(u_rows[0]["sign"])
        except Exception as e:
            logger.warning(f"users 主档读取异常: {e}")

        # 6. 动态注入 users.extra 偏好与贴纸排版
        try:
            extra = _load_user_extra(db, uid)
            if extra.get("profile_sign") and not obj.get("sign"):
                obj["sign"] = str(extra["profile_sign"])
            if extra.get("profile_tags"):
                obj["used_tag_list"] = _as_id_list(extra["profile_tags"])
            if "likes" not in obj and extra.get("profile_likes") is not None:
                obj["likes"] = int(extra["profile_likes"] or 0)

            # 修复：today_send_like 协议为 repeated uint64，必须为整型列表，严禁传单整数！
            today_likes = extra.get("today_send_like")
            if isinstance(today_likes, list):
                obj["today_send_like"] = [int(x) for x in today_likes]
            elif isinstance(today_likes, (int, str)) and int(today_likes) > 1:
                obj["today_send_like"] = [int(today_likes)]
            else:
                obj["today_send_like"] = []

            # 修复：贴纸画册排版还原
            if extra.get("sticker_show_info"):
                obj["sticker_show_info"] = extra["sticker_show_info"]
            # 修复：贴纸已领套装奖励列表还原
            if extra.get("sticker_suit_rewarded"):
                obj["admitted_suit_reaward_list"] = extra["sticker_suit_rewarded"]
            # 纯白模式与组件
            if extra.get("pure_mode"):
                obj["table_setting"] = extra["pure_mode"]
            if extra.get("table_modules"):
                obj["table_setting_module"] = extra["table_modules"]

            # 修复：随机看板娘与随机场景配置组装还原
            if extra.get("random_info") and isinstance(extra["random_info"], list):
                obj["random_info"] = extra["random_info"]
            else:
                rm = extra.get("random_model") or {}
                rl = extra.get("random_list") or {}
                sh = extra.get("show_hero_dressing_scene") or {}
                rt = extra.get("routine_hero_dressing_scene") or {}
                random_info_list = []
                for rtype in (1, 2):
                    stype = str(rtype)
                    random_info_list.append({
                        "random_type": rtype,
                        "random_model": int(rm.get(stype, 0) or 0),
                        "show_hero_dressing_scene": int(sh.get(stype, 0) or 0),
                        "routine_hero_dressing_scene": int(rt.get(stype, 0) or 0),
                        "random_list": [int(x) for x in (rl.get(stype, []) or [])],
                    })
                obj["random_info"] = random_info_list
        except Exception as e:
            logger.warning(f"users.extra 真值注入异常: {e}")

        # Sticker state and entitlement lists must override even an old nonempty template.
        wall = self.get_sticker_wall_state(uid, db)
        obj["sticker_show_info"] = wall["pages"]
        obj["sticker_background"] = wall["selected"]
        obj["all_sticker_list"] = sorted(wall["owned"]["stickers"])
        obj["all_foreground_list"] = sorted(wall["owned"]["foregrounds"])
        obj["all_background_list"] = sorted(wall["owned"]["backgrounds"])
        obj["sign"] = self.get_profile_signature(uid, db)
        obj["heroes"] = self.get_show_heroes(uid, db)
        from sticker_wall_rules import read_extra
        extra = read_extra(db, uid)
        from profile_rules import default_home_scene_ids, project_like_history
        if int(obj.get('poster_background_id') or 0) not in {row['id'] for row in poster_bg}:
            obj['poster_background_id'] = default_home_scene_ids()[0]
        obj['info_list'] = project_like_history(extra.get('profile_like_history'))
        obj["admitted_suit_reaward_list"] = extra.get("sticker_suit_rewarded", [])
        from profile_rules import project_pure_mode, project_modules
        obj["table_setting"] = project_pure_mode(extra.get("pure_mode"))
        obj["table_setting_module"] = project_modules(extra.get("table_modules"))
        if ("pure_mode" in extra and extra["pure_mode"] != obj["table_setting"]
                or "table_modules" in extra and extra["table_modules"] != obj["table_setting_module"]):
            logger.warning(f"uid={uid} 历史纯白/桌面设置已作安全读取投影，原存档保留")
        return encode("sc_32009", obj)

    def get_bgm_push_payload(self, uid, db):
        """sc_12029 大厅 BGM 登录推送。"""
        from codec import encode
        bgm_id = 0
        try:
            rows = db.query("SELECT bgm_id FROM game_user WHERE uid=?", (uid,))
            if rows:
                bgm_id = int(rows[0]["bgm_id"] or 0)
        except Exception:
            bgm_id = 0
        return encode("sc_12029", {"id": bgm_id or 4})

    def get_birthday_info(self, uid):
        from profile_rules import read_birthday
        return read_birthday(self.db, uid)

    def get_birthday_push_payload(self, uid, db):
        """sc_12033: preserve unset state; never invent a birthday on read error."""
        from codec import encode
        from profile_rules import read_birthday
        return encode("sc_12033", read_birthday(db, uid))


# ======================================================================
# 基类：轻量确认
# ======================================================================

class _PeripheralOkOp(Operation):
    """外围轻量确认基类：apply 落库后 respond 回 sc {result:0} 单帧。"""

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_%d" % int(self.sc), {"result": 0}) or b"\\x08\\x00"
        return [DownFrame(self.sc, payload)]


# ======================================================================
# 协议控制器层 (Operations Controller Layer)
# ======================================================================

@operation
class HomeSceneSetOp(Operation):
    cmd = 32108
    sc = 32109

    def apply(self, data, ctx):
        ctx.uid = self.uid
        scene_id = int(data.get("poster_background_id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_home_scene(self.uid, scene_id, ctx)
        return {"scene_id": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32109", {"result": 0}) or b"\\x08\\x00"
        ctx.log(f"cs_32108 -> 设置主界面大厅场景 id={result['scene_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class QuerySetBgmOp(Operation):
    cmd = 12026
    sc = 12027

    def apply(self, data, ctx):
        ctx.uid = self.uid
        bgm_id = int(data.get("id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_bgm(self.uid, bgm_id, ctx)
        return {"id": res}

    def respond(self, result, data, ctx):
        p27 = ctx.codec_encode("sc_12027", {"result": 0}) or b"\\x08\\x00"
        p29 = ctx.codec_encode("sc_12029", {"id": result["id"]}) or b"\\x08\\x00"
        ctx.log(f"cs_12026 -> 设置大厅BGM id={result['id']}")
        return [DownFrame(12027, p27), DownFrame(12029, p29)]


@operation
class SetTextLanguageOp(_PeripheralOkOp):
    cmd = 12100
    sc = 12101

    def apply(self, data, ctx):
        lang = data.get("language")
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_text_language(self.uid, lang, ctx)
        return {"language": res}


@operation
class ChangeChatBubbleOp(_PeripheralOkOp):
    cmd = 32120
    sc = 32121

    def apply(self, data, ctx):
        ctx.uid = self.uid
        bubble_id = int(data.get("chat_bubble") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_chat_bubble(self.uid, bubble_id, ctx)
        return {"chat_bubble": res}


@operation
class ChangePosterGirlOp(_PeripheralOkOp):
    cmd = 32016
    sc = 32017

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hero_id = int(data.get("poster_girl") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_poster_girl(self.uid, hero_id, ctx)
        return {"poster_girl": res}


@operation
class ChangeShowHerosOp(_PeripheralOkOp):
    cmd = 32014
    sc = 32015
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_show_heroes(self.uid, data.get("heroes"), ctx)
        return {"heroes": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32015", {"result": 0})
        if not payload:
            raise ValueError("必要名片角色回执sc_32015为空")
        return [DownFrame(self.sc, payload)]


@operation
class ChangePortraitOp(_PeripheralOkOp):
    cmd = 32032
    sc = 32033

    def apply(self, data, ctx):
        ctx.uid = self.uid
        icon_id = int(data.get("icon_id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_portrait(self.uid, icon_id, ctx)
        return {"icon_id": res}


@operation
class ChangeFrameIconOp(_PeripheralOkOp):
    cmd = 32034
    sc = 32035

    def apply(self, data, ctx):
        ctx.uid = self.uid
        frame_id = int(data.get("iconframe_id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_frame(self.uid, frame_id, ctx)
        return {"iconframe_id": res}


@operation
class ChangeCardBgOp(_PeripheralOkOp):
    cmd = 32114
    sc = 32115

    def apply(self, data, ctx):
        ctx.uid = self.uid
        bg_id = int(data.get("id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_card_bg(self.uid, bg_id, ctx)
        return {"id": res}


@operation
class ChangeTagListOp(_PeripheralOkOp):
    cmd = 32116
    sc = 32117

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_tags(self.uid, data.get("tags"), ctx)
        return {"tags": res}


@operation
class SendLikeOp(_PeripheralOkOp):
    cmd = 32118
    sc = 32119

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.send_like(self.uid, data.get("uid"), data.get("source"), data.get("activity_id"), ctx)
        return res


@operation
class ChangeSignOp(_PeripheralOkOp):
    cmd = 32012
    sc = 32013
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_sign(self.uid, data.get("sign"), ctx)
        return {"sign": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32013", {"result": 0})
        if not payload:
            raise ValueError("必要签名回执sc_32013为空")
        return [DownFrame(self.sc, payload)]


@operation
class GetSimpleForeignInfoOp(Operation):
    cmd = 32036
    sc = 32037

    def apply(self, data, ctx):
        ctx.uid = self.uid
        uids = _as_id_list(data.get("user_id_list"))
        service = PeripheralService.get_instance(ctx.db)
        brief_list = service.get_simple_foreign_info(uids)
        return {"user_brief_list": brief_list}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32037", {
            "result": 0,
            "user_brief_list": result["user_brief_list"]
        }) or b"\x08\x00"
        ctx.log(f"cs_32036 -> 批量简要名片响应 {len(result['user_brief_list'])} 人", "INFO")
        return [DownFrame(self.sc, payload)]


@operation
class GetForeignDetailInfoOp(Operation):
    cmd = 32018
    sc = 32019

    def apply(self, data, ctx):
        ctx.uid = self.uid
        target_uid = int(data.get("user_id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        detail = service.get_foreign_detail_info(target_uid)
        return detail

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32019", result) or b"\x08\x00"
        ctx.log(f"cs_32018 -> 查看玩家详情响应 uid={result.get('user_id')}", "INFO")
        return [DownFrame(self.sc, payload)]


@operation
class GetForeignHeroInfoOp(Operation):
    cmd = 32020
    sc = 32021

    def apply(self, data, ctx):
        ctx.uid = self.uid
        target_uid = int(data.get("user_id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        heroes = service.get_foreign_hero_info(target_uid)
        return heroes

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32021", result) or b"\x08\x00"
        ctx.log(f"cs_32020 -> 查看玩家英雄响应 uid={result.get('user_id')}", "INFO")
        return [DownFrame(self.sc, payload)]


@operation
class ChangeNicknameOp(Operation):
    cmd = 23012
    sc = 23013
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        return service.change_nickname(self.uid, data.get("nick"), ctx)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_23013", {"result": 0,
            "is_changed_nick": result["is_changed_nick"],
            "system_change_nick_times": result["system_change_nick_times"],
            "cost_item_list": result["cost_item_list"]})
        if not payload:
            raise ValueError("必要改名回执sc_23013为空")
        frames = [DownFrame(self.sc, payload)]
        if result["cost_item_list"]:
            from operations import _refresh_frames
            frames += _refresh_frames(ctx, self.uid, strict=True)
        return frames


@operation
class ChangeBirthdayOp(_PeripheralOkOp):
    cmd = 12030
    sc = 12031
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        return service.set_birthday(self.uid, data.get("month"), data.get("day"), ctx)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_12031", {"result": 0})
        if not payload:
            raise ValueError("必要生日回执sc_12031为空")
        return [DownFrame(self.sc, payload)]

    def after_commit(self, result, data, ctx):
        from birthday_service import trigger_today_greeting
        trigger_today_greeting(ctx.db, self.uid)


@operation
class SetPureModeOp(_PeripheralOkOp):
    cmd = 32068
    sc = 32069
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_pure_mode(self.uid, data.get("info"), ctx)
        return {"info": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32069", {"result": 0})
        if not payload:
            raise ValueError("必要大厅设置回执为空")
        return [DownFrame(self.sc, payload)]


@operation
class SetTableModuleOp(_PeripheralOkOp):
    cmd = 32070
    sc = 32071
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_table_module(self.uid, data.get("module", []), ctx)
        return {"module": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32071", {"result": 0})
        if not payload:
            raise ValueError("必要大厅设置回执为空")
        return [DownFrame(self.sc, payload)]


@operation
class SetRandomInfoOp(_PeripheralOkOp):
    cmd = 32122
    sc = 32123

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_random_info(self.uid, data.get("random_info_list"), ctx)
        return {"random_info_list": res}


@operation
class SetRandomModelOp(_PeripheralOkOp):
    cmd = 32124
    sc = 32125

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_random_model(self.uid, data.get("type"), data.get("model"), ctx)
        return res


@operation
class SetShowHeroDressingSceneOp(_PeripheralOkOp):
    cmd = 32126
    sc = 32127

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_show_hero_dressing_scene(self.uid, data.get("type"), data.get("show_hero_dressing_scene"), ctx)
        return res


@operation
class SetRoutineHeroDressingSceneOp(_PeripheralOkOp):
    cmd = 32128
    sc = 32129

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_routine_hero_dressing_scene(self.uid, data.get("type"), data.get("routine_hero_dressing_scene"), ctx)
        return res


@operation
class SetRandomListOp(_PeripheralOkOp):
    cmd = 32130
    sc = 32131

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_random_list(self.uid, data.get("type"), data.get("random_list"), ctx)
        return res


@operation
class SetSpecialSceneViewOp(_PeripheralOkOp):
    cmd = 32134
    sc = 32135

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.set_special_scene_view(self.uid, data.get("poster_background_id"), data.get("sub_poster_background"), ctx)
        return res


@operation
class TouchPosterGirlOp(_PeripheralOkOp):
    """
    触摸看板娘：cs_32054 (空) -> sc_32055。
    精准识别当前轮换角色，累计次数并向誓约模块发射 MAIN_INTERACT 广播。
    """
    cmd = 32054
    sc = 32055
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        res = service.touch_poster_girl(ctx, self.uid)
        return res

    def respond(self, result, data, ctx):
        from operation_notifications import take_pending_frames
        # Deliver the listener's current task snapshot before acknowledging the touch.
        payload = ctx.codec_encode("sc_32055", {"result": 0})
        if not payload:
            raise ValueError("必要触摸回执 sc_32055 为空")
        return take_pending_frames(ctx) + [DownFrame(self.sc, payload)]


@operation
class CheckRecommendEquipOp(_PeripheralOkOp):
    cmd = 32042
    sc = 32043

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {"role_id": int(data.get("role_id") or 0)}


@operation
class CheckHeroVoiceOp(_PeripheralOkOp):
    cmd = 32044
    sc = 32045

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {"role_id": int(data.get("role_id") or 0)}


@operation
class ReceiveSkinGiftOp(_PeripheralOkOp):
    cmd = 32052
    sc = 32053

    def apply(self, data, ctx):
        ctx.uid = self.uid
        skin_id = int(data.get("skin_id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.claim_skin_gift(self.uid, skin_id)
        return {"skin_id": res}


@operation
class ChangeStickerShowOp(_PeripheralOkOp):
    cmd = 32056
    sc = 32057
    prepare_response_in_transaction = True

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32057", {"result": 0})
        if not payload:
            raise ValueError("必要贴纸展示回执sc_32057为空")
        return [DownFrame(self.sc, payload)]

    def apply(self, data, ctx):
        ctx.uid = self.uid
        page_id = data.get("page_id", 0)
        service = PeripheralService.get_instance(ctx.db)
        res = service.change_sticker_show_page(self.uid, page_id)
        return {"page_id": res}


@operation
class ReceiveStickerSuitRewardOp(Operation):
    cmd = 32058
    sc = 32059
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        rewards = service.claim_sticker_suit_reward(self.uid, data.get("reward_id_list", []), ctx=ctx)
        return {"reward_list": rewards}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32059", {"result": 0, "reward_list": result["reward_list"]})
        if not payload:
            raise ValueError("必要贴纸套装回执sc_32059为空")
        from operations import _refresh_frames
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, strict=True)


@operation
class DealOverdueSceneOp(_PeripheralOkOp):
    cmd = 32112
    sc = 32113

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {}


@operation
class ClearOverdueFrameIconOp(_PeripheralOkOp):
    cmd = 32040
    sc = 32041

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {}


@operation
class ClearOverduePortraitOp(_PeripheralOkOp):
    cmd = 32060
    sc = 32061

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {}


@operation
class ClearOverdueBubbleOp(_PeripheralOkOp):
    cmd = 32064
    sc = 32065

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {}


@operation
class ClearOverdueCardBgOp(_PeripheralOkOp):
    cmd = 32206
    sc = 32207

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {}


@operation
class ClearOverdueTagOp(_PeripheralOkOp):
    cmd = 32208
    sc = 32209

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {}


@operation
class QueryPortraitListOp(Operation):
    cmd = 32062
    sc = 32063

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        return {"icon_list": service.query_portraits(self.uid)}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32063", {"icon_list": result["icon_list"]}) or b"\\x08\\x00"
        return [DownFrame(self.sc, payload)]


@operation
class QueryBubbleListOp(Operation):
    cmd = 32066
    sc = 32067

    def apply(self, data, ctx):
        ctx.uid = self.uid
        service = PeripheralService.get_instance(ctx.db)
        return {"chat_bubble_list": service.query_bubbles(self.uid)}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_32067", {"chat_bubble_list": result["chat_bubble_list"]}) or b"\\x08\\x00"
        return [DownFrame(self.sc, payload)]


@operation
class RecordActiveBoardHeroOp(Operation):
    """大厅多看板娘/场景上报：Push(32132) -> 静默不回包。"""
    no_reply = True
    cmd = 32132
    sc = None

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hero_id = int(data.get("hero_id") or 0)
        bg_id = int(data.get("background_id") or 0)
        service = PeripheralService.get_instance(ctx.db)
        service.record_active_board_hero(self.uid, hero_id, bg_id)
        return {}

    def respond(self, result, data, ctx):
        return []


# ======================================================================
# 总线监听
# ======================================================================

@bus.subscribe(Events.HERO_UNLOCK)
def on_portrait_hero_unlock(ctx, uid, hero_id=0, **kwargs):
    if hero_id and getattr(ctx, 'db', None) is not None:
        PeripheralService(ctx.db).unlock_acquisition_portrait(uid, hero_id, ctx)


@bus.subscribe(Events.SKIN_UNLOCK)
def on_portrait_skin_unlock(ctx, uid, skin_id=0, **kwargs):
    if skin_id and getattr(ctx, 'db', None) is not None:
        PeripheralService(ctx.db).unlock_acquisition_portrait(uid, skin_id, ctx)


@bus.subscribe(Events.USER_LOGIN)
def on_peripheral_login(ctx, uid, is_first_login=False, **kwargs):
    """
    登录总线联动：
    1. 跨日重置今日点赞与今日看板娘触摸计数；
    2. 刷新看板娘上任时间戳为当前时间。
    """
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    # Recover portraits for legitimately owned assets from older saves too.
    acquired = {int(row['id']) for row in ctx.db.query('SELECT id FROM hero WHERE uid=? AND unlock=1', (uid,))}
    acquired.update(int(row['skin_id']) for row in ctx.db.query('SELECT skin_id FROM player_skin_unlocked WHERE uid=?', (uid,)))
    service = PeripheralService(ctx.db)
    for skin_id in sorted(acquired):
        service.unlock_acquisition_portrait(uid, skin_id)
    extra = _load_user_extra(ctx.db, uid)
    changed = False
    if "profile_like_today" in extra:
        extra.pop("profile_like_today", None)
        changed = True
    if "daily_touch_counts" in extra:
        extra.pop("daily_touch_counts", None)
        changed = True
    now_ts = int(time.time())
    extra["board_hero_last_ts"] = now_ts
    changed = True
    if changed:
        _save_user_extra(ctx.db, uid, extra)
        logger.debug(f"[peripheral] uid={uid} 登录状态已刷新 (点赞/触摸跨日清零)")
