# 未解项的外部资料调研（`[16]` / 体感量纲 / 加速度计）

> 日期：2026-11-19
> 问题：这些没确定的部分，能不能靠官方资料、公开代码、Windows 实测，或
> openflydigi 里的相似机型解决？

## 结论先行

| 未解项 | 公开资料能否回答 | 结论 |
|---|---|---|
| 硬件有没有加速度计 | ✅ 能 | **有**。官方/说明书/Amazon 都写六轴；飞智帮助中心写明会自动校准陀螺仪和加速度 |
| 2.4G XInput 32 字节报文里有没有加速度 | ✅ 已实测，公开代码旁证 | **没有**。只有 `[14..16]` 三轴 int8 角速度 |
| 加速度计从哪里读 | ⚠ 同代机型有先例，本机待测 | 最可能是 **DInput 模式的 HID 报文**（Vader 3 Pro：int16，4096 = 1g） |
| `[14..16]` 的量纲（dps/LSB） | ❌ 没有 | 公开代码只覆盖 int16 报文族，不覆盖冰原狼4 的 int8 扩展；只能自己做 360° 标定 |
| `[16]` 的 4 值编码 | ❌ 没有 | 同上；做体感鼠标可以直接不用 roll |
| XInput 开 raw 流的命令 | ⚠ 上游写明是 **80**（不是 17） | 可试，但 sub-id 未知，且不保证冰原狼4 固件实现了 |

一句话：**公开资料能确认"硬件有六轴"和"同代 DInput 报文长什么样"，
但没有任何资料描述冰原狼4 在 2.4G XInput 下的 int8 体感扩展。**

## 1. SDL 的 Flydigi 驱动（目前最完整的公开解析器）

文件：[`SDL_hidapi_flydigi.c`](https://github.com/libsdl-org/SDL/blob/main/src/joystick/hidapi/SDL_hidapi_flydigi.c)

它解析两代报文：

| 报文 | 识别方式 | 陀螺仪 | 加速度 |
|---|---|---|---|
| V1（老） | 首字节 `0x04`，`data[1] == 0xFE` | `int16` @ 26/18/29 | `int16` @ 11/15/13 |
| V2（新） | `5A A5` 信封，命令 `0xEF` | `int16` @ 17/19/21 | `int16` @ 23/25/27 |

量纲常量（源码原样）：

| 机型 | 加速度 | 陀螺仪 |
|---|---|---|
| Vader 3 Pro / Vader 4 Pro | `1g / 256` | `72000 / 72000 / 1200` dps（按代码） |
| Apex 5 / Apex 6 / Vader 5 Pro | `1g / 4096` | `2000` dps（三轴相同） |

**为什么不能直接拿来用**：

1. SDL 的 Flydigi 驱动只匹配 **`04b4:2412`（V1）** 和 **`37d7:24xx/25xx`（V2）**。
   冰原狼4 配对后的 2.4G 接口是 **`045e:028e`**，SDL 把它当成普通 Xbox 360 手柄，
   根本不会走 Flydigi 分支，也就永远读不到 IMU。
2. 设备表里没有 `DeviceType 132`（Direwolf 4），也没有 045e 的条目。
3. 32 字节 XInput 报文不是 SDL 的 V1/V2 报文，偏移和宽度都对不上。

## 2. Vader 3 Pro 的 DInput 逆向（同代、最接近的公开数据）

仓库：[`fishchev/vader-3-pro-gyro`](https://github.com/fishchev/vader-3-pro-gyro)

关键结论（DInput HID 报文）：

* 陀螺仪是 **12 位有符号**（yaw/pitch/roll），不是 8 位；
* 加速度是 **`int16`，4096 = 1g**，三轴都在；
* 陀螺仪约 **100 Hz** 更新，加速度约 **50–53 Hz**；
* 手柄静置几秒后，加速度会**自动校准到 1g = 4096**；
* 报文里同时有 M1–M4、C、Z、Home、FN（aeromouse）等扩展键。

这和飞智帮助中心"静止 2.5 s 自动校准陀螺仪和加速度"的说法互相印证：
**六轴 IMU 真实存在，而且固件内部一直在用加速度计。**

## 3. 官方资料

* 冰原狼4 说明书 / Amazon 商品页：`6-Axis Gyro`、`六轴体感`。
* 飞智帮助中心《摇杆、扳机和体感出现失灵、异常等怎么办》：
  "手柄静止在桌面约 2.5s 无数据变化时，自动校准陀螺仪和加速度。"
* 模式切换（飞智帮助中心快捷键说明）：
  * 2.4G **XInput**：`FN + X`，指示灯白；
  * 2.4G **DInput**：`FN + A`，指示灯蓝。

最后一条很重要：**冰原狼4 在 2.4G 下可以切 DInput**，而 DInput 正是同代机型
暴露完整 IMU 的模式。

## 4. openflydigi 里的相似机型

* `docs/findings-other-devices.md`：老协议手柄（Direwolf 4 属于这一类）**没有 HID 接口**，
  2.4G 是 `045e:028e`，命令走 xpad 的端点，回包注入在 32 字节报文的 `[14]` 之后。
  Windows 上 Space Station 能用，是因为 `xusb22` 合成了一层 HID 前端；Linux 的 xpad 不合成。
* 同文件写明 `EnableRawDataTransportIn` 的方言变体：
  **NewXInput = 17，XInput = 80，DInput = 245**。
  我们之前只试过 17（新协议信封），**没有试过 XInput 的 80**。
* 配置 blob 的 **offset 137** 是体感块，作用是把陀螺仪映射到摇杆（**pad 侧完成**）。
  所以 Space Station 的"体感设置"并不需要主机读取原始 IMU。
* Vader 4 Pro（DeviceType 85）与冰原狼4 的传输层实测完全一致，是最可靠的类比对象；
  但上游明确"不支持老协议"，`motion.py` 只实现新协议 `0x04/0xEF`，不能直接复用。

## 5. Windows 实测能回答什么

| 方法 | 能回答 | 备注 |
|---|---|---|
| USBPcap + Wireshark 抓 `045e:028e` | Space Station 到底发不发 raw 开启命令（17 还是 80）、payload 是什么、之后有没有 `0x04` 报文 | 最有决定性；只需抓"打开体感页面 + 转动手柄"这一小段 |
| 复制 `Space Station\resources\app.asar` | `fp4` 的体感/校准代码路径、命令 80 的 sub-id、是否读加速度 | 静态、零风险，信息量最大 |
| Space Station 的"手柄测试"页 | 官方 app 是否能显示加速度/陀螺仪实时值 | 如果只显示陀螺仪，说明它也没读加速度 |

## 6. 建议的验证顺序

**A. 先试 DInput（不用 Windows，2 分钟，纯被动）**

```bash
# 手柄开关拨到 2.4G，长按 FN+A，等指示灯变蓝
sudo python3 scripts/flydigi_dinput_probe.py
```

脚本只读 `/dev/hidraw*`，不发送任何命令。若看到首字节 `0x04`、长度约 32–64 的报文，
基本就能按 Vader 3 Pro 的格式解出六轴，量纲也有公开值可参考。

**B. 再考虑 Windows 抓包 / 拷 asar**

如果 DInput 不暴露 IMU，或者想坚持 XInput 单模式，就走这条：
用 USBPcap 抓 Space Station 打开体感页面的过程，重点看 EP `0x05` 的 OUT 数据。
把 pcapng 或 app.asar 放到项目里，我来解。

**C. 最后才试老方言 cmd80**

`A5 50 <sub> 01 01 FF FF FF`。sub-id 未知，盲试可能改到别的设置；
建议等 B 拿到 asar 里的 sub-id 再发。

## 7. 对 daemon 的影响

* **加速度计不是必需**：体感鼠标只用 yaw/pitch，`[16]`（roll）可以不用。
* **量纲可以自标定**：让手柄匀速转一整圈 360°，对 `[14]` 积分，即可得到
  counts/度；这比抄同代机型的常量更准，也不依赖任何外部资料。
* 所以即使 A/B 都失败，daemon 仍可按原计划用 XInput 的 `[14..16]` 工作；
  缺的只是"绝对物理单位"，不影响功能可用性。
