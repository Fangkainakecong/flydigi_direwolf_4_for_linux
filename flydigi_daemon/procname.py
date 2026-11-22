"""把进程 comm 改成自己的名字（ps / 任务管理器显示用）。"""

import ctypes

PR_SET_NAME = 15


def set_name(name):
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_NAME, str(name)[:15].encode(), 0, 0, 0)
    except Exception:            # noqa: BLE001
        pass
