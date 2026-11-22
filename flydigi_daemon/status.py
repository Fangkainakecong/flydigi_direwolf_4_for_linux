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

PAUSE_FILE = '/tmp/flydigi-pause'
RESUME_FILE = '/tmp/flydigi-resume'


def _touch(path):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(str(time.time()))
        return True
    except OSError:
        return False


def request_pause():
    return _touch(PAUSE_FILE)


def request_resume():
    return _touch(RESUME_FILE)


def clear_control():
    for path in (PAUSE_FILE, RESUME_FILE):
        try:
            os.unlink(path)
        except OSError:
            pass


def write(cfg, info, profile=None, profile_slot=None, paused=False):
    from . import config as cfgmod
    path = cfgmod.status_file(cfg)
    old = {}
    try:
        with open(path, encoding='utf-8') as f:
            old = json.load(f)
        if not isinstance(old, dict):
            old = {}
    except (OSError, ValueError):
        old = {}

    # 手柄休眠/关闭时，dongle 的 cmd16 应答里电量常常是 0。0% 不是可信
    # 读数（真正耗尽前一般已经关机），保留上一次非零值并标记为“上次”。
    try:
        level = int(info['battery']) if info.get('battery') is not None else None
    except (TypeError, ValueError):
        level = None
    stale = False
    if level is not None and level <= 0:
        level = None
    if level is None:
        try:
            last = int(old.get('battery'))
        except (TypeError, ValueError):
            last = 0
        level = last if last > 0 else None
        stale = True

    data = {
        'battery': level,
        'battery_percent': (min(level, 5) * 20
                            if level is not None else None),
        'battery_stale': stale,
        'connect': info.get('connect', old.get('connect')),
        'slot': info.get('slot', old.get('slot')),
        'fw': info.get('fw', old.get('fw')),
        'uid': info.get('uid', old.get('uid')),
        'paused': bool(paused),
        'updated': time.time(),
        'updated_text': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    if profile is not None:
        data['profile'] = profile
    elif old.get('profile') is not None:
        data['profile'] = old.get('profile')
    if profile_slot is not None:
        data['profile_slot'] = profile_slot
    elif old.get('profile_slot') is not None:
        data['profile_slot'] = old.get('profile_slot')
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
