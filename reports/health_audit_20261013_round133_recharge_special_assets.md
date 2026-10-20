# 第133轮｜累充高档专属资产与旧新追加

扫描日期：2026-10-13。问题编号：A274。

## A274｜累充和战令装扮被当普通材料发放，名片里无法使用

### 出现什么现象

累充和战令附赠装扮被写成普通材料，实际选择时提示406。

### 初步定位

发奖使用 add_material、add_item旧路由，绕过统一Inventory的装扮及实例分类；player_card等真实持有表没有更新。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `recharge_service.py` | 累充与战令附赠 |
| `inventory_service.py` | 专属资产路由 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A60 | [表情重复拥有判断使用错误编号，重复折算可能漏算](<health_audit_20260921_round33_unique_assets.md>) |
| A63 | [钥从和刻印已入库，在线客户端与图鉴未刷新](<health_audit_20260922_round39_servant_notify.md>) |
| A64 | [抽到角色后缺少解锁刷新，阵营收集进度也不更新](<health_audit_20260922_round40_hero_notify.md>) |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A242 | [周期礼包扣费成功但订阅或首日邮件缺失](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A247 | [活动奖励已入库却未刷新资产，客户端余额和物品不更新](<health_audit_20261010_round127_activity_point_claim_contract.md>) |
