# -*- coding: utf-8 -*-
"""业务侧数据：预约、告警、工单。

预约按会议室真实作息生成（工作日 8:30-20:00，周末稀疏），刻意包含
跨天会议、背靠背会议和已取消的记录——这些是 UI 设计时最容易漏掉的形态。
"""
import random
from datetime import datetime, timedelta

TOPICS = [
    '晨间交班', '医务科例会', '院感质控专题', '护理部业务学习', '多学科会诊 MDT',
    '设备科招标评审', '病案质量点评', '临床路径讨论', '科研课题开题',
    '住培师资培训', '信息化项目周会', '药事管理委员会', '继续教育讲座',
    '三甲复审推进会', '急诊绿色通道演练', '影像读片会',
]
DEPTS = ['医务科', '护理部', '院感科', '设备科', '信息科', '药学部', '科教科',
         '心内科', '神经外科', '影像科', '急诊科', '检验科']
OWNERS = ['张医生', '李主任', '王护士长', '陈工', '刘老师', '赵医生',
          '孙主任', '周工', '吴老师', '郑医生']


def make_bookings(rooms, now, days_back=21, days_fwd=10, seed=42):
    rnd = random.Random(seed)
    out = []
    start_day = now.date() - timedelta(days=days_back)
    total_days = days_back + days_fwd

    for d in range(total_days):
        day = start_day + timedelta(days=d)
        weekend = day.weekday() >= 5
        for room in rooms:
            # 大会议室更满，小会议室更闲；周末整体稀疏
            cap = 1.0 if (room.get('area') or 100) > 150 else 0.65
            n = rnd.choices([0, 1, 2, 3], [0.55, 0.3, 0.12, 0.03])[0] if weekend \
                else rnd.choices([0, 1, 2, 3, 4], [0.18, 0.28, 0.28, 0.18, 0.08])[0]
            n = int(round(n * cap))
            slot = 8.5
            for _ in range(n):
                if slot > 19:
                    break
                gap = rnd.choice([0, 0, 0.25, 0.5, 1.0])   # 0 = 背靠背
                slot += gap
                dur = rnd.choice([0.5, 1.0, 1.0, 1.5, 2.0, 3.0])
                begin = datetime.combine(day, datetime.min.time()) + timedelta(hours=slot)
                end = begin + timedelta(hours=dur)
                slot += dur

                if rnd.random() < 0.12:
                    state = '已取消'
                elif end < now:
                    state = '已结束'
                elif begin <= now <= end:
                    state = '进行中'
                else:
                    state = '待开始'

                out.append({
                    'id': 'BK-%05d' % (len(out) + 1),
                    'space_id': room['id'], 'space_name': room['name'],
                    'topic': rnd.choice(TOPICS), 'dept': rnd.choice(DEPTS),
                    'owner': rnd.choice(OWNERS),
                    'attendees': rnd.randint(4, min(40, int((room.get('area') or 80) / 4))),
                    'start': begin.isoformat(timespec='minutes'),
                    'end': end.isoformat(timespec='minutes'),
                    'state': state,
                    'scene': rnd.choice(['标准会议', '投影汇报', '视频会议', '录播存档']),
                })

    # 一场跨天的封闭式培训，用来暴露「跨天会议怎么在日程条上画」这个问题
    if rooms:
        big = max(rooms, key=lambda r: r.get('area') or 0)
        b = datetime.combine(now.date() + timedelta(days=3), datetime.min.time()) \
            + timedelta(hours=14)
        out.append({
            'id': 'BK-%05d' % (len(out) + 1), 'space_id': big['id'],
            'space_name': big['name'], 'topic': '三甲复审封闭集训（跨天）',
            'dept': '医务科', 'owner': '李主任', 'attendees': 60,
            'start': b.isoformat(timespec='minutes'),
            'end': (b + timedelta(hours=22)).isoformat(timespec='minutes'),
            'state': '待开始', 'scene': '投影汇报',
        })
    return out


# --------------------------------------------------------------------------
ALARM_RULES = [
    {'code': 'DEV_OFFLINE', 'name': '设备离线', 'level': '严重'},
    {'code': 'OVER_VOLTAGE', 'name': '输入过压', 'level': '严重',
     'hint': '电压高于 245V，时序电源面板应已闪亮警示'},
    {'code': 'UNDER_VOLTAGE', 'name': '输入欠压', 'level': '重要',
     'hint': '电压低于 195V'},
    {'code': 'OVER_CURRENT', 'name': '整机电流接近额定', 'level': '重要',
     'hint': '整机额定 60A'},
]


def merge_alarms(store, readings, devices, now, seed=7):
    """把一轮采集读数并入告警库。

    按 (设备, 告警码) 聚合：同一台设备的同一种故障始终是一条告警，
    只累加 occurrences 与 last_seen。不聚合的话，一台离线设备每轮询一次
    就刷一条，10 秒一轮的话一小时 360 条——告警中心会立刻失去可读性。
    故障恢复后自动置为『已恢复』，而不是留一条永远待处理的僵尸告警。
    """
    rnd = random.Random(seed)
    by_id = {d['id']: d for d in devices}
    seen_now = set()

    def hit(dev, rule, detail):
        key = (dev['id'], rule['code'])
        seen_now.add(key)
        a = store.get(key)
        if a is None:
            a = store[key] = {
                'id': 'AL-%05d' % (len(store) + 1),
                'device_id': dev['id'], 'device_name': dev['name'],
                'space_id': dev['space_id'], 'model': dev['model'],
                'code': rule['code'], 'name': rule['name'], 'level': rule['level'],
                'hint': rule.get('hint'),
                'first_raised': now.isoformat(timespec='seconds'),
                'occurrences': 0,
                'state': rnd.choices(['待处理', '已处理', '误报警'],
                                     [0.55, 0.35, 0.10])[0],
            }
        a['occurrences'] += 1
        a['detail'] = detail
        a['last_seen'] = now.isoformat(timespec='seconds')
        a['recovered'] = False

    for r in readings:
        dev = by_id.get(r.device_id)
        if not dev or r.error == 'subordinate':
            continue
        if not r.online:
            hit(dev, ALARM_RULES[0], '探测失败：%s' % (r.error or 'unknown'))
            continue
        v = r.metrics.get('voltage')
        if v is not None and v > 245:
            hit(dev, ALARM_RULES[1], '实测 %.1f V' % v)
        elif v is not None and v < 195:
            hit(dev, ALARM_RULES[2], '实测 %.1f V' % v)
        c = r.metrics.get('current')
        if c is not None and c > 48:
            hit(dev, ALARM_RULES[3], '实测 %.2f A' % c)

    for key, a in store.items():          # 本轮没再命中的 = 故障已恢复
        if key not in seen_now and not a.get('recovered'):
            a['recovered'] = True
            a['recovered_at'] = now.isoformat(timespec='seconds')
    return store


def backfill_history(store, now, days=14, seed=13):
    """把当前在告的故障回溯成过去若干天的发生记录。

    这些设备昨天同样是坏的，所以把 first_raised 前移、occurrences 补足，
    比凭空造一批不存在的告警更可信。仅调整时间与计数，不新增故障种类。
    """
    rnd = random.Random(seed)
    for a in store.values():
        back = rnd.randint(1, days)
        a['first_raised'] = (now - timedelta(days=back,
                                             minutes=rnd.randint(0, 1439))
                             ).isoformat(timespec='seconds')
        a['occurrences'] += back * rnd.randint(30, 90)
    return store


def derive_alarms(readings, devices, now, seed=7):
    """单轮采集的告警快照（不聚合），供实时接口使用。"""
    return list(merge_alarms({}, readings, devices, now, seed).values())


def make_tickets(alarms, now, seed=11):
    """从在告的故障派生工单，形成 告警 → 工单 → 巡检 的闭环。

    派单策略与清单第 187-190 条一致：严重告警必派单，重要告警按概率派。
    """
    rnd = random.Random(seed)
    out = []
    for a in sorted(alarms, key=lambda x: x['id']):
        if a['state'] != '待处理' or a.get('recovered'):
            continue
        if a['level'] != '严重' and rnd.random() > 0.5:
            continue
        created = datetime.fromisoformat(a['first_raised'])
        out.append({
            'id': 'WO-%05d' % (len(out) + 1),
            'title': '%s · %s' % (a['device_name'], a['name']),
            'alarm_id': a['id'], 'device_id': a['device_id'],
            'space_id': a['space_id'],
            'priority': {'严重': '高', '重要': '中'}.get(a['level'], '低'),
            'assignee': rnd.choice(['运维-陈', '运维-林', '运维-黄', '厂商-EMeta']),
            'state': rnd.choices(['待接单', '处理中', '已完成'], [0.3, 0.4, 0.3])[0],
            'created_at': created.isoformat(timespec='seconds'),
            'sla_hours': {'严重': 4, '重要': 24}.get(a['level'], 72),
        })
    return out
