# -*- coding: utf-8 -*-
"""下行指令执行。

网关是控制权限的**最后一道闸**：点位是否可写由驱动声明，网关直接拒绝越权指令，
不依赖平台自觉。医院场景下医护对讲、防冲撞柱这类系统一旦被误控是人身安全问题，
把判断放在离设备最近的地方才可靠。
"""
import asyncio
import re

import collect
from model import CommandResult

CH_RE = re.compile(r'^ch(\d+)$')


class Commander:
    def __init__(self, devices, drivers, ports, host='127.0.0.1'):
        self.by_id = {d.device_id: d for d in devices}
        self.drivers = drivers
        self.ports = ports
        self.host = host

    async def execute(self, cmd):
        dev = self.by_id.get(cmd.device_id)
        if dev is None:
            return CommandResult(cmd.command_id, 'rejected', '未知设备')
        if cmd.expired():
            return CommandResult(cmd.command_id, 'expired',
                                 '指令已过期，拒绝补发到现场设备')
        pt = dev.point(cmd.point_key)
        if pt is None:
            return CommandResult(cmd.command_id, 'rejected', '未知点位')
        if not pt.writable:
            return CommandResult(cmd.command_id, 'rejected',
                                 '点位 %s 声明为只读，网关拒绝下发' % pt.key)

        port = self.ports.get(dev.device_id, dev.port)
        drv = self.drivers[dev.driver]
        try:
            readback = await self._write(dev, drv, pt, cmd.value, port)
        except asyncio.TimeoutError:
            return CommandResult(cmd.command_id, 'failed', '设备无响应')
        except Exception as e:                                  # noqa: BLE001
            return CommandResult(cmd.command_id, 'failed',
                                 '%s: %s' % (e.__class__.__name__, e))
        return CommandResult(cmd.command_id, 'succeeded', readback=readback)

    async def _write(self, dev, drv, pt, value, port):
        kind = drv['transport']['type']
        m = CH_RE.match(pt.key)

        if kind == 'modbus-tcp' and m:
            addr = int(m.group(1)) - 1
            await collect.write_modbus_coil(
                self.host, port, addr, bool(value),
                drv['transport'].get('unit_id', 1))
            coils = await collect.probe_modbus(
                self.host, port, drv['transport'].get('unit_id', 1),
                drv['modbus']['coil_count'])
            return coils[addr]                     # 能回读的必须回读

        eol = drv['transport'].get('line_ending', '\r\n').encode()
        if drv['id'] in ('pnc218', 'pnc809') and m:
            q = 'PWR %s %s' % (m.group(1), 'ON' if value else 'OFF')
        elif drv['id'] == 'matrix88' and m:
            q = 'SW %d %s.' % (int(value), m.group(1))
        elif drv['id'] == 'chm9' and m:
            q = 'MIC %s %s' % (m.group(1), 'ON' if value else 'OFF')
        else:
            raise ValueError('驱动 %s 未定义点位 %s 的写法' % (drv['id'], pt.key))

        resp = await collect.probe_line(self.host, port, q, eol)
        if resp.startswith('ERR'):
            raise ValueError('设备拒绝: %s' % resp)
        return resp
