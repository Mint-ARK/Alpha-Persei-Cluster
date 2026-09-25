using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using Windows.ApplicationModel;
using Windows.ApplicationModel.Activation;
using Windows.Foundation;
using Windows.Foundation.Collections;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;

namespace LocalServer;

public partial class App : Application
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBox(IntPtr hWnd, string lpText, string lpCaption, uint uType);

    public static Window? MainWindowInstance { get; private set; }

    public App()
    {
        AppDomain.CurrentDomain.UnhandledException += (s, e) =>
        {
            if (e.ExceptionObject is Exception ex)
            {
                RecordFatalCrash("AppDomain.UnhandledException", ex);
            }
        };

        TaskScheduler.UnobservedTaskException += (s, e) =>
        {
            RecordFatalCrash("TaskScheduler.UnobservedTaskException", e.Exception);
            e.SetObserved();
        };

        this.UnhandledException += (s, e) =>
        {
            RecordFatalCrash("Application.UnhandledException", e.Exception);
            e.Handled = true;
        };

        try
        {
            InitializeComponent();
        }
        catch (Exception ex)
        {
            RecordFatalCrash("InitializeComponent", ex);
            throw;
        }
    }

    protected override void OnLaunched(Microsoft.UI.Xaml.LaunchActivatedEventArgs args)
    {
        try
        {
            var window = new MainWindow();
            MainWindowInstance = window;
            window.Activate();
        }
        catch (Exception ex)
        {
            RecordFatalCrash("OnLaunched", ex);
            throw;
        }
    }

    private static void RecordFatalCrash(string source, Exception ex)
    {
        string message = $"[{source}] 应用程序启动异常:\n\n{ex.GetType().FullName}: {ex.Message}\n\n堆栈详情:\n{ex.StackTrace}";
        if (ex.InnerException != null)
        {
            message += $"\n\n内部异常:\n{ex.InnerException.GetType().FullName}: {ex.InnerException.Message}\n{ex.InnerException.StackTrace}";
        }

        try
        {
            string logPath = Path.Combine(AppContext.BaseDirectory, "startup_crash.log");
            File.AppendAllText(logPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}\n\n");
        }
        catch { }

        try
        {
            string localAppDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalServer");
            Directory.CreateDirectory(localAppDir);
            File.AppendAllText(Path.Combine(localAppDir, "startup_crash.log"), $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}\n\n");
        }
        catch { }

        try
        {
            MessageBox(IntPtr.Zero, message, "Local Server 启动致命错误", 0x00000010 /* MB_ICONERROR */);
        }
        catch { }
    }
}

