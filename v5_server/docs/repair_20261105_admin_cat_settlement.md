# A232/A236：探索到期资格与资产原子差量

2026-11-05，仅本地。A232、A236可标记source_verified。覆盖前备份archive/admin_cat_settlement_20261105/before；未修改Lua、真实账号库或云端，未启动服务、重启、SSH/SCP/Git/封包。

A232：普通67006现显式force_finish=False，服务默认亦为False；未到stop_time返回非零且不发满时长奖励、不清队列。达到精确秒边界可领一次，重复请求无收益。保留显式内部调试参数，不把它暴露为普通RPC。没有新增永久payload去重，协议仅有area_id，跨新探索实例的极晚请求仍须按当时有效队列处理，未虚造nonce。

A236：67002升级费用、67010解锁费用、67006探索收益、67008周奖励统一prepare_response_in_transaction，并委托新admin_cat_response.frames准备必要ACK、67001与strict17023资产差量。真实资产/技能/猫/队列/累计/领奖标志和必要编码同一Core事务，任何失败回滚后可重试。无重复发奖，不把表现reward_list再次入库。

响应顺序是17023资产在先、各自原生ACK、67001最后。升级/解锁的Lua ACK有本地增量，快照不能先发造成再增一次；结束ACK会读取旧region后删除，因此也保ACK在67001前。派遣67004仍保上一票67001在67005前，两种原生回调合同不同，未全局机械翻转。经验53跨等级时先更新ItemData资产，再进入依赖经验的67001计算。当前定制收益/天赋10%/技能及精确整数ceil保留，A239未借机改金额。

修改：admin_cat_explore_service.py的finish默认、operations.py管理喵67002/06/08/10四类、admin_cat_response.py新模块；新增tools/verify_admin_cat_settlement.py。原verify_report_small_fixes.py的CatTests内存夹具补53—56真实货币目录，用于首次实际走strict17023；此前测试不发该差量而缺catalog，失败诊断为“无法确定道具56的类型”，未放宽生产守卫。

6项专项（内部覆盖四入口各三类必要帧故障）与6项既有CatTests通过，共12项；实际内存AccountDB/Core/Inventory/codec：提前/到期边界/防重、默认服务到期、真实奖励与绝对余额、资产→ACK→状态顺序、费用扣0、周奖/升级/解锁、各ACK/67001/17023失败全回滚、队列删除后故障整体回滚可重试。日志archive/admin_cat_settlement_20261105/test_final.log、regression_cat_final.log。初次失败回归及诊断保留，相关Python编译通过。

没有验证原生客户端实际回调和网络传输。A233周累计、A234周末窗口、A239作用域及A241日志奖励仍未修，不将整个探索玩法关闭。
