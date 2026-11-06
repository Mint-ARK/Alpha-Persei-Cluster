# A254：五项新手通知标记持久消除

2026-11-05，仅本地开发源码。没有操作真实存档、游戏Lua、服务进程、云端、Git或发布包。

当前Lua `activitynoobaction.lua:151—184`的五个请求实际清除对象为59016→mc_new_role、59018→mc_new_sign、59020→fr_new6、59022→fr_new18、59024→bp_new；旧59016/18的中文注释不准确。新增`newbie_notice_service.py`，只在当前已有活动行对应列非零时清零；缺行或已清均幂等，不创建/激活活动，也不改变奖励资格、签到天数、物品或其他标记。

`operations.py`五类仅复用此定向操作，必要ACK在原Core事务内编码；编码或SQL失败完整回滚。成功只回各自ACK，沿Lua本地清除，不重复推全量。`recharge_service.build_59009_payload`现在读实际schema与充值交付使用的bp_new，保留可独立只读调用的project_59009_payload。普通59009、59011和新档59011充值子对象同源恢复，不再永久false掩盖未读标记。

本次核Lua发现失败边界：五个ACK回调均不检查result，收到失败也会本地清false。因此五类沿用Core既有`error_recovery_frames`，在失败ACK后补59009恢复存档事实；恢复只投影已存在行，不调用get_or_create。若恢复包本身无法编码，服务记录错误且持久状态仍保留，等待后续正常冷读取恢复；不能声称此异常情况下即时界面已恢复。没有修改客户端。

`tools/verify_newbie_notices.py`6项内存Core/真实codec验证首轮通过：逐指令只改对应列且成功单ACK、重复幂等、普通/新档冷恢复一致、SQL写后失败回滚与错误ACK→59009顺序、五种ACK编码失败回滚后可重试、缺行不创建、恢复帧也失败时SQL保护。有限Python本地消费者镜像仅验证回调顺序，不是原生Lua/实机测试。

覆盖前副本与日志：`archive/newbie_notice_20261105/`。A248—A250普通账号领取资格、A255充值子奖励等仍独立保留，本票未改变任何奖励金额或发放政策。
