using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;
using Windows.UI;

namespace LocalServer;

public sealed partial class MainPage : Page
{
    private readonly ConcurrentQueue<string> _logQueue = new();
    private readonly DispatcherTimer _logBatchTimer = new();
    private readonly DispatcherTimer _statusTimer = new();
    private readonly StringBuilder _logDisplayBuffer = new();
    private readonly List<string> _allRawLogs = new();

    private bool _autoScroll = true;
    private string _filterKeyword = "";
    private bool _redeployingCertificate;
    private bool _preparingCertificate;
    private const int MaxLogLines = 2500;
    private const string WebConsoleUrl = "http://127.0.0.1/web/gm_console.html";
    private static readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromMilliseconds(600) };

    private static readonly string[] RequiredHostsDomains =
    [
        "open.ys4fun.com",
        "prod-api-activity.ys4fun.com",
        "account.ys4fun.com",
        "skzy.ys4fun.com",
        "download.ys4fun.com",
        "download-eo.ys4fun.com",
        "packaging.ys4fun.com",
        "ta.ys4fun.com",
        "webstatic.ys4fun.com",
        "ys4fun-prod-pub.ys4fun.com"
    ];
    private const string HostsHeaderTag = "# >>> Local Server Hosts Redirect Begin >>>";
    private const string HostsFooterTag = "# <<< Local Server Hosts Redirect End <<<";

    public MainPage()
    {
        InitializeComponent();

        // 1. 初始化 100ms 批量节流定时器（防范高频日志刷新冲垮 UI）
        _logBatchTimer.Interval = TimeSpan.FromMilliseconds(100);
        _logBatchTimer.Tick += OnLogBatchTimerTick;
        _logBatchTimer.Start();

        // 2. 初始化 1.5s 状态与网络端口巡检定时器
        _statusTimer.Interval = TimeSpan.FromMilliseconds(1500);
        _statusTimer.Tick += OnStatusTimerTick;
        _statusTimer.Start();

        ServerSupervisor.Instance.LogReceived += OnLogReceived;
        ServerSupervisor.Instance.StateChanged += OnServerStateChanged;

        Loaded += MainPage_Loaded;
    }

    private async void MainPage_Loaded(object sender, RoutedEventArgs e)
    {
        // 1. 尝试自动探测并接管可能在后台运行的独立服务
        ServerSupervisor.Instance.TryAttachExistingServer();

        UpdateUIState(ServerSupervisor.Instance.IsRunning);

        LoadDynamicVersions();

        // 优先加载已持久化的配置文件，未配置项再执行环境推测
        bool configLoaded = LoadConfigFromFile();
        if (!configLoaded || string.IsNullOrEmpty(TxtClientPath.Text))
        {
            TryQuickDetectEnvironment();
        }
        else if (string.IsNullOrEmpty(TxtCdnDir.Text))
        {
            RecommendCdnDrive();
        }

        UpdateDashboardResVersionDisplay();
        CheckPortStatuses();
        CheckHostsStatus();
        await CheckCertificateStatusAsync(logToTerminal: true);

        if (ToggleAlwaysPlaySplash != null)
        {
            ToggleAlwaysPlaySplash.IsOn = AppConfigManager.AlwaysPlaySplash;
        }

        // 启动视觉与品牌动效决策：首次运行自动呈现一次；后续若开启偏好则再次呈现，默认直接跳过
        if (!AppConfigManager.HasCompletedInitialSplash || AppConfigManager.AlwaysPlaySplash)
        {
            TriggerSplashScreen(isPreview: false);
        }
    }

    #region 导航与视图切换

    private void OnNavSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is NavigationViewItem item)
        {
            string tag = item.Tag?.ToString() ?? "Dashboard";

            DashboardView.Visibility = tag == "Dashboard" ? Visibility.Visible : Visibility.Collapsed;
            ConsoleView.Visibility = tag == "Console" ? Visibility.Visible : Visibility.Collapsed;
            TerminalView.Visibility = tag == "Terminal" ? Visibility.Visible : Visibility.Collapsed;
            SettingsView.Visibility = tag == "Settings" ? Visibility.Visible : Visibility.Collapsed;

            if (tag == "Console")
            {
                EnsureConsoleLoaded();
            }
        }
    }

    private void OnGoSettingsClick(object sender, RoutedEventArgs e)
    {
        NavView.SelectedItem = NavItemSettings;
    }

    #endregion

    #region 服务启停与端口卫士

    private async void OnToggleServerClick(object sender, RoutedEventArgs e)
    {
        if (_redeployingCertificate || _preparingCertificate) return;
        if (ServerSupervisor.Instance.IsRunning)
        {
            BtnToggleServer.IsEnabled = false;
            StatusText.Text = "正在终止服务...";
            ServerSupervisor.Instance.StopServer();
            BtnToggleServer.IsEnabled = true;
            UpdateUIState(false);
        }
        else
        {
            BtnToggleServer.IsEnabled = false;
            StatusText.Text = "正在启动服务...";
            StatusBadge.Background = new SolidColorBrush(Color.FromArgb(0x26, 0x3B, 0x82, 0xF6));
            StatusText.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x3B, 0x82, 0xF6));

            _preparingCertificate = true;
            bool certificateReady;
            try { certificateReady = await PrepareCertificateForStartupAsync(); }
            finally { _preparingCertificate = false; }
            if (!certificateReady)
            {
                BtnToggleServer.IsEnabled = true;
                UpdateUIState(false);
                return;
            }

            // 装配启动参数（若有自定义配置）
            var argsList = new List<string>();
            string clientPath = TxtClientPath?.Text?.Trim() ?? "";
            if (!string.IsNullOrEmpty(clientPath))
            {
                argsList.Add("--client-assets-dir");
                argsList.Add(clientPath);
            }

            string resVer = (CmbResVersion?.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "auto";
            if (!string.IsNullOrEmpty(resVer))
            {
                argsList.Add("--res-version");
                argsList.Add(resVer);
            }

            bool started = await ServerSupervisor.Instance.StartServerAsync(argsList);

            BtnToggleServer.IsEnabled = true;
            UpdateUIState(started);

            if (started)
            {
                _ = PollAndLoadConsoleAsync();
            }
        }
    }

    private void OnKillPortsClick(object sender, RoutedEventArgs e)
    {
        BtnKillPorts.IsEnabled = false;
        try
        {
            int killed = ServerSupervisor.Instance.KillPortHolders();
            if (killed > 0)
            {
                OnLogReceived($"[PortManager] 成功释放 {killed} 个占用服务端口的残留进程。");
            }
            else
            {
                OnLogReceived("[PortManager] 端口巡检完毕，服务端口均处于空闲状态。");
            }
        }
        catch (Exception ex)
        {
            OnLogReceived($"[PortManager ERROR] 释放端口异常: {ex.Message}");
        }
        finally
        {
            BtnKillPorts.IsEnabled = true;
            CheckPortStatuses();
        }
    }

    private async Task PollAndLoadConsoleAsync()
    {
        for (int i = 0; i < 25; i++)
        {
            try
            {
                var res = await _httpClient.GetAsync(WebConsoleUrl);
                if (res.IsSuccessStatusCode)
                {
                    DispatcherQueue.TryEnqueue(EnsureConsoleLoaded);
                    break;
                }
            }
            catch
            {
                await Task.Delay(300);
            }
        }
    }

    private bool _isWebView2Initialized = false;

    private async void EnsureConsoleLoaded()
    {
        try
        {
            if (!_isWebView2Initialized)
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string webView2UserDataFolder = Path.Combine(localAppData, "LocalServer", "WebView2");
                Directory.CreateDirectory(webView2UserDataFolder);
                Environment.SetEnvironmentVariable("WEBVIEW2_USER_DATA_FOLDER", webView2UserDataFolder);

                await ConsoleWebView.EnsureCoreWebView2Async();
                _isWebView2Initialized = true;
            }

            if (ConsoleWebView.Source == null || ConsoleWebView.Source.ToString() == "about:blank")
            {
                ConsoleWebView.Source = new Uri(WebConsoleUrl);
            }
        }
        catch (Exception ex)
        {
            OnLogReceived($"[WebConsole ERROR] WebView2 环境初始化异常: {ex.Message}");
        }
    }

    private void OnReloadWebClick(object sender, RoutedEventArgs e)
    {
        try
        {
            if (!_isWebView2Initialized || ConsoleWebView.Source == null)
            {
                EnsureConsoleLoaded();
            }
            else
            {
                ConsoleWebView.Reload();
            }
        }
        catch { }
    }

    private void OnOpenBrowserClick(object sender, RoutedEventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = WebConsoleUrl,
                UseShellExecute = true
            });
        }
        catch { }
    }

    #endregion

    #region 高性能无锁节流日志引擎 (彻底消除卡死)

    private void OnLogReceived(string log)
    {
        // 关键点：绝对不在这里直接 call DispatcherQueue！仅推入无锁线程安全队列！
        _logQueue.Enqueue(log);
    }

    private void OnLogBatchTimerTick(object? sender, object e)
    {
        if (_logQueue.IsEmpty) return;

        var batchSb = new StringBuilder();
        int processedCount = 0;

        // 每次定时器最多聚合处理 500 行，避免占用 UI 帧时间
        while (_logQueue.TryDequeue(out string? line) && processedCount < 500)
        {
            if (line == null) continue;

            _allRawLogs.Add(line);
            if (_allRawLogs.Count > MaxLogLines * 2)
            {
                _allRawLogs.RemoveRange(0, 1000);
            }

            if (string.IsNullOrEmpty(_filterKeyword) || line.Contains(_filterKeyword, StringComparison.OrdinalIgnoreCase))
            {
                batchSb.AppendLine(line);
            }

            processedCount++;
        }

        if (batchSb.Length > 0)
        {
            _logDisplayBuffer.Append(batchSb);

            // 定长环形缓冲截断
            if (_logDisplayBuffer.Length > 300000)
            {
                _logDisplayBuffer.Remove(0, _logDisplayBuffer.Length - 150000);
            }

            LogTextBox.Text = _logDisplayBuffer.ToString();

            if (_autoScroll && LogTextBox != null)
            {
                LogTextBox.SelectionStart = LogTextBox.Text.Length;
                LogTextBox.SelectionLength = 0;
            }
        }

        TxtLogStats.Text = $"已记录: {_allRawLogs.Count} 行 | 缓冲待刷: {_logQueue.Count} 行";
    }

    private void OnToggleAutoScrollChanged(object sender, RoutedEventArgs e)
    {
        _autoScroll = ToggleAutoScroll.IsOn;
    }

    private void OnFilterKeywordChanged(object sender, TextChangedEventArgs e)
    {
        _filterKeyword = TxtFilterKeyword.Text.Trim();
        RebuildFilteredLogs();
    }

    private void RebuildFilteredLogs()
    {
        _logDisplayBuffer.Clear();
        var sb = new StringBuilder();

        var source = _allRawLogs.TakeLast(MaxLogLines);
        foreach (var line in source)
        {
            if (string.IsNullOrEmpty(_filterKeyword) || line.Contains(_filterKeyword, StringComparison.OrdinalIgnoreCase))
            {
                sb.AppendLine(line);
            }
        }

        _logDisplayBuffer.Append(sb);
        LogTextBox.Text = _logDisplayBuffer.ToString();

        if (_autoScroll && LogTextBox != null)
        {
            LogTextBox.SelectionStart = LogTextBox.Text.Length;
            LogTextBox.SelectionLength = 0;
        }
    }

    private void OnClearLogsClick(object sender, RoutedEventArgs e)
    {
        _allRawLogs.Clear();
        _logDisplayBuffer.Clear();
        LogTextBox.Text = "";
        TxtLogStats.Text = "已清空终端日志";
    }

    private void OnCopyLogsClick(object sender, RoutedEventArgs e)
    {
        try
        {
            var dataPackage = new DataPackage();
            dataPackage.SetText(LogTextBox.Text);
            Clipboard.SetContent(dataPackage);
            TxtLogStats.Text = "已成功复制全量日志至系统剪贴板！";
        }
        catch { }
    }

    #endregion

    #region 状态巡检与网络看板

    private void OnStatusTimerTick(object? sender, object e)
    {
        CheckPortStatuses();
    }

    private void CheckPortStatuses()
    {
        bool isRunning = ServerSupervisor.Instance.IsRunning;
        int? pid = ServerSupervisor.Instance.CurrentPid;

        if (isRunning && pid.HasValue)
        {
            StatusBadge.Background = new SolidColorBrush(Color.FromArgb(0x26, 0x10, 0xB9, 0x81));
            StatusText.Text = ServerSupervisor.Instance.IsAttached
                ? $"运行中 · 已接管 (PID: {pid.Value})"
                : $"运行中 (PID: {pid.Value})";
            StatusText.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81));
            ServerDetailText.Text = ServerSupervisor.Instance.IsAttached
                ? $"运行基准路径: {ServerSupervisor.Instance.LastResolvedServerDir ?? "已就绪"} | 进程拓扑: 已接入已有独立后台守护进程"
                : $"运行基准路径: {ServerSupervisor.Instance.LastResolvedServerDir ?? "已就绪"} | 进程拓扑: 作业对象严格宿主托管";

            TxtGwPortStatus.Text = "🟢 监听中";
            TxtGwPortStatus.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81));

            TxtGamePortStatus.Text = "🟢 业务就绪";
            TxtGamePortStatus.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81));

            TxtHttpsPortStatus.Text = "🟢 直通激活";
            TxtHttpsPortStatus.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81));

            TxtWebPortStatus.Text = "🟢 运维就绪";
            TxtWebPortStatus.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81));

            TxtCdnStateStatus.Text = "🟢 缓存就绪";
            TxtCdnStateStatus.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81));

            QueryServerActiveVersionAsync();
        }
        else
        {
            StatusBadge.Background = new SolidColorBrush(Color.FromArgb(0x26, 0xEF, 0x44, 0x44));
            StatusText.Text = "守护进程未运行";
            StatusText.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0xEF, 0x44, 0x44));
            ServerDetailText.Text = "守护状态: 闲置 (IDLE) | 端口监听: 闲置 | 托管拓扑: 待命";

            TxtGwPortStatus.Text = "空闲";
            TxtGwPortStatus.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];

            TxtGamePortStatus.Text = "空闲";
            TxtGamePortStatus.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];

            TxtHttpsPortStatus.Text = "空闲";
            TxtHttpsPortStatus.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];

            TxtWebPortStatus.Text = "空闲";
            TxtWebPortStatus.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];

            TxtCdnStateStatus.Text = "待命";
            TxtCdnStateStatus.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];

            UpdateDashboardResVersionDisplay(null);
        }
    }

    private void OnServerStateChanged(bool isRunning)
    {
        DispatcherQueue.TryEnqueue(() =>
        {
            UpdateUIState(isRunning);
            CheckPortStatuses();
        });
    }

    private void UpdateUIState(bool isRunning)
    {
        if (isRunning)
        {
            BtnServerText.Text = "终止服务 (Terminate)";
            BtnServerIcon.Glyph = "\uE71A";
        }
        else
        {
            BtnServerText.Text = "初始化并启动 (Initialize)";
            BtnServerIcon.Glyph = "\uE768";
        }
    }

    #endregion

    #region 协议版本协商与两级基线管理

    private void OnResVersionSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        UpdateDashboardResVersionDisplay();
    }

    private void LoadDynamicVersions()
    {
        try
        {
            string? srvDir = ServerSupervisor.Instance.FindServerDir();
            if (string.IsNullOrEmpty(srvDir)) return;
            string dynPath = Path.Combine(srvDir, "dynamic_versions.json");
            if (!File.Exists(dynPath)) return;

            string json = File.ReadAllText(dynPath);
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind == JsonValueKind.Object)
            {
                var existingTags = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var item in CmbResVersion.Items)
                {
                    if (item is ComboBoxItem cbi && cbi.Tag != null)
                        existingTags.Add(cbi.Tag.ToString()!);
                }

                foreach (var prop in doc.RootElement.EnumerateObject())
                {
                    string verKey = prop.Name;
                    if (!existingTags.Contains(verKey))
                    {
                        string displayName = $"Build {verKey} (动态注册)";
                        if (prop.Value.TryGetProperty("display_name", out var dnElem))
                        {
                            displayName = dnElem.GetString() ?? displayName;
                        }
                        CmbResVersion.Items.Add(new ComboBoxItem
                        {
                            Content = displayName,
                            Tag = verKey
                        });
                        existingTags.Add(verKey);
                    }
                }
            }
        }
        catch { }
    }

    private static string? InspectClientResourceBuild(string clientRoot)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(clientRoot) || !Directory.Exists(clientRoot)) return null;

            string[] candidates = [
                Path.Combine(clientRoot, "AetherGazer_Data", "StreamingAssets", "Windows"),
                Path.Combine(clientRoot, "StreamingAssets", "Windows"),
                Path.Combine(clientRoot, "AetherGazer_Data", "StreamingAssets"),
                Path.Combine(clientRoot, "StreamingAssets"),
                clientRoot
            ];

            foreach (var c in candidates)
            {
                if (!Directory.Exists(c)) continue;
                var files = Directory.GetFiles(c, "assethash_307_*.bytes");
                int maxBuild = -1;
                foreach (var f in files)
                {
                    string fname = Path.GetFileName(f);
                    var parts = fname.Split('_');
                    if (parts.Length >= 4 && int.TryParse(parts[2], out int b))
                    {
                        if (b > maxBuild) maxBuild = b;
                    }
                }
                if (maxBuild > 0)
                {
                    return maxBuild.ToString();
                }
            }
        }
        catch { }
        return null;
    }

    private void UpdateDashboardResVersionDisplay(string? liveVersion = null)
    {
        if (TxtDashResVersion == null) return;

        if (!string.IsNullOrEmpty(liveVersion))
        {
            string themeDesc = (int.TryParse(liveVersion, out int b) && b >= 311) ? "Theme 44 演进基线" : "Theme 43 兼容基线";
            TxtDashResVersion.Text = $"协议对齐基线: Build {liveVersion} ({themeDesc} · 活跃运行)";
            return;
        }

        string selectedVer = (CmbResVersion?.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "auto";
        if (selectedVer == "auto")
        {
            string clientPath = TxtClientPath?.Text?.Trim() ?? "";
            string? detectedBuild = InspectClientResourceBuild(clientPath);
            if (!string.IsNullOrEmpty(detectedBuild))
            {
                string themeDesc = (int.TryParse(detectedBuild, out int db) && db >= 311) ? "Theme 44 演进基线" : "Theme 43 兼容基线";
                TxtDashResVersion.Text = $"协议对齐基线: auto → 预期匹配 Build {detectedBuild} ({themeDesc})";
            }
            else
            {
                TxtDashResVersion.Text = "协议对齐基线: auto (客户端自动协商)";
            }
        }
        else
        {
            string themeDesc = (int.TryParse(selectedVer, out int sb) && sb >= 311) ? "Theme 44 演进基线" : "Theme 43 兼容基线";
            TxtDashResVersion.Text = $"协议对齐基线: Build {selectedVer} ({themeDesc} · 待命就绪)";
        }
    }

    private async void QueryServerActiveVersionAsync()
    {
        try
        {
            var res = await _httpClient.GetAsync("http://127.0.0.1/api/system/res_version");
            if (res.IsSuccessStatusCode)
            {
                string json = await res.Content.ReadAsStringAsync();
                using var doc = JsonDocument.Parse(json);
                if (doc.RootElement.TryGetProperty("data", out var dataElem) &&
                    dataElem.TryGetProperty("current_version", out var verElem))
                {
                    string? curVer = verElem.GetString();
                    if (!string.IsNullOrEmpty(curVer))
                    {
                        DispatcherQueue.TryEnqueue(() => UpdateDashboardResVersionDisplay(curVer));
                    }
                }
            }
        }
        catch { }
    }

    #endregion

    #region 智能探测与部署配置

    private void TryQuickDetectEnvironment()
    {
        // 1. 尝试探测客户端路径
        string? clientDir = DetectGameClientPath();
        if (!string.IsNullOrEmpty(clientDir))
        {
            TxtClientPath.Text = clientDir;
            TxtDashClientDir.Text = $"客户端: {clientDir}";
            string? buildVer = InspectClientResourceBuild(clientDir);
            if (!string.IsNullOrEmpty(buildVer))
            {
                TxtClientDetectionResult.Text = $"✔️ 已匹配客户端，资源版本: Build {buildVer} (v5.{(int.TryParse(buildVer, out int bv) && bv >= 311 ? "3" : "2")})";
            }
            else
            {
                TxtClientDetectionResult.Text = $"✔️ 已从桌面快捷方式/常见盘符自动匹配";
            }
            UpdateDashboardResVersionDisplay();
        }

        // 2. 自动推荐 CDN 磁盘
        RecommendCdnDrive();
    }

    private string? DetectGameClientPath()
    {
        // A. 嗅探桌面快捷方式
        try
        {
            string[] desktopDirs = [
                Environment.GetFolderPath(Environment.SpecialFolder.Desktop),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory)
            ];

            foreach (var d in desktopDirs)
            {
                if (!Directory.Exists(d)) continue;
                foreach (var lnk in Directory.GetFiles(d, "*.lnk"))
                {
                    string filename = Path.GetFileNameWithoutExtension(lnk);
                    if (filename.Contains("深空之眼") || filename.Contains("AetherGazer") || filename.Contains("AtherGazer"))
                    {
                        string? target = ResolveShortcutTarget(lnk);
                        if (!string.IsNullOrEmpty(target) && File.Exists(target))
                        {
                            string dir = Path.GetDirectoryName(target) ?? "";
                            string probe1 = Path.Combine(dir, "AetherGazer_Data", "StreamingAssets", "Windows");
                            if (Directory.Exists(probe1)) return dir;

                            string probe2 = Path.Combine(dir, "AetherGazer", "AetherGazer_Data", "StreamingAssets", "Windows");
                            if (Directory.Exists(probe2)) return Path.Combine(dir, "AetherGazer");
                        }
                    }
                }
            }
        }
        catch { }

        // B. 扫描各盘符常见安装目录
        string[] candidates = [
            @"D:\AtherGazer\AetherGazerLauncher\AetherGazer",
            @"D:\AetherGazer\AetherGazerLauncher\AetherGazer",
            @"C:\Program Files\AetherGazer\AetherGazerLauncher\AetherGazer",
            @"E:\AtherGazer\AetherGazerLauncher\AetherGazer",
            @"E:\AetherGazer\AetherGazerLauncher\AetherGazer"
        ];

        foreach (var c in candidates)
        {
            string sa = Path.Combine(c, "AetherGazer_Data", "StreamingAssets", "Windows");
            if (Directory.Exists(sa)) return c;
        }

        return null;
    }

    private static string? ResolveShortcutTarget(string shortcutPath)
    {
        try
        {
            Type? shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType != null)
            {
                dynamic shell = Activator.CreateInstance(shellType)!;
                dynamic shortcut = shell.CreateShortcut(shortcutPath);
                return shortcut.TargetPath;
            }
        }
        catch { }
        return null;
    }

    private void OnAutoDetectClientClick(object sender, RoutedEventArgs e)
    {
        string? client = DetectGameClientPath();
        if (!string.IsNullOrEmpty(client))
        {
            TxtClientPath.Text = client;
            TxtDashClientDir.Text = $"客户端: {client}";
            string? buildVer = InspectClientResourceBuild(client);
            if (!string.IsNullOrEmpty(buildVer))
            {
                TxtClientDetectionResult.Text = $"✔️ 自动探测成功，已识别客户端 (资源构建: Build {buildVer} · v5.{(int.TryParse(buildVer, out int bv) && bv >= 311 ? "3" : "2")})";
            }
            else
            {
                TxtClientDetectionResult.Text = "自动探测完成，已识别客户端运行时目录。";
            }
            UpdateDashboardResVersionDisplay();
        }
        else
        {
            TxtClientDetectionResult.Text = "未在默认路径找到客户端，请使用「浏览」按钮手动指定。";
        }
    }

    private async void OnBrowseClientClick(object sender, RoutedEventArgs e)
    {
        string? folder = await PickFolderAsync("选择客户端运行时根目录 (包含 AetherGazer_Data)");
        if (!string.IsNullOrEmpty(folder))
        {
            TxtClientPath.Text = folder;
            TxtDashClientDir.Text = $"客户端: {folder}";
            string? buildVer = InspectClientResourceBuild(folder);
            if (!string.IsNullOrEmpty(buildVer))
            {
                TxtClientDetectionResult.Text = $"✔️ 已选定自定义路径 (资源构建: Build {buildVer} · v5.{(int.TryParse(buildVer, out int bv) && bv >= 311 ? "3" : "2")})";
            }
            else
            {
                TxtClientDetectionResult.Text = "已选定自定义路径。";
            }
            UpdateDashboardResVersionDisplay();
        }
    }

    private void RecommendCdnDrive()
    {
        try
        {
            DriveInfo? bestDrive = null;
            long maxSpace = 0;

            foreach (var drive in DriveInfo.GetDrives())
            {
                if (drive.IsReady && drive.DriveType == DriveType.Fixed)
                {
                    // 优先挑选非系统盘
                    bool isSystem = drive.Name.StartsWith("C:", StringComparison.OrdinalIgnoreCase);
                    long space = drive.AvailableFreeSpace;
                    if (!isSystem && space > maxSpace)
                    {
                        maxSpace = space;
                        bestDrive = drive;
                    }
                    else if (bestDrive == null && space > maxSpace)
                    {
                        maxSpace = space;
                        bestDrive = drive;
                    }
                }
            }

            if (bestDrive != null)
            {
                double freeGb = maxSpace / (1024.0 * 1024.0 * 1024.0);
                string recommendPath = Path.Combine(bestDrive.Name, "AlphaPersei_CDN_Cache");
                TxtCdnDir.Text = recommendPath;
                TxtCdnDriveResult.Text = $"推荐使用 {bestDrive.Name[..2]} 盘作为缓存目录 (剩余可用空间: {freeGb:F1} GB)";
            }
        }
        catch { }
    }

    private void OnRecommendCdnDriveClick(object sender, RoutedEventArgs e)
    {
        RecommendCdnDrive();
    }

    private async void OnBrowseCdnClick(object sender, RoutedEventArgs e)
    {
        string? folder = await PickFolderAsync("选择 CDN 缓存存储目录 (建议预留 20GB+ 空间)");
        if (!string.IsNullOrEmpty(folder))
        {
            TxtCdnDir.Text = folder;
            TxtCdnDriveResult.Text = "已选定缓存存储路径。";
        }
    }

    private Task<string?> PickFolderAsync(string title)
    {
        IntPtr hwnd = IntPtr.Zero;
        if (App.MainWindowInstance != null)
        {
            hwnd = WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindowInstance);
        }
        string? folder = FolderPickerHelper.PickFolder(hwnd, title);
        return Task.FromResult(folder);
    }

    #region 证书管理与安全基线

    private async void OnCheckCertClick(object sender, RoutedEventArgs e)
    {
        if (!_redeployingCertificate) await CheckCertificateStatusAsync(logToTerminal: true);
    }

    private async Task<bool> CheckCertificateStatusAsync(bool logToTerminal = false)
    {
        try
        {
            string directory = ServerSupervisor.Instance.FindServerDir()
                ?? throw new InvalidOperationException("未定位服务端目录。");
            CertificateStatus status = await CertificateManager.InspectAsync(
                ServerSupervisor.Instance.FindPythonPath(), directory);
            string thumb = status.Files.RootThumbprint;
            UpdateCertUIStatus(status.Ready, status.Badge,
                string.IsNullOrEmpty(thumb) ? null : $"指纹: {thumb[..Math.Min(8, thumb.Length)]}...");
            if (logToTerminal)
            {
                if (status.Ready)
                    OnLogReceived($"[CertManager SUCCESS] 游戏握手证书检查通过，根指纹: {thumb}；服务证书有效至 {status.Files.LeafNotAfter}。");
                foreach (string issue in status.Issues) OnLogReceived("[CertManager WARNING] " + issue);
            }
            return status.Ready;
        }
        catch (Exception error)
        {
            UpdateCertUIStatus(false, "⚠️ 证书检查失败", null);
            OnLogReceived("[CertManager ERROR] " + error.Message);
            return false;
        }
    }

    private async Task<bool> PrepareCertificateForStartupAsync()
    {
        try
        {
            string directory = ServerSupervisor.Instance.FindServerDir()
                ?? throw new InvalidOperationException("未定位服务端目录。");
            // 只自动修复叶证书与安装文件；正常启动不导入系统根、不替换既有根。
            CertificateCommand prepared = await CertificateManager.RunGeneratorAsync(
                ServerSupervisor.Instance.FindPythonPath(), directory);
            if (prepared.ExitCode != 0)
            {
                LogCertificateCommand(prepared);
                await CheckCertificateStatusAsync(logToTerminal: true);
                OnLogReceived("[CertManager WARNING] 请点击【重新部署并安装根证书】后再启动游戏服务。");
                return false;
            }
            return await CheckCertificateStatusAsync(logToTerminal: true);
        }
        catch (Exception error)
        {
            UpdateCertUIStatus(false, "⚠️ 证书准备失败", null);
            OnLogReceived("[CertManager ERROR] " + error.Message);
            return false;
        }
    }

    private void LogCertificateCommand(CertificateCommand command)
    {
        foreach (string line in (command.Output + "\n" + command.Error).Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries))
            OnLogReceived("[CertManager] " + line);
    }

    private void UpdateCertUIStatus(bool isTrusted, string badgeText, string? thumbprintText)
    {
        DispatcherQueue.TryEnqueue(() =>
        {
            if (BadgeCertStatus != null && TxtCertStatus != null)
            {
                BadgeCertStatus.Background = new SolidColorBrush(isTrusted
                    ? Color.FromArgb(0x26, 0x10, 0xB9, 0x81)
                    : Color.FromArgb(0x26, 0xF5, 0x9E, 0x0B));
                TxtCertStatus.Foreground = new SolidColorBrush(isTrusted
                    ? Color.FromArgb(0xFF, 0x10, 0xB9, 0x81)
                    : Color.FromArgb(0xFF, 0xF5, 0x9E, 0x0B));
                TxtCertStatus.Text = badgeText;
            }
            if (TxtCertThumbprint != null)
            {
                TxtCertThumbprint.Text = thumbprintText ?? "";
            }
        });
    }

    private async void OnRedeployCertClick(object sender, RoutedEventArgs e)
    {
        if (_redeployingCertificate || _preparingCertificate) return;
        if (ServerSupervisor.Instance.IsRunning)
        {
            OnLogReceived("[CertManager WARNING] 请先停止服务，再重新部署证书；运行中的 HTTPS 监听仍使用启动时加载的证书。");
            return;
        }
        _redeployingCertificate = true;
        BtnToggleServer.IsEnabled = false;
        if (BtnQuickRedeployCert != null) BtnQuickRedeployCert.IsEnabled = false;
        try
        {
            string directory = ServerSupervisor.Instance.FindServerDir()
                ?? throw new InvalidOperationException("未定位服务端目录。");
            string python = ServerSupervisor.Instance.FindPythonPath();
            CertificateStatus previous = await CertificateManager.InspectAsync(python, directory);
            bool replaceRoot = previous.Files.RequiresRootReplacement;
            OnLogReceived(replaceRoot
                ? "[CertManager] 根证书已丢失、损坏或临期，将重新生成根证书并同步 iOS 描述文件。"
                : "[CertManager] 保留现有根证书，修复服务证书及 Windows/iOS 安装文件。");
            CertificateCommand generated = await CertificateManager.RunGeneratorAsync(
                python, directory, replaceRoot ? "--force-ca" : "--force");
            LogCertificateCommand(generated);
            if (generated.ExitCode != 0)
                throw new InvalidOperationException($"证书生成失败（退出码 {generated.ExitCode}），未执行系统导入。");

            CertificateStatus filesCheck = await CertificateManager.InspectAsync(python, directory);
            if (!filesCheck.Files.Ready)
                throw new InvalidOperationException("生成后证书检查未通过：" + string.Join("；", filesCheck.Files.Issues));
            bool rootChanged = !string.Equals(previous.Files.RootThumbprint,
                filesCheck.Files.RootThumbprint, StringComparison.OrdinalIgnoreCase);
            // 同步文件已经生成；实际换根时即使 Windows 导入失败，也应提示 iOS 信任需要更新。
            if (rootChanged)
            {
                OnLogReceived("[CertManager WARNING] 根证书已更新：iOS 请重新安装 AetherGazer.mobileconfig，并在设置→通用→关于本机→证书信任设置开启完全信任。");
                var notice = new ContentDialog
                {
                    XamlRoot = this.XamlRoot,
                    Title = "iOS 根证书需要更新",
                    Content = "新的 iOS 描述文件已同步生成。请在 iPhone 重新安装 AetherGazer.mobileconfig，并在“设置 → 通用 → 关于本机 → 证书信任设置”开启新根证书的完全信任。",
                    CloseButtonText = "知道了",
                };
                await notice.ShowAsync();
            }
            else
                OnLogReceived("[CertManager] 根指纹保持不变，iOS 已信任的根无需重新安装；两份描述文件已检查同步。");

            try
            {
                CertificateManager.InstallCurrentUserRoot(directory);
                OnLogReceived("[CertManager] 当前用户根证书导入并精确核验通过。");
            }
            catch (Exception error)
            {
                OnLogReceived("[CertManager WARNING] 当前用户根导入失败：" + error.Message);
            }
            try
            {
                int code = await CertificateManager.InstallMachineRootAsync(directory);
                if (code != 0) OnLogReceived($"[CertManager WARNING] 计算机根导入失败，退出码 {code}。");
            }
            catch (System.ComponentModel.Win32Exception error) when (error.NativeErrorCode == 1223)
            {
                OnLogReceived("[CertManager] 管理员授权已取消，下面检查当前用户是否仍能通过游戏握手。");
            }
            catch (Exception error)
            {
                OnLogReceived("[CertManager WARNING] 计算机根导入失败：" + error.Message);
            }
            if (!await CheckCertificateStatusAsync(logToTerminal: true))
                OnLogReceived("[CertManager ERROR] 部署未完成：当前 Windows 游戏握手校验仍未通过。");
        }
        catch (Exception error)
        {
            OnLogReceived("[CertManager ERROR] " + error.Message);
            await CheckCertificateStatusAsync(logToTerminal: true);
        }
        finally
        {
            _redeployingCertificate = false;
            BtnToggleServer.IsEnabled = true;
            if (BtnQuickRedeployCert != null) BtnQuickRedeployCert.IsEnabled = true;
        }
    }

    #endregion

    private bool LoadConfigFromFile()
    {
        bool loadedAny = false;
        try
        {
            string? srvDir = ServerSupervisor.Instance.FindServerDir();
            var candidatePaths = new List<string>();

            // 1. 优先读取用户在 LocalAppData 中的自定义覆盖配置 (优先 LocalServer，向后兼容 TwilightStation)
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string userConfigPath = Path.Combine(localAppData, "LocalServer", "server_config.json");
            if (File.Exists(userConfigPath))
            {
                candidatePaths.Add(userConfigPath);
            }
            string legacyConfigPath = Path.Combine(localAppData, "TwilightStation", "server_config.json");
            if (File.Exists(legacyConfigPath))
            {
                candidatePaths.Add(legacyConfigPath);
            }

            // 2. 其次读取服务端安装目录自带的默认配置模板
            if (!string.IsNullOrEmpty(srvDir))
            {
                string srvConfigPath = Path.Combine(srvDir, "server_config.json");
                if (File.Exists(srvConfigPath) && !candidatePaths.Contains(srvConfigPath))
                {
                    candidatePaths.Add(srvConfigPath);
                }
            }

            foreach (var configPath in candidatePaths)
            {
                try
                {
                    string json = File.ReadAllText(configPath, Encoding.UTF8);
                    using var doc = JsonDocument.Parse(json);
                    var root = doc.RootElement;

                    if (root.TryGetProperty("client", out var clientElem) &&
                        clientElem.TryGetProperty("game_install_dir", out var gameDirElem))
                    {
                        string? clientDir = gameDirElem.GetString();
                        if (!string.IsNullOrWhiteSpace(clientDir) && Directory.Exists(clientDir))
                        {
                            TxtClientPath.Text = clientDir;
                            TxtDashClientDir.Text = $"客户端: {clientDir}";
                            TxtClientDetectionResult.Text = "已加载配置文件中的客户端路径。";
                            loadedAny = true;
                        }
                    }

                    if (root.TryGetProperty("storage", out var storageElem) &&
                        storageElem.TryGetProperty("cdn_cache_dir", out var cdnDirElem))
                    {
                        string? cdnDir = cdnDirElem.GetString();
                        if (!string.IsNullOrWhiteSpace(cdnDir))
                        {
                            TxtCdnDir.Text = cdnDir;
                            TxtCdnDriveResult.Text = "已加载配置文件中的 CDN 缓存目录。";
                            loadedAny = true;
                        }
                    }

                    if (root.TryGetProperty("network", out var networkElem) &&
                        networkElem.TryGetProperty("default_res_version", out var resVerElem))
                    {
                        string? resVer = resVerElem.GetString();
                        if (!string.IsNullOrWhiteSpace(resVer))
                        {
                            foreach (var item in CmbResVersion.Items)
                            {
                                if (item is ComboBoxItem cbi && string.Equals(cbi.Tag?.ToString(), resVer, StringComparison.OrdinalIgnoreCase))
                                {
                                    CmbResVersion.SelectedItem = cbi;
                                    break;
                                }
                            }
                        }
                    }

                    if (root.TryGetProperty("lifecycle", out var lcElem) &&
                        lcElem.TryGetProperty("on_close_action", out var actionElem))
                    {
                        string? actionStr = actionElem.GetString();
                        if (!string.IsNullOrWhiteSpace(actionStr))
                        {
                            foreach (var item in CmbCloseAction.Items)
                            {
                                if (item is ComboBoxItem cbi && string.Equals(cbi.Tag?.ToString(), actionStr, StringComparison.OrdinalIgnoreCase))
                                {
                                    CmbCloseAction.SelectedItem = cbi;
                                    break;
                                }
                            }
                        }
                    }
                    else
                    {
                        string currentPref = AppConfigManager.CloseAction switch
                        {
                            CloseActionPreference.KillServer => "kill",
                            CloseActionPreference.KeepServer => "keep",
                            _ => "ask"
                        };
                        foreach (var item in CmbCloseAction.Items)
                        {
                            if (item is ComboBoxItem cbi && string.Equals(cbi.Tag?.ToString(), currentPref, StringComparison.OrdinalIgnoreCase))
                            {
                                CmbCloseAction.SelectedItem = cbi;
                                break;
                            }
                        }
                    }

                    if (root.TryGetProperty("presentation", out var presElem))
                    {
                        if (presElem.TryGetProperty("has_completed_initial_splash", out var hasElem))
                        {
                            AppConfigManager.HasCompletedInitialSplash = hasElem.GetBoolean();
                        }
                        if (presElem.TryGetProperty("always_play_splash", out var alwaysElem))
                        {
                            AppConfigManager.AlwaysPlaySplash = alwaysElem.GetBoolean();
                            if (ToggleAlwaysPlaySplash != null)
                            {
                                ToggleAlwaysPlaySplash.IsOn = AppConfigManager.AlwaysPlaySplash;
                            }
                        }
                    }

                    if (loadedAny)
                    {
                        TxtSaveResult.Text = $"已读取配置: {Path.GetFileName(configPath)}";
                        OnLogReceived($"[ConfigManager] 成功读取配置: {configPath}");
                        break;
                    }
                }
                catch (Exception ex)
                {
                    OnLogReceived($"[ConfigManager] 读取 {configPath} 失败: {ex.Message}");
                }
            }
        }
        catch (Exception ex)
        {
            OnLogReceived($"[ConfigManager ERROR] 读取配置异常: {ex.Message}");
        }

        return loadedAny;
    }

    private void OnCloseActionSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (CmbCloseAction?.SelectedItem is ComboBoxItem cbi)
        {
            string tag = cbi.Tag?.ToString() ?? "ask";
            var pref = tag switch
            {
                "kill" => CloseActionPreference.KillServer,
                "keep" => CloseActionPreference.KeepServer,
                _ => CloseActionPreference.Ask
            };
            AppConfigManager.SaveCloseAction(pref);
        }
    }

    private void OnSaveConfigClick(object sender, RoutedEventArgs e)
    {
        try
        {
            string? srvDir = ServerSupervisor.Instance.FindServerDir();
            string clientPath = TxtClientPath.Text.Trim();
            string cdnPath = TxtCdnDir.Text.Trim();
            string resVer = (CmbResVersion.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "auto";
            string closeAction = (CmbCloseAction.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "ask";
            bool alwaysPlay = ToggleAlwaysPlaySplash?.IsOn ?? AppConfigManager.AlwaysPlaySplash;
            bool hasCompleted = AppConfigManager.HasCompletedInitialSplash;

            string json = $$"""
            {
              "storage": {
                "cdn_cache_dir": "{{cdnPath.Replace("\\", "/")}}",
                "notes": "CDN资源落包根目录"
              },
              "client": {
                "game_install_dir": "{{clientPath.Replace("\\", "/")}}",
                "notes": "客户端安装目录"
              },
              "network": {
                "capture_cdn": true,
                "default_res_version": "{{resVer}}"
              },
              "lifecycle": {
                "on_close_action": "{{closeAction}}"
              },
              "presentation": {
                "has_completed_initial_splash": {{hasCompleted.ToString().ToLowerInvariant()}},
                "always_play_splash": {{alwaysPlay.ToString().ToLowerInvariant()}}
              }
            }
            """;

            // 优先写入服务端安装目录；若受 UAC / Program Files 权限写保护，平滑降级写入 LocalAppData
            bool saved = false;
            if (!string.IsNullOrEmpty(srvDir))
            {
                string configPath = Path.Combine(srvDir, "server_config.json");
                try
                {
                    File.WriteAllText(configPath, json, Encoding.UTF8);
                    TxtSaveResult.Text = "配置已成功保存至 server_config.json。";
                    OnLogReceived($"[ConfigManager] 参数已持久化至 {configPath}");
                    saved = true;
                }
                catch (UnauthorizedAccessException)
                {
                    // 权限不足，降级处理
                }
            }

            if (!saved)
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string userConfigDir = Path.Combine(localAppData, "LocalServer");
                Directory.CreateDirectory(userConfigDir);
                string userConfigPath = Path.Combine(userConfigDir, "server_config.json");
                File.WriteAllText(userConfigPath, json, Encoding.UTF8);

                TxtSaveResult.Text = "配置已安全保存至用户本地数据区 (LocalAppData)。";
                OnLogReceived($"[ConfigManager] 服务端目录受系统写保护，配置已平滑降级保存至: {userConfigPath}");
            }

            UpdateDashboardResVersionDisplay();
        }
        catch (Exception ex)
        {
            TxtSaveResult.Text = $"保存失败: {ex.Message}";
        }
    }

    #endregion

    #region HOSTS 重定向检测与控制

    private void OnCheckHostsClick(object sender, RoutedEventArgs e)
    {
        CheckHostsStatus();
    }

    private void CheckHostsStatus()
    {
        try
        {
            string hostsPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "drivers", "etc", "hosts");
            if (!File.Exists(hostsPath))
            {
                BadgeHostsStatus.Background = new SolidColorBrush(Color.FromArgb(0x26, 0xEF, 0x44, 0x44));
                TxtHostsBadge.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0xEF, 0x44, 0x44));
                TxtHostsBadge.Text = "文件未找到";
                TxtHostsDetail.Text = "系统 HOSTS 文件不存在或无法访问。";
                TxtHostsOpResult.Text = "未找到 HOSTS 文件。";
                return;
            }

            string content = File.ReadAllText(hostsPath);
            int matchedCount = CountConfiguredHostsDomains(content);
            int total = RequiredHostsDomains.Length;

            if (matchedCount == total)
            {
                BadgeHostsStatus.Background = new SolidColorBrush(Color.FromArgb(0x26, 0x10, 0xB9, 0x81));
                TxtHostsBadge.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81));
                TxtHostsBadge.Text = $"已重定向 ({matchedCount}/{total})";
                TxtHostsDetail.Text = "所有服务目标域名均已重定向解析至 127.0.0.1 本地回环。";
            }
            else if (matchedCount == 0)
            {
                BadgeHostsStatus.Background = new SolidColorBrush(Color.FromArgb(0x26, 0x6B, 0x72, 0x80));
                TxtHostsBadge.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0x9C, 0xA3, 0xAF));
                TxtHostsBadge.Text = $"未配置 (0/{total})";
                TxtHostsDetail.Text = "尚未配置本地域名路由规则。客户端将解析至外部远端服务。";
            }
            else
            {
                BadgeHostsStatus.Background = new SolidColorBrush(Color.FromArgb(0x26, 0xF5, 0x9E, 0x0B));
                TxtHostsBadge.Foreground = new SolidColorBrush(Color.FromArgb(0xFF, 0xF5, 0x9E, 0x0B));
                TxtHostsBadge.Text = $"部分重定向 ({matchedCount}/{total})";
                TxtHostsDetail.Text = $"当前仅部分域名解析至本地回环 ({matchedCount}/{total})，建议补全路由重定向配置。";
            }

            TxtHostsOpResult.Text = $"检测就绪: {matchedCount}/{total} 个目标域名已映射至 127.0.0.1";
        }
        catch (Exception ex)
        {
            TxtHostsOpResult.Text = $"检测异常: {ex.Message}";
        }
    }

    private static int CountConfiguredHostsDomains(string hostsContent)
    {
        var matched = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var lines = hostsContent.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);

        foreach (var rawLine in lines)
        {
            string line = rawLine.Trim();
            if (line.StartsWith('#') || string.IsNullOrWhiteSpace(line)) continue;

            var parts = line.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length >= 2 && parts[0] == "127.0.0.1")
            {
                for (int i = 1; i < parts.Length; i++)
                {
                    string candidate = parts[i];
                    if (candidate.StartsWith('#')) break;
                    foreach (var domain in RequiredHostsDomains)
                    {
                        if (string.Equals(domain, candidate, StringComparison.OrdinalIgnoreCase))
                        {
                            matched.Add(domain);
                        }
                    }
                }
            }
        }

        return matched.Count;
    }

    private async void OnApplyHostsClick(object sender, RoutedEventArgs e)
    {
        BtnApplyHosts.IsEnabled = false;
        BtnRemoveHosts.IsEnabled = false;
        BtnCheckHosts.IsEnabled = false;
        TxtHostsOpResult.Text = "正在请求管理员权限写入 HOSTS...";

        try
        {
            string script = GenerateApplyHostsScript();
            bool success = await ExecuteElevatedPowerShellAsync(script);
            if (success)
            {
                TxtHostsOpResult.Text = "HOSTS 写入完成，已备份原文件并刷新 DNS 缓存。";
                OnLogReceived("[HostsManager] HOSTS 规则写入完成，已刷新 DNS 解析缓存。");
            }
            else
            {
                TxtHostsOpResult.Text = "HOSTS 写入未完成（用户取消提权或权限不足）。";
                OnLogReceived("[HostsManager WARNING] 用户取消了 UAC 提权或脚本执行未成功。");
            }
        }
        catch (Exception ex)
        {
            TxtHostsOpResult.Text = $"写入异常: {ex.Message}";
            OnLogReceived($"[HostsManager ERROR] 写入异常: {ex.Message}");
        }
        finally
        {
            BtnApplyHosts.IsEnabled = true;
            BtnRemoveHosts.IsEnabled = true;
            BtnCheckHosts.IsEnabled = true;
            CheckHostsStatus();
        }
    }

    private async void OnRemoveHostsClick(object sender, RoutedEventArgs e)
    {
        BtnApplyHosts.IsEnabled = false;
        BtnRemoveHosts.IsEnabled = false;
        BtnCheckHosts.IsEnabled = false;
        TxtHostsOpResult.Text = "正在请求管理员权限清除 HOSTS 重定向...";

        try
        {
            string script = GenerateRemoveHostsScript();
            bool success = await ExecuteElevatedPowerShellAsync(script);
            if (success)
            {
                TxtHostsOpResult.Text = "HOSTS 重定向已清除，已备份原文件并刷新 DNS 缓存。";
                OnLogReceived("[HostsManager] HOSTS 重定向已恢复默认，已刷新 DNS 解析缓存。");
            }
            else
            {
                TxtHostsOpResult.Text = "HOSTS 清除未完成（用户取消提权或权限不足）。";
                OnLogReceived("[HostsManager WARNING] 用户取消了 UAC 提权或脚本执行未成功。");
            }
        }
        catch (Exception ex)
        {
            TxtHostsOpResult.Text = $"清除异常: {ex.Message}";
            OnLogReceived($"[HostsManager ERROR] 清除异常: {ex.Message}");
        }
        finally
        {
            BtnApplyHosts.IsEnabled = true;
            BtnRemoveHosts.IsEnabled = true;
            BtnCheckHosts.IsEnabled = true;
            CheckHostsStatus();
        }
    }

    private static string GenerateApplyHostsScript()
    {
        var sb = new StringBuilder();
        sb.AppendLine("$ErrorActionPreference = 'Stop'");
        sb.AppendLine("$hostsPath = [System.IO.Path]::Combine($env:windir, 'System32', 'drivers', 'etc', 'hosts')");
        sb.AppendLine("if (-not (Test-Path $hostsPath)) { exit 1 }");
        sb.AppendLine("$backupPath = \"$hostsPath.bak.$((Get-Date).ToString('yyyyMMdd_HHmmss'))\"");
        sb.AppendLine("Copy-Item -Path $hostsPath -Destination $backupPath -Force");
        sb.AppendLine("$raw = [System.IO.File]::ReadAllText($hostsPath, [System.Text.Encoding]::UTF8)");
        sb.AppendLine("$pattern = '(?s)# >>> (?:Local Server|TwilightStation) Hosts Redirect Begin >>>.*?# <<< (?:Local Server|TwilightStation) Hosts Redirect End <<<\r?\n?'");
        sb.AppendLine("$cleaned = [System.Text.RegularExpressions.Regex]::Replace($raw, $pattern, '')");

        sb.AppendLine("$block = @'");
        sb.AppendLine(HostsHeaderTag);
        foreach (var domain in RequiredHostsDomains)
        {
            sb.AppendLine($"127.0.0.1 {domain}");
        }
        sb.AppendLine(HostsFooterTag);
        sb.AppendLine("'@");

        sb.AppendLine("$newContent = $cleaned.TrimEnd() + \"`r`n`r`n\" + $block + \"`r`n\"");
        sb.AppendLine("[System.IO.File]::WriteAllText($hostsPath, $newContent, [System.Text.Encoding]::UTF8)");
        sb.AppendLine("ipconfig /flushdns | Out-Null");
        sb.AppendLine("exit 0");
        return sb.ToString();
    }

    private static string GenerateRemoveHostsScript()
    {
        var sb = new StringBuilder();
        sb.AppendLine("$ErrorActionPreference = 'Stop'");
        sb.AppendLine("$hostsPath = [System.IO.Path]::Combine($env:windir, 'System32', 'drivers', 'etc', 'hosts')");
        sb.AppendLine("if (-not (Test-Path $hostsPath)) { exit 1 }");
        sb.AppendLine("$backupPath = \"$hostsPath.bak.$((Get-Date).ToString('yyyyMMdd_HHmmss'))\"");
        sb.AppendLine("Copy-Item -Path $hostsPath -Destination $backupPath -Force");
        sb.AppendLine("$raw = [System.IO.File]::ReadAllText($hostsPath, [System.Text.Encoding]::UTF8)");
        sb.AppendLine("$pattern = '(?s)# >>> (?:Local Server|TwilightStation) Hosts Redirect Begin >>>.*?# <<< (?:Local Server|TwilightStation) Hosts Redirect End <<<\r?\n?'");
        sb.AppendLine("$cleaned = [System.Text.RegularExpressions.Regex]::Replace($raw, $pattern, '')");
        sb.AppendLine("[System.IO.File]::WriteAllText($hostsPath, $cleaned.TrimEnd() + \"`r`n\", [System.Text.Encoding]::UTF8)");
        sb.AppendLine("ipconfig /flushdns | Out-Null");
        sb.AppendLine("exit 0");
        return sb.ToString();
    }

    private static async Task<bool> ExecuteElevatedPowerShellAsync(string scriptContent)
    {
        string tempScript = Path.Combine(Path.GetTempPath(), $"localserver_hosts_{Guid.NewGuid():N}.ps1");
        try
        {
            await File.WriteAllTextAsync(tempScript, scriptContent, Encoding.UTF8);

            var psi = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = $"-NoProfile -ExecutionPolicy Bypass -File \"{tempScript}\"",
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden
            };

            using var proc = Process.Start(psi);
            if (proc == null) return false;

            await proc.WaitForExitAsync();
            return proc.ExitCode == 0;
        }
        catch
        {
            return false;
        }
        finally
        {
            try
            {
                if (File.Exists(tempScript))
                {
                    File.Delete(tempScript);
                }
            }
            catch { }
        }
    }

    #endregion

    #region 品牌启动屏动效与呈现管理 (Brand Splash Screen Lifecycle)

    private bool _isSplashDismissed = false;
    private bool _isSplashWebViewInitialized = false;
    private DispatcherTimer? _splashSafetyTimer;

    private async void TriggerSplashScreen(bool isPreview)
    {
        try
        {
            _isSplashDismissed = false;
            SplashOverlay.Opacity = 1.0;
            SplashOverlay.Visibility = Visibility.Visible;

            if (!_isSplashWebViewInitialized)
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string webView2UserDataFolder = Path.Combine(localAppData, "LocalServer", "WebView2");
                Directory.CreateDirectory(webView2UserDataFolder);
                Environment.SetEnvironmentVariable("WEBVIEW2_USER_DATA_FOLDER", webView2UserDataFolder);

                await SplashWebView.EnsureCoreWebView2Async();
                SplashWebView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                SplashWebView.CoreWebView2.Settings.AreDevToolsEnabled = false;
                SplashWebView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                SplashWebView.CoreWebView2.WebMessageReceived += OnSplashWebMessageReceived;
                _isSplashWebViewInitialized = true;
            }

            string splashHtmlPath = Path.Combine(AppContext.BaseDirectory, "Assets", "splash.html");
            if (File.Exists(splashHtmlPath))
            {
                string html = File.ReadAllText(splashHtmlPath, Encoding.UTF8);
                SplashWebView.NavigateToString(html);
            }
            else
            {
                DismissSplashScreen(isPreview);
                return;
            }

            // 4.5秒安全超时熔断计时器（防范异常导致遮罩卡死）
            _splashSafetyTimer?.Stop();
            _splashSafetyTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(4500) };
            _splashSafetyTimer.Tick += (s, e) =>
            {
                _splashSafetyTimer?.Stop();
                DismissSplashScreen(isPreview);
            };
            _splashSafetyTimer.Start();
        }
        catch (Exception ex)
        {
            OnLogReceived($"[Splash ERROR] 启动屏初始化异常: {ex.Message}");
            DismissSplashScreen(isPreview);
        }
    }

    private void OnSplashWebMessageReceived(Microsoft.Web.WebView2.Core.CoreWebView2 sender, Microsoft.Web.WebView2.Core.CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            string msg = args.TryGetWebMessageAsString();
            if (msg == "splash_completed")
            {
                DismissSplashScreen(isPreview: false);
            }
        }
        catch { }
    }

    private void DismissSplashScreen(bool isPreview = false)
    {
        if (_isSplashDismissed) return;
        _isSplashDismissed = true;
        _splashSafetyTimer?.Stop();

        // 仅在真实启动流程中将首次完成标记置为 true 并持久化
        if (!isPreview && !AppConfigManager.HasCompletedInitialSplash)
        {
            AppConfigManager.SavePresentationConfig(hasCompleted: true, alwaysPlay: AppConfigManager.AlwaysPlaySplash);
        }

        // WinUI 3 平滑渐变消隐 (350ms)
        try
        {
            var fadeAnim = new Microsoft.UI.Xaml.Media.Animation.DoubleAnimation
            {
                From = 1.0,
                To = 0.0,
                Duration = new Duration(TimeSpan.FromMilliseconds(350)),
                EasingFunction = new Microsoft.UI.Xaml.Media.Animation.CubicEase { EasingMode = Microsoft.UI.Xaml.Media.Animation.EasingMode.EaseOut }
            };

            var sb = new Microsoft.UI.Xaml.Media.Animation.Storyboard();
            sb.Children.Add(fadeAnim);
            Microsoft.UI.Xaml.Media.Animation.Storyboard.SetTarget(fadeAnim, SplashOverlay);
            Microsoft.UI.Xaml.Media.Animation.Storyboard.SetTargetProperty(fadeAnim, "Opacity");

            sb.Completed += (s, e) =>
            {
                SplashOverlay.Visibility = Visibility.Collapsed;
                SplashOverlay.Opacity = 1.0;
            };

            sb.Begin();
        }
        catch
        {
            SplashOverlay.Visibility = Visibility.Collapsed;
            SplashOverlay.Opacity = 1.0;
        }
    }

    private void OnToggleAlwaysPlaySplashChanged(object sender, RoutedEventArgs e)
    {
        if (ToggleAlwaysPlaySplash != null)
        {
            AppConfigManager.SavePresentationConfig(
                hasCompleted: AppConfigManager.HasCompletedInitialSplash,
                alwaysPlay: ToggleAlwaysPlaySplash.IsOn
            );
            OnLogReceived($"[Presentation] 每次启动播放动效已设为: {(ToggleAlwaysPlaySplash.IsOn ? "开启" : "关闭")}");
        }
    }

    private void OnPreviewSplashClick(object sender, RoutedEventArgs e)
    {
        TriggerSplashScreen(isPreview: true);
    }

    #endregion
}
