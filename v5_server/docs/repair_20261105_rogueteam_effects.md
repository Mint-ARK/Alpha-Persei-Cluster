# 2026-11-05 RogueTeam 五件收集品效果状态机

本线在保留 Issue8、旧状态机守卫及隐藏地图实现的工作源码上，完成五件收集品、七条原生效果的有界修补。内部状态与实际 Core/codec 输出检查通过：21 项专项、原有 75 项回归，共 96 项。没有执行客户端 Lua/Unity，没有实机验收，不能把本报告解释为所有 RogueTeam 效果均已复原。

本轮仅本地开发源码及隔离测试；未上传、同步云端、重启、Git 操作、封包、写真实账号存档或修改 Lua。原版备份在 `archive/rogueteam_effects_20261105/before/`；Astra ultra 接手点六文件备份在其 `astra_ultra_before/`，复制后已校验 SHA256。未物理删除文件。根记忆与协作看板由主线程统一更新。

## 原生证据与实现边界

| 道具 ID | 原生 effect / moment / params | 本轮行为 |
|---|---|---|
| 1310002 招财树盆栽 | 13100021 / ENTERROOM=20 / `[1,6,0,15]` | 每个实际节点首次进入 +15，包括商店；持有后在商店真实支出才失效；免费购买不失效，先花钱后买树不被追溯失效 |
| 1310003 货币战争 | 13100031 / ENTERSHOPROOM=21 / `[1,6,0,60]` | 每个商店首次进入 +60；在商店内才获得此物不补发本次进房收入 |
| 1310015 养生能量饮料 | 13100151 / IMMEDIATE=1 / `[0,5,0,1]`；13100152 / WINBATTLE=4 / `[1,4,0,1]` | 获得时把上限赋值为 1 并钳制当前值；真正胜利和免战胜利都补充 1，按胜利收据只生效一次 |
| 1310016 无人的风景照 | 13100161 / OUTRANDOMROOM=11 / gap=3 / `[301,1]` | 从持有开始累计真正完成的抉择房间，第三次完成才赠物；安全屋、剧情节点、事件多跳、旧 used_events 长度不充当抉择完成次数 |
| 1310022 完美的设想 | 13100221 / FOREVER=2 / `[1,26,0,1]`；13100222 / OUTANYROOM=10 / gap=3 / `[1,1]` | 禁止已覆盖路径的正向金币获得，负向消耗仍生效；持有后每三次实际房间完成随机提升一个可升级战术外接程序；普通战斗需最后奖励领取完成，终局 Boss 无奖励窗时在结算完成 |

原始 `decompiled_v2/x64/game/config/rogueteam/rogueteameffectcfg.lua` 对应起始行分别为 3426、3440、3643、3657、3671、3748、3762。`game/const/challengerogueteamconst.lua:169` 给出 moment 枚举。旧 `rogueteam_cfg.json` 的 effects 存在 moment=0、event_effects 缺 moment，不能据其把第一条效果当作“获得时效果”。本轮没有重写大配置，新增相邻合同 `rogueteam_effects_contract.json`，由 `tools/export_rogueteam_effects_contract.py` 从只读 Lua 可重复导出；专项断言导出结果逐字段相等并核原始文件 SHA256。

补充合同收录 55 个原生正常收集品与其 65 条效果，供周期赠品筛选和获得时解析使用。这不是 65 条效果均已实现的声明。周期赠品执行其 `effect_id_list` 中 IMMEDIATE 效果及 FOREVER 属性注册，完整处理原生属性值/百分比、直接治疗、程序发放与升级；未来房间/战斗时机不在获得时偷跑。例如 1310006 赠出两个未持有程序、1310013 两条立即效果均处理，1310005 按当前金币的 50% 算而非固定 +500。额外发放和升级均生成所有权及动画差量，来源使用原生 `ITEM_SOURCE_TYPE.RELIC=1`；这些新程序亦接入既有图鉴及勘察计数。

**pool301 的公开证据不足以复原官方池表与权重。** 原生只暴露效果参数中的池号，当前 x64/x86 客户端提取目录没有其服务端池成员/权重表。本轮采用明示的本地策略：从原生 `type=3/sub_type=1` 正常收集品均匀抽取，排除本局已持有；不混入 132 负面或 133 剧情收集品；池耗尽不发重复。它修复了旧泛 relic 池误混负面品，但不能声称已核准官方所有成员、解锁权重及耗尽策略。程序赠品同样沿用本地配置与可证明的池/流派限制，无官方 RNG 权重声明。

**撤回首版的无效 3→4 升级推断。** 原生 `game/tools/challengerogueteamtools.lua:510` 的 GetItemRare 对 subtype=1 钳到 3，其他 subtype 固定 1；`:562` 的 GetTreasureAffix 明确消费该等级。最终候选因此为 type=4、subtype=1、rare<3，升级 1→2→3；没有候选时计经过房间而不制造升级产物。现有服务端用于面板配色的 default_rare 约定未整体重写。专项验证 2→3、满级及其他 subtype 不得生成假奖励。

## 状态、事务与输出链

`rogueteam_effects.py` 使用 `flow_json.room_effects_v1` 保存道具计数、进入节点、完成节点与胜利收据；节点键含楼层、主/隐藏地图及节点 ID，外层 `flow.run_id` 随新局初始化。有效取得以后开始计时，重复取得同 ID 不叠永久效果、不重置计数，刚在本节点获得的道具不追溯计算本节点。旧存档只有道具而没有效果收据时，在第一次成功变更时从零开始；只读/登录不猜获得时刻、不用全局累计房间数迁移，也不补发历史收益。

入口选择、事件选择、奖励领取、商店购买/刷新/退出、战斗结算、丢弃均在既有守卫以后套用 savepoint。只在成功状态转换后核对前后持久状态，资产、图鉴、收据、效果投影与协议编码失败共同回滚。原 `commit_event_selection` 的即时属性/英雄结果只算不回写问题亦补齐，避免 `_attr_dict/_heroes` 被旧数组覆盖。

`effect_list_json` 持久化五件道具的七条效果，保留其他来源。客户端按 effect_id 更新，`left_time=0` 移除；永久效果采用本地哨兵 left_time=9999，trigger_cd=0，不捏造未给出的真实冷却。`moment_time`、`calc_value` 对应原生描述读者。

原生 `game/action/challengerogueteamaction.lua:74/110/117` 分别消费 88021、88205、88017；`game/data/rogueteam/challengerogueteameffectitemlistdata.lua:26/35/59/69` 负责所有权初始化、效果全量初始化和差量更新/移除。缺省差量插在已有 88001 全量之前，确保先有物品所有权；一次响应已有 88001 则不再冗余发送 88017。没有全量时发所需 88017 增改或 tombstone；属性与英雄仍分别使用 88013/88019。

终局 Boss 会直接完成房间，周期升级不能依赖后续不存在的 88200：`rogueteam_battle.py` 已接入 `settle_battle_node` 返回第三项中的 item_list/update_list，输出为 **88033 → 88021 → 88205 → 88001 → 88013 → 88019**（有其他必要事件帧时按既有流程插入），无额外 88017。普通战的周期完成仍等最后奖励领取后发生。

## 验证及可复现材料

验证只用 `ACCOUNT_DB_PATH=:memory:` 的 AccountDB、真实 Core、真实 codec，以及确定性持有/节点夹具，不挂载生产数据。`python -B -X utf8` 配合 PYTHONPATH=`v5_server;v5_server/tools` 运行。

21 项专项在 `tools/verify_rogueteam_effects.py`：原生合同/哈希、获取不偷跑、进入幂等、冷读状态不变、商店两种收入、正价/免费/后取得失效边界、能量上限赋值及真实/免战胜利、抉择计数、重复取得、赠品完整程序链、立即多效果/百分比/未来时机过滤、可消费等级上限、无金币正收益/负支出、正常战最后领奖、final boss 原子输出、88017 与赠品 88205 失败回滚、final boss 88021/88205 分别失败回滚及重试，以及空外围表的真正冷新档登录。

原有回归 75 项来自 TransitionTests、Issue8Tests、RogueBattleTests、RogueLoginTests、HiddenGuardTests、MirrorSourceTests、CoreHiddenWireTests；全部通过，既有 Issue8 与主/隐藏图路径保留。

接受日志：`archive/rogueteam_effects_20261105/astra_ultra_effects_cold_login.txt`（21/21）、`astra_ultra_regression.txt`（75/75）。原先 `astra_ultra_effects_accepted.txt` 的20项通过版本也保留。同目录 `astra_ultra_manifest_final.json` 记录最终源码/合同/测试/报告 SHA256 及原始 Lua 哈希；早一版本 `astra_ultra_manifest.json` 保留，勿当最终值。失败尝试日志保留：首轮两例为测试夹具误设 shop event_type=3（原生要求7）及重复 boss 夹具遗留完成收据；后续一例为测试假定 maxHP 属性初值0而实际1000，改成验证原值+250。没有放宽玩法断言以让测试通过。

主线程额外回归揭露旧登录夹具预置 rogueteam_user，掩盖空表真实 88315 路径：`get_user_outside_info` 的新行初始化写 `tech_point=9999`，而实际 account_db.py:892 的表无此列，故首次登录会失败。这不是缺迁移；实际 `unlock_tech_node` 使用材料 TECH_ITEM_ID，tech_point 属旧未注册方法遗留。本轮只移除新行的错误字段，不添加列、不凭空发9999、不改变现有科技消费。新增专项从空表经真实 generator.gen_payload(88315) 和完整 serialized login 执行，验证合法默认外围行、0 point，无材料/货币/运行存档/历史/科技树副作用。此小修前另留 `fresh_login_before/` 备份；21专项在小修后通过，75回归在此前效果收敛版本通过，主线程另验原先暴露问题的成就登录测试。

旧 codec 警告仍有 sc88001 的 difficult/hero_list/attr_list/in_game、sc88305 的 max_difficult、事件 sc88023 的 _history 被 schema 丢弃；本轮状态验证依赖真正存在的字段，属性/英雄另有专门帧，未把这些警告算作客户端实机通过。

## 明确保留项

1310010“活力泉水”原本被误当成额外30%治疗，本轮移除这条错误增益；其原生含义为按实际恢复量提高生命上限。真实逐英雄恢复量/最大生命权威报告未建立，正确的最大HP增长仍延后，不用聚合 injured_num 推算。1310011、1320004 的恢复叠加/百分比及其他未审计 HP 行为也不宣称已修复。

未扩大为全量道具/科技效果审计；其他收集品未来时机、被动撤销、所有获取渠道的统一原生解释器仍未覆盖。pool301 官方成员/权重、旧存档历史计数恢复、同类型连续请求无 nonce 的区分边界、隐藏地图 ACK 重试、guide101 与真实客户端行为仍保留既有报告边界。主线程指出的 RogueTeam 全局成就生产未接问题亦不在此线扩大修补。
