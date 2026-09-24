# -*- coding: utf-8 -*-
"""
replay.py — V5 重放模块（独立模块位，MAIN 启动参数 --replay 激活才运行）

职责（老大定稿：重放系统独立模块，未激活不运行不干扰其他模块）：
- 加载归档素材（https HTTP 索引 + tcp 帧序列）
- 登录洪流原样转发（10200 触发 200+ 帧全量推送）
- 素材覆盖帧（--frame-override 改数据）

数据流：replay 是"旁路"——客户端登录后，由 replay 按素材推全量帧；
业务请求（登录后交互）走 CORE（core.dispatch）。互不干扰。

来源：V4.1 replay_push_frames / load_replay_material 独立化（行为不变）。
"""
import json
import os
import struct
import time


# ---------------- 素材加载 ----------------

def load_replay_material(replay_dir, override_map=None):
    """加载归档素材：https/*.json → HTTP 索引；tcpfwd_*.jsonl → 帧序列。

    override_map: {cmd: bytes} 覆盖帧（--frame-override 改数据）。

    返回 dict：{http_index, frames, mat_ts, files}
      http_index:  {(host, path): [{"status","response","ctype","query"}, ...]}
      frames:      [(dir_tag, raw_bytes), ...] 完整帧序列（含 C->U/U->C）
      mat_ts:      素材时间戳（文件名日期 + 首帧时间）
    """
    out = {"http_index": {}, "frames": [], "mat_ts": int(time.time()), "files": []}
    # ---- HTTPS ----
    hdir = os.path.join(replay_dir, "https")
    if os.path.isdir(hdir):
        for fn in sorted(os.listdir(hdir)):
            if not fn.startswith("proxy_") or not fn.endswith(".json"):
                continue
            p = os.path.join(hdir, fn)
            try:
                j = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            host = (j.get("host") or "").lower()
            path = j.get("path") or ""
            resp_text = j.get("response") or ""
            ctype = ("text/html; charset=utf-8"
                     if resp_text.lstrip().startswith(("<", "<!DOCTYPE")) else "application/json")
            ent = {"status": j.get("status", 200), "response": resp_text,
                   "ctype": ctype, "query": j.get("query") or ""}
            out["http_index"].setdefault((host, path), []).append(ent)
            out["files"].append(fn)
    # ---- TCP ----
    tdir = os.path.join(replay_dir, "tcp")
    tfile = None
    if os.path.isdir(tdir):
        cands = [f for f in sorted(os.listdir(tdir))
                 if f.startswith("tcpfwd_") and f.endswith(".jsonl")]
        if cands:
            tfile = os.path.join(tdir, cands[-1])
    if tfile:
        for line in open(tfile, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            raw = bytes.fromhex(e.get("hex") or "")
            if not raw:
                continue
            out["frames"].append((e.get("dir", "?"), raw))
        # mat_ts：文件名日期 + 首帧时间
        try:
            import datetime as _dt
            d = os.path.basename(tfile).split("_")[1]
            day = _dt.datetime.strptime(d, "%Y%m%d")
            for line in open(tfile, encoding="utf-8"):
                try:
                    hm = json.loads(line).get("t")
                    if hm:
                        hh, mm, ss = hm.split(":")
                        day = day.replace(hour=int(hh), minute=int(mm), second=int(ss))
                        break
                except Exception:
                    pass
            out["mat_ts"] = int(day.timestamp())
        except Exception:
            pass
        out["files"].append(os.path.basename(tfile))
    # ---- 覆盖帧 ----
    if override_map:
        out["override_map"] = override_map
    return out


def split_frames(raw):
    """按 C# 逻辑切帧（严格按 size，无重同步）：yield (cmd, frame_bytes)。"""
    i = 0
    while i + 2 <= len(raw):
        size = struct.unpack(">H", raw[i:i + 2])[0]
        total = size + 2
        if i + total > len(raw):
            break
        frame = raw[i:i + total]
        if len(frame) >= 7:
            cmd = struct.unpack(">H", frame[5:7])[0]
            yield cmd, frame
        i += total


def extract_u2c(frames):
    """提取 U->C 帧序列（登录洪流重放用）：返回 [(cmd, raw), ...]，严格截止于 10201（seq<=192）。"""
    out = []
    for d, raw in frames:
        if d == "U->C":
            try:
                for cmd, frame in split_frames(raw):
                    out.append((cmd, frame))
                    if cmd == 10201:
                        return out
            except Exception:
                continue
    return out


def extract_10200_flow(frames):
    """提取 cs_10200 后的 U->C 洪流（登录后全量推送段）。
    返回 frames 列表：从首个 U->C 起直到下一个 C->U 前的连续 U->C 帧。
    """
    # 简化：返回全部 U->C 帧（登录洪流主体），由调用方决定起点
    return extract_u2c(frames)


# ---------------- 登录分段提取（gateway 段 / game 查表 / 心跳） ----------------

def split_login_segments(frames, override_map=None):
    """按登录链路切分素材（V4.1 分段逻辑）：

    gateway 段（8102）：cs_10038 起 → sc_10039 止（含 10042→10043 后续登录帧）
    game 查表（8105）：每个 C->U 锚点 → 其后连续 U->C 段（同一 cmd 取最后一次）
    心跳（8102）：10050 → 10051 响应单独查表

    返回 dict：
      gw_seq:       [hex, ...] gateway 段 U->C 帧（10039 在首）
      game_resp_map: {cmd: hex} game 请求-响应查表
      gw_resp_map:  {cmd: hex} gateway 心跳查表（10050→10051）
    """
    override_map = override_map or {}
    out = {"gw_seq": [], "game_resp_map": {}, "gw_resp_map": {}}
    gw_seq = []
    resp_map = {}
    gw_resp_map = {}
    in_gw = False
    gw_done = False
    batch_cmds = []
    seg_buf = []
    last_hb = None

    def flush_batch():
        if batch_cmds and seg_buf:
            seg = "".join(seg_buf)
            for c in batch_cmds:
                resp_map[c] = seg

    def frame_cmd(d, raw):
        """按方向解析帧 cmd：上行 u32 LE / 下行 u16 BE。"""
        try:
            if d == "C->U" and len(raw) >= 13:
                ln = struct.unpack("<I", raw[0:4])[0]
                if ln + 4 == len(raw):
                    return struct.unpack("<I", raw[5:9])[0]
            elif d == "U->C" and len(raw) >= 7:
                sz = struct.unpack(">H", raw[0:2])[0]
                if sz + 2 == len(raw):
                    return struct.unpack(">H", raw[5:7])[0]
        except Exception:
            pass
        return None

    for d, raw in frames:
        cmd = frame_cmd(d, raw)
        if cmd is None:
            continue
        if not in_gw and d == "C->U" and cmd == 10038:
            in_gw = True
        if in_gw and not gw_done:
            if d == "U->C":
                gw_seq.append(raw.hex())
                if cmd == 10039:
                    gw_done = True
                    in_gw = False
            continue
        if cmd in (10050, 10051):
            if d == "C->U" and cmd == 10050:
                last_hb = cmd
            elif d == "U->C" and cmd == 10051 and last_hb is not None:
                gw_resp_map[last_hb] = raw.hex()
                last_hb = None
            continue  # 心跳帧不属于 game 连接
        if d == "C->U":
            if seg_buf:
                flush_batch()
                batch_cmds = []
                seg_buf = []
            batch_cmds.append(cmd)
        elif d == "U->C" and batch_cmds:
            raw2 = override_map.get(cmd, raw)
            seg_buf.append(raw2.hex())
            if cmd == 10201 and 10200 in batch_cmds:
                flush_batch()
                batch_cmds = []
                seg_buf = []
    if seg_buf:
        flush_batch()
    out["gw_seq"] = gw_seq
    out["game_resp_map"] = resp_map
    out["gw_resp_map"] = gw_resp_map
    return out


# ---------------- 重放执行（登录洪流） ----------------

def replay_login_flow(conn, material, frame_delay=0.03, log=None):
    """登录洪流重放：把素材 U->C 帧序列原样转发（10200 触发登录段）。

    conn: socket 连接（已建立 game 连接）
    material: load_replay_material 返回值
    frame_delay: 帧间延迟
    log: 日志函数
    """
    log = log or (lambda *a, **k: None)
    frames = extract_u2c(material["frames"])
    if not frames:
        log("[replay] 无素材帧，跳过登录洪流", "WARN")
        return 0
    sent = 0
    conn.settimeout(5)
    try:
        for cmd, frame in frames:
            # 覆盖帧（改数据）
            om = material.get("override_map") or {}
            if cmd in om:
                frame = om[cmd]
            conn.sendall(frame)
            sent += 1
            if frame_delay:
                time.sleep(frame_delay)
    except OSError as e:
        log(f"[replay] 洪流中断: {e} after {sent} frames", "WARN")
    finally:
        conn.settimeout(None)
    log(f"[replay] 登录洪流完成: {sent}/{len(frames)} 帧")
    return sent


# ---------------- 动态洪流生成（库驱动，替代素材原样） ----------------

# generator 已支持的动态帧 cmd（登录洪流核心数据）
# 2026-08-15 排查结论：
#  - 24009 素材是分片语义(11帧×1章节)，动态全量 → 卡 → 保持素材
#  - 23009(hero_num) 与 14009(英雄列表) 必须配对动态化（同一数据源 84 英雄），
#    否则 23009 说 84 但 14009 素材只有 51 → 客户端数据不一致卡死
#  - 17009/15009 单独动态化验证过结构正常，但为最小改动先只配对 23009+14009
# 2026-08-16 追加 15009（钱包）：素材钱包不含券 5/19/38（抓包账号当时为 0），
#    客户端券数恒显 0 且本地购买校验走旧钱包 → 必须库驱动下发实时余额
# 2026-08-16 追加 56001（红点状态）/30001（邮件未读摘要）/28001（任务列表）：
#    红点持久化闭环——领取写库后重登不再复活（素材旧帧会让红点按 8/11 状态重亮）
# 2026-08-18 追加 52001（图鉴与插图收集）/ 46011（钥从实例库驱动）：
#    钥从库存持久化闭环——清退三星狗粮钥从，精准下发 4/5 星珍贵钥从
# 2026-08-26 追加四大高难周常玩法全套下发帧（梦境再构/黑区净化/多维变量/迭代校验）+ 好友/后宅/MomoTalk
DYNAMIC_CMDS = {
    23009, 14009, 15009, 56001, 30001, 28001, 16015, 52001, 46011,
    45201, 45001, 45101, 44007, 44009, 44019, 44021, 44023, 18001, 75009,
    19001, 19029, 91001, 58001, 12023, 35011
}

# 默认跳过重放的帧（若需要在素材重放中完全由动态服务接管且不发素材旧帧）
DEFAULT_EXCLUDE_CMDS = set()


def extract_u2c(frames, exclude_cmds=None):
    """提取 U->C 帧序列（登录洪流重放用）：返回 [(cmd, raw), ...]，严格截止于 10201（seq<=192）。"""
    exclude_cmds = exclude_cmds or set()
    out = []
    for d, raw in frames:
        if d == "U->C":
            try:
                for cmd, frame in split_frames(raw):
                    if cmd in exclude_cmds:
                        continue
                    out.append((cmd, frame))
                    if cmd == 10201:
                        return out
            except Exception:
                continue
    return out


def build_dynamic_flow(u2c_frames, db, uid, generator, frame_delay=0.03, exclude_cmds=None):
    """按素材洪流帧序列生成动态帧流。
    exclude_cmds: 指定不重放、直接剔除的 CMD 集合。
    """
    exclude_cmds = exclude_cmds or set()
    import struct
    import zlib
    out = []

    def _pack_dyn_frame(f_cmd, payload, raw_ref=None):
        if not payload:
            return None
        if len(payload) > 1024:
            payload = zlib.compress(payload)
        if raw_ref and len(raw_ref) >= 11:
            idx = struct.unpack(">H", raw_ref[7:9])[0]
            srv = struct.unpack(">H", raw_ref[9:11])[0]
        else:
            idx, srv = 0, 0
        from generator import build_frame
        return build_frame(f_cmd, payload, index=idx, server_idx=srv)

    for cmd, raw in u2c_frames:
        if cmd in exclude_cmds:
            continue
        if cmd in DYNAMIC_CMDS:
            # 动态生成：generator.gen_payload 只给 payload，重建帧头
            payload = generator.gen_payload(cmd, uid=uid, db=db)
            if payload:
                frame = _pack_dyn_frame(cmd, payload, raw)
                out.append((cmd, frame))
                # 周常高难数据联动补齐（录制素材中未包含的关联周常子帧）：
                if cmd == 45201:
                    # 梦境再构：登录时同时补发 45001（普通）与 45101（进阶）
                    p001 = generator.gen_payload(45001, uid=uid, db=db)
                    if p001:
                        out.append((45001, _pack_dyn_frame(45001, p001, raw)))
                    p101 = generator.gen_payload(45101, uid=uid, db=db)
                    if p101:
                        out.append((45101, _pack_dyn_frame(45101, p101, raw)))
                elif cmd == 18001:
                    # 多维变量后联动补发 75009 迭代校验
                    p75009 = generator.gen_payload(75009, uid=uid, db=db)
                    if p75009:
                        out.append((75009, _pack_dyn_frame(75009, p75009, raw)))
                continue
        # 其余/生成失败：保留素材原帧
        out.append((cmd, raw))
    return out


def replay_dynamic_flow(conn, u2c_frames, db, uid, generator, frame_delay=0.03, log=None):
    """动态洪流重放：按素材序列，动态帧库驱动生成 + 其余素材原样。"""
    log = log or (lambda *a, **k: None)
    frames = build_dynamic_flow(u2c_frames, db, uid, generator)
    sent = 0
    conn.settimeout(5)
    try:
        for cmd, frame in frames:
            conn.sendall(frame)
            sent += 1
            if frame_delay:
                time.sleep(frame_delay)
    except OSError as e:
        log(f"[replay-dyn] 洪流中断: {e} after {sent} frames", "WARN")
    finally:
        conn.settimeout(None)
    log(f"[replay-dyn] 动态洪流完成: {sent}/{len(frames)} 帧（动态帧 {sum(1 for c, _ in frames if c in DYNAMIC_CMDS)}）")
    return sent


# ---------------- HTTP 索引（SDK/区服代理用，V5 预留） ----------------

def find_http_response(material, host, path, query=""):
    """按 (host, path, query) 查 HTTP 素材响应。未命中返回 None。"""
    idx = material.get("http_index") or {}
    ents = idx.get((host.lower(), path))
    if not ents:
        return None
    if query:
        for e in ents:
            if e.get("query") == query:
                return e
    return ents[0]


if __name__ == "__main__":
    # 自检：素材加载 + 切帧 + 洪流提取
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'replay_data')
    if not os.path.isdir(d):
        old_d = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'analysis_scripts', 'archive', '20260805_全流程_服务器2_蒂卡拉', 'complete_replay')
        if os.path.isdir(old_d):
            d = old_d
        else:
            print("未指定素材目录且默认素材目录不存在:", d)
            sys.exit(0)
    mat = load_replay_material(d)
    print(f"素材加载: http={len(mat['http_index'])} 帧={len(mat['frames'])} mat_ts={mat['mat_ts']}")
    u2c = extract_u2c(mat["frames"])
    print(f"U->C 帧: {len(u2c)}")
    cmds = {}
    for cmd, _ in u2c:
        cmds[cmd] = cmds.get(cmd, 0) + 1
    top = sorted(cmds.items(), key=lambda x: -x[1])[:10]
    print("高频帧:", top)
    print("replay 自检通过")
