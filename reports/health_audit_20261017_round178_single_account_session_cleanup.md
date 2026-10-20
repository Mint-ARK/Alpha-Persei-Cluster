# 第178轮｜单账号在途会话与资源清理

扫描日期：2026-10-17。问题编号：A376。

## A376｜战斗连接超时后未清理心跳序号，同一连接编号重建沿用旧状态

### 出现什么现象

战斗会话回收后，Ping游标仍留内存，长期累积且同ID新会话沿用旧值。

### 初步定位

_conns和_results有回收，_pong_seq没有同步清理、上限或代次检查；FIN、ERR也只移除主会话。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `battle_server.py` | _pong_seq、_gc_loop、FIN与ERR |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A13 | [首通判断与单账号存档的归属关系](<health_audit_20260920_round4.md>) |
| A20 | [同一场战斗可被两个请求同时结算](<health_audit_20260920_round5.md>) |
| A21 | [同毫秒建立的不同连接可能生成相同战斗编号](<health_audit_20260920_round5.md>) |
| A105 | [重复结算请求返回旧缓存，客户端成绩与次数回退](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A306 | [同账号仍有另一连接在线，关闭一个连接却标记离线](<health_audit_20261014_round145_login_ready_reconnect.md>) |
| A307 | [战斗会话丢失后结算返回成功，退出状态与客户端预期不同](<health_audit_20261014_round146_reconnect_consumers.md>) |
