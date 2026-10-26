#!/bin/sh
set -eu
SERVER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SERVER_DIR"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
server_python=${PYTHON_BIN:-"$SERVER_DIR/.venv/bin/python"}
if [ ! -x "$server_python" ] && ! command -v "$server_python" >/dev/null 2>&1; then
    echo 'Run sh setup_linux.sh first.' >&2
    exit 1
fi
exec "$server_python" -B linux_manager.py "$@"
