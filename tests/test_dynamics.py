"""Tests for mfgkit.dynamics."""

import numpy as np
import pandas as pd
import pytest

from mfgkit.dynamics import (
    SelfRegulating,
    delayed,
    fit_self_regulating,
    to_eu,
    to_pct,
    to_seconds,
)

PV_RANGE = (0.0, 150.0)


def _step(t, at=10.0, before=30.0, after=40.0):
    return np.where(t >= at, after, before)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def test_pct_eu_round_trip():
    values = np.array([0.0, 37.5, 150.0])
    assert np.allclose(to_pct(values, PV_RANGE), [0.0, 25.0, 100.0])
    assert np.allclose(to_eu(to_pct(values, PV_RANGE), PV_RANGE), values)


def test_delayed_holds_first_value_then_shifts():
    t = np.arange(0, 10, 1.0)
    u = np.where(t >= 2, 1.0, 0.0)
    ud = delayed(t, u, 3.0)
    assert np.all(ud[t < 5] == 0.0)
    assert np.all(ud[t >= 5] == 1.0)


def test_to_seconds_accepts_datetimes():
    idx = pd.date_range("2026-01-01", periods=4, freq="500ms")
    assert np.allclose(to_seconds(idx), [0.0, 0.5, 1.0, 1.5])
    tz = pd.date_range("2026-01-01", periods=3, freq="1s", tz="America/Chicago")
    assert np.allclose(to_seconds(tz), [0.0, 1.0, 2.0])
    assert np.allclose(to_seconds([0, 2, 4]), [0.0, 2.0, 4.0])


# --------------------------------------------------------------------------- #
# SelfRegulating
# --------------------------------------------------------------------------- #

def test_holds_pv_init_at_steady_state():
    """No drift toward K*output: with the output unchanged the PV holds."""
    t = np.arange(0, 100, 0.5)
    m = SelfRegulating(t, np.full_like(t, 30.0), 60.0, 0.5, 10.0, 4.0, PV_RANGE)
    assert np.allclose(m.pv, 60.0)


def test_step_response_gain_in_pct_per_pct():
    """10% output step, K=0.5 %/%, 150 degC span -> 7.5 degC rise."""
    t = np.arange(0, 200, 0.1)
    m = SelfRegulating(t, _step(t), 60.0, 0.5, 10.0, 4.0, PV_RANGE)
    assert m.pv[-1] == pytest.approx(67.5, abs=1e-4)


def test_step_response_dead_time_and_time_constant():
    t = np.arange(0, 200, 0.1)
    m = SelfRegulating(t, _step(t), 60.0, 0.5, 10.0, 4.0, PV_RANGE)
    moved = np.abs(m.pv - 60.0) > 1e-9
    # step at 10 s + 4 s dead time (+ one scan, as the model steps scan to scan)
    assert t[np.argmax(moved)] == pytest.approx(14.1, abs=1e-6)
    # one time constant after the PV starts moving: 63.2% of the way there
    at = np.searchsorted(t, 24.0)
    assert m.pv[at] - 60.0 == pytest.approx(7.5 * (1 - np.exp(-1)), rel=1e-6)


def test_negative_gain_moves_pv_down():
    t = np.arange(0, 200, 0.5)
    m = SelfRegulating(t, _step(t), 60.0, -0.5, 10.0, 4.0, PV_RANGE)
    assert m.pv[-1] == pytest.approx(52.5, abs=1e-3)


def test_plot_shares_one_x_axis():
    pytest.importorskip("plotly")
    t = np.arange(0, 50, 0.5)
    m = SelfRegulating(t, _step(t), 60.0, 0.5, 10.0, 4.0, PV_RANGE)
    fig = m.plot(pv_measured=m.pv)
    assert fig.layout.hoversubplots == "axis"
    assert {tr.xaxis for tr in fig.data} == {None}  # all on the default "x"
    assert len(fig.data) == 3


# --------------------------------------------------------------------------- #
# fit_self_regulating
# --------------------------------------------------------------------------- #

def _practice_data(noise=0.2, seed=1):
    t = np.arange(0, 300, 0.5)
    out = np.where(t >= 20, 40.0, 30.0)
    out = np.where(t >= 150, 35.0, out)
    true = SelfRegulating(t, out, 60.0, 0.8, 25.0, 6.0, PV_RANGE)
    pv = true.pv + np.random.default_rng(seed).normal(0, noise, t.size)
    return t, out, pv


def test_fit_recovers_known_process():
    pytest.importorskip("scipy")
    t, out, pv = _practice_data()
    fit = fit_self_regulating(t, out, pv, pv_range=PV_RANGE)
    assert fit.success
    assert fit.process_gain == pytest.approx(0.8, rel=0.03)
    assert fit.time_const == pytest.approx(25.0, rel=0.05)
    assert fit.dead_time == pytest.approx(6.0, abs=0.5)
    assert fit.r2 > 0.99
    assert np.allclose(fit.model.pv, pv, atol=1.0)


def test_fit_with_historian_timestamps():
    pytest.importorskip("scipy")
    t, out, pv = _practice_data()
    idx = pd.Timestamp("2026-03-01 06:00") + pd.to_timedelta(t, unit="s")
    fit = fit_self_regulating(idx, out, pv, pv_range=PV_RANGE)
    assert fit.time_const == pytest.approx(25.0, rel=0.05)


def test_fit_rejects_nan_and_mismatched_lengths():
    pytest.importorskip("scipy")
    t, out, pv = _practice_data()
    pv[50] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        fit_self_regulating(t, out, pv, pv_range=PV_RANGE)
    with pytest.raises(ValueError, match="same length"):
        fit_self_regulating(t, out[:-1], pv, pv_range=PV_RANGE)
