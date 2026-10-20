# 第6轮｜多维任务联动、状态回包与对局归属

扫描日期：2026-09-20。问题编号：A22、A23、A24。

## A22｜多维终局事件缺少请求上下文，任务监听报错

### 出现什么现象

多维结算返回成功，但任务监听报错，任务进度和通知没有跟上。

### 初步定位

settle_run 发布 POLYHEDRON_PASS 时把 ctx 传为 None，任务监听立即读取 ctx.db 而报错；总线吞掉异常后，主结算继续成功，成就通知也无法挂入原请求。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `polyhedron_service.py` | settle_run |
| `task_listener.py` | on_polyhedron_pass |
| `achievement_service.py` | _on_polyhedron_pass |
| `operations.py` | 多维结算调用入口 |

## A23｜多维先返回成功再发新状态，客户端回调读取旧数据

### 出现什么现象

选奖励或选关成功后，客户端回调可能仍依据旧多维状态跳转。

### 初步定位

回复列表先放成功 ACK，再放新的全量状态。奖励回调收到 ACK 就读取当前存档阶段，选关回调也会直接启动战斗，此时新状态尚未消费。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | PolyhedronRewardOp.respond、PolyhedronSelectStageOp.respond |
| `polyhedronaction.lua` | 奖励和选关成功回调 |
| `operations.py` | _build_18003_frame 实际生成 18001 状态包 |

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
