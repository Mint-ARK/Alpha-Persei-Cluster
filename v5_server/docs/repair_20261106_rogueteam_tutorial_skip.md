# 新存档跳过虚构推演101教学（2026-11-06）

用户决定暂不复刻101教学，并在新存档生成处跳过。只跳过虚构推演教学，不改变501剧情、取名流程、其他功能首次引导或玩法开放条件。

## 实现

- 统一生成器`save_generator._seed_player`保存`save.rogueteam_tutorial=skip`。完整开荒、跳过序章、快速体验以及自定义方案均走此入口；旧的两个构建脚本、CLI、SDK选档菜单及Web生成接口均复用它，不各自复制规则。
- `rogueteam_login.prepare_login_frames`只对具有该标记且`save.owner_uid`匹配的存档，替换所有旧抓包88029，并在10201之前生成唯一的`sc_88029 {template_id:100001,finish_guide:1}`；缺少抓包也补齐。
- 这是单纯的教学完成布尔量。原生`ChallengeRogueTeamAction`的Bind88029调用`UpdateGuideState`，后者只写`isFinishGuide_`。即使玩法尚未开放，也不需要提前发送玩法进度、授予访问权限或预通关地图。
- 该策略独立于`skip_feature_guides`和`skip_intro`，不把地图tier101误当成账号guide_id写入通用guide表，不新增DDL、奖励或清关历史。
- 没有标记的旧档和不匹配的UID继续原行为；没有修改任何已有真实存档。101真实教学流程暂缓，不再作为本阶段必须实现的功能。

## 验证

```powershell
$env:ACCOUNT_DB_PATH=':memory:'
python -B -X utf8 v5_server/tools/verify_rogueteam_tutorial_skip.py
$env:PYTHONPATH="$PWD/v5_server;$PWD/v5_server/tools"
python -B -X utf8 -m unittest verify_save_generator.SaveGeneratorTests verify_rogueteam_login.RogueLoginTests
```

5项专项通过：三个预设真实内存生成、501保留/跳过各自不变、玩法锁定时仅发教学布尔量、重复抓包替换、缺帧补齐、旧档/外UID隔离、编码失败不修改状态。此处多个子场景归属5个测试，不重复累加。

27项生成器/原生登录关联回归中25项通过、2项旧断言失败；用改前`_seed_player`函数在测试进程内替换重跑，两项同样失败：冷owner绑定测试的解码UID为None（期望261023235950），邮件附件旧期望412而当前410。未更改这些夹具或扩大到其他模块；不宣称整套旧回归全通过。基线脚本`archive/rogueteam_tutorial_skip_20261106/check_legacy_baseline.py`及原始日志保留。

全部数据库为内存新档，母库仅以mode=ro读取。备份和证据在`archive/rogueteam_tutorial_skip_20261106/`。未经游戏实测，仅本地；无真实库修改、服务操作、云同步、Git或封包。
