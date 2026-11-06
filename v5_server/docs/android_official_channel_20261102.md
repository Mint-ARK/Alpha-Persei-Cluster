# Android 官服 SDK 渠道统一为7

当前支持范围为勇仕官方 SDK，不增加B站、小米等渠道SDK适配。

## 正确编号

| 平台 | 版本/登录接口的Mix SDK channelId |
| --- | --- |
| PC官服 | 9 |
| Android官服 | 7 |
| iOS官服 | 7 |

Android官服原包assets/ys4fun_mix_environment_prod.properties为YS_MIX_SDK_Channel=7；Manifest内YSMIX_CHANNEL_ID=1属于另一套插件编号。B站同理：Manifest为8、正式Mix SDK为6。官方版本接口android/8目前命中小米安装包。原先“Android=8”的统一映射错误。

只拿到资源清单不代表渠道号正确：官方对无效channelId也可能按platformType返回通用noInstall条目。取证见archive/channel_id_audit_20261102/findings.md及脱敏JSON。

## 修改范围

- 版本探测、清单下载/生成、SDK登录和/config统一使用Android官服7。
- Android和iOS继续保有独立清单、资源目录、选择及连接业务版本。
- 平台识别优先platformType或游戏platform_type，HTTP再结合User-Agent；共享渠道7不再单独解释成iOS。游戏的platform_type枚举0/1/4不变。
- 旧原生/派生清单记录存着Android渠道8时，读取后按平台归一为7；不改写原配置或清单资产。
- Web显示“Android 官服”，检测、生成参数统一为7，渠道常量收束到共享平台控件导出。
- 去掉支付开关误传的未定义mode，恢复前端类型检查。

## 验证

5项官方探测离线检查、21项清单管理夹具检查、13项连接版本/内存数据库检查、8项清单下载接口检查，共47项通过。包含SDK登录及配置回包、Android/iOS同用7时的独立分流、旧渠道8记录读取且原文件不变。前端TypeScript检查及生产构建通过，static_web产物已更新；尚未实机联调。

本轮不修改真实存档、不重启服务、不封包或推送；手机/Termux运行中的旧部署须后续同步源码才能加载。

源码与前端产物写前副本位于archive/android_official_channel_20261102/before。
