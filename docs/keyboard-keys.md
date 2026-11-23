# 键盘按键对照表（映射用）

写映射时用 `key:英文名`，例如 `dpad_up = "key:pageup"`、`x = "key:ctrl+alt+k"`。大小写不敏感。

> 本文档不是 README，只是按键速查表；写入 `config/user.toml` 即可生效。

## 三条规则

1. **映射的是键位，不是字符。** `key:1` 就是数字 1 那个键位；要打 `!` 得写 `key:shift+1`，要打 `+` 得写 `key:shift+equal`。输入法（fcitx/ibus）和键盘布局在按下时才决定最终字符。
2. **组合键用 `+` 连接，顺序无关**：`key:ctrl+alt+k`、`key:shift+alt+delete`。修饰键要写在组合里，单独映射修饰键会一直按住。
3. **只有键码 1–248 真正可用**：虚拟键盘设备只声明了这一段（另含 6 个媒体键，都已在下表内）。表外名字能解析但不会发出事件。

## 常用写法

| 目的 | 写法 |
|------|------|
| 回车 / 退格 / 删除 | `key:enter` / `key:backspace` / `key:delete` |
| 复制 / 粘贴 / 撤销 | `key:ctrl+c` / `key:ctrl+v` / `key:ctrl+z` |
| 全选 | `key:ctrl+a` |
| 大写字母 A | `key:shift+a` |
| 符号 + / ! / @ | `key:shift+equal` / `key:shift+1` / `key:shift+2` |
| 切换窗口 | `key:alt+tab` |
| 关闭窗口 | `key:alt+f4` |
| 音量 | `key:volumeup` / `key:volumedown` / `key:mute` |
| 播放控制 | `key:playpause` / `key:nextsong` / `key:previoussong` |

## 别名

下面这些写法等价，条目标题里写的是显示用的正式名：

| 正式名 | 等价写法 |
|--------|----------|
| `esc` | `escape` |
| `enter` | `return` |
| `delete` | `del` |
| `leftctrl` | `ctrl` |
| `leftshift` | `shift` |
| `leftalt` | `alt` |
| `leftmeta` | `win` |
| `leftmeta` | `super` |
| `leftmeta` | `meta` |
| `pageup` | `pgup` |
| `pagedown` | `pgdn` |
| `kpplus` | `kp_plus` |
| `kpminus` | `kp_minus` |
| `kpdot` | `kp_dot` |
| `kpenter` | `kp_enter` |
| `kpmultiply` | `kp_multiply` |
| `kpdivide` | `kp_divide` |
| `equal` | `plus` |

另有一批 evdev 定义的同键码别名：

| 正式名 | 等价写法 |
|--------|----------|
| `mute` | `min_interesting` |
| `hangeul` | `hanguel` |
| `coffee` | `screenlock` |
| `direction` | `rotate_display` |
| `all_applications` | `dashboard` |
| `brightness_auto` | `brightness_zero` |
| `wimax` | `wwan` |

## 字母

28 个字母键，直接写单个字母。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 字母 A | `key:a` | 30 |  |
| 字母 B | `key:b` | 48 |  |
| 字母 C | `key:c` | 46 |  |
| 字母 D | `key:d` | 32 |  |
| 字母 E | `key:e` | 18 |  |
| 字母 F | `key:f` | 33 |  |
| 字母 G | `key:g` | 34 |  |
| 字母 H | `key:h` | 35 |  |
| 字母 I | `key:i` | 23 |  |
| 字母 J | `key:j` | 36 |  |
| 字母 K | `key:k` | 37 |  |
| 字母 L | `key:l` | 38 |  |
| 字母 M | `key:m` | 50 |  |
| 字母 N | `key:n` | 49 |  |
| 字母 O | `key:o` | 24 |  |
| 字母 P | `key:p` | 25 |  |
| 字母 Q | `key:q` | 16 |  |
| 字母 R | `key:r` | 19 |  |
| 字母 S | `key:s` | 31 |  |
| 字母 T | `key:t` | 20 |  |
| 字母 U | `key:u` | 22 |  |
| 字母 V | `key:v` | 47 |  |
| 字母 W | `key:w` | 17 |  |
| 字母 X | `key:x` | 45 |  |
| 字母 Y | `key:y` | 21 |  |
| 字母 Z | `key:z` | 44 |  |

## 数字行

主键盘区 1–0（不含小键盘）。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 数字 1 | `key:1` | 2 |  |
| 数字 2 | `key:2` | 3 |  |
| 数字 3 | `key:3` | 4 |  |
| 数字 4 | `key:4` | 5 |  |
| 数字 5 | `key:5` | 6 |  |
| 数字 6 | `key:6` | 7 |  |
| 数字 7 | `key:7` | 8 |  |
| 数字 8 | `key:8` | 9 |  |
| 数字 9 | `key:9` | 10 |  |
| 数字 0 | `key:0` | 11 |  |

## 符号与标点

映射的是**键位**，不是字符；大写/上档符号要配 `shift`。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 减号 - | `key:minus` | 12 |  |
| 等号 = | `key:equal` | 13 |  |
| 左方括号 [ | `key:leftbrace` | 26 |  |
| 右方括号 ] | `key:rightbrace` | 27 |  |
| 分号 ; | `key:semicolon` | 39 |  |
| 单引号 ' | `key:apostrophe` | 40 |  |
| 反引号 ` | `key:grave` | 41 |  |
| 反斜杠 \ | `key:backslash` | 43 |  |
| 逗号 , | `key:comma` | 51 |  |
| 句点 . | `key:dot` | 52 |  |
| 斜杠 / | `key:slash` | 53 |  |

## 编辑与导航

光标移动、翻页、删除等。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| Esc 退出 | `key:esc` | 1 |  |
| Tab 制表 | `key:tab` | 15 |  |
| 回车 Enter | `key:enter` | 28 |  |
| 退格 Backspace | `key:backspace` | 14 |  |
| 空格 | `key:space` | 57 |  |
| 大写锁定 | `key:capslock` | 58 |  |
| 插入 Insert | `key:insert` | 110 |  |
| 删除 Delete | `key:delete` | 111 |  |
| Home 行首 | `key:home` | 102 |  |
| End 行尾 | `key:end` | 107 |  |
| PageUp 上翻页 | `key:pageup` | 104 |  |
| PageDown 下翻页 | `key:pagedown` | 109 |  |
| 方向键 ↑ | `key:up` | 103 |  |
| 方向键 ↓ | `key:down` | 108 |  |
| 方向键 ← | `key:left` | 105 |  |
| 方向键 → | `key:right` | 106 |  |
| 菜单键 | `key:menu` | 139 |  |
| 打印/截屏 Print | `key:print` | 210 |  |
| 暂停媒体 | `key:pause` | 119 |  |
| 系统请求 SysRq | `key:sysrq` | 99 |  |
| 102 键（\ 旁） | `key:102nd` | 86 |  |
| 组合键 Compose | `key:compose` | 127 |  |
| 换行 LineFeed | `key:linefeed` | 101 |  |
| 帮助 Help | `key:help` | 138 |  |

## 功能键

F1–F24。多数笔记本只有 F1–F12。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 功能键 F1 | `key:f1` | 59 |  |
| 功能键 F2 | `key:f2` | 60 |  |
| 功能键 F3 | `key:f3` | 61 |  |
| 功能键 F4 | `key:f4` | 62 |  |
| 功能键 F5 | `key:f5` | 63 |  |
| 功能键 F6 | `key:f6` | 64 |  |
| 功能键 F7 | `key:f7` | 65 |  |
| 功能键 F8 | `key:f8` | 66 |  |
| 功能键 F9 | `key:f9` | 67 |  |
| 功能键 F10 | `key:f10` | 68 |  |
| 功能键 F11 | `key:f11` | 87 |  |
| 功能键 F12 | `key:f12` | 88 |  |
| 功能键 F13 | `key:f13` | 183 |  |
| 功能键 F14 | `key:f14` | 184 |  |
| 功能键 F15 | `key:f15` | 185 |  |
| 功能键 F16 | `key:f16` | 186 |  |
| 功能键 F17 | `key:f17` | 187 |  |
| 功能键 F18 | `key:f18` | 188 |  |
| 功能键 F19 | `key:f19` | 189 |  |
| 功能键 F20 | `key:f20` | 190 |  |
| 功能键 F21 | `key:f21` | 191 |  |
| 功能键 F22 | `key:f22` | 192 |  |
| 功能键 F23 | `key:f23` | 193 |  |
| 功能键 F24 | `key:f24` | 194 |  |

## 修饰键

单独映射会真的按住；和别的键同时按可组组合键。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 左 Ctrl | `key:leftctrl` | 29 |  |
| 右 Ctrl | `key:rightctrl` | 97 |  |
| 左 Shift | `key:leftshift` | 42 |  |
| 右 Shift | `key:rightshift` | 54 |  |
| 左 Alt | `key:leftalt` | 56 |  |
| 右 Alt | `key:rightalt` | 100 |  |
| 左 Win/Super | `key:leftmeta` | 125 |  |
| 右 Win/Super | `key:rightmeta` | 126 |  |

## 小键盘

需要键盘有数字小键盘，或在 X 里打开 NumLock。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 数字锁定 | `key:numlock` | 69 |  |
| 滚动锁定 | `key:scrolllock` | 70 |  |
| 小键盘 7 | `key:kp7` | 71 |  |
| 小键盘 8 | `key:kp8` | 72 |  |
| 小键盘 9 | `key:kp9` | 73 |  |
| 小键盘 - | `key:kpminus` | 74 |  |
| 小键盘 4 | `key:kp4` | 75 |  |
| 小键盘 5 | `key:kp5` | 76 |  |
| 小键盘 6 | `key:kp6` | 77 |  |
| 小键盘 + | `key:kpplus` | 78 |  |
| 小键盘 1 | `key:kp1` | 79 |  |
| 小键盘 2 | `key:kp2` | 80 |  |
| 小键盘 3 | `key:kp3` | 81 |  |
| 小键盘 0 | `key:kp0` | 82 |  |
| 小键盘 . | `key:kpdot` | 83 |  |
| 小键盘 * | `key:kpasterisk` | 55 |  |
| 小键盘 / | `key:kpslash` | 98 |  |
| 小键盘回车 | `key:kpenter` | 96 |  |
| 小键盘 = | `key:kpequal` | 117 |  |
| 小键盘 , | `key:kpcomma` | 121 |  |
| 小键盘 ( | `key:kpleftparen` | 179 |  |
| 小键盘 ) | `key:kprightparen` | 180 |  |
| 小键盘 ± | `key:kpplusminus` | 118 |  |
| 小键盘逗号（日） | `key:kpjpcomma` | 95 |  |

## 媒体与音量

走系统媒体键，能不能生效取决于播放器/桌面。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 静音 | `key:mute` | 113 | 别名 `min_interesting` |
| 音量 - | `key:volumedown` | 114 |  |
| 音量 + | `key:volumeup` | 115 |  |
| 播放/暂停 | `key:playpause` | 164 |  |
| 下一首 | `key:nextsong` | 163 |  |
| 上一首 | `key:previoussong` | 165 |  |
| 停止 | `key:stop` | 128 |  |
| 播放 | `key:play` | 207 |  |
| 录音 | `key:record` | 167 |  |
| 快退 | `key:rewind` | 168 |  |
| 快进 | `key:fastforward` | 208 |  |
| 播放 CD | `key:playcd` | 200 |  |
| 暂停 CD | `key:pausecd` | 201 |  |
| 停止 CD | `key:stopcd` | 166 |  |
| 弹出光驱 | `key:ejectcd` | 161 |  |
| 弹出/关闭光驱 | `key:ejectclosecd` | 162 |  |
| 关闭光驱 | `key:closecd` | 160 |  |
| 媒体中心 | `key:media` | 226 |  |
| 麦克风静音 | `key:micmute` | 248 |  |
| 低音增强 | `key:bassboost` | 209 |  |
| 声音 | `key:sound` | 213 |  |
| 下一个视频 | `key:video_next` | 241 |  |
| 上一个视频 | `key:video_prev` | 242 |  |
| 向上滚动 | `key:scrollup` | 177 |  |
| 向下滚动 | `key:scrolldown` | 178 |  |

## 浏览器与办公

网页/邮件/剪贴板类按键，需要应用自己响应。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 浏览器 | `key:www` | 150 |  |
| 主页 | `key:homepage` | 172 |  |
| 后退 | `key:back` | 158 |  |
| 前进 | `key:forward` | 159 |  |
| 刷新 | `key:refresh` | 173 |  |
| 书签 | `key:bookmarks` | 156 |  |
| 搜索 | `key:search` | 217 |  |
| 邮件 | `key:mail` | 155 |  |
| 写邮件 | `key:email` | 215 |  |
| 聊天 | `key:chat` | 216 |  |
| 回复 | `key:reply` | 232 |  |
| 转发邮件 | `key:forwardmail` | 233 |  |
| 发送 | `key:send` | 231 |  |
| 保存 | `key:save` | 234 |  |
| 打开 | `key:open` | 134 |  |
| 关闭 | `key:close` | 206 |  |
| 新建 | `key:new` | 181 |  |
| 文件 | `key:file` | 144 |  |
| 发送文件 | `key:sendfile` | 145 |  |
| 删除文件 | `key:deletefile` | 146 |  |
| 传输 | `key:xfer` | 147 |  |
| 文档 | `key:documents` | 235 |  |
| 撤销 | `key:undo` | 131 |  |
| 重做 | `key:redo` | 182 |  |
| 剪切 | `key:cut` | 137 |  |
| 复制 | `key:copy` | 133 |  |
| 粘贴 | `key:paste` | 135 |  |
| 查找 | `key:find` | 136 |  |
| 重复 | `key:again` | 129 |  |
| 属性 | `key:props` | 130 |  |
| 置前 | `key:front` | 132 |  |
| 编辑 | `key:edit` | 176 |  |
| 移动 | `key:move` | 175 |  |
| 退出 | `key:exit` | 174 |  |
| 计算器 | `key:calc` | 140 |  |
| 设置 | `key:setup` | 141 |  |
| 配置 | `key:config` | 171 |  |
| 电话 | `key:phone` | 169 |  |
| 帮助/问号 | `key:question` | 214 |  |
| 连接 | `key:connect` | 218 |  |
| 财务 | `key:finance` | 219 |  |
| 运动 | `key:sport` | 220 |  |
| 购物 | `key:shop` | 221 |  |
| 我的电脑 | `key:computer` | 157 |  |

## 应用与系统

厂商自定义键与系统功能键，多数普通键盘没有。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 自定义程序 1 | `key:prog1` | 148 |  |
| 自定义程序 2 | `key:prog2` | 149 |  |
| 自定义程序 3 | `key:prog3` | 202 |  |
| 自定义程序 4 | `key:prog4` | 203 |  |
| 宏键 | `key:macro` | 112 |  |
| 缩放 | `key:scale` | 120 |  |
| 切换窗口 | `key:cyclewindows` | 154 |  |
| 显示所有应用 | `key:all_applications` | 204 | 别名 `dashboard` |
| 替代擦除 | `key:alterase` | 222 |  |
| 未知键 | `key:unknown` | 240 |  |
| ISO 专用键 | `key:iso` | 170 |  |
| DOS 专用键 | `key:msdos` | 151 |  |
| 屏幕方向 | `key:direction` | 153 | 别名 `rotate_display` |
| 锁屏/咖啡键 | `key:coffee` | 152 | 别名 `screenlock` |
| 电池 | `key:battery` | 236 |  |
| 蓝牙 | `key:bluetooth` | 237 |  |
| 无线网络 | `key:wlan` | 238 |  |
| 超宽带 | `key:uwb` | 239 |  |
| 无线宽带 | `key:wimax` | 246 | 别名 `wwan` |
| 无线开关 | `key:rfkill` | 247 |  |
| 切换显示输出 | `key:switchvideomode` | 227 |  |
| HP 专用键 | `key:hp` | 211 |  |
| 相机 | `key:camera` | 212 |  |
| 取消 | `key:cancel` | 223 |  |

## 电源与背光

慎用：`power`、`sleep`、`suspend` 会直接作用于系统。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 电源键 | `key:power` | 116 |  |
| 睡眠 | `key:sleep` | 142 |  |
| 唤醒 | `key:wakeup` | 143 |  |
| 挂起 | `key:suspend` | 205 |  |
| 降低亮度 | `key:brightnessdown` | 224 |  |
| 提高亮度 | `key:brightnessup` | 225 |  |
| 循环亮度 | `key:brightness_cycle` | 243 |  |
| 自动亮度 | `key:brightness_auto` | 244 | 别名 `brightness_zero` |
| 关闭显示 | `key:display_off` | 245 |  |
| 键盘背光切换 | `key:kbdillumtoggle` | 228 |  |
| 键盘背光 - | `key:kbdillumdown` | 229 |  |
| 键盘背光 + | `key:kbdillumup` | 230 |  |

## 日韩输入

需要系统装了对应输入法/键盘布局，否则只是普通键位。

| 中文名 | 写入映射 | 键码 | 说明 |
|--------|----------|------|------|
| 全角/半角（日） | `key:zenkakuhankaku` | 85 |  |
| 片假名 | `key:katakana` | 90 |  |
| 平假名 | `key:hiragana` | 91 |  |
| 片假名/平假名 | `key:katakanahiragana` | 93 |  |
| 变换（日） | `key:henkan` | 92 |  |
| 无变换（日） | `key:muhenkan` | 94 |  |
| 罗马字（日） | `key:ro` | 89 |  |
| 日元符号 ¥ | `key:yen` | 124 |  |
| 韩/英切换 | `key:hangeul` | 122 | 别名 `hanguel` |
| 汉字转换（韩） | `key:hanja` | 123 |  |

## 组合键示例

```toml
[profile.desktop]
lb = "key:ctrl+c"          # 复制
rb = "key:ctrl+v"          # 粘贴
start = "key:alt+tab"      # 切换窗口
back = "key:ctrl+z"        # 撤销
m1 = "key:shift+equal"     # 打 +
m2 = "key:ctrl+alt+t"      # 打开终端（XFCE 默认）
```

注意 XFCE 默认把 `Ctrl+Alt+Esc` 绑定到 `xkill`、`Ctrl+Esc` 绑定到开始菜单，映射组合键时避开这些系统快捷键更安全。

键码来源：Linux `input-event-codes.h`，与 `flydigi_daemon/mapping.py` 的 `_KEY_CODES` 一致。
