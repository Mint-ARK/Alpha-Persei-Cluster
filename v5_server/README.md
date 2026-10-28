# v5_server — V5 模块化服务器（自包含目录）

> 2026-08-22 由 `analysis_scripts/core/lib`（六件套）+ `临上线基准数据/Server/v5`（入口）
> + `临上线基准数据/响应体格式`（skeleton/模板/操作表）合并迁移而来。
> 本目录是 V5 唯一的工作副本，旧位置的 `.py` 均为死副本（已移入 Trash）。

## 目录内容

- **入口**：`main.py`（装配）、`server_net.py`（TCP/HTTPS 网络层）、`login.py`（登录洪流，读 DB login_push 表）、`replay.py`（素材重放，--replay 激活）
- **CORE 六件套**：`core.py`（调度）`middleware.py`（骨架/模板链）`operations.py`（135+ 操作类）`generator.py`（响应生成）`account_db.py`（数据层）`codec.py`（protobuf 编解码）
- **支撑**：`transport.py`（组帧）`hero_codec.py` `schema_default.py` `logger.py` `protocol_ref.py`
- **数据**：`account.db`（权威数据库）、`skeleton.json`(920)、`total_response_templates.json`(835)、`operation_cfg.json`（遗留配置版，7 个独有 cmd 兜底）、各类运行时 json/bin、SDK 证书、`mail_archive/`
- **日志**：`logs/`（middleware_YYYYMMDD.log 按天滚动）

## 启动

```
python main.py                                    # 极简一键启动（全套原生：HTTPS 443/80 + Gateway 8102 + Game 8105 + UDP 6105）
python main.py --battle-port 0                    # 禁用 UDP 战斗服
python main.py --self-check                       # 自检
python main.py --replay <素材目录>                 # 遗留素材重放模式（可选备用调试）
```

默认端口：HTTPS 443（需管理员，兼 HTTP:80 拍照直传）、gateway 8102、game 8105、**UDP 战斗服 6105**。
内置原生 Token 快速登录（秒进游戏无等待）与免验证账密直通。

## 资源清单与渠道选择

Web“资源分发”页分别管理PC（channelId=9）、Android（8）、iOS（7）的清单方案和原生保险方案。总控预设只填写各渠道选项，点击“应用渠道选择”才保存；“生成副本”只入清单库，不自动应用。生成器可填写安装包版本、资源版本和中文名称，并预览最终文件名与MD5。只有已登记且校验通过的原生清单能作为BASE；派生副本只能引用原生BASE，支持移入回收区和恢复。

```powershell
python main.py --list-resource-profiles
python main.py --res-version auto --game-version 321
python main.py --resource-profile "ios=我的229兼容方案" --resource-profile "pc=auto"
```

`--resource-profile`接受列表中的方案名称或短ID，可重复指定。启动参数只作为本轮进程的渠道选择，Web应用该渠道设置后接管；重启仍带相同参数时会再次按参数选择。旧`--res-version 229/311/321`保留兼容，填写对应渠道的原生版本（缺少时退回其最新原生清单），并在没有显式`--game-version`时沿用原业务版本选择逻辑。

默认读取已保存的渠道选择。自动模式只在Windows尝试匹配本机已登记的PC原生清单，其他渠道和Linux按各自最新完整原生来源选择；本机探测不改写游戏业务版本。语音保留BASE原始字节，不提供手改语音版本入口。使用方法和验收记录见[清单管理说明](docs/manifest_manager_20261028.md)。

## 主动资源下载

Web“资源分发 → 主动下载管理”按渠道和清单建立全局任务。可以选择“必须”（非延迟分组）、“扩展”（清单声明的延迟分组）或“全部”，先分析缓存和预计体积，再确认下载；也可以直接加入下载队列。独立语言包稍后接入。官方检测弹窗只带参数跳转，不再直接下载。

文件并发默认32路，可调1–128；并行任务数独立设置1–8，1即按队列顺序串行执行任务。多个任务共享对象只下载一次。客户端实际回源时优先，主动任务暂停并在恢复前更新索引；本地缓存只读分发不受阻塞。取消任务回收残片，完整缓存保留；服务重启后未完成任务暂停，手动继续按文件恢复。

```powershell
# 默认把任务交给运行中的本机服务端；使用当前渠道分发方案
python tools/download_assets.py --platform pc --scope all
python tools/download_assets.py --platform ios --profile native-ios-列表中的ID --scope extended --analyze
# 没有服务端时单独运行，仍使用同一全局队列目录和跨进程门控
python tools/download_assets.py --standalone --platform android --manifest assethash_完整文件名.bytes --scope all
```

旧PC/iOS下载脚本的命令入口已转交统一CLI；`main.py --sync-update`转交运行中的服务端队列。队列状态位于`runtime/cdn_downloads/`，不属于玩家存档，也不随发行包发布。详见[主动工具链说明](docs/active_download_chain_20261028.md)。

准备官方主清单只保存下载蓝图，不自动切换分发或登记完整原生BASE；独立语音包入口后续接入。

资源分发页右上角“资源存储位置”可浏览服务器目录并选择共享根目录，自动分为pc/android/ios三个平台。Windows默认优先D盘，其次其他非系统盘；Linux默认程序目录下cdn_cache，也可选择挂载目录。与启动壳共用server_config.json的storage配置；切换后旧缓存继续读取，不自动搬迁。传输和索引空闲时可直接保存生效。详见[存储位置说明](docs/resource_storage_20261028.md)。

## AI配置与旧档升级

AI接口、模型和密钥统一保存在当前存档数据库中。在Web控制面板选择存档，进入“AI修正者 → API配置管理”，保存配置后从下方选择生效API；角色可以单独绑定或跟随本档默认配置。

程序不再读取或写入`.env`、全局`ai_bot_config.json`或其他应用的AI密钥。未配置的存档保持未配置，新建存档不继承其他存档的凭据。

旧档可使用`python tools/upgrade_legacy_saves.py --db account.db --db account_test.db`预览升级。明确传入`--legacy-config`和`--legacy-env`才能一次性导入旧配置；添加`--apply`后先在项目根`db_backup/`备份，再补齐缺表、迁入配置，保留既有业务数据。实际升级前应先停止正在使用这些库的服务进程。含密钥的存档和备份不得随代码发布。

## 战斗系统（2026-08-22 V5 动态化重写）

- `battle_server.py`：UDP/KCP 战斗服（XServer 协议，pcapng 对齐：9B 报文头/20B 无 conv 段/
  sn 序号对齐/分片重组/Pong 裸二进制收敛差值/134+135 重传）。V4.1 的 frame_loop addr
  缺陷已修，魔法数字全部参数化，会话表线程安全。132 结算结果登记在服内，
  由 h_54032 被动结算（客户端战毕必发 cs_54032，无需 UDP 线程推 TCP）。
- `middleware.py` 战斗段：battle_id 会话注册（54030/54034）→ sc_54007 动态下发 ip/port →
  sc_54003 按出战英雄重写（hero_list[].hero_base_info.id）→ h_54032 结算引擎
  （通用掉落 + 进度推进 + 24009/15009/17009 联动推送）。
- **通用掉落引擎**：`drop_cfg.json`（dropcfg.lua 1274 库：base+权重抽取）+
  `daily_stage_drop_cfg.json`（2114 关卡→drop_lib 映射）+ `chapter_reward_cfg.json`
  （chaptercfg 2025 关卡首通/重复奖励）+ stage_sub 库表兜底——替代 V4.1 硬编码
  gain={32194,1}。多倍卡按 battle_times 逐倍独立抽取，材料本掉落带随机性。

## 外部依赖（仓库内相对路径引用，勿移动）

- `analysis_scripts/解码尝试/decode_schema.py` — codec 的 schema 引擎（共享分析工具，未迁移）
- `analysis_scripts/协议分析/` — hero_items_84.json、lua_proto_messages.json
- `analysis_scripts/archive/` — 重放素材（--replay）
- `decompiled_v2/x64/` — 客户端客户端配置 Lua（collectpicturecfg.lua 等配置读取）

## 已知约定

- 修改 operations/generator/account_db 只改本目录，旧位置副本不生效。
- 数据库以本目录 `account.db` 为准。
- operation_cfg.json 已于 2026-08-22 退役（改名 `operation_cfg.retired.json` 仅存档）：
  原有 7 个独有 cmd（13024/17028/17030/28016/43004/58052/60054）已全部移植为
  operations.py 末尾的操作类，配置驱动机制（core.ConfigOperation）不再有注册来源。

## AI 会话历史与内部 Prompt

- `ai_history_manager.py` 只读客户端 `chat/<uid>/friend/<char_id>.txt`，以客户端 JSONL
  作为用户可见会话的首选事实源，并用 `ai_chat_memory` 补齐面板消息或离线缺口。
- 可通过环境变量 `AETHERGAZER_CHAT_ROOT` 指定 `chat` 根目录；未设置时使用当前 Windows
  用户的 `AppData/LocalLow/yongshi/AetherGazer/chat`。
- 客户端目录严禁由服务端写入。第一版不会同步、清空或修复客户端文件。
- `ai_context_builder.py` 从 `data/ai_prompt_fragments.json` 选择动态亲和片段；最终 System
  Prompt 仅在一次 LLM 请求的内存中存在，历史接口和两套持久化记录只允许
  `user` / `assistant` 文本消息。
