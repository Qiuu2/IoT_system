# -*- coding: utf-8 -*-
"""上行推送：注册、遥测、事件、心跳。

所有上行数据先落盘队列再发送。发送失败不丢数据，链路恢复后按原顺序补传。
"""
import json
import urllib.error
import urllib.request

from buffer import DiskQueue
from model import new_id, now_iso


class Publisher:
    def __init__(self, base_url, gateway_id, token='', spool='var/spool',
                 timeout=8, batch_max=200):
        self.base = base_url.rstrip('/')
        self.gateway_id = gateway_id
        self.token = token
        self.timeout = timeout
        self.batch_max = batch_max
        self.q_tel = DiskQueue(spool, 'telemetry')
        self.q_evt = DiskQueue(spool, 'events')
        self.online = None                 # 到平台的链路状态，None = 尚未尝试

    # ---------- HTTP ----------
    def _post(self, path, payload):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + self.token},
            method='POST')
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = r.read().decode('utf-8') or '{}'
            return r.status, json.loads(body)

    def _get(self, path):
        req = urllib.request.Request(
            self.base + path,
            headers={'Authorization': 'Bearer ' + self.token})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8') or '{}')

    # ---------- 入队 ----------
    def queue_telemetry(self, readings):
        for i in range(0, len(readings), self.batch_max):
            chunk = readings[i:i + self.batch_max]
            self.q_tel.put({'gateway_id': self.gateway_id,
                            'batch_id': new_id('TB'),
                            'readings': [r.to_json() for r in chunk]})

    def queue_events(self, events):
        if events:
            self.q_evt.put({'gateway_id': self.gateway_id,
                            'batch_id': new_id('EB'),
                            'events': [e.to_json() for e in events]})

    # ---------- 出队 ----------
    def flush(self, max_batches=20):
        """把队列里的批次发出去。任一批次失败即停止，保持顺序。"""
        sent = 0
        for path, queue in (('/telemetry', self.q_tel), ('/events', self.q_evt)):
            for f, payload in queue.peek(max_batches):
                try:
                    status, _ = self._post(path, payload)
                except urllib.error.HTTPError as e:
                    if e.code == 409:            # 幂等命中：平台已收过，视为成功
                        queue.ack(f)
                        sent += 1
                        continue
                    self.online = False
                    return sent
                except (urllib.error.URLError, OSError, TimeoutError):
                    self.online = False
                    return sent
                if status in (200, 202):
                    queue.ack(f)
                    sent += 1
                else:
                    self.online = False
                    return sent
        self.online = True
        return sent

    def depth(self):
        return self.q_tel.depth() + self.q_evt.depth()

    # ---------- 其余接口 ----------
    def sync_registry(self, devices):
        return self._post('/registry/sync', {
            'gateway_id': self.gateway_id,
            'devices': [d.to_json() for d in devices]})

    def heartbeat(self, total, online_count, last_sweep_ms, version='0.1.0'):
        return self._post('/heartbeat', {
            'gateway_id': self.gateway_id, 'ts': now_iso(), 'version': version,
            'devices_total': total, 'devices_online': online_count,
            'queue_depth': self.depth(), 'last_sweep_ms': last_sweep_ms})

    def pull_commands(self, max_n=20):
        _, body = self._get('/commands/pending?gateway_id=%s&max=%d'
                            % (self.gateway_id, max_n))
        return body.get('commands', [])

    def report_result(self, result):
        return self._post('/commands/%s/result' % result.command_id, result.to_json())
