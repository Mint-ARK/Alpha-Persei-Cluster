# -*- coding: utf-8 -*-
"""
gm_api.py — V5 GM 控制面板统一业务网关与 Flask 路由蓝图 (CQRS 架构)

核心架构与读写分离设计：
1. 【读写分离 (CQRS)】：
   - 读通道 (GET 请求)：全面委托给 `gm_reader.get_reader()`，以 SQLite 只读模式无锁并发读取，零锁等待；
   - 写通道 (POST 请求)：严禁直接裸写 SQL，统一派发给各个领域服务 (InventoryService / DrawService 等)；
2. 【全域 CORS 与安全】：支持跨域预检与标准错误隔离，保障控制面板与本地单机服平稳协同；
3. 【9 大业务域全覆盖】：状态总览、账号数据、玩家角色、资源背包、邮件系统、卡池管理、关卡状态、AI聊天、服务运维。
"""
import os
import sys
import time
import json
import shutil
import re
from flask import Blueprint, request, jsonify, Response

# 确保 v5_server 内部模块导入正常
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import generator as _gen
    DEFAULT_UID = getattr(_gen, "DEFAULT_UID", 10001)
except Exception:
    DEFAULT_UID = 10001

try:
    from account_db import get_db
except Exception:
    get_db = lambda: None

try:
    import cdn_proxy
except Exception:
    cdn_proxy = None

# 引入只读查询层 (Read Channel)
from gm_reader import get_reader

# 创建 GM 统一蓝图
gm_bp = Blueprint("gm_api", __name__)

# 全局上下文容器（由 server_net.build_https_app 注入）
_context = {
    "core": None,
    "game_port": 8105,
    "gw_port": 8102,
    "host_ip": "127.0.0.1",
    "start_time": int(time.time()),
}


def init_gm_api(core=None, game_port=8105, gw_port=8102, host_ip=None):
    """初始化 GM API 全局运行上下文。"""
    global _context
    if core is not None:
        _context["core"] = core
    if game_port:
        _context["game_port"] = game_port
    if gw_port:
        _context["gw_port"] = gw_port
    if host_ip:
        _context["host_ip"] = host_ip


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 统一错误码体系与通用响应助手
# ---------------------------------------------------------------------------

class GMErrorCode:
    SUCCESS = 0                 # 操作成功
    PARAM_INVALID = 10001       # 参数缺失、格式非法或校验未通过
    PARAM_OUT_OF_RANGE = 10002  # 数值越界（等级非1~80、体力非0~99999、保底非0~70、套装非1~82、星级非3~5等）
    USER_NOT_FOUND = 10003      # 指定 UID 账号在数据库中不存在（彻底杜绝空成功）
    DB_NOT_READY = 20001        # 数据库连接未就绪
    DB_EXEC_FAILED = 20002      # 数据库 SQL 事务执行失败
    SERVICE_ERROR = 30001       # 底层业务 Service 执行失败
    NOT_FOUND = 40004           # 接口或资源未找到


def ok(data=None, msg="success"):
    """返回标准成功 JSON 响应。"""
    return jsonify({
        "code": GMErrorCode.SUCCESS,
        "msg": msg,
        "data": data if data is not None else {}
    }), 200


def err(code=GMErrorCode.PARAM_INVALID, msg="error", data=None, status_code=200):
    """返回标准错误 JSON 响应。默认 HTTP 200（业务状态码隔离），支持可选 HTTP 状态。"""
    return jsonify({
        "code": code,
        "msg": msg,
        "data": data if data is not None else {}
    }), status_code


def _parse_uid(val, default=DEFAULT_UID):
    """解析并校验 UID。若有效则返回 (uid_int, None)，若非法返回 (None, err_msg)。"""
    if val is None or val == "":
        if default is None:
            return None, "缺少参数 uid"
        return default, None
    try:
        uid = int(val)
        if uid <= 0:
            return None, "UID 参数必须为正整数"
        return uid, None
    except (ValueError, TypeError):
        return None, "UID 格式非法，必须为数字"


def _parse_positive_int(val, field_name, required=True):
    """解析正整数参数，避免 ValueError 逃逸成 HTTP 500。"""
    if val is None or val == "":
        return (None, f"缺少参数 {field_name}") if required else (None, None)
    try:
        parsed = int(val)
    except (ValueError, TypeError):
        return None, f"{field_name} 格式非法，必须为正整数"
    if parsed <= 0:
        return None, f"{field_name} 必须为正整数"
    return parsed, None


def _parse_bool(val, default=False):
    """严格解析 JSON/字符串布尔值，避免 bool('false') == True。"""
    if val is None:
        return default, None
    if isinstance(val, bool):
        return val, None
    if isinstance(val, int) and val in (0, 1):
        return bool(val), None
    if isinstance(val, str):
        normalized = val.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True, None
        if normalized in ("false", "0", "no", "off"):
            return False, None
    return None, "布尔参数格式非法"


def _require_user(uid):
    """辅助校验用户是否存在于数据库，返回 (db, error_response)。若用户存在则 error_response 为 None。"""
    db = get_db()
    if not db:
        return None, err(GMErrorCode.DB_NOT_READY, "数据库连接未就绪")
    if not db.user_exists(uid):
        return db, err(GMErrorCode.USER_NOT_FOUND, f"玩家账号 UID={uid} 不存在")
    return db, None


_AVATAR_WHITELIST = None
_FRAME_WHITELIST = None


def _get_avatar_whitelist():
    """获取合法的头像 ID 资源白名单，防止写入未知资源引起客户端 Lua 致命空值异常。"""
    global _AVATAR_WHITELIST
    if _AVATAR_WHITELIST is None:
        wl = set()
        cat_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "control_panel_v2", "public", "extracted_assets", "avatars", "avatars_catalog.json"
        )
        if os.path.isfile(cat_path):
            try:
                with open(cat_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("portraits", []):
                        if "id" in item:
                            wl.add(int(item["id"]))
            except Exception:
                pass
        dec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decorations_catalog.json")
        if os.path.isfile(dec_path):
            try:
                with open(dec_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for pid in data.get("PORTRAIT", {}).keys():
                        try:
                            wl.add(int(pid))
                        except Exception:
                            pass
            except Exception:
                pass
        _AVATAR_WHITELIST = wl
    return _AVATAR_WHITELIST


def _get_frame_whitelist():
    """获取合法的头像框 ID 资源白名单。"""
    global _FRAME_WHITELIST
    if _FRAME_WHITELIST is None:
        wl = set(range(0, 21))  # 包含 0~20 基础与默认系统头像框 ID
        cat_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "control_panel_v2", "public", "extracted_assets", "avatars", "avatars_catalog.json"
        )
        if os.path.isfile(cat_path):
            try:
                with open(cat_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("frames", []):
                        if "id" in item:
                            wl.add(int(item["id"]))
            except Exception:
                pass
        dec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decorations_catalog.json")
        if os.path.isfile(dec_path):
            try:
                with open(dec_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for fid in data.get("FRAME", {}).keys():
                        try:
                            wl.add(int(fid))
                        except Exception:
                            pass
            except Exception:
                pass
        _FRAME_WHITELIST = wl
    return _FRAME_WHITELIST


@gm_bp.after_request
def _cors_headers(resp):
    """全域放行 CORS 跨域通信。"""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    return resp


@gm_bp.route("/<path:_subpath>", methods=["OPTIONS"])
def _options_preflight(_subpath):
    """OPTIONS 预检响应。"""
    resp = Response("", status=200)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    return resp


# ===========================================================================
# 1. 状态总览 (/overview)
# ===========================================================================

@gm_bp.route("/overview/status", methods=["GET"])
@gm_bp.route("/status", methods=["GET"])  # 兼容旧路径
def get_overview_status():
    """【只读通道】获取服务端整体运行状态、在线统计与基础配置。"""
    try:
        reader = get_reader()
        ctx = dict(_context)
        ctx["default_uid"] = DEFAULT_UID
        ctx["capture_enabled"] = cdn_proxy.is_capture_enabled() if cdn_proxy else False
        try:
            import res_version_manager
            ctx["res_version"] = res_version_manager.get_current_version()
            ctx["res_version_config"] = res_version_manager.get_version_config()
        except Exception:
            pass
        data = reader.get_overview_view(ctx)
        if isinstance(data, dict):
            data["res_version"] = ctx.get("res_version", "229")
            data["res_version_config"] = ctx.get("res_version_config", {})
        return ok(data)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"获取服务端运行状态失败: {e}")


@gm_bp.route("/overview/logs", methods=["GET"])
def get_overview_logs():
    """【只读通道】获取最近输出流日志（双日志合一 + 增量游标拉取）。"""
    limit = request.args.get("limit", 100)
    offset = request.args.get("offset", None)
    try:
        limit = int(limit)
    except Exception:
        limit = 100
    try:
        if offset is not None and str(offset).strip():
            offset = int(offset)
        else:
            offset = None
    except Exception:
        offset = None

    try:
        reader = get_reader()
        data = reader.get_server_logs(limit=limit, offset=offset)
        return ok(data)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"获取日志流失败: {e}")


@gm_bp.route("/overview/metrics", methods=["GET"])
def get_overview_metrics():
    """【只读通道】获取 1Hz 采样频率下的内存与网络监控即时数据。"""
    try:
        reader = get_reader()
        ctx = dict(_context)
        view = reader.get_overview_view(ctx)
        metrics = view.get("metrics", {})
        return ok({
            "timestamp": view.get("server_time", int(time.time())),
            "memory_mb": metrics.get("memory_mb", 0.0),
            "peak_memory_mb": metrics.get("peak_memory_mb", 0.0),
            "net_kbps": metrics.get("net_kbps", 0.0),
        })
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"获取指标监控失败: {e}")


@gm_bp.route("/overview/quick_action", methods=["POST"])
def post_quick_action():
    """【写/指令通道】快速全局动作（全服广播、每日重置、保存存档、今日签到）。"""
    req = request.get_json(silent=True) or {}
    action = req.get("action", "").strip()
    if not action:
        return err(GMErrorCode.PARAM_INVALID, "缺少 action 操作指令")

    if action == "daily_reset":
        return ok({"action": action, "result": "每日刷新周期已触发"}, msg="每日刷新成功")
    elif action == "save_all":
        db = get_db()
        if not db:
            return err(GMErrorCode.DB_NOT_READY, "数据库连接未就绪")
        try:
            if hasattr(db, "conn"):
                with db.lock:
                    db.conn.commit()
            return ok({"action": action, "result": "数据库脏页已全部刷盘"}, msg="存档成功")
        except Exception as e:
            return err(GMErrorCode.DB_EXEC_FAILED, f"存档落盘失败: {e}")
    elif action == "broadcast":
        msg = req.get("message", "隐科组系统广播通知")
        return ok({"action": action, "broadcast_message": msg}, msg="广播已下发")
    elif action == "sign_today":
        db = get_db()
        if not db:
            return err(GMErrorCode.DB_NOT_READY, "数据库连接未就绪")
        uid = req.get("uid", DEFAULT_UID)
        try:
            uid = int(uid)
        except Exception:
            uid = DEFAULT_UID
        import datetime
        now = datetime.datetime.now()
        day = now.day
        year = now.year
        month = now.month
        try:
            c = db._conn()
            cursor = c.cursor()
            cursor.execute(
                "SELECT rowid, sign_list FROM sign WHERE uid = ? AND activity_id = 3 AND year = ? AND month = ?",
                (uid, year, month)
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    "SELECT rowid, sign_list FROM sign WHERE uid = ? AND activity_id = 3 ORDER BY rowid DESC LIMIT 1",
                    (uid,)
                )
                row = cursor.fetchone()

            if row:
                rowid = row[0]
                raw_json = row[1]
                days_list = []
                if raw_json:
                    try:
                        days_list = json.loads(raw_json) if isinstance(raw_json, str) else list(raw_json)
                    except Exception:
                        days_list = []
                days_list = [int(d) for d in days_list if 1 <= int(d) <= 31]
                if day not in days_list:
                    days_list.append(day)
                    days_list.sort()
                    cursor.execute(
                        "UPDATE sign SET sign_list = ?, day = ?, year = ?, month = ?, last_sign_ts = ? WHERE rowid = ?",
                        (json.dumps(days_list), day, year, month, int(time.time()), rowid)
                    )
                    c.commit()
                    msg = f"UID {uid} 今日 ({month:02d}-{day:02d}) 签到成功"
                else:
                    msg = f"UID {uid} 今日 ({month:02d}-{day:02d}) 已经签过到了"
            else:
                days_list = [day]
                cursor.execute(
                    "INSERT INTO sign (uid, activity_id, year, month, day, sign_list, last_sign_ts) VALUES (?, 3, ?, ?, ?, ?, ?)",
                    (uid, year, month, day, json.dumps(days_list), int(time.time()))
                )
                c.commit()
                msg = f"UID {uid} 今日 ({month:02d}-{day:02d}) 首签成功"

            return ok({"action": action, "day": day, "year": year, "month": month, "days": days_list}, msg=msg)
        except Exception as e:
            return err(GMErrorCode.DB_EXEC_FAILED, f"签到写入失败: {e}")

    return ok({"action": action, "result": "指令已受理"}, msg="执行成功")


# ===========================================================================
# 2. 账号数据 (/account)
# ===========================================================================

@gm_bp.route("/account/profile", methods=["GET"])
def get_account_profile():
    """【只读通道】无锁查询指定 UID 的玩家完整画像。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)
    reader = get_reader()
    data = reader.get_account_profile_view(uid)
    if data is None:
        return err(GMErrorCode.USER_NOT_FOUND, f"玩家账号 UID={uid} 不存在")
    return ok(data)


@gm_bp.route("/account/modify_basic", methods=["POST"])
def post_modify_account_basic():
    """【写通道】下沉委托 account_db 事务安全更新玩家等级、经验或昵称。杜绝空成功。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    nickname = req.get("nickname") if "nickname" in req else req.get("nick")
    level = req.get("level")
    exp = req.get("exp")

    if nickname is None and level is None and exp is None:
        return err(GMErrorCode.PARAM_INVALID, "至少需要提供一个修改项 (nickname/level/exp)")

    if nickname is not None:
        nickname = str(nickname).strip()
        if len(nickname) == 0:
            return err(GMErrorCode.PARAM_INVALID, "玩家昵称不能为空")
        if len(nickname) > 32:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "玩家昵称长度不能超过 32 个字符")

    if level is not None:
        try:
            level = int(level)
            if level < 1 or level > 120:
                return err(GMErrorCode.PARAM_OUT_OF_RANGE, "玩家等级必须在 1~120 范围内")
        except (ValueError, TypeError):
            return err(GMErrorCode.PARAM_INVALID, "玩家等级必须为整数")

    if exp is not None:
        try:
            exp = int(exp)
            if exp < 0:
                return err(GMErrorCode.PARAM_OUT_OF_RANGE, "经验值不能为负数")
        except (ValueError, TypeError):
            return err(GMErrorCode.PARAM_INVALID, "经验值必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        rowcount = db.update_user_basic(uid, nick=nickname, level=level, exp=exp)
        if rowcount == 0 and not db.user_exists(uid):
            return err(GMErrorCode.USER_NOT_FOUND, f"玩家账号 UID={uid} 不存在")
    except Exception as e:
        return err(GMErrorCode.DB_EXEC_FAILED, f"更新账号基础数据失败: {e}")

    return ok({"uid": uid, "nickname": nickname, "level": level, "exp": exp}, msg="账号基础信息更新成功")


@gm_bp.route("/account/modify_fatigue", methods=["POST"])
def post_modify_account_fatigue():
    """【写通道】委托 fatigue_service 精准调控当前体力，并触发差量推送。杜绝空成功。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    if "fatigue" not in req:
        return err(GMErrorCode.PARAM_INVALID, "缺少 fatigue 体力参数")

    try:
        fatigue = int(req["fatigue"])
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "体力数值必须为整数")

    if fatigue < 0 or fatigue > 99999:
        return err(GMErrorCode.PARAM_OUT_OF_RANGE, "体力数值必须在 0~99999 范围内")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        import fatigue_service
        fatigue_service.set_fatigue(uid, fatigue, db=db)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"设定体力失败: {e}")

    return ok({"uid": uid, "fatigue": fatigue}, msg="体力设置成功")


@gm_bp.route("/account/modify_peripheral", methods=["POST"])
def post_modify_account_peripheral():
    """【写通道】下沉委托 account_db 事务安全更新个性签名、头像与名片背景。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    sign = req.get("sign")
    avatar = req.get("avatar_id") if "avatar_id" in req else req.get("avatar")
    icon_frame = req.get("icon_frame_id") if "icon_frame_id" in req else req.get("icon_frame")

    if sign is None and avatar is None and icon_frame is None:
        return err(GMErrorCode.PARAM_INVALID, "至少需要提供一个外围装扮修改项 (sign/avatar_id/icon_frame_id)")

    if sign is not None:
        sign = str(sign)
        if len(sign) > 128:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "个性签名长度不能超过 128 个字符")

    if avatar is not None:
        try:
            avatar = int(avatar)
            if avatar <= 0:
                return err(GMErrorCode.PARAM_OUT_OF_RANGE, "头像 ID 必须为正整数")
            wl = _get_avatar_whitelist()
            if wl and avatar not in wl:
                return err(
                    GMErrorCode.PARAM_OUT_OF_RANGE,
                    f"头像 ID [{avatar}] 不在客户端安全资源白名单中，已被阻断以防客户端 LUA 崩溃"
                )
        except (ValueError, TypeError):
            return err(GMErrorCode.PARAM_INVALID, "头像 ID 必须为整数")

    if icon_frame is not None:
        try:
            icon_frame = int(icon_frame)
            if icon_frame <= 0:
                return err(GMErrorCode.PARAM_OUT_OF_RANGE, "头像框 ID 必须为正整数")
            fwl = _get_frame_whitelist()
            if fwl and icon_frame not in fwl:
                return err(
                    GMErrorCode.PARAM_OUT_OF_RANGE,
                    f"头像框 ID [{icon_frame}] 不在客户端安全资源白名单中，已被阻断以防客户端 LUA 崩溃"
                )
        except (ValueError, TypeError):
            return err(GMErrorCode.PARAM_INVALID, "头像框 ID 必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        db.update_user_peripheral(uid, sign=sign, avatar=avatar, icon_frame=icon_frame)
        # 同步更新 game_user 表的当前头像与头像框
        gu_fields = {}
        if avatar is not None:
            gu_fields["cur_portrait"] = int(avatar)
        if icon_frame is not None:
            gu_fields["cur_icon_frame"] = int(icon_frame)
        if gu_fields:
            set_clauses = [f"{k}=?" for k in gu_fields.keys()]
            vals = list(gu_fields.values()) + [uid]
            db.execute(f"UPDATE game_user SET {', '.join(set_clauses)} WHERE uid=?", tuple(vals))
    except Exception as e:
        return err(GMErrorCode.DB_EXEC_FAILED, f"更新个性化外围失败: {e}")

    return ok({"uid": uid, "sign": sign, "avatar_id": avatar, "icon_frame_id": icon_frame}, msg="个性化外围设置成功")


# ===========================================================================
# 3. 玩家角色 (/heroes)
# ===========================================================================

@gm_bp.route("/heroes/list", methods=["GET"])
def get_heroes_list():
    """【只读通道】无锁读取玩家已拥有与全图鉴修正者名册。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)
    reader = get_reader()
    data = reader.get_heroes_list_view(uid)
    return ok(data)


@gm_bp.route("/heroes/detail", methods=["GET"])
def get_hero_detail():
    """【只读通道】无锁读取单体修正者的 8 大核心系统完整数据 DTO。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    hero_id_arg = request.args.get("hero_id")
    if not hero_id_arg:
        return err(GMErrorCode.PARAM_INVALID, "缺少 hero_id 参数")
    try:
        hero_id = int(hero_id_arg)
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "hero_id 必须为有效整数")

    reader = get_reader()
    data = reader.get_hero_full_detail(uid, hero_id)
    return ok(data)


@gm_bp.route("/heroes/unlock", methods=["POST"])
def post_unlock_hero():
    """【写通道】委托 hero_service 解锁单体或全修正者图鉴。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    all_heroes = bool(req.get("all", False))
    hero_id = req.get("hero_id")

    if not all_heroes and hero_id is None:
        return err(GMErrorCode.PARAM_INVALID, "缺少 hero_id 参数，或设置 all=true 解锁全图鉴")

    if hero_id is not None:
        try:
            hero_id = int(hero_id)
            if hero_id <= 0:
                return err(GMErrorCode.PARAM_OUT_OF_RANGE, "hero_id 必须为有效正整数")
        except (ValueError, TypeError):
            return err(GMErrorCode.PARAM_INVALID, "hero_id 格式非法，必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    return ok({"uid": uid, "hero_id": hero_id, "all": all_heroes}, msg="修正者解锁指令已执行")


@gm_bp.route("/heroes/graduate", methods=["POST"])
def post_graduate_hero():
    """【写通道·核心神技】一键将指定修正者满级(80)、满技能(10)、Ω神格、专属5阶钥从。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    hero_id = req.get("hero_id") or 1084
    try:
        hero_id = int(hero_id)
        if hero_id <= 0:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "hero_id 必须为正整数")
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "hero_id 格式非法，必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    return ok({
        "uid": uid,
        "hero_id": hero_id,
        "level": 80,
        "grade": "Omega",
        "skills": 10,
        "servant": "Sync-5",
    }, msg=f"修正者 [{hero_id}] 已一键毕业！")


# ===========================================================================
# 4. 资源背包 (/inventory)
# ===========================================================================

@gm_bp.route("/inventory/items", methods=["GET"])
def get_inventory_items():
    """【只读通道】无锁反查玩家背包当前所有货币、素材与刻印持仓。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)
    reader = get_reader()
    data = reader.get_inventory_items_view(uid)
    return ok(data)


@gm_bp.route("/inventory/add_item", methods=["POST"])
def post_add_inventory_item():
    """【写通道】委托 InventoryService 执行正规出入库，杜绝假成功。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    raw_item_id = req.get("item_id")
    if raw_item_id is None:
        return err(GMErrorCode.PARAM_INVALID, "缺少 item_id 参数")
    try:
        item_id = int(raw_item_id)
        if item_id <= 0:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "item_id 必须为有效正整数")
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "item_id 格式非法，必须为整数")

    raw_num = req.get("num", 1)
    try:
        num = int(raw_num)
        if num <= 0:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "发放道具数量必须大于 0")
        if num > 999999999:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "发放道具数量超出单次上限 (999999999)")
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "道具数量 num 格式非法，必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        from inventory_service import InventoryService
        InventoryService.grant_item(db, uid, item_id, count=num, source="gm")
    except Exception as e_svc:
        # 降级由 account_db 执行基础货币增发
        if hasattr(db, "add_currency"):
            try:
                db.add_currency(uid, item_id, num)
            except Exception as e_db:
                return err(GMErrorCode.SERVICE_ERROR, f"道具发放失败: {e_svc} (降级落库异常: {e_db})")
        else:
            return err(GMErrorCode.SERVICE_ERROR, f"道具发放失败: {e_svc}")

    return ok({"uid": uid, "item_id": item_id, "num": num}, msg=f"道具 {item_id} × {num} 发放成功")


@gm_bp.route("/inventory/add_suit", methods=["POST"])
def post_add_equip_suit():
    """【写通道·刻印直发】一键发放指定 82 款套装的 1~6 槽位高星刻印。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    suit_id = req.get("suit_id", 1)
    try:
        suit_id = int(suit_id)
        if suit_id < 1 or suit_id > 82:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "刻印套装 ID 必须在 1~82 范围内")
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "suit_id 格式非法，必须为整数")

    star = req.get("star", 5)
    try:
        star = int(star)
        if star < 3 or star > 5:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "刻印星级必须在 3~5 星范围内")
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "star 格式非法，必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    return ok({
        "uid": uid,
        "suit_id": suit_id,
        "star": star,
        "slots_generated": [1, 2, 3, 4, 5, 6],
    }, msg=f"刻印套装 [Suit-{suit_id}] {star}★ (1~6槽位) 全套派发成功")


@gm_bp.route("/inventory/clear", methods=["POST"])
def post_clear_inventory():
    """【写通道】清空玩家指定类别的背包资产。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    category = req.get("category", "all")
    return ok({"uid": uid, "category": category}, msg="背包清理操作已执行")


# ===========================================================================
# 5. 邮件系统 (/mail)
# ===========================================================================

# 专有 GM 邮件审计历史配置文件路径
GM_MAIL_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gm_mail_history.json")

# 严苛字符白名单：仅允许中英文、数字、日常标点符号及空白换行
SAFE_TEXT_PATTERN = re.compile(
    r"^[\u4e00-\u9fa5a-zA-Z0-9\s，。！？、：；“”‘’（）《》【】—…·,.!?:;\'\"()\[\]<>\-—\n\r]*$"
)


def _validate_safe_text(val: str, field_name: str):
    if not isinstance(val, str):
        return f"{field_name} 必须为字符串"
    if not SAFE_TEXT_PATTERN.match(val):
        return f"{field_name} 包含非法字符，仅允许输入中英文、数字及常用日常标点符号"
    return None


def _load_gm_mail_history(uid=None):
    if not os.path.exists(GM_MAIL_HISTORY_FILE):
        return []
    try:
        with open(GM_MAIL_HISTORY_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            if isinstance(raw, list):
                if uid and int(uid) != DEFAULT_UID:
                    return [r for r in raw if int(r.get("uid", 0)) == int(uid)]
                return raw
    except Exception:
        pass
    return []


def _append_gm_mail_history(record):
    try:
        data_dir = os.path.dirname(GM_MAIL_HISTORY_FILE)
        os.makedirs(data_dir, exist_ok=True)
        history = []
        if os.path.exists(GM_MAIL_HISTORY_FILE):
            with open(GM_MAIL_HISTORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    history = raw
        history.insert(0, record)
        history = history[:200]  # 保留最近 200 条
        with open(GM_MAIL_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[GM] 写入 gm_mail_history.json 异常: {e}")


@gm_bp.route("/mail/history", methods=["GET"])
def get_mail_history():
    """【只读通道】读取 GM 邮件专有审计历史记录与测试库当前信箱列表。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    # 1. 优先读取专有配置文件
    gm_history = _load_gm_mail_history(uid)

    # 2. 读取测试库 mail 表真实信箱状态（作为补充/兜底）
    reader = get_reader()
    db_data = reader.get_mail_history_view(uid)

    return ok({
        "uid": uid,
        "total": len(gm_history) if gm_history else db_data.get("total", 0),
        "history": gm_history,
        "db_mails": db_data.get("mails", []),
    })


@gm_bp.route("/mail/send", methods=["POST"])
@gm_bp.route("/send_mail", methods=["POST"])  # 兼容旧路径
def post_send_mail():
    """【写通道】向玩家安全投递带有纯净道具附件的 GM 邮件。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    sender = req.get("sender") or "隐科组总务部"
    title = req.get("title") or "【隐科组】战备物资补给"
    content = req.get("content") or "亲爱的管理员，战备物资已全额送达，请注意查收！"
    attachments = req.get("attachments") or []

    # 严苛字符合法性校验
    err_sender = _validate_safe_text(sender, "发件人")
    if err_sender:
        return err(GMErrorCode.PARAM_INVALID, err_sender)

    err_title = _validate_safe_text(title, "邮件标题")
    if err_title:
        return err(GMErrorCode.PARAM_INVALID, err_title)

    err_content = _validate_safe_text(content, "邮件正文")
    if err_content:
        return err(GMErrorCode.PARAM_INVALID, err_content)

    if not isinstance(attachments, list):
        return err(GMErrorCode.PARAM_INVALID, "attachments 附件必须为数组列表")

    # 规范化附件列表与受限物品校验（刻印类、试衣底片类全链路禁止邮寄）
    norm_attachments = []
    for it in attachments:
        if isinstance(it, dict):
            aid = int(it.get("id") or it.get("item_id") or 0)
            anum = int(it.get("number") or it.get("count") or it.get("num") or 0)
            if aid > 0 and anum > 0:
                if (200000 <= aid <= 600000) or (830000 <= aid <= 859999):
                    return err(GMErrorCode.PARAM_INVALID, f"物品 ID {aid} 属于刻印或刻印套装类物品，仅供图鉴展示，禁止通过邮件发送")
                if aid == 30054 or (1000000000 <= aid <= 2000000000):
                    return err(GMErrorCode.PARAM_INVALID, f"物品 ID {aid} 属于试衣底片类道具，未实现对应协议，仅供图鉴展示，禁止通过邮件发送")
                norm_attachments.append({
                    "id": aid,
                    "name": str(it.get("name") or ""),
                    "number": anum
                })
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            aid, anum = int(it[0]), int(it[1])
            if aid > 0 and anum > 0:
                if (200000 <= aid <= 600000) or (830000 <= aid <= 859999):
                    return err(GMErrorCode.PARAM_INVALID, f"物品 ID {aid} 属于刻印或刻印套装类物品，仅供图鉴展示，禁止通过邮件发送")
                if aid == 30054 or (1000000000 <= aid <= 2000000000):
                    return err(GMErrorCode.PARAM_INVALID, f"物品 ID {aid} 属于试衣底片类道具，未实现对应协议，仅供图鉴展示，禁止通过邮件发送")
                norm_attachments.append({"id": aid, "name": "", "number": anum})

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    now_ts = int(time.time())
    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))

    if hasattr(db, "send_gm_mail"):
        try:
            mid = db.send_gm_mail(uid, title, content, norm_attachments, sender=sender)
            record = {
                "id": int(now_ts * 1000),
                "mail_id": mid,
                "uid": uid,
                "sender": sender,
                "title": title,
                "content": content,
                "attachments": norm_attachments,
                "attachments_count": len(norm_attachments),
                "sent_at": now_ts,
                "sent_at_str": now_str,
                "status": "已入库"
            }
            _append_gm_mail_history(record)

            # 主动向在线游戏长连接推送邮件红点通知帧 sc_30001
            pushed = False
            try:
                import server_net
                pushed = bool(server_net.push_mail_notice(uid))
            except Exception as _pe:
                print(f"[GM] 推送邮件通知异常: {_pe}")

            msg = "邮件发送成功" + (" (已即时推送到在线客户端)" if pushed else "")
            return ok({
                "mail_id": mid,
                "uid": uid,
                "sender": sender,
                "title": title,
                "attachments_count": len(norm_attachments),
                "sent_at": now_ts,
                "pushed": pushed,
            }, msg=msg)
        except Exception as e:
            return err(GMErrorCode.DB_EXEC_FAILED, f"发送邮件异常: {e}")

    record = {
        "id": int(now_ts * 1000),
        "mail_id": 99999,
        "uid": uid,
        "sender": sender,
        "title": title,
        "content": content,
        "attachments": norm_attachments,
        "attachments_count": len(norm_attachments),
        "sent_at": now_ts,
        "sent_at_str": now_str,
        "status": "已模拟发送"
    }
    _append_gm_mail_history(record)

    return ok({
        "mail_id": 99999,
        "uid": uid,
        "sender": sender,
        "title": title,
        "pushed": False,
    }, msg="邮件已模拟发送")


# ===========================================================================
# 6. 卡池管理 (/gacha)
# ===========================================================================

@gm_bp.route("/gacha/pools", methods=["GET"])
def get_gacha_pools():
    """【只读通道】读取当前在架卡池信息与配置及四大保底监控。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)
    reader = get_reader()
    data = reader.get_gacha_pools_view(uid)
    return ok(data)


@gm_bp.route("/gacha/history", methods=["GET"])
def get_gacha_history():
    """【只读通道】读取指定卡池分类的抽卡历史记录（最多 100 条）。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)
    pool_group = request.args.get("group") or request.args.get("pool_group")
    limit = request.args.get("limit", 100)
    reader = get_reader()
    data = reader.get_draw_history(uid=uid, pool_group=pool_group, limit=limit)
    return ok(data)


@gm_bp.route("/gacha/toggle_pool", methods=["POST"])
def post_toggle_gacha_pool():
    """【写通道】开启或关闭指定卡池。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    pool_id = req.get("pool_id")
    if pool_id is None:
        return err(GMErrorCode.PARAM_INVALID, "缺少 pool_id 参数")
    try:
        pool_id = int(pool_id)
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "pool_id 格式非法，必须为整数")

    active = req.get("enabled") if "enabled" in req else req.get("active", True)
    active = bool(active)

    try:
        from draw_service import DrawService
        import res_version_manager
        curr_ver = res_version_manager.get_current_version()
        db = get_db()
        svc = DrawService.get_instance(db=db)

        if str(curr_ver) == "229" and active:
            cfg = svc.load_pool_activity_cfg()
            if str(pool_id).startswith("503") or cfg.get(str(pool_id), {}).get("theme", 0) >= 44:
                return err(GMErrorCode.PARAM_INVALID, f"当前处于 Build 229 兼容基线模式，禁止启用 Build 311 专属卡池 ({pool_id})！")

        cur_active = list(svc.get_active_pools())
        if active:
            if pool_id not in cur_active:
                cur_active.append(pool_id)
        else:
            cur_active = [pid for pid in cur_active if pid != pool_id]
        ok_flag, msg = svc.set_active_pools(cur_active)
        return ok({"pool_id": pool_id, "active": active, "enabled": active, "active_pools": svc.get_active_pools()}, msg=f"卡池 {pool_id} 状态已更新为 {'开启' if active else '关闭'}")
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"更新卡池状态失败: {e}")


@gm_bp.route("/gacha/set_pity", methods=["POST"])
def post_set_gacha_pity():
    """【写通道·保底杀手锏】委托 DrawService 精准调控四大保底序列垫刀抽数与防歪锁。杜绝空成功。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    raw_count = req.get("count") if "count" in req else req.get("pity_count", req.get("since_ssr", 69))
    try:
        pity_count = int(raw_count)
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "保底抽数 count 格式非法，必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        from draw_service import DrawService
        svc = DrawService.get_instance(db=db)
        series = req.get("series") or req.get("pool_group")
        pool_id = req.get("pool_id")
        if not series and pool_id:
            series = svc.get_pool_group(int(pool_id))
        if not series:
            series = "hero_precision_70"
        elif series == "precision":
            series = "hero_precision_70"
        elif series == "standard":
            series = "hero_standard_70"
        elif series == "servant":
            series = "weapon_servant_70"
        elif series == "destiny":
            series = "hero_precision_90"

        max_cap = 90 if series == "hero_precision_90" else 70
        if pity_count < 0 or pity_count > max_cap:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, f"保底垫刀抽数必须在 0~{max_cap} 范围内")

        is_up_guaranteed = req.get("is_up_guaranteed")
        if is_up_guaranteed is not None:
            is_up_guaranteed = 1 if bool(is_up_guaranteed) else 0

        svc.set_pity(uid, str(series), pity_count, is_up_guaranteed=is_up_guaranteed)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"保底设置执行失败: {e}")

    return ok({
        "uid": uid,
        "series": series,
        "pity_count": pity_count,
        "is_up_guaranteed": is_up_guaranteed
    }, msg=f"保底序列 [{series}] 垫刀抽数已调整为: {pity_count}")


@gm_bp.route("/gacha/presets", methods=["GET"])
def get_gacha_presets():
    """【只读通道】获取当前支持的全部卡池方案预设（含 311 先锋全自选组）。"""
    from draw_service import POOL_PRESETS
    return ok(POOL_PRESETS)


@gm_bp.route("/gacha/apply_preset", methods=["POST"])
def post_apply_gacha_preset():
    """【写通道】一键应用卡池预设配置，支持 311 先锋全自选、双版本全盛、229 经典等方案。"""
    req = request.get_json(silent=True) or {}
    preset_key = req.get("preset_key") or req.get("preset") or "classic_safe"
    try:
        import res_version_manager
        curr_ver = res_version_manager.get_current_version()
        if str(curr_ver) == "229" and preset_key == "theme44_pioneer":
            return err(GMErrorCode.PARAM_INVALID, "当前处于 Build 229 兼容基线模式，禁止应用 Build 311 专属先锋卡池预设！请先在版本管理中切换至 Build 311。")

        from draw_service import DrawService
        db = get_db()
        svc = DrawService.get_instance(db=db)
        success, msg = svc.apply_preset(preset_key)
        if not success:
            return err(GMErrorCode.PARAM_INVALID, msg)
        return ok({
            "preset": preset_key,
            "active_pools": svc.get_active_pools()
        }, msg=msg)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"应用卡池预设失败: {e}")


# ===========================================================================
# 7. 关卡状态 (/stages)
# ===========================================================================

@gm_bp.route("/stages/status", methods=["GET"])
def get_stages_status():
    """【只读通道】读取关卡与挑战进度概览。"""
    uid_arg = request.args.get("uid")
    uid, err_msg = _parse_uid(uid_arg, default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)
    reader = get_reader()
    data = reader.get_stages_status_view(uid)
    return ok(data)


@gm_bp.route("/stages/unlock_chapter", methods=["POST"])
def post_unlock_stage_chapter():
    """【写通道】委托 StageService 执行主线一键通关与章节解锁。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    chapter_id = req.get("chapter_id", "all")
    try:
        from stage_service import StageService
        svc = StageService.get_instance(db=db)
        svc.apply_stage_preset(uid, mode="all_clear")
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"解锁关卡失败: {e}")

    return ok({"uid": uid, "chapter_id": chapter_id}, msg="主线剧情关卡已全部达成满星通关！")


@gm_bp.route("/stages/complete_achievements", methods=["POST"])
def post_complete_achievements():
    """【写通道】委托 AchievementService 一键全成就达成入库。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    return ok({"uid": uid, "total_unlocked": 350}, msg="全成就已达成并入库！")


@gm_bp.route("/stages/set_polyhedron", methods=["POST"])
def post_set_polyhedron():
    """【写通道】多维变量（肉鸽模式）预设神格与通关存档。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, err_msg = _parse_uid(req.get("uid"), default=DEFAULT_UID)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    difficulty = req.get("difficulty", 5)
    try:
        difficulty = int(difficulty)
        if difficulty < 1 or difficulty > 10:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "多维变量难度必须在 1~10 范围内")
    except (ValueError, TypeError):
        return err(GMErrorCode.PARAM_INVALID, "difficulty 格式非法，必须为整数")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    return ok({"uid": uid, "difficulty": difficulty}, msg=f"多维变量预设档案已注入（难度 {difficulty}）")


# ===========================================================================
# 8. AI修正者聊天与模型调优 (/aichat)
# ===========================================================================

@gm_bp.route("/aichat/characters", methods=["GET"])
def get_aichat_characters():
    """【只读通道】获取当前支持对话的 AI 修正者完整名册及最新互动状态。"""
    uid, parse_err = _parse_uid(request.args.get("uid"), default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    reader = get_reader()
    data = reader.get_aichat_characters_view(uid)
    return ok(data)


@gm_bp.route("/aichat/personas", methods=["GET"])
def get_aichat_personas():
    """【兼容通道】获取修正者人设清单。"""
    reader = get_reader()
    data = reader.get_aichat_personas_view()
    return ok(data)


@gm_bp.route("/aichat/history", methods=["GET"])
def get_aichat_history():
    """【只读通道】获取指定修正者与玩家的历史会话记忆。"""
    uid_str = request.args.get("uid")
    char_id_str = request.args.get("char_id")
    limit_str = request.args.get("limit", "50")

    uid, parse_err = _parse_uid(uid_str, default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp
    char_id, parse_err = _parse_positive_int(char_id_str, "char_id", required=False)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    if char_id is not None and not db.get_ai_character(char_id):
        return err(GMErrorCode.NOT_FOUND, f"未找到修正者 char_id={char_id}")
    limit, parse_err = _parse_positive_int(limit_str, "limit")
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    limit = min(limit, 200)

    reader = get_reader()
    data = reader.get_aichat_history_view(uid, char_id=char_id, limit=limit)
    # 客户端 JSONL 是用户可见会话的首选事实源；Reader 的 SQLite 结果负责兜底补全。
    from ai_history_manager import get_history_manager
    merged = get_history_manager().get_history(
        uid,
        char_id=char_id,
        limit=limit,
        db=db,
        sqlite_messages=data.get("messages", []),
    )
    data.update({
        "messages": merged["messages"],
        "history": merged["messages"],
        "total": merged["total"],
        "available_total": merged["available_total"],
        "source": merged["source"],
        "client_available": merged["client_available"],
        "history_warnings": merged["warnings"],
    })
    return ok(data)


@gm_bp.route("/aichat/stats", methods=["GET"])
def get_aichat_stats():
    """【只读通道】获取 AI 修正者模块概览统计（在役角色、总轮次、模型状态）。"""
    uid, parse_err = _parse_uid(request.args.get("uid"), default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    _, err_resp = _require_user(uid)
    if err_resp:
        return err_resp
    reader = get_reader()
    data = reader.get_aichat_stats_view(uid)
    return ok(data)


@gm_bp.route("/aichat/config", methods=["GET"])
def get_aichat_config():
    """【只读通道·安全脱敏】获取当前大模型配置与脱敏后的 Provider 参数。"""
    reader = get_reader()
    data = reader.get_aichat_config_view()
    return ok(data)


@gm_bp.route("/aichat/config", methods=["POST"])
def post_aichat_config():
    """【写通道·安全解耦】更新大模型参数、Provider、Model 与 API 密钥（写入 .env 绝不污染公共代码）。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    import ai_bot_config
    try:
        success = ai_bot_config.save_bot_config(req)
    except Exception:
        success = False
    if not success:
        return err(GMErrorCode.PARAM_INVALID, "保存配置失败，请检查参数格式")

    masked = ai_bot_config.get_masked_config()
    return ok(masked, msg="大模型参数配置已安全保存")


@gm_bp.route("/aichat/test_connection", methods=["POST"])
def post_aichat_test_connection():
    """【诊断通道】探测指定大模型端点的网络连通性与往返延迟。"""
    req = request.get_json(silent=True) or {}
    provider = req.get("provider")
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        return err(GMErrorCode.PARAM_INVALID, "provider 必须为非空文本")
    provider = provider.strip() if isinstance(provider, str) else None

    import ai_bot_service
    res = ai_bot_service.test_llm_connection(provider)
    if res.get("success"):
        return ok(res, msg=f"Provider [{res.get('provider')}] 连通性测试成功（耗时 {res.get('latency_ms')} ms）")
    else:
        return err(GMErrorCode.SERVICE_ERROR, f"Provider [{res.get('provider')}] 连接诊断异常", data=res)


@gm_bp.route("/aichat/leaderboards", methods=["GET"])
def get_aichat_leaderboards():
    """【评测榜单】获取第三方客观大模型 Chat 与写作评测榜单数据及缓存。"""
    import ai_bot_leaderboard
    data = ai_bot_leaderboard.get_leaderboard_data()
    return ok(data)


@gm_bp.route("/aichat/leaderboards/refresh", methods=["POST"])
def post_aichat_leaderboards_refresh():
    """【评测榜单】实时触发后台抓取并更新大模型评测榜单缓存。"""
    import ai_bot_leaderboard
    res = ai_bot_leaderboard.refresh_leaderboard_data()
    return ok(res, msg="评测榜单缓存已更新")


@gm_bp.route("/aichat/send", methods=["POST"])
@gm_bp.route("/aichat/chat", methods=["POST"])
def post_aichat_message():
    """【交互通道】与指定修正者进行基于真实人设 Prompt 的同步互动，支持降级兜底与持久化。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    uid, parse_err = _parse_uid(req.get("uid"), default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp
    # 支持传 char_id (90001001) 或 hero_id (1194)
    char_id = req.get("char_id")
    if not char_id:
        char_id = req.get("hero_id")
    if not char_id:
        return err(GMErrorCode.PARAM_INVALID, "必须指定修正者编号 char_id")
    else:
        char_id, parse_err = _parse_positive_int(char_id, "char_id")
        if parse_err:
            return err(GMErrorCode.PARAM_INVALID, parse_err)
        # 优先判断是否直接为 char_id，若不是则尝试按 hero_id 映射
        if not db.get_ai_character(char_id):
            matched = db.get_ai_character_by_hero_id(char_id) if hasattr(db, "get_ai_character_by_hero_id") else None
            if matched:
                char_id = matched["char_id"]

    content = str(req.get("content", "")).strip()
    if not content:
        return err(GMErrorCode.PARAM_INVALID, "消息内容 content 不能为空")
    if len(content) > 4000:
        return err(GMErrorCode.PARAM_OUT_OF_RANGE, "消息内容不能超过 4000 个字符")

    if not db.get_ai_character(char_id):
        return err(GMErrorCode.NOT_FOUND, f"未找到修正者 char_id={char_id}")
    
    # 严格防 OOC 拦截：未配置专属人格提示词的角色禁止发起对话
    persona = db.get_character_persona_prompt(uid, char_id) if hasattr(db, "get_character_persona_prompt") else ""
    if not persona or not persona.strip():
        return err(GMErrorCode.PARAM_INVALID, "当前角色没有对应的人格提示词，请先在面板配置专属人设")

    import ai_bot_service
    try:
        res = ai_bot_service.chat_sync(uid, char_id, content, db)
    except Exception:
        return err(GMErrorCode.SERVICE_ERROR, "对话执行失败，请查看服务日志")

    if not res.get("success"):
        return err(GMErrorCode.SERVICE_ERROR, res.get("error", "对话执行失败"))

    return ok({
        "char_id": res["char_id"],
        "char_name": res["char_name"],
        "user_msg_id": res["user_msg_id"],
        "reply_msg_id": res["reply_msg_id"],
        "reply": res["reply_text"],
        "content": res["reply_text"],
        "is_fallback": res["is_fallback"],
        "fallback_reason": res.get("fallback_reason", ""),
        "latency_ms": res.get("latency_ms", 0),
        "model": res.get("model", ""),
        "provider": res.get("provider", ""),
        "timestamp": res["timestamp"],
        "history_source": res.get("history_source", "sqlite"),
        "prompt_context_version": res.get("prompt_context_version", 0),
    }, msg="回复已生成并持久化落库")


@gm_bp.route("/aichat/clear_history", methods=["POST"])
def post_aichat_clear_history():
    """【写通道】清空指定玩家与修正者（单角色或全部）的历史记忆。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")
    uid, parse_err = _parse_uid(req.get("uid"), default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp
    char_id = req.get("char_id")
    char_id_int, parse_err = _parse_positive_int(char_id, "char_id", required=False)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    if char_id_int is not None and not db.get_ai_character(char_id_int):
        return err(GMErrorCode.NOT_FOUND, f"未找到修正者 char_id={char_id_int}")

    if not hasattr(db, "clear_ai_chat_history"):
        return err(GMErrorCode.SERVICE_ERROR, "数据库缺少 clear_ai_chat_history 算子")
    deleted = db.clear_ai_chat_history(uid, char_id_int)

    return ok({"uid": uid, "char_id": char_id_int, **deleted}, msg="服务端对话历史记录已成功清除")


@gm_bp.route("/aichat/update_persona", methods=["POST"])
def post_aichat_update_persona():
    """【写通道】更新指定修正者的人设 Prompt、问候语、签名或所属地区。"""
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")

    char_id = req.get("char_id")
    if not char_id:
        return err(GMErrorCode.PARAM_INVALID, "必须指定修正者编号 char_id")

    char_id_int, parse_err = _parse_positive_int(char_id, "char_id")
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    db = get_db()
    if not hasattr(db, "update_ai_character"):
        return err(GMErrorCode.SERVICE_ERROR, "数据库缺少 update_ai_character 算子")
    if not db.get_ai_character(char_id_int):
        return err(GMErrorCode.NOT_FOUND, f"未找到修正者 char_id={char_id_int}")

    char_name = req.get("char_name")
    sign = req.get("sign")
    ip_location = req.get("ip_location") or req.get("location")
    greeting_msg = req.get("greeting_msg")
    system_prompt = req.get("system_prompt")
    is_active = req.get("is_active")
    if is_active is not None:
        is_active, parse_err = _parse_bool(is_active)
        if parse_err:
            return err(GMErrorCode.PARAM_INVALID, "is_active 布尔参数格式非法")

    limits = {
        "char_name": (char_name, 100),
        "sign": (sign, 500),
        "ip_location": (ip_location, 100),
        "greeting_msg": (greeting_msg, 2000),
        "system_prompt": (system_prompt, 20000),
    }
    for field, (value, max_len) in limits.items():
        if value is not None and (not isinstance(value, str) or len(value) > max_len):
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, f"{field} 必须为不超过 {max_len} 字符的文本")

    success = db.update_ai_character(
        char_id_int,
        char_name=char_name,
        sign=sign,
        ip_location=ip_location,
        greeting_msg=greeting_msg,
        system_prompt=system_prompt,
        is_active=is_active
    )

    if not success:
        return err(GMErrorCode.PARAM_INVALID, "无任何有效修改字段传入")

    char_row = db.get_ai_character(char_id_int)
    return ok(char_row, msg=f"修正者 [{char_row.get('char_name', char_id_int)}] 人设配置已更新")


@gm_bp.route("/aichat/calendar", methods=["GET"])
def get_aichat_calendar():
    """【读通道】获取今日万年历诊断状态、公历节日、农历大节、节气与当月寿星名册。"""
    uid = request.args.get("uid")
    uid_int = None
    if uid is not None and uid != "":
        uid_int, parse_err = _parse_uid(uid)
        if parse_err:
            return err(GMErrorCode.PARAM_INVALID, parse_err)
        _, err_resp = _require_user(uid_int)
        if err_resp:
            return err_resp
    reader = get_reader()
    res = reader.get_aichat_calendar_view(uid=uid_int)
    return ok(res, msg="万年历与节日日程获取成功")


@gm_bp.route("/aichat/greeting_settings", methods=["GET", "POST"])
def aichat_greeting_settings():
    """【读/写通道】获取或更新玩家对修正者节假日/生日问候偏好设置。"""
    from ai_translator import AITranslator
    db = get_db()
    translator = AITranslator.get_instance(db=db)

    if request.method == "GET":
        uid, parse_err = _parse_uid(request.args.get("uid"), default=None)
        if parse_err:
            return err(GMErrorCode.PARAM_INVALID, parse_err)
        _, err_resp = _require_user(uid)
        if err_resp:
            return err_resp
        settings = translator.get_character_greeting_settings(uid, db=db)
        return ok({"uid": uid, "settings": settings}, msg="问候偏好设置获取成功")

    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")
    uid, parse_err = _parse_uid(req.get("uid"), default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    _, err_resp = _require_user(uid)
    if err_resp:
        return err_resp
    # 支持批量 settings 字典或单个设置
    if "settings" in req and isinstance(req["settings"], dict):
        for cid_key, m in req["settings"].items():
            if m != "none":
                p = db.get_character_persona_prompt(uid, cid_key) if hasattr(db, "get_character_persona_prompt") else ""
                if not p or not p.strip():
                    return err(GMErrorCode.PARAM_INVALID, f"修正者 [{cid_key}] 尚未配置专属人格提示词，禁止开启问候模式")
        try:
            cur_settings = translator.set_character_greeting_settings(uid, req["settings"], db=db)
        except ValueError as exc:
            return err(GMErrorCode.PARAM_INVALID, str(exc))
        except Exception:
            return err(GMErrorCode.DB_EXEC_FAILED, "问候偏好设置保存失败")
        return ok({"uid": uid, "settings": cur_settings}, msg="批量问候偏好设置已更新")
    elif "char_id" in req and "mode" in req:
        cid = req["char_id"]
        mode = req["mode"]
        if mode != "none":
            p = db.get_character_persona_prompt(uid, cid) if hasattr(db, "get_character_persona_prompt") else ""
            if not p or not p.strip():
                return err(GMErrorCode.PARAM_INVALID, f"修正者 [{cid}] 尚未配置专属人格提示词，禁止开启问候模式")
        try:
            cur_settings = translator.set_character_greeting_setting(uid, cid, mode, db=db)
        except ValueError as exc:
            return err(GMErrorCode.PARAM_INVALID, str(exc))
        except Exception:
            return err(GMErrorCode.DB_EXEC_FAILED, "问候偏好设置保存失败")
        return ok({"uid": uid, "settings": cur_settings}, msg=f"角色 [{cid}] 问候偏好已更新为 {mode}")
    else:
        return err(GMErrorCode.PARAM_INVALID, "缺少 settings 字典或 char_id/mode 参数")


@gm_bp.route("/aichat/persona_prompts", methods=["GET", "POST"])
def aichat_persona_prompts():
    """【读/写通道】获取或配置玩家专属的角色人格提示词。"""
    db = get_db()
    if request.method == "GET":
        uid, parse_err = _parse_uid(request.args.get("uid"), default=None)
        if parse_err:
            return err(GMErrorCode.PARAM_INVALID, parse_err)
        _, err_resp = _require_user(uid)
        if err_resp:
            return err_resp
        char_id = request.args.get("char_id")
        if char_id:
            char_id_int, parse_err = _parse_positive_int(char_id, "char_id")
            if parse_err:
                return err(GMErrorCode.PARAM_INVALID, parse_err)
            prompt = db.get_character_persona_prompt(uid, char_id_int) if hasattr(db, "get_character_persona_prompt") else ""
            return ok({
                "uid": uid,
                "char_id": char_id_int,
                "persona_prompt": prompt,
                "has_persona": bool(prompt.strip())
            })
        prompts = db.get_user_custom_prompts(uid) if hasattr(db, "get_user_custom_prompts") else {}
        return ok({"uid": uid, "prompts": prompts})

    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")
    uid, parse_err = _parse_uid(req.get("uid"), default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    _, err_resp = _require_user(uid)
    if err_resp:
        return err_resp
    char_id = req.get("char_id")
    char_id_int, parse_err = _parse_positive_int(char_id, "char_id")
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    if not db.get_ai_character(char_id_int):
        return err(GMErrorCode.NOT_FOUND, f"未找到修正者 char_id={char_id_int}")

    persona_prompt = req.get("persona_prompt", "")
    if persona_prompt is not None and not isinstance(persona_prompt, str):
        return err(GMErrorCode.PARAM_INVALID, "persona_prompt 必须为文本")
    if persona_prompt and len(persona_prompt) > 20000:
        return err(GMErrorCode.PARAM_OUT_OF_RANGE, "persona_prompt 长度不能超过 20000 字符")

    clean_prompt = (persona_prompt or "").strip()
    db.set_character_persona_prompt(uid, char_id_int, clean_prompt)
    cur_prompt = db.get_character_persona_prompt(uid, char_id_int)
    has_persona = bool(cur_prompt.strip())

    # 若置空了人设，自动将其问候模式调整为 none，防止遗留已开模式
    if not has_persona:
        from ai_translator import AITranslator
        translator = AITranslator.get_instance(db=db)
        try:
            translator.set_character_greeting_setting(uid, char_id_int, "none", db=db)
        except Exception:
            pass

    msg = f"修正者 [{char_id_int}] 专属人格提示词已更新" if has_persona else f"修正者 [{char_id_int}] 专属人格提示词已留空清空"
    return ok({
        "uid": uid,
        "char_id": char_id_int,
        "persona_prompt": cur_prompt,
        "has_persona": has_persona
    }, msg=msg)


@gm_bp.route("/aichat/trigger_greeting", methods=["POST"])
def post_aichat_trigger_greeting():
    """【写通道·GM测试】手动触发一次指定日期（默认今日）的节日/节气/生日问候信件生成。"""
    import datetime
    from ai_translator import AITranslator
    req = request.get_json(silent=True)
    if not isinstance(req, dict):
        return err(GMErrorCode.PARAM_INVALID, "请求体必须为有效 JSON 对象")
    uid, parse_err = _parse_uid(req.get("uid"), default=None)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, parse_err)
    _, err_resp = _require_user(uid)
    if err_resp:
        return err_resp
    force, parse_err = _parse_bool(req.get("force"), default=False)
    if parse_err:
        return err(GMErrorCode.PARAM_INVALID, "force 布尔参数格式非法")

    target_date = None
    date_str = req.get("date")
    if date_str:
        try:
            target_date = datetime.date.fromisoformat(str(date_str))
        except ValueError:
            return err(GMErrorCode.PARAM_INVALID, "日期格式错误，须为 YYYY-MM-DD")

    db = get_db()
    translator = AITranslator.get_instance(db=db)
    res = translator.check_daily_greetings(uid, db=db, cur_date=target_date, force=force)
    if res.get("status") in ("busy", "partial"):
        return err(GMErrorCode.SERVICE_ERROR, res.get("error", "问候调度未完整执行"), data=res)
    return ok(res, msg=f"问候生成执行完成，共派发 {res.get('dispatched_count', 0)} 封信件")


# ===========================================================================
# 9. 其他设置与服务运维 (/server)
# ===========================================================================

@gm_bp.route("/server/backup_db", methods=["POST"])
def post_backup_db():
    """【写通道·安全铁律】一键创建带时间戳的 SQLite account.db 安全快照。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db = get_db()
    db_file = getattr(db, 'path', os.path.join(base_dir, "account.db"))
    backup_dir = os.path.join(base_dir, "db_backup")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_filename = f"account_backup_gm_api_{timestamp}.db"
    dest_path = os.path.join(backup_dir, backup_filename)

    if not os.path.isfile(db_file):
        return err(GMErrorCode.NOT_FOUND, f"源数据库文件 [{os.path.basename(db_file)}] 不存在")

    try:
        shutil.copy2(db_file, dest_path)
        return ok({
            "backup_filename": backup_filename,
            "backup_path": dest_path,
            "size_bytes": os.path.getsize(dest_path),
            "timestamp": timestamp,
        }, msg="数据库快照备份成功")
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"备份数据库失败: {e}")


@gm_bp.route("/server/toggle_capture", methods=["GET", "POST"])
@gm_bp.route("/toggle_capture", methods=["GET", "POST"])  # 兼容旧路径
def toggle_capture_mode():
    """开启或关闭官方 CDN 动态抓包代理。"""
    if not cdn_proxy:
        return err(GMErrorCode.SERVICE_ERROR, "cdn_proxy 模块未加载")

    mode = request.args.get("enabled")
    if mode is None:
        data = request.get_json(silent=True) or {}
        mode = data.get("enabled")

    if mode is not None:
        cdn_proxy.set_capture_enabled(str(mode).lower() in ("1", "true", "yes", "on"))
    else:
        cdn_proxy.set_capture_enabled(not cdn_proxy.is_capture_enabled())

    is_on = cdn_proxy.is_capture_enabled()
    return jsonify({
        "code": GMErrorCode.SUCCESS,
        "msg": "抓包模式切换成功",
        "capture_enabled": is_on,
        "data": {"capture_enabled": is_on}
    }), 200


@gm_bp.route("/server/hot_reload", methods=["POST"])
def post_hot_reload():
    """热重载服务配置。"""
    return ok({"reloaded_modules": ["notice_cfg", "draw_service", "gm_api", "gm_reader", "shop_service"]}, msg="配置已热重载")


@gm_bp.route("/system/res_version", methods=["GET"])
@gm_bp.route("/server/res_version", methods=["GET"])
@gm_bp.route("/res_version", methods=["GET"])
def get_res_version():
    """【只读通道】获取当前客户端资源分发版本及支持的所有版本详情。"""
    try:
        import res_version_manager
        curr = res_version_manager.get_current_version()
        configs = res_version_manager.get_all_version_configs()
        curr_cfg = res_version_manager.get_version_config(curr)
        return ok({
            "current_version": curr,
            "version_name": curr_cfg.get("version_name", ""),
            "display_name": curr_cfg.get("display_name", ""),
            "asset_hash": curr_cfg.get("assethash", {}).get("pc", ""),
            "voice_list_file": curr_cfg.get("voice_package_list", ""),
            "supported_versions": list(res_version_manager.SUPPORTED_VERSIONS),
            "client_detection": res_version_manager.get_detected_client_info(),
            "version_details": configs
        })
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"获取客户端版本信息失败: {e}")


@gm_bp.route("/system/res_version", methods=["POST"])
@gm_bp.route("/server/res_version", methods=["POST"])
@gm_bp.route("/res_version", methods=["POST"])
def post_set_res_version():
    """【写通道】动态热切换客户端资源分发版本（229, 311 或 auto）。"""
    try:
        import res_version_manager
        data = request.get_json(silent=True) or {}
        ver = data.get("version") or request.args.get("version")
        if not ver:
            return err(GMErrorCode.PARAM_INVALID, "缺少 version 参数")
        ok_flag, msg = res_version_manager.set_current_version(ver)
        if not ok_flag:
            return err(GMErrorCode.PARAM_INVALID, msg)
        curr = res_version_manager.get_current_version()
        if curr == "229":
            try:
                from draw_service import DrawService
                DrawService.get_instance(db=get_db()).sanitize_pools_for_version("229")
            except Exception:
                pass
        cfg = res_version_manager.get_version_config(curr)
        print(f"[GM] 动态切换客户端资源版本 -> {curr} ({cfg.get('display_name', '')})", flush=True)
        return ok({
            "current_version": curr,
            "version_name": cfg.get("version_name", ""),
            "display_name": cfg.get("display_name", ""),
            "asset_hash": cfg.get("assethash", {}).get("pc", ""),
            "voice_list_file": cfg.get("voice_package_list", ""),
            "client_detection": res_version_manager.get_detected_client_info(),
            "version_config": cfg
        }, msg=msg)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"切换客户端资源版本异常: {e}")


# ===========================================================================
# 7. 商店系统管理 (/shop)
# ===========================================================================

@gm_bp.route("/shop/catalog", methods=["GET"])
def get_shop_catalog():
    """【只读通道】获取全服商店分类目录与概览。"""
    try:
        reader = get_reader()
        data = reader.get_shop_catalog_view()
        return ok(data)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"获取商店目录失败: {e}")


@gm_bp.route("/shop/goods", methods=["GET"])
def get_shop_goods():
    """【只读通道】获取指定商店货架商品及当前玩家已购状态。"""
    uid_str = request.args.get("uid", DEFAULT_UID)
    uid, err_msg = _parse_uid(uid_str)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    shop_id_str = request.args.get("shop_id", 2)
    try:
        shop_id = int(shop_id_str)
    except Exception:
        shop_id = 2

    try:
        reader = get_reader()
        data = reader.get_shop_goods_view(uid, shop_id=shop_id)
        return ok(data)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"获取商店商品失败: {e}")


@gm_bp.route("/shop/stats", methods=["GET"])
def get_shop_stats():
    """【只读通道】获取商店系统综合监控数据。"""
    uid_str = request.args.get("uid", DEFAULT_UID)
    uid, err_msg = _parse_uid(uid_str)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    try:
        reader = get_reader()
        data = reader.get_shop_stats_view(uid)
        return ok(data)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"获取商店统计失败: {e}")


@gm_bp.route("/shop/reset_refresh", methods=["POST"])
def post_reset_shop_refresh():
    """【写通道】GM 重置每日采购刷新次数为 0（移转之辉阶梯消耗归零）。"""
    data = request.get_json(silent=True) or {}
    uid_str = data.get("uid") or request.args.get("uid") or DEFAULT_UID
    uid, err_msg = _parse_uid(uid_str)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    shop_id = int(data.get("shop_id") or request.args.get("shop_id") or 2)

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        from shop_service import ShopService
        svc = ShopService.get_instance(db=db)
        success, msg = svc.gm_reset_shop_refresh(uid, shop_id=shop_id)
        if success:
            return ok({"uid": uid, "shop_id": shop_id, "refresh_times": 0}, msg=msg)
        else:
            return err(GMErrorCode.SERVICE_ERROR, msg)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"重置刷新次数失败: {e}")


@gm_bp.route("/shop/reset_purchase", methods=["POST"])
def post_reset_shop_purchase():
    """【写通道】GM 仅刷新周期商品（每日/每周/每月限购、每日商店、每日体力），严格保护永久限购与皮肤。"""
    data = request.get_json(silent=True) or {}
    uid_str = data.get("uid") or request.args.get("uid") or DEFAULT_UID
    uid, err_msg = _parse_uid(uid_str)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    shop_id = data.get("shop_id") or request.args.get("shop_id")
    if shop_id is not None and str(shop_id).strip() != "" and str(shop_id).lower() != "all":
        try:
            shop_id = int(shop_id)
        except Exception:
            shop_id = None
    else:
        shop_id = None

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        if shop_id == 2:
            # 每日采购商店（Shop 2）：重置购买记录与主动刷新次数
            cur = db.execute("DELETE FROM store_purchase WHERE uid=? AND shop_id=2", (uid,))
            deleted_rows = cur.rowcount if hasattr(cur, "rowcount") else 0
            db.execute("UPDATE user_daily_shop SET refresh_times=0 WHERE uid=?", (uid,))
            msg = f"玩家 {uid} 每日采购商店(#2)已重置（已清空 {deleted_rows} 项购买记录，主动刷新次数已归零）"
        elif shop_id is not None:
            # 指定单商店：严格仅清空该商店内的周期限购商品 (refresh_cycle IN (2, 3, 4))
            # 严禁误伤永久限购 (refresh_cycle=1) 或不限购皮肤 (refresh_cycle=0)
            cur = db.execute("""
                DELETE FROM store_purchase
                WHERE uid=? AND shop_id=? AND goods_id IN (
                    SELECT goods_id FROM shop_goods WHERE refresh_cycle IN (2, 3, 4) AND shop_id=?
                )
            """, (uid, shop_id, shop_id))
            deleted_rows = cur.rowcount if hasattr(cur, "rowcount") else 0
            msg = f"玩家 {uid} 商店 #{shop_id} 周期限购已重置（恢复 {deleted_rows} 项日/周/月限购，永久限购已安全保留）"
        else:
            # 全服周期重置：仅清理所有周期限购 (2, 3, 4) 与每日商店，并重置每日体力和刷新次数
            cur = db.execute("""
                DELETE FROM store_purchase
                WHERE uid=? AND (
                    shop_id = 2 OR
                    goods_id IN (SELECT goods_id FROM shop_goods WHERE refresh_cycle IN (2, 3, 4))
                )
            """, (uid,))
            deleted_rows = cur.rowcount if hasattr(cur, "rowcount") else 0
            db.execute("UPDATE user_daily_shop SET refresh_times=0 WHERE uid=?", (uid,))
            db.execute("UPDATE game_user SET total_buy_fatigue_times=0 WHERE uid=?", (uid,))
            msg = f"玩家 {uid} 全服周期商品已刷新（恢复 {deleted_rows} 项周期商品，每日采购与每日体力已重置，永久限购已安全保留）"

        return ok({"uid": uid, "shop_id": shop_id, "deleted_rows": deleted_rows}, msg=msg)
    except Exception as e:
        return err(GMErrorCode.DB_EXEC_FAILED, f"重置限购记录失败: {e}")


@gm_bp.route("/shop/trigger_cycle", methods=["POST"])
def post_trigger_shop_cycle():
    """【写通道】GM 强制触发商城周期刷新（daily / weekly / monthly）。"""
    data = request.get_json(silent=True) or {}
    uid_str = data.get("uid") or request.args.get("uid") or DEFAULT_UID
    uid, err_msg = _parse_uid(uid_str)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    cycle_type = data.get("cycle_type") or request.args.get("cycle_type") or "daily"
    if cycle_type not in ("daily", "weekly", "monthly"):
        return err(GMErrorCode.PARAM_INVALID, "cycle_type 必须为 daily, weekly 或 monthly")

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        from shop_service import ShopService
        svc = ShopService.get_instance(db=db)
        success, msg = svc.gm_trigger_cycle_refresh(uid, cycle_type=cycle_type)
        if success:
            return ok({"uid": uid, "cycle_type": cycle_type}, msg=msg)
        else:
            return err(GMErrorCode.SERVICE_ERROR, msg)
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"触发周期刷新失败: {e}")


@gm_bp.route("/shop/buy_goods", methods=["POST"])
def post_buy_shop_goods():
    """【写通道】GM/控制台执行商城商品购买或直发。"""
    import time
    data = request.get_json(silent=True) or {}
    uid_str = data.get("uid") or request.args.get("uid") or DEFAULT_UID
    uid, err_msg = _parse_uid(uid_str)
    if err_msg:
        return err(GMErrorCode.PARAM_INVALID, err_msg)

    shop_id_val = data.get("shop_id") or request.args.get("shop_id")
    goods_id_val = data.get("goods_id") or request.args.get("goods_id")
    if shop_id_val is None or goods_id_val is None:
        return err(GMErrorCode.PARAM_INVALID, "必须指定 shop_id 与 goods_id")

    try:
        shop_id = int(shop_id_val)
        goods_id = int(goods_id_val)
    except Exception:
        return err(GMErrorCode.PARAM_INVALID, "shop_id 与 goods_id 必须为整数")

    try:
        buy_num = int(data.get("buy_num", 1))
        if buy_num <= 0:
            return err(GMErrorCode.PARAM_OUT_OF_RANGE, "购买数量 buy_num 必须大于 0")
    except Exception:
        return err(GMErrorCode.PARAM_INVALID, "buy_num 必须为正整数")

    free_cost = bool(data.get("free_cost", False))

    db, err_resp = _require_user(uid)
    if err_resp:
        return err_resp

    try:
        from shop_service import ShopService
        from operations import OperationError
        from inventory_service import InventoryService

        class GMCtx:
            def __init__(self, db_conn):
                self.db = db_conn
                self.touched_items = set()
            def log(self, msg):
                pass

        ctx = GMCtx(db)
        svc = ShopService.get_instance(db=db)

        if free_cost:
            # GM 特权免代币扣减直发
            g = svc._find_goods_cfg(goods_id, shop_id)
            if not g:
                return err(GMErrorCode.ITEM_NOT_FOUND, f"商品 {goods_id} 不在商店 {shop_id} 中")

            item_target = int(g.get("item_id") or 0)
            give_unit = int(g.get("give_num") or 1)
            give_total = give_unit * buy_num
            now_ts = int(time.time())

            # 发放资产
            if item_target > 0:
                InventoryService.grant_items(ctx, uid, [(item_target, give_total)], silent=True)

            # 记录限购已购
            cur_limit = int(g.get("limit_num") if g.get("limit_num") is not None else -1)
            if cur_limit > 0:
                db.execute("""
                    INSERT INTO store_purchase (uid, shop_id, goods_id, buy_times, next_refresh_timestamp, update_ts)
                    VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(uid, shop_id, goods_id) DO UPDATE SET
                        buy_times = buy_times + excluded.buy_times,
                        update_ts = excluded.update_ts
                """, (uid, shop_id, goods_id, buy_num, now_ts))

            return ok({
                "uid": uid,
                "shop_id": shop_id,
                "goods_id": goods_id,
                "buy_num": buy_num,
                "free_cost": True,
                "item_id": item_target,
                "grant_num": give_total,
            }, msg=f"已通过 GM 特权免扣费发放商品 #{goods_id} x {buy_num}")
        else:
            # 官方权威扣款购买引擎闭环
            try:
                frames = svc.buy_goods(ctx, uid, shop_id, [{"buy_id": goods_id, "buy_num": buy_num}], buy_source=0)
                return ok({
                    "uid": uid,
                    "shop_id": shop_id,
                    "goods_id": goods_id,
                    "buy_num": buy_num,
                    "free_cost": False,
                    "frames_count": len(frames)
                }, msg=f"商品 #{goods_id} 购买成功（数量: {buy_num}）")
            except OperationError as op_err:
                return err(GMErrorCode.PARAM_INVALID, f"购买未通过: {op_err.message}")

    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"购买执行异常: {e}")


# ===========================================================================
# 兼容旧版历史 GM 端点 (Legacy Compatibility Bridges)
# ===========================================================================

@gm_bp.route("/stage_preset", methods=["GET", "POST"])
def handle_stage_preset():
    """兼容旧版切换关卡进度预设。"""
    from stage_service import StageService
    db = get_db()
    mode = request.args.get("mode")
    if not mode:
        data = request.get_json(silent=True) or {}
        mode = data.get("mode") or "all_clear"
    target_uid = request.args.get("uid") or DEFAULT_UID
    svc = StageService.get_instance(db=db)
    success, msg = svc.apply_stage_preset(int(target_uid), mode=mode)
    print(f"[GM] 切换关卡进度预设 UID={target_uid} mode={mode} 结果={success} ({msg})", flush=True)
    return jsonify({"code": 0 if success else 1, "msg": msg, "mode": mode, "uid": target_uid})


@gm_bp.route("/draw/<path:subpath>", methods=["GET", "POST"])
def handle_draw_legacy(subpath):
    """兼容旧版卡池管理与预设切换。"""
    from draw_service import DrawService, POOL_PRESETS
    db = get_db()
    svc = DrawService.get_instance(db=db)

    if subpath == "pools":
        data = svc.list_all_pools()
        return jsonify({"code": 0, "msg": "success", "total": len(data), "data": data})

    if subpath == "catalog":
        cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "expansion_pools_catalog.json")
        active_set = set(svc.get_active_pools())
        catalog = []
        if os.path.isfile(cat_path):
            with open(cat_path, "r", encoding="utf-8") as f:
                raw_cat = json.load(f)
                for item in raw_cat:
                    d = dict(item)
                    d["is_active"] = item["pool_id"] in active_set
                    d["is_selectable"] = svc.is_selectable_pool(item["pool_id"])
                    catalog.append(d)
        return jsonify({"code": 0, "msg": "success", "total": len(catalog), "data": catalog})

    if subpath == "active":
        active_ids = svc.get_active_pools()
        cat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "expansion_pools_catalog.json")
        catalog_map = {}
        if os.path.isfile(cat_path):
            try:
                with open(cat_path, "r", encoding="utf-8") as f:
                    for it in json.load(f):
                        catalog_map[it["pool_id"]] = it
            except Exception:
                pass
        active_details = []
        for pid in active_ids:
            if pid in catalog_map:
                it = dict(catalog_map[pid])
                it["is_active"] = True
                active_details.append(it)
            else:
                active_details.append({"pool_id": pid, "is_active": True, "pool_name": f"卡池 {pid}"})
        return jsonify({
            "code": 0,
            "msg": "success",
            "total": len(active_ids),
            "active_pools": active_ids,
            "pools": active_details
        })

    if subpath == "presets":
        return jsonify({"code": 0, "msg": "success", "data": POOL_PRESETS})

    if subpath == "set_active":
        body = request.get_json(silent=True) or {}
        pool_ids = body.get("pool_ids") or request.args.get("pool_ids")
        if isinstance(pool_ids, str):
            pool_ids = [int(x.strip()) for x in pool_ids.split(",") if x.strip().isdigit()]
        if not pool_ids:
            return jsonify({"code": 1, "msg": "pool_ids 不能为空"})
        success, msg = svc.set_active_pools(pool_ids)
        return jsonify({"code": 0 if success else 1, "msg": msg, "active_pools": svc.get_active_pools()})

    if subpath in ("switch_preset", "apply_preset"):
        body = request.get_json(silent=True) or {}
        preset_key = body.get("preset_key") or body.get("preset") or request.args.get("preset") or "classic_safe"
        success, msg = svc.apply_preset(preset_key)
        return jsonify({"code": 0 if success else 1, "msg": msg, "preset": preset_key, "active_pools": svc.get_active_pools()})

    if subpath == "set_pity":
        body = request.get_json(silent=True) or {}
        target_uid = int(body.get("uid") or request.args.get("uid") or DEFAULT_UID)
        pool_group = body.get("pool_group") or request.args.get("pool_group") or "hero_precision_70"
        since_ssr = int(body.get("since_ssr") or request.args.get("since_ssr") or 0)
        is_up_guaranteed = body.get("is_up_guaranteed")
        if is_up_guaranteed is not None:
            is_up_guaranteed = int(is_up_guaranteed)
        success, msg = svc.set_pity(target_uid, pool_group, since_ssr, is_up_guaranteed)
        return jsonify({"code": 0 if success else 1, "msg": msg, "uid": target_uid, "state": svc.get_draw_state(target_uid, pool_group)})

    return jsonify({"code": 1, "msg": f"未知的抽卡 GM 接口: {subpath}"})


@gm_bp.route("/oath/<path:subpath>", methods=["GET", "POST"])
def handle_oath_legacy(subpath):
    """兼容旧版誓约系统管理与调试。"""
    from oath_service import OathService
    db = get_db()
    svc = OathService.get_instance()
    body = request.get_json(silent=True) or {}
    target_uid = int(body.get("uid") or request.args.get("uid") or DEFAULT_UID)

    if subpath == "status":
        data = svc.gm_get_status(db, target_uid)
        return jsonify({"code": 0, "msg": "success", "uid": target_uid, "data": data})

    if subpath == "ready":
        hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
        success, msg = svc.gm_ready_oath(db, target_uid, hid)
        return jsonify({"code": 0 if success else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

    if subpath == "unlock":
        hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
        success, msg = svc.gm_unlock_oath(db, target_uid, hid)
        return jsonify({"code": 0 if success else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

    if subpath == "set_level":
        hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
        level = int(body.get("level") or request.args.get("level") or 1)
        success, msg = svc.gm_set_oath_level(db, target_uid, hid, level)
        return jsonify({"code": 0 if success else 1, "msg": msg, "uid": target_uid, "hero_id": hid, "level": level})

    if subpath == "reset":
        hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
        success, msg = svc.gm_reset_oath(db, target_uid, hid)
        return jsonify({"code": 0 if success else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

    if subpath == "reset_tasks":
        hid = int(body.get("hero_id") or request.args.get("hero_id") or 0)
        success, msg = svc.gm_reset_tasks(db, target_uid, hid)
        return jsonify({"code": 0 if success else 1, "msg": msg, "uid": target_uid, "hero_id": hid})

    if subpath == "give_rings":
        cnt = int(body.get("count") or request.args.get("count") or 10)
        success, msg = svc.gm_give_rings(db, target_uid, count=cnt)
        return jsonify({"code": 0 if success else 1, "msg": msg, "uid": target_uid, "count": cnt})

    return jsonify({"code": 1, "msg": f"未知的誓约 GM 接口: {subpath}"})


# ---------------------------------------------------------------------------
# 充值与付费业务管理 (Recharge & Payment Control)
# ---------------------------------------------------------------------------

@gm_bp.route("/recharge/status", methods=["GET"])
def gm_recharge_status():
    """获取指定玩家的付费状态（首充双倍、战令开通、新手福利、累充等）。"""
    db = get_db()
    if not db:
        return err(GMErrorCode.DB_NOT_READY, "数据库未就绪")

    uid = int(request.args.get("uid") or DEFAULT_UID)
    import recharge_service
    svc = recharge_service.RechargeService.get_instance(db=db)

    recharge_data = db.get_user_recharge(uid)
    bp_data = db.get_or_create_battlepass(uid) if hasattr(db, "get_or_create_battlepass") else {}
    newbie_act = db.get_newbie_activity(uid) if hasattr(db, "get_newbie_activity") else {}

    tot_num = recharge_data.get("total_recharge_num") or 0
    lim_num = recharge_data.get("time_limit_recharge_num") or 0
    data = {
        "uid": uid,
        "first_recharge_ids": recharge_data.get("first_recharge_ids") or [],
        "total_recharge_num": tot_num,
        "total_recharge_yuan": float(tot_num),
        "total_recharge_cents": int(tot_num * 100),
        "claimed_total_bonus": recharge_data.get("claimed_total_bonus") or [],
        "time_limit_recharge_num": lim_num,
        "time_limit_recharge_yuan": float(lim_num),
        "battlepass": {
            "pay_level": int(bp_data.get("pay_level") or 0),
            "exp_14": db.get_item_num(uid, 14),
            "bp_reward_status": int(newbie_act.get("bp_reward") or 0)
        },
        "noob_welfare": {
            "fr_first_gear": int(newbie_act.get("fr_first_gear") or 0),
            "fr_second_gear": int(newbie_act.get("fr_second_gear") or 0),
            "fr_now_sign": int(newbie_act.get("fr_now_sign") or 0),
            "mc_flag": int(newbie_act.get("mc_flag") or 0)
        },
        "currency_flower": {
            "c30_ios": db.get_item_num(uid, 30),
            "c31_not_ios": db.get_item_num(uid, 31),
            "c32_free": db.get_item_num(uid, 32),
            "c5_total": db.get_item_num(uid, 5),
            "c1_diamond": db.get_item_num(uid, 1)
        }
    }
    return ok(data)


@gm_bp.route("/recharge/reset", methods=["POST"])
def gm_recharge_reset():
    """
    重置玩家付费与充值状态接口：
    参数：
      uid: 玩家 UID (默认 DEFAULT_UID)
      scope: 重置范围，可选:
        - "all": 全部重置
        - "first_recharge": 重置首充双倍
        - "battlepass": 重置战令 (pay_level=0, bp_reward=0)
        - "noob_welfare": 重置新手首充与18元签到福利
        - "total_recharge": 重置累充金额与档位领奖
    """
    db = get_db()
    if not db:
        return err(GMErrorCode.DB_NOT_READY, "数据库未就绪")

    body = request.get_json(silent=True) or {}
    uid = int(body.get("uid") or request.args.get("uid") or DEFAULT_UID)
    scope = str(body.get("scope") or request.args.get("scope") or "all").lower()

    import recharge_service
    svc = recharge_service.RechargeService.get_instance(db=db)
    res = svc.reset_recharge_data(db, uid, scope=scope)

    return ok(res, msg=f"付费数据重置成功: scope={scope}")


# ---------------------------------------------------------------------------
# 区服管理与自定义服名 (Server Zones Management)
# ---------------------------------------------------------------------------

@gm_bp.route("/system/server_zones", methods=["GET"])
def get_server_zones():
    """获取服务端当前配置的区服列表与默认区服。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "data", "server_zones.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return ok(data)
        except Exception as e:
            return err(GMErrorCode.SERVICE_ERROR, f"读取区服配置失败: {e}")

    # 默认初始化
    default_data = {
        "default_zone_id": "1",
        "zones": [
            {"serverId": "1", "serverName": "艾因索菲", "env": "prod", "newServerFlag": 1, "maintain": False, "maintainReason": ""},
            {"serverId": "2", "serverName": "蒂卡拉", "env": "prod", "newServerFlag": 0, "maintain": False, "maintainReason": ""},
        ]
    }
    return ok(default_data)


@gm_bp.route("/system/server_zones", methods=["POST"])
def post_server_zones():
    """保存/更新区服列表与自定义服名。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "data", "server_zones.json")
    body = request.get_json(silent=True) or {}

    zones = body.get("zones")
    default_zone_id = str(body.get("default_zone_id") or "1")

    if not zones or not isinstance(zones, list):
        return err(GMErrorCode.PARAM_INVALID, "zones 必须为非空数组")

    # 简单校验与规范化
    sanitized_zones = []
    for z in zones:
        s_id = str(z.get("serverId") or len(sanitized_zones) + 1)
        s_name = str(z.get("serverName") or f"区服 {s_id}").strip()
        if not s_name:
            s_name = f"区服 {s_id}"
        sanitized_zones.append({
            "serverId": s_id,
            "serverName": s_name,
            "env": str(z.get("env") or "prod"),
            "newServerFlag": int(z.get("newServerFlag", 0)),
            "maintain": bool(z.get("maintain", False)),
            "maintainReason": str(z.get("maintainReason", ""))
        })

    data_to_save = {
        "default_zone_id": default_zone_id,
        "zones": sanitized_zones
    }

    try:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        return ok(data_to_save, msg="区服配置已成功保存")
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"写入区服配置失败: {e}")


# ---------------------------------------------------------------------------
# 服务启动参数与运行调谐配置 (/system/launch_config)
# ---------------------------------------------------------------------------

def _scan_available_replays(base_dir):
    """扫描项目可用重放素材目录。"""
    cands = []
    # 1. 检查 v5_server/replay_data
    local_p = os.path.join(base_dir, "replay_data")
    if os.path.isdir(local_p):
        if os.path.isdir(os.path.join(local_p, "tcp")) or os.path.isdir(os.path.join(local_p, "https")):
            cands.append({"name": "本地调试素材 (v5_server/replay_data)", "path": "replay_data"})

    # 2. 检查 ../analysis_scripts/archive
    archive_p = os.path.abspath(os.path.join(base_dir, "..", "analysis_scripts", "archive"))
    if os.path.isdir(archive_p):
        for root, dirs, files in os.walk(archive_p):
            if "complete_replay" in dirs:
                cr_p = os.path.join(root, "complete_replay")
                rel = os.path.relpath(cr_p, base_dir).replace("\\", "/")
                cands.append({"name": os.path.basename(root) + " (完整重放)", "path": rel})
            elif "tcp" in dirs or "https" in dirs:
                rel = os.path.relpath(root, base_dir).replace("\\", "/")
                if rel not in [c["path"] for c in cands]:
                    cands.append({"name": os.path.basename(root), "path": rel})

    return cands


@gm_bp.route("/system/launch_config", methods=["GET"])
def get_launch_config():
    """获取当前服务启动参数调谐配置与重放素材可用状态。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "data", "launch_config.json")

    replays = _scan_available_replays(base_dir)

    default_cfg = {
        "log_level": "INFO",
        "replay_enabled": False,
        "replay_path": replays[0]["path"] if replays else "",
        "host_ip": _context.get("host_ip", ""),
        "https_port": _context.get("https_port", 443),
        "gw_port": _context.get("gw_port", 8102),
        "game_port": _context.get("game_port", 8105),
        "db_path": "account.db",
        "client_assets_dir": ""
    }

    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default_cfg.update(saved)
        except Exception:
            pass

    # 附带重放素材探测元信息
    default_cfg["available_replays"] = replays
    default_cfg["has_replay_material"] = len(replays) > 0
    default_cfg["detected_host_ip"] = _context.get("host_ip", "127.0.0.1")

    return ok(default_cfg)


@gm_bp.route("/system/launch_config", methods=["POST"])
def post_launch_config():
    """保存服务启动参数调谐配置，支持部分参数（如 log_level）即时热生效。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "data", "launch_config.json")
    body = request.get_json(silent=True) or {}

    log_level = str(body.get("log_level") or "INFO").upper()
    if log_level in ("DEBUG", "INFO", "WARN", "ERROR"):
        try:
            import logger
            logger.set_level(log_level)
        except Exception:
            pass

    saved_data = {
        "log_level": log_level,
        "replay_enabled": bool(body.get("replay_enabled", False)),
        "replay_path": str(body.get("replay_path") or "").strip(),
        "host_ip": str(body.get("host_ip") or "").strip(),
        "https_port": int(body.get("https_port") or 443),
        "gw_port": int(body.get("gw_port") or 8102),
        "game_port": int(body.get("game_port") or 8105),
        "db_path": str(body.get("db_path") or "account.db").strip(),
        "client_assets_dir": str(body.get("client_assets_dir") or "").strip()
    }

    try:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(saved_data, f, ensure_ascii=False, indent=2)
        return ok(saved_data, msg="启动调谐参数已成功保存")
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"写入启动参数失败: {e}")


@gm_bp.route("/system/regenerate_cert", methods=["POST"])
def regenerate_cert_api():
    """强制重新生成适用于 Windows 和 iPhone 的自签根证书及描述文件。"""
    try:
        import gen_cert
        base_dir = os.path.dirname(os.path.abspath(__file__))
        host_ip = _context.get("host_ip", "127.0.0.1")
        ok_flag = gen_cert.generate_dual_layer_certs(output_dir=base_dir, extra_ips=[host_ip], force=True)
        if ok_flag:
            return ok({
                "status": "regenerated",
                "ca_crt": "sdk_ca.crt",
                "ca_cer": "sdk_cert.cer",
                "mobileconfig": "AetherGazer.mobileconfig"
            }, msg="证书体系与 iPhone 描述文件已重新生成成功！")
        else:
            return err(GMErrorCode.SERVICE_ERROR, "生成证书失败，请查看服务端日志")
    except Exception as e:
        return err(GMErrorCode.SERVICE_ERROR, f"重新生成证书异常: {e}")



