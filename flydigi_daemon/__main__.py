"""命令行入口

    python3 -m flydigi_daemon check     # 识别 + 当前板载槽审计
    python3 -m flydigi_daemon run       # 独占手柄 + 虚拟 Xbox 手柄
    python3 -m flydigi_daemon run --dry-run
    python3 -m flydigi_daemon run --replay captures/control_xxx.bin --dry-run
"""

import argparse
import errno
import os
import sys

from .procname import set_name

from . import config as cfgmod
from . import mapping


def build_parser():
    ap = argparse.ArgumentParser(
        prog='flydigi-daemon',
        description='飞智冰原狼4 Linux daemon（M4 + 可视化映射编辑器）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('check', help='识别设备并审计当前板载槽')
    c.add_argument('--config', default=None, help='配置文件（默认 config/default.toml）')
    c.add_argument('--no-deep', action='store_true',
                   help='只看设备是否存在，不接管、不读槽位')

    r = sub.add_parser('run', help='独占手柄并转发到虚拟 Xbox 手柄')
    r.add_argument('--config', default=None)
    r.add_argument('--dry-run', action='store_true',
                   help='不建虚拟设备，只打印按键/轴变化')
    r.add_argument('--replay', default=None,
                   help='回放抓包 .bin（不碰 USB；配合 --dry-run 可无手柄运行）')
    r.add_argument('--realtime', action='store_true',
                   help='回放时按原始时间节奏；默认尽快跑完')
    r.add_argument('--seconds', type=float, default=None,
                   help='实时模式运行 N 秒后自动退出')
    r.add_argument('--max-frames', type=int, default=None,
                   help='回放最多处理多少帧')
    r.add_argument('--profile', default=None,
                   help='临时用指定 profile 启动（不读 state.json）')
    r.add_argument('--calibration', default=None,
                   help='体感标定文件（默认 config/calibration.json）')

    cal = sub.add_parser('calibrate', help='标定体感（静置 + 360° + 90°）')
    cal.add_argument('--config', default=None)
    cal.add_argument('--still', type=float, default=3.0,
                     help='静置采样秒数（默认 3）')
    cal.add_argument('--turns', type=int, default=3,
                     help='水平转几整圈（默认 3，越大越准）')
    cal.add_argument('--yaw-seconds', type=float, default=20.0,
                     help='水平旋转最长采样时间（默认 20 秒，停住会自动结束）')
    cal.add_argument('--pitch-seconds', type=float, default=6.0,
                     help='俯仰最长采样时间（默认 6 秒）')
    cal.add_argument('--estimate', action='store_true',
                     help='不测动作，写入抓包估算值（±20%%，仅起步）')
    cal.add_argument('--set-yaw', type=float, default=None,
                     help='直接指定 yaw 的 counts_per_degree')
    cal.add_argument('--set-pitch', type=float, default=None,
                     help='直接指定 pitch 的 counts_per_degree（默认同 yaw）')
    cal.add_argument('--sign-yaw', type=int, choices=(1, -1), default=1)
    cal.add_argument('--sign-pitch', type=int, choices=(1, -1), default=1)
    cal.add_argument('--strict', action='store_true',
                     help='yaw/pitch 相差过大时直接失败，不用 pitch 兜底')
    cal.add_argument('--output', default=None,
                     help='输出路径（默认 config/calibration.json）')

    tray = sub.add_parser('tray', help='状态栏托盘（暂停/重启/编辑配置/退出）')

    logs = sub.add_parser('logs', help='查看 daemon/watch/编辑器日志')
    logs.add_argument('-n', '--lines', type=int, default=80)
    logs.add_argument('-f', '--follow', action='store_true',
                      help='持续跟随 daemon/watch 日志')

    dev = sub.add_parser('devices',
                         help='列出 045e:028e 手柄并显示会选哪一个')
    dev.add_argument('--config', default=None)

    watch = sub.add_parser('watch',
                           help='按需启动/停止 daemon（root，systemd）')
    watch.add_argument('--config', default=None)

    rel = sub.add_parser('release',
                         help='释放 XTEST 卡住的按键/鼠标按钮（通用清理）')
    rel.add_argument('--dry-run', action='store_true',
                     help='只打印，不实际注入释放事件')
    rel.add_argument('--display', default=None, help='X display（默认 $DISPLAY）')

    ed = sub.add_parser('edit', help='启动本地网页映射编辑器')
    ed.add_argument('--config', default=None)
    ed.add_argument('--port', type=int, default=8765,
                    help='本地端口（默认 8765，仅监听 127.0.0.1）')
    ed.add_argument('--no-open', action='store_true',
                    help='不自动打开浏览器')
    ed.add_argument('--idle-timeout', type=float, default=1800,
                    help='无请求多少秒后自动退出（默认 1800，0=不超时）')
    ed.add_argument('--page-close-grace', type=float, default=20,
                    help='页面关闭且没有修改后多少秒退出（默认 20）')

    sh = sub.add_parser('show', help='打印映射表 / 生成可视化 HTML')
    sh.add_argument('--config', default=None)
    sh.add_argument('--profile', default=None, help='只看某个 profile')
    sh.add_argument('--html', nargs='?', default=None,
                    const=os.path.join(cfgmod.ROOT, 'config', 'mappings.html'),
                    help='生成单文件 HTML（默认 config/mappings.html）')
    sh.add_argument('--open', action='store_true', help='生成后用浏览器打开')
    sh.add_argument('--quiet', action='store_true', help='不打印终端表格')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    set_name({
        'run': 'flydigi-dw4',
        'watch': 'flydigi-watch',
        'edit': 'flydigi-edit',
        'tray': 'flydigi-tray',
        'calibrate': 'flydigi-calib',
        'check': 'flydigi-check',
        'show': 'flydigi-show',
        'devices': 'flydigi-devices',
        'logs': 'flydigi-logs',
        'release': 'flydigi-release',
    }.get(args.cmd, 'flydigi'))
    cfg = cfgmod.load(getattr(args, 'config', None))

    errors, warnings = mapping.lint_config(cfg)
    if args.cmd != 'show':
        for warning in warnings:
            print('⚠ %s' % warning)
    if errors:
        print('✗ 配置有 %d 处错误，先修好再启动：' % len(errors))
        for error in errors:
            print('  - %s' % error)
        return 2

    try:
        if args.cmd == 'calibrate':
            from .calibrate import run as calibrate_run
            return calibrate_run(cfg, args, cfgmod.calibration_file(cfg))
        if args.cmd == 'tray':
            from .tray import main as tray_main
            return tray_main()
        if args.cmd == 'logs':
            from .logs import run as logs_run
            return logs_run(args)
        if args.cmd == 'devices':
            from . import usb
            devcfg = cfgmod.device_section(cfg)
            candidates = usb.candidate_pads(devcfg['vendor'],
                                            devcfg['product'])
            chosen, reason = usb.choose_pad(candidates,
                                            devcfg.get('product_hint'))
            print('VID:PID %04x:%04x  product_hint=%r'
                  % (devcfg['vendor'], devcfg['product'],
                     devcfg.get('product_hint')))
            for c in candidates:
                mark = '*' if chosen and c['name'] == chosen['name'] else ' '
                print('%s %-8s %-28s serial=%r  %s'
                      % (mark, c['name'], c['product'] or '(无产品名)',
                         c['serial'] or '', c['path'] or '(无节点)'))
                nodes = usb.find_event_nodes(c)
                if nodes:
                    print('    input: %s' % ' '.join(nodes))
            if chosen:
                print('→ 会接管: %s (%s)' % (chosen['name'], chosen['product']))
            else:
                print('→ 不会接管: %s' % reason)
            return 0
        if args.cmd == 'watch':
            from .watch import run as watch_run
            return watch_run(cfg)
        if args.cmd == 'release':
            from .xrelease import run as release_run
            return release_run(args)
        if args.cmd == 'edit':
            from .ui import run as ui_run
            return ui_run(cfg, args)
        if args.cmd == 'show':
            from .show import run as show_run
            return show_run(cfg, args)
        if args.cmd == 'check':
            from .check import run
            return run(cfg, deep=not args.no_deep)
        from .daemon import run_live, run_replay
        if args.replay:
            return run_replay(cfg, args.replay, dry_run=args.dry_run,
                              realtime=args.realtime,
                              max_frames=args.max_frames,
                              profile=args.profile,
                              calibration=args.calibration)
        return run_live(cfg, dry_run=args.dry_run, seconds=args.seconds,
                        profile=args.profile, calibration=args.calibration)
    except PermissionError as exc:
        print('权限不足：%s' % exc)
        print('用 sudo 运行，或先配置 udev 规则（M6）。')
        return 1
    except OSError as exc:
        if exc.errno in (errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN):
            # dongle 被拔掉/设备消失：正常退出，交给 udev 下次插入时拉起。
            print('设备已断开，daemon 退出（拔插后会自动恢复）')
            return 0
        print('USB 错误：%s' % exc)
        print('确认 dongle 没被拔掉，且没有另一个程序占用接口。')
        return 1


if __name__ == '__main__':
    sys.exit(main() or 0)
