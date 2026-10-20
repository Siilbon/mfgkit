"""Time-series queries against the HISTORY and AGGREGATES tables."""

from __future__ import annotations

import logging

import pandas as pd

from .helpers import IP21_TS_PARSE_FORMAT, TENTHS_PER_SECOND, _chunks, _ts_chunks, _ts_literal

logger = logging.getLogger(__name__)

# HISTORY.REQUEST values (SQLplus docs). 1-3 and 6-7 are computed at evenly
# spaced times, so STEPPED changes them; 4-5 are the stored values themselves.
REQUEST_TIMES = 1       # evenly spaced (interpolated)
REQUEST_TIMES2 = 2      # evenly spaced plus a point at the end of the range
REQUEST_ACTUALS = 4     # actual recorded data (raw)


def _as_int(wide: pd.DataFrame) -> pd.DataFrame:
    """Round to whole numbers as nullable Int64, so gaps stay <NA>, not NaN."""
    if wide.empty:
        return wide
    return wide.apply(pd.to_numeric, errors="coerce").round().astype("Int64")


class HistoryMixin:
    """Raw, interpolated, and aggregated time-series pulls."""

    def _history_pull(self, tag_list, where_time: str, request: int,
                      period_tenths: int | None = None,
                      stepped: bool = False) -> pd.DataFrame:
        """Shared HISTORY query: chunked, pivoted wide (TS x tag)."""
        tags = self._normalize_tags(tag_list)
        period_clause = f" AND PERIOD = {int(period_tenths)}" if period_tenths else ""
        # STEPPED = 1 holds the last value instead of drawing a line between samples
        stepped_clause = " AND STEPPED = 1" if stepped else ""
        frames = []
        for chunk in _chunks(tags, self.chunk_size):
            in_list = ", ".join(f"'{t}'" for t in chunk)
            sql_query = f"""
                SELECT NAME, TS, VALUE
                FROM HISTORY
                WHERE NAME IN ({in_list})
                  AND {where_time}
                  AND REQUEST = {int(request)}{period_clause}{stepped_clause}
            """
            df_chunk = self._execute_df(sql_query)
            if len(df_chunk) >= self.max_rows:
                logger.warning(
                    "Query for %d tag(s) returned %d rows (>= MAX_ROWS=%d) and "
                    "was likely truncated by IP21; narrow the date range or "
                    "tag chunk to get complete data.",
                    len(chunk), len(df_chunk), self.max_rows,
                )
            frames.append(df_chunk)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df.empty:
            logger.warning("HISTORY query returned no rows.")
            return df
        # pivot_table tolerates duplicate (TS, NAME) pairs; keep the last value
        wide = df.pivot_table(index="TS", columns="NAME", values="VALUE",
                              aggfunc="last")
        wide.index = pd.to_datetime(wide.index, format=IP21_TS_PARSE_FORMAT)
        return wide.sort_index()

    def _ranged_pull(self, tag_list, start_datetime, end_datetime, request: int,
                     period_tenths: int | None = None, chunk_days: float = 1,
                     stepped: bool = False) -> pd.DataFrame:
        """Date-chunked wrapper around `_history_pull` for TS BETWEEN literal queries.

        MAX_ROWS bounds each query regardless of tag chunking, so long pulls
        also need to be split by date; results are concatenated back together.
        """
        frames = []
        for chunk_start, chunk_end in _ts_chunks(start_datetime, end_datetime, chunk_days):
            where_time = (
                f"TS BETWEEN {_ts_literal(chunk_start)} "
                f"AND {_ts_literal(chunk_end)}"
            )
            frames.append(self._history_pull(tag_list, where_time, request,
                                             period_tenths, stepped=stepped))
        if not frames:
            return pd.DataFrame()
        wide = pd.concat(frames)
        wide = wide[~wide.index.duplicated(keep="last")]
        return wide.sort_index()

    def start_end(self, tag_list, start_datetime, end_datetime,
                  request: int = REQUEST_ACTUALS, chunk_days: float = 1,
                  stepped: bool = False, as_int: bool = False) -> pd.DataFrame:
        """Raw (REQUEST=4, actual recorded values) history between two
        timestamps, wide format.

        `chunk_days` bounds each underlying query so long raw pulls don't hit
        IP21's MAX_ROWS truncation; lower it further for very dense tags, or
        raise it for sparse ones to cut down on round-trips.
        `stepped=True` only matters if you pass an evenly spaced `request`
        (1-3, 6-7); raw values are returned as stored either way.
        `as_int=True` returns whole numbers (nullable Int64).
        """
        wide = self._ranged_pull(tag_list, start_datetime, end_datetime,
                                 request, chunk_days=chunk_days,
                                 stepped=stepped)
        return _as_int(wide) if as_int else wide

    def current(self, tag_list, mins: int = 30, hours: int = 0,
                days: int = 0, request: int = REQUEST_ACTUALS,
                stepped: bool = False, as_int: bool = False) -> pd.DataFrame:
        """Raw (REQUEST=4) history from now back by the given duration,
        wide format.

        `stepped=True` only matters with an evenly spaced `request` (1-3, 6-7).
        `as_int=True` returns whole numbers (nullable Int64).
        """
        total_seconds = 60 * (mins + 60 * hours + 24 * 60 * days)
        duration_tenths = TENTHS_PER_SECOND * total_seconds
        where_time = (
            f"TS BETWEEN CURRENT_TIMESTAMP - {duration_tenths} "
            f"AND CURRENT_TIMESTAMP"
        )
        wide = self._history_pull(tag_list, where_time, request,
                                  stepped=stepped)
        return _as_int(wide) if as_int else wide

    def interpolated(self, tag_list, start_datetime, end_datetime,
                     period_seconds: int = 60, chunk_days: float = 30,
                     stepped: bool = False, as_int: bool = False) -> pd.DataFrame:
        """Interpolated history (REQUEST=2, TIMES2) on a fixed period.

        Far cheaper than raw pulls for trending — one row per tag per period.
        `chunk_days` bounds each underlying query to avoid MAX_ROWS
        truncation on very long or high-tag-count pulls.

        For discrete/state tags (modes, counts, step numbers) pass
        `stepped=True` so IP21 holds the last value instead of drawing a line
        between samples (a 2 -> 3 change would otherwise come back as 2.4,
        2.7, ...), and `as_int=True` to get whole numbers (nullable Int64).
        """
        wide = self._ranged_pull(
            tag_list, start_datetime, end_datetime, request=REQUEST_TIMES2,
            period_tenths=TENTHS_PER_SECOND * period_seconds,
            chunk_days=chunk_days, stepped=stepped,
        )
        return _as_int(wide) if as_int else wide

    def aggregates(self, tag_list, start_datetime, end_datetime,
                   period_seconds: int = 3600,
                   stats: tuple[str, ...] = ("AVG", "MIN", "MAX"),
                   stepped: bool = False) -> pd.DataFrame:
        """Periodic aggregates from the AGGREGATES table, long format.

        Returns one row per (tag, period) with the requested statistics.
        IP21 integrates (time-weights) the stats by default. For discrete/state
        tags pass `stepped=True` so each value is held until the next one,
        instead of being treated as a ramp, when AVG/STD/etc. are worked out.
        """
        tags = self._normalize_tags(tag_list)
        stat_cols = ", ".join(stats)
        period = TENTHS_PER_SECOND * int(period_seconds)
        stepped_clause = " AND STEPPED = 1" if stepped else ""
        frames = []
        for chunk in _chunks(tags, self.chunk_size):
            in_list = ", ".join(f"'{t}'" for t in chunk)
            sql_query = f"""
                SELECT NAME, TS, {stat_cols}
                FROM AGGREGATES
                WHERE NAME IN ({in_list})
                  AND TS BETWEEN {_ts_literal(start_datetime)}
                             AND {_ts_literal(end_datetime)}
                  AND PERIOD = {period}{stepped_clause}
            """
            frames.append(self._execute_df(sql_query))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not df.empty:
            df["TS"] = pd.to_datetime(df["TS"], format=IP21_TS_PARSE_FORMAT)
        return df
