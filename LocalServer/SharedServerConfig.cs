using System;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace LocalServer;

internal static class SharedServerConfig
{
    internal static string ConfigPath(string directory) => Path.GetFullPath(
        Environment.GetEnvironmentVariable("AETHERGAZER_SERVER_CONFIG") is { Length: > 0 } selected
            ? selected : Path.Combine(directory, "server_config.json"), directory);

    internal static string? RecommendRoot()
    {
        string system = Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\";
        return DriveInfo.GetDrives().Where(d => d.IsReady && d.DriveType == DriveType.Fixed
            && !d.Name.Equals(system, StringComparison.OrdinalIgnoreCase)
            && !d.Name.StartsWith("C:", StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(d => d.Name.StartsWith("D:", StringComparison.OrdinalIgnoreCase))
            .ThenByDescending(d => d.AvailableFreeSpace)
            .Select(d => Path.Combine(d.Name, "AlphaPersei_CDN_Cache")).FirstOrDefault();
    }

    internal static string AbsoluteRoot(string value, string config)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 4096 || value.Any(char.IsControl))
            throw new ArgumentException("请选择有效的资源根目录");
        return Path.GetFullPath(value.Trim(), Path.GetDirectoryName(config)!);
    }

    internal static async Task SaveAsync(string directory, object patch, string root, bool running)
    {
        string path = ConfigPath(directory);
        root = AbsoluteRoot(root, path);
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(12) };
        JsonObject? state = null;
        try
        {
            using var stateResponse = await http.GetAsync("http://127.0.0.1/api/gm/system/resource_storage");
            stateResponse.EnsureSuccessStatusCode();
            state = JsonNode.Parse(await stateResponse.Content.ReadAsStringAsync())!.AsObject();
        }
        catch (HttpRequestException error) when (!running && error.StatusCode is null
            && error.InnerException is SocketException socket && socket.SocketErrorCode == SocketError.ConnectionRefused) { }
        if (state != null)
        {
            running = true; // Also catches source servers not started by this shell.
            if (state["code"]!.GetValue<int>() != 0) throw new IOException(state["msg"]?.GetValue<string>());
            string actual = state["data"]!["config_path"]!.GetValue<string>();
            if (!Path.GetFullPath(actual).Equals(path, StringComparison.OrdinalIgnoreCase))
                throw new IOException("运行中服务端使用了其他配置文件，请在对应Web面板修改存储位置");
            if (!root.Equals(state["data"]!["root"]!.GetValue<string>(), StringComparison.OrdinalIgnoreCase)
                || state["data"]!["root_selected"]?.GetValue<bool>() == false)
            {
                using var content = new StringContent(JsonSerializer.Serialize(new { root }), Encoding.UTF8, "application/json");
                using var response = await http.PostAsync("http://127.0.0.1/api/gm/system/resource_storage", content);
                response.EnsureSuccessStatusCode();
                var result = JsonNode.Parse(await response.Content.ReadAsStringAsync())!.AsObject();
                if (result["code"]!.GetValue<int>() != 0) throw new IOException(result["msg"]?.GetValue<string>());
                root = result["data"]!["root"]!.GetValue<string>();
            }
        }
        string chosen = root;
        if (!running && new[] { "CDN_CACHE_DIR", "CDN_PC_DIR", "CDN_ANDROID_DIR", "CDN_IOS_DIR" }
            .Any(key => !string.IsNullOrEmpty(Environment.GetEnvironmentVariable(key))))
            throw new IOException("当前由CDN环境变量覆盖目录，请移除覆盖后再修改共享配置");
        await Task.Run(() => SaveMerged(path, patch, chosen, !running));
    }

    internal static void SaveMerged(string path, object patch, string root, bool prepareStorage)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        using var lease = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
        if (lease.Length == 0) { lease.WriteByte(0); lease.Flush(true); }
        DateTime until = DateTime.UtcNow.AddSeconds(5);
        while (true)
        {
            try { lease.Lock(0, 1); break; }
            catch (IOException) when (DateTime.UtcNow < until) { Thread.Sleep(50); }
        }
        try
        {
            JsonObject data = File.Exists(path) ? JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8))!.AsObject() : new();
            JsonObject storage = data["storage"] as JsonObject ?? new();
            if (data["storage"] is not null && data["storage"] is not JsonObject)
                throw new IOException("存储配置格式无效，未覆盖原文件");
            if (prepareStorage)
            {
                foreach (string platform in new[] { "pc", "android", "ios" })
                {
                    string target = Path.Combine(root, platform);
                    Directory.CreateDirectory(target);
                    using var check = new FileStream(Path.Combine(target, ".cdn-write-test-" + Guid.NewGuid().ToString("N")), FileMode.CreateNew, FileAccess.ReadWrite);
                    check.WriteByte(1);
                    check.Flush(true);
                }
                JsonArray history = storage["cdn_read_roots"] as JsonArray ?? new();
                if (storage["cdn_read_roots"] is not null && storage["cdn_read_roots"] is not JsonArray)
                    throw new IOException("历史缓存目录格式无效");
                string? before = storage["cdn_cache_dir"]?.GetValue<string>();
                if (!string.IsNullOrWhiteSpace(before))
                {
                    before = AbsoluteRoot(before, path);
                    if (!before.Equals(root, StringComparison.OrdinalIgnoreCase)
                        && !history.Any(item => item?.GetValue<string>()?.Equals(before, StringComparison.OrdinalIgnoreCase) == true))
                        history.Add(before);
                }
                storage["cdn_read_roots"] = history;
                storage["cdn_cache_dir"] = root;
                storage["cdn_root_selected"] = true;
                data["storage"] = storage;
            }
            Merge(data, JsonSerializer.SerializeToNode(patch)!.AsObject());
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".part";
            byte[] raw = Encoding.UTF8.GetBytes(data.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            { output.Write(raw); output.Flush(true); }
            if (File.Exists(path)) File.Copy(path, path + ".before_publish_" + Guid.NewGuid().ToString("N"));
            File.Move(temporary, path, true);
        }
        finally { lease.Unlock(0, 1); }
    }

    private static void Merge(JsonObject target, JsonObject patch)
    {
        foreach (var pair in patch)
        {
            if (pair.Value is JsonObject child && target[pair.Key] is JsonObject existing) Merge(existing, child);
            else target[pair.Key] = pair.Value?.DeepClone();
        }
    }
}
