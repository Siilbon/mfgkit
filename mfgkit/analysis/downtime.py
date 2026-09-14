"""Flagging and masking downtime stretches in historian data.

Process data pulled from a historian nearly always contains stretches the
analysis should not see: the line was down, a unit was in CIP, an instrument
was isolated. Fitting or trending across those stretches quietly corrupts the
result, so they get flagged and dropped first.

The core idea is that a *threshold crossing alone is not downtime*. Flow dips
below a threshold constantly -- noise, a brief upset, an exception-reporting
gap. A stretch only counts once it persists for ``min_duration``, and each
qualifying stretch is then padded by ``pad`` on both sides so the startup and
shutdown ramps around it are excluded too.
"""

from __future__ import annotations

from typing import Iterator

import pandas as pd

# Accepted spellings for the two comparison directions.
_BELOW = frozenset({"below", "lower", "under", "<"})
_ABOVE = frozenset({"above", "higher", "over", ">"})


def contiguous_runs(mask: pd.Series) -> Iterator[tuple]:
    """Yield ``(start, end)`` index labels for each contiguous True run.

    General-purpose over any boolean Series -- downtime is just the first
    caller. ``end`` is the last True label, not one past it, so the pair can be
    handed straight to ``.loc``.

        >>> list(contiguous_runs(pd.Series([False, True, True, False, True])))
        [(1, 2), (4, 4)]
    """
    if mask.empty:
        return
    group = (mask != mask.shift()).cumsum()
    for _, idx in mask[mask].groupby(group[mask]).groups.items():
        yield idx[0], idx[-1]


def _threshold_exceeded(series: pd.Series, threshold: float, direction: str) -> pd.Series:
    """Compare `series` against `threshold` in the requested direction."""
    normalized = str(direction).strip().lower()
    if normalized in _BELOW:
        return series < threshold
    if normalized in _ABOVE:
        return series > threshold
    raise ValueError(
        f"direction must be one of {sorted(_BELOW | _ABOVE)}, got {direction!r}"
    )


def flag_downtime(
    series: pd.Series,
    threshold: float,
    direction: str = "below",
    min_duration: str = "10min",
    pad: str = "30min",
) -> pd.Series:
    """Boolean mask (True = downtime) over a time-indexed Series.

    Args:
        series: time-indexed values to threshold, e.g. a flow or pressure tag.
        threshold: the value to compare against.
        direction: ``'below'`` (the default -- downtime is a *low* reading, as
            for flow) or ``'above'`` (downtime is a *high* reading, as for a
            level that backs up or a valve stuck open). ``'lower'``/``'higher'``
            are accepted as synonyms.
        min_duration: a crossing must persist at least this long to count,
            which is what separates real downtime from noise and brief upsets.
        pad: each qualifying stretch is widened by this much on both sides to
            also exclude the startup/shutdown ramp around it.

    Returns:
        A boolean Series on ``series``'s index; True where the data should be
        treated as downtime.
    """
    if not isinstance(series, pd.Series):
        raise TypeError(f"Expected a pandas Series, got {type(series).__name__}")

    min_duration = pd.Timedelta(min_duration)
    pad = pd.Timedelta(pad)

    exceeded = _threshold_exceeded(series, threshold, direction)

    mask = pd.Series(False, index=series.index)
    for start, end in contiguous_runs(exceeded):
        if end - start >= min_duration:
            # .loc on a sorted DatetimeIndex clips at the ends, so padding past
            # the first or last sample is safe.
            mask.loc[start - pad : end + pad] = True
    return mask


def mask_downtime(df: pd.DataFrame, tag_name: str, **kwargs) -> pd.DataFrame:
    """Drop rows of `df` falling within a downtime stretch on `tag_name`.

    ``tag_name`` is the column to threshold; every other keyword is passed
    through to :func:`flag_downtime`. Apply it once per tag to stack conditions:

        up = mask_downtime(df, 'F42103_PV', threshold=125, pad='120min')
        up = mask_downtime(up, 'P42115_PV', threshold=15,  pad='60min')

    There is deliberately no default tag -- the right one is plant- and
    unit-specific, and a wrong default silently masks the wrong thing.
    """
    if tag_name not in df.columns:
        raise KeyError(
            f"{tag_name!r} is not a column in the DataFrame. Available: "
            f"{list(df.columns)[:10]}{'...' if len(df.columns) > 10 else ''}"
        )
    return df.loc[~flag_downtime(df[tag_name], **kwargs)]


def downtime_summary(series: pd.Series, threshold: float, **kwargs) -> pd.DataFrame:
    """One row per downtime stretch: start, end, and duration.

    Useful for reporting what a mask actually removed before trusting it.
    """
    mask = flag_downtime(series, threshold, **kwargs)
    rows = [
        {"start": start, "end": end, "duration": end - start}
        for start, end in contiguous_runs(mask)
    ]
    return pd.DataFrame(rows, columns=["start", "end", "duration"])
