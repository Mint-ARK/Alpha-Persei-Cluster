# 虚构推演：战术指令激活与六项获取成就（2026-11-06）

仅本地开发；132项隔离检查通过，未经游戏实测。无真实存档写入、服务操作、云同步、Git提交或封包。由Codex继续实现，不再调用Antigravity。

## 根因和结果

原生战术指令是`ITEM_TYPE.SUIT_SKILL=5`，不是`MECHANISM=2`的四种机制技巧。原服务端虽有六种1500xxx道具定义，却没有按流派程序数激活、保存当前战术、下发88211或累计300009的链路。

本轮按原生`SUIT_SKILL_NEED_TREASURE_CNT=6`及每个程序的`camp`配置，在真实操作取得第6个不同的同流派程序时激活战术。多流派程序为其每个原生camp各贡献1；程序升阶、重复ID不算额外数量。

用户明确批准的本服规则：本局首次满足的流派固定至新局；同次满足多系时按原生战术配置顺序选择。并非宣称已复原官方多系切换算法。新局清局内战术，累计获取记录保留。

六种指令依原生camp映射：

| camp | 道具 | 成就 | 累计需求 |
| --- | --- | --- | --- |
| 1 | 1500101 连锁过载 | 63011 | 3次 |
| 2 | 1500201 风载荷 | 63023 | 3次 |
| 3 | 1500301 动能镀层 | 63012 | 3次 |
| 4 | 1500401 乱刃风暴 | 63013 | 3次 |
| 5 | 1500501 湮灭时刻 | 63014 | 3次 |
| 6 | 1500601 凌冰风暴 | 63015 | 3次 |

## 持久化和输出

- `rogueteam_tactics.py`外层包装已有8个服务方法，放在Astra的房间效果包装之后，覆盖战后奖励选择、事件、商店和周期赠品等实际程序入库路径。只有同run、实际程序集合增加才检查激活；登录/继续、旧包扫描和升阶不生成历史获取事件。
- `flow_json.tactic_v1`保存本局camp、道具和能量；`ops_common(kind=rogueteam_tactic_acquisitions_v1,item_id=100001)`按数据库/UID保存六种累计次数。无DDL、无跨库全局缓存。
- 新战术道具进局内other_item_list，并用必要88205播放原生获取流程；88211的id发camp（1—6），不是1500xxx道具ID，value发已保存能量。没有把战术错记为旧图鉴helper的奇遇类别。
- `rogueteam_achievement.py`新增300009的1/3、2/3部分进度和3/3完成时间投影，使用53003差量；不直接发奖。重复请求/本局额外程序不再计获取。新局获得同类才再加1。
- 在线继续88030在88031前恢复88211；冷登录动态替换旧抓包88211，在10201之前恢复。无当前战术时发0/0，防残留上个存档/对局的状态。仅恢复，不重复播放获取动画或增加次数。
- 88205、88211、53003和操作ACK均为事务内必要帧；失败回滚程序、战术、累计次数与成就进度。旧成就领奖记录不变，玩家仍按53004手动领取。

## 规则和提取证据

工具`v5_server/tools/export_rogueteam_tactics_contract.py`从只读Lua导出`rogueteam_tactics_contract.json`，包含105个程序的camp、6种指令及6项300009成就，逐项行号及6份源文件SHA256。已有合同拒绝覆盖；专项重跑export()逐字段比较，同时比对实际achievement_cfg的parameter/need。

```powershell
# 仅第一次生成；已有文件用专项复核，不覆盖
python -B -X utf8 v5_server/tools/export_rogueteam_tactics_contract.py
$env:ACCOUNT_DB_PATH=':memory:'
python -B -X utf8 v5_server/tools/verify_rogueteam_tactics.py
python -B -X utf8 archive/rogueteam_tactics_20261106/run_acceptance.py
```

关键来源：

- `game/const/challengerogueteamconst.lua:4`的6程序门槛，ITEM_TYPE与ITEM_SOURCE_TYPE。
- `game/config/guidestepcfg.lua:9734`说明同种战术6个程序激活指令。
- `game/config/rogueteam/rogueteamitemcfg.lua:3830`起六种type5/camp与全部程序camp。
- `game/tools/challengerogueteamtools.lua`的GetTreasureCmapMaxCnt、GetItemIDByCampID、HasSuitSkillPop、GetTreasureAffix；客户端本身按不同程序ID统计，并按camp查战术。
- `game/action/challengerogueteamaction.lua:124`绑定88211，SuitSkillSetValue保存camp/energy；`game/data/rogueteam/challengerogueteamsuitskilldata.lua`初始能量0。

## 验证与保留项

132项一次组合验收通过：11项本轮专项、上一轮12项通关成就专项、109项状态/Issue8/战斗/登录/隐藏岛/道具效果/多维隔离/成就领奖关联回归。单独9项首版已通过，不重复计数。

专项覆盖六系参数映射、5→6阈值、重复/升阶不计数、单局锁定、同时满足按配置选、跨三局完成及正常领奖、必要帧与ACK失败整体回滚、真实序列化冷登录及其必要88211失败、登录按累计凭证恢复部分进度。全部为内存AccountDB、真实Core/codec与Inventory；无游戏或Lua VM实测。

证据`archive/rogueteam_tactics_20261106/`。首轮旧登录夹具8个精确序列断言未包含新增88211，按本次明确的消费者合同更新并断言0/0；组合首版另有1个故障夹具误预期ValueError，实际必要帧抛OperationError，纠正后通过。没有通过放宽业务断言掩盖运行故障。

新战术初始能量沿客户端默认0，没有发明战后充能/消耗公式。战斗模板会读取该状态并挂对应战术词缀；完整C#战斗充能、消耗和结果回传仍需实机/协议证据，未声称这部分已复原。

300012“单次战术指令击杀5敌”仍待证据。现有UDP解析仅有总击杀与通用record_data等计数，尚未找到可以证明一次施放击杀量的原生记录映射，不能拿整场击杀数或持有战术代替。当前审查的17项成就已接16项，剩此1项。HP/其它效果/引导101仍沿既有后续清单。
