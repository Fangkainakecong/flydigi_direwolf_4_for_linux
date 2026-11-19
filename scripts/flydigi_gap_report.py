#!/usr/bin/env python3
"""报文流断点报告：找出真正的中断，排除“探针自己停手”的假象

为什么需要
----------
2026-11-19 差点把 **探针的测量开销** 当成设备卡顿：

* `identify()` 会先 drain 再收 0.6 s，这段时间的报文不会写进抓包；
* 如果间隔统计沿用上一帧的时间戳，每个阶段都会凭空多出一个 ~1 s 的
  “中断”。`SELECT+A/B` 被误读成“固件切槽并卡顿 1 秒”就是这么来的。

真实的中断应该满足两条：**落在同一个连续采集段内部**，而且期间设备
没有重新枚举。这个脚本把抓包切成“连续段”，只在段内统计间隔，并打印
每段的帧率，方便判断是设备掉线还是探针停手。

用法
----
    python3 scripts/flydigi_gap_report.py captures/control_20261119-222738.bin
    python3 scripts/flydigi_gap_report.py --min-gap 0.1 captures/*.bin
    python3 scripts/flydigi_gap_report.py --json data/txt/gaps.json captures/x.bin

判读
----
* `段内断点` 才是真问题（丢输入、URB 没挂上、dongle 抖动）。
* `段间间隔` 是探针在做别的事（identify、等 Enter、写文件），不是设备问题。
* 段内帧率明显低于 ~1000 Hz，也说明有丢帧。
"""

import argparse
import glob
import json
import os
import struct
import sys

sys.dont_write_bytecode = True

SEG_SPLIT = 0.4      # 超过这个间隔就算“换了一段”


def load(path):
    raw = open(path, 'rb').read()
    recs = []
    i = 0
    while i + 12 <= len(raw):
        ts, ln = struct.unpack_from('<dI', raw, i)
        i += 12
        recs.append((ts, raw[i:i + ln]))
        i += ln
    return recs


def analyze(recs, min_gap):
    if not recs:
        return None
    t0 = recs[0][0]
    rel = [(ts - t0, d) for ts, d in recs]

    segments = []
    start = 0
    for i in range(1, len(rel)):
        if rel[i][0] - rel[i - 1][0] > SEG_SPLIT:
            segments.append((start, i))
            start = i
    segments.append((start, len(rel)))

    seg_info = []
    for a, b in segments:
        span = rel[b - 1][0] - rel[a][0]
        frames = b - a
        seg_info.append({
            'start_s': round(rel[a][0], 3),
            'end_s': round(rel[b - 1][0], 3),
            'frames': frames,
            'hz': round(frames / span, 1) if span > 0 else None,
        })

    gaps = []
    for a, b in segments:
        for i in range(a + 1, b):
            gap = rel[i][0] - rel[i - 1][0]
            if gap >= min_gap:
                gaps.append({'at_s': round(rel[i][0], 3),
                             'gap_s': round(gap, 3),
                             'segment_start_s': round(rel[a][0], 3)})

    between = []
    for i in range(1, len(segments)):
        prev_end = rel[segments[i - 1][1] - 1][0]
        next_start = rel[segments[i][0]][0]
        between.append({'after_s': round(prev_end, 3),
                        'before_s': round(next_start, 3),
                        'gap_s': round(next_start - prev_end, 3)})

    return {
        'path': None,
        'frames': len(rel),
        'duration_s': round(rel[-1][0], 3),
        'segments': seg_info,
        'within_segment_gaps': gaps,
        'between_segment_gaps': between,
    }


def main():
    ap = argparse.ArgumentParser(description='报文流断点报告')
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--min-gap', type=float, default=0.3,
                    help='段内超过多少秒算一个断点（默认 0.3）')
    ap.add_argument('--json', default=None, help='把结果写成 JSON')
    args = ap.parse_args()

    paths = []
    for p in args.paths:
        paths.extend(sorted(glob.glob(p)) if any(c in p for c in '*?[') else [p])
    if not paths:
        sys.exit('没有输入文件')

    results = []
    for path in paths:
        recs = load(path)
        info = analyze(recs, args.min_gap)
        if not info:
            print('%s: 空文件' % path)
            continue
        info['path'] = path
        results.append(info)
        print('\n=== %s ===' % path)
        print('帧数 %d  时长 %.3fs  连续段 %d'
              % (info['frames'], info['duration_s'], len(info['segments'])))
        for i, seg in enumerate(info['segments'], 1):
            print('  段%d  %7.2fs .. %7.2fs  %6d 帧  %s Hz'
                  % (i, seg['start_s'], seg['end_s'], seg['frames'], seg['hz']))
        gaps = info['within_segment_gaps']
        print('  段内断点(>%.2fs): %s' % (
            args.min_gap,
            '无' if not gaps else
            ', '.join('%.2fs@%.2fs' % (g['gap_s'], g['at_s']) for g in gaps)))
        between = info['between_segment_gaps']
        if between:
            print('  段间间隔（探针停手，不是设备问题）: %s'
                  % ', '.join('%.2fs' % b['gap_s'] for b in between))

    if args.json and results:
        d = os.path.dirname(args.json)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print('\n已写 %s' % args.json)


if __name__ == '__main__':
    main()
