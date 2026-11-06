# 虚构推演隐藏中继岛：可直接实现的最小契约

只读Lua、当前源码和配置。未修改Rogue运行文件；本页给出已确定的ID链与最小本服图方案，不将缺失的官方布局或概率伪称还原。

## 确定的入口与配置

| 步骤 | 原生数值依据 |
|---|---|
| 主图际遇 | RogueTeamRoomCfg[8002]唯一params=[60020]；事件60020“守望”含600201云桥、600202中继岛、600203离开。现服务端固定第二层际遇是本服生成规则。 |
| 中继入口 | 选600202进入事件60022；该事件唯一选项600221。600221无condition_list附加锁，params=[1,[6002211]]；effect6002211 action19,params=[1]。 |
| 隐藏内容 | RoomCfg[8003]唯一params=[60030]；8004唯一[60040]；8005唯一[60050]。60030选择600301查看核心或600302调查云团，现事件链分别转60040或60050。 |
| 无战斗出口 | 60040的600402/600403→60042→600421；60050的600502→60052→600521。 |
| 战斗出口 | 60040的600401→60041→600411“进入战斗”；60050的600501→60051→600511“进入战斗”；文案都承诺一场战斗与一个外接程序，胜利后应继续60042/60053的“收下”，不能选项一按就视为胜利。 |
| 三个收下 | 600421/600521/600531效果各有两条：action6给原生1330003维修工具箱，然后action20空params。文案明确“返回岱屿空岛”。 |

入口与收下条件均空，不应额外附加金币/藏品锁。已有事件选项校验仍必须确认当前事件与选项归属，不能凭客户端传600221直接进入。云桥是另一条分支，不要混用1330001/2替换本链原生1330003。

## 地图选择与可复现最小生成

- RogueTeamCfg.map_floor_temp=1，RogueTeamMapFloorCfg[100005]为floor102“中继岛”背景；Tools.GetMapFloorID在PathIsNormalMap=false时仅将显示floor映射102。
- RogueTeamMapCfg只有tier1/2/3/101及col强度系数，没有tier102拓扑。PathGetRogueTeamMapID在隐藏图仍用主层与主图来源节点查强度。不能写floor_num=102，再按正常下一层+1。
- 未找到客户端隐藏图节点数量、连线、随机池或概率表。最小不猜概率的方案：second_map独立放一个type8节点，param直接取RoomCfg8003唯一60030，col=1,row=3、唯一且不撞主图的node_id；60030→60040/60050→收下全部沿事件内链完成。此为明确的固定本服布局；8004/8005不需要再随机抽为额外地图节点。
- 主图完整map_info、原floor_num、来源node_id、来源后继、局内run身份必须保留。second_map_info与主图独立校验前沿；不能在隐藏内选择主图ID或用主图战报完成隐藏节点。

## sign2/3切图的必要状态与时序

原生常量NORMAL=1、ENTER_HIDDEN=2、EXIT_HIDDEN=3。88221下发sign；客户端88008上传同sign→88009，并非客户端任意选择下层。待sign与图身份应持久化，拒过期/不匹配确认，重复不能重建图/重发奖。

1. **准备进入**：600221实际提交后，将入口主图来源node置OVER3、select_node_id=0，保存second_map初始节点和pending_sign2；再推88221(sign2)。不能先把主图当前节点留CLEAN1或保留selected非0，否则Tools.IsClearMapFloor=false，Lua不会启动切场。
2. **确认进入**：88008.sign2只消费已保存的pending2，88001/88003提供两图事实。Lua在pending2期间保持显示主图；88009成功回调清flag再CheckFinishNode，才转未完成second_map。
3. **准备返程**：三个合法收下分支发1330003与隐藏来源OVER3、select0、pending_sign3同一事务；推88221(sign3)。不得再解释action20为清除负面圣物。
4. **确认返程**：88008.sign3消费pending3，保持原主层、主图入口已完成及合法后继；隐藏图完成或清空。Lua在pending3期间保持显示隐藏图，88009回调PathExitSecondMap后回主图，并重播主图最后完成节点的动效。
5. **重登恢复**：88001同时恢复map_info与second_map_info；若仍有pending2/3，88221必须先于最后一次88001/88003的图选择重算。Lua的88221绑定只SetRefreshMapFlag与检查切场，不调用CheckFinishNode；若简单88001后只补88221，当前图索引不会按flag重算。可88221→88001，或88001→88221→88003，不能在Python镜像里替Lua隐式补调用。不能擅自重新完成节点或给奖励。

证据：challengerogueteamaction.lua:133/248-276；challengerogueteampathdata.lua:46-61、117-154；challengerogueteamtools.lua:140-168；mapswitcheffect/challengerogueteammapswitcheffectview.lua:30-61。

## 当前源码缺口与接受边界

读取时旧next_floor未区分sign；旧action19被当折扣、20被当清负面，与原生链冲突。已有second_map_info编码并不证明链已实现。布局概率可延期；合法入口、双图前沿、待切换握手、返程保原主图、真实战斗与奖励、防重及重登不能延期后仍声称此链完成。
