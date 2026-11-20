"""读 `config/default.toml`

M1 只真正用 `[device]`、`[virtual.*]`、`[safety]` 三块；
`[daemon]` 的四槽和 `[profile.*]` 的映射留给 M2/M3，先只做结构检查。
"""

import os
import sys

import tomli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, 'config', 'default.toml')


def load(path=None):
    path = path or DEFAULT_PATH
    try:
        with open(path, 'rb') as f:
            cfg = tomli.load(f)
    except FileNotFoundError:
        sys.exit('找不到配置文件：%s' % path)
    except tomli.TOMLDecodeError as exc:
        sys.exit('配置语法错误（%s）：%s' % (path, exc))

    if not isinstance(cfg, dict):
        sys.exit('配置根节点必须是表：%s' % path)
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


def device_section(cfg):
    dev = cfg.get('device', {})
    return {
        'vendor': int(dev.get('vendor', 0x045E)),
        'product': int(dev.get('product', 0x028E)),
        'interface': int(dev.get('interface', 0)),
    }


def safety_section(cfg):
    return cfg.get('safety', {})
