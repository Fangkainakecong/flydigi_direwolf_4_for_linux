"""状态栏托盘（XFCE/AppIndicator）：暂停/恢复、重启、编辑配置、退出"""

import fcntl
import os
import shutil
import subprocess
import sys

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, GLib                      # noqa: E402
from gi.repository import AyatanaAppIndicator3 as appindicator  # noqa: E402

from . import config as cfgmod
from . import status as statusmod
from .procname import set_name

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_SRC = os.path.join(ROOT, 'assets', 'flydigi.svg')
SERVICE = 'flydigi-dw4.service'
LOCK_PATH = '/tmp/flydigi-tray.lock'


def _ensure_icon():
    """把项目图标装到用户 hicolor 主题；失败时退回系统图标。"""
    data_home = (os.environ.get('XDG_DATA_HOME')
                 or os.path.expanduser('~/.local/share'))
    dest_dir = os.path.join(data_home, 'icons', 'hicolor', 'scalable', 'apps')
    dest = os.path.join(dest_dir, 'flydigi.svg')
    try:
        if os.path.exists(ICON_SRC):
            os.makedirs(dest_dir, exist_ok=True)
            if (not os.path.exists(dest)
                    or os.path.getmtime(dest) < os.path.getmtime(ICON_SRC)):
                shutil.copyfile(ICON_SRC, dest)
            return 'flydigi'
    except OSError:
        pass
    return ICON_SRC if os.path.exists(ICON_SRC) else 'input-gaming-symbolic'


def _systemctl(action):
    try:
        proc = subprocess.run(['systemctl', action, SERVICE],
                              check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            print('systemctl %s 失败: %s'
                  % (action, (proc.stderr or '').strip()), file=sys.stderr)
        return proc
    except OSError as exc:
        print('systemctl %s 失败: %r' % (action, exc), file=sys.stderr)
        return exc


def _state():
    try:
        out = subprocess.run(['systemctl', 'is-active', SERVICE],
                             check=False, capture_output=True,
                             text=True).stdout.strip()
        return out or 'unknown'
    except OSError:
        return 'unknown'


class Tray(object):
    def __init__(self):
        set_name('flydigi-tray')
        self.lock = open(LOCK_PATH, 'w')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit('flydigi-tray 已在运行')

        icon = _ensure_icon()
        self.ind = appindicator.Indicator.new(
            'flydigi-tray', icon,
            appindicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(appindicator.IndicatorStatus.ACTIVE)
        self.ind.set_title('Flydigi Direwolf 4')
        try:
            self.ind.set_icon_full(icon, 'Flydigi Direwolf 4')
        except Exception:               # noqa: BLE001
            pass

        self.menu = Gtk.Menu()
        self.state_item = self._item('daemon: —', None)
        self.profile_item = self._item('配置: —', None)
        self.battery_item = self._item('电量: —', None)
        self._separator()
        self.toggle_item = self._item('暂停映射', self.toggle)
        self._item('重启 daemon', self.restart)
        self._item('编辑配置', self.edit)
        self._separator()
        self._item('退出托盘（映射继续）', self.quit)
        self._item('停止映射并退出', self.quit_all)
        self.ind.set_menu(self.menu)

        self.refresh()
        GLib.timeout_add_seconds(5, self.refresh)

    def _item(self, label, callback):
        item = Gtk.MenuItem(label=label)
        if callback is not None:
            item.connect('activate', lambda *_: callback())
        else:
            item.set_sensitive(False)
        item.show()
        self.menu.append(item)
        return item

    def _separator(self):
        item = Gtk.SeparatorMenuItem()
        item.show()
        self.menu.append(item)

    def refresh(self):
        state = _state()
        label = {'active': '运行中', 'inactive': '已停止',
                 'failed': '失败', 'activating': '启动中',
                 'deactivating': '停止中', 'reloading': '重载中'}.get(state,
                                                                     state)
        self.state_item.set_label('daemon: ' + label)
        running = state in ('active', 'activating', 'reloading')
        self.toggle_item.set_label('暂停映射' if running else '恢复映射')
        try:
            cfg = cfgmod.load()
            info = statusmod.read(cfg)
        except (SystemExit, Exception):     # noqa: BLE001
            info = None
        if info:
            slot = info.get('slot')
            profiles = cfgmod.slot_profiles(cfg)
            name = profiles[slot - 1] if (isinstance(slot, int)
                                          and 0 < slot <= len(profiles)) else '?'
            self.profile_item.set_label('配置: %s（槽 %s）' % (name, slot))
            pct = info.get('battery_percent')
            if pct is None:
                self.battery_item.set_label('电量: —')
            else:
                self.battery_item.set_label(
                    '电量: %d%%（%s/5，%s）'
                    % (pct, info.get('battery'), info.get('fw') or '?'))
        else:
            self.profile_item.set_label('配置: —')
            self.battery_item.set_label('电量: —')
        return True

    def toggle(self):
        state = _state()
        action = 'stop' if state in ('active', 'activating', 'reloading') \
            else 'start'
        _systemctl(action)
        self.refresh()

    def restart(self):
        _systemctl('restart')
        self.refresh()

    def edit(self):
        script = os.path.join(ROOT, 'scripts', 'flydigi_edit.sh')
        subprocess.Popen([script], start_new_session=True)

    def quit(self):
        Gtk.main_quit()

    def quit_all(self):
        _systemctl('stop')
        Gtk.main_quit()

    def run(self):
        Gtk.main()


def main():
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        print('没有 DISPLAY/WAYLAND_DISPLAY；托盘需要桌面会话')
        return 1
    try:
        Tray().run()
    except Exception as exc:            # noqa: BLE001
        print('托盘启动失败：%r' % (exc,), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
