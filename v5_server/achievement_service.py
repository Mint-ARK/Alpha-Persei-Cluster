# -*- coding: utf-8 -*-
"""
achievement_service.py — V5 服务端成就领域服务模块 (AchievementService)

[2026-09-08 领域架构落地] 遵循老大指导思想：
1. 成就系统为纯接收终端：以监听订阅底层各业务系统事件为主，唯一的主动发射源是通知资产管理模块 (InventoryService) 入库奖励；
2. 全动态即时弹窗引擎 (Runtime Dynamic Engine)：全覆盖升级、突破、抽卡、通关、后宅等场景，
   在业务触发当场生成并挂载专属原子差量帧 sc_53003，驱动客户端原生弹出金色成就 Banner (manager.achievementTips:AddAchievementID)；
3. 静态快照自愈引擎 (State Reconciler)：登录 (USER_LOGIN) 与冷启动时静默对账，
   扫描 hero、equip、users、chip、player_card、tower 等已有持久化表，实现 100% 自动补漏与状态保底；
4. 兼顾大头业务与小零碎系统：覆盖角色养成、关卡战斗、局内表现、装备刻印、后宅、十连抽卡，
   以及账号基础 (管理员等级/累计签到)、芯片智库 (管理喵/装配)、贴纸画册等全部 478 项官方配置。
"""

import json
import logging
import os
import threading
import time

from codec import encode, decode
from core import Operation, OperationError, operation
from event_bus import bus, Events
from middleware import DownFrame

logger = logging.getLogger("achievement_service")

_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------- 全局配置与静态元数据缓存 ----------------
_CFG = {}
_CFG_BY_COND = {}
_HERO_RACE_MAP = {}
_INITIALIZED = False

# 成就物语静态配置 (1..6)
ACHIEVEMENT_STORIES = {
    1: {"id": 1, "unlock_point": 100, "name": "起源之星·上篇"},
    2: {"id": 2, "unlock_point": 200, "name": "起源之星·下篇"},
    3: {"id": 3, "unlock_point": 300, "name": "拓扑之礼·上篇"},
    4: {"id": 4, "unlock_point": 400, "name": "拓扑之礼·下篇"},
    5: {"id": 5, "unlock_point": 500, "name": "星河长卷·上篇"},
    6: {"id": 6, "unlock_point": 600, "name": "星河长卷·下篇"},
}


def _ensure_cfg():
    global _CFG, _CFG_BY_COND, _HERO_RACE_MAP, _INITIALIZED
    if _INITIALIZED:
        return
    cfg_path = os.path.join(_DIR, "achievement_cfg.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                _CFG = {int(k): v for k, v in raw.items()}
                _CFG_BY_COND = {}
                for aid, item in _CFG.items():
                    cond = int(item.get("condition") or 0)
                    _CFG_BY_COND.setdefault(cond, []).append(item)
        except Exception as e:
            logger.error(f"加载 achievement_cfg.json 失败: {e}")
            _CFG = {}
            _CFG_BY_COND = {}
    else:
        logger.warning(f"未找到 achievement_cfg.json 路径: {cfg_path}")

    race_path = os.path.join(_DIR, "hero_race_map.json")
    if os.path.exists(race_path):
        try:
            with open(race_path, "r", encoding="utf-8") as f:
                _HERO_RACE_MAP = {int(k): int(v) for k, v in json.load(f).items()}
        except Exception as e:
            logger.warning(f"加载 hero_race_map.json 失败: {e}")
            _HERO_RACE_MAP = {}

    _INITIALIZED = True


def _load_user_extra(db, uid):
    if not db:
        return {}
    rows = db.query("SELECT extra FROM users WHERE uid=?", (uid,))
    if not rows or not rows[0].get("extra"):
        return {}
    try:
        return json.loads(rows[0]["extra"])
    except Exception:
        return {}


def _save_user_extra(db, uid, extra):
    if not db:
        return
    extra_str = json.dumps(extra, ensure_ascii=False)
    db.execute("UPDATE users SET extra=? WHERE uid=?", (extra_str, uid))


def _append_frame_to_ctx(ctx, frame):
    """向请求上下文统一挂载下行帧"""
    if not ctx or not frame:
        return
    if hasattr(ctx, "append_frame"):
        ctx.append_frame(frame)
    elif hasattr(ctx, "pending_frames"):
        if ctx.pending_frames is None:
            ctx.pending_frames = []
        ctx.pending_frames.append(frame)


# ======================================================================
# 成就领域服务核心类
# ======================================================================

class AchievementService:
    """成就领域服务单例"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self, db=None):
        _ensure_cfg()
        self.db = db

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db)
        if db is not None:
            cls._instance.db = db
        return cls._instance

    # ------------------------------------------------------------------
    # 基础与登录全量包 (Login Payload sc_53001)
    # ------------------------------------------------------------------

    def init_user_achievements(self, uid):
        """确保玩家数据库中存在全量 478 项成就行"""
        if not self.db or not _CFG:
            return
        rows = self.db.query("SELECT achievement_id FROM achievement WHERE uid=?", (uid,))
        existing_ids = {int(r["achievement_id"]) for r in (rows or [])}
        missing_ids = set(_CFG.keys()) - existing_ids
        if not missing_ids:
            return

        insert_batch = []
        for aid in missing_ids:
            cfg_item = _CFG[aid]
            insert_batch.append((
                uid,
                aid,
                cfg_item.get("name", ""),
                int(cfg_item.get("condition") or 0),
                cfg_item.get("desc", ""),
                int(cfg_item.get("need") or 0),
                json.dumps(cfg_item.get("reward") or []),
                0,  # progress
                0,  # complete_flag
                0,  # achieve_time
            ))

        sql = """
            INSERT OR IGNORE INTO achievement 
            (uid, achievement_id, name, condition, desc, need, reward_json, progress, complete_flag, achieve_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            self.db.executemany(sql, insert_batch)
            logger.info(f"[achievement] 初始化 uid={uid} 成就数据，补充 {len(insert_batch)} 条记录")
        except Exception as e:
            logger.error(f"[achievement] 批量初始化成就失败 uid={uid}: {e}")

    def get_achievement_login_payload(self, uid):
        """
        组装登录全量包 sc_53001：
        包含全量 478 项成就状态 (achievement_list) 与已读物语列表 (story_line)
        """
        _ensure_cfg()
        self.init_user_achievements(uid)
        # 登录时触发一次静态快照自愈对账
        self.reconcile_achievements(uid)

        rows = self.db.query(
            "SELECT achievement_id, progress, complete_flag, achieve_time FROM achievement WHERE uid=? ORDER BY achievement_id",
            (uid,)
        )
        now_ts = int(time.time())
        ach_list = []
        for r in (rows or []):
            a_ts = int(r.get("achieve_time") or 0)
            if a_ts == 1786411503:
                a_ts = now_ts if (int(r.get("progress") or 0) >= int(r.get("need") or 0)) else 0
            ach_list.append({
                "id": int(r["achievement_id"]),
                "progress": int(r.get("progress") or 0),
                "achieve_time": a_ts,
                "complete_flag": int(r.get("complete_flag") or 0)
            })

        extra = _load_user_extra(self.db, uid)
        story_read = extra.get("achievement_story_read")
        if story_read is None:
            # 兼容老账号：根据持有的成就点数 (货币33) 自动解锁已达标物语
            c_rows = self.db.query("SELECT num FROM currency WHERE uid=? AND id=33", (uid,))
            pts = int(c_rows[0]["num"] or 0) if c_rows else 0
            if pts >= 100:
                story_read = [s_id for s_id, s_cfg in ACHIEVEMENT_STORIES.items() if pts >= s_cfg["unlock_point"]]
            else:
                story_read = []
            extra["achievement_story_read"] = story_read
            _save_user_extra(self.db, uid, extra)

        return encode("sc_53001", {
            "achievement_list": ach_list,
            "story_line": list(story_read or [])
        })

    # ------------------------------------------------------------------
    # 奖励领取 (Claim Rewards cs_53004 -> sc_53005)
    # ------------------------------------------------------------------

    def claim_achievements(self, uid, aid_list, ctx=None):
        """
        领奖业务核心：
        支持单领 (GetReceiveReward) 与一键全领 (TryToSubmitAchievementList)。
        严格校验未领 (complete_flag=0) 与达标 (progress>=need)，防刷防重领。
        通过 InventoryService 统一入库移转之辉与成就点 (货币33)。
        """
        _ensure_cfg()
        if not aid_list:
            return {"result": 0, "reward_list": [], "claimed_ids": []}

        clean_ids = [int(x) for x in aid_list if int(x) in _CFG]
        if not clean_ids:
            return {"result": 0, "reward_list": [], "claimed_ids": []}

        placeholders = ",".join("?" for _ in clean_ids)
        rows = self.db.query(
            f"SELECT achievement_id, progress, need, complete_flag FROM achievement WHERE uid=? AND achievement_id IN ({placeholders})",
            (uid, *clean_ids)
        )
        row_map = {int(r["achievement_id"]): r for r in (rows or [])}

        valid_ids = []
        raw_rewards = []

        for aid in clean_ids:
            r = row_map.get(aid)
            if not r:
                continue
            # 已领过的跳过
            if int(r.get("complete_flag") or 0) == 1:
                continue
            # 未达成指标的跳过
            prog = int(r.get("progress") or 0)
            need = int(r.get("need") or 0)
            if prog < need:
                continue

            valid_ids.append(aid)
            cfg_item = _CFG[aid]
            for pair in (cfg_item.get("reward") or []):
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    raw_rewards.append((int(pair[0]), int(pair[1])))

        if not valid_ids:
            return {"result": 0, "reward_list": [], "claimed_ids": []}

        # 1. 标记 complete_flag = 1 (已领奖)
        v_placeholders = ",".join("?" for _ in valid_ids)
        self.db.execute(
            f"UPDATE achievement SET complete_flag=1 WHERE uid=? AND achievement_id IN ({v_placeholders})",
            (uid, *valid_ids)
        )

        # 2. 统一移交 InventoryService 资产发放
        from inventory_service import InventoryService
        InventoryService.grant_items(ctx or self.db, uid, raw_rewards, source="achievement")

        # 2.1 领奖触发即时成就自愈对齐（驱动如 Condition 312 贴纸收集等连环成就即时弹窗）
        try:
            self.reconcile_achievements(uid, ctx=ctx)
        except Exception as e:
            logger.warning(f"[achievement] 领奖后自愈对账异常: {e}")

        # 3. 汇总合并同类奖励，供 sc_53005.reward_list 与客户端弹窗展示
        merged_rewards = {}
        for iid, count in raw_rewards:
            merged_rewards[iid] = merged_rewards.get(iid, 0) + count

        formatted_rewards = [{"id": iid, "num": cnt} for iid, cnt in merged_rewards.items()]

        logger.info(f"[achievement] uid={uid} 成功领取成就奖励 ids={valid_ids} rewards={formatted_rewards}")
        return {
            "result": 0,
            "reward_list": formatted_rewards,
            "claimed_ids": valid_ids
        }

    # ------------------------------------------------------------------
    # 物语阅读 (Read Story cs_53006 -> sc_53007)
    # ------------------------------------------------------------------

    def read_story(self, uid, story_id, ctx=None):
        """
        读取/解锁成就剧情物语：
        校验成就点数 (货币33) 是否满足 unlock_point 门槛，持久化 users.extra
        """
        story_id = int(story_id)
        if story_id not in ACHIEVEMENT_STORIES:
            raise OperationError(2, f"无效的成就物语 ID: {story_id}")

        story_cfg = ACHIEVEMENT_STORIES[story_id]
        req_pt = story_cfg["unlock_point"]

        # 查询成就点数
        c_rows = self.db.query("SELECT num FROM currency WHERE uid=? AND id=33", (uid,))
        cur_pt = int(c_rows[0]["num"] or 0) if c_rows else 0
        if cur_pt < req_pt:
            raise OperationError(403, f"成就点数未达标 (当前:{cur_pt}, 需:{req_pt})")

        extra = _load_user_extra(self.db, uid)
        read_list = set(extra.get("achievement_story_read") or [])
        read_list.add(story_id)
        extra["achievement_story_read"] = sorted(list(read_list))
        _save_user_extra(self.db, uid, extra)

        logger.info(f"[achievement] uid={uid} 阅读成就物语 id={story_id} ({story_cfg['name']})")
        return {"result": 0}

    # ------------------------------------------------------------------
    # 全动态事件驱动引擎 (Runtime Dynamic Engine)
    # ------------------------------------------------------------------

    def trigger_condition(self, uid, condition, delta=1, param=None, set_value=False, ctx=None):
        """
        核心动态触发入口：
        根据 condition 和 parameter 过滤目标成就，原子更新进度，
        若产生变更则即时组装 sc_53003 差量包并挂载到 ctx.pending_frames。
        """
        _ensure_cfg()
        target_items = _CFG_BY_COND.get(int(condition), [])
        if not target_items or not self.db:
            return []

        now = int(time.time())
        diff_list = []

        # 查询当前未领奖成就的现有状态
        item_ids = [item["id"] for item in target_items]
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.db.query(
            f"SELECT achievement_id, progress, complete_flag, achieve_time FROM achievement WHERE uid=? AND achievement_id IN ({placeholders})",
            (uid, *item_ids)
        )
        db_state = {int(r["achievement_id"]): r for r in (rows or [])}

        for item in target_items:
            aid = item["id"]
            need = int(item.get("need") or 0)
            item_param = item.get("parameter") or []

            # 校验参数匹配度 (如等级档位、星级、阵营、指定道具等)
            if item_param and item_param != [0]:
                if param is None:
                    continue
                if isinstance(param, (list, tuple)):
                    if list(param) != list(item_param):
                        continue
                else:
                    if param not in item_param:
                        continue
            else:
                if param is not None and param != [0] and param != 0:
                    continue

            cur_row = db_state.get(aid)
            if cur_row and int(cur_row.get("complete_flag") or 0) == 1:
                # 已经领过奖的不再推进
                continue

            old_prog = int(cur_row.get("progress") or 0) if cur_row else 0
            old_achieve_ts = int(cur_row.get("achieve_time") or 0) if cur_row else 0

            if set_value:
                new_prog = int(delta)
            else:
                new_prog = old_prog + int(delta)

            # 进度不可倒退
            if new_prog < old_prog:
                continue

            # 进度发生变化或修复素材旧时间戳 (1786411503≈2026/08/11)
            if new_prog != old_prog or (new_prog >= need and old_achieve_ts in (0, 1786411503)):
                is_newly_finished = (new_prog >= need and old_prog < need)
                if new_prog >= need:
                    new_achieve_ts = now if (is_newly_finished or old_achieve_ts in (0, 1786411503)) else old_achieve_ts
                else:
                    new_achieve_ts = 0

                self.db.execute(
                    "UPDATE achievement SET progress=?, achieve_time=? WHERE uid=? AND achievement_id=?",
                    (new_prog, new_achieve_ts, uid, aid)
                )

                diff_list.append({
                    "id": aid,
                    "progress": new_prog,
                    "complete_flag": 0,
                    "achieve_time": new_achieve_ts
                })

        if diff_list and ctx:
            p_diff = encode("sc_53003", {"achievement_progress": diff_list})
            if p_diff:
                _append_frame_to_ctx(ctx, DownFrame(53003, p_diff))
                logger.info(f"[achievement] uid={uid} 触发 sc_53003 差量推送 cond={condition} count={len(diff_list)}")

        return diff_list

    def eval_draw_ten(self, uid, items, pool_id=0, ctx=None):
        """
        抽卡十连专属成就研判 (Condition 80001 ~ 80005)
        """
        if not items or len(items) < 10:
            return

        import draw_service as _ds

        # 整理抽卡 10 项产物的品阶与类型
        s_hero_cnt = 0
        a_hero_cnt = 0
        b_hero_cnt = 0
        hero_counts = {}
        race_counts = {}
        servant_counts = {}
        servant_5_cnt = 0
        servant_4_cnt = 0
        servant_3_cnt = 0

        s_set = set(_ds.STANDARD_S_HEROES + _ds.LIMITED_S_HEROES)
        a_set = set(_ds.ALL_A_HEROES)
        b_set = set(_ds.HERO_B_LIST)
        s5_set = set(_ds.SLEEPING_CHILDREN)
        s4_set = set(_ds.SERVANT_4_LIST)
        s3_set = set(_ds.SERVANT_3_LIST)

        for it in items:
            raw_id = int(it.get("id") or 0)
            # 碎片还原为本体 ID
            base_hid = raw_id - 10000 if raw_id > 10000 else raw_id

            if base_hid in s_set:
                s_hero_cnt += 1
                hero_counts[base_hid] = hero_counts.get(base_hid, 0) + 1
                r = _HERO_RACE_MAP.get(base_hid, 0)
                if r:
                    race_counts[r] = race_counts.get(r, 0) + 1
            elif base_hid in a_set:
                a_hero_cnt += 1
                hero_counts[base_hid] = hero_counts.get(base_hid, 0) + 1
                r = _HERO_RACE_MAP.get(base_hid, 0)
                if r:
                    race_counts[r] = race_counts.get(r, 0) + 1
            elif base_hid in b_set:
                b_hero_cnt += 1
                hero_counts[base_hid] = hero_counts.get(base_hid, 0) + 1
                r = _HERO_RACE_MAP.get(base_hid, 0)
                if r:
                    race_counts[r] = race_counts.get(r, 0) + 1

            if raw_id in s5_set:
                servant_5_cnt += 1
                servant_counts[raw_id] = servant_counts.get(raw_id, 0) + 1
            elif raw_id in s4_set or (2400000 <= raw_id < 2500000):
                servant_4_cnt += 1
                servant_counts[raw_id] = servant_counts.get(raw_id, 0) + 1
            elif raw_id in s3_set:
                servant_3_cnt += 1
                servant_counts[raw_id] = servant_counts.get(raw_id, 0) + 1

        # 80001: 初始超越
        if s_hero_cnt >= 2:
            self.trigger_condition(uid, 80001, delta=1, param=[2, 3], ctx=ctx)
        if a_hero_cnt >= 2:
            self.trigger_condition(uid, 80001, delta=1, param=[2, 2], ctx=ctx)
        if b_hero_cnt >= 2:
            self.trigger_condition(uid, 80001, delta=1, param=[2, 1], ctx=ctx)

        # 80002: 同角色
        if any(cnt >= 2 for cnt in hero_counts.values()):
            self.trigger_condition(uid, 80002, delta=1, param=[2], ctx=ctx)

        # 80003: 同神系阵营
        if any(cnt >= 2 for cnt in race_counts.values()):
            self.trigger_condition(uid, 80003, delta=1, param=[2, 0], ctx=ctx)
        if any(cnt >= 3 for cnt in race_counts.values()):
            self.trigger_condition(uid, 80003, delta=1, param=[3, 0], ctx=ctx)

        # 80004: 多钥从
        if servant_5_cnt >= 2:
            self.trigger_condition(uid, 80004, delta=1, param=[2, 5], ctx=ctx)
        if servant_4_cnt >= 2:
            self.trigger_condition(uid, 80004, delta=1, param=[2, 4], ctx=ctx)
        if servant_3_cnt >= 2:
            self.trigger_condition(uid, 80004, delta=1, param=[2, 3], ctx=ctx)

        # 80005: 同钥从
        if any(cnt >= 2 for cnt in servant_counts.values()):
            self.trigger_condition(uid, 80005, delta=1, param=[2], ctx=ctx)

    def on_user_daily_login(self, uid, ctx=None, now_ts=None):
        """
        每日登录处理：与账号真实累计登录天数对接（Condition 12），每日首次登录自动 +1 并推进
        """
        if not self.db or not uid:
            return
        now_ts = now_ts or int(time.time())
        now_date_str = time.strftime("%Y-%m-%d", time.localtime(now_ts))
        extra = _load_user_extra(self.db, uid)

        total_login_days = extra.get("total_login_days")
        if total_login_days is None:
            ach_rows = self.db.query("SELECT MAX(progress) as p FROM achievement WHERE uid=? AND condition=12", (uid,))
            base_days = int(ach_rows[0]["p"] or 0) if ach_rows else 0
            total_login_days = max(base_days, 251)

        last_login_date = extra.get("last_ach_login_date")
        if last_login_date != now_date_str:
            total_login_days += 1
            extra["total_login_days"] = total_login_days
            extra["last_ach_login_date"] = now_date_str
            _save_user_extra(self.db, uid, extra)
            logger.info(f"[achievement] uid={uid} 每日首次登录跨天，账号真实累计登录天数更新至 {total_login_days} 天")

        self.trigger_condition(uid, 12, delta=total_login_days, set_value=True, ctx=ctx)
        return total_login_days

    # ------------------------------------------------------------------
    # 静态快照自愈引擎 (State Reconciler)
    # ------------------------------------------------------------------

    def reconcile_achievements(self, uid, ctx=None):
        """
        全面扫描真实资产表，校准对齐成就进度并标记完成。
        确保历史导入、离线变动、GM 改库等漏项 100% 自动修复。
        同时支持实时上下文差量捕获，向 ctx 挂载 sc_53003 差量帧，驱动客户端即刻弹出金色成就 Banner。
        """
        _ensure_cfg()
        if not self.db or not _CFG:
            return []

        now = int(time.time())
        diff_list = []

        # 查询该玩家所有成就的现有状态，必要时自动初始化
        rows = self.db.query(
            "SELECT achievement_id, progress, complete_flag, achieve_time FROM achievement WHERE uid=?",
            (uid,)
        )
        if not rows or len(rows) < len(_CFG):
            self.init_user_achievements(uid)
            rows = self.db.query(
                "SELECT achievement_id, progress, complete_flag, achieve_time FROM achievement WHERE uid=?",
                (uid,)
            )

        # 洗净库中残留的素材历史假时间戳 (1786411503≈2026/08/11)
        self.db.execute("UPDATE achievement SET achieve_time=0 WHERE uid=? AND complete_flag=0 AND progress < need AND achieve_time > 0", (uid,))
        self.db.execute("UPDATE achievement SET achieve_time=? WHERE uid=? AND complete_flag=0 AND progress >= need AND achieve_time=1786411503", (now, uid))

        db_state = {int(r["achievement_id"]): dict(r) for r in (rows or [])}

        def update_item_progress(aid, new_val):
            cur_row = db_state.get(aid)
            if not cur_row:
                return
            if int(cur_row.get("complete_flag") or 0) == 1:
                return
            old_prog = int(cur_row.get("progress") or 0)
            old_achieve_ts = int(cur_row.get("achieve_time") or 0)
            need = int(_CFG[aid].get("need") or 0)
            new_prog = max(old_prog, int(new_val))
            if new_prog != old_prog or (new_prog >= need and old_achieve_ts in (0, 1786411503)):
                is_newly_finished = (new_prog >= need and old_prog < need)
                if new_prog >= need:
                    new_achieve_ts = now if (is_newly_finished or old_achieve_ts in (0, 1786411503)) else old_achieve_ts
                else:
                    new_achieve_ts = 0
                self.db.execute(
                    "UPDATE achievement SET progress=?, achieve_time=? WHERE uid=? AND achievement_id=?",
                    (new_prog, new_achieve_ts, uid, aid)
                )
                cur_row["progress"] = new_prog
                cur_row["achieve_time"] = new_achieve_ts
                diff_list.append({
                    "id": aid,
                    "progress": new_prog,
                    "complete_flag": 0,
                    "achieve_time": new_achieve_ts
                })

        # 1. 账号等级 (Condition 2)
        u_rows = self.db.query("SELECT level FROM users WHERE uid=?", (uid,))
        if u_rows and u_rows[0].get("level"):
            user_lv = int(u_rows[0]["level"])
            for item in _CFG_BY_COND.get(2, []):
                update_item_progress(item["id"], user_lv)

        # 2. 账号累计真实登录天数 (Condition 12)
        # 彻底解耦时迹馈赠活动(115天)，从 users.extra 维护真正的账号累计登录天数（基准 251 天，每日首次登录自动+1）
        extra = _load_user_extra(self.db, uid)
        total_login_days = extra.get("total_login_days")
        if total_login_days is None:
            ach_rows = self.db.query("SELECT MAX(progress) as p FROM achievement WHERE uid=? AND condition=12", (uid,))
            base_days = int(ach_rows[0]["p"] or 0) if ach_rows else 0
            total_login_days = max(base_days, 251)
            extra["total_login_days"] = total_login_days
            _save_user_extra(self.db, uid, extra)
        for item in _CFG_BY_COND.get(12, []):
            update_item_progress(item["id"], total_login_days)

        # 3. 贴纸收集数 (Condition 312)
        st_rows = self.db.query(
            "SELECT COUNT(DISTINCT item_id) as cnt FROM player_card WHERE uid=? AND kind='sticker'",
            (uid,)
        )
        st_cnt = int(st_rows[0]["cnt"] or 0) if st_rows else 0
        for item in _CFG_BY_COND.get(312, []):
            update_item_progress(item["id"], st_cnt)

        # 4. 芯片与智库猫咪 (Condition 260, 261)
        mimir_rows = self.db.query("SELECT COUNT(*) as cnt FROM chip WHERE uid=? AND cat='kernel' AND (unlocked=1 OR unlocked IS NULL)", (uid,))
        mimir_cnt = int(mimir_rows[0]["cnt"] or 0) if mimir_rows else 0
        for item in _CFG_BY_COND.get(260, []):
            update_item_progress(item["id"], mimir_cnt)

        chip_rows = self.db.query("SELECT COUNT(*) as cnt FROM chip WHERE uid=? AND cat!='kernel' AND (unlocked=1 OR unlocked IS NULL)", (uid,))
        chip_cnt = int(chip_rows[0]["cnt"] or 0) if chip_rows else 0
        for item in _CFG_BY_COND.get(261, []):
            update_item_progress(item["id"], chip_cnt)

        # 5. 历战空间 (Condition 441)
        tower_rows = self.db.query("SELECT stage FROM tower WHERE uid=?", (uid,))
        if tower_rows:
            cleared_stages = {int(r["stage"]) for r in tower_rows if r.get("stage")}
            for item in _CFG_BY_COND.get(441, []):
                aid = item["id"]
                param = item.get("parameter") or []
                need = int(item.get("need") or 10)
                if param:
                    req_stage = int(param[0])
                    if req_stage in cleared_stages or any((s % 1000) >= (req_stage % 1000) for s in cleared_stages):
                        update_item_progress(aid, need)

        # 6. 后宅设施与宿舍 (Condition 2001, 2004, 2006)
        # 餐厅设施总等级
        can_rows = self.db.query("SELECT SUM(level) as s FROM backhome_canteen_furniture WHERE uid=?", (uid,))
        can_lv = int(can_rows[0]["s"] or 0) if (can_rows and can_rows[0].get("s")) else 0
        for item in _CFG_BY_COND.get(2001, []):
            update_item_progress(item["id"], can_lv)

        # 宿舍解锁数
        dorm_rows = self.db.query("SELECT COUNT(*) as cnt FROM backhome_dorm WHERE uid=?", (uid,))
        dorm_cnt = int(dorm_rows[0]["cnt"] or 0) if dorm_rows else 0
        for item in _CFG_BY_COND.get(2004, []):
            update_item_progress(item["id"], dorm_cnt)

        # 后宅家具总数
        furn_rows = self.db.query("SELECT SUM(num) as s FROM backhome_furniture WHERE uid=?", (uid,))
        furn_cnt = int(furn_rows[0]["s"] or 0) if (furn_rows and furn_rows[0].get("s")) else 0
        for item in _CFG_BY_COND.get(2006, []):
            update_item_progress(item["id"], furn_cnt)

        # 7. 钥从超越 (Condition 210)
        srv_rows = self.db.query("SELECT stage FROM servant WHERE uid=?", (uid,))
        if srv_rows:
            for item in _CFG_BY_COND.get(210, []):
                param = item.get("parameter") or [5]
                target_stage = int(param[0]) if param else 5
                cnt = sum(1 for s in srv_rows if int(s.get("stage") or 0) >= target_stage)
                update_item_progress(item["id"], cnt)

        # 8. 拥有达标等级的刻印数 (Condition 200)
        eq_rows = self.db.query("SELECT id, prefab_id, exp, now_break_level FROM equip WHERE uid=?", (uid,))
        if eq_rows:
            from equip_service import EquipService
            eq_svc = EquipService.get_instance(db=self.db)
            eq_levels = []
            for e in eq_rows:
                pid = int(e.get("prefab_id") or 0)
                exp = int(e.get("exp") or 0)
                cfg = eq_svc.get_equip_cfg(pid, db=self.db)
                variant = cfg["exp_variant"] if cfg else 1
                lv, _, _ = eq_svc.calc_equip_level(variant, exp, db=self.db)
                eq_levels.append(lv)

            for item in _CFG_BY_COND.get(200, []):
                param = item.get("parameter") or []
                target_lv = int(param[0]) if param else 10
                cnt = sum(1 for elv in eq_levels if elv >= target_lv)
                update_item_progress(item["id"], cnt)

        # 8.1 拥有5星品质刻印数 (Condition 301)
        eq_5_rows = self.db.query("SELECT COUNT(*) as cnt FROM equip WHERE uid=? AND (prefab_id >= 500000 OR prefab_id LIKE '5%')", (uid,))
        eq_5_cnt = int(eq_5_rows[0]["cnt"] or 0) if eq_5_rows else 0
        for item in _CFG_BY_COND.get(301, []):
            param = item.get("parameter") or []
            if not param or param == [5]:
                update_item_progress(item["id"], eq_5_cnt)

        # 8.2 多维变量资产自愈 (Condition 300005, 300009, 300013, 300014)
        p_diff_rows = self.db.query("SELECT difficulty_id FROM polyhedron_difficulty WHERE uid=?", (uid,))
        p_diffs = set(int(r["difficulty_id"]) for r in (p_diff_rows or []))
        max_p_diff = max(p_diffs) if p_diffs else 0
        for item in _CFG_BY_COND.get(300005, []):
            param = item.get("parameter") or []
            if len(param) >= 2 and max_p_diff >= int(param[1]):
                update_item_progress(item["id"], 1)

        p_beacon_rows = self.db.query("SELECT beacon_id FROM polyhedron_beacon WHERE uid=?", (uid,))
        p_beacons = set(int(r["beacon_id"]) for r in (p_beacon_rows or []))
        for item in _CFG_BY_COND.get(300009, []):
            param = item.get("parameter") or []
            if len(param) >= 2 and int(param[1]) in p_beacons:
                update_item_progress(item["id"], int(item.get("need") or 1))

        p_art_rows = self.db.query("SELECT COUNT(*) as cnt FROM polyhedron_artifact WHERE uid=?", (uid,))
        p_art_cnt = int(p_art_rows[0]["cnt"] or 0) if p_art_rows else 0
        for item in _CFG_BY_COND.get(300013, []):
            param = item.get("parameter") or []
            threshold = int(param[1]) if len(param) >= 2 else int(item.get("need") or 1)
            if p_art_cnt >= threshold:
                update_item_progress(item["id"], 1)

        p_hero_rows = self.db.query("SELECT COUNT(*) as cnt FROM polyhedron_hero WHERE uid=?", (uid,))
        p_hero_cnt = int(p_hero_rows[0]["cnt"] or 0) if p_hero_rows else 0
        for item in _CFG_BY_COND.get(300014, []):
            param = item.get("parameter") or []
            threshold = int(param[2]) if len(param) >= 3 else (int(param[1]) if len(param) >= 2 else int(item.get("need") or 1))
            if p_hero_cnt >= threshold:
                update_item_progress(item["id"], 1)

        # 9. 修正者档案：累计送礼 (Condition 112) 与 心链事件 (Condition 121)
        arch_rows = self.db.query("SELECT archive_id, exp, gift_list, super_heart_link_list, video_list FROM hero_archive WHERE uid=?", (uid,))
        archive_exp_map = {}
        from archive_service import ArchiveService
        archive_svc = ArchiveService.get_instance()
        owned_archive_ids = set(archive_svc.owned_archive_ids(self.db, uid))
        if arch_rows:
            total_gifts = 0
            completed_heart_cnt = 0
            for ar in arch_rows:
                archive_id = int(ar.get("archive_id") or 0)
                # 历史库可能残留按战斗形态编号保存的影子行；自愈后保留作取证，
                # 成就统计只读取已拥有的规范档案行，避免礼物与角色数重复累计。
                if archive_id not in owned_archive_ids:
                    continue
                archive_exp_map[archive_id] = int(ar.get("exp") or 0)
                gl = ar.get("gift_list")
                if gl:
                    try:
                        g_arr = json.loads(gl) if isinstance(gl, str) else gl
                        if isinstance(g_arr, list):
                            for g in g_arr:
                                if isinstance(g, dict):
                                    total_gifts += int(g.get("num") or 0)
                    except Exception:
                        pass
                cfg = archive_svc.archives.get(archive_id) or {}
                plot_ids = {int(x) for x in cfg.get("plot_ids") or []}
                super_ids = [int(x) for x in cfg.get("super_plot_ids") or []]
                try:
                    vl_arr = json.loads(ar.get("video_list") or "[]")
                    viewed_plots = {int(x) for x in vl_arr if str(x).isdigit()} if isinstance(vl_arr, list) else set()
                except Exception:
                    viewed_plots = set()
                try:
                    sh_arr = json.loads(ar.get("super_heart_link_list") or "[]")
                    viewed_super = {
                        int(item.get("index") or 0)
                        for item in sh_arr
                        if isinstance(item, dict) and item.get("is_viewed")
                    } if isinstance(sh_arr, list) else set()
                except Exception:
                    viewed_super = set()
                normal_complete = bool(plot_ids) and plot_ids.issubset(viewed_plots)
                super_complete = all(index in viewed_super for index in range(1, len(super_ids) + 1))
                if normal_complete and super_complete:
                    completed_heart_cnt += 1

            for item in _CFG_BY_COND.get(112, []):
                update_item_progress(item["id"], total_gifts)

            for item in _CFG_BY_COND.get(121, []):
                update_item_progress(item["id"], completed_heart_cnt)

        # 10. 角色养成全量核心 (Condition 101, 102, 105, 106, 109, 110, 120, 160, 206, 212, 218)
        heroes = self.db.query(
            "SELECT id, level, star, break_level, skill_list, skill_intensify, weapon_exp, unlock_astrolabe, exclusive_skill_list, clear_times, trust_level "
            "FROM hero WHERE uid=? AND unlock=1",
            (uid,)
        )
        if heroes:
            import draw_service as _ds
            s_set = set(_ds.STANDARD_S_HEROES + _ds.LIMITED_S_HEROES)

            # 阵营计数与初始S计数
            race_hero_counts = {}
            init_s_cnt = 0
            for h in heroes:
                hid = int(h["id"])
                r = _HERO_RACE_MAP.get(hid, 0)
                if r:
                    race_hero_counts[r] = race_hero_counts.get(r, 0) + 1
                if hid in s_set:
                    init_s_cnt += 1

            # Condition 101: 阵营角色数
            for item in _CFG_BY_COND.get(101, []):
                param = item.get("parameter") or []
                if param:
                    target_race = int(param[0])
                    cnt = race_hero_counts.get(target_race, 0)
                    update_item_progress(item["id"], cnt)

            # Condition 102: 等级达标修正者数 (支持 10个>=90级等所有档位)
            for item in _CFG_BY_COND.get(102, []):
                param = item.get("parameter") or []
                if len(param) >= 2:
                    target_lv = int(param[1])
                    cnt = sum(1 for h in heroes if int(h.get("level") or 1) >= target_lv)
                    update_item_progress(item["id"], cnt)

            # Condition 105: 超越达标修正者数
            for item in _CFG_BY_COND.get(105, []):
                param = item.get("parameter") or []
                if len(param) >= 2:
                    target_star = int(param[1])
                    cnt = sum(1 for h in heroes if int(h.get("star") or 100) >= target_star)
                    update_item_progress(item["id"], cnt)

            # Condition 106: 技能等级之和达标修正者数
            def _hero_skill_sum(h):
                sl = h.get("skill_list")
                if not sl:
                    return 0
                try:
                    arr = json.loads(sl) if isinstance(sl, str) else sl
                    return sum(int(p[1]) for p in arr if isinstance(p, (list, tuple)) and len(p) >= 2)
                except Exception:
                    return 0

            hero_skill_sums = [_hero_skill_sum(h) for h in heroes]
            for item in _CFG_BY_COND.get(106, []):
                param = item.get("parameter") or []
                if len(param) >= 2:
                    target_sum = int(param[1])
                    cnt = sum(1 for s in hero_skill_sums if s >= target_sum)
                    update_item_progress(item["id"], cnt)

            # Condition 109: 一阶档案好感度达标数。同一本体的多个战斗形态只计一次。
            def _archive_love_lv(archive_id):
                a_exp = archive_exp_map.get(archive_id, 0)
                if a_exp >= 1000:
                    return 5
                elif a_exp >= 600:
                    return 4
                elif a_exp >= 300:
                    return 3
                elif a_exp >= 100:
                    return 2
                return 1

            archive_love_lvs = [
                _archive_love_lv(archive_id)
                for archive_id in owned_archive_ids
            ]
            for item in _CFG_BY_COND.get(109, []):
                param = item.get("parameter") or []
                if len(param) >= 2:
                    target_trust = int(param[1])
                    cnt = sum(1 for lv in archive_love_lvs if lv >= target_trust)
                    update_item_progress(item["id"], cnt)

            # Condition 110: 熟练度达标修正者数
            for item in _CFG_BY_COND.get(110, []):
                param = item.get("parameter") or []
                if len(param) >= 2:
                    target_clear = int(param[1])
                    cnt = sum(1 for h in heroes if int(h.get("clear_times") or 0) >= target_clear)
                    update_item_progress(item["id"], cnt)

            # Condition 120: 解锁全部神格修正者数
            def _hero_astrolabe_count(h):
                ua = h.get("unlock_astrolabe")
                if not ua:
                    return 0
                try:
                    arr = json.loads(ua) if isinstance(ua, str) else ua
                    return len(arr) if isinstance(arr, list) else 0
                except Exception:
                    return 0

            hero_astrolabes = [_hero_astrolabe_count(h) for h in heroes]
            for item in _CFG_BY_COND.get(120, []):
                cnt = sum(1 for c in hero_astrolabes if c >= 9)
                update_item_progress(item["id"], cnt)

            # Condition 160: 拥有修正者数 / 初始S数
            total_hero_cnt = len(heroes)
            for item in _CFG_BY_COND.get(160, []):
                param = item.get("parameter") or []
                is_init_s = (len(param) > 0 and int(param[0]) == 3)
                cnt = init_s_cnt if is_init_s else total_hero_cnt
                update_item_progress(item["id"], cnt)

            # Condition 206: 权钥等级达标修正者数
            def _weapon_exp_to_lv(exp):
                if exp >= 199800:
                    return 80
                if exp >= 99800:
                    return 60
                if exp >= 19800:
                    return 40
                if exp >= 4800:
                    return 20
                return 1

            hero_weapon_lvs = [_weapon_exp_to_lv(int(h.get("weapon_exp") or 0)) for h in heroes]
            for item in _CFG_BY_COND.get(206, []):
                param = item.get("parameter") or []
                target_wlv = int(param[0]) if param else 20
                cnt = sum(1 for wlv in hero_weapon_lvs if wlv >= target_wlv)
                update_item_progress(item["id"], cnt)

            # Condition 218: 技能属性强化等级之和达标修正者数 (拥有1/3/6/10位技能属性强化等级之和达到15/30/45/60级的修正者)
            def _hero_skill_intensify_sum(h):
                si_data = h.get("skill_intensify")
                if not si_data:
                    return 0
                if isinstance(si_data, str):
                    try:
                        si_data = json.loads(si_data)
                    except Exception:
                        return 0
                total_lv = 0
                if isinstance(si_data, list):
                    for item in si_data:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            total_lv += int(item[1] or 0)
                        elif isinstance(item, dict):
                            total_lv += int(item.get("level") or 0)
                elif isinstance(si_data, dict):
                    for v in si_data.values():
                        if isinstance(v, dict):
                            total_lv += int(v.get("level") or 0)
                        elif isinstance(v, (int, float)):
                            total_lv += int(v)
                return total_lv

            hero_intens_sums = [_hero_skill_intensify_sum(h) for h in heroes]
            for item in _CFG_BY_COND.get(218, []):
                param = item.get("parameter") or []
                if len(param) >= 2:
                    target_lv = int(param[1])
                    cnt = sum(1 for slv in hero_intens_sums if slv >= target_lv)
                    update_item_progress(item["id"], cnt)

            # Condition 212: 累计进行跃迁次数 (从所有英雄 exclusive_skill_list 统计累计跃迁点数自愈)
            def _hero_transition_points(h):
                from hero_codec import normalize_exclusive_skills
                norm = normalize_exclusive_skills(h.get("exclusive_skill_list"))
                return sum(int(v.get("talent_points") or 0) for v in norm.values())

            total_trans_pts = sum(_hero_transition_points(h) for h in heroes)
            for item in _CFG_BY_COND.get(212, []):
                update_item_progress(item["id"], total_trans_pts)

        # 差量帧推送：当存在新达成或进度推进的成就且传入 ctx 时，挂载 sc_53003 差量包
        if diff_list and ctx:
            p_diff = encode("sc_53003", {"achievement_progress": diff_list})
            if p_diff:
                _append_frame_to_ctx(ctx, DownFrame(53003, p_diff))
                logger.info(f"[achievement] uid={uid} reconcile_achievements 触发 sc_53003 差量推送 count={len(diff_list)}")

        return diff_list


# ======================================================================
# 协议控制器层 (Operations Controller Layer)
# ======================================================================

@operation
class AchievementRewardOp(Operation):
    """
    领取成就奖励：cs_53004 {id = [aid1, aid2, ...]} -> sc_53005 {result, reward_list}。
    支持单项领取与一键全领，前置下发 sc_17023 原子刷新货币 1/33。
    """
    cmd = 53004
    sc = 53005

    def validate(self, data, ctx):
        aid_data = data.get("id")
        if isinstance(aid_data, int):
            aid_list = [aid_data]
        elif isinstance(aid_data, (list, tuple)):
            aid_list = [int(x) for x in aid_data if int(x) > 0]
        else:
            aid_list = []
        data["_aid_list"] = aid_list
        return data

    def apply(self, data, ctx):
        aid_list = data["_aid_list"]
        svc = AchievementService.get_instance(ctx.db)
        return svc.claim_achievements(self.uid, aid_list, ctx=ctx)

    def respond(self, result, data, ctx):
        import generator as _gen
        out = []

        # 1. 优先下发变动货币的原子差量帧 sc_17023 (移转之辉 1 与成就点 33)
        if hasattr(ctx, "touched_items") and ctx.touched_items:
            try:
                p_17023 = _gen.gen_payload(17023, uid=self.uid, db=ctx.db, touched_items=ctx.touched_items)
                if p_17023:
                    out.append(DownFrame(17023, p_17023))
            except Exception as e:
                logger.warning(f"生成成就 sc_17023 异常: {e}")

        # 2. 响应业务确认帧 sc_53005
        p_53005 = ctx.codec_encode("sc_53005", {
            "result": 0,
            "reward_list": result.get("reward_list", [])
        }) or b"\x08\x00"
        out.append(DownFrame(self.sc, p_53005))

        ctx.log(f"cs_53004 -> 领取成就奖励 uid={self.uid} claimed={len(result.get('claimed_ids', []))}项")
        return out


@operation
class ReadAchievementStoryOp(Operation):
    """
    阅读成就物语：cs_53006 {id = story_id} -> sc_53007 {result}。
    点数门槛拦截，校验通过后持久化 users.extra["achievement_story_read"]。
    """
    cmd = 53006
    sc = 53007

    def validate(self, data, ctx):
        story_id = int(data.get("id") or 0)
        if story_id <= 0 or story_id not in ACHIEVEMENT_STORIES:
            raise OperationError(2, f"无效的成就物语 ID: {story_id}")
        data["_story_id"] = story_id
        return data

    def apply(self, data, ctx):
        story_id = data["_story_id"]
        svc = AchievementService.get_instance(ctx.db)
        return svc.read_story(self.uid, story_id, ctx=ctx)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_53007", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_53006 -> 阅读成就物语 uid={self.uid} story_id={data.get('_story_id')}")
        return [DownFrame(self.sc, payload)]


# ======================================================================
# 全局事件总线订阅器 (EventBus Subscriptions)
# ======================================================================

@bus.subscribe(Events.PLAYER_LEVEL_UP)
def _on_player_level_up(ctx, uid, **kwargs):
    new_lv = int(kwargs.get("new_lv") or 1)
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.trigger_condition(uid, 2, delta=new_lv, set_value=True, ctx=ctx)


@bus.subscribe(Events.STAGE_PASS)
def _on_stage_pass(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    times = int(kwargs.get("times") or 1)
    stage_id = int(kwargs.get("stage_id") or 0)
    ws = kwargs.get("win_stars")
    if isinstance(ws, (list, tuple)):
        win_stars = len([x for x in ws if x])
    else:
        win_stars = int(ws or 0)
    difficulty = int(kwargs.get("difficulty") or 0)

    # 梦境再构 3 星 (Condition 450, param=[diff, 3])
    if difficulty and win_stars >= 3:
        svc.trigger_condition(uid, 450, delta=1, param=[difficulty, 3], ctx=ctx)

    # 关卡指定首通/通关 (Condition 100004, param=[stage_id, die_count])
    if stage_id:
        svc.trigger_condition(uid, 100004, delta=1, param=[stage_id, 0], ctx=ctx)

    # 因子再构/关卡难度等级 (Condition 452, param=[level])
    lvl = int(kwargs.get("level") or kwargs.get("stage_level") or 0)
    if lvl > 0:
        for L in range(1, lvl + 1):
            svc.trigger_condition(uid, 452, delta=1, param=[L], ctx=ctx)

    # 矩阵探索 / 因果巡回 (Condition 495, 1010, 1011)
    if kwargs.get("stage_type") == "matrix" or kwargs.get("stage_kind") == "matrix":
        m_diff = int(kwargs.get("difficulty") or 1)
        svc.trigger_condition(uid, 495, delta=1, param=[m_diff], ctx=ctx)
        layers = int(kwargs.get("layers") or kwargs.get("tier") or 1)
        svc.trigger_condition(uid, 1010, delta=layers, ctx=ctx)
        m_score = int(kwargs.get("matrix_score") or kwargs.get("score") or 0)
        for sc_threshold in (7000, 10000):
            if m_score >= sc_threshold:
                svc.trigger_condition(uid, 1011, delta=1, param=[sc_threshold], ctx=ctx)

    # 扭曲梦境积分 (Condition 474, param=[diff, score])
    if kwargs.get("stage_kind") in ("twist_dream", "distortion_dream") or kwargs.get("stage_type") == "twist_dream":
        score = int(kwargs.get("score") or 0)
        diff = int(kwargs.get("difficulty") or 101)
        for s_diff, s_thresh in ((101, 23000), (102, 28000), (102, 50000)):
            if diff >= s_diff and score >= s_thresh:
                svc.trigger_condition(uid, 474, delta=1, param=[s_diff, s_thresh], ctx=ctx)

    # 黑区净化 (Condition 472, param=[0, tier, diff])
    if kwargs.get("stage_kind") in ("black_zone_normal", "black_zone_final") or kwargs.get("stage_type") == "black_zone":
        tier = int(kwargs.get("tier") or 1)
        diff = int(kwargs.get("difficulty") or 1)
        svc.trigger_condition(uid, 472, delta=1, param=[0, tier, diff], ctx=ctx)

    # 失序深阱通关 (Condition 491, param=[tier])
    if kwargs.get("stage_kind") in ("disorder_abyss", "abyss") or kwargs.get("stage_type") in ("disorder_abyss", "abyss"):
        tier = int(kwargs.get("tier") or kwargs.get("difficulty") or 1)
        for t in range(1, tier + 1):
            svc.trigger_condition(uid, 491, delta=1, param=[t], ctx=ctx)

    # 物资副本 (Condition 1010)
    if 101000 <= stage_id < 102000 or (kwargs.get("stage_type") == "material"):
        svc.trigger_condition(uid, 1010, delta=1, ctx=ctx)

    # 迷之隙 (Condition 310002)
    if stage_id == 11109 or kwargs.get("stage_type") == "labyrinth":
        svc.trigger_condition(uid, 310002, delta=1, param=[11109], ctx=ctx)

    # 因果残片 (Condition 329001)
    if kwargs.get("fragment_id"):
        svc.trigger_condition(uid, 329001, delta=1, param=[int(kwargs.get("fragment_id"))], ctx=ctx)

    # 局内战斗战报数据消费 (b_info)
    b_info = kwargs.get("b_info") or {}
    if b_info:
        # 100001: 无伤通关 (injured_num == 0)
        if b_info.get("injured_num") == 0 and b_info.get("battle_time"):
            svc.trigger_condition(uid, 100001, delta=1, ctx=ctx)

        # 100002: 队长不受伤害获胜 (param=[0]) 与 指定关卡无伤通关 (param=[stage_id])
        if b_info.get("injured_num") == 0:
            svc.trigger_condition(uid, 100002, delta=1, param=[0], ctx=ctx)
            if stage_id:
                svc.trigger_condition(uid, 100002, delta=1, param=[stage_id], ctx=ctx)

        # 100003: 残血通关 (全队血量 <= 10%, param=[0, 10])
        hp_p = b_info.get("hp_percent")
        if hp_p is not None:
            if hp_p <= 10:
                svc.trigger_condition(uid, 100003, delta=1, param=[0, 10], ctx=ctx)
            if hp_p <= 5:
                svc.trigger_condition(uid, 100003, delta=1, param=[0, 5], ctx=ctx)
            if hp_p <= 2:
                svc.trigger_condition(uid, 100003, delta=1, param=[0, 2], ctx=ctx)

        # 100005: 修正模式次数 (param=[5])
        if int(b_info.get("modify_times") or 0) >= 5:
            svc.trigger_condition(uid, 100005, delta=1, param=[5], ctx=ctx)

        # 100006: 10秒内进修正模式 (param=[10])
        if int(b_info.get("battle_time") or 999) <= 10:
            svc.trigger_condition(uid, 100006, delta=1, param=[10], ctx=ctx)

        # 100008: 仅队长存活/全队阵亡只剩1人
        if b_info.get("only_captain_alive") or b_info.get("die_heroes_count") == 2:
            svc.trigger_condition(uid, 100008, delta=1, ctx=ctx)

        # 100009: 造成6/7/8种属性伤害 (param=[6], [7], [8])
        elem_k = int(b_info.get("element_kinds") or 0)
        for req_k in (6, 7, 8):
            if elem_k >= req_k:
                svc.trigger_condition(uid, 100009, delta=1, param=[req_k], ctx=ctx)

        # 100010: 单次伤害突破
        max_dmg = int(b_info.get("max_damage") or 0)
        for threshold in (10000, 50000, 150000, 300000, 1000000, 2000000):
            if max_dmg >= threshold:
                svc.trigger_condition(uid, 100010, delta=1, param=[threshold], ctx=ctx)

        # 499: 主线21章死兆之女召唤的幻影击杀 (param=[1032123, 404701, kill_count])
        phantom_kills = int(b_info.get("phantom_kills") or kwargs.get("phantom_kills") or 0)
        if phantom_kills > 0:
            for p_k in (1, 3, 5, 7, 8, 13):
                if phantom_kills >= p_k:
                    svc.trigger_condition(uid, 499, delta=1, param=[1032123, 404701, p_k], ctx=ctx)

        # 120001: 累计进入零时空间时间 (秒)
        zero_t = int(b_info.get("zero_time") or 0)
        if zero_t > 0:
            svc.trigger_condition(uid, 120001, delta=zero_t, ctx=ctx)

        # 120002: 累计极限闪避次数
        ext_d = int(b_info.get("extreme_dodge") or 0)
        if ext_d > 0:
            svc.trigger_condition(uid, 120002, delta=ext_d, ctx=ctx)

        # 120003: 战斗处于修正模式时间累计 (秒)
        mod_t = int(b_info.get("modify_total_time") or 0)
        if mod_t > 0:
            svc.trigger_condition(uid, 120003, delta=mod_t, ctx=ctx)

        # 120004..120030: 指定神系触发修正模式 (真樱 120004, 尼罗 120005, 圣树 120006, 众星 120007, 奥山 120008, 天垣 120030)
        race_to_cond = {
            1: 120008,  # 奥山
            2: 120005,  # 尼罗
            3: 120004,  # 真樱
            4: 120006,  # 圣树
            5: 120007,  # 众星
            9: 120030,  # 天垣
        }
        if b_info.get("race_modify"):
            for r_key, r_cnt in b_info["race_modify"].items():
                val = int(r_cnt or 0)
                if val <= 0:
                    continue
                r_int = int(r_key)
                if r_int in race_to_cond:
                    svc.trigger_condition(uid, race_to_cond[r_int], delta=val, ctx=ctx)
                elif r_int in (120004, 120005, 120006, 120007, 120008, 120030):
                    svc.trigger_condition(uid, r_int, delta=val, ctx=ctx)

        # 120009: 累计打出100连击
        combo_100 = int(b_info.get("combo_100_times") or (1 if int(b_info.get("combo_max") or 0) >= 100 else 0))
        if combo_100 > 0:
            svc.trigger_condition(uid, 120009, delta=combo_100, ctx=ctx)

        # 120010: 累计闪避次数
        d_times = int(b_info.get("dodge_times") or 0)
        if d_times > 0:
            svc.trigger_condition(uid, 120010, delta=d_times, ctx=ctx)

        # 120011: 累计打出连携奥义次数
        coop_u = int(b_info.get("coop_ultimate_times") or b_info.get("combo_ultimate") or 0)
        if coop_u > 0:
            svc.trigger_condition(uid, 120011, delta=coop_u, ctx=ctx)

        # 120012 ~ 120016: 状态异常 (灼烧/冰冻/禁锢/眩晕/破甲)
        for cond_id, key in ((120012, "burn_times"), (120013, "freeze_times"), (120014, "imprison_times"),
                             (120015, "stun_times"), (120016, "break_times")):
            val = int(b_info.get(key) or 0)
            if val > 0:
                svc.trigger_condition(uid, cond_id, delta=val, ctx=ctx)

        # 120018: 零时空间中击杀
        zt_k = int(b_info.get("zero_time_kills") or 0)
        if zt_k > 0:
            svc.trigger_condition(uid, 120018, delta=zt_k, ctx=ctx)

        # 120019: 零时空间进入修正模式
        zt_m = int(b_info.get("zero_time_modify") or 0)
        if zt_m > 0:
            svc.trigger_condition(uid, 120019, delta=zt_m, ctx=ctx)

        # 120021: 全队不使用技能获胜
        if b_info.get("no_skill_win"):
            svc.trigger_condition(uid, 120021, delta=times, ctx=ctx)

        # 120022: 纯远程角色通关
        if b_info.get("range_only"):
            svc.trigger_condition(uid, 120022, delta=times, ctx=ctx)

        # 120023: 纯近战角色通关
        if b_info.get("melee_only"):
            svc.trigger_condition(uid, 120023, delta=times, ctx=ctx)

        # 120024: 单人通关
        if b_info.get("solo_pass"):
            svc.trigger_condition(uid, 120024, delta=times, ctx=ctx)

        # 120025: 累计治疗量
        heal_v = int(b_info.get("heal_amount") or 0)
        if heal_v > 0:
            svc.trigger_condition(uid, 120025, delta=heal_v, ctx=ctx)

        # 120026: 同神系角色通关
        if b_info.get("same_race_pass"):
            svc.trigger_condition(uid, 120026, delta=times, ctx=ctx)

        # 120017: 使敌人进入受创状态
        wound_times = int(b_info.get("wound_times") or b_info.get("injured_state_times") or 0)
        if wound_times > 0:
            svc.trigger_condition(uid, 120017, delta=wound_times, ctx=ctx)

        # 120020: 单次修正空间达到22秒
        max_mod_t = int(b_info.get("max_modify_time") or b_info.get("single_modify_time") or 0)
        if max_mod_t >= 22:
            svc.trigger_condition(uid, 120020, delta=1, ctx=ctx)

        # 120027: 三位Ω角色出战通关
        if b_info.get("all_omega_pass") or b_info.get("all_omega"):
            svc.trigger_condition(uid, 120027, delta=times, ctx=ctx)

        # 120028: 装配全部神格的角色获胜
        if b_info.get("full_astrolabe_win") or b_info.get("all_astrolabe_win"):
            svc.trigger_condition(uid, 120028, delta=1, ctx=ctx)

        # 120029: 含有同角色/同名角色通关
        if b_info.get("same_hero_pass"):
            svc.trigger_condition(uid, 120029, delta=times, ctx=ctx)


@bus.subscribe(Events.STAGE_FIRST_CLEAR)
def _on_stage_first_clear(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    stage_id = int(kwargs.get("stage_id") or 0)
    difficulty = int(kwargs.get("difficulty") or 0)
    win_stars = int(kwargs.get("win_stars") or 0)
    if difficulty and win_stars >= 3:
        svc.trigger_condition(uid, 450, delta=1, param=[difficulty, 3], ctx=ctx)
    if stage_id:
        svc.trigger_condition(uid, 100004, delta=1, param=[stage_id, 0], ctx=ctx)


@bus.subscribe(Events.HERO_UPGRADE)
def _on_hero_upgrade(ctx, uid, **kwargs):
    # 战斗熟练度与一阶好感在同一事务内变动，已由专用档案事件统一对账。
    if kwargs.get("oper") == "proficiency_up":
        return
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    # 角色升级/突破触发全量养成快照自愈比对，传入 ctx 挂载 sc_53003 差量包
    svc.reconcile_achievements(uid, ctx=ctx)
    if kwargs.get("oper") == "transition_upgrade":
        svc.trigger_condition(uid, 212, delta=1, ctx=ctx)


@bus.subscribe(Events.HERO_ARCHIVE_EXP_CHANGE)
def _on_hero_archive_exp_change(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.STORY_READ)
def _on_archive_story_read(ctx, uid, **kwargs):
    if not kwargs.get("archive_id"):
        return
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.HERO_UNLOCK)
def _on_hero_unlock(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.TRUST_LEVEL_UP)
def _on_trust_level_up(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.DRAW_PERFORM)
def _on_draw_perform(ctx, uid, **kwargs):
    times = int(kwargs.get("draw_count") or kwargs.get("times") or 1)
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    # Condition 700: 累计探测次数 (param=[0])
    svc.trigger_condition(uid, 700, delta=times, param=[0], ctx=ctx)

    # 探测获得五星钥从检测 (Condition 780: 严格限定各神系5星沉睡之子，杜绝3星与4星杂钥从冒领)
    import draw_service as _ds
    sleeping_set = set(_ds.SLEEPING_CHILDREN)
    sleeping_race_map = {
        2510000: 1,  # 奥山
        2520000: 2,  # 尼罗
        2530000: 3,  # 真樱
        2540000: 4,  # 圣树
        2550000: 5,  # 众星
        2590000: 9,  # 天垣
    }
    items = kwargs.get("items") or []
    for it in items:
        raw_id = int(it.get("id") or 0)
        # 严密过滤：必须属于6大阵营五星沉睡之子 (或 25 开头的五星钥从)
        if raw_id in sleeping_set or (2500000 <= raw_id < 2600000):
            # 13501..13503: 获得任意五星钥从 (param=[0])
            svc.trigger_condition(uid, 780, delta=1, param=[0], ctx=ctx)
            # 各神系专属五星钥从 (param=[race])
            race = sleeping_race_map.get(raw_id) or ((raw_id // 10000) % 10)
            if race > 0:
                svc.trigger_condition(uid, 780, delta=1, param=[race], ctx=ctx)


@bus.subscribe(Events.DRAW_TEN_RESULT)
def _on_draw_ten_result(ctx, uid, **kwargs):
    items = kwargs.get("items") or []
    pool_id = kwargs.get("pool_id") or 0
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.eval_draw_ten(uid, items, pool_id=pool_id, ctx=ctx)


@bus.subscribe(Events.DORM_ACTION)
def _on_dorm_action(ctx, uid, **kwargs):
    action_type = kwargs.get("action_type")
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    if action_type == "furniture":
        svc.trigger_condition(uid, 2005, delta=1, ctx=ctx)
        svc.trigger_condition(uid, 2006, delta=1, ctx=ctx)
        svc.reconcile_achievements(uid, ctx=ctx)
    elif action_type in ("commission", "commission_submit"):
        svc.trigger_condition(uid, 2002, delta=1, ctx=ctx)
    elif action_type in ("cook", "feed"):
        svc.trigger_condition(uid, 2003, delta=1, ctx=ctx)
    elif action_type == "dorm_unlock":
        svc.trigger_condition(uid, 2004, delta=1, ctx=ctx)
        svc.reconcile_achievements(uid, ctx=ctx)
    elif action_type == "canteen":
        svc.trigger_condition(uid, 2001, delta=1, ctx=ctx)
        svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.DORM_GIFT)
def _on_dorm_gift(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.trigger_condition(uid, 2003, delta=1, ctx=ctx)


@bus.subscribe(Events.CHIP_UNLOCK)
def _on_chip_unlock(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.trigger_condition(uid, 260, delta=1, ctx=ctx)
    svc.trigger_condition(uid, 261, delta=1, ctx=ctx)
    svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.CHIP_EQUIP)
def _on_chip_equip(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.trigger_condition(uid, 261, delta=1, ctx=ctx)


@bus.subscribe(Events.EQUIP_REFORGE)
def _on_equip_reforge(ctx, uid, **kwargs):
    oper = kwargs.get("oper")
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    if oper == "enchant":
        svc.trigger_condition(uid, 204, delta=1, ctx=ctx)
    elif oper == "race_refresh":
        svc.trigger_condition(uid, 205, delta=1, ctx=ctx)
    elif oper == "reconstruct_hero":
        svc.trigger_condition(uid, 211, delta=1, ctx=ctx)
    elif oper in ("upgrade", "breakthrough"):
        svc.reconcile_achievements(uid, ctx=ctx)
    else:
        svc.trigger_condition(uid, 205, delta=1, ctx=ctx)
        svc.trigger_condition(uid, 211, delta=1, ctx=ctx)
        svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.USER_LOGIN)
def _on_user_login(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.on_user_daily_login(uid, ctx=ctx, now_ts=kwargs.get("now_ts"))
    svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.EQUIP_OBTAIN)
def _on_equip_obtain(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    prefab_id = int(kwargs.get("prefab_id") or 0)
    count = int(kwargs.get("count") or 1)
    star = int(kwargs.get("star") or 0)
    # Condition 301: 获得5星品质刻印 (param=[5])
    if star == 5 or (500000 <= prefab_id < 600000) or str(prefab_id).startswith("5"):
        svc.trigger_condition(uid, 301, delta=count, param=[5], ctx=ctx)


@bus.subscribe(Events.POLYHEDRON_PASS)
def _on_polyhedron_pass(ctx, uid, **kwargs):
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    difficulty = int(kwargs.get("difficulty") or 1)
    for d in range(1, difficulty + 1):
        svc.trigger_condition(uid, 300005, delta=1, param=[100001, d], ctx=ctx)
    terminals = kwargs.get("terminals") or []
    for t_id in terminals:
        svc.trigger_condition(uid, 300006, delta=1, param=[100001, int(t_id)], ctx=ctx)
    beacons = kwargs.get("beacons") or []
    for b_id in beacons:
        svc.trigger_condition(uid, 300009, delta=3, param=[100001, int(b_id)], ctx=ctx)
    artifacts = kwargs.get("artifacts") or []
    if len(artifacts) >= 10:
        svc.trigger_condition(uid, 300013, delta=1, param=[100001, 10], ctx=ctx)
    heroes = kwargs.get("heroes") or []
    if len(heroes) >= 20:
        svc.trigger_condition(uid, 300014, delta=1, param=[100001, 0, 20], ctx=ctx)
    if kwargs.get("beacon_kill"):
        svc.trigger_condition(uid, 300012, delta=1, param=[0, 0, 5], ctx=ctx)
    svc.reconcile_achievements(uid, ctx=ctx)


@bus.subscribe(Events.POLYHEDRON_TACTIC_KILL)
def _on_polyhedron_tactic_kill(ctx, uid, **kwargs):
    """
    虚构推演战术击杀预留订阅 (Condition 300011):
    tactic_type: 1..6 (对应各战术词缀)
    delta / kill_count: 击杀数累加 (默认 1)
    """
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    tactic_type = int(kwargs.get("tactic_type") or 1)
    count = int(kwargs.get("kill_count") or kwargs.get("delta") or 1)
    svc.trigger_condition(uid, 300011, delta=count, param=[0, tactic_type], ctx=ctx)


@bus.subscribe(Events.DISORDER_ABYSS_PASS)
def _on_disorder_abyss_pass(ctx, uid, **kwargs):
    """
    失序深阱通关预留订阅 (Condition 491):
    tier / level: 1..6 阶层
    """
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    tier = int(kwargs.get("tier") or kwargs.get("level") or 1)
    for t in range(1, tier + 1):
        svc.trigger_condition(uid, 491, delta=1, param=[t], ctx=ctx)


@bus.subscribe(Events.SUMMER_PARK_ACTION)
def _on_summer_park_action(ctx, uid, **kwargs):
    """
    盛夏乐园活动专属预留订阅 (Condition 303):
    param=[1]
    """
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    svc.trigger_condition(uid, 303, delta=1, param=[1], ctx=ctx)


@bus.subscribe(Events.STORY_BRANCH_EXPLORE)
def _on_story_branch_explore(ctx, uid, **kwargs):
    """
    主线19章剧情多分支与记忆碎片预留订阅 (Condition 801, 802):
    - memory_count / fragment_count: 碎片收集数量累加 (Condition 801, need=30)
    - stage_id: 分支关卡ID (Condition 802)
    - branch_id: 1, 2, 3 或 50119, 50120, 50121
    """
    svc = AchievementService.get_instance(getattr(ctx, "db", None))
    mem_cnt = int(kwargs.get("memory_count") or kwargs.get("fragment_count") or 0)
    if mem_cnt > 0:
        svc.trigger_condition(uid, 801, delta=mem_cnt, param=None, ctx=ctx)

    stage_id = int(kwargs.get("stage_id") or 0)
    if stage_id > 0:
        svc.trigger_condition(uid, 802, delta=1, param=stage_id, ctx=ctx)

    stages = kwargs.get("stage_list") or kwargs.get("stages") or []
    for sid in stages:
        svc.trigger_condition(uid, 802, delta=1, param=int(sid), ctx=ctx)

    branch_id = int(kwargs.get("branch_id") or 0)
    if branch_id > 0:
        for it in _CFG_BY_COND.get(802, []):
            if it["id"] == branch_id or branch_id == (it["id"] - 50118):
                for sid in (it.get("parameter") or []):
                    svc.trigger_condition(uid, 802, delta=1, param=int(sid), ctx=ctx)
