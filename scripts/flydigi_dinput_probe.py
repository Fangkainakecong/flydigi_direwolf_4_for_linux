#!/usr/bin/env python3
"""冰原狼4 DInput 模式被动探针：只看不写。

用途
----
2.4G 的 XInput 模式（045e:028e）只有一个厂商接口，报文是 32 字节的
XInput 扩展，里面只有 3 个 int8 角速度。官方说明冰原狼4 是六轴，
所以加速度计很可能只在 DInput 模式的 HID 报文里（参考 Vader 3 Pro
的 DInput 报文：0x04 开头、0xEF 标记、int16 加速度 4096=1g）。

本脚本把当前所有 Flydigi/手柄相关的 /dev/hidraw* 都打开，被动读取
并统计首字节、长度、是否出现 0x04/0xEF，同时按仓库统一格式落盘，
之后可以用 scripts/flydigi_capture_to_text.py 转文本。

用法
----
    sudo python3 scripts/flydigi_dinput_probe.py            # 读 20 秒
    sudo python3 scripts/flydigi_dinput_probe.py 30         # 读 30 秒

切到 DInput：手柄开关拨到 2.4G 档，长按 FN + A，指示灯变蓝。
切回 XInput：长按 FN + X，指示灯变白。

脚本不发送任何 USB 命令，不会改手柄状态；中途 Ctrl-C 也会正常收尾。
"""

import glob
import json
import os
import select
import struct
import sys
import time
from collections import Counter, defaultdict

OUT_DIR = "captures"


def read_text(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def usb_parent(hidraw_dir):
    """从 /sys/class/hidraw/hidrawN/device 往上找带 idVendor 的 USB 设备目录。"""
    d = os.path.realpath(hidraw_dir)
    while d and d != "/":
        if os.path.exists(os.path.join(d, "idVendor")):
            return d
        d = os.path.dirname(d)
    return None


def enumerate_nodes():
    nodes = []
    for sysdir in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        node = "/dev/" + os.path.basename(sysdir)
        if not os.path.exists(node):
            continue
        uevent = read_text(os.path.join(sysdir, "device", "uevent"))
        hid_name = ""
        hid_id = ""
        for line in uevent.splitlines():
            if line.startswith("HID_NAME="):
                hid_name = line.split("=", 1)[1]
            elif line.startswith("HID_ID="):
                hid_id = line.split("=", 1)[1]

        parent = usb_parent(os.path.join(sysdir, "device"))
        vid = pid = None
        product = manufacturer = ""
        if parent:
            try:
                vid = int(read_text(os.path.join(parent, "idVendor")), 16)
                pid = int(read_text(os.path.join(parent, "idProduct")), 16)
            except ValueError:
                pass
            product = read_text(os.path.join(parent, "product"))
            manufacturer = read_text(os.path.join(parent, "manufacturer"))

        blob = (hid_name + " " + product + " " + manufacturer).lower()
        interesting = (
            "flydigi" in blob
            or "direwolf" in blob
            or "x-box" in blob
            or "xbox" in blob
            or vid in (0x37D7, 0x045E, 0x04B4)
        )
        nodes.append(
            {
                "node": node,
                "hid_name": hid_name,
                "hid_id": hid_id,
                "vid": vid,
                "pid": pid,
                "product": product,
                "manufacturer": manufacturer,
                "interesting": interesting,
            }
        )
    return nodes


def main():
    seconds = 20.0
    if len(sys.argv) > 1:
        try:
            seconds = float(sys.argv[1])
        except ValueError:
            print(f"用法: {sys.argv[0]} [秒数]")
            return 2

    nodes = enumerate_nodes()
    if not nodes:
        print("没有找到任何 /dev/hidraw*。")
        print("如果手柄现在是 XInput 模式（白灯），这是正常的：")
        print("  1. 把背部开关拨到 2.4G 档")
        print("  2. 长按 FN + A，等指示灯变蓝（DInput）")
        print("  3. 重新运行本脚本")
        return 1

    print("当前 HID 节点：")
    targets = []
    for n in nodes:
        mark = "  <-- 关注" if n["interesting"] else ""
        vidpid = f"{n['vid']:04x}:{n['pid']:04x}" if n["vid"] is not None else "?"
        print(
            f"  {n['node']:<20} {vidpid}  {n['hid_name'] or n['product']}{mark}"
        )
        if n["interesting"]:
            targets.append(n)

    if not targets:
        print("\n没有 Flydigi/手柄相关的节点。确认已经切到 DInput（蓝灯）。")
        return 1

    fds = {}
    stats = defaultdict(lambda: {"count": 0, "lens": Counter(), "first": Counter(), "bytes": 0})
    has_raw = defaultdict(bool)
    sample = defaultdict(int)
    t_end = time.time() + seconds
    t_next_print = time.time() + 2.0

    for n in targets:
        try:
            fd = os.open(n["node"], os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            print(f"  打不开 {n['node']}: {e}（试试 sudo）")
            continue
        fds[fd] = n["node"]
        print(f"  已打开 {n['node']}")

    if not fds:
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bin_path = os.path.join(OUT_DIR, f"dinput_{stamp}.bin")
    meta_path = os.path.join(OUT_DIR, f"dinput_{stamp}.json")
    t0 = time.time()

    print(f"\n开始被动采集 {seconds:.0f} 秒。请做这些动作：")
    print("  1. 平放静止 3 秒")
    print("  2. 绕竖轴左右各转 45°（yaw）")
    print("  3. 前后点头（pitch）")
    print("  4. 左右横滚（roll）")
    print("  5. 把柄翻过来正面朝下，再翻回来")
    print("  6. 随便按 M1/M2/FN/Turbo")
    print("（不用卡时间，想到哪个做哪个）\n")

    total = 0
    with open(bin_path, "wb") as out:
        while time.time() < t_end:
            timeout = max(0.0, min(0.25, t_end - time.time()))
            try:
                ready, _, _ = select.select(list(fds), [], [], timeout)
            except InterruptedError:
                continue
            for fd in ready:
                node = fds[fd]
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    continue
                if not data:
                    continue
                now = time.time()
                out.write(struct.pack("<dI", now - t0, len(data)))
                out.write(data)
                total += 1

                s = stats[node]
                s["count"] += 1
                s["lens"][len(data)] += 1
                s["first"][data[0]] += 1
                s["bytes"] += len(data)

                # 0x04/0xEF 厂商报文：首字节 0x04，标记在 [1] 或 [3]
                if data[0] == 0x04 and (len(data) > 3 and 0xEF in (data[1], data[3])):
                    has_raw[node] = True

                if sample[node] < 3:
                    sample[node] += 1
                    print(f"  [{node}] len={len(data):2d}  {data[:32].hex(' ')}")

            if time.time() >= t_next_print:
                t_next_print += 2.0
                parts = []
                for node in stats:
                    s = stats[node]
                    parts.append(f"{node}:{s['count']}帧")
                print(f"  ... {time.time()-t0:4.1f}s  " + "  ".join(parts))

    for fd in fds:
        os.close(fd)

    meta = {
        "capture": os.path.basename(bin_path),
        "started": stamp,
        "duration_s": round(time.time() - t0, 3),
        "frames": total,
        "nodes": [],
    }

    print("\n===== 结果 =====")
    for n in targets:
        s = stats.get(n["node"])
        if not s:
            continue
        lens = " ".join(f"{k}B×{v}" for k, v in s["lens"].most_common(4))
        firsts = " ".join(f"0x{k:02x}×{v}" for k, v in s["first"].most_common(6))
        print(f"\n{n['node']}  ({n['hid_name'] or n['product']})")
        print(f"  帧数 {s['count']}，长度 {lens}")
        print(f"  首字节 {firsts}")
        print(f"  0x04/0xEF 厂商报文: {'有' if has_raw[n['node']] else '没有'}")
        meta["nodes"].append(
            {
                "node": n["node"],
                "name": n["hid_name"] or n["product"],
                "vid": n["vid"],
                "pid": n["pid"],
                "frames": s["count"],
                "lengths": dict(s["lens"]),
                "first_bytes": {f"0x{k:02x}": v for k, v in s["first"].items()},
                "has_0x04_0xEF": has_raw[n["node"]],
            }
        )

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"\n已保存 {bin_path}（{total} 帧）")
    print(f"已保存 {meta_path}")
    print("转文本: python3 scripts/flydigi_capture_to_text.py " + bin_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
