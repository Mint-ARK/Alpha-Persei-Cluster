# 第168轮｜商城/PAY/周期礼包广播与直接发货

扫描日期：2026-10-15。问题编号：A365。

## A365｜指定商店购买事件丢失店号，相关任务不增长

### 出现什么现象

指定商店购买任务不增长，普通不限商店任务却正常。

### 初步定位

SHOP_BUY已经带shop_id，消费者调用条件600时没有传param；匹配器遇到指定商店限制和空参数便跳过。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `shop_service.py` | SHOP_BUY |
| `task_listener.py` | on_shop_buy与条件参数 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A242 | [周期礼包扣费成功但订阅或首日邮件缺失](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A244 | [周期礼包次日邮件已生成，在线未读和红点未刷新](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A262 | [月卡购买后只更新签到标记，客户端期限未刷新](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| A268 | [PAY 直购把不同资产槽位混算，花币余额被错误覆盖](<health_audit_20261013_round131_ios229_pay_delivery.md>) |
| A269 | [PAY 首充福利写入失败，主商品和首购记录却已提交](<health_audit_20261013_round131_ios229_pay_delivery.md>) |
| A270 | [战令购买允许错误升级、重复附赠和等级回退](<health_audit_20261013_round131_ios229_pay_delivery.md>) |
