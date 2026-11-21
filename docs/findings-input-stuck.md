# 卡键 / 鼠标左右键错乱：XTEST 幽灵按键

> 日期：2026-11-21
> 现象：Ctrl/Shift 像被一直按住；鼠标左键表现为右键；重启电脑恢复。
> 硬件检查无异常。

## 现场证据

出问题时在本机抓到的状态：

* `xinput list` 里只有 `Virtual core XTEST keyboard/pointer` 和各物理设备，
  **没有** Flydigi daemon 的虚拟键盘/鼠标（当时 daemon 没在运行）。
* XTEST 键盘上 `Shift_R`（keycode 62）和 `Control_R`（105）处于按下状态，
  物理键盘没有任何键按下。
* 核心指针上鼠标按钮 **1、3、5** 同时按下，所有真实设备都显示已松开。
* `xmodmap -pm` 里 `Shift_R`、`Control_R` 确实属于 shift/control 修饰键。

## 来源

本机在跑 **Deskflow 1.27.0（Flatpak）客户端**：

```
/app/bin/deskflow-core client
remoteHost=192.168.5.110   # ~/.var/app/org.deskflow.deskflow/config/Deskflow/Deskflow.conf
```

Deskflow 客户端把远端机器的键鼠通过 **XTEST** 注入本地 X。连接中断/切换时，
注入端可能只发出了按下、没有配对发出松开，于是 X 服务器里留下“幽灵按下”。
这与“重启电脑就好”完全一致：XTEST 状态只在 X 会话里，重启即清空。

另外本机还在跑 `xcape -e Super_L Control_L Escape`（也是 XTEST 客户端，
只监听 Control_L），不是本次 Shift_R/Control_R 的来源，但同属一类风险。

## 修复

### 1. 不用重启，立即释放

```bash
cd ~/projects/flydigi_direwolf_4_for_linux
python3 -m flydigi_daemon release
```

它通过 `libXtst` 对全部 keycode 和鼠标按钮 1..32 发释放事件，只影响 XTEST
的全局状态，不动物理设备，不需要 root。`--dry-run` 只看不执行。

### 2. 断线自动释放（可选常驻）

```bash
python3 scripts/deskflow_release_watch.py
```

每 0.5 秒检查 24800 端口，连接从“已建立”变成“断开”时自动调用同一套释放
逻辑。它只处理断线瞬间；连接没断但已经卡住时，仍用第 1 步。

### 3. Deskflow 侧

* 重启客户端：`flatpak kill org.deskflow.deskflow`，再从托盘/应用菜单启动。
* 检查更新：`flatpak update org.deskflow.deskflow`。
* 如果只在用手柄时出现，可临时停掉 Deskflow 复现对比；
  它的 autostart 在 `~/.config/autostart/deskflow.desktop`。

## 本项目的防御

即使问题来自 XTEST，daemon 侧也补了三条：

1. `release_all()` 不再依赖内部差量状态，退出/切配置时**强制**给全部按键、
   鼠标按钮、手柄按钮发松开；
2. 400 ms 没有输入（手柄休眠、2.4G 掉线）就自动释放所有输出，输入恢复后
   再按新帧重新按下；由 `[safety] input_timeout_ms` 控制；
3. `SIGHUP`/`SIGQUIT`/`SIGTERM`/`SIGINT` 和 `atexit` 都走收尾路径。

Flydigi 的虚拟设备是 uinput 设备，不是 XTEST；两者独立。上面的证据表明
本次卡键来自 Deskflow 的 XTEST，但 daemon 的这三条能避免同类“设备移除时
按键没释放”的问题。
