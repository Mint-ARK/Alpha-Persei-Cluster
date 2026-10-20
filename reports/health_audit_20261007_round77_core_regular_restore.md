# 第77轮｜常规迭代重置、空快照与最佳阵容

扫描日期：2026-10-07。

## 检查结果

| 检查项 | 发现与结果 |
| --- | --- |
| 正常重置 | 75011成功回调先清客户端旧通关，再接收空75009，正常序列能够清除旧状态。 |
| 单独空快照 | UpdateCycleInfo只覆盖收到的关卡，不先清缓存；没有重置回调时，空列表本身不会否定旧通关。 |
| 最佳阵容混配 | 重置当前锁定后以较低分新队伍通关，数据库保留历史高分，max_score_info却使用新锁定角色，分数和阵容来自不同对局。 |

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | CoreVerificationResetChallengeOp |
| `weekly_challenge_service.py` | get_core_verification_data、max_score_info |
| `coreverificationdata.lua` | ResetStage、UpdateCycleInfo、GetMaxScoreHeroInfo |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A94 | [Mode4 恢复遗漏队伍和挑战关，第二队编号也会丢失](<health_audit_20261007_round71_core_mode4_progress.md>) |
| A101 | [迭代校验轮次编号循环复用，旧成绩和领史可能重新生效](<health_audit_20261007_round74_core_regular_cycle.md>) |
| A105 | [重复结算请求返回旧缓存，客户端成绩与次数回退](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
| A106 | [迭代校验成绩刷新包结构错误，分数编码为空](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
