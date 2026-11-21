"""读配置：`config/default.toml` + 可选的 `config/user.toml`

`default.toml` 是带注释的样例（会随仓库更新）；自己的改动写进
`config/user.toml`，启动时做**深合并**，只覆盖写到的键，不会丢掉默认值。
`config/user.toml` 不进 git，模板见 `config/user.example.toml`。
"""

import os
import sys

import tomli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, 'config', 'default.toml')
USER_PATH = os.path.join(ROOT, 'config', 'user.toml')
_UNSET = object()


def _read_toml(path):
    try:
        with open(path, 'rb') as f:
            data = tomli.load(f)
    except FileNotFoundError:
        sys.exit('找不到配置文件：%s' % path)
    except tomli.TOMLDecodeError as exc:
        sys.exit('配置语法错误（%s）：%s' % (path, exc))
    if not isinstance(data, dict):
        sys.exit('配置根节点必须是表：%s' % path)
    return data


def merge(base, extra):
    """深合并：表递归合并，其它类型整体替换。"""
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path=None, user_path=_UNSET):
    """读配置。`path=None` 时才叠加 `config/user.toml`。"""
    base_path = path or DEFAULT_PATH
    cfg = _read_toml(base_path)
    if user_path is _UNSET:
        user_path = USER_PATH if path is None else None
    if user_path and os.path.exists(user_path):
        cfg = merge(cfg, _read_toml(user_path))
    if cfg.get('version') != 1:
        print('⚠ 配置 version=%r，本程序按 version=1 解释'
              % (cfg.get('version'),), file=sys.stderr)
    return cfg


def daemon_section(cfg):
    return cfg.get('daemon', {})


def slot_profiles(cfg):
    """四槽对应的 profile 名，长度必须是 4。M1 不用，M2 用。"""
    names = list(daemon_section(cfg).get('slot_profiles', []))
    if len(names) != 4:
        print('⚠ daemon.slot_profiles 应有 4 项，当前 %d 项' % len(names),
              file=sys.stderr)
    return names


def state_file(cfg):
    """`[daemon] state_file`；留空则按 root/普通用户选默认路径。"""
    from .slots import default_state_path
    path = daemon_section(cfg).get('state_file') or ''
    return os.path.expanduser(path) if path else default_state_path()


def device_section(cfg):
    dev = cfg.get('device', {})
    return {
        'vendor': int(dev.get('vendor', 0x045E)),
        'product': int(dev.get('product', 0x028E)),
        'interface': int(dev.get('interface', 0)),
    }


def safety_section(cfg):
    return cfg.get('safety', {})
