"""Connection health and live-value queries.

``ConnStatus`` is the structured result of a health check; ``HealthMixin`` adds
the cheap "is this connection / are these tags healthy right now" queries that
don't need a full HISTORY pull.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .helpers import DBError, _chunks, _is_good_quality

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnStatus:
    """Structured result of a connection health check."""

    server: str
    connected: bool
    error: str | None = None

    def __bool__(self) -> bool:  # truthy iff the connection is usable
        return self.connected


class HealthMixin:
    """Connection-health and live-value queries (no full HISTORY pull)."""

    def status(self) -> ConnStatus:
        """Check connection health with a cheap ping; never raises.

        Returns a :class:`ConnStatus` describing whether the connection is
        usable and, if not, the error encountered.
        """
        if self.conn is None:
            return ConnStatus(self.server, connected=False, error="connection closed")
        try:
            cursor = self.conn.cursor()
            try:
                cursor.execute(self.ping_sql)
                cursor.fetchall()
            finally:
                cursor.close()
            return ConnStatus(self.server, connected=True)
        except DBError as exc:
            return ConnStatus(self.server, connected=False, error=str(exc))

    @property
    def is_alive(self) -> bool:
        """True if the connection responds to a ping query."""
        return self.status().connected

    def snapshot(self, tag_list) -> pd.DataFrame:
        """Live value + quality per tag from the current-value (AnalogDef) record.

        Far cheaper than a HISTORY pull when you only need the latest reading.
        Returns a DataFrame indexed by tag NAME with columns:
        ``VALUE``, ``TS`` (reading time), ``QUALITY`` (raw IP21 quality),
        and ``IS_GOOD`` (bool). Tags with no record are simply absent.

        Note: field names below are the standard IP_AnalogDef columns; IP21
        installations with custom record definitions may need adjustment.
        """
        tags = self._normalize_tags(tag_list)
        frames = []
        for chunk in _chunks(tags, self.chunk_size):
            in_list = ", ".join(f"'{t}'" for t in chunk)
            sql_query = f"""
                SELECT NAME, IP_INPUT_VALUE, IP_INPUT_TIME, IP_INPUT_QUALITY
                FROM IP_AnalogDef
                WHERE NAME IN ({in_list})
            """
            frames.append(self._execute_df(sql_query))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df.empty:
            logger.warning("snapshot() returned no rows.")
            return pd.DataFrame(columns=["VALUE", "TS", "QUALITY", "IS_GOOD"])
        df = df.rename(
            columns={
                "IP_INPUT_VALUE": "VALUE",
                "IP_INPUT_TIME": "TS",
                "IP_INPUT_QUALITY": "QUALITY",
            }
        )
        df["TS"] = pd.to_datetime(df["TS"], errors="coerce")
        df["IS_GOOD"] = df["QUALITY"].map(_is_good_quality)
        return df.set_index("NAME")[["VALUE", "TS", "QUALITY", "IS_GOOD"]]

    def io_task_status(self) -> pd.DataFrame:
        """Per-IO-task good/bad tag health from IOGetHistDef.

        One row per IO main task with total tag count and the percentage of
        good/bad tags, worst (most bad) first.
        """
        sql_query = """
            SELECT IO_MAIN_TASK,
                   SUM(IO_#TAGS) AS Total_Tags,
                   (SUM(IO_#_BAD_TAGS) - SUM(IO_#_SCAN_OFF_TAGS)) * 100.0
                       / SUM(IO_#TAGS) AS Pct_Bad_Tags,
                   SUM(IO_#_GOOD_TAGS) * 100.0 / SUM(IO_#TAGS) AS Pct_Good_Tags
            FROM IOGetHistDef
            GROUP BY IO_MAIN_TASK
            ORDER BY Pct_Bad_Tags DESC
        """
        return self._execute_df(sql_query)

    # Backwards-compatible alias for the original method name.
    iostatus = io_task_status
