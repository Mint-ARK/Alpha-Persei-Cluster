# -*- coding: utf-8 -*-
r"""PC downloader using the common verified CDN engine.
Incomplete attempts are retained; retry restarts rather than appending unverified bytes.
"""
import concurrent.futures
import http.client
import json
import os
import socket
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

OUTPUT_DIR = cdn_proxy.get_data_dir("pc")
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
    tasks = load_platform_tasks('pc', data_dir,
        os.path.join(v5_dir, 'static_resources', 'pc'))
    return {digest + '.ys': value[1] for digest, value in tasks.items()}


def download_single_file(fn, expected_size, out_dir=OUTPUT_DIR, ips=None):
    try:
        spec = integrity.ObjectSpec(fn, expected_size)
        final = integrity.safe_path(out_dir, fn)
        skipped = integrity.valid_file(final, spec)
        path = cdn_proxy.download_verified('pc', fn, out_dir, expected_size=expected_size)
        with _PRINT_LOCK:
            _STATS['skipped_files' if skipped else 'downloaded_files'] += 1
            _STATS['skipped_bytes' if skipped else 'downloaded_bytes'] += os.path.getsize(path)
        return True
    except (OSError, ValueError, http.client.HTTPException):
        with _PRINT_LOCK:
            _STATS['failed_files'] += 1
        return False


def main():
    print("=" * 64)
    print("🚀 深空之眼 Windows PC 全量游戏资源高速多线程下载器")
    print(f"📁 目标落地目录: {OUTPUT_DIR}")
    print(f"⚡ 并发下载线程: {MAX_WORKERS}")
    print("=" * 64)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cdn_proxy.sync_initial_manifests()

    items = load_all_manifest_items(OUTPUT_DIR)
    if not items:
        print("[ERROR] 未解析到任何待下载资源，请检查 PC 清单文件！")
        return 1

    total_files = len(items)
    total_bytes = sum(items.values())
    _STATS["total_files"] = total_files
    _STATS["total_bytes"] = total_bytes

    print(f"[*] 待下载 PC 资源总计: {total_files} 个文件, 约 {total_bytes / (1024*1024*1024):.2f} GB")
    print("[*] 正在解析官方 CDN IP 并启动多线程下载池...")

    ips = cdn_proxy.resolve_doh(CDN_HOST)
    print(f"[*] 官方 CDN 穿透 IP 列表: {ips}")

    start_t = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(download_single_file, fn, sz, OUTPUT_DIR, ips): fn
            for fn, sz in items.items()
        }

        while True:
            done_cnt = sum(1 for f in futures if f.done())
            now = time.time()
            elapsed = max(1.0, now - start_t)
            dl_b = _STATS["downloaded_bytes"]
            speed_mb = (dl_b / (1024 * 1024)) / elapsed

            percent = (done_cnt / total_files) * 100
            print(
                f"\r[进度] {done_cnt}/{total_files} ({percent:.1f}%) | "
                f"已下: {_STATS['downloaded_files']} | 跳过: {_STATS['skipped_files']} | "
                f"失败: {_STATS['failed_files']} | 实时网速: {speed_mb:.2f} MB/s",
                end="", flush=True
            )

            if done_cnt >= total_files:
                break
            time.sleep(0.5)

        # An unexpected worker exception must not become a successful CLI exit.
        for future in futures:
            future.result()

    print("\n" + "=" * 64)
    print("🎉 Windows PC 资源下载完毕！")
    print(f"  - 新增下载: {_STATS['downloaded_files']} 个 ({_STATS['downloaded_bytes'] / (1024*1024):.2f} MB)")
    print(f"  - 本地已有: {_STATS['skipped_files']} 个")
    print(f"  - 失败重试: {_STATS['failed_files']} 个")
    print("=" * 64)
    return 1 if _STATS["failed_files"] else 0


if __name__ == "__main__":
    from download_assets import main as queue_main
    sys.exit(queue_main(default_platform='pc'))
