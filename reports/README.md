# 项目问题索引：按现象查模块与报告

整理日期：2026-10-20。汇总 181 轮扫描报告，按 20 个模块列出 381 个问题编号。
点击问题标题可打开报告；按 A 编号搜索，可快速串联同一问题的后续记录。

使用方法：先按下方模块和问题现象打开对应报告，记录 A 编号，再根据“初步定位”和“相关文件与入口”检查代码或日志；也可把对应报告交给 Agent 辅助排查。修复后重测原操作及重新登录后的状态。提交 Issue 时，请附上客户端平台、版本、复现步骤、相关日志及对应编号。

## 模块导航

| 模块 | 条目数 | 主要查找内容 |
| --- | ---: | --- |
| [01 公共事务、事件与响应](#m01) | 5 | 重复执行、半途提交、回复失败、通知丢失 |
| [02 网络连接、登录与会话](#m02) | 9 | 漏发登录数据、旧状态覆盖、重连和心跳清理 |
| [03 资产、邮件、背包与体力](#m03) | 14 | 重复扣费、重复发奖、到账不同步、计时与折算 |
| [04 关卡、扫荡、战斗与多维变量](#m04) | 22 | 结算重复、进度回退、体力约束、对局状态 |
| [05 角色养成与权钥](#m05) | 14 | 升级与突破、经验曲线、技能和模板数据 |
| [06 钥从唤名、消耗与实例同步](#m06) | 5 | 材料保护、失效引用、实例删除和在线刷新 |
| [07 刻印装配、突破、重构与赋能](#m07) | 7 | 槽位冲突、突破费用、预览确认和删除通知 |
| [08 任务、战令、签到与周期刷新](#m08) | 23 | 进度不增长、重复累计、领史、跨日和阶段恢复 |
| [09 抽卡、卡池与角色解锁](#m09) | 3 | 扣券与发奖、卡池自选目标、角色解锁刷新 |
| [10 编队、管理喵与芯片](#m10) | 13 | 保存与重登、助战字段、装配资格和解锁条件 |
| [11 梦境与黑区周常](#m11) | 14 | 领奖资格、成绩归属、角色锁定、难度和周期切换 |
| [12 迭代校验与多重心魔](#m12) | 21 | Mode1—4 结算、活动归属、任务奖励和状态恢复 |
| [13 自走棋](#m13) | 28 | 开局与回放、棋盘与技能、胜负、成长和重试 |
| [14 成就、图鉴、插画与收集](#m14) | 14 | 完成门槛、领奖确认、战报统计和收集状态 |
| [15 档案、好感、交心、连携与誓约](#m15) | 32 | 赠礼与成长、解锁条件、任务事件和角色身份 |
| [16 后宅、房间、食堂与猫咪探索](#m16) | 51 | 人员占用、疲劳计时、收益、布局和委托领奖 |
| [17 活动、礼包、商店、月卡与充值](#m17) | 27 | 购买履约、补发、限购、资产分类和奖励配置 |
| [18 换装、场景、看板与个人资料](#m18) | 25 | 轮换设置、装扮拥有、画册贴纸和资料恢复 |
| [19 AI 对话、人格、问候与配置](#m19) | 25 | 重复生成、清空历史、线程排队、配置保存和面板 |
| [20 GM 控制台、维护与资源同步](#m20) | 29 | 快捷指令、事务、参数、备份、日志和版本下载 |

[按扫描轮次查报告](#rounds)

<a id="m01"></a>

## 01 公共事务、事件与响应

| 编号 | 问题标题与报告 |
| --- | --- |
| `A02` | [事件监听器内部报错后被再次调用，同一事件可能执行两遍](<health_audit_20260920.md>) |
| `A03` | [业务已保存但回包失败，客户端重试可能再次扣费](<health_audit_20260920.md>) |
| `A04` | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| `A308` | [必要回复编码失败时被丢弃，业务已保存但客户端一直等待](<health_audit_20261014_round147_reply_wait.md>) |
| `A375` | [数据库提交失败后事务未恢复，后续请求可能误提交旧修改](<health_audit_20261017_round177_nested_transaction_bus.md>) |

<a id="m02"></a>

## 02 网络连接、登录与会话

| 编号 | 问题标题与报告 |
| --- | --- |
| `A09` | [战斗会话的账号归属检查](<health_audit_20260920_round3.md>) |
| `A21` | [同毫秒建立的不同连接可能生成相同战斗编号](<health_audit_20260920_round5.md>) |
| `A142` | [登录补发旧状态，成就和冷却剂进度被覆盖](<health_audit_20261008_round96_login_achievement_cycle.md>) |
| `A303` | [登录只加载前 192 条记录，后追加的合法协议被漏发](<health_audit_20261014_round144_login_flood.md>) |
| `A304` | [登录先发新状态再发旧全量，角色和次数显示回退](<health_audit_20261014_round144_login_flood.md>) |
| `A305` | [登录洪流未完成也激活在线计数和体力恢复](<health_audit_20261014_round145_login_ready_reconnect.md>) |
| `A306` | [同账号仍有另一连接在线，关闭一个连接却标记离线](<health_audit_20261014_round145_login_ready_reconnect.md>) |
| `A307` | [战斗会话丢失后结算返回成功，退出状态与客户端预期不同](<health_audit_20261014_round146_reconnect_consumers.md>) |
| `A376` | [战斗连接超时后未清理心跳序号，同一连接编号重建沿用旧状态](<health_audit_20261017_round178_single_account_session_cleanup.md>) |

<a id="m03"></a>

## 03 资产、邮件、背包与体力

| 编号 | 问题标题与报告 |
| --- | --- |
| `A05` | [同一封邮件可重复领取附件](<health_audit_20260920_round2.md>) |
| `A06` | [邮件发奖失败仍标记已领取，并返回成功](<health_audit_20260920_round2.md>) |
| `A07` | [统一扣费未触发消费事件，相关任务不增长](<health_audit_20260920_round2.md>) |
| `A52` | [系统时间回拨后，体力恢复计时修正未保存](<health_audit_20260921_round26_fatigue.md>) |
| `A53` | [批量使用道具后项失败，前面已扣材料却没有奖励](<health_audit_20260921_round27_fatigue_grants.md>) |
| `A54` | [道具选择越界或奖励配置缺失，仍先扣道具再返回成功](<health_audit_20260921_round28_item_use.md>) |
| `A55` | [自选箱接受重复选项编号，同一奖励可能重复生成](<health_audit_20260921_round29_reward_rules.md>) |
| `A56` | [随机包未命中配置时仍走默认分支发奖](<health_audit_20260921_round29_reward_rules.md>) |
| `A59` | [背包上限折算重复扣减返回数量，奖励列表与实际到账不一致](<health_audit_20260921_round32_overflow.md>) |
| `A70` | [扣费写入后报错，备用逻辑再次扣费](<health_audit_20260922_round46_chip_cost_atomic.md>) |
| `A81` | [公共发奖写入后报错，备用逻辑再次增加资产](<health_audit_20260928_round56_weekly_grant_fallback.md>) |
| `A247` | [活动奖励已入库却未刷新资产，客户端余额和物品不更新](<health_audit_20261010_round127_activity_point_claim_contract.md>) |
| `A280` | [普通冷却剂编号撞上礼包配置，被误发为多日订阅](<health_audit_20261014_round136_shop_special_delivery.md>) |
| `A281` | [活动代币写入和读取分类不同，背包显示与实际余额不一致](<health_audit_20261014_round136_shop_special_delivery.md>) |

<a id="m04"></a>

## 04 关卡、扫荡、战斗与多维变量

| 编号 | 问题标题与报告 |
| --- | --- |
| `A10` | [战报晚到时仍沿用退出结果，胜利无法重新结算](<health_audit_20260920_round3.md>) |
| `A13` | [首通判断与单账号存档的归属关系](<health_audit_20260920_round4.md>) |
| `A14` | [一次胜利触发两次通关事件，任务可能重复计数](<health_audit_20260920_round4.md>) |
| `A15` | [关卡进度写入失败，仍发奖励并返回胜利](<health_audit_20260920_round4.md>) |
| `A16` | [结算未核对体力消耗，零体力也能获得胜利奖励](<health_audit_20260920_round4.md>) |
| `A17` | [普通战斗可走剧情入口，无战报也能完成](<health_audit_20260920_round4.md>) |
| `A18` | [多维变量无对局也能结算，已结算的对局仍可领奖](<health_audit_20260920_round5.md>) |
| `A19` | [多维变量无待选奖励时仍能反复放弃并获得货币](<health_audit_20260920_round5.md>) |
| `A20` | [同一场战斗可被两个请求同时结算](<health_audit_20260920_round5.md>) |
| `A22` | [多维终局事件缺少请求上下文，任务监听报错](<health_audit_20260920_round6_handoff.md>) |
| `A23` | [多维先返回成功再发新状态，客户端回调读取旧数据](<health_audit_20260920_round6_handoff.md>) |
| `A24` | [多维旧战报能推进重置后的新一局](<health_audit_20260920_round6_handoff.md>) |
| `A25` | [多维终局重置后，恢复数据出现额外的 31 项升级](<health_audit_20260920_round7_recovery.md>) |
| `A26` | [扫荡把章节编号当关卡编号，重复发放宝箱奖励](<health_audit_20260920_round8_common_stage.md>) |
| `A27` | [战斗固定上报击杀编号和种族，任务与图鉴统计失真](<health_audit_20260920_round8_common_stage.md>) |
| `A28` | [多倍战斗和扫荡的累计任务进度未按次数增长](<health_audit_20260920_round9_multiplier.md>) |
| `A29` | [支线首通记录读取错误，首通奖励可能作为普通掉落重发](<health_audit_20260920_round10_subplot_stars.md>) |
| `A30` | [收集类三星条件未检查，胜利即视为达成](<health_audit_20260920_round10_subplot_stars.md>) |
| `A105` | [重复结算请求返回旧缓存，客户端成绩与次数回退](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
| `A162` | [扫荡未传递队伍和连携信息，编队与成长统计丢失](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| `A369` | [战斗动态构建失败后套用其他角色旧素材，仍返回开战成功](<health_audit_20261015_round170_binary_template_overrides.md>) |
| `A372` | [主线通关记录被旧支线记录覆盖，星级和完成状态回退](<health_audit_20261017_round173_weekly_stage_restore.md>) |

<a id="m05"></a>

## 05 角色养成与权钥

| 编号 | 问题标题与报告 |
| --- | --- |
| `A31` | [一键升级接受更低目标等级，角色降级并返还历史经验](<health_audit_20260920_round11_hero.md>) |
| `A32` | [角色突破未检查角色等级和账号等级](<health_audit_20260920_round11_hero.md>) |
| `A33` | [技能升级被服务端截断，客户端仍按原次数加等级](<health_audit_20260920_round12_skills.md>) |
| `A34` | [未知技能编号可被直接写入并升级](<health_audit_20260920_round12_skills.md>) |
| `A35` | [普通材料也能作为权钥经验材料使用](<health_audit_20260920_round13_weapon.md>) |
| `A36` | [目标角色不存在时仍扣除权钥材料和金币](<health_audit_20260920_round13_weapon.md>) |
| `A37` | [权钥经验不足仍可突破，甚至连续跨越阶段](<health_audit_20260920_round14_weapon_progress.md>) |
| `A57` | [已有但未解锁的角色被当作重复角色，只给碎片](<health_audit_20260921_round31_hero_conversion.md>) |
| `A58` | [同时获得新角色和重复角色时，奖励列表只剩碎片](<health_audit_20260921_round31_hero_conversion.md>) |
| `A158` | [战斗经验增加却不换算角色等级，突破上限也未限制](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| `A159` | [账号等级经验曲线与客户端不同，等级和升级事件偏差](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| `A366` | [战斗角色未解析存档 JSON，技能和神格仍采用旧模板](<health_audit_20261015_round170_binary_template_overrides.md>) |
| `A367` | [角色强化或跃迁明确清空后，旧模板数据仍被保留](<health_audit_20261015_round170_binary_template_overrides.md>) |
| `A368` | [取消最后收藏或清空碎片后，登录恢复旧收藏和旧碎片](<health_audit_20261015_round170_binary_template_overrides.md>) |

<a id="m06"></a>

## 06 钥从唤名、消耗与实例同步

| 编号 | 问题标题与报告 |
| --- | --- |
| `A11` | [钥从唤名接受无效模板和错误材料](<health_audit_20260920_round3.md>) |
| `A12` | [钥从唤名可消耗锁定或佩戴中的材料，角色留下失效引用](<health_audit_20260920_round3.md>) |
| `A38` | [钥从消耗已回滚，后续刷新仍带有删除通知](<health_audit_20260921_round15_rollback.md>) |
| `A63` | [钥从和刻印已入库，在线客户端与图鉴未刷新](<health_audit_20260922_round39_servant_notify.md>) |
| `A363` | [钥从精炼、锁定和吞噬引用未定义事件，后续联动不执行](<health_audit_20261015_round165_broadcast_contracts.md>) |

<a id="m07"></a>

## 07 刻印装配、突破、重构与赋能

| 编号 | 问题标题与报告 |
| --- | --- |
| `A39` | [刻印消耗已回滚，失败回包仍通知客户端删除实例](<health_audit_20260921_round16_equip_rollback.md>) |
| `A40` | [刻印可佩戴到不存在的角色，原角色被卸下](<health_audit_20260921_round17_ownership.md>) |
| `A41` | [同一枚刻印能占用同一角色的多个槽位](<health_audit_20260921_round17_ownership.md>) |
| `A42` | [刻印突破费用配置缺失时仍能免费突破](<health_audit_20260921_round18_equip_progress.md>) |
| `A43` | [刻印无经验投入也能通过一键操作突破](<health_audit_20260921_round18_equip_progress.md>) |
| `A44` | [重构重复确认时，原有神系可能被清空](<health_audit_20260921_round19_preview.md>) |
| `A45` | [赋能预览编号越界时，默认消耗第一项结果](<health_audit_20260921_round19_preview.md>) |

<a id="m08"></a>

## 08 任务、战令、签到与周期刷新

| 编号 | 问题标题与报告 |
| --- | --- |
| `A08` | [跨日处理失败仍记为已刷新，当天无法重试](<health_audit_20260920_round2.md>) |
| `A46` | [任务一键领取未检查永久领史，已领任务可能再次发奖](<health_audit_20260921_round20_tasks.md>) |
| `A47` | [一次活跃度奖励触发两次相同事件](<health_audit_20260921_round21_task_inventory.md>) |
| `A48` | [战令经验只到账一次，周累计经验却增加两次](<health_audit_20260921_round21_task_inventory.md>) |
| `A49` | [一次活跃事件匹配多个条件，任务进度重复增长](<health_audit_20260921_round22_activity_consumer.md>) |
| `A50` | [补建任务缺少周期类型，已领状态跨日或跨周不清除](<health_audit_20260921_round23_task_cycle.md>) |
| `A51` | [日常和周常已重置，客户端却未收到刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
| `A141` | [时迹馈赠每天重新打开已确认状态，无奖励也弹首次提示](<health_audit_20261008_round95_accumulate_login_popup.md>) |
| `A160` | [战斗熟练度广播串入交互任务，未交互也增加进度](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| `A248` | [新手签到同日重复推进天数，结束后仍继续发默认奖励](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A249` | [新手等级奖励未检查等级和领史，未知档位也能领](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A250` | [新手累计任务奖未检查任务完成状态，可重复领奖](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A251` | [回归签到保存错误编号，重复或越界天数也能发奖](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A252` | [回归活动期限和额度未保存，每次登录重新获得期限](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A253` | [回归资源找回把次数写成时间戳，领取机会无法正确扣减](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A254` | [新手提示标记未清除，战令新标记读取错误字段](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A256` | [已有一个新手任务就跳过补建，后续阶段任务缺失](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| `A257` | [指定关卡任务监听错误事件，通关后条件 401 不推进](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| `A258` | [回归任务已领奖但找回机会未保存，重新登录丢失权益](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| `A364` | [登录先记跨日完成再更新任务，后续失败后同日无法补算](<health_audit_20261015_round166_task_login_broadcasts.md>) |
| `A365` | [指定商店购买事件丢失店号，相关任务不增长](<health_audit_20261015_round168_shop_pay_broadcasts.md>) |
| `A370` | [任务恢复固定返回旧阶段，后续阶段和有效任务不显示](<health_audit_20261017_round172_common_restore_sources.md>) |
| `A371` | [时间同步包按四点重置，任务与签到实际按五点刷新](<health_audit_20261017_round172_common_restore_sources.md>) |

<a id="m09"></a>

## 09 抽卡、卡池与角色解锁

| 编号 | 问题标题与报告 |
| --- | --- |
| `A61` | [抽卡发奖失败仍扣券、重置保底并返回成功](<health_audit_20260921_round34_draw_settlement.md>) |
| `A62` | [保存卡池进度时，将另一卡池的自选目标写入当前池](<health_audit_20260922_round35_draw_inheritance.md>) |
| `A64` | [抽到角色后缺少解锁刷新，阵营收集进度也不更新](<health_audit_20260922_round40_hero_notify.md>) |

<a id="m10"></a>

## 10 编队、管理喵与芯片

| 编号 | 问题标题与报告 |
| --- | --- |
| `A65` | [应用不存在的芯片预设会清空当前装配](<health_audit_20260922_round42_chip_proposals.md>) |
| `A66` | [应用芯片预设可绕过解锁、槽位和重复检查](<health_audit_20260922_round42_chip_proposals.md>) |
| `A67` | [角色 AI 芯片可装给错误角色或错误槽位](<health_audit_20260922_round43_hero_chip_binding.md>) |
| `A68` | [助战芯片未满足前置条件也能解锁](<health_audit_20260922_round44_reviser_unlock.md>) |
| `A69` | [未知角色芯片编号可免费创建并下发](<health_audit_20260922_round45_hero_chip_unlock.md>) |
| `A71` | [芯片解锁或装卸重复增加成就进度](<health_audit_20260922_round47_chip_closeout.md>) |
| `A72` | [编队保存和恢复读取不同数据表，重登恢复旧队伍](<health_audit_20260922_round48_reserve_roundtrip.md>) |
| `A73` | [同名协议结构混用，编队保存与出战信息解析错误](<health_audit_20260928_round49_reserve_protocol.md>) |
| `A74` | [编队请求缺少客户端等待的回复，操作一直等待](<health_audit_20260928_round49_reserve_protocol.md>) |
| `A75` | [助战编队字段错位，保存时丢失扩展信息](<health_audit_20260928_round50_assist_fields.md>) |
| `A76` | [恢复编队时过滤空位，后排角色被前移](<health_audit_20260928_round51_reserve_slots.md>) |
| `A77` | [编队重置只返回成功，实际存档未清除](<health_audit_20260928_round52_reserve_reset.md>) |
| `A78` | [多队编队附加信息未保存，登录无法恢复](<health_audit_20260928_round53_multichess_closeout.md>) |

<a id="m11"></a>

## 11 梦境与黑区周常

| 编号 | 问题标题与报告 |
| --- | --- |
| `A79` | [梦境未达标、已领取或未知档位仍能发奖](<health_audit_20260928_round54_weekly_claim.md>) |
| `A80` | [梦境一键领奖查询失败后，仍按未领取发奖](<health_audit_20260928_round55_weekly_claim_interop.md>) |
| `A82` | [梦境首领匹配失败时仍登记成绩](<health_audit_20261002_round57_weekly_settlement.md>) |
| `A83` | [梦境已领奖后仍能重置，成绩与英雄锁定被清除](<health_audit_20261002_round58_weekly_reset_state.md>) |
| `A84` | [梦境可使用已被其他首领锁定的角色](<health_audit_20261002_round59_weekly_team_lock.md>) |
| `A85` | [梦境首领周三轮换，成绩和领史却到周四才清理](<health_audit_20261002_round60_weekly_cycle.md>) |
| `A86` | [梦境旧战斗跨周结算，写入新周期成绩与角色锁定](<health_audit_20261002_round61_weekly_old_battle.md>) |
| `A87` | [梦境周刷新访问不存在的旧表，后续清理中断](<health_audit_20261003_round62_weekly_refresh_paths.md>) |
| `A88` | [黑区第一队完成后，未进入第二队待战状态](<health_audit_20261003_round64_mythic_relay.md>) |
| `A89` | [黑区切换难度保留旧成绩，不同难度进度混合](<health_audit_20261003_round65_mythic_transition.md>) |
| `A90` | [黑区半周刷新按固定 3.5 天倒推，旧数据漏清或新进度被清](<health_audit_20261003_round67_mythic_cycle.md>) |
| `A91` | [黑区星级奖励缺少达标与已领检查](<health_audit_20261003_round68_mythic_normal.md>) |
| `A92` | [黑区星级列表固定满星，与实际成绩不符](<health_audit_20261003_round68_mythic_normal.md>) |
| `A373` | [周常数据读取失败被当作空状态，成绩、英雄和徽章显示丢失](<health_audit_20261017_round173_weekly_stage_restore.md>) |

<a id="m12"></a>

## 12 迭代校验与多重心魔

| 编号 | 问题标题与报告 |
| --- | --- |
| `A93` | [Mode4 领奖未检查完成状态，重复或未知任务也能发奖](<health_audit_20261007_round70_core_mode4_claim.md>) |
| `A94` | [Mode4 恢复遗漏队伍和挑战关，第二队编号也会丢失](<health_audit_20261007_round71_core_mode4_progress.md>) |
| `A95` | [Mode4 任务通关后仍不显示可领奖](<health_audit_20261007_round71_core_mode4_progress.md>) |
| `A96` | [Mode4 挑战关类型未识别，结算遗漏专属记录和刷新](<health_audit_20261007_round72_core_mode4_lifecycle.md>) |
| `A97` | [Mode4 单队重置只返回成功，持久进度没有变化](<health_audit_20261007_round72_core_mode4_lifecycle.md>) |
| `A98` | [Mode4 结算缺少成绩刷新，客户端仍显示旧分数](<health_audit_20261007_round72_core_mode4_lifecycle.md>) |
| `A99` | [Mode4 重置后旧战斗仍可结算，已清成绩重新出现](<health_audit_20261007_round73_core_mode4_old_settlement.md>) |
| `A100` | [迭代校验实际轮换时间与客户端刷新时间不一致](<health_audit_20261007_round74_core_regular_cycle.md>) |
| `A101` | [迭代校验轮次编号循环复用，旧成绩和领史可能重新生效](<health_audit_20261007_round74_core_regular_cycle.md>) |
| `A102` | [迭代校验未知任务也能获得默认奖励](<health_audit_20261007_round75_core_regular_claim_reset.md>) |
| `A103` | [迭代校验可用当前轮进度领取其他轮次任务](<health_audit_20261007_round75_core_regular_claim_reset.md>) |
| `A104` | [迭代校验旧战斗写入新轮次，影响新周期成绩和领奖](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
| `A106` | [迭代校验成绩刷新包结构错误，分数编码为空](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
| `A107` | [Mode4 已关闭活动仍能结算，记录与广播归属不一致](<health_audit_20261007_round78_core_mode4_activity.md>) |
| `A108` | [Mode4 重置后的即时最高分与重登结果不同](<health_audit_20261007_round79_core_score_handoff.md>) |
| `A374` | [Mode3 恢复使用错误活动编号，退回 Mode1 关卡和零分](<health_audit_20261017_round174_dream_core_restore.md>) |
| `A377` | [多重心魔阶段编号错误，挑战期的开战按钮被挡住](<health_audit_20261017_round180_multheartdemon.md>) |
| `A378` | [多重心魔保存、重置和结算缺少处理，返回成功却无玩法状态](<health_audit_20261017_round180_multheartdemon.md>) |
| `A379` | [多重心魔奖励任务没有进度事件，正常游玩也不增长](<health_audit_20261017_round180_multheartdemon.md>) |
| `A380` | [Mode1/2 首次保存的挑战词缀读不到，通关又覆盖原设置](<health_audit_20261017_round181_core123_closure.md>) |
| `A381` | [Mode3 成绩摘要缺少当前分、最高分和用时字段](<health_audit_20261017_round181_core123_closure.md>) |

<a id="m13"></a>

## 13 自走棋

| 编号 | 问题标题与报告 |
| --- | --- |
| `A109` | [自走棋读取失败覆盖旧局，保存失败仍返回开局成功](<health_audit_20261007_round80_autochess_entry.md>) |
| `A110` | [自走棋商店未按活动限制卡池，未知活动退回全量卡池](<health_audit_20261007_round81_autochess_shop.md>) |
| `A111` | [自走棋免费刷新次数恢复丢失，新一局又继承旧次数](<health_audit_20261007_round81_autochess_shop.md>) |
| `A112` | [自走棋把棋子经验当等级，售价和技能档位错误](<health_audit_20261007_round82_autochess_buy_merge.md>) |
| `A113` | [自走棋梦影购买次数未恢复，重复购买不累计成长](<health_audit_20261007_round82_autochess_buy_merge.md>) |
| `A114` | [自走棋布阵缺少容量和位置检查，超额棋子也能入战](<health_audit_20261007_round83_autochess_formation.md>) |
| `A115` | [自走棋失败结果未保存，重建对局后被默认判胜](<health_audit_20261007_round84_autochess_round_restore.md>) |
| `A116` | [自走棋通关徽章映射错误，重复通关还可能降低徽章等级](<health_audit_20261007_round85_autochess_clear_medal.md>) |
| `A117` | [自走棋重复通关重复触发首次通关事件](<health_audit_20261007_round85_autochess_clear_medal.md>) |
| `A118` | [自走棋配置积分未发放，补入库存后也不推进任务](<health_audit_20261007_round85_autochess_clear_medal.md>) |
| `A119` | [自走棋回放返回成功，却缺少录像和行动数据](<health_audit_20261007_round86_autochess_cancel_replay.md>) |
| `A120` | [自走棋平局结果编号错误，双方退场还可能按原人数判胜](<health_audit_20261008_round87_autochess_draw_limit.md>) |
| `A121` | [自走棋低血量平局误入终败，结算状态前后不一致](<health_audit_20261008_round87_autochess_draw_limit.md>) |
| `A122` | [自走棋行动次数耗尽后默认判胜，可能直接记为通关](<health_audit_20261008_round87_autochess_draw_limit.md>) |
| `A123` | [自走棋技能触发时机、目标和参数与配置不一致](<health_audit_20261008_round88_autochess_skills.md>) |
| `A124` | [自走棋战斗中的永久成长未保存，下一轮属性恢复原值](<health_audit_20261008_round88_autochess_skills.md>) |
| `A125` | [自走棋召唤物被技能击败后仍能继续攻击](<health_audit_20261008_round88_autochess_skills.md>) |
| `A126` | [自走棋按描述关键词套技能，护盾和召唤对象错误](<health_audit_20261008_round89_autochess_death_shield.md>) |
| `A127` | [自走棋护盾消耗未通知移除，客户端残留旧护盾](<health_audit_20261008_round89_autochess_death_shield.md>) |
| `A128` | [自走棋多个召唤物使用同一位置，画面站位覆盖](<health_audit_20261008_round89_autochess_death_shield.md>) |
| `A129` | [自走棋困难关已通关，馆主任务仍不增长](<health_audit_20261008_round90_autochess_tasks_retry.md>) |
| `A130` | [自走棋终败重试清空整局，未恢复上一准备阶段](<health_audit_20261008_round90_autochess_tasks_retry.md>) |
| `A131` | [自走棋普通结算保留旧余额，金币结转可能多算](<health_audit_20261008_round90_autochess_tasks_retry.md>) |
| `A132` | [自走棋关卡初始等级未应用，购买棋子仍从一级开始](<health_audit_20261008_round91_autochess_brahma_props.md>) |
| `A133` | [梵天自走棋特殊阶段未触发，普通胜场直接结束对局](<health_audit_20261008_round91_autochess_brahma_props.md>) |
| `A134` | [自走棋局内道具不生成，使用时又被当棋子购买合成](<health_audit_20261008_round91_autochess_brahma_props.md>) |
| `A135` | [自走棋任务进度已保存，任务刷新通知却未发送](<health_audit_20261008_round92_autochess_core_recovery.md>) |
| `A136` | [自走棋操作回滚后内存缓存未恢复，在线状态与存档不同](<health_audit_20261008_round92_autochess_core_recovery.md>) |

<a id="m14"></a>

## 14 成就、图鉴、插画与收集

| 编号 | 问题标题与报告 |
| --- | --- |
| `A137` | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| `A138` | [成就部分未达标仍整体返回成功，客户端把未领项也标成已领](<health_audit_20261008_round93_achievement_claim.md>) |
| `A139` | [成就沿用旧门槛，造成提前领奖或达标后无法领取](<health_audit_20261008_round94_achievement_threshold_recovery.md>) |
| `A140` | [成就补算失败后使用旧数据，未读内容可能被标成已读](<health_audit_20261008_round94_achievement_threshold_recovery.md>) |
| `A143` | [成就按零点计日，签到按五点计日，同一天累计口径不同](<health_audit_20261008_round96_login_achievement_cycle.md>) |
| `A144` | [战斗成就使用错误统计，缺少必要数据也能达标](<health_audit_20261008_round97_stage_achievement_contract.md>) |
| `A145` | [介质攫取通关未接入成就事件，相关成就一直为零](<health_audit_20261008_round97_stage_achievement_contract.md>) |
| `A146` | [战报解析丢失生命、输出和治疗统计，成就判断错误](<health_audit_20261008_round98_battle_achievement_statistics.md>) |
| `A147` | [单角色通关事件没有生产，孤狼成就不增长](<health_audit_20261008_round98_battle_achievement_statistics.md>) |
| `A148` | [成就物语保存失败，客户端仍标已读并清除红点](<health_audit_20261009_round99_achievement_story_read.md>) |
| `A149` | [插画局部领奖额外领取其他项目，客户端只刷新请求项](<health_audit_20261009_round100_illustration_claim_view.md>) |
| `A151` | [阵营收集缺少当前奖励档位，领取成功却没有奖励](<health_audit_20261009_round102_hero_race_collect.md>) |
| `A152` | [清空过场插画选择后，重登恢复为固定旧插画](<health_audit_20261009_round103_loading_picture_settings.md>) |
| `A154` | [世界观词条缺少常规获取入口，关卡和剧情不增加收集](<health_audit_20261009_round107_worldview_intelligence.md>) |

<a id="m15"></a>

## 15 档案、好感、交心、连携与誓约

| 编号 | 问题标题与报告 |
| --- | --- |
| `A150` | [档案心链保存失败，客户端已读状态未撤销](<health_audit_20261009_round101_archive_illustration_recovery.md>) |
| `A155` | [档案赠礼接受非礼物道具，货币也能被消耗换好感](<health_audit_20261009_round108_archive_gift_contract.md>) |
| `A156` | [异格角色赠礼后刷新成另一机体，角色列表与存档不一致](<health_audit_20261009_round108_archive_gift_contract.md>) |
| `A157` | [战斗成长依赖的表和列缺失，部分成长被跳过](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| `A161` | [战斗后交心心情已增加，在线客户端没有刷新](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| `A163` | [战斗成长只保存一部分仍返回成功，同场重试不补缺项](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| `A164` | [交心突破先推新等级再触发客户端加级，在线显示多一级](<health_audit_20261009_round110_trust_growth_contract.md>) |
| `A165` | [交心赠礼数量截断错误，一次送满仍差一件](<health_audit_20261009_round110_trust_growth_contract.md>) |
| `A166` | [交心解锁和突破未检查角色、档案与消耗条件](<health_audit_20261009_round110_trust_growth_contract.md>) |
| `A167` | [交心置换可空成本领取，重复目标还能绕过总量限制](<health_audit_20261009_round110_trust_growth_contract.md>) |
| `A168` | [二阶赠礼接受非礼物道具，普通货币也能增加信赖](<health_audit_20261009_round110_trust_growth_contract.md>) |
| `A169` | [连携奥义使用次数没有记录，三级条件无法自然完成](<health_audit_20261009_round111_combo_relation_feedback.md>) |
| `A170` | [连携条件忽略难度参数，普通首领也计入高难任务](<health_audit_20261009_round111_combo_relation_feedback.md>) |
| `A171` | [连携条件进度已增长，客户端没有即时刷新](<health_audit_20261009_round111_combo_relation_feedback.md>) |
| `A172` | [同队关系网先推新熟练度再回调累加，在线显示重复增长](<health_audit_20261009_round111_combo_relation_feedback.md>) |
| `A173` | [誓约未检查角色拥有、好感与交心条件](<health_audit_20261009_round112_oath_state_contract.md>) |
| `A174` | [誓约任务可直接补满未来阶段，并重复触发完成事件](<health_audit_20261009_round112_oath_state_contract.md>) |
| `A175` | [誓约把角色等级当熟练度，任务可能提前完成](<health_audit_20261009_round112_oath_state_contract.md>) |
| `A176` | [誓约宿舍条件在空房或读取失败时也判完成](<health_audit_20261009_round112_oath_state_contract.md>) |
| `A177` | [誓约任务不读取已有主线通关记录](<health_audit_20261009_round112_oath_state_contract.md>) |
| `A178` | [助理陪伴天数没有生产事件，誓约相关任务不增长](<health_audit_20261009_round112_oath_state_contract.md>) |
| `A179` | [誓约先返回升级成功再刷新等级，回调读到旧值](<health_audit_20261009_round112_oath_state_contract.md>) |
| `A180` | [一次大厅交互被两个模块重复计入累计次数](<health_audit_20261009_round113_oath_producer_feedback.md>) |
| `A184` | [誓约照片协议分流错误，空照片请求可能变成扣戒指缔约](<health_audit_20261009_round114_oath_tail_contract.md>) |
| `A185` | [誓约物语未检查婚约、等级和角色归属，未知故事也记已读](<health_audit_20261009_round114_oath_tail_contract.md>) |
| `A186` | [誓约升级未发放配置中的装扮，相关头像框无法选择](<health_audit_20261009_round114_oath_tail_contract.md>) |
| `A187` | [誓约等级不足、物语未读时，插画仍提前解锁](<health_audit_20261009_round114_oath_tail_contract.md>) |
| `A188` | [超级心链阅读记录字段不一致，重登反锁已读插画](<health_audit_20261009_round115_super_heart_relation_story.md>) |
| `A189` | [关系剧情新增配置未同步，服务端退回固定旧故事](<health_audit_20261009_round115_super_heart_relation_story.md>) |
| `A190` | [关系剧情缺少资格检查，无故事或无角色也能领奖](<health_audit_20261009_round115_super_heart_relation_story.md>) |
| `A193` | [教学关通关未刷新教学状态，交心条件可能继续显示旧进度](<health_audit_20261009_round117_archive_lifecycle.md>) |
| `A194` | [旧交心开启回调把已有信赖等级和经验重置为默认值](<health_audit_20261009_round117_archive_lifecycle.md>) |

<a id="m16"></a>

## 16 后宅、房间、食堂与猫咪探索

| 编号 | 问题标题与报告 |
| --- | --- |
| `A182` | [异格角色家具按本体保存，誓约家具任务识别不到](<health_audit_20261009_round113_oath_producer_feedback.md>) |
| `A183` | [后宅家具重复赠送或空赠送也能返回成功](<health_audit_20261009_round113_oath_producer_feedback.md>) |
| `A191` | [后宅进餐按错误机体增加信赖，异格角色成长丢失](<health_audit_20261009_round116_dorm_feed_anecdote.md>) |
| `A192` | [后宅进餐只刷新疲劳，信赖经验未同步到客户端](<health_audit_20261009_round116_dorm_feed_anecdote.md>) |
| `A195` | [食堂更换岗位未移除旧岗位，同一角色同时占两岗](<health_audit_20261010_round118_backhome_identity.md>) |
| `A196` | [食堂撤岗后 NPC 仅在线恢复，重登岗位信息丢失](<health_audit_20261010_round118_backhome_identity.md>) |
| `A197` | [食堂委托把分钟当秒，人员过早释放并恢复疲劳](<health_audit_20261010_round118_backhome_identity.md>) |
| `A198` | [岗位派遣缺少角色资格与排他检查，同一角色可重复占用](<health_audit_20261010_round118_backhome_identity.md>) |
| `A199` | [安排岗位被误计为完成委托，重试还能重复增加成就](<health_audit_20261010_round118_backhome_identity.md>) |
| `A200` | [食堂售菜未正确扣除食材，扣料失败也继续获得收入](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A201` | [食堂 NPC 不产收益，手动营业期间仍执行自动售卖](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A202` | [食堂重新上架未清旧销量和收入，在线与重登状态不同](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A203` | [食堂手动收益写入错误货币，客户端也未收到资产刷新](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A204` | [食堂销量已保存但待领取收益缺失，领取结果为零](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A205` | [食堂手动领奖调用错误模块，成功回调无法完成](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A206` | [委托领奖后没有结束旧领取资格，同一槽位可再次发奖](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A207` | [委托食材已到账，客户端背包没有刷新](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A208` | [后宅工作状态切换未推进计时，疲劳恢复重复累计](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| `A209` | [委托领奖后未通知人员归还，在线入住状态不同步](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| `A210` | [食堂委托派遣与领奖均未扣除配置疲劳成本](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| `A211` | [刷新或重复解锁委托会清掉进行中任务，同日还能多次刷新](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| `A212` | [食堂设施未检查升级条件，满级仍成功并让客户端再升一级](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| `A213` | [食堂设施容量未接入计算，存储上限与客户端不一致](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| `A214` | [食堂计时广播参数不匹配，自动制作被跳过](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| `A215` | [食堂未检查菜谱解锁条件，未知菜品也能上架赚钱](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| `A216` | [食堂设施升级已扣费，收益技能却未生效](<health_audit_20261010_round121_canteen_skill_cycle.md>) |
| `A217` | [食堂未检查上架资格和槽位容量，超额菜品也能营业](<health_audit_20261010_round121_canteen_skill_cycle.md>) |
| `A218` | [食堂手动收益的取整与技能加成计算不一致](<health_audit_20261010_round121_canteen_skill_cycle.md>) |
| `A219` | [食堂手动场次未关闭，旧营业报告可重复领取收益](<health_audit_20261010_round121_canteen_skill_cycle.md>) |
| `A220` | [食堂人员技能忽略等级门槛，低等级也能启用高阶技能](<health_audit_20261010_round121_canteen_skill_cycle.md>) |
| `A221` | [食堂委托大成功判定未计算人员技能加成](<health_audit_20261010_round121_canteen_skill_cycle.md>) |
| `A222` | [房间布局被服务端裁减，仍按完整布局返回成功](<health_audit_20261010_round122_dorm_layout_lifecycle.md>) |
| `A223` | [房间余额不足仍能解锁，重复解锁又扣费并清成长](<health_audit_20261010_round122_dorm_layout_lifecycle.md>) |
| `A224` | [读取家具模板失败时，以空布局覆盖原模板并返回成功](<health_audit_20261010_round122_dorm_layout_lifecycle.md>) |
| `A225` | [公共大厅人员名单保存不完整，清空后重登又出现旧角色](<health_audit_20261010_round122_dorm_layout_lifecycle.md>) |
| `A226` | [私人房间召回删掉房间归属，大厅里的角色却没撤走](<health_audit_20261010_round122_dorm_layout_lifecycle.md>) |
| `A227` | [访问大厅时赠礼写入大厅房间，未计入角色私人房间](<health_audit_20261010_round123_dorm_gift_qualification.md>) |
| `A228` | [房间经验、等级和疲劳规则与客户端计算不同](<health_audit_20261010_round123_dorm_gift_qualification.md>) |
| `A229` | [家具摆放未检查赠送对象、房间资格及容量](<health_audit_20261010_round123_dorm_gift_qualification.md>) |
| `A230` | [房间入住未检查角色解锁、好感与人数上限](<health_audit_20261010_round123_dorm_gift_qualification.md>) |
| `A231` | [房门批量改位未检查最终冲突，零位置和越界楼层也能保存](<health_audit_20261010_round123_dorm_gift_qualification.md>) |
| `A232` | [猫咪探索普通结束请求强制完成，未到期也能领满奖](<health_audit_20261010_round124_cat_explore_lifecycle.md>) |
| `A233` | [猫咪探索按零点计日，与五点游戏日和周重置不一致](<health_audit_20261010_round124_cat_explore_lifecycle.md>) |
| `A234` | [猫咪周宝箱未检查领取日期，周中也能领取](<health_audit_20261010_round124_cat_explore_lifecycle.md>) |
| `A235` | [猫咪探索生成不存在的日志编号，客户端找不到文本](<health_audit_20261010_round124_cat_explore_lifecycle.md>) |
| `A236` | [猫咪领奖和培养只改存档，资产与等级刷新缺失](<health_audit_20261010_round124_cat_explore_lifecycle.md>) |
| `A237` | [猫咪培养未检查技能归属，错误技能编号仍能扣费升级](<health_audit_20261010_round125_cat_cultivation_reward.md>) |
| `A238` | [猫咪解锁忽略等级门槛，一级也能开启二级猫咪](<health_audit_20261010_round125_cat_cultivation_reward.md>) |
| `A239` | [猫咪收益加成作用到错误资源，经验与代币互相串用](<health_audit_20261010_round125_cat_cultivation_reward.md>) |
| `A240` | [猫咪收益浮点取整误差，奖励比应得数多一](<health_audit_20261010_round125_cat_cultivation_reward.md>) |
| `A241` | [猫咪日志显示额外奖励，最终领取却没有发放](<health_audit_20261010_round125_cat_cultivation_reward.md>) |

<a id="m17"></a>

## 17 活动、礼包、商店、月卡与充值

| 编号 | 问题标题与报告 |
| --- | --- |
| `A01` | [商店按客户端上报金额扣费，可能绕过配置价格](<health_audit_20260920.md>) |
| `A242` | [周期礼包扣费成功但订阅或首日邮件缺失](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| `A243` | [周期礼包补发后进度保存失败，重试重复寄出邮件](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| `A244` | [周期礼包次日邮件已生成，在线未读和红点未刷新](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| `A245` | [活动领奖入口覆盖配置，奖励内容和领取归属错误](<health_audit_20261010_round127_activity_point_claim_contract.md>) |
| `A246` | [活动领奖未检查积分与奖励目录，零积分或未知编号也能领](<health_audit_20261010_round127_activity_point_claim_contract.md>) |
| `A255` | [新手首充和月卡签到部分分支缺少资格及同日领取保护](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| `A259` | [月卡累计签到天数没有生产，持续签到也不推进新手任务](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| `A260` | [月卡签到回包补入默认状态，在线已签到标记被覆盖](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| `A261` | [普通月卡缺行或过期仍生成未来期限，并允许领取日奖](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| `A262` | [月卡购买后只更新签到标记，客户端期限未刷新](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| `A263` | [大月卡购买未扣除花币，零余额也能激活](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| `A264` | [大月卡复购未重置奖励池，新周期继承旧材料状态](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| `A265` | [大月卡未激活或已过期仍能签到领日奖](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| `A266` | [大月卡按零点而非五点重置，同一游戏日可能领两次](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| `A267` | [大月卡已日签时直接返回，漏发累计天数奖励](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| `A268` | [PAY 直购把不同资产槽位混算，花币余额被错误覆盖](<health_audit_20261013_round131_ios229_pay_delivery.md>) |
| `A269` | [PAY 首充福利写入失败，主商品和首购记录却已提交](<health_audit_20261013_round131_ios229_pay_delivery.md>) |
| `A270` | [战令购买允许错误升级、重复附赠和等级回退](<health_audit_20261013_round131_ios229_pay_delivery.md>) |
| `A271` | [累充领奖成功但回复包为空，客户端收不到结果和奖励](<health_audit_20261013_round132_recharge_claim_contract.md>) |
| `A272` | [限时累充套用常驻或旧版本配置，门槛与奖励错位](<health_audit_20261013_round132_recharge_claim_contract.md>) |
| `A273` | [累充未达标项也返回成功，客户端把未领取档位标成已领](<health_audit_20261013_round132_recharge_claim_contract.md>) |
| `A275` | [商店未核对货架与店铺归属，未上架或跨店商品也能买](<health_audit_20261013_round134_common_shop_purchase.md>) |
| `A276` | [永久限购按一天后过期处理，旧商品可再次兑换](<health_audit_20261013_round134_common_shop_purchase.md>) |
| `A277` | [商店月限购用固定 31 天计算，下次刷新日期偏移](<health_audit_20261013_round135_shop_cycle_eligibility.md>) |
| `A278` | [花币未按平台槽位聚合，余额足够仍提示不足](<health_audit_20261013_round135_shop_cycle_eligibility.md>) |
| `A279` | [商店发放优先匹配错误资产类型，角色或头像框被记成皮肤](<health_audit_20261014_round136_shop_special_delivery.md>) |

<a id="m18"></a>

## 18 换装、场景、看板与个人资料

| 编号 | 问题标题与报告 |
| --- | --- |
| `A60` | [表情重复拥有判断使用错误编号，重复折算可能漏算](<health_audit_20260921_round33_unique_assets.md>) |
| `A153` | [大厅音乐和资料选择保存失败，仍回显新值](<health_audit_20261009_round104_lobby_bgm_recovery.md>) |
| `A181` | [切换看板娘前未结算旧时长，陪伴时间计到新角色](<health_audit_20261009_round113_oath_producer_feedback.md>) |
| `A274` | [累充和战令装扮被当普通材料发放，名片里无法使用](<health_audit_20261013_round133_recharge_special_assets.md>) |
| `A282` | [限时场景的有效期写入与选择规则](<health_audit_20261014_round137_profile_scene_skin.md>) |
| `A283` | [看板轮换接受未拥有角色和场景，后续交互记到错误角色](<health_audit_20261014_round137_profile_scene_skin.md>) |
| `A284` | [随机展示单项设置已保存，重登却读取旧整包设置](<health_audit_20261014_round138_profile_random_decorations.md>) |
| `A285` | [随机展示可保存未拥有或未知的换装场景，以及无效参数](<health_audit_20261014_round138_profile_random_decorations.md>) |
| `A286` | [名片展示只检查角色记录存在，未解锁角色也能放入](<health_audit_20261014_round138_profile_random_decorations.md>) |
| `A287` | [清空名片角色或标签后，旧登录模板让它们重新出现](<health_audit_20261014_round138_profile_random_decorations.md>) |
| `A288` | [贴纸画册未正确计算占用，同一资产重复使用或被意外卸下](<health_audit_20261014_round139_sticker_layout.md>) |
| `A289` | [贴纸画册接受未拥有资产、错误层级和无效页面](<health_audit_20261014_round139_sticker_layout.md>) |
| `A290` | [部分解锁套装生成稀疏层级，后续已拥有贴纸也不显示](<health_audit_20261014_round139_sticker_layout.md>) |
| `A291` | [前景贴纸写入背景资产表，获得后重登仍未解锁](<health_audit_20261014_round139_sticker_layout.md>) |
| `A292` | [套装未集齐或编号不存在也能领取集齐奖励](<health_audit_20261014_round139_sticker_layout.md>) |
| `A293` | [套装奖励入账失败仍记已领取，恢复后重试也不补发](<health_audit_20261014_round139_sticker_layout.md>) |
| `A294` | [资料详情返回固定空画册和生日，与已保存资料不同](<health_audit_20261014_round139_sticker_layout.md>) |
| `A295` | [未设置生日被恢复成默认日期，设置入口和红点消失](<health_audit_20261014_round140_profile_gift_birthday.md>) |
| `A296` | [生日只检查月日范围，二月三十日等无效日期也能保存](<health_audit_20261014_round140_profile_gift_birthday.md>) |
| `A297` | [触摸日计数和在线时长计时不一致，签到还会清掉当天次数](<health_audit_20261014_round140_profile_gift_birthday.md>) |
| `A298` | [面板清空签名后旧字段又恢复，页面还会补入示例文字](<health_audit_20261014_round141_profile_basic.md>) |
| `A299` | [纯白大厅接受无效选项，客户端按错误编号恢复设置](<health_audit_20261014_round142_lobby_settings.md>) |
| `A300` | [大厅桌面接受残缺、重复或无效组件，恢复布局缺少数据](<health_audit_20261014_round142_lobby_settings.md>) |
| `A301` | [特殊场景镜头方向已保存，但登录没有读取并下发](<health_audit_20261014_round142_lobby_settings.md>) |
| `A302` | [特殊场景未检查拥有状态、镜头配置与方向范围](<health_audit_20261014_round142_lobby_settings.md>) |

<a id="m19"></a>

## 19 AI 对话、人格、问候与配置

| 编号 | 问题标题与报告 |
| --- | --- |
| `A309` | [AI 一次玩家发言被保存两次，模型上下文出现重复消息](<health_audit_20261014_round148_ai_chat_entry.md>) |
| `A310` | [AI 没有人格时游戏入口仍回显成功，并推送未入库提示](<health_audit_20261014_round148_ai_chat_entry.md>) |
| `A311` | [清空 AI 历史后同秒发送的新消息，也被清空时间过滤](<health_audit_20261014_round149_ai_history_clear.md>) |
| `A312` | [AI 单角色清空未作用到全角色查询，旧消息重新显示](<health_audit_20261014_round149_ai_history_clear.md>) |
| `A313` | [AI 面板旧异步请求覆盖当前角色，清空后历史又出现](<health_audit_20261014_round149_ai_history_clear.md>) |
| `A314` | [清空 AI 历史未使旧生成和排队任务失效，旧回复重新写回](<health_audit_20261014_round149_ai_history_clear.md>) |
| `A315` | [同一 AI 角色的锁等待占满共享线程，其他角色也被堵住](<health_audit_20261014_round150_ai_queue_delivery.md>) |
| `A316` | [AI 后台任务提交或执行失败，前台仍显示成功且等不到回复](<health_audit_20261014_round150_ai_queue_delivery.md>) |
| `A317` | [AI 回复发送到已关闭连接，重连后没有补收](<health_audit_20261014_round150_ai_queue_delivery.md>) |
| `A318` | [清空个人 AI 人格后又回退到全局人格，继续生成回复](<health_audit_20261014_round151_ai_persona_context.md>) |
| `A319` | [AI 人格面板读取个人配置却保存全局配置，修改不生效](<health_audit_20261014_round151_ai_persona_context.md>) |
| `A320` | [AI 人格按展示机体查找，其他别名已有配置却显示为空](<health_audit_20261014_round151_ai_persona_context.md>) |
| `A321` | [AI 问候生成成功但邮件或防重记录失败，重试反复生成](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| `A322` | [AI 问候部分失败仍标记整批完成，漏发角色无法补发](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| `A323` | [AI 问候用旧配置整包覆盖，已清人格和历史边界重新出现](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| `A324` | [AI 角色别名未统一，静音不生效且同一角色重复问候](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| `A325` | [AI 首次并发创建出两个服务实例，同一问候重复进入生成](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| `A326` | [AI 接口对超时、鉴权和格式错误统一重试，可能重复生成](<health_audit_20261014_round153_ai_timeout_retry.md>) |
| `A327` | [AI 问候整批缺少总截止时间，排队与等待时间难以收束](<health_audit_20261014_round153_ai_timeout_retry.md>) |
| `A328` | [AI 问候附件未使用白名单与数量上限，重复条目可叠加超限](<health_audit_20261014_round154_ai_greeting_mail.md>) |
| `A329` | [AI 问候面板读取错误数量字段，零封或失败也显示成功](<health_audit_20261014_round154_ai_greeting_mail.md>) |
| `A330` | [AI 密钥和模型配置分步保存，失败后出现新旧配置混用](<health_audit_20261014_round155_ai_config_leaderboard.md>) |
| `A331` | [旧格式 AI 密钥未迁移，保存其他设置也可能把密钥清空](<health_audit_20261014_round155_ai_config_leaderboard.md>) |
| `A332` | [AI 面板把合法温度 0 当作空值，保存为默认温度](<health_audit_20261014_round155_ai_config_leaderboard.md>) |
| `A333` | [AI 榜单缓存写入失败会损坏旧文件，界面却提示同步成功](<health_audit_20261014_round155_ai_config_leaderboard.md>) |

<a id="m20"></a>

## 20 GM 控制台、维护与资源同步

| 编号 | 问题标题与报告 |
| --- | --- |
| `A334` | [GM 外围资料两表分开提交，保存失败后界面仍读旧资料](<health_audit_20261014_round156_gm_account.md>) |
| `A335` | [GM 单独修改等级或经验后，账号等级、经验资产和体力上限不一致](<health_audit_20261014_round156_gm_account.md>) |
| `A336` | [GM 刻印发放失败后退为材料发放，实例与材料可能重复到账](<health_audit_20261015_round157_gm_inventory_quick.md>) |
| `A337` | [GM 快捷签到沿用上月日号，且按错误游戏日判断已签到](<health_audit_20261015_round157_gm_inventory_quick.md>) |
| `A338` | [GM 刻印查询使用旧字段，库存为空或把实例编号当道具编号](<health_audit_20261015_round157_gm_inventory_quick.md>) |
| `A339` | [GM 指定章节通关变成全局通关，其他章节也被修改](<health_audit_20261015_round158_gm_stage_preset.md>) |
| `A340` | [GM 全通关不补缺失记录，主线星级仍为零](<health_audit_20261015_round158_gm_stage_preset.md>) |
| `A341` | [GM 关卡与剧情修改分开提交，后段失败留下半份存档](<health_audit_20261015_round158_gm_stage_preset.md>) |
| `A342` | [GM 誓约接受不支持或无效角色，登录出现没有配置的婚约](<health_audit_20261015_round159_gm_oath_commands.md>) |
| `A343` | [GM 誓约数量和等级参数解析错误，零值、负数与回显不一致](<health_audit_20261015_round159_gm_oath_commands.md>) |
| `A344` | [GM 誓约命令部分提交，重试可能重复补发戒指](<health_audit_20261015_round159_gm_oath_commands.md>) |
| `A345` | [GM 自定义货架未更新时间，下次读取又被自动刷新覆盖](<health_audit_20261015_round160_gm_shop_commands.md>) |
| `A346` | [GM 强制周期刷新失败仍成功，旧货架被标成当天有效](<health_audit_20261015_round160_gm_shop_commands.md>) |
| `A347` | [GM 自定义商品接受无效资源编号，直接下发到货架](<health_audit_20261015_round160_gm_shop_commands.md>) |
| `A348` | [GM 商店参数解析错误，字符串 false 变免费，非法店号变全店重置](<health_audit_20261015_round161_gm_shop_purchase.md>) |
| `A349` | [GM 免费商品未正确解析奖励，已记购买却没有发奖](<health_audit_20261015_round161_gm_shop_purchase.md>) |
| `A350` | [GM 商城扣费、发奖和收据未共同回滚，重试可能重复发奖](<health_audit_20261015_round161_gm_shop_purchase.md>) |
| `A351` | [同秒备份使用相同文件名，新备份覆盖上一份快照](<health_audit_20261015_round162_maintenance_update.md>) |
| `A352` | [同步取消与新任务状态串用，旧任务可能把新任务标完成](<health_audit_20261015_round162_maintenance_update.md>) |
| `A353` | [同步线程启动失败后仍占用忙碌状态，后续重试被拒绝](<health_audit_20261015_round162_maintenance_update.md>) |
| `A354` | [资源下载、比对或注册失败仍标记完成，重启后版本数据丢失](<health_audit_20261015_round162_maintenance_update.md>) |
| `A355` | [同步目标版本与实际下载版本不一致，资源和注册信息错配](<health_audit_20261015_round162_maintenance_update.md>) |
| `A356` | [区服和启动配置直接覆盖写入，保存失败损坏旧文件](<health_audit_20261015_round163_zone_launch_config.md>) |
| `A357` | [启动配置保存失败但日志等级已修改，文件与运行状态不同](<health_audit_20261015_round163_zone_launch_config.md>) |
| `A358` | [启动配置局部更新覆盖其他字段，原端口和路径恢复默认](<health_audit_20261015_round163_zone_launch_config.md>) |
| `A359` | [GM 总览使用固定在线人数和统计值，查询失败也显示正常](<health_audit_20261015_round164_overview_status.md>) |
| `A360` | [GM 只读连接失败后退回可写连接，只读保护失效](<health_audit_20261015_round164_overview_status.md>) |
| `A361` | [日志增量游标跳过截断和超额记录，面板漏显示日志](<health_audit_20261015_round164_overview_status.md>) |
| `A362` | [旧请求覆盖新状态，面板在线状态或版本显示回退](<health_audit_20261015_round164_overview_status.md>) |

<a id="rounds"></a>

## 按扫描轮次查报告

<details>
<summary>展开 181 轮报告目录</summary>

| 轮次 | 报告主题 |
| ---: | --- |
| 1 | [架构与计划](<health_audit_20260920.md>) |
| 2 | [资产与事件](<health_audit_20260920_round2.md>) |
| 3 | [战斗与钥从](<health_audit_20260920_round3.md>) |
| 4 | [游玩状态流转](<health_audit_20260920_round4.md>) |
| 5 | [多维与并发](<health_audit_20260920_round5.md>) |
| 6 | [多维修复交接指引](<health_audit_20260920_round6_handoff.md>) |
| 7 | [多维新旧局与状态重建](<health_audit_20260920_round7_recovery.md>) |
| 8 | [常用关卡与奖励边界](<health_audit_20260920_round8_common_stage.md>) |
| 9 | [资源倍率与主线首通对照](<health_audit_20260920_round9_multiplier.md>) |
| 10 | [支线首通与三星条件](<health_audit_20260920_round10_subplot_stars.md>) |
| 11 | [角色升级与突破](<health_audit_20260920_round11_hero.md>) |
| 12 | [技能与属性接口契约](<health_audit_20260920_round12_skills.md>) |
| 13 | [权钥强化入口](<health_audit_20260920_round13_weapon.md>) |
| 14 | [权钥突破与消耗对照](<health_audit_20260920_round14_weapon_progress.md>) |
| 15 | [回滚与删除通知](<health_audit_20260921_round15_rollback.md>) |
| 16 | [刻印失败响应](<health_audit_20260921_round16_equip_rollback.md>) |
| 17 | [刻印佩戴引用](<health_audit_20260921_round17_ownership.md>) |
| 18 | [刻印突破与继承](<health_audit_20260921_round18_equip_progress.md>) |
| 19 | [赋能重构与刻印收口](<health_audit_20260921_round19_preview.md>) |
| 20 | [任务领取入口](<health_audit_20260921_round20_tasks.md>) |
| 21 | [任务库存广播与战令](<health_audit_20260921_round21_task_inventory.md>) |
| 22 | [活跃度事件消费者](<health_audit_20260921_round22_activity_consumer.md>) |
| 23 | [任务跨周期状态链](<health_audit_20260921_round23_task_cycle.md>) |
| 24 | [在线跨周期刷新](<health_audit_20260921_round24_cycle_notifications.md>) |
| 25 | [普通日与长离线恢复](<health_audit_20260921_round25_cycle_recovery.md>) |
| 26 | [体力恢复与计时边界](<health_audit_20260921_round26_fatigue.md>) |
| 27 | [补给与升级赠送](<health_audit_20260921_round27_fatigue_grants.md>) |
| 28 | [道具使用事务与产出](<health_audit_20260921_round28_item_use.md>) |
| 29 | [自选数量与随机掉落](<health_audit_20260921_round29_reward_rules.md>) |
| 30 | [限时道具契约](<health_audit_20260921_round30_item_expiry.md>) |
| 31 | [角色重复获得与折算](<health_audit_20260921_round31_hero_conversion.md>) |
| 32 | [背包上限与超额折算](<health_audit_20260921_round32_overflow.md>) |
| 33 | [场景皮肤表情重复拥有](<health_audit_20260921_round33_unique_assets.md>) |
| 34 | [抽卡结算失败链](<health_audit_20260921_round34_draw_settlement.md>) |
| 35 | [保底继承与自选目标](<health_audit_20260922_round35_draw_inheritance.md>) |
| 36 | [完整大小保底对照](<health_audit_20260922_round36_draw_guarantees.md>) |
| 37 | [十连与钥从自选](<health_audit_20260922_round37_ten_servant.md>) |
| 38 | [钥从实例与返利](<health_audit_20260922_round38_servant_assets.md>) |
| 39 | [钥从实例通知](<health_audit_20260922_round39_servant_notify.md>) |
| 40 | [抽卡角色同步](<health_audit_20260922_round40_hero_notify.md>) |
| 41 | [抽卡碎片与剩余计划](<health_audit_20260922_round41_draw_pieces.md>) |
| 42 | [管理喵预设](<health_audit_20260922_round42_chip_proposals.md>) |
| 43 | [角色芯片绑定](<health_audit_20260922_round43_hero_chip_binding.md>) |
| 44 | [助战解锁前置](<health_audit_20260922_round44_reviser_unlock.md>) |
| 45 | [角色芯片解锁](<health_audit_20260922_round45_hero_chip_unlock.md>) |
| 46 | [芯片扣费事务](<health_audit_20260922_round46_chip_cost_atomic.md>) |
| 47 | [芯片成就与子链收口](<health_audit_20260922_round47_chip_closeout.md>) |
| 48 | [编队保存回读](<health_audit_20260922_round48_reserve_roundtrip.md>) |
| 49 | [编队协议与回包](<health_audit_20260928_round49_reserve_protocol.md>) |
| 50 | [助战字段读写](<health_audit_20260928_round50_assist_fields.md>) |
| 51 | [编队槽位恢复](<health_audit_20260928_round51_reserve_slots.md>) |
| 52 | [编队重置入口](<health_audit_20260928_round52_reserve_reset.md>) |
| 53 | [多队附加状态与总结](<health_audit_20260928_round53_multichess_closeout.md>) |
| 54 | [梦境领奖入口](<health_audit_20260928_round54_weekly_claim.md>) |
| 55 | [梦境领取互操作](<health_audit_20260928_round55_weekly_claim_interop.md>) |
| 56 | [梦境发奖降级与事务](<health_audit_20260928_round56_weekly_grant_fallback.md>) |
| 57 | [梦境结算归属与广播](<health_audit_20261002_round57_weekly_settlement.md>) |
| 58 | [梦境重置与锁定状态](<health_audit_20261002_round58_weekly_reset_state.md>) |
| 59 | [梦境队伍与英雄锁定](<health_audit_20261002_round59_weekly_team_lock.md>) |
| 60 | [梦境周期边界](<health_audit_20261002_round60_weekly_cycle.md>) |
| 61 | [梦境旧战斗跨周](<health_audit_20261002_round61_weekly_old_battle.md>) |
| 62 | [梦境双刷新与恢复](<health_audit_20261003_round62_weekly_refresh_paths.md>) |
| 63 | [梦境在线恢复与阶段收口](<health_audit_20261003_round63_weekly_online_closeout.md>) |
| 64 | [黑区双队接力](<health_audit_20261003_round64_mythic_relay.md>) |
| 65 | [黑区切档与重置](<health_audit_20261003_round65_mythic_transition.md>) |
| 66 | [黑区领奖互操作](<health_audit_20261003_round66_mythic_claim.md>) |
| 67 | [黑区半周边界](<health_audit_20261003_round67_mythic_cycle.md>) |
| 68 | [常规黑区星级与领奖](<health_audit_20261003_round68_mythic_normal.md>) |
| 69 | [黑区输出与阶段收口](<health_audit_20261003_round69_mythic_output.md>) |
| 70 | [迭代校验311与Mode 4领奖](<health_audit_20261007_round70_core_mode4_claim.md>) |
| 71 | [Mode 4队伍成绩恢复与任务达标](<health_audit_20261007_round71_core_mode4_progress.md>) |
| 72 | [Mode 4逐队参数与结算重置](<health_audit_20261007_round72_core_mode4_lifecycle.md>) |
| 73 | [Mode 4旧结算与成绩归属](<health_audit_20261007_round73_core_mode4_old_settlement.md>) |
| 74 | [常规迭代校验周期与台账](<health_audit_20261007_round74_core_regular_cycle.md>) |
| 75 | [常规任务归属与领奖重置](<health_audit_20261007_round75_core_regular_claim_reset.md>) |
| 76 | [常规旧结算与成绩广播](<health_audit_20261007_round76_core_regular_old_settlement.md>) |
| 77 | [常规空快照与最佳阵容](<health_audit_20261007_round77_core_regular_restore.md>) |
| 78 | [Mode 4活动与版本归属](<health_audit_20261007_round78_core_mode4_activity.md>) |
| 79 | [Mode 4最高分重置与阶段收口](<health_audit_20261007_round79_core_score_handoff.md>) |
| 80 | [自走棋入口与会话](<health_audit_20261007_round80_autochess_entry.md>) |
| 81 | [自走棋商店与免费刷新](<health_audit_20261007_round81_autochess_shop.md>) |
| 82 | [自走棋经验、合成与售价](<health_audit_20261007_round82_autochess_buy_merge.md>) |
| 83 | [自走棋布阵与整备边界](<health_audit_20261007_round83_autochess_formation.md>) |
| 84 | [自走棋胜败与冷恢复](<health_audit_20261007_round84_autochess_round_restore.md>) |
| 85 | [自走棋通关徽章与积分任务](<health_audit_20261007_round85_autochess_clear_medal.md>) |
| 86 | [自走棋取消与回放](<health_audit_20261007_round86_autochess_cancel_replay.md>) |
| 87 | [自走棋平局与行动上限](<health_audit_20261008_round87_autochess_draw_limit.md>) |
| 88 | [自走棋技能与阵亡队列](<health_audit_20261008_round88_autochess_skills.md>) |
| 89 | [自走棋亡语护盾与补位](<health_audit_20261008_round89_autochess_death_shield.md>) |
| 90 | [自走棋困难任务与终败重试](<health_audit_20261008_round90_autochess_tasks_retry.md>) |
| 91 | [梵天特殊阶段与局内道具](<health_audit_20261008_round91_autochess_brahma_props.md>) |
| 92 | [自走棋Core通知与同账号恢复](<health_audit_20261008_round92_autochess_core_recovery.md>) |
| 93 | [成就领奖去重与双端确认](<health_audit_20261008_round93_achievement_claim.md>) |
| 94 | [成就门槛与补算降级](<health_audit_20261008_round94_achievement_threshold_recovery.md>) |
| 95 | [时迹馈赠累计登录与每日误弹](<health_audit_20261008_round95_accumulate_login_popup.md>) |
| 96 | [登录成就周期与旧状态补帧](<health_audit_20261008_round96_login_achievement_cycle.md>) |
| 97 | [通关事件与战报成就合同](<health_audit_20261008_round97_stage_achievement_contract.md>) |
| 98 | [局内表现统计与单人通关累计](<health_audit_20261008_round98_battle_achievement_statistics.md>) |
| 99 | [成就物语点数、阅读确认与已读恢复](<health_audit_20261009_round99_achievement_story_read.md>) |
| 100 | [图鉴插画解锁、领取与查看恢复](<health_audit_20261009_round100_illustration_claim_view.md>) |
| 101 | [剧情与档案插画资格、阅读失败和冷恢复](<health_audit_20261009_round101_archive_illustration_recovery.md>) |
| 102 | [阵营收集计数、档位奖励和恢复](<health_audit_20261009_round102_hero_race_collect.md>) |
| 103 | [已解锁插画自定义过场图、保存与冷恢复](<health_audit_20261009_round103_loading_picture_settings.md>) |
| 104 | [大厅BGM设置、场景优先级与持久恢复](<health_audit_20261009_round104_lobby_bgm_recovery.md>) |
| 105 | [视骸击杀、图鉴计数、查看与恢复](<health_audit_20261009_round105_enemy_illustrated_recovery.md>) |
| 106 | [钥从与刻印图鉴、获得通知与持有恢复](<health_audit_20261009_round106_servant_equip_illustrated.md>) |
| 107 | [世界观词条与情报收集、获得与恢复](<health_audit_20261009_round107_worldview_intelligence.md>) |
| 108 | [一阶档案赠礼、扣费与形态同步](<health_audit_20261009_round108_archive_gift_contract.md>) |
| 109 | [BS战斗结果反馈与在线成长](<health_audit_20261009_round109_battle_growth_feedback.md>) |
| 110 | [交心／信赖成长与置换合同](<health_audit_20261009_round110_trust_growth_contract.md>) |
| 111 | [连携升级与关系网战斗反馈](<health_audit_20261009_round111_combo_relation_feedback.md>) |
| 112 | [誓约资格、任务状态机与仪式反馈](<health_audit_20261009_round112_oath_state_contract.md>) |
| 113 | [誓约真实生产端与后宅身份联动](<health_audit_20261009_round113_oath_producer_feedback.md>) |
| 114 | [誓约昵称、照片、物语与权益恢复](<health_audit_20261009_round114_oath_tail_contract.md>) |
| 115 | [超级心链与关系剧情阅读、领奖和恢复](<health_audit_20261009_round115_super_heart_relation_story.md>) |
| 116 | [后宅进餐成长映射与档案轶事领奖、冷恢复](<health_audit_20261009_round116_dorm_feed_anecdote.md>) |
| 117 | [档案好感外围生命周期与常用主干收束](<health_audit_20261009_round117_archive_lifecycle.md>) |
| 118 | [后宅食堂冷初始化、岗位派遣身份与广播](<health_audit_20261010_round118_backhome_identity.md>) |
| 119 | [食堂营业、食材与委托领奖](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| 120 | [委托批量终态、人员释放与食堂设施资格](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| 121 | [食堂技能、手动场次与每日刷新](<health_audit_20261010_round121_canteen_skill_cycle.md>) |
| 122 | [房间布局、模板与入住恢复](<health_audit_20261010_round122_dorm_layout_lifecycle.md>) |
| 123 | [房间赠礼、成长与资格、门牌保存](<health_audit_20261010_round123_dorm_gift_qualification.md>) |
| 124 | [猫咪探索派遣、结算与周期恢复](<health_audit_20261010_round124_cat_explore_lifecycle.md>) |
| 125 | [猫咪培养、收益合同与常用主链收口](<health_audit_20261010_round125_cat_cultivation_reward.md>) |
| 126 | [连续周期礼包激活、每日履约与邮件反馈](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| 127 | [通用活动积分领奖入口、资产反馈与日周恢复](<health_audit_20261010_round127_activity_point_claim_contract.md>) |
| 128 | [新手／回归资格、领取记录与周期恢复](<health_audit_20261010_round128_newbie_regression_lifecycle.md>) |
| 129 | [新手任务、回归权益与月卡天数生产端](<health_audit_20261013_round129_newbie_regression_producers.md>) |
| 130 | [普通月卡／大月卡激活、续期、领取与恢复](<health_audit_20261013_round130_month_cards_lifecycle.md>) |
| 131 | [iPhone229限定PAY商品发货与恢复](<health_audit_20261013_round131_ios229_pay_delivery.md>) |
| 132 | [229累充领奖与首充恢复](<health_audit_20261013_round132_recharge_claim_contract.md>) |
| 133 | [累充高档专属资产与旧新追加](<health_audit_20261013_round133_recharge_special_assets.md>) |
| 134 | [常用商店购买、目录归属与限购刷新](<health_audit_20261013_round134_common_shop_purchase.md>) |
| 135 | [商店周期恢复、兑换资格与聚合花币扣费](<health_audit_20261013_round135_shop_cycle_eligibility.md>) |
| 136 | [商店专属交付、分类冲突与联购回滚](<health_audit_20261014_round136_shop_special_delivery.md>) |
| 137 | [场景、展示换装与作战换装选择保存](<health_audit_20261014_round137_profile_scene_skin.md>) |
| 138 | [随机展示设置、名片英雄与常用装扮](<health_audit_20261014_round138_profile_random_decorations.md>) |
| 139 | [贴纸多布局、占用迁移、套装领奖与标签](<health_audit_20261014_round139_sticker_layout.md>) |
| 140 | [生日、赠礼预留、语音确认与触摸周期](<health_audit_20261014_round140_profile_gift_birthday.md>) |
| 141 | [昵称面板保存、签名双入口与单账号点赞合同](<health_audit_20261014_round141_profile_basic.md>) |
| 142 | [大厅纯白、组件、特殊视角与本地／云配置](<health_audit_20261014_round142_lobby_settings.md>) |
| 143 | [永久装扮、文本语言与外围清册收束](<health_audit_20261014_round143_peripheral_closure.md>) |
| 144 | [登录基础帧、替换追加和完成边界](<health_audit_20261014_round144_login_flood.md>) |
| 145 | [登录完成、在线注册与同账号重连退出](<health_audit_20261014_round145_login_ready_reconnect.md>) |
| 146 | [Lua重连完成回调与同账号战斗会话恢复](<health_audit_20261014_round146_reconnect_consumers.md>) |
| 147 | [必要回执、等待退出与登录组交接](<health_audit_20261014_round147_reply_wait.md>) |
| 148 | [当前AI入口、预写与双表持久化](<health_audit_20261014_round148_ai_chat_entry.md>) |
| 149 | [AI历史、清空与迟到响应](<health_audit_20261014_round149_ai_history_clear.md>) |
| 150 | [AI队列、任务失败与重连补收](<health_audit_20261014_round150_ai_queue_delivery.md>) |
| 151 | [AI人格作用域与动态context](<health_audit_20261014_round151_ai_persona_context.md>) |
| 152 | [问候消耗、防重与冷并发](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| 153 | [AI总时限、重试与登录排队](<health_audit_20261014_round153_ai_timeout_retry.md>) |
| 154 | [问候资格、附件校验与邮件反馈](<health_audit_20261014_round154_ai_greeting_mail.md>) |
| 155 | [模型配置与参考榜单恢复](<health_audit_20261014_round155_ai_config_leaderboard.md>) |
| 156 | [GM账号覆写与状态恢复](<health_audit_20261014_round156_gm_account.md>) |
| 157 | [GM资产直发与快捷签到](<health_audit_20261015_round157_gm_inventory_quick.md>) |
| 158 | [GM章节与进度预设](<health_audit_20261015_round158_gm_stage_preset.md>) |
| 159 | [GM誓约七命令与失败恢复](<health_audit_20261015_round159_gm_oath_commands.md>) |
| 160 | [GM商城刷新、自定义货架与强制周期](<health_audit_20261015_round160_gm_shop_commands.md>) |
| 161 | [GM商品购买、免费直发与页面重置限购](<health_audit_20261015_round161_gm_shop_purchase.md>) |
| 162 | [维护备份与资源同步任务状态机](<health_audit_20261015_round162_maintenance_update.md>) |
| 163 | [区服服名与启动配置保存及消费](<health_audit_20261015_round163_zone_launch_config.md>) |
| 164 | [总览、公共在线状态与读口恢复](<health_audit_20261015_round164_overview_status.md>) |
| 165 | [广播横向首段：资产与战斗反馈](<health_audit_20261015_round165_broadcast_contracts.md>) |
| 166 | [任务、活跃、战令与周期/登录广播](<health_audit_20261015_round166_task_login_broadcasts.md>) |
| 167 | [资料/誓约/档案/芯片扩展广播与显式反馈](<health_audit_20261015_round167_extension_broadcasts.md>) |
| 168 | [商城/PAY/周期礼包广播与直接发货](<health_audit_20261015_round168_shop_pay_broadcasts.md>) |
| 169 | [CORE骨架/模板与旧素材来源首段](<health_audit_20261015_round169_response_sources.md>) |
| 170 | [角色/战斗模板与SQL覆盖、生成失败](<health_audit_20261015_round170_binary_template_overrides.md>) |
| 171 | [协议schema来源、同名引用与业务边界](<health_audit_20261017_round171_schema_provenance.md>) |
| 172 | [任务/活动恢复固定值与日周月时间源](<health_audit_20261017_round172_common_restore_sources.md>) |
| 173 | [关卡双表权威与黑区/介质恢复降级](<health_audit_20261017_round173_weekly_stage_restore.md>) |
| 174 | [梦境/迭代恢复身份、目录与故障](<health_audit_20261017_round174_dream_core_restore.md>) |
| 175 | [常用服务装配、单例与BUS生命周期](<health_audit_20261017_round175_service_bus_lifecycle.md>) |
| 176 | [新旧分发优先级、异常出口与回滚差量](<health_audit_20261017_round176_dispatch_failure_boundary.md>) |
| 177 | [跨服务事务、BUS回调与提交失败恢复](<health_audit_20261017_round177_nested_transaction_bus.md>) |
| 178 | [单账号在途会话与资源清理](<health_audit_20261017_round178_single_account_session_cleanup.md>) |
| 179 | [常用横向覆盖、冲突与修复依赖](<health_audit_20261017_round179_common_closure_review.md>) |
| 180 | [多重心魔独立纵向链](<health_audit_20261017_round180_multheartdemon.md>) |
| 181 | [Mode1—3旧尾与阶段收工](<health_audit_20261017_round181_core123_closure.md>) |

</details>
