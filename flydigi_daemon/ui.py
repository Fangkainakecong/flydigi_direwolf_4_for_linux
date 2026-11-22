"""可视化映射编辑器（`flydigi-daemon edit`）

在本机起一个只监听 127.0.0.1 的小网页，四套配置分页显示：

* 每个物理键 → 目标字符串（`key:*` / `mouse:*` / `xbox:*` / `wheel:*` /
  `toggle:*` / `none`），留空 = 继承默认；
* 每个轴（摇杆/扳机/体感）的目标 + 全部参数（deadzone/curve/max_speed/
  sensitivity/smoothing/deadband/max_hz/threshold/repeat_hz/invert/group）；
* 保存时只把与 `default.toml` 不同的字段写进 `config/user.toml`，
  不覆盖默认文件，也不丢已有 user.toml 里的其它段。

不依赖 Flask/Qt，只用标准库；页面是单文件，样式和 `show --html` 一致。
"""

import html
import json
import os
import re
import shutil
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tomli

from . import config as cfgmod
from . import mapping
from . import status as statusmod
from .show import AXIS_ROWS, BUTTON_ROWS, STANDARD_SOURCES

PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui.html')
MAX_BODY = 1024 * 1024

AXIS_FIELDS = (
    'target', 'group', 'deadzone', 'curve', 'invert', 'max_speed',
    'sensitivity', 'smoothing', 'deadband', 'max_hz', 'threshold',
    'release_threshold', 'repeat_hz', 'neg', 'pos',
)

COMMON_TARGETS = [
    'none', 'key:space', 'key:escape', 'key:enter', 'key:tab',
    'key:ctrl+shift+esc', 'key:playpause', 'key:volumeup',
    'mouse:left', 'mouse:right', 'mouse:middle',
    'wheel:up', 'wheel:down', 'toggle:gyro',
    'xbox:a', 'xbox:b', 'xbox:x', 'xbox:y', 'xbox:lb', 'xbox:rb',
    'xbox:back', 'xbox:start', 'xbox:guide', 'xbox:l3', 'xbox:r3',
    'mouse:x', 'mouse:y', 'keypair', 'xbox:abs_x', 'xbox:abs_y',
]


def _target_string(target):
    return '' if target is None else mapping.format_target(target)


def button_value(profile, source):
    rule = profile.buttons.get(source) if profile else None
    return _target_string(rule.target) if rule else ''


def button_hint(profile, source):
    rule = profile.buttons.get(source) if profile else None
    if rule:
        return mapping.format_target(rule.target)
    if profile and profile.passthrough and source in STANDARD_SOURCES:
        return '原样（passthrough）'
    return '无输出'


def axis_dict(rule):
    if rule is None:
        return None
    return {
        'target': _target_string(rule.target),
        'group': rule.group or '',
        'deadzone': rule.deadzone,
        'curve': rule.curve,
        'invert': rule.invert,
        'max_speed': rule.max_speed,
        'sensitivity': rule.sensitivity,
        'smoothing': rule.smoothing,
        'deadband': rule.deadband,
        'max_hz': rule.max_hz,
        'threshold': rule.threshold,
        'release_threshold': rule.release_threshold,
        'repeat_hz': rule.repeat_hz,
        'neg': _target_string(rule.neg),
        'pos': _target_string(rule.pos),
    }


def _referenced_groups(profile):
    """配置文件里出现过的组名：显式初值 + 按钮/轴上的 group。"""
    if profile is None:
        return set()
    names = set(profile.groups)
    names.update(rule.group for rule in profile.buttons.values()
                 if rule.group)
    names.update(rule.group for rule in profile.axes.values() if rule.group)
    return names


def build_model(cfg):
    default_cfg = cfgmod.load(cfgmod.DEFAULT_PATH)
    default_profiles = mapping.parse_config(default_cfg)
    effective = mapping.parse_config(cfg)
    # 只编辑四槽实际引用的 profile；改名后不要显示 default 里的旧名字。
    order = [name for name in cfgmod.slot_profiles(cfg) if name in effective]

    # 组是 profile 之间共享的名字（例如 gyro）。把任意 profile 用到的组
    # 都列到每一页，这样每个配置都能设置自己的初值，而不是只有显式写了
    # [profile.X.groups] 的那一页才有勾选框。
    all_groups = set()
    for profile in list(effective.values()) + list(default_profiles.values()):
        all_groups.update(_referenced_groups(profile))

    profiles = []
    for name in order:
        eff = effective[name]
        dft = default_profiles.get(name)
        buttons = []
        for label, source in BUTTON_ROWS:
            buttons.append({
                'source': source,
                'label': label,
                'default': button_value(dft, source),
                'value': button_value(eff, source),
                'hint': button_hint(eff, source),
            })
        axes = []
        for label, axis in AXIS_ROWS:
            axes.append({
                'axis': axis,
                'label': label,
                'default': axis_dict(dft.axes.get(axis)) if dft else None,
                'value': axis_dict(eff.axes.get(axis)),
                'hint': (mapping.describe_axis(eff.axes[axis], short=True)
                         if axis in eff.axes else '—'),
            })
        slots = cfgmod.slot_profiles(cfg)
        group_names = sorted(all_groups)
        profiles.append({
            'original': name,
            'name': name,
            'slot': slots.index(name) + 1 if name in slots else 0,
            'passthrough': eff.passthrough,
            'default_passthrough': bool(dft.passthrough) if dft else False,
            'groups': {g: bool(eff.groups.get(g, True)) for g in group_names},
            'default_groups': {g: bool(dft.groups.get(g, True)) if dft
                               else True for g in group_names},
            'buttons': buttons,
            'axes': axes,
        })
    return {'editor': 4, 'profiles': profiles, 'targets': COMMON_TARGETS}


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------
def _dump_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_dump_value(v) for v in value) + ']'
    raise TypeError('不支持的 TOML 值: %r' % (value,))


_BARE_KEY = re.compile(r'^[A-Za-z0-9_-]+$')


def _key(name):
    name = str(name)
    return name if _BARE_KEY.match(name) else json.dumps(
        name, ensure_ascii=False)


def dump_toml(data):
    lines = ['# 由 `flydigi-daemon edit` 生成；也可以手改。',
             '# 只保存与 config/default.toml 不同的字段。', '']

    def walk(prefix, table):
        scalars = [(k, v) for k, v in table.items()
                   if not isinstance(v, dict)]
        tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        for key, value in scalars:
            lines.append('%s = %s' % (_key(key), _dump_value(value)))
        for key, value in tables:
            parts = prefix + [_key(key)]
            lines.append('')
            lines.append('[%s]' % '.'.join(parts))
            walk(prefix + [_key(key)], value)

    walk([], data)
    return '\n'.join(lines).rstrip() + '\n'


def _read_user(path):
    try:
        with open(path, 'rb') as f:
            data = tomli.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except tomli.TOMLDecodeError as exc:
        raise mapping.ConfigError('config/user.toml 语法错误：%s' % exc)


def _profile_overrides(default_profiles, original, posted, existing, full):
    dft = default_profiles.get(original)
    prof = {}

    buttons = posted.get('buttons') or {}
    for source, value in buttons.items():
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        base = button_value(dft, source)
        if full or value != base:
            prof[source] = value

    axes = posted.get('axes') or {}
    for axis, value in axes.items():
        if not value or not isinstance(value, dict):
            continue
        target = str(value.get('target', '')).strip()
        if not target:
            continue
        clean = {field: value[field] for field in AXIS_FIELDS
                 if field in value}
        clean['target'] = target
        if full or dft is None:
            prof.setdefault('axis', {})[axis] = clean
        else:
            base_rule = axis_dict(dft.axes.get(axis))
            if base_rule is None:
                prof.setdefault('axis', {})[axis] = clean
            else:
                diff = {k: v for k, v in clean.items()
                        if base_rule.get(k) != v}
                if diff:
                    prof.setdefault('axis', {})[axis] = diff

    passthrough = bool(posted.get('passthrough'))
    base_passthrough = bool(dft.passthrough) if dft else False
    if full:
        if passthrough:
            prof['passthrough'] = True
    elif passthrough != base_passthrough:
        prof['passthrough'] = passthrough

    groups = {str(k): bool(v) for k, v in (posted.get('groups') or {}).items()}
    base_groups = dft.groups if dft else {}
    if full:
        # 新名字没有默认组，组的隐式默认是 true，只有 false 需要写。
        group_diff = {k: v for k, v in groups.items() if not v}
    else:
        group_diff = {k: v for k, v in groups.items()
                      if bool(base_groups.get(k, True)) != v}
    if group_diff:
        prof['groups'] = group_diff

    # 保留 UI 不管理的其它键（自定义 source / 未知轴 / 其它段）
    old = (existing.get('profile') or {}).get(original) or {}
    managed_sources = {row[1] for row in BUTTON_ROWS}
    managed_axes = {row[1] for row in AXIS_ROWS}
    for key, value in old.items():
        if key == 'axis':
            extra = {axis: axis_data for axis, axis_data in value.items()
                     if axis not in managed_axes}
            if extra:
                prof.setdefault('axis', {}).update(extra)
        elif key in ('passthrough', 'groups'):
            # 这两个由上面的 UI 状态决定，不能把旧值原样带回来。
            continue
        elif key not in managed_sources and key not in prof:
            prof[key] = value
    return prof


def apply_overrides(default_cfg, posted, existing):
    default_profiles = mapping.parse_config(default_cfg)
    base = cfgmod.merge(default_cfg, existing)
    base_daemon = base.get('daemon', {}) or {}
    base_slots = list(base_daemon.get('slot_profiles', []))
    base_startup = base_daemon.get('startup_profile')
    existing_profiles = dict(existing.get('profile') or {})
    posted_profiles = posted.get('profiles') or {}

    finals = {}
    for original, data in posted_profiles.items():
        if not isinstance(data, dict):
            raise mapping.ConfigError('profile %r 数据格式错误' % (original,))
        name = str(data.get('name') or original).strip()
        if not name:
            raise mapping.ConfigError('profile 名字不能为空')
        if len(name) > 64:
            raise mapping.ConfigError('profile 名字太长：%r' % (name,))
        finals[original] = name

    unposted = (set(existing_profiles) | set(default_profiles)) - set(
        posted_profiles)
    all_names = list(finals.values()) + list(unposted)
    if len(all_names) != len(set(all_names)):
        raise mapping.ConfigError('profile 名字重复：%s'
                                 % ', '.join(sorted(all_names)))

    new_profiles = {name: data for name, data in existing_profiles.items()
                    if name not in posted_profiles}
    for original, data in posted_profiles.items():
        new = finals[original]
        full = (new != original) or (original not in default_profiles)
        prof = _profile_overrides(default_profiles, original, data,
                                  existing, full)
        if prof or full:
            new_profiles[new] = prof

    slots = [finals.get(name, name) for name in base_slots]
    startup = finals.get(base_startup, base_startup) if base_startup else None

    out = dict(existing)
    if new_profiles:
        out['profile'] = new_profiles
    else:
        out.pop('profile', None)
    daemon = dict(out.get('daemon') or {})
    if slots:
        daemon['slot_profiles'] = slots
    if startup:
        daemon['startup_profile'] = startup
    if daemon:
        out['daemon'] = daemon
    return out


def save_overrides(default_cfg, posted, user_path, existing=None):
    """校验并写 user.toml；返回 (ok, errors)。"""
    try:
        if existing is None:
            existing = _read_user(user_path)
        new_user = apply_overrides(default_cfg, posted, existing)
        text = dump_toml(new_user)
        folder = os.path.dirname(os.path.abspath(user_path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder or '.', prefix='.user-',
                                   suffix='.toml')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        try:
            merged = cfgmod.load(cfgmod.DEFAULT_PATH, user_path=tmp)
            errors, _warnings = mapping.lint_config(merged)
            mapping.parse_config(merged)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        if errors:
            os.unlink(tmp)
            return False, errors
        if os.path.exists(user_path):
            try:
                shutil.copyfile(user_path, user_path + '.bak')
            except OSError:
                pass
        os.replace(tmp, user_path)
        return True, []
    except (mapping.ConfigError, OSError, ValueError, TypeError) as exc:
        return False, [str(exc)]


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
def render_page(model):
    payload = json.dumps(model).replace('</', '<\\/')
    with open(PAGE_PATH, encoding='utf-8') as f:
        return f.read().replace('__MODEL__', payload)


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_request = time.time()
        self.saved = False


def make_handler(model, cfg):
    # 保存后重建 model，页面刷新才能看到新的配置名。
    state = {'model': model, 'cfg': cfg}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):      # noqa: A003
            pass

        def _send(self, code, body, content_type):
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):                        # noqa: N802
            if self.path in ('/', '/index.html'):
                self.server.last_request = time.time()
                self._send(200, render_page(state['model']), 'text/html')
            elif self.path == '/api/model':
                self.server.last_request = time.time()
                self._send(200, json.dumps(state['model']),
                           'application/json')
            elif self.path == '/api/status':
                data = statusmod.read(state['cfg'])
                payload = {'available': bool(data), 'server_time': time.time()}
                if data:
                    payload.update(data)
                self._send(200, json.dumps(payload), 'application/json')
            else:
                self._send(404, 'not found', 'text/plain')

        def do_POST(self):                       # noqa: N802
            if self.path == '/api/refresh':
                statusmod.request_refresh()
                self.server.last_request = time.time()
                self._send(200, json.dumps({'ok': True}),
                           'application/json')
                return
            if self.path != '/api/save':
                self._send(404, 'not found', 'text/plain')
                return
            length = int(self.headers.get('Content-Length') or 0)
            if length <= 0 or length > MAX_BODY:
                self._send(400, 'bad length', 'text/plain')
                return
            try:
                posted = json.loads(self.rfile.read(length).decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as exc:
                self._send(400, json.dumps({'ok': False,
                                            'errors': [str(exc)]}),
                           'application/json')
                return
            try:
                ok, errors = save_overrides(cfgmod.load(cfgmod.DEFAULT_PATH),
                                            posted, cfgmod.USER_PATH)
            except Exception as exc:            # noqa: BLE001
                ok, errors = False, ['保存失败：%r' % (exc,)]
            self.server.last_request = time.time()
            if ok:
                try:
                    state['cfg'] = cfgmod.load()
                    state['model'] = build_model(state['cfg'])
                except Exception:               # noqa: BLE001
                    pass
                self.server.saved = True
            payload = {'ok': ok, 'errors': errors,
                       'path': cfgmod.USER_PATH}
            self._send(200 if ok else 400, json.dumps(payload),
                       'application/json')

    return Handler


def run(cfg, args):
    model = build_model(cfg)
    url = 'http://127.0.0.1:%d/' % args.port
    try:
        server = EditorServer(('127.0.0.1', args.port),
                              make_handler(model, cfg))
    except OSError as exc:
        # 快捷方式重复点击时端口已占用：如果占用者就是本编辑器，直接开页面。
        try:
            import urllib.request
            with urllib.request.urlopen(url + 'api/model', timeout=1) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            if 'profiles' not in data:
                raise ValueError('not our editor')
            print('编辑器已在运行：%s' % url)
            if not args.no_open:
                webbrowser.open(url)
            return 0
        except Exception:                       # noqa: BLE001
            pass
        print('启动失败（端口 %d）：%s' % (args.port, exc))
        print('换一个端口：python3 -m flydigi_daemon edit --port 8800')
        return 1
    print('可视化编辑器: %s' % url)
    print('保存到: %s' % cfgmod.USER_PATH)
    print('保存后编辑器会自动退出；Ctrl-C 停止')
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    idle = max(0.0, float(getattr(args, 'idle_timeout', 1800) or 0))

    def monitor():
        while True:
            time.sleep(0.5)
            if server.saved:
                time.sleep(0.5)
                print('已保存，编辑器退出')
                server.shutdown()
                return
            if idle and time.time() - server.last_request > idle:
                print('空闲超时，编辑器退出')
                server.shutdown()
                return

    threading.Thread(target=monitor, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        server.server_close()
    return 0
