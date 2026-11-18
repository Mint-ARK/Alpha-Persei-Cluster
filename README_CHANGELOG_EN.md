# Alpha Persei Cluster · Detailed Changelog

[← Back to Main README](./README_EN.md) | [简体中文](./README_CHANGELOG.md) | **English** | [Concise Changelog (`CHANGELOG.md`)](./CHANGELOG.md)

> This document archives detailed release notes and technical changelogs for each version. For project architecture, deployment guides, CLI flags, and module documentation, see [README_EN.md](./README_EN.md).

---

## Current Release: v5.0.5 (2026-11-18)

This release merges community PR fixes, refines iOS SDK reporting behavior, and resolves shop frame and recommended equipment issues. Please visit the [GitHub Releases Page](https://github.com/Mint-ARK/Alpha-Persei-Cluster/releases) to download the latest packages and checksum files.

### Key Update Highlights:

1. **Merged Community PR Fixes (PR #13 / #14 / #15 / #17)**:
   - **PR #13**: Fixed the missing response (`sc_13057`) when querying or updating sigil decomposition settings.
   - **PR #14**: Fixed an empty-context crash in the Dimensional Variable (Polyhedron) clear event (`POLYHEDRON_PASS`) and restored task progress notifications.
   - **PR #15**: Updated battle report parsing to skip empty party slots according to native packet conventions while preserving HP boundary checks, and isolated release-repo battle regression test names.
   - **PR #17**: Updated the Windows desktop launcher (`LocalServer`) to detect system Python 3.14 registry entries and standard repository source directories (`LocalServer/` + `v5_server/`).

2. **iOS SDK Reporting Parameter Adjustment**:
   - Changed `adCodeReportEnable` from `true` to `false` for iPhone/iOS SDK configuration and server responses.

3. **Shop Frame & Catalog Fixes**:
   - Adjusted shop update frames (`sc_20005` / `sc_20013`) to filter duplicate static-refresh items and fix rolling-limit countdown synchronization, resolving PC shop back-button and post-purchase UI glitches.
   - Supplemented missing v321 Sigil R&D shop (Shop 32) entries and automatically backfilled them for both new and existing saves.

4. **Dynamic Recommended Equipment Generation**:
   - Enabled dynamic/full generation of cultivation guide and recommended sigil configurations (`sc_12097`), along with updated sigil suit position mappings.

---

## v5.0.4 (2026-11-14)

This release focuses on overhauling the active CDN download pipeline, resolving save/server selection connection hangs, and introducing three new gacha probability models.

### Key Update Highlights:

1. **CDN Active Download Pipeline & Resource Distribution Overhaul**:
   - Added configurable **bandwidth rate limiting** for active downloads.
   - Fixed disk selection issues for asset storage and resolved a bug where custom disk locations did not propagate to cache index creation.
   - Fixed repeated full index rebuilds after interrupted downloads and significantly optimized **index rebuild performance**.
   - Fixed an issue where newly added official asset manifest versions could not be selected for distribution in the web panel.
   - Adjusted manifest download routing and enabled queuing download tasks directly after checking for updates.
   - Added **voice pack selection** and **asset download mode selection** to the active downloader.
   - Synchronized Build 332 manifests and panel item catalogs, fixed Cross-pool Shifted Flower deductions, and added web panel launch-parameter and cycle management.

2. **Server / Save Selection Hang Fix**:
   - Fixed connection hangs when switching servers or save slots (previously requiring signing out of the SDK and logging in again to recover).

3. **Three New Gacha Probability Models (4 Models Total)**:
   - **Model 0**: Original default model (legacy baseline).
   - **Model 1**: Improved Model 0 that fixes the 90-pull banner first-S guaranteed UP bug to match official rules.
   - **Model 2**: Calibrated main lookup-table model that inversely fits a soft-pity curve from a base rate and target overall rate; includes a default **+0.005 percentage-point margin** to counter RNG seed variance so the long-run rate stays at or above **2.40%**.
   - **Model 3**: Feedback-circuit shadow model ("pulling back lucky streaks and boosting unlucky streaks"); includes a special bias setting where `100` shifts single-pull S-rank probability directly to a guaranteed hit (adjustable in the web panel), plus **cold-start overshoot and large-parameter dilution protections**.

   #### Probability Comparison Across Models

   | Pool | Model 0 (Legacy) | Model 1 (Pity Fix) | Model 2 (Lookup Table) | Model 3 (Feedback) |
   | --- | ---: | ---: | ---: | ---: |
   | 70-Pull Expansion / Standard / Functor | 2.411% | 2.411% | 2.398% | **2.405%** |
   | 90-Pull Anchored (Official Rules) | 2.162% | 2.989% | 2.396% | **2.409%** |
   | 90-Pull Anchored (Legacy Rules) | 2.162% | — | 2.398% | **2.411%** |

   > **Note**: Measured across 4 different random seeds totaling **4,000,000 pulls** via Monte Carlo simulation; all calibrated models converge in probability to the target rate (`0.024`) at large sample sizes.

   #### Model 2 Algorithm & Panel Parameters

   Model 2 exposes three numeric inputs in the panel ([Gacha Panel (`Gacha.tsx`)](v5_server/web_src/src/panels/Gacha.tsx#L223)), with defaults `1.6`, `2.4`, and `0.005` (the first two in `%`, the third in percentage points). The backend automatically fits the soft-pity curve without requiring per-pull manual weights:

   | Input Field | Default | What It Controls |
   | --- | --- | --- |
   | Base Rate (%) | `1.6` | Per-pull probability of any S/5-star before entering soft pity (`1.60%`) |
   | Overall Target (%) | `2.4` | Target long-run S-rank rate including soft and hard pity |
   | Target Margin (pp) | `0.005` | Added onto the overall target (`2.405%` default fit; set `0` for `2.400%`) |

   Because the margin is added to the long-run target, early pulls remain flat at `1.60%`.

   * **Soft-Pity Fitting Formula** (see [fitting implementation (`draw_models.py`)](v5_server/draw_models.py#L74-L99)):
     For the 70-pull banner, pulls 1–65 stay fixed at `1.60%` and pull 70 is guaranteed (`100%`). Pulls $k \in \{66, 67, 68, 69\}$ follow:

     $$P(k) = 1.60\% + (100\% - 1.60\%)\left(\frac{k - 65}{70 - 65}\right)^\gamma$$

     The server uses binary search over $\gamma$ until the renewal-process long-run rate matches the target. With default settings, single-pull S-rank chances at pulls 66–69 are **`13.60%`, `31.30%`, `52.06%`, and `75.10%`**.

   * **90-Pull Rules & Valid Bounds** (see [90-pull renewal rate (`draw_models.py`)](v5_server/draw_models.py#L56-L64)):
     The 90-pull banner enters soft pity at pull 81 by default (off-banner S-ranks count toward the overall rate, while pity resets only upon obtaining the featured UP). Enabling **"90-pull S guarantees UP (Legacy)"** switches soft pity back to pull 66. With a `1.60%` base rate, the shared `(Overall Target + Margin)` across both 70- and 90-pull curves must fall roughly within **`2.3645%–2.4423%`** (e.g., `2.42 + 0.005 = 2.425%` is valid, while values like `3` will report out of bounds).

---

## v5.0.3 (2026-11-06)

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

