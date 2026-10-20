# 第78轮｜Mode 4活动与版本归属

扫描日期：2026-10-07。问题编号：A107。

## A107｜Mode4 已关闭活动仍能结算，记录与广播归属不一致

### 出现什么现象

已关闭或请求不匹配的挑战活动仍能结算，入库活动与回复活动还可能不同。

### 初步定位

结算按关卡反查活动却不验证请求归属和开放状态；未知关卡退回默认 Mode1，广播又使用原请求 activity_id。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | 挑战模式结算与广播 |
| `weekly_challenge_service.py` | get_core_verification_challenge_data |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A94 | [Mode4 恢复遗漏队伍和挑战关，第二队编号也会丢失](<health_audit_20261007_round71_core_mode4_progress.md>) |
| A95 | [Mode4 任务通关后仍不显示可领奖](<health_audit_20261007_round71_core_mode4_progress.md>) |
| A96 | [Mode4 挑战关类型未识别，结算遗漏专属记录和刷新](<health_audit_20261007_round72_core_mode4_lifecycle.md>) |
| A99 | [Mode4 重置后旧战斗仍可结算，已清成绩重新出现](<health_audit_20261007_round73_core_mode4_old_settlement.md>) |
| A105 | [重复结算请求返回旧缓存，客户端成绩与次数回退](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
