using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;

namespace LocalServer;

public sealed class CertificateFilesStatus
{
    [JsonPropertyName("ready")] public bool Ready { get; set; }
    [JsonPropertyName("root_usable")] public bool RootUsable { get; set; }
    [JsonPropertyName("requires_root_replacement")] public bool RequiresRootReplacement { get; set; }
    [JsonPropertyName("needs_leaf_renewal")] public bool NeedsLeafRenewal { get; set; }
    [JsonPropertyName("needs_distribution_sync")] public bool NeedsDistributionSync { get; set; }
    [JsonPropertyName("root_thumbprint")] public string RootThumbprint { get; set; } = "";
    [JsonPropertyName("root_not_after")] public string RootNotAfter { get; set; } = "";
    [JsonPropertyName("leaf_not_after")] public string LeafNotAfter { get; set; } = "";
    [JsonPropertyName("issues")] public List<string> Issues { get; set; } = new();
}

public sealed class CertificateStatus
{
    public CertificateFilesStatus Files { get; init; } = new();
    public bool InCurrentUser { get; set; }
    public bool InLocalMachine { get; set; }
    public bool ChainValid { get; set; }
    public List<string> Issues { get; } = new();
    public bool RootTrusted => InCurrentUser || InLocalMachine;
    public bool Ready => Files.Ready && RootTrusted && ChainValid;
    public string Badge => Ready ? "🟢 游戏握手证书就绪" :
        Files.RequiresRootReplacement ? "⚠️ 根证书需重新部署" :
        !RootTrusted ? "⚠️ 根证书不匹配，需重新部署" : "⚠️ 服务证书需重新部署";
}

public sealed record CertificateCommand(int ExitCode, string Output, string Error);

/// <summary>共享 Python 文件校验与 Windows 原生信任校验；只在用户点击部署时安装根。</summary>
public static class CertificateManager
{
    public static async Task<CertificateCommand> RunGeneratorAsync(string python, string directory, params string[] arguments)
    {
        var start = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = directory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        start.ArgumentList.Add("-B");
        start.ArgumentList.Add(Path.Combine(directory, "gen_cert.py"));
        foreach (string argument in arguments) start.ArgumentList.Add(argument);
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        using var process = Process.Start(start) ?? throw new InvalidOperationException("无法启动证书工具。");
        Task<string> output = process.StandardOutput.ReadToEndAsync();
        Task<string> error = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            await process.WaitForExitAsync();
            throw new TimeoutException("证书检查超过 30 秒，请检查本项目 Python 运行时。");
        }
        return new CertificateCommand(process.ExitCode, await output, await error);
    }

    public static async Task<CertificateStatus> InspectAsync(string python, string directory)
    {
        CertificateCommand command = await RunGeneratorAsync(python, directory, "--check", "--json");
        var files = JsonSerializer.Deserialize<CertificateFilesStatus>(command.Output)
            ?? throw new InvalidOperationException($"无法读取证书检查结果：{command.Error}");
        return EvaluateWindowsTrust(directory, files);
    }

    public static CertificateStatus EvaluateWindowsTrust(string directory, CertificateFilesStatus files)
    {
        var result = new CertificateStatus { Files = files };
        result.Issues.AddRange(files.Issues);
        try
        {
            using var root = X509CertificateLoader.LoadCertificateFromFile(Path.Combine(directory, "sdk_ca.crt"));
            foreach (StoreLocation location in new[] { StoreLocation.CurrentUser, StoreLocation.LocalMachine })
            {
                try
                {
                    bool installed = IsRootInstalled(root.Thumbprint, location);
                    if (location == StoreLocation.CurrentUser) result.InCurrentUser = installed;
                    else result.InLocalMachine = installed;
                }
                catch (Exception error)
                {
                    result.Issues.Add($"读取 {location} 根证书库失败：{error.Message}");
                }
            }
            if (!result.RootTrusted)
                result.Issues.Add("当前根证书指纹未在 Windows 受信任根库中注册，请点击重新部署并安装根证书。");
            if (!files.RootUsable || files.NeedsLeafRenewal) return result;
            using var leaf = X509CertificateLoader.LoadCertificateFromFile(Path.Combine(directory, "sdk_cert.pem"));
            using var chain = new X509Chain();
            chain.ChainPolicy.ExtraStore.Add(root);
            chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
            chain.ChainPolicy.DisableCertificateDownloads = true;
            chain.ChainPolicy.ApplicationPolicy.Add(new Oid("1.3.6.1.5.5.7.3.1"));
            result.ChainValid = chain.Build(leaf) &&
                chain.ChainElements[^1].Certificate.Thumbprint.Equals(root.Thumbprint, StringComparison.OrdinalIgnoreCase);
            if (!result.ChainValid)
                result.Issues.Add("Windows 未通过当前服务证书链校验，请重新部署证书。" +
                    string.Join(", ", Array.ConvertAll(chain.ChainStatus, entry => entry.Status.ToString())));
        }
        catch (Exception error)
        {
            result.Issues.Add("Windows 证书检查失败：" + error.Message);
        }
        return result;
    }

    public static bool IsRootInstalled(string thumbprint, StoreLocation location)
    {
        using var store = new X509Store(StoreName.Root, location);
        store.Open(OpenFlags.ReadOnly);
        return store.Certificates.Find(X509FindType.FindByThumbprint, thumbprint, validOnly: true).Count > 0;
    }

    public static void InstallCurrentUserRoot(string directory)
    {
        using var root = X509CertificateLoader.LoadCertificateFromFile(Path.Combine(directory, "sdk_ca.crt"));
        using var store = new X509Store(StoreName.Root, StoreLocation.CurrentUser);
        store.Open(OpenFlags.ReadWrite);
        store.Add(root);
        if (!IsRootInstalled(root.Thumbprint, StoreLocation.CurrentUser))
            throw new InvalidOperationException("当前用户根证书导入后指纹复核失败。");
    }

    public static async Task<int> InstallMachineRootAsync(string directory)
    {
        using var root = X509CertificateLoader.LoadCertificateFromFile(Path.Combine(directory, "sdk_ca.crt"));
        if (IsRootInstalled(root.Thumbprint, StoreLocation.LocalMachine)) return 0;
        var start = new ProcessStartInfo
        {
            FileName = "certutil.exe",
            Arguments = $"-addstore -f \"Root\" \"{Path.Combine(directory, "sdk_ca.crt")}\"",
            WorkingDirectory = directory,
            UseShellExecute = true,
            Verb = "runas",
            WindowStyle = ProcessWindowStyle.Hidden,
        };
        using var process = Process.Start(start) ?? throw new InvalidOperationException("无法启动系统根证书安装。");
        await process.WaitForExitAsync();
        if (process.ExitCode == 0 && !IsRootInstalled(root.Thumbprint, StoreLocation.LocalMachine))
            throw new InvalidOperationException("计算机根证书导入后指纹复核失败。");
        return process.ExitCode;
    }
}
