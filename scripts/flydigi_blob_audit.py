#!/usr/bin/env python3
"""冰原狼4 板载配置审计：读 4 个槽位，判断哪个槽是"干净默认"

为什么需要
----------
daemon 独占 dongle 后，标准手柄输入由我们自己转发；但 M1/M2 是否还能
出现在 `[19]` 位图里，取决于**板载配置**。如果某个槽位把 M1/M2 重映射成
了键盘（目标 254），固件就不会再报这两个键，daemon 也无从得知。

所以启动时必须审计当前槽位：只接受"全默认"的槽，否则警告用户去 Windows
空间站把槽位恢复成默认。

产出
----
* 终端表格：每个槽位是否存在 / data_version / 标题 / 判定 / 改动列表
* JSON：`data/txt/blob_audit_<时间戳>.json`（文本，可进仓库）

用法
----
    sudo python3 scripts/flydigi_blob_audit.py
    sudo python3 scripts/flydigi_blob_audit.py --slot 0     # 只审一个
    sudo python3 scripts/flydigi_blob_audit.py --no-save
"""

import argparse
import json
import os
import struct
import sys
import time

from flydigi_report import KEY_NAMES
from flydigi_usb import Pad, Link, find_pad, identify, read_slot, versions

OFF_KEYS = 13
NKEYS = 32
OFF_MOTION = 137
OFF_VERSION = 225
OFF_TITLE = 770
TITLE_LEN = 20
OFF_TRIGGER_MOTORS = 154
TRIGGER_MOTORS_LEN = 30

TARGET_SELF = 255
TARGET_KEYBOARD = 254
TARGET_MACRO = 32

MOTION_TARGETS = {0: 'off', 1: 'left_stick', 2: 'right_stick', 3: 'mouse'}
MOTION_ENABLE = {0: 'click', 1: 'hold'}
MOTION_USE = {0: 'fps', 1: 'racer'}


def key_name(k):
    return KEY_NAMES.get(k, 'id%d' % k)


def title_of(blob):
    raw = blob[OFF_TITLE:OFF_TITLE + TITLE_LEN]
    return raw.decode('utf-16-le', 'replace').rstrip('\uffff\x00')


def target_name(target):
    if target == TARGET_SELF:
        return 'default(自身)'
    if target == TARGET_KEYBOARD:
        return 'keyboard(死键)'
    if target == TARGET_MACRO:
        return 'macro'
    return key_name(target)


def analyze(blob):
    """把一个 840 字节 blob 解成结构化信息。"""
    if blob is None:
        return {'present': False}

    remaps = []
    for k in range(NKEYS):
        off = OFF_KEYS + k * 3
        target, mode, freq = blob[off], blob[off + 1], blob[off + 2]
        if target == TARGET_SELF and freq == 0:
            continue
        remaps.append({
            'key': key_name(k),
            'target': target,
            'target_name': target_name(target),
            'mode': mode,
            'freq': freq,
            'turbo': bool(freq),
        })

    m = blob[OFF_MOTION:OFF_MOTION + 8]
    motion = {
        'target': m[0],
        'target_name': MOTION_TARGETS.get(m[0], 'unknown(%d)' % m[0]),
        'key1': key_name(m[1]) if m[1] not in (255,) else None,
        'enable_type': m[2],
        'enable_name': MOTION_ENABLE.get(m[2], 'unknown'),
        'dead_zone': m[3],
        'sensitivity_xy': (m[4], m[5]),
        'use_mode': m[6],
        'use_mode_name': MOTION_USE.get(m[6], 'unknown'),
        'key2': key_name(m[7]) if m[7] not in (255,) else None,
    }

    keyboard = [r for r in remaps if r['target'] == TARGET_KEYBOARD]
    macros = [r for r in remaps if r['target'] == TARGET_MACRO]
    turbo = [r for r in remaps if r['turbo']]
    trigger_motors = blob[OFF_TRIGGER_MOTORS:OFF_TRIGGER_MOTORS + TRIGGER_MOTORS_LEN]

    # 只有"键位全默认 + 体感映射关闭"才算干净。
    clean = not remaps and motion['target'] == 0

    warnings = []
    if keyboard:
        warnings.append(
            '有 %d 个键映射到键盘(254)，Linux 下是死键：%s'
            % (len(keyboard), ', '.join(r['key'] for r in keyboard)))
    if macros:
        warnings.append('有 %d 个宏：%s' % (len(macros), ', '.join(r['key'] for r in macros)))
    if motion['target'] != 0:
        warnings.append(
            '板载体感映射开启（%s），会降低轮询率；主机侧体感应改为 off'
            % motion['target_name'])

    return {
        'present': True,
        'length': len(blob),
        'data_version': struct.unpack_from('<H', blob, OFF_VERSION)[0],
        'title': title_of(blob),
        'clean': clean,
        'remaps': remaps,
        'keyboard_keys': [r['key'] for r in keyboard],
        'macros': [r['key'] for r in macros],
        'turbo_keys': [r['key'] for r in turbo],
        'motion': motion,
        'trigger_motors_zero': not any(trigger_motors),
        'warnings': warnings,
    }


def main():
    ap = argparse.ArgumentParser(description='审计 4 个板载配置槽位')
    ap.add_argument('--slot', type=int, action='append',
                    help='只读指定槽位（可重复）')
    ap.add_argument('--json', default=None, help='JSON 输出路径')
    ap.add_argument('--no-save', action='store_true', help='不写 JSON')
    args = ap.parse_args()

    dev = find_pad()
    if not dev:
        sys.exit('未找到手柄 (045e:028e)。确认 2.4G 已连接、开关在 2.4G 档。')

    pad = Pad(dev)
    claimed = False
    result = {'device': dev, 'time': time.strftime('%Y-%m-%dT%H:%M:%S')}
    print('手柄: %s' % dev)
    try:
        method = pad.takeover()
        print('解绑驱动: %s' % method)
        claimed = True
        link = Link(pad)
        link.drain(0.3)

        info = identify(link)
        if not info:
            sys.exit('cmd16 识别无应答（老方言通道不通）')
        result['identify'] = {k: v for k, v in info.items() if k != 'raw'}
        print('识别: DeviceType=%s uid=%s 固件=%s 电量=%s 连接=%s 当前槽位=%s'
              % (info['device_type'], info['uid'], info['fw'], info['battery'],
                 '有线' if info['connect'] == 1 else '2.4G', info['slot']))

        ver = versions(link)
        if ver:
            ids, raw = ver
            result['data_versions'] = ids
            print('配置版本: ' + '  '.join('槽%d=%d(0x%04x)' % (i, v, v)
                                           for i, v in enumerate(ids)))
        else:
            print('配置版本: 无应答（继续）')

        slots = args.slot if args.slot else [0, 1, 2, 3]
        report = {}
        for slot in slots:
            blob, packets = read_slot(link, slot)
            info_slot = analyze(blob)
            info_slot['packets'] = packets
            report[slot] = info_slot
        result['slots'] = report
        result['current_slot'] = info['slot']
        cur = report.get(info['slot'])
        result['current_slot_clean'] = bool(cur and cur.get('clean'))
    finally:
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        pad.reconnect_driver()
        pad.close()
        print('xpad 已恢复')

    print('\n=== 槽位审计 ===')
    for slot in sorted(report):
        s = report[slot]
        if not s['present']:
            print('槽位%d: 空（%d 包）' % (slot, s['packets']))
            continue
        print('槽位%d: %s  %s  data_version=%d  标题=%r'
              % (slot, '干净' if s['clean'] else '**非默认**',
                 '6轴/震动' if s['trigger_motors_zero'] else '含扳机马达配置',
                 s['data_version'], s['title']))
        if s['remaps']:
            items = []
            for r in s['remaps'][:12]:
                t = r['target_name']
                if r['turbo']:
                    t += '(turbo %dHz)' % r['freq']
                items.append('%s→%s' % (r['key'], t))
            more = '' if len(s['remaps']) <= 12 else ' …另 %d 项' % (len(s['remaps']) - 12)
            print('  改动: ' + '  '.join(items) + more)
        if s['motion']['target'] != 0:
            print('  体感: %s, 开启键=%s/%s, 灵敏度=%s'
                  % (s['motion']['target_name'], s['motion']['key1'],
                     s['motion']['key2'], s['motion']['sensitivity_xy']))
        for w in s['warnings']:
            print('  ⚠ ' + w)

    print('\n=== 结论 ===')
    if result['current_slot_clean']:
        print('当前槽位 %d 是干净默认：M1/M2/FN/Turbo 会出现在位图里，daemon 可以接管。'
              % result['current_slot'])
    else:
        print('当前槽位 %d 不是干净默认：先用 Windows 空间站把它恢复成默认，'
              '否则 M1/M2 可能被固件吞掉。' % result['current_slot'])
    clean_slots = [s for s in sorted(report) if report[s]['present'] and report[s]['clean']]
    print('干净槽位: %s' % (clean_slots or '无'))

    if not args.no_save:
        os.makedirs('data/txt', exist_ok=True)
        path = args.json or ('data/txt/blob_audit_%s.json'
                             % time.strftime('%Y%m%d-%H%M%S'))
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print('\n已写 %s' % path)
        if path.startswith('data/txt/'):
            print('记得把它加进 data/MANIFEST.md 并提交。')


if __name__ == '__main__':
    main()
