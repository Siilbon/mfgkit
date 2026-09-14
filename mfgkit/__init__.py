"""mfgkit -- a manufacturing toolkit for control-system data.

Subpackages, one per vendor system:

- :mod:`mfgkit.aspen`       Aspen InfoPlus.21 (IP21) historian, via SQLplus ODBC
- :mod:`mfgkit.deltav`      Emerson DeltaV DCS flat-file and FHX exports
- :mod:`mfgkit.intellution` Intellution / iFIX HMI tag databases
- :mod:`mfgkit.plc`         Allen-Bradley ControlLogix tags, rungs, logic graphs

Plus system-agnostic analysis helpers:

- :mod:`mfgkit.analysis`    downtime flagging/masking and other shared routines

Shared parsing helpers live in :mod:`mfgkit.utils`.

Subpackages resolve lazily, so ``import mfgkit`` costs nothing and never
requires a driver that isn't installed. Reach for what you need:

    from mfgkit.aspen import AspenConn          # needs mfgkit[aspen]
    from mfgkit.deltav.modules import load_modules
    from mfgkit.intellution import IntellutionDB
    from mfgkit.plc import ControlLogix_Tags
    from mfgkit.analysis import flag_downtime, mask_downtime
"""

from __future__ import annotations

import importlib

__version__ = "0.1.0"

_SUBPACKAGES = ("analysis", "aspen", "deltav", "intellution", "plc", "utils")

__all__ = [*_SUBPACKAGES, "__version__"]


def __getattr__(name: str):
    if name in _SUBPACKAGES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
