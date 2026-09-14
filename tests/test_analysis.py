"""Tests for mfgkit.analysis.downtime."""

import pandas as pd
import pytest

from mfgkit.analysis import (
    contiguous_runs,
    downtime_summary,
    flag_downtime,
    mask_downtime,
)


def _series(values, freq="1min", start="2026-01-01"):
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


# --------------------------------------------------------------------------- #
# contiguous_runs
# --------------------------------------------------------------------------- #

def test_contiguous_runs_finds_each_run():
    mask = pd.Series([False, True, True, False, True], index=range(5))
    assert list(contiguous_runs(mask)) == [(1, 2), (4, 4)]


def test_contiguous_runs_empty_series():
    assert list(contiguous_runs(pd.Series([], dtype=bool))) == []


def test_contiguous_runs_all_false():
    assert list(contiguous_runs(pd.Series([False, False]))) == []


def test_contiguous_runs_all_true_is_one_run():
    assert list(contiguous_runs(pd.Series([True, True, True]))) == [(0, 2)]


# --------------------------------------------------------------------------- #
# direction: the below/above switch
# --------------------------------------------------------------------------- #

def test_below_flags_low_readings():
    s = _series([100] * 10 + [0] * 30 + [100] * 10)
    mask = flag_downtime(s, threshold=20, min_duration="10min", pad="0min")
    assert mask.sum() == 30
    assert not mask.iloc[0]


def test_above_flags_high_readings():
    """Downtime as a *high* reading -- a level backing up, a valve stuck open."""
    s = _series([0] * 10 + [100] * 30 + [0] * 10)
    mask = flag_downtime(s, threshold=20, direction="above",
                         min_duration="10min", pad="0min")
    assert mask.sum() == 30
    assert not mask.iloc[0]


def test_direction_inverts_the_result():
    s = _series([0] * 20 + [100] * 20)
    low = flag_downtime(s, threshold=50, direction="below", min_duration="0min", pad="0min")
    high = flag_downtime(s, threshold=50, direction="above", min_duration="0min", pad="0min")
    assert not (low & high).any()
    assert (low | high).all()


@pytest.mark.parametrize("alias", ["below", "lower", "under", "<", "BELOW", " Lower "])
def test_below_synonyms(alias):
    s = _series([0] * 20)
    assert flag_downtime(s, threshold=10, direction=alias,
                         min_duration="0min", pad="0min").all()


@pytest.mark.parametrize("alias", ["above", "higher", "over", ">", "ABOVE"])
def test_above_synonyms(alias):
    s = _series([100] * 20)
    assert flag_downtime(s, threshold=10, direction=alias,
                         min_duration="0min", pad="0min").all()


def test_unknown_direction_raises():
    with pytest.raises(ValueError, match="direction must be one of"):
        flag_downtime(_series([1, 2, 3]), threshold=1, direction="sideways")


# --------------------------------------------------------------------------- #
# min_duration and pad
# --------------------------------------------------------------------------- #

def test_brief_dip_is_ignored():
    """A 3-minute blip must not count as downtime at min_duration=10min."""
    s = _series([100] * 10 + [0] * 3 + [100] * 10)
    assert not flag_downtime(s, threshold=20, min_duration="10min", pad="0min").any()


def test_sustained_dip_is_flagged():
    s = _series([100] * 10 + [0] * 20 + [100] * 10)
    assert flag_downtime(s, threshold=20, min_duration="10min", pad="0min").any()


def test_pad_widens_each_stretch():
    s = _series([100] * 20 + [0] * 20 + [100] * 20)
    unpadded = flag_downtime(s, threshold=20, min_duration="5min", pad="0min")
    padded = flag_downtime(s, threshold=20, min_duration="5min", pad="5min")
    # 5 minutes added on each side, at 1-minute sampling.
    assert padded.sum() == unpadded.sum() + 10


def test_pad_clips_at_series_bounds():
    """Padding past the first/last sample must not raise."""
    s = _series([0] * 20)
    assert flag_downtime(s, threshold=10, min_duration="0min", pad="600min").all()


def test_non_series_input_raises():
    with pytest.raises(TypeError):
        flag_downtime([1, 2, 3], threshold=1)


# --------------------------------------------------------------------------- #
# mask_downtime
# --------------------------------------------------------------------------- #

def _frame():
    idx = pd.date_range("2026-01-01", periods=40, freq="1min")
    return pd.DataFrame(
        {"F42103_PV": [100] * 10 + [0] * 20 + [100] * 10, "other": range(40)},
        index=idx,
    )


def test_mask_downtime_drops_flagged_rows():
    df = _frame()
    out = mask_downtime(df, "F42103_PV", threshold=20,
                        min_duration="10min", pad="0min")
    assert len(out) == 20
    assert (out["F42103_PV"] == 100).all()


def test_mask_downtime_passes_direction_through():
    df = _frame()
    # min_duration=5min, not 10: each 10-sample run spans only 9 minutes.
    out = mask_downtime(df, "F42103_PV", threshold=20, direction="above",
                        min_duration="5min", pad="0min")
    # Inverted: the running rows are dropped instead.
    assert len(out) == 20
    assert (out["F42103_PV"] == 0).all()


def test_mask_downtime_is_stackable():
    """Applying per tag narrows further each time."""
    df = _frame()
    df["P42115_PV"] = [50] * 20 + [0] * 20
    first = mask_downtime(df, "F42103_PV", threshold=20,
                          min_duration="5min", pad="0min")
    second = mask_downtime(first, "P42115_PV", threshold=10,
                           min_duration="5min", pad="0min")
    assert len(second) < len(first) < len(df)


def test_mask_downtime_requires_explicit_tag_name():
    """No default tag: a wrong one would silently mask the wrong thing."""
    with pytest.raises(TypeError):
        mask_downtime(_frame())


def test_mask_downtime_unknown_column_names_the_options():
    with pytest.raises(KeyError, match="F42103_PV"):
        mask_downtime(_frame(), "NOT_A_TAG", threshold=20)


# --------------------------------------------------------------------------- #
# downtime_summary
# --------------------------------------------------------------------------- #

def test_downtime_summary_one_row_per_stretch():
    s = _series([100] * 10 + [0] * 20 + [100] * 10 + [0] * 20)
    out = downtime_summary(s, threshold=20, min_duration="5min", pad="0min")
    assert list(out.columns) == ["start", "end", "duration"]
    assert len(out) == 2
    assert out["duration"].iloc[0] == pd.Timedelta("19min")


def test_downtime_summary_empty_when_no_downtime():
    out = downtime_summary(_series([100] * 20), threshold=20)
    assert out.empty
    assert list(out.columns) == ["start", "end", "duration"]
