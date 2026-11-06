# 当前 Linux SH / Python 启动链与参数

日期：2026-10-29。只读源码分析；本报告描述当前实现，尚未重构。

## 三个 shell 入口

三个脚本均`set -eu`、定位自身目录并`cd`，通过带引号的`"$@"`保留参数边界。最后用`exec`把shell进程替换成Python，不作为常驻rebooter。

### setup_linux.sh

1. 设置UTF-8及禁写pyc环境变量。
2. 优先取PYTHON_BIN；否则依次尝试python3、python3.14、python3.13、python3.12、python3.11、python3.10，选择第一个满足>=3.10的解释器。不是扫描后选择最高版本。
3. 用该解释器执行`linux_manager.py prepare`。
4. prepare创建或复用`.venv`，固定使用`.venv/bin/python`执行pip install -r requirements.txt。每次调用prepare都调用pip，不因包已存在而跳过这条命令。不会自动apt/dnf安装系统Python。
5. prepare成功后，exec `.venv/bin/python -B linux_manager.py setup "$@"`。
6. setup交互读取/询问配置，写linux_config.json及服务模板，然后退出；不会自动启动游戏服务。

PYTHON_BIN影响引导创建venv的解释器，setup后半段固定进入.venv。传给setup_linux.sh的参数只跟在setup后，不能传给prepare；管理解析器仅允许run接收额外参数，所以`sh setup_linux.sh --host-ip ...`当前会被拒绝。

### run_server.sh

1. 解释器优先级：PYTHON_BIN > 可执行的`.venv/bin/python` > python3。不会像setup一样寻找带版本号的备选解释器。
2. 检查Python>=3.10、三个直接库可导入；不会核对它们的具体版本或证书API能力，也不会自动安装。
3. 设置UTF-8环境，exec该解释器执行`linux_manager.py run "$@"`。
4. manager读取linux_config.json并校验认证，构造argv，通过runpy在同一Python进程执行main.py。

### manage_server.sh

选择PYTHON_BIN，否则默认`.venv/bin/python`；不存在时提示先setup。没有run脚本的python3兜底，也没有相同的版本/依赖预检。参数原样交给linux_manager.py。管理器的11个子命令见下表。

## Linux manager 的命令

| 子命令 | 读取配置 | 操作及输出 | 可接收额外内核参数 |
| --- | --- | --- | --- |
| prepare | 否 | 创建venv并运行pip | 否 |
| setup | 有旧配置则读 | 交互设置，保存配置，生成模板 | 否 |
| run | 有则必须先读且有效 | 拼接配置参数和本次参数，执行main.py | 是 |
| check | 是 | 检查部分包文件、存档和写权限、认证及端口绑定 | 否 |
| templates | 是 | 生成linux_generated下的systemd/Nginx模板 | 否 |
| install-service | 是 | 要求root和运行中的systemd；检查、写入/etc/systemd/system、enable --now | 否 |
| start | 本分支不读 | systemctl start aethergazer.service | 否 |
| stop | 本分支不读 | systemctl stop aethergazer.service | 否 |
| restart | 本分支不读 | systemctl restart aethergazer.service | 否 |
| status | 本分支不读 | systemctl status aethergazer.service --no-pager | 否 |
| logs | 本分支不读 | journalctl -u aethergazer.service -n 100 -f | 否 |

start等实际启动由systemd读取已安装unit，unit再运行run_server.sh，因此服务真正启动时仍会读取配置。它们不能管理任意前台运行的Python实例。

run分支还有一个现有特殊规则：无linux_config.json且无附加参数时拒绝启动；无配置但给了附加参数时允许按main参数运行。配置存在但无效时，附加参数不能绕过其校验。`--help`由manager自身argparse先处理，不会原样转给main的帮助。

## linux_config.json字段及转换

配置必须schema=1且字段集合完全相等，不接受缺失/额外字段。

| 配置字段 | 初始默认 | 给main的参数/用途 |
| --- | --- | --- |
| host_ip | 空 | --host-ip；向导要求填写可发布IPv4，拒绝127.0.0.1、0.0.0.0等 |
| res_version | 321 | --res-version；manager只接受229/311/321 |
| https_port | 443 | --https-port |
| http_port | 80 | --http-port；0关闭 |
| web_bind | 0.0.0.0 | --web-bind；仅HTTP/HTTPS监听 |
| gw_port | 8102 | --gw-port；网关池基础端口 |
| game_port | 8105 | --game-port |
| battle_port | 6105 | --battle-port；0关闭UDP战斗服务 |
| dns | false | true时追加--dns，false时不追加 |
| db | account.db | --db |
| schema | 1 | 仅manager校验，不给main |
| username | 向导默认admin | 仅认证模块，不给main |
| password_hash | 必填、PBKDF2摘要 | 仅认证模块，不给main |

选择Nginx模式会设置web_bind=127.0.0.1，HTTPS询问默认18443，HTTP默认0。这个部署模式本身没有保存成字段；重复setup又默认询问直接模式，DNS询问也默认n，不能视为完整保留全部旧选择。

配置写入采用临时文件+fsync+replace，旧内容备份到同级archive，chmod600。读取时拒绝符号链接及POSIX权限过宽。setup写配置前要求目标存档已存在；随后生成模板，不向存档写数据。

## main.py的全部24项参数

以下默认是直接调用main.py的默认值，和Linux管理器默认不是同一套。

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| --host-ip | 空，自动发现LAN地址 | 向客户端发布的游戏连接地址，不是网卡绑定 |
| --https-port | 443 | HTTPS入口 |
| --http-port | 80 | HTTP入口，0关闭 |
| --web-bind | 0.0.0.0 | HTTP/HTTPS监听IPv4 |
| --gw-port | 8102 | 网关池基础端口 |
| --game-port | 8105 | 游戏TCP端口 |
| --battle-port | 6105 | 战斗UDP端口，0关闭 |
| --capture-cdn | true | 开启捕获，现默认已开启 |
| --no-capture-cdn | false | 强制关闭捕获；同时出现时关闭优先 |
| --dns | false | 启用TCP/UDP53内置DNS |
| --dns-upstream | 未指定，可重复 | DNS转发上游IPv4 |
| --db | ACCOUNT_DB_PATH环境值，否则account.db | 默认挂载库 |
| --res-version | auto | 总控资源选择预设；数字兼容旧命令 |
| --game-version | 空 | 缺少渠道信息时的业务默认版本 |
| --resource-profile | 空列表，可重复 | pc/ios/android=方案名或短ID |
| --list-resource-profiles | false | 列出方案后退出 |
| --check-update | false | 检查上游更新后退出 |
| --sync-update | false | 同步上游更新后退出 |
| --client-assets-dir | 空 | 本机PC资产探测目录；本机探测仅Windows渠道路径适用 |
| --log-level | LOG_LEVEL环境值，否则INFO | DEBUG/INFO/WARN/ERROR |
| --self-check | false | 内核自检后退出 |
| --replay | 空 | 可选重放素材目录 |
| --frame-delay | 0.001秒 | 重放帧间隔 |
| --frame-override | 空 | 重放帧覆盖文件 |

main的--host-ip没有manager相同的IPv4类型校验；web-bind和dns-upstream则使用IPv4Address类型。main的参数检查、配置检查与最终网络绑定目前并未完全统一。

## 参数覆盖顺序

管理器执行的是：

```python
sys.argv = [main_py] + config_args(saved_config) + extra_args
runpy.run_path(main_py, run_name="__main__")
```

例如保存https_port=443、res_version=321时：

```sh
sh run_server.sh --https-port 18443 --res-version auto --db '中文存档.db'
```

main收到配置中的旧值和命令行新值；普通单值argparse参数以最后出现者为准。追加型参数按出现顺序累积；布尔开关不能一概按最后出现覆盖。未调用任何配置保存函数，本次覆盖不会写回linux_config.json，systemd重启也不会记住本次临时参数。

现有缺口：没有--no-dns，故保存dns=true后无法仅靠本次命令行关闭。认证经环境变量传递，不能通过--username等参数覆盖；这些参数根本不存在。

用AST只提取argparse声明和config_args做隔离探针：确认24参数、标量后值覆盖、中文名称保持、dns保持true及不存在--no-dns。没有执行main/manager业务或读写真实配置，结果archive/linux_start_chain_20261029/parameters.json。

## 配置文件与环境变量

| 来源 | 管理内容/作用 |
| --- | --- |
| linux_config.json | Linux启动参数和当前认证；主入口main不直接读取 |
| server_config.json | 已有共享配置，含资源目录；不是linux_config的别名 |
| resource_distribution.json | 各渠道清单选择、派生清单等配置 |
| 玩家数据库 | 账号状态、库内API密钥等；不作为Linux进程启动配置 |
| PYTHON_BIN | shell选择解释器，不是Python内核参数 |
| PYTHONUTF8 / PYTHONIOENCODING | Python启动时的编码环境 |
| AG_ADMIN_AUTH_FILE | manager设置为linux_config路径，启用Web管理接口认证；核心本来是可选认证 |
| ACCOUNT_DB_PATH | main的db参数默认来源；main解析后又将最终绝对路径写入该进程环境 |
| LOG_LEVEL | main日志级别默认来源 |
| AETHERGAZER_SERVER_CONFIG | 共享server_config路径 |
| CDN_CACHE_DIR | 资源总目录覆盖配置 |
| CDN_PC_DIR / CDN_ANDROID_DIR / CDN_IOS_DIR | 对应平台资源目录覆盖 |

环境变量不是一概高于配置：例如manager总会显式传--db，通常会盖过main从ACCOUNT_DB_PATH得到的默认值；资源目录则有明确的环境覆盖优先级。

资源选择另有一条优先关系：数字res-version会创建各渠道本次启动选择；显式resource-profile随后可覆盖对应渠道。auto不等于抹掉面板已保存配置，通常交由保存的渠道选择/默认策略决定。显式启动选择存于内存；Web应用渠道选择时可移除相应内存覆盖并保存。业务版本由连接渠道及原生BASE绑定，game-version是缺少信息时的默认值；数字res-version在未指定game-version时兼容地设置默认业务版本。

## 进入内核后的执行与生命周期

main模块顶部先导入业务模块、注册操作，之后才进入main函数解析参数。因此连--help也不应等价于绝无导入副作用的独立环境检查。

正常启动路径：参数/认证/非Windows端口预检 → 协议定义检查 → 设置默认存档及选档管理器 → 资源方案配置 → 日志/PID和端口处理 → 地址及CDN出站选择 → 证书准备 → 可选DNS → 数据库及核心装配 → 周期事件/退出信号 → 清单准备 → 可选重放 → 战斗UDP → 游戏TCP → 网关池 → HTTP/HTTPS → 游戏TCP主循环。

self-check不是纯环境探针：它在默认存档/选档管理器、资源选择和日志/PID路径之后，证书及显式get_db之前退出；不应宣传为保证零写入的检查。

main在非Windows环境会反向导入linux_manager.check_ports，所以linux_manager目前也被内核依赖，尚非完全独立的安装工具。check_ports只检查网关起始端口，没有覆盖实际32槽池，见前轮兼容性报告。

web-bind只控制HTTP/HTTPS；当前游戏TCP、网关TCP、战斗UDP仍绑定0.0.0.0。将host-ip或web-bind设为127.0.0.1，不会使全部服务自动限制本机访问。

没有常驻shell守护：run_server的shell被exec替换；manager的runpy和内核同进程。main游戏循环异常最多重试两次，第三次失败退出。systemd是另一层进程托管：Restart=on-failure、RestartSec=5、120秒最多3次启动；正常退出不重启。unit只调用run_server.sh，不带当前交互命令临时参数。旧server_daemon.py不属于本链路。

## 可简化的实际位置

1. shell只保留解释器定位、编码、目录与参数转交；统一三个入口的解释器选择规则。
2. Python统一参数定义、默认值、验证和持久化，避免linux_config321与main auto等差异。
3. 安装准备、可重复配置、运行、可选systemd管理可由单入口子命令组织；单纯改成一个sh不会自动解决当前不一致。
4. 为布尔参数提供双向设置，为修改配置提供非交互方式；分清保存与本次覆盖。
5. 按用户决定移除强制认证；将通用端口检查从Linux管理器中移到独立模块，检查完整实际监听集合。
6. Termux使用本机用途/高位端口并按能力安装依赖，不强制生成systemd/Nginx模板。

本轮未更改脚本、业务代码或运行配置，未启动服务。
