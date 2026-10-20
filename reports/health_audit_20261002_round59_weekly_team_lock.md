# 第59轮｜梦境队伍与英雄锁定

扫描日期：2026-10-02。问题编号：A84。

## A84｜梦境可使用已被其他首领锁定的角色

### 出现什么现象

被其他梦境首领锁定的角色，仍可保存进当前队伍并建立战斗会话。

### 初步定位

保存只更新 last_heroes_cfg，没有比对其他首领 used_heroes；开战登记前也没有执行同一角色锁定检查。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | BossChallengeSaveHardTeamOp |
| `account_db.py` | save_boss_challenge_hero_team |
| `middleware.py` | h_54030 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A82 | [梦境首领匹配失败时仍登记成绩](<health_audit_20261002_round57_weekly_settlement.md>) |
