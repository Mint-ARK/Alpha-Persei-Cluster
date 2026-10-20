# 第40轮｜抽卡角色同步

扫描日期：2026-09-22。问题编号：A64。

## A64｜抽到角色后缺少解锁刷新，阵营收集进度也不更新

### 出现什么现象

抽到新角色后存档已有角色，在线列表和阵营收集却未及时更新。

### 初步定位

抽卡直接写 hero 而未交付角色解锁差量；刷新器不消费 touched_heroes，相关生成分支还引用错误表名，阵营刷新也没有重算新档位。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `draw_service.py` | 新角色写入 |
| `operations.py` | DrawOp.respond、_refresh_frames |
| `generator.py` | 角色刷新 |
| `illustrated_listener.py` | 阵营收集 |
