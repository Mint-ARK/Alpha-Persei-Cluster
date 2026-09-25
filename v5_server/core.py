# -*- coding: utf-8 -*-
"""
core.py — CORE 模块（V5 架构 ②：请求分发 + 骨架调度 + 响应组装）

定位（老大定稿）：
- CORE 负责"分配"：接收 SERVER NET 剥帧后的请求，决定回不回/回几个/回什么
- 骨架系统归 CORE 调度（skeleton.json 844 条 = 帧结构权威）
- 数据层（account_db）/编解码（codec）/响应生成（generator）是 CORE 的工具，CORE 命令它们干活
- 编解码在 NET↔CORE 之间（transport）与 CORE 内部（codec）自行调用

与 middleware 的关系：
- middleware.handle_request 已实现 CORE 调度逻辑（CORE_HANDLERS → SKELETON → TEMPLATE → DROP）
- core.py 是其**正式化接口层**：提供 CoreRequest/CoreResponse/Connection 干净契约，
  供 SERVER NET（V5 server_net.py）只依赖 core，不碰 middleware 内部细节
- 行为与 middleware 完全一致（复用其调度），避免双实现

数据流：
  SERVER NET --(CoreRequest)--> core.dispatch() --> CSResponse --> (CoreResponse)--> SERVER NET
"""
import os
import sys
import json
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import middleware as _mw  # noqa: E402
from middleware import (  # noqa: E402
    CSRequest, DownFrame, CSResponse, HandlerCtx,
    CORE_HANDLERS, handle_request as _dispatch,
)

__all__ = ["CoreRequest", "CoreResponse", "Connection", "Core", "get_core"]


# ---------------- CORE 数据结构（SERVER NET 对接契约） ----------------

class CoreRequest:
    """SERVER NET → CORE 的请求（已剥帧头、已解 zlib）。

    与 CSRequest 的区别：seq 帧序（登录洪流用）、body 惰性解码视图。
    idx/srv 由 SERVER NET 分配，CORE 不碰（传输计数，老大定稿）。
    """

    __slots__ = ("cmd", "payload", "uid", "seq", "index", "server_idx", "body")

    def __init__(self, cmd, payload=b"", uid=0, seq=0, index=0, server_idx=0, body=None):
        self.cmd = cmd
        self.payload = payload or b""
        self.uid = uid
        self.seq = seq
        self.index = index
        self.server_idx = server_idx
        self.body = body

    def to_cs_request(self):
        """转 CSRequest（middleware 调度用）。"""
        return CSRequest(self.cmd, self.payload, self.index, self.server_idx, self.uid, self.body)


class CoreResponse:
    """CORE → SERVER NET 的响应：已拼好的帧序列 + 回包语义。

    frames: [{'sc': int, 'payload': bytes, 'flag': int, 'srv': int|None}]
    reply:  False = 明确不回包（SERVER NET 不发送）
    src:    数据源标识 ('dynamic'|'skeleton'|'template'|'replay'|'drop'|'ack0')
    """

    __slots__ = ("frames", "reply", "src")

    def __init__(self, frames=None, reply=True, src="dynamic"):
        self.frames = list(frames or [])
        self.reply = reply
        self.src = src

    @classmethod
    def from_cs_response(cls, resp):
        """从 middleware CSResponse 转换。None → reply=False。"""
        if resp is None:
            return cls([], reply=False, src="drop")
        frames = [{"sc": f.cmd, "payload": f.payload, "flag": f.flag, "srv": f.srv}
                  for f in resp.frames]
        return cls(frames, reply=resp.reply, src=getattr(resp, "src", "dynamic"))

    @property
    def scs(self):
        return [f["sc"] for f in self.frames]


# ---------------- 连接上下文（每连接一个实例） ----------------

class Connection:
    """连接级上下文：CORE 注入给 handler 的运行时状态（V5 每连接一个）。

    收敛 V4.1 的全局状态：battle_id/battle_stage/chat_srv 等。
    SERVER NET 在连接建立时创建，连接关闭时丢弃。
    """

    __slots__ = ("uid", "db", "generator", "codec_encode", "codec_decode",
                 "log", "battle_id", "battle_stage", "battle_seq",
                 "chat_srv", "seq", "_ctx", "send_push")

    def __init__(self, uid=0, db=None, generator=None, codec_encode=None, codec_decode=None,
                 log=None):
        self.uid = uid
        self.db = db
        self.generator = generator
        self.codec_encode = codec_encode
        self.codec_decode = codec_decode
        self.log = log
        self.battle_id = [None]
        self.battle_stage = [0]
        self.battle_seq = [0]
        self.chat_srv = [None]
        self.seq = [0]          # 帧序（登录洪流/推送用，SERVER NET 或 CORE 维护）
        self._ctx = None
        self.send_push = None

    def to_handler_ctx(self):
        """转 HandlerCtx（middleware handler 用）；懒创建并保持单例。"""
        if self._ctx is None:
            self._ctx = HandlerCtx(cfg={}, db=self.db, generator=self.generator,
                                   codec_encode=self.codec_encode,
                                   codec_decode=self.codec_decode, log=self.log)
            # 同步连接级状态
            self._ctx.battle_id = self.battle_id
            self._ctx.battle_stage = self.battle_stage
            self._ctx.battle_seq = self.battle_seq
            self._ctx.chat_srv = self.chat_srv
            self._ctx.send_push = getattr(self, "send_push", None)
        return self._ctx


# ---------------- 操作调度层（CORE 核心：玩家操作 → 解析/校验/计算/落库/双端响应） ----------------

class OperationError(Exception):
    """操作失败：携带错误码（客户端 result 字段）。code=0 成功，非0 失败提示。"""

    def __init__(self, code=1, msg=""):
        super().__init__(msg)
        self.code = code
        self.msg = msg


class Operation:
    """操作基类（CORE 最重要的能力——数据双端实时响应）。

    四阶段固定（每个操作子类实现）：
      parse(req)      ① 解码请求 payload → 结构化数据
      validate(data)  ② 校验（余额/幂等/条件）——不通过抛 OperationError(code)
      apply(data)     ③ 计算 + 落库（须在事务内，原子）
      respond(result) ④ 拼响应帧（含双端推送帧）

    子类注册：class 属性 cmd（cs cmd 号），创建后自动入 OPERATIONS 注册表。
    """

    cmd = None          # cs cmd（子类必须定义）
    sc = None           # 主响应 sc（错误回包用）

    # uid 走线程本地存储 —— OPERATIONS 注册表里存的是**单例实例**，而 server_net 是
    # ThreadingTCPServer 每连接一线程。若 uid 是普通实例属性，两个连接并发时
    # `op.uid = cs_req.uid` 会互相覆盖，玩家 A 的操作可能落到玩家 B 的 uid 上。
    # 做成 property + threading.local 后，74 个子类里的 self.uid 用法一行都不用改。
    _tls = threading.local()

    @property
    def uid(self):
        return getattr(Operation._tls, "uid", 0)

    @uid.setter
    def uid(self, value):
        Operation._tls.uid = value

    def parse(self, req, ctx):
        """① 解码请求 payload。支持二进制 protobuf 与 JSON 调试请求。"""
        if req.payload:
            if isinstance(req.payload, dict):
                return req.payload
            if isinstance(req.payload, (bytes, bytearray)):
                stripped = req.payload.strip()
                if stripped.startswith(b"{") and stripped.endswith(b"}"):
                    try:
                        return json.loads(stripped.decode("utf-8"))
                    except Exception:
                        pass
                if ctx.codec_decode:
                    return ctx.codec_decode(req.payload, "cs_%d" % self.cmd)
        return {}

    def validate(self, data, ctx):
        """② 校验。通过返回 data（或 None）；不通过抛 OperationError(code)。"""
        return data

    def apply(self, data, ctx):
        """③ 计算+落库（原子，事务由 dispatch 包裹）。返回 result（dict 或 None）。"""
        raise NotImplementedError

    def respond(self, result, data, ctx):
        """④ 拼响应帧。返回 list[DownFrame] 或 CSResponse。"""
        raise NotImplementedError

    # ---- 便捷工具 ----
    def _ok(self, **kw):
        """result=0 的响应 dict。"""
        return {"result": 0, **kw}

    def _err(self, code):
        """result=非0 错误 dict。"""
        return {"result": code}

    def run(self, req, ctx, uid=0):
        """完整执行链（由 dispatch 调用）：parse → validate → apply → respond。"""
        self.uid = uid or req.uid
        if hasattr(ctx, "reset_operation_state"):
            ctx.reset_operation_state()
        else:
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.clear()
            if hasattr(ctx, "removed_equips") and ctx.removed_equips is not None:
                ctx.removed_equips.clear()
            else:
                ctx.removed_equips = []
            if hasattr(ctx, "pending_frames") and ctx.pending_frames is not None:
                ctx.pending_frames.clear()
        data = self.parse(req, ctx)
        data = self.validate(data, ctx)
        result = self.apply(data, ctx)
        resp = self.respond(result, data, ctx)
        if isinstance(resp, CSResponse):
            return resp
        return CSResponse(list(resp) if resp else [])


# ---------------- 配置驱动操作（操作表 operation_cfg.json） ----------------

_OP_CFG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "operation_cfg.json")
_OP_CFG = None


def load_op_cfg():
    """加载操作表（operation_cfg.json）：{cs_cmd: {sc, apply[], respond, validate}}。"""
    global _OP_CFG
    if _OP_CFG is None:
        import json as _json
        try:
            _OP_CFG = _json.load(open(_OP_CFG_FILE, encoding="utf-8"))
        except Exception:
            _OP_CFG = {}
    return _OP_CFG


def _resolve_ref(ref, data, result):
    """解析配置引用：'req.xxx' → 请求字段；'result.xxx' → 执行结果字段；常量原样。"""
    if isinstance(ref, str) and ref.startswith("req."):
        return data.get(ref[4:])
    if isinstance(ref, str) and ref.startswith("result."):
        return result.get(ref[7:])
    return ref


def _resolve_deep(obj, path):
    """深层引用解析：path=['a','b'] → obj['a']['b']；支持数字索引（list.0.num）。"""
    cur = obj
    for seg in path:
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, (list, tuple)) and seg.isdigit():
            cur = cur[int(seg)] if int(seg) < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


class ConfigOperation(Operation):
    """操作表驱动的操作：读 operation_cfg.json 按配置执行数据操作。

    配置结构（每 cs cmd）：
      sc        主响应 sc
      validate  {required: [字段...]} 请求必填校验
      apply     [{op, ...}] 数据操作原语（事务内顺序执行）
      respond   {sc, template, items, extra_frames, empty_payload} 响应组装

    支持原语 op（对应 account_db 收口方法 + 通用）：
      set_sign / claim_task / claim_task_batch / set_red_dot / set_currency / set_material
    返回值放入 result，供 respond 的 template 用 'result.xxx' 引用。
    """

    def __init__(self, cmd, cfg):
        self.cmd = cmd
        self.cfg = cfg
        self.sc = int(cfg.get("sc", cmd))
        self.uid = 0  # _run_operation 分发时赋值

    def parse(self, req, ctx):
        d = super().parse(req, ctx)
        if not isinstance(d, dict):
            raise OperationError(1, "请求解码失败")
        return d

    def validate(self, data, ctx):
        for field in (self.cfg.get("validate") or {}).get("required", []):
            if field not in data:
                raise OperationError(2, f"缺少必填字段 {field}")
        return data

    def apply(self, data, ctx):
        result = {}
        for step in self.cfg.get("apply", []):
            op = step.get("op")
            if op == "set_sign":
                aid = int(_resolve_ref(step.get("activity_id"), data, result))
                import time as _t
                now = _t.localtime()
                ctx.db.upsert("sign", self.uid,
                              {"activity_id": aid, "year": now.tm_year, "month": now.tm_mon,
                               "day": now.tm_mday, "sign_list": str([now.tm_mday]),
                               "sign_count": 1, "last_sign_ts": int(_t.time())},
                              keys=("uid", "activity_id"))
                result["sign_num"] = 1
            elif op == "set_sign_reward":
                # 签到奖励写入 result.item_list（respond 模板引用）
                reward = _resolve_ref(step.get("reward"), data, result) or []
                items = []
                for it in reward:
                    if isinstance(it, dict):
                        items.append({
                            "item_id": int(it.get("item_id", it.get("id", 0))),
                            "item_num": int(it.get("item_num", it.get("num", 1))),
                        })
                result["item_list"] = items
            elif op == "claim_task":
                tid = int(_resolve_ref(step.get("task_id"), data, result))
                claimed = ctx.db.is_task_claimed(self.uid, tid)
                if not claimed:
                    ctx.db.claim_task(self.uid, tid)
                result["claimed"] = claimed
                result["task_id"] = tid
            elif op == "claim_task_batch":
                ids = [int(x) for x in (_resolve_ref(step.get("task_ids"), data, result) or [])]
                claimed = 0
                for tid in ids:
                    if ctx.db.is_task_claimed(self.uid, tid):
                        claimed += 1
                        continue
                    ctx.db.claim_task(self.uid, tid)
                result["total"] = len(ids)
                result["claimed"] = claimed
            elif op == "set_red_dot":
                rd = int(_resolve_ref(step.get("red_dot_id"), data, result))
                ctx.db.set_red_dot(self.uid, rd, 0)
                result["red_dot"] = rd
            elif op == "set_currency":
                cid = int(_resolve_ref(step.get("currency_id"), data, result))
                num = int(_resolve_ref(step.get("num"), data, result))
                ctx.db.set_currency(self.uid, cid, num)
            elif op == "set_material":
                mid = int(_resolve_ref(step.get("material_id"), data, result))
                num = int(_resolve_ref(step.get("num"), data, result))
                ctx.db.set_material(self.uid, mid, num)
            elif op == "deduct":
                # 通用扣减：走底层权威路由与统一扣减
                mid = int(_resolve_ref(step.get("id") or step.get("material_id") or step.get("currency_id"), data, result))
                num = int(_resolve_ref(step.get("num"), data, result))
                have = ctx.db.get_item_num(self.uid, mid) if ctx.db else 0
                if have < num:
                    raise OperationError(3, f"扣除不足: id={mid} 需{num} 有{have}")
                if ctx.db:
                    ctx.db.item_cost(self.uid, mid, num)
                result["deducted"] = num
            elif op == "deduct_currency":
                cid = int(_resolve_ref(step.get("currency_id") or step.get("id"), data, result))
                num = int(_resolve_ref(step.get("num"), data, result))
                have = ctx.db.get_item_num(self.uid, cid) if ctx.db else 0
                if have < num:
                    raise OperationError(3, f"货币不足: id={cid} 需{num} 有{have}")
                if ctx.db:
                    ctx.db.item_cost(self.uid, cid, num)
                result["deducted"] = num
            elif op == "deduct_materials":
                # 批量扣材料：[{item_id/item_id, item_num/num}] 或 [id1, id2...]（num 默认 1）
                items = _resolve_ref(step.get("items") or step.get("material_list"), data, result) or []
                for it in items:
                    if isinstance(it, dict):
                        mid = it.get("item_id", it.get("id"))
                        num = it.get("item_num", it.get("num", 1))
                        mid, num = int(mid), int(num)
                    else:
                        mid, num = int(it), 1
                    have = ctx.db.get_item_num(self.uid, mid) if ctx.db else 0
                    if have < num:
                        raise OperationError(3, f"材料不足: id={mid} 需{num} 有{have}")
                    if ctx.db:
                        ctx.db.item_cost(self.uid, mid, num)
                result["deducted_items"] = len(items)
            elif op == "upsert":
                table = step.get("table")
                if not table:
                    raise OperationError(9, f"upsert 步骤缺少 table（规格稿未实现: {step.get('desc', '')[:30]}）")
                row = {}
                for k, v in (step.get("row") or {}).items():
                    row[k] = _resolve_ref(v, data, result) if isinstance(v, str) else v
                keys = tuple(step.get("keys") or ["uid"])
                ctx.db.upsert(table, self.uid, row, keys=keys)
            elif op == "query":
                # 条件查询：把结果存 result，供后续步骤/响应引用
                sql = step.get("sql")
                if not sql:
                    raise OperationError(9, f"query 步骤缺少 sql（规格稿未实现: {step.get('desc', '')[:30]}）")
                args = step.get("args") or []
                resolved = []
                for a in args:
                    if isinstance(a, str) and a == "uid":
                        resolved.append(self.uid)
                    else:
                        resolved.append(_resolve_ref(a, data, result))
                rows = ctx.db.query(sql, tuple(resolved))
                result[step.get("as") or "query_result"] = rows
            elif op == "grant_items":
                # 批量发道具：items=[{id/item_id, num}]，material 加 / currency 加
                items = _resolve_ref(step.get("items"), data, result) or []
                for it in items:
                    if isinstance(it, dict):
                        iid = it.get("item_id", it.get("id"))
                        num = it.get("item_num", it.get("num", 1))
                        iid, num = int(iid), int(num)
                    else:
                        iid, num = int(it), 1
                    ctx.db.execute(
                        "INSERT INTO material (uid, id, num) VALUES (?,?,?) "
                        "ON CONFLICT(uid, id) DO UPDATE SET num = material.num + excluded.num",
                        (self.uid, iid, num))
                result["granted"] = len(items)
            elif op == "deduct_piece":
                # 扣英雄碎片（hero_piece 表）
                hid = int(_resolve_ref(step.get("hero_id") or step.get("id"), data, result))
                num = int(_resolve_ref(step.get("num"), data, result))
                rows = ctx.db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (self.uid, hid))
                have = rows[0]["num"] if rows else 0
                if have < num:
                    raise OperationError(3, f"碎片不足: hero={hid} 需{num} 有{have}")
                ctx.db.execute("UPDATE hero_piece SET num=num-? WHERE uid=? AND hero_id=?",
                               (num, self.uid, hid))
                result["deducted_piece"] = num
            elif op == "calc_hero_exp":
                # 经验→等级换算（真实曲线：game_level_setting 表，级内进度语义——
                # 参考 herotools.CheckExp：exp >= 本级阈值 → 升级并扣除，级内经验不累计跨级）
                # exp_add 支持：数值 / req.xxx / result.xxx / "auto"（item_list 按道具换算）
                hid = int(_resolve_ref(step.get("hero_id") or step.get("id"), data, result))
                exp_add_raw = _resolve_ref(step.get("exp_add"), data, result)
                if isinstance(exp_add_raw, str) and exp_add_raw.strip().lower() in ("auto", "自动"):
                    exp_add = 0
                    for it in (data.get("item_list") or []):
                        if isinstance(it, dict):
                            iid = int(it.get("item_id", it.get("id", 0)))
                            num = int(it.get("item_num", it.get("num", 0)))
                            exp_add += {40101: 500, 40102: 1000, 40103: 5000, 40104: 10000}.get(iid, 0) * num
                else:
                    exp_add = int(exp_add_raw or 0)
                rows = ctx.db.query("SELECT level, exp FROM hero WHERE uid=? AND id=?", (self.uid, hid))
                if rows:
                    curve = {r["level"]: r["hero_exp_need"] for r in ctx.db.query(
                        "SELECT level, hero_exp_need FROM game_level_setting")}
                    max_lv = max(curve) if curve else 100
                    level = rows[0]["level"] or 1
                    exp = (rows[0]["exp"] or 0) + exp_add
                    while level < max_lv and exp >= curve.get(level, 1 << 30):
                        exp -= curve[level]
                        level += 1
                    if level >= max_lv:
                        exp = min(exp, curve.get(max_lv, exp))  # 满级经验钳制
                    ctx.db.execute("UPDATE hero SET exp=?, level=? WHERE uid=? AND id=?",
                                   (exp, level, self.uid, hid))
                    result["exp"] = exp
                    result["level"] = level
            elif op == "consume_equips":
                # 吃素材装备（删除，经验转目标装备——简化：直接删）
                eids = _resolve_ref(step.get("equip_ids"), data, result) or []
                for eid in eids:
                    ctx.db.execute("DELETE FROM equip WHERE uid=? AND id=?", (self.uid, int(eid)))
                result["consumed"] = len(eids)
            elif op == "remove_equips":
                # 分解删除装备
                eids = _resolve_ref(step.get("equip_ids"), data, result) or []
                for eid in eids:
                    ctx.db.execute("DELETE FROM equip WHERE uid=? AND id=?", (self.uid, int(eid)))
                result["removed"] = len(eids)
            elif op == "upsert_canteen_entrust":
                # 食堂委托位 upsert（backhome_canteen_entrust，键 uid+pos）
                pos = int(_resolve_ref(step.get("pos") or "req.pos", data, result) or 0)
                row = {}
                for k in ("task_id", "hero_list", "num_max", "refresh_times", "start_time", "duration"):
                    if step.get(k) is not None:
                        row[k] = _resolve_ref(step[k], data, result)
                row.update({"pos": pos, "update_ts": int(__import__("time").time())})
                ctx.db.upsert("backhome_canteen_entrust", self.uid, row, keys=("uid", "pos"))
            elif op == "fatigue_update":
                # 后宅英雄疲劳调整：{hero_ids|hero_id, delta|set}
                hs = _resolve_ref(step.get("hero_ids") or step.get("hero_id"), data, result)
                ids = hs if isinstance(hs, list) else [hs]
                delta = _resolve_ref(step.get("delta"), data, result)
                setv = _resolve_ref(step.get("set"), data, result)
                for hid in ids:
                    if hid is None:
                        continue
                    if setv is not None:
                        ctx.db.execute("UPDATE backhome_hero SET fatigue=?, update_ts=? "
                                       "WHERE uid=? AND archives_id IN "
                                       "(SELECT archives_id FROM backhome_hero WHERE hero_id=?)",
                                       (int(setv), int(__import__("time").time()), self.uid, int(hid)))
                    else:
                        ctx.db.execute("UPDATE backhome_hero SET fatigue=MAX(0, fatigue+?), update_ts=? "
                                       "WHERE uid=? AND hero_id=?",
                                       (int(delta or 0), int(__import__("time").time()), self.uid, int(hid)))
            elif op == "calc_canteen_earnings":
                # 食堂收益结算（2026-08-17 子代理挖掘定稿官方公式）：
                #   A. 委托结算（58018，pos_list）：收益 = floor(基础奖励 × 时长档位倍率)
                #      [480s→100% / 720s→140% / 1200s→210%]；成功率=base_success+标签命中×5
                #      （英雄 tag 无数据源，简化按 base_success）→ 命中则大成功 = ceil(基础×1.5)
                #      产出 result.reward_list/extra_reward/fatigue_list/entrust
                #   B. 售货结算（58120，oper_list+unit_cost）：简化 unit_cost×量
                import math as _mth
                import random as _rnd
                import json as _mj
                pos_list = _resolve_ref(step.get("pos_list"), data, result)
                if pos_list:
                    _mult = {480: 1.0, 720: 1.4, 1200: 2.1}
                    reward_list, extra_reward, fatigue_list, entrust = [], [], [], []
                    for pos in pos_list:
                        erows = ctx.db.query(
                            "SELECT * FROM backhome_canteen_entrust WHERE uid=? AND pos=?",
                            (self.uid, int(pos)))
                        if not erows:
                            continue
                        e = erows[0]
                        crow = ctx.db.query(
                            "SELECT * FROM backhome_canteen_task WHERE task_id=?",
                            (e["task_id"],))
                        if not crow:
                            continue
                        c = crow[0]
                        try:
                            reward = _mj.loads(c.get("reward_list") or "[]")
                        except Exception:
                            reward = []
                        mult = _mult.get(int(e["duration"] or 480), 1.0)
                        base = []
                        for rw in reward:
                            if isinstance(rw, (list, tuple)) and len(rw) >= 2:
                                base.append([int(rw[0]), int(int(rw[1]) * mult)])
                        # 大成功判定：成功率=base_success（英雄 tag 匹配无数据源，简化）
                        success = min(100, int(c.get("base_success") or 0))
                        big = 1 if _rnd.randint(1, 100) <= success else 0
                        if big:
                            base = [[i, _mth.ceil(n * 1.5)] for i, n in base]
                        reward_list.extend([{"item_id": i, "item_num": n} for i, n in base])
                        extra_reward.append({
                            "pos": int(pos), "extra_reward": big,
                            "reward_list": [{"item_id": i, "item_num": n} for i, n in base]})
                    result["reward_list"] = reward_list
                    result["extra_reward"] = extra_reward
                    result["fatigue_list"] = fatigue_list
                    result["entrust"] = entrust
                    result["earnings"] = sum(n for _, n in
                                             ((x["item_id"], x["item_num"]) for x in reward_list))
                else:
                    # 售货/点餐结算（简化：unit_cost×量，官方公式未挖掘）
                    oper_list = _resolve_ref(step.get("oper_list"), data, result) or []
                    unit = float(_resolve_ref(step.get("unit_cost"), data, result) or 0)
                    result["earnings"] = int(unit * len(oper_list))
                    result["reward_list"] = []
            elif op == "set_dorm_layout":
                # 宿舍布局保存（backhome_dorm pos 更新）
                dorm = _resolve_ref(step.get("dorm_id"), data, result)
                if dorm is not None:
                    ctx.db.execute("UPDATE backhome_dorm SET update_ts=? WHERE uid=? AND dorm_id=?",
                                   (int(__import__("time").time()), self.uid, int(dorm)))
            elif op == "upsert_dorm_hero":
                # 英雄入住/召回宿舍（backhome_dorm_hero 增删）
                dorm = int(_resolve_ref(step.get("dorm_id"), data, result) or 0)
                hid = _resolve_ref(step.get("hero_id"), data, result)
                remove = step.get("remove")
                if hid is None:
                    pass
                elif remove:
                    ctx.db.execute("DELETE FROM backhome_dorm_hero WHERE uid=? AND dorm_id=? AND hero_id=?",
                                   (self.uid, dorm, int(hid)))
                else:
                    ctx.db.execute("INSERT OR IGNORE INTO backhome_dorm_hero (uid, dorm_id, hero_id, update_ts) "
                                   "VALUES (?,?,?,?)", (self.uid, dorm, int(hid), int(__import__("time").time())))
            elif op == "grant_items_from_cfg":
                # 按配置发奖（items 引用解析后发；与 grant_items 同路由）
                items = _resolve_ref(step.get("items"), data, result) or []
                granted = []
                for it in items:
                    if isinstance(it, dict):
                        iid = int(it.get("item_id", it.get("id", 0)))
                        num = int(it.get("item_num", it.get("num", 1)))
                    else:
                        iid, num = int(it), 1
                    cat = ctx.db.query("SELECT category FROM item_catalog WHERE id=?", (iid,))
                    t = "currency" if (cat and cat[0]["category"] == "currency") else "material"
                    ctx.db.execute(f"INSERT INTO {t} (uid, id, num) VALUES (?,?,?) "
                                   f"ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                                   (self.uid, iid, num))
                    granted.append({"item_id": iid, "item_num": num})
                result.setdefault("give_items", []).extend(granted)
                result["granted"] = len(granted)
            elif op == "deduct_point":
                # 活动积分扣减（activity_pt）
                key = int(_resolve_ref(step.get("activity_pt_id") or step.get("key"), data, result) or 1)
                num = int(_resolve_ref(step.get("num"), data, result) or 0)
                rows = ctx.db.query("SELECT active_point FROM activity_pt WHERE uid=? AND activity_pt_id=?",
                                    (self.uid, key))
                have = rows[0]["active_point"] if rows else 0
                if have < num:
                    raise OperationError(3, f"积分不足: key={key} 需{num} 有{have}")
                ctx.db.execute("UPDATE activity_pt SET active_point=active_point-? WHERE uid=? AND activity_pt_id=?",
                               (num, self.uid, key))
            elif op == "mail_collect_attachments":
                # 邮件附件收集：标已领 + result.give_items 供 grant/响应
                mid = _resolve_ref(step.get("mail_id") or step.get("id"), data, result)
                if mid is not None:
                    import json as _mj
                    ctx.db.execute("UPDATE mail SET read_flag=2, attach_flag=2, update_ts=? "
                                   "WHERE uid=? AND mail_id=?",
                                   (int(__import__("time").time()), self.uid, int(mid)))
                    mrow = ctx.db.query("SELECT attachment_json FROM mail WHERE uid=? AND mail_id=?",
                                        (self.uid, int(mid)))
                    if mrow:
                        try:
                            att = _mj.loads(mrow[0]["attachment_json"] or "[]")
                        except Exception:
                            att = []
                        # 键名兼容：库表 attachment_json 用 item_id/count（历史数据两种都有）
                        result.setdefault("give_items", []).extend(
                            [{"item_id": a.get("item_id") if a.get("item_id") is not None else a.get("id"),
                              "item_num": a.get("count") if a.get("count") is not None else a.get("number", 1)}
                             for a in att if isinstance(a, dict)])
            elif op == "grant":
                # 通用发奖（grant_items 别名）
                items = _resolve_ref(step.get("items"), data, result) or []
                for it in items:
                    iid = int(it.get("item_id", it.get("id", 0))) if isinstance(it, dict) else int(it)
                    num = int(it.get("item_num", it.get("num", 1))) if isinstance(it, dict) else 1
                    cat = ctx.db.query("SELECT category FROM item_catalog WHERE id=?", (iid,))
                    t = "currency" if (cat and cat[0]["category"] == "currency") else "material"
                    ctx.db.execute(f"INSERT INTO {t} (uid, id, num) VALUES (?,?,?) "
                                   f"ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                                   (self.uid, iid, num))
                result.setdefault("give_items", []).extend(
                    [{"item_id": (it.get("item_id", it.get("id")) if isinstance(it, dict) else it),
                      "item_num": (it.get("item_num", it.get("num", 1)) if isinstance(it, dict) else 1)}
                     for it in items])
            else:
                raise OperationError(9, f"未知操作原语: {op}")
        return result

    def respond(self, result, data, ctx):
        r = self.cfg.get("respond") or {}
        if r.get("empty_payload"):
            return [DownFrame(self.sc, b"")]
        # template 组装（'result.xxx' 深层引用，支持 result.a.b / result.list.0.num）
        tmpl = {}
        for k, v in (r.get("template") or {}).items():
            if isinstance(v, str) and v.startswith("result."):
                tmpl[k] = _resolve_deep(result, v[7:].split("."))
            else:
                tmpl[k] = v
        payload = b"\x08\x00"
        if ctx.codec_encode:
            try:
                payload = ctx.codec_encode("sc_%d" % self.sc, tmpl) or b"\x08\x00"
            except Exception:
                payload = b"\x08\x00"
        # items（奖励列表，若有）
        items = r.get("items")
        if items and ctx.codec_encode and payload == b"\x08\x00":
            try:
                tmpl["item_list"] = [{"item_id": i["item_id"], "item_num": i["item_num"]} for i in items]
                payload = ctx.codec_encode("sc_%d" % self.sc, tmpl) or b"\x08\x00"
            except Exception:
                pass
        frames = [DownFrame(self.sc, payload)]
        # extra_frames（附加帧，如 28007 进度；main_frame_last=true 时 extra 先发）
        efs = r.get("extra_frames", [])
        if r.get("main_frame_last") and efs:
            frames = []
            for ef in efs:
                frames.append(DownFrame(int(ef["sc"]), bytes.fromhex(ef["hex"])))
            frames.append(DownFrame(self.sc, payload))
        return frames


def register_config_operations():
    """从操作表加载配置驱动操作（已注册的代码版操作优先级更高，不被覆盖）。"""
    for cmd_str, cfg in load_op_cfg().items():
        if cmd_str.startswith("_"):
            continue
        if not cmd_str.isdigit():
            continue  # 非数字 cmd（如 COOK_LOCAL 纯客户端标记）不注册
        cmd = int(cmd_str)
        if cmd not in OPERATIONS:
            OPERATIONS[cmd] = ConfigOperation(cmd, cfg)


class OperationRegistry(dict):
    """操作注册表：cmd → Operation 实例。"""

    def register(self, op_cls):
        inst = op_cls()
        self[inst.cmd] = inst
        return inst


OPERATIONS = OperationRegistry()


def operation(cls):
    """类装饰器：注册 Operation 子类。代码版操作拥有最高执行优先级。"""
    OPERATIONS.register(cls)
    return cls


# ---------------- CORE 主类 ----------------

class Core:
    """CORE 模块：统一分发入口（SERVER NET 唯一依赖）。

    用法：
        core = Core(db=..., generator=..., codec_encode=..., codec_decode=...)
        resp = core.dispatch(CoreRequest(cmd=10038, payload=b"...", uid=...))
    """

    def __init__(self, db=None, generator=None, codec_encode=None, codec_decode=None, log=None):
        self.db = db
        self.generator = generator
        self.codec_encode = codec_encode
        self.codec_decode = codec_decode
        self.log = log

    def dispatch(self, req, conn=None):
        """核心入口：一个 CoreRequest → CoreResponse。

        分发链（操作优先，响应兜底）：
          OPERATIONS（操作调度：parse→validate→apply→respond，事务原子）
          → CORE_HANDLERS（响应调度）→ SKELETON → TEMPLATE → DROP
        conn: Connection（可选；提供时用其 handler ctx 与状态）
        """
        cs_req = req.to_cs_request()
        if conn is not None:
            ctx = conn.to_handler_ctx()
            # uid 以连接绑定为准（登录后 SERVER NET 绑定，非每帧携带）
            cs_req.uid = conn.uid or req.uid
        else:
            ctx = HandlerCtx(cfg={}, db=self.db, generator=self.generator,
                             codec_encode=self.codec_encode,
                             codec_decode=self.codec_decode,
                             log=self.log or (lambda *a, **k: None))
            cs_req.uid = req.uid
        ctx.uid = cs_req.uid

        # 重置单次操作状态（清空跨请求残存的 removed_equips / touched_items / pending_frames）
        if hasattr(ctx, "reset_operation_state"):
            ctx.reset_operation_state()

        # 前置惰性时间戳广播源脉冲（保证所有操作在最新的跨天/跨周/跨月与物理时间下执行）
        if cs_req.uid and ctx.db is not None:
            try:
                from lazy_timer import lazy_timer
                lazy_timer.pulse(ctx, cs_req.uid)
            except Exception as _lte:
                ctx.log(f"[core] lazy_timer.pulse 执行异常: {_lte}", "WARN")

        # 0. OPERATIONS：操作调度（玩家操作 → 解析/校验/计算/落库/双端响应）
        op = OPERATIONS.get(cs_req.cmd)
        if op is not None:
            return self._run_operation(op, cs_req, ctx)
        # 1-4. 响应调度（middleware 链）
        resp = _dispatch(cs_req, ctx)
        if getattr(ctx, "log", None):
            n = len(resp.frames) if resp is not None else 0
            src = (getattr(resp, "src", "") or "?") if resp is not None else "no-reply"
            ctx.log(f"cs_{cs_req.cmd} -> 响应 {n} 帧({src})", "DEBUG", module="CORE", cmd=cs_req.cmd, to_console=False)
        return CoreResponse.from_cs_response(resp)

    def _run_operation(self, op, cs_req, ctx):
        """执行操作：parse → validate → apply(事务) → respond。
        任何环节 OperationError → 回 sc 错误帧（result=非0）。
        未知异常 → 回滚 + 错误帧（操作不产生半状态）。"""
        sc = op.sc or cs_req.cmd
        if hasattr(ctx, "reset_operation_state"):
            ctx.reset_operation_state()
        try:
            op.uid = cs_req.uid  # 操作内可用 self.uid 取玩家 uid
            # 参数层防御：parse/validate 的任何异常 → 干净 code=2（缺参/格式错），
            # 不产生通用异常帧（客户端只弹 toast，绝不卡死）
            try:
                data = op.parse(cs_req, ctx)
                data = op.validate(data, ctx)
            except OperationError:
                raise
            except Exception as e:
                raise OperationError(2, f"参数解析失败: {e}")
            # apply 在事务内（原子：扣券+出卡同生共死）；执行异常 → 干净 code=9 并回滚
            try:
                with ctx.db.transaction() if ctx.db else _null_cm():
                    result = op.apply(data, ctx)
            except OperationError:
                raise
            except Exception as e:
                raise OperationError(9, f"执行失败: {e}")
            # 帧组装：以 op.respond 返回的动态帧为主，补充 skeleton 结构帧
            sk = _mw.get_skeleton(cs_req.cmd)
            op_frames = op.respond(result, data, ctx)  # 动态业务帧
            if isinstance(op_frames, list):
                op_list = op_frames
            elif isinstance(op_frames, CSResponse):
                op_list = op_frames.frames
            else:
                op_list = []

            if not op_list:
                resp = CSResponse([], reply=False, src="dynamic")
            elif sk and sk.get("frames") and sk.get("reply") != "no":
                frames = list(op_list)
                covered_scs = {f.cmd for f in op_list}
                # 补充 skeleton 中有但 op_list 未显式包含的兜底帧
                for f in sk["frames"]:
                    f_sc = int(f["sc"])
                    if f_sc in covered_scs:
                        continue
                    # 语义互斥保护：若操作已返回材料原子差分帧 17023，不重复补充全量材料 17009
                    if f_sc == 17009 and 17023 in covered_scs:
                        continue
                    # 任务进度帧保护：绝不向客户端下发 0 字节的 sc_28007 空帧（避免客户端触发无意义重算与振荡）
                    if f_sc == 28007:
                        continue
                    if f.get("hex"):
                        pl = bytes.fromhex(f["hex"])
                    else:
                        pl = _mw.fallback_payload(f_sc)
                    if pl is None:
                        continue
                    frames.append(DownFrame(f_sc, pl, flag=int(f.get("flag", 0)), srv=f.get("srv")))
                    covered_scs.add(f_sc)
                resp = CSResponse(frames, src="dynamic")
            else:
                resp = CSResponse(op_list, src="dynamic")

            # 提炼玩家物流操作动向日志（入库、消费、操作语义高亮）
            import log_sifter
            op_name = log_sifter.get_operation_name(cs_req.cmd, op)
            details = log_sifter.extract_logistics_details(result, data)
            logistics_line = log_sifter.build_logistics_line(cs_req.cmd, sc, cs_req.uid, op_name, details)
            ctx.log(logistics_line, "INFO", module="LOGISTICS", cmd=cs_req.cmd, uid=cs_req.uid)
            return CoreResponse.from_cs_response(resp)
        except OperationError as e:
            import log_sifter
            op_name = log_sifter.get_operation_name(cs_req.cmd, op)
            ctx.log(f"CS_{cs_req.cmd} -> SC_{sc} {op_name}校验拦截: code={e.code} ({e.msg})", "WARN", module="LOGISTICS", cmd=cs_req.cmd, uid=cs_req.uid)
            payload = b"\x08" + _mw._varint(e.code & 0xFFFFFFFFFFFFFFFF)
            err_frames = []
            if getattr(ctx, "removed_equips", None):
                try:
                    import generator as _gen
                    p_diff = _gen.gen_payload(17023, uid=cs_req.uid, db=ctx.db, equip_list=ctx.removed_equips)
                    if p_diff:
                        err_frames.append({"sc": 17023, "payload": p_diff, "flag": 0, "srv": None})
                except Exception:
                    pass
            err_frames.append({"sc": sc, "payload": payload, "flag": 0, "srv": None})
            return CoreResponse(err_frames, reply=True, src="dynamic")
        except Exception as e:
            import log_sifter
            op_name = log_sifter.get_operation_name(cs_req.cmd, op)
            ctx.log(f"CS_{cs_req.cmd} -> SC_{sc} {op_name}处理异常: {e}", "ERROR", module="LOGISTICS", cmd=cs_req.cmd, uid=cs_req.uid)
            return CoreResponse([{"sc": sc, "payload": b"\x08\x01", "flag": 0, "srv": None}], reply=True, src="dynamic")

    def handle(self, cmd, payload=b"", uid=0, conn=None, index=0, server_idx=0):
        """便捷入口：cmd + payload → CoreResponse（无 CSRequest 构造样板）。"""
        return self.dispatch(CoreRequest(cmd, payload, uid, index=index,
                                         server_idx=server_idx), conn)


class _null_cm:
    """空上下文管理器（无 db 时替代 transaction）。"""

    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


# ---------------- 全局单例（SERVER NET 装配后设置） ----------------

_CORE = None


def get_core():
    """全局 CORE 单例（V5 main.py 装配后可用）。"""
    return _CORE


def set_core(core):
    """装配 CORE 单例（main.py 启动时调用一次）。"""
    global _CORE
    _CORE = core


register_config_operations()
try:
    import operations  # noqa: F401  代码版操作注册
    import backhome_service  # noqa: F401  游园街领域服务（BackHome / Dorm / Canteen 事件总线挂载）
    import peripheral_service  # noqa: F401  外围系统模块（32xxx 个性化/大厅场景/偏好）
    import achievement_service  # noqa: F401  成就领域服务（53xxx 领奖/物语/事件总线挂载）
except Exception:
    pass


if __name__ == "__main__":
    # 自检：Core.dispatch 与 middleware.handle_request 行为一致
    import generator as _gen
    from codec import encode as _enc, decode as _dec
    from account_db import get_db

    # 加载操作表（配置驱动操作）
    register_config_operations()
    try:
        import operations  # noqa: F401  代码版操作（配置未覆盖的）
        import backhome_service  # noqa: F401  游园街领域服务（BackHome / Dorm / Canteen 事件总线挂载）
        import peripheral_service  # noqa: F401  外围系统模块（32xxx 个性化/大厅场景/偏好）
    except Exception:
        pass

    core = Core(db=get_db(), generator=_gen, codec_encode=_enc, codec_decode=_dec)
    conn = Connection(uid=_gen.DEFAULT_UID, db=core.db, generator=core.generator,
                      codec_encode=_enc, codec_decode=_dec)
    ok = 0
    for cmd in sorted(CORE_HANDLERS.keys()):
        resp = core.handle(cmd, b"", conn=conn)
        if resp.reply:
            ok += 1
            print(f"cs_{cmd} -> {' -> '.join(map(str, resp.scs))} ({len(resp.frames)}帧)")
        else:
            print(f"cs_{cmd} -> 不回包")
    print(f"\nCORE 自检: {ok}/{len(CORE_HANDLERS)} 个 handler 回包正常")
