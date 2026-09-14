"""Smoke tests: every subpackage imports, and the lazy wiring actually works.

These are cheap but catch the failure mode that matters most during the
consolidation -- a stale absolute import (``from utils import ...``) that only
resolves when the module happens to sit on sys.path.
"""

import importlib
import sys

import pytest


def test_top_level_import_is_cheap():
    """Importing mfgkit must not eagerly pull in a vendor subpackage."""
    for mod in [m for m in sys.modules if m.startswith("mfgkit")]:
        del sys.modules[mod]

    importlib.import_module("mfgkit")

    assert "mfgkit.aspen" not in sys.modules
    assert "mfgkit.plc" not in sys.modules


def test_subpackages_resolve_lazily():
    import mfgkit

    assert mfgkit.deltav.modules is not None
    assert mfgkit.utils.read_stacked_csv is not None


def test_unknown_attribute_raises():
    import mfgkit

    with pytest.raises(AttributeError):
        mfgkit.nonexistent


@pytest.mark.parametrize(
    "module",
    [
        "mfgkit.utils",
        "mfgkit.aspen",
        "mfgkit.deltav",
        "mfgkit.deltav.modules",
        "mfgkit.deltav.io",
        "mfgkit.deltav.em",
        "mfgkit.deltav.interlocks",
        "mfgkit.deltav.alarm_config",
        "mfgkit.deltav.alarm_active",
        "mfgkit.intellution",
        "mfgkit.plc",
        "mfgkit.plc.tags",
        "mfgkit.plc.rungs",
    ],
)
def test_module_imports(module):
    assert importlib.import_module(module) is not None


def test_shared_utils_is_single_source():
    """intellution and plc.tags must resolve to the same read_stacked_csv."""
    from mfgkit.utils import read_stacked_csv
    from mfgkit.intellution import tags as intellution_tags
    from mfgkit.plc import tags as plc_tags

    assert intellution_tags.read_stacked_csv is read_stacked_csv
    assert plc_tags.read_stacked_csv is read_stacked_csv


def test_graph_requires_extra_but_is_not_imported_by_default():
    import mfgkit.plc

    assert "mfgkit.plc.graph" not in sys.modules
    pytest.importorskip("networkx")
    assert mfgkit.plc.build_logic_graph is not None
