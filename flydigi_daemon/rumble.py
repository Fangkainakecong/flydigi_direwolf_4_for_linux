"""游戏 FF_RUMBLE → 冰原狼4 厂商 rumble 报文

独占 dongle 后 xpad 被解绑，内核不再替我们处理 `/dev/input/event*` 上的 FF。
所以虚拟手柄声明 `EV_FF`/`FF_RUMBLE`，daemon 从 `UInput.fd` 读
`UI_FF_UPLOAD` / `EV_FF`，再把强弱力度翻译成 EP 0x05 的 12 字节包。

报文模板/字节位置来自 `scripts/flydigi_ff_probe.py` 的实测。
"""

import os
import queue
import select
import struct
import threading
import time

from evdev import ecodes

from .usb import EP_OUT, URB_INT

INPUT_EVENT = struct.Struct('<qqHHi')
# Xbox 360 标准 rumble 输出包，和 xpad 的 XTYPE_XBOX360 一样：
#   [0]=0x00 [1]=0x08 [2]=0x00 [3]=左马达 [4]=右马达 [5..7]=0
# Direwolf 4 在 PC 模式就是 045e:028e XTYPE_XBOX360，dongle 认这个包。
# 旧模板 00 01 0f c0 ... 只会让 OUT URB 完成，实际不驱动马达。
DEFAULT_TEMPLATE = bytes.fromhex('00 08 00 00 00 00 00 00')


def _field(obj, *names):
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _rumble_of(effect):
    for name in ('ff_rumble_effect', 'rumble'):
        sub = getattr(effect.u, name, None)
        if sub is not None:
            return sub.strong_magnitude, sub.weak_magnitude
    return 0, 0


def to_byte(value):
    """0..65535 → 0..255。"""
    return max(0, min(255, int(value) >> 8))


def rumble_packet(strong, weak, template=DEFAULT_TEMPLATE,
                  strong_byte=3, weak_byte=4, enable_byte=-1):
    packet = bytearray(template)
    packet[strong_byte] = strong & 0xFF
    packet[weak_byte] = weak & 0xFF
    if enable_byte is not None and enable_byte >= 0:
        packet[enable_byte] = 0xFF
    return bytes(packet)


class FFHub(object):
    """线程：读 UInput.fd，处理上传/擦除，把 play/stop 放进队列。"""

    def __init__(self, uinput):
        self.ui = uinput
        self.queue = queue.Queue()
        self.effects = {}
        self.errors = []
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        fd = self.ui.fd
        while self.running:
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                data = os.read(fd, INPUT_EVENT.size * 16)
            except (BlockingIOError, OSError):
                continue
            for off in range(0, len(data) - INPUT_EVENT.size + 1,
                             INPUT_EVENT.size):
                _, _, etype, code, value = INPUT_EVENT.unpack_from(data, off)
                self._handle(etype, code, value)

    def _handle(self, etype, code, value):
        if etype == ecodes.EV_UINPUT:
            if code == ecodes.UI_FF_UPLOAD:
                try:
                    up = self.ui.begin_upload(value)
                    eff = up.effect
                    replay = _field(eff, 'replay', 'ff_replay')
                    item = {'id': eff.id, 'type': eff.type,
                            'length_ms': replay.length if replay else 0}
                    if eff.type == ecodes.FF_RUMBLE:
                        item['strong'], item['weak'] = _rumble_of(eff)
                    self.effects[eff.id] = item
                    up.retval = 0
                    self.ui.end_upload(up)
                except Exception as exc:        # noqa: BLE001
                    self.errors.append('upload: %r' % (exc,))
            elif code == ecodes.UI_FF_ERASE:
                try:
                    er = self.ui.begin_erase(value)
                    self.effects.pop(er.effect_id, None)
                    self.ui.end_erase(er)
                except Exception as exc:        # noqa: BLE001
                    self.errors.append('erase: %r' % (exc,))
            return
        if etype == ecodes.EV_FF:
            self.queue.put(('play', {'id': code, 'value': value}))

    def stop(self):
        self.running = False
        try:
            self.thread.join(timeout=0.5)
        except RuntimeError:
            pass


class Rumble(object):
    """主循环里轮询 FF 队列，并定时向物理手柄发 rumble 包。"""

    def __init__(self, uinput, pad, cfg=None):
        cfg = cfg or {}
        self.pad = pad
        self.hub = FFHub(uinput)
        try:
            template = bytes.fromhex(
                str(cfg.get('template', '')).replace(' ', ''))
            if len(template) < 8:
                template = DEFAULT_TEMPLATE
        except ValueError:
            template = DEFAULT_TEMPLATE
        self.template = template
        self.strong_byte = int(cfg.get('strong_byte', 3))
        self.weak_byte = int(cfg.get('weak_byte', 4))
        self.enable_byte = int(cfg.get('enable_byte', -1))
        self.interval = 1.0 / max(5.0, float(cfg.get('rate_hz', 50)))
        self.active = None          # (strong, weak, end_time)
        self.last_sent = 0.0
        self.errors = 0

    def _send(self, strong, weak):
        packet = rumble_packet(strong, weak, self.template,
                              self.strong_byte, self.weak_byte,
                              self.enable_byte)
        try:
            self.pad.submit(URB_INT, EP_OUT, packet, len(packet))
        except OSError as exc:
            self.errors += 1
            if self.errors <= 5:
                print('震动包发送失败: %r' % (exc,))

    def pulse(self, strong=255, weak=200, ms=250):
        """切配置/开体感时的短震动；会覆盖当前游戏震动。"""
        print('震动: %d/%d %dms' % (strong, weak, ms))
        self.active = (strong, weak, time.time() + ms / 1000.0)
        self.last_sent = 0.0
        self._send(strong, weak)

    def stop(self):
        self.active = None
        self._send(0, 0)

    def poll(self, now=None):
        now = time.time() if now is None else now
        while True:
            try:
                kind, data = self.hub.queue.get_nowait()
            except queue.Empty:
                break
            if kind != 'play':
                continue
            if data['value'] == 0:
                # 停止事件可能发生在 erase 之后，所以先处理 value=0。
                print('FF 停止: id=%s' % data['id'])
                self.stop()
                continue
            effect = self.hub.effects.get(data['id'])
            if not effect:
                print('FF 播放: 找不到 effect %s' % data['id'])
                continue
            if effect.get('type') != ecodes.FF_RUMBLE:
                continue
            repeat = data['value']
            length = max(60, effect.get('length_ms') or 100) / 1000.0
            if repeat < 0:
                end = now + 3600
            else:
                end = now + length * max(1, repeat)
            strong = to_byte(effect.get('strong', 0))
            weak = to_byte(effect.get('weak', 0))
            print('FF 播放: id=%s 次数=%s 时长=%dms 强度=%d/%d'
                  % (data['id'], repeat, length * 1000, strong, weak))
            self.active = (strong, weak, end)
            self.last_sent = 0.0

        if not self.active:
            return
        strong, weak, end = self.active
        if now >= end:
            self.stop()
            return
        if now - self.last_sent >= self.interval:
            self.last_sent = now
            self._send(strong, weak)

    def close(self):
        self.stop()
        self.hub.stop()
