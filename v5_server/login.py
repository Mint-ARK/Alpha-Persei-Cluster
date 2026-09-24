# -*- coding: utf-8 -*-
"""
login.py — V5 登录洪流模块（DB 驱动，2026-08-16 大项目阶段2）

职责（替代 replay 的登录洪流角色，重放系统从功能链路退役）：
- 登录洪流帧序列存 DB（login_push 表：seq/cmd/header/payload/dynamic）
- **header = 原素材 11B 帧头整体入库**（size/三字节标志域/cmd/idx/srv 原样零解释）：
  实测教训——标志域第 3 字节(byte2)=压缩标志，只存 byte4 会毁掉全部压缩帧
  （12097/20007/20009/28001/46011/52001/11001），客户端无重同步，一帧坏全流废。
- 发送时 dynamic=1 的帧用 generator.gen_payload 实时生成（payload>1024 压缩，
  size 重算），失败回库内 blob 兜底；其余帧 header+payload 直拼（逐字节=素材）。
"""
import json
import struct
import time
import zlib


def load_login_push(db):
    """读 login_push 表：[(seq, cmd, header, payload, dynamic, payload_json), ...] 按 seq 序（登录流截止于 10201，seq<=192）。"""
    rows = db.query("SELECT seq, cmd, header, payload, dynamic, payload_json FROM login_push WHERE seq <= 192 ORDER BY seq")
    out = []
    for r in rows:
        h, p, pj = r["header"], r["payload"], r.get("payload_json")
        if isinstance(h, str):
            h = h.encode("latin1", errors="ignore")
        if isinstance(p, str):
            p = p.encode("latin1", errors="ignore")
        out.append((r["seq"], r["cmd"], bytes(h), bytes(p), r["dynamic"] or 0, pj))
    return out


def rebuild(header, payload):
    """原帧头 + payload 重组，size 重算（动态帧换 payload 用）。"""
    body = header[2:] + payload          # 保留 flag域/cmd/idx/srv（9B）
    return struct.pack(">H", len(body)) + body


def send_login_flood(sock, db, uid, generator, frame_delay=0.001, log=None):
    """发送登录洪流：
    - dynamic=1 帧优先调用 generator 业务生成器动态生成；
    - dynamic=0 帧从 payload_json 读取字典并通过 codec.encode 现场编码；
    - 若动态编码失败则回退至库内 blob 兜底；
    - 自动适配 zlib 压缩（>10KB 或原压缩标记）与 cmd>65535 高位协议标志。"""
    log = log or (lambda *a, **k: None)
    frames = load_login_push(db)
    if not frames:
        log("[login] login_push 表为空，无洪流", "WARN")
        return 0, 0
    # 一次登录洪流必须只属于一个资源版本。运行中切换只影响下一次洪流，
    # 避免 192 帧发送途中出现 229/311 活动 ID 混发。
    import res_version_manager as _rvm
    login_res_version = _rvm.get_current_version()
    # 311_CORE_VERIFICATION_GUARD:
    # Build 311 的活动根 111 只开放 template=433 (Mode 4)。旧登录表仍含 sc_89013
    # (Mode 1)；若让它回退到库内 blob，311 会在 CoreVerificationChallengeData.InitTaskInfo
    # 中索引 ActivityCfg[nil] 并卡死大厅 Loading。这里必须跳过整帧，不能返回 None 走 blob 兜底。
    version_suppressed_cmds = {"311": {89013}}
    sent = dyn_hit = 0
    sock.settimeout(5)

    # 登录前置强制触发 lazy_timer 脉冲（保证登录洪流下发的所有数据处于最新周期状态）
    if uid and db is not None:
        try:
            from lazy_timer import lazy_timer
            class _LoginCtx:
                def __init__(self, d, l):
                    self.db = d
                    self.log = l
            lazy_timer.pulse(_LoginCtx(db, log), uid, force=True)
        except Exception as _lte:
            log(f"[login] 登录前置 lazy_timer 脉冲异常: {_lte}", "WARN")

    try:
        from codec import encode as _codec_encode
    except Exception:
        _codec_encode = None

    try:
        for seq, cmd, header, blob, dynamic, payload_json in frames:
            if cmd in version_suppressed_cmds.get(login_res_version, set()):
                log(
                    f"[login] version={login_res_version} 跳过不兼容推送 sc_{cmd} "
                    "(311_CORE_VERIFICATION_GUARD)",
                    "INFO",
                )
                continue
            gen = None

            # 1. dynamic == 1 时优先使用 generator 业务生成器
            if dynamic and generator is not None:
                try:
                    gen = generator.gen_payload(
                        cmd, uid=uid, db=db, res_version=login_res_version
                    )
                except Exception as e:
                    log(f"[login] sc_{cmd} 业务动态生成异常: {e}", "WARN")

            # 2. 非 dynamic 帧（或 generator 未实现该 cmd），走 payload_json 现场 Protobuf 编码
            if gen is None and payload_json and _codec_encode is not None:
                try:
                    json_data = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
                    if isinstance(json_data, dict):
                        gen = _codec_encode(f"sc_{cmd}", json_data)
                except Exception as e:
                    log(f"[login] sc_{cmd} JSON 动态编码异常，降级至 blob: {e}", "WARN")

            # 3. 动态组包与压缩适配
            if gen is not None:
                # 若业务生成器直接返回了 dict 数据对象，在此统一进行 Protobuf 序列化
                if isinstance(gen, dict) and _codec_encode is not None:
                    try:
                        gen = _codec_encode(f"sc_{cmd}", gen)
                    except Exception as e:
                        log(f"[login] sc_{cmd} 动态生成 dict 转 Protobuf 异常: {e}", "WARN")
                        gen = None

            if gen is not None and isinstance(gen, (bytes, bytearray)):
                orig_is_comp = (len(header) > 2 and header[2] == 1)
                is_compressed = False
                if orig_is_comp or (len(gen) > 10240 and cmd not in (30017, 91001, 25465)):
                    if gen[:2] != b"\x78\x9c":
                        gen = zlib.compress(gen)
                    is_compressed = True
                elif gen[:2] == b"\x78\x9c":
                    is_compressed = True

                _hd = bytearray(header)
                _hd[2] = 1 if is_compressed else 0  # byte 2: ZLIB 压缩标志
                _hd[3] = 0                          # byte 3: 恒为 0
                _hd[4] = 1 if cmd > 65535 else 0    # byte 4: 高位协议标志（客户端据此累加 65536 偏移）
                header = bytes(_hd)
                frame = rebuild(header, gen)
                dyn_hit += 1
            else:
                # 4. 终极兜底：使用原始 blob 组帧
                if len(header) > 2 and header[2] == 1:
                    if blob and blob[:2] not in (b"\x78\x9c", b"\x78\x01", b"\x78\xda", b"\x78\x5e"):
                        blob = zlib.compress(blob)
                elif len(header) > 2 and header[2] == 0:
                    if blob and blob[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda", b"\x78\x5e"):
                        try:
                            blob = zlib.decompress(blob)
                        except Exception:
                            pass
                frame = rebuild(header, blob)

            sock.sendall(frame)
            sent += 1

            # 梦境再构联动补发 45001（普通首领列表）与 45101（进阶首领列表）
            if cmd == 45201 and generator is not None:
                for extra_cmd in (45001, 45101):
                    try:
                        ext_gen = generator.gen_payload(
                            extra_cmd, uid=uid, db=db, res_version=login_res_version
                        )
                        if ext_gen:
                            ext_frame = generator.build_frame(extra_cmd, ext_gen)
                            sock.sendall(ext_frame)
                            sent += 1
                            dyn_hit += 1
                            if frame_delay:
                                time.sleep(frame_delay)
                    except Exception as e:
                        log(f"[login] 联动下发 sc_{extra_cmd} 异常: {e}", "WARN")

            if frame_delay:
                time.sleep(frame_delay)

        # 多维变量联动下发 sc_11003：活动 ID/theme 与资源版本同步，
        # stop_time 统一使用远期时间，周常刷新另由玩法状态管理。
        # 多维变量周期 11003 推送
        try:
            import weekly_challenge_service as _wcs
            from codec import encode as _c_enc
            nxt_mon = _wcs.get_next_monday_5am()
            version_cfg = _rvm.get_version_config(login_res_version)
            p11003 = _c_enc("sc_11003", {
                "activity": {
                    "activity_id": int(version_cfg["polyhedron_activity_id"]),
                    "start_time": nxt_mon - 604800,
                    "stop_time": _rvm.FAR_FUTURE_TIMESTAMP,
                    "state": 1,
                    "theme": int(version_cfg["theme_id"]),
                    "template": 73
                }
            })
            if p11003 and generator is not None:
                frame_11003 = generator.build_frame(11003, p11003)
                sock.sendall(frame_11003)
                sent += 1
                dyn_hit += 1
        except Exception as _e:
            log(f"[login] 多维变量周期 11003 推送异常: {_e}", "WARN")

        # 虚构推演登录状态联动推送：局内 session (sc_88001) 与属性/血量 (sc_88013 / sc_88019)
        try:
            from rogueteam_service import RogueTeamService
            svc = RogueTeamService.get(db=db)
            session_data = svc.get_session_data(uid)
            if session_data and session_data.get("in_game", 0) != 0 and generator is not None:
                p88001 = _c_enc("sc_88001", session_data)
                if p88001:
                    sock.sendall(generator.build_frame(88001, p88001))
                    sent += 1
                    dyn_hit += 1
                p88013 = _c_enc("sc_88013", {"attr_list": session_data.get("attr_list", [])})
                if p88013:
                    sock.sendall(generator.build_frame(88013, p88013))
                    sent += 1
                    dyn_hit += 1
                p88019 = _c_enc("sc_88019", {"hero_list": session_data.get("hero_list", [])})
                if p88019:
                    sock.sendall(generator.build_frame(88019, p88019))
                    sent += 1
                    dyn_hit += 1
                log(f"[login] 虚构推演在局状态已同步下发 (难度={session_data.get('difficult')}, 层数={session_data.get('floor_num')})")
        except Exception as _re:
            log(f"[login] 虚构推演登录态推送异常: {_re}", "WARN")

        # 好感与人际关系网登录联动推送：sc_73005 (礼物置换限额) + sc_73007 (已领羁绊故事) + sc_73001 (连携技能网)
        try:
            from trust_service import TrustService
            _trust_svc = TrustService.get_instance()
            p73005 = _c_enc("sc_73005", _trust_svc.get_displace_limit_payload(db, uid) if (db and uid) else {"limit_list": []})
            if p73005 and generator is not None:
                sock.sendall(generator.build_frame(73005, p73005))
                sent += 1
                dyn_hit += 1

            claimed_rows = db.query("SELECT key_id FROM claim_ledger WHERE uid=? AND kind='relation_story'", (uid,)) if db and uid else []
            got_list = [int(r["key_id"]) for r in claimed_rows]
            p73007 = _c_enc("sc_73007", {"got_reward_id_list": got_list})
            if p73007 and generator is not None:
                sock.sendall(generator.build_frame(73007, p73007))
                sent += 1
                dyn_hit += 1

            # sc_73001 连携技能（协作技能网）全量等级与进度：客户端 ComboSkillData:InitLevelData 初始化
            p73001 = _c_enc("sc_73001", _trust_svc.get_combo_skill_payload(db, uid) if (db and uid) else {"info": {"skill_list": []}})
            if p73001 and generator is not None:
                sock.sendall(generator.build_frame(73001, p73001))
                sent += 1
                dyn_hit += 1
        except Exception as _te:
            log(f"[login] 好感与羁绊故事登录态推送异常: {_te}", "WARN")

        # AI 转译器：静默检测并派发今日节日/节气/生日问候信件
        try:
            from ai_translator import AITranslator
            AITranslator.get_instance(db=db).trigger_login_greeting(uid, db=db)
        except Exception as _aie:
            log(f"[login] AI 节日问候联动异常: {_aie}", "WARN")

    except OSError as e:
        log(f"[login] 洪流中断: {e} after {sent} frames", "WARN")
    finally:
        sock.settimeout(None)
    log(f"[login] 登录洪流完成: {sent}/{len(frames)} 帧（动态 {dyn_hit}）")
    return sent, dyn_hit


if __name__ == "__main__":
    _cur = os.path.dirname(os.path.abspath(__file__))
    if _cur not in sys.path:
        sys.path.insert(0, _cur)
    from account_db import get_db
    import generator
    db = get_db()
    frames = load_login_push(db)
    print(f"login_push: {len(frames)} 帧；generator动态帧:",
          sorted({c for _, c, _, _, d, _ in frames if d}))
    json_count = sum(1 for _, _, _, _, _, pj in frames if pj)
    print(f"payload_json 明文帧覆盖: {json_count}/{len(frames)}")
    ok = 0
    for _, c, _, _, d, pj in frames:
        if d:
            p = generator.gen_payload(c, uid=generator.DEFAULT_UID, db=db)
            ok += 1 if p else 0
    print(f"generator 动态生成 dry-run: {ok} 成功")
