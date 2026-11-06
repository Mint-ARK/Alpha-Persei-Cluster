# 桌面观察记录：弥弥观测站派遣两项修复

2026-11-05。本轮优先读取 `C:\Users\Suzur\Desktop\观察到的BUG.txt`，原文件仅只读，完整保留、未编辑。暂停上一项Rogue入口审查，未并写Rogue service/flow/transition/login/middleware。

## 三条观察的当前结论

| 桌面观察 | 当前本地结论 | 云端/边界 |
| --- | --- | --- |
| 单个派遣后画面仍空，重进界面正常 | 本轮复核时仍未修；已在本轮本地修复数据先于刷新回调的顺序 | 此为本轮新增源码，尚未同步，更未由云进程加载；只重启旧云源码不会得到此次修复 |
| 派遣未广播，任务不响应，重登不恢复 | 本轮复核时仍未修；已接成功派遣事实、必要任务广播及普通/新档冷恢复 | 同上；不是已有本地补丁仅等待云重启的情况 |
| 一次退出登录选区后点击无响应，重启游戏恢复，用户推测丢包 | 当前没有异常时日志或复现，未定位、未修；不能确认网络丢包，也不能证明某个已有修复覆盖 | 未连接云端或修改连接/登录链，后续以实际报错/断连日志鉴别 |

已有项目同步记录最后停留 `MG-AUTOCHESS-PERSISTENCE-20261105`，云服务PID1827974当时未重启，此后用户明确暂停上传、远端测试和重启。本轮只根据本地源码及该记录报告，没有用SSH读取新云端状态。后续上传和加载须等用户明确恢复同步/加载；本轮不启动任何服务。

## 真实根因与修复

1. 当前Lua `admincatexploreaction.lua` 的67005成功回调立即触发 `EXPLORE_UPDATE`；67001只调用Data.InitExploreData填队列，不再次触发该界面刷新。旧Operation输出 `[67005,67001]`，第一次刷新因此读旧队列。`AdminCatExploreOp` 现先准备67001、再67005，任务差量在前，所有必要帧在Core事务内准备，失败回滚派遣和通知。
2. `AdminCatExploreService.start_explore` 原先成功不发任何任务事件；任务订阅器却把condition9错误挂在 `EQUIP_REFORGE`。本轮逐条检查229/311两份原生AssignmentCfg，condition9 **仅6014(type6、need1、参数[0])与160145(type603、need1、空参数)**，都是管理喵派遣一次，没有限定区域或猫咪，没有“派遣完成后才计”的语义。
3. 新增 `Events.ADMIN_CAT_EXPLORE_START`，只在成功保存一次派遣后 `emit_required`；新订阅器严格投影上述两个任务，撤掉刻印重构错误推进condition9，原cond350行为保留。失败派遣、同请求重复导致区域/猫已占用、未解锁、非法时长不会计数。两个不同合法派遣各加一次，任务按原need1封顶，不代领、不发任务奖励。
4. 新增 `admin_cat_dispatch_progress.py`，使用 `ops_common.admin_cat_dispatch_v1` 保存版本化总次数与05:00游戏日次数，成功派遣与事实、任务、67001/67005/28007同一Core事务。普通generator28001及新档28001窄接同一恢复，已领/过期任务不被重新打开。
5. 旧档没有累计记录时，只从实际存在且时间有效的queue.start_time或explore_data.daily_time证明“至少1次”；不把多条队列推测为完整历史次数，不把昨日事实补到今日每日任务，完全无事实则不补。旧错误任务进度/已领资产没有被倒扣或清空。

## 编号与范围

在当前 `health_audit_index.md` / `repair_tracker.json` 中未找到直接对应“派遣ACK先于状态刷新”或“condition9错误事件归属/派遣恢复”的既有A编号。本轮不猜造编号，交根线程登记观察缺口。

相邻A233涉及探索周天数/自然日累计，原服务weekly_time/daily_time的自然日逻辑仍未改；新任务事实独立遵循05:00，不应把A233整体关闭。A236是67006/67008领奖资产差量缺失，本轮只修67004派遣，不将A236误计为完成。A232/A234/A235/A239/A241等其他猫咪旧项未扩修。

修改：新progress模块、专项脚本与本报告；窄改67004单类、start_explore成功分支、event_bus新事件、task_listener正确订阅/移除误归属、generator普通28001及new_player_service新档28001的恢复钩子。覆盖前源码备份 `archive/admin_cat_dispatch_20261105/before/`。没有物理删除文件。

## 验证

- `tools/verify_admin_cat_dispatch.py`：13项真实内存AccountDB/Core/EventBus/codec检查通过，含67001先于67005、在线28007、普通/新档恢复、两版本完整cond9目录、两个成功派遣/重复失败/非法参数、刻印不误推进、旧证据保守恢复、05:00边界、已领保护以及67001/67005/28007/累计写故障整体回滚后重试。
- 一次关联 `verify_report_small_fixes.py CatTests`：6项通过，保技能资格、解锁等级、精确奖励与原资产合同。合计19项本地检查；相关Python编译通过，未扩大到全仓库反复测试。
- 证据 `archive/admin_cat_dispatch_20261105/test_final.log`、`regression_cat.log`。初版自测发现新任务已入库但pending28007尚未被respond取出，已修为 `_refresh_frames(strict=True)`；另新档测试夹具缺必需newbie_activity初始行，补齐后通过。两轮迭代日志保留。

所有账号数据均在SQLite内存；未写真实数据库、未启动服务、未云测/同步/重启、未Git或封包、未修改Lua，尚未游戏实测。静态Lua和本地编码链支持此次顺序修复，但不冒称已经实机确认用户画面恢复。
