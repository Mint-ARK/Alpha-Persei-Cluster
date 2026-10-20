# 第47轮｜芯片装卸、拥有数量与成就计量

扫描日期：2026-09-22。问题编号：A71。

## A71｜芯片解锁或装卸重复增加成就进度

### 出现什么现象

实际拥有的管理喵或芯片数量不变，反复解锁或装卸却使数量成就增加。

### 初步定位

_on_chip_unlock 不区分类别就增加两种拥有数量条件；后续对账保留虚高值，重复事件继续把成就推到奖励门槛。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `achievement_service.py` | _on_chip_unlock、reconcile_achievements |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A65 | [应用不存在的芯片预设会清空当前装配](<health_audit_20260922_round42_chip_proposals.md>) |
| A66 | [应用芯片预设可绕过解锁、槽位和重复检查](<health_audit_20260922_round42_chip_proposals.md>) |
| A68 | [助战芯片未满足前置条件也能解锁](<health_audit_20260922_round44_reviser_unlock.md>) |
| A69 | [未知角色芯片编号可免费创建并下发](<health_audit_20260922_round45_hero_chip_unlock.md>) |
| A70 | [扣费写入后报错，备用逻辑再次扣费](<health_audit_20260922_round46_chip_cost_atomic.md>) |
