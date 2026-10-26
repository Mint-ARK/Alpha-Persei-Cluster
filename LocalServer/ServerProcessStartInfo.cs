using System.Collections.Generic;
using System.Diagnostics;
using System.Text;

namespace LocalServer;

internal static class ServerProcessStartInfo
{
    // Keep values as Unicode arguments; ProcessStartInfo handles Windows quoting.
    internal static ProcessStartInfo Create(string python, string mainPy, string directory,
                                           IEnumerable<string>? arguments = null)
    {
        var start = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = directory,
            CreateNoWindow = true,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        start.ArgumentList.Add("-B");
        start.ArgumentList.Add("-X");
        start.ArgumentList.Add("utf8");
        start.ArgumentList.Add(mainPy);
        if (arguments is not null)
            foreach (string argument in arguments) start.ArgumentList.Add(argument);
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        start.Environment["PYTHONUTF8"] = "1";
        return start;
    }
}
