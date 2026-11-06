# A139/A140：成就门槛与物语恢复一致性

依据health_audit_20261008_round94_achievement_threshold_recovery.md。复核当前AchievementService、generator53001、login洪流及当前成就配置；历史缺db_schema.sql/chip的环境问题没有泛化成当前部署仍缺表，本轮以隔离内存缺表和补算异常复现恢复边界。

## 修改

- A139：当前已装载成就配置的need统一用于领奖判断。正常资格全部通过后，先同步所请求记录的need，再执行已有0→1条件标记和发奖事务，保留A137去重、A138整包资格规则。
- 初始化即使成就行已齐也同步旧门槛；动态事件和补算对已读取的记录做同样的批量同步。无门槛变化时不写库，不逐成就查询。
- 只更新need，不覆盖progress、已领奖标记、奖励字段和真实完成时间。补算按同步后的门槛清理错误待领奖时间，并同步本地快照，避免刚修正SQL又按旧快照处理。已领奖历史不重开，不追溯补发或回收。
- A140：正常和降级读取共用物语阅读解析。明确空列表和已读子集保留；损坏资料/错误字段类型使用安全空阅读状态，降级不写回用户extra。缺阅读字段仍保留原有“根据成就点兼容旧账号”的规则，正常入口按原方式保存该兼容结果。
- 成就补算失败记录原因，以实际achievement记录和持久阅读状态构造53001，不再固定六篇全已读。登录53001强制动态、缺行补齐、必要恢复失败不回退旧素材。

未重新设计已读/可读旧账号兼容政策，也未重构全部用户extra写入、条件生产及所有成就的参数语义。本次配置权威仍为当前服务已经装载的achievement_cfg.json，不额外引入配置分版系统。

## 验证

tools/verify_achievement_threshold_guard.py新增11项，修前14个失败断言；修后新增11项加前轮领奖10项，共21项通过。真实Core/Inventory/SQL/codec和内存登录洪流覆盖：旧need20→当前10可领、旧need5不能提前领、全行初始化仍同步、动态升级53003、真实时间/已领历史、清理未达成时间、降级空/子集/坏资料、旧点数兼容、真实内存缺chip，以及静态登录记录不能覆盖当前阅读状态。

测试的DROP TABLE仅对内存数据库，未操作真实存档；外部AI入口在洪流测试中适配为不调用。未经游戏实机。

## 同步

achievement_service.py、generator.py、login.py已同步43.139.154.74:/game-server/aethergazer，源码副本archive/achievement_threshold_20261103_113341_5e70f9。仅同步文件，无存档备份或重启，PID1693172保持不变，待统一加载。

云端另起独立进程以虚构内存数据验证高低门槛、完成时间、缺表降级已读子集和实际静态登录帧覆盖，全部通过。它不表示旧业务进程已经加载修复。日志中的no such table: chip来自该内存故障夹具。

根archive/achievement_threshold_guard_20261103保留源码前件、before_checks.txt、after_checks.txt、events.json和云端探针结果。next_release_fixes.md及台账已追加；未Git/封包。
