# A233/A234：管理喵游戏日与周末领奖窗口

2026-11-05，仅本地开发源码；没有操作真实存档、服务、云端、Git、发布包或Lua。

A233已修：`admin_cat_explore_service.py`统一调用既有`lazy_timer.get_daily_5am_ts/get_weekly_mon_5am_ts`。派遣成功后的`daily_time`仍记录实际时间，仅按05:00游戏日判断本日是否已计。午夜不多计、05:00可新增、周一05:00自愈清周账后首次派遣计1；同游戏日一次、上限7。失败建队或必要67001编码失败时，队列、周天数、任务事实一并回滚。原有在途队列、永久`total_explore_c`保留，不推测历史漏计补偿。

A234已修：67008在周状态自愈后，以同一05:00日界取得游戏星期，仅周六/周日开放。周六04:59拒绝、05:00允许；周一04:59仍属于周日、05:00后拒绝。工作日即使有累计也不发奖、不改领取标记。正常周账自愈仍由现有入口执行，不能将周重置误称领奖副作用。等级奖励与现有定制倍率未调整；已有17023资产→67009→67001顺序、防重和事务内响应准备保留。

当前客户端依据：`decompiled_v2/x64/game/data/admincatexploredata.lua:84`的`CheckCanGetWeeklyReward`读`GetGameServerWeek()==6/7`；`manager/timemgr.lua:80/88`使用刷新小时偏移；`admincatexplorerewardview.lua:60`按该门禁展示领取。服务沿用现有全局计时器的已配置本地游戏时区，不新设另一时区规则。

`tools/verify_admin_cat_calendar.py`10项内存Core/真实codec专项通过，覆盖午夜、精确05:00、周一重置与旧队到期领取、同日双队与失败不计、cap7、必要帧/SQL故障回滚重试、工作日和周末边界、零累计、反复冷读。关联`verify_admin_cat_settlement.py`6项通过，含四种资产变更操作、必要资产/ACK/状态编码失败及领取删除失败回滚。合计16项；不是原生客户端实机或线上网络验收。

证据及覆盖前副本：`archive/admin_cat_calendar_20261105/`。专项首轮即通过；运行时既有skeleton文件句柄ResourceWarning保留于日志，未扩大修复共享中间件。A239收益作用域/天赋政策、A241日志实际奖励仍独立保留，不将本轮关闭推广为整个管理喵完成。
