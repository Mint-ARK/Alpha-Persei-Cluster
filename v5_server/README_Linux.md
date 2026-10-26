# Linux 核心包

包含 V5 Python 核心、网页控制台及其静态资产、配置和存档快照。Windows 电脑可以通过浏览器访问部署在 Linux 上的控制台；本包没有 Windows 启动壳。

## 首次安装

需要 Python **3.10+** 和 venv/pip，以及下载 Python 依赖的网络。解压 Linux 核心 ZIP，进入包含 `main.py` 的目录：

```sh
sh setup_linux.sh
```

向导自动创建本目录 `.venv` 并安装 `requirements.txt` 中的依赖，然后询问：

- 客户端可访问的 IPv4；云主机经过 NAT 时填写公网 IP。
- 客户端资源版本：229、311 或 321。
- 直接部署或已有 Nginx 反代；HTTPS、HTTP、网关、游戏及战斗端口。
- 是否开启内置 DNS，及需要挂载的存档路径。
- 控制台用户名和至少 12 字符的密码。

配置保存在 `linux_config.json`，权限为 600；密码只保存加盐摘要。重复运行向导会先把旧配置备份到同目录 `archive/`，密码留空可保留原密码。向导不打开或修改存档；填写的存档文件须已存在。依赖安装失败后可重新执行，不会无限重试。不会全局安装 Python、修改防火墙或自动接管 Nginx。

如果 Python/venv 缺失，先按当前发行版安装。Ubuntu/Debian 通常需要 `python3 python3-venv python3-pip`；OpenCloudOS/RHEL 可安装发行版提供的 `python3.11 python3.11-pip` 或更新版本。Python 版本仍需达到 3.10。没有可用 wheel 的架构可能需要发行版编译工具。可通过 `PYTHON_BIN` 指定已安装的解释器。本包采用在线安装方式，不内置 Linux Python 或离线 wheels。

## 启动与检查

```sh
sh manage_server.sh check
sh run_server.sh
```

检查包括配置、必要包文件、写入权限、本机 TCP/UDP 端口可用性；不检测云厂商安全组。退出前台进程按 Ctrl+C。运行用户必须能写入包目录、存档及其目录、日志与 CDN 缓存。直接监听 80/443、启用 DNS 53 时通常需要 root，可使用 `sudo sh run_server.sh`；也可选择反代模式使用高位端口。

`run_server.sh` 优先选择本目录 `.venv/bin/python`，读取保存的配置。附加参数会传给核心，重复的标量参数以命令行为准，例如：

```sh
sh run_server.sh --res-version 229 --no-capture-cdn
```

使用 `python main.py` 直接启动时不会读取 Linux 配置；日常请使用启动脚本，使控制台认证和保存的配置生效。旧部署仍可用启动脚本显式传入参数运行，但应先执行向导配置认证。

## systemd 后台运行

先停止原有同名服务或前台实例，再执行：

```sh
sudo sh manage_server.sh install-service
sh manage_server.sh status
sh manage_server.sh logs
sudo sh manage_server.sh stop
sudo sh manage_server.sh start
sudo sh manage_server.sh restart
```

`install-service` 是显式的系统安装操作：检查端口，生成并安装 `/etc/systemd/system/aethergazer.service`，设置开机启动并立即启动。已有 unit 会先备份到其同目录 `archive/`。服务以安装命令的当前用户运行，使用 sudo 安装时为 root。修改配置后重启生效；移动部署目录后需要重新安装 unit。没有 systemd 的容器或发行版请使用前台启动。

游戏监听循环发生异常时最多重试 2 次，第三次失败退出。进程异常退出后，systemd 每隔 5 秒重启一次，120 秒内最多启动 3 次，超过后停止；修复原因后可执行 `sudo systemctl reset-failed aethergazer.service` 再启动。正常 SIGTERM 会退出，不再进入异常重试。`sh manage_server.sh templates` 只生成模板，不安装系统服务。

## Nginx 与端口

反代模式默认 HTTP 关闭（`--http-port 0`），HTTPS 在 `127.0.0.1:18443` 监听，网关/游戏 TCP 与战斗 UDP 继续对外监听。因此不会抢占已有 Nginx 的 80/443。

向导生成 `linux_generated/nginx.example.conf`，由部署者按现有站点的域名、SDK SNI 和证书规则合并，并替换示例证书路径后检查加载。模板不会自动安装。反代保持 Host，传递 X-Real-IP/X-Forwarded-For；服务向客户端发布的网关地址使用向导填写的公网 IP。CDN 下载另选本机可绑定网卡地址。

直接部署默认 TCP 80/443、8102、8105，以及战斗 UDP 6105；内置 DNS 可选 TCP/UDP 53。请在服务器防火墙和云厂商安全组放行实际使用的对外端口。反代后端 18443 保持本机访问即可；客户端仍需访问对外 HTTPS 443。云安全组需要在云厂商控制台配置。

## Windows 远程控制台与客户端

浏览器访问 `https://服务器地址/web/gm_console.html`；直接部署若改了 HTTPS 端口，地址中需要带端口。反代时使用站点对外 HTTPS 地址。浏览器会提示输入向导设置的账号和密码。GM API 同时受保护，SDK 游戏登录不使用控制台账号。认证成功会在进程内缓存凭据摘要，面板轮询不会反复执行耗时的密码派生。建议始终通过 HTTPS 管理；旧 Windows 本地启动方式保持兼容。

客户端 DNS 指向 Linux 主机或现有 SDK 域名解析入口，并信任包内 `sdk_ca.crt`。服务启动时检查服务证书，不需要运行 Windows 证书安装脚本。直接部署可通过 `/cert` 获取证书。

大型游戏 CDN 资源按需获取；已有缓存可通过 `CDN_CACHE_DIR` 指定包含 `pc`、`ios`、`android` 子目录的目录。完整离线运行需要提前准备客户端所需缓存，安装向导本身也需要联网安装依赖。
