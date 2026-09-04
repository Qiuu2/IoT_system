# -*- coding: utf-8 -*-
"""接入网关主程序。

    python3 gateway/main.py --platform http://127.0.0.1:8090/api/v1

流程：注册 → 循环（采集 → 判事件 → 入队 → 推送 → 心跳 → 拉指令 → 执行 → 回执）。

设备来源目前是开发期的 sim/topology（由招标 BOM 生成）。现场部署时替换为
真实清单即可，本文件与其余模块不用改。
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'sim')]

import collect                                    # noqa: E402
import registry                                   # noqa: E402
from commander import Commander                   # noqa: E402
from model import Command, Event                  # noqa: E402
from publisher import Publisher                   # noqa: E402

LIMITS = {'voltage': (195.0, 245.0), 'current': (None, 48.0)}


def load_inventory():
    """开发期：从招标 BOM 生成。现场：换成真实清单导入。"""
    import topology
    _, devices = topology.build()
    return [d for d in devices if not d['subordinate']]


def detect_events(readings, prev_online):
    """只报事实（上下线、越限、越限恢复），聚合与派单由平台负责。"""
    events = []
    for r in readings:
        was = prev_online.get(r.device_id)
        if was is not None and was != r.online:
            events.append(Event(
                device_id=r.device_id,
                type='device_online' if r.online else 'device_offline',
                code='DEV_ONLINE' if r.online else 'DEV_OFFLINE',
                severity='info' if r.online else 'major',
                detail=None if r.online else (r.error or '探测失败')))
        prev_online[r.device_id] = r.online
        if not r.online:
            continue
        for s in r.samples:
            lo, hi = LIMITS.get(s.key, (None, None))
            if not isinstance(s.value, (int, float)) or isinstance(s.value, bool):
                continue
            over = (hi is not None and s.value > hi) or (lo is not None and s.value < lo)
            if over:
                code = 'OVER_VOLTAGE' if (hi and s.value > hi and s.key == 'voltage') \
                    else ('UNDER_VOLTAGE' if s.key == 'voltage' else 'OVER_CURRENT')
                events.append(Event(device_id=r.device_id, point_key=s.key,
                                    type='limit_exceeded', code=code,
                                    severity='critical', value=s.value,
                                    detail='实测 %.1f' % s.value))
    return events


async def run(args):
    drivers = registry.load_drivers()
    inv = load_inventory()
    devices = registry.build(inv, drivers, args.gateway_id)
    ports = {}                       # 开发期指向模拟器端口；现场为空，走设备真实端口
    if args.sim_base:
        addressable = [d for d in devices if d.port is not None]
        ports = {d.device_id: args.sim_base + i for i, d in enumerate(addressable)}

    pub = Publisher(args.platform, args.gateway_id, args.token, args.spool)
    cmdr = Commander(devices, drivers, ports)

    pollable = [d for d in devices if d.port is not None]
    print('设备 %d（可寻址 %d）· 点位 %d'
          % (len(devices), len(pollable), sum(len(d.points) for d in devices)))

    try:
        _, body = pub.sync_registry(devices)
        print('注册成功：设备 %s · 点位 %s'
              % (body.get('accepted_devices'), body.get('accepted_points')))
    except Exception as e:                                       # noqa: BLE001
        print('注册失败（%s），继续运行，数据先落盘' % e.__class__.__name__)

    prev_online = {}
    while True:
        t0 = time.perf_counter()
        readings = await collect.sweep(pollable, drivers, ports)
        sweep_ms = round((time.perf_counter() - t0) * 1000, 1)
        events = detect_events(readings, prev_online)

        pub.queue_telemetry(readings)
        pub.queue_events(events)
        sent = pub.flush()

        online = sum(1 for r in readings if r.online)
        try:
            pub.heartbeat(len(pollable), online, sweep_ms)
        except Exception:                                        # noqa: BLE001
            pass

        executed = 0
        if pub.online:
            try:
                for raw in pub.pull_commands():
                    res = await cmdr.execute(Command.from_json(raw))
                    pub.report_result(res)
                    executed += 1
            except Exception:                                    # noqa: BLE001
                pass

        print('[%s] 采集 %d · 在线 %d · 事件 %d · 已发批次 %d · 队列 %d · 指令 %d · %sms'
              % (time.strftime('%H:%M:%S'), len(readings), online, len(events),
                 sent, pub.depth(), executed, sweep_ms), flush=True)
        await asyncio.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--platform', default='http://127.0.0.1:8090/api/v1')
    ap.add_argument('--gateway-id', default='GW-01')
    ap.add_argument('--token', default='dev-token')
    ap.add_argument('--interval', type=int, default=10)
    ap.add_argument('--spool', default='var/spool')
    ap.add_argument('--sim-base', type=int, default=21000,
                    help='开发期对接模拟器的起始端口；现场部署传 0 表示走设备真实端口')
    a = ap.parse_args()
    if a.sim_base == 0:
        a.sim_base = None
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
