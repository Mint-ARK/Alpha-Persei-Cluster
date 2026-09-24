#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用 protobuf schema 驱动解码器（深空之眼 / AetherGazer 网络协议逆向）。

从反编译 Lua protobuf 定义提取出的 schema（lua_proto_messages.json）驱动，
按 protobuf wire format 递归解码二进制帧，把字段号映射为可读字段名。

设计要点：
  - 索引：{消息名小写: {字段号: 字段定义}}
  - wire type 决定原始值的读取方式（varint / 64bit / length-delimited / 32bit），
    不依赖 schema 声明的数字类型；schema 只用来取名、区分 string/bytes、递归 message。
  - 未知字段保留数字名 f<字段号>，不丢数据。
  - repeated 字段收集为列表。
  - string 字段按 UTF-8 解码，不可读时保留 hex。
  - depth 上限 12 防递归爆炸；单字段数据 > 64KB 截断并标记 "_truncated"。
  - 所有解析包 try/except，单字段失败不中断整体。

用法：
  python decode_schema.py <bin文件路径或hex字符串> <消息名> [--out 输出.json]
  python decode_schema.py --batch <hex清单.txt> <消息名> [--out out.json]
"""

import argparse
import json
import os
import re
import struct
import sys

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
MAX_DEPTH = 12          # 递归深度上限，超限返回 {"_truncated": True}
MAX_FIELD_BYTES = 65536  # 单字段 length-delimited 数据上限，超限截断

# protobuf wire types
WIRE_VARINT = 0  # int32/uint32/int64/uint64/bool/enum ...
WIRE_64BIT = 1   # fixed64/sfixed64/double
WIRE_LEN = 2     # length-delimited：string/bytes/嵌套 message/打包 repeated
WIRE_32BIT = 5   # fixed32/sfixed32/float

# 默认 schema 路径：同级 lua_proto_messages.json（优先）或 ../analysis_scripts/协议分析/lua_proto_messages.json
_LOCAL_SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lua_proto_messages.json")
_ANALYSIS_SCHEMA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "analysis_scripts", "协议分析", "lua_proto_messages.json",
)
_FALLBACK_SCHEMA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "协议分析", "lua_proto_messages.json",
)

def _find_default_schema():
    for p in (_LOCAL_SCHEMA, _ANALYSIS_SCHEMA, _FALLBACK_SCHEMA):
        if os.path.exists(p):
            return p
    return _LOCAL_SCHEMA

DEFAULT_SCHEMA_PATH = _find_default_schema()

# 全局 schema 索引：{消息名小写: {字段号: 字段定义}}
SCHEMA = {}
SCHEMA_LOADED_FROM = None


# ---------------------------------------------------------------------------
# schema 加载
# ---------------------------------------------------------------------------
def load_schema(json_path=None):
    """从 JSON 文件加载消息 schema。

    返回 {消息名小写: {字段号: 字段定义}}。消息名重复时后者覆盖前者。
    """
    path = json_path or DEFAULT_SCHEMA_PATH
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    index = {}
    for msg in data.get("messages") or []:
        name = (msg.get("name") or "").strip().lstrip(".").lower()
        if not name:
            continue
        fields = {}
        for fd in msg.get("fields") or []:
            try:
                num = int(fd["number"])
            except (KeyError, TypeError, ValueError):
                continue
            fields[num] = fd
        if name in index:
            if len(fields) > len(index[name]):
                index[name] = fields
            else:
                for k, v in fields.items():
                    if k not in index[name]:
                        index[name][k] = v
        else:
            index[name] = fields
    return index


def ensure_schema(json_path=None):
    """确保全局 SCHEMA 已加载；可指定覆盖路径。"""
    global SCHEMA, SCHEMA_LOADED_FROM
    path = json_path or DEFAULT_SCHEMA_PATH
    if SCHEMA_LOADED_FROM == path and SCHEMA:
        return SCHEMA
    if not os.path.exists(path):
        raise FileNotFoundError("schema 文件不存在: %s" % path)
    SCHEMA = load_schema(path)
    SCHEMA_LOADED_FROM = path
    return SCHEMA


# ---------------------------------------------------------------------------
# wire format 基础读取
# ---------------------------------------------------------------------------
def _read_varint(data, offset):
    """读取一个 varint（LEB128），返回 (值, 新偏移)。

    最多 10 字节（64bit 上限），超长或数据截断抛 ValueError。
    """
    value = 0
    shift = 0
    n = len(data)
    i = offset
    while i < n and shift < 70:
        b = data[i]
        value |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return value, i
        shift += 7
    raise ValueError("varint 过长或数据截断")


def _read_fixed(data, offset, size):
    """按 little-endian 读取固定宽度整数（size=4 或 8），返回 (值, 新偏移)。"""
    if offset + size > len(data):
        raise ValueError("fixed 字段数据不足")
    return int.from_bytes(data[offset:offset + size], "little"), offset + size


def _read_bytes(data, offset, length):
    """读取指定长度的字节切片，返回 (切片, 新偏移)。"""
    if offset + length > len(data):
        raise ValueError("length-delimited 字段越界")
    return data[offset:offset + length], offset + length


def _try_utf8(chunk):
    """尝试 UTF-8 解码；失败则返回 hex 字符串。"""
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        return chunk.hex()


def _interp_varint(fdef, value):
    """按 schema 字段类型解释 varint 原始值。

    encode 侧已支持 zigzag/fixed（codec.py），这里做对称还原：
    sint* → 反 zigzag；int32/int64/enum 为负时补符号；bool → True/False。
    fdef 为 None（无 schema 字段）时保持原始无符号值。
    """
    t = fdef.get("type") if fdef else None
    if t in ("sint32", "sint64"):
        return (value >> 1) ^ -(value & 1)
    if t in ("int32", "int64", "enum"):
        # 负数以 64 位补码编码，还原符号位
        if value > 0x7FFFFFFFFFFFFFFF:
            value -= 1 << 64
        elif t == "int32" and value > 0x7FFFFFFF and value <= 0xFFFFFFFF:
            value -= 1 << 32
        return value
    if t == "bool":
        return bool(value)
    return value


def _decode_length_delimited(fdef, chunk, depth, truncated=False):
    """解码 length-delimited 字段内容。

    fdef 为 None 时按未知字段处理（尽量 UTF-8，不可读保留 hex）。
    message 类型按 ref 递归解码，ref 查不到时内部字段全部回退 fN。
    """
    ftype = fdef["type"] if fdef else None
    if ftype == "message":
        ref = (fdef.get("ref") or "").strip().lstrip(".").lower()
        value = decode_payload(chunk, ref, depth)
        if truncated and isinstance(value, dict):
            value["_truncated"] = True
        return value
    if ftype == "string":
        return _try_utf8(chunk)
    if ftype == "bytes":
        return chunk.hex()
    # 未知类型 / 无 schema 字段：优先按字符串展示，不可读保留 hex
    return _try_utf8(chunk)


def _to_bytes(hex_or_bytes):
    """把 hex 字符串（可含空格/逗号/0x 前缀）或 bytes/bytearray 转成 bytes。"""
    if isinstance(hex_or_bytes, bytes):
        return hex_or_bytes
    if isinstance(hex_or_bytes, bytearray):
        return bytes(hex_or_bytes)
    text = re.sub(r"[\s,;]+", "", str(hex_or_bytes).strip())
    if text.lower().startswith("0x"):
        text = text[2:]
    if not text:
        return b""
    if len(text) % 2:
        text = "0" + text  # 容错奇数长度的 hex
    return bytes.fromhex(text)


# ---------------------------------------------------------------------------
# 核心解码
# ---------------------------------------------------------------------------
def decode_payload(hex_or_bytes, msg_name, depth=0):
    """按 schema 递归解码一个 protobuf 消息 payload。

    hex_or_bytes: hex 字符串（可含空格、换行）或 bytes。
    msg_name:     消息名（大小写不敏感）；查不到时按未知消息解码，
                  内部字段全部回退为 f<字段号>。
    depth:        内部递归深度，调用者一般无需传。
    返回：字段名 -> 值的 dict；解析失败时附 "_errors" 列表。
    """
    data = _to_bytes(hex_or_bytes)
    if depth > MAX_DEPTH:
        return {"_truncated": True}

    key = (msg_name or "").strip().lstrip(".").lower()
    fields = SCHEMA.get(key, {})

    result = {}
    errors = []
    offset = 0
    n = len(data)
    while offset < n:
        field_num = 0
        try:
            tag, offset = _read_varint(data, offset)
            if tag == 0:
                # 非法 tag（字段号 0），跳过 1 字节防止死循环
                offset += 1
                continue
            field_num = tag >> 3
            wire_type = tag & 7

            fdef = fields.get(field_num)
            name = fdef["name"] if fdef else "f%d" % field_num
            repeated = bool(fdef and fdef.get("label") == "repeated")

            if wire_type == WIRE_VARINT:
                value, offset = _read_varint(data, offset)
                value = _interp_varint(fdef, value)
            elif wire_type == WIRE_64BIT:
                value, offset = _read_fixed(data, offset, 8)
                if fdef and fdef.get("type") == "double":
                    value = struct.unpack("<d", struct.pack("<Q", value))[0]
            elif wire_type == WIRE_32BIT:
                value, offset = _read_fixed(data, offset, 4)
                if fdef and fdef.get("type") == "float":
                    value = struct.unpack("<f", struct.pack("<I", value))[0]
            elif wire_type == WIRE_LEN:
                length, offset = _read_varint(data, offset)
                if offset + length > n:
                    raise ValueError(
                        "字段 %d length=%d 超出数据边界" % (field_num, length))
                truncated = length > MAX_FIELD_BYTES
                take = min(length, MAX_FIELD_BYTES)
                chunk = data[offset:offset + take]
                offset += length  # 完整跳过该字段（截断时丢弃超出部分）
                value = _decode_length_delimited(fdef, chunk, depth + 1, truncated)
            else:
                # 未知 wire type（含旧式 group 3/4），无法确定边界，跳过 1 字节
                errors.append("字段 %d 未知 wire_type=%d" % (field_num, wire_type))
                offset += 1
                continue
        except Exception as exc:
            errors.append("字段 %d 解析失败: %s" % (field_num, exc))
            break

        if repeated:
            result.setdefault(name, []).append(value)
        else:
            result[name] = value

    if errors:
        result["_errors"] = errors
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    """命令行入口：单帧解码或 --batch 批量解码。"""
    parser = argparse.ArgumentParser(
        prog="decode_schema",
        description="通用 protobuf schema 驱动解码器（深空之眼网络协议）",
    )
    parser.add_argument(
        "payload",
        help="hex 字符串或二进制文件路径；--batch 模式下为 hex 清单 txt 文件路径",
    )
    parser.add_argument(
        "msg_name",
        help="消息名（大小写不敏感）",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="批量模式：hex 清单每行一条 hex（可含空格/换行，程序内 strip）",
    )
    parser.add_argument(
        "--out",
        metavar="输出.json",
        help="把解码结果写入 JSON 文件（否则打印到 stdout）",
    )
    parser.add_argument(
        "--schema",
        metavar="路径",
        help="覆盖默认的 lua_proto_messages.json 路径",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ensure_schema(args.schema)

    if args.batch:
        results = []
        with open(args.payload, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(decode_payload(line, args.msg_name))
                except Exception as exc:
                    results.append({"_error": str(exc), "_raw": line})
        output = results
    elif os.path.isfile(args.payload):
        # 参数是已存在的文件 → 按二进制文件读取
        with open(args.payload, "rb") as fh:
            raw = fh.read()
        output = decode_payload(raw, args.msg_name)
    else:
        # 否则按 hex 字符串处理
        try:
            output = decode_payload(args.payload, args.msg_name)
        except Exception as exc:
            print("解码失败: %s" % exc, file=sys.stderr)
            return 1

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("已写入 %s" % args.out)
    else:
        print(text)
    return 0


# 模块被 import 时尝试预加载默认 schema（失败不报错，CLI 会再 ensure）
if __name__ == "__main__":
    try:
        ensure_schema()
    except Exception:
        pass
    sys.exit(main())
