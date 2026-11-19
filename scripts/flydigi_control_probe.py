#!/usr/bin/env python3
"""冰原狼4 控制键实测：SELECT 组合、Home 短按/长按、FN/Turbo/M1/M2

要回答的问题
------------
1. `SELECT + A/B/X/Y` 会不会被固件当成切槽？切了的话槽位号怎么变？
2. 这些组合键会不会同时出现在标准按钮字里（游戏会收到 Back+A）？
3. Home 短按/长按分别做什么，会不会切换模式或断开连接？
4. FN / Turbo / M1 / M2 是否稳定出现在 `[19..20]` 位图里？

这些答案决定 daemon 的切配置方式：如果固件不认 `SELECT+...`，就由
daemon 自己实现软件四槽（推荐）；如果固件认，daemon 只跟着 `[27]` 走。

交互
----
每个项目只显示一行短提示，按 Enter 开始采集，6 秒后自动进入下一项。
采集期间底部有一行实时状态，不刷屏。中途 Ctrl-C 会正常收尾并恢复 xpad。

产物
----
* `captures/control_<时间戳>.bin`  原始报文流（32 字节/帧）
* `captures/control_<时间戳>.json` 每项结论 + 槽位变化 + 按键事件

用法
----
    sudo python3 scripts/flydigi_control_probe.py
    sudo python3 scripts/flydigi_control_probe.py --seconds 8
    sudo python3 scripts/flydigi_control_probe.py --free    # 自由记录 60 秒
"""

import argparse
import json
import os
import select
import struct
import sys
import time

sys.dont_write_bytecode = True          # 见 blob_audit：别让 root 写 __pycache__

import flydigi_report as rep
from flydigi_usb import Link, Pad, find_pad, identify

PHASES = [
    ('select_tap', '只按一次 SELECT（Back）'),
    ('select_a', '按住 SELECT，再按 A，然后都松开'),
    ('select_b', '按住 SELECT，再按 B，然后都松开'),
    ('select_x', '按住 SELECT，再按 X，然后都松开'),
    ('select_y', '按住 SELECT，再按 Y，然后都松开'),
    ('home_short', '短按一次 Home（Logo）'),
    ('home_long', '按住 Home 约 1 秒再松开'),
    ('fn', '按一次 FN（SELECT 右边那颗）'),
    ('turbo', '按一次 Turbo'),
    ('m1', '按一次 M1（背面左）'),
    ('m2', '按一次 M2（背面右）'),
]


class Recorder(object):
    """边收报文边记按键边沿。"""

    def __init__(self, link):
        self.link = link
        self.prev_std = frozenset()
        self.prev_bits = frozenset()
        self.events = []
        self.frames = []
        self.last_frame = time.time()
        self.gap = 0.0

    def pump_once(self):
        got = self.link.pad.reap()
        if got is None:
            return False
        ctx, st, d = got
        if ctx not in self.link.in_ids:
            return False
        self.link.in_ids.discard(ctx)
        self.link.pump(1)
        if not d or len(d) < 32:
            return False
        if rep.is_reply(d):
            return False
        now = time.time()
        if now - self.last_frame > 0.5:
            self.gap = max(self.gap, now - self.last_frame)
        self.last_frame = now
        self.frames.append((now, d))
        self._edges(now, d)
        return True

    def _edges(self, now, data):
        std = rep.std_buttons(data)
        bits = rep.decode(data)['bits']
        for name in std - self.prev_std:
            self.events.append([round(now, 3), 'std_down', name])
        for name in self.prev_std - std:
            self.events.append([round(now, 3), 'std_up', name])
        for name in bits - self.prev_bits:
            self.events.append([round(now, 3), 'bit_down', name])
        for name in self.prev_bits - bits:
            self.events.append([round(now, 3), 'bit_up', name])
        self.prev_std, self.prev_bits = std, bits

    def snapshot(self):
        return {'std': sorted(self.prev_std), 'bits': sorted(self.prev_bits)}

    def run(self, seconds):
        """采集 N 秒；返回 (事件列表, 本段起始下标)。"""
        start = len(self.events)
        end = time.time() + seconds
        while time.time() < end:
            if not self.pump_once():
                time.sleep(0.001)
        return start

    def wait_enter(self):
        """等 Enter，同时继续 pump，避免手柄输入缓冲溢出。"""
        sys.stdout.write('    Enter 开始… ')
        sys.stdout.flush()
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.02)
            if r:
                sys.stdin.readline()
                return
            self.pump_once()


def summarize(events):
    """把一段事件整理成"按了哪些键、最大同时组合"。"""
    pressed = set()
    max_std = set()
    max_bits = set()
    cur_std, cur_bits = set(), set()
    for _, kind, name in events:
        if kind == 'std_down':
            cur_std.add(name)
            if len(cur_std) > len(max_std):
                max_std = set(cur_std)
            pressed.add(name)
        elif kind == 'std_up':
            cur_std.discard(name)
        elif kind == 'bit_down':
            cur_bits.add(name)
            if len(cur_bits) > len(max_bits):
                max_bits = set(cur_bits)
            pressed.add(name)
        elif kind == 'bit_up':
            cur_bits.discard(name)
    return {
        'pressed': sorted(pressed),
        'max_std_combo': sorted(max_std),
        'max_bit_combo': sorted(max_bits),
        'n_events': len(events),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=6.0,
                    help='每项采集时长（默认 6 秒）')
    ap.add_argument('--free', action='store_true', help='自由记录 60 秒')
    ap.add_argument('--free-seconds', type=float, default=60.0)
    ap.add_argument('--out', default='captures')
    args = ap.parse_args()

    dev = find_pad()
    if not dev:
        sys.exit('未找到手柄 (045e:028e)')

    print('手柄: %s' % dev)
    print('全程保持手柄亮灯。每个项目 6 秒，Ctrl-C 随时退出。\n')

    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    bin_path = os.path.join(args.out, 'control_%s.bin' % stamp)
    json_path = os.path.join(args.out, 'control_%s.json' % stamp)

    pad = Pad(dev)
    claimed = False
    result = {'time': stamp, 'device': dev, 'seconds': args.seconds, 'phases': []}
    rec = None
    try:
        print('解绑驱动: %s' % pad.takeover())
        claimed = True
        link = Link(pad)
        rec = Recorder(link)
        link.drain(0.3)

        info = identify(link)
        if not info:
            sys.exit('cmd16 无应答：老方言通道不通，先跑 flydigi_old_dialect_probe.py')
        result['identify'] = {k: v for k, v in info.items() if k != 'raw'}
        print('识别: 固件=%s uid=%s 当前槽位=%d\n'
              % (info['fw'], info['uid'], info['slot']))

        if args.free:
            print('自由记录 %.0f 秒：随便按，Ctrl-C 结束。' % args.free_seconds)
            start = len(rec.events)
            t0 = time.time()
            shown = -1
            while time.time() - t0 < args.free_seconds:
                if not rec.pump_once():
                    time.sleep(0.001)
                now = int(time.time() - t0)
                if now != shown:
                    shown = now
                    print('  %ds / %ds' % (now, int(args.free_seconds)))
            result['phases'].append({
                'phase': 'free',
                'summary': summarize(rec.events[start:]),
            })
        else:
            for idx, (phase, prompt) in enumerate(PHASES, 1):
                print('[%d/%d] %s' % (idx, len(PHASES), prompt))
                rec.wait_enter()

                slot_before = identify(link)['slot']
                start = len(rec.events)
                t0 = time.time()
                shown = -1
                while time.time() - t0 < args.seconds:
                    if not rec.pump_once():
                        time.sleep(0.001)
                    pct = int((time.time() - t0) / args.seconds * 10)
                    if pct != shown:
                        shown = pct
                        sys.stdout.write('\r    采集中 %3.0f%%' % (pct * 10))
                        sys.stdout.flush()
                sys.stdout.write('\r' + ' ' * 24 + '\r')
                gap_before = rec.gap
                rec.gap = 0.0
                slot_after = identify(link)['slot']
                events = rec.events[start:]
                summary = summarize(events)
                item = {
                    'phase': phase,
                    'prompt': prompt,
                    'seconds': args.seconds,
                    'slot_before': slot_before,
                    'slot_after': slot_after,
                    'slot_changed': slot_before != slot_after,
                    'max_gap_s': round(gap_before, 3),
                    'summary': summary,
                    'events': events[:400],
                }
                result['phases'].append(item)
                print('    → 标准键: %s' % (' '.join(summary['max_std_combo']) or '无'))
                print('      位图键: %s   槽位: %s→%s%s'
                      % (' '.join(summary['max_bit_combo']) or '无',
                         slot_before, slot_after,
                         '  (变了!)' if item['slot_changed'] else ''))
                if gap_before > 0.5:
                    print('      ⚠ 报文中断 %.1fs：手柄可能切了模式/掉线' % gap_before)
                print()

    except KeyboardInterrupt:
        print('\n已中断，正在收尾…')
    finally:
        if claimed:
            try:
                pad.release()
            except OSError:
                pass
        pad.reconnect_driver()
        pad.close()
        print('xpad 已恢复')

    if rec and rec.frames:
        # 和其它探针一致：<double 时间戳><uint32 长度><32 字节报文>
        with open(bin_path, 'wb') as f:
            for ts, frame in rec.frames:
                f.write(struct.pack('<dI', ts, len(frame)))
                f.write(frame)
        result['capture'] = os.path.basename(bin_path)
        print('原始报文: %s（%d 帧）' % (bin_path, len(rec.frames)))
    if result['phases']:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print('结论: %s' % json_path)
        print('文本存档: python3 scripts/flydigi_capture_to_text.py %s' % bin_path)


if __name__ == '__main__':
    main()
