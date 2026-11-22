"""映射 DSL：物理输入 → 手柄 / 键盘 / 鼠标 / 滚轮 / 组开关（M3）

配置格式见 `config/default.toml` 和 `docs/daemon-plan.md` §4。这一层只做
"当前 profile + 原始 PadState → 本帧该输出什么"，不碰 USB；uinput 写入由
`devices.py` 完成，方便回放和单元测试。

设计要点：

* 每帧从零计算**期望状态**（手柄键集合、键盘键集合、鼠标键集合），再和
  上一帧做差量。多个物理键映射到同一目标时天然去重，不需要引用计数。
* 键盘组合键拆成键集合；按下时先修饰键、后普通键，松开时反序。
* 轴映射（`mouse:x` / `keypair` / `wheel` 自动方向 / 固定滚轮 / 阈值按键）
  在 `axis.NAME` 下。
* `passthrough = true` 时标准手柄输入原样走；显式条目覆盖对应输入。
* 组（`group = "gyro"`）关闭时，该条目按"松开/归零"处理。
* 摇杆 Y 轴在引擎里统一成 **XInput 语义：向上为正**，与配置里的 `invert`
  注释一致；写回手柄时再转成 evdev 的 `~value`。
"""

import json
import os
import time
from dataclasses import dataclass, field, replace

from evdev import ecodes

from .report import deskew_gyro
from .translate import EMPTY, PadState, invert16

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 名字表
# ---------------------------------------------------------------------------
SOURCE_ALIASES = {
    'dpad_up': 'up', 'dpad_down': 'down', 'dpad_left': 'left',
    'dpad_right': 'right', 'guide': 'home', 'select': 'back',
}
SOURCES = {
    'a', 'b', 'x', 'y', 'lb', 'rb', 'back', 'start', 'home',
    'l3', 'r3', 'up', 'down', 'left', 'right',
    'm1', 'm2', 'fn', 'turbo',
}
PAD_BUTTONS = {'a', 'b', 'x', 'y', 'lb', 'rb', 'back', 'start',
               'home', 'l3', 'r3'}
PAD_ALIASES = {'guide': 'home', 'select': 'back'}
PAD_AXIS_INDEX = {
    'abs_x': ('axes', 0), 'abs_y': ('axes', 1),
    'abs_rx': ('axes', 2), 'abs_ry': ('axes', 3),
    'abs_z': ('triggers', 0), 'abs_rz': ('triggers', 1),
}
AXIS_NAMES = {'lx', 'ly', 'rx', 'ry', 'lt', 'rt', 'gyro_x', 'gyro_y', 'gyro_z'}
MOUSE_BUTTONS = {'left': ecodes.BTN_LEFT, 'right': ecodes.BTN_RIGHT,
                 'middle': ecodes.BTN_MIDDLE, 'side': ecodes.BTN_SIDE,
                 'extra': ecodes.BTN_EXTRA}

KEY_ALIASES = {
    'escape': 'esc', 'return': 'enter', 'del': 'delete',
    'ctrl': 'leftctrl', 'shift': 'leftshift', 'alt': 'leftalt',
    'win': 'leftmeta', 'super': 'leftmeta', 'meta': 'leftmeta',
    'pgup': 'pageup', 'pgdn': 'pagedown', 'kp_plus': 'kpplus',
    'kp_minus': 'kpminus', 'kp_dot': 'kpdot', 'kp_enter': 'kpenter',
    'kp_multiply': 'kpmultiply', 'kp_divide': 'kpdivide', 'plus': 'equal',
}
MODIFIER_CODES = {
    ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
    ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT,
    ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
    ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA,
}

_KEY_CODES = {}
for _name, _code in ecodes.ecodes.items():
    if _name.startswith('KEY_'):
        _KEY_CODES[_name[4:].lower()] = _code


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
# 目标
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    kind: str                 # none/pad/key/mouse_button/mouse_move/wheel/toggle
    value: object = None


NONE = Target('none')


def _split_combo(text):
    parts = [p.strip().lower() for p in str(text).split('+') if p.strip()]
    if not parts:
        raise ConfigError('空的按键组合：%r' % (text,))
    codes = []
    seen = set()
    for part in parts:
        name = KEY_ALIASES.get(part, part)
        code = _KEY_CODES.get(name)
        if code is None:
            raise ConfigError('未知按键名 %r（key:%s）' % (part, text))
        if code in seen:
            raise ConfigError('组合键里重复的修饰键：%r' % (text,))
        seen.add(code)
        codes.append(code)
    return tuple(codes)


def parse_button_target(text):
    text = str(text).strip()
    low = text.lower()
    if low == 'none':
        return NONE
    if low.startswith('xbox:'):
        name = low[5:]
        name = PAD_ALIASES.get(name, name)
        if name in PAD_BUTTONS:
            return Target('pad', name)
        raise ConfigError('未知手柄按钮 %r（可用：%s）'
                          % (name, ', '.join(sorted(PAD_BUTTONS))))
    if low.startswith('key:'):
        return Target('key', _split_combo(text[4:]))
    if low.startswith('mouse:'):
        name = low[6:]
        if name in MOUSE_BUTTONS:
            return Target('mouse_button', MOUSE_BUTTONS[name])
        raise ConfigError('未知鼠标键 %r（可用：%s）'
                          % (name, ', '.join(sorted(MOUSE_BUTTONS))))
    if low.startswith('wheel:'):
        name = low[6:]
        if name in ('up', 'down', 'left', 'right'):
            return Target('wheel', name)
        raise ConfigError('未知滚轮方向 %r（按钮只能用 up/down/left/right）'
                          % (name,))
    if low.startswith('toggle:'):
        name = low[7:]
        if not name:
            raise ConfigError('toggle 缺少组名')
        return Target('toggle', name)
    raise ConfigError('未知目标 %r' % (text,))


def parse_axis_target(text):
    text = str(text).strip()
    low = text.lower()
    if low == 'wheel':
        return Target('wheel', 'auto')
    if low.startswith('wheel:'):
        name = low[6:]
        if name in ('up', 'down', 'left', 'right', 'x', 'y', 'auto'):
            return Target('wheel', name)
        raise ConfigError('未知滚轮方向 %r（可用 up/down/left/right/x/y/auto）'
                          % (name,))
    if low.startswith('mouse:'):
        name = low[6:]
        if name in ('x', 'y'):
            return Target('mouse_move', name)
        # mouse:left/right/... 作为阈值按钮目标，继续走 parse_button_target
    if low == 'keypair':
        return Target('keypair')
    if low.startswith('xbox:'):
        name = low[5:]
        if name in PAD_AXIS_INDEX:
            return Target('pad_axis', name)
        name = PAD_ALIASES.get(name, name)
        if name in PAD_BUTTONS:
            return Target('pad', name)
        raise ConfigError('轴目标 %r 无效；手柄轴请用 abs_x/abs_y/abs_rx/'
                          'abs_ry/abs_z/abs_rz，按钮用 xbox:a/b/...' % (text,))
    try:
        target = parse_button_target(text)
    except ConfigError:
        raise
    if target.kind in ('pad', 'key', 'mouse_button', 'wheel', 'none'):
        return target
    raise ConfigError('轴目标 %r 不能用在 axis 下' % (text,))


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------
@dataclass
class AxisRule:
    axis: str
    target: Target
    group: str = None
    deadzone: float = 0.12
    curve: float = 1.0
    invert: bool = False
    max_speed: float = 800.0
    threshold: float = 0.35
    release_threshold: float = 0.25
    repeat_hz: float = 0.0
    neg: Target = None
    pos: Target = None
    sensitivity: float = 1.0
    smoothing: float = 0.0
    deadband: float = 0.0
    max_hz: float = 0.0


@dataclass
class ButtonRule:
    source: str
    target: Target
    group: str = None


@dataclass
class Profile:
    name: str
    passthrough: bool = False
    buttons: dict = field(default_factory=dict)
    axes: dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)


def _norm_source(name):
    name = str(name).strip().lower()
    name = SOURCE_ALIASES.get(name, name)
    if name not in SOURCES:
        raise ConfigError('未知物理键 %r（可用：%s）'
                          % (name, ', '.join(sorted(SOURCES))))
    return name


def _check_axis_name(name):
    name = str(name).strip().lower()
    if name not in AXIS_NAMES:
        raise ConfigError('未知轴 %r（可用：%s）'
                          % (name, ', '.join(sorted(AXIS_NAMES))))
    return name


AXIS_LIMITS = {
    'deadzone': (0.0, 0.95, '0–0.95'),
    'curve': (0.1, 8.0, '0.1–8'),
    'max_speed': (50.0, 5000.0, '50–5000'),
    'sensitivity': (0.1, 30.0, '0.1–30'),
    'smoothing': (0.0, 0.95, '0–0.95'),
    'deadband': (0.0, 20.0, '0–20'),
    'max_hz': (0.0, 60.0, '0–60'),
    'threshold': (0.0, 1.0, '0–1'),
    'release_threshold': (0.0, 1.0, '0–1'),
    'repeat_hz': (0.0, 60.0, '0–60'),
}


def _check_axis_ranges(name, rule):
    for field, (low, high, text) in AXIS_LIMITS.items():
        value = getattr(rule, field)
        if value is not None and not (low <= value <= high):
            raise ConfigError('[axis.%s] %s=%g 超出范围（%s）'
                              % (name, field, value, text))


def parse_axis_rule(name, data):
    if not isinstance(data, dict):
        raise ConfigError('[axis.%s] 必须是表' % (name,))
    if 'target' not in data:
        raise ConfigError('[axis.%s] 缺少 target' % (name,))
    target = parse_axis_target(data['target'])
    if name.startswith('gyro') and target.kind != 'mouse_move':
        raise ConfigError('[axis.%s] 体感轴目前只能映射 mouse:x 或 mouse:y'
                          % (name,))
    rule = AxisRule(axis=name, target=target,
                    group=data.get('group'),
                    invert=bool(data.get('invert', False)))
    if 'deadzone' in data:
        rule.deadzone = float(data['deadzone'])
    if 'curve' in data:
        rule.curve = float(data['curve'])
    if 'max_speed' in data:
        rule.max_speed = float(data['max_speed'])
    if 'threshold' in data:
        rule.threshold = float(data['threshold'])
    if 'release_threshold' in data:
        rule.release_threshold = float(data['release_threshold'])
    if 'repeat_hz' in data:
        rule.repeat_hz = float(data['repeat_hz'])
    if 'sensitivity' in data:
        rule.sensitivity = float(data['sensitivity'])
    if 'smoothing' in data:
        rule.smoothing = float(data['smoothing'])
    if 'deadband' in data:
        rule.deadband = float(data['deadband'])
    if 'max_hz' in data:
        rule.max_hz = float(data['max_hz'])

    if target.kind == 'keypair':
        for side in ('neg', 'pos'):
            if side not in data:
                raise ConfigError('[axis.%s] keypair 缺少 %s' % (name, side))
            sub = parse_button_target(data[side])
            if sub.kind not in ('pad', 'key', 'mouse_button', 'wheel'):
                raise ConfigError('[axis.%s] %s 的目标 %r 不能用在 keypair'
                                  % (name, side, data[side]))
            setattr(rule, side, sub)
        if 'deadzone' not in data:
            rule.deadzone = 0.35
        rule.release_threshold = rule.deadzone
    elif target.kind == 'mouse_move':
        if name not in ('lx', 'ly', 'rx', 'ry', 'gyro_x', 'gyro_y'):
            raise ConfigError('[axis.%s] 不能映射到 mouse:%s'
                              % (name, target.value))
    elif target.kind == 'wheel':
        # 轴目标选“滚轮（自动方向）”时默认按比例滚动；
        # 显式写了 threshold/repeat_hz 的仍按固定滚轮处理。
        if (target.value in ('auto', 'x', 'y') and 'max_hz' not in data
                and 'threshold' not in data and 'repeat_hz' not in data):
            rule.max_hz = 20.0
        if rule.max_hz > 0:
            # 比例滚轮：按键越深滚得越快，deadzone 以下停止。
            rule.deadzone = float(data.get('deadzone', 0.12))
            if not (0.0 <= rule.deadzone < 1.0):
                raise ConfigError('[axis.%s] deadzone 必须在 0..1' % (name,))
        else:
            if 'threshold' not in data:
                rule.threshold = 0.35
            if rule.release_threshold >= rule.threshold:
                rule.release_threshold = max(0.0, rule.threshold - 0.1)
            if rule.repeat_hz <= 0 and name in ('lt', 'rt'):
                rule.repeat_hz = 18.0
    _check_axis_ranges(name, rule)
    return rule


def parse_profile(name, data):
    if not isinstance(data, dict):
        raise ConfigError('[profile.%s] 必须是表' % (name,))
    profile = Profile(name=name,
                      passthrough=bool(data.get('passthrough', False)))
    if 'groups' in data:
        groups = data['groups']
        if not isinstance(groups, dict):
            raise ConfigError('[profile.%s.groups] 必须是表' % (name,))
        profile.groups = {str(k): bool(v) for k, v in groups.items()}

    for key, value in data.items():
        if key in ('passthrough', 'groups'):
            continue
        if key == 'axis':
            if not isinstance(value, dict):
                raise ConfigError('[profile.%s.axis] 必须是表' % (name,))
            for axis_name, axis_data in value.items():
                axis = _check_axis_name(axis_name)
                profile.axes[axis] = parse_axis_rule(axis, axis_data)
            continue
        source = _norm_source(key)
        target = parse_button_target(value)
        group = None
        if isinstance(value, dict):
            # 预留：以后允许按钮写成 {target=..., group=...}
            target = parse_button_target(value.get('target', 'none'))
            group = value.get('group')
        if source in profile.buttons:
            raise ConfigError('[profile.%s] 物理键 %r 重复' % (name, source))
        profile.buttons[source] = ButtonRule(source, target, group)
    return profile


def parse_config(cfg):
    """解析所有 `[profile.*]`，返回 {名字: Profile}。"""
    raw = cfg.get('profile', {})
    if not isinstance(raw, dict) or not raw:
        raise ConfigError('配置里没有任何 [profile.*]')
    profiles = {}
    for name, data in raw.items():
        profiles[name] = parse_profile(name, data)
    return profiles


def lint_config(cfg):
    """返回 (errors, warnings)，给 `check` 和启动前使用。"""
    errors, warnings = [], []
    cal_path = (cfg.get('calibration') or {}).get('file') or \
        'config/calibration.json'
    cal_path = os.path.expanduser(str(cal_path))
    if not os.path.isabs(cal_path):
        cal_path = os.path.join(ROOT, cal_path)
    cal_ok = os.path.exists(cal_path)
    names = cfg.get('daemon', {}).get('slot_profiles', [])
    if len(names) != 4:
        errors.append('daemon.slot_profiles 必须正好 4 项，当前 %d 项'
                      % len(names))
    profiles = cfg.get('profile', {})
    for name in names:
        if name not in profiles:
            errors.append('daemon.slot_profiles 引用了不存在的 profile %r'
                          % (name,))
    for name, data in profiles.items():
        try:
            profile = parse_profile(name, data)
        except ConfigError as exc:
            errors.append(str(exc))
            continue
        for rule in profile.axes.values():
            if rule.axis.startswith('gyro') and not cal_ok:
                warnings.append('[profile.%s.axis.%s] 体感轴需要先运行 '
                                'calibrate 生成校准文件'
                                % (name, rule.axis))
            if rule.target.kind in ('key', 'mouse_button', 'wheel', 'pad') \
                    and not (0 < rule.threshold < 1):
                errors.append('[profile.%s.axis.%s] threshold 必须在 0..1'
                              % (name, rule.axis))
            if (rule.target.kind == 'wheel' and rule.max_hz <= 0
                    and rule.release_threshold > rule.threshold):
                warnings.append('[profile.%s.axis.%s] release_threshold > '
                                'threshold，迟滞方向反了'
                                % (name, rule.axis))
    return errors, warnings


def gate_errors(cfg):
    """启动前必须为空的错误列表（warnings 只提示，不阻塞）。"""
    return lint_config(cfg)[0]


# ---------------------------------------------------------------------------
# 运行时
# ---------------------------------------------------------------------------
GYRO_CAL_KEYS = {'gyro_x': 'yaw', 'gyro_y': 'pitch', 'gyro_z': 'roll'}


def load_calibration(path):
    """读 calibrate 生成的 JSON；不存在/损坏返回 None。"""
    if not path:
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def calibration_axis(calibration, axis):
    """取出某个体感轴的 counts_per_degree / sign；未标定返回 None。"""
    if not calibration:
        return None
    section = calibration.get(GYRO_CAL_KEYS.get(axis, ''))
    if not isinstance(section, dict):
        return None
    try:
        cpd = float(section.get('counts_per_degree') or 0)
        sign = float(section.get('sign', 1))
    except (TypeError, ValueError):
        return None
    if cpd <= 0:
        return None
    return {'counts_per_degree': cpd, 'sign': sign}


@dataclass
class FrameState:
    pad: PadState = EMPTY
    keys: frozenset = frozenset()
    mouse: frozenset = frozenset()
    dx: int = 0
    dy: int = 0
    wheel: int = 0
    hwheel: int = 0
    wheel_hi: int = 0
    hwheel_hi: int = 0
    groups: dict = field(default_factory=dict)
    toggled: tuple = ()


NEUTRAL_PAD = PadState(axes=(0, -1, 0, -1), triggers=(0, 0))


def _clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


def _shape(value, rule):
    if rule.invert:
        value = -value
    mag = abs(value)
    dz = _clamp(rule.deadzone, 0.0, 0.95)
    if mag <= dz:
        return 0.0
    mag = (mag - dz) / (1.0 - dz)
    if rule.curve and rule.curve != 1.0:
        mag = mag ** rule.curve
    return mag if value >= 0 else -mag


def axis_norm(axis, raw):
    """原始状态 → -1..1（摇杆向上为正）或 0..1（扳机）。"""
    if axis == 'lx':
        return _clamp(raw.axes[0] / 32768.0, -1.0, 1.0)
    if axis == 'ly':
        return _clamp(-raw.axes[1] / 32768.0, -1.0, 1.0)
    if axis == 'rx':
        return _clamp(raw.axes[2] / 32768.0, -1.0, 1.0)
    if axis == 'ry':
        return _clamp(-raw.axes[3] / 32768.0, -1.0, 1.0)
    if axis == 'lt':
        return raw.triggers[0] / 255.0
    if axis == 'rt':
        return raw.triggers[1] / 255.0
    if axis == 'gyro_x':
        return float(raw.gyro[0])
    if axis == 'gyro_y':
        return float(raw.gyro[1])
    if axis == 'gyro_z':
        return float(raw.gyro[2])
    return 0.0


def key_order(code):
    return (0 if code in MODIFIER_CODES else 1, code)


class Mapper(object):
    def __init__(self, profile, devices=None, clock=None, calibration=None):
        self.devices = devices
        self.clock = clock or time.monotonic
        self.calibration = calibration or {}
        self.profile = profile
        self.groups = {}
        self.acc = {}
        self.axis_state = {}
        self.prev_held = frozenset()
        self.last_now = None
        self.out = FrameState()
        self.set_profile(profile)

    def set_profile(self, profile, groups=None):
        self.profile = profile
        self.groups = dict(profile.groups)
        if groups:
            self.groups.update(groups)
        self.acc = {'mouse:x': 0.0, 'mouse:y': 0.0}
        self.axis_state = {}
        self.gyro_prev = {}
        self.prev_held = frozenset()
        self.last_now = None
        self.out = FrameState(groups=dict(self.groups))

    def reset(self):
        self.acc = {'mouse:x': 0.0, 'mouse:y': 0.0}
        self.axis_state = {}
        self.gyro_prev = {}
        self.prev_held = frozenset()
        self.last_now = None
        self.out = FrameState(groups=dict(self.groups))

    # -- 主入口 ----------------------------------------------------------
    def apply(self, raw, now=None):
        if raw is None:
            return self.out
        now = self.clock() if now is None else now
        dt = 0.0 if self.last_now is None else _clamp(now - self.last_now, 0, 0.1)
        self.last_now = now

        gyro = self._deskew_gyro(raw.gyro)
        if gyro != raw.gyro:
            raw = replace(raw, gyro=gyro)

        held = raw.buttons | raw.extras
        pressed = held - self.prev_held
        toggled = []

        # 1) 组开关只认按下沿，先更新，后续条目按新状态计算。
        for source, rule in self.profile.buttons.items():
            if rule.target.kind != 'toggle' or source not in pressed:
                continue
            if not self._active(rule.group):
                continue
            name = rule.target.value
            self.groups[name] = not self.groups.get(name, True)
            toggled.append((name, self.groups[name]))
            # 组状态变了，体感平滑器清零，避免切换瞬间跳一下。
            for other in self.profile.axes.values():
                if other.axis.startswith('gyro'):
                    self.axis_state.pop(id(other), None)

        pad = self._build_pad(raw)
        pad_buttons = set(pad.buttons)
        pad_axes = list(pad.axes)
        pad_triggers = list(pad.triggers)
        keys = set()
        mouse = set()
        wheel = 0
        hwheel = 0
        wheel_hi = 0
        hwheel_hi = 0

        for source, rule in self.profile.buttons.items():
            if source not in held or not self._active(rule.group):
                continue
            step, hstep = self._add_target(rule.target, pad_buttons,
                                           keys, mouse)
            # 按钮类滚轮只在按下沿滚一格，不能按住 1000 Hz 连发。
            if rule.target.kind == 'wheel' and source not in pressed:
                step = hstep = 0
            wheel += step
            hwheel += hstep
            wheel_hi += step * 120
            hwheel_hi += hstep * 120

        for axis, rule in self.profile.axes.items():
            value = axis_norm(axis, raw)
            if not self._active(rule.group):
                value = 0.0
            if axis.startswith('gyro'):
                self._apply_gyro(rule, value, dt)
            else:
                self._apply_axis(rule, value, dt, pad_axes, pad_triggers,
                                 pad_buttons, keys, mouse)
                step, hstep, shi, hshi = self._axis_wheel(rule, value, now,
                                                          dt)
                wheel += step
                hwheel += hstep
                wheel_hi += shi
                hwheel_hi += hshi
                step, hstep = self._apply_keypair(rule, value, pad_buttons,
                                                  keys, mouse)
                wheel += step
                hwheel += hstep
                wheel_hi += step * 120
                hwheel_hi += hstep * 120

        dx, dy = self._flush_mouse()
        pad = PadState(buttons=frozenset(pad_buttons), hat=pad.hat,
                       axes=tuple(pad_axes), triggers=tuple(pad_triggers))

        out = FrameState(pad=pad, keys=frozenset(keys),
                         mouse=frozenset(mouse), dx=dx, dy=dy,
                         wheel=int(wheel), hwheel=int(hwheel),
                         wheel_hi=int(wheel_hi), hwheel_hi=int(hwheel_hi),
                         groups=dict(self.groups),
                         toggled=tuple(toggled))
        self._commit(out)
        self.prev_held = held
        self.out = out
        return out

    # -- 内部 ------------------------------------------------------------
    def _deskew_gyro(self, gyro):
        out = []
        for axis, value in enumerate(gyro):
            value = deskew_gyro(self.gyro_prev.get(axis), value)
            self.gyro_prev[axis] = value
            out.append(value)
        return tuple(out)

    def _active(self, group):
        if not group:
            return True
        return bool(self.groups.get(group, True))

    def _build_pad(self, raw):
        buttons = set()
        axes = list(NEUTRAL_PAD.axes)
        triggers = list(NEUTRAL_PAD.triggers)
        hat_x = hat_y = 0

        if self.profile.passthrough:
            mapped = set(self.profile.buttons)
            for name in raw.buttons:
                if name in PAD_BUTTONS and name not in mapped:
                    buttons.add(name)
            if 'left' not in mapped and 'left' in raw.buttons:
                hat_x = -1
            elif 'right' not in mapped and 'right' in raw.buttons:
                hat_x = 1
            if 'up' not in mapped and 'up' in raw.buttons:
                hat_y = -1
            elif 'down' not in mapped and 'down' in raw.buttons:
                hat_y = 1
            if 'lx' not in self.profile.axes:
                axes[0] = raw.axes[0]
            if 'ly' not in self.profile.axes:
                axes[1] = raw.axes[1]
            if 'rx' not in self.profile.axes:
                axes[2] = raw.axes[2]
            if 'ry' not in self.profile.axes:
                axes[3] = raw.axes[3]
            if 'lt' not in self.profile.axes:
                triggers[0] = raw.triggers[0]
            if 'rt' not in self.profile.axes:
                triggers[1] = raw.triggers[1]

        return PadState(buttons=frozenset(buttons), hat=(hat_x, hat_y),
                        axes=tuple(axes), triggers=tuple(triggers),
                        extras=frozenset())

    def _add_target(self, target, pad_buttons, keys, mouse):
        """应用一个按钮目标，返回 (垂直滚轮, 水平滚轮) 格数。"""
        if target.kind == 'pad':
            pad_buttons.add(target.value)
        elif target.kind == 'key':
            keys.update(target.value)
        elif target.kind == 'mouse_button':
            mouse.add(target.value)
        elif target.kind == 'wheel':
            if target.value == 'up':
                return 1, 0
            if target.value == 'down':
                return -1, 0
            if target.value == 'left':
                return 0, -1
            if target.value == 'right':
                return 0, 1
        return 0, 0

    def _apply_axis(self, rule, value, dt, pad_axes, pad_triggers,
                    pad_buttons, keys, mouse):
        target = rule.target
        if target.kind == 'mouse_move':
            if not dt or value == 0.0:
                return
            shaped = _shape(value, rule)
            self.acc['mouse:' + target.value] += shaped * rule.max_speed * dt
        elif target.kind == 'pad_axis':
            bucket, idx = PAD_AXIS_INDEX[target.value]
            if rule.invert:
                value = -value
            if bucket == 'axes':
                raw = int(round(_clamp(value, -1.0, 1.0) * 32767))
                if idx in (1, 3):
                    raw = invert16(raw)
                pad_axes[idx] = raw
            else:
                pad_triggers[idx] = int(round(_clamp(value, 0.0, 1.0) * 255))
        elif target.kind in ('key', 'mouse_button', 'pad'):
            if rule.invert:
                value = -value
            if abs(value) >= rule.threshold:
                if target.kind == 'key':
                    keys.update(target.value)
                elif target.kind == 'mouse_button':
                    mouse.add(target.value)
                else:
                    pad_buttons.add(target.value)

    def _apply_gyro(self, rule, value, dt):
        """角速度积分：counts/秒 × dt ÷ counts_per_degree × 灵敏度。"""
        if rule.target.kind != 'mouse_move' or dt <= 0:
            return
        params = calibration_axis(self.calibration, rule.axis)
        if params is None:
            return
        value *= params['sign']
        if abs(value) < rule.deadband:
            value = 0.0
        state = self.axis_state.setdefault(id(rule), {'ema': 0.0})
        alpha = _clamp(rule.smoothing, 0.0, 0.99)
        ema = alpha * state['ema'] + (1.0 - alpha) * value
        state['ema'] = ema
        if rule.invert:
            ema = -ema
        degrees = (ema * dt) / params['counts_per_degree']
        self.acc['mouse:' + rule.target.value] += degrees * rule.sensitivity

    def _wheel_vector(self, rule, value):
        """返回 (轴 'x'/'y', 方向 ±1)；auto 按物理轴自动选左右/上下。"""
        name = rule.target.value
        if name == 'auto':
            axis = 'x' if rule.axis in ('lx', 'rx') else 'y'
            return axis, 1 if value >= 0 else -1
        if name == 'x':
            return 'x', 1 if value >= 0 else -1
        if name == 'y':
            return 'y', 1 if value >= 0 else -1
        if name == 'up':
            return 'y', 1
        if name == 'down':
            return 'y', -1
        if name == 'left':
            return 'x', -1
        if name == 'right':
            return 'x', 1
        return None

    def _axis_wheel(self, rule, value, now, dt=0.0):
        if rule.target.kind != 'wheel':
            return 0, 0, 0, 0
        if rule.invert:
            value = -value
        vector = self._wheel_vector(rule, value)
        if vector is None:
            return 0, 0, 0, 0
        axis, sign = vector

        if rule.max_hz > 0:
            # 比例滚轮：高分辨率 120 单位/格，慢速也平滑。
            # 同时输出标准 REL_WHEEL，兼容只认整格的老程序。
            state = self.axis_state.setdefault(
                id(rule), {'wheel_hi_acc': 0.0, 'hwheel_hi_acc': 0.0,
                           'wheel_norm_acc': 0.0, 'hwheel_norm_acc': 0.0,
                           'axis': None, 'sign': 0})
            dz = _clamp(rule.deadzone, 0.0, 0.95)
            mag = abs(value)
            if mag <= dz:
                for key in ('wheel_hi_acc', 'hwheel_hi_acc',
                            'wheel_norm_acc', 'hwheel_norm_acc'):
                    state[key] = 0.0
                state['axis'] = None
                state['sign'] = 0
                return 0, 0, 0, 0
            if state['axis'] != axis or state['sign'] != sign:
                for key in ('wheel_hi_acc', 'hwheel_hi_acc',
                            'wheel_norm_acc', 'hwheel_norm_acc'):
                    state[key] = 0.0
                state['axis'] = axis
                state['sign'] = sign
            norm = (mag - dz) / (1.0 - dz)
            if rule.curve and rule.curve != 1.0:
                norm = norm ** rule.curve
            hi_key = 'wheel_hi_acc' if axis == 'y' else 'hwheel_hi_acc'
            norm_key = 'wheel_norm_acc' if axis == 'y' else 'hwheel_norm_acc'
            state[hi_key] += rule.max_hz * norm * dt * sign * 120.0
            hi = int(state[hi_key])
            state[hi_key] -= hi
            state[norm_key] += hi
            steps = int(state[norm_key] / 120.0)
            state[norm_key] -= steps * 120.0
            if axis == 'y':
                return steps, 0, hi, 0
            return 0, steps, 0, hi

        state = self.axis_state.setdefault(
            id(rule), {'active': False, 'next': 0.0,
                       'axis': None, 'sign': 0})
        mag = abs(value)
        if state['active'] and (state['axis'] != axis
                                or state['sign'] != sign):
            state['active'] = False
            state['next'] = 0.0
        if state['active']:
            if mag <= rule.release_threshold:
                state['active'] = False
                return 0, 0, 0, 0
            if rule.repeat_hz <= 0:
                return 0, 0, 0, 0
            interval = 1.0 / rule.repeat_hz
            steps = 0
            while now >= state['next'] and steps < 10:
                steps += 1
                state['next'] += interval
            if axis == 'y':
                return sign * steps, 0, sign * steps * 120, 0
            return 0, sign * steps, 0, sign * steps * 120
        if mag >= rule.threshold:
            state['active'] = True
            state['axis'] = axis
            state['sign'] = sign
            interval = 1.0 / rule.repeat_hz if rule.repeat_hz > 0 else 0.0
            state['next'] = now + interval
            if axis == 'y':
                return sign, 0, sign * 120, 0
            return 0, sign, 0, sign * 120
        return 0, 0, 0, 0

    def _apply_keypair(self, rule, value, pad_buttons, keys, mouse):
        if rule.target.kind != 'keypair':
            return 0, 0
        state = self.axis_state.setdefault(id(rule), {'side': 0})
        scaled = _shape(value, rule)
        side = 0 if scaled == 0 else (1 if scaled > 0 else -1)
        state['side'] = side
        chosen = rule.pos if side > 0 else rule.neg if side < 0 else None
        if chosen is None:
            return 0, 0
        return self._add_target(chosen, pad_buttons, keys, mouse)

    def _flush_mouse(self):
        dx = int(self.acc['mouse:x'])
        dy = int(self.acc['mouse:y'])
        self.acc['mouse:x'] -= dx
        self.acc['mouse:y'] -= dy
        return dx, dy

    def _commit(self, out):
        if self.devices is None:
            return
        self.devices.pad.apply(out.pad)
        self.devices.keyboard.apply(out.keys)
        self.devices.mouse.apply(out.mouse, out.dx, out.dy,
                                 out.wheel, out.hwheel,
                                 out.wheel_hi, out.hwheel_hi)


def describe(prev, cur):
    """两帧 FrameState 的差异，给 --dry-run 用。"""
    parts = []
    pad_line = _pad_diff(prev.pad, cur.pad)
    if pad_line:
        parts.append(pad_line)
    if cur.keys != prev.keys:
        added = cur.keys - prev.keys
        removed = prev.keys - cur.keys
        if added:
            parts.append('keys+%s' % ' '.join(_key_names(added)))
        if removed:
            parts.append('keys-%s' % ' '.join(_key_names(removed)))
    if cur.mouse != prev.mouse:
        if cur.mouse - prev.mouse:
            parts.append('mouse+%s' % ' '.join(map(str, cur.mouse - prev.mouse)))
        if prev.mouse - cur.mouse:
            parts.append('mouse-%s' % ' '.join(map(str, prev.mouse - cur.mouse)))
    if cur.dx or cur.dy:
        parts.append('move(%d,%d)' % (cur.dx, cur.dy))
    if cur.wheel:
        parts.append('wheel%+d' % cur.wheel)
    if cur.hwheel:
        parts.append('hwheel%+d' % cur.hwheel)
    return ' '.join(parts)


def _pad_diff(a, b):
    parts = []
    for name in sorted(b.buttons - a.buttons):
        parts.append('+' + name)
    for name in sorted(a.buttons - b.buttons):
        parts.append('-' + name)
    if a.hat != b.hat:
        parts.append('hat=%s' % (b.hat,))
    for name, old, new in zip(('lx', 'ly', 'rx', 'ry'), a.axes, b.axes):
        if old != new:
            parts.append('%s=%d' % (name, new))
    for name, old, new in zip(('lt', 'rt'), a.triggers, b.triggers):
        if old != new:
            parts.append('%s=%d' % (name, new))
    return ' '.join(parts)


def _key_names(codes):
    names = []
    for code in sorted(codes, key=key_order):
        name = ecodes.bytype[ecodes.EV_KEY].get(code, str(code))
        names.append(name)
    return names


# ---------------------------------------------------------------------------
# 展示用格式化（show 命令）
# ---------------------------------------------------------------------------
_KEY_DISPLAY = {
    ecodes.KEY_LEFTCTRL: 'ctrl', ecodes.KEY_RIGHTCTRL: 'rightctrl',
    ecodes.KEY_LEFTSHIFT: 'shift', ecodes.KEY_RIGHTSHIFT: 'rightshift',
    ecodes.KEY_LEFTALT: 'alt', ecodes.KEY_RIGHTALT: 'rightalt',
    ecodes.KEY_LEFTMETA: 'meta', ecodes.KEY_RIGHTMETA: 'rightmeta',
    ecodes.KEY_ESC: 'esc', ecodes.KEY_SPACE: 'space',
    ecodes.KEY_ENTER: 'enter', ecodes.KEY_BACKSPACE: 'backspace',
    ecodes.KEY_TAB: 'tab', ecodes.KEY_PAGEUP: 'pageup',
    ecodes.KEY_PAGEDOWN: 'pagedown',
}


def key_name(code):
    if code in _KEY_DISPLAY:
        return _KEY_DISPLAY[code]
    name = ecodes.bytype[ecodes.EV_KEY].get(code)
    if isinstance(name, (list, tuple)):
        name = name[0] if name else None
    if not name:
        return 'code%d' % code
    return name[4:].lower() if name.startswith('KEY_') else name.lower()


_MOUSE_NAMES = {v: k for k, v in MOUSE_BUTTONS.items()}


def format_target(target, short=False):
    kind = target.kind
    if kind == 'none':
        return '—' if short else 'none'
    if kind == 'pad':
        return target.value.upper() if short else 'xbox:' + target.value
    if kind == 'key':
        combo = '+'.join(key_name(code) for code in target.value)
        return combo if short else 'key:' + combo
    if kind == 'mouse_button':
        name = _MOUSE_NAMES.get(target.value, str(target.value))
        if short:
            return {'left': '左键', 'right': '右键', 'middle': '中键',
                    'side': '侧键', 'extra': '附加键'}.get(name, name)
        return 'mouse:' + name
    if kind == 'mouse_move':
        if short:
            return '鼠标←→' if target.value == 'x' else '鼠标↑↓'
        return 'mouse:' + target.value
    if kind == 'wheel':
        names = {'up': '滚轮↑', 'down': '滚轮↓', 'left': '滚轮←',
                 'right': '滚轮→', 'x': '滚轮左右', 'y': '滚轮上下',
                 'auto': '滚轮自动'}
        if target.value == 'auto':
            return '滚轮自动' if short else 'wheel'
        return names.get(target.value, '滚轮') if short \
            else 'wheel:' + str(target.value)
    if kind == 'toggle':
        if short:
            return '体感开关' if target.value == 'gyro' else '开关:' + target.value
        return 'toggle:' + target.value
    if kind == 'keypair':
        return 'keypair'
    return kind


def describe_axis(rule, short=False):
    if rule.target.kind == 'keypair':
        if short:
            return '%s ⇄ %s' % (format_target(rule.neg, True),
                                format_target(rule.pos, True))
        return 'keypair：负 %s / 正 %s（死区 %.2f）' % (
            format_target(rule.neg), format_target(rule.pos), rule.deadzone)
    base = format_target(rule.target, short)
    if short:
        return base
    params = []
    if rule.axis.startswith('gyro'):
        params.append('灵敏度 %g px/°' % rule.sensitivity)
        if rule.smoothing:
            params.append('平滑 %g' % rule.smoothing)
        if rule.deadband:
            params.append('死区 %g' % rule.deadband)
    elif rule.target.kind == 'mouse_move':
        params.append('死区 %.2f' % rule.deadzone)
        if rule.curve != 1.0:
            params.append('曲线 %.1f' % rule.curve)
        params.append('速度 %d px/s' % rule.max_speed)
    elif rule.target.kind == 'wheel':
        if rule.max_hz > 0:
            params.append('死区 %.2f' % rule.deadzone)
            params.append('最大 %g 格/秒' % rule.max_hz)
            if rule.curve != 1.0:
                params.append('曲线 %.1f' % rule.curve)
        else:
            params.append('阈值 %.2f' % rule.threshold)
            params.append('释放 %.2f' % rule.release_threshold)
            if rule.repeat_hz:
                params.append('连发 %g Hz' % rule.repeat_hz)
    elif rule.target.kind in ('key', 'mouse_button', 'pad'):
        params.append('阈值 %.2f' % rule.threshold)
    elif rule.target.kind == 'pad_axis':
        if rule.invert:
            params.append('反向')
    if rule.group:
        params.append('组:%s' % rule.group)
    return base + ('（%s）' % ', '.join(params) if params else '')
