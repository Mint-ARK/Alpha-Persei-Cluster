# 第117轮｜角色教学恢复与交心重复开启

扫描日期：2026-10-09。问题编号：A193、A194。

## A193｜教学关通关未刷新教学状态，交心条件可能继续显示旧进度

### 出现什么现象

角色教学已通关入库，交心所读的教学缓存却没有更新。

### 初步定位

教学结算只写通用关卡记录，没有47003反馈；47001、47003动态生成也为空，而客户端教学资格读取独立 BattleTeachData。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | type13教学结算 |
| `generator.py` | 47001、47003 |
| `battleteachdata.lua` | 教学缓存 |

## A194｜旧交心开启回调把已有信赖等级和经验重置为默认值

### 出现什么现象

重复请求已开启的交心，会把在线等级和经验重置为1级、零经验。

### 初步定位

服务端保持原数据库值并先推真实14007，再回73021成功；客户端每次成功都无条件执行 UnlockTrust，覆盖为初始状态。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `trust_service.py` | unlock_trust |
| `operations.py` | UnlockTrustOp.respond |
| `herodata.lua` | UnlockTrust |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A150 | [档案心链保存失败，客户端已读状态未撤销](<health_audit_20261009_round101_archive_illustration_recovery.md>) |
| A164 | [交心突破先推新等级再触发客户端加级，在线显示多一级](<health_audit_20261009_round110_trust_growth_contract.md>) |
| A166 | [交心解锁和突破未检查角色、档案与消耗条件](<health_audit_20261009_round110_trust_growth_contract.md>) |
| A189 | [关系剧情新增配置未同步，服务端退回固定旧故事](<health_audit_20261009_round115_super_heart_relation_story.md>) |
| A190 | [关系剧情缺少资格检查，无故事或无角色也能领奖](<health_audit_20261009_round115_super_heart_relation_story.md>) |
| A192 | [后宅进餐只刷新疲劳，信赖经验未同步到客户端](<health_audit_20261009_round116_dorm_feed_anecdote.md>) |
