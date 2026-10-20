# 第121轮｜食堂技能、手动场次与每日刷新

扫描日期：2026-10-10。问题编号：A216、A217、A218、A219、A220、A221。

## A216｜食堂设施升级已扣费，收益技能却未生效

### 出现什么现象

升级设施已扣金币，菜品售价却没有获得对应技能加成。

### 初步定位

服务端技能汇总只读取岗位角色，未合并设施当前等级技能；制作按该结果计价，升级因此不影响收益。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `account_db.py` | get_canteen_skill_effects与制作 |
| `dormskilldata.lua` | 设施技能 |
| `canteendata.lua` | 升级回调 |

## A217｜食堂未检查上架资格和槽位容量，超额菜品也能营业

### 出现什么现象

可以上架超过界面容量的菜品种类。

### 初步定位

上架按菜品ID直接新增或替换，没有检查当前已上架种类数；当前默认容量为2，设施配置的额外槽位均为零。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `backhome_service.py` | send_sign_food |
| `dormskilldata.lua` | 槽位容量 |
| `choosesignfoodview.lua` | 选择限制 |

## A218｜食堂手动收益的取整与技能加成计算不一致

### 出现什么现象

手动营业收益在多份菜和技能加成场景下偏低。

### 初步定位

服务端逐条乘倍率并取整，再求和，且没有应用人员和设施收益技能；客户端按菜类汇总、应用加成后统一取整。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `backhome_service.py` | 手动收益计算 |
| `canteenmanualdata.lua` | 收益汇总与取整 |

## A219｜食堂手动场次未关闭，旧营业报告可重复领取收益

### 出现什么现象

同一场手动营业的结束报告可以重复结算。

### 初步定位

服务端只记录营业模式，没有场次身份和待结算、已结算终态；非空报告即可发奖，退出后重试旧报告仍能再次入账。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `backhome_service.py` | 模式切换与手动结算 |
| `canteendata.lua` | 正常结束流程 |

## A220｜食堂人员技能忽略等级门槛，低等级也能启用高阶技能

### 出现什么现象

低等级食堂员工提前获得高等级技能效果。

### 初步定位

技能汇总直接应用角色技能列表，没有按具体形态等级和40、70级槽位门槛过滤，一级和七十级算出相同非空效果。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `account_db.py` | get_canteen_skill_effects |
| `dormdatatemplate.lua` | 技能开启 |
| `dormskilldata.lua` | 等级过滤 |

## A221｜食堂委托大成功判定未计算人员技能加成

### 出现什么现象

委托大成功概率没有计算派遣人员的已开启技能。

### 初步定位

结算只用任务 base_success，未合并人员type8等加成；有效人员技能增加20概率时，固定roll30仍按基础20判失败。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `backhome_service.py` | 委托大成功判定 |
| `dormskilldata.lua` | EntrustExternSucceedAdd |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A197 | [食堂委托把分钟当秒，人员过早释放并恢复疲劳](<health_audit_20261010_round118_backhome_identity.md>) |
| A200 | [食堂售菜未正确扣除食材，扣料失败也继续获得收入](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| A202 | [食堂重新上架未清旧销量和收入，在线与重登状态不同](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| A203 | [食堂手动收益写入错误货币，客户端也未收到资产刷新](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| A205 | [食堂手动领奖调用错误模块，成功回调无法完成](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| A207 | [委托食材已到账，客户端背包没有刷新](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| A208 | [后宅工作状态切换未推进计时，疲劳恢复重复累计](<health_audit_20261010_round119_canteen_revenue_reward.md>) |
| A209 | [委托领奖后未通知人员归还，在线入住状态不同步](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| A211 | [刷新或重复解锁委托会清掉进行中任务，同日还能多次刷新](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| A213 | [食堂设施容量未接入计算，存储上限与客户端不一致](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| A214 | [食堂计时广播参数不匹配，自动制作被跳过](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
| A215 | [食堂未检查菜谱解锁条件，未知菜品也能上架赚钱](<health_audit_20261010_round120_canteen_entrust_facility.md>) |
