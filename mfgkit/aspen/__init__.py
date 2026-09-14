"""Aspen InfoPlus.21 (IP21) data access via the SQLplus ODBC driver.

Features
--------
- Cursor-based fetches (no pandas/SQLAlchemy warning, explicit columns)
- Tag and timestamp sanitization (no raw f-string injection)
- pivot_table instead of pivot (tolerates duplicate timestamps)
- Tag-list chunking for large queries, concatenated transparently
- SET MAX_ROWS to avoid silent result truncation
- Interpolated history (REQUEST=2 + PERIOD) and aggregates table support
- Context manager, close(), and one automatic reconnect attempt

Status
------
- ``snapshot()``       live value + quality per tag (current-value records)
- ``is_alive`` /
  ``status()``         connection health (cheap ping + structured result)
- ``io_task_status()`` per-IO-task good/bad tag health from IOGetHistDef

Layout
------
The package is split into focused modules, all re-exported here:

- :mod:`aspen.helpers`    constants, optional ``pyodbc``/``DBError``, pure helpers
- :mod:`aspen.base`       connection lifecycle + core execution
- :mod:`aspen.health`     ``ConnStatus`` + health/live-value queries
- :mod:`aspen.history`    HISTORY/AGGREGATES time-series queries
- :mod:`aspen.metadata`   definition queries + the raw-``query`` escape hatch
- :mod:`aspen.connection` the public ``AspenConn`` assembled from the above

Testability
-----------
``pyodbc`` is imported lazily, and the connection factory is injectable via
``connect_fn``, so the package imports and unit-tests without the driver or a
live IP21 server. See ``test_aspen.py``.
"""

from __future__ import annotations

from .connection import AspenConn
from .health import ConnStatus
from .helpers import (
    DBError,
    IP21_TS_FORMAT,
    TENTHS_PER_SECOND,
    _chunks,
    _is_good_quality,
    _sanitize,
    _sanitize_field,
    _ts_literal,
)

__all__ = [
    "AspenConn",
    "ConnStatus",
    "DBError",
    "IP21_TS_FORMAT",
    "TENTHS_PER_SECOND",
    "_chunks",
    "_is_good_quality",
    "_sanitize",
    "_sanitize_field",
    "_ts_literal",
]
