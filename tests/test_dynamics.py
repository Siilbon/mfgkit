"""Tests for mfgkit.dynamics."""

import numpy as np
import pandas as pd
import pytest

from mfgkit.dynamics import (
    Integrating,
    SelfRegulating,
    delayed,
    fit_integrating,
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
    assert fit.model.process_gain == pytest.approx(0.8, rel=0.03)
    assert fit.model.time_const == pytest.approx(25.0, rel=0.05)
    assert fit.model.dead_time == pytest.approx(6.0, abs=0.5)
    assert fit.r2 > 0.99
    assert np.allclose(fit.model.pv, pv, atol=1.0)


def test_fit_with_historian_timestamps():
    pytest.importorskip("scipy")
    t, out, pv = _practice_data()
    idx = pd.Timestamp("2026-03-01 06:00") + pd.to_timedelta(t, unit="s")
    fit = fit_self_regulating(idx, out, pv, pv_range=PV_RANGE)
    assert fit.model.time_const == pytest.approx(25.0, rel=0.05)


def test_fit_rejects_nan_and_mismatched_lengths():
    pytest.importorskip("scipy")
    t, out, pv = _practice_data()
    pv[50] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        fit_self_regulating(t, out, pv, pv_range=PV_RANGE)
    with pytest.raises(ValueError, match="same length"):
        fit_self_regulating(t, out[:-1], pv, pv_range=PV_RANGE)


def test_fit_result_is_generic():
    """Parameters live on the model; params/stderr are dicts keyed by name."""
    pytest.importorskip("scipy")
    t, out, pv = _practice_data()
    fit = fit_self_regulating(t, out, pv, pv_range=PV_RANGE)
    assert set(fit.params) == {"process_gain", "time_const", "dead_time"}
    assert set(fit.stderr) == set(fit.params)
    for name, value in fit.params.items():
        assert getattr(fit.model, name) == value
    assert all(0 < e < 0.1 * abs(fit.params[n]) for n, e in fit.stderr.items())


# --------------------------------------------------------------------------- #
# Integrating
# --------------------------------------------------------------------------- #

LEVEL_RANGE = (0.0, 4.0)  # m


def _pulses(t):
    """+10% for 60 s, then -5% for 60 s, around 30%: a level test."""
    out = np.full_like(t, 30.0)
    out[(t >= 20) & (t < 80)] = 40.0
    out[(t >= 160) & (t < 220)] = 25.0
    return out


def _tank_data(ki=0.012, theta=8.0, out_bal=30.5, noise=0.005, seed=2):
    t = np.arange(0, 300, 0.5)
    out = _pulses(t)
    true = Integrating(t, out, 2.0, ki, theta, out_bal, pv_range=LEVEL_RANGE)
    pv = true.pv + np.random.default_rng(seed).normal(0, noise, t.size)
    return t, out, pv


def test_integrating_holds_still_at_balance():
    t = np.arange(0, 100, 0.5)
    m = Integrating(t, np.full_like(t, 30.0), 2.0, 0.01, 5.0, pv_range=LEVEL_RANGE)
    assert m.out_bal == 30.0  # default: the starting output
    assert np.allclose(m.pv, 2.0)


def test_integrating_ramp_rate_and_dead_time():
    """+10% output, Ki = 0.01 1/s -> 0.1 %/s of a 4 m span = 0.004 m/s."""
    t = np.arange(0, 100, 0.5)
    m = Integrating(t, _step(t), 2.0, 0.01, 5.0, pv_range=LEVEL_RANGE)
    assert np.all(m.pv[t <= 15] == 2.0)  # step at 10 s + 5 s dead time
    assert np.allclose(np.diff(m.pv[t >= 20]) / 0.5, 0.004)


def test_integrating_drifts_off_balance():
    t = np.arange(0, 100, 0.5)
    m = Integrating(
        t, np.full_like(t, 30.0), 2.0, 0.01, 5.0, out_bal=31.0, pv_range=LEVEL_RANGE
    )
    assert np.allclose(np.diff(m.pv) / 0.5, -0.0004)


def test_integrating_plot():
    pytest.importorskip("plotly")
    t = np.arange(0, 50, 0.5)
    m = Integrating(t, _step(t), 2.0, 0.01, 5.0, pv_range=LEVEL_RANGE)
    fig = m.plot(pv_measured=m.pv)
    assert fig.layout.hoversubplots == "axis"
    assert len(fig.data) == 3


def test_fit_integrating_recovers_drifting_tank():
    """out_bal is fitted by default, so a level drifting before the test is fine."""
    pytest.importorskip("scipy")
    t, out, pv = _tank_data()
    fit = fit_integrating(t, out, pv, pv_range=LEVEL_RANGE)
    assert fit.success
    assert set(fit.params) == {"integrating_gain", "dead_time", "out_bal"}
    assert fit.model.integrating_gain == pytest.approx(0.012, rel=0.02)
    assert fit.model.dead_time == pytest.approx(8.0, abs=0.5)
    assert fit.model.out_bal == pytest.approx(30.5, abs=0.05)
    assert fit.r2 > 0.99


def test_fit_integrating_with_fixed_out_bal():
    pytest.importorskip("scipy")
    t, out, pv = _tank_data()
    fit = fit_integrating(t, out, pv, pv_range=LEVEL_RANGE, out_bal=30.5)
    assert set(fit.params) == {"integrating_gain", "dead_time"}
    assert fit.model.out_bal == 30.5
    assert fit.model.integrating_gain == pytest.approx(0.012, rel=0.02)


def test_fit_integrating_wrongly_fixed_out_bal_skews_the_fit():
    """Why out_bal is fitted by default: pinning it 0.5% off biases Ki and theta."""
    pytest.importorskip("scipy")
    t, out, pv = _tank_data()
    fit = fit_integrating(t, out, pv, pv_range=LEVEL_RANGE, out_bal=30.0)
    assert fit.model.integrating_gain == pytest.approx(0.010, abs=0.001)
    assert fit.model.dead_time < 6.0


def test_fit_integrating_negative_gain():
    """A drain valve: more output lowers the level. The default guess gets the sign."""
    pytest.importorskip("scipy")
    t, out, pv = _tank_data(ki=-0.012, seed=3)
    fit = fit_integrating(t, out, pv, pv_range=LEVEL_RANGE)
    assert fit.model.integrating_gain == pytest.approx(-0.012, rel=0.02)
    assert fit.model.dead_time == pytest.approx(8.0, abs=0.5)


def test_fit_integrating_with_historian_timestamps():
    pytest.importorskip("scipy")
    t, out, pv = _tank_data()
    idx = pd.Timestamp("2026-03-01 06:00") + pd.to_timedelta(t, unit="s")
    fit = fit_integrating(idx, out, pv, pv_range=LEVEL_RANGE)
    assert fit.model.integrating_gain == pytest.approx(0.012, rel=0.02)
