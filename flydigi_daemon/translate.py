"""32 字节手柄报文 → 虚拟 Xbox 手柄状态（纯函数）

这里不碰 USB，也不碰 uinput，方便：

* `--replay` 在没有手柄的机器上回放；
* 单元测试直接断言某个报文会变成哪些按键/轴；
* 以后加映射 DSL 时，这层保持"物理输入"的语义，映射层再往上叠。

M1 只做**透明转发**：标准按钮字、扳机、双摇杆、十字键、Home 全部 1:1。
M1/M2/FN/Turbo 只在 `extras` 里带出来给日志用，不输出到手柄。
"""

from dataclasses import dataclass

from . import report as rep

# 进虚拟手柄的标准键。`reserved8` 之类的忽略。
PAD_BUTTONS = ('a', 'b', 'x', 'y', 'lb', 'rb', 'back', 'start',
               'home', 'l3', 'r3')

# 只在飞智位图里出现的键（M1 只记录，不转发）。
EXTRA_KEYS = ('m1', 'm2', 'fn', 'turbo')


@dataclass(frozen=True)
class PadState:
    """一份可以直接写进虚拟手柄的状态。"""
    buttons: frozenset = frozenset()
    hat: tuple = (0, 0)                 # (x, y)，-1/0/1
    axes: tuple = (0, 0, 0, 0)          # (lx, ly, rx, ry)，evdev 语义
    triggers: tuple = (0, 0)            # (lt, rt)，0..255
    extras: frozenset = frozenset()     # m1/m2/fn/turbo
    gyro: tuple = (0, 0, 0)             # int8 角速度，yaw/pitch/roll

    def is_empty(self):
        return (not self.buttons and self.hat == (0, 0)
                and self.axes == (0, 0, 0, 0)
                and self.triggers == (0, 0))


EMPTY = PadState()


def invert16(value):
    """XInput 的 Y 轴向上为正，evdev 的 ABS_Y 向下为正。

    xpad 驱动用的是 `~value`（按位取反），这里照抄，保证和原厂驱动一致：
    中点是 -1 而不是 0，游戏和 SDL 都已经习惯。
    """
    unsigned = (value & 0xFFFF) ^ 0xFFFF
    return unsigned - 0x10000 if unsigned >= 0x8000 else unsigned


def translate(data):
    """把一帧 32 字节输入报文转成 `PadState`。

    `data[14] == 0xA5` 的回包返回 `None`（调用方跳过）。
    """
    if len(data) < 32 or rep.is_reply(data):
        return None
    d = rep.decode(data)
    buttons = frozenset(d['std'] & set(PAD_BUTTONS))
    hat = (1 if 'right' in d['std'] else -1 if 'left' in d['std'] else 0,
           1 if 'down' in d['std'] else -1 if 'up' in d['std'] else 0)
    axes = (d['lx'], invert16(d['ly']), d['rx'], invert16(d['ry']))
    # 位图 bit 24 在上游叫 `menu`，物理上就是 FN 那颗键。
    extras = set(d['extra'])
    if 'menu' in d['bits']:
        extras.add('fn')
    return PadState(
        buttons=buttons,
        hat=hat,
        axes=axes,
        triggers=(d['lt'], d['rt']),
        extras=frozenset(extras),
        gyro=tuple(d['gyro']),
    )


def describe(prev, cur):
    """人类可读的差异；完全相同返回空字符串。

    只用于 `--dry-run` 和日志，不影响热路径。
    """
    if cur is None:
        return ''
    parts = []
    for name in sorted(cur.buttons - prev.buttons):
        parts.append('+' + name)
    for name in sorted(prev.buttons - cur.buttons):
        parts.append('-' + name)
    for name in sorted(cur.extras - prev.extras):
        parts.append('+%s(extra)' % name)
    for name in sorted(prev.extras - cur.extras):
        parts.append('-%s(extra)' % name)
    if cur.hat != prev.hat:
        parts.append('hat=%s' % (cur.hat,))
    for name, old, new in zip(('lx', 'ly', 'rx', 'ry'), prev.axes, cur.axes):
        if old != new:
            parts.append('%s=%d' % (name, new))
    for name, old, new in zip(('lt', 'rt'), prev.triggers, cur.triggers):
        if old != new:
            parts.append('%s=%d' % (name, new))
    return ' '.join(parts)
