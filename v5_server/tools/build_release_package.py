#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《深空之眼》离线单机服务一键标准发布打包工具
功能：
1. 采用严格生产白名单机制，抽取生产运行真正必需的模块；
2. 自动剔除开发备份 (db_backup/)、单测与测试沙盒 (tests/)、旧版前端 node_modules、代码备份与临时测试库；
3. 对权威数据库 account.db 执行临时备份与 VACUUM 紧凑化；
4. 包含已全面瘦身至 WebP 的静态 Web 控制台 (static_web/)；
5. 打包生成便携运行启动脚本 (run_server.bat) 与证书安装脚本；
6. 在独立临时目录构建，校验后交付桌面“调试输出”，旧包移入回收站。
"""

import os
import sys
import tempfile
import uuid
import zipfile
import time
from pathlib import Path

# 导入发布包生命周期管理
try:
    from .package_lifecycle import publish_package_to_debug_output, clean_workspace_dist, recycle_or_raise, vacuum_database
    from .package_contract import collect_runtime_assets, stage_runtime_assets, seal_runtime, validate_archive
    from .windows_launchers import write_console_launcher
except ImportError:
    from package_lifecycle import publish_package_to_debug_output, clean_workspace_dist, recycle_or_raise, vacuum_database
    from package_contract import collect_runtime_assets, stage_runtime_assets, seal_runtime, validate_archive
    from windows_launchers import write_console_launcher


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
V5_SERVER = PROJECT_ROOT / "v5_server"
DIST_DIR = Path(tempfile.gettempdir()) / ("V5BackendRelease_" + uuid.uuid4().hex)
TEMP_STAGE = DIST_DIR / "_staging_v5_release"


def create_launcher_scripts(target_dir: Path):
    """生成友好直观的用户一键启动脚本"""
    for name in ("启动离线单机服务.bat", "run_server.bat"):
        write_console_launcher(target_dir / name)


def build_release_package(output_zip_name="AetherGazer_Server_v5_Release.zip"):
    start_time = time.time()
    # 缺协议/配置时，在数据库快照与任何发布操作之前失败。
    plan = collect_runtime_assets(PROJECT_ROOT)
    if not (V5_SERVER / "account.db").is_file():
        raise FileNotFoundError("缺少 account.db，停止封包")
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    if TEMP_STAGE.exists():
        recycle_or_raise(TEMP_STAGE)
    TEMP_STAGE.mkdir(parents=True, exist_ok=True)

    print("========================================================")
    print("正在构建标准精简发布包...")
    print(f"源工程目录: {V5_SERVER}")
    print(f"临时暂存区: {TEMP_STAGE}")
    print("========================================================")

    print(f"[1/4] 按统一资源合同拷贝 {len(plan)} 个运行文件...")
    stage_runtime_assets(TEMP_STAGE, plan)

    # 4. 紧凑化拷贝生产数据库 account.db
    print("[2/4] 只读快照生产数据库，在副本执行 VACUUM...")
    src_db = V5_SERVER / "account.db"
    dst_db = TEMP_STAGE / "account.db"
    vacuum_database(src_db, dst_db)

    # 生成一键启动 BAT
    create_launcher_scripts(TEMP_STAGE)
    print("[3/4] 校验暂存资源并独立加载包内协议，生成 SHA256 清单...")
    inventory = seal_runtime(TEMP_STAGE, plan)

    # 6. 计算未压缩发布目录各部分大小
    total_uncompressed = sum(f.stat().st_size for f in TEMP_STAGE.rglob("*") if f.is_file())
    print(f"  -> 未压缩发布包总大小: {total_uncompressed / 1024 / 1024:.2f} MB")

    # 7. 压缩生成 ZIP 归档
    zip_path = DIST_DIR / output_zip_name
    if zip_path.exists():
        recycle_or_raise(zip_path)

    print(f"[4/4] 正在压缩并验证最终 ZIP: {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _, files in os.walk(TEMP_STAGE):
            for file in files:
                abs_p = Path(root) / file
                rel_p = abs_p.relative_to(TEMP_STAGE)
                zf.write(abs_p, rel_p)

    validate_archive(zip_path, inventory)
    zip_size_mb = zip_path.stat().st_size / 1024 / 1024
    elapsed = time.time() - start_time

    # 清理暂存区
    recycle_or_raise(TEMP_STAGE)

    # 移动发布包至桌面“调试输出”并保留最新包，旧包移入回收站
    final_output_path = publish_package_to_debug_output(zip_path)
    clean_workspace_dist(DIST_DIR)

    print("========================================================")
    print("[OK] 发布包构建成功！")
    print(f"交付产物路径: {final_output_path}")
    print(f"未压缩展开体积: {total_uncompressed / 1024 / 1024:.2f} MB")
    print(f"ZIP 最终压缩体积: {zip_size_mb:.2f} MB")
    print(f"整体构建耗时:   {elapsed:.2f} 秒")
    print("========================================================")


if __name__ == "__main__":
    build_release_package()
