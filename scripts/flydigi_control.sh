#!/bin/sh
# 桌面快捷方式：确保托盘在运行，然后打开配置编辑器。
set -u
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if ! pgrep -f '^/usr/bin/python3 -m flydigi_daemon tray$' >/dev/null 2>&1; then
  nohup "$ROOT/scripts/flydigi_tray.sh" >/dev/null 2>&1 &
  sleep 0.6
fi

exec "$ROOT/scripts/flydigi_edit.sh"
