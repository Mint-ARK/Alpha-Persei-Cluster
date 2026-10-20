# 第174轮｜梦境和迭代恢复的活动身份及查询失败

扫描日期：2026-10-17。问题编号：A374。

## A374｜Mode3 恢复使用错误活动编号，退回 Mode1 关卡和零分

### 出现什么现象

迭代Mode3登录恢复引用不存在活动，显示成Mode1关卡和零分。

### 初步定位

89431生成器固定传3943101，而当前有效Mode3活动是3941901、4241901；服务对未知活动回退Mode1，生成器又忽略实际模式。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `generator.py` | 89431 |
| `weekly_challenge_service.py` | get_core_verification_challenge_data |
| `core_verification_cl_catalog.json` | mode3 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A04 | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| A51 | [日常和周常已重置，客户端却未收到刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
| A94 | [Mode4 恢复遗漏队伍和挑战关，第二队编号也会丢失](<health_audit_20261007_round71_core_mode4_progress.md>) |
| A107 | [Mode4 已关闭活动仍能结算，记录与广播归属不一致](<health_audit_20261007_round78_core_mode4_activity.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A373 | [周常数据读取失败被当作空状态，成绩、英雄和徽章显示丢失](<health_audit_20261017_round173_weekly_stage_restore.md>) |
