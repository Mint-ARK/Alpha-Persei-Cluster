# -*- coding: utf-8 -*-
"""
generator.py — 响应生成器（④）：请求 cmd → 查库 → 编码 → 组帧

核心：把"伪装服务器触发的请求"映射到"某用户的数据表"，查库后用 codec 编码成
payload，再与帧头（size/cmd/idx/srv）组合成下行帧，交给服务器发送。

- ROUTER：cmd → handler(pkt, db, uid) → list[frame bytes]（可多帧）或 None（不响应）
- generate(cmd, pkt, uid)：统一入口
- 帧头构造与 v3.2 的 build_downstream 一致（u16 size + 000000 + cmd + idx + srv + payload），
  idx/srv 回显请求（SendWithLoadingNew 回调匹配必需）
"""
import os
import struct
import time

from account_db import get_db, DEFAULT_DB  # noqa: E402
from codec import encode, decode  # noqa: E402

DEFAULT_UID = 2174928301  # 业务 UID（掩码随机 UID）


def build_frame(cmd, payload, index=0, server_idx=0, flag=0):
    """下行帧：u16 size(BE) + flag域(3B) + u16 cmd + u16 idx + u16 srv + payload。
    byte 2 = zlib 压缩标志；byte 4 = 高位标志 (cmd > 65535)。"""
    zlib_flag = 1 if (payload and payload[:2] == b"\x78\x9c") else 0
    high_flag = 1 if (cmd > 65535 or flag == 1) else 0
    body = (bytes([zlib_flag & 0xFF, 0x00, high_flag & 0xFF])
            + struct.pack(">H", cmd & 0xFFFF)
            + struct.pack(">H", index & 0xFFFF)
            + struct.pack(">H", server_idx & 0xFFFF)
            + payload)
    return struct.pack(">H", len(body)) + body



# ---------------- 各 cmd 的响应生成器 ----------------

def _fidx(pkt):
    """回显序号：idx=请求 idx，srv=请求 srv+1（首帧）。多帧时 srv 递增。"""
    if not pkt:
        return 0, 0
    return pkt.get("index", 0), pkt.get("server_idx", 0) + 1


def gen_17009(pkt, db, uid):
    """材料查询 → sc_17009 material_list（仅编码 num>0 的条目，0 数量不编码）"""
    sql = """
        SELECT m.id, m.num 
        FROM material m
        JOIN item_catalog ic ON m.id = ic.id
        WHERE m.uid = ? AND m.num > 0 AND ic.type IN (4, 5, 6, 10, 14, 20)
          AND m.id NOT IN (1, 116601, 52001, 52002, 52003)
    """
    try:
        mats = db.query(sql, (uid,))
    except Exception:
        mats = [r for r in db.get_material(uid) if r.get("num", 0) > 0 and r["id"] not in (1, 116601, 52001, 52002, 52003)]
    obj = {"material_list": [{"id": r["id"], "num": r["num"]} for r in mats]}
    payload = encode("sc_17009", obj)
    idx, srv = _fidx(pkt)
    return [build_frame(17009, payload, idx, srv)]


def gen_15009(pkt, db, uid):
    """货币查询 → sc_15009 currency_list + last_fatigue_recover_time（required，格式必须齐全）。
    对齐真实服务器（93级实测）：真实 36 条 = 33 条 num>0 + 3 条特定 0 值货币(id 30/31/32)；
    库里 197 行其余 158 个 num=0 货币真实不编码（账号未拥有）。"""
    last_recover_ts = 0
    if db is not None and uid:
        try:
            import fatigue_service as _fs
            _, last_recover_ts = _fs.settle_fatigue(db, uid)
        except Exception:
            pass
    last_recover_ts = 0 if last_recover_ts is None else int(last_recover_ts)
    curs = db.get_currency(uid)
    # 编码规则：num>0 全部 + 0 值但属于"需展示"白名单（核心货币/抽卡券必须编码 0 值，确保客户端扣减至 0 时 UI 正确清零）
    _ZERO_SHOW = {1, 2, 5, 19, 30, 31, 32, 36, 38, 101, 53029, 53111}
    items = [{"id": r["id"], "num": r["num"]} for r in curs
             if r.get("num", 0) > 0 or r["id"] in _ZERO_SHOW]
    obj = {"currency_list": items,
           "last_fatigue_recover_time": last_recover_ts}
    payload = encode("sc_15009", obj)
    idx, srv = _fidx(pkt)
    return [build_frame(15009, payload, idx, srv)]


def gen_23009(pkt, db, uid):
    """玩家信息 → sc_23009（nick/total_exp/hero_num/plot_progress，required 字段齐全，users 表驱动）"""
    u = db.get("users", uid)
    if not u:
        return None
    # hero_num 必须与 sc_14009 实际编码数一致（hero_codec 只编码有 hero_<id>.json 模板的英雄，
    # 库 hero 表可能比模板多——直接用 hero_codec 的编码数，防客户端数据不一致卡死）
    try:
        import hero_codec as _hc
        _rows = db.query("SELECT id FROM hero WHERE uid=? AND unlock=1", (uid,))
        _n = sum(1 for r in _rows if _hc.has_hero_template(r["id"]))
    except Exception:
        _n = db.query("SELECT COUNT(*) AS n FROM hero WHERE uid=? AND unlock=1", (uid,))[0]["n"]
    obj = {"nick": u.get("nick", ""), "total_exp": u.get("exp", 0),
           "hero_num": _n,
           "plot_progress": u.get("plot_progress") or 0,
           "is_changed_nick": u.get("is_changed_nick") or 0,
           "system_change_nick_times": u.get("change_nick_times") or 0}
    payload = encode("sc_23009", obj)
    idx, srv = _fidx(pkt)
    return [build_frame(23009, payload, idx, srv)]


def gen_11010(pkt, db, uid):
    """签到查询 → sc_11011（奖励列表 result=0）"""
    # 每日签到（ActivityConst.SIGN=3）奖励，后续按 activity_id 区分
    payload = bytes.fromhex("0800120608edba021032")  # result=0 + item_list（素材同款）
    idx, srv = _fidx(pkt)
    return [build_frame(11011, payload, idx, srv)]


def gen_11081(pkt, db, uid):
    """执行签到 → sc_11082（result=0 成功）"""
    payload = b"\x08\x00\x10\x01"
    idx, srv = _fidx(pkt)
    return [build_frame(11082, payload, idx, srv)]


def gen_34024(pkt, db, uid):
    """月卡奖励查询 → sc_34025（result=0 + is_sign=0 + reward_list）"""
    payload = bytes.fromhex("080010001a040801105a")
    idx, srv = _fidx(pkt)
    return [build_frame(34025, payload, idx, srv)]


def gen_10500(pkt, db, uid):
    """刷新时间同步 → sc_10501（动态时间戳）+ sc_12045（每日免费体力状态同步）"""
    import time
    now = int(time.time())
    import middleware as _mw
    payload = _mw.gen_10501_payload(now)
    idx, srv = _fidx(pkt)
    frames = [build_frame(10501, payload, idx, srv)]
    try:
        p45 = gen_payload(12045, uid=uid, db=db)
        if p45:
            frames.append(build_frame(12045, p45, idx, srv + 1))
    except Exception:
        pass
    return frames


def gen_10700(pkt, db, uid):
    """服务器时间 → sc_10701"""
    import time
    from codec import _varint
    payload = b"\x08" + _varint(int(time.time()))
    idx, srv = _fidx(pkt)
    return [build_frame(10701, payload, idx, srv)]


def gen_19036(pkt, db, uid):
    """聊天已读 ack → sc_19037 result=0"""
    idx, srv = _fidx(pkt)
    return [build_frame(19037, b"\x08\x00", idx, srv)]


def gen_32132(pkt, db, uid):
    """玩家背景/英雄上报 → sc_27013 + sc_27005（素材实测：客户端期望两帧，缺 27005 会卡）"""
    idx = pkt.get("index", 0)
    srv = pkt.get("server_idx", 0) + 1
    return [build_frame(27013, b"\x08\x00", idx, srv),
            build_frame(27005, b"\x08\x05", idx, srv + 1)]


def gen_27012(pkt, db, uid):
    """聊天操作 → sc_27013 result=0"""
    idx, srv = _fidx(pkt)
    return [build_frame(27013, b"\x08\x00", idx, srv)]


def gen_49001(pkt, db, uid):
    """因果观测全量进度查询 → sc_49001 chess_map_list"""
    payload = gen_payload(49001, uid=uid, db=db)
    idx, srv = _fidx(pkt)
    return [build_frame(49001, payload, idx, srv)]


def gen_49023(pkt, db, uid):
    """因果观测开放时间戳查询 → sc_49023 chess_open_info_list"""
    payload = gen_payload(49023, uid=uid, db=db)
    idx, srv = _fidx(pkt)
    return [build_frame(49023, payload, idx, srv)]


def gen_sc_49003(uid, chapter_id, db=None):
    """生成因果观测进入地图响应 sc_49003 payload。"""
    db = db or get_db()
    session = db.get_or_create_warchess_session(uid, chapter_id)
    return encode("sc_49003", {
        "result": 0,
        "map_info": session
    })


def gen_28001(pkt, db, uid):
    """生成全量任务列表 sc_28001 (登录洪流第 68 帧)"""
    db.check_and_refresh_periodic_tasks(uid)
    tasks = db.query(
        "SELECT task_id, progress, complete_flag, expired_ts FROM task WHERE uid=?",
        (uid,)
    )
    if not tasks:
        cfgs = db.query("SELECT task_id, name, task_type FROM task_cfg")
        for c in cfgs:
            tid, name, ttype = c["task_id"], c["name"], c["task_type"]
            init_prog = 1 if tid == 6001 else 0
            db.execute(
                "INSERT OR IGNORE INTO task (uid, task_id, name, task_type, progress, complete_flag, expired_ts, claimed_ts) "
                "VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
                (uid, tid, name, ttype, init_prog)
            )
        tasks = db.query(
            "SELECT task_id, progress, complete_flag, expired_ts FROM task WHERE uid=?",
            (uid,)
        )

    try:
        from task_listener import sync_dorm_illu_tasks
        sync_dorm_illu_tasks(db, uid)
        tasks = db.query(
            "SELECT task_id, progress, complete_flag, expired_ts, claimed_ts FROM task WHERE uid=?",
            (uid,)
        )
    except Exception:
        pass

    assignment_list = []
    for t in tasks:
        is_claimed = bool(t.get("claimed_ts"))
        assignment_list.append({
            "id": int(t["task_id"]),
            "progress": int(t.get("progress") or 0),
            "complete_flag": 1 if is_claimed else int(t.get("complete_flag") or 0),
            "expired_timestamp": int(t.get("expired_ts") or 0)
        })

    return encode("sc_28001", {
        "assignment_list": assignment_list,
        "send_type": 0,
        "newbie_phase": 19,
        "assignment_phase": 1
    })


def gen_28019(pkt, db, uid):
    """生成全量活跃度数据 sc_28019 (登录洪流第 69 帧)"""
    pt_dict = db.get_activity_pt_dict(uid)
    pt_list = []
    for pt_id, val in sorted(pt_dict.items()):
        pt_list.append({
            "activity_pt_id": int(pt_id),
            "active_point": int(val.get("active_point") or 0),
            "get_id_list": [int(x) for x in val.get("get_id_list", [])]
        })
    return encode("sc_28019", {
        "pt_list": pt_list
    })


def gen_16015(pkt, db, uid):
    """生成卡池列表数据 sc_16015（委托 DrawService 动态组装）"""
    from draw_service import DrawService
    import res_version_manager
    cur_ver = res_version_manager.get_current_version()
    svc = DrawService.get_instance(db=db)
    payload = svc.build_16015_payload(uid, res_version=cur_ver)
    return encode("sc_16015", payload)


# ---------------- 路由表 ----------------
ROUTER = {
    16015: gen_16015,   # 卡池列表
    17009: gen_17009,   # 材料
    15009: gen_15009,   # 货币
    23009: gen_23009,   # 玩家
    11010: gen_11010,   # 签到查询
    11081: gen_11081,   # 执行签到
    34024: gen_34024,   # 月卡
    10500: gen_10500,   # 刷新时间
    10700: gen_10700,   # 服务器时间
    27012: gen_27012,   # 聊天操作
    49001: gen_49001,   # 因果观测进度
    49023: gen_49023,   # 因果观测开放时间
    # 19036/32132 回退素材 resp_map（素材实测：19036→30003 邮件列表、32132→27013+27005 原帧，
    # 生成器的 ack 响应缺邮件/房间数据导致客户端大厅初始化卡住）
}


def generate(cmd, pkt, uid=DEFAULT_UID, db=None):
    """统一入口：cmd + 请求包 → 帧列表 bytes。无路由返回 None（服务器不响应）。"""
    db = db or get_db()
    fn = ROUTER.get(cmd)
    if fn is None:
        return None
    try:
        return fn(pkt, db, uid)
    except Exception as e:
        print(f"[generator] cmd={cmd} 生成失败: {e}")
        return None


_HERO_PAYLOAD = None
_HERO_PAYLOAD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero_14009_payload.bin")

# 14009 库驱动：模板 item（84 英雄） + 库字段覆盖（unlock 过滤 / module_level / module_assignment）
_ASSIGN_PHASE = None

def _assignment_phases():
    """assignmentcfg.lua 提取的 {task_id: [type, phase]}（PLOT=3 剧情阶段 / ALPHA=4 新手阶段）。"""
    global _ASSIGN_PHASE
    if _ASSIGN_PHASE is None:
        import json as _jap
        try:
            raw = _jap.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "assignment_phase.json"), encoding="utf-8"))
            _ASSIGN_PHASE = {int(k): (int(v["type"]), int(v["phase"])) for k, v in raw.items()}
        except Exception:
            _ASSIGN_PHASE = {}
    return _ASSIGN_PHASE

_HERO_ITEMS = None
_HERO_ITEMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analysis_scripts", "协议分析", "hero_items_84.json")


def _load_hero_items():
    global _HERO_ITEMS
    if _HERO_ITEMS is None:
        import json as _json
        if os.path.exists(_HERO_ITEMS_FILE):
            _HERO_ITEMS = _json.load(open(_HERO_ITEMS_FILE, encoding="utf-8"))
    return _HERO_ITEMS or {}


def _varint(v):
    # 负数先掩码成 64 位无符号：Python 负数右移恒为 -1，否则此处死循环（见 hero_codec 同注）
    v = int(v)
    if v < 0:
        v &= 0xFFFFFFFFFFFFFFFF
    out = b""
    while True:
        x = v & 0x7F
        v >>= 7
        if v:
            out += bytes([x | 0x80])
        else:
            return out + bytes([x])


def _rd(b, i):
    v, sh = 0, 0
    while i < len(b):
        x = b[i]
        i += 1
        v |= (x & 0x7F) << sh
        if not (x & 0x80):
            return v, i
        sh += 7
    return v, i


def _set_skill_levels(hbi, skill_list):
    """覆盖 hbi 里每个技能 field5 的 field2(skill_level)。skill_list: [[skill_id, level], ...]"""
    if not skill_list:
        return hbi
    lv_map = {int(s[0]): int(s[1]) for s in skill_list if isinstance(s, (list, tuple)) and len(s) >= 2}
    out = b""
    i = 0
    while i < len(hbi):
        tag, i2 = _rd(hbi, i)
        f, w = tag >> 3, tag & 7
        if f == 5 and w == 2:
            ln, i3 = _rd(hbi, i2)
            sub = hbi[i3:i3+ln]
            st, sp = _rd(sub, 0)
            sid, _ = _rd(sub, sp)
            nv = lv_map.get(sid)
            if nv is not None:
                sub = _set_varint_field(sub, b"\x10", nv)
            out += hbi[i:i2] + _varint(len(sub)) + sub
            i = i3 + ln
        elif w == 0:
            _, i3 = _rd(hbi, i2)
            out += hbi[i:i3]
            i = i3
        elif w == 2:
            ln, i3 = _rd(hbi, i2)
            out += hbi[i:i3+ln]
            i = i3 + ln
        else:
            i = i2
    return out


def _set_varint_field(item, tag_byte, val):
    """在 item 里把指定 tag 的 varint 值替换为 val（变长安全：读旧值长度，写新 varint）"""
    idx = item.find(tag_byte)
    if idx >= 0 and idx + 1 < len(item):
        j = idx + 1
        while j < len(item) and (item[j] & 0x80):
            j += 1
        new_v = _varint(val)
        return item[:idx+1] + new_v + item[j+1:]
    return item


def _load_hero_payload():
    global _HERO_PAYLOAD
    if _HERO_PAYLOAD is None and os.path.exists(_HERO_PAYLOAD_FILE):
        _HERO_PAYLOAD = open(_HERO_PAYLOAD_FILE, "rb").read()
    return _HERO_PAYLOAD


# ---------------- 签到公共逻辑（每日 activity_id=3 / 月卡 activity_id=34024） ----------------

_SIGN_DAILY = 3
_SIGN_MONTHCARD = 34024

_SEVEN_DAY_REWARD = None

def seven_day_reward(activity_id, nth):
    """七日/限时签到第 nth 次的奖励：ActivityCumulativeSignCfg[id].config_list[nth-1]
    → SignCfg[sign_id].reward（提取自 x64 Lua：activity_cumulative_sign_cfg.json /
    sign_cfg.json）。返回 (item_id, num) 或 None。"""
    global _SEVEN_DAY_REWARD
    if _SEVEN_DAY_REWARD is None:
        import json as _j7
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            cl = _j7.load(open(os.path.join(base, "activity_cumulative_sign_cfg.json"), encoding="utf-8"))
            sc = _j7.load(open(os.path.join(base, "sign_cfg.json"), encoding="utf-8"))
            _SEVEN_DAY_REWARD = {int(a): [tuple(sc[str(sid)]) for sid in ids if str(sid) in sc]
                                 for a, ids in cl.items()}
        except Exception:
            _SEVEN_DAY_REWARD = {}
    lst = _SEVEN_DAY_REWARD.get(int(activity_id)) or []
    return lst[nth - 1] if 1 <= nth <= len(lst) else None


def gen_11015(activity_id, uid=DEFAULT_UID, db=None):
    """生成指定七日签到活动的 sc_11015 状态帧。"""
    db = db or get_db()
    rows = db.query("SELECT sign_count, last_sign_ts FROM sign WHERE uid=? AND activity_id=?",
                    (uid, int(activity_id)))
    cnt = rows[0]["sign_count"] if rows else 0
    last = rows[0]["last_sign_ts"] if rows else 0
    return encode("sc_11015", {"activity_id": int(activity_id), "sign_count": cnt,
                               "last_sign_time": last})


def get_game_time(ts=None):
    """深空之眼业务时间：每日 05:00:00 刷新切天（ts - 5*3600）。
    与客户端 manager.time:GetDeltaToday() 完全对称。"""
    import time as _t
    if ts is None:
        ts = _t.time()
    return _t.localtime(ts - 5 * 3600)


def _mc_signed_today(db, uid):
    """月卡今日是否已签：配合 update_ts 按每日 5:00 刷新判定。"""
    mc = db.query("SELECT is_sign, update_ts FROM month_card WHERE uid=?", (uid,))
    if not mc or mc[0]["is_sign"] != 1:
        return False
    ts = mc[0]["update_ts"] or 0
    if not ts:
        return False
    a = get_game_time(int(ts))
    b = get_game_time()
    return (a.tm_year, a.tm_mon, a.tm_mday) == (b.tm_year, b.tm_mon, b.tm_mday)


def _sign_state(db, uid, activity_id):
    """签到状态：{year, month, day, sign_list, sign_count, signed_today}。

    两类语义（按 05:00:00 业务日界线计算）：
    - 日历签（activity 3 每日 / 34024 月卡）：累计机制（老大定稿），不要求连续、
      不绑定日期，第 N 次签到 → 奖励表第 N 项；跨月（业务月）重置 sign_list/sign_count。
    - 七日/限时签到（4300101 等）：种子行 year/month 为 NULL，按 last_sign_ts 判当日（>=当日5:00），
      count 永不归零。
    """
    import json as _j
    now = get_game_time()
    rows = db.query("SELECT * FROM sign WHERE uid=? AND activity_id=?", (uid, activity_id))
    row = rows[0] if rows else None

    # 七日/限时签到（非日历活动）：一律按 last_sign_ts 判当日，count 只增不减。
    if activity_id not in (_SIGN_DAILY, _SIGN_MONTHCARD):
        ts = int(row.get("last_sign_ts") or 0) if row else 0
        a = get_game_time(ts) if ts else None
        signed = bool(a and (a.tm_year, a.tm_mon, a.tm_mday) == (now.tm_year, now.tm_mon, now.tm_mday))
        return {"year": now.tm_year, "month": now.tm_mon, "day": now.tm_mday,
                "sign_list": [], "sign_count": int(row.get("sign_count") or 0) if row else 0,
                "signed_today": signed}

    if row and row.get("year") == now.tm_year and row.get("month") == now.tm_mon:
        try:
            sl = _j.loads(row.get("sign_list") or "[]")
        except Exception:
            sl = []
        is_signed = (now.tm_mday in sl)
        if activity_id == 34024 and not is_signed:
            is_signed = _mc_signed_today(db, uid)
        return {"year": now.tm_year, "month": now.tm_mon, "day": now.tm_mday,
                "sign_list": sl, "sign_count": row.get("sign_count") or len(sl),
                "signed_today": is_signed}
    is_signed = False
    if activity_id == 34024:
        is_signed = _mc_signed_today(db, uid)
    return {"year": now.tm_year, "month": now.tm_mon, "day": now.tm_mday,
            "sign_list": [], "sign_count": 0, "signed_today": is_signed}



def _sign_reward(db, month, n):
    """第 n 次累计签到奖励：优先 (当前月, n)，无则 (0, n) 兜底，再无则 1×100 移转之辉。"""
    rows = db.query("SELECT reward_id, reward_num FROM sign_reward_cfg WHERE month=? AND day=?",
                    (month, n))
    if not rows:
        rows = db.query("SELECT reward_id, reward_num FROM sign_reward_cfg WHERE month=0 AND day=?",
                        (n,))
    if rows:
        return (rows[0]["reward_id"] or 0, rows[0]["reward_num"] or 0)
    return (1, 100)


def gen_payload(cmd, uid=DEFAULT_UID, db=None, **kw):
    """仅编码 payload（不带帧头）——供 10200 进服响应段帧替换用。
    返回 bytes 或 None。支持：17009 材料 / 15009 货币 / 23009 玩家 / 14009 英雄 / 16015 动态卡池。"""
    db = db or get_db()
    if cmd == 16015:
        from draw_service import DrawService
        svc = DrawService.get_instance(db=db)
        cur_ver = kw.get("res_version") or kw.get("cur_ver")
        payload = svc.build_16015_payload(uid, res_version=cur_ver)
        return encode("sc_16015", payload)
    if cmd == 14501:
        from oath_service import OathService
        return OathService.get_instance().build_14501_payload(db, uid)
    if cmd == 14503:
        from oath_service import OathService
        return OathService.get_instance().build_14503_payload(db, uid)
    if cmd == 28001:
        return gen_28001(None, db, uid)
    if cmd == 28019:
        return gen_28019(None, db, uid)
    if cmd == 89401:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_89401_payload(db, uid)
    if cmd == 84331:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_84331_payload(db, uid)
    if cmd == 68181:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_68181_payload(db, uid)
    if cmd == 60105:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_60105_payload(db, uid)
    if cmd == 60107:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_60107_payload(db, uid)
    if cmd == 68171:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_68171_payload(db, uid)
    if cmd == 24051:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_24051_payload(db, uid)
    if cmd == 89421:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_89421_payload(db, uid)
    if cmd == 83016:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_83016_payload(db, uid)
    if cmd == 83020:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_83020_payload(db, uid)
    if cmd == 83021:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_83021_payload(db, uid)
    if cmd == 83026:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_83026_payload(db, uid)
    if cmd == 89701:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_89701_payload(db, uid)
    if cmd == 61047:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_61047_payload(db, uid)
    if cmd == 83124:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_83124_payload(db, uid)
    if cmd == 89011:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_89011_payload(db, uid)
    if cmd == 89601:
        from minigame_service import MiniGameService
        return MiniGameService.get_instance(db=db).build_89601_payload(db, uid)
    if cmd == 89201:
        from autochess_service import AutoChessService
        return AutoChessService.get_instance(db=db).build_89201_payload(db, uid)
    if cmd == 90051:
        from autochess_service import AutoChessService
        return AutoChessService.get_instance(db=db).build_90051_payload(db, uid)
    if cmd == 89125:
        from autochess_service import AutoChessService
        return AutoChessService.get_instance(db=db).build_89125_payload(db, uid)
    if cmd == 90007:
        from autochess_service import AutoChessService
        return AutoChessService.get_instance(db=db).build_90007_payload(db, uid)
    if cmd == 89207:
        from autochess_service import AutoChessService
        return AutoChessService.get_instance(db=db).build_89207_payload(db, uid)
    if cmd == 89101:
        from autochess_service import AutoChessService
        return AutoChessService.get_instance(db=db).build_89101_payload(db, uid)




    if cmd == 17009:
        sql = """
            SELECT m.id, m.num 
            FROM material m
            JOIN item_catalog ic ON m.id = ic.id
            WHERE m.uid = ? AND m.num > 0 
              AND (ic.type IS NULL OR ic.type NOT IN (1, 2, 3, 7, 8, 9, 15, 16, 21, 24))
              AND m.id NOT IN (1, 116601, 52001, 52002, 52003)
        """
        try:
            mats = db.query(sql, (uid,))
        except Exception:
            mats = [r for r in db.get_material(uid) if r.get("num", 0) > 0 and r["id"] not in (1, 116601, 52001, 52002, 52003)]
        return encode("sc_17009",
                      {"material_list": [{"id": r["id"], "num": r["num"]} for r in mats]})
    if cmd == 17023:
        # 材料/资源增量推送（sc_17023: normal_items, equip_list, weapon_list 等）
        # 支持原子化差量推送：如果传入 touched_items，则只下发变动的道具，未变动部分不下发
        touched = kw.get("touched_items")
        wl = kw.get("weapon_list")
        el = kw.get("equip_list")
        if touched is not None:
            bl = kw.get("back_home_list")
            if not touched and not wl and not el and not bl:
                return None
            normal_items = []
            back_home_list = list(bl) if bl else []
            for iid in touched:
                if iid in (116601, 52001, 52002, 52003):
                    continue
                try:
                    cat_rows = db.query("SELECT type FROM item_catalog WHERE id=?", (iid,))
                    itype = cat_rows[0].get("type") if cat_rows else None
                    if not itype:
                        if 950000 <= iid < 970000:
                            itype = 15
                        elif 51000 <= iid < 52000:
                            itype = 16
                        elif 1 <= iid <= 50:
                            itype = 1

                    if itype in (15, 16, 24) or (950000 <= iid < 970000):
                        # 家园后宅道具原子增量推送 (back_home_list -> itemaction 17023: DormAction.ModifyFurniture)
                        if itype == 15 or (950000 <= iid < 970000):
                            fur_rows = db.query("SELECT num, give_num FROM backhome_furniture WHERE uid=? AND furniture_id=?", (uid, iid))
                            if fur_rows:
                                back_home_list.append({
                                    "id": iid,
                                    "num": int(fur_rows[0].get("num") or 0),
                                    "give_num": int(fur_rows[0].get("give_num") or 0)
                                })
                        elif itype == 16:
                            ing_rows = db.query("SELECT num FROM backhome_ingredient WHERE uid=? AND item_id=?", (uid, iid))
                            if ing_rows:
                                back_home_list.append({
                                    "id": iid,
                                    "num": int(ing_rows[0].get("num") or 0),
                                    "give_num": 0
                                })
                        elif itype == 24:
                            back_home_list.append({"id": iid, "num": 1, "give_num": 0})
                        continue

                    if itype not in (1, 2, 3, 4, 5, 6, 8, 10, 11, 12, 13, 14, 17, 18, 20, 21, 22, 23, 25, 26, 27, 28, 29):
                        continue
                    
                    bal = 0
                    if itype == 1:
                        c_rows = db.query("SELECT num FROM currency WHERE uid=? AND id=?", (uid, iid))
                        if c_rows:
                            bal = int(c_rows[0].get("num") or 0)
                    elif itype == 2:
                        h_rows = db.query("SELECT 1 FROM heroes WHERE uid=? AND id=?", (uid, iid))
                        bal = 1 if h_rows else 0
                    elif itype == 3:
                        hid = iid % 10000 if iid > 10000 else iid
                        p_rows = db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hid))
                        if p_rows:
                            bal = int(p_rows[0].get("num") or 0)
                    elif itype == 8:
                        s_rows = db.query("SELECT 1 FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (uid, iid))
                        bal = 1 if s_rows else 0
                    elif itype == 21:
                        sc_rows = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
                        bal = int(sc_rows[0].get("num") or 0) if sc_rows else 1
                    elif itype in (11, 12, 13, 18, 22, 23, 25, 26, 28):
                        m_rows = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
                        if m_rows and int(m_rows[0].get("num") or 0) > 0:
                            bal = int(m_rows[0]["num"])
                        else:
                            p_rows = db.query("SELECT obtained FROM player_card WHERE uid=? AND item_id=?", (uid, iid))
                            bal = int(p_rows[0].get("obtained") or 0) if p_rows else 0
                    else:
                        m_rows = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
                        if m_rows:
                            bal = int(m_rows[0].get("num") or 0)
                    
                    normal_items.append({"id": iid, "num": bal})
                except Exception:
                    pass
            payload_dict = {}
            if normal_items:
                payload_dict["normal_items"] = normal_items
            if back_home_list:
                payload_dict["back_home_list"] = back_home_list
            if wl:
                payload_dict["weapon_list"] = wl
            if el:
                payload_dict["equip_list"] = el
            if not payload_dict:
                return None
            return encode("sc_17023", payload_dict)

        # 默认全量安全材料回退
        sql = """
            SELECT m.id, m.num 
            FROM material m
            JOIN item_catalog ic ON m.id = ic.id
            WHERE m.uid = ? AND m.num >= 0 
              AND (ic.type IS NULL OR ic.type NOT IN (2, 3, 7, 8, 9, 15, 16, 21, 24))
              AND m.id NOT IN (1, 116601, 52001, 52002, 52003)
        """
        try:
            mats = db.query(sql, (uid,))
        except Exception:
            mats = [r for r in db.get_material(uid) if r.get("num", 0) >= 0 and r["id"] not in (1, 116601, 52001, 52002, 52003)]
        payload_dict = {"normal_items": [{"id": r["id"], "num": r["num"]} for r in mats]}
        if wl:
            payload_dict["weapon_list"] = wl
        if el:
            payload_dict["equip_list"] = el
        return encode("sc_17023", payload_dict)
    if cmd == 17027:
        # 累计签到数据（accumulatesigndata.lua:19-26 InitAccumulateSignData）
        # 此前登录洪流用素材静态帧：领取后 award_ids 不更新，重登"复活"红点
        row = db.query("SELECT version, open_sign, login_days FROM accumulate_sign WHERE uid=?", (uid,))
        aids = db.query("SELECT award_id FROM accumulate_sign_award WHERE uid=? ORDER BY award_id", (uid,))
        if row:
            return encode("sc_17027", {
                "version": int(row[0].get("version") or 1),
                "open_sign": int(row[0].get("open_sign") or 0),
                "login_days": int(row[0].get("login_days") or 0),
                "award_ids": [int(r["award_id"]) for r in aids]})
        return None
    if cmd == 17033:
        # 累计签到折扣购买/战令数据（accumulatesigndata.lua:28-34 InitDiscountData）
        row = db.query("SELECT version, cumulative_buy_card_num FROM accumulate_sign_discount WHERE uid=?", (uid,))
        bps = db.query("SELECT bp_id FROM accumulate_sign_bp WHERE uid=? ORDER BY bp_id", (uid,))
        ver = int(row[0]["version"] or 2) if row else 2
        card_num = int(row[0]["cumulative_buy_card_num"] or 0) if row else 0
        bp_list = [int(r["bp_id"]) for r in bps]
        return encode("sc_17033", {
            "version": ver,
            "cumulative_buy_card_num": card_num,
            "cumulative_buy_battlepass_list": bp_list
        })
    if cmd == 11013:
        # 每日签到日历（登录推）：真实年/月/日 + 本月已签天数集合（跨月自动空）
        st = _sign_state(db, uid, 3)
        return encode("sc_11013", {"year": st["year"], "month": st["month"],
                                   "day": st["day"], "sign_list": st["sign_list"]})
    if cmd == 10503:
        # 服务器时间同步（登录推，seq=4）：真实 timestamp + 5 点跨天刷新基准
        # 杜绝旧素材时间戳（1786411503≈8/10）覆盖客户端 TimeMgr，导致客户端误判为 8/10 产生重复弹窗
        import time as _t
        import datetime as _dt
        now_ts = int(_t.time())
        now = _dt.datetime.fromtimestamp(now_ts)

        # 每日 5 点刷新
        next_day = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if now >= next_day:
            next_day += _dt.timedelta(days=1)

        # 每周一 5 点刷新 (weekday: 0=Monday)
        days_ahead = (0 - now.weekday()) % 7
        if days_ahead == 0 and now >= now.replace(hour=5, minute=0, second=0, microsecond=0):
            days_ahead = 7
        next_week = (now + _dt.timedelta(days=days_ahead)).replace(hour=5, minute=0, second=0, microsecond=0)

        # 每月 1 日 5 点刷新
        year = now.year + 1 if now.month == 12 else now.year
        month = 1 if now.month == 12 else now.month + 1
        next_month = _dt.datetime(year, month, 1, 5, 0, 0)

        return encode("sc_10503", {
            "timestamp": now_ts,
            "verify_timestamp": 316800,
            "next_refresh_time": int(next_day.timestamp()),
            "next_weekly_refresh_time": int(next_week.timestamp()),
            "next_monthly_refresh_time": int(next_month.timestamp())
        })
    if cmd == 10201:
        # 进服时间同步（登录推）：真实 timestamp——2026-08-17 修复：素材旧时间戳
        # （1786411503≈8/10）导致客户端 GetDeltaToday 用旧日期判断签到（认为未签）
        import time as _t
        now = int(_t.time())
        lt = _t.localtime(now)
        # 本周一 0 点（周一凌晨）
        monday = now - ((lt.tm_wday) * 86400 + lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
        return encode("sc_10201", {"timestamp": now, "monday_0oclock_timestamp": monday,
                                   "verify_timestamp": 316800})
    if cmd == 10043:
        # 登录验证响应（洪流版，seq=2）：素材同构 + timestamp 真实化——
        # 2026-08-17 修复：洪流里的素材 10043 旧时间戳（8/10）会覆盖 10042 响应的新时间戳，
        # 客户端时间基准=旧 → GetDeltaToday 错 → 签到/月卡判断错乱。
        # uid_sign/register_timestamp 保持素材值（服务器不校验，客户端回带一致）。
        import time as _t
        return encode("sc_10043", {"result": 0, "register_timestamp": 1760068476,
                                   "timestamp": int(_t.time()), "verify_timestamp": 316800,
                                   "uid_sign": "f52a9206f87a10eaa6caa54146a90db9"})
    if cmd == 11015:
        # 七日签到状态（登录推）：根据当前客户端版本动态自适应下发
        import time as _t
        import res_version_manager
        version_cfg = res_version_manager.get_version_config(kw.get("res_version"))
        act_id = int(version_cfg["sign_activity_id"])
        now_ts = int(_t.time())
        return encode("sc_11015", {
            "activity_id": act_id,
            "sign_count": 7,
            "last_sign_time": now_ts
        })
    if cmd == 12045:
        # 每日免费体力甜点（百宝囊上下午领蛋，登录推，seq=144）
        import time as _t
        today_str = _t.strftime("%Y-%m-%d")
        rows = db.query("SELECT type FROM daily_fatigue WHERE uid=? AND date_str=?", (uid, today_str))
        got_types = set(r["type"] for r in rows)
        desserts = [{"type": t, "is_got": (t in got_types)} for t in (11, 18)]
        return encode("sc_12045", {"daily_fatigue_dessert_list": desserts})
    if cmd == 88305:
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        out = svc.get_outside_data(uid)
        return encode("sc_88305", {
            "template_id": out["template_id"],
            "difficult": out["difficult"],
            "max_difficult": out["max_difficult"],
            "collection_list": out["collection_list"],
            "unlock_collection": out["unlock_collection"],
            "view_collection": out["view_collection"],
            "tree_list": out["tree_list"],
            "his_difficult": out["his_difficult"],
            "his_avg": out["his_avg"],
            "last_id": out["last_id"]
        })
    if cmd == 88309:
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        out = svc.get_outside_data(uid)
        return encode("sc_88309", {
            "template_id": out["template_id"],
            "point": out["point"],
            "reward_list": out["reward_list"]
        })
    if cmd == 88315:
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        out = svc.get_outside_data(uid)
        return encode("sc_88315", {
            "activity_id": out["activity_id"],
            "next_timestamps": out["next_timestamps"],
            "fetters_id": out["fetters_id"]
        })
    if cmd == 88001:
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        s = svc.get_session_data(uid)
        if s and s.get("in_game", 0) != 0:
            return encode("sc_88001", s)
        return None
    if cmd == 11011:
        # 签到查询响应（登录推 + 11010 响应）：item_list = 下次累计奖励（第 sign_count+1 天）
        st = _sign_state(db, uid, 3)
        items = []
        if not st["signed_today"]:
            rw = _sign_reward(db, st["month"], st["sign_count"] + 1)
            if rw:
                items.append({"id": rw[0], "num": rw[1]})
        return encode("sc_11011", {"result": 0, "item_list": items})
    if cmd == 34025:
        # 月卡签到状态（登录推）：is_sign = 今日已签；reward_list = 可领 90 移转之辉
        st = _sign_state(db, uid, 34024)
        return encode("sc_34025", {"result": 0, "is_sign": 1 if st["signed_today"] else 0,
                                   "reward_list": [{"id": 1, "num": 90}]})
    if cmd == 34023:
        # 月卡数据（RechargeData 初始化）：月卡到期时间必须在未来
        import time as _t
        now_ts = int(_t.time())
        st = _sign_state(db, uid, 34024)
        mc = db.query("SELECT monthly_card_timestamp, is_sign FROM month_card WHERE uid=?", (uid,))
        # monthly_card_timestamp 可能为 NULL —— 直接和 now_ts 比较会 TypeError
        _dead = (mc[0]["monthly_card_timestamp"] or 0) if mc else 0
        dead_ts = _dead if _dead > now_ts else now_ts + 30 * 86400
        is_s = 1 if st["signed_today"] else 0   # 以 signed_today 为准（含 update_ts 当日判定）
        return encode("sc_34023", {"monthly_card_num": 1, "monthly_card_timestamp": dead_ts,
                                   "is_sign": is_s})
    if cmd == 34101:
        # 大月卡数据（BigMonthCardData 初始化）
        import time as _t, json as _j
        brow = db.query("SELECT * FROM big_month_card WHERE uid=?", (uid,))
        brow = brow[0] if brow else {}
        total_times = int(brow.get("total_sign_times") or 0)
        # is_sign 判断：依据 big_month_card 自身 update_ts 是否落在自然日当天
        _bts = brow.get("update_ts") or 0
        _bsigned = False
        if brow.get("is_sign") == 1 and _bts:
            _a, _b = _t.localtime(int(_bts)), _t.localtime()
            _bsigned = (_a.tm_year, _a.tm_mon, _a.tm_mday) == (_b.tm_year, _b.tm_mon, _b.tm_mday)
        is_s = 1 if _bsigned else 0
        buy_ts = int(brow.get("buy_timestamp") or 0)
        try:
            rec_list = _j.loads(brow.get("total_sign_receive_list") or "[]")
        except Exception:
            rec_list = []
        try:
            daily_rec = _j.loads(brow.get("daily_record") or "[]")
        except Exception:
            daily_rec = []
        return encode("sc_34101", {
            "buy_timestamp": buy_ts,
            "is_sign": is_s,
            "total_sign_times": total_times,
            "total_sign_receive_list": [int(x) for x in rec_list],
            "daily_record": daily_rec,
            "is_expire_tip": int(brow.get("is_expire_tip") or 0),
            "template_id": int(brow.get("template_id") or 2)
        })
    if cmd == 59011:
        # 新手活动数据（ActivityNoobData 初始化）
        import json as _j
        import recharge_service as _rcs
        row = db.query("SELECT * FROM newbie_activity WHERE uid=?", (uid,))
        if not row:
            return None
        r = row[0]
        pts = db.query("SELECT pt_id FROM newbie_pt_reward WHERE uid=?", (uid,))
        pt_list = [int(p["pt_id"]) for p in pts]
        lvls = db.query("SELECT level FROM newbie_level_reward WHERE uid=?", (uid,))
        lvl_list = [int(p["level"]) for p in lvls]
        rec_svc = _rcs.RechargeService.get_instance(db=db)
        recharge_part = rec_svc.build_59009_payload(db, uid).get("newbie_recharge_reward") or {}
        return encode("sc_59011", {
            "completed_time": int(r.get("completed_time") or 0),
            "newbie_sign": {
                "now_sign_times": int(r.get("now_sign_times") or 0),
                "last_sign_timestamp": int(r.get("last_sign_ts") or 0)
            },
            "newbie_level_reward": {
                "received_level_list": lvl_list
            },
            "newbie_recharge_reward": recharge_part,
            "trigger_time": int(r.get("trigger_time") or 0),
            "max_phase": int(r.get("max_phase") or 7),
            "got_pt_id_list": pt_list,
            "version_id": int(r.get("version_id") or 3)
        })
    if cmd == 62011:
        # 回归活动数据（RegressionDataNew 初始化）
        import time as _t, json as _j
        now_ts = int(_t.time())
        row = db.query("SELECT * FROM regression WHERE uid=?", (uid,))
        r = row[0] if row else {}
        end_ts = int(r.get("end_ts") or (now_ts + 14 * 86400))
        try:
            rec_signs = _j.loads(r.get("received_sign_list") or "[]")
        except Exception:
            rec_signs = []
        return encode("sc_62011", {
            "end_timestamps": end_ts,
            "return_vs_id": int(r.get("return_vs_id") or 110),
            "return_level": 1,
            "mul_times": 1,
            "receive_sign_index": int(r.get("receive_sign_index") or 7),
            "received_sign_list": [int(x) for x in rec_signs],
            "left_day": max(0, int((end_ts - now_ts) / 86400)),
            "find_time": int(r.get("find_time") or 0),
            "open_draw_sign": True,
            "pic_sign": False
        })
    if cmd == 34031:
        # [Fix by Gemini 3.7-flash] 战令/通行证数据库驱动 → sc_34031 (自适应版本赛季)
        import json as _jm
        import time as _t
        from lazy_timer import get_weekly_mon_5am_ts
        import res_version_manager
        version_cfg = res_version_manager.get_version_config(kw.get("res_version"))
        expected_bp_id = int(version_cfg["battlepass_list_id"])

        b = db.get_or_create_battlepass(uid) if hasattr(db, "get_or_create_battlepass") else {}
        now_ts = int(_t.time())
        try:
            rec_info = _jm.loads(b.get("receive_info") or "[]")
        except Exception:
            rec_info = []
        start_ts = int(b.get("start_timestamp") or (now_ts - 7 * 86400))
        # 活动类时间不再沿用 DB 中会过期的赛季时间。
        end_ts = res_version_manager.FAR_FUTURE_TIMESTAMP
        refresh_ts = int(b.get("next_refresh_timestamp") or 0)
        if refresh_ts <= now_ts:
            refresh_ts = get_weekly_mon_5am_ts(now_ts) + 7 * 86400
        return encode("sc_34031", {
            "battlepass_list_id": expected_bp_id,
            "pay_level": int(b["pay_level"]) if b.get("pay_level") is not None else 0,
            "is_start": 1,
            "weekly_gain_exp": int(b.get("weekly_gain_exp") or 0),
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "next_refresh_timestamp": refresh_ts,
            "receive_info": rec_info
        })

    if cmd == 34007:
        import recharge_service
        svc = recharge_service.RechargeService.get_instance(db=db)
        return encode("sc_34007", svc.build_34007_payload(db, uid))

    if cmd == 34021:
        import recharge_service
        svc = recharge_service.RechargeService.get_instance(db=db)
        return encode("sc_34021", svc.build_34021_payload(db, uid))

    if cmd == 59009:
        import recharge_service
        svc = recharge_service.RechargeService.get_instance(db=db)
        return encode("sc_59009", svc.build_59009_payload(db, uid))

    if cmd == 15009:
        last_recover_ts = 0
        if db is not None and uid:
            try:
                import fatigue_service as _fs
                _, last_recover_ts = _fs.settle_fatigue(db, uid)
            except Exception:
                pass
        last_recover_ts = 0 if last_recover_ts is None else int(last_recover_ts)
        # 与 gen_15009 同规则：num>0 全发 + 0 值展示白名单（核心货币/抽卡券必须编码 0 值）
        _ZERO_SHOW = {1, 2, 5, 19, 30, 31, 32, 36, 38, 101, 53029, 53111}
        curs = [r for r in db.get_currency(uid) if r.get("num", 0) > 0 or r["id"] in _ZERO_SHOW]
        return encode("sc_15009",
                      {"currency_list": [{"id": r["id"], "num": r["num"]} for r in curs],
                       "last_fatigue_recover_time": last_recover_ts})
    if cmd == 17025:
        last_recover_ts = 0
        if db is not None and uid:
            try:
                import fatigue_service as _fs
                _, last_recover_ts = _fs.settle_fatigue(db, uid)
            except Exception:
                pass
        last_recover_ts = 0 if last_recover_ts is None else int(last_recover_ts)
        return encode("sc_17025", {"last_fatigue_recover_time": last_recover_ts})
    if cmd == 23009:
        u = db.get("users", uid)
        if not u:
            return None
        # 玩家信息补全（required 字段齐全，格式与素材还原体一致）：
        # hero_num 与 sc_14009 实际编码数一致（hero_codec 只编有模板的英雄，防客户端不一致卡死）
        try:
            import hero_codec as _hc
            _rows = db.query("SELECT id FROM hero WHERE uid=? AND unlock=1", (uid,))
            _n = sum(1 for r in _rows if _hc.has_hero_template(r["id"]))
        except Exception:
            _n = db.query("SELECT COUNT(*) AS n FROM hero WHERE uid=? AND unlock=1", (uid,))[0]["n"]
        return encode("sc_23009", {
            "nick": u.get("nick", ""), "total_exp": u.get("exp", 0),
            "hero_num": _n,
            "plot_progress": u.get("plot_progress") or 0,
            "is_changed_nick": u.get("is_changed_nick") or 0,
            "system_change_nick_times": u.get("change_nick_times") or 0})
    if cmd == 14009:
        # 英雄列表库驱动：hero 表独立列 → 展开结构覆盖 → 编码（hero_codec）
        import hero_codec as _hc
        payload = _hc.build_hero_payload(db, uid)
        return payload if payload else None
    if cmd == 68151:
        # 换装/场景卡池状态下发（默认诗蔻蒂换装子活动 4322201）
        state = db.get_skin_draw_pool_state(uid, 1001)
        state_list = [{"drop_id": did, "num": s["remain_num"]} for did, s in state.items()]
        return encode("sc_68151", {"activity_id": 4322201, "info": state_list})
    if cmd == 68161:
        # 换装活动剧情状态下发（默认 4322301）
        finished = db.get_skin_story_state(uid, 4322301)
        return encode("sc_68161", {"activity_id": 4322301, "story_stage": 0, "finished_story": finished})
    if cmd == 68185:
        # 誓约活动卡池状态下发（默认 4342401，卡池 1025）
        state = db.get_skin_draw_pool_state(uid, 1025)
        state_list = [{"drop_id": did, "num": s["remain_num"]} for did, s in state.items()]
        return encode("sc_68185", {
            "activity_id": 4342401,
            "info": state_list,
            "draw_info2": {"last_drop": 0, "already_drop": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
        })
    if cmd == 12039:
        # 聊天表情与自定义表情列表动态驱动（chat_sticker + material 表）
        base_stickers = [343, 240, 346, 227, 349, 352, 339, 207, 342, 239, 345, 242, 226, 348, 351, 338, 206, 341, 344, 241, 225, 347, 228, 350, 353, 321, 205, 340]
        unlocked = list(base_stickers)
        rows = db.query("SELECT emoji_id FROM chat_sticker WHERE uid=?", (uid,))
        for r in (rows or []):
            eid = int(r["emoji_id"])
            if eid < 90000 and eid not in unlocked:
                unlocked.append(eid)
        try:
            import inventory_service as _inv_svc
            stk_map = _inv_svc.get_sticker_item_map()
        except Exception:
            stk_map = {}
        mats = db.query("SELECT id FROM material WHERE uid=? AND id>=90000 AND id<92000 AND num>0", (uid,))
        for r in (mats or []):
            mid = int(r["id"])
            real_eid = stk_map.get(mid, mid)
            if real_eid < 90000 and real_eid not in unlocked:
                unlocked.append(real_eid)
        return encode("sc_12039", {
            "emoticon_id_list": unlocked[:20],
            "unlocked_emoji_list": unlocked
        })
    if cmd == 14019:
        # 英雄碎片列表动态驱动（hero_piece 表 → sc_14019 piece_list）
        rows = db.query("SELECT hero_id, num FROM hero_piece WHERE uid=? ORDER BY hero_id", (uid,))
        if rows:
            lst = [{"id": r.get("hero_id"), "num": r.get("num") or 0} for r in rows]
            return encode("sc_14019", {"piece_list": lst})
    if cmd == 11001:
        # 活动全量列表（sc_11001 activity_list）
        # 保证全量支线与常驻活动、以及在架卡池的 activity_id 处于开启激活状态，解除客户端时间锁与置灰
        from stage_service import StageService
        svc = StageService.get_instance(db=db)
        needed_acts = set(svc.get_active_activity_ids())

        import json as _j11
        import res_version_manager
        cur_ver = str(kw.get("res_version") or kw.get("cur_ver") or res_version_manager.get_current_version())
        version_cfg = res_version_manager.get_version_config(cur_ver)
        far_future_ts = res_version_manager.FAR_FUTURE_TIMESTAMP

        # 联动卡池服务，自动将当前激活的卡池对应 activity_id 注入并解除时间锁
        pool_act_map = {}
        all_pool_aids = set()
        try:
            from draw_service import DrawService
            draw_svc = DrawService.get_instance(db=db)
            pool_entries = draw_svc.get_active_activity_entries(uid, res_version=cur_ver)
            pool_act_map = {ent["activity_id"]: ent for ent in pool_entries}
            pool_cfg = draw_svc.load_pool_activity_cfg()
            all_pool_aids = {int(item["activity_id"]) for item in pool_cfg.values() if item.get("activity_id")}
        except Exception as _e_draw:
            pass

        # Theme 44 权威拓扑来自 311 ActivityCfg.lua 新增的 26 条记录；
        # 复刻换装活动则来自 311 ActivityToggleCfg.lua (53006~53010) 的引用。
        theme44_config = {
            4410001: {"theme": 44, "template": 100, "sub_activity_id_list": [4435001, 4435002, 4435003, 4435004, 4435005]},
            4400101: {"theme": 44, "template": 1, "sub_activity_id_list": []},
            4400301: {"theme": 44, "template": 3, "sub_activity_id_list": []},
            4400303: {"theme": 44, "template": 3, "sub_activity_id_list": []},
            4407301: {"theme": 44, "template": 73, "sub_activity_id_list": []},
            4407302: {"theme": 44, "template": 73, "sub_activity_id_list": []},
            4412001: {"theme": 44, "template": 120, "sub_activity_id_list": [4405601]},
            4405601: {"theme": 44, "template": 56, "sub_activity_id_list": [4418201]},
            4418201: {"theme": 44, "template": 182, "sub_activity_id_list": []},
            4435001: {"theme": 44, "template": 350, "sub_activity_id_list": []},
            4435002: {"theme": 44, "template": 350, "sub_activity_id_list": []},
            4435003: {"theme": 44, "template": 350, "sub_activity_id_list": []},
            4435004: {"theme": 44, "template": 350, "sub_activity_id_list": []},
            4435005: {"theme": 44, "template": 350, "sub_activity_id_list": []},
            4443301: {"theme": 44, "template": 433, "sub_activity_id_list": [4443401, 4443501]},
            4443302: {"theme": 44, "template": 433, "sub_activity_id_list": [4443402, 4443502]},
            4443303: {"theme": 44, "template": 433, "sub_activity_id_list": [4443403, 4443503]},
            4443304: {"theme": 44, "template": 433, "sub_activity_id_list": [4443404, 4443504]},
            4443401: {"theme": 44, "template": 434, "sub_activity_id_list": []},
            4443402: {"theme": 44, "template": 434, "sub_activity_id_list": []},
            4443403: {"theme": 44, "template": 434, "sub_activity_id_list": []},
            4443404: {"theme": 44, "template": 434, "sub_activity_id_list": []},
            4443501: {"theme": 44, "template": 435, "sub_activity_id_list": []},
            4443502: {"theme": 44, "template": 435, "sub_activity_id_list": []},
            4443503: {"theme": 44, "template": 435, "sub_activity_id_list": []},
            4443504: {"theme": 44, "template": 435, "sub_activity_id_list": []},
            # 皮肤抽奖 (Toggle 53006~53010) 与全部实体子活动
            312211: {"theme": 31, "template": 221, "sub_activity_id_list": [312221, 312222, 310049, 311465, 312231, 312233]},
            312221: {"theme": 31, "template": 222, "sub_activity_id_list": []},
            312222: {"theme": 31, "template": 222, "sub_activity_id_list": []},
            310049: {"theme": 31, "template": 4, "sub_activity_id_list": []},
            311465: {"theme": 31, "template": 146, "sub_activity_id_list": []},
            312231: {"theme": 31, "template": 223, "sub_activity_id_list": []},
            312233: {"theme": 31, "template": 223, "sub_activity_id_list": []},

            3522101: {"theme": 35, "template": 221, "sub_activity_id_list": [3522201, 3522202, 3522203, 3522301, 3500406, 3514601]},
            3522201: {"theme": 35, "template": 222, "sub_activity_id_list": []},
            3522202: {"theme": 35, "template": 222, "sub_activity_id_list": []},
            3522203: {"theme": 35, "template": 222, "sub_activity_id_list": []},
            3522301: {"theme": 35, "template": 223, "sub_activity_id_list": []},
            3500406: {"theme": 35, "template": 4, "sub_activity_id_list": []},
            3514601: {"theme": 35, "template": 146, "sub_activity_id_list": []},

            3722101: {"theme": 37, "template": 221, "sub_activity_id_list": [3722201, 3722202, 3722301, 3700407, 3714601]},
            3722201: {"theme": 37, "template": 222, "sub_activity_id_list": []},
            3722202: {"theme": 37, "template": 222, "sub_activity_id_list": []},
            3722301: {"theme": 37, "template": 223, "sub_activity_id_list": []},
            3700407: {"theme": 37, "template": 4, "sub_activity_id_list": []},
            3714601: {"theme": 37, "template": 146, "sub_activity_id_list": []},

            4022101: {"theme": 40, "template": 221, "sub_activity_id_list": [4022201, 4022202, 4022301, 4000410, 4014601]},
            4022201: {"theme": 40, "template": 222, "sub_activity_id_list": []},
            4022202: {"theme": 40, "template": 222, "sub_activity_id_list": []},
            4022301: {"theme": 40, "template": 223, "sub_activity_id_list": []},
            4000410: {"theme": 40, "template": 4, "sub_activity_id_list": []},
            4014601: {"theme": 40, "template": 146, "sub_activity_id_list": []},

            4122101: {"theme": 41, "template": 221, "sub_activity_id_list": [4122201, 4122202, 4122301, 4100402, 4114601]},
            4122201: {"theme": 41, "template": 222, "sub_activity_id_list": []},
            4122202: {"theme": 41, "template": 222, "sub_activity_id_list": []},
            4122301: {"theme": 41, "template": 223, "sub_activity_id_list": []},
            4100402: {"theme": 41, "template": 4, "sub_activity_id_list": []},
            4114601: {"theme": 41, "template": 146, "sub_activity_id_list": []},
        }
        theme44_native_ids = {
            aid for aid, cfg in theme44_config.items() if int(cfg["theme"]) == 44
        }
        # 311_CORE_VERIFICATION_GUARD:
        # 4443301..4 共用 template=433，同时开启会使客户端缓存被最后一轮覆盖。
        # 保留全部轮次的远期 stop_time，但只将版本配置指定的首轮设为 state=1。
        core_verification_active_ids = set(
            int(aid) for aid in version_cfg.get("core_verification_active_activity_ids", [])
        )
        core_verification_round_ids = {
            aid for aid, cfg in theme44_config.items()
            if int(cfg["template"]) in (433, 434, 435)
        }

        act_rows = db.query("SELECT activity_id, start_time, stop_time, state, theme, template, sub_activity_id_list FROM activity WHERE uid=?", (uid,))
        act_list = []
        seen_acts = set()
        for r in (act_rows or []):
            aid = int(r["activity_id"])
            seen_acts.add(aid)
            # 229 ActivityCfg 中根本不存在这 26 个 311 新增 ID。
            # 即使 state=0 也可能诱发旧客户端索引 nil，因此 229 模式必须完全不下发。
            if cur_ver == "229" and aid in theme44_native_ids:
                continue
            is_sub = aid in needed_acts
            is_pool = aid in pool_act_map

            st = int(r["start_time"] or 1577836800)
            # 无论当前模式是否启用该活动，协议时间都保持远期有效。
            # 版本隔离只由 state/theme/template/topology 表达，禁止再用 stop_time=0 关闭。
            et = far_future_ts
            state = int(r["state"]) if r.get("state") is not None else 1
            theme = int(r.get("theme") or 0)
            template = int(r.get("template") or 0)
            try:
                sub_list = _j11.loads(r["sub_activity_id_list"]) if r["sub_activity_id_list"] else []
            except Exception:
                sub_list = []

            if cur_ver == "311":
                # 311 客户端版本自适应
                if aid in all_pool_aids:
                    # 卡池活动（包括 Theme 44 的 4400301/4400303 与历史/229卡池）权威受控于在架状态
                    state = 1 if is_pool else 0
                    et = far_future_ts
                    if aid in theme44_config:
                        cfg = theme44_config[aid]
                        theme = cfg["theme"]
                        template = cfg["template"]
                        sub_list = list(cfg["sub_activity_id_list"])
                elif aid in theme44_config:
                    cfg = theme44_config[aid]
                    state = (
                        1
                        if aid not in core_verification_round_ids
                        or aid in core_verification_active_ids
                        else 0
                    )
                    et = far_future_ts
                    theme = cfg["theme"]
                    template = cfg["template"]
                    sub_list = list(cfg["sub_activity_id_list"])
                elif aid == 111:
                    state = 1
                    et = far_future_ts
                    sub_list = [4443301, 4443302, 4443303, 4443304]
                elif aid == 4310001 or str(aid).startswith("43") or theme == 43:
                    # 在 311 下关闭 Theme 43 专属大厅与剧情关卡活动，确保大厅入口完全切换至 Theme 44
                    state = 0
                else:
                    if is_sub:
                        state = 1
                        et = far_future_ts
            else:
                # 229 客户端版本自适应
                if aid in all_pool_aids:
                    # 卡池活动严格受控于在架状态，未在架卡池置 0 消除白屏与噪点
                    state = 1 if is_pool else 0
                    et = far_future_ts
                elif aid == 111:
                    state = 1
                    et = far_future_ts
                    sub_list = [4243301, 4339301, 4343301]
                elif aid == 4310001 or str(aid).startswith("43") or theme == 43 or is_sub:
                    state = 1
                    et = far_future_ts

            act_list.append({
                "activity_id": aid,
                "start_time": st,
                "stop_time": et,
                "state": state,
                "theme": theme,
                "template": template,
                "sub_activity_id_list": sub_list
            })

        # 311 版本核心活动保底补齐（防止数据库缺失记录导致客户端判断崩溃）
        if cur_ver == "311":
            for caid, cfg in theme44_config.items():
                if caid not in seen_acts:
                    seen_acts.add(caid)
                    # 卡池类活动依然严格遵从在架状态，非卡池类核心活动默认开启
                    if caid in all_pool_aids:
                        c_state = 1 if (caid in pool_act_map) else 0
                    else:
                        c_state = (
                            1
                            if caid not in core_verification_round_ids
                            or caid in core_verification_active_ids
                            else 0
                        )
                    act_list.append({
                        "activity_id": caid,
                        "start_time": 1577836800,
                        "stop_time": far_future_ts,
                        "state": c_state,
                        "theme": cfg["theme"],
                        "template": cfg["template"],
                        "sub_activity_id_list": list(cfg["sub_activity_id_list"])
                    })

        for aid in needed_acts:
            if cur_ver == "229" and aid in theme44_native_ids:
                continue
            if cur_ver == "311" and (aid == 4310001 or str(aid).startswith("43")):
                continue
            if aid not in seen_acts:
                seen_acts.add(aid)
                act_list.append({
                    "activity_id": aid,
                    "start_time": 1577836800,
                    "stop_time": far_future_ts,
                    "state": 1,
                    "theme": 0,
                    "template": 0,
                    "sub_activity_id_list": []
                })

        for aid, pent in pool_act_map.items():
            if cur_ver == "229" and aid in theme44_native_ids:
                continue
            if cur_ver == "311" and aid == 4310001:
                continue
            if aid not in seen_acts:
                seen_acts.add(aid)
                act_list.append({
                    "activity_id": aid,
                    "start_time": pent.get("start_time", 0),
                    "stop_time": far_future_ts,
                    "state": pent.get("state", 1),
                    "theme": pent.get("theme", 0),
                    "template": pent.get("template", 3),
                    "sub_activity_id_list": []
                })

        return encode("sc_11001", {"activity_list": act_list})
    if cmd == 24009:
        # 章节与剧情进度（StageService 统管驱动 → sc_24009 user_chapter_list）
        # 覆盖全量主线与支线剧情关卡，自愈上下篇与星级对账
        from stage_service import StageService
        svc = StageService.get_instance(db=db)
        lst = svc.get_plot_downframe_list(uid)
        return encode("sc_24009", {"user_chapter_list": lst})
    if cmd == 25009:
        # 日常与刻印资源关卡进度（StageService 统管驱动 → sc_25009 daily_battle_list）
        # 严格限制在日常本/刻印本区间（201xxxx, 202xxxx, 203xxxx）
        from stage_service import StageService
        svc = StageService.get_instance(db=db)
        lst = svc.get_resource_downframe_list(uid)
        return encode("sc_25009", {"daily_battle_list": lst})
    if cmd == 13057:
        # 刻印自动分解开启列表（ops_common 表驱动 → sc_13057 type_list）
        # type: 1=NORMAL(常规刻印副本), 2=PT(活动)
        enabled = []
        try:
            rows = db.query("SELECT item_id, value_json FROM ops_common WHERE uid=? AND kind='auto_decompose'", (uid,))
            for r in (rows or []):
                import json as _jd
                v = _jd.loads(r.get("value_json") or "{}")
                if int(v.get("sign") or 0) == 1:
                    enabled.append(int(r["item_id"]))
        except Exception:
            pass
    if cmd == 35011:
        # 介质攫取主进度与增益词缀（weekly_challenge_service 驱动 → sc_35011）
        import weekly_challenge_service as _wcs
        sc_obj = _wcs.get_equip_seizure_data(uid, db)
        return encode("sc_35011", sc_obj)
    if cmd == 24017:
        # 章节星数阶段宝箱领取状态（chapter_star_reward 表驱动 → sc_24017 gain_list）
        import json as _j17
        import os as _o17
        _cfg_path = _o17.path.join(_o17.path.dirname(_o17.path.abspath(__file__)), "chapter_star_rewards.json")
        ch_cfg = {}
        try:
            if _o17.path.exists(_cfg_path):
                ch_cfg = _j17.load(open(_cfg_path, "r", encoding="utf-8"))
        except Exception:
            ch_cfg = {}

        claimed_rows = db.query("SELECT chapter_id, reward_order FROM chapter_star_reward WHERE uid=?", (uid,))
        claimed_set = set((int(r["chapter_id"]), int(r["reward_order"])) for r in (claimed_rows or []))

        gain_list = []
        for ch_id_str, cfg in ch_cfg.items():
            ch_id = int(ch_id_str)
            r_list = []
            for order in [1, 2, 3]:
                is_rec = 1 if (ch_id, order) in claimed_set else 0
                r_list.append({"reward_order": order, "is_received": is_rec})
            gain_list.append({"id": ch_id, "reward_list": r_list})
        return encode("sc_24017", {"gain_list": gain_list})
    if cmd == 13009:
        # [Fix by Gemini 3.7-flash] 刻印装备库驱动（equip 表独立列 → sc_13009 equip_list）
        # 协议：equip{f1 equip_id, f2 prefab_id, f3 exp, f4 hero_id, f5 is_lock,
        #       f6 now_break_level, f7 enchant_slot_list{id,effect_list{id,level},preview_list{effect_list}},
        #       f8 race, f9 race_preview}
        # 保证每个装备至少下发 slot 1 和 slot 2（即使无词条），防止客户端进入洗练界面时访问 nil 崩溃
        import json as _je
        target_eid = kw.get("equip_id")
        if target_eid:
            rows = db.query("SELECT * FROM equip WHERE uid=? AND id=?", (uid, int(target_eid)))
        else:
            rows = db.query("SELECT * FROM equip WHERE uid=? ORDER BY id", (uid,))
        lst = []
        for r in rows:
            slots = []
            try:
                _es = _je.loads(r.get("enchant_slots") or "[]")
            except Exception:
                _es = []
            slot_map = {}
            for s in (_es or []):
                sid = int(s.get("id") or 1)
                eff = [{"id": e.get("id"), "level": e.get("level")} for e in (s.get("effect_list") or []) if isinstance(e, dict) and e.get("id")]
                raw_prev = s.get("preview_list") or []
                prev = []
                for p in raw_prev:
                    if isinstance(p, list):
                        p_eff = [{"id": pe.get("id"), "level": pe.get("level")} for pe in p if isinstance(pe, dict) and pe.get("id")]
                        if p_eff:
                            prev.append({"effect_list": p_eff})
                    elif isinstance(p, dict) and p.get("id"):
                        prev.append({"effect_list": [{"id": p.get("id"), "level": p.get("level", 1)}]})
                slot_map[sid] = {"id": sid, "effect_list": eff, "preview_list": prev}
            # 基础槽位 1 与 2 保底（客户端 EquipStruct.ParseServerData 依赖下发的 slot.id 初始化 enchant 与 enchant_preview 容器）
            for base_sid in (1, 2):
                if base_sid not in slot_map:
                    slot_map[base_sid] = {"id": base_sid, "effect_list": [], "preview_list": []}
            slots = sorted(slot_map.values(), key=lambda x: x["id"])
            lst.append({
                "equip_id": r.get("id"),
                "prefab_id": r.get("prefab_id"),
                "exp": r.get("exp") or 0,
                "hero_id": r.get("hero_id") or 0,
                "is_lock": r.get("is_lock") or 0,
                "now_break_level": r.get("now_break_level") or 0,
                "enchant_slot_list": slots,
                "race": r.get("race_hero") or r.get("race") or 0,
                "race_preview": r.get("race_preview") or 0,
            })
        if lst:
            return encode("sc_13009", {"equip_list": lst, "is_init": 0})
        return None
    if cmd == 46011:
        # 钥从实例库驱动（servant 表 → sc_46011 servant_list）
        # 协议：servant{uid 实例uid, id 配置id, stage 突破, is_locked}
        rows = db.query("SELECT * FROM servant WHERE uid=? ORDER BY id", (uid,))
        lst = []
        for r in rows:
            lst.append({
                "uid": r.get("id"),
                "id": r.get("prefab_id"),
                "stage": r.get("stage") or 1,
                "is_locked": r.get("is_locked") or 0,
            })
        if lst:
            return encode("sc_46011", {"servant_list": lst})
        return None
    if cmd == 50001:
        # 芯片解锁库驱动（统一委托给 ChipService -> sc_50001）
        from chip_service import ChipService
        chip_dict = ChipService.get_instance(db).get_sc_50001_dict(uid)
        return encode("sc_50001", chip_dict)
    if cmd == 43001:
        # [Fix by Gemini 3.7-flash] 刻印副本状态库驱动（battle_equip 表 → sc_43001）
        # 包含：开放关卡组 stage_base_id=30001, 选中的 UP 套装 equip_suit_id, 刷新时间 next_refresh_time, 难1~5保底列表 insure_list
        rows = db.query("SELECT stage_id, insure_times, suit_id FROM battle_equip WHERE uid=?", (uid,))
        suit_id = 1
        insure_map = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        if rows:
            for r in rows:
                if r.get("suit_id"):
                    suit_id = int(r["suit_id"])
                d = int(r["stage_id"]) % 10
                if 1 <= d <= 5:
                    insure_map[d] = max(insure_map[d], int(r.get("insure_times") or 0))
        return encode("sc_43001", {
            "stage_base_id": 30001,
            "equip_suit_id": suit_id if suit_id > 0 else 1,
            "next_refresh_time": int(time.time()) + 86400,
            "insure_list": [{"difficulty": d, "insure_times": insure_map[d]} for d in range(1, 6)]
        })
    if cmd == 10600:
        # [Fix by Gemini 3.7-flash] open_system 全量下发（封锁公会体系 2401/2402/2404/2405/2406，保留通行证 1901/1902/1903/1904/1905/519，其余系统正常开放）
        _sys_ids = [20, 101, 201, 203, 204, 205, 206, 207, 208, 209, 210, 211, 221, 231, 235, 301, 302, 303, 304, 305, 306, 307, 308, 309, 311, 312, 313, 314, 316, 330, 331, 332, 333, 334, 335, 336, 337, 341, 342, 343, 344, 345, 346, 401, 501, 510, 511, 512, 513, 519, 601, 701, 702, 703, 801, 802, 803, 804, 901, 902, 904, 905, 1001, 1101, 1201, 1301, 1401, 1501, 1601, 1701, 1702, 1802, 1803, 1901, 1902, 1903, 1904, 1905, 2001, 2101, 2201, 2301, 2302, 2501, 2510, 2511, 2512, 2513, 2514, 2515, 2601, 2701, 2801, 2901, 3001, 3101, 3110, 3120, 9901, 9902, 9903, 9904, 9905, 9907, 9908, 9909, 9910, 9911, 9912, 10101, 10102, 10301, 10401, 10501, 10601, 10701, 10702, 10703, 10704, 11102, 20001, 20002]
        return encode("sc_10600", {"open_system": _sys_ids})
    if cmd == 67001:
        # [AdminCatExplore] 管理员猫咪探索主数据动态下发（sc_67001）
        from admin_cat_explore_service import AdminCatExploreService
        payload_dict = AdminCatExploreService.get_instance().generate_sc_67001_dict(db, uid)
        return encode("sc_67001", payload_dict)
    if cmd == 31001:
        # [Fix by Gemini 3.7-flash] 公会数据帧（公会系统关闭状态下下发空公会数据）
        return encode("sc_31001", {"last_share_timestamp": 0})
    if cmd == 56001:
        # 红点状态库驱动 → sc_56001：nothing=0 + red_dot(待展示白名单) + client_finished_red_dot(已处理 key)
        finished = db.get_red_dot_finished(uid)
        pending = db.get_red_dot_pending(uid)
        return encode("sc_56001",
                      {"nothing": 0, "red_dot": pending, "client_finished_red_dot": finished})
    if cmd == 16015:
        # 抽卡卡池初始状态与自选UP/保底（登录洪流推：sc_16015，db驱动，彻底解决重登UP显示为素材旧值问题）
        from operations import get_pool_group, get_draw_state
        # 活跃展示卡池清单（按当前开放卡池与素材一致顺序）：
        # 5020601 (90精准), 5020303 (70精准), 5020302 (70精准), 5020301 (70精准), 4080101 (70标准自选), 10002 (70钥从自选)
        active_pool_ids = [5020601, 5020303, 5020302, 5020301, 4080101, 10002]
        ctx = type("Ctx", (), {"db": db})()
        draw_info_list = []
        for pid in active_pool_ids:
            prow = db.query("SELECT detail_json FROM draw_pool WHERE pool_id=?", (pid,))
            dj = prow[0]["detail_json"] if prow else ""
            pgroup = get_pool_group(pid, dj)
            st = get_draw_state(ctx, uid, pgroup)
            since_ssr = int(st.get("since_ssr") or 0)
            up_id = int(st.get("up_id") or 0)
            total_draws = int(st.get("total_draws") or 0)
            if pid == 10002 and up_id == 0:
                up_id = 2510000
            elif pid == 4080101 and up_id == 0:
                up_id = 1066
            elif pid in (5020601, 5020301, 5020302, 5020303):
                up_id = 0
            draw_info_list.append({
                "id": pid,
                "ssr_draw_times": since_ssr,
                "up": up_id,
                "up_times": total_draws,
                "is_new": 0
            })
        return encode("sc_16015", {
            "draw_info_list": draw_info_list,
            "first_ssr_draw_flag": False,
            "today_draw_times": 0,
            "newbie_choose_draw_flag": True
        })
    if cmd == 30001:
        # 邮件未读摘要库驱动 → sc_30001{unread_number, total_number}（MAIL_UNREAD 红点数据源）
        from mail_service import MailService
        summary = MailService.get_instance(db).get_login_summary(uid)
        return encode("sc_30001", summary)
    if cmd == 63005:
        # 预备编队与出战队伍库驱动（reserve_team / reserve_team_hero 表 → sc_63005）
        import reserve_codec as _rc
        payload = _rc.build_sc_63005(db, uid)
        return payload if payload else None
    if cmd == 12045:
        # 每日定时免费体力补给状态库驱动 → sc_12045 daily_fatigue_dessert_list (11:00 / 18:00)
        import daily_fatigue_codec as _dfc
        return _dfc.build_sc_12045(db, uid, codec_encode=encode)
    if cmd == 30003:
        # 普通邮件列表库驱动 → sc_30003 mail_list
        from mail_service import MailService
        mails = MailService.get_instance(db).get_inbox_list(uid)
        return encode("sc_30003", {"mail_list": mails, "total_num": len(mails)})
    if cmd == 30017:
        # 修正者专属信件列表库驱动 → sc_30017 letter_list
        from mail_service import MailService
        letters = MailService.get_instance(db).get_special_letters(uid)
        return encode("sc_30017", {"letter_list": letters})
    if cmd == 30021:
        # 收藏邮件列表库驱动 → sc_30021 collect_mail_list
        from mail_service import MailService
        collect_list = MailService.get_instance(db).get_collect_list(uid)
        return encode("sc_30021", {"collect_mail_list": collect_list, "collect_total_num": len(collect_list)})
    if cmd == 28001:

        # 任务列表库驱动 → sc_28001 assignment_list（task 表 831 行 1:1 映射）
        # phase 动态化（tasktools.lua: phase 给小会卡剧情/新手引导）：
        #   newbie_phase     = max(AssignmentCfg[玩家PLOT任务].phase)  —— 客户端 TaskUpdatePlotPhase 同式
        #   assignment_phase = max(AssignmentCfg[玩家ALPHA任务].phase)
        # phase 表由 x64 assignmentcfg.lua 提取（assignment_phase.json，4285 条）
        rows = db.query("SELECT task_id, progress, complete_flag, expired_ts FROM task "
                        "WHERE uid=? ORDER BY task_id", (uid,))
        if rows:
            lst = [{"id": r["task_id"], "progress": r["progress"] or 0,
                    "complete_flag": r["complete_flag"] or 0,
                    "expired_timestamp": r["expired_ts"] or 0} for r in rows]
            _row_ids = {r["task_id"] for r in rows}
            ph = _assignment_phases()
            # PLOT(type=3) → newbie_phase；ALPHA(type=4) → assignment_phase（TaskConst）
            nb = max([v[1] for k, v in ph.items()
                      if v[0] == 3 and k in _row_ids] or [0])
            ap = max([v[1] for k, v in ph.items()
                      if v[0] == 4 and k in _row_ids] or [1])
            return encode("sc_28001", {"assignment_list": lst,
                                       "send_type": 0,
                                       "newbie_phase": max(1, nb),
                                       "assignment_phase": max(1, ap)})
        return None
    if cmd == 12001:
        # 剧情观看记录（StageService 统管驱动 → sc_12001 story_list）
        from stage_service import StageService
        svc = StageService.get_instance(db=db)
        s_list = svc.get_story_unlock_list(uid)
        return encode("sc_12001", {"story_list": s_list})
    if cmd == 12011:
        # 新手引导跳过列表库驱动 → sc_12011 mod_guide_list (guide 表)
        rows = db.query("SELECT guide_id FROM guide WHERE uid=? ORDER BY guide_id", (uid,))
        g_list = [int(r["guide_id"]) for r in rows] if rows else []
        # 确保 501-526 引导全覆盖（防止引导卡死）
        g_set = set(g_list)
        for gid in range(501, 527):
            if gid not in g_set:
                g_list.append(gid)
        return encode("sc_12011", {"mod_guide_list": g_list})
    if cmd == 53001:
        # 全量成就列表库驱动 → sc_53001 (achievement 表，委托至 achievement_service)
        try:
            import achievement_service
            return achievement_service.AchievementService.get_instance(db).get_achievement_login_payload(uid)
        except Exception:
            rows = db.query("SELECT achievement_id, progress, complete_flag, achieve_time FROM achievement WHERE uid=? ORDER BY achievement_id", (uid,))
            ach_list = []
            for r in (rows or []):
                ach_list.append({
                    "id": int(r["achievement_id"]),
                    "progress": int(r.get("progress") or 0),
                    "achieve_time": int(r.get("achieve_time") or 0),
                    "complete_flag": int(r.get("complete_flag") or 0)
                })
            return encode("sc_53001", {
                "achievement_list": ach_list,
                "story_line": [6, 5, 4, 3, 2, 1]
            })
    if cmd == 41001:
        # 历战空间/爬塔进度库驱动 → sc_41001 info_list (tower 表)
        rows = db.query("SELECT area, stage FROM tower WHERE uid=? ORDER BY area", (uid,))
        info_list = []
        for r in (rows or []):
            a = int(r["area"])
            s = int(r["stage"])
            if a < 1000:
                a = 4010100 + a
                s = 4010000 + s
            info_list.append({"area": a, "stage": s})
        return encode("sc_41001", {"info_list": info_list})
    if cmd == 52021:
        # 加载插画图集库驱动 → sc_52021 id_list (loading_set 表)
        import json as _j21
        row = db.query("SELECT id_list FROM loading_set WHERE uid=?", (uid,))
        ids = []
        if row and row[0]["id_list"]:
            try:
                ids = _j21.loads(row[0]["id_list"]) if isinstance(row[0]["id_list"], str) else row[0]["id_list"]
            except Exception:
                ids = []
        if not ids:
            ids = [2109401, 2109403, 2109402]
        return encode("sc_52021", {"id_list": [int(x) for x in ids]})
    if cmd == 52025:
        # 英雄种族收集进度库驱动 → sc_52025 hero_race_collect (hero_race_collect 表)
        import json as _j25
        rows = db.query("SELECT race_type, received_cnt_list FROM hero_race_collect WHERE uid=? ORDER BY race_type", (uid,))
        race_list = []
        for r in (rows or []):
            try:
                cnts = _j25.loads(r["received_cnt_list"]) if isinstance(r["received_cnt_list"], str) else r["received_cnt_list"]
            except Exception:
                cnts = []
            race_list.append({
                "race_type": int(r["race_type"]),
                "cnt_list": [int(x) for x in (cnts or [])]
            })
        return encode("sc_52025", {"hero_race_collect": race_list})
    if cmd == 52001:
        # 图鉴系统全量库驱动 → sc_52001（illustrated 表全分类驱动，登录自动自愈对齐）
        import json as _j51
        from illustrated_listener import IllustratedSyncService

        # 1. 登录时执行全量图鉴自愈校准
        IllustratedSyncService.sync_all_from_db(db, uid)

        # 2. 读取全量图鉴数据（仅将已解锁条目加入下行负载帧，未解锁条目保留库中元数据）
        plots = db.query("SELECT item_id, is_view FROM illustrated WHERE uid=? AND kind='plot' AND (complete_flag IS NULL OR complete_flag != 0) ORDER BY item_id", (uid,))
        inbetweens = db.query("SELECT item_id, is_view, is_receive FROM illustrated WHERE uid=? AND kind='inbetweening' AND (complete_flag IS NULL OR complete_flag != 0) ORDER BY item_id", (uid,))
        servants = db.query("SELECT item_id, is_view FROM illustrated WHERE uid=? AND kind='servant' ORDER BY item_id", (uid,))
        enemies = db.query("SELECT item_id, times, is_view FROM illustrated WHERE uid=? AND kind='enemy' ORDER BY item_id", (uid,))
        equips = db.query("SELECT item_id, is_view, pos_list_json FROM illustrated WHERE uid=? AND kind='equip' ORDER BY item_id", (uid,))
        affixes = db.query("SELECT item_id, is_view FROM illustrated WHERE uid=? AND kind='affix' ORDER BY item_id", (uid,))

        def _pos_list(js):
            try:
                v = _j51.loads(js or "[]")
                return [int(x) for x in v] if isinstance(v, list) else []
            except Exception:
                return []

        return encode("sc_52001", {
            "intelligence_reward": [],
            "enemy_info": [{"id": r["item_id"], "times": r["times"] or 0, "is_view": r["is_view"] or 0} for r in enemies] if enemies else [],
            "servant_info": [{"id": r["item_id"], "is_view": r["is_view"] or 0} for r in servants] if servants else [],
            "equip_info": [{"suit": r["item_id"], "pos_list": _pos_list(r.get("pos_list_json")), "is_view": r["is_view"] or 0} for r in equips] if equips else [],
            "plot_info": [{"id": r["item_id"], "is_view": r["is_view"] or 0} for r in plots] if plots else [],
            "inbetweening_info": [{"id": r["item_id"], "is_receive": r.get("is_receive") or 0, "is_view": r.get("is_view") or 0} for r in inbetweens] if inbetweens else [],
            "affix_info": [{"id": r["item_id"], "is_view": r["is_view"] or 0} for r in affixes] if affixes else []
        })

    if cmd in (91001, 25465):
        # [Fix by Gemini 3.7-flash] MomoTalk 随身通讯全量数据驱动 → sc_91001
        momo_data = db.get_momotalk_data(uid)
        return encode("sc_91001", momo_data)
    if cmd == 58001:
        # [2026-09-06 模块迁移] 游园街登录简要数据驱动 → 委托 backhome_service（原直连 DAO）
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_login_concise_payload(uid, db)
    if cmd == 58003:
        # [2026-09-06 模块迁移] 游园街全量数据驱动 → 委托 backhome_service
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_detail_payload(uid, db)
    if cmd == 58027:
        # [2026-09-06 模块迁移] 后宅英雄疲劳度推送 → 委托 backhome_service
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_fatigue_push_payload(uid, db)
    if cmd == 58169:
        # 游园街练舞房总览数据驱动 → 委托 backhome_service
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_idol_trainee_overview_payload(uid, db)
    if cmd == 58189:
        # 游园街舞蹈培训生 PVE 关卡进度全量数据驱动 → 委托 backhome_service
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_idol_trainee_pve_payload(uid, db)
    if cmd == 58195:
        # 游园街练舞房 DIY 舞蹈序列数据驱动 → 委托 backhome_service
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_dance_diy_payload(uid, db)
    if cmd == 58191:
        # 游园街练舞房偶像排行积分与已领档位数据驱动 → 委托 backhome_service
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_idol_trainee_rank_payload(uid, db)
    if cmd == 20007:
        # 全量动态商店商品配置数据驱动 → sc_20007 (由 ShopService 统一生成并清洗非法字段消除警告)
        from shop_service import ShopService
        import res_version_manager
        shop_cfg_data = ShopService.get_instance(db=db).get_shop_cfg_payload()
        if res_version_manager.get_version_config(kw.get("res_version"))["version"] == "311":
            dep_shops = set(res_version_manager.VERSION_CONFIGS["311"].get("deprecated_shops", []))
            if "shop_item_cfg_list" in shop_cfg_data:
                shop_cfg_data["shop_item_cfg_list"] = [
                    s for s in shop_cfg_data["shop_item_cfg_list"]
                    if int(s.get("shop_id", 0)) not in dep_shops
                ]
        return encode("sc_20007", shop_cfg_data)
    if cmd == 20009:
        # 全量 116+ 商店购买状态与限购计数数据驱动 → sc_20009 (由 ShopService 统筹每日货架与刷新状态)
        from shop_service import ShopService
        import res_version_manager
        shop_data = ShopService.get_instance(db=db).get_shop_data_payload(uid)
        if res_version_manager.get_version_config(kw.get("res_version"))["version"] == "311":
            dep_shops = set(res_version_manager.VERSION_CONFIGS["311"].get("deprecated_shops", []))
            if "shop_item_list" in shop_data:
                shop_data["shop_item_list"] = [
                    s for s in shop_data["shop_item_list"]
                    if int(s.get("shop_id", 0)) not in dep_shops
                ]
        return encode("sc_20009", shop_data)
    if cmd == 12029:
        # [2026-09-06 外围模块迁移] 大厅 BGM 登录推送动态化（原静态 blob id=4，
        # 玩家改 BGM 重登会跳回默认）→ 委托 peripheral_service 读 game_user.bgm_id
        from peripheral_service import PeripheralService
        return PeripheralService.get_instance(db=db).get_bgm_push_payload(uid, db)
    if cmd == 12033:
        # [2026-09-08 个性化模块修复] 生日登录推送动态化 → 委托 peripheral_service 读 game_user.birth_month/birth_day
        from peripheral_service import PeripheralService
        return PeripheralService.get_instance(db=db).get_birthday_push_payload(uid, db)
    if cmd == 32009:
        # [2026-09-06 外围模块迁移] 玩家个性化装扮与场景数据驱动 → 委托 peripheral_service
        # （blob + user_scene 注入 + DB 个性化真值：sign/poster_girl/icon/icon_frame/chat_bubble）
        from peripheral_service import PeripheralService
        return PeripheralService.get_instance(db=db).get_profile_card_payload(uid, db)
    if cmd == 91001:
        # [Fix by Gemini 3.7-flash] MomoTalk 简讯与对话进度/红点全量数据驱动 → sc_91001
        import json
        sessions = db.query(
            "SELECT hero_id, session_id, send_time, is_view, current_content_id, save_list "
            "FROM momotalk_session WHERE uid=? ORDER BY send_time DESC", (uid,)
        )
        hero_map = {}
        for s in (sessions or []):
            hid = s["hero_id"]
            if hid not in hero_map:
                hero_map[hid] = []
            try:
                s_list = json.loads(s["save_list"]) if s["save_list"] else []
            except Exception:
                s_list = []
            hero_map[hid].append({
                "id": s["session_id"],
                "send_time": s["send_time"] or 0,
                "is_view": s["is_view"] or 0,
                "current_content_id": s["current_content_id"] or 0,
                "save_list": [{"content_id": it.get("content_id", 0), "state": it.get("state", 0)} for it in s_list]
            })

        hero_session_list = []
        for hid, slist in hero_map.items():
            hero_session_list.append({
                "sender_id": hid,
                "session_list": slist
            })

        gu = db.query("SELECT cur_momotalk_frame FROM game_user WHERE uid=?", (uid,))
        cur_frame = gu[0]["cur_momotalk_frame"] if gu and gu[0]["cur_momotalk_frame"] else 1

        obj = {
            "icon": cur_frame,
            "background": 0,
            "type_hero": [{
                "type": 1,
                "hero_session": hero_session_list
            }],
            "icon_list": [1, 2, 3]
        }
        return encode("sc_91001", obj)
    if cmd == 19029:
        # [AI Bot] 好友初始化数据驱动 → sc_19029 (brief_friend_list 注入所有活跃 AI 角色 + 真实好友)
        ai_chars = db.get_active_ai_characters() if hasattr(db, "get_active_ai_characters") else []
        brief_list = [{"user_id": int(c["char_id"]), "timestamp": int(c.get("created_at") or 0)} for c in ai_chars]
        real_friends = db.query("SELECT friend_uid, timestamp FROM friends WHERE uid = ?", (uid,))
        for rf in real_friends:
            brief_list.append({"user_id": int(rf["friend_uid"]), "timestamp": int(rf.get("timestamp") or 0)})
        return encode("sc_19029", {
            "request_list": [],
            "brief_friend_list": brief_list,
            "black_list": []
        })
    if cmd == 19001:
        # [AI Bot] 全量好友数据驱动 → sc_19001 (friend_list 完整名片/头像/气泡/签名/在线状态)
        now_ts = int(time.time())
        ai_chars = db.get_active_ai_characters() if hasattr(db, "get_active_ai_characters") else []
        friend_list = []
        for c in ai_chars:
            friend_list.append({
                "user_id": int(c["char_id"]),
                "base_info": {
                    "nick": str(c["char_name"]),
                    "icon": int(c.get("avatar_icon") or 0),
                    "icon_frame": int(c.get("icon_frame") or 0)
                },
                "online_state": 0,
                "sign": str(c.get("sign") or ""),
                "timestamp": now_ts,
                "ip_location": str(c.get("ip_location") or "未知"),
                "level": int(c.get("level") or 80),
                "chat_bubble": int(c.get("chat_bubble") or 9001),
                "info_background": int(c.get("info_background") or 0)
            })
        real_friends = db.query("SELECT friend_uid, timestamp FROM friends WHERE uid = ?", (uid,))
        for rf in real_friends:
            f_uid = int(rf["friend_uid"])
            u = db.query("SELECT nick, level, sign, portrait, icon_frame FROM users WHERE uid = ?", (f_uid,))
            gu = db.query("SELECT cur_bubble, cur_card_bg FROM game_user WHERE uid = ?", (f_uid,))
            nick = u[0]["nick"] if (u and u[0]["nick"]) else f"玩家{f_uid}"
            icon = u[0]["portrait"] if (u and u[0]["portrait"]) else 1084
            frame = u[0]["icon_frame"] if (u and u[0]["icon_frame"]) else 2001
            sign = u[0]["sign"] if (u and u[0]["sign"]) else ""
            level = u[0]["level"] if (u and u[0]["level"]) else 80
            bubble = gu[0]["cur_bubble"] if (gu and gu[0]["cur_bubble"]) else 9001
            bg = gu[0]["cur_card_bg"] if (gu and gu[0]["cur_card_bg"]) else 0
            friend_list.append({
                "user_id": f_uid,
                "base_info": {
                    "nick": str(nick),
                    "icon": int(icon),
                    "icon_frame": int(frame)
                },
                "online_state": 1,
                "sign": str(sign),
                "timestamp": int(rf.get("timestamp") or 0),
                "ip_location": "同服",
                "level": int(level),
                "chat_bubble": int(bubble),
                "info_background": int(bg)
            })
        return encode("sc_19001", {
            "friend_list": friend_list,
            "black_list": [],
            "request_list": []
        })

    if cmd == 12023:
        # sc_12023 挑战补偿未领取提示 (unclaimed)
        unclaimed_list = []
        if db:
            try:
                rows = db.query("SELECT unclaimed_id, stage FROM unclaimed WHERE uid=?", (uid,))
                for r in rows:
                    unclaimed_list.append({
                        "id": int(r["unclaimed_id"]),
                        "stage": int(r.get("stage") or 0)
                    })
            except Exception:
                pass
        return encode("sc_12023", {"reward": unclaimed_list})

    # ---------- 四大高难周常玩法动态下发数据驱动 ----------
    if cmd in (45201, 45001, 45101, 44007, 44009, 44019, 44021, 44023, 18001, 75009, 75017, 89013, 89025, 89431, 89801, 89021):
        import weekly_challenge_service as _wcs
        if cmd == 45201:
            p, _, _ = _wcs.get_boss_challenge_data(uid, db)
            return encode("sc_45201", p)
        if cmd == 45001:
            _, p, _ = _wcs.get_boss_challenge_data(uid, db)
            return encode("sc_45001", p)
        if cmd == 45101:
            _, _, p = _wcs.get_boss_challenge_data(uid, db)
            return encode("sc_45101", p)
        if cmd == 44007:
            p, _, _, _, _ = _wcs.get_mythic_data(uid, db)
            return encode("sc_44007", p)
        if cmd == 44009:
            _, p, _, _, _ = _wcs.get_mythic_data(uid, db)
            return encode("sc_44009", p)
        if cmd == 44019:
            _, _, p, _, _ = _wcs.get_mythic_data(uid, db)
            return encode("sc_44019", p)
        if cmd == 44021:
            _, _, _, p, _ = _wcs.get_mythic_data(uid, db)
            return encode("sc_44021", p)
        if cmd == 44023:
            _, _, _, _, p = _wcs.get_mythic_data(uid, db)
            return encode("sc_44023", p)
        if cmd == 18001:
            p = _wcs.get_polyhedron_data(uid, db, res_version=kw.get("res_version"))
            return encode("sc_18001", p)
        if cmd == 75009:
            p = _wcs.get_core_verification_data(uid, db)
            return encode("sc_75009", p)
        if cmd == 75017:
            import res_version_manager
            version_cfg = res_version_manager.get_version_config(kw.get("res_version"))
            open_ed = int(version_cfg["heart_demon_activity_id"])
            return encode("sc_75017", {
                "open_edition": open_ed,
                "challenge_stage": 1,
                "info_list": [],
                "max_score_list": []
            })
        if cmd == 89013:
            _, p, _ = _wcs.get_core_verification_challenge_data(uid, db, 3539501)
            return encode("sc_89013", p)
        if cmd == 89025:
            _, p, _ = _wcs.get_core_verification_challenge_data(uid, db, 3640501)
            return encode("sc_89025", p)
        if cmd == 89431:
            _, p, _ = _wcs.get_core_verification_challenge_data(uid, db, 3943101)
            return encode("sc_89431", p)
        if cmd == 89801:
            import res_version_manager
            version_cfg = res_version_manager.get_version_config(kw.get("res_version"))
            act_id = int(version_cfg["core_verification_activity_id"])
            _, p, _ = _wcs.get_core_verification_challenge_data(uid, db, act_id)
            return encode("sc_89801", p)
        if cmd == 89021:
            _, _, p = _wcs.get_core_verification_challenge_data(uid, db)
            return encode("sc_89021", p)

    if cmd == 49001:
        current_chapter, chapter_info_list = db.get_warchess_overview(uid)
        return encode("sc_49001", {
            "chess_map_list": [{
                "activity_id": 0,
                "chapter": current_chapter,
                "chapter_info": chapter_info_list
            }]
        })
    if cmd == 49023:
        open_list = db.get_warchess_open_list()
        return encode("sc_49023", {
            "chess_open_info_list": open_list
        })

    return None




if __name__ == "__main__":
    # 自检：模拟请求包 → 生成帧 → 校验帧头
    db = get_db(DEFAULT_DB)
    for cmd in (17009, 15009, 23009, 11010, 11081, 34024):
        pkt = {"index": 5, "server_idx": 700, "cmd": cmd, "len": 0, "payload": b""}
        frames = generate(cmd, pkt, db=db)
        if frames is None:
            print(f"cmd={cmd}: 无路由")
            continue
        for f in frames:
            size = struct.unpack(">H", f[0:2])[0]
            fcmd = struct.unpack(">H", f[5:7])[0]
            fidx = struct.unpack(">H", f[7:9])[0]
            fsrv = struct.unpack(">H", f[9:11])[0]
            print(f"cmd={cmd} -> 帧 cmd={fcmd} size={size}(总{len(f)}B) idx={fidx} srv={fsrv} payload={f[11:].hex()[:40]}")
    # 改数据验证：改库里金币 → 重新生成
    db.set_currency(DEFAULT_UID, 2, 999999)
    pkt = {"index": 6, "server_idx": 701, "cmd": 15009, "len": 0, "payload": b""}
    frames = generate(15009, pkt, db=db)
    from codec import decode
    print("\n改库后 sc_15009 解码:", decode(frames[0][11:], "sc_15009"))
