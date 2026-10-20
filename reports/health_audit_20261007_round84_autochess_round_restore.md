# 第84轮｜自走棋胜败与冷恢复

扫描日期：2026-10-07。问题编号：A115。

## A115｜自走棋失败结果未保存，重建对局后被默认判胜

### 出现什么现象

战斗结果尚未结算时重启服务，原本失败的对局可能按胜利处理。

### 初步定位

等待结算状态被保存，但 last_battle_result 没有持久化；恢复后结算读取不到结果便默认取胜利值1。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `autochess_service.py` | launch_round_battle、last_battle_result、settle_battle |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A113 | [自走棋梦影购买次数未恢复，重复购买不累计成长](<health_audit_20261007_round82_autochess_buy_merge.md>) |
