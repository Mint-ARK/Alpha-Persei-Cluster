# A64 角色解锁差量与阵营收集事件

2026-11-03。对照第40/102轮证据、当前Inventory/Core/codec以及客户端ItemAction、HeroData、HeroRaceCollectData修复。当前generator已使用正确的hero表，A57/A58和A63已补角色入库/14007及去重，本票只处理剩余解锁通知。

## 修改

- Inventory仅在角色首次取得或锁定行首次解锁时，将角色ID加入touched_items。17023.normal_items携带{id:角色ID,num:1}，触发原生HERO_UNLOCK_SUCCESS；同包重复角色转碎片，不重复触发解锁。
- 保留单角色14007，完整成长/技能状态只更新一次；严格响应路径遇到必要14007空值与异常同样回滚资产、消耗、领奖记录和通知队列。
- generator不发送未拥有角色的num=0条目。Lua的HERO分支不检查num，即便0也会执行UnlockHeroSuccess；材料和碎片的0余额仍照常发送。
- 移除HERO_UNLOCK订阅中多余的52025领奖记录刷新。原生17023解锁事件经HeroAction/Herodata驱动阵营数量、任务状态、红点和头像更新；服务端HERO_UNLOCK广播及其他订阅保留。52025登录恢复与阵营领奖SQL保持。

### 更正历史证据的解释

52025虽然只携带领史，HeroRaceCollectData.InitData实际仍会调用RefreshHeroRaceCounts、RefreshTasksState和UpdateRedPoint。因此不能声称52025完全不会重算阵营。旧路径在14007之后推送非空52025时可能已更新数量；无领史时空protobuf被跳过，或其他HERO_UNLOCK_SUCCESS订阅仍缺失。14007.ModifyHero只有HERO_DATA_MODIFY，不能替代解锁事件。新路径统一使用原生解锁差量，不再依赖领史帧绕行重算。

## 验证

tools/verify_hero_unlock_delta.py：8专项、10阵营领奖、8实例差量回归，共26通过。真实内存AccountDB/Core/Inventory/codec；旧四个源定义提取运行8专项均失败，保留before_checks与after_checks。

覆盖：从两名同阵营跨三名门槛、正常领奖及52025冷恢复；锁定37级行解锁不重置；重复角色仅30碎片；十连只一次新角色解锁、九份转换；邮件领取与重放；多角色共用一个17023；必要角色帧/资产差量失败回滚及正常重试；未拥有角色不发虚假0解锁。既有26档/7缺档/旧19档领史、实例UID和高精炼/锁定/佩戴不变。

客户端仅按当前Lua的公开角色模板、17023/14007及通知消费者做有限状态投影，未运行Unity或Lua解释器；不将此验证扩大为全部私有/联动角色兼容性结论。旧审计脚本依赖已移除的on_hero_unlock，仅作为历史证据保留，不再作为当前修复测试入口。

## 云端

四个运行文件inventory_service.py、generator.py、operations.py、illustrated_listener.py已同步43.139.154.74:/game-server/aethergazer。覆盖前副本archive/hero_unlock_delta_20261103_235120_8763fa；覆盖前后SHA验证、原子替换及编译通过。

云端独立内存Core探针通过：礼包1075/抽卡1076/邮件1011均仅首次发解锁差量，重复无解锁、三名门槛可领奖、冷领史保留、必要14007为空时回滚。没有写真实存档。PID1827974不变，源码等待统一重启，未经游戏实机。不Git或封包。

证据archive/hero_unlock_delta_20261103包含源码前件、26项结果、旧定义8项对照、部署/探针脚本、哈希及远端回执。台账A64标记source_verified，发布小本本与协作看板同步记录。
