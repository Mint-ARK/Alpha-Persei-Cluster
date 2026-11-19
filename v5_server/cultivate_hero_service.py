"""Restore and claim account-owned 限期委培 progress."""
from collections import Counter
import json
from pathlib import Path
import time

from core import Operation, OperationError
from event_bus import Events, bus
from hero_codec import normalize_hero_db_row
from hero_service import _get_weapon_growth_rules
from inventory_service import InventoryService
from lazy_timer import get_daily_5am_ts
from middleware import DownFrame


# Numeric activity, task, and reward data from ActivityCultivateHeroCfg
# and CultivateHeroTaskCfg. Login applies the selected version's activity list.
CATALOG = {int(aid): dict(row, tasks={int(tid): task for tid, task in row["tasks"].items()})
           for aid, row in json.loads(Path(__file__).with_name("cultivate_hero_cfg.json").read_text("utf-8")).items()}
TARGET_HEROES = frozenset(task["hero"] for row in CATALOG.values() for task in row["tasks"].values())


def open_activities(activities, now):
    return {
        row["activity_id"] for row in activities
        if row["activity_id"] in CATALOG and row["state"] == 1
        and row["start_time"] <= now < row["stop_time"]
    }


def _visible(db, uid, now):
    from generator import get_activity_list
    return open_activities(get_activity_list(db, uid), now)


def _progress(db, uid, tasks):
    hero_ids = sorted({task["hero"] for task in tasks.values()})
    if not hero_ids:
        return {}
    markers = ",".join("?" for _ in hero_ids)
    rows = db.query(f"SELECT id,level,weapon_exp,skill_list FROM hero "
                    f"WHERE uid=? AND unlock=1 AND id IN ({markers})", (uid, *hero_ids))
    levels = {}
    curve = _get_weapon_growth_rules()["level_exp"]
    for row in rows:
        skills = normalize_hero_db_row(row).get("skill_list") or []
        skill_sum = 0
        for skill in skills:
            value = skill.get("skill_level", skill.get("level", 0)) if isinstance(skill, dict) else (
                skill[1] if isinstance(skill, list) and len(skill) >= 2 else 0)
            if isinstance(value, int) and value > 0:
                skill_sum += value
        weapon_level = max((level for level, need in curve.items()
                            if need <= (row["weapon_exp"] or 0)), default=1)
        levels[row["id"]] = {1: row["level"] or 1, 2: weapon_level, 3: skill_sum}
    return {tid: levels.get(task["hero"], {}).get(task["type"], 0) for tid, task in tasks.items()}


def _task_claims(db, uid, aid):
    rows = db.query("SELECT item_id FROM ops_common WHERE uid=? AND kind='cultivate_hero_task' AND name=?",
                    (uid, str(aid)))
    return {int(row["item_id"]) for row in rows}


def _daily_claimed(db, uid, aid, now):
    rows = db.query("SELECT value_json FROM ops_common WHERE uid=? AND kind='cultivate_hero_daily' AND item_id=?",
                    (uid, str(aid)))
    return bool(rows and rows[0]["value_json"] == str(get_daily_5am_ts(now)))


def payload(db, uid, aid, now):
    progress = _progress(db, uid, CATALOG[aid]["tasks"])
    tiers = db.query("SELECT reward_id FROM activity_point_reward WHERE uid=? AND activity_id=? ORDER BY reward_id",
                     (uid, aid))
    return {
        "activity_id": aid,
        "task_info_list": [{"id": tid, "progress": count} for tid, count in progress.items()],
        "got_reward_task_id_list": sorted(_task_claims(db, uid, aid)),
        "daily_pt_task_got_flag": _daily_claimed(db, uid, aid, now),
        "got_point_id_list": [row["reward_id"] for row in tiers],
    }


def _frame(encode, command, data):
    raw = encode(f"sc_{command}", data)
    if not raw:
        raise ValueError(f"限期委培 sc_{command} 编码失败")
    return DownFrame(command, raw)


def login_frames(db, uid, activities, encode):
    now = int(time.time())
    return [_frame(encode, 64077, payload(db, uid, aid, now))
            for aid in sorted(open_activities(activities, now))]


@bus.subscribe(Events.HERO_UPGRADE)
@bus.subscribe(Events.HERO_UNLOCK)
def _hero_changed(ctx, uid, hero_id=0, **kwargs):
    if not hasattr(ctx, "append_frame") or hero_id not in TARGET_HEROES:
        return
    now = int(time.time())
    for aid in sorted(_visible(ctx.db, uid, now)):
        if any(task["hero"] == hero_id for task in CATALOG[aid]["tasks"].values()):
            # Build 229 的任务配置缺少 sc_64081 消费方用于定位活动的 activity_id。
            ctx.append_frame(_frame(ctx.codec_encode, 64077, payload(ctx.db, uid, aid, now)))


@bus.subscribe(Events.DAILY_RESET_5AM)
def _daily_reset(ctx, uid, now_ts, **kwargs):
    if hasattr(ctx, "append_frame"):
        for aid in sorted(_visible(ctx.db, uid, now_ts)):
            ctx.append_frame(_frame(ctx.codec_encode, 64083, {
                "activity_id": aid, "daily_pt_task_got_flag": False,
            }))


class _CultivateClaim(Operation):
    prepare_response_in_transaction = True

    def apply(self, data, ctx):
        now = int(time.time())
        aid = int(data.get("activity_id") or 0)
        if aid not in _visible(ctx.db, self.uid, now):
            raise OperationError(2, "限期委培活动未开放")
        items = self.claim(ctx.db, aid, now, data)
        summary = InventoryService.grant_items(ctx, self.uid, items, source="cultivate_hero")
        actual = InventoryService.rewards_after_overflow(items, summary)
        return [{"id": item, "num": count} for item, count in actual]

    def respond(self, result, data, ctx):
        from operations import _refresh_frames
        return _refresh_frames(ctx, self.uid, strict=True) + [
            _frame(ctx.codec_encode, self.sc, {"result": 0, "reward_list": result}),
        ]


class HeroTaskClaimOp(_CultivateClaim):
    cmd, sc = 64078, 64079

    def claim(self, db, aid, now, data):
        task_ids = data.get("task_id_list") or []
        tasks = CATALOG[aid]["tasks"]
        if not task_ids or len(set(task_ids)) != len(task_ids) or any(tid not in tasks for tid in task_ids):
            raise OperationError(2, "限期委培任务无效")
        if _task_claims(db, self.uid, aid).intersection(task_ids):
            raise OperationError(2, "限期委培任务已领取")
        progress = _progress(db, self.uid, {tid: tasks[tid] for tid in task_ids})
        if any(progress[tid] < tasks[tid]["condition"] for tid in task_ids):
            raise OperationError(2, "限期委培任务未完成")
        items = Counter()
        for tid in task_ids:
            db.execute("INSERT INTO ops_common (uid,kind,item_id,name,value_json) "
                       "VALUES (?,'cultivate_hero_task',?,?,'true')", (self.uid, str(tid), str(aid)))
            for item, count in tasks[tid]["reward"]:
                items[item] += count
        return list(items.items())


class DailyTaskClaimOp(_CultivateClaim):
    cmd, sc = 64084, 64085

    def claim(self, db, aid, now, data):
        rows = db.query("SELECT active_point FROM activity_pt WHERE uid=? AND activity_pt_id=1", (self.uid,))
        if not rows or (rows[0]["active_point"] or 0) < 100:
            raise OperationError(2, "每日活跃度未达 100")
        if _daily_claimed(db, self.uid, aid, now):
            raise OperationError(2, "限期委培每日奖励已领取")
        db.execute("INSERT INTO ops_common (uid,kind,item_id,name,value_json) "
                   "VALUES (?,'cultivate_hero_daily',?,'',?) "
                   "ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json",
                   (self.uid, str(aid), str(get_daily_5am_ts(now))))
        return CATALOG[aid]["daily_reward"]
