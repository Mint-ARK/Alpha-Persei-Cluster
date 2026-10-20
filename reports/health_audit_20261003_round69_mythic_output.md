# 第69轮｜黑区成绩输出与在线恢复入口

扫描日期：2026-10-03。

## 检查结果

| 检查项 | 发现与结果 |
| --- | --- |
| 成绩输出 | 核对结算、查询中的分区、难度、星级和领奖状态，客户端通过相应状态包恢复。 |
| 问题串联 | 双队接力、切档、半周周期和常规星奖分别记录在A88—A92，避免把不同模式的字段与重置规则混用。 |

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `weekly_challenge_service.py` | 黑区状态构造 |
| `middleware.py` | 黑区结算输出 |
| `generator.py` | 黑区登录恢复 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A88 | [黑区第一队完成后，未进入第二队待战状态](<health_audit_20261003_round64_mythic_relay.md>) |
| A89 | [黑区切换难度保留旧成绩，不同难度进度混合](<health_audit_20261003_round65_mythic_transition.md>) |
| A90 | [黑区半周刷新按固定 3.5 天倒推，旧数据漏清或新进度被清](<health_audit_20261003_round67_mythic_cycle.md>) |
| A91 | [黑区星级奖励缺少达标与已领检查](<health_audit_20261003_round68_mythic_normal.md>) |
| A92 | [黑区星级列表固定满星，与实际成绩不符](<health_audit_20261003_round68_mythic_normal.md>) |
