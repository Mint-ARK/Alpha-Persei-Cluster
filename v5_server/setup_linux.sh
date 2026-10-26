#!/bin/sh
set -eu
SERVER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SERVER_DIR"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
server_python=${PYTHON_BIN:-}
if [ -z "$server_python" ]; then
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,10))' 2>/dev/null; then
            server_python=$candidate
            break
        fi
    done
fi
if [ -z "$server_python" ] || ! command -v "$server_python" >/dev/null 2>&1; then
    echo 'Python 3.10+ is required. Install it with venv/pip support, then retry.' >&2
    echo 'Ubuntu/Debian: apt install python3 python3-venv python3-pip (version must be >=3.10)' >&2
    echo 'OpenCloudOS/RHEL: dnf install python3.11 python3.11-pip (or newer supported Python)' >&2
    exit 1
fi
"$server_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else "Python 3.10+ is required")'
"$server_python" -B linux_manager.py prepare
exec "$SERVER_DIR/.venv/bin/python" -B linux_manager.py setup "$@"
