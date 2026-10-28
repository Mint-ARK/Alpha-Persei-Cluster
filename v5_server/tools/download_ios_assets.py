# -*- coding: utf-8 -*-
"""IOS downloader using the common verified CDN engine.
Incomplete attempts are retained; retry restarts rather than appending unverified bytes.
"""
import concurrent.futures
import http.client
import json
import os
import ssl
import sys
import threading
import time
import urllib.request

v5_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if v5_dir not in sys.path:
    sys.path.insert(0, v5_dir)

import cdn_proxy
import cdn_integrity as integrity

OUTPUT_DIR = cdn_proxy.get_data_dir("ios")
CDN_HOST = "download-eo.ys4fun.com"
CHUNK_SIZE = 65536
MAX_WORKERS = 24  # 并发线程数

_PRINT_LOCK = threading.Lock()
_STATS = {
    "total_files": 0,
    "total_bytes": 0,
    "downloaded_files": 0,
    "downloaded_bytes": 0,
    "skipped_files": 0,
    "skipped_bytes": 0,
    "failed_files": 0,
    "start_time": time.time(),
    "last_report_time": time.time(),
    "last_report_bytes": 0,
}


def load_all_manifest_items(data_dir=OUTPUT_DIR):
    from cdn_integrity import load_platform_tasks
    tasks = load_platform_tasks('ios', data_dir,
        os.path.join(v5_dir, 'static_resources', 'ios'))
    return tasks


def download_single_file(item, ips=None, ctx=None, data_dir=OUTPUT_DIR, max_retries=3):
    try:
        digest, (_name, expected_size) = item
        spec = integrity.ObjectSpec(str(digest) + '.ys', expected_size, digest)
        final = integrity.safe_path(data_dir, spec.filename)
        skipped = integrity.valid_file(final, spec)
        path = cdn_proxy.download_verified('ios', spec.filename, data_dir,
            expected_size=expected_size, expected_md5=digest, max_attempts=max_retries)
        with _PRINT_LOCK:
            _STATS['skipped_files' if skipped else 'downloaded_files'] += 1
            _STATS['skipped_bytes' if skipped else 'downloaded_bytes'] += os.path.getsize(path)
        return True, 'skipped' if skipped else 'ok'
    except (OSError, ValueError, http.client.HTTPException):
        with _PRINT_LOCK:
            _STATS['failed_files'] += 1
        return False, 'failed'


def run_downloader(workers=MAX_WORKERS, limit=None):
    """多线程并发下载入口。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 64)
    print("  🚀 深空之眼 iOS 游戏资源全量极速下载器")
    print(f"  - 目标落盘目录 : {OUTPUT_DIR}")
    print(f"  - 并发下载线程 : {workers} 线程")
    print("=" * 64)

    print("[*] 正在解析 iOS 资源清单列表...")
    tasks = load_all_manifest_items(OUTPUT_DIR)
    if not tasks:
        print("[ERROR] 未找到任何有效资源清单，请先启动一次服务器捕获 assethash 清单！")
        return 1

    task_list = list(tasks.items())
    if limit:
        task_list = task_list[:limit]

    total_files = len(task_list)
    total_bytes = sum(sz for _, (_, sz) in task_list)
    _STATS["total_files"] = total_files
    _STATS["total_bytes"] = total_bytes
    _STATS["start_time"] = time.time()
    _STATS["last_report_time"] = time.time()

    print(f"[OK] 成功解析待下载资源: 共 {total_files} 个文件 (总大小: {total_bytes / (1024**3):.2f} GB)")

    # DoH 解析真实 CDN IP
    ips = cdn_proxy.resolve_doh(CDN_HOST)
    print(f"[*] CDN ({CDN_HOST}) 解析真实节点 IP: {ips}")

    ctx = ssl.create_default_context()

    print("[*] 开始并发下载...")

    stop_monitor = False

    def _monitor():
        last_bytes = 0
        last_t = time.time()
        while not stop_monitor:
            time.sleep(1.0)
            now = time.time()
            dt = now - last_t
            if dt <= 0:
                continue
            with _PRINT_LOCK:
                cur_dl_bytes = _STATS["downloaded_bytes"]
                cur_skip_bytes = _STATS["skipped_bytes"]
                cur_dl_files = _STATS["downloaded_files"]
                cur_skip_files = _STATS["skipped_files"]
                cur_fail = _STATS["failed_files"]

            speed_mb = ((cur_dl_bytes - last_bytes) / (1024 * 1024)) / dt
            total_done_files = cur_dl_files + cur_skip_files
            pct = (total_done_files / total_files * 100) if total_files else 0
            total_done_bytes = cur_dl_bytes + cur_skip_bytes
            pct_bytes = (total_done_bytes / total_bytes * 100) if total_bytes else 0

            print(
                f"\r[下载进度] {total_done_files}/{total_files} 文件 ({pct:.1f}%) | "
                f"{total_done_bytes / (1024**3):.2f}/{total_bytes / (1024**3):.2f} GB ({pct_bytes:.1f}%) | "
                f"速度: {speed_mb:.2f} MB/s | 失败: {cur_fail}  ",
                end="",
                flush=True,
            )
            last_bytes = cur_dl_bytes
            last_t = now

    mon_thread = threading.Thread(target=_monitor, daemon=True)
    mon_thread.start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(download_single_file, it, ips, ctx, OUTPUT_DIR) for it in task_list]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    stop_monitor = True
    time.sleep(0.5)
    print("\n" + "=" * 64)
    print(f"  🎉 全部下载任务已完成！")
    print(f"  - 新下载文件 : {_STATS['downloaded_files']} 个 ({_STATS['downloaded_bytes'] / (1024**3):.2f} GB)")
    print(f"  - 跳过已存在 : {_STATS['skipped_files']} 个 ({_STATS['skipped_bytes'] / (1024**3):.2f} GB)")
    print(f"  - 失败文件数 : {_STATS['failed_files']} 个")
    print("=" * 64)

    return 1 if _STATS["failed_files"] else 0


if __name__ == "__main__":
    from download_assets import main as queue_main
    sys.exit(queue_main(default_platform='ios'))
