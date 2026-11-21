#!/usr/bin/env python3
"""M4 回归：体感积分 + 标定数学

    python3 tests/test_m4.py
"""

import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon import calibrate, mapping          # noqa: E402
from flydigi_daemon.calibrate import Sample             # noqa: E402
from flydigi_daemon.translate import PadState, translate  # noqa: E402


def raw(buttons=(), extras=(), gyro=(0, 0, 0)):
    return PadState(buttons=frozenset(buttons), extras=frozenset(extras),
                    axes=(0, -1, 0, -1), triggers=(0, 0), gyro=tuple(gyro))


def profile(data):
    return mapping.parse_profile('t', data)


def samples(values, hz=100.0, start=0.0):
    """values: [(gx,gy,gz), ...]，按 hz 采样。"""
    return [Sample(start + i / hz, tuple(v))
            for i, v in enumerate(values)]


# ---------------------------------------------------------------------------
# 报文 / 轴
# ---------------------------------------------------------------------------
def test_translate_carries_gyro():
    d = bytearray(32)
    d[1] = 0x14
    struct.pack_into('<3b', d, 14, 10, -20, 30)
    state = translate(bytes(d))
    assert state.gyro == (10, -20, 30), state.gyro


def test_axis_norm_gyro_is_raw():
    state = raw(gyro=(5, -6, 7))
    assert mapping.axis_norm('gyro_x', state) == 5
    assert mapping.axis_norm('gyro_z', state) == 7


def test_gyro_rule_must_target_mouse():
    try:
        profile({'axis': {'gyro_x': {'target': 'key:space'}}})
    except mapping.ConfigError as exc:
        assert '体感轴' in str(exc), exc
    else:
        raise AssertionError('应当报错')


# ---------------------------------------------------------------------------
# 积分
# ---------------------------------------------------------------------------
CAL = {'yaw': {'counts_per_degree': 10.0, 'sign': 1},
       'pitch': {'counts_per_degree': 12.0, 'sign': -1}}


def test_gyro_integration_distance():
    p = profile({'axis': {'gyro_x': {
        'target': 'mouse:x', 'sensitivity': 2.0,
        'smoothing': 0.0, 'deadband': 0.0,
    }}})
    mapper = mapping.Mapper(p, calibration=CAL)
    state = raw(gyro=(100, 0, 0))
    mapper.apply(state, 0.0)
    total = 0
    for i in range(1, 101):                       # 1 秒
        total += mapper.apply(state, i * 0.01).dx
    # 100 * 1s / 10 counts/° * 2 px/° = 20 px
    assert 19 <= total <= 20, total


def test_gyro_sign_and_invert():
    p = profile({'axis': {'gyro_x': {'target': 'mouse:x', 'sensitivity': 1,
                                     'smoothing': 0, 'deadband': 0}}})
    cal = {'yaw': {'counts_per_degree': 1.0, 'sign': -1}}
    mapper = mapping.Mapper(p, calibration=cal)
    total = sum(mapper.apply(raw(gyro=(10, 0, 0)), i * 0.01).dx
                for i in range(1, 31))
    assert total < 0, total


def test_gyro_deadband():
    p = profile({'axis': {'gyro_x': {'target': 'mouse:x', 'sensitivity': 10,
                                     'deadband': 5}}})
    mapper = mapping.Mapper(p, calibration=CAL)
    total = sum(mapper.apply(raw(gyro=(4, 0, 0)), i * 0.01).dx
                for i in range(1, 21))
    assert total == 0


def test_gyro_smoothing_halves_first_second():
    def run(smoothing):
        p = profile({'axis': {'gyro_x': {
            'target': 'mouse:x', 'sensitivity': 1,
            'smoothing': smoothing, 'deadband': 0}}})
        mapper = mapping.Mapper(p, calibration=CAL)
        mapper.apply(raw(gyro=(1000, 0, 0)), 0.0)
        return sum(mapper.apply(raw(gyro=(1000, 0, 0)), i * 0.01).dx
                   for i in range(1, 11))

    plain = run(0.0)
    smooth = run(0.9)
    assert plain == 10, plain
    assert smooth < plain * 0.5, (plain, smooth)


def test_gyro_needs_calibration():
    p = profile({'axis': {'gyro_x': {'target': 'mouse:x'}}})
    mapper = mapping.Mapper(p, calibration=None)
    assert mapper.apply(raw(gyro=(100, 0, 0)), 1.0).dx == 0


def test_gyro_group_off_and_toggle():
    p = profile({
        'home': 'toggle:gyro',
        'groups': {'gyro': False},
        'axis': {'gyro_x': {'target': 'mouse:x', 'group': 'gyro',
                            'sensitivity': 1, 'deadband': 0}},
    })
    mapper = mapping.Mapper(p, calibration=CAL)
    assert mapper.apply(raw(gyro=(100, 0, 0)), 1.0).dx == 0
    frame = mapper.apply(raw(buttons={'home'}, gyro=(1000, 0, 0)), 1.01)
    assert frame.toggled == (('gyro', True),)
    moved = sum(mapper.apply(raw(gyro=(1000, 0, 0)), 1.01 + i * 0.01).dx
                for i in range(1, 6))
    assert moved > 0, moved


# ---------------------------------------------------------------------------
# 标定数学
# ---------------------------------------------------------------------------
def test_integrate_and_scale():
    data = samples([(90, 0, 0)] * 400)            # 100 Hz × 4 s
    total, active = calibrate.integrate(data, 0, threshold=2)
    assert abs(total - 360.0) < 1.0, total
    scale = calibrate.scale_from(total, 360)
    assert abs(scale['counts_per_degree'] - 1.0) < 0.01
    assert scale['sign'] == 1


def test_integrate_trims_still_parts():
    data = samples([(0, 0, 0)] * 100 + [(100, 0, 0)] * 100 + [(0, 0, 0)] * 100)
    total, active = calibrate.integrate(data, 0, threshold=5)
    assert abs(total - 100.0) < 1.0, total
    assert abs(active - 1.0) < 0.02, active


def test_build_calibration():
    still = samples([(0, 0, 0)] * 100)
    yaw = samples([(90, 0, 0)] * 400)             # 360°
    pitch = samples([(0, -30, 0)] * 300)          # -90°
    cal = calibrate.build_calibration(still, yaw, pitch)
    assert abs(cal['yaw']['counts_per_degree'] - 1.0) < 0.01
    assert cal['yaw']['sign'] == 1
    assert abs(cal['pitch']['counts_per_degree'] - 1.0) < 0.01
    assert cal['pitch']['sign'] == -1


def test_build_calibration_three_turns_and_pooling():
    still = samples([(0, 0, 0)] * 100)
    yaw = samples([(90, 0, 0)] * 1200)            # 90/s × 12 s = 1080°
    pitch = samples([(0, -30, 0)] * 300)          # -90°
    cal = calibrate.build_calibration(still, yaw, pitch, turns=3)
    assert abs(cal['yaw']['counts_per_degree'] - 1.0) < 0.02
    assert abs(cal['yaw']['measured'] - 1.0) < 0.02
    assert abs(cal['pitch']['measured'] - 1.0) < 0.02
    assert cal['turns'] == 3 and cal['source'] == 'gesture'


def test_constant_calibration():
    cal = calibrate.constant_calibration(0.67, sign_pitch=-1,
                                         source='estimate')
    assert mapping.calibration_axis(cal, 'gyro_x') == {
        'counts_per_degree': 0.67, 'sign': 1.0}
    assert mapping.calibration_axis(cal, 'gyro_y') == {
        'counts_per_degree': 0.67, 'sign': -1.0}
    assert cal['source'] == 'estimate'


def test_build_calibration_rejects_inconsistent():
    still = samples([(0, 0, 0)] * 100)
    yaw = samples([(90, 0, 0)] * 400)             # 1.0
    pitch = samples([(0, -15, 0)] * 300)          # 0.5
    try:
        calibrate.build_calibration(still, yaw, pitch, strict=True)
    except ValueError as exc:
        assert '相差' in str(exc), exc
    else:
        raise AssertionError('--strict 应当拒绝不一致的标定')
    cal = calibrate.build_calibration(still, yaw, pitch)
    # 不一致时用 pitch 的尺度兜底
    assert cal['source'] == 'gesture-pitch'
    assert abs(cal['yaw']['counts_per_degree'] - 0.5) < 0.01
    assert cal['consistency']['fallback'] is True


def test_build_calibration_rejects_wrong_axis():
    still = samples([(0, 0, 0)] * 50)
    yaw = samples([(0, 80, 0)] * 400)             # 转到 pitch 去了
    pitch = samples([(0, -30, 0)] * 300)
    try:
        calibrate.build_calibration(still, yaw, pitch)
    except ValueError as exc:
        assert '主要落在' in str(exc), exc
    else:
        raise AssertionError('应当报错')


def test_calibration_roundtrip_and_lookup():
    cal = {'version': 1, 'yaw': {'counts_per_degree': 3.5, 'sign': -1},
           'pitch': {'counts_per_degree': 4.0, 'sign': 1}}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'cal.json')
        calibrate.write_calibration(path, cal)
        loaded = mapping.load_calibration(path)
        assert loaded == cal
        assert mapping.calibration_axis(loaded, 'gyro_x') == {
            'counts_per_degree': 3.5, 'sign': -1.0}
        assert mapping.calibration_axis(loaded, 'gyro_z') is None
        assert mapping.calibration_axis(None, 'gyro_x') is None
        # 再写一次必须留下 .bak
        calibrate.write_calibration(path, {'version': 2})
        assert os.path.exists(path + '.bak')
        assert mapping.load_calibration(path + '.bak')['version'] == 1
    assert mapping.load_calibration('/no/such/file.json') is None


def test_update_rate():
    data = samples([(i % 10, 0, 0) for i in range(200)], hz=100.0)
    rate = calibrate.update_rate(data)
    assert 95 <= rate <= 100, rate


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
