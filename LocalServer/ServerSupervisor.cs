using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace LocalServer;

public sealed class ServerSupervisor : IDisposable
{
    private static readonly Lazy<ServerSupervisor> _instance = new(() => new ServerSupervisor());
    public static ServerSupervisor Instance => _instance.Value;

    private Process? _serverProcess;
    private JobObject? _jobObject;
    private readonly object _lock = new();

    public bool IsRunning => _serverProcess != null && !_serverProcess.HasExited;
    public int? CurrentPid => IsRunning ? _serverProcess?.Id : null;
    public bool IsAttached { get; private set; }

    public string? LastResolvedServerDir { get; private set; }
    public string? CustomServerDir { get; set; }

    public event Action<string>? LogReceived;
    public event Action<bool>? StateChanged;

    public static readonly int[] DefaultServerPorts = [8102, 8105, 443, 80, 6105];

    private ServerSupervisor()
    {
        try
        {
            // 构造 JobObject，killOnClose 为 false，由上层生命周期策略按需管理
            _jobObject = new JobObject(killOnClose: false);
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[JobObject Init Warning] {ex.Message}");
        }
    }

    public string FindPythonPath()
    {
        // 1. 优先查找应用目录下的独立嵌入式运行时 (便携/安装包优先)
        string appBase = AppDomain.CurrentDomain.BaseDirectory;
        string[] bundledCandidates = [
            Path.Combine(appBase, "runtime", "python", "python.exe"),
            Path.Combine(appBase, "runtime", "python.exe"),
            Path.Combine(appBase, "python", "python.exe"),
            Path.Combine(appBase, "python_embed", "python.exe")
        ];

        foreach (var candidate in bundledCandidates)
        {
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        // 2. 查找环境变量中的 python
        string? envPath = Environment.GetEnvironmentVariable("PATH");
        if (!string.IsNullOrEmpty(envPath))
        {
            foreach (string p in envPath.Split(Path.PathSeparator))
            {
                string candidate = Path.Combine(p.Trim(), "python.exe");
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
        }

        return "python.exe";
    }

    public string? FindServerDir()
    {
        // 1. 若外部显式指定了路径
        if (!string.IsNullOrWhiteSpace(CustomServerDir) && Directory.Exists(CustomServerDir) && File.Exists(Path.Combine(CustomServerDir, "main.py")))
        {
            LastResolvedServerDir = Path.GetFullPath(CustomServerDir);
            return LastResolvedServerDir;
        }

        string current = AppDomain.CurrentDomain.BaseDirectory;

        // 2. 优先检查本地同级发布目录（打包发布安装的标准形态）
        string[] localCandidates = [
            Path.Combine(current, "server"),
            Path.Combine(current, "v5_server"),
            Path.GetFullPath(Path.Combine(current, @"..\server")),
            Path.GetFullPath(Path.Combine(current, @"..\v5_server"))
        ];

        foreach (var c in localCandidates)
        {
            if (Directory.Exists(c) && File.Exists(Path.Combine(c, "main.py")))
            {
                LastResolvedServerDir = Path.GetFullPath(c);
                return LastResolvedServerDir;
            }
        }

        // 3. 向上逐级回溯寻找源码工作区（开发与调试形态）
        DirectoryInfo? dir = new DirectoryInfo(current);
        for (int i = 0; i < 7 && dir != null; i++)
        {
            string probeV5 = Path.Combine(dir.FullName, "lua解析", "v5_server");
            if (Directory.Exists(probeV5) && File.Exists(Path.Combine(probeV5, "main.py")))
            {
                LastResolvedServerDir = Path.GetFullPath(probeV5);
                return LastResolvedServerDir;
            }

            string probeServer = Path.Combine(dir.FullName, "server");
            if (Directory.Exists(probeServer) && File.Exists(Path.Combine(probeServer, "main.py")))
            {
                LastResolvedServerDir = Path.GetFullPath(probeServer);
                return LastResolvedServerDir;
            }

            string probeAlpha = Path.Combine(dir.FullName, "AlphaPerseiCluster", "server");
            if (Directory.Exists(probeAlpha) && File.Exists(Path.Combine(probeAlpha, "main.py")))
            {
                LastResolvedServerDir = Path.GetFullPath(probeAlpha);
                return LastResolvedServerDir;
            }

            dir = dir.Parent;
        }

        // 4. 环境变量指定的自定义路径兜底
        string? envServer = Environment.GetEnvironmentVariable("AETHERGAZER_SERVER_DIR");
        if (!string.IsNullOrWhiteSpace(envServer) && Directory.Exists(envServer) && File.Exists(Path.Combine(envServer, "main.py")))
        {
            LastResolvedServerDir = Path.GetFullPath(envServer);
            return LastResolvedServerDir;
        }

        return null;
    }

    public string? FindV5ServerDir() => FindServerDir();

    /// <summary>
    /// 尝试发现并接管已在后台运行中的服务进程
    /// </summary>
    public bool TryAttachExistingServer()
    {
        lock (_lock)
        {
            if (IsRunning) return true;

            int targetPid = -1;
            string? savedDir = null;

            // 1. 尝试从上次保存的运行状态读取 PID
            try
            {
                var (sPid, sDir) = AppConfigManager.LoadServerState();
                savedDir = sDir;
                if (sPid.HasValue && sPid.Value > 0)
                {
                    var p = Process.GetProcessById(sPid.Value);
                    if (!p.HasExited && p.ProcessName.Contains("python", StringComparison.OrdinalIgnoreCase))
                    {
                        targetPid = sPid.Value;
                    }
                }
            }
            catch { }

            // 2. 若未命中状态文件，扫描服务核心端口 (8102 网关 / 8105 核心) 上的存活 Python 进程
            if (targetPid <= 0)
            {
                var pids = FindPidsOnPorts([8102, 8105]);
                int currentPid = Process.GetCurrentProcess().Id;
                foreach (int pid in pids)
                {
                    if (pid <= 4 || pid == currentPid) continue;
                    try
                    {
                        var p = Process.GetProcessById(pid);
                        if (!p.HasExited && p.ProcessName.Contains("python", StringComparison.OrdinalIgnoreCase))
                        {
                            targetPid = pid;
                            break;
                        }
                    }
                    catch { }
                }
            }

            if (targetPid > 0)
            {
                try
                {
                    var proc = Process.GetProcessById(targetPid);
                    _serverProcess = proc;
                    IsAttached = true;
                    if (!string.IsNullOrEmpty(savedDir) && Directory.Exists(savedDir))
                    {
                        LastResolvedServerDir = savedDir;
                    }
                    else
                    {
                        FindServerDir();
                    }

                    proc.EnableRaisingEvents = true;
                    proc.Exited += (_, _) =>
                    {
                        LogReceived?.Invoke($"[ServerSupervisor] 接管的后台服务主进程 (PID: {targetPid}) 已退出。");
                        AppConfigManager.ClearServerState();
                        IsAttached = false;
                        _serverProcess = null;
                        StateChanged?.Invoke(false);
                    };

                    AppConfigManager.SaveServerState(targetPid, LastResolvedServerDir ?? "");
                    LogReceived?.Invoke($"[ServerSupervisor] 成功自动接管后台运行中的 Python 服务端 (PID: {targetPid})。");
                    StateChanged?.Invoke(true);
                    return true;
                }
                catch (Exception ex)
                {
                    Debug.WriteLine($"[TryAttachExistingServer Error] {ex.Message}");
                }
            }

            return false;
        }
    }

    /// <summary>
    /// 解除对当前服务进程的界面托管，使其在后台独立继续运行
    /// </summary>
    public void DetachServer()
    {
        lock (_lock)
        {
            if (_serverProcess != null && !_serverProcess.HasExited)
            {
                int pid = _serverProcess.Id;
                AppConfigManager.SaveServerState(pid, LastResolvedServerDir ?? "");
                LogReceived?.Invoke($"[ServerSupervisor] 用户选择在后台保留服务，正在解除与进程 (PID: {pid}) 的界面托管...");

                try
                {
                    _serverProcess.EnableRaisingEvents = false;
                }
                catch { }

                _serverProcess = null;
                IsAttached = false;
            }
        }
    }

    /// <summary>
    /// 精准检测并强杀占用指定端口的残留孤儿/守护进程（彻底治愈端口占用死锁）
    /// </summary>
    public int KillPortHolders(IEnumerable<int>? ports = null)
    {
        ports ??= DefaultServerPorts;
        var portSet = new HashSet<int>(ports);
        var pids = FindPidsOnPorts(portSet);

        int killedCount = 0;
        int currentPid = Process.GetCurrentProcess().Id;

        foreach (int pid in pids)
        {
            if (pid <= 4 || pid == currentPid) continue;

            try
            {
                var proc = Process.GetProcessById(pid);
                string procName = proc.ProcessName;
                LogReceived?.Invoke($"[PortManager] 正在终止占用服务端口的进程: {procName} (PID: {pid})...");
                proc.Kill(entireProcessTree: true);
                proc.WaitForExit(1500);
                killedCount++;
                LogReceived?.Invoke($"[PortManager] 进程 (PID: {pid}) 已终止，端口占用已释放。");
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[KillPortHolders] PID {pid} 清理跳过: {ex.Message}");
            }
        }

        return killedCount;
    }

    public static List<int> FindPidsOnPorts(HashSet<int> ports)
    {
        var pids = new HashSet<int>();
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "netstat",
                Arguments = "-ano",
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using var proc = Process.Start(psi);
            if (proc != null)
            {
                string output = proc.StandardOutput.ReadToEnd();
                proc.WaitForExit(2000);
                var lines = output.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                foreach (var line in lines)
                {
                    var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                    // TCP / UDP 格式: Protocol LocalAddr ForeignAddr [State] PID
                    if (parts.Length >= 4 && (parts[0].Equals("TCP", StringComparison.OrdinalIgnoreCase) || parts[0].Equals("UDP", StringComparison.OrdinalIgnoreCase)))
                    {
                        string localAddr = parts[1];
                        int colonIndex = localAddr.LastIndexOf(':');
                        if (colonIndex >= 0 && int.TryParse(localAddr[(colonIndex + 1)..], out int port))
                        {
                            if (ports.Contains(port))
                            {
                                string pidStr = parts[^1];
                                if (int.TryParse(pidStr, out int pid) && pid > 0)
                                {
                                    pids.Add(pid);
                                }
                            }
                        }
                    }
                }
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[FindPidsOnPorts Exception] {ex.Message}");
        }
        return pids.ToList();
    }

    public Task<bool> StartServerAsync(string? extraArgs = null)
    {
        lock (_lock)
        {
            if (IsRunning)
            {
                return Task.FromResult(true);
            }

            // 启动前先执行一次端口卫士守护，防止老残留进程霸占端口引发死锁
            try
            {
                KillPortHolders();
            }
            catch { }

            string pythonExe = FindPythonPath();
            string? srvDir = FindServerDir();

            if (string.IsNullOrEmpty(srvDir))
            {
                LogReceived?.Invoke("[ServerSupervisor ERROR] 未能定位到服务核心目录 (main.py)，请核实安装路径！");
                return Task.FromResult(false);
            }

            string mainPy = Path.Combine(srvDir, "main.py");
            string arguments = $"\"{mainPy}\"";
            if (!string.IsNullOrWhiteSpace(extraArgs))
            {
                arguments += $" {extraArgs}";
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = pythonExe,
                Arguments = arguments,
                WorkingDirectory = srvDir,
                CreateNoWindow = true,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };

            // 注入通用 UTF-8 环境
            startInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            startInfo.EnvironmentVariables["PYTHONUTF8"] = "1";

            try
            {
                _serverProcess = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
                _serverProcess.OutputDataReceived += (_, e) =>
                {
                    if (e.Data != null)
                    {
                        LogReceived?.Invoke(e.Data);
                    }
                };
                _serverProcess.ErrorDataReceived += (_, e) =>
                {
                    if (e.Data != null)
                    {
                        LogReceived?.Invoke($"[STDERR] {e.Data}");
                    }
                };
                _serverProcess.Exited += (_, _) =>
                {
                    LogReceived?.Invoke($"[ServerSupervisor] 服务主进程 (PID: {_serverProcess?.Id}) 已退出。");
                    AppConfigManager.ClearServerState();
                    IsAttached = false;
                    StateChanged?.Invoke(false);
                };

                bool started = _serverProcess.Start();
                if (started)
                {
                    IsAttached = false;
                    AppConfigManager.SaveServerState(_serverProcess.Id, srvDir);

                    // 绑定到 Windows Job Object
                    _jobObject?.AddProcess(_serverProcess);

                    _serverProcess.BeginOutputReadLine();
                    _serverProcess.BeginErrorReadLine();

                    LogReceived?.Invoke($"[ServerSupervisor] 服务进程已启动，PID: {_serverProcess.Id}，工作目录: {srvDir}");
                    StateChanged?.Invoke(true);
                    return Task.FromResult(true);
                }

                return Task.FromResult(false);
            }
            catch (Exception ex)
            {
                LogReceived?.Invoke($"[ServerSupervisor ERROR] 启动服务进程异常: {ex.Message}");
                return Task.FromResult(false);
            }
        }
    }

    public void StopServer()
    {
        lock (_lock)
        {
            AppConfigManager.ClearServerState();
            IsAttached = false;

            if (_serverProcess != null && !_serverProcess.HasExited)
            {
                try
                {
                    LogReceived?.Invoke("[ServerSupervisor] 正在停止服务端进程树...");
                    _serverProcess.Kill(entireProcessTree: true);
                    _serverProcess.WaitForExit(3000);
                }
                catch (Exception ex)
                {
                    LogReceived?.Invoke($"[ServerSupervisor WARNING] 终止主进程异常: {ex.Message}");
                }
                finally
                {
                    _serverProcess.Dispose();
                    _serverProcess = null;
                }
            }

            // 联动「端口管理」进行清理，释放后台可能残留的孤儿进程
            try
            {
                KillPortHolders();
            }
            catch { }

            StateChanged?.Invoke(false);
        }
    }

    public void Dispose()
    {
        StopServer();
        _jobObject?.Dispose();
        _jobObject = null;
    }
}
