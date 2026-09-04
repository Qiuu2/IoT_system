# -*- coding: utf-8 -*-
"""从设备 BOM 生成空间树与设备台账。

空间与设备型号全部来自 docs/设备BOM.csv（即 清单.xlsx），不是编造的；
只有 IP、端口、序列号、启用日期这类清单没写的字段才由本模块按规则派生。
"""
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOM = ROOT / 'docs' / '设备BOM.csv'
DRIVERS = ROOT / 'sim' / 'drivers'

# BOM 的「设备名称」→ 驱动。未命中的归入 generic-tcp（只做探活）。
DRIVER_BY_NAME = [
    ('智能电源监测管理器', 'pnc218'),
    ('电源时序器', 'pnc809'),
    ('网络电源控制器', 'rel08'),
    ('联网扩展模块', 'rs6608'),
    ('红外存储网络模块', 'ir8'),
    ('HDMI 4K无缝矩阵', 'matrix88'),
    ('高清混合管理矩阵', 'matrix88'),
    ('全数字化会议系统主机', 'chm9'),
]

# 纯软件与耗材，不进台账。
NOT_A_DEVICE = re.compile(
    r'软件|辅材|话筒杆|专用线|转换电缆|接线地座|机柜|音箱架'
    r'|系统监测模块|日志及查询模块|资产管理模块|数据分析|数据备份模块|接口开发'
    r'|管理系统|管理控制平台|信息发布对接')

# 有实体但不独立占用 IP：话筒挂在会议主机的手拉手总线上，插卡插在矩阵机箱里。
# 它们进台账（资产要盘点），但不做网络探活，记为子设备。
SUBORDINATE = re.compile(r'发言单元|信号输入卡|信号输出卡|话筒')

FLOOR_RE = re.compile(r'(\d+)F')


def driver_for(name):
    for key, drv in DRIVER_BY_NAME:
        if key in name:
            return drv
    return 'generic-tcp'


def stable_int(*parts, mod=1000):
    """由稳定的字符串派生一个确定数字，保证每次生成结果一致。"""
    h = hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()
    return int(h[:8], 16) % mod


def load_drivers():
    return {p.stem: json.loads(p.read_text(encoding='utf-8'))
            for p in sorted(DRIVERS.glob('*.json'))}


def parse_section(section):
    """『十七、4F无纸化智慧会议室（127.22㎡）』→ (楼层, 房间名, 面积)"""
    body = section.split('、', 1)[-1]
    area = None
    m = re.search(r'（([\d.]+)㎡）', body)
    if m:
        area = float(m.group(1))
        body = body[:m.start()]
    f = FLOOR_RE.search(body)
    return (f.group(1) + 'F' if f else None), body.strip(), area


def build():
    drivers = load_drivers()
    rows = list(csv.DictReader(BOM.open(encoding='utf-8-sig')))

    spaces, devices = [], []
    # 中心机房：清单『二十四、会务管理及总运维管理系统』对应的总平台所在地
    spaces.append({'id': 'SP-DC', 'name': '中心机房', 'parent': None,
                   'floor': None, 'area': None, 'type': '机房'})

    seen = {}
    for r in rows:
        section = r['section']
        if not section or '会务管理及总运维管理系统' in section:
            continue
        floor, room, area = parse_section(section)
        if not room:
            continue
        if section not in seen:
            fid = 'SP-' + (floor or 'X')
            if not any(s['id'] == fid for s in spaces):
                spaces.append({'id': fid, 'name': floor or '未标注楼层',
                               'parent': None, 'floor': floor,
                               'area': None, 'type': '楼层'})
            sid = 'SP-%s-%02d' % (floor or 'X', len([s for s in spaces
                                                     if s['parent'] == fid]) + 1)
            spaces.append({'id': sid, 'name': room, 'parent': fid, 'floor': floor,
                           'area': area, 'type': '会议室'})
            seen[section] = sid
        sid = seen[section]

        name = r['name']
        drv_id = driver_for(name)
        # 能匹配到具体驱动的一定是设备，不受 NOT_A_DEVICE 的「模块」等字样误伤。
        if drv_id == 'generic-tcp' and NOT_A_DEVICE.search(name):
            continue
        subordinate = bool(SUBORDINATE.search(name))
        try:
            qty = int(float(r['qty'] or 0))
        except ValueError:
            continue
        if qty <= 0:
            continue

        drv = drivers[drv_id]
        base_port = drv['transport']['base_port']
        floor_n = int(floor[:-1]) if floor else 99

        for i in range(qty):
            seq = len(devices) + 1
            host = 10 + stable_int(sid, name, i, mod=200)
            devices.append({
                'id': 'DEV-%04d' % seq,
                'name': name if qty == 1 else '%s #%d' % (name, i + 1),
                'model': drv['model'] if drv['model'] != '-' else (r['alias'] or '—'),
                'spec_name': r['model'] or r['name'],   # 清单原文的规格描述
                'alias': r['alias'],
                'driver': drv_id,
                'space_id': sid,
                'group': r['group'] or '—',
                'ip': None if subordinate else '10.10.%d.%d' % (floor_n, host),
                'port': None if subordinate else (502 if drv_id == 'rel08' else base_port),
                'subordinate': subordinate,
                'sn': 'SN%s' % hashlib.sha256(
                    ('%s|%s|%d' % (sid, name, i)).encode()).hexdigest()[:10].upper(),
                'unit_price': float(r['price']) if r['price'] else None,
            })
    return spaces, devices


if __name__ == '__main__':
    sp, dv = build()
    rooms = [s for s in sp if s['type'] == '会议室']
    print('空间 %d（楼层 %d / 会议室 %d / 机房 1）  设备 %d'
          % (len(sp), len([s for s in sp if s['type'] == '楼层']), len(rooms), len(dv)))
    from collections import Counter
    net = [x for x in dv if not x['subordinate']]
    print('其中网络可寻址 %d 台，子设备（话筒/插卡）%d 台'
          % (len(net), len(dv) - len(net)))
    for d, n in Counter(x['driver'] for x in net).most_common():
        print('  %-12s %3d 台' % (d, n))
