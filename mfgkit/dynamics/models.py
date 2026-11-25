"""Process models simulated scan by scan from a recorded output signal.

The models take the controller output (or any manipulated variable) as their
input and return the PV the process would produce, so they work for any
output signal -- a clean step test, a closed-loop trend, a bump the operator
made -- not only a single textbook step.

Gains are dimensionless (% of PV span per % of output span), the form used for
loop tuning. Signals go in and come out in engineering units; the conversion
to % of span happens inside.
"""

from __future__ import annotations

import numpy as np


def delayed(time_series, out_series, dead_time):
    """``out_series`` as the process sees it, ``dead_time`` seconds late.

    Before the data starts the signal is held at its first value, so the
    process sits at steady state until the first change reaches it. Linear
    interpolation handles dead times that aren't a whole number of scans.
    """
    time_series = np.asarray(time_series, dtype=np.float64)
    out_series = np.asarray(out_series, dtype=np.float64)
    return np.interp(
        time_series - dead_time, time_series, out_series, left=out_series[0]
    )


def to_pct(value, eu_range):
    """Engineering units -> % of span. ``eu_range`` is ``(low, high)``."""
    lo, hi = eu_range
    return (np.asarray(value, dtype=np.float64) - lo) / (hi - lo) * 100.0


def to_eu(pct, eu_range):
    """% of span -> engineering units. ``eu_range`` is ``(low, high)``."""
    lo, hi = eu_range
    return lo + np.asarray(pct, dtype=np.float64) / 100.0 * (hi - lo)


class SelfRegulating:
    """FOPDT process: tau*dPV/dt + PV = PV_init + K*(OUT(t - theta) - OUT_init).

    Inputs and outputs are in engineering units. ``process_gain`` is in %/%
    (% of PV span per % of output span), the dimensionless gain used for
    tuning. The process starts at steady state: ``pv_init`` is the PV it holds
    while the output stays at ``out_series[0]``.

    This is a local, linear model around that operating point. Real processes
    are rarely linear over their whole range (a heater bottoms out at ambient,
    a valve's gain changes with opening), so fit and use it near the range the
    plant actually runs in.

    Parameters
    ----------
    time_series : array of float
        Sample times in seconds. Need not be evenly spaced.
    out_series : array of float
        Controller output in engineering units, one value per sample.
    pv_init : float
        PV, in engineering units, at the starting output.
    process_gain : float
        Gain in %/%. Negative for a process where more output lowers the PV.
    time_const : float
        Time constant (s).
    dead_time : float
        Dead time (s).
    pv_range, out_range : (low, high)
        Engineering-unit spans of the PV transmitter and the output,
        e.g. ``(0, 150)`` degC and ``(0, 100)`` % valve.

    Attributes
    ----------
    pv : array of float
        The simulated PV in engineering units.
    """

    def __init__(
        self,
        time_series,
        out_series,
        pv_init,
        process_gain,
        time_const,
        dead_time,
        pv_range=(0.0, 100.0),
        out_range=(0.0, 100.0),
    ) -> None:
        self.time_series = np.asarray(time_series, dtype=np.float64)
        self.out_series = np.asarray(out_series, dtype=np.float64)
        self.pv_init = pv_init
        self.process_gain = process_gain
        self.time_const = time_const
        self.dead_time = dead_time
        self.pv_range = pv_range
        self.out_range = out_range
        self.pv = self._simulate()

    def __repr__(self) -> str:
        return (
            f"SelfRegulating(K={self.process_gain:.4g} %/%, "
            f"tau={self.time_const:.4g} s, theta={self.dead_time:.4g} s, "
            f"pv_init={self.pv_init:.4g})"
        )

    def _simulate(self):
        # Work in % of span, so process_gain is %/%.
        out_pct = to_pct(self.out_series, self.out_range)
        out_delayed = delayed(self.time_series, out_pct, self.dead_time)
        pv_init_pct = to_pct(self.pv_init, self.pv_range)

        # Exact zero-order-hold step: each scan the PV closes a fraction
        # (1 - a) of the gap to where the current output would settle it.
        a = np.exp(-np.diff(self.time_series) / self.time_const)
        target = pv_init_pct + self.process_gain * (out_delayed - out_pct[0])

        pv = np.empty_like(self.time_series)
        pv[0] = pv_init_pct
        for k in range(len(self.time_series) - 1):
            pv[k + 1] = a[k] * pv[k] + (1 - a[k]) * target[k]
        return to_eu(pv, self.pv_range)

    def plot(self, pv_measured=None):
        """PV over output, sharing one hover box. Needs ``mfgkit[dynamics]``.

        Pass ``pv_measured`` (same length as the time series) to overlay the
        data the model was fitted to.
        """
        import plotly.graph_objects as go

        # Both traces share one x axis and sit on stacked y axes, so
        # hoversubplots="axis" shows PV and output in one hover box.
        fig = go.Figure()
        if pv_measured is not None:
            fig.add_trace(
                go.Scatter(
                    x=self.time_series,
                    y=np.asarray(pv_measured, dtype=np.float64),
                    mode="markers",
                    marker_size=3,
                    name="PV measured",
                )
            )
        fig.add_trace(
            go.Scatter(x=self.time_series, y=self.pv, mode="lines", name="PV model")
        )
        fig.add_trace(
            go.Scatter(
                x=self.time_series,
                y=self.out_series,
                mode="lines",
                name="Output",
                yaxis="y2",
            )
        )
        fig.update_layout(
            title=(
                f"Self-regulating: K={self.process_gain:.3g} %/%, "
                f"tau={self.time_const:.3g} s, "
                f"theta={self.dead_time:.3g} s"
            ),
            height=500,
            hovermode="x unified",
            hoversubplots="axis",
            xaxis={"title_text": "time (s)", "anchor": "y2"},
            yaxis={"title_text": "PV", "domain": [0.53, 1.0]},
            yaxis2={"title_text": "Output", "domain": [0.0, 0.47]},
        )
        return fig
