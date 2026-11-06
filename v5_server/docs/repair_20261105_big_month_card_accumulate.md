# A267：大月卡已日签后的累计权益补领

2026-11-05，本地源码验收。A267 可记为 source_verified。本轮只修改 operations.py 的 BigMonthCardSignOp.respond；paid_entitlement_service.py、购买/续期/价格/跨日规则未改。

历史问题中的“已签到即提前返回”已由现有 claim_big 解决：已日签调用 _big_rewards(row, 0)，保留当天签到和累计天数，只消费尚未领取且已达标的累计档位。本轮按真实 Core.dispatch、AccountDB 内存库、实际 paid.settle、InventoryService 与 codec 验证旧存档 day7/[201] 可补 202 的既有 200 奖励，不重发每日材料或 100 每日补给。

本轮发现并修复同一补领链的交付缺口：34111 编码为空时原实现用 0800 伪成功；34101 的缺失/异常被吞掉，累计领取记录仍可能提交。现在 34111、34101 和必要资产差量必须在原有事务内准备成功，失败统一非零 ACK，并回滚资产、领取记录以及该操作内的权益迁移通知。

最终帧序为 strict 资产差量及操作内前置通知 → 34111 → 最终 34101。Lua bigmonthcardaction.OnSignToday 会比较旧累计列表；bigmonthcarddata.SignAccumulate 遇到第一个已有 ID 就 break，故完整 [201,202] 仍需要回执后的完整 34101 收敛。最终快照不能提前覆盖旧累计列表，也不能被 settle 在补领前排队的旧 34101 再覆盖。保留现有完整状态帧，仅调整位置并使其成为必要帧；不改变 Lua。

验证：tools/verify_big_month_card_accumulate.py 6 项全过：

- 旧存档迁移后已日签 day7、缺 202，仅补既有累计奖励；SQL 天数/材料抽取记录不变，邮件不增加，最后 34101 与冷包相同，再请求无正数奖励。
- 已迁移的当日领取游标同样补一次；17023 先于 34111，最终状态在回执后。
- day6 未达标不发 202、不推进签到天数。
- 两种现有模板保留已领档位，只发已达标的缺失档位，不发未来奖励。
- 34111、34101、17023 三种编码失败均返回失败，整个内存 SQL dump 与操作前相同，pending 通知清空，之后重试只发一次。
- 领取记录写失败回滚此前实际资产发放及旧数据迁移，重试成功。

另跑既有 verify_paid_entitlements 两项回归通过：test_big_daily_full_period_and_natural_renewal、test_card_asset_frame_failure_rolls_back_claim（后者是小月卡共用资产失败边界）。共 6 + 2 = 8 项检查通过，不把子断言另计为检查数。

测试使用 SQLite :memory:。专项只隔离 Core 前置全局 lazy_timer.pulse，claim_big 内的 paid.settle 未 mock，确实覆盖旧权益迁移及排队快照；固定时间且未连接外部服务。既有响应 skeleton 打开文件未关闭的 ResourceWarning 原样保留，未顺带改共享 middleware。

证据：archive/big_month_card_accumulate_20261105/ 下 before/ 保存覆盖前 operations.py、paid_entitlement_service.py 和第一版测试；test_before.log、test_before_corrected.log 保留失败过程；test_after.log 为最终 6 项通过，regression_paid.log 为 2 项回归。首次测试把既有零数量占位奖励误断言为空，已修正为“无正数奖励”，没有为通过测试改变生产奖励形状。故旧日志中零奖励断言失败不算新生产缺陷；旧代码的 ACK/快照伪成功和帧顺序失败则确实复现。

边界：未真实客户端点击、网络交付或重登实测；冷包使用真实 generator 编码验证。A263 价格政策和 A264/A265/A266 未随本票关闭。未写真存档、修改 Lua、Git、打包、云同步或重启。用户要求“再修一个后停工”，本票为最后一项，完成后停止。
