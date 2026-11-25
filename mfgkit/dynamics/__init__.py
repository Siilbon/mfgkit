"""Process dynamics: simulate process models and fit them to plant data.

System-agnostic, like :mod:`mfgkit.analysis`: feed it output and PV trends
from any historian or DCS export.

    from mfgkit.dynamics import SelfRegulating, fit_self_regulating

    fit = fit_self_regulating(df.index, df["TIC101.OUT"], df["TIC101.PV"],
                              pv_range=(0, 150))
    fit.process_gain, fit.time_const, fit.dead_time   # %/%, s, s
    fit.model.plot(fit.pv_measured).show()

The models need only numpy. Fitting needs scipy and plotting needs plotly:
``pip install "mfgkit[dynamics]"``.
"""

from .fit import FitResult, fit_self_regulating, to_seconds
from .models import SelfRegulating, delayed, to_eu, to_pct

__all__ = [
    "SelfRegulating",
    "fit_self_regulating",
    "FitResult",
    "delayed",
    "to_pct",
    "to_eu",
    "to_seconds",
]
