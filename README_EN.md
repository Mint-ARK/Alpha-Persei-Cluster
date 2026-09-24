# Alpha Persei Cluster

<div align="center">

[简体中文](./README.md) | **English**

![Platform](https://img.shields.io/badge/Platform-Windows%2010%2B%20%7C%20x64-0078D6?style=flat-square&logo=windows)
![.NET Version](https://img.shields.io/badge/.NET-10.0%20(WinUI%203)-512BD4?style=flat-square&logo=dotnet)
![Python Version](https://img.shields.io/badge/Python-3.10%2B%20(asyncio)-3776AB?style=flat-square&logo=python)
![Frontend](https://img.shields.io/badge/Frontend-React%2019%20%7C%20TailwindCSS-61DAFB?style=flat-square&logo=react)
![License](https://img.shields.io/badge/License-Non--Commercial%20Research-grey?style=flat-square)
![Status](https://img.shields.io/badge/Milestone-Stage%201%20Completed-brightgreen?style=flat-square)

<p align="center">
  <b>A local offline simulation server (LocalServer) developed for Aether Gazer, designed to keep the game playable via local host redirection even if official game servers become unavailable.</b>
</p>

</div>

---

## Table of Contents

- [1. Project Overview & Design Philosophy](#1-project-overview--design-philosophy)
- [2. System Architecture Diagram](#2-system-architecture-diagram)
- [3. Quick Start & Setup Guide](#3-quick-start--setup-guide)
  - [3.1 Prerequisites](#31-prerequisites)
  - [3.2 Mode A: Desktop Launcher (Recommended)](#32-mode-a-desktop-launcher-recommended)
  - [3.3 Mode B: Command-Line Interface (CLI)](#33-mode-b-command-line-interface-cli)
  - [3.4 Client Traffic Redirection](#34-client-traffic-redirection)
- [4. CLI Arguments Reference](#4-cli-arguments-reference)
- [5. Core Server Architecture: Breakdown of all 70 Modules](#5-core-server-architecture-breakdown-of-all-70-modules)
  - [5.1 Main Assembly & Lifecycle Entrypoints (4 modules)](#51-main-assembly--lifecycle-entrypoints-4-modules)
  - [5.2 Network Transport & Listeners (6 modules)](#52-network-transport--listeners-6-modules)
  - [5.3 Session Scheduling & Dispatch Pipeline (5 modules)](#53-session-scheduling--dispatch-pipeline-5-modules)
  - [5.4 Dynamic Protobuf Codec & Response Assembly (7 modules)](#54-dynamic-protobuf-codec--response-assembly-7-modules)
  - [5.5 Data Persistence & Migration (2 modules)](#55-data-persistence--migration-2-modules)
  - [5.6 Core Business Domain Services (25 modules)](#56-core-business-domain-services-25-modules)
  - [5.7 Combat & Battle Subsystems (5 modules)](#57-combat--battle-subsystems-5-modules)
  - [5.8 AI Companion & Dialogue Subsystem (7 modules)](#58-ai-companion--dialogue-subsystem-7-modules)
  - [5.9 Operations, Logging & Event Listeners (9 modules)](#59-operations-logging--event-listeners-9-modules)
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

## 2. System Architecture Diagram

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

## 3. Quick Start & Setup Guide

### 3.1 Prerequisites

* **Operating System**: Windows 10 or later (x64 recommended), with Microsoft Edge WebView2 Runtime and WinUI 3 support available;
* **Dependencies**:
  * **Python 3.10+**;
  * **.NET 10.0 Runtime / SDK** (if running the desktop launcher);
  * **Microsoft Edge WebView2 Runtime** (pre-installed on Windows 11);
* **Permissions**: Modifying the system `hosts` file and binding privileged ports (such as 443 and 80) require Administrator privileges. Run terminal scripts or the launcher as Administrator.

### 3.2 Mode A: Desktop Launcher (Recommended)

1. **Launch Desktop Host**:
   ```powershell
   cd LocalServer
   dotnet run -c Release
   ```
2. **Install Local Root Certificate**:
   Follow the prompt on the desktop interface to install the self-signed root certificate (required for the game client to trust the local HTTPS authentication service).
3. **One-Click Service Supervision**:
   Click **"Start Service"** on the GUI. The host will check port availability, start the Python backend, and render the management dashboard in the embedded WebView2 view.

### 3.3 Mode B: Command-Line Interface (CLI)

Ideal for headless environments or direct Python debugging:

1. **Install Python Dependencies**:
   ```powershell
   cd v5_server
   pip install -r requirements.txt
   ```
2. **Install Root CA** (Run as Administrator):
   ```powershell
   ..\一键安装Windows证书.bat
   ```
3. **Start Core Server**:
   ```powershell
   python main.py
   ```
4. **Access Management Console**:
   Open the URL printed in the console (e.g. `http://127.0.0.1/web/gm_console.html`) in your browser.

### 3.4 Client Traffic Redirection

The game client queries official domains for metadata and login authentication. For local offline play, traffic must be redirected to `127.0.0.1`:

#### Method 1: System HOSTS Redirection (Recommended for Windows PC)
Add static resolution entries to `C:\Windows\System32\drivers\etc\hosts` pointing to `127.0.0.1`. The desktop launcher includes a one-click host configuration tool (domain names are omitted here for security and compliance).

#### Method 2: Mobile Device (iOS) LAN Redirection
1. Connect both the PC and mobile device to the same local Wi-Fi network;
2. Start the server with LAN IP binding and DNS forwarding enabled:
   ```powershell
   python main.py --host-ip 192.168.1.100 --dns
   ```
3. In iOS Wi-Fi settings, manually set the DNS server to the PC's LAN IP (`192.168.1.100`);
4. Open Safari on iOS, navigate to `http://192.168.1.100/sdk_ca.mobileconfig`, then install and trust the profile;
5. **Note**: Due to limited testing hardware, only iOS has undergone preliminary verification. Android and HarmonyOS have not yet been systematically tested, though the underlying networking principles remain comparable.

---

## 4. CLI Arguments Reference

Run `python main.py --help` in the `v5_server` directory to inspect available options:

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--host-ip` | `string` | Auto LAN IP | Bound local IPv4 address, used to provide connection metadata for mobile clients |
| `--https-port` | `int` | `443` | HTTPS auth & SDK port (privileged port; requires Administrator) |
| `--gw-port` | `int` | `8102` | TCP Gateway port for the initial client connection handshake |
| `--game-port` | `int` | `8105` | TCP Game port carrying primary runtime protocol packets |
| `--battle-port` | `int` | `6105` | UDP battle server port (pass `0` to disable the standalone battle server) |
| `--db` | `string` | `account.db` | Path or filename of the local SQLite database |
| `--res-version` | `enum` | `auto` | Target asset version baseline: `auto` (auto-detect), `229`, or `311` |
| `--client-assets-dir` | `string` | Empty | Path to the client's `StreamingAssets` directory for version detection |
| `--capture-cdn` | `flag` | `True` | Forward CDN requests to official mirrors and cache downloaded assets locally |
| `--no-capture-cdn` | `flag` | - | Disable external CDN probing; operate strictly offline |
| `--dns` | `flag` | `False` | Run a lightweight UDP:53 DNS redirection server (requires Administrator) |
| `--log-level` | `enum` | `INFO` | Console output verbosity: `DEBUG`, `INFO`, `WARN`, `ERROR` |
| `--self-check` | `flag` | - | Validate component initialization and operation registrations, then exit |

---

## 5. Core Server Architecture: Breakdown of all 70 Modules

The 70 Python modules in `v5_server` are systematically divided into 9 functional subsystems:

### 5.1 Main Assembly & Lifecycle Entrypoints (4 modules)

* **`main.py`**: Server entrypoint. Handles argument parsing, environment and port checks, initializes the core dispatch engine, and launches asynchronous network listeners;
* **`login.py`**: Login flow coordinator. Reads login push templates and SQLite data, preparing initial state synchronization and establishing user sessions;
* **`server_daemon.py`**: Background daemon helper providing cross-platform PID tracking, health monitoring, and shutdown hooks;
* **`replay.py`**: Protocol replay and packet debugging script. Loads serialized packets to simulate client traffic (legacy utility, largely unused except during specialized debugging).

### 5.2 Network Transport & Listeners (6 modules)

* **`server_net.py`**: Multi-protocol network gateway. Listens on TCP 8102 (Gateway) and 8105 (Game), provides HTTPS (443) and HTTP (80) routing, and serves static files and WebP assets;
* **`transport.py`**: Binary TCP transport framer. Handles streaming packet boundaries, magic bytes, payload length calculations, and sequence numbers;
* **`dns_server.py`**: Lightweight UDP:53 DNS server for redirecting target domains to the server machine's local IP;
* **`cdn_proxy.py`**: Transparent CDN proxy and caching module. Retrieves remote asset patches requested by clients and writes them to local storage;
* **`fetch_manifests.py`**: Remote asset manifest fetcher and parser for indexing version hashes;
* **`gen_cert.py`**: Self-signed Certificate Authority (CA) and SSL certificate generator, with support for exporting `.mobileconfig` profiles for iOS.

### 5.3 Session Scheduling & Dispatch Pipeline (5 modules)

* **`core.py`**: Session dispatcher and packet router. Parses sequence numbers (`sn`), builds `OperationContext` transaction envelopes, and catches unhandled exceptions;
* **`middleware.py`**: Pipeline middleware. Performs basic session validation, heartbeat maintenance, skeleton packet completion, and outbound frame scheduling;
* **`operations.py`**: Central registry of 130+ native operation handlers, managing parameter checks, database mutations, and downstream packet routing;
* **`event_bus.py`**: Lightweight in-process publish/subscribe (Pub/Sub) event bus, decoupling domain services;
* **`protocol_ref.py`**: Protocol reference table mapping packet IDs (1xxxx~9xxxx) and error code semantics.

### 5.4 Dynamic Protobuf Codec & Response Assembly (7 modules)

* **`codec.py`**: Pure-Python dynamic Protobuf wire-format encoder/decoder, supporting schema parsing without precompiled proto stubs;
* **`generator.py`**: Outbound packet generator. Assembles initial login sync packets, incremental pushes, and business responses according to protocol structures;
* **`decode_schema.py`**: Protocol schema reverser and field dictionary extractor;
* **`hero_codec.py`**: Serializer tailored for character trees, astrolabe nodes, and warp skill configurations;
* **`reserve_codec.py`**: Serializer for team presets and lineup configurations;
* **`daily_fatigue_codec.py`**: State management and `sc_12045` packet assembler for twice-daily stamina deliveries (11:00 and 18:00);
* **`schema_default.py`**: Default field value inferrer and placeholder provider, preserving backward compatibility across protocol revisions.

### 5.5 Data Persistence & Migration (2 modules)

* **`account_db.py`**: SQLite database abstraction layer. Manages connections, schema initialization, transactions, and common queries;
* **`normalize_db_transitions.py`**: Data migration utility. Normalizes legacy nested list formats in the `exclusive_skill_list` column of the `hero` table into standard JSON format.

### 5.6 Core Business Domain Services (25 modules)

1. **`hero_service.py`**: Modifier character progression service. Handles character exp, breakthroughs, skill leveling, astrolabe nodes, and warp skills;
2. **`draw_service.py`**: Gacha summoning service. Manages 4 pity pools (Precision 90, Precision 70, Standard 70, Functor 70), drop rates, anti-dupe guarantees, duplicate shards, and dynamic pool lists;
3. **`inventory_service.py`**: Inventory and asset management. Centrally handles currencies, items, materials, sigils, and functors, generating atomic delta frames (`sc_17023`);
4. **`achievement_service.py`**: Achievement system. Listens to domain events, tracking 478 achievements, pushing banner notifications (`sc_53003`), and managing achievement stories and point claims;
5. **`fatigue_service.py`**: Stamina natural recovery service. Recovers 1 stamina point every 360 seconds (6 minutes) and calculates dynamic caps based on player level;
6. **`equip_service.py`**: Sigil equipment service. Supports leveling (with low-tier exp material refunds on excess), breakthroughs, enchantments, gen-zone reconstruction, slot equips, and suit inheritance;
7. **`servant_service.py`**: Weapon Functor service. Tracks unique functor instances by ID, handling awakening (sleeping children to signature functors), transcendence, locking, equipping, and salvaging;
8. **`chip_service.py`**: Chip system. Coordinates Admin Cat Mimir chips and presets, character-specific AI combat chips (4 slots), and support tactic chips;
9. **`stage_service.py`**: Stage and chapter progression. Manages main story, subplots, resident events, and resource stages, including 3-star conditions, first-clear rewards, and progression presets;
10. **`mail_service.py`**: Mail and Special Letters archive service. Handles inbox management, attachments claiming, and character birthday letters;
11. **`shop_service.py`**: Store and trade system. Manages regular token shops, packs, daily shop slot draws and manual refreshes, and periodic stock resets;
12. **`recharge_service.py`**: Payment simulation service. Recognizes item IDs, handles initial double-bonus simulation, accumulates recharge points, and grants cumulative tier rewards;
13. **`trust_service.py`**: Trust and affection service. Manages affection levels (Lv.1~Lv.5), gift interactions, combat mood modifiers, and relationship network progression;
14. **`oath_service.py`**: Oath system. Supports oath ceremonies, 3D chapel protocols, 24 exclusive hero assignments, and photo gallery storage for supported heroines (Verthandi, Izanami, Thoth);
15. **`backhome_service.py`**: Dormitory and canteen service (58xxx protocols). Handles canteen recipe queues, dorm furniture placement, character stamina recovery, and commissions;
16. **`admin_cat_explore_service.py`**: Admin Cat exploration (idle dispatch) service. Manages dispatch queues, offline reward calculations, exploration levels, zone unlocks, and cat upgrades;
17. **`polyhedron_service.py`**: Dimensional Variable (Polyhedron) rogue-like engine. Handles stage tree pathing, beacons, terminal talents, shop rooms, and run settlements *(Note: this system has known bugs and incomplete features)*;
18. **`weekly_challenge_service.py`**: Weekly challenge rotator. Handles automated period rotation (Monday / Thursday 05:00), boss rotations, and affixes for Recurring Dream and Hazard Zone;
19. **`rogueteam_service.py`**: Challenge Rogue Team (Fictional Deduction) service. Handles tech trees, branching nodes, events, and relics *(Note: this mode is currently an unfinished work-in-progress)*;
20. **`autochess_service.py`**: Dojo AutoChess service. Handles PVE stage selection, store card rolling, piece synthesis, and local combat playback simulations; PVP matching is safely blocked with alerts *(Note: lacks comprehensive testing)*;
21. **`minigame_service.py`**: Lightweight resident minigames service. Handles progression and scoring for specific minigames like Hela Pinball, Summer Tank Race, and Ash Cowboy Shooting;
22. **`periodic_gift_service.py`**: Multi-day supply pack service. Manages daily email distribution and fulfillment for 7-day, 14-day, and season-pass style subscriptions;
23. **`peripheral_service.py`**: Profile and customization service. Manages avatars, frames, card backgrounds, signatures, main lobby scene/BGM changes, assistant interactions, and sticker layouts;
24. **`archive_service.py`**: Character profile and Heart-Link story service. Maps character base identities to battle forms, unlocking Heart-Link stories and anecdotes;
25. **`activity_lottery.py`**: Activity chest lottery service. Awards chances at exclusive skins, 3D scenes, and vouchers from daily/weekly activity chests, with automatic duplicate refunds into Shifted Flowers.

### 5.7 Combat & Battle Subsystems (5 modules)

* **`battle_server.py`**: Standalone UDP battle server. Matches the XServer protocol header (9-byte header / 20-byte sequence retransmission), managing ping/pong keepalives and fragmented frame forwarding;
* **`battle_payload.py`**: Battle initialization payload builder. Dynamically serializes real-time character stats, functors, astrolabes, and sigil enchants to assemble the `sc_54003` combat start frame;
* **`cooperation_skill_server.py`**: Ultimate Skill Combo tracking service. Tracks combo usage in normal and high-difficulty stages, reporting progress to `trust_service` to unlock combo upgrades;
* **`team_server.py`**: Active team lineup memory service. Records stage-specific team configurations to preserve lineup presets across sessions;
* **`mythic_affix_cfg.py`**: Hazard Zone and high-difficulty affix database. Manages environmental buffs/debuffs, boss-specific mechanisms, and semi-weekly rotations.

### 5.8 AI Companion & Dialogue Subsystem (7 modules)

* **`ai_bot_service.py`**: Large Language Model (LLM) interaction service, enabling immersive chat conversations with game characters;
* **`ai_bot_config.py`**: AI configuration manager. Loads and validates API credentials (OpenAI, Gemini, DeepSeek, etc.) and model tuning parameters;
* **`ai_context_builder.py`**: Context and system prompt assembler. Compiles prompts based on character lore, bond levels, and recent dialog history;
* **`ai_history_manager.py`**: Chat history persistence manager. Stores conversation logs in local JSONL format;
* **`ai_calendar.py`**: Solar terms and character birthday calendar engine, triggering greetings and mail on specific dates;
* **`ai_bot_leaderboard.py`**: Model benchmark lookup helper (provides non-critical reference data; users are encouraged to check official model sites directly);
* **`ai_translator.py`**: Translation helper mapping in-game item identifiers and sticker emoji syntax into tokens understandable by LLMs.

### 5.9 Operations, Logging & Event Listeners (9 modules)

* **`gm_api.py`**: HTTP API service. Exposes read-only and diagnostic endpoints for account data, inventory, and gacha statuses to the Web UI;
* **`gm_reader.py`**: Static configuration reader for querying read-only game tables for the management console;
* **`res_version_manager.py`**: Resource version manager. Manages version asset hashes for builds 229 and 311, handling dynamic version switching;
* **`task_listener.py`**: Task system event subscriber. Listens to gameplay events, updating daily, weekly, and achievement criteria, and dispatching progress frames (`sc_28007`);
* **`timer_listeners.py`**: Periodic time event listener. Subscribes to `lazy_timer` events to trigger stamina recovery, daily/weekly 05:00 resets, and shop refreshes;
* **`lazy_timer.py`**: Inbound-driven lazy timestamp tracker. Calculates 05:00 reset boundaries on incoming requests without requiring background polling loops;
* **`logger.py`**: Base logging engine, providing colored console output and daily log rotation;
* **`log_sifter.py`**: Dual-track log filter. Separates raw protocol debug logs (written to file) from readable console notifications, collapsing high-frequency heartbeats;
* **`illustrated_listener.py`**: Gallery and collection event listener. Tracks stage clears, story reads, and unlocks to update gallery indexes and loading wallpapers.

---

## 6. WinUI 3 Desktop Host & Process Supervisor (`LocalServer`)

The desktop application is built with **WinUI 3 + .NET 10** to manage the server process and provide an integrated viewer:

* **Job Object Process Management**:
  * Utilizes Win32 `CreateJobObject` and `SetInformationJobObject` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`;
  * When the desktop launcher exits (normally or via an unexpected termination), the Windows kernel cleans up the associated Python child process, preventing port locks from orphan processes;
* **Embedded WebView2**:
  * Employs Microsoft Edge WebView2 to load the web console once the backend is ready;
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
  1. Confirm your system `hosts` file has the required domain redirection pointing to `127.0.0.1`;
  2. Verify that `AetherGazer V5 Root CA` has been imported into "Trusted Root Certification Authorities";
  3. If using local proxy tools (Clash, v2ray, etc.), configure loopback addresses and target domains to DIRECT mode to prevent loopback traffic from being intercepted.

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
- [x] **Web Console**: Read-only browser dashboard for status inspection and AI conversation testing.

### Known Limitations & Stage 2 Roadmap
- [ ] **Multiplayer Network Blocked**: Multiplayer features and Guild (Co-op) networks are disabled;
- [ ] **Complex Game Modes Unverified**:
  - Many resident events involve dozens of unique protocol frames; they have only been adjusted to avoid hard client hangs and have not been systematically verified;
  - Citong Port, Summer Arena, and Battle of Dojo currently experience significant issues;
  - Dimensional Variable (Polyhedron) remains unstable;
  - Challenge Rogue Team (Fictional Deduction) is unfinished and remains a work-in-progress;
  - Tower Climb has no protocol implementation; AutoChess and Verthandi events lack rigorous testing;
- [ ] **Automated Account Migration**: Packet analysis and database conversion tools have not yet been automated;
- [ ] **Costume Gacha Banner**: Exclusive costume banner mechanics have not yet been implemented.

---

## 10. Disclaimer & Compliance

1. **Non-Commercial Research Purpose**:
   This project is intended strictly for personal educational research, reverse engineering study, and distributed system exploration. **Under no circumstances should this source code or its binaries be used for commercial profit, paid services, or public online deployment**;
2. **Intellectual Property Rights**:
   All artistic assets, models, audio, voice acting, text scripts, worldbuilding, and trademarks related to *Aether Gazer* belong exclusively to the original developer and publisher. **This repository does not distribute or alter any proprietary binary assets from the official client**;
3. **User Responsibility**:
   Users should remove research files within 24 hours of testing and support the official game product. Any legal liability arising from misuse rests entirely with the individual user; the authors assume no direct or indirect responsibility.
