# 范围裁剪

> 决策日期：2026-11-18
> 依据：Space Station 的四类功能 vs 冰原狼4（Direwolf 4, `fp4`）的实际硬件能力

## 四类功能取舍

| # | Space Station 功能 | 需要？ | 决定 | 理由 |
|---|---|---|---|---|
| **1** | **配置**：按键重映射 / 宏 / Turbo / 摇杆扳机曲线 / 体感参数 / 震动强度 / 多配置组与切换 | **是（最高优先级）** | **全部做** | 这是"不装 Windows"的唯一硬需求；也是标准驱动做不到的部分 |
| 2 | 外观：灯效、160×80 屏幕、底座灯 | 否 | **不做** | DW4 没有屏幕（上游 `setup.py` 明确：*A Vader or a Direwolf has neither* DualSense 模拟 nor 屏幕）；灯效非目标 |
| 3 | 特殊硬件：ForceAdapt 自适应扳机 | 否 | **不做**（见下方判定） | ForceAdapt 是 Apex 4/5 专属硬件；DW4 是霍尔扳机 + 普通马达 |
| 4 | 按游戏自动切换（进程名轮询 / 窗口标题匹配） | 否 | **不做** | 收益低，用 shell 别名或 `flydigi-ctl switch` 手工切换即可 |

## 第 3 项的判定依据

Space Station 里"特殊硬件"实际只有两样，且各自绑定一个系列：

| 能力 | 归属 | DW4 |
|---|---|---|
| `IsSupportForceTrigger`（自适应扳机，阻力/段落） | Apex 4 / 5 / 6 | ❌ |
| `IsSupportTriggerVibration`（扳机马达） | Vader 4 Pro / 5 | ❌（待实测确认） |
| `IsSupportScreen`（160×80 屏） | Apex 5 | ❌ |
| `IsSupportLed` | Apex / Vader | ✅ 但不做 |

实测确认方法（并入老方言 blob 读取实验，不单独安排）：

1. 读 840 字节 profile，检查 **offset 154..183**（`trigger motors`，左右各 2 组 7 字节）。
   factory 全 0 → 无扳机马达。
2. 检查 XInput 震动报文只有 `[3]`/`[4]` 两个强度字节（360 协议本来就无扳机通道）。

> 结论预期：第 3 项整体不做。若将来换 Vader 系列，扳机马达属于"配置/效果"范畴，
> 不是新功能类别。

## 因此项目收敛为

```
目标：用一条 2.4G dongle，在 Linux 上得到和 Windows 一样的"第一项"
       ── 改键、宏、Turbo、体感、多配置，且标准手柄功能不丢
非目标：灯效、屏幕、自适应扳机、按游戏自动切换、DualSense 模拟、宏的图形编辑
```

## 第一项的内部拆解（执行顺序）

| 子项 | 内容 | 依赖 |
|---|---|---|
| 1a | 老方言读 blob（`cmd 33/34`，10 字节小包，790/840 字节） | 待跑探针 |
| 1b | blob 解析（复用 `reference/openflydigi/mapping.py`） | 1a |
| 1c | blob 写回 + `APPLY` / `SAVE`（老方言命令 id 待实测） | 1a、1b |
| 1d | daemon：usbfs 独占 + uinput 虚拟手柄 + 主机侧键鼠注入 | 传输层（已完成） |
| 1e | 体感：陀螺仪 → 鼠标 / 右摇杆（参数存本地 yaml） | 1d |
| 1f | 多配置组 + 热切换（FN 组合键） | 1c、1d |

**注意"改键"是两半，缺一不可：**

- **手柄内**（写 blob）：只能映射到 XInput 键 / 宏 / Turbo —— 与 Windows 行为一致
- **主机侧**（daemon 注入）：键盘、鼠标按键 —— Windows 上也是 `KeyboardMouseInjectRunner`
  做的，手柄里根本不存键值

所以 1c 和 1d 必须都做，才能实现"M2 映射成键盘 V 键"这种最常见的 FPS 用法。
