# -*- coding: utf-8 -*-
"""
illustrated_listener.py — 图鉴与收集系统事件总线订阅器 (Illustrated Listener)
监听全局业务事件（关卡通关、剧情阅读、修正者解锁、怪物击杀、钥从与刻印获取），
基于全真机 stage_story_media_graph.json 与 illustrated_meta.json 拓扑驱动
illustrated / hero_race_collect / loading_set 表并生成标准差分下行帧。
同时提供 IllustratedSyncService 高性能内存级全量数据自愈与校准服务。
"""

import os
import re
import json
import logging
from codec import encode
from middleware import DownFrame
from event_bus import bus, Events

logger = logging.getLogger('illustrated_listener')

# ================= 静态配置文件懒加载 =================

_DIR = os.path.dirname(os.path.abspath(__file__))

_STAGE_MEDIA_GRAPH_CACHE = None
_ILLU_META_CACHE = None
_HERO_RACE_CFG_CACHE = None
_COLLECT_PICTURE_CFG_CACHE = None


def _get_stage_media_graph():
    global _STAGE_MEDIA_GRAPH_CACHE
    if _STAGE_MEDIA_GRAPH_CACHE is None:
        p = os.path.join(_DIR, "stage_story_media_graph.json")
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    _STAGE_MEDIA_GRAPH_CACHE = json.load(f)
            else:
                _STAGE_MEDIA_GRAPH_CACHE = {}
        except Exception as e:
            logger.error(f"加载 stage_story_media_graph.json 失败: {e}")
            _STAGE_MEDIA_GRAPH_CACHE = {}
    return _STAGE_MEDIA_GRAPH_CACHE


def _get_illu_meta():
    global _ILLU_META_CACHE
    if _ILLU_META_CACHE is None:
        p = os.path.join(_DIR, "illustrated_meta.json")
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    _ILLU_META_CACHE = json.load(f)
            else:
                _ILLU_META_CACHE = {}
        except Exception as e:
            logger.error(f"加载 illustrated_meta.json 失败: {e}")
            _ILLU_META_CACHE = {}
    return _ILLU_META_CACHE


def _get_hero_race_cfg():
    global _HERO_RACE_CFG_CACHE
    if _HERO_RACE_CFG_CACHE is None:
        p = os.path.join(_DIR, "collect_hero_race_cfg.json")
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    _HERO_RACE_CFG_CACHE = json.load(f)
            else:
                _HERO_RACE_CFG_CACHE = {"tasks": {}, "by_race": {}}
        except Exception as e:
            logger.error(f"加载 collect_hero_race_cfg.json 失败: {e}")
            _HERO_RACE_CFG_CACHE = {"tasks": {}, "by_race": {}}
    return _HERO_RACE_CFG_CACHE


def _get_collect_picture_cfg():
    global _COLLECT_PICTURE_CFG_CACHE
    if _COLLECT_PICTURE_CFG_CACHE is None:
        cfg_path = os.path.abspath(os.path.join(_DIR, "..", "decompiled_v2", "x64", "game", "config", "collectpicturecfg.lua"))
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                chunks = re.split(r'\n\t\[(\d+)\]\s*=\s*\{', text)
                pics = {}
                for i in range(1, len(chunks), 2):
                    pid = int(chunks[i])
                    body = chunks[i + 1]
                    tm = re.search(r'type\s*=\s*(\d+)', body)
                    pm = re.search(r'(?:additional_parameter|parameters)\s*=\s*\{([^}]*)\}', body)
                    uc_m = re.search(r'unlock_condition\s*=\s*(\d+)', body)
                    wed_m = re.search(r'wedding_role_id\s*=\s*(\d+)', body)
                    ch_m = re.search(r'chapter_id\s*=\s*(\d+)', body)
                    nm = re.search(r'name\s*=\s*\"([^\"]*)\"', body)

                    typ = int(tm.group(1)) if tm else 0
                    params = [int(x.strip()) for x in pm.group(1).split(',') if x.strip().lstrip('-').isdigit()] if pm else []
                    uc = int(uc_m.group(1)) if uc_m else 0
                    wed = int(wed_m.group(1)) if wed_m else 0
                    chap = int(ch_m.group(1)) if ch_m else 0
                    name = nm.group(1) if nm else ''

                    pics[pid] = {
                        "id": pid,
                        "type": typ,
                        "name": name,
                        "chapter_id": chap,
                        "unlock_condition": uc,
                        "params": params,
                        "wedding_role_id": wed,
                    }
                _COLLECT_PICTURE_CFG_CACHE = pics
                return _COLLECT_PICTURE_CFG_CACHE
            except Exception as e:
                logger.error(f"加载 collectpicturecfg.lua 失败: {e}")
                _COLLECT_PICTURE_CFG_CACHE = {}

        # 降级回退：读取本地 illustrated_meta.json
        meta_path = os.path.join(_DIR, "illustrated_meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                pic_info = meta.get("pic_info", {})
                _COLLECT_PICTURE_CFG_CACHE = {int(k): v for k, v in pic_info.items()}
                return _COLLECT_PICTURE_CFG_CACHE
            except Exception as e:
                logger.error(f"读 illustrated_meta.json 失败: {e}")
                _COLLECT_PICTURE_CFG_CACHE = {}
        else:
            _COLLECT_PICTURE_CFG_CACHE = {}
    return _COLLECT_PICTURE_CFG_CACHE


# ================= 核心图鉴驱动逻辑 =================

def _push_frame(ctx, frame):
    """安全将 DownFrame 塞入 ctx 发送队列"""
    if not frame:
        return
    if hasattr(ctx, "append_frame"):
        ctx.append_frame(frame)
    else:
        if not hasattr(ctx, "pending_frames") or ctx.pending_frames is None:
            ctx.pending_frames = []
        ctx.pending_frames.append(frame)


def trigger_story_unlock(db, uid, story_id):
    """
    观看/通关剧情时的原子处理：
    1. 判定 plot 是否首次解锁（若已存在但 complete_flag=0，则激活为 1 并下发）；
    2. 判定该剧情绑定的插画中，哪些是真正首次解锁（激活 complete_flag=1，严格查重，杜绝重复下发 is_receive=0）；
    3. 返回 (new_plots, new_pics)。
    """
    story_id = int(story_id)
    if not story_id:
        return [], []

    # 1. 剧情入库与去重
    plot_row = db.query("SELECT complete_flag FROM illustrated WHERE uid=? AND kind='plot' AND item_id=?", (uid, story_id))
    new_plots = []
    if not plot_row:
        try:
            db.execute(
                "INSERT OR REPLACE INTO illustrated (uid, kind, item_id, is_view, complete_flag) VALUES (?, 'plot', ?, 1, 1)",
                (uid, story_id)
            )
        except Exception:
            db.execute(
                "INSERT OR REPLACE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, 'plot', ?, 1)",
                (uid, story_id)
            )
        db.execute(
            "INSERT OR REPLACE INTO story_unlock (uid, story_id, unlock_flag) VALUES (?, ?, 1)",
            (uid, story_id)
        )
        new_plots.append({"id": story_id, "is_view": 1})
    else:
        cflag = plot_row[0].get("complete_flag")
        if cflag is not None and cflag == 0:
            db.execute("UPDATE illustrated SET complete_flag=1, is_view=1 WHERE uid=? AND kind='plot' AND item_id=?", (uid, story_id))
            new_plots.append({"id": story_id, "is_view": 1})
        db.execute(
            "INSERT OR REPLACE INTO story_unlock (uid, story_id, unlock_flag) VALUES (?, ?, 1)",
            (uid, story_id)
        )

    # 2. 插画关联与去重 (防重复刷奖核心)
    meta = _get_illu_meta()
    s2p = meta.get("story_to_pics", {})
    pids = s2p.get(str(story_id), [])

    new_pics = []
    for pid in pids:
        pid_int = int(pid)
        pic_row = db.query(
            "SELECT is_receive, is_view, complete_flag FROM illustrated WHERE uid=? AND kind='inbetweening' AND item_id=?",
            (uid, pid_int)
        )
        if not pic_row:
            # 真正首次解锁插画！初始未领取(is_receive=0)，未查看(is_view=0)，complete_flag=1
            try:
                db.execute(
                    "INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view, complete_flag) VALUES (?, 'inbetweening', ?, 0, 0, 1)",
                    (uid, pid_int)
                )
            except Exception:
                db.execute(
                    "INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view) VALUES (?, 'inbetweening', ?, 0, 0)",
                    (uid, pid_int)
                )
            new_pics.append({"id": pid_int, "is_receive": 0, "is_view": 0})
        else:
            cflag = pic_row[0].get("complete_flag")
            if cflag is not None and cflag == 0:
                # 此前处于未解锁锁定状态，现被阅读剧情点亮！
                db.execute(
                    "UPDATE illustrated SET complete_flag=1 WHERE uid=? AND kind='inbetweening' AND item_id=?",
                    (uid, pid_int)
                )
                new_pics.append({"id": pid_int, "is_receive": pic_row[0].get("is_receive") or 0, "is_view": pic_row[0].get("is_view") or 0})
            else:
                # 已经在数据库中了且已处于解锁状态（可能已领奖），绝不重复下发 is_receive=0！
                pass

    return new_plots, new_pics


def trigger_monster_kill(db, uid, monster_id, times=1):
    """视骸击杀记录与增量推送"""
    mid_int = int(monster_id)
    if not mid_int:
        return None

    row = db.query("SELECT times, is_view FROM illustrated WHERE uid=? AND kind='enemy' AND item_id=?", (uid, mid_int))
    if row:
        old_times = int(row[0]["times"] or 0)
        new_times = old_times + times
        db.execute(
            "UPDATE illustrated SET times=? WHERE uid=? AND kind='enemy' AND item_id=?",
            (new_times, uid, mid_int)
        )
        return {"id": mid_int, "times": new_times, "is_view": int(row[0]["is_view"] or 0)}
    else:
        db.execute(
            "INSERT INTO illustrated (uid, kind, item_id, times, is_view) VALUES (?, 'enemy', ?, ?, 0)",
            (uid, mid_int, times)
        )
        return {"id": mid_int, "times": times, "is_view": 0}


def trigger_servant_obtain(db, uid, servant_id):
    """钥从获得入图鉴库"""
    sid_int = int(servant_id)
    if not sid_int:
        return
    db.execute(
        "INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, 'servant', ?, 0)",
        (uid, sid_int)
    )


def trigger_equip_obtain(db, uid, suit_id, pos):
    """刻印获得并合并 1~6 号部位入图鉴库"""
    suit_int = int(suit_id)
    pos_int = int(pos)
    if not suit_int or not (1 <= pos_int <= 6):
        return

    row = db.query("SELECT pos_list_json FROM illustrated WHERE uid=? AND kind='equip' AND item_id=?", (uid, suit_int))
    if row:
        try:
            pos_list = json.loads(row[0]["pos_list_json"] or "[]")
        except Exception:
            pos_list = []
        if pos_int not in pos_list:
            pos_list.append(pos_int)
            pos_list.sort()
            db.execute(
                "UPDATE illustrated SET pos_list_json=? WHERE uid=? AND kind='equip' AND item_id=?",
                (json.dumps(pos_list), uid, suit_int)
            )
    else:
        db.execute(
            "INSERT INTO illustrated (uid, kind, item_id, pos_list_json, is_view) VALUES (?, 'equip', ?, ?, 0)",
            (uid, suit_int, json.dumps([pos_int]))
        )


def get_hero_race_counts(db, uid):
    """查询玩家当前各阵营（1奥山/2尼罗/3真樱/4圣树/5众星/9天垣）已拥有修正者数量"""
    rows = db.query("SELECT id FROM hero WHERE uid=? AND (unlock=1 OR clear_times > 0)", (uid,))
    if not rows:
        return {}

    counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 9: 0}
    cfg_p = os.path.join(_DIR, "hero_race_map.json")
    hero_race = {}
    if os.path.exists(cfg_p):
        try:
            with open(cfg_p, "r", encoding="utf-8") as f:
                hero_race = json.load(f)
        except Exception:
            hero_race = {}

    for r in rows:
        hid = str(r["id"])
        race = hero_race.get(hid)
        if race and race in counts:
            counts[race] += 1

    return counts


def get_hero_race_collect_frame(db, uid):
    """构建 sc_52025 下行负载"""
    rows = db.query("SELECT race_type, received_cnt_list FROM hero_race_collect WHERE uid=? ORDER BY race_type", (uid,))
    race_list = []
    for r in (rows or []):
        try:
            cnts = json.loads(r["received_cnt_list"]) if isinstance(r["received_cnt_list"], str) else r["received_cnt_list"]
        except Exception:
            cnts = []
        race_list.append({
            "race_type": int(r["race_type"]),
            "cnt_list": [int(x) for x in (cnts or [])]
        })
    return {"hero_race_collect": race_list}


# ================= 全局自愈与校准服务 (高性能集合版) =================

class IllustratedSyncService:
    """图鉴全量自愈校准引擎：根据系统真实记录（关卡/心链/誓约）进行双向自愈对齐。"""

    @classmethod
    def sync_all_from_db(cls, db, uid):
        summary = {
            "plots_added": 0, "pics_added": 0,
            "plots_locked": 0, "pics_locked": 0,
            "plots_unlocked": 0, "pics_unlocked": 0,
            "servants_added": 0, "equips_synced": 0
        }

        # 1. 批量读取已有图鉴
        existing_rows = db.query(
            "SELECT kind, item_id, is_receive, is_view, complete_flag, pos_list_json FROM illustrated WHERE uid=?",
            (uid,)
        )
        existing_plots = {}       # item_id -> complete_flag
        existing_pics = {}        # item_id -> (is_receive, is_view, complete_flag)
        existing_servants = set()
        existing_equips = {}      # suit_id -> set(pos)

        for r in (existing_rows or []):
            k = r.get("kind")
            iid = int(r.get("item_id") or 0)
            cflag = 1 if r.get("complete_flag") is None else int(r.get("complete_flag") or 0)
            if k == "plot":
                existing_plots[iid] = cflag
            elif k == "inbetweening":
                existing_pics[iid] = (int(r.get("is_receive") or 0), int(r.get("is_view") or 0), cflag)
            elif k == "servant":
                existing_servants.add(iid)
            elif k == "equip":
                try:
                    pos_l = json.loads(r.get("pos_list_json") or "[]")
                    existing_equips[iid] = set(pos_l)
                except Exception:
                    existing_equips[iid] = set()

        # 2. 真实系统记录聚合
        graph = _get_stage_media_graph()
        meta = _get_illu_meta()
        cfg_pics = _get_collect_picture_cfg()

        try:
            cleared_stages_rows = db.query(
                "SELECT id FROM chapter WHERE uid=? AND clear_times > 0 "
                "UNION SELECT id FROM stage_sub WHERE uid=? AND clear_times > 0",
                (uid, uid)
            )
        except Exception:
            cleared_stages_rows = db.query("SELECT id FROM chapter WHERE uid=? AND clear_times > 0", (uid,))

        cleared_stages = {int(cr["id"]) for cr in (cleared_stages_rows or [])}

        archive_stories = set()
        arch_rows = []
        try:
            arch_rows = db.query("SELECT video_list, super_heart_link_list FROM hero_archive WHERE uid=?", (uid,))
        except Exception:
            try:
                arch_rows = db.query("SELECT video_list FROM hero_archive WHERE uid=?", (uid,))
            except Exception:
                arch_rows = []
        for ar in (arch_rows or []):
            try:
                v_list = json.loads(ar.get("video_list") or "[]")
                for s in v_list:
                    archive_stories.add(int(s))
            except Exception:
                pass
            try:
                super_list = json.loads(ar.get("super_heart_link_list") or "[]")
                for s in super_list:
                    if isinstance(s, dict) and "plot_id" in s:
                        archive_stories.add(int(s["plot_id"]))
            except Exception:
                pass

        oath_heroes = set()
        try:
            oath_rows = db.query("SELECT hero_id FROM hero_oath WHERE uid=? AND oath=1", (uid,))
            for o_r in (oath_rows or []):
                oath_heroes.add(int(o_r["hero_id"]))
        except Exception:
            pass

        # 3. 关卡产出的故事与直属插画
        stage_stories = set()
        stage_direct_pics = set()
        for sid in cleared_stages:
            s_key = str(sid)
            if s_key in graph:
                for st_id in graph[s_key].get("stories", []):
                    stage_stories.add(int(st_id))
                for p_id in graph[s_key].get("pics", []):
                    stage_direct_pics.add(int(p_id))

        # 合法剧情回顾集合
        legit_plots = stage_stories | archive_stories

        # 4. 派生合法插画集合 (Type 1 ~ 6 官方规则)
        legit_pics = set()
        if cfg_pics:
            for pid, p in cfg_pics.items():
                t = p.get("type", 1)
                param0 = p["params"][0] if p.get("params") else None
                is_legit = False
                if t == 1:
                    if p.get("unlock_condition") == 402 and param0 in cleared_stages:
                        is_legit = True
                    elif p.get("unlock_condition") == 800 and param0 in legit_plots:
                        is_legit = True
                    elif pid in stage_direct_pics:
                        is_legit = True
                elif t == 2:
                    if param0 and param0 in archive_stories:
                        is_legit = True
                elif t == 3:
                    if p.get("unlock_condition") == 402 and param0 in cleared_stages:
                        is_legit = True
                    elif p.get("unlock_condition") == 800 and param0 in legit_plots:
                        is_legit = True
                    elif pid in stage_direct_pics:
                        is_legit = True
                elif t == 4:
                    wed = p.get("wedding_role_id", 0)
                    if wed and wed in oath_heroes:
                        is_legit = True
                    elif param0 and (param0 in archive_stories or param0 in legit_plots):
                        is_legit = True
                elif t == 5:
                    if param0 and param0 in legit_plots:
                        is_legit = True
                    elif not param0 or param0 == 0:
                        is_legit = True
                elif t == 6:
                    if param0 and param0 in legit_plots:
                        is_legit = True
                    elif p.get("unlock_condition") == 402 and param0 in cleared_stages:
                        is_legit = True
                    elif pid in stage_direct_pics:
                        is_legit = True

                if is_legit:
                    legit_pics.add(pid)
        else:
            s2p = meta.get("story_to_pics", {})
            stg2p = meta.get("stage_to_pics", {})
            for pid in legit_plots:
                for pic_id in s2p.get(str(pid), []):
                    legit_pics.add(int(pic_id))
            for sid in cleared_stages:
                for pic_id in stg2p.get(str(sid), []):
                    legit_pics.add(int(pic_id))
            legit_pics.update(stage_direct_pics)

        # 5. 剧情对齐
        missing_plots = legit_plots - set(existing_plots.keys())
        if missing_plots:
            try:
                plot_records = [(uid, "plot", pid, 0, 1) for pid in missing_plots]
                db.executemany("INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_view, complete_flag) VALUES (?, ?, ?, ?, ?)", plot_records)
            except Exception:
                plot_records = [(uid, "plot", pid, 0) for pid in missing_plots]
                db.executemany("INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, ?, ?, ?)", plot_records)
            story_records = [(uid, pid, 1) for pid in missing_plots]
            db.executemany("INSERT OR IGNORE INTO story_unlock (uid, story_id, unlock_flag) VALUES (?, ?, ?)", story_records)
            summary["plots_added"] = len(missing_plots)
            for pid in missing_plots:
                existing_plots[pid] = 1

        for pid, cflag in existing_plots.items():
            if pid not in legit_plots and cflag != 0:
                try:
                    db.execute("UPDATE illustrated SET complete_flag=0, is_view=0 WHERE uid=? AND kind='plot' AND item_id=?", (uid, pid))
                    summary["plots_locked"] += 1
                except Exception:
                    pass
            elif pid in legit_plots and cflag == 0:
                try:
                    db.execute("UPDATE illustrated SET complete_flag=1 WHERE uid=? AND kind='plot' AND item_id=?", (uid, pid))
                    summary["plots_unlocked"] += 1
                except Exception:
                    pass

        # 6. 插画对齐
        missing_pics = legit_pics - set(existing_pics.keys())
        if missing_pics:
            try:
                pic_records = [(uid, "inbetweening", pic_id, 0, 0, 1) for pic_id in missing_pics]
                db.executemany("INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_receive, is_view, complete_flag) VALUES (?, ?, ?, ?, ?, ?)", pic_records)
            except Exception:
                pic_records = [(uid, "inbetweening", pic_id, 0, 0) for pic_id in missing_pics]
                db.executemany("INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_receive, is_view) VALUES (?, ?, ?, ?, ?)", pic_records)
            summary["pics_added"] = len(missing_pics)
            for pid in missing_pics:
                existing_pics[pid] = (0, 0, 1)

        for pid, (rec, view, cflag) in existing_pics.items():
            if pid not in legit_pics:
                if cflag != 0 or rec != 0:
                    try:
                        db.execute("UPDATE illustrated SET complete_flag=0, is_receive=0, is_view=0 WHERE uid=? AND kind='inbetweening' AND item_id=?", (uid, pid))
                        summary["pics_locked"] += 1
                    except Exception:
                        pass
            else:
                if cflag == 0:
                    try:
                        db.execute("UPDATE illustrated SET complete_flag=1 WHERE uid=? AND kind='inbetweening' AND item_id=?", (uid, pid))
                        summary["pics_unlocked"] += 1
                    except Exception:
                        pass

        # 7. 钥从对齐
        servants = db.query("SELECT prefab_id FROM servant WHERE uid=?", (uid,))
        needed_servants = {int(sr["prefab_id"]) for sr in (servants or []) if sr.get("prefab_id")}
        new_servants = needed_servants - existing_servants
        if new_servants:
            try:
                servant_records = [(uid, "servant", sid, 0, 1) for sid in new_servants]
                db.executemany("INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_view, complete_flag) VALUES (?, ?, ?, ?, ?)", servant_records)
            except Exception:
                servant_records = [(uid, "servant", sid, 0) for sid in new_servants]
                db.executemany("INSERT OR IGNORE INTO illustrated (uid, kind, item_id, is_view) VALUES (?, ?, ?, ?)", servant_records)
            summary["servants_added"] = len(new_servants)

        # 8. 刻印对齐
        equips = db.query("SELECT prefab_id FROM equip WHERE uid=?", (uid,))
        eq_map = {}
        eq_cfg_p = os.path.join(_DIR, "equip_suit_pos_map.json")
        if os.path.exists(eq_cfg_p):
            try:
                with open(eq_cfg_p, "r", encoding="utf-8") as f:
                    eq_map = json.load(f)
            except Exception:
                eq_map = {}

        suit_pos_map = {}
        for eq in (equips or []):
            pfid = eq.get("prefab_id") or 0
            info = eq_map.get(str(pfid))
            if info:
                suit_id = info["suit"]
                pos = info["pos"]
            else:
                pos = (pfid // 10000) % 10
                suit_id = pfid // 100 if pfid > 1000 else pfid
            if suit_id and 1 <= pos <= 6:
                suit_pos_map.setdefault(suit_id, set()).add(pos)

        for suit_id, pos_set in suit_pos_map.items():
            if suit_id in existing_equips:
                cur_set = existing_equips[suit_id]
                merged = sorted(list(cur_set | pos_set))
                if len(merged) > len(cur_set):
                    try:
                        db.execute(
                            "UPDATE illustrated SET pos_list_json=?, complete_flag=1 WHERE uid=? AND kind='equip' AND item_id=?",
                            (json.dumps(merged), uid, suit_id)
                        )
                    except Exception:
                        db.execute(
                            "UPDATE illustrated SET pos_list_json=? WHERE uid=? AND kind='equip' AND item_id=?",
                            (json.dumps(merged), uid, suit_id)
                        )
                    summary["equips_synced"] += 1
            else:
                try:
                    db.execute(
                        "INSERT INTO illustrated (uid, kind, item_id, pos_list_json, is_view, complete_flag) VALUES (?, 'equip', ?, ?, 0, 1)",
                        (uid, suit_id, json.dumps(sorted(list(pos_set))))
                    )
                except Exception:
                    db.execute(
                        "INSERT INTO illustrated (uid, kind, item_id, pos_list_json, is_view) VALUES (?, 'equip', ?, ?, 0)",
                        (uid, suit_id, json.dumps(sorted(list(pos_set))))
                    )
                summary["equips_synced"] += 1

        return summary


# ================= 事件总线订阅注册 =================

@bus.subscribe(Events.STAGE_PASS)
def on_stage_pass(ctx, uid, stage_id=0, times=1, **kwargs):
    """关卡通关：触发剧情解锁与插画解锁"""
    if not stage_id:
        return

    graph = _get_stage_media_graph()
    s_info = graph.get(str(stage_id), {})
    story_ids = s_info.get("stories", [])
    direct_pics = s_info.get("pics", [])

    all_new_plots = []
    all_new_pics = []

    for st_id in story_ids:
        np, npi = trigger_story_unlock(ctx.db, uid, st_id)
        all_new_plots.extend(np)
        all_new_pics.extend(npi)

    # 关卡直属插画（如有）
    for pic_id in direct_pics:
        pid_int = int(pic_id)
        pic_row = ctx.db.query(
            "SELECT is_receive, is_view, complete_flag FROM illustrated WHERE uid=? AND kind='inbetweening' AND item_id=?",
            (uid, pid_int)
        )
        if not pic_row:
            try:
                ctx.db.execute(
                    "INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view, complete_flag) VALUES (?, 'inbetweening', ?, 0, 0, 1)",
                    (uid, pid_int)
                )
            except Exception:
                ctx.db.execute(
                    "INSERT INTO illustrated (uid, kind, item_id, is_receive, is_view) VALUES (?, 'inbetweening', ?, 0, 0)",
                    (uid, pid_int)
                )
            all_new_pics.append({"id": pid_int, "is_receive": 0, "is_view": 0})
        else:
            cflag = pic_row[0].get("complete_flag")
            if cflag is not None and cflag == 0:
                ctx.db.execute(
                    "UPDATE illustrated SET complete_flag=1 WHERE uid=? AND kind='inbetweening' AND item_id=?",
                    (uid, pid_int)
                )
                all_new_pics.append({"id": pid_int, "is_receive": pic_row[0].get("is_receive") or 0, "is_view": pic_row[0].get("is_view") or 0})

    # 增量推送剧情 (sc_52027)
    if all_new_plots:
        try:
            p27 = encode("sc_52027", {"plot_info": all_new_plots})
            if p27:
                _push_frame(ctx, DownFrame(52027, p27))
                if hasattr(ctx, "log"):
                    ctx.log(f"[IllustratedListener] sc_52027 关卡={stage_id} 解锁剧情: {[x['id'] for x in all_new_plots]}")
        except Exception as e:
            logger.error(f"构造 sc_52027 失败: {e}")

    # 增量推送插画 (sc_52011)
    if all_new_pics:
        try:
            p11 = encode("sc_52011", {"inbetweening_info": all_new_pics})
            if p11:
                _push_frame(ctx, DownFrame(52011, p11))
                if hasattr(ctx, "log"):
                    ctx.log(f"[IllustratedListener] sc_52011 关卡={stage_id} 解锁新插画: {[x['id'] for x in all_new_pics]}")
        except Exception as e:
            logger.error(f"构造 sc_52011 失败: {e}")


@bus.subscribe(Events.STAGE_FIRST_CLEAR)
def on_stage_first_clear(ctx, uid, stage_id=0, **kwargs):
    """首次通关专属广播：联动剧情与插画首通点亮"""
    if stage_id:
        on_stage_pass(ctx, uid, stage_id=stage_id, times=1)


@bus.subscribe(Events.MONSTER_KILL)
def on_monster_kill(ctx, uid, monster_id=0, count=1, **kwargs):
    """击杀怪物/视骸敌人：累加图鉴击杀计数并下发 sc_52009"""
    if not monster_id:
        return
    diff = trigger_monster_kill(ctx.db, uid, monster_id, times=count)
    if diff:
        try:
            p09 = encode("sc_52009", {"enemy_info": [diff]})
            if p09:
                _push_frame(ctx, DownFrame(52009, p09))
                if hasattr(ctx, "log"):
                    ctx.log(f"[IllustratedListener] sc_52009 视骸={monster_id} 击杀累加={diff['times']}")
        except Exception as e:
            logger.error(f"构造 sc_52009 失败: {e}")


@bus.subscribe(Events.STORY_READ)
def on_story_read(ctx, uid, story_id=0, archive_id=None, video_list=None, **kwargs):
    """主线/心链/活动剧情观看：解锁剧情并防重复解锁插画"""
    s_ids = []
    if video_list and isinstance(video_list, (list, tuple)):
        s_ids.extend([int(x) for x in video_list])
    elif story_id:
        s_ids.append(int(story_id))

    all_new_plots = []
    all_new_pics = []

    for sid in s_ids:
        np, npi = trigger_story_unlock(ctx.db, uid, sid)
        all_new_plots.extend(np)
        all_new_pics.extend(npi)

    # 心链档案视频列表更新
    if archive_id and s_ids and kwargs.get("story_kind") != "super_heart":
        aid = int(archive_id)
        ar = ctx.db.query("SELECT video_list FROM hero_archive WHERE uid=? AND archive_id=?", (uid, aid))
        cur_videos = json.loads(ar[0]["video_list"] or "[]") if ar and ar[0]["video_list"] else []
        for vid in s_ids:
            if vid not in cur_videos:
                cur_videos.append(vid)
        ctx.db.execute("UPDATE hero_archive SET video_list=? WHERE uid=? AND archive_id=?",
                       (json.dumps(cur_videos), uid, aid))

    # 增量推送剧情 (sc_52027)
    if all_new_plots:
        try:
            p27 = encode("sc_52027", {"plot_info": all_new_plots})
            if p27:
                _push_frame(ctx, DownFrame(52027, p27))
        except Exception as e:
            logger.error(f"构造 sc_52027 失败: {e}")

    # 增量推送插画 (sc_52011)
    if all_new_pics:
        try:
            p11 = encode("sc_52011", {"inbetweening_info": all_new_pics})
            if p11:
                _push_frame(ctx, DownFrame(52011, p11))
                if hasattr(ctx, "log"):
                    ctx.log(f"[IllustratedListener] sc_52011 剧情阅读解锁新插画: {[x['id'] for x in all_new_pics]}")
        except Exception as e:
            logger.error(f"构造 sc_52011 失败: {e}")


@bus.subscribe(Events.HERO_UNLOCK)
def on_hero_unlock(ctx, uid, hero_id=0, **kwargs):
    """解锁新修正者：更新阵营收集状态并推送 sc_52025"""
    try:
        data = get_hero_race_collect_frame(ctx.db, uid)
        p25 = encode("sc_52025", data)
        if p25:
            _push_frame(ctx, DownFrame(52025, p25))
            if hasattr(ctx, "log"):
                ctx.log(f"[IllustratedListener] sc_52025 修正者={hero_id} 解锁更新阵营收集")
    except Exception as e:
        logger.error(f"构造 sc_52025 失败: {e}")


@bus.subscribe(Events.SERVANT_OBTAIN)
def on_servant_obtain(ctx, uid, servant_id=0, **kwargs):
    """获得新钥从：点亮钥从图鉴"""
    if servant_id:
        trigger_servant_obtain(ctx.db, uid, servant_id)


@bus.subscribe(Events.EQUIP_OBTAIN)
def on_equip_obtain(ctx, uid, suit_id=0, pos=1, **kwargs):
    """获得新刻印：并入套装槽位"""
    if suit_id and pos:
        trigger_equip_obtain(ctx.db, uid, suit_id, pos)
