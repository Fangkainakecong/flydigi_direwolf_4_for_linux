#!/usr/bin/env python3
"""兼容层：实现已移到 `flydigi_daemon/usb.py`。

旧探针仍然写 `from flydigi_usb import Pad, Link, ...`，这里把名字转出去，
避免为了一个包去改十几个已经验证过的脚本。新代码请直接用
`from flydigi_daemon import usb`。
"""

import os
import sys

sys.dont_write_bytecode = True

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flydigi_daemon.usb import *                      # noqa: F401,F403,E402
from flydigi_daemon.usb import _ioctl, _LIBC, status_name  # noqa: F401,E402
