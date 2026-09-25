# -*- coding: utf-8 -*-
"""
battle_payload.py — 动态战斗初始化帧生成器（sc_54003）

职责：
1. 抛弃静态 sc_54003_payload.bin 模板依赖；
2. 依据玩家 account.db 中的实时数据，将选中的出战英雄参数准确载入战斗：
   - 基础养成：等级、突破阶数、星级、经验、皮肤
   - 技能系统：全部普攻/技能/大招等级与属性强化 (skill_intensify)
   - 武器与钥从：武器经验、突破阶数、专属钥从 (servant)
   - 神格星盘：当前激活的神格节点 (using_astrolabe) 与跃迁 (exclusive_skill_list)
   - 刻印装备：全套 6 件刻印 prefab_id、突破等级、阵营归属 (race) 与双槽赋能词条 (enchant_slots)
   - 好感与心境：trust_level、trust_exp、trust_mood
"""
import json
import os
import sys

# 引用同目录 hero_codec
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import hero_codec


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


def _pb_str(fnum, s):
    if not s:
        return b""
    b = s.encode("utf-8") if isinstance(s, str) else s
    return _tag(fnum, 2) + _varint(len(b)) + b


def build_enchant_slot_bytes(slot_data):
    """构建单个赋能槽位 enchant_slot_net_rec (f1=id, f2=repeated effect_list{id, level})"""
    sid = slot_data.get("id", 1)
    body = _pb_varint(1, sid)
    for eff in slot_data.get("effect_list", []):
        eid = eff.get("id")
        elv = eff.get("level", 1)
        if eid:
            eff_body = _pb_varint(1, int(eid)) + _pb_varint(2, int(elv))
            body += _pb_msg(2, eff_body)
    return body


def build_battle_equip_bytes(eq_row):
    """构建单个刻印 battle_equip_net_rec

    f1=equip_id, f2=prefab_id, f3=exp, f4=hero_id, f5=is_lock,
    f6=now_break_level, f7=repeated enchant_slot_list, f8=race
    """
    body = bytearray()
    body += _pb_varint(1, int(eq_row.get("id") or 0))
    body += _pb_varint(2, int(eq_row.get("prefab_id") or 0))
    if eq_row.get("exp"):
        body += _pb_varint(3, int(eq_row["exp"]))
    if eq_row.get("hero_id"):
        body += _pb_varint(4, int(eq_row["hero_id"]))
    if eq_row.get("is_lock"):
        body += _pb_varint(5, 1 if eq_row["is_lock"] else 0)
    if eq_row.get("now_break_level"):
        body += _pb_varint(6, int(eq_row["now_break_level"]))

    # 赋能槽位解析
    enchants = eq_row.get("enchant_slots")
    if enchants:
        if isinstance(enchants, str):
            try:
                enchants = json.loads(enchants)
            except Exception:
                enchants = []
        if isinstance(enchants, list):
            for es in enchants:
                if isinstance(es, dict) and es.get("effect_list"):
                    slot_b = build_enchant_slot_bytes(es)
                    body += _pb_msg(7, slot_b)

    if eq_row.get("race"):
        body += _pb_varint(8, int(eq_row["race"]))
    return bytes(body)


def build_battle_hero_bytes(db_conn, uid, hid):
    """构建单个出战英雄 battle_hero_net_rec

    f1 = hero_base_info (HERO_BASE_INFO 完整养成)
    f2 = hero_type (1=自有角色)
    f3 = equip_list (repeated battle_equip_net_rec 刻印)
    f4 = dorm_level (0)
    f5 = trust (hero_trust_net_rec 好感度)
    f6 = main_damage_type (0)
    """
    cur = db_conn.cursor()
    cur.execute("SELECT * FROM hero WHERE uid=? AND id=?;", (uid, hid))
    row = cur.fetchone()
    if not row:
        # 尝试不限 uid 查兜底
        cur.execute("SELECT * FROM hero WHERE id=? LIMIT 1;", (hid,))
        row = cur.fetchone()

    h = dict(row) if row else {}

    # 查询角色绑定的专属钥从
    w_uid = h.get("weapon_servant_uid")
    if w_uid:
        cur.execute("SELECT prefab_id, stage FROM servant WHERE uid=? AND id=?;", (uid, w_uid))
        s_r = cur.fetchone()
        if not s_r:
            cur.execute("SELECT prefab_id, stage FROM servant WHERE id=? LIMIT 1;", (w_uid,))
            s_r = cur.fetchone()
        if s_r:
            h["servant_info"] = {"id": s_r[0], "stage": s_r[1]}

    # 预设试用角色库加载（严格基于 Lua TemplateHeroDataTemplate 与 HeroStandardSystemCfg）
    _trial_meta = None
    try:
        _meta_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trial_system_data.json")
        if os.path.exists(_meta_path):
            with open(_meta_path, "r", encoding="utf-8") as _f:
                _trial_meta = json.load(_f)
    except Exception:
        _trial_meta = None

    std_cfgs = _trial_meta.get("std_cfgs", {}) if _trial_meta else {}
    std_info = std_cfgs.get(str(hid)) or std_cfgs.get(hid) if std_cfgs else None

    # 如果是预设试用角色，严格按照 TemplateHeroDataTemplate.lua 构建
    if std_info:
        real_hid = int(std_info.get("hero_id") or hid)
        skin_id = int(std_info.get("skin_id") or real_hid)
        h_lv = int(std_info.get("hero_lv") or 80)
        s_lv = int(std_info.get("star_lv") or 500)
        b_lv = int(std_info.get("hero_break") or std_info.get("break_lv") or 6)
        sk_lv = int(std_info.get("skill_lv") or 35)
        w_lv = int(std_info.get("weapon_level") or 60)
        w_brk = int(std_info.get("weapon_break") or 4)
        w_key = int(std_info.get("weapon_key") or 0)
        w_stage = int(std_info.get("weapon_stage") or 1)
        w_mod = int(std_info.get("weapon_module_level") or 0)
        astro_ids = std_info.get("astrolabe_id") or []

        # 1. 技能表（GetSkillTable: 普攻/技能为 skill_lv，闪避 avoid 为 1 级）
        hero_cfgs = _trial_meta.get("hero_cfgs", {}) if _trial_meta else {}
        h_meta = hero_cfgs.get(str(real_hid)) or {}
        skills = h_meta.get("skills") or []
        avoid = h_meta.get("avoid") or []
        skill_b = bytearray()
        for sid in skills:
            if sid > 0:
                lvl = 1 if sid in avoid else sk_lv
                skill_b += _pb_msg(5, _pb_varint(1, sid) + _pb_varint(2, lvl))
        if not skill_b:
            skill_b += _pb_msg(5, _pb_varint(1, real_hid * 100 + 1) + _pb_varint(2, sk_lv))

        # 2. 武器与专属钥从
        weapon_b = _pb_varint(1, w_lv) + _pb_varint(2, w_brk) + _pb_varint(3, 0)
        servant_b = _pb_varint(1, w_key) + _pb_varint(2, w_stage)

        # 3. 神格 (using_astrolabe & unlock_astrolabe)
        astro_b = bytearray()
        for aid in astro_ids:
            if aid > 0:
                astro_b += _pb_varint(8, aid) + _pb_varint(9, aid)

        # 4. 跃迁专属技能 (InitTransitionByCfg -> exclusive_skill_list)
        ex_ids = std_info.get("equip_exclusive_id_list") or []
        ex_lvs = std_info.get("equip_exclusive_lv_list") or []
        exclusive_b = bytearray()
        for slot_idx, (s_ids, s_lvs) in enumerate(zip(ex_ids, ex_lvs)):
            s_b = bytearray()
            t_pts = 0
            for sid, slv in zip(s_ids, s_lvs):
                t_pts += slv
                s_b += _pb_msg(2, _pb_varint(1, sid) + _pb_varint(2, slv))
            if s_b:
                slot_b = _pb_varint(1, slot_idx + 1) + s_b + _pb_varint(3, t_pts)
                exclusive_b += _pb_msg(12, slot_b)

        # 5. 技能强化属性元素 (skill_intensify_attribute_list)
        elem_b = bytearray()
        for idx, lvl in enumerate(std_info.get("skill_element") or []):
            if lvl > 0:
                elem_b += _pb_msg(14, _pb_varint(1, idx + 1) + _pb_varint(2, lvl))

        hbi_body = (_pb_varint(1, real_hid) + _pb_varint(2, h_lv) +
                    _pb_varint(3, s_lv) + _pb_varint(4, 0) + skill_b +
                    _pb_msg(6, weapon_b) + _pb_msg(7, servant_b) +
                    astro_b +
                    _pb_varint(10, skin_id) + _pb_varint(11, b_lv) +
                    exclusive_b +
                    _pb_varint(13, w_mod) +
                    elem_b +
                    _pb_varint(15, skin_id))

        body = bytearray()
        body += _pb_msg(1, hbi_body)
        body += _pb_varint(2, int(hid))

        # 6. 专属试用刻印与赋能词条（GetConstructVirtualEquips）
        eq_stars = _trial_meta.get("eq_stars", {}) if _trial_meta else {}
        exp_cfgs = _trial_meta.get("exp_cfgs", {}) if _trial_meta else {}
        pool_cfgs = _trial_meta.get("pool_cfgs", {}) if _trial_meta else {}

        eq_lv = int(std_info.get("equip_lv") or 60)
        pool_list = std_info.get("equip_pool_list") or []
        for idx, p_id in enumerate(std_info.get("equip_list") or []):
            if p_id > 0:
                star = eq_stars.get(str(p_id)) or 5
                exp_info = exp_cfgs.get(str(eq_lv)) or exp_cfgs.get(eq_lv) or {}
                eq_exp = exp_info.get(f"exp_sum_{star}") or 0
                now_brk = max(0, b_lv - 1)

                eq_b = bytearray()
                eq_b += _pb_varint(1, int(hid) * 100 + (idx + 1))
                eq_b += _pb_varint(2, p_id)
                eq_b += _pb_varint(3, eq_exp)
                eq_b += _pb_varint(4, real_hid)
                eq_b += _pb_varint(5, 1)
                eq_b += _pb_varint(6, now_brk)

                if idx < len(pool_list) and pool_list[idx]:
                    for s_idx, pool_id in enumerate(pool_list[idx]):
                        s_pairs = pool_cfgs.get(str(pool_id)) or []
                        eff_b = bytearray()
                        for p in s_pairs:
                            eff_b += _pb_msg(2, _pb_varint(1, p[0]) + _pb_varint(2, p[1]))
                        if eff_b:
                            slot_b = _pb_varint(1, s_idx + 1) + eff_b
                            eq_b += _pb_msg(7, slot_b)

                eq_b += _pb_varint(8, real_hid)
                body += _pb_msg(3, eq_b)

        # 7. 宿舍与好感度
        body += _pb_varint(4, 0)
        rel_b = _pb_msg(1, _pb_varint(1, 1))
        trust_b = _pb_varint(1, 1) + _pb_varint(2, 0) + _pb_varint(3, 1) + _pb_msg(4, rel_b)
        body += _pb_msg(5, trust_b)
        body += _pb_varint(6, 0)
        return bytes(body)

    # 1. 玩家自持角色：构建完整 hero_base_info
    exp = hero_codec.load_hero_expanded(hid)
    hero_type_val = 1
    if exp:
        if h:
            hero_codec.apply_db(exp, h)
        hbi = next((it[2] for it in exp if it[0] == 1 and it[1] == "m"), None)
        hbi_body = hero_codec.encode_expanded(hbi) if hbi else b""
    else:
        # 兜底最小 hero_base_info
        skin_id = int(h.get("using_skin") or hid)
        b_lv = int(h.get("break_level") or 0)
        weapon_b = _pb_varint(1, int(h.get("weapon_exp") or 0)) + _pb_varint(2, int(h.get("weapon_break") or 0)) + _pb_varint(3, int(h.get("weapon_servant_uid") or 0))
        servant_b = _pb_varint(1, 0) + _pb_varint(2, 0)
        hbi_body = (_pb_varint(1, hid) + _pb_varint(2, int(h.get("level") or 1)) +
                    _pb_varint(3, int(h.get("star") or 100)) + _pb_varint(4, int(h.get("exp") or 0)) +
                    _pb_msg(6, weapon_b) + _pb_msg(7, servant_b) +
                    _pb_varint(10, skin_id) + _pb_varint(11, b_lv) +
                    _pb_varint(13, 0) + _pb_varint(15, skin_id))

    body = bytearray()
    body += _pb_msg(1, hbi_body)
    body += _pb_varint(2, hero_type_val)  # hero_type (1=自有角色)

    # 2. 玩家自持角色刻印装备（按 1~6 槽位提取）
    cur.execute("SELECT * FROM equip WHERE uid=? AND hero_id=?;", (uid, hid))
    equips = [dict(r) for r in cur.fetchall()]
    es_json = h.get("equip_slot")
    if es_json:
        if isinstance(es_json, str):
            try:
                es_json = json.loads(es_json)
            except Exception:
                es_json = {}
        if isinstance(es_json, dict) and es_json:
            eq_map = {eq["id"]: eq for eq in equips}
            sorted_equips = []
            for pos in range(1, 7):
                eid = es_json.get(str(pos))
                if eid and eid in eq_map:
                    sorted_equips.append(eq_map[eid])
                elif eid:
                    cur.execute("SELECT * FROM equip WHERE uid=? AND id=?;", (uid, eid))
                    eq_r = cur.fetchone()
                    if eq_r:
                        sorted_equips.append(dict(eq_r))
            if sorted_equips:
                equips = sorted_equips

    for eq in equips:
        eq_bytes = build_battle_equip_bytes(eq)
        body += _pb_msg(3, eq_bytes)

    # 3. 宿舍等级
    body += _pb_varint(4, 0)

    # 4. 好感度（必须包含 relation.tier_list 否则客户端在扫荡结算时访问 nil 报错卡死）
    t_lvl = int(h.get("trust_level") or 1)
    t_exp = int(h.get("trust_exp") or 0)
    t_mood = int(h.get("trust_mood") or 1)
    rel_b = _pb_msg(1, _pb_varint(1, 1))  # HERO_RELATION_NET_NET_REC { tier_list: [{tier: 1}] }
    trust_b = _pb_varint(1, t_lvl) + _pb_varint(2, t_exp) + _pb_varint(3, t_mood) + _pb_msg(4, rel_b)
    body += _pb_msg(5, trust_b)

    # 5. 主伤害类型
    body += _pb_varint(6, 0)
    return bytes(body)


def build_player_net_rec_bytes(db_conn, uid, hero_ids):
    """构建标准 player_net_rec 字节 (供 sc_54003 / sc_54039 使用)"""
    raw_conn = db_conn._conn() if hasattr(db_conn, "_conn") else db_conn
    cur = raw_conn.cursor()
    cur.execute("SELECT * FROM users WHERE uid=? LIMIT 1;", (uid,))
    user_row = cur.fetchone()
    if not user_row:
        cur.execute("SELECT * FROM users LIMIT 1;")
        user_row = cur.fetchone()
    user = dict(user_row) if user_row else {}

    # player_battle_net_rec (f2)
    pbi = bytearray()
    pbi += _pb_varint(2, 0)  # channel
    pbi += _pb_varint(3, 0)  # server

    for hid in (hero_ids or []):
        if not hid:
            continue
        bh_b = build_battle_hero_bytes(raw_conn, uid, hid)
        if bh_b:
            pbi += _pb_msg(4, bh_b)

    nick = user.get("nickname") or user.get("nick") or "管理员"
    pbi += _pb_str(5, nick)
    pbi += _pb_varint(6, int(user.get("level") or 1))
    pbi += _pb_varint(7, int(user.get("icon") or 0))
    pbi += _pb_varint(8, int(user.get("frame") or 0))

    # player_room_info_net_rec (f3)
    pri = _pb_varint(2, 0) + _pb_varint(3, 1)  # is_master=0, is_ready=1

    # player_net_rec (f1=player_id, f2=player_battle_info, f3=player_room_info)
    return _pb_varint(1, uid) + _pb_msg(2, bytes(pbi)) + _pb_msg(3, pri)


def build_sc_54003_dynamic(db_conn, uid, hero_ids):
    """动态生成 sc_54003 战斗初始化完整下行报文"""
    pi = build_player_net_rec_bytes(db_conn, uid, hero_ids)
    # sc_54003 { f1 = player_info }
    return _pb_msg(1, pi)


def build_sc_54039_dynamic(db_conn, uid, br, heroes, codec_encode=None):
    """动态生成 sc_54039 扫荡结算完整下行报文 (f1=result, f2=player_info, f3=battle_result)"""
    body = bytearray()
    body += _pb_varint(1, 0)  # f1: result = 0
    
    # f2: player_info (PLAYER_NET_REC)
    pi_bytes = build_player_net_rec_bytes(db_conn, uid, heroes)
    if pi_bytes:
        body += _pb_msg(2, pi_bytes)
        
    # f3: battle_result (BATTLE_RESULT_NET_REC)
    if codec_encode:
        br_bytes = codec_encode("battle_result_net_rec", br)
    else:
        import codec
        br_bytes = codec.encode("battle_result_net_rec", br)
    if br_bytes:
        body += _pb_msg(3, br_bytes)
        
    return bytes(body)

