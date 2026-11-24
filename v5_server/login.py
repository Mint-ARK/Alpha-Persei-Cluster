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


def single_plot_progress_frame(frames):
    """One authoritative plot snapshot per login, at the full frame's position."""
    matches = [index for index, row in enumerate(frames) if row[1] == 24009]
    if not matches:
        return frames
    dynamic = [index for index in matches if frames[index][4]]
    selected = dynamic[-1] if dynamic else matches[-1]
    result = []
    for index, row in enumerate(frames):
        if row[1] != 24009:
            result.append(row)
        elif index == selected:
            # Static first-stage stubs must never replace account progress.
            seq, cmd, header, blob, _, payload_json = row
            result.append((seq, cmd, header, blob, 1, payload_json))
    return result


def osiris_point_frame(frames, generator):
    """Replace only this activity's captured 60201; preserve other owners."""
    from codec import decode
    from osiris_service import ACTIVITY
    result=[]
    for row in frames:
        if row[1]==60201:
            data=json.loads(row[5]) if row[5] else decode(zlib.decompress(row[3]) if row[2][2] else row[3],'sc_60201')
            if data.get('activity_id')==ACTIVITY:continue
        result.append(row)
    marker=next((i for i,r in enumerate(result) if r[1]==10201),len(result))
    result.insert(marker,(0,60201,generator.build_frame(60201,b'')[:11],b'',1,json.dumps(dict(activity_id=ACTIVITY))))
    return result


def reconcile_oath_rights_before_login(db, uid, log):
    """Backfill existing eligible marriages atomically before profile snapshots."""
    if not uid or db is None:
        return False
    try:
        from oath_rights_service import grant_eligible_rights
        with db.transaction():
            result = grant_eligible_rights(db, uid, silent=True)
        return result["has_rights"]
    except Exception as exc:
        log(f"[oath] 登录权益补齐失败: {exc}", "WARN")
        return False


def prepare_skin_draw_login(db, uid, version, generator):
    from codec import encode
    from skin_draw_service import login_packets
    result = []
    for cmd, data in login_packets(db, uid, version):
        payload = encode(f'sc_{cmd}', data)
        if not payload:
            raise RuntimeError(f'sc_{cmd} 抽取活动进度编码为空')
        result.append((0, cmd, generator.build_frame(cmd, b'')[:11], payload, 0, None))
    has_draws = bool(db.query('SELECT 1 FROM user_skin_draw_pool WHERE uid=? AND drawn_num>0 LIMIT 1', (uid,)))
    return result, has_draws


def send_login_flood(sock, db, uid, generator, frame_delay=0.001, log=None):
    """发送登录洪流：
    - dynamic=1 帧优先调用 generator 业务生成器动态生成；
    - dynamic=0 帧从 payload_json 读取字典并通过 codec.encode 现场编码；
    - 若动态编码失败则回退至库内 blob 兜底；
    - 自动适配 zlib 压缩（>10KB 或原压缩标记）与 cmd>65535 高位协议标志。"""
    started_ns = time.perf_counter_ns()
    log = log or (lambda *a, **k: None)
    frames = load_login_push(db)
    original_plot_count = sum(row[1] == 24009 for row in frames)
    frames = single_plot_progress_frame(frames)
    if original_plot_count > 1:
        log(f'[login] sc_24009 合并 {original_plot_count} 帧为一次全量进度', 'INFO')
    from new_player_service import enabled as _fresh_enabled
    from multiplayer_policy import blocks_command
    fresh_save=_fresh_enabled(db,uid)
    from new_player_login_policy import LoginPolicy
    login_policy = LoginPolicy(db, uid)
    login_policy.report(log)
    if not frames:
        log("[login] login_push 表为空，无洪流", "WARN")
        return 0, 0
    frames = osiris_point_frame(frames, generator)
    try:
        from birthday_service import ensure_official_mail
        if ensure_official_mail(db,uid):
            log('[birthday] 官方生日邮件已投递', 'INFO')
    except Exception as exc:
        log(f'[birthday] 官方生日邮件未完成，下次登录再检查: {exc}', 'WARN')
    try:
        from support_cosmetic_service import ensure_support_cosmetic_mails
        extra_mail_ids = ensure_support_cosmetic_mails(db, uid)
        if extra_mail_ids:
            log(f'[support] 装饰补给邮件已补齐: {len(extra_mail_ids)}封', 'INFO')
    except Exception as exc:
        log(f'[support] 装饰补给暂未完成，下次登录重试: {exc}', 'WARN')
    from paid_entitlement_service import settle as settle_paid_rights
    settle_paid_rights(db, uid, notify=False)
    # Recover knowledge before currency/task snapshots, without online deltas.
    if login_policy.allows(28801):
        from quanzhou_collection import restore_for_login as restore_quanzhou
        restore_quanzhou(db, uid)
    # Progress is account data even when an older login row is static.
    # Supply missing packets before the login-complete marker as well.
    present_cmds = {row[1] for row in frames}
    if fresh_save:
        missing_fresh=[cmd for cmd in (12009,12011,12111,28001,41001,59011) if cmd not in present_cmds]
        marker=next((i for i,row in enumerate(frames) if row[1]==10201),len(frames))
        frames[marker:marker]=[(0,cmd,generator.build_frame(cmd,b'')[:11],b'',1,None) for cmd in missing_fresh]
    missing_account_pushes = [cmd for cmd in (47001, 47003, 24029, 24061, 30001, 19029, 34031, 34023, 34101, 52021, 53001, 68181, 12119, 89011, 68171, 24051, 24043, 85035, 49001, 49023, 51001, 83016, 83017, 83020, 83021, 83026, 79601, 28801, 28837, 86035) if cmd not in present_cmds]
    if missing_account_pushes:
        marker = next((i for i, row in enumerate(frames) if row[1] == 10201), len(frames))
        additions = [(0, cmd, generator.build_frame(cmd, b"")[:11], b"", 1, None)
                     for cmd in missing_account_pushes]
        frames[marker:marker] = additions
    # p49 initializes account-wide progress/time tables. Captured duplicate or
    # missing rows must never revive another account's exploration state.
    chess_commands = (49001, 49023)
    frames = [r for r in frames if r[1] not in chess_commands]
    marker = next((i for i,r in enumerate(frames) if r[1] == 10201), len(frames))
    frames[marker:marker] = [(0,cmd,generator.build_frame(cmd,b'')[:11],b'',1,None)
                             for cmd in chess_commands if login_policy.allows(cmd)]
    # 83017's Lua reader requires the activity initialized by83016 first.
    schedules=[r for r in frames if r[1]==83017]
    if schedules:
        frames=[r for r in frames if r[1]!=83017]
        at=next(i for i,r in enumerate(frames) if r[1]==83016)+1
        frames[at:at]=schedules[:1]
    # These readers initialize whole tables. Replace captured duplicates with
    # exactly one account-owned snapshot after the activity topology.
    quanzhou_commands = (79601, 28801, 28837, 86035)
    frames = [r for r in frames if r[1] not in quanzhou_commands]
    marker = next((i for i, r in enumerate(frames) if r[1] == 10201), len(frames))
    frames[marker:marker] = [(0, cmd, generator.build_frame(cmd, b'')[:11], b'', 1, None)
                             for cmd in quanzhou_commands if login_policy.allows(cmd)]
    frames = [(seq, cmd, h, p, 1 if cmd == 15009 else dynamic, pj)
              for seq, cmd, h, p, dynamic, pj in frames]
    # 一次登录洪流必须只属于一个资源版本。运行中切换只影响下一次洪流，
    # 避免 192 帧发送途中出现 229/311 活动 ID 混发。
    import res_version_manager as _rvm
    login_res_version = _rvm.get_current_version()
    # 311_CORE_VERIFICATION_GUARD:
    # Theme 44+ (Build 311, 321, 322...) 的活动根 111 只开放 template=433 (Mode 4)。旧登录表仍含 sc_89013
    # (Mode 1)；若让它回退到库内 blob，311+ 会在 CoreVerificationChallengeData.InitTaskInfo
    # 中索引 ActivityCfg[nil] 并卡死大厅 Loading。这里必须跳过整帧，不能返回 None 走 blob 兜底。
    login_ver_cfg = _rvm.get_version_config(login_res_version)
    suppressed_cmds = set(login_ver_cfg.get("suppressed_cmds", set()))
    sent = dyn_hit = 0
    first_frame_ns = login_marker_ns = last_frame_ns = None
    generation_timings = {28001: (0, 0), 16015: (0, 0)}

    def send_frame(cmd, frame):
        nonlocal sent, first_frame_ns, login_marker_ns, last_frame_ns
        sock.sendall(frame)
        last_frame_ns = time.perf_counter_ns()
        sent += 1
        if first_frame_ns is None:
            first_frame_ns = last_frame_ns
        if cmd == 10201 and login_marker_ns is None:
            login_marker_ns = last_frame_ns

    def elapsed_ms(end_ns):
        return "-" if end_ns is None else f"{(end_ns - started_ns) / 1_000_000:.2f}"

    sock.settimeout(5)

    # 登录前置强制触发 lazy_timer 脉冲（保证登录洪流下发的所有数据处于最新周期状态）
    if uid and db is not None:
        try:
            from lazy_timer import lazy_timer
            class _LoginCtx:
                is_login_settlement = True

                def __init__(self, d, l):
                    self.db = d
                    self.log = l
            lazy_timer.pulse(_LoginCtx(db, log), uid, force=True)
        except Exception as _lte:
            log(f"[login] 登录前置 lazy_timer 脉冲异常: {_lte}", "WARN")

    oath_rights_profile = reconcile_oath_rights_before_login(db, uid, log)
    # Replace every historical draw row, including static rows and missing pools.
    # Insert the whole catalogue after activity topology and before login complete.
    from skin_draw_service import DRAW_STATE_COMMANDS
    if any(login_policy.allows(cmd) for cmd in DRAW_STATE_COMMANDS):
        skin_frames, skin_draw_profile = prepare_skin_draw_login(db, uid, login_res_version, generator)
        skin_frames = [row for row in skin_frames if login_policy.allows(row[1])]
    else:
        # Skip before pool initialization as well as before transmission.
        skin_frames, skin_draw_profile = [], False
    frames = [row for row in frames if row[1] not in DRAW_STATE_COMMANDS]
    # 限期委培与联防协议状态由当前活动列表和存档生成。
    activity_state_commands = (64077, 64081, 64083, 76151)
    frames = [row for row in frames if row[1] not in activity_state_commands]
    marker = next((i for i, row in enumerate(frames) if row[1] == 10201), len(frames))
    if skin_frames:
        for cmd in (20007, 20009, 11001):
            if cmd not in present_cmds:
                frames.insert(marker, (0, cmd, generator.build_frame(cmd, b'')[:11], b'', 1, None))
                marker += 1
    frames[marker:marker] = skin_frames
    dynamic_profile = oath_rights_profile or skin_draw_profile
    if dynamic_profile and 32009 not in present_cmds:
        marker = next((i for i, row in enumerate(frames) if row[1] == 10201), len(frames))
        frames.insert(marker, (0, 32009, generator.build_frame(32009, b"")[:11], b"", 1, None))

    # Replace captured PVE snapshots as one coherent account-owned sequence.
    # Replay actions are request-only: 89113 would launch battle during login.
    from autochess_login import prepare_login_frames as prepare_autochess_login
    frames, autochess_payloads = prepare_autochess_login(
        frames, db, uid, generator, login_policy, log)
    from rogueteam_login import prepare_login_frames as prepare_rogueteam_login
    frames, rogue_payloads = prepare_rogueteam_login(frames, db, uid, generator, login_policy)
    from polyhedron_login import prepare_login_frames as prepare_polyhedron_login
    frames, polyhedron_payloads = prepare_polyhedron_login(
        frames, db, uid, generator, login_policy, res_version=login_res_version)
    owned_payloads = {**autochess_payloads, **rogue_payloads, **polyhedron_payloads}

    from core_special_challenge import COMMANDS as special_commands, active_groups
    special_now = int(time.time())
    topology_index = next((i for i, row in enumerate(frames) if row[1] == 11001), -1)
    marker_index = next((i for i, row in enumerate(frames) if row[1] == 10201), len(frames))
    special_slot = next((i for i in range(topology_index + 1, marker_index)
                         if frames[i][1] in special_commands.values()), marker_index)
    special_insert_at = sum(row[1] not in special_commands.values() for row in frames[:special_slot])
    frames = [row for row in frames if row[1] not in special_commands.values()]
    if generator is not None:
        special_rows = []
        for group in active_groups(login_res_version, special_now):
            cmd = special_commands[group.mode]
            if login_policy.allows(cmd) and cmd not in suppressed_cmds:
                payload = generator.gen_payload(cmd, uid=uid, db=db, res_version=login_res_version,
                                                special_now=special_now)
                if not payload:
                    raise RuntimeError(f"特殊挑战状态 sc_{cmd} 生成失败")
                owned_payloads[cmd] = payload
                special_rows.append((0, cmd, generator.build_frame(cmd, b"")[:11], payload, 0, None))
        frames[special_insert_at:special_insert_at] = special_rows

    try:
        from codec import encode as _codec_encode
        _c_enc = _codec_encode
    except Exception:
        _codec_encode = None

    if login_policy.allows(73007) and 73007 not in suppressed_cmds:
        try:
            claimed_rows = db.query(
                "SELECT key_id FROM claim_ledger WHERE uid=? AND kind='relation_story' ORDER BY key_id",
                (uid,),
            ) if uid else ()
            story_payload = _codec_encode("sc_73007", {
                "got_reward_id_list": [int(row["key_id"]) for row in claimed_rows],
            })
            if not isinstance(story_payload, bytes):
                raise ValueError("羁绊领奖状态编码失败")
        except Exception as exc:
            frames = [row for row in frames if row[1] != 73007]
            log(f"[login] 羁绊领奖状态不可用，跳过 sc_73007: {exc}", "WARN")
        else:
            # An empty payload is a valid snapshot. Preserve the first capture's
            # position and header, or insert a missing snapshot before login completion.
            owned_payloads[73007] = story_payload
            story_index = next((i for i, row in enumerate(frames) if row[1] in (73007, 10201)), len(frames))
            if story_index == len(frames) or frames[story_index][1] == 10201:
                header = generator.build_frame(73007, b"")[:11]
                frames.insert(story_index, (0, 73007, header, b"", 0, None))
            frames[story_index + 1:] = [row for row in frames[story_index + 1:] if row[1] != 73007]

    prepared_ns = time.perf_counter_ns()
    try:
        for seq, cmd, header, blob, dynamic, payload_json in frames:
            if blocks_command(cmd):
                log(f'[offline] omit sc_{cmd}: multiplayer disabled','INFO')
                continue
            if not login_policy.allows(cmd):
                continue
            if fresh_save and cmd==10039:
                # GatewayHandler already supplies the actual endpoint/session.
                # Never replay or zero this authentication response on game TCP.
                continue
            if cmd in suppressed_cmds:
                log(
                    f"[login] version={login_res_version} 跳过不兼容推送 sc_{cmd} "
                    "(THEME44_CORE_VERIFICATION_GUARD)",
                    "INFO",
                )
                continue
            gen = owned_payloads.get(cmd)
            osiris_point_push = cmd == 60201 and payload_json and json.loads(payload_json).get('activity_id') == 323801
            if osiris_point_push:
                from osiris_service import point_payload
                gen = _codec_encode('sc_60201',point_payload(db,uid))
                if not gen:
                    raise RuntimeError('战术研习积分帧生成失败')

            # 1. dynamic == 1 时优先使用 generator 业务生成器
            if cmd not in owned_payloads and not osiris_point_push and (fresh_save or dynamic or cmd in (47001, 47003, 24029, 24061, 30001, 11001, 18001, 20007, 20009, 52001, 52021, 53001, 19029, 34031, 34023, 34025, 34101, 68181, 12119, 89011, 68171, 24051, 24043, 85035, 49001, 49023, 51001, 83016, 83017, 83020, 83021, 83026, 79601, 28801, 28837, 86035) or (cmd == 32009 and dynamic_profile)) and generator is not None:
                try:
                    version_args = {"res_version": login_res_version}
                    if cmd == 11001:
                        version_args["special_now"] = special_now
                    timing = generation_timings.get(cmd)
                    generation_started_ns = time.perf_counter_ns() if timing is not None else None
                    try:
                        gen = generator.gen_payload(cmd, uid=uid, db=db, **version_args)
                    finally:
                        if generation_started_ns is not None:
                            calls, duration_ns = timing
                            generation_timings[cmd] = (
                                calls + 1, duration_ns + time.perf_counter_ns() - generation_started_ns,
                            )
                except Exception as e:
                    log(f"[login] sc_{cmd} 业务动态生成异常: {e}", "WARN")
                    if fresh_save or cmd in (13009, 14009, 46011, 47001, 47003, 24009, 24029, 24061, 30001, 11001, 18001, 20007, 20009, 52001, 52021, 53001, 19029, 34031, 34023, 34025, 34101, 68181, 12119, 89011, 68171, 24051, 24043, 85035, 49001, 49023, 51001, 83016, 83017, 83020, 83021, 83026, 79601, 28801, 28837, 86035) or (cmd == 32009 and dynamic_profile):
                        raise RuntimeError(f"sc_{cmd} 当前资产读取失败，拒绝回退旧录制资产") from e
            if cmd in (68181, 12119, 89011, 68171, 24051, 24043, 85035, 49001, 49023, 51001, 83016, 83017, 83020, 83021, 83026, 79601, 28801, 28837, 86035) and gen is None:
                raise RuntimeError(f'Ash account push {cmd} missing dynamic payload')
            if cmd in (47001, 47003) and gen is None:
                raise RuntimeError(f"sc_{cmd} 教学进度生成失败")
            if cmd == 15009 and gen is None:
                raise RuntimeError('sc_15009 当前货币状态生成失败')
            if cmd in (34031, 34023, 34025, 34101) and gen is None:
                raise RuntimeError(f'sc_{cmd} 权益状态生成失败，拒绝回退录制状态')
            if cmd in (24029, 24061) and gen is None:
                raise RuntimeError(f'sc_{cmd} 主线地图进度生成失败')
            if cmd == 24009 and gen is None:
                raise RuntimeError('sc_24009 主支线进度生成失败，拒绝回退旧录制进度')
            if cmd == 52001 and gen is None:
                raise RuntimeError('sc_52001 图鉴进度生成失败')
            if cmd == 52021 and gen is None:
                raise RuntimeError('sc_52021 过场图设置生成失败')
            if cmd == 53001 and gen is None:
                raise RuntimeError('sc_53001 成就状态恢复失败')
            if cmd == 30001 and gen is None:
                raise RuntimeError('sc_30001 邮件摘要生成失败')
            if cmd == 18001 and gen is None:
                raise RuntimeError('sc_18001 多维历程生成失败')
            if cmd == 19029 and gen is None:
                raise RuntimeError('sc_19029 本地角色聊天初始化失败，拒绝回退旧好友记录')
            if cmd == 32009 and dynamic_profile and gen is None:
                raise RuntimeError("sc_32009 装扮状态生成失败")
            if cmd == 63005 and gen is None and generator is not None:
                gen = generator.gen_payload(cmd, uid=uid, db=db, res_version=login_res_version)
                if not gen:
                    raise RuntimeError("sc_63005 编队状态生成失败")
            if cmd in (11001, 20007, 20009) and gen is None:
                raise RuntimeError(f"sc_{cmd} 抽取活动/票券配置生成失败")

            if fresh_save and gen is None:
                import schema_default
                initial=schema_default.default_obj(f'sc_{cmd}')
                if not login_policy.allows_empty_default(cmd,initial):
                    keys=','.join(login_policy.empty_omissions[cmd])
                    log(f'[login-policy] omit sc_{cmd}: no state generator; {keys}=0','INFO')
                    continue
                gen=schema_default.build(f'sc_{cmd}')
                if gen is None:
                    raise RuntimeError(f'新档 sc_{cmd} 没有可用初始响应')
                log(f'[new-save] sc_{cmd} 使用字段完整的空初始响应','INFO')

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
                if fresh_save:
                    raise RuntimeError(f'新档 sc_{cmd} 禁止回退录制玩家数据')
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

            send_frame(cmd, frame)

            if cmd == 11001 and gen:
                from codec import decode
                from cultivate_hero_service import login_frames as cultivate_login_frames
                from pt_scroll_service import login_frames as pt_scroll_login_frames
                activities = decode(gen, "sc_11001").get("activity_list", [])
                for command, prepare in ((64077, cultivate_login_frames), (76151, pt_scroll_login_frames)):
                    if login_policy.allows(command):
                        for packet in prepare(db, uid, activities, _codec_encode):
                            send_frame(packet.cmd, generator.build_frame(packet.cmd, packet.payload))
                            dyn_hit += 1

            # 梦境再构联动补发 45001（普通首领列表）与 45101（进阶首领列表）
            if cmd == 45201 and generator is not None:
                for extra_cmd in (45001, 45101):
                    if not login_policy.allows(extra_cmd):
                        continue
                    try:
                        ext_gen = generator.gen_payload(
                            extra_cmd, uid=uid, db=db, res_version=login_res_version
                        )
                        if ext_gen:
                            ext_frame = generator.build_frame(extra_cmd, ext_gen)
                            send_frame(extra_cmd, ext_frame)
                            dyn_hit += 1
                            if frame_delay:
                                time.sleep(frame_delay)
                    except Exception as e:
                        log(f"[login] 联动下发 sc_{extra_cmd} 异常: {e}", "WARN")

            if frame_delay:
                time.sleep(frame_delay)

        if login_policy.allows(11003):
            # 多维变量联动下发 sc_11003：活动 ID/theme 与资源版本同步，
            # stop_time就是客户端展示的刷新时间，与实际历程周期共用一个计算器。
            # 多维变量周期 11003 推送
            try:
                import weekly_challenge_service as _wcs
                from codec import encode as _c_enc
                p11003 = _c_enc("sc_11003", {
                    "activity": _wcs.get_polyhedron_activity(login_res_version)
                })
                if p11003 and generator is not None:
                    frame_11003 = generator.build_frame(11003, p11003)
                    send_frame(11003, frame_11003)
                    dyn_hit += 1
            except Exception as _e:
                log(f"[login] 多维变量周期 11003 推送异常: {_e}", "WARN")

        # 好感与人际关系网登录联动推送：sc_73005 (礼物置换限额) + sc_73001 (连携技能网)
        try:
            from trust_service import TrustService
            _trust_svc = TrustService.get_instance()
            p73005 = _c_enc("sc_73005", _trust_svc.get_displace_limit_payload(db, uid) if (db and uid) else {"limit_list": []})
            if p73005 and generator is not None:
                send_frame(73005, generator.build_frame(73005, p73005))
                dyn_hit += 1

            # sc_73001 连携技能（协作技能网）全量等级与进度：客户端 ComboSkillData:InitLevelData 初始化
            p73001 = _c_enc("sc_73001", _trust_svc.get_combo_skill_payload(db, uid) if (db and uid) else {"info": {"skill_list": []}})
            if p73001 and generator is not None:
                send_frame(73001, generator.build_frame(73001, p73001))
                dyn_hit += 1
        except Exception as _te:
            log(f"[login] 礼物置换与连携技能登录态推送异常: {_te}", "WARN")

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
        finished_ns = time.perf_counter_ns()
        task_calls, task_ns = generation_timings[28001]
        draw_calls, draw_ns = generation_timings[16015]
        log(
            f"[login] 登录耗时(ms): 准备={elapsed_ms(prepared_ns)} 首帧={elapsed_ms(first_frame_ns)} "
            f"10201={elapsed_ms(login_marker_ns)} 末帧={elapsed_ms(last_frame_ns)} 总计={elapsed_ms(finished_ns)} "
            f"28001生成={task_ns / 1_000_000:.2f}({task_calls}次) "
            f"16015生成={draw_ns / 1_000_000:.2f}({draw_calls}次)"
        )
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
