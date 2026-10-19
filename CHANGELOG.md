# 更新记录 / Changelog

## v5.0.1 — 2026-10-19

### 修复与改进

- 修复永久换装/场景与部分编队状态的持久化、恢复和同步问题。
- 修复邮件领取、部分任务和养成链路的事务边界、必要回包及重复领取保护；改善刻印与钥从实例消耗后的同步。
- 修复已有任务条件满足时的模组初次开启、免费体力分档领取保护和管理面刻印库存读取。
- DNS 增加普通域名转发、UDP/TCP、大响应及失败应答支持，支持指定上游与 LAN 出站绑定。
- 保留战斗服时钟精度并增加入场阶段日志，便于继续定位实际入场耗时。
- CDN 使用公网解析和 TLS 验证；完整文件须通过长度与 MD5 检查后才发布缓存，部分 Range 或中断下载不会冒充完整文件。
- 两个封包入口共用资源合同与逐文件清单；补齐必需协议、角色编码配置、邮件模板和 iOS229/321 元数据，并降低无价值的版本轮询输出。

### 验证与使用范围

发布包通过防漏检查、ZIP CRC/清单/逐文件哈希、独立包内 Python 的 TLS、平台/版本响应、协议编解码、邮件模板加载及轮询检查。安装器与新版 GUI/游戏的完整安装验收仍需使用者复测；不将脚本检查称为所有玩法通过。

升级前备份数据库。发行证书与开发环境隔离，移动端按新宿主机指引部署证书。完整游戏资源与外置缓存不包含在服务端包内；历史 iOS311 清单缺失，不能据版本选择项认定其资源已归档。换装等资源可能在首次使用时按需下载，退出下载会留下未完成缓存。

### English summary

- Maintenance fixes for persistent skin/scene state, selected formation, mail, task, progression and entity-consumption flows.
- Improved DNS forwarding and TCP compatibility, battle timing precision, and CDN cache integrity.
- Required protocol/config assets are checked before publication; release downloads include SHA-256 checksums.
- Full game resources are external. The original iOS311 manifest is not included. Native installer/GUI acceptance remains separate from package checks.
