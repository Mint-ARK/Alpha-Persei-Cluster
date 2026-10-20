# 第141轮｜昵称面板保存、签名双入口与单账号点赞合同

扫描日期：2026-10-14。问题编号：A298。

## A298｜面板清空签名后旧字段又恢复，页面还会补入示例文字

### 出现什么现象

面板清空签名后，游戏重登会恢复旧签名，面板还可能显示示例文字。

### 初步定位

游戏同时写users.sign和extra.profile_sign，面板只写前者；恢复把空签名退回旧副本，GM读取和React又对空值补默认文案。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `peripheral_service.py` | change_sign与恢复 |
| `Accounts.tsx` | 签名显示 |
| `gm_reader.py` | 资料读取 |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A153 | [大厅音乐和资料选择保存失败，仍回显新值](<health_audit_20261009_round104_lobby_bgm_recovery.md>) |
| A287 | [清空名片角色或标签后，旧登录模板让它们重新出现](<health_audit_20261014_round138_profile_random_decorations.md>) |
