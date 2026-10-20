"""Find event-like tables in a database and suggest a mapping for each.

Run once per site (``python -m mfgkit.eventlog discover ...``), then save and
review a mapping. Uses the ODBC catalog, so it works the same on SQL Server
and Access-era Event Chronicle databases.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .base import DBError, connect_readonly
from .mapping import quote_table, suggest_mapping

# Table names that usually hold alarms or events.
EVENT_TABLE_HINT = r"event|alarm|journal|chronicle|fixalm|almlog"


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def discover(conn, pattern: str = EVENT_TABLE_HINT, sample_rows: int = 5) -> dict:
    """Describe event-like tables: columns, a few sample rows, a suggested mapping.

    Args:
        conn: an open connection, or an ODBC connection string (opened read-only).
        pattern: regex on table names. Tables that don't match are still
            included when their columns look like an event log (a time column
            plus at least two other recognised columns).
        sample_rows: rows to read from each table found.

    Returns:
        ``{"schema.table": {"columns", "types", "sample", "suggested_mapping"}}``,
        ready for ``json.dump``.
    """
    own = isinstance(conn, str)
    if own:
        conn = connect_readonly(conn)
    try:
        cursor = conn.cursor()
        tables = [
            (row.table_schem, row.table_name)
            for row in cursor.tables(tableType="TABLE").fetchall()
        ]
        found = {}
        for schema, name in tables:
            key = f"{schema}.{name}" if schema else name
            cols = cursor.columns(table=name, schema=schema).fetchall()
            columns = [c.column_name for c in cols]
            try:
                mapping = suggest_mapping(columns, key)
            except ValueError:
                mapping = None
            looks_like_events = mapping is not None and len(mapping.columns) >= 3
            if not (re.search(pattern, name, re.I) or looks_like_events):
                continue

            entry = {
                "columns": columns,
                "types": [c.type_name for c in cols],
                "sample": [],
                "suggested_mapping": mapping.to_dict() if mapping else None,
            }
            try:
                cursor.execute(f"SELECT TOP {int(sample_rows)} * FROM {quote_table(key)}")
                names = [d[0] for d in cursor.description]
                entry["sample"] = [
                    {n: _jsonable(v) for n, v in zip(names, row)} for row in cursor.fetchall()
                ]
            except (ValueError, DBError) as exc:
                entry["sample_error"] = str(exc)
            found[key] = entry
        cursor.close()
        return found
    finally:
        if own:
            conn.close()
