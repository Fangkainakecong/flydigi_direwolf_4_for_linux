#!/usr/bin/env python3
"""M2 回归：SELECT + A/B/X/Y 四槽仲裁

不碰 USB/uinput，纯状态机测试：

    python3 tests/test_m2.py
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon.slots import SlotController   # noqa: E402
from flydigi_daemon.translate import PadState     # noqa: E402

PROFILES = ['gamepad', 'desktop', 'media', 'passthrough']


def st(*buttons):
    return PadState(buttons=frozenset(buttons), axes=(0, -1, 0, -1))


def make(state_path=None, **kw):
    # 测试里固定用 120 ms，断言的时间点才不用跟着默认值改。
    kw.setdefault('window_ms', 120)
    calls = []

    def on_switch(index, name, letter):
        calls.append((index, name, letter))

    ctrl = SlotController(PROFILES, state_path=state_path,
                          persist=bool(state_path), on_switch=on_switch, **kw)
    return ctrl, calls


def test_letter_to_slot_order():
    for letter, index in (('a', 0), ('b', 1), ('x', 2), ('y', 3)):
        ctrl, calls = make()
        assert ctrl.slot == 0
        ctrl.feed(st('back'), 0.0)
        out = ctrl.feed(st('back', letter), 0.03)
        assert out.buttons == frozenset(), (letter, out.buttons)
        assert ctrl.slot == index, (letter, ctrl.slot)
        assert calls == [(index, PROFILES[index], letter)], calls


def test_tap_back_is_forwarded():
    ctrl, _ = make()
    assert ctrl.feed(st('back'), 0.0).buttons == frozenset()
    tap = ctrl.feed(st(), 0.05)
    assert 'back' in tap.buttons, tap.buttons     # 补发按下
    released = ctrl.feed(st(), 0.06)
    assert 'back' not in released.buttons, released.buttons


def test_hold_back_past_window():
    ctrl, _ = make()
    ctrl.feed(st('back'), 0.0)
    flushed = ctrl.tick(0.13)
    assert flushed is not None and 'back' in flushed.buttons, flushed
    assert 'back' in ctrl.feed(st('back'), 0.2).buttons
    assert 'back' not in ctrl.feed(st(), 0.3).buttons
    assert ctrl.tick(0.4) is None


def test_chord_swallow_until_both_released():
    ctrl, calls = make()
    ctrl.feed(st('back'), 0.0)
    ctrl.feed(st('back', 'a'), 0.02)
    assert ctrl.slot == 0 and len(calls) == 1
    # 字母还按着：继续吞
    assert ctrl.feed(st('back', 'a'), 0.04).buttons == frozenset()
    # 字母松开、SELECT 还按着：仍然吞 Back
    assert ctrl.feed(st('back'), 0.06).buttons == frozenset()
    # SELECT 松开：全部安静
    assert ctrl.feed(st(), 0.08).buttons == frozenset()


def test_second_letter_while_chord_held_does_not_switch_or_leak():
    ctrl, calls = make()
    ctrl.feed(st('back'), 0.0)
    ctrl.feed(st('back', 'b'), 0.02)
    assert ctrl.slot == 1 and len(calls) == 1
    ctrl.feed(st('back'), 0.03)
    out = ctrl.feed(st('back', 'a'), 0.04)      # 第二个字母
    assert ctrl.slot == 1 and len(calls) == 1, calls
    assert out.buttons == frozenset(), out.buttons
    ctrl.feed(st(), 0.05)
    # 物理松开后重新按 A，应该正常透传
    assert ctrl.feed(st('a'), 0.06).buttons == frozenset({'a'})


def test_letter_after_window_passes_through():
    ctrl, calls = make()
    ctrl.feed(st('back'), 0.0)
    ctrl.tick(0.13)
    out = ctrl.feed(st('back', 'a'), 0.2)
    assert out.buttons == frozenset({'back', 'a'}), out.buttons
    assert calls == []
    assert ctrl.slot == 0


def test_other_button_aborts_window():
    ctrl, calls = make()
    ctrl.feed(st('back'), 0.0)
    out = ctrl.feed(st('back', 'start'), 0.05)
    assert out.buttons == frozenset({'back', 'start'}), out.buttons
    assert calls == []


def test_letter_held_before_select_is_not_a_chord():
    ctrl, calls = make()
    ctrl.feed(st('a'), 0.0)
    out = ctrl.feed(st('back', 'a'), 0.02)
    assert out.buttons == frozenset({'a'}), out.buttons
    assert calls == []
    assert 'back' in ctrl.tick(0.2).buttons


def test_hold_back_twice_does_not_double_flush():
    ctrl, _ = make()
    ctrl.feed(st('back'), 0.0)
    first = ctrl.tick(0.13)
    second = ctrl.tick(0.20)
    assert first is not None and second is None


def test_suppress_disabled_still_switches_but_does_not_swallow():
    ctrl, calls = make(suppress=False)
    ctrl.feed(st('back'), 0.0)
    out = ctrl.feed(st('back', 'x'), 0.02)
    assert ctrl.slot == 2 and len(calls) == 1
    assert out.buttons == frozenset({'back', 'x'}), out.buttons


def test_state_file_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'state.json')
        ctrl, _ = make(state_path=path)
        ctrl.feed(st('back'), 0.0)
        ctrl.feed(st('back', 'y'), 0.02)
        assert os.path.exists(path)
        again, _ = make(state_path=path)
        assert again.slot == 3 and again.profile == 'passthrough'


def test_state_file_corrupt_is_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'state.json')
        with open(path, 'w') as f:
            f.write('{not json')
        ctrl, _ = make(state_path=path, startup_profile='media')
        assert ctrl.slot == 2


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
