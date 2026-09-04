# -*- coding: utf-8 -*-
"""把导出的数据集裁剪成 UI 原型要用的负载（去掉原型用不到的字段）。

    python3 sim/ui_payload.py dataset.json payload.json
"""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta

DEV_KEYS = ('id', 'name', 'model', 'spec_name', 'driver', 'space_id', 'group',
            'ip', 'port', 'sn', 'subordinate')
READ_KEYS = ('device_id', 'online', 'latency_ms', 'metrics', 'channels', 'error')
ALARM_KEYS = ('id', 'device_id', 'device_name', 'space_id', 'model', 'code',
              'name', 'level', 'detail', 'first_raised', 'last_seen',
              'occurrences', 'state', 'recovered')
BOOK_KEYS = ('id', 'space_id', 'space_name', 'topic', 'dept', 'owner',
             'attendees', 'start', 'end', 'state', 'scene')


def pick(d, keys):
    return {k: d[k] for k in keys if k in d}


def main(src, dst, window_days=7):
    d = json.load(open(src, encoding='utf-8'))
    now = datetime.fromisoformat(d['generated_at'])
    lo = (now - timedelta(days=window_days)).date().isoformat()
    hi = (now + timedelta(days=window_days)).date().isoformat()

    reads = {r['device_id']: r for r in d['readings']}
    devices = []
    for x in d['devices']:
        if x['subordinate']:
            continue
        r = reads.get(x['id'], {})
        devices.append(dict(pick(x, DEV_KEYS), **pick(r, READ_KEYS)))

    bookings = [pick(b, BOOK_KEYS) for b in d['bookings']
                if lo <= b['start'][:10] <= hi]

    out = {
        'generated_at': d['generated_at'],
        'source': d['source'],
        'spaces': d['spaces'],
        'devices': devices,
        'alarms': [pick(a, ALARM_KEYS) for a in d['alarms']],
        'tickets': d['tickets'],
        'bookings': bookings,
        'driver_counts': dict(Counter(x['driver'] for x in devices)),
    }
    json.dump(out, open(dst, 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    print('设备 %d · 告警 %d · 工单 %d · 预约 %d（%s ~ %s）· %.0f KB'
          % (len(devices), len(out['alarms']), len(out['tickets']),
             len(bookings), lo, hi,
             len(json.dumps(out, ensure_ascii=False)) / 1024))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
