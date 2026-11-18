#!/usr/bin/env python3
"""受控运动实验: 开启指令17后捕获22秒, 分6个动作阶段, 用阶段间差异锁定体感字节
阶段: 0-3静止 | 3-7水平快转 | 7-10静止 | 10-13俯仰 | 13-16静止 | 16-19滚动 | 19-22静止
"""
import os, struct, sys, time, glob, subprocess, ctypes

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
    def send_cmd(self, cmd_id, payload=b''):
        buf = bytearray(32)
        buf[0] = 0xA5; buf[1] = 0x5A; buf[2] = 0xA5; buf[3] = cmd_id
        buf[4] = len(payload) + 2; buf[5:5+len(payload)] = payload
        buf[3+buf[4]] = sum(buf[3:3+buf[4]]) & 0xFF
        tgt = self.submit(URB_INT, 0x05, bytes(buf), 32)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            got = self.reap()
            if got is None: time.sleep(0.002); continue
            userctx, st, data = got
            if userctx == tgt:
                if st != 0: raise OSError(f"OUT status={st}")
                return data
        raise TimeoutError(f"指令{cmd_id}超时")

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

PHASES = [(0, 3, "静止平放"), (3, 7, "水平快转(yaw)"), (7, 10, "静止"),
          (10, 13, "上下俯仰(pitch)"), (13, 16, "静止"),
          (16, 19, "左右滚动(roll)"), (19, 22, "静止")]
TOTAL = 22

claimed = False
try:
    pad.claim(); claimed = True
    print("接口0已占用")

    def pump(n=2):
        for _ in range(n): pad.submit(URB_INT, 0x81, None, 64)
    pump(8)

    ack = pad.send_cmd(0x01)
    print(f"指令1 ACK: {ack.hex(' ') if ack else '(无)'}")
    ack = pad.send_cmd(17, bytes([1, 1, 0xFF, 0xFF, 0xFF]))
    print(f"指令17 ACK: {ack.hex(' ') if ack else '(无)'}")

    print("\n=== 22秒受控捕获, 按下表操作 ===")
    for a, b, name in PHASES:
        print(f"  {a:2d}-{b:2d}秒: {name}")
    raw = []
    t0 = time.time()
    while time.time() - t0 < TOTAL:
        got = pad.reap()
        if got is None: time.sleep(0.001); continue
        userctx, st, d = got
        if not d: continue
        raw.append((time.time() - t0, d))
        pump()
    with open('/tmp/opencode/motion_phases.bin', 'wb') as f:
        for ts, d in raw:
            f.write(struct.pack('<dI', ts, len(d)) + d)
    print(f"\n全量 {len(raw)} 包已存 /tmp/opencode/motion_phases.bin")
finally:
    if claimed:
        try: pad.release()
        except OSError: pass
    subprocess.run(['modprobe', 'xpad'], check=False)
    print("xpad 已重载, 手柄恢复")
