# A82 梦境首领身份与开战积分快照

2026-11-04。承接A86周期快照，核对第57轮报告、当前p54实际codec、客户端BattleController及普通/进阶模板和完整54030→54032结算链。

## 根因与Lua证据

BattleController common_info.activity_id来自GetActivityID，index来自GetServerExtant。进阶BattleBossAdvanceStageTemplate.GetServerExtant返回当前boss_list条目的id，也就是轮换pool_id；普通模板沿用基类的0。p54.enter_battle_common_info.index为字段6、uint32。

旧_parse_battle_common未提取index；结算却用stage_id相同或boss_id等于activity_id取首个命中，接着全池搜索/当前第一只/20001兜底。当前48项进阶配置中14个关卡存在多条首领配置，同一轮可能选出共用关卡的两只首领，不能只靠关卡确定归属。普通模式还存在固定5024兜底。

## 修改

- 新boss_battle_identity.resolve只在当前轮换列表内解析。进阶非零index必须是当前开放的首领，并且其配置关卡等于dest；index=0旧请求只有唯一stage命中才兼容。普通模式按当前可见stage_group唯一命中，不用activity_id猜测归属。
- 解析器与会话保存index，开战boss_context增加boss_id/stage_id。战斗类型与实际梦境类别冲突时拒绝，避免两个旧范围判断同时命中后写错进度表。
- 进阶按所选首领和实际advance_id保存开战score快照，结算不再重新读取玩家后来修改的难度/词缀。这只冻结现有算分公式及配置，不改奖励或概率政策。
- 结算在任何通关广播之前检查并使用已校验归属；去掉普通5024、进阶20001/第一只/全池搜索等成功兜底。旧无身份快照的内存会话只有当前轮换中唯一命中或带有效index时采用，含糊的旧会话返回完整QUIT错误和当前状态。
- A86周期/模式/所属UID、领域保存点、已提交缓存规则保持。新帧数量和正常刷新顺序保持，不为身份修复增加全量帧；错误时复用既有状态恢复。

## 内部检查

tools/verify_boss_identity.py：8专项+12 A86+5串行缓存+11当前入口回归，共36通过。实际内存AccountDB/Core/codec/动态54003及完整_settle_frames；跳过旧入口测试中会覆写历史证据的修前控制项。

真实配置/受控合法种子选出共用3012206的80005与70001，两次原生index请求分别只更新对应首领，均有真实通关事件、实例和SQL链。验证index丢失歧义拒绝、唯一旧请求允许、未知/未开放/冲突index、类型冲突、activity_id不能抢走身份、普通未开放/未知关卡无兜底、会话身份被改后无成功广播、旧含糊会话拒绝；开战8500分配置后改为更高难度，仍只记8500。

A86夹具更新为原生activity_id=0/index=pool_id，旧ts用例选择真正唯一的当前关卡；快照断言保留周期/模式子集，新增字段由本票检查。原脚本前件已备份，没有放宽生产判定。

修前AST定义全部放入同一模块命名空间，避免旧入口引用新helper；8专项7组失败/9失败断言，1组因缺少boss_id报错。最初混合命名空间的6失败输出保留，最终修前对照以before_checks_v2为准。没有用修前“应失败”探针作为修复验收。

不运行Unity或真实UDP；普通/进阶完整界面、A83领奖后重置、A84跨首领禁选仍按后续计划处理，未经游戏实测。

## 云端与留档

middleware.py及boss_battle_identity.py已同步43.139.154.74:/game-server/aethergazer，覆盖前副本archive/boss_identity_20261104_084105_a232c6。编译/前后哈希和原子替换通过。新根模块被现有发布*.py规则覆盖，没有封包。

云端独立内存Core探针实际seed2选出3012206上的80005/70001，原生index分别入库；未知/无index歧义拒绝不发布会话，开战8500分快照不受后改配置影响，旧含糊会话拒绝前通关事件0。PID1827974保持，待统一重启，不写真实存档、不Git或封包。

证据archive/boss_identity_20261104含源码前件、36检查、修前两版输出、哈希、payload/probe及远端回执。A82台账、小本本、看板和总清单已更新，累计127项source_verified。
