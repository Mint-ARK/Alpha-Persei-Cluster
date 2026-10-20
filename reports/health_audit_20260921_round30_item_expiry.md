# 第30轮｜道具有效期字段与永久库存的处理方式

扫描日期：2026-09-21。

## 检查结果

| 检查项 | 发现与结果 |
| --- | --- |
| U02：库存身份 | material只按账号和道具ID保存数量，没有同ID多个期限批次；use_items也不使用请求time_valid定位库存。 |
| 不同期限请求 | 同一普通材料库存下，time_valid为0、过去时间或未来时间时，均扣同一份库存，产出期限固定为0。 |
| 项目设计 | 本地服装扮按永久道具使用，期限装扮机制未开发；相关整理见第143轮。 |

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `account_db.py` | material主键与字段 |
| `inventory_service.py` | use_items、余额与扣费 |
| `operations.py` | UseItemOp |
| `materialdata.lua` | 按id和time_valid保存客户端材料 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A53 | [批量使用道具后项失败，前面已扣材料却没有奖励](<health_audit_20260921_round27_fatigue_grants.md>) |
| A54 | [道具选择越界或奖励配置缺失，仍先扣道具再返回成功](<health_audit_20260921_round28_item_use.md>) |
| A55 | [自选箱接受重复选项编号，同一奖励可能重复生成](<health_audit_20260921_round29_reward_rules.md>) |
| A56 | [随机包未命中配置时仍走默认分支发奖](<health_audit_20260921_round29_reward_rules.md>) |
| A282 | [限时场景的有效期写入与选择规则](<health_audit_20261014_round137_profile_scene_skin.md>) |
