# 飞智冰原狼4 (Direwolf 4) Linux 2.4G 体感方案

> 目标：在 Ubuntu 22.04 (Dell Inspiron 3443) 上，通过 2.4G dongle 直连实现
> 手柄 + 体感瞄准 + FN/M1/M2/Turbo 可见 + 多配置切换，全程 CLI，无需 Windows 空间站。
>
> 状态：方案设计（核心假设待 USB 厂商通道实验验证）
> 日期：2026-11-17

---

## 1. 背景与已验证的事实

前期实验结论（蓝牙/2.4G/USB 各模式实测）：

| 模式 | 手柄功能 | 体感 | FN/M1/M2/Turbo |
|---|---|---|---|
| 2.4G dongle / USB (XInput) | ✅ xpad 驱动满量程正常 | ❌ 标准报文无体感字段 | ❌ 不可见 |
| 蓝牙 XInput | ❌ HID 挂载失败 (br-connection-create-socket) | — | — |
| 蓝牙 DInput (BLE) | ❌ 仅 GATT，私有握手未破 | — | — |
| 蓝牙 NS (Switch Pro) | ✅ hid-nintendo 驱动 | ✅ 6轴 IMU @64Hz（需 rumble 保活防休眠） | ❌ 不可见 |
| Windows 空间站 (2.4G) | ✅ | ✅ | ✅ |

关键推论：**Windows 空间站在 2.4G 下能读到体感，说明体感数据在 2.4G 链路上存在**，
只是走的是飞智私有报文通道，Linux 标准 xpad 驱动丢弃了它。

usbmon 抓包证据（设备 045e:028e，飞智模拟 Xbox 360 手柄）：

- 所有输入报文都是 32 字节标准 XInput 格式，首字节 `0x00`
- 抓包期间无任何第二种报文、无控制传输（仅枚举时的 3 条 GET_DESCRIPTOR）
- → 手柄默认**不开**体感流，需要主机先发一条厂商使能指令

## 2. 协议依据（来自 OpenFlydigi 逆向文档）

OpenFlydigi（`/home/adrian/projects/openflydigi-main`）从空间站 4.2.0.9 反编译
得到的协议细节，直接适用于本方案：

1. **厂商指令信封（V2，32 字节）**：

   | 偏移 | 值 |
   |---|---|
   | `[0]` | 报告 ID：XInput 模式 → `0xA5` |
   | `[1..2]` | `0x5A 0xA5` |
   | `[3]` | 指令 ID |
   | `[4]` | 长度 = 负载长度 + 2 |
   | `[5..]` | 负载 |
   | `[3+len]` | `[3, 3+len)` 的 8 位累加和 |

2. **指令 17：开启 raw data 传输流**（Apex 5 上硬件验证有效）

   负载 5 字节：`controller_data, raw, keyboard, mouse, third_party`
   （1=开 / 0=关 / 0xFF=保持不变）。开启 raw=1 后，设备在输入端点输出
   `0x04` 开头、`0xEF` 标记的厂商报文，约 300Hz：

   - 偏移 4/6/8/10：左摇杆 X/Y、右摇杆 X/Y（有符号 16 位 LE）
   - 偏移 18/20/22：陀螺仪 X/Y/Z
   - 偏移 24/26/28：加速度计 X/Y/Z

   官方文档确认：**开启 raw 不影响 xpad 节点**（Apex 5 为独立厂商接口时）。

3. **冰原狼4 的通道特性**：不带飞智官方 VID (0x37d7)，空间站靠产品字符串
   `"Direwolf 4"` 识别（DInput 类）。经 2.4G dongle 时模拟 `045e:028e`，
   **单一厂商专用接口**，无独立 HID 接口 → 指令走 USB 中断 OUT 端点
   `0x05`（已确认存在，64 字节），报文从 `0x81` 读。

4. **FN/M1/M2/Turbo 假设**：这四个键在所有标准协议中不可见，但飞智 SDK 的
   键位表（27~29 键）远超标准手柄键数，推测在厂商报文中有副本。待验证。

## 3. 验证实验（下一步）

`flydigi_raw_test.py`（已写好，位于 /tmp/opencode）：

```
sudo python3 flydigi_raw_test.py
```

流程：卸载 xpad（临时，~10 秒自动恢复）→ usbfs 占用接口 → 发指令 1（设备信息，
验证通道）→ 发指令 17（开 raw 流）→ 读 0x81 分类统计报文（期间转动手柄）。

判定：

- 看到 `0x04` 首字节、含 `0xEF` 标记的报文且陀螺仪三轴随转动变化 → **假设成立**
- 指令 17 无响应 → 冰原狼4 固件不支持 → 退回蓝牙 NS 方案
- 顺带观察报文中有无 FN/M1/M2/Turbo 位段

## 4. 正式方案：单进程用户态守护

### 架构

```
手柄/dongle (USB EP 0x81)
      │ usbfs 读取（daemon 独占接口，接管 xpad）
      ▼
┌─ flydigi-daemon ─────────────────────────────┐
│ ① 报文解析: 0x00=XInput数据, 0x04=厂商数据     │
│ ② uinput 注入虚拟手柄（游戏照常识别手柄）       │
│ ③ 体感引擎: 陀螺仪→鼠标（死区/灵敏度/开关）     │
│ ④ 配置切换: FN/M1/M2 按键 或 Guide+组合键      │
│ ⑤ 自动切换: 前台窗口标题匹配 (xdotool/xprop)   │
└───────────────────────────────────────────────┘
```

**为何独占接口 + uinput，而非与 xpad 共存**：厂商报文（首字节 0x04）会被
xpad 误解析为 XInput 数据产生幻影输入，无法共存。独占后两种流都自己解析，
游戏看到的是干净的虚拟手柄。OpenFlydigi 的 `relay.py` / `uhid.py` /
`evdev.py` 已是该模式（Apex 5 实现），可直接改造。

### 功能清单

| 功能 | 状态 |
|---|---|
| 2.4G 直连，免蓝牙免保活 | ✅ USB 供电，不休眠 |
| 体感瞄准（陀螺仪→鼠标） | ✅ daemon 内实现 |
| 手柄功能不丢 | ✅ uinput 虚拟设备 |
| FN/M1/M2/Turbo 可见 | ❓ 待实验验证 |
| 多配置组 + 按键热切换 | ✅ daemon 实现 |
| 按游戏自动切配置 | ✅ 窗口匹配 |
| 键鼠映射（FPS） | ✅ daemon 实现 |

### 风险与降级路径

- 指令 17 不被冰原狼4 认可 → 放弃 2.4G 体感，退回蓝牙 NS + 保活方案（已打通）
- FN/M1/M2 不在厂商报文 → 体感照常，配置切换改用 Guide+组合键
- 虚拟手柄兼容性（极少数仅认原生 XInput 的场景）→ 个例再议
- 实验阶段需 sudo；正式化后用 udev 规则赋权给用户组，免 sudo

### 交付物（正式实施时）

```
~/.local/bin/flydigi-daemon          # 守护进程主体
~/.config/flydigi/profiles/*.yaml    # 配置组（手柄 / FPS键鼠 等）
~/.config/systemd/user/flydigi.service  # 开机自启
flydigi-ctl status|switch            # CLI 管理命令
```

## 5. 已有的替代方案（备份）

- **蓝牙 NS 模式**（已打通）：开关拨 NS 档 + 蓝牙配对 + hid-nintendo + 零强度
  rumble 保活脚本（`procon_keepalive.py`，300ms 间隔）+ Steam Input 或 antimicro。
  缺点：需保活、偶尔需重新配对、FN/M1/M2 不可见。
- **Steam Input**：Steam 内游戏可开体感 + 每游戏布局自动切换，零代码。
