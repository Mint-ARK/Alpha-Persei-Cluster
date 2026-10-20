# 第38轮｜钥从十连实例、返利与扣券对照

扫描日期：2026-09-22。

## 检查结果

| 检查项 | 发现与结果 |
| --- | --- |
| 实例创建 | 确定的十连结果生成十个不同实例，模板与返回奖励逐项一致，原实例保留。 |
| 返利与消耗 | 五星100、八个三星各10、四星20，当前组合返利合计200；抽卡券减少10，获得事件数量合计10。 |

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `draw_service.py` | draw、calc_draw_rebate |
| `inventory_service.py` | grant_items、_grant_servants_batch |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A61 | [抽卡发奖失败仍扣券、重置保底并返回成功](<health_audit_20260921_round34_draw_settlement.md>) |
| A63 | [钥从和刻印已入库，在线客户端与图鉴未刷新](<health_audit_20260922_round39_servant_notify.md>) |
