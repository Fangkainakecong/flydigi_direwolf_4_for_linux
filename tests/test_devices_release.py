#!/usr/bin/env python3
"""虚拟设备强制释放回归（不创建 uinput）"""

import os
import sys

from evdev import ecodes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon import devices as devmod          # noqa: E402


class FakeUI(object):
    def __init__(self):
        self.events = []

    def write(self, etype, code, value):
        self.events.append((etype, code, value))

    def syn(self):
        self.events.append(('syn',))


def _bare(cls, cur):
    dev = cls.__new__(cls)
    dev.ui = FakeUI()
    dev.cur = cur
    return dev


def test_keyboard_release_all_force():
    dev = _bare(devmod.VirtualKeyboard, {ecodes.KEY_LEFTCTRL})
    dev.release_all()
    downs = [e for e in dev.ui.events if e[0] == ecodes.EV_KEY]
    assert len(downs) == len(devmod.KEYBOARD_KEYS)
    assert all(value == 0 for _, _, value in downs)
    assert dev.ui.events[-1] == ('syn',)
    assert dev.cur == set()


def test_mouse_release_all_force():
    dev = _bare(devmod.VirtualMouse, {ecodes.BTN_LEFT})
    dev.release_all()
    events = [e for e in dev.ui.events if e[0] == ecodes.EV_KEY]
    assert len(events) == len(devmod.MOUSE_BUTTONS)
    assert all(value == 0 for _, _, value in events)
    assert dev.cur == set()


def test_gamepad_release_all_force():
    dev = _bare(devmod.VirtualGamepad, None)
    dev.release_all()
    events = dev.ui.events
    key_ups = [e for e in events if e[0] == ecodes.EV_KEY]
    assert len(key_ups) == len(devmod.BUTTON_MAP)
    assert all(value == 0 for _, _, value in key_ups)
    axes = {e[1]: e[2] for e in events if e[0] == ecodes.EV_ABS}
    assert axes[ecodes.ABS_X] == 0
    assert axes[ecodes.ABS_Y] == -1
    assert axes[ecodes.ABS_RX] == 0
    assert axes[ecodes.ABS_RY] == -1
    assert axes[ecodes.ABS_HAT0X] == 0
    assert axes[ecodes.ABS_HAT0Y] == 0
    assert axes[ecodes.ABS_Z] == 0
    assert axes[ecodes.ABS_RZ] == 0
    assert dev.cur.buttons == frozenset()
    assert dev.cur.axes == (0, -1, 0, -1)
    assert dev.cur.triggers == (0, 0)


def test_input_timeout_default_sane():
    from flydigi_daemon import config
    safety = config.safety_section(config.load())
    timeout = float(safety.get('input_timeout_ms', 400))
    watchdog = float(safety.get('watchdog_ms', 1500))
    assert 100 <= timeout <= watchdog, (timeout, watchdog)


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
