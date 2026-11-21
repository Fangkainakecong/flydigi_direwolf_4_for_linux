"""轻量监视进程：按需启动/停止 Flydigi daemon

设计：

* 只认 `[device] product_hint` 匹配的 USB 设备（默认 `Direwolf 4`）；
  同时插着的黑武士2/`2.4G Android` 不会被选中。
* dongle 插入后启动 `flydigi-dw4.service`；拔掉就 stop。
* daemon 自己 1 小时无操作退出；watch 不立刻重启，而是等**下一次真实
  输入**（按键/摇杆/扳机）再启动。
* 冰原狼4 无线休眠（约 15 分钟）时 daemon 继续等；醒来如果 daemon 还在
  就直接恢复；已经因 idle 退出，则由 watch 的输入检测重新拉起。

只跑在 root（systemd 服务），因为它要读 `/dev/input` 并调用 systemctl。
"""

import os
import select
import subprocess
import time

import evdev
from evdev import ecodes

from . import config as cfgmod
from . import usb

SERVICE = 'flydigi-dw4.service'


def _systemctl(*args):
    try:
        return subprocess.run(['systemctl', *args], check=False,
                              capture_output=True, text=True)
    except OSError:
        return None


def service_active():
    result = _systemctl('is-active', '--quiet', SERVICE)
    return bool(result and result.returncode == 0)


def start_service():
    result = _systemctl('start', SERVICE)
    return bool(result and result.returncode == 0)


def stop_service():
    _systemctl('stop', SERVICE)


def manual_daemon_running():
    """用户手动 `python3 -m flydigi_daemon run` 时避免 service 重复启动。"""
    try:
        out = subprocess.run(['pgrep', '-af', 'python3 -m flydigi_daemon run'],
                             capture_output=True, text=True).stdout
    except OSError:
        return False
    for line in out.splitlines():
        if 'flydigi_daemon run' in line and 'watch' not in line:
            return True
    return False


def node_activity(path, timeout=1.0):
    """等这个 evdev 节点上是否有真实输入；超时返回 False。"""
    try:
        dev = evdev.InputDevice(path)
    except OSError:
        return False
    try:
        ready, _, _ = select.select([dev.fd], [], [], timeout)
        if not ready:
            return False
        for event in dev.read():
            if event.type == ecodes.EV_SYN:
                continue
            if event.type == ecodes.EV_KEY:
                return True
            if event.type == ecodes.EV_ABS:
                if event.code in (ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y):
                    return True
                if event.code in (ecodes.ABS_X, ecodes.ABS_Y,
                                  ecodes.ABS_RX, ecodes.ABS_RY) and \
                        abs(event.value) > 500:
                    return True
                if event.code in (ecodes.ABS_Z, ecodes.ABS_RZ) and \
                        event.value > 10:
                    return True
    except OSError:
        # 节点在 daemon 启动瞬间消失，当作有活动处理。
        return True
    finally:
        try:
            dev.close()
        except OSError:
            pass
    return False


def any_activity(info, timeout=1.0):
    for path in usb.find_event_nodes(info):
        if node_activity(path, timeout):
            return True
    return False


def run(cfg, args=None):
    if os.geteuid() != 0:
        print('watch 需要 root（systemd 服务），普通用户请用 sudo 或安装服务。')
        return 1
    devcfg = cfgmod.device_section(cfg)
    vendor, product = devcfg['vendor'], devcfg['product']
    hint = devcfg.get('product_hint') or ''
    print('watch: %04x:%04x product_hint=%r' % (vendor, product, hint))
    print('watch: systemd service = %s' % SERVICE)

    present = False
    was_active = False
    last_stop = 0.0
    failed = False
    info = None

    while True:
        try:
            info = usb.find_pad_info(vendor, product, hint)
            active = service_active()
            manual = manual_daemon_running()
            now = time.time()

            if was_active and not active:
                last_stop = now
                print('daemon 已停止，等待下一次操作')
            was_active = active

            if not info:
                if active:
                    stop_service()
                    active = False
                if present:
                    print('dongle 已拔出')
                present = False
                failed = False
            elif active or manual:
                present = True
            elif not present:
                print('检测到 dongle，启动 daemon')
                if start_service():
                    present = True
                    failed = False
                elif not failed:
                    print('启动 %s 失败；请先运行 install_services.sh'
                          % SERVICE)
                    failed = True
            elif now - last_stop > 3.0:
                nodes = usb.find_event_nodes(info)
                if not nodes:
                    # daemon 退出后 xpad 没自动绑回来时，补一次只绑本接口。
                    usb.bind_xpad(info)
                    nodes = usb.find_event_nodes(info)
                if any_activity(info):
                    print('检测到操作，重新启动 daemon')
                    if start_service():
                        failed = False
        except KeyboardInterrupt:
            print('\nwatch 已停止')
            return 0
        except Exception as exc:            # noqa: BLE001
            print('watch 错误：%r' % (exc,))
        time.sleep(1.0 if info else 2.0)
