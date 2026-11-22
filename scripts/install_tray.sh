#!/bin/sh
# 安装/移除托盘自启动（用户级，不需要 root）。
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP="$AUTOSTART/flydigi-tray.desktop"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
CONTROL="$DESKTOP_DIR/flydigi-control.desktop"
CONTROL_APP="$APP_DIR/flydigi-control.desktop"

case "${1:-install}" in
  install)
    mkdir -p "$AUTOSTART" "$ICON_DIR" "$DESKTOP_DIR" "$APP_DIR"
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Flydigi 托盘
Name[zh_CN]=Flydigi 托盘
Comment=飞智冰原狼4：暂停/恢复、重启、编辑配置
Exec=$ROOT/scripts/flydigi_tray.sh
Path=$ROOT
Icon=$ICON_DIR/flydigi.svg
Terminal=false
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF
    cat > "$CONTROL" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=飞智手柄控制
Name[zh_CN]=飞智手柄控制
Comment=打开托盘控制/本地映射编辑器
Exec=$ROOT/scripts/flydigi_control.sh
Path=$ROOT
Icon=$ICON_DIR/flydigi.svg
Terminal=false
Categories=Settings;Utility;
StartupNotify=false
EOF
    chmod +x "$DESKTOP" "$CONTROL"
    cp -f "$CONTROL" "$CONTROL_APP"
    cp -f "$ROOT/assets/flydigi.svg" "$ICON_DIR/flydigi.svg"
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t \
            "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" \
            >/dev/null 2>&1 || true
    fi
    echo "已安装托盘自启动：$DESKTOP"
    echo "已安装桌面快捷方式：$CONTROL"
    echo "  现在启动：$ROOT/scripts/flydigi_tray.sh &"
    ;;
  remove)
    rm -f "$DESKTOP" "$CONTROL" "$CONTROL_APP"
    echo "已移除托盘自启动/桌面快捷方式"
    ;;
  *)
    echo "用法: $0 [install|remove]"
    exit 1
    ;;
esac
