#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试发布包生命周期管理模块 (Package Lifecycle Manager)
======================================================================
规范要求：
1. 测试发布包统一输出至桌面“调试输出”文件夹（C:\\Users\\<user>\\Desktop\\调试输出）；
2. 仅保留最新包，每次生成新包后，若目标目录已存在旧包，旧包安全移入 Windows 回收站；
3. 工作目录下绝不残留任何发布包或编译生成的文件，避免出现修改发布包代码的“伪修改”行为。
"""

import os
import shutil
import subprocess
import sqlite3
import tempfile
import time
from pathlib import Path


DEBUG_OUTPUT_DIR = Path(r"C:\Users\<user>\Desktop\调试输出")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _checked_recycle_target(path: Path) -> Path:
    resolved = Path(path).resolve()
    allowed_roots = (PROJECT_ROOT.resolve(), Path(tempfile.gettempdir()).resolve(), DEBUG_OUTPUT_DIR.resolve())
    if not any(resolved != root and resolved.is_relative_to(root) for root in allowed_roots):
        raise ValueError(f"拒绝回收构建范围之外的路径: {resolved}")
    return resolved


def recycle_path(path: Path) -> bool:
    """
    通过 Windows 原生 Shell API (Microsoft.VisualBasic.FileIO.FileSystem)
    将文件或目录安全移入 Windows 回收站（支持撤销，非物理抹除）。
    """
    if not path.exists():
        return False

    resolved = _checked_recycle_target(path)
    target_str = str(resolved).replace("'", "''")

    if resolved.is_dir():
        ps_cmd = (
            f"$ErrorActionPreference='Stop'; Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('{target_str}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
        )
    else:
        ps_cmd = (
            f"$ErrorActionPreference='Stop'; Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{target_str}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
        )

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=True
        )
        return not resolved.exists()
    except Exception as e:
        print(f"  [警告] 移动至回收站失败: {path} -> {e}")
        return False


def recycle_or_raise(path: Path):
    """旧产物回收失败就停止，禁止继续覆盖。"""
    if path.exists() and not recycle_path(path):
        raise OSError(f"回收失败，原文件已保留: {path}")


def vacuum_database(src_db: Path, dst_db: Path):
    """Snapshot the donor, then export a compact API-free copy from RAM."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / 'v5_server'))
    from account_db import AccountDB
    src_db = src_db.resolve(strict=True)
    dst_db = dst_db.resolve()
    if src_db == dst_db:
        raise ValueError('构建数据库不能覆盖源库')
    backup = AccountDB.backup_save_file(src_db)
    recycle_or_raise(dst_db)
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    AccountDB.export_sanitized_snapshot(src_db, dst_db)
    print(f'  [OK] 数据库写前备份: {backup}；发布副本API配置已清空')


def get_debug_output_dir() -> Path:
    """获取并确保桌面“调试输出”目录存在"""
    DEBUG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return DEBUG_OUTPUT_DIR


def publish_package_to_debug_output(source_path: Path, target_filename: str = None) -> Path:
    """
    将工作区编译输出的测试发布包移动至桌面“调试输出”目录：
    1. 确保目标目录存在；
    2. 若目标目录已存在同名旧包，先将旧包移入 Windows 回收站；
    3. 将新包移动至“调试输出”目录，仅保留最新包；
    4. 返回最终就绪的文件路径。
    """
    source_path = Path(source_path).resolve(strict=True)
    if not source_path.is_file() or source_path.stat().st_size == 0:
        raise ValueError(f"发布源文件为空或不是文件: {source_path}")
    target_dir = get_debug_output_dir().resolve()
    name = target_filename or source_path.name
    if Path(name).name != name or name in (".", ".."):
        raise ValueError("发布文件名不能包含目录")
    dest_path = (target_dir / name).resolve()
    if dest_path.parent != target_dir:
        raise ValueError("发布目标必须在调试输出目录内")
    if source_path == dest_path:
        return dest_path

    if dest_path.exists():
        print(f"  [*] 检测到调试输出目录中存在旧版本发布包: {dest_path.name}")
        print(f"  [*] 正在将旧包移入 Windows 回收站...")
        recycle_or_raise(dest_path)

    print(f"  [*] 正在将新发布包移入桌面【调试输出】目录: {name}...")
    shutil.move(str(source_path), str(dest_path))
    print(f"  [√] 测试发布包就绪: {dest_path}")
    return dest_path


def clean_workspace_dist(dist_dir: Path):
    """清理工作目录中的 dist 目录，确保工作区干净"""
    if dist_dir.exists():
        recycle_or_raise(dist_dir)
        print(f"  [√] 构建临时目录已移入回收站: {dist_dir}")
