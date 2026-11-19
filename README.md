# flydigi-dw4-linux

飞智冰原狼4 (Direwolf 4) 在 Linux 下的体感/按键映射/配置切换支持项目。
openflydigi 的分支延续：上游明确不支持老协议手柄（`045e:028e`，无 HID vendor
collection），本项目在其转录的**老方言 (XInput legacy) 协议**基础上继续。

## 目标

在 Ubuntu 22.04 上，让冰原狼4 的 2.4G dongle 达到 Windows 飞智空间站的效果：

1. **体感（陀螺仪→鼠标）**：不破坏标准手柄功能
2. **M1/M2/FN/Turbo 可见**：守护进程识别这些键
3. **按键→键鼠映射**：桌面全局生效（uinput 键盘/鼠标，不依赖 Steam Input）
4. **配置切换**：`SELECT + A/B/X/Y` 四套配置，固件槽位留给 Windows

## 当前状态 (2026-11-19)

- ✅ usbfs 异步 URB 传输层验证通过（IN 读 ~1000Hz、rumble OUT、厂商指令 ACK）
- ✅ 32 字节输入报文完全解码（标准按钮字、扳机、四轴、飞智键位位图）
- ✅ 老方言通道打通：cmd16 识别（`DeviceType=132` FP4 / 固件 4.0.7.2）、
  cmd80 sub4 配置版本、cmd33 读 4 个槽位的 840 字节配置 blob
- ✅ 体感定位：`[14..16]` 是三轴角速度（`int8`，约 89 Hz）；`[21..24]` 不是姿态
- ✅ 加速度计：**硬件是六轴**，但 2.4G XInput 32 字节报文里不输出加速度；
  同代机型在 DInput 的 HID 报文里有（待本机验证，见
  `docs/findings-public-sources.md`）
- ✅ 虚拟设备验证：uinput 能建 Xbox 手柄（GUID
  `030000005e0400008e02000014010000`）/ 键盘 / 鼠标；FF_RUMBLE 上传/播放全链路
  通，客户端→daemon 延迟 0.18–0.30 ms
- ✅ 方案定稿：[docs/daemon-plan.md](docs/daemon-plan.md)；
  配置样例：[config/default.toml](config/default.toml)
- ⏳ `[16]` 轴编码、体感量纲标定（公开资料只覆盖 int16 报文族，本机 int8 需自标定）
- ⏳ M0 硬件实测（SELECT 组合、槽位审计、FF 字节语义）尚未跑
- ⏳ 守护进程（uinput + 体感→鼠标 + 四槽切换）尚未实现

## 关键结论（详见 docs/）

- **老方言帧**：15 字节 `[0]=0xA5 [1]=cmdId [2]=sub`，无校验，写 EP 0x05；
  回包在 32 字节输入报文 **offset 14** 注入，ack 在 `[15]`
  （docs/findings-other-devices.md，Vader 4 Pro 2.4G 实测，同类情况）
- **体感不需要额外使能**：标准 XInput 报文里 `[14..16]` 一直有 int8 角速度，
  cmd17 不是开关；板载体感映射（blob @137）才决定固件是否自己用
- 识别=cmd16（DeviceType@[16], uid@17-20, 固件@21/22, 电量@23, 连接@25, 槽位@27）；
  配置版本读=cmd80 sub4；blob 读=cmd33/ack34（10 字节/包）

## 目录

```
scripts/          实验脚本（需 sudo，跑完自动恢复 xpad）+ 共用模块
config/           daemon 配置样例（default.toml、calibration.example.json）
docs/             协议文档 + 方案 + 脚本说明
reference/openflydigi/   上游确认有用的代码（参考件，import 路径未改）
captures/         原始抓包 .bin（git 忽略，仅本地）
data/txt/         抓包的文本存档（进仓库，索引见 data/MANIFEST.md）
```

上游仓库：https://github.com/（openflydigi 本地克隆在 ~/projects/openflydigi-main）
