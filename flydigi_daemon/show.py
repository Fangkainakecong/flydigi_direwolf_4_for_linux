"""映射可视化：终端表格 + 单文件 HTML（`flydigi-daemon show`）

用法::

    python3 -m flydigi_daemon show                # 终端打印四套配置
    python3 -m flydigi_daemon show --profile desktop
    python3 -m flydigi_daemon show --html         # 写 config/mappings.html
    python3 -m flydigi_daemon show --html --open  # 生成后直接开浏览器

HTML 是自包含的（无网络、无服务），双击就能看；改完 `config/user.toml`
重新生成即可。真正的映射编辑仍在 TOML 里，`show` 负责把结果画清楚。
"""

import html
import os
import time
import webbrowser
from dataclasses import dataclass

from . import config as cfgmod
from . import mapping

BUTTON_ROWS = [
    ('A', 'a'), ('B', 'b'), ('X', 'x'), ('Y', 'y'),
    ('LB', 'lb'), ('RB', 'rb'),
    ('十字 ↑', 'up'), ('十字 ↓', 'down'),
    ('十字 ←', 'left'), ('十字 →', 'right'),
    ('SELECT', 'back'), ('START', 'start'), ('HOME', 'home'),
    ('L3', 'l3'), ('R3', 'r3'),
    ('M1', 'm1'), ('M2', 'm2'), ('FN', 'fn'), ('TURBO', 'turbo'),
]

AXIS_ROWS = [
    ('LT', 'lt'), ('RT', 'rt'),
    ('左摇杆 X', 'lx'), ('左摇杆 Y', 'ly'),
    ('右摇杆 X', 'rx'), ('右摇杆 Y', 'ry'),
    ('体感 yaw', 'gyro_x'), ('体感 pitch', 'gyro_y'),
    ('体感 roll', 'gyro_z'),
]

AXIS_TO_PAD = {
    'lx': 'abs_x', 'ly': 'abs_y', 'rx': 'abs_rx', 'ry': 'abs_ry',
    'lt': 'abs_z', 'rt': 'abs_rz',
}

STANDARD_SOURCES = mapping.PAD_BUTTONS | {'up', 'down', 'left', 'right'}


@dataclass
class ProfileView:
    name: str
    slot: int
    passthrough: bool
    groups: dict
    rows: list
    axis_rows: list


def _resolve(profile, source):
    if source in mapping.AXIS_NAMES:
        rule = profile.axes.get(source)
        if rule is not None:
            return (mapping.describe_axis(rule),
                    mapping.describe_axis(rule, short=True), 'mapped')
        if profile.passthrough and source in AXIS_TO_PAD:
            return ('原样（xbox:%s）' % AXIS_TO_PAD[source], '原样', 'passthrough')
        return ('—', '—', 'none')
    rule = profile.buttons.get(source)
    if rule is not None:
        return (mapping.format_target(rule.target),
                mapping.format_target(rule.target, short=True), 'mapped')
    if profile.passthrough and source in STANDARD_SOURCES:
        return ('原样（xbox:%s）' % source, '原样', 'passthrough')
    return ('—', '—', 'none')


def build_views(cfg):
    profiles = mapping.parse_config(cfg)
    slots = cfgmod.slot_profiles(cfg)
    order = [name for name in slots if name in profiles]
    order += [name for name in profiles if name not in order]
    views = []
    for name in order:
        profile = profiles[name]
        rows = []
        for label, source in BUTTON_ROWS:
            full, short, kind = _resolve(profile, source)
            rows.append({'label': label, 'source': source,
                         'target': full, 'short': short, 'kind': kind})
        axes = []
        for label, source in AXIS_ROWS:
            full, short, kind = _resolve(profile, source)
            axes.append({'label': label, 'source': source,
                         'target': full, 'short': short, 'kind': kind})
        slot = slots.index(name) + 1 if name in slots else 0
        views.append(ProfileView(name=name, slot=slot,
                                 passthrough=profile.passthrough,
                                 groups=dict(profile.groups),
                                 rows=rows, axis_rows=axes))
    return views


def _groups_text(groups):
    if not groups:
        return '无'
    return ', '.join('%s=%s' % (name, 'on' if value else 'off')
                     for name, value in sorted(groups.items()))


def render_text(views, config_label, calibration_label=None):
    lines = ['映射预览  配置: %s' % config_label]
    slots = ['%s=%s' % ('ABXY'[view.slot - 1], view.name)
             for view in views if view.slot]
    if slots:
        lines.append('槽位: ' + '  '.join(slots))
    if calibration_label:
        lines.append('体感标定: ' + calibration_label)
    lines.append('')
    for view in views:
        head = '── %s' % view.name
        if view.slot:
            head += '（槽 %d/4，SELECT+%s）' % (view.slot, 'ABXY'[view.slot - 1])
        head += ' ── passthrough=%s  组: %s' % (
            'on' if view.passthrough else 'off', _groups_text(view.groups))
        lines.append(head)
        width = max(len(row['label']) for row in view.rows)
        for row in view.rows:
            lines.append('  %-*s  %s' % (width, row['label'], row['target']))
        if view.axis_rows:
            lines.append('  -- 轴 --')
            for row in view.axis_rows:
                lines.append('  %-*s  %s' % (width, row['label'], row['target']))
        lines.append('')
    return '\n'.join(lines)


def _esc(text):
    return html.escape(str(text), quote=True)


def _card(row):
    return ('<div class="card %s" title="%s → %s">'
            '<div class="ctl">%s</div><div class="act">%s</div></div>'
            % (_esc(row['kind']), _esc(row['label']),
               _esc(row['target']), _esc(row['label']),
               _esc(row['short'])))


def _panel(view):
    cards = ''.join(_card(row) for row in view.rows)
    axis_rows = ''.join(
        '<tr class="%s"><td>%s</td><td>%s</td></tr>'
        % (_esc(row['kind']), _esc(row['label']), _esc(row['target']))
        for row in view.axis_rows)
    slot = ('<span class="chip">槽 %d/4 · SELECT+%s</span>'
            % (view.slot, 'ABXY'[view.slot - 1])) if view.slot else ''
    return """
<section class="panel" id="p-{name}">
  <h2>{name} {slot}</h2>
  <div class="summary">passthrough <b>{pass_}</b> · 组 <b>{groups}</b></div>
  <div class="pad">{cards}</div>
  <table class="axes"><thead><tr><th>轴 / 扳机</th><th>映射</th></tr></thead>
    <tbody>{axis_rows}</tbody></table>
  <p class="hint">卡片颜色：<span class="dot mapped"></span> 已映射　
     <span class="dot passthrough"></span> 原样透传　
     <span class="dot none"></span> 无输出</p>
</section>""".format(
        name=_esc(view.name), slot=slot,
        pass_='on' if view.passthrough else 'off',
        groups=_esc(_groups_text(view.groups)),
        cards=cards, axis_rows=axis_rows)


def render_html(views, config_label, user_label=None,
                calibration_label=None):
    tabs = ''.join(
        '<button class="tab%s" data-panel="p-%s">%s · %s</button>'
        % (' active' if i == 0 else '', _esc(view.name),
           ('%d/4' % view.slot) if view.slot else '—', _esc(view.name))
        for i, view in enumerate(views))
    panels = ''.join(_panel(view) for view in views)
    user_line = ('<div class="meta">个人覆盖: %s</div>' % _esc(user_label)
                 if user_label else
                 '<div class="meta">个人覆盖: 未使用（可创建 config/user.toml）</div>')
    generated = time.strftime('%Y-%m-%d %H:%M:%S')
    cal_line = ('<div class="meta">体感标定: %s</div>'
                % _esc(calibration_label)) if calibration_label else ''
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>飞智冰原狼4 映射</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; padding: 28px; background: #12151c; color: #dce3ee;
        font: 14px/1.5 -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif; }}
h1 {{ margin: 0 0 6px; font-size: 20px; }}
.meta {{ color: #8b97ab; font-size: 12px; }}
.tabs {{ display: flex; gap: 8px; margin: 18px 0 14px; flex-wrap: wrap; }}
.tab {{ background: #1d2330; color: #aab6c8; border: 1px solid #2c3546;
        border-radius: 8px; padding: 7px 14px; cursor: pointer; font-size: 13px; }}
.tab.active {{ background: #2b6cb0; border-color: #2b6cb0; color: #fff; }}
.panel {{ display: none; }}
.panel.active {{ display: block; }}
.panel h2 {{ font-size: 16px; margin: 10px 0 4px; }}
.chip {{ background: #22304a; color: #9ec5ff; border-radius: 999px;
         padding: 2px 9px; font-size: 11px; vertical-align: middle; }}
.summary {{ color: #93a0b5; margin-bottom: 12px; font-size: 12px; }}
.pad {{ display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr));
        gap: 9px; max-width: 900px; }}
.card {{ background: #1a202b; border: 1px solid #2a3344; border-left: 4px solid #39445a;
         border-radius: 9px; padding: 8px 10px; }}
.card.mapped {{ border-left-color: #4d9cff; background: #182234; }}
.card.passthrough {{ border-left-color: #4a5568; }}
.card.none {{ opacity: .55; }}
.ctl {{ font-size: 11px; color: #8b97ab; letter-spacing: .02em; }}
.act {{ font-size: 14px; font-weight: 600; margin-top: 2px; word-break: break-word; }}
table.axes {{ border-collapse: collapse; margin-top: 14px; min-width: 520px; }}
table.axes th, table.axes td {{ text-align: left; padding: 6px 14px 6px 8px;
        border-bottom: 1px solid #232c3b; font-size: 13px; }}
table.axes th {{ color: #8b97ab; font-weight: 500; }}
tr.mapped td {{ color: #cfe4ff; }}
tr.passthrough td {{ color: #a3adbd; }}
tr.none td {{ color: #6b7488; }}
.hint {{ color: #77839a; font-size: 12px; margin-top: 16px; }}
.dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px;
        background: #39445a; margin-right: 4px; vertical-align: middle; }}
.dot.mapped {{ background: #4d9cff; }}
.dot.passthrough {{ background: #4a5568; }}
.dot.none {{ background: #39445a; }}
</style></head>
<body>
<h1>飞智冰原狼4 · 映射</h1>
<div class="meta">配置: {config}</div>
{user_line}
{cal_line}
<div class="meta">生成于 {generated} · 修改 config/user.toml 后重新运行 show</div>
<div class="tabs">{tabs}</div>
{panels}
<script>
document.querySelectorAll('.tab').forEach(function (button) {{
  button.addEventListener('click', function () {{
    document.querySelectorAll('.tab').forEach(function (b) {{
      b.classList.toggle('active', b === button);
    }});
    document.querySelectorAll('.panel').forEach(function (p) {{
      p.classList.toggle('active',
        p.id === button.getAttribute('data-panel'));
    }});
  }});
}});
</script>
</body></html>
""".format(config=_esc(config_label), user_line=user_line,
           generated=generated, tabs=tabs, panels=panels, cal_line=cal_line)


def write_html(path, views, config_label, user_label=None,
               calibration_label=None):
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(render_html(views, config_label, user_label,
                            calibration_label))
    return os.path.abspath(path)


def run(cfg, args):
    views = build_views(cfg)
    if args.profile:
        views = [v for v in views if v.name == args.profile]
        if not views:
            print('没有 profile %r' % (args.profile,))
            return 2
    config_label = 'config/default.toml'
    if os.path.exists(cfgmod.USER_PATH):
        config_label += ' + config/user.toml'
    cal = mapping.load_calibration(cfgmod.calibration_file(cfg))
    if cal:
        cal_label = '已加载（更新率 %s Hz）' % cal.get('update_rate_hz', '?')
    else:
        cal_label = '未标定（运行 calibrate 生成）'
    if not args.quiet:
        print(render_text(views, config_label, cal_label))
    if args.html is not None:
        path = write_html(args.html, views, config_label,
                          'config/user.toml' if os.path.exists(cfgmod.USER_PATH)
                          else None, cal_label)
        print('已生成: %s' % path)
        if args.open:
            webbrowser.open('file://' + path)
    return 0
