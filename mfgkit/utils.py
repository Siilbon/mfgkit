"""Shared parsing helpers used across the vendor subpackages.

``read_stacked_csv`` is the one that matters: several control-system exports
(Intellution tag databases, ControlLogix tag CSVs, FactoryTalk PowerTool
configs) are not single CSVs but many tables concatenated into one file, each
with its own header row. This reads them into a dict of DataFrames.

This module is the single source of truth for that function. It previously
existed in three drifted copies with incompatible signatures; ``encoding`` is
keyword-only here and forwarded through ``**kwargs``, so callers that never
passed one keep working.
"""

from __future__ import annotations

import re

import pandas as pd


def read_stacked_csv(
    path,
    sep,
    offset,
    table_name_col=None,
    table_name_default=None,
    column_parser=None,
    **kwargs,
):
    """Read a file containing multiple stacked tables into a dict of DataFrames.

    Args:
        path (str): path to the .csv file.
        sep (str): regex matching the line that separates one table from the next.
        offset (int): number of lines between the separator and the table's data.
        table_name_col (str): column to take each table's name from. When None,
            tables are named positionally (``table_0``, ``table_1``, ...).
        table_name_default (str): name to use when ``table_name_col`` is NaN.
        column_parser (callable): receives ``df.columns`` and returns cleaned
            column names.
        **kwargs: forwarded to ``pd.read_csv`` (e.g. ``encoding='latin1'``).

    Returns:
        dict[str, pd.DataFrame]: table name -> table.
    """
    # 'encoding' has to reach both open() and read_csv(), so read it without
    # consuming it from kwargs.
    encoding = kwargs.get("encoding")

    tables = {}
    start = None
    table_number = 0

    with open(path, encoding=encoding) as file:
        for line_number, line in enumerate(file.readlines()):
            if not re.search(sep, line):
                continue

            if start is None:
                # First separator: the first table starts here.
                start = line_number
                continue

            end = line_number
            length = end - start - offset

            table = pd.read_csv(path, skiprows=start, nrows=length, **kwargs)

            if column_parser is not None:
                table.columns = column_parser(table.columns)

            if table_name_col is not None:
                if pd.notna(table[table_name_col][0]):
                    table_name = table[table_name_col][0]
                else:
                    table_name = table_name_default
            else:
                table_name = f"table_{table_number}"

            tables[table_name] = table
            start = end
            table_number += 1

    return tables
