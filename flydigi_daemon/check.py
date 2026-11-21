"""`flydigi-daemon check`：识别设备 + 审计当前板载槽

只做诊断，不创建虚拟设备。为了读 `cmd16`/`cmd33` 仍然要短暂接管接口，
退出时（含异常）一定会把 xpad 恢复。
"""


from . import config as cfgmod
from . import status as statusmod
from . import usb
from .report import KEY_NAMES

MOTION_TARGETS = {0: 'off', 1: 'left_stick', 2: 'right_stick', 3: 'mouse'}


def audit_blob(blob):
    """检查一个 840 字节板载槽是否会影响 daemon。

    判定标准只有一条：**有没有物理键被固件吃掉**（目标 254 键盘 / 32 宏）。
    Turbo、重映射、板载体感都不阻塞。
    """
    if not blob or len(blob) < 840:
        return None
    dead, macros, remaps = [], [], []
    for k in range(32):
        target = blob[13 + k * 3]
        freq = blob[15 + k * 3]
        if target == 0xFF and freq == 0:
            continue
        name = KEY_NAMES.get(k, 'id%d' % k)
        if target == 254:
            dead.append(name)
        elif target == 32:
            macros.append(name)
        elif target == 0xFF:
            remaps.append('%s(自身 turbo %dHz)' % (name, freq))
        else:
            remaps.append('%s→%s' % (name, KEY_NAMES.get(target, 'id%d' % target)))
    motion = blob[137]
    return {
        'title': blob[770:790].decode('utf-16-le', 'replace').rstrip('\uffff\x00'),
        'data_version': int.from_bytes(blob[225:227], 'little'),
        'dead': dead,
        'macros': macros,
        'remaps': remaps,
        'motion': motion,
        'motion_name': MOTION_TARGETS.get(motion, 'unknown(%d)' % motion),
    }


def print_audit(slot, audit):
    if audit is None:
        print('板载槽 %s: 读取失败（可能是空槽）' % slot)
        return False
    ok = not audit['dead'] and not audit['macros']
    print('板载槽 %d: 标题=%r  data_version=%d'
          % (slot, audit['title'], audit['data_version']))
    if audit['dead']:
        print('  ✗ 被固件吃掉（键盘 254）: %s' % ', '.join(audit['dead']))
        print('    这些键不会出现在报文里，daemon 收不到。')
    if audit['macros']:
        print('  ✗ 宏（32）: %s' % ', '.join(audit['macros']))
    if audit['remaps']:
        print('  · 其它重映射: %s' % ', '.join(audit['remaps']))
    if not (audit['dead'] or audit['macros'] or audit['remaps']):
        print('  · 键位表: 全部默认')
    if audit['motion'] != 0:
        print('  · 板载体感: %s（XInput 下一般惰性；不需要它的话可在空间站关掉）'
              % audit['motion_name'])
    print('  → %s' % ('干净：M1/M2/FN/Turbo 都会原样上报' if ok
                      else '不可用：上面的键在 Linux 下是死键'))
    return ok


def run(cfg, deep=True):
    devcfg = cfgmod.device_section(cfg)
    dev = usb.find_pad(devcfg['vendor'], devcfg['product'],
                       devcfg.get('product_hint'))
    if not dev:
        print(usb.no_pad_message(devcfg['vendor'], devcfg['product'],
                                 devcfg.get('product_hint')))
        print('确认 dongle 插好、手柄已开机；插着别的手柄时也不会误接管。')
        return 1
    if not deep:
        print('设备: %s（--no-deep，未接管）' % dev)
        return 0
    print('设备: %s' % dev)

    pad = usb.Pad(dev, interface=devcfg['interface'])
    link = None
    try:
        print('解绑驱动: %s' % pad.takeover())
        link = usb.Link(pad)
        info = usb.identify_retry(link, tries=5)
        if not info:
            print('✗ cmd16 无应答：老方言通道不通。先跑 '
                  'scripts/flydigi_old_dialect_probe.py 排查。')
            return 1
        print('识别: DeviceType=%d uid=%s 固件=%s 电量=%d 连接=%d 当前槽位=%d'
              % (info['device_type'], info['uid'], info['fw'],
                 info['battery'], info['connect'], info['slot']))
        statusmod.write(cfg, info)

        slot = info['slot']
        print('读取板载槽 %d（约 1.5 s）…' % slot)
        blob, packets = usb.read_slot(link, slot)
        if blob is None:
            print('板载槽 %d: 只收到 %d 个包，读取失败' % (slot, packets))
            return 1
        audit = audit_blob(blob)
        ok = print_audit(slot, audit)
        return 0 if ok else 2
    finally:
        if link is not None:
            for uid in list(link.in_ids):
                pad.discard(uid)
            link.in_ids.clear()
        try:
            pad.release()
        except OSError:
            pass
        pad.reconnect_driver()
        pad.close()
        print('xpad 已恢复')
