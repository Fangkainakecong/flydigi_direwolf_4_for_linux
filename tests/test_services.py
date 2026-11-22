#!/usr/bin/env python3
"""设备选择 / 空闲判定 / 自动启停相关回归"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon import config, usb               # noqa: E402
from flydigi_daemon.translate import significant_change  # noqa: E402
from flydigi_daemon.translate import PadState        # noqa: E402


def st(buttons=(), axes=(0, -1, 0, -1), triggers=(0, 0),
       extras=(), gyro=(0, 0, 0)):
    return PadState(buttons=frozenset(buttons), axes=axes,
                    triggers=triggers, extras=frozenset(extras),
                    gyro=gyro)


def test_product_hint_selects_only_direwolf():
    other = {'name': '1-1.2', 'product': 'Flydigi 2.4G Android',
             'serial': 'Flydigi 2.4G x360', 'path': '/dev/bus/usb/001/031'}
    wolf = {'name': '2-1', 'product': 'Flydigi Direwolf 4',
            'serial': 'Flydigi Direwolf 4', 'path': '/dev/bus/usb/002/003'}
    chosen, reason = usb.choose_pad([other, wolf], 'Direwolf 4')
    assert chosen is wolf and not reason
    chosen, _ = usb.choose_pad([other], 'Direwolf 4')
    assert chosen is None          # 宁可不接管，也不抓黑武士2
    chosen, _ = usb.choose_pad([other, wolf], '')
    assert chosen is None          # 两个候选且没给 hint：拒绝猜


def test_hint_matching_case_and_spaces():
    assert usb._hint_matches('Flydigi Direwolf 4', 'direwolf 4')
    assert usb._hint_matches('Flydigi  Direwolf  4', 'Direwolf 4')
    assert not usb._hint_matches('Flydigi 2.4G Android', 'Direwolf 4')


def test_xpad_other_interfaces_excludes_target():
    with tempfile.TemporaryDirectory() as tmp:
        usbdev = os.path.join(tmp, '2-1')
        otherdev = os.path.join(tmp, '1-1.2')
        driver = os.path.join(tmp, 'xpad')
        os.makedirs(os.path.join(usbdev, '2-1:1.0'))
        os.makedirs(os.path.join(otherdev, '1-1.2:1.0'))
        os.makedirs(driver)
        os.symlink(os.path.join(usbdev, '2-1:1.0'),
                   os.path.join(driver, '2-1:1.0'))
        os.symlink(os.path.join(otherdev, '1-1.2:1.0'),
                   os.path.join(driver, '1-1.2:1.0'))
        others = usb.xpad_other_interfaces({'sysfs': usbdev}, 0,
                                           driver_root=driver)
        assert others == ['1-1.2:1.0'], others


def test_find_event_nodes_by_usb_path():
    with tempfile.TemporaryDirectory() as tmp:
        usbdev = os.path.join(tmp, '2-1')
        os.makedirs(os.path.join(usbdev, 'input', 'input24'))
        event = os.path.join(tmp, 'input', 'event15')
        os.makedirs(event)
        os.symlink(os.path.join(usbdev, 'input', 'input24'),
                   os.path.join(event, 'device'))
        info = {'sysfs': usbdev}
        nodes = usb.find_event_nodes(info, input_root=os.path.join(tmp,
                                                                   'input'))
        assert nodes == ['/dev/input/event15'], nodes


def test_activity_tolerates_noise_but_counts_motion():
    idle = st()
    assert not significant_change(idle, st(axes=(1, -2, 0, -1)))  # 轴抖
    assert not significant_change(idle, st(gyro=(1, 2, 1)))       # 体感噪声
    assert significant_change(None, idle)
    assert significant_change(idle, st(buttons={'a'}))
    assert significant_change(idle, st(extras={'m1'}))
    assert significant_change(idle, st(axes=(600, -1, 0, -1)))
    assert significant_change(idle, st(triggers=(10, 0)))
    assert significant_change(idle, st(gyro=(20, 0, 0)))


def test_identify_reply_parser():
    d = bytearray(32)
    d[14] = 0xA5
    d[15] = 16
    d[16] = 132
    d[17:21] = b'\xcf\x53\x65\x38'
    d[21] = 0x40
    d[22] = 0x72
    d[23] = 3
    d[25] = 0
    d[27] = 3
    info = usb.info_from_reply(bytes(d))
    assert info['device_type'] == 132
    assert info['battery'] == 3
    assert info['fw'] == '4.0.7.2'
    assert info['slot'] == 3
    d[14] = 0
    assert usb.info_from_reply(bytes(d)) is None


def test_idle_config_default_present():
    cfg = config.load()
    assert int(cfg['daemon']['idle_exit_s']) == 3600
    assert cfg['device']['product_hint'] == 'Direwolf 4'


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
