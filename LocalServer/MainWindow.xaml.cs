using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using Windows.Graphics;

namespace LocalServer;

public sealed partial class MainWindow : Window
{
    private bool _isExplicitExit = false;
    private bool _isDialogShowing = false;
    private readonly SubclassProc _subclassProc;

    [DllImport("comctl32.dll", SetLastError = true)]
    private static extern bool SetWindowSubclass(IntPtr hWnd, SubclassProc pfnSubclass, UIntPtr uIdSubclass, IntPtr dwRefData);

    [DllImport("comctl32.dll", SetLastError = true)]
    private static extern IntPtr DefSubclassProc(IntPtr hWnd, uint uMsg, IntPtr wParam, IntPtr lParam);

    private delegate IntPtr SubclassProc(IntPtr hWnd, uint uMsg, IntPtr wParam, IntPtr lParam, UIntPtr uIdSubclass, IntPtr dwRefData);
    private const uint WM_CLOSE = 0x0010;

    public MainWindow()
    {
        InitializeComponent();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        Title = "Local Server";

        // 1. 设置任务栏、Alt+Tab 与窗口高清图标
        try
        {
            var appWindow = this.AppWindow;
            string iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico");
            if (File.Exists(iconPath))
            {
                appWindow.SetIcon(iconPath);
            }
        }
        catch { }

        // 2. 设置标准工作台窗口尺寸并居中
        try
        {
            var appWindow = this.AppWindow;
            int width = 1280;
            int height = 820;
            appWindow.Resize(new SizeInt32(width, height));

            if (DisplayArea.GetFromWindowId(appWindow.Id, DisplayAreaFallback.Primary) is DisplayArea displayArea)
            {
                var centeredPosition = appWindow.Position;
                centeredPosition.X = Math.Max(0, (displayArea.WorkArea.Width - width) / 2);
                centeredPosition.Y = Math.Max(0, (displayArea.WorkArea.Height - height) / 2);
                appWindow.Move(centeredPosition);
            }
        }
        catch { }

        // 3. 安装 Win32 原生 WM_CLOSE 消息拦截钩子（绝对保证 100% 拦截关闭并弹窗）
        _subclassProc = new SubclassProc(WindowSubclassHandler);
        try
        {
            IntPtr hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
            SetWindowSubclass(hwnd, _subclassProc, (UIntPtr)1001, IntPtr.Zero);
        }
        catch { }

        // 4. AppWindow.Closing 双保险
        try
        {
            this.AppWindow.Closing += (sender, args) =>
            {
                if (!_isExplicitExit)
                {
                    args.Cancel = true;
                    DispatcherQueue.TryEnqueue(async () =>
                    {
                        await HandleWindowCloseRequestAsync();
                    });
                }
            };
        }
        catch { }

        // 加载生命周期与启动呈现偏好配置
        AppConfigManager.LoadLifecycleConfig();
        AppConfigManager.LoadPresentationConfig();

        RootFrame.Navigate(typeof(MainPage));

        Closed += MainWindow_Closed;
    }

    private IntPtr WindowSubclassHandler(IntPtr hWnd, uint uMsg, IntPtr wParam, IntPtr lParam, UIntPtr uIdSubclass, IntPtr dwRefData)
    {
        if (uMsg == WM_CLOSE)
        {
            if (_isExplicitExit)
            {
                return DefSubclassProc(hWnd, uMsg, wParam, lParam);
            }

            // 拦截关闭消息，异步转交生命周期管理逻辑
            DispatcherQueue.TryEnqueue(async () =>
            {
                await HandleWindowCloseRequestAsync();
            });

            return IntPtr.Zero; // 拦截并阻止默认关闭
        }

        return DefSubclassProc(hWnd, uMsg, wParam, lParam);
    }

    private async Task HandleWindowCloseRequestAsync()
    {
        if (_isDialogShowing) return;

        bool isServerRunning = ServerSupervisor.Instance.IsRunning;
        var pref = AppConfigManager.CloseAction;

        // 若服务在运行，且用户已配置了直接执行策略（非每次询问）
        if (isServerRunning)
        {
            if (pref == CloseActionPreference.KillServer)
            {
                ServerSupervisor.Instance.StopServer();
                _isExplicitExit = true;
                this.Close();
                return;
            }
            else if (pref == CloseActionPreference.KeepServer)
            {
                ServerSupervisor.Instance.DetachServer();
                _isExplicitExit = true;
                this.Close();
                return;
            }
        }

        _isDialogShowing = true;
        try
        {
            await ShowCloseConfirmDialogAsync(isServerRunning);
        }
        finally
        {
            _isDialogShowing = false;
        }
    }

    private async Task ShowCloseConfirmDialogAsync(bool isServerRunning)
    {
        var xamlRoot = this.Content?.XamlRoot ?? RootFrame.XamlRoot;
        if (xamlRoot == null)
        {
            // 若 XamlRoot 尚未挂载，延迟重试
            await Task.Delay(100);
            xamlRoot = this.Content?.XamlRoot ?? RootFrame.XamlRoot;
            if (xamlRoot == null) return;
        }

        var rememberCheckBox = new CheckBox
        {
            Content = "记住我的选择，以后不再询问 (可在「环境配置」中修改)",
            IsChecked = false,
            Margin = new Thickness(0, 14, 0, 0),
            FontSize = 12
        };

        var panel = new StackPanel { Spacing = 8 };
        ContentDialog dialog;

        if (isServerRunning)
        {
            panel.Children.Add(new TextBlock
            {
                Text = "检测到底层服务端守护进程当前处于活动状态。\n请选择关闭 Local Server 宿主时的操作策略：",
                TextWrapping = TextWrapping.Wrap,
                FontSize = 13
            });
            panel.Children.Add(new TextBlock
            {
                Text = "• 终止服务：发送终止信号并完全回收进程树，释放所有网络端口。\n• 托管脱钩：解除宿主界面监控，保留后台守护进程独立持续运行。",
                TextWrapping = TextWrapping.Wrap,
                FontSize = 12,
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["TextFillColorSecondaryBrush"]
            });
            panel.Children.Add(rememberCheckBox);

            dialog = new ContentDialog
            {
                Title = "守护进程生命周期策略",
                Content = panel,
                PrimaryButtonText = "终止服务并退出",
                SecondaryButtonText = "脱离托管并在后台保持运行",
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = xamlRoot
            };
        }
        else
        {
            panel.Children.Add(new TextBlock
            {
                Text = "确定要退出 Local Server 管理宿主吗？",
                TextWrapping = TextWrapping.Wrap,
                FontSize = 13
            });
            panel.Children.Add(rememberCheckBox);

            dialog = new ContentDialog
            {
                Title = "退出程序",
                Content = panel,
                PrimaryButtonText = "确认退出",
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = xamlRoot
            };
        }

        var result = await dialog.ShowAsync();

        if (isServerRunning)
        {
            if (result == ContentDialogResult.Primary)
            {
                if (rememberCheckBox.IsChecked == true)
                {
                    AppConfigManager.SaveCloseAction(CloseActionPreference.KillServer);
                }
                ServerSupervisor.Instance.StopServer();
                _isExplicitExit = true;
                this.Close();
            }
            else if (result == ContentDialogResult.Secondary)
            {
                if (rememberCheckBox.IsChecked == true)
                {
                    AppConfigManager.SaveCloseAction(CloseActionPreference.KeepServer);
                }
                ServerSupervisor.Instance.DetachServer();
                _isExplicitExit = true;
                this.Close();
            }
            // 取消则不执行退出，窗口完好保留
        }
        else
        {
            if (result == ContentDialogResult.Primary)
            {
                _isExplicitExit = true;
                this.Close();
            }
            // 取消则不执行退出，窗口完好保留
        }
    }

    private void MainWindow_Closed(object sender, WindowEventArgs args)
    {
        // 兜底清理：若非保留后台，停止服务
        if (AppConfigManager.CloseAction == CloseActionPreference.KillServer)
        {
            ServerSupervisor.Instance.StopServer();
        }
    }
}
