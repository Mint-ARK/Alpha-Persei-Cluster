# 虚构推演：十项通关成就生产链（2026-11-06）

状态：源码修复及隔离脚本通过，未经游戏实测。仅本地开发；不写真存档、不重启服务、不云同步、不提交Git或封包。

## 修复结果

昨天移除了多维变量对虚构推演成就的错误推进来源，但真正的Rogue生产链没有接上。本轮通过真实88306结算与登录投影补齐10项：

| 原生成就 | 条件 | 事实来源 |
| --- | --- | --- |
| 63001—63004 | 300005：评级Ⅰ—Ⅳ胜利 | 当前账号、模板100001的diff_clear_json；评级严格匹配，不由解锁难度推算 |
| 63006/63007/63008/63022 | 300006：结局1/2/3/6胜利 | 同模板ending_pass_json |
| 63009 | 300013：通关时持有10个收集品 | 本次首次成功胜利结算前的真实背包，原生RELIC=3 |
| 63010 | 300014：通关时持有20个任意外接程序 | 同上，原生TREASURE=4；parameter第二项0代表不限子类 |

失败/放弃不会创造胜利事实；正常未达数量条件只跳过该成就，不阻断结算。程序等级、疑似num字段、重复ID、未知ID和错误背包类别均不当作额外持有数量。RELIC按原生大类统计，含正常/负面/剧情子类，没有自创按前缀/名称过滤。

首次结算在既有run/report门禁后，保存新胜利的持有数量上界到`ops_common(kind=rogueteam_achievement_v1,item_id=100001)`。每个阈值均是独立单项数量条件，独立最大值足以证明对应成就；不把不同局的道具数量相加。新局不删除该证据。

已领奖标志、已有更高进度及有效完成时间保留。只推进成就，不直接发奖；仍走53004正常手动领取。旧档仅根据自己的评级/结局胜利历史补漏；旧历史没有终局持有量时，不从当前背包、解锁难度或多维表猜补。

## 源码与协议

- 新`rogueteam_achievement.py`：当前数据库/UID内的胜利证据、分类计数、成就投影；无全局用户缓存、无DDL。
- `rogueteam_flow.install_operations/settle_apply`：取结算前状态，正常结算后记录有效胜利并生成53003；重复结算不二次记录或发差量。
- `settle_respond`：资产差量后发必要53003，之后原88307、88305，不新增在线53001。
- `AchievementService.reconcile_achievements`：仅新增Rogue专用权威投影，登录仍使用原有53001；带ctx的在线投影将生成的53003正常入通知队列。
- 成就进度、持有数量凭证、通关历史、积分/资产和响应均受既有88306事务约束；53003空值/None/异常或88307必要帧失败会回滚。没有复用吞空编码的通用trigger_condition。

## 数据提取与复跑

提取器：`v5_server/tools/export_rogueteam_achievement_contract.py`，沿用Astra导出脚本的原生Lua顶层块解析方式。生成`rogueteam_achievement_contract.json`，10项成就、169项RELIC/TREASURE配置，保留每项源行号与三份原始文件SHA256。提取器拒绝覆盖已有合同。

首次生成：

```powershell
python -B -X utf8 v5_server/tools/export_rogueteam_achievement_contract.py
```

已有文件用专项检查中的`export()`重新提取并逐字段比较，不覆盖合同。关键来源：

- `decompiled_v2/x64/game/config/achievementcfg.lua:11602`起；例63001 `[100001,1]`、63009 `[100001,10]`、63010 `[100001,0,20]`。
- `decompiled_v2/x64/game/const/challengerogueteamconst.lua`的`ITEM_TYPE`（RELIC=3、TREASURE=4）及`RELIC_TYPE`。
- `decompiled_v2/x64/game/config/rogueteam/rogueteamitemcfg.lua`逐项type/sub_type，不按ID前缀猜分类。
- `decompiled_v2/x64/game/action/achievementaction.lua`开头Bind53003，`AchievementData:UpdateAchievementList(..., false)`消费增量。

SHA256见合同source字段；专项同时逐项比对当前`achievement_cfg.json`的id/condition/parameter/need，保证导出与运行时成就元数据一致。

## 验证

```powershell
$env:ACCOUNT_DB_PATH=':memory:'
python -B -X utf8 v5_server/tools/verify_rogueteam_achievements.py
```

12项通过：真实54030/54032终局与88306、重复结算无增量、失败、10/20数量及9/19不足边界、原生合同重提取、正常/负面/剧情分类及无效/重复ID、登录历史与解锁等级隔离、手动领奖/防重、跨库同UID及同库不同UID、53003及88307失败回滚、持有证据跨新局恢复。使用真实AccountDB内存库/Core/Inventory/codec；终局路线复用Astra的RogueBattleTests，不是伪造apply返回值。

另105项既有回归通过：原状态/Issue8/战斗/登录/隐藏岛75项、道具效果21项、多维成就隔离3项、既有成就防重复领取6项。合计117项；初期4项验收是专项的子集，不重复累加。原有codec字段警告及skeleton文件句柄ResourceWarning保留，没有为消警告扩改协议或夹具。

日志与改前副本：`archive/rogue_bridge_20261106/`，主线程改前`root_before/`。CLI全阶段超时、纯代码请求失败，未留下业务改动；本轮业务实现与验收均由Codex接手，没有把CLI的成功状态当作交付证据。

## 剩余边界

300009六类战术获取、300012一次战术指令击杀5敌仍未补；不凭总击杀伪造战术击杀。完整HP、全部道具/科技效果、引导101及既有无nonce边界仍沿昨天记录。整套虚构推演不因这10项完成就算全部重构完成。
