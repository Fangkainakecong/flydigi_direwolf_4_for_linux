#!/bin/sh
# 安装/卸载 flydigi-watch + flydigi-dw4 systemd 服务。需要 root。
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行：sudo $0"
  exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SYSTEMD=/etc/systemd/system
WATCH=flydigi-watch.service
DAEMON=flydigi-dw4.service

if [ "${1:-}" = "--remove" ]; then
  systemctl disable --now "$WATCH" 2>/dev/null || true
  systemctl stop "$DAEMON" 2>/dev/null || true
  rm -f "$SYSTEMD/$WATCH" "$SYSTEMD/$DAEMON"
  systemctl daemon-reload
  echo "已卸载 $WATCH / $DAEMON"
  exit 0
fi

sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-dw4.service.in" \
  > "$SYSTEMD/$DAEMON"
sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-watch.service.in" \
  > "$SYSTEMD/$WATCH"
chmod 644 "$SYSTEMD/$DAEMON" "$SYSTEMD/$WATCH"
systemctl daemon-reload
systemctl enable --now "$WATCH"
systemctl restart "$WATCH"
echo "已安装并启动 $WATCH"
echo "  daemon: $DAEMON（由 watch 按需启动，不单独 enable）"
echo "  日志: journalctl -u $WATCH -u $DAEMON -f"
