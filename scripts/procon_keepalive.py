#!/usr/bin/env python3
# Pro Controller 保活：每300ms发一次0强度震动包（模拟Switch主机行为），并监控连接
import os, sys, time

HIDRAW = "/dev/hidraw3"
counter = 0
deadline = time.time() + float(sys.argv[1]) if len(sys.argv) > 1 else time.time() + 3600

def rumble_zero():
    # 0x30 报告 + counter + 8字节零震动数据
    global counter
    counter = (counter + 1) & 0xFF
    # 左右马达均为0强度（高频/低频都为0）
    return bytes([0x30, counter, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

print(f"保活启动 -> {HIDRAW}", flush=True)
fd = os.open(HIDRAW, os.O_WRONLY | os.O_NONBLOCK)
while time.time() < deadline:
    try:
        os.write(fd, rumble_zero())
    except OSError as e:
        print(f"写入失败: {e} - 连接可能已断", flush=True)
        break
    time.sleep(0.3)
os.close(fd)
print("保活结束", flush=True)
