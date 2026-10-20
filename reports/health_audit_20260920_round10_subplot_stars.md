# 第10轮｜支线首通奖励与收集类三星条件

扫描日期：2026-09-20。问题编号：A29、A30。

## A29｜支线首通记录读取错误，首通奖励可能作为普通掉落重发

### 出现什么现象

已通关支线再次战斗时，仍可能收到首通奖励。

### 初步定位

支线进度写入 stage_sub，首通判断却只读 chapter；掉落为空时还会用 stage_sub.first_reward 作为普通掉落，再作为首通额外奖励拼入。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | _stage_progress、_settle_frames |
| `stage_service.py` | get_stage_info、pass_stage |

## A30｜收集类三星条件未检查，胜利即视为达成

### 出现什么现象

需要收集物品的三星条件，在未核对收集数量时也按胜利达成。

### 初步定位

evaluate_three_star 没有处理收集类条件，默认用胜利结果替代。战报解析也未向判定器传递收集物品数据，条件判断和输入两端都有缺口。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | evaluate_three_star |
| `battle_server.py` | parse_battle_result |
| `battleinstance.lua` | CaculateStar |
| `threestarconditioncfg.lua` | 收集类星级条件 |
