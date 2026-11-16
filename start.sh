#!/usr/bin/env sh
# Skillset 启动脚本（macOS / Linux）
cd "$(dirname "$0")" || exit 1
exec python3 app/server.py "$@"
