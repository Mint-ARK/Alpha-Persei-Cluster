# 普通材料清理清单

清理仅接受 `category="material"`。下列 ID 有当前扣料代码依据；用途未确认的物品保留。实现前冻结本清单，隔离验证通过后开放按钮。

| ID | 消耗用途 | 当前来源 |
| --- | --- | --- |
| 40101、40102、40103、40104 | 修正者升级经验 | `v5_server/hero_service.py:180` 的 `EXP_ITEMS`，由升级入口校验并扣除 |
| 40201、40202、40203 | 权钥升级经验 | `v5_server/hero_service.py:360` 的 `WEAPON_MAT_EXP`，由 `_weapon_material_plan` 校验 |
| 40301 | 修正者技能升级 | `v5_server/hero_service.py:1021` |
| 40501、40502、40503 | 权钥突破 | `v5_server/weapon_growth_cfg.json` 的 `break_costs`，由 `hero_service.py` 读取并扣料 |
| 40701 | 刻印重构及角色神系重构 | `v5_server/equip_service.py:854`、`v5_server/hero_service.py:1846` |
| 40801、40802、40803 | 刻印升级经验 | `v5_server/equip_service.py:41` 的 `EQUIP_EXP_ITEMS` |
| 41002、41401 | 刻印继承 | `v5_server/equip_service.py:85` 的 `INHERIT_COST_DEFAULT` |
| 41201 | 钥从精炼 | `v5_server/servant_service.py:64`、`:196` |
| 41301、41302、41303 | 刻印跃迁天赋升级 | `v5_server/hero_service.py:463` 的 `TRANSITION_TALENT_COST` |

删除还须同时满足：所选存档与 UID 匹配、当前目录存在该 ID、`classify_item()` 返回 `MATERIAL`。从 `commemorative_items()` 和 `permanent_replay_items` 读取保护清单，保护优先于删除名单。

食材、货币、碎片、刻印、钥从、换装券、礼包、聊天表情、旧档装饰、永久资产和未知 ID 均不在删除范围。`40504` 仅见返还依据，本轮保留。

验收输入须同时包含 41201、全部回放纪念品（含 10009）、1013011、仅材料记录的 90001、礼包及未知 ID。返回删除行数必须等于所选 UID 的实际删除行数；第二次清理返回 0。另验同 UID 的第二存档、错 UID、在线连接、写入失败回滚及重新读取。
