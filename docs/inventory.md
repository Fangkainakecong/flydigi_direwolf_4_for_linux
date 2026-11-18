# 上游 openflydigi 可用资源清单

> 目标：冰原狼4 (Direwolf 4, `fp4`) 2.4G 在 Linux 下的体感/按键/配置适配
> 上游：`~/projects/openflydigi-main`（完整克隆，只读参考；有用文件已拷贝到本项目）
> 盘点日期：2026-11-18

## 一、上游对手柄的定位（决定了"能用什么"）

- `IsOldProtocol() = (VendorId != 0x37D7)`。**冰原狼4 是老协议（XInput legacy）**：
  2.4G dongle 枚举为 `045e:028e`，无 HID vendor collection，xpad 占用 —— 上游
  `find_device` 永远匹配不到，整个驱动链路（relay/daemon/GUI）**不可用**
- 支持列表只有 `k5`(Apex5)/`f5`(Vader5)，老协议手柄**有意不支持**
- 但上游用 **Vader 4 Pro + 2.4G dongle（同类情况）实测并转录了老方言协议**，
  见 `docs/findings-other-devices.md`

## 二、直接可用的协议知识（docs/）

| 文件 | 内容 | 对我们的用处 |
|---|---|---|
| `findings-other-devices.md` | **老方言实测**：15 字节帧 `A5 cmd sub`、回包注入 offset 14、命令表（识别 16 / blob 33-34 / 配置版本 80sub4 / ADC 校准 20 / 体感流开关 80） | ⭐ 核心：我们的命令号全部按这个改 |
| `findings-profile-blob.md` | **840 字节配置 blob 完整布局**：键位表@13（32×3）、体感块@137、宏@230、标题@770 | ⭐ 核心：按键映射/配置文件的数据结构 |
| `PROTOCOL.md` | 信封格式、校验、各命令负载、报告 ID | 协议总汇 |
| `findings-steam.md` | cmd17/cmd16 细节、raw data 五个开关位语义 | 体感流开关语义 |
| `findings-games.md` | 游戏检测/自动切换路由（94 款游戏列表 API） | 自动切配置参考 |

## 三、可复用的代码（reference/openflydigi/）

| 文件 | 作用 | 复用方式 |
|---|---|---|
| `mapping.py` | **840 字节 blob 读写**：键位表/连发/体感块/宏/曲线/标题，含 `TARGET_*`、`KEY_NAMES`(M1-M6,Fn=24,Turbo=25)、`read_status`(活动槽位) | 布局解析直接可用；命令号和传输层需换老方言 |
| `blobs.py` | 分包传输 blob，**`pkg_size` 参数已支持老手柄 10 字节/包** | 传输流程参考 |
| `identity.py` | DeviceType→型号表（**fp4=132/146/147/148**，f4=85） | 识别/防写错型号 |
| `motion.py` | cmd17 负载五开关、0x04 报文解析（新协议） | 开关语义；老协议报文需另解 |
| `device.py` | hidraw 传输参考：`build/checksum`、发送/等回包 | 构造参考（我们走 usbfs） |
| `evdev.py` | evdev 枚举/读键/UInput 映射表 | 虚拟手柄按键映射参考 |
| `uhid.py` | 纯 Python 虚拟 HID 设备（可收输出报文） | 将来若要模拟 DualSense |
| `effects.py` | 震动/扳机效果词汇表 | 震动透传参考 |
| `prefs.py` + `games.py` | per-game 自动模式偏好、游戏列表 API | 自动切配置参考 |

## 四、明确不可用

- `relay.py` / daemon / GUI / screen / lighting / haptics / ds5*：Apex 专属硬件 +
  hidraw 传输 + `SUPPORTED` 门槛
- `tools/flydigi_cmd.py`：hidraw + `37d7:2501` 专用
- `factory_config.py`：只有 k5/f5 出厂 blob，**无 fp4** → 我们自己读手柄配置做备份
- `mapping.py` 的命令号（161-175 等）是 NewXInput 的，老方言要换

## 五、关键结论（影响方案）

1. **键盘/鼠标映射在 Windows 也是主机侧注入**（`KeyboardMouseInjectRunner`），
   pad 只存"映射到哪个 XInput 键" → Linux 下必须我们自己做 uinput 注入，
   与 `docs/plan.md` 的守护进程架构一致
2. **pad 侧配置能做的**（写 blob 即可，无需主机）：M1-M6 改映射到任意 XInput 键、
   连发（turbo）、宏、体感映射到摇杆（体感块@137）、4 套配置切换（Fn 键由固件切换）
3. **配置切换是固件行为**：Fn/Menu 键在 pad 内切换，主机要读活动槽位
   （`read_status`，活动槽位也出现在输入报文注入区 @27 附近）
4. 老协议标识：`findings-other-devices.md` 记录 Vader 4 Pro DeviceType=85；
   冰原狼4 应在 132/146/147/148 中 → 用 cmd16 实测确认

## 六、下一步

1. 跑 `scripts/flydigi_old_dialect_probe.py`：验证 cmd16 回包注入 + 拿 DeviceType
2. 扩展探测：老方言 cmd33 读 blob（4 个槽位），用 `mapping.py` 解析出当前
   M1/M2/Fn/Turbo 映射、体感块、宏 → 得到真实配置结构
3. 解体感字节布局（`captures/motion_phases.bin`）——注入区 [14..] 的原始数据帧格式
4. 按 `docs/plan.md` 建守护进程：独占 usbfs + uinput 虚拟手柄 + 体感→鼠标 + 配置跟随
