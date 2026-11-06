# A79/A80 梦境再构领奖资格、幂等与查询失败

2026-11-04。承接A81公共发奖异常回滚，核对第54/55轮报告、当前45004/45102/45112注册Operation、AccountDB/Inventory与客户端星级、积分、领奖回调。

## 修改

- 三个入口共用_claim_boss_challenge_rewards：在现有周期惰性刷新后，严格读取当前界面快照、区域/进阶档位、成绩与已领记录。没有选择模式、类型不匹配、当前配置不存在的档位返回2；未达标2005；重复单领2013。一键没有可领奖项仍可空成功。
- 兼容mode=3/4/101/102旧格式及现行mode=1/2配合area_id/advance_id格式，修复mode2误用默认102奖励表。
- 普通星级按当前首领的stage_id去重、统计星值等于1的项，与StageTools.CalcBossChallengeStar一致；进阶只计当前界面首领的max_point，新旧成绩表沿既有快照合并规则，不对同首领重复求和，不把未选首领的遗留成绩累加。
- 单领与一键共用领史；全部资格通过后标记，再按配置汇总发奖，整笔由Core事务包围。发奖、标记或必要响应失败一并回滚。没有新增数据库列、批量清历史或自动补发。
- get_boss_challenge_data增加显式strict参数，领奖路径上的刷新、进度、成绩、JSON及领史读取异常向外传播，不能等同空表或未领；其他既有展示调用默认容错口径保持。
- get_boss_challenge_reward_items仅查当前区域/模式，不再跨模式搜档或发固定兜底奖励。合法配置的数量不变，合法空奖励与未知档位由配置资格区分。
- 三个Operation开启prepare_response_in_transaction；取消空ACK成功兜底，保留原生45005/45103/45113与17023资产差量。撤掉领奖后的45001/45101全量状态帧，Lua成功回调原本会维护单档/一键领史和红点，避免重复插入已领记录。

## Lua依据

Item/StageTools的星级列表按stage_id覆盖后统计等于1的星；BattleBossChallengeAdvanceData.GetTotalPoint按当前bossData求和。普通BossStarExchangeItem成功后SetReceiveStarList；进阶BattleBossAdvanceRewardItem成功后ModifyReceiveReward；一键RewardView成功后ModifyAllReceiveReward，失败均只ShowTips。各入口仍等待原生ACK，没有更改客户端Lua。

## 验证

tools/verify_boss_reward_guard.py共12专项、8公共发奖回归，20通过；真实内存AccountDB/Core/Inventory/codec。旧weekly两个函数及三Operation方法提取，12专项全部失败，共33个失败断言，原输出留存。

覆盖普通3/4区及进阶101/102共50个配置档位的合法奖励；门槛前后、未知及其他模式档位、锁定模式、合法空一键；单领后一键、一键后单领、重复重放只发一次；stage重复不多算星、两张成绩表/未选首领不多算分；当前/旧模式格式；冷45001/45101领史恢复；进度/成绩/台账/刷新查询失败；三入口空/异常ACK及17023缺失；标记写后和真实入账后失败全回滚，随后正常重试。

前轮verify_grant_failure_guard补上合法7000分成绩夹具，原45101全量断言调整为无全量并核对冷领史；原脚本前件已备份。没有为了通过检查去放宽新资格。

数据库全部为内存，有限Lua计数/回调契约核对，没有运行Unity/Lua或游戏实机。当前单档/一键串行幂等已验；未扩大为所有跨进程并发、网络送达保证。

## 周期边界

本票服从当前UI快照与现有check_and_lazy_refresh_boss_challenge周期，验证过期存量先刷新、旧领奖记录不阻止新周期达标后领取。请求失败会回滚该次刷新，下一次正常数据读取可持久化刷新。没有在此票修改种子周三/清理周四不一致的A85、BUS旧表A87、旧战斗跨周A86；这些仍待独立修复，不把本票认作整个梦境模块验收。

## 云端

operations.py及weekly_challenge_service.py已同步43.139.154.74:/game-server/aethergazer，覆盖前副本archive/boss_reward_guard_20261104_002818_8331fe，哈希/编译及原子替换通过。云端独立内存Core验证现行mode2/advance101、7000/9000/11000档单领一键互操作、普通3星、重复2013、未达标2005/非法2、已领读取异常9、空ACK1完整回滚，领取后全量状态帧0。

PID1827974保持，源码待统一重启，未写真实存档、不Git或封包，未经游戏实测。证据archive/boss_reward_guard_20261104保存源前件、修前后输出、payload/probe、哈希和远端回执。A79/A80台账、小本本、协作看板及总清单更新。
