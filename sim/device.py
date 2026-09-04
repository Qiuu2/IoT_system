# -*- coding: utf-8 -*-
"""模拟设备运行时。

每个模拟设备是一个 asyncio TCP server。协议分三类：
  LineDevice        ASCII 行协议（时序电源、红外、矩阵、会议主机）—— 指令集是 guessed
  ModbusDevice      真实的 Modbus-TCP 帧（网络继电器）—— 标准协议，做的是真的
  PassthroughDevice 串口服务器透传，8 个端口各自独立
  SilentDevice      只接受连接不回数据，对应「PING/TCP 探活」那一类
"""
import asyncio
import math
import random
import struct
import time


class BaseDevice:
    def __init__(self, meta, driver, port):
        self.meta = meta
        self.driver = driver
        self.port = port
        self.rx = 0          # 收到的指令条数
        self.tx = 0          # 回应条数
        self.last_seen = None

    async def start(self):
        self.server = await asyncio.start_server(
            self._client, '127.0.0.1', self.port)
        return self

    async def _client(self, reader, writer):
        raise NotImplementedError

    def close(self):
        self.server.close()


# --------------------------------------------------------------------------
class PowerSequencer:
    """时序电源的共同状态：通道开关 + 电压电流遥测。

    电压按正弦漂移叠加噪声；被标记为 fault 的设备会漂进 <195V 或 >245V 的
    告警区间——清单原文写明超出这个范围面板会闪亮警示。
    """

    def __init__(self, channels, telemetry, fault=None, seed=0):
        self.channels = [False] * channels
        self.tel = telemetry
        self.fault = fault
        self.rnd = random.Random(seed)
        self.t0 = time.time()

    def voltage(self):
        v = self.tel.get('voltage')
        if not v:
            return None
        phase = (time.time() - self.t0) / 47.0
        base = v['nominal'] + math.sin(phase) * (v['jitter'] / 2)
        base += self.rnd.uniform(-1.2, 1.2)
        if self.fault == 'overvoltage':
            base += 30
        elif self.fault == 'undervoltage':
            base -= 32
        return round(base, 1)

    def current(self):
        c = self.tel.get('current')
        if not c:
            return None
        on = sum(self.channels)
        amps = on * c['per_channel_on'] + self.rnd.uniform(0, c['jitter'])
        return round(amps, 2)

    def bits(self):
        return ''.join('1' if x else '0' for x in self.channels)

    def set(self, ch, on):
        if 1 <= ch <= len(self.channels):
            self.channels[ch - 1] = on
            return True
        return False


class LineDevice(BaseDevice):
    """ASCII 行协议。指令语法来自 driver json 的 commands 段（guessed）。"""

    def __init__(self, meta, driver, port, fault=None):
        super().__init__(meta, driver, port)
        self.eol = driver['transport'].get('line_ending', '\r\n').encode()
        seed = int(meta['sn'][2:8], 16)
        self.power = None
        if driver.get('telemetry'):
            self.power = PowerSequencer(
                driver.get('channels', 8), driver['telemetry'], fault, seed)
            # 开机默认点亮一部分通道，否则电流恒为 0，看不出负载差异
            rnd = random.Random(seed)
            for i in range(len(self.power.channels)):
                self.power.channels[i] = rnd.random() < 0.55
        self.routing = {i: i for i in range(1, driver.get('outputs', 0) + 1)}
        self.mics = set()

    async def _client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                self.rx += 1
                self.last_seen = time.time()
                resp = self.handle(line.decode('ascii', 'ignore').strip())
                if resp is not None:
                    writer.write(resp.encode('ascii') + self.eol)
                    await writer.drain()
                    self.tx += 1
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    def handle(self, cmd):
        p = cmd.replace('.', ' ').split()
        if not p:
            return None
        head = p[0].upper()

        if head == 'STAT?' or head == 'STA':
            if self.power:
                v, c = self.power.voltage(), self.power.current()
                if c is None:
                    return 'STAT %.1f %s' % (v, self.power.bits())
                return 'STAT %.1f %.2f %s' % (v, c, self.power.bits())
            if self.routing:
                return 'STA ' + ','.join(
                    '%d>%d' % (v, k) for k, v in sorted(self.routing.items()))
            return 'STAT %d' % len(self.mics)

        if head == 'PWR' and len(p) >= 3 and self.power:
            try:
                ch = int(p[1])
            except ValueError:
                return 'ERR ARG'
            on = p[2].upper() == 'ON'
            return 'OK %d %d' % (ch, on) if self.power.set(ch, on) else 'ERR RANGE'

        if head == 'SEQ' and len(p) >= 2 and self.power:
            on = p[1].upper() == 'ON'
            for i in range(len(self.power.channels)):
                self.power.channels[i] = on
            return 'OK SEQ %d' % on

        if head == 'SW' and len(p) >= 3:
            try:
                inp = int(p[1])
            except ValueError:
                return 'ERR ARG'
            if p[2].upper() == 'ALL':
                for o in self.routing:
                    self.routing[o] = inp
                return 'SW %d ALL OK' % inp
            outp = int(p[2])
            if outp not in self.routing:
                return 'ERR RANGE'
            self.routing[outp] = inp
            return 'SW %d %d OK' % (inp, outp)

        if head == 'IR' and len(p) >= 3:
            return 'OK %s %s' % (p[1], p[2])

        if head == 'MIC' and len(p) >= 3:
            on = p[2].upper() == 'ON'
            if p[1].upper() == 'ALL':
                self.mics.clear()
                return 'OK MIC ALL 0'
            seat = int(p[1])
            self.mics.add(seat) if on else self.mics.discard(seat)
            return 'OK MIC %d %d' % (seat, on)

        return 'ERR CMD'


# --------------------------------------------------------------------------
class ModbusDevice(BaseDevice):
    """真实的 Modbus-TCP：MBAP 头 + FC1 读线圈 / FC5 写单线圈 / FC15 写多线圈。

    清单写明 REL08-1U 支持 Modbus-TCP，而 Modbus 是公开标准，
    所以这一个模拟器的报文格式和真机是一致的，不是猜的。
    """
    EX_ILLEGAL_FUNC, EX_ILLEGAL_ADDR = 0x01, 0x02

    def __init__(self, meta, driver, port):
        super().__init__(meta, driver, port)
        self.n = driver['modbus']['coil_count']
        self.coils = [False] * self.n
        self.unit = driver['transport'].get('unit_id', 1)

    async def _client(self, reader, writer):
        try:
            while True:
                head = await reader.readexactly(7)
                tid, pid, ln, unit = struct.unpack('>HHHB', head)
                pdu = await reader.readexactly(ln - 1)
                self.rx += 1
                self.last_seen = time.time()
                resp = self.pdu(pdu)
                out = struct.pack('>HHHB', tid, 0, len(resp) + 1, unit) + resp
                writer.write(out)
                await writer.drain()
                self.tx += 1
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    def err(self, func, code):
        return struct.pack('>BB', func | 0x80, code)

    def pdu(self, pdu):
        func = pdu[0]
        if func == 1:                                    # 读线圈
            addr, qty = struct.unpack('>HH', pdu[1:5])
            if addr + qty > self.n or qty == 0:
                return self.err(func, self.EX_ILLEGAL_ADDR)
            nb = (qty + 7) // 8
            data = bytearray(nb)
            for i in range(qty):
                if self.coils[addr + i]:
                    data[i // 8] |= 1 << (i % 8)
            return struct.pack('>BB', func, nb) + bytes(data)
        if func == 5:                                    # 写单线圈
            addr, val = struct.unpack('>HH', pdu[1:5])
            if addr >= self.n:
                return self.err(func, self.EX_ILLEGAL_ADDR)
            self.coils[addr] = (val == 0xFF00)
            return pdu[:5]
        if func == 15:                                   # 写多线圈
            addr, qty = struct.unpack('>HH', pdu[1:5])
            if addr + qty > self.n:
                return self.err(func, self.EX_ILLEGAL_ADDR)
            data = pdu[6:]
            for i in range(qty):
                self.coils[addr + i] = bool(data[i // 8] & (1 << (i % 8)))
            return struct.pack('>BHH', func, addr, qty)
        return self.err(func, self.EX_ILLEGAL_FUNC)


# --------------------------------------------------------------------------
class PassthroughDevice(BaseDevice):
    """串口服务器：TCP 收到什么就当作串口字节流。挂在其后的设备用回声代替。"""

    async def _client(self, reader, writer):
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                self.rx += 1
                self.last_seen = time.time()
                writer.write(b'<' + data.strip() + b'\r\n')
                await writer.drain()
                self.tx += 1
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()


class SilentDevice(BaseDevice):
    """只接受连接，不回数据。对应截图里『检测方式 PING』那一类。"""

    async def _client(self, reader, writer):
        self.last_seen = time.time()
        try:
            await reader.read(256)
        except ConnectionResetError:
            pass
        finally:
            writer.close()


def make(meta, driver, port, fault=None):
    t = driver['transport']['type']
    if t == 'modbus-tcp':
        return ModbusDevice(meta, driver, port)
    if t == 'tcp-passthrough':
        return PassthroughDevice(meta, driver, port)
    if driver['id'] == 'generic-tcp':
        return SilentDevice(meta, driver, port)
    return LineDevice(meta, driver, port, fault)
