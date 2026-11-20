"""M1：独占 dongle，把标准手柄输入透明转发到虚拟 Xbox 手柄

不做映射、不做体感、不做宏、不做震动。目标只有一个：让游戏看到的手柄
和原厂 xpad 一样，同时 daemon 能拿到 M1/M2/FN/Turbo（M1 只记日志）。

两种输入源：

* 实时：`Pad.takeover()` → usbfs 异步 URB → 每帧 `translate()`
* 回放：`--replay <capture.bin>`，不需要手柄，适合离线回归/调试
"""

import signal
import struct
import time

from . import config as cfgmod
from . import report as rep
from . import usb
from .devices import Devices, scan_event_nodes
from .translate import EMPTY, describe, translate


def _fmt_elapsed(seconds):
    return '%7.2fs' % seconds


def _install_stop_handler(flag):
    def handler(signum, frame):        # noqa: ARG001
        flag['running'] = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


def _print_devices(devs):
    nodes = scan_event_nodes({devs.pad.name, devs.keyboard.name,
                              devs.mouse.name})
    for role, dev in (('手柄', devs.pad), ('键盘', devs.keyboard),
                      ('鼠标', devs.mouse)):
        print('  %-3s %-18s %s'
              % (role, ' '.join(nodes.get(dev.name, ['(未找到节点)'])),
                 dev.name))


def run_live(cfg, dry_run=False, seconds=None):
    devcfg = cfgmod.device_section(cfg)
    dev = usb.find_pad(devcfg['vendor'], devcfg['product'])
    if not dev:
        print('未找到手柄（%04x:%04x）。确认 dongle 插好、手柄已开机。'
              % (devcfg['vendor'], devcfg['product']))
        return 1

    print('M1 透明转发' + ('（dry-run，不建虚拟设备）' if dry_run else ''))
    print('手柄: %s' % dev)
    pad = usb.Pad(dev, interface=devcfg['interface'])
    link = None
    devs = None
    try:
        print('解绑驱动: %s' % pad.takeover())
        link = usb.Link(pad)
        info = usb.identify_retry(link, tries=3)
        if info:
            print('识别: 固件=%s uid=%s 当前槽位=%d'
                  % (info['fw'], info['uid'], info['slot']))
        else:
            print('⚠ cmd16 无应答（不影响转发，但读不到槽位）')

        if not dry_run:
            devs = Devices(cfg)
            print('虚拟设备:')
            _print_devices(devs)
            print('提示：物理手柄已由 daemon 独占；Steam/RetroArch 用虚拟手柄。')

        watchdog = float(cfgmod.safety_section(cfg).get('watchdog_ms', 1500)) / 1000.0
        flag = {'running': True}
        _install_stop_handler(flag)
        print('开始转发（Ctrl-C 退出）…')
        t0 = time.time()
        last_reset = 0.0
        prev = EMPTY
        while flag['running']:
            if seconds is not None and time.time() - t0 >= seconds:
                break
            got = pad.reap()
            if got is None:
                now = time.time()
                if now - link.last_data > watchdog and now - last_reset > watchdog:
                    print('⚠ %.1f s 没有报文，重置 URB' % (now - link.last_data))
                    link.reset_urbs()
                    last_reset = now
                time.sleep(0.001)
                continue
            ctx, _status, data = got
            if ctx not in link.in_ids:
                continue
            link.in_ids.discard(ctx)
            link.pump(1)
            if not data:
                continue
            link.last_data = time.time()
            if len(data) < 32 or rep.is_reply(data):
                continue
            state = translate(data)
            if state is None:
                continue
            if state.extras != prev.extras and not dry_run:
                added = state.extras - prev.extras
                removed = prev.extras - state.extras
                if added:
                    print('%s [extra] +%s'
                          % (_fmt_elapsed(time.time() - t0),
                             ' +'.join(sorted(added))))
                if removed:
                    print('%s [extra] -%s'
                          % (_fmt_elapsed(time.time() - t0),
                             ' -'.join(sorted(removed))))
            if dry_run:
                line = describe(prev, state)
                if line:
                    print('%s %s' % (_fmt_elapsed(time.time() - t0), line))
            else:
                devs.pad.apply(state)
            prev = state
    except KeyboardInterrupt:
        print('\n已中断，正在收尾…')
    finally:
        if devs is not None:
            devs.close()
            print('虚拟设备已移除')
        if link is not None:
            for uid in list(link.in_ids):
                pad.discard(uid)
            link.in_ids.clear()
        try:
            pad.release()
        except OSError:
            pass
        pad.reconnect_driver()
        pad.close()
        print('xpad 已恢复')
    return 0


def load_capture(path):
    """读 `<double 时间戳><uint32 长度><数据>`，时间戳归一到 0。"""
    raw = open(path, 'rb').read()
    frames = []
    i = 0
    while i + 12 <= len(raw):
        ts, ln = struct.unpack_from('<dI', raw, i)
        i += 12
        frames.append((ts, raw[i:i + ln]))
        i += ln
    if not frames:
        raise SystemExit('回放文件里没有帧：%s' % path)
    t0 = frames[0][0]
    return [(ts - t0, d) for ts, d in frames]


def run_replay(cfg, path, dry_run=False, realtime=False, max_frames=None):
    """回放抓包，不需要 USB/root（dry-run 时连 uinput 都不建）。"""
    frames = load_capture(path)
    print('回放: %s（%d 帧%s）'
          % (path, len(frames), '，按原始节奏' if realtime else ''))
    devs = None
    if not dry_run:
        devs = Devices(cfg)
        print('虚拟设备:')
        _print_devices(devs)
    prev = EMPTY
    t0 = time.time()
    shown = 0
    try:
        for idx, (ts, data) in enumerate(frames):
            if max_frames is not None and idx >= max_frames:
                break
            if realtime:
                wait = t0 + ts - time.time()
                if wait > 0:
                    time.sleep(wait)
            if len(data) < 32 or rep.is_reply(data):
                continue
            state = translate(data)
            if state is None:
                continue
            shown += 1
            if dry_run:
                line = describe(prev, state)
                if line:
                    print('%s %s' % (_fmt_elapsed(ts), line))
            else:
                devs.pad.apply(state)
            prev = state
        if not dry_run:
            # 回放结束时把状态归零，避免留下"按住不放"的键。
            devs.pad.release_all()
    except KeyboardInterrupt:
        print('\n已中断，正在收尾…')
    finally:
        if devs is not None:
            devs.close()
            print('虚拟设备已移除')
    print('共处理 %d 帧' % shown)
    return 0
