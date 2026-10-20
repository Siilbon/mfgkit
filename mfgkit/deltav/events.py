"""DeltaV Event Chronicle: alarms and operator changes from its SQL database.

    from mfgkit.eventlog import EventLogMapping
    from mfgkit.deltav.events import EventChronicle

    with EventChronicle(conn_str, 'event_chronicle_mapping.json') as ec:
        events = ec.fetch('2026-10-01', '2026-10-08')

    events = EventChronicle.read_export('chronicle_export.txt')   # a saved export

Alarm rows are classified by how each alarm's State changed, not by the State
text alone: alarms whose priority needs no acknowledgement come in as
``ACT/ACK``, so ``ACT/ACK`` is an ``ack`` only right after ``ACT/UNACK``.
See :func:`alarm_transitions`.

See :mod:`mfgkit.eventlog` for mappings and the normalized columns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from mfgkit.eventlog.base import EventLogConn
from mfgkit.eventlog.rules import DELTAV_RULES

_STATE = r"^(ACT|INACT)/(ACK|UNACK)$"


def alarm_transitions(events: pd.DataFrame) -> pd.DataFrame:
    """Re-classify alarm rows as ``alarm``, ``ack`` or ``return`` from state changes.

    Each alarm (module + parameter) is followed in time order:

    - becomes active (``INACT/*`` -> ``ACT/*``): ``alarm``
    - becomes inactive (``ACT/*`` -> ``INACT/*``): ``return``
    - same activity, ``UNACK`` -> ``ACK``: ``ack``
    - anything else, including an alarm's first row: ``alarm`` if active,
      else ``return``. So the first row of an alarm that came in before the
      time window counts as an alarm even if it was an acknowledgement.

    Only rows already typed ``alarm``, ``ack`` or ``return`` with a State like
    ``ACT/UNACK`` are changed. ``events`` must be sorted by time.
    """
    state = events["state"].astype("string").str.upper().str.extract(_STATE)
    is_alarm_row = state[0].notna() & events["event_type"].isin(["alarm", "ack", "return"])
    if not is_alarm_row.any():
        return events

    alarms = pd.DataFrame(
        {
            "module": events["module"],
            "parameter": events["parameter"],
            "active": (state[0] == "ACT").fillna(False).astype(bool),
            "acked": (state[1] == "ACK").fillna(False).astype(bool),
        }
    ).loc[is_alarm_row]
    active, acked = alarms["active"], alarms["acked"]

    # Each alarm's previous row; its first row is compared with itself ("no change").
    by_alarm = alarms.groupby(["module", "parameter"], dropna=False, sort=False)
    was_active = by_alarm["active"].shift().fillna(active).astype(bool)
    was_acked = by_alarm["acked"].shift().fillna(acked).astype(bool)

    came_in = active & ~was_active
    cleared = ~active & was_active
    acked_now = acked & ~was_acked
    unchanged = np.where(active, "alarm", "return")
    # First match wins, so an alarm that clears and is acknowledged at once is a return.
    new_type = np.select([came_in, cleared, acked_now], ["alarm", "return", "ack"], default=unchanged)

    out = events.copy()
    out.loc[is_alarm_row, "event_type"] = new_type
    return out


class EventChronicle(EventLogConn):
    """Event Chronicle reader; classifies with :data:`DELTAV_RULES`, then
    :func:`alarm_transitions`."""

    source = "deltav"
    default_rules = DELTAV_RULES
    after_classify = staticmethod(alarm_transitions)


__all__ = ["EventChronicle", "DELTAV_RULES", "alarm_transitions"]
