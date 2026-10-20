# 第166轮｜任务、活跃、战令与周期/登录广播

扫描日期：2026-10-15。问题编号：A364。

## A364｜登录先记跨日完成再更新任务，后续失败后同日无法补算

### 出现什么现象

登录任务或累计签到写入失败后，同日重试无法补回。

### 初步定位

监听先保存05:00周期水位，再更新任务和累计；异常被本地或EventBus吞掉，签到仍提交，旧水位令下一次登录提前去重。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `task_listener.py` | on_user_login的水位和累计写入 |
| `event_bus.py` | 异常处理 |
| `operations.py` | DailySignOp |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A04 | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| A08 | [跨日处理失败仍记为已刷新，当天无法重试](<health_audit_20260920_round2.md>) |
| A47 | [一次活跃度奖励触发两次相同事件](<health_audit_20260921_round21_task_inventory.md>) |
| A48 | [战令经验只到账一次，周累计经验却增加两次](<health_audit_20260921_round21_task_inventory.md>) |
| A49 | [一次活跃事件匹配多个条件，任务进度重复增长](<health_audit_20260921_round22_activity_consumer.md>) |
| A51 | [日常和周常已重置，客户端却未收到刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A306 | [同账号仍有另一连接在线，关闭一个连接却标记离线](<health_audit_20261014_round145_login_ready_reconnect.md>) |
