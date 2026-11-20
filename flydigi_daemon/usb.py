#!/usr/bin/env python3
"""冰原狼4 2.4G usbfs 传输层 + 老方言命令（可复用模块）

这个文件不单独运行，给探针脚本和以后的 daemon 共用，避免每个脚本
复制 200 行 ioctl 代码。里面全部是已验证过的实现：

* usbfs 异步 URB（`URB_SIZE=56`，URB 和数据缓冲必须一直持有到 reap）
* 老方言帧 15 字节 `[0]=0xA5, [1]=cmd, [2]=sub`，写 EP 0x05
* 回包注入在 32 字节输入报文 `[14]` 起，`[15]` 是应答命令号
* 识别 cmd16 / 版本 cmd80 sub4 / 读配置 cmd33→ack34（10 字节/包）

用法示例::

    from flydigi_usb import find_pad, Pad, Link, identify, read_slot

    dev = find_pad()
    pad = Pad(dev)
    pad.takeover()                 # 解绑 xpad 并 claim（失败自动退回 modprobe）
    link = Link(pad)
    print(identify(link))
    blob, pkts = read_slot(link, 0)

所有调用都需要 root（或用 udev 规则给当前用户 USB 权限）。
"""

import ctypes
import glob
import os
import struct
import subprocess
import time

VID, PID = 0x045E, 0x028E
PRODUCT_HINT = "Flydigi Direwolf 4"

# ---- usbdevice_fs ioctl ---------------------------------------------------
# 数值来自 /usr/include/linux/usbdevice_fs.h；用 libc 的 ioctl 直接传指针，
# 不要用 fcntl.ioctl（它会把 64 位地址截断成 32 位）。
CLAIMINTERFACE = (2 << 30) | (4 << 16) | (ord('U') << 8) | 15
RELEASEINTERFACE = (2 << 30) | (4 << 16) | (ord('U') << 8) | 16
SUBMITURB = (2 << 30) | (56 << 16) | (ord('U') << 8) | 10
REAPURBNDELAY = (1 << 30) | (8 << 16) | (ord('U') << 8) | 13
DISCARDURB = (0 << 30) | (0 << 16) | (ord('U') << 8) | 11
DISCONNECT = (0 << 30) | (0 << 16) | (ord('U') << 8) | 22
CONNECT = (0 << 30) | (0 << 16) | (ord('U') << 8) | 23

URB_INT = 1
URB_SIZE = 56
EAGAIN = 11
INFLIGHT = 8

EP_IN = 0x81
EP_OUT = 0x05

CMD_IDENTIFY = 16
CMD_READ = 33
ACK_READ = 34
CMD_VERSION = 80
ACK_VERSION = 81


class CUrb(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ubyte), ('endpoint', ctypes.c_ubyte),
                ('status', ctypes.c_int), ('flags', ctypes.c_uint),
                ('buffer', ctypes.c_void_p), ('buffer_length', ctypes.c_int),
                ('actual_length', ctypes.c_int), ('start_frame', ctypes.c_int),
                ('number_of_packets', ctypes.c_int), ('error_count', ctypes.c_int),
                ('signr', ctypes.c_uint), ('usercontext', ctypes.c_void_p)]


_LIBC = ctypes.CDLL(None, use_errno=True)


def _ioctl(fd, req, addr=0):
    r = _LIBC.ioctl(ctypes.c_int(fd), ctypes.c_ulong(req), ctypes.c_void_p(addr))
    if r == -1:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return r


def status_name(status):
    """URB status 是人类可读的负数 errno。"""
    return {
        0: 'ok', 75: 'EOVERFLOW', 19: 'ENODEV', 32: 'EPIPE(STALL)',
        100: 'ENETRESET', 104: 'ECONNRESET(cancelled)', 5: 'EIO',
        110: 'ETIMEDOUT', 71: 'EPROTO',
    }.get(abs(status), f'errno{abs(status)}')


class Pad(object):
    """独占一个 USB 接口的简单异步 URB 封装。

    关键点：`pending` 里的 URB 结构和数据缓冲必须存活到 reap，否则内核
    仍然持有指针，Python GC 一回收就会读到垃圾或段错误。
    """

    def __init__(self, devpath, interface=0):
        self.devpath = devpath
        self.interface = interface
        self.fd = os.open(devpath, os.O_RDWR)
        self.pending = {}

    # -- 驱动解绑 --------------------------------------------------------
    def disconnect_driver(self):
        """让内核驱动（xpad）放开接口。

        优先用 USBDEVFS_DISCONNECT，它只针对当前接口，不影响其它 USB 设备；
        但实测这个 ioctl 在部分内核/设备上不可靠，所以失败（或随后 claim
        仍然 EBUSY）时退回 `modprobe -r xpad`。返回实际使用的方法名。
        """
        try:
            _ioctl(self.fd, DISCONNECT)
            return 'USBDEVFS_DISCONNECT'
        except OSError:
            pass
        subprocess.run(['modprobe', '-r', 'xpad'], check=False)
        time.sleep(0.3)
        return 'modprobe -r xpad'

    def takeover(self):
        """解绑驱动并 claim 接口，返回实际生效的方法名。

        探针脚本应该用这个，而不是自己拼 disconnect + claim：DISCONNECT
        返回 0 不代表驱动真的放手了，必须用 claim 的结果说话。
        """
        method = self.disconnect_driver()
        try:
            self.claim()
            return method
        except OSError as e:
            if e.errno != 16:            # EBUSY：驱动还占着
                raise
        subprocess.run(['modprobe', '-r', 'xpad'], check=False)
        time.sleep(0.3)
        self.claim()
        return 'modprobe -r xpad（claim 回退）'

    def reconnect_driver(self):
        try:
            _ioctl(self.fd, CONNECT)
        except OSError:
            subprocess.run(['modprobe', 'xpad'], check=False)

    # -- 接口 ------------------------------------------------------------
    def claim(self):
        buf = ctypes.create_string_buffer(4)
        struct.pack_into('<I', buf, 0, self.interface)
        _ioctl(self.fd, CLAIMINTERFACE, ctypes.addressof(buf))

    def release(self):
        buf = ctypes.create_string_buffer(4)
        struct.pack_into('<I', buf, 0, self.interface)
        _ioctl(self.fd, RELEASEINTERFACE, ctypes.addressof(buf))

    # -- URB -------------------------------------------------------------
    def submit(self, xfer, ep, data=None, size=64):
        if data is not None:
            buf = ctypes.create_string_buffer(bytes(data), size)
        else:
            buf = ctypes.create_string_buffer(size)
        u = CUrb()
        u.type = xfer
        u.endpoint = ep & 0xFF
        u.buffer = ctypes.cast(buf, ctypes.c_void_p)
        u.buffer_length = size
        u.usercontext = ctypes.c_void_p(id(u))
        # 先入表再提交：提交后可能立刻完成，reap 时找不到就会丢包。
        self.pending[id(u)] = (u, buf)
        _ioctl(self.fd, SUBMITURB, ctypes.addressof(u))
        return id(u)

    def reap(self):
        """非阻塞取一个完成事件，返回 (uid, status, data)；没完成返回 None。"""
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

    def discard(self, uid):
        entry = self.pending.pop(uid, None)
        if entry is None:
            return
        u, _ = entry
        try:
            _ioctl(self.fd, DISCARDURB, ctypes.addressof(u))
        except OSError:
            pass

    def close(self):
        for uid in list(self.pending):
            self.discard(uid)
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def find_pad(vid=VID, pid=PID):
    """按 VID:PID 找 dongle 的 /dev/bus/usb/BBB/DDD。"""
    for path in sorted(glob.glob('/dev/bus/usb/*/*')):
        try:
            fd = os.open(path, os.O_RDONLY)
            head = os.pread(fd, 18, 0)
            os.close(fd)
        except OSError:
            continue
        if len(head) >= 18 and struct.unpack_from('<HH', head, 8) == (vid, pid):
            return path
    return None


# ---- 老方言 ---------------------------------------------------------------
def legacy(cmd, sub=0, payload=b''):
    """15 字节老方言帧：A5 cmd sub payload…，无校验，写 EP 0x05。"""
    buf = bytearray(15)
    buf[0] = 0xA5
    buf[1] = cmd
    buf[2] = sub
    buf[3:3 + len(payload)] = payload
    return bytes(buf)


class Link(object):
    """保持若干 IN URB 在途，同时收发老方言帧。

    回包不是独立端点，而是混在 1000Hz 手柄报文里；所以必须持续 pump，
    不然手柄的输入缓冲会溢出，体感和回包一起丢。
    """

    def __init__(self, pad, inflight=INFLIGHT):
        self.pad = pad
        self.inflight = inflight
        self.in_ids = set()
        self.last_data = time.time()
        self.pump(inflight)

    def pump(self, n=1):
        for _ in range(n):
            self.in_ids.add(self.pad.submit(URB_INT, EP_IN, None, 64))

    def send(self, data, wait=0.5, out_size=None):
        """发一帧并等它在 OUT URB 上完成；期间继续 reap IN 保持管道畅通。"""
        uid = self.pad.submit(URB_INT, EP_OUT, data, out_size or len(data))
        deadline = time.time() + wait
        while time.time() < deadline:
            got = self.pad.reap()
            if got is None:
                time.sleep(0.002)
                continue
            ctx, st, d = got
            if ctx in self.in_ids:
                self.in_ids.discard(ctx)
                self.pump(1)
                self.last_data = time.time()
                continue
            if ctx == uid:
                return st
        return None

    def _reset(self):
        for uid in list(self.in_ids):
            self.pad.discard(uid)
        self.in_ids.clear()
        self.pump(self.inflight)
        self.last_data = time.time()

    def reset_urbs(self):
        """公开版 `_reset`：看门狗发现长时间没数据时重新挂 URB。

        daemon 用这个而不是直接摸 `_reset`，方便以后加计数/日志。
        """
        self._reset()

    def collect(self, seconds):
        """收 N 秒的输入报文（原始 32 字节，未过滤）。"""
        out = []
        end = time.time() + seconds
        while time.time() < end:
            got = self.pad.reap()
            if got is None:
                if time.time() - self.last_data > 1.0:
                    self._reset()
                time.sleep(0.001)
                continue
            ctx, st, d = got
            if ctx in self.in_ids:
                self.in_ids.discard(ctx)
                self.pump(1)
                if d:
                    self.last_data = time.time()
                    out.append(d)
        return out

    def drain(self, seconds=0.05):
        """倒掉旧报文，避免拿到上一条命令的残余应答。"""
        return self.collect(seconds)


def van(frames, cmd=None):
    """过滤出注入区回包：`[14]==0xA5` 且（可选）`[15]==cmd`。"""
    out = []
    for d in frames:
        if len(d) >= 32 and d[14] == 0xA5 and (cmd is None or d[15] == cmd):
            out.append(d)
    return out


def identify(link, settle=0.6):
    """cmd16：DeviceType / uid / 固件 / 电量 / 连接方式 / 当前板载槽位。"""
    link.drain()
    link.send(legacy(CMD_IDENTIFY))
    frames = link.collect(settle)
    got = van(frames, CMD_IDENTIFY)
    if not got:
        return None
    d = got[0]
    return {
        'device_type': d[16],
        'uid': d[17:21].hex(),
        'fw': f'{d[21] >> 4}.{d[21] & 15}.{d[22] >> 4}.{d[22] & 15}',
        'battery': d[23],
        'connect': d[25],
        'slot': d[27],
        'raw': bytes(d[14:32]),
    }


def identify_retry(link, tries=4, delay=0.2, settle=0.6):
    """cmd16 带重试。

    FN 组合切板载槽之后，命令通道会安静一阵（输入流不受影响），
    所以 check/daemon 启动时不能只问一次。
    """
    for _ in range(tries):
        info = identify(link, settle=settle)
        if info:
            return info
        time.sleep(delay)
    return None


def versions(link, settle=0.6):
    """cmd80 sub4：四个槽位的 data_version（uint16 LE）。"""
    link.drain()
    link.send(legacy(CMD_VERSION, 4))
    frames = link.collect(settle)
    got = van(frames, ACK_VERSION)
    if not got:
        return None
    d = got[0]
    return ([struct.unpack_from('<H', d, 17 + 2 * i)[0] for i in range(4)],
            bytes(d[14:32]))


def read_slot(link, slot, pkg=10, timeout=1.5, min_packets=70):
    """cmd33 读一个槽位的 840 字节 blob。

    回包是连续 84 个 10 字节小包，可能乱序/重复；按索引拼。空槽位不回包。
    返回 (blob | None, 收到的包数)。
    """
    link.drain()
    link.send(legacy(CMD_READ, slot, bytes([pkg])), wait=0.05)
    frames = link.collect(timeout)
    chunks = {}
    for d in van(frames, ACK_READ):
        chunks.setdefault(d[16], bytes(d[17:17 + pkg]))
    if len(chunks) < min_packets:
        return None, len(chunks)
    total = max(chunks) + 1
    blob = bytearray(total * pkg)
    for i, c in chunks.items():
        blob[i * pkg:(i + 1) * pkg] = c
    return bytes(blob), len(chunks)


def keep_awake(seconds):
    """把 USB 自动挂起关掉（部分主机会在空闲时挂起 dongle）。"""
    for ctrl in glob.glob('/sys/bus/usb/devices/*/power/control'):
        try:
            if open(ctrl).read().strip() != 'on':
                open(ctrl, 'w').write('on')
        except OSError:
            pass
    time.sleep(seconds)
