# 第165轮｜资产与战斗反馈的事件检查

扫描日期：2026-10-15。问题编号：A363。

## A363｜钥从精炼、锁定和吞噬引用未定义事件，后续联动不执行

### 出现什么现象

钥从精炼、锁定和吞噬成功后，对应内部事件实际没有发出。

### 初步定位

服务访问的 SERVANT_PROMOTE、SERVANT_LOCK、SERVANT_DESTROY 未在Events定义，属性访问先报错，又被except忽略，尚未进入emit。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `servant_service.py` | promote_servant、lock_servant、consume_servants_as_food |
| `event_bus.py` | Events |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A02 | [事件监听器内部报错后被再次调用，同一事件可能执行两遍](<health_audit_20260920.md>) |
| A04 | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| A07 | [统一扣费未触发消费事件，相关任务不增长](<health_audit_20260920_round2.md>) |
| A08 | [跨日处理失败仍记为已刷新，当天无法重试](<health_audit_20260920_round2.md>) |
| A14 | [一次胜利触发两次通关事件，任务可能重复计数](<health_audit_20260920_round4.md>) |
| A51 | [日常和周常已重置，客户端却未收到刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
| A63 | [钥从和刻印已入库，在线客户端与图鉴未刷新](<health_audit_20260922_round39_servant_notify.md>) |
| A64 | [抽到角色后缺少解锁刷新，阵营收集进度也不更新](<health_audit_20260922_round40_hero_notify.md>) |
| A105 | [重复结算请求返回旧缓存，客户端成绩与次数回退](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A157 | [战斗成长依赖的表和列缺失，部分成长被跳过](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| A171 | [连携条件进度已增长，客户端没有即时刷新](<health_audit_20261009_round111_combo_relation_feedback.md>) |
