# flydigi-dw4-linux

飞智冰原狼4 (Direwolf 4) 在 Linux 下的体感/按键映射/配置切换支持项目。
openflydigi 的分支延续：上游明确不支持老协议手柄（`045e:028e`，无 HID vendor
collection），本项目在其转录的**老方言 (XInput legacy) 协议**基础上继续。

## 目标

在 Ubuntu 22.04 上，让冰原狼4 的 2.4G dongle 达到 Windows 飞智空间站的效果：

1. **体感（陀螺仪→鼠标）**：不破坏标准手柄功能
2. **M1/M2/FN/Turbo 可见**：守护进程识别这些键
3. **按键→键鼠映射**：按配置文件生效（对标空间站的 per-game 映射）
4. **配置切换**：按键触发 profile 切换

## 当前状态 (2026-11-19)

- ✅ usbfs 异步 URB 传输层验证通过（IN 读 ~1000Hz、rumble OUT、厂商指令 ACK）
- ✅ 32 字节输入报文完全解码（标准按钮字、扳机、四轴、飞智键位位图）
- ✅ 老方言通道打通：cmd16 识别（`DeviceType=132` FP4 / 固件 4.0.7.2）、
  cmd80 sub4 配置版本、cmd33 读 4 个槽位的 840 字节配置 blob
- ✅ 体感定位：`[14..16]` 是三轴角速度（`int8`，约 89 Hz）；`[21..24]` 不是姿态
- ✅ 加速度计：标准 32 字节报文内**没有**加速度数据（按键/轴/体感速查见
  `docs/inputs-quickref.md`）
- ⏳ `[16]` 轴编码、体感量纲标定
- ⏳ 守护进程（uinput + 体感→鼠标 + profile 切换）尚未实现

## 关键结论（详见 docs/）

- **老方言帧**：15 字节 `[0]=0xA5 [1]=cmdId [2]=sub`，无校验，写 EP 0x05；
  回包在 32 字节输入报文 **offset 14** 注入，ack 在 `[15]`
  （docs/findings-other-devices.md，Vader 4 Pro 2.4G 实测，同类情况）
- **EnableRawDataTransportIn（体感流开关）命令号按方言分**：
  NewXInput=17, **XInput=80**, DInput=245
- 识别=cmd16（DeviceType@[16], uid@17-20, 固件@21/22, 电量@23, 连接@25, 槽位@27）；
  配置版本读=cmd80 sub4；blob 读=cmd33/ack34（10 字节/包）；摇杆 ADC 校准=cmd20

## 目录

```
scripts/          实验脚本（需 sudo，跑完自动恢复 xpad）
docs/             协议文档 + 方案 + 脚本说明
reference/openflydigi/   上游确认有用的代码（参考件，import 路径未改）
captures/         原始抓包 .bin（git 忽略，仅本地）
data/txt/         抓包的文本存档（进仓库，索引见 data/MANIFEST.md）
```

上游仓库：https://github.com/（openflydigi 本地克隆在 ~/projects/openflydigi-main）
