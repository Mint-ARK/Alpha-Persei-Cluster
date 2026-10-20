# 第65轮｜黑区切档与重置

扫描日期：2026-10-03。问题编号：A89。

## A89｜黑区切换难度保留旧成绩，不同难度进度混合

### 出现什么现象

黑区切换挑战难度后，上一档队伍成绩仍保留，两个难度的进度被拼成通关。

### 初步定位

切档只更新 now_difficulty，不清挑战列表；后续按 team_id 合并成绩，记录又没有独立的难度或挑战实例身份。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `account_db.py` | set_mythic_final_difficulty、record_mythic_final_clear |
| `operations.py` | MythicSelectLevelOp |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A88 | [黑区第一队完成后，未进入第二队待战状态](<health_audit_20261003_round64_mythic_relay.md>) |
