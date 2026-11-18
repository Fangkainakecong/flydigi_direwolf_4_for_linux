# 飞智冰原狼4 (Direwolf 4) 实验脚本

> 配套方案文档: [docs/plan.md](../docs/flydigi-dw4-2.4g-gyro-plan.md)
> 全部脚本需 sudo 运行（直接操作 USB 设备），运行结束自动恢复 xpad 驱动
> 日期: 2026-11-18

## 脚本清单

### `flydigi_button_probe.py` — 全输入探针（事件驱动，推荐）⭐

**作用**: 确认每个按键/轴的报文落点。布局已解出（见
[docs/input-report.md](input-report.md)），脚本直接查位图，不再用基线猜。

**交互**（专门为"人看不完长提示"改过）:
- 每个按键只显示一行短提示，如 `[5/21] 按 A`；
- **摇杆不再逐方向提问**：提示"慢慢转一圈"，脚本自动判定四个方向，
  转到哪个打哪个勾（`上✓ 下· 左✓ 右·`），转完自动进下一项；
- 等待时在终端底部用一行实时显示 `左[..] 右[..] LT RT`，不刷屏；
- 单项超时 20 秒（`--max-wait` 可调），`Enter` 跳过、`r` 重测；
- 报文流断 1 秒会自动重置 URB 并提示，不会像上次那样静默 22 秒。

**用法**:
```bash
pkill -TERM steam
sudo python3 scripts/flydigi_button_probe.py --note "Windows 全默认键位"
sudo python3 scripts/flydigi_button_probe.py --no-keys        # 只测两个摇杆
sudo python3 scripts/flydigi_button_probe.py --only m1,m2
sudo python3 scripts/flydigi_button_probe.py --only rs_up     # 单项方向重测
sudo python3 scripts/flydigi_button_probe.py --free           # 现场看任意键
```

**产物**: `captures/inputs_<时间戳>.bin`（报文流）+ `.json`（每项签名）

**已验证**: 2026-11-19 全量跑通 21 键；摇杆逻辑用合成报文离线验证
（整圈、斜推不误判、按错键不误判、方向反了不通过）。

### `flydigi_old_dialect_probe.py` — 老方言识别（cmd16 / cmd80 sub4）

**作用**: 确认老协议通道可用，读 `DeviceType / uid / 固件 / 电量 / 连接方式 /
当前槽位`，以及 4 个槽位的配置版本号。10 秒，不需要按键。

```bash
sudo python3 scripts/flydigi_old_dialect_probe.py
```

**已验证**（2026-11-19）: `DeviceType=132 (FP4)`、固件 `4.0.7.2`、
回包注入在 `[14]`、应答在 `[15]`。详见
[findings-legacy-measured.md](findings-legacy-measured.md)。

### `flydigi_legacy_blob_read.py` — 读全部 4 个配置槽位 ⭐

**作用**: `cmd33` 读 840 字节配置 blob（84 包 × 10 字节），解析键位表，
横向对比 4 个槽位；同时打印识别信息和 4 个 data_version。

```bash
sudo python3 scripts/flydigi_legacy_blob_read.py
sudo python3 scripts/flydigi_legacy_blob_read.py --slot 2
```

**已验证**（2026-11-19）: 槽位 0/2/3 各 840 字节读全，槽位 1 为空（0 包）；
解析结果与上游 `mapping.py` 一致。产物 `captures/blob_slot<N>_<时间>.bin`。

### `flydigi_motion_capture.py` — 体感采集（回车打标记，不掐时间）

**作用**: 连续录报文，每个动作"回车开始 → 做 → 回车结束"，按标记切片
统计 `[14..16]` / `[21..24]` 的范围。分 A 段（不发 cmd17）和 B 段（发 cmd17），
用来判断厂商流开关是否改变运动数据。

```bash
sudo python3 scripts/flydigi_motion_capture.py
```

**产物**: `captures/motion_<时间>.bin` + `.json`（阶段标记与逐字节范围）

**已验证**（2026-11-19）: `[14..16]` 是三轴角速度（`int8`，约 89 Hz），
`[21..24]` 是状态字节不是姿态。详见 [findings-motion.md](findings-motion.md)。

### `flydigi_accel_probe.py` — 加速度计探测（静态姿态法）

**作用**: 让手柄保持 7 种静态姿态各录 2 秒，再录一段"水平上下颠"。
角速度静止恒为 0、加速度计随重力方向变化，所以跨姿态极差 ≥8 的字节
就是加速度计候选。每个姿态只按一次回车，不掐时间。

```bash
sudo python3 scripts/flydigi_accel_probe.py
sudo python3 scripts/flydigi_accel_probe.py --try-raw   # 附带试 raw 流开关（实验性）
```

**产物**: `captures/accel_<时间>.bin` + `.json`

**实测结论**（2026-11-19，`captures/accel_20261119-011738.bin`）: 七种静态姿态下
`[14..16]` 均值全 0（陀螺仪），只有 `[24]` 随正面朝下 +6，但上下颠时不动 →
标准报文里没有可用的加速度数据。注意：**硬件本身是六轴**，加速度计只是没有出现在
2.4G XInput 报文里，见 [findings-public-sources.md](findings-public-sources.md)。

### `flydigi_dinput_probe.py` — DInput 模式被动探针（只读）

**作用**: 切到 DInput（2.4G 档，长按 `FN+A`，指示灯变蓝）后，枚举所有
Flydigi/手柄相关的 `/dev/hidraw*`，被动读取并统计首字节、长度、是否出现
`0x04`/`0xEF` 厂商报文，同时按仓库格式落盘。**不发送任何 USB 命令，不改手柄状态。**

```bash
sudo python3 scripts/flydigi_dinput_probe.py        # 读 20 秒
sudo python3 scripts/flydigi_dinput_probe.py 30
```

**产物**: `captures/dinput_<时间>.bin` + `.json`

**目的**: 同代 Vader 3 Pro 在 DInput 下用 `0x04/0xEF` 报文输出 12 位三轴陀螺仪
和 int16 三轴加速度（4096 = 1g）。如果冰原狼4 也一样，就能拿到完整六轴，
而不必再猜 XInput 的 int8 扩展。见 [findings-public-sources.md](findings-public-sources.md)。

### `flydigi_capture_to_text.py` — 抓包转文本存档

**作用**: 把 `captures/*.bin` 转成 `data/txt/*.txt`（CSV / 十六进制转储），
作为进仓库的长期数据形式；同时重写 `data/MANIFEST.md` 索引。文件头记录源
bin 的 sha256。格式说明见 [data.md](data.md)。

```bash
python3 scripts/flydigi_capture_to_text.py --all
python3 scripts/flydigi_capture_to_text.py captures/motion_20261119-003907.bin
python3 scripts/flydigi_capture_to_text.py --blob captures/blob_slot0_*.bin
```

### `flydigi_capture_decode.py` — 离线解码抓包

**作用**: 不连硬件，把 `captures/inputs_*.bin` 拆成"断流/按键时间段/
静止期字节范围/活动段"，用于复核探针结果或分析别人给的抓包。

```bash
python3 scripts/flydigi_capture_decode.py captures/inputs_20261119-000752.bin
python3 scripts/flydigi_capture_decode.py <bin> --segments
```

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
