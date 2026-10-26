#!/bin/sh
set -eu
SERVER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SERVER_DIR"
if [ -n "${PYTHON_BIN:-}" ]; then
    server_python=$PYTHON_BIN
elif [ -x "$SERVER_DIR/.venv/bin/python" ]; then
    server_python=$SERVER_DIR/.venv/bin/python
else
    server_python=python3
fi
if ! command -v "$server_python" >/dev/null 2>&1; then
    echo "Python 3.10+ is required. See README_Linux.md." >&2
    exit 1
fi
"$server_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required")'
if ! "$server_python" -c 'import flask, werkzeug, cryptography'; then
    echo "Install dependencies with: .venv/bin/python -m pip install -r requirements.txt" >&2
    exit 1
fi
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
exec "$server_python" -B linux_manager.py run "$@"
