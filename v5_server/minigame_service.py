# -*- coding: utf-8 -*-
"""
minigame_service.py — 常驻小游戏服务中枢 (MiniGame Service)

集中纳管《深空之眼》全量常驻/轻量小游戏（海拉弹珠、卡达斯假日赛·坦克竞速、灰烬牛仔等）：
- 模块化纳管局外成长进度持久化、局内通关结算、技能/装备装配与挑战最高分记录；
- 统筹调用 inventory_service 统一资产/道具出入库并保持差量追踪；
- 触发全局 EventBus 广播（STAGE_PASS, STAGE_FIRST_CLEAR 等），与任务系统（task_listener）无缝联动；
- 驱动登录洪流动态帧生成（sc_89401, sc_84331 等）。
"""

import os
import json
import time
import logging

from core import Operation, OperationError, operation
from middleware import DownFrame
import event_bus
from event_bus import bus, Events
from inventory_service import InventoryService
from minigame_point_service import rewards_for as _native_point_rewards, ActivityPointClaimOp as _ActivityPointClaimOp

logger = logging.getLogger("minigame_service")

# ---------------- 1. 远村异闻 (海拉弹珠, Activity 3840801) 元数据 ----------------
from pinball_service import STAGES as PINBALL_STAGES

# ---------------- 2. 卡达斯假日赛 (夏活坦克竞速, Activity 4343601) 元数据 ----------------
# 关卡对应的战车配件解锁（1=武器, 2=装备, 3=车体），用于战车改装及常驻任务进度推进（非外部背包资产）
# Stage rewards may unlock several weapon/equipment/body entries.
from kadas_race_service import STAGES as SUMMER_RACE_STAGES

# ---------------- 3. 列车大劫案 (灰烬牛仔射击, Activity 3639701) 元数据 ----------------
from ash_service import REAL as ASH_REAL_TO_STAGE
ASH_STAGE_TO_REAL = {v: k for k, v in ASH_REAL_TO_STAGE.items()}

# 灰烬牛仔同调率积分奖励 (363970101 ~ 363970110)
ASH_POINT_REWARDS = _native_point_rewards(3639701)

# ---------------- 4. 战术研习记录 (奥西里斯研习, Activity 323801) 元数据 ----------------
from osiris_service import CHAPTERS as _OSIRIS_CHAPTERS
OSIRIS_CHAPTER_STAGES = {i:c['stages'] for i,c in _OSIRIS_CHAPTERS.items() if c['type']==1}
OSIRIS_STAGE_TO_CHAPTER = {s:i for i,stages in OSIRIS_CHAPTER_STAGES.items() for s in stages}

# 奥西里斯同调率积分奖励 (32380101 ~ 32380118, 每档 20 钻石)
OSIRIS_POINT_REWARDS = _native_point_rewards(323801)

# ---------------- 5. 溯梦之隙 (斯克尔德心象, Activity 321201) 元数据 ----------------
# 36 关全量关卡配置 (reward: drop_id, type: 1战斗/2拼图, param: 实际战斗关卡ID或拼图关卡ID)
from skuld_service import NODES as SKULD_STAGES, REAL as SKULD_BATTLE_TO_STAGE

# 斯克尔德心象信赖度大奖 (32120101 ~ 32120110, 每档 30 钻石 + 专属音乐/名片)
SKULD_POINT_REWARDS = _native_point_rewards(321201)

# ---------------- 6. 谧光叙录 (霍德尔专属活动, Activity 3941101) 元数据 ----------------
HODUR_STAGE_TO_CHAPTER = {
    5310220: 1, 5310219: 1,
    5310213: 2, 5310211: 2, 5310218: 2,
    5310201: 3, 5310202: 3, 5310221: 3,
    5310203: 4, 5310204: 4, 5310205: 4, 5310206: 4,
}

# ---------------- 7. 潜质觉醒 (现时无返之途·薇儿丹蒂 SP, Activity 242841/242851/242871) 元数据 ----------------
SP_HERO_ACTIVITY_ID = 242841
SP_HERO_BOSS_ACTIVITY_ID = 242851
SP_HERO_PUZZLE_ACTIVITY_ID = 242871
SP_HERO_BARBECUE_ACTIVITY_ID = 242861

SP_HERO_STORY_STAGES = [5170201, 5170202, 5170203, 5170204, 5170205, 5170206]
SP_HERO_TRAIN_STAGES = {
    5170211: 2, 5170212: 2, 5170213: 2,  # 基础训练组
    5170214: 3, 5170215: 3, 5170216: 3,  # 进阶训练组
    5170217: 4, 5170218: 4, 5170219: 4,  # 特殊训练组
}
SP_HERO_BOSS_STAGES = [5170221, 5170222]

# ---------------- 7. 极限机装 (乌尔碰碰车, Activity 4243101) 元数据 ----------------
VEHICLE_BALL_ACTIVITY_ID = 4243101
VEHICLE_BALL_LIMITED_TASK_ACTIVITY_ID = 4200401
VEHICLE_BALL_STAGES = [50101, 50102, 50103, 50104]
VEHICLE_BALL_VEHICLES = [50111, 50112, 50113]

# ---------------- 9. 浮光绎曲与鸣律探微 (常驻音律演奏会与夏日小游戏, Activity 3814801 / 283041) 元数据 ----------------
RESIDENT_MUSIC_ACTIVITY_ID = 3814801
RESIDENT_MUSIC_TASK_ACTIVITY_ID = 3800402

# ---------------- 10. 食与异世界 (夏日餐厅经营, Activity 3539101) 元数据 ----------------
from summer_pub_service import ACTIVITY as SUMMER_PUB_ACTIVITY_ID, REAL as SUMMER_PUB_BATTLE_TO_LEVEL



class MiniGameService:
    """常驻小游戏领域服务单例"""
    _instance = None

    def __init__(self, db=None):
        self.db = db

    @classmethod
    def get_instance(cls, db=None):
        if db is not None:
            return cls(db=db)
        if cls._instance is None or cls._instance.db is not None:
            cls._instance = cls()
        return cls._instance

    def _resolve_db_uid(self, arg1, arg2=None):
        if isinstance(arg1, (int, str)) and not hasattr(arg1, "query"):
            uid = int(arg1)
            db = arg2 or self.db
        else:
            db = arg1 or self.db
            uid = int(arg2) if arg2 is not None else 2174928301
        return db, uid

    # ==================== 1. 远村异闻 · 海拉弹珠 (Activity 3840801) ====================

    def get_or_init_pinball(self, db, uid, activity_id=3840801):
        from pinball_service import read
        return read(db or self.db, uid, activity_id)

    def build_89401_payload(self, db, uid, activity_id=3840801):
        from codec import encode
        from pinball_service import read
        data = read(db or self.db, uid, activity_id)
        return encode("sc_89401", dict(hai_la_level=data["stage_count"], stage_list=data["cleared_stage_list"],
            skills=data["unlock_skill_list"], select_active_skills=data["equip_skill_list"], challenge_score=data["score"]))

    def settle_pinball_level(self, ctx, uid, stage_id, score, harm):
        from pinball_service import settle
        return settle(ctx, uid, stage_id, score, harm)

    def equip_pinball_skills(self, ctx, uid, activity_id, skill_list):
        from pinball_service import equip
        return equip(ctx, uid, activity_id, skill_list)

    # ==================== 2. 卡达斯假日赛 · 夏活坦克竞速 (Activity 4343601) ====================

    def get_or_init_kadas_race(self, db, uid, activity_id=4343601):
        from kadas_race_service import read, require, ACTIVITY
        require(activity_id == ACTIVITY, "未知卡达斯活动")
        return read(db or self.db, uid)

    def build_84331_payload(self, db, uid, activity_id=4343601):
        from codec import encode
        from kadas_race_service import payload, require, ACTIVITY
        require(activity_id == ACTIVITY, "未知卡达斯活动")
        return encode("sc_84331", payload(db or self.db, uid))

    def settle_race_stage(self, ctx, uid, stage_id, result, round_num=0, score=0, kill_num=0, activity_id=4343601):
        from kadas_race_service import settle
        return settle(ctx, uid, activity_id, stage_id, result, round_num, score, kill_num)

    def modify_tank_build(self, ctx, uid, tank_id, weapon_list, activity_id=4343601):
        from kadas_race_service import modify
        return modify(ctx, uid, activity_id, tank_id, weapon_list)

    # ==================== 3. 列车大劫案 · 灰烬牛仔 (Activity 3639701) ====================

    def get_ash_shoot_stages(self, db, uid):
        from ash_service import payload
        return payload(db or self.db, uid)['pass_stages']

    def build_68181_payload(self, db, uid):
        from ash_service import payload
        from codec import encode
        return encode('sc_68181', payload(db or self.db, uid))

    def build_60105_payload(self, db, uid):
        """生成 sc_60105 (常驻积分活动分值汇总) 下行 payload"""
        from codec import encode
        db = db or self.db
        from ash_service import mirror_point
        mirror_point(db, uid)
        from osiris_service import sync as sync_osiris
        sync_osiris(db, uid)
        rows = db.query(
            "SELECT activity_id, point FROM activity_point WHERE uid=?",
            (uid,)
        )
        score_info_list = [{"key": int(r["activity_id"]), "score": int(r["point"] or 0)} for r in (rows or [])]
        try:
            return encode("sc_60105", {"score_info_list": score_info_list})
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_60105 失败: {e}")
            return None

    def build_60107_payload(self, db, uid):
        """生成 sc_60107 (常驻积分奖励领奖记录) 下行 payload"""
        from codec import encode
        db = db or self.db
        rows = db.query(
            "SELECT reward_id FROM activity_point_reward WHERE uid=?",
            (uid,)
        )
        reward_info_list = [int(r["reward_id"]) for r in (rows or [])]
        try:
            return encode("sc_60107", {"reward_info_list": reward_info_list})
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_60107 失败: {e}")
            return None

    def settle_ash_stage(self, ctx, uid, real_stage_id, *, battle_info=None, win=True):
        from ash_service import settle, ACTIVITY
        return settle(ctx, uid, real_stage_id, ACTIVITY, 1 if win else 2, battle_info or {})

    def claim_point_rewards(self, ctx, uid, reward_id_list):
        from minigame_point_service import claim
        return claim(ctx, uid, reward_id_list)

    # ==================== 4. 战术研习记录 · 奥西里斯研习 (Activity 323801) ====================

    def get_osiris_stages(self, db, uid):
        from osiris_service import payload
        return payload(db or self.db, uid)['pass_stages']

    def get_osiris_chapters(self, db, uid):
        from osiris_service import payload
        return payload(db or self.db, uid)['pass_chapters']

    def build_68171_payload(self, db, uid):
        from osiris_service import payload
        from codec import encode
        return encode('sc_68171', payload(db or self.db, uid))

    # ==================== 5. 溯梦之隙 · 斯克尔德心象 (Activity 321201) ====================

    def get_or_init_skuld(self, db, uid, activity_id=321201):
        from skuld_service import read, ACTIVITY, require
        require(activity_id==ACTIVITY,'心象活动不匹配')
        return read(db or self.db,uid)

    def build_24051_payload(self, db, uid, activity_id=321201):
        from skuld_service import payload, ACTIVITY, require
        from codec import encode
        require(activity_id==ACTIVITY,'心象活动不匹配')
        return encode('sc_24051',payload(db or self.db,uid))

    # ==================== 6. 谧光叙录 · 霍德尔活动 (Activity 3941101) ====================

    def get_or_init_hodur(self, db, uid, activity_id=3941101):
        from hodur_service import read, ACTIVITY, require
        require(activity_id == ACTIVITY, '未知谧光叙录活动')
        return list(read(db or self.db, uid).values())

    def build_89421_payload(self, arg1, arg2=None):
        from codec import encode
        from hodur_service import payload
        db, uid = self._resolve_db_uid(arg1, arg2)
        return encode('sc_89421', payload(db, uid))

    def reset_hodur_chapter(self, ctx, uid, chapter_id, activity_id=3941101):
        from hodur_service import reset
        return reset(ctx, uid, activity_id, chapter_id)

    def settle_hodur_challenge(self, ctx, uid, activity_id=3941101):
        from hodur_challenge import settle
        return settle(ctx, uid, activity_id)

    def select_hodur_event(self, ctx, uid, chapter_id, option_id, activity_id=3941101):
        from hodur_service import select
        return select(ctx, uid, activity_id, chapter_id, option_id)

    def settle_hodur_stage(self, ctx, uid, dest_int, win=True):
        from hodur_service import NORMAL, finish_battle
        if dest_int not in NORMAL:
            return None
        frames = finish_battle(ctx, uid, dest_int, 3941101, win=win)
        return {'chapter_id': NORMAL[dest_int]['chapter_id'], 'stage_id': dest_int, 'frames': frames}

    # ==================== 7. 潜质觉醒 · 现时无返之途·薇儿丹蒂 SP (Activity 242841/242851/242871) ====================

    def _get_sphero_active_entrusts(self,db,uid):
        from sphero_schedule_service import read,wire_entrusts
        return wire_entrusts(read(db,uid))

    def build_83016_payload(self, arg1, arg2=None):
        from sphero_schedule_service import snapshot
        from codec import encode
        db,uid=self._resolve_db_uid(arg1,arg2)
        return encode('sc_83016',snapshot(db,uid))

    def build_83020_payload(self,arg1,arg2=None):
        from sphero_battle_service import boss_payload
        from codec import encode
        db,uid=self._resolve_db_uid(arg1,arg2)
        return encode('sc_83020',boss_payload(db,uid))

    def build_83021_payload(self,arg1,arg2=None):
        from sphero_leisure_service import puzzle_payload
        from codec import encode
        db,uid=self._resolve_db_uid(arg1,arg2)
        return encode('sc_83021',puzzle_payload(db,uid))

    def build_83026_payload(self,arg1,arg2=None):
        from sphero_leisure_service import barbecue_payload
        from codec import encode
        db,uid=self._resolve_db_uid(arg1,arg2)
        return encode('sc_83026',barbecue_payload(db,uid))

    def confirm_sphero_schedule(self, ctx, uid, schedule_info_list, activity_id=SP_HERO_ACTIVITY_ID):
        from sphero_schedule_service import apply
        return apply(ctx,uid,83003,dict(schedule_info_list=schedule_info_list,activity_id=activity_id))

    def dispatch_sphero_entrust(self, ctx, uid, index, entrust_id, activity_id=SP_HERO_ACTIVITY_ID):
        from sphero_schedule_service import apply
        return apply(ctx,uid,83009,dict(index=index,entrust_id=entrust_id,activity_id=activity_id))

    def cancel_sphero_entrust(self, ctx, uid, index, activity_id=SP_HERO_ACTIVITY_ID):
        from sphero_schedule_service import apply
        return apply(ctx,uid,83011,dict(index=index,activity_id=activity_id))

    def get_sphero_entrust_award(self, ctx, uid, index_list, activity_id=SP_HERO_ACTIVITY_ID):
        from sphero_schedule_service import apply
        return apply(ctx,uid,83013,dict(index_list=index_list,activity_id=activity_id))

    def use_sphero_accelerator(self, ctx, uid, index, use_cnt=1, activity_id=SP_HERO_ACTIVITY_ID):
        from sphero_schedule_service import apply
        return apply(ctx,uid,83018,dict(index=index,use_cnt=use_cnt,activity_id=activity_id))

    def settle_sphero_barbecue(self,ctx,uid,stage_id,grade_id,hero_id,result):
        from sphero_leisure_service import settle_barbecue
        return settle_barbecue(ctx,uid,dict(stage_id=stage_id,grade_id=grade_id,hero_id=hero_id,result=result))

    def settle_sphero_stage(self,*args,**kwargs):
        raise OperationError(2,'薇儿挑战必须经有效的66战斗会话结算')

    # ==================== 7. 极限机装 · 乌尔碰碰车 (Activity 4243101) ====================

    def build_89701_payload(self, arg1, arg2=None):
        from vehicle_ball_service import read
        from codec import encode
        db, uid = self._resolve_db_uid(arg1, arg2)
        return encode('sc_89701', read(db, uid))

    def pass_vehicle_stage(self, ctx, uid, stage_id, is_pass, kv_list=None, buffs=None, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        from vehicle_ball_service import settle
        return settle(ctx, uid, activity_id, stage_id, is_pass,
                      [] if kv_list is None else kv_list, [] if buffs is None else buffs)

    def set_vehicle(self, ctx, uid, vehicle_id, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        from vehicle_ball_service import select
        return select(ctx, uid, activity_id, vehicle_id)

    def get_vehicle_illustrate_reward(self, ctx, uid, buff_id, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        from vehicle_ball_service import claim
        return claim(ctx, uid, activity_id, buff_id)

    def unlock_vehicle_buff(self, ctx, uid, buff_id, activity_id=VEHICLE_BALL_ACTIVITY_ID):
        from vehicle_ball_service import unlock
        return unlock(ctx, uid, activity_id, buff_id)

    # ==================== 9. 浮光绎曲与鸣律探微 · 常驻音律演奏会 (Activity 3814801 / 283041) ====================

    def get_or_init_music_records(self, db, uid):
        from resident_music_service import records
        return records(db or self.db, uid)

    def build_61047_payload(self, arg1, arg2=None):
        """生成常驻音律音乐会 sc_61047 登录/查询载荷 (challenge_info: [{id, score, sign}])，返回 bytes"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rows = self.get_or_init_music_records(db, uid)
        challenges = [
            {
                "id": int(r["music_id"]),
                "score": int(r["score"] or 0),
                "sign": int(r["sign"] or 0),
            }
            for r in rows
        ]
        obj = {"challenge_info": challenges}
        try:
            from codec import encode
            return encode("sc_61047", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_61047 失败: {e}")
            return b""

    def build_83124_payload(self, arg1, arg2=None):
        """生成夏日鸣律探微小游戏 sc_83124 载荷 (rhythm_stage_list, story_id_list)，返回 bytes"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rows = db.query(
            "SELECT stage_id, star_id_list, use_seconds FROM rhythm_game_progress WHERE uid = ? ORDER BY stage_id",
            (uid,)
        )
        stage_list = []
        for r in rows:
            try:
                stars = json.loads(r["star_id_list"]) if isinstance(r["star_id_list"], str) else (r["star_id_list"] or [])
            except Exception:
                stars = []
            st_item = {
                "id": int(r["stage_id"]),
                "use_seconds": int(r["use_seconds"] or 0),
            }
            if stars:
                st_item["star_id_list"] = stars
            stage_list.append(st_item)

        s_rows = db.query("SELECT story_id FROM rhythm_story_record WHERE uid = ?", (uid,))
        story_ids = [int(r["story_id"]) for r in s_rows]
        if not story_ids:
            story_ids = [911001011]

        obj = {
            "rhythm_stage_list": stage_list,
            "story_id_list": story_ids,
        }
        try:
            from codec import encode
            return encode("sc_83124", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_83124 失败: {e}")
            return b""

    def settle_music_stage(self, ctx, uid, music_id, score, state, other_data=None):
        from resident_music_service import settle
        return settle(ctx, uid, music_id, score, state)

    def claim_music_rewards(self, ctx, uid, id_list):
        from resident_music_service import claim
        return claim(ctx, uid, id_list)

    def play_rhythm_story(self, ctx, uid, activity_id, story_id):
        """记录鸣律探微剧情阅览 (cs_83126 -> sc_83127)"""
        db = getattr(ctx, "db", self.db)
        now_ts = int(time.time())
        story_id = int(story_id)
        db.execute(
            """INSERT INTO rhythm_story_record (uid, story_id, update_ts)
               VALUES (?, ?, ?)
               ON CONFLICT(uid, story_id) DO UPDATE SET update_ts = excluded.update_ts""",
            (uid, story_id, now_ts)
        )
        return {"result": 0}

    # ==================== 10. 食与异世界 · 夏日餐厅 (Activity 3539101) ====================

    def get_or_init_summer_pub(self, db, uid):
        from summer_pub_service import read
        return read(db or self.db, uid)

    def save_summer_pub(self, db, uid, record):
        from summer_pub_service import save
        return save(db or self.db, uid, record)

    def build_89011_payload(self, arg1, arg2=None):
        from summer_pub_service import payload
        from codec import encode
        db, uid = self._resolve_db_uid(arg1, arg2)
        return encode('sc_89011', payload(db, uid))

    # -------------------------------------------------------------
    # 诡谈夜话 (Activity 4142701 / RogueCard) 协议族 (89601~89629)
    # -------------------------------------------------------------
    def get_or_init_rogue_card(self, db, uid):
        """获取或初始化诡谈夜话玩家数据"""
        db = db or self.db
        if db is None:
            from account_db import get_db
            db = get_db()
        rows = db.query(
            """SELECT thread_id, thread_state, battle_id,
                      finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
                      challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
                      challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
                      save_data, save_rollback, update_ts
               FROM rogue_card_game_record WHERE uid = ?""",
            (uid,)
        )
        if not rows:
            now_ts = int(time.time())
            db.execute(
                """INSERT OR REPLACE INTO rogue_card_game_record
                (uid, thread_id, thread_state, battle_id,
                 finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
                 challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
                 challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
                 save_data, save_rollback, update_ts)
                VALUES (?, 0, 0, 0, '[]', '[]', '[]', '[]', 0, 0, 0, 0, '[]', '[]', '[]', '[]', '{}', '{}', ?)""",
                (uid, now_ts)
            )
            rows = db.query(
                """SELECT thread_id, thread_state, battle_id,
                          finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
                          challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
                          challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
                          save_data, save_rollback, update_ts
                   FROM rogue_card_game_record WHERE uid = ?""",
                (uid,)
            )
        r = rows[0]
        def _parse_json(val, default):
            if val is None:
                return default
            if isinstance(val, (list, dict)):
                return val
            try:
                return json.loads(val)
            except Exception:
                return default

        return {
            "thread_id": int(r["thread_id"] or 0),
            "thread_state": int(r["thread_state"] or 0),
            "battle_id": int(r["battle_id"] or 0),
            "finish_thread_list": _parse_json(r["finish_thread_list"], []),
            "like_thread_list": _parse_json(r["like_thread_list"], []),
            "post_thread_list": _parse_json(r["post_thread_list"], []),
            "view_thread_list": _parse_json(r["view_thread_list"], []),
            "challenge_deck": int(r["challenge_deck"] or 0),
            "challenge_diff": int(r["challenge_diff"] or 0),
            "challenge_state": int(r["challenge_state"] or 0),
            "challenge_battle_id": int(r["challenge_battle_id"] or 0),
            "challenge_status_list": _parse_json(r["challenge_status_list"], []),
            "gather_card_list": _parse_json(r["gather_card_list"], []),
            "gather_enhance_list": _parse_json(r["gather_enhance_list"], []),
            "gather_weal_woe_list": _parse_json(r["gather_weal_woe_list"], []),
            "save_data": _parse_json(r["save_data"], {}),
            "save_rollback": _parse_json(r["save_rollback"], {}),
        }

    def save_rogue_card(self, db, uid, rec):
        """保存诡谈夜话玩家数据"""
        db = db or self.db
        if db is None:
            from account_db import get_db
            db = get_db()
        now_ts = int(time.time())
        db.execute(
            """INSERT OR REPLACE INTO rogue_card_game_record
            (uid, thread_id, thread_state, battle_id,
             finish_thread_list, like_thread_list, post_thread_list, view_thread_list,
             challenge_deck, challenge_diff, challenge_state, challenge_battle_id,
             challenge_status_list, gather_card_list, gather_enhance_list, gather_weal_woe_list,
             save_data, save_rollback, update_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uid,
                int(rec.get("thread_id", 0)),
                int(rec.get("thread_state", 0)),
                int(rec.get("battle_id", 0)),
                json.dumps(rec.get("finish_thread_list", [])),
                json.dumps(rec.get("like_thread_list", [])),
                json.dumps(rec.get("post_thread_list", [])),
                json.dumps(rec.get("view_thread_list", [])),
                int(rec.get("challenge_deck", 0)),
                int(rec.get("challenge_diff", 0)),
                int(rec.get("challenge_state", 0)),
                int(rec.get("challenge_battle_id", 0)),
                json.dumps(rec.get("challenge_status_list", [])),
                json.dumps(rec.get("gather_card_list", [])),
                json.dumps(rec.get("gather_enhance_list", [])),
                json.dumps(rec.get("gather_weal_woe_list", [])),
                json.dumps(rec.get("save_data", {})),
                json.dumps(rec.get("save_rollback", {})),
                now_ts
            )
        )

    def build_89601_payload(self, arg1, arg2=None):
        """构造 sc_89601 登录下发全量状态帧（动态生成）"""
        db, uid = self._resolve_db_uid(arg1, arg2)
        rec = self.get_or_init_rogue_card(db, uid)
        obj = {
            "story": {
                "thread_id": int(rec.get("thread_id", 0)),
                "thread_state": int(rec.get("thread_state", 0)),
                "battle_id": int(rec.get("battle_id", 0)),
                "finish_thread_list": [int(x) for x in rec.get("finish_thread_list", [])],
                "like_thread_list": [int(x) for x in rec.get("like_thread_list", [])],
                "post_thread_list": [int(x) for x in rec.get("post_thread_list", [])],
                "view_thread_list": [int(x) for x in rec.get("view_thread_list", [])],
            },
            "challenge": {
                "deck": int(rec.get("challenge_deck", 0)),
                "diff": int(rec.get("challenge_diff", 0)),
                "state": int(rec.get("challenge_state", 0)),
                "battle_id": int(rec.get("challenge_battle_id", 0)),
                "status_list": rec.get("challenge_status_list", []),
            },
            "gather_card_list": [int(x) for x in rec.get("gather_card_list", [])],
            "gather_enhance_list": [int(x) for x in rec.get("gather_enhance_list", [])],
            "gather_weal_woe_list": [int(x) for x in rec.get("gather_weal_woe_list", [])],
        }
        try:
            from codec import encode
            return encode("sc_89601", obj)
        except Exception as e:
            logger.error(f"[MiniGameService] 编码 sc_89601 失败: {e}")
            return b""

    def start_rogue_card_post(self, ctx, uid, thread_id):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'start_rogue_card_post', thread_id)

    def complete_rogue_card_story(self, ctx, uid, thread_id, story_id=0, story_id_index=0):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'complete_rogue_card_story', thread_id, story_id, story_id_index)

    def click_like_rogue_card_post(self, ctx, uid, thread_id):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'click_like_rogue_card_post', thread_id)

    def reback_rogue_card_post(self, ctx, uid, thread_id):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'reback_rogue_card_post', thread_id)

    def view_rogue_card_thread_post(self, ctx, uid, thread_id):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'view_rogue_card_thread_post', thread_id)

    def complete_rogue_card_post(self, ctx, uid, thread_id, joker_result, info=None):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'complete_rogue_card_post', thread_id, joker_result, info)

    def settle_rogue_card_challenge(self, ctx, uid, deck, diff, info=None):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'settle_rogue_card_challenge', deck, diff, info)

    def enter_rogue_card_game(self, ctx, uid, stage_id=0, deck=0, diff=0):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'enter_rogue_card_game', stage_id, deck, diff)

    def save_rogue_card_progress(self, ctx, uid, battle_id, battle_type=0, save_data=None, check_sum=None):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'save_rogue_card_progress', battle_id, battle_type, save_data, check_sum)

    def continue_rogue_card_progress(self, ctx, uid, battle_id=0, battle_type=0):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'continue_rogue_card_progress', battle_id, battle_type)

    def save_rogue_card_rollback(self, ctx, uid, battle_id=0, battle_type=0):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'save_rogue_card_rollback', battle_id, battle_type)

    def get_stage_save_data(self, ctx, uid, battle_id=0, battle_type=0, save_rollback=None):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'get_stage_save_data', battle_id, battle_type, save_rollback)

    def interrupt_rogue_card_post(self, ctx, uid, battle_id=0, battle_type=0):
        from rogue_card_service import dispatch
        return dispatch(self, ctx, uid, 'interrupt_rogue_card_post', battle_id, battle_type)




# ==================== 协议算子实现 (Operations) ====================

# 1. 远村异闻 · 海拉弹珠算子
@operation
class PinballFinishOp(Operation):
    """海拉弹珠关卡结算：cs_89402 {stage_id, score, harm} -> sc_89403 {result: 0} + sc_89401。"""
    prepare_response_in_transaction = True

    cmd = 89402
    sc = 89403

    def validate(self, data, ctx):
        if not data or "stage_id" not in data:
            raise OperationError(1, "缺少 stage_id")
        return data

    def apply(self, data, ctx):
        stage_id = data.get("stage_id", 0)
        score = data.get("score", 0)
        harm = data.get("harm", 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_pinball_level(ctx, self.uid, stage_id, score, harm)
        ctx.log(
            f"cs_89402 -> 海拉弹珠关卡结算 stage={stage_id} score={score} harm={harm} "
            f"海拉等级={res['stage_count']} 通关数={len(res['cleared_stage_list'])}"
        )
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        from pinball_service import payload
        encoder = ctx.codec_encode or encode
        frames = []
        if result['changed']:
            state = encoder('sc_89401', payload(ctx.db, self.uid))
            if not isinstance(state, (bytes, bytearray, memoryview)) or not state:
                raise ValueError('弹珠进度帧为空')
            frames.append(DownFrame(89401, bytes(state)))
        frames.extend(ctx.pop_pending_frames())
        ack = encoder('sc_89403', dict(result=0))
        if not ack:
            raise ValueError('弹珠结算回执为空')
        return frames + [DownFrame(self.sc, ack)]


@operation
class PinballEquipSkillOp(Operation):
    """海拉弹珠技能装配：cs_89404 {activity_id, skill_list} -> sc_89405 {result: 0} + sc_89401。"""
    prepare_response_in_transaction = True

    cmd = 89404
    sc = 89405

    def validate(self, data, ctx):
        if not data or "activity_id" not in data:
            raise OperationError(1, "缺少 activity_id")
        return data

    def apply(self, data, ctx):
        aid = data.get("activity_id", 3840801)
        skill_list = data.get("skill_list", [])
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.equip_pinball_skills(ctx, self.uid, aid, skill_list)
        ctx.log(f"cs_89404 -> 海拉弹珠装配技能 activity={aid} skills={res['equip_skill_list']}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        ack = (ctx.codec_encode or encode)('sc_89405', dict(result=0))
        if not ack:
            raise ValueError('弹珠装配回执为空')
        # Native callback applies this exact active list and derives passives.
        return [DownFrame(self.sc, ack)]


# 2. 卡达斯假日赛 · 夏活坦克竞速算子
@operation
class SummerRaceCompleteOp(Operation):
    """坦克竞速关卡结算：cs_84332 {activity_id, stage_id, result, round, score, kill_num} -> sc_84333 {result: 0} + sc_84331。"""
    prepare_response_in_transaction = True

    cmd = 84332
    sc = 84333

    def validate(self, data, ctx):
        if not data:
            raise OperationError(1, "请求数据为空")
        return data

    def apply(self, data, ctx):
        from kadas_race_service import settle
        return settle(ctx, self.uid, data.get('activity_id'), data.get('stage_id'), data.get('result'),
                      data.get('round',0), data.get('score',0), data.get('kill_num',0))

    def respond(self, result, data, ctx):
        from codec import encode
        from kadas_race_service import payload
        encoder=ctx.codec_encode or encode
        ack=encoder('sc_84333',dict(result=0))
        state=encoder('sc_84331',payload(ctx.db,self.uid))
        if not ack or not state:raise ValueError('卡达斯结算必要帧为空')
        # Native callback prepares first-clear animations before marking clear;
        # it also touches challenge values for normal stages. Apply the exact
        # server snapshot AFTER that callback, not before it.
        return ctx.pop_pending_frames()+[DownFrame(84333,ack),DownFrame(84331,state)]


@operation
class SummerRaceModifyOp(Operation):
    """坦克战车改装：cs_84334 {activity_id, tank_id, weapon_list} -> sc_84335 {result: 0} + sc_84331。"""
    prepare_response_in_transaction = True

    cmd = 84334
    sc = 84335

    def validate(self, data, ctx):
        if not data or "tank_id" not in data:
            raise OperationError(1, "缺少 tank_id")
        return data

    def apply(self, data, ctx):
        from kadas_race_service import modify
        return modify(ctx, self.uid, data.get('activity_id'), data.get('tank_id'), data.get('weapon_list',[]))

    def respond(self, result, data, ctx):
        from codec import encode
        ack=(ctx.codec_encode or encode)('sc_84335',dict(result=0))
        if not ack:raise ValueError('卡达斯改装回执为空')
        return [DownFrame(84335,ack)]


# 3. 通用常驻积分领奖算子
@operation
class ActivityPointRewardOp(_ActivityPointClaimOp):
    cmd, sc = 60054, 60055


# 4. 溯梦之隙 · 斯克尔德心象算子
from skuld_service import SkuldSendMarkOp, SkuldPuzzleCompleteOp


# 5. 谧光叙录 · 霍德尔活动算子
@operation
class HodurResetChapterOp(Operation):
    """霍德尔重置章节：cs_89422 {activity_id, chapter_id} -> sc_89423 {result: 0}"""
    prepare_response_in_transaction = True
    cmd = 89422
    sc = 89423

    def validate(self, data, ctx):
        if not data or "chapter_id" not in data:
            raise OperationError(1, "缺少 chapter_id")
        return data

    def apply(self, data, ctx):
        chap_id = data.get("chapter_id", 0)
        act_id = data.get("activity_id", 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.reset_hodur_chapter(ctx, self.uid, chap_id, activity_id=act_id)
        ctx.log(f"cs_89422 -> 霍德尔重置章节 chap={chap_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89423 = ctx.codec_encode("sc_89423", {"result": 0}) if ctx.codec_encode else encode("sc_89423", {"result": 0})
        if not isinstance(p89423, (bytes, bytearray, memoryview)) or not p89423:
            raise ValueError('谧光叙录重置回执为空')
        frames = ctx.pop_pending_frames() if hasattr(ctx, "pop_pending_frames") else []
        frames.append(DownFrame(self.sc, p89423))
        return frames


@operation
class HodurSettleChallengeOp(Operation):
    """霍德尔结算挑战：cs_89424 {activity_id} -> sc_89425 {result: 0}"""
    prepare_response_in_transaction = True
    cmd = 89424
    sc = 89425

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        act_id = data.get("activity_id", 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_hodur_challenge(ctx, self.uid, activity_id=act_id)
        ctx.log(f"cs_89424 -> 霍德尔结算挑战")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p89425 = ctx.codec_encode("sc_89425", {"result": 0}) if ctx.codec_encode else encode("sc_89425", {"result": 0})
        if not isinstance(p89425, (bytes, bytearray, memoryview)) or not p89425:
            raise ValueError('谧光挑战结算回执为空')
        frames = ctx.pop_pending_frames() if hasattr(ctx, 'pop_pending_frames') else []
        frames.append(DownFrame(self.sc, p89425))
        return frames


@operation
class HodurSelectEventOp(Operation):
    """霍德尔选择事件：cs_89426 {activity_id, chapter_id, option_id} -> sc_89427 {round, result: 0}"""
    prepare_response_in_transaction = True
    error_result_field = 2
    cmd = 89426
    sc = 89427

    def validate(self, data, ctx):
        if not data or "option_id" not in data:
            raise OperationError(1, "缺少 option_id")
        return data

    def apply(self, data, ctx):
        chap_id = data.get("chapter_id", 0)
        opt_id = data.get("option_id", 0)
        act_id = data.get("activity_id", 0)
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.select_hodur_event(ctx, self.uid, chap_id, opt_id, activity_id=act_id)
        ctx.log(f"cs_89426 -> 霍德尔选择事件 chap={chap_id} opt={opt_id} round={res.get('round')}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        rnd = int(result.get("round", 1))
        p89427 = ctx.codec_encode("sc_89427", {"result": 0, "round": rnd}) if ctx.codec_encode else encode("sc_89427", {"result": 0, "round": rnd})
        if not isinstance(p89427, (bytes, bytearray, memoryview)) or not p89427:
            raise ValueError('谧光叙录事件回执为空')
        frames = ctx.pop_pending_frames() if hasattr(ctx, "pop_pending_frames") else []
        frames.append(DownFrame(self.sc, p89427))
        return frames


# 6. 潜质觉醒 · 现时无返之途·薇儿丹蒂 SP 挑战算子
from sphero_schedule_service import SPHeroScheduleOp
from sphero_leisure_service import LeisureOp,settle_barbecue
@operation
class SPHeroConfirmScheduleOp(SPHeroScheduleOp):
    cmd,sc=83003,83004


@operation
class SPHeroGetScheduleAwardOp(SPHeroScheduleOp):
    cmd,sc=83005,83006


@operation
class SPHeroRefreshEntrustListOp(SPHeroScheduleOp):
    cmd,sc=83007,83008


@operation
class SPHeroDispatchEntrustOp(SPHeroScheduleOp):
    cmd,sc=83009,83010


@operation
class SPHeroCancelEntrustOp(SPHeroScheduleOp):
    cmd,sc=83011,83012


@operation
class SPHeroGetEntrustAwardOp(SPHeroScheduleOp):
    cmd,sc=83013,83014


@operation
class SPHeroUseAcceleratorOp(SPHeroScheduleOp):
    cmd,sc=83018,83019


@operation
class SPHeroSettleBarbecueOp(LeisureOp):
    cmd,sc=83022,83023

    def apply(self,data,ctx):return settle_barbecue(ctx,self.uid,data)


@operation
class SPHeroFreeRefreshEntrustOp(SPHeroScheduleOp):
    cmd,sc=83040,83041


# ==================== 7. 极限机装 · 乌尔碰碰车算子 (Operations) ====================

@operation
class VehicleBallPassStageOp(Operation):
    prepare_response_in_transaction = True
    cmd = 89702
    sc = 89703

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = MiniGameService.get_instance(ctx.db)
        return svc.pass_vehicle_stage(ctx, self.uid, data.get('stage_id', 0), data.get('is_pass', 0), kv_list=data.get('kv_list', []), buffs=data.get('buffs', []), activity_id=data.get('activity_id', 0))

    def respond(self, result, data, ctx):
        from codec import encode
        packet = (ctx.codec_encode or encode)('sc_89703', result)
        frames = ctx.pop_pending_frames() if hasattr(ctx, 'pop_pending_frames') else []
        frames.append(DownFrame(self.sc, packet))
        return frames


@operation
class VehicleBallSetVehicleOp(Operation):
    prepare_response_in_transaction = True
    cmd = 89704
    sc = 89705

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = MiniGameService.get_instance(ctx.db)
        return svc.set_vehicle(ctx, self.uid, data.get('vehicle', 0), activity_id=data.get('activity_id', 0))

    def respond(self, result, data, ctx):
        from codec import encode
        packet = (ctx.codec_encode or encode)('sc_89705', result)
        frames = ctx.pop_pending_frames() if hasattr(ctx, 'pop_pending_frames') else []
        frames.append(DownFrame(self.sc, packet))
        return frames


@operation
class VehicleBallGetIllustrateRewardOp(Operation):
    prepare_response_in_transaction = True
    cmd = 89706
    sc = 89707

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = MiniGameService.get_instance(ctx.db)
        return svc.get_vehicle_illustrate_reward(ctx, self.uid, data.get('buff_id', 0), activity_id=data.get('activity_id', 0))

    def respond(self, result, data, ctx):
        from codec import encode
        packet = (ctx.codec_encode or encode)('sc_89707', result)
        from operations import _refresh_frames
        frames = _refresh_frames(ctx, self.uid, strict=True)
        frames.append(DownFrame(self.sc, packet))
        return frames


@operation
class VehicleBallUnlockBuffOp(Operation):
    prepare_response_in_transaction = True
    cmd = 89708
    sc = 89709

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        svc = MiniGameService.get_instance(ctx.db)
        return svc.unlock_vehicle_buff(ctx, self.uid, data.get('buff_id', 0), activity_id=data.get('activity_id', 0))

    def respond(self, result, data, ctx):
        from codec import encode
        packet = (ctx.codec_encode or encode)('sc_89709', result)
        frames = ctx.pop_pending_frames() if hasattr(ctx, 'pop_pending_frames') else []
        frames.append(DownFrame(self.sc, packet))
        return frames


# 9. 浮光绎曲与鸣律探微算子
@operation
class MusicQueryCompeletOp(Operation):
    """浮光绎曲乐曲挑战结算：cs_61048 {id, score, other_data, state} -> sc_61049 {result: 0}"""
    prepare_response_in_transaction = True
    cmd = 61048
    sc = 61049

    def validate(self, data, ctx):
        if not data or "id" not in data:
            raise OperationError(1, "缺少乐曲 id")
        return data

    def apply(self, data, ctx):
        music_id = data.get("id", 0)
        score = data.get("score", 0)
        state = data.get("state", 0)
        other_data = data.get("other_data") or []
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_music_stage(ctx, self.uid, music_id, score, state, other_data)
        ctx.log(f"cs_61048 -> 浮光绎曲结算: music_id={music_id} score={score} state={state}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p61049 = ctx.codec_encode("sc_61049", result) if ctx.codec_encode else encode("sc_61049", result)
        if not p61049:
            raise ValueError("音乐结算回执为空")
        frames = ctx.pop_pending_frames() if hasattr(ctx, "pop_pending_frames") else []
        frames.append(DownFrame(self.sc, p61049))
        return frames


@operation
class MusicQueryRewardOp(Operation):
    """浮光绎曲乐曲奖励领取：cs_61050 {id_list} -> sc_61051 {result: 0, reward_list}"""
    prepare_response_in_transaction = True
    cmd = 61050
    sc = 61051

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        id_list = data.get("id_list") or []
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.claim_music_rewards(ctx, self.uid, id_list)
        ctx.log(f"cs_61050 -> 浮光绎曲领奖: id_list={id_list}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p61051 = ctx.codec_encode("sc_61051", result) if ctx.codec_encode else encode("sc_61051", result)
        if not p61051:
            raise ValueError("音乐领奖回执为空")
        from operations import _refresh_frames
        frames = _refresh_frames(ctx, self.uid, strict=True)
        frames.append(DownFrame(self.sc, p61051))
        return frames


@operation
class RhythmPlayStoryOp(Operation):
    """鸣律探微剧情阅览：cs_83126 {activity_id, story_id} -> sc_83127 {result: 0}"""
    cmd = 83126
    sc = 83127

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        activity_id = int(data.get("activity_id", 0))
        story_id = int(data.get("story_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.play_rhythm_story(ctx, self.uid, activity_id, story_id)
        ctx.log(f"cs_83126 -> 鸣律探微剧情已读: story_id={story_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p83127 = ctx.codec_encode("sc_83127", result) if ctx.codec_encode else encode("sc_83127", result)
        frames = [DownFrame(self.sc, p83127)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


# ==================== 10. 食与异世界 · 夏日餐厅算子 (Activity 3539101) ====================

from summer_pub_service import (SummerPubPinBallOp, SummerPubViewIlluOp,
                                SummerPubCookOp, SummerPubTimeStateOp)


# ==================== 诡谈夜话 (Activity 4142701) 协议算子 ====================

@operation
class RogueCardStartPostOp(Operation):
    """诡谈夜话·开帖：cs_89602 {thread_id} -> sc_89603 {result: 0}"""
    cmd = 89602
    sc = 89603

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.start_rogue_card_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89602 -> 诡谈夜话开帖 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89603", result) if ctx.codec_encode else encode("sc_89603", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardCompleteStoryOp(Operation):
    """诡谈夜话·完成剧情：cs_89604 {thread_id, story_id, story_id_index} -> sc_89605 {result: 0}"""
    cmd = 89604
    sc = 89605

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        story_id = int(data.get("story_id", 0))
        story_id_index = int(data.get("story_id_index", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.complete_rogue_card_story(ctx, self.uid, thread_id, story_id, story_id_index)
        ctx.log(f"cs_89604 -> 诡谈夜话剧情推进 thread_id={thread_id} story_id={story_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89605", result) if ctx.codec_encode else encode("sc_89605", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardClickLikePostOp(Operation):
    """诡谈夜话·论坛点赞：cs_89606 {thread_id} -> sc_89607 {result: 0}"""
    cmd = 89606
    sc = 89607

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.click_like_rogue_card_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89606 -> 诡谈夜话论坛点赞 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89607", result) if ctx.codec_encode else encode("sc_89607", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardRebackPostOp(Operation):
    """诡谈夜话·重开/回退帖子：cs_89608 {thread_id} -> sc_89609 {result: 0}"""
    cmd = 89608
    sc = 89609

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.reback_rogue_card_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89608 -> 诡谈夜话重开帖子 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89609", result) if ctx.codec_encode else encode("sc_89609", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardViewThreadPostOp(Operation):
    """诡谈夜话·论坛阅览：cs_89610 {thread_id} -> sc_89611 {result: 0}"""
    cmd = 89610
    sc = 89611

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.view_rogue_card_thread_post(ctx, self.uid, thread_id)
        ctx.log(f"cs_89610 -> 诡谈夜话论坛阅览 thread_id={thread_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89611", result) if ctx.codec_encode else encode("sc_89611", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardCompletePostOp(Operation):
    """诡谈夜话·主线帖子对局结算：cs_89612 {thread_id, joker_result, info} -> sc_89613 {result: 0} + sc_28007(如有)"""
    cmd = 89612
    sc = 89613

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        thread_id = int(data.get("thread_id", 0))
        joker_result = int(data.get("joker_result", 0))
        info = data.get("info")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.complete_rogue_card_post(ctx, self.uid, thread_id, joker_result, info)
        ctx.log(f"cs_89612 -> 诡谈夜话主线结算 thread_id={thread_id} win={joker_result==1}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89613", result) if ctx.codec_encode else encode("sc_89613", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardChallengeSettleOp(Operation):
    """诡谈夜话·挑战模式结算：cs_89616 {deck, diff, info} -> sc_89617 {result: 0}"""
    cmd = 89616
    sc = 89617

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        deck = int(data.get("deck", 0))
        diff = int(data.get("diff", 0))
        info = data.get("info")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.settle_rogue_card_challenge(ctx, self.uid, deck, diff, info)
        ctx.log(f"cs_89616 -> 诡谈夜话挑战结算 deck={deck} diff={diff}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89617", result) if ctx.codec_encode else encode("sc_89617", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardEnterGameOp(Operation):
    """诡谈夜话·进入对局：cs_89618 {stage_id, deck, diff} -> sc_89619 {result: 0, battle_id}"""
    cmd = 89618
    sc = 89619

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        stage_id = int(data.get("stage_id", 0))
        deck = int(data.get("deck", 0))
        diff = int(data.get("diff", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.enter_rogue_card_game(ctx, self.uid, stage_id, deck, diff)
        ctx.log(f"cs_89618 -> 诡谈夜话进入对局 stage_id={stage_id} deck={deck} diff={diff} battle_id={res.get('battle_id')}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89619", result) if ctx.codec_encode else encode("sc_89619", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardSaveProgressPostOp(Operation):
    """诡谈夜话·保存局内进度：cs_89620 {battle_id, battle_type, save_data, check_sum} -> sc_89621 {result: 0}"""
    cmd = 89620
    sc = 89621

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        save_data = data.get("save_data")
        check_sum = data.get("check_sum")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.save_rogue_card_progress(ctx, self.uid, battle_id, battle_type, save_data, check_sum)
        ctx.log(f"cs_89620 -> 诡谈夜话保存局内进度 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89621", result) if ctx.codec_encode else encode("sc_89621", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardContinueProgressPostOp(Operation):
    """诡谈夜话·继续局内进度：cs_89622 {battle_id, battle_type} -> sc_89623 {result: 0, battle_id, save_data, save_rollback}"""
    cmd = 89622
    sc = 89623

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.continue_rogue_card_progress(ctx, self.uid, battle_id, battle_type)
        ctx.log(f"cs_89622 -> 诡谈夜话继续局内进度 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89623", result) if ctx.codec_encode else encode("sc_89623", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardSaveRollBackPostOp(Operation):
    """诡谈夜话·中断结算：cs_89624 -> sc_89625，返回 settle_info。"""
    cmd = 89624
    sc = 89625

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.save_rogue_card_rollback(ctx, self.uid, battle_id, battle_type)
        ctx.log(f"cs_89624 -> 诡谈夜话中断结算 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89625", result) if ctx.codec_encode else encode("sc_89625", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardGetStageSaveDataOp(Operation):
    """诡谈夜话·写入回滚点：cs_89626 {save_rollback} -> sc_89627。"""
    cmd = 89626
    sc = 89627

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        save_rollback = data.get("save_rollback")
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.get_stage_save_data(ctx, self.uid, battle_id, battle_type, save_rollback)
        ctx.log(f"cs_89626 -> 诡谈夜话写入回滚点 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89627", result) if ctx.codec_encode else encode("sc_89627", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


@operation
class RogueCardInterruptPostOp(Operation):
    """诡谈夜话·只读当前存档：cs_89628 -> sc_89629 {save_data}。"""
    cmd = 89628
    sc = 89629

    def validate(self, data, ctx):
        return data or {}

    def apply(self, data, ctx):
        battle_id = int(data.get("battle_id", 0))
        battle_type = int(data.get("battle_type", 0))
        svc = MiniGameService.get_instance(ctx.db)
        res = svc.interrupt_rogue_card_post(ctx, self.uid, battle_id, battle_type)
        ctx.log(f"cs_89628 -> 诡谈夜话读取当前存档 battle_id={battle_id}")
        return res

    def respond(self, result, data, ctx):
        from codec import encode
        p = ctx.codec_encode("sc_89629", result) if ctx.codec_encode else encode("sc_89629", result)
        frames = [DownFrame(self.sc, p)]
        if hasattr(ctx, "pop_pending_frames"):
            frames.extend(ctx.pop_pending_frames())
        return frames


from rogue_card_service import install as _install_rogue_card
_install_rogue_card(globals())




# Native Summer ChessBoard bootstrap operations (850xx).
import summer_board_service as _summer_board_service
