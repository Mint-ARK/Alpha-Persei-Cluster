# 第21轮｜任务库存广播与战令

扫描日期：2026-09-21。问题编号：A47、A48。

## A47｜一次活跃度奖励触发两次相同事件

### 出现什么现象

一次活跃奖励入账时，同一事件被发送两次。

### 初步定位

库存发放已经排入 ACTIVITY_POINT_GAIN，任务领取操作在发奖后又显式发送同一事件，两个生产入口重叠。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `inventory_service.py` | grant_items |
| `operations.py` | TaskSubmitOp、TaskOneKeySubmitOp |

## A48｜战令经验只到账一次，周累计经验却增加两次

### 出现什么现象

战令经验只增加一份，周累计经验却增加两份。

### 初步定位

库存发放经验道具时已更新 weekly_gain_exp，任务操作在发奖后又执行同一更新。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `inventory_service.py` | 经验道具发放 |
| `operations.py` | 任务领取后的 weekly_gain_exp 更新 |
