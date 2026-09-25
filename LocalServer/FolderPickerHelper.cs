using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace LocalServer;

/// <summary>
/// 原生 Win32 COM 文件/文件夹选取器 (彻底解决 UAC 管理员提权模式下 WinRT FolderPicker 被阻断静默失败的痛点)
/// </summary>
public static class FolderPickerHelper
{
    public static string? PickFolder(IntPtr hwnd, string? title = null)
    {
        try
        {
            var dialog = (IFileOpenDialog)new FileOpenDialogRCW();
            dialog.GetOptions(out uint options);
            // 0x20 = FOS_PICKFOLDERS (仅选目录)
            // 0x40 = FOS_FORCEFILESYSTEM (仅物理文件系统)
            dialog.SetOptions(options | 0x20 | 0x40);

            if (!string.IsNullOrEmpty(title))
            {
                dialog.SetTitle(title);
            }

            int hr = dialog.Show(hwnd);
            if (hr == 0) // S_OK: 用户点击了确认选择
            {
                dialog.GetResult(out IShellItem item);
                item.GetDisplayName(0x80058000, out string path); // SIGDN_FILESYSPATH (完整物理路径)
                return path;
            }

            return null; // 用户取消选择
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[FolderPickerHelper] COM 选取器异常: {ex.Message}，尝试回退模式");
            return PickFolderFallback(title);
        }
    }

    private static string? PickFolderFallback(string? title)
    {
        try
        {
            string escapedTitle = (title ?? "请选择文件夹").Replace("'", "''");
            string script = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description = '" + escapedTitle + "'; if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::WriteLine($f.SelectedPath) }";
            var psi = new ProcessStartInfo
            {
                FileName = "powershell",
                Arguments = "-NoProfile -Command \"" + script + "\"",
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using var p = Process.Start(psi);
            string? outPath = p?.StandardOutput.ReadToEnd()?.Trim();
            p?.WaitForExit(4000);
            return string.IsNullOrWhiteSpace(outPath) ? null : outPath;
        }
        catch
        {
            return null;
        }
    }

    [ComImport]
    [Guid("d57c7288-d4ad-4768-be02-9d969532d960")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileOpenDialog
    {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
        void SetFileTypeIndex(uint iFileType);
        void GetFileTypeIndex(out uint piFileType);
        void Advise(IntPtr pfde, out uint pdwCookie);
        void Unadvise(uint dwCookie);
        void SetOptions(uint fos);
        void GetOptions(out uint pfos);
        void SetDefaultFolder(IShellItem psi);
        void SetFolder(IShellItem psi);
        void GetFolder(out IShellItem ppsi);
        void GetCurrentSelection(out IShellItem ppsi);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
        void GetResult(out IShellItem ppsi);
        void AddPlace(IShellItem psi, int fdap);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
        void Close([MarshalAs(UnmanagedType.Error)] int hr);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr pFilter);
        void GetResults(out IntPtr ppenum);
        void GetSelectedItems(out IntPtr ppsai);
    }

    [ComImport]
    [Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem
    {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem ppsi);
        void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
        void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
        void Compare(IShellItem psi, uint hint, out int piOrder);
    }

    [ComImport]
    [Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    [ClassInterface(ClassInterfaceType.None)]
    private class FileOpenDialogRCW { }
}
