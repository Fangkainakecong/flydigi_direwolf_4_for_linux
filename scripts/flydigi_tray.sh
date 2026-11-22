#!/bin/sh
# 托盘启动脚本（用户级，不需要 root）。
set -u
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LOG="${TMPDIR:-/tmp}/flydigi-tray.log"
cd "$ROOT" || exit 1
exec /usr/bin/python3 -m flydigi_daemon tray >>"$LOG" 2>&1
