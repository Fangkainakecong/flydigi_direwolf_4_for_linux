#!/usr/bin/env python3
"""Deskflow 断线时自动释放 XTEST 按键/鼠标按钮（可选常驻）

Deskflow 客户端通过 XTEST 注入远端键鼠；连接中断时可能只发了按下、没发松开，
留下 Ctrl/Shift/鼠标键卡住。这个脚本每 0.5 秒检查一次 24800 端口的 TCP
连接，发现从“已连接”变成“断开”时调用 `flydigi-daemon release` 的同一套
libXtst 释放逻辑。

用法：

    python3 scripts/deskflow_release_watch.py
    python3 scripts/deskflow_release_watch.py --interval 1 --port 24800

需要桌面会话（$DISPLAY）。只处理“断开”这一瞬间；如果连接一直没断但按键
已经卡住，手动运行：

    python3 -m flydigi_daemon release
"""

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon.xrelease import ReleaseError, release   # noqa: E402


def established(port=24800):
    """当前是否有到 `port` 的 ESTABLISHED TCP 连接。"""
    try:
        out = subprocess.run(['ss', '-Htn', 'state', 'established'],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return has_connection(out, port)


def has_connection(ss_output, port=24800):
    needle = ':%d' % port
    for line in ss_output.splitlines():
        if needle in line:
            return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description='Deskflow 断线时释放卡住的键')
    ap.add_argument('--port', type=int, default=24800)
    ap.add_argument('--interval', type=float, default=0.5)
    args = ap.parse_args(argv)

    if not os.environ.get('DISPLAY'):
        print('没有 DISPLAY；请在桌面会话里运行')
        return 1

    was = established(args.port)
    print('监视 Deskflow 端口 %d；当前%s连接。Ctrl-C 停止。'
          % (args.port, '已' if was else '未'))
    try:
        while True:
            now = established(args.port)
            if was and not now:
                try:
                    keys, buttons = release()
                    print('%s Deskflow 断开，已释放 %d 个 keycode、%d 个按钮'
                          % (time.strftime('%H:%M:%S'), keys, buttons))
                except ReleaseError as exc:
                    print('%s Deskflow 断开，释放失败：%s'
                          % (time.strftime('%H:%M:%S'), exc))
            was = now
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        print('\n已停止')
    return 0


if __name__ == '__main__':
    sys.exit(main())
