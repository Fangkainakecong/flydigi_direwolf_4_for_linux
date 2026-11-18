#!/usr/bin/env python3
"""冰原狼4 全输入探针 —— 按键逐项 + 摇杆转圈自动判定

报文布局不是猜的，已在 2026-11-19 的全量测试中解出，见
`docs/input-report.md`。要点：

    [2..3]   标准 XInput 按钮字（xpad 认的就是这个）
    [4]/[5]  LT/RT 0..255
    [6..13]  左右摇杆 int16 小端（Y 上正）
    [17..20] 飞智键位位图，位 k = [17 + k//8] 的第 k%8 位

    位图编号（沿用上游 KEY_NAMES）：
        0 上 1 右 2 下 3 左 4 A 5 B 6 Back 7 X
        8 Y 9 Start 10 LB 11 RB 12 LT 13 RT 14 L3 15 R3
        18 M1 19 M2 24 FN 25 Turbo 27 Home

所以直接查位，不需要基线、阈值、锚点。

交互设计（针对"人看不完长提示"）：
  * 每个按键只显示一行短提示，例如 `[5/21] 按 A`；
  * 摇杆不逐方向提问，改成"慢慢转一圈"：脚本自动识别四个方向，
    转到哪个就打个勾，不需要你读 8 条提示；
  * 等待时在终端底部用一行实时显示摇杆数值（`\\r` 原地刷新，不刷屏）；
  * Enter 跳过当前项，`r` 重测上一项。

用法
----
    sudo python3 scripts/flydigi_button_probe.py
    sudo python3 scripts/flydigi_button_probe.py --only m1,m2
    sudo python3 scripts/flydigi_button_probe.py --only ls_right,rs_up
    sudo python3 scripts/flydigi_button_probe.py --free
    sudo python3 scripts/flydigi_button_probe.py --note "Windows 全默认键位"

结束释放接口、重载 xpad，并把报文存到 captures/inputs_<时间戳>.{bin,json}。
"""
import argparse
import json
import os
import select
import struct
import subprocess
import sys
import time
import ctypes
import glob

CLAIMINTERFACE = (2 << 30) | (4 << 16) | (ord('U') << 8) | 15
RELEASEINTERFACE = (2 << 30) | (4 << 16) | (ord('U') << 8) | 16
SUBMITURB = (2 << 30) | (56 << 16) | (ord('U') << 8) | 10
REAPURBNDELAY = (1 << 30) | (8 << 16) | (ord('U') << 8) | 13
DISCARDURB = (0 << 30) | (0 << 16) | (ord('U') << 8) | 11
URB_INT = 1
EAGAIN = 11
URB_SIZE = 56
INFLIGHT = 8

CAPTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'captures')

FULL = 32767

KEY_ID = {0: 'up', 1: 'right', 2: 'down', 3: 'left', 4: 'a', 5: 'b', 6: 'back',
          7: 'x', 8: 'y', 9: 'start', 10: 'lb', 11: 'rb', 12: 'lt', 13: 'rt',
          14: 'l3', 15: 'r3', 16: 'c', 17: 'z',
          18: 'm1', 19: 'm2', 20: 'm3', 21: 'm4', 22: 'm5', 23: 'm6',
          24: 'fn', 25: 'turbo', 26: 'id26', 27: 'home'}
KEY_BIT = {name: 1 << kid for kid, name in KEY_ID.items()}
KEY_NUM = {name: kid for kid, name in KEY_ID.items()}

# 标准按钮字里的同名位，用来交叉验证
STD_BIT = {'up': 0x0001, 'down': 0x0002, 'left': 0x0004, 'right': 0x0008,
           'start': 0x0010, 'back': 0x0020, 'l3': 0x0040, 'r3': 0x0080,
           'lb': 0x0100, 'rb': 0x0200, 'home': 0x0400,
           'a': 0x1000, 'b': 0x2000, 'x': 0x4000, 'y': 0x8000}

DISPLAY = {'up': '十字-上', 'down': '十字-下', 'left': '十字-左', 'right': '十字-右',
           'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'lb': 'LB', 'rb': 'RB',
           'start': 'Start', 'back': 'Back', 'home': 'Home',
           'l3': 'L3', 'r3': 'R3', 'm1': 'M1', 'm2': 'M2',
           'fn': 'FN', 'turbo': 'Turbo',
           'ls_right': '左摇杆→右', 'ls_left': '左摇杆→左',
           'ls_up': '左摇杆→上', 'ls_down': '左摇杆→下',
           'rs_right': '右摇杆→右', 'rs_left': '右摇杆→左',
           'rs_up': '右摇杆→上', 'rs_down': '右摇杆→下'}

AXES = {'lx': (6, '左摇杆 X'), 'ly': (8, '左摇杆 Y'),
        'rx': (10, '右摇杆 X'), 'ry': (12, '右摇杆 Y')}

DIR_IDS = {
    'ls': {'up': ('ly', +1), 'down': ('ly', -1), 'left': ('lx', -1), 'right': ('lx', +1)},
    'rs': {'up': ('ry', +1), 'down': ('ry', -1), 'left': ('rx', -1), 'right': ('rx', +1)},
}
DIR_CN = {'up': '上', 'down': '下', 'left': '左', 'right': '右'}

# (id, 类型, 参数, 提示语) —— 提示一律短句，详细说明在 docs/input-report.md
KEYS = [
    ('a', '按 A'),
    ('b', '按 B'),
    ('x', '按 X'),
    ('y', '按 Y'),
    ('lb', '按 LB（左肩键）'),
    ('rb', '按 RB（右肩键）'),
    ('lt', '按住 LT 到底，约 1 秒后松开'),
    ('rt', '按住 RT 到底，约 1 秒后松开'),
    ('l3', '左摇杆垂直按下（L3）再松开'),
    ('r3', '右摇杆垂直按下（R3）再松开'),
    ('start', '按 Start'),
    ('back', '按 Back（Select）'),
    ('home', '按 Home（LOGO）'),
    ('up', '按十字 上'),
    ('down', '按十字 下'),
    ('left', '按十字 左'),
    ('right', '按十字 右'),
    ('m1', '按 M1（左侧背键）'),
    ('m2', '按 M2（右侧背键）'),
    ('fn', '按住 FN 约 1 秒'),
    ('turbo', '按 Turbo 键'),
]

AXIS_ONLY = {
    'ls_right': ('lx', +1), 'ls_left': ('lx', -1),
    'ls_up': ('ly', +1), 'ls_down': ('ly', -1),
    'rs_right': ('rx', +1), 'rs_left': ('rx', -1),
    'rs_up': ('ry', +1), 'rs_down': ('ry', -1),
}
AXIS_PAIR = {'lx': 'ly', 'ly': 'lx', 'rx': 'ry', 'ry': 'rx'}


# ---------------------------------------------------------------- 传输层
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
        struct.pack_into('<I', b, 0, 0)
        _ioctl(self.fd, CLAIMINTERFACE, ctypes.addressof(b))

    def release(self):
        b = ctypes.create_string_buffer(4)
        struct.pack_into('<I', b, 0, 0)
        _ioctl(self.fd, RELEASEINTERFACE, ctypes.addressof(b))

    def submit(self, xfer, ep, data=None, size=64):
        buf = (ctypes.create_string_buffer(bytes(data), size)
               if data is not None else ctypes.create_string_buffer(size))
        u = CUrb()
        u.type = xfer
        u.endpoint = ep & 0xFF
        u.buffer = ctypes.cast(buf, ctypes.c_void_p)
        u.buffer_length = size
        u.usercontext = ctypes.c_void_p(id(u))
        self.pending[id(u)] = (u, buf)      # 保活到 reap
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
        actual_len, = struct.unpack_from('<i', urb, 28)
        userctx, = struct.unpack_from('<Q', urb, 48)
        data = b''
        entry = self.pending.pop(userctx, None)
        if status == 0 and actual_len > 0 and entry is not None:
            u, buf = entry
            if ctypes.cast(buf, ctypes.c_void_p).value == bufptr:
                data = ctypes.string_at(bufptr, actual_len)
        return (userctx, status, data)

    def discard(self, urb_id):
        entry = self.pending.get(urb_id)
        if entry is None:
            return
        _ioctl(self.fd, DISCARDURB, ctypes.addressof(entry[0]))


def find_pad():
    for bus in sorted(glob.glob('/dev/bus/usb/*/*')):
        try:
            fd = os.open(bus, os.O_RDONLY)
            d = os.pread(fd, 18, 0)
            os.close(fd)
        except OSError:
            continue
        if len(d) < 18:
            continue
        vid, pid = struct.unpack_from('<HH', d, 8)
        if vid == 0x045e and pid == 0x028e:
            return bus
    return None


class Monitor:
    def __init__(self, pad):
        self.pad = pad
        self.in_ids = set()
        self.frames = []        # (ts, data)
        self.t0 = time.time()
        self.last_frame = time.time()
        self.stalls = 0
        self.rearm()

    def rearm(self):
        """重置在途 URB。报文流卡住时（dongle 休眠/丢包）用。"""
        for uid in list(self.in_ids):
            try:
                self.pad.discard(uid)
            except OSError:
                pass
            self.pad.pending.pop(uid, None)
        self.in_ids.clear()
        self.pump(INFLIGHT)
        self.last_frame = time.time()

    def pump(self, n=1):
        for _ in range(n):
            self.in_ids.add(self.pad.submit(URB_INT, 0x81, None, 64))

    def poll(self):
        got = self.pad.reap()
        if got is None:
            if time.time() - self.last_frame > 1.0:
                self.stalls += 1
                if self.stalls == 1:
                    print("\n    ⚠ 1 秒没收到报文，重置传输…", flush=True)
                self.rearm()
            return None
        ctx, st, d = got
        if ctx in self.in_ids:
            self.in_ids.discard(ctx)
            self.pump(1)
        if st != 0 or not d:
            return None
        now = time.time()
        self.last_frame = now
        if self.stalls:
            print(f"    ✔ 报文恢复（重置 {self.stalls} 次）", flush=True)
            self.stalls = 0
        self.frames.append((now - self.t0, d))
        if len(self.frames) > 600000:
            del self.frames[:150000]
        return d

    def collect(self, seconds):
        out = []
        end = time.time() + seconds
        while time.time() < end:
            d = self.poll()
            if d is None:
                time.sleep(0.001)
            else:
                out.append(d)
        return out


_STDIN_EOF = False


def stdin_line():
    global _STDIN_EOF
    if _STDIN_EOF:
        return None
    if select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline()
        if line == '':          # stdin 关闭（后台运行），别当成"跳过"
            _STDIN_EOF = True
            return None
        return line.strip()
    return None


# ---------------------------------------------------------------- 解码
def s16(d, off):
    return struct.unpack_from('<h', d, off)[0]


def state(d):
    return {
        'btn': d[2] | (d[3] << 8),
        'lt': d[4], 'rt': d[5],
        'lx': s16(d, 6), 'ly': s16(d, 8), 'rx': s16(d, 10), 'ry': s16(d, 12),
        'keys': d[17] | (d[18] << 8) | (d[19] << 16) | (d[20] << 24),
    }


def active_keys(st):
    return [KEY_ID.get(k, f'id{k}') for k in range(32) if st['keys'] & (1 << k)]


def is_pressed(key, d):
    st = state(d)
    if key in ('lt', 'rt'):
        return st[key] > 20          # 扳机看模拟量，不依赖位图
    return bool(st['keys'] & KEY_BIT[key])


def describe_key(key, first):
    st = state(first)
    if key in ('lt', 'rt'):
        return f"[{4 if key == 'lt' else 5}]={st[key]}/255  位图{'有' if st['keys'] & KEY_BIT[key] else '无'}"
    bits = [f"位图 {KEY_BIT[key]:#010x} → [17+{KEY_NUM[key] // 8}]"]
    std = STD_BIT.get(key)
    if std is not None:
        bits.append(f"标准字 {std:#06x} {'有' if st['btn'] & std else '无'}")
    return '  '.join(bits)


def side_effects(key, first):
    st = state(first)
    keys = [k for k in active_keys(st) if k != key]
    extra = [f"同时:{','.join(keys)}"] if keys else []
    std = st['btn'] & 0x0fff
    if std:
        names = [n for n, m in STD_BIT.items() if std & m and n != key]
        if names:
            extra.append('标准:' + ','.join(names))
    return '  ' + ' '.join(extra) if extra else ''


# ---------------------------------------------------------------- 实时数值行
class StatusLine:
    """在终端底部原地显示摇杆/扳机数值，不刷屏。"""

    def __init__(self, enabled=True):
        self.enabled = enabled and sys.stdout.isatty()
        self.width = 0
        self.last = 0.0
    def update(self, st, force=False):
        if not self.enabled:
            return
        now = time.time()
        if not force and now - self.last < 0.08:
            return
        self.last = now
        text = ('    左 [{:+6d} {:+6d}]   右 [{:+6d} {:+6d}]   LT {:3d} RT {:3d}'
                .format(st['lx'], st['ly'], st['rx'], st['ry'], st['lt'], st['rt']))
        pad = max(0, self.width - len(text))
        sys.stdout.write('\r' + text + ' ' * pad)
        sys.stdout.flush()
        self.width = len(text)

    def clear(self):
        if not self.enabled or not self.width:
            return
        sys.stdout.write('\r' + ' ' * self.width + '\r')
        sys.stdout.flush()
        self.width = 0


# ---------------------------------------------------------------- 单项按键
def key_press(mon, key, status, max_wait):
    """等这个键被按下并松开。返回 (status, info)。"""
    start = time.time()
    first = None
    while time.time() - start < max_wait:
        d = mon.poll()
        if d is None:
            line = stdin_line()
            if line is not None:
                return ('retry', None) if line == 'r' else ('skip', None)
            time.sleep(0.001)
            continue
        st = state(d)
        status.update(st)
        if is_pressed(key, d):
            first = d
            break
    if first is None:
        return 'timeout', None

    t0 = time.time()
    last = t0
    while time.time() - t0 < 4.0:
        d = mon.poll()
        if d is None:
            time.sleep(0.001)
            continue
        st = state(d)
        if is_pressed(key, d):
            last = time.time()
        elif time.time() - last > 0.25:
            break
    info = {'duration': round(last - t0, 2),
            'text': describe_key(key, first),
            'extra': side_effects(key, first)}
    return 'event', info


# ---------------------------------------------------------------- 摇杆转圈
def stick_circle(mon, side, status, timeout=90.0, need_frames=3):
    """提示一次，用户慢慢转一圈；脚本自动识别四个方向。

    判定：目标轴 |v| >= 0.55 满量程，且明显大于另一轴（防止斜推误判），
          连续 need_frames 帧成立才算。
    """
    dirs = DIR_IDS[side]
    found = {}
    streak = {k: 0 for k in dirs}
    label = '左摇杆' if side == 'ls' else '右摇杆'
    t0 = time.time()
    last_show = None
    while len(found) < 4 and time.time() - t0 < timeout:
        d = mon.poll()
        if d is None:
            line = stdin_line()
            if line is not None:
                break
            time.sleep(0.001)
            continue
        st = state(d)
        status.update(st)
        for name, (axis, sign) in dirs.items():
            v = st[axis] * sign
            other = st[AXIS_PAIR[axis]]
            ok = v >= 0.55 * FULL and v >= 1.8 * abs(other)
            streak[name] = streak[name] + 1 if ok else 0
            if name in found:
                found[name] = max(found[name], v)
            elif streak[name] >= need_frames:
                found[name] = v
        done = set(found)
        if done != last_show:
            marks = ' '.join(f"{DIR_CN[k]}{'✓' if k in done else '·'}"
                             for k in ('up', 'down', 'left', 'right'))
            status.clear()
            print(f"    {label}: {marks}", flush=True)
            last_show = done
    status.clear()
    results = []
    for name in ('up', 'down', 'left', 'right'):
        rid = f'{side}_{name}'
        if name in found:
            results.append({'id': rid, 'kind': 'axis', 'status': 'ok',
                            'peak': int(found[name]),
                            'text': f"{DIR_CN[name]} 方向，峰值 {found[name]}"})
        else:
            results.append({'id': rid, 'kind': 'axis', 'status': 'timeout'})
    return results


# ---------------------------------------------------------------- 单项方向（重测用）
def axis_dir(mon, rid, status, max_wait):
    axis, sign = AXIS_ONLY[rid]
    start = time.time()
    peak = 0
    while time.time() - start < max_wait:
        d = mon.poll()
        if d is None:
            line = stdin_line()
            if line is not None:
                return ('retry', None) if line == 'r' else ('skip', None)
            time.sleep(0.001)
            continue
        st = state(d)
        status.update(st)
        v = st[axis] * sign
        peak = max(peak, v)
        if v >= 0.55 * FULL:
            break
    else:
        return 'timeout', {'peak': peak}
    info = {'duration': 0.0, 'peak': peak,
            'text': f"峰值 {peak}（满量程 {FULL}）"}
    return 'event', info


# ---------------------------------------------------------------- 主流程
def static_check(mon):
    print(">>> 静止检查：1 秒内不要碰手柄")
    frames = [d for d in mon.collect(1.0) if len(d) >= 25]
    if not frames:
        raise SystemExit("没有收到报文，检查 dongle")
    latched = 0
    names = set()
    for d in frames:
        st = state(d)
        if st['keys'] or st['btn'] or st['lt'] > 20 or st['rt'] > 20 or \
           max(abs(st['lx']), abs(st['ly']), abs(st['rx']), abs(st['ry'])) > 6000:
            latched += 1
            names.update(active_keys(st))
    print(f"    报文 {len(frames)} 条，长度 {max(len(d) for d in frames)}")
    if latched > len(frames) * 0.1:
        print(f"    ⚠ {latched}/{len(frames)} 帧有输入：{','.join(sorted(names)) or '轴/扳机'}")
        print("      松手、摇杆回中；如果一直这样，可能是按键卡住。")
        input("      回车继续 > ")


def do_key(mon, key, idx, total, results, status, max_wait):
    while True:
        print(f"\n[{idx}/{total}] {dict(KEYS).get(key, key)}")
        print("    Enter=跳过  r=重测", flush=True)
        st, info = key_press(mon, key, status, max_wait)
        status.clear()
        if st == 'retry':
            continue
        if st == 'event':
            print(f"    ✓ {info['text']}   按住 {info['duration']:.2f}s"
                  + (f"   额外:{info['extra']}" if info['extra'].strip() else ''))
            results.append({'id': key, 'kind': 'key', 'status': 'ok', **info})
            return
        if st == 'skip':
            print("    - 跳过")
            results.append({'id': key, 'kind': 'key', 'status': 'skipped'})
            return
        print(f"    ✗ {max_wait:.0f} 秒没检测到")
        results.append({'id': key, 'kind': 'key', 'status': 'timeout'})
        return


def do_circle(mon, side, idx, total, results, status):
    label = '左摇杆' if side == 'ls' else '右摇杆'
    print(f"\n[{idx}/{total}] {label}：慢慢转一圈，每个方向停一下")
    print("    四个方向都会自动打勾；Enter=结束", flush=True)
    results.extend(stick_circle(mon, side, status))
    miss = [r['id'] for r in results if r['id'].startswith(side) and r['status'] != 'ok']
    if miss:
        print(f"    缺：{', '.join(DISPLAY.get(m, m) for m in miss)}"
              f"\n    重测：sudo python3 scripts/flydigi_button_probe.py --only {','.join(miss)}")


def do_axis(mon, rid, idx, total, results, status, max_wait):
    while True:
        print(f"\n[{idx}/{total}] {DISPLAY[rid]}   peak 目标 ≥ {int(0.55 * FULL)}")
        print("    Enter=跳过  r=重测", flush=True)
        st, info = axis_dir(mon, rid, status, max_wait)
        status.clear()
        if st == 'retry':
            continue
        if st == 'event':
            print(f"    ✓ {DISPLAY[rid]}  {info['text']}")
            results.append({'id': rid, 'kind': 'axis', 'status': 'ok', **info})
            return
        if st == 'skip':
            print("    - 跳过")
            results.append({'id': rid, 'kind': 'axis', 'status': 'skipped'})
            return
        print(f"    ✗ {max_wait:.0f} 秒没到阈值（最大 {info.get('peak', 0)}）")
        results.append({'id': rid, 'kind': 'axis', 'status': 'timeout',
                        'peak': info.get('peak', 0)})
        return


def free_mode(mon, status, seconds=0):
    print("\n=== 自由记录模式 ===")
    print("按任意键（含重映射后的键），变化会打印出来。Ctrl-C 结束。\n")
    prev = None
    end = time.time() + seconds if seconds else None
    n = 0
    while end is None or time.time() < end:
        d = mon.poll()
        if d is None:
            time.sleep(0.001)
            continue
        st = state(d)
        status.update(st)
        sig = (st['btn'], st['keys'], st['lt'] // 32, st['rt'] // 32,
               st['lx'] // 8192, st['ly'] // 8192, st['rx'] // 8192, st['ry'] // 8192)
        if sig == prev:
            continue
        prev = sig
        if not any(sig):
            continue
        n += 1
        keys = active_keys(st)
        parts = []
        if st['btn']:
            parts.append(f"标准=0x{st['btn']:04x}")
        if keys:
            parts.append('位图=' + ','.join(keys))
        if st['lt']:
            parts.append(f"LT={st['lt']}")
        if st['rt']:
            parts.append(f"RT={st['rt']}")
        for k, (off, _) in AXES.items():
            if abs(st[k]) > 8000:
                parts.append(f"{k}={st[k]}")
        status.clear()
        print(f"  #{n}  " + '  '.join(parts))
    status.clear()
    print(f"共 {n} 个状态")


def main():
    ap = argparse.ArgumentParser(description='冰原狼4 全输入探针')
    ap.add_argument('--only', default='',
                    help='只测这些 id，逗号分隔：按键名 / ls_right / ls_circle …')
    ap.add_argument('--free', action='store_true', help='直接进入自由记录模式')
    ap.add_argument('--free-seconds', type=float, default=0)
    ap.add_argument('--note', default='', help='写进 json 的备注')
    ap.add_argument('--max-wait', type=float, default=20.0,
                    help='单项等待秒数（默认 20，足够读完提示再动手）')
    ap.add_argument('--no-keys', action='store_true', help='不测按键，只测摇杆')
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("需要 sudo：sudo python3 scripts/flydigi_button_probe.py")
        sys.exit(1)

    dev = find_pad()
    if not dev:
        print("未找到手柄 (045e:028e)")
        sys.exit(1)
    print(f"手柄: {dev}")

    if subprocess.run(['pgrep', '-x', 'steam'], capture_output=True).stdout.strip():
        print("\n⚠ Steam 在运行，它会抢设备。建议先 pkill -TERM steam。")
        input("  回车继续，Ctrl-C 退出 > ")

    todo = []      # ('key', id) / ('circle', 'ls'/'rs') / ('axis', id)
    if args.only:
        ids = [s.strip() for s in args.only.split(',') if s.strip()]
        for s in ids:
            if s in dict(KEYS):
                todo.append(('key', s))
            elif s in AXIS_ONLY:
                todo.append(('axis', s))
            elif s in ('ls_circle', 'ls'):
                todo.append(('circle', 'ls'))
            elif s in ('rs_circle', 'rs'):
                todo.append(('circle', 'rs'))
            else:
                raise SystemExit(f"未知 id {s}\n可按键: {', '.join(dict(KEYS))}"
                                 f"\n可摇杆: {', '.join(AXIS_ONLY)}, ls_circle, rs_circle")
    else:
        if not args.no_keys:
            todo = [('key', k) for k, _ in KEYS]
        todo += [('circle', 'ls'), ('circle', 'rs')]

    pad = Pad(dev)
    subprocess.run(['modprobe', '-r', 'xpad'], check=False)
    time.sleep(0.3)

    mon = None
    claimed = False
    results = []
    status = StatusLine()
    try:
        pad.claim()
        claimed = True
        mon = Monitor(pad)
        static_check(mon)

        if args.free:
            free_mode(mon, status, args.free_seconds)
            return

        print(f"\n=== {len(todo)} 项 ===")
        for i, (kind, arg) in enumerate(todo, 1):
            if kind == 'key':
                do_key(mon, arg, i, len(todo), results, status, args.max_wait)
            elif kind == 'circle':
                do_circle(mon, arg, i, len(todo), results, status)
            else:
                do_axis(mon, arg, i, len(todo), results, status, args.max_wait)

        missed = [r['id'] for r in results if r['status'] != 'ok']
        if missed:
            print("\n=== 没检测到的 ===")
            print('  ' + ', '.join(f"{DISPLAY.get(m, m)}({m})" for m in missed))
            print("  重跑：sudo python3 scripts/flydigi_button_probe.py --only "
                  + ','.join(missed))

        if input("\n自由记录模式（验证板载映射）？[y/N] > ").strip().lower() == 'y':
            free_mode(mon, status, args.free_seconds)

    except KeyboardInterrupt:
        print("\n中断。")
    finally:
        status.clear()
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        subprocess.run(['modprobe', 'xpad'], check=False)
        print("xpad 已重载。")
        if mon is not None and mon.frames:
            save(mon, results, args.note)


def save(mon, results, note):
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = os.path.join(CAPTURE_DIR, f'inputs_{stamp}')
    with open(base + '.bin', 'wb') as f:
        for ts, d in mon.frames:
            f.write(struct.pack('<dI', ts, len(d)) + d)
    with open(base + '.json', 'w') as f:
        json.dump({'when': stamp, 'note': note,
                   'report_len': max(len(d) for _, d in mon.frames),
                   'layout': 'docs/input-report.md',
                   'results': results}, f, indent=2, ensure_ascii=False)

    print("\n=== 结果 ===")
    for r in results:
        if r['status'] == 'ok':
            print(f"  {DISPLAY.get(r['id'], r['id']):<12} {r['text']}"
                  + (f"   {r['duration']:.2f}s" if 'duration' in r else ''))
        else:
            print(f"  {DISPLAY.get(r['id'], r['id']):<12} {r['status']}")
    print(f"\n{len(mon.frames)} 帧 → {base}.bin\n签名 → {base}.json")


if __name__ == '__main__':
    main()
