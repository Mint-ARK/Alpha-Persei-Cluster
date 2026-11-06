#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《深空之眼》离线单机服务全量发布打包工具 (Master Distribution Builder)
======================================================================
本脚本用于构建最终发布的两个官方标准形态：
1. 绿色免安装便携版 (Portable ZIP)：解压即用，内置 WinUI 3 壳程序 + 嵌入式 Python 运行时 + 精简 V5 仿真服务端。
2. Inno Setup 安装包 (Setup.exe)：标准 Windows 应用程序生命周期，支持开始菜单/桌面快捷方式、一键信任根证书、安装即用、卸载彻底清理脏数据。
"""

import os
import sys
import shutil
import tempfile
import uuid
import zipfile
import subprocess
import time
from pathlib import Path

# 导入发布包生命周期管理
try:
    from .package_lifecycle import publish_package_to_debug_output, clean_workspace_dist, recycle_or_raise, vacuum_database
    from .package_contract import collect_runtime_assets, stage_runtime_assets, seal_runtime, validate_archive, validate_inventory, validate_installer_template
    from .windows_launchers import write_console_launcher
except ImportError:
    from package_lifecycle import publish_package_to_debug_output, clean_workspace_dist, recycle_or_raise, vacuum_database
    from package_contract import collect_runtime_assets, stage_runtime_assets, seal_runtime, validate_archive, validate_inventory, validate_installer_template
    from windows_launchers import write_console_launcher

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
V5_SERVER = PROJECT_ROOT / "v5_server"
DIST_DIR = Path(tempfile.gettempdir()) / ("V5FullDistribution_" + uuid.uuid4().hex)
STAGING_DIR = DIST_DIR / "_staging_full_distribution"

SHELL_PUBLISH_DIR = PROJECT_ROOT / "LocalServer" / "bin" / "Release" / "net10.0-windows10.0.26100.0" / "win-x64" / "publish"
SHELL_FALLBACK_DIR = PROJECT_ROOT / "LocalServer" / "bin" / "x64" / "Release" / "net10.0-windows10.0.26100.0" / "win-x64" / "publish"

EMBEDDED_PYTHON_CANDIDATES = [
    Path(r"C:\Users\Suzur\Desktop\项目归档\发布包与部署产物\AetherGazer_Server_v5.0\runtime\python"),
    Path(r"C:\Users\Suzur\Desktop\AetherGazer_Server_v5.0\runtime\python"),
    PROJECT_ROOT / "runtime" / "python",
]
EMBEDDED_PYTHON_DIR = next((p for p in EMBEDDED_PYTHON_CANDIDATES if p.exists() and p.is_dir()), EMBEDDED_PYTHON_CANDIDATES[0])

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / r"Programs\Inno Setup 6\ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]

def find_iscc() -> Path | None:
    for candidate in ISCC_CANDIDATES:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def copy_tree_without_junk(src_dir: Path, dst_dir: Path):
    """递归拷贝目录并排除开发垃圾（__pycache__, .git, 临时文件等）"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".vscode", "node_modules", ".test_runs")]
        rel = Path(root).relative_to(src_dir)
        cur_dst = dst_dir / rel
        cur_dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f.endswith((".pyc", ".log", ".tmp", ".pdb")) or ".before_" in f:
                continue
            shutil.copy2(os.path.join(root, f), cur_dst / f)


def stage_clean_v5_server(target_v5_dir: Path, plan=None, python_executable=sys.executable):
    """与后端 ZIP 共用资源合同，完整验证后才允许构建分发包。"""
    plan = plan if plan is not None else collect_runtime_assets(PROJECT_ROOT)
    src_db = V5_SERVER / "account.db"
    if not src_db.is_file():
        raise FileNotFoundError("缺少 account.db，停止封包")
    target_v5_dir.mkdir(parents=True, exist_ok=True)
    stage_runtime_assets(target_v5_dir, plan)
    dst_db = target_v5_dir / "account.db"
    vacuum_database(src_db, dst_db)

    # 8. 生成服务端专用启动批处理
    write_console_launcher(target_v5_dir / "run_server.bat")
    return seal_runtime(target_v5_dir, plan, python_executable)


def stage_shell_and_runtime(staging_root: Path):
    """拷贝 WinUI 3 壳程序与嵌入式 Python 运行时"""
    # 1. 拷贝 WinUI 壳程序
    shell_src = SHELL_PUBLISH_DIR if SHELL_PUBLISH_DIR.exists() else SHELL_FALLBACK_DIR
    print(f"[*] 正在从 [{shell_src}] 抽取 WinUI 3 壳程序产物...")
    filtered_lang_cnt = 0
    for item in os.listdir(shell_src):
        if item in ("v5_server", "runtime", "dist", "logs", "__pycache__"):
            continue
        s_path = shell_src / item
        d_path = staging_root / item
        if s_path.is_dir():
            # 过滤多余的多语言资源包（仅保留中文 zh-CN 与英文 en-us）
            if item.lower() not in ("assets", "microsoft.ui.xaml", "zh-cn", "en-us") and "-" in item:
                filtered_lang_cnt += 1
                continue
            copy_tree_without_junk(s_path, d_path)
        else:
            if not item.endswith((".pdb", ".log", ".tmp")):
                shutil.copy2(s_path, d_path)

    if filtered_lang_cnt > 0:
        print(f"[*] 已清理剔除 {filtered_lang_cnt} 个冗余多语言包，仅保留中文 (zh-CN) 与英文 (en-us)")

    # 2. 注入 64 位 VC++ CRT 运行时 DLL，确保在纯净虚拟机环境下 100% 自包含
    vc_runtime_dlls = [
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ]
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    copied_vc_cnt = 0
    for dll_name in vc_runtime_dlls:
        src_dll = sys32 / dll_name
        if src_dll.exists():
            shutil.copy2(src_dll, staging_root / dll_name)
            copied_vc_cnt += 1
    print(f"[*] 已注入 {copied_vc_cnt} 个 App-Local VC++ 核心运行库 DLL (免装 VC Redistributable)")

    # 3. 拷贝嵌入式 Python 运行时
    print(f"[*] 正在拷贝嵌入式 Python 3.14 运行时环境...")
    dst_py = staging_root / "runtime" / "python"
    copy_tree_without_junk(EMBEDDED_PYTHON_DIR, dst_py)


def create_portable_launcher_scripts(staging_root: Path):
    """创建便携版启动脚本与说明文档"""
    # The Python core can also use a native Linux interpreter after extraction.
    (staging_root / 'run_server_linux.sh').write_text(
        '#!/bin/sh\nset -eu\n'
        'cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        'exec sh v5_server/server.sh run "$@"\n', encoding='utf-8', newline='\n')
    # 1. GUI 快捷启动脚本
    gui_bat = """@echo off
cd /d "%~dp0"
start "" "LocalServer.exe"
"""
    with open(staging_root / "启动离线单机服务(GUI界面).bat", "w", encoding="utf-8") as f:
        f.write(gui_bat)

    # 2. 带排错控制台的 GUI 启动入口
    gui_debug_bat = """@echo off
chcp 65001 >nul
title LocalServer 启动诊断终端
cd /d "%~dp0"
echo ======================================================================
echo           Local Server 管理终端启动诊断
echo ======================================================================
echo [*] 正在拉起 LocalServer 进程...
echo.
LocalServer.exe
echo.
echo [*] LocalServer 已退出，退出码: %errorlevel%
echo.
pause
"""
    with open(staging_root / "启动诊断模式(排错专用).bat", "w", encoding="utf-8") as f:
        f.write(gui_debug_bat)

    # 3. 控制台直接启动脚本
    write_console_launcher(staging_root / "启动离线单机服务(控制台).bat", "v5_server")

    # 4. 根目录一键安装证书快捷入口
    cert_bat = """@echo off
cd /d "%~dp0v5_server"
call "一键安装Windows证书.bat"
"""
    with open(staging_root / "一键安装Windows证书.bat", "w", encoding="utf-8") as f:
        f.write(cert_bat)

    # 5. 便携版说明文档
    readme = """======================================================================
《深空之眼》离线单机仿真服务 · 绿色便携版 (v5.0.3)
======================================================================

【极简上手指南】
1. 双击运行根目录下的「LocalServer.exe」（或「启动离线单机服务(GUI界面).bat」）；
2. 首次运行会弹出 UAC 管理员提权，这是正常现象（服务监听 443/80/8102 系统端口及 Hosts 重定向必需）；
3. 在管理面板中点击【启动服务】按钮，即可一键拉起本地网关与业务服务；
4. 证书配置：若游戏端提示网络或安全证书错误，可点击面板右上角证书按钮或运行「一键安装Windows证书.bat」；
5. GM 控制台：可在面板内嵌标签页直接操作，或在浏览器中访问 http://localhost/web/gm_console.html。

【排错指南】
若在极度纯净的系统或虚拟机中直接双击无法弹出界面，可双击「启动诊断模式(排错专用).bat」查看具体诊断错误。

【目录结构】
├── LocalServer.exe              # WinUI 3 现代化桌面管理面板 (主入口)
├── runtime/python/              # 内置自包含 Python 3.14 运行环境 (无需外部安装 Python)
├── v5_server/                   # 离线单机仿真服务端核心、数据库与 Web 静态资源
├── 启动离线单机服务(GUI界面).bat  # 便携启动脚本
├── 启动诊断模式(排错专用).bat    # 故障诊断与启动日志捕获
├── 启动离线单机服务(控制台).bat   # 纯命令行黑框启动脚本 (供纯无界面环境使用)
└── 一键安装Windows证书.bat      # 本地仿真 CA 根证书快速导入工具
======================================================================
"""
    with open(staging_root / "README_绿色版使用说明.txt", "w", encoding="utf-8") as f:
        f.write(readme)


def build_portable_zip(staging_root: Path, output_zip_path: Path, inventory):
    """构建便携版绿色 ZIP 归档"""
    print(f"[*] 正在打包绿色免安装便携版: {output_zip_path.name}...")
    if output_zip_path.exists():
        recycle_or_raise(output_zip_path)

    with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _, files in os.walk(staging_root):
            for file in files:
                abs_p = Path(root) / file
                rel_p = abs_p.relative_to(staging_root)
                zf.write(abs_p, rel_p)

    validate_archive(output_zip_path, inventory, "v5_server")
    zip_mb = output_zip_path.stat().st_size / 1024 / 1024
    print(f"[OK] 绿色免安装版打包完成: {output_zip_path} ({zip_mb:.2f} MB)")


TEMPLATE_ISS_FILE = Path(__file__).resolve().parent / "installer_template.iss"


def generate_inno_setup_script(staging_root: Path, iss_path: Path, output_setup_name: str):
    """生成完全遵从 Windows 应用程序生命周期的 Inno Setup 脚本"""
    source_dir_str = str(staging_root.resolve())
    dist_dir_str = str(DIST_DIR.resolve())

    if not TEMPLATE_ISS_FILE.exists():
        raise FileNotFoundError(f"Inno Setup 模板文件不存在: {TEMPLATE_ISS_FILE}")

    with open(TEMPLATE_ISS_FILE, "r", encoding="utf-8") as f:
        iss_template = f.read()
    validate_installer_template(iss_template)

    iss_content = iss_template.replace("__SOURCE_DIR__", source_dir_str)\
                              .replace("__DIST_DIR__", dist_dir_str)\
                              .replace("__OUTPUT_SETUP_NAME__", output_setup_name)

    with open(iss_path, "w", encoding="utf-8") as f:
        f.write(iss_content)
    print(f"[OK] Inno Setup 脚本已生成: {iss_path}")


def build_inno_installer(iscc_exe: Path, iss_path: Path):
    """调用 ISCC.exe 编译 Inno Setup 安装包"""
    print(f"[*] 正在调用 Inno Setup 编译器 ({iscc_exe.name}) 构建安装包...")
    cmd = [str(iscc_exe), "/Qp", str(iss_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERR] Inno Setup 编译失败！\n{res.stderr or res.stdout}")
        sys.exit(res.returncode)
    print("[OK] Inno Setup 安装包构建成功！")


def main():
    start_time = time.time()
    plan = collect_runtime_assets(PROJECT_ROOT)
    if not (V5_SERVER / "account.db").is_file():
        raise FileNotFoundError("缺少 account.db，停止封包")
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    print("======================================================================")
    print("      《深空之眼》离线单机服务 · 全量发布打包流水线 (Master Build)")
    print("======================================================================")

    # 1. 验证编译工具链
    iscc_exe = find_iscc()
    if not iscc_exe:
        print("[ERR] 未找到 Inno Setup 编译器 (ISCC.exe)！请确认已安装 Inno Setup 6。")
        sys.exit(1)
    print(f"[OK] Inno Setup 编译器就绪: {iscc_exe}")

    # 2. 清理并初始化暂存区
    if STAGING_DIR.exists():
        print("[*] 清理旧暂存区...")
        recycle_or_raise(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    # 3. 组装完整应用载荷
    print("\n[Step 1/5] 正在装配 WinUI 3 壳程序与嵌入式 Python 运行时...")
    stage_shell_and_runtime(STAGING_DIR)

    print("\n[Step 2/5] 正在抽取并紧凑化构建精简 V5 服务端 (WebP 优化 & VACUUM)...")
    inventory = stage_clean_v5_server(
        STAGING_DIR / "v5_server", plan, STAGING_DIR / "runtime" / "python" / "python.exe")

    print("\n[Step 3/5] 正在生成便携版引导脚本与使用说明...")
    create_portable_launcher_scripts(STAGING_DIR)

    # 计算暂存区未压缩体积
    total_uncompressed = sum(f.stat().st_size for f in STAGING_DIR.rglob("*") if f.is_file())
    uncompressed_mb = total_uncompressed / 1024 / 1024
    print(f"  -> 完整应用未压缩展开体积: {uncompressed_mb:.2f} MB")

    # 4. 构建 Package 1: 绿色免安装版 ZIP
    print("\n[Step 4/5] 正在构建【绿色免安装版压缩包】...")
    zip_output_name = "AetherGazer_Server_v5.0.3_Portable.zip"
    zip_path = DIST_DIR / zip_output_name
    build_portable_zip(STAGING_DIR, zip_path, inventory)
    zip_size_mb = zip_path.stat().st_size / 1024 / 1024

    # 等安装包与便携包均构建并校验完成后，再替换调试输出中的旧包。

    # 5. 构建 Package 2: Inno Setup 安装包
    print("\n[Step 5/5] 正在构建【Inno Setup 标准安装包】...")
    iss_file = DIST_DIR / "AetherGazer_Server.iss"
    setup_base_name = "AetherGazer_Server_v5.0.3_Setup"
    generate_inno_setup_script(STAGING_DIR, iss_file, setup_base_name)
    # 安装包使用同一份已经独立验证的载荷，不接受中途增减/修改资源。
    validate_inventory(STAGING_DIR / "v5_server", inventory)
    build_inno_installer(iscc_exe, iss_file)

    setup_exe_path = DIST_DIR / f"{setup_base_name}.exe"
    setup_size_mb = setup_exe_path.stat().st_size / 1024 / 1024 if setup_exe_path.exists() else 0.0

    # 移动安装包至桌面“调试输出”并保留最新包，旧包入回收站
    if not setup_exe_path.is_file() or setup_exe_path.stat().st_size == 0:
        raise RuntimeError("安装包未生成，保留调试输出中的旧包")
    validate_archive(zip_path, inventory, "v5_server")
    final_portable_path = publish_package_to_debug_output(zip_path)
    final_setup_path = publish_package_to_debug_output(setup_exe_path)

    # 6. 清理暂存区与工作区 dist 目录
    print("\n[*] 正在清理临时暂存区...")
    recycle_or_raise(STAGING_DIR)
    clean_workspace_dist(DIST_DIR)

    elapsed = time.time() - start_time

    print("\n======================================================================")
    print("                 [SUCCESS] 发布包全量流水线构建成功！")
    print("======================================================================")
    print(f"1. 绿色免安装版 (Portable ZIP):")
    print(f"   - 产物路径: {final_portable_path}")
    print(f"   - 压缩体积: {zip_size_mb:.2f} MB (较旧版 740MB 缩减约 75%!)")
    print(f"   - 解压即用: 内置完整运行时与精简服务端，双击 LocalServer.exe 即可运行")
    print()
    print(f"2. Inno Setup 标准安装包 (Setup EXE):")
    print(f"   - 产物路径: {final_setup_path}")
    print(f"   - 压缩体积: {setup_size_mb:.2f} MB (高密度 LZMA2 极速安装)")
    print(f"   - 规范特性: 快捷方式创建、一键证书信任、服从 Windows 应用程序生命周期、卸载彻底零残留")
    print()
    print(f"总构建耗时: {elapsed:.2f} 秒")
    print("======================================================================")


if __name__ == "__main__":
    main()
