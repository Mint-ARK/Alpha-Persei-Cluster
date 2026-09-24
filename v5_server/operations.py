# -*- coding: utf-8 -*-
"""
operations.py — CORE 操作调度：玩家操作 → 解析/校验/计算/落库/双端响应

每个操作是 Operation 子类（四阶段固定：parse→validate→apply→respond），
@operation 装饰器自动注册进 core.OPERATIONS。
"""
import os
import json
import time
import random as _random

from core import Operation, OperationError, operation
from middleware import DownFrame
from polyhedron_service import PolyhedronRunManager
import event_bus
import task_listener
import illustrated_listener

# ---------------- BS 结算广播订阅者装配（import 副作用注册监听器） ----------------
# hero_service：熟练度/胜场/档案经验/角色经验 + proficiency_up 广播（STAGE_PASS 订阅）
from hero_service import HeroService as _HeroSvc_activate
_HeroSvc_activate.get_instance()
# cooperation_skill_server：连携出场计量 → COMBO_SKILL_PROGRESS 广播
import cooperation_skill_server as _coopskill_activate
_coopskill_activate.CooperationSkillServer.get_instance()
# team_server：关卡通关阵容增量记录（关卡 ID 主键）
import team_server as _team_activate
_team_activate.TeamServer.get_instance()
# trust_service：COMBO_SKILL_PROGRESS 订阅（连携升级进度/红点）——显式激活防懒加载滞后
from trust_service import TrustService as _TrustSvc_activate
_TrustSvc_activate.get_instance()

# backhome_service：游园街领域模块与 TIME_TICK 订阅——显式激活防懒加载滞后
from backhome_service import BackHomeService as _BackHomeSvc_activate
_BackHomeSvc_activate.get_instance()

# mail_service：统一邮件与信件收藏室领域服务
from mail_service import MailService as _MailSvc_activate
_MailSvc_activate.get_instance()


def _build_18003_frame(ctx, run_data, uid=None):
    """
    客户端 PolyhedronData.lua 在 18003 处理中调用了 uv0:UpdateProcess()，
    但客户端 PolyhedronTemplate.lua 中实际定义为 UpdatePocess（客户端代码少拼了 r）。
    下发 18003 会直接导致客户端触发 attempt to call method 'UpdateProcess' (a nil value) 崩溃死锁。
    因此服务端统一推送 18001（走 UpdateGame -> UpdatePocess），100% 顺畅更新全量进度与红点！
    """
    frames = []
    if ctx.db:
        try:
            import generator as _gen
            import res_version_manager as _rvm
            version_cfg = _rvm.get_version_config()
            target_uid = uid
            if not target_uid and isinstance(run_data, dict):
                target_uid = run_data.get("uid")
            if not target_uid:
                target_uid = getattr(ctx, "uid", None)
            if not target_uid:
                rows = ctx.db.query("SELECT uid FROM users LIMIT 1") or ctx.db.query("SELECT uid FROM currency LIMIT 1")
                if rows:
                    target_uid = rows[0]["uid"]
                else:
                    target_uid = 2174928301
            p18001 = _gen.gen_payload(
                18001, uid=target_uid, db=ctx.db,
                res_version=version_cfg["version"]
            )
            if p18001:
                frames.append(DownFrame(18001, p18001))

            if ctx.codec_encode:
                import weekly_challenge_service as _wcs
                nxt_mon = _wcs.get_next_monday_5am()
                p11003 = ctx.codec_encode("sc_11003", {
                    "activity": {
                        "activity_id": int(version_cfg["polyhedron_activity_id"]),
                        "start_time": nxt_mon - 604800,
                        "stop_time": _rvm.FAR_FUTURE_TIMESTAMP,
                        "state": 1,
                        "theme": int(version_cfg["theme_id"]),
                        "template": 73
                    }
                })
                if p11003:
                    frames.append(DownFrame(11003, p11003))
        except Exception as e:
            ctx.log(f"_build_18001_frame error: {e}")
    return frames


# ---------------- 道具动态路由（item_catalog 驱动，无硬编码表名） ----------------

def _item_table(ctx, item_id):
    """按 item_catalog.type 路由条目到库表。"""
    iid = int(item_id)
    if iid < 1000 or (54000 <= iid <= 55000) or (60000 <= iid <= 61000):
        return "currency"
    try:
        rows = ctx.db.query("SELECT type FROM item_catalog WHERE id=?", (iid,))
        if rows and rows[0]["type"]:
            itype = rows[0]["type"]
            if itype == 1:
                return "currency"
            if itype == 2:
                return "hero"
            if itype == 3:
                return "hero_piece"
            if itype == 7:
                return "equip"
            if itype == 9:
                return "servant"
            if itype == 8:
                return "skin"
            if itype == 21:
                return "scene"
            if itype == 15:
                return "furniture"
            if itype in (11, 12, 13, 18, 22, 23, 25, 26, 28):
                return "cosmetic"
            if itype in (4, 5, 6, 10, 14, 20):
                return "material"
    except Exception:
        pass
    return "material"


def _item_balance(ctx, uid, item_id):
    """查条目余额：返回 (table, num)。"""
    iid = int(item_id)
    t = _item_table(ctx, iid)
    if t == "currency":
        rows = ctx.db.query("SELECT num FROM currency WHERE uid=? AND id=?", (uid, iid))
        return t, (rows[0]["num"] if rows else 0)
    elif t == "hero":
        rows = ctx.db.query("SELECT unlock FROM hero WHERE uid=? AND id=?", (uid, iid))
        return t, (1 if rows and rows[0].get("unlock", 0) == 1 else 0)
    elif t == "hero_piece":
        hid = iid % 10000 if iid > 10000 else iid
        rows = ctx.db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hid))
        return t, (rows[0]["num"] if rows else 0)
    elif t == "equip":
        rows = ctx.db.query("SELECT COUNT(*) AS num FROM equip WHERE uid=? AND prefab_id=?", (uid, iid))
        return t, (rows[0]["num"] if rows else 0)
    elif t == "servant":
        rows = ctx.db.query("SELECT COUNT(*) AS num FROM servant WHERE uid=? AND prefab_id=?", (uid, iid))
        return t, (rows[0]["num"] if rows else 0)
    elif t == "cosmetic":
        return t, 0
    else:
        rows = ctx.db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, iid))
        return t, (rows[0]["num"] if rows else 0)


_EQUIP_SUIT_POS_MAP_CACHE = None

def _get_equip_suit_pos_map():
    global _EQUIP_SUIT_POS_MAP_CACHE
    if _EQUIP_SUIT_POS_MAP_CACHE is not None:
        return _EQUIP_SUIT_POS_MAP_CACHE
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "equip_suit_pos_map.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                _EQUIP_SUIT_POS_MAP_CACHE = json.load(f)
                return _EQUIP_SUIT_POS_MAP_CACHE
        except Exception:
            pass
    _EQUIP_SUIT_POS_MAP_CACHE = {}
    return _EQUIP_SUIT_POS_MAP_CACHE


# 🚀 架构迁移开关：True=启用全新独立道具库服务；False=完全回退到旧内核
USE_NEW_INVENTORY_SERVICE = True


def _item_add(ctx, uid, item_id, delta):
    """加条目（优先委托给 inventory_service，支持异常自动降级走就地旧逻辑）。"""
    if USE_NEW_INVENTORY_SERVICE:
        try:
            import inventory_service
            inventory_service.grant_item(ctx, uid, item_id, delta)
            return
        except Exception as e:
            if hasattr(ctx, "log"):
                ctx.log(f"[InventoryService WARN] grant_item 异常: {e}，降级至旧逻辑")
            else:
                logger.warning(f"[InventoryService WARN] grant_item 异常: {e}，降级至旧逻辑")

    # ================= 🛡️ 旧版就地代码（完全保留保底） =================
    iid = int(item_id)
    delta = int(delta)
    if delta <= 0:
        return
    now = int(time.time())
    
    t = _item_table(ctx, iid)
    
    if t == "currency":
        ctx.db.execute(
            "INSERT INTO currency (uid, id, num) VALUES (?,?,?) "
            "ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
            (uid, iid, delta))
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.add(iid)
            
    elif t == "hero":
        _grant_hero(ctx, uid, iid)
        if hasattr(ctx, "touched_heroes"):
            ctx.touched_heroes.add(iid)

    elif t == "hero_piece":
        hid = iid % 10000 if iid > 10000 else iid
        ctx.db.execute(
            "INSERT INTO hero_piece (uid, hero_id, num) VALUES (?,?,?) "
            "ON CONFLICT(uid, hero_id) DO UPDATE SET num = num + excluded.num",
            (uid, hid, delta))
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.add(iid)
            
    elif t == "equip":
        eq_map = _get_equip_suit_pos_map()
        eq_info = eq_map.get(str(iid))
        suit_id = eq_info["suit"] if eq_info else (iid // 100 if iid > 1000 else iid)
        pos = eq_info["pos"] if eq_info else ((iid // 10000) % 10)
        for _ in range(delta):
            r = ctx.db.query("SELECT COALESCE(MAX(id), 0) + 1 AS new_id FROM equip WHERE uid=?", (uid,))
            new_id = r[0]["new_id"] if r else 1
            ctx.db.execute(
                "INSERT INTO equip (uid, id, prefab_id, exp, hero_id, is_lock, now_break_level, enchant_slots, race, race_preview, update_ts, is_init) "
                "VALUES (?, ?, ?, 0, 0, 0, 0, '[]', 0, 0, ?, 0)",
                (uid, new_id, iid, now))
        try:
            event_bus.bus.emit(event_bus.Events.EQUIP_OBTAIN, ctx, uid, suit_id=suit_id, pos=pos, prefab_id=iid, count=delta)
        except Exception:
            pass
                
    elif t == "servant":
        for _ in range(delta):
            r = ctx.db.query("SELECT COALESCE(MAX(id), 0) + 1 AS new_id FROM servant WHERE uid=?", (uid,))
            new_id = r[0]["new_id"] if r else 1
            ctx.db.execute(
                "INSERT INTO servant (uid, id, prefab_id, stage, owned, is_locked, update_ts) "
                "VALUES (?, ?, ?, 1, 1, 0, ?)",
                (uid, new_id, iid, now))
        try:
            event_bus.bus.emit(event_bus.Events.SERVANT_OBTAIN, ctx, uid, servant_id=iid, count=delta)
        except Exception:
            pass

                
    elif t == "skin":
        ctx.db.upsert("player_skin_unlocked", uid,
                      {"skin_id": iid, "unlock_ts": now, "update_ts": now},
                      keys=("uid", "skin_id"))
                      
    elif t == "scene":
        ctx.db.upsert("user_scene", uid,
                      {"scene_id": iid, "lasted_time": 0, "obtain_time": now, "update_ts": now},
                      keys=("uid", "scene_id"))
                      
    elif t == "furniture" and hasattr(ctx.db, "add_furniture"):
        ctx.db.add_furniture(uid, iid, delta)
        
    elif t == "cosmetic":
        pass  # 装饰/贴纸/气泡暂不落 material 表
        
    else:
        # 仅合法材料类型写入 material 表
        cat_rows = ctx.db.query("SELECT type FROM item_catalog WHERE id=?", (iid,))
        itype = cat_rows[0].get("type") if cat_rows else None
        if itype in (4, 5, 6, 10, 14, 20) and iid not in (1, 116601, 52001, 52002, 52003):
            ctx.db.execute(
                "INSERT INTO material (uid, id, num) VALUES (?,?,?) "
                "ON CONFLICT(uid, id) DO UPDATE SET num = num + excluded.num",
                (uid, iid, delta))
            if hasattr(ctx, "touched_items"):
                ctx.touched_items.add(iid)


def _item_deduct(ctx, uid, item_id, num):
    """扣条目（余额校验 + 按路由表扣减）。不足抛 OperationError(3)。"""
    iid = int(item_id)
    num = int(num)

    if USE_NEW_INVENTORY_SERVICE:
        try:
            import inventory_service
            succ = inventory_service.cost_item(ctx, uid, iid, num)
            if not succ:
                t, have = inventory_service.get_item_balance(ctx, uid, iid)
                raise OperationError(3, f"条目不足: id={iid} 需{num} 有{have}（{t}表）")
            return
        except OperationError:
            raise
        except Exception as e:
            if hasattr(ctx, "log"):
                ctx.log(f"[InventoryService WARN] cost_item 异常: {e}，降级至旧逻辑")
            else:
                logger.warning(f"[InventoryService WARN] cost_item 异常: {e}，降级至旧逻辑")

    # ================= 🛡️ 旧版就地代码（完全保留保底） =================
    t, have = _item_balance(ctx, uid, iid)
    if have < num:
        raise OperationError(3, f"条目不足: id={iid} 需{num} 有{have}（{t}表）")
    
    if t == "currency":
        ctx.db.execute("UPDATE currency SET num=num-? WHERE uid=? AND id=?", (num, uid, iid))
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.add(iid)
    elif t == "hero_piece":
        hid = iid % 10000 if iid > 10000 else iid
        ctx.db.execute("UPDATE hero_piece SET num=num-? WHERE uid=? AND hero_id=?", (num, uid, hid))
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.add(iid)
    elif t == "equip":
        ctx.db.execute("DELETE FROM equip WHERE rowid IN (SELECT rowid FROM equip WHERE uid=? AND prefab_id=? AND (hero_id=0 OR hero_id IS NULL) AND is_lock=0 LIMIT ?)", (uid, iid, num))
    elif t == "servant":
        ctx.db.execute("DELETE FROM servant WHERE rowid IN (SELECT rowid FROM servant WHERE uid=? AND prefab_id=? AND is_locked=0 LIMIT ?)", (uid, iid, num))
    else:
        ctx.db.execute("UPDATE material SET num=num-? WHERE uid=? AND id=?", (num, uid, iid))
        if hasattr(ctx, "touched_items"):
            ctx.touched_items.add(iid)
            
    try:
        if iid == 4:
            event_bus.bus.emit(event_bus.Events.STAMINA_COST, ctx, uid, amount=num)
            try:
                import fatigue_service as _fs
                _fs.on_fatigue_consume(ctx.db, uid, num)
            except Exception:
                pass
        elif iid == 2:
            event_bus.bus.emit(event_bus.Events.GOLD_COST, ctx, uid, amount=num)
    except Exception:
        pass
    return t


def _refresh_frames(ctx, uid, hero_id=None):
    """操作后补推数据原子差量刷新帧：sc_14007（单英雄实时数据）+ sc_17023（道具/货币差量原子推送）。
    彻底剥离 15009/17009/14019/46011/50001 等全量重刷包，消灭客户端 GC 卡顿。"""
    out = []
    # 1. 优先推送原子差量帧 (sc_17023: 货币、材料、被销毁钥从/刻印)
    try:
        import generator as _gen
        touched = getattr(ctx, "touched_items", None)
        rem_eq = getattr(ctx, "removed_equips", None)
        rem_wl = getattr(ctx, "removed_weapons", None)
        del_servants = getattr(ctx, "deleted_servants", None)
        if del_servants:
            wl_from_del = [{"uid": x["uid"], "item": {"id": x["id"], "num": 0}} for x in del_servants if x.get("id")]
            rem_wl = (rem_wl or []) + wl_from_del
        if touched is not None or rem_eq or rem_wl:
            p = _gen.gen_payload(17023, uid=uid, db=ctx.db, touched_items=(touched or set()), equip_list=rem_eq, weapon_list=rem_wl)
            if p:
                out.append(DownFrame(17023, p))
            if touched and (4 in touched):
                p_17025 = _gen.gen_payload(17025, uid=uid, db=ctx.db)
                if p_17025:
                    out.append(DownFrame(17025, p_17025))

        # 表情/贴纸专属原子差量推送 (sc_12039)
        need_stickers = getattr(ctx, "touched_chat_stickers", False)
        if not need_stickers and touched:
            for iid in touched:
                if 90000 <= iid < 92000:
                    need_stickers = True
                    break
        if need_stickers:
            p_12039 = _gen.gen_payload(12039, uid=uid, db=ctx.db)
            if p_12039:
                out.append(DownFrame(12039, p_12039))
    except Exception:
        pass
    # 2. 英雄实时数据帧 (sc_14007)
    if hero_id:
        try:
            import hero_codec as _hc
            hf = _hc.build_hero_14007_frame(ctx.db, uid, int(hero_id))
            if hf:
                out.append(hf)
        except Exception:
            pass

    # 3. 收集由 EventBus / Listener 在当前上下文中产生的待发帧（如 sc_28007）
    pending = []
    if hasattr(ctx, "pop_pending_frames"):
        pending = ctx.pop_pending_frames()
    elif hasattr(ctx, "pending_frames") and ctx.pending_frames:
        pending = list(ctx.pending_frames)
        ctx.pending_frames = []

    # 过滤掉任何空载荷或空 progress_list 的无效 28007 任务帧
    task_frames = []
    for f in pending:
        if f.cmd == 28007:
            if f.payload and len(f.payload) > 0:
                try:
                    import codec as _codec
                    dec = _codec.decode(f.payload, "sc_28007")
                    if dec.get("progress_list"):
                        task_frames.append(f)
                except Exception:
                    pass
    other_frames = [f for f in pending if f.cmd != 28007]
    if len(task_frames) > 1:
        merged_progress = []
        seen = {}
        for f in task_frames:
            try:
                import codec as _codec
                dec = _codec.decode(f.payload, "sc_28007")
                for p in dec.get("progress_list", []):
                    tid = p.get("id")
                    if tid not in seen:
                        seen[tid] = p
                        merged_progress.append(p)
                    else:
                        seen[tid]["progress"] = max(seen[tid].get("progress", 0), p.get("progress", 0))
            except Exception:
                pass
        if merged_progress:
            import codec as _codec
            p_28007 = _codec.encode("sc_28007", {"progress_list": merged_progress})
            if p_28007:
                out.append(DownFrame(28007, p_28007))
        out.extend(other_frames)
    elif len(task_frames) == 1:
        out.append(task_frames[0])
        out.extend(other_frames)
    else:
        out.extend(other_frames)

    return out


# ---------------- 11081 签到 ----------------

@operation
class SignOp(Operation):
    """签到：cs_11081 {activity_id} → sc_11082 result=0 + sign_num + item_list。"""

    cmd = 11081
    sc = 11082

    def parse(self, req, ctx):
        d = super().parse(req, ctx)
        if not isinstance(d, dict):
            raise OperationError(1, "签到请求解码失败")
        return d

    def validate(self, data, ctx):
        if "activity_id" not in data:
            raise OperationError(2, "签到缺少 activity_id")
        return data

    def apply(self, data, ctx):
        aid = int(data["activity_id"])
        import generator as _g
        now = _g.get_game_time()
        now_ts = int(time.time())
        st = _g._sign_state(ctx.db, self.uid, aid)
        if st["signed_today"]:
            if aid in (_g._SIGN_DAILY, _g._SIGN_MONTHCARD):
                rw = _g._sign_reward(ctx.db, now.tm_mon, st["sign_count"])
            else:
                rw = _g.seven_day_reward(aid, st["sign_count"])
            items = [{"item_id": rw[0], "item_num": rw[1]}] if rw and rw[0] else []
            return {"already": True, "sign_num": st["sign_count"], "aid": aid, "items": items}
        new_count = st["sign_count"] + 1
        if aid in (_g._SIGN_DAILY, _g._SIGN_MONTHCARD):
            rw = _g._sign_reward(ctx.db, now.tm_mon, new_count)
        else:
            # 七日/限时签到：奖励来自活动 config_list 第 N 项（signcfg），非月历表
            rw = _g.seven_day_reward(aid, new_count)
            if rw is None:
                raise OperationError(2, f"活动 {aid} 签到次数已领完或未知（第{new_count}天）")
        items = [{"item_id": rw[0], "item_num": rw[1]}] if rw and rw[0] else []
        for it in items:
            _item_add(ctx, self.uid, it["item_id"], it["item_num"])
        # 七日语义 count 只增不减（此前 len(sign_list) 会把种子 count 归零）
        new_list = st["sign_list"] + [now.tm_mday] if aid == _g._SIGN_DAILY else list(st["sign_list"])
        ctx.db.execute(
            "INSERT INTO sign (uid, activity_id, year, month, day, sign_list, sign_count, last_sign_ts) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(uid, activity_id) DO UPDATE SET year=excluded.year, month=excluded.month, "
            "day=excluded.day, sign_list=excluded.sign_list, sign_count=excluded.sign_count, "
            "last_sign_ts=excluded.last_sign_ts",
            (self.uid, aid, now.tm_year, now.tm_mon, now.tm_mday,
             json.dumps(new_list), new_count, now_ts))
        try:
            event_bus.bus.emit(event_bus.Events.USER_LOGIN, ctx, self.uid, is_first_login=True)
        except Exception:
            pass
        return {"already": False, "sign_num": new_count, "aid": aid, "items": items}

    def respond(self, result, data, ctx):
        if result.get("already"):
            payload = ctx.codec_encode("sc_11082", {"result": 0, "sign_num": result["sign_num"],
                                                     "item_list": result.get("items") or [],
                                                     "today_buy_num": 0, "left_sign_time": 0}) or b"\x08\x00"
            ctx.log(f"cs_11081 -> 今日已签 activity={result['aid']} 累计{result['sign_num']}天 奖={result.get('items')}")
            return [DownFrame(self.sc, payload)]
        payload = ctx.codec_encode("sc_11082", {
            "result": 0, "sign_num": result["sign_num"],
            "item_list": result["items"], "today_buy_num": 0, "left_sign_time": 0
        }) or b"\x08\x00"
        ctx.log(f"cs_11081 -> 签到成功 activity={result['aid']} 第{result['sign_num']}天 奖={result['items']}")
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class DailySignOp(Operation):
    """每日签到/七日签到：cs_11010 {activity_id} → sc_11011 {result: 0, item_list}。

    2026-08-22 修正（登录签到窗反复弹+空奖励的根因）：
    ① 已签判定用 _sign_state.signed_today（七日/限时活动按 last_sign_ts 当日判），
      不再自行用 mday∈sign_list 重判——七日行 sign_list 恒空会重复发奖；
    ② sign_count = st.sign_count + 1，不再 len(sign_list)——七日种子 count(7) 被归零成 1，
      客户端 sc_11015 拿到 count=1 → 渲染"第二天"；同日二次请求落"已签"分支回空 item_list
      → 签到窗弹"第二天"但奖励栏空白；
    ③ 七日活动奖励走 config_list/sign_cfg（活动配置第 N 项），非月历表。
    """

    cmd = 11010
    sc = 11011

    def apply(self, data, ctx):
        ctx.uid = self.uid
        act_id = int(data.get("activity_id") or 0)
        import time as _t
        import json as _j
        import generator as _g
        now = _g.get_game_time()
        now_ts = int(_t.time())

        # 默认 daily sign activity_id 映射为 3 (或按请求原值)
        sign_act_id = 3 if act_id in (0, 3, 10001) else act_id

        st = _g._sign_state(ctx.db, self.uid, sign_act_id)
        rewards = []
        sign_list = list(st["sign_list"])
        sign_count = st["sign_count"]

        if not st["signed_today"]:
            sign_count = st["sign_count"] + 1
            if sign_act_id in (_g._SIGN_DAILY, _g._SIGN_MONTHCARD):
                rw = _g._sign_reward(ctx.db, now.tm_mon, sign_count)
                new_list = sign_list + [now.tm_mday]
                # 联动时迹回赠：已统一交由 task_listener.on_user_login 事件总线幂等驱动
            else:
                # 七日/限时签到：奖励来自活动 config_list 第 N 项（signcfg），非月历表
                rw = _g.seven_day_reward(sign_act_id, sign_count)
                if rw is None:
                    raise OperationError(2, f"活动 {sign_act_id} 签到次数已领完或未知（第{sign_count}天）")
                new_list = sign_list
            r_id, r_num = rw if rw else (1, 100)

            _item_add(ctx, self.uid, r_id, r_num)
            rewards.append({"id": r_id, "num": r_num})

            ctx.db.execute(
                "INSERT INTO sign (uid, activity_id, year, month, day, sign_list, sign_count, last_sign_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(uid, activity_id) DO UPDATE SET "
                "year=excluded.year, month=excluded.month, day=excluded.day, "
                "sign_list=excluded.sign_list, sign_count=excluded.sign_count, last_sign_ts=excluded.last_sign_ts",
                (self.uid, sign_act_id, now.tm_year, now.tm_mon, now.tm_mday,
                 _j.dumps(new_list), sign_count, now_ts)
            )
            sign_list = new_list
            try:
                event_bus.bus.emit(event_bus.Events.USER_LOGIN, ctx, self.uid, is_first_login=True)
            except Exception:
                pass
        else:
            # 今日已签：按当前 sign_count 回显今日已得奖励，确保界面展示不为空
            if sign_act_id in (_g._SIGN_DAILY, _g._SIGN_MONTHCARD):
                rw = _g._sign_reward(ctx.db, now.tm_mon, sign_count)
            else:
                rw = _g.seven_day_reward(sign_act_id, sign_count)
            if rw and rw[0]:
                rewards.append({"id": rw[0], "num": rw[1]})

        return {"activity_id": act_id, "rewards": rewards, "sign_list": sign_list, "sign_count": sign_count, "already": st["signed_today"]}

    def respond(self, result, data, ctx):
        rec_items = [{"id": it["id"], "num": it["num"]} for it in result["rewards"]]
        payload = ctx.codec_encode("sc_11011", {"result": 0, "item_list": rec_items}) or b"\x08\x00"
        frames = [DownFrame(self.sc, payload)]
        # 签到成功后回推最新 sc_11013（日历含今天）与 sc_17027（时迹回赠累计天数）
        if ctx.generator is not None and result["rewards"]:
            try:
                p13 = ctx.generator.gen_payload(11013, uid=self.uid, db=ctx.db)
                if p13:
                    frames.append(DownFrame(11013, p13))
                p27 = ctx.generator.gen_payload(17027, uid=self.uid, db=ctx.db)
                if p27:
                    frames.append(DownFrame(17027, p27))
            except Exception:
                pass
        ctx.log(f"cs_11010 -> 每日签到 act={result['activity_id']} 累计{result['sign_count']}天 {'(今日已签)' if result.get('already') else ''} 获得={result['rewards']}")
        return frames + _refresh_frames(ctx, self.uid)


@operation
class MonthCardBonusOp(Operation):
    """月卡每日签到领奖：cs_34024 {} → sc_34025 {result: 0, is_sign: 1, reward_list}。"""

    cmd = 34024
    sc = 34025

    def apply(self, data, ctx):
        uid = self.uid
        import time as _t
        import json as _j
        import generator as _g
        now = _g.get_game_time()
        now_ts = int(_t.time())

        # 每日一次守卫：今日已签 → 不再发放（原先无条件发 90，可连点无限刷）
        st = _g._sign_state(ctx.db, uid, _g._SIGN_MONTHCARD)
        if st["signed_today"]:
            return {"already": True, "is_sign": 1, "reward": {"id": 1, "num": 90}}

        _item_add(ctx, uid, 1, 90)   # 90 移转之辉 (currency id 1)

        ctx.db.execute(
            "INSERT INTO month_card (uid, monthly_card_num, monthly_card_timestamp, is_sign, update_ts) "
            "VALUES (?, 1, ?, 1, ?) "
            "ON CONFLICT(uid) DO UPDATE SET is_sign=1, update_ts=excluded.update_ts",
            (uid, now_ts + 30 * 86400, now_ts)
        )

        # sign 表累加（原先每次都把 sign_list 覆盖成 [今天]，跨天记录会丢）
        sl = list(st["sign_list"])
        if now.tm_mday not in sl:
            sl.append(now.tm_mday)
        ctx.db.execute(
            "INSERT INTO sign (uid, activity_id, year, month, day, sign_list, sign_count, last_sign_ts) "
            "VALUES (?, 34024, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid, activity_id) DO UPDATE SET "
            "year=excluded.year, month=excluded.month, day=excluded.day, "
            "sign_list=excluded.sign_list, sign_count=excluded.sign_count, "
            "last_sign_ts=excluded.last_sign_ts",
            (uid, now.tm_year, now.tm_mon, now.tm_mday, _j.dumps(sl), len(sl), now_ts)
        )
        try:
            event_bus.bus.emit(event_bus.Events.USER_LOGIN, ctx, self.uid, is_first_login=True)
        except Exception:
            pass
        return {"already": False, "is_sign": 1, "reward": {"id": 1, "num": 90}}


    def respond(self, result, data, ctx):
        # is_sign 恒为 1：客户端 SignToday(is_sign) 据此不再弹签到面板（已签时奖励列表为空）
        rewards = [result["reward"]] if result.get("reward") else []
        payload = ctx.codec_encode("sc_34025", {
            "result": 0,
            "is_sign": 1,
            "reward_list": rewards
        }) or b"\x08\x00"
        ctx.log("cs_34024 -> 月卡签到%s" % ("（今日已签，空奖励）" if result.get("already")
                                            else "成功，获得 90 移转之辉"))
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class RequestGSPayOp(Operation):
    """发起支付下单：cs_34010 {id, number, shop_id, buy_id, buy_source, ticket} → sc_34011 {result, order}。"""

    cmd = 34010
    sc = 34011

    def apply(self, data, ctx):
        import recharge_service
        good_id = int(data.get("id") or 0)
        num = int(data.get("number") or 1)
        shop_id = int(data.get("shop_id") or 0)
        buy_id = int(data.get("buy_id") or 0)
        target_uid = self.uid or getattr(ctx, "uid", 0)
        svc = recharge_service.RechargeService.get_instance(db=ctx.db)
        return svc.handle_pay_order(ctx.db, target_uid, goods_id=good_id, num=num,
                                   shop_id=shop_id, buy_id=buy_id, log=ctx.log)

    def respond(self, result, data, ctx):
        p34011 = ctx.codec_encode("sc_34011", {
            "result": 0,
            "order": result["order"]
        }) or b""
        frames = [DownFrame(self.sc, p34011)]
        try:
            p34009 = ctx.codec_encode("sc_34009", {
                "order": result["order"],
                "reward": result.get("reward") or []
            })
            if p34009:
                frames.append(DownFrame(34009, p34009))
        except Exception as _e:
            ctx.log(f"[PAY] sc_34009 发货帧组包异常: {_e}", "WARN")

        # 同步推送最新的累充状态 (sc_34007)、首充双倍状态 (sc_34021) 与新手充值状态 (sc_59009)
        try:
            target_uid = self.uid or getattr(ctx, "uid", 0)
            import recharge_service
            svc = recharge_service.RechargeService.get_instance(db=ctx.db)
            p34007 = ctx.codec_encode("sc_34007", svc.build_34007_payload(ctx.db, target_uid))
            if p34007:
                frames.append(DownFrame(34007, p34007))
            p34021 = ctx.codec_encode("sc_34021", svc.build_34021_payload(ctx.db, target_uid))
            if p34021:
                frames.append(DownFrame(34021, p34021))
            p59009 = ctx.codec_encode("sc_59009", svc.build_59009_payload(ctx.db, target_uid))
            if p59009:
                frames.append(DownFrame(59009, p59009))
            # 实时推送最新货币列表 sc_15009 (确保战令经验 14、移转之花 30 即刻到账生效)
            if ctx.generator is not None:
                p15009 = ctx.generator.gen_payload(15009, uid=target_uid, db=ctx.db)
                if p15009:
                    frames.append(DownFrame(15009, p15009))
            # 若购买的是战令/合约 (Type 3)，即时推送最新的战令状态 sc_34031
            if result.get("goods_type") == 3 or result.get("order", {}).get("goods_id") in (8, 9, 201, 202, 203, 211, 212, 213, 221, 222, 223):
                if ctx.generator is not None:
                    p34031 = ctx.generator.gen_payload(34031, uid=target_uid, db=ctx.db)
                    if p34031:
                        frames.append(DownFrame(34031, p34031))
            # 若发货奖励中包含场景背景，即时推送 sc_32009
            has_scene = any((6000 <= int(it.get("id") or 0) <= 6999) for it in result.get("reward", []))
            if has_scene and ctx.generator is not None:
                p32009 = ctx.generator.gen_payload(32009, uid=target_uid, db=ctx.db)
                if p32009:
                    frames.append(DownFrame(32009, p32009))
        except Exception as _pe:
            ctx.log(f"[PAY] 充值关联状态即时推送异常: {_pe}", "WARN")

        ctx.log(f"cs_34010 -> 支付发货成功 goods_id={result['order']['goods_id']} order_id={result['order']['order_id']} 奖励={result.get('reward')}")
        return frames + _refresh_frames(ctx, target_uid)


@operation
class GetTotalRechargeBonusOp(Operation):
    """领取累计充值奖励：cs_34012 {id_list} → sc_34013 {result, reward_list, id_list}。"""

    cmd = 34012
    sc = 34013

    def apply(self, data, ctx):
        import recharge_service
        id_list = data.get("id_list") or []
        svc = recharge_service.RechargeService.get_instance(db=ctx.db)
        return svc.handle_claim_total_bonus(ctx.db, ctx.uid, id_list, log=ctx.log)

    def respond(self, result, data, ctx):
        p34013 = ctx.codec_encode(self.sc, result) or b""
        frames = [DownFrame(self.sc, p34013)]
        try:
            import recharge_service
            svc = recharge_service.RechargeService.get_instance(db=ctx.db)
            p34007 = ctx.codec_encode("sc_34007", svc.build_34007_payload(ctx.db, ctx.uid))
            if p34007:
                frames.append(DownFrame(34007, p34007))
        except Exception:
            pass
        return frames


@operation
class GetVersionRechargeBonusOp(Operation):
    """领取版本限时累计充值奖励：cs_34118 {id_list} → sc_34119。"""

    cmd = 34118
    sc = 34119

    def apply(self, data, ctx):
        import recharge_service
        id_list = data.get("id_list") or []
        svc = recharge_service.RechargeService.get_instance(db=ctx.db)
        return svc.handle_claim_version_bonus(ctx.db, ctx.uid, id_list, log=ctx.log)

    def respond(self, result, data, ctx):
        p34119 = ctx.codec_encode(self.sc, result) or b""
        return [DownFrame(self.sc, p34119)]


def _is_today(ts):
    """时间戳是否落在本地当天（4 点刷新的日历语义由各活动自行处理，这里只判自然日）。"""
    if not ts:
        return False
    a = time.localtime(int(ts))
    b = time.localtime()
    return (a.tm_year, a.tm_mon, a.tm_mday) == (b.tm_year, b.tm_mon, b.tm_mday)


# ---------------- 28010 单个任务 / 28014 批量任务 ----------------

def _task_rewards(ctx, task_id):
    """读任务配置奖励：task_cfg.reward_json = [[item_id, num], ...]。"""
    rows = ctx.db.query("SELECT reward_json FROM task_cfg WHERE task_id=?", (int(task_id),))
    if not rows or not rows[0]["reward_json"]:
        return []
    try:
        raw = json.loads(rows[0]["reward_json"])
    except Exception:
        return []
    out = []
    for rw in raw or []:
        if isinstance(rw, dict):
            iid = rw.get("item_id", rw.get("id"))
            num = rw.get("item_num", rw.get("num", 1))
        elif isinstance(rw, (list, tuple)) and len(rw) >= 2:
            iid, num = rw[0], rw[1]
        else:
            continue
        try:
            out.append({"id": int(iid), "num": int(num)})
        except (TypeError, ValueError):
            continue
    return out


@operation
class TaskSubmitOp(Operation):
    """任务提交/领奖：cs_28010 {id} → sc_28011 {result, reward_list}。

    语义（对齐 client Lua）：progress >= need 表达成可领，claimed_ts > 0 表已领取。
    只有未领取的任务才发奖；已领取则幂等回 result=0 空奖励。
    """

    cmd = 28010
    sc = 28011

    def apply(self, data, ctx):
        uid = self.uid
        tid = int(data.get("id") or data.get("task_id") or 0)
        t = ctx.db.query("SELECT * FROM task WHERE uid=? AND task_id=?", (uid, tid))
        if not t:
            raise OperationError(406, f"任务 {tid} 不存在")
        t = t[0]
        if ctx.db.is_task_claimed(uid, tid):
            return {"task_id": tid, "already": True, "rewards": []}

        # 校验进度是否达成 (progress >= need)
        cfg_row = ctx.db.query("SELECT need FROM task_cfg WHERE task_id=?", (tid,))
        need = int(cfg_row[0]["need"]) if (cfg_row and cfg_row[0]["need"] is not None) else 1
        prog = int(t.get("progress") or 0)
        if prog < need:
            raise OperationError(2, f"任务 {tid} 未达成，不可领取 (当前进度 {prog}/{need})")

        rewards = _task_rewards(ctx, tid)
        has_ticket = False
        final_rewards = []
        for rw in rewards:
            if rw["id"] == 61:
                has_ticket = True
                # 游园街贴票每周获取上限 100 增量截断
                r_rows = ctx.db.query("SELECT weekly_point FROM idol_trainee_rank WHERE uid=?", (uid,))
                cur_weekly = int(r_rows[0]["weekly_point"] or 0) if r_rows else 0
                avail = max(0, 100 - cur_weekly)
                gain = min(rw["num"], avail)
                if gain > 0:
                    _item_add(ctx, uid, 61, gain)
                    new_weekly = cur_weekly + gain
                    now_ts = int(time.time())
                    ctx.db.execute(
                        "INSERT INTO idol_trainee_rank (uid, weekly_point, update_ts) VALUES (?, ?, ?) "
                        "ON CONFLICT(uid) DO UPDATE SET weekly_point=?, update_ts=?",
                        (uid, new_weekly, now_ts, new_weekly, now_ts)
                    )
                    final_rewards.append({"id": 61, "num": gain})
            else:
                _item_add(ctx, uid, rw["id"], rw["num"])
                final_rewards.append(rw)
                if not USE_NEW_INVENTORY_SERVICE:
                    if rw["id"] == 22:
                        ctx.db.add_activity_point(uid, 1, rw["num"])
                    elif rw["id"] == 35:
                        ctx.db.add_activity_point(uid, 3, rw["num"])
                    elif rw["id"] == 23:
                        ctx.db.add_activity_point(uid, 2, rw["num"])
                if rw["id"] == 22:
                    try:
                        event_bus.bus.emit(event_bus.Events.ACTIVITY_POINT_GAIN, ctx, uid, pt_id=1, amount=rw["num"], item_id=22)
                    except Exception:
                        pass
                elif rw["id"] == 35:
                    try:
                        event_bus.bus.emit(event_bus.Events.ACTIVITY_POINT_GAIN, ctx, uid, pt_id=3, amount=rw["num"], item_id=35)
                    except Exception:
                        pass
                elif rw["id"] == 23:
                    try:
                        event_bus.bus.emit(event_bus.Events.ACTIVITY_POINT_GAIN, ctx, uid, pt_id=2, amount=rw["num"], item_id=23)
                    except Exception:
                        pass
                elif rw["id"] == 14:
                    bp_rows = ctx.db.query("SELECT weekly_gain_exp FROM battlepass WHERE uid=?", (uid,))
                    if bp_rows:
                        cur_wk = int(bp_rows[0]["weekly_gain_exp"] or 0)
                        new_wk = min(15000, cur_wk + rw["num"])
                        ctx.db.execute("UPDATE battlepass SET weekly_gain_exp=? WHERE uid=?", (new_wk, uid))
        ctx.db.claim_task(uid, tid)
        return {"task_id": tid, "already": False, "rewards": final_rewards, "has_ticket": has_ticket}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_28011", {
            "result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log(f"cs_28010 -> 任务领奖 tid={result['task_id']} already={result['already']} 奖={result['rewards']}")
        # 原子时序前置：优先推送资产差分帧 sc_17023，再回显 sc_28011 业务帧，保证客户端在回调触发时资产已是最新状态
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class TaskOneKeySubmitOp(Operation):
    """任务一键领取：cs_28014 {type|id_list} → sc_28015 {result, reward_list}。

    领取范围：进度达成（progress >= need）且未领取（claimed_ts=0）的任务。
    请求带 id_list 时只领这些；否则领全部符合条件的。
    """

    cmd = 28014
    sc = 28015

    def apply(self, data, ctx):
        uid = self.uid
        ids = data.get("id_list") or data.get("ids") or []
        ttype = int(data.get("type") or 0)
        if isinstance(ids, (int, str)):
            ids = [ids]

        where_clauses = ["t.uid=?", "COALESCE(t.claimed_ts, 0) = 0", "t.progress >= COALESCE(c.need, 1)"]
        params = [uid]

        if ids:
            ph = ",".join("?" * len(ids))
            where_clauses.append(f"t.task_id IN ({ph})")
            params.extend([int(x) for x in ids])
        elif ttype > 0:
            where_clauses.append("c.task_type = ?")
            params.append(ttype)

        sql = f"""
            SELECT t.task_id, t.progress, COALESCE(c.need, 1) as need, COALESCE(c.task_type, 0) as task_type
            FROM task t
            LEFT JOIN task_cfg c ON t.task_id = c.task_id
            WHERE {" AND ".join(where_clauses)}
        """
        tasks = ctx.db.query(sql, params)
        claimed_task_types = {int(t["task_type"] or 0) for t in tasks}

        # 记录领取任务前的旧活跃度，用于严格对齐客户端 PostSubmitTaskList 仅结算旧达成宝箱的时序
        old_pt_map = {}
        for pid in (1, 3):
            r = ctx.db.query("SELECT active_point FROM activity_pt WHERE uid=? AND activity_pt_id=?", (uid, pid))
            old_pt_map[pid] = int(r[0]["active_point"] or 0) if r else 0

        merged = {}
        claimed = 0
        has_ticket = False
        for t in tasks:
            tid = t["task_id"]
            for rw in _task_rewards(ctx, tid):
                if rw["id"] == 61:
                    has_ticket = True
                    # 游园街贴票每周获取上限 100 增量截断
                    r_rows = ctx.db.query("SELECT weekly_point FROM idol_trainee_rank WHERE uid=?", (uid,))
                    cur_weekly = int(r_rows[0]["weekly_point"] or 0) if r_rows else 0
                    avail = max(0, 100 - cur_weekly)
                    gain = min(rw["num"], avail)
                    if gain > 0:
                        _item_add(ctx, uid, 61, gain)
                        new_weekly = cur_weekly + gain
                        now_ts = int(time.time())
                        ctx.db.execute(
                            "INSERT INTO idol_trainee_rank (uid, weekly_point, update_ts) VALUES (?, ?, ?) "
                            "ON CONFLICT(uid) DO UPDATE SET weekly_point=?, update_ts=?",
                            (uid, new_weekly, now_ts, new_weekly, now_ts)
                        )
                        merged[61] = merged.get(61, 0) + gain
                else:
                    _item_add(ctx, uid, rw["id"], rw["num"])
                    merged[rw["id"]] = merged.get(rw["id"], 0) + rw["num"]
                    if not USE_NEW_INVENTORY_SERVICE:
                        if rw["id"] == 22:
                            ctx.db.add_activity_point(uid, 1, rw["num"])
                        elif rw["id"] == 35:
                            ctx.db.add_activity_point(uid, 3, rw["num"])
                        elif rw["id"] == 23:
                            ctx.db.add_activity_point(uid, 2, rw["num"])
                    if rw["id"] == 22:
                        try:
                            event_bus.bus.emit(event_bus.Events.ACTIVITY_POINT_GAIN, ctx, uid, pt_id=1, amount=rw["num"], item_id=22)
                        except Exception:
                            pass
                    elif rw["id"] == 35:
                        try:
                            event_bus.bus.emit(event_bus.Events.ACTIVITY_POINT_GAIN, ctx, uid, pt_id=3, amount=rw["num"], item_id=35)
                        except Exception:
                            pass
                    elif rw["id"] == 23:
                        try:
                            event_bus.bus.emit(event_bus.Events.ACTIVITY_POINT_GAIN, ctx, uid, pt_id=2, amount=rw["num"], item_id=23)
                        except Exception:
                            pass
                    elif rw["id"] == 14:
                        bp_rows = ctx.db.query("SELECT weekly_gain_exp FROM battlepass WHERE uid=?", (uid,))
                        if bp_rows:
                            cur_wk = int(bp_rows[0]["weekly_gain_exp"] or 0)
                            new_wk = min(15000, cur_wk + rw["num"])
                            ctx.db.execute("UPDATE battlepass SET weekly_gain_exp=? WHERE uid=?", (new_wk, uid))
            ctx.db.claim_task(uid, tid)
            claimed += 1

        # 联动一键领取达成的活跃度宝箱 (仅当领取的任务中包含日常 6 / 周常 5 时触发，严禁污染练舞房 901/902 等独立任务)
        target_pt_ids = []
        if 6 in claimed_task_types or (not ids and ttype == 6):
            target_pt_ids.append(1)
        if 5 in claimed_task_types or (not ids and ttype == 5):
            target_pt_ids.append(3)

        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity_pt_cfg.json")
        pt_cfg = {}
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    pt_cfg = json.load(f)
        except Exception:
            pass

        all_touched_heroes = []
        all_touched_scenes = []
        import activity_lottery
        for pt_id in target_pt_ids:
            pt_rows = ctx.db.query("SELECT active_point, get_id_list FROM activity_pt WHERE uid=? AND activity_pt_id=?", (uid, pt_id))
            if not pt_rows:
                continue
            cur_pt = int(pt_rows[0]["active_point"] or 0)
            try:
                got = json.loads(pt_rows[0]["get_id_list"] or "[]")
            except Exception:
                got = []

            box_targets = pt_cfg.get(str(pt_id), {}).get("target", [])
            box_rewards = pt_cfg.get(str(pt_id), {}).get("rewards", {})
            got_changed = False
            # 携带任务ID一键领任务时，严格对齐客户端仅结算任务提交前已满足条件的宝箱；
            # 本次新增活跃度达成的宝箱由客户端点亮后由玩家点击领取(cs_28016)或后续空ids一键领宝箱触发
            eval_pt = old_pt_map.get(pt_id, 0) if ids else cur_pt
            for target_need in box_targets:
                if eval_pt >= int(target_need) and int(target_need) not in got:
                    rws = box_rewards.get(str(target_need), [])
                    for rw in rws:
                        _item_add(ctx, uid, rw["id"], rw["num"])
                        # 移转之辉（ID 1）已由客户端本地 PostSubmitTaskList 逐箱自动回显（如周任务 5 个 200，日任务 5/5/5/10/15）
                        # 此处静默真实入库，sc_28015 回包不再冗余下发，彻底避免 1000 与 5个200 产生视觉冲突
                        if rw["id"] != 1:
                            merged[rw["id"]] = merged.get(rw["id"], 0) + rw["num"]

                    # 活跃度宝箱额外神装/大场景抽奖彩蛋
                    lottery_res, th, tsc = activity_lottery.roll_activity_box(ctx.db, uid, pt_id, int(target_need))
                    if lottery_res:
                        lid = lottery_res["id"]
                        lnum = lottery_res["num"]
                        merged[lid] = merged.get(lid, 0) + lnum
                        all_touched_heroes.extend(th)
                        all_touched_scenes.extend(tsc)

                    got.append(int(target_need))
                    got_changed = True

            if got_changed:
                ctx.db.execute("UPDATE activity_pt SET get_id_list=? WHERE uid=? AND activity_pt_id=?", (json.dumps(got), uid, pt_id))

        return {"claimed": claimed,
                "rewards": [{"id": i, "num": n} for i, n in sorted(merged.items()) if n > 0],
                "touched_heroes": list(set(all_touched_heroes)),
                "touched_scenes": list(set(all_touched_scenes)),
                "has_ticket": has_ticket,
                "target_pt_ids": target_pt_ids}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_28015", {
            "result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log(f"cs_28014 -> 一键领取 {result['claimed']} 个任务，合并奖励 {len(result['rewards'])} 项")

        frames = []
        # 1. 资产与数据原子差量帧前置（sc_17023 贴票/货币等，保证客户端在 28015 回调前已完成资产就绪）
        frames.extend(_refresh_frames(ctx, self.uid))

        # 2. 任务领奖响应严禁下发 sc_28019 全量活跃度帧：
        # 客户端在收到 sc_28015 后会在 OnSubmitTaskList 中读取 reward_list 的活跃度货币 (22/35) 本地调用 AddTaskPoint 增量累加；
        # 若在回包中夹带 sc_28019，客户端会先重置为服务端新值，随后再累加本次奖励，导致活跃度 Double-Add 跳变并点亮未达成宝箱。

        # 3. 差分同步帧：若开出了新换装或新场景，顺带下发 sc_14007 和 sc_32009
        for hid in result.get("touched_heroes", []):
            try:
                import hero_codec as _hc
                hf = _hc.build_single_hero_frame(ctx.db, self.uid, hid)
                if hf:
                    frames.append(DownFrame(14007, hf))
            except Exception:
                pass

        if result.get("touched_scenes"):
            try:
                import generator as _gen
                p32009 = _gen.gen_payload(32009, uid=self.uid, db=ctx.db)
                if p32009:
                    frames.append(DownFrame(32009, p32009))
            except Exception:
                pass

        # 4. 业务回包 sc_28015 作为终结帧下发（触发客户端 SendWithLoadingNew 回调并关闭 Loading 圈）
        frames.append(DownFrame(self.sc, payload))
        return frames


# ---------------- 56002 红点上报 ----------------

@operation
class RedpointReportOp(Operation):
    """红点上报：cs_56002 {red_dot / red_point_list} → 客户端 Push 单向通知，无需回包。"""

    cmd = 56002
    sc = 56002

    def apply(self, data, ctx):
        rd_id = data.get("red_dot") or data.get("id")
        if rd_id is not None:
            ctx.db.set_red_dot(self.uid, int(rd_id), 0)
        rds = data.get("red_point_list") or []
        for rd in rds:
            if isinstance(rd, dict):
                rid = int(rd.get("id") or 0)
                if rid:
                    ctx.db.set_red_dot(self.uid, rid, 0)
        return {"count": 1 if rd_id is not None else len(rds)}

    def respond(self, result, data, ctx):
        # 56002 是单向客户端 Push 通知，下发 sc_56002 会导致 Lua Protocol.Unpack 崩溃卡死
        return []


# ---------------- 16010 抽卡系统与卡池管理（统一委托给 DrawService 领域服务） ----------------

from draw_service import (
    DrawService,
    STANDARD_S_HEROES,
    LIMITED_S_HEROES,
    ALL_A_HEROES,
    SLEEPING_CHILDREN,
    POOL_UP_HERO_MAP
)


def get_pool_group(pool_id, detail_json=""):
    return DrawService.get_instance().get_pool_group(pool_id, detail_json)


def get_draw_state(ctx, uid, pool_group):
    svc = DrawService.get_instance(db=getattr(ctx, "db", None))
    return svc.get_draw_state(uid, pool_group)


def save_draw_state(ctx, uid, pool_group, state, pool_id=0):
    svc = DrawService.get_instance(db=getattr(ctx, "db", None))
    svc.save_draw_state(uid, pool_group, state, pool_id)


def _grant_hero(ctx, uid, hero_id):
    svc = DrawService.get_instance(db=getattr(ctx, "db", None))
    return svc._grant_hero(uid, hero_id)


def _insert_servant(ctx, uid, prefab_id):
    svc = DrawService.get_instance(db=getattr(ctx, "db", None))
    return svc._insert_servant(uid, prefab_id)


@operation
class DrawOp(Operation):
    """抽卡：cs_16010 {type, pool} → 17023×2 + 53003 + 28007 + 16011。统一委托给 DrawService 领域服务。"""

    cmd = 16010
    sc = 16011

    def validate(self, data, ctx):
        pool_id = int(data.get("pool") or 0)
        dtype = int(data.get("type", 0))
        svc = DrawService.get_instance(db=ctx.db)
        ok, msg = svc.validate_draw(self.uid, pool_id, dtype)
        if not ok:
            raise OperationError(2, msg)
        data["_pool_id"] = pool_id
        data["_type"] = dtype
        return data

    def apply(self, data, ctx):
        pool_id = data["_pool_id"]
        dtype = data["_type"]
        svc = DrawService.get_instance(db=ctx.db)
        return svc.draw(ctx, self.uid, pool_id, dtype)

    def respond(self, result, data, ctx):
        if isinstance(result, list):
            return result + _refresh_frames(ctx, self.uid)
        payload = ctx.codec_encode("sc_16011", {
            "result": 0,
            "item": result["item"],
            "first_ssr_draw_flag": result.get("first_ssr_draw_flag", False),
            "newbie_choose_draw_flag": result.get("newbie_choose_draw_flag", True),
            "ssr_draw_times": result.get("ssr_draw_times", 0)
        }) or b"\x08\x00"
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class DrawInfoOp(Operation):
    """抽卡信息/保底查询：cs_16012 {id} → sc_16013 {result, ssr_draw_times}。"""

    cmd = 16012
    sc = 16013

    def apply(self, data, ctx):
        pool_id = int(data.get("id") or data.get("pool_id") or 10001)
        svc = DrawService.get_instance(db=ctx.db)
        return svc.query_info(ctx, self.uid, pool_id)

    def respond(self, result, data, ctx):
        draw_records = result.get("draw_record_list") or []
        payload = ctx.codec_encode("sc_16013", {
            "result": 0,
            "ssr_draw_times": result["since_ssr"],
            "draw_record_list": draw_records
        }) or b"\x08\x00"
        ctx.log(f"cs_16012 -> 查询卡池 pool={result['pool_id']} (系列:{result['pool_group']}) 保底={result['since_ssr']} 历史记录={len(draw_records)}条")
        return [DownFrame(self.sc, payload)]


@operation
class DrawSetPollUpOp(Operation):
    """设置自选卡池/UP：cs_16016 {id, up} → sc_16017 {result}。"""

    cmd = 16016
    sc = 16017

    def apply(self, data, ctx):
        pool_id = int(data.get("pool_id") or data.get("poll_id") or data.get("id") or 10002)
        up_id = int(data.get("up_id") or data.get("up") or data.get("hero_id") or 0)
        svc = DrawService.get_instance(db=ctx.db)
        return svc.set_pool_up(self.uid, pool_id, up_id)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_16017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_16016 -> 设置卡池UP pool={result['pool_id']} (系列:{result['pool_group']}) up={result['up_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class DrawPoolDetailsOp(Operation):
    """卡池详情与展示角色列查询：cs_16018 {id} → sc_16019 {result, pool_details}。"""

    cmd = 16018
    sc = 16019

    def apply(self, data, ctx):
        pool_id = int(data.get("id") or data.get("pool_id") or 10001)
        svc = DrawService.get_instance(db=ctx.db)
        return svc.get_pool_details(pool_id, self.uid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_16019", result) or b"\x08\x00"
        ctx.log(f"cs_16018 -> 查询卡池详情 pool_id={data.get('id')}")
        return [DownFrame(self.sc, payload)]


# ---------------- 46030 钥从唤名（沉睡之子转换专属五星钥从） ----------------

@operation
class ServantMergeOp(Operation):
    """钥从唤名（转换）：cs_46030 {servant_id, cost_uid_list} → sc_46031 {result, servant_uid}。
    统一委托给 ServantService 领域服务处理。"""

    cmd = 46030
    sc = 46031

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from servant_service import ServantService
        svc = ServantService.get_instance(db=ctx.db)
        sid = int(data.get("servant_id") or 0)
        cost_uids = [int(u) for u in (data.get("cost_uid_list") or [])]
        return svc.awake_servant(ctx, self.uid, sid, cost_uids)

    def respond(self, result, data, ctx):
        ctx.log(f"cs_46030 -> 钥从唤名转换 沉睡之子 → 专属五星钥从 {result['servant_id']} (UID {result['new_servant_uid']})")
        return result.get("frames", [])


@operation
class ServantReplaceOp(Operation):
    """钥从佩戴/更换：cs_46020 {hero_id, servant_id} → sc_46021 {result}。
    统一委托给 HeroService 领域服务处理。"""

    cmd = 46020
    sc = 46021

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        suid = int(data.get("servant_id") or 0)
        return svc.replace_servant(ctx, self.uid, hid, suid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_46021", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_46020 -> 钥从佩戴 hero={result['hero_id']} servant_uid={result['servant_id']}")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        return [DownFrame(self.sc, payload)] + svc.build_hero_refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class ServantLockOp(Operation):
    """钥从锁定：cs_46014 {uid, is_lock} → sc_46015 {result}。
    统一委托给 ServantService 领域服务处理。"""

    cmd = 46014
    sc = 46015

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from servant_service import ServantService
        svc = ServantService.get_instance(db=ctx.db)
        suid = int(data.get("uid") or 0)
        is_lock = 1 if data.get("is_lock") else 0
        return svc.lock_servant(ctx, self.uid, suid, is_lock)

    def respond(self, result, data, ctx):
        ctx.log(f"cs_46014 -> 钥从锁定 servant_uid={result['uid']} lock={result['is_locked']}")
        return result.get("frames", [])


@operation
class HeroWeaponStrOp(Operation):
    """权钥强化/升级：cs_46016 {hero_id, material_list, servant_list} → sc_46017 {result: 0}。
    统一委托给 HeroService 领域服务处理（经验计算、封顶与溢出返还）。"""

    cmd = 46016
    sc = 46017

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        mlist = data.get("material_list") or []
        slist = data.get("servant_list") or []
        return svc.enhance_weapon(ctx, self.uid, hid, material_list=mlist, servant_list=slist)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_46017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_46016 -> 权钥升级 hero={result['hero_id']} 突破阶段={result['cur_break']} +{result['exp_added']}exp -> exp={result['new_exp']}")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        diff_frames = svc.build_diff_frames(ctx, self.uid)
        hero_frames = svc.build_hero_14007_frame(ctx, self.uid, result.get("hero_id"))
        return diff_frames + [DownFrame(self.sc, payload)] + hero_frames


@operation
class HeroWeaponBreakOp(Operation):
    """权钥突破：cs_46018 {hero_id} → sc_46019 {result: 0}。
    统一委托给 HeroService 领域服务处理。"""

    cmd = 46018
    sc = 46019

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        return svc.break_weapon(ctx, self.uid, hid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_46019", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_46018 -> 权钥突破 hero={result['hero_id']} 阶数={result['new_break']}")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        diff_frames = svc.build_diff_frames(ctx, self.uid)
        hero_frames = svc.build_hero_14007_frame(ctx, self.uid, result.get("hero_id"))
        return diff_frames + [DownFrame(self.sc, payload)] + hero_frames


@operation
class HeroWeaponQuickOp(Operation):
    """权钥一键升级/突破：cs_46034 {hero_id, material_list, servant_list, target_level, breakthrough_times} → sc_46035 {result: 0, mat_list}。
    统一委托给 HeroService 领域服务处理。"""

    cmd = 46034
    sc = 46035

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        mlist = data.get("material_list") or []
        slist = data.get("servant_list") or []
        tlvl = int(data.get("target_level") or 0)
        btimes = int(data.get("breakthrough_times") or 0)
        return svc.quick_upgrade_weapon(ctx, self.uid, hid, material_list=mlist, servant_list=slist, target_level=tlvl, breakthrough_times=btimes)

    def respond(self, result, data, ctx):
        refund = result.get("refund_items") or []
        payload = ctx.codec_encode("sc_46035", {"result": 0, "mat_list": refund}) or b"\x08\x00"
        tlv = data.get("target_level", 0)
        ctx.log(f"cs_46034 -> 权钥一键升级 hero={result['hero_id']} target_lv={tlv} break={result.get('new_break', 0)}")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        diff_frames = svc.build_diff_frames(ctx, self.uid)
        hero_frames = svc.build_hero_14007_frame(ctx, self.uid, result.get("hero_id"))
        return diff_frames + [DownFrame(self.sc, payload)] + hero_frames


_SERVANT_GOLD_COSTS = {
    3: [1000, 2000, 3000, 4000],
    4: [1000, 2000, 3000, 4000],
    5: [2000, 3000, 4000, 5000]
}


@operation
class ServantPromoteOp(Operation):
    """钥从超越/精炼：cs_46012 {uid, cost_uid, refined_type} → sc_46013 {result}。
    统一委托给 ServantService 领域服务处理（绝杀 Bug 1 延迟扣款与 Bug 2 狗粮残留）。"""

    cmd = 46012
    sc = 46013

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from servant_service import ServantService
        svc = ServantService.get_instance(db=ctx.db)
        suid = int(data.get("uid") or 0)
        cost_uid = int(data.get("cost_uid") or 0)
        raw_rt = data.get("refined_type")
        # 客户端消耗同名钥从时传入 refined_type=0，Python 中 0 or 1 会被篡改为 1，必须显式区分 None
        refined_type = int(raw_rt) if raw_rt is not None else (0 if cost_uid > 0 else 1)
        return svc.promote_servant(ctx, self.uid, suid, cost_uid=cost_uid, refined_type=refined_type)

    def respond(self, result, data, ctx):
        cost_uid = int(data.get("cost_uid") or 0)
        is_dup = (cost_uid > 0) or (result.get("refined_type") in (0, 2))
        cost_desc = f"同名钥从 UID {cost_uid}" if is_dup else f"神识凝晶×{result['mat_cost']}"
        ctx.log(f"cs_46012 -> 钥从超越 servant_uid={result['uid']} 阶数={result['new_stage']} (消耗:{cost_desc}, 金币:{result['gold_cost']})")
        return result.get("frames", [])


@operation
class ServantDecomposeOp(Operation):
    """钥从分解出售：cs_46032 {servant_list} → sc_46033 {result, return_list}。
    统一委托给 ServantService 领域服务处理。"""

    cmd = 46032
    sc = 46033

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from servant_service import ServantService
        svc = ServantService.get_instance(db=ctx.db)
        slist = data.get("servant_list") or []
        return svc.decompose_servants(ctx, self.uid, slist)

    def respond(self, result, data, ctx):
        ctx.log(f"cs_46032 -> 钥从分解 count={result['decomposed_count']} 获得={result['items']}")
        return result.get("frames", [])


# ---------------- 20012 商店购买 ----------------

# [Old logic commented out by Gemini 3.7-flash]
# def _deduct_flower(ctx, uid, num):
#     """扣除移转之花：优先扣除 31 (非iOS充值) 与 32 (免费)，并同步更新 5 (总和)。"""
#     _, have31 = _item_balance(ctx, uid, 31)
#     _, have32 = _item_balance(ctx, uid, 32)
#     left = num
#     out = {}
#     if have31 > 0:
#         pay31 = min(have31, left)
#         out[31] = pay31
#         left -= pay31
#     if left > 0 and have32 > 0:
#         pay32 = min(have32, left)
#         out[32] = pay32
#         left -= pay32
#     if left > 0:
#         out[5] = left
#     return out
# 
# def _cost_breakdown(ctx, uid, cost_id, total, cost_type=None):
#     cid = int(cost_id or 0)
#     if int(cost_type or 0) == 2 or cid == 5:
#         return _deduct_flower(ctx, uid, int(total))
#     return {cid: int(total)}

def _cost_breakdown(ctx, uid, cost_id, total, cost_type=None):
    """[Fix by Gemini 3.7-flash] 精确按客户端标准分流扣费：
    1. cost_type == 2: 移转之辉 (1) 档。优先扣除免费移转之辉 (1)；若不足，差额由移转之花 (32 免费 / 31 充值) 补足。
    2. cid in (30, 31, 32): 移转之花专属档。优先扣 32，次扣 31。
    3. 其他常规货币/材料 (金币 2、抽卡券 5、材料等): 严格扣除对应 cost_id，禁止误扣抽卡券。
    """
    cid = int(cost_id or 0)
    total = int(total or 0)
    if total <= 0:
        return {}

    if int(cost_type or 0) == 2:
        _, have1 = _item_balance(ctx, uid, 1)
        out = {}
        if have1 >= total:
            out[1] = total
        else:
            if have1 > 0:
                out[1] = have1
            rem = total - max(0, have1)
            _, have32 = _item_balance(ctx, uid, 32)
            _, have31 = _item_balance(ctx, uid, 31)
            if have32 + have31 < rem:
                raise OperationError(12, f"移转之辉与移转之花不足 (缺 {rem - have32 - have31})")
            pay32 = min(have32, rem)
            if pay32 > 0:
                out[32] = pay32
                rem -= pay32
            if rem > 0:
                out[31] = rem
        return out

    if cid in (30, 31, 32):
        out = {}
        rem = total
        _, have32 = _item_balance(ctx, uid, 32)
        _, have31 = _item_balance(ctx, uid, 31)
        if have32 + have31 < rem:
            raise OperationError(12, f"移转之花不足 (缺 {rem - have32 - have31})")
        pay32 = min(have32, rem)
        if pay32 > 0:
            out[32] = pay32
            rem -= pay32
        if rem > 0:
            out[31] = rem
        return out

    return {cid: total}


_RECHARGE_PACKS = None

def _get_recharge_packs():
    global _RECHARGE_PACKS
    if _RECHARGE_PACKS is None:
        _RECHARGE_PACKS = {}
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recharge_packs.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    _RECHARGE_PACKS = json.load(f)
            except Exception:
                pass
    return _RECHARGE_PACKS


_SKIN_HERO_MAP = None
_TICKET_SKIN_MAP = None

_ITEM_SKIN_MAP = {
    30124: 109503,  # 托特誓约换装「白夜之约」
    30098: 104903,  # 丝卡蒂「夏日阳光」
    30099: 104402,  # 提尔「极地探索」
    34096: 106604,  # 金乌换装
    34140: 104204,  # 操偶师换装
    34143: 103906,  # 换装
}

def _get_skin_hero_map():
    global _SKIN_HERO_MAP
    if _SKIN_HERO_MAP is None:
        _SKIN_HERO_MAP = {}
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_hero_map.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    _SKIN_HERO_MAP = {int(k): int(v) for k, v in json.load(f).items()}
            except Exception:
                pass
    return _SKIN_HERO_MAP


def _get_ticket_skin_map():
    global _TICKET_SKIN_MAP
    if _TICKET_SKIN_MAP is None:
        _TICKET_SKIN_MAP = {}
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket_skin_map.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    _TICKET_SKIN_MAP = {int(k): int(v) for k, v in json.load(f).items()}
            except Exception:
                pass
    return _TICKET_SKIN_MAP


def _resolve_skin_id(rew_id, skin_map=None):
    skin_map = skin_map or _get_skin_hero_map()
    if rew_id in skin_map:
        return rew_id
    tmap = _get_ticket_skin_map()
    if rew_id in tmap:
        return tmap[rew_id]
    return _ITEM_SKIN_MAP.get(rew_id)


@operation
class ShopBuyOp(Operation):
    """商店购买：cs_20012 {buy_goods_list, shop_id} → sc_20013 {result, give_items, cost_items} + 差分帧。
    统一委托给 ShopService 领域服务处理。"""

    cmd = 20012
    sc = 20013

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from shop_service import ShopService
        svc = ShopService.get_instance(db=ctx.db)
        sid = int(data.get("shop_id") or 0)
        buy_list = data.get("buy_goods_list") or []
        buy_source = int(data.get("buy_source") or 0)
        frames = svc.buy_goods(ctx, self.uid, sid, buy_list, buy_source=buy_source)
        return {"frames": frames}

    def respond(self, result, data, ctx):
        return result.get("frames") or []


@operation
class ShopRefreshSingleOp(Operation):
    """刷新单个商店：
    cs_20014 {shop_id} → sc_20015 {result: 0} + sc_20005 {changed_shop_info}。
    统一委托给 ShopService 领域服务处理（含每日采购20次上限与阶梯扣费）。"""

    cmd = 20014
    sc = 20015

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from shop_service import ShopService
        svc = ShopService.get_instance(db=ctx.db)
        sid = int(data.get("shop_id") or 0)
        frames = svc.refresh_single_shop(ctx, self.uid, sid)
        return {"frames": frames}

    def respond(self, result, data, ctx):
        return result.get("frames") or []


@operation
class ShopRefreshAllOp(Operation):
    """刷新全商店时间与状态：
    cs_20016 {} → sc_20017 {result: 0, timestamp}。
    统一委托给 ShopService 领域服务处理。"""

    cmd = 20016
    sc = 20017

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from shop_service import ShopService
        svc = ShopService.get_instance(db=ctx.db)
        frames = svc.refresh_all_shops(ctx, self.uid)
        return {"frames": frames}

    def respond(self, result, data, ctx):
        return result.get("frames") or []


@operation
class ShopRemoveRedPointOp(Operation):
    """[Fix by Gemini 3.7-flash] 移除商店新商品红点：
    cs_20020 {shop_id} → sc_20021 {result: 0}。"""

    cmd = 20020
    sc = 20021

    def apply(self, data, ctx):
        ctx.uid = self.uid
        shop_id = int(data.get("shop_id") or 0)
        return {"shop_id": shop_id}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_20021", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_20020 -> 移除商店红点 shop_id={result['shop_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class ShopResolveFragmentOp(Operation):
    """[Fix by Gemini] 满阶角色碎片分解/一键转化：
    cs_14032 {} → sc_14033 {result: 0} + sc_14019 (更新碎片) + sc_17009/sc_17023 (更新情报徽章) + sc_15009。
    """

    cmd = 14032
    sc = 14033

    _STAR_ORDER = [100, 101, 102, 103, 104, 200, 201, 202, 203, 204, 300, 301, 302, 303, 304, 400, 401, 402, 403, 404, 500, 501, 502, 503, 504]
    _STAR_UP_MAP = {
        100: 2, 101: 2, 102: 2, 103: 2, 104: 4,
        200: 3, 201: 3, 202: 3, 203: 3, 204: 6,
        300: 5, 301: 5, 302: 5, 303: 5, 304: 10,
        400: 10, 401: 10, 402: 10, 403: 10, 404: 50,
        500: 20, 501: 20, 502: 20, 503: 20, 504: 100
    }

    def apply(self, data, ctx):
        heroes = ctx.db.query("SELECT id, star, unlock FROM hero WHERE uid=?", (self.uid,))
        pieces = {r["hero_id"]: r["num"] for r in ctx.db.query("SELECT hero_id, num FROM hero_piece WHERE uid=?", (self.uid,))}
        
        hero_rares = {}
        try:
            ic_rows = ctx.db.query("SELECT id, rare FROM item_catalog WHERE type=3")
            for r in ic_rows:
                hid = r["id"] % 10000 if r["id"] > 10000 else r["id"]
                hero_rares[hid] = r.get("rare") or 3
        except Exception:
            pass

        total_b_a = 0  # 40901 中级情报徽章 (Rare 3/4 碎片转化, x5)
        total_s = 0    # 40902 高级情报徽章 (Rare 5 碎片转化, x5)

        for h in heroes:
            hid = h["id"]
            unlocked = h.get("unlock", 0) == 1 or h.get("star", 0) > 0
            if not unlocked:
                continue
            
            star = h.get("star", 100)
            needed = 0
            if star in self._STAR_ORDER:
                idx = self._STAR_ORDER.index(star)
                for s in self._STAR_ORDER[idx:]:
                    needed += self._STAR_UP_MAP.get(s, 0)
            elif star >= 504 or star >= 600:
                needed = 0
            
            cur_piece = pieces.get(hid, 0)
            if cur_piece > needed:
                surplus = cur_piece - needed
                ctx.db.execute("UPDATE hero_piece SET num = num - ? WHERE uid=? AND hero_id=?", (surplus, self.uid, hid))
                piece_iid = 10000 + hid
                if hasattr(ctx, "touched_items"):
                    ctx.touched_items.add(piece_iid)
                
                rare = hero_rares.get(hid, 3)
                if rare == 5:
                    total_s += surplus * 5
                else:
                    total_b_a += surplus * 5

        if total_b_a > 0:
            _item_add(ctx, self.uid, 40901, total_b_a)
        if total_s > 0:
            _item_add(ctx, self.uid, 40902, total_s)

        return {"result": 0, "total_b_a": total_b_a, "total_s": total_s}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14033", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14032 -> 满阶角色碎片分解完成: 产出中级徽章={result.get('total_b_a',0)}, 高级徽章={result.get('total_s',0)}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


_STAMINA_ITEM_MAP = {
    20001: 30, 20002: 60, 20003: 120, 20004: 30, 20005: 60,
    20006: 120, 20007: 120, 20008: 60, 20009: 120, 20010: 120,
    20011: 120, 20012: 120, 20013: 30, 20014: 60, 20015: 120,
    20016: 30, 20017: 60, 20018: 120, 20019: 30, 20020: 60,
    20021: 120, 20022: 120, 20023: 120, 20024: 120, 20025: 120,
    20026: 120, 20027: 120, 20028: 120
}

_GOLD_ITEM_MAP = {
    22001: 1000, 22002: 2000, 22003: 5000, 22004: 1000000
}


@operation
class UseItemOp(Operation):
    """背包道具/礼包/自选箱/材料/体力药/表情使用：
    cs_17012 {use_item_list} → sc_17013 {result, drop_list} + sc_14007 (英雄变动) + sc_32009 (场景变动) + _refresh_frames (sc_17023 道具差量/sc_17025 体力时间戳)。
    统一委托给 InventoryService 库管理模块内部自治，杜绝逻辑割裂与幽灵数据。
    """

    cmd = 17012
    sc = 17013

    def apply(self, data, ctx):
        use_list = data.get("use_item_list") or []
        import inventory_service
        res = inventory_service.use_items(ctx, self.uid, use_list)
        return res

    def respond(self, result, data, ctx):
        res_code = result.get("result", 0)
        drop_list = result.get("drop_list") or []
        payload = ctx.codec_encode("sc_17013", {
            "result": res_code,
            "drop_list": drop_list
        }) or b"\x08\x00"

        extra_frames = []
        # 换装/英雄变更帧下发 (sc_14007)
        for hid in result.get("touched_heroes") or []:
            try:
                import hero_codec as _hc
                hf = _hc.build_single_hero_frame(ctx.db, self.uid, hid)
                if hf:
                    extra_frames.append(DownFrame(14007, hf))
            except Exception:
                pass

        # 场景变更帧下发 (sc_32009)
        if result.get("touched_scene"):
            try:
                import generator as _gen
                p = _gen.gen_payload(32009, uid=self.uid, db=ctx.db)
                if p:
                    extra_frames.append(DownFrame(32009, p))
            except Exception:
                pass

        ctx.log(f"cs_17012 -> 道具使用完成: result={res_code}, drops={len(drop_list)}")
        # 核心标准：原子帧 (_refresh_frames + extra_frames) 必须先于业务主响应帧 (sc_17013) 下发，确保客户端回调时本地内存已完全对齐
        return _refresh_frames(ctx, self.uid) + extra_frames + [DownFrame(self.sc, payload)]



# ---------------- 货币与兑换系统 (15020/15012/15016/15022) ----------------

@operation
class CurrencyBuyDiamondOp(Operation):
    """[Fix by Gemini 3.7-flash] 移转之花兑换移转之辉：cs_15020 {num} → sc_15021 {result, reward_list} + sc_15009。"""

    cmd = 15020
    sc = 15021

    def validate(self, data, ctx):
        num = int(data.get("num") or 0)
        if num <= 0:
            raise OperationError(2, "兑换数量必须大于0")
        _, cur31 = _item_balance(ctx, self.uid, 31)
        _, cur32 = _item_balance(ctx, self.uid, 32)
        have_flower = cur31 + cur32
        if have_flower < num:
            raise OperationError(402, f"移转之花不足（拥有 {have_flower}，需要 {num}）")
        return data

    def apply(self, data, ctx):
        num = int(data.get("num") or 0)
        flow_cost = _cost_breakdown(ctx, self.uid, 32, num)
        for cid, cnt in flow_cost.items():
            _item_deduct(ctx, self.uid, cid, cnt)
        _item_add(ctx, self.uid, 1, num)
        return {"num": num}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_15021", {
            "result": 0,
            "reward_list": [{"id": 1, "num": result["num"]}]
        }) or b"\x08\x00"
        ctx.log(f"cs_15020 -> 移转之花兑换移转之辉成功: {result['num']} 朵")
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class BuyCoinOp(Operation):
    """[Fix by Gemini 3.7-flash] 购买金币：cs_15012 {id} → sc_15013 {result, reward_list} + sc_15009。"""

    cmd = 15012
    sc = 15013

    def apply(self, data, ctx):
        _item_deduct(ctx, self.uid, 1, 20)
        _item_add(ctx, self.uid, 2, 100000)
        return {"gold": 100000}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_15013", {
            "result": 0,
            "reward_list": [{"id": 2, "num": result["gold"]}]
        }) or b"\x08\x00"
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class BuyFatigueOp(Operation):
    """[Fix by Gemini 3.7-flash] 购买体力：cs_15016 {num} → sc_15017 {result, reward_list} + sc_15009。"""

    cmd = 15016
    sc = 15017

    def apply(self, data, ctx):
        num = int(data.get("num") or 1)
        fatigue_gain = num * 120
        cost_diamond = num * 50
        _item_deduct(ctx, self.uid, 1, cost_diamond)
        _item_add(ctx, self.uid, 4, fatigue_gain)
        try:
            event_bus.bus.emit(event_bus.Events.BUY_FATIGUE, ctx, self.uid, times=num)
        except Exception:
            pass
        return {"fatigue": fatigue_gain}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_15017", {
            "result": 0,
            "reward_list": [{"id": 4, "num": result["fatigue"]}]
        }) or b"\x08\x00"
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class ExchangeItemOp(Operation):
    """[Fix by Gemini 3.7-flash] 道具兑换：cs_15022 {exchange_id, num} → sc_15023 {result} + _refresh_frames。"""

    cmd = 15022
    sc = 15023

    def apply(self, data, ctx):
        return {"data": data}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_15023", {"result": 0}) or b"\x08\x00\x12\x00\x18\x00"
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


# ---------------- 68152 服装/场景卡池抽卡 ----------------

_SKIN_DRAW_POOLS_CACHE = None

def _get_skin_draw_pools():
    global _SKIN_DRAW_POOLS_CACHE
    if _SKIN_DRAW_POOLS_CACHE is None:
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_draw_pool_cfg.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    _SKIN_DRAW_POOLS_CACHE = json.load(f)
            except Exception:
                _SKIN_DRAW_POOLS_CACHE = {}
        else:
            _SKIN_DRAW_POOLS_CACHE = {}
    return _SKIN_DRAW_POOLS_CACHE


@operation
class SkinDrawOp(Operation):
    """[Full Implementation] 服装/场景卡池有限奖池不放回抽取：
    cs_68152 {activity_id, pool_id, drop_type} → sc_68153 {result, drop_list} + sc_68151 + sc_68155。
    - drop_type: 1 (单抽) 或 10 (十连抽)
    - 动态加权不放回抽样 (Weighted Sampling without Replacement)
    - 十连核心大奖保底
    - 奖励即时入库（皮肤 -> player_skin_unlocked，场景 -> user_scene，道具 -> item/material/currency）
    - 重复皮肤自动触发 sc_68155
    """

    cmd = 68152
    sc = 68153

    def apply(self, data, ctx):
        aid = int(data.get("activity_id") or 182211)
        pool_id = int(data.get("pool_id") or 1001)
        drop_type = int(data.get("drop_type") or 1)
        if drop_type not in (1, 10):
            drop_type = 1

        pools_cfg = _get_skin_draw_pools()
        pcfg = pools_cfg.get(str(pool_id)) or pools_cfg.get(pool_id)
        if not pcfg:
            raise OperationError(404, f"卡池 {pool_id} 不存在")

        # 1. 门票/货币扣减校验
        cost_info = pcfg.get("cost_ten_times") if drop_type == 10 else pcfg.get("cost_once")
        ticket_id = cost_info[0] if (cost_info and len(cost_info) >= 1) else 53030
        req_ticket_num = cost_info[1] if (cost_info and len(cost_info) >= 2) else drop_type

        # 检查门票余额
        t, have_ticket = _item_balance(ctx, self.uid, ticket_id)
        if have_ticket >= req_ticket_num:
            _item_deduct(ctx, self.uid, ticket_id, req_ticket_num)
        else:
            need_tickets = req_ticket_num - have_ticket
            need_currency = need_tickets * 120
            if have_ticket > 0:
                _item_deduct(ctx, self.uid, ticket_id, have_ticket)
            _, have_gem = _item_balance(ctx, self.uid, 1)
            if have_gem >= need_currency:
                _item_deduct(ctx, self.uid, 1, need_currency)
            else:
                _, have_flower = _item_balance(ctx, self.uid, 32)
                if have_flower >= need_currency:
                    _item_deduct(ctx, self.uid, 32, need_currency)
                else:
                    raise OperationError(3, f"门票与货币不足：缺少 {need_tickets} 张门票（需 {need_currency} 移转之辉/花）")

        # 2. 读取卡池当前库存
        pool_state = ctx.db.get_skin_draw_pool_state(self.uid, pool_id)
        drops_cfg = pcfg.get("drops", {})
        
        remain_total = sum(int(item["remain_num"]) for item in pool_state.values())
        if remain_total <= 0:
            raise OperationError(2, f"卡池 {pool_id} 奖品已全部抽空")

        actual_draws = min(drop_type, remain_total)
        chosen_drops = []
        unlocked_skins = []
        duplicate_skins = []
        skin_map = _get_skin_hero_map()
        now = int(time.time())

        # 执行抽取
        for draw_i in range(actual_draws):
            avail_items = [item for item in pool_state.values() if int(item["remain_num"]) > 0]
            if not avail_items:
                break
            
            total_drawn = sum(int(item["drawn_num"]) for item in pool_state.values())
            is_guaranteed = ((total_drawn + 1) % 10 == 0)
            core_avail = [item for item in avail_items if drops_cfg.get(str(item["drop_id"]), {}).get("is_core") == 1]

            if is_guaranteed and core_avail:
                candidates = core_avail
            else:
                candidates = avail_items

            weights = []
            for item in candidates:
                did = str(item["drop_id"])
                w = int(drops_cfg.get(did, {}).get("weight") or 1)
                r_num = int(item["remain_num"])
                weights.append(w * r_num)

            tot_w = sum(weights)
            if tot_w <= 0:
                selected_item = candidates[0]
            else:
                rand_val = _random.uniform(0, tot_w)
                cum = 0
                selected_item = candidates[-1]
                for idx, w in enumerate(weights):
                    cum += w
                    if rand_val <= cum:
                        selected_item = candidates[idx]
                        break

            chosen_did = int(selected_item["drop_id"])
            chosen_drops.append(chosen_did)

            pool_state[chosen_did]["remain_num"] -= 1
            pool_state[chosen_did]["drawn_num"] += 1
            ctx.db.update_skin_draw_pool_item(
                self.uid, pool_id, chosen_did,
                pool_state[chosen_did]["remain_num"],
                pool_state[chosen_did]["drawn_num"])

            drop_meta = drops_cfg.get(str(chosen_did), {})
            rewards = drop_meta.get("reward", [])
            for rew in rewards:
                rew_id = int(rew[0])
                rew_num = int(rew[1])

                target_sk = _resolve_skin_id(rew_id, skin_map)
                if target_sk or (10000 <= rew_id <= 19999 and rew_num == 1):
                    actual_sk = target_sk or rew_id
                    owned = ctx.db.query("SELECT skin_id FROM player_skin_unlocked WHERE uid=? AND skin_id=?", (self.uid, actual_sk))
                    if not owned:
                        ctx.db.upsert("player_skin_unlocked", self.uid,
                                      {"skin_id": actual_sk, "unlock_ts": now, "update_ts": now},
                                      keys=("uid", "skin_id"))
                        unlocked_skins.append(actual_sk)
                    else:
                        duplicate_skins.append(actual_sk)
                elif (6000 <= rew_id <= 6999) or (rew_id in [6001, 6002, 6003, 6004, 6005, 6006, 6007, 6008, 6009, 6010, 6011, 6012]):
                    ctx.db.upsert("user_scene", self.uid,
                                  {"scene_id": rew_id, "lasted_time": 0, "obtain_time": now, "update_ts": now},
                                  keys=("uid", "scene_id"))
                else:
                    if 90000 <= rew_id < 92000:
                        ctx.db.upsert("chat_sticker", self.uid, {"emoji_id": rew_id}, keys=("uid", "emoji_id"))
                    _item_add(ctx, self.uid, rew_id, rew_num)

        return {
            "activity_id": aid,
            "pool_id": pool_id,
            "drop_list": chosen_drops,
            "pool_state": pool_state,
            "unlocked_skins": unlocked_skins,
            "duplicate_skins": duplicate_skins
        }

    def respond(self, result, data, ctx):
        frames = []
        payload = ctx.codec_encode("sc_68153", {
            "result": 0, "drop_list": result["drop_list"]}) or b"\x08\x00"
        frames.append(DownFrame(self.sc, payload))

        state_list = [{"drop_id": did, "num": s["remain_num"]} for did, s in result["pool_state"].items()]
        p68151 = ctx.codec_encode("sc_68151", {
            "activity_id": result["activity_id"],
            "info": state_list
        })
        if p68151:
            frames.append(DownFrame(68151, p68151))

        if result.get("duplicate_skins"):
            p68155 = ctx.codec_encode("sc_68155", {
                "skin_id": result["duplicate_skins"]
            })
            if p68155:
                frames.append(DownFrame(68155, p68155))

        skin_map = _get_skin_hero_map()
        for sk_id in result.get("unlocked_skins") or []:
            hid = skin_map.get(sk_id)
            if hid:
                try:
                    import hero_codec as _hc
                    hf = _hc.build_single_hero_frame(ctx.db, self.uid, hid)
                    if hf:
                        frames.append(DownFrame(14007, hf))
                except Exception:
                    pass

        frames.extend(_refresh_frames(ctx, self.uid))
        ctx.log(f"cs_68152 -> 换装卡池抽取 aid={result['activity_id']} pool={result['pool_id']} drops={result['drop_list']}")
        return frames


@operation
class SkinStoryFinishOp(Operation):
    """换装活动剧情完成：cs_68162 {activity_id, story_id} → sc_68163 {result: 0} + sc_68161。"""

    cmd = 68162
    sc = 68163

    def apply(self, data, ctx):
        aid = int(data.get("activity_id") or 0)
        story_id = int(data.get("story_id") or 0)
        if aid and story_id:
            ctx.db.finish_skin_story(self.uid, aid, story_id)
        return {"activity_id": aid, "story_id": story_id}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_68163", {"result": 0}) or b"\x10\x00"
        frames = [DownFrame(self.sc, payload)]
        aid = result["activity_id"]
        if aid:
            finished = ctx.db.get_skin_story_state(self.uid, aid)
            p68161 = ctx.codec_encode("sc_68161", {
                "activity_id": aid,
                "story_stage": 0,
                "finished_story": finished
            })
            if p68161:
                frames.append(DownFrame(68161, p68161))
        ctx.log(f"cs_68162 -> 换装剧情完成 aid={aid} story_id={result['story_id']}")
        return frames


@operation
class SkinFinishPopOp(Operation):
    """换装重复弹窗确认/剧情道具领奖：cs_68156 {skin_id} → sc_68157 {result: 0}。"""

    cmd = 68156
    sc = 68157

    def apply(self, data, ctx):
        skin_id = int(data.get("skin_id") or 0)
        _item_add(ctx, self.uid, 53028, 1000)
        return {"skin_id": skin_id}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_68157", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_68156 -> 换装重复确认 skin_id={result['skin_id']} (发放 53028 记忆结晶*1000)")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class OathDrawOp(Operation):
    """誓约换装/场景卡池抽取：cs_68186 {activity_id, pool_id, card_index} → sc_68187 {result: 0, drop_list: [{card_index, drop_id}]} + sc_68185。"""

    cmd = 68186
    sc = 68187

    def validate(self, data, ctx):
        aid = int(data.get("activity_id") or 0)
        pool_id = int(data.get("pool_id") or 0)
        if not pool_id:
            raise OperationError(406, "未指定誓约卡池 ID")
        
        pools = _get_skin_draw_pools()
        pool_cfg = pools.get(str(pool_id))
        if not pool_cfg:
            raise OperationError(406, f"卡池 {pool_id} 配置不存在")
        
        cost_info = pool_cfg.get("cost_once") or [53219, 1]
        cost_item_id, cost_num = cost_info[0], cost_info[1]
        
        t, have_ticket = _item_balance(ctx, self.uid, cost_item_id)
        if have_ticket >= cost_num:
            _item_deduct(ctx, self.uid, cost_item_id, cost_num)
        else:
            need_tickets = cost_num - have_ticket
            need_currency = need_tickets * 120
            if have_ticket > 0:
                _item_deduct(ctx, self.uid, cost_item_id, have_ticket)
            _, have_gem = _item_balance(ctx, self.uid, 1)
            if have_gem >= need_currency:
                _item_deduct(ctx, self.uid, 1, need_currency)
            else:
                _, have_flower = _item_balance(ctx, self.uid, 32)
                if have_flower >= need_currency:
                    _item_deduct(ctx, self.uid, 32, need_currency)
                else:
                    raise OperationError(3, f"门票与货币不足：缺少 {need_tickets} 张门票（需 {need_currency} 移转之辉/花）")
        
        return data

    def apply(self, data, ctx):
        import random
        aid = int(data.get("activity_id") or 0)
        pool_id = int(data.get("pool_id") or 0)
        card_index = int(data.get("card_index") or 0)
        if card_index <= 0:
            card_index = 1
        
        pools = _get_skin_draw_pools()
        pool_cfg = pools.get(str(pool_id))
        drops_cfg = pool_cfg.get("drops") or {}
        
        pool_state = ctx.db.get_skin_draw_pool_state(self.uid, pool_id)
        avail_drops = [d for d in pool_state.values() if d["remain_num"] > 0]
        if not avail_drops:
            raise OperationError(2, f"卡池 {pool_id} 奖品已全部抽空")
        
        weights = [int(drops_cfg.get(str(d["drop_id"]), {}).get("weight") or 100) * d["remain_num"] for d in avail_drops]
        chosen = random.choices(avail_drops, weights=weights, k=1)[0]
        chosen_drop = chosen["drop_id"]
        
        ctx.db.update_skin_draw_pool_item(self.uid, pool_id, chosen_drop,
                                          chosen["remain_num"] - 1,
                                          chosen["drawn_num"] + 1)
        pool_state[chosen_drop]["remain_num"] -= 1
        pool_state[chosen_drop]["drawn_num"] += 1
        
        unlocked_skins = []
        duplicate_skins = []
        d_cfg = drops_cfg.get(str(chosen_drop)) or {}
        rewards = d_cfg.get("reward") or []
        now = int(time.time())
        skin_map = _get_skin_hero_map()
        
        for rew_id, rew_num in rewards:
            target_sk = _resolve_skin_id(rew_id, skin_map)
            if target_sk:
                existing = ctx.db.query("SELECT skin_id FROM player_skin_unlocked WHERE uid=? AND skin_id=?",
                                        (self.uid, target_sk))
                if existing:
                    duplicate_skins.append(target_sk)
                else:
                    unlocked_skins.append(target_sk)
                    ctx.db.upsert("player_skin_unlocked", self.uid,
                                  {"skin_id": target_sk, "unlock_ts": now, "update_ts": now},
                                  keys=("uid", "skin_id"))
            elif (rew_id >= 6000 and rew_id < 7000):
                ctx.db.upsert("user_scene", self.uid,
                              {"scene_id": rew_id, "lasted_time": 0, "obtain_time": now, "update_ts": now},
                              keys=("uid", "scene_id"))
            else:
                if 90000 <= rew_id < 92000:
                    ctx.db.upsert("chat_sticker", self.uid, {"emoji_id": rew_id}, keys=("uid", "emoji_id"))
                _item_add(ctx, self.uid, rew_id, rew_num)
        
        return {
            "activity_id": aid,
            "pool_id": pool_id,
            "card_index": card_index,
            "chosen_drop": chosen_drop,
            "pool_state": pool_state,
            "unlocked_skins": unlocked_skins,
            "duplicate_skins": duplicate_skins
        }

    def respond(self, result, data, ctx):
        frames = []
        payload = ctx.codec_encode("sc_68187", {
            "result": 0,
            "drop_list": [{
                "card_index": result["card_index"],
                "drop_id": result["chosen_drop"]
            }]
        }) or b"\x08\x00"
        frames.append(DownFrame(self.sc, payload))
        
        state_list = [{"drop_id": did, "num": s["remain_num"]} for did, s in result["pool_state"].items()]
        p68185 = ctx.codec_encode("sc_68185", {
            "activity_id": result["activity_id"],
            "info": state_list,
            "draw_info2": {
                "last_drop": result["chosen_drop"],
                "already_drop": [result["chosen_drop"], 0, 0, 0, 0, 0, 0, 0, 0, 0]
            }
        })
        if p68185:
            frames.append(DownFrame(68185, p68185))
        
        skin_map = _get_skin_hero_map()
        for sk_id in result.get("unlocked_skins") or []:
            hid = skin_map.get(sk_id)
            if hid:
                try:
                    import hero_codec as _hc
                    hf = _hc.build_single_hero_frame(ctx.db, self.uid, hid)
                    if hf:
                        frames.append(DownFrame(14007, hf))
                except Exception:
                    pass
        
        frames.extend(_refresh_frames(ctx, self.uid))
        ctx.log(f"cs_68186 -> 誓约卡池抽取 aid={result['activity_id']} pool={result['pool_id']} card_idx={result['card_index']} drop={result['chosen_drop']}")
        return frames


# ---------------- 邮件与修正者信件系统（30004 / 30006 / 30008 / 30014 / 30018 / 30020 / 30022） ----------------
# 统一委托 mail_service.MailService 领域服务处理

@operation
class MailDeleteOp(Operation):
    """删除邮件：cs_30006 {id} → sc_30007 {id_list}。
    id=0 为一键删除已读邮件；id>0 为删除单封邮件。"""

    cmd = 30006
    sc = 30007

    def apply(self, data, ctx):
        ctx.uid = self.uid
        mid = data.get("id")
        if mid is None:
            mid = 0
        from mail_service import MailService
        deleted_ids = MailService.get_instance(ctx.db).delete_mails(self.uid, mid)
        return {"deleted_ids": deleted_ids}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_30007", {
            "id_list": result["deleted_ids"],
            "no_need_data": []
        }) or b"\x08\x00"
        ctx.log(f"cs_30006 -> 删除邮件 {result['deleted_ids']}")
        return [DownFrame(self.sc, payload)]


@operation
class MailReceiveOp(Operation):
    """领取邮件附件：cs_30004 {id} → sc_30005 {result, attachment_list, success_mail_ids}。
    id=0 为一键领取全部；id>0 为领取单封。附件由 InventoryService 统一接收入库。"""

    cmd = 30004
    sc = 30005

    def apply(self, data, ctx):
        ctx.uid = self.uid
        mid = data.get("id") or data.get("mail_id") or 0
        from mail_service import MailService
        claim_res = MailService.get_instance(ctx.db).claim_mail_attachments(self.uid, mid, ctx=ctx)
        return claim_res

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_30005", {
            "result": 0,
            "attachment_list": result["attachment_list"],
            "success_mail_ids": result["success_mail_ids"]
        }) or b"\x08\x00"
        ctx.log(f"cs_30004 -> 领取邮件附件 count={len(result['success_mail_ids'])} items={result['attachment_list']}")

        extra_frames = []
        # 换装即时推送 sc_14007
        for hid in result.get("touched_heroes") or []:
            try:
                import hero_codec as _hc
                hf = _hc.build_single_hero_frame(ctx.db, self.uid, hid)
                if hf:
                    extra_frames.append(DownFrame(14007, hf))
            except Exception:
                pass

        # 场景即时推送 sc_32009
        if result.get("touched_scene"):
            try:
                import generator as _gen
                p = _gen.gen_payload(32009, uid=self.uid, db=ctx.db)
                if p:
                    extra_frames.append(DownFrame(32009, p))
            except Exception:
                pass

        return _refresh_frames(ctx, self.uid) + extra_frames + [DownFrame(self.sc, payload)]


@operation
class MailGetListOp(Operation):
    """获取普通邮件列表：cs_30002 {} → sc_30003 {mail_list, total_num}。"""

    cmd = 30002
    sc = 30003

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from mail_service import MailService
        mails = MailService.get_instance(ctx.db).get_inbox_list(self.uid)
        return {"mail_list": mails, "total_num": len(mails)}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_30003", {
            "mail_list": result["mail_list"],
            "total_num": result["total_num"]
        }) or b"\x10\x00"
        ctx.log(f"cs_30002 -> 动态下发普通邮件列表 count={result['total_num']}")
        return [DownFrame(self.sc, payload)]


@operation
class MailGetDetailOp(Operation):
    """查看邮件详情：cs_30008 {id} → sc_30009 {result, detail_info}。"""

    cmd = 30008
    sc = 30009

    def apply(self, data, ctx):
        ctx.uid = self.uid
        mid = data.get("id") or 0
        from mail_service import MailService
        detail = MailService.get_instance(ctx.db).get_mail_detail(self.uid, mid)
        return {"mail_id": mid, "detail": detail}

    def respond(self, result, data, ctx):
        if result.get("detail"):
            payload = ctx.codec_encode("sc_30009", {
                "result": 0,
                "detail_info": result["detail"]
            }) or b"\x08\x00"
        else:
            payload = ctx.codec_encode("sc_30009", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_30008 -> 查看邮件详情 id={result['mail_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class LetterReadOp(Operation):
    """修正者专属信件设为已读（cs_30018 {id} → sc_30019 {result}）。
    同时联动同角色全部年度信件设为已读，彻底消除角色头像与红点系统的顽疾！"""

    cmd = 30018
    sc = 30019

    def apply(self, data, ctx):
        ctx.uid = self.uid
        lid = int(data.get("id") or 0)
        from mail_service import MailService
        MailService.get_instance(ctx.db).read_special_letter(self.uid, lid)
        return {"id": lid}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_30019", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_30018 -> 修正者信件设为已读 id={result['id']}")
        return [DownFrame(self.sc, payload)]


@operation
class GetCollectMailListOp(Operation):
    """获取收藏邮件列表：cs_30020 {} → sc_30021 {collect_mail_list, collect_total_num}。"""

    cmd = 30020
    sc = 30021

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from mail_service import MailService
        collect_list = MailService.get_instance(ctx.db).get_collect_list(self.uid)
        return {"collect_list": collect_list, "collect_total_num": len(collect_list)}

    def respond(self, result, data, ctx):
        lst = result["collect_list"]
        payload = ctx.codec_encode("sc_30021", {
            "collect_mail_list": lst,
            "collect_total_num": result.get("collect_total_num", len(lst))
        }) or b"\x08\x00"
        ctx.log(f"cs_30020 -> 获取收藏邮件列表 count={len(lst)}")
        return [DownFrame(self.sc, payload)]


@operation
class GetCollectMailDetailOp(Operation):
    """获取收藏邮件详情：cs_30022 {mail_id} → sc_30023 {result, detail_info}。"""

    cmd = 30022
    sc = 30023

    def apply(self, data, ctx):
        ctx.uid = self.uid
        mid = data.get("mail_id") or 0
        from mail_service import MailService
        detail = MailService.get_instance(ctx.db).get_mail_detail(self.uid, mid)
        return {"mail_id": mid, "detail": detail}

    def respond(self, result, data, ctx):
        if result.get("detail"):
            payload = ctx.codec_encode("sc_30023", {
                "result": 0,
                "detail_info": result["detail"]
            }) or b"\x08\x00"
        else:
            payload = ctx.codec_encode("sc_30023", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_30022 -> 查看收藏邮件详情 id={result['mail_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class MailCollectToggleOp(Operation):
    """收藏/取消收藏邮件：cs_30014 {mail_id, opt} → sc_30015 {result, collect_mail}。"""

    cmd = 30014
    sc = 30015

    def apply(self, data, ctx):
        ctx.uid = self.uid
        mid = data.get("mail_id") or 0
        opt = int(data.get("opt") or 1)
        from mail_service import MailService
        res = MailService.get_instance(ctx.db).toggle_collect(self.uid, mid, opt)
        return {"mail_id": mid, "opt": opt, "collect_mail": res.get("collect_mail")}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_30015", {
            "result": 0,
            "collect_mail": result["collect_mail"]
        }) or b"\x08\x00"
        ctx.log(f"cs_30014 -> 收藏邮件切换 id={result['mail_id']} opt={result['opt']}")
        return [DownFrame(self.sc, payload)]


# ---------------- 战令 / 通行证 (Passport / BattlePass) ----------------

_BATTLEPASS_CFG = None

def _get_battlepass_cfg():
    global _BATTLEPASS_CFG
    if _BATTLEPASS_CFG is None:
        cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "battlepass_cfg.json")
        try:
            if os.path.exists(cfg_file):
                with open(cfg_file, "r", encoding="utf-8") as f:
                    _BATTLEPASS_CFG = json.load(f)
            else:
                _BATTLEPASS_CFG = {}
        except Exception:
            _BATTLEPASS_CFG = {}
    return _BATTLEPASS_CFG


def _get_battlepass_level(ctx, uid):
    """根据 14 号货币（BATTLEPASS_EXP）计算当前战令等级（1000 exp / 级，上限 70）。"""
    rows = ctx.db.query("SELECT num FROM currency WHERE uid=? AND id=14", (uid,))
    exp = int(rows[0]["num"]) if rows else 0
    return min(70, exp // 1000)


@operation
class PassportGetInfoOp(Operation):
    """战令/通行证数据请求：cs_34030 {} → sc_34031。"""

    cmd = 34030
    sc = 34031

    def apply(self, data, ctx):
        ctx.uid = self.uid
        return {}

    def respond(self, result, data, ctx):
        import generator as _g
        payload = _g.gen_payload(34031, uid=self.uid, db=ctx.db)
        ctx.log(f"cs_34030 -> 动态下发战令/通行证数据 (sc_34031)")
        return [DownFrame(self.sc, payload)]


@operation
class PassportClaimBonusOp(Operation):
    """战令/通行证单个奖励领取：cs_34032 {id, is_pay} → sc_34033 {result, receive_info, reward_list}。"""

    cmd = 34032
    sc = 34033

    def apply(self, data, ctx):
        ctx.uid = self.uid
        bid = int(data.get("id") or 0)
        is_pay = int(data.get("is_pay") or 0)

        cfg = _get_battlepass_cfg()
        entries = cfg.get("entries", {})
        entry = entries.get(str(bid))
        if not entry:
            raise OperationError(406, f"战令奖励条目 {bid} 不存在")

        # 校验玩家战令等级是否达到
        bp_type = entry.get("type", 32)
        type_list = cfg.get("types", {}).get(str(bp_type), [])
        entry_lv = 1
        for idx, it in enumerate(type_list, 1):
            if it["id"] == bid:
                entry_lv = idx
                break

        cur_lv = _get_battlepass_level(ctx, self.uid)
        if entry_lv > cur_lv:
            raise OperationError(2, f"战令等级未达到 (需要 Lv.{entry_lv}, 当前 Lv.{cur_lv})")

        b = ctx.db.get_or_create_battlepass(self.uid)
        pay_level = int(b.get("pay_level") or 0)

        if is_pay == 1 and pay_level <= 0:
            raise OperationError(2, "未解锁付费通行证，无法领取付费档奖励")

        rec_info = []
        try:
            rec_info = json.loads(b.get("receive_info") or "[]")
        except Exception:
            rec_info = []

        # 检查是否已领
        for item in rec_info:
            if item.get("id") == bid and item.get("is_pay") == is_pay:
                return {"bid": bid, "is_pay": is_pay, "rewards": [], "receive_info": rec_info}

        # 获取奖励配置
        rewards_cfg = entry.get("reward_pay" if is_pay == 1 else "reward_free", [])
        rewards = []
        for r in rewards_cfg:
            item_id, item_num = r[0], r[1]
            _item_add(ctx, self.uid, item_id, item_num)
            rewards.append({"id": item_id, "num": item_num})

        rec_info.append({"id": bid, "is_pay": is_pay})
        ctx.db.save_battlepass_receive_info(self.uid, rec_info)

        return {"bid": bid, "is_pay": is_pay, "rewards": rewards, "receive_info": rec_info}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_34033", {
            "result": 0,
            "receive_info": result["receive_info"],
            "reward_list": result["rewards"]
        }) or b"\x08\x00"
        ctx.log(f"cs_34032 -> 领取战令奖励 id={result['bid']} is_pay={result['is_pay']} 获得: {result['rewards']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class PassportOneKeyGetOp(Operation):
    """战令/通行证一键全领：cs_34034 {} → sc_34035 {result, receive_info, reward_list}。"""

    cmd = 34034
    sc = 34035

    def apply(self, data, ctx):
        ctx.uid = self.uid
        cur_lv = _get_battlepass_level(ctx, self.uid)
        b = ctx.db.get_or_create_battlepass(self.uid)
        pay_level = int(b.get("pay_level") or 0)
        import res_version_manager as _rvm
        version_cfg = _rvm.get_version_config()
        list_id = int(version_cfg["battlepass_list_id"])

        cfg = _get_battlepass_cfg()
        list_cfg = cfg.get("lists", {}).get(str(list_id), {})
        bp_type = list_cfg.get("battlepass_type", int(version_cfg["battlepass_season"]))
        type_list = cfg.get("types", {}).get(str(bp_type), [])

        rec_info = []
        try:
            rec_info = json.loads(b.get("receive_info") or "[]")
        except Exception:
            rec_info = []

        claimed_set = {(x.get("id"), x.get("is_pay")) for x in rec_info}
        merged_rewards = {}
        new_rec_info = list(rec_info)

        # 遍历已达成的等级（1..cur_lv）
        for idx in range(min(cur_lv, len(type_list))):
            entry = type_list[idx]
            bid = entry["id"]

            # 免费轨
            if (bid, 0) not in claimed_set:
                for r in entry.get("reward_free", []):
                    item_id, item_num = r[0], r[1]
                    _item_add(ctx, self.uid, item_id, item_num)
                    merged_rewards[item_id] = merged_rewards.get(item_id, 0) + item_num
                new_rec_info.append({"id": bid, "is_pay": 0})
                claimed_set.add((bid, 0))

            # 付费轨
            if pay_level > 0 and (bid, 1) not in claimed_set:
                for r in entry.get("reward_pay", []):
                    item_id, item_num = r[0], r[1]
                    _item_add(ctx, self.uid, item_id, item_num)
                    merged_rewards[item_id] = merged_rewards.get(item_id, 0) + item_num
                new_rec_info.append({"id": bid, "is_pay": 1})
                claimed_set.add((bid, 1))

        if len(new_rec_info) > len(rec_info):
            ctx.db.save_battlepass_receive_info(self.uid, new_rec_info)

        rewards = [{"id": k, "num": v} for k, v in merged_rewards.items()]
        return {"rewards": rewards, "receive_info": new_rec_info}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_34035", {
            "result": 0,
            "receive_info": result["receive_info"],
            "reward_list": result["rewards"]
        }) or b"\x08\x00"
        ctx.log(f"cs_34034 -> 战令一键全领，共发放 {len(result['rewards'])} 种道具: {result['rewards']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class PassportBuyLevelOp(Operation):
    """战令/通行证购买等级：cs_34036 {num} → sc_34037 {result}。"""

    cmd = 34036
    sc = 34037

    def apply(self, data, ctx):
        ctx.uid = self.uid
        num = int(data.get("num") or 0)
        if num <= 0:
            raise OperationError(5, "购买等级数量必须大于0")

        cur_lv = _get_battlepass_level(ctx, self.uid)
        if cur_lv >= 70:
            raise OperationError(2, "战令已达最高等级(Lv.70)")
        if cur_lv + num > 70:
            num = 70 - cur_lv

        # 消耗计算：150 移转之辉 (id=1) / 级
        cost = 150 * num
        _item_deduct(ctx, self.uid, 1, cost)

        # 增加 14 号战令经验 (1000 / 级)
        exp_add = 1000 * num
        _item_add(ctx, self.uid, 14, exp_add)

        return {"num": num, "cost": cost, "exp_add": exp_add}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_34037", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_34036 -> 购买战令等级: +{result['num']}级, 消耗移转之辉={result['cost']}, 增加经验={result['exp_add']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)



# ---------------- 大厅场景与展示 (Home Scene) ----------------
# [2026-09-06 外围模块迁移] HomeSceneSetOp(32108 设置主界面大厅场景) 已迁至
# peripheral_service.py（独立外围系统模块，含 PROFILE_UPDATE 事件总线挂载）。



# ---------------- 修正者角色战斗养成与构筑 (统一委托给 HeroService) ----------------

@operation
class HeroAddExpOp(Operation):
    """英雄加经验：cs_14014 {id, item_list} → sc_14015 {result}。
    统一委托给 HeroService 领域服务处理。"""

    cmd = 14014
    sc = 14015

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("id") or data.get("hero_id") or 0)
        items = data.get("item_list") or []
        return svc.add_hero_exp(ctx, self.uid, hid, items)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14015", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14014 -> 英雄加经验 hero={result['hero_id']} +{result['exp_add']}exp → Lv{result['level']} ({result['exp']})")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        return [DownFrame(self.sc, payload)] + svc.build_hero_refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class HeroBreakOp(Operation):
    """英雄突破：cs_14036 {hero_id} → sc_14037 {result}。
    统一委托给 HeroService 领域服务处理。"""

    cmd = 14036
    sc = 14037

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or data.get("id") or 0)
        return svc.break_hero(ctx, self.uid, hid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14037", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14036 -> 英雄突破 hero={result['hero_id']} → 突破阶数 {result['break_level']}")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        return [DownFrame(self.sc, payload)] + svc.build_hero_refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class OneClickHeroUpOp(Operation):
    """英雄一键升级+突破：cs_14120 {hero_id, level, break_list, item_list} → sc_14121 {result}。
    统一委托给 HeroService 领域服务处理。"""

    cmd = 14120
    sc = 14121

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        tlvl = int(data.get("level") or 0)
        blist = data.get("break_list") or []
        ilist = data.get("item_list") or []
        return svc.one_click_hero_up(ctx, self.uid, hid, target_level=tlvl, break_list=blist, item_list=ilist)

    def respond(self, result, data, ctx):
        refund_items = result.get("refund_items") or []
        resp_data = {"result": 0}
        if refund_items:
            resp_data["item_list"] = refund_items
        payload = ctx.codec_encode("sc_14121", resp_data) or b"\x08\x00"
        ctx.log(f"cs_14120 -> 一键升级+突破 hero={result['hero_id']} → Lv{result['level']} 突破{result['breaks']}次 返还{len(refund_items)}组材料")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        return [DownFrame(self.sc, payload)] + svc.build_hero_refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class SkillUpOp(Operation):
    """技能升级：cs_14030 {hero_id, skill_id, num} → sc_14031 {result}。
    统一委托给 HeroService 领域服务处理。
    注意：客户端 OnHeroSkillUpgrade 会在收到 sc_14031 后本地累加 num 级，
    因此严禁下发 SC_14007，否则会导致技能等级被刷新覆盖后再累加，发生翻倍叠加并越界崩溃。"""

    cmd = 14030
    sc = 14031

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        sid = data.get("skill_id") or data.get("id")
        num = int(data.get("num") or 1)
        return svc.upgrade_skill(ctx, self.uid, hid, sid, num=num)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14031", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14030 -> 技能升级 hero={result['hero_id']} skill={result['skill']} +{result['num']}级 → Lv{result['level']} (消耗神力因子:{result.get('factor_cost', 0)})")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        return svc.build_hero_refresh_frames(ctx, self.uid, hero_id=None) + [DownFrame(self.sc, payload)]


@operation
class SkillElementOp(Operation):
    """技能属性强化：cs_14044 {hero_id, index, num} → sc_14045 {result}。
    统一委托给 HeroService 领域服务处理。
    注意：客户端 OnHeroSkillAttrUpgrade 会在收到 sc_14045 后本地累加 num 级，严禁下发 SC_14007。
    但必须下发 SC_17023 原子差量变动帧，确保材料与神力因子扣减实时同步至客户端。"""

    cmd = 14044
    sc = 14045

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        idx = int(data.get("index") or 0)
        num = int(data.get("num") or 0)
        return svc.enhance_skill_element(ctx, self.uid, hid, idx, num=num)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14045", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14044 -> 属性强化 index={result['index']} → Lv{result['level']}")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        return svc.build_hero_refresh_frames(ctx, self.uid, hero_id=None) + [DownFrame(self.sc, payload)]


@operation
class HeroStarUpOp(Operation):
    """角色神识超越/升星：cs_14012 {id} → sc_14013 {result: 0}。
    统一委托给 HeroService 领域服务处理。
    注意：客户端 OnHeroStarUp 会在收到 sc_14013 后本地 StarUp，严禁下发 SC_14007 避免重算下一阶。
    但必须下发 SC_17023 原子差量变动帧，确保金币与碎片扣除实时生效。"""

    cmd = 14012
    sc = 14013

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("id") or data.get("hero_id") or 0)
        return svc.star_up_hero(ctx, self.uid, hid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14013", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14012 -> 角色超越 hero={result['hero_id']} star {result['old_star']} -> "
                f"{result['new_star']}（扣碎片{result['piece_cost']} 余{result['piece_left']}，"
                f"金币{result['gold_cost']}）")
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        return svc.build_hero_refresh_frames(ctx, self.uid, hero_id=None) + [DownFrame(self.sc, payload)]


@operation
class HeroSkinSelectOp(Operation):
    """皮肤选择/立绘切换：cs_14034 {hero_id, skin_id} → sc_14035 {result: 0}。
    统一委托给 HeroService 领域服务处理。
    【逆序纠偏机制】：
    由于客户端本地代码在收到 sc_14035 时会乐观触发 HeroData:SetBattleSkin 强行同步出战皮肤，
    服务端在此处先下发应答帧 sc_14035，紧接着下发携带数据库真实 battle_using_skin 的 sc_14007 英雄差量帧。
    客户端收到 sc_14007 后执行 HeroData:ModifyHero，强制将 battle_using_skin 恢复为真实状态，
    彻底击碎客户端的自作主张，确保作战换装开关独立分立！
    """

    cmd = 14034
    sc = 14035

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        sid = int(data.get("skin_id") or 0)
        return svc.set_using_skin(ctx, self.uid, hid, sid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14035", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14034 -> 皮肤切换 hero={result['hero_id']} skin={result['skin_id']} (逆序纠偏下发 sc_14007)")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, hero_id=result["hero_id"])


@operation
class HeroBattleSkinChangeOp(Operation):
    """战斗皮肤切换：cs_14046 {hero_id, skin_id} → sc_14047 {result: 0}。
    统一委托给 HeroService 领域服务处理。独立控制出战3D模型皮肤。"""

    cmd = 14046
    sc = 14047

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        hid = int(data.get("hero_id") or 0)
        sid = int(data.get("skin_id") or 0)
        return svc.set_battle_skin(ctx, self.uid, hid, sid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14047", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14046 -> 战斗皮肤切换 hero={result['hero_id']} skin={result['skin_id']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, hero_id=result["hero_id"])


@operation
class HeroUnlockSkinOp(Operation):
    """皮肤解锁：cs_14110 {skin_id} → sc_14111 {result: 0}。
    统一委托给 HeroService 领域服务处理，支持伴生专属头像与大厅场景的联动解锁。"""

    cmd = 14110
    sc = 14111

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from hero_service import HeroService
        svc = HeroService.get_instance(db=ctx.db)
        sid = int(data.get("skin_id") or 0)
        return svc.unlock_skin(ctx, self.uid, sid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14111", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14110 -> 皮肤解锁 skin={result['skin_id']} hero={result.get('hero_id')}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class HeroAstrolabeUnlockOp(Operation):
    """神格节点解锁：cs_14026 {id, astrolabe_id} → sc_14027 {result: 0}。"""

    cmd = 14026
    sc = 14027

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("id") or 0)
        aid = int(data.get("astrolabe_id") or 0)
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).unlock_astrolabe_node(ctx, self.uid, hid, aid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14027", {"result": 0, "hero_id": result["hero_id"], "unlock_id": result["astrolabe_id"]}) or b"\x08\x00"
        ctx.log(f"cs_14026 -> 神格解锁 hero={result['hero_id']} node={result['astrolabe_id']}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class HeroAstrolabeEquipOp(Operation):
    """神格装配/卸下：cs_14028 {operation: 1|2, id, astrolabe_id} → sc_14029 {result: 0}。"""

    cmd = 14028
    sc = 14029

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("id") or 0)
        aid = int(data.get("astrolabe_id") or 0)
        op_type = int(data.get("operation") or 1)
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).equip_astrolabe_node(ctx, self.uid, hid, aid, op_type)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14029", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14028 -> 神格调整 hero={result['hero_id']} op={result['operation']} node={result['astrolabe_id']} now={result['using']}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class HeroAstrolabeUnloadAllOp(Operation):
    """神格一键全部卸下：cs_14040 {hero_id} → sc_14041 {result: 0}。"""

    cmd = 14040
    sc = 14041

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).unload_all_astrolabe(ctx, self.uid, hid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14041", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14040 -> 神格一键卸下 hero={result['hero_id']}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class HeroAstrolabeEquipListOp(Operation):
    """神格按列表装配：cs_71116 {hero_id, astrolabe_id_list} → sc_71117 {result: 0}。"""

    cmd = 71116
    sc = 71117

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        lst = data.get("astrolabe_id_list") or []
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).equip_astrolabe_list(ctx, self.uid, hid, lst)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_71117", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_71116 -> 神格批量装配 hero={result['hero_id']} list={result['list']}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class HeroSendGiftOp(Operation):
    """好感度送礼：cs_14100 {archive_id, gift_list} → sc_14101 {result: 0}。"""

    cmd = 14100
    sc = 14101

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from archive_service import ArchiveService
        try:
            return ArchiveService.get_instance().give_gifts(
                ctx,
                self.uid,
                int(data.get("archive_id") or 0),
                data.get("gift_list") or [],
            )
        except ValueError as exc:
            raise OperationError(2, str(exc))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14101", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14100 -> 好感度送礼 archive={result['archive_id']} +{result['exp_added']}exp")
        from archive_service import ArchiveService
        _, cfg = ArchiveService.get_instance().get_archive_config(result.get("archive_id"))
        hids = (cfg.get("hero_ids") or []) if cfg else []
        hid = hids[0] if hids else result.get("archive_id")
        return _refresh_frames(ctx, self.uid, hero_id=hid) + [DownFrame(self.sc, payload)]


@operation
class ViewArchiveStoryOp(Operation):
    """档案轶事阅读：cs_71108 {hero_id} → sc_71109 {result, reward_list}。"""

    cmd = 71108
    sc = 71109

    def validate(self, data, ctx):
        if int(data.get("hero_id") or 0) <= 0:
            raise OperationError(1, "缺少 hero_id")
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from archive_service import ArchiveService
        try:
            return ArchiveService.get_instance().read_anecdote(
                ctx, self.uid, int(data["hero_id"])
            )
        except ValueError as exc:
            raise OperationError(2, str(exc))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode(
            "sc_71109", {"result": 0, "reward_list": result.get("rewards") or []}
        ) or b"\x08\x00"
        ctx.log(
            f"cs_71108 -> 档案轶事 hero={result['hero_id']} first={result['first_view']}"
        )
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class ViewSuperHeartOp(Operation):
    """超心链阅读：cs_71114 {archive_id, index} → sc_71115 {result}。"""

    cmd = 71114
    sc = 71115

    def validate(self, data, ctx):
        if int(data.get("archive_id") or 0) <= 0 or int(data.get("index") or 0) <= 0:
            raise OperationError(1, "缺少 archive_id/index")
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from archive_service import ArchiveService
        try:
            return ArchiveService.get_instance().read_super_heart(
                ctx,
                self.uid,
                int(data["archive_id"]),
                int(data["index"]),
            )
        except ValueError as exc:
            raise OperationError(2, str(exc))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_71115", {"result": 0}) or b"\x08\x00"
        ctx.log(
            f"cs_71114 -> 超心链 archive={result['archive_id']} index={result['index']} story={result['story_id']}"
        )
        from archive_service import ArchiveService
        _, cfg = ArchiveService.get_instance().get_archive_config(result.get("archive_id"))
        hids = (cfg.get("hero_ids") or []) if cfg else []
        hid = hids[0] if hids else result.get("archive_id")
        return _refresh_frames(ctx, self.uid, hero_id=hid) + [DownFrame(self.sc, payload)]


@operation
class HeroReadHeartLinkOp(Operation):
    """心链阅读：cs_14102 {archive_id, text_list} → sc_14103 {result: 0}。"""

    cmd = 14102
    sc = 14103

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from archive_service import ArchiveService
        try:
            return ArchiveService.get_instance().read_heart_texts(
                ctx,
                self.uid,
                int(data.get("archive_id") or 0),
                data.get("text_list") or [],
            )
        except ValueError as exc:
            raise OperationError(2, str(exc))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14103", {"result": 0}) or b"\x08\x00"
        from archive_service import ArchiveService
        _, cfg = ArchiveService.get_instance().get_archive_config(result.get("archive_id"))
        hids = (cfg.get("hero_ids") or []) if cfg else []
        hid = hids[0] if hids else result.get("archive_id")
        return _refresh_frames(ctx, self.uid, hero_id=hid) + [DownFrame(self.sc, payload)]


_STORY_TO_PICS_CACHE = None
_PIC_REWARDS_CACHE = None

def _load_picture_cfg():
    global _STORY_TO_PICS_CACHE, _PIC_REWARDS_CACHE
    if _STORY_TO_PICS_CACHE is not None:
        return _STORY_TO_PICS_CACHE, _PIC_REWARDS_CACHE
    meta_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "illustrated_meta.json")
    s2p = {}
    prew = {}
    if os.path.exists(meta_p):
        try:
            with open(meta_p, "r", encoding="utf-8") as f:
                meta = json.load(f)
            raw_s2p = meta.get("story_to_pics", {})
            s2p = {int(k): [int(x) for x in v] for k, v in raw_s2p.items() if str(k).isdigit()}
            pic_info = meta.get("pic_info", {})
            for pid_str, pdata in pic_info.items():
                pid = int(pid_str)
                rews = pdata.get("rewards")
                if rews and len(rews) > 0 and len(rews[0]) >= 2:
                    prew[pid] = (int(rews[0][0]), int(rews[0][1]))
                else:
                    prew[pid] = (1, 20)
        except Exception:
            pass
    _STORY_TO_PICS_CACHE = s2p
    _PIC_REWARDS_CACHE = prew
    return s2p, prew


@operation
class PlayerChangeStoryOp(Operation):
    """剧情观看/记录：cs_12002 {story_id} → sc_12003 {result: 0, story_id}。"""

    cmd = 12002
    sc = 12003

    def apply(self, data, ctx):
        ctx.uid = self.uid
        story_id = int(data.get("story_id") or 0)
        if story_id:
            event_bus.bus.emit(event_bus.Events.STORY_READ, ctx, self.uid, story_id=story_id, source="player_change_story")
        return {"story_id": story_id}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_12003", {"result": 0, "story_id": result["story_id"]}) or b"\x08\x00"
        ctx.log(f"cs_12002 -> 剧情观看 story_id={result['story_id']}")
        # STORY_READ 的图鉴/誓约监听器可能排入额外推送，必须在本次请求中一并冲刷。
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class HeroReadStoryOp(Operation):
    """剧情阅读/插图收集：cs_14104 {archive_id, video_list} → sc_14105 {result: 0}。"""

    cmd = 14104
    sc = 14105

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from archive_service import ArchiveService
        try:
            return ArchiveService.get_instance().read_normal_stories(
                ctx,
                self.uid,
                int(data.get("archive_id") or 0),
                data.get("video_list") or [],
            )
        except ValueError as exc:
            raise OperationError(2, str(exc))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14105", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14104 -> 剧情档案阅读 archive={result['archive_id']} videos={result.get('video_list')}")
        from archive_service import ArchiveService
        _, cfg = ArchiveService.get_instance().get_archive_config(result.get("archive_id"))
        hids = (cfg.get("hero_ids") or []) if cfg else []
        hid = hids[0] if hids else result.get("archive_id")
        return _refresh_frames(ctx, self.uid, hero_id=hid) + [DownFrame(self.sc, payload)]


# [2026-09-06 外围模块迁移] QuerySetBgmOp(12026 设置大厅BGM) 已迁至 peripheral_service.py
# （落库双写 illustrated + game_user.bgm_id；原 middleware.h_12026 死代码同步移除）。


@operation
class ReceiveIllustrationRewardOp(Operation):
    """领取插画奖励：cs_52004 {id_list} → sc_52005 {result: 0, item_list}。"""

    cmd = 52004
    sc = 52005

    def apply(self, data, ctx):
        ctx.uid = self.uid
        id_list = [int(x) for x in (data.get("id_list") or []) if str(x).isdigit()]
        # 插图领取统一结算：无论点击单个还是全部，均自动拉取当前所有待领取的插画 ID 一并结算（仅限已解锁条目）
        unclaimed = ctx.db.query(
            "SELECT item_id FROM illustrated WHERE uid=? AND kind='inbetweening' AND is_receive=0 AND (complete_flag IS NULL OR complete_flag != 0)",
            (self.uid,)
        )
        all_unclaimed_ids = [int(r["item_id"]) for r in unclaimed] if unclaimed else []
        id_list = sorted(list(set(id_list + all_unclaimed_ids)))

        _, prew = _load_picture_cfg()
        total_items = {}
        claimed_ids = []
        for pid in id_list:
            p_int = int(pid)
            r = ctx.db.query("SELECT is_receive, complete_flag FROM illustrated WHERE uid=? AND kind='inbetweening' AND item_id=?", (self.uid, p_int))
            if r and (r[0]["is_receive"] or 0) == 0:
                cflag = r[0].get("complete_flag")
                if cflag is not None and cflag == 0:
                    continue
                ctx.db.execute(
                    "UPDATE illustrated SET is_receive=1 WHERE uid=? AND kind='inbetweening' AND item_id=?",
                    (self.uid, p_int)
                )
                rew = prew.get(p_int, (1, 20))
                cid, num = rew
                _item_add(ctx, self.uid, cid, num)
                total_items[cid] = total_items.get(cid, 0) + num
                claimed_ids.append(p_int)

        items_out = [{"id": cid, "num": n} for cid, n in total_items.items()]
        return {"id_list": claimed_ids, "items": items_out}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_52005", {"result": 0, "item_list": result["items"]}) or b"\x08\x00"
        ctx.log(f"cs_52004 -> 领取插图奖励 id_list={result['id_list']} 获得={result['items']}")
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class ViewIllustrationOp(Operation):
    """查看图鉴条目（消除红点）：cs_52014 {id, type} → sc_52015 {result: 0}。"""

    cmd = 52014
    sc = 52015

    # CollectConst（collectconst.lua）：1情报 2视骸 3钥从 4刻印 5剧情 6插画 7世界观
    _TYPE_KIND = {2: "enemy", 3: "servant", 4: "equip", 5: "plot", 6: "inbetweening", 7: "affix"}

    def apply(self, data, ctx):
        ctx.uid = self.uid
        item_id = int(data.get("id") or 0)
        kind = self._TYPE_KIND.get(int(data.get("type") or 0))
        if item_id and kind:
            # 带 kind 条件：affix 与 enemy 存在 18 个撞号 item_id，不带会把两类同时点亮
            ctx.db.execute(
                "UPDATE illustrated SET is_view=1 WHERE uid=? AND kind=? AND item_id=?",
                (self.uid, kind, item_id)
            )
        elif item_id:
            ctx.db.execute(
                "UPDATE illustrated SET is_view=1 WHERE uid=? AND item_id=?",
                (self.uid, item_id)
            )
        return {"id": item_id, "type": data.get("type")}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_52015", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class SaveLoadingSetOp(Operation):
    """保存加载图壁纸列表：cs_52016 {all_id_list} → sc_52017 {result: 0}。"""

    cmd = 52016
    sc = 52017

    def apply(self, data, ctx):
        ctx.uid = self.uid
        all_id_list = data.get("all_id_list") or []
        ctx.db.execute(
            "INSERT OR REPLACE INTO loading_set (uid, id_list, update_ts) VALUES (?, ?, ?)",
            (self.uid, json.dumps(all_id_list), int(time.time()))
        )
        return {"count": len(all_id_list)}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_52017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_52016 -> 保存加载图设置 count={result['count']}")
        return [DownFrame(self.sc, payload)]


@operation
class ChangeLoadingSetOp(Operation):
    """设置/切换单张加载图：cs_52018 {type, id} → sc_52019 {result: 0}。"""

    cmd = 52018
    sc = 52019

    def apply(self, data, ctx):
        ctx.uid = self.uid
        typ = data.get("type")
        pic_id = int(data.get("id") or 0)
        r = ctx.db.query("SELECT id_list FROM loading_set WHERE uid=?", (self.uid,))
        ids = json.loads(r[0]["id_list"]) if r and r[0]["id_list"] else []
        if typ and pic_id:
            if pic_id not in ids:
                ids.append(pic_id)
        elif pic_id in ids:
            ids.remove(pic_id)
        ctx.db.execute(
            "INSERT OR REPLACE INTO loading_set (uid, id_list, update_ts) VALUES (?, ?, ?)",
            (self.uid, json.dumps(ids), int(time.time()))
        )
        return {"id": pic_id, "type": typ}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_52019", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_52018 -> 切换单张加载图 id={result['id']} type={result['type']}")
        return [DownFrame(self.sc, payload)]


@operation
class HeroRaceCollectRewardOp(Operation):
    """领取修正者阵营收集奖励：cs_52022 {collect_info_list} → sc_52023 {result: 0, item_list}。"""

    cmd = 52022
    sc = 52023

    def apply(self, data, ctx):
        ctx.uid = self.uid
        req_list = data.get("collect_info_list") or []
        race_cfg = illustrated_listener._get_hero_race_cfg()
        by_race = race_cfg.get("by_race", {})

        # 统计各阵营实际拥有修正者数量
        race_counts = illustrated_listener.get_hero_race_counts(ctx.db, self.uid)

        total_items = {}
        for item in req_list:
            race_type = int(item.get("race_type") or 0)
            cnt_list = item.get("cnt_list") or []
            user_cnt = race_counts.get(race_type, 0)

            # 查询当前已领取的档位
            r = ctx.db.query("SELECT received_cnt_list FROM hero_race_collect WHERE uid=? AND race_type=?", (self.uid, race_type))
            claimed_set = set(json.loads(r[0]["received_cnt_list"])) if r and r[0]["received_cnt_list"] else set()

            tasks = by_race.get(str(race_type), [])
            for c in cnt_list:
                c_int = int(c)
                if c_int not in claimed_set and user_cnt >= c_int:
                    for t in tasks:
                        if t["need"] == c_int:
                            claimed_set.add(c_int)
                            for rw in t.get("reward", []):
                                cid = rw["id"]
                                cnum = rw["num"]
                                _item_add(ctx, self.uid, cid, cnum)
                                total_items[cid] = total_items.get(cid, 0) + cnum
                            break

            ctx.db.execute(
                "INSERT OR REPLACE INTO hero_race_collect (uid, race_type, received_cnt_list) VALUES (?, ?, ?)",
                (self.uid, race_type, json.dumps(sorted(list(claimed_set))))
            )

        items_out = [{"id": cid, "num": n} for cid, n in total_items.items()]
        return {"items": items_out}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_52023", {"result": 0, "item_list": result["items"]}) or b"\x08\x00"
        ctx.log(f"cs_52022 -> 领取阵营收集奖励 items={result['items']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class HeroFavoriteOnOp(Operation):
    """设为特别关注：cs_14106 {hero_id} → sc_14107 {result: 0}。"""
    cmd = 14106
    sc = 14107

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).set_favorite(ctx, self.uid, hid, True)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14107", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14106 -> 设为关注 hero={result['hero_id']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class HeroFavoriteOffOp(Operation):
    """取消特别关注：cs_14108 {hero_id} → sc_14109 {result: 0}。"""

    cmd = 14108
    sc = 14109

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).set_favorite(ctx, self.uid, hid, False)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14109", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14108 -> 取消关注 hero={result['hero_id']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)




@operation
class ResolveModuleItemOp(Operation):
    """模组材料/同调档案回销：cs_14118 {item_list} → sc_14119 {result: 0} + 材料刷新帧。
    统一委托给 ShopService 领域服务处理（1:4 兑换行动记录 41601）。"""

    cmd = 14118
    sc = 14119

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from shop_service import ShopService
        svc = ShopService.get_instance(db=ctx.db)
        items = data.get("item_list") or []
        frames = svc.resolve_module_items(ctx, self.uid, items)
        return {"frames": frames}

    def respond(self, result, data, ctx):
        return result.get("frames") or []



from hero_service import TRANSITION_TALENT_COST as _TRANSITION_TALENT_COST


@operation
class ImproveTransitionGiftPtOp(Operation):
    """跃迁槽位天赋点强化：cs_14112 {hero_id, slot_id, lv_up_num} → sc_14113 {result: 0}。"""

    cmd = 14112
    sc = 14113

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        slot_id = int(data.get("slot_id") or 0)
        lv_up_num = max(1, int(data.get("lv_up_num") or 1))
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).improve_transition_gift_pt(ctx, self.uid, hid, slot_id, lv_up_num)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14113", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14112 -> 跃迁槽位强化 hero={result['hero_id']} slot={result['slot_id']} +{result['lv_up_num']}点 → {result['talent_points']}点")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class SaveTransitionSkillOp(Operation):
    """保存跃迁技能：cs_14114 {hero_id, slot_id, skill_list} → sc_14115 {result: 0}。"""

    cmd = 14114
    sc = 14115

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        slot_id = int(data.get("slot_id") or 0)
        skills = data.get("skill_list") or []
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).save_transition_skill(ctx, self.uid, hid, slot_id, skills)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14115", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14114 -> 保存跃迁技能 hero={result['hero_id']} slot={result['slot_id']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class LevelUpModuleOp(Operation):
    """同调武器模块升级：cs_14116 {hero_id} → sc_14117 {result: 0}。"""

    cmd = 14116
    sc = 14117

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).level_up_module(ctx, self.uid, hid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14117", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14116 -> 同调武器模块升级 hero={result['hero_id']} → Lv{result['level']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class HeroAstrolabeEquipSuitOp(Operation):
    """神格流派一键装配：cs_14038 {hero_id, astrolabe_suit_id} → sc_14039 {result: 0}。"""

    cmd = 14038
    sc = 14039

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hid = int(data.get("hero_id") or 0)
        suit_id = int(data.get("astrolabe_suit_id") or 0)
        from hero_service import HeroService
        return HeroService.get_instance(ctx.db).equip_astrolabe_suit(ctx, self.uid, hid, suit_id)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14039", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_14038 -> 神格流派装配 hero={result['hero_id']} suit={result['suit_id']} using={result.get('using')}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class QueryHeroNewDataOp(Operation):
    """查询修正者新数据：cs_14042 {hero_id} → sc_14043 {result: 0}。"""

    cmd = 14042
    sc = 14043

    def apply(self, data, ctx):
        return {"hero_id": data.get("hero_id")}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14043", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class ReqHeroHeartRateOp(Operation):
    """查询心动值：cs_14122 {hero_id} → sc_14123 {result: 0, value: 100}。"""

    cmd = 14122
    sc = 14123

    def apply(self, data, ctx):
        return {"hero_id": data.get("hero_id")}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14123", {"result": 0, "value": 100}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


# ---------------- 刻印系统 (Equip System) ----------------
# 全量委托 EquipService 统一领域服务处理

@operation
class EquipEnhanceOp(Operation):
    """刻印强化：cs_13014 {equip_id, mat_list, equip_list} → sc_13015 {result, mat_list}。"""

    cmd = 13014
    sc = 13015

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        mats = data.get("mat_list") or []
        el = data.get("equip_list") or []
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).enhance_equip(ctx, self.uid, eid, mat_list=mats, equip_list=el)

    def respond(self, result, data, ctx):
        resp_dict = {"result": 0}
        if result.get("refund_mats"):
            resp_dict["mat_list"] = result["refund_mats"]
        payload = ctx.codec_encode("sc_13015", resp_dict) or b"\x08\x00"
        ctx.log(f"cs_13014 -> 强化 equip={result['equip_id']} +{result.get('exp_add', 0)}exp → Lv{result.get('level', 1)} ({result.get('exp', 0)})")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class EquipBreakOp(Operation):
    """刻印突破：cs_13022 {equip_id} → sc_13023 {result}。"""

    cmd = 13022
    sc = 13023

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).breakthrough_equip(ctx, self.uid, eid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13023", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13022 -> 突破 equip={result['equip_id']} → break Lv{result.get('break_level', 1)}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class OneClickEnhanceOp(Operation):
    """刻印一键强化+突破：cs_13058 {equip_id, equip_list, mat_list, break_times, target_level} → sc_13059 {result, mat_list}。"""

    cmd = 13058
    sc = 13059

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        el = data.get("equip_list") or []
        ml = data.get("mat_list") or []
        bt = int(data.get("break_times") or 0)
        tl = data.get("target_level")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).one_click_enhance(
            ctx, self.uid, eid, mat_list=ml, equip_list=el, break_times=bt, target_level=tl
        )

    def respond(self, result, data, ctx):
        resp_dict = {"result": 0}
        if result.get("refund_mats"):
            resp_dict["mat_list"] = result["refund_mats"]
        payload = ctx.codec_encode("sc_13059", resp_dict) or b"\x08\x00"
        ctx.log(f"cs_13058 -> 一键强化 equip={result['equip_id']} exp={result.get('exp', 0)} 突破{result.get('breaks', 0)}次")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class EquipLockOp(Operation):
    """刻印锁定：cs_13016 {equip_id, is_lock} → sc_13017 {result}。"""

    cmd = 13016
    sc = 13017

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        is_lock = bool(data.get("is_lock"))
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).lock_equip(ctx, self.uid, eid, is_lock)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13016 -> 刻印锁定 equip={result['equip_id']} lock={result.get('is_lock', 0)}")
        return [DownFrame(self.sc, payload)]


def _hero_equip_slot(ctx, uid, hero_id):
    """读 hero.equip_slot（JSON dict {"1":equip_id, ... "6":equip_id}）。"""
    from hero_service import HeroService
    return HeroService.get_instance(getattr(ctx, "db", None)).get_hero_equip_slots(uid, hero_id)


def _save_hero_equip_slot(ctx, uid, hero_id, slots):
    from hero_service import HeroService
    HeroService.get_instance(getattr(ctx, "db", None)).save_hero_equip_slots(uid, hero_id, slots)


@operation
class EquipUnloadAllOp(Operation):
    """全部卸下刻印：cs_13018 {hero_id} → sc_13019 {result}。"""

    cmd = 13018
    sc = 13019

    def apply(self, data, ctx):
        hid = int(data.get("hero_id") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).unload_all_equips(ctx, self.uid, hid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13019", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13018 -> 全部卸下刻印 hero={result['hero_id']} "
                f"卸下{result.get('removed', 0)}件 保留锁定{result.get('kept_locked', 0)}件")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid, hero_id=result["hero_id"]) + [DownFrame(self.sc, payload)]


@operation
class EquipSwapOp(Operation):
    """刻印穿戴/替换：cs_13012 {hero_id, equip_id, pos} → sc_13013 {result}。"""

    cmd = 13012
    sc = 13013

    def apply(self, data, ctx):
        uid = self.uid
        hid = int(data.get("hero_id") or 0)
        eid = int(data.get("equip_id") or 0)
        pos = int(data.get("pos") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).swap_equip(ctx, uid, hid, eid, pos)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13013", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13012 -> 刻印穿戴 hero={result['hero_id']} pos={result['pos']} "
                f"equip={result['equip_id']}" +
                (f"（替换掉 {result['replaced']}）" if result.get("replaced") else ""))
        from equip_service import EquipService
        hids = [result["hero_id"]]
        if result.get("prev_owner"):
            hids.append(result["prev_owner"])
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid, hero_id=hids) + [DownFrame(self.sc, payload)]


@operation
class EquipQuickDressOp(Operation):
    """一键快速穿戴刻印：cs_13026 {hero_id, use_equip_list} → sc_13027 {result: repeated use_equip_return}。"""

    cmd = 13026
    sc = 13027

    def apply(self, data, ctx):
        hid = int(data.get("hero_id") or 0)
        uel = data.get("use_equip_list") or []
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).quick_dress_equips(ctx, self.uid, hid, uel)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13027", {"result": result["result"]}) or b"\x08\x00"
        ctx.log(f"cs_13026 -> 一键快速穿戴 hero={result['hero_id']} dressed={len(result['result'])}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(
            ctx, self.uid, hero_id=result.get("affected_heroes")
        ) + [DownFrame(self.sc, payload)]


@operation
class EquipInheritOp(Operation):
    """刻印继承：cs_13052 {inherit_equip_prefab_id, new_equip_id} → sc_13053 {result}。"""

    cmd = 13052
    sc = 13053

    def apply(self, data, ctx):
        eid = int(data.get("new_equip_id") or 0)
        target_pid = int(data.get("inherit_equip_prefab_id") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).inherit_equip(ctx, self.uid, eid, target_pid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13053", {"result": 0}) or b"\x08\x00"
        eid = result.get("equip_id")
        ctx.log(f"cs_13052 -> 刻印继承 equip={eid} {result['old_prefab_id']} -> {result['new_prefab_id']}")
        hero_id = result.get("wearing_hero_id")
        from equip_service import EquipService
        frames = EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid, hero_id=hero_id if hero_id else None)
        import generator as _gen
        p13 = _gen.gen_payload(13009, uid=self.uid, db=ctx.db, equip_id=eid)
        if p13:
            frames.append(DownFrame(13009, p13))
        frames.append(DownFrame(self.sc, payload))
        return frames


# ---------------- 刻印重铸 / 绑定 / 赋能 ----------------
# [Fix by Gemini 3.7-flash] 彻底修复刻印赋能、洗练多条待选栈、定向赋能与放弃预览系统（全量委托 EquipService）

@operation
class RaceRefreshOp(Operation):
    """神系（种族）刷新：cs_13032 {equip_id} → sc_13033 {result, race_preview}。"""

    cmd = 13032
    sc = 13033

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).refresh_race(ctx, self.uid, eid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13033", {
            "result": 0, "race_preview": result["race"]}) or b"\x08\x00"
        ctx.log(f"cs_13032 -> 神系刷新 equip={result['equip_id']} race={result['race']}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class RaceConfirmOp(Operation):
    """神系确认：cs_13034 {equip_id, confirm} → sc_13035 {result}。"""

    cmd = 13034
    sc = 13035

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        confirm = bool(data.get("confirm"))
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).confirm_race(ctx, self.uid, eid, confirm)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13035", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13034 -> 神系确认 equip={result['equip_id']} confirm={result['confirmed']} race={result['race']}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class EquipBindHeroOp(Operation):
    """刻印专属修正者祈生绑定：cs_13046 {equip_id, hero_id} → sc_13047 {result}。"""

    cmd = 13046
    sc = 13047

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        hid = int(data.get("hero_id") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).bind_equip_hero(ctx, self.uid, eid, hid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13047", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13046 -> 刻印角色专属绑定 equip={result['equip_id']} hero={result['hero_id']}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid, hero_id=result["hero_id"]) + [DownFrame(self.sc, payload)]


@operation
class EnchantRefreshOp(Operation):
    """刻印赋能刷新：cs_13028 {equip_id, enchant_slot_id, pool_id, lock_type} → sc_13029 {result, enchant_preview}。"""

    cmd = 13028
    sc = 13029

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        slot_id = int(data.get("enchant_slot_id") or 1)
        try:
            tier = int(data.get("pool_id") or 1)
        except Exception:
            tier = 1
        lock_type = int(data.get("lock_type") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).refresh_enchant(
            ctx, self.uid, eid, slot_id, tier=tier, lock_type=lock_type
        )

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13029", {
            "result": 0,
            "enchant_preview": {"effect_list": result["preview"]}}) or b"\x08\x00"
        ctx.log(f"cs_13028 -> 刷词条 equip={result['equip_id']} slot={result['slot_id']} lock_type={result.get('lock_type', 0)}"
                f" roll={[(e['id'], e['level']) for e in result['preview']]}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class EnchantConfirmOp(Operation):
    """词条确认/放弃单条：cs_13030 {equip_id, enchant_slot_id, confirm, preview_index} → sc_13031 {result}。"""

    cmd = 13030
    sc = 13031

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        slot_id = int(data.get("enchant_slot_id") or 1)
        confirm = bool(data.get("confirm"))
        preview_index = int(data.get("preview_index") or 1)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).confirm_enchant(
            ctx, self.uid, eid, slot_id, confirm=confirm, preview_index=preview_index
        )

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13031", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13030 -> 词条确认 confirm={result['confirmed']} equip={result['equip_id']} slot={result.get('slot_id')}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class EquipGiveUpAllEnchantOp(Operation):
    """放弃全部洗练预览：cs_13044 {equip_id, enchant_slot_id} → sc_13045 {result}。"""

    cmd = 13044
    sc = 13045

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        slot_id = int(data.get("enchant_slot_id") or 1)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).give_up_all_enchant(ctx, self.uid, eid, slot_id)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13045", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13044 -> 放弃全部洗练预览 equip={result['equip_id']} slot={result['slot_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class DirectionalEnchantOp(Operation):
    """定向赋能：cs_13060 {equip_id, enchant_slot_id, seq, id} → sc_13061 {result}。"""

    cmd = 13060
    sc = 13061

    def apply(self, data, ctx):
        eid = int(data.get("equip_id") or 0)
        slot_id = int(data.get("enchant_slot_id") or 1)
        sid = int(data.get("id") or 0)
        seq = int(data.get("seq") or 1)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).directional_enchant(ctx, self.uid, eid, slot_id, sid, seq=seq)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13061", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13060 -> 定向赋能 equip={result['equip_id']} slot={result['slot']} seq={result.get('seq')} +技能{result['id']}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


# ---------------- 宿舍 / 食堂 ----------------
# [2026-09-06 规范化收归] DormLikeQueryOp(58054) / CanteenModeOp(58108) / CanteenTaskSubmitOp(58018)
# 已统一收归至下方 58xxx 家园/后宅/食堂独立区块集中托管。


# ---------------- 73xxx 英雄关系网与好感度系统 (委托给 TrustService) ----------------
from trust_service import TrustService
from oath_service import OathService

@operation
class UnlockRelationNetOp(Operation):
    """73016 解锁人际关系网节点/增益：cs_73016 {id, group_index} → sc_73017 {result}。"""

    cmd = 73016
    sc = 73017

    def validate(self, data, ctx):
        if "id" not in data or "group_index" not in data:
            raise OperationError(1, "缺少关系网节点参数")
        return data

    def apply(self, data, ctx):
        svc = TrustService.get_instance()
        return svc.unlock_relation_net(ctx, self.uid, int(data["id"]), int(data["group_index"]))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_73017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_73016 -> 解锁关系网 hero={result['hero_id']} tier={result['tier']} group={result['group_index']}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class UnlockTrustOp(Operation):
    """73020 解锁好感度/心链：cs_73020 {hero_id} → sc_73021 {result, mood}。"""

    cmd = 73020
    sc = 73021

    def validate(self, data, ctx):
        if "hero_id" not in data:
            raise OperationError(1, "缺少 hero_id")
        return data

    def apply(self, data, ctx):
        svc = TrustService.get_instance()
        try:
            return svc.unlock_trust(ctx, self.uid, int(data["hero_id"]))
        except ValueError as e:
            raise OperationError(2, str(e))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_73021", {"result": 0, "mood": result["mood"]}) or b"\x08\x00"
        ctx.log(f"cs_73020 -> 解锁好感度 hero={result['hero_id']} mood={result['mood']}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class UpgradeTrustLevelOp(Operation):
    """73014 提升好感度等级：cs_73014 {hero_id} → sc_73015 {result, reward_list}。"""

    cmd = 73014
    sc = 73015

    def validate(self, data, ctx):
        if "hero_id" not in data:
            raise OperationError(1, "缺少 hero_id")
        return data

    def apply(self, data, ctx):
        svc = TrustService.get_instance()
        try:
            return svc.upgrade_trust_level(ctx, self.uid, int(data["hero_id"]))
        except ValueError as e:
            raise OperationError(2, str(e))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_73015", {"result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log(f"cs_73014 -> 提升好感度等级 hero={result['hero_id']} → Lv{result['new_level']}")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class SendTrustItemOp(Operation):
    """73012 赠送好感度礼物：cs_73012 {hero_id, item_list} → sc_73013 {result}。"""

    cmd = 73012
    sc = 73013

    def validate(self, data, ctx):
        if "hero_id" not in data or "item_list" not in data:
            raise OperationError(1, "缺少礼物赠送参数")
        return data

    def apply(self, data, ctx):
        svc = TrustService.get_instance()
        try:
            return svc.send_trust_item(ctx, self.uid, int(data["hero_id"]), data.get("item_list", []))
        except ValueError as e:
            raise OperationError(2, str(e))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_73013", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_73012 -> 赠送好感度礼物 hero={result['hero_id']} 心情={result['mood']}(x{result['rate']}) +{result['exp_added']}exp")
        return _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id")) + [DownFrame(self.sc, payload)]


@operation
class ExChangeTrustItemOp(Operation):
    """73010 置换好感度礼物：cs_73010 {use_item_list, reward_item_list} → sc_73011 {result}。"""

    cmd = 73010
    sc = 73011

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        svc = TrustService.get_instance()
        return svc.exchange_trust_item(ctx, self.uid, data.get("use_item_list", []), data.get("reward_item_list", []))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_73011", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_73010 -> 置换好感度礼物成功")
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class RelationStoryRewardOp(Operation):
    """73022 领取关系网羁绊故事奖励：cs_73022 {id} → sc_73023 {result, reward_list}。"""

    cmd = 73022
    sc = 73023

    def validate(self, data, ctx):
        if "id" not in data:
            raise OperationError(1, "缺少故事 ID")
        return data

    def apply(self, data, ctx):
        svc = TrustService.get_instance()
        return svc.claim_relation_story_reward(ctx, self.uid, int(data["id"]))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_73023", {"result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log("cs_73022 -> 领取羁绊故事奖励 id=%s%s"
                % (result["id"], "（已领过，空奖励）" if result.get("already") else ""))
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class UpgradeComboSkillOp(Operation):
    """73018 连携技能升级：cs_73018 {cooperate_unique_skill_id} → sc_73019 {result} + 推 sc_73003 更新帧。

    调用点：game/action/comboskillaction.lua:19（档案-羁绊-连携页 herotrammelsview 升级按钮）
        SendWithLoadingNew(73018, {cooperate_unique_skill_id = comboId}, 73019, OnUpgradeComboSkillLevelBack)
    客户端先本地校验条件再发包；服务端仍严格校验（条件未达/满级 → OperationError 拦截）。
    sc_73003 必须先于 sc_73019 下发：73019 成功回调弹 Toast 时读取的是 73003 更新后的等级。
    """

    cmd = 73018
    sc = 73019

    def validate(self, data, ctx):
        if "cooperate_unique_skill_id" not in data:
            raise OperationError(1, "缺少 cooperate_unique_skill_id")
        return data

    def apply(self, data, ctx):
        svc = TrustService.get_instance()
        try:
            return svc.upgrade_combo_skill(ctx, self.uid, int(data["cooperate_unique_skill_id"]))
        except ValueError as e:
            raise OperationError(2, str(e))

    def respond(self, result, data, ctx):
        svc = TrustService.get_instance()
        rec = svc.build_combo_skill_rec(ctx.db, self.uid, result["skill_id"])
        push_payload = ctx.codec_encode("sc_73003", {"skill": rec}) or b""
        resp_payload = ctx.codec_encode("sc_73019", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_73018 -> 连携技能 {result['skill_id']} 升级至 Lv.{result['new_level']}（含 sc_73003 更新帧）")
        return [DownFrame(73003, push_payload), DownFrame(self.sc, resp_payload)]


# ---------------- 145xx 誓约（Oath / Wedding）核心系统 ----------------

@operation
class OathOp(Operation):
    """14514 缔结誓约 / 保存合影相片：cs_14514 {hero_id, picture_url?} → sc_14515 {result, time}。"""

    cmd = 14514
    sc = 14515

    def validate(self, data, ctx):
        if "hero_id" not in data:
            raise OperationError(1, "缺少 hero_id")
        return data

    def apply(self, data, ctx):
        svc = OathService.get_instance()
        try:
            return svc.handle_oath_or_photo(ctx, self.uid, data)
        except Exception as e:
            raise OperationError(2, str(e))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14515", {"result": result.get("result", 0), "time": result.get("time", 0)}) or b"\x08\x00"
        frames = [DownFrame(self.sc, payload)]
        mode = result.get("mode")
        # 无论结缔新誓约、重复进入还是更新合影照片，均推送最新的 14501 保证双端强一致
        if mode in ("oath", "oath_already", "photo"):
            svc = OathService.get_instance()
            p14501 = svc.build_14501_payload(ctx.db, self.uid)
            if p14501:
                frames.append(DownFrame(14501, p14501))
            if mode in ("oath", "oath_already"):
                p14503 = svc.build_14503_payload(ctx.db, self.uid)
                if p14503:
                    frames.append(DownFrame(14503, p14503))
                frames.extend(_refresh_frames(ctx, self.uid, hero_id=data.get("hero_id")))
        return frames


@operation
class SubmitOathTaskOp(Operation):
    """14512 提交誓约任务并晋升等级：cs_14512 {id: [task_id, ...]} → sc_14513 {result, reward_list}。"""

    cmd = 14512
    sc = 14513

    def validate(self, data, ctx):
        if "id" not in data:
            raise OperationError(1, "缺少任务 ID")
        return data

    def apply(self, data, ctx):
        svc = OathService.get_instance()
        return svc.handle_submit_task(ctx, self.uid, data.get("id", []))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14513", {"result": result.get("result", 0), "reward_list": result.get("reward_list", [])}) or b"\x08\x00"
        frames = [DownFrame(self.sc, payload)]
        # 推送 14503 任务变更与 14501 誓约等级变更
        svc = OathService.get_instance()
        p14501 = svc.build_14501_payload(ctx.db, self.uid)
        if p14501:
            frames.append(DownFrame(14501, p14501))
        p14503 = svc.build_14503_payload(ctx.db, self.uid)
        if p14503:
            frames.append(DownFrame(14503, p14503))
        return frames


@operation
class SetHeroNickNameOp(Operation):
    """14516 定制誓约个性爱称：cs_14516 {hero_id, nick} → sc_14517 {result}。"""

    cmd = 14516
    sc = 14517

    def validate(self, data, ctx):
        if "hero_id" not in data:
            raise OperationError(1, "缺少 hero_id")
        return data

    def apply(self, data, ctx):
        svc = OathService.get_instance()
        return svc.handle_set_nickname(ctx, self.uid, int(data["hero_id"]), str(data.get("nick", "")))

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14517", {"result": result.get("result", 0)}) or b"\x08\x00"
        frames = [DownFrame(self.sc, payload)]
        svc = OathService.get_instance()
        p14501 = svc.build_14501_payload(ctx.db, self.uid)
        if p14501:
            frames.append(DownFrame(14501, p14501))
        return frames


@operation
class ReadPlotStoryOp(Operation):
    """14510 阅读誓约物语与专属剧情：cs_14510 {oath_plot: {hero_id, text_list}} → sc_14511 {result}。"""

    cmd = 14510
    sc = 14511

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        svc = OathService.get_instance()
        plot = data.get("oath_plot") or {}
        hid = int(plot.get("hero_id") or 0)
        t_list = plot.get("text_list") or []
        return svc.handle_read_plot(ctx, self.uid, hid, t_list)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14511", {"result": result.get("result", 0)}) or b"\x08\x00"
        frames = [DownFrame(self.sc, payload)]
        svc = OathService.get_instance()
        p14501 = svc.build_14501_payload(ctx.db, self.uid)
        if p14501:
            frames.append(DownFrame(14501, p14501))
        return frames


# ---------------- 芯片系统 (50xxx 统一委托给 ChipService) ----------------

@operation
class ChipUnlockOp(Operation):
    """50002 解锁芯片/芯片管理器/角色芯片/英雄AI芯片：cs_50002 {id} → sc_50003 {result: 0}。"""

    cmd = 50002
    sc = 50003

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        chip_id = int(data.get("id") or 0)
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).unlock_any_chip(ctx, self.uid, chip_id)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50003", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50002 -> 芯片解锁 cat={result.get('cat')} chip_id={result.get('chip_id')}")
        return _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class ChipHeroEnableOp(Operation):
    """50018 装配/卸下角色专属芯片：cs_50018 {hero_id, slot_id, secondary} → sc_50019 {result: 0}。"""

    cmd = 50018
    sc = 50019

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        hero_id = int(data.get("hero_id") or 0)
        slot_id = int(data.get("slot_id") or 0)
        sec_chip = int(data.get("secondary") or 0)
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).enable_hero_chip(ctx, self.uid, hero_id, slot_id, sec_chip)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50019", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50018 -> 角色芯片装配 hero={result['hero_id']} slot={result['slot_id']} chip={result['secondary']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid, hero_id=result.get("hero_id"))


@operation
class ChipMimirEnableOp(Operation):
    """50006 管理喵装配/卸下次级芯片：cs_50006 {kernel_id, secondary_id, oper: 1|2} → sc_50007 {result: 0}。"""

    cmd = 50006
    sc = 50007

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        kid = int(data.get("kernel_id") or 0)
        sid = int(data.get("secondary_id") or 0)
        oper = int(data.get("oper") or 1)
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).enable_mimir_chip(ctx, self.uid, kid, sid, oper)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50007", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50006 -> 管理喵芯片装配 kernel={result['kernel_id']} chip={result['secondary_id']} oper={result['oper']} now={result['chips']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class ChipMimirResetOp(Operation):
    """50016 重置/清空管理喵次级芯片：cs_50016 {kernel_id} → sc_50017 {result: 0}。"""

    cmd = 50016
    sc = 50017

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        kid = int(data.get("kernel_id") or 0)
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).reset_mimir_chip(ctx, self.uid, kid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50016 -> 管理喵芯片清空 kernel={result['kernel_id']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class ChipSchemeSaveOp(Operation):
    """50008 保存管理喵芯片方案：cs_50008 {id, name, secondary} → sc_50009 {result: 0}。"""

    cmd = 50008
    sc = 50009

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        pid = int(data.get("id") or 0)
        name = str(data.get("name") or "")
        sec = data.get("secondary") or []
        if isinstance(sec, (int, str)):
            sec = [int(sec)]
        else:
            sec = [int(x) for x in sec]
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).save_proposal(ctx, self.uid, pid, name, sec)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50009", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50008 -> 管理喵方案保存 id={result['id']} name={result['name']} sec={result['secondary']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class ChipSchemeDeleteOp(Operation):
    """50010 删除管理喵芯片方案：cs_50010 {id} → sc_50011 {result: 0}。"""

    cmd = 50010
    sc = 50011

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        pid = int(data.get("id") or 0)
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).delete_proposal(ctx, self.uid, pid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50011", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50010 -> 管理喵方案删除 id={result['id']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class ChipSchemeRenameOp(Operation):
    """50012 重命名管理喵芯片方案：cs_50012 {id, name} → sc_50013 {result: 0}。"""

    cmd = 50012
    sc = 50013

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        pid = int(data.get("id") or 0)
        name = str(data.get("name") or "")
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).rename_proposal(ctx, self.uid, pid, name)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50013", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50012 -> 管理喵方案重命名 id={result['id']} name={result['name']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class ChipSchemeEnableOp(Operation):
    """50014 使用管理喵芯片方案：cs_50014 {kernel_chip_id, proposal_id} → sc_50015 {result: 0}。"""

    cmd = 50014
    sc = 50015

    def validate(self, data, ctx):
        return data

    def apply(self, data, ctx):
        ctx.uid = self.uid
        kid = int(data.get("kernel_chip_id") or 0)
        pid = int(data.get("proposal_id") or 0)
        from chip_service import ChipService
        return ChipService.get_instance(ctx.db).apply_proposal(ctx, self.uid, kid, pid)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_50015", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_50014 -> 管理喵方案应用 kernel={result['kernel_chip_id']} proposal={result['proposal_id']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


# ---------------- 客户端在等但此前无人实现的 cmd（tools/client_expect.py 扫出来的） ----------------

# 碎片解锁角色所需数量：GameSetting.unlock_hero_need.value = {20, 30, 50}
# 按 heroCfg.rare 索引（Lua 1-based），对应库里 hero_cfg.unlock_star 的 100/200/300。
_UNLOCK_HERO_NEED = {100: 20, 200: 30, 300: 50}


@operation
class UnlockHeroByPieceOp(Operation):
    """碎片解锁角色：cs_14016 {id} → sc_14017 {result: 0}。

    调用点：game/views/newhero/heroinfoview.lua:102（角色信息页「获取」按钮）
        if GetHeroPiece() < GameSetting.unlock_hero_need.value[heroCfg.rare] then 弹提示
        else SendWithLoadingNew(14016, {id=...}, 14017, OnUnlockHero)
    回调 OnUnlockHero 只读 result：isSuccess 后调 HeroAction.UnlockHeroSuccess + 跳 obtainView。

    注意这是 SendWithLoadingNew（带 loading 遮罩）——不回包就是永久转圈。
    而且不能只回 result=0 不落库：客户端会认为已解锁，下次登录 sc_14009 又说没有，数据打架。
    """

    cmd = 14016
    sc = 14017

    def validate(self, data, ctx):
        if not (data.get("id") or data.get("hero_id")):
            raise OperationError(2, "缺少角色 id")
        return data

    def apply(self, data, ctx):
        uid = self.uid
        hid = int(data.get("id") or data.get("hero_id"))

        cfg = ctx.db.query("SELECT unlock_star FROM hero_cfg WHERE hero_id=?", (hid,))
        if not cfg:
            cfg = ctx.db.query("SELECT unlock_star FROM draw_hero_pool WHERE hero_id=?", (hid,))
        star = int((cfg[0]["unlock_star"] if cfg else 300) or 300)
        need = _UNLOCK_HERO_NEED.get(star, 50)

        hrow = ctx.db.query("SELECT unlock FROM hero WHERE uid=? AND id=?", (uid, hid))
        if hrow and (hrow[0]["unlock"] or 0) == 1:
            return {"hero_id": hid, "already": True, "need": 0, "left": None}

        prow = ctx.db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hid))
        have = int((prow[0]["num"] if prow else 0) or 0)
        if have < need:
            raise OperationError(3, f"碎片不足: hero={hid} 需{need} 有{have}")

        ctx.db.execute("UPDATE hero_piece SET num=num-? WHERE uid=? AND hero_id=?",
                       (need, uid, hid))
        now = int(time.time())
        if hrow:
            ctx.db.execute("UPDATE hero SET unlock=1, update_ts=? WHERE uid=? AND id=?",
                           (now, uid, hid))
        else:
            ctx.db.execute(
                "INSERT INTO hero (uid, id, level, star, exp, skill_list, unlock, update_ts) "
                "VALUES (?, ?, 1, ?, 0, '[]', 1, ?)", (uid, hid, star, now))
        return {"hero_id": hid, "already": False, "need": need, "left": have - need}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_14017", {"result": 0}) or b"\x08\x00"
        if result.get("already"):
            ctx.log(f"cs_14016 -> 角色 {result['hero_id']} 已解锁，幂等回 result=0")
        else:
            ctx.log(f"cs_14016 -> 碎片解锁角色 {result['hero_id']}（扣 {result['need']} 片，余 {result['left']}）")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid,
                                                              hero_id=result["hero_id"])


# [2026-09-06 外围模块迁移] SetTextLanguageOp(12100 设置文本语言) 已迁至 peripheral_service.py。


# ---------------- MomoTalk 随身通讯（# [Fix by Gemini 3.7-flash]） ----------------

@operation
class MomoTalkAddBreakOp(Operation):
    """[Fix by Gemini 3.7-flash] MomoTalk 对话推进到新断点：
    cs_91014 {session_id, content_id} → sc_91015 {result}。"""

    cmd = 91014
    sc = 91015

    def apply(self, data, ctx):
        ctx.uid = self.uid
        s_id = int(data.get("session_id") or 0)
        c_id = int(data.get("content_id") or 0)
        if ctx.db and s_id and c_id:
            ctx.db.save_momotalk_break(self.uid, s_id, c_id)
        return {"session_id": s_id, "content_id": c_id}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_91015", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_91014 -> MomoTalk 添加断点 session={result['session_id']} content={result['content_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class MomoTalkFinishBreakOp(Operation):
    """[Fix by Gemini 3.7-flash] MomoTalk 完成对话断点/确认分支选项：
    cs_91002 {session_id, content_id, state} → sc_91003 {result}。"""

    cmd = 91002
    sc = 91003

    def apply(self, data, ctx):
        ctx.uid = self.uid
        s_id = int(data.get("session_id") or 0)
        c_id = int(data.get("content_id") or 0)
        state = int(data.get("state") or 0)
        if ctx.db and s_id and c_id:
            ctx.db.finish_momotalk_break(self.uid, s_id, c_id, state)
        return {"session_id": s_id, "content_id": c_id, "state": state}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_91003", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_91002 -> MomoTalk 完成断点 session={result['session_id']} content={result['content_id']} state={result['state']}")
        return [DownFrame(self.sc, payload)]


@operation
class MomoTalkSetReadOp(Operation):
    """[Fix by Gemini 3.7-flash] MomoTalk 标记会话已读：
    cs_91006 {session_id} → sc_91007 {result}。"""

    cmd = 91006
    sc = 91007

    def apply(self, data, ctx):
        ctx.uid = self.uid
        s_id = int(data.get("session_id") or 0)
        if ctx.db and s_id:
            ctx.db.set_momotalk_read(self.uid, s_id)
        return {"session_id": s_id}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_91007", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_91006 -> MomoTalk 设为已读 session={result['session_id']}")
        return [DownFrame(self.sc, payload)]


# [2026-09-06 外围模块迁移] MomoTalkSetFrameOp(91016) 删除——原为 flag=0 死代码，
# 遮蔽了 middleware.h_91016 的正确实现（91xxx 族需 flag=1+连接级 srv），删除后 h_91016 接管。
# [2026-09-06 外围模块迁移] ChangeChatBubbleOp(32120 更换聊天气泡) 已迁至 peripheral_service.py。


# ======================================================================
# 家园/后宅/游园街（BackHome / Dorm / Canteen，58xxx 协议族）
# 架构规范化重构：轻量托管转发层（Delegation Pattern）
# 控制器统一声明在 operations.py，业务逻辑委托 BackHomeService 单例。
# ======================================================================

@operation
class BackHomeGetDetailOp(Operation):
    """[Fix by Gemini 3.7-flash] 进入家园系统拉取全量详情：
    cs_58002 {nothing} → sc_58003 {result: 0, canteens, ingredients, food, dorms, exhibition_id, template, furnitures}。"""

    cmd = 58002
    sc = 58003

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_detail(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58003", result)
            if not payload:
                from generator import generate
                frames = generate(58003, {"index": 0, "server_idx": 0, "cmd": 58003, "len": 0, "payload": b""}, db=ctx.db)
                if frames:
                    return [DownFrame(self.sc, frames[0][11:])]
                payload = b"\x08\x00"
            ctx.log(f"cs_58002 -> 进入家园系统详情 canteens={len(result.get('canteens', []))} dorms={len(result.get('dorms', []))} furnitures={len(result.get('furnitures', []))}")
            return [DownFrame(self.sc, payload)]


@operation
class BatchSendTaskDispatchOp(Operation):
    """[Fix by Gemini 3.7-flash] 委托一键派遣：
    cs_58100 {architecture_id, entrust_list} → sc_58101 {result: 0}。"""

    cmd = 58100
    sc = 58101

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().batch_send_task_dispatch(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = (result or {}).get("result", 0)
        payload = ctx.codec_encode("sc_58101", {"result": res_code}) or b"\x08\x00"
        ctx.log(f"cs_58100 -> 批量委托派遣 res={res_code}")
        if res_code != 0:
            return [DownFrame(self.sc, payload)]
        from operations import _refresh_frames
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class SendTaskDispatchOp(Operation):
    """[Fix by Gemini 3.7-flash] 单个委托派遣/撤回：
    cs_58102 {architecture_id, pos, hero_list, duration} → sc_58103 {result: 0}。"""

    cmd = 58102
    sc = 58103

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().send_task_dispatch(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = (result or {}).get("result", 0)
        payload = ctx.codec_encode("sc_58103", {"result": res_code}) or b"\x08\x00"
        ctx.log(f"cs_58102 -> 单个委托操作 pos={data.get('pos')} res={res_code}")
        if res_code != 0:
            return [DownFrame(self.sc, payload)]
        from operations import _refresh_frames
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class RefreshEntrustOp(Operation):
    """[Fix by Gemini 3.7-flash] 刷新委托：
    cs_58012 {architecture_id, pos} → sc_58013 {result: 0, entrust}。"""

    cmd = 58012
    sc = 58013

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().refresh_entrust(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58013", {
                "result": 0,
                "entrust": {
                    "pos": result["pos"],
                    "id": result["task_id"],
                    "hero_list": [],
                    "tags": [],
                    "num_max": 3,
                    "refresh_times": result["refresh_times"],
                    "start_time": 0,
                    "duration": 1200
                }
            }) or b"\x08\x00"
            ctx.log(f"cs_58012 -> 刷新委托 pos={result['pos']} task_id={result['task_id']} (B/A/S品级) refresh_times={result['refresh_times']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class UnLockEntrustOp(Operation):
    """[Fix by Gemini 3.7-flash] 解锁委托槽位：
    cs_58024 {pos} → sc_58025 {result: 0, entrust}。"""

    cmd = 58024
    sc = 58025

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().unlock_entrust(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58025", {
                "result": 0,
                "entrust": {
                    "pos": result["pos"],
                    "id": result["task_id"],
                    "hero_list": [],
                    "tags": [],
                    "num_max": 3,
                    "refresh_times": 0,
                    "start_time": 0,
                    "duration": 1200
                }
            }) or b"\x08\x00"
            ctx.log(f"cs_58024 -> 解锁委托槽位 pos={result['pos']} task_id={result['task_id']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class BackHomeGetVisitOp(Operation):
    """[Fix by Gemini 3.7-flash] 进入后宅房间拉取拜访数据与礼物：
    cs_58058 {} → sc_58059 {result: 0, visited_user_list: [], is_have_gift: false}。"""

    cmd = 58058
    sc = 58059

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_visit(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58059", result) or b"\x08\x00"
            ctx.log("cs_58058 -> 拉取后宅拜访数据")
            return [DownFrame(self.sc, payload)]


@operation
class BackHomeGetRewardOp(Operation):
    """[Fix by Gemini 3.7-flash] 领取后宅被拜访礼物奖励：
    cs_58060 {} → sc_58061 {result: 0, be_visited_reward_list: []}。"""

    cmd = 58060
    sc = 58061

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().get_reward(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58061", result) or b"\x08\x00"
            ctx.log("cs_58060 -> 领取后宅被拜访礼物奖励")
            return [DownFrame(self.sc, payload)]


@operation
class BackHomeHeroLockOp(Operation):
    """[Fix by Gemini 3.7-flash] 锁定/解锁后宅英雄：
    cs_58218 {hero_id, type} → sc_58219 {result: 0}。"""

    cmd = 58218
    sc = 58219

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().hero_lock(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = (result or {}).get("result", 0)
        payload = ctx.codec_encode("sc_58219", {"result": res_code}) or b"\x08\x00"
        ctx.log(f"cs_58218 -> 锁定/解锁后宅英雄 hero={result.get('hero_id')} type={result.get('type')} res={res_code}")
        return [DownFrame(self.sc, payload)]


@operation
class SetFurListInMapOp(Operation):
    """[Fix by Gemini 3.7-flash] 保存宿舍房间3D家具摆放布局：
    cs_58010 {architecture_id, furniture_layout, picture_link} → sc_58011 {result: 0}。"""

    cmd = 58010
    sc = 58011

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().set_fur_list_in_map(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58011", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58010 -> 保存宿舍房间家具布局 dorm={result['architecture_id']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class UnLockDormArchitectureOp(Operation):
    """[Fix by Gemini 3.7-flash] 解锁新宿舍房间：
    cs_58130 {architecture_id, pos_id} → sc_58131 {result: 0} + sc_17009 (扣除小窝资金)。"""

    cmd = 58130
    sc = 58131

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().unlock_dorm_architecture(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58131", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58130 -> 解锁宿舍房间 dorm={result['architecture_id']} pos={result['pos_id']} (扣小窝资金 800)")
            from operations import _refresh_frames
            frames = [DownFrame(self.sc, payload)]
            rem = result.get("rem_mat")
            if rem is not None:
                mat_payload = ctx.codec_encode("sc_17009", {"material_list": [{"id": 41701, "num": rem}]}) or b""
                if mat_payload:
                    frames.append(DownFrame(17009, mat_payload))
            return frames + _refresh_frames(ctx, self.uid)


@operation
class DeployHeroInRoomOp(Operation):
    """[Fix by Gemini 3.7-flash] 安排英雄入住房间：
    cs_58132 {architecture_id, hero_id: [uint32]} → sc_58133 {result: 0}。"""

    cmd = 58132
    sc = 58133

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().deploy_hero_in_room(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = result.get("result", 0) if isinstance(result, dict) else 0
        payload = ctx.codec_encode("sc_58133", {"result": res_code}) or b"\x08\x00"
        ctx.log(f"cs_58132 -> 安排英雄入住房间 dorm={result['architecture_id']} heroes={result['hero_ids']} res={res_code}")
        if res_code != 0:
            return [DownFrame(self.sc, payload)]
        from operations import _refresh_frames
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class RecallHeroInPrivateDormOp(Operation):
    """[Fix by Gemini 3.7-flash] 召回私人宿舍英雄：
    cs_58134 {architecture_id, hero_id} → sc_58135 {result: 0}。"""

    cmd = 58134
    sc = 58135

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().recall_hero_in_private_dorm(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58135", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58134 -> 召回私人宿舍英雄 dorm={result['architecture_id']} hero={result['hero_id']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class GiftFurToHeroOp(Operation):
    """[Fix by Gemini 3.7-flash] 赠送专属家具（提升宿舍好感经验）：
    cs_58136 {hero_id, furniture: [item_net_rec]} → sc_58137 {result: 0}。"""

    cmd = 58136
    sc = 58137

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().gift_fur_to_hero(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58137", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58136 -> 赠送专属家具 hero={result['hero_id']} items={result.get('furniture')}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class GiftFoodToHeroOp(Operation):
    """[Fix by Gemini 3.7-flash] 给英雄喂食（恢复疲劳度至 120）：
    cs_58138 {type, hero_id} → sc_58139 {result: 0, fatigue_list}。"""

    cmd = 58138
    sc = 58139

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().gift_food_to_hero(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58139", {
                "result": 0,
                "fatigue_list": result.get("fatigue_list", [])
            }) or b"\x08\x00"
            ctx.log(f"cs_58138 -> 给英雄喂食 type={result['type']} hero={result['hero_id']} 恢复数={len(result.get('fatigue_list', []))}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class SetHeroSkinOp(Operation):
    """[Fix by Gemini 3.7-flash] 更换后宅英雄皮肤：
    cs_58126 {hero_id, skin_id, source} → sc_58127 {result: 0}。"""

    cmd = 58126
    sc = 58127

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().set_hero_skin(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58127", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58126 -> 更换后宅英雄皮肤 hero={result['hero_id']} skin={result['skin_id']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class RevisePrivateDormPosOp(Operation):
    """[Fix by Gemini 3.7-flash] 修改私人宿舍门牌位置：
    cs_58146 {dorm_pos_list} → sc_58147 {result: 0}。"""

    cmd = 58146
    sc = 58147

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().revise_private_dorm_pos(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58147", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58146 -> 修改私人宿舍门牌位置 count={len(result.get('dorm_pos_list', []))}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class SetCharacterJobOp(Operation):
    """[Fix by Gemini 3.7-flash] 设置餐厅岗位安排（厨师/服务员/收银）：
    cs_58104 {architecture_id, type, hero_id} → sc_58105 {result: 0}。"""

    cmd = 58104
    sc = 58105

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().set_character_job(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = (result or {}).get("result", 0)
        payload = ctx.codec_encode("sc_58105", {"result": res_code}) or b"\x08\x00"
        ctx.log(f"cs_58104 -> 设置食堂工作安排 ctype={result.get('type')} hero={result.get('hero_id')} res={res_code}")
        if res_code != 0:
            return [DownFrame(self.sc, payload)]
        from operations import _refresh_frames
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class ReceiveCanteenAutoAwardOp(Operation):
    """58106 领取食堂营业收入：cs_58106 {architecture_id} → sc_58107 {result, earnings}。

    earnings 来自 db.settle_canteen_earnings() 的 lazy 结算（_calculate_canteen_dishes：
    按菜品推进 sold，受岗位体力/成本耗时/14000 上限停止约束），增量持久化在
    meta.pending_earnings。此前读不存在的 last_receive_earnings_time 列 → elapsed 恒 0 → 恒领 0。"""

    cmd = 58106
    sc = 58107

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().receive_canteen_auto_award(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_58107", {
            "result": 0,
            "earnings": result["earnings"]
        }) or b"\x08\x00"
        ctx.log(f"cs_58106 -> 领取食堂收入 earnings={result['earnings']}")
        frames = [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)
        # 实时原子下发疲劳度更新（sc_58027），客户端收到立即刷新打工角色体力条，无需重新登录
        try:
            from backhome_service import BackHomeService
            p58027 = BackHomeService.get_instance().get_fatigue_push_payload(self.uid, ctx.db)
            if p58027:
                frames.append(DownFrame(58027, p58027))
        except Exception as e:
            ctx.log(f"cs_58106 下发 sc_58027 异常: {e}")
        return frames


@operation
class SendSignFoodOp(Operation):
    """[Fix by Gemini 3.7-flash] 上架/下架/修改在售招牌菜品：
    cs_58114 {architecture_id, food_id, sell_num} → sc_58115 {result: 0}。"""

    cmd = 58114
    sc = 58115

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().send_sign_food(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58115", {"result": 0}) or b"\x08\x00"
            action_str = "下架" if result["sell_num"] <= 0 else f"上架/设置{result['sell_num']}份"
            ctx.log(f"cs_58114 -> 招牌菜调整: food={result['food_id']} ({action_str})")
            return [DownFrame(self.sc, payload)]


@operation
class CanteenFurUpgradeOp(Operation):
    """[Fix by Gemini 3.7-flash] 升级食堂设施厨具：
    cs_58116 {uid} → sc_58117 {result: 0}。"""

    cmd = 58116
    sc = 58117

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().canteen_fur_upgrade(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = int((result or {}).get("result") or 0)
        payload = ctx.codec_encode("sc_58117", {"result": res_code}) or b"\x08\x00"
        ctx.log(f"cs_58116 -> 升级食堂设施 result={res_code}")
        from operations import _refresh_frames
        frames = [DownFrame(self.sc, payload)]
        if res_code == 0:
            frames.extend(_refresh_frames(ctx, self.uid))
        return frames


@operation
class CanteenManualSettlementOp(Operation):
    """[Fix by Gemini 3.7-flash] 手动烹饪做菜结算：
    cs_58120 {architecture_id, oper_list} → sc_58121 {result: 0}。"""

    cmd = 58120
    sc = 58121

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().canteen_manual_settlement(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = int((result or {}).get("result") or 0)
        payload = ctx.codec_encode("sc_58121", {"result": res_code}) or b"\x08\x00"
        ctx.log(f"cs_58120 -> 手动做菜结算完成 result={res_code} income={(result or {}).get('income', 0)}")
        from operations import _refresh_frames
        frames = [DownFrame(self.sc, payload)]
        if res_code == 0:
            frames.extend(_refresh_frames(ctx, self.uid))
        return frames


@operation
class SaveFurTemplateOp(Operation):
    """[Fix by Gemini 3.7-flash] 保存预设家具方案模板：
    cs_58040 {id, type, name, architecture_id, furniture_pos_list, pos, host_info} → sc_58041 {result: 0}。"""

    cmd = 58040
    sc = 58041

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().save_fur_template(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58041", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58040 -> 保存家具模板 id={result['id']} name={result['name']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class ReviseFurTemplateNameOp(Operation):
    """[Fix by Gemini 3.7-flash] 重命名家具模板：
    cs_58142 {id, name} → sc_58143 {result: 0}。"""

    cmd = 58142
    sc = 58143

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().revise_fur_template_name(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58143", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58142 -> 重命名家具模板 id={data.get('id')} name={data.get('name')}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class DeleteFurTemplateOp(Operation):
    """[Fix by Gemini 3.7-flash] 删除家具模板：
    cs_58144 {id} → sc_58145 {result: 0}。"""

    cmd = 58144
    sc = 58145

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().delete_fur_template(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58145", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58144 -> 删除家具模板 id={data.get('id')}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class AskFurTemplateExhibitOp(Operation):
    """[Fix by Gemini 3.7-flash] 请求展示家具模板列表：
    cs_58148 {type} → sc_58149 {result: 0, exhibition_brief}。"""

    cmd = 58148
    sc = 58149

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().ask_fur_template_exhibit(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58149", {"result": 0, "exhibition_brief": []}) or b"\x08\x00"
            ctx.log("cs_58148 -> 请求展示模板列表")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class SetFurTemplateExhibitOp(Operation):
    """[Fix by Gemini 3.7-flash] 设置展示家具模板：
    cs_58152 {architecture_id, picture_link} → sc_58153 {result: 0}。"""

    cmd = 58152
    sc = 58153

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().set_fur_template_exhibit(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58153", {"result": 0}) or b"\x08\x00"
            ctx.log("cs_58152 -> 设置展示家具模板")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class SetFurTemplateCanSaveOp(Operation):
    """[Fix by Gemini 3.7-flash] 设置家具模板是否允许他人保存：
    cs_58158 {is_open} → sc_58159 {result: 0}。"""

    cmd = 58158
    sc = 58159

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().set_fur_template_can_save(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58159", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58158 -> 设置模板保存权限 is_open={data.get('is_open')}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class WatchTheatreOp(Operation):
    """[Fix by Gemini 3.7-flash] 观看生日小剧场：
    cs_58128 {theatrical_id} → sc_58129 {result: 0}。"""

    cmd = 58128
    sc = 58129

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().watch_theatre(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58129", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58128 -> 观看小剧场 id={result['theatrical_id']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class SettlementRhythmGameOp(Operation):
    """[Fix by Gemini 3.7-flash] 音游小游戏结算：
    cs_58154 {stage_id, percentage_complete, hero_id} → sc_58155 {result: 0, reward_list, fatigue}。"""

    cmd = 58154
    sc = 58155

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().settlement_rhythm_game(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = int((result or {}).get("result") or 0)
        fatigue = int((result or {}).get("fatigue") if (result and "fatigue" in result) else 120)
        reward_list = (result or {}).get("reward_list") or []
        payload = ctx.codec_encode("sc_58155", {
            "result": res_code,
            "reward_list": reward_list,
            "fatigue": fatigue
        }) or b"\x08\x00"
        ctx.log(f"cs_58154 -> 音游小游戏结算 stage={data.get('stage_id')} fatigue={fatigue} reward={reward_list}")
        from operations import _refresh_frames
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class DeployHeroInCampOp(Operation):
    """[Fix by Gemini 3.7-flash] 虫虫/偶像养成营英雄派遣：
    cs_58174 {hero_pos_list} → sc_58175 {result: 0}。"""

    cmd = 58174
    sc = 58175

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().deploy_hero_in_camp(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_58175", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_58174 -> 养成营安排英雄入营")
        frames = [DownFrame(self.sc, payload)]
        try:
            from backhome_service import BackHomeService
            p58169 = BackHomeService.get_instance().get_idol_trainee_overview_payload(self.uid, ctx.db)
            if p58169:
                frames.append(DownFrame(58169, p58169))
        except Exception as e:
            ctx.log(f"cs_58174 附带 sc_58169 异常: {e}")
        return frames


@operation
class TrainHeroPropertyOp(Operation):
    """[Fix by Gemini 3.7-flash] 58166 训练室培养英雄属性：
    cs_58166 {hero_id, attribute_index} → sc_58167 {result, fatigue, attribute_value}。"""

    cmd = 58166
    sc = 58167

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().train_hero_property(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        res_code = int((result or {}).get("result") or 0)
        fatigue = int((result or {}).get("fatigue") if (result and "fatigue" in result) else 0)
        attr_val = int((result or {}).get("attribute_value") if (result and "attribute_value" in result) else 0)
        payload = ctx.codec_encode("sc_58167", {
            "result": res_code,
            "fatigue": fatigue,
            "attribute_value": attr_val
        }) or b"\x08\x00"
        ctx.log(f"cs_58166 -> 训练室培养英雄 hero={data.get('hero_id')} attr={data.get('attribute_index')} res={res_code}")
        if res_code != 0:
            return [DownFrame(self.sc, payload)]
        from operations import _refresh_frames
        frames = [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)
        # 附带推送 sc_58171 训练次数更新帧
        try:
            use_times = int((result or {}).get("use_times") or 0)
            p58171 = ctx.codec_encode("sc_58171", {
                "exercise_times_info": {
                    "use_times": use_times,
                    "camp_list": []
                }
            })
            if p58171:
                frames.append(DownFrame(58171, p58171))
        except Exception as e:
            ctx.log(f"cs_58166 附带 sc_58171 异常: {e}")
        return frames


@operation
class SaveDanceDIYSequenceOp(Operation):
    """58198 保存 DIY 舞蹈序列：
    cs_58198 {sequence: {pos, base_sequence: {scene_id, music_id, action_id_list}}} → sc_58199 {result: 0}。
    附带推送 sc_58195 全量 DIY 编舞列表。
    """

    cmd = 58198
    sc = 58199

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().save_dance_diy(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_58199", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_58198 -> 保存 DIY 舞蹈序列 pos={data.get('sequence', {}).get('pos')}")
        frames = [DownFrame(self.sc, payload)]
        try:
            from backhome_service import BackHomeService
            p58195 = BackHomeService.get_instance().get_dance_diy_payload(self.uid, ctx.db)
            if p58195:
                frames.append(DownFrame(58195, p58195))
        except Exception as e:
            ctx.log(f"cs_58198 附带 sc_58195 异常: {e}")
        return frames


@operation
class DeleteDanceDIYSequenceOp(Operation):
    """58200 删除 DIY 舞蹈序列：
    cs_58200 {pos} → sc_58201 {result: 0}。
    附带推送 sc_58195 全量 DIY 编舞列表。
    """

    cmd = 58200
    sc = 58201

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().delete_dance_diy(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_58201", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_58200 -> 删除 DIY 舞蹈序列 pos={data.get('pos')}")
        frames = [DownFrame(self.sc, payload)]
        try:
            from backhome_service import BackHomeService
            p58195 = BackHomeService.get_instance().get_dance_diy_payload(self.uid, ctx.db)
            if p58195:
                frames.append(DownFrame(58195, p58195))
        except Exception as e:
            ctx.log(f"cs_58200 附带 sc_58195 异常: {e}")
        return frames


            # ======================================================================
            # Operation：宿舍点赞（原 operations.py 独立 DormLikeOp 迁移）
            # ======================================================================


@operation
class DormLikeOp(Operation):
    """58052 给他人宿舍点赞：cs_58052 {user_id, architecture_id} → sc_58053 {result}。

    dormaction.lua:713-739。落被赞者 backhome_dorm.liked_num（行不存在则建）。
    原 cfg 版把 "backhome_dorm.liked_num + 1" 公式字符串写进整型列。"""

    cmd = 58052
    sc = 58053

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().dorm_like(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58053", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58052 -> 宿舍点赞 target={result['target']} dorm={result['dorm']}")
            return [DownFrame(self.sc, payload)]


@operation
class DormLikeQueryOp(Operation):
    """58054 宿舍点赞数查询：cs_58054 {architecture_id} → sc_58055 {result, liked_num, be_visited_num}。

    协议里的 architecture_id 对应库表 backhome_dorm.dorm_id（原先直接拿 architecture_id
    当列名查，SQL 报 no such column，该 cmd 恒回 result=9）。"""

    cmd = 58054
    sc = 58055

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().dorm_like_query(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58055", {
                "result": 0,
                "liked_num": result["liked_num"],
                "be_visited_num": result["be_visited_num"]}) or b"\x08\x00"
            ctx.log(f"cs_58054 -> 宿舍点赞 dorm={result['aid']} liked={result['liked_num']} "
                    f"visited={result['be_visited_num']}")
            return [DownFrame(self.sc, payload)]


@operation
class CanteenModeOp(Operation):
    """58108 切换食堂经营模式：cs_58108 {architecture_id, cmd} → sc_58109 {result}。

    请求里的模式字段名是 cmd（原先读 data['mode']，恒取到 0，切换无效）。"""

    cmd = 58108
    sc = 58109

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().canteen_mode(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58109", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_58108 -> 切换食堂模式 mode={result['mode']}")
            return [DownFrame(self.sc, payload)]


@operation
class CanteenTaskSubmitOp(Operation):
    """58018 领取食堂委托收益：cs_58018 {architecture_id, pos[]} → sc_58019 {result, extra_reward, entrust, fatigue_list}。

    玩家侧数据在 backhome_canteen_entrust(uid, pos, task_id, start_time, duration)。
    duration 单位是分钟（客户端 canteenentrustdata.lua: start_time + time[tier][1]*60 对比服务器秒）。

    结算公式（时长档位倍率读配置表 time 列 {档:[分钟,百分比]}，非硬编码）：
      收益 = floor(基础奖励 × 档位倍率)；按 base_success 判定大成功 → ceil(×1.5)
    结算后该槽位立即刷新出一个新委托并随 58019 的 entrust 字段下发（客户端每槽只显示一次刷新）。
    奖励路由：510xx 食材 → backhome_ingredient（item_catalog 误归 material，客户端食材库不读 material 表），
    其余按 item_catalog 走 currency/material。"""

    cmd = 58018
    sc = 58019

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().canteen_task_submit(ctx, self.uid, data)

    def respond(self, result, data, ctx):
            payload = ctx.codec_encode("sc_58019", {
                "result": 0,
                "extra_reward": result["extra_reward"],
                "entrust": result["entrust"],
                "fatigue_list": []}) or b"\x08\x00"
            ctx.log(f"cs_58018 -> 食堂委托结算 pos={result['settled']} 奖励 {result['rewards']}")
            from operations import _refresh_frames
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class IdolTraineeRewardOp(Operation):
    """58192 领取游园街贴票任务积分档位奖励：
    cs_58192 {id: rank_id, select_list} → sc_58193 {result, get_id_list, reward_list}。"""

    cmd = 58192
    sc = 58193

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().claim_idol_trainee_rank_reward(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_58193", {
            "result": result["result"],
            "get_id_list": result.get("get_id_list", []),
            "reward_list": result.get("reward_list", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_58192 -> 领取贴票档位奖励 rank={data.get('id')} result={result['result']} get_id_list={result.get('get_id_list')}")
        from operations import _refresh_frames
        frames = _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]

        # 附带 sc_58191 同步帧
        try:
            from backhome_service import BackHomeService
            p58191 = BackHomeService.get_instance().get_idol_trainee_rank_payload(self.uid, ctx.db)
            if p58191:
                frames.append(DownFrame(58191, p58191))
        except Exception as e:
            ctx.log(f"cs_58192 附带 sc_58191 异常: {e}")
        return frames


@operation
class IdolTraineePveBattleOp(Operation):
    """58186 舞蹈培训生 PVE 关卡挑战：
    cs_58186 {stage_id} → sc_58187 {result, prepare_info, round_list, attacker_skin_id, defender_skin_id}。
    彻底解决场景加载卡 90% 的 Bug。
    """

    cmd = 58186
    sc = 58187

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().start_cricket_pve_battle(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_58187", result) or b"\x08\x00"
        ctx.log(f"cs_58186 -> 开始斗舞PVE stage={data.get('stage_id')} atk_skin={result.get('attacker_skin_id')} def_skin={result.get('defender_skin_id')} rounds={len(result.get('round_list', []))}")
        return [DownFrame(self.sc, payload)]


@operation
class IdolTraineeBattleCompleteOp(Operation):
    """58164 舞蹈培训生对战演出完成与结算上报：
    cs_58164 {battle_type, acc_event} → sc_58165 {result, battle_result, attacker_data, defender_data}。
    附带推送 sc_58189 关卡进度全量更新。
    """

    cmd = 58164
    sc = 58165

    def apply(self, data, ctx):
        ctx.uid = self.uid
        from backhome_service import BackHomeService
        return BackHomeService.get_instance().settle_cricket_battle(ctx, self.uid, data)

    def respond(self, result, data, ctx):
        payload_58165 = ctx.codec_encode("sc_58165", {
            "result": result.get("result", 0),
            "battle_result": result.get("battle_result", 1),
            "attacker_data": result.get("attacker_data"),
            "defender_data": result.get("defender_data"),
        }) or b"\x08\x00\x10\x01"

        ctx.log(f"cs_58164 -> 斗舞战斗演出结算 result={result.get('battle_result')} stage={result.get('stage_id')}")
        from operations import _refresh_frames
        frames = [DownFrame(self.sc, payload_58165)] + _refresh_frames(ctx, self.uid)

        # 附带 sc_58189 关卡更新通知帧
        try:
            from backhome_service import BackHomeService
            p58189 = BackHomeService.get_instance().get_idol_trainee_pve_payload(self.uid, ctx.db)
            if p58189:
                frames.append(DownFrame(58189, p58189))
        except Exception as e:
            ctx.log(f"cs_58164 附带 sc_58189 异常: {e}")

        # 附带 sc_28007 关卡得分任务更新帧
        changed_tasks = (result or {}).get("changed_tasks") or []
        if changed_tasks:
            try:
                p28007 = ctx.codec_encode("sc_28007", {"progress_list": changed_tasks})
                if p28007:
                    frames.append(DownFrame(28007, p28007))
                    ctx.log(f"cs_58164 附带 sc_28007 任务差量帧: {[x['id'] for x in changed_tasks]}")
            except Exception as e:
                ctx.log(f"cs_58164 附带 sc_28007 异常: {e}")

        return frames


# ---------------- 游园街静默桩集中注册（19 个多人/联机/未实现协议） ----------------
from backhome_service import SILENT_STUB_CMDS


def _make_backhome_silent_stub(cs_cmd, sc_cmd, desc):
    """表驱动批量生成静默桩 Operation（回 sc_{sc_cmd} result=0 + pb 默认空集合）。"""

    @operation
    class _BackHomeSilentStub(Operation):
        cmd = cs_cmd
        sc = sc_cmd

        def apply(self, data, ctx):
            ctx.uid = self.uid
            return {"result": 0}

        def respond(self, result, data, ctx):
            payload = ctx.codec_encode(f"sc_{sc_cmd}", {"result": 0}) or b"\x08\x00"
            ctx.log(f"cs_{cs_cmd} -> [静默桩] {desc}")
            return [DownFrame(sc_cmd, payload)]

    _BackHomeSilentStub.__name__ = f"BackHomeSilentStub{cs_cmd}Op"
    _BackHomeSilentStub.__qualname__ = _BackHomeSilentStub.__name__
    _BackHomeSilentStub.__doc__ = (
        f"{cs_cmd} 静默桩：{desc}（多人/未实现协议，result=0 空回包保不崩溃）")
    return _BackHomeSilentStub


for _cs, (_sc, _desc) in SILENT_STUB_CMDS.items():
    _make_backhome_silent_stub(_cs, _sc, _desc)


# ---------------- operation_cfg.json 遗留 7 cmd 移植（2026-08-22 退役配置驱动） ----------------
# 原 ConfigOperation 运行时对这 7 条多为"规格稿"级执行（引用解析失败/字段错配/
# 说明文字写库），此处按条目内 Lua 出处改为真实行为，随后 operation_cfg.json 已退役。


@operation
class EquipResolveOp(Operation):
    """13024 装备分解：cs_13024 {equip_id_list} → sc_13025 {result, return_mat_list}。"""

    cmd = 13024
    sc = 13025

    def apply(self, data, ctx):
        eids = data.get("equip_id_list") or data.get("equip_list") or []
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).resolve_equips(ctx, self.uid, eids)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13025", {
            "result": 0,
            "return_mat_list": result["mat_list"]}) or b"\x08\x00"
        ctx.log(f"cs_13024 -> 装备分解 removed={result['removed']} 返还 {result['mat_list']}")
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).build_equip_refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class AccumulateSignOpenOp(Operation):
    """17028 累计签到打开视图：cs_17028 {} → sc_17029 {result}。

    accumulatesignaction.lua:16-17，空请求仅打开界面，数据由 sc_17027 推送。
    """

    cmd = 17028
    sc = 17029

    def apply(self, data, ctx):
        ctx.db.execute("UPDATE accumulate_sign SET open_sign = 0 WHERE uid = ?", (self.uid,))
        return {}

    def respond(self, result, data, ctx):
        return [DownFrame(self.sc, ctx.codec_encode("sc_17029", {"result": 0}) or b"\x08\x00")]


@operation
class AccumulateSignRewardOp(Operation):
    """17030 累计签到领奖：cs_17030 {id_list} → sc_17031 {result, item}。

    accumulatesignaction.lua:28-36。奖励来自 AccumulateLoginCfg（v5_server/
    accumulate_login_cfg.json，2026-08-22 从 x64 Lua 提取：v2=ids 9-16，天数档 40~320）。
    校验：id 属于当前 version 且 login_days >= cfg.num；幂等：已领拒（code 2）。
    领取状态即 accumulate_sign_award 表，sc_17027（generator 动态下发）同源。
    """

    cmd = 17030
    sc = 17031

    _CFG = None

    @classmethod
    def _cfg(cls):
        if cls._CFG is None:
            import os as _os
            try:
                cls._CFG = json.load(open(_os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)),
                    "accumulate_login_cfg.json"), encoding="utf-8"))
            except Exception:
                cls._CFG = {}
        return cls._CFG

    def apply(self, data, ctx):
        ids = data.get("id_list") or []
        if isinstance(ids, (int, str)):
            ids = [ids]
        srow = ctx.db.query("SELECT version, login_days FROM accumulate_sign WHERE uid=?", (self.uid,))
        version = int(srow[0]["version"] or 1) if srow else 1
        login_days = int(srow[0]["login_days"] or 0) if srow else 0
        cfg = self._cfg()
        items = []
        for aid in ids:
            try:
                aid = int(aid)
            except (TypeError, ValueError):
                continue
            c = cfg.get(str(aid)) or cfg.get(aid)
            if not c:
                raise OperationError(2, f"未知签到奖励档 {aid}")
            if int(c.get("version") or 1) != version:
                raise OperationError(2, f"奖励档 {aid} 不属于当前期（v{c.get('version')}）")
            if ctx.db.query("SELECT 1 FROM accumulate_sign_award WHERE uid=? AND award_id=?",
                            (self.uid, aid)):
                raise OperationError(2, f"奖励档 {aid} 已领取")
            if login_days < int(c.get("num") or 0):
                raise OperationError(3, f"累计登录不足: 需{c.get('num')}天 有{login_days}天")
            for iid, num in (c.get("reward") or []):
                _item_add(ctx, self.uid, int(iid), int(num))
                items.append({"id": int(iid), "num": int(num)})
            ctx.db.execute(
                "INSERT OR REPLACE INTO accumulate_sign_award (uid, award_id, update_ts) VALUES (?,?,?)",
                (self.uid, aid, int(time.time())))
        return {"items": items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_17031", {"result": 0, "item": result["items"]}) or b"\x08\x00"
        ctx.log(f"cs_17030 -> 累计签到领奖 {result['items']}")
        frames = [DownFrame(self.sc, payload)]
        if ctx.generator is not None:
            try:
                p27 = ctx.generator.gen_payload(17027, uid=self.uid, db=ctx.db)
                if p27:
                    frames.append(DownFrame(17027, p27))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class ActivityPointRewardOp(Operation):
    """28016 任务活动点档位领奖：cs_28016 {need_active_point, activity_pt_id} → sc_28017 {result, reward_list}。

    activityptaction.lua:9-31。领取档位礼盒不消耗积分：只校验 active_point >= need
    且该档未领过（get_id_list JSON 幂等）。按 activity_pt_cfg.json 配置发放对应档位道具并入库。
    """

    cmd = 28016
    sc = 28017

    def apply(self, data, ctx):
        need = int(data.get("need_active_point") or 0)
        key = int(data.get("activity_pt_id") or 1)
        rows = ctx.db.query(
            "SELECT active_point, get_id_list FROM activity_pt WHERE uid=? AND activity_pt_id=?",
            (self.uid, key))
        if not rows:
            raise OperationError(2, f"活动点 {key} 不存在")
        have = int(rows[0]["active_point"] or 0)
        if have < need:
            raise OperationError(3, f"活动点不足: 需{need} 有{have}")
        try:
            got = json.loads(rows[0]["get_id_list"] or "[]")
        except Exception:
            got = []
        if need in got:
            # 档位已领取时幂等返回空奖励，避免客户端因重复发包报红字错误
            return {"key": key, "need": need, "rewards": []}

        rewards = []
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity_pt_cfg.json")
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    pt_cfg = json.load(f)
                rewards = pt_cfg.get(str(key), {}).get("rewards", {}).get(str(need), [])
        except Exception as e:
            ctx.log(f"读取 activity_pt_cfg.json 异常: {e}", "WARN")

        # 真实发放奖励入库
        for rw in rewards:
            _item_add(ctx, self.uid, rw["id"], rw["num"])

        # 活跃度宝箱额外神装/大场景抽奖彩蛋
        import activity_lottery
        lottery_res, touched_heroes, touched_scenes = activity_lottery.roll_activity_box(ctx.db, self.uid, key, need)
        if lottery_res:
            if lottery_res.get("is_convert"):
                found = False
                for r in rewards:
                    if r["id"] == lottery_res["id"]:
                        r["num"] += lottery_res["num"]
                        found = True
                        break
                if not found:
                    rewards.append({"id": lottery_res["id"], "num": lottery_res["num"]})
            else:
                rewards.append({"id": lottery_res["id"], "num": lottery_res["num"]})

        got.append(need)
        ctx.db.execute(
            "UPDATE activity_pt SET get_id_list=? WHERE uid=? AND activity_pt_id=?",
            (json.dumps(got), self.uid, key))
        return {
            "need": need,
            "key": key,
            "rewards": rewards,
            "touched_heroes": touched_heroes if lottery_res and not lottery_res.get("is_convert") else [],
            "touched_scenes": touched_scenes if lottery_res and not lottery_res.get("is_convert") else []
        }

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_28017", {
            "result": 0,
            "reward_list": result.get("rewards", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_28016 -> 活动点档位领取 key={result['key']} need={result['need']} 获得={result.get('rewards')}")
        frames = [DownFrame(self.sc, payload)]

        # 差分同步帧：若开出了新换装或新场景，顺带下发 sc_14007 和 sc_32009
        for hid in result.get("touched_heroes", []):
            try:
                import hero_codec as _hc
                hf = _hc.build_single_hero_frame(ctx.db, self.uid, hid)
                if hf:
                    frames.append(DownFrame(14007, hf))
            except Exception:
                pass

        if result.get("touched_scenes"):
            try:
                import generator as _gen
                p32009 = _gen.gen_payload(32009, uid=self.uid, db=ctx.db)
                if p32009:
                    frames.append(DownFrame(32009, p32009))
            except Exception:
                pass

        return frames + _refresh_frames(ctx, self.uid)


@operation
class BattleEquipSuitOp(Operation):
    """43004 战斗装备切换上场套装：cs_43004 {equip_suit_id} → sc_43005 {result}。

    battleequipaction.lua:13-24。battle_equip.suit_id 列（2026-08-15 补）。
    原 cfg 版把 "unknown（当前关卡...）" 说明文字写进 stage_id 列。
    当前玩法实例不区分 stage，统一更新 uid 全部行的 suit_id。
    """

    cmd = 43004
    sc = 43005

    def apply(self, data, ctx):
        suit = int(data.get("equip_suit_id") or 0)
        ctx.db.execute("UPDATE battle_equip SET suit_id=? WHERE uid=?", (suit, self.uid))
        return {"suit_id": suit}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_43005", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_43004 -> 战斗装备套装切换 suit={result['suit_id']}")
        return [DownFrame(self.sc, payload)]


@operation
class EquipAutoDecomposeOp(Operation):
    """13054 设置装备自动分解状态：cs_13054 {type, sign} → sc_13055 {result}。"""

    cmd = 13054
    sc = 13055

    def apply(self, data, ctx):
        t = int(data.get("type") or 1)
        sign = int(data.get("sign") or 0)
        from equip_service import EquipService
        return EquipService.get_instance(db=ctx.db).save_auto_decompose_cfg(ctx, self.uid, t, sign)

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_13055", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_13054 -> 设置自动分解 type={result['type']} sign={result['sign']}")
        return [DownFrame(self.sc, payload)]


# [2026-09-06 规范化收归] DormLikeOp(58052) 已统一收归至上方 58xxx 家园/后宅/食堂独立区块集中托管。


@operation
class ActivityPointTierRewardOp(Operation):
    """60054 活动积分档位领奖：cs_60054 {point_reward_id_list} → sc_60055 {result, reward_list}。

    activityaction.lua:519-546。奖励来自 ActivityPointRewardCfg（v5_server/
    activity_point_reward_cfg.json，1397 条，2026-08-22 从 x64 Lua 提取）。
    幂等：activity_point_reward(uid, activity_id, reward_id) 已领拒。
    积分门槛未校验（本地服务宽松；正式还原需活动积分累计表），奖励按配置真实发放。
    原 cfg 版 grant 原语读不到 items 实际从未发放。
    """

    cmd = 60054
    sc = 60055

    _CFG = None

    @classmethod
    def _cfg(cls):
        if cls._CFG is None:
            import os as _os
            try:
                cls._CFG = json.load(open(_os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)),
                    "activity_point_reward_cfg.json"), encoding="utf-8"))
            except Exception:
                cls._CFG = {}
        return cls._CFG

    def apply(self, data, ctx):
        ids = data.get("point_reward_id_list") or []
        if isinstance(ids, (int, str)):
            ids = [ids]
        cfg = self._cfg()
        rogueteam_claimed = []
        for rid in ids:
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            c = cfg.get(str(rid)) or cfg.get(rid)
            if not c:
                raise OperationError(2, f"未知积分奖励档 {rid}")
            if ctx.db.query(
                    "SELECT 1 FROM activity_point_reward WHERE uid=? AND reward_id=?",
                    (self.uid, rid)):
                raise OperationError(2, f"积分奖励档 {rid} 已领取")
            for iid, num in (c.get("reward") or []):
                _item_add(ctx, self.uid, int(iid), int(num))
                items.append({"id": int(iid), "num": int(num)})
            act_id = int(c.get("activity_id") or 0)
            ctx.db.execute(
                "INSERT OR REPLACE INTO activity_point_reward (uid, activity_id, reward_id, update_ts) "
                "VALUES (?,?,?,?)",
                (self.uid, act_id, rid, int(time.time())))
            if act_id == 1101:
                rogueteam_claimed.append(rid)

        if rogueteam_claimed:
            user_row = ctx.db.get("rogueteam_user", self.uid, "AND template_id=100001")
            cur_rewarded = json.loads(user_row.get("rewarded_list_json") or "[]") if user_row else []
            for r in rogueteam_claimed:
                if r not in cur_rewarded:
                    cur_rewarded.append(r)
            ctx.db.upsert("rogueteam_user", self.uid, {
                "template_id": 100001,
                "rewarded_list_json": json.dumps(cur_rewarded, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))

        return {"items": items, "rogueteam_claimed": rogueteam_claimed}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_60055", {
            "result": 0, "reward_list": result["items"]}) or b"\x08\x00"
        ctx.log(f"cs_60054 -> 积分档位领取 {result['items']}")
        extra_frames = []
        if result.get("rogueteam_claimed"):
            p88309 = ctx.codec_encode("sc_88309", {
                "template_id": 100001,
                "reward_list": result["rogueteam_claimed"]
            })
            if p88309:
                extra_frames.append(DownFrame(88309, p88309))
        return [DownFrame(self.sc, payload)] + extra_frames + _refresh_frames(ctx, self.uid)


# ==============================================================================
#                      四大高难周常玩法系统操作 (@operation 调度)
# ==============================================================================

# ----------------- 1. 梦境再构 (Boss Challenge) -----------------

@operation
class BossChallengeSelectModeOp(Operation):
    """cs_45202 {select: mode} -> sc_45203 {result: 0} 切换普通/进阶模式"""
    cmd = 45202
    sc = 45203

    def apply(self, data, ctx):
        mode = int(data.get("select") or 2)
        if ctx.db and hasattr(ctx.db, "set_boss_challenge_mode"):
            ctx.db.set_boss_challenge_mode(self.uid, mode)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45203", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_45202 -> 梦境再构切换模式 mode={data.get('select')}")
        extra = []
        try:
            import weekly_challenge_service
            sc_201, sc_001, sc_101 = weekly_challenge_service.get_boss_challenge_data(self.uid, ctx.db)
            p201 = ctx.codec_encode("sc_45201", sc_201)
            if p201: extra.append(DownFrame(45201, p201))
            if sc_201.get("mode") == 1:
                p001 = ctx.codec_encode("sc_45001", sc_001)
                if p001: extra.append(DownFrame(45001, p001))
            elif sc_201.get("mode") == 2:
                p101 = ctx.codec_encode("sc_45101", sc_101)
                if p101: extra.append(DownFrame(45101, p101))
        except Exception:
            pass
        return extra + [DownFrame(self.sc, payload)]


@operation
class BossChallengeResetHardModeOp(Operation):
    """cs_45204 {} -> sc_45205 {result: 0} 重置整轮进阶梦境"""
    cmd = 45204
    sc = 45205

    def apply(self, data, ctx):
        if ctx.db and hasattr(ctx.db, "reset_boss_challenge_advance_all"):
            ctx.db.reset_boss_challenge_advance_all(self.uid)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45205", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_45204 -> 梦境再构重置进阶梦境")
        extra = []
        try:
            import weekly_challenge_service
            sc_201, _, sc_101 = weekly_challenge_service.get_boss_challenge_data(self.uid, ctx.db)
            p201 = ctx.codec_encode("sc_45201", sc_201)
            if p201: extra.append(DownFrame(45201, p201))
            p101 = ctx.codec_encode("sc_45101", sc_101)
            if p101: extra.append(DownFrame(45101, p101))
        except Exception:
            pass
        return extra + [DownFrame(self.sc, payload)]


@operation
class BossChallengeResetNormalBossOp(Operation):
    """cs_45006 {group_id} -> sc_45007 {result: 0} 重置普通梦境指定首领"""
    cmd = 45006
    sc = 45007

    def apply(self, data, ctx):
        gid = int(data.get("group_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_boss_challenge_normal_boss"):
            ctx.db.reset_boss_challenge_normal_boss(self.uid, gid)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45007", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_45006 -> 梦境再构重置普通首领 group_id={data.get('group_id')}")
        extra = []
        try:
            import weekly_challenge_service
            _, sc_001, _ = weekly_challenge_service.get_boss_challenge_data(self.uid, ctx.db)
            p001 = ctx.codec_encode("sc_45001", sc_001)
            if p001: extra.append(DownFrame(45001, p001))
        except Exception:
            pass
        return [DownFrame(self.sc, payload)] + extra


@operation
class BossChallengeResetHardBossOp(Operation):
    """cs_45106 {boss_id} -> sc_45107 {result: 0} 重置进阶梦境指定首领"""
    cmd = 45106
    sc = 45107

    def apply(self, data, ctx):
        bid = int(data.get("boss_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_boss_challenge_advance_boss"):
            ctx.db.reset_boss_challenge_advance_boss(self.uid, bid)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45107", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_45106 -> 梦境再构重置进阶首领 boss_id={data.get('boss_id')}")
        extra = []
        try:
            import weekly_challenge_service
            _, _, sc_101 = weekly_challenge_service.get_boss_challenge_data(self.uid, ctx.db)
            p101 = ctx.codec_encode("sc_45101", sc_101)
            if p101: extra.append(DownFrame(45101, p101))
        except Exception:
            pass
        return [DownFrame(self.sc, payload)] + extra


@operation
class BossChallengeSaveHardTeamOp(Operation):
    """cs_45108 {id, heroes_cfg} -> sc_45109 {result: 0} 保存进阶梦境首领出战阵容"""
    cmd = 45108
    sc = 45109

    def apply(self, data, ctx):
        boss_id = int(data.get("id") or 0)
        heroes_cfg = data.get("heroes_cfg") or []
        if ctx.db and hasattr(ctx.db, "save_boss_challenge_hero_team"):
            ctx.db.save_boss_challenge_hero_team(self.uid, 2, boss_id, heroes_cfg)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45109", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_45108 -> 保存进阶梦境阵容 boss_id={data.get('id')} heroes={data.get('heroes_cfg')}")
        return [DownFrame(self.sc, payload)]


@operation
class BossChallengeSaveNormalTeamOp(Operation):
    """cs_45110 {group_id, heroes_cfg} -> sc_45111 {result: 0} 保存普通梦境首领出战阵容"""
    cmd = 45110
    sc = 45111

    def apply(self, data, ctx):
        group_id = int(data.get("group_id") or 0)
        heroes_cfg = data.get("heroes_cfg") or []
        if ctx.db and hasattr(ctx.db, "save_boss_challenge_hero_team"):
            ctx.db.save_boss_challenge_hero_team(self.uid, 1, group_id, heroes_cfg)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45111", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_45110 -> 保存普通梦境阵容 group_id={data.get('group_id')} heroes={data.get('heroes_cfg')}")
        return [DownFrame(self.sc, payload)]


@operation
class BossChallengeClaimStarRewardOp(Operation):
    """cs_45004 {star} -> sc_45005 {result: 0, item_list} 领取普通梦境星级奖励"""
    cmd = 45004
    sc = 45005

    def apply(self, data, ctx):
        import weekly_challenge_service
        star = int(data.get("star") or 0)
        area_id = 4
        if ctx.db:
            try:
                prog = ctx.db.get_boss_challenge_progress(self.uid)
                if prog and prog.get("area_id"):
                    area_id = int(prog["area_id"])
            except Exception:
                pass
        items = weekly_challenge_service.get_boss_challenge_reward_items(1, star, area_id=area_id)
        for it in items:
            _item_add(ctx, self.uid, it["id"], it["num"])
        if ctx.db and hasattr(ctx.db, "claim_boss_challenge_reward"):
            ctx.db.claim_boss_challenge_reward(self.uid, 1, star)
        return {"items": items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45005", {
            "result": 0,
            "item_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_45004 -> 梦境再构领取星级奖励 star={data.get('star')} 奖励={result.get('items')}")
        extra = []
        try:
            import weekly_challenge_service
            _, sc_001, _ = weekly_challenge_service.get_boss_challenge_data(self.uid, ctx.db)
            p001 = ctx.codec_encode("sc_45001", sc_001)
            if p001: extra.append(DownFrame(45001, p001))
        except Exception:
            pass
        return _refresh_frames(ctx, self.uid) + extra + [DownFrame(self.sc, payload)]


@operation
class BossChallengeClaimPointRewardOp(Operation):
    """cs_45102 {point} -> sc_45103 {result: 0, item_list} 领取进阶梦境单档积分奖励"""
    cmd = 45102
    sc = 45103

    def apply(self, data, ctx):
        import weekly_challenge_service
        point = int(data.get("point") or 0)
        mode_id = 102
        if ctx.db:
            try:
                prog = ctx.db.get_boss_challenge_progress(self.uid)
                if prog and prog.get("mode") in (101, 102):
                    mode_id = int(prog["mode"])
                elif prog and int(prog.get("mode") or 0) == 1:
                    mode_id = 101
            except Exception:
                pass
        items = weekly_challenge_service.get_boss_challenge_reward_items(2, point, mode_id=mode_id)
        for it in items:
            _item_add(ctx, self.uid, it["id"], it["num"])
        if ctx.db and hasattr(ctx.db, "claim_boss_challenge_reward"):
            ctx.db.claim_boss_challenge_reward(self.uid, 2, point)
        return {"items": items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45103", {
            "result": 0,
            "item_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_45102 -> 梦境再构领取积分奖励 point={data.get('point')} 奖励={result.get('items')}")
        extra = []
        try:
            import weekly_challenge_service
            _, _, sc_101 = weekly_challenge_service.get_boss_challenge_data(self.uid, ctx.db)
            p101 = ctx.codec_encode("sc_45101", sc_101)
            if p101: extra.append(DownFrame(45101, p101))
        except Exception:
            pass
        return _refresh_frames(ctx, self.uid) + extra + [DownFrame(self.sc, payload)]




@operation
class BossChallengeClaimAllPointRewardOp(Operation):
    """cs_45112 {} -> sc_45113 {result: 0, item_list} 一键领取进阶梦境全部可领积分奖励"""
    cmd = 45112
    sc = 45113

    def apply(self, data, ctx):
        import weekly_challenge_service
        cat = weekly_challenge_service.get_boss_catalog()
        modes = cat.get("advance_modes", {})
        
        mode_id = 102
        if ctx.db:
            try:
                prog = ctx.db.get_boss_challenge_progress(self.uid)
                if prog and prog.get("mode") in (101, 102):
                    mode_id = int(prog["mode"])
                elif prog and int(prog.get("mode") or 0) == 1:
                    mode_id = 101
            except Exception:
                pass
                
        m_info = modes.get(str(mode_id), modes.get(mode_id, {}))
        all_rewards = m_info.get("rewards", [])
        
        # 计算当前玩家进阶总分（去重求各首领最高分之和）
        total_score = 0
        claimed_ids = set()
        if ctx.db:
            try:
                score_rows = ctx.db.query("SELECT boss_id, score FROM boss_challenge_advance WHERE uid=?", (self.uid,))
                if not score_rows:
                    score_rows = ctx.db.query("SELECT boss_id, score FROM boss_challenge_scores WHERE uid=?", (self.uid,))
                b_scores = {}
                for sr in score_rows:
                    bid = int(sr.get("boss_id") or 0)
                    b_scores[bid] = max(b_scores.get(bid, 0), int(sr.get("score") or 0))
                total_score = sum(b_scores.values())
                
                reward_rows = ctx.db.query("SELECT reward_id FROM boss_challenge_claimed_rewards WHERE uid=? AND reward_type=2", (self.uid,))
                for rr in reward_rows:
                    claimed_ids.add(int(rr["reward_id"]))
            except Exception:
                pass
                
        # 汇总所有满足 point <= total_score 且未领取的奖励
        merged_items = {}
        new_claimed = []
        for rew in all_rewards:
            p_need = int(rew.get("point") or 0)
            if p_need not in claimed_ids and total_score >= p_need:
                new_claimed.append(p_need)
                for d in rew.get("drops", []):
                    iid, inum = int(d[0]), int(d[1])
                    merged_items[iid] = merged_items.get(iid, 0) + inum

        out_items = [{"id": k, "num": v} for k, v in merged_items.items()]
        for it in out_items:
            _item_add(ctx, self.uid, it["id"], it["num"])
            
        if ctx.db and hasattr(ctx.db, "claim_all_boss_challenge_point_rewards") and new_claimed:
            ctx.db.claim_all_boss_challenge_point_rewards(self.uid, new_claimed)
            
        return {"items": out_items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45113", {
            "result": 0,
            "item_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_45112 -> 梦境再构一键领取积分奖励 奖励={result.get('items')}")
        extra = []
        try:
            import weekly_challenge_service
            _, _, sc_101 = weekly_challenge_service.get_boss_challenge_data(self.uid, ctx.db)
            p101 = ctx.codec_encode("sc_45101", sc_101)
            if p101: extra.append(DownFrame(45101, p101))
        except Exception:
            pass
        return _refresh_frames(ctx, self.uid) + extra + [DownFrame(self.sc, payload)]


@operation
class BossChallengeModifyAffixOp(Operation):
    """cs_45104 {id, affix_index_list, time_index_list, diffculty_index} -> sc_45105 {result: 0} 保存词缀/难度"""
    cmd = 45104
    sc = 45105

    def apply(self, data, ctx):
        boss_id = int(data.get("id") or 0)
        affixes = data.get("affix_index_list") or []
        times = data.get("time_index_list") or []
        diff_idx = int(data.get("diffculty_index") or 1)
        if ctx.db and hasattr(ctx.db, "save_boss_challenge_affixes"):
            ctx.db.save_boss_challenge_affixes(self.uid, boss_id, affixes, times, diff_idx)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_45105", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_45104 -> 梦境再构配置词缀 boss={data.get('id')} diff={data.get('diffculty_index')} affixes={data.get('affix_index_list')}")
        return [DownFrame(self.sc, payload)]


# ----------------- 2. 黑区净化 (Mythic) -----------------

@operation
class MythicQueryInfoOp(Operation):
    """cs_44010 {} -> sc_44011 {result: 0} 查询黑区最新轮换"""
    cmd = 44010
    sc = 44011

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        import weekly_challenge_service
        payload = ctx.codec_encode("sc_44011", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_44010 -> 查询黑区轮换信息")
        
        frames = []
        if ctx.codec_encode and ctx.db:
            try:
                sc_07, sc_09, sc_19, sc_21, sc_23 = weekly_challenge_service.get_mythic_data(self.uid, ctx.db)
                p07 = ctx.codec_encode("sc_44007", sc_07)
                p09 = ctx.codec_encode("sc_44009", sc_09)
                p19 = ctx.codec_encode("sc_44019", sc_19)
                p21 = ctx.codec_encode("sc_44021", sc_21)
                p23 = ctx.codec_encode("sc_44023", sc_23)
                if p07: frames.append(DownFrame(44007, p07))
                if p09: frames.append(DownFrame(44009, p09))
                if p19: frames.append(DownFrame(44019, p19))
                if p21: frames.append(DownFrame(44021, p21))
                if p23: frames.append(DownFrame(44023, p23))
            except Exception as e:
                ctx.log(f"cs_44010 -> 动态推送下行帧异常: {e}")
        return frames + [DownFrame(self.sc, payload)]


@operation
class MythicClaimStarRewardOp(Operation):
    """cs_44012 {star} -> sc_44013 {result: 0, item_list} 领取常规黑区星级奖励"""
    cmd = 44012
    sc = 44013

    def apply(self, data, ctx):
        import mythic_affix_cfg
        star = int(data.get("star") or 0)
        
        # 默认查询当前玩家选中的黑区难度
        diff = 10
        if ctx.db:
            rows = ctx.db.query("SELECT current_difficulty FROM mythic_public WHERE uid=?", (self.uid,))
            if rows and rows[0].get("current_difficulty"):
                diff = int(rows[0]["current_difficulty"])
                
        # 奖励模板
        reward_template = mythic_affix_cfg.MYTHIC_NORMAL_STAR_REWARDS.get(diff, [{"id": 25, "num": 150}, {"id": 40701, "num": 4}])
        items = [{"id": it["id"], "num": it["num"]} for it in reward_template]
        
        for it in items:
            _item_add(ctx, self.uid, it["id"], it["num"])
            
        if ctx.db and hasattr(ctx.db, "claim_mythic_reward"):
            ctx.db.claim_mythic_reward(self.uid, star)
            
        return {"items": items}

    def respond(self, result, data, ctx):
        import weekly_challenge_service
        payload = ctx.codec_encode("sc_44013", {
            "result": 0,
            "item_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_44012 -> 黑区净化领取星级奖励 star={data.get('star')} 奖励={result.get('items')}")
        
        frames = [DownFrame(self.sc, payload)]
        if ctx.codec_encode and ctx.db:
            try:
                _, sc_09, _, _, _ = weekly_challenge_service.get_mythic_data(self.uid, ctx.db)
                p09 = ctx.codec_encode("sc_44009", sc_09)
                if p09: frames.append(DownFrame(44009, p09))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class MythicReadDifficultyOp(Operation):
    """cs_44014 {} -> sc_44015 {result: 0} 标记新难度已读"""
    cmd = 44014
    sc = 44015

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_44015", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_44014 -> 黑区标记新难度已读")
        return [DownFrame(self.sc, payload)]


@operation
class MythicChangeDifficultyOp(Operation):
    """cs_44016 {difficulty} -> sc_44017 {result: 0} 切换黑区难度"""
    cmd = 44016
    sc = 44017

    def apply(self, data, ctx):
        diff = int(data.get("difficulty") or 10)
        if ctx.db and hasattr(ctx.db, "set_mythic_difficulty"):
            ctx.db.set_mythic_difficulty(self.uid, diff)
        return {"result": 0, "difficulty": diff}

    def respond(self, result, data, ctx):
        import weekly_challenge_service
        payload = ctx.codec_encode("sc_44017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_44016 -> 黑区切换难度 diff={data.get('difficulty')}")
        
        frames = [DownFrame(self.sc, payload)]
        if ctx.codec_encode and ctx.db:
            try:
                _, _, sc_19, _, _ = weekly_challenge_service.get_mythic_data(self.uid, ctx.db)
                p19 = ctx.codec_encode("sc_44019", sc_19)
                if p19: frames.append(DownFrame(44019, p19))
            except Exception:
                pass
        return frames


@operation
class MythicSelectLevelOp(Operation):
    """cs_44024 {difficulty_id} -> sc_44025 {result: 0} 选择失序深区热度档位"""
    cmd = 44024
    sc = 44025

    def apply(self, data, ctx):
        diff = int(data.get("difficulty_id") or 30)
        if ctx.db and hasattr(ctx.db, "set_mythic_final_difficulty"):
            ctx.db.set_mythic_final_difficulty(self.uid, diff)
        return {"result": 0, "difficulty_id": diff}

    def respond(self, result, data, ctx):
        import weekly_challenge_service
        payload = ctx.codec_encode("sc_44025", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_44024 -> 失序深区选择热度档位 diff={data.get('difficulty_id')}")
        
        frames = [DownFrame(self.sc, payload)]
        if ctx.codec_encode and ctx.db:
            try:
                _, _, _, _, sc_23 = weekly_challenge_service.get_mythic_data(self.uid, ctx.db)
                p23 = ctx.codec_encode("sc_44023", sc_23)
                if p23: frames.append(DownFrame(44023, p23))
            except Exception:
                pass
        return frames


@operation
class MythicResetTeamOp(Operation):
    """cs_44026 {} -> sc_44027 {result: 0} 重置失序深区队伍/放弃挑战"""
    cmd = 44026
    sc = 44027

    def apply(self, data, ctx):
        if ctx.db and hasattr(ctx.db, "reset_mythic_final_team"):
            ctx.db.reset_mythic_final_team(self.uid)
        return {"result": 0}

    def respond(self, result, data, ctx):
        import weekly_challenge_service
        payload = ctx.codec_encode("sc_44027", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_44026 -> 失序深区放弃挑战/重置队伍")
        
        frames = [DownFrame(self.sc, payload)]
        if ctx.codec_encode and ctx.db:
            try:
                _, _, _, _, sc_23 = weekly_challenge_service.get_mythic_data(self.uid, ctx.db)
                p23 = ctx.codec_encode("sc_44023", sc_23)
                if p23: frames.append(DownFrame(44023, p23))
            except Exception:
                pass
        return frames


@operation
class MythicGetFinalRewardOp(Operation):
    """cs_44028 {difficulty_id} -> sc_44029 {result: 0, item_list} 领取失序深区单档奖励"""
    cmd = 44028
    sc = 44029

    def apply(self, data, ctx):
        import mythic_affix_cfg
        diff = int(data.get("difficulty_id") or 1)
        claimed = True
        if ctx.db and hasattr(ctx.db, "claim_mythic_final_reward"):
            claimed = ctx.db.claim_mythic_final_reward(self.uid, diff)
        
        if not claimed:
            return {"items": [], "difficulty_id": diff}

        reward_template = mythic_affix_cfg.MYTHIC_FINAL_REWARDS.get(diff, [
            {"id": 43, "num": 60},
            {"id": 41301, "num": 2},
            {"id": 25, "num": 150},
            {"id": 40701, "num": 4}
        ])
        items = [{"id": it["id"], "num": it["num"]} for it in reward_template]
        for it in items:
            _item_add(ctx, self.uid, it["id"], it["num"])
            
        return {"items": items, "difficulty_id": diff}

    def respond(self, result, data, ctx):
        import weekly_challenge_service
        payload = ctx.codec_encode("sc_44029", {
            "result": 0,
            "item_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_44028 -> 失序深区领取单档奖励 diff={data.get('difficulty_id')} 奖励={result.get('items')}")
        
        frames = []
        if ctx.codec_encode and ctx.db:
            try:
                _, _, _, _, sc_23 = weekly_challenge_service.get_mythic_data(self.uid, ctx.db)
                p23 = ctx.codec_encode("sc_44023", sc_23)
                if p23: frames.append(DownFrame(44023, p23))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class MythicGetAllFinalRewardOp(Operation):
    """cs_44030 {} -> sc_44031 {result: 0, item_list} 一键领取全部失序深区通关奖励"""
    cmd = 44030
    sc = 44031

    def apply(self, data, ctx):
        import mythic_affix_cfg
        newly_claimed = []
        if ctx.db and hasattr(ctx.db, "claim_all_mythic_final_rewards"):
            newly_claimed = ctx.db.claim_all_mythic_final_rewards(self.uid)
            
        merged_items = {}
        for diff in newly_claimed:
            rewards = mythic_affix_cfg.MYTHIC_FINAL_REWARDS.get(diff, [
                {"id": 43, "num": 60},
                {"id": 41301, "num": 2},
                {"id": 25, "num": 150},
                {"id": 40701, "num": 4}
            ])
            for it in rewards:
                merged_items[it["id"]] = merged_items.get(it["id"], 0) + it["num"]
                
        item_list = [{"id": k, "num": v} for k, v in merged_items.items()]
        for it in item_list:
            _item_add(ctx, self.uid, it["id"], it["num"])
            
        return {"items": item_list, "claimed_diffs": newly_claimed}

    def respond(self, result, data, ctx):
        import weekly_challenge_service
        payload = ctx.codec_encode("sc_44031", {
            "result": 0,
            "item_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_44030 -> 失序深区一键全领 档位={result.get('claimed_diffs')} 汇总奖励={result.get('items')}")
        
        frames = []
        if ctx.codec_encode and ctx.db:
            try:
                _, _, _, _, sc_23 = weekly_challenge_service.get_mythic_data(self.uid, ctx.db)
                p23 = ctx.codec_encode("sc_44023", sc_23)
                if p23: frames.append(DownFrame(44023, p23))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid) + [DownFrame(self.sc, payload)]


@operation
class MythicReadFinalOp(Operation):
    """cs_44032 {} -> sc_44033 {result: 0} 标记失序深区已读"""
    cmd = 44032
    sc = 44033

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_44033", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_44032 -> 黑区失序深区标记已读")
        return [DownFrame(self.sc, payload)]


# ----------------- 3. 多维变量 (Polyhedron / Matrix) -----------------

@operation
class PolyhedronStartOp(Operation):
    """cs_18010 {hero_id_list, beacon_id_list, difficulty} -> sc_18001 + sc_18011 {result: 0} 开始多维探索"""
    cmd = 18010
    sc = 18011

    def apply(self, data, ctx):
        hero_ids = data.get("hero_id_list") or []
        beacon_ids = data.get("beacon_id_list") or []
        difficulty = int(data.get("difficulty") or 1)
        run_data = PolyhedronRunManager.start_run(self.uid, ctx.db, hero_ids, beacon_ids, difficulty)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        frames = _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        payload = ctx.codec_encode("sc_18011", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18010 -> 多维变量开启探索 diff={data.get('difficulty')} heroes={data.get('hero_id_list')}")
        frames.append(DownFrame(self.sc, payload))
        return frames


@operation
class PolyhedronRewardOp(Operation):
    """cs_18012 {index} -> sc_18001 + sc_18013 {result: 0} 挑选战后珍宝/放弃领金币"""
    cmd = 18012
    sc = 18013

    def apply(self, data, ctx):
        idx = int(data.get("index") if data.get("index") is not None else 0)
        run_data = PolyhedronRunManager.select_reward(self.uid, ctx.db, idx)
        end_info = None
        if run_data and run_data.get("state") == 3:
            end_info = PolyhedronRunManager.settle_run(self.uid, ctx.db)
        return {"result": 0, "run_data": run_data, "end_info": end_info}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_18013", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18012 -> 多维变量选择珍宝奖励 index={data.get('index')}")
        frames = [DownFrame(self.sc, payload)]
        end_info = result.get("end_info")
        if end_info:
            end_bytes = ctx.codec_encode("sc_18005", {"end_info": end_info})
            if end_bytes:
                frames.append(DownFrame(18005, end_bytes))
        frames += _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        if end_info:
            frames += _refresh_frames(ctx, self.uid)
        return frames


@operation
class PolyhedronSelectStageOp(Operation):
    """cs_18014 {index} -> sc_18001 + sc_18015 {result: 0} 选择节点关卡大门"""
    cmd = 18014
    sc = 18015

    def apply(self, data, ctx):
        idx = int(data.get("index") or 1)
        run_data = PolyhedronRunManager.select_gate(self.uid, ctx.db, idx)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_18015", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18014 -> 多维变量选择大门 index={data.get('index')}")
        frames = [DownFrame(self.sc, payload)] + _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        return frames


@operation
class PolyhedronResetOp(Operation):
    """cs_18016 {} -> sc_18001 + sc_18017 {result: 0} 放弃/重置本轮探索"""
    cmd = 18016
    sc = 18017

    def apply(self, data, ctx):
        PolyhedronRunManager.reset_run(self.uid, ctx.db)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_18017", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_18016 -> 多维变量放弃/重置探索")
        frames = [DownFrame(self.sc, payload)] + _build_18003_frame(ctx, None, uid=self.uid)
        return frames


@operation
class PolyhedronSettleQueryOp(Operation):
    """cs_18018 {} -> sc_18005 + sc_18001 + sc_18019 {result: 0} 结算多维对局"""
    cmd = 18018
    sc = 18019

    def apply(self, data, ctx):
        end_info = PolyhedronRunManager.settle_run(self.uid, ctx.db)
        return {"result": 0, "end_info": end_info}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_18019", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18018 -> 多维变量结算 end_info={result.get('end_info')}")
        frames = [DownFrame(self.sc, payload)]
        end_info = result.get("end_info") or {"point": 0, "decision_exp": 0, "terminal_exp": 0}
        end_bytes = ctx.codec_encode("sc_18005", {"end_info": end_info})
        if end_bytes:
            frames.append(DownFrame(18005, end_bytes))
        frames += _build_18003_frame(ctx, None, uid=self.uid)
        frames += _refresh_frames(ctx, self.uid)
        return frames


@operation
class PolyhedronEnlistHeroOp(Operation):
    """cs_18020 {hero_id} -> sc_18001 + sc_18021 {result: 0} 招募多维英雄"""
    cmd = 18020
    sc = 18021

    def apply(self, data, ctx):
        hid = int(data.get("hero_id") or 0)
        run_data = PolyhedronRunManager.enlist_hero(self.uid, ctx.db, hid)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        frames = _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        payload = ctx.codec_encode("sc_18021", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18020 -> 多维变量招募英雄 hero_id={data.get('hero_id')}")
        frames.append(DownFrame(self.sc, payload))
        return frames


@operation
class PolyhedronBuyShopItemOp(Operation):
    """cs_18022 {index} -> sc_18001 + sc_18023 {result: 0} 多维商店购买"""
    cmd = 18022
    sc = 18023

    def apply(self, data, ctx):
        idx = int(data.get("index") or 1)
        run_data = PolyhedronRunManager.buy_shop_item(self.uid, ctx.db, idx)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        frames = _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        payload = ctx.codec_encode("sc_18023", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18022 -> 多维商店购买道具 index={data.get('index')}")
        frames.append(DownFrame(self.sc, payload))
        return frames


@operation
class PolyhedronRefreshShopOp(Operation):
    """cs_18024 {} -> sc_18001 + sc_18025 {result: 0} 刷新多维商店"""
    cmd = 18024
    sc = 18025

    def apply(self, data, ctx):
        run_data = PolyhedronRunManager.refresh_shop(self.uid, ctx.db)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        frames = _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        payload = ctx.codec_encode("sc_18025", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_18024 -> 多维商店刷新")
        frames.append(DownFrame(self.sc, payload))
        return frames


@operation
class PolyhedronShopHealOp(Operation):
    """cs_18026 {} -> sc_18001 + sc_18027 {result: 0} 多维生命泉水恢复"""
    cmd = 18026
    sc = 18027

    def apply(self, data, ctx):
        run_data = PolyhedronRunManager.recover_shop_blood(self.uid, ctx.db)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        frames = _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        payload = ctx.codec_encode("sc_18027", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_18026 -> 多维生命恢复")
        frames.append(DownFrame(self.sc, payload))
        return frames


@operation
class PolyhedronSwitchTeamOp(Operation):
    """cs_18028 {fight_id_list} -> sc_18001 + sc_18029 {result: 0} 调整多维出战编队"""
    cmd = 18028
    sc = 18029

    def apply(self, data, ctx):
        fight_list = data.get("fight_id_list") or []
        run_data = PolyhedronRunManager.switch_team(self.uid, ctx.db, fight_list)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        frames = _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        payload = ctx.codec_encode("sc_18029", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18028 -> 多维调整出战队伍 {data.get('fight_id_list')}")
        frames.append(DownFrame(self.sc, payload))
        return frames


@operation
class PolyhedronResetRewardOp(Operation):
    """cs_18030 {} -> sc_18001 + sc_18031 {result: 0} 重置/重骰珍宝"""
    cmd = 18030
    sc = 18031

    def apply(self, data, ctx):
        run_data = PolyhedronRunManager.reroll_reward(self.uid, ctx.db)
        return {"result": 0, "run_data": run_data}

    def respond(self, result, data, ctx):
        frames = _build_18003_frame(ctx, result.get("run_data"), uid=self.uid)
        payload = ctx.codec_encode("sc_18031", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_18030 -> 多维重置奖励")
        frames.append(DownFrame(self.sc, payload))
        return frames


@operation
class PolyhedronSetTerminalOp(Operation):
    """cs_18032 {upgrade_id_list} -> sc_18033 {result: 0} 升级/配置多维终端"""
    cmd = 18032
    sc = 18033

    def apply(self, data, ctx):
        up_list = data.get("upgrade_id_list") or []
        ctx.db.execute("DELETE FROM polyhedron_terminal WHERE uid = ?", (self.uid,))
        now_ts = int(time.time())
        for up_id in up_list:
            ctx.db.execute("INSERT OR REPLACE INTO polyhedron_terminal (uid, terminal_id, update_ts) VALUES (?, ?, ?)",
                           (self.uid, int(up_id), now_ts))
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_18033", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_18032 -> 多维终端配置已持久化 {data.get('upgrade_id_list')}")
        return [DownFrame(self.sc, payload)]


@operation
class PolyhedronRedPointOp(Operation):
    """cs_18034 {} -> sc_18035 {result: 0} 多维消除新赛季红点"""
    cmd = 18034
    sc = 18035

    def apply(self, data, ctx):
        ctx.db.execute("UPDATE polyhedron_meta SET is_new = 0, update_ts = ? WHERE uid = ?",
                       (int(time.time()), self.uid))
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_18035", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_18034 -> 多维消除新赛季红点")
        return [DownFrame(self.sc, payload)]


@operation
class PolyhedronClaimPolicyRewardOp(Operation):
    """cs_66000 {activity_id, type, level} -> sc_66001 {result: 0, reward_list} 领取多维历程政策奖励"""
    cmd = 66000
    sc = 66001

    def apply(self, data, ctx):
        import res_version_manager as _rvm
        activity_id = int(data.get("activity_id") or _rvm.get_version_config()["polyhedron_activity_id"])
        ctype = int(data.get("type") or 1)
        target_level = int(data.get("level") or 1)
        now = int(time.time())

        # 20 级常驻多维历程经验阶梯与奖励表 (Activity 0)
        policy_exp_steps = [60, 120, 180, 240, 300, 400, 500, 600, 700, 800, 920, 1040, 1160, 1280, 1400, 1520, 1640, 1760, 1880, 2000]
        policy_rewards = {
            1: [{"id": 28, "num": 60}],
            2: [{"id": 28, "num": 60}],
            3: [{"id": 28, "num": 60}],
            4: [{"id": 28, "num": 60}],
            5: [{"id": 41601, "num": 35}],
            6: [{"id": 28, "num": 60}],
            7: [{"id": 28, "num": 60}],
            8: [{"id": 28, "num": 60}],
            9: [{"id": 28, "num": 60}],
            10: [{"id": 1, "num": 800}],
            11: [{"id": 28, "num": 60}],
            12: [{"id": 28, "num": 60}],
            13: [{"id": 28, "num": 60}],
            14: [{"id": 28, "num": 60}],
            15: [{"id": 41601, "num": 35}],
            16: [{"id": 28, "num": 70}],
            17: [{"id": 28, "num": 70}],
            18: [{"id": 28, "num": 70}],
            19: [{"id": 28, "num": 70}],
            20: [{"id": 1, "num": 800}],
        }

        _, exp_num = _item_balance(ctx, self.uid, 45)
        max_claimable_level = 0
        for lvl_idx, req_exp in enumerate(policy_exp_steps, 1):
            if exp_num >= req_exp:
                max_claimable_level = lvl_idx
            else:
                break

        claimed_rows = ctx.db.query("SELECT level FROM polyhedron_policy_claimed WHERE uid=? AND activity_id=?",
                                    (self.uid, activity_id))
        already_claimed = {r["level"] for r in claimed_rows}

        new_claimed_levels = []
        if ctype == 1:
            for lvl in range(1, max_claimable_level + 1):
                if lvl not in already_claimed:
                    new_claimed_levels.append(lvl)
        else:
            # 单个领取：校验 target_level 在有效范围 1~20 且未被领取，且经验达标
            req_exp = policy_exp_steps[target_level - 1] if 1 <= target_level <= len(policy_exp_steps) else 999999
            if 1 <= target_level <= 20 and target_level not in already_claimed and exp_num >= req_exp:
                new_claimed_levels.append(target_level)

        item_map = {}
        if new_claimed_levels:
            for lvl in new_claimed_levels:
                ctx.db.execute(
                    "INSERT OR REPLACE INTO polyhedron_policy_claimed (uid, activity_id, level, update_ts) VALUES (?, ?, ?, ?)",
                    (self.uid, activity_id, lvl, now)
                )
                for it in policy_rewards.get(lvl, [{"id": 28, "num": 60}]):
                    item_map[it["id"]] = item_map.get(it["id"], 0) + it["num"]

            items = [{"id": iid, "num": inum} for iid, inum in item_map.items()]
            for it in items:
                _item_add(ctx, self.uid, it["id"], it["num"])
        else:
            items = []

        return {"items": items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_66001", {
            "result": 0,
            "reward_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_66000 -> 领取多维历程政策奖励 奖励={result.get('items')}")
        extra = _refresh_frames(ctx, self.uid) + _build_18003_frame(ctx, None, uid=self.uid)
        return [DownFrame(self.sc, payload)] + extra


@operation
class PolyhedronResetTerminalOp(Operation):
    """cs_66002 {} -> sc_66003 {result: 0} 重置多维终端升级"""
    cmd = 66002
    sc = 66003

    def apply(self, data, ctx):
        ctx.db.execute("DELETE FROM polyhedron_terminal WHERE uid = ?", (self.uid,))
        ctx.db.execute("UPDATE polyhedron_meta SET reset_times = reset_times + 1, update_ts = ? WHERE uid = ?",
                       (int(time.time()), self.uid))
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_66003", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_66002 -> 多维终端重置成功")
        return [DownFrame(self.sc, payload)]


@operation
class PolyhedronAstrolabeEquipOp(Operation):
    """cs_66004 {hero_id, astrolabe_id_list} -> sc_66005 {result: 0} 多维英雄加装/调整神格"""
    cmd = 66004
    sc = 66005

    def apply(self, data, ctx):
        hero_id = int(data.get("hero_id") or 0)
        astros = data.get("astrolabe_id_list") or []
        a1 = astros[0] if len(astros) > 0 else 0
        a2 = astros[1] if len(astros) > 1 else 0
        a3 = astros[2] if len(astros) > 2 else 0
        if hero_id > 0:
            ctx.db.upsert("polyhedron_hero", self.uid, {
                "hero_id": hero_id,
                "astrolabe_1": a1,
                "astrolabe_2": a2,
                "astrolabe_3": a3,
                "update_ts": int(time.time())
            }, keys=("uid", "hero_id"))
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_66005", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_66004 -> 多维英雄神格更新 hero_id={data.get('hero_id')} astrolabes={data.get('astrolabe_id_list')}")
        return [DownFrame(self.sc, payload)]


@operation
class PolyhedronUnlockBeaconOp(Operation):
    """cs_66034 {beacon_id} -> sc_66035 {result: 0} 解锁多维信标"""
    cmd = 66034
    sc = 66035

    def apply(self, data, ctx):
        beacon_id = int(data.get("beacon_id") or 0)
        if beacon_id > 0:
            ctx.db.upsert("polyhedron_beacon", self.uid, {
                "beacon_id": beacon_id,
                "update_ts": int(time.time())
            }, keys=("uid", "beacon_id"))
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_66035", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_66034 -> 多维解锁信标 beacon_id={data.get('beacon_id')}")
        return [DownFrame(self.sc, payload)]


@operation
class PolyhedronUnlockHeroOp(Operation):
    """cs_66036 {hero_id} -> sc_66037 {result: 0} 消耗映射仪解锁多维初始英雄"""
    cmd = 66036
    sc = 66037

    def apply(self, data, ctx):
        hero_id = int(data.get("hero_id") or 0)
        if hero_id <= 0:
            return {"result": 5}  # 错误参数
        
        # 检查/扣除 1 个映射仪 (item_id=44)
        _, have = _item_balance(ctx, self.uid, 44)
        if have < 1:
            ctx.log(f"cs_66036 -> 解锁多维英雄失败: 映射仪(44)不足 当前={have}")
            return {"result": 12}  # 材料不足
        
        _item_deduct(ctx, self.uid, 44, 1)
        
        # 存入 polyhedron_hero 表持久化
        ctx.db.upsert("polyhedron_hero", self.uid, {
            "hero_id": hero_id,
            "astrolabe_1": 0,
            "astrolabe_2": 0,
            "astrolabe_3": 0,
            "update_ts": int(time.time())
        }, keys=("uid", "hero_id"))
        
        ctx.log(f"cs_66036 -> 多维解锁英雄成功 hero_id={hero_id}, 消耗映射仪×1, 剩余={have-1}")
        return {"result": 0, "hero_id": hero_id}

    def respond(self, result, data, ctx):
        res_code = result.get("result", 0)
        payload = ctx.codec_encode("sc_66037", {"result": res_code}) or b"\x08\x00"
        if res_code == 0:
            return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)
        return [DownFrame(self.sc, payload)]


@operation
class PolyhedronComboSkillOp(Operation):
    """cs_66038 {cooperate_unique_skill_id} -> sc_66039 {result: 0} 多维设置连携奥义"""
    cmd = 66038
    sc = 66039

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_66039", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_66038 -> 多维设置连携奥义 skill_id={data.get('cooperate_unique_skill_id')}")
        return [DownFrame(self.sc, payload)]


# ----------------- 4. 迭代校验 (Core Verification) -----------------

@operation
class CoreVerificationGetRewardOp(Operation):
    """cs_75006 {reward_list} -> sc_75007 {result: 0, reward_list} 领取迭代校验常规任务奖励"""
    cmd = 75006
    sc = 75007

    def apply(self, data, ctx):
        import weekly_challenge_service as _wcs
        r_list = data.get("reward_list") or []
        if isinstance(r_list, (int, str)):
            r_list = [int(r_list)]
        
        now_cycle = _wcs.get_core_verification_cycle()
        rew_cat = _wcs.get_core_verification_rewards_catalog()

        # 读取当前周期通关战绩，计算 Boss 1 和 Boss 2 当前最高通关难度
        records = {}
        if ctx.db and hasattr(ctx.db, "get_core_verification_stage_records"):
            records = ctx.db.get_core_verification_stage_records(self.uid, now_cycle)
        
        max_diff1 = max([r["difficult"] for r in records.values() if r["boss_type"] == 1 and r["sign"] == 1] or [0])
        max_diff2 = max([r["difficult"] for r in records.values() if r["boss_type"] == 2 and r["sign"] == 1] or [0])
        min_diff = min(max_diff1, max_diff2)

        granted_items = []
        for t_id_raw in r_list:
            t_id = int(t_id_raw)
            t_obj = rew_cat.get(str(t_id)) or {}
            rtype = int(t_obj.get("reward_type") or 0)

            # 1. 检查是否已领过
            if rtype == 4:
                # 首通奖励：永久一次性（cycle=0 检查）
                if ctx.db and hasattr(ctx.db, "is_core_verification_task_claimed"):
                    if ctx.db.is_core_verification_task_claimed(self.uid, t_id, cycle=0):
                        continue
            else:
                # 常规周常奖励（回归测试α、回归测试β、难度奖励）：按当前周期 cycle 检查
                if ctx.db and hasattr(ctx.db, "is_core_verification_task_claimed"):
                    if ctx.db.is_core_verification_task_claimed(self.uid, t_id, cycle=now_cycle):
                        continue

            # 2. 校验达成条件
            can_claim = False
            if rtype == 1:
                # 回归测试·α: 需要 Boss 1 达到对应难度
                req_diff = t_id % 10
                if max_diff1 >= req_diff and req_diff > 0:
                    can_claim = True
            elif rtype == 2:
                # 回归测试·β: 需要 Boss 2 达到对应难度
                req_diff = t_id % 10
                if max_diff2 >= req_diff and req_diff > 0:
                    can_claim = True
            elif rtype == 3:
                # 难度奖励 (1001..1008): 取两个 BOSS 通关进度的最低值
                req_diff = t_id - 1000
                if min_diff >= req_diff and req_diff > 0:
                    can_claim = True
            elif rtype == 4:
                # 首通奖励: 永久只能领取一次
                if 2001 <= t_id <= 2008:
                    req_diff = t_id - 2000
                    if min_diff >= req_diff:
                        can_claim = True
                elif 2011 <= t_id <= 2018:
                    req_diff = t_id - 2010
                    if max_diff1 >= req_diff:
                        can_claim = True
                elif 2021 <= t_id <= 2028:
                    req_diff = t_id - 2020
                    if max_diff2 >= req_diff:
                        can_claim = True
            else:
                # 兼容未分类条目
                can_claim = True

            if not can_claim:
                continue

            # 3. 发放道具并记录入库
            items = t_obj.get("rewards") or [{"id": 40414, "num": 3}, {"id": 2, "num": 1000}]
            for it in items:
                _item_add(ctx, self.uid, it["id"], it["num"])
                granted_items.append(it)

            if ctx.db and hasattr(ctx.db, "claim_core_verification_task"):
                claim_cycle = 0 if rtype == 4 else now_cycle
                ctx.db.claim_core_verification_task(self.uid, t_id, cycle=claim_cycle)

        return {"items": granted_items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_75007", {
            "result": 0,
            "reward_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_75006 -> 迭代校验领取奖励 tasks={data.get('reward_list')} 实际发放={result.get('items')}")
        extra = []
        if ctx.generator and ctx.db:
            try:
                p_75009 = ctx.generator.gen_payload(75009, uid=self.uid, db=ctx.db)
                if p_75009:
                    extra.append(DownFrame(75009, p_75009))
            except Exception:
                pass
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid) + extra


@operation
class CoreVerificationResetChallengeOp(Operation):
    """cs_75010 {type} -> sc_75011 {result: 0} 重置迭代校验关卡/阵容"""
    cmd = 75010
    sc = 75011

    def apply(self, data, ctx):
        import weekly_challenge_service as _wcs
        r_type = int(data.get("type") or 0)
        cycle = _wcs.get_core_verification_cycle()
        if ctx.db and hasattr(ctx.db, "reset_core_verification_challenge"):
            ctx.db.reset_core_verification_challenge(self.uid, cycle, r_type)
        return {"result": 0, "type": r_type}

    def respond(self, result, data, ctx):
        import weekly_challenge_service as _wcs
        payload = ctx.codec_encode("sc_75011", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_75010 -> 迭代校验重置挑战 type={data.get('type')}")
        frames = [DownFrame(self.sc, payload)]
        if ctx.codec_encode:
            d = _wcs.get_core_verification_data(self.uid, ctx.db)
            p09 = ctx.codec_encode("sc_75009", d)
            if p09:
                frames.append(DownFrame(75009, p09))
        return frames


@operation
class CoreVerificationSelectSuffixOp(Operation):
    """cs_75012 {affix_info} -> sc_75013 {result: 0} 保存迭代校验自选词缀"""
    cmd = 75012
    sc = 75013

    def apply(self, data, ctx):
        info = data.get("affix_info") or {}
        mode_id = int(info.get("id") or 1)
        affixes = info.get("affix_list") or []
        if ctx.db and hasattr(ctx.db, "save_core_verification_affix"):
            ctx.db.save_core_verification_affix(self.uid, mode_id, affixes)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_75013", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_75012 -> 迭代校验保存自选词缀 {data.get('affix_info')}")
        return [DownFrame(self.sc, payload)]


# ----------------- 迭代校验·挑战模式 (Mode 1 ~ Mode 4) -----------------

@operation
class CoreVerificationChallengeSetAffixOp(Operation):
    """cs_89014 {activity_id, buff_list} -> sc_89015 {result: 0} Mode 1 保存出战词缀"""
    cmd = 89014
    sc = 89015

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        buffs = data.get("buff_list") or []
        if ctx.db and hasattr(ctx.db, "save_core_verification_cl_buffs"):
            ctx.db.save_core_verification_cl_buffs(self.uid, act_id, buffs)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89015", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_89014 -> 挑战模式Mode1保存词条 {data}")
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationChallengeGetRewardOp(Operation):
    """cs_89018 {activity_id, assignment_id} -> sc_89019 {result: 0, reward_list} Mode 1 领取任务奖励"""
    cmd = 89018
    sc = 89019

    def apply(self, data, ctx):
        import weekly_challenge_service as _wcs
        act_id = int(data.get("activity_id") or 0)
        ass_id = data.get("assignment_id") or []
        if isinstance(ass_id, (int, str)):
            ass_id = [int(ass_id)]
        
        cl_cat = _wcs.get_core_verification_cl_catalog()
        rewards_map = cl_cat.get("rewards", {})
        granted_items = []
        for tid in ass_id:
            r_info = rewards_map.get(str(tid)) or {}
            items = r_info.get("rewards") or [{"id": 1, "num": 100}]
            for it in items:
                _item_add(ctx, self.uid, it["id"], it["num"])
                granted_items.append(it)
            if ctx.db and hasattr(ctx.db, "claim_core_verification_cl_task"):
                ctx.db.claim_core_verification_cl_task(self.uid, act_id, int(tid))
        
        if not granted_items:
            granted_items = [{"id": 1, "num": 100}]
            for it in granted_items:
                _item_add(ctx, self.uid, it["id"], it["num"])

        return {"items": granted_items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89019", {
            "result": 0,
            "reward_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_89018 -> 挑战模式Mode1领奖 tasks={data.get('assignment_id')} 奖励={result.get('items')}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class CoreVerificationChallengeResetOp(Operation):
    """cs_89016 {activity_id} -> sc_89017 {result: 0} Mode 1 重置全部挑战"""
    cmd = 89016
    sc = 89017

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_core_verification_cl"):
            ctx.db.reset_core_verification_cl(self.uid, act_id, stage_id=0)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_89016 -> 挑战模式Mode1重置全部 {data}")
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationChallengeResetCurStageOp(Operation):
    """cs_89022 {activity_id, stage_id} -> sc_89023 {result: 0} Mode 1 重置单关"""
    cmd = 89022
    sc = 89023

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        stg_id = int(data.get("stage_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_core_verification_cl"):
            ctx.db.reset_core_verification_cl(self.uid, act_id, stage_id=stg_id)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89023", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_89022 -> 挑战模式Mode1重置单关 {data}")
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationChallengeSetSeasonTipOp(Operation):
    """cs_89036 {activity_id} -> sc_89037 {result: 0} Mode 1 赛季提示标记"""
    cmd = 89036
    sc = 89037

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89037", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


# Mode 2
@operation
class CoreVerificationMode2SetAffixOp(Operation):
    """cs_89026 {activity_id, buff_list} -> sc_89027 {result: 0} Mode 2 保存词缀"""
    cmd = 89026
    sc = 89027

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        buffs = data.get("buff_list") or []
        if ctx.db and hasattr(ctx.db, "save_core_verification_cl_buffs"):
            ctx.db.save_core_verification_cl_buffs(self.uid, act_id, buffs)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89027", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_89026 -> 挑战模式Mode2保存词条 {data}")
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationMode2GetRewardOp(Operation):
    """cs_89030 {activity_id, assignment_id} -> sc_89031 {result: 0, reward_list} Mode 2 领奖"""
    cmd = 89030
    sc = 89031

    def apply(self, data, ctx):
        import weekly_challenge_service as _wcs
        act_id = int(data.get("activity_id") or 0)
        ass_id = data.get("assignment_id") or []
        if isinstance(ass_id, (int, str)):
            ass_id = [int(ass_id)]
        
        cl_cat = _wcs.get_core_verification_cl_catalog()
        rewards_map = cl_cat.get("rewards", {})
        granted_items = []
        for tid in ass_id:
            r_info = rewards_map.get(str(tid)) or {}
            items = r_info.get("rewards") or [{"id": 1, "num": 100}]
            for it in items:
                _item_add(ctx, self.uid, it["id"], it["num"])
                granted_items.append(it)
            if ctx.db and hasattr(ctx.db, "claim_core_verification_cl_task"):
                ctx.db.claim_core_verification_cl_task(self.uid, act_id, int(tid))
        
        if not granted_items:
            granted_items = [{"id": 1, "num": 100}]
            for it in granted_items:
                _item_add(ctx, self.uid, it["id"], it["num"])

        return {"items": granted_items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89031", {
            "result": 0,
            "reward_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_89030 -> 挑战模式Mode2领奖 {data} 奖励={result.get('items')}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class CoreVerificationMode2ResetOp(Operation):
    """cs_89028 {activity_id} -> sc_89029 {result: 0} Mode 2 重置全部"""
    cmd = 89028
    sc = 89029

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_core_verification_cl"):
            ctx.db.reset_core_verification_cl(self.uid, act_id, stage_id=0)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89029", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationMode2ResetStageOp(Operation):
    """cs_89034 {activity_id, stage_id} -> sc_89035 {result: 0} Mode 2 重置单关"""
    cmd = 89034
    sc = 89035

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        stg_id = int(data.get("stage_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_core_verification_cl"):
            ctx.db.reset_core_verification_cl(self.uid, act_id, stage_id=stg_id)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89035", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationMode2SetSeasonTipOp(Operation):
    """cs_89038 {activity_id} -> sc_89039 {result: 0} Mode 2 赛季提示"""
    cmd = 89038
    sc = 89039

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89039", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


# Mode 3
@operation
class CoreVerificationMode3GetRewardOp(Operation):
    """cs_89434 {activity_id, assignment_id} -> sc_89435 {result: 0, reward_list} Mode 3 领奖"""
    cmd = 89434
    sc = 89435

    def apply(self, data, ctx):
        import weekly_challenge_service as _wcs
        act_id = int(data.get("activity_id") or 0)
        ass_id = data.get("assignment_id") or []
        if isinstance(ass_id, (int, str)):
            ass_id = [int(ass_id)]
        
        cl_cat = _wcs.get_core_verification_cl_catalog()
        rewards_map = cl_cat.get("rewards", {})
        granted_items = []
        for tid in ass_id:
            r_info = rewards_map.get(str(tid)) or {}
            items = r_info.get("rewards") or [{"id": 1, "num": 100}]
            for it in items:
                _item_add(ctx, self.uid, it["id"], it["num"])
                granted_items.append(it)
            if ctx.db and hasattr(ctx.db, "claim_core_verification_cl_task"):
                ctx.db.claim_core_verification_cl_task(self.uid, act_id, int(tid))
        
        if not granted_items:
            granted_items = [{"id": 1, "num": 100}]
            for it in granted_items:
                _item_add(ctx, self.uid, it["id"], it["num"])

        return {"items": granted_items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89435", {
            "result": 0,
            "reward_list": result.get("items", [])
        }) or b"\x08\x00"
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class CoreVerificationMode3ResetOp(Operation):
    """cs_89432 {activity_id} -> sc_89433 {result: 0} Mode 3 重置全部"""
    cmd = 89432
    sc = 89433

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_core_verification_cl"):
            ctx.db.reset_core_verification_cl(self.uid, act_id, stage_id=0)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89433", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationMode3ResetStageOp(Operation):
    """cs_89436 {activity_id, stage_id} -> sc_89437 {result: 0} Mode 3 重置单关"""
    cmd = 89436
    sc = 89437

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        stg_id = int(data.get("stage_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_core_verification_cl"):
            ctx.db.reset_core_verification_cl(self.uid, act_id, stage_id=stg_id)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89437", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationMode3SetSeasonTipOp(Operation):
    """cs_89438 {activity_id} -> sc_89439 {result: 0} Mode 3 赛季提示"""
    cmd = 89438
    sc = 89439

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89439", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


# Mode 4
@operation
class CoreVerificationMode4ResetTeamOp(Operation):
    """cs_89802 {activity_id, stage_id, team_index} -> sc_89803 {result: 0} Mode 4 重置单队"""
    cmd = 89802
    sc = 89803

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89803", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class CoreVerificationMode4GetRewardOp(Operation):
    """cs_89804 {activity_id, assignment_id} -> sc_89805 {result: 0, reward_list} Mode 4 领奖"""
    cmd = 89804
    sc = 89805

    def apply(self, data, ctx):
        import weekly_challenge_service as _wcs
        act_id = int(data.get("activity_id") or 0)
        ass_id = data.get("assignment_id") or []
        if isinstance(ass_id, (int, str)):
            ass_id = [int(ass_id)]
        
        cl_cat = _wcs.get_core_verification_cl_catalog()
        rewards_map = cl_cat.get("rewards", {})
        granted_items = []
        for tid in ass_id:
            r_info = rewards_map.get(str(tid)) or {}
            items = r_info.get("rewards") or [{"id": 1, "num": 100}]
            for it in items:
                _item_add(ctx, self.uid, it["id"], it["num"])
                granted_items.append(it)
            if ctx.db and hasattr(ctx.db, "claim_core_verification_cl_task"):
                ctx.db.claim_core_verification_cl_task(self.uid, act_id, int(tid))
        
        if not granted_items:
            granted_items = [{"id": 1, "num": 100}]
            for it in granted_items:
                _item_add(ctx, self.uid, it["id"], it["num"])

        return {"items": granted_items}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89805", {
            "result": 0,
            "reward_list": result.get("items", [])
        }) or b"\x08\x00"
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class CoreVerificationMode4ResetStageOp(Operation):
    """cs_89806 {stage_id, activity_id} -> sc_89807 {result: 0} Mode 4 重置关卡/全部"""
    cmd = 89806
    sc = 89807

    def apply(self, data, ctx):
        act_id = int(data.get("activity_id") or 0)
        stg_id = int(data.get("stage_id") or 0)
        if ctx.db and hasattr(ctx.db, "reset_core_verification_cl"):
            ctx.db.reset_core_verification_cl(self.uid, act_id, stage_id=stg_id)
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_89807", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class PlayerReadUnclaimedMessageOp(Operation):
    """cs_12024 {id} -> sc_12025 {result: 0} 已读未领取补偿邮件提示"""
    cmd = 12024
    sc = 12025

    def apply(self, data, ctx):
        unclaimed_id = int(data.get("id") or 0)
        if ctx.db and hasattr(ctx.db, "execute") and unclaimed_id:
            ctx.db.execute("DELETE FROM unclaimed WHERE uid=? AND unclaimed_id=?", (self.uid, unclaimed_id))
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_12025", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_12024 -> 已读未领取挑战补偿提示 id={data.get('id')}")
        return [DownFrame(self.sc, payload)]


# ----------------- 5. 介质攫取 (Equip Seizure) 积分奖励领取 -----------------

@operation
class EquipSeizureClaimRewardOp(Operation):
    """cs_35014 {id_list} -> sc_35015 {result: 0, reward_list} 领取介质攫取积分奖励（单领 / 一键批量）"""
    cmd = 35014
    sc = 35015

    def parse(self, req, ctx):
        d = super().parse(req, ctx)
        if not isinstance(d, dict):
            return {"id_list": []}
        return d

    def apply(self, data, ctx):
        raw_ids = data.get("id_list") or []
        if isinstance(raw_ids, (int, str)):
            raw_ids = [int(raw_ids)]
        req_ids = [int(x) for x in raw_ids if x]

        import weekly_challenge_service as _wcs
        cat = _wcs.get_equip_seizure_catalog()
        reward_cfg_map = cat.get("rewards") or {}

        prog = ctx.db.get_equip_seizure_progress(self.uid) if ctx.db else {}
        sum_score = int(prog.get("sum_score") or 0)
        already_claimed = set(prog.get("got_reward_id_list") or [])

        claimed_ids = []
        total_items_map = {}

        for rid in req_ids:
            if rid in already_claimed:
                continue
            r_cfg = reward_cfg_map.get(str(rid)) or reward_cfg_map.get(rid)
            if not r_cfg:
                continue
            need_score = int(r_cfg.get("need") or 0)
            if sum_score < need_score:
                continue

            claimed_ids.append(rid)
            for itm in r_cfg.get("reward_item_list", []):
                iid = int(itm.get("id") or 0)
                inum = int(itm.get("num") or 0)
                if iid and inum:
                    total_items_map[iid] = total_items_map.get(iid, 0) + inum

        # 道具动态路由发放入库
        for iid, inum in total_items_map.items():
            _item_add(ctx, self.uid, iid, inum)

        # 持久化记录已领取状态
        if claimed_ids and ctx.db and hasattr(ctx.db, "claim_equip_seizure_rewards"):
            ctx.db.claim_equip_seizure_rewards(self.uid, claimed_ids)

        out_items = [{"id": k, "num": v} for k, v in total_items_map.items()]
        return {"items": out_items, "claimed_ids": claimed_ids}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_35015", {
            "result": 0,
            "reward_list": result.get("items", [])
        }) or b"\x08\x00"
        ctx.log(f"cs_35014 -> 介质攫取领取积分奖励 档位={result.get('claimed_ids')} 获得={result.get('items')}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


# =========================================================================
# 因果观测（WarChess 49xxx 战棋玩法）操作集
# =========================================================================

@operation
class WarChessEnterOp(Operation):
    """请求进入因果观测地图（cs_49002 -> sc_49003）。"""

    cmd = 49002
    sc = 49003

    def parse(self, req, ctx):
        d = super().parse(req, ctx)
        if not isinstance(d, dict):
            return {"chapter_id": 4040101}
        return d

    def apply(self, data, ctx):
        chapter_id = int(data.get("chapter_id") or 4040101)
        if ctx.db:
            session = ctx.db.get_or_create_warchess_session(self.uid, chapter_id)
        else:
            session = {
                "pos": {"x": 0.0, "z": 0.0},
                "direction": 0,
                "is_fog": False,
                "fog": [100, 100],
                "map": [],
                "bag": {"item": [], "artifact": []},
                "hp_list": [],
                "log": [],
                "event_list": [],
                "is_viewed_log": False,
                "guide_pos": [],
                "event_info": [],
            }
        return {"chapter_id": chapter_id, "session": session}

    def respond(self, result, data, ctx):
        session = result.get("session") or {}
        payload = ctx.codec_encode("sc_49003", {
            "result": 0,
            "map_info": session
        }) or b"\x08\x00"
        ctx.log(f"cs_49002 -> 请求进入因果观测地图 章节={result.get('chapter_id')}")
        return [DownFrame(self.sc, payload)]


@operation
class WarChessMoveOp(Operation):
    """因果观测角色移动路径同步（cs_49004 -> sc_49005）。"""

    cmd = 49004
    sc = 49005

    def parse(self, req, ctx):
        d = super().parse(req, ctx)
        if not isinstance(d, dict):
            return {"path": []}
        return d

    def apply(self, data, ctx):
        path = data.get("path") or []
        if path and ctx.db:
            last_pos = path[-1]
            x = float(last_pos.get("x", 0.0))
            z = float(last_pos.get("z", 0.0))
            row = ctx.db.query("SELECT current_chapter FROM warchess_map WHERE uid = ? AND activity_id = 0", (self.uid,))
            cur_ch = int(row[0]["current_chapter"]) if row else 0
            if cur_ch > 0:
                ctx.db.update_warchess_session_pos(self.uid, cur_ch, x, z)
        return {"success": True}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_49005", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


_WARCHESS_EVENT_INDEX = None

def _get_warchess_event_index():
    global _WARCHESS_EVENT_INDEX
    if _WARCHESS_EVENT_INDEX is None:
        p = os.path.join(os.path.dirname(__file__), "static_resources", "warchess_event_index.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _WARCHESS_EVENT_INDEX = json.load(f)
            except Exception:
                _WARCHESS_EVENT_INDEX = {}
        else:
            _WARCHESS_EVENT_INDEX = {}
    return _WARCHESS_EVENT_INDEX


@operation
class WarChessInteractOp(Operation):
    """因果观测地块交互/触发事件（cs_49006 -> sc_49007）。"""

    cmd = 49006
    sc = 49007

    def parse(self, req, ctx):
        d = super().parse(req, ctx)
        if not isinstance(d, dict):
            return {"pos": {"x": 0, "z": 0}, "param": 0, "type": 0}
        return d

    def apply(self, data, ctx):
        pos = data.get("pos") or {}
        x = float(pos.get("x", 0.0))
        z = float(pos.get("z", 0.0))
        param = int(data.get("param") or 0)
        itype = int(data.get("type") or 0)

        row = ctx.db.query("SELECT current_chapter FROM warchess_map WHERE uid = ? AND activity_id = 0", (self.uid,)) if ctx.db else None
        cur_ch = int(row[0]["current_chapter"]) if row else 0

        rewards = []
        ev_idx = _get_warchess_event_index().get(str(cur_ch), {})
        tile_info = ev_idx.get(f"{int(x)},{int(z)}")
        actions = tile_info.get("actions", []) if tile_info else []

        is_box_handled = False
        # 执行该地块在配置中的所有事件（包括连带开门、开箱等）
        for act in actions:
            cmd_id = act[0] if act else 0
            if cmd_id in (10203, 10204):
                is_box_handled = True
                box_id = cmd_id
                if cur_ch > 0 and ctx.db:
                    if not ctx.db.is_warchess_box_opened(self.uid, cur_ch, box_id, 0, x, z):
                        ctx.db.record_warchess_box_opened(self.uid, cur_ch, box_id, 0, x, z)
                        diamond_num = 100 if box_id == 10204 else 50
                        _item_add(ctx, self.uid, 1, diamond_num)
                        _item_add(ctx, self.uid, 2, 5000)
                        rewards.append({"id": 1, "num": diamond_num})
                        rewards.append({"id": 2, "num": 5000})
            elif cmd_id == 10302 and len(act) >= 4:
                # 连带修改其他地块状态（如击败怪兽后自动开门 / 机关联动）
                tx, tz, tstatus = act[1], act[2], act[3]
                if cur_ch > 0 and ctx.db:
                    ctx.db.update_warchess_grid_state(self.uid, cur_ch, tag=0, x=float(tx), z=float(tz), state=tstatus)

        # 兜底：处理非配置驱动的直接宝箱交互
        if param in (10203, 10204, 106031) and not is_box_handled:
            box_id = param
            if cur_ch > 0 and ctx.db:
                if not ctx.db.is_warchess_box_opened(self.uid, cur_ch, box_id, 0, x, z):
                    ctx.db.record_warchess_box_opened(self.uid, cur_ch, box_id, 0, x, z)
                    diamond_num = 100 if box_id == 10204 else (50 if box_id == 10203 else 20)
                    _item_add(ctx, self.uid, 1, diamond_num)
                    _item_add(ctx, self.uid, 2, 5000)
                    rewards.append({"id": 1, "num": diamond_num})
                    rewards.append({"id": 2, "num": 5000})

        # 记录神格/圣物与地块自身状态
        if cur_ch > 0 and ctx.db:
            if param and 1000 <= param <= 9999:
                ctx.db.add_warchess_artifact(self.uid, cur_ch, param)
            ctx.db.update_warchess_grid_state(self.uid, cur_ch, tag=0, x=x, z=z, state=1, attribute=[param] if param else [])

        return {"cur_ch": cur_ch, "pos": (x, z), "param": param, "type": itype, "rewards": rewards}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_49007", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_49006 -> 因果观测地块交互 章节={result.get('cur_ch')} 坐标={result.get('pos')} param={result.get('param')} type={result.get('type')}")
        frames = [DownFrame(self.sc, payload)]
        if result.get("rewards"):
            frames.extend(_refresh_frames(ctx, self.uid))
        return frames


@operation
class WarChessQuitOp(Operation):
    """因果观测放弃/退出/完成探索（cs_49008 -> sc_49009）。"""

    cmd = 49008
    sc = 49009

    def apply(self, data, ctx):
        if ctx.db:
            ctx.db.clear_warchess_session(self.uid)
        return {"success": True}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_49009", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_49008 -> 因果观测完成/退出探索")
        return [DownFrame(self.sc, payload)]


@operation
class WarChessFireOp(Operation):
    """因果观测玩家开炮（cs_49016 -> sc_49017）。"""

    cmd = 49016
    sc = 49017

    def apply(self, data, ctx):
        return {"success": True}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_49017", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_49016 -> 因果观测玩家开炮")
        return [DownFrame(self.sc, payload)]


@operation
class WarChessSwitchControlOp(Operation):
    """因果观测切换控制模式（cs_49024 -> sc_49025）。"""

    cmd = 49024
    sc = 49025

    def apply(self, data, ctx):
        return {"success": True}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_49025", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_49024 -> 因果观测切换控制模式")
        return [DownFrame(self.sc, payload)]


# ==============================================================================
# 签到与每日福利大工程新增操作集 (BigMonthCard / DailyFatigue / Noob / Regression)
# ==============================================================================

@operation
class BigMonthCardSignOp(Operation):
    """34110 大月卡今日签到：cs_34110 {} → sc_34111 {result, rewards, daily_record, is_sign, total_sign_times, total_sign_receive_list}。

    百宝囊/恒定观测每日签到抽取：从 6 种材料池（每种上限 5 份，合计 30 天 30 份）中随机抽取 1 份，
    并在 daily_record 中记录各材料已领次数。
    """

    cmd = 34110
    sc = 34111

    # 6 种每日材料签到池配置 (GameSetting.big_monthly_card_reward_daily.value)
    # 1-based index: (item_id, item_num, max_limit)
    DAILY_MATERIAL_POOL = {
        1: (40103, 3, 5),   # 作战记录 x 3 (上限5份)
        2: (40203, 3, 5),   # 启示录C01 x 3 (上限5份)
        3: (40803, 4, 5),   # 神力因子 x 4 (上限5份)
        4: (40701, 1, 5),   # 铸炼结晶 x 1 (上限5份)
        5: (40504, 5, 5),   # 技能源质 x 5 (上限5份)
        6: (40603, 2, 5),   # 刻印核心 x 2 (上限5份)
    }

    def apply(self, data, ctx):
        import random as _rnd
        uid = self.uid
        now_ts = int(time.time())
        brow = ctx.db.query("SELECT * FROM big_month_card WHERE uid=?", (uid,))
        brow = brow[0] if brow else {}

        try:
            rec_list = json.loads(brow.get("total_sign_receive_list") or "[]")
        except Exception:
            rec_list = []
        try:
            raw_daily_rec = json.loads(brow.get("daily_record") or "[]")
        except Exception:
            raw_daily_rec = []

        # 整理已领材料次数映射: {index: times}
        rec_map = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
        for item in raw_daily_rec:
            if isinstance(item, dict) and "index" in item:
                rec_map[int(item["index"])] = int(item.get("times", 0))

        # 每日一次守卫：今日已签则幂等返回当前状态，不重复累加天数与发奖
        _bts = brow.get("update_ts") or 0
        _bsigned = False
        if brow.get("is_sign") == 1 and _bts:
            _a, _b = time.localtime(int(_bts)), time.localtime()
            _bsigned = (_a.tm_year, _a.tm_mon, _a.tm_mday) == (_b.tm_year, _b.tm_mon, _b.tm_mday)

        if _bsigned:
            daily_rec = [{"index": k, "times": v} for k, v in sorted(rec_map.items())]
            return {
                "already": True,
                "rewards": [],
                "daily_record": daily_rec,
                "is_sign": 1,
                "total_sign_times": int(brow.get("total_sign_times") or 0),
                "total_sign_receive_list": rec_list
            }

        total_times = int(brow.get("total_sign_times") or 0) + 1

        # 找出仍有剩余份额的材料候选池 (times < 5)
        avail = [idx for idx, cfg in self.DAILY_MATERIAL_POOL.items() if rec_map.get(idx, 0) < cfg[2]]

        rewards = []
        # 1. 每日随机抽取 1 份签到材料 (若30份全抽完则保底给经验)
        if avail:
            chosen_idx = _rnd.choice(avail)
            rec_map[chosen_idx] = rec_map.get(chosen_idx, 0) + 1
            mat_id, mat_num, _ = self.DAILY_MATERIAL_POOL[chosen_idx]
            _item_add(ctx, uid, mat_id, mat_num)
            rewards.append({"id": mat_id, "num": mat_num})
        else:
            _item_add(ctx, uid, 40103, 3)
            rewards.append({"id": 40103, "num": 3})

        # 2. 每日大月卡基础额外奖励 (100 移转之辉)
        _item_add(ctx, uid, 1, 100)
        rewards.append({"id": 1, "num": 100})

        # 3. 累计天数档位奖励（BigMonthCardAccumulationCfg）
        template_id = int(brow.get("template_id") or 2)
        milestones = {
            201: (1, 38, 10),
            202: (7, 1, 200),
            203: (14, 19, 10),
            204: (21, 1, 300)
        } if template_id == 2 else {
            101: (1, 1, 200),
            102: (7, 38, 10),
            103: (14, 1, 300),
            104: (21, 19, 10)
        }
        for mid, (need_days, iid, inum) in milestones.items():
            if total_times >= need_days and mid not in rec_list:
                rec_list.append(mid)
                _item_add(ctx, uid, iid, inum)
                rewards.append({"id": iid, "num": inum})

        daily_rec = [{"index": k, "times": v} for k, v in sorted(rec_map.items())]

        ctx.db.execute(
            "INSERT INTO big_month_card (uid, buy_timestamp, is_sign, total_sign_times, total_sign_receive_list, daily_record, template_id, update_ts) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET is_sign=1, total_sign_times=excluded.total_sign_times, "
            "total_sign_receive_list=excluded.total_sign_receive_list, daily_record=excluded.daily_record, update_ts=excluded.update_ts",
            (uid, int(brow.get("buy_timestamp") or now_ts), total_times, json.dumps(rec_list), json.dumps(daily_rec), template_id, now_ts)
        )
        try:
            event_bus.bus.emit(event_bus.Events.USER_LOGIN, ctx, self.uid, is_first_login=True)
        except Exception:
            pass
        return {
            "already": False,
            "rewards": rewards,
            "daily_record": daily_rec,
            "is_sign": 1,
            "total_sign_times": total_times,
            "total_sign_receive_list": rec_list
        }

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_34111", {
            "result": 0,
            "rewards": result["rewards"],
            "daily_record": result["daily_record"],
            "is_sign": result["is_sign"],
            "total_sign_times": result["total_sign_times"],
            "total_sign_receive_list": result["total_sign_receive_list"]
        }) or b"\x08\x00"
        ctx.log(f"cs_34110 -> 恒定观测(大月卡)签到成功，获得={result['rewards']}")
        frames = [DownFrame(self.sc, payload)]
        if ctx.generator is not None:
            try:
                p01 = ctx.generator.gen_payload(34101, uid=self.uid, db=ctx.db)
                if p01:
                    frames.append(DownFrame(34101, p01))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class BigMonthCardBuyOp(Operation):
    """34114 购买大月卡：cs_34114 {} → sc_34115 {result}。"""

    cmd = 34114
    sc = 34115

    def apply(self, data, ctx):
        uid = self.uid
        now_ts = int(time.time())
        ctx.db.execute(
            "INSERT INTO big_month_card (uid, buy_timestamp, is_sign, total_sign_times, total_sign_receive_list, daily_record, template_id, update_ts) "
            "VALUES (?, ?, 0, 0, '[]', '[]', 2, ?) "
            "ON CONFLICT(uid) DO UPDATE SET buy_timestamp=excluded.buy_timestamp, is_sign=0, total_sign_times=0, "
            "total_sign_receive_list='[]', update_ts=excluded.update_ts",
            (uid, now_ts, now_ts)
        )
        return {}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_34115", {"result": 0}) or b"\x08\x00"
        ctx.log("cs_34114 -> 购买大月卡成功")
        frames = [DownFrame(self.sc, payload)]
        if ctx.generator is not None:
            try:
                p01 = ctx.generator.gen_payload(34101, uid=self.uid, db=ctx.db)
                if p01:
                    frames.append(DownFrame(34101, p01))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class BigMonthCardExpireTipOp(Operation):
    """34116 大月卡过期提示已读：cs_34116 {} → sc_34117 {result}。"""

    cmd = 34116
    sc = 34117

    def apply(self, data, ctx):
        ctx.db.execute("UPDATE big_month_card SET is_expire_tip = 0 WHERE uid = ?", (self.uid,))
        return {}

    def respond(self, result, data, ctx):
        return [DownFrame(self.sc, ctx.codec_encode("sc_34117", {"result": 0}) or b"\x08\x00")]


@operation
class DailyFatigueOp(Operation):
    """12046 领取每日免费体力甜点（百宝囊上下午领蛋）：cs_12046 {type} → sc_12047 {result}。"""

    cmd = 12046
    sc = 12047

    def apply(self, data, ctx):
        uid = self.uid
        now_ts = int(time.time())
        today_str = time.strftime("%Y-%m-%d")
        dessert_type = int(data.get("type") or 11)

        # 发放 60 体力 (CurrencyConst.CURRENCY_TYPE_FATIGUE = item 4)
        _item_add(ctx, uid, 4, 60)
        ctx.db.execute(
            "INSERT OR REPLACE INTO daily_fatigue (uid, type, date_str, update_ts) VALUES (?, ?, ?, ?)",
            (uid, dessert_type, today_str, now_ts)
        )
        return {"type": dessert_type}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_12047", {"result": 0}) or b"\x08\x00"
        ctx.log(f"cs_12046 -> 领取每日免费体力甜点 type={result['type']} (+60 体力)")
        frames = [DownFrame(self.sc, payload)]
        if ctx.generator is not None:
            try:
                p45 = ctx.generator.gen_payload(12045, uid=self.uid, db=ctx.db)
                if p45:
                    frames.append(DownFrame(12045, p45))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class NoobSignInOp(Operation):
    """59012 新手每日签到：cs_59012 {} → sc_59013 {result, reward_list}。"""

    cmd = 59012
    sc = 59013

    def apply(self, data, ctx):
        uid = self.uid
        now_ts = int(time.time())
        row = ctx.db.query("SELECT * FROM newbie_activity WHERE uid=?", (uid,))
        r = row[0] if row else {}
        times = int(r.get("now_sign_times") or 0) + 1
        newbie_rewards = {
            1: (38, 5), 2: (2, 20000), 3: (41002, 10), 4: (38, 5),
            5: (40504, 20), 6: (41301, 20), 7: (1, 500), 8: (38, 5),
            9: (2, 30000), 10: (41002, 15), 11: (38, 5), 12: (40504, 30),
            13: (41301, 30), 14: (1, 1000)
        }
        rw = newbie_rewards.get(times, (38, 1))
        _item_add(ctx, uid, rw[0], rw[1])
        ctx.db.execute(
            "UPDATE newbie_activity SET now_sign_times=?, last_sign_ts=?, update_ts=? WHERE uid=?",
            (times, now_ts, now_ts, uid)
        )
        return {"rewards": [{"id": rw[0], "num": rw[1]}]}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_59013", {"result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log(f"cs_59012 -> 新手每日签到成功，获得={result['rewards']}")
        frames = [DownFrame(self.sc, payload)]
        if ctx.generator is not None:
            try:
                p11 = ctx.generator.gen_payload(59011, uid=self.uid, db=ctx.db)
                if p11:
                    frames.append(DownFrame(59011, p11))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class NoobLevelRewardOp(Operation):
    """59004 新手等级奖励：cs_59004 {level} → sc_59005 {result, reward_list}。"""

    cmd = 59004
    sc = 59005

    def apply(self, data, ctx):
        uid = self.uid
        lvl = int(data.get("level") or 10)
        _item_add(ctx, uid, 1, 200)
        ctx.db.execute(
            "INSERT OR REPLACE INTO newbie_level_reward (uid, level, update_ts) VALUES (?, ?, ?)",
            (uid, lvl, int(time.time()))
        )
        return {"rewards": [{"id": 1, "num": 200}]}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_59005", {"result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log(f"cs_59004 -> 领取新手等级奖励，获得={result['rewards']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class NoobAccumulateRewardOp(Operation):
    """59014 新手任务累计进度奖励：cs_59014 {id} → sc_59015 {result, reward_list}。"""

    cmd = 59014
    sc = 59015

    def apply(self, data, ctx):
        uid = self.uid
        ptid = int(data.get("id") or 1)
        _item_add(ctx, uid, 38, 2)
        ctx.db.execute(
            "INSERT OR REPLACE INTO newbie_pt_reward (uid, pt_id, update_ts) VALUES (?, ?, ?)",
            (uid, ptid, int(time.time()))
        )
        return {"rewards": [{"id": 38, "num": 2}]}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_59015", {"result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log(f"cs_59014 -> 领取新手进度奖励，获得={result['rewards']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


@operation
class NoobRechargeRewardOp(Operation):
    """59006 领取新手充值奖励（首充礼包/月卡角色/战令专属）：cs_59006 {type, reward_type} → sc_59007 {result, reward_list}。"""

    cmd = 59006
    sc = 59007

    def apply(self, data, ctx):
        uid = self.uid
        rtype = int(data.get("type") or 1)
        sub_type = int(data.get("reward_type") or 0)
        n_act = ctx.db.get_newbie_activity(uid)
        now_ts = int(time.time())

        rewards = []
        updates = {}

        # 1. 首充奖励 (type=1)
        if rtype == 1:
            if sub_type == 0:
                # 6元档首充奖励 (30202 x1 首充自选/碎片礼包)
                status = int(n_act.get("fr_first_gear") or 0)
                if status == 0:
                    raise OperationError(406, "未达成首充条件")
                if status == 2:
                    return {"rewards": []}  # 已领取
                updates["fr_first_gear"] = 2
                updates["fr_new6"] = 0
                _item_add(ctx, uid, 30202, 1)
                rewards.append({"id": 30202, "num": 1})
            else:
                # 连续签到奖励 (根据 now_sign_times 发放对应天数奖励)
                sign_cnt = int(n_act.get("fr_now_sign") or 0) + 1
                if sign_cnt > 3:
                    return {"rewards": []}
                updates["fr_now_sign"] = sign_cnt
                updates["fr_last_sign_ts"] = now_ts
                _item_add(ctx, uid, 30203, 1)
                rewards.append({"id": 30203, "num": 1})
                if sign_cnt == 1:
                    _item_add(ctx, uid, 40103, 10)
                    rewards.append({"id": 40103, "num": 10})
                elif sign_cnt == 2:
                    _item_add(ctx, uid, 20002, 5)
                    rewards.append({"id": 20002, "num": 5})
                elif sign_cnt == 3:
                    _item_add(ctx, uid, 20003, 5)
                    rewards.append({"id": 20003, "num": 5})

        # 2. 月卡专享角色奖励 (type=2)
        elif rtype == 2:
            if sub_type == 0:
                flag = int(n_act.get("mc_flag") or 0)
                role_flag = int(n_act.get("mc_role_flag") or 0)
                if flag == 0:
                    raise OperationError(406, "未激活月卡")
                if role_flag == 1 or role_flag == 2:
                    return {"rewards": []}
                updates["mc_role_flag"] = 1
                updates["mc_new_role"] = 0
                # 月卡首充角色道具 30204 x1
                _item_add(ctx, uid, 30204, 1)
                rewards.append({"id": 30204, "num": 1})
            else:
                # 月卡累计签到奖励 30205 x1
                updates["mc_sign_reward"] = 1
                _item_add(ctx, uid, 30205, 1)
                rewards.append({"id": 30205, "num": 1})

        # 3. 战令首充专属奖励 (type=3)
        elif rtype == 3:
            bp_st = int(n_act.get("bp_reward") or 0)
            if bp_st == 0:
                raise OperationError(406, "未激活战令合约")
            if bp_st == 2:
                return {"rewards": []}
            updates["bp_reward"] = 2
            updates["bp_new"] = 0
            # 战令首充奖励道具 107301 x1 + 6033 x1
            _item_add(ctx, uid, 107301, 1)
            rewards.append({"id": 107301, "num": 1})
            _item_add(ctx, uid, 6033, 1)
            rewards.append({"id": 6033, "num": 1})

        if updates:
            ctx.db.save_newbie_activity(uid, updates)

        return {"rewards": rewards}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_59007", {"result": 0, "reward_list": result["rewards"]}) or b"\x08\x00"
        ctx.log(f"cs_59006 -> 领取新手充值奖励成功(type={data.get('type')}, sub={data.get('reward_type')}) 获得={result['rewards']}")
        frames = [DownFrame(self.sc, payload)]
        # 同步推送 sc_59009 与场景/装扮刷新帧 sc_32009
        try:
            import recharge_service
            svc = recharge_service.RechargeService.get_instance(db=ctx.db)
            p59009 = ctx.codec_encode("sc_59009", svc.build_59009_payload(ctx.db, self.uid))
            if p59009:
                frames.append(DownFrame(59009, p59009))
            has_scene = any((6000 <= int(it.get("id") or 0) <= 6999) for it in result.get("rewards", []))
            if has_scene and ctx.generator is not None:
                p32009 = ctx.generator.gen_payload(32009, uid=self.uid, db=ctx.db)
                if p32009:
                    frames.append(DownFrame(32009, p32009))
        except Exception:
            pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class NoobTag16Op(Operation):
    """59016 新手首充标记消除。"""
    cmd = 59016
    sc = 59017
    def apply(self, data, ctx): return {}
    def respond(self, result, data, ctx):
        return [DownFrame(self.sc, ctx.codec_encode("sc_59017", {"result": 0}) or b"\x08\x00")]


@operation
class NoobTag18Op(Operation):
    """59018 新手首次签到标记消除。"""
    cmd = 59018
    sc = 59019
    def apply(self, data, ctx): return {}
    def respond(self, result, data, ctx):
        return [DownFrame(self.sc, ctx.codec_encode("sc_59019", {"result": 0}) or b"\x08\x00")]


@operation
class NoobTag20Op(Operation):
    """59020 新手一档标记消除。"""
    cmd = 59020
    sc = 59021
    def apply(self, data, ctx): return {}
    def respond(self, result, data, ctx):
        return [DownFrame(self.sc, ctx.codec_encode("sc_59021", {"result": 0}) or b"\x08\x00")]


@operation
class NoobTag22Op(Operation):
    """59022 新手二档标记消除。"""
    cmd = 59022
    sc = 59023
    def apply(self, data, ctx): return {}
    def respond(self, result, data, ctx):
        return [DownFrame(self.sc, ctx.codec_encode("sc_59023", {"result": 0}) or b"\x08\x00")]


@operation
class NoobTag24Op(Operation):
    """59024 新手战令标记消除。"""
    cmd = 59024
    sc = 59025
    def apply(self, data, ctx): return {}
    def respond(self, result, data, ctx):
        return [DownFrame(self.sc, ctx.codec_encode("sc_59025", {"result": 0}) or b"\x08\x00")]


@operation
class RegressionSignInOp(Operation):
    """62012 回归签到：cs_62012 {index} → sc_62013 {result, item_list}。"""

    cmd = 62012
    sc = 62013

    def apply(self, data, ctx):
        uid = self.uid
        now_ts = int(time.time())
        idx = int(data.get("index") or 1)

        row = ctx.db.query("SELECT * FROM regression WHERE uid=?", (uid,))
        r = row[0] if row else {}
        try:
            rec_signs = json.loads(r.get("received_sign_list") or "[]")
        except Exception:
            rec_signs = []
        if idx not in rec_signs:
            rec_signs.append(idx)

        # 回归签到奖励 (来自 SignCfg[10001~10007])
        rewards_map = {
            1: (2, 20000), 2: (38, 3), 3: (20003, 1), 4: (40301, 300),
            5: (38, 2), 6: (40803, 10), 7: (38, 5)
        }
        rw = rewards_map.get(idx, (38, 1))
        _item_add(ctx, uid, rw[0], rw[1])

        ctx.db.execute(
            "INSERT INTO regression (uid, received_sign_list, update_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET received_sign_list=excluded.received_sign_list, update_ts=excluded.update_ts",
            (uid, json.dumps(rec_signs), now_ts)
        )
        return {"items": [{"id": rw[0], "num": rw[1]}]}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_62013", {"result": 0, "item_list": result["items"]}) or b"\x08\x00"
        ctx.log(f"cs_62012 -> 回归每日签到成功，获得={result['items']}")
        frames = [DownFrame(self.sc, payload)]
        if ctx.generator is not None:
            try:
                p11 = ctx.generator.gen_payload(62011, uid=self.uid, db=ctx.db)
                if p11:
                    frames.append(DownFrame(62011, p11))
            except Exception:
                pass
        return frames + _refresh_frames(ctx, self.uid)


@operation
class RegressionFindResOp(Operation):
    """62016 回归资源找回：cs_62016 {} → sc_62017 {result, item_list}。"""

    cmd = 62016
    sc = 62017

    def apply(self, data, ctx):
        uid = self.uid
        now_ts = int(time.time())
        # 找回 240 体力(id=4) 与 50000 金币(id=2)
        _item_add(ctx, uid, 4, 240)
        _item_add(ctx, uid, 2, 50000)
        ctx.db.execute("UPDATE regression SET find_time=?, update_ts=? WHERE uid=?", (now_ts, now_ts, uid))
        return {"items": [{"id": 4, "num": 240}, {"id": 2, "num": 50000}]}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_62017", {"result": 0, "item_list": result["items"]}) or b"\x08\x00"
        ctx.log(f"cs_62016 -> 回归资源找回成功，获得={result['items']}")
        return [DownFrame(self.sc, payload)] + _refresh_frames(ctx, self.uid)


# ---------------- 88xxx 虚构推演（Challenge Rogue Team）全套操作 ----------------

@operation
class RogueTeamStartRunOp(Operation):
    """88100 开启虚构推演：cs_88100 {template_id, difficult, hero_list, affix_pool_id_list} -> sc_88101 {result} + sc_88001/88013/88019/88029"""
    cmd = 88100
    sc = 88101

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        template_id = data.get("template_id", 100001)
        difficult = data.get("difficult", 1)
        hero_list = data.get("hero_list", [])
        affix_pool_id_list = data.get("affix_pool_id_list", [])
        svc = RogueTeamService.get()
        res, session = svc.start_run(self.uid, template_id, difficult, hero_list, affix_pool_id_list)
        return {"result": res, "session": session}

    def respond(self, result, data, ctx):
        frames = []
        p88101 = ctx.codec_encode("sc_88101", {"result": result["result"]})
        if p88101:
            frames.append(DownFrame(88101, p88101))
        session = result.get("session")
        if session and ctx.codec_encode:
            p88001 = ctx.codec_encode("sc_88001", session)
            if p88001:
                frames.append(DownFrame(88001, p88001))
            p88013 = ctx.codec_encode("sc_88013", {"attr_list": session.get("attr_list", [])})
            if p88013:
                frames.append(DownFrame(88013, p88013))
            p88019 = ctx.codec_encode("sc_88019", {"hero_list": session.get("hero_list", []), "modify_type": 1})
            if p88019:
                frames.append(DownFrame(88019, p88019))
            p88029 = ctx.codec_encode("sc_88029", {"finish_guide": 1, "template_id": session.get("template_id", 100001)})
            if p88029:
                frames.append(DownFrame(88029, p88029))
            # 明确清理残留事件弹窗与商店
            p88023 = ctx.codec_encode("sc_88023", {"event_id": 0, "opt_list": [], "trigger_type": 0})
            if p88023:
                frames.append(DownFrame(88023, p88023))
            p88015 = ctx.codec_encode("sc_88015", {"other_info": {"event_type": 0, "param_list": [], "drop_type": 0}})
            if p88015:
                frames.append(DownFrame(88015, p88015))
            p88027 = ctx.codec_encode("sc_88027", {"shop_info": {"event_type": 0, "param_list": [], "drop_type": 0}})
            if p88027:
                frames.append(DownFrame(88027, p88027))
        ctx.log(f"cs_88100 -> 虚构推演开局成功，难度={data.get('difficult')}")
        return frames


@operation
class RogueTeamHeroRecruitOp(Operation):
    """88102 修正者招募：cs_88102 {hero_list} -> sc_88103 {result} + sc_88019"""
    cmd = 88102
    sc = 88103

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        hero_list = data.get("hero_list", [])
        svc = RogueTeamService.get()
        res, all_heroes, next_ev = svc.hero_recruit(self.uid, hero_list)
        return {"result": res, "heroes": all_heroes, "next_ev": next_ev}

    def respond(self, result, data, ctx):
        frames = []
        p88103 = ctx.codec_encode("sc_88103", {"result": result["result"]})
        if p88103:
            frames.append(DownFrame(88103, p88103))
        heroes = result.get("heroes")
        if heroes and ctx.codec_encode:
            p88019 = ctx.codec_encode("sc_88019", {"hero_list": heroes, "modify_type": 1})
            if p88019:
                frames.append(DownFrame(88019, p88019))

            next_ev = result.get("next_ev")
            if next_ev:
                p88023 = ctx.codec_encode("sc_88023", next_ev)
                if p88023:
                    frames.append(DownFrame(88023, p88023))

        ctx.log(f"cs_88102 -> 招募修正者: count={len(data.get('hero_list', []))}")
        return frames


@operation
class RogueTeamSelectNodeOp(Operation):
    """88004 选择节点：cs_88004 {node_id} -> sc_88005 {result} + sc_88003 + 节点交互"""
    cmd = 88004
    sc = 88005

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        node_id = data.get("node_id", 0)
        svc = RogueTeamService.get()
        res, update_info = svc.select_node(self.uid, node_id)
        return {"result": res, "update_info": update_info}

    def respond(self, result, data, ctx):
        frames = []
        p88005 = ctx.codec_encode("sc_88005", {"result": result["result"]})
        if p88005:
            frames.append(DownFrame(88005, p88005))
        u = result.get("update_info")
        if u and ctx.codec_encode:
            p88003 = ctx.codec_encode("sc_88003", {"map_info": u.get("map_info", []), "second_map_info": []})
            if p88003:
                frames.append(DownFrame(88003, p88003))
            if u.get("shop_info"):
                p88027 = ctx.codec_encode("sc_88027", {"shop_info": u["shop_info"]})
                if p88027:
                    frames.append(DownFrame(88027, p88027))
            if u.get("other_info"):
                oinfo = u["other_info"]
                if oinfo.get("event_id"):
                    p88023 = ctx.codec_encode("sc_88023", {
                        "event_id": oinfo["event_id"],
                        "opt_list": oinfo.get("opt_list", []),
                        "trigger_type": oinfo.get("trigger_type", 1)
                    })
                    if p88023:
                        frames.append(DownFrame(88023, p88023))
                elif oinfo.get("event_type"):
                    p88015 = ctx.codec_encode("sc_88015", {"other_info": oinfo})
                    if p88015:
                        frames.append(DownFrame(88015, p88015))
            if u.get("attr_list"):
                p88013 = ctx.codec_encode("sc_88013", {"attr_list": u["attr_list"]})
                if p88013:
                    frames.append(DownFrame(88013, p88013))
        ctx.log(f"cs_88004 -> 选择虚构推演节点={data.get('node_id')}")
        return frames


@operation
class RogueTeamNextFloorOp(Operation):
    """88008 前往下一层/离开隐藏层：cs_88008 {sign} -> sc_88009 {result} + sc_88001"""
    cmd = 88008
    sc = 88009

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        sign = data.get("sign", 0)
        svc = RogueTeamService.get()
        res, session = svc.next_floor(self.uid, sign)
        return {"result": res, "session": session}

    def respond(self, result, data, ctx):
        frames = []
        p88009 = ctx.codec_encode("sc_88009", {"result": result["result"]})
        if p88009:
            frames.append(DownFrame(88009, p88009))
        session = result.get("session")
        if session and ctx.codec_encode:
            p88001 = ctx.codec_encode("sc_88001", session)
            if p88001:
                frames.append(DownFrame(88001, p88001))
        ctx.log("cs_88008 -> 进入下一层")
        return frames


@operation
class RogueTeamChooseEventOptionOp(Operation):
    """88024 选择奇遇/安全屋事件选项：cs_88024 {opt_id} -> sc_88025 {result} + sc_88023 + sc_88019 + sc_88013 + sc_88021 + sc_88205"""
    cmd = 88024
    sc = 88025

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        opt_id = data.get("opt_id", 0)
        svc = RogueTeamService.get()
        res, extra = svc.choose_event_option(self.uid, opt_id)
        return {"result": res, "extra": extra}

    def respond(self, result, data, ctx):
        frames = []
        p88025 = ctx.codec_encode("sc_88025", {"result": result["result"]}) or b"\x08\x00"
        frames.append(DownFrame(self.sc, p88025))
        extra = result.get("extra")
        if extra and ctx.codec_encode:
            # 1. 角色血量变动
            if extra.get("hero_list"):
                p88019 = ctx.codec_encode("sc_88019", {"hero_list": extra["hero_list"], "modify_type": 4})
                if p88019:
                    frames.append(DownFrame(88019, p88019))
            # 2. 全局属性变动
            if extra.get("attr_list"):
                p88013 = ctx.codec_encode("sc_88013", {"attr_list": extra["attr_list"]})
                if p88013:
                    frames.append(DownFrame(88013, p88013))
            # 3. 背包道具列表增减
            if extra.get("item_list"):
                p88021 = ctx.codec_encode("sc_88021", {"item_list": extra["item_list"]})
                if p88021:
                    frames.append(DownFrame(88021, p88021))
            # 4. 获得物品动效与弹窗队列 (必须先于 sc_88023/sc_88003 入队)
            if extra.get("update_list"):
                p88205 = ctx.codec_encode("sc_88205", {"update_list": extra["update_list"]})
                if p88205:
                    frames.append(DownFrame(88205, p88205))
            # 5. 唤起其他弹窗（如角色招募 event_type=3）
            if extra.get("pop_window"):
                p88015 = ctx.codec_encode("sc_88015", {"other_info": extra["pop_window"]})
                if p88015:
                    frames.append(DownFrame(88015, p88015))
            # 6. 世界线跃迁与结局剧情
            if extra.get("world_line_id"):
                p88215 = ctx.codec_encode("sc_88215", {"id": int(extra["world_line_id"])})
                if p88215:
                    frames.append(DownFrame(88215, p88215))
                    ctx.log(f"cs_88024 -> 世界线跃迁，下发 sc_88215 id={extra['world_line_id']}")
            if extra.get("ending_id"):
                p88217 = ctx.codec_encode("sc_88217", {"id": int(extra["ending_id"])})
                if p88217:
                    frames.append(DownFrame(88217, p88217))
                    ctx.log(f"cs_88024 -> 达成结局剧情，下发 sc_88217 id={extra['ending_id']}")
            # 7. 地图节点状态更新与下一跳解锁
            if extra.get("map_info"):
                p88003 = ctx.codec_encode("sc_88003", {"map_info": extra["map_info"], "second_map_info": []})
                if p88003:
                    frames.append(DownFrame(88003, p88003))
            # 8. 切层标识
            if extra.get("is_floor_clear"):
                p88221 = ctx.codec_encode("sc_88221", {"sign": 1})
                if p88221:
                    frames.append(DownFrame(88221, p88221))
                    ctx.log("cs_88024 -> 本层全部通关，下发 sc_88221 触发切层动画")
            # 9. 推进下一阶段事件或关闭事件窗口 (最后下发触发 UI 推进/回退)
            if extra.get("next_event"):
                p88023 = ctx.codec_encode("sc_88023", extra["next_event"])
                if p88023:
                    frames.append(DownFrame(88023, p88023))
            elif not extra.get("pop_window"):
                p88023 = ctx.codec_encode("sc_88023", {"event_id": 0, "opt_list": [], "trigger_type": 0})
                if p88023:
                    frames.append(DownFrame(88023, p88023))
        ctx.log(f"cs_88024 -> 奇遇/安全屋事件选择选项={data.get('opt_id')}")
        return frames


@operation
class RogueTeamSyncPlayStateOp(Operation):
    """88030 游戏内状态同步：cs_88030 {type} -> sc_88031 {result} + sc_88001 + sc_88013 + sc_88019"""
    cmd = 88030
    sc = 88031

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        frames = []
        payload = ctx.codec_encode("sc_88031", {"result": 0}) or b"\x08\x00"
        frames.append(DownFrame(self.sc, payload))

        # 继续推演时同步补发局内最新 session (sc_88001)，确保客户端地图层数与节点状态 100% 顺畅加载
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        sd = svc.get_session_data(self.uid)
        if sd and sd.get("in_game", 0) != 0 and ctx.codec_encode:
            p88001 = ctx.codec_encode("sc_88001", sd)
            if p88001:
                frames.append(DownFrame(88001, p88001))
            if sd.get("attr_list"):
                p88013 = ctx.codec_encode("sc_88013", {"attr_list": sd["attr_list"]})
                if p88013:
                    frames.append(DownFrame(88013, p88013))
            if sd.get("hero_list"):
                p88019 = ctx.codec_encode("sc_88019", {"hero_list": sd["hero_list"]})
                if p88019:
                    frames.append(DownFrame(88019, p88019))

        ctx.log("cs_88030 -> 虚构推演游戏状态同步完成（已下发 sc_88031 与 sc_88001）")
        return frames


@operation
class RogueTeamEventCommitOp(Operation):
    """88200 事件选择提交：cs_88200 {event_id, param_arg} -> sc_88201 {result, window_opt} + sc_88021 + sc_88205 + sc_88013 + sc_88003"""
    cmd = 88200
    sc = 88201

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        event_id = data.get("event_id", 0)
        param_arg = data.get("param_arg", 0)
        svc = RogueTeamService.get()
        res, extra = svc.commit_event_selection(self.uid, event_id, param_arg)
        # 还有第二次选择（天赋 10399 战功延展）时 window_opt 必须为 0，
        # 否则客户端 ClearUnOperateData() 会把待选窗口清掉
        has_next = bool(extra and extra.get("next_pop"))
        return {"result": res, "window_opt": 0 if (has_next or res != 0) else 1, "extra": extra}

    def respond(self, result, data, ctx):
        frames = []
        p88201 = ctx.codec_encode("sc_88201", {"result": result["result"], "window_opt": result["window_opt"]})
        if p88201:
            frames.append(DownFrame(88201, p88201))
        extra = result.get("extra")
        if extra and ctx.codec_encode:
            if extra.get("item_list"):
                p88021 = ctx.codec_encode("sc_88021", {"item_list": extra["item_list"]})
                if p88021:
                    frames.append(DownFrame(88021, p88021))
            if extra.get("update_list"):
                p88205 = ctx.codec_encode("sc_88205", {"update_list": extra["update_list"]})
                if p88205:
                    frames.append(DownFrame(88205, p88205))
            if extra.get("attr_list"):
                p88013 = ctx.codec_encode("sc_88013", {"attr_list": extra["attr_list"]})
                if p88013:
                    frames.append(DownFrame(88013, p88013))
            if extra.get("map_info"):
                p88003 = ctx.codec_encode("sc_88003", {"map_info": extra["map_info"], "second_map_info": []})
                if p88003:
                    frames.append(DownFrame(88003, p88003))
            # 天赋 10399：续推第二轮掉落弹窗（drop_type=2 -> UI 文案"额外选择"）
            if extra.get("next_pop"):
                p88015b = ctx.codec_encode("sc_88015", {"other_info": extra["next_pop"]})
                if p88015b:
                    frames.append(DownFrame(88015, p88015b))
                    ctx.log("cs_88200 -> 战功延展：续推第二次掉落选择 sc_88015")
            if extra.get("is_floor_clear"):
                p88221 = ctx.codec_encode("sc_88221", {"sign": 1})
                if p88221:
                    frames.append(DownFrame(88221, p88221))
                    ctx.log("cs_88200 -> 本层全部通关，下发 sc_88221 触发切层动画")
        ctx.log(f"cs_88200 -> 提交推演事件选择 event_id={data.get('event_id')}, param_arg={data.get('param_arg')}")
        return frames


@operation
class RogueTeamEventResetOp(Operation):
    """88202 三选一刷新（重掷候选）：cs_88202 {event_id=RESET_OPERATE_TYPE} ->
    sc_88015（新候选，必须先发）+ sc_88013（重置次数-1）+ sc_88203 {result}"""
    cmd = 88202
    sc = 88203

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        res, extra = svc.reset_operate_data(self.uid, data.get("event_id", 1))
        return {"result": res, "extra": extra}

    def respond(self, result, data, ctx):
        frames = []
        extra = result.get("extra") or {}
        # 顺序很关键：客户端收到 sc_88203 的回调里直接 RefreshUI()，
        # 新候选（sc_88015）必须在它之前到达，否则刷了个寂寞。
        if extra.get("other_info") and ctx.codec_encode:
            p88015 = ctx.codec_encode("sc_88015", {"other_info": extra["other_info"]})
            if p88015:
                frames.append(DownFrame(88015, p88015))
        if extra.get("attr_list") and ctx.codec_encode:
            p88013 = ctx.codec_encode("sc_88013", {"attr_list": extra["attr_list"]})
            if p88013:
                frames.append(DownFrame(88013, p88013))
        payload = ctx.codec_encode("sc_88203", {"result": result["result"]}) or b"\x08\x00"
        frames.append(DownFrame(self.sc, payload))
        ctx.log(f"cs_88202 -> 三选一刷新 type={data.get('event_id')} result={result['result']} 剩余重置={extra.get('reset_left')}")
        return frames


@operation
class RogueTeamCloseReplaceNodeOp(Operation):
    """88208 关闭替换节点：cs_88208 {node_id, node_type} -> sc_88209 {result}"""
    cmd = 88208
    sc = 88209

    def apply(self, data, ctx):
        return {"result": 0}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_88209", {"result": 0}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


@operation
class RogueTeamDiscardItemOp(Operation):
    """88212 丢弃道具：cs_88212 {item_id_list} -> sc_88213 {result} + sc_88021"""
    cmd = 88212
    sc = 88213

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        item_id_list = data.get("item_id_list", [])
        svc = RogueTeamService.get()
        res, extra = svc.discard_items(self.uid, item_id_list)
        return {"result": res, "extra": extra}

    def respond(self, result, data, ctx):
        frames = []
        p88213 = ctx.codec_encode("sc_88213", {"result": result["result"]})
        if p88213:
            frames.append(DownFrame(88213, p88213))
        extra = result.get("extra")
        if extra and extra.get("item_list") and ctx.codec_encode:
            p88021 = ctx.codec_encode("sc_88021", {"item_list": extra["item_list"]})
            if p88021:
                frames.append(DownFrame(88021, p88021))
        ctx.log(f"cs_88212 -> 丢弃推演道具 item_id_list={data.get('item_id_list')}")
        return frames


@operation
class RogueTeamPlayEndingPlotOp(Operation):
    """88218 播放结局剧情：cs_88218 {} -> sc_88219 {result}"""
    cmd = 88218
    sc = 88219

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        res = svc.play_ending_plot(self.uid)
        return {"result": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_88219", {"result": result["result"]}) or b"\x08\x00"
        ctx.log("cs_88218 -> 播放结局剧情")
        return [DownFrame(self.sc, payload)]


@operation
class RogueTeamShopBuyOp(Operation):
    """88222 游商购买：cs_88222 {index} -> sc_88223 {result} + sc_88013 + sc_88021 + sc_88027"""
    cmd = 88222
    sc = 88223

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        index = data.get("index", 0)
        svc = RogueTeamService.get()
        res, extra = svc.shop_buy(self.uid, index)
        return {"result": res, "extra": extra}

    def respond(self, result, data, ctx):
        frames = []
        p88223 = ctx.codec_encode("sc_88223", {"result": result["result"]})
        if p88223:
            frames.append(DownFrame(88223, p88223))
        extra = result.get("extra")
        if extra and ctx.codec_encode:
            if extra.get("attr_list"):
                p88013 = ctx.codec_encode("sc_88013", {"attr_list": extra["attr_list"]})
                if p88013:
                    frames.append(DownFrame(88013, p88013))
            if extra.get("item_list"):
                p88021 = ctx.codec_encode("sc_88021", {"item_list": extra["item_list"]})
                if p88021:
                    frames.append(DownFrame(88021, p88021))
            if extra.get("shop_info"):
                p88027 = ctx.codec_encode("sc_88027", {"shop_info": extra["shop_info"]})
                if p88027:
                    frames.append(DownFrame(88027, p88027))
            # index=0 = 客户端「离开」按钮：必须补推地图与全量 session，
            # 否则商店节点结束后前沿指针不动（玩家卡在商店节点）
            if extra.get("map_info"):
                p88003 = ctx.codec_encode("sc_88003", {"map_info": extra["map_info"], "second_map_info": []})
                if p88003:
                    frames.append(DownFrame(88003, p88003))
            if extra.get("session"):
                p88001 = ctx.codec_encode("sc_88001", extra["session"])
                if p88001:
                    frames.append(DownFrame(88001, p88001))
            if extra.get("is_floor_clear"):
                p88221 = ctx.codec_encode("sc_88221", {"sign": 1})
                if p88221:
                    frames.append(DownFrame(88221, p88221))
                    ctx.log("cs_88222 -> 本层全部通关，下发 sc_88221 触发切层动画")
        if int(data.get("index", 0) or 0) == 0:
            ctx.log("cs_88222 index=0 -> 离开游商，推进节点指针")
        else:
            ctx.log(f"cs_88222 -> 游商购买商品 index={data.get('index')}")
        return frames


@operation
class RogueTeamShopRefreshOp(Operation):
    """88224 游商刷新：cs_88224 {} -> sc_88225 {result} + sc_88013 + sc_88027"""
    cmd = 88224
    sc = 88225

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        res, extra = svc.shop_refresh(self.uid)
        return {"result": res, "extra": extra}

    def respond(self, result, data, ctx):
        frames = []
        p88225 = ctx.codec_encode("sc_88225", {"result": result["result"]})
        if p88225:
            frames.append(DownFrame(88225, p88225))
        extra = result.get("extra")
        if extra and ctx.codec_encode:
            if extra.get("attr_list"):
                p88013 = ctx.codec_encode("sc_88013", {"attr_list": extra["attr_list"]})
                if p88013:
                    frames.append(DownFrame(88013, p88013))
            if extra.get("shop_info"):
                p88027 = ctx.codec_encode("sc_88027", {"shop_info": extra["shop_info"]})
                if p88027:
                    frames.append(DownFrame(88027, p88027))
        ctx.log("cs_88224 -> 游商刷新商品列表")
        return frames


@operation
class RogueTeamUnlockSkillTreeOp(Operation):
    """88300 天赋科技树升级：cs_88300 {template_id, tree_id} -> sc_88301 {result} + sc_88305"""
    cmd = 88300
    sc = 88301

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        template_id = data.get("template_id", 100001)
        tree_id = data.get("tree_id", 0)
        svc = RogueTeamService.get()
        res = svc.unlock_tech_node(self.uid, template_id, tree_id)
        out = svc.get_outside_data(self.uid, template_id)
        return {"result": res, "outside": out}

    def respond(self, result, data, ctx):
        frames = []
        p88301 = ctx.codec_encode("sc_88301", {"result": result["result"]})
        if p88301:
            frames.append(DownFrame(88301, p88301))
        out = result.get("outside")
        if out and ctx.codec_encode:
            p88305 = ctx.codec_encode("sc_88305", {
                "template_id": out["template_id"],
                "difficult": out["difficult"],
                "max_difficult": out["max_difficult"],
                "collection_list": out["collection_list"],
                "unlock_collection": out["unlock_collection"],
                "view_collection": out["view_collection"],
                "tree_list": out["tree_list"],
                "his_difficult": out["his_difficult"],
                "his_avg": out["his_avg"],
                "last_id": out["last_id"]
            })
            if p88305:
                frames.append(DownFrame(88305, p88305))
        ctx.log(f"cs_88300 -> 升级虚构推演科技树节点={data.get('tree_id')}, result={result['result']}")
        return frames + _refresh_frames(ctx, self.uid)


@operation
class RogueTeamSettleScoreOp(Operation):
    """88306 结算统计请求：cs_88306 {} -> sc_88001 + sc_88217 {id, state} + sc_88307 {result, end_info_list, collection_list, unlock_collection, total_time} + sc_88305"""
    cmd = 88306
    sc = 88307

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        res, settle_info = svc.settle_run(self.uid, is_win=False)
        return {"result": res, "settle": settle_info}

    def respond(self, result, data, ctx):
        frames = []
        settle = result.get("settle", {})
        ending_id = settle.get("ending_id", 4)
        
        # 1. 下发最新局内 session (sc_88001)，同步 activeTemplateID_ 与 floor_state = FAIL
        from rogueteam_service import RogueTeamService
        svc = RogueTeamService.get()
        sd = svc.get_session_data(self.uid)
        if sd and ctx.codec_encode:
            p88001 = ctx.codec_encode("sc_88001", sd)
            if p88001:
                frames.append(DownFrame(88001, p88001))
            # 明确清理残留事件弹窗与商店
            p88023 = ctx.codec_encode("sc_88023", {"event_id": 0, "opt_list": [], "trigger_type": 0})
            if p88023:
                frames.append(DownFrame(88023, p88023))
            p88015 = ctx.codec_encode("sc_88015", {"other_info": {"event_type": 0, "param_list": [], "drop_type": 0}})
            if p88015:
                frames.append(DownFrame(88015, p88015))
            p88027 = ctx.codec_encode("sc_88027", {"shop_info": {"event_type": 0, "param_list": [], "drop_type": 0}})
            if p88027:
                frames.append(DownFrame(88027, p88027))

        # 2. 下发结局 ID (sc_88217)
        p88217 = ctx.codec_encode("sc_88217", {"id": ending_id, "state": 1})
        if p88217:
            frames.append(DownFrame(88217, p88217))

        # 3. 下发统计结算数据 (sc_88307)
        payload = ctx.codec_encode("sc_88307", {
            "result": settle.get("result", 0),
            "end_info_list": settle.get("end_info_list", []),
            "collection_list": settle.get("collection_list", []),
            "unlock_collection": settle.get("unlock_collection", []),
            "total_time": settle.get("total_time", 180)
        }) or b"\x08\x00"
        frames.append(DownFrame(self.sc, payload))

        # 4. 刷新外围总览（难度重置为 0，切换为开始推演）
        out = svc.get_outside_data(self.uid)
        p88305 = ctx.codec_encode("sc_88305", {
            "template_id": out["template_id"],
            "difficult": 0,
            "max_difficult": out["max_difficult"],
            "collection_list": out["collection_list"],
            "unlock_collection": out["unlock_collection"],
            "view_collection": out["view_collection"],
            "tree_list": out["tree_list"],
            "his_difficult": out["his_difficult"],
            "his_avg": out["his_avg"],
            "last_id": out["last_id"]
        })
        if p88305:
            frames.append(DownFrame(88305, p88305))

        ctx.log("cs_88306 -> 虚构推演结算完成（已下发 sc_88001、sc_88217 结局与 sc_88307 结算报告）")
        return frames + _refresh_frames(ctx, self.uid)


@operation
class RogueTeamViewIllustratedOp(Operation):
    """88312 查看图鉴条目：cs_88312 {template_id, colllection_id, type} -> sc_88313 {result}"""
    cmd = 88312
    sc = 88313

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        template_id = data.get("template_id", 100001)
        coll_id = data.get("colllection_id") or data.get("collection_id") or 0
        coll_type = data.get("type", 1)
        svc = RogueTeamService.get()
        res = svc.view_collection(self.uid, template_id, coll_id, coll_type)
        return {"result": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_88313", {"result": result["result"]}) or b"\x08\x00"
        ctx.log(f"cs_88312 -> 查看图鉴已读 coll_id={data.get('colllection_id')}")
        return [DownFrame(self.sc, payload)]


@operation
class RogueTeamRecordScoreIdOp(Operation):
    """88316 记录积分目标：cs_88316 {main_id, id} -> sc_88317 {result}"""
    cmd = 88316
    sc = 88317

    def apply(self, data, ctx):
        from rogueteam_service import RogueTeamService
        main_id = data.get("main_id", 100001)
        score_id = data.get("id", 0)
        svc = RogueTeamService.get()
        res = svc.record_score_id(self.uid, main_id, score_id)
        return {"result": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_88317", {"result": result["result"]}) or b"\x08\x00"
        return [DownFrame(self.sc, payload)]


# ----------------------------------------------------------------------
# 管理员猫咪探索（弥弥尔探索 / 远征挂机收益玩法 p67 67002~67013）
# ----------------------------------------------------------------------

@operation
class AdminCatSkillLevelUpOp(Operation):
    """67002 猫咪技能升级：cs_67002 {mimir_id, skill_id} -> sc_67003 {result}"""
    cmd = 67002
    sc = 67003

    def apply(self, data, ctx):
        from admin_cat_explore_service import AdminCatExploreService
        mimir_id = int(data.get("mimir_id") or 0)
        skill_id = int(data.get("skill_id") or 0)
        svc = AdminCatExploreService.get_instance()
        res = svc.level_up_skill(ctx, self.uid, mimir_id, skill_id)
        return {"result": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_67003", {"result": result["result"]}) or b"\x08\x00"
        ctx.log(f"[探索] 升级猫咪技能: uid={self.uid} mimir={data.get('mimir_id')} skill={data.get('skill_id')} 结果={result['result']}")
        frames = [DownFrame(self.sc, payload)]
        if result["result"] == 0:
            from admin_cat_explore_service import AdminCatExploreService
            svc = AdminCatExploreService.get_instance()
            p_67001 = ctx.codec_encode("sc_67001", svc.generate_sc_67001_dict(ctx.db, self.uid))
            if p_67001:
                frames.append(DownFrame(67001, p_67001))
        return frames


@operation
class AdminCatExploreOp(Operation):
    """67004 开始探索派遣：cs_67004 {area_id, hour_time, mimir_id} -> sc_67005 {result}"""
    cmd = 67004
    sc = 67005

    def apply(self, data, ctx):
        from admin_cat_explore_service import AdminCatExploreService
        area_id = int(data.get("area_id") or 0)
        hour_time = int(data.get("hour_time") or 0)
        mimir_id = int(data.get("mimir_id") or 0)
        svc = AdminCatExploreService.get_instance()
        res = svc.start_explore(ctx, self.uid, area_id, hour_time, mimir_id)
        return {"result": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_67005", {"result": result["result"]}) or b"\x08\x00"
        ctx.log(f"[探索] 派遣猫咪: uid={self.uid} 区域={data.get('area_id')} 猫咪={data.get('mimir_id')} 时长={data.get('hour_time')}h 结果={result['result']}")
        frames = [DownFrame(self.sc, payload)]
        if result["result"] == 0:
            from admin_cat_explore_service import AdminCatExploreService
            svc = AdminCatExploreService.get_instance()
            p_67001 = ctx.codec_encode("sc_67001", svc.generate_sc_67001_dict(ctx.db, self.uid))
            if p_67001:
                frames.append(DownFrame(67001, p_67001))
        return frames


@operation
class AdminCatExploreFinishOp(Operation):
    """67006 结束探索领取挂机收益：cs_67006 {area_id} -> sc_67007 {result, event_id, reward_list}"""
    cmd = 67006
    sc = 67007

    def apply(self, data, ctx):
        from admin_cat_explore_service import AdminCatExploreService
        area_id = int(data.get("area_id") or 0)
        svc = AdminCatExploreService.get_instance()
        res, event_id, reward_list = svc.finish_explore(ctx, self.uid, area_id, force_finish=True)
        return {"result": res, "event_id": event_id, "reward_list": reward_list}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_67007", {
            "result": result["result"],
            "event_id": result["event_id"],
            "reward_list": result["reward_list"]
        })
        rew_desc = ", ".join([f"道具{r['id']}x{r['num']}" for r in result.get("reward_list", [])])
        ctx.log(f"[探索] 结算挂机收益: uid={self.uid} 区域={data.get('area_id')} 奖励: [{rew_desc}] 奇遇={result.get('event_id')} 结果={result['result']}")
        frames = [DownFrame(self.sc, payload)]
        if result["result"] == 0:
            from admin_cat_explore_service import AdminCatExploreService
            svc = AdminCatExploreService.get_instance()
            p_67001 = ctx.codec_encode("sc_67001", svc.generate_sc_67001_dict(ctx.db, self.uid))
            if p_67001:
                frames.append(DownFrame(67001, p_67001))
        return frames


@operation
class AdminCatGetWeekRewardOp(Operation):
    """67008 领取周末周常累计宝箱：cs_67008 {} -> sc_67009 {result, reward_list}"""
    cmd = 67008
    sc = 67009

    def apply(self, data, ctx):
        from admin_cat_explore_service import AdminCatExploreService
        svc = AdminCatExploreService.get_instance()
        res, reward_list = svc.claim_weekly_reward(ctx, self.uid)
        return {"result": res, "reward_list": reward_list}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_67009", {
            "result": result["result"],
            "reward_list": result["reward_list"]
        })
        rew_desc = ", ".join([f"道具{r['id']}x{r['num']}" for r in result.get("reward_list", [])])
        ctx.log(f"[探索] 领取周末周常累计宝箱: uid={self.uid} 奖励: [{rew_desc}] 结果={result['result']}")
        frames = [DownFrame(self.sc, payload)]
        if result["result"] == 0:
            from admin_cat_explore_service import AdminCatExploreService
            svc = AdminCatExploreService.get_instance()
            p_67001 = ctx.codec_encode("sc_67001", svc.generate_sc_67001_dict(ctx.db, self.uid))
            if p_67001:
                frames.append(DownFrame(67001, p_67001))
        return frames


@operation
class AdminCatUnlockOp(Operation):
    """67010 解锁新猫咪：cs_67010 {mimir_id} -> sc_67011 {result}"""
    cmd = 67010
    sc = 67011

    def apply(self, data, ctx):
        from admin_cat_explore_service import AdminCatExploreService
        mimir_id = int(data.get("mimir_id") or 0)
        svc = AdminCatExploreService.get_instance()
        res = svc.unlock_mimir(ctx, self.uid, mimir_id)
        return {"result": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_67011", {"result": result["result"]}) or b"\x08\x00"
        ctx.log(f"[探索] 解锁新猫咪: uid={self.uid} mimir={data.get('mimir_id')} 结果={result['result']}")
        frames = [DownFrame(self.sc, payload)]
        if result["result"] == 0:
            from admin_cat_explore_service import AdminCatExploreService
            svc = AdminCatExploreService.get_instance()
            p_67001 = ctx.codec_encode("sc_67001", svc.generate_sc_67001_dict(ctx.db, self.uid))
            if p_67001:
                frames.append(DownFrame(67001, p_67001))
        return frames


@operation
class AdminCatWeeklyFirstOpenOp(Operation):
    """67012 每周首次进入场景：cs_67012 {} -> sc_67013 {result}"""
    cmd = 67012
    sc = 67013

    def apply(self, data, ctx):
        from admin_cat_explore_service import AdminCatExploreService
        svc = AdminCatExploreService.get_instance()
        res = svc.set_weekly_scene_open(ctx, self.uid)
        return {"result": res}

    def respond(self, result, data, ctx):
        payload = ctx.codec_encode("sc_67013", {"result": result["result"]}) or b"\x08\x00"
        ctx.log(f"[探索] 每周首次进入场景: uid={self.uid} 结果={result['result']}")
        return [DownFrame(self.sc, payload)]


def get_operation(cmd):
    """获取指定 cmd 的 Operation 实例辅助函数，支持 bind uid 调用。"""
    from core import OPERATIONS
    op = OPERATIONS.get(int(cmd))
    if op is None:
        return None
    class _BoundOp:
        def __init__(self, op_inst):
            self._op = op_inst
        def __call__(self, uid=None):
            if uid is not None:
                self._op.uid = uid
            return self._op
        def __getattr__(self, item):
            return getattr(self._op, item)
    return _BoundOp(op)




