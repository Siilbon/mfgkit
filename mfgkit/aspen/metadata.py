"""Definition/metadata table queries and the raw-query escape hatch."""

from __future__ import annotations

import pandas as pd

from .helpers import _sanitize, _sanitize_field


_SEARCHABLE_TABLES = frozenset({"IP_AnalogDef", "IP_DiscreteDef"})

_IO_DEF_TABLES = frozenset({"IOGetDef", "IOGetHistDef"})

# Searchable IO definition fields. "IO_VALUE_RECORD&&FLD" isn't a bare
# identifier, so these are whitelisted and quoted rather than run through
# _sanitize_field.
_IO_DEF_FIELDS = frozenset({"NAME", "IO_TAGNAME", "IO_VALUE_RECORD&&FLD"})


class MetadataMixin:
    """Search definition tables and run arbitrary SQLplus."""

    def search_tags(
        self,
        pattern: str | None = None,
        field: str = "NAME",
        table: str = "IP_AnalogDef",
    ) -> pd.DataFrame:
        """Search a definition table by a field, tagname (``NAME``) by default.

        Pass e.g. ``field="IP_DESCRIPTION"`` to search descriptions instead, or
        ``table="IP_DiscreteDef"`` to search discrete rather than analog tags.
        ``pattern`` is matched with a substring LIKE; omit it to return every row.
        """
        if table not in _SEARCHABLE_TABLES:
            raise ValueError(f"table must be one of {sorted(_SEARCHABLE_TABLES)}, got {table!r}")
        field = _sanitize_field(field)
        pattern = _sanitize(pattern) if pattern else ""
        sql_query = f"""
            SELECT *
            FROM {table}
            WHERE {field} LIKE '%{pattern}%'
        """
        return self._execute_df(sql_query)

    def ip_analog(self, pattern: str | None = None) -> pd.DataFrame:
        """Search IP_AnalogDef records by name pattern."""
        return self.search_tags(pattern, field="NAME", table="IP_AnalogDef")

    def ip_discrete(self, pattern: str | None = None) -> pd.DataFrame:
        """Search IP_DiscreteDef records by name pattern."""
        return self.search_tags(pattern, field="NAME", table="IP_DiscreteDef")

    def iogethistdef(self, record_pattern: str | None = None,
                     name_pattern: str = "") -> pd.DataFrame:
        return self._io_def("IOGetHistDef", record_pattern, name_pattern)

    def iogetdef(self, record_pattern: str | None = None,
                 name_pattern: str = "") -> pd.DataFrame:
        return self._io_def("IOGetDef", record_pattern, name_pattern)

    def _io_def(self, table: str, record_pattern, name_pattern) -> pd.DataFrame:
        record_pattern = _sanitize(record_pattern) if record_pattern else "%"
        name_pattern = _sanitize(name_pattern) if name_pattern else ""
        sql_query = f"""
            SELECT NAME, IO_TAGNAME, "IO_VALUE_RECORD&&FLD"
            FROM {table}
            WHERE "IO_VALUE_RECORD&&FLD" LIKE '{record_pattern}'
              AND NAME LIKE '%{name_pattern}%'
        """
        return self._execute_df(sql_query)

    def search_io_defs(
        self,
        pattern: str | None = None,
        field: str = "IO_TAGNAME",
        table: str = "IOGetDef",
    ) -> pd.DataFrame:
        """Search an IO transfer definition table by one field, DCS tag (``IO_TAGNAME``) by default.

        ``field`` is one of ``NAME`` (the IO get record), ``IO_TAGNAME``, or
        ``IO_VALUE_RECORD&&FLD`` (the IP21 record it writes). ``table`` is
        ``IOGetDef`` or ``IOGetHistDef``. ``pattern`` is matched with a
        substring LIKE; omit it to return every row.
        """
        if table not in _IO_DEF_TABLES:
            raise ValueError(f"table must be one of {sorted(_IO_DEF_TABLES)}, got {table!r}")
        if field not in _IO_DEF_FIELDS:
            raise ValueError(f"field must be one of {sorted(_IO_DEF_FIELDS)}, got {field!r}")
        pattern = _sanitize(pattern) if pattern else ""
        sql_query = f"""
            SELECT NAME, IO_TAGNAME, "IO_VALUE_RECORD&&FLD"
            FROM {table}
            WHERE "{field}" LIKE '%{pattern}%'
        """
        return self._execute_df(sql_query)

    def query(self, sql_query: str) -> pd.DataFrame:
        """Run an arbitrary SQLplus query. Caller is responsible for safety."""
        return self._execute_df(sql_query)
