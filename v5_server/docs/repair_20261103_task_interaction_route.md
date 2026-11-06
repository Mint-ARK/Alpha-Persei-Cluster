# A160 大厅交互任务广播纠正

2026-11-03。依据第109轮报告及当前Lua assignmentcfg的6013（condition8/type6/need1，“与修正者交互1次”），修复任务订阅养成广播导致战斗熟练度、等级/技能等养成代替交互的问题。

## 修改

- TaskListener的condition8订阅由HERO_UPGRADE改为MAIN_INTERACT，名称/注释同步改为大厅交互。
- 每次真实触摸事件推进1次，并携带hero_id供参数匹配，不采用养成times或累计触摸次数作为本次增量。
- 复用现有32054→PeripheralService.touch_poster_girl→MAIN_INTERACT→28007→32055链路，没有新增协议或修改客户端。已有完成/已领取任务不再次推进，不重置历史误完成记录。
- HERO_UPGRADE生产者、成就、誓约与信赖消费者未修改；打关/扫荡的熟练度、胜场和共用档案经验保留。

业务仅修改task_listener.py。未扩展其他任务条件或重新设计大厅触摸的角色回退规则。

## 验证

tools/verify_task_interaction_route.py共8项真实内存Core/SQL/codec检查。修前14个失败断言，修后全部通过：9类养成事件不完成交互；实际普通/三次扫荡成长合计熟练度4、胜场4、档案经验20且仍发布养成事件；实际触摸推进6013并在32055前发28007；重复触摸不再发任务差量但触摸计数照增；已领取历史保持；回执故障完整回滚后可重试；隔离另一UID。

初版英雄夹具clear_times未指定，旧DDL默认NULL使熟练度断言额外失败；改为真实0起点后重新保存修前日志before_checks_v2.txt，仍14失败。没有借此顺手更改成长写入策略。

旧test_hero_response_boundary.py原来把合成condition8任务增长当作养成正常结果，已调整为交互任务不增长。同时补齐其缺失的版本上下文/单人策略/非新存档适配，及当前成就_sync_achievement_needs真实方法。第一次回归因旧夹具拒绝新模块导入失败，诊断和修正日志均保留；最终13项养成事务/响应、关系网刷新和重复有意升级回归通过。它不用于验证版本选择、多人与新存档功能。

最终共21项通过，数据库写入均在内存，未经游戏实机。

## 同步

task_listener.py已同步43.139.154.74:/game-server/aethergazer，源码副本archive/task_interaction_20261103_123608_6f263e。独立云端内存Core探针通过，含真实成长/触摸、任务差量顺序、重复触摸、失败回滚；没有写真实存档。

服务PID1693172保持不变，未重启，待统一加载；未备份存档、未Git或封包。台账A160和发布小本本已更新。

证据archive/task_interaction_route_20261103：before、before_checks_v2.txt、final_checks.txt、hero_response_final.txt、旧夹具失败/诊断记录、events.json、remote_result.json及部署/探针脚本。
