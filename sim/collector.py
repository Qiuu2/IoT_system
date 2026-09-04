# -*- coding: utf-8 -*-
"""采集器：真的用各设备的协议去问一遍，把结果归一化成统一的读数模型。

这就是 L1 的读路径。将来换成真设备，只有本文件里的 client 需要改，
上层（归一化后的 Reading）不动。
"""
import asyncio
import struct
import time

CONNECT_TIMEOUT = 1.5
READ_TIMEOUT = 1.5


class Reading:
    """归一化读数——上层只认这个结构，不关心底下是 ASCII 还是 Modbus。"""

    def __init__(self, device_id):
        self.device_id = device_id
        self.online = False
        self.latency_ms = None
        self.metrics = {}       # {'voltage': 231.4, 'current': 8.2}
        self.channels = None    # [True, False, ...]
        self.extra = {}         # 驱动声明的非数值字段，如矩阵路由
        self.raw = None         # 原始回应，排障用
        self.error = None
        self.ts = time.time()

    def as_dict(self):
        return {'device_id': self.device_id, 'online': self.online,
                'latency_ms': self.latency_ms, 'metrics': self.metrics,
                'channels': self.channels, 'extra': self.extra,
                'raw': self.raw, 'error': self.error,
                'ts': round(self.ts, 3)}


async def _open(host, port):
    return await asyncio.wait_for(
        asyncio.open_connection(host, port), CONNECT_TIMEOUT)


async def probe_line(host, port, query='STAT?', eol=b'\r\n'):
    """ASCII 行协议：发一条查询，读一行。"""
    r, w = await _open(host, port)
    try:
        w.write(query.encode() + eol)
        await w.drain()
        line = await asyncio.wait_for(r.readline(), READ_TIMEOUT)
        return line.decode('ascii', 'ignore').strip()
    finally:
        w.close()


async def probe_modbus(host, port, unit=1, count=8):
    """Modbus-TCP FC1 读线圈。真实报文格式。"""
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
        nb = body[1]
        data = body[2:2 + nb]
        return [bool(data[i // 8] & (1 << (i % 8))) for i in range(count)]
    finally:
        w.close()


async def probe_tcp(host, port):
    """只做 TCP 建连探活，对应截图里的『检测方式 PING』。"""
    r, w = await _open(host, port)
    w.close()
    return True


def _parse_fields(text, fields):
    """按驱动声明的字段顺序解析回应，不猜语义。

    fields 里的名字决定每个 token 落到哪：channel_bits/routing 是结构，
    其余按数值指标处理。驱动没声明的东西一律不产出。
    """
    metrics, channels, extra = {}, None, {}
    toks = text.split()
    if not toks:
        return metrics, channels, extra
    body = toks[1:]                      # 第一个 token 是回应头（STAT / STA）
    for name, tok in zip(fields, body):
        if name == 'channel_bits':
            if set(tok) <= {'0', '1'}:
                channels = [c == '1' for c in tok]
            continue
        if name == 'routing':
            extra['routing'] = tok
            continue
        try:
            metrics[name] = float(tok)
        except ValueError:
            extra[name] = tok
    return metrics, channels, extra


async def read_device(dev, driver, host='127.0.0.1', port=None):
    """按驱动声明的 probe 方式探测，返回归一化 Reading。"""
    out = Reading(dev['id'])
    port = port or dev['port']
    if port is None:                       # 子设备（话筒/插卡）不做网络探活
        out.error = 'subordinate'
        return out
    probe = driver.get('probe') or {'mode': 'tcp'}
    t0 = time.perf_counter()
    try:
        mode = probe['mode']
        if mode == 'modbus':
            out.channels = await probe_modbus(
                host, port, driver['transport'].get('unit_id', 1),
                driver['modbus']['coil_count'])
        elif mode == 'line':
            eol = driver['transport'].get('line_ending', '\r\n').encode()
            text = await probe_line(host, port, probe['query'], eol)
            if text.startswith('ERR'):
                raise ValueError('设备拒绝指令: %s' % text)
            out.metrics, out.channels, out.extra = _parse_fields(
                text, probe.get('fields', []))
            out.raw = text
        else:
            await probe_tcp(host, port)
        out.online = True
    except asyncio.TimeoutError:
        out.error = 'timeout'
    except (ConnectionRefusedError, OSError) as e:
        out.error = 'unreachable: %s' % e.__class__.__name__
    except Exception as e:                                    # noqa: BLE001
        out.error = '%s: %s' % (e.__class__.__name__, e)
    out.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return out


async def sweep(devices, drivers, ports, concurrency=64):
    """并发轮询全部设备。"""
    sem = asyncio.Semaphore(concurrency)

    async def one(d):
        async with sem:
            return await read_device(d, drivers[d['driver']],
                                     port=ports.get(d['id']))
    return await asyncio.gather(*(one(d) for d in devices))
