"""命令行入口

    python3 -m flydigi_daemon check     # 识别 + 当前板载槽审计
    python3 -m flydigi_daemon run       # 独占手柄 + 虚拟 Xbox 手柄
    python3 -m flydigi_daemon run --dry-run
    python3 -m flydigi_daemon run --replay captures/control_xxx.bin --dry-run
"""

import argparse
import os
import sys

from . import config as cfgmod
from . import mapping


def build_parser():
    ap = argparse.ArgumentParser(
        prog='flydigi-daemon',
        description='飞智冰原狼4 Linux daemon（M4：体感标定）')
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
    cal.add_argument('--yaw-seconds', type=float, default=6.0,
                     help='水平旋转采样窗口（默认 6 秒）')
    cal.add_argument('--pitch-seconds', type=float, default=4.0,
                     help='俯仰采样窗口（默认 4 秒）')
    cal.add_argument('--output', default=None,
                     help='输出路径（默认 config/calibration.json）')

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
    cfg = cfgmod.load(args.config)

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
        print('USB 错误：%s' % exc)
        print('确认 dongle 没被拔掉，且没有另一个程序占用接口。')
        return 1


if __name__ == '__main__':
    sys.exit(main() or 0)
