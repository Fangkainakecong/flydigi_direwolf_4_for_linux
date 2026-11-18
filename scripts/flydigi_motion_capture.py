#!/usr/bin/env python3
"""冰原狼4 体感（运动数据）采集 —— 连续录 + 回车打标记，不掐时间

背景：标准 32 字节报文里 `[14..16]` 和 `[21..24]` 在静止时也在变，怀疑是
运动数据，但 2026-11-18 那次是按时间窗采的，分不清"手在动"和"格式没解对"。
本脚本让**你控制节奏**：每个动作"回车开始 → 做 → 回车结束"，数据按标记切片。

流程
----
  A 段（不发 cmd17）：
    1. 平放静止 3 秒（自动）
    2. 拿在手里，原地不动            （回车开始/结束）
    3. 绕竖轴左右各转 45°            （yaw）
    4. 绕横轴前后各倾 45°            （pitch）
    5. 绕前后轴左右各滚 45°          （roll）
  B 段（发厂商 cmd17 之后）：
    6. 平放静止 3 秒（自动）
    7. 快速左右晃 3 秒（自动）

产物
----
    captures/motion_<时间戳>.bin   原始报文（<double ts><uint32 len><data>）
    captures/motion_<时间戳>.json  阶段标记 + 每阶段每字节的范围统计

用法
----
    sudo python3 scripts/flydigi_motion_capture.py
"""
import ctypes
import glob
import json
import os
import select
import struct
import subprocess
import sys
import time

CLAIMINTERFACE = (2 << 30) | (4 << 16) | (ord('U') << 8) | 15
RELEASEINTERFACE = (2 << 30) | (4 << 16) | (ord('U') << 8) | 16
SUBMITURB = (2 << 30) | (56 << 16) | (ord('U') << 8) | 10
REAPURBNDELAY = (1 << 30) | (8 << 16) | (ord('U') << 8) | 13
DISCARDURB = (0 << 30) | (0 << 16) | (ord('U') << 8) | 11
URB_INT = 1
EAGAIN = 11
URB_SIZE = 56
INFLIGHT = 8

_LIBC = ctypes.CDLL(None, use_errno=True)


def _ioctl(fd, req, addr):
    r = _LIBC.ioctl(ctypes.c_int(fd), ctypes.c_ulong(req), ctypes.c_void_p(addr))
    if r == -1:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return r


class CUrb(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ubyte), ('endpoint', ctypes.c_ubyte),
                ('status', ctypes.c_int), ('flags', ctypes.c_uint),
                ('buffer', ctypes.c_void_p), ('buffer_length', ctypes.c_int),
                ('actual_length', ctypes.c_int), ('start_frame', ctypes.c_int),
                ('number_of_packets', ctypes.c_int), ('error_count', ctypes.c_int),
                ('signr', ctypes.c_uint), ('usercontext', ctypes.c_void_p)]


class Pad:
    def __init__(self, devpath):
        self.fd = os.open(devpath, os.O_RDWR)
        self.pending = {}

    def claim(self):
        b = ctypes.create_string_buffer(4)
        _ioctl(self.fd, CLAIMINTERFACE, ctypes.addressof(b))

    def release(self):
        b = ctypes.create_string_buffer(4)
        _ioctl(self.fd, RELEASEINTERFACE, ctypes.addressof(b))

    def submit(self, xfer, ep, data, size):
        buf = ctypes.create_string_buffer(bytes(data), size)
        u = CUrb()
        u.type = xfer
        u.endpoint = ep & 0xFF
        u.buffer = ctypes.cast(buf, ctypes.c_void_p)
        u.buffer_length = size
        u.usercontext = ctypes.c_void_p(id(u))
        self.pending[id(u)] = (u, buf)
        _ioctl(self.fd, SUBMITURB, ctypes.addressof(u))
        return id(u)

    def reap(self):
        p = ctypes.create_string_buffer(8)
        try:
            _ioctl(self.fd, REAPURBNDELAY, ctypes.addressof(p))
        except OSError as e:
            if e.errno == EAGAIN:
                return None
            raise
        (urbptr,) = struct.unpack('<Q', ctypes.string_at(ctypes.addressof(p), 8))
        if not urbptr:
            return None
        urb = ctypes.string_at(urbptr, URB_SIZE)
        status, = struct.unpack_from('<i', urb, 4)
        bufptr, = struct.unpack_from('<Q', urb, 16)
        actual, = struct.unpack_from('<i', urb, 28)
        userctx, = struct.unpack_from('<Q', urb, 48)
        entry = self.pending.pop(userctx, None)
        data = b''
        if status == 0 and actual > 0 and entry is not None:
            if ctypes.cast(entry[1], ctypes.c_void_p).value == bufptr:
                data = ctypes.string_at(bufptr, actual)
        return userctx, status, data

    def discard(self, uid):
        entry = self.pending.get(uid)
        if entry is None:
            return
        try:
            _ioctl(self.fd, DISCARDURB, ctypes.addressof(entry[0]))
        except OSError:
            pass


def find_pad():
    for bus in sorted(glob.glob('/dev/bus/usb/*/*')):
        try:
            fd = os.open(bus, os.O_RDONLY)
            d = os.pread(fd, 18, 0)
            os.close(fd)
        except OSError:
            continue
        if len(d) >= 18 and struct.unpack_from('<HH', d, 8) == (0x045e, 0x028e):
            return bus
    return None


def vendor(cmd, payload=b''):
    """A5 5A A5 信封（本项目已验证）"""
    buf = bytearray(32)
    buf[0] = 0xA5
    buf[1] = 0x5A
    buf[2] = 0xA5
    buf[3] = cmd
    buf[4] = len(payload) + 2
    buf[5:5 + len(payload)] = payload
    buf[3 + buf[4]] = sum(buf[3:3 + buf[4]]) & 0xFF
    return bytes(buf)


class Rec:
    def __init__(self, pad):
        self.pad = pad
        self.in_ids = set()
        self.frames = []
        self.marks = []
        self.t0 = time.time()
        self.last_frame = self.t0
        self.pump(INFLIGHT)

    def pump(self, n=1):
        for _ in range(n):
            self.in_ids.add(self.pad.submit(URB_INT, 0x81, b'\0' * 64, 64))

    def poll(self):
        got = self.pad.reap()
        if got is None:
            if time.time() - self.last_frame > 1.0:
                for uid in list(self.in_ids):
                    self.pad.discard(uid)
                    self.pad.pending.pop(uid, None)
                self.in_ids.clear()
                self.pump(INFLIGHT)
                self.last_frame = time.time()
            time.sleep(0.001)
            return None
        ctx, st, d = got
        if ctx in self.in_ids:
            self.in_ids.discard(ctx)
            self.pump(1)
            if st == 0 and d:
                self.last_frame = time.time()
                self.frames.append((time.time() - self.t0, d))
        return d

    def send(self, data):
        uid = self.pad.submit(URB_INT, 0x05, data, len(data))
        t0 = time.time()
        while time.time() - t0 < 0.5:
            got = self.pad.reap()
            if got is None:
                time.sleep(0.002)
                continue
            ctx, st, d = got
            if ctx in self.in_ids:
                self.in_ids.discard(ctx)
                self.pump(1)
                continue
            if ctx == uid:
                return st
        return None

    def pump_frames(self, seconds=None):
        """只收数据，不碰键盘"""
        end = time.time() + seconds if seconds else None
        while end is None or time.time() < end:
            self.poll()

    def wait_enter(self, prompt):
        """一边收数据一边等回车"""
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            self.poll()
            if select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.readline()
                return time.time() - self.t0


def phase(rec, name, hint):
    print(f"\n[{name}] {hint}")
    t1 = rec.wait_enter('    准备好了按回车开始 > ')
    t2 = rec.wait_enter('    做这个动作… 做完按回车 > ')
    rec.marks.append({'name': name, 'start': round(t1, 3), 'end': round(t2, 3)})
    print(f"    记录 {t1:.2f} → {t2:.2f}  ({t2 - t1:.2f}s)")


def auto(rec, name, hint, seconds):
    print(f"\n[{name}] {hint}（{seconds:.0f} 秒，自动）")
    t1 = time.time() - rec.t0
    rec.pump_frames(seconds)
    t2 = time.time() - rec.t0
    rec.marks.append({'name': name, 'start': round(t1, 3),
                      'end': round(t2, 3)})
    print(f"    记录 {t1:.2f} → {t2:.2f}  ({t2 - t1:.2f}s)")


def main():
    dev = find_pad()
    if not dev:
        sys.exit("未找到手柄 (045e:028e)")
    print(f"手柄: {dev}")
    print("""
说明：每个动作分两步——回车开始，做动作，再回车结束。
      中间不催你，慢慢来。Ctrl-C 可随时中断并保存已录数据。
""")

    pad = Pad(dev)
    subprocess.run(['modprobe', '-r', 'xpad'], check=False)
    time.sleep(0.3)
    claimed = False
    rec = None
    try:
        pad.claim()
        claimed = True
        rec = Rec(pad)
        rec.pump_frames(0.3)

        auto(rec, 'A1 平放静止', '把柄平放在桌上，不要碰', 3.0)
        phase(rec, 'A2 手持静止', '拿在手里，保持不动，不要转')
        phase(rec, 'A3 yaw 偏航', '绕竖轴：向左转 45°，回中，再向右转 45°')
        phase(rec, 'A4 pitch 俯仰', '绕横轴：向前倾 45°，回中，再向后仰 45°')
        phase(rec, 'A5 roll 横滚', '绕前后轴：向左歪 45°，回中，再向右歪 45°')

        print("\n>>> 现在发厂商指令 17（开启 raw data 流），对照看 [14..16] 是否变化")
        print(f"    OUT status={rec.send(vendor(0x11, bytes([1, 1, 0xFF, 0xFF, 0xFF])))}")
        rec.pump_frames(0.5)
        auto(rec, 'B1 cmd17 后静止', '把柄平放在桌上，不要碰', 3.0)
        auto(rec, 'B2 cmd17 后晃动', '拿起来快速左右晃', 3.0)
    except KeyboardInterrupt:
        print("\n中断。")
    finally:
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        subprocess.run(['modprobe', 'xpad'], check=False)
        print("xpad 已重载")
        if rec and rec.frames:
            save(rec)


def save(rec):
    os.makedirs('captures', exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = f'captures/motion_{stamp}'
    with open(base + '.bin', 'wb') as f:
        for ts, d in rec.frames:
            f.write(struct.pack('<dI', ts, len(d)) + d)

    stats = []
    for m in rec.marks:
        seg = [d for ts, d in rec.frames if m['start'] <= ts < m['end'] and len(d) >= 25]
        if not seg:
            continue
        row = {'name': m['name'], 'start': m['start'], 'end': m['end'],
               'frames': len(seg),
               'range': {f'{i:02d}': [min(d[i] for d in seg), max(d[i] for d in seg)]
                         for i in (14, 15, 16, 21, 22, 23, 24)}}
        stats.append(row)
    with open(base + '.json', 'w') as f:
        json.dump({'marks': rec.marks, 'stages': stats,
                   'report_len': max(len(d) for _, d in rec.frames)},
                  f, indent=2, ensure_ascii=False)

    print(f"\n{len(rec.frames)} 帧 → {base}.bin")
    print("\n=== 各阶段 [14..16] / [21..24] 范围 ===")
    print(f"{'阶段':<16} {'帧数':>6}  " +
          '  '.join(f'[{i}]'.rjust(9) for i in (14, 15, 16, 21, 22, 23, 24)))
    for s in stats:
        cells = []
        for i in (14, 15, 16, 21, 22, 23, 24):
            lo, hi = s['range'][f'{i:02d}']
            cells.append(f'{lo:3d}..{hi:3d}'.rjust(9))
        print(f"{s['name']:<16} {s['frames']:>6}  " + '  '.join(cells))


if __name__ == '__main__':
    main()
