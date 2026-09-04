# -*- coding: utf-8 -*-
"""启动全部模拟设备 + 采集循环 + HTTP API。

    python3 sim/run.py                 # 起服务，默认 http://127.0.0.1:8080
    python3 sim/run.py --export a.json # 采集一轮，导出数据集后退出（喂给 UI 原型）
    python3 sim/run.py --selftest      # 自检：验证每种协议真的能通

故意让一部分设备离线、一部分电压越界——全在线的数据看不出 UI 问题。
"""
import argparse
import asyncio
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import collector
import device as devmod
import topology
import world

ROOT = Path(__file__).resolve().parent
STATE = {'readings': [], 'alarms': [], 'updated_at': None, 'sweep_ms': None}
ALARM_STORE = {}       # (device_id, code) -> 告警，跨轮次聚合


SIM_PORT_BASE = 21000


def plan(devices):
    """分配模拟端口，并确定哪些设备『坏掉』。真实部署里这些由现场决定。

    真实部署中每台设备有独立 IP，端口可以重复（都是 502、8801…）；模拟器
    全挤在 127.0.0.1 上，所以另开一段 21000+ 的本地端口。设备记录里的
    `port` 始终是真机端口，两者不混淆。
    """
    ports, faults, offline = {}, {}, set()
    seq = 0
    for d in devices:
        if d['port'] is None:
            continue
        ports[d['id']] = SIM_PORT_BASE + seq
        seq += 1
        if topology.stable_int(d['id'], 'fault', mod=100) < 8:      # ~8% 离线
            offline.add(d['id'])

    # 电压越界只对有电压遥测的设备有意义，否则注入了也观测不到。
    powered = [d for d in devices
               if d['driver'] in ('pnc218', 'pnc809')
               and d['id'] in ports and d['id'] not in offline]
    powered.sort(key=lambda d: topology.stable_int(d['id'], 'volt'))
    for d in powered[:2]:
        faults[d['id']] = 'overvoltage'
    for d in powered[2:3]:
        faults[d['id']] = 'undervoltage'
    return ports, faults, offline


async def boot(devices, drivers, ports, faults, offline):
    started = []
    for d in devices:
        if d['id'] not in ports or d['id'] in offline:
            continue
        srv = devmod.make(d, drivers[d['driver']], ports[d['id']],
                          faults.get(d['id']))
        await srv.start()
        started.append(srv)
    return started


async def sweep_loop(devices, drivers, ports, interval=10):
    pollable = [d for d in devices if d['id'] in ports]
    while True:
        t0 = time.perf_counter()
        readings = await collector.sweep(pollable, drivers, ports)
        now = datetime.now()
        STATE['readings'] = readings
        world.merge_alarms(ALARM_STORE, readings, devices, now)
        STATE['alarms'] = list(ALARM_STORE.values())
        STATE['sweep_ms'] = round((time.perf_counter() - t0) * 1000, 1)
        STATE['updated_at'] = now.isoformat(timespec='seconds')
        on = sum(1 for r in readings if r.online)
        print('[%s] 轮询 %d 台 · 在线 %d · 离线 %d · 告警 %d · 耗时 %sms'
              % (now.strftime('%H:%M:%S'), len(readings), on,
                 len(readings) - on, len(STATE['alarms']), STATE['sweep_ms']),
              flush=True)
        await asyncio.sleep(interval)


# --------------------------------------------------------------------------
def build_api(spaces, devices, bookings):
    by_dev = {d['id']: d for d in devices}

    def overview():
        rs = STATE['readings']
        on = sum(1 for r in rs if r.online)
        al = STATE['alarms']
        today = datetime.now().date().isoformat()
        todays = [b for b in bookings if b['start'][:10] == today]
        return {
            'updated_at': STATE['updated_at'], 'sweep_ms': STATE['sweep_ms'],
            'device': {'total': len(rs), 'online': on, 'offline': len(rs) - on},
            'alarm': {'total': len(al),
                      'pending': sum(1 for a in al if a['state'] == '待处理'),
                      'critical': sum(1 for a in al if a['level'] == '严重')},
            'booking': {'total': len(bookings),
                        'today': len(todays),
                        'upcoming': sum(1 for b in bookings if b['state'] == '待开始'),
                        'running': sum(1 for b in bookings if b['state'] == '进行中'),
                        'done': sum(1 for b in bookings if b['state'] == '已结束'),
                        'cancelled': sum(1 for b in bookings if b['state'] == '已取消')},
            'space': {'total': sum(1 for s in spaces if s['type'] == '会议室')},
        }

    ROUTES = {
        '/api/overview': lambda q: overview(),
        '/api/spaces': lambda q: spaces,
        '/api/devices': lambda q: [
            dict(by_dev[r.device_id], **r.as_dict()) for r in STATE['readings']
        ] if STATE['readings'] else devices,
        '/api/readings': lambda q: [r.as_dict() for r in STATE['readings']],
        '/api/alarms': lambda q: STATE['alarms'],
        '/api/bookings': lambda q: [
            b for b in bookings
            if not q.get('date') or b['start'][:10] == q['date'][0]
        ],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            fn = ROUTES.get(u.path.rstrip('/') or '/api/overview')
            if not fn:
                self.send_error(404, 'no such endpoint')
                return
            body = json.dumps(fn(parse_qs(u.query)), ensure_ascii=False,
                              default=str).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler, ROUTES


# --------------------------------------------------------------------------
async def selftest(devices, drivers, ports, faults, offline):
    """逐个协议族验证：模拟器说的话，采集器听得懂。"""
    srvs = await boot(devices, drivers, ports, faults, offline)
    await asyncio.sleep(0.2)
    ok = True
    for drv_id in ('pnc218', 'pnc809', 'rel08', 'rs6608', 'ir8',
                   'matrix88', 'chm9', 'generic-tcp'):
        d = next((x for x in devices
                  if x['driver'] == drv_id and x['id'] in ports
                  and x['id'] not in offline), None)
        if not d:
            print('  %-12s 跳过（该型号全部被判离线）' % drv_id)
            continue
        r = await collector.read_device(d, drivers[drv_id], port=ports[d['id']])
        detail = []
        if r.metrics:
            detail.append(' '.join('%s=%s' % kv for kv in r.metrics.items()))
        if r.channels is not None:
            detail.append('通道 %d/%d 开' % (sum(r.channels), len(r.channels)))
        print('  %-12s %s  %sms  %s' % (
            drv_id, '在线' if r.online else '失败 ' + str(r.error),
            r.latency_ms, ' · '.join(detail)))
        ok &= r.online
    # 写指令验证：Modbus 写线圈后读回
    d = next((x for x in devices if x['driver'] == 'rel08'
              and x['id'] in ports and x['id'] not in offline), None)
    if d:
        import struct
        host, port = '127.0.0.1', ports[d['id']]
        rd, wr = await asyncio.open_connection(host, port)
        pdu = struct.pack('>BHH', 5, 2, 0xFF00)          # 第3路闭合
        wr.write(struct.pack('>HHHB', 9, 0, len(pdu) + 1, 1) + pdu)
        await wr.drain()
        await rd.readexactly(12)
        wr.close()
        coils = await collector.probe_modbus(host, port)
        print('  %-12s 写线圈#3 后读回 → %s  %s'
              % ('rel08(写)', ''.join('1' if c else '0' for c in coils),
                 'OK' if coils[2] else '失败'))
        ok &= coils[2]
    for s in srvs:
        s.close()
    return ok


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--interval', type=int, default=10)
    ap.add_argument('--export')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()

    drivers = topology.load_drivers()
    spaces, devices = topology.build()
    ports, faults, offline = plan(devices)
    rooms = [s for s in spaces if s['type'] == '会议室']
    now = datetime.now()
    bookings = world.make_bookings(rooms, now)

    print('空间 %d（会议室 %d）· 设备 %d（可寻址 %d）· 预约 %d'
          % (len(spaces), len(rooms), len(devices), len(ports), len(bookings)))
    print('注入故障：离线 %d 台 · 过压 %d 台 · 欠压 %d 台'
          % (len(offline), sum(1 for v in faults.values() if v == 'overvoltage'),
             sum(1 for v in faults.values() if v == 'undervoltage')))

    if args.selftest:
        print('\n协议自检：')
        ok = await selftest(devices, drivers, ports, faults, offline)
        print('\n自检%s' % ('通过' if ok else '未通过'))
        raise SystemExit(0 if ok else 1)

    srvs = await boot(devices, drivers, ports, faults, offline)
    print('已启动 %d 个模拟设备' % len(srvs))
    await asyncio.sleep(0.3)

    if args.export:
        pollable = [d for d in devices if d['id'] in ports]
        store, readings = {}, []
        for _ in range(12):                     # 多轮采集，让告警计数真的累积
            readings = await collector.sweep(pollable, drivers, ports)
            world.merge_alarms(store, readings, devices, datetime.now())
            await asyncio.sleep(0.15)
        alarms = list(world.backfill_history(store, now).values())
        data = {
            'generated_at': now.isoformat(timespec='seconds'),
            'source': '清单.xlsx（多媒体会议系统报价清单）',
            'spaces': spaces, 'devices': devices,
            'readings': [r.as_dict() for r in readings],
            'alarms': alarms, 'tickets': world.make_tickets(alarms, now),
            'bookings': bookings,
        }
        Path(args.export).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        print('已导出 → %s' % args.export)
        for s in srvs:
            s.close()
        return

    Handler, routes = build_api(spaces, devices, bookings)
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print('API → http://127.0.0.1:%d  端点：%s'
          % (args.port, ' '.join(sorted(routes))))
    await sweep_loop(devices, drivers, ports, args.interval)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n已停止')
