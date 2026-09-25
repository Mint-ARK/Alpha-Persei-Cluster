# -*- coding: utf-8 -*-
"""
transport.py — 传输层（B 方案：独立 ZLIB 压缩层 + 帧头收发）

职责（架构分层 ①，老大定稿 B 方案）：
- 帧级收发：下行 11B 帧头（u16 size BE + 00 00 [flag] + u16 cmd + u16 idx + u16 srv）
              上行 13B 帧头（u32 len LE + u8 ver + u32 cmd + u16 idx + u16 srv）
- ZLIB 压缩层：payload 以 789c 魔数开头 ⇔ zlib.decompress；下行按阈值自动 deflate
- 与 codec（protobuf dict↔bytes）完全解耦：transport 只碰 bytes，不碰业务字段

帧格式权威依据：`临上线基准数据\中间件接口契约.md` §1（双向验证 223/223 帧一致）。
ZLIB 实测结论（2026-08-15 素材扫描）：flag=1 是 Lua 投递标志（91xxx 等 47 帧），
与压缩无关；zlib 帧（11001/12097/20007/20009/28001）flag=0 且 payload 直接 789c 开头，
解压后为标准 protobuf。压缩触发：解压后 >10KB 的帧均被压缩，明文帧最大 7.4KB。
"""
import struct
import zlib

ZLIB_MAGIC = b"\x78\x9c"          # zlib 魔数（素材帧实测）
DEFAULT_COMPRESS_THRESHOLD = 10240  # payload >10KB 时 deflate（93级账号实测：明文最大9.5KB未压，zlib最小11KB已压）

# ---------------- 帧常量 ----------------

DOWN_HEADER = 11   # 下行帧头长度（不含 payload）
UP_HEADER = 13     # 上行帧头长度（不含 payload）


# ---------------- ZLIB 层 ----------------

def maybe_inflate(payload):
    """payload 以 789c 开头 → zlib.decompress；否则原样返回。
    返回 (bytes, was_compressed)。任何解压异常原样返回（防整帧崩）。"""
    if payload and payload[:2] == ZLIB_MAGIC:
        try:
            return zlib.decompress(payload), True
        except Exception:
            return payload, False
    return payload, False


def maybe_deflate(payload, threshold=DEFAULT_COMPRESS_THRESHOLD, force=False):
    """下行压缩：len(payload) > threshold 时 zlib.compress；否则原样。
    force=True 强制压缩（供测试/特殊帧）。返回 bytes。"""
    if force or (payload and len(payload) > threshold):
        try:
            return zlib.compress(payload)
        except Exception:
            return payload
    return payload


# ---------------- 下行帧（服务器 → 客户端） ----------------

MAX_DOWN_BODY = 0xFFFF          # 下行 size 是 u16 → body 最大 65535，payload 最大 65526


def pack_down(cmd, payload=b"", index=0, server_idx=0, flag=0,
              compress=None, threshold=DEFAULT_COMPRESS_THRESHOLD, force=False):
    """下行组帧：u16 size(BE, size=9+payload) | 00 00 [flag] | frame_cmd(BE) | idx | srv | payload。

    cmd = 业务 SC 号（如 91015），帧头自动 & 0xFFFF 得 frame_cmd（>65535 回绕，素材实测一致）。
    flag=1 = Lua 投递 + 高 16 位标记（cmd>65535 必须；unpack_down 据此还原业务号）。

    compress:
      None（默认）/ True = 按阈值自动：payload > threshold 才 deflate。
        与真实服务器一致 —— 素材里解压后 >10KB 的帧全部压缩下发，明文帧最大 7.4KB。
      False = 不主动压缩（但超 u16 上限时仍会兜底压缩）
    force=True = 无视阈值强制压缩（测试/特殊帧用）

    payload 过大时：先尝试压缩；压缩后仍 >65526B 则抛 ValueError（原先直接让
    struct.pack 抛 'H' format requires ...，异常类型不在 server_net 的捕获集合里，
    会掀掉整条连接）。
    """
    if cmd > 65535 and flag == 0:
        flag = 1
    if force:
        body_payload = maybe_deflate(payload, threshold, force=True)
    elif compress is False:
        body_payload = payload
    else:
        body_payload = maybe_deflate(payload, threshold)
    # u16 上限兜底：即使调用方要求不压缩，超限也必须压，否则整帧发不出去
    if 9 + len(body_payload) > MAX_DOWN_BODY:
        body_payload = maybe_deflate(payload, threshold, force=True)
    if 9 + len(body_payload) > MAX_DOWN_BODY:
        raise ValueError(
            f"下行帧超出 u16 上限：cmd={cmd} payload={len(payload)}B "
            f"压缩后={len(body_payload)}B > {MAX_DOWN_BODY - 9}B（需分片下发）")
    zlib_flag = 1 if (body_payload and body_payload[:2] == ZLIB_MAGIC) else 0
    high_flag = 1 if (cmd > 65535 or flag == 1) else 0
    body = (bytes([zlib_flag & 0xFF, 0x00, high_flag & 0xFF])
            + struct.pack(">H", cmd & 0xFFFF)
            + struct.pack(">H", index & 0xFFFF)
            + struct.pack(">H", server_idx & 0xFFFF)
            + body_payload)
    return struct.pack(">H", len(body)) + body


def unpack_down(frame):
    """下行拆帧（单帧，须已按 size 切好）：返回 dict(cmd,frame_cmd,payload,index,server_idx,flag,compressed)。

    - frame_cmd = 帧头 u16 原值（传输层 CMD，>65535 的业务号按 u16 回绕）
    - cmd = 业务 SC 号还原：**以帧头 flag 为准**，flag==1 → frame_cmd | 0x10000。

    还原规则的素材依据（2026-08-19 全量扫描 archive/*/complete_replay/tcp/tcpfwd_rebuilt.jsonl，
    184 种 (flag, frame_cmd) 组合）：
      · flag=1 共 45 种 frame_cmd，|0x10000 后全部落在真实业务号上
        （67001 / 68151 / 73001 / 75009 / 76151 / 79601 / 81001 / 83016 / ... / 91001）
      · flag=0 的 frame_cmd 中没有任何一个落在 0x6300~0x64FF
    此前用「0x6300<=frame_cmd<=0x64FF」的数值区间判定，45 种里只命中 1 种（91001），
    67xxx~90xxx 整片协议族的高 16 位全部丢失（且与 pack_down 写 flag 的行为不对称）。
    """
    if len(frame) < DOWN_HEADER:
        raise ValueError(f"downstream frame too short: {len(frame)}B")
    size = struct.unpack(">H", frame[0:2])[0]
    if len(frame) != size + 2:
        raise ValueError(f"downstream size mismatch: size={size} len={len(frame)}")
    flag = frame[4]
    frame_cmd = struct.unpack(">H", frame[5:7])[0]
    index = struct.unpack(">H", frame[7:9])[0]
    server_idx = struct.unpack(">H", frame[9:11])[0]
    payload, compressed = maybe_inflate(frame[11:])
    cmd = (frame_cmd | 0x10000) if flag == 1 else frame_cmd
    return {"cmd": cmd, "frame_cmd": frame_cmd, "payload": payload,
            "index": index, "server_idx": server_idx,
            "flag": flag, "compressed": compressed}


# ---------------- 上行帧（客户端 → 服务器） ----------------

def pack_up(cmd, payload=b"", index=0, server_idx=0, ver=0, compress=False):
    """上行组帧：u32 len(LE, len=9+payload 不含自身) | u8 ver | u32 cmd(LE) | idx | srv | payload。"""
    body_payload = maybe_deflate(payload) if compress else payload
    body = (struct.pack("<I", 9 + len(body_payload))
            + bytes([ver & 0xFF])
            + struct.pack("<I", cmd & 0xFFFFFFFF)
            + struct.pack("<H", index & 0xFFFF)
            + struct.pack("<H", server_idx & 0xFFFF)
            + body_payload)
    return body


def unpack_up(frame):
    """上行拆帧（单帧，须已按 len 切好）：返回 dict(cmd,payload,index,server_idx,ver,compressed)。"""
    if len(frame) < UP_HEADER:
        raise ValueError(f"upstream frame too short: {len(frame)}B")
    length = struct.unpack("<I", frame[0:4])[0]
    if length + 4 != len(frame):
        raise ValueError(f"upstream length mismatch: len={length} total={len(frame)}")
    ver = frame[4]
    cmd = struct.unpack("<I", frame[5:9])[0]
    index = struct.unpack("<H", frame[9:11])[0]
    server_idx = struct.unpack("<H", frame[11:13])[0]
    payload, compressed = maybe_inflate(frame[13:])
    return {"cmd": cmd, "payload": payload, "index": index,
            "server_idx": server_idx, "ver": ver, "compressed": compressed}


# ---------------- 流式切帧（按 size 无重同步切分） ----------------

class FrameParser:
    """TCP 流式切帧：严格按 size/len 切（客户端无重同步，size 错则整帧丢弃）。
    下行切分依据 u16 size(BE)=总长-2；上行切分依据 u32 len(LE)=总长-4。"""

    def __init__(self, direction="down"):
        assert direction in ("down", "up")
        self.direction = direction
        self.buf = b""

    def feed(self, data):
        """喂入新字节，返回切好的完整帧列表（可能为空）。"""
        self.buf += data
        frames = []
        while True:
            if self.direction == "down":
                if len(self.buf) < 2:
                    break
                size = struct.unpack(">H", self.buf[0:2])[0]
                total = size + 2
            else:
                if len(self.buf) < 4:
                    break
                length = struct.unpack("<I", self.buf[0:4])[0]
                total = length + 4
            if len(self.buf) < total:
                break
            frames.append(self.buf[:total])
            self.buf = self.buf[total:]
        return frames


if __name__ == "__main__":
    # 自检：下行组帧→拆帧 往返 + zlib 压缩往返 + 流式切帧
    payload = bytes(range(256)) * 100  # 25.6KB 明文
    f1 = pack_down(17009, payload, index=7, server_idx=99, compress=False)
    d1 = unpack_down(f1)
    assert d1["cmd"] == 17009 and d1["index"] == 7 and d1["server_idx"] == 99
    assert d1["payload"] == payload and not d1["compressed"]
    print("下行明文往返 OK:", len(f1), "B")

    f2 = pack_down(17009, payload, index=7, server_idx=99, compress=True)
    d2 = unpack_down(f2)
    assert d2["payload"] == payload and d2["compressed"]
    print("下行zlib往返 OK: 压缩后", len(f2), "B (原", len(f1), "B)")

    # 上行
    u1 = pack_up(17009, payload[:100], index=3, server_idx=5)
    ud = unpack_up(u1)
    assert ud["cmd"] == 17009 and ud["payload"] == payload[:100]
    print("上行往返 OK:", len(u1), "B")

    # 流式切帧（下行 3 帧粘包 + 半包）
    frames = [pack_down(10000 + i, b"x" * (i * 100), index=i, server_idx=i) for i in range(1, 4)]
    blob = b"".join(frames)
    fp = FrameParser("down")
    got = []
    for i in range(0, len(blob), 37):  # 随机半包喂
        got += fp.feed(blob[i:i + 37])
    assert len(got) == 3 and got == frames
    print("流式切帧 OK: 3 帧粘包/半包还原一致")

    # 强制压缩（小 payload）
    small = b"\x08\x00"
    f3 = pack_down(56002, small, compress=True)
    # 小 payload 不触发阈值压缩
    assert unpack_down(f3)["payload"] == small and not unpack_down(f3)["compressed"]
    print("阈值控制 OK: 小 payload 不压缩")
    assert unpack_down(pack_down(56002, small, force=True))["compressed"]
    print("force 强制压缩 OK")

    # 高 16 位还原（flag 驱动）：素材实测 flag=1 的 45 种 frame_cmd 都要能还原
    for _c in (17009, 91015, 73013, 67001, 68151, 90051, 86037):
        _d = unpack_down(pack_down(_c, b"\x08\x00"))
        assert _d["cmd"] == _c, f"cmd 还原失败: {_c} -> {_d['cmd']} (flag={_d['flag']})"
    print("高16位还原 OK: 17009/91015/73013/67001/68151/90051/86037 往返一致")

    # u16 上限：超限自动压缩，压不下去则明确报错（不再抛 struct.error）
    big_ok = b"A" * 70000                      # 可压缩内容 → 压缩后能塞进 u16
    assert unpack_down(pack_down(14009, big_ok))["payload"] == big_ok
    print("超限自动压缩 OK: 70000B 可压缩 payload 正常下发")
    import os as _os
    big_bad = _os.urandom(70000)               # 随机数据压不动 → 应抛 ValueError
    try:
        pack_down(14009, big_bad)
        raise AssertionError("超限未压缩成功时应抛 ValueError")
    except ValueError as _e:
        print("超限显式报错 OK:", str(_e)[:60])

    print("\ntransport 自检全部通过")
