"""Tests for the ControlLogix IO mapping, using synthetic tag tables.

These use a stand-in for ControlLogix_Tags rather than a real export, so the
suite stays self-contained -- no plant data files in the repo.
"""

import logging
import types

import pandas as pd
import pytest

from mfgkit.plc import get_io


def _fake_tags(specifiers):
    """Minimal stand-in exposing the .tables attribute get_io reads."""
    df = pd.DataFrame({
        "type": ["ALIAS"] * len(specifiers),
        "scope": [None] * len(specifiers),
        "name": [f"TAG_{i}" for i in range(len(specifiers))],
        "description": [""] * len(specifiers),
        "datatype": ["BOOL"] * len(specifiers),
        "specifier": specifiers,
        "attributes": [""] * len(specifiers),
    })
    return types.SimpleNamespace(tables={"ctrl": df})


RACK_SPECS = [
    "R11B:I.Data[1].10",
    "R11B:I.Data[1].8",
    "R03C:O.Data[2].4",
]


def test_get_io_extracts_rack_mod_bit():
    io = get_io(_fake_tags(RACK_SPECS), rack_prefix=r"R\d{2}[A-Z]")

    assert len(io) == 3
    assert sorted(io["rack"].unique()) == ["R03C", "R11B"]
    assert set(io["in_out"]) == {"I", "O"}
    assert io["mod"].tolist() == [1, 1, 2]
    assert io["bit"].tolist() == [10, 8, 4]


def test_mismatched_rack_prefix_warns_with_real_specifiers(caplog):
    """An empty result must not pass silently -- it reads as 'no IO'."""
    with caplog.at_level(logging.WARNING, logger="mfgkit.plc.tags"):
        io = get_io(_fake_tags(RACK_SPECS), rack_prefix=r"Rack_\d{2}")

    assert io.empty
    assert "No alias specifiers matched" in caplog.text
    # The warning must show real specifiers so the fix is obvious.
    assert "R11B:I.Data[1].10" in caplog.text


def test_matching_prefix_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="mfgkit.plc.tags"):
        get_io(_fake_tags(RACK_SPECS), rack_prefix=r"R\d{2}[A-Z]")

    assert "No alias specifiers matched" not in caplog.text


def test_controller_with_no_aliases_does_not_warn(caplog):
    """Nothing to diagnose when there are no aliases at all."""
    with caplog.at_level(logging.WARNING, logger="mfgkit.plc.tags"):
        io = get_io(_fake_tags([]), rack_prefix=r"R\d{2}[A-Z]")

    assert io.empty
    assert "No alias specifiers matched" not in caplog.text
