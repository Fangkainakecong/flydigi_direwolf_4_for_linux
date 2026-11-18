#!/usr/bin/env python3
"""冰原狼4 2.4G: 老方言读板载配置 blob（cmd33 / ack34）

依据 docs/findings-other-devices.md（Vader 4 Pro 实测）：
  * 老方言帧 15 字节 = [A5, cmd, sub, ...payload]，写 EP 0x05，无校验
  * 回包注入在 32 字节输入报文 [14] 起，[15] = 命令号
  * 读配置 = 命令 33（0x21），回包 ack = 34（0x22），
    索引在 [16]，数据 10 字节在 [17..26]
  * 790 / 840 字节两种大小（79 / 84 包）

请求的 payload 形状文档没写死，这里依次试：
    A: A5 21 00 0A        (sub=槽位, 下一字节=包大小 10)
    B: A5 21 00           (只有槽位)
    C: A5 21 01 0A        (槽位 1)
    D: A5 21 00 00 0A     (槽位 + 2 字节包大小)
谁能连续吐出 >= 70 个不同索引就用谁，然后拼成 blob 存盘。

用法
----
    sudo python3 scripts/flydigi_legacy_blob_read.py
    sudo python3 scripts/flydigi_legacy_blob_read.py --slot 1
"""
import argparse
import collections
import ctypes
import glob
import os
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
PKG = 10
SEQ_CMD = 33
ACK_CMD = 34

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


class Link:
    def __init__(self, pad):
        self.pad = pad
        self.in_ids = set()
        self.last = time.time()
        self.pump(INFLIGHT)

    def pump(self, n=1):
        for _ in range(n):
            self.in_ids.add(self.pad.submit(URB_INT, 0x81, b'\0' * 64, 64))

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

    def collect(self, seconds):
        out = []
        end = time.time() + seconds
        while time.time() < end:
            got = self.pad.reap()
            if got is None:
                if time.time() - self.last > 1.0:
                    for uid in list(self.in_ids):
                        self.pad.discard(uid)
                        self.pad.pending.pop(uid, None)
                    self.in_ids.clear()
                    self.pump(INFLIGHT)
                    self.last = time.time()
                time.sleep(0.001)
                continue
            ctx, st, d = got
            if ctx in self.in_ids:
                self.in_ids.discard(ctx)
                self.pump(1)
                if d:
                    self.last = time.time()
                    out.append(d)
        return out


def try_variant(link, name, frame, seconds=2.0):
    print(f"\n--- {name}: {frame.hex(' ')} ---")
    st = link.send(frame)
    print(f"    OUT status={st}")
    frames = link.collect(seconds)
    acks = [d for d in frames if len(d) >= 32 and d[14] == 0xA5 and d[15] == ACK_CMD]
    idx = collections.Counter(d[16] for d in acks)
    print(f"    收到 {len(frames)} 帧，其中 cmd34 回包 {len(acks)} 个，"
          f"不同索引 {len(idx)}")
    if acks:
        for d in acks[:3]:
            print('    样本:', ' '.join('%02x' % b for b in d[14:27]))
        if len(idx) > 1:
            print(f"    索引范围 {min(idx)}..{max(idx)}"
                  f"  重复索引 {[k for k, v in idx.items() if v > 1][:5]}")
    return acks


def assemble(acks, pkg=PKG):
    chunks = {}
    for d in acks:
        chunks.setdefault(d[16], bytes(d[17:17 + pkg]))
    if not chunks:
        return b'', 0, []
    total = max(chunks) + 1

    blob = bytearray(total * pkg)
    for i, c in chunks.items():
        blob[i * pkg:(i + 1) * pkg] = c
    missing = sorted(set(range(total)) - set(chunks))
    return bytes(blob), total, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slot', type=int, default=0)
    ap.add_argument('--seconds', type=float, default=2.0)
    args = ap.parse_args()

    dev = find_pad()
    if not dev:
        sys.exit("未找到手柄 (045e:028e)")
    print(f"手柄: {dev}")

    pad = Pad(dev)
    subprocess.run(['modprobe', '-r', 'xpad'], check=False)
    time.sleep(0.3)
    claimed = False
    best = None
    try:
        pad.claim()
        claimed = True
        link = Link(pad)
        link.collect(0.3)

        s = args.slot
        variants = [
            (f'槽位{s} + 包大小10', legacy(SEQ_CMD, s, bytes([PKG]))),
            (f'槽位{s} 单独', legacy(SEQ_CMD, s)),
            (f'槽位{s} + 大小 00 0A', legacy(SEQ_CMD, s, bytes([0, PKG]))),
            (f'槽位{s} + 大小 0A 00', legacy(SEQ_CMD, s, bytes([PKG, 0]))),
        ]
        for name, frame in variants:
            acks = try_variant(link, name, frame, args.seconds)
            if len({d[16] for d in acks}) >= 70:
                best = (name, acks)
                break

        if not best:
            print("\n没有任何形状吐出 >= 70 个索引，把上面样本发我。")
            return

        name, acks = best
        print(f"\n=== 用 '{name}' 拼 blob ===")
        blob, total, missing = assemble(acks)
        print(f"  包数 {total}  字节 {len(blob)}  缺包 {missing[:10]}"
              f"{' …' if len(missing) > 10 else ''}")
        os.makedirs('captures', exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        base = f'captures/blob_slot{args.slot}_{stamp}'
        open(base + '.bin', 'wb').write(blob)
        print(f"  已存 {base}.bin")

        print("\n  前 96 字节:")
        for off in range(0, min(96, len(blob)), 16):
            row = blob[off:off + 16]
            print(f'    {off:04x}  ' + ' '.join('%02x' % b for b in row))

        print("\n  非零区段:")
        runs = []
        start = None
        for i, b in enumerate(blob):
            if b and start is None:
                start = i
            elif not b and start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, len(blob)))
        for a, b in runs[:30]:
            chunk = blob[a:min(b, a + 16)]
            print(f'    {a:04x}..{b - 1:04x} ({b - a:3d}B)  '
                  + ' '.join('%02x' % x for x in chunk))
        print(f"    共 {len(runs)} 段")
    finally:
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        subprocess.run(['modprobe', 'xpad'], check=False)
        print("xpad 已重载")


if __name__ == '__main__':
    main()
