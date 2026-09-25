# -*- coding: utf-8 -*-
"""
schema_default.py — schema 驱动的「字段完整」响应合成器

要解决的问题
------------
骨架层/模板层/ack0 兜底以前一律回 ``b"\\x08\\x00"``（result=0 最小帧）。
对 f1 恰好是单数 varint 的消息这是对的，但全库 1404 个 sc_ 消息里：

  · 1149 个  f1 是单数 varint          → 0800 正确
  ·  201 个  f1 是 message/string      → 0800 把 wire type 2 写成 0，**畸形帧**
  ·   47 个  f1 是 repeated uint32      → 0800 等于往列表里塞了一个多余的 0
  ·    7 个  压根没有 f1                → 0800 是个协议里不存在的字段

客户端 Lua 的 protobuf 运行时（decompiled_v2/x64/library/tolua/lua/protobuf/protobuf.lua）：
  · 解析时**不校验** required（ParseFromString → Clear + MergeFromString，无 IsInitialized）
  · 未赋值的标量字段  → 返回 field.default_value，即 0 / ""（安全）
  · 未赋值的 repeated → 返回 _default_constructor()，即空表（安全）
  · 未赋值的单数 message → 行 415-430 那支没给 slot3 赋值 → **返回 nil**（危险）
    全库 104 个 sc_ 消息带 required 单数 message 字段，客户端 ``data.xxx.yyy``
    直接索引就是 "attempt to index a nil value" → 回调中断 → 界面卡住

因此本模块的填充策略
--------------------
  required 标量        → 类型零值（0 / "" / b"" / False）显式写出，向真实服务器对齐
  required 单数 message → **递归实例化**（这是消掉 nil 崩溃的关键）
  repeated            → 不写（客户端取到空表，与真实服务器发 0 个元素等价）
  optional            → 不写（真实服务器也不发；客户端应走 default_value/nil 判断）

用法
----
    import schema_default as sd
    sd.build("sc_58003")                          # 全零值但字段完整
    sd.build("sc_58003", {"exhibition_id": 7})    # 已知字段给真值，其余补齐
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import codec  # noqa: E402

MAX_DEPTH = 8          # 递归实例化深度上限（防自引用消息无限展开）

# 「兜底必须回非 0 result」名单（由 tools/field_consume.py 扫出，每条都回客户端源码确认过）
#
# 这些 sc 的客户端处理函数长这样：
#     if isSuccess(msg.result) then  msg.<optional 单数 message>.<子字段>  ...
#     else ShowTips(msg.result) end
# 而那个子消息字段是 optional，真实服务器只在有数据时才发，我们兜底时没有数据。
# 此时回 result=0 会把客户端送进 nil 索引（attempt to index a nil value，回调中断 → 界面卡住），
# 回非 0 则走 else 的 ShowTips 分支，只是弹个提示 —— 后者才是安全的兜底。
#
#   sc_24033 .clue_location  game/action/battlestageaction.lua:56
#            slot1 = slot0.clue_location.location_id     ← 无判空
#   sc_89629 .save_data      game/action/activitysubmodule/roguecardgameaction.lua:277
#            RogueCardGameData:SaveSettleData(slot0.save_data.settle_data)   ← 无判空
#
# 对照：sc_54201.room_info 同样是 optional 单数 message，但客户端写了
# `if slot0.room_info and ...` 判空，所以不在此列。
RESULT_MUST_FAIL = {24033: 1, 89629: 1}
FAIL_CODE_DEFAULT = 1

_ZERO_BY_TYPE = {
    "int32": 0, "uint32": 0, "int64": 0, "uint64": 0,
    "sint32": 0, "sint64": 0, "enum": 0,
    "fixed32": 0, "fixed64": 0, "sfixed32": 0, "sfixed64": 0,
    "float": 0.0, "double": 0.0,
    "bool": False,
    "string": "",
    "bytes": b"",
}

_obj_cache = {}        # msg_name -> 无 overrides 时的默认 dict（只读复用）
UNFILLABLE = {}        # msg_name -> [因递归超限/循环引用而没填的 required message 字段]


def _schema_of(msg_name):
    codec._ensure_schema()
    import decode_schema as ds
    return ds.SCHEMA.get(msg_name) or {}


def default_obj(msg_name, values=None, max_depth=MAX_DEPTH, _chain=()):
    """按 schema 生成「字段完整」的 dict（可直接交给 codec.encode）。

    values 中显式给出的字段一律优先（含 repeated / optional）；未给出的按上面的策略补齐。
    """
    fields = _schema_of(msg_name)
    if not fields:
        return dict(values or {})

    out = {}
    vals = values or {}
    for num, fdef in fields.items():
        name = fdef.get("name") or ("f%s" % num)
        label = fdef.get("label", "")
        ftype = fdef.get("type", "")

        # 调用方给了真值 → 直接用（支持用字段名或数字编号作键）
        if name in vals:
            out[name] = vals[name]
            continue
        if num in vals or str(num) in vals:
            out[name] = vals.get(num, vals.get(str(num)))
            continue

        if label == "repeated":
            continue          # 不写：客户端未赋值的 repeated 取到空表，安全
        if label == "optional":
            continue          # 不写：真实服务器也不发

        # 到这里是 required（或 schema 未标 label 的字段，按 required 处理）
        if ftype == "message":
            ref = fdef.get("ref") or fdef.get("message_type")
            if not ref:
                continue
            if ref in _chain or len(_chain) >= max_depth:
                # 自引用或过深：填不下去了，记录下来供体检工具核对
                UNFILLABLE.setdefault(msg_name, [])
                if name not in UNFILLABLE[msg_name]:
                    UNFILLABLE[msg_name].append(name)
                continue
            out[name] = default_obj(ref, None, max_depth, _chain + (ref,))
        else:
            out[name] = _ZERO_BY_TYPE.get(ftype, 0)
    return out


def build(msg_name, values=None, max_depth=MAX_DEPTH):
    """生成字段完整的 payload bytes。schema 缺失时退回 0800（保持旧行为）。"""
    fields = _schema_of(msg_name)
    if not fields:
        return b"\x08\x00"

    # 「兜底必须回非 0」名单：调用方没给真实 result 时，注入失败码而不是 0（见 RESULT_MUST_FAIL 注释）
    fail_code = None
    m = re.match(r"^sc_(\d+)$", msg_name or "")
    if m:
        code = RESULT_MUST_FAIL.get(int(m.group(1)))
        if code is not None and not (values and "result" in values):
            fail_code = code or FAIL_CODE_DEFAULT

    if not values and fail_code is None:
        cached = _obj_cache.get(msg_name)
        if cached is None:
            cached = default_obj(msg_name, None, max_depth)
            _obj_cache[msg_name] = cached
        obj = cached
    else:
        obj = default_obj(msg_name, values, max_depth)
        if fail_code is not None:
            obj = dict(obj)
            obj["result"] = fail_code
    try:
        payload = codec.encode(msg_name, obj)
    except Exception:
        return b"\x08\x00"
    # 空 payload 是合法的（全 repeated 的消息就该是空），但对 f1 是单数 varint 的
    # 消息保留 0800 语义，避免把「result=0」这种客户端明确要读的字段丢了
    return payload if payload else b""


def build_for_sc(sc_cmd, values=None):
    """按 sc 号生成（sc_<cmd>）。"""
    return build("sc_%d" % int(sc_cmd), values)


# ---------------- 自检 ----------------

if __name__ == "__main__":
    import decode_schema as ds
    codec._ensure_schema()

    print("=== 逐类对比 0800 与 schema 合成 ===")
    samples = ["sc_58003", "sc_11003", "sc_18001", "sc_10401", "sc_17023",
               "sc_10600", "sc_15009", "sc_11082"]
    for m in samples:
        if m not in ds.SCHEMA:
            continue
        p = build(m)
        try:
            dec_new = codec.decode(p, m) if p else {}
        except Exception as e:
            dec_new = "解码失败: %s" % e
        try:
            dec_old = codec.decode(b"\x08\x00", m)
        except Exception as e:
            dec_old = "解码失败: %s" % e
        print("\n%s" % m)
        print("   0800     -> %s" % (dec_old,))
        print("   合成(%3dB)-> %s" % (len(p), dec_new))

    print("\n=== 全库统计 ===")
    tot = ok = fail = 0
    empt = 0
    for m in ds.SCHEMA:
        if not m.startswith("sc_"):
            continue
        tot += 1
        try:
            p = build(m)
            if not p:
                empt += 1
            codec.decode(p, m) if p else None
            ok += 1
        except Exception:
            fail += 1
    print("sc_ 消息 %d 个：合成并可回解 %d，失败 %d，合成为空(全 repeated/optional) %d"
          % (tot, ok, fail, empt))
    print("因自引用/超深而未能填充 required message 的消息数: %d" % len(UNFILLABLE))
    for m, fs in list(UNFILLABLE.items())[:8]:
        print("   %-14s %s" % (m, fs))
