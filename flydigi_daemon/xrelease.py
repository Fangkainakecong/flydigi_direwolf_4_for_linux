"""释放 XTEST 卡住的按键/鼠标按钮（`flydigi-daemon release`）

Deskflow / xcape / 远程桌面这类工具通过 XTEST 注入输入。连接中断时，注入端
可能只发出了按下、没发出松开，于是 X 服务器里留下 Shift、Ctrl、鼠标左键等
"幽灵按下"状态；重启 X/电脑才恢复。

做法：先用 `XQueryKeymap` / `XQueryPointer` 问 X 服务器“现在到底有哪些键和
鼠标按钮处于按下状态”，只对这些 keycode/按钮发 XTEST 释放事件。不会碰没按
下的键，也不需要 root。

只清理 XTEST 的全局状态，不动物理设备。
"""

import ctypes
import os


class ReleaseError(Exception):
    pass


def _load(libs):
    loaded = []
    for name in libs:
        try:
            loaded.append(ctypes.CDLL(name))
        except OSError as exc:
            raise ReleaseError('无法加载 %s：%s' % (name, exc))
    return loaded


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_void_p)


def _ignore_x_error(display, event):        # noqa: ARG001
    # XTEST 对个别未映射 keycode 会报 BadValue；释放请求本身已经发出，
    # 这里吞掉即可，不能让默认错误处理退出整个进程。
    return 0


def _pressed_keys(x11, dpy):
    buf = ctypes.create_string_buffer(32)
    x11.XQueryKeymap(dpy, buf)
    keys = []
    for code in range(8, 256):
        if buf.raw[code >> 3] & (1 << (code & 7)):
            keys.append(code)
    return keys


def _pressed_buttons(x11, dpy):
    root = x11.XDefaultRootWindow(dpy)
    root_ret = ctypes.c_ulong()
    child_ret = ctypes.c_ulong()
    rx = ctypes.c_int()
    ry = ctypes.c_int()
    wx = ctypes.c_int()
    wy = ctypes.c_int()
    mask = ctypes.c_uint()
    if not x11.XQueryPointer(dpy, root, ctypes.byref(root_ret),
                             ctypes.byref(child_ret), ctypes.byref(rx),
                             ctypes.byref(ry), ctypes.byref(wx),
                             ctypes.byref(wy), ctypes.byref(mask)):
        return []
    # Button1Mask = 1<<8 ... Button5Mask = 1<<12
    return [button for button in range(1, 9)
            if mask.value & (1 << (7 + button))]


def release(display=None):
    """释放当前按下的 keycode / 鼠标按钮，返回 (key_count, button_count)。"""
    name = display or os.environ.get('DISPLAY')
    if not name:
        raise ReleaseError('没有 DISPLAY；请在桌面会话里运行')
    x11, xtst = _load(['libX11.so.6', 'libXtst.so.6'])

    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    dpy = x11.XOpenDisplay(name.encode())
    if not dpy:
        raise ReleaseError('打不开 X display %s' % name)

    x11.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XQueryPointer.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_uint)]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    x11.XSetErrorHandler.restype = ctypes.c_void_p
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeKeyEvent.restype = ctypes.c_int
    xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.restype = ctypes.c_int

    handler = _ERROR_HANDLER(_ignore_x_error)
    old_handler = x11.XSetErrorHandler(ctypes.cast(handler, ctypes.c_void_p))
    try:
        keys = _pressed_keys(x11, dpy)
        buttons = _pressed_buttons(x11, dpy)
        for code in keys:
            xtst.XTestFakeKeyEvent(dpy, code, 0, 0)
        for button in buttons:
            xtst.XTestFakeButtonEvent(dpy, button, 0, 0)
        x11.XSync(dpy, 0)
    finally:
        x11.XSetErrorHandler(ctypes.c_void_p(old_handler))
        x11.XCloseDisplay(dpy)
    return len(keys), len(buttons)


def run(args):
    if args.dry_run:
        print('将释放当前按下的 keycode 和鼠标按钮（未执行）')
        return 0
    try:
        keys, buttons = release(args.display)
    except ReleaseError as exc:
        print('✗ %s' % exc)
        return 1
    print('✓ 已释放 %d 个 keycode、%d 个鼠标按钮' % (keys, buttons))
    return 0
