#!/bin/sh
set -eu
case "${1:-}" in
    ""|-h|--help|help)
        cat <<'HELP'
用法：sh server.sh <命令> [参数]

  setup            配置网络参数和可选 Nginx
  run [参数]       前台启动；Ctrl+C 退出
  environment      检查 Python 和运行依赖
  prepare          普通 Linux：创建虚拟环境并安装依赖
  check            检查配置、文件权限和端口（停止服务后使用）
  templates        生成 systemd 和 Nginx 模板
  install-service  安装并启动 systemd 服务，需要相应权限
  start            启动服务（若已安装系统守护服务）
  stop             停止服务（安全终止服务端进程及系统守护服务）
  restart          重启服务
  status           查看服务运行状态与进程 PID
  logs             跟踪 systemd 服务日志
  -h, --help, help 显示帮助

示例：
  sh server.sh setup
  sh server.sh run
  sh server.sh run --supervised
  sh server.sh run --db '新存档_01.db' --https-port 18443 --http-port 1880
  sh server.sh run --capture-cdn
  sh server.sh run --cdn-mode relay
  sudo sh server.sh install-service

run 附加参数作为本次启动的初始配置；持久配置使用 setup。
--supervised 启用命令行托管，允许从控制面板保存并重启。
CDN模式：--cdn-mode off|relay|cache（默认off）。relay仅穿透、不存盘，适合本地设备。
--capture-cdn兼容开启存盘；--no-capture-cdn强制关闭。云端穿透仍消耗出口带宽。
Termux 使用系统包准备依赖，不执行 prepare；systemd 命令只用于支持 systemd 的环境。
HELP
        exit 0
        ;;
esac
SERVER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SERVER_DIR"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
server_python=${PYTHON_BIN:-}
if [ -z "$server_python" ]; then
    if [ -x "$SERVER_DIR/.venv/bin/python" ]; then
        server_python="$SERVER_DIR/.venv/bin/python"
    else
        for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
            if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,10))' 2>/dev/null; then
                server_python=$candidate
                break
            fi
        done
    fi
fi
if [ -z "$server_python" ] || ! command -v "$server_python" >/dev/null 2>&1; then
    echo '缺少 Python 3.10+。' >&2
    echo 'Ubuntu/Debian: sudo apt update && sudo apt install -y python3' >&2
    echo 'Termux: pkg update && pkg install python' >&2
    echo '其他系统请安装 Python 3.10+ 并确保在 PATH 中。' >&2
    exit 1
fi
"$server_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else "需要 Python 3.10+；可通过 PYTHON_BIN 指定解释器")'
exec "$server_python" -B linux_manager.py "$@"
