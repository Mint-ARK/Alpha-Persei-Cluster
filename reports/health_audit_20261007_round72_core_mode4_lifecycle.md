# 第72轮｜Mode 4逐队参数与结算重置

扫描日期：2026-10-07。问题编号：A96、A97、A98。

## A96｜Mode4 挑战关类型未识别，结算遗漏专属记录和刷新

### 出现什么现象

Mode4 的部分挑战关获胜后，没有专属成绩记录和状态刷新。

### 初步定位

结算分类遗漏实际配置中的关卡类型 93，不能进入 Mode4 专属记账与广播分支。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | _settle_frames 的挑战模式分类 |
| `BattleCoreVerificationCl4Cfg.lua` | 关卡类型 |
| `battleconst.lua` | Mode4 类型定义 |

## A97｜Mode4 单队重置只返回成功，持久进度没有变化

### 出现什么现象

Mode4 单队重置回包成功，重新加载后该队进度仍在。

### 初步定位

CoreVerificationMode4ResetTeamOp.apply 只返回 result=0，没有读取目标、写数据库或调用重置服务。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | CoreVerificationMode4ResetTeamOp |
| `coreverificationchallengemode4data.lua` | OnResetTeam |

## A98｜Mode4 结算缺少成绩刷新，客户端仍显示旧分数

### 出现什么现象

Mode4 战斗结束后，本次分数仍为旧值或零。

### 初步定位

客户端依赖 89811 更新 lastBattleScore，服务端结算没有发出该包；89801 的最高分刷新不能替代本次成绩。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | Mode4 结算广播 |
| `coreverificationchallengemode4action.lua` | 89811 |
| `coreverificationchallengemode4data.lua` | UpdateLastBattleScore |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A93 | [Mode4 领奖未检查完成状态，重复或未知任务也能发奖](<health_audit_20261007_round70_core_mode4_claim.md>) |
| A94 | [Mode4 恢复遗漏队伍和挑战关，第二队编号也会丢失](<health_audit_20261007_round71_core_mode4_progress.md>) |
| A95 | [Mode4 任务通关后仍不显示可领奖](<health_audit_20261007_round71_core_mode4_progress.md>) |
