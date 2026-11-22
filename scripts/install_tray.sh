#!/bin/sh
# 启动/停止托盘（用户级，不需要 root）。
#   start：安装自启动/桌面快捷方式，并立即启动托盘
#   stop ：停止托盘，并移除自启动/桌面快捷方式
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP="$AUTOSTART/flydigi-tray.desktop"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
CONTROL="$DESKTOP_DIR/flydigi-control.desktop"
CONTROL_APP="$APP_DIR/flydigi-control.desktop"

case "${1:-start}" in
  start|install)
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
    if ! pgrep -f '^/usr/bin/python3 -m flydigi_daemon tray$' >/dev/null 2>&1; then
        # 优先用 systemd --user 启动，让托盘属于图形会话；从远程终端
        # nohup 启动时 polkit 会把进程当远程会话，暂停/重启会被拒绝。
        started=0
        if command -v systemd-run >/dev/null 2>&1; then
            systemctl --user reset-failed flydigi-tray.service \
                >/dev/null 2>&1 || true
            if systemd-run --user --collect --unit=flydigi-tray \
                --setenv=DISPLAY="${DISPLAY:-:0.0}" \
                --setenv=XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" \
                "$ROOT/scripts/flydigi_tray.sh" >/dev/null 2>&1; then
                started=1
            fi
        fi
        if [ "$started" -eq 0 ]; then
            nohup "$ROOT/scripts/flydigi_tray.sh" >/dev/null 2>&1 &
        fi
        sleep 0.8
    fi
    echo "已安装托盘自启动：$DESKTOP"
    echo "已安装桌面快捷方式：$CONTROL"
    ;;
  stop|remove)
    systemctl --user stop flydigi-tray.service >/dev/null 2>&1 || true
    systemctl --user reset-failed flydigi-tray.service >/dev/null 2>&1 || true
    pids=$(pgrep -f '^/usr/bin/python3 -m flydigi_daemon tray$' || true)
    if [ -n "$pids" ]; then
        kill $pids 2>/dev/null || true
    fi
    rm -f "$DESKTOP"
    echo "已停止托盘并移除自启动；桌面快捷方式保留"
    ;;
  *)
    echo "用法: $0 [start|stop]"
    exit 1
    ;;
esac
