# A53/A56：道具使用整批原子性与随机未命中

依据health_audit_20260921_round28_item_use.md、round29_reward_rules.md，当前UseItemOp/InventoryService、真实item_usable_cfg/drop_cfg，以及CommonAction、OptionalItemSelectView、FukubukuroAction消费代码。

## 修复

- A53：余额预检按道具ID汇总整包总消耗，仍逐条保留use_list和count，不能把不同选择合成一个方案。库存1瓶、两条各用1瓶在任何cost_item调用前返回不足，不会先扣一瓶。
- use_items公共服务新增保存点与通知检查点，执行阶段任一返回非零或抛异常时回滚本次消耗、发奖和通知；直接调用仍保留result/drop_list等结构化错误API。Core适配器遇到非零结果抛OperationError，不能以正常返回结束事务。空请求保留无操作成功。
- UseItemOp必要响应准备纳入Core事务；17013或必需库存差量生成失败也回滚，可重新操作。正常错误不发送假成功或旧资产差量。
- 生日及永久剧情放映道具依旧只查拥有、不消耗；重复条目按最大请求数校验拥有，不能当成会消耗的普通药瓶求和。这不改变现有观看协议。
- A56：三字段随机项只在概率命中时发放；未命中不再进入“两字段兼容”分支。两字段项依旧保证发放，0/100及20命中、21未命中的边界正确。每次使用分别抽取，base、weight及配置数值未修改。

未修改A54缺配置/越界选择仍消耗、A55普通箱多选资格、A57—A60折算/过期或状态型效果问题，也未修改其他战斗/抽卡概率引擎。正常概率未命中允许消耗后空掉落，不能把它当作使用失败。

## 差量与Lua顺序

保留原有17023绝对库存/货币差量，体力变化时保留17025，均在17013之前；不新增17009等全量刷新。真实30031、随机值固定100时只获得池100010的必得40×50，云端Core帧为[17023,17013]。

已读CommonAction的17013回调触发ON_TRY_TO_USE_ITEM/USE_ITEM_SUCCESS；普通自选与福袋成功回调展示drop_list并刷新/通知，没有按原use_item_list再次扣库存。因此继续使用服务器绝对差量先更新余额，再交回调展示奖励。保留现有角色/场景刷新路径，不以本票宣称其所有异常分支或A63均已修复。

## 验证

tools/verify_item_use_atomic_random.py：真实Core/Inventory/codec、内存SQLite与受控随机，共10项通过。覆盖重复条目总量不足且cost_item未执行、合法重复药瓶及不同自选项、后项扣费失败的Core与直接服务回滚、发奖实际写入后故障回滚、17013/必要17023失败重试、真实30031固定100仅40×50、0/100/20边界与两字段兼容、多次使用的base/weight、合法随机空掉落、永久放映道具重复条目不消费。

8项芯片解锁回归通过，共18项。仅替换备份旧use_items方法、其余Core/UseItemOp保持当前时10项出现7个失败；这是旧服务行为对照，不冒称完整旧操作框架的复现。随机来源为确定性边界，不用大样本跑分代替概率分支验证。没有改真实存档或做用户资产补偿。

## 同步与记录

inventory_service.py、operations.py已同步43.139.154.74:/game-server/aethergazer，写前哈希与本地前件一致，副本archive/item_use_atomic_20261103_164611_a94f55。独立新进程加载磁盘源码，内存库验证重复预检、直接失败回滚、ACK失败回滚、真实随机包产出及差量顺序，通过。

PID1693172未变，待统一重启加载，未经实机。不写真实存档，不Git/封包。根archive/item_use_atomic_random_20261103保存前件、修前后/回归日志、events.json、含预期“不足”提示的remote_probe_output.txt及提取的remote_result.json。小本本和A53/A56台账更新；386条中110 source_verified、3 partial、273 historical，为源码/脚本状态统计。
