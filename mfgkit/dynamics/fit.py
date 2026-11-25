"""Fit process models to recorded output and PV data with least squares.

The fit treats the controller output as the process input, so it works on
open-loop step tests and on closed-loop data alike: the controller doesn't
matter, only what the output did and how the PV answered.

Every fit returns the same :class:`FitResult`. The fitted parameters live on
``result.model`` (``result.model.time_const``, ``result.model.out_bal``...),
with ``result.params`` and ``result.stderr`` as name -> value dicts for
building tables across many fits.

``scipy`` is imported when a fit runs, so the models in
:mod:`mfgkit.dynamics.models` stay usable with numpy alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .models import Integrating, SelfRegulating, to_pct


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
    """Outcome of any ``fit_*`` function.

    Attributes
    ----------
    model : SelfRegulating or Integrating
        The fitted model, simulated over the data. Its attributes are the
        fitted parameters: ``result.model.dead_time`` etc.
    params : dict
        The fitted parameters, name -> value. Only the ones the fit
        adjusted: a parameter you fixed (e.g. ``out_bal=``) is on the model
        but not here.
    stderr : dict
        Standard error of each fitted parameter, same keys as ``params``.
        Large compared with the value means the data didn't pin it down
        (not enough excitation, or two parameters trading off). NaN if it
        couldn't be estimated.
    r2 : float
        Fraction of the PV's variance the model explains.
    pv_measured : array
        The PV the model was fitted to, for ``model.plot(pv_measured)``.
    optimize : scipy.optimize.OptimizeResult
        scipy's raw result, for anything not surfaced here.
    """

    model: SelfRegulating | Integrating
    params: dict[str, float]
    stderr: dict[str, float]
    r2: float
    pv_measured: np.ndarray = field(repr=False)
    optimize: object = field(repr=False)

    @property
    def success(self) -> bool:
        return bool(self.optimize.success)

    def plot(self):
        """The fitted model over the measured PV."""
        return self.model.plot(self.pv_measured)


# --------------------------------------------------------------------------- #
# shared machinery
# --------------------------------------------------------------------------- #

def _prepare(time, out, pv, n_base):
    """Convert and validate inputs; return float arrays (t in seconds)."""
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
    return t, out, pv


def _stderr(opt) -> np.ndarray:
    """Standard errors from the Jacobian at the solution.

    cov = s^2 (J^T J)^-1, with s^2 the residual variance. pinv rather than
    inv so a parameter the data can't determine gives a huge or NaN error
    instead of an exception.
    """
    n, p = opt.jac.shape
    if n <= p:
        return np.full(p, np.nan)
    s2 = 2 * opt.cost / (n - p)  # opt.cost is half the sum of squares
    cov = np.linalg.pinv(opt.jac.T @ opt.jac) * s2
    return np.sqrt(np.clip(np.diag(cov), 0, None))


def _fit(build: Callable, names, p0, bounds, pv) -> FitResult:
    """Run least_squares on ``build(p).pv - pv`` and package the result.

    ``build`` maps a parameter array to a model; ``names`` labels the array.
    """
    from scipy.optimize import least_squares

    opt = least_squares(lambda p: build(p).pv - pv, p0, bounds=bounds)
    model = build(opt.x)
    ss_tot = np.sum((pv - pv.mean()) ** 2)
    r2 = 1 - np.sum(opt.fun**2) / ss_tot if ss_tot > 0 else float("nan")
    return FitResult(
        model=model,
        params={n: float(v) for n, v in zip(names, opt.x)},
        stderr={n: float(e) for n, e in zip(names, _stderr(opt))},
        r2=float(r2),
        pv_measured=pv,
        optimize=opt,
    )


# --------------------------------------------------------------------------- #
# self-regulating
# --------------------------------------------------------------------------- #

def _self_regulating_guess(t, out_pct, pv_pct, n_base):
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

    Fitted parameters: ``process_gain`` (%/%), ``time_const`` (s),
    ``dead_time`` (s).

    Parameters
    ----------
    time : array-like
        Sample times: seconds, or datetimes from a historian.
    out, pv : array-like
        Controller output and PV in engineering units.
    pv_range, out_range : (low, high)
        Engineering-unit spans, so the gain comes out in %/%.
    p0 : [K, tau, theta], optional
        Starting guess. Estimated from the data if omitted; pass your own if
        the fit lands somewhere silly.
    bounds : (lower, upper), optional
        Lists of three. Default: K free (it may be negative), tau from 1 ms
        to ten times the record, theta from 0 to half the record.
    n_base : int
        Samples at the start treated as steady state, for ``pv_init`` and
        the initial guess. The data should begin at steady state.
    pv_init : float, optional
        Starting PV in engineering units. Default: the mean of the first
        ``n_base`` PV samples.
    """
    t, out, pv = _prepare(time, out, pv, n_base)
    if pv_init is None:
        pv_init = float(pv[:n_base].mean())
    if p0 is None:
        p0 = _self_regulating_guess(
            t, to_pct(out, out_range), to_pct(pv, pv_range), n_base
        )
    if bounds is None:
        span = t[-1] - t[0]
        bounds = ([-np.inf, 1e-3, 0.0], [np.inf, 10 * span, span / 2])

    def build(p):
        k, tau, theta = p
        return SelfRegulating(t, out, pv_init, k, tau, theta, pv_range, out_range)

    return _fit(build, ["process_gain", "time_const", "dead_time"], p0, bounds, pv)


# --------------------------------------------------------------------------- #
# integrating
# --------------------------------------------------------------------------- #

def _integrating_gain_guess(t, out_pct, pv_pct, bal_pct):
    """Ki from a straight-line fit, ignoring dead time.

    With no dead time, PV(t) - PV(0) = Ki * integral of (OUT - OUT_bal) dt,
    which is linear in Ki, so it has a one-line least-squares answer. Dead
    time makes it a little off, but it gets the sign and size right, and a
    wrong-sign start is what sends integrating fits to the wrong minimum.
    """
    dt = np.diff(t, prepend=t[0])
    x = np.cumsum((out_pct - bal_pct) * dt)
    y = pv_pct - pv_pct[0]
    denom = np.dot(x, x)
    return float(np.dot(x, y) / denom) if denom > 0 else 0.01


def fit_integrating(
    time,
    out,
    pv,
    pv_range=(0.0, 100.0),
    out_range=(0.0, 100.0),
    p0=None,
    bounds=None,
    n_base=10,
    pv_init=None,
    out_bal=None,
) -> FitResult:
    """Fit an :class:`Integrating` model (a level, typically) to recorded data.

    Fitted parameters: ``integrating_gain`` (1/s), ``dead_time`` (s) and, by
    default, ``out_bal`` (EU): the output at which the PV holds still. It is
    fitted rather than assumed because it moves with production rate and
    conditions, so it's worth knowing for each test, and because fixing it
    even slightly wrong skews the other two.

    Parameters
    ----------
    time : array-like
        Sample times: seconds, or datetimes from a historian.
    out, pv : array-like
        Controller output and PV in engineering units.
    pv_range, out_range : (low, high)
        Engineering-unit spans, so the gain comes out in (%/s)/%.
    p0 : list, optional
        Starting guess, ``[Ki, theta, out_bal]`` (or ``[Ki, theta]`` when
        ``out_bal`` is fixed). Default: Ki from a straight-line fit (right
        sign), theta a fiftieth of the record, out_bal the mean output.
    bounds : (lower, upper), optional
        Same length as ``p0``. Default: Ki free (negative for, e.g., a drain
        valve), theta from 0 to half the record, out_bal within out_range.
        If the fit pairs the wrong output move with the wrong ramp (a huge
        theta and a wrong-sign Ki), tighten theta's upper bound.
    n_base : int
        Samples at the start used for ``pv_init``.
    pv_init : float, optional
        Starting PV in engineering units. Default: the mean of the first
        ``n_base`` PV samples.
    out_bal : float, optional
        Fix the balance output (EU) instead of fitting it.
    """
    t, out, pv = _prepare(time, out, pv, n_base)
    if pv_init is None:
        pv_init = float(pv[:n_base].mean())
    fit_bal = out_bal is None
    span = t[-1] - t[0]

    if p0 is None:
        bal0 = float(out.mean()) if fit_bal else out_bal
        ki0 = _integrating_gain_guess(
            t, to_pct(out, out_range), to_pct(pv, pv_range), to_pct(bal0, out_range)
        )
        p0 = [ki0, span / 50, bal0] if fit_bal else [ki0, span / 50]
    if bounds is None:
        lower, upper = [-np.inf, 0.0], [np.inf, span / 2]
        if fit_bal:
            lower.append(min(out_range))
            upper.append(max(out_range))
        bounds = (lower, upper)

    def build(p):
        ki, theta = p[0], p[1]
        bal = p[2] if fit_bal else out_bal
        return Integrating(t, out, pv_init, ki, theta, bal, pv_range, out_range)

    names = ["integrating_gain", "dead_time"] + (["out_bal"] if fit_bal else [])
    return _fit(build, names, p0, bounds, pv)
