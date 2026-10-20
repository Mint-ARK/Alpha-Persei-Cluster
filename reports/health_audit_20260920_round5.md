# 第5轮｜多维重复结算、奖励放弃与战斗并发

扫描日期：2026-09-20。问题编号：A18、A19、A20、A21。

## A18｜多维变量无对局也能结算，已结算的对局仍可领奖

### 出现什么现象

没有开始多维探索也能查询结算收益；同一局结算后再次查询仍增加收益。

### 初步定位

settle_run 在对局不存在时使用默认难度和层数计算奖励，已有对局也没有按终态拒绝重复发奖。查询入口直接调用这段结算。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `polyhedron_service.py` | PolyhedronRunManager.settle_run |
| `operations.py` | PolyhedronSettleQueryOp、PolyhedronRewardOp |

## A19｜多维变量无待选奖励时仍能反复放弃并获得货币

### 出现什么现象

离开奖励阶段、没有待选奖励后，仍可反复放弃并增加局内金币。

### 初步定位

select_reward 只检查对局存在，没有检查奖励阶段和奖励是否已消费；无效选择仍进入放弃奖励分支，加币后继续生成后续状态。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `polyhedron_service.py` | PolyhedronRunManager.select_reward |
| `operations.py` | PolyhedronRewardOp |

## A20｜同一场战斗可被两个请求同时结算

### 出现什么现象

同一账号通过两个请求同时提交一场战斗结果，两次都能进入结算。

### 初步定位

会话锁只保护字典读取，没有覆盖结果检查、业务结算和缓存写入。两个请求可能同时看到尚未结算，并分别进入 _settle_frames。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | get_battle_session、h_54032、_settle_frames |

## A21｜同毫秒建立的不同连接可能生成相同战斗编号

### 出现什么现象

同一毫秒内，不同连接可能分配出相同战斗编号，新会话覆盖旧会话。

### 初步定位

战斗编号由毫秒时间和连接自己的局部序号拼接；不同连接的序号都从零开始。注册会话时按编号直接覆盖，没有冲突检查。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | _gen_battle_id、register_battle_session |
| `middleware.py` | HandlerCtx.battle_seq |
