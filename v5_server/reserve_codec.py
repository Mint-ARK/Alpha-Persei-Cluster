# -*- coding: utf-8 -*-
"""
reserve_codec.py — 预备编队与出战队伍持久化编解码器（sc_63005 / cs_63000 / sc_63999）

职责：
1. 从 account.db 的 reserve_team 与 reserve_team_hero 表中提取全部玩法编队；
2. 动态序列化生成符合官方 Protobuf 结构的 sc_63005（登录洪流与即时同步）；
3. 接收并解析客户端编队保存报文 cs_63000，精准写入数据库；
4. 战斗开战 (cs_54030) 与通关 (cs_54032) 自动将出战队伍持久化覆盖。
"""
import json
import time


def _varint(v):
    v = int(v)
    if v < 0:
        v &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        x = v & 0x7F
        v >>= 7
        if v:
            out.append(x | 0x80)
        else:
            out.append(x)
            return bytes(out)


def _tag(fnum, wt):
    return _varint((fnum << 3) | wt)


def _pb_varint(fnum, v):
    return _tag(fnum, 0) + _varint(v)


def _pb_msg(fnum, body):
    if not body:
        return b""
    return _tag(fnum, 2) + _varint(len(body)) + body


def build_hero_info_bytes(hero_id, owner_id=0, hero_type=1):
    """构建单个英雄槽位 hero_info_net_rec (f1=hero_id, f2=owner_id, f3=hero_type)"""
    b = bytearray()
    b += _pb_varint(1, int(hero_id or 0))
    if owner_id:
        b += _pb_varint(2, int(owner_id))
    b += _pb_varint(3, int(hero_type or 1))
    return bytes(b)


def build_mimir_bytes(mimir_id=0, chip_ids_str="", hero_chip_ids_str="", hero_id=0):
    """构建钥从与芯片 mimir_net_rec (f1=mimir_id, f2=repeated chip_list, f3=hero_id, f4=repeated hero_chip_list)"""
    b = bytearray()
    if mimir_id:
        b += _pb_varint(1, int(mimir_id))
    
    if chip_ids_str:
        for c in str(chip_ids_str).split(","):
            c = c.strip()
            if c and c.isdigit() and int(c) > 0:
                b += _pb_varint(2, int(c))
                
    if hero_id:
        b += _pb_varint(3, int(hero_id))
        
    if hero_chip_ids_str:
        for hc in str(hero_chip_ids_str).split(","):
            hc = hc.strip()
            if hc and hc.isdigit() and int(hc) > 0:
                b += _pb_varint(4, int(hc))
    return bytes(b)


def build_teams_net_rec_bytes(team_index, heroes, cooperate_skill=0,
                              mimir_id=0, chip_ids="", hero_chip_ids=""):
    """构建单个队伍 teams_net_rec (f1=team_index, f2=repeated hero_list, f3=cooperate_unique_skill_id, f4=mimir_info)"""
    b = bytearray()
    b += _pb_varint(1, int(team_index or 0))
    
    # 3 个槽位英雄
    for h in (heroes or []):
        hid = h.get("hero_id") or h.get("id") or 0
        oid = h.get("owner_id") or 0
        htype = h.get("hero_type") or 1
        b += _pb_msg(2, build_hero_info_bytes(hid, oid, htype))
        
    if cooperate_skill:
        b += _pb_varint(3, int(cooperate_skill))
        
    mimir_b = build_mimir_bytes(mimir_id, chip_ids, hero_chip_ids)
    if mimir_b:
        b += _pb_msg(4, mimir_b)
    return bytes(b)


def build_sc_63005(db, uid):
    """构建 sc_63005 报文（TEAM SERVER 接管版）

    数据源：stage_team 关卡现役编队表（关卡键原子分立，cont_id=关卡键 activity_id）。
    官方抓包静态数据与被出战污染的 reserve_team 预设不再下发。
    空数据时组最小合法帧（team_type=0），避免客户端 InitData nil 崩溃。

    结构：
    sc_63005:
      f1 = repeated teams_info_list (mulit_cont_teams_net_rec)
        f1 = team_type
        f2 = repeated cont_teams (single_cont_teams_net_rec)
          f1 = cont_id（关卡键 activity_id）
          f2 = repeated teams (teams_net_rec)
    """
    if db is None:
        return None

    raw_conn = db._conn() if hasattr(db, "_conn") else db
    cur = raw_conn.cursor()

    # 1. 关卡现役编队（TEAM SERVER 数据源）
    cur.execute(
        "SELECT stage_type, activity_id, team_index, hero_json, cooperate_skill, "
        "mimir_id, mimir_chips, hero_chips FROM stage_team WHERE uid=? "
        "ORDER BY stage_type, activity_id, team_index;", (uid,))
    team_rows = [dict(r) for r in cur.fetchall()]

    # 2. 英雄次序化（hero_json 有序数组，[0]=队长位；槽位从 1 起）
    for tr in team_rows:
        try:
            heroes = json.loads(tr.get("hero_json") or "[]")
        except Exception:
            heroes = []
        tr["heroes"] = [{"slot": i + 1, "hero_id": int(h), "owner_id": 0, "hero_type": 1}
                        for i, h in enumerate(heroes) if int(h or 0) > 0]

    # 3. 分组聚合: team_type -> activity_id -> list of teams
    grouped = {}
    for tr in team_rows:
        tt = int(tr["stage_type"])
        cid = int(tr["activity_id"])
        grouped.setdefault(tt, {}).setdefault(cid, []).append(tr)

    # 4. 序列化 protobuf
    body = bytearray()
    for tt, cont_dict in grouped.items():
        multi_b = bytearray()
        multi_b += _pb_varint(1, int(tt))

        for cid, t_list in cont_dict.items():
            single_b = bytearray()
            single_b += _pb_varint(1, int(cid))

            for tr in t_list:
                t_bytes = build_teams_net_rec_bytes(
                    team_index=tr["team_index"],
                    heroes=tr["heroes"],
                    cooperate_skill=tr.get("cooperate_skill") or 0,
                    mimir_id=tr.get("mimir_id") or 0,
                    chip_ids=tr.get("mimir_chips") or "",
                    hero_chip_ids=tr.get("hero_chips") or ""
                )
                single_b += _pb_msg(2, t_bytes)

            multi_b += _pb_msg(2, bytes(single_b))

        body += _pb_msg(1, bytes(multi_b))

    if not body:
        # 最小合法帧：team_type=0 空 cont_teams（官方新号语义，无幻影编队）
        body = bytearray()
        body += _pb_varint(1, 0)

    return bytes(body)


def _read_varint_from(buf, p):
    v = 0
    s = 0
    while p < len(buf):
        b = buf[p]
        p += 1
        v |= (b & 0x7f) << s
        s += 7
        if not (b & 0x80):
            break
    return v, p


def parse_cs_63000_raw(payload):
    """直接解析 cs_63000 二进制报文，提取 team_type 与 cont_team 多队结构"""
    pos = 0
    res = {"team_type": 0, "cont_team": {"cont_id": 0, "teams": []}}
    while pos < len(payload):
        tag, pos = _read_varint_from(payload, pos)
        field_num = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:
            val, pos = _read_varint_from(payload, pos)
            if field_num == 1:
                res["team_type"] = val
        elif wire_type == 2:
            length, pos = _read_varint_from(payload, pos)
            chunk = payload[pos:pos+length]
            pos += length
            if field_num == 2:
                c_pos = 0
                while c_pos < len(chunk):
                    ctag, c_pos = _read_varint_from(chunk, c_pos)
                    cf_num = ctag >> 3
                    cw_type = ctag & 7
                    if cw_type == 0:
                        cval, c_pos = _read_varint_from(chunk, c_pos)
                        if cf_num == 1:
                            res["cont_team"]["cont_id"] = cval
                    elif cw_type == 2:
                        clength, c_pos = _read_varint_from(chunk, c_pos)
                        tchunk = chunk[c_pos:c_pos+clength]
                        c_pos += clength
                        if cf_num == 2:
                            t_pos = 0
                            team = {
                                "team_index": 0,
                                "hero_list": [],
                                "cooperate_unique_skill_id": 0,
                                "mimir_info": {"mimir_id": 0, "chip_list": [], "hero_chip_list": []}
                            }
                            while t_pos < len(tchunk):
                                ttag, t_pos = _read_varint_from(tchunk, t_pos)
                                fn = ttag >> 3
                                wt = ttag & 7
                                if wt == 0:
                                    v, t_pos = _read_varint_from(tchunk, t_pos)
                                    if fn == 1: team["team_index"] = v
                                    elif fn == 3: team["cooperate_unique_skill_id"] = v
                                elif wt == 2:
                                    l, t_pos = _read_varint_from(tchunk, t_pos)
                                    sub = tchunk[t_pos:t_pos+l]
                                    t_pos += l
                                    if fn == 2:
                                        h_pos = 0
                                        hero = {"hero_id": 0, "hero_type": 1}
                                        while h_pos < len(sub):
                                            htag, h_pos = _read_varint_from(sub, h_pos)
                                            hfn = htag >> 3
                                            hwt = htag & 7
                                            if hwt == 0:
                                                hv, h_pos = _read_varint_from(sub, h_pos)
                                                if hfn == 1: hero["hero_id"] = hv
                                                elif hfn == 3: hero["hero_type"] = hv
                                        team["hero_list"].append(hero)
                                    elif fn == 4:
                                        m_pos = 0
                                        while m_pos < len(sub):
                                            mtag, m_pos = _read_varint_from(sub, m_pos)
                                            mfn = mtag >> 3
                                            mwt = mtag & 7
                                            if mwt == 0:
                                                mv, m_pos = _read_varint_from(sub, m_pos)
                                                if mfn == 1: team["mimir_info"]["mimir_id"] = mv
                                                elif mfn == 2: team["mimir_info"]["chip_list"].append(mv)
                                                elif mfn == 3: team["mimir_info"]["hero_chip_list"].append(mv)
                            res["cont_team"]["teams"].append(team)
    return res


def save_team_from_cs_63000(db, uid, req_data):
    """解析客户端 cs_63000 编队保存请求并写入数据库"""
    if db is None or not req_data:
        return False
    
    def _to_int(v, default=0):
        try:
            return int(v)
        except Exception:
            return default

    team_type = _to_int(req_data.get("team_type"))
    cont_team = req_data.get("cont_team")
    cont_team = cont_team if isinstance(cont_team, dict) else {}
    cont_id = _to_int(cont_team.get("cont_id"))
    teams = cont_team.get("teams") or []
    
    raw_conn = db._conn() if hasattr(db, "_conn") else db
    cur = raw_conn.cursor()
    now_ts = int(time.time())
    
    for t in teams:
        if not isinstance(t, dict):
            continue
        t_idx = _to_int(t.get("team_index"))
        coop_skill = _to_int(t.get("cooperate_unique_skill_id"))
        mimir = t.get("mimir_info")
        mimir = mimir if isinstance(mimir, dict) else {}
        mimir_id = _to_int(mimir.get("mimir_id"))
        chip_list = mimir.get("chip_list") or []
        chip_ids = ",".join(str(_to_int(x)) for x in chip_list if _to_int(x) > 0)
        hero_chips = mimir.get("hero_chip_list") or []
        hero_chip_ids = ",".join(str(_to_int(x)) for x in hero_chips if _to_int(x) > 0)
        
        target_types = [team_type]
        if team_type == 11:
            target_types.append(53)
            if hasattr(db, "save_equip_seizure_team"):
                db.save_equip_seizure_team(uid, [h.get("hero_id") or h.get("id") for h in (t.get("hero_list") or []) if isinstance(h, dict)])
        elif team_type == 53:
            target_types.append(11)

        for tt in target_types:
            # 1. 写入 reserve_team
            cur.execute("""
                INSERT OR REPLACE INTO reserve_team 
                (uid, team_type, cont_id, team_index, cooperate_skill, mimir_id, chip_ids, hero_chip_ids, update_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (uid, tt, cont_id, t_idx, coop_skill, mimir_id, chip_ids, hero_chip_ids, now_ts))
            
            # 2. 清理旧槽位并写入 reserve_team_hero
            cur.execute("""
                DELETE FROM reserve_team_hero 
                WHERE uid=? AND team_type=? AND cont_id=? AND team_index=?;
            """, (uid, tt, cont_id, t_idx))
            
            hero_list = t.get("hero_list") or []
            for slot, h in enumerate(hero_list, 1):
                if not isinstance(h, dict):
                    continue
                hid = _to_int(h.get("hero_id") or h.get("id"))
                oid = _to_int(h.get("owner_id"))
                htype = _to_int(h.get("hero_type"), 1)
                if hid > 0:
                    cur.execute("""
                        INSERT OR REPLACE INTO reserve_team_hero
                        (uid, team_type, cont_id, team_index, slot, hero_id, owner_id, hero_type, update_ts)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (uid, tt, cont_id, t_idx, slot, hid, oid, htype, now_ts))
                
    raw_conn.commit()
    return True


def sync_stage_battle_team(db, uid, dest, stage_type, heroes, cooperate_skill=0, mimir_info=None):
    """战斗开战或通关时，将出战队伍持久化至 reserve_team 与 reserve_team_hero

    覆盖规则：
    - 默认队伍 (team_type=0, cont_id=0, team_index=0) 始终更新；
    - 若传入了特定 stage_type (如日常本 5, 主线 1)，同步更新对应 team_type；
    """
    if db is None or not heroes:
        return
    
    raw_conn = db._conn() if hasattr(db, "_conn") else db
    cur = raw_conn.cursor()
    now_ts = int(time.time())
    
    mimir = mimir_info or {}
    mimir_id = int(mimir.get("mimir_id") or 0) if isinstance(mimir, dict) else 0
    chip_list = mimir.get("chip_list") or [] if isinstance(mimir, dict) else []
    chip_ids = ",".join(str(x) for x in chip_list if x)
    
    target_types = set([0])  # 默认队伍始终同步
    if stage_type is not None:
        st_int = int(stage_type)
        target_types.add(st_int)
        if st_int == 53:
            target_types.add(11)
    if dest and 3070101 <= int(dest) <= 3070126:
        target_types.add(11)
        target_types.add(53)
        
    for tt in target_types:
        # 1. 更新 reserve_team
        cur.execute("""
            INSERT OR REPLACE INTO reserve_team 
            (uid, team_type, cont_id, team_index, cooperate_skill, mimir_id, chip_ids, hero_chip_ids, update_ts)
            VALUES (?, ?, 0, 0, ?, ?, ?, '', ?);
        """, (uid, tt, int(cooperate_skill or 0), mimir_id, chip_ids, now_ts))
        
        # 2. 清理并更新 reserve_team_hero
        cur.execute("""
            DELETE FROM reserve_team_hero 
            WHERE uid=? AND team_type=? AND cont_id=0 AND team_index=0;
        """, (uid, tt))
        
        for slot, h in enumerate(heroes, 1):
            hid = int(h if isinstance(h, int) else (h.get("hero_id") or h.get("id") or 0))
            if hid > 0:
                cur.execute("""
                    INSERT OR REPLACE INTO reserve_team_hero
                    (uid, team_type, cont_id, team_index, slot, hero_id, owner_id, hero_type, update_ts)
                    VALUES (?, ?, 0, 0, ?, ?, 0, 1, ?);
                """, (uid, tt, slot, hid, now_ts))
                
    raw_conn.commit()


def get_stage_preset_heroes(db, uid, dest=0, stage_type=0):
    """查询指定关卡现役队伍的英雄列表（扫荡兜底，TEAM SERVER stage_team 数据源）

    优先按 (stage_type, dest) 的官方关卡键查现役队伍；无记录回退默认 []（玩家现配）。
    """
    if db is None:
        return []
    try:
        from team_server import TeamServer as _TS
        heroes = _TS.get_instance().get_stage_heroes(db, uid, stage_type, dest)
        if heroes:
            return heroes
    except Exception:
        pass
    return []

