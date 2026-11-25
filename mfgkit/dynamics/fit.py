"""Fit process models to recorded output and PV data with least squares.

The fit treats the controller output as the process input, so it works on
open-loop step tests and on closed-loop data alike: the controller doesn't
matter, only what the output did and how the PV answered.

``scipy`` is imported when a fit runs, so the models in
:mod:`mfgkit.dynamics.models` stay usable with numpy alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .models import SelfRegulating, to_pct


def to_seconds(time) -> np.ndarray:
    """Sample times as float seconds from the first sample.

    Accepts plain numbers (taken as seconds already), numpy ``datetime64``
    arrays, or a pandas ``DatetimeIndex`` / datetime Series straight from a
    historian pull.
    """
    arr = np.asarray(time)
    if np.issubdtype(arr.dtype, np.datetime64):
        return (arr - arr[0]) / np.timedelta64(1, "s")
    if arr.dtype == object:
        # e.g. a tz-aware pandas index, which numpy turns into objects
        import pandas as pd

        stamps = pd.to_datetime(time)
        return ((stamps - stamps[0]) / pd.Timedelta(seconds=1)).to_numpy(np.float64)
    return arr.astype(np.float64)


@dataclass
class FitResult:
    """Outcome of :func:`fit_self_regulating`.

    ``model`` is the fitted :class:`SelfRegulating`, simulated over the data,
    so ``result.model.plot(result.pv_measured)`` shows the fit. ``optimize``
    is scipy's raw ``OptimizeResult`` for anything not surfaced here.
    """

    process_gain: float
    time_const: float
    dead_time: float
    pv_init: float
    r2: float
    model: SelfRegulating
    pv_measured: np.ndarray = field(repr=False)
    optimize: object = field(repr=False)

    @property
    def success(self) -> bool:
        return bool(self.optimize.success)


def _initial_guess(t, out_pct, pv_pct, n_base):
    """Rough K, tau, theta from the data, the way you'd read them by hand.

    K is the change in PV over the change in output between the start and
    end of the record (both in %). tau and theta default to fractions of the
    record length. Good enough to start the fit; least_squares does the rest.
    """
    d_out = out_pct[-n_base:].mean() - out_pct[:n_base].mean()
    d_pv = pv_pct[-n_base:].mean() - pv_pct[:n_base].mean()
    span = t[-1] - t[0]
    k0 = d_pv / d_out if abs(d_out) > 1e-9 else 1.0
    return [k0, span / 5, span / 50]


def fit_self_regulating(
    time,
    out,
    pv,
    pv_range=(0.0, 100.0),
    out_range=(0.0, 100.0),
    p0=None,
    bounds=None,
    n_base=10,
    pv_init=None,
) -> FitResult:
    """Fit a :class:`SelfRegulating` (FOPDT) model to recorded data.

    Parameters
    ----------
    time : array-like
        Sample times: seconds, or datetimes from a historian.
    out, pv : array-like
        Controller output and PV in engineering units.
    pv_range, out_range : (low, high)
        Engineering-unit spans, so the gain comes out in %/%.
    p0 : [K, tau, theta], optional
        Starting guess (K in %/%, tau and theta in s). Estimated from the
        data if omitted; pass your own if the fit lands somewhere silly.
    bounds : (lower, upper), optional
        Lists of three. Default: K free (it may be negative), tau from 1 ms
        to ten times the record, theta from 0 to half the record.
    n_base : int
        Samples at the start treated as steady state, for ``pv_init`` and
        the initial guess. The data should begin at steady state.
    pv_init : float, optional
        Starting PV in engineering units. Default: the mean of the first
        ``n_base`` PV samples.

    Returns
    -------
    FitResult
    """
    from scipy.optimize import least_squares

    t = to_seconds(time)
    out = np.asarray(out, dtype=np.float64)
    pv = np.asarray(pv, dtype=np.float64)
    if not (len(t) == len(out) == len(pv)):
        raise ValueError(
            f"time, out and pv must be the same length, got {len(t)}, {len(out)}, {len(pv)}"
        )
    if len(t) <= n_base:
        raise ValueError(f"need more than n_base={n_base} samples, got {len(t)}")
    if np.isnan(out).any() or np.isnan(pv).any():
        raise ValueError("out and pv contain NaN; drop or fill those samples first")
    if np.any(np.diff(t) <= 0):
        raise ValueError("time must be strictly increasing")

    if pv_init is None:
        pv_init = float(pv[:n_base].mean())
    if p0 is None:
        p0 = _initial_guess(
            t, to_pct(out, out_range), to_pct(pv, pv_range), n_base
        )
    if bounds is None:
        span = t[-1] - t[0]
        bounds = ([-np.inf, 1e-3, 0.0], [np.inf, 10 * span, span / 2])

    def residuals(p):
        k, tau, theta = p
        model = SelfRegulating(t, out, pv_init, k, tau, theta, pv_range, out_range)
        return model.pv - pv

    opt = least_squares(residuals, p0, bounds=bounds)
    k, tau, theta = (float(v) for v in opt.x)
    model = SelfRegulating(t, out, pv_init, k, tau, theta, pv_range, out_range)
    ss_tot = np.sum((pv - pv.mean()) ** 2)
    r2 = 1 - np.sum(opt.fun**2) / ss_tot if ss_tot > 0 else float("nan")

    return FitResult(
        process_gain=k,
        time_const=tau,
        dead_time=theta,
        pv_init=pv_init,
        r2=float(r2),
        model=model,
        pv_measured=pv,
        optimize=opt,
    )
