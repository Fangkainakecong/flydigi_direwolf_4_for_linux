#!/usr/bin/env python3
"""可视化编辑器 / user.toml 生成回归"""

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time

import tomli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon import config, mapping, status, ui   # noqa: E402

DEFAULT = config.load(config.DEFAULT_PATH)


def test_model_has_buttons_and_axes():
    model = ui.build_model(config.load())
    names = [p['name'] for p in model['profiles']]
    assert names[:4] == ['gamepad', 'desktop', 'media', 'passthrough']
    desktop = model['profiles'][1]
    buttons = {b['source']: b for b in desktop['buttons']}
    assert buttons['a']['value'] == 'key:space'
    assert buttons['home']['value'] == 'toggle:gyro'
    axes = {a['axis']: a for a in desktop['axes']}
    assert axes['lx']['value']['target'] == 'mouse:x'
    assert axes['lt']['value']['max_hz'] == 30
    assert axes['gyro_x']['value']['sensitivity'] == 8.0
    assert axes['gyro_z']['value'] is None
    assert desktop['original'] == 'desktop'
    assert desktop['passthrough'] is False
    assert desktop['groups'] == {'gyro': False}
    # 组是全局概念：四个配置都显示 gyro，默认 true
    assert [p['groups'] for p in model['profiles']] == [
        {'gyro': True}, {'gyro': False}, {'gyro': True}, {'gyro': True}]
    # 组是跨 profile 共享的；每一页都应该能设置自己的 gyro 初值。
    for profile in model['profiles']:
        assert 'gyro' in profile['groups'], profile['name']
    assert model['profiles'][0]['groups']['gyro'] is True


def test_dump_toml_roundtrip():
    text = ui.dump_toml({'profile': {'desktop': {
        'a': 'key:f',
        'axis': {'lx': {'target': 'mouse:x', 'max_speed': 3000.0,
                        'invert': True}},
    }}})
    data = tomli.loads(text)
    axis = data['profile']['desktop']['axis']['lx']
    assert axis['target'] == 'mouse:x'
    assert axis['max_speed'] == 3000.0
    assert axis['invert'] is True


def model_posted(model):
    profiles = {}
    for profile in model['profiles']:
        profiles[profile['original']] = {
            'name': profile['name'],
            'passthrough': profile['passthrough'],
            'groups': profile['groups'],
            'buttons': {b['source']: b['value'] for b in profile['buttons']},
            'axes': {a['axis']: a['value'] for a in profile['axes']
                     if a['value']},
        }
    return {'profiles': profiles}


def _posted(**overrides):
    base = {
        'profiles': {
            'desktop': {
                'buttons': {'a': 'key:f', 'home': 'none'},
                'axes': {
                    'lx': {'target': 'mouse:x', 'max_speed': 3000},
                    'gyro_x': {'target': 'mouse:x', 'sensitivity': 10},
                },
            },
        },
    }
    for key, value in overrides.items():
        base['profiles']['desktop'][key].update(value)
    return base


def test_save_overrides_and_diff():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'user.toml')
        ok, errors = ui.save_overrides(
            DEFAULT, _posted(), path,
            existing={'daemon': {'state_file': '/tmp/state.json'}})
        assert ok, errors
        data = tomli.load(open(path, 'rb'))
        assert data['daemon']['state_file'] == '/tmp/state.json'
        prof = data['profile']['desktop']
        assert prof['a'] == 'key:f'
        assert prof['home'] == 'none'
        assert prof['axis']['lx']['max_speed'] == 3000
        assert prof['axis']['gyro_x']['sensitivity'] == 10
        # 与默认相同的字段不应被写死
        assert 'deadzone' not in prof['axis']['lx']

        merged = config.load(config.DEFAULT_PATH, user_path=path)
        parsed = mapping.parse_config(merged)
        assert parsed['desktop'].buttons['a'].target.value == \
            (mapping._KEY_CODES['f'],)
        assert parsed['desktop'].axes['lx'].max_speed == 3000
        assert parsed['desktop'].axes['gyro_x'].sensitivity == 10

        # 再保存一次，把 a/home/灵敏度改回默认：旧的覆盖必须消失
        posted = _posted()
        posted['profiles']['desktop']['buttons'] = {'a': 'key:space',
                                                    'home': 'toggle:gyro'}
        posted['profiles']['desktop']['axes'] = {
            'lx': {'target': 'mouse:x', 'max_speed': 1200},
            'gyro_x': {'target': 'mouse:x', 'sensitivity': 8},
        }
        ok, errors = ui.save_overrides(DEFAULT, posted, path)
        assert ok, errors
        data = tomli.load(open(path, 'rb'))
        prof = data.get('profile', {}).get('desktop', {})
        assert 'a' not in prof and 'home' not in prof
        assert 'axis' not in prof or not prof['axis'].get('lx')


def test_rename_profile_full_copy():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'user.toml')
        posted = model_posted(ui.build_model(config.load()))
        posted['profiles']['desktop']['name'] = '桌面'
        ok, errors = ui.save_overrides(DEFAULT, posted, path)
        assert ok, errors
        data = tomli.load(open(path, 'rb'))
        assert '桌面' in data['profile']
        assert 'desktop' not in data['profile']
        assert data['daemon']['slot_profiles'][1] == '桌面'
        merged = config.load(config.DEFAULT_PATH, user_path=path)
        parsed = mapping.parse_config(merged)
        assert parsed['桌面'].passthrough is False
        assert parsed['桌面'].groups['gyro'] is False
        assert parsed['桌面'].axes['lx'].target.value == 'x'
        assert parsed['桌面'].axes['gyro_x'].sensitivity == 8.0
        assert parsed['桌面'].buttons['a'].target.value == \
            (mapping._KEY_CODES['space'],)
        assert config.slot_profiles(merged)[1] == '桌面'


def test_rename_passthrough_profile():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'user.toml')
        posted = model_posted(ui.build_model(config.load()))
        posted['profiles']['gamepad']['name'] = '标准手柄'
        ok, errors = ui.save_overrides(DEFAULT, posted, path)
        assert ok, errors
        merged = config.load(config.DEFAULT_PATH, user_path=path)
        parsed = mapping.parse_config(merged)
        assert parsed['标准手柄'].passthrough is True
        assert parsed['标准手柄'].buttons['m1'].target.value == 'lb'


def test_rename_can_turn_off_passthrough_and_groups():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'user.toml')
        posted = model_posted(ui.build_model(config.load()))
        posted['profiles']['gamepad']['name'] = '自定义手柄'
        posted['profiles']['gamepad']['passthrough'] = False
        posted['profiles']['desktop']['groups']['gyro'] = True
        ok, errors = ui.save_overrides(DEFAULT, posted, path)
        assert ok, errors
        merged = config.load(config.DEFAULT_PATH, user_path=path)
        parsed = mapping.parse_config(merged)
        assert parsed['自定义手柄'].passthrough is False
        assert parsed['desktop'].groups.get('gyro', True) is True


def test_rename_duplicate_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'user.toml')
        posted = model_posted(ui.build_model(config.load()))
        posted['profiles']['desktop']['name'] = 'gamepad'
        ok, errors = ui.save_overrides(DEFAULT, posted, path)
        assert not ok
        assert any('重复' in e for e in errors), errors


def test_keyboard_target_names():
    names = ('kp0', 'kp_plus', 'kp_minus', 'kp_dot', 'kp_enter',
             'rightctrl', 'rightshift', 'f13', 'playpause', 'nextsong',
             'volumeup', 'mute', 'sysrq', 'compose', 'leftbrace')
    for name in names:
        target = mapping.parse_button_target('key:' + name)
        assert target.kind == 'key'


def test_save_rejects_bad_target():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'user.toml')
        posted = _posted()
        posted['profiles']['desktop']['buttons']['a'] = 'key:notakey'
        ok, errors = ui.save_overrides(DEFAULT, posted, path)
        assert not ok
        assert any('未知按键名' in e for e in errors), errors
        assert not os.path.exists(path)


def test_status_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = config.load()
        path = os.path.join(tmp, 'status.json')
        cfg['daemon']['status_file'] = path
        status.write(cfg, {'battery': 5, 'connect': 0, 'slot': 3,
                           'fw': '4.0.7.2', 'uid': 'x'})
        data = status.read(cfg)
        assert data['battery'] == 5
        assert data['battery_percent'] == 100
        assert oct(os.stat(path).st_mode & 0o777) == '0o644'


def test_status_refresh_file():
    old = status.REFRESH_FILE
    with tempfile.TemporaryDirectory() as tmp:
        status.REFRESH_FILE = os.path.join(tmp, 'refresh')
        try:
            assert status.refresh_mtime() == 0
            status.request_refresh()
            assert status.refresh_mtime() > 0
        finally:
            status.REFRESH_FILE = old


def test_render_page():
    page = ui.render_page(ui.build_model(config.load()))
    assert '__MODEL__' not in page
    assert 'const MODEL =' in page
    assert 'desktop' in page
    assert '保存到 user.toml' in page


def test_http_model_and_save():
    model = ui.build_model(config.load())
    cfg = config.load()
    server = ui.ThreadingHTTPServer(('127.0.0.1', 0),
                                    ui.make_handler(model, cfg))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    old_user = ui.cfgmod.USER_PATH
    try:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request('GET', '/api/model')
        data = json.loads(conn.getresponse().read())
        assert len(data['profiles']) >= 4

        with tempfile.TemporaryDirectory() as tmp:
            cfg['daemon']['status_file'] = os.path.join(tmp, 'status.json')
            status.write(cfg, {'battery': 4, 'connect': 0, 'slot': 3,
                               'fw': '4.0.7.2', 'uid': 'x'})
            conn.request('GET', '/api/status')
            info = json.loads(conn.getresponse().read())
            assert info['available'] and info['battery'] == 4
            assert info['battery_percent'] == 80

        with tempfile.TemporaryDirectory() as tmp:
            ui.cfgmod.USER_PATH = os.path.join(tmp, 'user.toml')
            conn.request('POST', '/api/save',
                         body=json.dumps(_posted()),
                         headers={'Content-Type': 'application/json'})
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200 and payload['ok'], payload
            assert os.path.exists(ui.cfgmod.USER_PATH)

            conn.request('GET', '/api/model')
            updated = json.loads(conn.getresponse().read())
            desktop = [p for p in updated['profiles']
                       if p['name'] == 'desktop'][0]
            a = [b for b in desktop['buttons'] if b['source'] == 'a'][0]
            assert a['value'] == 'key:f'
    finally:
        ui.cfgmod.USER_PATH = old_user
        server.shutdown()
        server.server_close()


def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _EditArgs(object):
    no_open = True
    idle_timeout = 0
    port = 0


def test_editor_save_exits():
    old_user = ui.cfgmod.USER_PATH
    with tempfile.TemporaryDirectory() as tmp:
        ui.cfgmod.USER_PATH = os.path.join(tmp, 'user.toml')
        args = _EditArgs()
        args.port = _free_port()
        thread = threading.Thread(target=ui.run,
                                  args=(config.load(), args), daemon=True)
        try:
            thread.start()
            conn = None
            for _ in range(30):
                try:
                    conn = http.client.HTTPConnection('127.0.0.1', args.port,
                                                      timeout=1)
                    conn.request('GET', '/api/model')
                    if conn.getresponse().status == 200:
                        break
                except OSError:
                    time.sleep(0.1)
            assert conn is not None
            conn.request('POST', '/api/save', body=json.dumps(_posted()),
                         headers={'Content-Type': 'application/json'})
            payload = json.loads(conn.getresponse().read())
            assert payload['ok'], payload
            thread.join(4)
            assert not thread.is_alive(), '保存后编辑器没有退出'
        finally:
            ui.cfgmod.USER_PATH = old_user


def test_editor_idle_exits():
    old_user = ui.cfgmod.USER_PATH
    with tempfile.TemporaryDirectory() as tmp:
        ui.cfgmod.USER_PATH = os.path.join(tmp, 'user.toml')
        args = _EditArgs()
        args.port = _free_port()
        args.idle_timeout = 0.2
        thread = threading.Thread(target=ui.run,
                                  args=(config.load(), args), daemon=True)
        try:
            thread.start()
            thread.join(4)
            assert not thread.is_alive(), '空闲超时没有退出'
        finally:
            ui.cfgmod.USER_PATH = old_user


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('ok   %s' % name)
        except AssertionError as exc:
            failed += 1
            print('FAIL %s: %r' % (name, exc))
    print('\n%d/%d 通过' % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
