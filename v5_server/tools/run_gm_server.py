# -*- coding: utf-8 -*-
"""
run_gm_server.py — V5 控制面板专用轻量级 GM 服务启动器

特点：
1. 100% 绑定测试数据库 account_test.db，严格与生产库物理隔离；
2. 仅提供 /api/gm 路由与跨域支持，无需管理员权限（可自定义端口）；
3. 方便前端开发人员或控制面板直接联调。
"""
import os
import sys
import argparse

# 将 v5_server 加入路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flask import Flask
from gm_api import gm_bp, init_gm_api

def create_app():
    app = Flask("v5_gm_dev_server")
    from admin_access import install_admin_guard
    install_admin_guard(app)
    init_gm_api(host_ip="127.0.0.1")
    app.register_blueprint(gm_bp, url_prefix="/api/gm")
    return app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V5 控制面板独立 GM API 服务")
    parser.add_argument("--port", type=int, default=80, help="监听端口 (默认 80)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    args = parser.parse_args()

    app = create_app()
    print("=" * 60)
    print(f"[*] V5 GM API 独立开发服务已启动")
    print(f"[*] 监听地址: http://{args.host}:{args.port}")
    print(f"[*] 数据库绑定: account_test.db (物理隔离测试库)")
    print(f"[*] 控制面板单文件打开亦可直接免开箱联调")
    print("=" * 60)
    try:
        app.run(host=args.host, port=args.port, debug=False)
    except Exception as e:
        print(f"[!] 启动端口 {args.port} 失败: {e}")
        if args.port == 80:
            print("[*] 尝试降级至 8080 端口启动...")
            app.run(host=args.host, port=8080, debug=False)
