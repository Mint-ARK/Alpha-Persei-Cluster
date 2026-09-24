# -*- coding: utf-8 -*-
"""
task_listener.py — 任务系统事件总线订阅器 (Task Listener)
监听全局业务事件，安全驱动任务内部计数器并生成 sc_28007 增量差分下行帧。
"""

import json
import time
from codec import encode
from middleware import DownFrame
from event_bus import bus, Events
from lazy_timer import get_daily_5am_ts


def _trigger_task_condition(db, uid, condition, delta=1, param=None, set_value=False):
    """
    通用任务条件进度推进核心函数。
    1. 查询所有未领取的、匹配 condition 的任务；
    2. 匹配 additional_parameter（如有）；
    3. 计算 new_progress = min(need, old_progress + delta)；
    4. 若有真实变化，原子更新数据库并收集；
    5. 返回变化列表: [{id: tid, progress: new_prog, complete_flag: 0}]
    """
    cond_int = int(condition)
    # 以 task_cfg 为主表，LEFT JOIN task 表，按需激活老账号缺省任务
    rows = db.query(
        """
        SELECT c.task_id, t.task_id as in_task, COALESCE(t.progress, 0) as progress, 
               COALESCE(c.need, 1) as need, c.additional_parameter,
               COALESCE(t.claimed_ts, 0) as claimed_ts, COALESCE(t.complete_flag, 0) as complete_flag
        FROM task_cfg c
        LEFT JOIN task t ON t.task_id = c.task_id AND t.uid = ?
        WHERE COALESCE(c.condition, 0) = ? 
          AND (t.task_id IS NOT NULL OR c.condition IN (392001, 423002, 380001, 501, 400004, 160001, 160002, 160003, 160004, 160005, 160006, 160007, 160008, 422001, 422002, 422003, 422004, 50104, 350001, 411008))
          AND COALESCE(t.claimed_ts, 0) = 0
          AND COALESCE(t.complete_flag, 0) = 0
          AND (
              c.task_type IN (5, 6, 7, 8, 901, 902, 903, 1101, 3001, 3002)
              OR (c.task_id BETWEEN 10000 AND 10015)
              OR NOT EXISTS (
                  SELECT 1 FROM claim_ledger cl 
                  WHERE cl.uid = ? 
                    AND cl.kind IN ('task', 'illustrated_task') 
                    AND cl.key_id = c.task_id
              )
          )
        """,
        (uid, cond_int, uid)
    )
    if not rows:
        return []

    changed = []
    seen_tasks = set()
    for r in rows:
        tid = r["task_id"]
        if tid in seen_tasks:
            continue
        seen_tasks.add(tid)
        old_prog = int(r["progress"] or 0)
        need = int(r["need"] or 1)

        # 校验 additional_parameter（如体力/金币/特定关卡类型等）
        param_str = r.get("additional_parameter")
        if param_str:
            try:
                if isinstance(param_str, str):
                    if param_str.startswith("["):
                        p_list = json.loads(param_str)
                    else:
                        p_list = [int(x.strip()) for x in param_str.split(",") if x.strip().isdigit()]
                elif isinstance(param_str, (list, tuple)):
                    p_list = list(param_str)
                else:
                    p_list = [int(param_str)]
                # 过滤有效限制项（0 表示通配/不限类型）
                effective_restrictions = [x for x in p_list if x != 0]
                if effective_restrictions:
                    if param is None or (int(param) not in p_list):
                        continue
            except Exception:
                pass

        if set_value:
            new_prog = min(need, int(delta))
        else:
            new_prog = min(need, old_prog + int(delta))

        if new_prog > old_prog:
            db.execute(
                """INSERT INTO task (uid, task_id, progress) VALUES (?, ?, ?)
                   ON CONFLICT(uid, task_id) DO UPDATE SET progress = excluded.progress""",
                (uid, tid, new_prog)
            )
            changed.append({
                "id": tid,
                "progress": new_prog,
                "complete_flag": 0
            })

    return changed


def _push_task_diff(ctx, uid, changed_list):
    """将变更的任务打包为 sc_28007 并塞入 ctx 的待发队列"""
    if not changed_list:
        return
    try:
        payload = encode("sc_28007", {"progress_list": changed_list})
        if payload:
            frame = DownFrame(28007, payload)
            if hasattr(ctx, "append_frame"):
                ctx.append_frame(frame)
            else:
                if not hasattr(ctx, "pending_frames"):
                    ctx.pending_frames = []
                ctx.pending_frames.append(frame)
            if hasattr(ctx, "log"):
                ctx.log(f"[TaskListener] sc_28007 推送 {len(changed_list)} 个任务进度更新: {[(x['id'], x['progress']) for x in changed_list]}")
    except Exception as e:
        if hasattr(ctx, "log"):
            ctx.log(f"[TaskListener ERROR] 构造 sc_28007 失败: {e}")


def _push_accumulate_sign_diff(ctx, uid, login_days, version=2):
    """时迹馈赠全量状态 sc_17027 下行帧推送"""
    try:
        awards = ctx.db.query("SELECT award_id FROM accumulate_sign_award WHERE uid = ?", (uid,))
        award_ids = [int(x["award_id"]) for x in awards]
        payload = encode("sc_17027", {
            "version": int(version or 2),
            "open_sign": True,
            "login_days": int(login_days),
            "award_ids": award_ids
        })
        if payload:
            frame = DownFrame(17027, payload)
            if hasattr(ctx, "append_frame"):
                ctx.append_frame(frame)
            else:
                if not hasattr(ctx, "pending_frames"):
                    ctx.pending_frames = []
                ctx.pending_frames.append(frame)
            if hasattr(ctx, "log"):
                ctx.log(f"[TaskListener] sc_17027 时迹馈赠推送：累计登录 {login_days} 天")
    except Exception as e:
        if hasattr(ctx, "log"):
            ctx.log(f"[TaskListener ERROR] 构造 sc_17027 失败: {e}")


# ==================== 事件订阅注册 ====================

@bus.subscribe(Events.STAGE_PASS)
def on_stage_pass(ctx, uid, times=1, stage_id=0, stage_type=None, **kwargs):
    """关卡/战斗通关 (times 次)"""
    db = getattr(ctx, "db", None) if ctx else None
    if not db:
        from account_db import get_db
        db = get_db()
    # 1. 通关任意关卡 (cond=402: 6002 需1次, 6004 需5次)
    c1 = _trigger_task_condition(db, uid, condition=402, delta=times)
    # 2. 历战轮回 (cond=405)
    c2 = []
    if stage_type == "regular" or (isinstance(stage_id, int) and 100000 <= stage_id < 200000):
        c2 = _trigger_task_condition(db, uid, condition=405, delta=times)
    # 3. 黑区净化 (cond=453)
    c3 = []
    if stage_type == "black_zone" or (isinstance(stage_id, int) and 200000 <= stage_id < 300000):
        c3 = _trigger_task_condition(ctx.db, uid, condition=453, delta=times)

    # 4. 海拉弹珠关卡 (cond=392001)
    c4 = []
    if stage_type == "minigame_pinball" or (isinstance(stage_id, int) and 40601 <= stage_id <= 40650):
        c4 = _trigger_task_condition(db, uid, condition=392001, delta=1, param=stage_id, set_value=True)

    # 5. 灰烬牛仔关卡 (cond=380001 & cond=501)
    c5 = []
    ash_stage_id = kwargs.get("ash_stage_id") or stage_id
    if stage_type == "ash_shoot" or (isinstance(stage_id, int) and (404101 <= stage_id <= 404401 or 5280301 <= stage_id <= 5280327)):
        # 推进单关通关任务 (cond=380001)
        c5_single = _trigger_task_condition(db, uid, condition=380001, delta=1, param=ash_stage_id, set_value=True)
        # 驱动累计完成活动关卡限时任务 (cond=501, need=3..18)
        done_rows = db.query(
            "SELECT count(*) as cnt FROM task WHERE uid = ? AND task_id BETWEEN 40401001 AND 40401018 AND progress >= 1",
            (uid,)
        )
        done_cnt = int(done_rows[0]["cnt"]) if done_rows else 0
        c5_total = _trigger_task_condition(db, uid, condition=501, delta=done_cnt, set_value=True)
        c5 = c5_single + c5_total

    # 6. 卡达斯夏活坦克配件收集 (cond=423002)
    c6 = []
    if stage_type == "summer_race":
        module_count = kwargs.get("module_count")
        if module_count is not None:
            module_type = kwargs.get("module_type", 1)
            c6 = _trigger_task_condition(db, uid, condition=423002, delta=module_count, param=module_type, set_value=True)

    # 7. 霍德尔活动关卡首通 (cond=400004)
    c7 = []
    stg = stage_id or kwargs.get("dest_int", 0)
    if stage_type == "hodur" or (isinstance(stg, int) and 5310201 <= stg <= 5310222):
        c7 = _trigger_task_condition(db, uid, condition=400004, delta=1, param=stg, set_value=True)

    # 8. 薇儿丹蒂 SP 潜质觉醒任务 (cond=160002 训练熟练度, cond=160003 Boss得分, cond=160007 满熟练度)
    c8 = []
    if stage_type == "sphero_train":
        grp = kwargs.get("group_type")
        prog = kwargs.get("progress", 0)
        if grp is not None:
            c8.extend(_trigger_task_condition(db, uid, condition=160002, delta=prog, param=grp, set_value=True))
            if prog >= 3000:
                c8.extend(_trigger_task_condition(db, uid, condition=160007, delta=1, param=3000, set_value=True))
    elif stage_type == "sphero_boss":
        boss_sc = kwargs.get("score", 0)
        stg = stage_id or kwargs.get("dest_int", 0)
        if boss_sc >= 4000000:
            c8.extend(_trigger_task_condition(db, uid, condition=160003, delta=1, param=stg, set_value=True))

    # 9. 乌尔碰碰车 (极限机装, cond=422001 关卡通关, cond=422002 击杀怪物, cond=422003 合成技能, cond=422004 累计通关, cond=501 小游戏通关)
    c9 = []
    if stage_type == "vehicle_ball":
        stg = stage_id or kwargs.get("dest_int", 0)
        if kwargs.get("win", True) and stg:
            c9.extend(_trigger_task_condition(db, uid, condition=422001, delta=1, param=stg, set_value=True))
            c9.extend(_trigger_task_condition(db, uid, condition=501, delta=1))
            c9.extend(_trigger_task_condition(db, uid, condition=422004, delta=1))
        kills = kwargs.get("kill_count", 0)
        if kills > 0:
            c9.extend(_trigger_task_condition(db, uid, condition=422002, delta=kills))
        merges = kwargs.get("merge_count", 0)
        if merges > 0:
            c9.extend(_trigger_task_condition(db, uid, condition=422003, delta=merges))

    # 10. 浮光绎曲音乐会 (cond=50104 完成指定乐曲初战任务 40601001~40601008)
    c10 = []
    if stage_type == "music_game":
        tid = kwargs.get("task_id")
        if tid:
            t_rows = db.query("SELECT progress FROM task WHERE uid = ? AND task_id = ?", (uid, tid))
            if not t_rows:
                db.execute("INSERT INTO task (uid, task_id, progress) VALUES (?, ?, 1)", (uid, tid))
                c10.append({"id": tid, "progress": 1, "complete_flag": 0})
            elif t_rows[0]["progress"] < 1:
                db.execute("UPDATE task SET progress = 1 WHERE uid = ? AND task_id = ?", (uid, tid))
                c10.append({"id": tid, "progress": 1, "complete_flag": 0})

    _push_task_diff(ctx, uid, c1 + c2 + c3 + c4 + c5 + c6 + c7 + c8 + c9 + c10)


@bus.subscribe(Events.STAMINA_COST)
def on_stamina_cost(ctx, uid, amount=1, **kwargs):
    """消耗体力/吨吨值 (cond=350, param=4: 6007 需100, 6009 需300, 100012 需1000)"""
    if amount <= 0:
        return
    c = _trigger_task_condition(ctx.db, uid, condition=350, delta=amount, param=4)
    _push_task_diff(ctx, uid, c)


@bus.subscribe(Events.GOLD_COST)
def on_gold_cost(ctx, uid, amount=1, **kwargs):
    """消耗金币 (cond=350, param=2: 100014 需50000)"""
    if amount <= 0:
        return
    c = _trigger_task_condition(ctx.db, uid, condition=350, delta=amount, param=2)
    _push_task_diff(ctx, uid, c)


@bus.subscribe(Events.SHOP_BUY)
def on_shop_buy(ctx, uid, count=1, **kwargs):
    """商店购买物品 (cond=600: 6005 需1次)"""
    c = _trigger_task_condition(ctx.db, uid, condition=600, delta=count)
    _push_task_diff(ctx, uid, c)


@bus.subscribe(Events.BUY_FATIGUE)
def on_buy_fatigue(ctx, uid, times=1, **kwargs):
    """移转之辉买体力 (cond=3: 6006 需1次)"""
    c = _trigger_task_condition(ctx.db, uid, condition=3, delta=times)
    _push_task_diff(ctx, uid, c)


@bus.subscribe(Events.HERO_UPGRADE)
def on_hero_upgrade(ctx, uid, times=1, **kwargs):
    """修正者升级/突破/钥从强化 (cond=8: 6013 需1次)"""
    c = _trigger_task_condition(ctx.db, uid, condition=8, delta=times)
    _push_task_diff(ctx, uid, c)


@bus.subscribe(Events.EQUIP_REFORGE)
def on_equip_reforge(ctx, uid, times=1, **kwargs):
    """刻印赋能/重构 (cond=9: 6014 需1次; cond=350 消耗模块 100018)"""
    c1 = _trigger_task_condition(ctx.db, uid, condition=9, delta=times)
    c2 = _trigger_task_condition(ctx.db, uid, condition=350, delta=times)
    _push_task_diff(ctx, uid, c1 + c2)


@bus.subscribe(Events.DISPATCH_FINISH)
def on_dispatch_finish(ctx, uid, times=1, **kwargs):
    """修正者派遣 (cond=10: 6015 需1次; cond=1014: 100015 需5次)"""
    c1 = _trigger_task_condition(ctx.db, uid, condition=10, delta=times)
    c2 = _trigger_task_condition(ctx.db, uid, condition=1014, delta=times)
    _push_task_diff(ctx, uid, c1 + c2)


@bus.subscribe(Events.DORM_ACTION)
def on_dorm_action(ctx, uid, action_type="visit", times=1, attribute_index=None, personality=None, **kwargs):
    """
    后宅/游园街交互任务条件推进：
    - feed: 进餐/投喂 (cond=2003: 6016 进餐1次, 10012 进餐10次)
    - commission / commission_dispatch: 委托接取/派遣 (cond=2007: 6018 接取1次委托)
    - commission_submit: 委托完成结算 (cond=2002: 10011 完成6次食材委托)
    - canteen_award: 领取餐厅营收 (cond=2020: 10000 领营收1次)
    - game: 小游戏参与/结算 (cond=2008: 6017 参与1次, 10010 完成3次)
    - train: 训练室培养 (cond=2009: 6019 培养1次, 10001 培养1次, 10003 培养12次, 10004 培养18次, 30701012~30701015 挑战)
             若传 attribute_index 则同步推进 cond=2017; 若传 personality 则同步推进 cond=2016
    - battle: 挑战其他玩家 (cond=2011: 10002 挑战1次, 10005 挑战3次, 10006 挑战6次, 150197)
    - battle_win: 对战胜利 (cond=2010: 10007 胜利3次)
    - furniture: 家具购买/交互 (cond=601: 100017 购买家具)
    """
    changed = []
    if action_type in ("feed", "dine"):
        changed += _trigger_task_condition(ctx.db, uid, condition=2003, delta=times)
    elif action_type in ("commission", "commission_dispatch"):
        changed += _trigger_task_condition(ctx.db, uid, condition=2007, delta=times)
    elif action_type == "commission_submit":
        changed += _trigger_task_condition(ctx.db, uid, condition=2002, delta=times)
    elif action_type == "canteen_award":
        changed += _trigger_task_condition(ctx.db, uid, condition=2020, delta=times)
    elif action_type == "game":
        changed += _trigger_task_condition(ctx.db, uid, condition=2008, delta=times)
    elif action_type == "train":
        changed += _trigger_task_condition(ctx.db, uid, condition=2009, delta=times)
        if attribute_index is not None:
            changed += _trigger_task_condition(ctx.db, uid, condition=2017, delta=times, param=attribute_index)
        if personality is not None:
            changed += _trigger_task_condition(ctx.db, uid, condition=2016, delta=times, param=personality)
    elif action_type in ("battle", "pve_battle"):
        changed += _trigger_task_condition(ctx.db, uid, condition=2011, delta=times)
        changed += _trigger_task_condition(ctx.db, uid, condition=2010, delta=times)
    elif action_type == "battle_win":
        changed += _trigger_task_condition(ctx.db, uid, condition=2010, delta=times)
    elif action_type == "furniture":
        changed += _trigger_task_condition(ctx.db, uid, condition=601, delta=times)
        try:
            changed += sync_dorm_illu_tasks(ctx.db, uid)
        except Exception:
            pass
    elif action_type == "visit":
        # 兼容旧代码
        pass

    _push_task_diff(ctx, uid, changed)


@bus.subscribe(Events.POLYHEDRON_PASS)
def on_polyhedron_pass(ctx, uid, times=1, **kwargs):
    """多维变量 (cond=423: 100007 需1次)"""
    c = _trigger_task_condition(ctx.db, uid, condition=423, delta=times)
    _push_task_diff(ctx, uid, c)


@bus.subscribe(Events.USER_LOGIN)
def on_user_login(ctx, uid, is_first_login=True, now_ts=None, **kwargs):
    """
    每日登录 / 签到：
    1. 接入 05:00 周期幂等锁（cur_daily_5am）：
       同一游戏日（当日05:00~次日05:00）内，无论触发多少次 USER_LOGIN 广播，绝对仅推进 1 次！
    2. 推进 cond=1 任务：
       - 6001 每日首次登录 (1/1)
       - 100009 每周累计登录5天 (+1)
       - 战令/活动累计登录任务 (+1)
    3. 联动推进【时迹馈赠】(accumulate_sign.login_days + 1):
       - 累计登录天数 +1
       - 构造 sc_17027 下行帧推送给客户端
    """
    if not is_first_login or not uid or not hasattr(ctx, 'db') or ctx.db is None:
        return

    if now_ts is None:
        now_ts = int(time.time())
    cur_cycle = get_daily_5am_ts(now_ts)

    # 1. 检查 game_user 上的 last_login_task_cycle_ts 周期锁
    user = ctx.db.get("game_user", uid)
    last_cycle = int((user.get("last_login_task_cycle_ts") if user else 0) or 0)
    if last_cycle >= cur_cycle:
        # 当前 05:00 游戏日周期已计入首次登录，幂等拦截
        return

    # 标记当前周期已处理
    ctx.db.execute(
        "UPDATE game_user SET last_login_task_cycle_ts = ? WHERE uid = ?",
        (cur_cycle, uid)
    )

    # 2. 推进登录条件任务 (cond=1)
    c = _trigger_task_condition(ctx.db, uid, condition=1, delta=1)
    _push_task_diff(ctx, uid, c)

    # 3. 联动推进【时迹馈赠】(accumulate_sign)
    try:
        acc = ctx.db.get("accumulate_sign", uid)
        if acc:
            new_days = int(acc.get("login_days") or 0) + 1
            ver = int(acc.get("version") or 2)
            ctx.db.execute(
                "UPDATE accumulate_sign SET login_days = ?, open_sign = 1, update_ts = ? WHERE uid = ?",
                (new_days, now_ts, uid)
            )
        else:
            new_days = 1
            ver = 2
            ctx.db.execute(
                "INSERT INTO accumulate_sign (uid, version, open_sign, login_days, update_ts) VALUES (?, ?, 1, 1, ?)",
                (uid, ver, now_ts)
            )
        _push_accumulate_sign_diff(ctx, uid, new_days, version=ver)
    except Exception as e:
        if hasattr(ctx, 'log'):
            ctx.log(f"[TaskListener] 时迹馈赠联动异常: {e}", "WARN")

    # 4. 家园图鉴任务全量自愈与校准 (Type 905)
    try:
        illu_c = sync_dorm_illu_tasks(ctx.db, uid)
        if illu_c:
            _push_task_diff(ctx, uid, illu_c)
    except Exception as e:
        if hasattr(ctx, 'log'):
            ctx.log(f"[TaskListener] 图鉴收集任务自愈异常: {e}", "WARN")


@bus.subscribe(Events.ACTIVITY_POINT_GAIN)
def on_activity_point_gain(ctx, uid, pt_id=1, amount=0, item_id=22, **kwargs):
    """获得活跃度/活动PT (cond=300: 71002 战令每日活跃度达到100, 100016 乐园印戳20000; cond=2021: 10008/10009 领营收10000/40000)"""
    if amount <= 0:
        return
    c1 = _trigger_task_condition(ctx.db, uid, condition=300, delta=amount, param=item_id)
    c2 = _trigger_task_condition(ctx.db, uid, condition=300, delta=amount, param=pt_id)
    c3 = _trigger_task_condition(ctx.db, uid, condition=300, delta=amount, param=None)
    c4 = _trigger_task_condition(ctx.db, uid, condition=2021, delta=amount, param=item_id)
    _push_task_diff(ctx, uid, c1 + c2 + c3 + c4)


@bus.subscribe(Events.MONSTER_KILL)
def on_monster_kill(ctx, uid, monster_id=0, count=1, race=0, monster_type=0, **kwargs):
    """击杀怪物/视骸/精英/Boss (cond=401 击杀指定怪; cond=403 击杀精英; cond=404 击杀Boss; cond=406 击杀指定种族)"""
    if count <= 0:
        return
    c1 = _trigger_task_condition(ctx.db, uid, condition=401, delta=count, param=monster_id)
    c2 = _trigger_task_condition(ctx.db, uid, condition=406, delta=count, param=race)
    c3 = []
    if monster_type == 1: # 精英怪
        c3 = _trigger_task_condition(ctx.db, uid, condition=403, delta=count)
    elif monster_type == 2: # 首领 Boss
        c3 = _trigger_task_condition(ctx.db, uid, condition=404, delta=count)
    _push_task_diff(ctx, uid, c1 + c2 + c3)


@bus.subscribe(Events.STAGE_FIRST_CLEAR)
def on_stage_first_clear(ctx, uid, stage_id=0, **kwargs):
    """首通关卡专属任务推进 (cond=400 首次通关指定关卡)"""
    if stage_id:
        c = _trigger_task_condition(ctx.db, uid, condition=400, delta=1, param=stage_id)
        _push_task_diff(ctx, uid, c)


@bus.subscribe(Events.PLAYER_LEVEL_UP)
def on_player_level_up(ctx, uid, old_lv=1, new_lv=1, stamina_gain=0, **kwargs):
    """
    账号升级事件订阅：
    1. 推进等级相关任务/成就 (如 cond=2 达到指定管理员等级)；
    2. 记录审计日志。
    """
    if hasattr(ctx, "log"):
        ctx.log(f"[TaskListener] 玩家 UID={uid} 等级提升: {old_lv} -> {new_lv}, 奖励体力={stamina_gain}")
    changed = []
    db = getattr(ctx, "db", None)
    if db:
        changed += _trigger_task_condition(db, uid, condition=2, delta=new_lv, set_value=True)
    if changed:
        _push_task_diff(ctx, uid, changed)


# ==================== 家园图鉴全量自愈服务 ====================

def sync_dorm_illu_tasks(db, uid):
    """
    家园图鉴收集类任务全量自愈与校准 (Type 905):
    - cond=2013: 拥有修正者总数 (227001~227006, 227015, 227017, need: 10~80)
    - cond=2014: 拥有舞蹈总数 (227007, 227008, need: 35, 40)
    - cond=2015: 拥有家具种类数 (227009~227014, 227016, need: 50~350)
    返回变化的 changed diff 列表。
    """
    if not db or not uid:
        return []
    changed = []
    try:
        # 1. 修正者总数 (cond=2013)
        h_rows = db.query("SELECT COUNT(*) AS cnt FROM hero WHERE uid=? AND unlock=1", (uid,))
        h_cnt = int(h_rows[0]["cnt"]) if h_rows else 0
        if h_cnt > 0:
            changed += _trigger_task_condition(db, uid, condition=2013, delta=h_cnt, set_value=True)

        # 2. 舞蹈总数 (cond=2014)
        d_cnt = 0
        try:
            d_rows = db.query("SELECT COUNT(*) AS cnt FROM idol_dance_collection WHERE uid=?", (uid,))
            d_cnt = int(d_rows[0]["cnt"]) if d_rows else 0
        except Exception:
            pass
        if d_cnt > 0:
            changed += _trigger_task_condition(db, uid, condition=2014, delta=d_cnt, set_value=True)

        # 3. 家具种类数 (cond=2015，按持有数量>0的不同家具ID去重统计)
        f_rows = db.query("SELECT COUNT(DISTINCT furniture_id) AS cnt FROM backhome_furniture WHERE uid=? AND num>0", (uid,))
        f_cnt = int(f_rows[0]["cnt"]) if f_rows else 0
        if f_cnt > 0:
            changed += _trigger_task_condition(db, uid, condition=2015, delta=f_cnt, set_value=True)
    except Exception as e:
        pass
    return changed


@bus.subscribe(Events.SUMMER_PUB_LEVEL_PASS)
def on_summer_pub_level_pass(ctx, uid, level_id=None, **kwargs):
    """夏日餐厅关卡推进：驱动 Condition 350001 (通关夏日酒馆/餐厅指定 level_id) 并原子下发差分帧"""
    if not uid or not level_id:
        return
    db = getattr(ctx, "db", None) if ctx else None
    if not db:
        from account_db import get_db
        db = get_db()
    changed = _trigger_task_condition(db, uid, condition=350001, delta=1, param=level_id)
    _push_task_diff(ctx, uid, changed)


@bus.subscribe(Events.ROGUE_CARD_POST_FINISH)
def on_rogue_card_post_finish(ctx, uid, post_id=None, **kwargs):
    """诡谈夜话完成帖子：驱动 Condition 411008 (完成指定 post_id 帖子) 并原子下发差分帧"""
    if not uid or not post_id:
        return
    db = getattr(ctx, "db", None) if ctx else None
    if not db:
        from account_db import get_db
        db = get_db()
    changed = _trigger_task_condition(db, uid, condition=411008, delta=1, param=post_id)
    _push_task_diff(ctx, uid, changed)





