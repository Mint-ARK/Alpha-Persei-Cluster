# 虚构推演：当前状态机复核与今晚可实施边界

日期：2026-11-05。范围：当前开发源码、x64原生Lua、真实AccountDB/Core/codec的内存夹具。未改业务文件、真实存档、远端、Git或客户端。本文是重构蓝图，不是修复完成报告。

## 结论

保留11月4日已经合入的本局身份、结局剧情确认、结算报告/发奖幂等、价格及业务效果，集中替换“当前节点允许哪些动作”的控制层和必要响应准备层。当前主要P1不是缺少整局事务，而是若干入口仍绕开节点/待办归属，或把必要数据帧当可选帧。无需重写约4500行效果逻辑，也不应复制旧附件覆盖当前文件。

本轮五个当前源码反例均经内存Core复现。旧10月26日报告中的问题不能原样当作当前未修问题；下表只记录此次复核。

## 当前P1及可实施修复

| 编号 | 触发与当前实际结果 | 根因位置 | 最小修复 |
|---|---|---|---|
| RT-P1-01 | 选中真实战斗节点101，state=UNCLEAN、param=4101101，无待办；88024 opt_id=0返回0，节点直接OVER、下一节点解锁、selected清零 | rogueteam_flow.py choose只检查节点state为0/1；rogueteam_service.py:3066 opt0统一收尾 | 统一finish_node动作；opt0只能消费当前事件/安全屋终结描述，禁止用于未结算战斗、奖励、商店。必须有对应待办或明确无交互节点类型 |
| RT-P1-02 | 同一未打战斗节点发88222 index=0也返回0并直接OVER | rogueteam_service.py:4328 leave_shop不检查节点类型、shop_info、本局阶段；flow保存了originals['leave_shop']却没有安装守卫 | 离店必须active＋当前SHOP节点＋同节点商店上下文；合法重复离店只读既有结果，不推进其他节点 |
| RT-P1-03 | 有待领奖other_info时发88102 hero_list=[]返回0并清空奖励；无招募上下文也可加入不存在hero_id=999999 | rogueteam_service.py:4050 hero_recruit仅检查session存在，最后无条件覆盖other_info | 招募仅消费HERO_RECRUIT=3待办；验证英雄/试用配置、拥有或合法试用资格、名额、去重及hp_ratio服务端初值；非招募状态不得清待办 |
| RT-P1-04 | 给88019编码注入None；88102仍返回ACK0且英雄999999持久入局，仅发88103，客户状态缺失 | operations.py:8504等respond对必要帧使用if payload；Core只检查主ACK存在 | 迁移Rogue操作至统一required_frame/prepare_transition_frames；本次变更需要的88019/88015/88023/88027/88013/88003等编码失败必须抛错，回滚SQL和通知 |
| RT-P1-05 | 仍处于战斗节点、shop_info={}，88224返回0、扣局内金币并生成商店货架 | rogueteam_service.py:4359 shop_refresh缺少节点/待办/生命周期守卫，还允许没有对应藏品的50起步刷新 | 只在active当前商店上下文允许刷新；依据原生按钮与配置校刷新资格/成本；不保留客户端无入口的默认50收费路径作为合法规则 |

这些是乱序、重试或构造请求能触发的服务端断链，不代表普通客户端一定主动发送这些序列。第04项是明确的失败注入一致性漏洞，与协议是否正常触发无关。

### 内存复现摘要

使用AccountDB.new_save_workspace()，ACCOUNT_DB_PATH=:memory:，uid42，初始实际英雄1084；Core.dispatch输入真实codec编码请求，输出真实codec解码ACK。只将局内地图换为两节点夹具，不写真实数据库。

```json
[
  {"scenario":"battle_event_exit","cs":88024,"result":0,"selected":0,"node101_state":3,"node102_state":0},
  {"scenario":"battle_shop_exit","cs":88222,"result":0,"selected":0,"node101_state":3,"node102_state":0},
  {"scenario":"recruit_clears_reward","cs":88102,"result":0,"before_pending_type":2,"after_pending":{}},
  {"scenario":"recruit_required_frame_missing","cs":88102,"result":0,"scs":[88103],"persisted_unknown_hero":999999},
  {"scenario":"shop_refresh_outside_shop","cs":88224,"result":0,"selected":101,"shop_event_type":7,"refresh_times":1}
]
```

复现采用直接内联Python，不生成外部执行脚本；结果为此次检查输出，不是沿用历史日志。

## 已成立、应保留的状态链

1. `start_run`拒覆盖在局session，并保存`flow_json.run_id`；`get(db)`绑定AccountDB实例，不共享可变库指针。
2. `select_node`已核上一已完成节点next_id_list、首列、selected及待办，普通点击不能随意跳地图。
3. `choose_event_option`非零选项已校当前opt_list；`commit_event_selection`已校event_type与候选集合，不能把另一种奖励类型当当前类型。
4. `next_floor`普通sign1要求末列OVER且无事件/奖励/队列；sign2/3仅在已有second_map时交换，不再错误加普通楼层。
5. 终局胜利写`ending_plot`；88218写`plot_played/ready_settle`；88306保存report、70/71奖励及awarded，重复返回同报告，不重复发奖。
6. 88030和登录已补事件88023、候选88015、货架88027、剧情88217；并非仍是10月26日“完全漏恢复事件”的状态。
7. 战斗请求保存rogue_run_id，结算check_battle拒旧局战报、节点变化。需继续沿用，不能只依节点ID去重。

## 统一控制层：最少接口

建议在现有`rogueteam_flow.py`扩成明确函数，或新增`rogueteam_transition.py`由flow调用；第一轮不改数据库DDL。`flow_json`新增version、当前待办归属/节点阶段即可，原有session资产/地图JSON仍保留。

```python
read_context(service, uid, template_id) -> RunContext
require_action(context, action, payload) -> ActionTarget
finish_node(service, context, reason) -> TransitionResult
record_pending(service, context, kind, payload, queue=()) -> Pending
consume_pending(service, context, action, selection) -> TransitionResult
prepare_transition_frames(ctx, result) -> list[DownFrame]
resume_frames(ctx, context) -> list[DownFrame]
```

`RunContext`只读取本局`run_id/phase/floor/map_kind/select_node`、当前节点类型/state，以及pending/shop；不能仅凭一个`in_game`布尔放行所有动作。

`Pending`最少含`run_id/floor/map_kind/node_id/kind/revision/payload/queue`。服务端revision用于状态一致性和诊断，不新增Lua不存在的请求字段。老存档只在上下文能唯一确定时迁入；不把残留货架、任意event_type或别的节点候选自动认成当前可消费状态。

`TransitionResult`持有本次已保存事实与需要的帧，而非respond再次读取并修改业务。编码发生在既有Core事务/保存点内；必要帧失败整体回滚，纯空repeated协议按schema允许，不能以全局`bool(payload)`排掉合法空包。

### 操作门禁表

| 操作 | 合法上下文 | 成功后的状态 |
|---|---|---|
| 88100开局 | 无在局；模板/难度/队伍合法 | active、可选首节点、独立run_id |
| 88004选节点 | active、无待办、无未完成selected、目标是当前next域 | 当前节点及其事件/奖励/商店/待战上下文 |
| 54030战斗 | active、当前选中战斗节点UNCLEAN、资源stage匹配、无冲突待办 | 同run/floor/map/node战斗身份 |
| 54032战胜 | 对应战斗身份、节点尚未结算 | CLEAN＋奖励队列；无队列才OVER；终局转ending_plot |
| 88024非零 | active、当前EVENT/REST待办且opt在候选内 | 下一事件、招募、奖励、战斗或明确可离开描述 |
| 88024零 | active、当前事件链允许结束；无未领取奖励/招募/战斗 | 一次finish_node、一次计步，不能借此跳战斗 |
| 88102招募 | active、HERO_RECRUIT=3，队伍与资格合法 | 名额消费＋新队员＋后续安全屋事件；其他待办保留 |
| 88200确认 | active、同节点当前奖励类型及候选 | 消费一个队列头；还有队列不结束节点 |
| 88202刷新候选 | active、正确可刷新待办类型、次数足够 | 原队列归属不变，候选revision变化、扣一次次数 |
| 88222购买/离店 | active、当前SHOP、有效货架；index0为离店 | 购买资产及剩余次数，或一次finish_node |
| 88224刷新商店 | active、当前SHOP、原生刷新资格与费用 | 当前货架换新、费用/次数一致 |
| 88208替换节点确认 | 有NODE_TYPE_CHANGED待办，node_id/node_type和当前待办匹配 | 消费该待办；不得空成功 |
| 88212丢弃 | active、待办要求丢弃/替换、物品确属该局且满足数量条件 | 仅消费对应待办，不清别类奖励 |
| 88008切层/图 | 无未消费交互/战斗，普通末节点完或合法隐藏图边 | 同局地图上下文变化，选中指针重建 |
| 88218剧情确认 | ending_plot/ready_settle | ready_settle，重复幂等 |
| 88306结算 | 合法失败/放弃或ready_settle；已结算可重取report | settled、报告和资产一次性提交 |
| 88030/登录 | 只重建持久事实 | 不重新抽奖励、不计步、不扣费 |

## 今晚建议落地顺序

**第一块：节点归属和待办门禁。** 先替换opt0与离店共用收尾函数，再把hero_recruit、shop_refresh、reset_operate_data、discard_items、88208纳入相同context。保留原效果函数，守卫成功后调用；收尾计数在一个明确位置执行一次。这一块直接覆盖本轮01/02/03/05。

**第二块：必要帧和恢复。** 将Rogue操作的必要响应统一编码，覆盖本轮04；战后88033/88015/88001/88013及恢复88001/88019也应用同原则。middleware目前异常会raise回滚，但返回None仍被if过滤，不能把“有try/raise”当作全部必要帧已保证。

**第三块：整合一次纵向验收。** 正常节点→事件→招募→商店→战斗→奖励队列→切层→终局→88218→88306；在事件、两轮奖励之间、商店、剧情确认前后读取冷恢复。并运行既有Issue8终局/幂等探针。无需重新穷举所有藏品效果。

对主线程的最小共享hook：`operations.py`对应Rogue操作apply/respond统一委托；`middleware.py`type78入场/结算调用统一校验与required frame。其它改动留在Rogue专属模块，避免冲击主线和当前刺桐整合。

## 明确保留的风险与后置工作

- 同类型连续奖励窗口的cs_88200仅含event_id/param_arg，没有服务端待办nonce；客户端若重复发送同索引且下一轮也有同索引，纯服务端不能完美区分旧重试与新合法选择。revision不能伪装解决协议信息不足。可以确保单一当前队列、跨类型拒绝、同局节点归属，并如实保留这个边界。
- 隐藏图生成仍未做。已有second_map的sign2/3交换不等于路线入口、图生成、回返位置都完整；今晚首块不杜撰官方随机分布。
- 当前战报没有已核准逐角色剩余HP权威字段，源码明确没有以累计伤害估算HP。生命继承需要实际报文证据；不能为了“完整”写假值。
- 地图随机权重、全部效果moment/冲突优先级、复杂事件转战斗、引导层101仍需后续配置核对，不能以本轮正常链通过宣称官方玩法完全还原。
- 88208当前Operation空成功、88212/88202生命周期门禁缺失属于静态候选，尚未像表中五项一样单独做Core反例；实施前应定向核原生消费者和参数。
- 当前`_active`仅拒settled，ending_plot/ready_settle仍可通过部分业务入口；统一动作白名单应封住该阶段，不再用“所有在局都可操作”判断。
- 未进行Unity实机；测试测量内部状态交互与真实协议输出，画面/动画异常留实机日志定位。

## 证据入口

- `AGENT_BOARD.md:12123`：11月4日实际合入和保留边界；不是10月26日未合入状态。
- `rogueteam_flow.py:63`、`:70`、`:115`、`:138`、`:199`：生命周期与部分包装守卫/恢复。
- `rogueteam_service.py:3045`、`:4050`、`:4284`、`:4328`、`:4359`：当前对应业务分支。
- `operations.py:8489`、`:8524`、`:8685`、`:8721`、`:8792`、`:8874`：原生入口及响应准备。
- `middleware.py`type78入口、`settle_battle_node`调用处：跨战斗身份及必要帧。
- 原生`challengerogueteamconst.lua`：节点、待办、切图、剧情枚举；`challengerogueteamaction.lua:301`以后：88222/88102/88202/88208/88212/88024/88218实际调用。

源码仍在主线程整合中，行号是此次阅读位置，实施时以函数和当前注册为准。

## 20:31 第一块落地验收

已在当前Issue8合入版上新增`rogueteam_transition.py`，由`rogueteam_flow.install`安装统一context/动作守卫；原有地图生成、物品效果、结局报告、奖励系数和价格算法保留。此阶段尚未修改隐藏图action19/20，后续另做。

- 事件opt0只接受当前事件/安全屋的无选项结束描述；离店只接受当前SHOP上下文；不能借二者完成未打战斗。
- pending/shop在flow_json保存run/floor/hidden/node归属与revision；非active阶段冻结局内操作。旧select0+唯一CLEAN战斗节点奖励可保守接管，多CLEAN拒绝，不重抽候选。
- 共用finish_map_node精确收尾，移除“任找首个CLEAN”的行为；stepped_nodes按地图/层/节点记录效果计步一次。真实战后selected0的奖励按owner收尾，队列未空不提前结束。
- 招募仅接受原生HERO_RECRUIT=3待办，校真实英雄配置、自有库存或本活动四个试用、重复及attr8名额；HP由服务端置10000。Lua允许空列表放弃招募，限定在真实窗口内保留。
- 商店刷新按原生attr30资格、RogueTeamSetting26001的20×2^次数；没有商店待办不会扣款/生成货架。
- 88202限定藏品窗口；88212校当前丢弃效果和范围/数量；88208只有真实替换待办才更新可达后继节点，不再空成功。
- 事件结束的机制弹窗实际保存，重登恢复后可领取；必要副帧None/异常回滚，合法空repeated实际append，避免只允许编码却被旧if过滤。
- 修正事件88305投影使用绑定AccountDB，避免`_RTS.get()`跨库读。
- check_battle严格run/floor/hidden/realstage/node/阶段；after允许本次完成节点/终局阶段。终局无候选窗口规范为空，88008不被空event_type1阻塞。

验证：22项新transition专项＋17项Issue8既有回归＋11项独立Rogue battle回归＝50项通过，仅AccountDB内存库、真实Core/codec。证据位于`archive/rogueteam_state_20261105/checks_v2.txt`、`issue8_regression.txt`、`battle_regression.txt`。原Issue8价格测试原本将商店event_type写0，已按原生7修正夹具；保留首次失败输出的已保存结果与备份。未通过篡改业务守卫保住错误夹具。

第一块核心SHA256：transition `f10250aa4e44a7c9bf95ec4a814526efd947af027f142313d1da1505418ac0fd`；flow `49d8293c9f0a27b9836e495eef73c5600d5b1efed57b4b4c4690a80612621360`；service `124c4e5386aab56e3985af67aff2b94caac185c98b4e75b805a02067778d0b5a`。这些是下一隐藏图阶段之前的验收点。

仍保留同类型无nonce请求歧义、未验证逐角色战后HP、全部效果优先级、实际Unity画面边界。第一块完成不等于所有历史玩法效果已核准。

## 20:45 第二块隐藏地图与剧情战续接

按原生 effect6002211/action19 进入、6004212/6005212/6005312/action20 返回的证据，修正旧版“商店折扣/清负面圣物”错误语义。原层 floor_num 不变；DB 内部活动图交换以复用原有节点业务，但所有 88001/88003 在线和登录投影恒为主 map_info、隐藏 second_map_info。隐藏来源节点须 OVER/select0 才能正式切换，map_switch 持久记录 run/floor/hidden/node/sign，跨局跨图与无待切换请求拒绝。

原生没有隐藏图拓扑表，故明确采用本服最小布局：单个 type8/param60030/col1/row3 节点，唯一 ID 从900001开始，原生事件链负责后续剧情；不宣称是官方拓扑，不新增概率。初版1001撞到现有Skuld入口，被真实Core战斗探针发现后改为高位ID。退出后保留完成的隐藏图，普通推进下一层时清空；主图完成路径始终保存，隐藏战斗强度仍由原主层/完成源列查找。

帧顺序按Lua真实消费者：88221先于88003/88001，因为88221本身不调用CheckFinishNode；接受88008时先88001后88009，因为ACK才清flag并重选图。88030全部恢复帧先于88031，避免回调进入页面时尚无地图。主线程登录已把pending_frames的88221前置。遇到机制阈值奖励时，源节点保CLEAN、选择指针保留，先消费奖励后置OVER并发切图信号，不抢跑动画；冷恢复仍定位隐藏图。

剧情战斗600411/600511不再提前发1330003。持久event_battle保存原事件节点和续接60042/60053；真实胜利后的掉落队列保存_resume_event，全部领取后才恢复事件type8/CLEAN，等待“收下”选项实际发工具箱并返程。失败/退出不推进；房间计步沿第一块receipt只发生一次。普通剧情战维持既有实际掉落窗口，因此战后after校验仍校原battle节点/关卡；没有放宽跨节点验证。

新增5项内存Core门禁/回滚测试覆盖未获资格/方向/重复切换、run/floor/hidden/node归属、88221/88003/88001/88009失败整笔回滚、负面圣物不被误删与工具箱一次、阈值奖励延后返程。独立Sol提供3条真实Core/codec/UDP/序列化登录路线和4条Python消费者镜像契约，覆盖非战斗往返、两战分支、四阶段冷恢复、稳定主图/原层及主图源列强度。镜像不是LuaVM或Unity实机，不覆盖动画渲染。

边界：重复88008不二次切图，返回错误；可重登恢复，但未实现ACK丢失后的同sign成功receipt重放。仍不声称历史所有效果优先级已全审、逐角色战后HP已验证、或者真实客户端已实测。云端继续暂停，不改真实库、不Git、不封包。

20:47补充：隐藏门禁专项为6项（另加待切图主动撤离后不重放信号）。父线程复核发现安全屋500101的真实招募窗口为event_type3/空param，旧pending_frames漏恢复；已按原生1..12类型与has_pending恢复88015，空货架type7同样恢复88027，不再要求候选列表非空。空终局奖励1/2/5/11仍不构成待办。Sol以真实500101/空店冷恢复做新增验证。

20:49更正并撤回上一段招募“漏恢复”判断：Sol逐帧/原生消费者证明88001.other_info已经带type3/空param，InitPathDataFromServer会UpdateOperateData并排招募窗口。新增88015虽有队列去重，不是必需修复；父线程决定最小范围，已回退这次pending_frames按纯类型补帧改动，保留原param_list条件。88030 ACK最后、登录恢复数据先于10201的时序确保窗口数据先于页面。招募冷恢复验证保留作证据，不新增修复票。6项hidden guard数不变。
