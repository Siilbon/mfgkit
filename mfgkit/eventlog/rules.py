"""Classify raw log rows into normalized event types with ordered regex rules.

A :class:`Rule` gives an event type and the patterns a row must match, one per
column: ``Rule("return", {"source_type": "^ALARM$", "state": "^OK$"})``. Rules
are tried in order and the first match wins; rows nothing matches are
``'other'``. Matching ignores case.

The column :data:`ANY_TEXT` (``"text"``) searches ``source_type | category |
state | description`` joined together, for patterns that can appear anywhere.

Rules can also be written as lists, which is how mapping JSON stores them:
``[type, pattern]`` (searches :data:`ANY_TEXT`), ``[type, pattern, column]`` or
``[type, {column: pattern, ...}]``.

The vendor rule sets below are starting points built from typical layouts.
Sites differ, so check a day of classified events against what the operator
stations showed before trusting them, and put corrected rules in the mapping.
"""

from __future__ import annotations

import re
import warnings
from typing import NamedTuple

import pandas as pd

# What classification assigns to each event.
EVENT_TYPES = ("alarm", "return", "ack", "change", "mode", "config", "other")

# Pseudo-column: the row's main text fields joined together.
ANY_TEXT = "text"
_ANY_TEXT_COLUMNS = ("source_type", "category", "state", "description")


class Rule(NamedTuple):
    """Rows matching every ``{column: pattern}`` in ``when`` get ``event_type``."""

    event_type: str
    when: dict[str, str]


# DeltaV Event Chronicle. State alone can't tell an acknowledgement
# (ACT/UNACK -> ACT/ACK) from an alarm that needs no acknowledgement coming in
# as ACT/ACK, so every ACT/* row is an 'alarm' here. EventChronicle then works
# out alarm / ack / return from each alarm's State history
# (mfgkit.deltav.events.alarm_transitions).
DELTAV_RULES = (
    Rule("return", {"state": r"^INACT"}),
    Rule("alarm", {"state": r"^ACT\b"}),
    Rule("ack", {"state": r"^ACK$"}),
    Rule("mode", {"parameter": r"\bMODE\b"}),
    Rule("config", {ANY_TEXT: r"DOWNLOAD|CONFIGURATION|INSTALL"}),
    Rule("change", {"source_type": r"CHANGE"}),
    Rule("alarm", {"source_type": r"ALARM"}),
)

# iFIX Alarm ODBC. The message type (ALM_MSGTYPE / MESSAGETYPE) says what the
# row is; a return to normal is an ALARM row whose status is OK. Operator
# writes are OPERATOR rows reading "Fix32.NODE.TAG.F_CV set to 1 by ...".
# Other OPERATOR rows (log-ins) and EVENT rows (tag value changes, including
# the echo of each operator write and sequence steps) fall through to 'other'.
IFIX_RULES = (
    Rule("ack", {"source_type": r"^ACK"}),
    Rule("return", {"source_type": r"^ALARM$", "state": r"^OK$"}),
    Rule("alarm", {"source_type": r"^ALARM$"}),
    Rule("mode", {"source_type": r"^OPERATOR$", "description": r"\bset to\s+(?:MANL?|AUTO|CASC?)\b"}),
    Rule("change", {"source_type": r"^OPERATOR$", "description": r"\bset to\b"}),
    Rule("config", {ANY_TEXT: r"DOWNLOAD|CONFIG"}),
)


def as_rule(rule) -> Rule:
    """Turn a :class:`Rule` or one of its list forms into a checked :class:`Rule`.

    Checks the event type and that every pattern compiles; column names are
    checked by :class:`~mfgkit.eventlog.mapping.EventLogMapping`.
    """
    if isinstance(rule, Rule):
        event_type, when = rule
    elif isinstance(rule, (list, tuple)) and len(rule) == 2 and isinstance(rule[1], dict):
        event_type, when = rule
    elif isinstance(rule, (list, tuple)) and len(rule) in (2, 3):
        event_type, pattern, *column = rule
        when = {(column[0] if column and column[0] is not None else ANY_TEXT): pattern}
    else:
        raise ValueError(
            "A rule is [type, pattern], [type, pattern, column] or "
            f"[type, {{column: pattern, ...}}]; got {rule!r}"
        )
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Rule event_type must be one of {EVENT_TYPES}; got {event_type!r}")
    if not when:
        raise ValueError(f"Rule {rule!r} has no patterns.")
    for pattern in when.values():
        if not isinstance(pattern, str):
            raise ValueError(f"Rule pattern must be a string; got {pattern!r}")
        re.compile(pattern)  # fail now on a bad regex, not mid-fetch
    return Rule(event_type, dict(when))


def rule_to_list(rule: Rule) -> list:
    """The shortest list form of ``rule``, for JSON."""
    if len(rule.when) == 1:
        (column, pattern), = rule.when.items()
        return [rule.event_type, pattern] if column == ANY_TEXT else [rule.event_type, pattern, column]
    return [rule.event_type, dict(rule.when)]


def _text(df: pd.DataFrame, column: str) -> pd.Series:
    """A column as text with missing values as "", or the joined :data:`ANY_TEXT`."""
    if column == ANY_TEXT:
        first, *rest = (_text(df, c) for c in _ANY_TEXT_COLUMNS)
        for part in rest:
            first = first + " | " + part
        return first
    if column not in df.columns:
        return pd.Series("", index=df.index, dtype="string")
    return df[column].astype("string").fillna("")


def classify(df: pd.DataFrame, rules) -> pd.Series:
    """Return an ``event_type`` Series for normalized events ``df``."""
    texts: dict[str, pd.Series] = {}  # each column's text, built once

    def matches(column: str, pattern: str) -> pd.Series:
        if column not in texts:
            texts[column] = _text(df, column)
        with warnings.catch_warnings():  # patterns may use groups; only the match matters
            warnings.filterwarnings("ignore", "This pattern is interpreted", UserWarning)
            return texts[column].str.contains(pattern, case=False, regex=True)

    result = pd.Series(pd.NA, index=df.index, dtype="string")
    for rule in map(as_rule, rules):
        found = pd.Series(True, index=df.index)
        for column, pattern in rule.when.items():
            found &= matches(column, pattern)
        result[result.isna() & found] = rule.event_type
    return result.fillna("other")
