# -*- coding: utf-8 -*-
"""平台侧的 mock 实现，严格按 contracts/ingest-api.yaml。

用途：让乙在甲还没写完平台时就能验证整条上行/下行链路。
甲那边写好真平台后，把 --platform 指到真地址即可，网关一行不改。

    python3 gateway/mock_platform.py --port 8090
    curl -s localhost:8090/api/v1/_debug/state | python3 -m json.tool
"""
import argparse
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

LOCK = threading.Lock()
STATE = {
    'devices': {}, 'points_total': 0,
    'readings': 0, 'events': [], 'heartbeats': [],
    'seen_batches': set(),          # 幂等去重
    'commands': {},                 # command_id -> {cmd, status, result}
    'pending': [],
}


def _iso(dt):
    return dt.astimezone().isoformat(timespec='seconds')


def enqueue_command(device_id, point_key, value, ttl_s=120, by='mock-operator'):
    cid = 'CMD-%04d' % (len(STATE['commands']) + 1)
    cmd = {'command_id': cid, 'device_id': device_id, 'point_key': point_key,
           'value': value, 'issued_by': by,
           'issued_at': _iso(datetime.now(timezone.utc)),
           'expire_at': _iso(datetime.now(timezone.utc) + timedelta(seconds=ttl_s))}
    with LOCK:
        STATE['commands'][cid] = {'cmd': cmd, 'status': 'pending'}
        STATE['pending'].append(cmd)
    return cmd


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = json.dumps(obj or {}, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(n) or b'{}')

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/api/v1/commands/pending':
            with LOCK:
                take, STATE['pending'] = STATE['pending'], []
            return self._send(200, {'commands': take})
        if u.path == '/api/v1/_debug/state':
            with LOCK:
                return self._send(200, {
                    'devices': len(STATE['devices']),
                    'points_total': STATE['points_total'],
                    'readings_received': STATE['readings'],
                    'events_received': len(STATE['events']),
                    'last_events': STATE['events'][-5:],
                    'heartbeats': len(STATE['heartbeats']),
                    'last_heartbeat': STATE['heartbeats'][-1] if STATE['heartbeats'] else None,
                    'commands': {k: {'status': v['status'],
                                     'detail': v.get('result', {}).get('detail'),
                                     'readback': v.get('result', {}).get('readback')}
                                 for k, v in STATE['commands'].items()},
                })
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self.send_error(400, 'bad json')

        if p == '/api/v1/registry/sync':
            devs = body.get('devices', [])
            with LOCK:
                STATE['devices'] = {d['device_id']: d for d in devs}
                STATE['points_total'] = sum(len(d.get('points', [])) for d in devs)
            return self._send(200, {'accepted_devices': len(devs),
                                    'accepted_points': STATE['points_total'],
                                    'removed_devices': 0})

        if p in ('/api/v1/telemetry', '/api/v1/events'):
            bid = body.get('batch_id')
            with LOCK:
                if bid in STATE['seen_batches']:
                    return self._send(409, {'detail': 'duplicate batch_id'})
                STATE['seen_batches'].add(bid)
                if p.endswith('telemetry'):
                    STATE['readings'] += len(body.get('readings', []))
                else:
                    STATE['events'] += body.get('events', [])
            return self._send(202)

        if p == '/api/v1/_debug/command':
            # 测试入口：模拟平台侧操作员下发控制，供乙在甲写好之前自测下行链路
            cmd = enqueue_command(body['device_id'], body['point_key'],
                                  body['value'], body.get('ttl_s', 120))
            return self._send(200, cmd)

        if p == '/api/v1/heartbeat':
            with LOCK:
                STATE['heartbeats'].append(body)
            return self._send(200, {'server_time': _iso(datetime.now(timezone.utc)),
                                    'poll_interval_s': 5})

        if p.startswith('/api/v1/commands/') and p.endswith('/result'):
            cid = p.split('/')[-2]
            with LOCK:
                rec = STATE['commands'].get(cid)
                if rec:
                    rec['status'] = body.get('status')
                    rec['result'] = body
            return self._send(200)

        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8090)
    a = ap.parse_args()
    print('mock 平台 → http://127.0.0.1:%d/api/v1' % a.port, flush=True)
    ThreadingHTTPServer(('127.0.0.1', a.port), H).serve_forever()


if __name__ == '__main__':
    main()
