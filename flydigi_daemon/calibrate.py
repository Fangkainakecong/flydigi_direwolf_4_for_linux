"""体感标定：静置 → 水平 360° → 俯仰 90°（`flydigi-daemon calibrate`）

产物 `config/calibration.json`（不进 git）保存：

* `yaw/pitch/roll.counts_per_degree`：积分多少 raw·秒等于 1 度；
* `sign`：传感器方向，映射时直接乘上去；
* `noise`：静置时的 RMS，用来提示和设死区；
* `update_rate_hz`：有效角速度更新率（实测约 89 Hz）。

积分用零阶保持：手柄报文是 1 kHz，角速度约 89 Hz 更新一次，两者之间的
重复值等价于保持上一拍，所以直接按每帧 `v * dt` 累加是正确的。

标定动作要慢、幅度准确；360° 那一圈如果转回来，积分会抵消，程序会报错
让你重来。
"""

import json
import os
import time
from dataclasses import dataclass

from . import config as cfgmod
from . import report as rep
from . import usb

YAW_DEGREES = 360.0
PITCH_DEGREES = 90.0


@dataclass
class Sample:
    t: float
    gyro: tuple


def rms(values):
    if not values:
        return 0.0
    return (sum(float(v) * float(v) for v in values) / len(values)) ** 0.5


def integrate(samples, axis, threshold=0.0):
    """零阶保持积分，返回 (raw·秒, 有效时长)。

    只累加 |v| ≥ threshold 的区间，避免静置漂移吃掉角度。
    """
    total = 0.0
    active = 0.0
    for a, b in zip(samples, samples[1:]):
        dt = b.t - a.t
        if dt <= 0 or dt > 0.25:
            continue
        value = a.gyro[axis]
        if abs(value) < threshold:
            continue
        total += value * dt
        active += dt
    return total, active


def total_angle(samples, threshold=0.0):
    """三轴各自积分，返回 [(raw·秒, 时长), ...]。"""
    return [integrate(samples, axis, threshold) for axis in range(3)]


def dominant_axis(samples, threshold=0.0):
    totals = total_angle(samples, threshold)
    index = max(range(3), key=lambda i: abs(totals[i][0]))
    return index, totals[index][0]


def scale_from(total, degrees):
    """raw·秒 → counts_per_degree 和符号。"""
    if not total:
        raise ValueError('积分为 0，没有检测到转动')
    return {
        'counts_per_degree': abs(total) / float(degrees),
        'sign': 1 if total > 0 else -1,
    }


def update_rate(samples):
    if len(samples) < 2:
        return 0.0
    duration = samples[-1].t - samples[0].t
    if duration <= 0:
        return 0.0
    changes = sum(1 for a, b in zip(samples, samples[1:])
                  if a.gyro != b.gyro)
    return changes / duration


def build_calibration(still, yaw_samples, pitch_samples, degrees=(YAW_DEGREES,
                                                                PITCH_DEGREES),
                      meta=None):
    """从三段采样算出 calibration dict；转得不够就抛 ValueError。"""
    noise = [rms([s.gyro[axis] for s in still]) for axis in range(3)]

    def threshold(axis):
        return max(1.5, noise[axis] * 3.0)

    def pick(samples, expected, degrees_total):
        index, total = dominant_axis(samples, threshold(expected))
        if index != expected:
            names = ('yaw/gyro_x', 'pitch/gyro_y', 'roll/gyro_z')
            raise ValueError('动作主要落在 %s，期望 %s；请按提示重做'
                             % (names[index], names[expected]))
        if abs(total) < max(20.0, threshold(expected) * 5):
            raise ValueError('检测到的转动太小（积分 %.1f），请重做' % total)
        return total, scale_from(total, degrees_total)

    yaw_total, yaw_scale = pick(yaw_samples, 0, degrees[0])
    pitch_total, pitch_scale = pick(pitch_samples, 1, degrees[1])

    cal = {
        'version': 1,
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'device': meta or {},
        'update_rate_hz': round(update_rate(yaw_samples), 1),
        'yaw': dict(yaw_scale, axis='gyro_x', noise=noise[0],
                    raw_total=round(yaw_total, 2)),
        'pitch': dict(pitch_scale, axis='gyro_y', noise=noise[1],
                      raw_total=round(pitch_total, 2)),
        # 同一颗传感器，roll 的灵敏度通常和 yaw 一致；只记录噪声。
        'roll': {'axis': 'gyro_z', 'sign': 1,
                 'counts_per_degree': yaw_scale['counts_per_degree'],
                 'noise': noise[2], 'note': '未单独标定，沿用 yaw 灵敏度'},
    }
    return cal


def record(link, pad, seconds):
    """收 N 秒输入报文，返回 [(monotonic, (gx,gy,gz)), ...]。"""
    samples = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = pad.reap()
        if got is None:
            time.sleep(0.001)
            continue
        ctx, _status, data = got
        if ctx not in link.in_ids:
            continue
        link.in_ids.discard(ctx)
        link.pump(1)
        if not data or len(data) < 32 or rep.is_reply(data):
            continue
        link.last_data = time.monotonic()
        samples.append(Sample(link.last_data, rep.decode(data)['gyro']))
    return samples


def _prompt(text):
    try:
        input(text)
    except EOFError:
        raise SystemExit('\n已取消')


def write_calibration(path, cal):
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)
    return os.path.abspath(path)


def run(cfg, args, calibration_path):
    print('体感标定：需要 root（要独占 USB 接口）')
    if os.geteuid() != 0:
        print('当前不是 root，直接读不到 USB；请用 sudo 运行。')
        return 1
    devcfg = cfgmod.device_section(cfg)
    dev = usb.find_pad(devcfg['vendor'], devcfg['product'])
    if not dev:
        print('未找到手柄（045e:028e）')
        return 1
    print('手柄: %s' % dev)

    pad = usb.Pad(dev)
    link = None
    try:
        print('解绑驱动: %s' % pad.takeover())
        link = usb.Link(pad)
        info = usb.identify_retry(link, tries=3)
        if info:
            print('识别: 固件=%s uid=%s' % (info['fw'], info['uid']))

        print('\n[1/3] 把手柄平放在桌面，不要碰。')
        _prompt('按 Enter 开始静置 %.0f 秒…' % args.still)
        link.drain(0.2)                       # 倒掉等待按键期间积压的报文
        still = record(link, pad, args.still)
        if len(still) < 50:
            print('只收到 %d 帧，检查连接后重试' % len(still))
            return 1
        noise = [rms([s.gyro[axis] for s in still]) for axis in range(3)]
        print('  噪声 RMS: yaw %.2f  pitch %.2f  roll %.2f'
              % tuple(noise))

        print('\n[2/3] 水平匀速转**整整一圈 360°**，转完停住，不要转回来。')
        _prompt('按 Enter 开始，然后 %.0f 秒内完成…' % args.yaw_seconds)
        link.drain(0.2)
        yaw = record(link, pad, args.yaw_seconds)
        print('  收到 %d 帧，角速度更新率约 %.1f Hz'
              % (len(yaw), update_rate(yaw)))

        print('\n[3/3] 从水平位置慢慢低头到大约 90°（手柄前端朝下），然后停住。')
        _prompt('按 Enter 开始，然后 %.0f 秒内完成…' % args.pitch_seconds)
        link.drain(0.2)
        pitch = record(link, pad, args.pitch_seconds)

        meta = {}
        if info:
            meta = {'vid': '045e', 'pid': '028e',
                    'uid': info['uid'], 'fw': info['fw']}
        try:
            cal = build_calibration(still, yaw, pitch, meta=meta)
        except ValueError as exc:
            print('\n✗ 标定失败：%s' % exc)
            print('  重新运行 calibrate，动作慢一点、幅度做足。')
            return 2

        path = write_calibration(args.output or calibration_path, cal)
        print('\n✓ 已写入 %s' % path)
        for name in ('yaw', 'pitch', 'roll'):
            section = cal[name]
            print('  %-5s %8.3f raw·s/°  sign=%+d  noise=%.2f'
                  % (name, section['counts_per_degree'],
                     section['sign'], section['noise']))
        print('\n桌面配置里 Home 切换体感组；改灵敏度用 config/user.toml：')
        print('  [profile.desktop.axis.gyro_x]\n  sensitivity = 1.0')
        return 0
    finally:
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
