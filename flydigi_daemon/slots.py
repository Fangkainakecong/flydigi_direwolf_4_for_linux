"""软件四槽：SELECT + A/B/X/Y 切 profile（M2）

固件里 `SELECT + ABXY` 是空键位（实测四组都只上报 `back` + 字母），
所以由 daemon 自己解释，不碰板载槽，切换零停顿。对应
[daemon-plan.md](../docs/daemon-plan.md) §5。

仲裁规则（默认 200 ms 窗口，可配）::

    t0            SELECT 按下
    t0..t0+200ms  等组合键，Back 先不输出
      ├─ 窗口内按下 A/B/X/Y → 吞掉两键，切到对应 profile，
      │                       一直吞到所有键松开
      └─ 超时          → 输出 Back，之后 ABXY 正常透传

细节：

* 只认**边沿**：先按着 A 再按 SELECT 不算组合。
* 窗口内按了其它键（Start/LB/…）→ 立刻放弃仲裁、输出 Back，保证顺序。
* 组合成功后，按住 SELECT 再按别的字母不会切第二次，也不会漏出去。
* `suppress_chord = false` 时仍然切换，但 Back/字母照常输出（调试用）。

这一层只管"键集合怎么过滤"和"切到哪个槽"；真正的映射在 M3。
"""

import json
import os
import tempfile
import time
from dataclasses import replace

from .translate import EMPTY

# SELECT + 字母 → profile 序号（与 config 的 slot_profiles 顺序一致）
LETTER_SLOTS = {'a': 0, 'b': 1, 'x': 2, 'y': 3}


def _held(state, name):
    return name in state.buttons or name in state.extras


def default_state_path():
    """普通用户用 XDG 状态目录，root（systemd 服务）用 /var/lib。"""
    if os.geteuid() == 0:
        base = '/var/lib/flydigi'
    else:
        base = os.environ.get('XDG_STATE_HOME') or os.path.join(
            os.path.expanduser('~'), '.local', 'state')
        base = os.path.join(base, 'flydigi')
    return os.path.join(base, 'state.json')


class SlotController(object):
    """把原始 `PadState` 过滤成"该发给游戏的" `PadState`，并维护当前槽。"""

    def __init__(self, profiles, chord='back', window_ms=200, suppress=True,
                 state_path=None, startup_profile=None, persist=True,
                 clock=None, on_switch=None):
        self.profiles = list(profiles) or ['default']
        self.chord = chord
        self.window = max(0.0, float(window_ms) / 1000.0)
        self.suppress = bool(suppress)
        self.state_path = state_path
        self.persist = persist
        self.clock = clock or time.monotonic
        self.on_switch = on_switch

        self.prev_raw = EMPTY
        self.last_raw = EMPTY
        self.out = EMPTY

        self.awaiting = False
        self.deadline = 0.0
        self.swallow_chord = False
        self.swallowed_letters = set()

        self.slot = self._initial_slot(startup_profile)

    # -- 状态 ------------------------------------------------------------
    @property
    def profile(self):
        return self.profiles[self.slot]

    def _initial_slot(self, startup_profile):
        saved = self._load_state()
        if saved:
            name = saved.get('profile')
            if name in self.profiles:
                return self.profiles.index(name)
            # 配置改名后旧名字失效，但槽位序号还在；优先按序号恢复。
            index = saved.get('slot')
            if isinstance(index, int) and 0 <= index < len(self.profiles):
                return index
        if startup_profile in self.profiles:
            return self.profiles.index(startup_profile)
        return 0

    def _load_state(self):
        if not self.persist or not self.state_path:
            return {}
        try:
            with open(self.state_path, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        if not self.persist or not self.state_path:
            return
        data = {'profile': self.profile, 'slot': self.slot}
        try:
            folder = os.path.dirname(self.state_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=folder or '.', prefix='.state-')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f)
                f.write('\n')
            os.replace(tmp, self.state_path)
        except OSError:
            # 状态写不进去不能拖垮输入转发。
            pass

    # -- 热路径 ----------------------------------------------------------
    def feed(self, raw, now=None):
        """喂一帧原始状态，返回过滤后的状态。"""
        now = self.clock() if now is None else now
        if self.awaiting and now >= self.deadline:
            self.awaiting = False

        # 已经吞掉的字母，物理松开后就不再需要吞了。
        for letter in list(self.swallowed_letters):
            if letter not in raw.buttons:
                self.swallowed_letters.discard(letter)

        pressed = raw.buttons - self.prev_raw.buttons
        physical = raw
        chord_held = _held(raw, self.chord)
        chord_was_held = _held(self.prev_raw, self.chord)

        if chord_held:
            if not chord_was_held and self.window > 0:
                self.awaiting = True
                self.deadline = now + self.window

            if self.awaiting:
                hit = [l for l in LETTER_SLOTS if l in pressed]
                if hit:
                    letter = hit[0]
                    self.awaiting = False
                    self._switch(LETTER_SLOTS[letter], letter)
                    if self.suppress:
                        self.swallowed_letters.add(letter)
                        self.swallow_chord = True
                elif pressed - {self.chord}:
                    # 窗口内先按了别的键：放弃仲裁，Back 立刻放行。
                    self.awaiting = False
        elif chord_was_held:
            # SELECT 松开了。窗口内单独点按 → 补发一个 Back（下一帧自然松开）。
            if self.awaiting:
                self.awaiting = False
                raw = replace(raw, buttons=raw.buttons | {self.chord})
            if self.swallow_chord:
                # 组合期间还按着的字母，继续吞到物理松开为止。
                for letter in LETTER_SLOTS:
                    if letter in raw.buttons:
                        self.swallowed_letters.add(letter)
                self.swallow_chord = False

        self.last_raw = physical
        out = self._render(raw)
        self.prev_raw = physical
        self.out = out
        return out

    def tick(self, now=None):
        """没有新报文时调用；窗口超时就把 Back 放行。没变化返回 None。"""
        now = self.clock() if now is None else now
        if not (self.awaiting and now >= self.deadline):
            return None
        self.awaiting = False
        out = self._render(self.last_raw)
        self.out = out
        return out

    def _render(self, raw):
        if not self.suppress:
            return raw
        buttons = set(raw.buttons)
        if self.awaiting or self.swallow_chord:
            buttons.discard(self.chord)
        if self.swallow_chord:
            buttons.difference_update(LETTER_SLOTS)
        buttons.difference_update(self.swallowed_letters)
        return replace(raw, buttons=frozenset(buttons))

    def _switch(self, index, letter):
        if not (0 <= index < len(self.profiles)):
            return
        self.slot = index
        self._save_state()
        if self.on_switch is not None:
            self.on_switch(index, self.profile, letter)
