#!/usr/bin/env python3
"""冰原狼4 全输入探针 —— 事件驱动，不需要掐秒表

覆盖范围
--------
所有按键：A/B/X/Y、LB/RB、LT/RT、L3/R3、Start/Back/Home、十字四向、
          M1/M2、FN、Turbo
所有轴：  左摇杆四方向满偏、右摇杆四方向满偏（扳机的模拟行程在 LT/RT 里一起测）

用法
----
    sudo python3 scripts/flydigi_button_probe.py
    sudo python3 scripts/flydigi_button_probe.py --only m1,m2,ls_right
    sudo python3 scripts/flydigi_button_probe.py --free
    sudo python3 scripts/flydigi_button_probe.py --note "Windows 全默认键位"

运行前：Windows 里把键位恢复成完全默认，关掉 Steam，手柄放桌上别碰。
运行结束自动关厂商流、释放接口、重载 xpad。

判定原理
--------
静止 2 秒采集基线，每个字节记录 min/max，再留一份等于噪声幅度的余量。
之后任何字节跳出 [min-余量, max+余量] 就算按键/轴动了：

- 数字位（基线 0）→ 任何置位都算，余量为 0
- 摇杆（基线 ±几百的抖动）→ 只有满偏才会越界，抖动和零漂不会误触发

事件在“所有字节回到范围内并保持 0.35 秒”时结束；M1 那种 30 Hz 通断、
FN 那种 7 Hz 脉冲都只算一次。每个输入 6 秒内没反应就自动跳过，最后列出
漏掉的项和重跑命令。
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


# ---------------------------------------------------------------- 输入清单
# (id, 显示名, 提示语, 类型)
INPUTS = [
    ('a', 'A', '按一次 A 键', 'button'),
    ('b', 'B', '按一次 B 键', 'button'),
    ('x', 'X', '按一次 X 键', 'button'),
    ('y', 'Y', '按一次 Y 键', 'button'),
    ('lb', 'LB', '按一次 LB（左肩键）', 'button'),
    ('rb', 'RB', '按一次 RB（右肩键）', 'button'),
    ('lt', 'LT', '把 LT 扳机按到底并保持约 1 秒，然后松开', 'button'),
    ('rt', 'RT', '把 RT 扳机按到底并保持约 1 秒，然后松开', 'button'),
    ('l3', 'L3', '把左摇杆垂直按下（L3），然后松开', 'button'),
    ('r3', 'R3', '把右摇杆垂直按下（R3），然后松开', 'button'),
    ('start', 'Start', '按一次 Start', 'button'),
    ('back', 'Back', '按一次 Back（Select）', 'button'),
    ('home', 'Home', '按一次 Home（LOGO 键）', 'button'),
    ('dup', '十字-上', '按一次十字键 上', 'button'),
    ('ddown', '十字-下', '按一次十字键 下', 'button'),
    ('dleft', '十字-左', '按一次十字键 左', 'button'),
    ('dright', '十字-右', '按一次十字键 右', 'button'),
    ('m1', 'M1', '按一次 M1（背键）', 'button'),
    ('m2', 'M2', '按一次 M2（背键）', 'button'),
    ('fn', 'FN', '按住 FN 约 1 秒，然后松开', 'button'),
    ('turbo', 'Turbo', '按一次 Turbo 键', 'button'),
    ('ls_right', '左摇杆→右', '把左摇杆推到最右并保持约 1 秒，然后回中', 'axis'),
    ('ls_up', '左摇杆→上', '把左摇杆推到最上（前）并保持约 1 秒，然后回中', 'axis'),
    ('ls_left', '左摇杆→左', '把左摇杆推到最左并保持约 1 秒，然后回中', 'axis'),
    ('ls_down', '左摇杆→下', '把左摇杆推到最下（后）并保持约 1 秒，然后回中', 'axis'),
    ('rs_right', '右摇杆→右', '把右摇杆推到最右并保持约 1 秒，然后回中', 'axis'),
    ('rs_up', '右摇杆→上', '把右摇杆推到最上并保持约 1 秒，然后回中', 'axis'),
    ('rs_left', '右摇杆→左', '把右摇杆推到最左并保持约 1 秒，然后回中', 'axis'),
    ('rs_down', '右摇杆→下', '把右摇杆推到最下并保持约 1 秒，然后回中', 'axis'),
]
INPUT_BY_ID = {i[0]: i for i in INPUTS}


# ---------------------------------------------------------------- 传输层
_LIBC = ctypes.CDLL(None, use_errno=True)


def _ioctl(fd, req, addr):
    """libc.ioctl + c_void_p：fcntl.ioctl 会复制缓冲区，内核会拿到失效地址"""
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


# ---------------------------------------------------------------- 监听
class Monitor:
    """持续提交 IN URB，把收到的报文连时间戳一起存起来"""

    def __init__(self, pad, max_frames=600000):
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
        self.lo = []            # 每个字节的基线最小值
        self.hi = []            # 每个字节的基线最大值
        self.margin = []        # 允许的额外偏移（等于噪声幅度）
        self.len = 0

    def calibrate(self, seconds=2.0):
        print(f"\n>>> 校准基线：{seconds:.0f} 秒内请不要碰手柄（摇杆也别碰）")
        for i in (2, 1):
            print(f"    {i}...", end='', flush=True)
            time.sleep(1)
        print()
        frames = [d for d in self.mon.collect(seconds) if len(d) >= 4]
        if not frames:
            raise SystemExit("没有收到任何报文，检查 dongle 是否插好")
        self.len = max(len(d) for d in frames)
        self.lo = [min(d[i] for d in frames if i < len(d)) for i in range(self.len)]
        self.hi = [max(d[i] for d in frames if i < len(d)) for i in range(self.len)]
        self.margin = []
        for lo, hi in zip(self.lo, self.hi):
            rng = hi - lo
            self.margin.append(0 if rng == 0 else max(2, rng))
        noisy = [(i, self.lo[i], self.hi[i]) for i in range(self.len) if self.hi[i] != self.lo[i]]
        print(f"    报文 {len(frames)} 条，长度 {self.len}")
        if noisy:
            shown = ', '.join(f'[{i}] {lo}..{hi}' for i, lo, hi in noisy[:8])
            print(f"    有抖动的字节 {len(noisy)} 个：{shown}{' …' if len(noisy) > 8 else ''}")
        else:
            print("    所有字节静止（基线干净）")
        print(f"    基线标准按键字节 [2] = 0x{self.lo[2]:02x}" if self.len > 2 else "")

    def diff(self, d):
        out = {}
        for i in range(min(len(d), self.len)):
            v = d[i]
            if v < self.lo[i] - self.margin[i] or v > self.hi[i] + self.margin[i]:
                out[i] = v
        return out

    def settle(self, seconds=0.3):
        """把“当前稳定但越界”的字节重新当成基线。

        摇杆用一会儿会自动重新校零，静止位置和开局基线可能差很多；
        不修正的话，后面的每个事件都会一直“未复位”，级联误判。
        只有整个窗口内恒定不变的字节才会被吸收，所以按下的按键不会被吞掉。
        """
        frames = [d for d in self.mon.collect(seconds) if len(d) >= 4]
        if not frames:
            return
        n = min(self.len, max(len(d) for d in frames))
        shifted = 0
        for i in range(n):
            vals = {d[i] for d in frames}
            if len(vals) != 1:
                continue
            v = next(iter(vals))
            if v < self.lo[i] - self.margin[i] or v > self.hi[i] + self.margin[i]:
                self.lo[i] = self.hi[i] = v
                shifted += 1
        if shifted:
            print(f"    （{shifted} 个字节的静止位置已漂移，重新对齐）")

    def capture_event(self, max_wait=6.0, quiet=0.35):
        """等一个输入事件。返回 (status, info)，status ∈ event/skip/timeout"""
        self.settle()
        start = time.time()
        press_start = None
        last_seen = None
        seen = {}               # 字节 -> [min, max]
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
                    cur = seen.get(i)
                    if cur is None:
                        seen[i] = [v, v]
                    else:
                        cur[0] = min(cur[0], v)
                        cur[1] = max(cur[1], v)
            elif press_start is not None and now - last_seen > quiet:
                return 'event', self._info(seen, press_start, last_seen,
                                           first_frame, last_frame, False)
        if press_start is not None:      # 超时但确实动过：照样记下来
            return 'event', self._info(seen, press_start, last_seen,
                                       first_frame, last_frame, True)
        return 'timeout', None

    def _info(self, seen, t0, t1, f0, f1, truncated):
        return {
            'bytes': {i: {'min': mn, 'max': mx,
                          'base_lo': self.lo[i] if i < self.len else None,
                          'base_hi': self.hi[i] if i < self.len else None}
                      for i, (mn, mx) in seen.items()},
            'duration': round(t1 - t0, 3),
            'standard_byte2': 2 in seen,
            'truncated': truncated,
            'frames': f1 - f0 + 1,
        }


def describe(info):
    parts = []
    for i, b in info['bytes'].items():
        if b['min'] == b['max']:
            parts.append(f'[{i}]=0x{b["min"]:02x}')
        else:
            parts.append(f'[{i}]=0x{b["min"]:02x}..0x{b["max"]:02x}')
    flag = '  (超时截断)' if info['truncated'] else ''
    flag += '  ← 标准[2]变了' if info['standard_byte2'] else ''
    return f"{'  '.join(parts)}   持续 {info['duration']:.2f}s{flag}"


def ask_input(probe, spec, results, tag='', max_wait=6.0):
    key_id, name, text, kind = spec
    while True:
        print(f"\n>>> {text}")
        print("    （直接回车 = 跳过，输入 r = 重测）")
        status, info = probe.capture_event(max_wait=max_wait)
        if status == 'retry':
            continue
        if status == 'event':
            print(f"    ✓ {name}: {describe(info)}")
            results.append({'id': key_id + tag, 'name': name + tag, 'kind': kind,
                            'status': 'ok', **info})
            return
        if status == 'skip':
            results.append({'id': key_id + tag, 'name': name + tag, 'kind': kind,
                            'status': 'skipped'})
            print("    - 已跳过")
            return
        print(f"    ✗ {max_wait:.0f} 秒内没检测到（记录为未识别）")
        results.append({'id': key_id + tag, 'name': name + tag, 'kind': kind,
                        'status': 'timeout'})
        return


# ---------------------------------------------------------------- 主流程
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
        print(f"  #{n}  {describe(info)}")
        if results is not None:
            results.append({'id': f'free{n}', 'name': f'自由#{n}', 'kind': 'free',
                            'status': 'ok', **info})
    print(f"共 {n} 个事件")


def main():
    ap = argparse.ArgumentParser(description='冰原狼4 全输入探针（事件驱动，不需要掐秒表）')
    ap.add_argument('--only', default='', help='只测这些 id，逗号分隔（见脚本顶部 INPUTS）')
    ap.add_argument('--free', action='store_true', help='直接进入自由记录模式')
    ap.add_argument('--no-raw', action='store_true', help='不发 cmd17，保持默认状态')
    ap.add_argument('--free-seconds', type=float, default=0, help='自由模式自动结束秒数')
    ap.add_argument('--note', default='', help='记进 json 的备注，例如 “Windows 全默认键位”')
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("需要 sudo 运行：sudo python3 scripts/flydigi_button_probe.py")
        sys.exit(1)

    dev = find_pad()
    if not dev:
        print("未找到手柄 (045e:028e)")
        sys.exit(1)
    print(f"手柄: {dev}")

    for d in glob.glob('/sys/bus/usb/devices/*'):
        try:
            vid = open(d + '/idVendor').read().strip()
            pid = open(d + '/idProduct').read().strip()
        except OSError:
            continue
        if vid == '045e' and pid == '028e':
            try:
                open(d + '/power/control', 'w').write('on')
            except OSError:
                pass

    if subprocess.run(['pgrep', '-x', 'steam'], capture_output=True).stdout.strip():
        print("\n⚠ 检测到 Steam 在运行，它会在 xpad 卸载时弹提示、并可能抢设备。")
        input("  建议先 pkill -TERM steam。回车继续，Ctrl-C 退出 > ")

    print("\n⚠ 手柄放桌上；只有提示时才动手。每个输入 6 秒内没反应就自动跳过。")

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

        if args.only:
            ids = [s.strip() for s in args.only.split(',') if s.strip()]
            unknown = [s for s in ids if s not in INPUT_BY_ID]
            if unknown:
                raise SystemExit(f"未知 id: {unknown}\n可用: {', '.join(INPUT_BY_ID)}")
            todo = [INPUT_BY_ID[s] for s in ids]
        else:
            todo = INPUTS

        if args.free:
            probe.calibrate()
            if not args.no_raw:
                raw_enabled = send_raw(mon, 1) == 0
                print(f"    cmd17: {'已开启' if raw_enabled else '无响应'}")
                probe.calibrate(1.5)
            free_mode(probe, args.free_seconds, results)
            return

        # 阶段 A：不开厂商流，只测 M1 —— 回答“默认可见吗”
        probe.calibrate()
        if any(t[0] == 'm1' for t in todo) and not args.no_raw:
            print("\n=== 阶段 A：不开厂商流（cmd17 之前）===")
            print("这一步回答：M1 在默认状态下，Linux 能不能看到。")
            print("如果没反应是正常的 —— 6 秒后自动继续。")
            ask_input(probe, INPUT_BY_ID['m1'], results, tag='(raw关闭)')

        if not args.no_raw:
            print("\n=== 阶段 B：开启厂商流（cmd17）===")
            ack = send_raw(mon, 1)
            raw_enabled = ack == 0
            print(f"    cmd17: {'成功' if raw_enabled else '无响应'}（OUT status={ack}）")
            probe.calibrate(1.5)

        print(f"\n=== 开始逐个测试：共 {len(todo)} 项 ===")
        for spec in todo:
            ask_input(probe, spec, results)

        missed = [r['id'] for r in results
                  if r['status'] != 'ok' and not r['id'].endswith('(raw关闭)')]
        if missed:
            print("\n=== 没识别到的项 ===")
            print("  " + ', '.join(missed))
            print("  重跑：sudo python3 scripts/flydigi_button_probe.py --only "
                  + ','.join(missed))

        if input("\n进入自由记录模式？按任意键打印签名（验证板载映射用）[y/N] > ").strip().lower() == 'y':
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
            save_and_report(mon, probe, results, args.note)


def save_and_report(mon, probe, results, note=''):
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = os.path.join(CAPTURE_DIR, f'inputs_{stamp}')
    with open(base + '.bin', 'wb') as f:
        for ts, d in mon.frames:
            f.write(struct.pack('<dI', ts, len(d)) + d)
    doc = {
        'when': stamp,
        'note': note,
        'report_len': max(len(d) for _, d in mon.frames),
        'baseline': {'lo': probe.lo, 'hi': probe.hi, 'margin': probe.margin},
        'results': results,
    }
    with open(base + '.json', 'w') as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    print("\n=== 输入签名 ===")
    for r in results:
        if r['status'] == 'ok':
            print(f"  {r['name']:<12} {describe(r)}")
        else:
            print(f"  {r['name']:<12} {r['status']}")
    print(f"\n报文 {len(mon.frames)} 条 → {base}.bin")
    print(f"签名 → {base}.json")


if __name__ == '__main__':
    main()
