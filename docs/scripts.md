# 飞智冰原狼4 (Direwolf 4) 实验脚本

> 配套方案文档: [docs/plan.md](../docs/flydigi-dw4-2.4g-gyro-plan.md)
> 全部脚本需 sudo 运行（直接操作 USB 设备），运行结束自动恢复 xpad 驱动
> 日期: 2026-11-18

## 脚本清单

### `flydigi_button_probe.py` — 全输入探针（事件驱动，推荐）⭐

**作用**: 弄清**所有按键和轴**在 USB 报文里的位置 —— 21 个按键
（A/B/X/Y、LB/RB、LT/RT、L3/R3、Start/Back/Home、十字四向、M1/M2、FN、Turbo）
+ 8 个轴动作（左右摇杆四方向满偏）。**不用掐秒表**：脚本自己从报文里检测
按下/松开，检测到就自动进下一个。

**流程**:
1. 静止 2 秒做基线，每个字节记录 min/max 再留一份等于噪声幅度的余量：
   数字位（基线 0）任何置位都算；摇杆（基线 ±几百抖动）只有满偏才越界，
   抖动和零漂不会误触发。
2. **阶段 A**：不开厂商流，先测一次 M1 —— 回答“默认状态能不能看到”。
3. **阶段 B**：发 cmd17 开厂商流，重新校准，再逐项测 29 个输入。
4. 每项：提示 → 检测到事件 → 打印签名（字节/取值或范围/时长/标准[2]是否变）。
   **6 秒**内无反应自动跳过，结束时列出漏掉的项和重跑命令。
5. 每个输入开测前有 0.3 秒“静止对齐”：摇杆自动校零导致的漂移会被吸收，
   不会级联误判。
6. 结束可选进入**自由记录模式**：按任意键打印一个签名，用于验证
   “Windows 里把 M2 改成 L3 后 Linux 收到什么”。

**产物**: `captures/inputs_<时间戳>.bin`（报文流）+
`captures/inputs_<时间戳>.json`（基线 min/max/margin、每项签名）

**用法**:
```bash
pkill -TERM steam
sudo python3 scripts/flydigi_button_probe.py --note "Windows 全默认键位"
sudo python3 scripts/flydigi_button_probe.py --only m1,m2,ls_right
sudo python3 scripts/flydigi_button_probe.py --free
```

**当前状态**: 事件逻辑已用合成报文离线验证（单次按下、30 Hz 通断、模拟轴满偏、
基线漂移四种情况），尚未在真机上跑。

### `flydigi_2.4g_raw_motion_test.py` — 主实验：开启 2.4G 体感流 ⭐

**作用**: 向 2.4G dongle (045e:028e) 发送飞智厂商指令 17（开启 raw data 流），
随后监测 12 秒输入流，验证体感报文（`0x04` 开头 / `0xEF` 标记，陀螺仪@偏移18-22、
加速度计@偏移24-28）是否出现，并在 0-3s/3-6s/6-9s/9-12s 分别按
M1/M2/FN/Turbo，检查这四个键是否藏在厂商报文里。

**流程**: 自动定位手柄 → 卸载 xpad → usbfs 独占接口 → 发指令1(ACK验证) →
发指令17 → 读流12秒 → 重载 xpad。

**产物**: `/tmp/opencode/raw_stream.bin`（带时间戳的全量报文）

**用法**:
```bash
sudo python3 scripts/flydigi_2.4g_raw_motion_test.py
```

**当前状态**: 传输层已验证（IN 读取 ~1000Hz ✓、rumble OUT ✓、厂商指令1 ACK ✓），
指令 17 的体感流验证进行中。

> 注：该脚本靠时间窗让用户按 M1/M2/FN/Turbo（“0-3s 按 M1”），人做不到。
> 按键部分已由 `flydigi_button_probe.py` 取代；此脚本现在只用于体感/流测试。

### `flydigi_2.4g_usbfs_probe.py` — 传输层验证诊断（分阶段 usbmon 抓包）

**作用**: 诊断/验证 usbfs + 异步 URB 传输层的完整链路。自动开 usbmon 抓总线数据，
分阶段执行: A(xpad在位基线) → B(卸xpad+独占+读IN) → C(发rumble×3) →
D(发厂商指令1) → E(收尾)，最后汇总"线上实际发生的事件"与阶段对照。

**用法**:
```bash
sudo python3 scripts/flydigi_2.4g_usbfs_probe.py
```

**已验证的结论**（2026-11-18）:
- usbfs 异步 URB 读 IN 正常: 3 秒 2997 包（~1000Hz 标准 XInput 流 `00 14 00 ...`）
- 中断 OUT (EP 0x05) 写 rumble: 完成 status=0
- 厂商指令 1 (0xA5 5A A5 信封): ACK `a5 5a a5 01 02 03` — **厂商通道可用**

### `flydigi_stick_monitor.py` — 摇杆轴实时监听（无依赖）

**作用**: 裸读 evdev 事件（不需要 python-evdev），每秒打印各摇杆/扳机轴的
值域与变化幅度。用于验证 2.4G 模式下 xpad 手柄数据质量
（满量程 ±32768、死区、静止噪音），排查体感是否被错误映射进摇杆。

**用法**:
```bash
python3 scripts/flydigi_stick_monitor.py            # 自动找手柄节点
python3 scripts/flydigi_stick_monitor.py /dev/input/event6
```

**已验证的结论**（2026-11-17）:
- 推摇杆满量程正常输出（±32768 连续值），2.4G 手柄功能无损坏
- 静止时 ±770 范围内 64 一档的量化抖动 = 摇杆噪音 + 未校准中心偏移，非体感映射

## 技术要点（踩坑记录）

1. **usbfs 普通 `write()` 对中断端点返回 EINVAL** — 必须用
   `USBDEVFS_SUBMITURB/REAPURBNDELAY` 异步 URB
2. **ioctl 编码必须用真实的 `struct usbdevfs_urb` 大小（64 位 = 56 字节）**，
   否则 ENOTTY
3. **Python `fcntl.ioctl` 会复制缓冲区 / 32 位截断大地址** — 必须用
   `libc.ioctl`（ctypes `c_void_p` 传完整地址），否则内核记录的
   `as->userurb` 指向失效内存（症状: reap 垃圾数据、DISCARDURB EINVAL、曾导致段错误）
4. **URB 结构体和数据缓冲区必须保活到 reap**（pending 字典持有引用），
   内核完成时会把 status/actual_length 写回提交时的结构体内存
5. xpad 驱动需先卸载才能 usbfs 独占接口；脚本用 `modprobe -r xpad` /
   `modprobe xpad` 包裹（`USBDEVFS_DISCONNECT` ioctl 不可靠）
6. 手柄"断开"提示来自 Steam（evdev 节点随 xpad 卸载消失），2.4G 链路本身不受影响
