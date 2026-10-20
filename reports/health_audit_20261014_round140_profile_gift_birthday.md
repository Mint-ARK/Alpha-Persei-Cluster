# 第140轮｜生日、赠礼预留、语音确认与触摸周期

扫描日期：2026-10-14。问题编号：A295、A296、A297。

## A295｜未设置生日被恢复成默认日期，设置入口和红点消失

### 出现什么现象

从未设置生日的账号显示10月28日，并隐藏设置入口。

### 初步定位

生日恢复把零值、缺行和读取失败统一替换为10、28；客户端依靠month0判断未设置，因而误判已经设置。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | get_birthday_push_payload |
| `generator.py` | 12033 |
| `playeraction.lua` | SetPlayerBirthday |

## A296｜生日只检查月日范围，二月三十日等无效日期也能保存

### 出现什么现象

2月30日等不存在的日期也能保存。

### 初步定位

生日只核月份1—12、日期1—31，没有按月份组合限制；客户端选择器有月长规则，服务端缺少对应检查。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | set_birthday |
| `birthdaysettingview.lua` | 月份天数 |

## A297｜触摸日计数和在线时长计时不一致，签到还会清掉当天次数

### 出现什么现象

同日签到会清掉当天触摸次数并丢失部分看板时长，05:00又不会正确换日。

### 初步定位

外围登录监听无周期水位，每次USER_LOGIN都清每日统计并重设起点；实际换日事件却没有对应消费或触摸时自校验。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | on_peripheral_login、touch_poster_girl |
| `operations.py` | SignOp的USER_LOGIN |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A04 | [新请求清空待发通知，上一次操作的刷新包丢失](<health_audit_20260920.md>) |
| A153 | [大厅音乐和资料选择保存失败，仍回显新值](<health_audit_20261009_round104_lobby_bgm_recovery.md>) |
| A181 | [切换看板娘前未结算旧时长，陪伴时间计到新角色](<health_audit_20261009_round113_oath_producer_feedback.md>) |
| A294 | [资料详情返回固定空画册和生日，与已保存资料不同](<health_audit_20261014_round139_sticker_layout.md>) |
