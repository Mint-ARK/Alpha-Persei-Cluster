# -*- coding: utf-8 -*-
"""
recharge_service.py — 充值发货与累计充值服务中枢 (Recharge & Pay Domain Service)

职责：
1. 充值发货处理：精准识别 PaymentCfg 商品类型，移转之辉/月卡/礼包真实发货入库，支持首充双倍；
2. 累计充值积分：根据支付金额（分）自动累加并持久化到 user_recharge 表；
3. 登录洪流驱动：为 sc_34007 (累充状态) 与 sc_34021 (首充状态) 提供实时数据库动态下发；
4. 累充档位领奖：处理 cs_34012 -> sc_34013 (常驻累充) 与 cs_34118 -> sc_34119 (版本限时累充)，
   自动按照 TotalRechargeCfg 发放道具/修正凭证/高级材料，记录已领档位并防重复领取。
"""
import copy
import json
import os
import re
import threading
import time

# 基础内置充值商品配置（对齐官方 PaymentCfg.lua）
DEFAULT_PAYMENT_CFG = {
    # 钻石直购 (Type 1)
    1: {"id": 1, "cost": 600, "total_point": 6, "type": 1, "product_id": "com.yongshi.tenojo.diamond01", "name": "60移转之花", "diamond": 60},
    2: {"id": 2, "cost": 3000, "total_point": 30, "type": 1, "product_id": "com.yongshi.tenojo.diamond02", "name": "300移转之花", "diamond": 300},
    3: {"id": 3, "cost": 9800, "total_point": 98, "type": 1, "product_id": "com.yongshi.tenojo.diamond03", "name": "980移转之花", "diamond": 980},
    4: {"id": 4, "cost": 19800, "total_point": 198, "type": 1, "product_id": "com.yongshi.tenojo.diamond04", "name": "1980移转之花", "diamond": 1980},
    5: {"id": 5, "cost": 32800, "total_point": 328, "type": 1, "product_id": "com.yongshi.tenojo.diamond05", "name": "3280移转之花", "diamond": 3280},
    6: {"id": 6, "cost": 64800, "total_point": 648, "type": 1, "product_id": "com.yongshi.tenojo.diamond06", "name": "6480移转之花", "diamond": 6480},
    # 恒定观测 (月卡 Type 2)
    7: {"id": 7, "cost": 3000, "total_point": 30, "type": 2, "product_id": "com.yongshi.tenojo.monthcard", "name": "月卡", "diamond": 300},
    101: {"id": 101, "cost": 3000, "total_point": 30, "type": 2, "product_id": "com.yongshi.tenojo.monthlycard", "name": "恒定观测", "diamond": 300},
    111: {"id": 111, "cost": 2100, "total_point": 21, "type": 2, "product_id": "com.yongshi.tenojo.monthlycardreturn", "name": "恒定观测-特权折扣", "diamond": 300},
    241: {"id": 241, "cost": 2700, "total_point": 27, "type": 2, "product_id": "com.yongshi.tenojo.monthlycardsale01", "name": "恒定观测-9折", "diamond": 300},
    242: {"id": 242, "cost": 2400, "total_point": 24, "type": 2, "product_id": "com.yongshi.tenojo.monthlycardsale02", "name": "恒定观测-8折", "diamond": 300},
    243: {"id": 243, "cost": 2100, "total_point": 21, "type": 2, "product_id": "com.yongshi.tenojo.monthlycardsale03", "name": "恒定观测-7折", "diamond": 300},
    # 战令 / 合约 (Passport Type 3)
    8: {"id": 8, "cost": 6800, "total_point": 68, "type": 3, "product_id": "com.yongshi.tenojo.passport01", "name": "基础合约", "diamond": 0},
    9: {"id": 9, "cost": 12800, "total_point": 128, "type": 3, "product_id": "com.yongshi.tenojo.passport02", "name": "进阶合约", "diamond": 0},
    201: {"id": 201, "cost": 6800, "total_point": 68, "type": 3, "product_id": "com.yongshi.tenojo.battlepass01", "name": "进阶合约", "diamond": 0},
    202: {"id": 202, "cost": 12800, "total_point": 128, "type": 3, "product_id": "com.yongshi.tenojo.battlepass02", "name": "深度合约", "diamond": 0},
    203: {"id": 203, "cost": 6000, "total_point": 60, "type": 3, "product_id": "com.yongshi.tenojo.battlepass03", "name": "进阶合约升级", "diamond": 0},
    211: {"id": 211, "cost": 4700, "total_point": 47, "type": 3, "product_id": "com.yongshi.tenojo.battlepassreturn01", "name": "进阶合约-特权折扣", "diamond": 0},
    212: {"id": 212, "cost": 8900, "total_point": 89, "type": 3, "product_id": "com.yongshi.tenojo.battlepassreturn02", "name": "深度合约-特权折扣", "diamond": 0},
    213: {"id": 213, "cost": 4200, "total_point": 42, "type": 3, "product_id": "com.yongshi.tenojo.battlepassreturn03", "name": "进阶合约升级-特权折扣", "diamond": 0},
    221: {"id": 221, "cost": 4700, "total_point": 47, "type": 3, "product_id": "com.yongshi.tenojo.battlepasscollab01", "name": "联动进阶合约", "diamond": 0},
    222: {"id": 222, "cost": 10700, "total_point": 107, "type": 3, "product_id": "com.yongshi.tenojo.battlepasscollab02", "name": "联动深度合约", "diamond": 0},
    223: {"id": 223, "cost": 6000, "total_point": 60, "type": 3, "product_id": "com.yongshi.tenojo.battlepasscollab03", "name": "联动进阶合约升级", "diamond": 0},
}

# 基础内置累计充值档位配置（对齐官方 TotalRechargeCfg.lua）
DEFAULT_TOTAL_RECHARGE_CFG = {
    1: {"id": 1, "num": 6, "reward": [{"id": 5, "num": 1}, {"id": 22001, "num": 5}, {"id": 40102, "num": 5}]},
    2: {"id": 2, "num": 30, "reward": [{"id": 2450002, "num": 1}, {"id": 22002, "num": 5}, {"id": 40202, "num": 5}]},
    3: {"id": 3, "num": 100, "reward": [{"id": 510001, "num": 1}, {"id": 520001, "num": 1}, {"id": 530001, "num": 1}]},
    4: {"id": 4, "num": 500, "reward": [{"id": 540002, "num": 1}, {"id": 550002, "num": 1}, {"id": 560002, "num": 1}]},
    5: {"id": 5, "num": 1000, "reward": [{"id": 5, "num": 10}, {"id": 40301, "num": 500}, {"id": 40103, "num": 10}]},
    6: {"id": 6, "num": 2000, "reward": [{"id": 5, "num": 15}, {"id": 40301, "num": 1000}, {"id": 40103, "num": 20}]},
    7: {"id": 7, "num": 3000, "reward": [{"id": 5, "num": 20}, {"id": 40301, "num": 1500}, {"id": 40103, "num": 30}]},
    8: {"id": 8, "num": 5000, "reward": [{"id": 5, "num": 25}, {"id": 40301, "num": 2000}, {"id": 40103, "num": 40}]},
    9: {"id": 9, "num": 7500, "reward": [{"id": 5, "num": 30}, {"id": 40301, "num": 3000}, {"id": 40103, "num": 50}]},
    10: {"id": 10, "num": 10000, "reward": [{"id": 5, "num": 40}, {"id": 40301, "num": 5000}, {"id": 40103, "num": 60}]},
}


class RechargeService:
    """充值发货与累计充值服务单例。"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self, db=None):
        self.db = db
        self.payment_cfg = copy.deepcopy(DEFAULT_PAYMENT_CFG)
        self.total_recharge_cfg = copy.deepcopy(DEFAULT_TOTAL_RECHARGE_CFG)
        self._load_configs_from_lua()

    @classmethod
    def get_instance(cls, db=None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db=db)
            elif db is not None:
                cls._instance.db = db
            return cls._instance

    def _load_configs_from_lua(self):
        """尝试从 decompiled_v2 配置表中加载全量配置以扩充内置词典。"""
        v5_dir = os.path.dirname(os.path.abspath(__file__))
        decomp_dir = os.path.join(os.path.dirname(v5_dir), "decompiled_v2", "x64", "game", "config")

        # 1. 加载 TotalRechargeCfg.lua
        tr_path = os.path.join(decomp_dir, "TotalRechargeCfg.lua")
        if os.path.isfile(tr_path):
            try:
                with open(tr_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                blocks = re.split(r'\n\s*\{\s*\n\s*version\s*=', content)[1:]
                for block in blocks:
                    id_m = re.search(r'id\s*=\s*(\d+)', block)
                    num_m = re.search(r'num\s*=\s*(\d+)', block)
                    rew_match = re.search(r'reward\s*=\s*\{(.*?)\}\s*,\s*reward_show', block, re.DOTALL)
                    if id_m and num_m and rew_match:
                        rid = int(id_m.group(1))
                        num = int(num_m.group(1))
                        items = re.findall(r'\{\s*(\d+)\s*,\s*(\d+)\s*\}', rew_match.group(1))
                        self.total_recharge_cfg[rid] = {
                            "id": rid,
                            "num": num,
                            "reward": [{"id": int(it[0]), "num": int(it[1])} for it in items]
                        }
            except Exception:
                pass

        # 2. 加载 PaymentCfg.lua
        pay_path = os.path.join(decomp_dir, "PaymentCfg.lua")
        if os.path.isfile(pay_path):
            try:
                with open(pay_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                blocks = re.split(r'\n\s*\{\s*\n\s*cost\s*=', content)[1:]
                for block in blocks:
                    cost_m = re.search(r'^(\d+)', block.strip())
                    id_m = re.search(r'id\s*=\s*(\d+)', block)
                    type_m = re.search(r'type\s*=\s*(\d+)', block)
                    pid_m = re.search(r'product_id\s*=\s*"([^"]+)"', block)
                    total_pt_m = re.search(r'total_point\s*=\s*(\d+)', block)
                    name_m = re.search(r'name\s*=\s*"([^"]+)"', block)
                    if id_m and cost_m:
                        pid = int(id_m.group(1))
                        cost = int(cost_m.group(1))
                        ptype = int(type_m.group(1)) if type_m else 1
                        name = name_m.group(1) if name_m else ""
                        d_amount = 0
                        dm = re.search(r'(\d+)', name)
                        if dm:
                            d_amount = int(dm.group(1))
                        if pid not in self.payment_cfg:
                            self.payment_cfg[pid] = {
                                "id": pid,
                                "cost": cost,
                                "type": ptype,
                                "product_id": pid_m.group(1) if pid_m else "",
                                "total_point": int(total_pt_m.group(1)) if total_pt_m else (cost // 100),
                                "name": name,
                                "diamond": d_amount
                            }
            except Exception:
                pass

    def get_payment_cfg(self, goods_id: int) -> dict:
        """获取商品配置，无则提供智能兜底。"""
        gid = int(goods_id or 1)
        if gid in self.payment_cfg:
            return copy.deepcopy(self.payment_cfg[gid])
        # 兜底
        return {
            "id": gid,
            "cost": 600,
            "type": 1,
            "product_id": f"com.yongshi.tenojo.goods_{gid}",
            "total_point": 6,
            "name": f"直购商品_{gid}",
            "diamond": 60
        }

    def get_total_recharge_tier(self, tier_id: int) -> dict:
        """获取累充档位配置。"""
        tid = int(tier_id or 1)
        if tid in self.total_recharge_cfg:
            return copy.deepcopy(self.total_recharge_cfg[tid])
        return {"id": tid, "num": 999999, "reward": []}

    def handle_pay_order(self, db, uid: int, goods_id: int, num: int = 1,
                         shop_id: int = 0, buy_id: int = 0, log=print) -> dict:
        """
        核心支付发货处理函数：
        1. 发放对应物品（移转之辉/月卡/礼包），支持首充双倍；
        2. 累加玩家 total_recharge_num 与 time_limit_recharge_num；
        3. 构建符合 order_net_rec 协议之回执并返回 rewards 列表。
        """
        import uuid
        now_ts = int(time.time())
        num = max(1, int(num or 1))
        cfg = self.get_payment_cfg(goods_id)
        order_id = f"ORDER_{now_ts}_{uuid.uuid4().hex[:12]}"
        cost_cents = int(cfg.get("cost") or 600) * num

        recharge_data = db.get_user_recharge(uid)
        first_ids = set(recharge_data.get("first_recharge_ids") or [])
        is_first = (goods_id not in first_ids) and (cfg.get("type") == 1)

        rewards = []

        # 1. 钻石直购发货
        if cfg.get("type") == 1:
            base_diamond = int(cfg.get("diamond") or 60) * num
            if is_first:
                # 触发首充双倍！额外赠送等量移转之花
                bonus_diamond = base_diamond
                total_diamond = base_diamond + bonus_diamond
                first_ids.add(goods_id)
                recharge_data["first_recharge_ids"] = sorted(list(first_ids))
                log(f"[PAY] 触发首充双倍！商品 goods_id={goods_id}, 基础={base_diamond}, 首充额外={bonus_diamond}, 总计={total_diamond} 移转之花")
            else:
                total_diamond = base_diamond
                log(f"[PAY] 充值直购发货: goods_id={goods_id}, 到账 {total_diamond} 移转之花")

            # 写入数据库货币表 (ID=30 iOS充值移转之花, 并联动维护 ID=5 移转之花总和)
            db.add_currency(uid, 30, total_diamond)
            try:
                c30 = db.get_item_num(uid, 30)
                c31 = db.get_item_num(uid, 31)
                c32 = db.get_item_num(uid, 32)
                db.set_currency(uid, 5, c30 + c31 + c32)
            except Exception:
                pass
            rewards.append({"id": 30, "num": total_diamond})

            # 联动新手活动首充
            try:
                n_act = db.get_newbie_activity(uid)
                n_up = {}
                if n_act.get("fr_first_gear") == 0:
                    n_up["fr_first_gear"] = 1
                    n_up["fr_new6"] = 1
                cur_pts = int(recharge_data.get("total_recharge_num") or 0)
                this_pts = int(cfg.get("total_point") or (cost_cents // 100))
                if (this_pts >= 18 or cur_pts + this_pts >= 18 or cost_cents >= 1800) and not n_act.get("fr_second_gear"):
                    n_up["fr_second_gear"] = 1
                    n_up["fr_new18"] = 1
                if n_up:
                    db.save_newbie_activity(uid, n_up)
            except Exception as _ne:
                log(f"[PAY] 新手首充联动异常: {_ne}")

        # 2. 月卡购买发货 (恒定观测)
        elif cfg.get("type") == 2:
            card_diamond = int(cfg.get("diamond") or 300) * num
            db.add_currency(uid, 1, card_diamond)
            rewards.append({"id": 1, "num": card_diamond})

            # 月卡到期时间增加 30 天
            m_rows = db.query("SELECT * FROM month_card WHERE uid=?", (uid,))
            cur_dead = int(m_rows[0].get("monthly_card_timestamp") or 0) if m_rows else 0
            base_ts = cur_dead if cur_dead > now_ts else now_ts
            new_dead = base_ts + 30 * 86400 * num
            db.upsert("month_card", uid, {
                "monthly_card_num": 1,
                "monthly_card_timestamp": new_dead,
                "is_sign": int(m_rows[0].get("is_sign") or 0) if m_rows else 0,
                "update_ts": now_ts
            })
            log(f"[PAY] 月卡充值成功: 到账 {card_diamond} 移转之辉, 月卡有效期延长至 {new_dead}")

            # 联动新手活动月卡角色奖励
            try:
                n_act = db.get_newbie_activity(uid)
                if not n_act.get("mc_flag"):
                    db.save_newbie_activity(uid, {"mc_flag": 1, "mc_role_flag": 0, "mc_new_role": 1})
            except Exception as _me:
                log(f"[PAY] 新手月卡联动异常: {_me}")

        # 3. 战令 / 合约 (Passport / BattlePass)
        elif cfg.get("type") == 3:
            is_full = goods_id in (202, 212, 222, 261, 262, 263, 9)
            is_upgrade = goods_id in (203, 213, 223, 271, 272, 273)
            target_pay_level = 202 if (is_full or is_upgrade) else 201
            db.set_battlepass_pay_level(uid, target_pay_level)
            log(f"[PAY] 战令/合约购买成功: pay_level 设置为 {target_pay_level}")

            # 若为深度合约或升级，附赠 10 级战令经验 (10,000点) 与专属额外奖励 (BattlePassListCfg[20032])
            if is_full or is_upgrade:
                db.add_currency(uid, 14, 10000)
                rewards.append({"id": 14, "num": 10000})
                db.add_item(uid, 2046, 1)
                rewards.append({"id": 2046, "num": 1})
                db.add_item(uid, 41201, 10)
                rewards.append({"id": 41201, "num": 10})
                db.add_item(uid, 53193, 5)
                rewards.append({"id": 53193, "num": 5})
                log(f"[PAY] 深度合约额外发放: 10级经验(10000) + 头像框2046 + 41201*10 + 53193*5")

            # 联动新手活动战令奖励
            try:
                n_act = db.get_newbie_activity(uid)
                if n_act.get("bp_reward") == 0:
                    db.save_newbie_activity(uid, {"bp_reward": 1, "bp_new": 1})
            except Exception as _be:
                log(f"[PAY] 新手战令联动异常: {_be}")

        # 4. 其他道具或通用发货
        else:
            base_diamond = int(cfg.get("diamond") or 0) * num
            if base_diamond > 0:
                db.add_currency(uid, 1, base_diamond)
                rewards.append({"id": 1, "num": base_diamond})

        # 5. 累计充值积分累加 (以元/积分 total_point 为单位，对齐 TotalRechargeCfg.lua)
        cost_cents = int(cfg.get("cost") or 600) * num
        total_pt = int(cfg.get("total_point") or (cost_cents // 100)) * num
        recharge_data["total_recharge_num"] += total_pt
        recharge_data["time_limit_recharge_num"] += total_pt
        db.save_user_recharge(uid, recharge_data)
        log(f"[PAY] 累计充值更新: +{total_pt} 积分(元), 当前累充总额={recharge_data['total_recharge_num']} 积分(元)")

        # 6. 全局 EventBus 广播
        try:
            from event_bus import bus, Events
            bus.emit(Events.RECHARGE_SUCCESS, ctx=None, uid=uid, goods_id=goods_id, cost_cents=cost_cents,
                     recharge_type=cfg.get("type", 1), rewards=rewards)
            if is_first:
                bus.emit(Events.FIRST_RECHARGE_TRIGGER, ctx=None, uid=uid, goods_id=goods_id, cost_cents=cost_cents)
            if cfg.get("type") == 2:
                bus.emit(Events.MONTHLY_CARD_TRIGGER, ctx=None, uid=uid, goods_id=goods_id, days=30)
            if cfg.get("type") == 3:
                bus.emit(Events.PASSPORT_BUY_TRIGGER, ctx=None, uid=uid, goods_id=goods_id, pay_level=target_pay_level)
        except Exception as _ee:
            log(f"[PAY] EventBus 广播异常: {_ee}")

        order_struct = {
            "order_id": order_id,
            "goods_id": goods_id,
            "num": num,
            "value": int(cost_cents or 0),
            "sign": "sandbox_mock_sign",
            "state": 1,
            "channel": 7,       # 7 = iOS
            "platform": 2,      # 2 = iOS
            "create_timestamp": now_ts,
            "extra_data": "{}",
            "shop_id": int(shop_id or 0),
            "shop_goods_id": int(buy_id or 0),
            "is_web_recharge": True
        }

        return {
            "result": 0,
            "order": order_struct,
            "reward": rewards,
            "total_recharge_num": recharge_data["total_recharge_num"],
            "first_recharge_ids": recharge_data["first_recharge_ids"],
            "goods_type": cfg.get("type", 1),
            "goods_id": goods_id
        }

    def reset_recharge_data(self, db, uid: int, scope: str = "all", log=print) -> dict:
        """
        重置玩家充值与付费状态（供 GM 控制面板及测试调用）：
        scope 可选：
          - "all": 重置全部付费数据
          - "first_recharge": 仅重置首充双倍标记
          - "battlepass": 仅重置战令购买状态
          - "noob_welfare": 仅重置首充/月卡新手福利及18元小签到
          - "total_recharge": 仅重置累充总额与已领档位
        """
        uid = int(uid)
        result = {"uid": uid, "reset_scopes": []}
        recharge_data = db.get_user_recharge(uid)

        # 1. 首充双倍重置
        if scope in ("all", "first_recharge"):
            recharge_data["first_recharge_ids"] = []
            db.save_user_recharge(uid, recharge_data)
            result["reset_scopes"].append("first_recharge")
            log(f"[GM] UID={uid} 首充双倍标记已重置清空")

        # 2. 战令购买状态重置
        if scope in ("all", "battlepass"):
            db.set_battlepass_pay_level(uid, 0)
            try:
                db.save_newbie_activity(uid, {"bp_reward": 0, "bp_new": 0})
            except Exception:
                pass
            result["reset_scopes"].append("battlepass")
            log(f"[GM] UID={uid} 战令已重置为未开通 (pay_level=0)")

        # 3. 新手首充/18元签到福利重置
        if scope in ("all", "noob_welfare"):
            n_reset = {
                "fr_first_gear": 0,
                "fr_second_gear": 0,
                "fr_now_sign": 0,
                "fr_last_sign_ts": 0,
                "fr_new6": 0,
                "fr_new18": 0,
                "mc_flag": 0,
                "mc_role_flag": 0,
                "mc_sign_times": 0,
                "mc_sign_reward": 0,
                "mc_new_role": 0,
                "mc_new_sign": 0,
            }
            try:
                db.save_newbie_activity(uid, n_reset)
            except Exception:
                pass
            result["reset_scopes"].append("noob_welfare")
            log(f"[GM] UID={uid} 新手首充与18元签到福利已重置")

        # 4. 累充金额与档位重置
        if scope in ("all", "total_recharge"):
            recharge_data["total_recharge_num"] = 0
            recharge_data["claimed_total_bonus"] = []
            recharge_data["time_limit_recharge_num"] = 0
            recharge_data["claimed_limit_bonus"] = []
            db.save_user_recharge(uid, recharge_data)
            result["reset_scopes"].append("total_recharge")
            log(f"[GM] UID={uid} 累充总额与已领档位已清零")

        return result

    def build_59009_payload(self, db, uid: int) -> dict:
        """构建当前玩家的 sc_59009 (新手首充/月卡/战令状态更新) 报文。"""
        n_act = db.get_newbie_activity(uid)
        return {
            "newbie_recharge_reward": {
                "first_recharge_reward": {
                    "first_gear_recharge_reward": int(n_act.get("fr_first_gear") or 0),
                    "second_gear_recharge_flag": bool(n_act.get("fr_second_gear") or 0),
                    "now_sign_times": int(n_act.get("fr_now_sign") or 0),
                    "last_sign_timestamp": int(n_act.get("fr_last_sign_ts") or 0),
                    "is_new_recharge_6": bool(n_act.get("fr_new6") or 0),
                    "is_new_recharge_18": bool(n_act.get("fr_new18") or 0),
                },
                "first_monthly_card_reward": {
                    "flag": bool(n_act.get("mc_flag") or 0),
                    "role_reward_flag": bool(n_act.get("mc_role_flag") or 0),
                    "sign_times": int(n_act.get("mc_sign_times") or 0),
                    "sign_reward_flag": bool(n_act.get("mc_sign_reward") or 0),
                    "is_new_tag_role_reward": bool(n_act.get("mc_new_role") or 0),
                    "is_new_tag_sign_reward": bool(n_act.get("mc_new_sign") or 0),
                },
                "first_battlepass_reward": {
                    "first_battlepass_reward": int(n_act.get("bp_reward") or 0),
                    "is_new_battlepass_reward": bool(n_act.get("bp_new_tag") or 0),
                }
            }
        }

    def build_34007_payload(self, db, uid: int) -> dict:
        """为 generator.py 构建当前玩家的 sc_34007 (累计充值状态) 动态下发报文。"""
        recharge_data = db.get_user_recharge(uid)
        tot_ver = int(recharge_data.get("total_recharge_version") or 2)
        if tot_ver < 2:
            tot_ver = 2
        lim_ver = int(recharge_data.get("time_limit_version") or 2)
        if lim_ver < 2:
            lim_ver = 2
        return {
            "total_recharge_num": int(recharge_data.get("total_recharge_num") or 0),
            "receive_total_recharge_list": [int(x) for x in (recharge_data.get("claimed_total_bonus") or [])],
            "total_recharge_version": tot_ver,
            "time_limit_recharge_num": int(recharge_data.get("time_limit_recharge_num") or 0),
            "now_time_limit_version": lim_ver,
            "receive_time_limit_recharge_list": [int(x) for x in (recharge_data.get("claimed_limit_bonus") or [])],
        }

    def build_34021_payload(self, db, uid: int) -> dict:
        """为 generator.py 构建当前玩家的 sc_34021 (首充双倍标记) 动态下发报文。"""
        recharge_data = db.get_user_recharge(uid)
        return {
            "first_recharge_id_list": [int(x) for x in (recharge_data.get("first_recharge_ids") or [])],
        }

    def handle_claim_total_bonus(self, db, uid: int, id_list: list, log=print) -> dict:
        """处理常驻累计充值领奖：cs_34012 {id_list} -> sc_34013 {result, reward_list, id_list}。"""
        recharge_data = db.get_user_recharge(uid)
        claimed = set(recharge_data.get("claimed_total_bonus") or [])
        cur_total_points = int(recharge_data.get("total_recharge_num") or 0)

        success_ids = []
        all_rewards = []

        for item_id in id_list:
            bid = int(item_id)
            if bid in claimed:
                continue
            tier = self.get_total_recharge_tier(bid)
            # 档位门槛金额（元/积分）<= 累充积分（元/积分）
            if tier["num"] <= cur_total_points:
                claimed.add(bid)
                success_ids.append(bid)
                for it in tier["reward"]:
                    iid = int(it["id"])
                    inum = int(it["num"])
                    all_rewards.append({"id": iid, "num": inum})
                    # 真实发放到数据库背包
                    db.add_material(uid, iid, inum)

        if success_ids:
            recharge_data["claimed_total_bonus"] = sorted(list(claimed))
            db.save_user_recharge(uid, recharge_data)
            log(f"[RECHARGE_CLAIM] 成功领取常驻累充档位: {success_ids}, 发放奖励 {len(all_rewards)} 项")

        return {
            "result": 0,
            "reward_list": all_rewards,
            "id_list": id_list  # 按照官方客户端习惯，回显请求的 id_list
        }

    def handle_claim_version_bonus(self, db, uid: int, id_list: list, log=print) -> dict:
        """处理版本限时累计充值领奖：cs_34118 {id_list} -> sc_34119。"""
        recharge_data = db.get_user_recharge(uid)
        claimed = set(recharge_data.get("claimed_limit_bonus") or [])
        cur_limit_points = int(recharge_data.get("time_limit_recharge_num") or 0)

        success_ids = []
        all_rewards = []

        for item_id in id_list:
            bid = int(item_id)
            if bid in claimed:
                continue
            tier = self.get_total_recharge_tier(bid)
            if tier["num"] <= cur_limit_points:
                claimed.add(bid)
                success_ids.append(bid)
                for it in tier["reward"]:
                    iid = int(it["id"])
                    inum = int(it["num"])
                    all_rewards.append({"id": iid, "num": inum})
                    db.add_material(uid, iid, inum)

        if success_ids:
            recharge_data["claimed_limit_bonus"] = sorted(list(claimed))
            db.save_user_recharge(uid, recharge_data)
            log(f"[RECHARGE_CLAIM] 成功领取版本限时累充档位: {success_ids}")

        return {
            "result": 0,
            "reward_list": all_rewards,
            "id_list": id_list,
            "receive_time_limit_recharge_list": recharge_data["claimed_limit_bonus"]
        }


# 全局单例便捷访问
recharge_service = RechargeService.get_instance()
