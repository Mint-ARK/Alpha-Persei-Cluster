# A55：自选箱选择数量校验

来源：`health_audit_20260921_round29_reward_rules.md` 的A55，以及当前 `InventoryService._plan_item_use`、`UseItemOp` 和客户端Lua。

## 修复

普通自选箱原先逐个遍历 `use_list` 并分别乘以 `item_info.num`，一箱提交 `[1,1]` 会多发一份奖励，提交 `[1,2]` 也能得到两种奖励。六类明确走普通自选界面的箱子（504、508、514、515、516、517）现在在使用计划阶段拒绝多于一个选择，返回既有错误码2；整批请求尚未扣费、发奖或执行其他效果。

不去重后静默接受。合法单选与批量使用仍按配置数量乘 `item_info.num`；同一批中不同条目可以各选一项。保留原有未填选择时默认第一项的兼容逻辑，不修改奖励配置。

Lua依据：

- `decompiled_v2/x64/game/views/bag/optionalitemselectview.lua` 发送单个 `trueIndex_`；使用数量单独放在 `item_info.num`，`RefreshItemNum` 按 `useNum_` 乘配置数量。
- `decompiled_v2/x64/game/views/item/itemuse/itemuseview.lua` 中508、516的MULT/MULTI指批量开箱，仍进入同一个单选界面，并非允许一个箱子选多个奖励。
- `decompiled_v2/x64/game/action/fukubukuroaction.lua` 的513福袋发送实例ID＋选择索引，是独立参数语义。本票只约束上述六类，不把其他参数列表强改为单选，也不认证福袋实例流程。

当前配置共67个上述类型的箱子，66个有可选奖励；30206“2026时迹回赠修正者自选券”原生配置 `param=[]`，继续明确拒绝，不编造奖励。

## 差量与验证

复用A53/A54的整批计划、保存点和必要响应事务。拒绝只回非零17013，资产/通知不变；成功沿用17023绝对资产差量，在17013之前下发。未增加全量刷新。客户端CommonAction与OptionalItemSelectView只按成功回执展示奖励，没有自行重复扣减库存的新增逻辑。

`tools/verify_item_choice_count.py` 最终24项检查通过：6项新增＋10项原子性/概率回归＋8项使用计划回归；真实内存AccountDB/Core/Inventory/codec，不修改真实存档。覆盖重复/多项选择、整包预检扣费0次、直接服务错误回滚、3箱单选奖励与绝对差量，以及原来的不同条目独立选择和失败重试。

修前方法提取对比：6组中4组失败，共71个失败子项；其中配置遍历为多项子用例，不是71个独立BUG。初版夹具误将30206空占位配置当作可正常开箱，出现1个错误，调整后输出全部保留在 `archive/item_choice_count_20261103`。

## 云端

只同步 `inventory_service.py`，覆盖前副本 `/game-server/aethergazer/archive/item_choice_count_20261103_201354_787211`，新SHA256为 `e2b55e3e69352d9a1490d3cefad21fc176b556a94aaf7c1a70a6a259b6c293e5`。

云端独立内存Core探针通过：非法请求扣费0次，合法2箱单选/另选一箱准确，实际出站17023→17013。服务PID1827974未变，源码待下次重启加载。未提交Git、未封包，未经游戏实机测试。
