# -*- coding: utf-8 -*-
"""
codec.py — 中间件：schema 驱动的 protobuf payload 编解码（双向）

职责（架构分层 ②）：
- decode(payload, msg_name) → dict（复用 decode_schema，字段名为键）
- encode(msg_name, dict) → bytes（按字段编号/类型编码，支持 varint/bytes/string/message/repeated）
- 与 account_db（③ 数据层）+ generator（④ 响应生成器）配合，实现"库存储 → 动态生成响应"

schema 来源：lua_proto_messages.json（3061 条消息），经 decode_schema.load_schema 解析为
SCHEMA[msg_name] = {field_num: {name, number, type, label, ref}}。
"""
import os
import struct
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
ROOT = os.path.dirname(BASE)   # 仓库根（v5_server 上一级）
legacy_analysis_path = os.path.join(ROOT, "analysis_scripts", "解码尝试")
if os.path.exists(legacy_analysis_path) and legacy_analysis_path not in sys.path:
    sys.path.insert(0, legacy_analysis_path)

import decode_schema as ds  # noqa: E402

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if not _SCHEMA_READY:
        ds.ensure_schema()
        # 补充芯片协议 (50006, 50007, 50016, 50017)
        ds.SCHEMA.setdefault("cs_50006", {
            1: {"name": "kernel_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "secondary_id", "number": 2, "type": "uint32", "label": "required", "ref": None},
            3: {"name": "oper", "number": 3, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_50007", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_50016", {
            1: {"name": "kernel_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_50017", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })

        # 补充/消除因果观测（WarChess 49xxx）协议歧义
        ds.SCHEMA["p49_chess_map_net_rec"] = {
            1: {"name": "activity_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "chapter", "number": 2, "type": "uint32", "label": "required", "ref": None},
            3: {"name": "chapter_info", "number": 3, "type": "message", "label": "repeated", "ref": "chapter_net_rec"},
        }
        if "sc_49001" in ds.SCHEMA and 1 in ds.SCHEMA["sc_49001"]:
            ds.SCHEMA["sc_49001"][1]["ref"] = "p49_chess_map_net_rec"

        ds.SCHEMA.setdefault("cs_49002", {
            1: {"name": "chapter_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_49004", {
            1: {"name": "path", "number": 1, "type": "message", "label": "repeated", "ref": "position"},
        })
        ds.SCHEMA.setdefault("sc_49005", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_49006", {
            1: {"name": "pos", "number": 1, "type": "message", "label": "required", "ref": "position"},
            2: {"name": "param", "number": 2, "type": "uint32", "label": "optional", "ref": None},
            3: {"name": "type", "number": 3, "type": "uint32", "label": "optional", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_49007", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_49008", {})
        ds.SCHEMA.setdefault("sc_49009", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_49015", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_49017", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_49024", {
            1: {"name": "type", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_49025", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })

        # 斗舞/舞蹈培训生（crickets 58186/58187/58164/58165）字段权威映射对齐
        ds.SCHEMA["crickets_action_net_rec"] = {
            1: {"name": "atk_style_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "skill_id", "number": 2, "type": "uint32", "label": "required", "ref": None},
            3: {"name": "index_list", "number": 3, "type": "uint32", "label": "repeated", "ref": None},
        }
        ds.SCHEMA["crickets_round_net_rec"] = {
            1: {"name": "round_index", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "attack_user_action", "number": 2, "type": "message", "label": "required", "ref": "crickets_action_net_rec"},
            3: {"name": "defend_user_action", "number": 3, "type": "message", "label": "required", "ref": "crickets_action_net_rec"},
            4: {"name": "attack_user_score", "number": 4, "type": "message", "label": "required", "ref": "crickets_round_score_net_rec"},
            5: {"name": "defend_user_score", "number": 5, "type": "message", "label": "required", "ref": "crickets_round_score_net_rec"},
        }
        # 管理员猫咪探索（弥弥尔探索 / 远征挂机收益 p67 67001~67013）
        ds.SCHEMA["explore_event_net_rec"] = {
            1: {"name": "time", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "address_id", "number": 2, "type": "uint32", "label": "required", "ref": None},
            3: {"name": "content_id", "number": 3, "type": "uint32", "label": "required", "ref": None},
        }
        ds.SCHEMA["explore_mimir_skill_net_rec"] = {
            1: {"name": "skill_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "skill_level", "number": 2, "type": "uint32", "label": "required", "ref": None},
        }
        ds.SCHEMA["explore_mimir_net_rec"] = {
            1: {"name": "mimir_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "skill", "number": 2, "type": "message", "label": "repeated", "ref": "explore_mimir_skill_net_rec"},
        }
        ds.SCHEMA["explore_state_net_rec"] = {
            1: {"name": "area_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "mimir_id", "number": 2, "type": "uint32", "label": "required", "ref": None},
            3: {"name": "start_time", "number": 3, "type": "uint32", "label": "required", "ref": None},
            4: {"name": "stop_time", "number": 4, "type": "uint32", "label": "required", "ref": None},
            5: {"name": "target_explore_hour", "number": 5, "type": "uint32", "label": "required", "ref": None},
            6: {"name": "explore_event", "number": 6, "type": "message", "label": "repeated", "ref": "explore_event_net_rec"},
        }
        ds.SCHEMA["sc_67001"] = {
            1: {"name": "mimir", "number": 1, "type": "message", "label": "repeated", "ref": "explore_mimir_net_rec"},
            2: {"name": "explore_queue", "number": 2, "type": "message", "label": "repeated", "ref": "explore_state_net_rec"},
            3: {"name": "weekly_time", "number": 3, "type": "uint32", "label": "required", "ref": None},
            4: {"name": "daily_time", "number": 4, "type": "uint32", "label": "required", "ref": None},
            5: {"name": "weekly_reward_state", "number": 5, "type": "uint32", "label": "required", "ref": None},
            6: {"name": "weekly_scene_open", "number": 6, "type": "uint32", "label": "required", "ref": None},
            7: {"name": "total_explore_c", "number": 7, "type": "uint32", "label": "required", "ref": None},
        }
        ds.SCHEMA.setdefault("cs_67002", {
            1: {"name": "mimir_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "skill_id", "number": 2, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_67003", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_67004", {
            1: {"name": "area_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "hour_time", "number": 2, "type": "uint32", "label": "required", "ref": None},
            3: {"name": "mimir_id", "number": 3, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_67005", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_67006", {
            1: {"name": "area_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_67007", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "event_id", "number": 2, "type": "uint32", "label": "optional", "ref": None},
            3: {"name": "reward_list", "number": 3, "type": "message", "label": "repeated", "ref": "item_net_rec"},
        })
        ds.SCHEMA.setdefault("cs_67008", {})
        ds.SCHEMA.setdefault("sc_67009", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
            2: {"name": "reward_list", "number": 2, "type": "message", "label": "repeated", "ref": "item_net_rec"},
        })
        ds.SCHEMA.setdefault("cs_67010", {
            1: {"name": "mimir_id", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("sc_67011", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        ds.SCHEMA.setdefault("cs_67012", {})
        ds.SCHEMA.setdefault("sc_67013", {
            1: {"name": "result", "number": 1, "type": "uint32", "label": "required", "ref": None},
        })
        _SCHEMA_READY = True

# wire types
WT_VARINT = 0
WT_64BIT = 1
WT_LEN = 2
WT_32BIT = 5

VARINT_TYPES = {"int32", "uint32", "int64", "uint64", "bool", "enum", "sint32", "sint64"}
LEN_TYPES = {"string", "bytes"}
ZIGZAG_TYPES = {"sint32", "sint64"}                    # 必须 zigzag，否则负数值错
FIXED64_TYPES = {"double", "fixed64", "sfixed64"}      # wire type 1
FIXED32_TYPES = {"float", "fixed32", "sfixed32"}       # wire type 5

# 键名别名：调用方（operations/middleware/core）内部普遍用 item_id/item_num 表示条目，
# 而 schema 里通用条目记录 item_net_rec/currency_net_rec/piece_info 的字段名是 id/num。
# 名字对不上时 encode 原先直接 continue → 编出空 message（静默丢奖励）。
# 这里做一次别名回退：仅在按原名查不到字段时才尝试，故对真有 item_id 字段的 5 个消息无影响。
_KEY_ALIASES = {
    "item_id": ("id", "item_id"),
    "item_num": ("num", "number", "count", "item_num"),
    "id": ("item_id", "id"),
    "num": ("item_num", "number", "count", "num"),
    "count": ("num", "number", "count"),
    "number": ("num", "count", "number"),
    "hero_id": ("id", "hero_id"),
    "level": ("lv", "level"),
    "completed": ("complete_flag", "completed"),
    "round": ("cur_round", "round"),
}

# 丢字段可见化：静默 continue 会把协议错误藏起来，这里累计并去重告警一次。
DROPPED_KEYS = {}


def _warn_drop(msg_name, key):
    """记录一次「字段名在 schema 中不存在，值被丢弃」。同一 (消息,键) 只告警一次。"""
    sig = (msg_name, str(key))
    if sig in DROPPED_KEYS:
        DROPPED_KEYS[sig] += 1
        return
    DROPPED_KEYS[sig] = 1
    try:
        from logger import log as _log
        _log(f"[codec] {msg_name} 无字段 {key!r}，该值被丢弃（键名与 schema 不符？）", "WARN")
    except Exception:
        pass


def _varint(v):
    v = v & 0xFFFFFFFFFFFFFFFF if v < 0 else v
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out += bytes([b | 0x80])
        else:
            out += bytes([b])
            return out


def _tag(field_num, wt):
    return _varint((field_num << 3) | wt)


def _encode_len(b):
    return _varint(len(b)) + b


def _zigzag(v, bits=64):
    """sint32/sint64 的 zigzag 编码：(n << 1) ^ (n >> bits-1)。"""
    v = int(v)
    if v < 0:
        return (-v * 2) - 1
    return v * 2


def _encode_value(fdef, value):
    """按字段类型编码单个值（fdef 含 type/label/ref）。返回 None 表示跳过（类型不匹配）。

    注意：本函数产出的字节必须与 _wt_of 给出的 wire type 一致，否则帧畸形、
    客户端从该字段起整段解析错位。float/double/fixed/sint 以前双错（值走 varint、
    tag 标 WT_LEN），已在此统一。
    """
    t = fdef.get("type", "")
    if t in FIXED64_TYPES:
        try:
            if t == "double":
                return struct.pack("<d", float(value or 0))
            if t == "sfixed64":
                return struct.pack("<q", int(value or 0))
            return struct.pack("<Q", int(value or 0) & 0xFFFFFFFFFFFFFFFF)
        except Exception:
            return struct.pack("<Q", 0)
    if t in FIXED32_TYPES:
        try:
            if t == "float":
                return struct.pack("<f", float(value or 0))
            if t == "sfixed32":
                return struct.pack("<i", int(value or 0))
            return struct.pack("<I", int(value or 0) & 0xFFFFFFFF)
        except Exception:
            return struct.pack("<I", 0)
    if t in VARINT_TYPES:
        if isinstance(value, bool):
            v = int(value)
        elif isinstance(value, str):
            try:
                v = int(value)
            except ValueError:
                return None  # 嵌套 message 被 decode 成 hex 串但 schema 误标 varint——跳过
        else:
            v = int(value) if value is not None else 0
        if t in ZIGZAG_TYPES:
            return _varint(_zigzag(v))
        # Google Protobuf 规范：负数 int32/int64 必须符号扩展为 64 位 10 字节 varint
        # 若编为 5 字节 0xFFFFFFFF0F，ToLua / C++ Protobuf 会将其解析为 4294967295 并触发 Value out of range
        if v < 0:
            v &= 0xFFFFFFFFFFFFFFFF
        elif t in ("uint32", "int32", "enum"):
            v &= 0xFFFFFFFF
        elif t in ("uint64", "int64", "sint64"):
            v &= 0xFFFFFFFFFFFFFFFF
        return _varint(v)
    if t == "string":
        return _encode_len(str(value).encode("utf-8"))
    if t == "bytes":
        if isinstance(value, str):
            try:
                b = bytes.fromhex(value)
            except ValueError:
                b = value.encode("utf-8")   # 不是 hex 串就按文本处理（原先直接抛）
        elif isinstance(value, (bytes, bytearray)):
            b = bytes(value)
        else:
            b = str(value).encode("utf-8")
        return _encode_len(b)
    if t == "message":
        ref = fdef.get("ref") or fdef.get("message_type")
        if isinstance(value, str):
            # decode 输出的嵌套 message 是 hex 串——直接当已编码 bytes（roundtrip 无损）
            try:
                return _encode_len(bytes.fromhex(value))
            except ValueError:
                pass
        sub = encode(ref, value) if ref else b""
        return _encode_len(sub)
    # 未知类型 → 尝试 varint（_wt_of 同样返回 WT_VARINT，保持一致）
    return _varint(int(value) if value is not None else 0)


def encode(msg_name, obj):
    """按消息 schema 编码 dict → payload bytes。obj 键支持字段名或数字编号。"""
    _ensure_schema()
    fields = ds.SCHEMA.get(msg_name, {})
    if not fields:
        # 无 schema：对象本身就是 bytes/hex
        if isinstance(obj, (bytes, bytearray)):
            return bytes(obj)
        if isinstance(obj, str):
            try:
                return bytes.fromhex(obj)
            except ValueError:
                return obj.encode("utf-8")
        return b""
    # 建 name→num 映射
    name2num = {f.get("name"): num for num, f in fields.items()}
    out = b""
    for key, value in obj.items() if isinstance(obj, dict) else []:
        num = key if isinstance(key, int) else name2num.get(key)
        if num is None and isinstance(key, str):
            # 别名回退（item_id→id / item_num→num / count→num ...）
            for alias in _KEY_ALIASES.get(key, ()):
                if alias in name2num:
                    num = name2num[alias]
                    break
        if num is None and isinstance(key, str) and key.startswith("f") and key[1:].isdigit():
            num = int(key[1:])  # schema 外字段（decode 兜底名 f<num>）
        if num is None:
            _warn_drop(msg_name, key)   # 不再静默丢弃：留下可查的告警
            continue
        num = int(num)
        fdef = fields.get(str(num)) or fields.get(num)
        if fdef is None:
            # schema 外字段：按值类型推断 wire type
            if isinstance(value, bool):
                out += _tag(num, WT_VARINT) + _varint(int(value))
            elif isinstance(value, int):
                out += _tag(num, WT_VARINT) + _varint(value)
            elif isinstance(value, str):
                out += _tag(num, WT_LEN) + _encode_len(value.encode("utf-8"))
            continue
        label = fdef.get("label", "")
        if label == "repeated":
            if isinstance(value, (list, tuple)):
                for item in value:
                    ev = _encode_value(fdef, item)
                    if ev is not None:
                        out += _tag(num, _wt_of(fdef)) + ev
            elif value is not None and not isinstance(value, (list, tuple)):
                ev = _encode_value(fdef, value)
                if ev is not None:
                    out += _tag(num, _wt_of(fdef)) + ev
        else:
            ev = _encode_value(fdef, value)
            if ev is not None:
                out += _tag(num, _wt_of(fdef)) + ev
    return out


def _wt_of(fdef):
    """字段的 wire type。必须与 _encode_value 的产出严格对应。"""
    t = fdef.get("type", "")
    if t in FIXED64_TYPES:
        return WT_64BIT
    if t in FIXED32_TYPES:
        return WT_32BIT
    if t in VARINT_TYPES:
        return WT_VARINT
    if t in LEN_TYPES or t == "message":
        return WT_LEN
    return WT_VARINT   # 未知类型：与 _encode_value 的 varint 兜底保持一致


def decode(payload, msg_name):
    """payload（hex 或 bytes）→ dict（字段名为键）。复用 decode_schema。自动解压 zlib 压缩包。"""
    _ensure_schema()
    raw = bytes.fromhex(payload) if isinstance(payload, str) else bytes(payload)
    if raw and raw[:2] == b"\x78\x9c":
        try:
            import zlib
            raw = zlib.decompress(raw)
        except Exception:
            pass
    return ds.decode_payload(raw, msg_name)


def roundtrip_check(msg_name, payload):
    """双向验证：decode → encode 是否与原始 payload 字节一致。返回 (一致?, 解码结果)。"""
    dec = decode(payload, msg_name)
    re = encode(msg_name, dec)
    orig = bytes.fromhex(payload) if isinstance(payload, str) else bytes(payload)
    return re == orig, dec


if __name__ == "__main__":
    ds.ensure_schema()
    # 自检：用素材真实 payload 做 roundtrip
    import json
    import struct
    rows = [json.loads(l) for l in open(os.path.join(
        ROOT, "archive", "20260806_全流程_热更新后", "complete_replay", "tcp", "tcpfwd_rebuilt.jsonl"),
        encoding="utf-8")]
    tested = {}
    for r in rows:
        raw = bytes.fromhex(r["hex"])
        if r["dir"] == "U->C" and len(raw) >= 7:
            cmd = struct.unpack(">H", raw[5:7])[0]
            if cmd in (17009, 15009, 23009, 11013, 11015, 10051) and cmd not in tested:
                try:
                    ok, dec = roundtrip_check("sc_%d" % cmd, raw[11:])
                    tested[cmd] = (ok, len(raw[11:]), dec)
                except Exception as e:
                    tested[cmd] = ("ERR", str(e)[:50], None)
        if len(tested) >= 5:
            break
    for cmd, (ok, n, dec) in sorted(tested.items()):
        if ok:
            print(f"sc_{cmd} ({n}B): roundtrip 无损 ✓")
        else:
            print(f"sc_{cmd} ({n}B): roundtrip 不一致 ✗ -> {dec}")
    # encode 手动测试：构造 17009
    enc = encode("sc_17009", {"material_list": [{"item_id": 20002, "item_num": 157}]})
    print("手动 encode sc_17009:", enc.hex())
