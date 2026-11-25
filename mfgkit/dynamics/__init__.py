"""Process dynamics: simulate process models and fit them to plant data.

System-agnostic, like :mod:`mfgkit.analysis`: feed it output and PV trends
from any historian or DCS export.

    from mfgkit.dynamics import fit_integrating, fit_self_regulating

    fit = fit_self_regulating(df.index, df["TIC101.OUT"], df["TIC101.PV"],
                              pv_range=(0, 150))
    fit.model.process_gain, fit.model.time_const, fit.model.dead_time
    fit.plot().show()

    lvl = fit_integrating(df.index, df["LIC201.OUT"], df["LIC201.PV"],
                          pv_range=(0, 4))
    lvl.params    # {"integrating_gain": ..., "dead_time": ..., "out_bal": ...}
    lvl.stderr    # how well the data pinned each one down

Every fit returns a :class:`FitResult`; the fitted parameters live on its
``model``.

The models need only numpy. Fitting needs scipy and plotting needs plotly:
``pip install "mfgkit[dynamics]"``.
"""

from .fit import FitResult, fit_integrating, fit_self_regulating, to_seconds
from .models import Integrating, SelfRegulating, delayed, to_eu, to_pct

__all__ = [
    "SelfRegulating",
    "Integrating",
    "fit_self_regulating",
    "fit_integrating",
    "FitResult",
    "delayed",
    "to_pct",
    "to_eu",
    "to_seconds",
]
