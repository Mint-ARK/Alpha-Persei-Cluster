# 第172轮｜任务/活动恢复固定值与日周月时间源

扫描日期：2026-10-17。问题编号：A370、A371。

## A370｜任务恢复固定返回旧阶段，后续阶段和有效任务不显示

### 出现什么现象

任务列表顶部阶段固定，后阶段入口可能不开放。

### 初步定位

正常28001入口提前调用固定newbie_phase19、assignment_phase1的生成器，后面的动态阶段分支不可达；客户端两字段分别用于剧情与新手阶段。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `generator.py` | gen_28001与28001后置分支 |
| `taskaction.lua` | 阶段字段 |
| `taskdata2.lua` | 阶段消费 |

## A371｜时间同步包按四点重置，任务与签到实际按五点刷新

### 出现什么现象

时间同步会把登录05:00刷新时间改成04:00，月初凌晨还会排错月份。

### 初步定位

10503使用05:00，10501仍按04:00生成；月刷新在当月05:00之前也没有与实际周期起点保持一致。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | gen_10501_payload、_refresh_timestamps |
| `generator.py` | 10503 |
| `lazy_timer.py` | 周期起点 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A51 | [日常和周常已重置，客户端却未收到刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
| A107 | [Mode4 已关闭活动仍能结算，记录与广播归属不一致](<health_audit_20261007_round78_core_mode4_activity.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A256 | [已有一个新手任务就跳过补建，后续阶段任务缺失](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| A260 | [月卡签到回包补入默认状态，在线已签到标记被覆盖](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| A261 | [普通月卡缺行或过期仍生成未来期限，并允许领取日奖](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
