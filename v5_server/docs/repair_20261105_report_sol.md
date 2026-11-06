# A91/A92 常规黑区星数与星奖修复（仅本地）

2026-11-05。A91、A92可更新为 `source_verified`。修复与验证仅在当前开发目录，未SSH/SCP、远端验证、重启、Git提交、封包或写真实账号库，未修改客户端Lua。原报告为 `health_audit_20261003_round68_mythic_normal.md`。

## 实现

- 新增 `mythic_normal_service.py`。`ops_common.mythic_normal_v1/state` 保存当前半周周期、信标独立代次、每难度精确星条件ID集合、通关分区和三档共享领史。周期沿现有东八区周一/周四05:00。难度1—13各自保留资格，星奖同周期跨难度共享，金额保留当前定制 `MYTHIC_NORMAL_STAR_REWARDS`。
- A91：`MythicClaimStarRewardOp` 验证合法档位1/2/3、当前有效难度、实际条件数量门槛和已领状态；只有条件2也能领取星奖1。资格查询失败中止。真实Inventory入账、精确状态和兼容领奖表、17023差量及44013必要ACK在同一Core事务内，任何失败一起回滚；不重发44009全量。Lua `MythicStarBack` 自行 `SetStarRewardState`。
- A92：删除44009固定满星及新档计数展开。周常和新档44009均委托同一payload，按当前主分区输出真实条件ID，例如 `[2,3]`；重打按集合并集，不把子分区通关算星。原分区表仍保留历史通关用于新档解锁，不能凭旧 `star=3` 推导三个真实条件。
- 原生常规主关3026001—3026013的配置为条件15（获得1个60001）、条件8（通关）和条件4（180秒内通关）；子关3027001—3027039无三星条件。专用评估不再使用未知条件默认成功，也不再以 `len([0,1,0])` 记三颗星。
- `dump.cs` 的 `BattleResultInfo` 明确采集数组为12/13，已有击杀计数为14/15。`battle_server.parse_record_counters` 增可选字段tuple、保留原14/15默认；实际UDP解析新增 `item_ids/item_numbers`，支持packed/unpacked重复int32，坏数据不生成采集星。
- Lua `BattleSettlementStrategyBase.GotoSettlement` 在失败/退出也接受条件15/16的已达成星。因此常规专属分支保留核实的采集星及54033星明细，失败不记通关，不全局修改其他模式失败结算。时间使用毫秒精确比较180000/180001边界。
- 开战绑定周期/难度/信标代次/真实关卡分区，结算前后重新校验。normal纳入54032保存点与54033预编码；资产差量失败传播，回滚后不缓存成功br。重复请求只回不可变54033回执，不重放资产。信标在AccountDB原保存点内重置normal独立代次；最终失序深区切队/重打不会清常规星。

## 证据与验证

16项专项真实内存 `AccountDB.new_save_workspace` + `Core` + `codec` 通过，覆盖空档/旧不明计数、非连续条件及并集、主子隔离、所有合法星档/非法档/重复、难度隔离与共享领史、冷恢复、原生UDP12/13 packed/unpacked、缺/坏/负采集计数、失败退出采集星、毫秒边界、真实17023与44013外部参数、标记/Inventory/ACK/17023故障回滚、结算ACK/资产故障重试、跨期/切难度/信标/缺身份及结算中跨期、资格查询失败、旧当前期领史防重、最终深区重置不误清常规星。

另运行关联回归：`verify_mythic_cycle.py` 30项、`verify_boss_reward_guard.py` 20项，共66项本地通过；相关Python编译通过。没有运行旧审计脚本中“复现旧bug才成功”的断言，没有扩展至全仓库验证。

- 专项命令：`python -W ignore::ResourceWarning v5_server/tools/verify_mythic_normal.py`
- 最终日志：`archive/report_repairs_20261105/normal_test_final.log`
- 回归日志：`normal_regression_cycle.log`、`normal_regression_boss_reward.log`（同目录）
- 初版夹具误用了不存在的进度表名、错误varint helper，以及未考虑codec省略空repeated字段；均已按现有源代码修正。迭代2失败日志与迭代3通过日志保留，未物理删除文件。
- 覆盖前备份：`archive/report_repairs_20261105/before/v5_server/`。

## 边界

旧档仅有整数star，且旧写入曾固定记3，无法可靠还原非连续条件。本轮保守保留当前周期有时间戳的通关/已领事实，不猜补星和资产；重打才建立精确星，旧领史不重发。此为明确迁移边界，不声称旧历史星已恢复。

未运行Lua VM、游戏客户端自然通关或重登、未发送真实UDP/TCP、未写生产数据，不能声称实机UI或部署已验收。仅验证原生数据契约的静态链和实际本地服务器内部/外部编码结果。看板及repair_tracker.json由主线程整合，避免并行写台账。

修改范围：新增normal服务、专项脚本与本报告；窄改operations.py、weekly_challenge_service.py、新档44009分支、middleware正常黑区分支、battle_server.py采集数组及account_db.py信标重置两行。middleware共享OR由主线程统一合并，刺桐84/85分支属于主线程，未覆盖。
