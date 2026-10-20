# 第135轮｜商店周期恢复、兑换资格与聚合花币扣费

扫描日期：2026-10-13。问题编号：A277、A278。

## A277｜商店月限购用固定 31 天计算，下次刷新日期偏移

### 出现什么现象

月限购显示的到期时间比实际月初重置更晚。

### 初步定位

收据期限按本月初加31天计算，而定时器按下一自然月1日05:00重置；短月份产生一至三天偏差。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `shop_service.py` | 月限购next_refresh |
| `lazy_timer.py` | 月脉冲 |
| `timer_listeners.py` | 月重置 |

## A278｜花币未按平台槽位聚合，余额足够仍提示不足

### 出现什么现象

iOS充值货币余额足够，商店仍提示不足。

### 初步定位

商店把协议合计报价当单槽余额处理，省略客户端成本后自身拆扣也只查31、32，遗漏iOS30，没有生成平台允许的扣费组合。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `shop_service.py` | _cost_breakdown与buy_goods |
| `inventory_service.py` | 单槽余额 |
| `currencydata.lua` | 平台合计 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A01 | [商店按客户端上报金额扣费，可能绕过配置价格](<health_audit_20260920.md>) |
| A04 | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| A08 | [跨日处理失败仍记为已刷新，当天无法重试](<health_audit_20260920_round2.md>) |
| A51 | [日常和周常已重置，客户端却未收到刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
| A274 | [累充和战令装扮被当普通材料发放，名片里无法使用](<health_audit_20261013_round133_recharge_special_assets.md>) |
| A275 | [商店未核对货架与店铺归属，未上架或跨店商品也能买](<health_audit_20261013_round134_common_shop_purchase.md>) |
| A276 | [永久限购按一天后过期处理，旧商品可再次兑换](<health_audit_20261013_round134_common_shop_purchase.md>) |
