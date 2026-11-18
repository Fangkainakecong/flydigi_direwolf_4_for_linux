#!/usr/bin/env python3
"""把抓包 .bin 转成文本（CSV），作为仓库里长期保存的数据形式

报文流 .bin 格式：`<double 时间戳><uint32 长度><数据>` 连续排列
老方言实验 .bin：在上面基础上，每条前面多 16 字节阶段名
配置 blob .bin：裸 840 字节

产物写到 `data/txt/`（进 git；原始 .bin 在 captures/，被 .gitignore 忽略）。
格式说明见 docs/data.md。

用法
----
    python3 scripts/flydigi_capture_to_text.py --all
    python3 scripts/flydigi_capture_to_text.py captures/motion_20261119-003907.bin
    python3 scripts/flydigi_capture_to_text.py --blob captures/blob_slot0_*.bin
"""
import argparse
import glob
import hashlib
import os
import struct
import sys

OUT_DIR = 'data/txt'
MANIFEST = 'data/MANIFEST.md'

HEADER = """# flydigi-dw4-linux 报文文本
# 来源: {src}  大小: {size} 字节  sha256: {sha}
# 帧数: {n}  时长: {dur:.3f}s  报文长度: {rlen}
# 列: t_ms,btn,lt,rt,lx,ly,rx,ry,keys,raw14,raw15,raw16,raw21,raw22,raw23,raw24
# btn  = [2..3] 标准按钮字 (0000-ffff 十六进制)
# keys = [17..20] 飞智键位位图 (00000000-ffffffff)
# 布局见 docs/input-report.md
"""


def s16(d, o):
    if len(d) < o + 2:
        return 0
    return struct.unpack_from('<h', d, o)[0]


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def convert_stream(path, out_path):
    raw = open(path, 'rb').read()
    recs = []
    i = 0
    while i + 12 <= len(raw):
        ts, ln = struct.unpack_from('<dI', raw, i)
        i += 12
        recs.append((ts, raw[i:i + ln]))
        i += ln
    if not recs:
        print(f'跳过空文件 {path}')
        return None
    rlen = max(len(d) for _, d in recs)
    lines = [HEADER.format(src=os.path.basename(path), size=len(raw),
                           sha=sha(path), n=len(recs),
                           dur=recs[-1][0], rlen=rlen)]
    for ts, d in recs:
        if len(d) < 21:
            continue
        btn = d[2] | (d[3] << 8)
        keys = d[17] | (d[18] << 8) | (d[19] << 16) | (d[20] << 24)
        x = [d[k] if len(d) > k else 0 for k in (14, 15, 16, 21, 22, 23, 24)]
        lines.append(f'{ts * 1000:.3f},{btn:04x},{d[4]},{d[5]},'
                     f'{s16(d, 6)},{s16(d, 8)},{s16(d, 10)},{s16(d, 12)},'
                     f'{keys:08x},' + ','.join(str(v) for v in x))
    info = write(out_path, lines, len(recs), recs[-1][0], rlen)
    return dict(kind='报文流', frames=len(recs), dur=recs[-1][0], rlen=rlen,
                **info)


def convert_phased(path, out_path):
    """老方言实验的抓包：每条前面多 16 字节阶段名"""
    raw = open(path, 'rb').read()
    recs = []
    i = 0
    while i + 12 <= len(raw):
        ts, ln = struct.unpack_from('<dI', raw, i)
        i += 12
        phase = raw[i:i + 16].rstrip(b'\0').decode('latin1')
        i += 16
        recs.append((ts, phase, raw[i:i + ln]))
        i += ln
    if not recs:
        return None
    lines = [f'# 来源: {os.path.basename(path)}  大小: {len(raw)} 字节'
             f'  sha256: {sha(path)}',
             f'# 帧数: {len(recs)}  时长: {recs[-1][0]:.3f}s',
             '# 列: t_ms,phase,btn,lt,rt,lx,ly,rx,ry,keys,raw14..raw16,raw21..raw24']
    for ts, phase, d in recs:
        if len(d) < 21:
            continue
        btn = d[2] | (d[3] << 8)
        keys = d[17] | (d[18] << 8) | (d[19] << 16) | (d[20] << 24)
        x = [d[k] if len(d) > k else 0 for k in (14, 15, 16, 21, 22, 23, 24)]
        lines.append(f'{ts * 1000:.3f},{phase},{btn:04x},{d[4]},{d[5]},'
                     f'{s16(d, 6)},{s16(d, 8)},{s16(d, 10)},{s16(d, 12)},'
                     f'{keys:08x},' + ','.join(str(v) for v in x))
    info = write(out_path, lines, len(recs), recs[-1][0], 32)
    return dict(kind='老方言抓包（含阶段名）', frames=len(recs),
                dur=recs[-1][0], rlen=32, **info)


def convert_blob(path, out_path):
    blob = open(path, 'rb').read()
    lines = [f'# flydigi-dw4-linux 配置 blob',
             f'# 来源: {os.path.basename(path)}  大小: {len(blob)} 字节'
             f'  sha256: {sha(path)}',
             '# 布局见 docs/findings-profile-blob.md']
    for off in range(0, len(blob), 16):
        row = blob[off:off + 16]
        lines.append(f'{off:04x}: ' + ' '.join(f'{b:02x}' for b in row))
    info = write(out_path, lines, 0, 0, len(blob))
    return dict(kind='配置 blob 十六进制转储', frames=0, dur=0,
                rlen=len(blob), **info)


def write(out_path, lines, n, dur, rlen):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    size = os.path.getsize(out_path)
    print(f'{out_path}  ({len(lines)} 行, {size / 1e6:.1f} MB)')
    return dict(path=out_path, bytes=size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--blob', action='store_true', help='按 840 字节 blob 转')
    ap.add_argument('--all', action='store_true', help='captures/*.bin 全转')
    ap.add_argument('--out-dir', default=OUT_DIR)
    args = ap.parse_args()

    paths = args.paths
    if args.all:
        paths = sorted(glob.glob('captures/*.bin'))
    if not paths:
        sys.exit('没有输入文件')

    entries = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(args.out_dir, stem + '.txt')
        try:
            if args.blob or os.path.getsize(path) <= 2000:
                meta = convert_blob(path, out)
            elif 'old_dialect' in path:
                meta = convert_phased(path, out)
            else:
                meta = convert_stream(path, out)
        except OSError as e:
            print(f'{path}: {e}')
            continue
        if meta:
            entries.append((os.path.basename(path), meta))

    if args.all and entries:
        write_manifest(entries, args.out_dir)


def write_manifest(entries, out_dir):
    lines = ['# 文本数据索引', '',
             '由 `scripts/flydigi_capture_to_text.py --all` 生成。'
             '原始 `.bin` 只在本地 `captures/`（已 gitignore），'
             '进仓库的是这里的文本形式。', '',
             '| 文本文件 | 类型 | 帧数 | 时长 | 来源 bin |',
             '|---|---|---|---|---|']
    for src, m in entries:
        name = os.path.basename(m['path'])
        kind = m['kind']
        frames = f"{m['frames']:,}" if m['frames'] else '—'
        dur = f"{m['dur']:.1f}s" if m['dur'] else '—'
        lines.append(f'| [{name}](txt/{name}) | {kind} | {frames} | {dur} | `{src}` |')
    lines += ['', '每列的含意见 [docs/data.md](../docs/data.md)。', '']
    os.makedirs('data', exist_ok=True)
    with open(MANIFEST, 'w') as f:
        f.write('\n'.join(lines))
    print(f'{MANIFEST}  ({len(entries)} 条)')


if __name__ == '__main__':
    main()
