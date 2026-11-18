#!/usr/bin/env python3
"""冰原狼4 按键探针 —— 事件驱动，不需要人掐秒表

和旧脚本的区别
--------------
旧脚本靠时间窗（"0-3 秒按 M1，3-6 秒按 M2"），人做不到。本脚本反过来：
自己检测按键事件，检测到了才进下一个，全程只提示"现在请按 X"。

工作方式
--------
1. 静止 2 秒做基线，自动求出"稳定字节集合"，摇杆/陀螺的噪声字节被排除，
   之后只有稳定字节发生变化才算按键。
2. 每次提示按一个键，等待 25 秒；一旦检测到事件就记录"哪个字节变成什么值、
   持续多久、标准按键字节 [2] 是否变化"，然后自动进入下一个键。
   期间按回车 = 跳过，输入 r + 回车 = 重测。
3. 先用 **未开厂商流** 的状态测一次 M1，再开 cmd17 测全套 —— 一次实验同时
   回答"这些键默认可见吗"和"是否需要 cmd17"。
4. 结束时会问是否进入自由记录模式：按任意键就打印一个签名，用来验证
   "Windows 里把 M2 改成 L3 之后，Linux 到底收到什么"。

产物
----
captures/buttons_<ts>.bin   报文流，格式 <double ts><uint32 len><data>
captures/buttons_<ts>.json  基线、稳定字节表、每个键的签名

用法
----
    sudo python3 scripts/flydigi_button_probe.py
    sudo python3 scripts/flydigi_button_probe.py --free     # 直接进自由模式
    sudo python3 scripts/flydigi_button_probe.py --keys M1,M2,M3

运行前：把 Steam 关掉（`pkill -TERM steam`），手柄放桌上别碰。
运行结束自动关 raw、释放接口、重载 xpad。
"""
import argparse
import collections
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

CAPTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'captures')

# 屏幕上提示的按键顺序；名字只用于报告
KEYS = ['M1', 'M2', 'FN', 'Turbo']


# ---------------------------------------------------------------- 传输层
_LIBC = ctypes.CDLL(None, use_errno=True)


def _ioctl(fd, req, addr):
    """libc.ioctl + c_void_p：fcntl.ioctl 会复制缓冲区，导致内核拿到失效地址"""
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
        self.pending[id(u)] = (u, buf)      # 必须保活到 reap
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
        u, _ = self.pending[urb_id]
        _ioctl(self.fd, DISCARDURB, ctypes.addressof(u))


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


def build_cmd(cmd_id, payload=b''):
    """已验证的 A5 5A A5 信封（32 字节，带校验）"""
    buf = bytearray(32)
    buf[0] = 0xA5
    buf[1] = 0x5A
    buf[2] = 0xA5
    buf[3] = cmd_id
    buf[4] = len(payload) + 2
    buf[5:5 + len(payload)] = payload
    buf[3 + buf[4]] = sum(buf[3:3 + buf[4]]) & 0xFF
    return bytes(buf)


def errno_name(st):
    m = {75: 'EOVERFLOW', 19: 'ENODEV', 32: 'EPIPE(STALL)', 100: 'ENETRESET',
         104: 'ECONNRESET(取消)', 5: 'EIO', 110: 'ETIMEDOUT', 71: 'EPROTO'}
    return m.get(abs(st), f'errno{abs(st)}')


# ---------------------------------------------------------------- 监听
class Monitor:
    """持续提交 IN URB，把收到的报文连时间戳一起存起来"""

    def __init__(self, pad, max_frames=400000):
        self.pad = pad
        self.in_ids = set()
        self.frames = []        # (ts, data)
        self.t0 = time.time()
        self.max_frames = max_frames
        self.pump(8)

    def pump(self, n=1):
        for _ in range(n):
            self.in_ids.add(self.pad.submit(URB_INT, 0x81, None, 64))

    def poll(self):
        got = self.pad.reap()
        if got is None:
            return None
        ctx, st, d = got
        if ctx in self.in_ids:          # 这是一条 IN，补一条新的
            self.in_ids.discard(ctx)
            self.pump(1)
        if st != 0 or not d:
            return None
        ts = time.time() - self.t0
        self.frames.append((ts, d))
        if len(self.frames) > self.max_frames:
            del self.frames[: self.max_frames // 4]
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

    def send_cmd(self, payload, size=32, timeout=2.0):
        """发一条 OUT，等待它完成；期间照常收 IN"""
        target = self.pad.submit(URB_INT, 0x05, payload, size)
        end = time.time() + timeout
        status = None
        while time.time() < end:
            got = self.pad.reap()
            if got is None:
                time.sleep(0.001)
                continue
            ctx, st, d = got
            if ctx in self.in_ids:
                self.in_ids.discard(ctx)
                self.pump(1)
                continue
            if ctx == target:
                status = st
                break
        if status is None:
            try:
                self.pad.discard(target)
            except OSError:
                pass
        return status


def stdin_line():
    """非阻塞读一行；没有输入返回 None"""
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.readline().strip()
    return None


# ---------------------------------------------------------------- 事件探测
class Probe:
    def __init__(self, mon):
        self.mon = mon
        self.baseline = []      # 每个字节的基线值
        self.stable = []        # 基线期间从不变化的字节下标

    def calibrate(self, seconds=2.0):
        print(f"\n>>> 校准基线：{seconds:.0f} 秒内请不要碰手柄（摇杆也别碰）")
        for i in (3, 2, 1):
            print(f"    {i}...", end='', flush=True)
            time.sleep(1)
        print()
        frames = [d for d in self.mon.collect(seconds) if len(d) >= 4]
        if not frames:
            raise SystemExit("没有收到任何报文，检查 dongle 是否插好")
        n = max(len(d) for d in frames)
        values = [set() for _ in range(n)]
        for d in frames:
            for i, v in enumerate(d):
                values[i].add(v)
        self.baseline = [next(iter(v)) if len(v) == 1 else 0 for v in values]
        self.stable = [i for i, v in enumerate(values) if len(v) == 1]
        noisy = [i for i, v in enumerate(values) if len(v) > 1]
        print(f"    报文 {len(frames)} 条，长度 {n}")
        print(f"    稳定字节 {len(self.stable)} 个，噪声字节 {noisy if noisy else '无'}")
        if 2 in self.stable:
            print(f"    基线标准按键字节 [2] = 0x{self.baseline[2]:02x}")
        return frames

    def diff(self, d):
        return {i: d[i] for i in self.stable
                if i < len(d) and d[i] != self.baseline[i]}

    def capture_event(self, max_wait=25.0, quiet=0.35):
        """等一个按键事件。返回 (status, info)，status ∈ event/skip/timeout"""
        start = time.time()
        press_start = None
        last_seen = None
        seen = collections.OrderedDict()
        first_frame = last_frame = None
        while time.time() - start < max_wait:
            d = self.mon.poll()
            if d is None:
                line = stdin_line()
                if line is not None:
                    if line == 'r':
                        return 'retry', None
                    return 'skip', None
                time.sleep(0.001)
                continue
            diff = self.diff(d)
            now = time.time()
            if diff:
                if press_start is None:
                    press_start = now
                    first_frame = len(self.mon.frames) - 1
                last_seen = now
                last_frame = len(self.mon.frames) - 1
                for i, v in diff.items():
                    seen.setdefault(i, set()).add(v)
            elif press_start is not None and now - last_seen > quiet:
                info = {
                    'bytes': {i: sorted(vs) for i, vs in seen.items()},
                    'duration': round(last_seen - press_start, 3),
                    'standard_byte2': 2 in seen,
                    'frames': last_frame - first_frame + 1,
                }
                return 'event', info
        return 'timeout', None


def describe(info):
    parts = []
    for i, vs in info['bytes'].items():
        vals = ' / '.join(f'0x{v:02x}' for v in vs)
        parts.append(f'[{i}]={vals}')
    std = '是' if info['standard_byte2'] else '否'
    return f"{'  '.join(parts)}   持续 {info['duration']:.2f}s   标准[2]: {std}"


def prompt(msg):
    print(f"\n>>> {msg}")
    return input("    （直接回车 = 跳过，输入 r = 重测）> ").strip().lower()


def ask_key(probe, key, results, tag=''):
    while True:
        line = prompt(f"请按一次 {key}，然后松开")
        if line == 'r':
            continue
        if line:
            results.append({'key': key + tag, 'status': 'skipped'})
            print("    已跳过")
            return
        status, info = probe.capture_event()
        if status == 'event':
            print(f"    ✓ 收到 {key}：{describe(info)}")
            results.append({'key': key + tag, 'status': 'ok', **info})
            return
        if status == 'retry':
            continue
        print("    ✗ 25 秒内没检测到任何变化，再试一次？")
        line = input("    （回车重试，输入 s + 回车跳过）> ").strip().lower()
        if line == 's':
            results.append({'key': key + tag, 'status': 'timeout'})
            return


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description='冰原狼4 按键探针（事件驱动，不需要掐秒表）')
    ap.add_argument('--keys', default=','.join(KEYS),
                    help=f'要测的按键，逗号分隔（默认 {",".join(KEYS)}）')
    ap.add_argument('--free', action='store_true', help='直接进入自由记录模式')
    ap.add_argument('--no-raw', action='store_true', help='不发 cmd17，保持默认状态')
    ap.add_argument('--free-seconds', type=float, default=0,
                    help='自由模式自动结束时间（0 = 手动 Ctrl-C）')
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("需要 sudo 运行：sudo python3 scripts/flydigi_button_probe.py")
        sys.exit(1)

    dev = find_pad()
    if not dev:
        print("未找到手柄 (045e:028e)")
        sys.exit(1)
    print(f"手柄: {dev}")

    # USB 自动休眠会让空闲的设备掉线
    for ctl in glob.glob('/sys/bus/usb/devices/*/power/control'):
        try:
            if open(ctl).read().strip() != 'on':
                open(ctl, 'w').write('on')
        except OSError:
            pass

    if subprocess.run(['pgrep', '-x', 'steam'], capture_output=True).stdout.strip():
        print("\n⚠ 检测到 Steam 在运行。它会在 xpad 卸载时弹提示、并可能抢设备。")
        input("  建议先 `pkill -TERM steam`。回车继续，Ctrl-C 退出 > ")

    print("\n⚠ 全程把前臂放松，手柄放桌上；只有提示时才按键。")

    pad = Pad(dev)
    subprocess.run(['modprobe', '-r', 'xpad'], check=False)
    time.sleep(0.3)

    claimed = False
    mon = probe = None
    results = []
    raw_enabled = False
    try:
        pad.claim()
        claimed = True
        mon = Monitor(pad)
        probe = Probe(mon)

        if args.free:
            probe.calibrate()
            if not args.no_raw:
                raw_enabled = send_raw(mon, 1) == 0
                print(f"    cmd17: {'已开启' if raw_enabled else '无响应'}")
                probe.calibrate(1.5)
            free_mode(probe, args.free_seconds, results)
            return

        keys = [k.strip() for k in args.keys.split(',') if k.strip()]

        # 阶段 A：不开厂商流，先看默认状态
        probe.calibrate()
        first = keys[0]
        print("\n=== 阶段 A：不开厂商流（cmd17 之前）===")
        print(f"这一步回答：{first} 在没有厂商流时，Linux 能不能看到。")
        ask_key(probe, first, results, tag='(raw关闭)')

        if args.no_raw:
            print("\n--no-raw：跳过阶段 B")
        else:
            print("\n=== 阶段 B：开启厂商流（cmd17）===")
            ack = send_raw(mon, 1)
            raw_enabled = ack == 0
            print(f"    cmd17 ACK: {'成功' if raw_enabled else '无响应'}"
                  f"（OUT status={ack}）")
            probe.calibrate(1.5)
            print("\n从现在起逐个提示，收到就自动进下一个。")
            for k in keys:
                ask_key(probe, k, results)

        print("\n=== 逐个按键结束 ===")
        if input("\n进入自由记录模式？按任意键都会打印签名（验证板载映射用）[y/N] > ").strip().lower() == 'y':
            free_mode(probe, args.free_seconds, results)

    except KeyboardInterrupt:
        print("\n中断。")
    finally:
        if mon is not None and raw_enabled:
            st = send_raw(mon, 0)
            print(f"\n关闭厂商流: OUT status={st}")
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        subprocess.run(['modprobe', 'xpad'], check=False)
        print("xpad 已重载，手柄恢复为普通手柄。")

        if mon is not None and mon.frames:
            save_and_report(mon, probe, results)


def send_raw(mon, on):
    payload = bytes([1, 1 if on else 0, 0xFF, 0xFF, 0xFF])
    return mon.send_cmd(build_cmd(0x11, payload))


def free_mode(probe, seconds=0, results=None):
    print("\n=== 自由记录模式 ===")
    print("按任意键（包括你在 Windows 里改过映射的键），松开后打印签名。")
    print("Ctrl-C 结束。\n")
    end = time.time() + seconds if seconds else None
    n = 0
    while end is None or time.time() < end:
        status, info = probe.capture_event(max_wait=1e9)
        if status != 'event':
            continue
        n += 1
        tag = '  ← 标准按键字节变了' if info['standard_byte2'] else ''
        print(f"  #{n}  {describe(info)}{tag}")
        if results is not None:
            results.append({'key': f'自由#{n}', 'status': 'ok', **info})
    print(f"共 {n} 个事件")


def save_and_report(mon, probe, results):
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = os.path.join(CAPTURE_DIR, f'buttons_{stamp}')
    with open(base + '.bin', 'wb') as f:
        for ts, d in mon.frames:
            f.write(struct.pack('<dI', ts, len(d)) + d)
    doc = {
        'when': stamp,
        'report_len': max(len(d) for _, d in mon.frames),
        'baseline': probe.baseline,
        'stable': probe.stable,
        'results': results,
    }
    with open(base + '.json', 'w') as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    print("\n=== 按键签名 ===")
    for r in results:
        if r['status'] == 'ok':
            print(f"  {r['key']:<14} {describe(r)}")
        else:
            print(f"  {r['key']:<14} {r['status']}")
    print(f"\n报文 {len(mon.frames)} 条 → {base}.bin")
    print(f"签名 → {base}.json")


if __name__ == '__main__':
    main()
