# -*- coding: utf-8 -*-
"""落盘的待发队列。

网关到平台的链路一定会断——网闸重启、平台升级、交换机割接。断网期间的数据
必须留在本地，恢复后按原始时间戳补传（契约里明确要求平台不得以时间戳过旧丢弃）。

实现取向：一个批次一个文件，而不是单一大文件。理由是崩溃安全——
写到一半的文件不会污染已经写好的批次，重启后跳过损坏文件即可继续。
"""
import itertools
import json
import os
import time
from pathlib import Path


class DiskQueue:
    def __init__(self, root, name, max_files=20000):
        self.dir = Path(root) / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        # 毫秒时间戳不足以区分同一毫秒内的多次入队，必须再加单调序号，
        # 否则后写的批次会直接覆盖前一个（实测三次入队只剩一个文件）。
        self._seq = itertools.count(self._max_seq_on_disk() + 1)

    def _max_seq_on_disk(self):
        """重启后从磁盘上已有的最大序号续起，保证顺序不回退。"""
        best = 0
        for p in self.dir.glob('*.json'):
            parts = p.stem.split('-')
            if len(parts) == 3 and parts[2].isdigit():
                best = max(best, int(parts[2]))
        return best

    def put(self, payload):
        """先写临时文件再原子改名，避免消费者读到写了一半的批次。"""
        self._evict()
        stamp = '%013d-%06d-%09d' % (time.time() * 1000,
                                     os.getpid() % 1000000, next(self._seq))
        tmp = self.dir / (stamp + '.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        tmp.rename(self.dir / (stamp + '.json'))

    def peek(self, limit=1):
        """按文件名排序 = 按入队时间排序，保证补传顺序与采集顺序一致。"""
        out = []
        for p in sorted(self.dir.glob('*.json'))[:limit]:
            try:
                out.append((p, json.loads(p.read_text(encoding='utf-8'))))
            except (json.JSONDecodeError, OSError):
                p.unlink(missing_ok=True)        # 损坏文件直接丢，不能卡住队列
        return out

    def ack(self, path):
        Path(path).unlink(missing_ok=True)

    def depth(self):
        return sum(1 for _ in self.dir.glob('*.json'))

    def _evict(self):
        """队列超限时丢最旧的。

        丢新的会让平台永远看不到最近状态；丢旧的至少保证「近况是准的」。
        运维层面 queue_depth 会通过心跳上报，持续增长就该报警了。
        """
        files = sorted(self.dir.glob('*.json'))
        for p in files[:max(0, len(files) - self.max_files + 1)]:
            p.unlink(missing_ok=True)
