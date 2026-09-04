# -*- coding: utf-8 -*-
"""设备与点位注册表：把现场清单 + 驱动定义合成契约里的 Device/Point。"""
import json
from pathlib import Path

from model import Device, Point

ROOT = Path(__file__).resolve().parent
DRIVERS = ROOT / 'drivers'

# 驱动的 transport.type → 契约里的 protocol 枚举
PROTOCOL = {
    'tcp': 'tcp', 'modbus-tcp': 'modbus-tcp',
    'tcp-passthrough': 'tcp-passthrough', 'serial': 'serial',
}


def load_drivers():
    return {p.stem: json.loads(p.read_text(encoding='utf-8'))
            for p in sorted(DRIVERS.glob('*.json'))}


def points_of(driver):
    return [Point(key=p['key'], name=p['name'], kind=p.get('kind', 'read'),
                  datatype=p.get('datatype', 'number'), unit=p.get('unit'))
            for p in driver.get('points', [])]


def build(inventory, drivers, gateway_id='GW-01'):
    """inventory: 现场设备清单（dict 列表），来自 BOM / 现场勘查 / 厂商导出。"""
    out = []
    for it in inventory:
        drv = drivers[it['driver']]
        out.append(Device(
            device_id=it.get('device_id') or '%s-%s' % (gateway_id, it['id']),
            name=it['name'],
            driver=it['driver'],
            model=drv.get('model') if drv.get('model') != '-' else it.get('model'),
            vendor=it.get('vendor'),
            subsystem=it.get('subsystem'),
            space_ref=it.get('space_id'),
            ip=it.get('ip'), port=it.get('port'),
            protocol=PROTOCOL.get(drv['transport']['type'], 'vendor-sdk'),
            confidence={'transport': drv['confidence']['transport'],
                        'commands': drv['confidence']['commands']},
            points=points_of(drv),
        ))
    return out
