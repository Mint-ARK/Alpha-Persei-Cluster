# 刺桐港埠漫行纪客户端契约与收集/航行实现

本轮仅本地源码与内存 SQLite 探针；没有真实库写、远端同步、Git、客户端 Lua 修改或客户端实测。

## 入口与任务

- `sandplaymapcfg.lua:47`、`activitysandplaycfg.lua:10`：activity323641、map1110101、hero9073；子活动323841包含航行/见闻/角色战。
- `qworldaction.lua:14/20/77/108`：79601全量位置、79602保存位置→79603，28837已解锁功能、28834上报sys_id无ACK。
- `qworldquestaction.lua:29/35/55/80/105/122`：28801登录全量、28803任务变更、28820主任务提交、28822子任务提交、28830接取、28832参数更新；各请求对应+1 ACK。
- 主状态0锁/1解锁/2待接/3进行/9完成；子状态-100未开始/0进行/1完成/-101跳过。uint32报文不写负状态，省略表示未开始。
- 当前活动53主任务、129子任务；120个condition60接受活动蓝图进度，7个330001是海战5240201..07，2个330002是角色战5240221/22。战斗条件只从真实战斗结算推进。
- 10个type1主任务=11199初始化+11101..09九段主线；14个type2=七条支线的七个前导与七个完结；29个type3探索=四隐藏照片+15小互动+10宝箱；type4为0。
- 所有主unlock_operation2，唯一pre_operation1子任务11207000为空前置；本活动所有有内容的多前置均2，按AND解释，无可区分的OR实例。important_reward全部为空；客户端该字段用于关键奖励展示。

## 航行、照片、奖励

- BattleConst SEA_WARFARE84、SAND_PLAY85。海战BattleSeaWarfareStageTemplate.GetDest使用400xxx航行ID；实际资源stage_id为52402xx；activity_id原生未赋值可为0。
- 海战11关：400001教学、400101..106剧情、400201..204挑战。挑战入口要子任务11109001完成，随后pre_stage链。
- 海战六技能分别从SeaWarfareSkillCfg.task_id对应子任务完成解锁。86035 `{data:{affix:[skill_ids],stage_id_list:[completed_400xxx]}}`，后者客户端语义是通关，并非旧数据库注释所说的解锁。
- 每个非教学海战首通读DropCfg[32364101..10]，各20移转之辉+10神力因子+2黄票；与同号ActivityPointRewardCfg见闻档奖励是两个独立配置。
- 刺桐十张照片来自QuanzhouPhotoCfg，按子任务完成解锁，不另造收藏领取RPC。86033/86028/86030对应旧313771春节收藏（十个SandplayIlluCfg均task103xx），不计入刺桐实现范围。
- QWorldBookletTools.lua:1/17：见闻档system_key40007701，直接比较Item77数量，need为1..10，无额外EXP阈值。
- tipscfg.lua:27017-27024，QUANZHOU_DESC第4条明确“通过完成主线任务和探索刺桐港来获得见闻等级”；未提供数值权重。Item77描述仍为“待正式文案”。
- 用户明确批准本服宽松规则：九段主线每段3点、七条支线完结/29探索各1点；11199和七前导不计；等级cap10。主线全通27点为需求2.7倍。这不是官方计分还原。

## 本地接口

- `tools/export_quanzhou_contract.py` → `quanzhou_contract.json`：完整主/子任务与前置、地图/功能/系统/阶段/照片、海战/角色战/技能、DropCfg、见闻十档、四个推荐任务、帮助原文及源文件hash。重导出先备份旧文件。
- `quanzhou_collection.enter_validate(ctx,uid,sea_id,skill_id=0)`：84原生dest校验，使用quest.read+advance验证刚激活但尚无SQL行的剧情任务。
- `quanzhou_collection.settle(ctx,uid,sea_id,won)`：父线程确认战报身份后调用；首通落入ops_common.quanzhou_collection_v1、真实发奖和86035同保存点；重复/失败无额外奖励；不自行推进QWorld战斗任务。
- `sea_payload(db,uid)`用于86035登录强动态；旧quanzhou_sail_progress抓包种子保留，不导入新确认通关。
- `publish_progress(ctx,uid)`用于28822/28820后，同事务更新技能/补77差值/60501..04任务并推28007。新77走InventoryService，已有更高值不扣，重复不增。
- `restore_for_login(db,uid)`无通知恢复77及四任务，需在登录货币快照15009之前调用；照片直接由28801任务事实恢复。
- 见闻奖励复用现有60054→60055、activity_point_reward防重及60107登录恢复；不代领任务或见闻奖励。

## 探针结果

`python v5_server/tools/verify_quanzhou_collection.py`：15项内存探针通过。覆盖原生导出、11海战与6技能、故事/挑战门槛、首通/复打/失败、照片事实、旧抓包不回流、状态编码失败回滚、27点/10级、支线/探索、四任务差量/已领保留、登录无通知恢复、十档实际领奖及重复防重。

首次扩展夹具遗漏货币及角色附赠头像2110731的item_catalog类型，并误把DownFrame.cmd写为.sc；校正夹具后通过。没有为此放松业务编码或资产校验。

补充集成探针：`archive/quanzhou_20261105/probe_quanzhou_quest.py`八项（含全部53主任务链和总奖）、`tools/verify_quanzhou_login.py`六项真实序列洪流均通过，总计29项。登录fresh/old强动态去重并补齐79601→28801→28837→86035，全部位于10201前；空28837正常发空payload；77在15009之前静默恢复、无17023/28007登录差量。15009动态读取抛错与返回None均明确拒绝回退录制旧余额。

最终日志位于根目录`archive/quanzhou_20261105/{collection,quest,login}_final.log`。初版登录探针过度要求整个登录后无其他通知，但旧登录末尾原有73001；改为仅比较目标协议链，仍全局核实不推17023/28007。

尚待主线程合并验收：父线程84/85 battle身份及结算调用、所有任务自然推进；上述探针不代表客户端自然走图和实机UI验证。
