# -*- coding: utf-8 -*-
"""Bilibili PC SDK 与官方启动器网站接口离线适配模块。"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import secrets
import threading
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


_SESSION_LOCK = threading.Lock()
_SESSIONS: OrderedDict[str, dict] = OrderedDict()
_MAX_SESSIONS = 512

_RSA_LOCK = threading.Lock()
_RSA_PEM: str | None = None

_TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc```\x00\x00"
    b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_COUNTRY_LIST = [
    {"cname": "中国大陆", "id": 1, "country_id": "86"},
    {"cname": "中国香港特别行政区", "id": 5, "country_id": "852"},
    {"cname": "中国澳门特别行政区", "id": 2, "country_id": "853"},
    {"cname": "中国台湾省", "id": 4, "country_id": "886"},
    {"cname": "美国", "id": 3, "country_id": "1"},
    {"cname": "日本", "id": 6, "country_id": "81"},
    {"cname": "韩国", "id": 7, "country_id": "82"},
    {"cname": "新加坡", "id": 8, "country_id": "65"},
    {"cname": "马来西亚", "id": 9, "country_id": "60"},
    {"cname": "英国", "id": 10, "country_id": "44"},
]

_LAUNCHER_UPDATE_PAYLOAD = {
    "errorCode": "0",
    "data": {
        "gameUpdateInfo": {
            "zipFileSize": 0,
            "fileName": "AetherGazer_Bili_zh_cn_ali_prod_308_301_7_windows_limit_sdk_release_M13002001_3CD88D2DEA7CC40DE8144F54111BE802.zip",
            "unzipFileSize": 0,
            "forceUpdateVersion": "0",
            "downloadUrl": "https://packaging.ys4fun.com/packagepc/prod/308/AetherGazer_Bili_zh_cn_ali_prod_308_301_7_windows_limit_sdk_release_M13002001_3CD88D2DEA7CC40DE8144F54111BE802.zip",
            "currentVersion": "308",
            "content": "",
        },
        "pcLauncherHotUpdateInfo": {},
        "gameSubIdUpdateInfo": {},
        "pcLauncherUpdateInfo": {
            "zipFileSize": 0,
            "fileName": "mmr_setup_cn_bilibili_prod_1.9_W13002001_20260714.exe",
            "unzipFileSize": 0,
            "downloadUrl": "https://packaging.ys4fun.com/launcher/prod/1.9/mmr_setup_cn_bilibili_prod_1.9_W13002001_20260714.exe",
            "currentVersion": "1.9",
            "content": "",
        },
    },
}

_GAME_WEBSITE_STATIC_RESPONSES = {
    "/game-website-server/pass/sdk/get_index_config": {
        "errorCode": "0",
        "data": {
            "versionImage": "https://web-static.ys4fun.com/game-site/image/2e6d05fe-dc26-4ad8-8f07-64fe7e3fd9fb.png",
            "ageTip": {"url": "https://skzy.ys4fun.com", "content": ""},
        },
    },
    "/game-website-server/pass/sdk/get_menu_info": {"errorCode": "0", "data": []},
    "/game-website-server/pass/sdk/get_news": {"errorCode": "0", "data": []},
    "/game-website-server/pass/sdk/get_slide_image": {"errorCode": "0", "data": []},
    "/game-website-server/pass/sdk/get_update_version": _LAUNCHER_UPDATE_PAYLOAD,
}

_BILIGAME_API_PREFIXES = (
    "/api/pcg/",
    "/api/agreement/",
    "/api/external/",
    "/app/pcg/",
    "/collector/api/",
    "/tool/pcgame/",
    "/cg/api/",
)


def _get_rsa_public_pem() -> str:
    """生成并缓存 1024 位 RSA 公钥 PEM（匹配 wpg-api /api/pcg/rsa 长度合约）。"""
    global _RSA_PEM
    with _RSA_LOCK:
        if _RSA_PEM is None:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
            _RSA_PEM = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")
        return _RSA_PEM


def _first_param(args, form, req_json, key: str, default: str = "") -> str:
    val = args.get(key)
    if val not in (None, ""):
        return str(val)
    val = form.get(key)
    if val not in (None, ""):
        return str(val)
    if isinstance(req_json, dict) and req_json.get(key) not in (None, ""):
        return str(req_json[key])
    return default


def _request_envelope(args, form, req_json) -> dict:
    req_id = _first_param(args, form, req_json, "request_id") or uuid.uuid4().hex
    now_ms = int(time.time() * 1000)
    return {
        "request-id": str(uuid.uuid1()),
        "request_id": req_id,
        "ts": now_ms,
        "timestamp": now_ms,
    }


def _issue_session(account_hint: str = "", access_key: str = "") -> dict:
    """按输入账号或缓存 token 返回隔离的离线 Bili SDK 会话（不落盘、不依赖真实凭证）。"""
    cleaned_key = str(access_key or "").strip()
    cleaned_hint = str(account_hint or "").strip()
    with _SESSION_LOCK:
        if cleaned_key and cleaned_key in _SESSIONS:
            _SESSIONS.move_to_end(cleaned_key)
            return dict(_SESSIONS[cleaned_key])

        seed = cleaned_hint or cleaned_key or uuid.uuid4().hex
        digest = hashlib.sha256(f"bili-offline:{seed}".encode("utf-8")).hexdigest()
        if cleaned_hint.isdigit() and 5 <= len(cleaned_hint) <= 12:
            bili_uid = int(cleaned_hint)
        else:
            bili_uid = 90000000 + (int(digest[:8], 16) % 9000000)

        token = cleaned_key or f"{uuid.uuid4().hex}_sh"
        session = {
            "uid": bili_uid,
            "uname": f"Offline_{bili_uid % 10000:04d}",
            "access_key": token,
            "game_open_id": "",
        }
        _SESSIONS[token] = session
        _SESSIONS.move_to_end(token)
        while len(_SESSIONS) > _MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
        return dict(session)


def _time_conf_response(args, form, req_json) -> dict:
    now_ms = int(time.time() * 1000)
    cli_req = _first_param(args, form, req_json, "client_request_uuid", "0")
    return {
        "code": "0",
        "requestId": cli_req,
        "serverRequestId": uuid.uuid4().hex,
        "timestamp": str(now_ms),
        "clientRequestId": cli_req,
        "recEnable": "true",
        "recRate": "60",
        "support_https": True,
        "reserve_http_list": "http://line3-realtime-api.biligame.net,http://line1-realtime-api.biligame.net",
        "reserve_https_list": "https://line3-realtime-api.biligame.net,https://line1-realtime-api.biligame.net",
        "ts": 0,
    }


def _login_payload(session: dict, now_s: int, env: dict, *, include_expires: bool) -> dict:
    data = {
        **env,
        "code": 0,
        "message": "",
        "is_need_real_name": 1,
        "is_real_name": 1,
        "limit_alert_message": "",
        "user_limit_status": 0,
        "uid": int(session["uid"]),
        "uname": session["uname"],
        "face": "",
        "game_open_id": session["game_open_id"],
        "game_open_id_enable": False,
    }
    if include_expires:
        data["s_face"] = ""
        data["access_key"] = session["access_key"]
        data["expires"] = now_s + 86400 * 30
    return data


def _fallback_static_or_json(path: str) -> tuple[int, dict | bytes, str]:
    lower = path.lower()
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif")):
        return 200, _TRANSPARENT_PNG, "image/png"
    if lower.endswith(".css"):
        return 200, b"", "text/css"
    if lower.endswith(".js"):
        return 200, b"", "application/javascript"
    if lower.endswith((".html", ".htm")):
        return 200, b"<!doctype html><html><body>OK</body></html>", "text/html"
    return 200, {"code": 0, "message": "ok", "data": {}}, "application/json"


def dispatch_bilibili_request(host: str, path: str, args, form, req_json):
    """若请求属于官方启动器网站或 Bilibili PC SDK 接口，返回 (status, payload, mimetype)；否则返回 None。"""
    norm_path = path if path.startswith("/") else "/" + path
    norm_host = (host or "").split(":")[0].lower()

    if norm_path in _GAME_WEBSITE_STATIC_RESPONSES:
        return 200, _GAME_WEBSITE_STATIC_RESPONSES[norm_path], "application/json"

    is_bili_host = norm_host.endswith((".biligame.com", ".biligame.net")) or norm_host in ("biligame.com", "biligame.net")
    if not is_bili_host and not norm_path.startswith(_BILIGAME_API_PREFIXES):
        if norm_host in ("web-static.ys4fun.com", "webstatic.ys4fun.com") and norm_path.lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif")
        ):
            return 200, _TRANSPARENT_PNG, "image/png"
        return None

    env = _request_envelope(args, form, req_json)
    now_s = int(time.time())

    if norm_path == "/tool/pcgame/sdkHotConfig_pc_v2.json":
        return 200, {"nativeLoginType": [], "collectHardInfo": []}, "application/json"

    if norm_path == "/api/agreement/config":
        return 200, {
            "request_id": env["request_id"],
            "timestamp": env["timestamp"],
            "code": 0,
            "message": "响应成功",
            "data": {"cooperation_mode": 0, "agreement_switch": "OFF", "privacy_tips_switch": "ON"},
            "success": True,
        }, "application/json"

    if norm_path == "/api/external/country/list":
        return 200, {
            "request-id": env["request-id"],
            "request_id": env["request_id"],
            "ts": env["ts"],
            "code": 0,
            "data": {"country_list": _COUNTRY_LIST},
        }, "application/json"

    if norm_path == "/api/pcg/rsa":
        return 200, {**env, "code": 0, "hash": secrets.token_hex(8), "rsa_key": _get_rsa_public_pem()}, "application/json"

    if norm_path in ("/api/pcg/start_captcha", "/api/external/start_captcha/v2"):
        return 200, {
            **env,
            "code": 0,
            "data": {
                "challenge": uuid.uuid4().hex,
                "gs": 1,
                "captcha_type": 1,
                "gt": uuid.uuid4().hex,
                "userid": uuid.uuid4().hex,
            },
        }, "application/json"

    if norm_path in ("/api/pcg/login", "/api/pcg/otp/login"):
        user_hint = _first_param(args, form, req_json, "user_id") or _first_param(args, form, req_json, "phone")
        session = _issue_session(account_hint=user_hint)
        return 200, _login_payload(session, now_s, env, include_expires=True), "application/json"

    if norm_path == "/api/pcg/auto.login":
        access_key = _first_param(args, form, req_json, "access_key")
        session = _issue_session(access_key=access_key)
        return 200, _login_payload(session, now_s, env, include_expires=False), "application/json"

    if norm_path == "/api/pcg/otp/send":
        return 200, {**env, "code": 0, "message": "ok", "data": {}}, "application/json"

    if norm_path == "/api/pcg/qrcode/login/generate":
        ticket = uuid.uuid4().hex
        return 200, {
            **env,
            "code": 0,
            "message": "ok",
            "ticket": ticket,
            "redirect_url": f"https://sdk.biligame.com/login/?auth_code={ticket}",
            "game_base_id": 10503,
            "polymer_qrcode_switch": False,
            "support_app_list": [],
        }, "application/json"

    if norm_path == "/api/pcg/qrcode/login/status/poll":
        return 200, {**env, "code": 86101, "message": "waiting scan", "data": {}}, "application/json"

    if norm_path == "/api/pcg/common/getGameBaseId":
        return 200, {
            **env,
            "code": 0,
            "game_base_id": 10503,
            "data": {"game_base_id": 10503},
        }, "application/json"

    if norm_path == "/api/pcg/zone/notify":
        return 200, {**env, "code": 0, "message": "ok", "data": {}}, "application/json"

    if norm_path == "/api/pcg/config/dlc":
        req_gid = _first_param(args, form, req_json, "game_id", "10503")
        return 200, {
            **env,
            "code": 0,
            "game_id": req_gid,
            "uid": _first_param(args, form, req_json, "uid", "0"),
            "game_open_id_enable": False,
            "game_open_id": "",
            "product": [],
            "data": {"dlc_list": []},
        }, "application/json"

    if norm_path == "/cg/api/client/can.pay":
        return 200, {**env, "code": 0, "data": {"can_pay": False}}, "application/json"

    if norm_path in ("/app/pcg/time/conf", "/app/pcg/time/heartbeat"):
        return 200, _time_conf_response(args, form, req_json), "application/json"

    if norm_path == "/collector/api/report":
        return 200, {"code": 0, "message": "ok"}, "application/json"

    return _fallback_static_or_json(norm_path)


def extract_mix_channel_login(args, form, req_json, default_channel_id: int) -> tuple[int, int, str]:
    """解析 Mix SDK 登录请求的渠道号与渠道用户 ID，兼容官服 (7/9) 与 Bili PC (10 -> 6)。"""
    raw_ch = _first_param(args, form, req_json, "channelId")
    req_ch = int(raw_ch) if raw_ch.isdigit() else default_channel_id
    target_ch = 6 if req_ch == 10 else 7
    channel_uid = "1012030496969579190"
    raw_info = _first_param(args, form, req_json, "channelLoginRespInfo")
    if raw_info:
        try:
            parsed = json.loads(raw_info)
            if isinstance(parsed, dict) and parsed.get("channelUserId"):
                channel_uid = str(parsed["channelUserId"])
        except Exception:
            pass
    return req_ch, target_ch, channel_uid
