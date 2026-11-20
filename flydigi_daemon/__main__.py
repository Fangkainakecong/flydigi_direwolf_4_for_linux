"""命令行入口

    python3 -m flydigi_daemon check     # 识别 + 当前板载槽审计
    python3 -m flydigi_daemon run       # 独占手柄 + 虚拟 Xbox 手柄
    python3 -m flydigi_daemon run --dry-run
    python3 -m flydigi_daemon run --replay captures/control_xxx.bin --dry-run
"""

import argparse
import sys

from . import config as cfgmod
from . import mapping


def build_parser():
    ap = argparse.ArgumentParser(
        prog='flydigi-daemon',
        description='飞智冰原狼4 Linux daemon（M3：映射 DSL）')
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
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = cfgmod.load(args.config)

    errors, warnings = mapping.lint_config(cfg)
    for warning in warnings:
        print('⚠ %s' % warning)
    if errors:
        print('✗ 配置有 %d 处错误，先修好再启动：' % len(errors))
        for error in errors:
            print('  - %s' % error)
        return 2

    try:
        if args.cmd == 'check':
            from .check import run
            return run(cfg, deep=not args.no_deep)
        from .daemon import run_live, run_replay
        if args.replay:
            return run_replay(cfg, args.replay, dry_run=args.dry_run,
                              realtime=args.realtime,
                              max_frames=args.max_frames,
                              profile=args.profile)
        return run_live(cfg, dry_run=args.dry_run, seconds=args.seconds,
                        profile=args.profile)
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
