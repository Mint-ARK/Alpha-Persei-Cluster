# Alpha Persei Cluster

> This project is maintained independently by an individual. Due to heavy workloads recently, troubleshooting and issue fixes may take some time. Thank you for your patience and understanding.

> **Troubleshooting**: Please refer first to the [Issue Index and Diagnostic Reports](https://github.com/Mint-ARK/Alpha-Persei-Cluster/blob/codex/diagnostic-reports-20261020/reports/README.md). Locate the corresponding report based on feature modules and error symptoms, then investigate using the preliminary diagnosis, related files, and protocol entry points; you may also feed the report to an AI Agent for assisted localization. When submitting an Issue, please include your client platform, version, reproduction steps, and relevant logs, noting the corresponding "A-number" if applicable.

<div align="center">

[简体中文](./README.md) | **English**

![Platform](https://img.shields.io/badge/Platform-Windows%2010%2B%20%7C%20x64-0078D6?style=flat-square&logo=windows)
![.NET Version](https://img.shields.io/badge/.NET-10.0%20(WinUI%203)-512BD4?style=flat-square&logo=dotnet)
![Python Version](https://img.shields.io/badge/Python-3.10%2B%20(asyncio)-3776AB?style=flat-square&logo=python)
![Frontend](https://img.shields.io/badge/Frontend-React%2019%20%7C%20TailwindCSS-61DAFB?style=flat-square&logo=react)
![License](https://img.shields.io/badge/License-Non--Commercial%20Research-grey?style=flat-square)
![Status](https://img.shields.io/badge/Milestone-Stage%201%20Completed-brightgreen?style=flat-square)

<p align="center">
  <b>A local offline simulation server (LocalServer) built for Aether Gazer, designed to keep the game playable via local HOST redirection and network emulation even after official servers are shut down.</b>
</p>
</div>

> 📖 **Developer's Note / Behind the Scenes**:  
> Want to know the journey, motivations, and musings behind this project? Welcome to read the blog post: [About LocalServer - MoriaRuRuka (Chinese)](https://moriaruruka.com/2026/09/24/411/).

## 🚀 Changelog (2026.11.06)

## Current Release: v5.0.3 (2026-11-06)

This release introduces password protection for the web management console in public network deployments and deep fixes for minigames and core gameplay systems. To download validated portable packages, installer packages, or Linux core archives, visit the [v5.0.3 Release Page](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/tag/v5.0.3); see [Changelog](CHANGELOG.md) for full commit records and verify downloaded archives using [`SHA256SUMS_v5.0.3.txt`](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.3/SHA256SUMS_v5.0.3.txt).

**We strongly recommend downloading the portable archive [`AetherGazer_Server_v5.0.3_Portable.zip`](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.3/AetherGazer_Server_v5.0.3_Portable.zip), which requires no installation.** After extraction, you can launch it via `启动离线单机服务(控制台).bat` for command-line mode, or run `LocalServer.exe` / `启动离线单机服务(GUI界面).bat` for the desktop host. For Linux users, please use [`AetherGazer_Server_v5.0.3_Linux_Core.zip`](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.3/AetherGazer_Server_v5.0.3_Linux_Core.zip) and execute `sh server.sh setup` to complete the interactive setup and set an administrator security password.

### Key Update Highlights:

1. **Web Console Password Authentication**: Added an administrative password protection mechanism designed for public server deployments. Please remember the password set during initialization.
2. **Windows Core Source Package**: Released a lightweight core package containing pre-built web assets without bundled desktop launchers or pre-installed Python environments.
3. **Overhauled Linux Deployment Wizard**: The streamlined setup script is now fully compatible with WSL 2 (Ubuntu 20.04+), Termux (Android 8.1+ Rooted), and OpenCloudOS 9.
4. **Minigame Gameplay Logic Completion**:
   1. **Citong Alley (Quanzhou)**: 53 main quests, 129 sub-quests, 11 sailing stages, and 2 character battles; quest milestone rewards yield **2.7x** the requirement for max level, allowing players to claim all rewards without exhausting all exploration.
   2. **Battle of Dojo (Autochess)**: Players can now directly challenge the configured boss after four consecutive wins and claim victory rewards properly.
   3. **Verthandi Resident Event**: Passing stages now directly awards full scores, eliminating repetitive grinding.
   4. **Resident Events**: Fixed erroneous deliveries of expired limited-time rewards.
5. **Fictional Deduction (RogueTeam) Iteration**:
   Incorporated the community patch from **[Issue #8](https://github.com/Mint-ARK/Alpha-Persei-Cluster/issues/8)** and overhauled the finite state machine:
   - Unified ownership of run sessions, floors, main/hidden maps, nodes, and pending choices to prevent illegal phase skipping, stale session requests, and duplicate settlements.
   - Fixed drop deduplication, merchant pricing, immediate purchase effects, milestone points, mechanism selection, and reward queues.
   - Connected transitions for hidden island entries, story battles, reward claims, and returning to the main board.
   - Fixed the sequencing of endgame battles, story confirmations, settlements, reward distribution, and reconnect recoveries.
   - Restored 7 active effects across 5 relics, program upgrades, and immediate chain triggers.
   - Correctly activated, saved, and restored tactical directives upon collecting 6 identical school programs; the first activated school is locked for the run.
   - Recorded post-battle residual energy and tracked single-directive kill statistics based on genuine battle reports.
   - Reconnected 17 verified RogueTeam achievements (10 clear/possession, 6 tactical acquisition, 1 single kill).
   - New saves automatically skip the unready 101 tutorial while retaining 501 story and onboarding guides.
   *(Note: Per-character HP persistence and remaining relics/tech perks will be polished in subsequent updates; the core state machine, endgame settlements, and achievements are now fully hooked up.)*
6. **Reward and Achievement System Hardening**:
   - Deduplicated batched achievement requests and verified eligibility in atomic transactions, preventing duplicate claims or claiming unearned rewards.
   - Fixed legacy achievement thresholds to avoid faking unread content as fully read.
   - Fixed gacha ticket deductions and reward granting when essential server responses fail.
   - Fixed target banner cross-contamination under shared pity pools.
   - 4-star Functors are now generated strictly according to the currently selected Gen-zone.
   - Fixed pre-built but unowned characters from being misidentified as owned and prematurely converted to Intel shards.
   - Aligned displayed rewards with actual inventory increments for permanent items and capacity caps.
   - Employed delta/instance frames for new functors, sigils, and characters, reducing full-sync overhead.
   - Prevented fallback SQL insertions when public reward pipelines fail, eliminating duplicate deliveries.
   - Validated package consumption and candidate choices prior to item usage to prevent empty consumption or out-of-bounds selections.
7. **Character Progression & Admin Cat Fixes**:
   - **Sigils**: Fixed slot ownership, double slot occupation, breakthrough costs/experience checks, gen-zone reconstruction confirmations, and invalid enchantment preview indexes.
   - **Functors**: Fixed awakening targets, materials, and instance handling while preserving transcendence and equip references.
   - **Chips**: Fixed invalid presets, misaligned characters/slots, and missing prerequisites; achievements now tally real ownership.
   - **Battles**: Prevented duplicate battle IDs within the same millisecond; concurrent settlements only award assets once.
   - **Sweeps & Tasks**: Preserved gameplay, team, and combo contexts, preventing battle progression from misfiring lobby interaction quests.
   - **Mood & Collection**: Restored battle, sweep, and daily reset mood sync; added missing 7-tier faction collection rewards.
   - **Admin Cat**: Fixed early termination, log config referencing, 05:00 day boundary and weekend claim windows; fixed UI freeze after dispatch.
8. **Onboarding, Monthly Pass & Customization Fixes**:
   - Fixed game days, final days, and duplicate claiming in newbie check-in.
   - Fixed eligibility, claimed records, and task status for newbie level and cumulative progress rewards.
   - Persistently cleared 5 categories of newbie notification badges.
   - Prevented base status from overriding active 30-day monthly passes; synchronized online status upon claiming.
   - Fixed response sequence and failure rollbacks for uncollected battlepass tier rewards.
   - Fixed loss of stamina recovery baseline when system clocks rollback.
   - Fixed custom loading wallpapers resetting to default 3 images upon reconnect.
   - Synchronized illustration reward claim states.
   - Fixed cross-month blue dot misalignment on the web check-in calendar.
9. **Linux / WSL / Termux Deployment & CDN Overhaul**:
   - Unified legacy shell scripts into **`sh server.sh`**, offering interactive setup, env checks, dependency installation, start, stop, and status querying.
   - Guided setup checks dependencies and ports, captures local/LAN/public IPs, and persists configurations.
   - Integrated optional Nginx templates with syntax validation, hot reload, and failure rollbacks.
   - Termux optimizations: high-port defaults, local IP binding, and system package guidance.
   - Three CDN modes: **off, relay-only, and relay-with-cache** (default: off).
   - Eliminated invalid outbound IP blocks and fixed HTTP/TLS stream truncation errors.
   - Resolved Chinese filename encoding issues inside Linux ZIP archives.
   - **Standardized Official Android Channel to 7** (Channel 8 for Bilibili), synchronizing SDKs, asset probing, and web parameters.
   - Fixed server selection delays when creating/deleting saves on public iOS instances.
10. **Recurring Dream, Dark Zone, Polyhedron & WarChess Advancements**:

    | Mode | Key Changes |
    | :---: | :--- |
    | **Recurring Dream** | Unified Thursday 05:00 refresh; fixed reward eligibility, boss score collisions, stale cross-period battle reports, post-claim resets, and cross-boss character locks. |
    | **Hazard Zone (Final)** | Unified Monday/Thursday 05:00 semi-weekly reset; enabled team-relay handoffs and isolated challenge reports across saves. |
    | **Regular Dark Zone** | Preserved exact star requirements; fixed star rewards eligibility and repeat claims without blindly treating runs as 3-star clears. |
    | **Iterative Verification** | Added Mode 4 reward eligibility, history tracking, and task rollback logic; remaining modes and 5-team totals will follow. |
    | **Dimensional Variable** | Fixed "refresh in 7000+ days" bug to **every Monday 05:00**; refined opening, door selection, shops, recruitment, battles, weekly rewards, and reconnect recovery. |
    | **WarChess (Casualties Observation)** | Completed 14 battles, 3 small chests, 2 large chests, mechanisms, 100% progress, and mission recovery on the initial map. |

11. **Resident Minigames Completion List**:

    | Minigame | Major Improvements |
    | :---: | :--- |
    | **Mecha Gear Challenge** | Added stage prerequisites, vehicle unlocks, and archive rewards; fixed kill/retrofit/archive quests; added login state recovery. |
    | **Night Tales** | Separated main storyline and challenge save slots; fixed exit, interruptions, stale games, and duplicate settlements; added challenge and reading tasks. |
    | **Climbing Ladder** | Blocked multiplayer matchmaking while granting 4 permanent quest rewards with anti-duplication records. |
    | **Muted Light Chronicle** | Added normal chapter exploration, prerequisites, resets, and tasks; implemented Extreme Study 4-chain battles with affix, lineup, and HP inheritance. |
    | **Remote Village / Hela Pinball** | Fixed skill slots and equipped skill validation; separated normal and challenge scores; hooked up Stage 5 battle and mission trackers. |
    | **Fleeting Melodies (Rhythm)** | Matched songs with tasks; fixed high scores, completion flags, and reconnect recovery; rewards validated against target score thresholds. |
    | **PVE Autochess (Dojo)** | Overhauled card pools, exp, synthesis, enemy teams, skills, props, shields, summons, ties, coins, retries, replays, and persistence. |
    | **Train Heist** | Fixed duplicate movie progress rewards, fixed dummy scores, and score collisions; added skill prerequisites, tasks, and dialogue recovery. |
    | **Food & Another World** | Fixed mismatches between logical stages and asset IDs; resolved first-clear food delivery omissions; added pinballs, companions, and cooking tasks. |
    | **Tactical Study Records** | Added 18 tutorial stages and 6 sandbox stages with prerequisites, hero locks, and tasks; fixed score caching and first-clear refresh. |
    | **Citong Alley (Quanzhou)** | Added quest state machine, map positions, feature unlocks, sea battles, character duels, photos, milestone levels, and rewards. |
    | **Dream Catcher** | Fixed node and plot asset IDs, puzzle prerequisites, and first-clear logic; added 36 node tasks and 9 chapter archive rewards. |
    | **Summer Board Game** | Added 13 map grids, movement, dice, terrain events, NPCs, shops, 12 item types, QTEs, nested minigames, tasks, and clear history. |
    | **Verthandi Resident Event** | Added schedule, commissions, acceleration, story, training, boss, barbecue, 20 puzzle stages, garland exchange, and tasks. |
    | **Kadas Holiday Race** | Fixed course routing, modding slots, setups, and challenge scores; clearly distinguished current limited rewards from resident rewards. |

**Platform and Resource Support Matrix:**

| Platform | Supported Builds | Notes |
| --- | --- | --- |
| **PC** | **All three: 229 / 311 / 321** | Resource manifests are configured and verified for all three client builds. |
| **iOS** | **229 / 321; 311 is invalid** | **Build 229 supports the in-app purchase simulation**. iOS 311 resource manifests were omitted during initial capture and are currently unavailable. |
| **Android & Other Platforms** | **Manual configuration required** | Due to limited test devices, internal asset manifests require manual configuration and importing by users. |

## v5.0.2 Detailed Changelog (Included in v5.0.3)

1. **Save Slot Management Module**: Real-time selection, creation, and deletion of independent save slots via **Web Panel, Launch Arguments, and In-Game Server Selection**.
   - Consolidated newbie and Lv.70 generation routines; supports fully customized save creation.
   - One database per account, establishing clean character, inventory, quest, and gameplay states.
   - Adjustable starting level from **Lv.1 to Lv.100**, matching cumulative exp and stamina limits to client tables.
   - Custom or random UID support (numeric, up to 12 digits).
   - Save names support UTF-8 characters including Chinese and spaces, up to 32 characters.
   - Independent toggling for the opening 501 storyline and onboarding tutorials.
   - Supports pre-clearing Prologue through Chapter 5 with first-clear rewards granted directly.
2. **Three Preset Saves & Support Mail**:
   - *Full Exploration*: Lv.1, keeps 501 story and feature guides, no pre-cleared stages.
   - *Skip Prologue*: Lv.1, skips 501, pre-clears Prologue, keeps feature guides.
   - *Quick Start*: Lv.70, pre-clears Chapters 1~5, skips guides, newbie tasks ready to claim.
   - Bundles **18 support letters** with stamina props, anniversary characters, signature functors, cosmetics, currency, and materials.
3. **In-Game Server Selection Converted to Save Slot Manager**:
   - Create, switch, and delete save slots directly from the server selection screen.
   - Switch saves without restarting the client; automated backups are preserved before slot deletion.
4. **Web Management Console Upgrades**:
   - Independent save management tab with global save switcher in the top navigation bar.
   - Two-way binding between preset configurations and individual form controls.
5. **Per-Save AI Profiles & Proactive Greetings**:
   - Each save maintains its own LLM API keys and model configurations.
   - Automatic morning greetings upon the first login of each day, dynamically merging solar terms, birthdays, and relationship context.
6. **Storyline, Gacha, and Battlepass Fixes**:
   - Fixed trial character battle payloads in Chapter 23, Chapter 19 scanning clues, and stage star chests.
   - Handled 26 cosmetic/scene gacha pools across builds 311 and 321 with pity carryover.
   - Weekly battlepass refreshes every Monday at 05:00 with enhanced exp supply mail.

## v5.0.2 Source & Local Build Update: 2026-10-26

Integrated independent save generation, in-game save switching, UTF-8 launch arguments, resident activities and shop restorations, first-clear rewards and battle timer fixes, birthday mail, AI daily greetings, and chat history recovery. Portable packages allow choosing CMD or desktop launchers; Linux uses the core package. See [Save and AI Guide](docs/save_and_ai_guide.md).

## v5.0.2 Addendum: 2026-10-28

Added multi-channel manifest management, active download queues, and unified cache storage. Fixed Build 229 login loading hangs and preserved cross-version battlepass progress. See [Resource Download Guide](docs/resource_download_guide.md).

---

## Table of Contents

- [1. Project Overview & Design Philosophy](#1-project-overview--design-philosophy)
- [2. System Architecture Topology Diagram](#2-system-architecture-topology-diagram)
- [3. Quick Start & Multi-Platform Deployment Guide](#3-quick-start--multi-platform-deployment-guide)
  - [3.1 Prerequisites](#31-prerequisites)
  - [3.2 Windows Deployment & Running (GUI / CLI)](#32-windows-deployment--running-gui--cli)
  - [3.3 Linux Core Deployment (git / curl / scp)](#33-linux-core-deployment-git--curl--scp)
  - [3.4 Android Single-Device Standalone Deployment (Root / Termux / sing-box)](#34-android-single-device-standalone-deployment-root--termux--sing-box)
  - [3.5 iOS Local Area Network (LAN) Setup Guide](#35-ios-local-area-network-lan-setup-guide)
- [4. Server CLI Arguments Reference](#4-server-cli-arguments-reference)
- [5. Core Server Architecture: Breakdown of all 70 Modules](#5-core-server-architecture-breakdown-of-all-70-modules)
- [6. WinUI 3 Desktop Host & Process Supervisor (`LocalServer`)](#6-winui-3-desktop-host--process-supervisor-localserver)
- [7. Web Management Console (`web_src` / `static_web`)](#7-web-management-console-web_src--static_web)
- [8. Troubleshooting & FAQ](#8-troubleshooting--faq)
- [9. Project Milestones & Current Limitations](#9-project-milestones--current-limitations)
- [10. Disclaimer & Compliance](#10-disclaimer--compliance)

---

## 1. Project Overview & Design Philosophy

This project is an experimental local simulation environment for offline study and gameplay preservation:

1. **Local Offline Sandbox**:
   Network communication, authentication, inventory accounting, gacha computations, and data persistence operate entirely locally. Communications default to loopback `127.0.0.1` or the local area network (LAN).
2. **Dual-Process Management**:
   The desktop GUI is built with WinUI 3 (C# / .NET 10) to supervise the underlying Python server process. Using the Windows Job Object API, child processes are automatically terminated when the launcher exits, preventing orphan processes from locking network ports.
3. **Progressive Evolution**:
   Evolved from early packet recording and replaying, the server now features event-bus dispatching, dynamic runtime Protobuf encoding/decoding, and SQLite relational persistence to replicate gameplay as faithfully as possible offline.

---

## 2. System Architecture Topology Diagram

```
+-----------------------------------------------------------------------------------------------+
|                                LocalServer (WinUI 3 / .NET 10)                                |
|  [Mica UI Design]       [Embedded WebView2]       [Streamed Console Output]   [Env Self-Check] |
|  [Windows Job Object Process Supervisor] ---------------------+                               |
+---------------------------------------------------------------+-------------------------------+
                                                                | (Process Binding & IO Pipe)
                                                                v
+-----------------------------------------------------------------------------------------------+
|                               v5_server Core Engine (Python 3.10+)                            |
|                                                                                               |
|  [TCP Gateway: 8102]    [TCP Game: 8105]         [HTTPS Auth: 443]       [UDP Battle: 6105]   |
|        |                       |                        |                       |             |
|        +-----------------------+------------------------+-----------------------+             |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |     server_net      | (Packet Routing / TLS Handshake / Static Assets)  |
|                     +----------+----------+                                                   |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |       core.py       | (Session Dispatch / Sequence Tracking / Context)  |
|                     +----------+----------+                                                   |
|                                |                                                              |
|              +-----------------+-----------------+                                            |
|              |                                   |                                            |
|    +---------v---------+               +---------v---------+                                  |
|    |   operations.py   |               | 25 Domain Services|                                  |
|    |  (130+ Handlers)  | <-----------> | (Gacha, Heroes,   |                                  |
|    +---------+---------+               |  Equip, Mail etc.)|                                  |
|              |                         +---------+---------+                                  |
|              +-----------------+-----------------+                                            |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |  Dynamic Codec/Gen  | (Dynamic Protobuf Wire-Format / Delta Push Frames)|
|                     +----------+----------+                                                   |
|                                |                                                              |
|                     +----------v----------+                                                   |
|                     |  account.db (SQLite)| (Local Relational State Storage)                  |
|                     +---------------------+                                                   |
+-----------------------------------------------------------------------------------------------+
```

---

## 3. Quick Start & Multi-Platform Deployment Guide

### 3.1 Prerequisites

* **Operating System**: Windows 10 or later (x64 recommended), with Microsoft Edge WebView2 and WinUI 3 support available; Linux distributions (WSL 2 / Ubuntu 20.04+, OpenCloudOS 9, Debian, Termux, etc.).
* **Dependencies**:
  * **Python 3.10+**;
  * **.NET 10.0 Runtime / SDK** (if using the desktop launcher);
  * **Microsoft Edge WebView2 Runtime** (pre-installed on Windows 11);
* **Permissions**: Modifying the system `hosts` file and binding privileged ports (443 and 80) require Administrator privileges. Run terminal scripts or the launcher as Administrator.

### 3.2 Windows Deployment & Running (GUI / CLI)

1. **Portable GUI Mode (Recommended)**:
   Extract [`AetherGazer_Server_v5.0.3_Portable.zip`](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.3/AetherGazer_Server_v5.0.3_Portable.zip) and double-click `LocalServer.exe` (or run `启动离线单机服务(GUI界面).bat`).
   > **Note**: Initial server startup may take a few seconds. On first run, Windows Firewall will request network access permissions for Python; please click **"Allow access"**.

2. **Install Local Root Certificate**:
   Follow the prompts in the desktop GUI to install the self-signed root certificate, or locate `v5_server/sdk_ca.crt`, double-click it, and import it into **"Trusted Root Certification Authorities"**.

3. **Configure System HOSTS Redirection**:
   Open `C:/Windows/System32/drivers/etc/hosts` with Administrator privileges and append the following domain entries:

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

   This redirects client traffic intended for official servers to the local loopback network, where the server intercepts and responds.

4. **Console / CLI Mode**:
   Ensure Python 3.10+ is installed. Open a terminal inside the `v5_server` directory and execute:

   ```powershell
   python main.py
   ```

   Alternatively, double-click `启动离线单机服务(控制台).bat`.

### 3.3 Linux Core Deployment (git / curl / scp)

1. **Fetch and Launch Directly from GitHub**:

   * **Method 1: Clone via Git**
     ```bash
     # 1. Clone repository
     git clone https://github.com/Mint-ARK/Alpha-Persei-Cluster.git

     # 2. Enter server directory
     cd Alpha-Persei-Cluster/v5_server

     # 3. Launch interactive setup wizard
     sh server.sh setup
     ```

   * **Method 2: Pull Linux Core Archive via Curl**
     ```bash
     # 1. Create and enter deployment directory
     mkdir -p aethergazer && cd aethergazer

     # 2. Download v5.0.3 Linux Core archive
     curl -fL --retry 3 -o AetherGazer_Server_v5.0.3_Linux_Core.zip        https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.3/AetherGazer_Server_v5.0.3_Linux_Core.zip

     # 3. Extract release package
     unzip AetherGazer_Server_v5.0.3_Linux_Core.zip

     # 4. Run interactive setup wizard
     sh server.sh setup
     ```

2. **Cross-Platform Transfer Alternative (If GitHub access is constrained)**:
   * Download [`AetherGazer_Server_v5.0.3_Linux_Core.zip`](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.3/AetherGazer_Server_v5.0.3_Linux_Core.zip) on a Windows computer.
   * Transfer via SCP:
     ```bash
     scp file_path user@ip:server_path
     ```
   * Alternatively, upload via panel utilities (aaPanel, 1Panel, SFTP), extract, and execute `sh server.sh setup`.

### 3.4 Android Single-Device Standalone Deployment (Root / Termux / sing-box)

#### 3.4.1 Prerequisites

* **Install ADB (Android Debug Bridge)**:
  ```powershell
  winget install -e --id Google.PlatformTools --silent --accept-package-agreements --accept-source-agreements
  ```
  Verify installation:
  ```powershell
  adb --version
  ```
* **Acquire Root Privileges**:
  Meizu Flyme supports native Root toggle in Developer Options; OnePlus, Xiaomi, and other bootloader-unlocked devices running Magisk / KernelSU / APatch are fully compatible.
* **Install Terminal App**: Install [Termux](https://github.com/termux/termux-app).
* **Install Proxy Router**: Install [sing-box](https://github.com/SagerNet/sing-box) with the following Tun routing configuration (redirecting game packets to high ports bound by Termux):

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

#### 3.4.2 Practical Steps: Single-Device Standalone (No APK Patching Required)

1. **Calculate and Prepare Root CA**:
   * **Android System CA Naming Convention**: Certificates placed in `/system/etc/security/cacerts/` must be named strictly as **`<Subject_Hash>.0`**.
   * Calculate subject hash:
     ```bash
     openssl x509 -inform PEM -subject_hash_old -in sdk_ca.crt
     ```
     The bundled release CA hash is fixed to **`ad2b2d99`**, meaning the file must be named `ad2b2d99.0`.
   * Push certificate to device storage:
     ```bash
     adb push v5_server/sdk_ca.crt /sdcard/ad2b2d99.0
     ```

2. **Bind-Mount Certificate into System Trust Store**:
   ```bash
   mkdir -p /data/local/tmp/cacerts
   cp -n /system/etc/security/cacerts/* /data/local/tmp/cacerts/ 2>/dev/null
   cp /sdcard/ad2b2d99.0 /data/local/tmp/cacerts/
   chown -R root:root /data/local/tmp/cacerts
   chmod 755 /data/local/tmp/cacerts
   chmod 644 /data/local/tmp/cacerts/*
   mount -o bind /data/local/tmp/cacerts /system/etc/security/cacerts
   ```

3. **Bypass Tencent ACE Anti-Cheat Root Detection**:
   * **Principle**: Non-root apps detect root by checking whether `/system/xbin/su` is executable by everyone. By changing `su` permissions from `4755` (world executable) to `6750` (executable only by root and privileged groups), the game app receives `Permission Denied` and deems the environment unrooted.
   * Apply anti-detection permissions:
     ```bash
     mount -o rw,remount /system
     chmod 750 /system/xbin
     chmod 6750 /system/xbin/su
     chmod 644 /system/build.prop
     mount -o ro,remount /system
     ```
   * Verify detection evasion:
     ```bash
     adb shell "su 10102 ls -la /system/xbin/su"
     ```
     Expected output:
     ```
     Permission denied
     ```

4. **Environment Restoration (When Needed)**:
   ```bash
   # 1. Unmount CA certificate
   adb shell "su 0 umount /system/etc/security/cacerts"

   # 2. Restore su permissions (lift ACE evasion)
   adb shell "su 0 sh -c 'mount -o rw,remount /system && chmod 755 /system/xbin && chmod 4755 /system/xbin/su && mount -o ro,remount /system'"
   ```

5. **Diagnostic Checks**:
   ```bash
   # 1. Check connected device
   adb devices
   # 2. Verify CA presence in system store
   adb shell "ls -l /system/etc/security/cacerts/ad2b2d99.0 2>/dev/null"
   # 3. Check su permission status (6750 for shielded, 4755 for exposed)
   adb shell "ls -l /system/xbin/su"
   ```

6. **Deploy and Run Server in Termux**:
   * Update packages and install prerequisites:
     ```bash
     pkg update && pkg upgrade -y
     pkg install -y unzip curl
     ```
   * Fetch and extract the Linux Core package:
     ```bash
     # 1. Create directory
     mkdir -p aethergazer && cd aethergazer

     # 2. Download release package
     curl -fL --retry 3 -o AetherGazer_Server_v5.0.3_Linux_Core.zip        https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases/download/v5.0.3/AetherGazer_Server_v5.0.3_Linux_Core.zip

     # 3. Extract
     unzip AetherGazer_Server_v5.0.3_Linux_Core.zip

     # 4. Launch setup wizard
     sh server.sh setup
     ```
   * Follow the wizard to complete network parameters and set your admin password.

### 3.5 iOS Local Area Network (LAN) Setup Guide

1. **Same LAN Wi-Fi**: Ensure your iOS device and the computer/server hosting the simulation backend are on the same Wi-Fi network;
2. **Start Server with DNS Mode**: Launch the backend specifying `--dns` (binds to LAN IP with DNS forwarding):
   ```powershell
   python main.py --dns
   ```
3. **Configure Wi-Fi DNS**: In iOS Wi-Fi settings, switch DNS configuration to "Manual" and enter the computer's LAN IP (e.g. `192.168.1.100`);
4. **Install Certificate Profile**: Open Safari on iOS, navigate to `http://<LAN_IP>/sdk_ca.mobileconfig` (e.g. `http://192.168.1.100/sdk_ca.mobileconfig`), install the profile, and toggle full trust under "Settings -> General -> About -> Certificate Trust Settings";
5. **VPN / Proxy Configuration (e.g. [Shadowrocket](https://apps.apple.com/app/shadowrocket/id932747118))**:
   If modifying global Wi-Fi DNS is inconvenient, add rule entries under the `[Host]` section of your Shadowrocket profile:
   * **GUI Mode**: Profile -> Hosts -> Add -> Local Value:
     ```
     Domains: *.soboten.com, *.ys4fun.com, *.ys4fun.cn
     Value: <Your Server IPv4 Address>
     ```
   * **Text Edit Mode**: Append directly to the `[Host]` block:
     ```ini
     [Host]
     *.soboten.com = your_server_ip
     *.ys4fun.com = your_server_ip
     *.ys4fun.cn = your_server_ip
     ```

---

## 4. Server CLI Arguments Reference

| Category | Flag | Default | Description |
| :---: | :--- | :---: | :--- |
| **Help** | `-h`, `--help` | — | Display help message and exit. |
| **Save** | `--db <path>` | `ACCOUNT_DB_PATH` or `account.db` | Specify target SQLite database path. Supports UTF-8, spaces, and absolute paths. |
| **Network** | `--host-ip <IPv4>` | Auto LAN IP | Advertised address for client connections. Can be loopback, LAN, or public IPv4. |
| **Network** | `--web-bind <IPv4>` | `0.0.0.0` | Local bind address for HTTP/HTTPS listeners. Use `127.0.0.1` behind reverse proxies. |
| **Network** | `--https-port <port>` | `443` | HTTPS port for SDK, auth, and web panel (`1-65535`). |
| **Network** | `--http-port <port>` | `80` | HTTP auxiliary port (`0-65535`; `0` to disable). |
| **Network** | `--gw-port <port>` | `8102` | Starting TCP port for the multi-save gateway pool (`1-65535`). |
| **Network** | `--game-port <port>` | `8105` | Primary TCP game service port (`1-65535`). |
| **Network** | `--battle-port <port>` | `6105` | UDP battle server port (`0-65535`; `0` to disable). |
| **DNS** | `--dns` | Disabled | Enable embedded DNS server listening on UDP/TCP:53. |
| **DNS** | `--dns-upstream <IP>` | `223.5.5.5`, `114.114.114.114` | Upstream DNS servers for queries outside game domains. Repeatable. |
| **CDN** | `--cdn-mode <mode>` | `off` | `off`: disable; `relay`: forward only; `cache`: forward and save to local disk. |
| **CDN** | `--capture-cdn` | Disabled | Legacy alias for `--cdn-mode cache`. |
| **CDN** | `--no-capture-cdn` | Disabled | Force disable passive CDN forwarding, overriding other CDN flags. |
| **Assets** | `--res-version <ver>` | `auto` | Target asset baseline. `auto` uses channel defaults; supports `229`, `311`, `321`. |
| **Assets** | `--resource-profile "<ch>=<prof>"` | None | Explicit manifest profile selection (`pc`, `android`, `ios`). |
| **Assets** | `--list-resource-profiles` | Disabled | List available base/derived manifests and exit. |
| **Assets** | `--client-assets-dir <dir>` | Auto | Path to client's `StreamingAssets` for local version detection. |
| **Game** | `--game-version <ver>` | `321` | Fallback business version when client channel metadata is omitted. |
| **Tool** | `--check-update` | Disabled | Check differences between official PC manifests and local definitions, then exit. |
| **Tool** | `--sync-update` | Disabled | Delegate asset synchronization to the PC unified download queue. |
| **Login** | `--frame-delay <sec>` | `0.001` | Frame interval delay during login burst pushes in seconds. |
| **Debug** | `--replay <dir>` | Disabled | Enable protocol replay subsystem using archived capture files. |
| **Debug** | `--frame-override <bin>` | None | Override replay frames with custom binary payloads. |
| **Log** | `--log-level <level>` | `INFO` | Console output verbosity: `DEBUG`, `INFO`, `WARN`, `ERROR`. |
| **Diagnostic**| `--self-check` | Disabled | Validate component initialization and operation registrations, then exit. |

---

## 5. Core Server Architecture: Breakdown of all 70 Modules

| Category | Module (`v5_server/`) | Description |
| :---: | :---: | :--- |
| **Entry & Core** | `main.py` | Server entrypoint; parses launch flags, mounts databases, domain services, and network listeners. |
| **Entry & Core** | `core.py` | Defines request/connection models; registers and dispatches operations; packages responses. |
| **Entry & Core** | `middleware.py` | Receives decoded requests, connecting business logic, battle sessions, and settlements. |
| **Entry & Core** | `operations.py` | Registers game operations; validates parameters, mutates states, and generates responses. |
| **Entry & Core** | `event_bus.py` | In-process Pub/Sub event bus connecting combat, inventory, quest, and achievement modules. |
| **Entry & Core** | `operation_notifications.py` | Manages pending notifications within operations, tracking instance deletions and rollbacks. |
| **Entry & Core** | `lazy_timer.py` | Lazy time evaluator triggered on requests, heartbeats, and logins for daily/weekly boundaries. |
| **Entry & Core** | `timer_listeners.py` | Event subscriber driving stamina regeneration, tasks, stores, and periodic resets. |
| **Entry & Core** | `logger.py` | Unified logging entrypoint managing console output levels and rotating log files. |
| **Entry & Core** | `log_sifter.py` | Filters high-frequency heartbeat noise and extracts key asset mutations for console visibility. |
| **Entry & Core** | `multiplayer_policy.py` | Blocks multiplayer matchmaking and guild networks while preserving necessary single-player flows. |
| **Network & Protocol** | `server_net.py` | Coordinates TCP gateway, game connections, SDK routing, HTTPS handlers, and save slot handoffs. |
| **Network & Protocol** | `battle_server.py` | Standalone UDP battle server handling handshakes, frame interactions, and result caching. |
| **Network & Protocol** | `transport.py` | Manages binary frame boundaries, sequence numbers, and ZLIB compression. |
| **Network & Protocol** | `codec.py` | Bidirectional pure-Python dynamic Protobuf wire-format encoder/decoder. |
| **Network & Protocol** | `decode_schema.py` | Loads protocol schemas and provides field indexing and low-level decoding inspection. |
| **Network & Protocol** | `schema_default.py` | Infers and synthesizes schema-compliant default responses to prevent empty packet crashes. |
| **Network & Protocol** | `login.py` | Coordinates login push sequence, reads dynamic save states, and dedupes initial frames. |
| **Network & Protocol** | `generator.py` | Synthesizes login, query, and state synchronization payloads according to protocol definitions. |
| **Network & Protocol** | `battle_payload.py` | Serializes modifier stats, functors, astrolabes, and trial characters for `sc_54003`. |
| **Network & Protocol** | `hero_codec.py` | Serializes character database records into client modifier structures with backwards compatibility. |
| **Network & Protocol** | `reserve_codec.py` | Encodes and decodes team presets and game mode battle lineups. |
| **Network & Protocol** | `formation_codec.py` | Validates, serializes, and saves custom team formations. |
| **Network & Protocol** | `team_server.py` | Manages active team lineups, combo skills, and Mimir setups across gameplay modes. |
| **Network & Protocol** | `dns_server.py` | Local DNS redirector resolving game domains to host IP and forwarding other queries. |
| **Network & Protocol** | `replay.py` | Optional packet replay module reconstructing login streams from archived captures. |
| **Database & Save** | `account_db.py` | SQLite abstraction layer managing tables, migrations, transactions, backups, and exports. |
| **Database & Save** | `save_generator.py` | Generates clean save slots based on level, UID, characters, guides, and pre-clear parameters. |
| **Database & Save** | `save_presets.py` | Defines shared starting presets (Full Exploration, Skip Prologue, Quick Start). |
| **Database & Save** | `save_slot_manager.py` | Scans and registers saves, allocates gateway ports, and handles slot switching and mounting. |
| **Database & Save** | `new_player_service.py` | Manages initial states, onboarding activities, stage unlock criteria, and quest recovery. |
| **Database & Save** | `new_player_login_policy.py` | Regulates login pushes for new saves, omitting locked systems. |
| **Database & Save** | `onboarding_operations.py` | Handles newbie check-ins, level rewards, cumulative milestone rewards, and onboarding tasks. |
| **Panel & Auth** | `gm_api.py` | Web API provider routing read requests to reader layers and mutations to domain services. |
| **Panel & Auth** | `gm_reader.py` | Read-only inspector querying accounts, characters, items, mail, and system states. |
| **Panel & Auth** | `gm_save_scope.py` | Binds management requests to targeted save databases without altering in-game mounts. |
| **Panel & Auth** | `admin_access.py` | Identifies administrative entrypoints and applies authentication checks. |
| **Panel & Auth** | `admin_session_auth.py` | Implements single-password authentication, session management, CSRF validation, and brute-force throttling. |
| **Assets & CDN** | `client_version.py` | Binds platform and asset versions to isolate cross-channel version contexts. |
| **Assets & CDN** | `res_version_manager.py` | Tracks asset version registrations, local detection, and official update sync tasks. |
| **Assets & CDN** | `resource_distribution.py` | Public interface exposing platform selection and manifest management features. |
| **Assets & CDN** | `resource_manifests.py` | Manages native BASE manifests, derived copies, checksums, and delivery profiles. |
| **Assets & CDN** | `resource_storage.py` | Manages asset root directories, platform subdirectories, and storage path selection. |
| **Assets & CDN** | `fetch_manifests.py` | Retrieves and parses official primary manifests and audio archives with platform isolation. |
| **Assets & CDN** | `cdn_proxy.py` | Local cache distributor and passive forwarder (off, relay, cache modes). |
| **Assets & CDN** | `cdn_integrity.py` | Validates filenames, sizes, and hashes; handles atomic publication of cached assets. |
| **Assets & CDN** | `cdn_transfer_control.py` | Prioritizes real-time passive client requests over active background downloads. |
| **Assets & CDN** | `cdn_active_downloads.py` | Configures concurrent download workers for asset synchronization tasks. |
| **Assets & CDN** | `cdn_download_index.py` | Parses manifest entries and compiles deduplicated download queues. |
| **Assets & CDN** | `cdn_download_queue.py` | Persistent task queue manager supporting pauses, cancellations, and speed metrics. |
| **Assets & CDN** | `http_streaming.py` | Handles HTTP streaming disconnects, ensuring timely closure of sockets and file handles. |
| **Deploy & Certs** | `linux_manager.py` | Interactive Linux environment inspector, setup wizard, daemon controller, and status checker. |
| **Deploy & Certs** | `linux_nginx.py` | Generates and manages Nginx reverse proxy configs with syntax checks and failure rollbacks. |
| **Deploy & Certs** | `gen_cert.py` | Generates root CA, server certificates, and iOS mobileconfig configuration profiles. |
| **Progression & Items** | `inventory_service.py` | Atomic management of currencies, materials, sigils, and functors, generating delta frames. |
| **Progression & Items** | `hero_service.py` | Manages character levels, breakthroughs, skill trees, astrolabe nodes, functors, and skins. |
| **Progression & Items** | `equip_service.py` | Manages sigil instances, equipping, leveling, breakthroughs, enchants, and gen-zone reconstruction. |
| **Progression & Items** | `enchant_slots_util.py` | Serializes sigil enchantment slots, preview fields, and attribute bonuses. |
| **Progression & Items** | `servant_service.py` | Manages weapon functors: awakening, refinement, locking, binding, and salvaging. |
| **Progression & Items** | `chip_service.py` | Manages Mimir chips, character AI combat chips, support chips, and preset configurations. |
| **Progression & Items** | `fatigue_service.py` | Computes stamina recovery, level-up bonuses, and online timer synchronization. |
| **Progression & Items** | `daily_fatigue_codec.py` | Handles twice-daily free stamina claim windows (11:00 / 18:00) and client state frames. |
| **Progression & Items** | `trust_service.py` | Manages character trust, bond exp, mood, level breakthroughs, and relationship maps. |
| **Quests & Welfare** | `achievement_service.py` | Listens to domain events, tracks achievement progress, and validates atomic reward claims. |
| **Quests & Welfare** | `task_listener.py` | Translates battles, consumptions, and interactions into task progress and delta frames. |
| **Quests & Welfare** | `task_cycle_notifications.py` | Handles daily/weekly quest resets and baseline state notifications. |
| **Quests & Welfare** | `sign_service.py` | Validates version-specific check-in rewards and modal popup conditions. |
| **Quests & Welfare** | `newbie_sign_reward_service.py` | Tracks newbie 7-day check-ins, game day progression, and anti-duplication checks. |
| **Quests & Welfare** | `newbie_level_reward_service.py` | Validates level progression rewards against actual player level and claimed records. |
| **Quests & Welfare** | `newbie_progress_reward_service.py` | Evaluates newbie cumulative quest milestone points and rewards. |
| **Quests & Welfare** | `newbie_notice_service.py` | Persistently clears newbie notification badges. |
| **Quests & Welfare** | `teach_service.py` | Records character tutorial completions and unlocks related progression dependencies. |
| **Shop, Gacha, Mail** | `shop_service.py` | Store catalogue manager handling item purchases, shelf refreshes, and purchase caps. |
| **Shop, Gacha, Mail** | `draw_service.py` | Character/functor gacha summons: rate calculation, pity pools, and duplicate conversions. |
| **Shop, Gacha, Mail** | `skin_draw_service.py` | Limited-pool skin/scene gacha lotteries with ticket tracking and pity preservation. |
| **Shop, Gacha, Mail** | `activity_lottery.py` | Adds cosmetics and ticket lottery chances to daily/weekly activity milestone chests. |
| **Shop, Gacha, Mail** | `recharge_service.py` | Simulates in-app purchases, double-bonus tracking, and cumulative recharge rewards. |
| **Shop, Gacha, Mail** | `paid_entitlement_service.py` | Manages battlepass tiers, weekly supply mail, and monthly pass daily rewards. |
| **Shop, Gacha, Mail** | `periodic_gift_service.py` | Handles multi-day subscription packs, daily deliveries, and login catch-ups. |
| **Shop, Gacha, Mail** | `mail_service.py` | Mailbox manager handling attachments claiming, special letters, and read states. |
| **Shop, Gacha, Mail** | `support_cosmetic_service.py` | Awards historical cosmetics and commemorative avatars to eligible saves. |
| **Shop, Gacha, Mail** | `activity_content_service.py` | Assembles resident event tasks, story recoveries, and event shop catalogues. |
| **Archive & Profile** | `archive_service.py` | Manages character profiles, Heart-Link anecdotes, and story unlock states. |
| **Archive & Profile** | `illustrated_listener.py` | Listens to stage clears and asset unlocks to update gallery archives and faction sets. |
| **Archive & Profile** | `illustrated_story_service.py` | Verifies story reading facts and publishes illustration unlock frames. |
| **Archive & Profile** | `worldview_service.py` | Exposes worldview glossary entries and tracks reading flags. |
| **Archive & Profile** | `enemy_kill_service.py` | Tracks enemy kill counters and boss defeat statistics for the monster index. |
| **Archive & Profile** | `cooperation_skill_server.py` | Tracks ultimate combo skill usage across normal and challenge stages. |
| **Archive & Profile** | `peripheral_service.py` | Manages business cards, avatars, chat bubbles, lobby characters/scenes, and sticker layouts. |
| **Archive & Profile** | `profile_rules.py` | Validates birthdays, custom signatures, likes, and lobby component projections. |
| **Archive & Profile** | `sticker_wall_rules.py` | Validates sticker ownership, exclusion rules, and layout schemes for web and game. |
| **Archive & Profile** | `oath_service.py` | Manages oath ceremonies, chapel 3D events, hero assignments, and gallery photos. |
| **Archive & Profile** | `oath_rights_service.py` | Grants cosmetic perks tied to oath levels and tracks reward grants. |
| **Archive & Profile** | `birthday_service.py` | Evaluates player birthday eligibility, annual commemorative mail, and missed gifts. |
| **Dorm & Admin Cat** | `backhome_service.py` | Manages canteen recipe queues, dorm furniture placement, and character interactions. |
| **Dorm & Admin Cat** | `dorm_gift_config.py` | Configures manual furniture gifting permissions and constraints. |
| **Dorm & Admin Cat** | `admin_cat_explore_service.py` | Admin Cat idle dispatches: queues, zones, offline reward calculations, and levels. |
| **Dorm & Admin Cat** | `admin_cat_dispatch_progress.py` | Records dispatch counts and updates corresponding mission progress. |
| **Dorm & Admin Cat** | `admin_cat_log_service.py` | Generates exploration logs and filters obsolete entries. |
| **Dorm & Admin Cat** | `admin_cat_response.py` | Packages exploration state frames and asset notifications within transactional boundaries. |
| **AI Dialogue** | `ai_api_profiles.py` | Manages multi-profile LLM configurations per save slot. |
| **AI Dialogue** | `ai_bot_config.py` | Default AI templates, credential validation, character model routing, and token masking. |
| **AI Dialogue** | `ai_bot_service.py` | Adapts LLM interfaces, handling synchronous/asynchronous chat, health checks, and fallbacks. |
| **AI Dialogue** | `ai_history_manager.py` | Reconstructs and isolates chat histories between individual characters. |
| **AI Dialogue** | `ai_context_builder.py` | Compiles ephemeral roleplay and relationship context prompts without polluting history. |
| **AI Dialogue** | `ai_calendar.py` | Evaluates solar terms, traditional festivals, and character birthdays to schedule events. |
| **AI Dialogue** | `ai_translator.py` | Translates sticker semantics and dispatches customized greeting letters and attachments. |
| **AI Dialogue** | `ai_proactive_service.py` | Generates proactive morning greetings upon the first login of each day. |
| **AI Dialogue** | `ai_bot_leaderboard.py` | Aggregates public model leaderboards and caches benchmark directories. |
| **Stages & Story** | `stage_service.py` | Categorizes main, sub, resource, and event stages; evaluates stars and first-clear rewards. |
| **Stages & Story** | `chapter_v2_map_service.py` | Manages new-generation map nodes, positions, dossier reading, and time-rewind states. |
| **Stages & Story** | `chapter19_service.py` | Manages Chapter 19 scanning, clue acquisition, and document interactions. |
| **Stages & Story** | `plot_progress_service.py` | Reconstructs chapter quest completion rates from persistent stage clear facts. |
| **Stages & Story** | `chapter_star_reward_service.py` | Verifies star chest eligibility and manages reward claims. |
| **Weekly Challenges** | `weekly_challenge_service.py` | Shared state provider for Recurring Dream, Dark Zone, Polyhedron, and Matrix. |
| **Weekly Challenges** | `boss_battle_identity.py` | Determines boss identities and verifies score attribution according to weekly rotations. |
| **Weekly Challenges** | `boss_challenge_cycle.py` | Manages Recurring Dream cycles, boss rotations, and cross-period validation. |
| **Weekly Challenges** | `boss_challenge_reset.py` | Validates dream reset eligibility and restores previous scores upon failure. |
| **Weekly Challenges** | `boss_challenge_team.py` | Enforces character lineup locking across different bosses within the same cycle. |
| **Weekly Challenges** | `mythic_affix_cfg.py` | Provides environmental affixes and boss combat mechanics for Dark Zone and Hazard Zone. |
| **Weekly Challenges** | `mythic_cycle.py` | Calculates semi-weekly cycles for Hazard Zone (Final), intercepting stale battle attempts. |
| **Weekly Challenges** | `mythic_final_service.py` | Manages Hazard Zone team relay queues, challenge tiers, and stage transitions. |
| **Weekly Challenges** | `mythic_normal_service.py` | Preserves precise star conditions for regular Dark Zone challenges and verifies resets. |
| **Weekly Challenges** | `core_mode4_reward_service.py` | Verifies Mode 4 rewards and event history for Iterative Verification challenges. |
| **Polyhedron** | `polyhedron_service.py` | Dimensional Variable engine: opening, room selection, shops, recruitment, and settlements. |
| **Polyhedron** | `polyhedron_account.py` | Tracks persistent talent points, character unlock states, beacons, and combo choices. |
| **Polyhedron** | `polyhedron_battle.py` | Builds battle data for Polyhedron runs, validates combat identities, and syncs HP. |
| **Polyhedron** | `polyhedron_flow.py` | Coordinates operation flow, terminal talent updates, snapshots, and settlement order. |
| **Polyhedron** | `polyhedron_login.py` | Reconstructs active exploration, terminal state, and pending rewards upon login. |
| **RogueTeam** | `rogueteam_service.py` | Fictional Deduction engine: map generation, talent trees, and node exploration rules. |
| **RogueTeam** | `rogueteam_flow.py` | Manages run lifecycle, endings, reward distribution, and merchant settlements. |
| **RogueTeam** | `rogueteam_transition.py` | Validates node choices and enforces legal phase transitions in the state machine. |
| **RogueTeam** | `rogueteam_battle.py` | Dedicated battle initialization and settlement for RogueTeam stages. |
| **RogueTeam** | `rogueteam_battle_facts.py` | Tracks post-battle energy and single-directive kill statistics based on real combat logs. |
| **RogueTeam** | `rogueteam_effects.py` | Implements room entry effects, instant rewards, and program upgrade triggers for relics. |
| **RogueTeam** | `rogueteam_tactics.py` | Activates tactical directives when accumulating 6 matching programs; locks active school. |
| **RogueTeam** | `rogueteam_achievement.py` | Compiles victory facts, endings, and relic holdings to push 17 dedicated achievements. |
| **RogueTeam** | `rogueteam_login.py` | Restores map state, team lineup, pending choices, and applies tutorial-skipping policies. |
| **WarChess** | `warchess_service.py` | Coordinates map exploration, grid events, chests, and quests for Casualties Observation. |
| **WarChess** | `warchess_battle.py` | Bridges dedicated WarChess battles, validating map identities and updating results. |
| **Minigames Hub** | `minigame_service.py` | Dispatches minigame operations and coordinates individual minigame services. |
| **Minigames Hub** | `minigame_point_service.py` | Manages activity point rewards, score thresholds, and asset deltas across events. |
| **Minigames Hub** | `offline_minigame_rewards.py` | Awards multiplayer-exclusive task rewards in single-player offline environments. |
| **Autochess** | `autochess_service.py` | Manages PVE Dojo autochess: stage selection, store card rolling, pieces, and rounds. |
| **Autochess** | `autochess_combat.py` | Simulates piece attacks, skills, shields, summons, death queues, and combat outcomes. |
| **Autochess** | `autochess_preparation.py` | Executes skill effects during purchase, sale, round start, and preparation end phases. |
| **Autochess** | `autochess_special.py` | Generates and applies in-game props, upgrade cards, and bonus mechanics. |
| **Autochess** | `autochess_progress.py` | Records first-clear achievements and certificates to advance corresponding quests. |
| **Autochess** | `autochess_login.py` | Restores board configurations and pending settlement states upon login. |
| **Citong Alley** | `quanzhou_quest.py` | Manages 53 main and 129 sub-quests, prerequisites, and milestone reward tracking. |
| **Citong Alley** | `quanzhou_collection.py` | Tracks sailing clears, photo unlocks, nautical skills, and milestone knowledge levels. |
| **Citong Alley** | `quanzhou_service.py` | Preserves map coordinates and bridges sea battles and character duels. |
| **Summer Board Game** | `summer_board_service.py` | Manages 13 board maps, dice rolling, character movement, and match lifecycle. |
| **Summer Board Game** | `summer_board_movement.py` | Computes hexagonal tile traversal, landing events, and auto-movement. |
| **Summer Board Game** | `summer_board_chance.py` | Handles opportunity cards, internal stores, prop candidate draws, and claims. |
| **Summer Board Game** | `summer_board_npc.py` | Routes NPC turns, handling collision events, protection shields, and cutscenes. |
| **Summer Board Game** | `summer_board_props.py` | Validates and applies custom dice, swap cards, roadblocks, and bomb props. |
| **Summer Board Game** | `summer_board_games.py` | Evaluates QTE challenges, dice duels, item rewards, and turn attempts. |
| **Summer Board Game** | `summer_board_nested.py` | Bridges nested minigames and battles while isolating opportunity sessions. |
| **Summer Board Game** | `summer_board_progress.py` | Tracks board missions, chapter clear history, and external reward tasks. |
| **Resident Minigames** | `rogue_card_service.py` | Manages match lifecycles, exits, and state recovery for Night Tales card games. |
| **Resident Minigames** | `rogue_card_progress.py` | Tracks card operations, challenge criteria, and article reads to advance quests. |
| **Resident Minigames** | `resident_music_service.py` | Records rhythm game scores, completion marks, tasks, and reward thresholds. |
| **Resident Minigames** | `pinball_service.py` | Manages Hela Pinball stages, skill equip verification, and combat bridging. |
| **Resident Minigames** | `kadas_race_service.py` | Tracks Kadas Holiday Race routes, modding slots, setups, and challenge scores. |
| **Resident Minigames** | `vehicle_ball_service.py` | Coordinates Mecha Gear challenge stages, vehicle upgrades, and archive rewards. |
| **Resident Minigames** | `hodur_service.py` | Manages Muted Light Chronicle chapters, events, resets, and exploration missions. |
| **Resident Minigames** | `hodur_challenge.py` | Evaluates Extreme Study 4-chain battles with team affixes and HP inheritance. |
| **Resident Minigames** | `ash_service.py` | Tracks Train Heist movie progress, score calculations, and narrative dialogue. |
| **Resident Minigames** | `summer_pub_service.py` | Manages Food & Another World battles, recipes, cooking, pinballs, and tasks. |
| **Resident Minigames** | `osiris_service.py` | Manages Tactical Study Records: 18 study stages, 6 sandbox stages, and tasks. |
| **Resident Minigames** | `skuld_service.py` | Manages Dream Catcher nodes, puzzle locks, first-clears, and chapter collections. |
| **Resident Minigames** | `sphero_schedule_service.py` | Manages Verthandi resident activity daily schedules, commission queues, and timers. |
| **Resident Minigames** | `sphero_battle_service.py` | Handles Verthandi story battles, boss training, proficiency, and daily limits. |
| **Resident Minigames** | `sphero_leisure_service.py` | Manages Verthandi barbecue, 20 puzzle stages, auto-solve, and garland exchanges. |
| **Bundled Tools** | `tools/download_assets.py` | Unified active asset download CLI for PC, Android, and iOS manifests. |
| **Bundled Tools** | `tools/download_pc_assets.py` | Legacy PC asset download entrypoint delegating to unified download queue. |
| **Bundled Tools** | `tools/download_ios_assets.py` | Legacy iOS asset download entrypoint delegating to unified download queue. |
| **Bundled Tools** | `tools/upgrade_legacy_saves.py` | Validates legacy save schema and migrates obsolete AI configurations. |

---

## 6. WinUI 3 Desktop Host & Process Supervisor (`LocalServer`)

The desktop application is built with **WinUI 3 + .NET 10** to supervise the server process and provide an integrated viewer:

* **Job Object Process Management**:
  * Utilizes Win32 `CreateJobObject` and `SetInformationJobObject` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`;
  * When the desktop launcher exits (normally or via an unexpected termination), the Windows kernel cleans up the associated Python child process, preventing port locks from orphan processes;
* **Embedded WebView2**:
  * Employs Microsoft Edge WebView2 to load the web management console once the backend is ready;
* **Streamed Log Redirection**:
  * Asynchronously redirects Python stdout and stderr into a GUI terminal window for easy operational monitoring.

---

## 7. Web Management Console (`web_src` / `static_web`)

The management console is built with **React 19 + TypeScript + Tailwind CSS** and compiled via Vite into `v5_server/static_web`.

> **Note**: The management interface is primarily intended for **data inspection and status viewing**. While certain editing operations exist in code, they are not guaranteed to be robust across all game versions. We recommend treating the UI predominantly as an inspection tool.

* **Optimized Assets**: In-game icons and banners have been converted to WebP for faster browser loading;
* **Key Sections**:
  1. **Overview**: Service uptime, connection statistics, and account summary;
  2. **Accounts**: Stamina timers, assistant character settings, and cosmetic selections;
  3. **Heroes**: Character rosters, breakthrough tiers, astrolabe setups, and attributes;
  4. **Inventory**: Materials, upgrade currencies, and consumables inventory;
  5. **Gacha**: Banner states, pity counters, and 229 / 311 banner switching;
  6. **Agreement & Help**: Operational FAQ and foundational principles;
  7. **Shop**: Store catalogue and product price listings;
  8. **Mail**: Mailbox viewing and test mail dispatches;
  9. **AIChat**: LLM-based dialog interaction with selected characters;
  10. **Settings**: Port monitoring, version mode toggling, and recharge calculation.

---

## 8. Troubleshooting & FAQ

### 8.1 Connection Failure / 4xxxx Client Errors
* **Remedies**:
  1. Confirm your system `hosts` file has the required domain redirection pointing to `127.0.0.1` or the server's real IP;
  2. Verify that `AetherGazer V5 Root CA` has been imported into "Trusted Root Certification Authorities" on PC or installed and trusted on mobile devices;
  3. If using local proxy tools (Clash, v2ray, Surge, etc.), configure loopback addresses and target domains to DIRECT mode to prevent loopback traffic from being intercepted.

### 8.2 Port Already in Use (WinError 10048)
* **Remedies**:
  * Check if ports 443, 80, or 8105 are occupied by existing services (IIS, Apache, Nginx, VMware, etc.);
  * Run `netstat -ano | findstr :443` in the terminal to find the offending PID, then stop it via Task Manager;
  * Make sure to launch the server as **Administrator**, as binding low-number ports requires elevated privileges.

### 8.3 Resource Version Mismatch
* **Remedies**:
  * Ensure the server version parameter (`--res-version`) matches your game client (e.g., use `--res-version 229` for version 229 clients);
  * The server automatically hides 311-specific banners and events when running in 229 mode to prevent client crashes caused by missing local configs.

### 8.4 SSL Handshake Failure / Untrusted Certificate
* **Remedies**:
  * Verify `v5_server/sdk_ca.crt` is intact;
  * Run `certmgr.msc` and check if `AetherGazer V5 Root CA` exists under "Trusted Root Certification Authorities -> Certificates". Re-run `一键安装Windows证书.bat` if it is missing.

---

## 9. Project Milestones & Current Limitations

### Implemented in Current Stage (Stage 1 Completed)
- [x] **Network Pipeline**: Asynchronous TCP/HTTPS local infrastructure with dynamic Protobuf serialization;
- [x] **Core Progression**: Character progression, functors, sigils, chips, battle settlement, shop purchases, and gacha pity systems;
- [x] **Asset Delivery**: Dynamic manifest delivery matching client versions, backed by local asset caching;
- [x] **Desktop Supervision**: WinUI 3 + .NET 10 desktop host with Windows Job Object process supervision;
- [x] **Web Console**: Read-only browser dashboard for status inspection and AI conversation testing;
- [x] **DNS Auxiliary Resolution**: Non-target domain forwarding fixed; UDP/TCP queries and failure responses supported;
- [x] **Costume Gacha Banner**: Exclusive costume and 3D scene lottery pools fully implemented;
- [x] **Save Slot Management**: Multi-save creation, switching, deletion, and 3 starter presets fully operational.

### Known Limitations & Stage 2 Roadmap
- [ ] **Multiplayer Network Blocked**: Multiplayer features and Guild (Co-op) networks are disabled;
- [ ] **Complex Game Modes Progression**:
  - Many resident events involve dozens of unique protocol frames; they are being systematically polished through regression testing;
  - **Citong Alley (Quanzhou)**: 53 main quests, 129 sub-quests, sea battles, and character duels completed; milestone exp yields 2.7x max requirement;
  - **Battle of Dojo (Autochess)**: Overhauled card pools, exp, synthesis, and combat simulation; boss battle unlocks after 4 consecutive wins;
  - **Dimensional Variable (Polyhedron)**: Fixed refresh schedule to every Monday 05:00; refined room selection, shops, recruitment, and reconnect recovery;
  - **Fictional Deduction (RogueTeam)**: Incorporated community patch from [Issue #8](https://github.com/Mint-ARK/Alpha-Persei-Cluster/issues/8), completing the core state machine, endings, and 17 dedicated achievements;
  - Tower Climb and remaining minigames have logic implemented and are undergoing client-side verification;
- [ ] **Potential Program Issues**: You may still encounter occasional issues, such as loading overlay freezes or "Network Connection Failed" pop-ups, typically caused by minor protocol frame field discrepancies or missing fallback mappings;
- [ ] **Gameplay/Experience Anomalies**: Certain gameplay modes may exhibit abnormal item drops or value updates; minor display glitches can often be resolved by reconnecting. If you identify logic or database errors, feel free to [submit an Issue](https://github.com/Mint-ARK/Alpha-Persei-Cluster/issues); every report is warmly appreciated!

---

## Contributors

Special thanks to [@a3164155](https://github.com/a3164155), [@LingYzh](https://github.com/LingYzh), [@baixiaoxiao831](https://github.com/baixiaoxiao831), [@Rachel08311](https://github.com/Rachel08311), and [@qiuyuxc](https://github.com/qiuyuxc) for providing code patches, issue reports, diagnostic materials, and live feedback, and to [@Arborsm](https://github.com/Arborsm) for insightful discussions and suggestions. See the full list in [Contributors](CONTRIBUTORS.md).

---

## 10. Disclaimer & Compliance

1. **Non-Commercial Research Purpose**:
   This project is intended strictly for personal educational research, reverse engineering study, and distributed system exploration. **Under no circumstances should this source code or its binaries be used for commercial profit, paid services, or public online deployment**;
2. **Intellectual Property Rights**:
   All artistic assets, models, audio, voice acting, text scripts, worldbuilding, and trademarks related to *Aether Gazer* belong exclusively to the original developer and publisher. **This repository does not distribute or alter any proprietary binary assets from the official client**;
3. **User Responsibility**:
   Users should remove research files within 24 hours of testing and support the official game product. Any legal liability arising from misuse rests entirely with the individual user; the authors assume no direct or indirect responsibility.
