#!/usr/bin/env python3
"""M1 回归：报文 → PadState → uinput 事件

不碰 USB，也不创建 uinput，任何机器上都能跑：

    python3 tests/test_m1.py
    # 装了 pytest 也可以：pytest -q tests/test_m1.py
"""

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon import report as rep          # noqa: E402
from flydigi_daemon.translate import (            # noqa: E402
    EMPTY, describe, invert16, translate)


def frame(btn=0, lt=0, rt=0, axes=(0, 0, 0, 0), bits=0, gyro=(0, 0, 0)):
    """拼一帧 32 字节输入报文。"""
    d = bytearray(32)
    d[0] = 0x00
    d[1] = 0x14
    struct.pack_into('<H', d, 2, btn)
    d[4] = lt & 0xFF
    d[5] = rt & 0xFF
    struct.pack_into('<4h', d, 6, *axes)
    struct.pack_into('<3b', d, 14, *gyro)
    struct.pack_into('<I', d, 17, bits)
    return bytes(d)


# 标准字位：低字节 [2]，高字节 [3]
STD = {
    'up': 0x0001, 'down': 0x0002, 'left': 0x0004, 'right': 0x0008,
    'start': 0x0010, 'back': 0x0020, 'l3': 0x0040, 'r3': 0x0080,
    'lb': 0x0100, 'rb': 0x0200, 'home': 0x0400,
    'a': 0x1000, 'b': 0x2000, 'x': 0x4000, 'y': 0x8000,
}
BIT = {name: 1 << idx for idx, name in rep.KEY_NAMES.items()}


def test_idle():
    st = translate(frame())
    assert st.buttons == frozenset(), st
    assert st.hat == (0, 0), st.hat
    # xpad 用 ~value，所以中点是 -1
    assert st.axes == (0, -1, 0, -1), st.axes
    assert st.triggers == (0, 0)
    assert st.extras == frozenset()


def test_face_buttons_and_home():
    for name in ('a', 'b', 'x', 'y', 'home', 'lb', 'rb', 'start', 'back'):
        st = translate(frame(btn=STD[name]))
        assert st.buttons == frozenset({name}), (name, st.buttons)


def test_dpad_becomes_hat():
    assert translate(frame(btn=STD['up'])).hat == (0, -1)
    assert translate(frame(btn=STD['down'])).hat == (0, 1)
    assert translate(frame(btn=STD['left'])).hat == (-1, 0)
    assert translate(frame(btn=STD['right'])).hat == (1, 0)
    assert translate(frame(btn=STD['left'] | STD['up'])).hat == (-1, -1)


def test_axes_and_triggers():
    st = translate(frame(lt=12, rt=200, axes=(12345, 100, -20000, -1)))
    assert st.axes == (12345, invert16(100), -20000, invert16(-1)), st.axes
    assert st.triggers == (12, 200)
    # 端点不越界
    assert -32768 <= invert16(-32768) <= 32767
    assert -32768 <= invert16(32767) <= 32767


def test_extras_are_not_forwarded():
    st = translate(frame(bits=BIT['m1'] | BIT['m2'] | BIT['turbo'] | BIT['menu']))
    assert st.buttons == frozenset(), st.buttons
    assert st.extras == frozenset({'m1', 'm2', 'turbo', 'fn'}), st.extras


def test_reply_is_skipped():
    d = bytearray(frame())
    d[14] = 0xA5
    d[15] = 16
    assert translate(bytes(d)) is None


def test_describe():
    a = translate(frame())
    b = translate(frame(btn=STD['a']))
    line = describe(a, b)
    assert '+a' in line, line
    assert describe(b, a).find('-a') >= 0
    assert describe(a, a) == ''


def test_caps_do_not_advertise_ff_in_m1():
    from evdev import ecodes
    from flydigi_daemon.devices import pad_caps
    plain = pad_caps(with_ff=False)
    assert ecodes.EV_FF not in plain
    assert len(plain[ecodes.EV_ABS]) == 8
    assert ecodes.FF_RUMBLE in pad_caps(with_ff=True)[ecodes.EV_FF]


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('ok   %s' % name)
        except AssertionError as exc:
            failed += 1
            print('FAIL %s: %r' % (name, exc))
    print('\n%d/%d 通过' % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
