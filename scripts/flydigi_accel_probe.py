#!/usr/bin/env python3
"""冰原狼4 加速度计探测：静态姿态法

原理
----
角速度（陀螺仪）在**静止**时恒为 0，不管手柄摆成什么角度；
加速度计在静止时始终测到 1g 重力，**换个朝向数值就会变**（约 ±1g）。

所以让手柄依次保持 7 种静态姿态，每种录 2 秒：
  * 某个字节跨姿态有明显差异 → 该字节带重力分量，是加速度计
  * 所有字节都不变 → 标准 32 字节报文里没有加速度数据
最后加一段"保持水平上下颠"，用来分离线性加速度（加速度计看得到、
陀螺仪看不到；手抖带来的转动会同时出现在角速度轴上，可对照）。

每个阶段只按**一次回车**：摆好姿势 → 回车 → 自动录 2 秒。
不掐时间，也不用按第二下。

可选 `--try-raw` 会再试几种老方言"raw 流开关"帧，看报文是否变成
0x04/0xEF 格式（实验性，可能把 pad 留在 raw 模式，拔插 dongle 可恢复）。

产物
----
    captures/accel_<时间>.bin / .json

用法
----
    sudo python3 scripts/flydigi_accel_probe.py
    sudo python3 scripts/flydigi_accel_probe.py --try-raw
"""
import argparse
import ctypes
import glob
import json
import os
import select
import statistics
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
SETTLE = 0.35          # 回车后先丢掉这么多秒（手还在动）
RECORD = 2.0

BYTES = (14, 15, 16, 21, 22, 23, 24)

PHASES = [
    ('平放正面朝上', '屏幕/键面朝上，平放在桌上'),
    ('翻转正面朝下', '翻过来，键面朝下'),
    ('左侧朝下', '竖起来，左手握的那一侧贴桌'),
    ('右侧朝下', '竖起来，右手握的那一侧贴桌'),
    ('充电口朝下', '竖起来，顶部（充电口/LB 那端）朝下'),
    ('握把朝下', '竖起来，底部（两个握把）朝下'),
    ('回到正面朝上', '再平放回第一次的姿势'),
    ('水平上下颠', '保持水平，快速上下颠 2 秒（不要转动）'),
]

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


def legacy(cmd, sub=0, payload=b''):
    buf = bytearray(15)
    buf[0] = 0xA5
    buf[1] = cmd
    buf[2] = sub
    buf[3:3 + len(payload)] = payload
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

    def pump_frames(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.poll()

    def wait_enter(self, prompt):
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            self.poll()
            if select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if line == '':
                    raise KeyboardInterrupt
                return time.time() - self.t0


def run_phase(rec, idx, total, name, hint):
    print(f'\n[{idx}/{total}] {name} —— {hint}')
    rec.wait_enter('    摆好后按回车 > ')
    rec.pump_frames(SETTLE)
    t1 = time.time() - rec.t0
    rec.pump_frames(RECORD)
    t2 = time.time() - rec.t0
    rec.marks.append({'name': name, 'start': round(t1, 3), 'end': round(t2, 3),
                      'kind': 'static'})
    print(f'    录了 {t2 - t1:.1f}s')


def summarize(rec):
    rows = []
    for m in rec.marks:
        seg = [d for ts, d in rec.frames
               if m['start'] <= ts < m['end'] and len(d) >= 25 and d[14] != 0xA5]
        if not seg:
            continue
        row = {'name': m['name'], 'frames': len(seg), 'mean': {}, 'range': {}}
        for i in BYTES:
            vals = [d[i] for d in seg]
            row['mean'][i] = round(statistics.mean(vals), 1)
            row['range'][i] = (min(vals), max(vals))
        rows.append(row)

    if not rows:
        print('没有有效帧')
        return rows

    print('\n=== 各姿态的字节均值 ===')
    print(f"{'姿态':<14} {'帧':>5}  " +
          '  '.join(f'[{i}]'.rjust(10) for i in BYTES))
    for r in rows:
        cells = []
        for i in BYTES:
            lo, hi = r['range'][i]
            cells.append(f'{r["mean"][i]:6.1f}'.rjust(10))
        print(f"{r['name']:<14} {r['frames']:>5}  " + '  '.join(cells))

    static = [r for r in rows if r['name'] != '水平上下颠']
    print('\n=== 跨静态姿态的极差（只算前 7 个姿态）===')
    spread = {}
    for i in BYTES:
        means = [r['mean'][i] for r in static]
        spread[i] = max(means) - min(means)
        print(f'  [{i:2d}]  均值 {min(means):6.1f} .. {max(means):6.1f}   极差 {spread[i]:5.1f}')

    gyro = (14, 15, 16)
    cand = [i for i in BYTES if spread[i] >= 8]
    print('\n=== 判定 ===')
    if cand:
        print(f'  跨姿态变化 ≥8 的字节: {cand}')
        for i in cand:
            if i in gyro:
                print(f'  ⚠ [{i}] 属于角速度区却在静止姿态下随朝向变化，'
                      f'需要人工复核（可能是动作没停稳）')
            else:
                print(f'  ✅ [{i}] 疑似加速度计轴：静止时随重力方向变化')
    else:
        print('  所有字节跨姿态极差 < 8：静态姿态下没有任何字节跟随重力，')
        print('  标准 32 字节报文里**没有加速度计数据**。')

    shake = [r for r in rows if r['name'] == '水平上下颠']
    if shake:
        s = shake[0]
        print(f"\n  上下颠阶段：均值 " +
              ' '.join(f'[{i}]={s["mean"][i]:.1f}' for i in BYTES))
        print('  （加速度计会在这一阶段出现尖峰；陀螺仪只看转动）')

    return rows


def save(rec, rows):
    os.makedirs('captures', exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = f'captures/accel_{stamp}'
    with open(base + '.bin', 'wb') as f:
        for ts, d in rec.frames:
            f.write(struct.pack('<dI', ts, len(d)) + d)
    with open(base + '.json', 'w') as f:
        json.dump({'marks': rec.marks, 'stages': rows}, f, indent=2,
                  ensure_ascii=False)
    print(f'\n{len(rec.frames)} 帧 → {base}.bin / .json')


def try_raw(rec):
    """实验性：老方言 raw 流开关的几种形状，看报文格式是否改变"""
    print('\n=== 附加：尝试老方言 raw 流开关（实验性）===')
    frames = [
        ('cmd80 sub1 + 五开关', legacy(0x50, 1, bytes([1, 1, 0xFF, 0xFF, 0xFF]))),
        ('cmd80 sub0 + 五开关', legacy(0x50, 0, bytes([1, 1, 0xFF, 0xFF, 0xFF]))),
        ('cmd17 sub1 + 四开关', legacy(0x11, 1, bytes([1, 1, 0xFF, 0xFF]))),
        ('cmd17 sub1 + 五开关', legacy(0x11, 1, bytes([1, 1, 0xFF, 0xFF, 0xFF]))),
    ]
    for name, frame in frames:
        print(f'\n  {name}: {frame.hex(" ")}')
        print(f'    OUT status={rec.send(frame)}')
        before = len(rec.frames)
        rec.pump_frames(1.0)
        new = [d for _, d in rec.frames[before:]]
        ids = sorted({d[0] for d in new if d})
        lens = sorted({len(d) for d in new if d})
        marks = sorted({d[3] for d in new if len(d) > 3})
        print(f'    收到 {len(new)} 帧  [0]={ids}  长度={lens}  [3]={marks}')
        if any(i != 0 for i in ids):
            print('    ⚠ 报文首字节变了：可能已进入 raw 模式，把上面数据发我')
        # 恢复：raw=0
        rec.send(legacy(0x50, 1, bytes([0xFF, 0, 0xFF, 0xFF, 0xFF])))
        rec.pump_frames(0.3)
    print('\n  已尝试恢复（raw=0）。若手柄异常，拔插 dongle 即可。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--try-raw', action='store_true',
                    help='额外尝试老方言 raw 流开关（实验性）')
    args = ap.parse_args()

    dev = find_pad()
    if not dev:
        sys.exit('未找到手柄 (045e:028e)')
    print(f'手柄: {dev}')
    print('''
加速度计判定：静止时角速度恒为 0，加速度计始终有 1g 重力。
所以每个姿态只要摆好按一次回车，脚本自动录 2 秒，不催时间。
''')

    pad = Pad(dev)
    subprocess.run(['modprobe', '-r', 'xpad'], check=False)
    time.sleep(0.3)
    claimed = False
    rec = None
    rows = []
    try:
        pad.claim()
        claimed = True
        rec = Rec(pad)
        rec.pump_frames(0.3)
        for i, (name, hint) in enumerate(PHASES, 1):
            run_phase(rec, i, len(PHASES), name, hint)
        if args.try_raw:
            try_raw(rec)
    except KeyboardInterrupt:
        print('\n中断。')
    finally:
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        subprocess.run(['modprobe', 'xpad'], check=False)
        print('xpad 已重载')
        if rec and rec.frames:
            rows = summarize(rec)
            save(rec, rows)


if __name__ == '__main__':
    main()
