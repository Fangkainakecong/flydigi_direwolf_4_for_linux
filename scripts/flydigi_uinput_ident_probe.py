#!/usr/bin/env python3
"""uinput 虚拟设备识别探针：手柄 / 键盘 / 鼠标

要回答的问题
------------
daemon 不解绑 xpad 就没法读 M1/M2/体感；解绑之后必须自己造一个"看起来
就是 Xbox 360 手柄"的设备。本脚本用和 daemon 完全相同的 capability 建
三个 uinput 设备，确认：

1. 内核接受这套 capability（轴范围、FF、按键表）；
2. 桌面/Steam/RetroArch 能不能认成标准 Xbox 360 手柄；
3. 键盘、鼠标是否作为独立设备出现在 `/dev/input/`；
4. 生成的 SDL GUID 是不是 `030000005e0400008e02000014010000`。

它不禁用 xpad、不碰 USB，可以和真实手柄同时存在，风险为零。

用法
----
    # 需要 python3-evdev（Ubuntu: sudo apt install python3-evdev）
    python3 scripts/flydigi_uinput_ident_probe.py --wait 60

    保持 60 秒，其间在另一个终端跑：
        sudo evtest
        sudo libinput debug-events
        xinput list
        cat /proc/bus/input/devices

    python3 scripts/flydigi_uinput_ident_probe.py --emit-test   # 自动发一串事件
    python3 scripts/flydigi_uinput_ident_probe.py --dry-run     # 只打印 capability

产物
----
`captures/uinput_ident_<时间戳>.txt`：设备节点、名字、GUID、capability 摘要。
"""

import argparse
import os
import struct
import sys
import time

try:
    from evdev import AbsInfo, UInput, ecodes
except ImportError:
    sys.exit(
        '缺少 python3-evdev。安装方法（任选其一）：\n'
        '  sudo apt install python3-evdev\n'
        '  python3 -m venv .venv && .venv/bin/pip install evdev\n'
        '注意项目脚本要用同一个解释器运行（venv 里用 .venv/bin/python3）。')

# xpad 对 045e:028e 用的身份；虚拟设备照抄，Steam/SDL 才会命中内置映射。
PAD_VID, PAD_PID, PAD_VERSION = 0x045E, 0x028E, 0x0114
PAD_NAME = 'Microsoft X-Box 360 pad'
KB_NAME = 'Flydigi DW4 Virtual Keyboard'
MOUSE_NAME = 'Flydigi DW4 Virtual Mouse'

AXIS = AbsInfo(0, -32768, 32767, 16, 128, 0)
TRIGGER = AbsInfo(0, 0, 255, 0, 0, 0)
HAT = AbsInfo(0, -1, 1, 0, 0, 0)

PAD_CAPS = {
    ecodes.EV_KEY: [
        ecodes.BTN_A, ecodes.BTN_B, ecodes.BTN_X, ecodes.BTN_Y,
        ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_SELECT, ecodes.BTN_START,
        ecodes.BTN_MODE, ecodes.BTN_THUMBL, ecodes.BTN_THUMBR,
    ],
    ecodes.EV_ABS: [
        (ecodes.ABS_X, AXIS), (ecodes.ABS_Y, AXIS),
        (ecodes.ABS_RX, AXIS), (ecodes.ABS_RY, AXIS),
        (ecodes.ABS_Z, TRIGGER), (ecodes.ABS_RZ, TRIGGER),
        (ecodes.ABS_HAT0X, HAT), (ecodes.ABS_HAT0Y, HAT),
    ],
    ecodes.EV_FF: [ecodes.FF_RUMBLE],
}

# 1..248 覆盖全部标准键盘键；再加几个常用多媒体/浏览器键。
KEYBOARD_KEYS = [k for k in range(1, 249)]
KEYBOARD_KEYS += [k for k in (ecodes.KEY_PLAYPAUSE, ecodes.KEY_NEXTSONG,
                              ecodes.KEY_PREVIOUSSONG, ecodes.KEY_VOLUMEUP,
                              ecodes.KEY_VOLUMEDOWN, ecodes.KEY_MUTE)
                  if k not in KEYBOARD_KEYS]
KB_CAPS = {ecodes.EV_KEY: KEYBOARD_KEYS}

MOUSE_KEYS = [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE,
              ecodes.BTN_SIDE, ecodes.BTN_EXTRA]
MOUSE_RELS = [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL, ecodes.REL_HWHEEL]
for extra in ('REL_WHEEL_HI_RES', 'REL_HWHEEL_HI_RES'):
    code = getattr(ecodes, extra, None)
    if code is not None:
        MOUSE_RELS.append(code)
MOUSE_CAPS = {ecodes.EV_KEY: MOUSE_KEYS, ecodes.EV_REL: MOUSE_RELS}


def sdl_guid(bus=0x0003, vendor=PAD_VID, product=PAD_PID, version=PAD_VERSION):
    """SDL2 GUID 字符串：bus/vendor/product/version 各占 4 字节（低 16 位有效）。

    内置映射里 Xbox 360 手柄是 030000005e0400008e02000014010000。
    """
    return struct.pack('<IIII', bus, vendor, product, version).hex()


def summarize(caps):
    out = []
    for evtype, items in sorted(caps.items()):
        names = []
        for item in items:
            if isinstance(item, tuple):
                code, info = item
                names.append('%s[%d..%d]' % (code_name(evtype, code),
                                             info.min, info.max))
            else:
                names.append(code_name(evtype, item))
        out.append('%s(%d): %s' % (ev_name(evtype), len(items), ', '.join(names)))
    return '\n  '.join(out)


def code_name(evtype, code):
    table = ecodes.bytype.get(evtype, {})
    name = table.get(code, 'code%d' % code)
    if isinstance(name, (list, tuple)):
        name = min(name, key=len)     # 取最短别名：BTN_X 优先于 BTN_NORTH
    return name


def ev_name(evtype):
    try:
        return ecodes.EV[evtype]
    except KeyError:
        return 'EV%d' % evtype


def make_devices():
    pad = UInput(PAD_CAPS, name=PAD_NAME, version=PAD_VERSION,
                 vendor=PAD_VID, product=PAD_PID, bustype=ecodes.BUS_USB)
    kb = UInput(KB_CAPS, name=KB_NAME, version=0x0001,
                vendor=0x1234, product=0x5678, bustype=ecodes.BUS_USB)
    mouse = UInput(MOUSE_CAPS, name=MOUSE_NAME, version=0x0001,
                   vendor=0x1234, product=0x5679, bustype=ecodes.BUS_USB)
    return pad, kb, mouse


def scan_event_nodes(names):
    """从 /sys/class/input 找同名设备，列出 eventN。"""
    found = {}
    base = '/sys/class/input'
    for entry in sorted(os.listdir(base)):
        if not entry.startswith('event'):
            continue
        try:
            with open(os.path.join(base, entry, 'device', 'name')) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name in names:
            found.setdefault(name, []).append('/dev/input/' + entry)
    return found


def emit_test(pad, kb, mouse):
    """发一串事件，方便在 evtest 里肉眼确认。"""
    print('发测试事件：A 按下/松开，左摇杆画方框，键盘 Shift+A，鼠标移动+滚轮')
    pad.write(ecodes.EV_KEY, ecodes.BTN_A, 1)
    pad.syn()
    time.sleep(0.15)
    pad.write(ecodes.EV_KEY, ecodes.BTN_A, 0)
    pad.syn()

    for x, y in ((30000, 0), (0, 30000), (-30000, 0), (0, -30000), (0, 0)):
        pad.write(ecodes.EV_ABS, ecodes.ABS_X, x)
        pad.write(ecodes.EV_ABS, ecodes.ABS_Y, y)
        pad.syn()
        time.sleep(0.12)

    kb.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)
    kb.write(ecodes.EV_KEY, ecodes.KEY_A, 1)
    kb.syn()
    time.sleep(0.1)
    kb.write(ecodes.EV_KEY, ecodes.KEY_A, 0)
    kb.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
    kb.syn()

    mouse.write(ecodes.EV_REL, ecodes.REL_X, 40)
    mouse.write(ecodes.EV_REL, ecodes.REL_Y, -25)
    mouse.write(ecodes.EV_REL, ecodes.REL_WHEEL, 1)
    mouse.syn()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wait', type=float, default=0, help='保持设备 N 秒')
    ap.add_argument('--emit-test', action='store_true', help='自动发一串测试事件')
    ap.add_argument('--dry-run', action='store_true', help='只打印 capability')
    ap.add_argument('--out', default='captures')
    args = ap.parse_args()

    lines = []
    lines.append('=== 计划创建的三台虚拟设备 ===')
    lines.append('Xbox 手柄: %s  %04x:%04x version=%04x  SDL GUID=%s'
                 % (PAD_NAME, PAD_VID, PAD_PID, PAD_VERSION, sdl_guid()))
    lines.append('  ' + summarize(PAD_CAPS))
    lines.append('键盘: %s  (%d 个键)' % (KB_NAME, len(KEYBOARD_KEYS)))
    lines.append('鼠标: %s  REL=%s KEY=%s'
                 % (MOUSE_NAME,
                    ','.join(code_name(ecodes.EV_REL, c) for c in MOUSE_RELS),
                    ','.join(code_name(ecodes.EV_KEY, c) for c in MOUSE_KEYS)))
    print('\n'.join(lines))
    if args.dry_run:
        print('\n--dry-run：未创建设备。')
        return

    pad, kb, mouse = make_devices()
    try:
        # 不要用 ui.device：它要求当前用户对新建的 event 节点有读写权限，
        # 非 root 时会是 None。直接扫 /sys 更可靠。
        found = scan_event_nodes({PAD_NAME, KB_NAME, MOUSE_NAME})
        lines.append('\n=== 实际创建 ===')
        for role, name in (('pad', PAD_NAME), ('keyboard', KB_NAME),
                           ('mouse', MOUSE_NAME)):
            nodes = ' '.join(found.get(name, ['(未找到节点)']))
            lines.append('%-9s %-18s %s' % (role, nodes, name))
            print('%-9s %s  %s' % (role, nodes, name))
        if not found:
            print('提示：看不到 event 节点通常是因为非 root；用 sudo 或把用户加入 input 组。')

        lines.append('\n=== 验证命令 ===')
        for cmd in ('sudo evtest', 'sudo libinput debug-events',
                    'xinput list', 'cat /proc/bus/input/devices'):
            lines.append('  ' + cmd)
            print('  验证: %s' % cmd)

        if args.emit_test:
            emit_test(pad, kb, mouse)
            time.sleep(0.5)

        if args.wait > 0:
            print('\n设备保持 %.0f 秒，去另一个终端验证。Ctrl-C 提前结束。'
                  % args.wait)
            t0 = time.time()
            while time.time() - t0 < args.wait:
                time.sleep(0.2)
    except KeyboardInterrupt:
        print('\n结束。')
    finally:
        pad.close()
        kb.close()
        mouse.close()

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'uinput_ident_%s.txt'
                        % time.strftime('%Y%m%d-%H%M%S'))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n已写 %s' % path)


if __name__ == '__main__':
    main()
