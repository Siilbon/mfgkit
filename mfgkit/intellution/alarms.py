"""iFIX alarm and message history written by the Alarm ODBC service.

    from mfgkit.intellution.alarms import IfixAlarmLog

    with IfixAlarmLog(conn_str, 'ifix_alarm_mapping.json') as log:
        events = log.fetch('2026-10-01', '2026-10-08')

See :mod:`mfgkit.eventlog` for mappings and the normalized columns.
"""

from __future__ import annotations

from mfgkit.eventlog.base import EventLogConn
from mfgkit.eventlog.rules import IFIX_RULES


class IfixAlarmLog(EventLogConn):
    """iFIX Alarm ODBC reader; classifies with :data:`IFIX_RULES` by default."""

    source = "ifix"
    default_rules = IFIX_RULES


__all__ = ["IfixAlarmLog", "IFIX_RULES"]
