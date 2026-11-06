# CDN默认关闭与平台分发检查（2026-10-30）

已修改main.py和cdn_proxy.py：命令行及模块初始捕获/自动回源默认false；未启用时不选择CDN出站地址，启动清单准备按实际开关判断。显式--capture-cdn出站异常时关闭捕获并继续启动本地业务，--no-capture-cdn仍优先。显式Web/主动工具联网入口保持原语义。

平台链路：server_net.py更新接口按platformType、channelId（PC9/Android8/iOS7）、UA兜底，明确参数矛盾拒绝；GET/form/JSON均支持。调用distribution_entries(platform, appVersion)，从本渠道配置选方案。appVersion为安装build，不代表资源build；未提供时返回候选清单，无法可靠推断远程当前资源。CDN资源按URL /pc/ /android/ /ios/ 优先、UA其次、无信息默认PC；profiles路径带具体方案ID，对平台及语音BASE映射检查。游戏10042登录再以platform_type/resource_vs绑定连接业务基线。127来源与远近不参与上述平台选择，属于网关连接地址等其他逻辑。

主机Windows+auto时可扫描本机PC资产作为pc_hint；Linux无此扫描，但仍正常按客户端HTTP字段和平台路径分发。云端多平台与Termux单机均可使用这套规则，无需按Linux另建固定平台。

待讨论边界：缺平台路径且UA无信息会默认为PC；SDK/config与通用http_platform优先顺序和版本接口不完全一致（/config不读channelId且主要看query/UA）；appVersion未命中可能返回空候选；安装条目PC/Android仍硬编码307、iOS308，与动态资源条目独立。源地址完整fallback及gen_cert虚构192.168.0.105尚未改动，本轮先隔离默认启动依赖。

17项AST隔离检查通过：默认关闭且不调用出站选择、显式开启、关闭优先、出站失败降级；同一127来源下三平台GET/form/JSON正确分流、渠道矛盾拒绝、三平台资源路径识别。真实分发配置/数据库/网络/服务均未操作；未重启/封包/上传。证据archive/cdn_default_off_20261030/probe_result.json；源码与说明备份同目录。
