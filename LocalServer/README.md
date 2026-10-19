# Local Server — 本地单机服务桌面宿主运行时

基于 **Windows App SDK (WinUI 3) + .NET 10** 打造的现代化桌面控制台与进程看门狗宿主程序。

## 核心职责

1. **进程看门狗 (Server Supervisor)**:
   - 托管并守护底层 Python 服务端 (`v5_server`);
   - 基于 Windows 内核级 **Job Object (作业对象)** 实现生命周期托管与优雅脱钩，支持后台独立运行或退出时彻底回收，杜绝端口泄露与孤儿进程；
   - 异步重定向输出/报错管道，实时捕获流式日志。

2. **现代化前端宿主 (Mica & WebView2)**:
   - 采用 Windows 11 原生 **Mica (云母材质)** 设计语言；
   - 原生内嵌官方 **WebView2 容器**，直通运维管理控制台 (`http://127.0.0.1/web/gm_console.html`)；
   - 内置智能 HTTP 探针，自适应等待服务端监听就绪。

3. **全域运维看板**:
   - **仪表盘**: 网络拓扑与端口状态实时监控，一键初始化与端口冲突回收；
   - **管理控制台**: 沉浸式 Web 运维控制面板；
   - **运行日志**: 高性能节流深色终端，实时查看报文交互与服务流水；
   - **环境配置**: 客户端路径识别、CDN 缓存挂载、HOSTS 重定向注入及生命周期策略。

## 源码与开发

- **开发环境**: Visual Studio / .NET 10.0 SDK
- **构建命令**:
  ```powershell
  dotnet build LocalServer.csproj
  ```
- **可执行文件**:
  `bin\Debug\net10.0-windows10.0.26100.0\win-x64\LocalServer.exe`
