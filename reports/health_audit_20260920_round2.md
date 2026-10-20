# 第2轮｜邮件领取、消费任务与跨日刷新

扫描日期：2026-09-20。问题编号：A05、A06、A07、A08。

## A05｜同一封邮件可重复领取附件

### 出现什么现象

连续领取同一封邮件，两次都得到附件。

### 初步定位

单领只按账号和邮件编号查找，没有再次检查 attach_flag 是否未领取；一键领取路径有未领过滤，单领路径没有使用相同判据。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `mail_service.py` | claim_mail_attachments |
| `operations.py` | MailReceiveOp |

## A06｜邮件发奖失败仍标记已领取，并返回成功

### 出现什么现象

邮件显示领取成功并变为已领，但附件未到账。

### 初步定位

领取函数先写已领和已读状态，再发附件。发奖异常只记日志，没有交给外层事务处理；回复又固定返回成功和原附件列表。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `mail_service.py` | claim_mail_attachments |
| `operations.py` | MailReceiveOp.respond |
| `mailaction.lua` | OnGetMailAttachment |

## A07｜统一扣费未触发消费事件，相关任务不增长

### 出现什么现象

角色突破等操作扣除了金币，但对应消费任务没有增加进度。

### 初步定位

统一扣费成功后直接返回，GOLD_COST、STAMINA_COST 只在旧备用路径中发出；新资产服务只修改库存，没有补发任务所依赖的消费事件。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | _item_deduct |
| `inventory_service.py` | InventoryService.cost_item |
| `hero_service.py` | _deduct_item、break_hero |
| `task_listener.py` | on_gold_cost、on_stamina_cost |

## A08｜跨日处理失败仍记为已刷新，当天无法重试

### 出现什么现象

跨日或跨周处理失败后，系统仍记为本周期已完成，下一次检查不再重试。

### 初步定位

EventBus.emit 捕获监听器异常后继续返回；LazyTimer 随后照常推进日、周、月水位，无法区分业务处理成功与失败。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `event_bus.py` | EventBus.emit |
| `lazy_timer.py` | LazyTimerBroadcaster.pulse |
| `timer_listeners.py` | 日切和周切监听 |
