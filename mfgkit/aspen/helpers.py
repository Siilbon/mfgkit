"""Constants, the optional pyodbc driver handle, and pure helpers.

Everything here is importable without pyodbc or a live IP21 server, so it can
be unit-tested in isolation. See ``test_aspen.py``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Iterable, Sequence

import pandas as pd

if TYPE_CHECKING:
    import pyodbc
else:  # pyodbc is optional at runtime (absent in test/CI environments)
    try:
        import pyodbc
    except ImportError:  # pragma: no cover - only where pyodbc is absent
        pyodbc = None

# pyodbc raises pyodbc.Error; fall back to a broad base when the driver is absent
DBError: type[Exception] = pyodbc.Error if pyodbc is not None else Exception

# IP21 expresses durations/periods in tenths of a second
TENTHS_PER_SECOND = 10

# Timestamp literal format SQLplus reliably accepts (for literals we send).
# The driver rejects "%d-%b-%y" here with "Invalid timestamp ... check format
# is YYYY-MM-DD HH:MM:SS" even though it returns that format in result rows.
IP21_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# Format of TS strings IP21 returns: %d-%b-%y, plus a tenth-of-second fraction
# (IP21's native resolution, so the fractional digit is always present).
# strptime's %f accepts 1-6 digits, so ".1" parses fine.
IP21_TS_PARSE_FORMAT = "%d-%b-%y %H:%M:%S.%f"

# Permissive but safe charset for tag names / search patterns
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_\-\.\:/%\* ]+$")

# Bare SQL identifier (column/table name) - no quotes, operators, or whitespace
_FIELD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Quality strings IP21 reports for a healthy reading (compared case-insensitively)
_GOOD_QUALITIES = frozenset({"good", "0"})


def _sanitize(value: str) -> str:
    """Validate a tag name or LIKE pattern and escape single quotes."""
    if not isinstance(value, str):
        raise TypeError(f"Expected a string tag/pattern, got {type(value).__name__}")
    if not _TAG_PATTERN.match(value):
        raise ValueError(f"Tag/pattern contains unsupported characters: {value!r}")
    return value.replace("'", "''")


def _sanitize_field(value: str) -> str:
    """Validate a bare column/field name used as a raw SQL identifier."""
    if not isinstance(value, str):
        raise TypeError(f"Expected a string field name, got {type(value).__name__}")
    if not _FIELD_PATTERN.match(value):
        raise ValueError(f"Field name contains unsupported characters: {value!r}")
    return value


def _ts_literal(ts) -> str:
    """Format a datetime (or parseable string) as a SQLplus TIMESTAMP literal."""
    if isinstance(ts, str):
        ts = pd.to_datetime(ts)
    if not isinstance(ts, datetime):
        raise TypeError(f"Expected datetime or parseable string, got {type(ts)}")
    return f"TIMESTAMP'{ts.strftime(IP21_TS_FORMAT)}'"


def _ts_chunks(start, end, days: float) -> Iterable[tuple[datetime, datetime]]:
    """Split a datetime range into sequential (start, end) windows <= `days` wide.

    IP21 truncates any single query at MAX_ROWS regardless of tag count, so
    long raw pulls need to be bounded by date as well as by tag.
    """
    start = pd.to_datetime(start) if isinstance(start, str) else start
    end = pd.to_datetime(end) if isinstance(end, str) else end
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise TypeError(f"Expected datetime or parseable string, got {type(start)}/{type(end)}")
    step = pd.Timedelta(days=days)
    cur = start
    while cur < end:
        chunk_end = min(cur + step, end)
        yield cur, chunk_end
        cur = chunk_end


def _chunks(seq: Sequence, size: int) -> Iterable[Sequence]:
    if size < 1:
        raise ValueError("chunk size must be >= 1")
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _is_good_quality(value) -> bool:
    """Best-effort interpretation of an IP21 quality field as good/not-good.

    IP21 reports quality as a string ("Good", "Bad", "Suspect", ...) or, in some
    configurations, a numeric status code where 0 means good. Anything else is
    treated as not-good so the result errs on the side of flagging problems.
    """
    if value is None:
        return False
    return str(value).strip().lower() in _GOOD_QUALITIES
