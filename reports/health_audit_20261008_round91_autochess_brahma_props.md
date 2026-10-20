# 第91轮｜梵天特殊阶段与局内道具

扫描日期：2026-10-08。问题编号：A132、A133、A134。

## A132｜自走棋关卡初始等级未应用，购买棋子仍从一级开始

### 出现什么现象

配置要求高初始等级的棋子，刷出后仍是最低等级。

### 初步定位

商店固定生成经验1，开局没有应用场景增益；协议属性12的固定值也没有改变实际棋子经验。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `autochess_service.py` | generate_shop_items、scene_buff、属性12 |

## A133｜梵天自走棋特殊阶段未触发，普通胜场直接结束对局

### 出现什么现象

特殊四胜阶段没有进入升级流程，继续按普通轮次推进。

### 初步定位

专属阶段标记始终为零，没有生成或保存 hero_upgrade_info；升级与一键购买入口只返回空成功回复。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `autochess_service.py` | 特殊阶段与 hero_upgrade_info |
| `operations.py` | 升级及一键购买入口 |

## A134｜自走棋局内道具不生成，使用时又被当棋子购买合成

### 出现什么现象

商店道具没有生成，直接请求购买时又会被当成棋子处理。

### 初步定位

商店缺少道具池；购买虽然接收类型，后续仍统一使用棋子价格、同ID合并和新建棋子逻辑，没有道具目标与增益规则。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `autochess_service.py` | 商店生成与 buy_chess |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A109 | [自走棋读取失败覆盖旧局，保存失败仍返回开局成功](<health_audit_20261007_round80_autochess_entry.md>) |
| A112 | [自走棋把棋子经验当等级，售价和技能档位错误](<health_audit_20261007_round82_autochess_buy_merge.md>) |
| A115 | [自走棋失败结果未保存，重建对局后被默认判胜](<health_audit_20261007_round84_autochess_round_restore.md>) |
| A119 | [自走棋回放返回成功，却缺少录像和行动数据](<health_audit_20261007_round86_autochess_cancel_replay.md>) |
| A123 | [自走棋技能触发时机、目标和参数与配置不一致](<health_audit_20261008_round88_autochess_skills.md>) |
| A124 | [自走棋战斗中的永久成长未保存，下一轮属性恢复原值](<health_audit_20261008_round88_autochess_skills.md>) |
| A130 | [自走棋终败重试清空整局，未恢复上一准备阶段](<health_audit_20261008_round90_autochess_tasks_retry.md>) |
| A131 | [自走棋普通结算保留旧余额，金币结转可能多算](<health_audit_20261008_round90_autochess_tasks_retry.md>) |
