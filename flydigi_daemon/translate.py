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

# 十字键：报文里既在标准按钮字里，又合并成 hat。映射层把它当四个普通
# 物理键（`dpad_up`/`up` → `key:up` 之类），所以 `buttons` 里要带上；
# 虚拟手柄仍然只把 hat 写进 ABS_HAT0X/Y（BUTTON_MAP 里没有这四个名字）。
DPAD_BUTTONS = ('up', 'down', 'left', 'right')


@dataclass(frozen=True)
class PadState:
    """一份可以直接写进虚拟手柄的状态。

    `buttons` 里可能带十字键的 `up`/`down`/`left`/`right`（映射层要用），
    `VirtualGamepad` 只按 `BUTTON_MAP` 写，十字键仍走 `hat`，不会重复。
    """
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
    buttons = frozenset(d['std'] & (set(PAD_BUTTONS) | set(DPAD_BUTTONS)))
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


def significant_change(prev, cur):
    """这一帧是否算“用户操作”，用于 1 小时空闲自动退出。

    不能直接用 `prev != cur`：摇杆/扳机/体感都有微小噪声，静止时也会
    偶尔差 1。按钮/十字键精确比较；摇杆和扳机要超过阈值；体感看绝对值
    （角速度 > 约 18°/s），静止漂移不会把空闲计时一直续上。
    """
    if prev is None or cur is None:
        return cur is not None
    if (cur.buttons != prev.buttons or cur.extras != prev.extras
            or cur.hat != prev.hat):
        return True
    for old, new in zip(prev.axes, cur.axes):
        if abs(old - new) >= 96:
            return True
    for old, new in zip(prev.triggers, cur.triggers):
        if abs(old - new) >= 8:
            return True
    if max(abs(v) for v in cur.gyro) >= 12:
        return True
    return False


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
