# -*- coding: utf-8 -*-
"""
middleware.py — 中间件：统一请求处理入口（V5 CORE 第一步）

契约：`临上线基准数据\中间件接口契约.md`（§2 数据结构 / §3 响应策略 / §8 迁移路径）
数据流：handle_request(req, ctx) → CORE_HANDLERS（业务处理器）→ generator → TEMPLATE → FAIL → DROP

与 V4.1 的对接：
- 本模块独立可测（handler 逻辑从 all_in_one_server_v4_1.py 的 if 链原样迁入，行为不变）
- V4.1 接入点：handle_tcp_replay 收到 cs 帧 → 构造 CSRequest → handle_request → CSResponse.to_bytes()
- 第四步（契约 §8）删除 V4.1 if 链后，handle_tcp_replay 只调 handle_request
"""
import os
import struct
import threading
import time
import json

from logger import log as _file_log  # noqa: E402
import schema_default as _sd  # noqa: E402  兜底响应的字段补齐器
import battle_payload  # 动态战斗初始化帧构建器


def fallback_payload(sc_cmd, values=None):
    """兜底 payload：按 schema 生成字段完整的响应（替代原先一律回 0800）。

    原先的 b"\\x08\\x00" 只对「f1 是单数 varint」的消息成立。全库 1404 个 sc_ 消息里
    有 201 个的 f1 是 message/string —— 0800 把 wire type 2 写成 0，是畸形帧；
    另有 47 个 f1 是 repeated，0800 等于往列表里塞一个多余的 0 元素
    （如 sc_10600 会变成「已开放系统包含 id=0」，sc_17023 会变成「道具列表里有个 0 号道具」）。
    详见 schema_default 模块头注释与客户端 Lua protobuf 运行时的取值语义。
    """
    try:
        p = _sd.build_for_sc(sc_cmd, values)
        return p if p is not None else b"\x08\x00"
    except Exception:
        return b"\x08\x00"

# ---------------- 帧构造（帧格式锁死，勿动） ----------------

def build_downstream(cmd, payload, index=0, server_idx=0, flag=0):
    """下行帧：u16 size(BE, size=9+payload) | [zlib_flag] 00 [high_flag] | cmd u16(BE) | idx u16 | srv u16 | payload。
    zlib_flag=1 (byte 2) → 压缩帧标志；high_flag=1 (byte 4) → Lua 回调/投递标志（>65535 协议必须）。"""
    zlib_flag = 1 if (payload and payload[:2] == b"\x78\x9c") else 0
    high_flag = 1 if (cmd > 65535 or flag == 1) else 0
    body = (bytes([zlib_flag & 0xFF, 0x00, high_flag & 0xFF])
            + struct.pack(">H", cmd & 0xFFFF)
            + struct.pack(">H", index & 0xFFFF)
            + struct.pack(">H", server_idx & 0xFFFF)
            + payload)
    return struct.pack(">H", len(body)) + body


def rd_varint(b, i):
    v, sh = 0, 0
    while True:
        x = b[i]
        i += 1
        v |= (x & 0x7F) << sh
        sh += 7
        if not (x & 0x80):
            return v, i


def _tag(field, wt):
    return _varint((field << 3) | wt)


def _varint(v):
    # 负数先掩码成 64 位无符号：Python 负数右移恒为 -1，否则此处死循环（见 hero_codec 同注）
    v = int(v)
    if v < 0:
        v &= 0xFFFFFFFFFFFFFFFF
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


# ---------------- 数据结构（契约 §2.2） ----------------

class CSRequest:
    """客户端请求：cmd + payload + 序号 + uid。body 为按需解码视图。"""
    __slots__ = ("cmd", "payload", "index", "server_idx", "uid", "body")

    def __init__(self, cmd, payload=b"", index=0, server_idx=0, uid=0, body=None):
        self.cmd = cmd
        self.payload = payload or b""
        self.index = index
        self.server_idx = server_idx
        self.uid = uid
        self.body = body


class DownFrame:
    """一帧下行响应。flag=1 = Lua 投递（91xxx）。srv 显式指定时优先（91xxx 连接级计数器）。"""
    __slots__ = ("cmd", "payload", "flag", "srv")

    def __init__(self, cmd, payload=b"", flag=0, srv=None):
        self.cmd = cmd
        self.payload = payload or b""
        self.flag = flag
        self.srv = srv


class CSResponse:
    """零或多帧响应。reply=False = 明确不回包。src = 命中层（handler/skeleton/template/drop/ack0，日志标注用）。"""
    __slots__ = ("frames", "reply", "src")

    def __init__(self, frames=None, reply=True, src=""):
        self.frames = list(frames or [])
        self.reply = reply
        self.src = src

    def to_bytes(self, index=0, server_idx=0):
        """组帧：idx 回显请求，srv 从 server_idx+1 起逐帧递增（DownFrame.srv 显式指定时优先）。"""
        out = b""
        next_srv = server_idx + 1
        for f in self.frames:
            srv = f.srv if f.srv is not None else next_srv
            out += build_downstream(f.cmd, f.payload, index, srv, flag=f.flag)
            next_srv = srv + 1
        return out

    @property
    def cmds(self):
        return [f.cmd for f in self.frames]


# ---------------- HandlerCtx（V4.1 状态容器） ----------------

class HandlerCtx:
    """handler 运行上下文：V4.1 连接级状态 + 配置 + 库。每个连接一个实例。"""

    def __init__(self, cfg=None, db=None, generator=None, codec_encode=None, codec_decode=None, log=None):
        self.cfg = cfg or {}
        self.db = db
        self.generator = generator          # lib/generator 模块（结算推送 15009/17009）
        self.codec_encode = codec_encode    # codec.encode
        self.codec_decode = codec_decode    # codec.decode
        # 默认落盘日志器；外部可注入自定义 log（测试/对接时）
        self.log = log or (lambda msg, level="INFO", **kw: _file_log(msg, level, **kw))
        # 战斗链路状态（连接级）
        self.battle_id = [None]             # sc_54035 生成 → cs_54032 校验/回显
        self.battle_stage = [0]             # cs_54034 解析 → sc_54033 的 dest
        self.battle_seq = [0]               # battle_id 自增序号（防同秒重复）
        self.hero_last_payload = [b""]      # 最近 cs_54030 payload（54033 回显出战英雄用）
        # 聊天连接级 srv 计数器（91xxx：并行请求响应 srv 重复会被客户端丢弃）
        self.chat_srv = [None]
        # 战斗初始化 payload（lib/sc_54003_payload.bin，惰性加载）
        self._p03_payload = None
        # 操作级道具变动原子追踪集（sc_17023 差量推送）与事件待发帧队列
        self.touched_items = set()
        self.removed_equips = []
        self.pending_frames = []

    def append_frame(self, frame):
        """添加异步待下发帧（如任务进度广播 sc_28007）"""
        if frame:
            self.pending_frames.append(frame)

    def pop_pending_frames(self):
        """弹出并清空所有待发帧"""
        frames = list(self.pending_frames)
        self.pending_frames.clear()
        return frames

    def reset_operation_state(self):
        """重置单次操作级状态（防止跨请求累积泄漏导致客户端重复删除/Lua崩溃）"""
        self.touched_items.clear()
        self.removed_equips.clear()
        self.pending_frames.clear()

    # ---- payload 加载器（素材提取，惰性） ----
    # 邮件素材 bin（mail_30003/30009）已随死 handler 一并退役（2026-08-22）
    def battle_03_payload(self):
        if self._p03_payload is None:
            self._p03_payload = _load_bin("sc_54003_payload.bin")
        return self._p03_payload

    def next_chat_srv(self, req):
        """91xxx 连接级 srv：递增（首帧以请求 srv 为基准+1）。"""
        self.chat_srv[0] = (self.chat_srv[0] if self.chat_srv[0] is not None else req.server_idx) + 1
        return self.chat_srv[0]


_LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
_BIN_CACHE = {}


def _load_bin(name):
    if name not in _BIN_CACHE:
        p = os.path.join(_LIB_DIR, name)
        _BIN_CACHE[name] = open(p, "rb").read() if os.path.exists(p) else b""
    return _BIN_CACHE[name]


# ---------------- 时间同步（10500/10501） ----------------

def _refresh_timestamps(now_ts):
    """返回 (now_ts, next_refresh, next_weekly, next_monthly)（刷新点 04:00，与真实服务器一致）。"""
    import datetime as _dt
    now = _dt.datetime.fromtimestamp(now_ts)

    def next_4am(d):
        t = d.replace(hour=4, minute=0, second=0, microsecond=0)
        if t <= d:
            t += _dt.timedelta(days=1)
        return t

    def next_week_4am(d):
        t = d.replace(hour=4, minute=0, second=0, microsecond=0)
        while t.weekday() != 0:
            t += _dt.timedelta(days=1)
        if t <= d:
            t += _dt.timedelta(days=7)
        return t

    def next_month_4am(d):
        t = (d.replace(day=1, hour=4, minute=0, second=0, microsecond=0)
             + _dt.timedelta(days=32)).replace(day=1)
        return t

    return (now_ts, int(next_4am(now).timestamp()),
            int(next_week_4am(now).timestamp()), int(next_month_4am(now).timestamp()))


def gen_10501_payload(now_ts):
    """sc_10501: result=1, timestamp=2, verify_timestamp=3,
       next_refresh_time=4, next_weekly_refresh_time=5, next_monthly_refresh_time=6"""
    ts, nxt, wk, mo = _refresh_timestamps(now_ts)
    p = b"\x08" + _varint(0)                      # result=0
    p += b"\x10" + _varint(ts)                    # timestamp=now
    p += b"\x18" + _varint(316800)                # verify_timestamp
    p += b"\x20" + _varint(nxt)                   # next_refresh_time
    p += b"\x28" + _varint(wk)                    # next_weekly_refresh_time
    p += b"\x30" + _varint(mo)                    # next_monthly_refresh_time
    return p


# ---------------- CORE_HANDLERS：业务处理器（V4.1 if 链原样迁移） ----------------

def h_10500(req, ctx):
    """刷新时间同步 → sc_10501（动态时间戳）+ sc_12045（每日免费体力状态同步）"""
    ctx.log("cs_10500 -> 动态生成 sc_10501（刷新时间同步）", "DEBUG", module="TIME", to_console=False)
    frames = [DownFrame(10501, gen_10501_payload(int(time.time())))]
    if ctx.generator is not None and ctx.db is not None:
        try:
            p45 = ctx.generator.gen_payload(12045, uid=req.uid, db=ctx.db)
            if p45:
                frames.append(DownFrame(12045, p45))
        except Exception:
            pass
    return CSResponse(frames)


def h_10050(req, ctx):
    """心跳/时间同步 → sc_10051（素材实测：{state=0, timestamp=now, verify_timestamp=316800}）"""
    now = int(time.time())
    p = b"\x08\x00"                       # state=0
    p += b"\x10" + _varint(now)           # timestamp=当前时间（动态）
    p += b"\x18" + _varint(316800)        # verify_timestamp=316800（素材同值）
    # 全量底账记录每次心跳
    ctx.log("cs_10050 -> 动态生成 sc_10051（心跳时钟同步）", "DEBUG", module="HEARTBEAT", to_console=False)
    # 控制台人机流：经节流器每 30 秒聚合报告一次脉冲，绝不刷屏
    try:
        import log_sifter
        should_pulse, p_msg = log_sifter.heartbeat_throttler.record_pulse(uid=req.uid)
        if should_pulse:
            ctx.log(p_msg, "INFO", module="HEARTBEAT")
    except Exception:
        pass
    return CSResponse([DownFrame(10051, p)])


def h_10700(req, ctx):
    """服务器时间 → sc_10701"""
    ctx.log("cs_10700 -> 动态生成 sc_10701（服务器时间）", "DEBUG", module="TIME", to_console=False)
    return CSResponse([DownFrame(10701, b"\x08" + _varint(int(time.time())))])


def h_11010(req, ctx):
    """每日签到执行（QueryDailySign，signaction.lua:16-20——11010 是签到不是查询！）：
    客户端 AutoGetReward 登录自动签：cs_11010 {activity_id=3} → sc_11011。
    未签 → 累计+1（不要求连续不绑定日期）→ 奖励=signcfg 第 N 项 → 落库 →
           11011 {result:0, item_list:[奖励]}（客户端 OnDailySignCallBack 插入本地 days）
    已签 → 11011 {result=1}（客户端 isSuccess 失败 → 不重复插入 days，面板不弹）"""
    ctx.log("cs_11010 -> 每日签到执行（自动签，累计机制）")
    try:
        import time as _t
        import json as _j
        import generator as _g
        aid = int(req.payload and _req_activity(req, ctx) or _g._SIGN_DAILY)
        st = _g._sign_state(ctx.db, req.uid, aid)
        if st["signed_today"]:
            # 已签：result=0 + 空 item_list（空容器）。不报错！客户端 OnDailySignCallBack
            # isSuccess → UpdateDailySign(GetDeltaToday) 把"今天"插入本地 days →
            # 下次登录 GetDailySignIndex 命中 → 面板不再弹。
            # 此前返回 result=1：客户端弹"服务器内部错误"且不插入 → 每次登录都弹面板
            # （2026-08-17 实测修复）。hex = result=0 + item_list 空容器。
            return CSResponse([DownFrame(11011, bytes.fromhex("08001200"))])
        new_count = st["sign_count"] + 1
        if aid in (_g._SIGN_DAILY, _g._SIGN_MONTHCARD):
            rw = _g._sign_reward(ctx.db, st["month"], new_count)
        else:
            # 七日/限时签到：奖励来自活动 config_list 第 N 项（signcfg），不是月历表
            rw = _seven_day_reward(aid, new_count)
        items = [{"item_id": rw[0], "item_num": rw[1]}] if rw and rw[0] else []
        # 奖励落库（item_catalog 路由）
        for it in items:
            _item_route_add(ctx, req.uid, it["item_id"], it["item_num"])
        new_list = st["sign_list"] + [st["day"]]
        ctx.db.execute(
            "INSERT INTO sign (uid, activity_id, year, month, day, sign_list, sign_count, last_sign_ts) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(uid, activity_id) DO UPDATE SET year=excluded.year, month=excluded.month, "
            "day=excluded.day, sign_list=excluded.sign_list, sign_count=excluded.sign_count, "
            "last_sign_ts=excluded.last_sign_ts",
            (req.uid, aid, st["year"], st["month"], st["day"],
             _j.dumps(new_list), new_count, int(_t.time())))
        payload = ctx.codec_encode("sc_11011", {"result": 0, "item_list": items}) or b"\x08\x00"
        return CSResponse([DownFrame(11011, payload)])
    except Exception:
        return CSResponse([DownFrame(11011, bytes.fromhex("0800120608edba021032"))])


def _req_activity(req, ctx):
    """解析 cs_11010/cs_11081 的 activity_id（payload 解码失败默认每日活动）。"""
    try:
        if ctx.codec_decode and req.payload:
            d = ctx.codec_decode(req.payload, "cs_%d" % req.cmd)
            if isinstance(d, dict):
                return int(d.get("activity_id") or 0)
    except Exception:
        pass
    return 0


_SEVEN_DAY_REWARD = None

def _seven_day_reward(activity_id, nth):
    """七日/限时签到第 nth 次的奖励：ActivityCumulativeSignCfg[id].config_list[nth-1]
    → SignCfg[sign_id].reward（均提取自 x64 Lua：activity_cumulative_sign_cfg.json /
    sign_cfg.json）。返回 (item_id, num) 或 None。"""
    global _SEVEN_DAY_REWARD
    if _SEVEN_DAY_REWARD is None:
        import json as _j7
        import os as _o7
        try:
            base = _o7.path.dirname(_o7.path.abspath(__file__))
            cl = _j7.load(open(_o7.path.join(base, "activity_cumulative_sign_cfg.json"), encoding="utf-8"))
            sc = _j7.load(open(_o7.path.join(base, "sign_cfg.json"), encoding="utf-8"))
            _SEVEN_DAY_REWARD = {int(a): [tuple(sc[str(sid)]) for sid in ids
                                           if str(sid) in sc or sid in sc]
                                 for a, ids in cl.items()}
        except Exception:
            _SEVEN_DAY_REWARD = {}
    lst = _SEVEN_DAY_REWARD.get(int(activity_id)) or []
    return lst[nth - 1] if 1 <= nth <= len(lst) else None


def _item_route_add(ctx, uid, item_id, num):
    """奖励落库：统一使用 inventory_service.grant_item（纳管 currency/material/equip/servant 全资产落库与时间戳维护）。"""
    try:
        import inventory_service
        inventory_service.grant_item(ctx, uid, int(item_id), int(num), source="hero_race_collect")
        return
    except Exception:
        pass
    try:
        if hasattr(ctx, "db") and ctx.db:
            ctx.db.add_item(uid, int(item_id), int(num))
    except Exception:
        pass


def h_11081(req, ctx):
    """七日/限时皮肤签到执行 → sc_11082 {result, sign_num, item_list}。

    sevendayskinaction.lua ReqSign：cs_11081 {activity_id}。此前是纯应答桩：
    不写库 → 客户端本地推进，重登状态回退 → 签到窗反复弹、奖励重复显示。
    落库语义与 h_11010 七日分支一致（count+1、last_sign_ts=now、奖励按
    config_list 第 N 项发放）。"""
    ctx.log("cs_11081 -> 七日/限时签到执行（落库+发奖）")
    try:
        import time as _t
        import json as _j
        import generator as _g
        aid = int(req.payload and _req_activity(req, ctx) or 0)
        if not aid:
            return CSResponse([DownFrame(11082, b"\x08\x02")])
        st = _g._sign_state(ctx.db, req.uid, aid)
        if st["signed_today"]:
            # 已签：result=0 + 当前次数，不发奖（客户端 UpdateActivityData 刷新）
            return CSResponse([DownFrame(11082, ctx.codec_encode(
                "sc_11082", {"result": 0, "sign_num": st["sign_count"]}) or b"\x08\x00")])
        new_count = st["sign_count"] + 1
        rw = _seven_day_reward(aid, new_count)
        items = []
        if rw:
            items.append({"item_id": rw[0], "item_num": rw[1]})
            _item_route_add(ctx, req.uid, rw[0], rw[1])
        ctx.db.execute(
            "INSERT INTO sign (uid, activity_id, year, month, day, sign_list, sign_count, last_sign_ts) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(uid, activity_id) DO UPDATE SET year=excluded.year, month=excluded.month, "
            "day=excluded.day, sign_list=excluded.sign_list, sign_count=excluded.sign_count, "
            "last_sign_ts=excluded.last_sign_ts",
            (req.uid, aid, st["year"], st["month"], st["day"], _j.dumps([]),
             new_count, int(_t.time())))
        payload = ctx.codec_encode("sc_11082", {
            "result": 0, "sign_num": new_count, "item_list": items}) or b"\x08\x00"
        return CSResponse([DownFrame(11082, payload)])
    except Exception:
        return CSResponse([DownFrame(11082, b"\x08\x09")])


def h_34024(req, ctx):
    """月卡每日奖励领取 → 3 帧链 [17023 材料, 59009 月卡状态, 34025 领取结果]。
    月卡签到执行（payaction.GetMonthCardBonus）：今日未签 → 90 移转之辉入账
    （currency id=1 +90）+ sign 表(activity=34024) 记录；已签 → 仅 is_sign=1 空奖励。
    客户端 SignToday(is_sign) + getReward(reward_list)；失败才弹签到面板（SIGN_INPUT）。
    2026-08-17 卡死修复：素材实测 34024 是 3 帧链（骨架无 hex 帧内容会丢），
    缺 17023/59009 帧 → 客户端等待 → 月卡界面卡死。"""
    ctx.log("cs_34024 -> 月卡签到领取（90 移转之辉）3 帧链")
    try:
        import time as _t
        import json as _j
        import generator as _g
        now = _t.localtime()
        st = _g._sign_state(ctx.db, req.uid, _g._SIGN_MONTHCARD)
        reward_list = []
        if not st["signed_today"]:
            # 发 90 移转之辉（currency id=1）
            ctx.db.execute(
                "INSERT INTO currency (uid, id, num) VALUES (?,1,90) "
                "ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num", (req.uid,))
            sl = list(st["sign_list"]) + [now.tm_mday]
            ctx.db.execute(
                "INSERT INTO sign (uid, activity_id, year, month, day, sign_list, sign_count, last_sign_ts) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid, activity_id) DO UPDATE SET year=excluded.year, month=excluded.month, "
                "day=excluded.day, sign_list=excluded.sign_list, sign_count=excluded.sign_count, "
                "last_sign_ts=excluded.last_sign_ts",
                (req.uid, _g._SIGN_MONTHCARD, now.tm_year, now.tm_mon, now.tm_mday,
                 _j.dumps(sl), st["sign_count"] + 1, int(_t.time())))
            reward_list = [{"item_id": 1, "item_num": 90}]
        # 帧链：17023 材料动态 + 59009 月卡状态（素材 hex）+ 34025 领取结果
        p17 = b""
        try:
            p17 = _g.gen_payload(17023, uid=req.uid, db=ctx.db) or b""
        except Exception:
            p17 = b""
        p59 = bytes.fromhex(
            "0a220a0c080010001800200028003000120c08011001180b2000280030001a0408001000")
        p25 = ctx.codec_encode("sc_34025", {"result": 0, "is_sign": 1,
                                            "reward_list": reward_list}) or b"\x08\x00"
        # 帧序：素材实测官方链 = [17023, 59009, 34025]（tcpfwd 素材 34024 请求后逐帧核对）
        return CSResponse([DownFrame(17023, p17), DownFrame(59009, p59), DownFrame(34025, p25)])
    except Exception:
        return CSResponse([DownFrame(34025, bytes.fromhex("080010001a040801105a"))])


# 2026-08-22 清理：h_30020/h_30002/h_30004/h_30008 四个素材回退 handler 已删除。
# OPERATIONS 优先级使它们从未生效（MailGetListOp 等 6 个操作类才是真入口，mail 表驱动），
# 注释里"动态版卡死故回退素材"的决策也因此从未执行过；cs_30002 若实测仍卡死，
# 问题在操作类帧组装而非数据源。_mark_mail_claimed 一并移除（领取落库由 MailReceiveOp 承担）。

def h_30001(req, ctx):
    """未读邮件摘要 → sc_30001（generator 库驱动：mail 表 read_flag!=2 统计）。

    此前该 cmd 全链无入口，落到 ack0 兜底回 sc_30002 畸形帧。
    """
    p = None
    try:
        import generator as _g
        p = _g.gen_payload(30001, uid=req.uid, db=ctx.db)
    except Exception as e:
        ctx.log(f"cs_30001 -> 摘要生成失败: {e}", "WARN")
    if not p:
        return CSResponse(reply=False)
    return CSResponse([DownFrame(30001, p)])


def h_30016(req, ctx):
    """专属信件列表 → sc_30017（generator 库驱动：letter_special 表）。

    此前该 cmd 全链无入口，落到 ack0 兜底回 schema 填充帧（与洪流 DB 版不同源）。
    """
    p = None
    try:
        import generator as _g
        p = _g.gen_payload(30017, uid=req.uid, db=ctx.db)
    except Exception as e:
        ctx.log(f"cs_30016 -> 信件列表生成失败: {e}", "WARN")
    if not p:
        return CSResponse(reply=False)
    return CSResponse([DownFrame(30017, p)])


def h_16012(req, ctx):
    """抽卡信息 → sc_16013 骨架 result=0"""
    ctx.log("cs_16012 -> 动态生成 sc_16013（抽卡 result=0）")
    return CSResponse([DownFrame(16013, b"\x08\x00")])


def h_58002(req, ctx):
    """家园/后宅总览全量数据 → sc_58003（db.get_backhome_detail 全量动态驱动：餐厅、食材、菜谱、宿舍房间、英雄入住、家具、布局）。"""
    bh_data = ctx.db.get_backhome_detail(req.uid) if ctx.db else {}
    _p03 = ctx.codec_encode("sc_58003", bh_data) if (ctx.codec_encode and bh_data) else None
    if not _p03:
        if ctx.generator is not None:
            _p03 = ctx.generator.gen_payload(58003, uid=req.uid, db=ctx.db)
        if not _p03:
            _p03 = b"\x08\x00"
    ctx.log(f"cs_58002 -> 家园总览详情 dorms={len(bh_data.get('dorms', []))} canteens={len(bh_data.get('canteens', []))} furnitures={len(bh_data.get('furnitures', []))}")
    return CSResponse([DownFrame(58003, _p03)])


def h_28014(req, ctx):
    """任务一键领取 → sc_28007(进度) + sc_28015(真实奖励)；幂等：已领任务跳过入账"""
    _ids = []
    try:
        if ctx.codec_decode:
            _d = ctx.codec_decode(req.payload, "cs_28014")
            _ids = _d.get("id_list") if isinstance(_d, dict) else []
    except Exception:
        pass
    _claimed = 0
    if _ids and ctx.db is not None:
        for _tid in _ids:
            if ctx.db.is_task_claimed(req.uid, _tid):
                _claimed += 1
                continue  # 已领跳过（幂等）
            ctx.db.claim_task(req.uid, _tid)
        ctx.log(f"cs_28014 -> 一键领取 {len(_ids)} 个任务入账（{_claimed} 个已领跳过）")
    _p07 = bytes.fromhex("0a0b08b1db0610a50318002000")
    _p15 = bytes.fromhex("0800120608a29c0110011204080c100a1204080c100a12040816100a")
    return CSResponse([DownFrame(28007, _p07), DownFrame(28015, _p15)])


def h_62012(req, ctx):
    """回归活动签到 → sc_17023(道具) + sc_62013(真实奖励)"""
    _p17 = bytes.fromhex("0a040826106a")          # 道具 id=38 num=106
    _p13 = bytes.fromhex("0800120408261001")      # 签到奖励 id=38 num=1
    ctx.log("cs_62012 -> 动态 sc_17023(道具)+sc_62013(回归签到真实奖励)")
    return CSResponse([DownFrame(17023, _p17), DownFrame(62013, _p13)])


def h_16010(req, ctx):
    """抽卡 → 官方链：17023×2 + 53003 + 28007 + 16011（10 全金卡）"""
    _p17a = ctx.codec_encode("sc_17023", {"normal_items": [{"id": 38, "num": 95},
                                                            {"id": 36, "num": 1000}]}) or b""
    _p17b = ctx.codec_encode("sc_17023", {"normal_items": [{"id": 1166, "num": 1}] * 10}) or b""
    _p53 = b"\x0a\x00"                            # 53003 钥从列表空
    _p07 = bytes.fromhex("0a0a08c5d5031001180020000a0a08c6d5031001180020000a0a08c7d5031001180020000a0a08c8d5031001180020000a0a089ee309100118002000")
    _p11 = bytes.fromhex("08001205088e0910011205088e0910011205088e0910011205088e0910011205088e0910011205088e0910011205088e0910011205088e0910011205088e0910011205088e09100118002000280a")
    ctx.log("cs_16010 -> 官方链+16011（10 全金卡）")
    return CSResponse([DownFrame(17023, _p17a), DownFrame(17023, _p17b),
                       DownFrame(53003, _p53), DownFrame(28007, _p07), DownFrame(16011, _p11)])


def h_91002(req, ctx):
    """聊天选项选择 / 分支落库 → 落库 + sc_91003（flag=1 Lua 投递 + 连接级 srv）"""
    _srv = ctx.next_chat_srv(req)
    sid, cid, state = 0, 0, 0
    try:
        dec = ctx.codec_decode(req.payload, "cs_91002") if ctx.codec_decode else None
        if isinstance(dec, dict):
            sid = dec.get("session_id", 0)
            cid = dec.get("content_id", 0)
            state = dec.get("state", 0)
    except Exception:
        pass
    if sid and cid and ctx.db:
        ctx.db.finish_momotalk_break(req.uid, sid, cid, state)
        ctx.log(f"cs_91002 -> MomoTalk 选项选择落库 session={sid} content={cid} state={state}")
    _p = ctx.codec_encode("sc_91003", {"result": 0}) or b"\x08\x00"
    ctx.log(f"cs_91002 -> 动态 sc_91003(ack flag=1 srv={_srv})")
    return CSResponse([DownFrame(91003, _p, flag=1, srv=_srv)])


def h_91016(req, ctx):
    """换 MomoTalk 头像框 → 落库 + sc_91017 result=0（flag=1 + 连接级 srv）"""
    _srv = ctx.next_chat_srv(req)
    icon = 0
    try:
        dec = ctx.codec_decode(req.payload, "cs_91016") if ctx.codec_decode else None
        if isinstance(dec, dict):
            icon = dec.get("icon", 0)
    except Exception:
        pass
    if icon and ctx.db:
        ctx.db.set_momotalk_frame(req.uid, icon)
        ctx.log(f"cs_91016 -> 更换 MomoTalk 头像框 icon={icon}")
    _p = ctx.codec_encode("sc_91017", {"result": 0}) or b"\x08\x00"
    ctx.log(f"cs_91016 -> 动态 sc_91017(ack flag=1 srv={_srv})")
    return CSResponse([DownFrame(91017, _p, flag=1, srv=_srv)])


def h_91006(req, ctx):
    """会话读取上报（消除红点） → 落库 + sc_91007 ack（flag=1 + 连接级 srv）"""
    _srv = ctx.next_chat_srv(req)
    sid = 0
    try:
        dec = ctx.codec_decode(req.payload, "cs_91006") if ctx.codec_decode else None
        if isinstance(dec, dict):
            sid = dec.get("session_id", 0)
    except Exception:
        pass
    if sid and ctx.db:
        ctx.db.set_momotalk_read(req.uid, sid)
        ctx.log(f"cs_91006 -> MomoTalk 会话已读 session={sid}（消除红点）")
    _p = ctx.codec_encode("sc_91007", {"result": 0}) or b"\x08\x00"
    ctx.log(f"cs_91006 -> 动态 sc_91007(ack srv={_srv})")
    return CSResponse([DownFrame(91007, _p, flag=1, srv=_srv)])


def h_91014(req, ctx):
    """读会话内容 / 保存新断点 → 落库 + sc_91015 ack（flag=1 + 连接级 srv）"""
    _srv = ctx.next_chat_srv(req)
    sid, cid = 0, 0
    try:
        dec = ctx.codec_decode(req.payload, "cs_91014") if ctx.codec_decode else None
        if isinstance(dec, dict):
            sid = dec.get("session_id", 0)
            cid = dec.get("content_id", 0)
    except Exception:
        pass
    if sid and cid and ctx.db:
        ctx.db.save_momotalk_break(req.uid, sid, cid)
        ctx.log(f"cs_91014 -> 保存 MomoTalk 对话进度 session={sid} content={cid}")
    _p = ctx.codec_encode("sc_91015", {"result": 0}) or b"\x08\x00"
    ctx.log(f"cs_91014 -> 动态 sc_91015(ack srv={_srv})")
    return CSResponse([DownFrame(91015, _p, flag=1, srv=_srv)])


def h_28010(req, ctx):
    """单个任务提交 → sc_28011（result=0 + 真实奖励）；幂等：已领任务不发奖励"""
    _tid = None
    try:
        if ctx.codec_decode:
            _d = ctx.codec_decode(req.payload, "cs_28010")
            _tid = _d.get("id") if isinstance(_d, dict) else None
    except Exception:
        pass
    _claimed = False
    if _tid is not None and ctx.db is not None:
        _claimed = ctx.db.is_task_claimed(req.uid, _tid)
    if _claimed:
        _p11 = b"\x08\x00"  # result=0 无奖励（幂等，防重复入账）
        ctx.log(f"cs_28010 -> task={_tid} 已领，幂等回 result=0")
    else:
        _p11 = bytes.fromhex("0800120608a29c0110011204080c100a1204080c100a12040816100a")
        if _tid is not None and ctx.db is not None:
            ctx.db.claim_task(req.uid, _tid)
            ctx.log(f"cs_28010 -> task={_tid} 领取入账")
    return CSResponse([DownFrame(28011, _p11)])


def _pkt_session_id(pkt):
    """解析请求 payload field 1 varint（cs_54034 的 stage_id）。"""
    b = pkt.get("payload") or b""
    if len(b) >= 2 and b[0] == 0x08:
        v, sh, i = 0, 0, 1
        while i < len(b):
            x = b[i]; i += 1
            v |= (x & 0x7F) << sh
            if not (x & 0x80):
                return v
            sh += 7
    return 0


def _pkt_field1_str(pkt):
    """解析请求 payload field1 string（wire type 2, tag 0x0a）。
    注意：cs_54032.battle_id 是 uint64 而非 string（decompiled_v2 的 p54_pb.lua 实测
    type=4 即 TYPE_UINT64），battle_id 请改用 _pkt_field1_varint。"""
    b = pkt.get("payload") or b""
    if b and b[0] == 0x0A:
        try:
            ln, sh, i = 0, 0, 1
            while i < len(b) and (b[i] & 0x80):
                ln |= (b[i] & 0x7F) << sh; sh += 7; i += 1
            if i < len(b):
                ln |= (b[i] & 0x7F) << sh
                i += 1
                return b[i:i + ln].decode("utf-8", errors="replace")
        except Exception:
            pass
    return ""


def _pkt_field1_varint(pkt):
    """解析请求 payload field1 varint（tag 0x08）。cs_54032.battle_id 用这个。"""
    return _pkt_session_id(pkt)


def _gen_battle_id(seq_ref):
    """battle_id 生成：时间戳毫秒 + 连接级自增序号。

    返回 int —— 协议里 battle_id 是 uint64（varint）：
    sc_54035.f2 / cs_54032.f1 / battle_result_net_rec.f1 / battle_start_net_rec.f2
    在 decompiled_v2/x64/protocol/p54_pb.lua 里 type 全为 4(TYPE_UINT64)。
    （只有 cs_54300.battle_id 是 string，那是 f21，另一回事。）
    17 位十进制 ≈ 1.7e16，远小于 uint64 上限，可安全 varint 编码。
    """
    seq_ref[0] += 1
    return int("%d%04d" % (int(time.time() * 1000), seq_ref[0]))


# ---------------- 战斗系统（V5 动态战斗服集成，2026-08-22） ----------------
# 会话/掉落/进度三层全部动态化，替代 V4.1 的重放依赖实现：
#   ① battle_id 会话注册（54030/54034 发起时登记 uid/dest/英雄/倍数）
#   ② 通用掉落引擎：dropcfg.lua（1274 库）+ 2114 关卡 drop_lib_id 映射（提取自 x64 Lua）
#      + chaptercfg 首通/重复奖励 + stage_sub 库内首通 —— 不再是硬编码 gain={32194,1}
#   ③ 结算引擎（h_54032）：UDP 132 上报结果登记 → 掉落计算/入包 → 关卡进度推进 →
#      24009 章节推送 + 15009/17009 刷新
# sc_54003 仍以素材 bin 为骨架（房间/队伍结构），但按出战英雄重写 hero_id（V4.1
# _gen_54003 移植——那段代码在 V4.1 里从未被调用过）。

_BATTLE_PORT = [6105]
_BATTLE_IP = ["127.0.0.1"]                  # 动态战斗服 IP（下发 sc_54007 用）
_BATTLE_SRV = [None]                      # BattleServer 实例（main 装配注入）
_BATTLE_SESSIONS = {}                     # battle_id -> {uid, dest, activity_id, battle_times, heroes, ts}
_BATTLE_LOCK = threading.Lock()

_DROP_CFG = None                          # drop_cfg.json（懒加载）
_STAGE_DROP = None                        # daily_stage_drop_cfg.json
_CHAPTER_REWARD = None                    # chapter_reward_cfg.json
_STAGE_INFO = None                        # stage_info_cfg.json
_STAGE_FIRST_REWARDS = None
_STAGE_STORY_TRIGGERS = None


def set_battle_server(bs, port, host_ip="127.0.0.1"):
    """main 装配：注入战斗服实例与端口及下发 IP（h_54030 的 sc_54007 用）。"""
    _BATTLE_SRV[0] = bs
    _BATTLE_PORT[0] = int(port)
    if host_ip:
        _BATTLE_IP[0] = str(host_ip)


def register_battle_session(bid, uid, dest=0, activity_id=0, battle_times=1, heroes=None, stage_type=0, node_id=0, cooperate_skill=0, mimir_info=None, is_story=False, **kwargs):
    with _BATTLE_LOCK:
        _BATTLE_SESSIONS[bid] = {"uid": uid, "dest": int(dest or 0),
                                 "activity_id": int(activity_id or 0),
                                 "battle_times": int(battle_times or 1),
                                 "heroes": list(heroes or []),
                                 "stage_type": int(stage_type or 0),
                                 "node_id": int(node_id or 0),
                                 "cooperate_skill": int(cooperate_skill or 0),
                                 "mimir_info": mimir_info if isinstance(mimir_info, dict) else {},
                                 "is_story": bool(is_story),
                                 "ts": time.time()}
        # 只留最近 100 场
        if len(_BATTLE_SESSIONS) > 100:
            for k in list(_BATTLE_SESSIONS)[:-100]:
                _BATTLE_SESSIONS.pop(k, None)


def get_battle_session(bid):
    with _BATTLE_LOCK:
        return _BATTLE_SESSIONS.get(bid)


def pop_battle_result(bid, default=None, timeout=0.15):
    """取 UDP 132 上报的战斗结果（battle_server 登记），带 150ms 轮询防抖。"""
    bs = _BATTLE_SRV[0]
    if bs is None:
        return default
    # 立即尝试
    res = bs.get_result(bid, None)
    if res is not None:
        return res
    # 轮询最多 timeout 秒（应对 UDP/TCP 瞬时网络时序差）
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.01)
        res = bs.get_result(bid, None)
        if res is not None:
            return res
_DROP_CFG = None                          # drop_cfg.json（懒加载）
_STAGE_DROP = None                        # daily_stage_drop_cfg.json
_CHAPTER_REWARD = None                    # chapter_reward_cfg.json
_STAGE_INFO = None                        # stage_info_cfg.json


def _load_battle_cfgs():
    global _DROP_CFG, _STAGE_DROP, _CHAPTER_REWARD, _STAGE_INFO, _STAGE_FIRST_REWARDS, _STAGE_STORY_TRIGGERS
    if _DROP_CFG is None:
        import json as _jb
        base = os.path.dirname(os.path.abspath(__file__))
        try:
            _DROP_CFG = _jb.load(open(os.path.join(base, "drop_cfg.json"), encoding="utf-8"))
        except Exception:
            _DROP_CFG = {}
        try:
            _STAGE_DROP = _jb.load(open(os.path.join(base, "daily_stage_drop_cfg.json"), encoding="utf-8"))
        except Exception:
            _STAGE_DROP = {}
        try:
            _CHAPTER_REWARD = _jb.load(open(os.path.join(base, "chapter_reward_cfg.json"), encoding="utf-8"))
        except Exception:
            _CHAPTER_REWARD = {}
        try:
            _STAGE_INFO = _jb.load(open(os.path.join(base, "stage_info_cfg.json"), encoding="utf-8"))
        except Exception:
            _STAGE_INFO = {}
        try:
            _STAGE_FIRST_REWARDS = _jb.load(open(os.path.join(base, "stage_first_clear_rewards.json"), encoding="utf-8"))
        except Exception:
            _STAGE_FIRST_REWARDS = {}
        try:
            _STAGE_STORY_TRIGGERS = _jb.load(open(os.path.join(base, "stage_story_triggers.json"), encoding="utf-8"))
        except Exception:
            _STAGE_STORY_TRIGGERS = {}
    return _DROP_CFG, _STAGE_DROP, _CHAPTER_REWARD, _STAGE_INFO, _STAGE_FIRST_REWARDS, _STAGE_STORY_TRIGGERS


def _weighted_pick(entries, rng):
    """权重掉落：entries=[{item,num,weight}] 抽 1 条。"""
    total = sum(e[2] for e in entries)
    r = rng.randint(1, max(total, 1))
    acc = 0
    for e in entries:
        acc += e[2]
        if r <= acc:
            return e[0], e[1]
    e = entries[-1]
    return e[0], e[1]


def stage_drop(dest, rng=None):
    """通用掉落引擎：dest → gain_list [[item,num],..]。

    层级：关卡 drop_lib_id → dropcfg (base 保底 + random 独立概率 + weight 权重抽取)
          → 无库则 chapter_reward（主线首通/重复在结算处另行叠加）
          → 再无则 stage_sub.first_reward（库表）
          → 兜底 [(1,10)]（移转之辉）。
    """
    import random as _r
    rng = rng or _r
    drop_cfg, stage_drop_map, chapter_reward, _, _, _ = _load_battle_cfgs()
    lib = stage_drop_map.get(str(dest)) or stage_drop_map.get(dest)
    if lib is not None:
        d = drop_cfg.get(str(lib)) or drop_cfg.get(lib)
        if d:
            gains = []
            # 1. 基础保底掉落 base_drop: [[item, num, prob], ...]
            for b_item in (d.get("base") or []):
                if len(b_item) >= 3:
                    iid, num, prob = b_item[0], b_item[1], b_item[2]
                    if prob >= 100 or rng.randint(1, 100) <= prob:
                        gains.append([iid, num])
                elif len(b_item) >= 2:
                    gains.append([b_item[0], b_item[1]])
            # 2. 独立概率随机掉落 random_drop: [[item, num, prob], ...]
            for r_item in (d.get("random") or []):
                if len(r_item) >= 3:
                    iid, num, prob = r_item[0], r_item[1], r_item[2]
                    if prob >= 100 or rng.randint(1, 100) <= prob:
                        gains.append([iid, num])
                elif len(r_item) >= 2:
                    gains.append([r_item[0], r_item[1]])
            # 3. 权重抽取掉落 weight_drop: [[item, num, weight], ...]
            entries = [tuple(x) for x in (d.get("weight") or []) if len(x) >= 3]
            w_cnt = int(d.get("weight_count") or d.get("count") or (1 if entries else 0))
            for _ in range(w_cnt):
                if entries:
                    iid, num = _weighted_pick(entries, rng)
                    gains.append([iid, num])
            if gains:
                return gains
    cr = chapter_reward.get(str(dest)) or chapter_reward.get(dest)
    if cr and (cr.get("second") or cr.get("first")):
        return [list(x) for x in (cr.get("second") or cr.get("first"))]
    return None


def chapter_bonus(dest, first_clear):
    """主线/剧情章节首通/重复奖励（stage_first_clear_rewards / chaptercfg）。"""
    _, _, chapter_reward, _, first_rewards_map, _ = _load_battle_cfgs()
    if first_clear:
        if first_rewards_map:
            fr = first_rewards_map.get(str(dest)) or first_rewards_map.get(dest)
            if fr:
                return [list(x) for x in fr]
        cr = chapter_reward.get(str(dest)) or chapter_reward.get(dest)
        if cr and cr.get("first"):
            return [list(x) for x in cr["first"]]
    else:
        cr = chapter_reward.get(str(dest)) or chapter_reward.get(dest)
        if cr and cr.get("second"):
            return [list(x) for x in cr["second"]]
    return []


def stage_first_reward_db(db, dest):
    """库内 stage_sub 首通奖励兜底。"""
    try:
        rows = db.query("SELECT first_reward FROM stage_sub WHERE id=?", (int(dest),))
        if rows:
            import json as _jf
            v = _jf.loads(rows[0]["first_reward"] or "[]")
            return [list(x) for x in v] if v else []
    except Exception:
        pass
    return []


def _stage_progress(db, dest):
    """当前通关次数（chapter 主线进度表；无行=未通关）。"""
    try:
        rows = db.query("SELECT clear_times FROM chapter WHERE id=?", (int(dest),))
        return int(rows[0]["clear_times"] or 0) if rows else 0
    except Exception:
        return 0


_STAGE_THREE_STARS = None


def _load_three_star_cfg():
    global _STAGE_THREE_STARS
    if _STAGE_THREE_STARS is None:
        import json as _jb
        base = os.path.dirname(os.path.abspath(__file__))
        try:
            meta = _jb.load(open(os.path.join(base, "trial_system_data.json"), encoding="utf-8"))
            _STAGE_THREE_STARS = meta.get("stage_three_stars", {})
        except Exception:
            _STAGE_THREE_STARS = {}
    return _STAGE_THREE_STARS


def evaluate_three_star(stage_id, win, b_info, heroes):
    """根据关卡配置与实际战报评估三星条件达成情况。"""
    if not win:
        return [0, 0, 0], []
    
    st_stars = _load_three_star_cfg()
    conds = st_stars.get(str(stage_id)) or st_stars.get(stage_id)
    if not conds:
        conds = [[8], [13], [14, 1]]
        
    b_info = b_info or {}
    res_times = int(b_info.get("resurrect_times", 0))
    dead_num = int(b_info.get("total_dead_num", 0))
    hitted_num = int(b_info.get("total_hitted_num", 0))
    injured_num = int(b_info.get("injured_num", 0))
    battle_time = int(b_info.get("battle_time", 0))
    knockout_num = int(b_info.get("knockout_num", 0))
    enemy_dead = int(b_info.get("enemy_dead_num", 0))
    fall_down = int(b_info.get("fall_down_num", 0))
    
    star_achieved = []
    star_list_pb = []
    
    for idx, cond in enumerate(conds):
        cond_id = cond[0] if isinstance(cond, list) else int(cond)
        achieve = False
        now_prog = 0
        need_prog = 1
        
        if cond_id == 8:    # 通关关卡
            achieve = win
            now_prog = 1 if win else 0
        elif cond_id == 13:  # 不复活通关
            achieve = (res_times == 0)
            now_prog = 1 if (res_times == 0) else 0
        elif cond_id == 14:  # 复活次数不超过 X
            limit = cond[1] if len(cond) > 1 else 1
            achieve = (res_times <= limit)
            now_prog = 1 if achieve else 0
        elif cond_id == 1:   # 败退人次不超过 X
            limit = cond[1] if len(cond) > 1 else 0
            achieve = (dead_num <= limit)
            now_prog = 1 if achieve else 0
        elif cond_id == 2:   # 队伍中无人败退
            achieve = (dead_num == 0)
            now_prog = 1 if achieve else 0
        elif cond_id == 3:   # 队长受击数不超过 X
            limit = cond[1] if len(cond) > 1 else 0
            achieve = (hitted_num <= limit)
            now_prog = 1 if achieve else 0
        elif cond_id == 4:   # X秒内通关
            limit = cond[1] if len(cond) > 1 else 180
            achieve = (battle_time <= limit)
            now_prog = 1 if achieve else 0
        elif cond_id == 9:   # 受到伤害小于 X
            limit = cond[1] if len(cond) > 1 else 0
            achieve = (injured_num < limit)
            now_prog = 1 if achieve else 0
        elif cond_id == 10:  # 被击倒次数不超过 X
            limit = cond[1] if len(cond) > 1 else 0
            achieve = (fall_down <= limit)
            now_prog = 1 if achieve else 0
        elif cond_id == 11:  # 击倒怪数量达到 X
            limit = cond[1] if len(cond) > 1 else 1
            achieve = (knockout_num >= limit)
            now_prog = knockout_num
            need_prog = limit
        elif cond_id == 12:  # 队伍包含 X
            req_hero = cond[1] if len(cond) > 1 else 0
            achieve = (req_hero in heroes)
            now_prog = 1 if achieve else 0
        elif cond_id == 17:  # 击杀敌人数达到 X
            limit = cond[1] if len(cond) > 1 else 1
            achieve = (enemy_dead >= limit)
            now_prog = enemy_dead
            need_prog = limit
        else:
            achieve = win
            now_prog = 1 if win else 0
            
        star_achieved.append(1 if achieve else 0)
        star_list_pb.append({
            "star_id": idx + 1,
            "now_progress": now_prog,
            "need_progress": need_prog,
            "is_achieve": 1 if achieve else 0
        })
        
    return star_achieved, star_list_pb


def _advance_stage(db, uid, dest, star_achieved=None, times=1):
    """通关推进：委托给 StageService 统管领域服务，自动识别主线/支线/剧情，合并星级，广播事件总线。"""
    try:
        from stage_service import StageService
        svc = StageService.get_instance(db=db)
        new_ct, _, _ = svc.pass_stage(uid, dest, win_stars=star_achieved, times=times)
        return new_ct
    except Exception as e:
        import logging
        logging.getLogger("middleware").warning(f"[_advance_stage] StageService pass_stage 异常: {e}")
        return 1



# ---- cs_54030/cs_54038 common_info 解析（codec schema 驱动） ----

def _parse_battle_common(req, ctx):
    """cs_54030/54038 的 common_info → {heroes, hero_details, dest, activity_id, battle_times, type, cooperate_skill, mimir_info}。"""
    out = {"heroes": [], "hero_details": [], "dest": 0, "activity_id": 0, "battle_times": 1, "type": 0,
           "cooperate_skill": 0, "mimir_info": None}
    try:
        if ctx.codec_decode and req.payload:
            d = ctx.codec_decode(req.payload, "cs_%d" % req.cmd)
            ci = (d or {}).get("common_info") or {}
            out["dest"] = int(ci.get("dest") or 0)
            out["activity_id"] = int(ci.get("activity_id") or 0)
            out["battle_times"] = int(ci.get("battle_times") or 1)
            out["type"] = int(ci.get("type") or 0)
            out["cooperate_skill"] = int(ci.get("cooperate_unique_skill_id") or 0)
            out["mimir_info"] = ci.get("mimir_info")
            for h in (ci.get("hero_list") or []):
                # 实测（两次失败抓包 cs_54030）hero_list[].hero_base_info 是裸 varint
                # hero_id（schema 标注的嵌套 hero_base_net_rec 与线上格式不符），两种都兼容
                hb = h.get("hero_base_info")
                if isinstance(hb, dict):
                    hid = hb.get("id") or hb.get("hero_id")
                elif isinstance(hb, int):
                    hid = hb
                else:
                    hid = h.get("hero_id") or h.get("id")
                if hid:
                    out["heroes"].append(int(hid))
                    # hero_type：1己方/2试用（试用英雄不持久化到关卡现役编队）
                    out["hero_details"].append({
                        "hero_id": int(hid),
                        "hero_type": int(h.get("hero_type") or 1),
                    })
    except Exception:
        pass
    return out


# ---- 54003 素材骨架英雄重写（V4.1 _gen_54003 移植，最小 pb 工具） ----

def _pb_fields(buf):
    """[(field, wire_type, value)] 顶层扫描（varint → int；LEN → bytes）。"""
    out, i = [], 0
    try:
        while i < len(buf):
            tag, i = rd_varint(buf, i)
            f, w = tag >> 3, tag & 7
            if w == 0:
                v, i = rd_varint(buf, i)
                out.append((f, w, v))
            elif w == 2:
                ln, i = rd_varint(buf, i)
                out.append((f, w, buf[i:i + ln]))
                i += ln
            else:
                break
    except (IndexError, ValueError):
        pass
    return out


def _pb_field_bytes(fnum, wt, val):
    if wt == 0:
        return _tag(fnum, 0) + _varint(int(val))
    return _tag(fnum, 2) + _varint(len(val)) + val


def rewrite_54003_heroes(hero_ids, tpl):
    """按出战英雄重写 54003 模板里的 hero_base_info.hero_id（其余字段保持模板）。"""
    if not hero_ids or not tpl:
        return tpl
    try:
        out = []
        for fnum, wt, val in _pb_fields(tpl):
            if fnum == 1 and wt == 2:  # player_room_info
                pri_out = []
                for pf, pw, pv in _pb_fields(val):
                    if pf == 2 and pw == 2:  # 队伍 hero_list
                        team_out, hi = [], 0
                        for tf, tw, tv in _pb_fields(pv):
                            if tf == 4 and tw == 2 and hi < len(hero_ids):  # hero
                                hb_out = []
                                for hf, hw, hv in _pb_fields(tv):
                                    if hf == 1 and hw == 2 and hi < len(hero_ids):
                                        hbi_out, replaced = [], False
                                        for bf, bw, bv in _pb_fields(hv):
                                            if bf == 1 and bw == 0 and not replaced:
                                                hbi_out.append(_pb_field_bytes(1, 0, hero_ids[hi]))
                                                replaced = True
                                            else:
                                                hbi_out.append(_pb_field_bytes(bf, bw, bv))
                                        hb_out.append(_pb_field_bytes(1, 2, b"".join(hbi_out)))
                                        hi += 1
                                    else:
                                        hb_out.append(_pb_field_bytes(hf, hw, hv))
                                team_out.append(_pb_field_bytes(4, 2, b"".join(hb_out)))
                            else:
                                team_out.append(_pb_field_bytes(tf, tw, tv))
                        pri_out.append(_pb_field_bytes(2, 2, b"".join(team_out)))
                    else:
                        pri_out.append(_pb_field_bytes(pf, pw, pv))
                out.append(_pb_field_bytes(1, 2, b"".join(pri_out)))
            else:
                out.append(_pb_field_bytes(fnum, wt, val))
        return b"".join(out)
    except Exception:
        return tpl


# ---- 结算辅助与成长计算 ----

def _stage_stamina_cost(db, dest):
    """获取关卡体力消耗（优先 stage_info_cfg / chapter / stage_sub 库表配置）。"""
    if not dest:
        return 0
    dest_int = int(dest or 0)
    # 高难/挑战/周本/肉鸽/核心检验/迭代校验/梦境再构/黑区净化/教学等玩法不消耗体力
    if (2000 <= dest_int <= 2999 or
        3090001 <= dest_int <= 3093999 or
        10101 <= dest_int <= 16208 or
        3010101 <= dest_int <= 3012399 or
        3026001 <= dest_int <= 3028041 or
        dest_int in (1, 2)):
        return 0

    stage_info = _load_battle_cfgs()[3]
    s_info = stage_info.get(str(dest)) or stage_info.get(dest)
    if s_info and s_info.get("cost") is not None:
        return int(s_info["cost"])
    if db:
        try:
            rows = db.query("SELECT stamina_need FROM chapter WHERE id=?", (dest_int,))
            if rows and rows[0]["stamina_need"] is not None:
                return int(rows[0]["stamina_need"])
            rows = db.query("SELECT stamina_need FROM stage_sub WHERE id=?", (dest_int,))
            if rows and rows[0]["stamina_need"] is not None:
                return int(rows[0]["stamina_need"])
        except Exception:
            pass
    # 未明确配置消耗的特殊/挑战/活动关卡默认不扣体力（仅有配置了消耗的主线/材料关扣体力）
    return 0


def _update_hero_battle_growth(db, uid, heroes, dest=0, times=1):
    """出战英雄战毕成长：
    1. 熟练度（clear_times +1*times，上限 100，解神格前置条件）
    2. 好感度/心智链档案（hero_archive.exp +5*times，上限 1000；hero.trust_exp）
    3. 角色经验（按 stage_info_cfg 中的 hero_exp * times 计算）
    """
    if not db or not heroes:
        return
    times = max(1, int(times or 1))
    stage_info = _load_battle_cfgs()[3]
    s_info = (stage_info.get(str(dest)) or stage_info.get(dest) or {}) if dest else {}
    hero_exp_per = int(s_info.get("hero_exp") or 20)
    add_exp = hero_exp_per * times

    for hid in heroes:
        try:
            hid = int(hid)
            # 1. 角色熟练度：上限 100（gamesetting: mastery_gain=1, mastery_level_max=100）
            db.execute(
                "UPDATE hero SET clear_times = MIN(100, clear_times + ?) WHERE uid=? AND id=?",
                (times, uid, hid))
            try:
                from event_bus import bus, Events
                bus.emit(Events.HERO_UPGRADE, None, uid, hero_id=hid, oper="proficiency_up")
            except Exception:
                pass
            # 2. 一阶档案好感度：hero_archive 表（gamesetting: hero_love_exp_gain=5，上限 1000）
            db.execute(
                "UPDATE hero_archive SET exp = MIN(1000, exp + ?) WHERE uid=? AND archive_id=?",
                (5 * times, uid, hid))
            # 3. 角色经验
            db.execute(
                "UPDATE hero SET exp = exp + ? WHERE uid=? AND id=?",
                (add_exp, uid, hid))
        except Exception:
            pass


_USER_LEVEL_SETTINGS = None


def _calc_player_level(total_exp):
    """根据 user_level_setting.json 累加总经验计算玩家真实等级。"""
    global _USER_LEVEL_SETTINGS
    if _USER_LEVEL_SETTINGS is None:
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            cfg_p = os.path.join(base, "user_level_setting.json")
            if os.path.exists(cfg_p):
                import json as _jb
                _USER_LEVEL_SETTINGS = {int(k): int(v) for k, v in _jb.load(open(cfg_p, encoding="utf-8")).items()}
        except Exception:
            _USER_LEVEL_SETTINGS = {}
    if not _USER_LEVEL_SETTINGS:
        return 92
    lv = 1
    rem = int(total_exp or 0)
    while lv in _USER_LEVEL_SETTINGS and rem >= _USER_LEVEL_SETTINGS[lv]:
        rem -= _USER_LEVEL_SETTINGS[lv]
        lv += 1
    return lv


def _update_player_battle_growth(ctx_or_db, uid, dest=0, times=1, stamina_cost=10):
    """玩家经验与等级结算：累加总经验 total_exp，按官方曲线计算新等级，
    并完整通过 InventoryService 驱动体力发放、货币更新与事件广播。"""
    db = getattr(ctx_or_db, "db", ctx_or_db)
    if not db:
        return 0
    stage_info = _load_battle_cfgs()[3]
    s_info = (stage_info.get(str(dest)) or stage_info.get(dest) or {}) if dest else {}
    user_exp_per = int(s_info.get("user_exp") or stamina_cost or 10)
    add_exp = user_exp_per * times
    if add_exp <= 0:
        return 0
    try:
        import inventory_service
        inventory_service.InventoryService.grant_item(ctx_or_db, uid, 12, add_exp, source="battle")
    except Exception as e:
        if hasattr(ctx_or_db, "log"):
            ctx_or_db.log(f"[_update_player_battle_growth ERROR] {e}", "WARN")
    return add_exp


# ---- 结算帧构造（schema 正规编码，替代手搓字节） ----

def build_battle_result(bid, result, dest, clear_times=1, drops=None, heroes=None,
                        use_seconds=0, star_ids=(1, 2, 3), star_list_pb=None):
    """battle_result_net_rec dict（codec 编码用）。

    result 语义（battlecalllua.lua GotoTeam 打印原文）：0 进行中/1 胜利/2 失败/3 主动退出。
    失败(2)/退出(3)：star_list 与 all_drop_list 为空、clear_times 回显当前进度——客户端按
    result-1 路由到失败/退出结算页（isSuccess(result-1)=false 不加英雄经验）。
    """
    drops = drops or []
    heroes = heroes or []
    win = (int(result or 0) == 1)
    
    drop_list_out = []
    if win and drops:
        for i, d in enumerate(drops):
            if isinstance(d, dict):
                gains = d.get("gain_list") or []
                extras = d.get("extra_list") or []
            else:
                gains = d or []
                extras = []
            drop_list_out.append({
                "battle_times": i + 1,
                "gain_list": [{"id": int(g[0]), "num": int(g[1])} for g in gains],
                "extra_list": [{"id": int(e[0]), "num": int(e[1])} for e in extras]
            })

    if not win:
        final_stars = []
    elif star_list_pb is not None:
        final_stars = star_list_pb
    else:
        final_stars = [{"star_id": s, "now_progress": 1, "need_progress": 1, "is_achieve": 1}
                       for s in star_ids]

    return {
        "battle_id": int(bid or 0),
        "result": int(result or 0),
        "dest": int(dest or 0),
        "timestamp": int(time.time()),
        "clear_times": int(clear_times if win else 0),
        "target_times": int(clear_times if win else 1),
        "star_list": final_stars,
        "use_seconds": int(use_seconds or 0),
        "all_drop_list": drop_list_out,
        "hero_id_list": [int(h) for h in heroes],
    }


def _settle_frames(ctx, uid, bid, result, dest, times=1, heroes=None, sweep=False, battle_info=None, stage_type=0, activity_id=0):
    """结算引擎：掉落计算 + 首通/重复奖励区分 + 体力扣减 + 熟练度/好感/经验成长 + 进度推进 → 返回 (battle_result dict, 附加帧列表)。"""
    import random as _rs
    heroes = heroes or []
    times = max(1, int(times or 1))
    extra_frames = []
    if not hasattr(ctx, "touched_items") or ctx.touched_items is None:
        ctx.touched_items = set()
    drops, granted = [], {}
    new_equips = []
    win = (int(result or 0) == 1)
    battle_info = battle_info or {}

    dest_int = int(dest or 0)
    is_mythic_final = (stage_type == 35) or (3028001 <= dest_int <= 3028041) or (dest_int in (1, 2) and (1 <= int(activity_id or 0) <= 30))
    is_mythic_normal = (stage_type == 11) or (3026001 <= dest_int <= 3027039)
    is_boss_normal = (stage_type == 10) or (3010101 <= dest_int <= 3012015)
    is_boss_advance = (stage_type == 100) or (3012201 <= dest_int <= 3012399)
    is_equip_seizure = (stage_type == 53) or (3070101 <= dest_int <= 3070126)
    is_core_verification = (stage_type == 67) or (3090001 <= dest_int <= 3090999) or (10101 <= dest_int <= 16208)
    is_core_verification_cl = (stage_type in (88, 90)) or (3092001 <= dest_int <= 3093099)
    is_equip_stage = (2020000 <= dest_int <= 2020099)
    is_polyhedron = (stage_type == 52) or (3035000 <= dest_int <= 3036000) or (dest_int in (5001, 5002, 5003))
    is_rogue_team = (stage_type == 78) or (4100000 <= dest_int <= 4199999) or (100 <= dest_int <= 999)
    is_warchess = (stage_type == 15) or (4040000 <= dest_int <= 4050000)

    star_achieved, star_list_pb = evaluate_three_star(dest, win, battle_info, heroes)

    if win:
        first_clear = False if sweep else ((_stage_progress(ctx.db, dest) == 0) if dest else False)

        # 1. 扣减体力（Fatigue ID 4）：stamina_need * times
        stamina_per = _stage_stamina_cost(ctx.db, dest) if dest else 0
        total_stamina = stamina_per * times
        if total_stamina > 0 and ctx.db is not None:
            try:
                import fatigue_service as _fs
                _fs.on_fatigue_consume(ctx.db, uid, total_stamina)
            except Exception:
                try:
                    ctx.db.execute("UPDATE currency SET num = MAX(0, num - ?) WHERE uid=? AND id=4",
                                   (total_stamina, uid))
                except Exception:
                    pass
            if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                ctx.touched_items.add(4)

        # 广播事件总线：通关事件、首通事件、体力消耗事件与怪物击杀事件
        try:
            from event_bus import bus, Events
            st_type = "black_zone" if (is_mythic_final or is_mythic_normal) else ("polyhedron" if is_polyhedron else None)
            # 连携技能（协作技能网 p73）结算上下文：出战上报的本场连携 id + 关卡类别
            _combo_id = 0
            _mimir_id, _mimir_chips = 0, ""
            try:
                _b_sess = get_battle_session(bid) or {}
                _combo_id = int(_b_sess.get("cooperate_skill") or 0)
                _mi = _b_sess.get("mimir_info") or {}
                _mimir_id = int(_mi.get("mimir_id") or 0)
                _mimir_chips = ",".join(str(x) for x in (_mi.get("chip_list") or []) if x)
            except Exception:
                _combo_id, _mimir_id, _mimir_chips = 0, 0, ""
            _combo_kind = ("dream_boss" if (is_boss_normal or is_boss_advance)
                           else ("mythic_deep" if is_mythic_final else "normal"))
            # stage_kind：完整副本类型枚举（订阅者按需提取；与旧 stage_type 字符串并存，向后兼容）
            if is_mythic_final:
                _stage_kind = "black_zone_final"
            elif is_mythic_normal:
                _stage_kind = "black_zone_normal"
            elif is_boss_normal:
                _stage_kind = "dream_normal"
            elif is_boss_advance:
                _stage_kind = "dream_advance"
            elif is_equip_seizure:
                _stage_kind = "equip_seizure"
            elif is_equip_stage:
                _stage_kind = "equip_stage"
            elif is_polyhedron:
                _stage_kind = "polyhedron"
            elif is_rogue_team:
                _stage_kind = "rogue_team"
            elif is_core_verification:
                _stage_kind = "core_verification"
            elif is_core_verification_cl:
                _stage_kind = "core_verification_cl"
            elif is_warchess:
                _stage_kind = "warchess"
            else:
                _stage_kind = "normal"
            bus.emit(Events.STAGE_PASS, ctx, uid, times=times, stage_id=dest_int, stage_type=st_type,
                     win_stars=star_achieved, heroes=heroes,
                     cooperate_skill=_combo_id, combo_stage_kind=_combo_kind,
                     stage_kind=_stage_kind, stage_type_id=int(stage_type or 0), sweep=bool(sweep),
                     battle_id=bid, mimir_id=_mimir_id, mimir_chips=_mimir_chips,
                     b_info=battle_info)
            if first_clear:
                bus.emit(Events.STAGE_FIRST_CLEAR, ctx, uid, stage_id=dest_int, stage_type=st_type,
                         win_stars=star_achieved, b_info=battle_info)
            if total_stamina > 0:
                bus.emit(Events.STAMINA_COST, ctx, uid, amount=total_stamina)

            # 怪物击杀广播
            m_type = 2 if (is_boss_normal or is_boss_advance or (3010101 <= dest_int <= 3012399)) else 0
            m_id = 5024 if m_type == 2 else 0
            if dest_int > 0:
                bus.emit(Events.MONSTER_KILL, ctx, uid, monster_id=m_id or 101, count=1*times, race=7, monster_type=m_type)
        except Exception:
            pass

        # 扣减战斗复活消耗金币（Currency ID 2）：resurrect_times * 100
        res_times = int(battle_info.get("resurrect_times", 0))
        if res_times > 0 and ctx.db is not None:
            try:
                ctx.db.execute("UPDATE currency SET num = MAX(0, num - ?) WHERE uid=? AND id=2",
                               (res_times * 100, uid))
            except Exception:
                pass

        # 刻印副本专属上下文判定与提取
        equip_diff = dest_int % 10 if is_equip_stage else 0
        suit_id = 1
        cur_insure = 0
        INSURE_THRESHOLD = {5: 5, 4: 7, 3: 9, 2: 0, 1: 0}
        max_insure = INSURE_THRESHOLD.get(equip_diff, 0)
        EQUIP_PACK_MAP = {35105: 5, 35104: 5, 35103: 5, 35102: 4, 35101: 4, 35004: 4, 35003: 3, 35002: 2}

        if is_equip_stage and ctx.db is not None:
            try:
                rows = ctx.db.query("SELECT suit_id, insure_times FROM battle_equip WHERE uid=? AND stage_id=?", (uid, dest_int))
                if not rows:
                    rows = ctx.db.query("SELECT suit_id, insure_times FROM battle_equip WHERE uid=?", (uid,))
                if rows:
                    if rows[0].get("suit_id"):
                        suit_id = int(rows[0]["suit_id"])
                    cur_insure = int(rows[0].get("insure_times") or 0)
            except Exception:
                pass

        # 2. 掉落与奖励计算（严格区分初次首通与重复奖励）
        for t in range(times):
            one = stage_drop(dest, _rs) if dest else None
            if not one:
                one = stage_first_reward_db(ctx.db, dest) if dest else None
            if not one:
                one = []
            
            # 刻印副本保底检测：若达到保底次数且本次掉落中没有 5 星包，则强制追加 5 星包
            if is_equip_stage and max_insure > 0:
                has_five_star_pack = any(int(itm[0]) in (35103, 35104, 35105) for itm in one)
                if not has_five_star_pack and (cur_insure + 1 >= max_insure):
                    one.append([35105, 1])

            # 刻印副本装备包动态实例化（联动 ops_common 中的自动分解配置）
            auto_decomp = False
            if ctx.db is not None:
                try:
                    ad_rows = ctx.db.query("SELECT value_json FROM ops_common WHERE uid=? AND kind='auto_decompose' AND item_id='1'", (uid,))
                    if ad_rows:
                        import json as _jad
                        v = _jad.loads(ad_rows[0].get("value_json") or "{}")
                        auto_decomp = (int(v.get("sign") or 0) == 1)
                except Exception:
                    auto_decomp = False

            got_five_star = False
            converted_one = []
            for itm in one:
                iid, inum = int(itm[0]), int(itm[1])
                if is_equip_stage and iid in EQUIP_PACK_MAP:
                    star = EQUIP_PACK_MAP[iid]
                    if star == 5:
                        got_five_star = True
                    for _ in range(inum):
                        if auto_decomp and star < 5:
                            if star == 4:
                                converted_one.append([40802, 1])
                            elif star == 3:
                                converted_one.append([40801, 1])
                            else:
                                converted_one.append([1, 500])
                        else:
                            pos = _rs.randint(1, 6)
                            prefab_id = star * 100000 + pos * 10000 + suit_id
                            converted_one.append([prefab_id, 1])
                            if ctx.db is not None:
                                try:
                                    import inventory_service
                                    eq_summary = inventory_service.grant_item(ctx, uid, prefab_id, 1, source="battle")
                                    if eq_summary and eq_summary.get("equips"):
                                        for eq in eq_summary["equips"]:
                                            new_equips.append({
                                                "equip_id": eq["id"],
                                                "prefab_id": eq["prefab_id"],
                                                "num": 1,
                                                "enchant_slot_list": [],
                                                "race": 0,
                                            })
                                except Exception:
                                    try:
                                        max_row = ctx.db.query("SELECT MAX(id) as max_id FROM equip WHERE uid=?", (uid,))
                                        new_eid = int((max_row[0]["max_id"] or 0) + 1) if max_row else 1
                                        ctx.db.execute(
                                            "INSERT INTO equip (uid, id, prefab_id, exp, hero_id, is_lock, now_break_level, "
                                            "enchant_slots, race, race_preview, race_hero, race_faction, update_ts, is_init) "
                                            "VALUES (?, ?, ?, 0, 0, 0, 0, '[]', 0, 0, 0, 0, ?, 0)",
                                            (uid, new_eid, prefab_id, int(time.time()))
                                        )
                                        new_equips.append({
                                            "equip_id": new_eid,
                                            "prefab_id": prefab_id,
                                            "num": 1,
                                            "enchant_slot_list": [],
                                            "race": 0,
                                        })
                                        import event_bus as _eb
                                        _eb.bus.emit(_eb.Events.EQUIP_OBTAIN, ctx, uid, suit_id=suit_id, pos=pos, prefab_id=prefab_id, count=1)
                                    except Exception:
                                        pass

                else:
                    converted_one.append([iid, inum])
            one = converted_one

            # 首通差量奖励（仅第 1 倍首通时有效）
            extra_one = []
            if t == 0 and first_clear:
                first_rewards = chapter_bonus(dest, True)
                if first_rewards:
                    extra_one = [list(x) for x in first_rewards]
                else:
                    db_first = stage_first_reward_db(ctx.db, dest)
                    if db_first:
                        extra_one = [list(x) for x in db_first]
            elif not first_clear:
                second_rewards = chapter_bonus(dest, False)
                if second_rewards:
                    one = [list(x) for x in second_rewards] + one

            # 合并相同道具
            merged_gain = {}
            for itm in one:
                iid, inum = int(itm[0]), int(itm[1])
                merged_gain[iid] = merged_gain.get(iid, 0) + inum
            gain_list_out = [[k, v] for k, v in merged_gain.items()]

            merged_extra = {}
            for itm in extra_one:
                iid, inum = int(itm[0]), int(itm[1])
                merged_extra[iid] = merged_extra.get(iid, 0) + inum
            extra_list_out = [[k, v] for k, v in merged_extra.items()]

            # 汇总进 drop_list
            drops.append({
                "gain_list": gain_list_out,
                "extra_list": extra_list_out
            })
            for iid, num in gain_list_out + extra_list_out:
                if int(iid) < 100000:
                    granted[int(iid)] = granted.get(int(iid), 0) + int(num)

        # 3. 奖励入库（材料 & 货币）
        if ctx.db is not None:
            for iid, num in granted.items():
                _item_route_add(ctx, uid, iid, num)
                if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                    ctx.touched_items.add(int(iid))

            # 刻印保底次数写库
            if is_equip_stage:
                try:
                    ctx.db.execute(
                        "UPDATE battle_equip SET insure_times=? WHERE uid=? AND (stage_id % 10)=?",
                        (cur_insure, uid, equip_diff)
                    )
                except Exception:
                    pass

        # 4. 关卡进度推进（按实际三星评估入库），结算帧 clear_times 精准对齐本次作战倍数 times
        if dest and not sweep and ctx.db is not None:
            if not is_mythic_final and not is_boss_normal and not is_boss_advance and not is_equip_seizure and not is_polyhedron:
                _advance_stage(ctx.db, uid, dest, star_achieved=star_achieved)
            
            # 黑区净化关卡进度专属处理
            if is_mythic_normal:
                if 3026001 <= dest_int <= 3026013:
                    p_id = dest_int - 3026000
                    st_count = len(star_achieved) if star_achieved else 3
                    if hasattr(ctx.db, "record_mythic_partition_clear"):
                        ctx.db.record_mythic_partition_clear(uid, p_id, star=st_count)
                elif 3027001 <= dest_int <= 3027039:
                    sub_idx = dest_int - 3027001
                    d_idx = 1 + sub_idx // 3
                    s_idx = 1 + sub_idx % 3
                    p_id = d_idx * 100 + s_idx
                    if hasattr(ctx.db, "record_mythic_partition_clear"):
                        ctx.db.record_mythic_partition_clear(uid, p_id, star=3)
            elif is_mythic_final:
                # 失序深区 / 终焉难度 (1~30 档，支持单队与双队连续接力)
                raw_btime = int(battle_info.get("battle_time") or 45000)
                u_sec = int(round(raw_btime / 1000.0)) if raw_btime > 1000 else max(1, raw_btime)
                team_id = dest_int if dest_int in (1, 2) else (1 if 3028001 <= dest_int <= 3028019 else (1 + (dest_int - 3028020) % 2))
                diff = int(activity_id) if (1 <= int(activity_id or 0) <= 30) else (dest_int - 3028000 if 3028001 <= dest_int <= 3028019 else (20 + (dest_int - 3028020) // 2 if 3028020 <= dest_int <= 3028041 else 30))
                if hasattr(ctx.db, "record_mythic_final_clear"):
                    ctx.db.record_mythic_final_clear(uid, diff=diff, team_id=team_id, use_time=u_sec)
                elif hasattr(ctx.db, "record_mythic_final_stage_clear"):
                    ctx.db.record_mythic_final_stage_clear(uid, dest_int, use_time=u_sec)
            elif is_boss_normal:
                # 梦境再构·普通梦境通关处理
                import weekly_challenge_service
                cat = weekly_challenge_service.get_boss_catalog()
                stage_groups = cat.get("stage_groups", {})
                matched_gid = 5024 # 默认兜底
                for gid_str, g_obj in stage_groups.items():
                    if dest_int in g_obj.get("stages", []):
                        matched_gid = int(gid_str)
                        break
                if hasattr(ctx.db, "record_boss_challenge_normal_clear"):
                    ctx.db.record_boss_challenge_normal_clear(uid, matched_gid, dest_int, star_list=star_list_pb, heroes=heroes)
            elif is_boss_advance:
                # 梦境再构·进阶/扭曲梦境算分与通关处理
                import weekly_challenge_service
                cat = weekly_challenge_service.get_boss_catalog()
                adv_pool = cat.get("advance_pool") or cat.get("advance_boss_pool", {})
                
                # 获取当前玩家正在挑战的首领列表，精准匹配当前轮换的首领 pool_id
                _, _, sc_101 = weekly_challenge_service.get_boss_challenge_data(uid, ctx.db)
                cur_boss_ids = [b["id"] for b in sc_101.get("boss_list", [])]
                matched_pid = None
                for bid in cur_boss_ids:
                    p_obj = adv_pool.get(str(bid)) or adv_pool.get(bid, {})
                    if int(p_obj.get("stage_id", 0)) == dest_int or int(p_obj.get("boss_id", 0)) == int(activity_id or 0):
                        matched_pid = bid
                        break
                if matched_pid is None:
                    for pid_str, p_obj in adv_pool.items():
                        if int(p_obj.get("stage_id", 0)) == dest_int:
                            if int(pid_str) in cur_boss_ids:
                                matched_pid = int(pid_str)
                                break
                if matched_pid is None:
                    for pid_str, p_obj in adv_pool.items():
                        if int(p_obj.get("stage_id", 0)) == dest_int:
                            matched_pid = int(pid_str)
                            break
                if matched_pid is None and cur_boss_ids:
                    matched_pid = cur_boss_ids[0]
                matched_pid = matched_pid or 20001
                
                # 读取该首领自选词缀与难度
                aff_conf = {"diff_idx": 1, "time_list": [], "affix_list": []}
                try:
                    ar = ctx.db.query("SELECT * FROM boss_challenge_affixes WHERE uid=? AND boss_id=?", (uid, matched_pid))
                    if ar:
                        aff_conf["diff_idx"] = int(ar[0].get("diffculty_index") or 1)
                        aff_conf["time_list"] = json.loads(ar[0].get("time_index_list") or "[]")
                        aff_conf["affix_list"] = json.loads(ar[0].get("affix_index_list") or "[]")
                except Exception:
                    pass
                earned_score = weekly_challenge_service.calc_boss_advance_score(
                    aff_conf["diff_idx"], aff_conf["time_list"], aff_conf["affix_list"]
                )
                if hasattr(ctx.db, "record_boss_challenge_advance_clear"):
                    ctx.db.record_boss_challenge_advance_clear(uid, matched_pid, earned_score, heroes=heroes)
                ctx.log(f"[settle] 进阶梦境通关记录: boss_id={matched_pid} diff={aff_conf['diff_idx']} score={earned_score} heroes={heroes}")
            elif is_core_verification:
                # 迭代校验常规关卡结算
                import weekly_challenge_service as _wcs
                cur_cycle = _wcs.get_core_verification_cycle()
                raw_btime = int(battle_info.get("battle_time") or 30000)
                
                cat = _wcs.get_core_verification_catalog()
                cycle_info = cat.get("cycle_info", {})
                
                matched_info_id = dest_int
                b_type = 1
                diff = 1
                for iid_str, info in cycle_info.items():
                    if int(info.get("stage_id", 0)) == dest_int or int(iid_str) == dest_int:
                        matched_info_id = int(iid_str)
                        b_type = int(info.get("boss_type", 1))
                        diff = int(info.get("difficult", 1))
                        break
                
                stage_score = 0
                if diff == 8:
                    affixes = []
                    if ctx.db and hasattr(ctx.db, "get_core_verification_affixes"):
                        user_affs = ctx.db.get_core_verification_affixes(uid)
                        for ua in user_affs:
                            if ua.get("id") == b_type:
                                affixes = ua.get("affix_list", [])
                    stage_score = _wcs.calc_core_verification_super_score(matched_info_id, diff=8, battle_time_ms=raw_btime, affix_list=affixes)
                
                if hasattr(ctx.db, "record_core_verification_stage_clear"):
                    ctx.db.record_core_verification_stage_clear(
                        uid, cur_cycle, matched_info_id, b_type, diff, raw_btime, score=stage_score, heroes=heroes
                    )
                ctx.log(f"[settle] 迭代校验通关: cycle={cur_cycle} info_id={matched_info_id} boss={b_type} diff={diff} score={stage_score} heroes={heroes}")

            elif is_core_verification_cl:
                # 迭代校验·挑战模式关卡结算
                import weekly_challenge_service as _wcs
                cl_cat = _wcs.get_core_verification_cl_catalog()
                raw_btime = int(battle_info.get("battle_time") or 30000)
                
                target_act = int(activity_id or 3539501)
                mode_num = 1
                stg_cfg = {}
                for mkey in ["mode1", "mode2", "mode3", "mode4"]:
                    m_stgs = cl_cat.get(mkey, {})
                    if str(dest_int) in m_stgs:
                        stg_cfg = m_stgs[str(dest_int)]
                        mode_num = int(stg_cfg.get("mode", 1))
                        target_act = int(stg_cfg.get("activity_id", target_act))
                        break
                
                stg_type = int(stg_cfg.get("stage_type", 1))
                cl_score = 0
                if stg_type == 2:
                    time_sec = raw_btime / 1000.0 if raw_btime > 1000 else float(raw_btime)
                    time_bonus = max(0, int(3000 - time_sec * 20))
                    cl_score = 10000 + time_bonus
                
                if hasattr(ctx.db, "record_core_verification_cl_stage_clear"):
                    ctx.db.record_core_verification_cl_stage_clear(
                        uid, target_act, mode_num, dest_int, min_time_ms=raw_btime, score=cl_score, heroes=heroes
                    )
                ctx.log(f"[settle] 迭代校验挑战模式通关: act={target_act} mode={mode_num} stage={dest_int} score={cl_score} heroes={heroes}")

        ct = max(times, 1)

        # 5. 出战角色成长：熟练度/胜场/好感档案/角色经验 —— 已整体迁移至 hero_service
        #    （订阅上方 STAGE_PASS 广播统一驱动，单一数据路径；_update_hero_battle_growth 保留备回滚）

        # 6. 玩家经验累加与升级
        if ctx.db is not None:
            _update_player_battle_growth(ctx, uid, dest=dest, times=times, stamina_cost=stamina_per)

    else:
        # 战斗失败 (2) 或 作战中断 (3)：不扣体力、不发奖励、不推进度、不加成长
        ct = 0
        drops = []
        try:
            from event_bus import bus, Events
            if int(result or 0) == 2:
                bus.emit(Events.STAGE_FAIL, ctx, uid, stage_id=dest_int, heroes=heroes)
            else:
                bus.emit(Events.STAGE_QUIT, ctx, uid, stage_id=dest_int)
        except Exception:
            pass

    # 构造 battle_result 报文结构
    use_sec = int(battle_info.get("battle_time") or 0)
    if use_sec > 1000:
        use_sec = int(round(use_sec / 1000.0))
    br = build_battle_result(bid, result, dest, clear_times=ct, drops=drops, heroes=heroes,
                             use_seconds=use_sec, star_list_pb=star_list_pb)

    # 结算联动推送（仅胜利有数据变动时推送）：
    if win:
        if ctx.generator is not None:
            # 1. 资产原子差量帧 sc_17023（含经验 12、体力 4、掉落物与新掉落装备），驱动客户端 EXPChange 触发等级提升与结算升级弹窗
            if (hasattr(ctx, "touched_items") and ctx.touched_items) or new_equips:
                try:
                    p17023 = ctx.generator.gen_payload(17023, uid=uid, db=ctx.db, touched_items=getattr(ctx, "touched_items", set()), equip_list=(new_equips or None))
                    if p17023:
                        extra_frames.append(DownFrame(17023, p17023))
                except Exception:
                    pass
                if hasattr(ctx, "touched_items") and 4 in ctx.touched_items:
                    try:
                        p17025 = ctx.generator.gen_payload(17025, uid=uid, db=ctx.db)
                        if p17025:
                            extra_frames.append(DownFrame(17025, p17025))
                    except Exception:
                        pass

            if dest and dest < 2000000 and not is_mythic_final and not is_boss_normal and not is_boss_advance and not is_core_verification and not is_core_verification_cl and not is_polyhedron:
                # 24009 章节进度（仅主线/剧情关卡推送）
                try:
                    p = ctx.generator.gen_payload(24009, uid=uid, db=ctx.db)
                    if p:
                        extra_frames.append(DownFrame(24009, p))
                except Exception:
                    pass

            # 15009 货币全量（含扣减后最新体力） + 25009 关卡进度 + 43001 刻印保底同步
            # 彻底剔除 17009(材料全量, 1.5KB) 与 13009(刻印全量, 13.4KB)，杜绝大包重排刷新与二次推送告警
            push_cmds = [15009]
            if not is_mythic_final and not is_boss_normal and not is_boss_advance and not is_core_verification and not is_core_verification_cl and not is_polyhedron:
                push_cmds.append(25009)
            if is_equip_stage:
                push_cmds.append(43001)
            for _pc in push_cmds:
                try:
                    _pp = ctx.generator.gen_payload(_pc, uid=uid, db=ctx.db)
                    if _pp:
                        extra_frames.append(DownFrame(_pc, _pp))
                except Exception:
                    pass

        # 多维变量局内战斗胜利推进与全量状态推送 (sc_18001)
        if is_polyhedron and ctx.db is not None:
            try:
                from polyhedron_service import PolyhedronRunManager
                run_data = PolyhedronRunManager.on_battle_finish(uid, ctx.db, is_win=win, damage_info=battle_info)
                if run_data:
                    import generator as _gen
                    p18001 = _gen.gen_payload(18001, uid=uid, db=ctx.db)
                    if p18001:
                        extra_frames.append(DownFrame(18001, p18001))
            except Exception as _pye:
                ctx.log(f"[settle] 多维变量局内结算异常: {_pye}", "WARN")

        # 虚构推演局内战斗胜利推进与战后掉落面板/全量状态推送 (sc_88033 + sc_88001 + sc_88013)
        if is_rogue_team and ctx.db is not None:
            try:
                from rogueteam_service import RogueTeamService
                effective_uid = uid or 2174928301
                svc = RogueTeamService.get_instance(db=ctx.db)
                sess = get_battle_session(bid) or {}
                target_node_id = sess.get("node_id") or 0
                if not target_node_id and (100 <= dest_int <= 999):
                    target_node_id = dest_int
                if not target_node_id:
                    session = svc.get_session_data(effective_uid)
                    target_node_id = session.get("select_node_id", 0) if session else 0
                
                _, drop_stat, popup_event = svc.settle_battle_node(effective_uid, target_node_id, win=win)
                ctx.log(f"[settle] 虚构推演战斗节点结算: node_id={target_node_id} dest={dest_int} win={win} drop={drop_stat}")
                
                if ctx.codec_encode:
                    # 1. 战后掉落统计帧 (sc_88033: treasure_num, relic_num, coin_num) - 防止 BattleRogueTeamResultDropPanel 空指针崩溃
                    p_88033 = ctx.codec_encode("sc_88033", drop_stat or {"treasure_num": 1, "relic_num": 0, "coin_num": 40})
                    if p_88033:
                        extra_frames.append(DownFrame(88033, p_88033))

                    # 1b. 战后三选一弹窗 (sc_88015) - 注意顺序：必须在 sc_88001 之前推。
                    #     客户端回到地图后 PopOperateWindowProcessSystem 才消费 OPERATE_POP_WINDOW 触发，
                    #     sc_88015 的 UpdatePopWindow() 先把 unOperateData 与触发队列填好，
                    #     若漏发 -> 面板有数字但背包/货币永远不动（B 报告第 4 条）。
                    if popup_event and popup_event.get("param_list") and ctx.codec_encode:
                        p_88015b = ctx.codec_encode("sc_88015", {"other_info": popup_event})
                        if p_88015b:
                            extra_frames.append(DownFrame(88015, p_88015b))
                            ctx.log(f"[settle] 虚构推演战后三选一 sc_88015: type={popup_event.get('event_type')} n={len(popup_event.get('param_list'))}")
                    
                    # 2. 全量状态与下一轮地图前沿帧 (sc_88001)
                    session_data = svc.get_session_data(effective_uid)
                    if session_data:
                        p_88001 = ctx.codec_encode("sc_88001", session_data)
                        if p_88001:
                            extra_frames.append(DownFrame(88001, p_88001))
                        # 3. 属性更新 (sc_88013)
                        p_88013 = ctx.codec_encode("sc_88013", {"attr_list": session_data.get("attr_list", [])})
                        if p_88013:
                            extra_frames.append(DownFrame(88013, p_88013))
                        # 4. 若无战后弹窗且本层已通关，下发切层信号 (sc_88221)
                        if (not popup_event or not popup_event.get("param_list")) and svc._is_floor_clear(session_data.get("map_info", [])):
                            p_88221 = ctx.codec_encode("sc_88221", {"sign": 1})
                            if p_88221:
                                extra_frames.append(DownFrame(88221, p_88221))
                                ctx.log("[settle] 虚构推演本层已通关，下发 sc_88221 触发切层动画")
            except Exception as _re:
                ctx.log(f"[settle] 虚构推演战斗结算异常: {_re}", "WARN")

        # 灰烬牛仔 (列车大劫案) 战后关卡进度与同调率实时推送 (sc_68183)
        if dest_int in (5280301, 5280302, 5280304, 5280306, 5280311, 5280312, 5280313, 5280316, 5280322, 5280324, 5280325, 5280326, 5280327):
            try:
                from minigame_service import MiniGameService
                ash_score = int(battle_info.get("score") or 8000)
                ash_res = MiniGameService.get_instance(ctx.db).settle_ash_stage(
                    ctx, uid, dest_int, point=ash_score, win=win
                )
                if ash_res and ash_res.get("p68183"):
                    extra_frames.append(DownFrame(68183, ash_res["p68183"]))
                    ctx.log(f"[settle] 灰烬牛仔关卡结算: real={dest_int} ash_id={ash_res.get('ash_stage_id')} score={ash_res.get('point')}")
            except Exception as _ae:
                ctx.log(f"[settle] 灰烬牛仔结算异常: {_ae}", "WARN")

        # 奥西里斯战术研习 (Activity 323801) 战后关卡与章节推送 (sc_68177 / sc_68175)
        if 5260111 <= dest_int <= 5260164:
            try:
                from minigame_service import MiniGameService
                osiris_score = int(battle_info.get("score") or battle_info.get("point") or 10000)
                osiris_use_time = int(battle_info.get("battle_time") or 60)
                osi_res = MiniGameService.get_instance(ctx.db).settle_osiris_stage(
                    ctx, uid, dest_int, pass_time=osiris_use_time, point=osiris_score, win=win
                )
                if osi_res:
                    for f in osi_res.get("frames", []):
                        extra_frames.append(f)
                    ctx.log(f"[settle] 奥西里斯战术研习关卡结算: stage={dest_int} time={osiris_use_time} point={osiris_score}")
            except Exception as _oe:
                ctx.log(f"[settle] 奥西里斯战术研习结算异常: {_oe}", "WARN")

        # 斯克尔德心象 (Activity 321201) 战后关卡结算 (5240101 <= dest_int <= 5240130)
        if 5240101 <= dest_int <= 5240130:
            try:
                from minigame_service import MiniGameService
                skuld_res = MiniGameService.get_instance(ctx.db).settle_skuld_stage(
                    ctx, uid, dest_int, win=win
                )
                if skuld_res and skuld_res.get("first_rewards"):
                    for item_id, count in skuld_res["first_rewards"]:
                        res_rewards.append((item_id, count))
                    ctx.log(f"[settle] 斯克尔德心象关卡结算: dest={dest_int} 首通发奖={skuld_res['first_rewards']}")
            except Exception as _se:
                ctx.log(f"[settle] 斯克尔德心象结算异常: {_se}", "WARN")

        # 霍德尔活动 (Activity 3941101) 战后关卡结算 (5310201 <= dest_int <= 5310222)
        if 5310201 <= dest_int <= 5310222:
            try:
                from minigame_service import MiniGameService
                hodur_res = MiniGameService.get_instance(ctx.db).settle_hodur_stage(
                    ctx, uid, dest_int, win=win
                )
                if hodur_res:
                    ctx.log(f"[settle] 霍德尔关卡结算: dest={dest_int} chap={hodur_res['chapter_id']} first={hodur_res['is_first']}")
            except Exception as _he:
                ctx.log(f"[settle] 霍德尔关卡结算异常: {_he}", "WARN")

        # 薇儿丹蒂 SP 潜质觉醒 (Activity 242841/242851) 战后关卡结算 (5170201 <= dest_int <= 5170222)
        if 5170201 <= dest_int <= 5170222:
            try:
                from minigame_service import MiniGameService
                sphero_score = 0
                if battle_info and isinstance(battle_info, dict):
                    sphero_score = int(battle_info.get("score") or battle_info.get("battle_score") or 0)
                sphero_res = MiniGameService.get_instance(ctx.db).settle_sphero_stage(
                    ctx, uid, dest_int, battle_score=sphero_score, win=win
                )
                if sphero_res:
                    for f in sphero_res.get("frames", []):
                        extra_frames.append(f)
                    ctx.log(f"[settle] 薇儿丹蒂 SP 关卡结算: dest={dest_int} type={sphero_res.get('type')}")
            except Exception as _spe:
                ctx.log(f"[settle] 薇儿丹蒂 SP 关卡结算异常: {_spe}", "WARN")

        # 因果观测 (WarChess) 战后圣物/神格三选一推送 (sc_49011)
        if is_warchess:
            try:
                import random as _rs
                sample_artifacts = _rs.sample([1001, 1002, 1003, 1004, 1005, 1006, 1007, 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 3001, 3002, 3003, 3004, 3005], 3)
                p_49011 = ctx.codec_encode("sc_49011", {
                    "tag": 0,
                    "pos": {"x": 0.0, "z": 0.0},
                    "event_id": 10202,
                    "attribute": sample_artifacts
                })
                if p_49011:
                    extra_frames.append(DownFrame(49011, p_49011))
                    ctx.log(f"[settle] 因果观测战后下发三选一神格 (sc_49011): {sample_artifacts}")
            except Exception as _wce:
                ctx.log(f"[settle] 因果观测 sc_49011 组包异常: {_wce}", "WARN")

        # 黑区净化状态实时推送 (44009 / 44023 / 44039)
        if (is_mythic_normal or is_mythic_final) and ctx.codec_encode and ctx.db:
            try:
                import weekly_challenge_service
                _, sc_09, _, _, sc_23 = weekly_challenge_service.get_mythic_data(uid, ctx.db)
                if is_mythic_normal:
                    p09 = ctx.codec_encode("sc_44009", sc_09)
                    if p09: extra_frames.append(DownFrame(44009, p09))
                elif is_mythic_final:
                    p23 = ctx.codec_encode("sc_44023", sc_23)
                    if p23: extra_frames.append(DownFrame(44023, p23))
                    
                    # 44039: 实时推送当前战斗积分（基于最高通关档位与剩余时间加成）
                    ch_info = sc_23.get("challenge_info") or []
                    tot_use = sum(int(x.get("use_time") or 0) for x in ch_info if x.get("clear_state") == 1)
                    cur_diff = sc_23.get("now_difficulty") or 30
                    final_score = int(cur_diff * 600 + max(0, 300 - tot_use) * 10)
                    p39 = ctx.codec_encode("sc_44039", {"score": final_score})
                    if p39: extra_frames.append(DownFrame(44039, p39))
            except Exception as _me:
                ctx.log(f"[settle] 黑区净化联动推送异常: {_me}", "WARN")

        # 梦境再构状态实时推送 (45001 / 45003 / 45101)
        if (is_boss_normal or is_boss_advance) and ctx.codec_encode and ctx.db:
            try:
                import weekly_challenge_service
                _, sc_001, sc_101 = weekly_challenge_service.get_boss_challenge_data(uid, ctx.db)
                if is_boss_normal:
                    p001 = ctx.codec_encode("sc_45001", sc_001)
                    if p001: extra_frames.append(DownFrame(45001, p001))
                    p003 = ctx.codec_encode("sc_45003", {"use_times": sc_001.get("use_times", 0)})
                    if p003: extra_frames.append(DownFrame(45003, p003))
                elif is_boss_advance:
                    p101 = ctx.codec_encode("sc_45101", sc_101)
                    if p101: extra_frames.append(DownFrame(45101, p101))
            except Exception as _be:
                ctx.log(f"[settle] 梦境再构联动推送异常: {_be}", "WARN")

        # 迭代校验常规状态实时推送 (75009 / 75015)
        if is_core_verification and ctx.codec_encode and ctx.db:
            try:
                import weekly_challenge_service as _wcs
                sc_75009 = _wcs.get_core_verification_data(uid, ctx.db)
                p75009 = ctx.codec_encode("sc_75009", sc_75009)
                if p75009: extra_frames.append(DownFrame(75009, p75009))
                
                max_sc_info = sc_75009.get("max_score_info") or {}
                if max_sc_info:
                    p75015 = ctx.codec_encode("sc_75015", max_sc_info)
                    if p75015: extra_frames.append(DownFrame(75015, p75015))
            except Exception as _ce:
                ctx.log(f"[settle] 迭代校验常规推送异常: {_ce}", "WARN")

        # 迭代校验挑战模式状态实时推送 (89013 / 89025 / 89431 / 89801 / 89021)
        if is_core_verification_cl and ctx.codec_encode and ctx.db:
            try:
                import weekly_challenge_service as _wcs
                m_num, sc_cl_obj, sc_ill_obj = _wcs.get_core_verification_challenge_data(uid, ctx.db, activity_id=activity_id)
                if m_num == 1:
                    p = ctx.codec_encode("sc_89013", sc_cl_obj)
                    if p: extra_frames.append(DownFrame(89013, p))
                elif m_num == 2:
                    p = ctx.codec_encode("sc_89025", sc_cl_obj)
                    if p: extra_frames.append(DownFrame(89025, p))
                elif m_num == 3:
                    p = ctx.codec_encode("sc_89431", sc_cl_obj)
                    if p: extra_frames.append(DownFrame(89431, p))
                elif m_num == 4:
                    p = ctx.codec_encode("sc_89801", sc_cl_obj)
                    if p: extra_frames.append(DownFrame(89801, p))
                
                p_ill = ctx.codec_encode("sc_89021", sc_ill_obj)
                if p_ill: extra_frames.append(DownFrame(89021, p_ill))
            except Exception as _cle:
                ctx.log(f"[settle] 迭代校验挑战模式推送异常: {_cle}", "WARN")

        if dest:
            # 52027 剧情图鉴解锁与 52011 插图解锁实时推送（当通关触发剧情时）
            try:
                _, _, _, _, _, stage_story_triggers = _load_battle_cfgs()
                story_ids = (stage_story_triggers.get(str(dest)) or stage_story_triggers.get(dest) or []) if stage_story_triggers else []
                meta = _load_illu_meta()
                s2p = meta.get("story_to_pics", {})
                new_unlocked_pics = []
                new_unlocked_plots = []
                for sid in story_ids:
                    sid_int = int(sid)
                    if ctx.db:
                        ctx.db.execute("INSERT OR REPLACE INTO story_unlock (uid, story_id, unlock_flag) VALUES (?, ?, 1)", (uid, sid_int))
                        ctx.db.execute("INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, 'plot', ?, 0)", (uid, sid_int))
                    new_unlocked_plots.append({"id": sid_int, "is_view": 0})
                    pids = s2p.get(str(sid_int), []) or s2p.get(sid_int, [])
                    for pid in pids:
                        pid_int = int(pid)
                        if ctx.db:
                            r = ctx.db.query("SELECT 1 FROM illustrated WHERE uid=? AND kind='inbetweening' AND item_id=?", (uid, pid_int))
                            if not r:
                                ctx.db.execute("INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view) VALUES (?, 'inbetweening', ?, 0, 0)", (uid, pid_int))
                                new_unlocked_pics.append({"id": pid_int, "is_receive": 0, "is_view": 0})
                        else:
                            new_unlocked_pics.append({"id": pid_int, "is_receive": 0, "is_view": 0})
                if new_unlocked_pics and ctx.codec_encode:
                    p52011 = ctx.codec_encode("sc_52011", {"inbetweening_info": new_unlocked_pics})
                    if p52011:
                        extra_frames.append(DownFrame(52011, p52011))
                if new_unlocked_plots and ctx.codec_encode:
                    p52027 = ctx.codec_encode("sc_52027", {"plot_info": new_unlocked_plots})
                    if p52027:
                        extra_frames.append(DownFrame(52027, p52027))
            except Exception as _e:
                ctx.log(f"[settle] 剧情与插图图鉴推送异常: {_e}", "WARN")

    # 介质攫取全状态积分核算（波次/击杀/时间转换，胜利/失败/退出/超时均精准生效并推送 sc_35013）
    if is_equip_seizure:
        try:
            import weekly_challenge_service as _wcs
            enemy_dead = int(battle_info.get("enemy_dead_num") or battle_info.get("target_count") or 0)
            raw_btime = int(battle_info.get("battle_time") or 0)
            explicit_score = int(battle_info.get("score") or battle_info.get("point") or 0)
            raw_score, est_waves = _wcs.calc_equip_seizure_score(
                enemy_dead=enemy_dead,
                battle_time_ms=raw_btime,
                explicit_score=explicit_score,
                is_win=win
            )
            
            rate = _wcs.get_seizure_challenge_rate()
            final_score = int(round(raw_score * rate))
            
            if hasattr(ctx.db, "save_equip_seizure_battle_score") and ctx.db is not None:
                ctx.db.save_equip_seizure_battle_score(uid, final_score, team_heroes=heroes)
            
            ctx.log(f"[settle] 介质攫取作战结算: dest={dest_int} win={win} dead={enemy_dead} est_waves={est_waves} use_sec={use_sec} raw_score={raw_score} rate={rate} final_score={final_score}")
            
            if ctx.codec_encode and ctx.db is not None:
                prog = ctx.db.get_equip_seizure_progress(uid)
                p35013 = ctx.codec_encode("sc_35013", {
                    "is_valid": True,
                    "score": final_score,
                    "today_max_score": int(prog.get("today_max_score") or 0),
                    "sum_score": int(prog.get("sum_score") or 0)
                })
                if p35013:
                    extra_frames.append(DownFrame(35013, p35013))
        except Exception as _se:
            ctx.log(f"[settle] 介质攫取得分推送异常: {_se}", "WARN")

    # 收集由 EventBus 在本次战斗结算中产生的附加帧（如 sc_28007 任务进度）
    if hasattr(ctx, "pop_pending_frames"):
        extra_frames.extend(ctx.pop_pending_frames())
    elif hasattr(ctx, "pending_frames") and ctx.pending_frames:
        extra_frames.extend(ctx.pending_frames)
        ctx.pending_frames = []

    return br, extra_frames


def h_54034(req, ctx):
    """剧情战斗入口 → sc_54035（result=0 + battle_id f2 varint）。会话登记供结算。"""
    dest = _pkt_session_id({"payload": req.payload})
    bid = _gen_battle_id(ctx.battle_seq)
    ctx.battle_stage[0] = dest
    ctx.battle_id[0] = bid
    register_battle_session(bid, req.uid, dest=dest, is_story=True)
    _p35 = b"\x08\x00" + b"\x10" + _varint(bid)
    ctx.log(f"cs_54034 -> 剧情战斗 battle_id={bid} stage={dest}（会话已登记）")
    return CSResponse([DownFrame(54035, _p35)])


def h_54030(req, ctx):
    """普通战斗入口 → sc_54031 + sc_54003（按出战英雄重写）+ sc_54007（动态 battle_id/ip/port）。

    会话登记（uid/dest/倍数/英雄）供 h_54032 结算引擎与 UDP 132 结果对账。
    自动持久化出战编队至 reserve_team 与 reserve_team_hero。
    """
    info = _parse_battle_common(req, ctx)

    # 多维变量 (stage_type == 52 或 dest 处于多维关卡区间) 且 cs_54030 中 heroes 为空时：
    # 必须从当前活跃的 polyhedron_run 中获取 fight_hero_id_list 出战英雄，供 sc_54003 组装！
    dest_num = int(info.get("dest") or 0)
    if (info.get("type") == 52 or (3035000 <= dest_num <= 3038000)) and not info["heroes"]:
        if ctx.db:
            try:
                from polyhedron_service import PolyhedronRunManager
                run_data = PolyhedronRunManager.load_run(req.uid, ctx.db)
                if run_data and run_data.get("fight_hero_id_list"):
                    info["heroes"] = [int(x) for x in run_data["fight_hero_id_list"]]
                    ctx.log(f"[battle] 多维对局出战英雄自动装载: {info['heroes']}")
            except Exception as e:
                ctx.log(f"[battle] 多维出战英雄获取异常: {e}")

    # 虚构推演 (stage_type == 78 或 dest 处于地图节点 ID 区间 100~999):
    # 自动记录选中节点，并将 dest 映射为真实关卡 ID (param)
    is_rogue_team = (info.get("type") == 78 or (info.get("type") == 0 and 100 <= dest_num <= 999))
    rogue_node_id = 0
    effective_uid = req.uid or 2174928301
    if is_rogue_team:
        try:
            from rogueteam_service import RogueTeamService
            svc = RogueTeamService.get_instance(db=ctx.db)
            is_valid, corr_node, reason = svc.is_valid_node(effective_uid, dest_num)
            if not is_valid:
                ctx.log(f"[battle] 虚构推演节点 {dest_num} 越界 ({reason})，自动纠偏至合法节点 {corr_node}", "WARN")
                dest_num = corr_node
            svc.set_selected_node(effective_uid, dest_num)
            rogue_node_id = dest_num
            session = svc.get_session_data(effective_uid)
            if session and session.get("map_info"):
                for n in session["map_info"]:
                    if n.get("node_id") == dest_num:
                        real_stg = n.get("param")
                        if real_stg and real_stg != 0:
                            ctx.log(f"[battle] 虚构推演节点 {dest_num} 映射真实关卡 ID: {real_stg}")
                            info["dest"] = int(real_stg)
                        break
        except Exception as e:
            ctx.log(f"[battle] 虚构推演战斗映射异常: {e}", "WARN")

    bid = _gen_battle_id(ctx.battle_seq)
    ctx.battle_id[0] = bid
    ctx.battle_stage[0] = info["dest"]
    ctx.hero_last_payload[0] = req.payload
    register_battle_session(bid, effective_uid, dest=info["dest"],
                            activity_id=info["activity_id"],
                            battle_times=info["battle_times"], heroes=info["heroes"],
                            stage_type=info.get("type", 0),
                            node_id=rogue_node_id,
                            cooperate_skill=info.get("cooperate_skill", 0),
                            mimir_info=info.get("mimir_info") or {})

    # 自动持久化出战编队（旧逻辑，已停用）：reserve_team 预设不再被出战数据污染，
    # 编队记录由下方 TEAM SERVER 关卡现役编队接管（sync_stage_battle_team 保留备回滚）

    # 关卡现役编队原子保存（TEAM SERVER 接管：按官方关卡键分立，试用英雄过滤）
    if ctx.db and info.get("heroes"):
        try:
            from team_server import TeamServer as _TS
            _mi = info.get("mimir_info") or {}
            _TS.get_instance().record_stage_team(
                ctx, effective_uid, info.get("type", 0), info["dest"],
                heroes=info.get("hero_details") or info["heroes"],
                cooperate_skill=info.get("cooperate_skill", 0),
                mimir_id=int(_mi.get("mimir_id") or 0),
                mimir_chips=",".join(str(x) for x in (_mi.get("chip_list") or []) if x))
        except Exception as e:
            ctx.log(f"[team] 关卡现役编队保存异常: {e}", "WARN")

    _bport = str(_BATTLE_PORT[0]).encode()
    frames = [DownFrame(54031, b"\x08\x00")]
    # 若为虚构推演，先下发 sc_88001 同步 select_node_id，确保客户端 PathGetRogueTeamMapID 100% 命中合法节点
    if is_rogue_team and ctx.codec_encode and ctx.db:
        try:
            from rogueteam_service import RogueTeamService
            svc = RogueTeamService.get_instance(db=ctx.db)
            session_data = svc.get_session_data(effective_uid)
            if session_data:
                p_88001 = ctx.codec_encode("sc_88001", session_data)
                if p_88001:
                    frames.append(DownFrame(88001, p_88001))
        except Exception as _r88:
            ctx.log(f"[battle] 虚构推演 sc_88001 预下发异常: {_r88}", "WARN")
    # 动态构建出战英雄完整养成与刻印装备帧 (sc_54003)
    _p03 = None
    if ctx.db:
        try:
            _p03 = battle_payload.build_sc_54003_dynamic(ctx.db, req.uid, info["heroes"])
        except Exception as e:
            ctx.log(f"[battle] 动态 sc_54003 构建异常: {e}")
    if not _p03:
        _p03_tpl = ctx.battle_03_payload()
        if _p03_tpl:
            _p03 = rewrite_54003_heroes(info["heroes"], _p03_tpl)
    if _p03:
        frames.append(DownFrame(54003, _p03))
    _p07 = None
    battle_ip = _BATTLE_IP[0] or "127.0.0.1"
    b_ip_bytes = battle_ip.encode()
    if ctx.codec_encode:
        _p07 = ctx.codec_encode("sc_54007", {"battle_start": {
            "result": 0, "battle_id": bid, "battle_server_ip": battle_ip,
            "battle_server_port": _bport.decode()}})
    if not _p07:
        _bs = (b"\x08\x00" + b"\x10" + _varint(bid) + b"\x1a" + _varint(len(b_ip_bytes)) + b_ip_bytes
               + b"\x22" + _varint(len(_bport)) + _bport)
        _p07 = b"\x0a" + _varint(len(_bs)) + _bs
    frames.append(DownFrame(54007, _p07))
    ctx.log(f"cs_54030 -> battle_id={bid} dest={info['dest']} heroes={info['heroes']} "
            f"x{info['battle_times']} 战斗服={battle_ip}:{_BATTLE_PORT[0]}")
    return CSResponse(frames)


def h_54300(req, ctx):
    """战斗初始化 Push → 明确不回包（C# Launcher 直连战斗服 UDP，回包触发 Protocol 解析异常）"""
    ctx.log("cs_54300（战斗初始化，不回包）")
    return CSResponse(reply=False)


def h_54032(req, ctx):
    """战斗结算 → sc_54033（动态结算引擎）。

    结果来源：UDP 132 上报登记（pop_battle_result）优先；
    - 若无 132 战报（如暂停主动退出战斗），真实结果为主动退出 (result=3)；
    - 纯剧情战斗无 combat 战报，自动视为完成 (result=1)。
    结算内容：通用掉落引擎 + 首通章节奖励 + 进度推进 + 24009/15009/17009 联动推送。
    """
    bid = _pkt_field1_varint({"payload": req.payload}) or ctx.battle_id[0] or 1
    session = get_battle_session(bid) or {}
    dest = session.get("dest") or ctx.battle_stage[0] or 0
    heroes = session.get("heroes") or []
    times = session.get("battle_times") or 1
    stage_type = session.get("stage_type") or 0
    activity_id = session.get("activity_id") or 0

    res_obj = session.get("result_data")
    if res_obj is None:
        res_obj = pop_battle_result(bid, default=None)
        if res_obj is None:
            if session.get("is_story"):
                res_obj = {"result": 1, "info": {}}
            else:
                res_obj = {"result": 3, "info": {}}
        session["result_data"] = res_obj
    
    if isinstance(res_obj, dict):
        result = int(res_obj.get("result", 1))
        b_info = res_obj.get("info", {})
    else:
        result = int(res_obj or 1)
        b_info = {}

    if "br" in session and "extra" in session:
        br, extra = session["br"], session["extra"]
    else:
        br, extra = _settle_frames(ctx, req.uid, bid, result, dest, times=times, heroes=heroes, battle_info=b_info, stage_type=stage_type, activity_id=activity_id)
        session["br"] = br
        session["extra"] = extra
    _p33 = ctx.codec_encode("sc_54033", {"result": 0, "battle_result": br}) or b"\x08\x00"
    ctx.log(f"cs_54032 -> 结算 bid={bid} result={result} dest={dest} 掉落组={len(br['all_drop_list'])} 三星={br.get('star_list')}")
    return CSResponse(extra + [DownFrame(54033, _p33)])


def h_43002(req, ctx):
    """战斗装备 → sc_43003(result=0 ack) + sc_43001(动态 battle_equip 库表驱动)。"""
    _p01 = None
    if ctx.generator:
        try:
            _p01 = ctx.generator.gen_payload(43001, uid=req.uid, db=ctx.db)
        except Exception:
            _p01 = None
    if not _p01 and ctx.codec_encode and ctx.db:
        try:
            rows = ctx.db.query("SELECT stage_id, insure_times, suit_id FROM battle_equip WHERE uid=?", (req.uid,))
            suit_id = 1
            insure_map = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
            if rows:
                for r in rows:
                    if r.get("suit_id"):
                        suit_id = int(r["suit_id"])
                    d = int(r["stage_id"]) % 10
                    if 1 <= d <= 5:
                        insure_map[d] = max(insure_map[d], int(r.get("insure_times") or 0))
            _p01 = ctx.codec_encode("sc_43001", {
                "stage_base_id": 30001,
                "equip_suit_id": suit_id if suit_id > 0 else 1,
                "next_refresh_time": int(time.time()) + 86400,
                "insure_list": [{"difficulty": d, "insure_times": insure_map[d]} for d in range(1, 6)]
            })
        except Exception:
            _p01 = None
    if not _p01:
        _p01 = (b"\x08\xb1\xea\x01\x10\x01\x18\x80\xa4\xa7\xda\x06"
                + b"\x22\x04\x08\x01\x10\x00\x22\x04\x08\x02\x10\x00"
                + b"\x22\x04\x08\x03\x10\x00\x22\x04\x08\x04\x10\x00\x22\x04\x08\x05\x10\x00")
    ctx.log("cs_43002 -> 动态 sc_43003+sc_43001（刻印关卡与UP套装状态）")
    return CSResponse([DownFrame(43003, b"\x08\x00"), DownFrame(43001, _p01)])


def h_54038(req, ctx):
    """扫荡 → sc_54039（f1 result, f2 player_info, f3 battle_result，动态掉落引擎 × battle_times）。"""
    import reserve_codec
    import battle_payload
    info = _parse_battle_common(req, ctx)
    dest = info["dest"]
    times = max(1, int(info["battle_times"] or 1))
    heroes = info["heroes"]
    if not heroes and ctx.db is not None:
        heroes = reserve_codec.get_stage_preset_heroes(ctx.db, req.uid, dest, info["type"])

    bid = _gen_battle_id(ctx.battle_seq)
    ctx.battle_id[0] = bid
    br, extra = _settle_frames(ctx, req.uid, bid, 1, dest,
                               times=times, heroes=heroes, sweep=True)
    if ctx.db:
        _p39 = battle_payload.build_sc_54039_dynamic(ctx.db, req.uid, br, heroes, codec_encode=ctx.codec_encode)
    elif ctx.codec_encode:
        _p39 = ctx.codec_encode("sc_54039", {"result": 0, "battle_result": br}) or b"\x08\x00"
    else:
        _p39 = b"\x08\x00"
    ctx.log(f"cs_54038 -> 扫荡 dest={dest} x{times} 出战英雄={heroes} 获得掉落组={len(br.get('all_drop_list', []))}")
    return CSResponse(extra + [DownFrame(54039, _p39)])


def h_63000(req, ctx):
    """保存编队 → sc_63001(result=0) + 持久化写入 reserve_team/reserve_team_hero"""
    import reserve_codec
    req_data = {}
    if req.payload:
        try:
            req_data = reserve_codec.parse_cs_63000_raw(req.payload)
        except Exception:
            if ctx.codec_decode:
                try:
                    req_data = ctx.codec_decode(req.payload, "cs_63000") or {}
                except Exception:
                    req_data = {}
    if ctx.db is not None and req_data:
        try:
            reserve_codec.save_team_from_cs_63000(ctx.db, req.uid, req_data)
        except Exception as e:
            ctx.log(f"[reserve] cs_63000 写库失败: {e}", "WARN")
    ctx.log(f"cs_63000 -> 保存编队 team_type={req_data.get('team_type', 0)}")
    return CSResponse([DownFrame(63001, b"\x08\x00")])


def h_63006(req, ctx):
    """上阵即存（客户端 Push 单向，矩阵/多维连携选择等场景）→ 关卡现役编队原子保存，无需回包。

    schema: cs_63006 {team_type(1), cont_team(2): single_cont_teams_net_rec{cont_id, teams[]},
    data(3)}。cont_id 即官方关卡键（GetHeroTeamActivityID 产物），直传 stage_team。
    """
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_63006") or {}
        except Exception:
            data = {}
    if ctx.db is not None and data:
        try:
            from team_server import TeamServer as _TS
            stage_type = int(data.get("team_type") or 0)
            cont_team = data.get("cont_team") or {}
            cont_id = int(cont_team.get("cont_id") or 0)
            for t in (cont_team.get("teams") or []):
                heroes = []
                for hi in (t.get("hero_list") or []):
                    hid = int(hi.get("hero_id") or hi.get("id") or 0)
                    if hid:
                        heroes.append({"hero_id": hid, "hero_type": int(hi.get("hero_type") or 1)})
                if not heroes:
                    continue
                mi = t.get("mimir_info") or {}
                _TS.get_instance().record_stage_team(
                    ctx, req.uid, stage_type, 0,
                    heroes=heroes,
                    cooperate_skill=int(t.get("cooperate_unique_skill_id") or 0),
                    mimir_id=int(mi.get("mimir_id") or 0),
                    mimir_chips=",".join(str(x) for x in (mi.get("chip_list") or []) if x),
                    team_index=int(t.get("team_index") or t.get("id") or 0),
                    activity_key=cont_id)
            ctx.log(f"cs_63006 -> 上阵即存 stage_type={stage_type} 关卡键={cont_id}")
        except Exception as e:
            ctx.log(f"[team] cs_63006 上阵即存异常: {e}", "WARN")
    return CSResponse([])  # Push 单向不回包


def h_56002(req, ctx):
    """红点已读上报（客户端 Push 单向通知）→ 解码写库 state=0，无需回包（回包会引发客户端 unpack 崩溃）"""
    _rd_id = None
    try:
        if ctx.codec_decode:
            _rd = ctx.codec_decode(req.payload, "cs_56002")
            _rd_id = _rd.get("red_dot") if isinstance(_rd, dict) else None
        if _rd_id is not None and ctx.db is not None:
            ctx.db.set_red_dot(req.uid, int(_rd_id), 0)
            ctx.log(f"cs_56002 -> 红点已读写库 red_dot={_rd_id}")
        else:
            ctx.log(f"cs_56002 解码失败 payload={req.payload.hex()}", "WARN")
    except Exception as _e6:
        ctx.log(f"cs_56002 写库失败: {_e6}", "WARN")
    return CSResponse([])


# ---------------- 体力购买与道具兑换 ----------------

_FATIGUE_BUY_COUNT = {}  # uid -> int (今日购买次数)

def _get_fatigue_cost(buy_index):
    """根据今日已购买次数返回单次钻石消耗（第1~2次25，第3~5次50，第6次及以上100）"""
    if buy_index <= 2:
        return 25
    elif buy_index <= 5:
        return 50
    else:
        return 100


def h_15016(req, ctx):
    """移转之辉购买体力 → sc_15017 + sc_15007 + sc_15009"""
    req_data = {}
    if ctx.codec_decode and req.payload:
        try:
            req_data = ctx.codec_decode(req.payload, "cs_15016") or {}
        except Exception:
            req_data = {}
    
    times = max(1, int(req_data.get("num") or 1))
    current_buys = _FATIGUE_BUY_COUNT.get(req.uid, 0)
    
    total_cost = sum(_get_fatigue_cost(current_buys + i + 1) for i in range(times))
    total_gain = 60 * times
    
    if ctx.db is not None:
        # 扣除移转之辉（优先 ID 2 免费钻石，不足扣 ID 1 原石）
        c2_row = ctx.db.query("SELECT num FROM currency WHERE uid=? AND id=2", (req.uid,))
        c2_num = c2_row[0]["num"] if c2_row else 0
        
        if c2_num >= total_cost:
            ctx.db.execute("UPDATE currency SET num = num - ? WHERE uid=? AND id=2", (total_cost, req.uid))
        else:
            remain_cost = total_cost - c2_num
            ctx.db.execute("UPDATE currency SET num = 0 WHERE uid=? AND id=2", (req.uid,))
            ctx.db.execute("UPDATE currency SET num = MAX(0, num - ?) WHERE uid=? AND id=1", (remain_cost, req.uid))
            
        # 增加体力（ID 4）
        ctx.db.execute("UPDATE currency SET num = num + ? WHERE uid=? AND id=4", (total_gain, req.uid))
        
    _FATIGUE_BUY_COUNT[req.uid] = current_buys + times
    left_times = max(0, 10 - _FATIGUE_BUY_COUNT[req.uid])
    
    _p17 = ctx.codec_encode("sc_15017", {"result": 0, "reward_list": [{"id": 4, "num": total_gain}]}) or b"\x08\x00"
    _p07 = ctx.codec_encode("sc_15007", {"left_time_coin": 10, "left_time_fatigue": left_times}) or b"\x08\x0a\x10\x0a"
    
    extra = []
    if ctx.generator is not None:
        p09 = ctx.generator.gen_payload(15009, uid=req.uid, db=ctx.db)
        if p09:
            extra.append(DownFrame(15009, p09))
            
    ctx.log(f"cs_15016 -> 移转之辉购买体力 x{times}，花费 {total_cost} 钻石，获得 {total_gain} 体力")
    return CSResponse([DownFrame(15017, _p17), DownFrame(15007, _p07)] + extra)


_STAMINA_POTION_MAP = {
    20001: 30, 20004: 30,
    20002: 60, 20005: 60, 20007: 60, 20008: 60, 20011: 60, 20022: 60, 20024: 60, 20025: 60, 20027: 60,
    20003: 120, 20006: 120, 20009: 120, 20010: 120, 20012: 120, 20023: 120, 20026: 120, 20028: 120,
}

def h_17012(req, ctx):
    """使用道具（体力药/黑区信标/材料/礼包）→ sc_17013 + sc_17009 (+ sc_15009 + sc_44007..sc_44023)"""
    req_data = {}
    if ctx.codec_decode and req.payload:
        try:
            req_data = ctx.codec_decode(req.payload, "cs_17012") or {}
        except Exception:
            req_data = {}
            
    use_list = req_data.get("use_item_list") or []
    drop_list = []
    stamina_gained = 0
    is_mythic_beacon = False
    
    if ctx.db is not None:
        for u in use_list:
            info = u.get("item_info") or {}
            iid = int(info.get("id") or info.get("item_id") or 0)
            count = max(1, int(info.get("num") or 1))
            if not iid:
                continue
                
            if iid == 41101:
                # 黑区信标：刷新黑区与失序深区挑战与领奖状态（不影响自然刷新周期）
                is_mythic_beacon = True
                if hasattr(ctx.db, "reset_mythic_by_beacon"):
                    ctx.db.reset_mythic_by_beacon(req.uid)
                else:
                    ctx.db.execute("UPDATE material SET num = MAX(0, num - ?) WHERE uid=? AND id=?", (count, req.uid, iid))
            elif iid in _STAMINA_POTION_MAP:
                # 体力药判定
                ctx.db.execute("UPDATE material SET num = MAX(0, num - ?) WHERE uid=? AND id=?", (count, req.uid, iid))
                gain_per = _STAMINA_POTION_MAP[iid]
                gain = gain_per * count
                stamina_gained += gain
                drop_list.append({"id": 4, "num": gain})
            else:
                # 默认保底机制（若未匹配特定药剂，按 60 体力处理或原样掉落）
                ctx.db.execute("UPDATE material SET num = MAX(0, num - ?) WHERE uid=? AND id=?", (count, req.uid, iid))
                gain = 60 * count
                stamina_gained += gain
                drop_list.append({"id": 4, "num": gain})
                
        if stamina_gained > 0:
            ctx.db.execute("UPDATE currency SET num = num + ? WHERE uid=? AND id=4", (stamina_gained, req.uid))
            
    _p13 = ctx.codec_encode("sc_17013", {"result": 0, "drop_list": drop_list}) or b"\x08\x00"
    
    extra = []
    if is_mythic_beacon and ctx.codec_encode and ctx.db:
        try:
            import weekly_challenge_service
            sc_07, sc_09, sc_19, sc_21, sc_23 = weekly_challenge_service.get_mythic_data(req.uid, ctx.db)
            p07 = ctx.codec_encode("sc_44007", sc_07)
            p09 = ctx.codec_encode("sc_44009", sc_09)
            p19 = ctx.codec_encode("sc_44019", sc_19)
            p21 = ctx.codec_encode("sc_44021", sc_21)
            p23 = ctx.codec_encode("sc_44023", sc_23)
            if p07: extra.append(DownFrame(44007, p07))
            if p09: extra.append(DownFrame(44009, p09))
            if p19: extra.append(DownFrame(44019, p19))
            if p21: extra.append(DownFrame(44021, p21))
            if p23: extra.append(DownFrame(44023, p23))
        except Exception as e:
            ctx.log(f"[h_17012] 黑区信标推送异常: {e}", "WARN")

    if ctx.generator is not None:
        touched_use = set()
        for u in use_list:
            info = u.get("item_info") or {}
            _id = int(info.get("id") or info.get("item_id") or 0)
            if _id:
                touched_use.add(_id)
        if stamina_gained > 0:
            touched_use.add(4)
        for d in drop_list:
            if d.get("id"):
                touched_use.add(int(d["id"]))
        try:
            p17023 = ctx.generator.gen_payload(17023, uid=req.uid, db=ctx.db, touched_items=touched_use)
            if p17023:
                extra.append(DownFrame(17023, p17023))
        except Exception:
            pass
        if stamina_gained > 0:
            try:
                p17025 = ctx.generator.gen_payload(17025, uid=req.uid, db=ctx.db)
                if p17025:
                    extra.append(DownFrame(17025, p17025))
            except Exception:
                pass
            p_cur = ctx.generator.gen_payload(15009, uid=req.uid, db=ctx.db)
            if p_cur:
                extra.append(DownFrame(15009, p_cur))
                
    ctx.log(f"cs_17012 -> 使用道具 {use_list}，信标重置={is_mythic_beacon}，获得体力 +{stamina_gained}，产物={drop_list}")
    return CSResponse([DownFrame(17013, _p13)] + extra)


def h_12046(req, ctx):
    """领取每日定时免费体力（11点/18点，每次+30点）→ sc_12047 + sc_12045 + sc_15009"""
    import daily_fatigue_codec
    req_data = {}
    if ctx.codec_decode and req.payload:
        try:
            req_data = ctx.codec_decode(req.payload, "cs_12046") or {}
        except Exception:
            req_data = {}
            
    fatigue_type = int(req_data.get("type") or 11)
    gain_stamina = 30
    
    if ctx.db is not None:
        daily_fatigue_codec.claim_daily_fatigue(ctx.db, req.uid, fatigue_type)
        ctx.db.add_currency(req.uid, 4, gain_stamina)
        
    _p47 = ctx.codec_encode("sc_12047", {"result": 0}) or b"\x08\x00"
    
    extra = []
    if ctx.generator is not None:
        p45 = ctx.generator.gen_payload(12045, uid=req.uid, db=ctx.db)
        if p45:
            extra.append(DownFrame(12045, p45))
        p09 = ctx.generator.gen_payload(15009, uid=req.uid, db=ctx.db)
        if p09:
            extra.append(DownFrame(15009, p09))
            
    ctx.log(f"cs_12046 -> 领取每日免费体力 type={fatigue_type}，获得体力 +{gain_stamina}")
    return CSResponse([DownFrame(12047, _p47)] + extra)


def h_24014(req, ctx):
    """领取章节累计星数阶段奖励（cs_24014 -> sc_24015 + sc_15009 + sc_17009 + sc_24017）"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_24014") or {}
        except Exception:
            data = {}
    ch_id = int(data.get("id") or 0)
    treasure_id = int(data.get("treasure_id") or 1)

    import json as _j24
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chapter_star_rewards.json")
    ch_cfg = {}
    try:
        if os.path.exists(_cfg_path):
            ch_cfg = _j24.load(open(_cfg_path, "r", encoding="utf-8"))
    except Exception:
        ch_cfg = {}

    c_info = ch_cfg.get(str(ch_id)) or ch_cfg.get(ch_id) or {}
    star_needs = c_info.get("star_need") or []
    reward_keys = {1: "first_reward", 2: "second_reward", 3: "third_reward"}
    reward_items = c_info.get(reward_keys.get(treasure_id, "first_reward")) or []

    # 1. 校验星数门槛
    need_stars = star_needs[treasure_id - 1] if 0 <= treasure_id - 1 < len(star_needs) else 9999
    sec_ids = c_info.get("section_id_list") or []
    cur_stars = 0
    if ctx.db and sec_ids:
        placeholders = ",".join("?" for _ in sec_ids)
        rows = ctx.db.query(f"SELECT star_list FROM chapter WHERE uid=? AND id IN ({placeholders})", (req.uid, *sec_ids))
        for r in (rows or []):
            try:
                sl = _j24.loads(r.get("star_list") or "[]")
                cur_stars += sum(1 for x in sl if int(x) == 1)
            except Exception:
                pass

    if cur_stars < need_stars:
        ctx.log(f"cs_24014 -> 领取失败：星数不足 (当前 {cur_stars} < 需求 {need_stars})", "WARN")
        p15 = ctx.codec_encode("sc_24015", {"result": 1, "reward_list": []}) if ctx.codec_encode else b"\x08\x01"
        return CSResponse([DownFrame(24015, p15)])

    # 2. 校验是否已领取
    claimed = ctx.db.query(
        "SELECT 1 FROM chapter_star_reward WHERE uid=? AND chapter_id=? AND reward_order=?",
        (req.uid, ch_id, treasure_id)
    ) if ctx.db else []
    if claimed:
        ctx.log(f"cs_24014 -> 领取失败：已领取过 ch={ch_id} order={treasure_id}", "WARN")
        p15 = ctx.codec_encode("sc_24015", {"result": 1, "reward_list": []}) if ctx.codec_encode else b"\x08\x01"
        return CSResponse([DownFrame(24015, p15)])

    # 3. 发放奖励入库
    reward_list_proto = []
    if ctx.db and reward_items:
        for it in reward_items:
            iid, inum = int(it[0]), int(it[1])
            reward_list_proto.append({"id": iid, "num": inum})
            _item_route_add(ctx, req.uid, iid, inum)

        ctx.db.execute(
            "INSERT OR REPLACE INTO chapter_star_reward (uid, chapter_id, reward_order, receive_time) VALUES (?, ?, ?, ?)",
            (req.uid, ch_id, treasure_id, int(time.time()))
        )

    p15 = ctx.codec_encode("sc_24015", {"result": 0, "reward_list": reward_list_proto}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(24015, p15)]

    if ctx.generator is not None:
        p09 = ctx.generator.gen_payload(15009, uid=req.uid, db=ctx.db)
        if p09:
            frames.append(DownFrame(15009, p09))
        touched_rw = set(int(r.get("id") or 0) for r in reward_list_proto if r.get("id"))
        if touched_rw:
            try:
                p17023 = ctx.generator.gen_payload(17023, uid=req.uid, db=ctx.db, touched_items=touched_rw)
                if p17023:
                    frames.append(DownFrame(17023, p17023))
            except Exception:
                pass
        p24 = ctx.generator.gen_payload(24017, uid=req.uid, db=ctx.db)
        if p24:
            frames.append(DownFrame(24017, p24))

    ctx.log(f"cs_24014 -> 领取章节星数奖励成功 ch={ch_id} order={treasure_id} 获得={reward_list_proto}")
    return CSResponse(frames)


# ---------------- 图鉴与剧情系统 (52xxx / 12002 / 12026 / 14104 / 14106 / 14108) ----------------

_ILLU_META_CACHE = None

def _load_illu_meta():
    global _ILLU_META_CACHE
    if _ILLU_META_CACHE is not None:
        return _ILLU_META_CACHE
    import json as _jm
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "illustrated_meta.json")
    try:
        if os.path.exists(cfg_path):
            _ILLU_META_CACHE = _jm.load(open(cfg_path, "r", encoding="utf-8"))
        else:
            _ILLU_META_CACHE = {"story_to_pics": {}, "pic_info": {}}
    except Exception:
        _ILLU_META_CACHE = {"story_to_pics": {}, "pic_info": {}}
    return _ILLU_META_CACHE


def h_12002(req, ctx):
    """剧情观看/记录：cs_12002 {story_id} → sc_12003 {result: 0, story_id} + sc_52011 (解锁插图) + sc_52027 (解锁剧情)。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_12002") or {}
        except Exception:
            data = {}
    story_id = int(data.get("story_id") or 0)
    unlocked_pics = []

    if story_id and ctx.db:
        ctx.db.execute(
            "INSERT OR REPLACE INTO story_unlock (uid, story_id, unlock_flag) VALUES (?, ?, 1)",
            (req.uid, story_id)
        )
        ctx.db.execute(
            "INSERT OR REPLACE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, 'plot', ?, 1)",
            (req.uid, story_id)
        )
        meta = _load_illu_meta()
        s2p = meta.get("story_to_pics", {})
        pids = s2p.get(str(story_id), []) or s2p.get(story_id, [])
        for pid in pids:
            pid_int = int(pid)
            r = ctx.db.query("SELECT 1 FROM illustrated WHERE uid=? AND kind='inbetweening' AND item_id=?", (req.uid, pid_int))
            if not r:
                ctx.db.execute(
                    "INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view) VALUES (?, 'inbetweening', ?, 0, 0)",
                    (req.uid, pid_int)
                )
                unlocked_pics.append({"id": pid_int, "is_receive": 0, "is_view": 0})

    p03 = ctx.codec_encode("sc_12003", {"result": 0, "story_id": story_id}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(12003, p03)]

    # 实时推送插图解锁
    if unlocked_pics and ctx.codec_encode:
        p52011 = ctx.codec_encode("sc_52011", {"inbetweening_info": unlocked_pics})
        if p52011:
            frames.append(DownFrame(52011, p52011))

    # 实时推送剧情解锁
    if story_id and ctx.codec_encode:
        p52027 = ctx.codec_encode("sc_52027", {"plot_info": [{"id": story_id, "is_view": 0}]})
        if p52027:
            frames.append(DownFrame(52027, p52027))

    ctx.log(f"cs_12002 -> 剧情观看 story_id={story_id} 解锁插图={unlocked_pics}")
    return CSResponse(frames)


def h_52004(req, ctx):
    """领取插图奖励：cs_52004 {id_list} → sc_52005 {result: 0, item_list} + sc_15009 (货币同步)。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_52004") or {}
        except Exception:
            data = {}
    id_list = data.get("id_list") or []

    # 若客户端未传 id_list，默认领取所有未领取的插图
    if not id_list and ctx.db:
        unclaimed = ctx.db.query(
            "SELECT item_id FROM illustrated WHERE uid=? AND kind='inbetweening' AND is_receive=0",
            (req.uid,)
        )
        id_list = [int(r["item_id"]) for r in unclaimed] if unclaimed else []

    meta = _load_illu_meta()
    pic_info = meta.get("pic_info", {})

    total_rewards = {}
    claimed_ids = []

    if ctx.db and id_list:
        for pid in id_list:
            p_int = int(pid)
            row = ctx.db.query(
                "SELECT is_receive FROM illustrated WHERE uid=? AND kind='inbetweening' AND item_id=?",
                (req.uid, p_int)
            )
            if row and int(row[0].get("is_receive") or 0) == 0:
                ctx.db.execute(
                    "UPDATE illustrated SET is_receive=1 WHERE uid=? AND kind='inbetweening' AND item_id=?",
                    (req.uid, p_int)
                )
                claimed_ids.append(p_int)
                pcfg = pic_info.get(str(p_int)) or {}
                rew = pcfg.get("reward") or [1, 20]
                cid, num = int(rew[0]), int(rew[1])
                _item_route_add(ctx, req.uid, cid, num)
                total_rewards[cid] = total_rewards.get(cid, 0) + num
            elif not row:
                ctx.db.execute(
                    "INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view) VALUES (?, 'inbetweening', ?, 1, 1)",
                    (req.uid, p_int)
                )
                claimed_ids.append(p_int)
                pcfg = pic_info.get(str(p_int)) or {}
                rew = pcfg.get("reward") or [1, 20]
                cid, num = int(rew[0]), int(rew[1])
                _item_route_add(ctx, req.uid, cid, num)
                total_rewards[cid] = total_rewards.get(cid, 0) + num

    items_out = [{"id": cid, "num": n} for cid, n in total_rewards.items()]
    p05 = ctx.codec_encode("sc_52005", {"result": 0, "item_list": items_out}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(52005, p05)]

    # 同步货币与材料
    if ctx.generator is not None and total_rewards:
        p09 = ctx.generator.gen_payload(15009, uid=req.uid, db=ctx.db)
        if p09:
            frames.append(DownFrame(15009, p09))
        try:
            p17023 = ctx.generator.gen_payload(17023, uid=req.uid, db=ctx.db, touched_items=set(total_rewards.keys()))
            if p17023:
                frames.append(DownFrame(17023, p17023))
        except Exception:
            pass

    ctx.log(f"cs_52004 -> 领取插图奖励 claimed={claimed_ids} 获得={items_out}")
    return CSResponse(frames)


def h_52014(req, ctx):
    """查看图鉴条目（消除红点）：cs_52014 {id, type} → sc_52015 {result: 0}。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_52014") or {}
        except Exception:
            data = {}
    item_id = int(data.get("id") or 0)
    typ = int(data.get("type") or 0)

    # CollectConst: 1情报 2敌人 3钥从 4刻印 5剧情 6插画 7世界观
    type_kind_map = {
        1: "information",
        2: "enemy",
        3: "servant",
        4: "equip",
        5: "plot",
        6: "inbetweening",
        7: "affix"
    }
    kind = type_kind_map.get(typ)

    if item_id and kind and ctx.db:
        cur = ctx.db.execute(
            "UPDATE illustrated SET is_view=1 WHERE uid=? AND kind=? AND item_id=?",
            (req.uid, kind, item_id)
        )
        if cur.rowcount == 0:
            ctx.db.execute(
                "INSERT INTO illustrated (uid, kind, item_id, is_view, is_receive) VALUES (?, ?, ?, 1, 0)",
                (req.uid, kind, item_id)
            )
    elif item_id and ctx.db:
        ctx.db.execute(
            "UPDATE illustrated SET is_view=1 WHERE uid=? AND item_id=?",
            (req.uid, item_id)
        )

    p15 = ctx.codec_encode("sc_52015", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    ctx.log(f"cs_52014 -> 查看图鉴条目 id={item_id} type={typ}({kind}) 红点消除")
    return CSResponse([DownFrame(52015, p15)])


def h_52016(req, ctx):
    """保存加载图壁纸列表：cs_52016 {all_id_list} → sc_52017 {result: 0}。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_52016") or {}
        except Exception:
            data = {}
    all_id_list = data.get("all_id_list") or []
    if ctx.db:
        import json as _js
        ctx.db.execute(
            "INSERT OR REPLACE INTO loading_set (uid, id_list, update_ts) VALUES (?, ?, ?)",
            (req.uid, _js.dumps(all_id_list), int(time.time()))
        )
    p17 = ctx.codec_encode("sc_52017", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    ctx.log(f"cs_52016 -> 保存加载图设置 count={len(all_id_list)}")
    return CSResponse([DownFrame(52017, p17)])


def h_52018(req, ctx):
    """设置单张加载图：cs_52018 {type, id} → sc_52019 {result: 0}。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_52018") or {}
        except Exception:
            data = {}
    typ = data.get("type")
    pic_id = int(data.get("id") or 0)
    if ctx.db:
        import json as _js
        r = ctx.db.query("SELECT id_list FROM loading_set WHERE uid=?", (req.uid,))
        ids = _js.loads(r[0]["id_list"]) if r and r[0]["id_list"] else []
        if typ and pic_id:
            if pic_id not in ids:
                ids.append(pic_id)
        elif pic_id in ids:
            ids.remove(pic_id)
        ctx.db.execute(
            "INSERT OR REPLACE INTO loading_set (uid, id_list, update_ts) VALUES (?, ?, ?)",
            (req.uid, _js.dumps(ids), int(time.time()))
        )
    p19 = ctx.codec_encode("sc_52019", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    ctx.log(f"cs_52018 -> 切换单张加载图 id={pic_id} type={typ}")
    return CSResponse([DownFrame(52019, p19)])


def h_52022(req, ctx):
    """领取目标情报进度奖励：cs_52022 {collect_info_list} → sc_52023 {result: 0, item_list} + sc_15009。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_52022") or {}
        except Exception:
            data = {}
    req_list = data.get("collect_info_list") or []
    import illustrated_listener
    import json as _js
    race_cfg = illustrated_listener._get_hero_race_cfg()
    by_race = race_cfg.get("by_race", {})
    total_items = {}

    if ctx.db:
        race_counts = illustrated_listener.get_hero_race_counts(ctx.db, req.uid)
        for item in req_list:
            race_type = int(item.get("race_type") or 0)
            cnt_list = item.get("cnt_list") or []
            user_cnt = race_counts.get(race_type, 0)
            r = ctx.db.query("SELECT received_cnt_list FROM hero_race_collect WHERE uid=? AND race_type=?", (req.uid, race_type))
            claimed_set = set(_js.loads(r[0]["received_cnt_list"])) if r and r[0]["received_cnt_list"] else set()

            tasks = by_race.get(str(race_type), [])
            for c in cnt_list:
                c_int = int(c)
                if c_int not in claimed_set and user_cnt >= c_int:
                    for t in tasks:
                        if t["need"] == c_int:
                            claimed_set.add(c_int)
                            for rw in t.get("reward", []):
                                cid = rw["id"]
                                cnum = rw["num"]
                                _item_route_add(ctx, req.uid, cid, cnum)
                                total_items[cid] = total_items.get(cid, 0) + cnum
                            break

            ctx.db.execute(
                "INSERT OR REPLACE INTO hero_race_collect (uid, race_type, received_cnt_list) VALUES (?, ?, ?)",
                (req.uid, race_type, _js.dumps(sorted(list(claimed_set))))
            )

    items_out = [{"id": cid, "num": n} for cid, n in total_items.items()]
    p23 = ctx.codec_encode("sc_52023", {"result": 0, "item_list": items_out}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(52023, p23)]

    if ctx.generator is not None:
        p25 = ctx.generator.gen_payload(52025, uid=req.uid, db=ctx.db)
        if p25:
            frames.append(DownFrame(52025, p25))
        if total_items:
            p09 = ctx.generator.gen_payload(15009, uid=req.uid, db=ctx.db)
            if p09:
                frames.append(DownFrame(15009, p09))
            try:
                p17023 = ctx.generator.gen_payload(17023, uid=req.uid, db=ctx.db, touched_items=set(total_items.keys()))
                if p17023:
                    frames.append(DownFrame(17023, p17023))
            except Exception:
                pass

    ctx.log(f"cs_52022 -> 领取阵营收集奖励 items={items_out}")
    return CSResponse(frames)



# [2026-09-06 外围模块迁移] h_12026 删除——原为死代码（被 OPERATIONS 优先级更高的
# QuerySetBgmOp 遮蔽），大厅 BGM 归 peripheral_service.py 统筹（写 illustrated + game_user.bgm_id）。


def h_14104(req, ctx):
    """角色档案/羁绊剧情阅读：cs_14104 {archive_id, video_list} → sc_14105 {result: 0} + sc_52011 (解锁插图)。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_14104") or {}
        except Exception:
            data = {}
    aid = int(data.get("archive_id") or 0)
    vlist = data.get("video_list") or []
    meta = _load_illu_meta()
    s2p = meta.get("story_to_pics", {})
    all_unlocked_pics = []

    if ctx.db:
        for vid in vlist:
            v_int = int(vid)
            ctx.db.execute("INSERT OR REPLACE INTO story_unlock (uid, story_id, unlock_flag) VALUES (?, ?, 1)",
                           (req.uid, v_int))
            ctx.db.execute("INSERT OR REPLACE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, 'plot', ?, 1)",
                           (req.uid, v_int))
            pids = s2p.get(str(v_int), []) or s2p.get(v_int, [])
            for pid in pids:
                pid_int = int(pid)
                r = ctx.db.query("SELECT 1 FROM illustrated WHERE uid=? AND kind='inbetweening' AND item_id=?", (req.uid, pid_int))
                if not r:
                    ctx.db.execute(
                        "INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view) VALUES (?, 'inbetweening', ?, 0, 0)",
                        (req.uid, pid_int)
                    )
                    all_unlocked_pics.append({"id": pid_int, "is_receive": 0, "is_view": 0})

        if aid and vlist:
            import json as _js
            ar = ctx.db.query("SELECT video_list FROM hero_archive WHERE uid=? AND archive_id=?", (req.uid, aid))
            cur_videos = _js.loads(ar[0]["video_list"] or "[]") if ar and ar[0].get("video_list") else []
            for vid in vlist:
                v_int = int(vid)
                if v_int not in cur_videos:
                    cur_videos.append(v_int)
            ctx.db.execute("UPDATE hero_archive SET video_list=? WHERE uid=? AND archive_id=?",
                           (_js.dumps(cur_videos), req.uid, aid))

    p05 = ctx.codec_encode("sc_14105", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(14105, p05)]
    if all_unlocked_pics and ctx.codec_encode:
        p52011 = ctx.codec_encode("sc_52011", {"inbetweening_info": all_unlocked_pics})
        if p52011:
            frames.append(DownFrame(52011, p52011))
    ctx.log(f"cs_14104 -> 档案阅读 archive={aid} videos={vlist} 解锁插图={all_unlocked_pics}")
    return CSResponse(frames)


def h_14106(req, ctx):
    """设为特别关注：cs_14106 {hero_id} → sc_14107 {result: 0}。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_14106") or {}
        except Exception:
            data = {}
    hid = int(data.get("hero_id") or 0)
    if hid and ctx.db:
        ctx.db.execute("UPDATE hero SET is_favorite=1 WHERE uid=? AND id=?", (req.uid, hid))
    p07 = ctx.codec_encode("sc_14107", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    ctx.log(f"cs_14106 -> 设为特别关注 hero_id={hid}")
    return CSResponse([DownFrame(14107, p07)])


def h_14108(req, ctx):
    """取消特别关注：cs_14108 {hero_id} → sc_14109 {result: 0}。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_14108") or {}
        except Exception:
            data = {}
    hid = int(data.get("hero_id") or 0)
    if hid and ctx.db:
        ctx.db.execute("UPDATE hero SET is_favorite=0 WHERE uid=? AND id=?", (req.uid, hid))
    p09 = ctx.codec_encode("sc_14109", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    ctx.log(f"cs_14108 -> 取消特别关注 hero_id={hid}")
    return CSResponse([DownFrame(14109, p09)])


# ---------------- 73xxx 英雄关系网与好感度/信任度系统 ----------------

_HERO_TRUST_REWARDS_CACHE = None

def _load_hero_trust_rewards():
    global _HERO_TRUST_REWARDS_CACHE
    if _HERO_TRUST_REWARDS_CACHE is not None:
        return _HERO_TRUST_REWARDS_CACHE
    import json as _jht
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero_trust_rewards.json")
    try:
        if os.path.exists(cfg_path):
            _HERO_TRUST_REWARDS_CACHE = _jht.load(open(cfg_path, "r", encoding="utf-8"))
        else:
            _HERO_TRUST_REWARDS_CACHE = {}
    except Exception:
        _HERO_TRUST_REWARDS_CACHE = {}
    return _HERO_TRUST_REWARDS_CACHE


def h_73016(req, ctx):
    """解锁人际关系网节点/增益：cs_73016 {id, group_index} → sc_73017 {result} + sc_14007 (角色数据刷新)。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_73016") or {}
        except Exception:
            data = {}
    node_id = int(data.get("id") or 0)
    group_index = int(data.get("group_index") or 0)

    from trust_service import TrustService
    svc = TrustService.get_instance()
    res = svc.unlock_relation_net(ctx, req.uid, node_id, group_index)
    hero_id = res.get("hero_id")

    p17 = ctx.codec_encode("sc_73017", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(73017, p17)]

    # 刷新角色包 sc_14007 (原子差量)
    if hero_id and ctx.db:
        try:
            import hero_codec as _hc
            hf = _hc.build_hero_14007_frame(ctx.db, req.uid, hero_id)
            if hf:
                frames.append(hf)
        except Exception:
            pass

    ctx.log(f"cs_73016 -> 解锁关系网 hero={hero_id} tier={res.get('tier')} group={group_index}")
    return CSResponse(frames)


def h_73020(req, ctx):
    """解锁好感度/心链：cs_73020 {hero_id} → sc_73021 {result, mood} + sc_14007。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_73020") or {}
        except Exception:
            data = {}
    hero_id = int(data.get("hero_id") or 0)

    from trust_service import TrustService
    svc = TrustService.get_instance()
    try:
        res = svc.unlock_trust(ctx, req.uid, hero_id)
        mood = res["mood"]
        result_code = 0
    except Exception as e:
        mood = 1
        result_code = 1
        ctx.log(f"cs_73020 -> 解锁好感度异常: {e}")

    p21 = ctx.codec_encode("sc_73021", {"result": result_code, "mood": mood}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(73021, p21)]
    if hero_id and ctx.db and result_code == 0:
        try:
            import hero_codec as _hc
            hf = _hc.build_hero_14007_frame(ctx.db, req.uid, hero_id)
            if hf:
                frames.append(hf)
        except Exception:
            pass
    ctx.log(f"cs_73020 -> 解锁好感度 hero={hero_id} mood={mood}")
    return CSResponse(frames)


def h_73014(req, ctx):
    """提升好感度/信任度等级：cs_73014 {hero_id} → sc_73015 {result, reward_list} + 纪念品/货币入库 + sc_14007 + sc_17023。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_73014") or {}
        except Exception:
            data = {}
    hero_id = int(data.get("hero_id") or 0)

    from trust_service import TrustService
    svc = TrustService.get_instance()
    try:
        res = svc.upgrade_trust_level(ctx, req.uid, hero_id)
        rewards = res.get("rewards", [])
        new_lvl = res.get("new_level", 1)
        result_code = 0
    except Exception as e:
        rewards = []
        new_lvl = 1
        result_code = 1
        ctx.log(f"cs_73014 -> 提升好感度等级异常: {e}")

    p15 = ctx.codec_encode("sc_73015", {"result": result_code, "reward_list": rewards}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(73015, p15)]

    if hero_id and ctx.db and result_code == 0:
        try:
            import hero_codec as _hc
            hf = _hc.build_hero_14007_frame(ctx.db, req.uid, hero_id)
            if hf:
                frames.append(hf)
        except Exception:
            pass

    # 推送原子差量帧 sc_17023 (杜绝 15009/17009 全量卡顿)
    try:
        import generator as _gen
        p17023 = _gen.gen_payload(17023, uid=req.uid, db=ctx.db)
        if p17023:
            frames.append(DownFrame(17023, p17023))
    except Exception:
        pass

    ctx.log(f"cs_73014 -> 提升好感度等级 hero={hero_id} → Lv{new_lvl} 奖励={rewards}")
    return CSResponse(frames)


def h_73012(req, ctx):
    """赠送好感度礼物：cs_73012 {hero_id, item_list} → sc_73013 {result} + sc_14007 + sc_17023。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_73012") or {}
        except Exception:
            data = {}
    hero_id = int(data.get("hero_id") or 0)
    items = data.get("item_list") or []

    from trust_service import TrustService
    svc = TrustService.get_instance()
    try:
        res = svc.send_trust_item(ctx, req.uid, hero_id, items)
        result_code = 0
    except Exception as e:
        res = {}
        result_code = 1
        ctx.log(f"cs_73012 -> 赠送好感度礼物异常: {e}")

    p13 = ctx.codec_encode("sc_73013", {"result": result_code}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(73013, p13)]

    if hero_id and ctx.db and result_code == 0:
        try:
            import hero_codec as _hc
            hf = _hc.build_hero_14007_frame(ctx.db, req.uid, hero_id)
            if hf:
                frames.append(hf)
        except Exception:
            pass

    # 推送原子差量帧 sc_17023 (杜绝 17009 全量卡顿)
    try:
        import generator as _gen
        touched = set(int(it.get("id") or 0) for it in items)
        p17023 = _gen.gen_payload(17023, uid=req.uid, db=ctx.db, touched_items=touched)
        if p17023:
            frames.append(DownFrame(17023, p17023))
    except Exception:
        pass

    ctx.log(f"cs_73012 -> 赠送好感度礼物 hero={hero_id} items={items}")
    return CSResponse(frames)


def h_73022(req, ctx):
    """领取羁绊故事奖励：cs_73022 {id} → sc_73023 {result: 0, reward_list} + sc_17023。"""
    data = {}
    if ctx.codec_decode and req.payload:
        try:
            data = ctx.codec_decode(req.payload, "cs_73022") or {}
        except Exception:
            data = {}
    story_id = int(data.get("id") or 0)

    from trust_service import TrustService
    svc = TrustService.get_instance()
    res = svc.claim_relation_story_reward(ctx, req.uid, story_id)
    rewards = res.get("rewards", [])

    p23 = ctx.codec_encode("sc_73023", {"result": 0, "reward_list": rewards}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(73023, p23)]

    if rewards:
        try:
            import generator as _gen
            p17023 = _gen.gen_payload(17023, uid=req.uid, db=ctx.db, touched_items={1})
            if p17023:
                frames.append(DownFrame(17023, p17023))
        except Exception:
            pass

    return CSResponse(frames)


# ---------------- 统一错误码层（契约 §7：库异常回 result=非0，防客户端卡死） ----------------

# cs → sc 错误回包映射（优先取各 handler 已知的响应 cmd；未列出的 cmd 用自身 cmd 兜底回 result=非0）
_CS2SC = {
    10500: 10501, 10050: 10051, 10700: 10701, 11010: 11011, 11081: 11082, 34024: 34025,
    30001: 30001, 30016: 30017, 16012: 16013,
    58002: 58003, 28014: 28015, 62012: 62013, 16010: 16011, 91002: 91003,
    91016: 91017, 91006: 91007, 91014: 91015, 28010: 28011, 54034: 54035,
    54032: 54033, 43002: 43003, 54030: 54031, 54038: 54039, 56002: 56002,
    63000: 63001, 15016: 15017, 17012: 17013, 12046: 12047, 24014: 24015,
    12002: 12003, 52004: 52005, 52014: 52015, 52016: 52017, 52018: 52019,
    52022: 52023, 12026: 12027, 14104: 14105, 14106: 14107, 14108: 14109,
    73016: 73017, 73020: 73021, 73014: 73015, 73012: 73013, 73022: 73023,
}


def _error_payload(code=1):
    """result=非0 最小帧（field1 varint=code）。code=0 是成功，错误码必须非0。"""
    return b"\x08" + _varint(code)


def _error_response(req, ctx=None, code=1, flag=0, srv=None):
    """契约 §7：库异常/编码失败 → 回对应 sc 的 result=非0 错误帧。
    flag=1 用于 91xxx（Lua 投递必须）；srv 由调用方显式传入（91xxx 连接级）。"""
    sc = _CS2SC.get(req.cmd, req.cmd)
    if ctx is not None:
        ctx.log(f"cmd={req.cmd} -> 错误回包 sc_{sc} result={code}", "WARN")
    return CSResponse([DownFrame(sc, _error_payload(code), flag=flag, srv=srv)])


def h_19030(req, ctx):
    """刷新好友视图 → sc_19031 + sc_19001 (全量下发 AI 角色与好友列表)"""
    ctx.log("cs_19030 -> 刷新好友视图 (sc_19031 + sc_19001)")
    p_19031 = ctx.codec_encode("sc_19031", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    p_19001 = ctx.generator.gen_payload(19001, uid=req.uid, db=ctx.db) if (ctx.generator and hasattr(ctx.generator, "gen_payload")) else None
    frames = [DownFrame(19031, p_19031)]
    if p_19001:
        frames.append(DownFrame(19001, p_19001))
    return CSResponse(frames)


def h_19034(req, ctx):
    """发送好友私聊消息/表情贴纸 → sc_19035 (确认) + 同步推送己方聊天气泡 (sc_19039) + 异步 LLM 回复 (sc_19039)"""
    ctx.log("cs_19034 -> 发送好友私聊消息")
    dec = ctx.codec_decode(req.payload, "cs_19034") if ctx.codec_decode else {}
    recv_uid = int(dec.get("receive_uid") or 0)
    msg_type = int(dec.get("type") or 1)
    content = str(dec.get("content") or "")

    p_19035 = ctx.codec_encode("sc_19035", {"result": 0}) if ctx.codec_encode else b"\x08\x00"
    frames = [DownFrame(19035, p_19035)]

    # 判断接收方是否为 AI 角色
    ai_char = ctx.db.get_ai_character(recv_uid) if (ctx.db and hasattr(ctx.db, "get_ai_character")) else None
    if ai_char:
        user_text = content
        if msg_type == 2:
            user_text = f"[发送了表情贴纸 #{content}]"

        now_ts = int(time.time())
        # 保存玩家消息
        user_msg_id = int(now_ts * 1000 + (int(req.uid) % 1000))
        if hasattr(ctx.db, "save_ai_chat_message"):
            user_msg_id = ctx.db.save_ai_chat_message(req.uid, recv_uid, "user", user_text, timestamp=now_ts)

        # 立即组装并推送我方发言帧 (sc_19039)，驱动客户端 UI 实时渲染己方右侧对话气泡
        user_row = ctx.db.query("SELECT nick, level, sign, portrait, icon_frame FROM users WHERE uid = ?", (req.uid,)) if ctx.db else []
        user_nick = user_row[0]["nick"] if (user_row and user_row[0]["nick"]) else f"管理员{req.uid}"
        user_icon = user_row[0]["portrait"] if (user_row and user_row[0]["portrait"]) else 1084
        user_frame = user_row[0]["icon_frame"] if (user_row and user_row[0]["icon_frame"]) else 2001

        user_push_obj = {
            "friend_msg_list": [{
                "chat_base_info": {
                    "id": int(req.uid),
                    "user_profile_base": {
                        "nick": str(user_nick),
                        "icon": int(user_icon),
                        "icon_frame": int(user_frame)
                    },
                    "type": int(msg_type),
                    "content": str(content),
                    "timestamp": now_ts,
                    "msg_id": int(user_msg_id),
                    "ip_location": "同服",
                    "jump_link": ""
                },
                "receive_uid": int(recv_uid)
            }]
        }
        if ctx.codec_encode:
            p_user_19039 = ctx.codec_encode("sc_19039", user_push_obj)
            frames.append(DownFrame(19039, p_user_19039))
            ctx.log(f"cs_19034 -> 同步推送己方聊天气泡 sc_19039 msg_id={user_msg_id} content={content[:20]}")

        # 异步提交给 AI Bot 服务
        try:
            import ai_bot_service

            def _on_ai_reply(uid, char_id, reply_text, msg_id, char_info):
                if hasattr(ctx, "send_push") and callable(ctx.send_push):
                    push_obj = {
                        "friend_msg_list": [{
                            "chat_base_info": {
                                "id": int(char_id),
                                "user_profile_base": {
                                    "nick": str(char_info["char_name"]),
                                    "icon": int(char_info.get("avatar_icon") or 1084),
                                    "icon_frame": int(char_info.get("icon_frame") or 2001)
                                },
                                "type": 1,
                                "content": str(reply_text),
                                "timestamp": int(time.time()),
                                "msg_id": int(msg_id),
                                "ip_location": str(char_info.get("ip_location") or "未知"),
                                "jump_link": ""
                            },
                            "receive_uid": int(uid)
                        }]
                    }
                    push_payload = ctx.codec_encode("sc_19039", push_obj)
                    ctx.send_push(19039, push_payload)
                else:
                    ctx.log("[cs_19034] 当前连接无 send_push 能力，消息已入库", "WARN")

            ai_bot_service.submit_ai_chat_task(req.uid, recv_uid, user_text, ctx.db, _on_ai_reply, log_fn=ctx.log)
        except Exception as _e:
            ctx.log(f"[cs_19034] AI Bot 服务调用异常: {_e}", "ERROR")

    return CSResponse(frames)


def h_32018(req, ctx):
    """查询他人/好友详细名片 → sc_32019 (支持 AI 角色与真实玩家)"""
    dec = ctx.codec_decode(req.payload, "cs_32018") if ctx.codec_decode else {}
    target_uid = int(dec.get("user_id") or 0)
    ctx.log(f"cs_32018 -> 查询详细名片 uid={target_uid}")

    ai_char = ctx.db.get_ai_character(target_uid) if (ctx.db and hasattr(ctx.db, "get_ai_character")) else None
    if ai_char:
        hids = str(ai_char.get("hero_ids") or "")
        first_hid = 0
        if hids:
            try:
                first_hid = int(hids.split(",")[0].strip())
            except Exception:
                pass
        hero_id = first_hid or int(ai_char.get("avatar_icon") or 1084)
        skin_id = int(ai_char.get("avatar_icon") or hero_id)
        profile_obj = {
            "result": 1,
            "user_id": target_uid,
            "base_info": {
                "nick": str(ai_char["char_name"]),
                "icon": int(ai_char.get("avatar_icon") or 1084),
                "icon_frame": int(ai_char.get("icon_frame") or 2001)
            },
            "sign": str(ai_char.get("sign") or ""),
            "sticker_show_info": [],
            "hero_list": [{"hero_id": hero_id, "star": 5, "using_skin": skin_id}],
            "level": int(ai_char.get("level") or 80),
            "is_online": 1,
            "club_id": 0,
            "club_name": "",
            "club_icon": 0,
            "ip_location": str(ai_char.get("ip_location") or "深空之眼"),
            "achievement_static_info": {"not_hide_num": 100, "hide_num": 0, "cfg_hide_num": 100},
            "hero_static_info": {"not_hide_num": 84, "hide_num": 0, "cfg_hide_num": 84},
            "weapon_servant_static_info": {"not_hide_num": 50, "hide_num": 0, "cfg_hide_num": 50},
            "sticker_static_info": {"not_hide_num": 20, "hide_num": 0, "cfg_hide_num": 20},
            "poster_hero": {"hero_id": hero_id, "star": 5, "using_skin": skin_id},
            "birthday": {"month": 8, "day": 8},
            "backhome_architecture_id": 0,
            "hero_id_list": [hero_id],
            "likes": 999,
            "used_tag_list": [],
            "information_background_id": int(ai_char.get("info_background") or 0),
            "post_background_id": int(ai_char.get("info_background") or 0),
            "sticker_background_static_info": {"not_hide_num": 0, "hide_num": 0, "cfg_hide_num": 0},
            "sticker_foreground_static_info": {"not_hide_num": 0, "hide_num": 0, "cfg_hide_num": 0},
            "hero_oath_display": [{"hero_id": hero_id, "oath": True, "nick": ""}]
        }
        p = ctx.codec_encode("sc_32019", profile_obj) if ctx.codec_encode else fallback_payload(32019)
        return CSResponse([DownFrame(32019, p)])

    return CSResponse([DownFrame(32019, fallback_payload(32019))])


def h_32020(req, ctx):
    """查询他人角色信息 → sc_32021 (支持 AI 角色展示)"""
    dec = ctx.codec_decode(req.payload, "cs_32020") if ctx.codec_decode else {}
    target_uid = int(dec.get("user_id") or 0)
    ctx.log(f"cs_32020 -> 查询他人角色信息 uid={target_uid}")

    ai_char = ctx.db.get_ai_character(target_uid) if (ctx.db and hasattr(ctx.db, "get_ai_character")) else None
    if ai_char:
        hids = str(ai_char.get("hero_ids") or "")
        first_hid = 0
        if hids:
            try:
                first_hid = int(hids.split(",")[0].strip())
            except Exception:
                pass
        hero_id = first_hid or int(ai_char.get("avatar_icon") or 1084)
        skin_id = int(ai_char.get("avatar_icon") or hero_id)
        hero_obj = {
            "result": 1,
            "user_id": target_uid,
            "hero_list": [{
                "hero_base_info": {
                    "id": hero_id,
                    "level": 80,
                    "star": 5,
                    "exp": 0,
                    "skill": [{"skill_id": hero_id * 100 + 1, "skill_level": 20}],
                    "weapon": {"exp": 0, "breakthrough": 5},
                    "servant": {"id": 0, "stage": 0},
                    "using_astrolabe": [],
                    "unlock_astrolabe": [],
                    "using_skin": skin_id,
                    "break_level": 5,
                    "exclusive_skill_list": [],
                    "weapon_module_level": 3,
                    "skill_intensify_attribute_list": [],
                    "battle_using_skin": skin_id
                },
                "equip_list": [],
                "using_hero_chip": [],
                "dorm_level": 0,
                "trust": {"level": 5, "exp": 0, "mood": 1, "relation": {"tier_list": []}}
            }],
            "hero_oath_display": [{"hero_id": hero_id, "oath": True, "nick": ""}]
        }
        p = ctx.codec_encode("sc_32021", hero_obj) if ctx.codec_encode else fallback_payload(32021)
        return CSResponse([DownFrame(32021, p)])

    return CSResponse([DownFrame(32021, fallback_payload(32021))])


def h_32132(req, ctx):
    """[个性化模块] 捕获大厅实时轮换看板娘/皮肤与场景上报：Push(32132) -> 静默不回包。"""
    try:
        dec = ctx.codec_decode(req.payload, "cs_32132") if ctx.codec_decode else None
        if isinstance(dec, dict):
            hero_id = dec.get("hero_id", 0)
            bg_id = dec.get("background_id", 0)
            if hero_id or bg_id:
                from peripheral_service import PeripheralService
                PeripheralService.get_instance(ctx.db).record_active_board_hero(req.uid, hero_id, bg_id)
                ctx.log(f"cs_32132 -> 捕获大厅实时轮换看板娘: hero/skin={hero_id} bg={bg_id} (静默不回包)")
    except Exception as e:
        ctx.log(f"[middleware] cs_32132 处理异常: {e}", "WARN")
    return CSResponse(reply=False, src="drop")


def h_32038(req, ctx):
    """[个性化模块] 捕获玩家贴纸排版画册上报：Push(32038) -> 静默不回包。"""
    try:
        dec = ctx.codec_decode(req.payload, "cs_32038") if ctx.codec_decode else None
        if isinstance(dec, dict):
            sticker_info = dec.get("sticker_show_info")
            if sticker_info:
                from peripheral_service import PeripheralService
                PeripheralService.get_instance(ctx.db).save_sticker_show_info(req.uid, sticker_info)
                ctx.log(f"cs_32038 -> 捕获贴纸画册排版 pages={len(sticker_info)} (静默不回包)")
    except Exception as e:
        ctx.log(f"[middleware] cs_32038 处理异常: {e}", "WARN")
    return CSResponse(reply=False, src="drop")


# ---------------- 注册表 ----------------

CORE_HANDLERS = {
    10500: h_10500,   # 刷新时间同步
    10050: h_10050,   # 心跳/时间同步（动态时间戳）
    10700: h_10700,   # 服务器时间
    11010: h_11010,   # 签到查询
    11081: h_11081,   # 执行签到
    34024: h_34024,   # 月卡奖励查询
    30001: h_30001,   # 未读邮件摘要（此前无入口，ack0 回错 cmd 帧）
    30016: h_30016,   # 专属信件列表（此前无入口）
    16012: h_16012,   # 抽卡信息
    58002: h_58002,   # 家园总览
    28014: h_28014,   # 任务一键领取（2 帧）
    62012: h_62012,   # 回归活动签到（2 帧）
    16010: h_16010,   # 抽卡（5 帧官方链）
    91002: h_91002,   # 聊天选项选择（flag=1）
    91016: h_91016,   # 换头像（flag=1）
    91006: h_91006,   # 会话读取上报（flag=1）
    91014: h_91014,   # 读会话内容（flag=1）
    28010: h_28010,   # 单个任务提交
    54034: h_54034,   # 剧情战斗入口
    54032: h_54032,   # 战斗结算 + 推送
    43002: h_43002,   # 战斗装备
    54030: h_54030,   # 普通战斗入口
    54300: h_54300,   # 战斗初始化（不回包）
    54038: h_54038,   # 扫荡
    56002: h_56002,   # 红点已读上报（写库+回空包）
    63000: h_63000,   # 保存编队（写库+sc_63001）
    63006: h_63006,   # 上阵即存（关卡现役编队原子保存，Push 单向）
    15016: h_15016,   # 移转之辉兑换体力（sc_15017+sc_15007+sc_15009）
    17012: h_17012,   # 使用道具/体力药（sc_17013+sc_17009+sc_15009）
    12046: h_12046,   # 领取每日定时免费体力（sc_12047+sc_12045+sc_15009）
    24014: h_24014,   # 领取章节累计星数阶段奖励（sc_24015+sc_15009+sc_17009+sc_24017）
    12002: h_12002,   # 剧情观看/记录（sc_12003+sc_52011+sc_52027）
    52004: h_52004,   # 领取插画奖励（sc_52005+sc_15009）
    52014: h_52014,   # 查看图鉴条目（消除红点+sc_52015）
    52016: h_52016,   # 保存加载图壁纸列表（sc_52017）
    52018: h_52018,   # 设置单张加载图（sc_52019）
    52022: h_52022,   # 领取目标情报奖励（sc_52023）
    14104: h_14104,   # 档案/羁绊剧情阅读（sc_14105+sc_52011）
    # 12026 大厅BGM：2026-09-06 迁 peripheral_service.QuerySetBgmOp（OPERATIONS 优先）
    14106: h_14106,   # 设为特别关注（sc_14107）
    14108: h_14108,   # 取消特别关注（sc_14109）
    73016: h_73016,   # 解锁人际关系网（sc_73017+sc_14007）
    73020: h_73020,   # 解锁好感度/心情（sc_73021+sc_14007）
    73014: h_73014,   # 提升好感度等级（sc_73015+sc_14007+sc_15009）
    73012: h_73012,   # 赠送好感度礼物（sc_73013+sc_14007+sc_17009）
    73022: h_73022,   # 领取羁绊故事奖励（sc_73023）
    19030: h_19030,   # [AI Bot] 刷新好友界面（sc_19031 + sc_19001 全量角色好友）
    19034: h_19034,   # [AI Bot] 发送私聊消息（sc_19035 立即回执 + 异步 LLM 回复推送）
    32018: h_32018,   # [AI Bot] 查看他人/角色名片（sc_32019 角色档案）
    32020: h_32020,   # [AI Bot] 查看他人/角色英雄信息（sc_32021 满级角色展示）
    32132: h_32132,   # [个性化模块] 捕获大厅实时轮换看板娘
    32038: h_32038,   # [个性化模块] 捕获贴纸画册排版上报
}

# 不回包白名单（契约 §9.3，LUA 挖掘确认 sc_None，客户端不期待响应）
DROP_NO_REPLY = {81002, 28834, 40012, 38014, 78012, 54300, 19036}
# 56002 例外：回空包防重试（已由 h_56002 处理，不回 DROP）


# ---------------- TEMPLATE 层（835 对模板库，契约 §4 第 3 级） ----------------

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_FILE = os.path.join(_BASE_DIR, "total_response_templates.json")
_SKELETON_FILE = os.path.join(_BASE_DIR, "skeleton.json")
_TEMPLATES = None
_SKELETON = None


def _load_templates():
    """加载 835 对 cs→sc 模板库：{cs_cmd: {sc, template, reply, notes}}。"""
    global _TEMPLATES
    if _TEMPLATES is None:
        try:
            import json as _json
            _TEMPLATES = _json.load(open(_TEMPLATES_FILE, encoding="utf-8")).get("cs_sc", {})
        except Exception:
            _TEMPLATES = {}
    return _TEMPLATES


def load_skeleton():
    """加载骨架系统（CORE 调度用）：{cs: {frames:[{sc,flag,srv}], reply, notes, src}}。
    骨架优先于模板库（多帧链/flag/帧规则权威来源）。"""
    global _SKELETON
    if _SKELETON is None:
        try:
            import json as _json
            _SKELETON = _json.load(open(_SKELETON_FILE, encoding="utf-8")).get("cs_sc", {})
        except Exception:
            _SKELETON = {}
    return _SKELETON


def get_skeleton(cs_cmd):
    """CORE 查调度：按 cmd 返回骨架条目（无则 None）。"""
    return load_skeleton().get(str(cs_cmd))


def _template_response(req, ctx):
    """TEMPLATE 层：模板命中 → 按 reply 语义响应（yes/template 回模板编码，no 不回包）。
    编码失败/空模板 → 按 schema 生成字段完整的兜底帧（契约 §7 保底不崩）。未命中返回 None。"""
    t = _load_templates().get(str(req.cmd))
    if not t or not t.get("sc"):
        return None
    sc = int(t["sc"])
    if t.get("reply") == "no":
        return CSResponse(reply=False)
    tmpl = t.get("template") or {}
    # 模板值 + schema 补齐：模板往往只给了部分字段，缺的 required 单数 message
    # 会让客户端取到 nil（见 fallback_payload 注释）
    payload = fallback_payload(sc, tmpl)
    if not payload and not tmpl:
        payload = b""   # 全 repeated 的消息，空 payload 就是正确的
    return CSResponse([DownFrame(sc, payload)])


def _skeleton_response(req, ctx):
    """骨架层（CORE 调度）：按骨架帧序列组装响应（多帧链/flag/不回包）。
    帧 payload 来源优先级：帧 hex（配置静态数据）> handler 动态注入 > 模板兜底(0800)。
    未命中返回 None（回落 handler/TEMPLATE 链）。"""
    sk = get_skeleton(req.cmd)
    if sk is None:
        return None
    reply = sk.get("reply", "yes")
    if reply == "no" or not sk.get("frames"):
        return CSResponse(reply=False)
    frames = []
    for f in sk["frames"]:
        sc = int(f["sc"])
        flag = int(f.get("flag", 0))
        srv = f.get("srv")
        # payload：优先帧 hex（骨架静态数据）；否则默认最小帧（handler/上层覆盖）
        payload = bytes.fromhex(f["hex"]) if f.get("hex") else b"\x08\x00"
        frames.append(DownFrame(sc, payload, flag=flag, srv=srv))
    return CSResponse(frames)


# ---------------- 统一入口 ----------------

def handle_request(req, ctx=None):
    """统一入口（CORE 调度）：一个客户端请求 → CSResponse（0..n 帧）。
    处理器链（契约 §4 + 骨架系统）：
      1. CORE_HANDLERS：业务处理器（库驱动，生成帧 payload）
      2. SKELETON：骨架系统（帧结构权威：多帧链/flag/回几个/回不回）
      3. TEMPLATE：835 对模板库（单帧兜底）
      4. DROP：不回包白名单
    骨架是 CORE 的调度依据——handler 决定"数据"，骨架决定"怎么回"。
    未命中返回 None（上层走素材 resp_map / ack0 兜底）。
    """
    if ctx is None:
        ctx = HandlerCtx()
    # 请求级日志（DEBUG 级别，避免高频 cmd 刷盘）
    ctx.log(f"cs_{req.cmd} idx={req.index} srv={req.server_idx} payload={len(req.payload)}B", "DEBUG", cmd=req.cmd, uid=req.uid)
    # 1. CORE_HANDLERS：核心玩法处理器（库驱动，显式注册）
    fn = CORE_HANDLERS.get(req.cmd)
    if fn is not None:
        try:
            resp = fn(req, ctx)
            if resp is not None:
                resp.src = "handler"
            return resp
        except Exception as e:
            ctx.log(f"[middleware] cmd={req.cmd} 处理异常: {e}", "WARN")
            # 契约 §7：库异常 → 回 result=非0 错误码（91xxx 需 flag=1 + 连接级 srv）
            if req.cmd in (91002, 91006, 91014, 91016):
                resp = _error_response(req, ctx, flag=1, srv=ctx.next_chat_srv(req))
            else:
                resp = _error_response(req, ctx)
            resp.src = "handler"
            return resp
    # 2. SKELETON：骨架系统（CORE 调度——帧结构/回几个/回不回）
    sk = get_skeleton(req.cmd)
    if sk is not None:
        if sk.get("reply") == "no" or not sk.get("frames"):
            return CSResponse(reply=False, src="skeleton-drop")   # 骨架明确不回包
        frames = []
        for f in sk["frames"]:
            sc = int(f["sc"])
            # payload 来源优先级：骨架静态 hex（真实素材数据）> 模板值 + schema 补齐 > 纯 schema 补齐
            # （此前这里完全不读 f["hex"]，骨架里的真实数据被丢掉，一律回 0800）
            if f.get("hex"):
                try:
                    payload = bytes.fromhex(f["hex"])
                except ValueError:
                    payload = fallback_payload(sc)
            else:
                tmpl_vals = None
                if not sk.get("schema_missing"):
                    t = _load_templates().get(str(req.cmd))
                    if t and int(t.get("sc") or 0) == sc and t.get("template"):
                        tmpl_vals = t["template"]
                payload = fallback_payload(sc, tmpl_vals)
            frames.append(DownFrame(sc, payload, flag=int(f.get("flag", 0)), srv=f.get("srv")))
        return CSResponse(frames, src="skeleton")
    # 3. TEMPLATE：835 对模板库（无库数据时兜底）
    tr = _template_response(req, ctx)
    if tr is not None:
        tr.src = "template"
        return tr
    # 4. DROP：白名单明确不回包（54300/56002 已在 CORE_HANDLERS）
    if req.cmd in DROP_NO_REPLY:
        return CSResponse(reply=False, src="drop")
    # 5. 未命中 → ack0 兜底（V4.1 策略：回 (cmd+1) result=0 空帧，防客户端卡转圈）
    _ack_sc = (req.cmd + 1) & 0xFFFF if req.cmd <= 0xFFFF else req.cmd + 1
    return CSResponse([DownFrame(_ack_sc, fallback_payload(_ack_sc), flag=0, srv=None)], src="ack0")


if __name__ == "__main__":
    # 自检：构造请求 → 跑全部 CORE_HANDLERS → 校验响应链
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import generator as _gen
    from codec import encode as _enc, decode as _dec
    from account_db import get_db

    ctx = HandlerCtx(db=get_db(), generator=_gen, codec_encode=_enc, codec_decode=_dec,
                     log=lambda *a, **k: print(*a))
    req = CSRequest(cmd=0, payload=b"", index=5, server_idx=700, uid=_gen.DEFAULT_UID)
    ok = 0
    _chat_expect = None  # 91xxx 连接级 srv 期望（V4.1 语义：连接级全局递增）
    for cmd, fn in sorted(CORE_HANDLERS.items()):
        req.cmd = cmd
        resp = handle_request(req, ctx)
        if resp is None:
            print(f"cmd={cmd}: 未处理")
            continue
        raw = resp.to_bytes(req.index, req.server_idx)
        # 校验帧头
        n = 0
        i = 0
        while i + 2 <= len(raw):
            sz = struct.unpack(">H", raw[i:i+2])[0]
            tot = sz + 2
            fcmd = struct.unpack(">H", raw[i+5:i+7])[0]
            fidx = struct.unpack(">H", raw[i+7:i+9])[0]
            fsrv = struct.unpack(">H", raw[i+9:i+11])[0]
            assert fidx == req.index, f"cmd={cmd} idx 未回显: {fidx}"
            if cmd in (91002, 91006, 91014, 91016):
                # 91xxx：连接级 srv 递增（显式 DownFrame.srv）
                _chat_expect = (_chat_expect if _chat_expect is not None else req.server_idx) + 1
                assert fsrv == _chat_expect, f"cmd={cmd} 连接级 srv 错误: {fsrv} 期望 {_chat_expect}"
            else:
                assert fsrv == req.server_idx + n + 1, f"cmd={cmd} srv 错误: {fsrv}"
            assert tot <= len(raw) - i, f"cmd={cmd} 帧长越界"
            n += 1
            i += tot
        assert i == len(raw), f"cmd={cmd} 残留字节"
        print(f"cmd={cmd} -> {'不回包' if not resp.reply else ' -> '.join(map(str, resp.cmds))} ({len(raw)}B {n}帧 idx/srv OK)")
        ok += 1
    print(f"\n自检通过: {ok}/{len(CORE_HANDLERS)} 个处理器")
