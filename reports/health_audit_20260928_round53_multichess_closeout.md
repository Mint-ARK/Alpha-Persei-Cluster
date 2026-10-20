# 第53轮｜多队附加状态保存与恢复

扫描日期：2026-09-28。问题编号：A78。

## A78｜多队编队附加信息未保存，登录无法恢复

### 出现什么现象

多队棋盘位置和 AI 芯片变化没有保存，初始化也无法恢复这些附加数据。

### 初步定位

h_63006 只读取 cont_team、不消费 data；63005 构造器也只输出普通队伍列表，没有专属棋盘数据的读写分支。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `middleware.py` | h_63006 |
| `reserve_codec.py` | build_sc_63005 |
| `generator.py` | 63005 |
| `reserveaction.lua` | SaveMultiChessContData |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A72 | [编队保存和恢复读取不同数据表，重登恢复旧队伍](<health_audit_20260922_round48_reserve_roundtrip.md>) |
| A73 | [同名协议结构混用，编队保存与出战信息解析错误](<health_audit_20260928_round49_reserve_protocol.md>) |
| A74 | [编队请求缺少客户端等待的回复，操作一直等待](<health_audit_20260928_round49_reserve_protocol.md>) |
| A75 | [助战编队字段错位，保存时丢失扩展信息](<health_audit_20260928_round50_assist_fields.md>) |
| A76 | [恢复编队时过滤空位，后排角色被前移](<health_audit_20260928_round51_reserve_slots.md>) |
| A77 | [编队重置只返回成功，实际存档未清除](<health_audit_20260928_round52_reserve_reset.md>) |
