# -*- coding: utf-8 -*-
"""把 UI 负载注入原型模板，产出可直接发布的单文件页面。

    python3 sim/build_ui.py payload.json ui/prototype.html
"""
import pathlib
import sys

TPL = pathlib.Path(__file__).resolve().parent.parent / 'ui' / 'prototype.template.html'


def main(payload, out):
    data = pathlib.Path(payload).read_text(encoding='utf-8')
    # 负载放在 <script type="application/json"> 里，只需防住 </script> 提前闭合
    data = data.replace('</', '<\\/')
    html = TPL.read_text(encoding='utf-8').replace('/*__PAYLOAD__*/', data)
    pathlib.Path(out).write_text(html, encoding='utf-8')
    print('%s  %.0f KB' % (out, len(html) / 1024))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
