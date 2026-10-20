# 第7轮｜多维旧战报与终端升级恢复

扫描日期：2026-09-20。问题编号：A24、A25。

## A24｜多维旧战报能推进重置后的新一局

### 出现什么现象

重置并开始新一局后，上一局的迟到胜利仍能推进新一局。

### 初步定位

战斗会话没有把多维对局和节点身份传入结算；on_battle_finish 仅按账号加载当前 run，因此把旧战报当成当前对局的通关结果。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | _settle_frames 的多维分支 |
| `polyhedron_service.py` | PolyhedronRunManager.on_battle_finish |

## A25｜多维终局重置后，恢复数据出现额外的 31 项升级

### 出现什么现象

终端升级重置后，再加载全量状态又出现 31 项升级。

### 初步定位

重置会清空 polyhedron_terminal；get_polyhedron_data 遇到空表却返回写死的 31 个升级编号，而开局读取没有同样的默认分支，显示与实际对局来源不同。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | PolyhedronResetTerminalOp |
| `weekly_challenge_service.py` | get_polyhedron_data |
| `polyhedron_service.py` | start_run |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A18 | 多维变量无对局也能结算，已结算的对局仍可领奖 |
| A22 | 多维终局事件缺少请求上下文，任务监听报错 |
| A23 | 多维先返回成功再发新状态，客户端回调读取旧数据 |
