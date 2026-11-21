# 守护进程方案：四槽配置 + 全局键鼠注入 + 体感

> 状态：设计定稿，M0 探针已落地（未在硬件上跑）
> 日期：2026-11-19
> 前置阅读：[输入报文](input-report.md)、[老方言实测](findings-legacy-measured.md)、
> [体感分析](findings-motion.md)、[公开资料](findings-public-sources.md)
> 配置样例：[config/default.toml](../config/default.toml)

---

## 0. 一页结论

```
冰原狼4 dongle (045e:028e, EP 0x81 IN / 0x05 OUT)
        │  USBDEVFS_DISCONNECT 解绑 xpad → claim 接口 0
        ▼
  flydigi-daemon（单进程，root，systemd）
   ├─ USB 读线程：1000Hz 报文 → 解码
   ├─ 映射引擎：物理输入 → 目标（Xbox 键 / 键盘 / 鼠标 / 滚轮）
   ├─ 四槽状态机：SELECT + A/B/X/Y（软件槽，不碰固件）
   ├─ 体感引擎：陀螺仪积分 → 相对鼠标；Home 开关
   ├─ 震动翻译：FF_RUMBLE → 厂商 rumble 报文
   └─ uinput：虚拟 Xbox 手柄 + 键盘 + 鼠标
        ▼
  桌面 / Steam / RetroArch / Proton / 任意前台程序
```

四条硬结论：

1. **必须重建设备。** 用户态没有"原地改写事件"的接口；要读 M1/M2/FN/体感
   就必须独占 USB，而独占后系统里没有手柄了，只能用 uinput 补一个。
   uinput 是内核 evdev，桌面和游戏都当它是真设备，这是唯一能同时满足
   "全局可用"和"比游戏更底层"的做法。
2. **配置槽用软件槽，不用固件槽。** FN 组合属于固件（FN+A/X 会切
   XInput/DInput，FN+方向切板载配置）。daemon 只用 `SELECT + A/B/X/Y`，
   完全在主机侧切换，固件槽位原样留给 Windows。
3. **体感用 Home 开关。** 进入桌面配置时默认关闭，按一次开、再按一次关；
   开关状态跟随配置保存。
4. **震动必须翻译，不能直通。** 独占接口后 xpad 不在了，游戏看到的是虚拟
   手柄，FF 事件先到 daemon；daemon 再把强弱力度转成厂商 rumble 包。

---

## 1. 为什么必须重建手柄 + 键盘 + 鼠标

### 1.1 三条可能的路

| 方案 | 手柄 | 键鼠 | 稳定性 | 占用 | 功能 | 复杂度 |
|---|---|---|---|---|---|---|
| **A. 全虚拟（本方案）** | 解绑 xpad，uinput 虚拟 Xbox 手柄 | uinput 虚拟键鼠 | 高：uinput 是内核标准 ABI | 单核 2–5%，常驻 30–50 MB | 全部：M1/M2/FN/体感/四槽/震动 | 中 |
| B. 混合：Xbox 槽用原生 xpad | 原生 | 桌面槽才 uinput | Xbox 槽最高；切桌面要重新枚举（约 1 s） | Xbox 槽≈0 | **读不到 `[27]`、M1/M2 和体感**，切槽只能靠猜 | 高 |
| C. 内核驱动 | 改 xpad 或新 USB 驱动 | uinput 键鼠 | 最高 | 最低 | 全部 + 原生 FF | 很高（DKMS、内核升级维护） |

**选 A。** B 看起来省，但它把"M1/M2 可见"和"体感"两个核心需求砍掉了；
C 是以后想压到极致再做的优化，不是第一版该背的维护成本。

### 1.2 为什么不能"直接操控/直接映射"

- `EVIOCGRAB` 只能**截获**设备事件，不能修改后原地放回。
- XTest / `ydotool` 是 X/Wayland 层注入，只对桌面有效，进不了游戏的
  raw input / SDL 手柄路径。
- 要发"改过的事件"，只有两条路：uinput（内核 evdev）或内核驱动。

所以"重建"不是设计偏好，是接口限制。

### 1.3 占用与延迟预期

| 项 | 预期 | 依据 |
|---|---|---|
| USB 解析 | 1000 帧/s × 32 B，纯 `struct.unpack_from` | 已实测 895–1000 Hz |
| CPU | 单核 2–5%（空闲时 <1%） | 只在状态变化时写 uinput |
| 内存 | 30–50 MB | Python 运行时 + 缓冲 |
| USB → uinput 延迟 | p50 < 1 ms，p99 < 3 ms | uinput 写入是同步内核调用 |
| FF 客户端 → daemon | **实测 0.18–0.30 ms** | 本机 uinput 回路 |
| FF daemon → USB | < 1 ms（interrupt OUT） | 与 rumble 探针同路径 |

优化手段：事件合并、每帧只 `SYN_REPORT` 一次、无变化不写、缓冲区复用、
URB 保持 8–16 个在途、不开线程池。

---

## 2. 进程与线程模型

```
主线程
 ├─ 等 USB 完成事件（select/epoll on usbfs fd）
 ├─ 解码 → 状态机 → 映射 → 批量写 uinput → SYN
 ├─ 处理定时器（滚轮连发、长按、看门狗）
 └─ 处理信号（SIGTERM/SIGINT → 释放所有输出 → 恢复 xpad）

uinput FF 线程
 ├─ select(UInput.fd)
 ├─ UI_FF_UPLOAD / UI_FF_ERASE → begin/end
 └─ EV_FF 播放/停止 → 队列给主线程

配置监视线程（可选）
 └─ inotify config/default.toml → 校验 → 热加载
```

线程数固定为 2–3，不做每设备/每配置一个线程。

### 2.1 生命周期

1. 打开 `/dev/bus/usb/BBB/DDD`，`USBDEVFS_DISCONNECT` 解绑 xpad
   （失败才退回 `modprobe -r xpad`）。
2. `USBDEVFS_CLAIMINTERFACE` 接口 0。
3. 创建三台 uinput 设备（一次，配置切换不重建）。
4. 提交 8–16 个 IN URB，进入循环。
5. 退出：发中性报文 → 释放所有按键/鼠标键 → destroy uinput →
   `USBDEVFS_RELEASEINTERFACE` → `USBDEVFS_CONNECT`（或 `modprobe xpad`）。

### 2.2 看门狗

- 1.5 s 没有输入报文 → 丢弃并重挂全部 IN URB。
- 连续 3 次仍无数据 → 退出，systemd `Restart=always` 重启。
- 重启后如果 dongle 不在（比如手柄被 FN 组合切到 DInput），
  每 500 ms 重试，不刷日志。

### 2.3 为什么不用 asyncio

1000 Hz 的固定周期、少量 fd、需要严格顺序写 uinput。`select` +
同步循环更可预测，也避免 asyncio 调度抖动。Python 的 GIL 在这里不是问题：
USB 读和 uinput 写都是短调用，FF 线程 50 Hz 才动一次。

---

## 3. 输入模型

### 3.1 物理源清单

| 类别 | 源 | 说明 |
|---|---|---|
| 标准按钮（15） | `a b x y lb rb start back l3 r3 home` + 十字 `up down left right` | 来自 `[2..3]` |
| 位图专属（4） | `m1 m2 fn turbo` | 来自 `[19..20]`，标准字里没有 |
| 扳机（2） | `lt rt` | `[4][5]`，0–255 |
| 摇杆（4） | `lx ly rx ry` | `[6..13]`，int16，满量程 ±32768 |
| 体感（3） | `gyro_x gyro_y gyro_z` | `[14..16]`，int8；yaw→x，pitch→y，z 弱 |

注意：

- `[17..20]` 位图里同时有标准按钮的**副本**。板载重映射改的是副本，
  但固件同时会改标准字；两者不一致时以标准字为准（游戏看到的就是它）。
- M1/M2 只在位图里。如果板载配置把 M1/M2 映射成键盘（目标 254），
  固件就不再报它们 —— 这是启动审计必须查的。
- `[27]` 是当前**板载**槽位。它只用于启动警告和诊断，不参与切配置。
- 应答帧 `[14]==0xA5` 必须过滤掉，不能当输入解码。

### 3.2 摇杆语义（实测）

- `lx > 0` 向右，`ly > 0` 向上（XInput 语义）。
- 鼠标 `REL_Y` 向下为正 → `ly` 默认 `invert = true`。
- 死区按归一化值计算，满量程 1.0。

---

## 4. 映射 DSL

每个物理输入 → 一个目标。目标只有下面几类：

| 目标 | 写法 | 适用 | 说明 |
|---|---|---|---|
| Xbox 按钮 | `xbox:lb` `xbox:guide` | 按钮 | 进虚拟手柄 |
| Xbox 轴 | `xbox:abs_x` | 轴 | 一般不用，`passthrough` 已覆盖 |
| 键盘 | `key:space` `key:ctrl+shift+esc` | 按钮/轴阈值 | 组合键按顺序按下、反序释放 |
| 鼠标键 | `mouse:left` `mouse:right` `mouse:middle` | 按钮/轴阈值 | |
| 鼠标移动 | `mouse:x` `mouse:y` | 轴 | 相对位移 |
| 滚轮 | `wheel:up` `wheel:down` | 按钮/轴阈值 | 阈值触发 + 可连发 |
| 组开关 | `toggle:gyro` | 按钮（按下沿） | 翻转组状态 |
| 键对 | `keypair` + `neg`/`pos` | 轴 | 两个方向各一个按钮目标 |
| 无 | `none` | 全部 | 吞掉 |

### 4.1 按钮规则

- 按下沿 → 目标按下；松开沿 → 目标松开。
- 多个物理键映射到同一个目标时用**引用计数**：任一按下即输出按下，
  全部松开才输出松开。避免 M1 和 LB 同时映射 LB 时互相打架。
- `suppress_chord` 生效期间，组合键的物理键不进入映射。

### 4.2 轴规则

```toml
[profile.desktop.axis.lx]
target = "mouse:x"
deadzone = 0.12        # 归一化死区
curve = 2.0            # 输出 = sign(v) * ((|v|-dz)/(1-dz))^curve
max_speed = 1200       # 满推像素/秒
invert = false
```

按钮类目标（`key:`/`mouse:`/`wheel:`）用阈值：

```toml
[profile.desktop.axis.lt]
target = "wheel:up"
threshold = 0.35
release_threshold = 0.25   # 迟滞
repeat_hz = 18             # 按住时每秒 18 格
```

`keypair`：

```toml
[profile.desktop.axis.rx]
target = "keypair"
neg = "key:a"
pos = "key:d"
deadzone = 0.35
```

### 4.3 组与开关

- 任意按钮/轴条目可以带 `group = "gyro"`。
- 组状态由 `toggle:<组名>` 翻转，`[profile.X.groups]` 给初值（默认 true）。
- 关闭组时：所有该组条目按"松开/归零"处理，并释放它们按住的键。
- 本方案只用到一个组：`gyro`。以后要加"瞄准组/驾驶组"不用改代码。

### 4.4 `passthrough`

`passthrough = true` 表示标准输入（15 个按钮 + 6 个轴）原样进虚拟手柄；
显式条目覆盖对应输入。M1/M2/FN/Turbo 不在标准输入里，必须显式写，
否则就是死键。

### 4.5 配置校验（启动前 / `flydigi-daemon check`）

- TOML 语法、profile 引用、`slot_profiles` 长度 = 4。
- 键名必须是合法 evdev 名；未知名字报错并给最接近的建议。
- 组合键里修饰键不能重复；不允许空组合。
- 轴条目只能出现在 `axis` 下；按钮目标不能写 `mouse:x`。
- 同一个物理输入不能既在顶层又在 `axis` 里。
- 目标冲突只警告（引用计数允许共存）。
- 排查映射时可用 `run --replay ... --dry-run --profile desktop`，不碰硬件。

实现上的两个约定：`mouse:x/y` 只能用在 `axis` 下；其它 `mouse:left/right/...`
是按钮目标（可配合 `threshold` 用在轴条目）。摇杆 Y 轴在引擎内部统一成
XInput 语义（向上为正），写回手柄时再转 evdev。

---

## 5. 四槽切换：SELECT + A/B/X/Y

> **2026-11-19 实测结论（必读）**
>
> - **SELECT + A/B/X/Y 是空键位**：四个组合都只上报 `back` + 对应字母，
>   板载槽位不变、不重新枚举。**软件四槽成立，切配置零停顿。**
> - 固件自带的切槽键是 **FN + A/B/X/Y**（A→槽0、B→槽1），daemon 不拦截。
> - 之前记录的"切槽卡顿 0.7–1.0 秒"是探针自己的测量假象（`identify()`
>   消费了报文），设备实际没有掉线；见
>   [findings-slot-audit.md](findings-slot-audit.md) §7.3。
>
> 所以 **5.2 的软件槽是最终做法**；5.6 的"跟随固件槽位"已否决，仅留作记录。

### 5.1 为什么是软件槽

- **FN 组合被固件占用**：FN+A/B/X/Y 切四个板载槽。用 FN 做软件切换会和
  固件抢，而且切过去以后板载重映射会生效。
- **`SELECT + A/B/X/Y` 在固件里没有特殊含义**（实测：槽位不变、两键都上报），
  完全由 daemon 解释，切换零停顿。
- 板载槽位（`[27]`）保持不变，Windows 空间站怎么设就怎么设。
- **不要求清空板载配置**。只要固定停在槽 3、不按 FN+A/B/X，板载槽就不参与
  任何逻辑；daemon 既不读也不写它（只在启动时审计当前槽并提示）。
- 可选建议：把**槽 3** 保持干净、并在空间站里关掉板载体感→鼠标，启动审计
  就不会告警。其它三个槽可以保留你原来的设置。
- 唯一需要清理的情况：把 FN 当宏修饰键用，或经常误按 FN+A/B/X。
- FN+Y = 槽 3 目前来自你的使用经验，本仓库**尚未实测** X/Y 的对应关系
  （A→0、B→1 已实测）。

### 5.2 仲裁时序

```
t0        SELECT 按下
t0+0..200ms   等待窗口（Back 暂不输出）
  ├─ 窗口内按下 A/B/X/Y
  │     → 吞掉 SELECT 和该键，切到对应 profile
  │     → 一直吞到两键都松开（防止残余按下）
  └─ 窗口内没有组合键
        → 立即输出 Back（按配置映射）
        → 之后按 A/B/X/Y 正常输出
```

- 窗口默认 200 ms（`chord_window_ms`）：正常人手按组合键在 50 ms 内完成，
  200 ms 留足余量；代价是单独按 SELECT 最多晚 200 ms 输出。
  实测 `SELECT+A` 两键是**同时**出现在报文里的，没有先后顺序问题。
- 窗口只影响 SELECT 单独按下的延迟，不影响其它键。
- 组合判定成功时，`back` 和字母**都不输出**，直到两键都松开——否则游戏会
  收到 `Back+A`，可能触发菜单。
- 边沿检测依据：SELECT 在标准字和位图里都叫 `back`；FN 只有位图的
  `menu`（bit 24），标准字里什么都没有。两者不会混淆。
- 切槽时：释放全部键盘键/鼠标键/手柄键 → 加载新表 → 体感组复位 →
  （可选）短震确认。
- 按住 SELECT 再按两个键（如 A 然后 B）只认第一个，第二个被吞掉，
  直到全部松开。避免一次组合触发两次切换。

### 5.3 防卡键

- 所有输出都经过一个 `OutputState`，记录当前按下的键和引用计数。
- 切槽、组关闭、panic、SIGTERM、异常退出（`atexit` + 信号）都调用
  `release_all()`：键盘全 up、鼠标键全 up、手柄轴回中、再 `SYN`。
- 虚拟设备销毁前必须 `release_all()`，否则桌面会留着"按住 Shift"的状态。

### 5.4 状态持久化

`~/.local/state/flydigi/state.json`（root 服务放 `/var/lib/flydigi/state.json`）：

```json
{"profile": "desktop", "groups": {"gyro": false}}
```

- 启动时恢复上次的槽和组状态。
- 板载槽位不持久化，它由手柄自己记。

### 5.5 板载槽位审计

启动时读当前板载槽的 840 字节 blob（`flydigi_blob_audit.py` 的逻辑）：

- 键位全默认 + 体感 off → 干净，正常启动。
- 有键盘映射（目标 254）→ 警告：这些键在 Linux 下是死键，M1/M2 可能消失。
- 有宏/Turbo/板载体感 → 警告并列出，但不阻止启动（用户可能就想要）。

daemon **不写 blob**，所有映射都在主机侧，避免和 Windows 空间站互相覆盖。

### 5.6 已否决：跟随固件槽位

原方案 B 是"让 FN+ABXY 切固件槽，daemon 检测流中断后重读 `[27]` 换表"。
实测后否决，原因：

- SELECT 组合是空的，主机侧可以自己切，**零停顿**，状态完全可控；
- 跟随固件要忍受真正的中断（哪怕只有几十毫秒）、要处理 `cmd16` 偶发
  无应答、还要把四套配置写进板载槽（daemon 不写 blob 的前提下做不到）；
- 固件槽位是"设备的全局状态"，一旦用户在 Windows 空间站里改过，
  两边的配置就对不上。

保留一条防御：启动时审计**当前**板载槽，有键盘(254)/宏(32) 就警告，
因为误按 FN+X/Y 会让这些键在 Linux 下变成死键。

---

## 6. Home 键：体感开关

```toml
[profile.desktop]
home = "toggle:gyro"

[profile.desktop.groups]
gyro = false
```

| 事件 | 行为 |
|---|---|
| 进入桌面配置 | `gyro = false`（按配置初值） |
| Home 按下沿 | 翻转 `gyro`；开→短震 1 下，关→短震 2 下 |
| Home 松开 | 不做事 |
| 50 ms 内重复按下 | 忽略（去抖） |
| 切到别的配置 | 组状态按该配置的 `groups` 重新初始化 |
| daemon 重启 | 从 `state.json` 恢复（可选，默认 false 更安全） |

- 只切换 `gyro` 组，**不影响左摇杆鼠标**。要连摇杆一起关，把
  `lx/ly` 也加上 `group = "aim"`，Home 改成 `toggle:aim`。
- Xbox 配置里 Home 仍走 `xbox:guide`，不占用开关。
- **实测 Home 没有长按事件**：短按和按住 1 秒都只报 `home`。所以"长按做别的"
  必须 daemon 自己计时；本方案只取按下沿，不受影响。
- 更长时间按住 Home 是否触发关机/配对尚未测到；daemon 不依赖它。

---

## 7. 体感引擎

### 7.1 已知事实

- `[14..16]` 是 int8 角速度，约 **89 Hz 更新**（每帧重复约 11 次）。
- yaw → `[14]`，pitch → `[15]`，`[16]` 弱且编码未完全确定 → 不用。
- 静止时为零，漂移很小；没有加速度计（加速度只在 DInput 报文里，待验证）。
- 体感鼠标只需要 yaw/pitch，不需要加速度。

### 7.2 标定（`flydigi_gyro_calibrate.py`，M4）

1. 静止 3 s：记录每轴均值/噪声/漂移。
2. 慢速水平转一圈 360°（用鼠标垫上的标记对准），记录积分和 `Σcount`。
3. 慢速俯仰 90°（抬头→低头），记录积分。
4. 重复 3 次取中位数。
5. 输出 `config/calibration.json`：

```json
{"gyro": {"update_rate_hz": 89.0,
          "counts_per_degree": {"yaw": 12.5, "pitch": 11.8},
          "invert": {"yaw": false, "pitch": false},
          "noise_counts": {"yaw": 1.2, "pitch": 1.1}}}
```

### 7.3 计算

对每个**新的**体感样本（值变化才算，重复帧忽略）：

```
deg = count / counts_per_degree
px  = deg * sensitivity_px_per_deg
```

`deg` 是"这一个样本对应的角度增量"，和帧率无关；不需要乘 `dt`。
左摇杆是速度量，需要 `px = max_speed * f(v) * dt`。

- 低通：`y = a*y + (1-a)*x`，`a = smoothing`（0.35 起步）。
- 死区：`|count| < deadband` 时输出 0；超过 `deadband*1.6` 才重新启用
  （迟滞），避免静止时鼠标抖。
- 漂移：静止 200 ms 后把残余速度归零；不做自动零偏（会吃掉慢速瞄准）。
- 灵敏度：`sensitivity` 是"像素/度"，1.0 表示转 1° 鼠标走 1 px。
  360° = 360 px，配合桌面加速通常偏慢，实际从 2–4 起调。

### 7.4 与左摇杆叠加

- 两者都输出到 `mouse:x` / `mouse:y`，在**同一帧内相加**后一次性写
  `REL_X/REL_Y`，避免两次事件被桌面当成两次移动。
- `max_speed` 和 `sensitivity` 独立可调。
- 不做"摇杆优先"互斥；需要时把摇杆的 `group` 和体感设成同一组，
  用 Home 一起开关。

---

## 8. 震动透传

### 8.1 为什么不能直通

游戏 → 虚拟手柄 FF → daemon → 厂商 rumble 包 → 手柄。
xpad 已经解绑，没有"游戏直接发 USB"的路径。翻译是唯一选择。

### 8.2 uinput FF 流程（已实测）

1. 游戏 `upload_effect()` → 内核向 daemon 的 **`UInput.fd`** 发
   `EV_UINPUT / UI_FF_UPLOAD`（不在 evdev 节点上）。
2. daemon `begin_upload(id)` → 读 `effect.u.ff_rumble_effect`
   （新版本字段名可能是 `rumble`）→ `retval=0` → `end_upload()`。
3. 游戏写 `EV_FF` 播放 → daemon 记下 `strong/weak/length/repeat`。
4. daemon 以 50 Hz 发 rumble 包，直到时长结束或收到 `EV_FF value=0`。
5. 结束时发一包全零，确保马达停。

### 8.3 两个坑（本机踩过）

- **客户端 upload 会阻塞**，直到 daemon `end_upload()`。python-evdev 的
  C 调用期间不释放 GIL，所以"同一进程既当客户端又当 daemon"会死锁。
  真实场景是两个进程，没问题；测试脚本必须把客户端放子进程。
- `UI_FF_UPLOAD` 事件读的是 `UInput.fd`（原始 24 字节
  `struct input_event`），不是 `UInput.device.fd`。读错 fd 会一直等不到。

### 8.4 强度映射

- 内核给的是 `strong_magnitude` / `weak_magnitude`（0–65535）。
- 线性缩放到 0–255：`v = round(raw * 255 / 65535)`。
- 厂商包 12 字节，实测可用：

```
00 01 0F C0 00 FF 00 FF 00 00 00 00
      ↑     ↑     ↑
     [3]   [5]   [7]
```

`[3]/[5]/[7]` 的具体含义（左马达/右马达/使能）尚未确定。
`flydigi_ff_probe.py --sweep` 会逐档扫描并让你回答哪边在震，
结果写进 `captures/ff_*.json`，再回填到 `config/default.toml` 的
`strong_byte/weak_byte/enable_byte`。

### 8.5 限速与降级

- 50 Hz 发送；值不变不重复发（除了停止包）。
- 游戏可能每帧重传同一个 effect，用 `(strong, weak)` 去重。
- 只支持 `FF_RUMBLE`。`FF_PERIODIC` 等效果降级为按包络近似或忽略。
- 虚拟手柄不声明不支持的效果，避免游戏以为有而收不到。

---

## 9. 设备身份与桌面集成

### 9.1 虚拟手柄（已验证可创建）

| 项 | 值 |
|---|---|
| name | `Microsoft X-Box 360 pad` |
| bus / vendor / product / version | `BUS_USB` / `045e` / `028e` / `0114` |
| SDL GUID | `030000005e0400008e02000014010000` |
| 按钮 | `BTN_A/B/X/Y/TL/TR/SELECT/START/MODE/THUMBL/THUMBR` |
| 轴 | `ABS_X/Y/RX/RY`（-32768..32767）、`ABS_Z/RZ`（0..255）、`ABS_HAT0X/Y`（-1..1） |
| FF | `FF_RUMBLE` |

GUID 与 SDL 内置的 Xbox 360 映射完全一致，Steam 和 RetroArch 应能直接识别。
**待实测**：Steam 大屏幕、RetroArch 输入配置页、Proton 游戏的 raw input。

### 9.2 键盘 / 鼠标

| 设备 | capability |
|---|---|
| 键盘 | `EV_KEY` 1..248 + 媒体键；名字 `Flydigi DW4 Virtual Keyboard` |
| 鼠标 | `EV_KEY` 左右中侧键 + `EV_REL` X/Y/WHEEL/HWHEEL（含 HI_RES）；名字 `Flydigi DW4 Virtual Mouse` |

- 独立设备，不塞进手柄。游戏按"手柄 + 键鼠"两套输入处理，符合桌面习惯。
- 不声明 `INPUT_PROP_POINTER`；libinput 按相对轴识别为鼠标。
- 滚轮同时发 `REL_WHEEL`（1 格）和 `REL_WHEEL_HI_RES`（120），
  桌面滚动更顺。

### 9.3 桌面加速

X11（当前 XFCE）下鼠标指针会经过指针加速：

- 方案一：在 XFCE 设置里把加速关掉/调到最低，daemon 输出即最终位移。
- 方案二：保留加速，用 `sensitivity` 标定抵消。
- 游戏走 raw input 时不受 X11 加速影响，所以两种方案对游戏都成立。

### 9.4 权限

- daemon 以 root 运行（usbfs + uinput）。
- 或者：udev 规则给 `uinput` 和 dongle 的 USB 节点 `uaccess`/组权限，
  以普通用户运行。第一版用 root + systemd，简单可靠。
- udev 规则同时负责：插拔时 `TAG+="systemd"` 触发服务重启。

---

## 10. 配置文件

完整样例：[`config/default.toml`](../config/default.toml)。要点：

| 段 | 内容 |
|---|---|
| `[daemon]` | 四槽列表、启动槽、组合键、窗口、抑制 |
| `[device]` | dongle VID/PID/接口 |
| `[virtual.*]` | 三台虚拟设备的身份 |
| `[rumble]` | 包模板、字节位置、发送频率 |
| `[calibration]` | 标定文件路径 |
| `[safety]` | 看门狗、panic 组合、退出清理 |
| `[feedback]` | 切槽/体感开关的提示震动 |
| `[profile.<名>]` | 按钮映射 |
| `[profile.<名>.axis.<源>]` | 轴映射 |
| `[profile.<名>.groups]` | 组初值 |

- 热加载：文件 mtime 变化 → 解析 → 校验 → 原子替换；失败保留旧配置并记日志。
- `state.json` 只存运行时状态，不写回 TOML。
- `calibration.json` 由标定脚本生成，不进 git（`.gitignore`），
  仓库里只放 `calibration.example.json`。

---

## 11. 脚本矩阵

### 11.1 已有（本仓库）

| 脚本 | 作用 | 状态 |
|---|---|---|
| `flydigi_button_probe.py` | 全键/轴落点，摇杆转圈 | ✅ 已跑 |
| `flydigi_old_dialect_probe.py` | cmd16/80 识别与版本 | ✅ 已跑 |
| `flydigi_legacy_blob_read.py` | 读四槽 blob | ✅ 已跑 |
| `flydigi_motion_capture.py` | 体感采样与标记 | ✅ 已跑 |
| `flydigi_accel_probe.py` | 七种姿态找加速度 | ✅ 已跑（结论：标准报文无加速度） |
| `flydigi_dinput_probe.py` | DInput 是否暴露完整六轴 | ⏳ 待你切 DInput 跑 |
| `flydigi_capture_to_text.py` | 抓包转文本存档 | ✅ |
| `flydigi_usb.py` | **新**：共用 usbfs + 老方言 | ✅ 已验证语法/复用逻辑 |
| `flydigi_report.py` | **新**：32 字节报文解码 | ✅ 已用历史抓包复核 |

### 11.2 新增 M0 探针

| 脚本 | 探测什么 | 判定标准 | 产物 |
|---|---|---|---|
| `flydigi_control_probe.py` | SELECT+A/B/X/Y 是否切槽/是否漏进标准字；Home 短按/长按；FN/Turbo/M1/M2 | 组合键是否同时出现在 `[2..3]`；`[27]` 是否变化；报文是否中断 | `captures/control_*.bin/.json` |
| `flydigi_blob_audit.py` | 四槽是否"干净默认"、键盘哨兵、宏、板载体感 | 当前槽 `clean=true` 才能保证 M1/M2 可见 | `data/txt/blob_audit_*.json` |
| `flydigi_uinput_ident_probe.py` | 三台虚拟设备能否创建、GUID/节点/名称 | `/sys` 出现三台设备；GUID = `030000005e0400008e02000014010000` | `captures/uinput_ident_*.txt` |
| `flydigi_ff_probe.py` | FF 上传/播放/停止、强度映射、延迟 | `--sweep` 得出 `[3]/[5]/[7]` 语义；延迟 < 2 ms | `captures/ff_*.json` |

### 11.3 后续阶段脚本

| 脚本 | 阶段 | 作用 |
|---|---|---|
| `flydigi_gyro_calibrate.py` | M4 | 360°/90° 标定，生成 `calibration.json` |
| `flydigi_replay.py` | M3 | 回放历史 `.bin`，断言映射输出，测 CPU |
| `flydigi_latency_probe.py` | M6 | USB→uinput→evdev 的 p50/p99，对比 xpad |
| `flydigi_hotplug_probe.py` | M6 | 拔插、`kill -9`、xpad 重绑、虚拟设备清理 |
| `flydigi_config_lint.py` | M3 | 独立配置校验（也作为 `daemon check` 子命令） |

### 11.4 跑的顺序

```
1) sudo python3 scripts/flydigi_blob_audit.py        # 先确认槽位干净
2) sudo python3 scripts/flydigi_control_probe.py     # 再确认组合键行为
3) python3 scripts/flydigi_uinput_ident_probe.py --wait 60
4) sudo python3 scripts/flydigi_ff_probe.py --pad --sweep
5) （可选）sudo python3 scripts/flydigi_dinput_probe.py   # 切 DInput 后

USB 层独占是用 `USBDEVFS_DISCONNECT` 解绑内核驱动完成的，Steam 拦不住，
所以**不需要先关 Steam**（早期脚本有这条提示，已移除）。
```

---

## 12. 外部资源清单

### 12.1 已取得

| 资源 | 位置 | 提取了什么 |
|---|---|---|
| SDL Flydigi 驱动 | `SDL_hidapi_flydigi.c` | V1/V2 报文、陀螺仪/加速度量纲、FF 报告 |
| SDL 手柄 GUID 规则 | SDL2 `SDL_joystick.c` | 16 字节 GUID = bus/vendor/product/version |
| Linux `xpad.c` | 内核源码 | 360 协议、rumble OUT 包、FF 行为 |
| `uinput.h` / `input-event-codes.h` | 本机 `/usr/include/linux/` | FF ioctl、事件常量（不需要联网） |
| python-evdev 源码 | 1.4.0 / 1.7.1 | FF 上传接口、`UInput` 行为 |
| openflydigi | 本地 `reference/openflydigi/` | 老方言、840 字节 blob、键位表、体感块 |
| Vader 3 Pro DInput | `fishchev/vader-3-pro-gyro` | int16 陀螺仪、4096=1g、100/50 Hz |
| 官方规格 | 飞智官网/电商页 | 六轴、1000 Hz、霍尔扳机、双马达 |

### 12.2 待取得

| 资源 | 在哪 | 要什么 | 用途 |
|---|---|---|---|
| Space Station `app.asar` | 你的 Windows 安装目录 | `fp4` 的体感/校准/槽位逻辑、cmd80 子命令 | 确认厂商 rumble 字节、槽位切换实现 |
| Space Station SDK DLL | 同上 | 老方言命令构造器 | 只在缺命令时参考 |
| `hid-flydigi` 内核模块 | GitHub 搜索 | Vader 5 Pro 的厂商报文/FF | 对照 rumble 包和轮询率 |
| Steam 控制器配置 | Steam 安装目录 | 虚拟手柄识别条件 | 验证 GUID 是否足够 |
| RetroArch udev autoconfig | RetroArch 仓库 | 设备名/SDL 匹配规则 | 验证 RetroArch 识别 |
| gamecontrollerdb.txt | SDL 仓库 | `030000005e0400008e02000014010000` 条目 | 确认内置映射 |

### 12.3 不需要爬的

- 内核头文件（本机已有）。
- 32 字节报文布局（已实测，见 `docs/input-report.md`）。
- 老方言帧格式（已实测，见 `docs/findings-legacy-measured.md`）。

---

## 13. 已有 / 还缺

### 已有

- 32 字节报文全解码，21 个物理键全部实测。
- usbfs 异步 URB、厂商 rumble OUT、老方言命令、四槽 blob 读取。
- 体感定位（int8、89 Hz、yaw/pitch）与噪声/重复帧特征。
- 文本存档 + 回放素材（`data/txt/`、`captures/*.bin`）。
- 共用传输层、报文解码模块、四个 M0 探针、配置样例、本方案。

### 还缺

| 项 | 阻塞谁 | 怎么补 |
|---|---|---|
| SELECT 组合的实测结论 | 切槽状态机 | `flydigi_control_probe.py` |
| 当前槽是否干净 | 启动审计 | `flydigi_blob_audit.py` |
| FF 字节语义 | 震动 | `flydigi_ff_probe.py --sweep` |
| Steam/RetroArch 识别 | 桌面集成 | `uinput_ident_probe` + 手动验证 |
| 陀螺仪 counts/度 | 体感手感 | `gyro_calibrate.py`（M4） |
| DInput 完整 IMU | 可选增强 | `dinput_probe.py`（你切模式） |
| daemon 代码 | 全部 | M1–M6 |
| systemd/udev | 部署 | M7 |

---

## 14. 技术栈

| 层 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.10（系统自带） | 现有脚本同栈；1000 Hz 解码足够 |
| USB | `ctypes` + libc `ioctl` + usbfs 异步 URB | 不依赖 libusb；已在本机验证 |
| uinput | `python-evdev` ≥ 1.4 | 1.4.0 已支持 FF 上传/擦除（本机验证） |
| 配置 | TOML（`tomli` 读） | 可读、可注释；标定单独 JSON |
| 服务 | systemd + udev | root、自动重启、热插拔 |
| CLI | `argparse`：`run` / `check` / `calibrate` / `replay` / `switch` | 纯 CLI |
| 测试 | `pytest` + `.bin` 回放 | 无手柄也能回归 |
| 验证 | `evtest` / `fftest` / `libinput debug-events` / `xinput` | 标准工具 |

安装：

```bash
sudo apt install python3-evdev        # 1.4.0，够用
# 或（想用新版）
python3 -m venv .venv && .venv/bin/pip install evdev
```

不用 `libusb`/`pyusb`：多一层依赖，且对 usbfs 异步 URB 没有额外收益。
不用 `evdev` 读物理手柄：手柄走的是厂商接口，不是 evdev。

---

## 15. 里程碑与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M0** ✅ | 四个探针 + 共用模块 + 配置样例 | 组合键/槽位结论确定（SELECT+ABXY 为空键位）；GUID 正确；FF 延迟 < 2 ms |
| **M1** | 传输 + 三台 uinput + **标准输入 1:1 透传** | `evtest` 全键/全轴正常；Steam/RetroArch 识别；Ctrl-C / `kill` 后 xpad 自动恢复、无卡键 |
| **M2** | 软件四槽（SELECT+ABXY）+ Back 抑制 + 防卡键 | 四套 profile 零停顿切换；游戏收不到 `Back+A`；组合按住不放不重复切；`kill -9` 后无残留按键 |
| **M3** | 映射引擎 + TOML + lint + 回放 | 任意按钮→任意目标；回放断言通过；CPU < 5% |
| **M4** | 摇杆/体感→鼠标、扳机→滚轮、Home 开关、标定 | 桌面全局可移动/点击/滚动；360° 标定可复现 |
| **M5** | FF_RUMBLE 透传 | 游戏震动正常；延迟 p99 < 5 ms；停止无残留 |
| **M6** | systemd/udev/热插拔/延迟测试 | 拔插自动恢复；`systemctl restart` 无卡键 |
| **M7** | 文档、脚本目录、回归、推送 | README/脚本说明齐全；CI 回放通过 |

每个阶段的验收命令都写进 `docs/scripts.md`，避免"跑过了但不知道怎么复现"。

---

## 16. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| 板载配置把 M1/M2 映射成键盘 | M1/M2 消失 | 启动审计 + 明确警告；不自动改 blob |
| ~~SELECT 组合被固件解释~~ | — | **已排除**：实测四组都不切槽、不重枚举，只上报 `back` + 字母 |
| FF 字节语义不对 | 震动弱/单边/不震 | sweep 标定；保留模板可改 |
| uinput 手柄不被 Steam 识别 | Steam 里没有手柄 | GUID 已对齐；必要时写 udev/uinput 属性或让用户加映射 |
| X11 指针加速 | 体感手感不一致 | 关加速或用灵敏度抵消；游戏 raw input 不受影响 |
| 1000 Hz 偶发停顿 | 短时丢输入 | 看门狗重挂 URB；systemd 重启 |
| Wayland | 桌面注入行为不同 | uinput 与显示服务器无关；指针加速需在 compositor 调 |
| Python 性能 | 高负载掉帧 | 只在变化时写、批量 SYN、必要时把热路径移到 C |
| daemon 崩溃 | 手柄不可用/卡键 | systemd 重启；退出/异常都 `release_all()`；恢复 xpad |
| 与 Windows 空间站互改 | 配置漂移 | daemon 不写 blob；板载槽只读 |

回退路径：停掉 daemon → `modprobe xpad` → 手柄立即回到原生 Xbox 模式，
所有标准功能不受影响。

---

## 17. 目录规划

```
flydigi_direwolf_4_for_linux/
├── config/
│   ├── default.toml              # 本方案的配置样例
│   └── calibration.example.json  # 标定文件结构
├── docs/
│   ├── daemon-plan.md            # 本文
│   ├── input-report.md
│   ├── findings-*.md             # 实测记录
│   └── scripts.md                # 脚本目录
├── scripts/
│   ├── flydigi_usb.py            # 共用传输层
│   ├── flydigi_report.py         # 共用报文解码
│   ├── flydigi_control_probe.py  # M0
│   ├── flydigi_blob_audit.py     # M0
│   ├── flydigi_uinput_ident_probe.py  # M0
│   ├── flydigi_ff_probe.py       # M0
│   └── ...                       # 历史探针
├── flydigi_daemon/               # M1 起：正式代码
│   ├── __main__.py               # run/check/calibrate/replay
│   ├── transport.py              # 从 scripts/flydigi_usb.py 提升
│   ├── report.py
│   ├── mapping.py
│   ├── devices.py                # uinput 三设备
│   ├── profiles.py               # 四槽状态机
│   ├── gyro.py
│   ├── rumble.py
│   └── config.py
├── systemd/flydigi-dw4.service
├── udev/99-flydigi-dw4.rules
└── tests/
    └── replay/                   # 回放用 .bin（小样本）
```

---

## 18. 下一步

> 2026-11-19 更新：M0 已完成。`control_probe` 确认 **SELECT+A/B/X/Y 是空键位**，
> 软件四槽成立；FN+A/B/X/Y 属于固件，daemon 不碰。

### M1：透明转发（代码完成，待硬件验收）

目标：**先做到和 xpad 一样**，再加任何功能。M1 不改映射、不做体感、不做宏。

实现位置：`flydigi_daemon/`（`usb.py` / `report.py` / `translate.py` /
`devices.py` / `daemon.py` / `check.py` / `config.py` / `__main__.py`），
`scripts/flydigi_usb.py` 与 `scripts/flydigi_report.py` 保留为兼容层。
离线回归：`python3 tests/test_m1.py`。

任务：

1. **建包**：`flydigi_daemon/`
   - `usb.py` ← 由 `scripts/flydigi_usb.py` 提升（保留 `scripts/` 旧脚本可用的路径）
   - `report.py` ← 由 `scripts/flydigi_report.py` 提升
   - `devices.py`：三台 uinput（手柄 / 键盘 / 鼠标），沿用探针验证过的 capability
   - `config.py`：`tomli` 读 `config/default.toml`，先只读 `[daemon]`
   - `daemon.py`：claim → 1000 Hz 读流 → 写虚拟手柄 → SIGINT/SIGTERM 收尾
   - `__main__.py`：`python3 -m flydigi_daemon run|check`
2. **透传范围**：标准按钮字、LT/RT、左右摇杆、十字键、Home。
   M1/M2/FN/Turbo 只记日志（它们不在标准字里，等 M3 再映射）。
   已实现；`--dry-run` 会打印 `[extra] +m1` 之类。
3. **收尾**：任何退出路径都 `release_all()` + `modprobe xpad`；`SIGKILL` 没法收尾，
   所以启动时先做一次"残局清理"（若有旧虚拟设备/残留 xpad 状态）。
4. **`check` 子命令**：只读识别设备、打印槽位/固件/当前板载槽审计结果，
   不 claim、不发虚拟设备。

M1 验收（硬件上跑一次即可）：

```bash
sudo python3 -m flydigi_daemon check          # 识别、槽位、审计
sudo python3 -m flydigi_daemon run            # 另开终端：
evtest /dev/input/eventN                      # 每个键/轴都对得上
jstest /dev/input/jsN
# Steam / RetroArch 能识别为 Xbox 360 手柄
# Ctrl-C 后：xpad 自动回来，evtest 里没有卡住的键
```

### M2：软件四槽（代码完成，待硬件验收）

实现：`flydigi_daemon/slots.py`（`SlotController`），接在 `translate()`
之后、`uinput` 之前；`tests/test_m2.py` 12 项离线全过。

- 200 ms 仲裁窗口；`SELECT+ABXY` 吞掉 `back` 和字母，直到所有键松开
- `slot_profiles = [a, b, c, d]` 按 SELECT 的字母选表；状态存 `state.json`
  （root = `/var/lib/flydigi/state.json`，普通用户 =
  `~/.local/state/flydigi/state.json`，可用 `[daemon] state_file` 覆盖）
- 只认边沿：先按住 A 再按 SELECT 不算组合；窗口内按其它键会立刻放行 Back
- 切换时 `release_all()` → 换表；体感组复位、短震确认在 M4/M5 接上
- `--replay` **不写** `state.json`，避免回放旧抓包改掉用户当前槽
- 验收：按住 SELECT 连按 A/B 只切一次；`kill -9` 后重新启动无残留按键

### M3：映射 DSL（代码完成，待硬件验收）

实现：`flydigi_daemon/mapping.py`；键盘/鼠标的差量写入在 `devices.py`；
`tests/test_m3.py` 20 项离线全过。

- 已支持：按钮/扳机/摇杆 → `xbox:*`、`key:*`（含组合键）、`mouse:*`、
  `wheel:*`、`toggle:*`；轴 → `mouse:x/y`、`keypair`、阈值按键/鼠标、
  阈值滚轮（迟滞 + `repeat_hz` 连发）、`xbox:abs_*`
- `passthrough = true` 原样转发标准输入；显式条目覆盖对应输入（含轴和十字键）
- 组：按钮/轴条目可带 `group`；`toggle:<组>` 只认按下沿，关组立即释放
- 启动前和 `check` 都会 lint；有错误直接退出码 2，warnings 只提示
- `--profile NAME` 可临时指定 profile（回放/排查用，不读 `state.json`）
- 组合键先按修饰键、后按普通键，松开反序；多来源映射同一目标自动去重
- `config/user.toml` 覆盖层：深合并到 `default.toml`，只写想改的键；
  模板 `config/user.example.toml`，不进 git
- `show`：终端打印四套映射表；`show --html [--open]` 生成自包含
  `config/mappings.html`（四槽分页、按钮卡片 + 轴参数）
- 尚未包含：宏（按需再做）；**连发/涡轮由板载固件负责，daemon 不做**
- 体感轴（`gyro_x/gyro_y`）留到 M4

- 验收：任意按钮映射到任意目标；`.bin` 回放断言通过

### M4：体感与标定（代码完成，待标定/硬件验收）

实现：`flydigi_daemon/calibrate.py` + `mapping.py` 的 `_apply_gyro`；
`tests/test_m4.py` 15 项离线全过。

- 轴向沿用实测：yaw→`gyro_x`、pitch→`gyro_y`、roll→`gyro_z`（弱）
- 积分用零阶保持：`px += v × dt ÷ counts_per_degree × sensitivity`
  （报文 1 kHz、角速度约 89 Hz，重复值保持到下一拍）
- `calibrate`：静置 3 s（噪声 RMS）→ 水平 **3 整圈**（默认，可 `--turns`）→
  低头 90°；自动检测起停、挑主导轴、判符号；yaw/pitch 分别测量后按角度加权，
  差异过大时提示重做；写 `config/calibration.json`（不进 git）。
  `--estimate` 用 11-19 抓包的估算值，`--set-yaw/--set-pitch` 可直接写常数
- **官方文件里没有物理标定**：体感块只有 target/enable/deadzone/sensitivity
  （出厂 25/25、死区 8）；上游也写明 LSB-per-deg/s 无参考值。详见
  `docs/findings-motion.md` 的"官方参数"一节
- 桌面配置里 Home 按下沿切换 `gyro` 组；组关闭时条目归零并释放
- 体感与左摇杆映射到同一个 `mouse:x/y`，输出自然**相加**
- 平滑（EMA）、死区、灵敏度、反向都在 `[profile.*.axis.gyro_*]`
- 短震反馈等 M5 接上 FF 后补；现在是终端打印 `[group] gyro=on/off`
- 验收：桌面全局可移动/点击；360° 标定可复现

### M5：震动透传

- uinput `FF_RUMBLE` → 12 字节模板，50 Hz；strong/weak → 0..255
- `--sweep` 标定 `[3]/[5]/[7]` 三字节语义，结果回填 `config/default.toml`
- 验收：游戏震动正常；停止无残留；p99 < 5 ms

### M6：服务化

- systemd unit、udev 规则、安装/卸载脚本、非 root 运行（`input` 组 + uinput 权限）
- 热插拔、`systemctl restart` 无卡键、拔插自动恢复
- 验收：拔插 10 次不掉；重启服务不粘键

### M7：收尾

- README「当前状态」、`docs/scripts.md`、回归脚本、CI 回放
