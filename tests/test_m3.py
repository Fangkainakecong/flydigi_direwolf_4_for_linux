#!/usr/bin/env python3
"""M3 回归：映射 DSL（按钮/轴/组合键/滚轮/组开关）

    python3 tests/test_m3.py

全部离线：不碰 USB，也不建 uinput。
"""

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evdev import ecodes                              # noqa: E402

from flydigi_daemon import config                     # noqa: E402
from flydigi_daemon.mapping import (                  # noqa: E402
    ConfigError, Mapper, axis_norm, format_target, gate_errors,
    lint_config, parse_button_target, parse_config, parse_profile)
from flydigi_daemon.translate import PadState, translate   # noqa: E402


def raw(buttons=(), extras=(), axes=(0, -1, 0, -1), triggers=(0, 0)):
    return PadState(buttons=frozenset(buttons), extras=frozenset(extras),
                    axes=axes, triggers=triggers)


def profile(data, name='test'):
    return parse_profile(name, data)


class FakeDevices(object):
    def __init__(self):
        self.pad = _Sink()
        self.keyboard = _Sink()
        self.mouse = _Sink()


class _Sink(object):
    def __init__(self):
        self.calls = []

    def apply(self, *args):
        self.calls.append(args)


# ---------------------------------------------------------------------------
# 解析 / 校验
# ---------------------------------------------------------------------------
def test_parse_default_config():
    cfg = config.load(config.DEFAULT_PATH)
    errors, warnings = lint_config(cfg)
    assert errors == [], errors
    assert all('calibrate' in w for w in warnings), warnings
    profiles = parse_config(cfg)
    assert set(profiles) == {'gamepad', 'desktop', 'media', 'passthrough'}
    assert profiles['desktop'].buttons['a'].target.value == (ecodes.KEY_SPACE,)
    assert profiles['desktop'].groups == {'gyro': False}


def test_unknown_key_name_suggests_nothing():
    try:
        profile({'a': 'key:notakey'})
    except ConfigError as exc:
        assert '未知按键名' in str(exc), exc
    else:
        raise AssertionError('应当报错')


def test_axis_ranges_rejected():
    for bad in ({'deadzone': 1.2}, {'curve': 0}, {'smoothing': 2},
                {'max_hz': 100}, {'threshold': 1.5}):
        try:
            profile({'axis': {'lx': dict({'target': 'mouse:x'}, **bad)}})
        except ConfigError:
            pass
        else:
            raise AssertionError('应当拒绝 %r' % (bad,))


def test_mouse_move_is_not_a_button_target():
    try:
        profile({'a': 'mouse:x'})
    except ConfigError as exc:
        assert '未知鼠标键' in str(exc), exc
    else:
        raise AssertionError('应当报错')


def test_axis_must_not_be_toggle():
    try:
        profile({'axis': {'lx': {'target': 'toggle:gyro'}}})
    except ConfigError as exc:
        assert '不能用在 axis 下' in str(exc), exc
    else:
        raise AssertionError('应当报错')


def test_keypair_requires_both_sides():
    try:
        profile({'axis': {'lx': {'target': 'keypair', 'pos': 'key:d'}}})
    except ConfigError as exc:
        assert '缺少 neg' in str(exc), exc
    else:
        raise AssertionError('应当报错')


def test_duplicate_modifier_rejected():
    try:
        profile({'a': 'key:ctrl+leftctrl+space'})
    except ConfigError as exc:
        assert '重复' in str(exc), exc
    else:
        raise AssertionError('应当报错')


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------
def test_passthrough_is_verbatim():
    mapper = Mapper(profile({'passthrough': True}))
    state = raw(buttons={'a', 'lb', 'up'},
                axes=(1000, -2000, 3000, -4000), triggers=(10, 20))
    frame = mapper.apply(state, 0.0)
    assert frame.pad.buttons == frozenset({'a', 'lb'}), frame.pad.buttons
    assert frame.pad.hat == (0, -1), frame.pad.hat
    assert frame.pad.axes == (1000, -2000, 3000, -4000), frame.pad.axes
    assert frame.pad.triggers == (10, 20), frame.pad.triggers


def test_button_override_and_pad_target():
    mapper = Mapper(profile({
        'passthrough': True, 'a': 'key:space', 'm1': 'xbox:lb',
    }))
    frame = mapper.apply(raw(buttons={'a', 'b'}, extras={'m1'}), 0.0)
    assert frame.pad.buttons == frozenset({'b', 'lb'}), frame.pad.buttons
    assert frame.keys == frozenset({ecodes.KEY_SPACE}), frame.keys


def test_button_hold_keeps_key_down():
    mapper = Mapper(profile({'a': 'key:space'}))
    first = mapper.apply(raw(buttons={'a'}), 0.0)
    second = mapper.apply(raw(buttons={'a'}), 0.01)
    assert first.keys == frozenset({ecodes.KEY_SPACE})
    assert second.keys == frozenset({ecodes.KEY_SPACE})
    released = mapper.apply(raw(), 0.02)
    assert released.keys == frozenset()


def test_axis_threshold_to_pad_button():
    mapper = Mapper(profile({'axis': {'lt': {
        'target': 'xbox:lb', 'threshold': 0.5,
    }}}))
    high = mapper.apply(raw(triggers=(200, 0)), 0.0)
    assert 'lb' in high.pad.buttons, high.pad.buttons
    low = mapper.apply(raw(triggers=(50, 0)), 0.01)
    assert 'lb' not in low.pad.buttons, low.pad.buttons


def test_key_combo_and_aliases():
    mapper = Mapper(profile({'a': 'key:ctrl+shift+esc'}))
    frame = mapper.apply(raw(buttons={'a'}), 0.0)
    assert frame.keys == frozenset({
        ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_ESC}), frame.keys


def test_button_key_stays_held_until_release():
    """按住手柄键 = 一直按住映射的键盘键，不是只发一次按下。"""
    mapper = Mapper(profile({'a': 'key:space'}))
    down = raw(buttons={'a'})
    assert mapper.apply(down, 0.0).keys == frozenset({ecodes.KEY_SPACE})
    assert mapper.apply(down, 0.5).keys == frozenset({ecodes.KEY_SPACE})
    assert mapper.apply(down, 2.0).keys == frozenset({ecodes.KEY_SPACE})
    assert mapper.apply(raw(buttons=set()), 2.1).keys == frozenset()


def test_mouse_button_and_wheel_edge():
    mapper = Mapper(profile({'lb': 'mouse:left', 'rb': 'wheel:up'}))
    frame = mapper.apply(raw(buttons={'lb', 'rb'}), 0.0)
    assert frame.mouse == frozenset({ecodes.BTN_LEFT}), frame.mouse
    assert frame.wheel == 1, frame.wheel
    again = mapper.apply(raw(buttons={'lb', 'rb'}), 0.01)
    assert again.wheel == 0, again.wheel


def test_dpad_buttons_are_not_passed_when_mapped():
    mapper = Mapper(profile({'passthrough': True, 'up': 'key:w'}))
    frame = mapper.apply(raw(buttons={'up', 'down'}), 0.0)
    assert frame.pad.hat == (0, 1), frame.pad.hat      # 只剩 down
    assert frame.keys == frozenset({ecodes.KEY_W})


def test_dpad_frame_reaches_key_targets():
    """十字键在报文里只有标准按钮字 + hat；映射层要能拿到 up/down/…"""
    data = bytearray(32)
    data[1] = 0x14
    struct.pack_into('<H', data, 2, 0x01)              # 十字 ↑
    state = translate(bytes(data))
    assert state.buttons == frozenset({'up'}), state.buttons
    assert state.hat == (0, -1), state.hat
    mapper = Mapper(profile({'dpad_up': 'key:up'}))
    frame = mapper.apply(state, 0.0)
    assert frame.keys == frozenset({ecodes.KEY_UP}), frame.keys
    assert frame.pad.hat == (0, 0), frame.pad.hat       # 非透传不写 hat


# ---------------------------------------------------------------------------
# 轴
# ---------------------------------------------------------------------------
def test_axis_direction_is_xinput_style():
    state = raw(axes=(0, -20000, 0, -1))
    assert axis_norm('ly', state) > 0.5                 # 向上为正


def test_keypair_and_deadzone():
    mapper = Mapper(profile({'axis': {'lx': {
        'target': 'keypair', 'neg': 'key:a', 'pos': 'key:d',
        'deadzone': 0.3,
    }}}))
    assert mapper.apply(raw(axes=(20000, 0, 0, 0)), 0.0).keys == \
        frozenset({ecodes.KEY_D})
    assert mapper.apply(raw(axes=(100, 0, 0, 0)), 0.01).keys == frozenset()
    assert mapper.apply(raw(axes=(-20000, 0, 0, 0)), 0.02).keys == \
        frozenset({ecodes.KEY_A})


def test_mouse_move_accumulates_and_inverts():
    mapper = Mapper(profile({'axis': {
        'ly': {'target': 'mouse:y', 'invert': True, 'deadzone': 0.0,
               'max_speed': 1000},
    }}))
    state = raw(axes=(0, -30000, 0, -1))                # 向上推
    assert mapper.apply(state, 0.0).dy == 0
    frame = mapper.apply(state, 0.02)
    assert frame.dy < 0, frame.dy                      # 鼠标向上 = 负
    assert frame.pad.axes[1] == -1, frame.pad.axes     # 已接管，手柄回中


def test_horizontal_wheel_button():
    mapper = Mapper(profile({'lb': 'wheel:left', 'rb': 'wheel:right'}))
    left = mapper.apply(raw(buttons={'lb'}), 0.0)
    assert left.wheel == 0 and left.hwheel == -1, (left.wheel, left.hwheel)
    right = mapper.apply(raw(buttons={'rb'}), 0.01)
    assert right.wheel == 0 and right.hwheel == 1, (right.wheel, right.hwheel)


def test_axis_wheel_auto_directions():
    def wheel_step(axis, axes):
        mapper = Mapper(profile({'axis': {axis: {
            'target': 'wheel', 'deadzone': 0.0,
            'max_hz': 20, 'curve': 1.0,
        }}}))
        mapper.apply(raw(axes=axes), 0.0)
        return mapper.apply(raw(axes=axes), 0.1)

    # rx 正 → 水平滚轮右；rx 负 → 左
    frame = wheel_step('rx', (0, -1, 32767, -1))
    assert frame.hwheel > 0 and frame.wheel == 0, frame
    frame = wheel_step('rx', (0, -1, -32768, -1))
    assert frame.hwheel < 0 and frame.wheel == 0, frame
    # ry 正 → 垂直滚轮上；ry 负 → 下（axis_norm 向上为正）
    frame = wheel_step('ry', (0, -1, 0, -32768))
    assert frame.wheel > 0 and frame.hwheel == 0, frame
    frame = wheel_step('ry', (0, -1, 0, 32767))
    assert frame.wheel < 0 and frame.hwheel == 0, frame


def test_axis_wheel_auto_defaults_proportional():
    auto = parse_profile('t', {'axis': {
        'rx': {'target': 'wheel'},
        'ry': {'target': 'wheel:y'},
    }})
    assert auto.axes['rx'].max_hz == 20
    assert auto.axes['ry'].max_hz == 20
    fixed = parse_profile('t', {'axis': {
        'rx': {'target': 'wheel', 'threshold': 0.5, 'repeat_hz': 10},
    }})
    assert fixed.axes['rx'].max_hz == 0


def test_wheel_high_res_smooth_slow():
    mapper = Mapper(profile({'axis': {'rx': {
        'target': 'wheel:x', 'deadzone': 0.0,
        'max_hz': 1, 'curve': 1.0,
    }}}))
    state = raw(axes=(0, -1, 32767, -1))
    mapper.apply(state, 0.0)
    hi = normal = 0
    for i in range(1, 13):
        frame = mapper.apply(state, i * 0.1)
        hi += frame.hwheel_hi
        normal += frame.hwheel
    # 1 格/秒 × 1.2 秒 ≈ 144 高分辨率单位 = 1 个标准格
    assert 120 <= hi <= 160, hi
    assert normal == 1, normal


def test_wheel_threshold_repeat_and_hysteresis():
    mapper = Mapper(profile({'axis': {'lt': {
        'target': 'wheel:up', 'threshold': 0.5,
        'release_threshold': 0.4, 'repeat_hz': 10,
    }}}))
    high = raw(triggers=(200, 0))
    low = raw(triggers=(90, 0))
    assert mapper.apply(high, 0.0).wheel == 1
    assert mapper.apply(high, 0.05).wheel == 0
    assert mapper.apply(high, 0.11).wheel == 1
    assert mapper.apply(low, 0.12).wheel == 0
    assert mapper.apply(high, 0.2).wheel == 1


def test_wheel_is_proportional():
    def run(depth, frames=10, dt=0.1):
        mapper = Mapper(profile({'axis': {'lt': {
            'target': 'wheel:up', 'deadzone': 0.1,
            'max_hz': 20, 'curve': 1.0,
        }}}))
        trig = int(depth * 255)
        mapper.apply(raw(triggers=(trig, 0)), 0.0)
        return sum(mapper.apply(raw(triggers=(trig, 0)),
                                i * dt).wheel for i in range(1, frames + 1))

    full = run(1.0)          # 20 格/秒 × 1 秒
    half = run(128 / 255)
    light = run(51 / 255)
    assert 19 <= full <= 20, full
    assert 8 <= half <= 9, half
    assert 2 <= light <= 3, light
    assert full > half > light


def test_proportional_wheel_stops_below_deadzone():
    mapper = Mapper(profile({'axis': {'lt': {
        'target': 'wheel:up', 'deadzone': 0.2,
        'max_hz': 30, 'curve': 1.0,
    }}}))
    mapper.apply(raw(triggers=(255, 0)), 0.0)
    assert mapper.apply(raw(triggers=(255, 0)), 0.1).wheel > 0
    assert mapper.apply(raw(triggers=(20, 0)), 0.2).wheel == 0


def test_pad_axis_target():
    mapper = Mapper(profile({'axis': {'lt': {'target': 'xbox:abs_z'}}}))
    frame = mapper.apply(raw(triggers=(128, 0)), 0.0)
    assert frame.pad.triggers[0] == 128, frame.pad.triggers


# ---------------------------------------------------------------------------
# 组
# ---------------------------------------------------------------------------
def test_toggle_group_releases_members():
    mapper = Mapper(profile({
        'home': 'toggle:gyro',
        'groups': {'gyro': False},
        'axis': {'lt': {
            'target': 'mouse:left', 'group': 'gyro', 'threshold': 0.3,
        }},
    }))
    high = raw(triggers=(200, 0))
    assert mapper.apply(high, 0.0).mouse == frozenset()   # 组关
    frame = mapper.apply(raw(buttons={'home'}, triggers=(200, 0)), 0.01)
    assert frame.toggled == (('gyro', True),), frame.toggled
    assert frame.mouse == frozenset({ecodes.BTN_LEFT}), frame.mouse
    # 按住 home 不会再次翻转
    frame = mapper.apply(raw(buttons={'home'}, triggers=(200, 0)), 0.02)
    assert frame.toggled == (), frame.toggled


# ---------------------------------------------------------------------------
# 输出 / 切换
# ---------------------------------------------------------------------------
def test_commit_to_devices():
    devs = FakeDevices()
    mapper = Mapper(profile({'passthrough': True}), devices=devs)
    mapper.apply(raw(buttons={'a'}), 0.0)
    assert devs.pad.calls and devs.keyboard.calls and devs.mouse.calls


def test_profile_switch_resets_state():
    devs = FakeDevices()
    gamepad = parse_profile('gamepad', {'passthrough': True})
    desktop = parse_profile('desktop', {'a': 'key:space'})
    mapper = Mapper(gamepad, devices=devs)
    mapper.apply(raw(buttons={'a'}), 0.0)
    mapper.set_profile(desktop)
    frame = mapper.apply(raw(buttons={'a'}), 0.01)
    assert frame.keys == frozenset({ecodes.KEY_SPACE}), frame.keys
    assert frame.pad.buttons == frozenset(), frame.pad.buttons


def test_gate_errors_helper_reports_all():
    errors = gate_errors({'daemon': {'slot_profiles': ['a']},
                          'profile': {'a': {'a': 'key:bad'}}})
    assert any('slot_profiles' in e for e in errors), errors
    assert any('未知按键名' in e for e in errors), errors


def test_mute_shows_as_mute():
    """113 同时叫 MIN_INTERESTING 和 MUTE，显示统一用 mute。"""
    assert format_target(parse_button_target('key:mute')) == 'key:mute'
    assert format_target(parse_button_target('key:min_interesting')) == \
        'key:mute'


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
