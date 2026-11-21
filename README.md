# flydigi_direwolf_4_for_linux

飞智冰原狼4 (Direwolf 4) 在 Linux 下的体感/按键映射/配置切换支持项目。
openflydigi 的分支延续：上游明确不支持老协议手柄（`045e:028e`，无 HID vendor
collection），本项目在其转录的**老方言 (XInput legacy) 协议**基础上继续。

## 目标

在 Ubuntu 22.04 上，让冰原狼4 的 2.4G dongle 达到 Windows 飞智空间站的效果：

1. **体感（陀螺仪→鼠标）**：不破坏标准手柄功能
2. **M1/M2/FN/Turbo 可见**：守护进程识别这些键
3. **按键→键鼠映射**：桌面全局生效（uinput 键盘/鼠标，不依赖 Steam Input）
4. **配置切换**：`SELECT + A/B/X/Y` 四套配置，固件槽位留给 Windows

## 当前状态 (2026-11-20)

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
- ✅ **M0 完成**：四槽审计（当前槽 3 干净）、按键复核
  （`SELECT+A/B/X/Y` 是空键位、`FN+A/B/X/Y` 才是固件切槽、Home 无长按、
  Turbo 在位图可见）、出厂 `.dat` → 840 字节翻译
- ✅ 传输层断点复核：抓包段内稳定 1000 Hz，此前"切槽卡顿 1 秒"已确认是
  探针测量假象（`scripts/flydigi_gap_report.py`）
- ✅ **M1 硬件验收通过（2026-11-20）**：`check` 识别固件 4.0.7.2、槽 3 干净；
  `run` 建出三台虚拟设备，M1/M2/FN/Turbo 在日志里可见；Ctrl-C 后 xpad 恢复
- ✅ **M2 硬件验收通过**：四槽切换、吞键、`state.json` 记忆均正常
- 🟡 **M3 代码完成，待硬件验收**：映射 DSL（按钮/轴 → 手柄、键盘、鼠标、
  滚轮、组开关），`SELECT+ABXY` 之上叠加 per-profile 映射；
  新增 `config/user.toml` 个人覆盖层和 `show` 可视化；
  `tests/test_m3.py` + `tests/test_show.py` 共 24 项离线全过
- ⏳ 体感→鼠标（M4）、FF 透传（M5）尚未实现；宏按需再加
  （**连发由板载负责**，daemon 不做软件连发）

### 快速开始

```bash
sudo python3 -m flydigi_daemon check     # 识别 + 当前板载槽审计（会短暂接管）
sudo python3 -m flydigi_daemon run       # 独占手柄；另开终端 evtest / jstest
# daemon 跑着时按 SELECT+A/B/X/Y 切四套配置，终端会打印“配置 → n/4 …”
python3 -m flydigi_daemon run --replay captures/xxx.bin --dry-run   # 无手柄回放
python3 -m flydigi_daemon run --replay captures/xxx.bin --dry-run --profile desktop
python3 tests/test_m1.py && python3 tests/test_m2.py && python3 tests/test_m3.py
python3 tests/test_show.py
```

### 自定义映射

不要直接改 `config/default.toml`（它随仓库更新）。复制模板，只写想改的键：

```bash
cp config/user.example.toml config/user.toml
```

```toml
# config/user.toml：深合并，没写的键继续用 default.toml
[profile.desktop]
a = "key:f"
home = "none"

[profile.desktop.axis.lx]
target = "keypair"
neg = "key:a"
pos = "key:d"
deadzone = 0.35
```

改完先看效果，再启动：

```bash
python3 -m flydigi_daemon show                # 终端里的四套配置表
python3 -m flydigi_daemon show --html --open  # 生成并打开可视化页面
sudo python3 -m flydigi_daemon check          # 校验 + 板载槽审计
sudo python3 -m flydigi_daemon run
```

`show --html` 生成 `config/mappings.html`（自包含单文件，不进 git），
四个槽各一页，按钮卡片 + 轴参数表，改完重新生成即可。
- ⏳ `[16]` 轴编码、体感量纲标定（公开资料只覆盖 int16 报文族，本机 int8 需自标定）
- ⏳ FF 三字节语义（`[3]/[5]/[7]`）仍需 `--sweep` 实测

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
flydigi_daemon/   M1 起的正式代码（daemon 包）
scripts/          实验脚本（需 sudo，跑完自动恢复 xpad）+ 兼容层
tests/            离线回归（不需要手柄）
config/           daemon 配置样例（default.toml、calibration.example.json）
docs/             协议文档 + 方案 + 脚本说明
reference/openflydigi/   上游确认有用的代码（参考件，import 路径未改）
captures/         原始抓包 .bin（git 忽略，仅本地）
data/txt/         抓包的文本存档（进仓库，索引见 data/MANIFEST.md）
```

上游仓库：https://github.com/（openflydigi 本地克隆在 ~/projects/openflydigi-main）
