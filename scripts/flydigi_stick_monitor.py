#!/usr/bin/env python3
"""飞智手柄摇杆/扳机轴 实时监听: 每秒打印各轴的值域与变化幅度
用途: 验证2.4G模式下xpad的手柄数据是否正常(满量程/死区/量化), 不依赖evdev库, 裸读evdev事件
用法: python3 flydigi_stick_monitor.py [/dev/input/event6]  (Ctrl+C退出)"""
import os, sys, glob, select, struct, time, collections

AXN = {0:'LX',1:'LY',2:'RX',3:'RY',4:'LT',5:'RT',16:'DPX',17:'DPY'}
BTN = {0x130:'A',0x131:'B',0x132:'X',0x133:'Y',0x134:'LB',0x135:'RB',0x136:'LT键',0x137:'RT键',
       0x138:'BACK',0x139:'GUIDE',0x13a:'START',0x13b:'LS按下',0x13c:'RS按下',
       0x13d:'DPAD上',0x13e:'DPAD下',0x13f:'DPAD左',0x140:'DPAD右'}

def find_event_node():
    """自动找名字含xbox/360的evdev节点"""
    for e in sorted(glob.glob('/dev/input/event*')):
        try:
            name = open(f'/sys/class/input/{os.path.basename(e)}/device/name').read()
        except OSError:
            continue
        if 'x-box' in name.lower() or '360' in name.lower():
            return e
    return None

path = sys.argv[1] if len(sys.argv) > 1 else find_event_node()
if not path:
    print("未找到手柄evdev节点"); sys.exit(1)
fd = os.open(path, os.O_RDONLY)
print(f"监听 {path}  (每秒一行: 各轴当秒 值域/变化幅度)", flush=True)
stats = collections.defaultdict(lambda: [None, None])
def flush():
    out = []
    for ax in (0,1,2,3,4,5):
        lo, hi = stats.get(ax, [None, None])
        out.append(f"{AXN[ax]}:-" if lo is None else f"{AXN[ax]}:{lo}~{hi} d{hi-lo}")
    print(time.strftime('%H:%M:%S'), ' '.join(out), flush=True)
    stats.clear()
last = time.time()
while True:
    r, _, _ = select.select([fd], [], [], 0.25)
    if r:
        data = os.read(fd, 4096)
        for i in range(0, len(data)//24*24, 24):
            sec, us, typ, code, val = struct.unpack_from('<qqHHi', data, i)
            if typ == 3 and code in AXN:
                s = stats[code]
                s[0] = val if s[0] is None else min(s[0], val)
                s[1] = val if s[1] is None else max(s[1], val)
    if time.time() - last >= 1.0:
        flush(); last = time.time()
