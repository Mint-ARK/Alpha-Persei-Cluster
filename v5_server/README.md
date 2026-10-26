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
