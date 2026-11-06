# A84：梦境跨首领角色锁定

2026-11-04。开发源码修复及隔离检查，已同步云端，待统一重启；未经游戏实测。

## 规则与修复

只禁止使用当前模式、当前轮换中其他首领的used_heroes。本首领自己的角色允许继续使用；last_heroes_cfg是预设而不是锁定来源，不合并所有历史首领，也不跨玩家/模式锁定。

Lua依据：ReserveBossChallengeTemplate.GetLockHero只加入bossIndex之外的锁定角色；HeroTeamInfoBossView对当前首领角色显示挑战标记。BattleBossChallengeAction.ChangeBossHeroTeam在请求之前就SetCacheHeroTeam，回调为空。

- 新增boss_challenge_team.check_team，共用当前严格快照，检查模式、有效目标和最多三个角色槽。0空槽保留，顺序不改；锁定交集返回客户端现有提示180021（BOSS_CHALLENGE_HERO_LOCKED）。读取/JSON/锁定记录结构错误直接失败，不把失败当作没有锁定。
- AccountDB.save_boss_challenge_hero_team在方法内部保存点中检查再写入；直接调用及嵌套调用失败也不会残留半次保存。
- 45108/45110使用BossTeamSaveOperation，共用既有严格预提交ACK和失败恢复出口。失败先错误ACK，再45201/45001/45101重建客户端提前覆盖的预设；成功只原生ACK，不重复全量刷新。没有独立预设差量协议，因此仅失败恢复使用状态快照。
- h_54030在A82的当前首领解析中检查实际heroes，复用同一次快照，不增加循环SQL；锁定拒绝在54003构造、battle_id发布、会话登记、现役编队保存之前发生，只返回非零54031。已保存的预设不免除开战重新检查。
- 本票没有修改最高分计算、通关后的used_heroes覆盖策略、结算奖励或预备编队协议。多会话在各自开战之后产生的新锁定，不由本票扩展为跨战斗预约锁。

## 保存协议缺项与兼容边界

真实codec检查发现cs_45108/sc_45109/cs_45110/sc_45111都不在当前SCHEMA中，旧Operation编码返回空，再用固定成功ACK掩盖问题；decode不能取得请求参数。x64/x86的p45_pb.lua也没有这四个定义，而两个平台的BattleBossChallengeAction仍有对应调用。

codec补入兼容定义：目标id/group_id为uint32 field1，heroes_cfg为repeated uint32 field2，ACK result为uint32 field1，使用setdefault不替换未来原生定义。字段名称来自Lua调用，字段编号布局按同组协议和既有服务端入口结构补齐，是兼容方案，**尚无原生描述或实机抓包证实这些编号**。内部往返证明的是本兼容格式的编码/解码及业务边界，不将其写成已还原官方协议。后续若抓到原生请求，应先核对这四条；不能以内部探针替代实机验收。

## 内部验证

tools/verify_boss_team_lock.py：10专项+12 A83重置回归，共22项通过。

真实内存AccountDB/Core/codec检查：两模式保存与开战拒绝其他首领锁定英雄，原预设/现役队伍/会话/序号不改；当前首领英雄可用；陈旧预设开战重查，重置释放后可用；预设、非当前轮换、另一模式及另一UID不错误加锁；目标/模式错误；SQL读取失败及JSON结构损坏；空/异常ACK、COMMIT被拒时回滚并恢复客户端缓存；直接及嵌套方法；跨期释放；兼容格式往返和空槽/长度。失败恢复顺序用有限Lua缓存赋值投影验证，没有运行Unity。

tools/verify_boss_identity.py：36项入口、归属、跨期、结算缓存回归通过。原同关两个首领的测试连续使用1084，现改成1084/1011两个已拥有角色，保留两个首领各自记分的全部断言，符合新锁定规则。

修前定义提取重放10专项产生15失败断言，其余正常对照仍通过。基线比较保留本票兼容codec，比较的是旧保存方法/Operation及旧开战边界，不声称还原旧完整网络环境。首版发现schema缺项；随后修正夹具对Connection/HandlerCtx状态字段的访问、故障注入误命中取证查询、假首领配置引用及两模式夹具残留。各版输出保留，最终after_checks_v5和identity_regression均通过。

## 云端与证据

account_db.py、operations.py、middleware.py、codec.py、boss_challenge_team.py已同步43.139.154.74:/game-server/aethergazer，覆盖前副本archive/boss_team_lock_20261104_090806_fddb5b。哈希、编译和原子替换通过。

云端独立内存Core探针通过两模式锁定拦截、本首领可用、ACK后预设恢复、拒绝不进入报文构造/会话登记、必要ACK空值回滚、重置释放和成功保存单帧。PID1827974保持，等待统一重启；不写真实存档、不Git、不封包。

archive/boss_team_lock_20261104包含覆盖前副本、修前/后输出、限定运行时差异、源码哈希、同步脚本及回执、独立探针。A84台账、小本本、项目记忆和协作看板已更新。
