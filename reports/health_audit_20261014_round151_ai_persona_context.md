# 第151轮｜AI人格作用域与动态context

扫描日期：2026-10-14。问题编号：A318、A319、A320。

## A318｜清空个人 AI 人格后又回退到全局人格，继续生成回复

### 出现什么现象

明确清空个人AI人格后，仍继续继承旧形态或全局人格。

### 初步定位

保存写入空字符串，但getter只承认非空覆盖，将“明确清空”与“未配置”合并，再回退其他alias及全局值。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `account_db.py` | set_character_persona_prompt与有效人格getter |

## A319｜AI 人格面板读取个人配置却保存全局配置，修改不生效

### 出现什么现象

面板编辑显示为个人的有效人格，保存却只改全局，聊天仍用旧个人覆盖。

### 初步定位

Reader给的是合成有效值，前端按system_prompt提交到全局更新接口，没有携带个人范围；读取范围与写入范围不一致。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `AIChat.tsx` | handleOpenPersona、handleSavePersona |
| `gm_reader.py` | 角色人格 |
| `gm_api.py` | update_persona |

## A320｜AI 人格按展示机体查找，其他别名已有配置却显示为空

### 出现什么现象

角色列表显示无人格，实际聊天却能使用另一形态的个人人格。

### 初步定位

有效getter检查全部hero_ids，Reader另写一套逻辑只查固定头像形态，遗漏其他alias并产生展示与执行分歧。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `gm_reader.py` | AI角色名册人格 |
| `account_db.py` | 有效人格alias解析 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A309 | [AI 一次玩家发言被保存两次，模型上下文出现重复消息](<health_audit_20261014_round148_ai_chat_entry.md>) |
| A310 | [AI 没有人格时游戏入口仍回显成功，并推送未入库提示](<health_audit_20261014_round148_ai_chat_entry.md>) |
| A313 | [AI 面板旧异步请求覆盖当前角色，清空后历史又出现](<health_audit_20261014_round149_ai_history_clear.md>) |
| A316 | [AI 后台任务提交或执行失败，前台仍显示成功且等不到回复](<health_audit_20261014_round150_ai_queue_delivery.md>) |
