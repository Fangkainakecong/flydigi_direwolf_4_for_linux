"""`flydigi-daemon logs`：三个进程的日志入口

* daemon / watch：systemd journal（`journalctl -u flydigi-dw4 -u flydigi-watch`）
* 编辑器：启动脚本写 `/tmp/flydigi-edit.log`
"""

import os
import subprocess


EDITOR_LOG = '/tmp/flydigi-edit.log'


def run(args):
    cmd = ['journalctl', '-u', 'flydigi-watch', '-u', 'flydigi-dw4',
           '-n', str(args.lines), '--no-pager']
    if args.follow:
        cmd.append('-f')
    try:
        subprocess.run(cmd, check=False)
    except OSError as exc:
        print('无法运行 journalctl：%s' % exc)

    print('\n--- 编辑器日志 %s ---' % EDITOR_LOG)
    try:
        with open(EDITOR_LOG, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        for line in lines[-args.lines:]:
            print(line.rstrip())
    except OSError:
        print('（还没有编辑器日志；通过桌面快捷方式启动后才会生成）')
    print('\n提示：daemon/watch 的完整日志用 journalctl -f 查看；'
          '编辑器关闭页面后退出。')
    return 0
