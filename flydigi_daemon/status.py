"""daemon 写入、可视化编辑器读取的设备状态（电量等）

编辑器以普通用户运行，不能直接读 USB；daemon/check 以 root 读过 cmd16 后，
把电量、固件、槽位写进 `status.json`（0644），网页右上角再轮询它。

电池字节 `[23]` 是 0..5 的档位（`check` 里显示为“电量=2”），网页按
`level * 20%` 显示；没有充电标志的可靠来源，所以不显示充电状态。
"""

import json
import os
import tempfile
import time


def write(cfg, info):
    from . import config as cfgmod
    path = cfgmod.status_file(cfg)
    data = {
        'battery': info.get('battery'),
        'battery_percent': (min(int(info['battery']), 5) * 20
                            if info.get('battery') is not None else None),
        'connect': info.get('connect'),
        'slot': info.get('slot'),
        'fw': info.get('fw'),
        'uid': info.get('uid'),
        'updated': time.time(),
        'updated_text': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder or '.', prefix='.status-')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.write('\n')
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return data
    except OSError:
        return None


def read(cfg):
    from . import config as cfgmod
    path = cfgmod.status_file(cfg)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
