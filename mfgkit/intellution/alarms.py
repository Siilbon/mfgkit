"""iFIX alarm and message history written by the Alarm ODBC service.

    from mfgkit.intellution.alarms import IfixAlarmLog

    with IfixAlarmLog(conn_str, 'ifix_alarm_mapping.json') as log:
        events = log.fetch('2026-10-01', '2026-10-08')

Operator writes arrive as OPERATOR rows with no tag name, only a message like
``[NODE1 ] Fix32.NODE1.FIC101.F_CV set to 50 by STATION::OPERATOR NAME``.
:func:`parse_operator_messages` fills ``module``, ``parameter``, ``value`` and
``node`` from that text so the writes can be grouped by tag.

See :mod:`mfgkit.eventlog` for mappings and the normalized columns.
"""

from __future__ import annotations

import pandas as pd

from mfgkit.eventlog.base import EventLogConn
from mfgkit.eventlog.rules import IFIX_RULES

# SCADA.NODE.TAG.FIELD set to VALUE by STATION::USER (the "by" part is optional)
_WRITE = (
    r"\b\w+\.(?P<node>\w+)\.(?P<module>[^.\s]+)\.(?P<parameter>[A-Z]_\w+)"
    r"\s+set to\s+(?P<value>.*?)(?:\s+by\s+\S+::.*)?$"
)


def parse_operator_messages(events: pd.DataFrame) -> pd.DataFrame:
    """Fill ``module``, ``parameter``, ``value`` and ``node`` from operator write messages.

    Only blank cells are filled; columns the table already supplies are kept.
    """
    if "description" not in events.columns:
        return events
    parsed = events["description"].astype("string").str.extract(_WRITE)
    found = parsed["module"].notna()
    if not found.any():
        return events
    df = events.copy()
    for col in ("node", "module", "parameter", "value"):
        if col not in df.columns:
            df[col] = pd.NA
        fill = found & df[col].isna()
        df[col] = df[col].astype(object)
        df.loc[fill, col] = parsed.loc[fill, col].astype(object)
    return df


class IfixAlarmLog(EventLogConn):
    """iFIX Alarm ODBC reader; classifies with :data:`IFIX_RULES` by default."""

    source = "ifix"
    default_rules = IFIX_RULES

    before_classify = staticmethod(parse_operator_messages)


__all__ = ["IfixAlarmLog", "IFIX_RULES", "parse_operator_messages"]
