"""Alarm system performance against ISA-18.2 / EEMUA 191 benchmarks.

Works on normalized events from :mod:`mfgkit.eventlog` (any frame with
``time`` and ``event_type`` columns). Only ``event_type == 'alarm'`` rows count:
returns, acks and operator actions are ignored.

ISA-18.2 states its targets *per operator position*. Rates can be divided by
``operator_positions``, but flood metrics can't: for those, filter the events
to the areas one console covers before calling.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .downtime import contiguous_runs

FLOOD_THRESHOLD = 10       # alarms in one window
FLOOD_WINDOW = "10min"
CHATTER_COUNT = 3          # alarms from one source ...
CHATTER_WINDOW = "60s"     # ... within this window
DEFAULT_SOURCE = ("module", "parameter")


def _alarms(events: pd.DataFrame) -> pd.DataFrame:
    missing = {"time", "event_type"} - set(events.columns)
    if missing:
        raise KeyError(f"events is missing columns {sorted(missing)}")
    return events.loc[events["event_type"] == "alarm"]


def alarm_counts(events, start=None, end=None, freq: str = FLOOD_WINDOW) -> pd.Series:
    """Alarms per fixed ``freq`` bucket from ``start`` to ``end``, zeros included.

    ``start`` defaults to the first alarm floored to ``freq``; ``end`` to just
    after the last alarm. Buckets are labelled by their start.
    """
    times = _alarms(events)["time"]
    freq_td = pd.Timedelta(freq)
    if start is None or end is None:
        if times.empty:
            return pd.Series(dtype="int64", name="alarms")
    start = pd.Timestamp(start) if start is not None else times.min().floor(freq)
    end = pd.Timestamp(end) if end is not None else times.max() + pd.Timedelta(1, "ns")
    n = max(math.ceil((end - start) / freq_td), 0)
    times = times[(times >= start) & (times < end)]
    idx = ((times - start) // freq_td).to_numpy(dtype="int64")
    counts = np.bincount(idx, minlength=n)[:n]
    return pd.Series(counts, index=pd.date_range(start, periods=n, freq=freq), name="alarms")


def floods(
    events,
    threshold: int = FLOOD_THRESHOLD,
    window: str = FLOOD_WINDOW,
    start=None,
    end=None,
) -> pd.DataFrame:
    """Alarm floods: runs of consecutive ``window`` buckets with more than ``threshold`` alarms.

    One row per flood with its start, end, duration, total alarms, the peak
    count in one window and (when modules are known) the biggest contributor.
    """
    counts = alarm_counts(events, start, end, window)
    alarms = _alarms(events)
    rows = []
    for s, e in contiguous_runs(counts > threshold):
        stop = e + pd.Timedelta(window)
        in_flood = counts.loc[s:e]
        row = {
            "start": s,
            "end": stop,
            "duration": stop - s,
            "alarms": int(in_flood.sum()),
            "peak": int(in_flood.max()),
        }
        if "module" in alarms.columns:
            mods = alarms.loc[(alarms["time"] >= s) & (alarms["time"] < stop), "module"]
            row["top_module"] = mods.mode().iloc[0] if mods.notna().any() else pd.NA
        rows.append(row)
    columns = ["start", "end", "duration", "alarms", "peak", "top_module"]
    return pd.DataFrame(rows, columns=columns)


def bad_actors(events, n: int = 10, by=DEFAULT_SOURCE) -> pd.DataFrame:
    """The ``n`` most frequent alarm sources, their share and cumulative share of all alarms."""
    alarms = _alarms(events)
    by = list(by)
    total = len(alarms)
    counts = (
        alarms.groupby(by, dropna=False).size().sort_values(ascending=False, kind="stable").head(n)
    )
    out = counts.rename("alarms").reset_index()
    out["share"] = out["alarms"] / total if total else 0.0
    out["cumulative_share"] = out["share"].cumsum()
    return out


def chattering(
    events,
    count: int = CHATTER_COUNT,
    window: str = CHATTER_WINDOW,
    by=DEFAULT_SOURCE,
) -> pd.DataFrame:
    """Alarm sources that came in at least ``count`` times within ``window``.

    One row per chattering source with its total alarms and the most it
    raised inside any one window, worst first.
    """
    alarms = _alarms(events)
    by = list(by)
    rows = []
    for key, group in alarms.groupby(by, dropna=False):
        times = group["time"].sort_values()
        peak = int(pd.Series(1, index=times.to_numpy()).rolling(window).sum().max())
        if peak >= count:
            key = key if isinstance(key, tuple) else (key,)
            rows.append({**dict(zip(by, key)), "alarms": len(times), "max_in_window": peak})
    out = pd.DataFrame(rows, columns=[*by, "alarms", "max_in_window"])
    return out.sort_values(["max_in_window", "alarms"], ascending=False, ignore_index=True)


def priority_distribution(events) -> pd.DataFrame:
    """Alarms and share by priority. ISA-18.2 suggests roughly 80/15/5 % low/medium/high."""
    alarms = _alarms(events)
    counts = alarms["priority"].value_counts(dropna=False)
    out = counts.rename("alarms").rename_axis("priority").reset_index()
    out["share"] = out["alarms"] / len(alarms) if len(alarms) else 0.0
    return out


def alarm_kpis(events, start, end, operator_positions: int = 1) -> pd.DataFrame:
    """Headline alarm metrics for ``start <= time < end`` against ISA-18.2 targets.

    Returns one row per metric with its ``value``, the ISA-18.2 ``target``
    (the "very likely acceptable" level where the standard gives a range) and
    whether it ``meets`` it. Rates are per operator position.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if end <= start:
        raise ValueError("end must be after start")
    if operator_positions < 1:
        raise ValueError("operator_positions must be at least 1")
    window = events.loc[(events["time"] >= start) & (events["time"] < end)]
    n = len(_alarms(window))
    days = (end - start) / pd.Timedelta("1D")
    per_10min = alarm_counts(window, start, end, "10min")
    per_hour = alarm_counts(window, start, end, "1h")
    top10 = bad_actors(window, 10)["alarms"].sum() / n * 100 if n else 0.0

    def pct(mask):
        return float(mask.mean() * 100) if len(mask) else 0.0

    rows = [
        ("Alarms per day per operator", n / days / operator_positions, 150),
        ("Alarms per hour per operator", n / (days * 24) / operator_positions, 6),
        ("% of 10-minute periods in flood (>10 alarms)", pct(per_10min > FLOOD_THRESHOLD), 1),
        ("Max alarms in a 10-minute period", int(per_10min.max()) if len(per_10min) else 0, 10),
        ("% of hours with >30 alarms", pct(per_hour > 30), 1),
        ("% of alarms from the top 10 sources", top10, 5),
        ("Chattering alarm sources", len(chattering(window)), 0),
    ]
    out = pd.DataFrame(rows, columns=["metric", "value", "target"])
    out["meets"] = out["value"] <= out["target"]
    return out
