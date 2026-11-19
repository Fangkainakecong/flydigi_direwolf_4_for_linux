#!/usr/bin/env python3
"""FF_RUMBLE 震动透传探针：虚拟手柄 → 冰原狼4

背景
----
独占 dongle 以后，游戏看到的是 daemon 造的虚拟手柄，震动不再经过 xpad。
daemon 必须接收虚拟手柄的 FF 上传/播放事件，再把强弱力度翻译成厂商
rumble 报文（EP 0x05，12 字节），否则游戏里所有震动都会消失。

已实测可用报文（flydigi_2.4g_usbfs_probe.py 阶段 C）::

    00 01 0F C0 00 FF 00 FF 00 00 00 00

`[3]` / `[5]` / `[7]` 各对应哪个马达、是否线性还没定。本脚本做两件事：

1. 建虚拟 Xbox 手柄，按 fftest / 游戏的强度发厂商报文，测延迟；
2. `--sweep` 逐档扫描字节，让你回答"哪边在震"，把结论写进 JSON。

实现要点（全部实测踩过）
------------------------
* `EV_UINPUT`（FF 上传/擦除通知）在 **uinput 的 fd** `UInput.fd` 上，
  不在 evdev 节点上。用 `select` + `os.read` 读 24 字节 `struct input_event`。
* 客户端 `upload_effect()` 会阻塞到 daemon 调用 `end_upload()`；python-evdev
  的这个 C 调用期间不释放 GIL，所以**同一个进程里既当客户端又当 daemon
  会死锁**。测试时客户端必须放到子进程里（真实场景本来就是两个进程）。
* `Effect.u` 在 python-evdev 1.4 里叫 `ff_rumble_effect`，新版本可能叫
  `rumble`，这里两个都兼容。

用法
----
    # 只建虚拟设备，配合 sudo fftest /dev/input/eventN 手动测
    sudo python3 scripts/flydigi_ff_probe.py --fftest

    # 虚拟手柄 + 真实手柄，自动播放四档强度并测延迟
    sudo python3 scripts/flydigi_ff_probe.py --pad

    # 扫描厂商报文里各字节的作用（需要人听/摸反馈）
    sudo python3 scripts/flydigi_ff_probe.py --pad --sweep

产物
----
`captures/ff_<时间戳>.json`：上传的效果、播放、延迟统计、扫描结论。
"""

import argparse
import json
import multiprocessing as mp
import os
import queue
import select
import struct
import sys
import threading
import time

sys.dont_write_bytecode = True          # 见 blob_audit：别让 root 写 __pycache__

try:
    from evdev import UInput, ecodes, ff
except ImportError:
    sys.exit('缺少 python3-evdev。sudo apt install python3-evdev')

from flydigi_usb import Link, Pad, find_pad
from flydigi_uinput_ident_probe import PAD_CAPS, scan_event_nodes

PAD_VID, PAD_PID, PAD_VERSION = 0x045E, 0x028E, 0x0114
PAD_NAME = 'Microsoft X-Box 360 pad'
EP_OUT = 0x05
SEND_HZ = 50
INPUT_EVENT = struct.Struct('<qqHHi')     # 64 位 struct input_event


def rumble_packet(strong, weak, enable_byte=7, strong_byte=3, weak_byte=5,
                  template=None):
    """按探针测得的模板拼 12 字节 rumble 包。strong/weak 是 0..255。"""
    packet = bytearray(template or
                       bytes([0x00, 0x01, 0x0F, 0xC0, 0x00, 0xFF,
                              0x00, 0xFF, 0x00, 0x00, 0x00, 0x00]))
    packet[strong_byte] = strong & 0xFF
    packet[weak_byte] = weak & 0xFF
    if enable_byte is not None:
        packet[enable_byte] = 0xFF
    return bytes(packet)


def field_of(obj, *names):
    """python-evdev 1.4 用 ff_replay/ff_trigger，新版本用 replay/trigger。"""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def rumble_of(effect):
    """兼容 python-evdev 1.4 / 1.7 的 union 字段名。"""
    for name in ('ff_rumble_effect', 'rumble'):
        sub = getattr(effect.u, name, None)
        if sub is not None:
            return sub.strong_magnitude, sub.weak_magnitude
    return 0, 0


class FFHub(object):
    """读 `UInput.fd`：处理上传/擦除，把播放/停止事件放进队列。"""

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
                r, _, _ = select.select([fd], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not r:
                continue
            try:
                data = os.read(fd, INPUT_EVENT.size * 16)
            except BlockingIOError:
                continue
            for off in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                _, _, etype, code, value = INPUT_EVENT.unpack_from(data, off)
                self._handle(etype, code, value)

    def _handle(self, etype, code, value):
        if etype == ecodes.EV_UINPUT:
            if code == ecodes.UI_FF_UPLOAD:
                try:
                    up = self.ui.begin_upload(value)
                except Exception as e:          # noqa: BLE001
                    self.errors.append('begin_upload: %r' % (e,))
                    return
                eff = up.effect
                replay = field_of(eff, 'replay', 'ff_replay')
                params = {'id': eff.id, 'type': eff.type,
                          'length_ms': replay.length if replay else 0,
                          'delay_ms': replay.delay if replay else 0}
                if eff.type == ecodes.FF_RUMBLE:
                    params['strong'], params['weak'] = rumble_of(eff)
                self.effects[eff.id] = params
                up.retval = 0
                try:
                    self.ui.end_upload(up)
                except Exception as e:          # noqa: BLE001
                    self.errors.append('end_upload: %r' % (e,))
                self.queue.put(('upload', params))
            elif code == ecodes.UI_FF_ERASE:
                try:
                    er = self.ui.begin_erase(value)
                    self.effects.pop(er.effect_id, None)
                    self.ui.end_erase(er)
                except Exception as e:          # noqa: BLE001
                    self.errors.append('erase: %r' % (e,))
                self.queue.put(('erase', {'id': value}))
            return
        if etype == ecodes.EV_FF:
            self.queue.put(('play', {'id': code, 'value': value,
                                     't': time.time()}))
            if value == 0:
                self.queue.put(('stop', {'id': code}))

    def stop(self):
        self.running = False
        self.thread.join(timeout=0.5)


class RumbleSender(object):
    def __init__(self, link=None, **packet_kw):
        self.link = link
        self.kw = packet_kw
        self.active = None          # (strong255, weak255, end_time)
        self.last_sent = 0.0
        self.latencies = []
        self.first_latency = None
        self.started_at = 0.0
        self.sent = 0

    def play(self, strong, weak, length_ms, repeat):
        reps = max(1, int(repeat))
        end = time.time() + max(60, length_ms or 100) / 1000.0 * reps
        self.active = (strong, weak, end)
        self.started_at = time.time()
        self.first_latency = None

    def stop(self):
        self.active = None
        self._send(0, 0)

    def _send(self, strong, weak):
        if self.link is None:
            return
        t0 = time.time()
        self.link.send(rumble_packet(strong, weak, **self.kw), wait=0.05)
        self.latencies.append(time.time() - t0)
        self.sent += 1

    def tick(self):
        if not self.active:
            return
        strong, weak, end = self.active
        now = time.time()
        if now >= end:
            self.active = None
            self._send(0, 0)
            return
        if now - self.last_sent >= 1.0 / SEND_HZ:
            self.last_sent = now
            self._send(strong, weak)
            if self.first_latency is None:
                self.first_latency = now - self.started_at


def resolve_node(ui):
    if ui.device is not None:
        return ui.device.path
    found = scan_event_nodes({PAD_NAME})
    return (found.get(PAD_NAME) or [None])[0]


def client_process(node, cases, stamps):
    """子进程：扮演游戏，上传并播放 FF。

    必须独立进程：客户端 upload_effect() 阻塞期间不释放 GIL，和 daemon
    在同一个进程里会互相等死。
    """
    from evdev import InputDevice, ff as effmod
    dev = InputDevice(node)
    for i, (strong, weak, length) in enumerate(cases):
        eff = effmod.Effect(
            ecodes.FF_RUMBLE, -1, 0,
            effmod.Trigger(0, 0),
            effmod.Replay(length, 0),
            effmod.EffectType(ff_rumble_effect=effmod.Rumble(
                strong_magnitude=strong, weak_magnitude=weak)))
        eid = dev.upload_effect(eff)
        time.sleep(0.05)
        stamps[i] = time.time()
        dev.write(ecodes.EV_FF, eid, 1)
        dev.write(ecodes.EV_SYN, 0, 0)
        time.sleep(length / 1000.0 + 0.1)
        dev.write(ecodes.EV_FF, eid, 0)
        dev.write(ecodes.EV_SYN, 0, 0)
        dev.erase_effect(eid)
        time.sleep(0.2)
    dev.close()


def playwright(ui, hub, sender, cases):
    """父进程：处理 FF 事件，把播放翻译成厂商报文并测延迟。"""
    node = resolve_node(ui)
    if not node:
        raise SystemExit('找不到虚拟手柄节点（需要 root 才能读 event 设备）')
    print('虚拟手柄节点: %s' % node)
    stamps = mp.Array('d', len(cases))
    proc = mp.Process(target=client_process, args=(node, cases, stamps))
    proc.start()
    results = []
    seen = 0
    deadline = time.time() + len(cases) * 3.0 + 10.0
    while (proc.is_alive() or seen < len(cases)) and time.time() < deadline:
        try:
            kind, data = hub.queue.get(timeout=0.01)
        except queue.Empty:
            sender.tick()
            continue
        if kind == 'upload':
            print('  上传: id=%d type=%d strong=%s weak=%s len=%sms'
                  % (data['id'], data['type'], data.get('strong'),
                     data.get('weak'), data['length_ms']))
        elif kind == 'play' and data['value']:
            info = hub.effects.get(data['id'], {})
            strong = int(info.get('strong', 0) * 255 / 65535)
            weak = int(info.get('weak', 0) * 255 / 65535)
            sender.play(strong, weak, info.get('length_ms', 0), data['value'])
            got = None
            idx = seen
            if idx < len(stamps) and stamps[idx]:
                got = (data['t'] - stamps[idx]) * 1000
            results.append({
                'effect_id': data['id'],
                'strong_u16': info.get('strong'),
                'weak_u16': info.get('weak'),
                'strong255': strong,
                'weak255': weak,
                'length_ms': info.get('length_ms'),
                'client_to_daemon_ms': round(got, 3) if got is not None else None,
            })
            print('  播放: id=%d strong=%d weak=%d，客户端到 daemon %.2f ms'
                  % (data['id'], strong, weak, got if got is not None else -1))
            seen += 1
        elif kind == 'play':
            sender.stop()
        sender.tick()
    proc.join(timeout=5)
    if proc.is_alive():
        proc.terminate()
    sender.stop()
    return results


def pump_link(link, seconds):
    """等用户输入时保持 IN URB 在途，避免手柄输入缓冲溢出。"""
    end = time.time() + seconds
    while time.time() < end:
        got = link.pad.reap()
        if got is None:
            time.sleep(0.002)
            continue
        ctx, st, d = got
        if ctx in link.in_ids:
            link.in_ids.discard(ctx)
            link.pump(1)


def sweep(sender):
    """扫描厂商报文各字节，让人回答哪边在震。"""
    print('\n=== 报文扫描 ===')
    print('每个强度 1.2 秒。听/摸手柄，回答：1=左 2=右 3=两边 0=没感觉')
    print('（通常左握把是主马达，右握把是细马达）')
    results = []
    trials = [
        ('baseline', dict(strong=192, weak=255)),
        ('strong_0', dict(strong=0, weak=255)),
        ('strong_64', dict(strong=64, weak=255)),
        ('strong_128', dict(strong=128, weak=255)),
        ('strong_255', dict(strong=255, weak=255)),
        ('weak_0', dict(strong=255, weak=0)),
        ('weak_64', dict(strong=255, weak=64)),
        ('weak_128', dict(strong=255, weak=128)),
        ('both_0', dict(strong=0, weak=0)),
    ]
    for label, kw in trials:
        packet = rumble_packet(**kw, **sender.kw)
        sender.link.send(packet, wait=0.05)
        pump_link(sender.link, 0.3)
        print('  %-10s %s' % (label, packet.hex(' ')), end='  ')
        try:
            ans = input('哪边震？[1/2/3/0] ').strip()
        except EOFError:
            ans = ''
        results.append({'label': label, 'packet': packet.hex(' '),
                        'answer': ans})
        pump_link(sender.link, 0.2)
        sender.link.send(rumble_packet(0, 0, **sender.kw), wait=0.05)
    return results


def run_fftest(ui, hub, seconds):
    node = resolve_node(ui) or '/dev/input/eventN'
    print('虚拟手柄已就绪：%s' % node)
    print('另开一个终端： sudo fftest %s' % node)
    print('保持 %.0f 秒，所有 FF 事件会打印在下面。' % seconds)
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            kind, data = hub.queue.get(timeout=0.2)
        except queue.Empty:
            continue
        print('  [%.2fs] %s %s' % (time.time() - t0, kind, data))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pad', action='store_true', help='连真实手柄，发厂商 rumble')
    ap.add_argument('--fftest', action='store_true', help='保持虚拟设备等 fftest')
    ap.add_argument('--sweep', action='store_true', help='扫描厂商报文字节')
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--strong-byte', type=int, default=3)
    ap.add_argument('--weak-byte', type=int, default=5)
    ap.add_argument('--enable-byte', type=int, default=7)
    ap.add_argument('--out', default='captures')
    args = ap.parse_args()

    if not (args.pad or args.fftest or args.sweep):
        ap.error('至少选一个：--fftest / --pad / --sweep')

    ui = UInput(PAD_CAPS, name=PAD_NAME, version=PAD_VERSION,
                vendor=PAD_VID, product=PAD_PID, bustype=ecodes.BUS_USB)
    hub = FFHub(ui)
    result = {'time': time.strftime('%Y%m%d-%H%M%S'),
              'map': {'strong_byte': args.strong_byte,
                      'weak_byte': args.weak_byte,
                      'enable_byte': args.enable_byte},
              'events': [], 'play': [], 'errors': []}

    pad = None
    claimed = False
    link = None
    sender = RumbleSender(**{'strong_byte': args.strong_byte,
                             'weak_byte': args.weak_byte,
                             'enable_byte': args.enable_byte})
    try:
        if args.pad or args.sweep:
            dev = find_pad()
            if not dev:
                sys.exit('未找到手柄 (045e:028e)')
            pad = Pad(dev)
            print('解绑驱动: %s' % pad.takeover())
            claimed = True
            link = Link(pad)
            link.drain(0.2)
            sender.link = link
            print('真实手柄已接管：%s' % dev)

        if args.sweep:
            result['sweep'] = sweep(sender)
        elif args.pad:
            result['play'] = playwright(ui, hub, sender, [
                (0xFFFF, 0xFFFF, 500),
                (0xFFFF, 0x0000, 500),
                (0x0000, 0xFFFF, 500),
                (0x8000, 0x4000, 400),
            ])

        if args.fftest:
            run_fftest(ui, hub, args.seconds)
        elif args.pad or args.sweep:
            print('\n保持 %.0f 秒，接受 fftest/游戏的实时震动。' % args.seconds)
            t0 = time.time()
            while time.time() - t0 < args.seconds:
                if link:
                    pump_link(link, 0.005)
                try:
                    kind, data = hub.queue.get(timeout=0.005)
                except queue.Empty:
                    pass
                else:
                    if kind == 'upload':
                        result['events'].append(['upload', data])
                    elif kind == 'play':
                        result['events'].append(['play', data])
                        if data['value'] == 0:
                            sender.stop()
                        else:
                            info = hub.effects.get(data['id'], {})
                            if info.get('type') == ecodes.FF_RUMBLE:
                                sender.play(
                                    int(info['strong'] * 255 / 65535),
                                    int(info['weak'] * 255 / 65535),
                                    info['length_ms'], data['value'])
                sender.tick()

        result['sent_packets'] = sender.sent
        result['usb_out_latency_ms'] = {
            'n': len(sender.latencies),
            'avg': round(sum(sender.latencies) / len(sender.latencies) * 1000, 3)
            if sender.latencies else None,
            'max': round(max(sender.latencies) * 1000, 3)
            if sender.latencies else None,
        }
        result['errors'] = hub.errors
        print('\nUSB OUT 次数=%d 平均延迟=%s ms'
              % (sender.sent, result['usb_out_latency_ms']['avg']))
        if hub.errors:
            print('FF 错误: %s' % hub.errors[:3])
    except KeyboardInterrupt:
        print('\n中断，收尾…')
    finally:
        hub.stop()
        try:
            ui.close()
        except Exception:                       # noqa: BLE001
            pass
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        if pad:
            pad.reconnect_driver()
            pad.close()
            print('xpad 已恢复')

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'ff_%s.json' % result['time'])
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('已写 %s' % path)


if __name__ == '__main__':
    main()
