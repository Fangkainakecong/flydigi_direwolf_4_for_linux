#!/usr/bin/env python3
"""终极诊断v2: 修复ioctl临时缓冲bug(改传整数地址) + usbmon进程内直读 + 内核时间阶段归类
阶段A: xpad在位基线3秒 → B: 卸xpad+独占+读IN → C: rumble×3 → D: 厂商指令1 → E: 收尾"""
import fcntl, os, struct, sys, time, glob, subprocess, ctypes, collections

CLAIMINTERFACE   = (2<<30)|(4<<16)|(ord('U')<<8)|15
RELEASEINTERFACE = (2<<30)|(4<<16)|(ord('U')<<8)|16
SUBMITURB        = (2<<30)|(56<<16)|(ord('U')<<8)|10
REAPURBNDELAY    = (1<<30)|(8<<16)|(ord('U')<<8)|13
DISCARDURB       = (0<<30)|(0<<16)|(ord('U')<<8)|11
URB_INT = 1
EAGAIN = 11
URB_SIZE = 56

# 直接用libc的ioctl: 完整64位指针参数, 无Python层的复制/截断
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
        buf = ctypes.create_string_buffer(4)
        struct.pack_into('<I', buf, 0, 0)
        _ioctl(self.fd, CLAIMINTERFACE, ctypes.addressof(buf))
    def release(self):
        buf = ctypes.create_string_buffer(4)
        struct.pack_into('<I', buf, 0, 0)
        _ioctl(self.fd, RELEASEINTERFACE, ctypes.addressof(buf))
    def submit(self, xfer, ep, data=None, size=64):
        buf = (ctypes.create_string_buffer(bytes(data), size)
               if data is not None else ctypes.create_string_buffer(size))
        u = CUrb()
        u.type = xfer; u.endpoint = ep & 0xFF
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
            if e.errno == EAGAIN: return None
            raise
        (urbptr,) = struct.unpack('<Q', ctypes.string_at(ctypes.addressof(p), 8))
        if not urbptr: return None
        urb = ctypes.string_at(urbptr, URB_SIZE)
        status,     = struct.unpack_from('<i', urb, 4)
        bufptr,     = struct.unpack_from('<Q', urb, 16)
        actual_len, = struct.unpack_from('<i', urb, 28)
        userctx,    = struct.unpack_from('<Q', urb, 48)
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
            fd = os.open(bus, os.O_RDONLY); d = os.pread(fd, 18, 0); os.close(fd)
        except OSError: continue
        if len(d) < 18: continue
        vid, pid = struct.unpack_from('<HH', d, 8)
        if vid == 0x045e and pid == 0x028e: return bus
    return None

def errno_name(st):
    m = {75:'EOVERFLOW', 19:'ENODEV', 32:'EPIPE(STALL)', 100:'ENETRESET',
         104:'ECONNRESET(取消)', 5:'EIO', 110:'ETIMEDOUT', 71:'EPROTO'}
    return m.get(abs(st), f'errno{abs(st)}')

def build_cmd(cmd_id, payload=b''):
    buf = bytearray(32)
    buf[0] = 0xA5; buf[1] = 0x5A; buf[2] = 0xA5; buf[3] = cmd_id
    buf[4] = len(payload) + 2; buf[5:5+len(payload)] = payload
    buf[3+buf[4]] = sum(buf[3:3+buf[4]]) & 0xFF
    return bytes(buf)

DEV = find_pad()
if not DEV:
    print("未找到手柄(045e:028e)"); sys.exit(1)
BUS = int(DEV.split('/')[4])
MON = f'/sys/kernel/debug/usb/usbmon/{BUS}u'
WANT_DEV = DEV.split('/')[-1]
print(f"手柄: {DEV}   usbmon: {MON}   目标dev号: {WANT_DEV}")

try:
    cur = open('/sys/bus/usb/devices/2-1/power/control').read().strip()
    if cur != 'on':
        open('/sys/bus/usb/devices/2-1/power/control', 'w').write('on')
        print(f"USB自动休眠: {cur} → on")
except OSError: pass

subprocess.run("mount -t debugfs none /sys/kernel/debug 2>/dev/null", shell=True, check=False)
subprocess.run(['modprobe', 'usbmon'], check=False)
time.sleep(0.3)
if not os.path.exists(MON):
    print(f"usbmon节点不存在: {MON}"); sys.exit(1)

# usbmon时间戳是"开机秒数", 用/proc/uptime对齐
T_BOOT = float(open('/proc/uptime').read().split()[0])
print("\n⚠ 先按Home/摇杆唤醒手柄并确认LED亮, 然后全程保持摇杆动作")

monf = open(MON, 'r', errors='replace')   # 进程内直读, 无缓冲丢失
T0 = float(open('/proc/uptime').read().split()[0])
phases = []
def mark(name):
    phases.append((name, float(open('/proc/uptime').read().split()[0])))
    print(f"\n===== {name} (t={phases[-1][1]-T0:.1f}s) =====")

time.sleep(0.5)
mark("A: xpad在位基线3秒")
time.sleep(3)

pad = Pad(DEV)
subprocess.run(['modprobe', '-r', 'xpad'], check=False)
time.sleep(0.3)
pad.claim()
mark("B: 卸xpad+独占+读IN 3秒")
n_in = 0; samples = []
def pump(n=4):
    for _ in range(n): pad.submit(URB_INT, 0x81, None, 64)
pump(8)
t0 = time.time()
while time.time() - t0 < 3:
    got = pad.reap()
    if got is None: time.sleep(0.002); continue
    ctx, st, d = got
    if st != 0: print(f"   IN错误 status={st} ({errno_name(st)})")
    if d:
        n_in += 1
        if len(samples) < 3: samples.append(d.hex(' '))
        pump()
print(f"   B阶段IN包数={n_in}  样本={samples}")

mark("C: 发rumble×3")
rumble = bytes([0x00, 0x01, 0x0F, 0xC0, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00])
for k in range(3):
    rid = pad.submit(URB_INT, 0x05, rumble, 12)
    done = False; t0 = time.time()
    while time.time() - t0 < 1:
        got = pad.reap()
        if got is None: time.sleep(0.002); continue
        ctx, st, d = got
        if ctx == rid:
            print(f"   rumble{k+1}: OUT完成 status={st} ({errno_name(st)})")
            done = True; break
        if d: pass
    if not done:
        print(f"   rumble{k+1}: 1秒未完成")
        try:
            pad.discard(rid)
            t0 = time.time()
            while time.time() - t0 < 1:
                got = pad.reap()
                if got is None: time.sleep(0.002); continue
                ctx, st, d = got
                if ctx == rid:
                    print(f"   rumble{k+1}: 取回 status={st} ({errno_name(st)})")
                    break
        except OSError as e: print(f"   discard失败: {e}")
    time.sleep(0.2)

mark("D: 发厂商指令1")
rid = pad.submit(URB_INT, 0x05, build_cmd(0x01), 32)
done = False; t0 = time.time()
while time.time() - t0 < 2:
    got = pad.reap()
    if got is None: time.sleep(0.002); continue
    ctx, st, d = got
    if ctx == rid:
        print(f"   指令1: OUT完成 status={st} 回包={d.hex(' ') if d else '(无)'}")
        done = True; break
if not done:
    print("   指令1: 2秒未完成")
    try:
        pad.discard(rid)
        t0 = time.time()
        while time.time() - t0 < 1:
            got = pad.reap()
            if got is None: time.sleep(0.002); continue
            ctx, st, d = got
            if ctx == rid:
                print(f"   指令1: 取回 status={st} ({errno_name(st)})")
                break
    except OSError as e: print(f"   discard失败: {e}")

mark("E: 收尾")
time.sleep(0.5)
try: pad.release()
except OSError: pass
subprocess.run(['modprobe', 'xpad'], check=False)
time.sleep(1.0)

# ---- 汇总: 读出usbmon自打开以来所有行 ----
print("\n开始汇总线上数据...")
lines = monf.readlines()
monf.close()
print(f"共 {len(lines)} 行")
per = collections.defaultdict(int)
samples_by_phase = collections.defaultdict(list)
errlines = []
def phase_of(ts):
    idx = 0
    for i, (nm, st) in enumerate(phases):
        if ts >= st - T_BOOT + T_BOOT:  # ts本身就是开机秒数
            idx = i
    return idx
def phase_name(ts):
    idx = 0
    for i, (nm, st) in enumerate(phases):
        if ts >= st: idx = i
    return phases[idx][0]

for line in lines:
    parts = line.split()
    if len(parts) < 5: continue
    try: ts = float(parts[1])
    except ValueError: continue
    tok = parts[3] if len(parts) > 3 else ''
    if ':' not in tok: continue
    f = tok.split(':')
    if len(f) < 4 or f[1] != '2' or f[2] != WANT_DEV: continue
    evt = parts[2]; typ = f[0]
    key = (phase_name(ts), evt, typ)
    per[key] += 1
    if evt == 'C' and '<' in line:
        d = line.split('<', 1)[1].replace(' ', '').replace('\n', '')
        if d and len(samples_by_phase[key]) < 2:
            samples_by_phase[key].append(d[:48])
    if evt == 'C' and not line.rstrip().endswith('<') and '<' not in line:
        errlines.append(line.strip())

print("\n=== 线上事件汇总 (阶段 x 事件) ===")
for k in sorted(per): print("  ", k, per[k])
print("\n=== 各阶段C事件(完成)的数据样本 ===")
for k, v in samples_by_phase.items():
    print("  ", k, v)
print("\n=== 无数据的C事件(错误完成) ===")
for l in errlines[:10]: print("  ", l)
if not errlines: print("  (无)")
