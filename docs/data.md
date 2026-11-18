# 文本数据存档

原始抓包是二进制、体积大，只留在本地 `captures/`（已被 `.gitignore` 忽略）。
**进仓库的是文本形式**，放在 `data/txt/`，由
`scripts/flydigi_capture_to_text.py` 生成。索引见
[`data/MANIFEST.md`](../data/MANIFEST.md)。

## 为什么用文本

* 能 `grep`、`awk`、`diff`，出问题时不用写解析器；
* 体积可接受（全量约 43 MB），远小于二进制进 git 的代价；
* 文件头带源文件的 **sha256**，可以确认文本确实来自哪份二进制。

## 报文流 CSV（大多数文件）

```
1.506,0000,0,0,0,0,0,0,00000000,0,0,0,128,130,130,127
```

| 列 | 含义 |
|---|---|
| `t_ms` | 相对录制开始的毫秒数（浮点） |
| `btn` | `[2..3]` 标准按钮字，4 位十六进制（XInput 语义） |
| `lt` / `rt` | `[4]` / `[5]` 扳机模拟量 0..255 |
| `lx` `ly` `rx` `ry` | `[6..13]` 四个 `int16` 轴 |
| `keys` | `[17..20]` 飞智键位位图，8 位十六进制 |
| `raw14` `raw15` `raw16` | 三轴角速度（`int8`），见 [findings-motion.md](findings-motion.md) |
| `raw21`..`raw24` | 状态字节，正常帧恒为 `128,130,129,126` 附近 |

列定义以 [input-report.md](input-report.md) 为准。

### 两个坑

* **`raw14 == 165`（`0xA5`）的行是厂商回包**，不是手柄输入：回包被注入在
  `[14]` 之后，`[17..20]`、`[21..24]` 都会变成回包内容。这类行很少
  （几十万帧里通常只有个位数），分析时按 `raw14==165` 剔除。
* `t_ms` 是毫秒，不是秒；`1.506` 表示 1.5 毫秒。

## 老方言抓包（`old_dialect.txt`）

多一列 `phase`，记录发送了哪条命令：

```
t_ms,phase,btn,lt,rt,lx,ly,rx,ry,keys,raw14..raw16,raw21..raw24
```

## 配置 blob（`blob_slot*.txt`）

840 字节的十六进制转储，每行 16 字节：

```
0000: 01 03 54 20 04 28 a0 00 00 ff 00 00 00 ff 00 00
```

布局见 [findings-profile-blob.md](findings-profile-blob.md)。

## 重新生成

```bash
# 全部 captures/*.bin → data/txt/，并重写 data/MANIFEST.md
python3 scripts/flydigi_capture_to_text.py --all

# 单个文件
python3 scripts/flydigi_capture_to_text.py captures/motion_20261119-003907.bin

# 配置 blob
python3 scripts/flydigi_capture_to_text.py --blob captures/blob_slot0_*.bin
```

脚本会自动识别格式：小于 2000 字节按 blob 处理，`old_dialect` 按带阶段名
处理，其余按报文流处理。
