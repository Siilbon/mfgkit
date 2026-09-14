"""Time-series queries against the HISTORY and AGGREGATES tables."""

from __future__ import annotations

import logging

import pandas as pd

from .helpers import IP21_TS_PARSE_FORMAT, TENTHS_PER_SECOND, _chunks, _ts_chunks, _ts_literal

logger = logging.getLogger(__name__)


class HistoryMixin:
    """Raw, interpolated, and aggregated time-series pulls."""

    def _history_pull(self, tag_list, where_time: str, request: int,
                      period_tenths: int | None = None) -> pd.DataFrame:
        """Shared HISTORY query: chunked, pivoted wide (TS x tag)."""
        tags = self._normalize_tags(tag_list)
        period_clause = f" AND PERIOD = {int(period_tenths)}" if period_tenths else ""
        frames = []
        for chunk in _chunks(tags, self.chunk_size):
            in_list = ", ".join(f"'{t}'" for t in chunk)
            sql_query = f"""
                SELECT NAME, TS, VALUE
                FROM HISTORY
                WHERE NAME IN ({in_list})
                  AND {where_time}
                  AND REQUEST = {int(request)}{period_clause}
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
                     period_tenths: int | None = None, chunk_days: float = 1) -> pd.DataFrame:
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
            frames.append(self._history_pull(tag_list, where_time, request, period_tenths))
        if not frames:
            return pd.DataFrame()
        wide = pd.concat(frames)
        wide = wide[~wide.index.duplicated(keep="last")]
        return wide.sort_index()

    def start_end(self, tag_list, start_datetime, end_datetime,
                  request: int = 1, chunk_days: float = 1) -> pd.DataFrame:
        """Raw (REQUEST=1) history between two timestamps, wide format.

        `chunk_days` bounds each underlying query so long raw pulls don't hit
        IP21's MAX_ROWS truncation; lower it further for very dense tags, or
        raise it for sparse ones to cut down on round-trips.
        """
        return self._ranged_pull(tag_list, start_datetime, end_datetime,
                                 request, chunk_days=chunk_days)

    def current(self, tag_list, mins: int = 30, hours: int = 0,
                days: int = 0, request: int = 1) -> pd.DataFrame:
        """History from now back by the given duration, wide format."""
        total_seconds = 60 * (mins + 60 * hours + 24 * 60 * days)
        duration_tenths = TENTHS_PER_SECOND * total_seconds
        where_time = (
            f"TS BETWEEN CURRENT_TIMESTAMP - {duration_tenths} "
            f"AND CURRENT_TIMESTAMP"
        )
        return self._history_pull(tag_list, where_time, request)

    def interpolated(self, tag_list, start_datetime, end_datetime,
                     period_seconds: int = 60, chunk_days: float = 30) -> pd.DataFrame:
        """Interpolated history (REQUEST=2) on a fixed period.

        Far cheaper than raw pulls for trending — one row per tag per period.
        `chunk_days` bounds each underlying query to avoid MAX_ROWS
        truncation on very long or high-tag-count pulls.
        """
        return self._ranged_pull(
            tag_list, start_datetime, end_datetime, request=2,
            period_tenths=TENTHS_PER_SECOND * period_seconds,
            chunk_days=chunk_days,
        )

    def aggregates(self, tag_list, start_datetime, end_datetime,
                   period_seconds: int = 3600,
                   stats: tuple[str, ...] = ("AVG", "MIN", "MAX")) -> pd.DataFrame:
        """Periodic aggregates from the AGGREGATES table, long format.

        Returns one row per (tag, period) with the requested statistics.
        """
        tags = self._normalize_tags(tag_list)
        stat_cols = ", ".join(stats)
        period = TENTHS_PER_SECOND * int(period_seconds)
        frames = []
        for chunk in _chunks(tags, self.chunk_size):
            in_list = ", ".join(f"'{t}'" for t in chunk)
            sql_query = f"""
                SELECT NAME, TS, {stat_cols}
                FROM AGGREGATES
                WHERE NAME IN ({in_list})
                  AND TS BETWEEN {_ts_literal(start_datetime)}
                             AND {_ts_literal(end_datetime)}
                  AND PERIOD = {period}
            """
            frames.append(self._execute_df(sql_query))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not df.empty:
            df["TS"] = pd.to_datetime(df["TS"], format=IP21_TS_PARSE_FORMAT)
        return df
