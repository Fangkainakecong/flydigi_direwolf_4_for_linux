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
import errno
import glob
import os
import re
import struct
import subprocess
import time

VID, PID = 0x045E, 0x028E
# 空间站自己也靠产品字符串认 Direwolf 4：`045e:028e` 不是飞智独占，
# 黑武士 2、Vader 等 XInput 手柄也会用这个 ID。默认只匹配产品名里的
# "Direwolf 4"，避免把别的手柄当成冰原狼4。
PRODUCT_HINT = "Direwolf 4"

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


class PadBusyError(OSError):
    """接口仍被 xpad 占用，而且系统里还有别的 Xbox 手柄。"""


def _usb_ids(devpath):
    """从 USB 设备描述符读 VID:PID；读不到时用配置里的默认值。"""
    try:
        fd = os.open(devpath, os.O_RDONLY)
        try:
            head = os.pread(fd, 18, 0)
        finally:
            os.close(fd)
        if len(head) >= 18:
            return struct.unpack_from('<HH', head, 8)
    except OSError:
        pass
    return VID, PID


class Pad(object):
    """独占一个 USB 接口的简单异步 URB 封装。

    关键点：`pending` 里的 URB 结构和数据缓冲必须存活到 reap，否则内核
    仍然持有指针，Python GC 一回收就会读到垃圾或段错误。
    """

    def __init__(self, devpath, interface=0):
        self.devpath = devpath
        self.interface = interface
        self.vendor, self.product = _usb_ids(devpath)
        self.info = device_info(devpath, self.vendor, self.product)
        self.fd = os.open(devpath, os.O_RDWR)
        self.pending = {}

    # -- 驱动解绑 --------------------------------------------------------
    def disconnect_driver(self):
        """只解绑当前接口的驱动（USBDEVFS_DISCONNECT）。

        这是首选路径，不会影响同一台机器上的黑武士2等其它 xpad 手柄。
        `takeover()` 在 claim 仍失败时才考虑全局卸载 xpad。
        """
        _ioctl(self.fd, DISCONNECT)
        return 'USBDEVFS_DISCONNECT'

    def other_xpad_devices(self):
        """返回同一 xpad 驱动下、不属于本设备的接口名。"""
        return xpad_other_interfaces(self.info, self.interface)

    def takeover(self, allow_global_unload=None):
        """解绑驱动并 claim 接口，返回实际生效的方法名。

        探针脚本应该用这个，而不是自己拼 disconnect + claim：DISCONNECT
        返回 0 不代表驱动真的放手了，必须用 claim 的结果说话。

        安全策略：只有**没有其它 xpad 手柄**时才允许 `modprobe -r xpad`。
        否则宁可报错，也不把用户同时插着的黑武士2一起解绑。默认
        `allow_global_unload=None` 会自动判断。
        """
        disconnected = False
        method = None
        try:
            method = self.disconnect_driver()
            disconnected = True
        except OSError:
            pass
        try:
            self.claim()
            return method or 'claim（xpad 未占用）'
        except OSError as exc:
            if exc.errno != errno.EBUSY:
                raise

        # 首选 sysfs 精准解绑：只动这个接口，不影响同时插着的黑武士2。
        if unbind_xpad(self.info, self.interface):
            disconnected = True
            try:
                self.claim()
                return 'sysfs unbind（只解绑本接口）'
            except OSError as exc:
                if exc.errno != errno.EBUSY:
                    raise

        others = self.other_xpad_devices()
        if allow_global_unload is None:
            allow_global_unload = not others
        if not allow_global_unload:
            if disconnected:
                self.reconnect_driver()
            raise PadBusyError(
                errno.EBUSY,
                '无法独占冰原狼4：接口仍被 xpad 占用，而且系统里还有其它 '
                'Xbox 手柄（%s）。为了不把其它手柄一起解绑，已拒绝全局卸载 '
                'xpad。' % (', '.join(others) or '无法识别'))

        subprocess.run(['modprobe', '-r', 'xpad'], check=False)
        time.sleep(0.3)
        self.claim()
        return 'modprobe -r xpad（claim 回退）'

    def reconnect_driver(self):
        try:
            _ioctl(self.fd, CONNECT)
            return
        except OSError:
            pass
        # sysfs unbind 之后 USBDEVFS_CONNECT 不一定能重新 probe；优先只
        # 把本接口绑回 xpad，避免影响其它手柄。
        if bind_xpad(self.info, self.interface):
            return
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
        # 只取消，不从 pending 里删。内核可能已经排队了完成事件，
        # 如果这时释放 CUrb/缓冲区，内核稍后写完成数据就会 use-after-free。
        # 统一由 reap() 收到完成后再 pop。
        entry = self.pending.get(uid)
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


def _read_text(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read().strip()
    except OSError:
        return ''


def _norm(text):
    return ''.join(ch for ch in str(text).lower() if ch.isalnum())


def _hint_matches(product, hint):
    """产品名是否像配置里的提示；忽略空格/连字符/大小写。"""
    product_norm = _norm(product)
    hint_norm = _norm(hint)
    if not hint_norm:
        return True
    if hint_norm in product_norm:
        return True
    # 允许产品名省略厂商名（例如只写 "Direwolf 4"）。厂商名和通用词
    # 不参与匹配，但 "Direwolf" 和 "4" 必须同时出现，避免匹配到 3 代。
    tokens = [tok for tok in re.findall(r'[a-z0-9]+', str(hint).lower())
              if tok not in ('flydigi', 'usb', 'receiver', 'controller')]
    return bool(tokens) and all(tok in product_norm for tok in tokens)


def candidate_pads(vid=VID, pid=PID,
                   devices_root='/sys/bus/usb/devices'):
    """列出所有 VID:PID 匹配的 USB 设备（含产品字符串）。

    用 sysfs 而不是挨个打开 `/dev/bus/usb/*`：非 root 也能读产品名，
    而且能区分同样 `045e:028e` 的黑武士2、Vader 等设备。
    """
    out = []
    for entry in sorted(glob.glob(os.path.join(devices_root, '*'))):
        if not os.path.isdir(entry):
            continue
        try:
            evid = int(_read_text(os.path.join(entry, 'idVendor')), 16)
            epid = int(_read_text(os.path.join(entry, 'idProduct')), 16)
        except ValueError:
            continue
        if (evid, epid) != (vid, pid):
            continue
        try:
            bus = int(_read_text(os.path.join(entry, 'busnum')))
            dev = int(_read_text(os.path.join(entry, 'devnum')))
        except ValueError:
            continue
        name = os.path.basename(entry)
        devpath = '/dev/bus/usb/%03d/%03d' % (bus, dev)
        out.append({
            'name': name,
            'sysfs': os.path.realpath(entry),
            'path': devpath if os.path.exists(devpath) else None,
            'bus': bus,
            'dev': dev,
            'vendor': vid,
            'product_id': pid,
            'product': _read_text(os.path.join(entry, 'product')),
            'manufacturer': _read_text(os.path.join(entry, 'manufacturer')),
            'serial': _read_text(os.path.join(entry, 'serial')),
        })
    return out


def choose_pad(candidates, product_hint=PRODUCT_HINT):
    """从候选里挑出冰原狼4，返回 `(info | None, 原因)`。

    产品名匹配不唯一时宁可失败，也不猜。这样插着黑武士2时，即使它也是
    `045e:028e`，也不会被 daemon 接管。
    """
    hint = (product_hint or '').strip()
    if not candidates:
        return None, '未找到 %04x:%04x 设备' % (VID, PID)
    if hint:
        matched = [c for c in candidates if _hint_matches(c['product'], hint)]
        if len(matched) == 1:
            chosen = matched[0]
            if not chosen.get('path'):
                return None, ('找到 %s（%s），但没有 %s；检查 USB 权限。'
                              % (chosen['name'], chosen['product'] or '?',
                                 chosen['path'] or '设备节点'))
            return chosen, ''
        if len(matched) > 1:
            return None, ('找到 %d 个产品名都像 %r 的设备，无法区分：%s'
                          % (len(matched), hint,
                             ', '.join('%s(%s)' % (c['name'], c['product'])
                                       for c in matched)))
        return None, ('找到 %d 个 %04x:%04x，但产品名都不含 %r。'
                      '如果冰原狼4 的产品名不同，请改 [device] product_hint。'
                      % (len(candidates), VID, PID, hint))
    if len(candidates) == 1:
        chosen = candidates[0]
        if not chosen.get('path'):
            return None, '找到 %s，但没有 USB 设备节点。' % chosen['name']
        return chosen, ''
    return None, ('找到 %d 个 %04x:%04x，未配置 product_hint，无法区分：%s'
                  % (len(candidates), VID, PID,
                     ', '.join('%s(%s)' % (c['name'], c['product'])
                               for c in candidates)))


def find_pad_info(vid=VID, pid=PID, product_hint=PRODUCT_HINT):
    chosen, _reason = choose_pad(candidate_pads(vid, pid), product_hint)
    return chosen


def find_pad(vid=VID, pid=PID, product_hint=PRODUCT_HINT):
    """按 VID:PID + 产品名找冰原狼4 的 /dev/bus/usb/BBB/DDD。"""
    chosen = find_pad_info(vid, pid, product_hint)
    return chosen['path'] if chosen else None


def no_pad_message(vid=VID, pid=PID, product_hint=PRODUCT_HINT):
    """给 check/run/calibrate 用的失败说明，列出实际看到的产品名。"""
    candidates = candidate_pads(vid, pid)
    _chosen, reason = choose_pad(candidates, product_hint)
    lines = [reason]
    if candidates:
        lines.append('当前 %04x:%04x 设备：' % (vid, pid))
        for c in candidates:
            lines.append('  %s  %s  %s' % (
                c['name'], c['product'] or '(无产品名)',
                c['path'] or '(无 /dev 节点)'))
    return '\n'.join(lines)


def device_info(devpath, vid=VID, pid=PID):
    """`/dev/bus/usb/001/004` → sysfs 候选信息。"""
    for c in candidate_pads(vid, pid):
        if c.get('path') == devpath:
            return c
    return None


def xpad_other_interfaces(info, interface=0,
                          driver_root='/sys/bus/usb/drivers/xpad'):
    """xpad 驱动下、不属于 info 所代表 USB 设备的接口名。

    全局 `modprobe -r xpad` 会同时解绑这些设备；调用方据此决定是否允许。
    info 为 None 时无法排除目标本身，保守地返回全部。
    """
    try:
        entries = sorted(os.listdir(driver_root))
    except OSError:
        return []
    target = os.path.realpath(info['sysfs']) if info and info.get('sysfs') else None
    out = []
    for entry in entries:
        real = os.path.realpath(os.path.join(driver_root, entry))
        if target and (real == target or real.startswith(target + os.sep)):
            continue
        out.append(entry)
    return out


def find_event_nodes(info, input_root='/sys/class/input'):
    """找属于这个 USB 设备的 /dev/input/eventN。

    用 sysfs 祖先路径匹配，不靠设备名：黑武士2和虚拟 Xbox 手柄都可能叫
    "Microsoft X-Box 360 pad"。daemon 停下后 xpad 会重新建节点，supervise
    用它来检测"手柄被唤醒/有操作"。
    """
    if not info or not info.get('sysfs'):
        return []
    target = os.path.realpath(info['sysfs'])
    out = []
    try:
        entries = sorted(os.listdir(input_root))
    except OSError:
        return out
    for entry in entries:
        if not entry.startswith('event'):
            continue
        real = os.path.realpath(os.path.join(input_root, entry, 'device'))
        if real == target or real.startswith(target + os.sep):
            out.append('/dev/input/' + entry)
    return out


def event_name(path):
    try:
        with open(os.path.join('/sys/class/input', os.path.basename(path),
                               'device', 'name'), encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def unbind_xpad(info, interface=0,
                driver_root='/sys/bus/usb/drivers/xpad'):
    """只把目标接口从 xpad 解绑（写 sysfs unbind）。

    和 `modprobe -r xpad` 不同：这个只影响冰原狼4 的接口，不会碰到同时
    插着的黑武士2。root 才能写；找不到接口说明本来就没绑定。
    """
    if not info or not info.get('name'):
        return False
    name = '%s:1.%d' % (info['name'], interface)
    if not os.path.exists(os.path.join(driver_root, name)):
        return True
    try:
        with open(os.path.join(driver_root, 'unbind'), 'w') as f:
            f.write(name)
    except OSError:
        return False
    return not os.path.exists(os.path.join(driver_root, name))


def bind_xpad(info, interface=0,
              driver_root='/sys/bus/usb/drivers/xpad'):
    """尝试把目标接口重新绑到 xpad（只影响这个设备）。"""
    if not info or not info.get('name'):
        return False
    name = '%s:1.%d' % (info['name'], interface)
    if os.path.exists(os.path.join(driver_root, name)):
        return True
    bind = os.path.join(driver_root, 'bind')
    try:
        with open(bind, 'w') as f:
            f.write(name)
    except OSError:
        return False
    return os.path.exists(os.path.join(driver_root, name))


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

    def send_async(self, data, out_size=None):
        """只提交 OUT URB，不等回包；主循环继续处理输入流。

        电量和状态查询用这个，避免 `identify()` 那种 0.6 秒的 drain 造成
        鼠标/键盘卡顿。
        """
        return self.pad.submit(URB_INT, EP_OUT, data, out_size or len(data))

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


def info_from_reply(d):
    """从一帧注入区 cmd16 应答解析设备信息；不是 cmd16 返回 None。"""
    if len(d) < 32 or d[14] != 0xA5 or d[15] != CMD_IDENTIFY:
        return None
    return {
        'device_type': d[16],
        'uid': d[17:21].hex(),
        'fw': f'{d[21] >> 4}.{d[21] & 15}.{d[22] >> 4}.{d[22] & 15}',
        'battery': d[23],
        'connect': d[25],
        'slot': d[27],
        'raw': bytes(d[14:32]),
    }


def identify(link, settle=0.6):
    """cmd16：DeviceType / uid / 固件 / 电量 / 连接方式 / 当前板载槽位。"""
    link.drain()
    link.send(legacy(CMD_IDENTIFY))
    frames = link.collect(settle)
    got = van(frames, CMD_IDENTIFY)
    if not got:
        return None
    return info_from_reply(got[0])


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
