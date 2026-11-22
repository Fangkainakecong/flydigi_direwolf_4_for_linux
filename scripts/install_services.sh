#!/bin/sh
# 安装/切换/卸载 Flydigi systemd 服务。需要 root。
#
#   sudo scripts/install_services.sh install  # daemon 开机自启 + udev（默认，不装 watch）
#   sudo scripts/install_services.sh --watch  # 可选：安装 watch 按需启停
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
SUDOERS=/etc/sudoers.d/flydigi
SYSTEMCTL=$(command -v systemctl 2>/dev/null || true)
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
  # polkitd 启动时如果 rules.d 还不存在，不一定会自动监视新建目录；
  # 重启一次确保规则立即生效。
  systemctl restart polkit 2>/dev/null || true
}

remove_polkit() {
  rm -f "$POLKIT_RULE"
  systemctl restart polkit 2>/dev/null || true
}

install_sudoers() {
  [ -n "$SYSTEMCTL" ] || return 0
  cat > "$SUDOERS" <<EOF
$TARGET_USER ALL=(root) NOPASSWD: $SYSTEMCTL start $DAEMON, $SYSTEMCTL stop $DAEMON, $SYSTEMCTL restart $DAEMON
EOF
  chmod 440 "$SUDOERS"
  if command -v visudo >/dev/null 2>&1; then
    if ! visudo -cf "$SUDOERS" >/dev/null 2>&1; then
      rm -f "$SUDOERS"
      echo "⚠ sudoers 规则校验失败，已跳过（托盘可能仍会询问密码）"
    fi
  fi
}

remove_sudoers() {
  rm -f "$SUDOERS"
}

case "$MODE" in
  remove|--remove)
    systemctl disable --now "$WATCH" 2>/dev/null || true
    systemctl disable --now "$DAEMON" 2>/dev/null || true
    rm -f "$SYSTEMD/$WATCH" "$SYSTEMD/$DAEMON" "$UDEV/$RULE"
    remove_polkit
    remove_sudoers
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
    install_sudoers
    systemctl enable --now "$WATCH"
    systemctl restart "$WATCH"
    echo "已启用 watch 模式：$WATCH 按需启动 $DAEMON"
    echo "  日志: journalctl -u $WATCH -u $DAEMON -f"
    exit 0
    ;;

  ""|--daemon|install)
    # watch 代码/unit 保留，但默认不启用、不自动启动；只有主程序开机自启。
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
    install_sudoers
    # 只 enable 主程序；watch 保留 unit 但明确 disable。
    systemctl disable --now "$WATCH" 2>/dev/null || true
    systemctl enable --now "$DAEMON"
    systemctl restart "$DAEMON"
    echo "已启用 daemon-only 模式：只有 $DAEMON 开机自启"
    echo "  watch 保留但未启用（不会自动启动）：systemctl start $WATCH 可手动启动"
    echo "  日志: journalctl -u $DAEMON -f"
    exit 0
    ;;

  *)
    echo "用法: $0 [install|remove|--watch]"
    exit 1
    ;;
esac
