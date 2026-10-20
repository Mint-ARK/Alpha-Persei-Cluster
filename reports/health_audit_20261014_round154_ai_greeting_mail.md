# 第154轮｜问候资格、附件校验与邮件反馈

扫描日期：2026-10-14。问题编号：A328、A329。

## A328｜AI 问候附件未使用白名单与数量上限，重复条目可叠加超限

### 出现什么现象

AI邮件附件声明的白名单和单封上限没有被执行。

### 初步定位

_get_bundle_attachments只取预设，不读whitelist_items、max_per_mail，也未合并重复ID；通用邮件仅做基础过滤，不执行AI专项规则。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `ai_translator.py` | _get_bundle_attachments |
| `ai_gift_whitelist.json` | 专项约束 |
| `mail_service.py` | 通用附件检查 |

## A329｜AI 问候面板读取错误数量字段，零封或失败也显示成功

### 出现什么现象

面板问候结果显示undefined封，没有新增邮件也提示派发成功。

### 初步定位

后端返回 dispatched_count及多种status，前端却读取 generated_mails_count，只以code0判断成功，未处理零新增和no_events。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `gm_api.py` | 问候返回 |
| `AIChat.tsx` | 派发结果提示 |
| `api.ts` | 响应类型 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A04 | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| A05 | [同一封邮件可重复领取附件](<health_audit_20260920_round2.md>) |
| A06 | [邮件发奖失败仍标记已领取，并返回成功](<health_audit_20260920_round2.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A244 | [周期礼包次日邮件已生成，在线未读和红点未刷新](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A309 | [AI 一次玩家发言被保存两次，模型上下文出现重复消息](<health_audit_20261014_round148_ai_chat_entry.md>) |
| A326 | [AI 接口对超时、鉴权和格式错误统一重试，可能重复生成](<health_audit_20261014_round153_ai_timeout_retry.md>) |
| A327 | [AI 问候整批缺少总截止时间，排队与等待时间难以收束](<health_audit_20261014_round153_ai_timeout_retry.md>) |
