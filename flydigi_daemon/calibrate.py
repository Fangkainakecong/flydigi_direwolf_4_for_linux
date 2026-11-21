"""体感标定：静置 → 水平多圈 → 俯仰 90°（`flydigi-daemon calibrate`）

官方 Space Station 的 840 字节配置里只有"体感映射/开关/死区/灵敏度"
（出厂 25/25、死区 8），**没有**陀螺仪的物理标定；上游 openflydigi 也明确
写了 Flydigi 的 LSB-per-deg/s 没有参考值、只能按手感调。所以 counts/degree
只能靠测：默认转 **3 整圈**，把起停误差摊到 1080° 上；yaw/pitch 的测量值
会互相校验，一致时按角度加权合并；差得太多（圈数没转够）就改用 pitch 的
尺度兜底，`--strict` 则直接失败、保留原文件。

也支持跳过动作、直接写常数（如果以后找到官方/数据表值）::

    calibrate --set-yaw 0.67 --set-pitch 0.67 --sign-pitch -1
    calibrate --estimate       # 用 2026-11-19 的 45° 抓包估算值，仅作起步

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
import shutil
import time
from dataclasses import dataclass

from . import config as cfgmod
from . import report as rep
from . import usb

YAW_DEGREES = 360.0
PITCH_DEGREES = 90.0
DEFAULT_TURNS = 3

# 2026-11-21 的 3 圈标定：yaw 0.654、pitch 0.687 raw·s/°，两者差 5%，
# 说明这个尺度自洽。旧的 45° 抓包不可靠（三轴积分几乎一样，动作里混了
# 滚转/俯仰），当时算出的 13.4 是错的。0.67 只适合当"不想标定"的起步值。
ESTIMATED_COUNTS_PER_DEGREE = 0.67


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


def build_calibration(still, yaw_samples, pitch_samples, turns=1,
                      degrees=(YAW_DEGREES, PITCH_DEGREES), meta=None,
                      strict=False):
    """从三段采样算出 calibration dict；转得不够就抛 ValueError。

    `turns` 是水平转的整圈数。yaw 和 pitch 各自算出 counts/degree 后，
    按角度加权合并成一个尺度（3 圈 1080° 对 90° 的权重是 12:1）。
    """
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

    yaw_total, yaw_scale = pick(yaw_samples, 0, turns * degrees[0])
    pitch_total, pitch_scale = pick(pitch_samples, 1, degrees[1])

    yaw_measured = yaw_scale['counts_per_degree']
    pitch_measured = pitch_scale['counts_per_degree']
    ratio = max(yaw_measured, pitch_measured) / max(
        1e-9, min(yaw_measured, pitch_measured))
    fallback = ratio > 1.35
    if fallback and strict:
        raise ValueError(
            'yaw/pitch 单独测量相差 %.0f%%（yaw %.3f / pitch %.3f）；'
            '通常是一次动作没做完整。请重做水平 3 圈（不要中途停顿）'
            '或低头 90°' % ((ratio - 1) * 100, yaw_measured, pitch_measured))

    yaw_degrees = turns * degrees[0]
    if fallback:
        # 圈数容易数错；低头 90° 只有一个终态，更好判断，用它兜底。
        pooled = pitch_measured
    else:
        pooled = (abs(yaw_total) + abs(pitch_total)) / (yaw_degrees +
                                                        degrees[1])

    cal = {
        'version': 1,
        'source': 'gesture-pitch' if fallback else 'gesture',
        'consistency': {'yaw': round(yaw_measured, 3),
                        'pitch': round(pitch_measured, 3),
                        'ratio': round(ratio, 2),
                        'fallback': fallback},
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'device': meta or {},
        'turns': turns,
        'update_rate_hz': round(update_rate(yaw_samples), 1),
        'yaw': dict(yaw_scale, axis='gyro_x', noise=noise[0],
                    counts_per_degree=pooled,
                    measured=round(yaw_scale['counts_per_degree'], 3),
                    raw_total=round(yaw_total, 2)),
        'pitch': dict(pitch_scale, axis='gyro_y', noise=noise[1],
                      counts_per_degree=pooled,
                      measured=round(pitch_scale['counts_per_degree'], 3),
                      raw_total=round(pitch_total, 2)),
        # 同一颗传感器，roll 的灵敏度通常和 yaw 一致；只记录噪声。
        'roll': {'axis': 'gyro_z', 'sign': 1,
                 'counts_per_degree': pooled,
                 'noise': noise[2], 'note': '未单独标定，沿用 yaw 灵敏度'},
    }
    return cal


def constant_calibration(yaw_cpd, pitch_cpd=None, sign_yaw=1, sign_pitch=1,
                         meta=None, source='constant', noise=(0.0, 0.0, 0.0)):
    """不测动作，直接写 counts/degree（官方值/数据表/估算值都走这里）。"""
    pitch_cpd = yaw_cpd if pitch_cpd is None else pitch_cpd
    return {
        'version': 1,
        'source': source,
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'device': meta or {},
        'update_rate_hz': 89.0,
        'yaw': {'axis': 'gyro_x', 'counts_per_degree': float(yaw_cpd),
                'sign': int(sign_yaw), 'noise': noise[0]},
        'pitch': {'axis': 'gyro_y', 'counts_per_degree': float(pitch_cpd),
                  'sign': int(sign_pitch), 'noise': noise[1]},
        'roll': {'axis': 'gyro_z', 'counts_per_degree': float(yaw_cpd),
                 'sign': 1, 'noise': noise[2],
                 'note': '未单独标定，沿用 yaw 灵敏度'},
    }


def record_motion(link, pad, max_seconds, threshold, settle=0.7):
    """从第一次检测到转动开始，直到静止 `settle` 秒（或超时）。"""
    samples = []
    started = False
    last_active = None
    end = time.monotonic() + max_seconds
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
        now = time.monotonic()
        link.last_data = now
        gyro = rep.decode(data)['gyro']
        samples.append(Sample(now, gyro))
        if max(abs(v) for v in gyro) >= threshold:
            started = True
            last_active = now
        elif started and now - last_active >= settle:
            break
    return samples


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
    # 覆盖前留一份 .bak：标定数据是纯手工得来的，删了就没了。
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path + '.bak')
        except OSError:
            pass
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)
    return os.path.abspath(path)


def run(cfg, args, calibration_path):
    output = args.output or calibration_path

    if args.estimate or args.set_yaw is not None:
        if args.set_yaw is not None:
            cal = constant_calibration(args.set_yaw, args.set_pitch,
                                       args.sign_yaw, args.sign_pitch,
                                       source='constant')
            note = '手动常数'
        else:
            cal = constant_calibration(ESTIMATED_COUNTS_PER_DEGREE,
                                       ESTIMATED_COUNTS_PER_DEGREE,
                                       args.sign_yaw, args.sign_pitch,
                                       source='estimate')
            note = ('2026-11-21 三次整圈标定，约 ±10%；'
                    '手感不对就重跑 calibrate')
        path = write_calibration(output, cal)
        print('✓ 已写入 %s（%s）' % (path, note))
        print('  yaw   %.3f raw·s/°  sign=%+d'
              % (cal['yaw']['counts_per_degree'], cal['yaw']['sign']))
        print('  pitch %.3f raw·s/°  sign=%+d'
              % (cal['pitch']['counts_per_degree'], cal['pitch']['sign']))
        return 0

    print('体感标定：需要 root（要独占 USB 接口）')
    if os.geteuid() != 0:
        print('当前不是 root，直接读不到 USB；请用 sudo 运行。')
        print('不想做动作可以先写估算值：')
        print('  python3 -m flydigi_daemon calibrate --estimate')
        return 1
    devcfg = cfgmod.device_section(cfg)
    dev = usb.find_pad(devcfg['vendor'], devcfg['product'])
    if not dev:
        print('未找到手柄（045e:028e）')
        return 1
    print('手柄: %s' % dev)

    turns = max(1, int(args.turns))
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
        threshold = max(1.0, max(noise) * 1.5)
        print('  噪声 RMS: yaw %.2f  pitch %.2f  roll %.2f（阈值 %.1f）'
              % (noise[0], noise[1], noise[2], threshold))

        total_degrees = turns * 360
        print('\n[2/3] 水平匀速转 **%d 整圈（共 %d°）**，转完停住，'
              '中途不要停顿。' % (turns, total_degrees))
        _prompt('按 Enter 开始，转完停住就会自动结束（最多 %.0f 秒）…'
                % args.yaw_seconds)
        link.drain(0.2)
        yaw = record_motion(link, pad, args.yaw_seconds, threshold)
        if len(yaw) < 50 or (yaw[-1].t - yaw[0].t) < 0.8:
            print('只记录到 %.1f 秒，动作可能没识别到；重试一次。'
                  % (yaw[-1].t - yaw[0].t if len(yaw) > 1 else 0))
            return 2
        yaw_duration = yaw[-1].t - yaw[0].t
        print('  记录 %.1f 秒，角速度更新率约 %.1f Hz'
              % (yaw_duration, update_rate(yaw)))
        if yaw_duration >= args.yaw_seconds - 0.3:
            print('  ⚠ 到达最长采样时间，可能还没转完；下次可以 --yaw-seconds 30 '
                  '或稍微转快一点。')

        print('\n[3/3] 从水平位置慢慢低头到大约 90°（手柄前端朝下），'
              '然后停住。')
        _prompt('按 Enter 开始，停住后自动结束（最多 %.0f 秒）…'
                % args.pitch_seconds)
        link.drain(0.2)
        pitch = record_motion(link, pad, args.pitch_seconds, threshold)
        if len(pitch) < 30:
            print('俯仰动作没记录到，重试一次。')
            return 2

        meta = {}
        if info:
            meta = {'vid': '045e', 'pid': '028e',
                    'uid': info['uid'], 'fw': info['fw']}
        try:
            cal = build_calibration(still, yaw, pitch, turns=turns, meta=meta,
                                    strict=args.strict)
        except ValueError as exc:
            print('\n✗ 标定失败：%s' % exc)
            print('  没有写入，原来的 calibration.json 保持不动。')
            return 2

        path = write_calibration(output, cal)
        yaw_m = cal['yaw']['measured']
        pitch_m = cal['pitch']['measured']
        ratio = max(yaw_m, pitch_m) / max(1e-9, min(yaw_m, pitch_m))
        print('\n✓ 已写入 %s' % path)
        print('  yaw    measured %.3f  sign=%+d  noise=%.2f'
              % (yaw_m, cal['yaw']['sign'], cal['yaw']['noise']))
        print('  pitch  measured %.3f  sign=%+d  noise=%.2f'
              % (pitch_m, cal['pitch']['sign'], cal['pitch']['noise']))
        print('  合并后 counts_per_degree = %.3f（按 %d° 与 90° 加权）'
              % (cal['yaw']['counts_per_degree'], total_degrees))
        if cal.get('consistency', {}).get('fallback'):
            print('⚠ yaw/pitch 单独测量相差 %.0f%%（yaw %.3f / pitch %.3f），'
                  '水平圈数可能没转够；' % ((ratio - 1) * 100, yaw_m, pitch_m))
            print('  已改用低头 90° 的尺度 %.3f。想重测水平：'
                  'sudo ... calibrate --turns 1' % cal['yaw']['counts_per_degree'])
        print('\n桌面配置里 Home 切换体感组；灵敏度用 config/user.toml 调：')
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
