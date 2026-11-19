# 需要从 Windows 拿的文件

> 用途：补齐 Linux 侧拿不到的三类信息——**出厂配置**、**厂商报文语义**、**版本对应**。
> 拿来的文件放 `windows-dump/`（已 gitignore，不会进仓库、不会公开）。
> 我们只提取协议常量和数据结构，不复制厂商代码进项目。

## 1. 必须拿（只有这一个）

| 文件 | 在 Windows 的哪里 | 大小 | 没有它会怎样 |
|---|---|---|---|
| `default_mapping_132.dat` | `<安装目录>\Configs\Controller\fp4\default\default_mapping_132.dat` | 几 KB | 只能靠 Windows 空间站恢复出厂；没法离线判断"当前槽被改过哪些字节" |

**为什么偏偏是 132**：`cmd16` 读到的 `DeviceType=132`，上游 `identity.DEVICE_TYPES`
把它命名为 `fp4` = Direwolf 4（见 `reference/openflydigi/identity.py`）。

**拿到后能得到什么**：

1. 用上游 `tools/mapping_bean.py` 翻译成 **840 字节出厂 blob**（纯 Python，无依赖）；
2. 和你现在的槽位逐字节 diff → 一眼看出你改过哪些字段（比如体感块 137..144、
   扳机马达 154..183）；
3. 此后"恢复出厂"不必进 Windows；
4. 校验我们对 `[13..` 键位表的解码是否和厂商一致。

## 2. 强烈建议（一次拿全，省得来回）

| 东西 | 位置 | 用途 |
|---|---|---|
| 整个 `Configs\Controller\` 目录（zip） | `<安装目录>\Configs\Controller\` | 里面有 fp1..fp4、k1..k6、f3..f5 各型号的出厂文件；横向对比能验证我们"哪些字段是型号差异、哪些是通用"的判断 |
| `resources\app.asar` | `<安装目录>\resources\app.asar` | 空间站的 JS 主程序。查：厂商 rumble 12 字节包的真实字节语义、`cmd80` 各子命令、体感标定流程/命令号、SELECT 相关逻辑 |
| Space Station 版本号 | 帮助 → 关于 | 和文档里引用的版本对齐 |
| 安装目录文件清单 | `dir /s /b "<安装目录>"` 的输出 | 判断有没有额外的 SDK DLL / 配置模板 |

`app.asar` 通常 30–100 MB，走 Tailscale `scp` 或共享目录都行。

如果 `app.asar` 太大或提取不顺利，退一步只要 `resources\app\`（如果未打包）
或 `resources\app.asar.unpacked\` 里的相关片段。

## 3. 可选

- `Configs\Charger\`（如果存在）——充电底座配置，和手柄本体无关，仅作参考。
- 你在空间站里为这把手柄存过的**游戏配置列表**（如果有导出功能），
  用来对照"你实际想要哪些映射"。没有也不影响，`config/default.toml` 已经覆盖。

## 4. 怎么找（Windows PowerShell）

```powershell
$roots = @(
  "C:\Program Files\Flydigi Space Station",
  "C:\Program Files (x86)\Flydigi Space Station",
  "$env:LOCALAPPDATA\Programs\Flydigi Space Station"
)
$roots | ForEach-Object { if (Test-Path $_) { "FOUND  $_" } }

Get-ChildItem -Path "C:\Program Files*","$env:LOCALAPPDATA\Programs" `
  -Recurse -Filter "default_mapping_132.dat" -ErrorAction SilentlyContinue |
  Select-Object FullName, Length

Get-ChildItem -Path "C:\Program Files*","$env:LOCALAPPDATA\Programs" `
  -Recurse -Filter "app.asar" -ErrorAction SilentlyContinue |
  Select-Object FullName, Length
```

## 5. 怎么交给我

放到本机（Linux）的这个目录，保持原文件名：

```
~/projects/flydigi_direwolf_4_for_linux/windows-dump/
├── default_mapping_132.dat
├── app.asar            （可选）
└── Controller.zip      （可选，整个 Configs\Controller）
```

传输方式任选：

- **Tailscale**（本机已在用）：Windows 上 `scp` 到 `adrian@<tailscale-ip>:~/projects/flydigi_direwolf_4_for_linux/windows-dump/`；
- U 盘 / 共享文件夹，再 `cp` 过去；
- 微信/网盘下载到 Linux 也行，只要最后落在上面那个目录。

放好之后告诉我一声（或者直接说"放好了"），我会：

1. 写 `scripts/flydigi_bean_translate.py`：
   `.dat` → 840 字节 → 打印关键区块 + 和当前槽逐字节 diff，产物存
   `data/txt/factory_fp4_*.txt`（这个进仓库，是纯数据）；
2. 用 `npx asar extract` 解开 `app.asar`，在解出的 JS 里搜
   `rumble` / `vibration` / `cmd` / `gyro` / `calibrat`，把结论写进
   `docs/findings-vendor.md`；
3. 把 rumble 三个字节的真实语义回填到 `config/default.toml`，
   把 `flydigi_ff_probe.py --sweep` 从"靠人听"降级为"仅用于验证"。
