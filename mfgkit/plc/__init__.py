"""Allen-Bradley ControlLogix PLC tag, rung, and logic-graph parsing.

``tags`` and ``rungs`` need only pandas. ``graph`` imports networkx at module
level, so it is resolved lazily -- ``import mfgkit.plc`` stays cheap and works
without the ``[graph]`` extra installed.
"""

import importlib

from .tags import ControlLogix_Tags, get_io, get_rack_df, find_references, reference_cols
from .rungs import ControlLogix_Rungs

__all__ = [
    "ControlLogix_Tags",
    "ControlLogix_Rungs",
    "get_io",
    "get_rack_df",
    "find_references",
    "reference_cols",
    "graph",
    "build_logic_graph",
    "edges_to_df",
    "subgraph_around",
    "plot_logic_graph",
    "plot_logic_graph_force",
]

_GRAPH_EXPORTS = {
    "build_logic_graph",
    "edges_to_df",
    "subgraph_around",
    "plot_logic_graph",
    "plot_logic_graph_force",
}


def __getattr__(name):
    if name == "graph" or name in _GRAPH_EXPORTS:
        # importlib, not "from . import graph" -- the latter re-enters this
        # __getattr__ through _handle_fromlist and recurses forever.
        graph = importlib.import_module(".graph", __name__)
        return graph if name == "graph" else getattr(graph, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
