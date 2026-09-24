"""The public ``AspenConn`` class, assembled from focused mixins."""

from __future__ import annotations

from .base import AspenConnBase
from .health import HealthMixin
from .history import HistoryMixin
from .metadata import MetadataMixin
from .opcua import OpcUaMixin


class AspenConn(AspenConnBase, HealthMixin, HistoryMixin, MetadataMixin, OpcUaMixin):
    """Connection wrapper for an IP21 server via AspenTech SQLplus ODBC.

    The implementation is split across focused modules:

    - :mod:`aspen.base`     connection lifecycle + core execution
    - :mod:`aspen.health`   ``status``/``is_alive``/``snapshot``/``io_task_status``
    - :mod:`aspen.history`  ``start_end``/``current``/``interpolated``/``aggregates``
    - :mod:`aspen.metadata` ``search_tags``/``ip_analog``/``iogethistdef``/``iogetdef``/``query``
    - :mod:`aspen.opcua`    ``opcua_node_id``/``opcua_node_ids``

    Usage:
        with AspenConn("MYIP21HOST") as ip21:
            df = ip21.current(["FI-101.PV", "TI-202.PV"], hours=4)
            snap = ip21.snapshot(["FI-101.PV"])
            if ip21.is_alive:
                ...
    """
