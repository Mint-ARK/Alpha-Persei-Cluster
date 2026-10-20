# 第173轮｜关卡双表权威与黑区/介质恢复降级

扫描日期：2026-10-17。问题编号：A372、A373。

## A372｜主线通关记录被旧支线记录覆盖，星级和完成状态回退

### 出现什么现象

主线实际通关和星级已保存，完整关卡恢复却被旧支线行覆盖。

### 初步定位

写入按元数据选择chapter或stage_sub，恢复却无条件先合chapter再用全部stage_sub覆盖，没有按关卡类别确定权威表。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `stage_service.py` | get_plot_downframe_list、pass_stage |
| `generator.py` | 24009 |

## A373｜周常数据读取失败被当作空状态，成绩、英雄和徽章显示丢失

### 出现什么现象

周常数据查询失败时，回复看似正常，却把进度、已领和难度显示成默认值。

### 初步定位

多个恢复分支分别捕获查询错误并保留默认字典，上层继续编码成功形状；数据库原值仍在，客户端无法区分新状态与读取失败。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `weekly_challenge_service.py` | 梦境、黑区、失序与介质恢复 |
| `generator.py` | 周常协议生成 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A80 | [梦境一键领奖查询失败后，仍按未领取发奖](<health_audit_20260928_round55_weekly_claim_interop.md>) |
| A87 | [梦境周刷新访问不存在的旧表，后续清理中断](<health_audit_20261003_round62_weekly_refresh_paths.md>) |
| A92 | [黑区星级列表固定满星，与实际成绩不符](<health_audit_20261003_round68_mythic_normal.md>) |
| A93 | [Mode4 领奖未检查完成状态，重复或未知任务也能发奖](<health_audit_20261007_round70_core_mode4_claim.md>) |
| A108 | [Mode4 重置后的即时最高分与重登结果不同](<health_audit_20261007_round79_core_score_handoff.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A341 | [GM 关卡与剧情修改分开提交，后段失败留下半份存档](<health_audit_20261015_round158_gm_stage_preset.md>) |
