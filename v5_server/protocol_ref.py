# -*- coding: utf-8 -*-
"""深空之眼 登录协议工具（分析用）

依据 analysis_scripts/docs/02_登录协议.md：
- 客户端上行包（Protocol.Pack，小端）：
    u32 len(9+payload) | u8 ver(8) | u32 serverIdx(0) | u16 index | u16 cmd | payload(protobuf)
- payload 为标准 protobuf wire format（仅需 uint32/string/bool 类型）
- 服务器下行可能压缩：zlib（ICSharpCode.SharpZipLib InflaterInputStream == zlib inflate）
"""
import struct
import zlib
from typing import Dict, Any, Tuple, Optional, List

# ---------- protobuf wire format（最小实现） ----------

WIRE_VARINT = 0
WIRE_I64 = 1
WIRE_LEN = 2
WIRE_I32 = 5


def _write_varint(out: bytearray, value: int) -> None:
    value &= 0xFFFFFFFFFFFFFFFF
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def pb_encode(fields: Dict[int, Any]) -> bytes:
    """按字段号编码。值类型：int→varint(uint32)，str→len-delimited，bytes→len-delimited。"""
    out = bytearray()
    for num in sorted(fields.keys()):
        val = fields[num]
        if isinstance(val, int):
            _write_varint(out, (num << 3) | WIRE_VARINT)
            _write_varint(out, val)
        elif isinstance(val, (bytes, bytearray)):
            _write_varint(out, (num << 3) | WIRE_LEN)
            _write_varint(out, len(val))
            out += val
        elif isinstance(val, str):
            b = val.encode("utf-8")
            _write_varint(out, (num << 3) | WIRE_LEN)
            _write_varint(out, len(b))
            out += b
        else:
            raise TypeError(f"unsupported field type: {type(val)}")
    return bytes(out)


def pb_decode(data: bytes) -> Dict[int, Any]:
    """解码为 {字段号: 值}。varint→int，len-delimited→bytes。"""
    result: Dict[int, Any] = {}
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        num = key >> 3
        wire = key & 7
        if wire == WIRE_VARINT:
            val, pos = _read_varint(data, pos)
            result[num] = val
        elif wire == WIRE_LEN:
            length, pos = _read_varint(data, pos)
            result[num] = data[pos:pos + length]
            pos += length
        elif wire == WIRE_I64:
            result[num] = data[pos:pos + 8]
            pos += 8
        elif wire == WIRE_I32:
            result[num] = data[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire} at field {num}")
    return result


# ---------- 包打包 / 解包 ----------

def pack_message(cmd: int, payload: bytes, index: int = 1, server_idx: int = 0, ver: int = 8) -> bytes:
    """客户端上行包（实测确认，X64DBG 抓包 2026-08-01）：
    物理布局 u32 len | u8 ver | u32 cmd | u16 index | u16 serverIdx | payload（头 13B）
    len 字段 = 9 + payload（从 ver 字节到包尾，与 Lua WriteUint32(9+#payload) 一致）
    例：实测数组长 174，len=170（9+161），ver=8，cmd=0x2736=10038，index=0，serverIdx=0
    """
    body = bytearray()
    body += struct.pack("<I", 9 + len(payload))  # u32 len 字段（不含 len 自身）
    body += struct.pack("<B", ver)               # u8 ver
    body += struct.pack("<I", cmd)               # u32 cmd
    body += struct.pack("<H", index)             # u16 index
    body += struct.pack("<H", server_idx)        # u16 serverIdx
    body += payload
    return bytes(body)


class Packet:
    def __init__(self, cmd: int, index: int = 0, server_idx: int = 0,
                 payload: bytes = b"", is_compress: bool = False,
                 decompressed_len: Optional[int] = None, raw: bytes = b""):
        self.cmd = cmd
        self.index = index
        self.server_idx = server_idx
        self.payload = payload
        self.is_compress = is_compress
        self.decompressed_len = decompressed_len
        self.raw = raw

    def decode(self) -> Dict[int, Any]:
        return pb_decode(self.payload)

    def __repr__(self):
        return (f"<Packet cmd={self.cmd} idx={self.index} srv={self.server_idx} "
                f"compress={self.is_compress} payload={len(self.payload)}B>")


def unpack_server_message(data: bytes, compressed: bool = True) -> Packet:
    """解析服务器下行包。

    依据 XServer.PacketParser.ParseTCP（登录主连接 PackParser 假定同构）：
        offset 0      u8   isCompress 标志（==1/2 压缩）
        offset 1-2    u16  Cmd
        offset 3-6    u32  [压缩时] 解压后长度
        offset 3|7    payload（总长 - 3 或 - 7）
    注意：该函数接收"已被外层 TCP 帧切好的单包"（不含 u16 总长前缀，或调用方自行剥离）。
    """
    if len(data) < 3:
        raise ValueError(f"server packet too short: {len(data)}B")
    is_compress = data[0]
    cmd = struct.unpack_from("<H", data, 1)[0]
    header_len = 3
    decompressed_len = None
    if is_compress in (1, 2):
        if len(data) < 7:
            raise ValueError("compressed packet too short")
        decompressed_len = struct.unpack_from("<I", data, 3)[0]
        header_len = 7
    payload = data[header_len:]
    if is_compress in (1, 2):
        if not compressed:
            raise RuntimeError("packet is compressed but compression disabled")
        payload = zlib.decompress(payload)
    return Packet(cmd=cmd, payload=payload, is_compress=is_compress in (1, 2),
                  decompressed_len=decompressed_len, raw=data)


def build_server_packet(cmd: int, payload: bytes, is_compress: bool = False,
                        include_size: bool = True) -> bytes:
    """伪造服务器回包（Phase 5 用）。include_size=True 时加 u16 总长前缀（对应 ParseTCP 读法）。"""
    if is_compress:
        comp = zlib.compress(payload)
        head = struct.pack("<BHI", 1, cmd, len(payload))  # flag + cmd + 解压后长度
        body = head + comp
    else:
        body = struct.pack("<BH", 0, cmd) + payload
    if include_size:
        return struct.pack("<H", len(body)) + body
    return body


# ---------- 登录协议字段 ----------

def cs_10038(channel_id: int, account: str, timestamp: int, token: str,
             device_id: str = "", app_id: str = "channel_internal_app_id",
             platform_type: int = 0) -> bytes:
    return pb_encode({
        1: channel_id,
        2: account,
        3: timestamp,
        4: token,
        5: device_id,
        6: app_id,
        7: platform_type,
    })


def cs_10042(user_id: str, gstoken: str, timestamp: int, account: str,
             channel_id: int = 0, server_id: int = 0, login_type: int = 0,
             platform_type: int = 0, device_id: str = "unknown",
             client_vs: str = "", resource_vs: str = "", master_channel_id: int = 0,
             app_id: str = "channel_internal_app_id", b_game_id: int = 8264,
             b_game_base_id: int = 109036, language: str = "zh_cn",
             resolution: str = "1080x2400", **extra) -> bytes:
    fields: Dict[int, Any] = {
        1: channel_id, 2: server_id, 3: account, 4: user_id,
        5: timestamp, 6: gstoken, 7: login_type, 8: platform_type,
        9: device_id, 10: client_vs, 21: resource_vs,
        18: master_channel_id, 19: app_id, 27: b_game_base_id,
        28: b_game_id, 34: language, 42: resolution,
    }
    # 可选设备指纹（留空则服务端可能不校验）
    field_map = {
        "os_vs": 11, "phone": 12, "mac": 13, "imei": 14, "idfa": 15,
        "distinct_id": 16, "oaid": 17, "sub_id": 20, "device_model": 22,
        "zone_offset": 23, "b_sdk_udid": 24, "b_sdk_uid": 25,
        "b_tour_indicator": 26, "b_ad_channel_id": 29, "ram": 30, "rom": 31,
        "cpu_hardware": 32, "network": 33, "graph_device_id": 35,
        "graph_device_name": 36, "graph_memory_size": 37,
        "graph_device_vendor": 38, "graph_device_vendor_id": 39,
        "graph_device_version": 40, "is_localization": 41, "gaid": 43,
    }
    for k, num in field_map.items():
        if k in extra and extra[k] is not None:
            fields[num] = extra[k]
    return pb_encode(fields)


if __name__ == "__main__":
    # 自检：roundtrip（头 13B = u32len + u8ver + u32cmd + u16index + u16serverIdx；len 字段 = 9+payload）
    p = pack_message(10038, cs_10038(1, "test_acct", 1700000000, "tok"), index=1)
    assert struct.unpack_from("<I", p, 0)[0] == 9 + len(p) - 13  # len 字段
    assert struct.unpack_from("<I", p, 5)[0] == 10038            # cmd u32 @ offset5
    assert struct.unpack_from("<H", p, 9)[0] == 1                # index u16 @ offset9
    assert struct.unpack_from("<H", p, 11)[0] == 0               # serverIdx u16 @ offset11
    # 伪造服务端回包 roundtrip（zlib 压缩）
    sp = build_server_packet(10039, pb_encode({1: 0, 2: 1}), is_compress=True)
    sp_body = sp[2:]  # 剥离 u16 长度
    pk = unpack_server_message(sp_body)
    assert pk.cmd == 10039 and pk.decode()[1] == 0
    print("protocol.py self-check OK")
    print("sample cs_10038 hex:", p.hex())
