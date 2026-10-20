# 第137轮｜场景、展示换装与作战换装选择保存

扫描日期：2026-10-14。问题编号：A282、A283。

## A282｜限时场景的有效期写入与选择规则

### 出现什么现象

有期限场景被选中后变成永久，过期记录也能选择。

### 初步定位

持有判断只看记录存在，选择又把 lasted_time 清零并建立常驻scene镜像，将选择状态变成新的永久持有来源。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | check_asset_ownership、set_home_scene |

## A283｜看板轮换接受未拥有角色和场景，后续交互记到错误角色

### 出现什么现象

未拥有的皮肤和场景上报后，会改变当前看板身份并产生该角色触摸记录。

### 初步定位

32132只解析数字后覆盖active字段，没有复用角色解锁、皮肤持有和场景资格检查；后续触摸直接使用该身份。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | record_active_board_hero、touch_poster_girl |

本项目装扮按永久道具设计，期限机制的处理范围见[第143轮](<health_audit_20261014_round143_peripheral_closure.md>)。

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A153 | [大厅音乐和资料选择保存失败，仍回显新值](<health_audit_20261009_round104_lobby_bgm_recovery.md>) |
| A181 | [切换看板娘前未结算旧时长，陪伴时间计到新角色](<health_audit_20261009_round113_oath_producer_feedback.md>) |
