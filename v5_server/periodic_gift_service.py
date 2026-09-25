# -*- coding: utf-8 -*-
"""
periodic_gift_service.py — 周期连续时间礼包领域服务 (PeriodicGiftService)

负责《深空之眼》商店中以 SHOP_SEVEN_PACKS (sub_type=505, 7日/14日包) 与 季卡 (sub_type=509, 周年探测季卡)
为代表的连续多日周期礼包的全生命周期管理。

【架构链路】：
  ShopService (购买触发) 
      └──> EventBus.emit(Events.PERIODIC_GIFT_BUY)
              └──> PeriodicGiftService.on_gift_buy
                      ├──> 入库 user_periodic_gift (订阅激活)
                      └──> 首日邮件立即下发 (剩余 total_days - 1 天)

  LazyTimer / UserLogin (跨天流逝 / 每日 05:00 / 登录脉冲)
      └──> EventBus.emit(Events.DAILY_RESET_5AM / Events.USER_LOGIN)
              └──> PeriodicGiftService.check_and_dispatch_daily
                      ├──> 检索 active 订阅，校验当日未派发
                      ├──> 自然日/登录推演，递减 remain_days
                      ├──> MailService.send_mail (模板ID、标题"剩余%s天"、附件每日配额)
                      └──> 全部天数派发完毕后更新 status=2 (已履约完成)
"""

import os
import json
import time
import datetime
import logging

from event_bus import bus, Events
from mail_service import MailService
from lazy_timer import get_daily_5am_ts

logger = logging.getLogger("periodic_gift")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(BASE_DIR, "periodic_gift_catalog.json")


class PeriodicGiftService:
    """连续时间/周期礼包领域服务单例"""

    _instance = None
    _CATALOG = None

    def __init__(self, db=None):
        self.db = db
        self._ensure_loaded_catalog()

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    def _ensure_loaded_catalog(self):
        """加载 66 款连续周期礼包配置元数据与邮件模板"""
        if self._CATALOG is None:
            catalog = {}
            if os.path.exists(CATALOG_PATH):
                try:
                    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                        catalog = json.load(f)
                except Exception as e:
                    logger.error(f"[PeriodicGiftService] 载入 periodic_gift_catalog.json 异常: {e}")
            self._CATALOG = catalog
            logger.info(f"[PeriodicGiftService] 成功载入周期礼包元数据: {len(catalog)} 档配置")

    def _get_db(self, db=None):
        if db is not None:
            return db
        if self.db is not None:
            return self.db
        try:
            from account_db import get_db
            self.db = get_db()
        except Exception:
            pass
        return self.db

    def get_gift_cfg(self, goods_or_desc_id):
        """根据 goods_id 或 description (desc_id) 检索礼包元数据"""
        self._ensure_loaded_catalog()
        gid_str = str(goods_or_desc_id)
        return self._CATALOG.get(gid_str)

    def is_periodic_gift(self, goods_or_desc_id):
        """判断是否为周期连续时间礼包 (sub_type 505 或 509)"""
        cfg = self.get_gift_cfg(goods_or_desc_id)
        return bool(cfg and int(cfg.get("sub_type") or 0) in (505, 509))

    def on_gift_buy(self, ctx, uid, goods_id, desc_id, buy_num=1):
        """
        响应商店购买连续礼包事件 (Events.PERIODIC_GIFT_BUY)：
        1. 校验礼包配置；
        2. 在 user_periodic_gift 表中插入订阅记录；
        3. 立即下发第 1 天配额邮件，并将剩余天数置为 total_days - 1；
        4. 标记 last_dispatch_date 为当日 5am 周期。
        """
        _db = self._get_db()
        if not _db:
            logger.error("[PeriodicGiftService] 无法获取数据库连接")
            return False

        cfg = self.get_gift_cfg(desc_id) or self.get_gift_cfg(goods_id)
        if not cfg:
            logger.warning(f"[PeriodicGiftService] 未找到周期礼包元数据: goods_id={goods_id}, desc_id={desc_id}")
            return False

        now_ts = int(time.time())
        cur_5am = get_daily_5am_ts(now_ts)
        cur_date = datetime.datetime.fromtimestamp(cur_5am).strftime("%Y-%m-%d")

        total_days = int(cfg.get("total_days") or 0)
        template_id = int(cfg.get("template_id") or 0)
        daily_items = cfg.get("daily_items") or []
        daily_json = json.dumps(daily_items, ensure_ascii=False)
        mtpl = cfg.get("mail_template") or {}

        mail_svc = MailService.get_instance(_db)

        for _ in range(int(buy_num or 1)):
            # 首日购买立即派发第 1 天配额，剩余天数置为 total_days - 1
            remain_days = max(0, total_days - 1)
            status = 1 if remain_days > 0 else 2

            _db.execute(
                """INSERT INTO user_periodic_gift (
                    uid, goods_id, desc_id, template_id, total_days,
                    remain_days, daily_rewards_json, last_dispatch_date,
                    status, create_ts, update_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, int(goods_id or 0), int(desc_id or 0), template_id, total_days,
                 remain_days, daily_json, cur_date, status, now_ts, now_ts)
            )

            # 组装首日邮件
            raw_title = mtpl.get("mail_title") or f"{cfg.get('name', '周期礼包')}（剩余%s天）"
            raw_desc = mtpl.get("mail_desc") or f"亲爱的管理员，您采购的{cfg.get('name', '周期礼包')}（剩余%s天）已存放至邮箱，请及时在本邮件附件内查收。"
            title = raw_title.replace("%s", str(remain_days))
            desc = raw_desc.replace("%s", str(remain_days))
            sender = mtpl.get("mail_sender") or "深空之眼第九部门"

            mail_svc.send_mail(
                uid=uid,
                title=title,
                content=desc,
                attachments=daily_items,
                sender=sender,
                template_id=template_id,
                db=_db
            )
            logger.info(f"[PeriodicGiftService] 成功激活订阅并派发首日邮件: uid={uid}, desc_id={desc_id}, 剩余天数={remain_days}")

        return True

    def check_and_dispatch_daily(self, ctx, uid, now_ts=None):
        """
        每日 05:00 跨天重置或玩家登录脉冲时触发：
        检查所有处于生效中 (status=1) 的周期礼包订阅，按日推演补发当日配额邮件。
        """
        _db = self._get_db()
        if not _db:
            return

        now_ts = int(now_ts) if now_ts else int(time.time())
        cur_5am = get_daily_5am_ts(now_ts)
        cur_date = datetime.datetime.fromtimestamp(cur_5am).strftime("%Y-%m-%d")

        rows = _db.query("SELECT * FROM user_periodic_gift WHERE uid=? AND status=1", (uid,))
        if not rows:
            return

        mail_svc = MailService.get_instance(_db)

        for row in rows:
            sub_id = int(row["id"])
            last_date_str = str(row.get("last_dispatch_date") or "").strip()

            if last_date_str == cur_date:
                # 当日周期已派发过，跳过
                continue

            # 计算跨日天数
            if last_date_str:
                try:
                    last_d = datetime.datetime.strptime(last_date_str, "%Y-%m-%d").date()
                    cur_d = datetime.datetime.fromtimestamp(cur_5am).date()
                    diff_days = (cur_d - last_d).days
                except Exception:
                    diff_days = 1
            else:
                diff_days = 1

            if diff_days <= 0:
                continue

            remain_days = int(row.get("remain_days") or 0)
            if remain_days <= 0:
                _db.execute("UPDATE user_periodic_gift SET status=2, update_ts=? WHERE id=?", (now_ts, sub_id))
                continue

            desc_id = int(row.get("desc_id") or 0)
            cfg = self.get_gift_cfg(desc_id)
            mtpl = (cfg.get("mail_template") if cfg else {}) or {}
            raw_title = mtpl.get("mail_title") or f"{cfg.get('name', '周期礼包') if cfg else '周期礼包'}（剩余%s天）"
            raw_desc = mtpl.get("mail_desc") or "亲爱的管理员，今日周期礼包已发放，请在附件中查收。"
            sender = mtpl.get("mail_sender") or "深空之眼第九部门"
            daily_items = json.loads(row.get("daily_rewards_json") or "[]")
            template_id = int(row.get("template_id") or 0)

            # 按实际应补发天数派发邮件（至多消耗 remain_days）
            send_count = min(diff_days, remain_days)
            for _ in range(send_count):
                remain_days -= 1
                title = raw_title.replace("%s", str(remain_days))
                desc = raw_desc.replace("%s", str(remain_days))
                mail_svc.send_mail(
                    uid=uid,
                    title=title,
                    content=desc,
                    attachments=daily_items,
                    sender=sender,
                    template_id=template_id,
                    db=_db
                )

            new_status = 2 if remain_days <= 0 else 1
            _db.execute(
                "UPDATE user_periodic_gift SET remain_days=?, last_dispatch_date=?, status=?, update_ts=? WHERE id=?",
                (remain_days, cur_date, new_status, now_ts, sub_id)
            )
            logger.info(
                f"[PeriodicGiftService] uid={uid} sub_id={sub_id} 补发 {send_count} 封邮件, "
                f"剩余天数: {remain_days}, 新状态: {new_status}"
            )


# ==================== 全局事件监听器绑定 ====================

@bus.subscribe(Events.PERIODIC_GIFT_BUY)
def _on_periodic_gift_buy(ctx, uid, **kwargs):
    goods_id = kwargs.get("goods_id", 0)
    desc_id = kwargs.get("desc_id", 0)
    buy_num = kwargs.get("buy_num", 1)
    PeriodicGiftService.get_instance().on_gift_buy(ctx, uid, goods_id, desc_id, buy_num)


@bus.subscribe(Events.DAILY_RESET_5AM)
def _on_daily_reset_5am(ctx, uid, **kwargs):
    now_ts = kwargs.get("now_ts")
    PeriodicGiftService.get_instance().check_and_dispatch_daily(ctx, uid, now_ts=now_ts)


@bus.subscribe(Events.USER_LOGIN)
def _on_user_login(ctx, uid, **kwargs):
    now_ts = kwargs.get("now_ts")
    PeriodicGiftService.get_instance().check_and_dispatch_daily(ctx, uid, now_ts=now_ts)
