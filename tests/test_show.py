#!/usr/bin/env python3
"""映射可视化 / 用户覆盖层回归"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flydigi_daemon import config, show    # noqa: E402


def test_views_default():
    views = show.build_views(config.load())
    assert [v.name for v in views] == ['gamepad', 'desktop', 'media',
                                       'passthrough']
    assert [v.slot for v in views] == [1, 2, 3, 4]

    desktop = views[1]
    row = {r['source']: r for r in desktop.rows}
    assert row['a']['target'] == 'key:space'
    assert row['home']['target'] == 'toggle:gyro'
    axes = {r['source']: r for r in desktop.axis_rows}
    assert 'mouse:x' in axes['lx']['target']
    assert 'wheel:up' in axes['lt']['target']

    gamepad = views[0]
    row = {r['source']: r for r in gamepad.rows}
    assert row['m1']['target'] == 'xbox:lb'
    assert row['m2']['target'] == 'xbox:rb'
    axes = {r['source']: r for r in gamepad.axis_rows}
    assert '原样' in axes['lx']['target']


def test_text_render():
    views = show.build_views(config.load())
    text = show.render_text(views, 'config/default.toml', '未标定')
    assert 'desktop' in text and 'key:space' in text
    assert 'SELECT+B' in text
    assert 'passthrough=on' in text
    assert '原样（xbox:abs_z）' in text
    assert '体感标定: 未标定' in text


def test_html_render_and_write():
    views = show.build_views(config.load())
    page = show.render_html(views, 'config/default.toml',
                            calibration_label='未标定')
    assert page.startswith('<!doctype html>')
    assert page.count('<section') == 4
    assert 'data-panel="p-desktop"' in page
    assert 'key:space' in page
    with tempfile.TemporaryDirectory() as tmp:
        path = show.write_html(os.path.join(tmp, 'm.html'), views,
                               'config/default.toml',
                               calibration_label='未标定')
        assert os.path.exists(path)
        assert open(path, encoding='utf-8').read() == page
    assert '体感标定' in page


def test_user_overlay_deep_merge():
    base = """
version = 1
[daemon]
slot_profiles = ["main", "b", "c", "d"]
[profile.main]
a = "key:space"
[profile.main.axis.lx]
target = "mouse:x"
deadzone = 0.2
curve = 2.0
"""
    user = """
[profile.main]
a = "key:f"
home = "none"
[profile.main.axis.lx]
target = "mouse:y"
"""
    with tempfile.TemporaryDirectory() as tmp:
        base_path = os.path.join(tmp, 'base.toml')
        user_path = os.path.join(tmp, 'user.toml')
        with open(base_path, 'w') as f:
            f.write(base)
        with open(user_path, 'w') as f:
            f.write(user)
        cfg = config.load(base_path, user_path=user_path)
        assert cfg['profile']['main']['a'] == 'key:f'
        assert cfg['profile']['main']['home'] == 'none'
        # 没写到的字段必须保留默认值
        assert cfg['profile']['main']['axis']['lx'] == {
            'target': 'mouse:y', 'deadzone': 0.2, 'curve': 2.0}
        # 显式路径 + user_path 才会合并
        plain = config.load(base_path)
        assert plain['profile']['main']['a'] == 'key:space'


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
