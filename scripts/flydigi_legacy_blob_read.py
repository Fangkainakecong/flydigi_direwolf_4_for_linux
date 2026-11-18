#!/usr/bin/env python3
"""冰原狼4 2.4G: 读全部 4 个板载配置槽位并对比

协议（Vader 4 Pro 实测，见 docs/findings-other-devices.md，本机已复现）：
  * 老方言帧 15 字节 = [A5, cmd, sub, ...payload]，写 EP 0x05，无校验
  * 回包注入在 32 字节输入报文 [14] 起，[15] = 命令号
  * 读配置 = cmd33 (0x21)，sub = 槽位，次字节 = 包大小 10
    回包 ack 34：索引 [16]，数据 10 字节 [17..26]
  * 版本 = cmd80 (0x50) sub 4 → 4 个槽位的 data_version（uint16 LE）
  * 识别 = cmd16 (0x10)：DeviceType [16]，uid [17..20]，固件 [21][22]，
    电量 [23]，连接 [25]，当前槽位 [27]

本脚本自动读 4 个槽位，解析键位表（偏移 13，32 槽 × 3 字节）并横向对比。

用法
----
    sudo python3 scripts/flydigi_legacy_blob_read.py          # 4 个槽位全读
    sudo python3 scripts/flydigi_legacy_blob_read.py --slot 2 # 只读一个
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
CMD_IDENTIFY, ACK_IDENTIFY = 16, 16
CMD_READ, ACK_READ = 33, 34
CMD_VERSION, ACK_VERSION = 80, 81

KEY_NAMES = {
    0: 'up', 1: 'right', 2: 'down', 3: 'left', 4: 'a', 5: 'b', 6: 'select',
    7: 'x', 8: 'y', 9: 'start', 10: 'lb', 11: 'rb', 12: 'lt', 13: 'rt',
    14: 'thl', 15: 'thr', 16: 'c', 17: 'z', 18: 'm1', 19: 'm2', 20: 'm3',
    21: 'm4', 22: 'm5', 23: 'm6', 24: 'menu', 25: 'turbo', 27: 'home',
    28: 'back',
}

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


def van(frames, cmd):
    """取出注入区以 [14]=A5、[15]=cmd 开头的回包"""
    return [d for d in frames if len(d) >= 32 and d[14] == 0xA5 and d[15] == cmd]


def identify(link):
    link.send(legacy(CMD_IDENTIFY))
    frames = link.collect(0.6)
    gotten = van(frames, ACK_IDENTIFY)
    if not gotten:
        return None
    d = gotten[0]
    return {
        'device_type': d[16],
        'uid': d[17:21].hex(),
        'fw': f'{d[21] >> 4}.{d[21] & 15}.{d[22] >> 4}.{d[22] & 15}',
        'battery': d[23],
        'connect': d[25],
        'slot': d[27],
        'raw': bytes(d[14:32]),
    }


def versions(link):
    link.send(legacy(CMD_VERSION, 4))
    frames = link.collect(0.6)
    gotten = van(frames, ACK_VERSION)
    if not gotten:
        return None
    d = gotten[0]
    return [struct.unpack_from('<H', d, 17 + 2 * i)[0] for i in range(4)], bytes(d[14:32])


def read_slot(link, slot):
    link.send(legacy(CMD_READ, slot, bytes([PKG])))
    frames = link.collect(1.5)
    acks = van(frames, ACK_READ)
    chunks = {}
    for d in acks:
        chunks.setdefault(d[16], bytes(d[17:17 + PKG]))
    if len(chunks) < 70:
        return None, len(chunks)
    total = max(chunks) + 1
    blob = bytearray(total * PKG)
    for i, c in chunks.items():
        blob[i * PKG:(i + 1) * PKG] = c
    return bytes(blob), len(chunks)


def key_table(blob):
    out = []
    for k in range(32):
        off = 13 + k * 3
        target, mode, freq = blob[off], blob[off + 1], blob[off + 2]
        out.append((KEY_NAMES.get(k, f'id{k}'), target, mode, freq))
    return out


def describe(blob):
    """把键位表翻译成人类可读的改动列表"""
    lines = []
    for name, target, mode, freq in key_table(blob):
        if target == 255 and freq == 0:
            continue
        tname = '自身' if target == 255 else KEY_NAMES.get(target, f'id{target}')
        if target == 254:
            tname = '键盘(死键)'
        elif target == 32:
            tname = '宏'
        turbo = f'  turbo(模式{mode}, 频率{freq})' if freq else ''
        lines.append(f'{name}→{tname}{turbo}')
    return lines


def title_of(blob):
    raw = blob[770:790]
    return raw.decode('utf-16-le', 'replace').rstrip('\uffff\x00')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slot', type=int, default=None)
    ap.add_argument('--save', action='store_true', default=True)
    args = ap.parse_args()

    dev = find_pad()
    if not dev:
        sys.exit("未找到手柄 (045e:028e)")
    print(f"手柄: {dev}")

    pad = Pad(dev)
    subprocess.run(['modprobe', '-r', 'xpad'], check=False)
    time.sleep(0.3)
    claimed = False
    saved = []
    try:
        pad.claim()
        claimed = True
        link = Link(pad)
        link.collect(0.3)

        info = identify(link)
        if info:
            print(f"\n识别: DeviceType={info['device_type']} uid={info['uid']} "
                  f"固件={info['fw']} 电量={info['battery']} "
                  f"连接={'有线' if info['connect'] == 1 else '2.4G'} "
                  f"当前槽位={info['slot']}")
        ver = versions(link)
        if ver:
            ids, raw = ver
            print(f"配置版本: " + '  '.join(f'槽位{i}={v} (0x{v:04x})'
                                            for i, v in enumerate(ids)))
            print(f"  [14..31] = {raw.hex(' ')}")

        slots = [args.slot] if args.slot is not None else [0, 1, 2, 3]
        blobs = {}
        for slot in slots:
            t0 = time.time()
            blob, got = read_slot(link, slot)
            if blob is None:
                print(f"\n槽位{slot}: 只收到 {got} 包，跳过")
                continue
            blobs[slot] = blob
            print(f"\n槽位{slot}: {len(blob)} 字节, {got} 包, "
                  f"data_version={struct.unpack_from('<H', blob, 225)[0]}, "
                  f"标题={title_of(blob)!r}")
            changes = describe(blob)
            if changes:
                print('  改动: ' + '   '.join(changes))
            else:
                print('  改动: 无（全默认键位）')

        if args.save and blobs:
            os.makedirs('captures', exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            for slot, blob in blobs.items():
                path = f'captures/blob_slot{slot}_{stamp}.bin'
                open(path, 'wb').write(blob)
                print(f"已存 {path}")

        if len(blobs) > 1:
            print("\n=== 槽位间差异（键位表 + 功能块）===")
            base = min(blobs)
            for slot in sorted(blobs):
                if slot == base:
                    continue
                diff = [i for i in range(min(len(blobs[base]), len(blobs[slot])))
                        if blobs[base][i] != blobs[slot][i]]
                print(f"  槽位{slot} vs 槽位{base}: {len(diff)} 字节不同")
                for i in diff[:40]:
                    print(f'    {i:3d}: {blobs[base][i]:3d} → {blobs[slot][i]:3d}')
                if len(diff) > 40:
                    print(f'    … 还有 {len(diff) - 40} 处')
    finally:
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        subprocess.run(['modprobe', 'xpad'], check=False)
        print("\nxpad 已重载")


if __name__ == '__main__':
    main()
