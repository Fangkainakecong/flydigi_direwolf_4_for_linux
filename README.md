# flydigi_direwolf_4_for_linux

飞智冰原狼4 (Direwolf 4) 在 Linux 下的体感/按键映射/配置切换支持项目。
openflydigi 的分支延续：上游明确不支持老协议手柄（`045e:028e`，无 HID vendor
collection），本项目在其转录的**老方言 (XInput legacy) 协议**基础上继续。

## 项目简介

这是一个在 **Ubuntu 22.04 / Linux** 上，为 **飞智冰原狼4（Flydigi Direwolf 4）2.4G dongle**
补齐 Windows「飞智空间站」核心能力的用户态项目。

它不修改内核驱动、不依赖 Steam Input，也不使用厂商 GUI；用 Python 直接操作
USB dongle，把输入翻译成标准的 uinput Xbox 手柄 + 键盘 + 鼠标。目标是在保留
标准手柄功能的前提下，获得：

- 体感（陀螺仪）→ 鼠标
- 手柄键 → 键盘 / 鼠标 / 滚轮 / 手柄键
- 四套配置，`SELECT + A/B/X/Y` 切换
- 游戏震动透传、切配置震动反馈
- 本地网页可视化编辑 + 自动启停

支持范围是空间站第 1 类能力（重映射/曲线/体感/配置）；灯光、屏幕、
ForceAdapt、按游戏自动切配置不在本项目范围内。

### 功能

| 功能 | 状态 |
|---|---|
| 标准 Xbox 手柄透明转发（按钮/摇杆/扳机/十字键） | ✅ 硬件验收 |
| M1/M2/FN/Turbo 可见、可映射 | ✅ |
| `SELECT+A/B/X/Y` 软件四槽 + `state.json` 记忆 | ✅ |
| 按钮/轴 → 手柄、键盘、鼠标、滚轮、组开关 | ✅ |
| 键盘组合键、鼠标方向/滚轮、轴死区/曲线 | ✅ |
| 体感 → 鼠标、三步标定、Home 开关 gyro 组 | ✅ |
| `FF_RUMBLE` 震动透传、切配置短震 | ✅ 软件链路验证 |
| 本地可视化编辑器、电量显示、保存后退出 | ✅ |
| systemd：daemon 开机自启 + udev 插拔；watch 可选 | ✅ |
| 宏 / 软件连发 | ⏳ 不做（连发由板载负责） |

### 命令速查

| 命令 | 作用 |
|---|---|
| `python3 -m flydigi_daemon check` | 识别设备、读电量/固件、审计当前板载槽 |
| `python3 -m flydigi_daemon run` | 独占 dongle，创建虚拟手柄/键盘/鼠标 |
| `python3 -m flydigi_daemon run --replay x.bin --dry-run` | 无手柄回放抓包 |
| `python3 -m flydigi_daemon calibrate` | 体感三步标定（静置/3 圈/90°） |
| `python3 -m flydigi_daemon show [--html]` | 终端/HTML 映射预览 |
| `python3 -m flydigi_daemon edit` | 启动本地网页编辑器（保存/关闭后退出） |
| `python3 -m flydigi_daemon devices` | 列出 `045e:028e`，标出会接管哪一个 |
| `python3 -m flydigi_daemon watch` | 按设备/活动启停（systemd 服务内部用） |
| `python3 -m flydigi_daemon logs [-f]` | 查看 daemon/watch/编辑器日志 |
| `python3 -m flydigi_daemon release` | 清理 XTEST 卡住的键/鼠标（通用） |

`run` 常用参数：`--profile desktop`、`--calibration path`、`--seconds N`。
`edit` 常用参数：`--port 8800`、`--no-open`、`--idle-timeout`、
`--page-close-grace`。

### 快速使用

```bash
sudo apt install python3-evdev python3-tomli
cd ~/projects/flydigi_direwolf_4_for_linux

# 1. 识别 + 当前板载槽审计（会短暂接管，再恢复 xpad）
sudo python3 -m flydigi_daemon check

# 2. 手动运行（自动启停装好后由 systemd 负责）
sudo python3 -m flydigi_daemon run

# 3. 图形化改映射（保存后自动退出）
python3 -m flydigi_daemon edit

# 4. 自动启停（已安装过可跳过）
sudo scripts/install_services.sh
python3 -m flydigi_daemon devices
```

配置默认在 `config/default.toml`；个人改动写到 `config/user.toml`
（深合并，模板 `config/user.example.toml`）。daemon 只接管产品名匹配
`Direwolf 4` 的 dongle；黑武士2（`Flydigi 2.4G Android`）不会被映射。

### 目录树

```text
flydigi_direwolf_4_for_linux/
├── README.md                         # 本文件：介绍、命令、目录、技术栈、历史
├── flydigi-mapping.desktop           # 项目内快捷方式；桌面另有一份
├── flydigi_daemon/                   # 正式 daemon（M1 起）
│   ├── __init__.py                   # 包版本
│   ├── __main__.py                   # CLI 子命令入口
│   ├── daemon.py                     # 主循环：USB 输入→映射→uinput，FF/idle/信号
│   ├── usb.py                        # usbfs ioctl/异步 URB、老方言、设备匹配、takeover
│   ├── report.py                     # 32 字节报文解码、回包识别、gyro 去回绕
│   ├── translate.py                  # PadState、报文→状态、空闲判定
│   ├── slots.py                      # SELECT+ABXY 四槽仲裁、state.json
│   ├── mapping.py                    # 映射 DSL、轴规则、lint、目标格式化
│   ├── devices.py                    # uinput Xbox/键盘/鼠标、capability、差量写入
│   ├── rumble.py                     # FF_RUMBLE 上传/播放 → 12 字节 rumble 包
│   ├── calibrate.py                  # 体感标定（静置/3 圈/90°、常数/估算）
│   ├── status.py                     # 电量/固件 status.json、网页刷新信号
│   ├── watch.py                      # dongle/活动监测，启停 systemd daemon
│   ├── config.py                     # default.toml + user.toml 深合并
│   ├── check.py                      # 识别 + 当前板载槽 blob 审计
│   ├── show.py                       # 终端映射表、自包含 HTML 可视化
│   ├── ui.py                         # 本地网页编辑器服务端
│   ├── ui.html                       # 编辑器前端（HTML/CSS/JS）
│   ├── logs.py                       # journal + 编辑器日志入口
│   └── xrelease.py                   # 通用 XTEST 幽灵按键清理
├── scripts/
│   ├── flydigi_usb.py                # 兼容层 → flydigi_daemon.usb
│   ├── flydigi_report.py             # 兼容层 → flydigi_daemon.report
│   ├── flydigi_2.4g_usbfs_probe.py   # 早期 usbfs/URB/rumble 验证
│   ├── flydigi_2.4g_raw_motion_test.py # 原始运动流实验
│   ├── flydigi_old_dialect_probe.py  # 老方言 cmd16/17/80 探针
│   ├── flydigi_legacy_blob_read.py   # 读槽位 blob 的早期脚本
│   ├── flydigi_blob_audit.py         # 四槽 840 字节审计
│   ├── flydigi_bean_translate.py     # Windows .dat → 840 字节翻译
│   ├── flydigi_button_probe.py       # 全输入/按键位图探针
│   ├── flydigi_control_probe.py      # 可控分段抓包（SELECT/ABXY/FN）
│   ├── flydigi_capture_decode.py     # 抓包解码/断点/按键统计
│   ├── flydigi_capture_to_text.py    # .bin → 文本时间线
│   ├── flydigi_gap_report.py         # 1000 Hz 断点分析
│   ├── flydigi_stick_monitor.py      # 摇杆/扳机范围监视
│   ├── flydigi_motion_capture.py     # 体感分段采集
│   ├── flydigi_motion_phases_test.py # 定时运动阶段实验
│   ├── flydigi_accel_probe.py        # `[21..24]` 姿态/加速度实验
│   ├── flydigi_dinput_probe.py       # DInput 模式探测
│   ├── flydigi_uinput_ident_probe.py # 三台 uinput capability/GUID 验证
│   ├── flydigi_ff_probe.py           # FF 上传/播放/强度扫描
│   ├── procon_keepalive.py           # Switch Pro 模式保活实验
│   ├── flydigi_edit.sh               # 编辑器启动脚本（快捷方式 Exec）
│   └── install_services.sh           # 安装/切换 daemon-only 或 watch 模式
├── systemd/
│   ├── flydigi-dw4.service.in        # daemon unit 模板（@ROOT@ 替换）
│   └── flydigi-watch.service.in      # watch unit 模板（可选）
├── udev/
│   └── 99-flydigi-dw4.rules          # dongle 插入时拉起 daemon（只匹配 Direwolf 4）
├── config/
│   ├── default.toml                  # 带注释的默认配置（四 profile）
│   ├── user.example.toml             # 个人覆盖层模板
│   ├── calibration.example.json      # 标定文件格式示例
│   ├── user.toml                     # 本地个人配置（gitignore）
│   ├── calibration.json              # 本地标定（gitignore）
│   └── mappings.html                 # show 生成的页面（gitignore）
├── tests/
│   ├── test_m1.py                    # 报文→状态、capability
│   ├── test_m2.py                    # 四槽仲裁/state
│   ├── test_m3.py                    # 映射 DSL/轴/滚轮/范围
│   ├── test_m4.py                    # 体感积分/标定
│   ├── test_rumble.py                # FF 包格式/脉冲
│   ├── test_show.py                  # 映射预览/HTML
│   ├── test_ui.py                    # 编辑器/改名/保存退出/HTTP
│   ├── test_services.py              # 设备选择/空闲判定/cfg
│   ├── test_devices_release.py       # 强制释放
│   └── test_release.py               # XTEST 释放/无 DISPLAY
├── docs/
│   ├── daemon-plan.md                # 架构/方案/M0–M7 任务与验收
│   ├── input-report.md               # 32 字节报文权威布局
│   ├── inputs-quickref.md            # 输入/体感/加速度一页速查
│   ├── findings-motion.md            # 角速度、姿态字节、加速度实测
│   ├── findings-slot-audit.md        # 四槽/按键复核/切槽假象
│   ├── findings-profile-blob.md      # 840 字节 profile 格式
│   ├── findings-onboard-remap.md     # 板载重映射字段
│   ├── findings-other-devices.md     # 同代设备/协议旁证
│   ├── findings-public-sources.md    # 外部资料（六轴/量纲）
│   ├── findings-steam.md             # Steam/SDL 行为
│   ├── findings-legacy-measured.md   # 老方言实测记录
│   ├── findings-games.md             # 游戏内验收记录
│   ├── PROTOCOL.md                   # 协议摘要
│   ├── plan.md / scope.md            # 早期方案与范围
│   ├── inventory.md / data.md        # 数据/文件清单
│   ├── scripts.md                    # 探针与 daemon 用法
│   └── windows-files.md              # Windows 侧文件说明
├── reference/openflydigi/            # 只读上游参考（不参与运行）
│   ├── mapping.py                    # 840 字节 blob 解析/写入
│   ├── mapping_bean.py               # .dat bean → blob
│   ├── motion.py                     # Apex5 厂商 motion 流参考
│   ├── evdev.py / uhid.py            # 上游 evdev/uhid 参考
│   ├── identity.py / device.py       # 识别/设备层参考
│   ├── blobs.py / effects.py         # blob/灯效参考
│   └── games.py / prefs.py           # 上游辅助模块
├── data/
│   ├── MANIFEST.md                   # 文本抓包索引
│   └── txt/                          # 文本抓包、审计 JSON、翻译结果
├── captures/                         # 原始 .bin 抓包（gitignore）
└── windows-dump/                     # Windows 厂商文件（gitignore，只读）
```

### 技术栈

- **语言**：Python 3.10（Ubuntu 22.04 自带）
- **USB**：Linux usbfs + `ctypes` ioctl，异步 interrupt URB（没有 libusb 依赖）
- **输入**：Linux evdev / uinput；虚拟 Xbox 360 手柄 GUID
  `030000005e0400008e02000014010000`
- **震动**：uinput `EV_FF` / `FF_RUMBLE`，翻译成 EP 0x05 的 12 字节厂商包
- **配置**：TOML（`tomli`），`default.toml` + `user.toml` 深合并
- **编辑器**：Python 标准库 `http.server` + 原生 HTML/CSS/JS；只监听
  `127.0.0.1`
- **服务**：systemd + journald；默认 `flydigi-dw4` 开机自启 + udev，watch 可选
- **辅助**：X11 `libXtst`（可选的 XTEST 卡键清理）、`evdev` 的 Gamepad API
  录制
- **逆向参考**：`reference/openflydigi/`（上游只读，不参与运行）

数据通路：

```text
dongle 045e:028e
  → usbfs 异步 URB (1000 Hz)
  → report.decode / translate.PadState
  → slots (SELECT+ABXY)
  → mapping DSL (按钮/轴/体感)
  → uinput 虚拟 Xbox 手柄 + 键盘 + 鼠标
  → 游戏 / 桌面

FF_RUMBLE: 游戏 → 虚拟手柄 EV_FF → rumble.py → EP 0x05 12 字节包
```

### 开发历程

- **2026-11-18**：usbfs/URB 打通，确认老方言帧与 32 字节 XInput 报文。
- **2026-11-19**：完整解码按钮/摇杆/扳机/飞智位图；cmd16/33/80 通道；
  四槽 blob 审计（槽 3 干净）；体感定位（`[14..16]` int8 角速度，
  约 89 Hz）；加速度计确认为硬件六轴但 2.4G 不输出。
- **2026-11-20**：M1 透明转发、三台 uinput、M2 软件四槽
  （`SELECT+A/B/X/Y`）硬件验收。
- **2026-11-21**：M3 映射 DSL、`user.toml`、`show`、可视化编辑器；
  M4 体感积分/三步标定/int8 回绕修复；自动启停设计。
- **2026-11-22**：M5 震动（FF_RUMBLE/切槽反馈）、电量在线刷新、
  systemd daemon-only + udev、日志/参数范围、编辑器保存/关闭退出；
  与黑武士2（`Flydigi 2.4G Android`）同插时按产品名隔离。

### 范围与限制

- 不实现灯光、屏幕、ForceAdapt、按游戏自动切 profile。
- 不实现宏；连发/turbo 由板载固件负责。
- 2.4G 报文只有三轴角速度，没有原始加速度；真正的“平移→鼠标”不可用。
- FF 目前只处理 `FF_RUMBLE`；复杂周期效果会忽略。
- 编辑器保存后自动退出；页面关闭且未修改约 20 秒后退出；默认 30 分钟
  无请求也会退出，不常驻后台。

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
- 🟡 **M4 代码完成，待标定/硬件验收**：体感积分 → 鼠标、`calibrate` 三步标定、
  Home 切换 gyro 组；`tests/test_m4.py` 15 项离线全过
- 🟡 **M5 震动透传代码完成，待硬件验收**：虚拟手柄声明 `FF_RUMBLE`，
  daemon 处理游戏 FF 上传/播放，翻译成 12 字节 rumble 包；
  `SELECT+A/B/X/Y` 成功切换时会短震一次（`[feedback] profile_switch`）
- ✅ **M6 服务化**：默认 **daemon-only**（`flydigi-dw4` 开机自启 + udev
  检测 dongle），不再常驻 watch；watch 代码保留，可用 `--watch` 切回
- ⏳ 宏按需再加（**连发由板载负责**，daemon 不做软件连发）

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

### 体感 → 鼠标

首次使用先标定（会短暂独占手柄）：

```bash
sudo python3 -m flydigi_daemon calibrate
```

按提示做三步：静置 3 秒 → 水平转 **3 整圈** → 低头约 90°。转完停住会
自动结束；起停误差摊到 1080° 上，yaw/pitch 还会互相校验。两者差得太多
（通常是把圈数数少了）会改用低头 90° 的尺度兜底；`--strict` 则直接失败、
不动原文件。产物是 `config/calibration.json`（不进 git）。

官方 Space Station 的配置里只有体感目标/开关/死区/灵敏度（出厂 25/25、
死区 8），**没有**物理标定；上游 openflydigi 也写明 Flydigi 的
LSB-per-deg/s 没有参考值。所以 counts/degree 只能测，或手动给常数：

```bash
# 不想做动作：用 2026-11-21 三次整圈标定的估算值（约 ±10%，仅起步）
python3 -m flydigi_daemon calibrate --estimate

# 以后拿到官方/数据表值，直接写
python3 -m flydigi_daemon calibrate --set-yaw 0.67 --sign-pitch -1
```

之后在 `desktop` 配置里按 **Home** 开/关体感；灵敏度/平滑/死区在
`config/user.toml` 里改：

```toml
[profile.desktop.axis.gyro_x]
sensitivity = 8.0     # 像素/度
smoothing = 0.35
deadband = 1.5        # 小于这个 raw 角速度视为噪声
```

体感会和左摇杆**相加**，两者都映射到 `mouse:x/y`，不需要二选一。

其他轴参数也都能在 `config/user.toml` 覆盖（默认值不变）：

```toml
# 左摇杆 → 鼠标：满推 1200 px/s、曲线 2.0、死区 0.12
[profile.desktop.axis.lx]
max_speed = 3000      # 想更快就加大
curve = 1.5           # 1=线性
deadzone = 0.08

# 扳机 → 滚轮：比例控制（默认 max_hz=30、curve=1.4）
[profile.desktop.axis.lt]
deadzone = 0.12
max_hz = 40           # 按到底每秒 40 格
curve = 1.5
```

`curve` 越大，轻推/轻按越慢、重推/重按越快；`show` 会显示最终生效值。

编辑器顶部「参数范围 / 适用对象」里有一张表，主要范围：

| 参数 | 范围 | 说明 |
|---|---|---|
| `deadzone` | 0–0.95 | 归一化死区；摇杆常用 0.08–0.15 |
| `deadband` | 0–20 raw | 体感角速度死区，**不是**死区百分比 |
| `curve` | 0.1–8 | 1=线性 |
| `max_speed` | 50–5000 px/s | 摇杆满推鼠标速度 |
| `sensitivity` | 0.1–30 px/° | 体感 |
| `smoothing` | 0–0.95 | 体感平滑；0=不滤波 |
| `max_hz` | 1–60 格/秒 | 比例滚轮最大速度 |
| `threshold` / `release_threshold` | 0–1 | 轴按键/固定滚轮阈值 |
| `repeat_hz` | 0–60 Hz | 固定滚轮连发；0=关 |

超出范围保存时会直接报错，不会写坏 `user.toml`。

`show --html` 生成 `config/mappings.html`（自包含单文件，不进 git），
四个槽各一页，按钮卡片 + 轴参数表，改完重新生成即可。

### 可视化编辑器（改映射不用手写 TOML）

```bash
python3 -m flydigi_daemon edit
```

会启动只监听 `127.0.0.1:8765` 的本地网页并自动打开浏览器（`--port` 换端口，
`--no-open` 不自动打开）。**保存后编辑器会自动退出**，不常驻后台；如果一直
没保存，默认 30 分钟无请求也会退出（`--idle-timeout` 可调，0 = 不超时）。
四套配置分页，**配置名可以直接改**（不再绑定 gamepad/desktop/media 的用途），
每个物理键、每个轴（摇杆/扳机/体感）的目标和全部参数都能直接改：

- 配置名：四页顶部的名字输入框，保存后同步更新 `slot_profiles` / `startup_profile`
- **标准输入原样透传**：勾选时，ABXY、LB/RB、双摇杆、LT/RT、十字键、
  Start/Back/Home 原样进虚拟手柄（显式映射优先）；取消后只输出显式映射，
  没映射的标准键会被吞掉。
- **组 gyro**：带 `group = "gyro"` 的条目（默认体感）只在该组开启时生效；
  勾选 = 该配置启动时默认开启，取消 = 默认关闭，运行时用映射到
  `toggle:gyro` 的键（桌面默认 Home）切换。四个配置都会显示这个勾选框。
- 按钮：目标字符串，留空 = 继承默认，`none` = 关闭
- **鼠标/手柄按键有下拉菜单**：按钮目标可选 A/B/X/Y/LB/RB/SELECT/START/
  HOME/L3/R3、鼠标左右中侧键、滚轮、`toggle:gyro`；轴目标可选
  `mouse:x` / `mouse:y`（鼠标左右/上下方向）、手柄轴、滚轮、keypair。
- 目标框旁的 **⌨🎮🖱** 仍然可以直接录制（适合自定义）：
  - ⌨ 键盘：组合键、单独修饰键、Esc/F1–F24/小键盘/媒体键
  - 🎮 手柄：按手柄上的 A/B/X/Y/LB/RB/十字/Start/Back/Home/L3/R3
  - 🖱 鼠标：点左/右/中/侧键
- 长按手柄按钮会**保持键盘按键按下**，松开手柄才松开键盘（组合键也一样）
- 摇杆：`deadzone` / `curve` / `max_speed` / `invert`
- 扳机滚轮：`deadzone` / `max_hz` / `curve`（比例）或 `threshold` / `repeat_hz`（固定）
- 体感：`sensitivity` / `smoothing` / `deadband` / `invert`
- 所有轴都可带 `group`

右上角有一个小电池图标：显示 daemon 用 cmd16 读到的电量（0..5 档换算成
百分比）和固件版本。daemon **每 15 分钟**非阻塞刷新一次；手柄从休眠唤醒时
会立即刷新，**点电池图标**也可以立即请求刷新。电量来自
`/var/lib/flydigi/status.json`，`sudo python3 -m flydigi_daemon check` 也会写一次。

保存时只把与 `default.toml` 不同的字段写进 `config/user.toml`（旧文件备份为
`.bak`），不会覆盖默认配置。改完重启 daemon 生效。
- ⏳ `[16]` 轴编码、体感量纲标定（公开资料只覆盖 int16 报文族，本机 int8 需自标定）
- ⏳ FF 三字节语义（`[3]/[5]/[7]`）仍需 `--sweep` 实测

## 自动启停（systemd）

默认是 **daemon-only**：`flydigi-dw4` 开机自启，udev 规则在 dongle 插入时
拉起它；`flydigi-watch` 代码保留但**不启动**，省掉一个常驻进程。

先确认会选对设备（两个手柄都插着也能看出来）：

```bash
cd ~/projects/flydigi_direwolf_4_for_linux
python3 -m flydigi_daemon devices
```

本机实测：

```
  1-1.2  Flydigi 2.4G Android   ← 黑武士2，不会接管
* 2-1    Flydigi Direwolf 4     ← 只接管这个
```

安装/切换：

```bash
sudo scripts/install_services.sh          # 默认：daemon 开机自启 + udev
sudo scripts/install_services.sh --watch  # 可选：watch 按需启停
sudo scripts/install_services.sh --remove # 卸载
journalctl -u flydigi-dw4 -f
```

默认 daemon-only 行为：

- 开机 → `flydigi-dw4` 自启（`idle_exit_s = 0`，常驻）
- 插入 dongle → udev 规则拉起 daemon
- 拔掉 dongle → daemon 退出并恢复 xpad
- 冰原狼4 无线约 15 分钟休眠时：daemon 继续运行，400 ms 没输入会先释放
  所有按键，醒来立即恢复
- 不再需要 `flydigi-watch`，节省约 12 MB 常驻内存
- 若切回 `--watch` 模式，可把 `[daemon] idle_exit_s` 设回 3600，由 watch
  在下次操作时重新启动

udev 只匹配 `Flydigi Direwolf 4`，不会因为黑武士2 插拔而启动 daemon。

### 和黑武士2 一起用

- daemon 只认产品名含 `Direwolf 4` 的 USB 设备；黑武士2 的
  `Flydigi 2.4G Android` 不匹配，永远走正常的 xpad，不经过本项目的映射。
- 接管时优先 `USBDEVFS_DISCONNECT` / 只解绑本接口的 sysfs unbind；
  只有**没有其它 xpad 手柄**时才会 `modprobe -r xpad`，所以不会把黑武士2
  一起解绑。
- 游戏里会看到两个标准 Xbox 手柄（虚拟冰原狼4 + 黑武士2），输入互不混；
  如果游戏区分 1P/2P，按需要在游戏或 Steam 里调顺序。
- 如果产品名不同，`devices` 会打印实际名字；把 `[device] product_hint`
  改成能唯一定位 dongle 的字符串即可。

## 日志

```bash
python3 -m flydigi_daemon logs          # daemon/watch journal + 编辑器日志
python3 -m flydigi_daemon logs -f       # 持续跟随
journalctl -u flydigi-watch -u flydigi-dw4 -f
```

- daemon / watch → systemd journal（自动写入；当前总 journal 约 64 MB）
- 编辑器 → `/tmp/flydigi-edit.log`（保存后自动退出）；超过 256 KiB 轮转为
  `.log.1`，最多约 512 KiB；`/tmp` 还会被系统按默认策略清理
- 编辑器日志只在通过快捷方式启动时产生
- journald 默认没有按时间删除的上限，只有磁盘比例上限。要收紧全局日志：
  `sudo journalctl --vacuum-size=200M`、`sudo journalctl --vacuum-time=14d`，
  或在 `/etc/systemd/journald.conf` 设 `SystemMaxUse=200M` /
  `MaxRetentionSec=14day` 后重启 `systemd-journald`（影响所有系统日志）

## 故障排查：Ctrl/Shift 卡住、鼠标左右键错乱

如果是本项目的 daemon 造成的，它会在 400 ms 没有手柄输入时自动释放所有
按键/鼠标；退出/切配置也会强制全量松开。

如果是其他 XTEST 注入工具留下的“幽灵按下”，可以手动清理（通用命令，
不针对任何具体软件）：

```bash
python3 -m flydigi_daemon release
```

它只释放 X 当前确实按下的 keycode/鼠标按钮，不需要 root。

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
