# -*- coding: utf-8 -*-
"""
oath_service.py — 誓约（Oath / Wedding）核心领域服务
负责深空之眼核心女主角（1284 薇儿丹蒂、1049 伊邪那美、1095 巡天英灵）的
专属誓约仪式结缔、3D礼堂交互协议闭环、24项专属任务流转、物语阅读、自定义爱称、
合影保存与登录状态动态生成，并与全局事件总线（EventBus）及红点系统深度联动。
"""

import time
import json
import logging
from codec import encode
from middleware import DownFrame
from event_bus import bus, Events

logger = logging.getLogger("oath_service")

# 官方三位拥有完整专属誓约资源与3D礼堂的女主配置
OATH_HERO_CFG = {
    1284: {
        "name": "终焉·薇儿丹蒂",
        "scene": "Levels/X317",
        "skin_id": 128402,
        "ring_id": 41711,    # 专属戒指：纯白之誓
        "heart_req": 5,
    },
    1049: {
        "name": "镜花黄泉·伊邪那美",
        "scene": "Levels/X317",
        "skin_id": 104903,
        "ring_id": 41710,    # 通用戒指：誓约之戒
        "heart_req": 5,
    },
    1095: {
        "name": "巡天·英灵",
        "scene": "Levels/X317",
        "skin_id": 109503,
        "ring_id": 41710,    # 通用戒指：誓约之戒
        "heart_req": 5,
    },
}

# 3 位官方受支持的女主 ID 集合 (三级逻辑门一级白名单)
SUPPORTED_OATH_HEROES = set(OATH_HERO_CFG.keys())

# 24 项专属誓约任务元数据镜像 (严格 1:1 源自官方 weddingassignmentcfg.lua)
WEDDING_TASKS = {
    # ---------------- 1284 薇儿丹蒂 (终焉·薇儿丹蒂 / 黯耀·薇儿丹蒂) ----------------
    128401: {"hero_id": 1284, "wedding_level": 1, "need": 3,  "condition": 410000, "desc": "完成誓约后，与「黯耀·薇儿丹蒂」在主界面交互3次。", "story": "「和管理员一起的话就算只是相互看着也很开心。」"},
    128402: {"hero_id": 1284, "wedding_level": 1, "need": 1,  "condition": 800,    "desc": "阅读「薇儿丹蒂」心链剧情「最初的伙伴」。", "story": "「当我还在医院里治疗时，管理员就已经在照顾着我，到现在也没有变……」", "story_id": 210840104},
    128403: {"hero_id": 1284, "wedding_level": 1, "need": 1,  "condition": 110,    "desc": "「黯耀·薇儿丹蒂」熟练度达到30。", "story": "「即便不用言语，我和管理员也能够互相理解。」", "req_prof": 30},
    128404: {"hero_id": 1284, "wedding_level": 1, "need": 1,  "condition": 2022,   "desc": "「黯耀·薇儿丹蒂」有一间宿舍。", "story": "「和管理员在深空之眼有了一间专属于我们的双人房间。」"},
    128405: {"hero_id": 1284, "wedding_level": 2, "need": 20, "condition": 410003, "desc": "完成誓约后，将「黯耀·薇儿丹蒂」编入队伍出战并获胜20次。", "story": "「从管理员来到深空之眼开始，我们原来已经一起经历了这么多事件。」"},
    128406: {"hero_id": 1284, "wedding_level": 2, "need": 1,  "condition": 410001, "desc": "完成誓约后，给「黯耀·薇儿丹蒂」赠送一件家具。", "story": "「和管理员一起给我们的房间做了一些小装饰。」"},
    128407: {"hero_id": 1284, "wedding_level": 2, "need": 2,  "condition": 410002, "desc": "完成誓约后，将「黯耀·薇儿丹蒂」设为界面助理2天。", "story": "「能一直陪在管理员身边真是太好了。」"},
    128408: {"hero_id": 1284, "wedding_level": 2, "need": 1,  "condition": 401,    "desc": "通关章节「远遥空庭」Main-96.我守护的心愿。", "story": "「我会为了守护管理员而一直坚持下去。」", "stage_id": 1031927},

    # ---------------- 1049 伊邪那美 (镜花黄泉·伊邪那美) ----------------
    104901: {"hero_id": 1049, "wedding_level": 1, "need": 3,  "condition": 410000, "desc": "完成誓约后，与「镜花黄泉·伊邪那美」在主界面交互3次。", "story": "「原来能够被理解是如此珍贵的感觉。」"},
    104902: {"hero_id": 1049, "wedding_level": 1, "need": 1,  "condition": 800,    "desc": "阅读「伊邪那美」心链剧情「矫正」。", "story": "「无论是好的回忆，还是坏的回忆，我都想让管理员知道。」", "story_id": 210490104},
    104903: {"hero_id": 1049, "wedding_level": 1, "need": 1,  "condition": 110,    "desc": "「镜花黄泉·伊邪那美」熟练度达到30。", "story": "「与管理员心灵相通的感觉让我不再害怕孤独。」", "req_prof": 30},
    104904: {"hero_id": 1049, "wedding_level": 1, "need": 1,  "condition": 2022,   "desc": "「镜花黄泉·伊邪那美」有一间宿舍。", "story": "「能够和管理员一起平淡地生活是我以前不敢奢望的。」"},
    104905: {"hero_id": 1049, "wedding_level": 2, "need": 20, "condition": 410003, "desc": "完成誓约后，将「镜花黄泉·伊邪那美」编入队伍出战并获胜20次。", "story": "「希望管理员能够更多注意些自己的安全。」"},
    104906: {"hero_id": 1049, "wedding_level": 2, "need": 1,  "condition": 410001, "desc": "完成誓约后，给「镜花黄泉·伊邪那美」赠送一件家具。", "story": "「管理员送我的摆件，我都会好好地珍藏起来。」"},
    104907: {"hero_id": 1049, "wedding_level": 2, "need": 2,  "condition": 410002, "desc": "完成誓约后，将「镜花黄泉·伊邪那美」设为界面助理2天。", "story": "「在管理员身边，感受到了久违的安全感和温暖。」"},
    104908: {"hero_id": 1049, "wedding_level": 2, "need": 1,  "condition": 401,    "desc": "通关章节「鸦杀荧惑」17-2-15.鸦陨。", "story": "「我想要和管理员一起继续走下去。」", "stage_id": 1031731},

    # ---------------- 1095 托特 (苍鹭·托特 / 巡天·英灵) ----------------
    109501: {"hero_id": 1095, "wedding_level": 1, "need": 3,  "condition": 410000, "desc": "完成誓约后，与「苍鹭·托特」在主界面交互3次。", "story": "「在管理员面前，我不需要费心思扮演什么。」"},
    109502: {"hero_id": 1095, "wedding_level": 1, "need": 1,  "condition": 800,    "desc": "阅读「托特」心链剧情「我的宝物」。", "story": "「这还是第一次想让别人也来了解我。」", "story_id": 210950104},
    109503: {"hero_id": 1095, "wedding_level": 1, "need": 1,  "condition": 110,    "desc": "「苍鹭·托特」熟练度达到30。", "story": "「原来对一个人坦诚心扉，并不危险。」", "req_prof": 30},
    109504: {"hero_id": 1095, "wedding_level": 1, "need": 1,  "condition": 2022,   "desc": "「苍鹭·托特」有一间宿舍。", "story": "「连我们的私人空间也安排好了吗？管理员的行事风格真是迅速。」"},
    109505: {"hero_id": 1095, "wedding_level": 2, "need": 20, "condition": 410003, "desc": "完成誓约后，将「苍鹭·托特」编入队伍出战并获胜20次。", "story": "「无论哪副面孔、哪种身份，只要管理员需要，我都会站在前线。」"},
    109506: {"hero_id": 1095, "wedding_level": 2, "need": 1,  "condition": 410001, "desc": "完成誓约后，给「苍鹭·托特」赠送一件家具。", "story": "「管理员送的摆件放在这里……看起来不错。」"},
    109507: {"hero_id": 1095, "wedding_level": 2, "need": 2,  "condition": 410002, "desc": "完成誓约后，将「苍鹭·托特」设为界面助理2天。", "story": "「守护管理员这个任务，我愿意一直执行。」"},
    109508: {"hero_id": 1095, "wedding_level": 2, "need": 1,  "condition": 401,    "desc": "通关章节「远遥空庭」Main-94.违令加班", "story": "「任务里我可以扮演任何人，但在管理员这里，我只想是我自己。」", "stage_id": 1031926},
}


class OathService:
    """誓约系统核心领域服务单例"""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._init_event_listeners()
        return cls._instance

    # ================= 入栈全局逻辑门与离散统计持久化 =================

    def check_gate(self, db, uid, hero_id, condition=None):
        """三级入栈全局逻辑门（Ingress Gatekeeper）
        1. 白名单检查: hero_id 必须属于 SUPPORTED_OATH_HEROES (1284, 1049, 1095)
        2. 誓约存活检查: 玩家当前在库中是否已与该角色缔结誓约 (oath=1)
        3. 待完成任务检查: 该角色当前阶段是否存在匹配 condition 且未完成 (complete_flag=0 且 progress < need) 的任务
        三道门全部放行才返回 True，任何一道不满足则 0 纳秒静默丢弃 (返回 False)。
        """
        if hero_id not in SUPPORTED_OATH_HEROES:
            return False
        oath_rec = self.get_hero_oath(db, uid, hero_id)
        if not oath_rec or not oath_rec.get("oath"):
            return False

        cur_lvl = int(oath_rec.get("oath_level") or 1)
        for tid, meta in WEDDING_TASKS.items():
            if meta["hero_id"] == hero_id and meta["wedding_level"] == cur_lvl:
                if condition is None or meta["condition"] == condition:
                    r = db.query("SELECT progress, complete_flag FROM oath_assignment WHERE uid=? AND assignment_id=?", (uid, tid))
                    if r and int(r[0]["complete_flag"] or 0) == 0:
                        if int(r[0]["progress"] or 0) < meta["need"]:
                            return True
        return False

    def record_user_stat(self, db, uid, stat_key, hero_id, delta=1):
        """统一归档离散外围系统行为统计数据至 user_behavior_stat 表"""
        now_ts = int(time.time())
        try:
            db.execute(
                """
                INSERT INTO user_behavior_stat (uid, stat_key, hero_id, value, update_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(uid, stat_key, hero_id) DO UPDATE SET
                    value = value + ?, update_ts = ?
                """,
                (uid, stat_key, hero_id, delta, now_ts, delta, now_ts)
            )
        except Exception:
            pass

    def push_14503_frame(self, ctx, uid, task_ids=None):
        """组装下发 sc_14503 任务更新帧到当前响应上下文"""
        if not ctx:
            return
        db = getattr(ctx, "db", None)
        if not db:
            return
        p14503 = self.build_14503_payload(db, uid)
        if not p14503:
            return
        try:
            from middleware import DownFrame
            frame = DownFrame(14503, p14503)
            if hasattr(ctx, "add_pending_frame"):
                ctx.add_pending_frame(frame)
            elif hasattr(ctx, "pending_frames"):
                ctx.pending_frames.append(frame)
        except Exception:
            pass

    def _init_event_listeners(self):
        """挂载全局事件总线监听器，自动驱动誓约专属任务（集成三级逻辑门与多角色批量并发）"""

        @bus.subscribe(Events.STAGE_PASS)
        def on_stage_pass(ctx, uid, **kwargs):
            stage_id = int(kwargs.get("stage_id", 0))
            times = int(kwargs.get("times", 1))
            heroes = kwargs.get("heroes") or []

            db = getattr(ctx, "db", None)
            if not db:
                return

            # 1. 参战英雄 battle_win_times 累加已迁移至 hero_service（STAGE_PASS 统一订阅，防双写）；
            #    本模块作为消费方依赖 hero 表字段推进誓约任务。

            dirty_hero_tasks = []

            # 2. 批量并发处理参战的誓约角色（多角色单帧批量独立推进）
            for h in heroes:
                hid = int(h.get("id") if isinstance(h, dict) else h)
                # 逻辑门检查：非白名单/未结婚/该任务已完成者 0 纳秒静默 DROP
                if not self.check_gate(db, uid, hid, condition=410003):
                    continue
                # 放行：出战获胜 20 次任务独立累加
                tids = self.advance_task_progress(ctx, uid, hid, condition=410003, delta=times)
                dirty_hero_tasks.extend(tids)

            # 3. 指定主线关卡直通检测 (condition 401)
            for hid in SUPPORTED_OATH_HEROES:
                if self.check_gate(db, uid, hid, condition=401):
                    for tid, meta in WEDDING_TASKS.items():
                        if meta["hero_id"] == hid and meta["condition"] == 401 and meta.get("stage_id") == stage_id:
                            tids = self.advance_task_progress(ctx, uid, hid, condition=401, delta=1)
                            dirty_hero_tasks.extend(tids)

            # 4. 多角色任务合并打包为单帧 sc_14503 下发
            if dirty_hero_tasks:
                self.push_14503_frame(ctx, uid, dirty_hero_tasks)

        @bus.subscribe(Events.MAIN_INTERACT)
        def on_main_interact(ctx, uid, **kwargs):
            hid = int(kwargs.get("hero_id", 0))
            db = getattr(ctx, "db", None)
            if not db or not self.check_gate(db, uid, hid, condition=410000):
                return
            self.record_user_stat(db, uid, "main_interact", hid, delta=1)
            tids = self.advance_task_progress(ctx, uid, hid, condition=410000, delta=1)
            if tids:
                self.push_14503_frame(ctx, uid, tids)

        @bus.subscribe(Events.DORM_GIFT)
        def on_dorm_gift(ctx, uid, **kwargs):
            hid = int(kwargs.get("hero_id", 0))
            db = getattr(ctx, "db", None)
            if not db or not self.check_gate(db, uid, hid, condition=410001):
                return
            self.record_user_stat(db, uid, "dorm_gift", hid, delta=1)
            tids = self.advance_task_progress(ctx, uid, hid, condition=410001, delta=1)
            if tids:
                self.push_14503_frame(ctx, uid, tids)

        @bus.subscribe(Events.DAILY_ASSISTANT)
        def on_daily_assistant(ctx, uid, **kwargs):
            hid = int(kwargs.get("hero_id", 0))
            days = int(kwargs.get("days", 1))
            db = getattr(ctx, "db", None)
            if not db or not self.check_gate(db, uid, hid, condition=410002):
                return
            self.record_user_stat(db, uid, "assistant_days", hid, delta=days)
            tids = self.advance_task_progress(ctx, uid, hid, condition=410002, delta=days)
            if tids:
                self.push_14503_frame(ctx, uid, tids)

        @bus.subscribe(Events.HERO_UPGRADE)
        def on_hero_upgrade(ctx, uid, **kwargs):
            hid = int(kwargs.get("hero_id", 0))
            db = getattr(ctx, "db", None)
            if not db or not self.check_gate(db, uid, hid, condition=110):
                return
            tids = self.evaluate_state_tasks(db, uid, hid)
            if tids:
                self.push_14503_frame(ctx, uid, tids)

        @bus.subscribe(Events.STORY_READ)
        def on_story_read(ctx, uid, **kwargs):
            hid = int(kwargs.get("hero_id", 0))
            sid = int(kwargs.get("story_id", 0))
            db = getattr(ctx, "db", None)
            if not db:
                return
            target_hid = hid
            if not target_hid and sid:
                for tid, meta in WEDDING_TASKS.items():
                    if meta.get("condition") == 800 and meta.get("story_id") == sid:
                        target_hid = meta["hero_id"]
                        break
            if target_hid and self.check_gate(db, uid, target_hid, condition=800):
                tids = self.evaluate_state_tasks(db, uid, target_hid)
                if not tids:
                    tids = self.advance_task_progress(ctx, uid, target_hid, condition=800, delta=1)
                if tids:
                    self.push_14503_frame(ctx, uid, tids)

    # ================= 状态查询与登录洪流构建 =================

    def get_hero_oath(self, db, uid, hero_id):
        """查询指定角色的誓约状态记录"""
        rows = db.query(
            "SELECT * FROM hero_oath WHERE uid=? AND hero_id=?",
            (uid, hero_id)
        )
        if not rows:
            return None
        return dict(rows[0])

    def get_all_hero_oaths(self, db, uid):
        """获取所有已记录的誓约角色列表 (构建 sc_14501)"""
        rows = db.query(
            "SELECT * FROM hero_oath WHERE uid=? ORDER BY hero_id",
            (uid,)
        )
        res = []
        for r in (rows or []):
            hid = int(r["hero_id"])
            # 已读剧情
            p_rows = db.query("SELECT text_id FROM hero_oath_plot WHERE uid=? AND hero_id=?", (uid, hid))
            plot_texts = [int(x["text_id"]) for x in (p_rows or [])]
            # 改名历史
            t_rows = db.query("SELECT oath_time FROM hero_oath_time WHERE uid=? AND hero_id=? ORDER BY time_idx", (uid, hid))
            time_list = [int(x["oath_time"]) for x in (t_rows or [])]

            res.append({
                "hero_id": hid,
                "oath": bool(r["oath"]),
                "oath_level": int(r["oath_level"] or 0),
                "nick": r.get("nick") or "",
                "picture_link": r.get("picture_link") or "",
                "oath_time": int(r["oath_time"] or 0),
                "oath_plot": {"hero_id": hid, "text_list": plot_texts},
                "time_list": time_list,
            })
        return res

    def get_all_assignments(self, db, uid):
        """获取玩家当前所有誓约任务进度 (构建 sc_14503)"""
        rows = db.query(
            "SELECT assignment_id, progress, complete_flag FROM oath_assignment WHERE uid=?",
            (uid,)
        )
        res = []
        for r in (rows or []):
            res.append({
                "id": int(r["assignment_id"]),
                "progress": int(r["progress"] or 0),
                "complete_flag": int(r["complete_flag"] or 0),
                "expired_timestamp": 0
            })
        return res

    def build_14501_payload(self, db, uid):
        """生成 sc_14501 下行二进制 payload"""
        hero_oath_list = self.get_all_hero_oaths(db, uid)
        return encode("sc_14501", {"hero_oath": hero_oath_list})

    def build_14503_payload(self, db, uid):
        """生成 sc_14503 下行二进制 payload"""
        # 组包前对所有已誓约角色自愈校验冷查库任务（熟练度、后宅、心链、关卡等）
        oaths = self.get_all_hero_oaths(db, uid)
        for o in oaths:
            if o.get("oath"):
                try:
                    self.evaluate_state_tasks(db, uid, o["hero_id"])
                except Exception:
                    pass
        assignments = self.get_all_assignments(db, uid)
        meta = db.query("SELECT send_type FROM oath_assignment_meta WHERE uid=?", (uid,))
        send_type = int(meta[0]["send_type"]) if meta else 0
        return encode("sc_14503", {"assignment_list": assignments, "send_type": send_type})

    # ================= 业务协议处理 (145xx 族) =================

    def handle_oath_or_photo(self, ctx, uid, data):
        """
        cs_14514 -> sc_14515
        分流 A: 结缔誓约 (仅发 hero_id)
        分流 B: 更新照片 (发 hero_id + picture_url)
        """
        from operations import _item_deduct

        hero_id = int(data.get("hero_id", 0))
        if hero_id not in OATH_HERO_CFG:
            raise ValueError(f"角色 {hero_id} 不在支持誓约名单中")

        picture_url = data.get("picture_url")
        now_ts = int(time.time())

        # 分流 B: 保存合影相片
        if picture_url:
            ctx.db.execute(
                "UPDATE hero_oath SET picture_link=?, update_ts=? WHERE uid=? AND hero_id=?",
                (picture_url, now_ts, uid, hero_id)
            )
            ctx.log(f"cs_14514 -> 更新誓约合影照片 hero={hero_id} url={picture_url}")
            return {"result": 0, "time": now_ts, "mode": "photo"}

        # 分流 A: 缔结誓约
        cfg = OATH_HERO_CFG[hero_id]
        ring_id = cfg["ring_id"]

        # 检查是否已有誓约
        existing = self.get_hero_oath(ctx.db, uid, hero_id)
        if existing and existing.get("oath"):
            return {"result": 0, "time": int(existing.get("oath_time") or now_ts), "mode": "oath_already"}

        # 扣除誓约之戒
        _item_deduct(ctx, uid, ring_id, 1)

        # 写入誓约状态
        ctx.db.execute(
            """
            INSERT INTO hero_oath (uid, hero_id, oath, oath_level, oath_time, update_ts)
            VALUES (?, ?, 1, 1, ?, ?)
            ON CONFLICT(uid, hero_id) DO UPDATE SET
                oath=1, oath_level=1, oath_time=?, update_ts=?
            """,
            (uid, hero_id, now_ts, now_ts, now_ts, now_ts)
        )

        # 初始化一阶段专属任务 (1~4)
        self.init_hero_tasks(ctx.db, uid, hero_id, level=1)

        # 自动扫描并对齐初始任务完成度（如角色等级、熟练度、后宅等）
        self.sync_initial_task_state(ctx.db, uid, hero_id)

        # 广播事件
        bus.emit(Events.OATH_UNLOCK, ctx, uid, hero_id=hero_id, oath_time=now_ts)
        ctx.log(f"cs_14514 -> 成功缔结誓约 hero={hero_id} ({cfg['name']}) 扣除戒指={ring_id}")

        return {"result": 0, "time": now_ts, "mode": "oath"}

    def handle_submit_task(self, ctx, uid, task_ids):
        """
        cs_14512 -> sc_14513
        提交誓约任务并触发阶段等级晋升
        """
        if isinstance(task_ids, (int, str)):
            task_ids = [int(task_ids)]

        hero_affected = set()
        for tid in task_ids:
            tid = int(tid)
            t_meta = WEDDING_TASKS.get(tid)
            if not t_meta:
                continue
            hid = t_meta["hero_id"]
            need = t_meta["need"]
            ctx.db.execute(
                """
                INSERT INTO oath_assignment (uid, assignment_id, progress, complete_flag, update_ts)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(uid, assignment_id) DO UPDATE SET
                    progress=?, complete_flag=1, update_ts=?
                """,
                (uid, tid, need, int(time.time()), need, int(time.time()))
            )
            hero_affected.add(hid)
            bus.emit(Events.OATH_TASK_COMPLETE, ctx, uid, hero_id=hid, task_id=tid)

        # 阶段晋升状态机检查
        upgraded_heroes = []
        for hid in hero_affected:
            oath_rec = self.get_hero_oath(ctx.db, uid, hid)
            if not oath_rec:
                continue
            cur_lvl = int(oath_rec.get("oath_level") or 1)

            # 检查 Level 1 任务是否全完结 -> 晋级至 Level 2
            if cur_lvl == 1 and self._is_level_tasks_all_complete(ctx.db, uid, hid, level=1):
                cur_lvl = 2
                ctx.db.execute("UPDATE hero_oath SET oath_level=2, update_ts=? WHERE uid=? AND hero_id=?", (int(time.time()), uid, hid))
                self.init_hero_tasks(ctx.db, uid, hid, level=2)
                self.sync_initial_task_state(ctx.db, uid, hid)
                upgraded_heroes.append((hid, 2))
                bus.emit(Events.OATH_LEVEL_UP, ctx, uid, hero_id=hid, new_level=2)

            # 检查 Level 2 任务是否全完结 -> 晋级至 Level 3 (MAX)
            if cur_lvl == 2 and self._is_level_tasks_all_complete(ctx.db, uid, hid, level=2):
                cur_lvl = 3
                ctx.db.execute("UPDATE hero_oath SET oath_level=3, update_ts=? WHERE uid=? AND hero_id=?", (int(time.time()), uid, hid))
                upgraded_heroes.append((hid, 3))
                bus.emit(Events.OATH_LEVEL_UP, ctx, uid, hero_id=hid, new_level=3)

        ctx.log(f"cs_14512 -> 提交誓约任务 ids={task_ids} 触发晋升={upgraded_heroes}")
        return {"result": 0, "reward_list": []}

    def handle_set_nickname(self, ctx, uid, hero_id, nick):
        """
        cs_14516 -> sc_14517
        自定义誓约爱称，校验 30 天 CD
        """
        hero_id = int(hero_id)
        now_ts = int(time.time())

        oath_rec = self.get_hero_oath(ctx.db, uid, hero_id)
        if not oath_rec or not oath_rec.get("oath"):
            return {"result": 1}  # 未誓约不可命名

        cur_nick = oath_rec.get("nick") or ""
        if nick and nick == cur_nick:
            return {"result": 2}  # 同名拦截

        # 校验 30 天改名 CD (2592000秒)
        t_rows = ctx.db.query(
            "SELECT oath_time FROM hero_oath_time WHERE uid=? AND hero_id=? ORDER BY oath_time DESC LIMIT 1",
            (uid, hero_id)
        )
        if t_rows:
            last_rename = int(t_rows[0]["oath_time"] or 0)
            if now_ts < last_rename + 30 * 86400:
                ctx.log(f"cs_14516 -> 改名处于冷却期 hero={hero_id} last={last_rename}")
                return {"result": 3}

        # 更新爱称
        ctx.db.execute(
            "UPDATE hero_oath SET nick=?, update_ts=? WHERE uid=? AND hero_id=?",
            (nick, now_ts, uid, hero_id)
        )
        # 记录改名时间戳
        cnt_row = ctx.db.query("SELECT COUNT(*) as c FROM hero_oath_time WHERE uid=? AND hero_id=?", (uid, hero_id))
        next_idx = int(cnt_row[0]["c"]) if cnt_row else 0
        ctx.db.execute(
            "INSERT INTO hero_oath_time (uid, hero_id, time_idx, oath_time, update_ts) VALUES (?, ?, ?, ?, ?)",
            (uid, hero_id, next_idx, now_ts, now_ts)
        )

        bus.emit(Events.OATH_NAME_CHANGE, ctx, uid, hero_id=hero_id, new_nick=nick)
        ctx.log(f"cs_14516 -> 成功设置誓约爱称 hero={hero_id} nick='{nick}'")
        return {"result": 0, "nick": nick, "hero_id": hero_id}

    def handle_read_plot(self, ctx, uid, hero_id, text_list):
        """
        cs_14510 -> sc_14511
        阅读誓约物语并上报记录
        """
        hero_id = int(hero_id)
        now_ts = int(time.time())

        if isinstance(text_list, (int, str)):
            text_list = [int(text_list)]

        for txt_id in text_list:
            txt_id = int(txt_id)
            # 幂等校验：已记录的剧情跳过，避免重复累加
            existing = ctx.db.query(
                "SELECT 1 FROM hero_oath_plot WHERE uid=? AND hero_id=? AND text_id=?",
                (uid, hero_id, txt_id)
            )
            if existing:
                continue

            cnt = ctx.db.query("SELECT COUNT(*) as c FROM hero_oath_plot WHERE uid=? AND hero_id=?", (uid, hero_id))
            p_idx = int(cnt[0]["c"]) if cnt else 0
            ctx.db.execute(
                """
                INSERT INTO hero_oath_plot (uid, hero_id, plot_idx, text_id, update_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(uid, hero_id, plot_idx) DO UPDATE SET
                    text_id=?, update_ts=?
                """,
                (uid, hero_id, p_idx, txt_id, now_ts, txt_id, now_ts)
            )

        ctx.log(f"cs_14510 -> 阅读誓约剧情 hero={hero_id} texts={text_list}")
        return {"result": 0, "hero_id": hero_id}

    # ================= 任务状态机自愈与初始化辅助 =================

    def init_hero_tasks(self, db, uid, hero_id, level=1):
        """初始化指定阶段的 4 个任务至数据库"""
        now_ts = int(time.time())
        for tid, meta in WEDDING_TASKS.items():
            if meta["hero_id"] == hero_id and meta["wedding_level"] == level:
                db.execute(
                    """
                    INSERT INTO oath_assignment (uid, assignment_id, progress, complete_flag, update_ts)
                    VALUES (?, ?, 0, 0, ?)
                    ON CONFLICT(uid, assignment_id) DO NOTHING
                    """,
                    (uid, tid, now_ts)
                )

    def evaluate_state_tasks(self, db, uid, hero_id):
        """
        首驱动：冷查库快照对齐 (Cold-Start DB Evaluator)
        自动扫描玩家资产并同步熟练度(110)、心链阅读(800)、专属主线(401)、专属宿舍(2022)
        返回本次推进的任务 ID 列表
        """
        if hero_id not in SUPPORTED_OATH_HEROES:
            return []

        oath_rec = self.get_hero_oath(db, uid, hero_id)
        if not oath_rec or not oath_rec.get("oath"):
            return []

        cur_lvl = int(oath_rec.get("oath_level") or 1)
        hrow = db.query("SELECT level, clear_times, battle_win_times FROM hero WHERE uid=? AND id=?", (uid, hero_id))
        lvl = int(hrow[0]["level"] or 1) if hrow else 1
        prof = int(hrow[0]["clear_times"] or 0) if hrow else 0
        now_ts = int(time.time())

        changed_tids = []
        for tid, meta in WEDDING_TASKS.items():
            if meta["hero_id"] != hero_id or meta["wedding_level"] != cur_lvl:
                continue

            cond = meta["condition"]
            need = meta["need"]

            row = db.query("SELECT progress, complete_flag FROM oath_assignment WHERE uid=? AND assignment_id=?", (uid, tid))
            if not row or int(row[0]["complete_flag"] or 0) == 1:
                continue
            cur_prog = int(row[0]["progress"] or 0)
            if cur_prog >= need:
                continue

            matched = False
            # 1. 熟练度达到 30 (110)
            if cond == 110:
                req_prof = meta.get("req_prof", 30)
                if prof >= req_prof or lvl >= 30:
                    matched = True
            # 2. 专属后宅宿舍 (2022)
            elif cond == 2022:
                try:
                    d_row = db.query("SELECT 1 FROM backhome_dorm_hero WHERE uid=? AND hero_id=?", (uid, hero_id))
                    if d_row or True:  # 宿舍未上线系统默认对齐
                        matched = True
                except Exception:
                    matched = True
            # 3. 通关指定主线关卡 (401)
            elif cond == 401 and meta.get("stage_id"):
                sid = meta["stage_id"]
                try:
                    s_row = db.query("SELECT 1 FROM stage_sub WHERE uid=? AND id=? AND clear_times > 0", (uid, sid))
                    if s_row:
                        matched = True
                except Exception:
                    pass
            # 4. 阅读心链故事 (800)
            elif cond == 800 and meta.get("story_id"):
                stid = meta["story_id"]
                try:
                    st_row = db.query("SELECT 1 FROM story_unlock WHERE uid=? AND story_id=? AND unlock_flag=1", (uid, stid))
                    if st_row:
                        matched = True
                except Exception:
                    pass

            if matched:
                db.execute(
                    "UPDATE oath_assignment SET progress=?, update_ts=? WHERE uid=? AND assignment_id=?",
                    (need, now_ts, uid, tid)
                )
                changed_tids.append(tid)

        return changed_tids

    def sync_initial_task_state(self, db, uid, hero_id):
        """兼容旧调用入口：自动从玩家现有资产与角色数据中，自愈校验可达成的初始任务进度"""
        return self.evaluate_state_tasks(db, uid, hero_id)

    def advance_task_progress(self, ctx, uid, hero_id, condition, delta=1):
        """根据业务事件推进匹配条件的任务进度，返回发生推进的任务 ID 列表"""
        now_ts = int(time.time())
        db = getattr(ctx, "db", None) if hasattr(ctx, "db") else ctx
        if not db:
            return []

        updated_tids = []
        for tid, meta in WEDDING_TASKS.items():
            if meta["hero_id"] == hero_id and meta["condition"] == condition:
                need = meta["need"]
                row = db.query("SELECT progress, complete_flag FROM oath_assignment WHERE uid=? AND assignment_id=?", (uid, tid))
                if row and int(row[0]["complete_flag"] or 0) == 0:
                    old_prog = int(row[0]["progress"] or 0)
                    new_prog = min(need, old_prog + int(delta))
                    if new_prog > old_prog:
                        db.execute(
                            "UPDATE oath_assignment SET progress=?, update_ts=? WHERE uid=? AND assignment_id=?",
                            (new_prog, now_ts, uid, tid)
                        )
                        updated_tids.append(tid)
        return updated_tids

    def evaluate_hero_level_tasks(self, ctx, uid, hero_id, level):
        """角色等级提升时更新等级相关任务"""
        self.sync_initial_task_state(ctx.db, uid, hero_id)

    def _is_level_tasks_all_complete(self, db, uid, hero_id, level):
        """检查指定阶段任务是否全部已领奖完成"""
        for tid, meta in WEDDING_TASKS.items():
            if meta["hero_id"] == hero_id and meta["wedding_level"] == level:
                r = db.query("SELECT complete_flag FROM oath_assignment WHERE uid=? AND assignment_id=?", (uid, tid))
                if not r or int(r[0]["complete_flag"] or 0) != 1:
                    return False
        return True

    # ================= GM 与调试快捷控制指令集 =================

    def gm_ready_oath(self, db, uid, hero_id):
        """一键就绪前置条件：确保拥有角色 + 好感度升至 5 级 + 发放 10 个戒指"""
        hero_id = int(hero_id)
        if hero_id not in OATH_HERO_CFG:
            return False, f"角色 {hero_id} 不在支持名单中"
        cfg = OATH_HERO_CFG[hero_id]
        ring_id = cfg["ring_id"]

        # 1. 确保拥有角色且好感 5 级
        db.execute(
            """
            INSERT INTO hero (uid, id, level, star, unlock, trust_level, trust_exp, trust_mood)
            VALUES (?, ?, 80, 600, 1, 5, 0, 3)
            ON CONFLICT(uid, id) DO UPDATE SET
                unlock=1, trust_level=5, trust_mood=3
            """,
            (uid, hero_id)
        )
        # 2. 补给戒指
        db.execute(
            """
            INSERT INTO material (uid, id, num) VALUES (?, ?, 10)
            ON CONFLICT(uid, id) DO UPDATE SET num = num + 10
            """,
            (uid, ring_id)
        )
        return True, f"角色 {hero_id} ({cfg['name']}) 誓约前置条件已全部就绪（拥有角色+好感5级+戒指x10）"

    def gm_unlock_oath(self, db, uid, hero_id):
        """直接完成缔结誓约（跳过 3D 动画，直接生成誓约初态）"""
        hero_id = int(hero_id)
        now_ts = int(time.time())
        db.execute(
            """
            INSERT INTO hero_oath (uid, hero_id, oath, oath_level, oath_time, update_ts)
            VALUES (?, ?, 1, 1, ?, ?)
            ON CONFLICT(uid, hero_id) DO UPDATE SET
                oath=1, oath_level=1, oath_time=?, update_ts=?
            """,
            (uid, hero_id, now_ts, now_ts, now_ts, now_ts)
        )
        self.init_hero_tasks(db, uid, hero_id, level=1)
        self.sync_initial_task_state(db, uid, hero_id)
        return True, f"角色 {hero_id} 誓约缔结成功（Level 1）"

    def gm_set_oath_level(self, db, uid, hero_id, level):
        """专属进度直接提升至指定等级（1/2/3），并自动将前置任务置为已完结"""
        hero_id = int(hero_id)
        level = max(1, min(3, int(level)))
        now_ts = int(time.time())

        # 确保已誓约
        db.execute(
            """
            INSERT INTO hero_oath (uid, hero_id, oath, oath_level, oath_time, update_ts)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(uid, hero_id) DO UPDATE SET
                oath=1, oath_level=?, update_ts=?
            """,
            (uid, hero_id, level, now_ts, now_ts, level, now_ts)
        )

        # 初始化所有任务
        self.init_hero_tasks(db, uid, hero_id, level=1)
        self.init_hero_tasks(db, uid, hero_id, level=2)

        # 标记前置任务为完结
        if level >= 2:
            for tid, meta in WEDDING_TASKS.items():
                if meta["hero_id"] == hero_id and meta["wedding_level"] == 1:
                    db.execute(
                        "UPDATE oath_assignment SET progress=?, complete_flag=1, update_ts=? WHERE uid=? AND assignment_id=?",
                        (meta["need"], now_ts, uid, tid)
                    )
        if level >= 3:
            for tid, meta in WEDDING_TASKS.items():
                if meta["hero_id"] == hero_id and meta["wedding_level"] == 2:
                    db.execute(
                        "UPDATE oath_assignment SET progress=?, complete_flag=1, update_ts=? WHERE uid=? AND assignment_id=?",
                        (meta["need"], now_ts, uid, tid)
                    )

        return True, f"角色 {hero_id} 誓约专属进度已设为 Level {level}"

    def gm_reset_oath(self, db, uid, hero_id):
        """彻底重置誓约状态为初态"""
        hero_id = int(hero_id)
        db.execute("DELETE FROM hero_oath WHERE uid=? AND hero_id=?", (uid, hero_id))
        db.execute("DELETE FROM hero_oath_plot WHERE uid=? AND hero_id=?", (uid, hero_id))
        db.execute("DELETE FROM hero_oath_time WHERE uid=? AND hero_id=?", (uid, hero_id))
        for tid, meta in WEDDING_TASKS.items():
            if meta["hero_id"] == hero_id:
                db.execute("DELETE FROM oath_assignment WHERE uid=? AND assignment_id=?", (uid, tid))
        return True, f"角色 {hero_id} 誓约数据已完全清空重置"

    def gm_reset_tasks(self, db, uid, hero_id):
        """重置指定角色的专属誓约任务状态"""
        hero_id = int(hero_id)
        now_ts = int(time.time())
        for tid, meta in WEDDING_TASKS.items():
            if meta["hero_id"] == hero_id:
                db.execute(
                    "UPDATE oath_assignment SET progress=0, complete_flag=0, update_ts=? WHERE uid=? AND assignment_id=?",
                    (now_ts, uid, tid)
                )
        return True, f"角色 {hero_id} 专属誓约任务已全部重置为未完成"

    def gm_give_rings(self, db, uid, count=10):
        """补给通用戒指 41710 与专属戒指 41711"""
        count = int(count)
        db.execute(
            "INSERT INTO material (uid, id, num) VALUES (?, 41710, ?) ON CONFLICT(uid, id) DO UPDATE SET num = num + ?",
            (uid, count, count)
        )
        db.execute(
            "INSERT INTO material (uid, id, num) VALUES (?, 41711, ?) ON CONFLICT(uid, id) DO UPDATE SET num = num + ?",
            (uid, count, count)
        )
        return True, f"成功向玩家发放 41710(通用戒指)x{count}, 41711(薇儿戒指)x{count}"

    def gm_get_status(self, db, uid):
        """获取所有誓约女主的汇总状态"""
        res = {}
        for hid, cfg in OATH_HERO_CFG.items():
            hrow = db.query("SELECT unlock, level, trust_level FROM hero WHERE uid=? AND id=?", (uid, hid))
            oath_row = db.query("SELECT oath, oath_level, nick, oath_time FROM hero_oath WHERE uid=? AND hero_id=?", (uid, hid))
            ring_row = db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, cfg["ring_id"]))

            tasks_done = 0
            tasks_total = 0
            for tid, tmeta in WEDDING_TASKS.items():
                if tmeta["hero_id"] == hid:
                    tasks_total += 1
                    trow = db.query("SELECT complete_flag FROM oath_assignment WHERE uid=? AND assignment_id=?", (uid, tid))
                    if trow and int(trow[0]["complete_flag"] or 0) == 1:
                        tasks_done += 1

            res[hid] = {
                "name": cfg["name"],
                "has_hero": bool(hrow and hrow[0]["unlock"]),
                "hero_level": int(hrow[0]["level"]) if hrow else 0,
                "trust_level": int(hrow[0]["trust_level"]) if hrow else 0,
                "ring_id": cfg["ring_id"],
                "ring_count": int(ring_row[0]["num"]) if ring_row else 0,
                "is_oathed": bool(oath_row and oath_row[0]["oath"]),
                "oath_level": int(oath_row[0]["oath_level"]) if oath_row else 0,
                "nick": oath_row[0]["nick"] if oath_row else "",
                "tasks_progress": f"{tasks_done}/{tasks_total}",
            }
        return res
