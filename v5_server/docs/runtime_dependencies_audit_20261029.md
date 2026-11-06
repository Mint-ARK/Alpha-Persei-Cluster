# Linux 发行包运行依赖与版本边界

日期：2026-10-29。依据实际 `AetherGazer_Server_v5.0.2_Linux_Core.zip`，与当前开发源码对照。

## 范围与证据

用户将范围明确为负责运行程序的代码，以 Linux 发行包实际 Python 文件为准。最终核对 ZIP 中全部 117 个 Python 文件：113 个顶层模块、4 个独立辅助工具；与当前对应源码逐字节一致。运行依赖结论以顶层核心为主，4 个独立工具不扩大核心必装依赖。资源提取、封包、测试、旧分析脚本不计入。

只读取 ZIP 中的 Python 和 requirements 文本，不解压、不导入业务模块、不启动服务、不打开真实存档。全部包内 Python 用 Python 3.10 grammar 参数解析通过；这不是在 Python 3.10 上执行了所有模块。SQLite 验证只用内存库。运行中保存的配置、证书及发行包均未修改。

证据目录：`archive/dependency_audit_20261029/`。最终适用证据为 `runtime_package_summary.json`、`runtime_package_files.json`、`runtime_imports.json`、`runtime_api_boundaries.json`、`runtime_sql_features.json`、`runtime_distribution_metadata.json`。该目录较早生成的宽范围清单已被用户缩小范围的指示取代，不作为本报告部署结论。

## 三种版本不能混为一谈

- 发布锁定值：requirements 的确切版本，当前安装器要求安装它。
- 接口下限：从实际代码用法和官方接口历史推出的必要条件；满足它不等于整套旧组合验收通过。
- 验证组合：已有探针或实际运行证据覆盖的组合。跨平台完整游戏验收另算。

不能把最低 Python 元数据、单个库的 wheel 标签、操作系统名称中的任何一个，直接当作整套程序的最低系统要求。

## 直接运行依赖

| 项目 | 当前发布要求 | 源码/接口边界 | 结论 |
| --- | --- | --- | --- |
| CPython | 启动入口明确要求 >=3.10 | save_generator.py:52 的 `str \| int \| None` 在没有推迟注解的类定义中求值，需要3.10；Path.is_relative_to 需要3.9 | 保留3.10为项目最低版本；本轮未发现需要提高到3.11的核心接口 |
| Flask | ==3.1.3 | server_net.py:1061 使用 send_file(max_age=...)，该参数名从2.0开始；其余注册、请求、Response等用法未建立更高的唯一API下限 | 2.0只是一项必要接口条件，不能据此下调发布依赖。当前保持3.1.3；3.1系列元数据要求Python>=3.9 |
| Werkzeug | ==3.1.8 | server_net.py:1985/2012/2025 使用 make_server、WSGIRequestHandler、threaded及ssl_context；Flask3.1.3元数据要求Werkzeug>=3.1.0 | 当前依赖组合的必要下限为3.1.0；3.1.8是发布锁定值，未验收降级组合 |
| cryptography | ==50.0.0 | gen_cert.py:85/95/107/119/390使用not_valid_*_utc（42.0.0）；:94/:112 verify_directly_issued_by（40.0.0）；:105 load_pem_x509_certificates（39.0.0） | 现用接口要求至少42.0.0；50并非所有现用接口的引入版本。>=42是必要条件，不承诺任意后续版本或整套42组合已通过 |
| SQLite库 | 通过Python sqlite3提供，无需独立数据库服务 | 多个业务写入使用带冲突目标的ON CONFLICT...DO UPDATE；扫描到129个对应SQL文本节点，未发现RETURNING、窗口函数、JSON SQL函数、STRICT或DROP COLUMN的更高版本用法 | 已识别SQL特性的必要下限3.24.0。检查sqlite3.sqlite_version，而非sqlite3命令版本；动态拼装SQL仍需业务验证 |
| Python SSL/OpenSSL | 通过Python ssl提供 | CPython3.10采用OpenSSL>=1.1.1基线；cryptography还可能使用wheel自带的另一份OpenSSL | 分别检查Python TLS和cryptography加载情况，不能只检查openssl命令 |

Python 3.8语法解析某些文件成功不表示3.8可运行：`X | Y`可被旧语法当作按位或表达式解析，但在注解求值阶段失败。单纯py_compile/AST不够证明运行版本。

官方依据：[PEP604](https://peps.python.org/pep-0604/)、[PEP644](https://peps.python.org/pep-0644/)、[cryptography42 X.509](https://cryptography.io/en/42.0.3/x509/reference/)、[SQLite UPSERT](https://www.sqlite.org/lang_upsert.html)、[Flask变更](https://flask.palletsprojects.com/en/stable/changes/)。

## 间接依赖完整清单（当前发布的三个直接依赖）

从当前开发解释器已安装、且与发行requirements同版本的三个发行包METADATA读取，未导入应用。表中最低约束是父包的依赖声明，不是本项目逐版本测试结果。当前requirements只锁三个直接包，间接包仍会随安装时间和Python版本解析变化。

| 包 | 来自哪里/条件 | 父包声明下限 | 本机已安装版本 | 本机该版本Requires-Python |
| --- | --- | --- | --- | --- |
| Jinja2 | Flask | >=3.1.2 | 3.1.6 | >=3.7 |
| MarkupSafe | Flask、Werkzeug、Jinja2 | 直接父项合并>=2.1.1 | 3.0.3 | >=3.9 |
| ItsDangerous | Flask | >=2.2.0 | 2.2.0 | >=3.8 |
| Click | Flask | >=8.1.3 | 8.4.2 | >=3.10 |
| Blinker | Flask | >=1.9.0 | 1.9.0 | >=3.9 |
| cffi | cryptography，CPython路径 | >=2.0.0 | 2.1.0 | >=3.10 |
| pycparser | 当前cffi，非PyPy路径 | 未指定数值下限 | 3.0 | >=3.10 |
| typing-extensions | cryptography，Python<3.11 | >=4.13.2 | 本机3.14环境不需要/未安装 | 安装目标环境时按元数据解析 |
| colorama | 当前Click，Windows条件 | 未指定数值下限 | 0.4.6 | 支持>=2.7但排除若干旧3.x；完整值见JSON |
| importlib-metadata | Flask，Python<3.10 | >=3.6.0 | 本机不需要/未安装 | 本项目>=3.10，正常不激活 |

Werkzeug也属于Flask的间接依赖，但项目直接使用它，因此已在直接依赖表单列。不启用的extras不计入：asgiref、python-dotenv、watchdog、bcrypt、Babel等。本项目AI请求使用urllib，未发现运行核心依赖openai、Google SDK、requests、aiohttp；协议编解码也未引入protobuf运行库。

安装器不能仅`import cryptography`即报告兼容：旧库可能能导入，却在访问UTC证书属性时失败。现有证书检查会把这类异常收入根证书需重新部署的诊断，宜先识别库版本/API能力，避免误导为证书本身损坏。

## 标准库及系统能力

全部导入根名称已列入runtime_package_summary.json（包含__future__等语言支持模块，不都是需要单独安装的包）。关键分组如下：

| 分组 | 模块 | 平台要求 |
| --- | --- | --- |
| 持久化/配置 | sqlite3、json、csv、pathlib、os、shutil、tempfile、io | Python带SQLite绑定；运行目录和存档目录可写；原子替换、同步写入和文件锁符合预期 |
| 网络/TLS | socket、socketserver、http、urllib、ssl、ipaddress | TCP/UDP、DNS解析、出站HTTPS；正常上游验证需要可用的系统CA |
| 协议/哈希/压缩 | struct、zlib、binascii、base64、hashlib、hmac、secrets | zlib和哈希算法实际可用；特殊FIPS环境的MD5策略另测 |
| 并发 | threading、concurrent.futures、contextvars、contextlib、weakref | 线程及文件锁支持；不要求多进程框架 |
| 时间/通用逻辑 | datetime、time、uuid、random、math、collections、copy、dataclasses、functools、re、string、typing、types、inspect | 有效时区；2047时间范围在32位环境另测 |
| 日志/启动/辅助 | argparse、getpass、logging、traceback、sys、subprocess、runpy、signal、mimetypes、plistlib、posixpath、errno | 安装器/可选命令按平台执行 |
| Windows分支 | ctypes、msvcrt及kernel32/psapi/shell32 | 有条件调用或异常降级；不应在Linux安装所谓同名第三方包 |
| POSIX分支 | fcntl、resource、pwd | fcntl用于一致性锁，不能随意关闭；resource是监控降级；pwd位于Linux服务模板生成路径 |

现用stdlib新接口中，Path.is_relative_to和线程池shutdown(cancel_futures=...)为3.9，均低于项目3.10门槛。未发现核心使用tomllib、TaskGroup、ExceptionGroup、file_digest等要求3.11以上的接口。本轮是静态识别，不是覆盖全部业务分支的执行测试。

外部系统工具：普通核心前台运行不要求Nginx、systemd、Node、Git、FFmpeg或独立openssl命令。现有sh入口需要POSIX shell，当前prepare需要venv/pip；systemctl/journalctl仅服务管理路径需要；netstat/tasklist/taskkill位于Windows启动清理逻辑。root权限不是高位端口核心运行的前提。

## 五个运行环境

| 环境 | Python/Web依赖 | cryptography/原生组件 | 当前兼容结论 |
| --- | --- | --- | --- |
| Windows | 发布运行时已配套，核心基线>=3.10 | 当前发布组合50.0.0；wheel架构必须匹配 | 已有主要运行经验；Linux包本身不包含WinUI壳，不把.NET作为Python核心依赖 |
| 原生Linux/云服务器 | Python>=3.10且ssl/sqlite3/zlib齐全 | 50.0.0存在x86_64/ARM64 glibc2.17+ wheel，以及musl1.2+的x86_64/ARM64 wheel；完整组合仍受CPython、cffi等约束 | 适合优先支持常见64位发行版。不能把cryptography的glibc2.17标签解读为整套程序在任意老发行版已可用 |
| WSL2 | 与所装发行版一致 | 用该发行版原生包/wheel | 已有Linux目录基础探针；Windows挂载盘chmod与当前配置权限校验冲突，另有网络入口配置 |
| 原生Termux | 平台Python>=3.10，Flask链可按平台环境安装 | Android/Bionic不能套Linux wheel；优先平台python-cryptography及其依赖；版本须>=42接口下限 | 当前venv隔离系统包及50.0.0精确锁定需改造。固定同手机用途、高位18443/1880，转发由外部工具承担；后台保护是用户前置条件 |
| iSH | 取决于Alpine rootfs实际Python版本，须>=3.10 | i386/x86用户态模拟；50.0.0没有对应Linux wheel；须查APK包是否提供>=42且能加载 | 安装阻断尚未解决；文件锁/时间和持续运行需设备验证。不能由iPhone ARM64硬件推断可装ARM64 Linux wheel |

当前无法给出一个覆盖全项目的“Linux最低内核版本”或“Android/iOS最低系统版本”。最低版本取决于所装解释器、libc、Termux/iSH发行版本、原生包和系统调用实现；本项目尚未建立该组合的设备验收矩阵。可定义推荐支持组合，再以能力检查明确失败项。

官方发布标签及平台依据：[cryptography50文件](https://pypi.org/project/cryptography/50.0.0/#files)、[Termux原生包](https://github.com/termux/termux-packages/blob/master/packages/python-cryptography/build.sh)、[iSH](https://github.com/ish-app/ish)、[WSL权限](https://learn.microsoft.com/en-us/windows/wsl/file-permissions)。

## 已发布网页的使用条件

面板是随Linux包提供的静态HTML/JS/CSS，部署服务器无需安装npm或Node。浏览器侧包含React/HeroUI/Tailwind组件；浏览器版本要求与服务端OS无关。Tailwind v4官方核心基线为Chrome111、Safari16.4、Firefox128；这是框架基线，不是本面板所有交互在这些最低浏览器上均已验证。较旧iOS即便iSH可启动，浏览器样式也可能先不满足。Windows内嵌面板另依赖WebView2，但不属于Linux核心依赖。

依据：[Tailwind兼容性](https://tailwindcss.com/docs/compatibility)。不评估前端构建工具和资源提取器依赖。

## 后续适配依据

1. 保持Python>=3.10；依赖检查覆盖ssl/sqlite3/zlib和实际证书接口。
2. 桌面/常规Linux保留发布锁定组合；单独验证Termux平台包组合，勿直接把requirements替换成无上限的>=42。
3. 只读检查sqlite3.sqlite_version和内存UPSERT能力，避免装完Python才在业务写入时报错。
4. 避免把缺API误报成根证书损坏；把临时锁占用与永久OS错误区分，防止无限重试。
5. 安装、可复用配置和前台运行不强制依赖后台服务管理器；保持用户已确认的手机高位端口/本机用途。

本轮未安装或降级依赖、未修改业务代码/配置/存档、未重启服务。元数据来自现有Python3.14.6环境；下限为代码与官方接口证据，不冒充降级实测。
