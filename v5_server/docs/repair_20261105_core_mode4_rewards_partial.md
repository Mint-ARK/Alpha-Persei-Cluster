# A93/A95：Mode4领奖守卫与通关任务状态（partial）

2026-11-05，仅本地开发修复。A93、A95应更新为 **partial**，不能关闭或称Mode4整组完成。未SSH/SCP、远端测试、重启、Git、封包、真实账号库写入，未改客户端Lua。

## 当前权威复核

旧报告对应的漏洞仍存在于本次修改前源码：`CoreVerificationMode4GetRewardOp` 对未知/空任务走100水晶兜底，合法任务未校验资格或已领，先发奖再INSERT OR IGNORE；周常Mode4构造器仅为已领任务生成state2，没有未领达标state1。311的50301—06也缺于旧服务端奖励目录。

本轮重新读取当前Operation/AccountDB/周常/生成器、311差异 `CoreVerificationClRewardCfg.lua`、`ConditionCfg.lua`、`ActivityCfg.lua`、`CoreVerificationClMode4Cfg.lua`、`BattleCoreVerificationCl4Cfg.lua` 及基线Mode4 Data/Action/Tools。type93和898xx属于当前Mode4合同，不由旧报告编号推定。

重要身份修正：Lua `CoreVerificationChallengeTools.UpdateActivityCache` 将template433的主活动保存为activityId，Data经GetModeActivityId取得它，Action.GetReward原样发送。因此原生请求活动是4443301/4343301主活动，奖励/关卡目录活动为4443501/4343501。旧报告仅使用关卡子活动的样本不是完整原生入口合同。

## 局部变更

- 新增 `core_mode4_reward_service.py`，使用新导出的 `core_mode4_rewards_cfg.json`；导出工具 `tools/export_core_mode4_rewards.py` 附六个原生源文件hash。229已有定制奖励金额保留；311原缺失的当前第一轮六任务由实际Lua补齐。
- 只接受当前连接资源版本配置的第一轮Mode4主活动/关卡子活动，主/子请求统一为关卡活动记账。查询旧主活动和子活动领史取并集，堵住alias重复领奖，保留既有领史而不重发。
- 89804拒绝空列表、未知任务、请求内重复、错模式/错版本/错误活动、关闭活动、未达标及已领取任务。同批任何条目不合法则整批拒绝，不静默跳过或部分发奖。
- 通关类资格仅用当前活动 `core_verification_cl_progress` 的 `mode=4 AND is_cleared=1` 持久事实。311支持50301—04，229支持50209—12；批量奖励合并后真实Inventory入账，真实17023差量与89805 ACK在Core事务内准备。标记、Inventory、必要回包或差量故障均回滚。
- weekly的Mode4分支窄接同一资格函数，未达成state0、达成未领state1、已领state2，恢复与领奖同源。Lua OnGetRewardCallback自行把本次请求ID改state2，因此领奖不加89801全量。

修改范围仅新服务/JSON/导出工具/专项测试/本报告、operations.py的89804单类及weekly_challenge_service.py的Mode4任务列表两行。未动Astra正在修改的Rogue Operation区域、battle/middleware、AccountDB DDL/队模型、其他领奖类。

## 必须保留的未完成边界

1. **A94队模型缺失。** 311的50305/50306（229为50213/50214）是条件11307：指定挑战关通关且总分15000/22000。当前只有关卡单行最高score，没有原生五队及普通/挑战当前组合分，不能把单行best当全部总分。因此这些任务本轮 **拒绝领取、未领状态维持0**，即使手工fixture单行score极高也不放行。它们是原生合法任务，等待A94补真实队伍成绩后再恢复，不能称全部六任务已支持。
2. 通关类验证使用实际AccountDB方法构造已存在的持久事实；当前type93挑战3093121仍受A96等入口/分类/逐队缺口影响，本轮没有宣称已自然游玩完成四关或五队。
3. A93在Mode1—3的共享问题没有随本次Mode4单类修改一并解决；A95其他模式的state1也未修。A94/A96/A97/A99/A107/A108保留原状态和待办，不冒充被本次奖励守卫覆盖。
4. 未尝试追回旧错误奖励或推测历史队分；旧领取记录保留。未运行Lua VM、游戏客户端或生产服务，无实机UI/自然通关结论。

## 验证

`python -W ignore::ResourceWarning v5_server/tools/verify_core_mode4_rewards.py`：14项真实内存AccountDB/Core/Inventory/codec检查通过；相关Python编译通过。

覆盖：两版本Lua导出一致及旧定制金额保留；空/未知/请求重复/未达标/错活动/错版本/关闭活动；四通关任务批量合并仅发一次；root/child及旧root领史防重；其他mode/活动成绩隔离；积分任务缺队模型不放行；混合已领批次原子拒绝；17023外部余额和89805奖励一致；台账/Inventory/ACK/差量故障回滚后可重试；查询失败不伪造未领；89801冷生成state1/state2；实际 `login.send_login_flood` TCP帧序列化与解压codec恢复state1/state2，均在10201前，且不写资产。其他登录特性已隔离，只把本模块真实数据源与编码链作为证据。

证据：`archive/core_mode4_rewards_20261105/test_iteration1.log`（13项），`test_final.log`（增加实际登录洪流后14项）；覆盖前备份在同目录 `before/operations.py`、`before/weekly_challenge_service.py`。没有运行旧审计中“必须复现漏洞才通过”的反向断言，没有全仓库重复验证。

共享repair_tracker.json/看板由根线程统一整合；本报告不并写台账。
