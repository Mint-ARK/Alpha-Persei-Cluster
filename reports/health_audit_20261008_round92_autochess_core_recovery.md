# 第92轮｜自走棋Core通知与同账号恢复

扫描日期：2026-10-08。问题编号：A135、A136。

## A135｜自走棋任务进度已保存，任务刷新通知却未发送

### 出现什么现象

结算后任务进度已经变化，客户端却收不到即时任务更新。

### 初步定位

任务监听器把28007放入请求上下文待发送队列，而自走棋结算只回复89201、89125、89127，没有取出这批推送；下一请求会清空旧队列。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | AutoChessSettleBattleOp.respond |
| `task_listener.py` | 28007推送 |

## A136｜自走棋操作回滚后内存缓存未恢复，在线状态与存档不同

### 出现什么现象

数据库事务失败后，当前会话仍保留已扣金币和已买棋子，重登结果却不同。

### 初步定位

apply 直接修改共享 _sessions 对象，Core回滚只覆盖SQLite；内存没有回滚，后续保存还可能把错误缓存重新写入数据库。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `autochess_service.py` | _sessions 与购买应用 |
| `core.py` | 事务回滚 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A03 | [业务已保存但回包失败，客户端重试可能再次扣费](<health_audit_20260920.md>) |
| A04 | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| A109 | [自走棋读取失败覆盖旧局，保存失败仍返回开局成功](<health_audit_20261007_round80_autochess_entry.md>) |
| A115 | [自走棋失败结果未保存，重建对局后被默认判胜](<health_audit_20261007_round84_autochess_round_restore.md>) |
| A117 | [自走棋重复通关重复触发首次通关事件](<health_audit_20261007_round85_autochess_clear_medal.md>) |
| A118 | [自走棋配置积分未发放，补入库存后也不推进任务](<health_audit_20261007_round85_autochess_clear_medal.md>) |
| A119 | [自走棋回放返回成功，却缺少录像和行动数据](<health_audit_20261007_round86_autochess_cancel_replay.md>) |
