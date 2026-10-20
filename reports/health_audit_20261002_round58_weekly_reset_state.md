# 第58轮｜梦境重置与锁定状态

扫描日期：2026-10-02。问题编号：A83。

## A83｜梦境已领奖后仍能重置，成绩与英雄锁定被清除

### 出现什么现象

梦境已经领取奖励后，仍可通过重置清除成绩并释放角色。

### 初步定位

单首领和整轮重置直接删除进度，没有读取领奖台账检查是否允许重置。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | BossChallengeResetHardBossOp、BossChallengeResetHardModeOp |
| `account_db.py` | 梦境重置 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A79 | [梦境未达标、已领取或未知档位仍能发奖](<health_audit_20260928_round54_weekly_claim.md>) |
