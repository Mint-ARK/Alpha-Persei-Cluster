# A249：普通账号新手等级奖资格与一次领取

2026-11-05，仅本地源码；没有写真实账号/存档、重启、云操作、Git、打包或Lua修改。

当前实际注册为FreshNoobLevelOp：new_player_v1已走已有新档规则，而普通账号仍退回旧NoobLevelRewardOp，任意level都发1×200再覆盖领史。本票修普通分支；新档奖励目录和既有分支保持原样。

新增newbie_level_reward_service.py，只接受当前GameSetting.levelup_reward已有20/30/40/50/60档，检查真实users.level、已存在且已触发的新手活动、有效期限与唯一领史。缺行拒绝，不借领奖激活活动；completed_time=0按当前Lua IsFinishAllActivity语义视为尚未结束，正截止时刻则拒绝。普通账号原有1×200定制金额保留，没有替换为新档11084/2540999等配置奖励。旧异常领史（如999）不删除、不补偿。

唯一领史插入与公共资产发放同Core事务；严格准备17023和59005任一失败全部回滚。成功顺序为17023权威资产→59005奖励ACK。原生ActivityNoobUpgradeItem成功回调自行SetUpgradeFinish，故不补59011（InitUpgradeData会再次append）。重复领取返回非零，不再发资产，也不触发第二次成功append；冷59011继续读取已保存领史。

tools/verify_newbie_level_reward.py的6项内存Core/真实codec专项首轮通过，覆盖5个档位的等级边界/防重/既有金额、未知档、未触发/未来/到期/缺行、零截止值及旧异常领史保护、领史/资产写后失败、必要ACK/17023编码失败回滚与重试。另运行verify_new_player_save.NewPlayerTests.test_level_reward_guards_and_idempotence：只读既有实验库复制至:memory:后验证新档原规则，1项通过。合计7项，未做实机或生产网络验证。

证据及覆盖前副本：archive/newbie_level_reward_20261105。A248签到、A250累计任务奖、A255充值子奖励仍独立保留，不能由本票推定整个新手活动已修完。
