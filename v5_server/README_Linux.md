# Linux 核心包

包含必备的python代码和前端代码与资源，因为不包含Windows启动壳程序，相对轻量。

> **版本说明**：调整了配置向导中的不合理部分，简单优化了信息显示。

## 统一管理脚本 `server.sh`

原 `setup_linux.sh`、`run_server.sh` 与 `manage_server.sh` 三个脚本已归档并统一为单脚本 `server.sh`。执行 `sh server.sh`（不带参数）、`-h`、`--help` 或 `help` 均会直接输出用法帮助。

| 命令 | 实际调用 | 用途 |
| --- | --- | --- |
| `sh server.sh setup` | `linux_manager.py setup` | 环境检查与交互式配置向导 |
| `sh server.sh run [参数]` | `linux_manager.py run [参数]` | 按保存配置前台启动服务（支持本次临时参数覆盖） |
| `sh server.sh environment` | `linux_manager.py environment` | 仅检查 Python 解释器和依赖状态 |
| `sh server.sh prepare` | `linux_manager.py prepare` | 普通 Linux 显式创建 `.venv` 并通过 pip 安装依赖 |
| `sh server.sh check` | `linux_manager.py check` | 检查配置、文件权限和端口（服务停止时使用） |
| `sh server.sh templates` | `linux_manager.py templates` | 生成 systemd 和 Nginx 配置文件模板 |
| `sudo sh server.sh install-service` | `linux_manager.py install-service` | 安装并立即启动 systemd 守护服务（需 root / sudo） |
| `sudo sh server.sh start` | `systemctl start aethergazer.service` | 启动已安装的 systemd 服务 |
| `sudo sh server.sh stop` | `systemctl stop aethergazer.service` | 停止已安装的 systemd 服务 |
| `sudo sh server.sh restart` | `systemctl restart aethergazer.service` | 重启已安装的 systemd 服务 |
| `sh server.sh status` | `systemctl status aethergazer.service` | 查看 systemd 服务运行状态 |
| `sh server.sh logs` | `journalctl -u aethergazer.service -f` | 跟踪 systemd 服务日志 |

Termux 不保证可用，仍在测试中，所以谨慎操作。
发行依赖中的 cryptography 为50.0.0；Termux 使用仓库提供的原生 python-cryptography（需42.0.0以上），不要求与发行锁定版本完全一致。请不要使用 systemd 相关命令。

## 先检查，再配置

使用mkdir命令创建一个新的文件夹，使用cd命令进入后通过curl命令拉取包：

```sh
curl -fL --retry 3 \
> -o AetherGazer_Server_v5.0.2_Linux_Core.zip \
> https://github.com/Mint-ARK/Alpha-Persei-Cluster/Releases/download/v5.0.2/AetherGazer_Server_v5.0.2_Linux_Core.zip
```
使用unzip工具解压包：

```sh
unzip AetherGazer_Server_v5.0.2_Linux_Core.zip
```

使用sh命令运行向导：

```sh
sh server.sh setup
```

向导会检查需要的依赖状态，但是它不会主动安装，只会输出缺少的依赖名，安装依赖后重新运行即可。需要 Python 3.10+、SQLite 3.24+ 和 Flask、Werkzeug、cryptography。发行依赖固定版本见 `requirements.txt`。

普通 Linux 缺少依赖时，可执行：

```sh
# 先安装系统提供的 Python 3.10+、venv、pip。
# Ubuntu/Debian 通常为 python3 python3-venv python3-pip。
sh server.sh prepare
sh server.sh setup
```

`prepare` 创建或复用本目录 `.venv`，运行 pip 安装发行依赖；不会安装系统 Python。需要网络，没有可用 wheel 的架构可能需要系统编译依赖。也可自行准备环境，通过 `PYTHON_BIN` 指定解释器。解释器选择顺序：`PYTHON_BIN` → 本目录 `.venv/bin/python` → 系统中可用的 Python 3.10+。

Termux 使用系统提供的原生密码学库，不执行 `prepare`。以下命令逐行执行：

```sh
pkg update
pkg install python python-pip python-cryptography
python -m pip install "Flask==3.1.3" "Werkzeug==3.1.8"
sh server.sh setup
```

Termux 以同手机使用为目标，非Root设备不可以使用443、80等重要端口，建议自定义一个高位端口，首次建议 HTTPS 18443、HTTP 1880，发布地址为 127.0.0.1，内置 DNS 和 Nginx 默认不会启动。向导拒绝 Termux 的低位监听端口。在此之外，需要使用流量转发工具，将游戏请求的 80/443 转到选定高位端口；仅修改 DNS 不会改端口。建议将程序放在 Termux 私有目录。

## 五步向导

1. **环境检查**：依赖、必要文件、目录权限；展示当前保存的监听计划、端口绑定问题及可读取的防火墙参考。缺必需环境时会结束运行。
2. **端口输入**：游戏 TCP、32 槽位网关池、HTTPS、HTTP、战斗 UDP。每项显示已保存值或首次建议值，回车也要通过校验。端口冲突、池越界、权限不足均拒绝。HTTP 和战斗 UDP 可以填 0 关闭。运行中的旧服务也会占用端口，需要先自行停止；向导不会强杀进程。
3. **连接地址**：本机、局域网、公网分别提供提示，接受回环地址。公网 NAT 地址不必属于本机网卡。内置 DNS 可选；当前 DNS 转发实现需要发布地址同时是可绑定的本机非回环地址，回环模式（127.0.0.1）请关闭内置 DNS。
4. **Nginx**：可选同机反代。Python Web 后端设为 127.0.0.1，使用非 80/443 的端口。游戏 TCP、网关及战斗 UDP 仍监听 0.0.0.0。游戏客户端仍需信任包内根证书。
5. **核对保存**：再次检查全部监听端口和存档路径，确认后备份旧配置、原子写入 `linux_config.json`。不打开或修改存档。配置没有用户名、密码和资源版本字段。

重复执行 `sh server.sh setup` 即可调整配置，无输入回车保留已保存值。向导显示的是保存值。旧版 Linux 配置读取时自动迁移，保留网络/存档参数；保存时原文件留在同级 `archive/`。

资源分发继续使用原有各渠道配置和内核默认策略；配置向导不会覆盖 `resource_distribution.json`。

## 启动与检查

```sh
sh server.sh environment  # 仅检查环境，不访问存档内容
sh server.sh check        # 配置、文件权限、完整端口池；应在服务停止时执行
sh server.sh run          # 前台启动，Ctrl+C 退出
```

保存后的端口/IP 在下一次启动生效。首次默认挂载 `account.db`，重新配置保留原存档路径。所有运行账号须能写入程序和存档目录。

附加参数只在本次运行覆盖保存值，不写回配置，例如：

```sh
sh server.sh run --host-ip 192.168.1.20 --https-port 18443 --http-port 1880
sh server.sh run --db '新存档_01.db' --no-dns
```

`--no-dns` 由管理层转换，用于关闭保存的 DNS 设置；不能与 `--dns` 同时指定。直接 `python main.py` 不会读取 Linux 配置。明确设置的其他自定义 `AG_ADMIN_AUTH_FILE` 仍属于内核的可选高级认证，不由向导生成。

对于云服务器，在确保ufw已经放行好端口，还需要检查云服务器厂商的防火墙配置，这些东西向导扫描不到，需要自行确认。启动时内核还会重新检查端口，防止配置后被其他程序占用。

## Nginx 安全应用

自动接入要求已安装 Nginx、有配置目录及进程控制权限，使用默认配置入口。向导不会自动安装 Nginx。自定义 `-c/-p/-g` 实例、无法确认的 PID、其他站点已配置游戏域名时停止自动接管，应手动处理。

向导只管理指定 include 目录下的 `aethergazer-game.conf`，不干涉主配置和其他网站。默认目录 `/etc/nginx/conf.d` 必须已被主配置加载；会通过 `nginx -T`（含语法检查）验证，运行网站的云服务器在修改NG配置时请务必确认语法校验正确。

应用过程：

- 先检查现有配置，一定要备份本次涉及文件。
- 添加游戏域名专属片段，使用游戏证书。
- 再检查语法、冲突及片段是否被加载。
- 已运行的 Nginx 使用 reload；未运行时启动。程序使用热重启命令，不会执行 stop/restart命令，除非您自己愿意手动编写配置并重启应用。
- 通过带游戏域名 SNI、根证书校验的 HTTPS 探针确认命中新配置，再保存 Linux 配置。
- 失败恢复本次文件；如已提交加载则重新加载旧配置。新建的失败文件移入同级 `archive/`，没有物理删除。恢复失败会明确输出错误及备份位置，但是仍然建议操作前备份好ng配置文件。


防火墙请自行配置。外部仍需访问 HTTPS 443、游戏 TCP、完整网关池及战斗 UDP。默认 TCP 网关为 8102～8134、跳过游戏 8105，战斗 UDP 6105。反代后端 18443 仅本机使用。内置 DNS 只在启用时需要 TCP/UDP 53。

## 可选 systemd 托管

普通前台运行、Termux 不依赖 systemd。需要后台托管时，先停止已有实例，再执行：

```sh
sudo sh server.sh install-service
sh server.sh status
sh server.sh logs
sudo sh server.sh stop
sudo sh server.sh start
sudo sh server.sh restart
```

安装会备份旧 unit，写入 `/etc/systemd/system/aethergazer.service`，设置开机启动并立即启动。以安装命令的当前用户运行，sudo 安装通常为 root；unit 记录当前 Python 解释器。移动部署目录或切换 Python 环境后需重新安装 unit。

游戏监听循环异常最多重试 2 次；systemd 采用 5 秒间隔、120 秒最多启动 3 次的失败重启。修复原因后可用 `sudo systemctl reset-failed aethergazer.service` 重置计数。`sh server.sh templates` 会生成模板。

## 控制台与资源

控制台位于 `/web/gm_console.html`，无需向导密码。直连访问 `https://服务器IP:HTTPS端口/web/gm_console.html`；Nginx 自动片段使用游戏域名，应在浏览器可解析该域名到服务器并信任根证书后访问 `https://open.ys4fun.com/web/gm_console.html`。

游戏客户端也需信任包内 `sdk_ca.crt`，通过适当 DNS/转发接入。开启被动回源后，缺失的 CDN 资源按需下载；资源目录仍由既有共享配置管理，也可用 `CDN_CACHE_DIR` 指定带 `pc/android/ios` 子目录的根目录。本向导不改资产目录或下载队列。

CDN 被动穿透/捕获默认关闭；默认启动不会为它检查出站 IP 或从官方自动补齐启动清单。需要时使用 `sh server.sh run --capture-cdn` 或在控制面板显式开启。显式启动启用时若出站地址检查失败，会关闭被动捕获并继续启动本地服务；主动下载及上游更新按钮仍是独立的人工联网操作。本地缺失资源在捕获关闭时返回 404。

## CDN模式、缓存与云端带宽

这里的 CDN 开关指“客户端请求缺失资源时，服务端是否向官方请求”。它不关闭 Web/SDK 接口，也不关闭已有本地资源的分发。主动下载队列和手动官方更新属于显式联网操作，独立于被动回源开关。

当前源码已实现关闭、仅穿透、穿透并存盘两种行为；

| 模式 | 已有本地资源 | 本地缺失资源 | 保留资源 | 状态 |
| --- | --- | --- | --- | --- |
| 关闭CDN | 正常分发 | 返回 404，不自动回源 | 否 | 当前源码默认 |
| 仅穿透 | 优先使用 | 从官方获取并返回 | 否 | 手动 |
| 穿透并存盘 | 优先使用 | 从官方获取，校验并存盘 | 是（务必确认磁盘空间充足） | 手动 |

源码当前命令：

```sh
sh server.sh run                    # 默认关闭被动回源，仅分发现有本地资源
sh server.sh run --no-capture-cdn   # 显式关闭，亦适用于旧发行包
sh server.sh run --capture-cdn      # 开启穿透并存盘
```


### 缺缓存与版本切换


客户端只有在缺资源的时候才向服务器下载，文件缓存主要是给版本切换用的，而且在Windows设备上一般建议开启穿透下载，这样后续切换版本玩分发速度走本地回环网省流量，也快。
如果没有开启CDN，切换版本会碰到下载进度为0，提示资源损坏，报404错误，这是正常的，因为这时候的CDN的请求没有打出去，本地又没有缓存，就会报错。挂载CDN参数重启服务器和游戏即可。

版本不变也可能请求以前没下载过的场景、换装或玩法资源；切换版本后若本地资源齐全，也可以正常分发。

平台依据来自客户端请求字段和路径：版本接口读取 platformType / channelId（iOS官服 7 / Android官服 7 / PC官服 9），资源 URL 区分 pc/android/ios。渠道号7不能单独区分Android和iOS，平台字段优先，缺失时结合User-Agent识别。
### 云端传输与成本

如果是云服务器，除非是海外机房你有一台超级便宜的服务器包年，超大带宽、超多流量、超多SSD空间，否则，十分有九分的不建议在云服务器直接下载缓存或者进行穿透。这个流程本质上讲，项目程序是一个中转站，整个下载过程中要同时占用服务器的上行下行带宽，每切换一次版本就要重新下载一次，而服务器到客户端的传输成本是真实的。而亚太区贵的要命的带宽根本不建议用。如果想要云服务器提供服务端但是不想下载走这里，建议在代理规则中放行download.ys4fun.com的CDN域名，让这个请求直连，官方的资源都是活着的。

消耗具体来说可以举个例子： 100MB 文件向客户端发送一次，服务器应用层资源出站约 100MB。但是，为了这100MB可以分发，服务器还要向官方CDN索要这100MB的数据，也就是说，服务器套餐上的流量实际消耗了200MB，缓存可以消除第二次及以后向官方请求资源的流量消耗，但是客户端请求的流量不会。仅穿透模式则更糟，相当于每次缓存都没有命中，每次都要请求双倍流量，退一万步来说，即便是无限流量套餐，带宽也很贵的，还不快，没有这个折腾的必要说实话。

### WSL、Termux与出站地址

本机使用仍需承担下载流量、存储和处理开销，但数据不经租用云服务器中转。Termux 适合按需要选择是否保留资源；WSL 中的 Windows 客户端只要能访问 WSL 服务，即满足本地接入目标。

客户端发布地址 host-ip 与 CDN 出站地址用途不同。当前源码关闭捕获时不会因出站 IP 选择失败阻止启动；显式启动启用捕获时，出站检查异常会关闭捕获并继续本地服务。
