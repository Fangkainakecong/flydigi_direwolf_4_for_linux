#!/usr/bin/env python3
"""冰原狼4 2.4G: 老方言(XInput legacy)命令探测
依据 openflydigi findings-other-devices.md 对 Vader 4 Pro 2.4G 的实测:
  - 老方言帧 = 15字节 [0]=0xA5 [1]=cmdId [2]=sub 无校验, 写EP 0x05
  - 回包在32字节输入报文 offset 14 注入, ack在[15]
  - 识别=cmd16: DeviceType@16, uid@17-20, fw@21/22, battery@23, connect@25, slot@27
  - 配置版本读=cmd80 sub4: ids@17..24
实验: 基线读流 → 发cmd16 → 发80sub4 → (对照)发我们的A5 5A A5 cmd1 → 全程记录注入区
"""
import os, struct, sys, time, glob, subprocess, ctypes, collections

CLAIMINTERFACE   = (2<<30)|(4<<16)|(ord('U')<<8)|15
RELEASEINTERFACE = (2<<30)|(4<<16)|(ord('U')<<8)|16
SUBMITURB        = (2<<30)|(56<<16)|(ord('U')<<8)|10
REAPURBNDELAY    = (1<<30)|(8<<16)|(ord('U')<<8)|13
URB_INT = 1
EAGAIN = 11
URB_SIZE = 56

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

def build_legacy(cmd_id, sub=0, payload=b''):
    """老方言15字节帧: A5 cmdId sub payload... 无校验"""
    buf = bytearray(15)
    buf[0] = 0xA5; buf[1] = cmd_id; buf[2] = sub
    buf[3:3+len(payload)] = payload
    return bytes(buf)

def build_new(cmd_id, payload=b''):
    """我们已验证的 A5 5A A5 信封(32字节)"""
    buf = bytearray(32)
    buf[0] = 0xA5; buf[1] = 0x5A; buf[2] = 0xA5; buf[3] = cmd_id
    buf[4] = len(payload) + 2; buf[5:5+len(payload)] = payload
    buf[3+buf[4]] = sum(buf[3:3+buf[4]]) & 0xFF
    return bytes(buf)

def find_pad():
    for bus in sorted(glob.glob('/dev/bus/usb/*/*')):
        try:
            fd = os.open(bus, os.O_RDONLY); d = os.pread(fd, 18, 0); os.close(fd)
        except OSError: continue
        if len(d) < 18: continue
        vid, pid = struct.unpack_from('<HH', d, 8)
        if vid == 0x045e and pid == 0x028e: return bus
    return None

DEV = find_pad()
if not DEV:
    print("未找到手柄(045e:028e)"); sys.exit(1)
print(f"手柄: {DEV}")

pad = Pad(DEV)
subprocess.run(['modprobe', '-r', 'xpad'], check=False)
time.sleep(0.3)

saved = []          # (phase, ts, packet)
claimed = False
try:
    pad.claim(); claimed = True
    print("接口0已占用")

    def pump(n=2):
        for _ in range(n): pad.submit(URB_INT, 0x81, None, 64)
    pump(8)

    def listen(seconds, phase):
        t0 = time.time()
        while time.time() - t0 < seconds:
            got = pad.reap()
            if got is None: time.sleep(0.001); continue
            userctx, st, d = got
            if not d: continue
            saved.append((phase, time.time() - t0, d))
            pump()

    # ① 基线 2秒
    print("\n① 基线读流 2秒 (注入区[13..20]应为空)")
    listen(2.0, "baseline")

    # ② 老方言 cmd16 识别 ×2
    print("② 发老方言帧 cmd16(识别) ×2, 监听1秒")
    for k in range(2):
        tgt = pad.submit(URB_INT, 0x05, build_legacy(16), 15)
        time.sleep(0.05)
        # 取回OUT
        t0 = time.time()
        while time.time() - t0 < 0.5:
            got = pad.reap()
            if got is None: time.sleep(0.002); continue
            userctx, st, d = got
            if userctx == tgt:
                print(f"   cmd16 OUT#{k+1} status={st}"); break
        listen(0.5, f"cmd16#{k+1}")

    # ③ 老方言 cmd80 sub4 配置版本 ×2
    print("③ 发老方言帧 cmd80 sub4(配置版本) ×2, 监听1秒")
    for k in range(2):
        tgt = pad.submit(URB_INT, 0x05, build_legacy(80, sub=4), 15)
        time.sleep(0.05)
        t0 = time.time()
        while time.time() - t0 < 0.5:
            got = pad.reap()
            if got is None: time.sleep(0.002); continue
            userctx, st, d = got
            if userctx == tgt:
                print(f"   cmd80 OUT#{k+1} status={st}"); break
        listen(0.5, f"cmd80sub4#{k+1}")

    # ④ 对照: 我们的 A5 5A A5 cmd1
    print("④ 对照: A5 5A A5 信封 cmd1, 监听1秒")
    tgt = pad.submit(URB_INT, 0x05, build_new(0x01), 32)
    time.sleep(0.05)
    t0 = time.time()
    while time.time() - t0 < 0.5:
        got = pad.reap()
        if got is None: time.sleep(0.002); continue
        userctx, st, d = got
        if userctx == tgt:
            print(f"   cmd1 OUT status={st} 回包={d.hex(' ') if d else '(无)'}"); break
    listen(0.5, "newenv_cmd1")

    # ⑤ 尾巴 1秒
    listen(1.0, "tail")

finally:
    if claimed:
        try: pad.release()
        except OSError: pass
    subprocess.run(['modprobe', 'xpad'], check=False)
    print("xpad 已重载, 手柄恢复")

with open('/tmp/opencode/old_dialect.bin', 'wb') as f:
    for phase, ts, d in saved:
        f.write(struct.pack('<dI', ts, len(d)) + phase.encode().ljust(16) + d)
print(f"\n全量 {len(saved)} 包已存 /tmp/opencode/old_dialect.bin")

# ---- 即时汇总: 按 [15] 值分组注入区包 ----
print("\n=== 注入区包统计 (按阶段 x [15]值) ===")
per = collections.Counter()
samples = {}
for phase, ts, d in saved:
    if any(d[13:20]):
        key = (phase.split('#')[0], d[15])
        per[key] += 1
        if key not in samples: samples[key] = d[13:22].hex(' ')
for k in sorted(per):
    print(f"  {k[0]:>12} [15]=0x{k[1]:02x}: {per[k]}包  样本[13..21]={samples[k]}")
if not per: print("  (无注入区包)")
