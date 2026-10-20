# 第177轮｜跨服务事务、BUS回调与提交失败恢复

扫描日期：2026-10-17。问题编号：A375。

## A375｜数据库提交失败后事务未恢复，后续请求可能误提交旧修改

### 出现什么现象

提交失败后仍残留SQLite事务，后续请求可能把本次失败修改顺带提交。

### 初步定位

事务退出在commit异常时仍清应用in_txn标志，却没有回滚或隔离底层事务；之后execute按标志自动commit，误提交残留修改。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `account_db.py` | _Transaction.__exit__、execute |
| `core.py` | 提交错误转结果9 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A06 | [邮件发奖失败仍标记已领取，并返回成功](<health_audit_20260920_round2.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A242 | [周期礼包扣费成功但订阅或首日邮件缺失](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A243 | [周期礼包补发后进度保存失败，重试重复寄出邮件](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A364 | [登录先记跨日完成再更新任务，后续失败后同日无法补算](<health_audit_20261015_round166_task_login_broadcasts.md>) |
