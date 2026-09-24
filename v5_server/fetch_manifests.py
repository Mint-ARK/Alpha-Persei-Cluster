# -*- coding: utf-8 -*-
r"""
fetch_manifests.py — 客户端资源清单文件下载与多平台物理隔离准备工具

职责：
1. 从官方 CDN (download-eo.ys4fun.com / download.ys4fun.com)
   精准下载各平台专属的最新资源清单与语音清单文件：
   - PC: assethash_307_229_3807d27e1a6e26853f1fccfa8b7681d0.bytes, voice_package_list_307_229.bytes (md5: 7fa2f2...), voice_hash_ja_30700229.bytes (51KB)
   - iOS: assethash_307_229_29e8f745e8ec6032937a65a0bfd3a9cf.bytes, voice_package_list_307_229.bytes (md5: 95b0ff...), voice_hash_ja_30700229.bytes (311KB)
   - Android: assethash_307_229_3807d27e1a6e26853f1fccfa8b7681d0.bytes, voice_package_list_307_229.bytes
2. 分别保存到 static_resources/pc/, static_resources/ios/, static_resources/android/
   以及 D:\PC_DATA, D:\iOS_DATA, D:\Android_DATA，确保各端 100% 独立隔离！
"""
import os
import sys
import ssl
import http.client
import shutil

v5_dir = os.path.dirname(os.path.abspath(__file__))
if v5_dir not in sys.path:
    sys.path.insert(0, v5_dir)

import cdn_proxy

DEFAULT_RES_DIR = os.path.join(v5_dir, "static_resources")
HOST = "download-eo.ys4fun.com"

PLATFORM_MANIFESTS = {
    "pc": [
        "assethash_307_229_3807d27e1a6e26853f1fccfa8b7681d0.bytes",
        "voice_package_list_307_229.bytes",
        "voice_hash_zh_30700229.bytes",
        "voice_hash_ja_30700229.bytes",
        "assethash_307_227_4af2a0111d0669c5b209324c615eae51.bytes",
        "voice_package_list_307_227.bytes",
        "voice_hash_zh_30700227.bytes",
        "voice_hash_ja_30700227.bytes",
    ],
    "ios": [
        "assethash_307_229_29e8f745e8ec6032937a65a0bfd3a9cf.bytes",
        "voice_package_list_307_229.bytes",
        "voice_hash_zh_30700229.bytes",
        "voice_hash_ja_30700229.bytes",
    ],
    "android": [
        "assethash_307_229_3807d27e1a6e26853f1fccfa8b7681d0.bytes",
        "voice_package_list_307_229.bytes",
        "voice_hash_zh_30700229.bytes",
        "voice_hash_ja_30700229.bytes",
    ]
}


def download_single_manifest(platform, fn, output_path, log=print):
    """从官方 CDN 下载指定平台的清单。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req_headers = {
        "Host": HOST,
        "User-Agent": "UnityPlayer/2022.3.62f3 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "Accept": "*/*",
    }
    url_path = f"/{platform}/resources/{fn}"
    ips = cdn_proxy.resolve_doh(HOST)

    for ip in ips:
        try:
            sock = cdn_proxy.create_direct_socket(ip, 443, timeout=10)
            conn = http.client.HTTPSConnection(ip, 443, context=ctx, timeout=10)
            conn.sock = ctx.wrap_socket(sock, server_hostname=HOST)
            conn.request("GET", url_path, headers=req_headers)
            resp = conn.getresponse()
            if resp.status == 200:
                data = resp.read()
                conn.close()
                if data:
                    with open(output_path, "wb") as f:
                        f.write(data)
                    return True
            conn.close()
        except Exception:
            continue
    return False


def prepare_manifests(res_dir=None, force_download=False, log=print):
    """确保所有平台的专属清单在本地及 D 盘专属目录就绪。"""
    res_dir = res_dir or DEFAULT_RES_DIR
    os.makedirs(res_dir, exist_ok=True)
    total = 0
    ready = 0

    for plat, fns in PLATFORM_MANIFESTS.items():
        plat_dir = os.path.join(res_dir, plat)
        os.makedirs(plat_dir, exist_ok=True)
        data_dir = cdn_proxy.get_data_dir(plat)

        for fn in fns:
            total += 1
            target_static = os.path.join(plat_dir, fn)
            target_data = os.path.join(data_dir, fn)

            if os.path.isfile(target_static) and os.path.getsize(target_static) > 0 and not force_download:
                ready += 1
                if not os.path.isfile(target_data) or os.path.getsize(target_data) == 0:
                    try:
                        shutil.copy2(target_static, target_data)
                    except Exception:
                        pass
                continue

            ok = download_single_manifest(plat, fn, target_static, log=log)
            if ok:
                ready += 1
                try:
                    shutil.copy2(target_static, target_data)
                except Exception:
                    pass
            else:
                log(f"[WARN] 未能准备 [{plat.upper()}] 清单文件: {fn}")

    log(f"[Manifest] 官方资源清单多平台物理隔离准备完毕: {ready}/{total} 个就绪")
    return ready == total


if __name__ == "__main__":
    force = "--force" in sys.argv
    prepare_manifests(force_download=force)
