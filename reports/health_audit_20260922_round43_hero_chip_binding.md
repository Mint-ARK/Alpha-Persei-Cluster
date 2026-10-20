# 第43轮｜角色芯片绑定

扫描日期：2026-09-22。问题编号：A67。

## A67｜角色 AI 芯片可装给错误角色或错误槽位

### 出现什么现象

角色 AI 芯片可装给错误角色或槽位，目标角色不存在也返回成功。

### 初步定位

enable_hero_chip 只检查槽位范围和芯片解锁，没有匹配 spec_char、role_type_id 与目标角色；零行更新仍发装配事件。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `chip_service.py` | enable_hero_chip |
| `operations.py` | ChipHeroEnableOp |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A65 | [应用不存在的芯片预设会清空当前装配](<health_audit_20260922_round42_chip_proposals.md>) |
| A66 | [应用芯片预设可绕过解锁、槽位和重复检查](<health_audit_20260922_round42_chip_proposals.md>) |
