# -*- coding: utf-8 -*-
"""
backhome_service.py — 游园街/后宅/食堂独立领域模块（BackHome / Dorm / Canteen，58xxx 协议族）

[2026-09-06 规范化架构重构]
本模块为纯粹的领域服务（Domain Service）：
1. 承载 34 个游园街业务逻辑（BackHomeService 单例方法）；
2. 登录帧动态生成（sc_58001 / sc_58003 / sc_58027 供 generator 调用）；
3. 时间流逝推演（消费 Events.TIME_TICK，做菜与疲劳恢复）；
4. 业务事件总线发射：
   - Events.DORM_ACTION（action_type=visit/game/train/commission/furniture）
   - Events.DORM_GIFT（hero_id, furniture_id）
5. 导出 SILENT_STUB_CMDS 供 operations.py 集中注册多人与未实现协议静默桩。

本模块不包含任何 @operation 声明，所有网络协议控制器入口统一收归至 operations.py 做轻量托管转发。
"""

import os
import json
import math
import time
import random as _random
import logging

_BACKHOME_HERO_CFG = None

def _get_backhome_hero_cfg():
    global _BACKHOME_HERO_CFG
    if _BACKHOME_HERO_CFG is None:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backhome_hero_cfg.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    _BACKHOME_HERO_CFG = json.load(f)
            except Exception:
                _BACKHOME_HERO_CFG = {}
        else:
            _BACKHOME_HERO_CFG = {}
    return _BACKHOME_HERO_CFG

from core import OperationError
from codec import encode
from event_bus import bus, Events

logger = logging.getLogger("backhome_service")


CANTEEN_TASK_CFG = {
    1: {'level': 1, 'rewards': [[51010, 120], [51023, 120], [51024, 360], [41701, 3]]},
    3: {'level': 1, 'rewards': [[51011, 120], [51019, 120], [51025, 360], [41701, 3]]},
    5: {'level': 1, 'rewards': [[51012, 120], [51020, 120], [51026, 360], [41701, 3]]},
    7: {'level': 1, 'rewards': [[51013, 240], [51017, 60], [51022, 120], [41701, 3]]},
    9: {'level': 1, 'rewards': [[51014, 240], [51018, 60], [51022, 120], [41701, 3]]},
    11: {'level': 1, 'rewards': [[51015, 240], [51021, 60], [51022, 120], [41701, 3]]},
    13: {'level': 1, 'rewards': [[51016, 240], [51021, 60], [51022, 120], [41701, 3]]},
    15: {'level': 1, 'rewards': [[51013, 240], [51017, 60], [51022, 120], [41701, 3]]},
    20001: {'level': 2, 'rewards': [[51010, 160], [51023, 160], [51024, 360], [41701, 3]]},
    20003: {'level': 2, 'rewards': [[51011, 160], [51019, 160], [51025, 360], [41701, 3]]},
    20005: {'level': 2, 'rewards': [[51012, 160], [51020, 160], [51026, 360], [41701, 3]]},
    20007: {'level': 2, 'rewards': [[51013, 300], [51017, 80], [51022, 180], [41701, 3]]},
    20009: {'level': 2, 'rewards': [[51014, 300], [51018, 80], [51022, 180], [41701, 3]]},
    20011: {'level': 2, 'rewards': [[51015, 300], [51021, 80], [51022, 180], [41701, 3]]},
    20013: {'level': 2, 'rewards': [[51016, 300], [51021, 80], [51022, 180], [41701, 3]]},
    20015: {'level': 2, 'rewards': [[51013, 300], [51017, 80], [51022, 180], [41701, 3]]},
    30001: {'level': 3, 'rewards': [[51010, 180], [51023, 180], [51024, 400], [41701, 3]]},
    30003: {'level': 3, 'rewards': [[51011, 180], [51019, 180], [51025, 400], [41701, 3]]},
    30005: {'level': 3, 'rewards': [[51012, 180], [51020, 180], [51026, 400], [41701, 3]]},
    30007: {'level': 3, 'rewards': [[51013, 300], [51017, 120], [51022, 180], [41701, 3]]},
    30009: {'level': 3, 'rewards': [[51014, 300], [51018, 120], [51022, 180], [41701, 3]]},
    30011: {'level': 3, 'rewards': [[51015, 300], [51021, 120], [51022, 180], [41701, 3]]},
    30013: {'level': 3, 'rewards': [[51016, 300], [51021, 120], [51022, 180], [41701, 3]]},
    30015: {'level': 3, 'rewards': [[51013, 300], [51017, 120], [51022, 180], [41701, 3]]},
}

ALL_CANTEEN_TASK_IDS = list(CANTEEN_TASK_CFG.keys())

def _pick_random_task():
    return _random.choice(ALL_CANTEEN_TASK_IDS)


# 食堂设施升级消耗配置（物品 2 为金币）
# 厨具 1~4 (941001~941004)：升 2 级 50,000，升 3 级 80,000，上限 3 级
# 餐桌 10~17 (941010~941017)：升 1 级 10,000，升 2 级 20,000，升 3 级 40,000，上限 3 级
CANTEEN_FUR_UPGRADE_COSTS = {
    1: {2: 50000, 3: 80000},
    2: {2: 50000, 3: 80000},
    3: {2: 50000, 3: 80000},
    4: {2: 50000, 3: 80000},
    941001: {2: 50000, 3: 80000},
    941002: {2: 50000, 3: 80000},
    941003: {2: 50000, 3: 80000},
    941004: {2: 50000, 3: 80000},
    10: {1: 10000, 2: 20000, 3: 40000},
    11: {1: 10000, 2: 20000, 3: 40000},
    12: {1: 10000, 2: 20000, 3: 40000},
    13: {1: 10000, 2: 20000, 3: 40000},
    14: {1: 10000, 2: 20000, 3: 40000},
    15: {1: 10000, 2: 20000, 3: 40000},
    16: {1: 10000, 2: 20000, 3: 40000},
    17: {1: 10000, 2: 20000, 3: 40000},
    941010: {1: 10000, 2: 20000, 3: 40000},
    941011: {1: 10000, 2: 20000, 3: 40000},
    941012: {1: 10000, 2: 20000, 3: 40000},
    941013: {1: 10000, 2: 20000, 3: 40000},
    941014: {1: 10000, 2: 20000, 3: 40000},
    941015: {1: 10000, 2: 20000, 3: 40000},
    941016: {1: 10000, 2: 20000, 3: 40000},
    941017: {1: 10000, 2: 20000, 3: 40000},
}


# 游园街贴票任务积分 33 位修正者碎片奖励池 (取自 IdolTraineeRewardRankCfg)
IDOL_TRAINEE_HERO_PIECE_POOL = [
    11075, 11076, 11074, 11055, 11049, 11158, 11060, 11061, 11150, 11015,
    11066, 11093, 11013, 11058, 11139, 11032, 11132, 11127, 11119, 11067,
    11070, 11071, 11072, 11052, 11138, 11081, 11199, 11111, 11042, 11094,
    11041, 11024, 11028
]
IDOL_TRAINEE_RANK_POINTS = {1: 100, 2: 200, 3: 300, 4: 400}


class BackHomeService:
    """游园街/后宅/食堂领域服务（单例）：业务逻辑、登录帧、时间推演。"""

    _instance = None

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._pending_cricket_battles = {}

    # ---- 登录洪流动态帧（generator.py 58001/58003/58027 委托）----

    def get_login_concise_payload(self, uid, db):
        """sc_58001 游园街登录简要（41 档案英雄疲劳度、岗位、在售菜品、委托）。"""
        if hasattr(db, 'calculate_dorm_hero_fatigue_recovery'):
            try:
                db.calculate_dorm_hero_fatigue_recovery(uid)
            except Exception as e:
                logger.debug(f"[BackHome] 登录前疲劳恢复计算跳过: {e}")
        return encode("sc_58001", db.get_backhome_concise(uid))

    def get_detail_payload(self, uid, db):
        """sc_58003 游园街全量数据驱动。"""
        return encode("sc_58003", db.get_backhome_detail(uid))

    def get_fatigue_push_payload(self, uid, db):
        """sc_58027 后宅英雄疲劳度推送。"""
        if hasattr(db, 'ensure_all_backhome_heroes'):
            try:
                db.ensure_all_backhome_heroes(uid)
            except Exception:
                pass
        if hasattr(db, 'calculate_dorm_hero_fatigue_recovery'):
            try:
                db.calculate_dorm_hero_fatigue_recovery(uid)
            except Exception as e:
                logger.debug(f"[BackHome] 推送前疲劳恢复计算跳过: {e}")
        rows = db.query("SELECT archives_id, fatigue FROM backhome_hero WHERE uid=?", (uid,))
        fatigue_list = [{"archives_id": r["archives_id"], "fatigue": r["fatigue"]} for r in (rows or [])]
        return encode("sc_58027", {"fatigue_list": fatigue_list})

    def _rand_task_id(self, ctx):
        """抽取委托：优先按全表 task_id 随机，兜底按 CFG。"""
        try:
            ids = [r["task_id"] for r in ctx.db.query("SELECT task_id FROM backhome_canteen_task")]
            if ids:
                return int(_random.choice(ids))
        except Exception:
            pass
        return _pick_random_task()

    # ---- 34 项业务操作领域逻辑（供 operations.py 托管转发）----
    def get_detail(self, ctx, uid, data=None):
        """58002 [Fix by Gemini 3.7-flash] 进入家园系统拉取全量详情："""
        if data is None:
            data = {}

        ctx.uid = uid
        bh_data = ctx.db.get_backhome_detail(uid) if ctx.db else {}
        return bh_data

    def batch_send_task_dispatch(self, ctx, uid, data=None):
        """58100 [Fix by Gemini 3.7-flash] 委托一键派遣："""
        if data is None:
            data = {}

        ctx.uid = uid
        entrust_list = data.get("entrust_list") or []
        now_ts = int(time.time())
        if ctx.db:
            # 校验是否有被锁定的英雄
            for item in entrust_list:
                for h in (item.get("hero_list") or []):
                    aid = ctx.db.get_archive_id(h)
                    h_rows = ctx.db.query("SELECT is_lock FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
                    if h_rows and int(h_rows[0].get("is_lock") or 0) == 1:
                        return {"result": 210297}

            for item in entrust_list:
                pos = int(item.get("pos") or 1)
                row = ctx.db.query("SELECT task_id FROM backhome_canteen_entrust WHERE uid=? AND pos=?", (uid, pos))
                cur_task = row[0]["task_id"] if row else _pick_random_task()
                heros = ",".join(str(h) for h in (item.get("hero_list") or []))
                duration = int(item.get("duration") or 1200)
                ctx.db.execute("""
                    INSERT OR REPLACE INTO backhome_canteen_entrust (uid, pos, task_id, hero_list, num_max, refresh_times, start_time, duration, update_ts)
                    VALUES (?, ?, ?, ?, 3, 0, ?, ?, ?)
                """, (uid, pos, cur_task, heros, now_ts, duration, now_ts))
            if entrust_list:
                _emit_dorm_action(ctx, uid, "commission", times=len(entrust_list))
        return {"result": 0}

    def send_task_dispatch(self, ctx, uid, data=None):
        """58102 [Fix by Gemini 3.7-flash] 单个委托派遣/撤回："""
        if data is None:
            data = {}

        ctx.uid = uid
        pos = int(data.get("pos") or 1)
        hero_list = data.get("hero_list")
        duration = int(data.get("duration") or 1200)
        now_ts = int(time.time())
        if ctx.db:
            if hero_list is not None and len(hero_list) > 0:
                for h in hero_list:
                    aid = ctx.db.get_archive_id(h)
                    h_rows = ctx.db.query("SELECT is_lock FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
                    if h_rows and int(h_rows[0].get("is_lock") or 0) == 1:
                        return {"result": 210297}

                row = ctx.db.query("SELECT task_id FROM backhome_canteen_entrust WHERE uid=? AND pos=?", (uid, pos))
                cur_task = row[0]["task_id"] if row else _pick_random_task()
                heros = ",".join(str(h) for h in hero_list)
                ctx.db.execute("""
                    INSERT OR REPLACE INTO backhome_canteen_entrust (uid, pos, task_id, hero_list, num_max, refresh_times, start_time, duration, update_ts)
                    VALUES (?, ?, ?, ?, 3, 0, ?, ?, ?)
                """, (uid, pos, cur_task, heros, now_ts, duration, now_ts))
                _emit_dorm_action(ctx, uid, "commission")
            else:
                # 撤回委托
                ctx.db.execute("""
                    UPDATE backhome_canteen_entrust SET hero_list='', start_time=0 WHERE uid=? AND pos=?
                """, (uid, pos))
        return {"result": 0}

    def refresh_entrust(self, ctx, uid, data=None):
        """58012 [Fix by Gemini 3.7-flash] 刷新委托："""
        if data is None:
            data = {}

        ctx.uid = uid
        pos = int(data.get("pos") or 1)
        task_id = _pick_random_task()
        now_ts = int(time.time())
        cur_ref = 0
        if ctx.db:
            row = ctx.db.query("SELECT refresh_times FROM backhome_canteen_entrust WHERE uid=? AND pos=?", (uid, pos))
            if row:
                cur_ref = (row[0]["refresh_times"] or 0) + 1
            ctx.db.execute("""
                INSERT OR REPLACE INTO backhome_canteen_entrust (uid, pos, task_id, hero_list, num_max, refresh_times, start_time, duration, update_ts)
                VALUES (?, ?, ?, '', 3, ?, 0, 1200, ?)
            """, (uid, pos, task_id, cur_ref, now_ts))
            _emit_dorm_action(ctx, uid, "commission")
        return {"pos": pos, "task_id": task_id, "refresh_times": cur_ref}

    def unlock_entrust(self, ctx, uid, data=None):
        """58024 [Fix by Gemini 3.7-flash] 解锁委托槽位："""
        if data is None:
            data = {}

        ctx.uid = uid
        pos = int(data.get("pos") or 1)
        task_id = _pick_random_task()
        now_ts = int(time.time())
        if ctx.db:
            ctx.db.execute("""
                INSERT OR REPLACE INTO backhome_canteen_entrust (uid, pos, task_id, hero_list, num_max, refresh_times, start_time, duration, update_ts)
                VALUES (?, ?, ?, '', 3, 0, 0, 1200, ?)
            """, (uid, pos, task_id, now_ts))
            _emit_dorm_action(ctx, uid, "commission")
        return {"pos": pos, "task_id": task_id}

    def get_visit(self, ctx, uid, data=None):
        """58058 [Fix by Gemini 3.7-flash] 进入后宅房间拉取拜访数据与礼物："""
        if data is None:
            data = {}

        ctx.uid = uid
        return {"result": 0, "visited_user_list": [], "is_have_gift": False}

    def get_reward(self, ctx, uid, data=None):
        """58060 [Fix by Gemini 3.7-flash] 领取后宅被拜访礼物奖励："""
        if data is None:
            data = {}

        ctx.uid = uid
        return {"result": 0, "be_visited_reward_list": []}

    def hero_lock(self, ctx, uid, data=None):
        """58218 [Fix by Gemini 3.7-flash] 锁定/解锁后宅英雄："""
        if data is None:
            data = {}

        ctx.uid = uid
        hid = int(data.get("hero_id") or 0)
        ltype = int(data.get("type") or 0)
        if ctx.db and hid:
            aid = ctx.db.get_archive_id(hid)
            if ltype == 1:
                # 检查是否在食堂担任职位
                car_rows = ctx.db.query("SELECT ctype, hero_id FROM backhome_canteen_career WHERE uid=?", (uid,))
                for r in car_rows:
                    if r.get("hero_id") and ctx.db.get_archive_id(r["hero_id"]) == aid:
                        return {"result": 7130, "hero_id": hid, "type": ltype}
                # 检查是否在委托派遣中
                ent_rows = ctx.db.query("SELECT hero_list FROM backhome_canteen_entrust WHERE uid=?", (uid,))
                for r in ent_rows:
                    hlist = [int(x) for x in r["hero_list"].split(",") if x] if r.get("hero_list") else []
                    for eh in hlist:
                        if ctx.db.get_archive_id(eh) == aid:
                            return {"result": 7130, "hero_id": hid, "type": ltype}

            ctx.db.set_backhome_hero_lock(uid, hid, ltype)
        return {"result": 0, "hero_id": hid, "type": ltype}

    def set_fur_list_in_map(self, ctx, uid, data=None):
        """58010 [Fix by Gemini 3.7-flash] 保存宿舍房间3D家具摆放布局："""
        if data is None:
            data = {}

        ctx.uid = uid
        did = int(data.get("architecture_id") or 0)
        layout = data.get("furniture_layout") or {}
        if ctx.db and did:
            ctx.db.save_dorm_layout(uid, did, layout)
            _emit_dorm_action(ctx, uid, "furniture")
        return {"architecture_id": did}

    def unlock_dorm_architecture(self, ctx, uid, data=None):
        """58130 [Fix by Gemini 3.7-flash] 解锁新宿舍房间："""
        if data is None:
            data = {}

        ctx.uid = uid
        did = int(data.get("architecture_id") or 0)
        pos = int(data.get("pos_id") or 0)
        rem_mat = ctx.db.unlock_dorm(uid, did, pos) if (ctx.db and did) else None
        if rem_mat is not None:
            _emit_dorm_action(ctx, uid, "furniture")
        return {"architecture_id": did, "pos_id": pos, "rem_mat": rem_mat}

    def deploy_hero_in_room(self, ctx, uid, data=None):
        """58132 [Fix by Gemini 3.7-flash] 安排英雄入住房间："""
        if data is None:
            data = {}

        ctx.uid = uid
        did = int(data.get("architecture_id") or 0)
        hids = data.get("hero_id") or []
        if not isinstance(hids, list):
            hids = [hids]
        hids = [int(x) for x in hids if x]
        res = 0
        if ctx.db and did and hids:
            res = ctx.db.deploy_dorm_hero(uid, did, hids) or 0
            if res == 0:
                _emit_dorm_action(ctx, uid, "furniture")
        return {"architecture_id": did, "hero_ids": hids, "result": res}

    def recall_hero_in_private_dorm(self, ctx, uid, data=None):
        """58134 [Fix by Gemini 3.7-flash] 召回私人宿舍英雄："""
        if data is None:
            data = {}

        ctx.uid = uid
        did = int(data.get("architecture_id") or 0)
        hid = int(data.get("hero_id") or 0)
        if ctx.db and did and hid:
            ctx.db.recall_dorm_hero(uid, did, hid)
            _emit_dorm_action(ctx, uid, "furniture")
        return {"architecture_id": did, "hero_id": hid}

    def gift_fur_to_hero(self, ctx, uid, data=None):
        """58136 [Fix by Gemini 3.7-flash] 赠送专属家具（提升宿舍好感经验）："""
        if data is None:
            data = {}

        ctx.uid = uid
        hid = int(data.get("hero_id") or 0)
        furs = data.get("furniture") or []
        if not isinstance(furs, list):
            furs = [furs]
        if ctx.db and hid:
            ctx.db.gift_furniture_to_hero(uid, hid, furs)
            _emit_dorm_action(ctx, uid, "furniture")
            first_fur = furs[0] if furs else {}
            _emit_dorm_gift(ctx, uid, hid,
                            furniture_id=int((first_fur or {}).get("furniture_id") or (first_fur or {}).get("id") or 0))
        return {"hero_id": hid, "furniture": furs}

    def gift_food_to_hero(self, ctx, uid, data=None):
        """58138 [Fix by Gemini 3.7-flash] 给英雄喂食（恢复疲劳度至 120）："""
        if data is None:
            data = {}

        ctx.uid = uid
        ftype = int(data.get("type") or 1)
        hid = int(data.get("hero_id") or 0)
        fatigue_list = ctx.db.feed_dorm_hero(uid, ftype, hid) if ctx.db else []
        if fatigue_list:
            _emit_dorm_action(ctx, uid, "feed", times=max(1, len(fatigue_list)))
        return {"type": ftype, "hero_id": hid, "fatigue_list": fatigue_list}

    def set_hero_skin(self, ctx, uid, data=None):
        """58126 [Fix by Gemini 3.7-flash] 更换后宅英雄皮肤："""
        if data is None:
            data = {}

        ctx.uid = uid
        hid = int(data.get("hero_id") or 0)
        sid = int(data.get("skin_id") or 0)
        if ctx.db and hid:
            ctx.db.set_dorm_hero_skin(uid, hid, sid)
            _emit_dorm_action(ctx, uid, "furniture")
        return {"hero_id": hid, "skin_id": sid}

    def revise_private_dorm_pos(self, ctx, uid, data=None):
        """58146 [Fix by Gemini 3.7-flash] 修改私人宿舍门牌位置："""
        if data is None:
            data = {}

        ctx.uid = uid
        dlist = data.get("dorm_pos_list") or []
        if ctx.db and dlist:
            ctx.db.revise_dorm_positions(uid, dlist)
            _emit_dorm_action(ctx, uid, "furniture")
        return {"dorm_pos_list": dlist}

    def set_character_job(self, ctx, uid, data=None):
        """58104 [Fix by Gemini 3.7-flash] 设置餐厅岗位安排（厨师/服务员/收银）："""
        if data is None:
            data = {}

        ctx.uid = uid
        ctype = int(data.get("type") or 1)
        hid = int(data.get("hero_id") or 0)
        if ctx.db:
            if hid > 0:
                aid = ctx.db.get_archive_id(hid)
                h_rows = ctx.db.query("SELECT is_lock FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
                if h_rows and int(h_rows[0].get("is_lock") or 0) == 1:
                    return {"result": 210297, "type": ctype, "hero_id": hid}
            ctx.db.set_canteen_career(uid, ctype, hid)
            _emit_dorm_action(ctx, uid, "commission")
        return {"result": 0, "type": ctype, "hero_id": hid}

    def receive_canteen_auto_award(self, ctx, uid, data=None):
        """58106 58106 领取食堂营业收入：cs_58106 {architecture_id} → sc_58107 {result, earnings}。"""
        if data is None:
            data = {}

        ctx.uid = uid
        now_ts = int(time.time())
        earnings = 0
        if ctx.db:
            earnings = min(14000, max(0, ctx.db.settle_canteen_earnings(uid)))
            if earnings > 0:
                ctx.db.add_currency(uid, 39, earnings)
                if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
                    ctx.touched_items.add(39)
                _emit_dorm_action(ctx, uid, "canteen_award", times=1)
                bus.emit(Events.ACTIVITY_POINT_GAIN, ctx, uid, pt_id=39, amount=earnings, item_id=39)
            ctx.db.execute(
                "UPDATE backhome_canteen_meta "
                "SET pending_earnings=MAX(0, COALESCE(pending_earnings,0)-?), "
                "    accruing_earnings=COALESCE(accruing_earnings,0)+?, "
                "    last_receive_earnings_time=? "
                "WHERE uid=?",
                (earnings, earnings, now_ts, uid))
        return {"result": 0, "earnings": earnings}

    def send_sign_food(self, ctx, uid, data=None):
        """58114 [Fix by Gemini 3.7-flash] 上架/下架/修改在售招牌菜品："""
        if data is None:
            data = {}

        ctx.uid = uid
        fid = int(data.get("food_id") or 0)
        snum = int(data.get("sell_num") or 0)
        now_ts = int(time.time())
        if ctx.db:
            if snum <= 0:
                # 下架菜品
                ctx.db.execute("DELETE FROM backhome_canteen_dish WHERE uid=? AND food_id=?", (uid, fid))
            else:
                # 上架 / 修改菜品售卖数量
                old = ctx.db.query("SELECT sold_num, sell_earnings FROM backhome_canteen_dish WHERE uid=? AND food_id=?", (uid, fid))
                sold = old[0]["sold_num"] if old else 0
                earn = old[0]["sell_earnings"] if old else 0
                ctx.db.execute("""
                    INSERT OR REPLACE INTO backhome_canteen_dish (uid, food_id, sell_num, sold_num, sell_earnings, update_ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (uid, fid, snum, sold, earn, now_ts))
        return {"food_id": fid, "sell_num": snum}

    _canteen_food_cfg_cache = None

    @classmethod
    def _get_canteen_food_cfg(cls):
        """读取食堂菜品配置缓存 (canteen_food_cfg.json)"""
        if cls._canteen_food_cfg_cache is None:
            cfg_path = os.path.join(os.path.dirname(__file__), "canteen_food_cfg.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cls._canteen_food_cfg_cache = json.load(f)
                except Exception:
                    cls._canteen_food_cfg_cache = {}
            else:
                cls._canteen_food_cfg_cache = {}
        return cls._canteen_food_cfg_cache

    def canteen_fur_upgrade(self, ctx, uid, data=None):
        """58116 升级食堂设施厨具/餐桌：cs_58116 {uid} -> sc_58117 {result}。"""
        if data is None:
            data = {}

        ctx.uid = uid
        fur_id = int(data.get("uid") or 0)
        if not fur_id or not ctx.db:
            return {"result": 0}

        rows = ctx.db.query(
            "SELECT entity_id, type_id, level FROM backhome_canteen_furniture WHERE uid=? AND (entity_id=? OR type_id=?)",
            (uid, fur_id, fur_id)
        )
        if not rows:
            entity_id = fur_id if fur_id < 100 else (fur_id - 941000)
            type_id = (941000 + entity_id) if fur_id < 100 else fur_id
            cur_level = 0
            ctx.db.execute(
                "INSERT OR IGNORE INTO backhome_canteen_furniture (uid, entity_id, type_id, level, update_ts) VALUES (?, ?, ?, ?, ?)",
                (uid, entity_id, type_id, 0, int(time.time()))
            )
        else:
            entity_id = int(rows[0]["entity_id"])
            type_id = int(rows[0]["type_id"])
            cur_level = int(rows[0]["level"] or 0)

        next_level = cur_level + 1
        if next_level > 3:
            return {"result": 0}  # 已达满级上限

        cost_map = CANTEEN_FUR_UPGRADE_COSTS.get(entity_id) or CANTEEN_FUR_UPGRADE_COSTS.get(type_id) or {}
        cost_gold = cost_map.get(next_level, 0)

        if cost_gold > 0:
            from inventory_service import InventoryService
            if not InventoryService.cost_item(ctx, uid, 2, cost_gold):
                return {"result": 13002}  # 金币不足

        now_ts = int(time.time())
        ctx.db.execute("""
            UPDATE backhome_canteen_furniture
            SET level = ?, update_ts = ?
            WHERE uid = ? AND entity_id = ?
        """, (next_level, now_ts, uid, entity_id))

        _emit_dorm_action(ctx, uid, "canteen")
        return {"result": 0, "uid": fur_id, "level": next_level}

    def canteen_manual_settlement(self, ctx, uid, data=None):
        """58120 手动烹饪做菜结算：cs_58120 {architecture_id, oper_list} -> sc_58121 {result}。"""
        if data is None:
            data = {}

        ctx.uid = uid
        oper_list = data.get("oper_list") or []
        if not oper_list or not ctx.db:
            return {"result": 0}

        food_cfg = self._get_canteen_food_cfg()
        total_income = 0
        now_ts = int(time.time())

        # 品质 1~5 对应单价评价乘数：50%, 70%, 100%, 120%, 130%
        eval_multipliers = {1: 0.5, 2: 0.7, 3: 1.0, 4: 1.2, 5: 1.3}

        from collections import defaultdict
        ing_cost_totals = defaultdict(int)
        dish_stats = defaultdict(lambda: {"sold": 0, "earnings": 0})

        for oper in oper_list:
            fid = int(oper.get("food_id") or 0)
            quality = int(oper.get("quality") or 3)
            finfo = food_cfg.get(str(fid)) or food_cfg.get(fid) or {}
            base_sell = int(finfo.get("sell") or 0)
            mult = eval_multipliers.get(quality, 1.0)
            income = int(base_sell * mult)
            total_income += income

            dish_stats[fid]["sold"] += 1
            dish_stats[fid]["earnings"] += income

            for ing in finfo.get("ingredient_list", []):
                if len(ing) >= 2:
                    ing_cost_totals[int(ing[0])] += int(ing[1])

        # 扣减烹饪消耗食材
        from inventory_service import InventoryService
        for ing_id, cnt in ing_cost_totals.items():
            InventoryService.cost_item(ctx, uid, ing_id, cnt)

        # 游园币入账 (货币 ID 31) 并更新累计收益
        if total_income > 0:
            ctx.db.add_currency(uid, 31, total_income)
            ctx.db.execute("""
                UPDATE backhome_canteen_meta
                SET accruing_earnings = COALESCE(accruing_earnings, 0) + ?, update_ts = ?
                WHERE uid = ?
            """, (total_income, now_ts, uid))

        # 更新菜品累计售卖数量与收入 (backhome_canteen_dish)
        for fid, stat in dish_stats.items():
            old = ctx.db.query("SELECT sell_num, sold_num, sell_earnings FROM backhome_canteen_dish WHERE uid=? AND food_id=?", (uid, fid))
            if old:
                ctx.db.execute("""
                    UPDATE backhome_canteen_dish
                    SET sold_num = sold_num + ?, sell_earnings = sell_earnings + ?, update_ts = ?
                    WHERE uid = ? AND food_id = ?
                """, (stat["sold"], stat["earnings"], now_ts, uid, fid))
            else:
                ctx.db.execute("""
                    INSERT INTO backhome_canteen_dish (uid, food_id, sell_num, sold_num, sell_earnings, update_ts)
                    VALUES (?, ?, 0, ?, ?, ?)
                """, (uid, fid, stat["sold"], stat["earnings"], now_ts))

        _emit_dorm_action(ctx, uid, "canteen")
        return {"result": 0, "income": total_income}

    def save_fur_template(self, ctx, uid, data=None):
        """58040 [Phase 1] 保存预设家具方案模板："""
        if data is None:
            data = {}

        ctx.uid = uid
        tid = int(data.get("id") or 1)
        name = str(data.get("name") or "预设模板")
        t_type = int(data.get("type") or 1)
        arch_id = int(data.get("architecture_id") or 0)
        pos = int(data.get("pos") or 0)
        fur_pos_list = data.get("furniture_pos_list") or []

        # [Enhance] 若客户端未直接上传 furniture_pos_list 且指定了房间 architecture_id，
        # 则从该房间当前保存的 3D 布局中提取 furniture_pos_list 继承给模板
        if not fur_pos_list and arch_id > 0 and ctx.db:
            try:
                row = ctx.db.query("SELECT layout_json FROM backhome_dorm_layout WHERE uid=? AND dorm_id=?", (uid, arch_id))
                if row and row[0].get("layout_json"):
                    lj = json.loads(row[0]["layout_json"])
                    if isinstance(lj, dict):
                        fur_pos_list = lj.get("furniture_pos_list") or []
                        if not fur_pos_list and lj.get("temp_id"):
                            t_row = ctx.db.query("SELECT layout_json FROM backhome_dorm_template WHERE uid=? AND template_id=?", (uid, int(lj["temp_id"])))
                            if t_row and t_row[0].get("layout_json"):
                                t_lj = json.loads(t_row[0]["layout_json"])
                                if isinstance(t_lj, dict):
                                    fur_pos_list = t_lj.get("furniture_pos_list") or []
                                elif isinstance(t_lj, list):
                                    fur_pos_list = t_lj
                    elif isinstance(lj, list):
                        fur_pos_list = lj
            except Exception:
                pass

        if ctx.db:
            ctx.db.save_dorm_template(uid, tid, name, t_type, arch_id, pos, fur_pos_list)
            _emit_dorm_action(ctx, uid, "furniture")
        return {"result": 0, "id": tid, "name": name}

    def revise_fur_template_name(self, ctx, uid, data=None):
        """58142 [Phase 1] 重命名家具模板："""
        if data is None:
            data = {}

        ctx.uid = uid
        tid = int(data.get("id") or 0)
        name = str(data.get("name") or "")
        if ctx.db and tid and name:
            ctx.db.revise_dorm_template_name(uid, tid, name)
            _emit_dorm_action(ctx, uid, "furniture")
        return {"result": 0}

    def delete_fur_template(self, ctx, uid, data=None):
        """58144 [Phase 1] 删除家具模板："""
        if data is None:
            data = {}

        ctx.uid = uid
        tid = int(data.get("id") or 0)
        if ctx.db and tid:
            ctx.db.delete_dorm_template(uid, tid)
            _emit_dorm_action(ctx, uid, "furniture")
        return {"result": 0}

    def ask_fur_template_exhibit(self, ctx, uid, data=None):
        """58148 [Phase 1] 请求展示家具模板列表："""
        if data is None:
            data = {}

        ctx.uid = uid
        _emit_dorm_action(ctx, uid, "furniture")
        return {"result": 0, "exhibition_brief": []}

    def set_fur_template_exhibit(self, ctx, uid, data=None):
        """58152 [Fix by Gemini 3.7-flash] 设置展示家具模板："""
        if data is None:
            data = {}

        ctx.uid = uid
        _emit_dorm_action(ctx, uid, "furniture")
        return {"result": 0}

    def set_fur_template_can_save(self, ctx, uid, data=None):
        """58158 [Fix by Gemini 3.7-flash] 设置家具模板是否允许他人保存："""
        if data is None:
            data = {}

        ctx.uid = uid
        _emit_dorm_action(ctx, uid, "furniture")
        return {"result": 0}

    def watch_theatre(self, ctx, uid, data=None):
        """58128 [Fix by Gemini 3.7-flash] 观看生日小剧场："""
        if data is None:
            data = {}

        ctx.uid = uid
        _emit_dorm_action(ctx, uid, "game")
        return {"theatrical_id": int(data.get("theatrical_id") or 0)}

    def settlement_rhythm_game(self, ctx, uid, data=None):
        """58154 音游小游戏结算：cs_58154 -> sc_58155"""
        if data is None:
            data = {}

        ctx.uid = uid
        hid = int(data.get("hero_id") or 0)
        aid = ctx.db.get_archive_id(hid) if (ctx.db and hid) else (hid or 1084)
        now_ts = int(time.time())

        # 1. 查找当前角色疲劳度
        cur_fatigue = 120
        if ctx.db and aid:
            row = ctx.db.query("SELECT fatigue FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
            if row:
                cur_fatigue = int(row[0]["fatigue"])

        # 2. 每日奖励与体力消耗保护机制：
        # 每日（以 05:00 游戏日为界）首次结算：扣除 10 点疲劳度并获得 1000 个乐园戳印 (Item 39)；
        # 当日已获取过乐园戳印后：不再消耗体力（消耗为 0），也不再重复发放戳印，直到次日 05:00 刷新。
        from lazy_timer import get_daily_5am_ts
        cur_5am = get_daily_5am_ts(now_ts)
        already_claimed = False
        if ctx.db:
            c_rows = ctx.db.query(
                "SELECT 1 FROM claim_ledger WHERE uid=? AND kind='rhythm_game_daily' AND key_id=?",
                (uid, cur_5am)
            )
            if c_rows:
                already_claimed = True

        reward_list = []
        if not already_claimed:
            # 当日首次：扣除 10 体力，发放 1000 戳印并记录已领
            COST = 10
            new_fatigue = max(0, cur_fatigue - COST)
            if ctx.db and aid:
                ctx.db.execute("UPDATE backhome_hero SET fatigue=?, update_ts=? WHERE uid=? AND archives_id=?",
                               (new_fatigue, now_ts, uid, aid))

            REWARD_NUM = 1000
            from operations import _item_add
            _item_add(ctx, uid, 39, REWARD_NUM)
            reward_list = [{"id": 39, "num": REWARD_NUM}]

            if ctx.db:
                ctx.db.execute(
                    "INSERT OR REPLACE INTO claim_ledger (uid, kind, key_id, claim_ts) VALUES (?, 'rhythm_game_daily', ?, ?)",
                    (uid, cur_5am, now_ts)
                )
        else:
            # 当日已获取过乐园戳印：保护机制生效，不再消耗体力
            new_fatigue = cur_fatigue

        _emit_dorm_action(ctx, uid, "game")
        return {
            "result": 0,
            "fatigue": new_fatigue,
            "reward_list": reward_list
        }

    def deploy_hero_in_camp(self, ctx, uid, data=None):
        """58174 偶像/虫虫养成营英雄入营/站位设置：cs_58174 -> sc_58175"""
        if data is None:
            data = {}

        ctx.uid = uid
        pos_list = data.get("hero_pos_list") or []
        now_ts = int(time.time())
        if ctx.db:
            ctx.db.execute("DELETE FROM idol_trainee_pos WHERE uid=?", (uid,))
            for item in pos_list:
                pos = int(item.get("pos") or 0)
                hid = int(item.get("hero_id") or 0)
                if pos and hid:
                    ctx.db.execute("""
                        INSERT INTO idol_trainee_pos (uid, pos, hero_id, update_ts)
                        VALUES (?, ?, ?, ?)
                    """, (uid, pos, hid, now_ts))
        return {"result": 0}

    def save_dance_diy(self, ctx, uid, data=None):
        """58198 保存 DIY 舞蹈序列：cs_58198 -> sc_58199"""
        if data is None:
            data = {}
        ctx.uid = uid
        seq = data.get("sequence") or {}
        pos = int(seq.get("pos") or 1)
        base_seq = seq.get("base_sequence") or {}
        scene_id = int(base_seq.get("scene_id") or 0)
        music_id = int(base_seq.get("music_id") or 0)
        action_id_list = base_seq.get("action_id_list") or []
        if isinstance(action_id_list, list):
            action_list_str = ",".join(str(x) for x in action_id_list)
        else:
            action_list_str = str(action_id_list)
        now_ts = int(time.time())
        if ctx.db:
            ctx.db.execute("""
                INSERT INTO dance_diy (uid, pos, scene_id, music_id, action_list, update_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid, pos) DO UPDATE SET
                    scene_id = excluded.scene_id,
                    music_id = excluded.music_id,
                    action_list = excluded.action_list,
                    update_ts = excluded.update_ts
            """, (uid, pos, scene_id, music_id, action_list_str, now_ts))
        return {"result": 0}

    def delete_dance_diy(self, ctx, uid, data=None):
        """58200 删除 DIY 舞蹈序列：cs_58200 -> sc_58201"""
        if data is None:
            data = {}
        ctx.uid = uid
        pos = int(data.get("pos") or 0)
        if ctx.db and pos:
            ctx.db.execute("DELETE FROM dance_diy WHERE uid=? AND pos=?", (uid, pos))
        return {"result": 0}

    def get_dance_diy_payload(self, uid, db):
        """生成 sc_58195 练舞房 DIY 舞蹈序列列表下行帧。"""
        rows = db.query("SELECT pos, scene_id, music_id, action_list FROM dance_diy WHERE uid=? ORDER BY pos ASC", (uid,)) if db else []
        sequence_list = []
        for r in rows:
            act_ids = [int(x.strip()) for x in (r.get("action_list") or "").split(",") if x.strip().isdigit()]
            sequence_list.append({
                "pos": int(r["pos"]),
                "base_sequence": {
                    "music_id": int(r.get("music_id") or 0),
                    "scene_id": int(r.get("scene_id") or 0),
                    "action_id_list": act_ids
                }
            })
        return encode("sc_58195", {"sequence_list": sequence_list, "shared_sequence_list": []})

    def get_idol_trainee_overview_payload(self, uid, db):
        """生成 sc_58169 练舞房总览（站位、训练次数、属性、阵容）下行帧。"""
        if not db:
            return None
        row = db.query("SELECT * FROM idol_trainee WHERE uid=?", (uid,))
        it_data = row[0] if row else {}
        attack_hero_id = int(it_data.get("attack_hero_id") or 1084)
        defend_hero_id = int(it_data.get("defend_hero_id") or 1084)
        use_times = int(it_data.get("exercise_use_times") or 0)
        camp_list_str = it_data.get("camp_list") or ""
        camp_list = [int(x.strip()) for x in camp_list_str.split(",") if x.strip().isdigit()]
        pvp_stage_id = int(it_data.get("pvp_stage_id") or 10013)
        pvp_refresh_ts = int(it_data.get("pvp_refresh_ts") or 1786482000)

        hero_rows = db.query("SELECT hero_id, attr1, attr2, attr3, attr4, attr5 FROM idol_trainee_hero WHERE uid=?", (uid,)) or []
        hero_list = []
        for hr in hero_rows:
            hero_list.append({
                "hero_id": int(hr["hero_id"]),
                "attribute_list": [
                    int(hr.get("attr1") or 200),
                    int(hr.get("attr2") or 200),
                    int(hr.get("attr3") or 200),
                    int(hr.get("attr4") or 200),
                    int(hr.get("attr5") or 200),
                ]
            })

        pos_rows = db.query("SELECT pos, hero_id FROM idol_trainee_pos WHERE uid=? ORDER BY pos ASC", (uid,)) or []
        hero_pos_list = []
        for pr in pos_rows:
            if pr["hero_id"]:
                hero_pos_list.append({
                    "pos": int(pr["pos"]),
                    "hero_id": int(pr["hero_id"])
                })

        return encode("sc_58169", {
            "attack_hero_id_list": [attack_hero_id],
            "defend_hero_id_list": [defend_hero_id],
            "hero_list": hero_list,
            "exercise_times_info": {
                "use_times": use_times,
                "camp_list": camp_list
            },
            "pvp_stage_info": {
                "stage_id": pvp_stage_id,
                "refresh_timestamp": pvp_refresh_ts
            },
            "hero_pos_list": hero_pos_list
        })

    def dorm_like(self, ctx, uid, data=None):
        """58052 58052 给他人宿舍点赞：cs_58052 {user_id, architecture_id} → sc_58053 {result}。"""
        if data is None:
            data = {}

        target = int(data.get("user_id") or 0) or uid
        dorm = int(data.get("architecture_id") or 0)
        rows = ctx.db.query(
            "SELECT liked_num FROM backhome_dorm WHERE uid=? AND dorm_id=?", (target, dorm))
        if rows:
            ctx.db.execute(
                "UPDATE backhome_dorm SET liked_num=liked_num+1, update_ts=? WHERE uid=? AND dorm_id=?",
                (int(time.time()), target, dorm))
        else:
            ctx.db.execute(
                "INSERT INTO backhome_dorm (uid, dorm_id, pos_id, exp, liked_num, update_ts) "
                "VALUES (?,?,0,0,1,?)", (target, dorm, int(time.time())))
        return {"target": target, "dorm": dorm}

    def dorm_like_query(self, ctx, uid, data=None):
        """58054 58054 宿舍点赞数查询：cs_58054 {architecture_id} → sc_58055 {result, liked_num, be_visited_num}。"""
        if data is None:
            data = {}

        aid = int(data.get("architecture_id") or data.get("dorm_id") or 0)
        row = ctx.db.query(
            "SELECT liked_num, be_visited_num FROM backhome_dorm WHERE uid=? AND dorm_id=?",
            (uid, aid))
        r = row[0] if row else {}
        return {"aid": aid,
                "liked_num": int(r.get("liked_num") or 0),
                "be_visited_num": int(r.get("be_visited_num") or 0)}

    def canteen_mode(self, ctx, uid, data=None):
        """58108 58108 切换食堂经营模式：cs_58108 {architecture_id, cmd} → sc_58109 {result}。"""
        if data is None:
            data = {}

        mode = int(data.get("cmd") if data.get("cmd") is not None else (data.get("mode") or 0))
        # 表主键是 (uid, canteen_id)，canteen_id 固定 4（schema 注释）
        ctx.db.execute(
            "INSERT INTO backhome_canteen_meta (uid, canteen_id, mode, update_ts) VALUES (?,?,?,?) "
            "ON CONFLICT(uid, canteen_id) DO UPDATE SET mode=excluded.mode, update_ts=excluded.update_ts",
            (uid, 4, mode, int(time.time())))
        return {"mode": mode}

    def canteen_task_submit(self, ctx, uid, data=None):
        """58018 58018 领取食堂委托收益：cs_58018 {architecture_id, pos[]} → sc_58019 {result, extra_reward, entrust, fatigue_list}。"""
        if data is None:
            data = {}

        uid = uid
        pos_list = data.get("pos") or data.get("pos_list") or []
        if isinstance(pos_list, (int, str)):
            pos_list = [pos_list]
        now = int(time.time())
        reward_all, extra_reward, settled, new_entrust = {}, [], [], []

        for pos in pos_list:
            try:
                pos = int(pos)
            except (TypeError, ValueError):
                continue
            erows = ctx.db.query(
                "SELECT * FROM backhome_canteen_entrust WHERE uid=? AND pos=?", (uid, pos))
            if not erows:
                continue
            e = erows[0]
            start = int(e.get("start_time") or 0)
            dur = int(e.get("duration") or 0)
            # duration 是分钟：到期时刻 = start_time + duration*60（客户端同公式）
            if start and dur and now < start + dur * 60:
                raise OperationError(2, f"委托位 {pos} 未到时间（还需 {start + dur * 60 - now}s）")
            crow = ctx.db.query(
                "SELECT * FROM backhome_canteen_task WHERE task_id=?", (e.get("task_id"),))
            if not crow:
                continue
            c = crow[0]
            try:
                base = json.loads(c.get("reward_list") or "[]")
            except Exception:
                base = []
            # 档位倍率：time 列 = {"1":[480,100], "2":[720,140], "3":[1200,210]}（分钟,百分比）
            mult = 1.0
            try:
                tcfg = json.loads(c.get("time") or "{}")
                for _tier, pair in (tcfg or {}).items():
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2 and int(pair[0]) == dur:
                        mult = int(pair[1]) / 100.0
                        break
            except Exception:
                mult = 1.0
            items = []
            for rw in base:
                if isinstance(rw, (list, tuple)) and len(rw) >= 2:
                    items.append([int(rw[0]), int(int(rw[1]) * mult)])
            big = 1 if _random.randint(1, 100) <= min(100, int(c.get("base_success") or 0)) else 0
            if big:
                import math as _mth
                items = [[i, _mth.ceil(n * 1.5)] for i, n in items]
            for iid, num in items:
                if str(iid).startswith("510"):
                    ctx.db.add_ingredient(uid, iid, num)
                else:
                    from operations import _item_add
                    _item_add(ctx, uid, iid, num)
                reward_all[iid] = reward_all.get(iid, 0) + num
            extra_reward.append({
                "pos": pos, "extra_reward": big,
                "reward_list": [{"id": i, "num": n} for i, n in items]})
            # 槽位立即刷新新委托（start_time=0 未开始），随 58019 entrust 下发，幂等由新行天然保证
            next_task = self._rand_task_id(ctx)
            ctx.db.execute(
                "INSERT OR REPLACE INTO backhome_canteen_entrust "
                "(uid, pos, task_id, hero_list, num_max, refresh_times, start_time, duration, update_ts) "
                "VALUES (?,?,?,'',3,0,0,1200,?)",
                (uid, pos, next_task, now))
            new_entrust.append({
                "pos": pos, "id": next_task, "hero_list": [], "tags": [],
                "num_max": 3, "refresh_times": 0, "start_time": 0, "duration": 1200})
            settled.append(pos)

        if not settled:
            raise OperationError(2, "没有可结算的委托位")
        _emit_dorm_action(ctx, uid, "commission_submit", times=len(settled))
        return {"settled": settled, "extra_reward": extra_reward,
                "entrust": new_entrust,
                "rewards": [{"id": i, "num": n} for i, n in sorted(reward_all.items())]}

    def train_hero_property(self, ctx, uid, data=None):
        """58166 训练室培养英雄属性：cs_58166 {hero_id, attribute_index} -> sc_58167 {result, fatigue, attribute_value}。"""
        if data is None:
            data = {}

        ctx.uid = uid
        hid = int(data.get("hero_id") or 0)
        attr_idx = int(data.get("attribute_index") or 1)
        if attr_idx < 1 or attr_idx > 5:
            attr_idx = 1
        now_ts = int(time.time())

        if not ctx.db or not hid:
            return {"result": 1, "fatigue": 0, "attribute_value": 100}

        aid = ctx.db.get_archive_id(hid)
        # 1. 查找英雄疲劳度
        row = ctx.db.query("SELECT fatigue FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
        cur_fatigue = int(row[0]["fatigue"] if row else 140)

        # 2. 读取英雄配置（基准属性与成长上限）
        cfg_all = _get_backhome_hero_cfg()
        hero_cfg = cfg_all.get(str(hid)) or cfg_all.get(str(aid)) or {}
        base_attrs = hero_cfg.get("idol_base_attribute") or []

        if len(base_attrs) >= 5 and isinstance(base_attrs[attr_idx - 1], (list, tuple)):
            base_val = base_attrs[attr_idx - 1][0]
            max_val = base_attrs[attr_idx - 1][1]
        else:
            base_val = 200
            max_val = 1000

        # 3. 查或初始化 idol_trainee_hero
        it_row = ctx.db.query("SELECT * FROM idol_trainee_hero WHERE uid=? AND (hero_id=? OR hero_id=?)", (uid, hid, aid))
        if not it_row:
            b_vals = [entry[0] if (isinstance(entry, (list, tuple)) and len(entry) >= 2) else 200 for entry in base_attrs[:5]]
            while len(b_vals) < 5:
                b_vals.append(200)
            target_hid = hid
            ctx.db.execute("""
                INSERT OR IGNORE INTO idol_trainee_hero (uid, hero_id, attr1, attr2, attr3, attr4, attr5, update_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (uid, target_hid, b_vals[0], b_vals[1], b_vals[2], b_vals[3], b_vals[4], now_ts))
            cur_attr_val = b_vals[attr_idx - 1]
        else:
            target_hid = it_row[0]["hero_id"]
            cur_attr_val = int(it_row[0].get(f"attr{attr_idx}") or base_val)

        # 4. 体力门槛校验（单次培养消耗 20 点体力）
        COST_FATIGUE = 20
        if cur_fatigue < COST_FATIGUE:
            return {"result": 210168, "fatigue": cur_fatigue, "attribute_value": cur_attr_val}

        # 5. 扣减 20 点体力
        new_fatigue = max(0, cur_fatigue - COST_FATIGUE)
        ctx.db.execute("UPDATE backhome_hero SET fatigue=?, update_ts=? WHERE uid=? AND archives_id=?",
                       (new_fatigue, now_ts, uid, aid))

        # 6. 官方成长公式计算增量
        # slot6 = 10 (dorm_idol_hero_exercise_default_value)
        # slot7 = 100 (dorm_idol_hero_exercise_fatigue_addition，体力 >= 40 增益 +100%)
        # slot8 = 0 (阵营加成)
        slot6 = 10
        slot7 = 100 if cur_fatigue >= 40 else 0
        slot8 = 0
        min_gain = int(math.floor(slot6 * (100 + slot7 + slot8) / 10000.0 * 90.0))
        max_gain = int(math.floor(slot6 * (100 + slot7 + slot8) / 10000.0 * 110.0))
        gain = _random.randint(min_gain, max(min_gain, max_gain))
        new_attr_val = min(max_val, cur_attr_val + gain)

        # 7. 更新 idol_trainee_hero
        ctx.db.execute(f"UPDATE idol_trainee_hero SET attr{attr_idx}=?, update_ts=? WHERE uid=? AND hero_id=?",
                       (new_attr_val, now_ts, uid, target_hid))

        # 7.5 累加训练次数并持久化
        new_use_times = 1
        if ctx.db:
            row_it = ctx.db.query("SELECT exercise_use_times FROM idol_trainee WHERE uid=?", (uid,))
            if row_it:
                new_use_times = int(row_it[0]["exercise_use_times"] or 0) + 1
                ctx.db.execute("UPDATE idol_trainee SET exercise_use_times=?, update_ts=? WHERE uid=?",
                               (new_use_times, now_ts, uid))
            else:
                new_use_times = 1
                ctx.db.execute("INSERT INTO idol_trainee (uid, exercise_use_times, update_ts) VALUES (?, ?, ?)",
                               (uid, new_use_times, now_ts))

        # 8. 发送行为通知
        _emit_dorm_action(ctx, uid, "train", times=1, attribute_index=attr_idx)

        return {"result": 0, "fatigue": new_fatigue, "attribute_value": new_attr_val, "use_times": new_use_times}

    def claim_idol_trainee_rank_reward(self, ctx, uid, data=None):
        """58192 领取游园街贴票任务积分档位奖励：
        cs_58192 {id: rank_id, select_list: [{id, num}]} -> sc_58193 {result, get_id_list, reward_list}。

        - 档位 1~3: 消耗贴票门槛 100/200/300，从 33 位英雄碎片池随机抽取 1 位，发放 5 个该英雄碎片；
        - 档位 4: 消耗贴票门槛 400，自选碎片（总数 5，可在池中任意分配，如 3个A + 2个B，或 5个A）；
        - 落库 idol_trainee_rank_got，并回显最新已领档位列表 get_id_list。
        """
        if data is None:
            data = {}
        ctx.uid = uid
        rank_id = int(data.get("id") or 0)
        select_list = data.get("select_list") or []
        db = ctx.db
        now_ts = int(time.time())

        # 1. 档位校验
        if rank_id not in (1, 2, 3, 4):
            return {"result": 2, "get_id_list": [], "reward_list": []}

        # 2. 查询已领档位
        got_rows = db.query("SELECT rank_id FROM idol_trainee_rank_got WHERE uid=? ORDER BY rank_id", (uid,))
        claimed_ranks = [int(r["rank_id"]) for r in (got_rows or [])]
        if rank_id in claimed_ranks:
            return {"result": 402, "get_id_list": claimed_ranks, "reward_list": []}

        # 3. 校验贴票门槛（currency 61）
        need_pt = IDOL_TRAINEE_RANK_POINTS.get(rank_id, 100)
        c_rows = db.query("SELECT num FROM currency WHERE uid=? AND id=61", (uid,))
        cur_ticket = int(c_rows[0]["num"] or 0) if c_rows else 0
        if cur_ticket < need_pt:
            return {"result": 2, "get_id_list": claimed_ranks, "reward_list": []}

        # 4. 计算奖励
        rewards = []
        if rank_id in (1, 2, 3):
            # 随机 1 个英雄碎片，发放 5 个
            chosen_piece = _random.choice(IDOL_TRAINEE_HERO_PIECE_POOL)
            rewards = [{"id": chosen_piece, "num": 5}]
        elif rank_id == 4:
            # 自选碎片：总数 5，支持单选或跨英雄多选
            valid_items = []
            for it in select_list:
                iid = int(it.get("id") or 0)
                num = int(it.get("num") or 0)
                if num > 0 and iid in IDOL_TRAINEE_HERO_PIECE_POOL:
                    valid_items.append({"id": iid, "num": num})
            total_num = sum(x["num"] for x in valid_items)
            if total_num == 5:
                rewards = valid_items
            else:
                # 兜底：若客户端未选满 5 个或未传，随机选 1 位发放 5 个
                chosen_piece = _random.choice(IDOL_TRAINEE_HERO_PIECE_POOL)
                rewards = [{"id": chosen_piece, "num": 5}]

        # 5. 发放奖励入库（英雄碎片走通用 _item_add）
        from operations import _item_add, _item_deduct
        for rw in rewards:
            _item_add(ctx, uid, rw["id"], rw["num"])

        # 6. 档位 4 达成大满贯：扣除 400 贴票，重置周期已领档位，开启新一轮循环
        if rank_id == 4:
            _item_deduct(ctx, uid, 61, 400)
            db.execute("DELETE FROM idol_trainee_rank_got WHERE uid=?", (uid,))
            claimed_ranks = []
        else:
            db.execute(
                "INSERT OR IGNORE INTO idol_trainee_rank_got (uid, rank_id, update_ts) VALUES (?, ?, ?)",
                (uid, rank_id, now_ts)
            )
            claimed_ranks.append(rank_id)
            claimed_ranks.sort()

        return {"result": 0, "get_id_list": claimed_ranks, "reward_list": rewards}

    def get_idol_trainee_rank_payload(self, uid, db):
        """生成 sc_58191 payload（供 generator 与实时同步使用）。"""
        rank_rows = db.query("SELECT weekly_point FROM idol_trainee_rank WHERE uid=?", (uid,))
        weekly_pt = int(rank_rows[0]["weekly_point"] or 0) if rank_rows else 0
        weekly_pt = min(100, max(0, weekly_pt))
        got_rows = db.query("SELECT rank_id FROM idol_trainee_rank_got WHERE uid=? ORDER BY rank_id", (uid,))
        get_id_list = [int(r["rank_id"]) for r in (got_rows or [])]
        return encode("sc_58191", {
            "get_id_list": get_id_list,
            "point": weekly_pt
        })

    def start_cricket_pve_battle(self, ctx, uid, data=None):
        """58186 开始舞蹈培训生 PVE 关卡挑战（cs_58186 -> sc_58187）：
        彻底修复加载卡 90% 的 Bug。下发合规的出战角色模型、对手 NPC 模型以及 5 回合对战数据。
        """
        if data is None:
            data = {}
        ctx.uid = uid
        stage_id = int(data.get("stage_id") or 101)

        # 1. 出战角色与皮肤 ID
        attacker_skin_id = 1084
        if ctx.db:
            try:
                rows = ctx.db.query("SELECT attack_hero_id FROM idol_trainee WHERE uid=?", (uid,))
                if rows and rows[0]["attack_hero_id"]:
                    attacker_skin_id = int(rows[0]["attack_hero_id"])
            except Exception:
                pass

        # 2. 关卡对手 NPC (IdolTraineePveBattleCfg[stage_id].npc_id)
        stage_npc_map = {101: 903901, 102: 908401, 103: 914801}
        defender_skin_id = stage_npc_map.get(stage_id, 914801)

        # 3. 准备信息
        prepare_info = {
            "attacker_active_buff_list": [],
            "defender_active_buff_list": []
        }

        # 4. 生成 5 回合完整舞步与得分（玩家数值碾压获胜）
        round_list = []
        atk_total = 0
        def_total = 0
        for i in range(1, 6):
            a_pec = 600 + i * 50
            a_skill = 200 + i * 20
            a_sum = a_pec + a_skill
            d_pec = 400 + i * 30
            d_skill = 100 + i * 10
            d_sum = d_pec + d_skill
            atk_total += a_sum
            def_total += d_sum
            round_list.append({
                "round_index": i,
                "attack_user_action": {
                    "atk_style_id": (i % 3) + 1,
                    "skill_id": 0,
                    "index_list": [1],
                },
                "defend_user_action": {
                    "atk_style_id": ((i + 1) % 3) + 1,
                    "skill_id": 0,
                    "index_list": [1],
                },
                "attack_user_score": {
                    "peculiarity_score": a_pec,
                    "skill_score": a_skill,
                    "sum_score": a_sum,
                },
                "defend_user_score": {
                    "peculiarity_score": d_pec,
                    "skill_score": d_skill,
                    "sum_score": d_sum,
                },
            })

        # 5. 缓存本场战斗数据供结算 58164 使用
        self._pending_cricket_battles[uid] = {
            "stage_id": stage_id,
            "atk_sum": atk_total,
            "def_sum": def_total,
            "attacker_skin_id": attacker_skin_id,
            "defender_skin_id": defender_skin_id,
        }

        return {
            "result": 0,
            "prepare_info": prepare_info,
            "round_list": round_list,
            "attacker_skin_id": attacker_skin_id,
            "defender_skin_id": defender_skin_id,
        }

    def settle_cricket_battle(self, ctx, uid, data=None):
        """58164 舞蹈培训生对战演出完成与结算上报（cs_58164 -> sc_58165）：
        返回获胜结算数据，更新 idol_trainee_pve 关卡进度，并附带推送 sc_58189。
        """
        if data is None:
            data = {}
        ctx.uid = uid
        battle_info = self._pending_cricket_battles.pop(uid, None) or {}
        stage_id = int(battle_info.get("stage_id") or 103)
        atk_sum = int(battle_info.get("atk_sum") or 4850)
        def_sum = int(battle_info.get("def_sum") or 3200)

        # 1. 关卡记录落库通过
        changed_tasks = []
        if ctx.db:
            now_ts = int(time.time())
            ctx.db.execute("""
                INSERT INTO idol_trainee_pve (uid, chapter_id, stage_id, score, is_clear, update_ts)
                VALUES (?, 1, ?, ?, 1, ?)
                ON CONFLICT(uid, chapter_id, stage_id) DO UPDATE SET
                    score = MAX(score, excluded.score),
                    is_clear = 1,
                    update_ts = excluded.update_ts
            """, (uid, stage_id, atk_sum, now_ts))

            # 1.5 联动推进 903 任务（Idol PVE 关卡得分任务 10101~10303）
            STAGE_TASKS = {
                101: [(10101, 3400), (10102, 3700), (10103, 4500)],
                102: [(10201, 4000), (10202, 4600), (10203, 6000)],
                103: [(10301, 4800), (10302, 5400), (10303, 7000)],
            }
            if stage_id in STAGE_TASKS:
                pve_row = ctx.db.query("SELECT score FROM idol_trainee_pve WHERE uid=? AND chapter_id=1 AND stage_id=?", (uid, stage_id))
                cur_highest = int(pve_row[0]["score"]) if pve_row else atk_sum
                for tid, need_score in STAGE_TASKS[stage_id]:
                    if cur_highest >= need_score:
                        t_rows = ctx.db.query("SELECT progress, complete_flag FROM task WHERE uid=? AND task_id=?", (uid, tid))
                        if t_rows:
                            if t_rows[0]["progress"] < 1:
                                ctx.db.execute("UPDATE task SET progress=1 WHERE uid=? AND task_id=?", (uid, tid))
                                changed_tasks.append({"id": tid, "progress": 1, "complete_flag": 0})
                        else:
                            ctx.db.execute("INSERT INTO task (uid, task_id, progress, complete_flag) VALUES (?, ?, 1, 0)", (uid, tid))
                            changed_tasks.append({"id": tid, "progress": 1, "complete_flag": 0})

        # 2. 构造双方结算详情
        attacker_data = {
            "sum_score": atk_sum,
            "peculiarity_score": int(atk_sum * 0.75),
            "skill_add_score": int(atk_sum * 0.25),
            "skill_decrease_score": 0,
        }
        defender_data = {
            "sum_score": def_sum,
            "peculiarity_score": int(def_sum * 0.75),
            "skill_add_score": int(def_sum * 0.25),
            "skill_decrease_score": 0,
        }

        # 3. 广播行为事件
        _emit_dorm_action(ctx, uid, "pve_battle", times=1)

        return {
            "result": 0,
            "battle_result": 1,  # 1 为胜利 (success)
            "attacker_data": attacker_data,
            "defender_data": defender_data,
            "stage_id": stage_id,
            "changed_tasks": changed_tasks,
        }

    def get_idol_trainee_pve_payload(self, uid, db):
        """生成 sc_58189 舞蹈培训生 PVE 关卡进度全量推送帧。下发 101, 102, 103 全量关卡并自愈得分任务。"""
        rows = db.query("SELECT stage_id, score, is_clear FROM idol_trainee_pve WHERE uid=? AND chapter_id=1", (uid,)) if db else []
        row_map = {int(r["stage_id"]): (int(r.get("score") or 0), bool(r.get("is_clear"))) for r in (rows or [])}

        STAGE_TASKS = {
            101: [(10101, 3400), (10102, 3700), (10103, 4500)],
            102: [(10201, 4000), (10202, 4600), (10203, 6000)],
            103: [(10301, 4800), (10302, 5400), (10303, 7000)],
        }
        # 自愈同步历史得分任务
        if db:
            for sid, t_list in STAGE_TASKS.items():
                if sid in row_map:
                    sc, is_clr = row_map[sid]
                    for tid, need_score in t_list:
                        if sc >= need_score:
                            t_rows = db.query("SELECT progress FROM task WHERE uid=? AND task_id=?", (uid, tid))
                            if t_rows:
                                if t_rows[0]["progress"] < 1:
                                    db.execute("UPDATE task SET progress=1 WHERE uid=? AND task_id=?", (uid, tid))
                            else:
                                db.execute("INSERT INTO task (uid, task_id, progress, complete_flag) VALUES (?, ?, 1, 0)", (uid, tid))

        ALL_STAGES = [101, 102, 103]
        stage_list = []
        for sid in ALL_STAGES:
            if sid in row_map:
                score, is_clear = row_map[sid]
                stage_list.append({"stage_id": sid, "score": score, "is_clear": is_clear})
            else:
                stage_list.append({"stage_id": sid, "score": 0, "is_clear": False})

        return encode("sc_58189", {"chapter_list": [{"chapter_id": 1, "stage_list": stage_list}]})



# ==================== 游园街时间推演监听（原 timer_listeners.on_dorm_tick） ====================

@bus.subscribe(Events.TIME_TICK)
def on_dorm_tick(ctx, uid, delta_seconds=0, now_ts=None, **kwargs):
    """连续时间流逝驱动食堂做菜与后宅英雄疲劳度恢复。"""
    if not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return
    db = ctx.db

    # 1. 食堂做菜与收益惰性结算
    if hasattr(db, '_calculate_canteen_dishes'):
        try:
            db._calculate_canteen_dishes(uid, now_ts=now_ts)
        except Exception as e:
            logger.debug(f"[LazyTimer] 食堂做菜推演异常: {e}")

    # 2. 后宅英雄休息疲劳恢复（好感等级 Lv 1~10 动态倍率恢复模型）
    if hasattr(db, 'calculate_dorm_hero_fatigue_recovery'):
        try:
            db.calculate_dorm_hero_fatigue_recovery(uid)
        except Exception as e:
            logger.debug(f"[LazyTimer] 后宅英雄疲劳恢复异常: {e}")


# ==================== 事件总线发射助手 ====================

def _emit_dorm_action(ctx, uid, action_type, times=1, attribute_index=None, personality=None):
    """广播游园街交互 → task_listener.on_dorm_action（任务条件 2003/2007/2008/2009/601 等）。"""
    bus.emit(Events.DORM_ACTION, ctx, uid, action_type=action_type, times=times,
             attribute_index=attribute_index, personality=personality)


def _emit_dorm_gift(ctx, uid, hero_id, furniture_id=0):
    """广播家园赠送 → oath_service.on_dorm_gift（誓约条件 410001）。"""
    bus.emit(Events.DORM_GIFT, ctx, uid, hero_id=hero_id, furniture_id=furniture_id)


SILENT_STUB_CMDS = {
    # ---- 多人/联机社交（永久静默） ----
    58050: (58051, "查看他人房间布局"),
    58160: (58161, "虫斗PVP对战请求"),
    58176: (58177, "虫斗进攻方阵容设置"),
    58178: (58179, "虫斗防守方阵容设置"),
    58180: (58181, "虫斗对手列表"),
    58182: (58183, "虫斗对战历史"),
    58184: (58185, "虫斗对战回放"),
    58204: (58205, "舞蹈DIY分享"),
    58206: (58207, "舞蹈DIY取消分享"),
    58208: (58209, "点赞他人舞蹈"),
    58210: (58211, "收藏他人舞蹈"),
    58212: (58213, "共享舞蹈详情"),
    58214: (58215, "共享舞蹈列表"),
    58216: (58217, "我的分享统计"),
}


# 模块激活：实例化领域服务（登录帧生成走 generator 委托，时间推演走总线订阅）
BackHomeService.get_instance()
