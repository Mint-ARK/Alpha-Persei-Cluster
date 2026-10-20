# 第142轮｜大厅纯白、组件、特殊视角与本地／云配置

扫描日期：2026-10-14。问题编号：A299、A300、A301、A302。

## A299｜纯白大厅接受无效选项，客户端按错误编号恢复设置

### 出现什么现象

纯白大厅可以保存超出范围的模式、范围和频率，界面读取时可能找不到控件或计时参数。

### 初步定位

setter与Operation没有参数域检查，直接持久化并恢复；客户端按索引取toggle和随机时间，没有处理这些未知值。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | 纯白模式保存 |
| `puremodesettingpopview.lua` | toggle索引 |
| `puremodeview.lua` | StartRandomHero |

## A300｜大厅桌面接受残缺、重复或无效组件，恢复布局缺少数据

### 出现什么现象

桌面布局可保存缺项、重复和未知组件，恢复时可能缺少尺寸或UI对象。

### 初步定位

setter接受任意非空列表，没有核1—3组件完整且唯一；客户端非空恢复只覆盖给定项，随后直接进行scale运算和组件访问。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | 桌面布局保存 |
| `puremodeview.lua` | InitUI、RefreshComponentPos |

## A301｜特殊场景镜头方向已保存，但登录没有读取并下发

### 出现什么现象

特殊场景镜头方向保存成功，重新初始化却恢复旧方向。

### 初步定位

方向写入extra.sub_poster_background，冷初始化依赖32011；当前generator没有该协议实现，也没有读取这个持久字段。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | set_special_scene_view |
| `generator.py` | 32011 |
| `homescenesettingaction.lua` | 特殊方向初始化 |

## A302｜特殊场景未检查拥有状态、镜头配置与方向范围

### 出现什么现象

未持有场景、未配置镜头方向甚至未知ID也能保存。

### 初步定位

setter只做整数转换并写字典，入口没有检查场景持有、方向配置与解锁条件。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | set_special_scene_view |
| `operations.py` | SetSpecialSceneViewOp |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A137 | [同一成就编号在一次请求中重复出现，会重复发奖](<health_audit_20261008_round93_achievement_claim.md>) |
| A282 | [限时场景的有效期写入与选择规则](<health_audit_20261014_round137_profile_scene_skin.md>) |
| A285 | [随机展示可保存未拥有或未知的换装场景，以及无效参数](<health_audit_20261014_round138_profile_random_decorations.md>) |
| A287 | [清空名片角色或标签后，旧登录模板让它们重新出现](<health_audit_20261014_round138_profile_random_decorations.md>) |
