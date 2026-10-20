# 第181轮｜迭代校验：挑战词缀丢失与成绩显示缺项

扫描日期：2026-10-17。问题编号：A380、A381。

本轮检查了 Mode1—3 的词缀选择、保存、通关和状态恢复，定位到两个问题：保存与读取词缀的位置不一致，以及 Mode3 的成绩回复缺少客户端需要的字段。

## A380｜首次保存或通关后，已选挑战词缀丢失

### 出现什么现象

Mode1、Mode2 选择词缀并保存后，重新加载状态时读不到选项；已有选项也会在通关后变成空列表。

### 初步定位

问题分为两处：

1. **首次保存与读取位置不同。** 没有通关记录时，保存函数把词缀写到 `stage_id=0` 的占位记录；恢复状态时却只读取挑战主关的记录，因此没有取到刚保存的选择。
2. **通关写入覆盖原选项。** 结算调用没有传入 `select_buffs`，记录函数将它转换为空数组，再覆盖原关卡记录，导致已保存的词缀被清空。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | `CoreVerificationChallengeSetAffixOp`：Mode1 保存，`89014 → 89015` |
| `operations.py` | `CoreVerificationMode2SetAffixOp`：Mode2 保存，`89026 → 89027` |
| `account_db.py` | `save_core_verification_cl_buffs`：首次保存到占位记录 |
| `account_db.py` | `record_core_verification_cl_stage_clear`：通关时将未传入的选项写为空数组 |
| `weekly_challenge_service.py` | 读取挑战主关的 `select_buffs`，组装 `challenge_buff` |
| `middleware.py` | 通关结算调用记录函数时，未传入本局词缀选择 |

## A381｜成绩已有记录，Mode3 回复仍缺总分和用时

### 出现什么现象

Mode3 的分数和用时已经保存，但回复包没有提供当前总分、最高总分和最短用时，客户端无法从该回复中取得这些显示数据。

### 初步定位

`weekly_challenge_service.py` 的 Mode3 分支只组装了主关编号、挑战分值等内容，遗漏了客户端直接读取的三个字段：

| 缺少的字段 | 用途 |
| --- | --- |
| `sc_89431.cur_point` | 当前总分 |
| `sc_89431.max_point` | 最高总分 |
| `sc_89431.challenge_stage.min_seconds` | 最短用时 |

该分支还写入了协议没有定义的 `challenge_buff`，编码时会被丢弃。使用正确活动编号后，字段缺失仍然存在，因此它与 A374 的活动编号错误是两个问题。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `weekly_challenge_service.py` | `mode_num == 3` 的回复构造分支 |
| `account_db.py` | `core_verification_cl_progress` 保存的分数与用时记录 |
| `coreverificationchallengemode3data.lua` | 读取当前总分、最高总分和挑战用时 |
| `challengestageviewmode3.lua` | 使用 `min_seconds` 显示时间 |
| `p89_pb.lua` | `sc_89431` 的字段定义 |

## 同轮补充发现

| 编号 | 问题 |
| --- | --- |
| A93 | Mode1—3 的领奖实现都先发奖、后记录领史；重复请求会再次触发发奖。 |
| A94 | 已保存普通关出战角色，但恢复包中的锁定角色列表仍为空。 |
| A95 | 任务列表只生成“已领取”状态，没有生成“已达标、可领取”状态。 |

关联编号：A99、A107（旧战斗与活动归属）；A374（Mode3 活动编号）。
