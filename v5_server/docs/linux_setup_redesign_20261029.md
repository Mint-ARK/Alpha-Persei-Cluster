# Linux 部署向导重构

本轮按五步流程实现环境检查、端口采集、使用方式与IP、可选Nginx、保存报告。

修改三个SH入口、linux_manager.py；新增linux_nginx.py。业务内核、游戏监听范围保持不变。main.py原本调用的check_ports接口不变，内部检查扩展到实际32槽位池。

配置升级schema2；旧schema1只在内存迁移，移除旧强制认证和固定资源版本参数，保存前自动备份。重复向导保留旧网络/存档参数。Termux首次18443/1880、127.0.0.1，无DNS/Nginx。配置不再包含秘密，取消旧的严格POSIX权限读取限制，兼容DrvFS。

setup不自动安装依赖；缺项汇总并退出。普通Linux可显式prepare；Termux提示使用系统python-cryptography。三个SH统一解释器选择。启动允许通过管理层--no-dns关闭已保存DNS；不注入业务/资源版本。不自动设置AG_ADMIN_AUTH_FILE，清除指向自身旧配置的继承项，其他显式高级认证不变。

Nginx自动接入管理独立aethergazer-game.conf，检查默认配置及PID，接受通常的systemd daemon/master_process参数，拒绝自定义配置实例及已有游戏域名冲突。使用游戏证书和上游CA校验；验证include、语法、reload后SNI健康探针。失败恢复本次文件，旧配置重载；新建失败文件归档，无物理删除。新启动的Nginx也不强行stop。Python后端未启动时只报告前端路由结果。

已更新README及封包必需项。27项隔离测试通过；WSL运行前26项并补验新增1项，三个SH通过sh -n。Python3.10语法、LF、修改模块的发布依赖覆盖及diff检查通过。Nginx调用以夹具模拟，没有真实修改Nginx、启动业务、写玩家库或重封包；Termux/iSH未实机验收。

证据：archive/linux_setup_redesign_20261029/acceptance.json；源码备份234336；测试夹具fixtures。
