# 人机决斗王旧孤立结算局恢复

MG-AUTOCHESS-ORPHAN-RECOVERY-20261105。入口3740601。一阶收口补票，仅本地；未经游戏实测。

## 确认的阻断

此前`autochess_login.prepare_login_frames`对状态3/4/5且缺少可信pending/replay的旧局只记录警告，仍发原结算状态89125，不发初始化战斗对象的89109。原生`autochessaction.lua:70`用89109创建battleData；`autochesstools.lua:17–21、72–92`按结算状态跳到回合结算页；`autochessroundsettleview.lua:133–137`直接读取battleData.selfPlayerData。因此旧局缺战报时存在明确源码阻断，不能以“89128可取消”作为完整UI恢复保证。

## 修改

运行改动仅`autochess_login.py`，复用现有`AutoChessService.cancel_game`，没有改正常战斗算法、奖励或共享login/generator接口。

- 仅在PVE结算状态3/4/5且`pending_round`明确拒绝当前结算凭据时恢复。有效准备局、PREPARE_END、有效待结算局、历史多人局保持原路径；读取错误、坏JSON与异账号战报仍报错，不猜测为可取消旧局。
- 取消前将解析会话、`autochess_session`原始行、`autochess_runtime_v1`原始行完整存入`ops_common`的`autochess_orphan_recovery_v1`。内容SHA256作为item_id，重复相同快照不覆盖原恢复记录。记录稳定原因`invalid_durable_round`、具体拒绝原因、动作`cancel_without_settlement`及时间。
- 按原生取消语义结束该局，返回NONE的89125和空准备表89101；保留89201的真实通关与徽章。冷登录不主动发送仅有请求回调消费者的89129，不制造战报、胜负或奖励。原生选关`autochesspvestageitem.lua:41、57`与`autochesspvedialogueview.lua:84–89`会根据NONE走新局路径。
- 账号货币、英雄、通关、徽章等级、任务已领及积分领史不变。被放弃局的棋子及chess_bonus等局内永久成长完整保存在恢复记录中，不把已取消局的局内状态继承给新局。
- 恢复记录、取消的两份持久状态、89201/89101/89125必要编码和组帧都在同一领域保存点内。实际写后失败、必要编码失败和调用方捕获嵌套异常均回滚；不把恢复记录与活动局状态写成半组。

## 验证

`python -B tools/verify_autochess_login.py`：17项通过，含三结算状态缺旧runtime、错battle_uid、普通/新档缺帧与旧静态重复帧、NONE后重新开局、重复登录幂等、有效待结算原样恢复、账号资产及徽章成长保留、恢复副本精确保存、三处真实写后故障、必要89125为空/None、嵌套保存点、禁用登录策略及异账号战报拒绝。

`python -B tools/verify_autochess_persistence.py`：原23项通过，含冷持久恢复、胜败/首通/重试、原生取消及响应失败回滚。合计40项内存AccountDB/Core/codec/真实洪流序列检查，没有重新运行整套小游戏测试，也未运行Unity。

首轮夹具使用旧hero.detail列，但当前内存模板已是hero.level结构；修正夹具后通过，未因此更改业务。早期失败日志保留。既有middleware载入skeleton的ResourceWarning不属于本票，未扩修。

## 证据与边界

`archive/autochess_orphan_recovery_20261105/`保留两个修改文件原件、`login_checks_v1.log`、`login_checks_final.log`与`persistence_regression.log`。未访问或写真实数据库，未启动/重启服务、SSH/SCP、改Lua、Git或封包。

本票替代`repair_20261105_autochess_finish.md:32`“旧缺战报只能人工89128取消”的遗留；正常一期与约定303四胜Boss路线仍可按一阶源码完成收口，A133官方管理员流程未还原的既有边界保留。客户端实际重登/动画/选关仍待用户实机验证。
