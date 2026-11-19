#!/usr/bin/env python3
"""厂商出厂配置 → 840 字节 blob，并和板载槽逐字节对比

为什么需要
----------
`Configs/Controller/fp4/default/default_mapping_132.dat` 是空间站自带的出厂
配置（DeviceType 132 = Direwolf 4）。它是 protobuf 的
`ControllerMappingConfigBeans`，装四个槽位。翻译成手柄真正收到的 840 字节
以后，可以做三件事：

1. 和我们从手柄读回来的 blob 逐字节比，**精确**知道改过哪些字段；
2. 给出"恢复出厂"的正确目标字节（daemon 不写，但审计和文档要用）；
3. 校验我们对键位表、体感块、扳机马达块的解码是否正确。

翻译器是上游 openflydigi 的 `tools/mapping_bean.py`，已原样放到
`reference/openflydigi/mapping_bean.py`（GPL-3.0，保留 SPDX 头）。

用法
----
    # 默认读 windows-dump/Controller/fp4/default/default_mapping_132.dat
    python3 scripts/flydigi_bean_translate.py

    # 指定文件
    python3 scripts/flydigi_bean_translate.py --dat windows-dump/.../default_mapping_146.dat

    # 和手柄读回来的原始 blob 对比（blob_audit.py --dump 生成）
    python3 scripts/flydigi_bean_translate.py --diff captures/blob_xxx_slot3.bin

产物
----
`data/txt/factory_fp4_<时间戳>.txt`：四个槽位的结构摘要 + 840 字节十六进制。
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'reference', 'openflydigi'))

import mapping_bean  # noqa: E402

BLOB_LEN = 840
NKEYS = 32

KEY_NAMES = {
    0: 'up', 1: 'right', 2: 'down', 3: 'left', 4: 'a', 5: 'b', 6: 'back',
    7: 'x', 8: 'y', 9: 'start', 10: 'lb', 11: 'rb', 12: 'lt', 13: 'rt',
    14: 'l3', 15: 'r3', 16: 'c', 17: 'z', 18: 'm1', 19: 'm2', 20: 'm3',
    21: 'm4', 22: 'm5', 23: 'm6', 24: 'menu', 25: 'turbo', 27: 'home',
    28: 'back2',
}

# 区域划分，来自 reference/openflydigi/mapping.py 的 OFF_* 常量。
REGIONS = [
    (0, 2, '版本/协议'),
    (2, 3, '包数'),
    (3, 13, 'LED 镜像'),
    (13, 109, '键位表 32×3'),
    (109, 123, '摇杆曲线 2×7'),
    (123, 137, '扳机行程 2×7'),
    (137, 145, '体感块 8'),
    (145, 154, '握把马达'),
    (154, 183, '扳机马达'),
    (183, 185, '保留'),
    (185, 225, '自适应扳机 2×20'),
    (225, 227, 'data_version'),
    (230, 768, '宏'),
    (770, 790, '标题'),
    (790, 814, '摇杆扩展'),
    (820, 825, '宏扩展'),
    (830, 836, '体感扩展'),
]


def key_name(k):
    return KEY_NAMES.get(k, 'id%d' % k)


def region_of(offset):
    for lo, hi, name in REGIONS:
        if lo <= offset < hi:
            return name
    return '未归类'


def title_of(blob):
    return blob[770:790].decode('utf-16-le', 'replace').rstrip('\uffff\x00')


def key_table(blob):
    """[(键名, 目标名, turbo 频率)]，只列改过的。"""
    out = []
    for k in range(NKEYS):
        target = blob[13 + k * 3]
        mode = blob[14 + k * 3]
        freq = blob[15 + k * 3]
        if target == 0xFF and freq == 0:
            continue
        if target == 254:
            name = 'keyboard(死键)'
        elif target == 32:
            name = 'macro'
        elif target == 0xFF:
            name = '默认(自身)'
        else:
            name = key_name(target)
        out.append((key_name(k), name, mode, freq))
    return out


def motion_block(blob):
    m = blob[137:145]
    targets = {0: 'off', 1: 'left_stick', 2: 'right_stick', 3: 'mouse'}
    return {
        'target': targets.get(m[0], 'unknown(%d)' % m[0]),
        'key1': key_name(m[1]) if m[1] != 0xFF else None,
        'enable': m[2],
        'dead_zone': m[3],
        'sensitivity': (m[4], m[5]),
        'use': m[6],
        'key2': key_name(m[7]) if m[7] != 0xFF else None,
        'raw': m.hex(' '),
    }


def trigger_motors(blob):
    """154..183：1 个使能字节（0=开, 1=关）+ 两侧各 14 字节。"""
    return {
        'enabled': blob[154] == 0,
        'enable_byte': blob[154],
        'left': blob[155:169].hex(' '),
        'right': blob[169:183].hex(' '),
    }


def hexdump(blob, per=32):
    lines = []
    for off in range(0, len(blob), per):
        chunk = blob[off:off + per]
        text = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append('%04x  %-*s  %s'
                     % (off, per * 3 - 1, chunk.hex(' '), text))
    return '\n'.join(lines)


def describe(blob):
    lines = []
    lines.append('  标题: %r   data_version=%d   proto=0x%04x'
                 % (title_of(blob),
                    int.from_bytes(blob[225:227], 'little'),
                    int.from_bytes(blob[0:2], 'little')))
    keys = key_table(blob)
    if keys:
        lines.append('  键位表改动 %d 项:' % len(keys))
        for src, dst, mode, freq in keys:
            extra = ' turbo %dHz' % freq if freq else ''
            lines.append('    %-6s → %-14s mode=%d%s' % (src, dst, mode, extra))
    else:
        lines.append('  键位表: 全部默认')
    mo = motion_block(blob)
    lines.append('  体感: %-11s 开启=%s 死区=%d 灵敏度=%s 用途=%d raw=[%s]'
                 % (mo['target'], mo['key1'], mo['dead_zone'],
                    mo['sensitivity'], mo['use'], mo['raw']))
    tm = trigger_motors(blob)
    lines.append('  扳机马达: %s（使能字节=0x%02x）'
                 % ('开' if tm['enabled'] else '关', tm['enable_byte']))
    lines.append('  握把马达 145..154: %s' % blob[145:154].hex(' '))
    return '\n'.join(lines)


def diff(a, b, label_a, label_b):
    """逐字节 diff，按区域汇总。"""
    diffs = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    print('\n=== 逐字节对比：%s vs %s ===' % (label_a, label_b))
    if not diffs:
        print('完全相同。')
        return
    print('共 %d / %d 字节不同。' % (len(diffs), len(a)))
    buckets = {}
    for i in diffs:
        buckets.setdefault(region_of(i), []).append(i)
    for lo, hi, name in REGIONS:
        if name not in buckets:
            continue
        offs = buckets.pop(name)
        print('\n[%s] %d 字节' % (name, len(offs)))
        for i in offs[:24]:
            print('  [%3d] %-22s %s: %02x → %02x'
                  % (i, name, i, a[i], b[i]))
        if len(offs) > 24:
            print('  …另 %d 字节' % (len(offs) - 24))
    for name, offs in buckets.items():
        print('\n[%s] %d 字节: %s' % (name, len(offs), offs[:24]))


def main():
    ap = argparse.ArgumentParser(description='厂商 .dat → 840 字节 blob')
    ap.add_argument('--dat', default=os.path.join(
        ROOT, 'windows-dump', 'Controller', 'fp4', 'default',
        'default_mapping_132.dat'))
    ap.add_argument('--slot', type=int, default=0, help='打印哪个槽位（默认 0）')
    ap.add_argument('--diff', default=None,
                    help='和这个 840 字节原始 blob 对比（blob_audit --dump 生成）')
    ap.add_argument('--diff-slot', type=int, default=None,
                    help='用哪个出厂槽和 --diff 对比（默认同 --slot）')
    ap.add_argument('--out', default='data/txt')
    ap.add_argument('--no-save', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(args.dat):
        sys.exit('找不到 %s\n把空间站的 Configs 放到 windows-dump/，'
                 '见 docs/windows-files.md' % args.dat)
    with open(args.dat, 'rb') as f:
        data = f.read()
    beans = mapping_bean.beans(data)
    blobs = [mapping_bean.blob_from_bean(b) for b in beans]
    print('%s：%d 个槽位，每个 %d 字节'
          % (args.dat, len(blobs), len(blobs[0])))

    for i, blob in enumerate(blobs):
        print('\n--- 出厂槽 %d ---' % i)
        print(describe(blob))

    slot = max(0, min(args.slot, len(blobs) - 1))
    blob = blobs[slot]

    if args.diff:
        with open(args.diff, 'rb') as f:
            live = f.read()
        if len(live) != BLOB_LEN:
            sys.exit('--diff 文件是 %d 字节，应为 %d' % (len(live), BLOB_LEN))
        other = blobs[args.diff_slot if args.diff_slot is not None else slot]
        diff(other, live, '出厂槽%d' % slot, os.path.basename(args.diff))

    if not args.no_save:
        os.makedirs(args.out, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        path = os.path.join(args.out, 'factory_fp4_%s.txt' % stamp)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('# 出厂配置转储：%s\n' % args.dat)
            f.write('# 生成：%s\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
            f.write('# 翻译器：reference/openflydigi/mapping_bean.py（上游）\n')
            for i, b in enumerate(blobs):
                f.write('\n=== 槽位 %d ===\n' % i)
                f.write(describe(b) + '\n')
                f.write('\n' + hexdump(b) + '\n')
        print('\n已写 %s' % path)


if __name__ == '__main__':
    main()
