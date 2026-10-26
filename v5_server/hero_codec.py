# -*- coding: utf-8 -*-
"""
hero_codec.py — 14009 英雄响应的"展开结构 → 字节"编码器（库独立列驱动）

原理：51 个 hero_<id>.json 是 sc_14009 完整帧的 protobuf 展开结构
（[field, "v"|"m"|"b"|"s", value] 列表）。本模块：
1. 加载展开结构（默认用真实数据，保证全字段正确）
2. 用 account.db 的 hero 独立列覆盖关键字段（改库即生效）
3. 编码回字节 → sc_14009 payload

字段路径（hero_net_rec 外层）：
  f1 = hero_base_info(msg)     ← 核心养成
  f2 = unlock (varint)
  f3 ×6 = equip{pos, equip_id} ← 装备槽（联动 equip 表）
  f4 = clear_times (熟练度)
  f5 ×N = clear_mission_list
  f7 = module_assignment
  f8 = trust(msg)

hero_base_info（f1 内）：
  f1=id f2=level f3=star f4=exp f5×6=skill{skill_id,level}
  f6=weapon{exp,breakthrough,servant_uid} f7=servant
  f8=using_astrolabe f9=unlock_astrolabe f10=using_skin
  f11=break_level f12=exclusive_skill_list f13=weapon_module_level
  f14=skill_intensify f15=battle_using_skin
"""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
# 优先查找同级/上级 analysis_scripts/协议分析/角色挖掘
_HERO_DIR_CANDIDATES = [
    os.path.join(os.path.dirname(_DIR), "analysis_scripts", "协议分析", "角色挖掘"),
    os.path.join(_DIR, "..", "analysis_scripts", "协议分析", "角色挖掘"),
    os.path.join(os.path.dirname(os.path.dirname(_DIR)), "analysis_scripts", "协议分析", "角色挖掘"),
    os.path.join(_DIR, "角色挖掘"),
]
_HERO_DIR = next((p for p in _HERO_DIR_CANDIDATES if os.path.exists(p)), _HERO_DIR_CANDIDATES[0])

_cache = {}
_EXTRA = None
_EXTRA_FILE = os.path.join(_DIR, "hero_14009_extra.json")
_HERO_SKILLS_FILE = os.path.join(_DIR, "hero_skills_cfg.json")
_HERO_SKILLS_MAP = None


def _get_hero_skills_cfg():
    global _HERO_SKILLS_MAP
    if _HERO_SKILLS_MAP is None:
        if os.path.exists(_HERO_SKILLS_FILE):
            try:
                with open(_HERO_SKILLS_FILE, "r", encoding="utf-8") as f:
                    _HERO_SKILLS_MAP = json.load(f)
            except Exception:
                _HERO_SKILLS_MAP = {}
        else:
            _HERO_SKILLS_MAP = {}
    return _HERO_SKILLS_MAP


def normalize_exclusive_skills(ex_data):
    """
    统一将任意形式的跃迁数据 (list of lists, list of dicts, dict, json str)
    规整为官方标准规范字典：
    {
        "1": {
            "talent_points": 6,
            "skill_list": [{"skill_id": 101, "skill_level": 3}, ...]
        },
        ...
    }
    """
    if not ex_data:
        return {}
    if isinstance(ex_data, str):
        import json as _j
        try:
            ex_data = _j.loads(ex_data)
        except Exception:
            return {}
    if not isinstance(ex_data, (dict, list)):
        return {}

    out = {}
    if isinstance(ex_data, dict):
        for slot_k, sdata in ex_data.items():
            try:
                sid = int(slot_k)
            except (ValueError, TypeError):
                continue
            if isinstance(sdata, dict):
                t_pts = int(sdata.get("talent_points") or 0)
                raw_s_list = sdata.get("skill_list") or []
            elif isinstance(sdata, list):
                t_pts = 0
                raw_s_list = sdata
            else:
                continue

            clean_skills = []
            for sk in raw_s_list:
                if isinstance(sk, dict):
                    sk_id = int(sk.get("skill_id") or sk.get("id") or 0)
                    sk_lv = int(sk.get("skill_level") or sk.get("level") or 1)
                elif isinstance(sk, (list, tuple)) and len(sk) >= 2:
                    sk_id, sk_lv = int(sk[0]), int(sk[1])
                else:
                    continue
                if sk_id > 0:
                    clean_skills.append({"skill_id": sk_id, "skill_level": sk_lv})
            if t_pts == 0 and clean_skills:
                t_pts = sum(s["skill_level"] for s in clean_skills)
            out[str(sid)] = {
                "talent_points": t_pts,
                "skill_list": clean_skills
            }
    elif isinstance(ex_data, list):
        for item in ex_data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    sid = int(item[0])
                except (ValueError, TypeError):
                    continue
                raw_s_list = item[1] if isinstance(item[1], list) else []
                t_pts = int(item[2]) if len(item) >= 3 else 0
                clean_skills = []
                for sk in raw_s_list:
                    if isinstance(sk, (list, tuple)) and len(sk) >= 2:
                        sk_id, sk_lv = int(sk[0]), int(sk[1])
                    elif isinstance(sk, dict):
                        sk_id = int(sk.get("skill_id") or sk.get("id") or 0)
                        sk_lv = int(sk.get("skill_level") or sk.get("level") or 1)
                    else:
                        continue
                    if sk_id > 0:
                        clean_skills.append({"skill_id": sk_id, "skill_level": sk_lv})
                if t_pts == 0 and clean_skills:
                    t_pts = sum(s["skill_level"] for s in clean_skills)
                out[str(sid)] = {
                    "talent_points": t_pts,
                    "skill_list": clean_skills
                }
            elif isinstance(item, dict):
                try:
                    sid = int(item.get("slot_id") or 0)
                except (ValueError, TypeError):
                    continue
                if sid <= 0:
                    continue
                t_pts = int(item.get("talent_points") or 0)
                raw_s_list = item.get("skill_list") or []
                clean_skills = []
                for sk in raw_s_list:
                    if isinstance(sk, dict):
                        sk_id = int(sk.get("skill_id") or sk.get("id") or 0)
                        sk_lv = int(sk.get("skill_level") or sk.get("level") or 1)
                    elif isinstance(sk, (list, tuple)) and len(sk) >= 2:
                        sk_id, sk_lv = int(sk[0]), int(sk[1])
                    else:
                        continue
                    if sk_id > 0:
                        clean_skills.append({"skill_id": sk_id, "skill_level": sk_lv})
                if t_pts == 0 and clean_skills:
                    t_pts = sum(s["skill_level"] for s in clean_skills)
                out[str(sid)] = {
                    "talent_points": t_pts,
                    "skill_list": clean_skills
                }

    return out


def _varint(v):
    # 负数必须先掩码成 64 位无符号：Python 里负数右移恒为 -1，原实现会死循环 +
    # bytearray 无界增长（库里任何一列被手工写成负数就能挂死整个连接线程）。
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


def encode_expanded(exp):
    """protobuf 展开 [f,type,val] 列表 → bytes"""
    out = bytearray()
    for item in exp:
        f, t = item[0], item[1]
        v = item[2]
        wire = 2 if t in ("m", "b", "s") else 0
        out += _varint((f << 3) | wire)
        if t == "v":
            out += _varint(v)
        elif t == "m":
            sub = encode_expanded(v)
            out += _varint(len(sub)) + sub
        elif t == "b":
            raw = bytes.fromhex(v) if isinstance(v, str) and v else b""
            out += _varint(len(raw)) + raw
        elif t == "s":
            raw = v.encode("utf-8")
            out += _varint(len(raw)) + raw
    return bytes(out)


def load_hero_expanded(hero_id, copy=True):
    """读取 hero_<id>.json 的展开结构（带缓存）。

    copy=True（默认）返回**深拷贝**：apply_db 是就地改写的，直接把缓存对象交出去会
    永久污染模板 —— 表现为「某玩家某列为 NULL 时沿用上一个玩家的值」。
    只做存在性判断时用 has_hero_template()，避免无谓的深拷贝开销。
    """
    key = int(hero_id)
    exp = _cache.get(key)
    if exp is None:
        path = os.path.join(_HERO_DIR, f"hero_{key}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            exp = json.load(fh)
        _cache[key] = exp
    if not copy:
        return exp
    import copy as _copy
    return _copy.deepcopy(exp)


def has_hero_template(hero_id):
    """该英雄是否有展开模板（只判存在，不拷贝）。"""
    key = int(hero_id)
    if key in _cache:
        return True
    return os.path.exists(os.path.join(_HERO_DIR, f"hero_{key}.json"))


def _configured_hero_skills(hero_id):
    """返回角色配置中的技能 ID；无有效技能配置时返回空列表。"""
    try:
        hero_id = int(hero_id)
    except (TypeError, ValueError):
        return []
    cfg = _get_hero_skills_cfg()
    skills = cfg.get(str(hero_id)) or cfg.get(hero_id) or []
    if not isinstance(skills, list):
        return []
    out = []
    for skill_id in skills:
        try:
            skill_id = int(skill_id)
        except (TypeError, ValueError):
            continue
        if skill_id > 0:
            out.append(skill_id)
    return out


def is_hero_payload_candidate(hero_id):
    """登录角色列表候选：有展开模板，或有明确的角色技能配置。"""
    try:
        hero_id = int(hero_id)
    except (TypeError, ValueError):
        return False
    return hero_id > 0 and (has_hero_template(hero_id) or bool(_configured_hero_skills(hero_id)))


def count_hero_payload_records(db, uid):
    """返回与 build_hero_payload 完全相同筛选规则下可编码的自持角色数。"""
    rows = db.query("SELECT id FROM hero WHERE uid=? AND unlock=1 ORDER BY id", (uid,))
    return sum(1 for row in rows if is_hero_payload_candidate(row["id"]))


_HERO_JSON_COLUMNS = {
    "skill_list": (list, []),
    "using_astrolabe": (list, []),
    "unlock_astrolabe": (list, []),
    "exclusive_skill_list": ((dict, list), {}),
    "skill_intensify": (list, []),
    "equip_slot": (dict, {}),
    "chip_state": ((dict, list), {}),
    "relation_net": (list, []),
    "clear_mission_list": (list, []),
}


def initial_hero_row(hero_id, star, now_ts=0):
    """Complete player state for a newly obtained hero, independent of captures."""
    hid = int(hero_id)
    return dict(id=hid, level=1, star=int(star), exp=0,
        skill_list=json.dumps([[sid, 1] for sid in _configured_hero_skills(hid)]),
        weapon_exp=0, weapon_break=0, weapon_servant_uid=0, servant='[]',
        using_astrolabe='[]', unlock_astrolabe='[]', using_skin=hid, break_level=0,
        exclusive_skill_list='[]', weapon_module_level=0, skill_intensify='[]',
        battle_using_skin=hid, clear_times=0, unlock=1, equip_slot='[]',
        module_level=0, module_assignment=0, chip_state='[]', update_ts=int(now_ts),
        is_favorite=0, trust_level=0, trust_exp=0, trust_mood=1,
        relation_net='[]', battle_win_times=0)


def _initial_defaults_for_save(row, *, enabled):
    """Project missing new-save fields without rewriting legacy save contents."""
    if enabled:
        defaults = initial_hero_row(row['id'], row.get('star') or 0)
        # This wire-only field has no hero table column, so it cannot be
        # allowed to survive from a captured hero's completed missions.
        defaults['clear_mission_list'] = []
        row = dict(row)
        for key, value in defaults.items():
            if row.get(key) is None:
                row[key] = value
    return row


def normalize_hero_db_row(row):
    """Parse JSON TEXT hero columns while preserving missing and SQL NULL values."""
    normalized = dict(row or {})
    for column, (expected, empty_value) in _HERO_JSON_COLUMNS.items():
        if column not in normalized or normalized[column] is None:
            continue
        value = normalized[column]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = empty_value
        if not isinstance(value, expected):
            value = empty_value
        normalized[column] = value
    return normalized


def _int_value(row, key, default=0):
    value = row.get(key)
    return int(default if value is None else (value or 0))


def build_hero_net_rec_from_db(row, template=None):
    """Build a hero_net_rec from one DB row, optionally applying the same mapping to a template.

    This is shared by sc_14009/sc_14007 and battle hero_base_info so both use one DB column map.
    """
    row = normalize_hero_db_row(row)
    if template is None:
        hid = _int_value(row, "id")
        module_level = row.get("weapon_module_level")
        if module_level is None:
            module_level = row.get("module_level")
        module_assignment = row.get("weapon_module_assignment")
        if module_assignment is None:
            module_assignment = row.get("module_assignment")
        hbi = [
            [1, "v", hid],
            [2, "v", _int_value(row, "level", 1)],
            [3, "v", _int_value(row, "star")],
            [4, "v", _int_value(row, "exp")],
            [6, "m", [[1, "v", _int_value(row, "weapon_exp")],
                       [2, "v", _int_value(row, "weapon_break")],
                       [3, "v", _int_value(row, "weapon_servant_uid")]]],
            [7, "m", [[1, "v", 0], [2, "v", 0]]],
            [10, "v", _int_value(row, "using_skin")],
            [11, "v", _int_value(row, "break_level")],
            [13, "v", int(module_level or 0)],
            [15, "v", _int_value(row, "battle_using_skin")],
        ]
        trust = [
            [1, "v", _int_value(row, "trust_level", 1)],
            [2, "v", _int_value(row, "trust_exp")],
            [3, "v", _int_value(row, "trust_mood", 1)],
            [4, "b", ""],
        ]
        template = [
            [1, "m", hbi],
            [2, "v", _int_value(row, "unlock", 1)],
        ] + [
            [3, "m", [[1, "v", pos], [2, "v", 0]]]
            for pos in range(1, 7)
        ] + [
            [4, "v", _int_value(row, "clear_times")],
            [7, "v", int(module_assignment or 0)],
            [8, "m", trust],
        ]
        if "clear_mission_list" in row and row["clear_mission_list"] is not None:
            template.extend([5, "v", int(mid)] for mid in row["clear_mission_list"] if mid)
    if not apply_db(template, row):
        raise ValueError("hero_net_rec is missing hero_base_info")
    return template


def _find(exp, field):
    return [it for it in exp if it[0] == field]


def _set_varint(exp, field, value):
    """把 exp 中 field 的 'v' 条目值改为 value；无则追加。返回 True 表示改动。"""
    for it in exp:
        if it[0] == field and it[1] == "v":
            it[2] = value
            return True
    exp.append([field, "v", value])
    return True


def _set_hbi(hbi, field, value):
    _set_varint(hbi, field, value)


_SKIN_HERO_MAP = None

def _get_skin_hero_map():
    global _SKIN_HERO_MAP
    if _SKIN_HERO_MAP is None:
        _SKIN_HERO_MAP = {}
        p = os.path.join(_DIR, "skin_hero_map.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _SKIN_HERO_MAP = {int(k): int(v) for k, v in json.load(f).items()}
            except Exception:
                pass
    return _SKIN_HERO_MAP


def apply_db(exp, row):
    """用 hero 表独立列 row(dict) 覆盖展开结构字段。"""
    row = normalize_hero_db_row(row)
    hbi = None
    for it in exp:
        if it[0] == 1 and it[1] == "m":
            hbi = it[2]
            break
    if hbi is None:
        return False

    # --- hero_base_info 覆盖 ---
    if row.get("level") is not None:
        _set_hbi(hbi, 2, int(row["level"]))
    if row.get("star") is not None:
        _set_hbi(hbi, 3, int(row["star"]))
    if row.get("exp") is not None:
        _set_hbi(hbi, 4, int(row["exp"]))
    if row.get("break_level") is not None:
        _set_hbi(hbi, 11, int(row["break_level"] or 0))
    if row.get("using_skin") is not None:
        _set_hbi(hbi, 10, int(row["using_skin"] or 0))
    if row.get("battle_using_skin") is not None:
        _set_hbi(hbi, 15, int(row["battle_using_skin"] or 0))
    if row.get("weapon_module_level") is not None:
        _set_hbi(hbi, 13, int(row["weapon_module_level"] or 0))
    # 技能等级覆盖：hbi f5 每项 {skill_id, level}（必须恒为规范 6 技能列表，杜绝 Lua 遍历越界/nil 崩溃）
    hid = row.get("id")
    if not hid:
        for it in hbi:
            if it[0] == 1 and it[1] == "v":
                hid = it[2]
                break

    skills = row.get("skill_list")
    # SQL JSON TEXT 空列表也是权威值：以基础技能等级重建，不沿用模板中的旧等级。
    # 字段缺失或 SQL NULL 才保留模板中的技能数据。
    skill_map = {}
    if skills is None:
        for s_item in _find(hbi, 5):
            if s_item[1] == "m":
                sub = s_item[2]
                sid = next((x[2] for x in sub if x[0] == 1 and x[1] == "v"), None)
                slv = next((x[2] for x in sub if x[0] == 2 and x[1] == "v"), 1)
                if sid:
                    skill_map[int(sid)] = int(slv)
    elif isinstance(skills, list):
        for sk in skills:
            if isinstance(sk, (list, tuple)) and len(sk) >= 2:
                sid, slv = int(sk[0]), int(sk[1])
            elif isinstance(sk, dict):
                sid = int(sk.get("skill_id") or sk.get("id") or 0)
                slv = int(sk.get("skill_level") or sk.get("level") or 1)
            else:
                continue
            if sid > 0:
                skill_map[sid] = slv

    cfg_map = _get_hero_skills_cfg()
    canonical_skills = _configured_hero_skills(hid)
    if not canonical_skills and hid:
        configured = cfg_map.get(str(hid)) or cfg_map.get(int(hid))
        if isinstance(configured, list):
            canonical_skills = [int(s) for s in configured if int(s) > 0]

    new_s_items = []
    if canonical_skills:
        for sid in canonical_skills:
            lv = skill_map.get(int(sid), 1)
            new_s_items.append([5, "m", [[1, "v", int(sid)], [2, "v", int(lv)]]])
    elif skills is not None:
        # 没有角色配置时只发 DB 明确给出的技能；显式 [] 会清除模板技能。
        for sid, lv in skill_map.items():
            new_s_items.append([5, "m", [[1, "v", sid], [2, "v", lv]]])
    else:
        seen_skills = set()
        for s_item in _find(hbi, 5):
            if s_item[1] == "m":
                sub = s_item[2]
                sid_entry = next((x for x in sub if x[0] == 1 and x[1] == "v"), None)
                if sid_entry:
                    sid = sid_entry[2]
                    if sid not in seen_skills:
                        seen_skills.add(sid)
                        if sid in skill_map:
                            _set_varint(sub, 2, skill_map[sid])
                        new_s_items.append(s_item)

    hbi[:] = [it for it in hbi if it[0] != 5] + new_s_items
    # 技能属性强化覆盖：hbi f14 每项 {index, level}
    intens = row.get("skill_intensify")
    if intens is not None:
        hbi[:] = [it for it in hbi if it[0] != 14]
        if isinstance(intens, list):
            for item in intens:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    hbi.append([14, "m", [[1, "v", int(item[0])], [2, "v", int(item[1])]]])
                elif isinstance(item, dict):
                    idx = int(item.get("index") or 0)
                    lv = int(item.get("level") or 0)
                    if idx and lv:
                        hbi.append([14, "m", [[1, "v", idx], [2, "v", lv]]])
    # 武器覆盖：hbi f6 {exp, breakthrough, servant_uid}
    w = _find(hbi, 6)
    if not w:
        w = [[6, "m", [[1, "v", 0], [2, "v", 0], [3, "v", 0]]]]
        hbi.append(w[0])
    if w[0][1] == "m":
        wsub = w[0][2]
        if row.get("weapon_exp") is not None:
            _set_varint(wsub, 1, int(row["weapon_exp"] or 0))
        if row.get("weapon_break") is not None:
            _set_varint(wsub, 2, int(row["weapon_break"] or 0))
        if row.get("weapon_servant_uid") is not None:
            _set_varint(wsub, 3, int(row["weapon_servant_uid"] or 0))
    # 钥从覆盖：hbi f7 {id, stage}（专属钥从进入战斗触发关键特技）
    s_info = row.get("servant_info") or row.get("servant")
    servant_source = ("servant_info" in row or "servant" in row or
                      ("weapon_servant_uid" in row and row.get("weapon_servant_uid") is not None))
    if s_info or servant_source:
        if isinstance(s_info, str):
            try:
                s_info = json.loads(s_info)
            except (TypeError, ValueError):
                s_info = None
        s_id, s_stage = None, 1
        if isinstance(s_info, dict):
            s_id = s_info.get("id") or s_info.get("prefab_id")
            s_stage = s_info.get("stage") or 1
        elif isinstance(s_info, (list, tuple)) and len(s_info) >= 1:
            s_id = s_info[0]
            if len(s_info) >= 2:
                s_stage = s_info[1]
        hbi[:] = [it for it in hbi if it[0] != 7]
        if s_id:
            servant_body = [[1, "v", int(s_id)], [2, "v", int(s_stage or 1)]]
        else:
            servant_body = [[1, "v", 0], [2, "v", 0]]
        hbi.append([7, "m", servant_body])
    if not _find(hbi, 7):
        hbi.append([7, "m", [[1, "v", 0], [2, "v", 0]]])
    if not _find(hbi, 6):
        hbi.append([6, "m", [[1, "v", 0], [2, "v", 0], [3, "v", 0]]])
    # 神格覆盖：hbi f8 using_astrolabe（repeated varint）
    ua = row.get("using_astrolabe")
    if isinstance(ua, list):
        hbi[:] = [it for it in hbi if it[0] != 8]
        for a in ua:
            if a:
                hbi.append([8, "v", int(a)])
    # 已解锁神格覆盖：hbi f9 unlock_astrolabe（repeated varint）
    ua_unlock = row.get("unlock_astrolabe")
    if isinstance(ua_unlock, list):
        hbi[:] = [it for it in hbi if it[0] != 9]
        for a in ua_unlock:
            if a:
                hbi.append([9, "v", int(a)])
    # 跃迁系统覆盖：hbi f12 exclusive_skill_list
    exclusive = row.get("exclusive_skill_list")
    if exclusive is not None:
        norm_ex = normalize_exclusive_skills(exclusive)
        f12_entries = []
        for slot_id_str, sdata in norm_ex.items():
            slot_id = int(slot_id_str)
            t_pts = int(sdata.get("talent_points") or 0)
            s_list = sdata.get("skill_list") or []
            sub = [[1, "v", slot_id]]
            for sk in s_list:
                sid = int(sk.get("skill_id") or 0)
                slv = int(sk.get("skill_level") or 1)
                if sid > 0:
                    sub.append([2, "m", [[1, "v", sid], [2, "v", slv]]])
            sub.append([3, "v", t_pts])
            f12_entries.append([12, "m", sub])
        hbi[:] = [it for it in hbi if it[0] != 12] + f12_entries
    # 同调武器模块等级覆盖：hbi f13 weapon_module_level
    if row.get("weapon_module_level") is not None or row.get("module_level") is not None:
        m_lv = row.get("weapon_module_level")
        if m_lv is None:
            m_lv = row.get("module_level")
        m_lv = int(m_lv or 0)
        _set_varint(hbi, 13, m_lv)
    # 突破等级 f11 已覆盖

    # --- 外层覆盖 ---
    if row.get("unlock") is not None:
        _set_varint(exp, 2, int(row["unlock"]))
    if row.get("clear_times") is not None:
        _set_varint(exp, 4, int(row["clear_times"] or 0))
    module_assignment = row.get("weapon_module_assignment")
    if module_assignment is None:
        module_assignment = row.get("module_assignment")
    if module_assignment is not None:
        _set_varint(exp, 7, int(module_assignment or 0))
    # 皮肤解锁列表覆盖：f6 unlocked_skin（repeated lasted_skin）
    unlocked_skins = row.get("unlocked_skin_list")
    if unlocked_skins is not None:
        exp[:] = [it for it in exp if it[0] != 6]
        for skin in unlocked_skins:
            if isinstance(skin, dict):
                sid = skin.get("skin_id") or skin.get("id")
                # lasted_skin.time is an expiry, not the acquisition timestamp.
                expiry_ts = skin.get("time") or 0
            else:
                sid, expiry_ts = skin, 0
            if sid:
                exp.append([6, "m", [[1, "v", int(sid)], [2, "v", int(expiry_ts)]]])
    # 装备槽覆盖：f3 ×6 {pos, equip_id}（必须恒为 1..6 孔稠密数组，空槽填 0，防客户端 Lua 崩溃）
    es = row.get("equip_slot")
    if es is not None:
        if not isinstance(es, dict):
            es = {}
        new_f3 = []
        for pos in (1, 2, 3, 4, 5, 6):
            eid = es.get(str(pos)) or es.get(pos) or 0
            new_f3.append([3, "m", [[1, "v", pos], [2, "v", int(eid)]]])
        exp[:] = [it for it in exp if it[0] != 3] + new_f3
    # clear_mission_list is only overridden when the DB row actually provides it.
    if row.get("clear_mission_list") is not None:
        exp[:] = [it for it in exp if it[0] != 5]
        exp.extend([5, "v", int(mid)] for mid in row["clear_mission_list"] if mid)
    # 关系网与好感度覆盖：f8 trust (hero_trust_net_rec)
    t_lvl = row.get("trust_level")
    t_exp = row.get("trust_exp")
    t_mood = row.get("trust_mood")
    rel_net = row.get("relation_net")
    if any(row.get(k) is not None for k in ("trust_level", "trust_exp", "trust_mood", "relation_net")):
        trust_sub = [it for it in exp if it[0] == 8]
        if not trust_sub or trust_sub[0][1] != "m":
            ts_list = [[1, "v", 1], [2, "v", 0], [3, "v", 1], [4, "b", ""]]
            exp[:] = [it for it in exp if it[0] != 8]
            exp.append([8, "m", ts_list])
        else:
            ts_list = trust_sub[0][2]
        if t_lvl is not None:
            _set_varint(ts_list, 1, int(t_lvl or 0))
        if t_exp is not None:
            _set_varint(ts_list, 2, int(t_exp or 0))
        if t_mood is not None:
            _set_varint(ts_list, 3, int(t_mood or 1))
        if rel_net is not None:
            if not isinstance(rel_net, list):
                rel_net = []
            ts_list[:] = [it for it in ts_list if it[0] != 4]
            if rel_net:
                tier_entries = []
                for t in rel_net:
                    if not isinstance(t, dict):
                        continue
                    tier_num = int(t.get("tier", 1))
                    # 编码层防御：客户端以 Lua 1-based 下标索引 relation_upgrade_group，
                    # tier/group_index 越界会让角色列表页 GetRelationNetAttr nil index 崩溃，
                    # 脏值（0/负数/越界）在此丢弃，绝不外推。
                    if not (1 <= tier_num <= 5):
                        continue
                    upgrades = [int(u) for u in t.get("upgrade_complete_list", []) if 1 <= int(u) <= 3]
                    tier_sub = [[1, "v", tier_num]]
                    for u in upgrades:
                        tier_sub.append([2, "v", u])
                    tier_entries.append([1, "m", tier_sub])
                ts_list.append([4, "m", tier_entries])
            else:
                ts_list.append([4, "b", ""])
    return True


def _load_hero_payload_assets(db, uid):
    """Load DB-authoritative unlocked skins and servants for one account."""
    skin_map = _get_skin_hero_map()
    skin_rows = db.query(
        "SELECT skin_id FROM player_skin_unlocked WHERE uid=? ORDER BY skin_id",
        (uid,),
    )
    unlocked_by_hero = {}
    for skin in skin_rows:
        skin_id = int(skin["skin_id"])
        hero_id = skin_map.get(skin_id)
        if hero_id:
            unlocked_by_hero.setdefault(int(hero_id), []).append({
                "skin_id": skin_id,
                # This ownership table records permanent unlocks; unlock_ts is
                # when they were acquired. The client requires time=0 for these.
                "time": 0,
            })

    servant_rows = db.query(
        "SELECT id, prefab_id, stage FROM servant WHERE uid=? ORDER BY id",
        (uid,),
    )
    servant_map = {
        int(r["id"]): {"id": int(r["prefab_id"]), "stage": int(r["stage"] or 0)}
        for r in servant_rows
    }
    return unlocked_by_hero, servant_map


def _with_hero_payload_assets(row, unlocked_by_hero, servant_map):
    """Return a normalized row decorated with current account-owned assets."""
    result = normalize_hero_db_row(row)
    hero_id = int(result["id"])
    result["unlocked_skin_list"] = list(unlocked_by_hero.get(hero_id, []))
    servant_uid = result.get("weapon_servant_uid")
    if servant_uid is not None and int(servant_uid or 0) > 0:
        # Set even when no matching servant row exists so a stale template key is cleared.
        result["servant_info"] = servant_map.get(int(servant_uid))
    return result


def build_hero_payload(db, uid):
    """主入口：hero 表 unlock=1 的行 → sc_14009 payload bytes。

    结构（schema sc_14009）：
      f1 hero_info_list（repeated hero_net_rec，展开模板+库覆盖）
      f3 archives（repeated archive，账号资产静态模板 hero_14009_extra.json）
      f4 piece_list（repeated piece_info）
      f5 favorites（repeated uint32）
    已解锁角色按模板（若存在）与数据库行统一映射；缺模板的已配置角色由数据库行构建。
    """
    rows = db.query(
        "SELECT * FROM hero WHERE uid=? AND unlock=1 ORDER BY id", (uid,))
    unlocked_by_hero, servant_map = _load_hero_payload_assets(db, uid)
    from new_player_service import enabled
    fresh_save = enabled(db, uid)

    parts = []
    for r in rows:
        hero_id = int(r["id"])
        if not is_hero_payload_candidate(hero_id):
            continue
        exp = load_hero_expanded(hero_id)
        row = _with_hero_payload_assets(_initial_defaults_for_save(r, enabled=fresh_save), unlocked_by_hero, servant_map)
        exp = build_hero_net_rec_from_db(row, template=exp)
        blob = encode_expanded(exp)
        parts.append(b"\x0a" + _varint(len(blob)) + blob)
    return b"".join(parts) + _encode_extra_fields(db, uid)


def build_single_hero_frame(db, uid, hero_id):
    """Build one current hero_net_rec for sc_14007 using the shared DB mapping."""
    rows = db.query(
        "SELECT * FROM hero WHERE uid=? AND id=? AND unlock=1 LIMIT 1",
        (uid, hero_id),
    )
    if not rows or not is_hero_payload_candidate(hero_id):
        return None
    unlocked_by_hero, servant_map = _load_hero_payload_assets(db, uid)
    from new_player_service import enabled
    row = _with_hero_payload_assets(_initial_defaults_for_save(rows[0], enabled=enabled(db, uid)), unlocked_by_hero, servant_map)
    exp = build_hero_net_rec_from_db(row, template=load_hero_expanded(hero_id))
    hero_bytes = encode_expanded(exp)
    return b"\x0a" + _varint(len(hero_bytes)) + hero_bytes


def _load_extra():
    """加载 14009 账号资产静态模板（archives/piece_list/favorites/hero_astrolabe，素材提取）。"""
    global _EXTRA
    if _EXTRA is None:
        try:
            _EXTRA = json.load(open(_EXTRA_FILE, encoding="utf-8"))
        except Exception:
            _EXTRA = {}
    return _EXTRA


def _encode_extra_fields(db=None, uid=None):
    """sc_14009 顶层附加字段：f3 archives + f4 piece_list + f5 favorites（codec 编码嵌套）。
    f4 piece_list 优先从 account.db 的 hero_piece 表动态查询，确保碎片变动与登录流实时一致。
    """
    from codec import encode as _enc
    dynamic = db is not None and uid is not None
    # Runtime DB is authoritative; static replay assets are used only by offline callers.
    extra = {} if dynamic else _load_extra()
    if not extra and not dynamic:
        return b""
    out = bytearray()
    # f3 archives（repeated archive，tag 0x1a）
    # 动态从数据库 hero_archive 读取规范档案数据（63 档案 / 84 战斗形态）。
    # 历史库只有 hero 行时，按已解锁英雄的本体映射补齐空档案。
    archive_svc = None
    if dynamic:
        try:
            from archive_service import ArchiveService
            archive_svc = ArchiveService.get_instance()
        except Exception:
            pass
    arch_rows = db.query("SELECT * FROM hero_archive WHERE uid=? ORDER BY archive_id", (uid,)) if dynamic else []
    if dynamic:
        archives = []
        for r in arch_rows:
            if archive_svc is not None and int(r.get("archive_id") or 0) not in archive_svc.archives:
                continue
            def _safe_json(raw, default, expected_type):
                try:
                    value = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    return default
                return value if isinstance(value, expected_type) else default

            a = {
                "archive_id": int(r["archive_id"]),
                "exp": min(1000, max(0, int(r.get("exp") or 0))),
                "text_list": _safe_json(r.get("text_list") or "[]", [], list),
                "video_list": _safe_json(r.get("video_list") or "[]", [], list),
                "gift_list": _safe_json(r.get("gift_list") or "[]", [], list),
                "selected_picture": _safe_json(r.get("selected_picture") or "{}", {}, dict),
                "super_heart_link_list": _safe_json(r.get("super_heart_link_list") or "[]", [], list),
                "hero_story_list": _safe_json(r.get("hero_story_list") or "[]", [], list),
            }
            archives.append(a)
    else:
        archives = extra.get("archives") or []

    for a in archives:
        try:
            sub = _enc("archive", a)
        except Exception:
            sub = b""
        if sub:
            out += _varint((3 << 3) | 2) + _varint(len(sub)) + sub

    # f4 piece_list（repeated piece_info，tag 0x22）
    # 动态从数据库 hero_piece 读取碎片数据
    piece_rows = db.query("SELECT hero_id, num FROM hero_piece WHERE uid=? ORDER BY hero_id", (uid,)) if dynamic else []
    if dynamic:
        pieces = [{"id": r["hero_id"], "num": r["num"]} for r in piece_rows]
    else:
        pieces = extra.get("piece_list") or []

    for p in pieces:
        try:
            sub = _enc("piece_info", p)
        except Exception:
            sub = b""
        if sub:
            out += _varint((4 << 3) | 2) + _varint(len(sub)) + sub

    # f5 favorites（repeated uint32，tag 0x28）
    fav_rows = db.query("SELECT id FROM hero WHERE uid=? AND is_favorite=1 ORDER BY id", (uid,)) if dynamic else []
    if dynamic:
        favs = [r["id"] for r in fav_rows]
    else:
        favs = extra.get("favorites") or []

    for fv in favs:
        out += _varint((5 << 3) | 0) + _varint(int(fv))
    return bytes(out)


def build_hero_14007_frame(db, uid, hero_id):
    """[Fix by Gemini 3.7-flash] 构建单英雄全量实时推送帧 sc_14007（包含皮肤解锁等全量数据）。"""
    if not (db and uid and hero_id):
        return None
    payload = build_single_hero_frame(db, uid, int(hero_id))
    if payload:
        from middleware import DownFrame
        return DownFrame(14007, payload)
    return None
