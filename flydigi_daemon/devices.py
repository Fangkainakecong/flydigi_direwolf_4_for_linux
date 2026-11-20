"""三台 uinput 虚拟设备：Xbox 手柄 / 键盘 / 鼠标

capability 与 `scripts/flydigi_uinput_ident_probe.py` 完全一致，已经验证：

* 手柄身份 `045e:028e`，version `0x0114`，SDL GUID
  `030000005e0400008e02000014010000`；
* 11 个按钮 + 8 个轴，Steam / RetroArch 认成 Xbox 360；
* 键盘 1..248 全键，鼠标 5 键 + 相对轴。

M1 只写手柄；键盘/鼠标先建好但不用（M3 的映射目标）。

FF（震动）在 M5 才加。这里故意**不**声明 `EV_FF`：声明了就必须及时处理
客户端的 upload/erase，否则 `upload_effect()` 会一直阻塞。等 M5 把
12 字节 rumble 包接上再一起开。
"""

import os

from evdev import AbsInfo, UInput, ecodes

from .translate import EMPTY, PadState

PAD_NAME_DEFAULT = 'Microsoft X-Box 360 pad'
PAD_VID, PAD_PID, PAD_VERSION = 0x045E, 0x028E, 0x0114

AXIS = AbsInfo(0, -32768, 32767, 16, 128, 0)
TRIGGER = AbsInfo(0, 0, 255, 0, 0, 0)
HAT = AbsInfo(0, -1, 1, 0, 0, 0)

BUTTON_MAP = {
    'a': ecodes.BTN_A,
    'b': ecodes.BTN_B,
    'x': ecodes.BTN_X,
    'y': ecodes.BTN_Y,
    'lb': ecodes.BTN_TL,
    'rb': ecodes.BTN_TR,
    'back': ecodes.BTN_SELECT,
    'start': ecodes.BTN_START,
    'home': ecodes.BTN_MODE,
    'l3': ecodes.BTN_THUMBL,
    'r3': ecodes.BTN_THUMBR,
}

AXIS_MAP = (ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY)
TRIGGER_MAP = (ecodes.ABS_Z, ecodes.ABS_RZ)


def pad_caps(with_ff=False):
    caps = {
        ecodes.EV_KEY: list(BUTTON_MAP.values()),
        ecodes.EV_ABS: [
            (ecodes.ABS_X, AXIS), (ecodes.ABS_Y, AXIS),
            (ecodes.ABS_RX, AXIS), (ecodes.ABS_RY, AXIS),
            (ecodes.ABS_Z, TRIGGER), (ecodes.ABS_RZ, TRIGGER),
            (ecodes.ABS_HAT0X, HAT), (ecodes.ABS_HAT0Y, HAT),
        ],
    }
    if with_ff:
        caps[ecodes.EV_FF] = [ecodes.FF_RUMBLE]
    return caps


KEYBOARD_KEYS = list(range(1, 249))
for _extra in (ecodes.KEY_PLAYPAUSE, ecodes.KEY_NEXTSONG,
               ecodes.KEY_PREVIOUSSONG, ecodes.KEY_VOLUMEUP,
               ecodes.KEY_VOLUMEDOWN, ecodes.KEY_MUTE):
    if _extra not in KEYBOARD_KEYS:
        KEYBOARD_KEYS.append(_extra)

MOUSE_BUTTONS = [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE,
                 ecodes.BTN_SIDE, ecodes.BTN_EXTRA]
MOUSE_RELS = [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL, ecodes.REL_HWHEEL]
for _extra in ('REL_WHEEL_HI_RES', 'REL_HWHEEL_HI_RES'):
    _code = getattr(ecodes, _extra, None)
    if _code is not None:
        MOUSE_RELS.append(_code)

KB_NAME_DEFAULT = 'Flydigi DW4 Virtual Keyboard'
MOUSE_NAME_DEFAULT = 'Flydigi DW4 Virtual Mouse'


class VirtualGamepad(object):
    """把 `PadState` 差量写进 uinput。只有变化时才写，1000 Hz 下开销很小。"""

    def __init__(self, name=PAD_NAME_DEFAULT, vendor=PAD_VID, product=PAD_PID,
                 version=PAD_VERSION, with_ff=False):
        self.ui = UInput(pad_caps(with_ff), name=name, version=version,
                         vendor=vendor, product=product, bustype=ecodes.BUS_USB)
        self.name = name
        self.cur = EMPTY

    def apply(self, state):
        if state is None or state == self.cur:
            return False
        cur = self.cur
        changed = False

        if state.buttons != cur.buttons:
            for name, code in BUTTON_MAP.items():
                old = name in cur.buttons
                new = name in state.buttons
                if old != new:
                    self.ui.write(ecodes.EV_KEY, code, 1 if new else 0)
                    changed = True

        if state.hat != cur.hat:
            self.ui.write(ecodes.EV_ABS, ecodes.ABS_HAT0X, state.hat[0])
            self.ui.write(ecodes.EV_ABS, ecodes.ABS_HAT0Y, state.hat[1])
            changed = True

        for code, old, new in zip(AXIS_MAP, cur.axes, state.axes):
            if old != new:
                self.ui.write(ecodes.EV_ABS, code, new)
                changed = True

        for code, old, new in zip(TRIGGER_MAP, cur.triggers, state.triggers):
            if old != new:
                self.ui.write(ecodes.EV_ABS, code, new)
                changed = True

        if changed:
            self.ui.syn()
        self.cur = state
        return changed

    def release_all(self):
        """松开所有键、回中所有轴。退出/异常/切配置时调用。"""
        self.apply(EMPTY)

    def close(self):
        try:
            self.release_all()
        finally:
            self.ui.close()


class VirtualKeyboard(object):
    def __init__(self, name=KB_NAME_DEFAULT, vendor=0x1234, product=0x5678):
        self.ui = UInput({ecodes.EV_KEY: KEYBOARD_KEYS}, name=name,
                         version=0x0001, vendor=vendor, product=product,
                         bustype=ecodes.BUS_USB)
        self.name = name

    def release_all(self):
        for code in KEYBOARD_KEYS:
            self.ui.write(ecodes.EV_KEY, code, 0)
        self.ui.syn()

    def close(self):
        try:
            self.release_all()
        finally:
            self.ui.close()


class VirtualMouse(object):
    def __init__(self, name=MOUSE_NAME_DEFAULT, vendor=0x1234, product=0x5679):
        self.ui = UInput({ecodes.EV_KEY: MOUSE_BUTTONS,
                          ecodes.EV_REL: MOUSE_RELS},
                         name=name, version=0x0001, vendor=vendor,
                         product=product, bustype=ecodes.BUS_USB)
        self.name = name

    def release_all(self):
        for code in MOUSE_BUTTONS:
            self.ui.write(ecodes.EV_KEY, code, 0)
        self.ui.syn()

    def close(self):
        try:
            self.release_all()
        finally:
            self.ui.close()


class Devices(object):
    """三台设备一起管理；任何一步失败都要把已建好的关掉。"""

    def __init__(self, cfg, with_ff=False):
        vcfg = cfg.get('virtual', {})
        gcfg = vcfg.get('gamepad', {})
        kcfg = vcfg.get('keyboard', {})
        mcfg = vcfg.get('mouse', {})
        self.pad = None
        self.keyboard = None
        self.mouse = None
        try:
            self.pad = VirtualGamepad(
                name=gcfg.get('name', PAD_NAME_DEFAULT),
                vendor=int(gcfg.get('vendor', PAD_VID)),
                product=int(gcfg.get('product', PAD_PID)),
                version=int(gcfg.get('version', PAD_VERSION)),
                with_ff=with_ff)
            self.keyboard = VirtualKeyboard(
                name=kcfg.get('name', KB_NAME_DEFAULT),
                vendor=int(kcfg.get('vendor', 0x1234)),
                product=int(kcfg.get('product', 0x5678)))
            self.mouse = VirtualMouse(
                name=mcfg.get('name', MOUSE_NAME_DEFAULT),
                vendor=int(mcfg.get('vendor', 0x1234)),
                product=int(mcfg.get('product', 0x5679)))
        except Exception:
            self.close()
            raise

    def close(self):
        for dev in (self.mouse, self.keyboard, self.pad):
            if dev is not None:
                try:
                    dev.close()
                except Exception:       # noqa: BLE001
                    pass
        self.pad = self.keyboard = self.mouse = None


def scan_event_nodes(names):
    """从 /sys/class/input 找同名设备的 /dev/input/eventN。

    不用 `UInput.device`：非 root 时它可能是 None，扫 /sys 更可靠。
    """
    found = {}
    base = '/sys/class/input'
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return found
    for entry in entries:
        if not entry.startswith('event'):
            continue
        try:
            with open(os.path.join(base, entry, 'device', 'name')) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name in names:
            found.setdefault(name, []).append('/dev/input/' + entry)
    return found
