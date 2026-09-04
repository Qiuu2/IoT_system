# -*- coding: utf-8 -*-
"""采集：按驱动声明的 probe 方式读设备，产出契约里的 Reading。

与 sim/collector.py 的区别：这里输出的是**契约结构**（samples 列表），
sim 那边是开发期的内部结构。真设备到货后，需要改的只有本文件的协议客户端。
"""
import asyncio
import struct
import time

from model import Reading, Sample, now_iso

CONNECT_TIMEOUT = 1.5
READ_TIMEOUT = 1.5


async def _open(host, port):
    return await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)


async def probe_line(host, port, query, eol=b'\r\n'):
    r, w = await _open(host, port)
    try:
        w.write(query.encode() + eol)
        await w.drain()
        line = await asyncio.wait_for(r.readline(), READ_TIMEOUT)
        return line.decode('ascii', 'ignore').strip()
    finally:
        w.close()


async def probe_modbus(host, port, unit=1, count=8):
    r, w = await _open(host, port)
    try:
        pdu = struct.pack('>BHH', 1, 0, count)
        w.write(struct.pack('>HHHB', 1, 0, len(pdu) + 1, unit) + pdu)
        await w.drain()
        head = await asyncio.wait_for(r.readexactly(7), READ_TIMEOUT)
        _, _, ln, _ = struct.unpack('>HHHB', head)
        body = await asyncio.wait_for(r.readexactly(ln - 1), READ_TIMEOUT)
        if body[0] & 0x80:
            raise ValueError('modbus exception %d' % body[1])
        data = body[2:2 + body[1]]
        return [bool(data[i // 8] & (1 << (i % 8))) for i in range(count)]
    finally:
        w.close()


async def write_modbus_coil(host, port, addr, value, unit=1):
    r, w = await _open(host, port)
    try:
        pdu = struct.pack('>BHH', 5, addr, 0xFF00 if value else 0x0000)
        w.write(struct.pack('>HHHB', 7, 0, len(pdu) + 1, unit) + pdu)
        await w.drain()
        await asyncio.wait_for(r.readexactly(12), READ_TIMEOUT)
    finally:
        w.close()


async def probe_tcp(host, port):
    _, w = await _open(host, port)
    w.close()


def _samples_from_line(text, fields):
    """按驱动声明的字段顺序解析，驱动没声明的一律不产出。"""
    out, toks = [], text.split()
    for name, tok in zip(fields, toks[1:]):
        if name == 'channel_bits':
            if set(tok) <= {'0', '1'}:
                out += [Sample('ch%d' % (i + 1), c == '1') for i, c in enumerate(tok)]
            continue
        if name == 'routing':
            for pair in tok.split(','):
                if '>' in pair:
                    src, dst = pair.split('>')
                    out.append(Sample('ch%s' % dst, float(src)))
            continue
        try:
            out.append(Sample(name, float(tok)))
        except ValueError:
            out.append(Sample(name, tok))
    return out


async def read_device(dev, driver, host='127.0.0.1', port=None):
    port = port if port is not None else dev.port
    if port is None:
        return Reading(dev.device_id, online=False, error='subordinate')
    probe = driver.get('probe') or {'mode': 'tcp'}
    t0 = time.perf_counter()
    samples, err, online = [], None, False
    try:
        mode = probe['mode']
        if mode == 'modbus':
            coils = await probe_modbus(host, port,
                                       driver['transport'].get('unit_id', 1),
                                       driver['modbus']['coil_count'])
            samples = [Sample('ch%d' % (i + 1), v) for i, v in enumerate(coils)]
        elif mode == 'line':
            eol = driver['transport'].get('line_ending', '\r\n').encode()
            text = await probe_line(host, port, probe['query'], eol)
            if text.startswith('ERR'):
                raise ValueError('设备拒绝指令: %s' % text)
            samples = _samples_from_line(text, probe.get('fields', []))
        else:
            await probe_tcp(host, port)
        online = True
    except asyncio.TimeoutError:
        err = 'timeout'
    except (ConnectionRefusedError, OSError) as e:
        err = 'unreachable: %s' % e.__class__.__name__
    except Exception as e:                                     # noqa: BLE001
        err = '%s: %s' % (e.__class__.__name__, e)
    return Reading(dev.device_id, online=online,
                   latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                   samples=samples, error=err, ts=now_iso())


async def sweep(devices, drivers, ports, concurrency=64):
    sem = asyncio.Semaphore(concurrency)

    async def one(d):
        async with sem:
            return await read_device(d, drivers[d.driver], port=ports.get(d.device_id))
    return await asyncio.gather(*(one(d) for d in devices))
