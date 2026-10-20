# 第79轮｜Mode4最高分重置

扫描日期：2026-10-07。问题编号：A108。

## A108｜Mode4 重置后的即时最高分与重登结果不同

### 出现什么现象

Mode4 重置后，客户端即时最高分和重新加载结果不同。

### 初步定位

重置删除了保存最高分的主关记录，之后恢复按空记录返回零；重置回复只发成功，没有即时同步新的最高分状态。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `account_db.py` | reset_core_verification_cl |
| `weekly_challenge_service.py` | Mode4 max_point |
| `operations.py` | CoreVerificationMode4ResetStageOp |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A94 | [Mode4 恢复遗漏队伍和挑战关，第二队编号也会丢失](<health_audit_20261007_round71_core_mode4_progress.md>) |
| A97 | [Mode4 单队重置只返回成功，持久进度没有变化](<health_audit_20261007_round72_core_mode4_lifecycle.md>) |
| A99 | [Mode4 重置后旧战斗仍可结算，已清成绩重新出现](<health_audit_20261007_round73_core_mode4_old_settlement.md>) |
