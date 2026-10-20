"""DeltaV Event Chronicle: alarms and operator changes from its SQL database.

    from mfgkit.eventlog import EventLogMapping
    from mfgkit.deltav.events import EventChronicle

    with EventChronicle(conn_str, 'event_chronicle_mapping.json') as ec:
        events = ec.fetch('2026-10-01', '2026-10-08')

See :mod:`mfgkit.eventlog` for mappings and the normalized columns.
"""

from __future__ import annotations

from mfgkit.eventlog.base import EventLogConn
from mfgkit.eventlog.rules import DELTAV_RULES


class EventChronicle(EventLogConn):
    """Event Chronicle reader; classifies with :data:`DELTAV_RULES` by default."""

    source = "deltav"
    default_rules = DELTAV_RULES


__all__ = ["EventChronicle", "DELTAV_RULES"]
