"""M1：独占 dongle，把标准手柄输入透明转发到虚拟 Xbox 手柄

不做映射、不做体感、不做宏、不做震动。目标只有一个：让游戏看到的手柄
和原厂 xpad 一样，同时 daemon 能拿到 M1/M2/FN/Turbo（M1 只记日志）。

两种输入源：

* 实时：`Pad.takeover()` → usbfs 异步 URB → 每帧 `translate()`
* 回放：`--replay <capture.bin>`，不需要手柄，适合离线回归/调试
"""

import atexit
import os
import signal
import struct
import time

from . import config as cfgmod
from . import mapping
from . import report as rep
from . import status as statusmod
from . import usb
from .devices import Devices, device_node, scan_event_nodes
from .rumble import Rumble
from .slots import SlotController
from .translate import describe, significant_change, translate


def _fmt_elapsed(seconds):
    return '%7.2fs' % seconds


def _install_stop_handler(flag):
    def handler(signum, frame):        # noqa: ARG001
        flag['running'] = False
    signals = [signal.SIGINT, signal.SIGTERM]
    for name in ('SIGHUP', 'SIGQUIT'):
        if hasattr(signal, name):
            signals.append(getattr(signal, name))
    for sig in signals:
        signal.signal(sig, handler)


def _print_devices(devs):
    nodes = scan_event_nodes({devs.pad.name, devs.keyboard.name,
                              devs.mouse.name})
    for role, dev in (('手柄', devs.pad), ('键盘', devs.keyboard),
                      ('鼠标', devs.mouse)):
        node = device_node(dev) or ' '.join(nodes.get(dev.name, ['(未找到节点)']))
        print('  %-3s %-18s %s' % (role, node, dev.name))


def _load_mapper(cfg, profiles, name, devices, calibration_path=None):
    """建 Mapper 并加载体感标定；只在一处打印状态。"""
    uses_gyro = any(axis.startswith('gyro')
                    for profile in profiles.values()
                    for axis in profile.axes)
    path = calibration_path or cfgmod.calibration_file(cfg)
    cal = mapping.load_calibration(path) if uses_gyro else None
    if uses_gyro:
        if cal:
            print('体感标定: %s（更新率 %s Hz）'
                  % (path, cal.get('update_rate_hz', '?')))
        else:
            print('体感: 未标定（%s 不存在，先运行 calibrate）' % path)
    return mapping.Mapper(profiles[name], devices=devices, calibration=cal)


def _make_slots(cfg, persist=True, on_switch=None):
    d = cfgmod.daemon_section(cfg)
    profiles = cfgmod.slot_profiles(cfg) or ['default']
    return SlotController(
        profiles,
        chord=d.get('chord_button', 'back'),
        window_ms=int(d.get('chord_window_ms', 200)),
        suppress=bool(d.get('suppress_chord', True)),
        state_path=cfgmod.state_file(cfg),
        startup_profile=d.get('startup_profile'),
        persist=persist,
        on_switch=on_switch,
    )


def _take_requested_profile(cfg):
    """托盘切换配置时写的小文件；读一次就删，避免下次开机误用。"""
    path = cfgmod.request_file(cfg)
    try:
        with open(path, encoding='utf-8') as f:
            name = f.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        os.unlink(path)
    except OSError:
        pass
    return name or None


def run_live(cfg, dry_run=False, seconds=None, profile=None,
             calibration=None):
    devcfg = cfgmod.device_section(cfg)
    dev = usb.find_pad(devcfg['vendor'], devcfg['product'],
                       devcfg.get('product_hint'))
    if not dev:
        print(usb.no_pad_message(devcfg['vendor'], devcfg['product'],
                                 devcfg.get('product_hint')))
        print('确认 dongle 插好、手柄已开机；插着别的手柄时也不会误接管。')
        # 没有目标设备属于正常情况（例如开机时 dongle 还没插）。返回 0 让
        # systemd 标成 inactive 而不是 failed；插上后 udev 规则会再拉起。
        return 0

    print('飞智 daemon（M3 映射）' + ('（dry-run，不建虚拟设备）' if dry_run else ''))
    print('手柄: %s' % dev)
    pad = usb.Pad(dev, interface=devcfg['interface'])
    link = None
    devs = None
    rumble = None
    try:
        print('解绑驱动: %s' % pad.takeover())
        link = usb.Link(pad)
        info = usb.identify_retry(link, tries=3)
        last_info = {}
        if info:
            print('识别: 固件=%s uid=%s 当前槽位=%d'
                  % (info['fw'], info['uid'], info['slot']))
            last_info.update(info)
            statusmod.write(cfg, last_info)
        else:
            print('⚠ cmd16 无应答（不影响转发，但读不到槽位）')

        if not dry_run:
            rcfg = cfg.get('rumble', {}) or {}
            with_ff = bool(rcfg.get('enabled', True))
            devs = Devices(cfg, with_ff=with_ff)
            atexit.register(devs.close)
            if with_ff:
                rumble = Rumble(devs.pad.ui, pad, rcfg)
                atexit.register(rumble.close)
            print('虚拟设备:')
            _print_devices(devs)
            print('提示：物理手柄已由 daemon 独占；Steam/RetroArch 用虚拟手柄。')

        t0 = time.time()
        profiles = mapping.parse_config(cfg)
        requested = _take_requested_profile(cfg)
        if requested and requested not in profiles:
            print('⚠ 请求的配置 %r 不存在，忽略（可用：%s）'
                  % (requested, ', '.join(sorted(profiles))))
            requested = None
        ctrl = _make_slots(cfg)
        if requested and not profile:
            ctrl.select(requested)
        if ctrl.profile not in profiles:
            print('✗ state.json 里的 profile %r 不在配置里' % (ctrl.profile,))
            return 1
        start_profile = profile or requested or ctrl.profile
        if start_profile not in profiles:
            print('✗ profile %r 不在配置里（可用：%s）'
                  % (start_profile, ', '.join(sorted(profiles))))
            return 1
        mapper = _load_mapper(cfg, profiles, start_profile,
                              None if dry_run else devs, calibration)
        if mapper is None:
            return 1

        feedback = (cfg.get('feedback', {}) or {}).get('profile_switch',
                                                        'short')

        def profile_slot_of(name):
            return (ctrl.profiles.index(name) + 1
                    if name in ctrl.profiles else None)

        def on_switch(index, name, letter):
            if devs is not None:
                devs.release_all()
            mapper.set_profile(profiles[name])
            statusmod.write(cfg, last_info, profile=name,
                            profile_slot=index + 1)
            print('%s 配置 → %d/%d %s（SELECT+%s）'
                  % (_fmt_elapsed(time.time() - t0), index + 1,
                     len(ctrl.profiles), name, letter.upper()))
            if rumble is not None and feedback != 'none':
                rumble.pulse(255, 200, 250 if feedback == 'short' else 320)

        ctrl.on_switch = on_switch
        statusmod.write(cfg, last_info, profile=mapper.profile.name,
                        profile_slot=profile_slot_of(mapper.profile.name))
        print('当前配置: %s（槽 %d/%d）'
              % (start_profile, ctrl.slot + 1, len(ctrl.profiles)))

        safety = cfgmod.safety_section(cfg)
        watchdog = float(safety.get('watchdog_ms', 1500)) / 1000.0
        release_after = float(safety.get('input_timeout_ms', 400)) / 1000.0
        daemon_cfg = cfgmod.daemon_section(cfg)
        idle_exit = float(daemon_cfg.get('idle_exit_s', 3600) or 0)
        battery_every = float(daemon_cfg.get('battery_refresh_s', 15) or 0)
        battery_pending = False
        battery_sent = 0.0
        last_battery = time.time()
        flag = {'running': True}
        _install_stop_handler(flag)
        print('开始转发（Ctrl-C 退出）…')
        last_reset = 0.0
        gap_released = False
        prev = mapper.out
        prev_extras = frozenset()
        prev_raw = None
        last_activity = time.time()
        next_pause_check = 0.0
        statusmod.clear_control()
        while flag['running']:
            now = time.time()
            if now >= next_pause_check:
                next_pause_check = now + 0.2
                if os.path.exists(statusmod.PAUSE_FILE):
                    try:
                        os.unlink(statusmod.PAUSE_FILE)
                    except OSError:
                        pass
                    if devs is not None:
                        devs.release_all()
                    if rumble is not None:
                        rumble.stop()
                    mapper.reset()
                    prev = mapper.out
                    statusmod.write(
                        cfg, last_info, profile=mapper.profile.name,
                        profile_slot=profile_slot_of(mapper.profile.name),
                        paused=True)
                    print('映射已挂起：进程保留，等待恢复')
                    if link is not None:
                        for uid in list(link.in_ids):
                            pad.discard(uid)
                        link.in_ids.clear()
                    while (flag['running']
                           and not os.path.exists(statusmod.RESUME_FILE)):
                        time.sleep(0.2)
                    try:
                        os.unlink(statusmod.RESUME_FILE)
                    except OSError:
                        pass
                    if not flag['running']:
                        break
                    if link is not None:
                        link.reset_urbs()
                    statusmod.write(
                        cfg, last_info, profile=mapper.profile.name,
                        profile_slot=profile_slot_of(mapper.profile.name),
                        paused=False)
                    print('映射已恢复')
                    last_activity = time.time()
                    gap_released = False
                    last_battery = 0.0
                    battery_pending = False
                    next_pause_check = time.time() + 0.2
                    continue
            if rumble is not None:
                rumble.poll(now)
            if (battery_every > 0 and not battery_pending
                    and now - last_battery >= battery_every):
                link.send_async(usb.legacy(usb.CMD_IDENTIFY))
                battery_pending = True
                battery_sent = now
            elif battery_pending and now - battery_sent > 2.0:
                battery_pending = False
                last_battery = now
            if seconds is not None and now - t0 >= seconds:
                break
            if idle_exit > 0 and now - last_activity >= idle_exit:
                print('%.0f 分钟没有操作，daemon 退出'
                      % (idle_exit / 60.0))
                break
            got = pad.reap()
            if got is None:
                now = time.time()
                # 手柄休眠/2.4G 掉线时，虚拟键盘/鼠标不能保持按下状态。
                if not gap_released and now - link.last_data > release_after:
                    if devs is not None:
                        devs.release_all()
                    if rumble is not None:
                        rumble.stop()
                    mapper.reset()
                    prev = mapper.out
                    gap_released = True
                    print('⚠ %.1f s 没有输入，已释放所有按键/鼠标'
                          % (now - link.last_data))
                flushed = ctrl.tick(now)
                if flushed is not None:
                    frame = mapper.apply(flushed, now)
                    for name, value in frame.toggled:
                        print('%s [group] %s=%s'
                              % (_fmt_elapsed(now - t0), name,
                                 'on' if value else 'off'))
                    if dry_run:
                        line = mapping.describe(prev, frame)
                        if line:
                            print('%s %s' % (_fmt_elapsed(now - t0), line))
                    prev = frame
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
            if gap_released:
                gap_released = False
                last_battery = 0.0
                battery_pending = False
                print('输入恢复')
            if len(data) < 32:
                continue
            if rep.is_reply(data):
                info = usb.info_from_reply(data)
                if info:
                    last_info.update(info)
                    statusmod.write(
                        cfg, last_info, profile=mapper.profile.name,
                        profile_slot=profile_slot_of(mapper.profile.name))
                    last_battery = time.time()
                    battery_pending = False
                continue
            state = translate(data)
            if state is None:
                continue
            if significant_change(prev_raw, state):
                last_activity = time.time()
            prev_raw = state
            if state.extras != prev_extras and not dry_run:
                added = state.extras - prev_extras
                removed = prev_extras - state.extras
                if added:
                    print('%s [extra] +%s'
                          % (_fmt_elapsed(time.time() - t0),
                             ' +'.join(sorted(added))))
                if removed:
                    print('%s [extra] -%s'
                          % (_fmt_elapsed(time.time() - t0),
                             ' -'.join(sorted(removed))))
            prev_extras = state.extras
            out = ctrl.feed(state, time.time())
            frame = mapper.apply(out, time.time())
            for name, value in frame.toggled:
                print('%s [group] %s=%s'
                      % (_fmt_elapsed(time.time() - t0), name,
                         'on' if value else 'off'))
            if dry_run:
                line = mapping.describe(prev, frame)
                if line:
                    print('%s %s' % (_fmt_elapsed(time.time() - t0), line))
            prev = frame
    except KeyboardInterrupt:
        print('\n已中断，正在收尾…')
    finally:
        if rumble is not None:
            rumble.close()
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
        statusmod.clear_control()
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


def run_replay(cfg, path, dry_run=False, realtime=False,
               max_frames=None, profile=None, calibration=None):
    """回放抓包，不需要 USB/root（dry-run 时连 uinput 都不建）。"""
    frames = load_capture(path)
    print('回放: %s（%d 帧%s）'
          % (path, len(frames), '，按原始节奏' if realtime else ''))
    devs = None
    rumble = None
    if not dry_run:
        devs = Devices(cfg)
        print('虚拟设备:')
        _print_devices(devs)
    now_ts = [0.0]
    profiles = mapping.parse_config(cfg)
    # 回放绝不能改真实的状态文件，否则按一次 SELECT+A 就把用户的槽位改了。
    ctrl = _make_slots(cfg, persist=False)
    if ctrl.profile not in profiles:
        raise SystemExit('state 里的 profile %r 不在配置里' % (ctrl.profile,))
    start_profile = profile or ctrl.profile
    if start_profile not in profiles:
        raise SystemExit('profile %r 不在配置里（可用：%s）'
                         % (start_profile, ', '.join(sorted(profiles))))
    mapper = _load_mapper(cfg, profiles, start_profile,
                          None if dry_run else devs, calibration)
    if mapper is None:
        raise SystemExit('体感标定文件读取失败')

    def on_switch(index, name, letter):
        if devs is not None:
            devs.release_all()
        mapper.set_profile(profiles[name])
        print('%s 配置 → %d/%d %s（SELECT+%s）'
              % (_fmt_elapsed(now_ts[0]), index + 1,
                 len(ctrl.profiles), name, letter.upper()))

    ctrl.on_switch = on_switch
    print('起始配置: %s（槽 %d/%d）'
          % (start_profile, ctrl.slot + 1, len(ctrl.profiles)))
    prev = mapper.out
    t0 = time.time()
    shown = 0
    try:
        for idx, (ts, data) in enumerate(frames):
            if max_frames is not None and idx >= max_frames:
                break
            now_ts[0] = ts
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
            out = ctrl.feed(state, ts)
            frame = mapper.apply(out, ts)
            for name, value in frame.toggled:
                print('%s [group] %s=%s'
                      % (_fmt_elapsed(ts), name, 'on' if value else 'off'))
            if dry_run:
                line = mapping.describe(prev, frame)
                if line:
                    print('%s %s' % (_fmt_elapsed(ts), line))
            prev = frame
        if not dry_run:
            # 回放结束时把状态归零，避免留下"按住不放"的键。
            devs.pad.release_all()
    except KeyboardInterrupt:
        print('\n已中断，正在收尾…')
    finally:
        if rumble is not None:
            rumble.close()
        if devs is not None:
            devs.close()
            print('虚拟设备已移除')
    print('共处理 %d 帧' % shown)
    return 0
