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

    skill_map = {}
    for s_item in _find(hbi, 5):
        if s_item[1] == "m":
            sub = s_item[2]
            sid = next((x[2] for x in sub if x[0] == 1 and x[1] == "v"), None)
            slv = next((x[2] for x in sub if x[0] == 2 and x[1] == "v"), 1)
            if sid:
                skill_map[int(sid)] = int(slv)

    skills = row.get("skill_list")
    if isinstance(skills, list) and skills:
        for sk in skills:
            if isinstance(sk, (list, tuple)) and len(sk) >= 2:
                skill_map[int(sk[0])] = int(sk[1])
            elif isinstance(sk, dict):
                skill_map[int(sk.get("skill_id") or sk.get("id"))] = int(sk.get("skill_level") or sk.get("level") or 1)

    cfg_map = _get_hero_skills_cfg()
    canonical_skills = cfg_map.get(str(hid)) or (cfg_map.get(int(hid)) if hid else None)
    if not canonical_skills and hid:
        canonical_skills = [
            int(hid) * 1000 + 101,
            int(hid) * 1000 + 201,
            int(hid) * 1000 + 202,
            int(hid) * 1000 + 203,
            int(hid) * 1000 + 209,
            int(hid) * 1000 + 305,
        ]

    new_s_items = []
    if canonical_skills:
        for sid in canonical_skills:
            lv = skill_map.get(int(sid), 1)
            new_s_items.append([5, "m", [[1, "v", int(sid)], [2, "v", int(lv)]]])
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

    if new_s_items:
        hbi[:] = [it for it in hbi if it[0] != 5] + new_s_items
    # 技能属性强化覆盖：hbi f14 每项 {index, level}
    intens = row.get("skill_intensify")
    if intens is not None:
        if isinstance(intens, str):
            try:
                import json as _j
                intens = _j.loads(intens)
            except Exception:
                intens = []
        if isinstance(intens, list) and intens:
            hbi[:] = [it for it in hbi if it[0] != 14]
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
    if w and w[0][1] == "m":
        wsub = w[0][2]
        if row.get("weapon_exp") is not None:
            _set_varint(wsub, 1, int(row["weapon_exp"] or 0))
        if row.get("weapon_break") is not None:
            _set_varint(wsub, 2, int(row["weapon_break"] or 0))
        if row.get("weapon_servant_uid") is not None:
            _set_varint(wsub, 3, int(row["weapon_servant_uid"] or 0))
    # 钥从覆盖：hbi f7 {id, stage}（专属钥从进入战斗触发关键特技）
    s_info = row.get("servant_info") or row.get("servant")
    if s_info:
        if isinstance(s_info, str):
            try:
                import json as _j
                s_info = _j.loads(s_info)
            except Exception:
                s_info = None
        s_id, s_stage = None, 1
        if isinstance(s_info, dict):
            s_id = s_info.get("id") or s_info.get("prefab_id")
            s_stage = s_info.get("stage") or 1
        elif isinstance(s_info, (list, tuple)) and len(s_info) >= 1:
            s_id = s_info[0]
            if len(s_info) >= 2:
                s_stage = s_info[1]
        if s_id:
            hbi[:] = [it for it in hbi if it[0] != 7]
            hbi.append([7, "m", [[1, "v", int(s_id)], [2, "v", int(s_stage or 1)]]])
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
    norm_ex = normalize_exclusive_skills(row.get("exclusive_skill_list"))
    if norm_ex:
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
        if f12_entries:
            hbi[:] = [it for it in hbi if it[0] != 12] + f12_entries
    # 同调武器模块等级覆盖：hbi f13 weapon_module_level
    if row.get("weapon_module_level") is not None or row.get("module_level") is not None:
        m_lv = int(row.get("weapon_module_level") or row.get("module_level") or 0)
        _set_varint(hbi, 13, m_lv)
    # 突破等级 f11 已覆盖

    # --- 外层覆盖 ---
    if row.get("unlock") is not None:
        _set_varint(exp, 2, int(row["unlock"]))
    if row.get("clear_times") is not None:
        _set_varint(exp, 4, int(row["clear_times"] or 0))
    if row.get("module_assignment") is not None:
        _set_varint(exp, 7, int(row["module_assignment"] or 0))
    # 皮肤解锁列表覆盖：f6 unlocked_skin（repeated lasted_skin）
    unlocked_skins = row.get("unlocked_skin_list")
    if unlocked_skins is not None:
        exp[:] = [it for it in exp if it[0] != 6]
        for sid in unlocked_skins:
            exp.append([6, "m", [[1, "v", int(sid)], [2, "v", 0]]])
    # 装备槽覆盖：f3 ×6 {pos, equip_id}（必须恒为 1..6 孔稠密数组，空槽填 0，防客户端 Lua 崩溃）
    es = row.get("equip_slot")
    if not isinstance(es, dict):
        es = {}
    new_f3 = []
    for pos in (1, 2, 3, 4, 5, 6):
        eid = es.get(str(pos)) or es.get(pos) or 0
        new_f3.append([3, "m", [[1, "v", pos], [2, "v", int(eid)]]])
    exp[:] = [it for it in exp if it[0] != 3] + new_f3
    # 关系网与好感度覆盖：f8 trust (hero_trust_net_rec)
    t_lvl = row.get("trust_level")
    t_exp = row.get("trust_exp")
    t_mood = row.get("trust_mood")
    rel_net = row.get("relation_net")
    if t_lvl is not None or rel_net is not None:
        trust_sub = [it for it in exp if it[0] == 8]
        if trust_sub and trust_sub[0][1] == "m":
            ts_list = trust_sub[0][2]
            if t_lvl is not None:
                _set_varint(ts_list, 1, int(t_lvl or 0))
            if t_exp is not None:
                _set_varint(ts_list, 2, int(t_exp or 0))
            if t_mood is not None:
                _set_varint(ts_list, 3, int(t_mood or 1))
            if rel_net is not None:
                if isinstance(rel_net, str):
                    import json as _j
                    try:
                        rel_net = _j.loads(rel_net)
                    except Exception:
                        rel_net = []
                ts_list[:] = [it for it in ts_list if it[0] != 4]
                if isinstance(rel_net, list) and rel_net:
                    tier_entries = []
                    for t in rel_net:
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


def build_hero_payload(db, uid):
    """主入口：hero 表 unlock=1 的行 → sc_14009 payload bytes。

    结构（schema sc_14009）：
      f1 hero_info_list（repeated hero_net_rec，展开模板+库覆盖）
      f3 archives（repeated archive，账号资产静态模板 hero_14009_extra.json）
      f4 piece_list（repeated piece_info）
      f5 favorites（repeated uint32）
    每个 hero 若模板含 astrolabe_list（f5 顶层，repeated varint）且展开模板缺失 → 附加补齐。
    """
    import json as _j
    rows = db.query(
        "SELECT * FROM hero WHERE uid=? ORDER BY id", (uid,))
    
    # 获取全量已解锁皮肤
    skin_map = _get_skin_hero_map()
    skin_rows = db.query("SELECT skin_id FROM player_skin_unlocked WHERE uid=?", (uid,)) if db and uid else []
    owned_skins = set([r["skin_id"] for r in skin_rows])

    # 预载玩家所有钥从
    servant_rows = db.query("SELECT id, prefab_id, stage FROM servant WHERE uid=?", (uid,)) if db and uid else []
    servant_map = {r["id"]: {"id": r["prefab_id"], "stage": r["stage"]} for r in servant_rows}

    parts = []
    for r in rows:
        if not r.get("unlock"):
            continue
        exp = load_hero_expanded(r["id"])
        if exp is None:
            continue
        row = dict(r)
        # JSON 列解析为 dict/list
        for k in ("skill_list", "using_astrolabe", "equip_slot", "chip_state",
                  "unlock_astrolabe", "exclusive_skill_list", "skill_intensify"):
            if isinstance(row.get(k), str) and row[k]:
                try:
                    row[k] = _j.loads(row[k])
                except Exception:
                    row[k] = {} if k in ("equip_slot", "chip_state", "exclusive_skill_list") else []
        
        # 注入该英雄已解锁皮肤
        h_unlocked = [sid for sid in owned_skins if skin_map.get(sid) == int(r["id"])]
        existing_f6 = [it[2] for it in exp if it[0] == 6 and it[1] == "m"]
        existing_sids = set()
        for sub in existing_f6:
            for item in sub:
                if item[0] == 1 and item[1] == "v":
                    existing_sids.add(item[2])
        row["unlocked_skin_list"] = list(existing_sids.union(h_unlocked))

        # 注入钥从数据
        w_uid = row.get("weapon_servant_uid")
        if w_uid and w_uid in servant_map:
            row["servant_info"] = servant_map[w_uid]
        
        apply_db(exp, row)
        blob = encode_expanded(exp)
        parts.append(b"\x0a" + _varint(len(blob)) + blob)
    if not parts:
        return None
    return b"".join(parts) + _encode_extra_fields(db, uid)


def build_single_hero_frame(db, uid, hero_id):
    """[Fix by Gemini 3.7-flash] 构建并编码单个英雄的 sc_14007 帧 payload (用于换装购买等即时同步)"""
    exp = load_hero_expanded(hero_id)
    if exp is None:
        return None
    rows = db.query("SELECT * FROM hero WHERE uid=? AND id=?", (uid, hero_id))
    row = dict(rows[0]) if rows else {"id": hero_id, "unlock": 1}
    for k in ("skill_list", "using_astrolabe", "equip_slot", "chip_state",
              "unlock_astrolabe", "exclusive_skill_list", "skill_intensify"):
        if k in row and isinstance(row[k], str) and row[k]:
            try:
                import json as _j
                row[k] = _j.loads(row[k])
            except Exception:
                row[k] = {} if k in ("equip_slot", "chip_state", "exclusive_skill_list") else []
    skin_map = _get_skin_hero_map()
    skin_rows = db.query("SELECT skin_id FROM player_skin_unlocked WHERE uid=?", (uid,)) if db and uid else []
    h_unlocked = [r["skin_id"] for r in skin_rows if skin_map.get(r["skin_id"]) == hero_id]
    existing_f6 = [it[2] for it in exp if it[0] == 6 and it[1] == "m"]
    existing_sids = set()
    for sub in existing_f6:
        for item in sub:
            if item[0] == 1 and item[1] == "v":
                existing_sids.add(item[2])
    row["unlocked_skin_list"] = list(existing_sids.union(h_unlocked))

    w_uid = row.get("weapon_servant_uid")
    if w_uid and db:
        s_rows = db.query("SELECT prefab_id, stage FROM servant WHERE uid=? AND id=?", (uid, w_uid))
        if s_rows:
            row["servant_info"] = {"id": s_rows[0]["prefab_id"], "stage": s_rows[0]["stage"]}

    apply_db(exp, row)
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
    extra = _load_extra()
    if not extra and not (db and uid):
        return b""
    out = bytearray()
    # f3 archives（repeated archive，tag 0x1a）
    # 动态从数据库 hero_archive 读取规范档案数据（63 档案 / 84 战斗形态）。
    # 历史库只有 hero 行时，按已解锁英雄的本体映射补齐空档案。
    archive_svc = None
    if db and uid:
        try:
            from archive_service import ArchiveService
            archive_svc = ArchiveService.get_instance()
            archive_svc.ensure_user_archives(db, uid)
        except Exception:
            pass
    arch_rows = db.query("SELECT * FROM hero_archive WHERE uid=? ORDER BY archive_id", (uid,)) if (db and uid) else []
    if arch_rows:
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
    piece_rows = db.query("SELECT hero_id, num FROM hero_piece WHERE uid=? ORDER BY hero_id", (uid,)) if (db and uid) else []
    if piece_rows:
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
    fav_rows = db.query("SELECT id FROM hero WHERE uid=? AND is_favorite=1 ORDER BY id", (uid,)) if (db and uid) else []
    if fav_rows:
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
