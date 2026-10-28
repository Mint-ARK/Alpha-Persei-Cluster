# v5.0.2 构建更新（2026-10-28）

更新多存档管理、库内API切换、生日与主动问候、三渠道资源分发、主动下载队列和共享缓存目录；修复229登录Loading及同周切换版本时的战令进度。

Windows启动壳从源码重新编译。推荐便携压缩包，可自由选择CMD或启动壳；Linux使用核心压缩包，包含Web面板，不包含Windows启动壳。

三个包均包含默认account.db，以及完整开荒、跳过序章、70级快速体验三份预设档。已清空API配置与密钥，排除本机存档登记、下载队列、缓存、日志和开发机目录。资源路径采用平台通用默认值。TLS使用既有的独立发行证书，启动时按运行主机处理。

| 文件 | 大小 | SHA256 |
| --- | --- | --- |
| AetherGazer_Server_v5.0.2_Portable.zip | 292.68 MiB | `b1f44ce95a4695ea2e8786fce612372079f6646203955c1faffab24cae42d083` |
| AetherGazer_Server_v5.0.2_Linux_Core.zip | 176.04 MiB | `f8e8fde8df417ad529c5d56f9cc2f86d564ab3d8bbd775e21cd2d9e0d2d4f1be` |
| AetherGazer_Server_v5.0.2_Setup.exe | 199.23 MiB | `f576c248188ec91a1ad4954fa4851a7117107596c812e002f4169df7548aa20c` |

源码提交：`dc3de868ca613734aa47629164bf84a5204d298d`。校验文件为`SHA256SUMS_v5.0.2.txt`。资源功能用法见[资源分发与下载](resource_download_guide.md)，存档与AI用法见[存档与AI说明](save_and_ai_guide.md)。

封包合同17项检查、ZIP CRC/逐文件哈希、四存档完整性与密钥检查、独立包内Python协议/渠道/战令探针和Linux启动脚本语法检查通过。未经完整游戏测试，未做Linux原生启动测试。虚构推演Issue #8补丁未合入。
