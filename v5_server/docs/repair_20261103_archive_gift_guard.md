# A155 一阶档案赠礼资格校验

2026-11-03。对照第108轮报告、当前14100注册Operation、ArchiveService及客户端herofilegiftpage.lua/heroaction.lua，修复非一阶礼物被消费并计入赠礼的问题。

## 修改

- ArchiveService.give_gifts在建档、扣资产及经验/赠礼史写入之前校验整份列表。只允许当前ItemCfg中type10/sub_type1物品，以及本体配置中明确的一阶偏好礼物；任一非法项整体失败，14101返回原有result2。
- 从当前Lua导出64种一阶礼物到已有archive_config.json，运行时只读随包JSON，不依赖Lua或不含sub_type的数据库路由表。货币、普通材料、后续阶段礼物30012及未知物品不会被一阶赠礼消费。
- 保留偏好10/普通5经验、按真实请求数量支付、重复项累计、1000封顶和所有权/余额规则。旧赠礼史不清理、不推测补偿。
- generate_archive_config.py新增礼物资格导出及--update-gifts-only，用于保留现有信赖等扩展配置；覆盖要求--backup-dir。本次只增加资格列表和来源字段，对照证明其余配置完全相同。

本轮未改operations.py、公共库存扣费、后续信赖赠礼或角色编码器。起初复核A156时发现当前build_single_hero_frame已经要求真实unlock=1行，不再使用历史缺行unlock1回退；没有将移除多余刷新作为本轮新BUG修复，也没有改A156台账状态。

## 验证

tools/verify_archive_gift_guard.py新增8项真实Core/Inventory/内存SQLite/codec检查，修前10个失败断言，修后8项通过。包括64种配置与当前Lua完整对照/全部合法礼物可用，非法单项与合法混合包整笔拒绝，偏好/普通/重复数量，封顶/未拥有，余额不足、保存失败回滚重试，以及直接服务拒绝前不写库/不广播档案经验。

配置保留检查确认原archive_config.json所有字段值未变，仅增两项。所有数据库写入为内存夹具，未经游戏实机。

## 同步与记录

archive_service.py、archive_config.json已同步43.139.154.74:/game-server/aethergazer。源码覆盖前副本archive/archive_gift_guard_20261103_122558_16b412。云端独立内存Core探针确认64种资格、非法混合整笔拒绝、直接服务校验、偏好/普通/重复数量和正确扣费经验35，全部通过。

服务PID1693172不变，未重启，待统一加载；未备份或写入真实存档。台账A155与发布小本本已更新，未Git或封包。

证据archive/archive_gift_refresh_20261103：before、before_checks.txt、final_checks.txt、config_preservation.json、events.json、remote_result.json及部署/探针脚本。
