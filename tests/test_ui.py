#!/usr/bin/env python3
"""可视化编辑器 / user.toml 生成回归"""

import http.client
import json
import os
import sys
import tempfile
import threading

import tomli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon import config, mapping, ui      # noqa: E402

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


def test_save_rejects_bad_target():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'user.toml')
        posted = _posted()
        posted['profiles']['desktop']['buttons']['a'] = 'key:notakey'
        ok, errors = ui.save_overrides(DEFAULT, posted, path)
        assert not ok
        assert any('未知按键名' in e for e in errors), errors
        assert not os.path.exists(path)


def test_render_page():
    page = ui.render_page(ui.build_model(config.load()))
    assert '__MODEL__' not in page
    assert 'const MODEL =' in page
    assert 'desktop' in page
    assert '保存到 user.toml' in page


def test_http_model_and_save():
    model = ui.build_model(config.load())
    server = ui.ThreadingHTTPServer(('127.0.0.1', 0),
                                    ui.make_handler(model))
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
            ui.cfgmod.USER_PATH = os.path.join(tmp, 'user.toml')
            conn.request('POST', '/api/save',
                         body=json.dumps(_posted()),
                         headers={'Content-Type': 'application/json'})
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200 and payload['ok'], payload
            assert os.path.exists(ui.cfgmod.USER_PATH)
    finally:
        ui.cfgmod.USER_PATH = old_user
        server.shutdown()
        server.server_close()


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
