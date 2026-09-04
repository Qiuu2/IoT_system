# -*- coding: utf-8 -*-
"""驱动一致性检查。

乙要写 13 个子系统的驱动，工作量大头在这里。让每个新驱动过同一套检查，
可以把「写驱动」从「读几百页文档后凭经验实现」变成「填一份声明再跑一次检查」。

    python3 gateway/conformance.py              # 检查全部驱动
    python3 gateway/conformance.py pnc218       # 只检查一个

检查项刻意保守：只查能自动查的结构与自洽性，不假装能验证厂商指令是否正确——
那件事只有对着真设备才能确认，所以 confidence 字段必须如实填写。
"""
import json
import sys
from pathlib import Path

DRIVERS = Path(__file__).resolve().parent / 'drivers'

TRANSPORTS = {'tcp', 'modbus-tcp', 'tcp-passthrough', 'serial',
              'bacnet', 'opc-ua', 'http', 'onvif', 'gb28182', 'vendor-sdk'}
CONFIDENCE = {'confirmed', 'guessed', 'partial', 'n/a'}
KINDS = {'read', 'write', 'readwrite'}
DATATYPES = {'number', 'bool', 'string', 'enum'}
PROBE_MODES = {'line', 'modbus', 'tcp', 'http', 'sdk'}


def check(d, name):
    errs, warns = [], []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    need(d.get('id') == name, 'id 字段应等于文件名 %r' % name)
    need(bool(d.get('label')), '缺少 label（人类可读名称）')
    need(bool(d.get('source')), '缺少 source（该驱动依据哪份材料写的）')

    t = d.get('transport') or {}
    need(t.get('type') in TRANSPORTS, 'transport.type 非法或缺失：%r' % t.get('type'))

    c = d.get('confidence') or {}
    need(bool(c), '缺少 confidence —— 必须显式区分「清单写明」与「我们猜的」')
    for k in ('transport', 'commands'):
        need(c.get(k) in CONFIDENCE, 'confidence.%s 非法或缺失：%r' % (k, c.get(k)))
    if not d.get('confirmed_facts'):
        warns.append('无 confirmed_facts，建议摘录依据原文，便于日后核对')

    probe = d.get('probe') or {}
    need(probe.get('mode') in PROBE_MODES, 'probe.mode 非法或缺失：%r' % probe.get('mode'))
    if probe.get('mode') == 'line':
        need(bool(probe.get('query')), 'probe.mode=line 必须给出 query')
        need(isinstance(probe.get('fields'), list),
             'probe.mode=line 必须声明 fields，否则采集器无从解析')
    if probe.get('mode') == 'modbus':
        need(bool(d.get('modbus')), 'probe.mode=modbus 必须提供 modbus 段')

    pts = d.get('points')
    need(isinstance(pts, list), '缺少 points 数组（可为空，但必须存在）')
    keys = set()
    for i, p in enumerate(pts or []):
        where = 'points[%d]' % i
        need(bool(p.get('key')), '%s 缺少 key' % where)
        need(p.get('key') not in keys, '%s key 重复：%r' % (where, p.get('key')))
        keys.add(p.get('key'))
        need(bool(p.get('name')), '%s 缺少 name' % where)
        need(p.get('kind') in KINDS, '%s kind 非法：%r' % (where, p.get('kind')))
        need(p.get('datatype') in DATATYPES, '%s datatype 非法：%r' % (where, p.get('datatype')))

    # 声明了可写点，就必须说明怎么写；否则运行期才会炸
    writable = [p for p in (pts or []) if p.get('kind') in ('write', 'readwrite')]
    if writable and probe.get('mode') not in ('modbus',) and not d.get('commands'):
        errs.append('声明了 %d 个可写点位，但没有 commands 段说明写法' % len(writable))

    # line 协议的 fields 若声明了 channel_bits，点位里应有对应的 chN
    if 'channel_bits' in (probe.get('fields') or []):
        need(any(k.startswith('ch') for k in keys),
             'probe.fields 含 channel_bits，但 points 里没有 chN 点位')

    if c.get('commands') == 'guessed' and writable:
        warns.append('指令集为 guessed 却已声明 %d 个可写点位——'
                     '拿到厂商文档前，不应在生产环境放开这些点位的下发'
                     % len(writable))
    return errs, warns


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    files = sorted(DRIVERS.glob('%s.json' % (only or '*')))
    if not files:
        print('没找到驱动：%s' % (only or '*'))
        return 1
    bad = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            print('✗ %-13s JSON 解析失败：%s' % (f.stem, e))
            bad += 1
            continue
        errs, warns = check(d, f.stem)
        w = sum(1 for p in d.get('points', []) if p.get('kind') != 'read')
        status = '✗' if errs else '✓'
        print('%s %-13s 点位 %2d（可写 %2d）  %s / %s'
              % (status, f.stem, len(d.get('points', [])), w,
                 d.get('confidence', {}).get('transport', '?'),
                 d.get('confidence', {}).get('commands', '?')))
        for e in errs:
            print('    错误  %s' % e)
        for x in warns:
            print('    提示  %s' % x)
        bad += bool(errs)
    print('\n%d 个驱动，%d 个不通过' % (len(files), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
