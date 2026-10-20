# 第153轮｜AI总时限、重试与登录排队

扫描日期：2026-10-14。问题编号：A326、A327。

## A326｜AI 接口对超时、鉴权和格式错误统一重试，可能重复生成

### 出现什么现象

AI请求读取超时、401或响应格式错误时，适配器都会再次提交相同生成请求。

### 初步定位

固定两次重试包住POST、读取及解析全部异常，没有区分提交前失败、结果未知和明确不可重试，也没有业务请求身份和结果复用。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `ai_bot_service.py` | _call_openai_compatible、call_llm |

## A327｜AI 问候整批缺少总截止时间，排队与等待时间难以收束

### 出现什么现象

登录问候不断排队，单批也没有整体截止时间。

### 初步定位

每次登录都向单worker队列提交，未按账号日期合并；批次等待所有future和同步总览，没有总预算、过时取消及迟到结果归属。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `ai_translator.py` | trigger_login_greeting与批次等待 |
| `gm_api.py` | 同步问候入口 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A309 | [AI 一次玩家发言被保存两次，模型上下文出现重复消息](<health_audit_20261014_round148_ai_chat_entry.md>) |
| A320 | [AI 人格按展示机体查找，其他别名已有配置却显示为空](<health_audit_20261014_round151_ai_persona_context.md>) |
| A321 | [AI 问候生成成功但邮件或防重记录失败，重试反复生成](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| A322 | [AI 问候部分失败仍标记整批完成，漏发角色无法补发](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| A324 | [AI 角色别名未统一，静音不生效且同一角色重复问候](<health_audit_20261014_round152_ai_greeting_cost.md>) |
| A325 | [AI 首次并发创建出两个服务实例，同一问候重复进入生成](<health_audit_20261014_round152_ai_greeting_cost.md>) |
