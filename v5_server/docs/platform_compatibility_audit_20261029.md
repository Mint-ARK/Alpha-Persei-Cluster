# 云端 Linux、WSL、Termux、iSH 兼容性审查

日期：2026-10-29（Asia/Taipei）。源码基线：`0e31f21a3d93facc70df769617bd039c63323d35`。

本轮只审查和生成报告，没有修改业务源码、启动脚本、真实配置、存档或已发布的包，没有安装软件、重启服务器、修改 WSL 设置或发起实际游戏资源下载。

## 结论

| 环境 | 当前可支持程度 | 主要条件或障碍 |
| --- | --- | --- |
| 云端原生 Linux（优先常见 x86_64 / ARM64 发行版） | 核心具备运行基础；部署需配置 | Python 3.10+、可安装的依赖、可写的本地文件系统、可达的发布地址；公网端口与 Nginx 由部署者配置。本轮没有连接云服务器实测。 |
| WSL 2 | Linux 文件系统中的基础能力已隔离验证；Windows 挂载盘有明确阻断 | 当前 `/mnt/c` 挂载中配置权限校验会失败；NAT/镜像网络、Windows DNS/证书与 UDP 接入仍需分别联调。 |
| Android 原生 Termux | 当前安装器不支持开箱即用；核心有适配潜力 | Android/Bionic 原生依赖、私有目录、低位端口及客户端固定入口、后台运行。不能用 Linux wheel 或假 root 替代这些条件。 |
| iOS iSH | 实验性候选，尚不能标为纯手机可玩 | i386/musl 依赖缺少对应 wheel、x86 模拟成本、iOS 后台执行限制、锁和持久化语义。本轮无 iSH 设备验证。 |

“Python 进程能运行”“Web 面板能打开”“同一手机切到游戏后仍可持续游玩”是三个不同验收点。

## 本轮做了哪些验证

对 `v5_server` 根目录 **113 个 Python 模块**做 AST/编译检查，并盘点第三方导入和原生调用；核对三个 shell 入口。业务目录、清单和证书逻辑做定向阅读，不宣称遍历了所有游戏玩法链路。

本机现有 WSL：Ubuntu-26.04，WSL 2，x86_64，Python 3.14.4，systemd 正在使用。系统 Python 有 cryptography 46.0.5，未发现 Flask/Werkzeug；没有为测试安装依赖。因此本轮不是发布包锁定依赖组合的完整启动验收。

实际执行的探针：

| 位置 | 结果 |
| --- | --- |
| WSL Linux `/tmp` 独立目录，10 组 | Python/shell 语法、配置写入与重读、原子 JSON 发布与备份、共享/独占跨进程锁、协议编解码、UTF-8/zlib、SQLite 内存库与大 UID、2047 时间、回环 TCP/UDP、独立根证书 TLS 握手均有对应证据；还包含端口检查缺口和锁错误边界的复现。 |
| WSL `/mnt/c` 工作区中新建夹具，3 组 | 原子发布、文件锁通过；配置写入后权限仍是 0777，`load_config` 拒绝重读。探针记录了此异常，不能把记录中的 `status=passed` 误解为配置可用。 |
| WSL Linux `/tmp`，补充 1 组 | 注入 ENOSYS 后对象锁持续重试，探针主动取消三次重试后退出。 |

所有数据库测试使用 `:memory:`。TLS 使用新建测试目录中的独立临时根；没有读取或替换真实 TLS 私钥。TCP/UDP 只使用回环地址、随机高位端口。夹具保留，不执行删除。

原始证据位于 `archive/compatibility_audit_20261029/`：

- `source_inventory.json`、`native_calls.json`
- `probe.py`（可复用隔离探针）
- `wsl_native.json`、`wsl_drvfs.json`、`permanent_lock_error.json`
- 对应 stdout/stderr 及 `drvfs_fixtures/`

## 编号问题与能力边界

### C01 / P1 / 已复现：WSL 挂载盘上的配置无法重读

位置：`linux_manager.py:81`，`backup_write` 的 chmod 与 `load_config` 权限检查。

当前逻辑先对配置执行 chmod 0600，再要求 `st_mode & 0077 == 0`。本机 `/mnt/c` 夹具仍返回 0777，故刚保存完的配置会被启动入口拒绝。重复 chmod 不是该挂载方式下的有效修复。相同探针在 `/tmp` 返回 0600 且可读取。

这是文件系统能力差异，不是 SQLite 或游戏协议问题。后续应检测配置所在挂载的权限能力，明确支持方式；不能只按 `os.name == 'posix'` 推断权限可设置。去掉用户不需要的面板认证后，也应重新审视是否还需要这项强制检查。[微软文件权限说明](https://learn.microsoft.com/en-us/windows/wsl/file-permissions)

### C02 / P1 / 源码确认：Termux 原生依赖安装策略不合适

位置：`requirements.txt`、`linux_manager.py:131`。

现有 prepare 创建隔离 venv，再统一 pip 安装锁定依赖，包括 cryptography 50.0.0。该版本 PyPI 文件列表没有 Android wheel；Termux 原生环境使用 Android/Bionic，不能直接套用 manylinux 或 musllinux wheel。Termux 官方维护 python-cryptography，并对扩展链接 libpython 做额外修补。

即使用户先安装 Termux 的 python-cryptography，默认 venv 也不继承系统 site-packages；而仓库包版本未必等于我们的精确 pin，继续 pip 安装可能又走源码编译，不能把“pkg 已装”当成当前安装器已兼容。

后续需要明确原生 Termux 的依赖来源和 Python 环境方案，并验证 `cryptography.x509` 实际导入；不能只检测 Python 主版本。[Termux 官方构建](https://github.com/termux/termux-packages/blob/master/packages/python-cryptography/build.sh)、[PyPI 50.0.0 文件列表](https://pypi.org/project/cryptography/50.0.0/#files)

### C03 / P1 / 环境条件：手机高位监听不能自动解决客户端固定 443

位置：`linux_manager.py` 的默认端口、`server_net.py:1976`、`dns_server.py`。

默认使用 80/443，可选 DNS 使用 TCP/UDP 53。非 root 环境的实际可绑定端口取决于内核阈值、能力和平台策略，不能一律说所有手机均能或均不能绑定；应探测能力。Linux 官方默认特权阈值是 1024，可按网络命名空间改变。[内核说明](https://docs.kernel.org/networking/ip-sysctl.html#ip-variables)

即使将核心改为 18443 能运行，客户端仍会请求原有 HTTPS 域名的 443。DNS 只改变 IP，不传递端口；需要明确的客户端可配置入口或另一层确实可达的转发方案。普通手机 App 不能凭向导自动获得系统 DNS、路由或 root 权限。iSH 中的模拟 root 也不等于获得 iOS 主机特权。

同手机接入还要验证局域网 IP、127.0.0.1 与当前连接版本绑定，不能用“面板能打开”推断游戏各端口都打通。

### C04 / P1 / 官方平台限制：切到游戏后服务端需继续运行

iSH 是用户态 x86 模拟和系统调用翻译，并非一个不受限制的后台 Linux 虚拟机。官方为后台运行单独给出 `/dev/location` 方案；它需要真实的位置权限和对应后台条件，本轮没有启用或验证，不能视为稳定长驻保证。[项目说明](https://github.com/ish-app/ish)、[官方后台说明](https://github.com/ish-app/ish/wiki/Running-in-background)

用户目标是同一手机玩游戏，iSH 必须在离开前台后继续处理网络。tmux/nohup 只能处理终端会话，不能解除 iOS 的 App 执行限制。是否能稳定保持前台游戏、后台服务端，是 iSH 的第一项实机验收条件。

Termux 同样受 Android 后台/耗电/内存策略影响；官方还记录了部分 Android 版本的 phantom-process 和 CPU 限制。我们的 32 个网关线程不是 32 个子进程，不应混淆，但服务进程本身仍可能被系统回收。[Termux 官方说明](https://github.com/termux/termux-app#issues)

### C05 / P1 / 安装边界：iSH 依赖不能按 ARM64 手机选包

iSH 官方文档使用 i386 Alpine rootfs，程序面对的是模拟的 x86 用户空间，不是 iPhone 原生 ARM64。锁定版本 cryptography 50.0.0 文件列表没有 i386/i686 对应的 Linux wheel。不能把 ARM64 或 x86_64 wheel 放进去。

当前没有 apk 软件包路线，也没有针对 iSH/Alpine 的依赖说明。若改用发行版已构建的 cryptography，还要解决版本锁与 venv 可见性；若走源码编译，需要兼容的 Rust/C/OpenSSL/libffi 工具链，不能承诺现有 pip 命令可直接完成。Python 本身若低于 3.10，当前安装器会先拒绝。

这是锁定依赖的分发障碍，不构成“Python 核心算法一定不能在 iSH 运行”的证明。[iSH rootfs 说明](https://github.com/ish-app/ish)、[cryptography 安装说明](https://cryptography.io/en/latest/installation/)

### C06 / P1 / 已注入复现：对象锁将永久错误当成争用无限重试

位置：`cdn_integrity.py:320`。

非 Windows 分支执行 `fcntl.flock`，却对所有 OSError 统一等待后重试。ENOSYS、ENOTSUP、EBADF 等永久错误不会随等待恢复。默认没有 deadline/cancel 时循环没有上限；`publish_json` 等调用正是未传入 cancel/deadline 的路径。

探针注入 ENOSYS，观察到三次重复调用，最后由探针自己的取消信号终止。本轮没有挂起真实服务。后续应区分占用错误与不支持/权限/坏句柄，永久错误立即报告；不能通过取消一致性锁来“适配”平台。

### C07 / P2 / 上游源码疑点、设备待证：iSH flock 错误码边界

位置：`cdn_transfer_control.py:47`。

iSH 当前上游 `sys_flock` 转交文件系统的 flock 实现；`realfs_flock` 直接返回宿主 `flock()` 结果。争用时可能把宿主 -1 转成 guest EPERM，需安装版本实机验证，不能当作已证实故障。

我们的 FileLease 只把 EACCES/EAGAIN/EDEADLK 当成暂时争用，EPERM 会抛出。用 EPERM 注入已确认异常会传播，但这不是 iSH 真机结果。上游确实实现了 flock/fsync，不能写成“iSH 完全不支持文件锁”。[sys_flock](https://github.com/ish-app/ish/blob/master/kernel/fs.c)、[realfs_flock/fsync](https://github.com/ish-app/ish/blob/master/fs/real.c)

### C08 / P2 / 已复现：安装检查没有覆盖实际网关端口池

位置：`linux_manager.py:101`、`validate_config`；实际监听 `server_net.py:885`。

核心创建 32 个网关监听，默认占用 8102..8134（跳过游戏端口 8105）；安装检查只绑定基础 gw_port 与 game_port。探针占用第二个网关端口，check_ports 仍通过。

同一缺口还会允许 HTTPS 自定义端口落入网关池，并且只检查基础端口不超过 65535，没有校验池尾是否溢出。后续应以 `slot_id_to_port` 的同一映射生成完整检查集合。云安全组也必须按实际集合开放。

### C09 / P2 / 源码确认：向导仍采用旧版本、固定认证和单向参数覆盖

位置：`linux_manager.py:47`、`:90`、`:163`。

向导拒绝 `auto`、强制三平台统一数字版本，并强制 username/password_hash；而核心支持 auto、按渠道选择及库内 AI 配置。这与当前设计和用户明确“不需要面板密码”的要求不符，本轮不改。

本次命令行附加参数不会保存回 Linux 配置；setup 不支持字段式修改；保存启用 DNS 后没有 `--no-dns` 反向覆盖参数。重复配置默认又询问直接部署，没有保存独立部署模式。上述都是重做可复用引导时需统一的接口问题。

### C10 / P2 / 源码确认：systemd 只是一个可选管理能力

位置：`linux_manager.py:213`、`:282`。

前台核心不要求 systemd；安装系统服务显式要求 root 和运行中的 systemd，没有能力时会退出，而不是核心不能运行。本机 WSL 已有 systemd；不能一概认定 WSL 无 systemd。

模板硬编码 `/bin/sh`、`/etc/systemd/system`。Termux 原生路径不遵循普通 Linux 的 FHS，不能把这个 unit 当作 Termux 通用管理方案；iSH/OpenRC 也不适用。使用 PRoot 的发行版可缓解依赖 ABI 问题，但其 root 是模拟的，不能由此获得 Android 系统权限或完整 init。[Termux 路径](https://github.com/termux/termux-packages/wiki/Termux-file-system-layout)、[PRoot 限制](https://github.com/termux/proot-distro)

### C11 / P2 / 源码确认：错误地址兜底和本机模式限制

位置：`gen_cert.py:214`、`:255`；`linux_manager.py:47`。

地址发现只使用主机名和出站路由，无法推导 NAT 公网映射；全部失败时返回写死的 192.168.0.105。Linux 配置又拒绝 loopback 地址，因此不能直接保存同设备 127.0.0.1 模式。

现有明确填写公网 IP 的云部署可工作，但向导应区分“监听地址”“对外发布地址”“同机客户端地址”，不要用假 LAN 地址继续启动。WSL 的浏览器 localhost 转发也不等于游戏回传 TCP/UDP 地址均可达。[WSL 网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking)

### C12 / P2 / 源码确认：旧守护入口无限重启且硬编码版本

位置：`server_daemon.py:26`、`:48`。

该独立旧入口硬编码 443/8102/8105、229、account.db，不读取 Linux 配置；子进程无论正常或异常退出，都在一秒后无限重启。标准 `run_server.sh -> linux_manager.py -> main.py` 和 systemd 路径不调用它，但封包合同按顶层 *.py 收集，所以不能将它宣传为手机或无 systemd 环境的现成 rebooter。

### C13 / P2 / 源码确认：时间业务依赖宿主时区

位置：`birthday_service.py:30` 等 `date.today()`；`paid_entitlement_service.py:397` 的 `time.localtime(now-5*3600)`；`ai_proactive_service.py`。

生日、节气/问候和部分周期逻辑使用宿主本地日期/时区。Linux 向导与 unit 没有统一游戏时区配置。在 UTC 云主机与东八区手机上运行，同一时间戳的日期和刷新点可能不同。这是部署前置条件，不能假定使用者恰好设置了东八区；本轮未改变时区或业务口径。

### C14 / P2 / 源码确认：面板监控不能作为接入与资源预算证明

位置：`gm_reader.py:688`、`:715`、`:726`；`resource_storage.py:131`。

内存读取失败时会降级为 resource.getrusage 的历史最大 RSS，同时展示为当前和峰值；部分状态仍写死 HTTPS 443、UDP 6105。资源页把所有 posix 标为 linux，不区分 WSL/Termux/iSH。默认 32 个网关监听及主动下载默认 32 路、最高 128 路，在手机上尚无吞吐、内存或耗电测试数据。

可以保留同一套核心，但引导应探测能力并明确提示；监控应使用实际运行参数，不能据旧文案判断服务已经接通。不要把网关线程数与 Android phantom 子进程计数混为一谈。

## 已确认具备的跨平台基础

- 顶层业务模块主要使用 Python 标准库；本轮 AST 中真正外部 Python 导入为 Flask/Werkzeug/cryptography。`tools` 为项目命名空间，不是需额外安装的第三方包。
- Windows 控制台和端口进程清理调用有平台分支；Linux 核心不会要求 WinUI、python.exe 或 PowerShell。
- 协议整数字段使用 Python 整数及显式线格式；WSL 的大 UID/编码通过，不能由 iSH 32 位用户空间直接推断 UID 必溢出。iSH 仍需验证真实解释器和时间库。
- CDN 和存档生成锁有 POSIX 路径；Linux/WSL 文件系统已测试共享/独占锁。iSH 的实现与挂载路径仍需实测。
- 非 Windows 存档移除已有先备份、再移至 Trash 归档的逻辑，不强制调用 Windows 回收站。
- 存储默认值在非 Windows 使用部署目录下的 cdn_cache，发行包已使用中性路径；没有把开发机 D 盘作为 Linux 必需目录。
- TLS 证书生成与标准 TLS 握手可在本机 WSL 隔离目录运行；这并不替代手机上的根证书信任和客户端验收。

## 新引导的能力检测输入建议（尚未实现）

1. 环境：原生 Linux / WSL / 原生 Termux / PRoot / iSH，以及 CPU、libc、Python。
2. 文件系统：配置与库所在路径的 chmod、锁、原子替换与 fsync；移动端默认私有目录。
3. 接入：本机/局域网/公网、监听/发布地址、完整端口池、客户端固定 443 的承接方案。
4. 依赖：PyPI wheel 或发行版包；避免默认 venv 隔离掉平台修补过的扩展。
5. 生命周期：前台、实际可用的系统管理器、移动端前后台切换；无无限重启。
6. 时间：游戏时区与宿主日期的明确约定。

## 后续真机最小验收

Termux/iSH 均先记录 Python、架构、libc、安装方式和私有目录，再运行隔离探针。然后用新建测试档验证：原生扩展导入、客户端域名/证书/443、登录、战斗 UDP、切后台持续运行、重进恢复、存档持久化、CDN 同一对象并发与中断。不得用“安装成功”替代这些验收。

本轮没有跨设备完整游戏测试，也没有对 iSH/Termux 给出未经设备验证的跑分或可玩承诺。发现的源码问题保留待修，与安装流程讨论分开。
