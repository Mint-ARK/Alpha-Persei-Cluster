# 第9轮｜多倍战斗与扫荡的累计进度

扫描日期：2026-09-20。问题编号：A28。

## A28｜多倍战斗和扫荡的累计任务进度未按次数增长

### 出现什么现象

三倍战斗的累计通关只加一次，三倍扫荡甚至不增加累计次数。

### 初步定位

奖励和体力计算使用了 times，但推进关卡时没有传递倍数，_advance_stage 默认只加一次；扫荡又被条件分支直接跳过。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | _settle_frames、_advance_stage |
| `stage_service.py` | pass_stage、get_resource_downframe_list |
| `generator.py` | sc_25009 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A14 | 一次胜利触发两次通关事件，任务可能重复计数 |
