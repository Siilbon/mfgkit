"""What one OPC UA reading looks like once it leaves the server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from asyncua import ua

@dataclass
class Sample:
    tag: str
    node_id: str
    value: object
    status_code: int
    source_ts: datetime | None
    server_ts: datetime | None

    @property
    def quality(self) -> str:
        return quality(self.status_code)

def quality(status_code: int) -> str:
    '''The top two bits of a status code are its severity: 00 good, 01 uncertain,
    1x bad'''
    severity = (status_code >> 30) & 0b11
    return {0: "good", 1: "uncertain"}.get(severity, "bad")


def _utc(ts: datetime | None) -> datetime | None:
    '''Convert to UTC for time'''
    if ts is None:
        return None
    else:
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)

def to_sample(tag: str, node_id: str, dv: ua.DataValue) -> Sample:
    return Sample(
        tag=tag,
        node_id=node_id,
        value=dv.Value.Value if dv.Value is not None else None,
        status_code=dv.StatusCode.value if dv.StatusCode is not None else 0,
        source_ts=_utc(dv.SourceTimestamp),
        server_ts=_utc(dv.ServerTimestamp)
    )

def split_value(value) -> tuple[float | None, str | None]:
    '''Numbers and booleans as a float, anything else as text so one column type
    holds each.'''
    if value is None:
        return None, None
    if isinstance(value, (bool, int, float)):
        return float(value), None
    return None, str(value)
