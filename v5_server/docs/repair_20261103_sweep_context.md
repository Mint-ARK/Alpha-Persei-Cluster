# A162 扫荡结算上下文补齐

2026-11-03。复核第109轮报告和当前54038→_parse_battle_common→_sweep_settlement→_settle_frames→STAGE_PASS→Team/Cooperation/Hero链路，确认请求解析后仍未传入玩法/活动与连携/弥弥尔上下文。

## 修改

- 扫荡显式传递已解析的stage_type、activity_id及battle_context给结算引擎。
- 结算引擎支持显式上下文；有此参数时不再尝试读取不存在的出战会话。没有显式参数的原结算调用仍按原方法读取battle session。
- STAGE_PASS因此包含正确玩法类型、连携ID、弥弥尔和芯片；现有TeamServer将扫荡队伍写入对应类型/关卡键，现有CooperationSkillServer按原通关倍数规则计量。
- 不为扫荡创建临时出战会话，不引入新会话清理负担；没有连携/弥弥尔的本次请求保持0/空，不混入其他会话数据。

业务仅修改middleware.py。保留现有扫荡通关计量政策、玩法分类和编队键规则，不新增“全部高难”等兜底，不盲迁旧type0队伍或历史补发/补计量。连携细分资格A170等不在本轮处理。

## 验证

tools/verify_sweep_context.py共4项真实Core/codec/内存SQL检查，运行完整扫荡结算以及当前TeamServer、CooperationSkillServer和HeroService实际消费者。最终夹具下，保存的修前函数在隔离解释器复现3项失败；修后4项通过。

覆盖：主线type1/活动参数传递，实际STAGE_PASS字段及队伍1284/连携109504/弥弥尔1/芯片1,2写入，三次扫荡队伍通关计数3及连携normal_times3、熟练度3/胜场3/档案经验15；无上下文时保持旧会话入口；空配置不读其他会话；54039构造失败时队伍/计数/成长/资产同事务回滚，重试只执行一份。

初版夹具沿用旧报告hero_base_info字段，当前codec已采用p54_team_hero_info_net_rec.hero_id，现按实际字段构造。共享入口夹具已预先激活同域消费者，初次手动重复装配造成计数翻倍；最终隔离旧消费者、通过当前单例装配一次。之前输出保留，不将夹具失配计作生产BUG。

所有数据库写入均为内存数据，未经游戏实机；没有修改客户端Lua或生产存档。

## 同步

middleware.py已同步43.139.154.74:/game-server/aethergazer，源码副本archive/sweep_context_20261103_135958_66c5da。独立云端内存Core探针通过，含真实54038、事务失败重试、队伍/连携/弥弥尔及成长结果。

第一次云端探针又额外挂载成长观察适配器，与实际惰性单例消费者重复；改为通过当前HeroService.get_instance装配后通过，生产业务仅同步一次middleware.py，未因探针问题扩展修改成长代码。

服务PID1693172不变，未重启，待统一加载；未备份或写入真实存档。修复小本本及台账A162已更新，未Git/封包。

证据archive/sweep_context_20261103：before、before_final_fixture_v2.txt、final_checks_v4.txt、早期夹具日志、events.json、remote_result_v1.txt、remote_result.json及部署/探针脚本。
