# 第25轮｜普通换日与长离线后的周期恢复

扫描日期：2026-09-21。

## 检查结果

| 检查项 | 发现与结果 |
| --- | --- |
| 普通日05:00 | 日常领取态清零，周常领取态、周活跃及战令周经验保留。 |
| 离线45天 | 月、周一、周四、日事件依次各处理一次，直接收敛到当前周期，没有逐日循环重置。TIME_TICK间隔按当前规则最多取30天。 |
| 同周期再次脉冲 | 数据库水位阻止重复重置，新领取状态得以保留。 |

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `lazy_timer.py` | 周期计算与LazyTimerBroadcaster |
| `timer_listeners.py` | 日、周任务重置 |
| `account_db.py` | get_timer_watermark、update_timer_watermark |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A08 | [跨日处理失败仍记为已刷新，当天无法重试](<health_audit_20260920_round2.md>) |
| A50 | [补建任务缺少周期类型，已领状态跨日或跨周不清除](<health_audit_20260921_round23_task_cycle.md>) |
| A51 | [日常和周常已重置，客户端却未收到刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
