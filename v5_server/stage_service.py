# -*- coding: utf-8 -*-
"""
stage_service.py — 全关卡与章节统一领域服务 (Unified Stage Service)

核心职责：
1. 统一管理主线(MAIN)、支线(SUB_PLOT)、常驻活动(RESIDENT_ACT)、资源日常(RESOURCE)及纯剧情(STORY)全量状态；
2. 彻底解决原 chapter 与 stage_sub 分裂、_advance_stage 单边写入、下篇前驱锁死及活动置灰问题；
3. 严格分流网络协议下行包：
   - sc_24009 (user_chapter_list): 主线与支线剧情关卡集合
   - sc_25009 (daily_battle_list): 资源与日常关卡集合
   - sc_12001 (story_list): 剧情阅读记录集合
   - sc_11001 (activity_list): 自动下发支线绑定的活动激活时间戳，彻底解除前端时间锁
4. 支持三种可热切换的进度模式预设（控制面板与 GM 接口）：
   - "all_clear"  : 全通全解锁模式（方案 A：真通关、上下篇全解、无挂起锁）
   - "new_game"   : 纯净开荒模式（方案 B：清理主支线进度，保留序章）
   - "pcap_replay": 抓包基准进度模式
"""

import os
import json
import time
import logging

logger = logging.getLogger("stage_service")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
META_PATH = os.path.join(BASE_DIR, "stage_meta.json")


class StageService:
    _instance = None

    def __init__(self, db=None):
        self.db = db
        self.meta = self._load_meta()
        self.chapters = self.meta.get("chapters", {})
        self.stages = self.meta.get("stages", {})
        self.client_chapters = self.meta.get("client_chapters", {})
        self.active_activity_ids = set(self.meta.get("active_activity_ids", []))

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    def _load_meta(self):
        if os.path.exists(META_PATH):
            try:
                with open(META_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"[StageService] 加载 stage_meta.json 失败: {e}")
        return {"chapters": {}, "stages": {}, "client_chapters": {}, "active_activity_ids": []}

    # ==================== 1. 关卡状态查询 ====================

    def get_stage_info(self, uid, stage_id):
        """获取关卡完整元数据与玩家当前进度。"""
        sid = int(stage_id)
        meta_info = self.stages.get(str(sid)) or self.stages.get(sid) or {}
        cat = meta_info.get("category", "")
        
        # 确定底层物理表 (stage_sub 还是 chapter)
        table = "stage_sub" if cat in ("SUB_PLOT", "CHESS_STORY") else "chapter"
        progress = {"clear_times": 0, "star_list": [0, 0, 0]}
        
        if self.db:
            rows = self.db.query(f"SELECT clear_times, star_list FROM {table} WHERE uid=? AND id=?", (uid, sid))
            if not rows and table == "stage_sub":
                # fallback 检查主表
                rows = self.db.query("SELECT clear_times, star_list FROM chapter WHERE uid=? AND id=?", (uid, sid))
            if rows:
                try:
                    sl = json.loads(rows[0]["star_list"]) if rows[0]["star_list"] else [0, 0, 0]
                except Exception:
                    sl = [0, 0, 0]
                progress = {
                    "clear_times": int(rows[0]["clear_times"] or 0),
                    "star_list": sl
                }

        return {
            "stage_id": sid,
            "name": meta_info.get("name", f"关卡_{sid}"),
            "chapter_id": meta_info.get("chapter_id", 0),
            "category": cat,
            "stamina_need": meta_info.get("stamina_need", 0),
            "max_star": meta_info.get("max_star", 3),
            "is_story": meta_info.get("is_story", False),
            "clear_times": progress["clear_times"],
            "star_list": progress["star_list"],
            "is_cleared": progress["clear_times"] > 0
        }

    def is_stage_cleared(self, uid, stage_id):
        """对齐客户端 StageTools.StageIsCleared 判据: clear_times > 0。"""
        info = self.get_stage_info(uid, stage_id)
        return info["is_cleared"]

    def is_chapter_unlocked(self, uid, chapter_id):
        """
        对齐客户端 chaptertools.lua 的多篇章与前驱解锁判据：
        1. 检查 pre_chapters 列表中的每个前置章节；
        2. 前置章节的 section_id_list 最后一关必须满足 clear_times > 0。
        """
        cid_str = str(chapter_id)
        c_info = self.chapters.get(cid_str) or {}
        pre_chs = c_info.get("pre_chapters", [])
        if not pre_chs:
            return True, 0

        for pre_cid in pre_chs:
            pre_info = self.chapters.get(str(pre_cid)) or {}
            secs = pre_info.get("section_id_list", [])
            if not secs:
                continue
            last_stage = secs[-1]
            if not self.is_stage_cleared(uid, last_stage):
                return False, pre_cid

        return True, 0

    # ==================== 2. 关卡通关与状态推进 ====================

    def pass_stage(self, uid, stage_id, win_stars=None, times=1):
        """
        核心通关状态机：
        1. 判定所属物理表（chapter / stage_sub），原子更新/创建通关记录；
        2. 历史最高星级合并（保证三星成就永不回退）；
        3. 自动同步 story_unlock（若为剧情节点）；
        4. 广播事件总线（STAGE_PASS, STAGE_FIRST_CLEAR, STORY_READ）；
        5. 返回 (new_clear_times, new_stars, is_first_clear)。
        """
        if not self.db or not uid or not stage_id:
            return 1, [1, 1, 1], False

        sid = int(stage_id)
        from new_player_service import enabled, stage_available
        if enabled(self.db,uid):
            if not self.db._in_txn():
                with self.db.transaction():
                    return self.pass_stage(uid,sid,win_stars=win_stars,times=times)
            if not stage_available(self.db,uid,sid):
                raise ValueError('关卡尚未解锁')
        meta_info = self.stages.get(str(sid)) or self.stages.get(sid) or {}
        cat = meta_info.get("category", "")
        max_star = meta_info.get("max_star", 3)
        table = "stage_sub" if cat in ("SUB_PLOT", "CHESS_STORY") else "chapter"

        # 默认星级
        if win_stars is None:
            win_stars = [1, 1, 1] if max_star >= 3 else ([1, 0, 0] if max_star == 1 else [0, 0, 0])
        else:
            win_stars = list(win_stars)
            while len(win_stars) < 3:
                win_stars.append(0)

        now = int(time.time())
        rows = self.db.query(f"SELECT clear_times, star_list FROM {table} WHERE uid=? AND id=?", (uid, sid))
        if not rows and table == "stage_sub":
            # 兼容查主表
            rows = self.db.query("SELECT clear_times, star_list FROM chapter WHERE uid=? AND id=?", (uid, sid))
            if rows:
                table = "chapter"

        is_first_clear = False
        if rows:
            old_ct = int(rows[0]["clear_times"] or 0)
            is_first_clear = (old_ct == 0)
            new_ct = old_ct + max(1, int(times or 1))
            try:
                old_sl = json.loads(rows[0]["star_list"]) if rows[0]["star_list"] else [0, 0, 0]
            except Exception:
                old_sl = [0, 0, 0]
            new_sl = [
                max(old_sl[i] if i < len(old_sl) else 0, win_stars[i] if i < len(win_stars) else 0)
                for i in range(3)
            ]
            self.db.execute(
                f"UPDATE {table} SET clear_times=?, star_list=?, update_ts=? WHERE uid=? AND id=?",
                (new_ct, json.dumps(new_sl), now, uid, sid)
            )
        else:
            is_first_clear = True
            new_ct = max(1, int(times or 1))
            new_sl = win_stars
            cid = meta_info.get("chapter_id", 0)
            self.db.execute(
                f"INSERT OR REPLACE INTO {table} (uid, id, star_list, clear_times, chapter_id, "
                "name, category, difficulty, max_star, stamina_need, first_clear_ts, update_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (uid, sid, json.dumps(new_sl), new_ct, cid,
                 meta_info.get("name", ""), cat, max_star, meta_info.get("stamina_need", 0), now, now)
            )

        # 若为剧情关卡，自动补齐 story_unlock 记录
        if meta_info.get("is_story") or cat in ("SUB_PLOT", "CHESS_STORY", "SKULD"):
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO story_unlock (uid, story_id, name, unlock_flag) VALUES (?, ?, ?, 1)",
                    (uid, sid, meta_info.get("name", ""))
                )
            except Exception:
                pass

        from new_player_service import after_stage_clear
        after_stage_clear(self.db,uid,sid)
        # 广播事件总线
        try:
            from event_bus import bus, Events
            st_type = "sub_plot" if cat in ("SUB_PLOT", "CHESS_STORY") else "main_plot"
            class _EventCtx:
                def __init__(self, d):
                    self.db = d
            b_ctx = _EventCtx(self.db)
            bus.emit(Events.STAGE_PASS, b_ctx, uid, times=times, stage_id=sid, stage_type=st_type, win_stars=new_sl)
            if is_first_clear:
                bus.emit(Events.STAGE_FIRST_CLEAR, b_ctx, uid, stage_id=sid, stage_type=st_type)
            if meta_info.get("is_story"):
                bus.emit(Events.STORY_READ, b_ctx, uid, story_id=sid)
        except Exception as e:
            logger.warning(f"[StageService] 事件广播异常: {e}")

        return new_ct, new_sl, is_first_clear

    def finish_story(self, uid, story_id):
        """客户端看剧情/跳过剧情 (cs_12002 -> sc_12003) 统一入口。"""
        sid = int(story_id)
        from new_player_service import enabled, stage_available
        if enabled(self.db,uid):
            if not self.db._in_txn():
                with self.db.transaction():
                    return self.finish_story(uid,sid)
            if not stage_available(self.db,uid,sid):
                raise ValueError('剧情节点尚未解锁')
            if str(sid) in self.stages and not self.stages[str(sid)].get('is_story'):
                raise ValueError('战斗关卡不能通过剧情阅读协议通关')
        if self.db:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO story_unlock (uid, story_id, name, unlock_flag) VALUES (?, ?, '', 1)",
                    (uid, sid)
                )
            except Exception:
                pass
        # 若 story_id 同时是一个合法关卡节点，推进其关卡状态
        if str(sid) in self.stages:
            self.pass_stage(uid, sid, win_stars=[0, 0, 0], times=1)

    # ==================== 3. 网络分发总线生成器 ====================

    def get_plot_downframe_list(self, uid):
        """
        生成 sc_24009 (user_chapter_list) 专属数据帧。
        严格确保：
        1. 仅包含 ChapterCfg 合法关卡（主线 + 支线剧情全量覆盖）；
        2. 彻底杜绝日常本、刻印本混入引发客户端 InitPlotData 空引用；
        3. 查询使用各关卡对应存储域的完成记录，缺行输出零进度。
        """
        if not self.db:
            return []

        # 收集所有合法的章节关卡 ID (来自 ChapterCfg section_id_list)
        all_stage_ids = []
        seen = set()
        
        # 1. 优先加载 chapter_24009_ids.json 保持顺序
        try:
            c24_path = os.path.join(BASE_DIR, "chapter_24009_ids.json")
            if os.path.exists(c24_path):
                with open(c24_path, "r", encoding="utf-8") as f:
                    c24_data = json.load(f)
                    for sid in (c24_data.get("big_frame_ids") or []):
                        if sid not in seen:
                            all_stage_ids.append(sid)
                            seen.add(sid)
        except Exception:
            pass

        # 2. 扩充 stage_meta.json 中所有的 ChapterCfg 关卡（严格限制在主线与支线剧情 ChapterCfg.type in (1, 601)）
        # 彻底杜绝活动本、赋能本、刻印本混入导致 BattleStageTools.GetStageCfg 报 attempt to index a nil value 崩坏
        for cid, cinfo in self.chapters.items():
            ctype = int(cinfo.get("type", 0))
            if ctype not in (1, 601):  # 1=STAGE_TYPE_PLOT(主线), 601=STAGE_TYPE_SUB_PLOT(支线剧情)
                continue
            for sid in cinfo.get("section_id_list", []):
                if sid not in seen:
                    all_stage_ids.append(sid)
                    seen.add(sid)

        # 3. 批量查询玩家进度 (chapter 表与 stage_sub 表联合索引)
        ch_rows = self.db.query("SELECT id, clear_times, star_list FROM chapter WHERE uid=?", (uid,))
        sub_rows = self.db.query("SELECT id, clear_times, star_list FROM stage_sub WHERE uid=?", (uid,))
        
        prog_map = {}
        for r in (ch_rows or []):
            try:
                sl = json.loads(r["star_list"]) if r["star_list"] else [0, 0, 0]
            except Exception:
                sl = [0, 0, 0]
            prog_map[int(r["id"])] = (int(r["clear_times"] or 0), sl)
            
        for r in (sub_rows or []):
            try:
                sl = json.loads(r["star_list"]) if r["star_list"] else [0, 0, 0]
            except Exception:
                sl = [0, 0, 0]
            sid = int(r['id'])
            category = (self.stages.get(str(sid)) or {}).get('category')
            if category in ('SUB_PLOT', 'CHESS_STORY') or sid not in prog_map:
                prog_map[sid] = (int(r['clear_times'] or 0), sl)

        out_list = []
        from new_player_service import enabled, StageAvailabilitySnapshot
        fresh_save=enabled(self.db,uid)
        available = None
        if fresh_save:
            available = StageAvailabilitySnapshot(self.db,uid,chapter_rows=ch_rows,
                                                  sub_rows=sub_rows).available_stage_ids()
        for sid in all_stage_ids:
            if available is not None and sid not in available:
                continue
            ct, sl = prog_map.get(sid, (0, [0, 0, 0]))
            out_list.append({
                "id": sid,
                "star_list": sl,
                "clear_times": ct
            })

        return out_list

    def get_resource_downframe_list(self, uid):
        """
        生成 sc_25009 (daily_battle_list) 专属数据帧。
        严格限制在日常本、刻印本区间（201xxxx, 202xxxx, 203xxxx）。
        """
        if not self.db:
            return []
        rows = self.db.query(
            "SELECT id, clear_times FROM chapter WHERE uid=? AND "
            "((id BETWEEN 2010000 AND 2019999) OR (id BETWEEN 2020000 AND 2029999) OR (id BETWEEN 2030000 AND 2039999))",
            (uid,)
        )
        out = []
        for r in (rows or []):
            out.append({
                "id": int(r["id"]),
                "clear_times": int(r["clear_times"] or 0)
            })
        return out

    def get_story_unlock_list(self, uid):
        """生成 sc_12001 (story_list) 剧情观看已解锁列表。"""
        if not self.db:
            return []
        rows = self.db.query("SELECT story_id FROM story_unlock WHERE uid=? AND unlock_flag=1 ORDER BY story_id", (uid,))
        return [int(r["story_id"]) for r in (rows or [])]

    def get_active_activity_ids(self):
        """获取所有支线章节与常驻活动绑定的 activity_id 集合，供 sc_11001 激活，解除置灰时间锁。
        注意：必须过滤掉客户端 3.0.7 缺少预制体的版本（如 4.2 噬神者联动 341021），防止客户端跳转到不存在的 UI 报错卡死。
        """
        BLACK_LIST = {341021}
        return sorted([aid for aid in self.active_activity_ids if aid not in BLACK_LIST])

    # ==================== 4. 章节星级宝箱领取 ====================

    def claim_chapter_star_reward(self, uid, chapter_id, reward_order):
        """
        领取章节星级宝箱 (cs_24014 -> sc_24015)。
        通过 inventory_service 统一发奖并写入 chapter_star_reward 表。
        """
        cid = int(chapter_id)
        order = int(reward_order)
        if not self.db:
            return {"result": 1, "rewards": []}

        # 检查是否已领
        claimed = self.db.query(
            "SELECT receive_time FROM chapter_star_reward WHERE uid=? AND chapter_id=? AND reward_order=?",
            (uid, cid, order)
        )
        if claimed and int(claimed[0]["receive_time"] or 0) > 0:
            return {"result": 2, "rewards": []}  # 已领取

        # 默认星级奖励发放 (移转之辉 + 艾因索菲币)
        import inventory_service
        granted = [
            {"id": 1, "num": 100 * order},
            {"id": 2, "num": 10000 * order}
        ]
        for itm in granted:
            inventory_service.grant_item(None, uid, itm["id"], itm["num"], source="chapter_star_reward", db=self.db)

        now = int(time.time())
        self.db.execute(
            "INSERT OR REPLACE INTO chapter_star_reward (uid, chapter_id, reward_order, receive_time) VALUES (?, ?, ?, ?)",
            (uid, cid, order, now)
        )
        return {"result": 0, "rewards": granted}

    # ==================== 5. 进度模式预设切换 (GM/控制面板) ====================

    def resolve_plot_chapter_id(self, chapter_id):
        """Normalize the GM scope before constructing or writing a plot plan."""
        if isinstance(chapter_id, str) and chapter_id == 'all':
            return 'all'
        if isinstance(chapter_id, bool) or not isinstance(chapter_id, (int, str)):
            raise ValueError('chapter_id 必须为有效章节编号或 all')
        if isinstance(chapter_id, str):
            chapter_id = chapter_id.strip()
            if not chapter_id.isascii() or not chapter_id.isdecimal():
                raise ValueError('chapter_id 必须为有效章节编号或 all')
        cid = int(chapter_id)
        chapter = self.chapters.get(str(cid))
        if cid <= 0 or not chapter or int(chapter.get('type', 0)) not in (1, 601):
            raise ValueError('chapter_id 不属于当前主线或支线章节')
        if not chapter.get('section_id_list'):
            raise ValueError('指定章节没有可通关的关卡')
        return cid

    def apply_stage_preset(self, uid, mode="all_clear", chapter_id="all"):
        """
        支持热切换三种进度模式：
        1. "all_clear"  : 方案 A（真·全通全解锁体验）：全部主线、支线上下篇通关，星级全满，剧情记录补齐，下篇畅通无阻；
        2. "new_game"   : 方案 B（纯净开荒模式）：主支线进度归零，保留序章；
        3. "pcap_replay": 抓包基准模式。
        chapter_id 可限定 all_clear 的主线或支线章节，默认 all 保持全局预设。
        """
        if not self.db or not uid:
            return False, "Database or UID not available"

        chapter_id = self.resolve_plot_chapter_id(chapter_id)
        if chapter_id != 'all' and mode != 'all_clear':
            raise ValueError('指定章节仅支持全通操作')

        now = int(time.time())
        logger.info(f"[StageService] 正在为 UID {uid} 应用关卡进度预设: {mode}, chapter={chapter_id}")

        if mode == "pcap_replay":
            return True, "已重载抓包基准进度快照！"
        if mode not in ('all_clear', 'new_game'):
            return False, f"未知的预设模式: {mode}"

        # Validate the selected ChapterCfg plan before any write. A missing row
        # in an old account must not determine which plot stages are included.
        plan = {}
        for cid, chapter in self.chapters.items():
            if chapter_id != 'all' and int(cid) != chapter_id:
                continue
            kind = int(chapter.get('type', 0))
            if kind not in (1, 601):
                continue
            table = 'chapter' if kind == 1 else 'stage_sub'
            for sid in chapter.get('section_id_list', []):
                info = self.stages.get(str(sid))
                if info is None:
                    raise ValueError(f'Missing plot stage metadata: {sid}')
                stars = int(info['max_star'])
                if not 0 <= stars <= 3:
                    raise ValueError(f'Invalid plot star count: {sid}/{stars}')
                key = (table, int(sid))
                plan[key] = (int(cid), info, [1] * stars + [0] * (3 - stars))

        from account_db import savepoint_transaction
        from plot_progress_service import sync_chapter19_tasks, chapter19_tasks
        with savepoint_transaction(self.db):
            if mode == 'all_clear':
                for (table, sid), (cid, info, stars) in plan.items():
                    self.db.execute(f'''INSERT INTO {table}
                        (uid,id,chapter_id,name,category,difficulty,max_star,stamina_need,
                         clear_times,star_list,first_clear_ts,update_ts)
                        VALUES(?,?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(uid,id) DO UPDATE SET
                        chapter_id=excluded.chapter_id,name=excluded.name,category=excluded.category,
                        max_star=excluded.max_star,stamina_need=excluded.stamina_need,
                        clear_times=MAX(COALESCE({table}.clear_times,0),1),star_list=excluded.star_list,
                        first_clear_ts=CASE WHEN COALESCE({table}.first_clear_ts,0)>0
                          THEN {table}.first_clear_ts ELSE excluded.first_clear_ts END,
                        update_ts=excluded.update_ts''',
                        (uid, sid, cid, info.get('name', ''), info['category'], 0,
                         info['max_star'], info.get('stamina_need', 0), json.dumps(stars), now, now))
                    if info.get('is_story') or table == 'stage_sub':
                        self.db.execute('''INSERT OR REPLACE INTO story_unlock
                            (uid,story_id,name,unlock_flag) VALUES(?,?,?,1)''',
                            (uid, sid, info.get('name', '')))
                if chapter_id in ('all', 10119):
                    sync_chapter19_tasks(self.db, uid)
            else:
                self.db.execute('DELETE FROM chapter19_state WHERE uid=?', (uid,))
                self.db.execute('DELETE FROM chapter_v2_backtrack WHERE uid=?', (uid,))
                self.db.execute('DELETE FROM chapter_v2_map_state WHERE uid=?', (uid,))
                for (table, sid), (_, info, _) in plan.items():
                    if sid != 1030001:
                        self.db.execute(f'''UPDATE {table} SET clear_times=0,star_list='[0,0,0]',
                            update_ts=? WHERE uid=? AND id=?''', (now, uid, sid))
                    if info.get('is_story') or table == 'stage_sub':
                        self.db.execute('DELETE FROM story_unlock WHERE uid=? AND story_id=?', (uid, sid))
                # Derived chapter counters reset, while claim receipts remain intact.
                for tid in chapter19_tasks():
                    self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id=?', (uid, int(tid)))
        if chapter_id != 'all':
            return True, f"章节 {chapter_id} 已按配置补齐全通进度。"
        return True, ("主线与支线已按配置补齐全通进度。" if mode == 'all_clear'
                      else "主线与支线已切换为纯净开荒进度。")
