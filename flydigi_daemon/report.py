#!/usr/bin/env python3
"""冰原狼4 32 字节输入报文解码（可复用模块）

布局出处：[docs/input-report.md](../docs/input-report.md)，全部为实测。
这里只做纯函数解码，不碰 USB，方便回放和单元测试。

    偏移      内容
    [0]       0x00
    [1]       0x14
    [2..3]    标准 Xbox 360 按钮字（小端）
    [4] [5]   LT / RT 模拟量 0..255
    [6..13]   左X 左Y 右X 右Y，int16 小端，满量程 ±32768
    [14..16]  陀螺仪三轴角速度，int8（只在 XInput 报文里）
    [17..20]  飞智键位位图，32 位小端（含 M1/M2/FN/Turbo/Home）
    [21..24]  状态字节
    [25..31]  保留

如果 `[14] == 0xA5`，这一帧不是手柄输入，而是老方言命令的应答
（注入在偏移 14，`[15]` 是命令号）。
"""

import struct

REPLY_MARK = 0xA5

# 标准按钮字
STD_LOW = {
    0x01: 'up', 0x02: 'down', 0x04: 'left', 0x08: 'right',
    0x10: 'start', 0x20: 'back', 0x40: 'l3', 0x80: 'r3',
}
STD_HIGH = {
    0x01: 'lb', 0x02: 'rb', 0x04: 'home', 0x08: 'reserved8',
    0x10: 'a', 0x20: 'b', 0x40: 'x', 0x80: 'y',
}

# 飞智位图：键序号 = 上游 openflydigi 的 KEY_NAMES
KEY_NAMES = {
    0: 'up', 1: 'right', 2: 'down', 3: 'left', 4: 'a', 5: 'b', 6: 'back',
    7: 'x', 8: 'y', 9: 'start', 10: 'lb', 11: 'rb', 12: 'lt', 13: 'rt',
    14: 'l3', 15: 'r3', 16: 'c', 17: 'z', 18: 'm1', 19: 'm2', 20: 'm3',
    21: 'm4', 22: 'm5', 23: 'm6', 24: 'menu', 25: 'turbo', 27: 'home',
    28: 'back2',
}
FLYDIGI_EXTRA = ('m1', 'm2', 'fn', 'turbo')   # 标准字里没有的键

AXES = ('lx', 'ly', 'rx', 'ry')
GYRO = ('gyro_x', 'gyro_y', 'gyro_z')


def is_reply(data):
    return len(data) >= 15 and data[14] == REPLY_MARK


def decode(data):
    """把一帧 32 字节输入报文解成普通 dict。

    返回::

        {
          'std': frozenset 标准按钮名,
          'bits': frozenset 飞智位图键名（含标准键的副本）,
          'extra': frozenset 只有位图里才有的键（m1/m2/fn/turbo）,
          'lt': 0..255, 'rt': 0..255,
          'lx': int16, 'ly': int16, 'rx': int16, 'ry': int16,
          'gyro': (x, y, z) int8,
          'status': bytes([21..24]),
          'raw': bytes,
        }
    """
    if len(data) < 32:
        raise ValueError('report shorter than 32 bytes: %d' % len(data))
    word = struct.unpack_from('<H', data, 2)[0]
    std = set()
    for bit, name in STD_LOW.items():
        if word & 0xFF & bit:
            std.add(name)
    for bit, name in STD_HIGH.items():
        if word >> 8 & bit:
            std.add(name)

    bits32 = struct.unpack_from('<I', data, 17)[0]
    bits = {KEY_NAMES[k] for k in range(32) if bits32 >> k & 1 and k in KEY_NAMES}

    lx, ly, rx, ry = struct.unpack_from('<4h', data, 6)
    gx, gy, gz = struct.unpack_from('<3b', data, 14)
    return {
        'std': frozenset(std),
        'bits': frozenset(bits),
        'extra': frozenset(bits & set(FLYDIGI_EXTRA)),
        'lt': data[4],
        'rt': data[5],
        'lx': lx, 'ly': ly, 'rx': rx, 'ry': ry,
        'gyro': (gx, gy, gz),
        'status': bytes(data[21:25]),
        'raw': bytes(data),
    }


def std_buttons(data):
    """只要标准按钮集合（热路径用，跳过位图和轴）。"""
    word = struct.unpack_from('<H', data, 2)[0]
    out = set()
    for bit, name in STD_LOW.items():
        if word & 0xFF & bit:
            out.add(name)
    for bit, name in STD_HIGH.items():
        if word >> 8 & bit:
            out.add(name)
    return frozenset(out)


def reply(data):
    """解析注入区应答，返回 (命令号, 载荷字节) 或 None。"""
    if not is_reply(data):
        return None
    return data[15], bytes(data[16:32])


def norm_axis(value, lo=-32768, hi=32767):
    """int16 → -1.0..1.0；中点 0 映射到 0。"""
    if value >= 0:
        return value / float(hi)
    return value / float(-lo)
