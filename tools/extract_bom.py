# -*- coding: utf-8 -*-
"""从招标/报价清单 xlsx 中抽取：设备BOM、空间清单、软件功能规格。

用法: python3 tools/extract_bom.py <清单.xlsx> <输出目录>
"""
import re
import sys
import csv
from pathlib import Path

import openpyxl

SECTION_RE = re.compile(r'^[一二三四五六七八九十百]+、')


def cell(ws, r, c):
    v = ws.cell(r, c).value
    return '' if v is None else str(v).strip()


def parse(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.worksheets[0]
    rows, section, group = [], '', ''
    for r in range(3, ws.max_row + 1):
        a, name = cell(ws, r, 1), cell(ws, r, 2)
        if SECTION_RE.match(a):
            section, group = a, ''
            continue
        if not name or name == '总计':
            continue
        unit, qty, model, alias = (cell(ws, r, i) for i in (4, 5, 6, 7))
        if not unit and not qty:      # 子系统分组标题行
            group = name
            continue
        rows.append({
            'row': r, 'section': section, 'group': group, 'seq': a, 'name': name,
            'spec': cell(ws, r, 3), 'unit': unit, 'qty': qty, 'model': model,
            'alias': alias, 'tech': cell(ws, r, 8),
            'price': cell(ws, r, 9), 'total': cell(ws, r, 10),
        })
    return rows


def write_bom_csv(rows, out):
    cols = ['section', 'group', 'seq', 'name', 'unit', 'qty', 'model', 'alias', 'price', 'total']
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def write_spec_md(rows, out, wanted):
    """把指定设备的『设备参数』『主要技术参数』拆成编号条目，输出为需求规格 Markdown。"""
    item_re = re.compile(r'(?:^|\n)\s*(\d{1,3})[.、]\s*')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('# 软件功能规格（自清单技术参数列还原）\n\n')
        f.write('> 本文由 `tools/extract_bom.py` 从清单自动抽取，**未经改写**。\n')
        f.write('> 它是招标方对软件的验收口径，可直接当作 PRD 的功能基线。\n\n')
        for row in rows:
            if row['name'] not in wanted:
                continue
            f.write('\n## %s\n\n' % row['name'])
            f.write('- 型号：`%s` / %s\n' % (row['model'] or '—', row['alias'] or '—'))
            f.write('- 单价：%s 元　数量：%s %s\n\n' % (row['price'] or '—', row['qty'], row['unit']))
            if row['spec']:
                f.write('### 采购要求\n\n')
                for line in row['spec'].splitlines():
                    if line.strip():
                        f.write('%s\n' % line.strip())
                f.write('\n')
            if not row['tech']:
                continue
            f.write('### 功能条目\n\n')
            parts = item_re.split('\n' + row['tech'])
            head = parts[0].strip()
            if head:
                f.write('%s\n\n' % head)
            for num, body in zip(parts[1::2], parts[2::2]):
                body = ' '.join(body.split())
                f.write('%s. %s\n' % (num, body))
            f.write('\n')


def main():
    xlsx, outdir = sys.argv[1], Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    rows = parse(xlsx)
    write_bom_csv(rows, outdir / '设备BOM.csv')
    write_spec_md(rows, outdir / '需求规格-自清单还原.md', wanted={
        '智慧医院会议总综合运维管理控制平台', '专业会议室管理系统', '信息发布对接',
        '系统监测模块', '日志及查询模块', '资产管理模块', '数据分析及报表模块',
        '分会议室数据备份模块', '平台对接接口开发',
        '高清混合管理矩阵', '综合运维控制平台',
    })
    spaces = sorted({r['section'] for r in rows if r['section']})
    print('设备行 %d 条，空间 %d 个' % (len(rows), len(spaces)))
    for s in spaces:
        print('  ', s)


if __name__ == '__main__':
    main()
