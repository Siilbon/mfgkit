"""Read-only connection to an alarm/event table and normalization of its rows.

:class:`EventLogConn` is the shared reader; vendor subclasses only choose the
default classification rules (``mfgkit.deltav.events.EventChronicle``,
``mfgkit.intellution.alarms.IfixAlarmLog``).

Queries are parameterized ``SELECT`` statements built from a validated
:class:`~mfgkit.eventlog.mapping.EventLogMapping`, and the connection is opened
with ``readonly=True``. Still use a read-only database account.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Self

import pandas as pd

from .mapping import NORMALIZED_COLUMNS, EventLogMapping, quote_column, quote_table
from .rules import classify

if TYPE_CHECKING:
    import pyodbc
else:  # pyodbc is optional (mfgkit[sql]); everything else works without it
    try:
        import pyodbc
    except ImportError:  # pragma: no cover - only where pyodbc is absent
        pyodbc = None

DBError: type[Exception] = pyodbc.Error if pyodbc is not None else Exception

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = (*NORMALIZED_COLUMNS, "event_type", "source")


def connect_readonly(conn_str: str, timeout: int = 120) -> "pyodbc.Connection":
    """Open a pyodbc connection in read-only mode."""
    if pyodbc is None:
        raise RuntimeError(
            "pyodbc is not installed; install mfgkit[sql] or pass connect_fn=..."
        )
    return pyodbc.connect(conn_str, timeout=timeout, readonly=True)


def build_query(mapping: EventLogMapping, filters: dict | None = None) -> tuple[str, list]:
    """SELECT for one time window; returns ``(sql, extra_params)``.

    The first two parameters are always the window start and end. ``filters``
    maps normalized columns to a value or a list of values (matched with IN).
    """
    select = ", ".join(
        f"{quote_column(src)} AS [{dst}]" for dst, src in mapping.columns.items()
    )
    time_col = quote_column(mapping.columns["time"])
    where = [f"{time_col} >= ?", f"{time_col} < ?"]
    params: list = []
    for col, value in (filters or {}).items():
        if col not in mapping.columns:
            raise KeyError(f"Can't filter on {col!r}: it isn't mapped in this table.")
        values = [value] if isinstance(value, (str, int, float)) else list(value)
        if not values:
            raise ValueError(f"Filter {col!r} has no values.")
        where.append(f"{quote_column(mapping.columns[col])} IN ({', '.join('?' * len(values))})")
        params.extend(values)
    sql = (
        f"SELECT {select} FROM {quote_table(mapping.table)} "
        f"WHERE {' AND '.join(where)} ORDER BY {time_col}"
    )
    return sql, params


def normalize_events(raw: pd.DataFrame, rules, source: str) -> pd.DataFrame:
    """Shape rows already renamed to normalized names into the standard frame.

    Fills unmapped columns with NA, parses ``time``, strips padded strings,
    classifies ``event_type`` and stamps ``source``.
    """
    df = raw.copy()
    for col in NORMALIZED_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    bad = df["time"].isna()
    if bad.any():
        logger.warning("Dropping %d rows with an unreadable time.", int(bad.sum()))
        df = df.loc[~bad]
    for col in NORMALIZED_COLUMNS[1:]:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col].dtype):
            df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)
    df["event_type"] = classify(df, rules)
    df["source"] = source
    return df.loc[:, list(OUTPUT_COLUMNS)].sort_values("time", kind="stable").reset_index(drop=True)


class EventLogConn:
    """Fetch normalized events from one mapped alarm/event table.

    Args:
        conn_str: ODBC connection string (include ``ApplicationIntent=ReadOnly``
            for SQL Server where available).
        mapping: an :class:`EventLogMapping`, or a path to its JSON.
        timeout: login and per-query timeout in seconds.
        connect_fn: optional connection factory, mainly for tests.
    """

    source = "eventlog"
    default_rules: tuple = ()

    def __init__(
        self,
        conn_str: str,
        mapping: EventLogMapping | str | Path,
        timeout: int = 120,
        connect_fn: Callable[[], "pyodbc.Connection"] | None = None,
    ):
        if not isinstance(mapping, EventLogMapping):
            mapping = EventLogMapping.from_json(mapping)
        self.conn_str = conn_str
        self.mapping = mapping
        self.timeout = timeout
        self._connect_fn = connect_fn or (lambda: connect_readonly(conn_str, timeout))
        self.conn = self._connect()

    @property
    def rules(self):
        return self.mapping.rules if self.mapping.rules is not None else self.default_rules

    def __repr__(self) -> str:
        state = "open" if self.conn is not None else "closed"
        return f"{type(self).__name__}(table={self.mapping.table!r}, {state})"

    def _connect(self) -> "pyodbc.Connection":
        conn = self._connect_fn()
        try:
            conn.timeout = self.timeout
        except (AttributeError, DBError):
            pass
        return conn

    def reconnect(self) -> None:
        self.close()
        self.conn = self._connect()

    def close(self) -> None:
        try:
            if self.conn is not None:
                self.conn.close()
        except DBError:
            pass
        self.conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _execute_df(self, sql: str, params: list) -> pd.DataFrame:
        for attempt in (1, 2):
            try:
                cursor = self.conn.cursor()
                try:
                    cursor.execute(sql, params)
                    columns = [c[0] for c in cursor.description]
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
                return pd.DataFrame.from_records([tuple(r) for r in rows], columns=columns)
            except DBError as exc:
                if attempt == 1:
                    logger.warning("Query failed (%s); reconnecting once.", exc)
                    self.reconnect()
                else:
                    raise

    def fetch(self, start, end, **filters) -> pd.DataFrame:
        """Events with ``start <= time < end``, normalized and classified.

        Keyword filters narrow the query on mapped columns, e.g.
        ``fetch(start, end, area='AREA_A')`` or ``module=['FIC101', 'FIC102']``.
        """
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        if end <= start:
            raise ValueError("end must be after start")
        sql, params = build_query(self.mapping, filters)
        raw = self._execute_df(sql, [start.to_pydatetime(), end.to_pydatetime(), *params])
        logger.info("Fetched %d events from %s.", len(raw), self.mapping.table)
        return normalize_events(raw, self.rules, self.source)
