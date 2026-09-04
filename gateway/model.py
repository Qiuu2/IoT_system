# -*- coding: utf-8 -*-
"""契约数据结构（contracts/ingest-api.yaml 的 Python 落地）。

这里的字段名与契约严格一致。改动必须先改契约、再改这里，否则甲乙两侧会漂移。
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
import uuid


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def new_id(prefix):
    return '%s-%s' % (prefix, uuid.uuid4().hex[:16])


def _clean(d):
    """去掉 None，减小报文体积；空列表保留（语义是「确实为空」）。"""
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


@dataclass
class Point:
    key: str
    name: str
    kind: str = 'read'            # read | write | readwrite
    datatype: str = 'number'      # number | bool | string | enum
    unit: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None

    @property
    def writable(self):
        return self.kind in ('write', 'readwrite')


@dataclass
class Device:
    device_id: str
    name: str
    driver: str
    points: list
    model: Optional[str] = None
    vendor: Optional[str] = None
    subsystem: Optional[str] = None
    space_ref: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None
    confidence: Optional[dict] = None

    def point(self, key):
        return next((p for p in self.points if p.key == key), None)

    def to_json(self):
        d = asdict(self)
        d['points'] = [_clean(asdict(p)) for p in self.points]
        return _clean(d)


@dataclass
class Sample:
    key: str
    value: Any
    quality: str = 'good'         # good | stale | bad


@dataclass
class Reading:
    device_id: str
    online: bool
    ts: str = field(default_factory=now_iso)
    latency_ms: Optional[float] = None
    samples: list = field(default_factory=list)
    error: Optional[str] = None

    def to_json(self):
        d = asdict(self)
        d['samples'] = [_clean(asdict(s)) for s in self.samples]
        return _clean(d)


@dataclass
class Event:
    device_id: str
    type: str                     # device_online | device_offline | limit_exceeded | ...
    code: str
    severity: str = 'warning'     # info | warning | major | critical
    detail: Optional[str] = None
    point_key: Optional[str] = None
    value: Any = None
    event_id: str = field(default_factory=lambda: new_id('EV'))
    ts: str = field(default_factory=now_iso)

    def to_json(self):
        return _clean(asdict(self))


@dataclass
class Command:
    command_id: str
    device_id: str
    point_key: str
    value: Any
    issued_by: Optional[str] = None
    issued_at: Optional[str] = None
    expire_at: Optional[str] = None

    @staticmethod
    def from_json(d):
        return Command(**{k: d.get(k) for k in
                          ('command_id', 'device_id', 'point_key', 'value',
                           'issued_by', 'issued_at', 'expire_at')})

    def expired(self):
        if not self.expire_at:
            return False
        try:
            return datetime.fromisoformat(self.expire_at) < datetime.now(timezone.utc).astimezone()
        except ValueError:
            return False


@dataclass
class CommandResult:
    command_id: str
    status: str                   # succeeded | failed | rejected | expired
    detail: Optional[str] = None
    readback: Any = None
    ts: str = field(default_factory=now_iso)

    def to_json(self):
        return _clean(asdict(self))
