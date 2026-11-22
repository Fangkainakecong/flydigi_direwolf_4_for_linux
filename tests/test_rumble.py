#!/usr/bin/env python3
"""震动包格式 / FF 常量回归（不建 uinput）"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon.rumble import (DEFAULT_TEMPLATE,  # noqa: E402
                                   Rumble, rumble_packet, to_byte)


class _FakeUI(object):
    def __init__(self):
        self.rfd, self.wfd = os.pipe()
        self.fd = self.rfd


class _FakePad(object):
    def __init__(self):
        self.packets = []

    def submit(self, xfer, ep, data, size):
        self.packets.append(bytes(data))
        return 1


def test_rumble_packet_bytes():
    packet = rumble_packet(0x12, 0x34)
    assert len(packet) == 8
    assert packet[3] == 0x12
    assert packet[4] == 0x34
    assert packet[5:] == b'\x00\x00\x00'
    assert packet[:3] == DEFAULT_TEMPLATE[:3]


def test_strong_weak_scale():
    assert to_byte(0) == 0
    assert to_byte(65535) == 255
    assert to_byte(0x8000) == 128
    assert to_byte(-1) == 0


def test_custom_enable_byte_template():
    template = bytes.fromhex('00 01 0f c0 00 ff 00 ff 00 00 00 00')
    packet = rumble_packet(1, 2, template=template, strong_byte=3,
                           weak_byte=5, enable_byte=7)
    assert packet[3] == 1
    assert packet[5] == 2
    assert packet[7] == 0xFF


def test_pulse_and_playback_via_fake_pad():
    ui = _FakeUI()
    pad = _FakePad()
    rumble = Rumble(ui, pad, {'enabled': True, 'rate_hz': 50})
    try:
        rumble.pulse(200, 120, 50)
        rumble.poll(time.time())
        assert pad.packets and pad.packets[-1][3] == 200
        assert pad.packets[-1][4] == 120
        rumble.stop()
        assert pad.packets[-1][3] == 0 and pad.packets[-1][4] == 0
    finally:
        rumble.close()
        os.close(ui.wfd)


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
