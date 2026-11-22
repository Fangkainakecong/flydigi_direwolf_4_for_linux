#!/bin/sh
# 安装/切换/卸载 Flydigi systemd 服务。需要 root。
#
#   sudo scripts/install_services.sh          # 默认：daemon 开机自启 + udev；watch 不启动
#   sudo scripts/install_services.sh --watch  # 可选：watch 按需启停（daemon 不 enable）
#   sudo scripts/install_services.sh --remove # 停止并删除两个 service/udev
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行：sudo $0"
  exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SYSTEMD=/etc/systemd/system
UDEV=/etc/udev/rules.d
WATCH=flydigi-watch.service
DAEMON=flydigi-dw4.service
RULE=99-flydigi-dw4.rules
MODE=${1:-}

case "$MODE" in
  --remove)
    systemctl disable --now "$WATCH" 2>/dev/null || true
    systemctl disable --now "$DAEMON" 2>/dev/null || true
    rm -f "$SYSTEMD/$WATCH" "$SYSTEMD/$DAEMON" "$UDEV/$RULE"
    systemctl daemon-reload
    udevadm control --reload-rules 2>/dev/null || true
    echo "已卸载 $WATCH / $DAEMON / $RULE"
    exit 0
    ;;

  --watch)
    sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-dw4.service.in" \
      > "$SYSTEMD/$DAEMON"
    sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-watch.service.in" \
      > "$SYSTEMD/$WATCH"
    chmod 644 "$SYSTEMD/$DAEMON" "$SYSTEMD/$WATCH"
    rm -f "$UDEV/$RULE"
    systemctl daemon-reload
    udevadm control --reload-rules 2>/dev/null || true
    systemctl disable --now "$DAEMON" 2>/dev/null || true
    systemctl enable --now "$WATCH"
    systemctl restart "$WATCH"
    echo "已启用 watch 模式：$WATCH 按需启动 $DAEMON"
    echo "  日志: journalctl -u $WATCH -u $DAEMON -f"
    exit 0
    ;;

  ""|--daemon)
    sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-dw4.service.in" \
      > "$SYSTEMD/$DAEMON"
    sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-watch.service.in" \
      > "$SYSTEMD/$WATCH"
    chmod 644 "$SYSTEMD/$DAEMON" "$SYSTEMD/$WATCH"
    install -m 644 "$ROOT/udev/$RULE" "$UDEV/$RULE"
    systemctl daemon-reload
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=045e 2>/dev/null || true
    systemctl disable --now "$WATCH" 2>/dev/null || true
    systemctl enable --now "$DAEMON"
    systemctl restart "$DAEMON"
    echo "已启用 daemon-only 模式：$DAEMON 开机自启；watch 已停用（代码保留）"
    echo "  日志: journalctl -u $DAEMON -f"
    echo "  切回 watch: sudo $0 --watch"
    exit 0
    ;;

  *)
    echo "用法: $0 [--daemon|--watch|--remove]"
    exit 1
    ;;
esac
