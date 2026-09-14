"""Connection lifecycle and core query execution.

``AspenConnBase`` owns the ODBC connection, its reconnect/close lifecycle, and
the shared ``_execute_df`` / ``_normalize_tags`` primitives that the query
mixins build on. The public class is assembled in :mod:`aspen.connection`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Self

import pandas as pd

from . import helpers
from .helpers import DBError, _sanitize

if TYPE_CHECKING:
    import pyodbc

logger = logging.getLogger(__name__)


class AspenConnBase:
    """Owns the connection and the primitives shared by the query mixins."""

    # Cheap query used to verify the connection is usable. It validates both the
    # ODBC round-trip and that the definition table is queryable, while matching
    # no rows. Override per-deployment via the ``ping_sql`` constructor argument.
    DEFAULT_PING_SQL = "SELECT NAME FROM IP_AnalogDef WHERE NAME = '__ip21_ping__'"

    def __init__(
        self,
        server: str,
        username: str | None = None,
        timeout: int = 120,
        max_rows: int = 1_000_000,
        chunk_size: int = 200,
        ping_sql: str | None = None,
        connect_fn: Callable[[], "pyodbc.Connection"] | None = None,
    ):
        self.server = server
        self.username = username
        self.timeout = timeout
        self.max_rows = max_rows
        self.chunk_size = chunk_size
        self.ping_sql = ping_sql or self.DEFAULT_PING_SQL
        # Injectable factory keeps the class testable without a live driver.
        self._connect_fn = connect_fn or self._default_connect
        self.conn = self._connect()

    def __repr__(self) -> str:
        state = "open" if self.conn is not None else "closed"
        return f"AspenConn(server={self.server!r}, {state})"

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    def _default_connect(self) -> "pyodbc.Connection":
        if helpers.pyodbc is None:
            raise RuntimeError(
                "pyodbc is not installed; install it or pass connect_fn=... "
                "to use AspenConn."
            )
        return helpers.pyodbc.connect(
            f"DRIVER={{AspenTech SQLplus}};HOST={self.server}",
            timeout=self.timeout,
        )

    def _connect(self) -> "pyodbc.Connection":
        conn = self._connect_fn()
        try:
            conn.timeout = self.timeout  # per-query timeout
        except (AttributeError, DBError):  # some drivers reject setting timeout
            pass
        try:
            # SQLplus silently truncates result sets at its MAX_ROWS limit.
            # Raise it explicitly so long pulls come back complete.
            conn.execute(f"SET MAX_ROWS {int(self.max_rows)};")
        except DBError:
            logger.warning("Could not SET MAX_ROWS; results may be truncated.")
        logger.info(f"Connected to IP21 server {self.server!r}")
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

    # ------------------------------------------------------------------ #
    # Core execution
    # ------------------------------------------------------------------ #

    def _execute_df(self, sql_query: str) -> pd.DataFrame:
        """Run a query and return a DataFrame, reconnecting once on failure."""
        for attempt in (1, 2):
            try:
                cursor = self.conn.cursor()
                try:
                    cursor.execute(sql_query)
                    if cursor.description is None:  # statement with no result set
                        return pd.DataFrame()
                    columns = [col[0] for col in cursor.description]
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
                df = pd.DataFrame.from_records(
                    [tuple(r) for r in rows], columns=columns
                )
                logger.debug("Query returned %d rows.", len(df))
                return df
            except DBError as exc:
                if attempt == 1:
                    logger.warning("Query failed (%s); reconnecting once.", exc)
                    self.reconnect()
                else:
                    logger.error("Query failed after reconnect:\n%s", sql_query)
                    raise

    def _normalize_tags(self, tag_list) -> list[str]:
        if isinstance(tag_list, str):
            tag_list = [tag_list]
        tags = [_sanitize(t) for t in tag_list]
        if not tags:
            raise ValueError("tag_list is empty")
        return tags
