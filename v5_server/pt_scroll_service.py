"""Serve the configured 联防协议 battle rewards and account progress."""
import json
from pathlib import Path
import time

from core import OperationError
from event_bus import Events, bus
from inventory_service import InventoryService
from middleware import DownFrame


STAGE_TYPE = 61
MAX_MULTIPLE = 10  # challenge_multiple_count_unlock_level has ten entries.
TICKET_CLEAR_COUNT = 2  # GameSetting.activity_pt_roulette_task_times
CATALOG = {int(aid): row for aid, row in
           json.loads(Path(__file__).with_name("pt_scroll_cfg.json").read_text("utf-8")).items()}
STAGE_KEYS = frozenset(number for activity in CATALOG.values() for key, stage in activity["stages"].items()
                       for number in (int(key), stage["stage_id"]))


def reserved(stage_type, dest):
    return stage_type == STAGE_TYPE or dest in STAGE_KEYS


def _open(row, now):
    return row["activity_id"] in CATALOG and row["state"] == 1 and row["start_time"] <= now < row["stop_time"]


def require_activity(db, uid, aid, now):
    from generator import get_activity_list
    if aid not in CATALOG or not any(row["activity_id"] == aid and _open(row, now)
                                     for row in get_activity_list(db, uid)):
        raise OperationError(2, "联防协议活动未开放")
    return CATALOG[aid]


def _progress(db, uid, aid):
    rows = db.query("SELECT * FROM pt_roulette_progress WHERE uid=? AND activity_id=?", (uid, aid))
    return rows[0] if rows else {}


def login_frames(db, uid, activities, encode):
    now = int(time.time())
    # TimeMgr:GetGameServerWeek uses the server time anchor (316800) and refresh offset (05:00).
    day = str((now - 316800 - 5 * 3600) // 86400 % 7 + 1)
    frames = []
    for entry in activities:
        if not _open(entry, now):
            continue
        aid = entry["activity_id"]
        progress = _progress(db, uid, aid)
        rotation = CATALOG[aid]["rotation"][day]
        selected = progress.get("up_select", 0)
        valid = progress.get("pool_id") == rotation["id"] and selected in rotation["suits"]
        data = dict(activity_id=aid, index=rotation["id"], buff_id=rotation["default_buff"],
                    up_select=selected if valid else 0, clear_times=progress.get("clear_times") or 0,
                    clear_stage_list=json.loads(progress.get("clear_stage_list") or "[]"))
        raw = encode("sc_76151", data)
        if not raw:
            raise ValueError("联防协议状态帧为空")
        frames.append(DownFrame(76151, raw))
    return frames


def validate(db, uid, stage_type, aid, dest, times, *, sweep=False, check_cost=True):
    stage = CATALOG.get(aid, {}).get("stages", {}).get(str(dest))
    if stage_type != STAGE_TYPE or not stage or not 1 <= times <= MAX_MULTIPLE:
        raise OperationError(2, "联防协议活动、关卡或次数无效")
    require_activity(db, uid, aid, int(time.time()))
    progress = _progress(db, uid, aid)
    cleared = set(json.loads(progress.get("clear_stage_list") or "[]"))
    if sweep and stage["stage_id"] not in cleared:
        raise OperationError(2, "联防协议关卡未通关")
    item, count = stage["cost"]
    if check_cost and InventoryService.get_item_balance(db, uid, item)[1] < count * times:
        raise OperationError(3, "联防协议入场资源不足")
    return stage, cleared


def claim_tickets(ctx, uid, aid, times, task_ids):
    from operations import TaskSubmitOp
    now = int(time.time())
    cfg = require_activity(ctx.db, uid, aid, now)
    progress = _progress(ctx.db, uid, aid)
    available = max(0, progress.get("clear_times") or 0) // TICKET_CLEAR_COUNT
    if times != available or (times == 0 and not task_ids) or len(set(task_ids)) != len(task_ids):
        raise OperationError(2, "联防协议任务奖励次数无效")
    task_submit = TaskSubmitOp()
    task_submit.uid = uid
    for tid in task_ids:
        rows = ctx.db.query("SELECT activity_id FROM task_cfg WHERE task_id=?", (tid,))
        if not rows or rows[0]["activity_id"] != cfg["task_activity_id"]:
            raise OperationError(2, "任务不属于本期联防协议")
        # The native client displays assignment rewards from its task config.
        task_submit.apply({"id": tid}, ctx)
    if not times:
        return []

    ticket, count = next(stage["cost"] for stage in cfg["stages"].values() if stage["mode"] == 2)
    left = progress["clear_times"] - times * TICKET_CLEAR_COUNT
    ctx.db.execute("UPDATE pt_roulette_progress SET clear_times=?,update_ts=? WHERE uid=? AND activity_id=?",
                   (left, now, uid, aid))
    items = [(ticket, count * times)]
    summary = InventoryService.grant_items(ctx, uid, items, source="pt_scroll_task")
    return [{"id": iid, "num": amount}
            for iid, amount in InventoryService.rewards_after_overflow(items, summary)]


def settle(ctx, uid, bid, result, stage_type, aid, dest, times, heroes, battle_info, sweep):
    from middleware import build_battle_result, stage_drop
    from operations import _refresh_frames
    win = result == 1
    stage, cleared = validate(ctx.db, uid, stage_type, aid, dest, times, sweep=sweep, check_cost=win)
    seconds = max(0, int(battle_info.get("battle_time") or 0)) // 1000
    if not win:
        return build_battle_result(bid, result, dest, heroes=heroes, use_seconds=seconds, star_ids=()), []

    stage_id = stage["stage_id"]
    first_clear = stage_id not in cleared
    item, count = stage["cost"]
    InventoryService.deduct_item(ctx, uid, item, count * times)
    drops = []
    for index in range(times):
        items = stage_drop(stage_id, first_clear=first_clear and index == 0, drop_lib=stage["drop_lib"])
        if items is None:
            raise ValueError("联防协议掉落库缺失")
        summary = InventoryService.grant_items(ctx, uid, items, source="pt_scroll_stage")
        drops.append(InventoryService.rewards_after_overflow(items, summary))

    cleared.add(stage_id)
    ctx.db.execute("INSERT INTO pt_roulette_progress(uid,activity_id,clear_stage_list,clear_times,update_ts) "
                   "VALUES(?,?,?,?,?) ON CONFLICT(uid,activity_id) DO UPDATE SET "
                   "clear_stage_list=excluded.clear_stage_list, "
                   "clear_times=COALESCE(pt_roulette_progress.clear_times,0)+excluded.clear_times, "
                   "update_ts=excluded.update_ts",
                   (uid, aid, json.dumps(sorted(cleared)), times if stage["mode"] == 1 else 0, int(time.time())))
    bus.emit(Events.STAGE_PASS, ctx, uid, times=times, stage_id=stage_id, stage_type_id=STAGE_TYPE,
             stage_kind="pt_scroll", heroes=heroes, sweep=sweep, battle_id=bid, b_info=battle_info)
    if first_clear:
        bus.emit(Events.STAGE_FIRST_CLEAR, ctx, uid, stage_id=stage_id, b_info=battle_info)
    if item == 4:
        bus.emit(Events.STAMINA_COST, ctx, uid, amount=count * times)
    return build_battle_result(bid, result, dest, clear_times=times, drops=drops, heroes=heroes,
                               use_seconds=seconds, star_ids=()), _refresh_frames(ctx, uid, strict=True)
