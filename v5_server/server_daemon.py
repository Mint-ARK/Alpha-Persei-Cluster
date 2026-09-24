# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import time

# 强制 UTF-8 编码支持
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

v5_dir = os.path.dirname(os.path.abspath(__file__))

cmd = [
    sys.executable,
    "-u",
    "-X", "utf8",
    os.path.join(v5_dir, "main.py"),
    "--https-port", "443",
    "--gw-port", "8102",
    "--game-port", "8105",
    "--res-version", "229",
    "--db", "account.db"
]

print("=" * 64, flush=True)
print("  [*] 深空之眼 V5 常驻守护引擎 (Auto-Restart Supervisor) 启动 [UTF-8 已启用]", flush=True)
print("  - 目标工作目录: " + v5_dir, flush=True)
print("  - 执行启动命令: " + " ".join(cmd), flush=True)
print("=" * 64, flush=True)

child_env = os.environ.copy()
child_env["PYTHONIOENCODING"] = "utf-8"
child_env["PYTHONUTF8"] = "1"

while True:
    try:
        proc = subprocess.Popen(cmd, cwd=v5_dir, env=child_env)
        print(f"[V5 Supervisor] 服务器子进程已上线 (PID: {proc.pid})", flush=True)
        ret = proc.wait()
        print(f"[V5 Supervisor] 服务器子进程结束 (ExitCode: {ret})，正在 1 秒内自动无缝拉起...", flush=True)
        time.sleep(1)
    except KeyboardInterrupt:
        print("[V5 Supervisor] 守护引擎收到终止指令，正常关闭。", flush=True)
        try:
            proc.terminate()
        except Exception:
            pass
        break
    except Exception as e:
        print(f"[V5 Supervisor] 捕获守护异常: {e}，正在重新拉起...", flush=True)
        time.sleep(1)
