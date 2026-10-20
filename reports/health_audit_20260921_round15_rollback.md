# 第15轮｜钥从消耗回滚后的删除通知

扫描日期：2026-09-21。问题编号：A38。

## A38｜钥从消耗已回滚，后续刷新仍带有删除通知

### 出现什么现象

钥从消耗因后续失败而回滚，但下一次刷新仍通知客户端删除该钥从。

### 初步定位

数据库回滚恢复了实例，ctx.deleted_servants 中的 Python 列表没有恢复；异常处理和请求重置也没有清除此字段，旧删除信息进入后续刷新。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `servant_service.py` | consume_servants_as_food |
| `core.py` | _run_operation |
| `middleware.py` | HandlerCtx.reset_operation_state |
| `hero_service.py` | build_hero_refresh_frames |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A03 | 业务已保存但回包失败，客户端重试可能再次扣费 |
| A04 | 新请求清空待发通知，上一次操作的刷新包丢失 |
