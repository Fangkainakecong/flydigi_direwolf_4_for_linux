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

# sudo 运行会以 root 身份写 __pycache__，之后普通用户既删不掉也没法重新编译。
sys.dont_write_bytecode = True

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

    # 出厂默认的扳机马达块本来就不是全零（`reference/openflydigi/mapping.py`:
    # 154..183 = 1 个使能字节 + 两侧各 14 字节 min/max/scale），所以"全零"
    # 不是判定依据，只看使能位。ENABLED = 0，DISABLED = 0xFF（反逻辑）。
    trigger_motor_enabled = trigger_motors[0] == 0

    # 严格意义的"干净"：键位全默认 + 体感关闭。只用于对照出厂状态。
    clean = not remaps and motion['target'] == 0

    # daemon 唯一在意的判定：有没有物理键被固件吃掉。
    # 固件把某个键映射到键盘(254)或宏(32) 之后，就不再在标准按钮字/位图里
    # 报这个键，daemon 收不到它 —— 这才是真正的阻塞项。
    consumed = keyboard + macros
    daemon_ok = not consumed
    blockers = ['%s→%s：固件吃掉此键，daemon 收不到'
                % (r['key'], r['target_name']) for r in consumed]

    notes = []
    if turbo:
        notes.append('%d 个键是 turbo（固件自动连发）：%s'
                     % (len(turbo), ', '.join(r['key'] for r in turbo)))
    if motion['target'] != 0:
        notes.append('板载体感映射开启（%s）：XInput 模式下没有鼠标通道，'
                     '一般惰性，但建议在空间站关掉' % motion['target_name'])

    return {
        'present': True,
        'length': len(blob),
        'data_version': struct.unpack_from('<H', blob, OFF_VERSION)[0],
        'title': title_of(blob),
        'clean': clean,
        'daemon_ok': daemon_ok,
        'blockers': blockers,
        'notes': notes,
        'remaps': remaps,
        'keyboard_keys': [r['key'] for r in keyboard],
        'macros': [r['key'] for r in macros],
        'turbo_keys': [r['key'] for r in turbo],
        'motion': motion,
        'trigger_motor_enabled': trigger_motor_enabled,
    }


def normalize(s):
    """兼容旧版 JSON：缺 daemon_ok/blockers/notes 时按 remaps 现算。"""
    if not s.get('present'):
        return s
    remaps = s.get('remaps') or []
    if 'daemon_ok' not in s:
        consumed = [r for r in remaps if r['target'] in (TARGET_KEYBOARD, TARGET_MACRO)]
        s['daemon_ok'] = not consumed
        s['blockers'] = ['%s→%s：固件吃掉此键，daemon 收不到'
                         % (r['key'], r['target_name']) for r in consumed]
    if 'notes' not in s:
        notes = []
        turbo = [r for r in remaps if r.get('turbo')]
        if turbo:
            notes.append('%d 个键是 turbo（固件自动连发）：%s'
                         % (len(turbo), ', '.join(r['key'] for r in turbo)))
        motion = s.get('motion') or {}
        if motion.get('target'):
            notes.append('板载体感映射开启（%s）：XInput 模式下没有鼠标通道，'
                         '一般惰性，但建议在空间站关掉' % motion.get('target_name'))
        s['notes'] = notes
    if 'clean' not in s:
        s['clean'] = False
    return s


def print_audit(result, report):
    print('\n=== 槽位审计 ===')
    for slot in sorted(report):
        s = normalize(report[slot])
        if not s['present']:
            print('槽位%d: 空（%d 包）' % (slot, s.get('packets', 0)))
            continue
        verdict = '可接管' if s.get('daemon_ok') else '**被固件占用**'
        extra = '出厂默认' if s.get('clean') else '已改过'
        print('槽位%d: %s（%s）  data_version=%d  标题=%r'
              % (slot, verdict, extra, s['data_version'], s['title']))
        if s['remaps']:
            items = []
            for r in s['remaps'][:12]:
                t = r['target_name']
                if r.get('turbo'):
                    t += '(turbo %dHz)' % r['freq']
                items.append('%s→%s' % (r['key'], t))
            more = '' if len(s['remaps']) <= 12 else ' …另 %d 项' % (len(s['remaps']) - 12)
            print('  改动: ' + '  '.join(items) + more)
        motion = s.get('motion') or {}
        if motion.get('target'):
            print('  体感: %s, 开启键=%s/%s, 灵敏度=%s'
                  % (motion.get('target_name'), motion.get('key1'),
                     motion.get('key2'), motion.get('sensitivity_xy')))
        for b in s.get('blockers', [])[:8]:
            print('  ✗ ' + b)
        if len(s.get('blockers', [])) > 8:
            print('  ✗ …另 %d 个' % (len(s['blockers']) - 8))
        for n in s.get('notes', []):
            print('  · ' + n)
        if s.get('trigger_motor_enabled'):
            print('  · 扳机马达已启用（不影响 daemon）')

    print('\n=== 结论 ===')
    cur = report.get(result.get('current_slot'))
    if cur and cur.get('daemon_ok'):
        print('当前槽位 %s 可以接管：没有键被固件吃掉，M1/M2/FN/Turbo 都会出现在位图里。'
              % result.get('current_slot'))
        if cur.get('notes'):
            print('  不影响接管，只是要知道：')
            for n in cur['notes']:
                print('   · ' + n)
    elif cur:
        print('当前槽位 %s 有键被固件吃掉（见上面的 ✗），daemon 收不到这些物理键。'
              % result.get('current_slot'))
        print('  处理办法：在 Windows 空间站把这些键改回"默认"，或换到下面的推荐槽。')
    ok_slots = [slot for slot in sorted(report)
                if report[slot]['present'] and report[slot].get('daemon_ok')]
    print('可接管槽位: %s' % (ok_slots or '无'))
    # 排序偏好（越左越好）：① 没有任何重映射 —— 重映射会让物理键改报目标键，
    # daemon 就分不清按的是 M1 还是 R3；② 板载体感关闭；③ 没有 turbo。
    def rank(slot):
        s = report[slot]
        return (len(s.get('remaps') or []),
                1 if (s.get('motion') or {}).get('target', 0) else 0,
                1 if s.get('turbo_keys') else 0)
    best = sorted(ok_slots, key=rank)
    if best:
        s = report[best[0]]
        why = []
        if s.get('remaps'):
            why.append('有 %d 处重映射' % len(s['remaps']))
        else:
            why.append('无重映射，M1/M2 原样上报')
        if (s.get('motion') or {}).get('target', 0):
            why.append('板载体感开着（建议关）')
        print('推荐槽位: %s（%s）' % (best[0], '，'.join(why)))
        if best[0] != result.get('current_slot'):
            print('  当前在槽位 %s，用 FN+方向键切到槽位 %s 即可。'
                  % (result.get('current_slot'), best[0]))
    else:
        print('推荐槽位: 无')


def main():
    ap = argparse.ArgumentParser(description='审计 4 个板载配置槽位')
    ap.add_argument('--slot', type=int, action='append',
                    help='只读指定槽位（可重复）')
    ap.add_argument('--json', default=None, help='JSON 输出路径')
    ap.add_argument('--no-save', action='store_true', help='不写 JSON')
    ap.add_argument('--from', dest='from_json', default=None,
                    help='离线重读之前保存的 JSON（不连硬件）')
    args = ap.parse_args()

    if args.from_json:
        with open(args.from_json, encoding='utf-8') as f:
            result = json.load(f)
        report = {int(k): v for k, v in result['slots'].items()}
        result.setdefault('current_slot',
                          (result.get('identify') or {}).get('slot'))
        print('离线重读 %s（当前槽位 %s）'
              % (args.from_json, result['current_slot']))
        print_audit(result, report)
        return

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
        result['current_slot_daemon_ok'] = bool(cur and cur.get('daemon_ok'))
    finally:
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        pad.reconnect_driver()
        pad.close()
        print('xpad 已恢复')

    print_audit(result, report)

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
