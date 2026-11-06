# A250：普通账号新手累计任务奖

2026-11-05，仅本地源码；不写真存档、不重启、不云操作/Git/打包、不改Lua。

实际注册FreshNoobAccumulateOp对new_player_v1已有独立规则，普通账号仍走旧无条件38×2和覆盖领史。本票仅修普通分支：新增newbie_progress_reward_service.py，以及从当前NoobVersionCfg/AssignmentCfg导出的newbie_reward_contract.json（3版本、315任务ID/type/phase）。tools/export_newbie_reward_contract.py保留来源SHA256，重复导出自动备份旧目录；奖励数额不导入，普通既有38×2定制保留。

59014的id为1基奖励档位索引，不能当任务ID。普通存档按持久version_id选择合同：版本2为type601、首档15；版本3为type603、七档10/20/30/45/60/75/90；版本1没有累计奖励表，未知版本/索引拒绝。查询真实已存在且已触发活动与期限，不用领奖创建资格。completed_time=0仍按Lua未结束语义，正截止时刻拒绝。

完成数只统计当前目录有效ID、正确task_type、当前已发布phase范围内且complete_flag>=1的任务。依据TaskTools.GetNoobCompletedList与ActivityNoobTaskView.RefreshProgress；仅progress达标、单有claimed_ts、未来phase、其他版本类型或目录外假ID不能充数。普通max_phase保现有59011投影口径（0时旧fallback7），本票没有扩改A256任务初始化/阶段解锁生产。

既领索引非零拒绝，领史INSERT与公共发奖同事务，必要17023/59015编码失败完整回滚。成功17023→59015；Lua成功回调自行SetAccumulateReceivedList，不额外补59011。冷59011继续从已保存领史恢复，旧异常索引不删除、不补偿。新档分支未修改。

tools/verify_newbie_progress_reward.py 7项内存Core/真实codec专项首轮通过：目录与原生源一致、9/10边界、七档门槛、防重/未知索引、当前类型/phase/完成标志、版本2的601/15门槛、无目录/未触发/过期/缺行、领史/资产写后与ACK/17023编码失败回滚重试。另1项新档累计奖励既有测试，实验库只读复制至:memory:后通过。合计8项；未进行原生客户端/线上网络验收。

证据与覆盖前副本：archive/newbie_progress_reward_20261105。A248签到与A256初始化等其他旧问题仍独立保留。
