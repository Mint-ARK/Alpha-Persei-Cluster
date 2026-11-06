# A44/A45：刻印重构和赋能预览确认

原报告：health_audit_20260921_round19_preview.md。当前EquipService与13034/13030注册操作仍有原缺陷，本次实际Core修前检查8项产生14个失败断言，日志保存在根archive/equip_preview_guard_20261103/before_checks.txt。

修复：

- A44：确认神系之前校验race_preview属于当前RaceEffectCfg.all对应的1、2、3、4、5、9。无预览、重复确认、非法预览返回result=2，不覆盖当前神系。放弃及重复放弃仍只清预览，允许再次生成合法预览后正常确认。随机生成与确认共用合法集合。
- A45：确认或放弃单条赋能必须命中当前预览序号；0、越界、空列表均返回result=2，不再取第一条。仅缺失字段保留默认第一条；操作层不再把显式0改写成1。合法选择第二项仍保存第二项，单条放弃只移除指定项。

客户端equipaction.lua只在result成功时调用EquipData.ConfirmRace/ConfirmEnchant，因此无预览不能幂等返回成功，否则客户端仍可能将预览0覆盖至当前神系。服务端失败返回后不触发成功刷新链。神系合法集合取本机x64和x86 RaceEffectCfg.all，未修改客户端。

tools/verify_equip_preview_guard.py使用AccountDB.new_save_workspace内存库、真实Core/Operation/EquipService和协议编解码，8项通过。覆盖重复/空/非法神系、合法六神系、重复放弃、不存在目标、赋能0/3/99/uint32最大值及确认/放弃、选择第二项、缺省第一项、冷13009恢复，以及SQL写失败回滚/重试。SQL与失败帧均验证；实际客户端乱序/网络重试未测。测试沿用真实Core事务，不写真实账号。

写前源码与台账副本位于根archive/equip_preview_guard_20261103/before；修后日志after_checks.txt。源码仅修改equip_service.py和operations.py（后者保留此前多维周期改动）。A40—A43及随机刷新池/费用/定向赋能均未在本票标记解决。

本轮只修本地源码，未同步云端、未重启或改存档、未Git或封包。

后续同步：2026-11-03用户授权后，已随成就领奖修复同步云端并重启加载，独立内存探针通过。详情见repair_20261103_achievement_claim_guard.md；实机仍待验证。
