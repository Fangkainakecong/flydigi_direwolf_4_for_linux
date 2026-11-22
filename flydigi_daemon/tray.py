"""状态栏托盘（XFCE/AppIndicator）：状态、暂停、配置、编辑、重启、退出"""

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
INSTALL_HINT = '请先运行一次：sudo scripts/install_services.sh'


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
    """返回 (ok, 错误文本)。没有 polkit 权限时尝试 pkexec 弹一次授权框。"""
    try:
        proc = subprocess.run(['systemctl', action, SERVICE],
                              check=False, capture_output=True, text=True)
        err = (proc.stderr or '').strip()
        if proc.returncode != 0 and 'Interactive authentication required' in err:
            proc = subprocess.run(['pkexec', 'systemctl', action, SERVICE],
                                  check=False, capture_output=True, text=True)
            err = (proc.stderr or '').strip()
        if proc.returncode != 0:
            print('systemctl %s 失败: %s' % (action, err), file=sys.stderr)
            if 'Interactive authentication required' in err:
                err += '\n\n' + INSTALL_HINT
            return False, err or ('systemctl 退出码 %d' % proc.returncode)
        return True, ''
    except OSError as exc:
        print('systemctl %s 失败: %r' % (action, exc), file=sys.stderr)
        return False, repr(exc)


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

        self.cfg = cfgmod.load()
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
        self.config_item = Gtk.MenuItem(label='配置选择')
        self.profile_menu = Gtk.Menu()
        self.config_item.set_submenu(self.profile_menu)
        self.config_item.show()
        self.menu.append(self.config_item)
        self._item('编辑配置', self.edit)
        self._separator()
        self._item('重启程序', self.restart)
        self._item('退出', self.quit_all)
        self.ind.set_menu(self.menu)

        self.profile_items = {}
        self._profile_names = []
        self._updating = False
        self._rebuild_profiles(self.cfg)
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

    def _rebuild_profiles(self, cfg):
        names = list(cfgmod.slot_profiles(cfg))
        for name in (cfg.get('profile') or {}):
            if name not in names:
                names.append(name)
        if names == self._profile_names:
            return
        self._profile_names = names
        for child in self.profile_menu.get_children():
            self.profile_menu.remove(child)
        self.profile_items = {}
        group = None
        for name in names:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, name)
            if group is None:
                group = item
            item.connect('activate',
                         lambda _item, n=name: self.select_profile(n))
            item.show()
            self.profile_menu.append(item)
            self.profile_items[name] = item
        self.config_item.set_sensitive(bool(names))
        if not names:
            item = Gtk.MenuItem(label='（没有配置）')
            item.set_sensitive(False)
            item.show()
            self.profile_menu.append(item)

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
            cfg, info = self.cfg, None
        self._rebuild_profiles(cfg)

        current = None
        slot = None
        if info:
            current = info.get('profile')
            slot = info.get('profile_slot', info.get('slot'))
            if current not in self.profile_items:
                profiles = cfgmod.slot_profiles(cfg)
                if (isinstance(slot, int) and 0 < slot <= len(profiles)):
                    current = profiles[slot - 1]
            self.profile_item.set_label(
                '配置: %s%s' % (current or '?',
                               '（槽 %s）' % slot if slot else ''))
            pct = info.get('battery_percent')
            if pct is None:
                self.battery_item.set_label('电量: —')
            else:
                stale = '（上次）' if info.get('battery_stale') else ''
                self.battery_item.set_label('电量: %d%%%s' % (pct, stale))
        else:
            self.profile_item.set_label('配置: —')
            self.battery_item.set_label('电量: —')

        self._updating = True
        try:
            for name, item in self.profile_items.items():
                item.set_active(name == current)
        finally:
            self._updating = False
        return True

    def _error(self, title, detail=''):
        dialog = Gtk.MessageDialog(None, 0, Gtk.MessageType.ERROR,
                                   Gtk.ButtonsType.OK, title)
        if detail:
            dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    def toggle(self):
        state = _state()
        action = 'stop' if state in ('active', 'activating', 'reloading') \
            else 'start'
        ok, err = _systemctl(action)
        if not ok:
            self._error('无法%s映射' % ('暂停' if action == 'stop' else '恢复'),
                        err)
            return
        GLib.timeout_add_seconds(1, self.refresh)

    def select_profile(self, name):
        if self._updating:
            return
        cfg = cfgmod.load()
        if name not in (cfg.get('profile') or {}):
            self._error('配置不存在', name)
            return
        info = statusmod.read(cfg)
        if info and info.get('profile') == name:
            return
        path = cfgmod.request_file(cfg)
        try:
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(name + '\n')
        except OSError as exc:
            self._error('无法写入配置请求', repr(exc))
            return
        ok, err = _systemctl('restart')
        if not ok:
            try:
                os.unlink(path)
            except OSError:
                pass
            self._error('切换配置失败', err)
            return
        GLib.timeout_add_seconds(2, self.refresh)

    def restart(self):
        ok, err = _systemctl('restart')
        if not ok:
            self._error('重启失败', err)
            return
        GLib.timeout_add_seconds(2, self.refresh)

    def edit(self):
        script = os.path.join(ROOT, 'scripts', 'flydigi_edit.sh')
        subprocess.Popen([script], start_new_session=True)

    def quit_all(self):
        ok, err = _systemctl('stop')
        if not ok:
            self._error('无法停止映射，未退出', err)
            return
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
