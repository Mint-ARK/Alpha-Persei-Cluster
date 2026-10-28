# 更新记录 / Changelog

## v5.0.2 增补 — 2026-10-28

- 增加PC、Android、iOS独立清单选择、原生BASE派生/移除/恢复和官方完整版本探测。
- 增加主动资源下载队列、缓存索引去重、可调并发、被动下载优先和统一跨平台存储目录。
- 修复229客户端误收新版战令ID导致的登录Loading；按连接匹配业务版本，保留同周战令经验与领奖记录。
- 更新Windows启动壳、便携包、安装包和Linux核心包；四存档载荷清理API配置与本机状态。

内部脚本检查通过，未经完整游戏测试。


## v5.0.2 — 2026-10-26

- 统一可配置的新存档生成器，补齐初始角色/物品、教学、新手任务、活动任务和商店；预通章节补发固定首通奖励。
- 游戏选区支持三档预设建档、多存档切换和删除模式；启动壳、CMD及Web命令支持UTF-8路径。
- 修复首通重复发奖、连续关卡计时、签到状态、场景与角色初态、邮件资产刷新和头像解锁。
- 扩展装饰助力邮件、敌人击杀统计与世界观目录，修复换装卡池道具购买和剧情任务恢复。
- 增加生日礼赠邮件与最近生日补发、按角色模型路由、每日登录/生日/节气主动私聊，修复重登后聊天列表及历史恢复。
- 新增Web存档管理、预设/自定义参数联动、中文名称和UID校验，修复顶部存档列表读取与头像资源选择。
- AI配置与密钥统一存入各自存档，支持默认API选择和角色独立绑定，退役.env与旧全局配置；提供旧档升级工具。
- 增加战令档位、大小月卡权益和离线补发，战令按周刷新并补充经验，最高档赠礼按周期防重。
- 修正模型榜单来源、缓存初始化和失败提示；Windows与Linux包包含控制面板及四个存档：默认account.db、完整开荒、跳过序章、70级快速体验，构建时清理API密钥与本机存档登记。
- 虚构推演Issue #8补丁暂未合入，后续单独设计状态机重构。

已进行内部脚本检查，部分功能完成实测；其余未经完整游戏测试。


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
