# iOS公网选服列表落后一拍

部署目标43.139.154.74:/game-server/aethergazer，服务aethergazer.service。通过已存在的SSH密钥和已登记主机密钥连接；没有保存用户在聊天提供的密码。

## 现场证据

远端旧源码server_net.py、save_slot_manager.py与本机修前一致。手机HTTP与10038网关均识别为同一个IP，Nginx转发头正确，排除本次为会话IP分裂。

远端日志23:01:36/37、23:03:20/21等多组均为：两次SDK列表查询在350ms观察窗口后返回idle，之后才收到Slot1网关请求并进入CREATE_MODE。再次点击同一旧菜单入口时，后台已是CREATE_MODE，于是执行返回，客户端列表持续落后一拍。不能把模式已切换的日志当成客户端已收到新列表。

## 修复

- 首次/孤立列表查询保留350ms短观察窗口。
- 同一客户端在3秒内紧接上一次idle回复发起的后续查询，观察窗口为3秒，为SDK版本检查和公网网关连接留出时间。
- 网关动作完成后Condition立即唤醒列表查询，不固定睡满3秒；一旦动作开始，仍沿用12秒总期限和503超时回执。
- GET不执行新建/删除动作，不增加重试；不同客户端等待记录隔离。
- 新增SDK列表日志，记录客户端、状态及返回槽位ID，便于手机复测后对照。

这是一项有界时序兼容修复，不改变客户端Lua，也不宣称能覆盖任意网络延迟或所有原生SDK调度形式。手机端是否成功仍以用户实测和新日志为准。

## 验证与部署

13项离线并发检查通过，包括延迟500ms（超过旧350ms）后才到达的实际GatewayHandler帧、首次查询不变、客户端隔离、无网关时有界返回，以及已有删除/退出/失败唤醒链。

源码及真实account.db写前快照：

- /game-server/aethergazer/archive/ios_save_refresh_20261102_232257_bebc82
- /game-server/aethergazer/db_backup/ios_save_refresh_20261102_232257_bebc82/account.db

SQLite backup API生成一致性快照，quick_check=ok，13426688字节，SHA256 d3475cbff7b3fc6786960eba46ab8582062e9d1484cc71993b55e51d1f887498。仅更新save_slot_manager.py与server_net.py，服务PID由1533079变为1542004，active。Nginx、证书、端口、启动配置不变。

VPS实际HTTPS与TCP探针仅使用独立回环菜单会话：首次列表372.7ms；后续查询等待650ms后发10038，661.5ms返回“返回＋三个预设”；随后退出恢复原三项，369.0ms。没有选择预设或实际创建/删除存档，手机会话未被探针修改。

本地证据archive/ios_save_refresh_remote_20261102：部署源码前件、deployment.jsonl、live_probe.json及受控测试脚本。已发起iOS实机复测，等待用户反馈；未Git、封包或远程发行更新。
