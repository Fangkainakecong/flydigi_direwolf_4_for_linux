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
import shutil
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tomli

from . import config as cfgmod
from . import mapping
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


def build_model(cfg):
    default_cfg = cfgmod.load(cfgmod.DEFAULT_PATH)
    default_profiles = mapping.parse_config(default_cfg)
    effective = mapping.parse_config(cfg)
    order = cfgmod.slot_profiles(cfg)
    order += [name for name in effective if name not in order]

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
        profiles.append({'name': name,
                         'slot': order.index(name) + 1 if name in
                         cfgmod.slot_profiles(cfg) else 0,
                         'buttons': buttons, 'axes': axes})
    return {'profiles': profiles, 'targets': COMMON_TARGETS}


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
    raise TypeError('不支持的 TOML 值: %r' % (value,))


def dump_toml(data):
    lines = ['# 由 `flydigi-daemon edit` 生成；也可以手改。',
             '# 只保存与 config/default.toml 不同的字段。', '']

    def walk(prefix, table):
        scalars = [(k, v) for k, v in table.items()
                   if not isinstance(v, dict)]
        tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        for key, value in scalars:
            lines.append('%s = %s' % (key, _dump_value(value)))
        for key, value in tables:
            name = '%s.%s' % (prefix, key) if prefix else key
            lines.append('')
            lines.append('[%s]' % name)
            walk(name, value)

    walk('', data)
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


def _profile_overrides(default_profiles, name, posted, existing):
    dft = default_profiles.get(name)
    prof = {}
    buttons = posted.get('buttons') or {}
    for source, value in buttons.items():
        if not isinstance(value, str):
            continue
        default = button_value(dft, source)
        if value == default or value == '':
            continue
        prof[source] = value
    axes = posted.get('axes') or {}
    for axis, value in axes.items():
        if not value or not isinstance(value, dict):
            continue
        target = str(value.get('target', '')).strip()
        if not target:
            continue
        dft_rule = axis_dict(dft.axes.get(axis)) if dft else None
        clean = {}
        for field in AXIS_FIELDS:
            if field not in value:
                continue
            clean[field] = value[field]
        clean['target'] = target
        if dft_rule is None:
            prof.setdefault('axis', {})[axis] = clean
        else:
            diff = {k: v for k, v in clean.items()
                    if dft_rule.get(k) != v}
            if diff:
                prof.setdefault('axis', {})[axis] = diff

    # 保留 UI 不管理的其它键（自定义 source / 未知轴）
    old = (existing.get('profile') or {}).get(name) or {}
    managed_sources = {row[1] for row in BUTTON_ROWS}
    managed_axes = {row[1] for row in AXIS_ROWS}
    for key, value in old.items():
        if key == 'axis':
            extra = {axis: axis_data for axis, axis_data in value.items()
                     if axis not in managed_axes}
            if extra:
                prof.setdefault('axis', {}).update(extra)
        elif key not in managed_sources and key not in prof:
            prof[key] = value
    return prof


def apply_overrides(default_cfg, posted, existing):
    default_profiles = mapping.parse_config(default_cfg)
    out = dict(existing)
    profiles = dict(out.get('profile') or {})
    for name, data in (posted.get('profiles') or {}).items():
        prof = _profile_overrides(default_profiles, name, data, existing)
        if prof:
            profiles[name] = prof
        else:
            profiles.pop(name, None)
    if profiles:
        out['profile'] = profiles
    else:
        out.pop('profile', None)
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


def make_handler(model):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):      # noqa: A003
            pass

        def _send(self, code, body, content_type):
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):                        # noqa: N802
            if self.path in ('/', '/index.html'):
                self._send(200, render_page(model), 'text/html')
            elif self.path == '/api/model':
                self._send(200, json.dumps(model), 'application/json')
            else:
                self._send(404, 'not found', 'text/plain')

        def do_POST(self):                       # noqa: N802
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
            payload = {'ok': ok, 'errors': errors,
                       'path': cfgmod.USER_PATH}
            self._send(200 if ok else 400, json.dumps(payload),
                       'application/json')

    return Handler


def run(cfg, args):
    model = build_model(cfg)
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port),
                                     make_handler(model))
    except OSError as exc:
        print('启动失败（端口 %d）：%s' % (args.port, exc))
        print('换一个端口：python3 -m flydigi_daemon edit --port 8800')
        return 1
    url = 'http://127.0.0.1:%d/' % args.port
    print('可视化编辑器: %s' % url)
    print('保存到: %s' % cfgmod.USER_PATH)
    print('Ctrl-C 停止')
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        server.server_close()
    return 0
