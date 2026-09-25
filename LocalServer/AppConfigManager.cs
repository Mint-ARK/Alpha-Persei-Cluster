using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace LocalServer;

public enum CloseActionPreference
{
    Ask,
    KillServer,
    KeepServer
}

public static class AppConfigManager
{
    private static CloseActionPreference _closeAction = CloseActionPreference.Ask;
    public static CloseActionPreference CloseAction
    {
        get => _closeAction;
        set => _closeAction = value;
    }

    private static bool _hasCompletedInitialSplash = false;
    public static bool HasCompletedInitialSplash
    {
        get => _hasCompletedInitialSplash;
        set => _hasCompletedInitialSplash = value;
    }

    private static bool _alwaysPlaySplash = false;
    public static bool AlwaysPlaySplash
    {
        get => _alwaysPlaySplash;
        set => _alwaysPlaySplash = value;
    }

    public static string LocalAppDataDir
    {
        get
        {
            string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalServer");
            Directory.CreateDirectory(dir);
            return dir;
        }
    }

    public static string ConfigFilePath => Path.Combine(LocalAppDataDir, "server_config.json");
    public static string StateFilePath => Path.Combine(LocalAppDataDir, "server_state.json");

    public static void LoadLifecycleConfig()
    {
        try
        {
            string? srvDir = ServerSupervisor.Instance.FindServerDir();
            string legacyConfig = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TwilightStation", "server_config.json");
            string[] candidates = [
                ConfigFilePath,
                legacyConfig,
                !string.IsNullOrEmpty(srvDir) ? Path.Combine(srvDir, "server_config.json") : ""
            ];

            foreach (var path in candidates)
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) continue;
                string json = File.ReadAllText(path);
                using var doc = JsonDocument.Parse(json);
                if (doc.RootElement.TryGetProperty("lifecycle", out var lcElem) &&
                    lcElem.TryGetProperty("on_close_action", out var actionElem))
                {
                    string action = actionElem.GetString()?.ToLowerInvariant() ?? "";
                    if (action == "kill") _closeAction = CloseActionPreference.KillServer;
                    else if (action == "keep") _closeAction = CloseActionPreference.KeepServer;
                    else _closeAction = CloseActionPreference.Ask;
                    return;
                }
            }
        }
        catch { }
    }

    public static void SaveCloseAction(CloseActionPreference action)
    {
        _closeAction = action;
        try
        {
            string path = ConfigFilePath;
            JsonObject root;
            if (File.Exists(path))
            {
                try
                {
                    string existingJson = File.ReadAllText(path);
                    root = JsonNode.Parse(existingJson)?.AsObject() ?? new JsonObject();
                }
                catch
                {
                    root = new JsonObject();
                }
            }
            else
            {
                root = new JsonObject();
            }

            string actionStr = action switch
            {
                CloseActionPreference.KillServer => "kill",
                CloseActionPreference.KeepServer => "keep",
                _ => "ask"
            };

            var lifecycle = root["lifecycle"] as JsonObject ?? new JsonObject();
            lifecycle["on_close_action"] = actionStr;
            root["lifecycle"] = lifecycle;

            File.WriteAllText(path, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        }
        catch { }
    }

    public static void LoadPresentationConfig()
    {
        try
        {
            string? srvDir = ServerSupervisor.Instance.FindServerDir();
            string legacyConfig = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TwilightStation", "server_config.json");
            string[] candidates = [
                ConfigFilePath,
                legacyConfig,
                !string.IsNullOrEmpty(srvDir) ? Path.Combine(srvDir, "server_config.json") : ""
            ];

            foreach (var path in candidates)
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) continue;
                string json = File.ReadAllText(path);
                using var doc = JsonDocument.Parse(json);
                if (doc.RootElement.TryGetProperty("presentation", out var presElem))
                {
                    if (presElem.TryGetProperty("has_completed_initial_splash", out var hasElem))
                    {
                        _hasCompletedInitialSplash = hasElem.GetBoolean();
                    }
                    if (presElem.TryGetProperty("always_play_splash", out var alwaysElem))
                    {
                        _alwaysPlaySplash = alwaysElem.GetBoolean();
                    }
                    return;
                }
            }
        }
        catch { }
    }

    public static void SavePresentationConfig(bool hasCompleted, bool alwaysPlay)
    {
        _hasCompletedInitialSplash = hasCompleted;
        _alwaysPlaySplash = alwaysPlay;
        try
        {
            string path = ConfigFilePath;
            JsonObject root;
            if (File.Exists(path))
            {
                try
                {
                    string existingJson = File.ReadAllText(path);
                    root = JsonNode.Parse(existingJson)?.AsObject() ?? new JsonObject();
                }
                catch
                {
                    root = new JsonObject();
                }
            }
            else
            {
                root = new JsonObject();
            }

            var presentation = root["presentation"] as JsonObject ?? new JsonObject();
            presentation["has_completed_initial_splash"] = hasCompleted;
            presentation["always_play_splash"] = alwaysPlay;
            root["presentation"] = presentation;

            File.WriteAllText(path, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        }
        catch { }
    }

    public static void SaveServerState(int pid, string serverDir)
    {
        try
        {
            var node = new JsonObject
            {
                ["pid"] = pid,
                ["server_dir"] = serverDir,
                ["start_time"] = DateTime.Now.ToString("o")
            };
            File.WriteAllText(StateFilePath, node.ToJsonString());
        }
        catch { }
    }

    public static (int? pid, string? serverDir) LoadServerState()
    {
        try
        {
            string[] paths = [
                StateFilePath,
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TwilightStation", "server_state.json")
            ];
            foreach (var statePath in paths)
            {
                if (File.Exists(statePath))
                {
                    string json = File.ReadAllText(statePath);
                    using var doc = JsonDocument.Parse(json);
                    int? pid = doc.RootElement.TryGetProperty("pid", out var pe) ? pe.GetInt32() : null;
                    string? srvDir = doc.RootElement.TryGetProperty("server_dir", out var se) ? se.GetString() : null;
                    if (pid.HasValue) return (pid, srvDir);
                }
            }
        }
        catch { }
        return (null, null);
    }

    public static void ClearServerState()
    {
        try
        {
            if (File.Exists(StateFilePath))
            {
                File.Delete(StateFilePath);
            }
            string legacyState = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TwilightStation", "server_state.json");
            if (File.Exists(legacyState))
            {
                File.Delete(legacyState);
            }
        }
        catch { }
    }
}
