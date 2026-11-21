#!/usr/bin/env python3
"""XTEST 释放命令 / Deskflow 断线检测回归"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from flydigi_daemon import xrelease            # noqa: E402


def test_release_without_display_errors():
    old = os.environ.pop('DISPLAY', None)
    try:
        try:
            xrelease.release()
        except xrelease.ReleaseError as exc:
            assert 'DISPLAY' in str(exc), exc
        else:
            raise AssertionError('无 DISPLAY 时应报错')
    finally:
        if old is not None:
            os.environ['DISPLAY'] = old


def test_release_dry_run():
    class Args(object):
        dry_run = True
        display = None
    assert xrelease.run(Args()) == 0


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
