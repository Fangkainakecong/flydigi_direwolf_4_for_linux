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
- 🟡 **M4 代码完成，待标定/硬件验收**：体感积分 → 鼠标、`calibrate` 三步标定、
  Home 切换 gyro 组；`tests/test_m4.py` 15 项离线全过
- 🟡 **M5 震动透传代码完成，待硬件验收**：虚拟手柄声明 `FF_RUMBLE`，
  daemon 处理游戏 FF 上传/播放，翻译成 12 字节 rumble 包；
  `SELECT+A/B/X/Y` 成功切换时会短震一次（`[feedback] profile_switch`）
- 🟡 **M6 自动启停已安装并 active**：`flydigi-watch` 按设备/操作启停
  `flydigi-dw4`，1 小时空闲退出，拔插自动恢复
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

右上角有一个小电池图标：显示 daemon/`check` 最近一次用 cmd16 读到的电量
（0..5 档换算成百分比）和固件版本；daemon 没运行过时会显示灰色 `—`。
电量来自 `/var/lib/flydigi/status.json`，`sudo python3 -m flydigi_daemon check`
也可以刷新一次。

保存时只把与 `default.toml` 不同的字段写进 `config/user.toml`（旧文件备份为
`.bak`），不会覆盖默认配置。改完重启 daemon 生效。
- ⏳ `[16]` 轴编码、体感量纲标定（公开资料只覆盖 int16 报文族，本机 int8 需自标定）
- ⏳ FF 三字节语义（`[3]/[5]/[7]`）仍需 `--sweep` 实测

## 自动启停（systemd，可选）

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

安装后由 `flydigi-watch.service` 按需启动 `flydigi-dw4.service`：

```bash
sudo scripts/install_services.sh
journalctl -u flydigi-watch -u flydigi-dw4 -f
```

行为：

- 插入 dongle → 自动启动 daemon
- 拔掉 dongle → 自动停止并恢复 xpad
- 连续 **1 小时没有真实操作** → daemon 自己退出；watch 保留，下一次
  按键/摇杆/扳机/体感转动时自动再启动
- 冰原狼4 无线约 15 分钟休眠时：daemon 继续等（最多到 1 小时），输入超时
  会先释放所有按键；1 小时后退出，醒来再按一下就会重新启动
- `config/user.toml` 可改 `[daemon] idle_exit_s`（秒，0 = 不自动退出）

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

- daemon / watch → systemd journal
- 编辑器 → `/tmp/flydigi-edit.log`（保存后自动退出）
- 编辑器日志不会常驻；只有通过快捷方式启动过才会有

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
