"""Cross-project analysis helpers.

Generic operations on historian data that are not specific to any one control
system, and so belong to none of the vendor subpackages. Add future shared
helpers here as new submodules.
"""

from .downtime import (
    contiguous_runs,
    downtime_summary,
    flag_downtime,
    mask_downtime,
)
from . import alarms, interventions

__all__ = [
    "contiguous_runs",
    "flag_downtime",
    "mask_downtime",
    "downtime_summary",
    "alarms",
    "interventions",
]
