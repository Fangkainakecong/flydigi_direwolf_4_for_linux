#!/usr/bin/env python3
"""解码一个 captures/inputs_*.bin 抓包（布局见 docs/input-report.md）

不连硬件，纯离线分析。用来复核探针结果、或事后拆别人给的抓包。

用法
----
    python3 scripts/flydigi_capture_decode.py captures/inputs_20261119-000752.bin
    python3 scripts/flydigi_capture_decode.py <bin> --segments   # 只看活动段
    python3 scripts/flydigi_capture_decode.py <bin> --stalls     # 只看断流

输出四块：
  1. 帧数、时长、帧长、>50ms 的断流
  2. 静止期的逐字节取值范围（判断哪些字节是噪声）
  3. 按键位图的时间段（谁在什么时候被按下，含 M1/M2/FN/Turbo）
  4. 每个活动段的按钮字、扳机、摇杆极值
"""
import argparse
import struct
import sys

KEY_ID = {0: '上', 1: '右', 2: '下', 3: '左', 4: 'A', 5: 'B', 6: 'Back', 7: 'X',
          8: 'Y', 9: 'Start', 10: 'LB', 11: 'RB', 12: 'LT', 13: 'RT',
          14: 'L3', 15: 'R3', 18: 'M1', 19: 'M2', 24: 'FN', 25: 'Turbo',
          26: 'id26', 27: 'Home'}

STD = [(0x0001, '上'), (0x0002, '下'), (0x0004, '左'), (0x0008, '右'),
       (0x0010, 'Start'), (0x0020, 'Back'), (0x0040, 'L3'), (0x0080, 'R3'),
       (0x0100, 'LB'), (0x0200, 'RB'), (0x0400, 'Home'),
       (0x1000, 'A'), (0x2000, 'B'), (0x4000, 'X'), (0x8000, 'Y')]


def load(path):
    raw = open(path, 'rb').read()
    out = []
    i = 0
    while i + 12 <= len(raw):
        ts, ln = struct.unpack_from('<dI', raw, i)
        i += 12
        out.append((ts, raw[i:i + ln]))
        i += ln
    return out


def s16(d, off):
    return struct.unpack_from('<h', d, off)[0]


def keybits(d):
    return d[17] | (d[18] << 8) | (d[19] << 16) | (d[20] << 24)


def names(v):
    return [KEY_ID.get(k, f'id{k}') for k in range(32) if v >> k & 1]


def std_names(b):
    return [n for m, n in STD if b & m]


def show_stalls(recs):
    print('=== 断流 (>50ms) ===')
    found = False
    for (t0, _), (t1, _) in zip(recs, recs[1:]):
        if t1 - t0 > 0.05:
            found = True
            print(f'  {t0:8.3f} → {t1:8.3f}   间隔 {t1 - t0:.3f}s')
    if not found:
        print('  无')


def show_baseline(recs, a=1.0, b=3.0):
    seg = [d for ts, d in recs if a <= ts < b and len(d) >= 26]
    if not seg:
        seg = [d for _, d in recs[:200] if len(d) >= 26]
    print(f'=== 静止期 ({a}..{b}s, {len(seg)} 帧) 逐字节范围 ===')
    for i in range(len(seg[0])):
        vals = [d[i] for d in seg]
        print(f'  [{i:2d}] {min(vals):3d}..{max(vals):3d}  不同值 {len(set(vals))}')


def show_keys(recs):
    print('=== 键位位图时间段 ===')
    prev, t0 = None, None
    for ts, d in recs:
        if len(d) < 26:
            continue
        v = keybits(d)
        if v != prev:
            if prev:
                print(f'  {t0:8.2f}..{ts:8.2f} ({ts - t0:5.2f}s)  {",".join(names(prev))}')
            prev, t0 = v, ts
    if prev:
        print(f'  {t0:8.2f}..  end     {",".join(names(prev))}')


def segments(recs, gap=0.35):
    out = []
    cur = None
    last = None
    for ts, d in recs:
        if len(d) < 26:
            continue
        btn = d[2] | (d[3] << 8)
        lt, rt = d[4], d[5]
        ax = (s16(d, 6), s16(d, 8), s16(d, 10), s16(d, 12))
        active = btn or lt > 20 or rt > 20 or max(abs(v) for v in ax) > 4000
        if active:
            if cur is None:
                cur = {'t0': ts, 'buttons': set(), 'keys': set(), 'lt': 0, 'rt': 0,
                       'ax': [[0, 0] for _ in range(4)]}
            cur['t1'] = ts
            cur['buttons'].add(btn)
            cur['keys'].add(keybits(d))
            cur['lt'] = max(cur['lt'], lt)
            cur['rt'] = max(cur['rt'], rt)
            for i, v in enumerate(ax):
                cur['ax'][i][0] = min(cur['ax'][i][0], v)
                cur['ax'][i][1] = max(cur['ax'][i][1], v)
            last = ts
        elif cur is not None and ts - last > gap:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def show_segments(recs):
    segs = segments(recs)
    print(f'=== 活动段 ({len(segs)} 个) ===')
    for n, e in enumerate(segs, 1):
        std = sorted({b for b in e['buttons'] if b})
        keys = sorted({k for k in e['keys'] if k})
        btns = ','.join(x for b in std for x in std_names(b)) or '-'
        kb = ','.join(x for k in keys for x in names(k)) or '-'
        axes = ' '.join(f'{n_}={v[0]}..{v[1]}' for n_, v in
                        zip(('lx', 'ly', 'rx', 'ry'), e['ax'])
                        if v[1] - v[0] > 3000)
        print(f'{n:2d} {e["t0"]:8.2f}..{e["t1"]:8.2f} ({e["t1"] - e["t0"]:5.2f}s)'
              f'  标准={btns:<28} 位图={kb:<24} LT={e["lt"]:3d} RT={e["rt"]:3d}  {axes}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--stalls', action='store_true')
    ap.add_argument('--baseline', action='store_true')
    ap.add_argument('--keys', action='store_true')
    ap.add_argument('--segments', action='store_true')
    args = ap.parse_args()

    recs = load(args.path)
    if not recs:
        sys.exit('空文件')
    lens = sorted({len(d) for _, d in recs})
    print(f'{args.path}: {len(recs)} 帧, {recs[-1][0]:.2f}s, 长度 {lens}')

    all_ = not (args.stalls or args.baseline or args.keys or args.segments)
    if all_ or args.stalls:
        show_stalls(recs)
    if all_ or args.keys:
        show_keys(recs)
    if all_ or args.baseline:
        show_baseline(recs)
    if all_ or args.segments:
        show_segments(recs)


if __name__ == '__main__':
    main()
