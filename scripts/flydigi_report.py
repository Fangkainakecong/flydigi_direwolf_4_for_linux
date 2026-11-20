#!/usr/bin/env python3
"""兼容层：实现已移到 `flydigi_daemon/report.py`。

旧探针仍然写 `import flydigi_report as rep`，这里把名字转出去。
新代码请直接用 `from flydigi_daemon import report`。
"""

import os
import sys

sys.dont_write_bytecode = True

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flydigi_daemon.report import *                   # noqa: F401,F403,E402
from flydigi_daemon.report import (                   # noqa: F401,E402
    AXES, FLYDIGI_EXTRA, GYRO, KEY_NAMES, STD_HIGH, STD_LOW,
    decode, is_reply, norm_axis, reply, std_buttons,
)
