# A67：角色AI芯片装配归属与槽位

依据health_audit_20260922_round43_hero_chip_binding.md、当前50018/50019操作、ChipService、ChipCfg以及ChipAction/ChipData/HeroData消费代码。

## 修复

- 装配和卸下前确认目标角色真实存在；不存在的目标不再UPDATE零行却回成功。
- 装配要求真实已解锁的hero芯片，并用存档配置字段验证type_id=3、spec_char等于目标角色、role_type_id等于请求槽位。不能把别人的芯片或其他类别放入槽位，也不通过ID算术猜所属角色。
- secondary=0保留卸下语义，无须查芯片0；仍检查角色和槽位1—4。已有未解锁预建角色的合法预配置行为未新增限制，避免把原报告的待定政策变成新门槛。
- 保留其余槽位；损坏JSON、null/标量及非空数组不再悄悄覆盖成空装配。空数组是当前新角色默认值之一，兼容为未装配的空字典，和空字符串/NULL/空字典一样正常处理。
- 50019必要响应准备纳入Core事务，取消空编码的伪成功回执；写入或编码失败回滚角色状态和本次通知，之后可重试。

未改AI芯片解锁条件/费用（A69）、助战芯片前置（A68）或历史错误装配迁移。

## Lua与在线同步

ChipAction.EnabledHeroChip成功回调直接执行ChipData.EnableHeroChip(hero,chip,slot)，失败不改客户端槽位。因此服务必须按原请求整单验证，不自动调整角色或槽位后报成功。

芯片装配由独立ChipData管理。14007是HeroData.ModifyHero的单角色快照，不携带这份chip_state；移除原操作额外传入hero_id导致的无用14007。保留_refresh_frames(strict=True)收集真正的资产差量/待发通知，不新增50001或17023。合法装卸本身只需50019，已有成就消费者可能附带53003，不能丢弃其通知。

50001的hero_chip_state是冷恢复来源，包含4槽数组；客户端InitData按非零芯片的配置role_type_id放回槽位。专项同时核对该包及即时回调的有限列表投影，装卸后状态一致，不代表实机登录或战斗效果验收。

## A71仍待下一轮

验证中实际收到[50019,53003]，定位achievement_service._on_chip_equip仍按每次装卸给条件261加1。这是既有A71“拥有数量型成就被事件次数抬高”的相邻入口。本轮未改其计数、对账或领奖判据；保留并解码待发53003，不把该计数当作正确性验收。下一轮集中处理生产、消费、权威计数与领奖链路，避免只消掉通知掩盖错误进度。

## 验证

tools/verify_hero_chip_binding.py以真实Core/codec与内存SQLite执行9项：错误角色/槽位/类别/缺元数据、缺目标装卸、未知或锁定芯片、合法替换/卸下/重复及其他槽位保留、空默认兼容、坏状态拒绝、预建角色原政策、写入/回执失败回滚重试、当前Lua全量metadata装配。当前405条type3芯片、84个宿主、配置槽位1—4逐条通过实际Core装配。

另9项管理喵预设回归通过，共18项。沿用当前操作框架、仅替换备份旧enable_hero_chip方法时9项出现6个失败（含子用例）；不宣称完整旧框架的响应故障注入。初版把所有合法响应误断言为只有50019，2项因真实53003失败；查明A71后改为保留并解码现有差量，初版日志保留。

## 同步与记录

chip_service.py、operations.py已同步43.139.154.74:/game-server/aethergazer，写前哈希与本地前件一致，副本archive/hero_chip_binding_20261103_154502_416339。独立新进程加载磁盘源码，内存库检查错误绑定/未知目标拒绝、回执失败回滚、合法装卸、空数组和50001，全部通过。

PID1693172未变，待统一重启加载，未经实机。不写真实存档，不Git/封包。根archive/hero_chip_binding_20261103保存前件、修前后/预设回归日志、lua_contract.json、events.json和remote_result.json。发布小本本与A67台账已更新；386条中105 source_verified、3 partial、278 historical，为源码/脚本状态统计。
