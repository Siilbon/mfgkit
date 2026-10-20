"""Alarm and event logs stored in SQL databases.

DeltaV's Event Chronicle and iFIX's Alarm ODBC service write alarms and
operator actions to tables whose names and columns vary by version and site.
This package maps such a table once (:class:`EventLogMapping`), then fetches
rows as one normalized frame with an ``event_type`` of ``alarm``, ``return``,
``ack``, ``change``, ``mode``, ``config`` or ``other``.

- :mod:`eventlog.mapping`   normalized columns, mappings, ``suggest_mapping``
- :mod:`eventlog.rules`     event-type classification and vendor rule sets
- :mod:`eventlog.base`      the read-only ``EventLogConn`` reader
- :mod:`eventlog.discover`  find event tables in an unfamiliar database

Vendor readers: ``mfgkit.deltav.events.EventChronicle`` and
``mfgkit.intellution.alarms.IfixAlarmLog``. Analysis lives in
``mfgkit.analysis.alarms`` and ``mfgkit.analysis.interventions``.
"""

from __future__ import annotations

from .base import OUTPUT_COLUMNS, EventLogConn, build_query, normalize_events
from .discover import discover
from .mapping import EVENT_TYPES, NORMALIZED_COLUMNS, EventLogMapping, suggest_mapping
from .rules import DELTAV_RULES, IFIX_RULES, classify

__all__ = [
    "DELTAV_RULES",
    "EVENT_TYPES",
    "EventLogConn",
    "EventLogMapping",
    "IFIX_RULES",
    "NORMALIZED_COLUMNS",
    "OUTPUT_COLUMNS",
    "build_query",
    "classify",
    "discover",
    "normalize_events",
    "suggest_mapping",
]
