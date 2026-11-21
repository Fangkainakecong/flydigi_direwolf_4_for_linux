#!/bin/sh
# 启动/打开可视化编辑器。桌面快捷方式和项目内快捷方式都指向这里。
set -u
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PORT=${FLYDIGI_EDIT_PORT:-8765}
URL="http://127.0.0.1:${PORT}/"
LOG="${TMPDIR:-/tmp}/flydigi-edit.log"
cd "$ROOT" || exit 1

open_url() {
  if [ "${FLYDIGI_NO_BROWSER:-0}" = 1 ]; then
    return 0
  fi
  if [ -n "${FLYDIGI_BROWSER:-}" ] && command -v "$FLYDIGI_BROWSER" >/dev/null 2>&1; then
    "$FLYDIGI_BROWSER" "$URL" >/dev/null 2>&1 &
  elif command -v microsoft-edge >/dev/null 2>&1; then
    microsoft-edge "$URL" >/dev/null 2>&1 &
  elif command -v firefox >/dev/null 2>&1; then
    firefox "$URL" >/dev/null 2>&1 &
  else
    xdg-open "$URL" >/dev/null 2>&1 &
  fi
}

model=$(curl -fsS --max-time 1 "${URL}api/model" 2>/dev/null || true)
case "$model" in
  *'"editor": 3'*|*'"editor":3'*)
    open_url
    exit 0
    ;;
esac

# 端口被旧版编辑器占着：只杀命令行能对上的 flydigi_daemon edit
if [ -n "$model" ]; then
  pid=$(ss -ltnp "sport = :${PORT}" 2>/dev/null \
        | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1)
  if [ -n "${pid:-}" ]; then
    cmd=$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)
    case "$cmd" in
      *flydigi_daemon*edit*)
        kill "$pid" 2>/dev/null
        sleep 0.4
        ;;
    esac
  fi
fi

nohup /usr/bin/python3 -m flydigi_daemon edit --no-open --port "$PORT" \
  >>"$LOG" 2>&1 &
sleep 0.8
open_url
exit 0
