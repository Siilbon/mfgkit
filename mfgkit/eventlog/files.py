"""Normalized events from exported files instead of a database.

Event viewers export what they show as CSV or tab-delimited text, e.g. DeltaV
Event Chronicle's export:

    Date/Time*  Event Type*  Category*  Area  Node  Unit  Module  ...
    1  10/20/2026 2:13:05.693 PM  ALARM  SYSTEM  ...

Asterisks on header names are dropped, and a leading row-number column with no
header is ignored. Vendor readers wrap this with their own rules and hooks:
``EventChronicle.read_export(path)`` and ``IfixAlarmLog.read_export(path)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from .base import normalize_events
from .mapping import EventLogMapping, suggest_mapping

# Tried first, row by row; anything they miss falls back to pandas' own parsing.
TIME_FORMATS = (
    "%m/%d/%Y %I:%M:%S.%f %p",  # DeltaV export: 10/20/2026 2:13:05.693 PM
    "%m/%d/%Y %I:%M:%S %p",
    "%Y-%m-%d %H:%M:%S.%f",  # SQL tools: 2026-10-20 14:26:37.003
    "%Y-%m-%d %H:%M:%S",
)


def parse_times(values: pd.Series, time_format: str | None = None) -> pd.Series:
    """Parse event times, trying ``time_format`` or :data:`TIME_FORMATS` first."""
    text = values.astype("string").str.strip()
    formats = (time_format,) if time_format else TIME_FORMATS
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    for fmt in formats:
        todo = result.isna()
        if not todo.any():
            break
        result[todo] = pd.to_datetime(text[todo], format=fmt, errors="coerce")
    todo = result.isna() & text.notna()
    if todo.any() and not time_format:
        result[todo] = pd.to_datetime(text[todo], format="mixed", errors="coerce")
    return result


def read_event_file(
    path: str | Path,
    mapping: EventLogMapping | None = None,
    rules=(),
    source: str = "file",
    sep: str | None = None,
    time_format: str | None = None,
    encoding: str | None = None,
    before_classify: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    after_classify: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Read an exported event file into the normalized event frame.

    Args:
        path: the export. ``.csv`` is comma-separated; anything else is
            tab-delimited unless ``sep`` is given.
        mapping: normalized column -> file column. Defaults to
            :func:`suggest_mapping` on the file's header; its ``table`` is unused.
        rules: classification rules (vendor readers pass their defaults).
        time_format: a ``strftime`` format for the time column, when the
            built-in :data:`TIME_FORMATS` don't fit.
        encoding: file encoding; tries UTF-8 (with or without BOM), then cp1252.
    """
    path = Path(path)
    if sep is None:
        sep = "," if path.suffix.lower() == ".csv" else "\t"
    encodings = (encoding,) if encoding else ("utf-8-sig", "cp1252")
    for i, enc in enumerate(encodings):
        try:
            # A header one name short of the rows makes pandas use the first
            # column (DeltaV's row number) as the index, which is dropped below.
            raw = pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False, encoding=enc)
            break
        except UnicodeDecodeError:
            if i == len(encodings) - 1:
                raise
    raw = raw.reset_index(drop=True)
    raw.columns = [str(c).strip().rstrip("*").strip() for c in raw.columns]

    if mapping is None:
        mapping = suggest_mapping(raw.columns, "file")
    missing = sorted(set(mapping.columns.values()) - set(raw.columns))
    if missing:
        raise KeyError(f"{path.name} has no column(s) {missing}; its columns are {list(raw.columns)}")

    renamed = pd.DataFrame({dst: raw[src] for dst, src in mapping.columns.items()})
    renamed = renamed.replace({"NULL": ""})  # SQL tools write NULL as text
    renamed["time"] = parse_times(renamed["time"], time_format)
    return normalize_events(renamed, rules, source, before_classify, after_classify)


__all__ = ["TIME_FORMATS", "parse_times", "read_event_file"]
