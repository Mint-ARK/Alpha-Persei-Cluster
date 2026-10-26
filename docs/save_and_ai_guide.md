# 新存档、选档与AI问候

## 创建和选择存档

游戏选区中的“新建存档”提供完整开荒、跳过序章、70级快速体验三个预设，名称从“新存档_01”递增。各存档使用独立UID与数据库，选择区服进入对应存档。删除入口保留确认模式与备份。

也可以在v5_server目录使用命令行：

```powershell
python -B -X utf8 save_generator.py --name "新的旅程"
python -B -X utf8 save_generator.py --name "进阶开局" --level 70 --skip-intro --skip-feature-guides --clear-through 5 --noob-tasks-ready
```

支持--uid、--nick、--preview和--no-support-mail。等级范围1至100；保留501剧情时在游戏内取名。预通关只补固定首通奖励，默认附带18封助力邮件。生成器输出位置和完整参数可用--help查看，启动不同库使用main.py --db参数。

Windows可使用CMD启动脚本或启动壳；CMD、PowerShell和Linux/macOS终端的命令格式分别处理，支持中文名称和含空格的路径。下载分发包时推荐便携式压缩包，方便选择CMD或启动壳。

## AI主动问候与聊天历史

配置角色人格和模型凭据后，all策略参加每天首次登录问候；birthday仅生日，holiday仅节日/节气，none关闭。当天生日和节气可合入同一条私聊。模型请求携带当前时间、时区、星期与玩家上下文。

玩家和角色聊天由客户端本地JSONL保存，登录初始化会恢复会话和历史。模型上下文优先读取客户端记录，并由当前存档数据库补缺去重。生日礼赠通过官方礼包邮件和自定义角色贺信发放，错过生日可按最近一次生日补发。

API密钥由使用者自行配置；源码不包含测试密钥或个人存档。
