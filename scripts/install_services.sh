#!/bin/sh
# 安装/切换/卸载 Flydigi systemd 服务。需要 root。
#
#   sudo scripts/install_services.sh install  # daemon 开机自启 + udev（默认）
#   sudo scripts/install_services.sh --watch  # 可选：watch 按需启停
#   sudo scripts/install_services.sh remove   # 停止并删除 service/udev/polkit
#   install/remove 与 --daemon/--remove 等旧写法都兼容
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
POLKIT_DIR=/etc/polkit-1/rules.d
POLKIT_RULE=$POLKIT_DIR/50-flydigi.rules
TARGET_USER=${SUDO_USER:-$(id -un)}
MODE=${1:-}

install_polkit() {
  mkdir -p "$POLKIT_DIR"
  cat > "$POLKIT_RULE" <<EOF
polkit.addRule(function(action, subject) {
  if (action.id == "org.freedesktop.systemd1.manage-units" &&
      subject.user == "$TARGET_USER" &&
      /^flydigi-(dw4|watch)\\.service\$/.test(action.lookup("unit"))) {
    return polkit.Result.YES;
  }
});
EOF
  chmod 644 "$POLKIT_RULE"
}

remove_polkit() {
  rm -f "$POLKIT_RULE"
}

case "$MODE" in
  remove|--remove)
    systemctl disable --now "$WATCH" 2>/dev/null || true
    systemctl disable --now "$DAEMON" 2>/dev/null || true
    rm -f "$SYSTEMD/$WATCH" "$SYSTEMD/$DAEMON" "$UDEV/$RULE"
    remove_polkit
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
    systemctl reset-failed "$DAEMON" 2>/dev/null || true
    systemctl disable --now "$DAEMON" 2>/dev/null || true
    install_polkit
    systemctl enable --now "$WATCH"
    systemctl restart "$WATCH"
    echo "已启用 watch 模式：$WATCH 按需启动 $DAEMON"
    echo "  日志: journalctl -u $WATCH -u $DAEMON -f"
    exit 0
    ;;

  ""|--daemon|install)
    sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-dw4.service.in" \
      > "$SYSTEMD/$DAEMON"
    sed "s|@ROOT@|$ROOT|g" "$ROOT/systemd/flydigi-watch.service.in" \
      > "$SYSTEMD/$WATCH"
    chmod 644 "$SYSTEMD/$DAEMON" "$SYSTEMD/$WATCH"
    install -m 644 "$ROOT/udev/$RULE" "$UDEV/$RULE"
    systemctl daemon-reload
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=045e 2>/dev/null || true
    systemctl reset-failed "$DAEMON" 2>/dev/null || true
    install_polkit
    systemctl disable --now "$WATCH" 2>/dev/null || true
    systemctl enable --now "$DAEMON"
    systemctl restart "$DAEMON"
    echo "已启用 daemon-only 模式：$DAEMON 开机自启；watch 已停用（代码保留）"
    echo "  日志: journalctl -u $DAEMON -f"
    echo "  切回 watch: sudo $0 --watch"
    exit 0
    ;;

  *)
    echo "用法: $0 [install|remove|--watch]"
    exit 1
    ;;
esac
