# 虚构推演：隐藏中继岛的原生契约（只读核对）

结论：second_map属于`rogueteam`虚构推演，不属于`roguecard`诡谈夜话。中继岛有原生入口、实际事件链、带奖励的返回流程，不能把`sign=2/3`当普通下一层。以下未修改任何Rogue运行文件。

## 必须实现或明确封闭的路径

| 行为 | 客户端/配置证据 | 必要服务端语义 |
|---|---|---|
| 进入隐藏图 | `rogueteameventoptioncfg.lua:4490`，600221“前往中继岛”；`rogueteameffectcfg.lua:3107`，6002211 action19 params[1] | 只有实际可选该事件选项时建立隐藏图；保存原主图、主层、来源节点和待切换身份；不是加商店折扣 |
| 双图同步 | `challengerogueteampathdata.lua:46-61`同时读map_info与second_map_info；`subclass/challengerogueteammapdata.lua:20-45`读每个node_id/col/row/node_type/next_id_list/state/param | 登录88001、节点88003必须同时提供当前事实；不要把隐藏图覆盖到主图而丢失返程现场 |
| 服务端要求切图 | `challengerogueteamaction.lua:133-136`读88221.sign；`challengerogueteamconst.lua:18-22` NORMAL1/ENTER_HIDDEN2/EXIT_HIDDEN3 | sign是服务端已经产生的待切换动作；客户端88008上传同sign，不能由任意客户端参数开图/跳层 |
| 切图确认 | `challengerogueteamaction.lua:248-276`，88008→88009成功后清切换flag；sign2调用CheckFinishNode，sign3调用PathExitSecondMap并恢复主图最后完成路径的动效 | 2进入隐藏、3回原主图；重复确认要幂等或明确失败，不能重复给奖励或继续普通层+1 |
| 返回主图及奖励 | option600421/600521/600531文案“获得维修工具箱，返回岱屿空岛”；effect6004211/5211/5311 action6给1330003，随后6004212/5212/5312 action20 | 物品与返程准备必须同一事务；不是清除负面圣物。应保留原生item1330003，不因结局猜测替换 |
| 保留真实主层 | `challengerogueteamtools.lua:140-148`隐藏图显示用102；`rogueteammapfloorcfg.lua:29-35`102名为中继岛；`PathGetRogueTeamMapID`仍按原主层和主图节点查地图强度 | 102是隐藏背景/地图显示标识，不能直接把普通floor_num设102后调用下一层，也不能把隐藏图返程当3层结算 |

## 当前源码确定的缺口

本次读取的`rogueteam_service.py`中，`next_floor`未区分sign，仍按主floor_num推进；second_map_json只看见初始空值和读取。旧效果分支把action19解释为商店折扣，把action20解释为移除负面圣物，与上述原生选项/效果组冲突。仅有second_map_info编码字段，不能算隐藏图链已完成。

## 可以延期而不应猜造的细节

- 隐藏图随机布局、节点概率、入口出现权重若没有原生配置证据，可用明确的固定本服布局先实现；应记录并与官方算法还原区分。
- 精确切场动效由现Lua完成；服务端先保证88001/88003/88221与88009的状态和顺序正确，不新增UI协议。
- “云桥”和“中继岛”各结局的额外计分/物品替换不能只靠中文名称推断；先完成数值ID确定的600221→action19→second_map→收下/1330003→action20→sign3返程链。
- 如本轮不实现隐藏图，至少在入口600221尚未执行时明确阻止；不能成功吃掉选项却把玩家推入主图下一层。已保存的隐藏图应保留可恢复，不能用新局或正常通关覆盖。

验收最小范围：一次合法进入、隐藏内实际节点推进、三条收下分支任一合法返程、重复88008不重复推进、隐藏中重登恢复、普通下一层不受影响。布局的官方概率和视觉实测可另轮。
