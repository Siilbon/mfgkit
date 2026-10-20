"""Classify raw log rows into normalized event types with ordered regex rules.

A rule is ``(event_type, pattern, column)``. Rules are tried in order and the
first match wins; rows nothing matches are ``'other'``. When ``column`` is
None the pattern is searched in ``source_type | category | state |
description`` joined together, so anchored patterns (``^OK$``) should name a
column. Matching ignores case.

The vendor rule sets below are starting points built from typical layouts.
Sites differ, so check a day of classified events against what the operator
stations showed before trusting them, and put corrected rules in the mapping.
"""

from __future__ import annotations

import warnings

import pandas as pd

# DeltaV Event Chronicle. Alarm rows carry their state in State:
# ACT/UNACK when the alarm comes in, ACT/ACK once acknowledged while active,
# INACT/... when it clears.
DELTAV_RULES = (
    ("return", r"^INACT", "state"),
    ("ack", r"^(?:ACT/ACK|ACK)$", "state"),
    ("alarm", r"^ACT(?:/UNACK)?$", "state"),
    ("mode", r"\bMODE\b", "parameter"),
    ("config", r"DOWNLOAD|CONFIGURATION|INSTALL", None),
    ("change", r"CHANGE", "source_type"),
    ("alarm", r"ALARM", "source_type"),
)

# iFIX Alarm ODBC (FIXALARMS-style tables). ALM_MSGTYPE says what the row is;
# a return to normal is an ALARM row whose status is OK.
IFIX_RULES = (
    ("ack", r"^ACK", "source_type"),
    ("return", r"^OK$", "state"),
    ("alarm", r"^ALARM$", "source_type"),
    ("mode", r"\bMODE\b|\bMANL\b|\bAUTO\b", "description"),
    ("change", r"^OPERATOR$", "source_type"),
    ("config", r"DOWNLOAD|CONFIG", None),
)

_COMBINED = ("source_type", "category", "state", "description")


def _text(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series("", index=df.index)
    return df[column].astype("string").fillna("")


def classify(df: pd.DataFrame, rules) -> pd.Series:
    """Return an ``event_type`` Series for normalized events ``df``."""
    result = pd.Series(pd.NA, index=df.index, dtype="string")
    combined = None
    for rule in rules:
        event_type, pattern, *rest = rule
        column = rest[0] if rest else None
        if column is None:
            if combined is None:
                combined = _text(df, _COMBINED[0])
                for col in _COMBINED[1:]:
                    combined = combined + " | " + _text(df, col)
            text = combined
        else:
            text = _text(df, column)
        with warnings.catch_warnings():  # rules may use groups; only the match matters
            warnings.filterwarnings("ignore", "This pattern is interpreted", UserWarning)
            found = text.str.contains(pattern, case=False, regex=True)
        hit = result.isna() & found
        result[hit] = event_type
    return result.fillna("other")
