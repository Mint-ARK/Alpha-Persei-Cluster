# 控制面板单密码后端接口与启用契约

2026-11-05。后端代码已实现，前端登录页、Linux引导及真实配置均未修改。当前不会自动启用，不涉及游戏SDK账号；后续由老大与Gemini设计入口并明确启用。

## 启用与旧模式

新模式以`AG_ADMIN_PASSWORD_FILE`环境变量指向独立JSON文件启用。未设置该变量时保持现状；变量存在但为空、文件缺失或格式错误时拒绝启动，不回退免认证。旧`AG_ADMIN_AUTH_FILE`仍是原来的用户名+Basic模式；两个变量同时存在直接报错，新模式完全不接受Basic凭据，不能用旧路径绕过会话或限流。

JSON契约如下；下列摘要是占位，不能直接启用：

```json
{
  "schema": 1,
  "password_hash": "由 admin_session_auth.hash_password 生成的摘要",
  "origin": "https://panel.example.com",
  "session_seconds": 3600,
  "trusted_proxies": []
}
```

`origin`必须是浏览器实际访问的单一HTTPS源，含必要非默认端口，不含路径、尾斜线、用户名或通配符。`session_seconds`可取300—86400秒，默认1小时。密码使用带随机盐的600000轮PBKDF2-SHA256，恒定时间比较摘要；新建/改密至少12字符、最多1024个UTF-8字节。提供Python函数`admin_session_auth.hash_password(password)`给后续引导复用；密码应通过`getpass`等隐藏输入获取，不放命令行参数、URL或日志。

认证文件必须在`static_web`以外，建议放独立私有配置目录。POSIX要求仅所有者可访问（0600），Windows由部署者限制文件及父目录ACL。本轮未建立任何真实认证文件。启用后的文件损坏、权限变宽或被移走将令管理请求返回503，保护保持开启；有效替换会撤销全部已有会话。现有静态文件解析已限制在静态根目录，认证加载器另拒绝静态目录内的文件。

## 接口

认证响应均为JSON，失败使用实际HTTP错误状态与`error`短码；所有认证/管理响应`Cache-Control: no-store`。

| 方法和路径 | 输入与要求 | 成功输出 |
| --- | --- | --- |
| `POST /api/gm/auth/login` | `Content-Type: application/json`；`Origin`等于配置值；正文仅`{"password":"..."}` | `csrf_token`、`expires_at`，并设置会话cookie |
| `GET /api/gm/auth/session` | 浏览器携带cookie；无需CSRF头 | `enabled/authenticated`；已登录时另含`csrf_token/expires_at` |
| `POST /api/gm/auth/logout` | 有效cookie、`Origin`、`X-Admin-CSRF` | 撤销本会话并清cookie |
| `POST /api/gm/auth/password` | 有效cookie、`Origin`、`X-Admin-CSRF`；正文仅`current_password/new_password` | 先备份、原子替换摘要，撤销所有会话，返回`reauthenticate:true` |

cookie名`__Host-v5_admin`，256位安全随机值，`Secure; HttpOnly; SameSite=Strict; Path=/`，无Domain；服务端仅保留其SHA256索引及独立随机CSRF值。绝对有效期不随轮询延长。登录重新签发会话，旧会话随之撤销；最多64个活跃会话，超出时撤销最旧会话。进程重启后必须重新登录。

前端先GET session；未登录则展示单密码输入，登录成功后把CSRF值保存在当前页面内存。所有管理API都应携带cookie和`X-Admin-CSRF`，包括GET：旧`toggle_capture`、`stage_preset`等GET存在写副作用，不能省略防护。POST/PUT/DELETE还要求正确Origin。401重新展示登录；403检查来源或CSRF；429按Retry-After等待；503显示配置不可用。不要自动重试不确定是否成功的改密/资产操作。

认证路径禁止查询参数，未知认证子路径直接拒绝，不进入SDK的请求正文诊断。未启用时session只报告`enabled:false`，其余新认证接口返回404；不提供免登录的首次设密码、关闭认证或读摘要API。

## 统一守卫与反代

守卫在Flask应用级before_request执行，早于GM读库/写库。保护整个`/api/gm`、GM蓝图及GM函数别名、旧`/api/system/res_version`和管理页面入口；独立`tools/run_gm_server.py`亦安装同一守卫。SDK、游戏协议、CDN和普通游戏静态资源保持原合同。旧启动器资源版本别名启用后也需要认证，不因兼容性放开旁路。

启用后只接受配置HTTPS源；直接HTTP管理请求拒绝。跨源响应不会沿用原GM的`Access-Control-Allow-Origin:*`，只允许匹配源、cookie和明确头；OPTIONS无凭据但无业务数据/副作用。应用级after_request在蓝图after_request之后清理旧CORS，拒绝、异常及OPTIONS同样覆盖。

默认不信任任何转发头。反代必须保留真实外部`Host`，设置`X-Forwarded-Proto`，并追加真实`X-Forwarded-For`；只有`trusted_proxies`显式CIDR内的直连来源才可提供这些信息。建议只写实际代理的单个IP（例如127.0.0.1/32或::1/128），不要填客户端网段；全地址0前缀明确拒绝。XFF从右向左剥离已知代理，以最近未信任来源计数，攻击者自带左侧前缀不作为身份。任意X-Forwarded-Host均不使用。网络部署还应确保客户端无法直接访问受信代理的内部入口；本轮没有更改Nginx或防火墙。

本机开发可用HTTPS loopback源及测试证书；Flask test_client可直接设`base_url=https://...`做无监听隔离测试。不为开发加入HTTP明文认证开关。正式前端页面必须在同一HTTPS源提供，当前后端未代做登录页或改前端构建。

## 次数保护与存储边界

每IP每60秒最多5次口令验证，全局每60秒最多30次，同时最多2次PBKDF2验证；预算在验证前加锁预占，改密验证也使用同一预算。超限返回429及Retry-After，拒绝请求不会无限续封；窗口过后可重新尝试。IP表有1024上限并回收过期项，全局预算也限制地址轮换。代理配置正确时，各真实客户端独立计数；不将全部用户无条件归为代理IP。

同一进程按认证文件实际路径共享会话、撤销与限流；当前HTTP/HTTPS本来共用一个app，多app亦共享。此实现供当前单进程多线程内核使用，不支持多个独立worker/副本共享会话或限流，后续若引入多进程需先更换共享状态后端。它不是跨网络的DDoS防护。

改密会在认证文件同级保留`.bak.<随机值>`副本，写私有`.new.<随机值>`临时文件后用os.replace原子替换；失败时不物理清理备份/临时文件。备份只含旧摘要而非明文，仍应按秘密限制权限。未提供删除或后台回收功能，后续清理须按项目回收站规则。

## 本轮验证

`tools/test_admin_password.py`17项隔离测试；其中真实`build_https_app`逐项检查111个管理路由/方法，未认证全部在读库前拒绝。另测登录/注销/绝对到期/改密、防Basic旁路、CSRF与GET写入口、真实蓝图CORS和OPTIONS、来源伪造、并发预占、窗口恢复、多app共享、配置损坏、改密文件写失败与跨app撤销。`tools/test_admin_access.py`旧Basic6项回归。全过程无网络监听、无真实账号/数据库/密钥配置写入；未做浏览器或线上反代实测。

证据与覆盖前副本：`archive/admin_password_20261105/`。本文件用于Gemini后续前端/引导对接，不表示当前环境已启用或已部署认证。
