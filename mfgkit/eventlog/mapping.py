"""Map a vendor's event table onto mfgkit's normalized event columns.

Event Chronicle and iFIX alarm tables differ in table name, column names and
column meaning across versions and sites. Rather than guess at query time, a
site's layout is captured once in an :class:`EventLogMapping` (saved as JSON
and reviewed by a person), and every query is built from it.

Every table and column name in a mapping is checked against a strict pattern
and bracket-quoted, so a mapping file can't smuggle SQL into a query.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Columns every normalized event frame has, in order. Only ``time`` must be
# mapped; the rest are filled with NA when a table has no equivalent.
NORMALIZED_COLUMNS = (
    "time",
    "source_type",  # the vendor's own event type (DeltaV Event_Type, iFIX ALM_MSGTYPE)
    "category",
    "state",
    "area",
    "node",
    "unit",
    "module",
    "module_description",
    "parameter",
    "description",
    "description2",
    "value",
    "previous_value",
    "priority",
    "user",
)

# What classification assigns to each event (see :mod:`mfgkit.eventlog.rules`).
EVENT_TYPES = ("alarm", "return", "ack", "change", "mode", "config", "other")

# One identifier part: starts with a letter or underscore; may contain the
# spaces, slashes, hyphens and # that Access-era tables use ("Date/Time").
# No brackets, quotes, dots or semicolons, so bracket-quoting is always safe.
_IDENT_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_ #/\-]*$")


def quote_column(name: str) -> str:
    """Validate a column name and bracket-quote it for SQL Server / Access."""
    if not isinstance(name, str) or not _IDENT_PART.match(name):
        raise ValueError(f"Unsupported column name: {name!r}")
    return f"[{name}]"


def quote_table(name: str) -> str:
    """Validate a ``table``, ``schema.table`` or ``db.schema.table`` name and quote it."""
    if not isinstance(name, str):
        raise ValueError(f"Unsupported table name: {name!r}")
    parts = name.split(".")
    if not 1 <= len(parts) <= 3 or not all(_IDENT_PART.match(p) for p in parts):
        raise ValueError(f"Unsupported table name: {name!r}")
    return ".".join(f"[{p}]" for p in parts)


def _check_rule(rule) -> tuple:
    if not 2 <= len(rule) <= 3:
        raise ValueError(f"A rule is [event_type, pattern] or [event_type, pattern, column]; got {rule!r}")
    event_type, pattern, *rest = rule
    column = rest[0] if rest else None
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Rule event_type must be one of {EVENT_TYPES}; got {event_type!r}")
    if column is not None and column not in NORMALIZED_COLUMNS:
        raise ValueError(f"Rule column must be a normalized column; got {column!r}")
    re.compile(pattern)  # fail now on a bad regex, not mid-fetch
    return (event_type, pattern, column)


@dataclass
class EventLogMapping:
    """Which source column feeds each normalized column, for one table.

    Args:
        table: the source table, e.g. ``'dbo.Events'`` or ``'FIXALARMS'``.
        columns: normalized column -> source column. ``time`` is required.
        rules: optional classification rules that *replace* the reader's
            defaults; each is ``[event_type, regex]`` or
            ``[event_type, regex, normalized_column]``.
    """

    table: str
    columns: dict[str, str]
    rules: list[tuple] | None = field(default=None)

    def __post_init__(self):
        quote_table(self.table)
        if "time" not in self.columns:
            raise ValueError("A mapping must map the 'time' column.")
        for target, source in self.columns.items():
            if target not in NORMALIZED_COLUMNS:
                raise ValueError(
                    f"{target!r} is not a normalized column; use one of {NORMALIZED_COLUMNS}"
                )
            quote_column(source)
        if self.rules is not None:
            self.rules = [_check_rule(r) for r in self.rules]

    # ------------------------------------------------------------------ #
    # (De)serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        d = {"table": self.table, "columns": dict(self.columns)}
        if self.rules is not None:
            d["rules"] = [list(r[:2]) if r[2] is None else list(r) for r in self.rules]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "EventLogMapping":
        return cls(table=d["table"], columns=dict(d["columns"]), rules=d.get("rules"))

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "EventLogMapping":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# Candidate source names per normalized column, most specific first. Names are
# compared lowercased with everything but letters and digits removed, so
# "Date_Time", "Date/Time" and "DATETIME" all match "datetime".
_SYNONYMS = {
    "time": ["datetime", "almnativetimein", "nativetimein", "eventtime", "eventdatetime", "timestamp", "time"],
    "source_type": ["eventtype", "almmsgtype", "msgtype", "type"],
    "category": ["category", "eventcategory"],
    "state": ["state", "almalmstatus", "almstatus", "alarmstatus", "status"],
    "area": ["area", "plantarea", "almalmarea", "alarmarea"],
    "node": ["node", "nodename", "almlognodename", "almphyslnode", "station"],
    "unit": ["unit", "unitname", "processunit"],  # not ALM_UNIT: that's engineering units
    "module": ["module", "modulename", "almtagname", "tagname", "tag"],
    "module_description": ["moduledescription", "moduledesc", "almtagdesc", "tagdescription", "tagdesc"],
    "parameter": ["parameter", "attribute", "param"],
    "description": ["desc1", "description", "almdescr", "message", "eventdescription", "desc"],
    "description2": ["desc2", "description2"],
    "value": ["value", "almvalue", "newvalue"],
    "previous_value": ["oldvalue", "previousvalue", "prevvalue"],
    "priority": ["level", "priority", "almalmpriority", "almpriority", "severity"],
    "user": ["user", "username", "almopname", "opname", "operator", "almopfullname", "userid"],
}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def suggest_mapping(columns, table: str) -> EventLogMapping:
    """Guess a mapping from a table's column names. Always review the result.

    Raises ValueError when no column looks like the event time.
    """
    by_key: dict[str, str] = {}
    for col in columns:
        by_key.setdefault(_key(col), col)

    mapped: dict[str, str] = {}
    used: set[str] = set()
    for target, candidates in _SYNONYMS.items():
        for cand in candidates:
            source = by_key.get(cand)
            if source is not None and source not in used and _IDENT_PART.match(source):
                mapped[target] = source
                used.add(source)
                break

    if "time" not in mapped:
        raise ValueError(f"No time-like column found in {table!r}: {list(columns)}")
    return EventLogMapping(table=table, columns=mapped)
