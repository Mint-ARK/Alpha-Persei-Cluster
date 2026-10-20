# 第70轮｜迭代校验311与Mode 4领奖

扫描日期：2026-10-07。问题编号：A93。

## A93｜Mode4 领奖未检查完成状态，重复或未知任务也能发奖

### 出现什么现象

Mode4 未完成、重复或未知任务都能触发奖励，空领取列表也会发默认包。

### 初步定位

领奖没有检查任务归属、完成与既领状态；缺配置和空结果又套默认奖励，发奖之后才写唯一领史。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `operations.py` | CoreVerificationMode4GetRewardOp |
| `account_db.py` | claim_core_verification_cl_task |
