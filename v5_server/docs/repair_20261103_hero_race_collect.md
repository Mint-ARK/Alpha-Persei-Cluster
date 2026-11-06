# A151 阵营收集奖励档位补齐

2026-11-03。对照第102轮报告、当前52022注册Operation及客户端HeroRaceCollectData/Action，修复动态角色名册与旧领奖表不一致。

## 修改

- 新增可重复导出工具tools/export_hero_race_collect_cfg.py，根据当前HeroCfg.private=0的公开名册、GameSetting每档数量和奖励生成collect_hero_race_cfg.json。导出覆盖前必须提供备份目录，运行时只读取随包JSON，不依赖开发机Lua。
- 84名公开角色生成26档，补齐尼罗12、圣树12/15、众星12/15、天垣9/12共7档；保留原19档内部ID及每档奖励：移转之辉30、40103×1、货币2×1000。
- 拥有计数与档位共用导出的公开角色映射，只计unlock=1，不再把clear_times>0或私有条目视为拥有。该口径对齐当前Lua的公开HeroData与GetUnlockHeros。
- 请求先归并相同阵营/档位并整单校验，再发奖及写入领史。无效阵营/档位/空请求返回2，尚未达标的新档位返回2005，避免客户端在成功回调中将整包假标已领。
- 保留原已领取集合，重复和已领重放幂等；没有批量修改历史存档或自动补发。旧19档已领账号可以正常领取剩余7档。
- 领奖、保存领史、必要52023和17023响应准备处于同一事务，编码/保存失败完整回滚后可重试。保持现有52023、17023发送次序。

## 验证

tools/verify_hero_race_collect.py共10项真实Core/Inventory/内存SQL/codec检查，修前16个失败断言，修后全部通过。覆盖当前Lua84名/26档/奖励一致性，7个缺档门槛前后，全部26档780辉/40103×26/26000货币2，旧19档只新增7份，重复/重放、非法混合整单、锁定/私有不计数、空请求、保存及必要响应失败回滚、52025冷恢复。

所有数据库写入为内存夹具，未经游戏实机。未运行旧审计脚本的“缺档应复现”断言充当修复验收，也未修改客户端Lua。

## 云端同步与记录

operations.py、illustrated_listener.py、collect_hero_race_cfg.json已同步43.139.154.74:/game-server/aethergazer。源码覆盖前副本archive/hero_race_collect_20261103_121640_b6f690。云端独立内存Core探针确认84名/26档、旧19档保留只领7份、非法整单阻断、回执失败回滚、重试幂等及冷恢复26档，全部通过。

服务PID1693172保持不变，未重启，等待统一加载；未备份或修改真实存档。未Git或封包。发布小本本及台账已更新A151为source_verified。

证据archive/hero_race_collect_20261103包含源码前件、before_checks.txt、final_checks.txt、events.json、remote_result.json及部署/探针脚本。
