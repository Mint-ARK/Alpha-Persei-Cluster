# 虚构推演：战后能量继承与单次指令击杀（2026-11-06）

状态：仅本地源码完成，143项隔离检查通过，未经游戏实测。无真实存档、服务、云端、Git或封包操作；Codex独立完成。

## 关键定位

现有UDP并非完全缺少这些信息，而是通用`record_data_ids/record_data_numbers`尚未映射到本玩法。

复用Astra旧工具`export_enemy_sweep_config.native_tables`，从已归档的原生`archive/enemy_illustration_20261023/native_config.bytes`提取`battle_record_data_type`：

| 原生ID | type_id | 原生名称 | 本轮用途 |
| --- | --- | --- | --- |
| 80 | 80 | 新多维大招充能继承用 | 真实剩余能量的绝对值；明确0表示耗尽，缺失则保留已知值 |
| 88 | 88 | 1次流派大招击杀数 | 一次施放的击杀记录；达到5才证明63016 |

同表82—87是各流派效果击杀数，不能代替88；整场总击杀亦不能代替。跨场88=3、88=3取最大3，不累加成6。

项目根`dump.cs:584374`的`BattleResultInfo`确认字段14/15是两列`RepeatedField<int>`，与当前UDP解析器的packed/unpacked处理一致。该类还存在17成就列表、18选择效果，但本轮**不使用笼统成就列表直接点亮成就**，只用已核准的80/88专用记录。

## 实现

- 新`rogueteam_battle_facts.py`读取原生合同，验证记录平行数组、重复ID、非负int32数值，并处理80/88。
- 入场保存当前camp和随机尝试token；发布战斗时将`bid/token/node`保存在本局flow。middleware仅新增这些会话字段和Rogue专用发布调用，不改其它玩法规则。
- 结算先沿既有uid/run/floor/map/node/stage门禁，再校对入场camp和本次bid/token。重开同节点后的旧战报不能覆盖新能量或推进成就。
- 80是绝对值：直接更新本局`tactic_v1.energy`并发必要88211；无80不自增、不归零、不猜充能公式。相同值不重复下发。88030/冷登录继续沿前轮状态恢复该值。
- 88的历史单次最大值保存到`ops_common(kind=rogueteam_battle_facts_v1,item_id=100001)`；达到5后用53003推进63016（300012、parameter=[0,0,5]）。已领保持，奖励仍通过53004手动领取，不自动发奖。
- 原生成就描述没有要求通关，因此本局已实际拥有战术时，失败/主动退出的合法战报也可证明一次施放。只记录真实剩余能量和击杀事实，不把失败当胜利、不推进地图或通关成就。
- 首次战报的数据库变化、88211/53003及54033准备在同一既有结算事务内；必要编码失败全部回滚，不缓存假成功。重复缓存回执不再消费修改后的报告。
- 没有入场战术、错误camp、旧尝试或无真实88记录，均不能凭总量解锁成就。旧档没有击杀记录不猜补；新持久事实可在登录时修复漏掉的成就投影。

## 可复现提取

工具`v5_server/tools/export_rogueteam_battle_facts.py`生成`rogueteam_battle_facts_contract.json`：保存80/88完整原生名称、wire14/15、63016条件，以及原生配置、dump.cs、Lua成就/战斗模板/88211消费者的SHA256。运行时仅需小型JSON合同，不读客户端或13MB原生数据。

```powershell
# 首次导出；已有合同不覆盖，专项会重提取逐字段比较
python -B -X utf8 v5_server/tools/export_rogueteam_battle_facts.py
$env:ACCOUNT_DB_PATH=':memory:'
python -B -X utf8 v5_server/tools/verify_rogueteam_battle_facts.py
python -B -X utf8 archive/rogueteam_battle_report_20261106/run_acceptance.py
```

最初检查备份客户端metadata发现是非标准加密头，没有修改/解密客户端；后改用项目原有dump.cs与已导出的原生配置。探索脚本和输出在同一archive目录保留。

## 验证结果和边界

11项新专项通过：原生记录/wire/成就合同、真实UDP→54032→持久能量与53003、packed/unpacked、能量0/缺失、总击杀与多场不混算、失败/退出不推进地图、坏计数拒绝、旧bid/camp拒绝、88211/53003/54033失败回滚、缓存回执不重放、继续恢复/登录修复与手动领奖、无战术不解锁。

再与前轮132项组合，共143项通过；均为内存AccountDB、真实Core/codec/Inventory与原生UDP解析，未运行Unity或Lua VM。日志`acceptance_passed.log`，改前副本`before/`，证据目录`archive/rogueteam_battle_report_20261106/`。

首版专项的packed夹具误写了不存在的`udp.vi`，改为真实`_varint`后通过；保留首版日志，没有改业务规则迁就测试。

额外执行20项旧AST入口/重试夹具时出现2失败、8错误；已用本轮修改前middleware源码替换**测试读取内容**复现相同10个测试问题，未还原磁盘源码。旧夹具假设register_battle_session仍是顶层语句，且Rogue/Polyhedron旧入场桩已过时；该额外旧套件不计入143项通过。`legacy_baseline.log`、`acceptance_first.log`和`verify_legacy_baseline.py`保存证据，没有重写这批不相关夹具。

本轮只继承客户端真实能量，没有另写战斗内充能算法；战斗内执行仍由原生客户端完成。当前审查的17项Rogue成就全部接通，不等于整个玩法的HP、全部道具/科技效果或引导101已全面完成。
