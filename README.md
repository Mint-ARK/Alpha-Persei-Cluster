# 英仙座α星团 (Alpha Persei Cluster)

> 本项目由个人独立维护，近期事务较多，问题排查与修复进度可能较慢，感谢大家的耐心与理解。

> **问题排查**：请先查阅[问题索引与扫描报告](https://github.com/Mint-ARK/Alpha-Persei-Cluster/blob/codex/diagnostic-reports-20261020/reports/README.md)，按功能模块和异常现象找到对应报告，再结合其中的初步定位、相关文件与协议入口排查；也可将报告交给 Agent 辅助定位。提交 Issue 时，请附上客户端平台、版本、复现步骤及相关日志，并注明对应的 A 编号（如有）。

<div align="center">

**简体中文** | [English](./README_EN.md)

![Platform](https://img.shields.io/badge/Platform-Windows%2010%2B%20%7C%20x64-0078D6?style=flat-square&logo=windows)
![.NET Version](https://img.shields.io/badge/.NET-10.0%20(WinUI%203)-512BD4?style=flat-square&logo=dotnet)
![Python Version](https://img.shields.io/badge/Python-3.10%2B%20(asyncio)-3776AB?style=flat-square&logo=python)
![Frontend](https://img.shields.io/badge/Frontend-React%2019%20%7C%20TailwindCSS-61DAFB?style=flat-square&logo=react)
![License](https://img.shields.io/badge/License-Non--Commercial%20Research-grey?style=flat-square)
![Status](https://img.shields.io/badge/Milestone-Stage%201%20Completed-brightgreen?style=flat-square)

<p align="center">
  <b>针对 AetherGazer 制作的本地仿真服务端 (LocalServer)，旨在官方服务器关停后可以通过 HOST 劫持等方式再次驱动游戏运行。</b>
</p>
</div>

> 📖 **项目杂谈 / 开发者手记**：  
> 想了解本项目立项背后的心路历程、开发故事与作者的碎碎念？欢迎阅读博文：[《关于LocalServer》- MoriaRuRuka](https://moriaruruka.com/2026/09/24/411/)。

## 📦 当前发行版与更新日志

* **当前发行版**：`v5.0.5`（2026-11-18）— 请前往 [**GitHub Releases 发行页**](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases) 获取最新安装包、便携压缩包与校验文件（`SHA256SUMS`）
* **推荐下载**：Windows 优先在 [Releases 发行页](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases) 下载免安装便携压缩包（`AetherGazer_Server_v5.x.x_Portable.zip`，解压后运行 `LocalServer.exe` / `启动离线单机服务(GUI界面).bat` 或 `启动离线单机服务(控制台).bat`）；Linux 请在同页下载核心包（`AetherGazer_Server_v5.x.x_Linux_Core.zip`）并运行 `sh server.sh setup` 完成配置。
* **更新日志**：详细更新内容已拆分至专用文档 👉 [**完整更新日志 (`README_CHANGELOG.md`)**](./README_CHANGELOG.md) ｜ [精简变更摘要 (`CHANGELOG.md`)](./CHANGELOG.md)

### 平台与资源支持说明

| 平台 | 版本支持 | 说明 |
| --- | --- | --- |
| **PC** | **229 / 311 / 321 全部支持** | 三个版本均已配置资源清单。 |
| **iOS** | **229 / 321；311 无效** | **229 支持内购体验**。开发时遗漏抓取 iOS311 资源包，暂无对应资源清单。 |
| **Android 及其他未列出平台** | **需自行配置** | 受开发者设备与测试条件限制，未配置对应的内部资产清单文件，需使用者自行配置并导入。 |

---

## 目录

- [一、 项目概述与设计思路](#一-项目概述与设计思路)
- [二、 系统架构拓扑图](#二-系统架构拓扑图)
- [三、 快速部署与多端运行指南](#三-快速部署与多端运行指南)
  - [3.1 运行前置条件](#31-运行前置条件)
  - [3.2 Windows 部署与运行 (GUI / 纯 CMD)](#32-windows-部署与运行-gui--纯-cmd)
  - [3.3 Linux 核心包部署 (git / curl / scp)](#33-linux-核心包部署-git--curl--scp)
  - [3.4 Android 单机免改包运行 (需要 Root)](#34-android-单机免改包运行-需要-root)
  - [3.5 iOS 设备局域网接入指南](#35-ios-设备局域网接入指南)
- [四、 服务端启动参数说明 (CLI Arguments)](#四-服务端启动参数说明-cli-arguments)
- [五、 服务端核心架构：模块职责明细 (共 70 个模块)](#五-服务端核心架构模块职责明细-共-70-个模块)
- [六、 WinUI 3 桌面宿主与看门狗 (`LocalServer`)](#六-winui-3-桌面宿主与看门狗-localserver)
- [七、 Web 管理面板 (`web_src` / `static_web`)](#七-web-管理面板-web_src--static_web)
- [八、 常见问题与排障说明 (Troubleshooting)](#八-常见问题与排障说明-troubleshooting)
- [九、 项目阶段与现状说明](#九-项目阶段与现状说明)
- [十、 免责声明与开源合规](#十-免责声明与开源合规)

---
## 一、 项目概述与设计思路

本项目是一个针对游戏客户端的本地离线环境模拟尝试，主要包含以下设计考虑：

1. **本地离线沙盒**：
   网络交互、身份认证、资产结算、概率抽取与数据存储均在本地离线进行，默认网络通信集中在 `127.0.0.1` 环回网络或局域网内部。
2. **双进程托管方案**：
   桌面管理壳基于 WinUI 3 (C# / .NET 10) 构建，负责管理 Python 服务端子进程，利用 Windows 系统的作业对象 (Job Object) 机制在程序退出时自动回收底层进程，防止孤儿进程残留占用端口。
3. **渐进式迭代演进**：
   项目从早期的协议录制与重放逐步演进而来，目前接入了事件总线分发、动态 Protobuf 编解码处理与 SQLite 结构化存储，力求在脱机环境下尽量恢复常规游玩体验。

---

## 二、 系统架构拓扑图

```
+-----------------------------------------------------------------------------------------------+
|                                LocalServer (WinUI 3 / .NET 10)                                |
|  [Mica 原生界面]        [内嵌 WebView2 视窗]        [标准输出流式终端]        [基础环境检测]         |
|  [Windows Job Object 进程生命周期托管] -----------------------+                                  |
+---------------------------------------------------------------+-------------------------------+
                                                                | (进程生命周期绑定与输出重定向)
                                                                v
+-----------------------------------------------------------------------------------------------+
|                               v5_server 仿真内核 (Python 3.10+)                                |
|                                                                                               |
|  [TCP 网关: 8102]       [TCP 逻辑服: 8105]       [HTTPS 认证: 443]       [UDP 战斗: 6105]        |
|        |                       |                        |                       |             |
|        +-----------------------+------------------------+-----------------------+             |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |     server_net      | (网络包分发 / TLS 握手 / 本地静态资源响应)             |
|                     +----------+----------+                                                   |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |       core.py       | (全局会话调度 / 流水号维护 / 事务上下文拦截)            |
|                     +----------+----------+                                                   |
|                                |                                                              |
|              +-----------------+-----------------+                                            |
|              |                                   |                                            |
|    +---------v---------+               +---------v---------+                                  |
|    |   operations.py   |               | 25 Domain Services|                                  |
|    |  (130+ 业务操作类) | <-----------> | (抽卡/角色/装备/  |                                     |
|    +---------+---------+               |  成就/体力/邮件等) |                                   |
|              |                         +---------+---------+                                  |
|              +-----------------+-----------------+                                            |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |  Dynamic Codec/Gen  | (动态 Protobuf 编解码 / 差量变动帧推送)               |
|                     +----------+----------+                                                   |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |  account.db (SQLite)| (本地持久化数据表)                                   |
|                     +---------------------+                                                   |
+-----------------------------------------------------------------------------------------------+
```

---

## 三、 快速部署与多端运行指南

### 3.1 运行前置条件

* **操作系统**：Windows 10 及以上系统（推荐 x64 架构），确保设备上可用 Microsoft Edge WebView2 和 WinUI 3 运行库；Linux 发行版（兼容 WSL 2 / Ubuntu 20.04+、OpenCloudOS 9、Debian、Termux 等）。
* **环境依赖**：
  * **Python 3.10+**；
  * **.NET 10.0 Runtime / SDK**（若使用桌面启动壳）；
  * **Microsoft Edge WebView2 Runtime**（Windows 11 通常已预装）；
* **运行权限**：由于程序需修改系统 HOSTS 文件并监听 443、80 等特权网络端口，运行或安装时请使用管理员权限启动。

### 3.2 Windows 部署与运行 (GUI / 纯 CMD)

1. **便携版 GUI 启动（推荐）**：
   前往 [GitHub Releases 发行页](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases) 下载最新便携压缩包（`AetherGazer_Server_v5.x.x_Portable.zip`），解压后双击 `LocalServer.exe`（或运行 `启动离线单机服务(GUI界面).bat`）。
   > **注意**：启动内核可能需要等待几秒钟，初次启动时 Python 会向 Windows 请求网络访问权限，请点击“允许访问”。

2. **安装本地根证书**：
   在桌面客户端界面中根据提示点击安装自签根证书（用于让客户端信任本地 HTTPS 认证连接）；或者在服务端目录下找到 `v5_server/sdk_ca.crt` 证书文件，双击手动将其导入并安装到**“受信任的根证书颁发机构”**。

3. **配置 HOSTS 重定向**：
   以管理员身份编辑系统 HOSTS 文件（路径：`C:/Windows/System32/drivers/etc/hosts`），添加如下域名映射：

   ```hosts
   127.0.0.1  open.ys4fun.com
   127.0.0.1  prod-api-activity.ys4fun.com
   127.0.0.1  ta.ys4fun.com
   127.0.0.1  account.ys4fun.com
   127.0.0.1  m.bbs.ys4fun.com
   127.0.0.1  bbs.ys4fun.com
   127.0.0.1  skzy.ys4fun.com
   127.0.0.1  webstatic.ys4fun.com
   127.0.0.1  ys4fun-prod-pub.ys4fun.com
   127.0.0.1  download.ys4fun.com
   127.0.0.1  download-eo.ys4fun.com
   127.0.0.1  packaging.ys4fun.com
   ```

   将原发往官方的流量导向本地回环网络，这部分流量会被本地 Server 监听并响应。

4. **纯 CMD / 控制台启动**：
   确认您的设备已安装 Python 3.10+，解压便携包或源码包，在 `v5_server` 目录下右键选择“在终端中打开”，直接运行：

   ```powershell
   python main.py
   ```

   即可快速启动服务；亦可直接双击运行 `启动离线单机服务(控制台).bat`。

### 3.3 Linux 核心包部署 (git / curl / scp)

1. **在 GitHub 上直接获取并启动**：

   * **方式一：使用 Git 克隆发布仓库**
     ```bash
     # 1. 使用 git 克隆发布仓库
     git clone https://github.com/Mint-ARK/Alpha-Persei-Cluster.git

     # 2. 进入服务端目录
     cd Alpha-Persei-Cluster/v5_server

     # 3. 运行安装配置向导
     sh server.sh setup
     ```

   * **方式二：使用 Curl 直接拉取 Release 核心包**
     ```bash
     # 1. 创建并进入专属部署目录
     mkdir -p aethergazer && cd aethergazer

     # 2. 使用 curl 命令拉取 Linux 核心包（请以 Releases 发行页最新版本为准）
     curl -fL --retry 3 -o AetherGazer_Server_v5.0.5_Linux_Core.zip \
       https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.5/AetherGazer_Server_v5.0.5_Linux_Core.zip

     # 3. 解压发布包
     unzip AetherGazer_Server_v5.0.5_Linux_Core.zip

     # 4. 运行安装配置向导
     sh server.sh setup
     ```

2. **跨平台传输备选方案（若云服务器访问 GitHub 受限）**：
   * 在 Windows 电脑上前往 [GitHub Releases 发行页](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases) 下载最新的 `Linux_Core.zip` 核心包；
   * 使用 SCP 命令将压缩包推送至 Linux 服务器：
     ```bash
     scp file_path user@ip:server_path
     ```
   * 或直接在宝塔面板、1Panel 等工具中上传至 Linux 服务器解压后运行 `sh server.sh setup`。

### 3.4 Android 单机免改包运行 (需要 Root)

#### 3.4.1 预先准备

* **安装 adb（安卓调试桥）**：
  ```powershell
  winget install -e --id Google.PlatformTools --silent --accept-package-agreements --accept-source-agreements
  ```
  验证安装：
  ```powershell
  adb --version
  ```
* **取得设备 Root 权限**：
  Flyme 等系统可在开发者选项中手动开启 Root；一加、小米等解锁后通过 Magisk / KernelSU / APatch 授权 Root 的设备均可使用。
* **安装终端工具**：安装 [Termux](https://github.com/termux/termux-app)。
* **安装与配置代理工具**：安装 [sing-box](https://github.com/SagerNet/sing-box)，配置示例如下（通过 Tun 劫持游戏流量并转发到本地 Termux 对应的高位端口）：

```json
{
  "log": {
    "level": "info"
  },
  "dns": {
    "servers": [
      {
        "type": "local",
        "tag": "local"
      }
    ],
    "final": "local"
  },
  "inbounds": [
    {
      "type": "tun",
      "tag": "tun-in",
      "stack": "gvisor",
      "address": ["172.18.0.1/30"],
      "mtu": 1500,
      "auto_route": true,
      "include_package": [
        "com.yongshi.tenojo.ys"
      ]
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    }
  ],
  "route": {
    "rules": [
      {
        "action": "sniff",
        "sniffer": ["http", "tls"]
      },
      {
        "network": "tcp",
        "domain_suffix": ["ys4fun.com"],
        "port": 443,
        "action": "route",
        "outbound": "direct",
        "override_address": "127.0.0.1",
        "override_port": 18443
      },
      {
        "network": "tcp",
        "domain_suffix": ["ys4fun.cn"],
        "port": 80,
        "action": "route",
        "outbound": "direct",
        "override_address": "127.0.0.1",
        "override_port": 1880
      }
    ],
    "final": "direct"
  }
}
```

#### 3.4.2 方案实操：Android 纯单一设备单机状态（免改安装包）

1. **计算并准备系统根证书**：
   * **Android 系统证书命名规则**：系统底层信任凭据目录（`/system/etc/security/cacerts/`）规定，**证书文件名必须是该证书的主题哈希 (Subject Hash) + `.0`**。
   * 查看 CRT 证书主题哈希：
     ```bash
     openssl x509 -inform PEM -subject_hash_old -in sdk_ca.crt
     ```
     当前发行版自带根证书的主题哈希固定为：`ad2b2d99`，对应目标文件名为 `ad2b2d99.0`。
   * 推送证书到手机存储：
     ```bash
     adb push v5_server/sdk_ca.crt /sdcard/ad2b2d99.0
     ```

2. **执行证书挂载到系统信任区**：
   ```bash
   mkdir -p /data/local/tmp/cacerts
   cp -n /system/etc/security/cacerts/* /data/local/tmp/cacerts/ 2>/dev/null
   cp /sdcard/ad2b2d99.0 /data/local/tmp/cacerts/
   chown -R root:root /data/local/tmp/cacerts
   chmod 755 /data/local/tmp/cacerts
   chmod 644 /data/local/tmp/cacerts/*
   mount -o bind /data/local/tmp/cacerts /system/etc/security/cacerts
   ```

3. **屏蔽腾讯 ACE 反作弊 Root 探测**：
   * **原理**：普通应用检测 Root 的方式通常是探测 `/system/xbin/su` 是否对所有人可执行。我们将 `su` 权限从 `4755`（所有人可执行）调整为 `6750`（仅 root 与特定特权组可执行），游戏 App 探测时便会收到 `Permission Denied`，判定为“无 Root”。
   * 执行屏蔽指令：
     ```bash
     mount -o rw,remount /system
     chmod 750 /system/xbin
     chmod 6750 /system/xbin/su
     chmod 644 /system/build.prop
     mount -o ro,remount /system
     ```
   * 验证屏蔽效果：
     ```bash
     adb shell "su 10102 ls -la /system/xbin/su"
     ```
     理想输出应为：
     ```
     Permission denied
     ```

4. **环境还原（必要时复原）**：
   ```bash
   # 1. 还原证书挂载
   adb shell "su 0 umount /system/etc/security/cacerts"

   # 2. 还原 su 权限（解除 ACE 屏蔽）
   adb shell "su 0 sh -c 'mount -o rw,remount /system && chmod 755 /system/xbin && chmod 4755 /system/xbin/su && mount -o ro,remount /system'"
   ```

5. **状态检查与自检**：
   ```bash
   # 1. 检查已连接设备
   adb devices
   # 2. 检查系统证书是否包含我们的 CA
   adb shell "ls -l /system/etc/security/cacerts/ad2b2d99.0 2>/dev/null"
   # 3. 检查 su 权限状态（6750 为屏蔽态，4755 为暴露态）
   adb shell "ls -l /system/xbin/su"
   ```

6. **在 Termux 中部署并运行服务端**：
   * 启动 Termux，执行更新命令并安装必要组件：
     ```bash
     pkg update && pkg upgrade -y
     pkg install -y unzip curl
     ```
   * 使用通用命令拉取 Linux 核心包并启动向导：
     ```bash
     # 1. 创建并进入专属部署目录
     mkdir -p aethergazer && cd aethergazer

     # 2. 使用 curl 命令拉取 Linux 核心包（请以 Releases 发行页最新版本为准）
     curl -fL --retry 3 -o AetherGazer_Server_v5.0.5_Linux_Core.zip \
       https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.5/AetherGazer_Server_v5.0.5_Linux_Core.zip

     # 3. 解压发布包
     unzip AetherGazer_Server_v5.0.5_Linux_Core.zip

     # 4. 运行安装配置向导
     sh server.sh setup
     ```
   * 之后跟随向导设定管理员密码与运行参数即可。

### 3.5 iOS 设备局域网接入指南

1. **同局域网连接**：确保 iOS 设备与运行本服务端的电脑/服务器处于同一 Wi-Fi 局域网内；
2. **服务端开启 DNS 辅助解析**：指定 `--dns` 参数启动服务端（监听局域网 IP 并开启 DNS 辅助解析）：
   ```powershell
   python main.py --dns
   ```
3. **设置 Wi-Fi DNS**：在 iOS 设备的 Wi-Fi 高级设置中，将“配置 DNS”改为“手动”，并将 DNS 服务器地址指定为服务端的局域网 IP（例如 `192.168.1.100`）；
4. **安装自签证书描述文件**：使用 iOS 设备自带的 Safari 浏览器访问 `http://<局域网IP>/sdk_ca.mobileconfig`（例如 `http://192.168.1.100/sdk_ca.mobileconfig`），下载并安装描述文件，随后在“设置 -> 通用 -> 关于本机 -> 证书信任设置”中开启完全信任；
5. **VPN / 代理软件方案（如 [Shadowrocket](https://apps.apple.com/app/shadowrocket/id932747118)）**：
   若不便修改全局 Wi-Fi DNS，可在 Shadowrocket 配置文件的 `[Host]` 模块中添加重定向规则：
   * **图形化添加**：选择配置文件 -> 点击“Hosts” -> 右上角“添加” -> 类型选本地值：
     ```
     域名：*.soboten.com、*.ys4fun.com、*.ys4fun.cn
     本地值：你的电脑或云服务器 IPv4 地址
     ```
   * **文本编辑模式**：在配置文件的 `[Host]` 区块中直接填写：
     ```ini
     [Host]
     *.soboten.com = your_server_ip
     *.ys4fun.com = your_server_ip
     *.ys4fun.cn = your_server_ip
     ```

---
## 四、 服务端启动参数说明 (CLI Arguments)

|   分类   |               参数               |                       默认值                        | 合法取值与作用                                               |
| :------: | :------------------------------: | :-------------------------------------------------: | ------------------------------------------------------------ |
|   帮助   |          `-h`、`--help`          |                          —                          | 显示帮助并退出。                                             |
|   存档   |       `--db 文件名或路径`        | `ACCOUNT_DB_PATH` 环境变量；未设置则为 `account.db` | 指定启动挂载的数据库，支持中文、空格及绝对路径。             |
|   网络   |         `--host-ip IPv4`         |                 自动检测局域网地址                  | 下发给客户端连接的地址；可以填写回环、局域网或公网 IPv4。它不是监听地址。 |
|   网络   |        `--web-bind IPv4`         |                      `0.0.0.0`                      | HTTP／HTTPS 的本机监听地址；反代部署可用 `127.0.0.1`。       |
|   网络   |       `--https-port 端口`        |                        `443`                        | HTTPS SDK、版本接口及面板端口，范围 `1—65535`。              |
|   网络   |        `--http-port 端口`        |                        `80`                         | HTTP 辅助端口，范围 `0—65535`；`0` 表示关闭。                |
|   网络   |         `--gw-port 端口`         |                       `8102`                        | 多存档网关池的起始TCP端口，范围 `1—65535`，并须能容纳后续槽位。 |
|   网络   |        `--game-port 端口`        |                       `8105`                        | 游戏业务TCP端口，范围 `1—65535`。                            |
|   网络   |       `--battle-port 端口`       |                       `6105`                        | 战斗UDP端口，范围 `0—65535`；`0` 表示关闭战斗服。            |
|   DNS    |             `--dns`              |                       不启用                        | 启用内置DNS，同时监听UDP和TCP的53端口。                      |
|   DNS    |      `--dns-upstream IPv4`       |           `223.5.5.5`、`114.114.114.114`            | 指定DNS转发上游，可重复填写；上游端口固定为53。需配合 `--dns`。 |
|   CDN    |        `--cdn-mode 模式`         |                        `off`                        | `off`：关闭被动回源；`relay`：仅穿透、不存盘；`cache`：穿透并存盘。 |
|   CDN    |         `--capture-cdn`          |                       不启用                        | 兼容开关，开启穿透并存盘，相当于选择 `cache`。               |
|   CDN    |        `--no-capture-cdn`        |                       不启用                        | 强制关闭被动CDN回源，优先于其他CDN选项。                     |
|   资源   |       `--res-version 版本`       |                       `auto`                        | 选择资源预设。`auto` 使用各渠道配置，Windows可匹配本机PC清单；也可指定 `229`、`311`、`321` 等原生资源版本。 |
|   资源   | `--resource-profile "渠道=方案"` |                     不额外覆盖                      | 精确选择清单方案。渠道为 `pc`、`android`、`ios`；方案可用名称、短ID或 `auto`，支持重复指定。 |
|   资源   |    `--list-resource-profiles`    |                       不启用                        | 列出原生、派生清单的渠道、名称、短ID和就绪状态，然后退出。   |
|   资源   |    `--client-assets-dir 路径`    |                      自动查找                       | 指定客户端 `StreamingAssets` 目录，供Windows本机PC资源自动探测使用。 |
| 业务版本 |      `--game-version 版本`       |           未指定；内置业务默认值为 `321`            | 指定缺少客户端渠道信息时的业务默认版本；通常使用 `229`、`311`、`321`。实际游戏连接仍按渠道及原生BASE绑定。 |
| 更新工具 |         `--check-update`         |                       不启用                        | 检查PC官方版本与清单差异，打印结果后退出，不启动游戏服务。   |
| 更新工具 |         `--sync-update`          |                       不启用                        | 当前实现转交PC统一下载队列，默认连接本机已运行的HTTP服务；不是启动游戏服务。 |
| 登录推送 |       `--frame-delay 秒数`       |                       `0.001`                       | 登录洪流逐帧发送间隔，单位秒；可设为 `0`，建议使用非负有限数值。正常动态登录也使用此参数。 |
| 调试重放 |       `--replay 素材目录`        |                       不启用                        | 指定存在的归档素材目录，激活可选重放模块。                   |
| 调试重放 |    `--frame-override BIN路径`    |                       不覆盖                        | 在启用重放时，用指定二进制文件覆盖对应素材帧。               |
|   日志   |        `--log-level 级别`        |       `LOG_LEVEL` 环境变量；未设置则为 `INFO`       | 可选 `DEBUG`、`INFO`、`WARN`、`ERROR`。                      |
|   自检   |          `--self-check`          |                       不启用                        | 执行部分启动初始化，输出协议、版本和操作注册信息后退出，不启动业务监听。 |
| 预留接口 |      `--custom-domain 域名`      |                         空                          | 参数解析器接受，但当前 `main.py` 尚未使用该值。              |
| 预留接口 |    `--domain-proxy-mode 模式`    |                       `none`                        | 接受 `none`、`semi`、`manual`、`no_https`，但当前 `main.py` 尚未使用该值。 |
| 预留接口 |     `--site-conf-path 路径`      |                         空                          | 参数解析器接受，但当前 `main.py` 尚未使用该值。              |

---

## 五、 服务端核心架构：模块职责明细 (共 70 个模块)

|         分类         |     模块（相对于 `v5_server/`）     |                           功能简述                           |
| :------------------: | :---------------------------------: | :----------------------------------------------------------: |
|    启动与基础调度    |              `main.py`              | 服务端主入口；解析启动参数，装配数据库、业务服务和网络监听。 |
|    启动与基础调度    |              `core.py`              | 定义请求、连接与操作模型，注册操作并统一分发、执行和组装响应。 |
|    启动与基础调度    |           `middleware.py`           |   接收已解析请求，衔接业务处理、战斗会话、结算与兼容响应。   |
|    启动与基础调度    |           `operations.py`           |    注册游戏操作，执行参数解析、资格校验、状态变更和回包。    |
|    启动与基础调度    |           `event_bus.py`            |  定义业务事件与订阅机制，连接战斗、资产、任务、成就等模块。  |
|    启动与基础调度    |    `operation_notifications.py`     |    保存和恢复操作内待发通知，管理实例删除记录与失败回滚。    |
|    启动与基础调度    |           `lazy_timer.py`           |   按请求、心跳和登录触发时间推进，计算日、周、月周期边界。   |
|    启动与基础调度    |        `timer_listeners.py`         |   订阅时间事件，驱动体力、任务、商店、周常玩法和权益刷新。   |
|    启动与基础调度    |             `logger.py`             |           统一日志级别、文件记录和控制台输出入口。           |
|    启动与基础调度    |           `log_sifter.py`           |  整理协议与业务日志，抑制控制台重复心跳并提取资产变化摘要。  |
|    启动与基础调度    |       `multiplayer_policy.py`       |  统一限制公会、联机匹配等多人入口，保留单人玩法需要的协议。  |
|      网络与协议      |           `server_net.py`           | 装配TCP网关、游戏连接、SDK及HTTP(S)路由，管理连接与选档交接。 |
|      网络与协议      |         `battle_server.py`          |   处理UDP/KCP战斗连接、握手与帧交互，接收并缓存战斗结果。    |
|      网络与协议      |           `transport.py`            |     处理上下行帧头、流式切帧和ZLIB压缩，不承载业务规则。     |
|      网络与协议      |             `codec.py`              |      按协议描述将命名字段与Protobuf二进制载荷双向转换。      |
|      网络与协议      |         `decode_schema.py`          |     加载协议描述并提供字段索引、底层解码及协议检查能力。     |
|      网络与协议      |         `schema_default.py`         |  按字段类型生成结构完整的默认响应，避免不合格式的空成功包。  |
|      网络与协议      |             `login.py`              | 编排登录推送，读取当前存档动态状态，合并重复帧并完成初始化。 |
|      网络与协议      |           `generator.py`            |     按协议号和当前账号状态生成登录、查询及状态同步载荷。     |
|      网络与协议      |         `battle_payload.py`         |   构建战斗初始化数据，包括角色养成、装备、试用和系统角色。   |
|      网络与协议      |           `hero_codec.py`           |  将角色数据库记录转换为客户端角色结构，并兼容模板及旧字段。  |
|      网络与协议      |         `reserve_codec.py`          |      编解码预备编队与玩法队伍数据，支持读取和保存编队。      |
|      网络与协议      |        `formation_codec.py`         |    解析、校验并保存自定义编队方案，保留嵌套协议原始内容。    |
|      网络与协议      |          `team_server.py`           |    按玩法、关卡和队伍索引管理现役编队、连携及弥弥尔配置。    |
|      网络与协议      |           `dns_server.py`           |    为游戏域名提供本地DNS解析，转发其他域名的UDP/TCP查询。    |
|      网络与协议      |             `replay.py`             |   可选抓包重放旁路，加载归档HTTP与TCP素材并重建登录序列。    |
|     数据库与存档     |           `account_db.py`           |  统一SQLite建表、迁移、事务和数据写入，提供存档备份与导出。  |
|     数据库与存档     |         `save_generator.py`         |  按等级、UID、角色、引导、预通章节和邮件参数生成独立新档。   |
|     数据库与存档     |          `save_presets.py`          |       定义完整开荒、跳过序章和快速体验等共享存档预设。       |
|     数据库与存档     |       `save_slot_manager.py`        |  扫描和登记存档、分配网关端口，处理选档、新建、删除和挂载。  |
|     数据库与存档     |       `new_player_service.py`       |       维护新档初态、新手活动、关卡开放条件及任务恢复。       |
|     数据库与存档     |    `new_player_login_policy.py`     |     按新档策略选择登录推送，控制未开放系统的帧省略规则。     |
|     数据库与存档     |     `onboarding_operations.py`      |      接管新档签到、等级奖、累计奖和任务领取等新手操作。      |
|    控制面板与认证    |             `gm_api.py`             |   提供Web管理API，将查询交给只读层、修改交给对应领域服务。   |
|    控制面板与认证    |           `gm_reader.py`            |    以只读方式查询账号、角色、资产、邮件、玩法和服务状态。    |
|    控制面板与认证    |         `gm_save_scope.py`          |     为每个管理请求绑定目标存档，避免改变游戏当前挂载库。     |
|    控制面板与认证    |          `admin_access.py`          |      识别管理入口并安装认证保护，兼容旧Basic认证配置。       |
|    控制面板与认证    |       `admin_session_auth.py`       |     实现单密码登录、会话、注销、CSRF校验及失败次数限制。     |
|      资源与CDN       |         `client_version.py`         | 根据连接的平台和资源版本绑定业务基线，隔离跨渠道版本上下文。 |
|      资源与CDN       |      `res_version_manager.py`       |   管理业务版本、资源版本登记、本机探测和官方更新同步任务。   |
|      资源与CDN       |     `resource_distribution.py`      |    提供资源分发公共接口，统一暴露平台选择和清单管理功能。    |
|      资源与CDN       |       `resource_manifests.py`       |    管理原生BASE、派生清单、来源验证、平台方案和分发条目。    |
|      资源与CDN       |        `resource_storage.py`        |    管理资源根目录、平台子目录、磁盘选择与旧缓存读取位置。    |
|      资源与CDN       |        `fetch_manifests.py`         |    准备或下载各平台主资源和语音清单，保留平台隔离与备份。    |
|      资源与CDN       |           `cdn_proxy.py`            | 提供本地缓存分发及被动回源，支持关闭、仅穿透和穿透存盘模式。 |
|      资源与CDN       |         `cdn_integrity.py`          |  校验文件名、大小和摘要，处理临时文件及完整缓存的原子发布。  |
|      资源与CDN       |      `cdn_transfer_control.py`      |    协调跨线程、跨进程传输权限，使被动请求优先于主动下载。    |
|      资源与CDN       |      `cdn_active_downloads.py`      |    保存主动下载并发设置，控制共享文件任务的并发执行窗口。    |
|      资源与CDN       |       `cdn_download_index.py`       |     固定清单来源、分析资源范围并生成去重后的待下载索引。     |
|      资源与CDN       |       `cdn_download_queue.py`       |   管理持久化下载队列、任务分组、暂停取消、进度和速度统计。   |
|      资源与CDN       |         `http_streaming.py`         |      处理HTTP流式响应断连，确保响应和底层资源及时关闭。      |
|      部署与证书      |         `linux_manager.py`          | 执行Linux环境检查、交互配置、启动停止、状态及服务管理命令。  |
|      部署与证书      |          `linux_nginx.py`           | 生成和接入Nginx反代配置，执行备份、检查、热重载与失败恢复。  |
|      部署与证书      |            `gen_cert.py`            |  生成和检查根CA、服务端证书及移动端描述文件，维护分发证书。  |
|    资产与角色养成    |       `inventory_service.py`        | 统一货币、材料、实例及解锁资产的发放、扣除、折算和事件通知。 |
|    资产与角色养成    |          `hero_service.py`          | 管理角色等级、突破、升星、技能、权钥、钥从佩戴、神格及换装。 |
|    资产与角色养成    |         `equip_service.py`          |    管理刻印实例、穿戴、升级、突破、赋能、神系重构与分解。    |
|    资产与角色养成    |       `enchant_slots_util.py`       |          统一刻印赋能槽、效果和预览字段的下行结构。          |
|    资产与角色养成    |        `servant_service.py`         |       管理钥从实例创建、锁定、精炼、唤名、消耗和销毁。       |
|    资产与角色养成    |          `chip_service.py`          |    管理弥弥尔芯片、角色AI芯片、助战芯片及预设装配和解锁。    |
|    资产与角色养成    |        `fatigue_service.py`         |      计算体力上限、自然恢复、升级奖励和在线倒计时同步。      |
|    资产与角色养成    |      `daily_fatigue_codec.py`       |      计算定时免费体力的领取窗口、已领记录和客户端状态。      |
|    资产与角色养成    |         `trust_service.py`          |       管理角色信赖、好感经验、心情、等级突破及关系网。       |
| 任务、成就与新手福利 |      `achievement_service.py`       |     订阅业务事件推进成就，恢复进度并校验单项或批量领奖。     |
| 任务、成就与新手福利 |         `task_listener.py`          |   将通关、消费、养成、交互等事件转换为任务进度和差量通知。   |
| 任务、成就与新手福利 |    `task_cycle_notifications.py`    |      处理任务日周重置、周期记录及操作前的状态基线通知。      |
| 任务、成就与新手福利 |          `sign_service.py`          |    读取版本对应签到奖励，判断累计签到可领奖项及弹窗资格。    |
| 任务、成就与新手福利 |   `newbie_sign_reward_service.py`   |     校验普通存档的新手每日签到、游戏日、终点及重复领取。     |
| 任务、成就与新手福利 |  `newbie_level_reward_service.py`   |       按实际等级、活动目录和已领记录处理新手等级奖励。       |
| 任务、成就与新手福利 | `newbie_progress_reward_service.py` |      按任务类型、阶段及已领取任务事实处理新手累计奖励。      |
| 任务、成就与新手福利 |     `newbie_notice_service.py`      |       保存新手通知的消除标记，恢复相关客户端提示状态。       |
| 任务、成就与新手福利 |         `teach_service.py`          |     记录角色教学关通关事实，衔接教学进度与相关解锁条件。     |
|   商店、抽卡与邮件   |          `shop_service.py`          |      管理商店商品、购买、每日货架刷新及日周月限购状态。      |
|   商店、抽卡与邮件   |          `draw_service.py`          |   处理角色和钥从卡池、自选目标、共享保底、抽取与奖励入账。   |
|   商店、抽卡与邮件   |       `skin_draw_service.py`        |    处理换装和场景有限奖池、票券商品、抽取进度及保底恢复。    |
|   商店、抽卡与邮件   |        `activity_lottery.py`        | 为活跃宝箱附加换装、场景等奖励抽取，处理未拥有优先和重复折算。 |
|   商店、抽卡与邮件   |        `recharge_service.py`        |       处理充值商品发货、首充双倍、累充积分与档位奖励。       |
|   商店、抽卡与邮件   |    `paid_entitlement_service.py`    |   维护战令档位、周刷新、经验补给、月卡权益和遗漏奖励补发。   |
|   商店、抽卡与邮件   |     `periodic_gift_service.py`      |    处理连续多日礼包、季卡等订阅周期、每日发放及登录补发。    |
|   商店、抽卡与邮件   |          `mail_service.py`          |  管理收件箱、收藏信、特殊信件和附件领取，调用统一资产服务。  |
|   商店、抽卡与邮件   |    `support_cosmetic_service.py`    |     为启用助力邮件的存档补齐新版装饰礼包，避免重复投递。     |
|   商店、抽卡与邮件   |    `activity_content_service.py`    |   装配活动任务、剧情恢复和兑换商店目录，读取当前存档进度。   |
|  档案、图鉴与个性化  |        `archive_service.py`         |      统一角色本体档案、心链、超级心链、物语和轶事状态。      |
|  档案、图鉴与个性化  |      `illustrated_listener.py`      |     监听通关、剧情、角色和资产获取，推进图鉴及阵营收集。     |
|  档案、图鉴与个性化  |   `illustrated_story_service.py`    |        核对剧情阅读事实，发布相关剧情和插画解锁通知。        |
|  档案、图鉴与个性化  |       `worldview_service.py`        |           提供默认开放的世界观词条目录与阅读状态。           |
|  档案、图鉴与个性化  |       `enemy_kill_service.py`       |       汇总战斗及扫荡的敌人总击杀、分类击杀与图鉴统计。       |
|  档案、图鉴与个性化  |    `cooperation_skill_server.py`    |     统计单人战斗中的连携技能使用次数，区分对应关卡类别。     |
|  档案、图鉴与个性化  |       `peripheral_service.py`       |   处理名片、头像、气泡、大厅角色与场景、贴纸墙和偏好设置。   |
|  档案、图鉴与个性化  |         `profile_rules.py`          |  统一生日、场景持有、点赞历史、签名和大厅组件的校验与投影。  |
|  档案、图鉴与个性化  |       `sticker_wall_rules.py`       |   校验贴纸墙持有、互斥、布局和展示方案，供游戏与Web共用。    |
|  档案、图鉴与个性化  |          `oath_service.py`          |       处理誓约仪式、任务、剧情、爱称、合影和登录状态。       |
|  档案、图鉴与个性化  |      `oath_rights_service.py`       |     按誓约等级授予对应装饰权益，保存每项权益的发放记录。     |
|  档案、图鉴与个性化  |        `birthday_service.py`        |     处理生日资格、年度邮件、错过补发、纪念品及收藏恢复。     |
|     后宅与管理喵     |        `backhome_service.py`        |      管理游园街、后宅、食堂、烹饪、角色休息和互动事件。      |
|     后宅与管理喵     |        `dorm_gift_config.py`        |          读取手动家具赠礼的运行配置和允许赠送规则。          |
|     后宅与管理喵     |   `admin_cat_explore_service.py`    |       管理管理喵探索、派遣队列、区域、挂机收益和等级。       |
|     后宅与管理喵     |  `admin_cat_dispatch_progress.py`   |      保存成功派遣的累计及游戏日事实，恢复对应任务进度。      |
|     后宅与管理喵     |     `admin_cat_log_service.py`      |     按合法配置生成探索日志，并过滤旧无效日志的下行展示。     |
|     后宅与管理喵     |       `admin_cat_response.py`       |       准备探索状态和资产通知，统一必要回包的事务边界。       |
|     AI对话与问候     |        `ai_api_profiles.py`         |    在各自存档中保存多组API配置，解析默认及角色绑定配置。     |
|     AI对话与问候     |         `ai_bot_config.py`          |    提供AI默认模板、配置校验、角色模型路由和密钥脱敏视图。    |
|     AI对话与问候     |         `ai_bot_service.py`         | 适配不同模型接口，处理同步及异步聊天、连通性检查和失败兜底。 |
|     AI对话与问候     |       `ai_history_manager.py`       |   读取客户端聊天历史，结合存档记录补齐会话并保持角色隔离。   |
|     AI对话与问候     |       `ai_context_builder.py`       |  为模型构建临时环境和关系上下文，不将内部提示写入聊天记录。  |
|     AI对话与问候     |          `ai_calendar.py`           |    计算公历、农历节日、二十四节气以及玩家和角色生日事件。    |
|     AI对话与问候     |         `ai_translator.py`          |  转译贴纸语义，调度节日和生日贺信、礼物附件及角色问候偏好。  |
|     AI对话与问候     |      `ai_proactive_service.py`      |  在登录完成后生成每日主动私聊，合并生日节气并控制重登补送。  |
|     AI对话与问候     |       `ai_bot_leaderboard.py`       |      聚合公开模型榜单和目录，维护来源、刷新状态及缓存。      |
|      主线与关卡      |         `stage_service.py`          |    统一主线、支线、资源及活动关卡的分类、通关和状态生成。    |
|      主线与关卡      |     `chapter_v2_map_service.py`     |     维护新版主线地图事件、位置、资料阅读及时间回溯状态。     |
|      主线与关卡      |       `chapter19_service.py`        |        维护第19章扫描、线索获取、档案阅读及日期操作。        |
|      主线与关卡      |     `plot_progress_service.py`      |        从持久通关事实恢复章节任务计数和第19章完成率。        |
|      主线与关卡      |  `chapter_star_reward_service.py`   |       核验章节星级宝箱资格，统一奖励、领取记录与回包。       |
|    周常与高难玩法    |    `weekly_challenge_service.py`    | 提供梦境、黑区、多维、迭代校验及介质攫取的公共配置和状态接口。 |
|    周常与高难玩法    |      `boss_battle_identity.py`      |      根据当前轮换和战斗索引确定梦境首领，校验成绩归属。      |
|    周常与高难玩法    |      `boss_challenge_cycle.py`      |            统一梦境周期、首领轮换与跨期战斗校验。            |
|    周常与高难玩法    |      `boss_challenge_reset.py`      |      检查梦境重置资格，失败时恢复客户端成绩与模式状态。      |
|    周常与高难玩法    |      `boss_challenge_team.py`       |         校验梦境队伍，避免复用其他首领已锁定的角色。         |
|    周常与高难玩法    |        `mythic_affix_cfg.py`        |        提供黑区和失序深区的词缀、首领机制与轮换配置。        |
|    周常与高难玩法    |          `mythic_cycle.py`          |     计算失序深区半周周期，拦截跨期旧战斗并生成刷新状态。     |
|    周常与高难玩法    |      `mythic_final_service.py`      |     管理失序深区接力队列、挑战代次、开战资格和状态变更。     |
|    周常与高难玩法    |     `mythic_normal_service.py`      |      保存常规黑区精确星级条件，核对同轮星奖和重置状态。      |
|    周常与高难玩法    |   `core_mode4_reward_service.py`    |       核验迭代校验Mode4奖励、主子活动领史和通关任务。        |
|       多维变量       |       `polyhedron_service.py`       |   管理多维变量开局、选门、战后选择、商店、招募和终局流程。   |
|       多维变量       |       `polyhedron_account.py`       |      维护局外累计事实、角色和信标资格、神格及连携选择。      |
|       多维变量       |       `polyhedron_battle.py`        |   构建多维专用战斗数据，校验本局战斗身份并接收血量和结算。   |
|       多维变量       |        `polyhedron_flow.py`         |       协调多维操作、终端变更、状态快照与终局回包顺序。       |
|       多维变量       |        `polyhedron_login.py`        |      在登录完成前恢复当前探索、终端和已保存的结算状态。      |
|       虚构推演       |       `rogueteam_service.py`        |     提供虚构推演配置、地图生成、局外科技与局内探索规则。     |
|       虚构推演       |         `rogueteam_flow.py`         |    保存本局生命周期、结局和发奖状态，衔接商店及节点结算。    |
|       虚构推演       |      `rogueteam_transition.py`      |     校验节点、阶段和待处理选择的归属，控制合法状态转移。     |
|       虚构推演       |        `rogueteam_battle.py`        |        处理虚构推演专用开战与结算，隔离普通主线掉落。        |
|       虚构推演       |     `rogueteam_battle_facts.py`     |      记录真实战后能量和单次指令击杀，校验当前战斗尝试。      |
|       虚构推演       |       `rogueteam_effects.py`        |  实现已接入收集品的进房、即时奖励、次数失效与程序升级效果。  |
|       虚构推演       |       `rogueteam_tactics.py`        |    按同流派程序数量激活战术指令，保存指令及累计获取状态。    |
|       虚构推演       |     `rogueteam_achievement.py`      |     汇总胜利、结局和持有等事实，恢复并推送对应成就进度。     |
|       虚构推演       |        `rogueteam_login.py`         | 恢复当前局的地图、队伍、待选奖励和指令，应用新档教学跳过策略。 |
|         战棋         |        `warchess_service.py`        | 处理因果观测的地图探索、格子事件、宝箱和任务；当前覆盖首图链路。 |
|         战棋         |        `warchess_battle.py`         |       桥接战棋专用战斗，校验地图与战斗身份并回写结果。       |
|    小游戏公共服务    |        `minigame_service.py`        |    注册和分发常驻小游戏操作，调用各玩法的进度与结算服务。    |
|    小游戏公共服务    |     `minigame_point_service.py`     |       统一活动积分奖励目录、领取门槛、防重和资产差量。       |
|    小游戏公共服务    |    `offline_minigame_rewards.py`    |         按单机补偿政策开放指定多人常驻任务领奖资格。         |
|        决斗王        |       `autochess_service.py`        |   管理人机决斗王开局、商店、棋盘、对战、回合与会话持久化。   |
|        决斗王        |        `autochess_combat.py`        |   模拟棋子攻击、技能、护盾、召唤、死亡队列及最终战斗结果。   |
|        决斗王        |     `autochess_preparation.py`      |     执行购买、出售、回合开始和准备结束等时点的技能效果。     |
|        决斗王        |       `autochess_special.py`        |        生成并使用局内道具，处理目标、升级和额外效果。        |
|        决斗王        |       `autochess_progress.py`       |         保存首通、徽章和凭证事实，恢复对应任务进度。         |
|        决斗王        |        `autochess_login.py`         |     恢复棋盘和待结算状态，处理缺少可信战报的旧孤立对局。     |
|        刺桐巷        |         `quanzhou_quest.py`         |      维护主子任务、前置、参数目标、功能解锁和任务奖励。      |
|        刺桐巷        |      `quanzhou_collection.py`       |      维护航行通关、照片、航行技能、见闻及相关任务投影。      |
|        刺桐巷        |        `quanzhou_service.py`        |      保存地图位置，桥接海战和角色战并校验当前挑战身份。      |
|       夏日竞技       |      `summer_board_service.py`      |         管理棋盘初态、开局、存档、骰子及进退场操作。         |
|       夏日竞技       |     `summer_board_movement.py`      |      执行六边形地图移动、地块事件、自动移动和回合结束。      |
|       夏日竞技       |      `summer_board_chance.py`       |        处理机会事件、局内商店、道具候选以及领取确认。        |
|       夏日竞技       |        `summer_board_npc.py`        |       规划NPC回合路线，处理相遇、保护效果与演出事件。        |
|       夏日竞技       |       `summer_board_props.py`       |       校验并应用任意骰子、交换、路障、炸弹等局内道具。       |
|       夏日竞技       |       `summer_board_games.py`       |       处理QTE和骰子竞技的结果、次数、奖励及局内道具。        |
|       夏日竞技       |      `summer_board_nested.py`       |        桥接嵌套小游戏和战斗，隔离当前机会与棋盘会话。        |
|       夏日竞技       |     `summer_board_progress.py`      |        保存局内任务、地图通关历史，恢复外部奖励任务。        |
|    其他常驻小游戏    |       `rogue_card_service.py`       |    管理诡谈夜话主线与挑战的对局生命周期、退出和状态恢复。    |
|    其他常驻小游戏    |      `rogue_card_progress.py`       |     统计诡谈夜话局内操作、挑战条件及杂谈阅读，推进任务。     |
|    其他常驻小游戏    |     `resident_music_service.py`     |      记录浮光绎曲等常驻音乐成绩、完成状态、任务和领奖。      |
|    其他常驻小游戏    |        `pinball_service.py`         |     管理海拉弹珠关卡、技能装配、挑战积分和普通战斗桥接。     |
|    其他常驻小游戏    |       `kadas_race_service.py`       |       维护卡达斯假日赛进度和配装，区分当期与常驻奖励。       |
|    其他常驻小游戏    |      `vehicle_ball_service.py`      |       管理极限机装关卡、载具成长、图鉴及对应任务奖励。       |
|    其他常驻小游戏    |         `hodur_service.py`          |     管理谧光叙录普通章节的探索、事件、重置、战斗和任务。     |
|    其他常驻小游戏    |        `hodur_challenge.py`         |     管理极限研习连续战斗、队伍词缀、血量继承和挑战评分。     |
|    其他常驻小游戏    |          `ash_service.py`           |      管理列车大劫案首通影片、花絮积分、剧情提示和任务。      |
|    其他常驻小游戏    |       `summer_pub_service.py`       |       管理食与异世界的战斗剧情、料理、弹珠和伙伴任务。       |
|    其他常驻小游戏    |         `osiris_service.py`         |      管理战术研习记录与沙盘关卡、机制记录、积分和任务。      |
|    其他常驻小游戏    |         `skuld_service.py`          |      管理溯梦之隙节点、拼图、首通、章节收藏和任务进度。      |
|    其他常驻小游戏    |    `sphero_schedule_service.py`     |      管理薇儿常驻活动的每日安排、委托队列、计时和领取。      |
|    其他常驻小游戏    |     `sphero_battle_service.py`      |   管理薇儿剧情、训练与Boss战斗、熟练度、日奖励额度及任务。   |
|    其他常驻小游戏    |     `sphero_leisure_service.py`     |    管理薇儿烧烤、解谜、自动解谜消耗、花环角色和收集奖励。    |
|     随包命令工具     |     `tools/download_assets.py`      | 统一PC、Android、iOS主动下载命令，支持连接服务端或独立执行。 |
|     随包命令工具     |    `tools/download_pc_assets.py`    |       保留PC下载兼容入口，直接运行时转交统一下载队列。       |
|     随包命令工具     |   `tools/download_ios_assets.py`    |      保留iOS下载兼容入口，直接运行时转交统一下载队列。       |
|     随包命令工具     |   `tools/upgrade_legacy_saves.py`   |       检查旧档结构并显式迁移旧AI配置；应用前备份存档。       |

## 六、 WinUI 3 桌面宿主与看门狗 (`LocalServer`)

桌面端采用 **WinUI 3 + .NET 10** 构建，主要起到进程管理与内嵌展示作用：

* **作业对象进程托管 (Job Object Supervisor)**：
  * 基于 Win32 原生 `CreateJobObject` 与 `SetInformationJobObject`，配置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`；
  * 当桌面宿主程序退出（无论是正常关闭还是异常崩溃）时，操作系统内核会自动清理绑定的 Python 子进程，降低端口被孤儿进程占用的概率；同时 Python 服务端内部也具备启动时排查清理残留端口的保护逻辑。
* **内嵌 WebView2 视窗**：
  * 内嵌 Edge WebView2 组件，在服务端就绪后直接加载管理面板网页。
* **标准流重定向**：
  * 异步读取 Python 的 stdout 与 stderr，并在界面文本框中进行流式输出，方便观察运行动向。

---

## 七、 Web 管理面板 (`web_src` / `static_web`)

管理面板基于 **React 19 + TypeScript + Tailwind CSS** 构建，经 Vite 打包后输出至 `v5_server/static_web`。

> **说明**：管理面板的设计相对克制，主要定位为**数据状态浏览与查看**。虽然代码中留有部分编辑接口，但由于缺乏全面校验并不保证可靠，因此建议将其主要作为状态观察工具使用。

* **静态图片转换**：相关素材已转换为体积更小的 WebP 格式以便于网页加载；
* **主要浏览板块**：
  1. **状态总览 (Overview)**：展示服务在线状态、基础连接信息与账号快照；
  2. **账号画像 (Accounts)**：查看玩家体力状态、看板娘设置与个性化装扮；
  3. **修正者名册 (Heroes)**：查看角色列表、等级突破、神格与基础属性；
  4. **资源背包 (Inventory)**：查看物资道具、培养素材与消耗品库存；
  5. **定向寻访 (Gacha)**：卡池状态与保底计数浏览，支持 229 与 311 版本的卡池切换；
  6. **协议与帮助 (Agreement)**：常见基础疑问（FAQ）与原理说明；
  7. **物资采购 (Shop)**：常规商店货架与商品列表查看；
  8. **邮件终端 (Mail)**：信件列表查看与邮件发送测试；
  9. **AI 角色对话 (AIChat)**：调用大语言模型进行修正者人设对话测试；
  10. **系统设置 (Settings)**：网络端口监控、版本模式切换与累充数据计算。

---

## 八、 常见问题与排障说明 (Troubleshooting)

### 8.1 客户端提示网络连接失败 / 4xxxx 报错
* **排查方法**：
  1. 检查系统的 HOSTS 文件是否已正确写入重定向条目（需指向 `127.0.0.1` 或服务端真实 IP）；
  2. 确认 `AetherGazer V5 Root CA` 证书已正确导入至系统的“受信任的根证书颁发机构”中，并在移动端完成信任；
  3. 若设备开启了网络代理工具（如 Clash、v2ray、Surge 等），需将相关域名与 `127.0.0.1` 加入直连白名单，防止本地回环流量被代理软件拦截。

### 8.2 提示端口已被占用 (WinError 10048)
* **排查方法**：
  * 检查 443、80 或 8105 等端口是否被其他本地 Web 服务器（如 IIS、Apache、Nginx、VMware 等）占用；
  * 可在终端运行 `netstat -ano | findstr :443` 查询占用该端口的进程 PID，并在任务管理器中结束对应程序；
  * 启动程序时必须使用**“管理员身份运行”**，否则无法正常监听特权端口。

### 8.3 客户端资源版本不匹配
* **排查方法**：
  * 确认服务端启动时指定的版本（`--res-version`）与客户端版本匹配（例如 229 版本客户端应配合 `--res-version 229` 运行）；
  * 系统内置了版本过滤规则，在 229 模式下会自动隔离 311 专有卡池与活动配置，防止客户端因找不到资源发生闪退。

### 8.4 证书信任失败 / 提示 SSL 握手异常
* **排查方法**：
  * 检查 `v5_server/sdk_ca.crt` 证书是否有效；
  * 在 Windows 运行窗口输入 `certmgr.msc`，检查“受信任的根证书颁发机构 -> 证书”下是否存在 `AetherGazer V5 Root CA`，若不存在可重新运行 `一键安装Windows证书.bat`。

---
## 九、 项目阶段与现状说明

### 当前阶段已实现的功能 (Stage 1 Completed)
- [x] **基础通信链路**：基于 `asyncio` 的 TCP/HTTPS 本地服务与动态 Protobuf 编解码处理；
- [x] **主要养成业务**：角色养成、武器钥从、刻印装备、神格芯片、常规战斗结算、商城采购与抽卡保底计算；
- [x] **资源分发接管**：支持根据版本下发对应资源清单，并配合本地缓存提供资源分发服务；
- [x] **桌面管理宿主**：基于 WinUI 3 + .NET 10 的简单桌面宿主，集成 Windows Job Object 进程托管；
- [x] **Web 浏览面板**：覆盖常用数据的只读浏览面板与简单的 AI 对话测试；
- [x] **DNS 辅助解析**：已修复非目标域名转发，支持 UDP/TCP 查询及失败应答；持续提升多设备兼容性；
- [x] **换装专属抽卡池**：角色换装与场景抽取有限奖池已全量接通；
- [x] **多存档管理与预设**：独立多存档新建、切换、删除与三种开荒预设已全量实装。

### 明确未完成或存在问题的部分 (Stage 2 Roadmap & Known Issues)
- [ ] **多人联机相关功能未开放**：多人联机网络已做拦截封锁，工会（公会）相关联机功能不可用；
- [ ] **常驻活动与复杂玩法迭代推进**：
  - 各类常驻活动涉及协议较多，正逐步通过回归测试完善；
  - **刺桐巷**：已补足 53 条主任务、129 条子任务、海战与角色战，见闻收益达到满级需求的 2.7 倍；
  - **决斗王**：集中修缮卡池、经验、合成、敌队技能与对战模拟，四胜后直接挑战 Boss；
  - **多维变量**：已修复刷新周期（每周一 05:00），完善开局、选门、商店、招募与终局重登恢复；
  - **虚构推演 (RogueTeam)**：吸收了 [Issue #8 的贡献补丁](https://github.com/Mint-ARK/Alpha-Persei-Cluster/issues/8)，打通状态机主干、结局结算与 17 项专属成就；
  - 爬塔玩法与其余小游戏基本完成代码逻辑，正结合客户端逐步实测调优；
- [ ] **潜在的程序问题**：在使用本程序时仍可能遇到部分偶发问题，包括 Loading 遮罩卡死、弹窗提示“网络连接失败”等，此类问题多由特定协议帧字段差异或缺乏精准兜底回包导致；
- [ ] **体感与数值问题**：部分玩法可能出现道具掉落或更新异常，少量显示问题可在退出重登后恢复。如发现逻辑或入库错误，欢迎[提交 Issues](https://github.com/Mint-ARK/Alpha-Persei-Cluster/issues) 反馈，感谢每一位参与测试的小伙伴！

---

## 项目贡献者

感谢 [@a3164155](https://github.com/a3164155)、[@LingYzh](https://github.com/LingYzh)、[@baixiaoxiao831](https://github.com/baixiaoxiao831)、[@Rachel08311](https://github.com/Rachel08311)、[@qiuyuxc](https://github.com/qiuyuxc) 提供代码补丁、问题报告、排查资料和实测反馈，也感谢 [@Arborsm](https://github.com/Arborsm) 参与讨论与建议。详细贡献与对应 Issue 见[项目贡献者名单](CONTRIBUTORS.md)。

---
## 十、 免责声明与开源合规

1. **非商业研究性质**：
   本项目仅供计算机技术爱好者与研究人员对软件逆向工程、网络协议编解码及现代化桌面进程守护等技术课题进行学习与交流。**严禁任何个人或组织将本项目源码、编译产物或相关工具用于商业牟利、付费代理或公开搭建在线服务**；
2. **知识产权归属**：
   《深空之眼》（Aether Gazer）游戏客户端内的全部美术资源、音频音效、模型动作、剧本设定及注册商标，均属其原开发商与发行商所有。**本项目源码不包含、不分发任何官方客户端专有资源文件**；
3. **法律责任自负**：
   使用者应当在体验后自行移除相关调试文件，并自觉支持正版游戏产品。使用者因违反相关法律法规或服务协议而产生的任何民事、刑事及连带法律责任，均由使用者自行承担，本项目开发者不承担任何直接或连带责任。
